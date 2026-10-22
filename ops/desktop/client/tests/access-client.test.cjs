'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const crypto=require('node:crypto');
const {AccessClient,accessRequest,accessFailure,descriptor,verifyGrant,verifyStatus,canonical,trustIdentityHash}=require('../access-client.cjs');
const now=Date.now(),CODE='ABCD-2345-WXYZ-6789',INVITE={inviteCode:CODE};
function fixture(){
 const pair=crypto.generateKeyPairSync('ed25519'),raw=pair.publicKey.export({format:'der',type:'spki'}).subarray(-32),keyId=crypto.createHash('sha256').update(raw).digest('hex').slice(0,24),key=raw.toString('base64');
 const trust={schemaVersion:1,purpose:'melange-desktop-trust',serverId:crypto.randomUUID(),url:'https://192.168.1.10:7443',tlsSpkiSha256:'a'.repeat(64),accessPublicKeys:{[keyId]:key},updatePublicKeysByApp:Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{[keyId]:key}]))};
 const identity={appId:'mentat',deviceId:crypto.randomUUID(),platform:'win32',arch:'x64'},principalId=crypto.randomUUID();
 const body={schemaVersion:1,purpose:'melange-desktop-access',serverId:trust.serverId,grantId:crypto.randomUUID(),principalId,accountIdentity:'recipient_'+principalId.replaceAll('-',''),requestId:crypto.randomUUID(),...identity,role:'local_user',districtIds:[],sequence:1,issuedAt:Math.floor(now/1000),offlineAllowed:true};
 const sign=(value,bytes=Buffer.from(canonical(value)))=>({schemaVersion:1,keyId,payload:bytes.toString('base64'),signature:crypto.sign(null,bytes,pair.privateKey).toString('base64')});
 const status=(changes={})=>{const value={...body,purpose:'melange-desktop-access-status',revoked:false,...changes};delete value.requestId;delete value.offlineAllowed;return value;};
 return {trust,identity,body,sign,status};
}
test('grant verifies separately from pin and binds app/device/server/account/Windows target',()=>{
 const f=fixture();assert.equal(verifyGrant(f.sign(f.body),f.trust,f.identity,{},now).grant.accountIdentity,f.body.accountIdentity);
 for(const changes of [{appId:'melange'},{deviceId:crypto.randomUUID()},{serverId:crypto.randomUUID()},{accountIdentity:'admin'},{platform:'darwin'},{arch:'arm64'},{districtIds:['co-05']},{role:'superuser'},{offlineAllowed:false},{unknown:true}])assert.throws(()=>verifyGrant(f.sign({...f.body,...changes}),f.trust,f.identity,{},now));
 assert.equal(descriptor({...f.trust,url:'https://public.example.org'}).url,'https://public.example.org');
});
test('unknown signer, altered bytes, duplicate keys and noncanonical numbers rejected',()=>{
 const f=fixture(),envelope=f.sign(f.body);assert.throws(()=>verifyGrant({...envelope,keyId:'f'.repeat(24)},f.trust,f.identity));
 assert.throws(()=>verifyGrant({...envelope,payload:Buffer.from(canonical({...f.body,sequence:7})).toString('base64')},f.trust,f.identity));
 for(const text of [canonical(f.body).replace('"sequence":1','"sequence":1,"sequence":1'),canonical(f.body).replace('"sequence":1','"sequence":1.0')])assert.throws(()=>verifyGrant(f.sign(null,Buffer.from(text)),f.trust,f.identity),/Noncanonical/);
});
test('rollback/equivocation refused; learned revocation sticky until higher approved grant',()=>{
 const f=fixture(),accepted=verifyGrant(f.sign(f.body),f.trust,f.identity,{},now);
 const status=verifyStatus(f.sign(f.status()),f.trust,f.identity,accepted,now);
 assert.doesNotThrow(()=>verifyStatus(f.sign(f.status({issuedAt:f.body.issuedAt+1})),f.trust,f.identity,status,now));
 const revoked=verifyStatus(f.sign(f.status({sequence:2,revoked:true})),f.trust,f.identity,status,now);
 assert.equal(revoked.revoked,true);
 assert.throws(()=>verifyGrant(f.sign(f.body),f.trust,f.identity,revoked,now),/Replayed/);
 assert.throws(()=>verifyStatus(f.sign(f.status({sequence:2})),f.trust,f.identity,revoked,now));
 assert.throws(()=>verifyStatus(f.sign(f.status({sequence:1,revoked:true})),f.trust,f.identity,revoked,now));
 assert.throws(()=>verifyGrant(f.sign({...f.body,issuedAt:f.body.issuedAt+1}),f.trust,f.identity,accepted,now),/Replayed/);
 assert.equal(verifyGrant(f.sign({...f.body,grantId:crypto.randomUUID(),sequence:3}),f.trust,f.identity,revoked,now).revoked,false);
});
test('Terrier signed sorted districts are explicit; wrong status scope refused',()=>{
 const f=fixture();f.identity.appId='terrier';Object.assign(f.body,{appId:'terrier',role:'local_administrator',districtIds:['co-05','or-05']});
 const accepted=verifyGrant(f.sign(f.body),f.trust,f.identity,{},now);
 assert.deepEqual(accepted.grant.districtIds,['co-05','or-05']);
 assert.throws(()=>verifyGrant(f.sign({...f.body,districtIds:['or-05','co-05']}),f.trust,f.identity,{},now));
 assert.throws(()=>verifyStatus(f.sign(f.status({districtIds:['ct-01']})),f.trust,f.identity,accepted,now),/another approved/);
});
function stores(){let state;const values=new Map();return {store:{read:async()=>structuredClone(state),write:async value=>{state=structuredClone(value);}},secrets:{read:async scope=>structuredClone(values.get(scope)),write:async(scope,value)=>{values.set(scope,structuredClone(value));},delete:async scope=>values.delete(scope)},state:()=>state,values};}
test('response-loss retry retains request secret/key, activation scoped, offline valid, revocation survives restart',async()=>{
 const f=fixture(),vault=stores(),accessToken=crypto.randomBytes(48).toString('base64url');let activationCalls=0,offline=false,revoked=false;
 const request=async(_trust,path,options)=>{
  if(offline)throw new Error('network unavailable');
  if(path==='/v1/access/requests'){f.body.requestId=options.body.requestId;return {requestId:f.body.requestId,status:'pending',expiresAt:f.body.issuedAt+86400};}
  if(path==='/v1/access/activate'){activationCalls++;assert.equal(options.body.requestId,f.body.requestId);if(activationCalls===1)throw new Error('response lost');return {grant:f.sign(f.body),accessToken};}
  if(path==='/v1/access/status')return f.sign(f.status({sequence:revoked?2:1,revoked}));
  throw new Error('unexpected operation');
 };
 let client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});
 assert.equal(await client.permitted(),false);await client.requestAccess('Fixture device',INVITE);
 const key=crypto.randomBytes(32).toString('base64url');await assert.rejects(client.activate(key),/response lost/);
 assert.equal(vault.values.get('access-request').requests[f.body.requestId].activationKey,key);assert.equal(await client.permitted(),false);
 client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});await client.activate(key);
 assert.equal(vault.values.has('access-request'),false);assert.equal(await client.permitted(),true);
 offline=true;await assert.rejects(client.refresh(),/network unavailable/);assert.equal(await client.permitted(),true);
 offline=false;revoked=true;await client.refresh();assert.equal(await client.permitted(),false);
 client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});assert.equal(await client.permitted(),false);
 offline=true;await assert.rejects(client.refresh());assert.equal(await client.permitted(),false);
});
test('failed protected state write does not grant access and changed trust never becomes owner mode',async()=>{
 const f=fixture(),vault=stores();let calls=0;
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request:async()=>{calls++;throw new Error('unexpected network');}});
 vault.store.write=async()=>{throw new Error('protected store unavailable');};
 await assert.rejects(client.requestAccess('Device',INVITE),/protected store/);assert.equal(calls,0);assert.equal(await client.permitted(),false);
});
test('activation token/state/cleanup failures retain correct grant binding across restart',async()=>{
 const f=fixture(),vault=stores();let suppliedGrant=f.body,token=crypto.randomBytes(48).toString('base64url');
 const request=async(_trust,path,options)=>{
  if(path==='/v1/access/requests'){suppliedGrant.requestId=options.body.requestId;return {requestId:suppliedGrant.requestId,status:'pending',expiresAt:f.body.issuedAt+86400};}
  if(path==='/v1/access/activate')return {grant:f.sign(suppliedGrant),accessToken:token};
  throw new Error('unexpected operation');
 };
 let client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});
 const key=crypto.randomBytes(32).toString('base64url');await client.requestAccess('Device',INVITE);await client.activate(key);
 const originalGrant=f.body.grantId,originalToken=token;
 suppliedGrant={...f.body,grantId:crypto.randomUUID(),sequence:3};token=crypto.randomBytes(48).toString('base64url');
 await client.requestAccess('Device',{...INVITE,reapproval:true});
 const write=vault.store.write;vault.store.write=async value=>{if(value.grantEnvelope&&value.pending===null)throw new Error('state commit failed');return write(value);};
 await assert.rejects(client.activate(key),/state commit failed/);
 client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});
 assert.equal((await client.accepted()).grant.grantId,originalGrant);
 assert.equal(vault.values.get('access-token').tokens[originalGrant],originalToken);
 assert.equal(vault.values.get('access-token').tokens[suppliedGrant.grantId],token);
 vault.store.write=write;vault.secrets.delete=async()=>{throw new Error('cleanup failed');};
 const result=await client.activate(key);assert.equal(result.grant.grantId,suppliedGrant.grantId);assert.equal(result.requestCleanupPending,true);
 client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});
 assert.equal((await client.accepted()).grant.grantId,suppliedGrant.grantId);assert.equal(await client.permitted(),true);
 vault.values.delete('access-token');await assert.rejects(client.refresh(),/credential.*missing/i);
});

