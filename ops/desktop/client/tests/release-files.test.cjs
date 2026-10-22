'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),fsp=fs.promises,path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const asar=require('@electron/asar'),files=require('../release-files.cjs'),root=path.resolve(__dirname,'..');
// Source-only export (desktop-source-checks.yml sets MELANGE_SOURCE_EXPORT=1): the security gate's data_leak rule denies media
// extensions (DENY_EXT), so those files are absent by design (d2bfa8d89). Only ABSENT media is skipped; everything else stays strict.
const absentMedia=file=>process.env.MELANGE_SOURCE_EXPORT==='1'&&/\.(mp4|mp3|wav|m4a|flac)$/i.test(file)&&!fs.existsSync(path.join(root,file));

test('frozen release contains every active local dependency and renderer asset, but nothing build-only',()=>{
 const included=new Set(files);
 assert.equal(included.size,files.length);
 for(const file of files){
  assert.match(file,/^[a-z0-9][a-z0-9./-]*$/i);assert.ok(!file.includes('..')&&!file.includes('*'));
  assert.ok(absentMedia(file)||fs.statSync(path.join(root,file)).isFile(),file);
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
 for(const file of ['builder.cjs','release-files.cjs','app-config.json'])assert.equal(included.has(file),false,file);
 for(const file of ['main.cjs','preload.cjs','access-preload.cjs','single-launch.cjs','desktop-chrome.css','district-packs.cjs','dataset-client.cjs','dataset-protocol.cjs','dataset-transport.cjs','loading.js'])assert.ok(included.has(file));
 assert.match(fs.readFileSync(path.join(root,'builder.cjs'),'utf8'),/files:\[\.\.\.require\('\.\/release-files\.cjs'\)\]/);
});

test('ASAR fixture preserves original icons and exact renderer/preload resources',async()=>{
 const temporary=await fsp.mkdtemp(path.join(os.tmpdir(),'desktop-release-fixture-')),stage=path.join(temporary,'source'),archive=path.join(temporary,'app.asar');
 try{
  const shipped=files.filter(file=>!absentMedia(file));
  for(const file of shipped){const target=path.join(stage,file);await fsp.mkdir(path.dirname(target),{recursive:true});await fsp.copyFile(path.join(root,file),target);}
  await asar.createPackage(stage,archive);
  for(const file of shipped){const expected=crypto.createHash('sha256').update(await fsp.readFile(path.join(root,file))).digest('hex'),actual=crypto.createHash('sha256').update(asar.extractFile(archive,file)).digest('hex');assert.equal(actual,expected,file);}
  const entries=asar.listPackage(archive).map(value=>value.replaceAll('\\','/'));
  assert.ok(['/district-packs.cjs','/dataset-client.cjs','/dataset-transport.cjs','/loading.js'].every(value=>entries.includes(value)));assert.ok(!entries.some(value=>/\/(builder|release-files)\.cjs$/.test(value)));
 }finally{assert.equal(path.dirname(path.resolve(temporary)),path.resolve(os.tmpdir()));assert.ok(path.basename(temporary).startsWith('desktop-release-fixture-'));await fsp.rm(temporary,{recursive:true,force:true});}
});
