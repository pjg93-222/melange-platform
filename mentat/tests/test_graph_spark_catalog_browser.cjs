/* Company sparklines' contributions catalog: one read per page, beside
   /api/graph (perf H11, 2026-09-24). Real graph.js, synthetic answers only:
   no report, no server, no FEC data.

   What this proves that app/tests/graph-spark-access.test.js cannot: the
   catalog request leaves while the first /api/graph is still loading (it used
   to wait for that answer and its render); an industry switch asks for its own
   session but not for the catalog again; the sparklines still draw; and a
   failed catalog read is read again at the next switch, then kept.

   Run: node tests/test_graph_spark_catalog_browser.cjs   (cwd: mentat/)
   MENTAT_GRAPH_SCRIPT serves another graph.js (the negative control).
   Needs MENTAT_NODE_MODULES pointing at a Playwright install, or the default
   ~/.cache/codex-runtimes/... path used by every other .cjs test here. */
'use strict';
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(require.resolve('playwright', {paths: [process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app = process.env.MENTAT_APP_DIR ? path.resolve(process.env.MENTAT_APP_DIR) : path.resolve(__dirname, '../app');
const script = process.env.MENTAT_GRAPH_SCRIPT ? path.resolve(process.env.MENTAT_GRAPH_SCRIPT) : path.join(app, 'graph.js');
const rows = (name, count) => Array.from({length: count}, (_, i) => ({id: name + '-' + i, label: name + ' label ' + i, amount: (count - i) * 1000 + 0.25, ...(name === 'candidates' ? {party: 'Fixture'} : {})}));
const FIXTURES = {
  graph: {options: {companies: ['Fixture Company'], cycles: ['2024'], unknown_tiers: false, recipients: []},
    filters: {company: '', cycle: '2024', tier: '', mode: 'direct', direction: 'Support'},
    totals: {receipt_groups: 12, giving: 123456.78, direct: 23456.78, organizations: 1000, unresolved_candidate_links: 0},
    candidate_available: true, candidate_note: 'Synthetic candidates, no real donor records.', note: 'Offline fixture only.',
    reports: [{name: 'Fixture workbook.xlsx', sha256: 'a'.repeat(64)}], candidate_details: [],
    categories: rows('categories', 3), organizations: rows('organizations', 3), candidates: rows('candidates', 3), companies: rows('companies', 3), levels: rows('levels', 2)},
  catalog: {datasets: [{id: 'Synthetic', label: 'Synthetic', available: true}, {id: 'Other', label: 'Other', available: true}]},
  query: {status: 'ready', rows: ['companies-0', 'companies-1', 'companies-2'].flatMap((company, at) => ['2025-Q1', '2025-Q2', '2025-Q3', '2025-Q4'].map((quarter, q) => ({company, quarter, amount: (at + 1) * 10000 + q * 2500})))}
};
let browser;
before(async () => { browser = await chromium.launch({channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', headless: true}); });
after(async () => { if (browser) await browser.close(); });

async function open(options) {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.abort());
  await page.setContent(fs.readFileSync(path.join(app, 'index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, ''));
  for (const sheet of ['style.css', 'graph.css', 'workspace.css']) await page.addStyleTag({path: path.join(app, sheet)});
  await page.addScriptTag({path: path.join(app, 'theme.js')});
  await page.evaluate(({fixtures, options}) => {
    window.currentView = 'graph'; window.splitView = false; window.selectedRun = 'fixture';
    document.querySelector('#view-loading').hidden = true; document.querySelector('#view-graph').hidden = false;
    window.MentatTheme = Object.assign({}, window.MentatTheme, {palette: ['#13717d', '#806bb1'], ink: '#263c48', muted: '#57666e', negative: '#be592f', grid: '#ddd', sans: 'Arial'});
    window.api = async url => url.startsWith('/api/report-sources') ? {runs: [{id: 'fixture', name: 'Offline fixture', state: 'completed'}]} : {industries: [{name: 'Synthetic'}, {name: 'Other'}]};
    window.calls = []; window.heldGraph = []; window.holdGraph = Boolean(options.holdFirstGraph); window.heldCatalog = []; window.holdCatalog = Boolean(options.holdFirstCatalog);
    const reply = data => ({ok: true, json: async () => JSON.parse(JSON.stringify(data))});
    window.fetch = url => {
      const where = String(url); window.calls.push(where);
      if (where.startsWith('/api/graph?')) return new Promise(resolve => { const answer = () => resolve(reply(fixtures.graph)); if (window.holdGraph) window.heldGraph.push(answer); else answer(); });
      if (where.startsWith('/api/contributions/session')) return Promise.resolve(reply({token: 'spark-token'}));
      if (where === '/api/contributions/catalog') return new Promise(resolve => {
        const answer = ok => resolve(ok ? reply(fixtures.catalog) : {ok: false, json: async () => ({error: 'Synthetic catalog failure'})});
        if (window.holdCatalog) window.heldCatalog.push(answer); else answer(true);
      });
      if (where === '/api/contributions/query') return Promise.resolve(reply(fixtures.query));
      return Promise.resolve(reply({}));
    };
    window.releaseGraph = () => { window.holdGraph = false; for (const answer of window.heldGraph.splice(0)) answer(); };
    window.answerCatalog = ok => { window.holdCatalog = false; for (const answer of window.heldCatalog.splice(0)) answer(ok); };
    window.count = prefix => window.calls.filter(url => url.startsWith(prefix)).length;
  }, {fixtures: FIXTURES, options});
  await page.addScriptTag({path: script});
  return {page, errors};
}
async function until(page, predicate, label, argument) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(label + ' never happened. Requests: ' + JSON.stringify(await page.evaluate(() => calls)));
}
const sparks = () => document.querySelectorAll('#graph-companies polyline.graph-spark-line').length > 0;
const counts = page => page.evaluate(() => ({graph: count('/api/graph?'), catalog: count('/api/contributions/catalog'), sessions: calls.filter(url => url.startsWith('/api/contributions/session')), queries: count('/api/contributions/query')}));
async function switchTo(page, industry) {
  await page.evaluate(name => { const select = document.getElementById('graph-industry'); select.value = name; select.dispatchEvent(new Event('change')); }, industry);
}

test('the catalog read leaves beside the first /api/graph, not after its answer', async () => {
  const {page, errors} = await open({holdFirstGraph: true});
  try {
    await until(page, () => count('/api/graph?') === 1, 'the first /api/graph request');
    await until(page, () => count('/api/contributions/catalog') === 1, 'the catalog request while /api/graph is still loading');
    assert.equal(await page.evaluate(() => heldGraph.length), 1, '/api/graph has not answered');
    await page.evaluate(() => releaseGraph());
    await until(page, sparks, 'the company sparklines drawing');
    const seen = await counts(page);
    assert.equal(seen.catalog, 1); assert.deepEqual(seen.sessions, ['/api/contributions/session?industry=Synthetic']); assert.equal(seen.queries, 1);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('an industry switch asks for its own session but not for the catalog again', async () => {
  const {page, errors} = await open({});
  try {
    await until(page, sparks, 'the company sparklines drawing');
    await switchTo(page, 'Other');
    await until(page, () => count('/api/contributions/query') === 2, 'the Other sparkline query');
    await until(page, sparks, 'the Other sparklines drawing');
    const seen = await counts(page);
    assert.equal(seen.graph, 2);
    assert.deepEqual(seen.sessions, ['/api/contributions/session?industry=Synthetic', '/api/contributions/session?industry=Other']);
    assert.equal(seen.catalog, 1, 'the industry-independent catalog is read once per page');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('a failed catalog read is read again at the next switch, and then kept', async () => {
  const {page, errors} = await open({holdFirstCatalog: true});
  try {
    await until(page, () => count('/api/contributions/catalog') === 1 && document.querySelector('#graph-companies svg') !== null, 'the first render while the catalog is loading');
    await until(page, () => count('/api/contributions/session') === 1, 'the sparklines waiting on that catalog read');
    await page.evaluate(() => answerCatalog(false));
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(await page.evaluate(sparks), false, 'no sparklines without the catalog; the bars stay');
    await switchTo(page, 'Other');
    await until(page, sparks, 'the Other sparklines drawing after a second catalog read');
    assert.equal((await counts(page)).catalog, 2, 'the failed read is forgotten and read again');
    await switchTo(page, 'Synthetic');
    await until(page, () => count('/api/contributions/query') === 2, 'the Synthetic sparkline query');
    await until(page, sparks, 'the Synthetic sparklines drawing');
    const seen = await counts(page);
    assert.equal(seen.catalog, 2, 'the good read is kept for every later industry');
    assert.deepEqual(seen.sessions, ['/api/contributions/session?industry=Synthetic', '/api/contributions/session?industry=Other', '/api/contributions/session?industry=Synthetic']);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});