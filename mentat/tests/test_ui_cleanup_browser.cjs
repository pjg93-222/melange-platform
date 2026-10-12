/* UI clean-up (2026-09-17), real-client pass for Graph and Chart Studio.
 *
 * What this proves that a unit test cannot: the global "Rows per chart" input
 * is gone and each Graph panel slices its own Top N; every bar carries its
 * share of the KPI its panel belongs to, and those shares add up to the
 * coverage figure in the panel footer; the KPI strip is three tiles with two
 * share lines; party dots and company sparklines really draw; Chart Studio's
 * chrome is two rows with one "Add to briefing", a Fit select at its intrinsic
 * width, one scroll container in the Inspector, a title that never reads
 * "Chart Studio", and one Show Me block that the toolbar button opens inside
 * the Inspector rather than duplicating.
 *
 * Design: audits/mentat_failings_20260915/UI_CLEANUP_DESIGN_2026-09-17.md
 * (Graph items 1-12, Chart Studio items 13-18; the Show Me popover was
 * dropped by Paul's later ruling, so the toolbar button opens the Inspector's
 * own disclosure).
 *
 * Spawn pattern and stubbing: tests/test_navigation_scope_browser.cjs - a real
 * `python app/server.py` on a free port with a throwaway --data-root, the
 * contribution dataset stubbed because no real snapshot exists in a throwaway
 * root.
 *
 * Run: node tests/test_ui_cleanup_browser.cjs   (cwd: mentat/)
 * Needs MENTAT_NODE_MODULES pointing at a Playwright install, or the default
 * ~/.cache/codex-runtimes/... path used by every other .cjs test here.
 */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const net = require('node:net');

