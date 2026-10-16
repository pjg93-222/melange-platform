'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const http=require('node:http');const {EventEmitter}=require('node:events');
const {startRenderService,LIMITS}=require('../render-service.cjs');
const fixture={document:'<h1>Fixture</h1>',width:200,height:100,scale:1};
function dependencies({hang=false,fail=false}={}){
  const windows=[],isolated=new EventEmitter();let network,partition,options;
  isolated.webRequest={onBeforeRequest:callback=>{network=callback;}};
  isolated.setPermissionRequestHandler=callback=>{isolated.permission=callback;};
  isolated.setPermissionCheckHandler=callback=>{isolated.permissionCheck=callback;};
  isolated.setDevicePermissionHandler=callback=>{isolated.devicePermission=callback;};
  isolated.clearCache=async()=>{};isolated.clearStorageData=async()=>{};
  class Window{
    constructor(opts){this.options=opts;this.destroyed=false;this.webContents=new EventEmitter();windows.push(this);
      this.webContents.setWindowOpenHandler=callback=>{this.windowOpen=callback;};
      this.webContents.capturePage=async(rect,captureOptions)=>{this.rect=rect;this.captureOptions=captureOptions;
        if(hang)await new Promise(()=>{});if(fail)throw new Error('private document leaked?');
        return {isEmpty:()=>false,resize:({width,height})=>({toPNG:()=>{const png=Buffer.alloc(24);Buffer.from([137,80,78,71,13,10,26,10]).copy(png);png.writeUInt32BE(width,16);png.writeUInt32BE(height,20);return png;}})};};
    }
    loadURL(url){this.url=url;return Promise.resolve();}isDestroyed(){return this.destroyed;}destroy(){this.destroyed=true;}
  }
  return {BrowserWindow:Window,session:{fromPartition:(name,opts)=>{partition=name;options=opts;return isolated;}},windows,isolated,
    network:url=>{let value;network({url},result=>{value=result;});return value;},get partition(){return partition;},get options(){return options;}};
}
async function setup(t,options={}){const deps=dependencies(options);const service=await startRenderService({...deps,timeoutMs:options.timeoutMs||2000});t.after(()=>service.close());return {deps,service};}
function request(service,{headers={},body=JSON.stringify(fixture),path='/render',method='POST'}={}){
  return new Promise((resolve,reject)=>{
    const url=new URL(service.url);const req=http.request({hostname:url.hostname,port:url.port,path,method,headers:{Authorization:'Bearer '+service.token,'Content-Type':'application/json',...headers}},res=>{
      const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
    });req.on('error',reject);req.end(body);
  });
}
async function until(predicate){for(let i=0;i<100;i++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,5));}throw new Error('Condition did not become true');}

test('authenticated PNG capture is isolated and preserves exact output dimensions',async t=>{
  const {deps,service}=await setup(t);const result=await request(service,{body:JSON.stringify({...fixture,scale:2,document:'</head><meta http-equiv="Content-Security-Policy" content="default-src *"><script>secret()</script><svg onload="secret()"></svg>'})});
  assert.equal(result.status,200);assert.equal(result.headers['content-type'],'image/png');assert.equal(result.headers['cache-control'],'no-store');
  assert.equal(result.body.readUInt32BE(16),400);assert.equal(result.body.readUInt32BE(20),200);
  const win=deps.windows[0],prefs=win.options.webPreferences;
  assert.equal(win.options.show,false);assert.equal(prefs.javascript,false);assert.equal(prefs.sandbox,true);assert.equal(prefs.contextIsolation,true);assert.equal(prefs.nodeIntegration,false);assert.equal(prefs.nodeIntegrationInWorker,false);assert.equal(prefs.nodeIntegrationInSubFrames,false);assert.equal(prefs.offscreen,true);assert.equal(prefs.backgroundThrottling,false);assert.equal(prefs.webSecurity,true);assert.equal(prefs.webviewTag,false);assert.equal(prefs.preload,undefined);assert.equal(prefs.zoomFactor,2);
  assert.equal(prefs.session,deps.isolated);assert.ok(!deps.partition.startsWith('persist:'));assert.equal(deps.options.cache,false);
  const html=Buffer.from(win.url.split(',')[1],'base64').toString();assert.ok(html.indexOf("script-src 'none'")<html.indexOf('default-src *'));assert.ok(html.includes("font-src data:"));assert.ok(!html.includes(service.token));
  for(const url of ['https://example.com','http://127.0.0.1:1','file:///secret','ftp://example.com','ws://example.com','blob:example'])assert.equal(deps.network(url).cancel,true,url);
  assert.equal(deps.network('data:image/svg+xml,anything').cancel,false);
  assert.deepEqual(win.windowOpen({url:'https://example.com'}),{action:'deny'});
  for(const event of ['will-navigate','will-frame-navigate','will-redirect','will-attach-webview']){let denied=false;win.webContents.emit(event,{preventDefault:()=>{denied=true;}});assert.equal(denied,true,event);}
  let downloadDenied=false;deps.isolated.emit('will-download',{preventDefault:()=>{downloadDenied=true;}});assert.equal(downloadDenied,true);
  let permission=true;deps.isolated.permission(null,'camera',value=>{permission=value;});assert.equal(permission,false);assert.equal(deps.isolated.permissionCheck(),false);assert.equal(deps.isolated.devicePermission(),false);
  assert.deepEqual(win.captureOptions,{stayHidden:true,stayAwake:true});assert.equal(win.destroyed,true);
});

