'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const crypto=require('node:crypto');
const {adoptBundledTrust}=require('../access-bootstrap.cjs');const {descriptor,canonical}=require('../access-client.cjs');
function trust(url='https://192.168.1.10:7443'){
 const raw=crypto.randomBytes(32),key=raw.toString('base64'),keyId=crypto.createHash('sha256').update(raw).digest('hex').slice(0,24);
 return {schemaVersion:1,purpose:'melange-desktop-trust',serverId:crypto.randomUUID(),url,tlsSpkiSha256:'a'.repeat(64),accessPublicKeys:{[keyId]:key},updatePublicKeysByApp:Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{[keyId]:key}]))};
}
const store=()=>{const writes=[];return {writes,write:async value=>{writes.push(structuredClone(value));}};};
test('bundled address replaces the stored one when verification identity is unchanged and is persisted canonically',async()=>{
 const stored=trust(),bundled={...stored,url:'https://updates.example.org'},vault=store();
 const next=await adoptBundledTrust(vault,stored,bundled);
 assert.equal(next.url,'https://updates.example.org');assert.deepEqual(vault.writes,[next]);assert.equal(canonical(next),canonical(descriptor(bundled)));
});
test('identical bundled trust writes nothing',async()=>{
 const stored=descriptor(trust()),vault=store();
 assert.deepEqual(await adoptBundledTrust(vault,stored,structuredClone(stored)),stored);assert.deepEqual(vault.writes,[]);
});
test('a changed verification identity throws and persists nothing',async()=>{
 const stored=trust(),vault=store();
 for(const changes of [{tlsSpkiSha256:'b'.repeat(64)},{serverId:crypto.randomUUID()},{accessPublicKeys:trust().accessPublicKeys}])await assert.rejects(adoptBundledTrust(vault,stored,{...stored,...changes,url:'https://updates.example.org'}),error=>error.reason==='trust'&&/identity/.test(error.message));
 assert.deepEqual(vault.writes,[]);
});
test('a failed persist still returns the identity-checked bundled descriptor for this session',async()=>{
 const stored=trust(),bundled={...stored,url:'https://updates.example.org'};
 const next=await adoptBundledTrust({write:async()=>{throw new Error('disk full');}},stored,bundled);
 assert.equal(next.url,'https://updates.example.org');assert.equal(canonical(next),canonical(descriptor(bundled)));
});
