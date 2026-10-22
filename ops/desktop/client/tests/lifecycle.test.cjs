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
  let state={},offline=false,version='1.0.0',downloads=0,installs=0,nativeChecks=0,envelope,status=[],nativeValid=true;
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
  function client(publisherName='Fixture Publisher',extra={}){
    let staged;
    const native={configOnDisk:{value:Promise.resolve({publisherName:'Fixture Publisher'})},setFeedURL(){},async checkForUpdates(){nativeChecks++;const active=state.recovery&&['staged','prepared'].includes(state.recovery.phase)?state.recovery.target.envelope:state.pending?.envelope||envelope;const m=JSON.parse(Buffer.from(active.payload,'base64'));return {updateInfo:{version:m.version,files:[m.package]}};},async downloadUpdate(){staged=path.join(directory,'native.exe');fs.writeFileSync(staged,bytes);return [staged];},quitAndInstall(){assert.ok(state.pending?.phase==='activating'||state.recovery?.phase==='activating','Transaction must be durable before native activation');this.quitAndInstallCalled=true;installs++;}};
    const updater=new DesktopUpdater({appId:'terrier',version,userData:directory,platform:'win32',arch:'x64',publisherName,packaged:true,autoUpdater:native,loadEnrollment:()=>enrollment,saveEnrollment(){},readState:()=>structuredClone(state),writeState:value=>{state=structuredClone(value);},onStatus:message=>status.push(message),verifyNativePackage:async()=>{if(!nativeValid)throw new Error('Native signature refused');},makeFeed:async()=>({url:'http://127.0.0.1/fixture/',close(){}}),request:async(p,url,options)=>{if(offline)throw new Error('offline');if(url.endsWith('/trust'))return {transitions:[]};if(url.includes('/recovery?'))return seed;if(url==='/v1/status'){assert.match(options.body.errorCode||'',/^[A-Z0-9_]*$/);return {};}return envelope;},download:async(p,token,m,target)=>{downloads++;fs.mkdirSync(target,{recursive:true});const destination=path.join(target,m.package.name);fs.writeFileSync(destination,bytes);return destination;},...extra});
    clients.push(updater);return updater;
  }
  t.after(()=>clients.forEach(c=>c.close()));
  return {client,sign,enrollment,get state(){return state;},set state(v){state=v;},set seed(v){seed=v;},set nativeValid(v){nativeValid=v;},set offline(v){offline=v;},set version(v){version=v;},set envelope(v){envelope=v;},get envelope(){return envelope;},get downloads(){return downloads;},get installs(){return installs;},get nativeChecks(){return nativeChecks;},get status(){return status;},directory};
}

test('download never arms quit hooks; explicit close persists transaction before native install',async t=>{
  const f=fixture(t),client=f.client();await client.check();
  assert.equal(client.autoUpdater.autoInstallOnAppQuit,false);assert.equal(f.installs,0);assert.equal(f.state.pending.phase,'prepared');
  assert.equal(await client.activateAfterClose(),true);assert.equal(f.installs,1);
});

test('first update records the running version from its signed manifest without downloading its installer, and enables first rollback',async t=>{
  const f=fixture(t);f.state={};fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});const first=f.client();await first.check();
  assert.equal(f.state.installed.version,'1.0.0');assert.equal(f.downloads,1);assert.equal(f.state.sequence,2);
  assert.equal(fs.existsSync(path.join(f.directory,'updates','1','Terrier.exe')),false,'the running version is never downloaded again');
  await first.activateAfterClose();f.version='1.1.0';const upgraded=f.client();upgraded.markApplicationReady();await upgraded.check();
  assert.equal(upgraded.recoveryCandidate().version,'1.0.0');assert.equal(f.state.previous.version,'1.0.0');assert.equal(f.downloads,1);
  // Only an actual rollback request fetches the older installer, then verifies it like any activation.
  assert.equal(await upgraded.beginRecovery(),'1.0.0');assert.equal(f.downloads,2);assert.equal(f.state.recovery.phase,'prepared');
  assert.equal(f.state.sequence,2);assert.equal(f.state.acceptedVersion,'1.1.0');
  assert.equal(await upgraded.activateAfterClose(),true);assert.equal(f.installs,2);assert.equal(f.state.recovery.phase,'activating');
});

test('an on-demand rollback installer failing its hash or publisher check is refused and never activates',async t=>{
  const f=fixture(t);f.state={};fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});const first=f.client();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const tampered=f.client('Fixture Publisher',{download:async(p,token,m,target)=>{fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,m.package.name),'tampered');}});
  tampered.markApplicationReady();await tampered.check();
  await assert.rejects(()=>tampered.beginRecovery(),/Cached package/);assert.equal(f.state.recovery.phase,'failed');assert.equal(await tampered.activateAfterClose(),false);
  fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});f.nativeValid=false;const refused=f.client();refused.markApplicationReady();
  await assert.rejects(()=>refused.beginRecovery(),/Native signature refused/);assert.equal(await refused.activateAfterClose(),false);
  assert.equal(f.installs,1);assert.equal(f.state.sequence,2);assert.equal(f.state.acceptedVersion,'1.1.0');
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

