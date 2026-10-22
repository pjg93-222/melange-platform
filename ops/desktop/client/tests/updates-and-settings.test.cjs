'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
// Owner decision: updates are always on. The Ed25519-signed manifest and sha256/sha512 checks (trust.cjs, github-source.cjs)
// gate every install; the Windows certificate store and Authenticode chain are not consulted.
test('updates are checked unconditionally and the Authenticode chain checks are skipped',()=>{
 const main=read('main.cjs');
 assert.match(main,/catch\{enrollmentDeferred=true;\}\r?\n\s*await updater\.check\(\);/);
 assert.doesNotMatch(main,/publisher-trust|publisherTrusted|trustPublisher|trustUpdates|trust-updates|offerUpdateTrust|UPDATES_UNTRUSTED|updatesTrusted|Turn on updates/);
 assert.match(main,/autoUpdater:require\('electron-updater'\)\.autoUpdater,verifyNativePackage:async\(\)=>\{\},packaged:/);
 assert.match(main,/^\s*require\('electron-updater'\)\.autoUpdater\.verifyUpdateCodeSignature=async\(\)=>null;/m);
 // updater.cjs nativeTrust refuses every update when electron-updater has no publisherName, which verifyUpdateCodeSignature:true provides.
 assert.match(read('builder.cjs'),/verifyUpdateCodeSignature:true/);
});
// Owner decision: the app never asks for a workspace folder or a district before launch. District switching lives inside the opened app.
test('the settings page has no folder, district or certificate-trust control and every script id exists',()=>{
 const html=read('setup.html'),script=read('setup.js'),preload=read('preload.cjs'),main=read('main.cjs');
 assert.doesNotMatch(html+script,/choose|district|directory|workspace|trustUpdates|Turn on updates/i);
 assert.doesNotMatch(preload,/chooseDirectory|trustUpdates|district:value|desktop:directory|desktop:trust-updates|desktop:district'/);
 assert.doesNotMatch(main,/desktop:directory|desktop:trust-updates|desktop:district'|openDirectory|createDirectory|Choose your local data directory/);
 for(const [, id] of script.matchAll(/byId\('([^']+)'\)/g))assert.ok(html.includes('id="'+id+'"'),'setup.html lacks #'+id);
 for(const id of ['launch','status'])assert.ok(html.includes('id="'+id+'"'),id);
 // The in-app district switcher (Terrier web UI) keeps its two runtime-only entry points.
 assert.match(preload,/districts:\(\)=>ipcRenderer\.invoke\('desktop:districts'\)/);assert.match(preload,/switchDistrict:value=>ipcRenderer\.invoke\('desktop:switch-district',value\)/);
 assert.match(main,/ipcMain\.handle\('desktop:switch-district',selectDistrict\)/);assert.match(main,/async function selectDistrict\(event,district\)\{\r?\n\s*runtimeOnly\(event\);/);
});
