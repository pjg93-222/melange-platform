'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {selectProfile}=require('../profile.cjs');
test('fresh packaged profiles are stable and separate regardless of AppData virtualization or test override',t=>{
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-profile-'));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
  const options={home,appData:path.join(home,'virtualized-appdata'),packaged:true,testDirectory:path.join(home,'ignored')};
  for(const appId of ['melange','terrier','mentat'])assert.deepEqual(selectProfile({...options,appId}),{directory:path.join(home,'Documents','MelangeApplicationState',appId),migrationRequired:false});
});
test('meaningful legacy profile is retained and blocks fresh profile use',t=>{
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-profile-'));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
  const appData=path.join(home,'appdata'),legacy=path.join(appData,'MelangeApps','terrier');fs.mkdirSync(legacy,{recursive:true});fs.writeFileSync(path.join(legacy,'settings.json'),'preserved settings');
  const result=selectProfile({appId:'terrier',home,appData,packaged:true});assert.equal(result.migrationRequired,true);assert.equal(fs.existsSync(result.directory),false);assert.equal(fs.readFileSync(path.join(legacy,'settings.json'),'utf8'),'preserved settings');
  assert.deepEqual(selectProfile({appId:'terrier',home,appData,packaged:false,testDirectory:path.join(home,'isolated')}),{directory:path.join(home,'isolated'),migrationRequired:false});
});