test('native signature failure prevents package activation even though the signed baseline is recorded',async t=>{
  const f=fixture(t);f.state={};f.nativeValid=false;const first=f.client();await first.check();
  // The baseline is only the running version's signed manifest; no unsigned bytes can be staged from it.
  assert.equal(f.state.installed.version,'1.0.0');assert.equal(f.state.pending,null);assert.ok(f.state.verifyFailure);assert.equal(await first.activateAfterClose(),false);
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
  // Offline, the corrupt copy is deleted and nothing can replace it: refused, nothing installs.
  const offline=f.client('Fixture Publisher',{download:async()=>{throw new Error('offline');}});offline.markApplicationReady();
  await assert.rejects(()=>offline.beginRecovery(),/offline/);assert.equal(fs.existsSync(path.join(f.directory,'updates','2','Terrier.exe')),false);
  assert.equal(await offline.activateAfterClose(),false);assert.equal(f.installs,2);
  // Online, the fallback download is hash-checked against the signed manifest like any other.
  assert.equal(await third.beginRecovery(),'1.1.0');assert.equal(f.state.recovery.phase,'prepared');assert.equal(await third.activateAfterClose(),true);assert.equal(f.installs,3);
});

test('isolated test profile never downloads, stages or activates native installers',async t=>{
  const fresh=fixture(t);fresh.state={};const isolated=fresh.client('Fixture Publisher',{nativeInstallation:false});
  await assert.rejects(()=>isolated.nativeTrust(),/isolated test profile/);
  await isolated.check();
  assert.equal(fresh.downloads,0);assert.equal(fresh.nativeChecks,0);assert.equal(fresh.installs,0);assert.equal(fresh.state.sequence,undefined);assert.equal(fresh.state.pending,undefined);
  assert.match(fresh.status.at(-1),/isolated test profile/);assert.equal(await isolated.activateAfterClose(),false);
  // A previously prepared production transaction found in the same state cannot be activated or recovered either.
  const f=fixture(t);await f.client().check();assert.equal(f.state.pending.phase,'prepared');const checks=f.nativeChecks;
  const confined=f.client('Fixture Publisher',{nativeInstallation:false});await confined.check();
  assert.equal(confined.ready,null);assert.equal(await confined.activateAfterClose(),false);assert.equal(f.installs,0);assert.equal(f.nativeChecks,checks);
  await assert.rejects(()=>confined.beginRecovery());assert.equal(f.installs,0);
  // Production behavior is unchanged when the option is absent or true.
  const production=f.client('Fixture Publisher',{nativeInstallation:true});await production.check();assert.equal(await production.activateAfterClose(),true);assert.equal(f.installs,1);
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

test('release failing native verification backs off, cannot wedge a corrected release, and keeps the high-water',async t=>{
  const f=fixture(t),bad=path.join(f.directory,'updates','2')+path.sep,failing=f.envelope,codes=[];
  const client=f.client('Fixture Publisher',{verifyNativePackage:async file=>{if(path.resolve(file).startsWith(bad))throw new Error('Native signature refused');}});
  const report=client.report.bind(client);client.report=(e,s,c)=>{codes.push(s+':'+c);return report(e,s,c);};
  await client.check();
  assert.equal(f.downloads,1);assert.equal(f.state.pending,null);assert.equal(client.ready,null);assert.match(f.status.at(-1),/Native signature refused/);assert.deepEqual(codes,['failed:VERIFY_FAILED']);
  assert.equal(f.state.verifyFailure.manifestHash,crypto.createHash('sha256').update(Buffer.from(failing.payload,'base64')).digest('hex'));
  // The signed high-water is recorded before download by design; a failed candidate keeps it.
  assert.equal(f.state.sequence,2);assert.equal(f.state.acceptedVersion,'1.1.0');assert.equal(await client.activateAfterClose(),false);
  // Inside the backoff window the same manifest is neither downloaded nor verified again.
  await client.check();await client.check();assert.equal(f.downloads,1);assert.match(f.status.at(-1),/retry after/);assert.equal(codes.length,1);
  // Older, equivocating or downgrading manifests stay refused.
  for(const [offer,reason] of [[f.sign('1.1.0',0),/Rollback/],[f.sign('1.1.1',1),/Rollback|equivocation/],[f.sign('1.0.9',3),/downgrade/]]){
    f.envelope=offer;await client.check();
    assert.match(f.status.at(-1),reason);assert.equal(f.state.sequence,2);assert.equal(f.state.acceptedVersion,'1.1.0');assert.equal(f.downloads,1);assert.equal(client.ready,null);
  }
  // After the window the same manifest is retried once (fresh download), fails again and backs off again.
  f.envelope=failing;f.state={...f.state,verifyFailure:{...f.state.verifyFailure,failedAt:Date.now()-6*60*60*1000-1}};
  await client.check();assert.equal(f.downloads,2);assert.equal(f.state.pending,null);assert.ok(Date.now()-f.state.verifyFailure.failedAt<60000);
  await client.check();assert.equal(f.downloads,2);
  // A corrected release with a higher sequence bypasses the backoff immediately.
  f.envelope=f.sign('1.1.1',2);await client.check();
  assert.equal(f.downloads,3);assert.equal(f.state.sequence,3);assert.equal(f.state.pending.version,'1.1.1');assert.equal(f.state.pending.phase,'prepared');
  assert.equal(await client.activateAfterClose(),true);assert.equal(f.installs,1);
});

test('restart reverification failure is reported and not overwritten by an empty update reply',async t=>{
  const f=fixture(t);let refuse=false;const bad=path.join(f.directory,'updates','2')+path.sep;
  const verifyNativePackage=async file=>{if(refuse&&path.resolve(file).startsWith(bad))throw new Error('Native signature refused');};
  await f.client('Fixture Publisher',{verifyNativePackage}).check();assert.equal(f.state.pending.phase,'prepared');
  refuse=true;f.envelope=null;const restarted=f.client('Fixture Publisher',{verifyNativePackage}),codes=[];
  const report=restarted.report.bind(restarted);restarted.report=(e,s,c)=>{codes.push(s+':'+c);return report(e,s,c);};
  await restarted.check();
  assert.match(f.status.at(-1),/Staged update 1.1.0 .*Native signature refused/);assert.deepEqual(codes,['failed:VERIFY_FAILED']);
  assert.equal(f.state.pending,null);assert.equal(restarted.ready,null);assert.equal(await restarted.activateAfterClose(),false);
});

test('download integrity failure records the high-water but a corrected release still installs',async t=>{
  const f=fixture(t);let fail=true;
  const client=f.client('Fixture Publisher',{download:async(p,token,m,target)=>{if(fail){fail=false;throw new Error('Downloaded package integrity failed');}fs.mkdirSync(target,{recursive:true});const destination=path.join(target,m.package.name);fs.writeFileSync(destination,Buffer.from('Non-installable signed test fixture'));return destination;}});
  await client.check();
  assert.equal(f.state.sequence,2);assert.equal(f.state.pending,null);assert.match(f.status.at(-1),/integrity failed/);
  f.envelope=f.sign('1.1.0',0);await client.check();assert.match(f.status.at(-1),/Rollback/);assert.equal(f.state.sequence,2);
  f.envelope=f.sign('1.1.1',2);await client.check();
  assert.equal(f.state.sequence,3);assert.equal(f.state.pending.phase,'prepared');assert.equal(await client.activateAfterClose(),true);
});

test('failed rollback preparation is marked failed, never activates, and stops blocking updates',async t=>{
  const f=fixture(t),first=f.client();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const second=f.client();second.markApplicationReady();await second.check();
  f.envelope=f.sign('1.2.0',2);await second.check();await second.activateAfterClose();
  f.version='1.2.0';const third=f.client();third.markApplicationReady();await third.check();
  // Rollback staged, then its retained installer fails reverification after a restart.
  assert.equal(await third.beginRecovery(),'1.1.0');fs.writeFileSync(path.join(f.directory,'updates','2','Terrier.exe'),'corrupt');
  const restarted=f.client();restarted.markApplicationReady();f.offline=true;await restarted.check();
  assert.equal(f.state.recovery.phase,'failed');assert.equal(restarted.ready,null);assert.equal(await restarted.activateAfterClose(),false);
  // The failed installer was deleted, so the next attempt fetches a fresh one (no manual cleanup).
  assert.equal(fs.existsSync(path.join(f.directory,'updates','2','Terrier.exe')),false);const original=restarted.download;
  // A manual retry whose fresh download still fails verification is refused, deleted again and marked failed.
  restarted.download=async(p,token,m,target)=>{fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,m.package.name),'corrupt');};
  await assert.rejects(()=>restarted.beginRecovery(),/Cached package/);assert.equal(f.state.recovery.phase,'failed');assert.equal(fs.existsSync(path.join(f.directory,'updates','2','Terrier.exe')),false);
  restarted.download=original;
  // Normal updates resume; the newer valid release becomes ready and the failed rollback never activates.
  f.offline=false;f.envelope=f.sign('1.2.1',3);await restarted.check();
  assert.equal(f.state.pending.version,'1.2.1');assert.equal(f.state.pending.phase,'prepared');assert.equal(restarted.ready.recovery,false);
  assert.equal(await restarted.activateAfterClose(),true);assert.equal(f.state.pending.phase,'activating');assert.equal(f.state.recovery.phase,'failed');assert.equal(f.installs,3);
});

