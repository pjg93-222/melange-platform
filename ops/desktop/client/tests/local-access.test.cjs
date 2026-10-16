'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const crypto=require('node:crypto');
const {LocalAccess,OPTIONS}=require('../local-access.cjs');
const grant=()=>({appId:'mentat',serverId:crypto.randomUUID(),deviceId:crypto.randomUUID(),principalId:crypto.randomUUID(),accountIdentity:'recipient_fixture',role:'local_user',districtIds:[]});
function store(){let value;return {read:async()=>value?structuredClone(value):null,write:async(_id,record)=>{value=structuredClone(record);},peek:()=>value};}
test('async verifier uses fixed strong parameters; creation is not login',async()=>{
 const vault=store(),a=new LocalAccess({store:vault}),g=grant();let responsive=false;
 const timer=setTimeout(()=>{responsive=true;},0);await a.create(g,'one long fixture password');clearTimeout(timer);
 assert.equal(responsive,true);assert.equal(a.current(g),null);assert.equal(vault.peek().N,131072);assert.equal(OPTIONS.maxmem,192*1024*1024);
 assert.ok(!JSON.stringify(vault.peek()).includes('one long fixture password'));
 const session=await a.unlock(g,'one long fixture password');assert.equal(session.accountIdentity,g.accountIdentity);assert.equal(a.current(g).sessionId,session.sessionId);a.lock();assert.equal(a.current(g),null);
 await assert.rejects(a.create(g,'another long password'),/already exists/);
});
test('wrong password and durable throttling survive object restart',async()=>{
 const vault=store(),g=grant();let now=2000000000000;let a=new LocalAccess({store:vault,now:()=>now});
 await a.create(g,'correct long fixture password');
 for(let i=0;i<5;i++)await assert.rejects(a.unlock(g,'wrong long fixture password'),/Incorrect/);
 assert.equal(vault.peek().failures,5);a=new LocalAccess({store:vault,now:()=>now});
 await assert.rejects(a.unlock(g,'correct long fixture password'),/Too many/);now+=5001;
 assert.ok(await a.unlock(g,'correct long fixture password'));assert.equal(vault.peek().failures,0);
});
test('mismatched account, malformed verifier, excessive password and Terrier refused',async()=>{
 const vault=store(),g=grant(),a=new LocalAccess({store:vault});await a.create(g,'correct long fixture password');
 await assert.rejects(a.unlock({...g,deviceId:crypto.randomUUID()},'correct long fixture password'),/different approved/);
 vault.peek().derivedKey='bad';await assert.rejects(a.unlock(g,'correct long fixture password'),/Invalid protected/);
 await assert.rejects(new LocalAccess({store:store()}).create(grant(),'x'.repeat(1025)),/at most/);
 await assert.rejects(a.create({...g,appId:'terrier',role:'local_administrator',districtIds:['co-05']},'correct long fixture password'),/existing sign-in/);
});
