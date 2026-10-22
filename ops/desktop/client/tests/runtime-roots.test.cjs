'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {runtimeRoots,prepareRuntimeRoots,validatedLaunchPath,refuseInstalledOverlap}=require('../runtime-roots.cjs');
const {Runtime,health}=require('../runtime.cjs');

function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-district-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));const user=path.join(root,'profile'),data=path.join(root,'data');fs.mkdirSync(data);return {root,user,data};}

test('data cannot equal, contain or sit inside installed files, including a junction alias',t=>{
  const {root,data}=fixture(t),installed=path.join(root,'installed'),nested=path.join(installed,'nested');fs.mkdirSync(nested,{recursive:true});
  for(const selected of [root,installed,nested])assert.throws(()=>refuseInstalledOverlap(selected,[installed]),/separate from installed/);
  const alias=path.join(root,'alias');fs.symlinkSync(installed,alias,process.platform==='win32'?'junction':'dir');
  assert.throws(()=>refuseInstalledOverlap(path.join(alias,'nested'),[installed]),/separate from installed/);
  refuseInstalledOverlap(data,[installed]);
  fs.writeFileSync(path.join(data,'existing.csv'),'preserved');
  const other=prepareRuntimeRoots('melange',path.join(root,'profile'),{dataDirectory:data},[installed]);
  assert.equal(other.data,data);assert.equal(fs.readFileSync(path.join(data,'existing.csv'),'utf8'),'preserved');
});

test('Terrier districts isolate writable data and accounts while preserving other app roots',t=>{
  const {user,data}=fixture(t);
  const colorado=prepareRuntimeRoots('terrier',user,{dataDirectory:data,district:'co-05'});
  const oregon=prepareRuntimeRoots('terrier',user,{dataDirectory:data,district:'or-05'});
  assert.notEqual(colorado.data,oregon.data);assert.notEqual(colorado.state,oregon.state);
  fs.writeFileSync(path.join(colorado.state,'account.db'),'Colorado fixture');
  assert.equal(fs.existsSync(path.join(oregon.state,'account.db')),false);
  assert.deepEqual(runtimeRoots('mentat',user,{dataDirectory:data}),{data,state:path.join(user,'state')});
});

test('the managed private store keeps district state beside it, outside the profile; every other layout is unchanged',t=>{
  const {root,user}=fixture(t);
  // Installed (.../terrier) and isolated (<profile>/packs) store roots.
  for(const store of [path.join(root,'terrier'),path.join(user,'packs')]){
    const stores=path.join(store,'stores');fs.mkdirSync(stores,{recursive:true});
    const roots=prepareRuntimeRoots('terrier',user,{dataDirectory:stores,district:'or-05'});
    assert.deepEqual(roots,{data:path.join(stores,'districts','or-05'),state:path.join(store,'state','districts','or-05'),district:'or-05'});
    assert.ok(fs.statSync(roots.state).isDirectory());assert.ok(fs.statSync(roots.data).isDirectory());
    assert.deepEqual(runtimeRoots('terrier',user,{dataDirectory:stores,district:'co-05'}).state,path.join(store,'state','districts','co-05'));
  }
  assert.equal(fs.existsSync(path.join(user,'state')),false,'nothing is created in the profile state folder');
  // Earlier state in the profile is neither read nor refused nor touched; only the store's own state folder is checked.
  const store=path.join(root,'terrier');fs.mkdirSync(path.join(user,'state'),{recursive:true});fs.writeFileSync(path.join(user,'state','users.sqlite3'),'earlier accounts');
  prepareRuntimeRoots('terrier',user,{dataDirectory:path.join(store,'stores'),district:'co-05'});assert.equal(fs.readFileSync(path.join(user,'state','users.sqlite3'),'utf8'),'earlier accounts');
  fs.writeFileSync(path.join(store,'state','stray.sqlite3'),'x');assert.throws(()=>prepareRuntimeRoots('terrier',user,{dataDirectory:path.join(store,'stores'),district:'co-05'}),/unscoped layout/);
  // A data directory chosen by hand, even one named stores, keeps the profile state; so do the legacy owner and the other apps.
  const own=path.join(root,'mine','stores');fs.mkdirSync(own,{recursive:true});
  assert.equal(runtimeRoots('terrier',user,{dataDirectory:own,district:'co-05'}).state,path.join(user,'state','districts','co-05'));
  const stores=path.join(store,'stores'),legacy={schemaVersion:1,district:'co-05',firstPasswordOwner:'admin',dataDirectory:stores};
  assert.deepEqual(runtimeRoots('terrier',user,{dataDirectory:stores,district:'co-05',terrierLegacy:legacy}),{data:stores,state:stores,district:'co-05',legacyOwner:'admin'});
  assert.deepEqual(runtimeRoots('melange',user,{dataDirectory:stores}),{data:stores,state:path.join(user,'state')});
});