test('broken current-version baseline is not blamed on the staged release and starts no backoff',async t=>{
  const f=fixture(t);await f.client().check();assert.equal(f.state.pending.phase,'prepared');
  const baseline=f.state.installed;f.state={...f.state,installed:{...baseline,envelope:{...baseline.envelope,signature:Buffer.alloc(64).toString('base64')}}};
  const restarted=f.client();await restarted.check();
  assert.equal(f.state.verifyFailure,undefined);assert.equal(f.state.pending.version,'1.1.0');assert.equal(restarted.ready,null);assert.equal(await restarted.activateAfterClose(),false);
  f.state={...f.state,installed:baseline};await restarted.check();
  assert.equal(f.downloads,1);assert.equal(f.state.verifyFailure,undefined);assert.equal(await restarted.activateAfterClose(),true);
});

test('signature tool failure without a verdict keeps the staged release and retries locally without backoff',async t=>{
  const f=fixture(t),bad=path.join(f.directory,'updates','2')+path.sep,codes=[];let unavailable=true;
  const client=f.client('Fixture Publisher',{verifyNativePackage:async file=>{if(unavailable&&path.resolve(file).startsWith(bad))throw Object.assign(new Error('Windows package signature verification failed.'),{code:'SIGNATURE_CHECK_UNAVAILABLE'});}});
  const report=client.report.bind(client);client.report=(e,s,c)=>{codes.push(s+':'+c);return report(e,s,c);};
  await client.check();
  assert.equal(f.state.verifyFailure,undefined);assert.equal(f.state.pending.phase,'staged');assert.equal(f.downloads,1);assert.deepEqual(codes,['deferred:SIGNATURE_CHECK_UNAVAILABLE']);
  await client.check();assert.equal(f.downloads,1);assert.equal(f.state.verifyFailure,undefined);assert.match(f.status.at(-1),/not prepared/);
  assert.deepEqual(codes,['deferred:SIGNATURE_CHECK_UNAVAILABLE']); // Reported once per staged manifest.
  unavailable=false;await client.check();assert.equal(f.downloads,1);assert.equal(await client.activateAfterClose(),true);assert.deepEqual(codes,['deferred:SIGNATURE_CHECK_UNAVAILABLE']);
});