function tlsMaterial(){
 const {spawnSync}=require('node:child_process');
 const generation=spawnSync(process.env.DESKTOP_TEST_PYTHON||'python',['-c',String.raw`
import datetime,json
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fixture-only')])
now=datetime.datetime.now(datetime.timezone.utc)
cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(hours=1)).sign(key,hashes.SHA256())
print(json.dumps({'key':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),'cert':cert.public_bytes(serialization.Encoding.PEM).decode()}))
`],{encoding:'utf8',timeout:30000,windowsHide:true}); // Bound fixture startup separately from the transport deadlines below.
 assert.equal(generation.status,0,'TLS fixture generator failed: '+String(generation.stderr));return JSON.parse(generation.stdout);
}
test('native pinned transport has absolute drip deadline, rejects redirects/overflow/aborts, wrong pins and ambiguous hosts',async t=>{
 const https=require('node:https'),material=tlsMaterial();
 let mode='drip',hits=0;const sockets=new Set();
 const server=https.createServer(material,(_request,response)=>{hits++;
  if(mode==='redirect'){response.writeHead(302,{Location:'/v1/access/status'});response.end();return;}
  if(mode==='forbidden'){response.writeHead(403);response.end('request authentication required');return;}
  response.writeHead(200,{'Content-Type':'application/json'});
  if(mode==='drip'){response.write('{');const interval=setInterval(()=>response.write(' '),20);response.on('close',()=>clearInterval(interval));}
  if(mode==='overflow')response.end('x'.repeat(20000));
  if(mode==='ok')response.end('{}');
  if(mode==='abort'){response.write('{');setImmediate(()=>response.destroy());}
 });
 server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>{for(const socket of sockets)socket.destroy();server.close();});
 const f=fixture();f.trust.url='https://127.0.0.1:'+server.address().port;
 f.trust.tlsSpkiSha256=crypto.createHash('sha256').update(new crypto.X509Certificate(material.cert).publicKey.export({format:'der',type:'spki'})).digest('hex');
 const started=Date.now();await assert.rejects(accessRequest(f.trust,'/v1/access/status',{deadlineMs:150}),error=>error.reason==='timeout'&&/deadline/.test(error.message));assert.ok(Date.now()-started<1500);
 await assert.rejects(accessRequest({...f.trust,tlsSpkiSha256:'0'.repeat(64)},'/v1/access/status',{deadlineMs:500}),error=>error.reason==='identity'&&/identity mismatch/.test(error.message));
 mode='redirect';const prior=hits;await assert.rejects(accessRequest(f.trust,'/v1/access/status',{deadlineMs:500}),error=>error.reason==='refused'&&error.statusCode===302&&/refused/.test(error.message));assert.equal(hits,prior+1);
 mode='forbidden';await assert.rejects(accessRequest(f.trust,'/v1/access/status',{deadlineMs:500}),error=>error.reason==='refused'&&error.statusCode===403);
 mode='overflow';await assert.rejects(accessRequest(f.trust,'/v1/access/status',{deadlineMs:500}),/too large/);
 mode='abort';await assert.rejects(accessRequest(f.trust,'/v1/access/status',{deadlineMs:500}),/interrupted|unavailable/);
 for(const url of ['https://[::1]','https://localhost','https://127.1','https://127.000.0.1'])assert.throws(()=>descriptor({...f.trust,url}));
 // DNS is replaced only inside this fixture; both real HTTPS clients still
 // handshake, send SNI and enforce their configured TLS key and origin.
 const tls=require('node:tls'),connect=tls.connect,serverNames=[];
 tls.connect=options=>{serverNames.push(options.servername);return connect({...options,host:'127.0.0.1'});};t.after(()=>{tls.connect=connect;});
 const publicTrust={...f.trust,url:'https://updates.example.org:'+server.address().port};mode='ok';
 assert.deepEqual(await accessRequest(publicTrust,'/v1/access/status',{deadlineMs:1000}),{});
 const {jsonRequest}=require('../trust.cjs');assert.deepEqual(await jsonRequest(publicTrust,'/v1/status'),{});
 assert.deepEqual(serverNames,['updates.example.org','updates.example.org']);
 await assert.rejects(jsonRequest({...publicTrust,tlsSpkiSha256:'0'.repeat(64)},'/v1/status'),/TLS identity changed/);
 mode='redirect';const beforeRedirect=hits;await assert.rejects(jsonRequest(publicTrust,'/v1/status'),/redirects/);assert.equal(hits,beforeRedirect+1);

 await new Promise(resolve=>setTimeout(resolve,25));assert.equal(sockets.size,0);
});

