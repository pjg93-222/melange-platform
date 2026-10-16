'use strict';
const {app,BrowserWindow}=require('electron');const assert=require('node:assert/strict');
const {PendingDistrictSwitch,preserveUnloadVeto}=require('../district-switch.cjs');
let window,writes=0;const control=new PendingDistrictSwitch({read:()=>({district:'co-05',dataDirectory:'.'}),write:()=>writes++,scope:()=>['or-05'],verify:()=>true});
const timeout=setTimeout(()=>app.exit(1),10000);
app.whenReady().then(async()=>{
  window=new BrowserWindow({show:false,webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true}});
  let veto=false;preserveUnloadVeto(window.webContents,()=>{veto=true;control.cancel();});
  await window.loadURL('data:text/html,<script>window.onbeforeunload=()=>false;</script><p>Unsaved fixture</p>');
  control.prepare('or-05');window.close();await new Promise(resolve=>setTimeout(resolve,200));
  assert.equal(veto,true);assert.equal(window.isDestroyed(),false);assert.equal(writes,0);assert.equal(control.pending,null);
  await window.webContents.executeJavaScript('window.onbeforeunload=null');
  control.prepare('or-05');window.once('closed',()=>{control.commitAfterClosed();});window.close();await new Promise(resolve=>setTimeout(resolve,100));
  assert.equal(writes,1);assert.equal(window.isDestroyed(),true);
  process.stdout.write(JSON.stringify({beforeunloadVetoPreserved:true,vetoCancelsSelection:true,persistOnlyAfterClosed:true,checkedAt:new Date().toISOString()})+'\n');
}).catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(()=>{clearTimeout(timeout);if(window&&!window.isDestroyed())window.destroy();app.exit(process.exitCode||0);});
app.on('window-all-closed',()=>{});