test('malformed backoff timestamp is ignored without throwing',async t=>{
  for(const failedAt of [String(Date.now()),[Date.now()]]){
    const f=fixture(t),client=f.client();
    f.state={...f.state,verifyFailure:{manifestHash:crypto.createHash('sha256').update(Buffer.from(f.envelope.payload,'base64')).digest('hex'),failedAt}};
    await client.check();
    assert.equal(f.downloads,1);assert.equal(f.state.pending.phase,'prepared');assert.doesNotMatch(f.status.join(' '),/Invalid time|retry after/);
  }
});

test('install-on-close refuses activation when the current-version baseline fails at close time',async t=>{
  const f=fixture(t),client=f.client();await client.check();assert.equal(f.state.pending.phase,'prepared');
  f.state={...f.state,installed:{...f.state.installed,envelope:f.sign('1.0.1',0)}};
  assert.equal(await client.activateAfterClose(),false);assert.equal(f.installs,0);assert.equal(f.state.pending.phase,'prepared');assert.match(f.status.at(-1),/activation refused/);
});

test('a verified update pending from an earlier session installs silently at the next start, before anything else',async t=>{
  const f=fixture(t),calls=[];
  const watch=client=>{const original=client.autoUpdater.quitAndInstall;client.autoUpdater.quitAndInstall=function(silent,run){calls.push([silent,run,this.autoRunAppAfterInstall]);return original.call(this);};return client;};
  const first=f.client();await first.check();first.suspendInstallation();assert.equal(await first.activateAfterClose(),false);
  // Nothing to install, or an OS shutdown in progress: a normal start continues.
  const shutdown=watch(f.client());shutdown.suspendInstallation();assert.equal(await shutdown.activateAtLaunch(true),false);
  f.offline=true;const next=watch(f.client());
  assert.equal(await next.activateAtLaunch(true),true);assert.deepEqual(calls,[[true,true,true]]);assert.equal(f.installs,1);assert.equal(f.state.pending.phase,'activating');
  // The interrupted install is never retried at launch; a version awaiting startup is not reinstalled.
  assert.equal(await watch(f.client()).activateAtLaunch(true),false);f.version='1.1.0';
  f.state={...f.state,pending:{...f.state.pending,phase:'prepared'}};assert.equal(await watch(f.client()).activateAtLaunch(true),false);assert.equal(f.installs,1);
  // A normal close installs silently without relaunching.
  const g=fixture(t),closing=watch(g.client());await closing.check();assert.equal(await closing.activateAfterClose(),true);assert.deepEqual(calls.at(-1),[true,false,false]);
});

test('launch install keeps every check: tampered, isolated, unpinned or unstartable installs never activate',async t=>{
  const f=fixture(t);await f.client().check();assert.equal(f.state.pending.phase,'prepared');
  assert.equal(await f.client('Fixture Publisher',{nativeInstallation:false}).activateAtLaunch(true),false);
  assert.equal(await f.client(null).activateAtLaunch(true),false);
  const failed=f.client();failed.autoUpdater.quitAndInstall=function(){this.quitAndInstallCalled=false;};
  assert.equal(await failed.activateAtLaunch(true),false,'an installer that could not start lets the app start normally');
  const g=fixture(t);await g.client().check();fs.writeFileSync(path.join(g.directory,'updates','2','Terrier.exe'),'tampered');
  assert.equal(await g.client().activateAtLaunch(false),false);assert.equal(g.installs,0);assert.equal(g.state.pending,null);assert.equal(f.installs,0);
});

