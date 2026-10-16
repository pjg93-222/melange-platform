'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {canonical,verifyGrant}=require('../access-client.cjs');
const {verifyRevision,proposal,strictJson,uploadStatus,CHUNK}=require('../dataset-protocol.cjs');
const {DatasetClient}=require('../dataset-client.cjs');
const fixtureLease=async()=>({assertHeld:async()=>{},release:async()=>{}});
function fixture(bytes=Buffer.from('fixture dataset')){
 const pair=crypto.generateKeyPairSync('ed25519'),raw=pair.publicKey.export({format:'der',type:'spki'}).subarray(-32),keyId=crypto.createHash('sha256').update(raw).digest('hex').slice(0,24),key=raw.toString('base64');
 const trust={schemaVersion:1,purpose:'melange-desktop-trust',serverId:crypto.randomUUID(),url:'https://127.0.0.1:7443',tlsSpkiSha256:'a'.repeat(64),accessPublicKeys:{[keyId]:key},updatePublicKeysByApp:Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{[keyId]:key}]))};
 const principalId=crypto.randomUUID(),grant={appId:'mentat',serverId:trust.serverId,deviceId:crypto.randomUUID(),principalId,grantId:crypto.randomUUID(),accountIdentity:'recipient_'+principalId.replaceAll('-',''),role:'local_user',districtIds:[],offlineAllowed:true};
 const body={schemaVersion:1,datasetId:crypto.randomUUID(),revisionId:crypto.randomUUID(),parentRevisionIds:[],districtId:null,kind:'mentat.forecast.v1',adapterVersion:1,objects:[{role:'fixture',sha256:crypto.createHash('sha256').update(bytes).digest('hex'),size:bytes.length}],counts:{files:1},purpose:'melange-desktop-dataset',serverId:trust.serverId,appId:'mentat',originPrincipalId:principalId,originDeviceId:grant.deviceId,committedAt:Math.floor(Date.now()/1000),catalogSequence:1};
 const sign=(value,rawBytes=Buffer.from(canonical(value)))=>({schemaVersion:1,keyId,payload:rawBytes.toString('base64'),signature:crypto.sign(null,rawBytes,pair.privateKey).toString('base64')});
 const adapter={appId:'mentat',version:1,validateMetadata:value=>value.objects.every(o=>o.role==='fixture'),validate:async()=>true,activate:async()=>({activated:true,activityIntentDurable:true,activityPending:true}),recoverActivation:async()=>({activated:true,activityIntentDurable:true,activityPending:true})};
 const registry={'mentat.forecast.v1':adapter};let revoked=false;
 const access={trust,accepted:async()=>({grant,revoked}),secrets:{read:async()=>({tokens:{[grant.grantId]:crypto.randomBytes(48).toString('base64url')}})}};
 return {trust,grant,body,sign,bytes,adapter,registry,access,revoke:()=>{revoked=true;}};
}
function store(folder){const file=path.join(folder,'fixture-journal.json');return {read:async()=>fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):null,write:async value=>{fs.writeFileSync(file+'.tmp',JSON.stringify(value));fs.renameSync(file+'.tmp',file);}};}
test('signed dataset bounds stay separate, scope/schema/signatures strict and production registry empty',()=>{
 const f=fixture(),signed=f.sign(f.body);assert.equal(verifyRevision(signed,f.trust,f.grant).supported,false);assert.equal(verifyRevision(signed,f.trust,f.grant,{registry:f.registry}).supported,true);
 for(const changes of [{appId:'melange'},{districtId:'co-05'},{purpose:'melange-desktop-access'},{extra:true},{kind:'mentat.unreviewed.v1'},{serverId:crypto.randomUUID()}])assert.throws(()=>verifyRevision(f.sign({...f.body,...changes}),f.trust,f.grant,{registry:f.registry}));
 assert.throws(()=>verifyRevision({...signed,signature:Buffer.alloc(64).toString('base64')},f.trust,f.grant));
 const multiple={...f.body,parentRevisionIds:[crypto.randomUUID(),crypto.randomUUID()]};assert.throws(()=>verifyRevision(f.sign(multiple),f.trust,f.grant,{registry:f.registry}),/merge/);
 const larger={...f.body,objects:Array.from({length:128},(_,i)=>({role:'part'+i,sha256:'a'.repeat(64),size:0}))};assert.ok(Buffer.byteLength(canonical(larger))>8192);assert.equal(verifyRevision(f.sign(larger),f.trust,f.grant).supported,false);assert.throws(()=>verifyGrant(f.sign(larger),f.trust,f.grant));
 const duplicate=canonical(f.body).replace('"adapterVersion":1','"adapterVersion":1,"adapterVersion":1');assert.throws(()=>verifyRevision(f.sign(null,Buffer.from(duplicate)),f.trust,f.grant),/Noncanonical/);
});
test('strict unsigned JSON and upload state reject duplicate aliases, invalid status/size and prototype fields',()=>{
 for(const value of ['{"x":1,"x":2}','{"__proto__":{}}','[1,]','{"x":1e999}','{"x":true}x','{"x":"\\q"}'])assert.throws(()=>strictJson(Buffer.from(value)));
 assert.equal(strictJson(Buffer.from(' {"x": [1,true,null]} ')).x.length,3);
 const value={uploadId:crypto.randomUUID(),offset:0,size:0,complete:true,status:'complete'};assert.equal(uploadStatus(value).complete,true);
 for(const change of [{size:-1},{offset:1},{complete:false},{error:'private path'}])assert.throws(()=>uploadStatus({...value,...change}));
});
test('download resumes durable offset after interruption; busy, validation failure and revoked access preserve data',async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dataset-client-fixture-'));try{
  const f=fixture(Buffer.alloc(CHUNK+19,7)),journal=store(folder);let breakTail=true,idle=false;const offsets=[];
  const request=async(_trust,route)=>{if(route.startsWith('/v1/data/catalog'))return {revisions:[f.sign(f.body)],nextCursor:1,hasMore:false};const url=new URL(route,'https://127.0.0.1'),offset=Number(url.searchParams.get('offset')),length=Number(url.searchParams.get('length'));offsets.push(offset);if(offset&&breakTail)throw new Error('fixture interruption');return f.bytes.subarray(offset,offset+length);};
  let client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,acquireActivationLease:async()=>idle?fixtureLease():null,minimumFreeBytes:0});await client.catalog();await assert.rejects(client.download(f.body.revisionId),/interruption/);assert.equal((await client.status())[0].bytes,CHUNK);
  client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,acquireActivationLease:async()=>idle?fixtureLease():null,minimumFreeBytes:0});breakTail=false;await client.download(f.body.revisionId);assert.equal(offsets.at(-1),CHUNK);
  assert.equal((await client.activate(f.body.revisionId)).status,'pending-idle');idle=true;f.adapter.validate=async()=>false;assert.equal((await client.activate(f.body.revisionId)).reason,'validation_failed');assert.deepEqual((await journal.read()).active,{});
  f.adapter.validate=async()=>true;assert.equal((await client.activate(f.body.revisionId)).status,'active');f.revoke();await assert.rejects(client.status(),/approval/);assert.equal(Object.keys((await journal.read()).active).length,1);
 }finally{fs.rmSync(folder,{recursive:true});}
});
test('domain activation crash recovers the same durable event ID without replaying mutation',async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dataset-activation-fixture-'));try{
  const f=fixture(),journal=store(folder);let applied=0,eventId;
  f.adapter.activate=async({transaction})=>{applied++;eventId=transaction.eventId;fs.writeFileSync(path.join(folder,'domain-pointer.json'),JSON.stringify(transaction));throw new Error('fixture crash after domain commit');};
  f.adapter.recoverActivation=async({transaction})=>{assert.deepEqual(transaction,JSON.parse(fs.readFileSync(path.join(folder,'domain-pointer.json'),'utf8')));return {activated:true,activityIntentDurable:true,activityPending:true};};
  const request=async(_trust,route)=>route.startsWith('/v1/data/catalog')?{revisions:[f.sign(f.body)],nextCursor:1,hasMore:false}:f.bytes;
  let client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,acquireActivationLease:fixtureLease,minimumFreeBytes:0});await client.catalog();await client.download(f.body.revisionId);await assert.rejects(client.activate(f.body.revisionId),/fixture crash/);
  client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,acquireActivationLease:fixtureLease,minimumFreeBytes:0});assert.equal((await client.activate(f.body.revisionId)).status,'active');assert.equal(applied,1);assert.equal((await journal.read()).revisions[f.body.revisionId].transaction.eventId,eventId);
 }finally{fs.rmSync(folder,{recursive:true});}
});
test('altered objects, competing heads and cached manifest tampering never activate',async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dataset-integrity-fixture-'));try{
  const f=fixture(),journal=store(folder);let revisions=[f.body],corrupt=true;
  const request=async(_trust,route)=>route.startsWith('/v1/data/catalog')?{revisions:revisions.map(value=>f.sign(value)),nextCursor:revisions.at(-1).catalogSequence,hasMore:false}:corrupt?Buffer.alloc(f.bytes.length):f.bytes;
  const client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,acquireActivationLease:fixtureLease,minimumFreeBytes:0});await client.catalog();await assert.rejects(client.download(f.body.revisionId),/integrity/);assert.deepEqual((await journal.read()).active,{});
  const branchA={...f.body,revisionId:crypto.randomUUID(),parentRevisionIds:[f.body.revisionId],catalogSequence:2},branchB={...branchA,revisionId:crypto.randomUUID(),catalogSequence:3};revisions=[branchA,branchB];await client.catalog();assert.equal((await client.activate(branchA.revisionId)).status,'conflict');
  const manifest=await client.file('manifests',branchA.revisionId+'.json');fs.writeFileSync(manifest,JSON.stringify(f.sign({...branchA,counts:{files:99}})));await assert.rejects(client.activate(branchA.revisionId),/changed/);
 }finally{fs.rmSync(folder,{recursive:true});}
});
test('upload lost acknowledgment resumes server offset, async verification waits, and immutable receipt persists',async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dataset-upload-fixture-'));try{
  const f=fixture(Buffer.alloc(CHUNK+11,3)),journal=store(folder);const value=Object.fromEntries(['schemaVersion','datasetId','revisionId','parentRevisionIds','districtId','kind','adapterVersion','objects','counts'].map(key=>[key,f.body[key]]));
  const uploadId=crypto.randomUUID();let offset=0,phase='uploading',lost=true,puts=0;const status=()=>({uploadId,offset,size:f.bytes.length,status:phase,complete:phase==='complete'});
  const request=async(_trust,route,options)=>{if(route==='/v1/data/uploads'||route==='/v1/data/uploads/'+uploadId)return status();if(options.method==='PUT'){assert.equal(Number(new URL(route,'https://127.0.0.1').searchParams.get('offset')),offset);offset+=options.bytes.length;puts++;if(lost){lost=false;throw new Error('lost upload acknowledgment');}return status();}if(route.endsWith('/complete')){phase='verifying';return status();}if(route==='/v1/data/revisions')return {revision:f.sign(f.body),conflict:false};throw new Error('unexpected route');};
  let client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,minimumFreeBytes:0});const source=await client.file('exports',f.body.objects[0].sha256+'.bin');fs.writeFileSync(source,f.bytes);await assert.rejects(client.register(value),/lost upload/);
  client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,minimumFreeBytes:0});assert.equal((await client.register(value)).reason,'verification_pending');assert.equal(puts,2);phase='complete';const result=await client.register(value);assert.equal(result.committed,true);assert.equal((await journal.read()).exports[value.revisionId].committed,true);assert.ok(fs.existsSync(source));
 }finally{fs.rmSync(folder,{recursive:true});}
});

