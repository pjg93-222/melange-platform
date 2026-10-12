'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));const app=path.join(__dirname,'../app');
test('Chart Studio startup only handshakes, and view changes cost no background request',{timeout:120000},async t=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});t.after(()=>browser.close());const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');await page.route('http://app.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));await page.goto('http://app.fixture/#chart-studio');
 await page.evaluate(()=>{window.calls=[];window.fetch=async url=>{calls.push(String(url));let body;if(String(url).startsWith('/api/session?light=1'))body={token:'token',read_only:true,evidence_collection_available:true};else if(url==='/api/evidence')body={snapshots:[]};else throw Error('Unexpected background request '+url);return {ok:true,json:async()=>body};};});
 for(const file of ['ui-state.js','coverage-view.js','app.js'])await page.addScriptTag({path:path.join(app,file)});
 await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('Local connection'));
 assert.deepEqual(await page.evaluate(()=>calls),['/api/session?light=1']);
 // S6 (487589584) CREATED app/evidence.js and moved the whole evidence pane
 // into it: app.js now holds no reference to #evidence-source or #evidence-mode.
 // This page injects ui-state.js, coverage-view.js and app.js only, so the
 // startup handshake is the claim it can still make. start() now fetches the
 // catalog on the FIRST mentat:view event whatever the view (evidence.js:1504,
 // 1507-1515) and a source change reads /api/evidence?id=<id>, so the old
 // per-source catalog counting described a console that no longer exists. The
 // pane's own lazy read is asserted in tests/test_evidence_panes_browser.cjs.
 await page.locator('#tab-evidence').click();
 await page.locator('#tab-chart-studio').click();
 await page.evaluate(()=>window.dispatchEvent(new CustomEvent('mentat:graph',{detail:{filters:{company:'Example',cycle:'2024'},options:{companies:['Example']},companies:[{id:'Example'}]}})));
 assert.deepEqual(await page.evaluate(()=>calls),['/api/session?light=1'],'view changes and a hidden Graph context must not cost a background request');
 assert.deepEqual(errors,[]);
});
test('An older build on disk raises the restart banner, and the stamp names the console answering',{timeout:120000},async t=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});t.after(()=>browser.close());const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');await page.route('http://app.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));await page.goto('http://app.fixture/#chart-studio');
 await page.evaluate(()=>{window.calls=[];const build={git_sha:'2906980d1',code_sha256:'a'.repeat(64),assets_sha256:'b'.repeat(64),on_disk_code_sha256_now:'c'.repeat(64),on_disk_assets_sha256_now:'b'.repeat(64),started_at:1757809380,port:8803,pid:79412,busy:false};window.fetch=async url=>{calls.push(String(url));let body;if(String(url).startsWith('/api/session?light=1'))body={token:'token',read_only:false,evidence_collection_available:true,api_version:2,build,...build};else if(url==='/api/evidence')body={snapshots:[]};else throw Error('Unexpected background request '+url);return {ok:true,json:async()=>body};};});
 for(const file of ['ui-state.js','coverage-view.js','app.js'])await page.addScriptTag({path:path.join(app,file)});
 await page.waitForFunction(()=>document.querySelector('#stale-banner')&&document.querySelector('#stale-banner').hidden===false);
 assert.equal(await page.locator('#stale-banner-message').textContent(),'This console runs an older build. Press Restart, or close this tab and open http://app.fixture.');
 assert.equal(await page.locator('#stale-restart').textContent(),'Restart');
 assert.match(await page.locator('#build-stamp').textContent(),/^build 2906980 · port 8803$/);
 assert.match(await page.locator('#build-stamp').getAttribute('title'),/^Up since .+$/,'board M4: the stamp is one line; the start time moves to its tooltip');
 assert.deepEqual(await page.evaluate(()=>calls),['/api/session?light=1'],'the stamp must not cost an extra startup request');
 assert.deepEqual(errors,[]);
});
test('Changed interface files ask for a restart, and a current build shows no banner',{timeout:120000},async t=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});t.after(()=>browser.close());
 const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
 for(const scenario of [{name:'assets',on_disk_assets_sha256_now:'d'.repeat(64),banner:true,action:'Restart'},{name:'current',banner:false}]){
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://app.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));await page.goto('http://app.fixture/#chart-studio');
  await page.evaluate(scenario=>{window.calls=[];const build={git_sha:'unknown',code_sha256:'a'.repeat(64),assets_sha256:'b'.repeat(64),on_disk_code_sha256_now:'a'.repeat(64),on_disk_assets_sha256_now:scenario.on_disk_assets_sha256_now||'b'.repeat(64),started_at:1757809380,port:8802,pid:41288,busy:false};window.fetch=async url=>{calls.push(String(url));let body;if(String(url).startsWith('/api/session?light=1'))body={token:'token',read_only:false,evidence_collection_available:true,api_version:2,build,...build};else if(url==='/api/evidence')body={snapshots:[]};else throw Error('Unexpected background request '+url);return {ok:true,json:async()=>body};};},scenario);
  for(const file of ['ui-state.js','coverage-view.js','app.js'])await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(()=>document.querySelector('#build-stamp')!==null);
  assert.equal(await page.locator('#stale-banner').isVisible(),scenario.banner,scenario.name);
  if(scenario.banner){
   assert.equal(await page.locator('#stale-banner-message').textContent(),'This console runs an older build. Press Restart, or close this tab and open http://app.fixture.');
   assert.equal(await page.locator('#stale-restart').textContent(),scenario.action);
  }
  // Git unavailable falls back to the code tree hash, never to a blank stamp.
  assert.match(await page.locator('#build-stamp').textContent(),/^build aaaaaaa · port 8802$/);
  assert.match(await page.locator('#build-stamp').getAttribute('title'),/^Up since .+$/,scenario.name);
  assert.deepEqual(errors,[],scenario.name);
  await page.close();
 }
});
// Finding 11 (2026-09-25): a read-only console cannot restart itself, and its own origin is the same stale
// process, so its banner says a restart of that process brings the current build, offers no Restart and names no URL.
test('A read-only console on an older build says a restart of its process brings the current one, with no Restart and no URL',{timeout:120000},async t=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});t.after(()=>browser.close());const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');await page.route('http://app.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));await page.goto('http://app.fixture/#chart-studio');
 await page.evaluate(()=>{window.calls=[];const build={git_sha:'2906980d1',code_sha256:'a'.repeat(64),assets_sha256:'b'.repeat(64),on_disk_code_sha256_now:'c'.repeat(64),on_disk_assets_sha256_now:'b'.repeat(64),started_at:1757809380,port:8812,pid:79412,busy:false};window.fetch=async url=>{calls.push(String(url));let body;if(String(url).startsWith('/api/session?light=1'))body={token:'token',read_only:true,evidence_collection_available:true,api_version:2,build,...build};else if(url==='/api/evidence')body={snapshots:[]};else throw Error('Unexpected background request '+url);return {ok:true,json:async()=>body};};});
 for(const file of ['ui-state.js','coverage-view.js','app.js'])await page.addScriptTag({path:path.join(app,file)});
 await page.waitForFunction(()=>document.querySelector('#stale-banner')&&document.querySelector('#stale-banner').hidden===false);
 const message=await page.locator('#stale-banner-message').textContent();
 assert.equal(message,'This read-only console runs an older build. It shows the current build once its process is restarted.');
 assert.doesNotMatch(message,/app\.fixture|Close this tab/,'its own origin is the same stale process');
 assert.equal(await page.locator('#stale-restart').isVisible(),false,'a read-only console cannot restart itself');
 assert.deepEqual(await page.evaluate(()=>calls),['/api/session?light=1']);
 assert.deepEqual(errors,[]);
});
