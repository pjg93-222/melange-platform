'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {LaunchInstall}=require('../launch-install.cjs');

test('launch requests during the launch install are recorded and never start anything',()=>{
  const li=new LaunchInstall(),starts=[];
  const secondInstance=()=>{if(li.defer())return;starts.push('launch');};
  secondInstance();assert.deepEqual(starts,['launch'],'outside an install a second instance launches normally');
  li.begin();secondInstance();secondInstance();assert.deepEqual(starts,['launch'],'during install nothing starts');
  assert.equal(li.end(),true,'the request is reported once the install phase ends');
  secondInstance();assert.deepEqual(starts,['launch','launch']);
  li.begin();assert.equal(li.end(),false,'no request, no stale state from the previous install');
});

test('relaunch is true for an interactive start or a launch requested while the update installed',()=>{
  const li=new LaunchInstall();
  li.begin();assert.equal(li.relaunch(false),false,'background start, nothing requested');assert.equal(li.relaunch(true),true);
  li.defer();assert.equal(li.relaunch(false),true,'a request recorded after the decision was first asked still counts');
  li.end();
});

test('main.cjs routes every launch entry point through the install guard, and decides relaunch after preparation',()=>{
  const main=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8');
  assert.match(main,/app\.on\('second-instance',\(\)=>\{background=false;if\(launchInstall\.defer\(\)\)\{launchInstall\.showWindow\(\);return;\}/);
  assert.match(main,/'desktop-access:open',async event=>\{accessOnly\(event\);if\(!launchInstall\.defer\(\)\)await launch\(true\)/);
  assert.ok(main.indexOf('launchInstall.begin()')>0&&main.indexOf('launchInstall.begin()')<main.indexOf('updater.activateAtLaunch('));
  assert.match(main,/activateAtLaunch\(\(\)=>launchInstall\.relaunch\(!background\)\)/,'the choice is a function, read after preparation');
  // The notice window is closed synchronously before anything is awaited or quit.
  assert.match(main,/if\(installed\)\{closing=true;launchInstall\.closeWindow\(\);await runtime\.stop\(\)/);
  assert.match(main,/updater\.close\(\);launchInstall\.closeWindow\(\);app\.quit\(\)/,'error handler quit path');
  assert.match(main,/app\.on\('before-quit',event=>\{launchInstall\.closeWindow\(\);if\(!closing&&!launchInstall\.installing/,'any other quit path');
});

test('shutdown handlers are attached before the launch install block',()=>{
  const main=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8'),block=main.indexOf('launchInstall.begin()');
  for(const handler of ["powerMonitor.on('shutdown',()=>updater.suspendInstallation())","window.on('query-session-end',()=>updater.suspendInstallation())","window.on('session-end',()=>updater.suspendInstallation())"]){
    assert.equal(main.split(handler).length,2,handler+' attached exactly once');assert.ok(main.indexOf(handler)<block,handler+' precedes the launch install');
  }
});

test('the notice window is created once and destroyed synchronously by closeWindow, so a quit is never blocked',()=>{
  const windows=[];const create=()=>{const w={destroyed:false,isDestroyed(){return this.destroyed;},destroy(){this.destroyed=true;}};windows.push(w);return w;};
  const li=new LaunchInstall({createWindow:create});
  li.showWindow();li.showWindow();assert.equal(windows.length,1);
  li.closeWindow();assert.equal(windows[0].destroyed,true,'closed in the same tick');li.closeWindow();
  li.showWindow();assert.equal(windows.length,2,'a new install phase gets a fresh window');
  windows[1].destroyed=true;li.showWindow();assert.equal(windows.length,3,'an already destroyed window is replaced');
});

test('the real notice-window factory from main.cjs runs under strict mode (no undeclared variable) and returns its window',()=>{
  const main=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8');
  const m=main.match(/LaunchInstall\)\(\{createWindow:(\(\)=>\{[\s\S]*?return owned;\})\}\);/);assert.ok(m,'factory found in main.cjs');
  const calls=[],fake=()=>new Proxy(function(){},{get:(_,k)=>k==='then'?undefined:(k==='catch'?()=>{}:fake()),apply:()=>fake()});
  class BrowserWindow{constructor(o){this.opts=o;this.webContents=fake();}setMenu(v){calls.push(['setMenu',v]);}loadURL(u){calls.push(['loadURL',u]);return Promise.resolve();}}
  const factory=require('node:vm').runInNewContext("'use strict';("+m[1]+")",{BrowserWindow,definition:{id:'x',name:'X'},path,__dirname,installingURL:'file:///installing.html'});
  const win=factory();// strict-mode ReferenceError on any undeclared assignment would throw here
  assert.ok(win instanceof BrowserWindow);assert.equal(win.opts.closable,false);assert.deepEqual(calls,[['setMenu',null],['loadURL','file:///installing.html']],'menu and URL set exactly once');
});
