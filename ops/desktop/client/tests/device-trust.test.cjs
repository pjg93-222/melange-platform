'use strict';
// One-time device verification (Melange/Mentat) and the passwordless Terrier owner, through the real gate and LocalAccess.
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {DesktopAccessGate}=require('../access-gate.cjs');const {LocalAccess}=require('../local-access.cjs');
const PASSWORD='fixture password one';
// An in-memory stand-in for one Credential Manager namespace, keyed by device like the real stores.
function vault(){const values=new Map();return {values,read:async id=>values.has(id.deviceId)?structuredClone(values.get(id.deviceId)):null,write:async(id,record)=>{values.set(id.deviceId,structuredClone(record));},delete:async id=>{values.delete(id.deviceId);}};}
// stores outlive launch(): each launch() is a fresh process over the same protected storage.
function world(appId='mentat',{owner=null}={}){
 const principalId=crypto.randomUUID();
 const grant={serverId:crypto.randomUUID(),deviceId:crypto.randomUUID(),principalId,accountIdentity:'recipient_'+principalId.replaceAll('-',''),appId,grantId:crypto.randomUUID(),requestId:crypto.randomUUID(),role:appId==='terrier'?'local_administrator':'local_user',districtIds:appId==='terrier'?['co-05']:[]};
 const stores={passwords:vault(),devices:vault()},state={accepted:{grant,revoked:false},passwordReads:0,owner};
 const passwords={read:async id=>{state.passwordReads++;return stores.passwords.read(id);},write:stores.passwords.write};
 const launch=()=>new DesktopAccessGate({appId,client:{accepted:async()=>state.accepted,state:async()=>({pending:null})},
  local:new LocalAccess({store:passwords,devices:stores.devices}),passwords,configuration:()=>({district:'co-05',...(owner?{terrierLegacy:{}}:{})}),owner:async()=>state.owner});
 return {grant,state,stores,launch};
}
async function verifyOnce(gate){
 assert.equal((await gate.info()).phase,'needs_password');
 await gate.action('setPassword',{password:PASSWORD});
 assert.equal((await gate.info()).phase,'locked');await assert.rejects(gate.authorize(),/sign-in/);
 await gate.action('unlock',{password:PASSWORD});
}

test('Melange/Mentat ask for the password on the first run only: one sign-in remembers this computer',async()=>{
 const w=world(),first=w.launch();
 await verifyOnce(first);
 assert.equal((await first.info()).phase,'ready');assert.equal((await first.authorize()).session.method,'local-password');
 assert.equal(w.stores.devices.values.size,1);
 const record=[...w.stores.devices.values.values()][0];
 assert.deepEqual(Object.keys(record).sort(),['binding','schemaVersion','verifiedAt']);
 assert.doesNotMatch(JSON.stringify(record),/fixture password|derivedKey|salt/);   // a device record, never a password or verifier
 // second run: a new process, the same protected storage, no password typed
 const second=w.launch(),before=w.state.passwordReads;
 assert.equal((await second.info()).phase,'ready');
 assert.equal((await second.authorize()).session.method,'device-trust');
 assert.equal(w.state.passwordReads,before);   // the password prompt path was never entered
 // the OS lock screen or sleep clears the session, and opening again still asks nothing
 second.local.lock();assert.equal((await second.info()).phase,'ready');assert.ok((await second.authorize()).session);
});

test('Lock forgets this computer: the next run asks for the password once more, then remembers again',async()=>{
 const w=world();await verifyOnce(w.launch());
 const second=w.launch();assert.equal((await second.info()).phase,'ready');
 assert.equal((await second.action('lock')).phase,'locked');assert.equal(w.stores.devices.values.size,0);
 await assert.rejects(second.authorize(),/sign-in/);
 const third=w.launch();assert.equal((await third.info()).phase,'locked');
 await assert.rejects(third.action('unlock',{password:'a wrong long password'}),/Incorrect/);assert.equal(w.stores.devices.values.size,0);
 await third.action('unlock',{password:PASSWORD});
 assert.equal((await w.launch().info()).phase,'ready');
});

test('device trust never replaces administrator approval: missing, revoked or replaced approvals still need verification',async()=>{
 const w=world();await verifyOnce(w.launch());
 const gate=w.launch();assert.equal((await gate.info()).phase,'ready');
 // a remote or unapproved installation: no signed grant, so a stored device record opens nothing
 w.state.accepted=null;await assert.rejects(gate.authorize(),/approval/);assert.equal((await gate.info()).phase,'needs_request');
 await assert.rejects(w.launch().authorize(),/approval/);
 // revoked by the administrator
 w.state.accepted={grant:w.grant,revoked:true};await assert.rejects(w.launch().authorize(),/approval/);assert.equal((await w.launch().info()).phase,'revoked');
 // the administrator approves again (a new grant): this computer must verify again
 w.state.accepted={grant:{...w.grant,grantId:crypto.randomUUID(),requestId:crypto.randomUUID()},revoked:false};
 const again=w.launch();assert.equal((await again.info()).phase,'locked');await assert.rejects(again.authorize(),/sign-in/);
 // a record cannot be carried to another device or account
 const other=world();other.stores.devices.values.set(other.grant.deviceId,[...w.stores.devices.values.values()][0]);
 assert.equal((await other.launch().info()).phase,'needs_password');
});