test('host, browser headers, credentials and fixed endpoint are checked before allocating a window',async t=>{
  const {deps,service}=await setup(t);
  for(const [options,status] of [[{headers:{Host:'localhost'}},403],[{headers:{Origin:''}},403],[{headers:{'Sec-Fetch-Site':'none'}},403],[{headers:{'Sec-Fetch-Mode':'navigate'}},403],[{headers:{Authorization:'Bearer wrong'}},401],[{headers:{Authorization:''}},401],[{method:'GET',body:''},404],[{path:'/render?path=/tmp/x'},404],[{path:'/capture'},404],[{headers:{'Content-Type':'text/html'}},415],[{headers:{'Content-Encoding':'gzip'}},415]])assert.equal((await request(service,options)).status,status,JSON.stringify(options));
  assert.equal(deps.windows.length,0);
});

test('malformed, extra-field, fractional, oversized and physical pixel/dimension inputs fail closed',async t=>{
  const {deps,service}=await setup(t);
  for(const value of [null,[],{...fixture,path:'file:///x'},{...fixture,width:100.5},{...fixture,height:0},{...fixture,scale:'2'},{...fixture,scale:0},{...fixture,width:19001},{...fixture,width:19000,scale:2},{...fixture,width:9000,height:4000},{...fixture,width:4000,height:3000,scale:2},{...fixture,document:''}])assert.equal((await request(service,{body:JSON.stringify(value)})).status,400);
  assert.equal((await request(service,{body:'not JSON'})).status,400);
  assert.equal((await request(service,{headers:{'Content-Length':String(LIMITS.bodyBytes+1)},body:''})).status,413);
  assert.equal(deps.windows.length,0);
});

test('oversized chunked bodies are rejected without capture',async t=>{
  const {deps,service}=await setup(t);const url=new URL(service.url);
  const response=await new Promise((resolve,reject)=>{
    const req=http.request({hostname:url.hostname,port:url.port,path:url.pathname,method:'POST',headers:{Authorization:'Bearer '+service.token,'Content-Type':'application/json','Transfer-Encoding':'chunked'}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});
    req.on('error',error=>{if(error.code!=='EPIPE'&&error.code!=='ECONNRESET')reject(error);});
    req.end(Buffer.alloc(LIMITS.bodyBytes+1,32));
  });assert.equal(response,413);assert.equal(deps.windows.length,0);
});

test('only one capture runs and timeout destroys it without leaking document errors',async t=>{
  const {deps,service}=await setup(t,{hang:true,timeoutMs:150});const first=request(service);
  await until(()=>deps.windows.length===1);assert.equal((await request(service)).status,429);
  const timed=await first;assert.equal(timed.status,504);assert.equal(timed.body.toString(),'Render timed out');assert.equal(deps.windows[0].destroyed,true);
});

test('a delayed body is bounded by the request deadline and creates no window',async t=>{
  const {deps,service}=await setup(t,{timeoutMs:100});const url=new URL(service.url);
  const status=await new Promise((resolve,reject)=>{
    const req=http.request({hostname:url.hostname,port:url.port,path:url.pathname,method:'POST',headers:{Authorization:'Bearer '+service.token,'Content-Type':'application/json'}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);req.write('{');
  });assert.equal(status,504);assert.equal(deps.windows.length,0);
});

test('client disconnect and service shutdown destroy the active window',async t=>{
  const {deps,service}=await setup(t,{hang:true});const url=new URL(service.url);
  const req=http.request({hostname:url.hostname,port:url.port,path:url.pathname,method:'POST',headers:{Authorization:'Bearer '+service.token,'Content-Type':'application/json'}});req.on('error',()=>{});req.end(JSON.stringify(fixture));
  await until(()=>deps.windows.length===1);req.destroy();await until(()=>deps.windows[0].destroyed);
  const next=request(service).catch(()=>null);await until(()=>deps.windows.length===2);await service.close();await next;assert.equal(deps.windows[1].destroyed,true);
});

test('unexpected renderer failures are generic and still clean up',async t=>{
  const {deps,service}=await setup(t,{fail:true});const result=await request(service);assert.equal(result.status,500);assert.equal(result.body.toString(),'Render failed');assert.equal(deps.windows[0].destroyed,true);
});
