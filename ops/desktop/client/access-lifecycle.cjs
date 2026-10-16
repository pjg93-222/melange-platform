'use strict';
const http=require('node:http');
function authorizationIdentity(value){
 const grant=value?.accepted?.grant;if(!grant||value.accepted.revoked)throw new Error('Current approval is required');
 return JSON.stringify({serverId:grant.serverId,deviceId:grant.deviceId,principalId:grant.principalId,
  accountIdentity:grant.accountIdentity,appId:grant.appId,role:grant.role,districtIds:grant.districtIds,
  owner:Boolean(value.owner),principal:value.principal||null});
}
async function terrierSession(runtime,partition,{logout=false}={}){
 if(runtime.definition.id!=='terrier'||!runtime.origin||!runtime.token||!runtime.authorization?.principal)return false;
 const origin=new URL(runtime.origin);
 if(origin.protocol!=='http:'||origin.hostname!=='127.0.0.1'||!origin.port||origin.pathname!=='/')return false;
 const cookies=await partition.cookies.get({url:runtime.origin});
 const cookie=cookies.map(item=>item.name+'='+item.value).join('; ');
 const request=(relative,method='GET',csrf)=>new Promise(resolve=>{
  const body=method==='POST'?'{}':undefined;
  const req=http.request(new URL(relative,origin),{method,headers:{'X-Desktop-Token':runtime.token,Cookie:cookie,Accept:'application/json',...(body?{'Content-Type':'application/json','Content-Length':2,'X-CSRFToken':csrf,Origin:runtime.origin}:{})},timeout:2000},response=>{
   let bytes=0;const chunks=[];response.on('data',part=>{bytes+=part.length;if(bytes>8192)response.destroy();else chunks.push(part);});response.on('error',()=>resolve(null));response.on('close',()=>resolve(null));response.on('end',()=>{try{resolve(response.statusCode===200?JSON.parse(Buffer.concat(chunks).toString('utf8')):null);}catch{resolve(null);}});
  });req.on('error',()=>resolve(null));req.on('timeout',()=>req.destroy());req.end(body);
 });
 const value=await request('/api/auth/session');
 const authenticated=Boolean(value?.authenticated===true&&value.administrator===true&&value.username===runtime.authorization.principal.localAccountIdentity);
 if(logout){if(!value||typeof value.csrf!=='string')return false;const result=await request('/api/auth/logout','POST',value.csrf);return result?.ok===true;}
 return authenticated;
}
class AccessLifecycle{
 constructor({runtime,gate,hide,showBoundary,presence=async()=>{},terrierAuthenticated=async()=>false,terrierLogout=async()=>false}){Object.assign(this,{runtime,gate,hide,showBoundary,presence,terrierAuthenticated,terrierLogout});this.suspended=false;this.generation=0;this.sessionId=require('node:crypto').randomUUID();this.osLocked=false;this.requiresFreshPassword=false;}
 async authenticated(){
  if(this.suspended||!this.runtime.child||this.runtime.requestsAllowed===false)return false;
  try{const approved=await this.gate.authorize();if(authorizationIdentity(approved)!==authorizationIdentity(this.runtime.authorization))return false;
   return this.runtime.definition.id==='terrier'?await this.terrierAuthenticated():Boolean(approved.owner||approved.session);
  }catch{return false;}
 }
 async suspend(reason){
  // Stop new network activity first. Existing requests/compute and the DOM remain intact.
  this.generation++;this.runtime.requestsAllowed=false;this.suspended=true;this.gate.local?.lock();this.hide();
  if(['locked','signed_out'].includes(reason)&&this.runtime.definition.id==='terrier'){this.requiresFreshPassword=true;await this.terrierLogout();}
  try{await this.presence(reason==='signed_out'?'signed_out':'locked',this.sessionId);}catch{}
  await this.showBoundary();
 }
 async resume(approved,{freshPassword=false,expectedGeneration=this.generation}={}){
  if(expectedGeneration!==this.generation)throw new Error('Access changed while sign-in was pending');
  if(this.requiresFreshPassword&&!freshPassword)throw new Error('Fresh Terrier password authentication required');
  if(this.runtime.child&&this.runtime.authorization&&authorizationIdentity(approved)!==authorizationIdentity(this.runtime.authorization))throw new Error('Close the workspace before changing its approved account');
  this.runtime.requestsAllowed=true;this.suspended=false;this.requiresFreshPassword=false;
 }
 authenticationTicket(){return Object.freeze({generation:this.generation});}
 authenticationCurrent(ticket){return Boolean(ticket)&&ticket.generation===this.generation;}
 async prepareAuthentication(){
  const ticket=this.authenticationTicket();
  if(this.requiresFreshPassword&&await this.terrierAuthenticated()){
   await this.terrierLogout();if(await this.terrierAuthenticated())throw new Error('The previous sign-in could not be cleared');
  }
  return this.authenticationCurrent(ticket)?ticket:null;
 }
 async canReveal(generation,approved){
  const current=()=>generation===this.generation&&!this.suspended&&this.runtime.requestsAllowed!==false&&Boolean(this.runtime.child);
  if(!current())return false;
  try{const latest=await this.gate.authorize();if(!current())return false;if(authorizationIdentity(latest)===authorizationIdentity(approved)&&authorizationIdentity(latest)===authorizationIdentity(this.runtime.authorization))return true;}catch{}
  if(current())await this.suspend('locked');return false;
 }
 async heartbeat(){const active=await this.authenticated();const state=this.osLocked?'locked':active?'active':'signed_out';try{await this.presence(state,this.sessionId);}catch{}return state;}
}
module.exports={authorizationIdentity,terrierSession,AccessLifecycle};
