'use strict';
const crypto=require('node:crypto'),path=require('node:path'),fs=require('node:fs');
const {desktopProbe}=require('./desktop-probe.cjs');
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
async function ownerDataBindings(resources,appId,profile,configuration,grant){
 if(appId!=='terrier')return [{districtId:null,dataRoot:runtimeRoots(appId,profile,configuration).data,localUserId:null,localUsername:null}];
 const command=backendCommand(resources,appId);
 return Promise.all(grant.districtIds.map(async district=>{
  const selected=districtConfiguration(configuration,district,grant.districtIds),roots=runtimeRoots(appId,profile,selected);
  if(!roots.legacyOwner)throw new Error('Explicit existing Terrier account mapping required');
  const value=await desktopProbe(command,'--desktop-owner-check',{sourceRoot:path.join(command.runtime,'sources'),dataRoot:roots.data,district,legacy:true},{limit:16384});
  if(value.district!==district||value.validated!==true||value.localUsername!=='admin'||typeof value.localUserId!=='string'||!(/^[1-9][0-9]{0,18}$/).test(value.localUserId))throw new Error('Existing account mapping could not be verified');
  return {districtId:district,dataRoot:roots.data,localUserId:value.localUserId,localUsername:value.localUsername};
 }));
}
function ownerBindingFingerprint(appId,profile,configuration,grant){
 if(appId!=='terrier')return JSON.stringify(configuration);
 return JSON.stringify(grant.districtIds.map(district=>{const selected=districtConfiguration(configuration,district,grant.districtIds),roots=runtimeRoots(appId,profile,selected);return [roots,...['accounts.sqlite3','accounts.sqlite3-wal','accounts.sqlite3-journal','.secret','street.sqlite3','scores.sqlite3','walk.sqlite3'].map(name=>{try{const stat=fs.lstatSync(path.join(roots.data,name),{bigint:true});return [name,String(stat.dev),String(stat.ino),String(stat.size),String(stat.mtimeNs),String(stat.ctimeNs),stat.isSymbolicLink()];}catch(error){if(error.code==='ENOENT')return [name,null];throw error;}})];}));
}
function bindingCache(validate,fingerprint,{now=Date.now,ttl=2000}={}){
 let cached;
 return async(...args)=>{const key=fingerprint(...args),time=now();if(cached&&cached.key===key&&time<cached.expires)return cached.promise;
  const entry={key,expires:time+ttl};entry.promise=Promise.resolve().then(()=>validate(...args)).then(value=>{if(fingerprint(...args)!==key)throw new Error('Account data changed during verification.');return value;}).catch(error=>{if(cached===entry)cached=null;throw error;});cached=entry;return entry.promise;
 };
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
 const bindings=bindingCache((selected,grant)=>ownerDataBindings(resources,appId,profile,selected,grant),(selected,grant)=>ownerBindingFingerprint(appId,profile,selected,grant));
 // The host identity is immutable for this process. Signed approval and protected
 // owner records are still freshly read and verified for every authorization.
 const host=await protectedAccess({resources,appId,profile,action:'describe'});
 const owner=async(accepted,selected)=>{
  const record=await store('owner').read();if(!record)return null;
  return verifyOwnerRecord(record,{trust:client.trust,accepted,profile,windowsSid:host.windowsSid,
    dataBindings:await bindings(selected,accepted.grant)});
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
module.exports={bootstrapAccess,importTrust,exactIdentity,ownerDataBindings,ownerBindingFingerprint,bindingCache};