test('a requested rollback beats a pending update at launch, and starts by parking that update',async t=>{
  const f=fixture(t),first=f.client();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const second=f.client();second.markApplicationReady();await second.check();
  f.envelope=f.sign('1.2.0',2);await second.check();assert.equal(f.state.pending.version,'1.2.0');assert.equal(f.state.pending.phase,'prepared');
  const staged=f.state.pending,installs=f.installs;
  assert.equal(await second.beginRecovery(),'1.0.0');assert.equal(f.state.pending,null,'the rolled-back-over update must not resurface');assert.equal(f.state.recovery.phase,'prepared');
  // Even if a pending update is somehow still recorded, a prepared rollback wins at launch.
  for(const phase of ['staged','prepared','activating']){
    f.state={...f.state,pending:staged,recovery:{...f.state.recovery,phase}};
    assert.equal(await f.client().activateAtLaunch(true),false);assert.equal(f.installs,installs);
  }
});

test('rollback installs from local copies without any network; the first installer supplies the very first copy',async t=>{
  const f=fixture(t),bytes=fs.readFileSync(path.join(f.directory,'updates','1','Terrier.exe'));
  f.state={};fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});
  const baselineRoot=path.join(f.directory,'baseline'),offline=async()=>{throw new Error('offline');};
  // installer.nsh put the running installer here under whatever name it had.
  fs.mkdirSync(path.join(baselineRoot,'1.0.0'),{recursive:true});fs.writeFileSync(path.join(baselineRoot,'1.0.0','Terrier Setup (1).exe'),bytes);
  const first=f.client('Fixture Publisher',{baselineRoot});await first.check();await first.activateAfterClose();
  f.version='1.1.0';const second=f.client('Fixture Publisher',{baselineRoot});second.markApplicationReady();await second.check();
  assert.equal(f.state.previous.version,'1.0.0');assert.ok(f.state.previous.envelope,'the signed manifest of the baseline is kept locally');
  const downloads=f.downloads;f.offline=true;second.download=offline;
  assert.equal(await second.beginRecovery(),'1.0.0');assert.equal(f.downloads,downloads);assert.equal(f.state.recovery.phase,'prepared');
  assert.equal(fs.readdirSync(path.join(baselineRoot,'1.0.0')).length,1,'the baseline is copied, not moved: it stays until the rollback completed');
  assert.equal(await second.activateAfterClose(),true);
  f.version='1.0.0';const restored=f.client('Fixture Publisher',{baselineRoot});restored.markApplicationReady();await restored.check();
  assert.equal(f.state.recovery.phase,'completed');assert.equal(fs.existsSync(path.join(baselineRoot,'1.0.0')),false,'removed once the target version is confirmed running');
  // A corrupt baseline copy fails the hash check, is deleted, and only then is the installer fetched.
  const g=fixture(t);g.state={};fs.rmSync(path.join(g.directory,'updates','1'),{recursive:true});
  const root=path.join(g.directory,'baseline');fs.mkdirSync(path.join(root,'1.0.0'),{recursive:true});fs.writeFileSync(path.join(root,'1.0.0','Terrier.exe'),'corrupt');
  const a=g.client('Fixture Publisher',{baselineRoot:root});await a.check();await a.activateAfterClose();
  g.version='1.1.0';const b=g.client('Fixture Publisher',{baselineRoot:root});b.markApplicationReady();await b.check();const before=g.downloads;
  assert.equal(await b.beginRecovery(),'1.0.0');assert.equal(g.downloads,before+1);assert.equal(fs.existsSync(path.join(root,'1.0.0','Terrier.exe')),false);
});

test('once a new version is confirmed running only it and the previous version keep installers',async t=>{
  const f=fixture(t),baselineRoot=path.join(f.directory,'baseline'),mk=()=>f.client('Fixture Publisher',{baselineRoot});
  fs.mkdirSync(path.join(baselineRoot,'1.0.0'),{recursive:true});fs.writeFileSync(path.join(baselineRoot,'1.0.0','Terrier.exe'),'old');
  const has=n=>fs.existsSync(path.join(f.directory,'updates',String(n)));
  const first=mk();await first.check();await first.activateAfterClose();
  f.version='1.1.0';const second=mk();second.markApplicationReady();await second.check();
  assert.deepEqual([has(1),has(2)],[true,true]);assert.equal(fs.existsSync(path.join(baselineRoot,'1.0.0')),true);
  f.envelope=f.sign('1.2.0',2);await second.check();await second.activateAfterClose();
  assert.deepEqual([has(1),has(2),has(3)],[true,true,true],'nothing is pruned before the new version is confirmed running');
  f.version='1.2.0';const third=mk();third.markApplicationReady();await third.check();
  assert.deepEqual([has(1),has(2),has(3)],[false,true,true]);assert.equal(fs.existsSync(path.join(baselineRoot,'1.0.0')),false);
});

test('launch install stops waiting after repeated "no verdict" and reads the relaunch choice after preparation',async t=>{
  const f=fixture(t);await f.client().check();let unavailable=true;
  const mk=()=>f.client('Fixture Publisher',{verifyNativePackage:async()=>{if(unavailable)throw Object.assign(new Error('x'),{code:'SIGNATURE_CHECK_UNAVAILABLE'});}});
  for(let i=0;i<3;i++)assert.equal(await mk().activateAtLaunch(true),false);
  assert.equal(f.state.launchUnavailable.count,3);unavailable=false;
  assert.equal(await mk().activateAtLaunch(true),false,'launch install is skipped; install-on-close remains');assert.equal(f.installs,0);
  // Strikes are not reset by a background check; the update still installs when the app closes.
  const checker=mk();await checker.check();assert.equal(f.state.launchUnavailable.count,3);assert.equal(await checker.activateAfterClose(),true);assert.equal(f.installs,1);
  // A launch requested while the update is being prepared makes the app relaunch even after a background start.
  const g=fixture(t);await g.client().check();let requested=false;
  const late=g.client('Fixture Publisher',{verifyNativePackage:async()=>{requested=true;}});
  assert.equal(await late.activateAtLaunch(()=>requested),true);assert.equal(late.autoUpdater.autoRunAppAfterInstall,true);
});