test('an unreadable, damaged or unwritable device record falls back to the password and never blocks sign-in',async()=>{
 const damaged=world();await verifyOnce(damaged.launch());
 const record=[...damaged.stores.devices.values.values()][0];
 for(const bad of [{...record,extra:true},{...record,verifiedAt:-1},{...record,schemaVersion:2},{...record,binding:{...record.binding,grantId:crypto.randomUUID()}},{...record,binding:{...record.binding,deviceId:crypto.randomUUID()}},'garbage',null]){
  damaged.stores.devices.values.set(damaged.grant.deviceId,bad);assert.equal((await damaged.launch().info()).phase,'locked');
 }
 const old=world();old.stores.devices.read=async()=>{throw new Error('helper without this namespace');};old.stores.devices.write=async()=>{throw new Error('helper without this namespace');};
 const first=old.launch();await verifyOnce(first);   // unlock still succeeds although the record could not be kept
 assert.equal((await first.info()).phase,'ready');
 assert.equal((await old.launch().info()).phase,'locked');
});

test('forgetting wins over a device-record read that is still in flight',async()=>{
 const w=world(),g=w.grant;await verifyOnce(w.launch());
 const local=new LocalAccess({store:w.stores.passwords,devices:w.stores.devices});
 let release;const gate=new Promise(resolve=>{release=resolve;}),read=w.stores.devices.read;
 w.stores.devices.read=async id=>{const found=await read(id);await gate;return found;};   // found before the forget, delivered after it
 const pending=local.resume(g);await local.forget(g);release();
 assert.equal(await pending,null);assert.equal(local.current(g),null);assert.equal(w.stores.devices.values.size,0);
 w.stores.devices.read=read;assert.equal(await local.resume(g),null);
});

test('a lock (revocation, activation, suspend) wins over a device-record read that is still in flight',async()=>{
 const w=world(),g=w.grant;await verifyOnce(w.launch());
 const local=new LocalAccess({store:w.stores.passwords,devices:w.stores.devices});
 let release;const gate=new Promise(resolve=>{release=resolve;}),read=w.stores.devices.read;
 w.stores.devices.read=async id=>{const found=await read(id);await gate;return found;};   // read started before the lock, delivered after it
 const pending=local.resume(g);local.lock();release();
 assert.equal(await pending,null);assert.equal(local.current(g),null);
 assert.equal(w.stores.devices.values.size,1);   // lock keeps the record: only forget deletes it
 w.stores.devices.read=read;assert.equal((await local.resume(g))?.method,'device-trust');   // a later open still uses it
});

test('an open that starts while a forget is still deleting the record gets no session, whether the delete then succeeds or fails',async()=>{
 for(const outcome of ['succeeds','fails']){
  const w=world(),g=w.grant;await verifyOnce(w.launch());
  const local=new LocalAccess({store:w.stores.passwords,devices:w.stores.devices}),remove=w.stores.devices.delete;
  let release;const gate=new Promise(resolve=>{release=resolve;});
  w.stores.devices.delete=async id=>{await gate;if(outcome==='fails')throw new Error('credential store unavailable');return remove(id);};
  const forgetting=local.forget(g);   // epoch is already bumped and the record is still stored: the delete has not settled
  assert.equal(w.stores.devices.values.size,1);
  assert.equal(await local.resume(g),null);assert.equal(local.current(g),null);   // started after the forget began, so only the in-progress guard stops it
  release();
  if(outcome==='fails')await assert.rejects(forgetting,/unavailable/);else await forgetting;
  assert.equal(local.forgetting,0);assert.equal(w.stores.devices.values.size,outcome==='fails'?1:0);
  w.stores.devices.delete=remove;
  assert.equal((await local.resume(g))?.method,outcome==='fails'?'device-trust':undefined);   // a failed delete was reported; the record still stands
 }
});

test('without a device store every launch still asks, and Terrier is never remembered by LocalAccess',async()=>{
 const w=world(),passwordOnly=new LocalAccess({store:w.stores.passwords});
 await passwordOnly.create(w.grant,PASSWORD);await passwordOnly.unlock(w.grant,PASSWORD);
 assert.equal(await new LocalAccess({store:w.stores.passwords}).resume(w.grant),null);
 const t=world('terrier'),local=new LocalAccess({store:t.stores.passwords,devices:t.stores.devices});
 assert.equal(await local.remember(t.grant),false);assert.equal(t.stores.devices.values.size,0);assert.equal(await local.resume(t.grant),null);   // Terrier has its own account system
});

test('the approved Terrier owner is never asked for a password; other Terrier accounts still use Terrier sign-in',async()=>{
 const owner=world('terrier',{owner:{mode:'existing-owner',dataBindings:[{districtId:'co-05',localUsername:'admin',localUserId:'7'}]}});
 const gate=owner.launch(),info=await gate.info();
 assert.equal(info.phase,'ready');assert.equal(info.accountIdentity,'admin');
 const approved=await gate.authorize();assert.equal(approved.principal.mode,'existing-owner');assert.equal(approved.principal.localAccountIdentity,'admin');
 assert.equal(owner.state.passwordReads,0);   // no password record is even consulted
 await assert.rejects(gate.action('setPassword',{password:PASSWORD}),/unavailable/);await assert.rejects(gate.action('unlock',{password:PASSWORD}),/unavailable/);
 const recipient=world('terrier'),other=recipient.launch();
 assert.equal((await other.info()).phase,'terrier_signin');assert.equal((await other.authorize()).principal.mode,'recipient');
 owner.state.accepted=null;await assert.rejects(gate.authorize(),/approval/);   // the owner still needs a current approval
 owner.state.accepted={grant:owner.grant,revoked:true};await assert.rejects(gate.authorize(),/approval/);assert.equal((await gate.info()).phase,'revoked');
});
