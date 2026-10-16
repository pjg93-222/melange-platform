'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');

test('ordinary build config forces the extractor-compatible codec before pinned archive arguments are computed',()=>{
  const root=path.resolve(__dirname,'..');
  const pkg=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
  for(const command of ['build','build:dir'])assert.match(pkg.scripts[command],/--config builder\.cjs(?:$|\s)/);
  const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'melange-build-codec-'));
  try{
    fs.mkdirSync(path.join(fixture,'terrier','python'),{recursive:true});
    fs.mkdirSync(path.join(fixture,'terrier','bootstrap'),{recursive:true});
    fs.writeFileSync(path.join(fixture,'terrier','python',process.platform==='win32'?'python.exe':'bin-python-placeholder'),'');
    if(process.platform!=='win32'){
      fs.mkdirSync(path.join(fixture,'terrier','python','bin'));
      fs.writeFileSync(path.join(fixture,'terrier','python','bin','python3'),'');
    }
    fs.writeFileSync(path.join(fixture,'terrier','bootstrap','desktop_backend.py'),'');
    for(const inherited of ['', 'ARM']){
      const child=spawnSync(process.execPath,['-e',
        "const config=require('./builder.cjs'); const {compute7zCompressArgs}=require('app-builder-lib/out/targets/archive.js'); process.stdout.write(JSON.stringify({appId:config.appId,filter:process.env.ELECTRON_BUILDER_7Z_FILTER,args:compute7zCompressArgs('7z',{compression:'normal'})}));"],
        {cwd:root,encoding:'utf8',windowsHide:true,env:{...process.env,DESKTOP_APP_ID:'terrier',
          DESKTOP_RUNTIME_ROOT:fixture,DESKTOP_ACCESS_TRUST:'',ELECTRON_BUILDER_7Z_FILTER:inherited,
          ELECTRON_BUILDER_COMPRESSION_LEVEL:'1'}});
      assert.equal(child.status,0,child.stderr);
      const result=JSON.parse(child.stdout);
      assert.equal(result.filter,'BCJ');
      assert.ok(result.args.includes('-mf=BCJ'));
      assert.equal(result.args.filter(value=>value.startsWith('-mf=')).length,1);
      assert.match(result.appId,/terrier/i);
    }
  }finally{
    // Only the exact random fixture directory created by this test can be removed.
    assert.equal(path.dirname(fixture),path.resolve(os.tmpdir()));
    assert.ok(path.basename(fixture).startsWith('melange-build-codec-'));
    fs.rmSync(fixture,{recursive:true,force:true});
  }
});