test('an installer spawn failure after quitAndInstall returned is recorded as failed and never retried at launch',async t=>{
  // electron-updater 6.8.9: doInstall returns true right after the async spawn, so activation reports success and the app quits.
  const f=fixture(t);await f.client().check();const c=f.client();
  assert.equal(await c.activateAtLaunch(true),true);assert.equal(c.autoUpdater.quitAndInstallCalled,true);
  await c.nativeFailure();assert.equal(f.state.pending.phase,'failed');assert.equal(f.client().launchCandidate(),null);
  // The process exited before the error event: the next start marks the interrupted transaction failed.
  const g=fixture(t);await g.client().check();assert.equal(await g.client().activateAtLaunch(true),true);assert.equal(g.state.pending.phase,'activating');
  assert.equal(g.client().launchCandidate(),null);await g.client().check();assert.equal(g.state.pending.phase,'failed');
});

test('a rollback copy is deleted only when its own bytes fail the hash check; an untrusted signature or tool error keeps it',async t=>{
  const f=fixture(t),bytes=fs.readFileSync(path.join(f.directory,'updates','1','Terrier.exe'));
  f.state={};fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});
  const baselineRoot=path.join(f.directory,'baseline'),baseline=path.join(baselineRoot,'1.0.0','Terrier.exe'),retained=path.join(f.directory,'updates','1','Terrier.exe');
  fs.mkdirSync(path.dirname(baseline),{recursive:true});fs.writeFileSync(baseline,bytes);
  const mk=extra=>f.client('Fixture Publisher',{baselineRoot,...extra});
  const first=mk();await first.check();await first.activateAfterClose();f.version='1.1.0';const second=mk();second.markApplicationReady();await second.check();
  // Authenticode refusal (untrusted certificate): the verified copy and the baseline both stay.
  f.nativeValid=false;await assert.rejects(()=>mk().beginRecovery(),/Native signature refused/);
  assert.ok(fs.existsSync(retained),'cached copy kept');assert.ok(fs.existsSync(baseline),'baseline kept');assert.equal(f.state.recovery.phase,'failed');
  // No verdict from the signature tool: also kept.
  await assert.rejects(()=>mk({verifyNativePackage:async()=>{throw Object.assign(new Error('x'),{code:'SIGNATURE_CHECK_UNAVAILABLE'});}}).beginRecovery(),/x/);
  assert.ok(fs.existsSync(retained));assert.ok(fs.existsSync(baseline));
  // A native-updater/feed error: kept.
  const broken=mk();broken.autoUpdater.checkForUpdates=async()=>{throw new Error('feed down');};f.nativeValid=true;
  await assert.rejects(()=>broken.beginRecovery(),/feed down/);assert.ok(fs.existsSync(retained));
  // A trust configuration error (no pinned publisher) is not the installer's fault either.
  await assert.rejects(()=>f.client(null,{baselineRoot}).beginRecovery());assert.ok(fs.existsSync(retained));
  // Once it verifies, the rollback works from that same copy without any download.
  const downloads=f.downloads;assert.equal(await mk().beginRecovery(),'1.0.0');assert.equal(f.downloads,downloads);
  // Corrupt bytes: now it is deleted, together with its baseline source.
  fs.writeFileSync(retained,'corrupt');fs.writeFileSync(baseline,'corrupt');
  assert.equal(await mk().beginRecovery(),'1.0.0');assert.equal(f.downloads,downloads+1,'fetched fresh after the corrupt copy was removed');assert.equal(fs.existsSync(baseline),false);
});

test('a transient read error (EBUSY) on a rollback copy is not an integrity failure: neither the copy nor its baseline source is deleted',async t=>{
  const f=fixture(t),bytes=fs.readFileSync(path.join(f.directory,'updates','1','Terrier.exe'));
  f.state={};fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});
  const baselineRoot=path.join(f.directory,'baseline'),baseline=path.join(baselineRoot,'1.0.0','Terrier.exe'),retained=path.join(f.directory,'updates','1','Terrier.exe');
  fs.mkdirSync(path.dirname(baseline),{recursive:true});fs.writeFileSync(baseline,bytes);
  const mk=extra=>f.client('Fixture Publisher',{baselineRoot,...extra});
  const first=mk();await first.check();await first.activateAfterClose();f.version='1.1.0';const second=mk();second.markApplicationReady();await second.check();
  // Antivirus holds the freshly copied installer open: reading it fails with EBUSY, which says nothing about its bytes.
  const read=fs.createReadStream;fs.createReadStream=(file,...rest)=>{if(String(file).endsWith('Terrier.exe'))throw Object.assign(new Error('EBUSY: resource busy or locked'),{code:'EBUSY'});return read.call(fs,file,...rest);};
  try{await assert.rejects(()=>mk({download:async()=>{throw new Error('offline');}}).beginRecovery(),/offline/);}finally{fs.createReadStream=read;}
  assert.ok(fs.existsSync(retained),'the adopted rollback copy is kept');assert.ok(fs.existsSync(baseline),'the baseline source is kept');
  // The lock is gone: the rollback now works offline from those same copies.
  const downloads=f.downloads;assert.equal(await mk().beginRecovery(),'1.0.0');assert.equal(f.downloads,downloads);
});

