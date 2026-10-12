/* The historical-baseline forecast card is hidden behind a per-browser setting (owner ruling,
   2026-09-30): off by default, switched on in More options. While it is off neither the card nor
   its switch is drawn, All and Core three leave it out, and a saved choice of it is never
   requested. Real Insights template and module; every response is synthetic. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(require.resolve('playwright',{paths:[process.env.MENTAT_NODE_MODULES || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app = path.resolve(__dirname,'../app');
const {PANELS} = require(path.join(app,'insights.js'));
const ALL = PANELS.map(panel => panel[0]), WITHOUT = ALL.filter(key => key !== 'forecast'), CORE = ['favorites','trends','changes'];
const TITLES = Object.fromEntries(PANELS.map(([key,title]) => [key,title]));
const SETTING = 'mentat.insights.forecast-card.v1', CHOICES = 'mentat.insights.panels.v1';
const html = '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/acu-theme.css"><link rel="stylesheet" href="/insights.css"><style>*{box-sizing:border-box}body{padding:20px;margin:0}.panel{border:1px solid #ddd}[hidden]{display:none!important}</style>'+fs.readFileSync(path.join(app,'templates/view_insights.html'),'utf8')+'<script src="/insights.js"></script>';

// A page with its own storage. `seed` is written once per tab (a reload keeps what the page saved);
// `blocked` makes browser storage throw. The synthetic server always adds a forecast panel to its reply:
// the page must not draw one it did not ask for.
async function open(browser, {seed = {}, blocked = false, cards = 3} = {}) {
  const page = await browser.newPage({viewport:{width:1440,height:1080}});
  page.errors = []; page.on('pageerror', error => page.errors.push(error.message));
  await page.route('http://insights-setting.test/**', route => {
    const file = path.basename(new URL(route.request().url()).pathname);
    return route.fulfill({contentType:file ? file.endsWith('.js') ? 'application/javascript;charset=utf-8' : 'text/css;charset=utf-8' : 'text/html;charset=utf-8',body:file ? fs.readFileSync(path.join(app,file),'utf8') : html});
  });
  await page.addInitScript(({seed,blocked,titles}) => {
    if (blocked) { Storage.prototype.getItem = () => { throw new DOMException('Blocked','SecurityError'); }; Storage.prototype.setItem = () => { throw new DOMException('Blocked','SecurityError'); }; }
    else if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded','1'); for (const [key,value] of Object.entries(seed)) localStorage.setItem(key,value); }
    window.queries = [];
    window.fetch = async (url, options = {}) => {
      const ok = data => ({ok:true,json:async () => data});
      if (url === '/api/contributions/session') return ok({token:'setting-fixture'});
      if (url === '/api/insights/catalog') return ok({datasets:[{id:'Alpha',label:'Alpha',available:true}],panels:[]});
      const query = JSON.parse(options.body); queries.push(query);
      const columns = [{key:'recipient',label:'Recipient',type:'text'},{key:'amount_cents',label:'Amount',type:'money'}];
      const rows = [{recipient:'Alpha recipient',amount_cents:125000}];
      const panels = {};
      for (const key of [...query.panels,'forecast']) panels[key] = {title:titles[key],status:'ready',summary:'Synthetic '+key,columns,rows,notices:[]};
      return ok({status:'ready',scope:{...query,as_of:'2026-09-01',baseline_cycle:'2023-2024',current_cycle:'2025-2026',basis_label:'Reported receipts'},panels,options:{companies:['Alpha Corp'],funding_sources:['employee_original'],cycles:['2023-2024','2025-2026']},provenance:{workbook:'Synthetic scope.xlsx',index_token:'synthetic-source'},notices:[]});
    };
  }, {seed,blocked,titles:TITLES});
  await page.goto('http://insights-setting.test/');
  await page.evaluate(() => { document.getElementById('view-insights').hidden = false; MentatInsights.setActive(true); });
  await settled(page, cards);
  return page;
}
// The cards are drawn once the query is answered and the results region is no longer busy.
const settled = (page, cards) => page.waitForFunction(count => document.getElementById('insights-results').getAttribute('aria-busy') === 'false' && document.querySelectorAll('#insights-results article').length === count, cards);
const requested = page => page.evaluate(() => queries.at(-1).panels);
const stored = (page, key) => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key);

(async () => {
  const browser = await chromium.launch({channel:process.env.MENTAT_BROWSER_CHANNEL || 'chrome',headless:true});
  const transmitted = [];
  try {
    // 1. Off by default: no card, no switch, and the setting waits in More options.
    const a = await open(browser);
    assert.deepEqual(await requested(a), CORE, 'The first request asks for the core three only');
    assert.equal(await a.locator('#insights-toggles input').count(), 10, 'Ten analysis switches; the forecast switch is not drawn');
    assert.equal(await a.locator('#insights-toggle-forecast').count(), 0, 'The forecast switch is not rendered while its setting is off');
    assert.equal(await a.locator('#insights-panel-forecast').count(), 0, 'The forecast card is not rendered while its setting is off');
    assert.doesNotMatch(await a.locator('#insights-toggles').innerText(), /Forecasts/, 'No Forecasts label in the rail');
    assert.equal(await a.locator('#insights-options').isHidden(), true, 'The setting sits inside the closed More options disclosure');
    await a.locator('#insights-more-options').click();
    const setting = a.getByRole('switch', {name:'Show the historical-baseline forecast card', exact:true});
    assert.equal(await setting.count(), 1, 'One labelled switch carries the setting');
    assert.equal(await setting.isChecked(), false, 'The setting is off by default');
    assert.equal(await a.locator('#insights-options').isVisible(), true);
    assert.equal(await a.locator('#insights-options').locator('#insights-show-forecast').count(), 1);
    await a.setViewportSize({width:390,height:844});
    assert.ok(await a.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'The setting fits a narrow screen');
    await a.setViewportSize({width:1440,height:1080});
    const shot = process.env.MENTAT_INSIGHTS_SETTING_SCREENSHOT; if (shot) await a.screenshot({path:shot,fullPage:true});

    // 2. All and Core three leave the card out while the setting is off, even though the reply carries a forecast panel.
    await a.locator('#insights-enable-all').click(); await settled(a, 10);
    assert.deepEqual(await requested(a), WITHOUT, 'All asks for ten analyses, never the forecast');
    assert.equal(await a.locator('#insights-panel-forecast').count(), 0, 'A forecast panel the server volunteered is not drawn');
    assert.deepEqual(await stored(a, CHOICES), WITHOUT, 'The saved choice never holds the forecast');
    assert.equal(await a.locator('#insights-toggles input:checked').count(), 10);
    await a.locator('#insights-core-only').click(); await settled(a, 3);
    assert.deepEqual(await requested(a), CORE);

    // 3. Switching the setting on shows the switch (off), fetches nothing, and draws no card by itself.
    const before = await a.evaluate(() => queries.length);
    await setting.check();
    assert.equal(await a.locator('#insights-toggle-forecast').count(), 1, 'The forecast switch appears when the setting is on');
    assert.equal(await a.locator('#insights-toggle-forecast').isChecked(), false, 'Showing the switch does not turn the card on');
    assert.equal(await a.locator('#insights-toggles input').count(), 11);
    assert.equal(await a.evaluate(() => queries.length), before, 'Showing the switch requests nothing');
    assert.equal(await a.locator('#insights-panel-forecast').count(), 0);
    assert.equal(await stored(a, SETTING), true, 'The setting is saved in this browser');

    // 4. Choosing the card asks for it and draws it.
    await a.locator('#insights-toggle-forecast').check(); await settled(a, 4);
    assert.deepEqual(await requested(a), [...CORE,'forecast']);
    assert.equal(await a.locator('#insights-panel-forecast').count(), 1, 'The forecast card renders once the setting and the switch are on');
    assert.match(await a.locator('#insights-panel-forecast').innerText(), /Forecasts/);
    await a.locator('#insights-core-only').click(); await settled(a, 3);
    await a.locator('#insights-enable-all').click(); await settled(a, 11);
    assert.deepEqual(await requested(a), ALL, 'With the setting on, All asks for all eleven analyses');
    assert.equal(await a.locator('#insights-panel-forecast').count(), 1);
    transmitted.push(...await a.evaluate(() => queries));

    // 5. The setting and the choices survive a reload.
    await a.reload();
    await a.evaluate(() => { document.getElementById('view-insights').hidden = false; MentatInsights.setActive(true); });
    await settled(a, 11);
    assert.deepEqual(await requested(a), ALL, 'A saved choice of the forecast is requested only because the setting is still on');
    await a.locator('#insights-more-options').click();
    assert.equal(await a.locator('#insights-show-forecast').isChecked(), true, 'The setting persists across reload');
    assert.equal(await a.locator('#insights-toggle-forecast').isChecked(), true);

    // 6. Switching the setting off while the card is on removes the card, its switch and its request.
    const beforeOff = await a.evaluate(() => queries.length);
    await a.locator('#insights-show-forecast').uncheck(); await settled(a, 10);
    assert.equal(await a.evaluate(() => queries.length), beforeOff + 1, 'Removing the card changes the request once');
    assert.deepEqual(await requested(a), WITHOUT, 'The forecast is no longer requested');
    assert.equal(await a.locator('#insights-panel-forecast').count(), 0);
    assert.equal(await a.locator('#insights-toggle-forecast').count(), 0);
    assert.deepEqual(await stored(a, CHOICES), WITHOUT, 'The saved choices drop the forecast');
    assert.equal(await stored(a, SETTING), false);
    // Turning it back on brings back a switch that is off, not the old card.
    const beforeOn = await a.evaluate(() => queries.length);
    await a.locator('#insights-show-forecast').check();
    assert.equal(await a.locator('#insights-toggle-forecast').isChecked(), false, 'The card does not return by itself');
    assert.equal(await a.evaluate(() => queries.length), beforeOn);
    assert.equal(await a.locator('#insights-panel-forecast').count(), 0);
    assert.deepEqual(a.errors, []);
    await a.close();

    // 7. A forecast choice saved before the setting existed is dropped, not requested.
    const stale = await open(browser, {seed:{[CHOICES]:JSON.stringify(['favorites','forecast'])}, cards:1});
    assert.deepEqual(await requested(stale), ['favorites'], 'A saved forecast choice is not requested while the setting is off');
    assert.equal(await stale.locator('#insights-toggle-forecast').count(), 0);
    assert.equal(await stale.locator('#insights-panel-forecast').count(), 0);
    transmitted.push(...await stale.evaluate(() => queries)); await stale.close();

    // 8. The setting on with no forecast choice: the switch is there and off.
    const shown = await open(browser, {seed:{[SETTING]:'true'}});
    assert.deepEqual(await requested(shown), CORE);
    assert.equal(await shown.locator('#insights-toggle-forecast').count(), 1);
    assert.equal(await shown.locator('#insights-toggle-forecast').isChecked(), false);
    await shown.close();

    // 9. A malformed setting reads as off and is not a storage failure.
    const corrupt = await open(browser, {seed:{[SETTING]:'invalid JSON'}});
    assert.equal(await corrupt.locator('#insights-toggle-forecast').count(), 0);
    assert.equal(await corrupt.locator('#insights-preference-note').isHidden(), true, 'A repaired malformed setting is not a storage failure');
    await corrupt.close();

    // 10. Blocked storage: the setting still works for this visit, and the page says nothing is saved.
    const blocked = await open(browser, {blocked:true});
    assert.equal(await blocked.locator('#insights-toggle-forecast').count(), 0);
    assert.equal(await blocked.locator('#insights-preference-note').isVisible(), true);
    await blocked.locator('#insights-more-options').click(); await blocked.locator('#insights-show-forecast').check();
    assert.equal(await blocked.locator('#insights-toggle-forecast').count(), 1, 'The setting applies to this visit');
    assert.equal(await blocked.locator('#insights-preference-note').isVisible(), true);
    assert.deepEqual(blocked.errors, []);
    await blocked.close();

    // Every request the page formed is one the server accepts.
    execFileSync(process.env.MENTAT_PYTHON || 'python', ['-c', 'import json,sys; sys.path.insert(0,sys.argv[1]); from analysis_data import validate; [validate(item) for item in json.loads(sys.argv[2])]', app, JSON.stringify(transmitted)], {encoding:'utf8',windowsHide:true});
    console.log('PASS Insights forecast setting: off by default (no card, no switch, absent from All), on in More options (switch, card, All), persistence, stale choices dropped, malformed and blocked storage');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
