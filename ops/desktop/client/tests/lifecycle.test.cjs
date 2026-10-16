'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {DesktopUpdater}=require('../updater.cjs');
const {watchNetwork}=require('../lifecycle.cjs');
const {verifyEnvelope}=require('../trust.cjs');
const {activity}=require('../runtime.cjs');
const http=require('node:http');

function fixture(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-lifecycle-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const keys=crypto.generateKeyPairSync('ed25519');
  const enrollment={appId:'terrier',deviceToken:'fixture-only',publicKeys:{admin:keys.publicKey.export({format:'der',type:'spki'}).subarray(-32).toString('base64')}};
  const bytes=Buffer.from('Non-installable signed test fixture');
  let state={},offline=false,version='1.0.0',downloads=0,installs=0,envelope,status=[],nativeValid=true;
  const sign=(release='1.1.0',sequence=1)=>{
    sequence+=1; // Sequence one is the already approved initial installer.
    const manifest={schemaVersion:1,appId:'terrier',version:release,sequence,platform:'win32',arch:'x64',minimumUpdaterVersion:'1.0.0',dataSchemaVersion:1,issuedAt:new Date().toISOString(),package:{name:'Terrier.exe',path:'/v1/packages/terrier/'+sequence+'/Terrier.exe',size:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),sha512:crypto.createHash('sha512').update(bytes).digest('base64')}};
    const payload=Buffer.from(JSON.stringify(manifest));return {schemaVersion:1,keyId:'admin',payload:payload.toString('base64'),signature:crypto.sign(null,payload,keys.privateKey).toString('base64')};
  };
  envelope=sign();
  let seed=sign('1.0.0',0);
  fs.mkdirSync(path.join(directory,'updates','1'),{recursive:true});fs.writeFileSync(path.join(directory,'updates','1','Terrier.exe'),bytes);
  state={installed:{envelope:seed,version:'1.0.0',phase:'installed'},sequence:1,manifestHash:crypto.createHash('sha256').update(Buffer.from(seed.payload,'base64')).digest('hex'),acceptedVersion:'1.0.0'};
  const clients=[];
  function client(publisherName='Fixture Publisher'){
    let staged;
    const native={configOnDisk:{value:Promise.resolve({publisherName:'Fixture Publisher'})},setFeedURL(){},async checkForUpdates(){const active=state.recovery&&['staged','prepared'].includes(state.recovery.phase)?state.recovery.target.envelope:state.pending?.envelope||envelope;const m=JSON.parse(Buffer.from(active.payload,'base64'));return {updateInfo:{version:m.version,files:[m.package]}};},async downloadUpdate(){staged=path.join(directory,'native.exe');fs.writeFileSync(staged,bytes);return [staged];},quitAndInstall(){assert.ok(state.pending?.phase==='activating'||state.recovery?.phase==='activating','Transaction must be durable before native activation');installs++;}};
    const updater=new DesktopUpdater({appId:'terrier',version,userData:directory,platform:'win32',arch:'x64',publisherName,packaged:true,autoUpdater:native,loadEnrollment:()=>enrollment,saveEnrollment(){},readState:()=>structuredClone(state),writeState:value=>{state=structuredClone(value);},onStatus:message=>status.push(message),verifyNativePackage:async()=>{if(!nativeValid)throw new Error('Native signature refused');},makeFeed:async()=>({url:'http://127.0.0.1/fixture/',close(){}}),request:async(p,url,options)=>{if(offline)throw new Error('offline');if(url.endsWith('/trust'))return {transitions:[]};if(url.includes('/recovery?'))return seed;if(url==='/v1/status'){assert.match(options.body.errorCode||'',/^[A-Z0-9_]*$/);return {};}return envelope;},download:async(p,token,m,target)=>{downloads++;fs.mkdirSync(target,{recursive:true});const destination=path.join(target,m.package.name);fs.writeFileSync(destination,bytes);return destination;}});
    clients.push(updater);return updater;
  }
  t.after(()=>clients.forEach(c=>c.close()));
  return {client,sign,enrollment,get state(){return state;},set state(v){state=v;},set seed(v){seed=v;},set nativeValid(v){nativeValid=v;},set offline(v){offline=v;},set version(v){version=v;},set envelope(v){envelope=v;},get envelope(){return envelope;},get downloads(){return downloads;},get installs(){return installs;},get status(){return status;},directory};
}

test('download never arms quit hooks; explicit close persists transaction before native install',async t=>{
  const f=fixture(t),client=f.client();await client.check();
  assert.equal(client.autoUpdater.autoInstallOnAppQuit,false);assert.equal(f.installs,0);assert.equal(f.state.pending.phase,'prepared');
  assert.equal(await client.activateAfterClose(),true);assert.equal(f.installs,1);
});

