'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),https=require('node:https'),{spawnSync}=require('node:child_process');
const {datasetRequest,route}=require('../dataset-transport.cjs');
test('dataset transport pins before HTTP, bounds ranges/JSON and rejects redirects, slow drip, aborts and malformed JSON',async t=>{
 const generated=spawnSync(process.env.DESKTOP_TEST_PYTHON||'python',['-E','-s','-c',String.raw`
import datetime,json
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
k=rsa.generate_private_key(public_exponent=65537,key_size=2048)
n=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'dataset-fixture')]);now=datetime.datetime.now(datetime.timezone.utc)
c=x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(hours=1)).sign(k,hashes.SHA256())
print(json.dumps({'key':k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),'cert':c.public_bytes(serialization.Encoding.PEM).decode()}))
`],{windowsHide:true,encoding:'utf8',timeout:10000});assert.equal(generated.status,0,'TLS fixture failed');const material=JSON.parse(generated.stdout);
 let mode='json',hits=0;const sockets=new Set();
 const server=https.createServer(material,(_request,response)=>{hits++;
  if(mode==='redirect'){response.writeHead(302,{'Content-Length':0,Location:'/v1/data/catalog?after=0&limit=25'});response.end();return;}
  if(mode==='drip'){response.writeHead(200,{'Content-Type':'application/json','Content-Length':100});response.write('{');const timer=setInterval(()=>response.write(' '),20);response.on('close',()=>clearInterval(timer));return;}
  if(mode==='range'){response.writeHead(200,{'Content-Type':'application/octet-stream','Content-Length':4});response.end(Buffer.from([1,2,3,4]));return;}
  if(mode==='short-range'){response.writeHead(200,{'Content-Type':'application/octet-stream','Content-Length':3});response.end(Buffer.from([1,2,3]));return;}
  if(mode==='huge'){response.writeHead(200,{'Content-Type':'application/json','Content-Length':10*1024*1024});response.end('{}');return;}
  const text=mode==='duplicates'?' {"revisions":[],"revisions":[]} ':JSON.stringify({revisions:[],nextCursor:0,hasMore:false});response.writeHead(200,{'Content-Type':'application/json','Content-Length':Buffer.byteLength(text)});response.end(text);
 });
 server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>{for(const socket of sockets)socket.destroy();server.close();});
 const pair=crypto.generateKeyPairSync('ed25519'),raw=pair.publicKey.export({format:'der',type:'spki'}).subarray(-32),keyId=crypto.createHash('sha256').update(raw).digest('hex').slice(0,24),key=raw.toString('base64');
 const trust={schemaVersion:1,purpose:'melange-desktop-trust',serverId:crypto.randomUUID(),url:'https://127.0.0.1:'+server.address().port,tlsSpkiSha256:crypto.createHash('sha256').update(new crypto.X509Certificate(material.cert).publicKey.export({format:'der',type:'spki'})).digest('hex'),accessPublicKeys:{[keyId]:key},updatePublicKeysByApp:Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{[keyId]:key}]))},token=crypto.randomBytes(48).toString('base64url');
 const catalog='/v1/data/catalog?after=0&limit=25',range='/v1/data/objects/'+'a'.repeat(64)+'?offset=0&length=4&revisionId='+crypto.randomUUID();
 await assert.rejects(datasetRequest({...trust,tlsSpkiSha256:'b'.repeat(64)},catalog,{token}),/identity/);assert.equal(hits,0);
 assert.equal((await datasetRequest(trust,catalog,{token})).nextCursor,0);
 mode='range';assert.deepEqual(await datasetRequest(trust,range,{token}),Buffer.from([1,2,3,4]));
 mode='short-range';await assert.rejects(datasetRequest(trust,range,{token}),/framing/);
 mode='redirect';const before=hits;await assert.rejects(datasetRequest(trust,catalog,{token}),/refused/);assert.equal(hits,before+1);
 mode='duplicates';await assert.rejects(datasetRequest(trust,catalog,{token}),/Invalid dataset response/);
 mode='huge';await assert.rejects(datasetRequest(trust,catalog,{token}),/framing/);
 mode='drip';const start=Date.now();await assert.rejects(datasetRequest(trust,catalog,{token,deadlineMs:150}),/deadline/);assert.ok(Date.now()-start<1500);
 const control=new AbortController();const pending=datasetRequest(trust,catalog,{token,signal:control.signal});setTimeout(()=>control.abort(),30);await assert.rejects(pending,/canceled/);
 for(const value of ['/v1/data/catalog?after=0&limit=999','/v1/data/objects/../../secret','https://8.8.8.8/private','/v1/access/status','/v1/data/catalog?after=0&limit=1&appId=terrier'])assert.throws(()=>route(value,'GET'));
 await new Promise(resolve=>setTimeout(resolve,40));assert.equal(sockets.size,0);
});
