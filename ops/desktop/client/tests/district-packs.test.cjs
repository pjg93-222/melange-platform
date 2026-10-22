'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),os=require('node:os'),zlib=require('node:zlib');
const {canonical}=require('../access-client.cjs');const {route}=require('../dataset-transport.cjs');const {CHUNK}=require('../dataset-protocol.cjs');
const {confineIsolatedPath,confineIsolatedSettings}=require('../profile.cjs');
const {runtimeRoots}=require('../runtime-roots.cjs');
const {ensureDistrictData,createAdapter,districtCheck,storeRoot,migrateSettings,removeOldDefaultData,sessionDirectory,logDirectory,bundleImportNotice,defaultDistrict,removeStore,gunzipTo,DistrictDataStop,KIND,REQUIRED,ALLOWED}=require('../district-packs.cjs');
const {Runtime}=require('../runtime.cjs');
const sha256=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function sandbox(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'district-packs-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}
// A SQLite header in rollback-journal (1,1) or WAL (2,2) mode, optionally padded with incompressible bytes.
function sqlite({wal=false,size=4096}={}){const bytes=Buffer.alloc(size);bytes.write('SQLite format 3\0','latin1');bytes[18]=bytes[19]=wal?2:1;if(size>4096)crypto.randomBytes(size-4096).copy(bytes,4096);return bytes;}
const district=id=>({'contacts.sqlite3':sqlite(),'roads.sqlite3':sqlite(),'scores.sqlite3':sqlite(),'street.sqlite3':sqlite(),'walk.sqlite3':sqlite(),'density.json':Buffer.from('{"'+id+'":1}'),'credit-quality.json':Buffer.from('{}'),'bundle.zip':Buffer.from('PK fixture '+id)});
// The access server's signed catalog and gzip objects, the approved gate and the journal, all in memory or in the sandbox.
// Every object is {role,sha256,size,rawSize}: rawSize (bytes after gunzip) is signed by the server; an override may be {gz,rawSize} to publish a damaged or lying object.
// (Needs dataset-protocol.cjs to accept rawSize for terrier.district-data.v1.)
function world(districts){
 const pair=crypto.generateKeyPairSync('ed25519'),raw=pair.publicKey.export({format:'der',type:'spki'}).subarray(-32),keyId=crypto.createHash('sha256').update(raw).digest('hex').slice(0,24),key=raw.toString('base64');
 const trust={schemaVersion:1,purpose:'melange-desktop-trust',serverId:crypto.randomUUID(),url:'https://127.0.0.1:7443',tlsSpkiSha256:'a'.repeat(64),accessPublicKeys:{[keyId]:key},updatePublicKeysByApp:Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{[keyId]:key}]))};
 const principalId=crypto.randomUUID(),token=crypto.randomBytes(48).toString('base64url');
 const grant={appId:'terrier',serverId:trust.serverId,deviceId:crypto.randomUUID(),principalId,grantId:crypto.randomUUID(),accountIdentity:'recipient_'+principalId.replaceAll('-',''),role:'local_administrator',districtIds:[...districts],offlineAllowed:true};
 const w={trust,grant,calls:[],attempts:[],sleeps:[],statuses:[],checked:[],objects:new Map(),revisions:[],sequence:0,fail:null,refreshes:0,datasetIds:{}};
 w.publish=(id,overrides={})=>{
  const files={...district(id),...overrides},objects=[];
  for(const [role,data] of Object.entries(files)){if(data===null)continue;const gz=Buffer.isBuffer(data)?zlib.gzipSync(data):data.gz;w.objects.set(sha256(gz),gz);objects.push({role,sha256:sha256(gz),size:gz.length,rawSize:Buffer.isBuffer(data)?data.length:data.rawSize});}
  const sequence=++w.sequence,head=w.revisions.filter(r=>r.districtId===id).at(-1);w.datasetIds[id]??=crypto.randomUUID();
  const body={schemaVersion:1,purpose:'melange-desktop-dataset',serverId:trust.serverId,appId:'terrier',datasetId:w.datasetIds[id],revisionId:crypto.randomUUID(),parentRevisionIds:head?[head.revisionId]:[],districtId:id,kind:KIND,adapterVersion:1,objects,counts:{files:objects.length},originPrincipalId:trust.serverId,originDeviceId:trust.serverId,committedAt:Math.floor(Date.now()/1000),catalogSequence:sequence};
  const bytes=Buffer.from(canonical(body)),envelope={schemaVersion:1,keyId,payload:bytes.toString('base64'),signature:crypto.sign(null,bytes,pair.privateKey).toString('base64')};
  w.revisions.push({districtId:id,sequence,revisionId:body.revisionId,envelope});return body;
 };
 for(const id of districts)w.publish(id);
 // The real route() validates every request the client builds.
 w.request=async(_trust,relative)=>{
  route(relative,'GET');w.calls.push(relative);const url=new URL(relative,'https://127.0.0.1');
  if(url.pathname.startsWith('/v1/data/objects/'))w.attempts.push(Number(url.searchParams.get('length')));
  const error=w.fail?.(relative);if(error)throw error;
  if(url.pathname==='/v1/data/catalog'){
   const after=Number(url.searchParams.get('after')),limit=Number(url.searchParams.get('limit')),rows=w.revisions.filter(r=>r.districtId===url.searchParams.get('districtId')&&r.sequence>after),page=rows.slice(0,limit);
   return {revisions:page.map(r=>r.envelope),nextCursor:page.length?page.at(-1).sequence:after,hasMore:rows.length>limit};
  }
  const offset=Number(url.searchParams.get('offset')),length=Number(url.searchParams.get('length'));return w.objects.get(url.pathname.split('/').pop()).subarray(offset,offset+length);
 };
 w.gate={accepted:null,evidence:async()=>{w.gate.accepted={grant,revoked:false};return w.gate.accepted;},districts:()=>w.gate.accepted&&!w.gate.accepted.revoked?[...grant.districtIds]:[],
  refresh:async()=>{w.refreshes++;return {phase:'ready'};},client:{trust,accepted:async()=>({grant,revoked:false}),secrets:{read:async()=>({tokens:{[grant.grantId]:token}})}}};
 return w;
}
function journalStore(dir){const file=path.join(dir,'fixture-journal.json');return {read:async()=>fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):null,write:async value=>{fs.writeFileSync(file+'.tmp',JSON.stringify(value));fs.renameSync(file+'.tmp',file);}};}
function options(dir,w,extra={}){
 return {root:path.join(dir,'packs'),settings:{district:'co-05'},journal:journalStore(dir),request:w.request,sleep:async ms=>{w.sleeps.push(ms);},onStatus:text=>w.statuses.push(text),statfs:async()=>({bavail:1e12,bsize:1}),
  check:async(directory,id)=>{w.checked.push({id,files:fs.readdirSync(directory).sort()});return true;},...extra};
}
const rangeCalls=w=>w.calls.filter(call=>call.includes('/objects/'));
const place=(dir,id)=>path.join(dir,'packs','stores','districts',id);
const failure=(reason,statusCode)=>Object.assign(new Error('fixture '+reason),{reason,...(statusCode?{statusCode}:{})});

