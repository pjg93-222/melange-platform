'use strict';
const {checkedRecord}=require('./local-access.cjs');
const NAMES={melange:'Melange',mentat:'Mentat',terrier:'Terrier'};
// Optional keys may be absent; when present they must be strings like the required one.
function argument(value,key,...optional){if(!value||typeof value!=='object'||Array.isArray(value)||!Object.hasOwn(value,key)||Object.keys(value).some(name=>name!==key&&!optional.includes(name))||Object.values(value).some(item=>typeof item!=='string'))throw new Error('Invalid access action');return value[key];}
class DesktopAccessGate{
 constructor({appId,client,local,passwords,configuration,owner=async()=>null,now=Date.now}){
  if(!NAMES[appId])throw new Error('Unsupported app');Object.assign(this,{appId,client,local,passwords,configuration,owner,now});this.accepted=null;this.requestReply=null;this.cleanupPending=false;this.busy=false;
 }
 async evidence(){const accepted=await this.client.accepted();this.accepted=accepted;return accepted;}
 async info(){
  const accepted=await this.evidence(),raw=await this.client.state();const base={appId:this.appId,appName:NAMES[this.appId]};
  if(raw.pending&&accepted?.grant.requestId!==raw.pending.requestId){
   const reply=this.requestReply?.requestId===raw.pending.requestId?this.requestReply:null;
   return {...base,phase:'pending',...(reply?{requestStatus:reply.status,requestExpiresAt:reply.expiresAt,
    canRequestAgain:['denied','expired'].includes(reply.status)||(reply.status==='used'&&reply.expiresAt<=Math.floor(this.now()/1000)&&accepted?.grant.requestId!==raw.pending.requestId)}:{})};
  }
  if(!accepted)return {...base,phase:'needs_request'};
  // A non-legacy Terrier tester's private district store is deleted on revocation (main.cjs dropDistrictData); every other install keeps its local data.
  if(accepted.revoked){this.local?.lock();return {...base,phase:'revoked',canRequestAgain:true,...(this.appId==='terrier'&&this.configuration().terrierLegacy===undefined?{districtDataRemoved:true}:{})};}
  const grant=accepted.grant,owner=await this.owner(accepted,this.configuration());
  const accountIdentity=owner&&this.appId==='terrier'?owner.dataBindings.find(item=>item.districtId===this.configuration().district)?.localUsername:grant.accountIdentity;
  const identity={...base,accountIdentity:accountIdentity||grant.accountIdentity,...(this.cleanupPending?{requestCleanupPending:true}:{})};
  // The approved Terrier owner is never asked for a password; other Terrier accounts use Terrier's own sign-in.
  if(this.appId==='terrier')return {...identity,phase:owner?'ready':'terrier_signin'};
  // Melange/Mentat: verified once on this computer, then remembered (see LocalAccess.resume).
  if(owner||await this.local.resume(grant))return {...identity,phase:'ready'};
  const password=await this.passwords.read(grant);if(password)checkedRecord(password,grant);
  return {...identity,phase:password?'locked':'needs_password'};
 }
 async action(method,payload){
  if(this.busy)throw new Error('Access action already in progress');this.busy=true;
  try{
   const state=await this.info();
   if(method==='request'){
    const displayName=argument(payload,'displayName','inviteCode'),inviteCode=(payload.inviteCode??'').trim();if(displayName.trim()!==displayName)throw new Error('Invalid display name');if(payload.inviteCode!==undefined&&(inviteCode.length<1||inviteCode.length>64))throw new Error('Invalid invite code');
    if(state.phase!=='needs_request'&&!state.canRequestAgain)throw new Error('Current request must be preserved');
    this.requestReply=await this.client.requestAccess(displayName,{newRequest:state.phase==='pending',reapproval:Boolean(this.accepted),inviteCode});
   }else if(method==='poll'){
    if(payload!==undefined||state.phase!=='pending')throw new Error('No pending request');this.requestReply=await this.client.poll();
   }else if(method==='activate'){
    const key=argument(payload,'activationKey');if(state.phase!=='pending'||!['approved','used'].includes(state.requestStatus)||state.canRequestAgain)throw new Error('Approval is not ready');
    const result=await this.client.activate(key);this.cleanupPending=result.requestCleanupPending;this.requestReply=null;this.local?.lock();
   }else if(method==='setPassword'){
    const password=argument(payload,'password');if(state.phase!=='needs_password')throw new Error('Password setup unavailable');await this.local.create(this.accepted.grant,password);
   }else if(method==='unlock'){
    const password=argument(payload,'password');if(state.phase!=='locked')throw new Error('Password sign-in unavailable');await this.local.unlock(this.accepted.grant,password);
   }else if(method==='lock'){
    // Lock also forgets this computer, so the password is asked for again at the next open.
    if(payload!==undefined)throw new Error('Invalid access action');this.local?.lock();if(this.accepted&&!this.accepted.revoked)await this.local?.forget(this.accepted.grant);
   }else throw new Error('Unsupported access action');
   return await this.info();
  }finally{this.busy=false;}
 }
 async authorize(configuration=this.configuration()){
  const accepted=await this.evidence();if(!accepted||accepted.revoked)throw new Error('Administrator approval required');
  const grant=accepted.grant,owner=await this.owner(accepted,configuration);
  if(this.appId==='terrier'){
   if(!grant.districtIds.includes(configuration.district))throw new Error('District approval required');
   if(Boolean(configuration.terrierLegacy)!==Boolean(owner))throw new Error('Explicit existing-owner migration is required');
   const mapping=owner?.dataBindings.find(item=>item.districtId===configuration.district);
   return {accepted,owner,principal:{schemaVersion:1,serverId:grant.serverId,deviceId:grant.deviceId,
    principalId:grant.principalId,accountIdentity:grant.accountIdentity,localAccountIdentity:mapping?.localUsername||grant.accountIdentity,
    localUserId:mapping?.localUserId||null,role:grant.role,districtId:configuration.district,mode:owner?'existing-owner':'recipient'}};
  }
  const session=await this.local.resume(grant);if(!owner&&!session)throw new Error('Local sign-in required');
  return {accepted,owner,session,principal:null};
 }
 districts(){if(!this.accepted||this.accepted.revoked||this.appId!=='terrier')return [];return [...this.accepted.grant.districtIds];}
 async refresh(){await this.client.refresh();await this.evidence();if(this.accepted?.revoked)this.local?.lock();return this.info();}
}
async function authorizeAvailable(gate,onRefresh=async()=>{}){
 let approved;try{approved=await gate.authorize();}catch{await gate.refresh();return gate.authorize();}
 // A current signed offline lease permits immediate opening. Revalidate online
 // concurrently; a learned revocation still suspends the native workspace.
 gate.refresh().then(onRefresh).catch(()=>{});return approved;
}
module.exports={DesktopAccessGate,argument,authorizeAvailable};
