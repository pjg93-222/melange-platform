'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const asar=require('@electron/asar'),files=require('../release-files.cjs'),root=path.resolve(__dirname,'..');

test('frozen release contains every active local dependency and renderer asset, but no inactive dataset module',()=>{
 const included=new Set(files);
 assert.equal(included.size,files.length);
 for(const file of files){
  assert.match(file,/^[a-z0-9][a-z0-9./-]*$/i);assert.ok(!file.includes('..')&&!file.includes('*'));
  assert.ok(fs.statSync(path.join(root,file)).isFile(),file);
  if(file.endsWith('.cjs')){
   const source=fs.readFileSync(path.join(root,file),'utf8');
   for(const match of source.matchAll(/require\(['"](\.\/[^'"]+)['"]\)/g))assert.ok(included.has(match[1].slice(2)),file+' depends on missing '+match[1]);
  }
  if(file.endsWith('.html')){
   const source=fs.readFileSync(path.join(root,file),'utf8');
   for(const match of source.matchAll(/(?:src|href)=["']([^"']+)["']/g)){
    if(match[1].startsWith('#')||match[1].startsWith('data:'))continue;
    assert.ok(included.has(match[1]),file+' references missing '+match[1]);
   }
  }
 }
 for(const file of ['dataset-client.cjs','dataset-protocol.cjs','dataset-transport.cjs','builder.cjs','release-files.cjs','app-config.json'])assert.equal(included.has(file),false,file);
 for(const file of ['main.cjs','preload.cjs','access-preload.cjs','single-launch.cjs','desktop-chrome.css'])assert.ok(included.has(file));
 assert.match(fs.readFileSync(path.join(root,'builder.cjs'),'utf8'),/files:\[\.\.\.require\('\.\/release-files\.cjs'\)\]/);
});

test('ASAR fixture preserves original icons and exact renderer/preload resources',async()=>{
 const temporary=await fsp.mkdtemp(path.join(os.tmpdir(),'desktop-release-fixture-')),stage=path.join(temporary,'source'),archive=path.join(temporary,'app.asar');
 try{
  for(const file of files){const target=path.join(stage,file);await fsp.mkdir(path.dirname(target),{recursive:true});await fsp.copyFile(path.join(root,file),target);}
  await asar.createPackage(stage,archive);
  for(const file of files){const expected=crypto.createHash('sha256').update(await fsp.readFile(path.join(root,file))).digest('hex'),actual=crypto.createHash('sha256').update(asar.extractFile(archive,file)).digest('hex');assert.equal(actual,expected,file);}
  const entries=asar.listPackage(archive).map(value=>value.replaceAll('\\','/'));
  assert.ok(!entries.some(value=>value.includes('/dataset-')));
 }finally{assert.equal(path.dirname(path.resolve(temporary)),path.resolve(os.tmpdir()));assert.ok(path.basename(temporary).startsWith('desktop-release-fixture-'));await fsp.rm(temporary,{recursive:true,force:true});}
});