const MENTAT_DIR = path.resolve(__dirname, '..');
const CONTRACTS = path.resolve(__dirname, '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const snapshotRegistry = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'snapshot_registry.json'), 'utf8'));

const FORBIDDEN_PORTS = new Set([8802, 8806, 8808, 8841]);
const trace = message => { if (process.env.MENTAT_BROWSER_TRACE) console.log(`[browser] ${message}`); };

function resolvePlaywright() {
  const modules = process.env.MENTAT_NODE_MODULES
    || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
  try {
    return {chromium: require(require.resolve('playwright', {paths: [modules]})).chromium, modules};
  } catch (error) {
    return {error, modules};
  }
}
function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} exceeded its ${ms}ms bound`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
function freePort() {
  const attemptOnce = () => new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
  const pick = async () => {
    for (;;) {
      const port = await attemptOnce();
      if (!FORBIDDEN_PORTS.has(port)) return port;
    }
  };
  return withTimeout(pick(), 10000, 'freePort()');
}
function startServer(port, dataRoot, reportRoot) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.MENTAT_PYTHON || 'python',
      ['app/server.py', '--port', String(port), '--data-root', dataRoot],
      {cwd: MENTAT_DIR, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
       // Without a report directory of its own this console resolves the configured
       // one - Paul's published reports - and chdir()s into it on every run.
       env: Object.assign({}, process.env, {MENTAT_REPORT_DIRECTORY: reportRoot})});
    const stderrLines = [];
    let stdout = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error(`server on port ${port} did not print a ready line within 30s; stderr: ${stderrLines.join('')}`));
    }, 30000);
    child.stdout.on('data', chunk => {
      stdout += chunk.toString();
      trace(`server ${port} stdout: ${chunk.toString().trim()}`);
      if (!settled && new RegExp(`Mentat app: http://127\\.0\\.0\\.1:${port}\\b`).test(stdout)) {
        settled = true; clearTimeout(timer); resolve(child);
      }
    });
    child.stderr.on('data', chunk => stderrLines.push(chunk.toString()));
    child.on('exit', code => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      reject(new Error(`server on port ${port} exited ${code}; stderr: ${stderrLines.join('')}`));
    });
    child.on('error', error => { if (!settled) { settled = true; clearTimeout(timer); reject(error); } });
  });
}
function stopServer(child) {
  return withTimeout(new Promise(resolve => {
    if (!child || child.exitCode !== null) { resolve(); return; }
    child.once('exit', () => resolve());
    child.kill();
  }), 10000, 'stopServer()').catch(error => { console.error(error.message); });
}
// server.py chdir()s into its report directory (MENTAT_REPORT_DIRECTORY). With MENTAT_PYTHON set to
// a venv's python.exe, the child stopServer() kills is the venv launcher (bpo-35797) and the console,
// the launcher's child, is still exiting - and still in that folder - when the launcher's exit is
// reported. Node 24's rmSync then throws EPERM at once, whatever maxRetries and retryDelay say, which
// left an empty %TEMP% *-reports-* folder after every such run. So wait for the folder to be free, up
// to 10 s, pass or fail: the strip suite's cleanupPath (lanes L2 and L3, 2026-09-26).
async function cleanupPath(target) {
  for (let attempt = 1; ; attempt += 1) {
    try { fs.rmSync(target, {recursive: true, force: true}); return; }
    catch (error) {
      if (attempt >= 40 || !['EPERM', 'EBUSY', 'ENOTEMPTY', 'EACCES'].includes(error.code)) {
        console.error(`cleanup failed for ${target} after ${attempt} attempt(s): ${error.message}`);
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
}

/* ------------------------------------------------------------- the fixtures */
const COMMON_FIELDS = [
  {id: 'company', label: 'Company', name: 'Company', role: 'dimension', type: 'category'},
  {id: 'cycle', label: 'Election cycle', name: 'Election cycle', role: 'dimension', type: 'category'},
  {id: 'quarter', label: 'Contribution quarter', name: 'Contribution quarter', role: 'dimension', type: 'category'},
  {id: 'amount', label: 'Selected amount', name: 'Selected amount', role: 'measure', type: 'number', unit: 'cents'},
  {id: 'records', label: 'Observation count', name: 'Observation count', role: 'measure', type: 'number', unit: 'count'},
  {id: 'contributors', label: 'Distinct reported donor identities', name: 'Distinct reported donor identities', role: 'measure', type: 'number', unit: 'count'}
];
const BASES = ['receipt', 'raw'].map(id => ({id, label: id, status: 'observed'}));
const BANKING = {
  id: 'Banking', industry: 'Banking', label: 'Banking', available: true, message: null,
  fields: COMMON_FIELDS, bases: BASES, default_basis: 'receipt',
  provenance: {
    workbook: 'Banking Industry Spending Overview.xlsx', workbook_sha256: 'a'.repeat(64),
    data_workbook: null, data_workbook_sha256: null, data_lineage_matches: null,
    destination_schema: 1, database_sha256: 'a'.repeat(64), payload_sha256: 'a'.repeat(64),
    original_database_sha256: 'a'.repeat(64), accounting_engine_sha256: 'e'.repeat(64),
    cycle_basis: 'Contribution date, calendar election cycle',
    scope: 'UI clean-up browser-test fixture. Not real collection data.'
  }
};
const REGISTRY_ENTRIES = snapshotRegistry.examples[0].snapshots;
// Registered election cycles still drive the main Graph scope. Sparklines use
// the separate observed-quarter fixture below.
const GRAPH_CYCLES = ['2022', '2024', '2026'];
const GRAPH_QUARTERS = ['2025-Q1','2025-Q2','2025-Q3','2025-Q4','2026-Q1','2026-Q2','2026-Q3','2026-Q4'];
const PARTIES = ['DEM', 'REP', 'DEM', 'IND', 'REP', 'DEM'];
const GIVING = 1200000, DIRECT = 216000, ORGANIZATIONS = 982000;
// Candidate rows are a subset of "direct to candidates" in the real payload
// (graph_data.py builds them from the direct rows that carry a unique
// authorized candidate link), so the fixture keeps their sum under that KPI.
const rows = (name, n = 6) => Array.from({length: n}, (_, i) => {
  const row = {id: `${name}-${i}`, label: `${name} label ${i}`,
    amount: Math.round((100000 - i * 11000) * (name === 'candidates' ? 0.33 : 1))};
  if (name === 'candidates') row.party = PARTIES[i % PARTIES.length];
  return row;
});
const LEVELS = [
  {id: 'Employees', label: 'Employees', amount: 300000},
  {id: 'Middle-Management', label: 'Middle-Management', amount: 120000},
  {id: 'Chief', label: 'Chief', amount: 40000}
];
function graphView(query) {
  const industry = query.get('industry') || 'Banking';
  const cycle = query.get('latest') === '1' ? GRAPH_CYCLES[GRAPH_CYCLES.length - 1] : (query.get('cycle') || '');
  // Each cycle is a different size, so a sparkline has a shape to draw.
  const factor = cycle === '2022' ? 0.6 : cycle === '2024' ? 0.85 : 1;
  const scaled = list => list.map(row => ({...row, amount: Math.round(row.amount * factor)}));
  return {
    options: {companies: ['Fixture Bank', 'Second Fixture Bank'], cycles: GRAPH_CYCLES,
      unknown_tiers: false, recipients: []},
    filters: {company: query.get('company') || '', cycle, tier: query.get('tier') || '',
      mode: query.get('mode') || 'direct', direction: query.get('direction') || 'Support'},
    totals: {receipt_groups: 2047, giving: GIVING, direct: DIRECT, organizations: ORGANIZATIONS,
      unresolved_candidate_links: 1450},
    candidate_available: true, candidate_note: 'Synthetic candidates, no real donor records.',
    note: 'Offline fixture only.',
    reports: [{name: `${industry} fixture workbook.xlsx`, sha256: 'c'.repeat(64), freshness: {sources: []}}],
    candidate_details: [],
    categories: scaled(rows('categories')), organizations: scaled(rows('organizations')),
    candidates: scaled(rows('candidates')), companies: scaled(rows('companies')), levels: scaled(LEVELS)
  };
}

let stepNumber = 0, passes = 0;
function step(label) { stepNumber += 1; console.log(`STEP ${stepNumber}: ${label}`); }
function pass(detail) { passes += 1; console.log(`PASS step ${stepNumber} - ${detail}`); }
function setup(label) { console.log(`SETUP: ${label}`); }

/* The visibility rule this suite uses everywhere: an element nobody can see or
   press does not count. `checkVisibility` knows about `[hidden]`, `display`
   and `visibility`; the closed-<details> rule is spelled out because Chrome
   hides disclosure content with content-visibility, which keeps the last
   laid-out rectangle and would otherwise measure as a visible box. */
const VISIBLE_SOURCE = `node => {
  if (!node) return false;
  if (node.closest('[hidden]')) return false;
  const closed = node.closest('details:not([open])');
  if (closed && !(node.tagName === 'SUMMARY' && node.parentElement === closed)) return false;
  if (typeof node.checkVisibility === 'function'
    && !node.checkVisibility({contentVisibilityAuto: true, visibilityProperty: true})) return false;
  const rect = node.getBoundingClientRect();
  if (!rect.width && !rect.height) return false;
  const style = getComputedStyle(node);
  return style.display !== 'none' && style.visibility !== 'hidden';
}`;

(async () => {
  const playwright = resolvePlaywright();
  if (playwright.error) {
    console.error('FAIL: playwright could not be resolved at ' + playwright.modules
      + ' (' + playwright.error.message + '). This suite is a page test; there is no node-driven substitute.');
    process.exitCode = 1;
    return;
  }
  const {chromium} = playwright;
  let browser = null, server = null, failed = false;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-ui-cleanup-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-ui-cleanup-reports-'));
  // The studio's first contribution query is held until the title has been
  // read, so "before a chart is drawn" is a state this test can observe
  // instead of a race it has to win.
  let releaseQuery = null;
  const held = new Promise(resolve => { releaseQuery = resolve; });
  let holdFirstQuery = false, heldOnce = false, graphRequests = 0, quarterlyQueries = 0;

  async function installRoutes(page) {
    await page.route(target => target.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url());
      const where = target.pathname;
      if (where === '/api/contributions/session') {
        return route.fulfill({json: {token: 'ui-cleanup-token', read_only: false,
          industry: 'Banking', industries: REGISTRY_ENTRIES}});
      }
      if (where === '/api/contributions/catalog') {
        return route.fulfill({json: {datasets: [BANKING], fields: COMMON_FIELDS, amount_unit: 'cents'}});
      }
      if (where === '/api/contributions/query') {
        if (holdFirstQuery && !heldOnce) { heldOnce = true; await held; }
        const body = route.request().postDataJSON() || {};
        const dims = body.dimensions && body.dimensions.length ? body.dimensions : ['company'];
        if(dims.includes('quarter'))quarterlyQueries += 1;
        const made = dims.includes('quarter')
          ? rows('companies').flatMap((company,i)=>GRAPH_QUARTERS.filter(quarter=>i!==0||quarter!=='2025-Q4').map((quarter,q)=>({company:company.id,quarter,
              amount:Math.round(company.amount*100*(0.52+q*0.07+i*0.01)),records:q+1,contributors:q+1})))
          : ['Goldman Fixture', 'Wells Fixture', 'Morgan Fixture'].map((who, i) => Object.assign(
              {}, ...dims.map(d => ({[d]: d === 'cycle' ? '2023-2024' : who})),
              {amount: 260398435 - i * 70000000, records: 3 - i, contributors: 3 - i}));
        return route.fulfill({json: {status: 'ready', rows: made,
          columns: [...dims, ...(body.measures || ['amount'])].map(id => COMMON_FIELDS.find(f => f.id === id) || {id, label: id}),
          totals: {amount: 1200318112, records: 9, contributors: 9}, group_count: made.length,
          offset: 0, has_more: false, provenance: BANKING.provenance,
          basis_label: 'Published included receipts',
          reconciliation: {conserved: true, receipt_matches_workbook: true},
          notices: [], verified_unique: false}});
      }
      if (where === '/api/report-sources') {
        return route.fulfill({json: {runs: [{id: 'published-reports', name: 'Published reports',
          state: 'completed', source_directory: 'fixture'}], report_directory: 'fixture'}});
      }
      if (where === '/api/graph-catalog') {
        return route.fulfill({json: {industries: [{name: 'Banking', indices: [0]}],
          unavailable_reports: [], report_directory: 'fixture', message: ''}});
      }
      if (where === '/api/graph') { graphRequests += 1; return route.fulfill({json: graphView(target.searchParams)}); }
      if (where === '/api/cross-reference') {
        return route.fulfill({json: {available: false, status: 'empty', industry: 'Banking', message: 'stub'}});
      }
      return route.continue();
    });
  }

  // 30s, not 20s: this box runs the whole browser sweep back to back and the first
  // Graph load has lost that race once.
  async function until(target, predicate, label, diagnose, attempts = 120) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await target.evaluate(predicate)) return;
      await target.waitForTimeout(250);
    }
    const seen = diagnose ? await target.evaluate(diagnose) : null;
    throw new Error(label + ' never happened' + (seen ? '. Page: ' + JSON.stringify(seen) : '.'));
  }

  try {
    setup('pick a free port and launch the console server on a throwaway --data-root');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    const origin = `http://127.0.0.1:${port}`;
    browser = await withTimeout(
      chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}),
      35000, 'chromium.launch()');
    console.log(`SETUP done - server ready on port ${port}, data root ${dataRoot}`);

    /* ------------------------------------------------------------- Graph */
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(20000);
    await installRoutes(page);
    await page.goto(origin + '/?industry=Banking#graph', {waitUntil: 'domcontentloaded'});
    await until(page, () => { const node = document.getElementById('graph-results'); return !!node && !node.hidden; },
      'the Graph results appearing', () => ({status: (document.getElementById('graph-status') || {}).textContent}));

    step('the global "Rows per chart" input is gone and every Overview panel has its own Top N');
    const topState = await page.evaluate(visibleSource => {
      const visible = eval(visibleSource);
      const panels = [...document.querySelectorAll('.graph-panels > section')].filter(s => !s.hidden);
      return {
        rowsPerChart: !!document.getElementById('graph-row-count'),
        showAllRows: !!document.getElementById('graph-all-rows'),
        panels: panels.map(s => s.id),
        withTop: panels.filter(s => visible(s.querySelector('select[data-top]'))).map(s => s.id),
        options: [...document.querySelectorAll('#graph-top-categories option')].map(o => o.value),
        scopeSentenceVisible: visible(document.getElementById('graph-scope'))
      };
    }, VISIBLE_SOURCE);
    assert.equal(topState.rowsPerChart, false, 'the global Rows per chart input must be gone');
    assert.equal(topState.showAllRows, false, 'the global "Show all rows" button must be gone');
    assert.deepEqual(topState.panels, ['graph-category-panel', 'graph-candidate-panel',
      'graph-company-panel', 'graph-level-panel'], 'the Overview is the four analyses');
    assert.deepEqual(topState.withTop, topState.panels, 'every Overview panel carries its own Top N');
    // Build 02 (2026-09-22, prompt item 8 / ruling R14): a comparison's Top N
    // default is 6, so every Top N select offers 6 as well; 10 stays the default.
    assert.deepEqual(topState.options, ['5', '6', '10', '25', '50'], 'Top N offers 5, 6, 10, 25 and 50');
    assert.equal(topState.scopeSentenceVisible, false, 'the scope sentence is no longer printed on screen');
    pass(`Overview panels ${JSON.stringify(topState.panels)}, each with a Top N of ${JSON.stringify(topState.options)}`);

    step('the KPI strip is three equal tiles and the two part figures are shares of net giving');
    const kpi = await page.evaluate(() => {
      const tiles = [...document.querySelectorAll('.graph-metrics > div')];
      return {
        count: tiles.length,
        widths: tiles.map(t => Math.round(t.getBoundingClientRect().width)),
        total: (document.getElementById('graph-total') || {}).textContent,
        direct: (document.getElementById('graph-direct-share') || {}).textContent,
        organizations: (document.getElementById('graph-organization-share') || {}).textContent,
        bars: [...document.querySelectorAll('.graph-share-track')].map(node => ({
          hidden: node.hidden, width: node.firstElementChild.style.width}))
      };
    });
    assert.equal(kpi.count, 3, 'three KPI tiles');
    assert.ok(Math.max(...kpi.widths) - Math.min(...kpi.widths) <= 2,
      `the three tiles are equal, got ${JSON.stringify(kpi.widths)}`);
    assert.match(kpi.direct, /% of net giving$/, 'direct to candidates states its share of net giving');
    assert.match(kpi.organizations, /% of net giving$/, 'recipient organizations states its share of net giving');
    assert.equal(kpi.bars.length, 2, 'only the two part figures carry a share bar');
    for (const bar of kpi.bars) {
      assert.equal(bar.hidden, false, 'the share bar is drawn for a share that is a real fraction');
      assert.match(bar.width, /^\d+(\.\d+)?%$/, `the bar width is a percentage, got ${bar.width}`);
    }
    pass(`net ${kpi.total}, direct ${kpi.direct}, organizations ${kpi.organizations}`);

    step('every bar carries its value and its share, and the shares add up to the panel footer');
    const bars = await page.evaluate(() => {
      const read = id => {
        const host = document.getElementById(id);
        const groups = [...host.querySelectorAll('.graph-bar-button')];
        return {
          marks: groups.length,
          values: groups.map(g => (g.querySelector('.graph-value') || {}).textContent || '').filter(Boolean).length,
          shares: groups.map(g => (g.querySelector('.graph-share') || {}).textContent || ''),
          legend: [...host.querySelectorAll('.graph-legend-item')].map(n => n.textContent),
          titles: groups.map(g => (g.querySelector('title') || {}).textContent || '')
        };
      };
      const foot = id => (document.getElementById('graph-foot-' + id) || {}).textContent || '';
      return {categories: read('graph-categories'), candidates: read('graph-candidates'),
        companies: read('graph-companies'), levels: read('graph-levels'),
        feet: {categories: foot('categories'), candidates: foot('candidates'),
          companies: foot('companies'), levels: foot('levels')}};
    });
    for (const name of ['categories', 'candidates', 'companies']) {
      const panel = bars[name];
      assert.ok(panel.marks > 0, `${name} drew bars`);
      assert.equal(panel.values, panel.marks, `${name}: every bar carries its value`);
      assert.equal(panel.shares.length, panel.marks, `${name}: every bar carries a share label`);
      for (const share of panel.shares) {
        assert.match(share, /^(\d+(\.\d+)?%|—)$/, `${name}: a share label reads as a percentage, got ${JSON.stringify(share)}`);
      }
      for (const title of panel.titles) {
        assert.match(title, /% of /, `${name}: the hover detail names what the share is a share of`);
      }
    }
    // The employee-level panel is one stacked bar; its per-segment value and
    // share are in the legend beside it (design item 11).
    assert.equal(bars.levels.legend.length, bars.levels.marks, 'employee level: one legend entry per segment');
    for (const entry of bars.levels.legend) {
      assert.match(entry, /\$/, 'a level legend entry carries its value');
      assert.match(entry, /%/, 'a level legend entry carries its share');
    }
    const shown = bars.categories.shares.map(text => Number(text.replace('%', ''))).filter(n => !Number.isNaN(n));
    const summed = shown.reduce((total, value) => total + value, 0);
    const coverage = Number((bars.feet.categories.match(/([\d.]+)% of/) || [])[1]);
    assert.ok(!Number.isNaN(coverage), `the recipient-type footer states a coverage figure, got ${JSON.stringify(bars.feet.categories)}`);
    assert.ok(Math.abs(summed - coverage) <= 0.5,
      `the recipient-type shares sum to ${summed.toFixed(1)}%, footer says ${coverage}%`);
    pass(`recipient types ${bars.categories.marks} bars summing to ${summed.toFixed(1)}%, footer "${bars.feet.categories}"`);

    step('Top N slices the payload already in hand: no new request, nothing in the URL');
    const before = await page.evaluate(() => location.search);
    await page.selectOption('#graph-top-categories', '5');
    await until(page, () => document.querySelectorAll('#graph-categories .graph-bar-button').length === 5,
      'the recipient-type panel slicing to five rows',
      () => ({marks: document.querySelectorAll('#graph-categories .graph-bar-button').length}));
    const others = await page.evaluate(() => ({
      candidates: document.querySelectorAll('#graph-candidates .graph-bar-button').length,
      search: location.search
    }));
    assert.equal(others.candidates, 6, 'one panel’s Top N does not touch another panel');
    assert.equal(others.search, before, 'Top N is not written to the URL');
    await page.selectOption('#graph-top-categories', '10');
    pass(`Top N 5 sliced one panel, the URL stayed ${before}`);

    step('party dots and company sparklines draw from the payload');
    await until(page, () => document.querySelectorAll('#graph-companies polyline.graph-spark-line').length > 0,
      'the company sparklines filling in after the panel painted',
      () => ({marks: document.querySelectorAll('#graph-companies .graph-bar-button').length,
        foot: (document.getElementById('graph-foot-companies') || {}).textContent}));
    const depth = await page.evaluate(() => ({
      dots: document.querySelectorAll('#graph-candidates circle.graph-party').length,
      dem: document.querySelectorAll('#graph-candidates circle.graph-party.is-dem').length,
      rep: document.querySelectorAll('#graph-candidates circle.graph-party.is-rep').length,
      other: document.querySelectorAll('#graph-candidates circle.graph-party.is-other').length,
      sparks: document.querySelectorAll('#graph-companies polyline.graph-spark-line').length,
      firstCompanySegments: document.querySelectorAll('#graph-companies [data-spark="companies-0"] polyline.graph-spark-line').length,
      companyMarks: document.querySelectorAll('#graph-companies .graph-bar-button').length,
      foot: (document.getElementById('graph-foot-companies') || {}).textContent,
      footTitle: (document.getElementById('graph-foot-companies') || {}).title,
      footLines: (() => { const foot = document.getElementById('graph-foot-companies'); if (!foot) return 0;
        const range = document.createRange(); range.selectNodeContents(foot);
        return new Set([...range.getClientRects()].map(rect => Math.round(rect.top))).size; })(),
      info: (document.querySelector('#graph-company-panel .graph-panel-foot .graph-info') || {getAttribute: () => ''}).getAttribute('aria-label'),
      hover: [...document.querySelectorAll('#graph-companies .graph-bar-button title')].map(n => n.textContent)[0]
    }));
    assert.equal(depth.dots, 6, 'a party dot on every candidate row the fixture gives a party');
    assert.equal(depth.dem, 3, 'three Democratic dots');
    assert.equal(depth.rep, 2, 'two Republican dots');
    assert.equal(depth.other, 1, 'a party this console cannot read as one side gets the neutral dot');
    assert.ok(depth.sparks>=depth.companyMarks, 'a sparkline on every company row');
    assert.equal(depth.firstCompanySegments,2,'a missing quarter splits the line instead of being joined or treated as zero');
    assert.equal(quarterlyQueries,1,'all company sparklines share one quarterly contribution query');
    assert.equal(graphRequests,1,'sparklines do not fan out extra Graph requests by cycle');
    // Board M4: the footer names the quarterly range on one line; the
    // missing-data rule is the (i) note beside it and the footer's hover
    // (r5 men-graph: spelled out on the line it wrapped the footer).
    assert.match(depth.foot, /sparkline 2025-Q1–2026-Q4$/, 'the footer names the quarterly range');
    assert.equal(depth.footLines, 1, 'the footer is one line, as on board M4');
    assert.match(depth.info, /missing quarters remain gaps, not zeroes/, 'the (i) beside the footer names the missing-data rule');
    assert.match(depth.footTitle, /^Sparkline: observed quarters 2025-Q1–2026-Q4; a missing quarter is a gap, not zero\.$/, 'and so does the footer\'s hover');
    assert.match(depth.hover, /2025-Q1 \$/, 'the company hover detail carries the per-quarter values');
    pass(`${depth.dots} party dots (${depth.dem}D/${depth.rep}R/${depth.other} other), ${depth.sparks} sparklines, footer "${depth.foot}"`);

    /* ------------------------------------------------------ Chart Studio */
    step('#studio-title is empty or hidden before a chart is drawn, and names the chart after');
    holdFirstQuery = true;
    const studio = await browser.newPage({viewport: {width: 1440, height: 900}});
    studio.setDefaultTimeout(20000);
    const studioErrors = [];
    studio.on('pageerror', error => studioErrors.push(error.message));
    await installRoutes(studio);
    await studio.goto(origin + '/?industry=Banking#chart-studio', {waitUntil: 'domcontentloaded'});
    await until(studio, () => !!document.getElementById('studio-title'), 'the studio markup arriving');
    const emptyTitle = await studio.evaluate(visibleSource => {
      const visible = eval(visibleSource);
      const node = document.getElementById('studio-title');
      return {text: node.textContent.trim(), visible: visible(node)};
    }, VISIBLE_SOURCE);
    assert.ok(emptyTitle.text === '' || emptyTitle.visible === false,
      `before a chart is drawn the title is empty or hidden, got ${JSON.stringify(emptyTitle)}`);
    assert.notEqual(emptyTitle.text, 'Chart Studio', 'the canvas title never repeats the console name');
    releaseQuery();
    await until(studio, () => !!document.querySelector('#studio-canvas svg'), 'the first chart drawing',
      () => ({title: (document.getElementById('studio-title') || {}).textContent,
        note: (document.getElementById('studio-note') || {}).textContent}));
    const drawn = await studio.evaluate(() => ({
      title: (document.getElementById('studio-title') || {}).textContent,
      subtitle: (document.getElementById('studio-subtitle') || {}).textContent
    }));
    assert.ok(drawn.title.trim().length > 0, 'once a chart is drawn the title names it');
    assert.notEqual(drawn.title.trim(), 'Chart Studio', 'and it still never reads "Chart Studio"');
    assert.match(drawn.subtitle, /Banking/, 'the scope subtitle names the scope under the title');
    pass(`title "${drawn.title}", subtitle "${drawn.subtitle}"`);

    step('the chart starts above the fold at 1440 by 900, and the Fit select is at its intrinsic width');
    const layout = await studio.evaluate(() => {
      const canvas = document.getElementById('studio-canvas').getBoundingClientRect();
      const fit = document.getElementById('studio-fit').getBoundingClientRect();
      const columns = getComputedStyle(document.getElementById('studio-grid')).gridTemplateColumns;
      return {canvasTop: Math.round(canvas.top), fitWidth: Math.round(fit.width), columns};
    });
    assert.ok(layout.canvasTop < 900, `the top of #studio-canvas is above the fold, got ${layout.canvasTop}px`);
    assert.ok(layout.fitWidth <= 160, `the Fit select is at most 160px wide, got ${layout.fitWidth}px`);
    pass(`#studio-canvas top ${layout.canvasTop}px, Fit select ${layout.fitWidth}px, grid ${layout.columns}`);

    step('one "Add to briefing", one "Company overview", and Views carries Save and Open view');
    const chrome = await studio.evaluate(visibleSource => {
      const visible = eval(visibleSource);
      const named = text => [...document.querySelectorAll('#view-chart-studio button')]
        .filter(node => node.textContent.trim().startsWith(text) && visible(node)).map(node => node.id || node.className);
      return {
        briefing: named('Add to briefing'),
        overview: named('Company overview'),
        menuBriefing: !!document.getElementById('studio-briefing-menu-add'),
        views: [...document.querySelectorAll('#studio-views button')].map(node => node.id),
        classic: (document.getElementById('studio-classic') || {}).parentElement.parentElement.id,
        segments: [...document.querySelectorAll('#studio-view-mode [role=radio]')].map(node => node.id),
        role: document.getElementById('studio-view-mode').getAttribute('role')
      };
    }, VISIBLE_SOURCE);
    assert.equal(chrome.briefing.length, 1, `exactly one visible "Add to briefing", got ${JSON.stringify(chrome.briefing)}`);
    assert.equal(chrome.overview.length, 1, `exactly one visible "Company overview", got ${JSON.stringify(chrome.overview)}`);
    assert.equal(chrome.menuBriefing, false, 'the duplicate briefing entry in the Export menu is gone');
    // The board build (design section 4, option C) adds the third entry: one
    // briefing card per filled tile, from the same menu Save view lives in.
    assert.deepEqual(chrome.views, ['studio-save-view', 'studio-open-views', 'studio-board-briefing'],
      'the Views menu holds Save view, Open view and Add board to briefing');
    assert.equal(chrome.classic, 'studio-download', 'Classic builder moved under Export');
    assert.equal(chrome.role, 'radiogroup', 'the three view modes are one radiogroup');
    assert.deepEqual(chrome.segments, ['studio-view-builder', 'studio-overview-toggle', 'studio-view-canvas'],
      'Builder, Company overview and Canvas are its three options');
    pass(`Add to briefing ${JSON.stringify(chrome.briefing)}, view modes ${JSON.stringify(chrome.segments)}`);

    step('the Inspector column is the single scroll container on the Marks pane');
    // Measured with every disclosure open, so the pane really overflows: a
    // pane that happens to fit would pass this by not scrolling at all.
    await studio.evaluate(() => {
      for (const id of ['studio-showme-details', 'studio-pages-details', 'studio-params-details'])
        document.getElementById(id).open = true;
    });
    const scrolls = await studio.evaluate(() => {
      const inspector = document.getElementById('studio-inspector');
      const scrollers = [inspector, ...inspector.querySelectorAll('*')].filter(node => {
        if (node.closest('[hidden]')) return false;
        const style = getComputedStyle(node);
        if (!['auto', 'scroll', 'overlay'].includes(style.overflowY)) return false;
        return node.clientHeight > 0 && node.scrollHeight > node.clientHeight + 1;
      });
      const nested = scrollers.filter(node => scrollers.some(other => other !== node && other.contains(node)));
      return {
        scrollers: scrollers.map(node => node.id || node.tagName + '.' + node.className),
        nested: nested.map(node => node.id || node.tagName + '.' + node.className),
        marksTab: document.getElementById('studio-tab-marks').getAttribute('aria-selected'),
        overflowing: inspector.scrollHeight > inspector.clientHeight + 1
      };
    });
    assert.equal(scrolls.marksTab, 'true', 'the Marks pane is the one in front');
    assert.deepEqual(scrolls.nested, [],
      `no scrollbar inside another scrollbar, got ${JSON.stringify(scrolls.scrollers)}`);
    assert.ok(scrolls.scrollers.length <= 1,
      `at most one scroll container in the Inspector, got ${JSON.stringify(scrolls.scrollers)}`);
    assert.equal(scrolls.overflowing, true,
      'with every disclosure open the Inspector content is taller than its column, so the check has something to measure');
    assert.deepEqual(scrolls.scrollers, ['studio-inspector'],
      `the column itself is the one that scrolls, got ${JSON.stringify(scrolls.scrollers)}`);
    pass(`Inspector scroll containers: ${JSON.stringify(scrolls.scrollers)}`);

    // Step 9 opened every disclosure to make the pane overflow; the reader's
    // own starting state is folded, so it is put back before the next step.
    await studio.evaluate(() => {
      for (const id of ['studio-showme-details', 'studio-pages-details', 'studio-params-details'])
        document.getElementById(id).open = false;
    });
    step('Show Me is one block in the DOM, opened in the Inspector by the toolbar button');
    const closedShowMe = await studio.evaluate(visibleSource => {
      const visible = eval(visibleSource);
      return {
        blocks: document.querySelectorAll('#studio-showme').length,
        grids: document.querySelectorAll('#studio-showme-grid').length,
        open: document.getElementById('studio-showme-details').open,
        visible: visible(document.getElementById('studio-showme-grid')),
        pressed: document.getElementById('studio-showme-toggle').getAttribute('aria-pressed'),
        controls: document.getElementById('studio-showme-toggle').getAttribute('aria-controls'),
        hint: (document.getElementById('studio-showme-hint') || {}).textContent
      };
    }, VISIBLE_SOURCE);
    assert.equal(closedShowMe.blocks, 1, 'Show Me appears once in the DOM');
    assert.equal(closedShowMe.grids, 1, 'and so does its tile grid');
    assert.equal(closedShowMe.open, false, 'it sits folded inside the Inspector, not as a second copy on screen');
    assert.equal(closedShowMe.visible, false, 'so its tiles are not on screen');
    assert.equal(closedShowMe.pressed, 'false', 'the toolbar toggle says so');
    assert.equal(closedShowMe.controls, 'studio-showme', 'and still points at the block it opens');
    await studio.click('#studio-showme-toggle');
    const openShowMe = await studio.evaluate(visibleSource => {
      const visible = eval(visibleSource);
      const grid = document.getElementById('studio-showme-grid');
      return {
        blocks: document.querySelectorAll('#studio-showme').length,
        open: document.getElementById('studio-showme-details').open,
        visible: visible(grid),
        tiles: grid.querySelectorAll('button').length,
        pressed: document.getElementById('studio-showme-toggle').getAttribute('aria-pressed'),
        inspectorHidden: document.getElementById('studio-inspector').hidden,
        marksTab: document.getElementById('studio-tab-marks').getAttribute('aria-selected')
      };
    }, VISIBLE_SOURCE);
    assert.equal(openShowMe.blocks, 1, 'opening it does not make a second copy');
    assert.equal(openShowMe.open, true, 'the Inspector disclosure is open');
    assert.equal(openShowMe.visible, true, 'and the tiles are on screen');
    assert.ok(openShowMe.tiles > 0, 'with tiles in them');
    assert.equal(openShowMe.pressed, 'true', 'the toolbar toggle says the block is showing');
    assert.equal(openShowMe.inspectorHidden, false, 'the Inspector is up');
    assert.equal(openShowMe.marksTab, 'true', 'on the Marks pane, where the block lives');
    await studio.click('#studio-showme-toggle');
    const reclosed = await studio.evaluate(() => ({
      blocks: document.querySelectorAll('#studio-showme').length,
      open: document.getElementById('studio-showme-details').open,
      pressed: document.getElementById('studio-showme-toggle').getAttribute('aria-pressed')
    }));
    assert.equal(reclosed.blocks, 1, 'and closing it leaves exactly one block behind');
    assert.equal(reclosed.open, false, 'folded again');
    assert.equal(reclosed.pressed, 'false', 'and the toggle agrees');
    pass(`Show Me: ${openShowMe.tiles} tiles, one block, hint "${closedShowMe.hint}"`);

    step('the Data source disclosure stays open and reads as a chip line with a ready mark');
    // Build 03 (S1): the disclosure lives in the source chip's popover.
    await studio.click('#studio-scope-change');
    const source = await studio.evaluate(visibleSource => {
      const visible = eval(visibleSource);
      return {
        open: document.getElementById('studio-source-details').open,
        datasetVisible: visible(document.getElementById('studio-dataset')),
        basisVisible: visible(document.getElementById('studio-basis')),
        chips: [...document.querySelectorAll('#studio-source-summary span')].map(node => node.textContent),
        ready: !!document.querySelector('#studio-source-summary span.is-ready'),
        scopeChips: [...document.querySelectorAll('#studio-scope-chip span')].map(node => node.textContent)
      };
    }, VISIBLE_SOURCE);
    assert.equal(source.open, true, 'the Data source disclosure stays open by default');
    assert.equal(source.datasetVisible, true, 'its Report select stays where a test can select it');
    assert.equal(source.basisVisible, true, 'and so does its Accounting basis select');
    assert.ok(source.chips.length >= 2, `the summary is a chip line, got ${JSON.stringify(source.chips)}`);
    assert.equal(source.ready, true, 'with a ready mark once a report is chosen');
    assert.ok(source.scopeChips.length >= 2, `the header scope is chips, got ${JSON.stringify(source.scopeChips)}`);
    pass(`source chips ${JSON.stringify(source.chips)}, header chips ${JSON.stringify(source.scopeChips)}`);

    step('page errors raised by the views this pass owns');
    // No exception list: the PAC evidence lane retired activateSavedEvidence, so any
    // page error raised while Graph or Chart Studio is on screen is this lane to answer for.
    assert.deepEqual(studioErrors, [], `Chart Studio raised ${JSON.stringify(studioErrors)}`);
    pass('no page error from Graph or Chart Studio');
  } catch (error) {
    failed = true;
    console.error('FAIL: ' + (error && error.stack ? error.stack : error));
  } finally {
    if (releaseQuery) releaseQuery();
    if (browser) await browser.close().catch(() => {});
    await stopServer(server);
    await cleanupPath(dataRoot);
    await cleanupPath(reportRoot);
  }
  if (failed) { process.exitCode = 1; console.log(`RESULT: ${passes}/${stepNumber} steps passed, exit 1`); }
  else console.log(`RESULT: ${passes}/${stepNumber} steps passed, exit 0`);
})();