test('first update retains approved current installer before activation and enables first rollback',async t=>{
  const f=fixture(t);f.state={};const first=f.client();await first.check();
  assert.equal(f.state.installed.version,'1.0.0');assert.equal(f.downloads,2);assert.equal(f.state.sequence,2);
  await first.activateAfterClose();f.version='1.1.0';const upgraded=f.client();upgraded.markApplicationReady();await upgraded.check();
  assert.equal(upgraded.recoveryCandidate().version,'1.0.0');assert.equal(f.state.previous.version,'1.0.0');
});

test('missing initial recovery approval defers first update without activation',async t=>{
  const f=fixture(t);f.state={};f.seed=null;const first=f.client();await first.check();
  assert.equal(f.downloads,0);assert.equal(f.state.installed,undefined);assert.equal(await first.activateAfterClose(),false);
  assert.match(f.status.at(-1),/recovery installer/);
});

test('forged or wrong-version recovery seed cannot establish first-update trust',async t=>{
  const f=fixture(t);f.state={};f.seed={...f.sign('1.0.0',0),signature:Buffer.alloc(64).toString('base64')};const first=f.client();await first.check();
  assert.equal(f.state.installed,undefined);assert.equal(f.downloads,0);assert.equal(await first.activateAfterClose(),false);
  f.seed=f.sign('1.1.0',1);await first.check();assert.equal(f.state.installed,undefined);assert.equal(f.downloads,0);
});

test('native signature failure prevents seed retention and later package activation',async t=>{
  const f=fixture(t);f.state={};f.nativeValid=false;const first=f.client();await first.check();
  assert.equal(f.state.installed,undefined);assert.equal(await first.activateAfterClose(),false);
  f.nativeValid=true;await first.check();f.nativeValid=false;assert.equal(await first.activateAfterClose(),false);assert.equal(f.installs,0);
});

test('older current-version recovery seed never lowers accepted release high-water',async t=>{
  const f=fixture(t);f.state={sequence:9,acceptedVersion:'9.0.0',manifestHash:'retained-high-water'};const first=f.client();await first.check();
  assert.equal(f.state.installed.version,'1.0.0');assert.equal(f.state.sequence,9);assert.equal(f.state.acceptedVersion,'9.0.0');assert.equal(f.state.manifestHash,'retained-high-water');assert.equal(await first.activateAfterClose(),false);
});

test('OS shutdown leaves staged update uninstalled and restores offline after next login',async t=>{
  const f=fixture(t),first=f.client();await first.check();first.suspendInstallation();
  assert.equal(await first.activateAfterClose(),false);assert.equal(f.installs,0);
  f.offline=true;const next=f.client();await next.check();
  assert.equal(f.downloads,1);assert.equal(next.ready.manifest.version,'1.1.0');
  assert.equal(await next.activateAfterClose(),true);assert.equal(f.installs,1);
});

test('altered staged bytes cannot be restored or installed offline',async t=>{
  const f=fixture(t),client=f.client();await client.check();
  fs.writeFileSync(path.join(f.directory,'updates','2','Terrier.exe'),'tampered');
  assert.equal(await client.activateAfterClose(),false);assert.equal(f.installs,0);
  f.offline=true;const next=f.client();await next.check();assert.equal(next.ready,null);assert.equal(await next.activateAfterClose(),false);
});

test('missing pinned native publisher fails before downloading or arming updates',async t=>{
  const f=fixture(t),client=f.client(null);await client.check();assert.equal(f.downloads,0);assert.equal(f.installs,0);assert.match(f.status.at(-1),/publisher/);
});

test('interrupted activation suspends repeated installation and retains recoverable metadata',async t=>{
  const f=fixture(t),client=f.client();await client.check();await client.activateAfterClose();
  const next=f.client();await next.check();assert.equal(f.state.pending.phase,'failed');assert.equal(f.installs,1);assert.equal(await next.activateAfterClose(),false);
  assert.ok(f.state.pending.envelope);assert.ok(fs.existsSync(path.join(f.directory,'updates','2','Terrier.exe')));
  f.envelope=f.sign('1.1.1',2);await next.check();assert.equal(f.state.pending.version,'1.1.1');assert.equal(f.state.pending.phase,'prepared');
});

test('installed status requires backend readiness and preserves previous authorized package',async t=>{
  const f=fixture(t),client=f.client();await client.check();await client.activateAfterClose();
  f.version='1.1.0';const next=f.client();await next.check();assert.equal(f.state.pending.version,'1.1.0');assert.equal(f.state.installed.version,'1.0.0');
  next.markApplicationReady();await next.check();assert.equal(f.state.pending,null);assert.equal(f.state.installed.version,'1.1.0');
  f.envelope=f.sign('1.2.0',2);await next.check();await next.activateAfterClose();
  f.version='1.2.0';const latest=f.client();latest.markApplicationReady();await latest.check();
  assert.equal(f.state.installed.version,'1.2.0');assert.equal(f.state.previous.version,'1.1.0');assert.ok(fs.existsSync(path.join(f.directory,'updates','2','Terrier.exe')));
});

