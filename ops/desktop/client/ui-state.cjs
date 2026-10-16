'use strict';
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');
const KEYS=Object.freeze(['mdesk_manual_v1','mdesk_bill_tracker_v1','mdesk_staffdir_v1','mdesk_pressov_v1','mdesk_coal_snapshots_v1','melange-compare','melange-members-columns','melange.interactions.wording','melange-influence-legend','melange-influence-rail']);
const COLUMNS=new Set(['rank','stance','tier','score','potential','invested','indmoney','roi','electoral','area','committees']);
const MAX_VALUE=6*1024*1024,MAX_TOTAL=10*1024*1024;
const MAX_ACTIVITY=10000,MAX_ACTIVITY_BYTES=4*1024*1024,SUBSTANTIVE=new Set(KEYS.slice(0,5));
const {binding}=require('./local-access.cjs');
const {checkedEvent}=require('./activity-outbox.cjs');
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.getPrototypeOf(value)===Object.prototype;
function validateValue(key,value){
  if(!KEYS.includes(key)||typeof value!=='string'||Buffer.byteLength(value)>MAX_VALUE)throw new Error('Unsupported or oversized saved preference.');
  if(key==='melange.interactions.wording'){if(!['posture','trend'].includes(value))throw new Error('Invalid wording preference.');return;}
  if(['melange-influence-legend','melange-influence-rail'].includes(key)){if(!['open','closed'].includes(value))throw new Error('Invalid panel preference.');return;}
  let parsed;try{parsed=JSON.parse(value);}catch{throw new Error('Saved preference must contain valid JSON.');}
  if(key==='melange-compare'){if(!Array.isArray(parsed)||parsed.length>4||parsed.some(v=>typeof v!=='string'||v.length>32))throw new Error('Invalid comparison selection.');}
  else if(key==='melange-members-columns'){if(!Array.isArray(parsed)||parsed.length>COLUMNS.size||new Set(parsed).size!==parsed.length||parsed.some(v=>!COLUMNS.has(v)))throw new Error('Invalid member columns.');}
  else if(key==='mdesk_coal_snapshots_v1'){if(!Array.isArray(parsed)||parsed.length>1000||parsed.some(v=>!plain(v)))throw new Error('Invalid coalition snapshots.');}
  else if(!plain(parsed)||Object.values(parsed).some(v=>!plain(v)))throw new Error('Invalid saved workspace entries.');
  let nodes=0;
  function walk(item,depth){
    if(++nodes>100000||depth>12)throw new Error('Saved preference is too complex.');
    if(item===null||typeof item==='boolean'||typeof item==='string')return;
    if(typeof item==='number'&&Number.isFinite(item))return;
    if(Array.isArray(item)){for(const value of item)walk(value,depth+1);return;}
    if(!plain(item))throw new Error('Invalid saved preference.');
    for(const [name,value]of Object.entries(item)){if(name.length>2048||['__proto__','prototype','constructor'].includes(name))throw new Error('Invalid saved preference field.');walk(value,depth+1);}
  }
  walk(parsed,0);
}
function validateValues(values){
  if(!plain(values)||Object.keys(values).some(key=>!KEYS.includes(key)))throw new Error('Invalid saved workspace state.');
  for(const [key,value]of Object.entries(values))validateValue(key,value);
  if(Buffer.byteLength(JSON.stringify(values))>MAX_TOTAL)throw new Error('Saved workspace exceeds its storage limit.');
  return values;
}
function atomicWrite(filename,value){
  fs.mkdirSync(path.dirname(filename),{recursive:true,mode:0o700});
  const temporary=filename+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';
  try{const fd=fs.openSync(temporary,'wx',0o600);try{fs.writeFileSync(fd,JSON.stringify(value));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}fs.renameSync(temporary,filename);}
  finally{try{fs.unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
}
function activityIdentity(accepted){
  if(!accepted?.grant||accepted.revoked!==false||accepted.grant.offlineAllowed!==true)throw new Error('Approved account required to record saved work.');
  const {role,districtIds,...identity}=binding(accepted.grant);if(identity.appId!=='melange')throw new Error('Invalid saved-work identity.');return identity;
}
function validateActivity(entries){
  if(!Array.isArray(entries)||entries.length>MAX_ACTIVITY||Buffer.byteLength(JSON.stringify(entries))>MAX_ACTIVITY_BYTES)throw new Error('Saved-work activity journal is full or invalid.');
  const seen=new Set();
  for(const item of entries){
    if(!plain(item)||Object.keys(item).sort().join('|')!=='event|identity'||!plain(item.identity))throw new Error('Invalid saved-work activity journal.');
    const identity=activityIdentity({revoked:false,grant:{...item.identity,role:'local_user',districtIds:[],offlineAllowed:true}});
    if(JSON.stringify(identity)!==JSON.stringify(item.identity))throw new Error('Invalid saved-work activity identity.');
    // Clock rollback must not make committed data unreadable. The actual outbox
    // validates time again before sending; a future event remains in this journal.
    const event=checkedEvent(item.event,'melange',Math.max(Date.now(),item.event?.occurredAt*1000));
    if(event.type!=='notes_updated'||event.occurredAt>4102444800||Object.keys(event).length!==6||Object.keys(event.counts).length||seen.has(event.eventId))throw new Error('Invalid saved-work activity event.');
    seen.add(event.eventId);
  }
  return entries;
}
class UIStateStore{
  constructor(directory,{write=atomicWrite,onMutation=()=>{},activityContext=()=>null,now=Date.now}={}){
    this.filename=path.join(directory,'melange-ui-state.json');this.write=write;this.onMutation=onMutation;this.activityContext=activityContext;this.now=now;this.values={};this.activity=[];this.draining=false;
    if(fs.existsSync(this.filename)){
      if(fs.statSync(this.filename).size>MAX_TOTAL+MAX_ACTIVITY_BYTES+1024)throw new Error('Saved workspace state exceeds its storage limit.');
      const raw=JSON.parse(fs.readFileSync(this.filename,'utf8')),allowed=raw.schemaVersion===1?['schemaVersion','values']:['schemaVersion','values','activity'];
      if(![1,2].includes(raw.schemaVersion)||Object.keys(raw).sort().join('|')!==allowed.sort().join('|'))throw new Error('Unsupported saved workspace state.');
      this.values=validateValues(raw.values);this.activity=raw.schemaVersion===2?validateActivity(raw.activity):[];
    }
  }
  get activityPending(){return this.activity.length>0;}
  commit(values,activity){validateValues(values);validateActivity(activity);this.write(this.filename,{schemaVersion:2,values,activity});this.values=values;this.activity=activity;}
  snapshot(){return {...this.values};}
  mutate(operation,key,value){
    const next={...this.values};
    if(operation==='set'){validateValue(key,value);next[key]=value;}
    else if(operation==='remove'){if(!KEYS.includes(key))throw new Error('Unsupported saved preference.');delete next[key];}
    else if(operation==='clear'){if(key!==undefined||value!==undefined)throw new Error('Invalid clear request.');for(const name of KEYS)delete next[name];}
    else throw new Error('Unsupported saved preference operation.');
    const changed=JSON.stringify(next)!==JSON.stringify(this.values),substantive=operation==='clear'?[...SUBSTANTIVE].some(name=>Object.hasOwn(this.values,name)):SUBSTANTIVE.has(key);
    const activity=[...this.activity];
    if(changed&&substantive){const accepted=this.activityContext();if(accepted)activity.push({identity:activityIdentity(accepted),event:{eventId:crypto.randomUUID(),type:'notes_updated',occurredAt:Math.floor(this.now()/1000),districtId:null,status:'succeeded',counts:{}}});}
    this.commit(next,activity);
    // The event was already committed with the data. This only wakes the drain;
    // a failed/out-of-process sink cannot lose the intent or relabel a saved edit.
    if(activity.length)try{this.onMutation();}catch{}
    return true;
  }
  importMissing(values){validateValues(values);this.commit({...values,...this.values},this.activity);}
  async drainActivity(accepted,appendCommitted){
    if(this.draining)return {inProgress:true};const identity=JSON.stringify(activityIdentity(accepted));this.draining=true;let drained=0;
    try{
      for(const item of [...this.activity]){
        if(JSON.stringify(item.identity)!==identity)continue;
        await appendCommitted(accepted,structuredClone(item.event));
        // A renderer may save while the protected outbox write awaits. Commit
        // the current values, removing only this original journal identity.
        this.commit(this.values,this.activity.filter(entry=>entry.event.eventId!==item.event.eventId));drained++;
      }
      return {drained,pending:this.activity.length};
    }finally{this.draining=false;}
  }
}
function trustedStateCaller(event,{window,runtime,appId}){
  if(appId!=='melange'||!window||window.isDestroyed()||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame||!runtime.origin||runtime.requestsAllowed===false)return false;
  try{return new URL(event.senderFrame.url).origin===runtime.origin;}catch{return false;}
}
function registerStateIPC(ipcMain,getContext,store){
  ipcMain.on('desktop:state-load',event=>{try{if(!trustedStateCaller(event,getContext())){event.returnValue={enabled:false};return;}event.returnValue={enabled:true,keys:KEYS,values:store.snapshot()};}catch{event.returnValue={enabled:true,error:'Saved workspace state could not be loaded.'};}});
  ipcMain.on('desktop:state-write',(event,operation,key,value)=>{try{if(!trustedStateCaller(event,getContext()))throw new Error('Untrusted saved preference request.');store.mutate(operation,key,value);event.returnValue={ok:true};}catch(error){event.returnValue={ok:false,error:error.message};}});
}
module.exports={KEYS,MAX_VALUE,MAX_TOTAL,MAX_ACTIVITY,validateValue,validateValues,UIStateStore,trustedStateCaller,registerStateIPC};
