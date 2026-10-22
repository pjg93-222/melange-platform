'use strict';
// Private district data for approved Terrier testers. The access server publishes one signed terrier.district-data.v1 revision per
// district (server/district_packs.py: one gzip object per file, named by role). This module downloads the missing ones through
// dataset-client.cjs, unzips them into staging, verifies them and swaps them in as <root>\stores\districts\<id>, the layout
// runtime-roots.cjs reads when dataDirectory is <root>\stores (its district state is <root>\state\districts\<id>, also under root).
// Nothing here runs in legacy-owner mode.
const fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),zlib=require('node:zlib'),crypto=require('node:crypto');
const {pipeline}=require('node:stream/promises'),{Transform}=require('node:stream');
const {DatasetClient}=require('./dataset-client.cjs');
const {desktopProbe}=require('./desktop-probe.cjs');const {backendCommand}=require('./backend-command.cjs');const {refuseInstalledOverlap}=require('./runtime-roots.cjs');
const KIND='terrier.district-data.v1';
// The server's REQUIRED/OPTIONAL/BUNDLE names. Any other role is refused in metadata and never read, unzipped or kept.
const REQUIRED=Object.freeze(['bundle.zip','contacts.sqlite3','density.json','roads.sqlite3','scores.sqlite3','street.sqlite3','walk.sqlite3']);
const ALLOWED=Object.freeze([...REQUIRED,'bundle.zip.sha256','credit-quality.json']);
const LABELS=Object.freeze({'co-05':'Colorado 05','or-05':'Oregon 05','ct-01':'Connecticut 01'});
const FIRST_WAIT=5000,MAX_WAIT=300000,THROTTLE_WAIT=60000;
// Every failure of the download phase leaves as one of these: the message is safe to show, cause is for the log.
class DistrictDataStop extends Error{constructor(message,cause){super(message);this.cause=cause;}}
const layout=root=>({root,districts:path.join(root,'stores','districts'),state:path.join(root,'state','districts'),cache:path.join(root,'dataset-sync'),staging:path.join(root,'staging')});
const remove=target=>fsp.rm(target,{recursive:true,force:true,maxRetries:5,retryDelay:200});
const exists=target=>fsp.access(target).then(()=>true,()=>false);
const lower=value=>process.platform==='win32'?value.toLowerCase():value;
// %LOCALAPPDATA%\MelangeData\terrier, like the updater root. Isolated and unpackaged runs keep everything inside the profile.
function storeRoot({userData,localAppData,packaged,isolated,platform=process.platform}){
 return platform==='win32'&&packaged&&!isolated?path.join(localAppData,'MelangeData','terrier'):path.join(userData,'packs');
}
// Testers still on the old default data directory move to the private store; any other (explicit) directory is theirs.
const onOldDefault=(settings,userData)=>settings.terrierLegacy===undefined&&settings.terrierDistricts===undefined&&typeof settings.dataDirectory==='string'&&lower(path.resolve(settings.dataDirectory))===lower(path.resolve(userData,'data'));
function migrateSettings(settings,{userData,root}){
 return onOldDefault(settings,userData)?{...settings,dataDirectory:path.join(root,'stores')}:null;
}
// Called with the settings from before migrateSettings: the old default <profile>\data and <profile>\state district trees (voter data, accounts.sqlite3, .secret)
// would otherwise stay in the synced Documents profile. The same test as the migration gates it, so never in legacy-owner mode and never for a hand-chosen
// data directory; a district folder that is a junction out of the profile is the tester's own and is left alone. Resolves the paths removed.
async function removeOldDefaultData(settings,{userData}){
 const removed=[];if(!onOldDefault(settings,userData))return removed;
 const profile=await fsp.realpath(userData);
 for(const target of [path.join(userData,'data','districts'),path.join(userData,'state','districts')]){
  const real=await fsp.realpath(target).catch(error=>error.code==='ENOENT'?null:Promise.reject(error)),relative=real&&path.relative(profile,real);
  if(!real||!relative||relative.startsWith('..')||path.isAbsolute(relative))continue;
  await remove(target);removed.push(target);
 }
 return removed;
}
// Chromium's session data (cookies and the disk cache of every page shown, which holds voter data) and the backend/download logs of a non-legacy tester sit in the
// store, not in the synced profile, so revocation and uninstall delete them with it. main.cjs/runtime.cjs decide who is non-legacy.
const sessionDirectory=root=>path.join(root,'session'),logDirectory=root=>path.join(root,'logs');
// Shown after a backend start when it served the district without importing its downloaded bundle (desktop_backend.py leaves the marker, retries next start).
const bundleImportNotice=roots=>roots?.state&&fs.existsSync(path.join(roots.state,'bundle-import-failed'))?'District data for '+(LABELS[roots.district]||roots.district)+' could not be imported; it will retry on next start.':null;
// Keep the chosen district while it is approved; otherwise the first approved one.
function defaultDistrict(settings,approved){return settings.terrierLegacy===undefined&&approved.length&&!approved.includes(settings.district)?approved[0]:settings.district;}
// Revocation: the whole private store goes, district state, session data and logs included (all under root), and the journal, whose identity belongs to the revoked approval.
// Chromium keeps its session files open until the app exits (main.cjs clears the partition first), so that folder is best effort and cannot keep the rest or the journal.
async function removeStore(root,{journal=null}={}){
 if(!['terrier','packs'].includes(path.basename(root)))throw new Error('Refusing to remove an unexpected district store.');
 for(const name of await fsp.readdir(root).catch(()=>[]))if(name!=='session')await remove(path.join(root,name));
 await journal?.write(null);
 await remove(path.join(root,'session')).catch(()=>{});await fsp.rmdir(root).catch(()=>{});
}
// The existing backend check (desktop_backend.py --desktop-district-check, as district-switch.cjs runs it) pointed at a staging directory.
function districtCheck({resources,protectedPaths=[],probe=desktopProbe,command=backendCommand}){
 return async(directory,district)=>{
  const backend=command(resources,'terrier');refuseInstalledOverlap(directory,[resources,...protectedPaths]);
  const reply=await probe(backend,'--desktop-district-check',{sourceRoot:path.join(backend.runtime,'sources'),dataRoot:directory,district,legacy:false},{timeout:120000});
  return reply.district===district&&reply.validated===true;
 };
}
async function gunzipTo(source,target,rawSize,signal){
 const name=path.basename(target);if(!Number.isSafeInteger(rawSize)||rawSize<0)throw new Error(name+' has no declared size');
 // zlib verifies the gzip CRC and length trailer, so a damaged object fails here; the signed rawSize bounds the output, so a gzip bomb stops at that many bytes.
 let count=0;const counter=new Transform({transform(chunk,_encoding,done){count+=chunk.length;done(count>rawSize?new Error(name+' is larger than its declared size'):null,chunk);}});
 await pipeline(fs.createReadStream(source),zlib.createGunzip(),counter,fs.createWriteStream(target,{flags:'wx'}),{signal});
 if(count!==rawSize)throw new Error(name+' is smaller than its declared size');
 const handle=await fsp.open(target,'r+');try{await handle.sync();}finally{await handle.close();}
}
async function sha256File(file){const hash=crypto.createHash('sha256');for await(const chunk of fs.createReadStream(file))hash.update(chunk);return hash.digest('hex');}
async function refuseWal(file){
 const header=Buffer.alloc(20),handle=await fsp.open(file,'r');try{await handle.read(header,0,20,0);}finally{await handle.close();}
 if(header.subarray(0,16).toString('latin1')!=='SQLite format 3\0')throw new Error(path.basename(file)+' is not a SQLite database');
 if(header[18]===2||header[19]===2)throw new Error(path.basename(file)+' is in WAL mode');
}
async function move(rename,from,to){
 for(let attempt=0;;attempt++){try{return await rename(from,to);}catch(error){if(attempt>=5||!['EPERM','EBUSY','EACCES'].includes(error.code))throw error;await new Promise(resolve=>setTimeout(resolve,200*(attempt+1)));}}
}
// The compiled adapter for terrier.district-data.v1. check(directory, districtId) must resolve true for a usable data directory.
function createAdapter({root,check,rename=fsp.rename}){
 const dirs=layout(root),places=revision=>({staging:path.join(dirs.staging,revision.revisionId),aside:path.join(dirs.staging,revision.revisionId+'.old'),place:path.join(dirs.districts,revision.districtId)});
 // Idempotent: also finishes a swap interrupted anywhere (staging only, old moved aside, new in place, aside left over).
 async function complete(revision){
  const {staging,aside,place}=places(revision);
  if(await exists(staging)){
   await fsp.mkdir(dirs.districts,{recursive:true});
   if(await exists(place)){await remove(aside);await move(rename,place,aside);}
   await move(rename,staging,place);
  }
  await remove(aside);
  if(!await exists(place))throw new Error('District data is missing after activation');
  return {activated:true,activityIntentDurable:true,activityPending:false};
 }
 const adapter={appId:'terrier',version:1,failure:null,cause:null,
  validateMetadata(value){const roles=value.objects.map(item=>item.role);return Object.hasOwn(LABELS,value.districtId)&&roles.every(role=>ALLOWED.includes(role))&&REQUIRED.every(role=>roles.includes(role));},
  async validate({revision,objects,signal}){
   const {staging}=places(revision);adapter.failure=adapter.cause=null;
   try{
    await remove(staging);await fsp.mkdir(staging,{recursive:true});
    for(const item of revision.objects)if(ALLOWED.includes(item.role))await gunzipTo(objects[item.role],path.join(staging,item.role),item.rawSize,signal);
    const digest=await sha256File(path.join(staging,'bundle.zip')),sidecar=path.join(staging,'bundle.zip.sha256');
    if(await exists(sidecar)&&(await fsp.readFile(sidecar,'ascii')).trim()!==digest)throw new Error('bundle.zip does not match its checksum');
    await fsp.writeFile(sidecar,digest+'\n','ascii');
    for(const role of ALLOWED.filter(name=>name.endsWith('.sqlite3')))await refuseWal(path.join(staging,role));
    if(await check(staging,revision.districtId)!==true)throw new Error('The district check did not accept this data');
    return true;
   }catch(error){if(signal?.aborted)throw error;adapter.failure=error.message;adapter.cause=error;await remove(staging);return false;}
  },
  activate:({revision})=>complete(revision),
  recoverActivation:({revision})=>complete(revision)};
 return adapter;
}
const present=(dirs,id)=>REQUIRED.every(name=>fs.existsSync(path.join(dirs.districts,id,name)));
function defaultSleep(ms,signal){
 return new Promise((resolve,reject)=>{
  const canceled=()=>reject(new Error('Dataset transfer canceled'));if(signal?.aborted)return canceled();
  const stop=()=>{clearTimeout(timer);canceled();},timer=setTimeout(()=>{signal?.removeEventListener('abort',stop);resolve();},ms);signal?.addEventListener('abort',stop,{once:true});
 });
}
const megabytes=bytes=>Math.round(bytes/1e6),gigabytes=bytes=>(bytes/1e9).toFixed(1)+' GB',after=ms=>ms<120000?Math.round(ms/1000)+' s':Math.round(ms/60000)+' min';
// A full disk or a locked file is not a verification failure: the downloaded objects stay cached and the next start picks them up (client.forget is never called for these).
// dataset-client.cjs reports its own staging-space check without a code.
const noSpace=(bytes,cause)=>new DistrictDataStop('Not enough disk space: need '+gigabytes(bytes)+' free.',cause);
const systemStop=(error,bytes)=>error?.code==='ENOSPC'||/Insufficient dataset staging space/.test(error?.message||'')?noSpace(bytes,error):error?.code==='EBUSY'?new DistrictDataStop('A district data file is in use by another program. Close it and reopen Terrier to try again.',error):null;
// Bytes a district needs on disk: its gzip objects, then the same files unzipped (rawSize is signed with the revision).
const objectBytes=(revision,field)=>revision.objects.filter(item=>ALLOWED.includes(item.role)).reduce((sum,item)=>sum+item[field],0);
// Downloads, verifies and activates every approved district that is not on disk yet, one at a time. When all are present it
// touches only the disk. Resolves {districts,downloaded} (or {skipped}); every failure rejects with a DistrictDataStop (or the abort error).
async function ensureDistrictData(gate,{root,settings={},journal,check,request,onStatus=()=>{},signal,sleep=defaultSleep,keepAwake=()=>()=>{},confine=null,statfs=fsp.statfs,rename,chunkSize,saveEvery}={}){
 if(settings.terrierLegacy!==undefined)return {skipped:'legacy'};
 // Only the app-managed store is filled and pruned; a data directory the tester chose themselves is theirs.
 if(typeof settings.dataDirectory==='string'&&lower(path.resolve(settings.dataDirectory))!==lower(path.resolve(root,'stores')))return {skipped:'own-data-directory'};
 await gate.evidence();const approved=gate.districts();
 // An empty list means "no usable approval right now", never "approved for nothing": do not prune on it.
 if(!approved.length)return {skipped:'unapproved'};
 const dirs=layout(root);confine?.(root);await fsp.mkdir(dirs.districts,{recursive:true});confine?.(root);
 const adapter=createAdapter({root,check,rename});let client=null;
 const open=()=>client||(client=new DatasetClient({access:gate.client,store:journal,profile:root,registry:{[KIND]:adapter},request,chunkSize,saveEvery,acquireActivationLease:async()=>({assertHeld:async()=>{},release:async()=>{}})}));
 // An unapproved district loses its data and its state (accounts, snapshots, secrets). The state folder does not exist before the first run.
 const listed=dir=>fsp.readdir(dir).catch(error=>error.code==='ENOENT'?[]:Promise.reject(error));
 for(const name of new Set([...await listed(dirs.districts),...await listed(dirs.state)].filter(entry=>!approved.includes(entry)))){
  await remove(path.join(dirs.districts,name));await remove(path.join(dirs.state,name));if(Object.hasOwn(LABELS,name))await open().forget(name).catch(()=>{});
 }
 // The district the tester last used first, so an interrupted run leaves the most useful one.
 const missing=approved.filter(id=>!present(dirs,id)).sort((a,b)=>(b===settings.district)-(a===settings.district));
 if(!missing.length){await remove(dirs.staging);await remove(path.join(dirs.cache,'objects'));return {districts:approved,downloaded:[]};}
 const release=keepAwake();let backoff=FIRST_WAIT,need=0;
 // Per district, just before it is downloaded (gzip + unzipped) and just before it is unzipped (the gzip is already on disk by then).
 const room=async bytes=>{need=bytes;const space=await statfs(dirs.root);if(space.bavail*space.bsize<bytes)throw noSpace(bytes);};
 const label=id=>LABELS[id]||id;
 async function retrying(task){
  for(;;){
   try{const value=await task();backoff=FIRST_WAIT;return value;}
   catch(error){
    if(signal?.aborted||error instanceof DistrictDataStop)throw error;
    const {reason,statusCode}=error;let wait;
    if(reason==='refused'&&statusCode===429)wait=THROTTLE_WAIT;
    else if(['unreachable','timeout','identity'].includes(reason)||reason==='refused'&&[502,503,504].includes(statusCode)){
     wait=backoff;backoff=Math.min(backoff*2,MAX_WAIT);if(reason==='timeout')open().shrinkChunk();
    }else if(reason==='refused'){try{await gate.refresh();}catch{/* offline or already revoked: the refresh hook handles it */}throw new DistrictDataStop('The access server refused the district download. Check this app\'s approval.',error);}
    else throw error;
    onStatus('Waiting for the connection. Retrying in '+after(wait)+'.');await sleep(wait,signal);
   }
  }
 }
 // A journal left by an earlier approval of this device cannot be read under the new one: start it afresh once.
 const rows=async id=>{try{return await open().status(id);}catch(error){if(!/another approved account/.test(error.message))throw error;await journal.write(null);return open().status(id);}};
 try{
  const c=open(),heads={};
  for(const id of missing){
   onStatus('Checking '+label(id)+' data...');
   await retrying(async()=>{
    // The journal says it is active but the data is gone (deleted or pruned): fetch it again from scratch.
    if((await rows(id)).some(row=>row.status==='active'))await c.forget(id);
    while((await c.catalog(id,{signal})).hasMore);
   });
   const latest=(await rows(id)).filter(row=>row.status!=='unsupported').sort((a,b)=>b.catalogSequence-a.catalogSequence)[0];
   if(!latest)throw new DistrictDataStop('No private data has been published for '+label(id)+' yet.');
   heads[id]=await c.revision(latest.revisionId);
  }
  const downloaded=[];
  for(const id of missing){
   const revision=heads[id],before={};let total=0;for(const item of revision.objects){before[item.role]=total;total+=item.size;}
   await room(objectBytes(revision,'size')+objectBytes(revision,'rawSize'));
   onStatus('Downloading '+label(id)+' — 0 MB of '+megabytes(total)+' MB');
   const fetched=await retrying(()=>c.download(revision.revisionId,{signal,onProgress:progress=>{
    // digestFile reports plain byte counts while re-checking a cached object; only range progress is shown.
    if(typeof progress!=='object')return;backoff=FIRST_WAIT;onStatus('Downloading '+label(id)+' — '+megabytes(before[progress.role]+progress.downloaded)+' MB of '+megabytes(total)+' MB');
   }}));
   if(fetched.status!=='validating')throw new DistrictDataStop('The '+label(id)+' data is not ready to install ('+fetched.status+'). Ask the administrator to publish it again.');
   await room(objectBytes(revision,'rawSize'));
   onStatus('Verifying '+label(id)+'...');
   const result=await c.activate(revision.revisionId,{signal});
   if(result.status!=='active'){
    const stop=systemStop(adapter.cause,need);if(stop)throw stop;
    await c.forget(id).catch(()=>{});
    throw new DistrictDataStop('The '+label(id)+' data failed verification'+(adapter.failure?' ('+adapter.failure+')':'')+' and was discarded.');
   }
   for(const item of revision.objects)await remove(path.join(dirs.cache,'objects',item.sha256+'.bin'));
   downloaded.push(id);
  }
  await remove(dirs.staging);
  return {districts:approved,downloaded};
 }catch(error){
  if(signal?.aborted||error instanceof DistrictDataStop)throw error;
  throw systemStop(error,need)||new DistrictDataStop('District data could not be prepared: '+String(error.message).slice(0,160),error);
 }finally{release();}
}
module.exports={ensureDistrictData,createAdapter,districtCheck,storeRoot,migrateSettings,removeOldDefaultData,sessionDirectory,logDirectory,bundleImportNotice,defaultDistrict,removeStore,present,gunzipTo,DistrictDataStop,KIND,REQUIRED,ALLOWED,LABELS};
