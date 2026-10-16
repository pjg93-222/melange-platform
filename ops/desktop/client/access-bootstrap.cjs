'use strict';
const crypto=require('node:crypto'),path=require('node:path');const {spawnSync}=require('node:child_process');
const {AccessClient,descriptor,refreshDescriptor}=require('./access-client.cjs');
const {accessStateStore,protectedAccess}=require('./access-state.cjs');
const {accessSecrets,passwordStore}=require('./access-credentials.cjs');
const {LocalAccess}=require('./local-access.cjs');const {DesktopAccessGate}=require('./access-gate.cjs');
const {verifyOwnerRecord}=require('./owner-access.cjs');
const {backendCommand,backendEnvironment}=require('./backend-command.cjs');
const {runtimeRoots}=require('./runtime-roots.cjs');
const {districtConfiguration}=require('./district-switch.cjs');
function exactIdentity(value,appId){
 if(!value||Object.keys(value).sort().join('|')!==['schemaVersion','appId','deviceId','platform','arch'].sort().join('|')||value.schemaVersion!==1||value.appId!==appId||value.platform!=='win32'||value.arch!=='x64'||!/^\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.deviceId)||value.deviceId==='00000000-0000-0000-0000-000000000000')throw new Error('Invalid protected installation identity');
 const {schemaVersion,...identity}=value;return identity;
}
function ownerDataBindings(resources,appId,profile,configuration,grant){
 if(appId!=='terrier')return [{districtId:null,dataRoot:runtimeRoots(appId,profile,configuration).data,localUserId:null,localUsername:null}];
 const command=backendCommand(resources,appId);
 return grant.districtIds.map(district=>{
  const selected=districtConfiguration(configuration,district,grant.districtIds),roots=runtimeRoots(appId,profile,selected);
  if(!roots.legacyOwner)throw new Error('Explicit existing Terrier account mapping required');
  const result=spawnSync(command.binary,[...command.args,'--desktop-owner-check'],{input:JSON.stringify({sourceRoot:path.join(command.runtime,'sources'),dataRoot:roots.data,district,legacy:true}),encoding:'utf8',env:backendEnvironment(),windowsHide:true,timeout:30000,maxBuffer:16384});
  if(result.error||result.status!==0)throw new Error('Existing account mapping could not be verified');
  let value;try{value=JSON.parse(result.stdout);}catch{throw new Error('Existing account mapping could not be verified');}
  if(value.district!==district||value.validated!==true||value.localUsername!=='admin'||typeof value.localUserId!=='string'||!(/^[1-9][0-9]{0,18}$/).test(value.localUserId))throw new Error('Existing account mapping could not be verified');
  return {districtId:district,dataRoot:roots.data,localUserId:value.localUserId,localUsername:value.localUsername};
 });
}
async function bootstrapAccess({resources,appId,profile,configuration,packaged,bundledTrust}){
 if(process.platform!=='win32'||process.arch!=='x64')throw new Error('This access build supports Windows x64 only');
 const store=kind=>accessStateStore({resources,appId,profile,kind}),trustStore=store('trust');
 let trust=await trustStore.read();
 if(packaged&&!bundledTrust)throw new Error('This recipient package has no administrator trust descriptor');
 if(bundledTrust){const bundled=descriptor(bundledTrust);if(trust){refreshDescriptor(bundled,trust);}else{trust=bundled;await trustStore.write(trust);}}
 if(!trust)throw new Error('Import the administrator trust descriptor in Settings');
 trust=descriptor(trust);
 const identityStore=store('identity');let saved=await identityStore.read();
 if(!saved){saved={schemaVersion:1,appId,deviceId:crypto.randomUUID(),platform:'win32',arch:'x64'};await identityStore.write(saved);}
 const identity=exactIdentity(saved,appId),secrets=accessSecrets(resources,identity),passwords=passwordStore(resources);
 const client=new AccessClient({trust,identity,store:store('state'),secrets}),local=new LocalAccess({store:passwords});
 const owner=async(accepted,selected)=>{
  const record=await store('owner').read();if(!record)return null;
  const host=await protectedAccess({resources,appId,profile,action:'describe'});
  return verifyOwnerRecord(record,{trust:client.trust,accepted,profile,windowsSid:host.windowsSid,
    dataBindings:ownerDataBindings(resources,appId,profile,selected,accepted.grant)});
 };
 return new DesktopAccessGate({appId,client,local,passwords,configuration,owner});
}
async function importTrust({resources,appId,profile,packaged,bundledTrust,value}){
 const next=descriptor(value),store=accessStateStore({resources,appId,profile,kind:'trust'}),previous=await store.read();
 if(packaged&&!bundledTrust)throw new Error('A recipient package must include its administrator trust descriptor');
 const pinned=previous||(bundledTrust?descriptor(bundledTrust):null);
 const checked=pinned?refreshDescriptor(pinned,next):next;
 await store.write(checked);return checked;
}
module.exports={bootstrapAccess,importTrust,exactIdentity,ownerDataBindings};
