'use strict';
// Main-process transport/journal. Production registry is empty until a compiled
// domain adapter proves its export, import and transaction recovery contracts.
const fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),crypto=require('node:crypto');
const {binding}=require('./local-access.cjs');const {canonical}=require('./access-client.cjs');
const {exact,uuid,hash,integer,scope,verifyRevision,proposal,uploadStatus,CHUNK}=require('./dataset-protocol.cjs');
const {datasetRequest}=require('./dataset-transport.cjs');
const STATUSES=new Set(['registering','downloading','validating','pending-idle','conflict','active','unsupported','failed']);
const MAX_JOURNAL=7*1024*1024,MAX_REVISIONS=10000;
function stableIdentity(grant){const {role,districtIds,...identity}=binding(grant);return identity;}
function keyFor(revision){return (revision.districtId||'local')+':'+revision.datasetId;}
function canceled(signal){if(signal?.aborted)throw new Error('Dataset transfer canceled');}
function within(root,target){const relative=path.relative(root,target);return relative!==''&&!relative.startsWith('..'+path.sep)&&relative!=='..'&&!path.isAbsolute(relative);}
async function atomic(file,bytes){const temporary=file+'.'+crypto.randomUUID()+'.tmp';let handle;try{handle=await fsp.open(temporary,'wx');await handle.writeFile(bytes);await handle.sync();await handle.close();handle=null;await fsp.rename(temporary,file);}finally{await handle?.close();await fsp.unlink(temporary).catch(error=>{if(error.code!=='ENOENT')throw error;});}}
async function digestFile(file,{signal,onProgress}={}){
 const before=await fsp.stat(file),digest=crypto.createHash('sha256');let size=0;
 for await(const chunk of fs.createReadStream(file,{highWaterMark:CHUNK})){canceled(signal);digest.update(chunk);size+=chunk.length;onProgress?.(size);}
 const after=await fsp.stat(file);if(before.size!==after.size||before.mtimeMs!==after.mtimeMs||before.ino!==after.ino||size!==before.size)throw new Error('Dataset object changed during verification');return {sha256:digest.digest('hex'),size};
}
class DatasetClient{
 constructor({access,store,profile,registry=Object.freeze({}),request=datasetRequest,acquireActivationLease=async()=>null,now=Date.now,minimumFreeBytes=512*1024*1024}){
  if(!access?.accepted||!store?.read||!store?.write||typeof profile!=='string')throw new Error('Verified access and protected dataset journal required');
  this.access=access;this.store=store;this.profile=fs.realpathSync.native(profile);this.root=path.join(this.profile,'dataset-sync');this.registry=registry;this.request=request;this.acquireActivationLease=acquireActivationLease;this.now=now;this.minimumFreeBytes=minimumFreeBytes;this.busy=false;
 }
 async operation(action){if(this.busy)throw new Error('A dataset operation is already running');this.busy=true;try{return await action();}finally{this.busy=false;}}
 async accepted(){const accepted=await this.access.accepted();if(!accepted||accepted.revoked||accepted.grant.offlineAllowed!==true)throw new Error('Active dataset approval required');return accepted;}
 async send(relative,options={}){const {grant}=await this.accepted(),token=(await this.access.secrets.read('access-token'))?.tokens?.[grant.grantId];return this.request(this.access.trust,relative,{...options,token});}
 async initialize(){
  for(const directory of [this.root,...['manifests','objects','exports'].map(name=>path.join(this.root,name))]){
   await fsp.mkdir(directory,{recursive:false}).catch(error=>{if(error.code!=='EEXIST')throw error;});
   const stat=await fsp.lstat(directory),real=await fsp.realpath(directory);if(stat.isSymbolicLink()||!stat.isDirectory()||!within(this.profile,real))throw new Error('Dataset cache leaves the application profile');
  }
 }
 async file(group,name){if(!['manifests','objects','exports'].includes(group)||typeof name!=='string'||!(/^[a-z0-9.-]{1,110}$/).test(name)||name==='.'||name==='..')throw new Error('Invalid dataset cache name');await this.initialize();const directory=path.join(this.root,group),file=path.join(directory,name);try{const stat=await fsp.lstat(file);if(stat.isSymbolicLink()||!stat.isFile()||!within(await fsp.realpath(directory),await fsp.realpath(file)))throw new Error('Unsafe dataset cache object');}catch(error){if(error.code!=='ENOENT')throw error;}return file;}
 async readJournal(grant){
  const identity=stableIdentity(grant),value=await this.store.read();if(!value)return {schemaVersion:1,identity,cursors:{},revisions:{},active:{},exports:{}};
  exact(value,['schemaVersion','identity','cursors','revisions','active','exports']);
  if(value.schemaVersion!==1||canonical(value.identity)!==canonical(identity)||Buffer.byteLength(JSON.stringify(value))>MAX_JOURNAL)throw new Error('Dataset journal belongs to another approved account or is invalid');
  for(const name of ['cursors','revisions','active','exports'])if(!value[name]||typeof value[name]!=='object'||Array.isArray(value[name]))throw new Error('Invalid dataset journal');
  if(Object.keys(value.revisions).length>MAX_REVISIONS||Object.keys(value.exports).length>MAX_REVISIONS)throw new Error('Dataset journal capacity exceeded');
  for(const [id,item]of Object.entries(value.revisions)){if(!uuid(id)||!item||!hash(item.digest)||!uuid(item.datasetId)||!STATUSES.has(item.status)||!Array.isArray(item.parents)||item.parents.some(parent=>!uuid(parent))||item.parents.length>8||!integer(item.catalogSequence)||!item.objects||typeof item.objects!=='object')throw new Error('Invalid dataset revision journal');for(const transfer of Object.values(item.objects))if(!transfer||!hash(transfer.sha256)||!integer(transfer.size)||!integer(transfer.offset,transfer.size)||typeof transfer.verified!=='boolean')throw new Error('Invalid dataset transfer journal');}
  for(const [id,item]of Object.entries(value.exports))if(!uuid(id)||!item||!hash(item.proposalHash)||!STATUSES.has(item.status)||!item.objects||typeof item.objects!=='object'||Array.isArray(item.objects))throw new Error('Invalid dataset export journal');
  for(const cursor of Object.values(value.cursors))if(!integer(cursor))throw new Error('Invalid dataset catalog cursor');
  for(const id of Object.values(value.active))if(!uuid(id)||!Object.hasOwn(value.revisions,id))throw new Error('Invalid active dataset pointer');
  return structuredClone(value);
 }
 async commit(journal){if(Buffer.byteLength(JSON.stringify(journal))>MAX_JOURNAL||Object.keys(journal.revisions).length>MAX_REVISIONS||Object.keys(journal.exports).length>MAX_REVISIONS)throw new Error('Dataset journal is full; prior state was preserved');await this.store.write(journal);}
 async manifest(revisionId,grant,journal){
  if(!uuid(revisionId)||!Object.hasOwn(journal.revisions,revisionId))throw new Error('Unknown dataset revision');const file=await this.file('manifests',revisionId+'.json');if((await fsp.stat(file)).size>100000)throw new Error('Cached dataset manifest too large');
  const checked=verifyRevision(JSON.parse(await fsp.readFile(file,'utf8')),this.access.trust,grant,{registry:this.registry,now:this.now()});if(checked.revision.revisionId!==revisionId||checked.digest!==journal.revisions[revisionId].digest)throw new Error('Cached dataset manifest changed');return checked;
 }
 conflicts(journal){
  const groups=new Map();for(const [id,row]of Object.entries(journal.revisions)){const key=(row.districtId||'local')+':'+row.datasetId;if(!groups.has(key))groups.set(key,new Set());groups.get(key).add(id);}
  for(const [id,row]of Object.entries(journal.revisions)){const group=groups.get((row.districtId||'local')+':'+row.datasetId);for(const parent of row.parents)group.delete(parent);}
  for(const [id,row]of Object.entries(journal.revisions)){const heads=groups.get((row.districtId||'local')+':'+row.datasetId);if(heads.size>1&&heads.has(id)&&row.status!=='unsupported'){row.status='conflict';row.reason='competing_heads';}}
 }
 async catalog(districtId=null,{signal}={}){return this.operation(async()=>{
  const {grant}=await this.accepted();scope(grant,districtId);const journal=await this.readJournal(grant),scopeKey=districtId||'local',after=journal.cursors[scopeKey]||0;
  const result=await this.send('/v1/data/catalog?after='+after+'&limit=25'+(districtId?'&districtId='+districtId:''),{signal});exact(result,['revisions','nextCursor','hasMore']);
  if(!Array.isArray(result.revisions)||result.revisions.length>25||!integer(result.nextCursor)||result.nextCursor<after||typeof result.hasMore!=='boolean'||result.hasMore&&(!result.revisions.length||result.nextCursor===after))throw new Error('Invalid dataset catalog');
  let sequence=after;const seen=new Set();
  for(const envelope of result.revisions){canceled(signal);const checked=verifyRevision(envelope,this.access.trust,grant,{registry:this.registry,now:this.now()}),revision=checked.revision;
   if(revision.districtId!==districtId||revision.catalogSequence<=sequence||revision.catalogSequence>result.nextCursor||seen.has(revision.revisionId))throw new Error('Contradictory dataset catalog');sequence=revision.catalogSequence;seen.add(revision.revisionId);
   const old=journal.revisions[revision.revisionId];if(old&&old.digest!==checked.digest)throw new Error('Dataset revision identity changed');
   for(const existing of Object.values(journal.revisions))if(existing.datasetId===revision.datasetId&&(existing.districtId!==revision.districtId||existing.kind!==revision.kind))throw new Error('Dataset kind or scope changed');
   const filename=await this.file('manifests',revision.revisionId+'.json');await atomic(filename,Buffer.from(JSON.stringify(envelope)));
   if(!old)journal.revisions[revision.revisionId]={digest:checked.digest,datasetId:revision.datasetId,districtId:revision.districtId,kind:revision.kind,parents:revision.parentRevisionIds,catalogSequence:revision.catalogSequence,status:checked.supported?'registering':'unsupported',objects:{}};
  }
  // A missing ancestor is explicitly deferred; no branch can silently replace it.
  for(const row of Object.values(journal.revisions)){
   for(const parent of row.parents){const existing=journal.revisions[parent];if(existing&&(existing.datasetId!==row.datasetId||existing.districtId!==row.districtId||existing.kind!==row.kind||existing.catalogSequence>=row.catalogSequence))throw new Error('Dataset parent graph is contradictory');}
   if(row.parents.some(parent=>!Object.hasOwn(journal.revisions,parent))&&row.status!=='unsupported'){row.status='registering';row.reason='parents_missing';}
  }
  this.conflicts(journal);journal.cursors[scopeKey]=result.nextCursor;await this.commit(journal);return {received:result.revisions.length,hasMore:result.hasMore,nextCursor:result.nextCursor};
 });}
 async status(districtId=null){const {grant}=await this.accepted();scope(grant,districtId);const journal=await this.readJournal(grant);return Object.entries(journal.revisions).filter(([,row])=>row.districtId===districtId).map(([revisionId,row])=>({revisionId,datasetId:row.datasetId,status:row.status,reason:row.reason||null,bytes:Object.values(row.objects).reduce((sum,item)=>sum+item.offset,0)}));}
 async download(revisionId,{signal,onProgress}={}){return this.operation(async()=>{
  const {grant}=await this.accepted(),journal=await this.readJournal(grant),checked=await this.manifest(revisionId,grant,journal),revision=checked.revision,row=journal.revisions[revisionId];
  if(!checked.supported){row.status='unsupported';await this.commit(journal);return {status:'unsupported'};}
  if(row.status==='conflict'||revision.parentRevisionIds.some(id=>!Object.hasOwn(journal.revisions,id)))return {status:row.status==='conflict'?'conflict':'registering',reason:'parents_or_conflicts'};
  row.status='downloading';delete row.reason;await this.commit(journal);
  try{
   for(const object of revision.objects){canceled(signal);const complete=await this.file('objects',object.sha256+'.bin'),part=await this.file('objects',object.sha256+'.part');
    let transfer=row.objects[object.role];if(!transfer)transfer=row.objects[object.role]={sha256:object.sha256,size:object.size,offset:0,verified:false};
    if(transfer.sha256!==object.sha256||transfer.size!==object.size)throw new Error('Dataset transfer binding changed');
    if(fs.existsSync(complete)){const actual=await digestFile(complete,{signal,onProgress});if(actual.sha256!==object.sha256||actual.size!==object.size)throw new Error('Invalid cached dataset object');transfer.offset=object.size;transfer.verified=true;await this.commit(journal);continue;}
    const disk=await fsp.statfs(path.dirname(part));if(disk.bavail*disk.bsize<object.size-transfer.offset+this.minimumFreeBytes)throw new Error('Insufficient dataset staging space');
    const handle=await fsp.open(part,fs.existsSync(part)?'r+':'wx');
    try{
     const stat=await handle.stat();if(stat.size<transfer.offset){transfer.offset=0;await handle.truncate(0);await handle.sync();await this.commit(journal);}else if(stat.size>transfer.offset){await handle.truncate(transfer.offset);await handle.sync();}
     while(transfer.offset<object.size){canceled(signal);const length=Math.min(CHUNK,object.size-transfer.offset),bytes=await this.send('/v1/data/objects/'+object.sha256+'?offset='+transfer.offset+'&length='+length+'&revisionId='+revisionId,{signal});if(!Buffer.isBuffer(bytes)||bytes.length!==length)throw new Error('Invalid dataset range');
      let written=0;while(written<bytes.length){const result=await handle.write(bytes,written,bytes.length-written,transfer.offset+written);if(result.bytesWritten<=0)throw new Error('Dataset staging write failed');written+=result.bytesWritten;}await handle.sync();transfer.offset+=bytes.length;await this.commit(journal);onProgress?.({revisionId,role:object.role,downloaded:transfer.offset,total:object.size});
     }
    }finally{await handle.close();}
    const actual=await digestFile(part,{signal});if(actual.sha256!==object.sha256||actual.size!==object.size)throw new Error('Dataset object integrity failed');
    await fsp.rename(part,complete);transfer.verified=true;await this.commit(journal);
   }
   row.status='validating';await this.commit(journal);return {status:'validating'};
  }catch(error){row.status='failed';row.reason=signal?.aborted?'canceled':'transfer_failed';await this.commit(journal);throw error;}
 });}
 async activate(revisionId,{signal}={}){return this.operation(async()=>{
  const {grant}=await this.accepted(),journal=await this.readJournal(grant),checked=await this.manifest(revisionId,grant,journal),revision=checked.revision,row=journal.revisions[revisionId];
  if(!checked.supported)return {status:'unsupported'};this.conflicts(journal);
  if(row.status==='conflict')return {status:'conflict'};
  for(const parent of revision.parentRevisionIds){const parentRow=journal.revisions[parent];if(!parentRow)return {status:'registering',reason:'parents_missing'};if(parentRow.datasetId!==revision.datasetId||parentRow.districtId!==revision.districtId||parentRow.catalogSequence>=revision.catalogSequence)throw new Error('Dataset parent scope changed');}
  if(journal.active[keyFor(revision)]===revisionId&&row.status==='active')return {status:'active',alreadyActive:true};
  const adapter=checked.adapter;if(!adapter.validate||!adapter.activate||!adapter.recoverActivation)throw new Error('Dataset activation adapter is incomplete');
  const objects={};for(const item of revision.objects){if(!row.objects[item.role]?.verified)throw new Error('Dataset objects are not verified');const file=await this.file('objects',item.sha256+'.bin'),actual=await digestFile(file,{signal});if(actual.sha256!==item.sha256||actual.size!==item.size)throw new Error('Dataset object changed before activation');objects[item.role]=file;}
  row.status='validating';await this.commit(journal);
  if(await adapter.validate({revision,objects,signal})!==true){row.status='failed';row.reason='validation_failed';await this.commit(journal);return {status:'failed',reason:'validation_failed'};}
  const lease=await this.acquireActivationLease({appId:grant.appId,districtId:revision.districtId,datasetId:revision.datasetId,revisionId});
  if(!lease){row.status='pending-idle';await this.commit(journal);return {status:'pending-idle'};}
  if(typeof lease.assertHeld!=='function'||typeof lease.release!=='function')throw new Error('Dataset activation requires an exclusive coordinator lease');
  try{
  await lease.assertHeld();const latest=await this.accepted();if(canonical(stableIdentity(latest.grant))!==canonical(stableIdentity(grant)))throw new Error('Approved dataset identity changed during validation');scope(latest.grant,revision.districtId);canceled(signal);
  const previous=journal.active[keyFor(revision)]||null;
  if(previous&&previous!==revisionId&&!revision.parentRevisionIds.includes(previous)){row.status='conflict';row.reason='local_active_revision_changed';await this.commit(journal);return {status:'conflict'};}
  let result;
  try{
   if(row.transaction){await lease.assertHeld();result=await adapter.recoverActivation({revision,objects,transaction:structuredClone(row.transaction)});}
   else{row.transaction={transactionId:crypto.randomUUID(),eventId:crypto.randomUUID(),priorRevisionId:previous};await this.commit(journal);await lease.assertHeld();result=await adapter.activate({revision,objects,transaction:structuredClone(row.transaction)});}
   if(!result||result.activated!==true||result.activityIntentDurable!==true)throw new Error('Dataset transaction is not durably complete');
   row.status='active';delete row.reason;row.activityPending=result.activityPending!==false;journal.active[keyFor(revision)]=revisionId;await this.commit(journal);return {status:'active',activityPending:row.activityPending};
  }catch(error){row.status='failed';row.reason='activation_recovery_required';await this.commit(journal);throw error;}
  }finally{await lease.release();}
 });}
 async register(value,{signal,onProgress}={}){return this.operation(async()=>{
  // value comes from a compiled domain export adapter after its durable local
  // commit. Objects must already be immutable snapshots in our fixed cache.
  const {grant}=await this.accepted();proposal(value,grant,{registry:this.registry});
  const journal=await this.readJournal(grant),proposalHash=crypto.createHash('sha256').update(canonical(value)).digest('hex');
  let row=journal.exports[value.revisionId];if(row&&row.proposalHash!==proposalHash)throw new Error('Local dataset revision identity changed');
  if(!row)row=journal.exports[value.revisionId]={proposalHash,status:'registering',objects:{}};
  await this.commit(journal);
  try{
   for(const object of value.objects){canceled(signal);const source=await this.file('exports',object.sha256+'.bin'),actual=await digestFile(source,{signal});if(actual.sha256!==object.sha256||actual.size!==object.size)throw new Error('Local dataset export changed');
    let progress=row.objects[object.role];if(!progress)progress=row.objects[object.role]={sha256:object.sha256,size:object.size,offset:0};
    if(progress.sha256!==object.sha256||progress.size!==object.size)throw new Error('Local dataset export binding changed');
    let status=uploadStatus(progress.uploadId?await this.send('/v1/data/uploads/'+progress.uploadId,{signal}):await this.send('/v1/data/uploads',{method:'POST',body:{districtId:value.districtId,sha256:object.sha256,size:object.size},signal}),{uploadId:progress.uploadId,size:object.size});
    Object.assign(progress,{uploadId:status.uploadId,offset:status.offset,status:status.status});await this.commit(journal);
    if(status.status==='failed')throw new Error('Dataset server verification failed');
    if(status.status==='verifying')return {status:'registering',reason:'verification_pending'};
    if(!status.complete){
     const handle=await fsp.open(source,'r');try{
      while(progress.offset<object.size){canceled(signal);const length=Math.min(CHUNK,object.size-progress.offset),bytes=Buffer.alloc(length);let read=0;while(read<length){const result=await handle.read(bytes,read,length-read,progress.offset+read);if(result.bytesRead<=0)throw new Error('Local dataset export shortened');read+=result.bytesRead;}
       status=uploadStatus(await this.send('/v1/data/uploads/'+progress.uploadId+'?offset='+progress.offset,{method:'PUT',bytes,signal}),{uploadId:progress.uploadId,size:object.size});
       if(status.offset!==progress.offset+length||status.status!=='uploading')throw new Error('Invalid dataset upload acknowledgment');progress.offset=status.offset;await this.commit(journal);onProgress?.({revisionId:value.revisionId,role:object.role,uploaded:progress.offset,total:object.size});
      }
     }finally{await handle.close();}
     status=uploadStatus(await this.send('/v1/data/uploads/'+progress.uploadId+'/complete',{method:'POST',body:{},signal}),{uploadId:progress.uploadId,size:object.size});progress.status=status.status;await this.commit(journal);
     if(status.status==='failed')throw new Error('Dataset server verification failed');if(!status.complete)return {status:'registering',reason:'verification_pending'};
    }
   }
   const result=await this.send('/v1/data/revisions',{method:'POST',body:value,signal});exact(result,['revision','conflict']);if(typeof result.conflict!=='boolean')throw new Error('Invalid dataset commit acknowledgment');
   const checked=verifyRevision(result.revision,this.access.trust,grant,{registry:this.registry,now:this.now()}),received=checked.revision;
   if(canonical(Object.fromEntries(Object.keys(value).map(key=>[key,received[key]])))!==canonical(value)||received.originPrincipalId!==grant.principalId||received.originDeviceId!==grant.deviceId)throw new Error('Dataset commit belongs to another export');
   const filename=await this.file('manifests',received.revisionId+'.json');await atomic(filename,Buffer.from(JSON.stringify(result.revision)));
   const old=journal.revisions[received.revisionId];if(old&&old.digest!==checked.digest)throw new Error('Dataset revision changed');
   if(!old)journal.revisions[received.revisionId]={digest:checked.digest,datasetId:received.datasetId,districtId:received.districtId,kind:received.kind,parents:received.parentRevisionIds,catalogSequence:received.catalogSequence,status:result.conflict?'conflict':'registering',objects:{}};
   row.status=result.conflict?'conflict':'registering';row.committed=true;await this.commit(journal);return {status:row.status,committed:true,revisionId:received.revisionId};
  }catch(error){row.status='failed';row.reason=signal?.aborted?'canceled':'registration_failed';await this.commit(journal);throw error;}
 });}
}
module.exports={DatasetClient,digestFile,STATUSES,MAX_JOURNAL};