test('higher release sequence cannot downgrade an already accepted version',t=>{
  const f=fixture(t);
  assert.throws(()=>verifyEnvelope(f.sign('1.1.0',3),f.enrollment,{appId:'terrier',platform:'win32',arch:'x64',version:'1.0.0'},{sequence:2,acceptedVersion:'1.2.0'}),/approved version downgrade/);
});

test('failed backend startup records a bounded failure and preserves previous recovery package',async t=>{
  const f=fixture(t),first=f.client();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const installed=f.client();installed.markApplicationReady();await installed.check();
  f.envelope=f.sign('1.2.0',2);await installed.check();await installed.activateAfterClose();
  f.version='1.2.0';f.offline=true;const failed=f.client();await failed.markApplicationFailed();
  assert.equal(f.state.pending.phase,'failed');assert.equal(f.state.installed.version,'1.1.0');
  assert.equal(f.state.outbox.errorCode,'APP_STARTUP_FAILED');assert.equal(f.state.outbox.status,'failed');
  await failed.check();assert.equal(f.state.pending.phase,'failed');assert.equal(failed.ready,null);
  assert.equal(failed.recoveryCandidate().version,'1.1.0');
});

test('local recovery restores only retained authorized package and preserves anti-rollback high-water',async t=>{
  const f=fixture(t),first=f.client();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const second=f.client();second.markApplicationReady();await second.check();
  f.envelope=f.sign('1.2.0',2);await second.check();await second.activateAfterClose();
  f.version='1.2.0';const third=f.client();third.markApplicationReady();await third.check();
  assert.equal(third.recoveryCandidate().version,'1.1.0');f.offline=true;
  assert.equal(await third.beginRecovery(),'1.1.0');assert.equal(f.state.sequence,3);assert.equal(f.state.acceptedVersion,'1.2.0');
  assert.equal(await third.activateAfterClose(),true);assert.equal(f.state.recovery.phase,'activating');
  f.version='1.1.0';const recovered=f.client();recovered.markApplicationReady();await recovered.check();
  assert.equal(f.state.recovery.phase,'completed');assert.equal(f.state.installed.version,'1.1.0');assert.equal(f.state.sequence,3);assert.equal(f.state.acceptedVersion,'1.2.0');assert.deepEqual(f.state.suspendedVersions,['1.2.0']);
  f.offline=false;await recovered.check();assert.equal(recovered.ready,null);assert.match(f.status.at(-1),/suspended/);
  f.envelope=f.sign('1.2.1',3);await recovered.check();assert.equal(f.state.pending.version,'1.2.1');
});

test('local recovery refuses tampered retained packages and installs nothing',async t=>{
  const f=fixture(t),first=f.client();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const second=f.client();second.markApplicationReady();await second.check();
  f.envelope=f.sign('1.2.0',2);await second.check();await second.activateAfterClose();
  f.version='1.2.0';const third=f.client();third.markApplicationReady();await third.check();
  fs.writeFileSync(path.join(f.directory,'updates','2','Terrier.exe'),'corrupt');
  await assert.rejects(()=>third.beginRecovery(),/Cached package/);assert.equal(await third.activateAfterClose(),false);assert.equal(f.installs,2);
});

test('network reconnect triggers one check without disclosing device data to discovery services',()=>{
  let online=false,calls=0,address='192.168.1.5';
  const watcher=watchNetwork({isOnline:()=>online,interfaces:()=>({WiFi:[{internal:false,family:'IPv4',address}]}),onReconnect:()=>calls++});
  try{watcher.poll();online=true;watcher.poll();watcher.poll();assert.equal(calls,1);address='192.168.2.5';watcher.poll();assert.equal(calls,2);online=false;watcher.poll();assert.equal(calls,2);}finally{watcher.close();}
});

test('activity signal requires matching app and typed boolean; unavailable is unknown',async()=>{
  let response={appId:'mentat',busy:true},code=200;
  const server=http.createServer((request,res)=>{assert.equal(request.headers['x-desktop-token'],'launch-secret');assert.equal(request.url,'/__desktop/activity');res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(response));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
  try{assert.equal(await activity(origin,'launch-secret','mentat'),true);response={appId:'mentat',busy:false};assert.equal(await activity(origin,'launch-secret','mentat'),false);response={appId:'terrier',busy:false};assert.equal(await activity(origin,'launch-secret','mentat'),null);response={appId:'mentat',busy:'false'};assert.equal(await activity(origin,'launch-secret','mentat'),null);code=404;assert.equal(await activity(origin,'launch-secret','mentat'),null);}finally{await new Promise(resolve=>server.close(resolve));}
});
