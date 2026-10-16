'use strict';
const crypto=require('node:crypto');

async function enrollFresh({bundle,appId,version,platform,arch,request,readPrevious,save,deleteCredential,randomUUID=crypto.randomUUID}){
  const previous=readPrevious(),deviceId=randomUUID();
  const result=await request(bundle,'/v1/enroll',{method:'POST',body:{enrollmentCode:bundle.code,appId,deviceId,platform,arch,currentVersion:version}});
  if(result.appId!==appId||result.deviceId!==deviceId||typeof result.deviceToken!=='string'||result.deviceToken.length<20)throw new Error('Invalid enrollment reply');
  const {code,...trust}=bundle;
  try{save({...trust,deviceToken:result.deviceToken,deviceId,appId});}
  catch(error){try{deleteCredential(appId,deviceId);}catch{}throw error;}
  let priorCredentialRemoved=true;
  if(previous?.deviceId&&previous.deviceId!==deviceId){try{deleteCredential(appId,previous.deviceId);}catch{priorCredentialRemoved=false;}}
  return {deviceId,priorCredentialRemoved};
}
async function enrollApproved({client,version,load,save}){
  // Preserve manual pairing and existing download identity, including signed key
  // transition history. A failed protected read must never trigger replacement.
  if(await load())return {enrolled:false,reason:'existing'};
  const accepted=await client.accepted();
  if(!accepted||accepted.revoked)return {enrolled:false,reason:'approval-required'};
  const enrollment=await client.enrollUpdates(version);
  // The server exchange is idempotent for this grant. If storage fails or the
  // reply is lost, the next scheduled attempt recovers the same credential.
  if(await load())return {enrolled:false,reason:'existing'};
  await save(enrollment);
  return {enrolled:true};
}
module.exports={enrollFresh,enrollApproved};
