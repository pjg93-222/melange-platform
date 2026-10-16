'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {enrollFresh,enrollApproved}=require('../enrollment.cjs');
function fixture(){
  let metadata={deviceId:'prior-device',appId:'terrier'},deleted=[];
  const options={bundle:{appId:'terrier',code:'one-use',url:'https://192.168.1.1'},appId:'terrier',version:'1.0.0',platform:'win32',arch:'x64',randomUUID:()=> 'new-device',readPrevious:()=>metadata,request:async(p,url,request)=>{assert.equal(request.body.deviceId,'new-device');assert.equal(request.body.enrollmentCode,'one-use');return {appId:'terrier',deviceId:'new-device',deviceToken:'new-credential-fixture-only'};},save:value=>{assert.equal(metadata.deviceId,'prior-device');metadata=value;},deleteCredential:(app,id)=>{assert.equal(app,'terrier');deleted.push(id);}};
  return {options,get metadata(){return metadata;},deleted};
}
test('explicit re-enrollment saves new identity before removing only previous app credential',async()=>{
  const f=fixture(),result=await enrollFresh(f.options);assert.equal(result.deviceId,'new-device');assert.equal(f.metadata.deviceId,'new-device');assert.equal(f.metadata.code,undefined);assert.deepEqual(f.deleted,['prior-device']);
});
test('failed local enrollment save preserves previous enrollment and cleans new orphan credential',async()=>{
  const f=fixture();f.options.save=()=>{throw new Error('disk unavailable');};await assert.rejects(()=>enrollFresh(f.options),/disk unavailable/);assert.equal(f.metadata.deviceId,'prior-device');assert.deepEqual(f.deleted,['new-device']);
});
test('prior credential cleanup failure does not discard a successfully saved enrollment',async()=>{
  const f=fixture();f.options.deleteCredential=()=>{throw new Error('store unavailable');};const result=await enrollFresh(f.options);assert.equal(result.priorCredentialRemoved,false);assert.equal(f.metadata.deviceId,'new-device');
});


test('automatic receiver retries lost exchange or failed protected save with the same approved identity',async()=>{
 const enrollment={appId:'mentat',deviceId:'approved-device',deviceToken:'separate-download-credential'},saved=[];let calls=0,metadata=null,failSave=true;
 const options={version:'1.0.1',client:{accepted:async()=>({revoked:false}),enrollUpdates:async version=>{assert.equal(version,'1.0.1');if(++calls===1)throw new Error('response lost');return enrollment;}},load:()=>metadata,save:value=>{if(failSave)throw new Error('protected storage unavailable');metadata=value;saved.push(value);}};
 await assert.rejects(enrollApproved(options),/response lost/);assert.equal(metadata,null);
 await assert.rejects(enrollApproved(options),/protected storage/);assert.equal(metadata,null);
 failSave=false;assert.deepEqual(await enrollApproved(options),{enrolled:true});
 assert.deepEqual(saved,[enrollment]);assert.equal(calls,3);
 assert.deepEqual(await enrollApproved(options),{enrolled:false,reason:'existing'});assert.equal(calls,3);
});
test('automatic receiver preserves manual enrollment and never enrolls unapproved/revoked installations',async()=>{
 let calls=0;const client={accepted:async()=>null,enrollUpdates:async()=>{calls++;throw new Error('must not request');}};
 for(const accepted of [null,{revoked:true}]){client.accepted=async()=>accepted;assert.deepEqual(await enrollApproved({client,load:()=>null,save:()=>assert.fail(),version:'1.0.1'}),{enrolled:false,reason:'approval-required'});}
 assert.deepEqual(await enrollApproved({client,load:()=>({deviceId:'manual'}),save:()=>assert.fail(),version:'1.0.1'}),{enrolled:false,reason:'existing'});
 await assert.rejects(enrollApproved({client,load:()=>{throw new Error('credential unreadable');},save:()=>assert.fail(),version:'1.0.1'}),/credential unreadable/);
 assert.equal(calls,0);
});
test('manual pairing completed during automatic exchange is not overwritten',async()=>{
 let metadata=null;
 const client={accepted:async()=>({revoked:false}),enrollUpdates:async()=>{metadata={deviceId:'manual'};return {deviceId:'automatic'};}};
 assert.deepEqual(await enrollApproved({client,version:'1.0.1',load:()=>metadata,save:()=>assert.fail('manual identity overwritten')}),{enrolled:false,reason:'existing'});
 assert.equal(metadata.deviceId,'manual');
});
