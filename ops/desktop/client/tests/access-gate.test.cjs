'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {DesktopAccessGate}=require('../access-gate.cjs');const {Runtime}=require('../runtime.cjs');
function fixture(appId='melange'){
 const principalId='33333333-3333-3333-3333-333333333333';
 const grant={serverId:'11111111-1111-1111-1111-111111111111',deviceId:'22222222-2222-2222-2222-222222222222',principalId,accountIdentity:'recipient_'+principalId.replaceAll('-',''),appId,grantId:'44444444-4444-4444-4444-444444444444',requestId:'55555555-5555-5555-5555-555555555555',role:appId==='terrier'?'local_administrator':'local_user',districtIds:appId==='terrier'?['co-05']:[]};
 const state={accepted:null,pending:null,password:null,session:null,configuration:{district:'co-05'},calls:[]};
 const client={accepted:async()=>state.accepted,state:async()=>({pending:state.pending}),requestAccess:async(name,options)=>{state.calls.push({name,options});state.pending={requestId:grant.requestId};return {requestId:grant.requestId,status:'pending',expiresAt:2000000000};},poll:async()=>({requestId:state.pending.requestId,status:'approved',expiresAt:2000000000}),activate:async()=>{state.accepted={grant,revoked:false};state.pending=null;return {grant,requestCleanupPending:false};},refresh:async()=>{if(state.offline)throw new Error('offline');}};
 const local={current:()=>state.session,create:async()=>{state.calls.push('password-created');},unlock:async()=>{state.session={authenticated:true};},lock:()=>{state.session=null;}};
 const gate=new DesktopAccessGate({appId,client,local,passwords:{read:async()=>state.password},configuration:()=>state.configuration});
 return {gate,state,grant};
}
test('approval and activation alone never open Melange/Mentat backend',async()=>{
 const {gate,state}=fixture();assert.equal((await gate.info()).phase,'needs_request');await assert.rejects(gate.authorize(),/approval/);
 await gate.action('request',{displayName:'Fixture laptop'});await assert.rejects(gate.action('request',{displayName:'Replacement'}),/preserved/);
 await gate.action('poll');await gate.action('activate',{activationKey:'fixture'});assert.equal((await gate.info()).phase,'needs_password');assert.equal(state.session,null);await assert.rejects(gate.authorize(),/sign-in/);
 await gate.action('setPassword',{password:'fixture-password'});assert.equal(state.session,null);await assert.rejects(gate.authorize(),/sign-in/);
 state.session={authenticated:true};assert.ok((await gate.authorize()).session);await gate.action('lock');await assert.rejects(gate.authorize(),/sign-in/);
});
test('recipient Terrier launch carries exact account and district but does not claim password authentication',async()=>{
 const {gate,state,grant}=fixture('terrier');state.accepted={grant,revoked:false};assert.equal((await gate.info()).phase,'terrier_signin');
 const allowed=await gate.authorize();assert.equal(allowed.principal.localAccountIdentity,grant.accountIdentity);assert.equal(allowed.principal.localUserId,null);assert.equal(allowed.session,undefined);assert.deepEqual(gate.districts(),['co-05']);
 state.configuration.district='or-05';await assert.rejects(gate.authorize(),/District/);
 state.configuration={district:'co-05',terrierLegacy:{}};await assert.rejects(gate.authorize(),/owner migration/);
 await assert.rejects(gate.action('setPassword',{password:'fixture-password'}),/unavailable/);
});
test('signed owner verifier errors never fall back to self-owner or ordinary password',async()=>{
 const {gate,state,grant}=fixture();state.accepted={grant,revoked:false};state.session={authenticated:true};gate.owner=async()=>{throw new Error('bad owner signature');};await assert.rejects(gate.authorize(),/signature/);
 gate.owner=async()=>({mode:'existing-owner'});state.session=null;assert.ok((await gate.authorize()).owner);
 state.accepted.revoked=true;await assert.rejects(gate.authorize(),/approval/);assert.equal((await gate.info()).phase,'revoked');
});
test('offline verified grant works only with existing local session, learned revocation denies',async()=>{
 const {gate,state,grant}=fixture();state.accepted={grant,revoked:false};state.offline=true;await assert.rejects(gate.refresh(),/offline/);await assert.rejects(gate.authorize(),/sign-in/);
 state.session={authenticated:true};assert.ok(await gate.authorize());state.accepted.revoked=true;assert.deepEqual(gate.districts(),[]);await assert.rejects(gate.authorize(),/approval/);
});
test('strict action arguments and phase prevent crafted IPC identity or path input',async()=>{
 const {gate,state}=fixture();for(const payload of [{displayName:'Fixture',appId:'terrier'},{displayName:'Fixture',url:'https://evil.invalid'},'Fixture',null])await assert.rejects(gate.action('request',payload));assert.equal(state.calls.length,0);
 await assert.rejects(gate.action('activate',{activationKey:'fixture'}),/not ready/);await assert.rejects(gate.action('open',{url:'file:///private'}),/Unsupported/);
});
test('expired renewal recovery is request-relative while older grant evidence stays intact',async()=>{
 const {gate,state,grant}=fixture();state.accepted={grant,revoked:true};state.pending={requestId:'66666666-6666-6666-6666-666666666666'};gate.requestReply={requestId:state.pending.requestId,status:'used',expiresAt:946684800};
 assert.equal((await gate.info()).canRequestAgain,true);await gate.action('request',{displayName:'Fixture retry'});assert.equal(state.calls[0].options.newRequest,true);assert.equal(state.calls[0].options.reapproval,true);assert.equal(state.accepted.grant,grant);
});
test('Runtime refuses missing gate before touching resources or writable roots',async()=>{
 const runtime=new Runtime('melange','unavailable-profile','unavailable-resources');await assert.rejects(runtime.start({dataDirectory:'unavailable'}),/approval/);assert.equal(runtime.child,null);assert.equal(runtime.roots,null);
});
