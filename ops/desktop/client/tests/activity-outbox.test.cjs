'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {ActivityOutbox,MAX_EVENTS}=require('../activity-outbox.cjs');
const accepted=(appId='mentat')=>({revoked:false,grant:{appId,serverId:crypto.randomUUID(),deviceId:crypto.randomUUID(),principalId:crypto.randomUUID(),accountIdentity:'recipient_fixture',role:appId==='terrier'?'local_administrator':'local_user',districtIds:appId==='terrier'?['co-05','or-05']:[],offlineAllowed:true}});
const event=(changes={})=>({type:'collection_completed',status:'succeeded',districtId:null,counts:{files:1},...changes});
function memory(){let state;return {read:async()=>structuredClone(state),write:async value=>{state=structuredClone(value);},peek:()=>state};}
test('offline queue survives process object restart on disk and clears only committed acknowledgment',async t=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'access-outbox-fixture-')),file=path.join(folder,'fixture.json');t.after(()=>fs.rmSync(folder,{recursive:true}));
 const store={read:async()=>fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):null,write:async value=>{fs.writeFileSync(file+'.pending',JSON.stringify(value));fs.renameSync(file+'.pending',file);}};
 const a=accepted();let box=new ActivityOutbox({store});const first=await box.append(a,event());assert.equal(first.queued,1);
 await assert.rejects(box.flush(a,async()=>{throw new Error('offline');}),/offline/);box=new ActivityOutbox({store});assert.equal((await box.pending(a)).queued,1);
 const delivered=[];await assert.rejects(box.flush(a,async events=>{delivered.push(events[0].eventId);throw new Error('ack response lost');}));
 assert.equal((await box.pending(a)).queued,1);await box.flush(a,async events=>{assert.equal(events[0].eventId,delivered[0]);return {acknowledged:[events[0].eventId],receivedAt:Math.floor(Date.now()/1000)};});
 assert.equal((await new ActivityOutbox({store}).pending(a)).queued,0);
});
test('append while flush waits is preserved; partial and unknown acknowledgments are bounded',async()=>{
 const store=memory(),a=accepted(),box=new ActivityOutbox({store});const first=await box.append(a,event());await box.append(a,event());
 let complete;const flushing=box.flush(a,()=>new Promise(resolve=>{complete=resolve;}));await new Promise(resolve=>setImmediate(resolve));
 await box.append(a,event());assert.deepEqual(await box.flush(a,async()=>{}),{inProgress:true});
 complete({acknowledged:[first.eventId],receivedAt:Math.floor(Date.now()/1000)});assert.equal((await flushing).queued,2);
 await assert.rejects(box.flush(a,async()=>({acknowledged:[crypto.randomUUID()],receivedAt:Math.floor(Date.now()/1000)})),/Invalid activity acknowledgment/);assert.equal((await box.pending(a)).queued,2);
});
test('revocation, wrong identity and revoked district do not send or discard pending events',async()=>{
 const store=memory(),a=accepted('terrier'),box=new ActivityOutbox({store});await box.append(a,event({type:'walk_saved',districtId:'co-05'}));await box.append(a,event({type:'walk_saved',districtId:'or-05'}));
 let sends=0;await assert.rejects(box.flush({...a,revoked:true},async()=>{sends++;}));assert.equal(sends,0);
 const narrower={...a,grant:{...a.grant,districtIds:['co-05']}};await box.flush(narrower,async events=>{assert.equal(events.length,1);assert.equal(events[0].districtId,'co-05');return {acknowledged:events.map(e=>e.eventId),receivedAt:Math.floor(Date.now()/1000)};});
 assert.equal((await box.pending(a)).queued,1);await assert.rejects(box.append(narrower,event({type:'walk_saved',districtId:'or-05'})),/not approved/);
 await assert.rejects(box.pending({...a,grant:{...a.grant,deviceId:crypto.randomUUID()}}),/Invalid protected/);assert.equal(store.peek().events.length,1);
});
test('only success metadata and typed references accepted; no record contents, paths or user supplied timestamp',async()=>{
 const store=memory(),a=accepted(),box=new ActivityOutbox({store});
 for(const changes of [{status:'failed'},{type:'arbitrary'},{path:'C:/private'},{message:'sensitive'},{districtId:'co-05'},{counts:{files:NaN}},{counts:{people:1}},{datasetId:'C:/dataset'},{occurredAt:0}])await assert.rejects(box.append(a,event(changes)));
 assert.equal(store.peek(),undefined);await box.append(a,event({datasetId:crypto.randomUUID(),revisionId:'a'.repeat(64)}));assert.equal(store.peek().events.length,1);
});
test('write failures preserve events, same completion ID is idempotent, conflicts and capacity are refused',async()=>{
 const store=memory(),a=accepted(),box=new ActivityOutbox({store}),id=crypto.randomUUID();await box.append(a,event({eventId:id}));await box.append(a,event({eventId:id}));assert.equal((await box.pending(a)).queued,1);
 await assert.rejects(box.append(a,event({eventId:id,counts:{files:2}})),/Conflicting/);
 const write=store.write;store.write=async()=>{throw new Error('disk unavailable');};await assert.rejects(box.flush(a,async events=>({acknowledged:events.map(e=>e.eventId),receivedAt:Math.floor(Date.now()/1000)})),/disk unavailable/);assert.equal((await box.pending(a)).queued,1);
 store.write=write;const current=store.peek();current.events=Array.from({length:MAX_EVENTS},()=>({...current.events[0],eventId:crypto.randomUUID()}));await assert.rejects(box.append(a,event()),/full/);assert.equal(store.peek().events.length,MAX_EVENTS);
});

test('committed domain journal retains original timestamp/ID and defers clock rollback without losing queued events',async()=>{
 const store=memory(),a=accepted();let now=2000000000000;let box=new ActivityOutbox({store,now:()=>now});
 const committed=event({eventId:crypto.randomUUID(),occurredAt:Math.floor(now/1000)-86400});await box.appendCommitted(a,committed);box=new ActivityOutbox({store,now:()=>now});await box.appendCommitted(a,committed);
 assert.deepEqual(store.peek().events,[committed]);await assert.rejects(box.appendCommitted(a,{...committed,occurredAt:committed.occurredAt+1}),/Conflicting/);
 now-=2*86400000;await assert.rejects(box.flush(a,async()=>{throw new Error('must not send');}),/timestamp/);assert.deepEqual(store.peek().events,[committed]);
 await assert.rejects(box.appendCommitted(a,{...committed,eventId:crypto.randomUUID()}),/timestamp/);assert.deepEqual(store.peek().events,[committed]);
 now+=2*86400000;assert.equal((await box.pending(a)).queued,1);
});
