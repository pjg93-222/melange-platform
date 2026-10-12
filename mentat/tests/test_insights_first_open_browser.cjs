/* Insights first open and status polling through the real Insights module.
   Synthetic responses only: no report, no server, no FEC data (perf,
   2026-09-24).

   What this proves that app/tests/insights-load.test.js cannot:
   1. With the industry known from the URL scope, the first query leaves as
      soon as the session token arrives, while the catalog is still loading;
      its reply is held until the catalog is installed and then renders - one
      query, and the same page the catalog-first order produces.
   2. When the catalog's arrival changes the body (the shared industry moved
      while it loaded), the early query is abandoned and the formed body is
      sent; the abandoned query can no longer answer.
   3. Status polls follow the server's retry_after_ms down to 200 ms, and an
      old server's 1500 ms is still honoured exactly (a fake clock, so no
      timing flake).
   4. An early reply's wait counts from its arrival: a 1500 ms reply that came
      while the catalog was loading is polled again 1500 ms after it arrived,
      not 1500 ms after the catalog.

   Run: node tests/test_insights_first_open_browser.cjs   (cwd: mentat/)
   MENTAT_INSIGHTS_SCRIPT serves another insights.js (the negative control).
   Needs MENTAT_NODE_MODULES pointing at a Playwright install, or the default
   ~/.cache/codex-runtimes/... path used by every other .cjs test here. */