test('explicit terminal request replacement preserves old recovery across failed commit and refuses active requests',async()=>{
 const f=fixture(),vault=stores();let status='pending',expiry=Math.floor(now/1000)+600,posts=0;
 const request=async(_trust,path,options)=>{
  if(path==='/v1/access/requests'){posts++;f.body.requestId=options.body.requestId;return {requestId:options.body.requestId,status:'pending',expiresAt:expiry};}
  if(path.startsWith('/v1/access/requests/'))return {requestId:path.split('/').at(-1),status,expiresAt:expiry};
  if(path==='/v1/access/activate')return {grant:f.sign(f.body),accessToken:crypto.randomBytes(48).toString('base64url')};
  throw new Error('unexpected route');
 };
 let client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});await client.requestAccess('Fixture',INVITE);
 const originalId=vault.state().pending.requestId,originalSecret=vault.values.get('access-request').requests[originalId].requestSecret;
 for(const active of ['pending','approved','used']){status=active;await assert.rejects(client.requestAccess('Fixture',{...INVITE,newRequest:true}),/preserved/);assert.equal(vault.state().pending.requestId,originalId);}
 status='expired';const write=vault.store.write;vault.store.write=async()=>{throw new Error('commit failed');};await assert.rejects(client.requestAccess('Fixture',{...INVITE,newRequest:true}),/commit failed/);
 assert.equal(vault.values.get('access-request').requests[originalId].requestSecret,originalSecret);assert.equal(vault.state().pending.requestId,originalId);assert.equal(posts,1);
 vault.store.write=write;client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});await client.requestAccess('Fixture',{...INVITE,newRequest:true});
 assert.notEqual(vault.state().pending.requestId,originalId);assert.equal(Object.keys(vault.values.get('access-request').requests).length,1);assert.equal(posts,2);
 status='used';expiry=Math.floor(now/1000)-1;const secondId=vault.state().pending.requestId;await client.requestAccess('Fixture',{...INVITE,newRequest:true});assert.notEqual(vault.state().pending.requestId,secondId);
 await client.activate(crypto.randomBytes(32).toString('base64url'));assert.equal(await client.permitted(),true);
});

