/* RACF on the client (Paul, 2026-09-24), against a REAL console: the wire
   shows the browser revalidating by ETag, and the page shows the memory tier
   and the idle prefetch at work. Read-only: GETs only, no collection, no
   Insights POST is sent by this test.

   What it proves that app/tests/graph-racf.test.js cannot:
   - a repeat read of /api/graph, /api/contributions/catalog,
     /api/insights/catalog and /api/comparison-flows goes out with
     If-None-Match and the raw status on the wire is 304 (CDP ExtraInfo
     events, below the renderer, where the HTTP cache adds the header);
   - after a Graph view is drawn, the likely next views are read while the
     page is idle, at most four, one at a time;
   - choosing a prefetched company, and going back to a scope already seen,
     draws before the network answers (the revalidation that follows is a 304).

   Run: node tests/test_graph_racf_browser.cjs   (cwd: mentat/)
   MENTAT_CONSOLE_URL names the console (default http://127.0.0.1:8827); the
   suite skips when nothing answers there. Needs Playwright as the other .cjs
   tests do (MENTAT_NODE_MODULES or the codex runtime default). */
'use strict';
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(require.resolve('playwright', {paths: [process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const origin = (process.env.MENTAT_CONSOLE_URL || 'http://127.0.0.1:8827').replace(/\/$/, '');
const WATCHED = /\/api\/(graph\?|contributions\/catalog|insights\/catalog|comparison-flows\?)/;

function reachable() {
  return new Promise(resolve => {
    const request = http.get(origin + '/api/report-sources', response => { response.resume(); resolve(response.statusCode === 200); });
    request.on('error', () => resolve(false));
    request.setTimeout(5000, () => { request.destroy(); resolve(false); });
  });
}
let browser, live = false;
before(async () => {
  live = await reachable();
  if (live) browser = await chromium.launch({channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', headless: true});
});
after(async () => { if (browser) await browser.close(); });

// Every watched request on the wire: url, whether it carried If-None-Match,
// the raw status, and when it started and ended (ms, page clock).
async function wire(page) {
  const cdp = await page.context().newCDPSession(page), byId = new Map(), early = new Map(), log = [];
  await cdp.send('Network.enable');
  // The ExtraInfo events (the headers and status actually on the wire) may
  // arrive before requestWillBeSent, so they are held by id until it does.
  const note = (id, fields) => { const entry = byId.get(id); if (entry) Object.assign(entry, fields); else early.set(id, Object.assign(early.get(id) || {}, fields)); };
  cdp.on('Network.requestWillBeSent', e => {
    if (!WATCHED.test(e.request.url)) return;
    const entry = Object.assign({url: e.request.url.slice(origin.length), start: e.timestamp * 1000, inm: null, status: null, end: null}, early.get(e.requestId) || {});
    early.delete(e.requestId); byId.set(e.requestId, entry); log.push(entry);
  });
  cdp.on('Network.requestWillBeSentExtraInfo', e => note(e.requestId, {inm: e.headers['If-None-Match'] || e.headers['if-none-match'] || ''}));
  cdp.on('Network.responseReceivedExtraInfo', e => note(e.requestId, {status: e.statusCode}));
  cdp.on('Network.loadingFinished', e => { const entry = byId.get(e.requestId); if (entry) entry.end = e.timestamp * 1000; });
  cdp.on('Network.loadingFailed', e => { const entry = byId.get(e.requestId); if (entry) { entry.end = e.timestamp * 1000; entry.failed = e.canceled ? 'canceled' : e.errorText; } });
  return log;
}
async function until(page, predicate, label, argument, timeout = 120000) {
  const stop = Date.now() + timeout;
  while (Date.now() < stop) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(label + ' never happened');
}
const quiet = ms => new Promise(resolve => setTimeout(resolve, ms));
// Each drawn view, as the page announces it (graph.js dispatches mentat:graph after render()).
const listen = page => page.addInitScript(() => {
  window.__drawn = [];
  window.addEventListener('mentat:graph', e => { if (e.detail) window.__drawn.push({t: performance.now(), company: e.detail.filters.company || '', cycle: String(e.detail.filters.cycle || ''), text: document.querySelector('#graph-results')?.innerText.length || 0}); });
});
// Choose a value on a Graph select the way a reader does, and time until that view is drawn.
async function choose(page, select, value) {
  return page.evaluate(({select, value}) => new Promise((resolve, reject) => {
    const t0 = performance.now(), seen = window.__drawn.length, node = document.getElementById(select);
    node.value = value; node.dispatchEvent(new Event('change', {bubbles: true}));
    // Drawn inside the change handler itself = drawn from memory, before any network answer.
    const sync = window.__drawn.slice(seen).some(d => d[select === 'graph-company' ? 'company' : 'cycle'] === value);
    const key = select === 'graph-company' ? 'company' : 'cycle';
    const poll = () => {
      const hit = window.__drawn.slice(seen).find(d => d[key] === value);
      if (hit) resolve({ms: Math.round((hit.t - t0) * 10) / 10, sync, text: hit.text});
      else if (performance.now() - t0 > 180000) reject(new Error('view never drawn: ' + select + '=' + value));
      else setTimeout(poll, 5);
    };
    poll();
  }), {select, value});
}

test('[graph-racf browser] revalidation by ETag, idle prefetch and memory-first redraws on a real console', async t => {
  if (!live) { t.skip('no console answers at ' + origin); return; }
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const log = await wire(page);
  await listen(page);
  await page.goto(origin + '/#graph', {waitUntil: 'load'});
  await until(page, () => window.__drawn && window.__drawn.length > 0, 'the first Graph view');
  const first = await page.evaluate(() => window.__drawn[0]);
  const report = {first};
  // Idle prefetch: up to four likely-next reads follow the first view, one at a time.
  // The view's own read is the first /api/graph request; every later one before any interaction is a prefetch.
  const prefetchStart = log.findIndex(e => e.url.startsWith('/api/graph?')) + 1;
  const settle = Date.now() + 150000;
  let last = -1, stable = 0;
  while (Date.now() < settle) {
    const done = log.slice(prefetchStart).filter(e => e.url.startsWith('/api/graph?') && e.end !== null).length;
    const open = log.slice(prefetchStart).filter(e => e.url.startsWith('/api/graph?') && e.end === null).length;
    if (done === last && open === 0) { stable += 1; if (stable >= 8) break; } else stable = 0;
    last = done;
    await quiet(250);
  }
  const prefetched = log.slice(prefetchStart).filter(e => e.url.startsWith('/api/graph?'));
  report.prefetch = prefetched.map(e => ({url: e.url, status: e.status, ms: e.end && Math.round(e.end - e.start)}));
  assert.ok(prefetched.length >= 1 && prefetched.length <= 4, 'one to four likely-next reads: ' + JSON.stringify(report.prefetch));
  for (let i = 1; i < prefetched.length; i += 1) assert.ok(prefetched[i].start >= prefetched[i - 1].end - 1, 'one at a time: ' + JSON.stringify(report.prefetch));
  const planned = prefetched.map(e => new URLSearchParams(e.url.split('?')[1]));
  const companies = planned.map(q => q.get('company')).filter(Boolean);
  assert.ok(companies.length >= 1, 'the other top companies are planned first');
  // A prefetched company: drawn from memory at once; its revalidation is a 304.
  const before1 = log.length;
  report.prefetchedCompany = await choose(page, 'graph-company', companies[0]);
  await quiet(1500);
  const reval1 = log.slice(before1).filter(e => e.url === prefetched.find(p => new URLSearchParams(p.url.split('?')[1]).get('company') === companies[0]).url);
  report.prefetchedCompanyWire = reval1.map(e => ({inm: Boolean(e.inm), status: e.status, ms: e.end && Math.round(e.end - e.start)}));
  assert.ok(reval1.length >= 1 && reval1[0].inm && reval1[0].status === 304, 'the prefetched view is revalidated with If-None-Match -> 304: ' + JSON.stringify(report.prefetchedCompanyWire));
  assert.equal(report.prefetchedCompany.sync, true, 'drawn from memory inside the change handler, before the network answered: ' + JSON.stringify(report.prefetchedCompany));
  // A company NOT prefetched (cold), then back to the prefetched one (memory).
  const offered = await page.evaluate(() => [...document.getElementById('graph-company').options].map(o => o.value).filter(Boolean));
  const cold = offered.find(value => !companies.includes(value));
  if (cold) {
    await quiet(300);
    report.coldCompany = await choose(page, 'graph-company', cold);
    await quiet(1500);
    report.backToSeen = await choose(page, 'graph-company', companies[0]);
    await quiet(1500);
    assert.equal(report.coldCompany.sync, false, 'a scope never read waits for the network: ' + JSON.stringify(report.coldCompany));
    assert.equal(report.backToSeen.sync, true, 'back to a scope already seen draws from memory: ' + JSON.stringify(report.backToSeen));
  }
  // A reload: the page's memory is gone (D-039, nothing persisted), so every
  // read goes to the HTTP cache, revalidated: If-None-Match -> 304.
  const beforeReload = log.length;
  await page.reload({waitUntil: 'load'});
  await until(page, () => window.__drawn && window.__drawn.length > 0, 'the Graph view after reload');
  report.afterReloadDrawn = await page.evaluate(() => window.__drawn[0]);
  await quiet(2500);
  // Insights catalog and the funding-comparison flows, read the way their tabs read them.
  const run = await page.evaluate(() => document.getElementById('graph-run')?.value || '');
  const industry = await page.evaluate(() => document.getElementById('graph-industry')?.value || '');
  for (let pass = 0; pass < 2; pass += 1) {
    await page.evaluate(detail => window.dispatchEvent(new CustomEvent('mentat:comparison-context', {detail})), {id: run, industry});
    await quiet(3000);
  }
  await page.goto(origin + '/#insights', {waitUntil: 'load'});
  await quiet(4000);
  await page.reload({waitUntil: 'load'});
  await quiet(4000);
  const after = log.slice(beforeReload);
  const route = name => after.filter(e => e.url.includes(name)).map(e => ({url: e.url.slice(0, 90), inm: Boolean(e.inm), status: e.status}));
  report.afterReload = {graph: route('/api/graph?').slice(0, 3), contributionsCatalog: route('/api/contributions/catalog'), insightsCatalog: route('/api/insights/catalog'), comparisonFlows: route('/api/comparison-flows')};
  console.log('RACF-REPORT ' + JSON.stringify(report));
  for (const [name, rows] of Object.entries(report.afterReload)) {
    assert.ok(rows.some(r => r.inm && r.status === 304), name + ' is revalidated with If-None-Match -> 304: ' + JSON.stringify(rows));
  }
  assert.deepEqual(errors, [], 'no page errors');
  await page.close();
});
