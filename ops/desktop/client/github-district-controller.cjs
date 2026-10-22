'use strict';
const {authorized,REPOSITORY}=require('./github-district-protocol.cjs');
const ownerGitHubSource=(appId,source)=>appId==='terrier'&&source?.provider==='github'&&typeof source.repository==='string'&&source.repository.toLowerCase()===REPOSITORY.toLowerCase();
// One native queue owns catalogue/cache changes. Discovery can stage an update,
// but has no capability to replace a runtime, navigate, or save settings.
class GitHubDistrictController{
 constructor({createProvider,authorize,keepAwake=()=>()=>{},onStatus=()=>{},canDiscover=()=>true}){
  Object.assign(this,{createProvider,authorize,keepAwake,onStatus,canDiscover});this.provider=null;this.tail=Promise.resolve();this.busy=false;
 }
 info(configuration,authorization){
  if(!authorized(configuration,authorization)||!this.provider)return null;
  const state=this.provider.info(configuration);return state?{...state,canRefresh:!this.busy}:null;
 }
 async run(configuration,stageOnly){
  if(stageOnly&&!this.canDiscover(configuration))return null;
  // Approval is obtained after earlier queued work finishes, never captured
  // before a long download or passed to the public HTTPS transport.
  const authorization=await this.authorize(configuration);
  if(!authorized(configuration,authorization))return stageOnly?null:configuration;
  if(stageOnly&&!this.canDiscover(configuration))return null;
  if(!this.provider)this.provider=this.createProvider(state=>this.onStatus({...state,canRefresh:false}));
  this.busy=true;let release;
  try{release=this.keepAwake();return stageOnly?await this.provider.discover(configuration,authorization,{stageOnly:true}):await this.provider.ensure(configuration,authorization);}
  finally{try{release?.();}finally{this.busy=false;const state=this.info(configuration,authorization);if(state)this.onStatus(state);}}
 }
 enqueue(configuration,stageOnly){
  const result=this.tail.then(()=>this.run(configuration,stageOnly));this.tail=result.catch(()=>{});return result;
 }
 ensure(configuration){return this.enqueue(configuration,false);}
 discover(configuration,{skipIfBusy=false}={}){if(skipIfBusy&&this.busy)return Promise.resolve(null);return this.enqueue(configuration,true);}
}
function registerDistrictDataIPC(ipc,{runtimeOnly,info,refresh,canRefresh}){
 ipc.handle('desktop:district-data',event=>{runtimeOnly(event);return info();});
 ipc.handle('desktop:district-data-refresh',async event=>{runtimeOnly(event);if(!canRefresh())throw new Error('District data check is unavailable.');try{return await refresh();}catch{throw new Error('District data check could not be completed.');}});
}
module.exports={GitHubDistrictController,registerDistrictDataIPC,ownerGitHubSource};