test('expired consumed renewal can restart while preserving the older active or revoked signed ledger',async()=>{
 for(const revoked of [false,true]){
  const f=fixture(),vault=stores();let requestState='pending',expiry=Math.floor(now/1000)+600;
  const request=async(_trust,path,options)=>{
   if(path==='/v1/access/requests')return {requestId:options.body.requestId,status:'pending',expiresAt:expiry};
   if(path.startsWith('/v1/access/requests/'))return {requestId:path.split('/').at(-1),status:requestState,expiresAt:expiry};
   if(path==='/v1/access/activate'){f.body.requestId=options.body.requestId;return {grant:f.sign(f.body),accessToken:crypto.randomBytes(48).toString('base64url')};}
   if(path==='/v1/access/status')return f.sign(f.status({sequence:revoked?2:1,revoked}));
   throw new Error('unexpected route');
  };
  let client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});await client.requestAccess('Fixture',INVITE);await client.activate(crypto.randomBytes(32).toString('base64url'));await client.refresh();
  const before=structuredClone(vault.state());await client.requestAccess('Fixture',{...INVITE,reapproval:true});const consumedId=vault.state().pending.requestId;
  requestState='used';expiry=Math.floor(now/1000)-1;client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});
  await client.requestAccess('Fixture',{...INVITE,reapproval:true,newRequest:true});assert.notEqual(vault.state().pending.requestId,consumedId);
  for(const field of ['grantEnvelope','statusEnvelope','highestSequence','revoked'])assert.deepEqual(vault.state()[field],before[field]);
  assert.equal((await client.accepted()).revoked,revoked);
 }
});


