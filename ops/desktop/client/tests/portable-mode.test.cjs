'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {portableMode}=require('../portable-mode.cjs');
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'terrier-portable-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
test('ordinary installed application retains startup and updater lifecycle',t=>{assert.equal(portableMode(fixture(t),'terrier'),false);});
test('explicit Terrier portable marker selects extraction-only lifecycle',t=>{const root=fixture(t);fs.writeFileSync(path.join(root,'portable-mode.json'),JSON.stringify({schemaVersion:1,appId:'terrier'}));assert.equal(portableMode(root,'terrier'),true);});
test('portable marker cannot select another app or carry profile/access overrides',t=>{const root=fixture(t),file=path.join(root,'portable-mode.json');for(const value of [{schemaVersion:1,appId:'melange'},{schemaVersion:2,appId:'terrier'},{schemaVersion:1,appId:'terrier',profile:'elsewhere'},{schemaVersion:1,appId:'terrier',skipAccess:true}]){fs.writeFileSync(file,JSON.stringify(value));assert.throws(()=>portableMode(root,'terrier'),/Invalid portable/);}});
test('malformed or oversized portable marker fails before launch',t=>{const root=fixture(t),file=path.join(root,'portable-mode.json');fs.writeFileSync(file,'{');assert.throws(()=>portableMode(root,'terrier'));fs.writeFileSync(file,' '.repeat(1025));assert.throws(()=>portableMode(root,'terrier'),/Invalid portable/);});
