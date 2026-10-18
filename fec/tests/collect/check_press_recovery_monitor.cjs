// Live smoke test against the local read-only monitor; isolated browser profile.
const {chromium}=require('C:/Users/pgoff/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8876');
 await page.waitForFunction(()=>document.querySelector('#processed').textContent!=='—');
 await page.waitForFunction(()=>document.querySelector('#body').textContent.length>200);
 assert(['Worker running','COMPLETE'].includes(await page.locator('#connection').textContent()));
 await page.getByRole('button',{name:'Pause display'}).click();
 assert.equal(await page.locator('#connection').textContent(),'Display paused');
 await page.getByRole('button',{name:'Resume display'}).click();
 await page.locator('#filter').selectOption('unavailable');
 if(await page.locator('.article').count()){
  await page.locator('.article').first().click();
  await page.waitForFunction(()=>document.querySelector('#article-meta').textContent.includes('Unavailable'));
  assert((await page.locator('#body').textContent()).length>0);
 }
 await page.locator('#search').fill('definitely-no-matching-article-xyz');
 assert.match(await page.locator('#articles').textContent(),/No completed articles/);
 await page.locator('#search').fill('');await page.locator('#filter').selectOption('recovered');
 await page.locator('.article').first().click();
 await page.waitForFunction(()=>document.querySelector('#article-meta').textContent.includes('words'));
 await page.locator('#filter').selectOption('all');
 await page.screenshot({path:path.resolve('outputs/press_recovery_20260920/display-desktop.png'),fullPage:true});
 await page.getByRole('button',{name:'Politico',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#politico-status').textContent.includes('not connected'));
 assert.match(await page.locator('#politico-panel').textContent(),/Politico news/);
 assert.equal(await page.locator('#politico-discovered').textContent(),'—');
 assert(!(await page.locator('#politico-panel').textContent()).includes('staff-directory'));
 await page.screenshot({path:path.resolve('outputs/press_recovery_20260920/politico-desktop.png'),fullPage:true});
 await page.getByRole('button',{name:'Official press',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile horizontal overflow');
 await page.screenshot({path:path.resolve('outputs/press_recovery_20260920/display-mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 const state=await (await page.request.get('http://127.0.0.1:8876/api/state')).json();
 assert(state.worker_alive||state.receipt.status==='COMPLETE');
 if(state.worker_alive)assert(Date.now()-Date.parse(state.live.heartbeat_at)<20000);
 assert(state.events.some(e=>['parsing','parsing_saved'].includes(e.kind)));
 assert.equal(state.processed,2141);assert(state.counts.recovered>=382);
 const health=await (await page.request.get('http://127.0.0.1:8876/health')).json();
 assert.equal(health.service,'melange-collection-monitor');
 const blocked=await page.request.get('http://127.0.0.1:8876/replay.json');assert.equal(blocked.status(),404);
 console.log(JSON.stringify({passed:true,processed:state.processed,counts:state.counts,active:Object.keys(state.live.active).length,pageErrors:errors,desktop:'1440px',mobile:'390px'}));
 await Promise.race([browser.close(),new Promise(resolve=>setTimeout(resolve,3000))]);process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