async function updateFixture(){
 const f=fixture(),vault=stores(),token=crypto.randomBytes(48).toString('base64url');f.trust.url='https://updates.example.org';
 await vault.store.write({schemaVersion:1,identity:f.identity,trustHash:trustIdentityHash(f.trust),grantEnvelope:f.sign(f.body),highestSequence:1,revoked:false});
 await vault.secrets.write('access-token',{tokens:{[f.body.grantId]:token}});
 const reply={appId:f.identity.appId,deviceId:f.identity.deviceId,deviceToken:crypto.randomBytes(48).toString('base64url'),trust:{schemaVersion:1,url:f.trust.url,appId:f.identity.appId,tlsSpkiSha256:f.trust.tlsSpkiSha256,publicKeys:f.trust.updatePublicKeysByApp[f.identity.appId]}};
 const requests=[];let response=reply;
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,now:()=>now,request:async(trust,route,options)=>{requests.push({trust,route,options});return structuredClone(response);}});
 return {f,vault,client,token,reply,requests,set response(value){response=value;}};
}
test('approved access exchanges a separate scoped download credential using only configured trust',async()=>{
 const f=await updateFixture(),before=structuredClone(f.vault.state());
 const value=await f.client.enrollUpdates('1.0.1');
 assert.deepEqual(value,{...f.reply.trust,appId:f.reply.appId,deviceId:f.reply.deviceId,deviceToken:f.reply.deviceToken});
 assert.equal(f.requests.length,1);assert.equal(f.requests[0].route,'/v1/access/update-enrollment');
 assert.deepEqual(f.requests[0].options,{method:'POST',authorization:'Bearer '+f.token,body:{currentVersion:'1.0.1'}});
 assert.notEqual(value.deviceToken,f.token);assert.deepEqual(f.vault.state(),before);
});
test('update exchange rejects response trust substitution, wrong device/app, reused access tokens and malformed metadata',async()=>{
 const f=await updateFixture(),before=structuredClone(f.vault.state());
 for(const mutate of [x=>x.appId='terrier',x=>x.deviceId=crypto.randomUUID(),x=>x.deviceToken=f.token,x=>x.deviceToken='short',x=>x.trust.url='https://untrusted.example.org',x=>x.trust.tlsSpkiSha256='b'.repeat(64),x=>x.trust.publicKeys={attacker:Buffer.alloc(32).toString('base64')},x=>x.trust.appId='terrier',x=>x.extra=true]){
  const changed=structuredClone(f.reply);mutate(changed);f.response=changed;
  await assert.rejects(f.client.enrollUpdates('1.0.1'));
 }
 assert.deepEqual(f.vault.state(),before);
});
test('additive server key rotation cannot introduce trust through the enrollment response',async()=>{
 const f=await updateFixture(),extra=crypto.randomBytes(32).toString('base64');
 f.response={...f.reply,trust:{...f.reply.trust,publicKeys:{...f.reply.trust.publicKeys,newSigner:extra}}};
 const enrollment=await f.client.enrollUpdates('1.0.1');
 assert.deepEqual(enrollment.publicKeys,f.reply.trust.publicKeys);assert.equal(enrollment.publicKeys.newSigner,undefined);
 const pinnedId=Object.keys(f.reply.trust.publicKeys)[0];
 f.response={...f.reply,trust:{...f.reply.trust,publicKeys:{[pinnedId]:extra}}};
 await assert.rejects(f.client.enrollUpdates('1.0.1'),/trust/);
 f.response={...f.reply,trust:{...f.reply.trust,publicKeys:{}}};
 await assert.rejects(f.client.enrollUpdates('1.0.1'));
});
test('unapproved, revoked or missing protected access credentials never request download enrollment',async()=>{
 const f=await updateFixture();
 for(const version of ['not-a-version','v1.0.1','1.0.1+unsupported-build','01.0.1'])await assert.rejects(f.client.enrollUpdates(version),/version/);
 f.vault.values.delete('access-token');await assert.rejects(f.client.enrollUpdates('1.0.1'),/credential/);
 await f.vault.secrets.write('access-token',{tokens:{[f.f.body.grantId]:f.token}});
 const previous=structuredClone(f.vault.state());
 await f.vault.store.write({...previous,statusEnvelope:f.f.sign(f.f.status({sequence:2,revoked:true})),highestSequence:2,revoked:true});
 await assert.rejects(f.client.enrollUpdates('1.0.1'),/approval/);
 await f.vault.store.write({...previous,grantEnvelope:null});await assert.rejects(f.client.enrollUpdates('1.0.1'),/approval/);
 assert.equal(f.requests.length,0);
});