const {selectStartupProfile,isolatedProfileArgument,confineIsolatedPath,confineIsolatedSettings,ISOLATED_TITLE_SUFFIX}=require('../profile.cjs');
const {APPS}=require('../apps.cjs');
function sandbox(t){
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-isolated-'));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
  const appData=path.join(home,'AppData','Roaming'),install=path.join(home,'Programs','Mentat');fs.mkdirSync(install,{recursive:true});
  return {home,appData,install,options:{home,appData,documents:()=>path.join(home,'Documents'),protectedPaths:()=>[path.join(install,'resources','app.asar'),install]}};
}
test('without the isolated switch every production root, identity and login registration is unchanged',t=>{
  const {home,appData,options}=sandbox(t);
  for(const appId of Object.keys(APPS))for(const packaged of [true,false])for(const argv of [[],['--desktop-updater'],['--melange-isolated-profiles-are-not-this-switch']]){
    const testDirectory=packaged?path.join(home,'ignored'):undefined;
    const selected=selectStartupProfile({...options,appId,argv,packaged,testDirectory});
    assert.deepEqual(selected,{...selectProfile({appId,home,appData,packaged,testDirectory}),isolated:false,applicationUserModelId:APPS[appId].applicationId,
      discoveryCache:path.join(appData,'MelangeDesktop','github-discovery'),registerLoginItem:packaged,nativeInstallation:true});
    assert.equal(selected.directory,path.join(home,'Documents','MelangeApplicationState',appId));
  }
  // The unpackaged development override keeps its existing precedence.
  assert.equal(selectStartupProfile({...options,appId:'mentat',argv:[],packaged:false,testDirectory:path.join(home,'dev')}).directory,path.join(home,'dev'));
  assert.equal(fs.existsSync(path.join(home,'Documents')),false,'production profile selection must not create directories');
});
test('isolated switch moves every writable root under the chosen directory and disables OS registration and native installation',t=>{
  const {home,appData,options}=sandbox(t),root=path.join(home,'acceptance','run 1');
  const legacy=path.join(appData,'MelangeApps','mentat');fs.mkdirSync(legacy,{recursive:true});fs.writeFileSync(path.join(legacy,'settings.json'),'{}');
  const seen=new Set();
  for(const appId of Object.keys(APPS)){
    const selected=selectStartupProfile({...options,appId,argv:['--desktop-updater','--melange-isolated-profile='+root],packaged:true,testDirectory:path.join(home,'ignored')});
    const {boundary,...plan}=selected;
    assert.deepEqual(plan,{directory:path.join(root,appId),migrationRequired:false,isolated:true,root,applicationUserModelId:APPS[appId].applicationId+'.isolated',
      discoveryCache:path.join(root,'github-discovery'),registerLoginItem:false,nativeInstallation:false});
    assert.equal(boundary.root,root);assert.ok(Object.isFrozen(boundary)&&Object.isFrozen(boundary.forbidden));
    assert.ok(boundary.forbidden.includes(path.join(home,'Documents','MelangeApplicationState')));
    assert.ok(fs.statSync(selected.discoveryCache).isDirectory());
    assert.ok(fs.statSync(selected.directory).isDirectory());
    // Electron keys the single-instance lock on userData: isolated instances never share production's or each other's.
    const production=selectProfile({appId,home,appData,packaged:true}).directory;
    assert.notEqual(selected.directory.toLowerCase(),production.toLowerCase());assert.ok(!seen.has(selected.directory));seen.add(selected.directory);
    assert.notEqual(selected.applicationUserModelId,APPS[appId].applicationId);
  }
  assert.equal(fs.existsSync(path.join(home,'Documents','MelangeApplicationState')),false);
  assert.equal(fs.readFileSync(path.join(legacy,'settings.json'),'utf8'),'{}');
  assert.equal(ISOLATED_TITLE_SUFFIX,' [isolated test profile]');
});
test('invalid isolated profile requests are refused before any profile is selected',t=>{
  const {home,appData,install,options}=sandbox(t);
  const state=path.join(home,'Documents','MelangeApplicationState');
  const cases=[
    ['--melange-isolated-profile=relative\\profile',/absolute local directory/],
    ['--melange-isolated-profile=\\rooted-without-drive',/absolute local directory/],
    ['--melange-isolated-profile=\\\\server\\share\\profile',/absolute local directory/],
    ['--melange-isolated-profile=',/missing or invalid/],
    ['--melange-isolated-profile',/exactly one/],
    ['--Melange-Isolated-Profile',/exactly one/],
    ['/melange-isolated-profile='+path.join(home,'x'),/exactly one/],
    ['--melange-isolated-profile='+state,/separate from production/],
    ['--melange-isolated-profile='+path.join(state,'mentat'),/separate from production/],
    ['--melange-isolated-profile='+path.join(home,'Documents'),/separate from production/],
    ['--melange-isolated-profile='+path.join(home,'Documents','MelangeApplications','Mentat'),/separate from production/],
    ['--melange-isolated-profile='+path.join(appData,'MelangeApps','x'),/separate from production/],
    ['--melange-isolated-profile='+path.join(home,'AppData','Roaming','MelangeApps'),/separate from production/],
    ['--melange-isolated-profile='+path.join(install,'profile'),/separate from production/],
    ['--melange-isolated-profile='+path.parse(home).root,/root/]
  ];
  for(const [argument,pattern] of cases){
    assert.throws(()=>selectStartupProfile({...options,appId:'mentat',argv:[argument],packaged:true}),error=>error.code==='ISOLATED_PROFILE_REFUSED'&&pattern.test(error.message),argument);
  }
  assert.throws(()=>isolatedProfileArgument(['--melange-isolated-profile='+path.join(home,'a'),'--melange-isolated-profile='+path.join(home,'b')]),/exactly one/);
  const file=path.join(home,'not-a-directory');fs.writeFileSync(file,'x');
  assert.throws(()=>selectStartupProfile({...options,appId:'mentat',argv:['--melange-isolated-profile='+file],packaged:true}),/not a directory/);
  assert.equal(fs.existsSync(state),false,'refusal must not create production state');
  assert.equal(fs.existsSync(path.join(appData,'MelangeApps')),false);
  assert.equal(fs.existsSync(path.join(home,'x')),false);
});
test('a junction into production state is refused after physical resolution',{skip:process.platform!=='win32'},t=>{
  const {home,options}=sandbox(t),state=path.join(home,'Documents','MelangeApplicationState');fs.mkdirSync(state,{recursive:true});
  const link=path.join(home,'innocent-looking');fs.symlinkSync(state,link,'junction');
  assert.throws(()=>selectStartupProfile({...options,appId:'mentat',argv:['--melange-isolated-profile='+path.join(link,'run')],packaged:true}),/separate from production/);
  assert.deepEqual(fs.readdirSync(state),[]);
});
test('main process applies the isolated plan before userData, lock, login item, discovery cache and updater use',()=>{
  const source=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8');
  const at=text=>{const index=source.indexOf(text);assert.notEqual(index,-1,text);assert.equal(source.indexOf(text,index+1),-1,'single occurrence: '+text);return index;};
  const refuse=at('if(isolationRefused){'),aumid=at('app.setAppUserModelId(profile.applicationUserModelId);'),userData=at("app.setPath('userData',profile.directory);"),lock=at('app.requestSingleInstanceLock()');
  assert.ok(refuse<aumid&&aumid<userData&&userData<lock,'refusal precedes identity; userData precedes the single-instance lock');
  assert.ok(source.slice(refuse,aumid).includes('app.exit(2);}else{'),'refused requests exit without selecting any profile');
  assert.equal(source.match(/setLoginItemSettings\(/g).length,1);at('if(profile.registerLoginItem)app.setLoginItemSettings(');
  assert.equal(source.match(/setAppUserModelId\(/g).length,1);assert.equal(source.match(/setPath\('userData'/g).length,1);
  at('cacheDirectory:profile.discoveryCache');at('nativeInstallation:profile.nativeInstallation');at('let background=!profile.isolated&&app.isPackaged&&(');
  assert.doesNotMatch(source,/setAsDefaultProtocolClient|setUserTasks|setJumpList/);
  assert.doesNotMatch(source,/getPath\('appData'\),'MelangeDesktop'/);
  // Runtime data confinement: on load (before the runtime exists), on change (before write), before backend launch and district verification.
  const load=at("if(profile.isolated)try{confineIsolatedSettings(profile.boundary,read('settings.json'));}catch(error){"),runtime=at('createRuntime=()=>new Runtime(');
  assert.ok(load<runtime&&source.slice(load,runtime).includes('app.quit();return;}'),'invalid loaded settings stop before any runtime is created');
  const change=at("if(profile.isolated)try{confineIsolatedSettings(profile.boundary,settings);}catch(error){updateStatus(error.message+' Settings were not changed.');return read('settings.json');}write('settings.json',settings);");
  assert.ok(change>source.indexOf("ipcMain.handle('desktop:directory'"));
  at("confine:profile.isolated?paths=>{for(const item of paths)confineIsolatedPath(profile.boundary,item,'The application data location');}:null");
  at('verifyDistrictAsync(resources,userData,profile.isolated?confineIsolatedSettings(profile.boundary,configuration):configuration,');
  at("write:value=>write('settings.json',profile.isolated?confineIsolatedSettings(profile.boundary,value):value),");
  // The only other writer creates the default settings with dataDirectory <userData>\data, inside the confined profile.
  at("const dataDirectory=path.join(userData,'data');fs.mkdirSync(dataDirectory,{recursive:true,mode:0o700});write('settings.json',");
  assert.equal(source.match(/write\('settings.json'/g).length,3,'no other settings writer bypasses confinement');
});
function boundaryFixture(t){
  const {home,options}=sandbox(t),root=path.join(home,'iso'),production=path.join(home,'Documents','MelangeApplicationState','mentat');
  fs.mkdirSync(production,{recursive:true});fs.writeFileSync(path.join(production,'access-identity.dpapi'),'production identity stand-in');
  const outside=path.join(home,'elsewhere');fs.mkdirSync(outside,{recursive:true});
  return {home,options,root,production,outside};
}
test('child junctions of the isolated root into production or outside the tree are refused before use',{skip:process.platform!=='win32'},t=>{
  for(const [child,target] of [['mentat','production'],['github-discovery','production'],[path.join('mentat','data'),'production'],[path.join('mentat','state'),'outside'],[path.join('mentat','logs'),'outside'],['mentat','outside']]){
    const f=boundaryFixture(t),link=path.join(f.root,child);fs.mkdirSync(path.dirname(link),{recursive:true});fs.symlinkSync(f[target],link,'junction');
    assert.throws(()=>selectStartupProfile({...f.options,appId:'mentat',argv:['--melange-isolated-profile='+f.root],packaged:true}),
      error=>error.code==='ISOLATED_PROFILE_REFUSED'&&/resolves (outside the isolated test profile|into production)/.test(error.message),child+' -> '+target);
    assert.deepEqual(fs.readdirSync(f.production),['access-identity.dpapi']);assert.deepEqual(fs.readdirSync(f.outside),[]);
  }
  // A junction that stays inside the isolated tree is acceptable.
  const f=boundaryFixture(t),inner=path.join(f.root,'real-profile');fs.mkdirSync(inner,{recursive:true});fs.symlinkSync(inner,path.join(f.root,'mentat'),'junction');
  assert.equal(selectStartupProfile({...f.options,appId:'mentat',argv:['--melange-isolated-profile='+f.root],packaged:true}).directory,path.join(f.root,'mentat'));
});
test('isolated settings are confined to the isolated tree when loaded or changed',t=>{
  const f=boundaryFixture(t),plan=selectStartupProfile({...f.options,appId:'terrier',argv:['--melange-isolated-profile='+f.root],packaged:true});
  const inside=path.join(plan.directory,'data');
  assert.equal(confineIsolatedSettings(plan.boundary,{dataDirectory:inside,district:'co-05'}).dataDirectory,inside);
  for(const settings of [{dataDirectory:f.production},{dataDirectory:path.join(f.production,'data')},{dataDirectory:f.outside},{dataDirectory:'relative\\data'},{dataDirectory:42},
    {dataDirectory:inside,terrierLegacy:{dataDirectory:f.production}},{dataDirectory:inside,terrierLegacy:null},{dataDirectory:inside,terrierDistricts:{legacyRoot:f.outside}}]){
    assert.throws(()=>confineIsolatedSettings(plan.boundary,settings),error=>error.code==='ISOLATED_PROFILE_REFUSED',JSON.stringify(settings));
  }
  if(process.platform==='win32'){const link=path.join(f.root,'looks-local');fs.symlinkSync(f.production,link,'junction');
    assert.throws(()=>confineIsolatedSettings(plan.boundary,{dataDirectory:path.join(link,'data')}),/resolves/);}
  assert.throws(()=>confineIsolatedPath(null,inside),/boundary is unavailable/);
});
test('isolated runtime refuses data roots outside the tree before creating directories or resolving the backend',async t=>{
  const {Runtime}=require('../runtime.cjs');
  const f=boundaryFixture(t),plan=selectStartupProfile({...f.options,appId:'mentat',argv:['--melange-isolated-profile='+f.root],packaged:true});
  const authorize=async()=>({accepted:{grant:{appId:'mentat'},revoked:false}}),seen=[];
  const confine=paths=>{seen.push(paths);for(const item of paths)confineIsolatedPath(plan.boundary,item,'The application data location');};
  const resources=path.join(f.home,'no-runtime');
  const target=path.join(f.production,'new-data');
  await assert.rejects(new Runtime('mentat',plan.directory,resources,{authorize,confine}).start({dataDirectory:target}),error=>error.code==='ISOLATED_PROFILE_REFUSED');
  assert.equal(fs.existsSync(target),false,'no production directory is created');assert.equal(seen.length,1);
  // Inside the tree the confinement passes and startup proceeds to backend resolution (absent here).
  const data=path.join(plan.directory,'data');fs.mkdirSync(data,{recursive:true});
  await assert.rejects(new Runtime('mentat',plan.directory,resources,{authorize,confine}).start({dataDirectory:data}),/runtime is not installed/);
  assert.deepEqual(seen.at(-1),[data,data,path.join(plan.directory,'state')]);
  // Production (no confine) is unchanged: same backend resolution failure, no confinement call.
  const count=seen.length;
  await assert.rejects(new Runtime('mentat',plan.directory,resources,{authorize}).start({dataDirectory:target}),/runtime is not installed/);assert.equal(seen.length,count);
});