test('unsupported and traversing districts never create data roots',t=>{
  const {user,data}=fixture(t);
  for(const district of ['../co-05','../../outside','CO-05','co-05/../../or-05','co-99',{}])assert.throws(()=>prepareRuntimeRoots('terrier',user,{dataDirectory:data,district}),/Unsupported/);
  assert.deepEqual(fs.readdirSync(data),[]);assert.equal(fs.existsSync(user),false);
});

test('legacy unscoped Terrier data requires explicit migration and remains unchanged',t=>{
  const {user,data}=fixture(t);fs.mkdirSync(path.join(user,'state'),{recursive:true});
  fs.writeFileSync(path.join(user,'state','users.sqlite3'),'existing accounts');
  assert.throws(()=>prepareRuntimeRoots('terrier',user,{dataDirectory:data,district:'co-05'}),/unscoped layout/);
  assert.equal(fs.readFileSync(path.join(user,'state','users.sqlite3'),'utf8'),'existing accounts');
  assert.deepEqual(fs.readdirSync(data),[]);
});

test('running backend refuses switching district or selected data without stopping',async t=>{
  const {user,data}=fixture(t),runtime=new Runtime('terrier',user,'unused',{authorize:async()=>({accepted:{grant:{},revoked:false}})});
  const config={dataDirectory:data,district:'co-05'};runtime.child={};runtime.roots=runtimeRoots('terrier',user,config);runtime.origin='http://127.0.0.1:12345';
  assert.equal(await runtime.start(config),runtime.origin);
  await assert.rejects(()=>runtime.start({...config,district:'or-05'}),/Close the app/);
  await assert.rejects(()=>runtime.start({...config,dataDirectory:path.join(data,'new')}),/Close the app/);
});

test('explicit Terrier legacy connection preserves exact district account and secret roots',t=>{
  const {user,data}=fixture(t),legacy={schemaVersion:1,district:'co-05',firstPasswordOwner:'admin',dataDirectory:data};
  fs.writeFileSync(path.join(data,'accounts.sqlite3'),'untouched database');fs.writeFileSync(path.join(data,'.secret'),'untouched secret');
  const roots=prepareRuntimeRoots('terrier',user,{dataDirectory:data,district:'co-05',terrierLegacy:legacy});
  assert.deepEqual(roots,{data,state:data,district:'co-05',legacyOwner:'admin'});
  assert.equal(fs.readFileSync(path.join(data,'accounts.sqlite3'),'utf8'),'untouched database');
  assert.equal(fs.readFileSync(path.join(data,'.secret'),'utf8'),'untouched secret');
  assert.equal(fs.existsSync(path.join(user,'state')),false);
  for(const override of [{district:'or-05'},{firstPasswordOwner:'owner'},{schemaVersion:2},{unexpected:true},{dataDirectory:user}])assert.throws(()=>prepareRuntimeRoots('terrier',user,{dataDirectory:data,district:'co-05',terrierLegacy:{...legacy,...override}}),/Invalid approved/);
  fs.unlinkSync(path.join(data,'.secret'));
  assert.throws(()=>prepareRuntimeRoots('terrier',user,{dataDirectory:data,district:'co-05',terrierLegacy:legacy}),/accounts and secret/);
  assert.equal(fs.existsSync(path.join(data,'.secret')),false);
});

test('startup path accepts only a narrow local Terrier invitation and never an arbitrary URL',()=>{
  const invite='/?invite='+'a'.repeat(43);assert.equal(validatedLaunchPath('terrier',invite),invite);
  for(const value of ['https://attacker.test/','//attacker.test/','/api/delete','/?invite=short',invite+'&redirect=https://attacker.test',invite+'#fragment'])assert.throws(()=>validatedLaunchPath('terrier',value),/Invalid/);
  assert.throws(()=>validatedLaunchPath('mentat',invite),/Invalid/);
  assert.equal(validatedLaunchPath('terrier',undefined),'/');
});

test('invitation startup metadata requires authenticated health with matching app and narrow path',async()=>{
  let payload={appId:'terrier',launchPath:'/?invite='+'a'.repeat(43)};
  const server=http.createServer((request,response)=>{assert.equal(request.headers['x-desktop-token'],'launch-secret');response.writeHead(200);response.end(JSON.stringify(payload));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
  try{assert.deepEqual(await health(origin,'launch-secret','terrier'),{launchPath:payload.launchPath});payload={...payload,appId:'mentat'};assert.equal(await health(origin,'launch-secret','terrier'),null);payload={appId:'terrier',launchPath:'//attacker.test/'};assert.equal(await health(origin,'launch-secret','terrier'),null);}finally{await new Promise(resolve=>server.close(resolve));}
});