test('connection refused and a silent server carry stable reason codes',async t=>{
 const net=require('node:net'),f=fixture(),sockets=new Set();
 const probe=net.createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));const closed=probe.address().port;await new Promise(resolve=>probe.close(resolve));
 const silent=net.createServer(socket=>{sockets.add(socket);socket.on('error',()=>{});});await new Promise(resolve=>silent.listen(0,'127.0.0.1',resolve));
 t.after(()=>{for(const socket of sockets)socket.destroy();silent.close();});
 await assert.rejects(accessRequest({...f.trust,url:'https://127.0.0.1:'+closed},'/v1/access/status',{deadlineMs:2000}),error=>error.reason==='unreachable'&&/unavailable/.test(error.message));
 await assert.rejects(accessRequest({...f.trust,url:'https://127.0.0.1:'+silent.address().port},'/v1/access/status',{deadlineMs:100}),error=>error.reason==='timeout');
});
test('accessFailure forwards only a known reason code, never message text, ids or secrets',()=>{
 for(const reason of ['unreachable','timeout','refused','identity','trust','invite'])assert.equal(accessFailure(Object.assign(new Error('secret-token request-id /private/path'),{reason})),'access:'+reason);
 for(const error of [new Error('secret-token'),Object.assign(new Error('x'),{reason:'secret-token'}),Object.assign(new Error('x'),{reason:'toString'}),Object.assign(new Error('x'),{statusCode:403}),null,undefined,'access:identity'])assert.equal(accessFailure(error),'access:failed');
});
test('poll re-POSTs the same unacknowledged request on 403 only; a distinct request is still refused while it is pending',async()=>{
 const f=fixture(),vault=stores();let clock=now,postFails=true,getError=null;const posts=[],gets=[];
 const request=async(_trust,path,options)=>{
  if(path==='/v1/access/requests'){posts.push(options.body);if(postFails)throw Object.assign(new Error('Access connection unavailable'),{reason:'unreachable'});return {requestId:options.body.requestId,status:'pending',expiresAt:f.body.issuedAt+86400};}
  gets.push(path);if(getError)throw getError;return {requestId:path.split('/').at(-1),status:'pending',expiresAt:f.body.issuedAt+86400};
 };
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>clock});
 await assert.rejects(client.requestAccess('Fixture',INVITE),/unavailable/);
 const id=vault.state().pending.requestId,material=structuredClone(vault.values.get('access-request'));assert.equal(posts.length,1);
 getError=Object.assign(new Error('Access service refused the request'),{statusCode:403});clock+=16000;
 await assert.rejects(client.poll(),/unavailable/);assert.equal(posts.length,2);
 postFails=false;clock+=16000;
 assert.equal((await client.poll()).status,'pending');assert.equal(posts.length,3);
 for(const body of posts){assert.equal(body.requestId,id);assert.equal(body.requestSecret,material.requests[id].requestSecret);assert.equal(body.displayName,'Fixture');}
 assert.equal(vault.state().pending.requestId,id);assert.deepEqual(vault.values.get('access-request'),material);
 for(const error of [Object.assign(new Error('x'),{statusCode:500}),Object.assign(new Error('x'),{statusCode:404}),new Error('Access connection unavailable')]){getError=error;clock+=16000;await assert.rejects(client.poll());}
 assert.equal(posts.length,3);
 getError=null;await assert.rejects(client.requestAccess('Fixture',{...INVITE,newRequest:true}),/preserved/);
 assert.equal(posts.length,3);assert.equal(vault.state().pending.requestId,id);
});
test('a failed first POST keeps the stored request so the next request re-sends the same id and secret',async()=>{
 const f=fixture(),vault=stores();let fail=true;const posts=[];
 const request=async(_trust,_path,options)=>{posts.push(options.body);if(fail)throw Object.assign(new Error('Access connection unavailable'),{reason:'unreachable'});return {requestId:options.body.requestId,status:'pending',expiresAt:f.body.issuedAt+86400};};
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});
 await assert.rejects(client.requestAccess('Fixture',INVITE),/unavailable/);fail=false;
 assert.equal((await client.requestAccess('Fixture',INVITE)).status,'pending');
 assert.equal(posts.length,2);assert.equal(posts[1].requestId,posts[0].requestId);assert.equal(posts[1].requestSecret,posts[0].requestSecret);
});

