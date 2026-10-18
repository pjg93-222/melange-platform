'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {AccessLifecycle}=require('../access-lifecycle.cjs');const {attachLaunchHeader}=require('../runtime.cjs');
function fixture(appId='melange'){
 const approved={accepted:{grant:{appId,serverId:'server',deviceId:'device',principalId:'principal',accountIdentity:'account',role:'local_user',districtIds:[]},revoked:false},session:{},principal:appId==='terrier'?{localAccountIdentity:'account',districtId:'co-05'}:null};
 const runtime={definition:{id:appId},child:{pid:123},authorization:approved,requestsAllowed:true,origin:'http://127.0.0.1:12345',token:'secret',contentsId:7};
 let before,headers,hidden=false,shown=false,authenticated=false,logout=0;
 attachLaunchHeader({webRequest:{onBeforeRequest:fn=>before=fn,onBeforeSendHeaders:fn=>headers=fn}},runtime);
 const lifecycle=new AccessLifecycle({runtime,gate:{authorize:async()=>approved,local:{lock(){}}},hide:()=>{hidden=true;},showBoundary:async()=>{shown=true;},terrierAuthenticated:async()=>authenticated,terrierLogout:async()=>{authenticated=false;logout++;return true;}});
 const request=(extra={})=>{const details={url:runtime.origin+'/api/save',method:'POST',webContentsId:7,requestHeaders:{'X-DESKTOP-TOKEN':'stale'},...extra};let blocked,sent;before(details,result=>blocked=result.cancel);headers(details,result=>sent=result.requestHeaders);return {blocked,sent};};
 return {approved,runtime,lifecycle,request,setAuthenticated:value=>authenticated=value,get hidden(){return hidden;},get shown(){return shown;},get logout(){return logout;}};
}
test('lock blocks new owned requests and strips any token casing without stopping backend',async()=>{
 const f=fixture();assert.equal(f.request().sent['X-Desktop-Token'],'secret');const child=f.runtime.child;
 await f.lifecycle.suspend('locked');assert.equal(f.request().blocked,true);assert.deepEqual(f.request().sent,{});assert.equal(f.runtime.child,child);assert.ok(f.hidden&&f.shown);
 await f.lifecycle.resume(f.approved);assert.equal(f.request().blocked,false);
});
test('unknown/service-worker contents never get launcher credential, even when active',()=>{
 const f=fixture();for(const webContentsId of [-1,0,undefined,99]){const result=f.request({webContentsId});assert.equal(result.blocked,true);assert.deepEqual(result.sent,{});}
});
test('suspended sign-in window only gets fixed authentication/static resources',async()=>{
 const f=fixture('terrier');await f.lifecycle.suspend('locked');f.runtime.authenticationContentsId=8;
 for(const [pathname,method]of [['/desktop-signin','GET'],['/api/auth/session','GET'],['/api/auth/login','POST'],['/assets/index-123.js','GET']])assert.equal(f.request({webContentsId:8,url:f.runtime.origin+pathname,method}).blocked,false);
 for(const pathname of ['/api/v3/meta','/api/accounts','/__desktop/health','/assets/private.json','/api/auth/session/extra']){const result=f.request({webContentsId:8,url:f.runtime.origin+pathname,method:'GET'});assert.equal(result.blocked,true);assert.deepEqual(result.sent,{});}
});
test('Terrier lock invalidates old sign-in and requires fresh authentication to resume',async()=>{
 const f=fixture('terrier');f.setAuthenticated(true);assert.equal(await f.lifecycle.authenticated(),true);await f.lifecycle.suspend('locked');assert.equal(f.logout,1);assert.equal(await f.lifecycle.authenticated(),false);
 await assert.rejects(f.lifecycle.resume(f.approved),/Fresh/);await f.lifecycle.resume(f.approved,{freshPassword:true});assert.equal(f.runtime.requestsAllowed,true);
});
test('explicit Terrier sign-out also refuses a stale authenticated cookie and requires a new password',async()=>{
 const f=fixture('terrier');f.setAuthenticated(true);await f.lifecycle.suspend('signed_out');assert.equal(f.logout,1);assert.equal(f.lifecycle.requiresFreshPassword,true);
 f.setAuthenticated(true);const ticket=await f.lifecycle.prepareAuthentication();assert.equal(f.logout,2);assert.equal(await f.lifecycle.terrierAuthenticated(),false);assert.ok(ticket);await assert.rejects(f.lifecycle.resume(f.approved),/Fresh/);assert.equal(f.runtime.requestsAllowed,false);
});
test('temporary approved-owner password bypass resumes after a screen lock without password authentication',async()=>{
 const f=fixture('terrier');f.setAuthenticated(true);f.lifecycle.terrierPasswordLockDisabled=async()=>true;
 await f.lifecycle.suspend('locked');assert.equal(f.logout,0);assert.equal(f.lifecycle.requiresFreshPassword,false);
 assert.equal(f.runtime.requestsAllowed,false);await f.lifecycle.resume(f.approved);assert.equal(await f.lifecycle.authenticated(),true);
});
test('unavailable password preference preserves the normal Terrier lock',async()=>{
 const f=fixture('terrier');f.setAuthenticated(true);f.lifecycle.terrierPasswordLockDisabled=async()=>{throw new Error('unavailable');};
 await f.lifecycle.suspend('locked');assert.equal(f.logout,1);await assert.rejects(f.lifecycle.resume(f.approved),/Fresh/);
});
test('wrong principal or district cannot resume preserved backend; health is not active presence',async()=>{
 const f=fixture('terrier');assert.equal(await f.lifecycle.heartbeat(),'signed_out');await f.lifecycle.suspend('locked');
 const changed=structuredClone(f.approved);changed.principal.localAccountIdentity='other';await assert.rejects(f.lifecycle.resume(changed,{freshPassword:true}),/approved account/);assert.equal(f.runtime.requestsAllowed,false);
});
test('lock during asynchronous startup/load invalidates every pending reveal and resume ticket',async()=>{
 const f=fixture(),ticket=f.lifecycle.generation;let resolve;f.lifecycle.gate.authorize=()=>new Promise(r=>{resolve=r;});
 const revealing=f.lifecycle.canReveal(ticket,f.approved);await f.lifecycle.suspend('locked');resolve(f.approved);assert.equal(await revealing,false);
 await assert.rejects(f.lifecycle.resume(f.approved,{expectedGeneration:ticket}),/Access changed/);assert.equal(f.runtime.requestsAllowed,false);assert.equal(await f.lifecycle.canReveal(ticket,f.approved),false);
});
test('a revoked or narrowed approval cannot reveal an otherwise preserved active window',async()=>{
 const f=fixture();f.lifecycle.gate.authorize=async()=>({...f.approved,accepted:{...f.approved.accepted,revoked:true}});assert.equal(await f.lifecycle.canReveal(f.lifecycle.generation,f.approved),false);assert.equal(f.runtime.requestsAllowed,false);assert.equal(f.hidden,true);
 const narrowed=fixture();narrowed.lifecycle.gate.authorize=async()=>({...narrowed.approved,accepted:{...narrowed.approved.accepted,grant:{...narrowed.approved.accepted.grant,districtIds:['co-05']}}});assert.equal(await narrowed.lifecycle.canReveal(narrowed.lifecycle.generation,narrowed.approved),false);assert.equal(narrowed.runtime.requestsAllowed,false);
});
test('a held Terrier password response cannot reuse a sign-in window ticket after another lock',async()=>{
 const f=fixture('terrier');await f.lifecycle.suspend('locked');const ticket=f.lifecycle.authenticationTicket();let complete;
 const passwordResponse=new Promise(resolve=>{complete=resolve;});
 const pending=(async()=>{await passwordResponse;if(!f.lifecycle.authenticationCurrent(ticket))return false;await f.lifecycle.resume(f.approved,{freshPassword:true,expectedGeneration:ticket.generation});return true;})();
 await f.lifecycle.suspend('locked');f.setAuthenticated(true);complete();assert.equal(await pending,false);assert.equal(f.runtime.requestsAllowed,false);assert.equal(f.lifecycle.authenticationCurrent(ticket),false);
 const logouts=f.logout,replacement=await f.lifecycle.prepareAuthentication();assert.equal(f.logout,logouts+1);assert.equal(await f.lifecycle.terrierAuthenticated(),false);assert.equal(f.lifecycle.requiresFreshPassword,true);await assert.rejects(f.lifecycle.resume(f.approved),/Fresh/);
 assert.notEqual(replacement.generation,ticket.generation);f.setAuthenticated(true);await f.lifecycle.resume(f.approved,{freshPassword:true,expectedGeneration:replacement.generation});assert.equal(f.runtime.requestsAllowed,true);
});