test('latest district approval is rechecked inside an exclusive activation lease; new work cannot race commit',async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dataset-lease-fixture-'));try{
  const f=fixture(),journal=store(folder);Object.assign(f.grant,{appId:'terrier',role:'local_administrator',districtIds:['co-05','or-05']});Object.assign(f.body,{appId:'terrier',kind:'terrier.record-changes.v1',districtId:'co-05'});f.adapter.appId='terrier';f.registry={'terrier.record-changes.v1':f.adapter};
  let held=false,writes=0,workStarted=0;const startWork=()=>{if(held)throw new Error('activation lease held');workStarted++;};
  const lease=async()=>{held=true;return {assertHeld:async()=>{assert.equal(held,true);},release:async()=>{held=false;}};};
  const request=async(_trust,route)=>route.startsWith('/v1/data/catalog')?{revisions:[f.sign(f.body)],nextCursor:1,hasMore:false}:f.bytes;
  const client=new DatasetClient({access:f.access,store:journal,profile:folder,registry:f.registry,request,acquireActivationLease:lease,minimumFreeBytes:0});await client.catalog('co-05');await client.download(f.body.revisionId);
  f.adapter.validate=async()=>{f.grant.districtIds=['or-05'];return true;};f.adapter.activate=async()=>{writes++;return {activated:true,activityIntentDurable:true};};
  await assert.rejects(client.activate(f.body.revisionId),/district.*approved/);assert.equal(writes,0);assert.equal(held,false);assert.deepEqual((await journal.read()).active,{});
  f.grant.districtIds=['co-05','or-05'];f.adapter.validate=async()=>true;f.adapter.activate=async()=>{assert.throws(startWork,/lease held/);writes++;return {activated:true,activityIntentDurable:true};};
  assert.equal((await client.activate(f.body.revisionId)).status,'active');assert.equal(writes,1);assert.equal(workStarted,0);assert.equal(held,false);startWork();assert.equal(workStarted,1);
 }finally{fs.rmSync(folder,{recursive:true});}
});
