'use strict';
const crypto = require('node:crypto');
const https = require('node:https');
const tls = require('node:tls');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const {pipeline} = require('node:stream/promises');
const {Transform} = require('node:stream');
const {appDefinition} = require('./apps.cjs');
const semver = require('semver');
const MAX_JSON=1024*1024;
const VERSION=/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/;
function lanAddress(host) {
  const p=host.split('.');
  if(p.length!==4 || p.some(x=>!/^\d{1,3}$/.test(x)||Number(x)>255||String(Number(x))!==x)) return false;
  const [a,b]=p.map(Number);
  return a===10 || a===127 || (a===172&&b>=16&&b<=31) || (a===192&&b===168);
}
// Only an administrator-supplied descriptor/pairing chooses this endpoint. DNS
// locates the server; the pinned TLS public key still authenticates every request.
function trustedOrigin(value) {
  if(typeof value!=='string'||value.length>2048)throw new Error('A plain HTTPS server origin is required');
  const match=value.match(/^https:\/\/([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([1-9][0-9]{0,4}))?\/?$/i);
  if(!match)throw new Error('A plain HTTPS server origin is required');
  const host=match[1].toLowerCase(),port=match[2]?Number(match[2]):443;
  if(port>65535||host.length>253)throw new Error('Invalid HTTPS server origin');
  if(/^[0-9.]+$/.test(host)){
    const first=Number(host.split('.')[0]);
    if(net.isIP(host)!==4||host==='0.0.0.0'||(first>=224&&first<=239))throw new Error('A canonical IPv4 server address is required');
  }else if(!host.includes('.')||!/[a-z]/.test(host.split('.').at(-1))||host.split('.').some(label=>!/^([a-z0-9]|[a-z0-9][a-z0-9-]{0,61}[a-z0-9])$/.test(label))){
    throw new Error('A fully qualified server hostname is required');
  }
  const origin=new URL(value).origin;
  if(new URL(origin).hostname!==host)throw new Error('Noncanonical server address');
  return origin;
}
function pairing(encoded,appId) {
  if(typeof encoded!=='string'||encoded.length>32768) throw new Error('Invalid pairing bundle');
  const p=JSON.parse(Buffer.from(encoded.trim(),'base64url').toString('utf8'));
  appDefinition(appId);
  const origin=trustedOrigin(p.url);
  if(p.schemaVersion!==1||p.appId!==appId) throw new Error('Pairing must name this app and a pinned HTTPS origin');
  if(typeof p.code!=='string'||p.code.length<16||p.code.length>512) throw new Error('Invalid enrollment code');
  if(typeof p.tlsSpkiSha256!=='string'||!/^([a-fA-F0-9]{64}|[A-Za-z0-9+/]{43}=)$/.test(p.tlsSpkiSha256)) throw new Error('Invalid TLS key pin');
  if(!p.publicKeys||typeof p.publicKeys!=='object'||Object.keys(p.publicKeys).length<1||Object.keys(p.publicKeys).length>8) throw new Error('Missing administrator public keys');
  for(const [id,key] of Object.entries(p.publicKeys)) if(!/^[a-zA-Z0-9._-]{1,100}$/.test(id)||typeof key!=='string'||Buffer.from(key,'base64').length!==32) throw new Error('Invalid administrator key');
  return {...p,url:origin};
}
function pinnedAgent(p) {
  const origin=new URL(trustedOrigin(p.url));
  const agent=new https.Agent({keepAlive:false});
  agent.createConnection=function(options,callback) {
    let called=false;
    const finish=(error,socket)=>{if(!called){called=true;callback(error,socket);}};
    const socket=tls.connect({host:origin.hostname,port:Number(origin.port)||443,...(!net.isIP(origin.hostname)?{servername:origin.hostname}:{}),rejectUnauthorized:false});
    socket.setTimeout(15000,()=>socket.destroy(new Error('Publisher connection timed out')));
    socket.once('error',error=>finish(error));
    socket.once('secureConnect',()=>{
      try {
        const cert=new crypto.X509Certificate(socket.getPeerCertificate(true).raw);
        if(Date.now()<Date.parse(cert.validFrom)||Date.now()>Date.parse(cert.validTo)) throw new Error('Publisher TLS certificate is not currently valid');
        const digest=crypto.createHash('sha256').update(cert.publicKey.export({format:'der',type:'spki'})).digest();
        const expected=Buffer.from(p.tlsSpkiSha256,/^[a-fA-F0-9]{64}$/.test(p.tlsSpkiSha256)?'hex':'base64');
        if(expected.length!==digest.length||!crypto.timingSafeEqual(expected,digest)) throw new Error('Publisher TLS identity changed');
        finish(null,socket);
      }catch(error){socket.destroy();finish(error);}
    });
  };
  return agent;
}
function response(p,relative,{method='GET',token,body}={}) {
  const origin=new URL(p.url),url=new URL(relative,origin);
  if(url.origin!==origin.origin||!relative.startsWith('/v1/')) throw new Error('Publisher request escaped trusted origin');
  const bytes=body===undefined?undefined:Buffer.from(JSON.stringify(body));
  return new Promise((resolve,reject)=>{
    const headers={Accept:'application/json'};
    if(token) headers.Authorization='Bearer '+token;
    if(bytes){headers['Content-Type']='application/json';headers['Content-Length']=bytes.length;}
    const request=https.request(url,{method,headers,agent:pinnedAgent(p)},res=>{
      if(res.statusCode>=300&&res.statusCode<400){res.resume();reject(new Error('Publisher redirects are refused'));return;}
      resolve(res);
    });
    request.setTimeout(15000,()=>request.destroy(new Error('Publisher timed out')));
    request.once('error',reject);request.end(bytes);
  });
}
async function jsonRequest(p,relative,options) {
  const res=await response(p,relative,options);
  if(res.statusCode===204){res.resume();return null;}
  let size=0;const chunks=[];
  for await(const chunk of res){size+=chunk.length;if(size>MAX_JSON){res.destroy();throw new Error('Publisher reply too large');}chunks.push(chunk);}
  if(res.statusCode!==200&&res.statusCode!==201) throw new Error('Publisher refused request ('+res.statusCode+')');
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
function verifyEnvelope(envelope,p,identity,previous={}) {
  if(!envelope||envelope.schemaVersion!==1||typeof envelope.payload!=='string'||envelope.payload.length>MAX_JSON*2||typeof envelope.signature!=='string') throw new Error('Invalid signed update');
  const raw=p.publicKeys[envelope.keyId];if(!raw) throw new Error('Untrusted update signing key');
  const payload=Buffer.from(envelope.payload,'base64');
  const key=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),Buffer.from(raw,'base64')]),format:'der',type:'spki'});
  if(!crypto.verify(null,payload,key,Buffer.from(envelope.signature,'base64'))) throw new Error('Invalid update signature');
  const m=JSON.parse(payload.toString('utf8')), digest=crypto.createHash('sha256').update(payload).digest('hex');
  if(p.source==='github')require('./github-source.cjs').githubPolicy(m,p.repository);
  if(m.schemaVersion!==1||m.appId!==identity.appId||m.platform!==identity.platform||m.arch!==identity.arch) throw new Error('Update belongs to a different app or platform');
  if(!VERSION.test(m.version)||!Number.isSafeInteger(m.sequence)||m.sequence<1||m.minimumUpdaterVersion!=='1.0.0'||m.dataSchemaVersion!==1) throw new Error('Unsupported update metadata');
  if(!semver.valid(m.version)||(identity.version&&(!semver.valid(identity.version)||semver.lt(m.version,identity.version))))throw new Error('Application version downgrade refused');
  if(previous.acceptedVersion&&(!semver.valid(previous.acceptedVersion)||semver.lt(m.version,previous.acceptedVersion)))throw new Error('Previously approved version downgrade refused');
  if(!Number.isFinite(Date.parse(m.issuedAt))||Date.parse(m.issuedAt)>Date.now()+300000) throw new Error('Invalid update timestamp');
  if(m.sequence<(previous.sequence||0)||(m.sequence===previous.sequence&&previous.manifestHash!==digest)) throw new Error('Rollback or equivocation refused');
  const pkg=m.package;
  if(!pkg||!/^[-A-Za-z0-9_.]+$/.test(pkg.name)||pkg.name==='.'||pkg.name==='..'||!Number.isSafeInteger(pkg.size)||pkg.size<=0||pkg.size>20*1024**3||!/^[a-f0-9]{64}$/.test(pkg.sha256)||Buffer.from(pkg.sha512||'','base64').length!==64) throw new Error('Invalid update package');
  if(pkg.path!=='/v1/packages/'+m.appId+'/'+m.sequence+'/'+pkg.name) throw new Error('Update package path mismatch');
  if(identity.platform==='win32'&&!pkg.name.endsWith('.exe')) throw new Error('Windows updates require an NSIS installer');
  if(identity.platform==='darwin'&&!pkg.name.endsWith('.zip')) throw new Error('Mac updates require a signed application zip');
  return {manifest:m,manifestHash:digest};
}
function applyKeyTransitions(enrollment,transitions) {
  if(!Array.isArray(transitions)||transitions.length>100) throw new Error('Invalid key transition list');
  const updated={...enrollment,publicKeys:{...enrollment.publicKeys}};
  for(const envelope of transitions){
    if(!envelope||envelope.schemaVersion!==1||typeof envelope.payload!=='string'||envelope.payload.length>32768)throw new Error('Invalid key transition');
    const publicKey=updated.publicKeys[envelope.keyId];if(!publicKey)throw new Error('Key transition signer is not trusted');
    const payload=Buffer.from(envelope.payload,'base64');
    const key=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),Buffer.from(publicKey,'base64')]),format:'der',type:'spki'});
    if(!crypto.verify(null,payload,key,Buffer.from(envelope.signature||'','base64')))throw new Error('Invalid key transition signature');
    const transition=JSON.parse(payload.toString('utf8')),raw=Buffer.from(transition.publicKey||'','base64');
    if(transition.schemaVersion!==1||transition.kind!=='keyTransition'||transition.appId!==updated.appId||transition.fromKeyId!==envelope.keyId||raw.length!==32||transition.toKeyId!==crypto.createHash('sha256').update(raw).digest('hex').slice(0,24)||!Number.isSafeInteger(transition.sequence)||transition.sequence<1)throw new Error('Invalid key transition identity');
    if(transition.sequence<=(updated.keySequence||0)){
      if(updated.publicKeys[transition.toKeyId]!==transition.publicKey)throw new Error('Key transition rollback refused');
      continue;
    }
    if(transition.sequence!==(updated.keySequence||0)+1)throw new Error('Key transition sequence gap');
    if(!Number.isFinite(Date.parse(transition.issuedAt))||Date.parse(transition.issuedAt)>Date.now()+300000)throw new Error('Invalid key transition timestamp');
    updated.publicKeys[transition.toKeyId]=transition.publicKey;updated.keySequence=transition.sequence;
  }
  return updated;
}
async function downloadVerified(p,token,manifest,directory) {
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const destination=path.join(directory,manifest.package.name),tmp=destination+'.'+crypto.randomUUID()+'.part';
  const sha256=crypto.createHash('sha256'),sha512=crypto.createHash('sha512');let count=0;
  try {
    const res=await response(p,manifest.package.path,{token});
    if(res.statusCode!==200){res.resume();throw new Error('Package download refused');}
    const check=new Transform({transform(chunk,encoding,callback){count+=chunk.length;if(count>manifest.package.size)return callback(new Error('Package larger than signed size'));sha256.update(chunk);sha512.update(chunk);callback(null,chunk);}});
    await pipeline(res,check,fs.createWriteStream(tmp,{flags:'wx',mode:0o600}));
    if(count!==manifest.package.size||sha256.digest('hex')!==manifest.package.sha256||sha512.digest('base64')!==manifest.package.sha512) throw new Error('Downloaded package integrity failed');
    fs.renameSync(tmp,destination);return destination;
  }catch(error){try{fs.unlinkSync(tmp);}catch{}throw error;}
}
async function verifyPackageFile(filename, manifest) {
  const stat=fs.lstatSync(filename);
  if(!stat.isFile()||stat.isSymbolicLink()||stat.size!==manifest.package.size) throw Object.assign(new Error('Cached package size or type changed'),{code:'PACKAGE_INTEGRITY'});
  const sha256=crypto.createHash('sha256'),sha512=crypto.createHash('sha512');
  for await(const chunk of fs.createReadStream(filename)){sha256.update(chunk);sha512.update(chunk);}
  if(sha256.digest('hex')!==manifest.package.sha256||sha512.digest('base64')!==manifest.package.sha512)throw Object.assign(new Error('Cached package integrity failed'),{code:'PACKAGE_INTEGRITY'});
  return filename;
}
module.exports={lanAddress,trustedOrigin,pairing,jsonRequest,verifyEnvelope,applyKeyTransitions,downloadVerified,verifyPackageFile};
