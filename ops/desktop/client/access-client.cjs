'use strict';
// Access grants are independent of TLS pins, updater manifests and OS code signing.
const crypto=require('node:crypto');const https=require('node:https');const tls=require('node:tls');const net=require('node:net');
const semver=require('semver');
const {trustedOrigin}=require('./trust.cjs');
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const APPS=['melange','mentat','terrier'];const DISTRICTS=['co-05','ct-01','or-05'];
const GRANT=['schemaVersion','purpose','serverId','grantId','principalId','accountIdentity','requestId','appId','deviceId','platform','arch','role','districtIds','sequence','issuedAt','offlineAllowed'];
const STATUS=GRANT.filter(k=>!['requestId','offlineAllowed'].includes(k)).concat('revoked');
function exact(value,fields){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==[...fields].sort().join('|'))throw new Error('Unsupported access metadata');}
function uuid(value){return typeof value==='string'&&UUID.test(value)&&value!=='00000000-0000-0000-0000-000000000000';}
function canonical(value){if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>canonical(k)+':'+canonical(value[k])).join(',')+'}';return JSON.stringify(value).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
function base64(value,length,max=16384){if(typeof value!=='string'||value.length>max)throw new Error('Invalid access encoding');const bytes=Buffer.from(value,'base64');if(bytes.toString('base64')!==value||(length!==undefined&&bytes.length!==length))throw new Error('Invalid access encoding');return bytes;}
function secret(value){return typeof value==='string'&&/^[A-Za-z0-9_-]{43,86}$/.test(value)&&Buffer.from(value,'base64url').toString('base64url')===value;}
function requestMaterials(value){
 if(!value)return {};
 const records=value.requests||{[value.requestId]:value};
 if(typeof records!=='object'||Array.isArray(records)||Object.keys(records).length>2)throw new Error('Invalid protected request material');
 for(const [id,item]of Object.entries(records)){
  if(!uuid(id)||!item||item.requestId!==id||!secret(item.requestSecret)||Object.keys(item).some(key=>!['requestId','requestSecret','activationKey','inviteCode'].includes(key))||(item.activationKey!==undefined&&!secret(item.activationKey))||(item.inviteCode!==undefined&&(typeof item.inviteCode!=='string'||item.inviteCode.length>64)))throw new Error('Invalid protected request material');
 }
 return records;
}
function requestStatus(reply,id){exact(reply,['requestId','status','expiresAt']);if(reply.requestId!==id||!['pending','approved','used','denied','expired'].includes(reply.status)||!Number.isSafeInteger(reply.expiresAt)||reply.expiresAt<946684800)throw new Error('Invalid request status');return reply;}
function target(value){if(!APPS.includes(value.appId)||!uuid(value.deviceId)||value.platform!=='win32'||value.arch!=='x64')throw new Error('Unsupported application/device target');}
function keys(value,max,hashed){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length<1||Object.keys(value).length>max)throw new Error('Invalid access trust keys');for(const [id,key] of Object.entries(value)){const raw=base64(key,32);if(hashed?id!==crypto.createHash('sha256').update(raw).digest('hex').slice(0,24):!/^[A-Za-z0-9._-]{1,100}$/.test(id))throw new Error('Invalid access key identity');}}
function descriptor(value){
 exact(value,['schemaVersion','purpose','serverId','url','tlsSpkiSha256','accessPublicKeys','updatePublicKeysByApp']);
 if(value.schemaVersion!==1||value.purpose!=='melange-desktop-trust'||!uuid(value.serverId)||typeof value.tlsSpkiSha256!=='string'||!(/^[a-f0-9]{64}$/).test(value.tlsSpkiSha256))throw new Error('Invalid access trust descriptor');
 const origin=trustedOrigin(value.url);
 keys(value.accessPublicKeys,4,true);exact(value.updatePublicKeysByApp,APPS);for(const app of APPS)keys(value.updatePublicKeysByApp[app],8,false);
 return {...structuredClone(value),url:origin};
}
function trustIdentityHash(value){const checked=descriptor(value);const stable={schemaVersion:checked.schemaVersion,purpose:checked.purpose,serverId:checked.serverId,tlsSpkiSha256:checked.tlsSpkiSha256,accessPublicKeys:checked.accessPublicKeys};return crypto.createHash('sha256').update(canonical(stable)).digest('hex');}
function refreshDescriptor(previous,next){if(trustIdentityHash(previous)!==trustIdentityHash(next))throw fail('Access server verification identity cannot be replaced','trust');return descriptor(next);}
function envelope(value,trust){
 exact(value,['schemaVersion','keyId','payload','signature']);
 if(value.schemaVersion!==1||typeof value.keyId!=='string'||!Object.hasOwn(trust.accessPublicKeys,value.keyId))throw new Error('Untrusted access signer');
 const bytes=base64(value.payload,undefined,12000),signature=base64(value.signature,64,128);if(bytes.length>8192)throw new Error('Access metadata too large');
 const key=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),base64(trust.accessPublicKeys[value.keyId],32)]),format:'der',type:'spki'});
 if(!crypto.verify(null,bytes,key,signature))throw new Error('Invalid access signature');
 const text=bytes.toString('utf8'),body=JSON.parse(text);
 // Equality to canonical bytes also rejects duplicate keys, extra whitespace,
 // numeric aliases, invalid UTF-8 replacement and noncanonical escape forms.
 if(canonical(body)!==text)throw new Error('Noncanonical access metadata');
 return {body,digest:crypto.createHash('sha256').update(bytes).digest('hex')};
}
function verifyAccessEnvelope(value,trust){return envelope(value,descriptor(trust));}
function scope(body,trust,identity,now){
 target(body);target(identity);
 if(body.schemaVersion!==1||body.serverId!==trust.serverId||body.appId!==identity.appId||body.deviceId!==identity.deviceId||body.platform!==identity.platform||body.arch!==identity.arch)throw new Error('Access grant belongs to another application, device or server');
 if(!uuid(body.grantId)||!uuid(body.principalId)||body.accountIdentity!=='recipient_'+body.principalId.replaceAll('-',''))throw new Error('Invalid approved account identity');
 if(!Number.isSafeInteger(body.sequence)||body.sequence<1||!Number.isSafeInteger(body.issuedAt)||body.issuedAt<946684800||body.issuedAt>Math.floor(now/1000)+300)throw new Error('Invalid access sequence or timestamp');
 if(!Array.isArray(body.districtIds)||body.districtIds.some(d=>!DISTRICTS.includes(d))||new Set(body.districtIds).size!==body.districtIds.length||[...body.districtIds].sort().join('|')!==body.districtIds.join('|'))throw new Error('Invalid approved district scope');
 if(body.appId==='terrier'?(body.role!=='local_administrator'||body.districtIds.length<1):(body.role!=='local_user'||body.districtIds.length!==0))throw new Error('Invalid approved application role');
}
function verifyGrant(value,trustValue,identity,previous={},now=Date.now()){
 const trust=descriptor(trustValue),{body,digest}=envelope(value,trust);exact(body,GRANT);scope(body,trust,identity,now);
 if(body.purpose!=='melange-desktop-access'||body.offlineAllowed!==true||!uuid(body.requestId))throw new Error('Unsupported access grant');
 if(identity.requestId&&body.requestId!==identity.requestId)throw new Error('Access grant belongs to another request');
 const old=previous.grant;
 if(old&&(body.principalId!==old.principalId||body.accountIdentity!==old.accountIdentity))throw new Error('Approved local account mapping changed');
 const floor=previous.highestSequence||0;
 if(body.sequence<floor||(body.sequence===floor&&((previous.grantHash&&digest!==previous.grantHash)||previous.revoked)))throw new Error('Replayed or revoked access grant');
 return {grant:body,grantHash:digest,highestSequence:body.sequence,revoked:false};
}
function verifyStatus(value,trustValue,identity,accepted,now=Date.now()){
 const trust=descriptor(trustValue),{body,digest}=envelope(value,trust);exact(body,STATUS);scope(body,trust,identity,now);
 if(body.purpose!=='melange-desktop-access-status'||typeof body.revoked!=='boolean')throw new Error('Unsupported access status');
 for(const key of ['serverId','grantId','principalId','accountIdentity','appId','deviceId','platform','arch','role','districtIds'])if(canonical(body[key])!==canonical(accepted.grant[key]))throw new Error('Status belongs to another approved account');
 if(body.sequence<accepted.highestSequence||(body.sequence===accepted.highestSequence&&accepted.statusHash&&body.revoked!==accepted.revoked)||body.revoked&&body.sequence<=accepted.grant.sequence)throw new Error('Replayed or conflicting access status');
 if(accepted.revoked&&!body.revoked)throw new Error('Learned revocation cannot be cleared by status');
 return {...accepted,highestSequence:body.sequence,revoked:accepted.revoked||body.revoked,statusHash:digest};
}
// A fixed reason code is the only failure detail that may leave the main process.
function fail(message,reason){const error=new Error(message);error.reason=reason;return error;}
const REASONS=new Set(['unreachable','timeout','refused','identity','trust','invite']);
function accessFailure(error){return 'access:'+(REASONS.has(error?.reason)?error.reason:'failed');}
function accessRequest(trustValue,relative,{method='GET',authorization,body,deadlineMs=15000}={}){
 const trust=descriptor(trustValue),origin=new URL(trust.url);
 if(!/^\/v1\/access\/(requests(?:\/[0-9a-f-]{36})?|activate|status|presence|events|update-enrollment)$/.test(relative))return Promise.reject(new Error('Unsupported access operation'));
 if(!Number.isInteger(deadlineMs)||deadlineMs<50||deadlineMs>15000)return Promise.reject(new Error('Invalid access deadline'));
 const data=body===undefined?null:Buffer.from(JSON.stringify(body));if(data&&data.length>(relative.endsWith('/events')?65536:16384))return Promise.reject(new Error('Access request too large'));
 if(authorization&&!/^(Request|Bearer) [A-Za-z0-9_-]{43,86}$/.test(authorization))return Promise.reject(new Error('Invalid access authorization'));
 return new Promise((resolve,reject)=>{
  const agent=new https.Agent({keepAlive:false});let request,response,socket,settled=false;
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(deadline);response?.destroy();request?.destroy();socket?.destroy();agent.destroy();error?reject(error):resolve(value);};
  const deadline=setTimeout(()=>finish(fail('Access request deadline exceeded','timeout')),deadlineMs);
  agent.createConnection=(_options,callback)=>{
   let completed=false;const done=(error,connection)=>{if(!completed){completed=true;callback(error,connection);}};
   socket=tls.connect({host:origin.hostname,port:Number(origin.port)||443,...(!net.isIP(origin.hostname)?{servername:origin.hostname}:{}),rejectUnauthorized:false});
   socket.once('error',()=>{done(fail('Access connection unavailable','unreachable'));finish(fail('Access connection unavailable','unreachable'));});
   socket.once('secureConnect',()=>{try{
    const certificate=new crypto.X509Certificate(socket.getPeerCertificate(true).raw);
    const pin=crypto.createHash('sha256').update(certificate.publicKey.export({format:'der',type:'spki'})).digest();
    if(Date.now()<Date.parse(certificate.validFrom)||Date.now()>Date.parse(certificate.validTo)||!crypto.timingSafeEqual(pin,Buffer.from(trust.tlsSpkiSha256,'hex')))throw new Error('Access server identity mismatch');
    done(null,socket);
   }catch{done(fail('Access server identity mismatch','identity'));finish(fail('Access server identity mismatch','identity'));}});
  };
  const headers={Accept:'application/json',...(authorization?{Authorization:authorization}:{}),...(data?{'Content-Type':'application/json','Content-Length':data.length}:{})};
  request=https.request(new URL(relative,origin),{method,headers,agent},incoming=>{
   response=incoming;
   if(![200,201].includes(response.statusCode)){
    const error=fail('Access service refused the request','refused');error.statusCode=response.statusCode;
    if(response.statusCode!==403){finish(error);return;}
    // Only a 403 body is read, and only to tell the fixed invite_invalid answer from any other refusal; anything unreadable stays 'refused'.
    let size=0;const chunks=[];
    response.on('data',part=>{size+=part.length;if(size>16384)finish(error);else chunks.push(part);});
    response.once('end',()=>{try{if(JSON.parse(Buffer.concat(chunks).toString('utf8'))?.error==='invite_invalid')error.reason='invite';}catch{/* not JSON: plain refusal */}finish(error);});
    response.once('aborted',()=>finish(error));response.once('error',()=>finish(error));response.once('close',()=>finish(error));
    return;
   }
   let size=0,ended=false;const chunks=[];
   response.on('data',part=>{size+=part.length;if(size>16384)finish(new Error('Access response too large'));else chunks.push(part);});
   response.once('aborted',()=>finish(new Error('Access response interrupted')));
   response.once('error',()=>finish(new Error('Access response unavailable')));
   response.once('close',()=>{if(!ended)finish(new Error('Access response interrupted'));});
   response.once('end',()=>{ended=true;try{finish(null,JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{finish(new Error('Invalid access response'));}});
  });
  request.once('error',()=>finish(fail('Access connection unavailable','unreachable')));request.end(data);
 });
}
class AccessClient{
 constructor({trust,identity,store,secrets,request=accessRequest,now=Date.now}){this.trust=descriptor(trust);exact(identity,['appId','deviceId','platform','arch']);target(identity);this.identity=structuredClone(identity);this.store=store;this.secrets=secrets;this.request=request;this.now=now;this.busy=false;}
 async state(){const value=await this.store.read();if(!value)return {schemaVersion:1,identity:this.identity,trustHash:trustIdentityHash(this.trust)};if(value.schemaVersion!==1||canonical(value.identity)!==canonical(this.identity)||value.trustHash!==trustIdentityHash(this.trust))throw new Error('Protected access state belongs to another device or server');return value;}
 async operation(action){if(this.busy)throw new Error('An access operation is already in progress');this.busy=true;try{return await action();}finally{this.busy=false;}}
 async requestAccess(displayName,{reapproval=false,newRequest=false,inviteCode}={}){return this.operation(async()=>{
  if(typeof displayName!=='string'||Array.from(displayName).length<1||Array.from(displayName).length>80||/[\x00-\x1f\x7f]/.test(displayName))throw new Error('Invalid device display name');
  const code=inviteCode===undefined?'':typeof inviteCode==='string'?inviteCode.trim():null;if(code===null||code.length>64)throw new Error('Invalid invite code');
  const state=await this.state();if(state.grantEnvelope&&reapproval!==true)throw new Error('Explicit administrator reapproval is required to replace an access grant');
  const materials=requestMaterials(await this.secrets.read('access-request'));let pending=state.pending,material=materials[pending?.requestId];
  if(pending&&!material)throw new Error('Protected request recovery material unavailable');
  if(pending&&newRequest){
   // A fresh explicit action rechecks authoritative terminal status. Never replace
   // an approved request or unexpired activation retry using a stale UI snapshot.
   const reply=requestStatus(await this.request(this.trust,'/v1/access/requests/'+pending.requestId,{authorization:'Request '+material.requestSecret}),pending.requestId);
   const accepted=await this.accepted(state);
   const expiredRecovery=reply.status==='used'&&reply.expiresAt<=Math.floor(this.now()/1000)&&accepted?.grant.requestId!==pending.requestId;
   if(!['denied','expired'].includes(reply.status)&&!expiredRecovery)throw new Error('Current request recovery must be preserved');
   pending=null;
  }
  if(!pending){
   const previous=state.pending?.requestId&&materials[state.pending.requestId];
   pending={requestId:crypto.randomUUID(),displayName};material={requestId:pending.requestId,requestSecret:crypto.randomBytes(32).toString('base64url'),...(code?{inviteCode:code}:{})};
   const protectedRecords={...(previous?{[previous.requestId]:previous}:{}),[pending.requestId]:material};
   // Old material remains usable if the durable state commit fails.
   await this.secrets.write('access-request',{requests:protectedRecords});
   await this.store.write({...state,pending});
   try{await this.secrets.write('access-request',{requests:{[pending.requestId]:material}});}catch{/* Redundant protected recovery is harmless and bounded. */}
  }
  return this.submit(pending,material);
 });}
 // The invite code is optional and lives only in the protected request material (never the state record) so the poll re-POST can resend it; without one, no inviteCode field is sent.
 async submit(pending,material){
  try{return requestStatus(await this.request(this.trust,'/v1/access/requests',{method:'POST',body:{...this.identity,...pending,requestSecret:material.requestSecret,...(material.inviteCode?{inviteCode:material.inviteCode}:{})}}),pending.requestId);}
  catch(error){if(error?.reason==='invite')await this.discard(pending.requestId);throw error;}
 }
 // A refused code can never succeed for this request, so drop it (state first: material without state would strand the request) and let the user start over.
 async discard(requestId){try{const state=await this.state();if(state.pending?.requestId===requestId){await this.store.write({...state,pending:null});await this.secrets.delete('access-request');}}catch{/* the invite refusal is still reported; a leftover request only costs one more refusal */}}
 async poll(){return this.operation(async()=>{const state=await this.state(),material=requestMaterials(await this.secrets.read('access-request'))[state.pending?.requestId];if(!state.pending||!material)throw new Error('No pending request');if(this.lastPoll&&this.now()-this.lastPoll<15000)throw new Error('Wait before checking approval again');this.lastPoll=this.now();
  // 403: the server never stored this id (the first POST was lost). Re-send the same id and secret; the server treats that as idempotent.
  try{return requestStatus(await this.request(this.trust,'/v1/access/requests/'+state.pending.requestId,{authorization:'Request '+material.requestSecret}),state.pending.requestId);}catch(error){if(error.statusCode!==403)throw error;return this.submit(state.pending,material);}});}
 async activate(activationKey){return this.operation(async()=>{
  if(!secret(activationKey))throw new Error('Invalid activation key');
  const state=await this.state(),materials=requestMaterials(await this.secrets.read('access-request')),material=materials[state.pending?.requestId];
  if(!state.pending||!material||material.requestId!==state.pending.requestId||!secret(material.requestSecret))throw new Error('No pending request');
  await this.secrets.write('access-request',{requests:{...materials,[material.requestId]:{...material,activationKey}}});
  const reply=await this.request(this.trust,'/v1/access/activate',{method:'POST',authorization:'Request '+material.requestSecret,body:{requestId:state.pending.requestId,...this.identity,activationKey}});
  exact(reply,['grant','accessToken']);
  if(!secret(reply.accessToken)||Buffer.from(reply.accessToken,'base64url').length!==48)throw new Error('Invalid access credential');
  const previous=await this.accepted(state);
  const accepted=verifyGrant(reply.grant,this.trust,{...this.identity,requestId:state.pending.requestId},previous||{},this.now());
  const stored=await this.secrets.read('access-token');
  const tokens={};
  if(previous&&stored?.tokens&&secret(stored.tokens[previous.grant.grantId]))tokens[previous.grant.grantId]=stored.tokens[previous.grant.grantId];
  tokens[accepted.grant.grantId]=reply.accessToken;
  // Keep the old grant's separately keyed token until the state commit succeeds.
  // A failed write can be retried with the same activation key across restart.
  await this.secrets.write('access-token',{tokens});
  await this.store.write({...state,pending:null,grantEnvelope:reply.grant,statusEnvelope:null,highestSequence:accepted.highestSequence,revoked:false});
  let requestCleanupPending=false;
  try{
   await this.secrets.write('access-token',{tokens:{[accepted.grant.grantId]:reply.accessToken}});
   await this.secrets.delete('access-request');
  }catch{requestCleanupPending=true;}
  // The approval is durable even if removing redundant retry material failed.
  return {grant:accepted.grant,requestCleanupPending};
 });}
 async accepted(state){state=state||await this.state();if(!state.grantEnvelope)return null;let accepted=verifyGrant(state.grantEnvelope,this.trust,this.identity,{},this.now());if(state.statusEnvelope)accepted=verifyStatus(state.statusEnvelope,this.trust,this.identity,accepted,this.now());if(state.highestSequence!==accepted.highestSequence||state.revoked!==accepted.revoked)throw new Error('Protected access ledger disagrees with signed evidence');return accepted;}
 async refresh(){return this.operation(async()=>{const state=await this.state(),accepted=await this.accepted(state);if(!accepted)throw new Error('Administrator approval required');const stored=await this.secrets.read('access-token'),token=stored?.tokens?.[accepted.grant.grantId];if(!secret(token))throw new Error('Protected credential for this access grant is missing');const signed=await this.request(this.trust,'/v1/access/status',{authorization:'Bearer '+token});const checked=verifyStatus(signed,this.trust,this.identity,accepted,this.now());await this.store.write({...state,statusEnvelope:signed,highestSequence:checked.highestSequence,revoked:checked.revoked});return {revoked:checked.revoked,grant:checked.grant};});}
 async permitted(districtId=null){const value=await this.accepted();return Boolean(value&&!value.revoked&&value.grant.offlineAllowed&&(value.grant.appId==='terrier'?value.grant.districtIds.includes(districtId):districtId===null));}
 async enrollUpdates(version){return this.operation(async()=>{
  if(typeof version!=='string'||!(/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/).test(version)||version!==semver.valid(version))throw new Error('Invalid application version');
  const accepted=await this.accepted();if(!accepted||accepted.revoked)throw new Error('Active administrator approval required');
  const token=(await this.secrets.read('access-token'))?.tokens?.[accepted.grant.grantId];if(!secret(token))throw new Error('Protected credential for this access grant is missing');
  const reply=await this.request(this.trust,'/v1/access/update-enrollment',{method:'POST',authorization:'Bearer '+token,body:{currentVersion:version}});
  exact(reply,['deviceToken','deviceId','appId','trust']);
  const expected={schemaVersion:1,url:this.trust.url,appId:this.identity.appId,tlsSpkiSha256:this.trust.tlsSpkiSha256,publicKeys:this.trust.updatePublicKeysByApp[this.identity.appId]};
  exact(reply.trust,['schemaVersion','url','appId','tlsSpkiSha256','publicKeys']);keys(reply.trust.publicKeys,8,false);
  const preservedKeys=Object.entries(expected.publicKeys).every(([id,key])=>Object.hasOwn(reply.trust.publicKeys,id)&&reply.trust.publicKeys[id]===key);
  if(reply.appId!==this.identity.appId||reply.deviceId!==this.identity.deviceId||!secret(reply.deviceToken)||Buffer.from(reply.deviceToken,'base64url').length!==48||reply.deviceToken===token||!preservedKeys||canonical({...reply.trust,publicKeys:expected.publicKeys})!==canonical(expected))throw new Error('Update enrollment differs from approved installation trust');
  // Extra server keys are not trusted here. Persist only independently pinned
  // roots; the updater must verify signed key transitions before trusting more.
  return {...structuredClone(expected),appId:this.identity.appId,deviceId:this.identity.deviceId,deviceToken:reply.deviceToken};
 });}
 async reportActivity(events){return this.accessWrite('/v1/access/events',{events});}
 async presence(value){return this.accessWrite('/v1/access/presence',value);}
 async accessWrite(route,body){
  if(!['/v1/access/events','/v1/access/presence'].includes(route))throw new Error('Unsupported activity operation');
  const accepted=await this.accepted();if(!accepted||accepted.revoked)throw new Error('Active administrator approval required');
  const token=(await this.secrets.read('access-token'))?.tokens?.[accepted.grant.grantId];if(!secret(token))throw new Error('Protected credential for this access grant is missing');
  return this.request(this.trust,route,{method:'POST',authorization:'Bearer '+token,body});
 }
}
module.exports={AccessClient,accessRequest,accessFailure,descriptor,verifyGrant,verifyStatus,canonical,trustIdentityHash,refreshDescriptor,verifyAccessEnvelope};