const inviteRefusal=()=>Object.assign(new Error('Access service refused the request'),{reason:'invite',statusCode:403});
const accepted=(f,options)=>({requestId:options.body.requestId,status:'pending',expiresAt:f.body.issuedAt+86400});
test('an optional invite code is trimmed, sent in the POST body, kept only in the protected request material and never in state or errors',async()=>{
 const f=fixture(),vault=stores(),posts=[],writes=[],write=vault.store.write;vault.store.write=async value=>{writes.push(JSON.stringify(value));return write(value);};
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request:async(_trust,_path,options)=>{posts.push(options.body);return accepted(f,options);},now:()=>now});
 for(const inviteCode of [null,5,['x'],'x'.repeat(65),' '+'x'.repeat(65)+' ']){const error=await client.requestAccess('Fixture',{inviteCode}).then(()=>null,e=>e);assert.match(error?.message,/invite code/i);}
 assert.equal(posts.length,0);assert.equal(writes.length,0);assert.equal(vault.values.has('access-request'),false);
 await client.requestAccess('Fixture',{inviteCode:'  '+CODE+'  '});const id=posts[0].requestId;
 assert.deepEqual(Object.keys(posts[0]).sort(),['appId','arch','deviceId','displayName','inviteCode','platform','requestId','requestSecret']);assert.equal(posts[0].inviteCode,CODE);
 assert.equal(vault.values.get('access-request').requests[id].inviteCode,CODE);
 assert.deepEqual(vault.state().pending,{requestId:id,displayName:'Fixture'});assert.ok(writes.length>0&&writes.every(text=>!text.includes(CODE)));
});
test('without an invite code the POST carries no inviteCode field and the material stores none (what servers with invites off expect)',async()=>{
 for(const inviteCode of [undefined,'','   ']){
  const f=fixture(),vault=stores(),posts=[];
  const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request:async(_trust,_path,options)=>{posts.push(options.body);return accepted(f,options);},now:()=>now});
  assert.equal((await client.requestAccess('Fixture',inviteCode===undefined?{}:{inviteCode})).status,'pending');
  assert.deepEqual(Object.keys(posts[0]).sort(),['appId','arch','deviceId','displayName','platform','requestId','requestSecret']);
  assert.deepEqual(Object.keys(vault.values.get('access-request').requests[posts[0].requestId]).sort(),['requestId','requestSecret']);
 }
});
test('an invite refusal clears the pending request and its material so a new name and code start a fresh request',async()=>{
 const f=fixture(),vault=stores(),posts=[];let refuse=true;
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request:async(_trust,_path,options)=>{posts.push(options.body);if(refuse)throw inviteRefusal();return accepted(f,options);},now:()=>now});
 const error=await client.requestAccess('Old name',{inviteCode:CODE}).then(()=>null,e=>e);
 assert.equal(error.reason,'invite');assert.equal(accessFailure(error),'access:invite');assert.ok(!(error.message+accessFailure(error)).includes(CODE));
 assert.equal(vault.state().pending,null);assert.equal(vault.values.has('access-request'),false);
 refuse=false;assert.equal((await client.requestAccess('New name',{inviteCode:'NEW-CODE'})).status,'pending');
 assert.equal(posts.length,2);assert.notEqual(posts[1].requestId,posts[0].requestId);assert.notEqual(posts[1].requestSecret,posts[0].requestSecret);
 assert.equal(posts[1].displayName,'New name');assert.equal(posts[1].inviteCode,'NEW-CODE');assert.deepEqual(vault.state().pending,{requestId:posts[1].requestId,displayName:'New name'});
});
test('refusals other than invite_invalid and transport failures keep the pending request and its code for retry',async()=>{
 for(const failure of [Object.assign(new Error('x'),{reason:'refused',statusCode:403}),Object.assign(new Error('x'),{reason:'unreachable'}),Object.assign(new Error('x'),{reason:'timeout'}),new Error('Invalid access response')]){
  const f=fixture(),vault=stores(),client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request:async()=>{throw failure;},now:()=>now});
  await assert.rejects(client.requestAccess('Fixture',INVITE),error=>error===failure);
  const id=vault.state().pending.requestId;assert.equal(vault.values.get('access-request').requests[id].inviteCode,CODE);
 }
});
test('poll re-POST resends the stored code, material without a code sends none, and an invite refusal there clears pending',async()=>{
 const f=fixture(),vault=stores();let clock=now,post='unreachable';const posts=[];
 const request=async(_trust,path,options)=>{
  if(path==='/v1/access/requests'){posts.push(options.body);if(post==='unreachable')throw Object.assign(new Error('Access connection unavailable'),{reason:'unreachable'});if(post==='invite')throw inviteRefusal();return accepted(f,options);}
  throw Object.assign(new Error('Access service refused the request'),{reason:'refused',statusCode:403});
 };
 const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>clock});
 await assert.rejects(client.requestAccess('Fixture',INVITE),/unavailable/);
 clock+=16000;post='ok';assert.equal((await client.poll()).status,'pending');assert.equal(posts[1].inviteCode,CODE);assert.equal(posts[1].requestId,posts[0].requestId);
 const id=vault.state().pending.requestId,requestSecret=vault.values.get('access-request').requests[id].requestSecret;
 await vault.secrets.write('access-request',{requests:{[id]:{requestId:id,requestSecret}}});
 clock+=16000;assert.equal((await client.poll()).status,'pending');assert.equal(Object.hasOwn(posts[2],'inviteCode'),false);
 post='invite';clock+=16000;const error=await client.poll().then(()=>null,e=>e);
 assert.equal(error.reason,'invite');assert.equal(vault.state().pending,null);assert.equal(vault.values.has('access-request'),false);
 clock+=16000;await assert.rejects(client.poll(),/No pending request/);
});
test('an invite refusal during reapproval clears only the new request and leaves the signed ledger and token intact',async()=>{
 for(const revoked of [false,true]){
  const f=fixture(),vault=stores();let refuse=false;
  const request=async(_trust,path,options)=>{
   if(path==='/v1/access/requests'){if(refuse)throw inviteRefusal();return accepted(f,options);}
   if(path==='/v1/access/activate'){f.body.requestId=options.body.requestId;return {grant:f.sign(f.body),accessToken:crypto.randomBytes(48).toString('base64url')};}
   if(path==='/v1/access/status')return f.sign(f.status({sequence:revoked?2:1,revoked}));
   throw new Error('unexpected route');
  };
  const client=new AccessClient({...vault,trust:f.trust,identity:f.identity,request,now:()=>now});await client.requestAccess('Fixture',INVITE);await client.activate(crypto.randomBytes(32).toString('base64url'));await client.refresh();
  const before=structuredClone(vault.state()),token=structuredClone(vault.values.get('access-token'));refuse=true;
  await assert.rejects(client.requestAccess('Fixture',{...INVITE,reapproval:true}),error=>error.reason==='invite');
  assert.equal(vault.state().pending,null);assert.equal(vault.values.has('access-request'),false);assert.deepEqual(vault.values.get('access-token'),token);
  for(const field of ['grantEnvelope','statusEnvelope','highestSequence','revoked'])assert.deepEqual(vault.state()[field],before[field]);
  assert.equal((await client.accepted()).revoked,revoked);
 }
});
test('only a 403 invite_invalid answer changes the refusal reason; other bodies, statuses and oversize bodies stay refused',async t=>{
 const https=require('node:https'),material=tlsMaterial(),sockets=new Set(),received=[];let status=403,body='';
 const server=https.createServer(material,(request,response)=>{const chunks=[];request.on('data',part=>chunks.push(part));request.on('end',()=>{received.push(Buffer.concat(chunks).toString('utf8'));response.writeHead(status,{'Content-Type':'application/json'});response.end(body);});});
 server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>{for(const socket of sockets)socket.destroy();server.close();});
 const f=fixture();f.trust.url='https://127.0.0.1:'+server.address().port;
 f.trust.tlsSpkiSha256=crypto.createHash('sha256').update(new crypto.X509Certificate(material.cert).publicKey.export({format:'der',type:'spki'})).digest('hex');
 const attempt=async(code,text)=>{status=code;body=text;return accessRequest(f.trust,'/v1/access/requests',{method:'POST',body:{inviteCode:CODE},deadlineMs:1500}).then(()=>null,e=>e);};
 const invite=await attempt(403,'{"error":"invite_invalid"}');assert.equal(invite.reason,'invite');assert.equal(invite.statusCode,403);assert.equal(JSON.parse(received[0]).inviteCode,CODE);
 for(const [code,text] of [[403,'{"error":"access_denied"}'],[403,'request authentication required'],[403,''],[403,'{"error":"invite_invalid"'],[403,'{"error":["invite_invalid"]}'],[403,'{"error":"INVITE_INVALID"}'],[403,'x'.repeat(20000)],[403,'{"error":"invite_invalid","pad":"'+'x'.repeat(20000)+'"}'],[429,'{"error":"invite_invalid"}'],[500,'{"error":"invite_invalid"}'],[409,'{"error":"state_conflict"}']]){
  const error=await attempt(code,text);assert.equal(error.reason,'refused',code+' '+text.slice(0,40));assert.equal(error.statusCode,code);
 }
});
