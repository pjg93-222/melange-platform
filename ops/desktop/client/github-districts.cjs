'use strict';
// Owner-only public transport. Approval credentials never enter this transport;
// the recipient access-server dataset client retains its separate TLS protocol.
const fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),crypto=require('node:crypto');
const {pipeline}=require('node:stream/promises'),{Transform}=require('node:stream');
const {canonical}=require('./access-client.cjs');
const {strictJson,exact}=require('./dataset-protocol.cjs');
const {response}=require('./github-source.cjs');
const protocol=require('./github-district-protocol.cjs');
const {REPOSITORY,POINTER_PATH,ROLES,IDS,COMMIT,MAX_MANIFEST,assetURL,requireValue}=protocol;
const API='https://api.github.com/repos/'+REPOSITORY;
class ConnectionError extends Error{constructor(cause){super('GitHub district connection unavailable');this.cause=cause;}}
const connectionFailure=error=>error?.githubRateLimit===true||['ENETUNREACH','EHOSTUNREACH','ENOTFOUND','EAI_AGAIN','ECONNRESET','ECONNREFUSED','ETIMEDOUT'].includes(error?.code)||/^GitHub (?:request (?:deadline exceeded|timed out)|release unavailable \((?:502|503|504)\))$/.test(error?.message||'');
const TEXT=Object.freeze({checking:'Checking GitHub district data.',downloading:'Downloading verified district data from GitHub.',verifying:'Verifying district data.',ready:'Using verified GitHub district data cached on this computer.',offline:'GitHub could not be reached. Using previously verified district data.',update:'New GitHub district data is ready. Reopen this district to use it.',error:'GitHub district data could not be verified. Existing files were preserved.'});
function canceled(signal){if(signal?.aborted)throw new Error('GitHub district operation canceled');}
async function streamed(url,{asset=false,signal,deadlineMs}={}){
 canceled(signal);const stream=await response(url,{asset,deadlineMs});
 const abort=()=>stream.destroy(new Error('GitHub district operation canceled'));
 signal?.addEventListener('abort',abort,{once:true});stream.once('close',()=>signal?.removeEventListener('abort',abort));
 if(signal?.aborted)abort();return stream;
}
const publicTransport=Object.freeze({
 async read(url,{maxBytes,signal,asset=false}){const stream=await streamed(url,{asset,signal}),chunks=[];let size=0;for await(const chunk of stream){size+=chunk.length;if(size>maxBytes){stream.destroy();throw new Error('GitHub district metadata exceeds limit');}chunks.push(chunk);}return Buffer.concat(chunks);},
 async download(url,target,{bytes,signal,onProgress}){let count=0;const stream=await streamed(url,{asset:true,signal,deadlineMs:60*60*1000});const counter=new Transform({transform(chunk,_encoding,done){count+=chunk.length;if(count>bytes)return done(new Error('GitHub district archive exceeds declared size'));onProgress?.(count);done(null,chunk);}});await pipeline(stream,counter,fs.createWriteStream(target,{flags:'wx',mode:0o600}),{signal});if(count!==bytes)throw new Error('GitHub district archive is incomplete');const file=await fsp.open(target,'r+');try{await file.sync();}finally{await file.close();}}
});
const fingerprint=stat=>[String(stat.dev),String(stat.ino),String(stat.size),String(stat.mtimeNs),String(stat.ctimeNs)].join('|');
async function digest(file,signal){
 const before=await fsp.lstat(file,{bigint:true});requireValue(before.isFile()&&!before.isSymbolicLink(),'Unsafe GitHub district file');
 const hash=crypto.createHash('sha256');let bytes=0;
 for await(const chunk of fs.createReadStream(file)){canceled(signal);hash.update(chunk);bytes+=chunk.length;}
 requireValue(fingerprint(await fsp.lstat(file,{bigint:true}))===fingerprint(before)&&BigInt(bytes)===before.size,'GitHub district file changed while verifying');
 return {bytes,sha256:hash.digest('hex'),fingerprint:fingerprint(before)};
}
const within=(root,target)=>{const relative=path.relative(root,target);return relative!==''&&!relative.startsWith('..'+path.sep)&&relative!=='..'&&!path.isAbsolute(relative);};
async function safeDirectory(root,target,{create=false}={}){
 requireValue(within(root,target),'GitHub cache leaves its managed root');
 let current=root;
 for(const part of path.relative(root,target).split(path.sep)){
  current=path.join(current,part);if(create)await fsp.mkdir(current,{mode:0o700}).catch(error=>{if(error.code!=='EEXIST')throw error;});
  const stat=await fsp.lstat(current);requireValue(stat.isDirectory()&&!stat.isSymbolicLink()&&within(root,await fsp.realpath(current)),'Unsafe GitHub cache directory');
 }
 return target;
}
async function discardStaging(root,stagingRoot,target){
 // Never let an extractor failure turn its destination into a generic delete
 // primitive. Resolve the managed parent, unlink links without following them,
 // and remove directories one level at a time (no recursive rm traversal).
 requireValue(path.dirname(target)===stagingRoot&&/^[a-f0-9-]{36}$/.test(path.basename(target))&&within(root,target),'Unsafe GitHub staging cleanup target');
 await safeDirectory(root,stagingRoot);
 let visited=0;
 async function remove(current,depth){
  requireValue(++visited<=10000&&depth<=40&&within(stagingRoot,current),'Unsafe GitHub staging cleanup inventory');
  let stat;try{stat=await fsp.lstat(current);}catch(error){if(error.code==='ENOENT')return;throw error;}
  if(stat.isSymbolicLink()){await fsp.unlink(current);return;}
  requireValue(await fsp.realpath(path.dirname(current))===path.dirname(current),'GitHub staging cleanup parent changed');
  if(!stat.isDirectory()){await fsp.unlink(current);return;}
  requireValue(await fsp.realpath(current)===current,'GitHub staging cleanup directory changed');
  for(const name of await fsp.readdir(current))await remove(path.join(current,name),depth+1);
  await fsp.rmdir(current);
 }
 await remove(target,0);
}
function identity(authorization){const grant=authorization.accepted.grant;return {appId:'terrier',repository:REPOSITORY,serverId:grant.serverId,deviceId:grant.deviceId,principalId:grant.principalId,accountIdentity:grant.accountIdentity};}
function legacy(configuration){return path.resolve(configuration.terrierLegacy.dataDirectory);}
function configurationFor(configuration,entry,root){const {ownerGitHubData,...base}=configuration;if(entry.source==='legacy')return base;return {...base,ownerGitHubData:{schemaVersion:1,district:configuration.district,cacheRoot:root,catalogueSha256:entry.manifestSha256}};}
class GitHubDistricts{
 constructor({resources,profile,root,publicKeys,store,transport=publicTransport,checkExisting,extract,onStatus=()=>{},signal,now=Date.now,statfs=fsp.statfs}){
  requireValue(typeof profile==='string'&&typeof root==='string'&&publicKeys&&Object.keys(publicKeys).length>0&&Object.keys(publicKeys).length<=8&&store?.read&&store?.write&&typeof transport?.read==='function'&&typeof transport.download==='function'&&typeof checkExisting==='function'&&typeof extract==='function','GitHub district provider requires pinned keys and protected storage');
  for(const [id,value]of Object.entries(publicKeys))requireValue(/^[A-Za-z0-9._-]{1,100}$/.test(id)&&typeof value==='string'&&Buffer.from(value,'base64').length===32&&Buffer.from(value,'base64').toString('base64')===value,'Invalid GitHub district verification key');
  Object.assign(this,{resources,profile:path.resolve(profile),root:path.resolve(root),publicKeys:structuredClone(publicKeys),store,transport,checkExisting,extract,onStatus,signal,now,statfs});this.states=new Map();this.busy=false;
 }
 status(configuration,phase,extra={}){const previous=this.states.get(configuration.district),retained={};for(const key of ['releaseTag','catalogueSequence','checkedAt','verifiedAt'])if(previous?.[key]!==undefined)retained[key]=previous[key];const value={provider:'github',repository:REPOSITORY,district:configuration.district,phase,cached:previous?.cached||false,canRefresh:true,...retained,...extra};this.states.set(configuration.district,value);this.onStatus(structuredClone(value));return structuredClone(value);}
 info(configuration){return this.states.has(configuration?.district)?structuredClone(this.states.get(configuration.district)):null;}
 async initialize(){
  await fsp.mkdir(this.root,{recursive:true,mode:0o700});requireValue(!(await fsp.lstat(this.root)).isSymbolicLink(),'Unsafe GitHub cache root');this.realRoot=await fsp.realpath(this.root);this.root=this.realRoot;
  for(const name of ['github-districts','github-districts/datasets','github-districts/downloads','github-districts/staging'])await safeDirectory(this.realRoot,path.join(this.root,...name.split('/')),{create:true});
 }
 directory(manifestSha256,district){return path.join(this.root,'github-districts','datasets',manifestSha256,district);}
 async readState(authorization){
  const expected=identity(authorization),saved=await this.store.read();
  if(!saved)return {schemaVersion:1,identity:expected,latest:null,catalogues:{},active:{},staged:{}};
  exact(saved,['schemaVersion','identity','latest','catalogues','active','staged']);requireValue(saved.schemaVersion===1&&canonical(saved.identity)===canonical(expected),'GitHub district state belongs to another owner');
  requireValue(Buffer.byteLength(JSON.stringify(saved))<6*1024*1024&&saved.catalogues&&typeof saved.catalogues==='object'&&!Array.isArray(saved.catalogues)&&Object.keys(saved.catalogues).length<=16);
  for(const [key,item]of Object.entries(saved.catalogues)){exact(item,['envelope','manifestBase64','commit']);requireValue(COMMIT.test(item.commit)&&typeof item.manifestBase64==='string'&&item.manifestBase64.length<4*Math.ceil(MAX_MANIFEST/3));const checked=protocol.pointer(item.envelope,this.publicKeys);requireValue(checked.digest===key);const bytes=Buffer.from(item.manifestBase64,'base64');requireValue(bytes.toString('base64')===item.manifestBase64);protocol.manifest(bytes,checked);}
  if(saved.latest!==null)requireValue(typeof saved.latest==='string'&&Object.hasOwn(saved.catalogues,saved.latest));
  for(const group of ['active','staged']){requireValue(saved[group]&&typeof saved[group]==='object'&&!Array.isArray(saved[group])&&Object.keys(saved[group]).length<=3);for(const [id,item]of Object.entries(saved[group])){exact(item,['pointerDigest','manifestSha256','catalogSequence','releaseTag','source','verifiedAt']);const checked=this.catalogue(saved,item.pointerDigest);requireValue(IDS.includes(id)&&['cache','legacy'].includes(item.source)&&item.manifestSha256===checked.manifest.sha256&&item.catalogSequence===checked.pointer.value.catalogSequence&&item.releaseTag===checked.pointer.value.releaseTag&&typeof item.verifiedAt==='string'&&Number.isFinite(Date.parse(item.verifiedAt)));}}
  return structuredClone(saved);
 }
 catalogue(state,key=state.latest){requireValue(key&&Object.hasOwn(state.catalogues,key),'No previously verified GitHub district catalogue');const row=state.catalogues[key],pointer=protocol.pointer(row.envelope,this.publicKeys);return {pointer,manifest:protocol.manifest(Buffer.from(row.manifestBase64,'base64'),pointer)};}
 async commit(state){
  const keep=new Set([state.latest,...Object.values(state.active).map(item=>item.pointerDigest),...Object.values(state.staged).map(item=>item.pointerDigest)]);for(const key of Object.keys(state.catalogues))if(!keep.has(key))delete state.catalogues[key];
  requireValue(Object.keys(state.catalogues).length<=7&&Buffer.byteLength(JSON.stringify(state))<6*1024*1024);await this.store.write(structuredClone(state));
 }
 async discoverCatalogue(state){
  const read=async(url,options={})=>{canceled(this.signal);let bytes;try{bytes=await this.transport.read(url,{maxBytes:512*1024,signal:this.signal,...options});}catch(error){throw connectionFailure(error)?new ConnectionError(error):error;}requireValue(Buffer.isBuffer(bytes));return bytes;};
  const commit=strictJson(await read(API+'/commits/main')).sha;requireValue(typeof commit==='string'&&COMMIT.test(commit)&&commit!=='0'.repeat(40),'Invalid GitHub branch commit');
  const content=strictJson(await read(API+'/contents/'+POINTER_PATH+'?ref='+commit));
  requireValue(content.type==='file'&&content.encoding==='base64'&&typeof content.content==='string'&&content.content.length<32768&&content.path===POINTER_PATH&&Number.isInteger(content.size)&&content.size>0&&content.size<=16384,'Invalid GitHub district pointer response');
  const compact=content.content.replaceAll('\n','');const pointerBytes=Buffer.from(compact,'base64');requireValue(pointerBytes.toString('base64')===compact&&pointerBytes.length===content.size);
  const previous=state.latest?this.catalogue(state).pointer:null,checked=protocol.pointer(strictJson(pointerBytes),this.publicKeys,previous?{catalogSequence:previous.value.catalogSequence,pointerDigest:previous.digest}:{});
  if(state.latest===checked.digest)return this.catalogue(state);
  const tag=strictJson(await read(API+'/git/ref/tags/'+checked.value.releaseTag));
  requireValue(tag.ref==='refs/tags/'+checked.value.releaseTag&&tag.object?.type==='commit'&&tag.object.sha===checked.value.releaseCommit,'GitHub district release commit changed');
  const bytes=await read(assetURL(checked.value.releaseTag,checked.value.manifest.asset),{maxBytes:checked.value.manifest.bytes,asset:true}),manifest=protocol.manifest(bytes,checked);
  state.catalogues[checked.digest]={envelope:checked.envelope,manifestBase64:bytes.toString('base64'),commit};state.latest=checked.digest;await this.commit(state);return {pointer:checked,manifest};
 }
 async verifyDirectory(directory,district,files,{managed=false}={}){
  try{
   if(managed)await safeDirectory(this.realRoot,directory);else{const stat=await fsp.lstat(directory);requireValue(stat.isDirectory()&&!stat.isSymbolicLink());}
   const verified=[];
   for(const item of files){
    const file=path.join(directory,item.role),actual=await digest(file,this.signal);if(actual.bytes!==item.bytes||actual.sha256!==item.sha256)return false;
    verified.push({file,fingerprint:actual.fingerprint});
    if(item.role.endsWith('.sqlite3')){for(const suffix of ['-wal','-shm','-journal'])if(await fsp.lstat(file+suffix).then(()=>true,error=>{if(error.code==='ENOENT')return false;throw error;}))return false;
     const header=Buffer.alloc(20),handle=await fsp.open(file,'r');try{requireValue((await handle.read(header,0,20,0)).bytesRead===20&&header.subarray(0,16).toString('latin1')==='SQLite format 3\0'&&header[18]===1&&header[19]===1);}finally{await handle.close();}}
   }
   canceled(this.signal);if(await this.checkExisting(directory,district)!==true)return false;
   for(const item of verified){const stat=await fsp.lstat(item.file,{bigint:true});requireValue(!stat.isSymbolicLink()&&fingerprint(stat)===item.fingerprint,'GitHub district files changed during metadata validation');if(item.file.endsWith('.sqlite3'))for(const suffix of ['-wal','-shm','-journal'])if(await fsp.lstat(item.file+suffix).then(()=>true,error=>{if(error.code==='ENOENT')return false;throw error;}))return false;}
   return true;
  }catch(error){if(this.signal?.aborted)throw error;return false;}
 }
 summary(bundle){return {releaseTag:bundle.releaseTag,catalogueSequence:bundle.catalogSequence,verifiedAt:bundle.verifiedAt,cached:true};}
 async useVerified(configuration,state,entry,{offline=false}={}){
  const checked=this.catalogue(state,entry.pointerDigest),directory=entry.source==='legacy'?legacy(configuration):this.directory(entry.manifestSha256,configuration.district);
  if(!await this.verifyDirectory(directory,configuration.district,checked.manifest.runtime[configuration.district],{managed:entry.source==='cache'}))return null;
  this.status(configuration,offline?'offline':'ready',{...this.summary(entry),checkedAt:new Date(this.now()).toISOString(),message:offline?TEXT.offline:TEXT.ready});return configurationFor(configuration,entry,this.root);
 }
 async prepare(configuration,state,checked){
  const district=configuration.district,{pointer,manifest}=checked,files=manifest.runtime[district];
  this.status(configuration,'verifying',{releaseTag:pointer.value.releaseTag,catalogueSequence:pointer.value.catalogSequence,checkedAt:new Date(this.now()).toISOString(),message:TEXT.verifying});
  let source;
  if(await this.verifyDirectory(legacy(configuration),district,files))source='legacy';
  const place=this.directory(manifest.sha256,district);
  if(!source&&await this.verifyDirectory(place,district,files,{managed:true}))source='cache';
  if(!source){
   const asset=manifest.assets.get(district),archive=path.join(this.root,'github-districts','downloads',asset.sha256+'.zip'),stagingRoot=path.join(this.root,'github-districts','staging'),staging=path.join(stagingRoot,crypto.randomUUID());
   // An invalid immutable cache is never silently replaced while a backend may
   // have it open. A new catalogue receives a new directory.
   const occupied=await fsp.lstat(place).then(()=>true,error=>{if(error.code==='ENOENT')return false;throw error;});requireValue(!occupied,'Existing GitHub district cache failed verification');
   const space=await this.statfs(this.root),need=asset.bytes+files.reduce((sum,item)=>sum+item.bytes,0)+512*1024*1024;requireValue(Number(space.bavail)*Number(space.bsize)>=need,'Insufficient GitHub district staging space');
   let archiveReady=false;try{const actual=await digest(archive,this.signal);archiveReady=actual.bytes===asset.bytes&&actual.sha256===asset.sha256;}catch(error){if(error.code!=='ENOENT')throw error;}
   if(!archiveReady){
    const temporary=archive+'.'+crypto.randomUUID()+'.part';
    let progressAt=0,progressBytes=-8*1024*1024;
    this.status(configuration,'downloading',{releaseTag:pointer.value.releaseTag,catalogueSequence:pointer.value.catalogSequence,receivedBytes:0,totalBytes:asset.bytes,message:TEXT.downloading});
    try{try{await this.transport.download(assetURL(pointer.value.releaseTag,asset.name),temporary,{bytes:asset.bytes,signal:this.signal,onProgress:receivedBytes=>{requireValue(Number.isSafeInteger(receivedBytes)&&receivedBytes>=0&&receivedBytes<=asset.bytes);const time=this.now();if(receivedBytes!==asset.bytes&&time-progressAt<250&&receivedBytes-progressBytes<8*1024*1024)return;progressAt=time;progressBytes=receivedBytes;this.status(configuration,'downloading',{releaseTag:pointer.value.releaseTag,catalogueSequence:pointer.value.catalogSequence,receivedBytes,totalBytes:asset.bytes,message:TEXT.downloading});}});}catch(error){throw connectionFailure(error)?new ConnectionError(error):error;}const actual=await digest(temporary,this.signal);requireValue(actual.bytes===asset.bytes&&actual.sha256===asset.sha256,'GitHub district archive integrity failed');await fsp.rename(temporary,archive);}finally{await fsp.unlink(temporary).catch(error=>{if(error.code!=='ENOENT')throw error;});}
   }
   this.status(configuration,'verifying',{releaseTag:pointer.value.releaseTag,catalogueSequence:pointer.value.catalogSequence,message:TEXT.verifying});
   try{
    const result=await this.extract({archive,destination:staging,district,files:structuredClone(files)});
    requireValue(result===true||result?.ok===true&&result.district===district&&result.sourceMetadataAccepted===true,'GitHub district extractor refused data');
    requireValue(await this.verifyDirectory(staging,district,files,{managed:true}),'Extracted GitHub district data failed verification');
    const names=await fsp.readdir(staging);requireValue(names.length===ROLES.length&&names.every(name=>ROLES.includes(name)),'Unexpected files in GitHub district staging');
    await safeDirectory(this.realRoot,path.dirname(place),{create:true});canceled(this.signal);await fsp.rename(staging,place);
   }finally{await discardStaging(this.realRoot,stagingRoot,staging);}
   source='cache';
  }
  return {pointerDigest:pointer.digest,manifestSha256:manifest.sha256,catalogSequence:pointer.value.catalogSequence,releaseTag:pointer.value.releaseTag,source,verifiedAt:new Date(this.now()).toISOString()};
 }
 async operation(configuration,authorization,{stageOnly=false}={}){
  if(!protocol.authorized(configuration,authorization))return stageOnly?null:configuration;
  requireValue(!this.busy,'GitHub district preparation is already running');this.busy=true;
  let state;
  try{
   canceled(this.signal);await this.initialize();state=await this.readState(authorization);
   const active=state.active[configuration.district];this.status(configuration,'checking',{...(active?this.summary(active):{}),cached:Boolean(active),message:TEXT.checking});
   const checked=await this.discoverCatalogue(state);
   const previous=state.active[configuration.district];
   if(stageOnly&&previous?.pointerDigest===checked.pointer.digest)return this.status(configuration,'ready',{...this.summary(previous),checkedAt:new Date(this.now()).toISOString(),updateAvailable:false,message:TEXT.ready});
   const entry=await this.prepare(configuration,state,checked);
   if(stageOnly){
    state.staged[configuration.district]=entry;await this.commit(state);
    const update=Boolean(previous&&previous.manifestSha256!==entry.manifestSha256);
    return this.status(configuration,update?'update-ready':'ready',{...this.summary(update?previous:entry),checkedAt:new Date(this.now()).toISOString(),updateAvailable:update,message:update?TEXT.update:TEXT.ready});
   }
   state.active[configuration.district]=entry;delete state.staged[configuration.district];await this.commit(state);
   this.status(configuration,'ready',{...this.summary(entry),checkedAt:new Date(this.now()).toISOString(),message:TEXT.ready});return configurationFor(configuration,entry,this.root);
  }catch(error){
   // Only connection failures permit offline use; untrusted metadata and
   // signature/rollback failures remain visible and preserve accepted state.
   if(!this.signal?.aborted&&error instanceof ConnectionError&&state?.active?.[configuration.district]){const reused=await this.useVerified(configuration,state,state.active[configuration.district],{offline:true});if(reused)return stageOnly?this.info(configuration):reused;}
   const active=state?.active?.[configuration.district];if(!this.signal?.aborted)this.status(configuration,'error',{...(active?this.summary(active):{}),cached:Boolean(active),message:TEXT.error});throw error;
  }
  finally{this.busy=false;}
 }
 ensure(configuration,authorization){return this.operation(configuration,authorization);}
 discover(configuration,authorization,{stageOnly=true}={}){return this.operation(configuration,authorization,{stageOnly});}
}
module.exports={GitHubDistricts,publicTransport,digest,safeDirectory,discardStaging,configurationFor,TEXT};