'use strict';
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(require.resolve('playwright', {paths: [process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app = path.resolve(__dirname, '../app');
const script = process.env.MENTAT_INSIGHTS_SCRIPT ? path.resolve(process.env.MENTAT_INSIGHTS_SCRIPT) : path.join(app, 'insights.js');
const html = '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/acu-theme.css"><link rel="stylesheet" href="/insights.css"><style>[hidden]{display:none!important}</style>'
  + fs.readFileSync(path.join(app, 'templates/view_insights.html'), 'utf8') + '<script src="/scope.js"></script><script src="/insights.js"></script>';
const CATALOG = {datasets: [{id: 'Alpha', label: 'Alpha industry', available: true}, {id: 'Beta', label: 'Beta industry', available: true}], panels: []};
function result(dataset) {
  const out = {status: 'ready', scope: {dataset, basis: 'receipt', basis_label: 'Reported receipts', as_of: '2026-09-01', baseline_cycle: '2023-2024', current_cycle: '2025-2026', company: null, funding_source: null},
    provenance: {workbook: dataset + ' synthetic.xlsx', index_token: 'first-open-fixture'},
    options: {companies: [dataset + ' Corp'], funding_sources: ['employee'], cycles: ['2023-2024', '2025-2026']}, notices: ['Synthetic fixture.'], panels: {}};
  for (const key of ['favorites', 'trends', 'changes']) out.panels[key] = {status: 'ready', title: key, summary: dataset + ' synthetic ' + key,
    columns: [{key: 'recipient', label: 'Recipient', type: 'text'}, {key: 'amount', label: 'Amount', type: 'money'}], rows: [{recipient: dataset + ' candidate', amount: 125000}], notices: []};
  return out;
}
let browser;
before(async () => { browser = await chromium.launch({channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', headless: true}); });
after(async () => { if (browser) await browser.close(); });

// Every request is held until the test answers it; an aborted one settles as an AbortError.
function stubFetch() {
  window.calls = [];
  window.fetch = (url, options = {}) => new Promise((resolve, reject) => {
    const call = {url: String(url), body: options.body ? JSON.parse(options.body) : null, token: options.headers ? options.headers['X-Mentat-Token'] : undefined, settled: false, aborted: false};
    call.answer = data => { if (call.settled) return false; call.settled = true; resolve({ok: true, json: async () => data}); return true; };
    if (options.signal) options.signal.addEventListener('abort', () => { call.aborted = true; if (!call.settled) { call.settled = true; reject(new DOMException('The operation was aborted.', 'AbortError')); } });
    window.calls.push(call);
  });
  window.answer = (index, data) => window.calls[index].answer(data);
  window.seen = () => window.calls.map(call => ({url: call.url, dataset: call.body ? call.body.dataset : undefined, token: call.token, settled: call.settled, aborted: call.aborted}));
}
async function open(search, clock = false) {
  const context = await browser.newContext({viewport: {width: 1280, height: 900}}), page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://insights-first-open.test/**', route => {
    const name = path.basename(new URL(route.request().url()).pathname);
    if (!name) return route.fulfill({contentType: 'text/html;charset=utf-8', body: html});
    return route.fulfill({contentType: name.endsWith('.js') ? 'application/javascript;charset=utf-8' : 'text/css;charset=utf-8',
      body: fs.readFileSync(name === 'insights.js' ? script : path.join(app, name), 'utf8')});
  });
  await page.addInitScript(stubFetch);
  if (clock) await page.clock.install({time: new Date('2026-09-24T12:00:00-04:00')});
  await page.goto('http://insights-first-open.test/' + search);
  if (clock) await page.clock.pauseAt(new Date('2026-09-24T12:00:05-04:00'));
  await page.evaluate(() => { document.getElementById('view-insights').hidden = false; MentatInsights.setActive(true); });
  return {context, page, errors};
}
// Polls from Node, never from page timers, so a paused page clock cannot stall it.
async function until(page, predicate, label, argument) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(label + ' never happened. Requests: ' + JSON.stringify(await page.evaluate(() => seen())));
}
const settle = () => new Promise(resolve => setTimeout(resolve, 100));
const seen = page => page.evaluate(() => seen());
const answer = (page, index, data) => page.evaluate(([index, data]) => answer(index, data), [index, data]);
const readyCards = page => page.evaluate(() => document.querySelectorAll('#insights-results article[data-state="ready"]').length);
const rendered = page => page.evaluate(() => ({results: document.getElementById('insights-results').innerHTML, scope: document.getElementById('insights-scope').innerHTML,
  summary: document.getElementById('insights-summary').innerHTML, status: document.getElementById('insights-status').textContent,
  dataset: document.getElementById('insights-dataset').value, options: Array.from(document.getElementById('insights-dataset').options, option => option.textContent)}));
// The poll the page schedules after a `preparing` reply leaves at exactly `ms`.
async function pollAt(page, ms, count) {
  await settle();
  await page.clock.runFor(ms - 1); await settle();
  assert.equal((await seen(page)).length, count - 1, 'no status poll before ' + ms + ' ms');
  await page.clock.runFor(1);
  await until(page, n => calls.length === n, 'the status poll at ' + ms + ' ms', count);
}

test('the first query leaves with the session token, beside the catalog, and renders as the catalog-first order does', async () => {
  const early = await open('?industry=Beta');
  try {
    await until(early.page, () => calls.length === 2, 'the catalog and session requests');
    assert.deepEqual((await seen(early.page)).map(call => call.url), ['/api/insights/catalog', '/api/contributions/session']);
    await answer(early.page, 1, {token: 'first-open-token'});
    await until(early.page, () => calls.length === 3, 'the Beta query leaving while the catalog is still loading');
    const sent = (await seen(early.page))[2];
    assert.deepEqual([sent.url, sent.dataset, sent.token], ['/api/insights/query', 'Beta', 'first-open-token']);
    assert.equal((await seen(early.page))[0].settled, false, 'the catalog has not answered yet');
    await answer(early.page, 2, result('Beta')); await settle();
    assert.equal(await readyCards(early.page), 0, 'nothing renders before the catalog is installed');
    await answer(early.page, 0, CATALOG);
    await until(early.page, () => document.querySelectorAll('#insights-results article[data-state="ready"]').length === 3, 'the Beta result rendering');
    await settle();
    assert.equal((await seen(early.page)).length, 3, 'one query: the early reply answered the formed body');
    assert.match(await early.page.locator('#insights-scope strong').textContent(), /^Beta industry/);
    assert.deepEqual(early.errors, []);
    // The catalog answering first is the old order: the formed body is sent after it.
    const sequential = await open('?industry=Beta');
    try {
      await until(sequential.page, () => calls.length === 2, 'the catalog and session requests');
      await answer(sequential.page, 0, CATALOG); await settle();
      await answer(sequential.page, 1, {token: 'first-open-token'});
      await until(sequential.page, () => calls.length === 3, 'the query after the catalog'); await settle();
      assert.equal((await seen(sequential.page)).length, 3, 'nothing is sent early once the catalog is here');
      assert.equal((await seen(sequential.page))[2].dataset, 'Beta');
      await answer(sequential.page, 2, result('Beta'));
      await until(sequential.page, () => document.querySelectorAll('#insights-results article[data-state="ready"]').length === 3, 'the Beta result rendering');
      assert.deepEqual(await rendered(early.page), await rendered(sequential.page), 'the early order renders the same page as the catalog-first order');
      assert.deepEqual(sequential.errors, []);
    } finally { await sequential.context.close(); }
  } finally { await early.context.close(); }
});

test('when the catalog changes the body, the early query is abandoned and the formed body is sent', async () => {
  const {context, page, errors} = await open('?industry=Beta');
  try {
    await until(page, () => calls.length === 2, 'the catalog and session requests');
    await answer(page, 1, {token: 'first-open-token'});
    await until(page, () => calls.length === 3, 'the early Beta query');
    // The shared industry moves while the catalog loads (another workspace, or history).
    await page.evaluate(() => MentatScope.write({industry: 'Alpha'}));
    await answer(page, 0, CATALOG);
    await until(page, () => calls.length === 4, 'the formed Alpha query');
    const [, , abandoned, formed] = await seen(page);
    assert.equal(abandoned.dataset, 'Beta'); assert.equal(abandoned.aborted, true, 'the early query is abandoned');
    assert.deepEqual([formed.url, formed.dataset], ['/api/insights/query', 'Alpha']);
    assert.equal(await answer(page, 2, result('Beta')), false, 'the abandoned query can no longer answer');
    await answer(page, 3, result('Alpha'));
    await until(page, () => document.querySelectorAll('#insights-results article[data-state="ready"]').length === 3, 'the Alpha result rendering');
    assert.match(await page.locator('#insights-scope strong').textContent(), /^Alpha industry/);
    assert.equal(await page.locator('#insights-dataset').inputValue(), 'Alpha');
    assert.doesNotMatch(await page.locator('#insights-results').innerText(), /Beta/);
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});

test('status polls follow the server pace down to 200 ms; an old server is still polled every 1.5 s', async () => {
  const {context, page, errors} = await open('', true);
  try {
    await until(page, () => calls.length === 2, 'the catalog and session requests');
    await answer(page, 1, {token: 'poll-token'}); await settle();
    assert.equal((await seen(page)).length, 2, 'with no industry known, the catalog chooses: nothing is sent early');
    await answer(page, 0, CATALOG);
    await until(page, () => calls.length === 3, 'the query after the catalog');
    assert.equal((await seen(page))[2].dataset, 'Alpha', 'the catalog chose its first industry');
    await answer(page, 2, {status: 'preparing', message: 'Preparing fixture', retry_after_ms: 1500});
    await pollAt(page, 1500, 4);
    await answer(page, 3, {status: 'preparing', retry_after_ms: 300});
    await pollAt(page, 300, 5);
    await answer(page, 4, {status: 'preparing', retry_after_ms: 750});
    await pollAt(page, 750, 6);
    await answer(page, 5, {status: 'preparing', retry_after_ms: 50});
    await pollAt(page, 200, 7);
    await answer(page, 6, result('Alpha'));
    await until(page, () => document.querySelectorAll('#insights-results article[data-state="ready"]').length === 3, 'the Alpha result rendering');
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});

test('an early reply waits its retry_after_ms from its own arrival, not from the catalog', async () => {
  const {context, page, errors} = await open('?industry=Beta', true);
  try {
    await until(page, () => calls.length === 2, 'the catalog and session requests');
    await answer(page, 1, {token: 'first-open-token'});
    await until(page, () => calls.length === 3, 'the early Beta query');
    await answer(page, 2, {status: 'preparing', message: 'Preparing fixture', retry_after_ms: 1500});
    await settle(); await page.clock.runFor(1000); await settle();
    await answer(page, 0, CATALOG);
    await pollAt(page, 500, 4);
    assert.equal((await seen(page))[3].dataset, 'Beta');
    await answer(page, 3, result('Beta'));
    await until(page, () => document.querySelectorAll('#insights-results article[data-state="ready"]').length === 3, 'the Beta result rendering');
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});