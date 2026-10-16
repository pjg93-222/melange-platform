'use strict';
const {app,BrowserWindow,ipcMain,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const {UIStateStore,registerStateIPC}=require('../ui-state.cjs');
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'melange-ui-state-electron-'));
app.setPath('userData',directory);
const boot=fs.readFileSync(path.resolve(__dirname,'../../../../fec/surfaces/approved/clean/mlk-boot.js'),'utf8').split('/* mlk-boot.js:')[0];
let window,runtime={origin:null},servers=[];const store=new UIStateStore(directory);
registerStateIPC(ipcMain,()=>({window,runtime,appId:'melange'}),store);
function server(){return new Promise(resolve=>{const s=http.createServer((request,response)=>{response.setHeader('Content-Type','text/html');response.end('<!doctype html><script>'+boot+'</script><script>window.early=localStorage.getItem("melange-influence-rail");</script>'+ (request.url==='/frame'?'':'<iframe src="/frame"></iframe>'));});s.listen(0,'127.0.0.1',()=>{servers.push(s);resolve('http://127.0.0.1:'+s.address().port);});});}
const deadline=setTimeout(()=>{process.stderr.write('UI state smoke timed out\n');app.exit(1);},30000);
app.whenReady().then(async()=>{
  store.mutate('set','melange-influence-rail','closed');
  const partition=session.fromPartition('persist:ui-state-fixture');
  partition.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith('http://127.0.0.1:')}));
  window=new BrowserWindow({show:false,webPreferences:{session:partition,preload:path.join(__dirname,'../preload.cjs'),sandbox:true,nodeIntegration:false,contextIsolation:true}});
  runtime.origin=await server();await window.loadURL(runtime.origin);
  assert.equal(await window.webContents.executeJavaScript('window.early'),'closed');
  const result=await window.webContents.executeJavaScript(`(()=>{const w=document.querySelector('iframe').contentWindow;w.localStorage.setItem('mdesk_manual_v1',JSON.stringify({fixture:{bio:'saved from frame'}}));w.localStorage.setItem('unreviewed-key','browser only');return {frameEarly:w.early,node:typeof w.process,wrapped:w.__melangeDesktopStorage};})()`);
  assert.equal(result.frameEarly,'closed');assert.equal(result.node,'undefined');assert.equal(result.wrapped,true);assert.match(store.snapshot().mdesk_manual_v1,/saved from frame/);assert.equal(store.snapshot()['unreviewed-key'],undefined);
  const first=runtime.origin;runtime.origin=await server();assert.notEqual(first,runtime.origin);await window.loadURL(runtime.origin);
  assert.equal(await window.webContents.executeJavaScript('window.early'),'closed');assert.match(await window.webContents.executeJavaScript('localStorage.getItem("mdesk_manual_v1")'),/saved from frame/);
  const previous=store.write;store.write=()=>{throw new Error('fixture disk unavailable');};
  const failed=await window.webContents.executeJavaScript(`(()=>{try{document.querySelector('iframe').contentWindow.localStorage.setItem('melange-influence-rail','open');return {threw:false};}catch(e){return {threw:true,value:localStorage.getItem('melange-influence-rail')};}})()`);
  assert.equal(failed.threw,true);assert.equal(failed.value,'closed');store.write=previous;
  const prefs=window.webContents.getLastWebPreferences();assert.equal(prefs.nodeIntegration,false);assert.equal(prefs.nodeIntegrationInSubFrames,false);assert.equal(prefs.sandbox,true);
  process.stdout.write(JSON.stringify({hydratedBeforeScripts:true,frameWritesPersist:true,changedPortRestores:true,failedSaveRollsBack:true,nodeInFrames:false,checkedAt:new Date().toISOString()})+'\n');
}).catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(()=>{clearTimeout(deadline);window?.destroy();for(const s of servers)s.close();app.exit(process.exitCode||0);});
