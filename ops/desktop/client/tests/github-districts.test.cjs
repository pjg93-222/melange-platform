'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),os=require('node:os');
const {canonical}=require('../access-client.cjs');
const protocol=require('../github-district-protocol.cjs');
const {GitHubDistricts,publicTransport}=require('../github-districts.cjs');
const {runtimeRoots,prepareRuntimeRoots}=require('../runtime-roots.cjs');
const {ownerBindingFingerprint}=require('../access-bootstrap.cjs');
const {districtConfiguration,verifyDistrictAsync}=require('../district-switch.cjs');
const {REPOSITORY,POINTER_PATH,IDS,ROLES,assetURL}=protocol;
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const json=value=>Buffer.from(JSON.stringify(value));
function fixture(t){
 const temporary=fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(),'github-district-fixture-')));t.after(()=>fs.rmSync(temporary,{recursive:true,force:true,maxRetries:5}));
 const profile=path.join(temporary,'profile'),root=path.join(profile,'packs'),legacy=path.join(temporary,'legacy');fs.mkdirSync(root,{recursive:true});fs.mkdirSync(legacy);
 const keys=crypto.generateKeyPairSync('ed25519'),raw=keys.publicKey.export({format:'der',type:'spki'}).subarray(-32).toString('base64'),publicKeys={fixture:raw};
 const sign=value=>({schemaVersion:1,keyId:'fixture',payload:Buffer.from(canonical(value)).toString('base64'),signature:crypto.sign(null,Buffer.from(canonical(value)),keys.privateKey).toString('base64')});
 const config={dataDirectory:legacy,district:'co-05',terrierLegacy:{schemaVersion:1,district:'co-05',firstPasswordOwner:'admin',dataDirectory:legacy},terrierDistricts:{schemaVersion:1,legacyRoot:legacy,districts:[...IDS]}};
 const grant={appId:'terrier',serverId:crypto.randomUUID(),deviceId:crypto.randomUUID(),principalId:crypto.randomUUID(),grantId:crypto.randomUUID(),accountIdentity:'fixture-owner',districtIds:[...IDS]};
 const authorization={accepted:{revoked:false,grant},owner:{mode:'existing-owner',appId:'terrier',grantId:grant.grantId,accountIdentity:grant.accountIdentity,dataBindings:IDS.map(districtId=>({districtId}))},principal:{...grant,mode:'existing-owner',districtId:'co-05'}};
 const worlds=new Map();let world;
 function build(sequence=1){
  const tag='district-testsets-2026-10-21'+(sequence>1?'-r'+sequence:''),files=[],runtime_roles={},dataset_assets=[],data=new Map(),archives=new Map();
  for(const id of IDS){runtime_roles[id]={};const name='terrier-testsets-'+id+'-'+tag.slice('district-testsets-'.length)+'.zip',archive=Buffer.from('fixture archive '+id+' revision '+sequence);archives.set(id,archive);
   for(const role of ROLES){const bytes=role.endsWith('.sqlite3')?Buffer.concat([Buffer.from('SQLite format 3\0'),Buffer.from([0,0,1,1]),Buffer.from(id+role+sequence)]):Buffer.from(JSON.stringify({district:id,revision:sequence}));data.set(id+'/'+role,bytes);const item={path:id+'/data/'+role,district:id,bytes:bytes.length,roles:['runtime_dataset'],sha256:sha(bytes),asset:name};files.push(item);runtime_roles[id][role]={path:item.path,bytes:item.bytes,sha256:item.sha256,asset:name};}
   dataset_assets.push({name,bytes:archive.length,sha256:sha(archive),members:6,raw_bytes:files.filter(item=>item.district===id).reduce((n,item)=>n+item.bytes,0),member_sha256_verified:true,zip_crc_verified:true,download_url:assetURL(tag,name)});
  }
  const manifest=json({schema:'terrier-district-testsets-v1',repository:REPOSITORY,release_tag:tag,districts:[...IDS],unique_dataset_files:18,dataset_input_bytes:files.reduce((n,item)=>n+item.bytes,0),distribution:{kind:'data_prerelease',make_latest:false,desktop_update_tag:false},files,runtime_roles,dataset_assets});
  const body={schemaVersion:1,purpose:'terrier-public-district-data',appId:'terrier',repository:REPOSITORY,catalogSequence:sequence,releaseTag:tag,releaseCommit:String(sequence).repeat(40),manifest:{asset:'manifest.json',bytes:manifest.length,sha256:sha(manifest)}};
  const result={body,envelope:sign(body),manifest,data,archives};worlds.set(tag,result);return result;
 }
 world=build();const requests=[],downloads=[],checks=[],statuses=[];let saved=null,networkDown=false,corruptDownload=false,extractFails=false,extractCount=0;
 const store={read:async()=>structuredClone(saved),write:async value=>{saved=structuredClone(value);}};
 const transport={read:async(url,options)=>{requests.push({url,options});if(networkDown)throw Object.assign(new Error('fixture network unavailable bearer=SENTINEL'),{code:'ENETUNREACH'});if(url.endsWith('/commits/main'))return json({sha:'a'.repeat(40)});if(url.includes('/contents/')){const bytes=json(world.envelope);return json({type:'file',encoding:'base64',path:POINTER_PATH,size:bytes.length,content:bytes.toString('base64')});}if(url.includes('/git/ref/tags/'))return json({ref:'refs/tags/'+world.body.releaseTag,object:{type:'commit',sha:world.body.releaseCommit}});const target=[...worlds.values()].find(item=>url===assetURL(item.body.releaseTag,'manifest.json'));assert.ok(target,url);return target.manifest;},download:async(url,target,options)=>{downloads.push({url,options});const item=[...worlds.values()].find(item=>url.includes('/'+item.body.releaseTag+'/')),id=IDS.find(id=>url.includes('-'+id+'-'));assert.ok(item&&id);const bytes=corruptDownload?Buffer.alloc(item.archives.get(id).length):item.archives.get(id);await fsp.writeFile(target,bytes,{flag:'wx'});options.onProgress(bytes.length);}};
 const checkExisting=async(directory,district)=>{checks.push({directory,district});return true;};
 const extract=async({archive,destination,district,files})=>{extractCount++;assert.ok(archive.endsWith('.zip'));await fsp.mkdir(destination);if(extractFails){await fsp.writeFile(path.join(destination,'partial'),'fixture');throw new Error('fixture extraction failure');}const item=[...worlds.values()].find(item=>files[0].sha256===sha(item.data.get(district+'/'+files[0].role)));assert.ok(item);for(const file of files)await fsp.writeFile(path.join(destination,file.role),item.data.get(district+'/'+file.role),{flag:'wx'});return {ok:true,district,files:files.map(({role,bytes,sha256})=>({role,bytes,sha256})),sourceMetadataAccepted:true};};
 const create=(overrides={})=>new GitHubDistricts({profile,root,publicKeys,store,transport,checkExisting,extract,onStatus:status=>statuses.push(status),statfs:async()=>({bavail:100000000000,bsize:1}),...overrides});
 function writeLegacy(item=world){for(const id of IDS){const directory=id==='co-05'?legacy:path.join(legacy,id);fs.mkdirSync(directory,{recursive:true});for(const role of ROLES)fs.writeFileSync(path.join(directory,role),item.data.get(id+'/'+role));fs.writeFileSync(path.join(directory,'accounts.sqlite3'),'fixture account remains local');fs.writeFileSync(path.join(directory,'.secret'),'fixture secret remains local');}}
 return {temporary,profile,root,legacy,config,authorization,publicKeys,sign,build,create,writeLegacy,requests,downloads,checks,statuses,transport,get world(){return world;},set world(value){world=value;},get state(){return saved;},set state(value){saved=value;},set networkDown(value){networkDown=value;},set corruptDownload(value){corruptDownload=value;},set extractFails(value){extractFails=value;},get extractCount(){return extractCount;}};
}
test('pointer signature domain, exact fields, pinned identity and independent sequence reject tamper and rollback',t=>{
 const f=fixture(t),first=protocol.pointer(f.world.envelope,f.publicKeys);assert.equal(first.value.catalogSequence,1);
 for(const change of [{purpose:'melange-desktop-github-release'},{repository:'attacker/Geo'},{appId:'mentat'},{catalogSequence:0},{unknown:true},{releaseTag:'desktop-v1.0.14'},{releaseCommit:'0'.repeat(40)}])assert.throws(()=>protocol.pointer(f.sign({...f.world.body,...change}),f.publicKeys));
 assert.throws(()=>protocol.pointer({...f.world.envelope,keyId:'unknown'},f.publicKeys));
 assert.throws(()=>protocol.pointer({...f.world.envelope,signature:Buffer.alloc(64).toString('base64')},f.publicKeys),/signature/);
 assert.throws(()=>protocol.pointer(f.world.envelope,f.publicKeys,{catalogSequence:2,pointerDigest:'b'.repeat(64)}),/rollback/);
 assert.throws(()=>protocol.pointer(f.world.envelope,f.publicKeys,{catalogSequence:1,pointerDigest:'b'.repeat(64)}),/equivocation/);
 assert.throws(()=>protocol.pointer(f.sign({...f.world.body,manifest:{...f.world.body.manifest,size:1}}),f.publicKeys));
 assert.throws(()=>require('../trust.cjs').verifyEnvelope(f.world.envelope,{source:'github',repository:REPOSITORY,publicKeys:f.publicKeys},{appId:'terrier',platform:'win32',arch:'x64'}));
});
test('catalogue validates exact archive/18-role integrity, paths and bounds rather than trusting checksum labels',t=>{
 const f=fixture(t),checked=protocol.pointer(f.world.envelope,f.publicKeys);assert.equal(protocol.manifest(f.world.manifest,checked).runtime['co-05'].length,6);
 const altered=Buffer.from(f.world.manifest);altered[5]^=1;assert.throws(()=>protocol.manifest(altered,checked),/integrity/);
 for(const mutate of [m=>m.files.push(m.files[0]),m=>m.runtime_roles['co-05']['walk.sqlite3'].path='../accounts.sqlite3',m=>m.dataset_assets[0].download_url='https://attacker.test/archive',m=>m.dataset_assets[0].bytes=2**32,m=>m.files[0].path='co-05/data/../accounts.sqlite3']){const m=JSON.parse(f.world.manifest);mutate(m);const bytes=json(m),body={...f.world.body,manifest:{asset:'manifest.json',bytes:bytes.length,sha256:sha(bytes)}};assert.throws(()=>protocol.manifest(bytes,protocol.pointer(f.sign(body),f.publicKeys)));}
});
test('recipient, revoked or forged owner contexts never make GitHub requests',async t=>{
 const f=fixture(t),p=f.create();
 for(const authorization of [null,{...f.authorization,owner:null},{...f.authorization,accepted:{...f.authorization.accepted,revoked:true}},{...f.authorization,principal:{...f.authorization.principal,mode:'recipient'}},{...f.authorization,principal:{...f.authorization.principal,deviceId:'other'}}]){assert.equal(await p.ensure(f.config,authorization),f.config);assert.equal(await p.discover(f.config,authorization),null);}
 assert.equal(f.requests.length,0);assert.equal(f.state,null);assert.equal(p.info(f.config),null);
});
test('identical legacy runtime bytes are reused with zero archive downloads and native owner roots unchanged',async t=>{
 const f=fixture(t);f.writeLegacy();const before=ownerBindingFingerprint('terrier',f.profile,f.config,f.authorization.accepted.grant),p=f.create(),result=await p.ensure(f.config,f.authorization);
 assert.deepEqual(result,f.config);assert.equal(f.downloads.length,0);assert.equal(f.extractCount,0);assert.equal(f.checks.length,1);assert.equal(p.info(f.config).phase,'ready');assert.equal(f.state.active['co-05'].source,'legacy');assert.equal(ownerBindingFingerprint('terrier',f.profile,result,f.authorization.accepted.grant),before);
 const text=JSON.stringify(p.info(f.config));for(const secret of [f.legacy,f.profile,'fixture-owner','grantId','SENTINEL'])assert.ok(!text.includes(secret));
 for(const request of [...f.requests,...f.downloads])for(const key of ['token','authorization','headers','grant','deviceId','owner'])assert.equal(Object.hasOwn(request.options,key),false);
 assert.ok(f.requests.some(request=>request.url.endsWith('?ref='+'a'.repeat(40))));
});
test('a changed byte with preserved size and mtime forces selected-only verified download, preserving owner state',async t=>{
 const f=fixture(t);f.writeLegacy();const p=f.create();await p.ensure(f.config,f.authorization);
 const changed=path.join(f.legacy,'walk.sqlite3'),stat=fs.statSync(changed),bytes=fs.readFileSync(changed);bytes[bytes.length-1]^=1;fs.writeFileSync(changed,bytes);fs.utimesSync(changed,stat.atime,stat.mtime);
 const before=ownerBindingFingerprint('terrier',f.profile,f.config,f.authorization.accepted.grant),result=await p.ensure(f.config,f.authorization);
 assert.equal(f.downloads.length,1);assert.ok(f.downloads[0].url.includes('co-05'));assert.equal(f.extractCount,1);assert.equal(result.ownerGitHubData.district,'co-05');
 const roots=prepareRuntimeRoots('terrier',f.profile,result);assert.equal(roots.state,f.legacy);assert.equal(roots.githubDistrictData,true);assert.ok(roots.data.startsWith(f.root));assert.equal(fs.readFileSync(path.join(f.legacy,'accounts.sqlite3'),'utf8'),'fixture account remains local');assert.equal(fs.readFileSync(path.join(f.legacy,'.secret'),'utf8'),'fixture secret remains local');assert.deepEqual(fs.readFileSync(changed),bytes);
 assert.equal(ownerBindingFingerprint('terrier',f.profile,result,f.authorization.accepted.grant),before);
 assert.ok(!fs.existsSync(path.join(roots.data,'accounts.sqlite3')));
 const again=await f.create().ensure(f.config,f.authorization);assert.deepEqual(again,result);assert.equal(f.downloads.length,1);
});
test('archive corruption or failed extraction leaves original files and active state intact, with no partial cache',async t=>{
 for(const mode of ['corrupt','extract']){
  const f=fixture(t);f.writeLegacy();const p=f.create();await p.ensure(f.config,f.authorization);const active=structuredClone(f.state.active),original=fs.readFileSync(path.join(f.legacy,'contacts.sqlite3'));f.world=f.build(2);if(mode==='corrupt')f.corruptDownload=true;else f.extractFails=true;
  await assert.rejects(()=>p.ensure(f.config,f.authorization));assert.deepEqual(f.state.active,active);assert.deepEqual(fs.readFileSync(path.join(f.legacy,'contacts.sqlite3')),original);assert.equal(p.info(f.config).phase,'error');assert.equal(fs.readdirSync(path.join(f.root,'github-districts','staging')).length,0);assert.ok(!fs.readdirSync(path.join(f.root,'github-districts','downloads')).some(name=>name.endsWith('.part')));
 }
});
test('failed extractor cleanup refuses traversal and never follows staging junctions into existing state',async t=>{
 for(const mode of ['no-directory','root-junction','child-junction']){
  const f=fixture(t);f.writeLegacy();await f.create().ensure(f.config,f.authorization);f.world=f.build(2);
  const outside=path.join(f.temporary,'protected-owner-fixture');fs.mkdirSync(outside);fs.writeFileSync(path.join(outside,'accounts.sqlite3'),'unchanged fixture state');
  const p=f.create({extract:async({destination})=>{if(mode==='root-junction')fs.symlinkSync(outside,destination,process.platform==='win32'?'junction':'dir');if(mode==='child-junction'){fs.mkdirSync(destination);fs.symlinkSync(outside,path.join(destination,'linked-state'),process.platform==='win32'?'junction':'dir');}throw new Error('fixture extractor stopped');}});
  await assert.rejects(()=>p.ensure(f.config,f.authorization));assert.equal(fs.readFileSync(path.join(outside,'accounts.sqlite3'),'utf8'),'unchanged fixture state');assert.deepEqual(fs.readdirSync(outside),['accounts.sqlite3']);assert.deepEqual(fs.readdirSync(path.join(f.root,'github-districts','staging')),[]);
 }
});
test('download progress is throttled while the final byte count is always reported',async t=>{
 const f=fixture(t);const original=f.transport.download;f.transport.download=async(url,target,options)=>{for(let index=0;index<1000;index++)options.onProgress(1);return original(url,target,options);};
 const p=f.create({now:()=>1000});await p.ensure(f.config,f.authorization);const progress=f.statuses.filter(item=>item.phase==='downloading');assert.ok(progress.length<=3);assert.equal(progress.at(-1).receivedBytes,progress.at(-1).totalBytes);
});
test('background updates stage without changing active runtime, then next ensure uses the immutable ready cache',async t=>{
 const f=fixture(t);f.writeLegacy();const p=f.create();await p.ensure(f.config,f.authorization);const first=structuredClone(f.state.active);f.world=f.build(2);
 const start=f.statuses.length;
 const info=await p.discover(f.config,f.authorization,{stageOnly:true});assert.equal(info.phase,'update-ready');assert.equal(info.updateAvailable,true);assert.deepEqual(f.state.active,first);assert.equal(f.downloads.length,1);
 for(const status of f.statuses.slice(start)){assert.equal(status.cached,true);if(status.releaseTag===f.world.body.releaseTag)assert.equal(status.catalogueSequence,2);assert.equal(status.verifiedAt,first['co-05'].verifiedAt);}
 const updated=await p.ensure(f.config,f.authorization);assert.ok(updated.ownerGitHubData);assert.equal(f.state.active['co-05'].catalogSequence,2);assert.equal(f.downloads.length,1);assert.equal(f.state.staged['co-05'],undefined);
 // No data hashing/metadata probe when background discovery sees no change.
 const checks=f.checks.length;await p.discover(updated,f.authorization);assert.equal(f.checks.length,checks);
 f.world=f.build(1);await assert.rejects(()=>p.ensure(f.config,f.authorization),/rollback/);assert.equal(f.state.active['co-05'].catalogSequence,2);
});
test('offline reuse revalidates exact bytes; signature/rollback failures never masquerade as offline success',async t=>{
 const f=fixture(t);f.writeLegacy();const p=f.create();await p.ensure(f.config,f.authorization);f.networkDown=true;
 assert.deepEqual(await f.create().ensure(f.config,f.authorization),f.config);assert.equal((await p.discover(f.config,f.authorization)).phase,'offline');
 f.networkDown=false;const accepted=structuredClone(f.state);f.world={...f.world,envelope:{...f.world.envelope,signature:Buffer.alloc(64).toString('base64')}};await assert.rejects(()=>p.ensure(f.config,f.authorization),/signature/);assert.equal(p.info(f.config).phase,'error');assert.deepEqual(f.state,accepted);
 f.networkDown=true;fs.writeFileSync(path.join(f.legacy,'density.json'),'invalid');await assert.rejects(()=>p.ensure(f.config,f.authorization));assert.equal(p.info(f.config).phase,'error');
});
test('cached catalogue/protected owner identity tamper and cache junctions are refused',async t=>{
 const f=fixture(t);f.writeLegacy();const p=f.create();await p.ensure(f.config,f.authorization);const accepted=structuredClone(f.state);
 f.state={...f.state,identity:{...f.state.identity,deviceId:'wrong'}};await assert.rejects(()=>p.ensure(f.config,f.authorization),/another owner/);f.state=accepted;
 const root=path.join(f.root,'github-districts','datasets'),outside=path.join(f.temporary,'outside');fs.mkdirSync(outside);fs.rmSync(root,{recursive:true});fs.symlinkSync(outside,root,process.platform==='win32'?'junction':'dir');await assert.rejects(()=>p.ensure(f.config,f.authorization),/Unsafe/);assert.deepEqual(fs.readdirSync(outside),[]);
});
test('a network interruption preparing a newer revision reuses only the prior verified active data',async t=>{
 const f=fixture(t);f.writeLegacy();const p=f.create();await p.ensure(f.config,f.authorization);const active=structuredClone(f.state.active);f.world=f.build(2);f.transport.download=async()=>{throw Object.assign(new Error('fixture disconnected'),{code:'ECONNRESET'});};
 assert.deepEqual(await p.ensure(f.config,f.authorization),f.config);assert.equal(p.info(f.config).phase,'offline');assert.deepEqual(f.state.active,active);
 f.transport.read=async()=>{throw new Error('GitHub district metadata exceeds limit');};await assert.rejects(()=>p.ensure(f.config,f.authorization),/limit/);assert.equal(p.info(f.config).phase,'error');
});
test('the protected GitHub state kind is distinct from recipient and binary-update journals',()=>{
 const {accessStateStore}=require('../access-state.cjs');for(const kind of ['github-district-state','dataset-journal','state'])assert.equal(typeof accessStateStore({resources:'fixture',appId:'terrier',profile:'fixture',kind}).read,'function');assert.throws(()=>accessStateStore({kind:'github-district-state/../owner'}));
});
test('runtime overrides require strict existing-owner fields and the fixed managed cache root',t=>{
 const f=fixture(t);f.writeLegacy();const sha='a'.repeat(64),cache=path.join(f.root,'github-districts','datasets',sha,'co-05');fs.mkdirSync(cache,{recursive:true});
 const override={schemaVersion:1,district:'co-05',cacheRoot:f.root,catalogueSha256:sha},selected={...f.config,ownerGitHubData:override};assert.equal(runtimeRoots('terrier',f.profile,selected).state,f.legacy);
 for(const changed of [{district:'or-05'},{cacheRoot:f.temporary},{catalogueSha256:'../escape'},{unexpected:true}])assert.throws(()=>runtimeRoots('terrier',f.profile,{...selected,ownerGitHubData:{...override,...changed}}));
 const {terrierLegacy,...recipient}=selected;assert.throws(()=>runtimeRoots('terrier',f.profile,recipient),/existing owner/);
 const next=districtConfiguration(selected,'or-05',IDS);assert.equal(next.ownerGitHubData,undefined);assert.equal(next.terrierLegacy.dataDirectory,path.join(f.legacy,'or-05'));
});
test('district verification sends separated dataset/state only for a validated owner cache override',async t=>{
 const f=fixture(t);f.writeLegacy();const catalogueSha256='a'.repeat(64),cached=path.join(f.root,'github-districts','datasets',catalogueSha256,'co-05');fs.mkdirSync(cached,{recursive:true});
 const selected={...f.config,ownerGitHubData:{schemaVersion:1,district:'co-05',cacheRoot:f.root,catalogueSha256}},resources=path.join(f.temporary,'resources'),runtime=path.join(resources,'runtime','terrier');fs.mkdirSync(runtime,{recursive:true});fs.writeFileSync(path.join(runtime,process.platform==='win32'?'desktop-backend.exe':'desktop-backend'),'fixture');
 const module=require('../desktop-probe.cjs'),original=module.desktopProbe,requests=[];module.desktopProbe=async(command,mode,payload)=>{requests.push({command,mode,payload});return {district:payload.district,validated:true};};
 try{await verifyDistrictAsync(resources,f.profile,f.config);await verifyDistrictAsync(resources,f.profile,selected);}finally{module.desktopProbe=original;}
 assert.equal(requests[0].payload.dataRoot,f.legacy);assert.equal(Object.hasOwn(requests[0].payload,'stateRoot'),false);assert.equal(Object.hasOwn(requests[0].payload,'githubDistrictData'),false);
 assert.equal(requests[1].payload.dataRoot,cached);assert.equal(requests[1].payload.stateRoot,f.legacy);assert.equal(requests[1].payload.legacy,true);assert.equal(requests[1].payload.githubDistrictData,true);
});
test('real public transport sends public GET headers only, bounds bytes and refuses external redirect hosts',async t=>{
 const {Readable}=require('node:stream'),{EventEmitter}=require('node:events'),https=require('node:https'),original=https.request,requests=[];let redirect=false,oversize=false;
 https.request=(url,options,callback)=>{requests.push({url:String(url),options});const request=new EventEmitter();request.setTimeout=()=>request;request.destroy=error=>{request.emit('error',error);return request;};request.end=()=>queueMicrotask(()=>{const stream=Readable.from([Buffer.from(oversize?'oversize':'safe')]);stream.statusCode=redirect?302:200;stream.headers=redirect?{location:'https://attacker.test/private'}:{};callback(stream);});return request;};
 try{
  assert.equal((await publicTransport.read('https://api.github.com/repos/'+REPOSITORY+'/commits/main',{maxBytes:4})).toString(),'safe');
  for(const request of requests){assert.equal(request.options.method,'GET');assert.equal(request.options.rejectUnauthorized,true);assert.equal(Object.keys(request.options.headers).some(key=>['authorization','cookie','x-desktop-token','x-device-id'].includes(key.toLowerCase())),false);assert.ok(!JSON.stringify(request).includes('fixture-owner'));}
  oversize=true;await assert.rejects(()=>publicTransport.read('https://api.github.com/repos/'+REPOSITORY+'/commits/main',{maxBytes:4}),/limit/);
  oversize=false;redirect=true;await assert.rejects(()=>publicTransport.read(assetURL('district-testsets-2026-10-21','manifest.json'),{maxBytes:10,asset:true}),/URL refused/);
 }finally{https.request=original;}
});
