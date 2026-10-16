'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {canonical,trustIdentityHash,refreshDescriptor}=require('../access-client.cjs');
const {windowsPathHash,verifyOwnerRecord}=require('../owner-access.cjs');
function fixture(root,appId='mentat'){
 const pair=crypto.generateKeyPairSync('ed25519'),raw=pair.publicKey.export({format:'der',type:'spki'}).subarray(-32),keyId=crypto.createHash('sha256').update(raw).digest('hex').slice(0,24),key=raw.toString('base64');
 const trust={schemaVersion:1,purpose:'melange-desktop-trust',serverId:crypto.randomUUID(),url:'https://192.168.1.10:7443',tlsSpkiSha256:'a'.repeat(64),accessPublicKeys:{[keyId]:key},updatePublicKeysByApp:Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{[keyId]:key}]))};
 const principalId=crypto.randomUUID(),grant={appId,serverId:trust.serverId,deviceId:crypto.randomUUID(),principalId,grantId:crypto.randomUUID(),accountIdentity:'recipient_'+principalId.replaceAll('-',''),sequence:1,role:appId==='terrier'?'local_administrator':'local_user',districtIds:appId==='terrier'?['co-05']:[]};
 const windowsSid='S-1-5-21-1-2-3-1001',dataBindings=[{districtId:appId==='terrier'?'co-05':null,dataRoot:root,localUserId:appId==='terrier'?'7':null,localUsername:appId==='terrier'?'admin':null}];
 const body={schemaVersion:1,purpose:'melange-desktop-owner',...Object.fromEntries(['serverId','appId','deviceId','grantId','principalId','accountIdentity'].map(key=>[key,grant[key]])),grantSequence:1,issuedAt:Math.floor(Date.now()/1000),profileHash:windowsPathHash(root),windowsSid,dataBindings:dataBindings.map(({dataRoot,...item})=>({...item,dataRootHash:windowsPathHash(dataRoot)}))};
 const sign=value=>{const bytes=Buffer.from(canonical(value));return {schemaVersion:1,attestation:{schemaVersion:1,keyId,payload:bytes.toString('base64'),signature:crypto.sign(null,bytes,pair.privateKey).toString('base64')}};};
 return {body,trust,grant,sign,options:{trust,accepted:{grant,revoked:false},profile:root,windowsSid,dataBindings}};
}
test('administrator endpoint/update-key refresh preserves access identity; pin/server/access keys cannot reset',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'owner-fixture-'));try{
  const f=fixture(root),updated={...f.trust,url:'https://192.168.2.20:7443',updatePublicKeysByApp:{...f.trust.updatePublicKeysByApp,mentat:{newKey:Object.values(f.trust.accessPublicKeys)[0]}}};
  assert.equal(trustIdentityHash(updated),trustIdentityHash(f.trust));assert.equal(refreshDescriptor(f.trust,updated).url,updated.url);
  assert.equal(refreshDescriptor(f.trust,{...updated,url:'https://updates.example.org'}).url,'https://updates.example.org');
  for(const value of [{...updated,serverId:crypto.randomUUID()},{...updated,tlsSpkiSha256:'b'.repeat(64)},{...updated,url:'http://updates.example.org'}])assert.throws(()=>refreshDescriptor(f.trust,value));
 }finally{fs.rmSync(root,{recursive:true});}
});
test('owner attestation binds signature, current grant, Windows user/profile and exact data mappings',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'owner-fixture-'));try{
  const f=fixture(root);assert.equal(verifyOwnerRecord(f.sign(f.body),f.options).mode,'existing-owner');
  for(const changes of [{purpose:'melange-desktop-access'},{grantSequence:2},{grantId:crypto.randomUUID()},{windowsSid:'S-1-5-21-9-8-7-1001'},{profileHash:'a'.repeat(64)},{extra:true},{dataBindings:[]}])assert.throws(()=>verifyOwnerRecord(f.sign({...f.body,...changes}),f.options));
  const tampered=f.sign(f.body);tampered.attestation.signature=Buffer.alloc(64).toString('base64');assert.throws(()=>verifyOwnerRecord(tampered,f.options));
  assert.throws(()=>verifyOwnerRecord(f.sign(f.body),{...f.options,accepted:{grant:f.grant,revoked:true}}));
  const other=path.join(root,'other');fs.mkdirSync(other);assert.throws(()=>verifyOwnerRecord(f.sign(f.body),{...f.options,profile:other}));
  assert.equal(windowsPathHash(root),windowsPathHash(root.toUpperCase()));
 }finally{fs.rmSync(root,{recursive:true});}
});
test('Terrier migration requires exact existing account PK and approved district; no recipient fallback',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'owner-fixture-'));try{
  const f=fixture(root,'terrier');assert.equal(verifyOwnerRecord(f.sign(f.body),f.options).dataBindings[0].localUserId,'7');
  for(const changes of [{localUserId:'8'},{localUserId:'0'},{localUsername:'another'},{districtId:'or-05'}]){const body={...f.body,dataBindings:[{...f.body.dataBindings[0],...changes}]};assert.throws(()=>verifyOwnerRecord(f.sign(body),f.options));}
  assert.throws(()=>verifyOwnerRecord(null,f.options));
 }finally{fs.rmSync(root,{recursive:true});}
});

test('Python and Node canonical path hashes agree across case, junction and trailing slash; extended input refused',()=>{
 const {spawnSync}=require('node:child_process'),root=fs.mkdtempSync(path.join(os.tmpdir(),'owner-path-fixture-'));
 try{
  const target=path.join(root,'Target'),junction=path.join(root,'Alias');fs.mkdirSync(target);
  fs.symlinkSync(target,junction,'junction');
  const vectors=[target,target.toUpperCase(),target+'\\',junction,'\\\\?\\'+target];
  const code="import sys,json;sys.path.insert(0,sys.argv[1]);from native_access_state import path_hash\nresults=[]\nfor p in json.loads(sys.argv[2]):\n try:results.append(path_hash(p))\n except Exception:results.append('refused')\nprint(json.dumps(results))";
  const python=spawnSync(process.env.DESKTOP_TEST_PYTHON||'python',['-E','-s','-c',code,path.join(__dirname,'../../packaging'),JSON.stringify(vectors)],{windowsHide:true,encoding:'utf8',timeout:10000});assert.equal(python.status,0,'Python path fixture failed');
  const expected=JSON.parse(python.stdout),actual=vectors.map(value=>{try{return windowsPathHash(value);}catch{return 'refused';}});assert.deepEqual(actual,expected);assert.equal(actual[4],'refused');assert.ok(actual.slice(0,4).every(value=>value===actual[0]));
 }finally{fs.rmSync(root,{recursive:true});}
});
