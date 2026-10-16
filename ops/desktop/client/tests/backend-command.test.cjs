'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {backendCommand}=require('../backend-command.cjs');
test('native launcher uses only the selected app bundled interpreter and never host Python',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-command-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  assert.throws(()=>backendCommand(root,'terrier','win32'),/runtime is not installed/);
  assert.throws(()=>backendCommand(root,'../terrier','win32'),/Unknown application/);
  const runtime=path.join(root,'runtime','terrier');fs.mkdirSync(path.join(runtime,'python'),{recursive:true});fs.mkdirSync(path.join(runtime,'bootstrap'));
  fs.writeFileSync(path.join(runtime,'python','python.exe'),'fixture');
  assert.throws(()=>backendCommand(root,'terrier','win32'),/runtime is not installed/);
  fs.writeFileSync(path.join(runtime,'bootstrap','desktop_backend.py'),'fixture');
  const command=backendCommand(root,'terrier','win32');assert.equal(command.binary,path.join(runtime,'python','python.exe'));assert.deepEqual(command.args,['-E','-s','-u',path.join(runtime,'bootstrap','desktop_backend.py')]);
  assert.throws(()=>backendCommand(root,'mentat','win32'),/runtime is not installed/);
});