test('a corrupt copy locked against delete and replace stops recovery before any download; once unlocked, recovery downloads',async t=>{
  const f=fixture(t),bytes=fs.readFileSync(path.join(f.directory,'updates','1','Terrier.exe'));
  f.state={};fs.rmSync(path.join(f.directory,'updates','1'),{recursive:true});
  const baselineRoot=path.join(f.directory,'baseline'),baseline=path.join(baselineRoot,'1.0.0','Terrier.exe'),retained=path.join(f.directory,'updates','1','Terrier.exe');
  fs.mkdirSync(path.dirname(baseline),{recursive:true});fs.writeFileSync(baseline,bytes);
  const mk=extra=>f.client('Fixture Publisher',{baselineRoot,...extra});
  const first=mk();await first.check();await first.activateAfterClose();f.version='1.1.0';const second=mk();second.markApplicationReady();await second.check();
  fs.mkdirSync(path.dirname(retained),{recursive:true});fs.writeFileSync(retained,'corrupt');fs.writeFileSync(baseline,'corrupt');
  // Another program holds the corrupt retained copy open with read sharing only (as Windows does for a running .exe):
  // it reads fine, but deleting it, renaming onto it or overwriting it fails with EPERM.
  const original={rmSync:fs.rmSync,rm:fs.promises.rm,renameSync:fs.renameSync,writeFileSync:fs.writeFileSync},locked=file=>path.resolve(String(file))===retained;
  const refuse=()=>Object.assign(new Error('EPERM: operation not permitted'),{code:'EPERM'});
  fs.rmSync=(file,...rest)=>{if(locked(file))throw refuse();return original.rmSync.call(fs,file,...rest);};
  fs.promises.rm=async(file,...rest)=>{if(locked(file))throw refuse();return original.rm.call(fs.promises,file,...rest);};
  fs.renameSync=(from,to)=>{if(locked(to))throw refuse();return original.renameSync.call(fs,from,to);};
  fs.writeFileSync=(file,...rest)=>{if(locked(file))throw refuse();return original.writeFileSync.call(fs,file,...rest);};
  const downloads=f.downloads;
  try{await assert.rejects(()=>mk().beginRecovery(),/in use by another program/);}finally{Object.assign(fs,{rmSync:original.rmSync,renameSync:original.renameSync,writeFileSync:original.writeFileSync});fs.promises.rm=original.rm;}
  assert.equal(f.downloads,downloads,'no download was spent on a path that could not be replaced');assert.ok(fs.existsSync(retained));
  // The lock is gone: the corrupt copies are removed and recovery downloads a fresh installer.
  assert.equal(await mk().beginRecovery(),'1.0.0');assert.equal(f.downloads,downloads+1);assert.equal(fs.existsSync(baseline),false);
});

test('launch strikes count every failure, including activation-time no-verdict and pre-try failures, and reset only after dispatch',async t=>{
  // Failure before the try in preparePending (no pinned publisher): three strikes, then no more offers.
  const f=fixture(t);await f.client().check();
  for(let i=0;i<3;i++){assert.ok(f.client(null).launchCandidate()!==null||i===3);assert.equal(await f.client(null).activateAtLaunch(true),false);}
  assert.equal(f.state.launchUnavailable.count,3);assert.equal(f.client().launchCandidate(),null,'no more installing window at launch');
  // No verdict only at activation time (preparation passes, the activation re-check does not).
  const g=fixture(t);await g.client().check();
  const flaky=()=>{let calls=0;return g.client('Fixture Publisher',{verifyNativePackage:async()=>{if(++calls>2)throw Object.assign(new Error('x'),{code:'SIGNATURE_CHECK_UNAVAILABLE'});}});};
  for(let i=0;i<3;i++){assert.notEqual(g.client().launchCandidate(),null);assert.equal(await flaky().activateAtLaunch(true),false);}
  assert.equal(g.state.launchUnavailable.count,3);assert.equal(g.client().launchCandidate(),null);assert.equal(g.installs,0);
  // A dispatched install resets the strikes.
  const h=fixture(t);await h.client().check();await h.client(null).activateAtLaunch(true);assert.equal(h.state.launchUnavailable.count,1);
  assert.equal(await h.client().activateAtLaunch(true),true);assert.equal(h.state.launchUnavailable,null);
});

