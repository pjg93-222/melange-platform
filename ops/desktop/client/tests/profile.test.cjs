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
