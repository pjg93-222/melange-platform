'use strict';
// Only trusted successful-operation adapters call append. No renderer accepts
// arbitrary activity strings, paths, record contents or actor identities.
const crypto=require('node:crypto');
const {binding}=require('./local-access.cjs');
const TYPES=Object.freeze({melange:new Set(['assertion_saved','notes_updated','collection_completed','dataset_imported']),mentat:new Set(['collection_completed','report_created','dataset_imported']),terrier:new Set(['record_updated','walk_saved','snapshot_activated','dataset_imported'])});
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DISTRICTS=new Set(['co-05','ct-01','or-05']);
const MAX_EVENTS=10000,MAX_BYTES=8*1024*1024;
function uuid(value){return typeof value==='string'&&UUID.test(value)&&value!=='00000000-0000-0000-0000-000000000000';}
function identity(grant){const {role,districtIds,...value}=binding(grant);return value;}
function stable(value){if(Array.isArray(value))return '['+value.map(stable).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',')+'}';return JSON.stringify(value);}
function checkedEvent(value,appId,now){
 const required=['eventId','type','occurredAt','districtId','status','counts'],allowed=new Set([...required,'datasetId','revisionId','entityId']);
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!allowed.has(key))||required.some(key=>!Object.hasOwn(value,key))||!uuid(value.eventId)||!TYPES[appId]?.has(value.type)||value.status!=='succeeded')throw new Error('Invalid activity event');
 if(!Number.isSafeInteger(value.occurredAt)||value.occurredAt<946684800||value.occurredAt>Math.floor(now/1000)+300)throw new Error('Invalid activity timestamp');
 if(appId==='terrier'?!DISTRICTS.has(value.districtId):value.districtId!==null)throw new Error('Invalid activity district');
 if(!value.counts||typeof value.counts!=='object'||Array.isArray(value.counts)||Object.keys(value.counts).some(key=>!['items','files','records'].includes(key)||!Number.isSafeInteger(value.counts[key])||value.counts[key]<0||value.counts[key]>1000000000))throw new Error('Invalid activity counts');
 for(const key of ['datasetId','revisionId','entityId'])if(value[key]!==undefined&&!(uuid(value[key])||typeof value[key]==='string'&&/^[0-9a-f]{64}$/.test(value[key])))throw new Error('Invalid activity reference');
 return structuredClone(value);
}
function checkedState(value,expected,now){
 if(!value)return {schemaVersion:1,identity:expected,events:[]};
 if(Object.keys(value).sort().join('|')!=='events|identity|schemaVersion'||value.schemaVersion!==1||stable(value.identity)!==stable(expected)||!Array.isArray(value.events)||value.events.length>MAX_EVENTS||Buffer.byteLength(JSON.stringify(value))>MAX_BYTES)throw new Error('Invalid protected activity outbox');
 const seen=new Set();for(const event of value.events){checkedEvent(event,expected.appId,now);if(seen.has(event.eventId))throw new Error('Duplicate protected activity identity');seen.add(event.eventId);}
 return structuredClone(value);
}
function active(accepted){if(!accepted?.grant||accepted.revoked!==false||accepted.grant.offlineAllowed!==true)throw new Error('Active administrator approval required for activity');binding(accepted.grant);return accepted.grant;}
function permitted(grant,event){return grant.appId!=='terrier'||grant.districtIds.includes(event.districtId);}
class ActivityOutbox{
 constructor({store,now=Date.now}){if(!store?.read||!store?.write)throw new Error('Protected durable activity store required');this.store=store;this.now=now;this.queue=Promise.resolve();this.flushing=false;}
 serial(action){const result=this.queue.then(action);this.queue=result.catch(()=>{});return result;}
 async append(accepted,value){
  const grant=active(accepted),expected=identity(grant);
  if(!value||typeof value!=='object'||Object.hasOwn(value,'occurredAt'))throw new Error('Activity time is assigned by the trusted local adapter');
  const event=checkedEvent({...value,eventId:value.eventId||crypto.randomUUID(),occurredAt:Math.floor(this.now()/1000)},grant.appId,this.now());
  return this.appendChecked(grant,expected,event,false);
 }
 async appendCommitted(accepted,value){
  // Called only with a durable domain-journal event. Preserve its original
  // commit timestamp and ID; an invalid/future event remains in that journal.
  const grant=active(accepted),event=checkedEvent(value,grant.appId,this.now());
  return this.appendChecked(grant,identity(grant),event,true);
 }
 async appendChecked(grant,expected,event,preserveTime){
  if(!permitted(grant,event))throw new Error('Activity district is not approved');
  return this.serial(async()=>{
   const state=checkedState(await this.store.read(),expected,this.now()),previous=state.events.find(item=>item.eventId===event.eventId);
   if(previous){const comparable=preserveTime?event:{...event,occurredAt:previous.occurredAt};if(stable(previous)!==stable(comparable))throw new Error('Conflicting activity identity');return {eventId:previous.eventId,queued:state.events.length};}
   if(state.events.length>=MAX_EVENTS)throw new Error('Activity outbox is full; existing events were preserved');
   state.events.push(event);if(Buffer.byteLength(JSON.stringify(state))>MAX_BYTES)throw new Error('Activity outbox is full; existing events were preserved');
   await this.store.write(state);return {eventId:event.eventId,queued:state.events.length};
  });
 }
 async pending(accepted){const expected=identity(accepted.grant);return this.serial(async()=>{const state=checkedState(await this.store.read(),expected,this.now());return {queued:state.events.length};});}
 async flush(accepted,send){
  const grant=active(accepted),expected=identity(grant);if(typeof send!=='function')throw new Error('Fixed activity sender required');
  if(this.flushing)return {inProgress:true};this.flushing=true;
  try{
   const events=await this.serial(async()=>checkedState(await this.store.read(),expected,this.now()).events.filter(event=>permitted(grant,event)).slice(0,100));
   if(!events.length)return {acknowledged:0,...await this.pending(accepted)};
   // Concurrent append is allowed while the network request runs. The later
   // commit reloads the durable queue and removes only this batch's exact IDs.
   const result=await send(structuredClone(events));
   if(!result||Object.keys(result).sort().join('|')!=='acknowledged|receivedAt'||!Array.isArray(result.acknowledged)||result.acknowledged.length>events.length||new Set(result.acknowledged).size!==result.acknowledged.length||result.acknowledged.some(id=>!uuid(id)||!events.some(event=>event.eventId===id))||!Number.isSafeInteger(result.receivedAt)||result.receivedAt<946684800||result.receivedAt>Math.floor(this.now()/1000)+300)throw new Error('Invalid activity acknowledgment; queued events were preserved');
   return this.serial(async()=>{const state=checkedState(await this.store.read(),expected,this.now()),acked=new Set(result.acknowledged);state.events=state.events.filter(event=>!acked.has(event.eventId));await this.store.write(state);return {acknowledged:acked.size,queued:state.events.length};});
  }finally{this.flushing=false;}
 }
}
module.exports={ActivityOutbox,checkedEvent,MAX_EVENTS,MAX_BYTES};