test('moving the updater file root keeps the anti-rollback high-water and installed record; an uncached staged installer is fetched again once',async t=>{
  const f=fixture(t);await f.client().check();assert.equal(f.state.pending.phase,'prepared');assert.equal(f.downloads,1);
  const before=structuredClone(f.state),moved=path.join(f.directory,'moved-root');
  const client=f.client('Fixture Publisher',{userData:moved});await client.check();
  // The old location's installers are ignored: one re-download, no failure, no backoff.
  assert.equal(f.downloads,2);assert.equal(f.state.verifyFailure,undefined);assert.equal(f.state.pending.phase,'prepared');
  assert.ok(fs.existsSync(path.join(moved,'updates','2','Terrier.exe')));
  // Nothing reset: same high-water, same accepted version, same installed record.
  assert.equal(f.state.sequence,before.sequence);assert.equal(f.state.acceptedVersion,before.acceptedVersion);assert.equal(f.state.manifestHash,before.manifestHash);assert.deepEqual(f.state.installed,before.installed);
  // And it cannot go backwards from the moved root either: an older signed release is refused.
  f.state={...f.state,pending:null};f.envelope=f.sign('1.0.5',0);const downloads=f.downloads;
  await f.client('Fixture Publisher',{userData:moved}).check();
  assert.equal(f.state.sequence,before.sequence);assert.equal(f.state.acceptedVersion,before.acceptedVersion);assert.equal(f.downloads,downloads);assert.equal(f.state.pending,null);
});

// An update installed by a version that cached its installer in the old updater file root leaves a pending record for
// the running version whose installer is not at the current path.
for(const [name,ready,legacy] of [['ready, installer only at the legacy path',true,true],['ready, installer nowhere',true,false],['background-only, installer only at the legacy path',false,true]])
  test('an activated update whose installer cache moved is confirmed by the running version, then discovery proceeds: '+name,async t=>{
    const f=fixture(t);const first=f.client();await first.check();await first.activateAfterClose();
    assert.equal(f.state.pending.phase,'activating');f.version='1.1.0';
    if(!legacy)fs.rmSync(path.join(f.directory,'updates','2'),{recursive:true});
    const client=f.client('Fixture Publisher',{userData:path.join(f.directory,'moved-root'),legacyUserData:f.directory});
    if(ready)client.markApplicationReady();
    await client.check();
    assert.equal(f.state.pending,null);assert.equal(f.state.installed.version,'1.1.0');assert.equal(f.state.installedVersion,'1.1.0');assert.equal(f.state.previous.version,'1.0.0');
    assert.match(f.status.at(-1),/^Already current\./,'the check went on to ask for newer releases');
    // The verified legacy copy is kept at the current path, where a later offline rollback looks for it.
    if(legacy)assert.deepEqual(fs.readFileSync(path.join(f.directory,'moved-root','updates','2','Terrier.exe')),fs.readFileSync(path.join(f.directory,'updates','2','Terrier.exe')));
  });

test('an installer gone from both paths does not prove the old cache was used: a background-only run waits for a successful startup',async t=>{
  const f=fixture(t);const first=f.client();await first.check();await first.activateAfterClose();f.version='1.1.0';
  fs.rmSync(path.join(f.directory,'updates','2'),{recursive:true});
  const options={userData:path.join(f.directory,'moved-root'),legacyUserData:f.directory};
  await f.client('Fixture Publisher',options).check();
  assert.equal(f.state.pending.phase,'activating');assert.equal(f.state.installed.version,'1.0.0');assert.match(f.status.at(-1),/awaits a successful local startup/);
  const ready=f.client('Fixture Publisher',options);ready.markApplicationReady();await ready.check();
  assert.equal(f.state.pending,null);assert.equal(f.state.installed.version,'1.1.0');
});

test('a legacy-path installer is still verified: one failing the native check does not confirm the update',async t=>{
  const f=fixture(t);const first=f.client();await first.check();await first.activateAfterClose();f.version='1.1.0';f.nativeValid=false;
  const client=f.client('Fixture Publisher',{userData:path.join(f.directory,'moved-root'),legacyUserData:f.directory});client.markApplicationReady();await client.check();
  assert.equal(f.state.pending.phase,'activating');assert.equal(f.state.installed.version,'1.0.0');assert.match(f.status.at(-1),/Native signature refused/);
});

test('an installer at the current path keeps the readiness gate: a background-only run does not confirm it',async t=>{
  const f=fixture(t);const first=f.client();await first.check();await first.activateAfterClose();f.version='1.1.0';
  await f.client().check();assert.equal(f.state.pending.phase,'activating');assert.equal(f.state.installed.version,'1.0.0');assert.match(f.status.at(-1),/awaits a successful local startup/);
});

test('main.cjs moves only the updater file root to %LOCALAPPDATA%, leaving the update state in the profile',()=>{
  const main=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8');
  assert.match(main,/const updaterRoot=localRoot\?path\.join\(localRoot,'updater'\):githubConfig\?path\.join\(userData,'github-updates'\):userData;/);
  assert.match(main,/baselineRoot:localRoot\?path\.join\(localRoot,'baseline'\):null/);assert.match(main,/userData:updaterRoot,loadEnrollment/);
  assert.match(main,/legacyUserData:localRoot&&githubConfig\?path\.join\(userData,'github-updates'\):null/,'the pre-move cache stays known to the updater');
  assert.match(main,/readState:\(\)=>read\(githubConfig\?'github-update-state\.json':'update-state\.json'\),writeState:value=>write\(/,'state stays in the profile');
});
