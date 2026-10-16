'use strict';
// Exercise the real main/preload/setup window in a temporary OS profile.
// This is UI-shell acceptance, not a native backend or installer acceptance test.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'melange-electron-smoke-'));
app.setPath('appData',temporary);
const profile=path.join(temporary,'profile');fs.mkdirSync(profile);
// Force setup with an empty test configuration; never select a real Documents
// profile, start a backend, register login startup or display fixture windows.
fs.writeFileSync(path.join(profile,'settings.json'),'{}');
process.env.MELANGE_DESKTOP_TEST_PROFILE=profile;
process.env.DESKTOP_APP_ID='terrier';
BrowserWindow.prototype.show=function(){};BrowserWindow.prototype.focus=function(){};
require('../main.cjs');
let timer=setTimeout(()=>{process.stderr.write('Electron setup smoke timed out\n');app.exit(1);},30000);
app.whenReady().then(async()=>{
  const {DiscoveryCache}=require('../github-discovery.cjs');
  const cacheOptions={directory:path.join(temporary,'shared-discovery'),repository:'fixture/release',validate:value=>value};
  assert.deepEqual(await new DiscoveryCache(cacheOptions).get(async()=>[]),[]);
  assert.deepEqual(await new DiscoveryCache(cacheOptions).get(async()=>{throw new Error('Cached empty result was not reused');}),[]);
  for(let attempt=0;attempt<100;attempt++){
    const window=BrowserWindow.getAllWindows().find(item=>item.webContents.getURL().endsWith('/setup.html'));
    if(window&&!window.webContents.isLoading()&&window.webContents.getURL().endsWith('/setup.html')){
      const preferences=window.webContents.getLastWebPreferences();
      assert.equal(app.isPackaged,false);assert.equal(app.getPath('userData'),profile);
      assert.equal(preferences.nodeIntegration,false);assert.equal(preferences.contextIsolation,true);assert.equal(preferences.sandbox,true);assert.equal(preferences.webSecurity,true);
      const result=await window.webContents.executeJavaScript('({nodeExposed:typeof process!=="undefined",body:document.body.textContent,desktopExposed:typeof window.desktop})');
      assert.equal(result.nodeExposed,false);assert.equal(result.desktopExposed,'object');assert.match(result.body,/Terrier/);
      process.stdout.write(JSON.stringify({uiShellLaunched:true,sandbox:true,nodeIntegration:false,isolatedPreload:true,sharedDiscoveryCache:true,nativeBackendAccepted:false})+'\n');
      clearTimeout(timer);app.quit();return;
    }
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('Setup window did not become ready');
}).catch(error=>{clearTimeout(timer);process.stderr.write(error.message+'\n');app.exit(1);});
