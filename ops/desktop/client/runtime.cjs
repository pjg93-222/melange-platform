'use strict';
const {spawn}=require('node:child_process');
const net=require('node:net');
const http=require('node:http');
const crypto=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const {appDefinition}=require('./apps.cjs');
const {backendCommand,backendEnvironment}=require('./backend-command.cjs');
const {runtimeRoots,prepareRuntimeRoots,validatedLaunchPath}=require('./runtime-roots.cjs');
function freePort(){return new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen(0,'127.0.0.1',()=>{const port=s.address().port;s.close(()=>resolve(port));});});}
function health(origin,token,appId){return new Promise(resolve=>{const request=http.get(origin+'/__desktop/health',{headers:{'X-Desktop-Token':token},timeout:1000},response=>{let body='';response.on('data',c=>{body+=c;if(body.length>4096)response.destroy();});response.on('error',()=>resolve(null));response.on('close',()=>resolve(null));response.on('end',()=>{try{const value=JSON.parse(body);if(response.statusCode!==200||value.appId!==appId){resolve(null);return;}resolve({launchPath:validatedLaunchPath(appId,value.launchPath)});}catch{resolve(null);}});});request.on('timeout',()=>request.destroy());request.on('error',()=>resolve(null));});}
async function probe(origin,token,appId){return Boolean(await health(origin,token,appId));}
function activity(origin,token,appId){return new Promise(resolve=>{
  if(!origin||!token){resolve(null);return;}
  const request=http.get(origin+'/__desktop/activity',{headers:{'X-Desktop-Token':token},timeout:1000},response=>{
    let body='';response.on('data',chunk=>{body+=chunk;if(body.length>4096)response.destroy();});
    response.on('error',()=>resolve(null));response.on('close',()=>resolve(null));
    response.on('end',()=>{try{const value=JSON.parse(body);resolve(response.statusCode===200&&value.appId===appId&&typeof value.busy==='boolean'?value.busy:null);}catch{resolve(null);}});
  });request.on('timeout',()=>request.destroy());request.on('error',()=>resolve(null));
});}
class Runtime {
  constructor(appId,userData,resources,{protectedPaths=[],rendererFactory=null,authorize=null}={}){this.definition=appDefinition(appId);this.userData=userData;this.resources=resources;this.protectedPaths=protectedPaths;this.rendererFactory=rendererFactory;this.authorize=authorize;this.authorization=null;this.renderer=null;this.child=null;this.origin=null;this.token=null;this.launchPath='/';this.roots=null;}
  async start(configuration){
    if(typeof this.authorize!=='function')throw new Error('Administrator approval and local authentication are required.');
    const authorization=await this.authorize();
    if(!authorization?.accepted?.grant||authorization.accepted.revoked)throw new Error('Administrator approval is required.');
    if(this.child&&this.authorization&&require('./access-lifecycle.cjs').authorizationIdentity(authorization)!==require('./access-lifecycle.cjs').authorizationIdentity(this.authorization))throw new Error('Close the app before changing its approved account.');
    this.authorization=authorization;
    const desired=runtimeRoots(this.definition.id,this.userData,configuration);
    if(this.child){if(JSON.stringify(desired)!==JSON.stringify(this.roots))throw new Error('Close the app before changing its district or data directory.');return this.origin;}
    const appId=this.definition.id, command=backendCommand(this.resources,appId),{binary,runtime}=command;
    const roots=prepareRuntimeRoots(appId,this.userData,configuration,[this.resources,path.dirname(binary),...this.protectedPaths]);this.roots=roots;
    const port=await freePort();this.token=crypto.randomBytes(32).toString('base64url');this.origin='http://127.0.0.1:'+port;
    const state=roots.state,logs=path.join(this.userData,'logs');fs.mkdirSync(logs,{recursive:true});
    const environment={...backendEnvironment(),DESKTOP_LAUNCH_TOKEN:this.token};
    if(appId==='terrier'){
      if(!authorization.principal||authorization.principal.districtId!==roots.district)throw new Error('Approved Terrier account is required.');
      environment.DESKTOP_ACCESS_PRINCIPAL=JSON.stringify(authorization.principal);
    }
    if(appId==='mentat'&&this.rendererFactory){this.renderer=await this.rendererFactory();environment.MENTAT_DESKTOP_RENDER_URL=this.renderer.url;environment.MENTAT_DESKTOP_RENDER_TOKEN=this.renderer.token;}
    const renderer=this.renderer;
    const log=fs.openSync(path.join(logs,'backend.log'),'a',0o600);
    const args=[...command.args,'--app',appId,'--port',String(port),'--source-root',path.join(runtime,'sources'),'--data-root',roots.data,'--state-root',state];
    if(appId==='terrier') args.push('--district',roots.district);
    if(roots.legacyOwner) args.push('--legacy-owner',roots.legacyOwner);
    const child=spawn(binary,args,{cwd:runtime,env:environment,windowsHide:true,detached:process.platform!=='win32',stdio:['ignore',log,log]});fs.closeSync(log);this.child=child;
    let failed=null;child.once('error',error=>{failed=error;});child.once('exit',(code)=>{failed=new Error('Backend stopped (exit '+code+'). See backend.log.');if(this.child===child)this.child=null;if(renderer){Promise.resolve(renderer.close()).catch(()=>{});if(this.renderer===renderer)this.renderer=null;}});
    const deadline=Date.now()+120000;
    while(Date.now()<deadline){if(failed){await this.stop();throw failed;}const ready=await health(this.origin,this.token,appId);if(ready){this.launchPath=ready.launchPath;return this.origin;}await new Promise(r=>setTimeout(r,250));}
    await this.stop();throw new Error('Application startup timed out. See backend.log.');
  }
  async stop(){const child=this.child,renderer=this.renderer;this.child=null;this.renderer=null;this.origin=null;this.token=null;this.launchPath='/';this.roots=null;if(renderer)await Promise.resolve().then(()=>renderer.close()).catch(()=>{});if(!child)return;
    if(process.platform==='win32') await new Promise(resolve=>{const killer=spawn('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});killer.once('error',()=>{child.kill();resolve();});killer.once('exit',resolve);});
    else {try{process.kill(-child.pid,'SIGTERM');}catch{}}
    if(child.exitCode===null)await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
    if(child.exitCode===null){if(process.platform==='win32')child.kill('SIGKILL');else try{process.kill(-child.pid,'SIGKILL');}catch{}}
  }
  activity(){return activity(this.origin,this.token,this.definition.id);}
}
function suspendedAuthRequest(details,runtime){
 if(!runtime.authenticationContentsId||details.webContentsId!==runtime.authenticationContentsId)return false;
 const url=new URL(details.url);if(url.origin!==runtime.origin)return false;
 if(details.method==='GET'&&['/desktop-signin','/api/auth/session'].includes(url.pathname))return true;
 if(details.method==='POST'&&['/api/auth/login','/api/auth/accept','/api/auth/logout'].includes(url.pathname))return true;
 return details.method==='GET'&&/^\/(assets|brand)\/[^/]+\.(js|css|woff2?|png|svg|ico|webp|mp4)$/.test(url.pathname);
}
function attachLaunchHeader(session,runtime){
  const owned=details=>Number.isInteger(runtime.contentsId)&&details.webContentsId===runtime.contentsId;
  session.webRequest.onBeforeRequest?.((details,callback)=>callback({cancel:Boolean(runtime.origin&&new URL(details.url).origin===runtime.origin&&!((owned(details)&&runtime.requestsAllowed!==false)||suspendedAuthRequest(details,runtime)))}));
  session.webRequest.onBeforeSendHeaders((details,callback)=>{
    const headers={...details.requestHeaders};for(const key of Object.keys(headers))if(key.toLowerCase()==='x-desktop-token')delete headers[key];
    if(((owned(details)&&runtime.requestsAllowed!==false)||suspendedAuthRequest(details,runtime))&&runtime.origin&&new URL(details.url).origin===runtime.origin) headers['X-Desktop-Token']=runtime.token;
    callback({requestHeaders:headers});
  });
}
module.exports={Runtime,freePort,probe,health,activity,attachLaunchHeader,suspendedAuthRequest};
