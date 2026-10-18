'use strict';
const os=require('node:os');
const fs=require('node:fs'),path=require('node:path');
const {performance}=require('node:perf_hooks');
const {districtConfiguration}=require('./district-switch.cjs');
const {runtimeRoots}=require('./runtime-roots.cjs');
function districtFingerprint(profile,configuration){
 const roots=runtimeRoots('terrier',profile,configuration),records=[];
 const record=filename=>{try{const stat=fs.lstatSync(filename,{bigint:true});records.push([filename,String(stat.dev),String(stat.ino),String(stat.size),String(stat.mtimeNs),String(stat.ctimeNs),stat.isSymbolicLink()]);}catch(error){if(error.code!=='ENOENT')throw error;records.push([filename,null]);}};
 for(const name of ['scores','contacts','street','walk','roads'])for(const suffix of ['','.sqlite3-wal','.sqlite3-journal'])record(path.join(roots.data,name+(suffix||'.sqlite3')));
 for(const suffix of ['','-wal','-journal'])record(path.join(roots.state,'accounts.sqlite3'+suffix));
 record(path.join(roots.data,'.desktop-password-lock-disabled'));
 const snapshots=path.join(roots.state,'snapshots');let entries=[];try{entries=fs.readdirSync(snapshots).sort();}catch(error){if(error.code!=='ENOENT')throw error;}
 records.push(['snapshots',entries]);for(const key of entries)for(const name of ['manifest.json','rows.json','geometry.json','streets.geojson','district.geojson','area_indices.json','adjacency.json'])record(path.join(snapshots,key,name));
 return JSON.stringify([roots,records]);
}
function approvalIdentity(authorization){const grant=authorization?.accepted?.grant;if(!grant||authorization.accepted.revoked)throw new Error('Current approval is required.');return JSON.stringify({serverId:grant.serverId,deviceId:grant.deviceId,principalId:grant.principalId,accountIdentity:grant.accountIdentity,role:grant.role,districtIds:grant.districtIds,owner:Boolean(authorization.owner)});}
class DistrictTransition{
 constructor({read,write,scope,verify,createRuntime,current,activate,navigate,location,canReveal,fingerprint=configuration=>JSON.stringify(configuration),onUnavailable=async()=>{},log=()=>{},freeMemory=os.freemem,maxWarm=3}){
  Object.assign(this,{read,write,scope,verify,createRuntime,current,activate,navigate,location,canReveal,fingerprint,onUnavailable,log,freeMemory,maxWarm});this.pool=new Map();this.generation=0;this.busy=false;this.pendingReady=null;this.starting=null;
 }
 mark(operation,phase,extra={}){this.log({event:'district-switch',district:operation.district,phase,elapsedMs:Math.round((performance.now()-operation.started)*10)/10,...extra});}
 cancel(){this.generation++;}
 async suspend(){this.cancel();this.pendingReady=null;for(const item of this.pool.values())item.runtime.requestsAllowed=false;if(this.starting&&this.starting!==this.current())await this.starting.stop();await this.clearInactive();}
 async clearInactive(){const active=this.current();for(const [id,item]of this.pool){if(item.runtime!==active){this.pool.delete(id);await item.runtime.stop();}}}
 async stopAll(){this.cancel();const runtimes=new Set([this.current(),this.starting,...Array.from(this.pool.values(),item=>item.runtime)]);this.pool.clear();await Promise.all(Array.from(runtimes,runtime=>runtime?.stop()));}
 async prune(){const active=this.current();for(const [id,item]of this.pool){if(item.runtime!==active&&(this.pool.size>this.maxWarm||this.freeMemory()<2*1024**3)){this.pool.delete(id);await item.runtime.stop();}}}
 remember(configuration,runtime){this.pool.delete(configuration.district);this.pool.set(configuration.district,{runtime,fingerprint:this.fingerprint(configuration)});}
 rendererReady(){const operation=this.pendingReady;if(!operation||operation.district!==this.read().district)return;operation.readyAt=performance.now();if(operation.committed){this.pendingReady=null;this.mark(operation,'renderer-ready',{cacheHit:operation.cacheHit});}}
 async switch(district){
  if(this.busy)throw new Error('A district is already opening.');
  const previous=this.read();if(district===previous.district)return false;
  const selected=districtConfiguration(previous,district,this.scope(previous));
  const operation={district,started:performance.now(),cacheHit:false},generation=++this.generation,old=this.current();
  const check=()=>{if(generation!==this.generation)throw new Error('District switch cancelled.');};
  this.busy=true;this.pendingReady=null;this.mark(operation,'begin');
  let target,changed=false,loaded=false,oldLocation=this.location();
  try{
   if(await old.activity()===true)throw new Error('Wait for the current calculation to finish before switching districts.');check();
   await this.verify(selected);check();this.mark(operation,'data-verified');
   const remembered=this.pool.get(district);target=remembered?.runtime;
   if(target&&remembered.fingerprint!==this.fingerprint(selected)){this.pool.delete(district);await target.stop();target=null;}
   if(target&&!target.child){this.pool.delete(district);await target.stop();target=null;}
   if(!target){await this.prune();check();target=this.createRuntime();}else operation.cacheHit=true;
   // Runtime.start freshly authorizes both new and retained backends.
   const candidateFingerprint=this.fingerprint(selected);
   this.starting=target;await target.start(selected);check();this.mark(operation,'backend-ready',{cacheHit:operation.cacheHit});
   if(operation.cacheHit&&this.fingerprint(selected)!==candidateFingerprint)throw new Error('District data changed while opening. Please try again.');
   const loadedFingerprint=this.fingerprint(selected);
   if(approvalIdentity(target.authorization)!==approvalIdentity(old.authorization)){await this.clearInactive();throw new Error('Access changed. Reopen the workspace before switching districts.');}
   if(await old.activity()===true)throw new Error('Wait for the current calculation to finish before switching districts.');check();
   this.write(selected);this.activate(target);changed=true;
   if(!await this.canReveal(target.authorization))throw new Error('Access changed while opening the district.');check();
   if(this.fingerprint(selected)!==loadedFingerprint)throw new Error('District data changed while opening. Please try again.');
   let pathname='/';try{pathname='/'+(new URL(oldLocation).pathname.split('/').filter(Boolean)[0]||'');}catch{}
   const destination=target.launchPath&&target.launchPath!=='/'?target.launchPath:pathname;
   this.pendingReady=operation;
   await this.navigate(target.origin+destination);loaded=true;check();
   if(!await this.canReveal(target.authorization))throw new Error('Access changed while opening the district.');check();
   if(this.fingerprint(selected)!==loadedFingerprint)throw new Error('District data changed while opening. Please try again.');
   target.launchPath='/';this.remember(selected,target);operation.committed=true;
   this.mark(operation,'navigation-ready',{cacheHit:operation.cacheHit});if(operation.readyAt){this.pendingReady=null;this.mark(operation,'renderer-ready',{cacheHit:operation.cacheHit,rendererElapsedMs:Math.round((operation.readyAt-operation.started)*10)/10});}await this.prune();return true;
  }catch(error){
   this.mark(operation,'failed',{cacheHit:operation.cacheHit});
   this.pendingReady=null;
   if(changed){this.write(previous);this.activate(old);if(loaded||this.location()!==oldLocation){try{if(await this.canReveal(old.authorization))await this.navigate(oldLocation);else await this.onUnavailable();}catch{await this.onUnavailable();}}}
   if(target&&target!==old){this.pool.delete(district);await target.stop();}
   throw error;
  }finally{this.starting=null;this.busy=false;}
 }
}
module.exports={DistrictTransition,approvalIdentity,districtFingerprint};