test('each missing approved district is downloaded privately, verified and activated; only allowlisted files arrive',async t=>{
 const dir=sandbox(t),w=world(['co-05','or-05']),settings={district:'or-05'};
 const result=await ensureDistrictData(w.gate,options(dir,w,{settings}));
 assert.deepEqual(result,{districts:['co-05','or-05'],downloaded:['or-05','co-05']},'the district in use comes first');
 for(const id of ['co-05','or-05']){
  assert.deepEqual(fs.readdirSync(place(dir,id)).sort(),[...ALLOWED].sort());
  const bundle=fs.readFileSync(path.join(place(dir,id),'bundle.zip'));assert.equal(fs.readFileSync(path.join(place(dir,id),'bundle.zip.sha256'),'ascii'),sha256(bundle)+'\n');
 }
 assert.deepEqual(w.checked.map(item=>item.id),['or-05','co-05']);assert.ok(w.checked.every(item=>item.files.includes('bundle.zip.sha256')&&!item.files.includes('accounts.sqlite3')));
 assert.ok(w.statuses.some(text=>/^Downloading Oregon 05 — \d+ MB of \d+ MB$/.test(text)),w.statuses.join('|'));
 // The gzip cache and staging are gone once activated, and the journal records both as active.
 assert.deepEqual(fs.readdirSync(path.join(dir,'packs','dataset-sync','objects')),[]);assert.equal(fs.existsSync(path.join(dir,'packs','staging')),false);
 assert.equal(Object.keys(JSON.parse(fs.readFileSync(path.join(dir,'fixture-journal.json'),'utf8')).active).length,2);
 assert.ok(w.calls.every(call=>call.startsWith('/v1/data/')),'only the private data routes were used');
});
test('the adapter refuses any role outside the allowlist, in metadata and when unzipping',async t=>{
 const dir=sandbox(t),adapter=createAdapter({root:path.join(dir,'packs'),check:async()=>true});
 const roles=names=>({districtId:'co-05',objects:names.map(role=>({role,sha256:'a'.repeat(64),size:1}))});
 assert.equal(adapter.validateMetadata(roles(REQUIRED)),true);
 for(const extra of ['accounts.sqlite3','.secret','audit.log','snapshots'])assert.equal(adapter.validateMetadata(roles([...REQUIRED,extra])),false,extra);
 assert.equal(adapter.validateMetadata(roles(REQUIRED.filter(role=>role!=='scores.sqlite3'))),false);assert.equal(adapter.validateMetadata({...roles(REQUIRED),districtId:'tx-01'}),false);
 const gz=path.join(dir,'gz');fs.mkdirSync(gz);const objects={},list=[];
 for(const [role,data] of Object.entries({...district('co-05'),'accounts.sqlite3':sqlite()})){objects[role]=path.join(gz,role);fs.writeFileSync(objects[role],zlib.gzipSync(data));list.push({role,rawSize:data.length});}
 const revision={revisionId:crypto.randomUUID(),districtId:'co-05',objects:list};
 assert.equal(await adapter.validate({revision,objects}),true);
 assert.deepEqual(fs.readdirSync(path.join(dir,'packs','staging',revision.revisionId)).sort(),[...ALLOWED].sort());
});
test('a data directory in WAL mode is refused and nothing is activated',async t=>{
 const dir=sandbox(t),w=world([]);w.grant.districtIds.push('co-05');w.publish('co-05',{'contacts.sqlite3':sqlite({wal:true})});
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w)),error=>error instanceof DistrictDataStop&&/WAL mode/.test(error.message));
 assert.equal(fs.existsSync(place(dir,'co-05')),false);assert.equal(fs.existsSync(path.join(dir,'packs','staging',w.revisions[0].revisionId)),false);assert.deepEqual(w.checked,[]);
});
test('a damaged gzip object is caught by its checksum before anything is activated',async t=>{
 const dir=sandbox(t),w=world([]);w.grant.districtIds.push('co-05');const bad=zlib.gzipSync(sqlite());bad[bad.length-8]^=0xff;w.publish('co-05',{'scores.sqlite3':{gz:bad,rawSize:4096}});
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w)),error=>error instanceof DistrictDataStop&&/data check/.test(error.message));
 assert.equal(fs.existsSync(place(dir,'co-05')),false);assert.deepEqual(w.checked,[]);
});
test('a failed district check discards the data and releases the wake lock; a refused check never reaches the data directory',async t=>{
 const dir=sandbox(t),w=world(['co-05']);let held=0,released=0;
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{check:async()=>false,keepAwake:()=>{held++;return()=>{released++;};}})),/failed verification/);
 assert.equal(fs.existsSync(place(dir,'co-05')),false);assert.deepEqual([held,released],[1,1]);
 const journal=JSON.parse(fs.readFileSync(path.join(dir,'fixture-journal.json'),'utf8'));assert.deepEqual(journal.revisions,{});assert.deepEqual(journal.cursors,{});
});
test('the district check runs the backend probe on the staging directory, not on a selected data directory',async t=>{
 const dir=sandbox(t),resources=path.join(dir,'resources'),staging=path.join(dir,'staging');fs.mkdirSync(resources);fs.mkdirSync(staging);const calls=[];
 const check=districtCheck({resources,probe:async(command,mode,payload,probeOptions)=>{calls.push({mode,payload,probeOptions});return {district:payload.district,validated:true};},command:()=>({binary:'backend',args:[],runtime:path.join(resources,'runtime','terrier')})});
 assert.equal(await check(staging,'or-05'),true);
 assert.equal(calls[0].mode,'--desktop-district-check');assert.deepEqual(calls[0].payload,{sourceRoot:path.join(resources,'runtime','terrier','sources'),dataRoot:staging,district:'or-05',legacy:false});
 assert.equal(await districtCheck({resources,probe:async()=>({district:'co-05',validated:true}),command:()=>({runtime:resources})})(staging,'or-05'),false);
 await assert.rejects(check(path.join(resources,'inside'),'or-05'),/separate from installed application files/);
});
test('a crash between the renames is completed by recovery, in every interrupted state',async t=>{
 const dir=sandbox(t),root=path.join(dir,'packs');let renames=0;
 const adapter=createAdapter({root,check:async()=>true,rename:async(from,to)=>{if(++renames===2)throw new Error('power cut');return fsp.rename(from,to);}});
 const gz=path.join(dir,'gz');fs.mkdirSync(gz);const objects={},list=[];
 for(const [role,data] of Object.entries(district('co-05'))){objects[role]=path.join(gz,role);fs.writeFileSync(objects[role],zlib.gzipSync(data));list.push({role,rawSize:data.length});}
 const revision={revisionId:crypto.randomUUID(),districtId:'co-05',objects:list},staging=path.join(root,'staging',revision.revisionId),aside=staging+'.old',live=path.join(root,'stores','districts','co-05');
 fs.mkdirSync(live,{recursive:true});fs.writeFileSync(path.join(live,'marker.txt'),'old data');
 assert.equal(await adapter.validate({revision,objects}),true);
 await assert.rejects(adapter.activate({revision}),/power cut/);
 // old data moved aside, new data still in staging, nothing in place
 assert.equal(fs.existsSync(live),false);assert.equal(fs.readFileSync(path.join(aside,'marker.txt'),'utf8'),'old data');assert.ok(fs.existsSync(path.join(staging,'contacts.sqlite3')));
 assert.equal((await adapter.recoverActivation({revision})).activated,true);
 assert.deepEqual(fs.readdirSync(live).sort(),[...ALLOWED].sort());assert.equal(fs.existsSync(aside),false);assert.equal(fs.existsSync(staging),false);
 // new data in place, old data left over: only the cleanup remains
 fs.mkdirSync(aside);fs.writeFileSync(path.join(aside,'marker.txt'),'old data');
 assert.equal((await adapter.recoverActivation({revision})).activated,true);assert.equal(fs.existsSync(aside),false);assert.deepEqual(fs.readdirSync(live).sort(),[...ALLOWED].sort());
 // nothing in place and nothing staged: recovery must not pretend
 fs.rmSync(live,{recursive:true});await assert.rejects(adapter.recoverActivation({revision}),/missing after activation/);
});
test('an interrupted activation resumes from the cached objects without downloading again',async t=>{
 const dir=sandbox(t),w=world(['co-05']);let renames=0;
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{rename:async(from,to)=>{if(++renames===1)throw new Error('power cut');return fsp.rename(from,to);}})),error=>error instanceof DistrictDataStop&&/could not be prepared/.test(error.message));
 assert.equal(fs.existsSync(place(dir,'co-05')),false);const fetched=rangeCalls(w).length;assert.ok(fetched>0);
 assert.deepEqual((await ensureDistrictData(w.gate,options(dir,w))).downloaded,['co-05']);
 assert.equal(rangeCalls(w).length,fetched,'cached gzip objects were reused');assert.deepEqual(fs.readdirSync(place(dir,'co-05')).sort(),[...ALLOWED].sort());
});
test('districts that are not approved are pruned from the store and the journal; approved ones stay',async t=>{
 const dir=sandbox(t),w=world(['co-05','or-05']);await ensureDistrictData(w.gate,options(dir,w));
 fs.mkdirSync(path.join(dir,'packs','stores','districts','junk'));w.grant.districtIds=['co-05'];const before=w.calls.length;
 // The backend's state for each district (accounts, snapshots, secrets) sits beside the store, not in the data directory.
 for(const id of ['co-05','or-05','junk']){fs.mkdirSync(path.join(dir,'packs','state','districts',id),{recursive:true});fs.writeFileSync(path.join(dir,'packs','state','districts',id,'accounts.sqlite3'),'accounts '+id);}
 fs.mkdirSync(path.join(dir,'packs','state','districts','ct-01'),{recursive:true});
 const result=await ensureDistrictData(w.gate,options(dir,w));
 assert.deepEqual(result,{districts:['co-05'],downloaded:[]});assert.deepEqual(fs.readdirSync(path.join(dir,'packs','stores','districts')),['co-05']);
 const journal=JSON.parse(fs.readFileSync(path.join(dir,'fixture-journal.json'),'utf8'));
 assert.ok(Object.values(journal.revisions).length===1&&Object.values(journal.revisions).every(row=>row.districtId==='co-05'));assert.deepEqual(Object.keys(journal.cursors),['co-05']);
 assert.equal(w.calls.length,before,'pruning needs no network');
 assert.deepEqual(fs.readdirSync(path.join(dir,'packs','state','districts')),['co-05'],'state of unapproved districts is deleted with their data');
 assert.equal(fs.readFileSync(path.join(dir,'packs','state','districts','co-05','accounts.sqlite3'),'utf8'),'accounts co-05');
 // No usable approval is not the same as approval for nothing: nothing is pruned.
 w.grant.districtIds=[];assert.deepEqual(await ensureDistrictData(w.gate,options(dir,w)),{skipped:'unapproved'});assert.deepEqual(fs.readdirSync(path.join(dir,'packs','stores','districts')),['co-05']);
});
test('with every approved district present no request, refresh or wake lock happens, and leftovers are swept',async t=>{
 const dir=sandbox(t),w=world(['co-05','or-05','ct-01']);await ensureDistrictData(w.gate,options(dir,w));
 fs.mkdirSync(path.join(dir,'packs','staging','old'),{recursive:true});fs.writeFileSync(path.join(dir,'packs','dataset-sync','objects','aa.bin'),'x');
 const calls=w.calls.length;let held=0;w.fail=()=>{throw new Error('the network must not be used');};
 assert.deepEqual(await ensureDistrictData(w.gate,options(dir,w,{keepAwake:()=>{held++;return()=>{};}})),{districts:['co-05','or-05','ct-01'],downloaded:[]});
 assert.equal(w.calls.length,calls);assert.equal(w.refreshes,0);assert.equal(held,0);assert.equal(fs.existsSync(path.join(dir,'packs','staging')),false);assert.equal(fs.existsSync(path.join(dir,'packs','dataset-sync','objects')),false);
});
test('legacy-owner installs are never touched',async t=>{
 const dir=sandbox(t),w=world(['co-05']);
 assert.deepEqual(await ensureDistrictData(w.gate,options(dir,w,{settings:{district:'co-05',terrierLegacy:{schemaVersion:1}}})),{skipped:'legacy'});
 assert.equal(fs.existsSync(path.join(dir,'packs')),false);assert.deepEqual(w.calls,[]);
});
test('a tester who chose their own data directory is left alone, while the managed store is used when named',async t=>{
 const dir=sandbox(t),w=world(['co-05']);
 assert.deepEqual(await ensureDistrictData(w.gate,options(dir,w,{settings:{district:'co-05',dataDirectory:path.join(dir,'chosen')}})),{skipped:'own-data-directory'});
 assert.equal(fs.existsSync(path.join(dir,'packs')),false);assert.deepEqual(w.calls,[]);
 assert.deepEqual((await ensureDistrictData(w.gate,options(dir,w,{settings:{district:'co-05',dataDirectory:path.join(dir,'packs','stores')}}))).downloaded,['co-05']);
});
test('unreachable, timeout and identity failures back off 5 s doubling to 5 min',async t=>{
 const dir=sandbox(t),w=world(['co-05']);let failures=7;w.fail=relative=>failures-->0&&relative.includes('/catalog')?failure(failures%3===0?'timeout':failures%3===1?'identity':'unreachable'):null;
 await ensureDistrictData(w.gate,options(dir,w));assert.deepEqual(w.sleeps,[5000,10000,20000,40000,80000,160000,300000]);
 assert.ok(w.statuses.some(text=>/^Waiting for the connection\. Retrying in 5 s\.$/.test(text)));assert.ok(w.statuses.some(text=>/Retrying in 5 min\./.test(text)));
});
test('a 429 waits 60 s, a 503 backs off, and a refusal refreshes approval and stops with a status',async t=>{
 let dir=sandbox(t),w=world(['co-05']),once=true;w.fail=relative=>once&&relative.includes('/catalog')?(once=false,failure('refused',429)):null;
 await ensureDistrictData(w.gate,options(dir,w));assert.deepEqual(w.sleeps,[60000]);
 dir=sandbox(t);w=world(['co-05']);once=true;w.fail=relative=>once&&relative.includes('/catalog')?(once=false,failure('refused',503)):null;
 await ensureDistrictData(w.gate,options(dir,w));assert.deepEqual(w.sleeps,[5000]);
 for(const code of [403,401,404]){
  dir=sandbox(t);w=world(['co-05']);w.fail=relative=>relative.includes('/catalog')?failure('refused',code):null;
  await assert.rejects(ensureDistrictData(w.gate,options(dir,w)),error=>error instanceof DistrictDataStop&&/refused/.test(error.message));
  assert.equal(w.refreshes,1);assert.deepEqual(w.sleeps,[]);assert.equal(fs.existsSync(place(dir,'co-05')),false);
 }
});
test('a timeout halves the range size, down to 1 MiB, and the download completes',async t=>{
 const dir=sandbox(t),w=world([]);w.grant.districtIds.push('co-05');w.publish('co-05',{'scores.sqlite3':sqlite({size:5*1024*1024})});let timeouts=2;
 w.fail=relative=>Number(new URL(relative,'https://127.0.0.1').searchParams.get('length'))>=CHUNK/2&&timeouts>0&&(timeouts--,true)?failure('timeout'):null;
 await ensureDistrictData(w.gate,options(dir,w));
 const first=w.attempts.indexOf(CHUNK);assert.ok(first>=0,'the first range of the large object is 4 MiB');assert.equal(w.attempts[first+1],CHUNK/2,'then 2 MiB');
 const afterTimeouts=w.attempts.slice(first+2);assert.ok(afterTimeouts.length>0&&afterTimeouts.every(length=>length<=CHUNK/4),'ranges after two timeouts are at most 1 MiB: '+afterTimeouts);
 assert.equal(fs.statSync(path.join(place(dir,'co-05'),'scores.sqlite3')).size,5*1024*1024);
});
test('a cancelled download stops promptly, even while waiting to retry',async t=>{
 const dir=sandbox(t),w=world(['co-05']),control=new AbortController();w.fail=()=>{setTimeout(()=>control.abort(),20);return failure('unreachable');};
 const started=Date.now();await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{sleep:undefined,signal:control.signal})),/canceled/);assert.ok(Date.now()-started<2000);
});
const signedBytes=(row,field)=>JSON.parse(Buffer.from(row.envelope.payload,'base64')).objects.reduce((sum,item)=>sum+item[field],0);
const stopsForSpace=error=>error instanceof DistrictDataStop&&/^Not enough disk space: need \d+\.\d GB free\.$/.test(error.message);
const cached=dir=>fs.readdirSync(path.join(dir,'packs','dataset-sync','objects')).filter(name=>name.endsWith('.bin'));
test('free space is checked per district before its download, as gzip plus unzipped bytes, and stops before any object is requested',async t=>{
 const dir=sandbox(t),w=world(['co-05','or-05']);
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{statfs:async()=>({bavail:1,bsize:1000})})),stopsForSpace);
 assert.deepEqual(rangeCalls(w),[]);assert.equal(fs.existsSync(place(dir,'co-05')),false);
 // A district needs sum(size+rawSize) of its objects: the old 2.5x gzip estimate is gone.
 const need=w.revisions.map(row=>signedBytes(row,'size')+signedBytes(row,'rawSize'));
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{statfs:async()=>({bavail:need[0]-1,bsize:1})})),stopsForSpace);assert.deepEqual(rangeCalls(w),[]);
 assert.equal((await ensureDistrictData(w.gate,options(dir,w,{statfs:async()=>({bavail:Math.max(...need),bsize:1})}))).downloaded.length,2);
 // The second district is checked on its own: room for the first only stops it, and the first stays usable.
 const other=sandbox(t),v=world(['co-05','or-05']);let calls=0;
 await assert.rejects(ensureDistrictData(v.gate,options(other,v,{statfs:async()=>({bavail:++calls<=2?1e12:1,bsize:1})})),stopsForSpace);
 assert.deepEqual(fs.readdirSync(path.join(other,'packs','stores','districts')),['co-05']);
});
test('a disk that fills up after the download stops before unzipping, keeps the downloaded cache and resumes from it',async t=>{
 const dir=sandbox(t),w=world(['co-05']);let calls=0;
 // call 1 is the check before the download, call 2 the one before unzipping (the gzip bytes are on disk by then)
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{statfs:async()=>({bavail:++calls===1?1e12:1,bsize:1})})),stopsForSpace);
 const fetched=rangeCalls(w).length;assert.ok(fetched>0);assert.equal(fs.existsSync(place(dir,'co-05')),false);
 assert.equal(Object.keys(JSON.parse(fs.readFileSync(path.join(dir,'fixture-journal.json'),'utf8')).revisions).length,1,'the revision is not forgotten');
 assert.ok(cached(dir).length>0,'downloaded objects are kept');
 assert.deepEqual((await ensureDistrictData(w.gate,options(dir,w))).downloaded,['co-05']);assert.equal(rangeCalls(w).length,fetched,'nothing was downloaded again');
});
test('ENOSPC and EBUSY are reported as themselves, never as a failed verification, and the downloaded cache is kept',async t=>{
 for(const [code,reported] of [['ENOSPC',stopsForSpace],['EBUSY',error=>error instanceof DistrictDataStop&&/in use by another program/.test(error.message)]]){
  // while unzipping or verifying
  const dir=sandbox(t),w=world(['co-05']),fail=Object.assign(new Error('fixture '+code),{code});
  await assert.rejects(ensureDistrictData(w.gate,options(dir,w,{check:async()=>{throw fail;}})),error=>reported(error)&&!/verification|discarded/.test(error.message)&&error.cause===fail,code);
  const fetched=rangeCalls(w).length;assert.equal(fs.existsSync(place(dir,'co-05')),false);assert.equal(fs.existsSync(path.join(dir,'packs','staging',w.revisions[0].revisionId)),false);
  assert.equal(Object.keys(JSON.parse(fs.readFileSync(path.join(dir,'fixture-journal.json'),'utf8')).revisions).length,1,code+': not forgotten');assert.ok(cached(dir).length>0,code+': cache kept');
  assert.deepEqual((await ensureDistrictData(w.gate,options(dir,w))).downloaded,['co-05']);assert.equal(rangeCalls(w).length,fetched,code+': resumed from the cache');
 }
 // while a range is being written
 const dir=sandbox(t),w=world(['co-05']);w.fail=relative=>relative.includes('/objects/')?Object.assign(new Error('fixture write'),{code:'ENOSPC'}):null;
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w)),stopsForSpace);assert.equal(fs.existsSync(place(dir,'co-05')),false);
 w.fail=null;assert.deepEqual((await ensureDistrictData(w.gate,options(dir,w))).downloaded,['co-05']);
});
test('gunzipTo enforces the signed rawSize: output past it is cut off, output short of it is refused, and a missing size is refused',async t=>{
 const dir=sandbox(t),source=path.join(dir,'object.gz'),bomb=Buffer.alloc(8*1024*1024);fs.writeFileSync(source,zlib.gzipSync(bomb));
 let n=0;const out=()=>path.join(dir,'out'+(n++));
 const exact=out();await gunzipTo(source,exact,bomb.length);assert.equal(fs.statSync(exact).size,bomb.length);
 const over=out();await assert.rejects(gunzipTo(source,over,1000),/larger than its declared size/);assert.ok((fs.existsSync(over)?fs.statSync(over).size:0)<=1000,'never more than rawSize bytes reach the disk');
 await assert.rejects(gunzipTo(source,out(),bomb.length+1),/smaller than its declared size/);
 for(const missing of [undefined,null,-1,1.5,'8','NaN'])await assert.rejects(gunzipTo(source,out(),missing==='NaN'?NaN:missing),/no declared size/,String(missing));
});
test('an object whose gzip is larger or smaller than its signed rawSize discards the district and activates nothing',async t=>{
 for(const [rawSize,pattern] of [[3,/density\.json is larger than its declared size/],[100,/density\.json is smaller than its declared size/]]){
  const dir=sandbox(t),w=world([]);w.grant.districtIds.push('co-05');w.publish('co-05',{'density.json':{gz:zlib.gzipSync(Buffer.from('{"co-05":1}')),rawSize}});
  await assert.rejects(ensureDistrictData(w.gate,options(dir,w)),error=>error instanceof DistrictDataStop&&/failed verification/.test(error.message)&&pattern.test(error.message));
  assert.equal(fs.existsSync(place(dir,'co-05')),false);assert.equal(fs.existsSync(path.join(dir,'packs','staging',w.revisions[0].revisionId)),false);assert.deepEqual(w.checked,[]);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir,'fixture-journal.json'),'utf8')).revisions,{},'a lying object is forgotten, not retried from the cache');
 }
});
test('a district with nothing published, and a journal left by an earlier approval, are handled',async t=>{
 let dir=sandbox(t),w=world([]);w.grant.districtIds.push('ct-01');
 await assert.rejects(ensureDistrictData(w.gate,options(dir,w)),error=>error instanceof DistrictDataStop&&/No private data has been published for Connecticut 01/.test(error.message));
 dir=sandbox(t);w=world(['co-05']);await ensureDistrictData(w.gate,options(dir,w));fs.rmSync(path.join(dir,'packs','stores'),{recursive:true});
 const later=world(['co-05']);assert.deepEqual((await ensureDistrictData(later.gate,options(dir,later))).downloaded,['co-05']);
});
test('the folders the backend reads and writes are exactly the ones the downloader manages',t=>{
 const dir=sandbox(t),roots=runtimeRoots('terrier',path.join(dir,'profile'),{dataDirectory:path.join(dir,'packs','stores'),district:'or-05'});
 assert.equal(roots.data,place(dir,'or-05'));assert.equal(roots.state,path.join(dir,'packs','state','districts','or-05'));
});
test('the default district is the first approved one unless the chosen district is approved',()=>{
 assert.equal(defaultDistrict({district:'co-05'},['or-05','ct-01']),'or-05');assert.equal(defaultDistrict({},['ct-01','or-05']),'ct-01');
 assert.equal(defaultDistrict({district:'or-05'},['co-05','or-05']),'or-05');assert.equal(defaultDistrict({district:'co-05'},[]),'co-05');
 assert.equal(defaultDistrict({district:'co-05',terrierLegacy:{}},['or-05']),'co-05','the owner PC keeps its own district');
});
test('only testers still on the old default data directory move to the private store',()=>{
 const userData=path.join(os.tmpdir(),'profile'),root=path.join(os.tmpdir(),'MelangeData','terrier'),old=path.join(userData,'data');
 assert.deepEqual(migrateSettings({dataDirectory:old,district:'or-05'},{userData,root}),{dataDirectory:path.join(root,'stores'),district:'or-05'});
 assert.deepEqual(migrateSettings({dataDirectory:path.join(userData,'.','data')+path.sep,district:'co-05'},{userData,root}),{dataDirectory:path.join(root,'stores'),district:'co-05'});
 for(const settings of [{dataDirectory:path.join(root,'stores')},{dataDirectory:path.join(os.tmpdir(),'chosen')},{dataDirectory:old,terrierLegacy:{}},{dataDirectory:old,terrierDistricts:{}},{}])assert.equal(migrateSettings(settings,{userData,root}),null);
 if(process.platform==='win32')assert.ok(migrateSettings({dataDirectory:old.toUpperCase()},{userData,root}));
});
test('the store root is under LOCALAPPDATA when installed, and inside the profile for isolated or unpackaged runs',()=>{
 const base={userData:path.join(os.tmpdir(),'t','Documents','MelangeApplicationState','terrier'),localAppData:path.join(os.tmpdir(),'t','AppData','Local'),platform:'win32'};
 assert.equal(storeRoot({...base,packaged:true,isolated:false}),path.join(base.localAppData,'MelangeData','terrier'));
 assert.equal(storeRoot({...base,packaged:true,isolated:true}),path.join(base.userData,'packs'));assert.equal(storeRoot({...base,packaged:false,isolated:false}),path.join(base.userData,'packs'));
 assert.equal(storeRoot({...base,packaged:true,isolated:false,platform:'linux'}),path.join(base.userData,'packs'));
});
test('isolated test profiles keep the store inside their boundary and refuse a redirected store before any request',{skip:process.platform!=='win32'},async t=>{
 const dir=sandbox(t),iso=path.join(dir,'iso'),production=path.join(dir,'Documents','MelangeApplicationState'),outside=path.join(dir,'elsewhere'),userData=path.join(iso,'terrier');
 for(const folder of [userData,production,outside])fs.mkdirSync(folder,{recursive:true});
 const boundary={root:iso,forbidden:[production]},root=storeRoot({userData,localAppData:path.join(dir,'Local'),packaged:true,isolated:true,platform:'win32'});
 assert.equal(root,path.join(userData,'packs'));confineIsolatedPath(boundary,root);
 assert.equal(confineIsolatedSettings(boundary,{dataDirectory:path.join(root,'stores'),district:'co-05'}).dataDirectory,path.join(root,'stores'));
 assert.throws(()=>confineIsolatedSettings(boundary,{dataDirectory:path.join(outside,'stores')}),/outside the isolated test profile/);
 const w=world(['co-05']);fs.symlinkSync(outside,root,'junction');
 await assert.rejects(ensureDistrictData(w.gate,{...options(dir,w),root,confine:item=>confineIsolatedPath(boundary,item,'The district data store')}),/outside the isolated test profile/);
 assert.deepEqual(w.calls,[]);assert.deepEqual(fs.readdirSync(outside),[]);
 fs.rmSync(root,{recursive:true});assert.equal((await ensureDistrictData(w.gate,{...options(dir,w),root,confine:item=>confineIsolatedPath(boundary,item,'The district data store')})).downloaded.length,1);
 assert.ok(fs.existsSync(path.join(root,'stores','districts','co-05','scores.sqlite3')));
});
test('learning of revocation deletes the whole private store and the journal, and refuses to delete anything else',async t=>{
 const dir=sandbox(t),w=world(['co-05','or-05']),journal=journalStore(dir);await ensureDistrictData(w.gate,options(dir,w,{journal}));
 fs.mkdirSync(path.join(dir,'packs','state','districts','co-05'),{recursive:true});fs.writeFileSync(path.join(dir,'packs','state','districts','co-05','accounts.sqlite3'),'door-knock results');
 for(const folder of [sessionDirectory(path.join(dir,'packs')),logDirectory(path.join(dir,'packs'))]){fs.mkdirSync(path.join(folder,'Cache'),{recursive:true});fs.writeFileSync(path.join(folder,'Cache','page'),'voter page');}
 assert.ok(fs.existsSync(place(dir,'co-05'))&&await journal.read());
 await removeStore(path.join(dir,'packs'),{journal});
 assert.equal(fs.existsSync(path.join(dir,'packs')),false,'data, district state, Chromium session data and logs are all gone');assert.equal(await journal.read(),null);
 await removeStore(path.join(dir,'packs'),{journal});
 fs.mkdirSync(path.join(dir,'Documents'));fs.writeFileSync(path.join(dir,'Documents','notes.txt'),'mine');
 await assert.rejects(removeStore(path.join(dir,'Documents')),/unexpected district store/);assert.equal(fs.readFileSync(path.join(dir,'Documents','notes.txt'),'utf8'),'mine');
});
test('after the move to the store, the old default data and state district trees are deleted; never for legacy owners, chosen directories or links out of the profile',async t=>{
 const dir=sandbox(t),userData=path.join(dir,'profile'),old=path.join(userData,'data'),dataTree=path.join(old,'districts'),stateTree=path.join(userData,'state','districts');
 const lay=()=>{
  fs.rmSync(userData,{recursive:true,force:true});for(const tree of [dataTree,stateTree])fs.mkdirSync(path.join(tree,'co-05'),{recursive:true});
  fs.writeFileSync(path.join(dataTree,'co-05','scores.sqlite3'),'voters');fs.writeFileSync(path.join(stateTree,'co-05','accounts.sqlite3'),'accounts');fs.writeFileSync(path.join(stateTree,'co-05','.secret'),'secret');
  fs.writeFileSync(path.join(old,'notes.txt'),'mine');fs.writeFileSync(path.join(userData,'state','other.txt'),'mine');
 };
 const intact=()=>fs.existsSync(path.join(dataTree,'co-05','scores.sqlite3'))&&fs.existsSync(path.join(stateTree,'co-05','accounts.sqlite3'))&&fs.existsSync(path.join(stateTree,'co-05','.secret'));
 lay();assert.deepEqual(await removeOldDefaultData({dataDirectory:old,district:'or-05'},{userData}),[dataTree,stateTree]);
 assert.equal(fs.existsSync(dataTree),false);assert.equal(fs.existsSync(stateTree),false);
 assert.equal(fs.readFileSync(path.join(old,'notes.txt'),'utf8'),'mine','only the district trees go');assert.equal(fs.readFileSync(path.join(userData,'state','other.txt'),'utf8'),'mine');
 assert.deepEqual(await removeOldDefaultData({dataDirectory:old},{userData}),[],'idempotent: nothing left to remove');
 // the very test migrateSettings applies gates it
 for(const settings of [{dataDirectory:old,terrierLegacy:{}},{dataDirectory:old,terrierDistricts:{}},{dataDirectory:path.join(dir,'chosen')},{dataDirectory:path.join(dir,'packs','stores')},{}]){
  lay();assert.deepEqual(await removeOldDefaultData(settings,{userData}),[],JSON.stringify(settings));assert.ok(intact(),JSON.stringify(settings));
 }
 // a district folder that is a link out of the profile is somebody else's data
 lay();const outside=path.join(dir,'elsewhere');fs.mkdirSync(path.join(outside,'co-05'),{recursive:true});fs.writeFileSync(path.join(outside,'co-05','scores.sqlite3'),'theirs');
 fs.rmSync(dataTree,{recursive:true});fs.symlinkSync(outside,dataTree,'junction');
 assert.deepEqual(await removeOldDefaultData({dataDirectory:old},{userData}),[stateTree]);assert.equal(fs.readFileSync(path.join(outside,'co-05','scores.sqlite3'),'utf8'),'theirs');
});
test('session data and logs live in the store root: under LOCALAPPDATA for a packaged install, never in the Documents profile',()=>{
 const userData=path.join(os.tmpdir(),'t','Documents','MelangeApplicationState','terrier'),localAppData=path.join(os.tmpdir(),'t','AppData','Local');
 const root=storeRoot({userData,localAppData,packaged:true,isolated:false,platform:'win32'});
 assert.equal(sessionDirectory(root),path.join(localAppData,'MelangeData','terrier','session'));assert.equal(logDirectory(root),path.join(localAppData,'MelangeData','terrier','logs'));
 for(const folder of [sessionDirectory(root),logDirectory(root)])assert.ok(path.relative(userData,folder).startsWith('..'),folder);
 // isolated and unpackaged runs keep the store inside their profile, so they keep these there too
 assert.equal(path.relative(userData,sessionDirectory(storeRoot({userData,localAppData,packaged:true,isolated:true,platform:'win32'}))),path.join('packs','session'));
});
test('the backend log goes to the store logs when the runtime is given one (not for the legacy owner), and to the profile otherwise',async t=>{
 const dir=sandbox(t),resources=path.join(dir,'resources'),backend=path.join(resources,'runtime','terrier'),userData=path.join(dir,'profile'),root=path.join(dir,'packs'),stores=path.join(root,'stores');
 fs.mkdirSync(backend,{recursive:true});fs.writeFileSync(path.join(backend,process.platform==='win32'?'desktop-backend.exe':'desktop-backend'),'');fs.mkdirSync(stores,{recursive:true});fs.mkdirSync(userData);
 const authorize=async()=>({accepted:{grant:{appId:'terrier'},revoked:false},principal:{districtId:'co-05'}});
 // confine is called with the log folder just before the backend would be spawned; stopping there keeps the test from running a backend.
 const logsFor=async(logRoot,configuration={dataDirectory:stores,district:'co-05'})=>{let seen=null;await assert.rejects(new Runtime('terrier',userData,resources,{authorize,logRoot,confine:paths=>{if(paths.length<3){seen=paths[0];throw new Error('stop before spawning');}}}).start(configuration),/stop before spawning/);return seen;};
 assert.equal(await logsFor(logDirectory(root)),path.join(root,'logs'));assert.equal(await logsFor(null),path.join(userData,'logs'));
 // the existing owner's connection (terrierLegacy) keeps its logs in the profile even when a store log folder is offered
 const legacy=path.join(dir,'legacy');fs.mkdirSync(legacy);for(const name of ['accounts.sqlite3','.secret'])fs.writeFileSync(path.join(legacy,name),'x');
 assert.equal(await logsFor(logDirectory(root),{dataDirectory:legacy,district:'co-05',terrierLegacy:{schemaVersion:1,district:'co-05',firstPasswordOwner:'admin',dataDirectory:legacy}}),path.join(userData,'logs'));
});
test('main.cjs points Chromium session data at the store before the app is ready, for non-legacy Terrier only, and hands the runtime the store logs',()=>{
 const main=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8'),at=text=>{const index=main.indexOf(text);assert.notEqual(index,-1,text);assert.equal(main.indexOf(text,index+1),-1,'single occurrence: '+text);return index;};
 const set=at("if(packs&&startSettings.terrierLegacy===undefined)app.setPath('sessionData',packs.sessionDirectory(packRoot));");
 assert.ok(at('const packRoot=')<set&&set<at('app.whenReady()')&&set<at("session.fromPartition('persist:'"),'set synchronously at load: before ready and before any session exists');
 at('logRoot:packs?packs.logDirectory(packRoot):null');at('shell.openPath(districtLogs())');
 assert.ok(fs.readFileSync(path.join(__dirname,'..','runtime.cjs'),'utf8').includes("logs=this.logRoot&&!roots.legacyOwner?this.logRoot:path.join(this.userData,'logs')"));
});
test('a district whose bundle could not be imported is reported by name; no marker, no notice',t=>{
 const dir=sandbox(t),state=path.join(dir,'state');fs.mkdirSync(state);
 assert.equal(bundleImportNotice({state,district:'or-05'}),null);assert.equal(bundleImportNotice(null),null);assert.equal(bundleImportNotice({state:null}),null);
 fs.writeFileSync(path.join(state,'bundle-import-failed'),'ValueError: Bundle checksum does not match\n');
 assert.equal(bundleImportNotice({state,district:'or-05'}),'District data for Oregon 05 could not be imported; it will retry on next start.');
 fs.rmSync(path.join(state,'bundle-import-failed'));assert.equal(bundleImportNotice({state,district:'or-05'}),null);
});
