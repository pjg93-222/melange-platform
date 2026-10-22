'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const afterPack=require('../after-pack.cjs');

function pack(t,files){
  const appOutDir=fs.mkdtempSync(path.join(os.tmpdir(),'after-pack-'));t.after(()=>fs.rmSync(appOutDir,{recursive:true,force:true}));
  for(const relative of files){const target=path.join(appOutDir,'resources','runtime',...relative.split('/'));fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,'x');}
  return {appOutDir,exists:relative=>fs.existsSync(path.join(appOutDir,'resources','runtime',...relative.split('/')))};
}
const quiet=async(context)=>{const original=console.log,lines=[];console.log=line=>lines.push(line);try{await afterPack(context);}finally{console.log=original;}return lines;};

const THIRD_PARTY=['mentat/python/Lib/site-packages/pyproj/proj_dir/share/proj/proj.db','mentat/python/Lib/site-packages/pyogrio/proj_data/proj.db','mentat/native/R/doc/KEYWORDS.db',
  'mentat/native/R/share/licenses/license.db','mentat/native/R/library/RSQLite/db/datasets.sqlite','mentat/python/lib/x.sqlite3','mentat/python/state/readme.txt'];

test('state directories and databases under runtime/<app>/sources are removed and logged, and the build continues',async t=>{
  const planted=['mentat/sources/mentat/state/prewarm/access.sqlite','mentat/sources/mentat/state/prewarm/access.sqlite-wal','mentat/sources/mentat/state/prewarm/access.sqlite-shm',
    'mentat/sources/x/app/foo.db','mentat/sources/app/Cache/A.DB-WAL','mentat/sources/a/b/c/STATE/x.txt','terrier/Sources/app/d.sqlite3','mentat/sources/app/j.sqlite-journal'];
  const keep=['mentat/sources/app/server.py','mentat/sources/app/statement.txt',...THIRD_PARTY];
  const context=pack(t,[...planted,...keep]);
  const lines=await quiet(context);
  for(const file of planted)assert.equal(context.exists(file),false,file);
  assert.equal(context.exists('mentat/sources/mentat/state'),false,'the whole state directory is gone');
  for(const file of keep)assert.equal(context.exists(file),true,file+' must survive');
  assert.ok(lines.some(line=>/removed .*mentat.state$/.test(line))&&lines.some(line=>/removed .*foo\.db$/.test(line)),'each removed path is logged');
  assert.ok(lines.some(line=>/packing report: removed \d+ state\/database path/.test(line)));
});

test('a runtime without sources, or without a runtime at all, packs cleanly',async t=>{
  const context=pack(t,[...THIRD_PARTY]);await quiet(context);for(const file of THIRD_PARTY)assert.equal(context.exists(file),true,file);
  await quiet({appOutDir:path.join(context.appOutDir,'nowhere')});
});

test('builder wires the strip step as afterPack and keeps its glob filter inside sources',()=>{
  const source=fs.readFileSync(path.join(__dirname,'..','builder.cjs'),'utf8');
  assert.match(source,/afterPack:path\.join\(__dirname,'after-pack\.cjs'\)/);
  assert.ok(source.includes("'!sources/**/state/**'")&&source.includes("'!sources/**/*.{"),'filter patterns stay under sources/');
  assert.ok(!require('../release-files.cjs').includes('after-pack.cjs'),'build-time only, not shipped in the app');
});

test('an unreadable directory under sources is logged and skipped; the rest is still stripped and the build continues',async t=>{
  const context=pack(t,['mentat/sources/locked/a.sqlite','mentat/sources/app/b.db']);
  const locked=path.join(context.appOutDir,'resources','runtime','mentat','sources','locked'),read=fs.readdirSync;
  fs.readdirSync=(directory,...rest)=>{if(path.resolve(String(directory))===locked)throw Object.assign(new Error('EACCES: permission denied'),{code:'EACCES'});return read.call(fs,directory,...rest);};
  let lines;try{lines=await quiet(context);}finally{fs.readdirSync=read;}
  assert.equal(context.exists('mentat/sources/app/b.db'),false,'the readable branch is still stripped');
  assert.ok(lines.some(line=>/could not read .*locked: EACCES/.test(line)),'the unreadable directory is logged');
});
