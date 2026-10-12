/* S5 (Area D), real-client pass: the hash names the tab, the query string
 * carries the scope.
 *
 * What this proves that a unit test cannot: a permalink opens Graph on the
 * right industry, company, cycle and candidate view; switching tabs, reloading
 * and clicking through to Chart Studio all keep that scope; Cross-reference
 * asks the server for the industry the URL names; and a saved-view permalink
 * opens its view on a second page. Contract:
 * audits/mentat_failings_20260915/contracts/url_scope.json.
 *
 * Spawn pattern and stubbing: tests/test_studio_industry_browser.cjs (real
 * `python app/server.py` on a free port with a throwaway --data-root, the two
 * contribution datasets stubbed because no real snapshot exists in a throwaway
 * root) plus the graph fixture shape from tests/test_graph_lazy_tables_browser.cjs.
 * /api/studio/views is deliberately NOT stubbed: saved views really reach disk.
 *
 * Run: node tests/test_navigation_scope_browser.cjs   (cwd: mentat/)
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
const loadContract = name => JSON.parse(fs.readFileSync(path.join(CONTRACTS, name + '.json'), 'utf8'));
const urlScope = loadContract('url_scope');
const snapshotRegistry = loadContract('snapshot_registry');

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
        settled = true;
        clearTimeout(timer);
        resolve(child);
      }
    });
    child.stderr.on('data', chunk => { stderrLines.push(chunk.toString()); trace(`server ${port} stderr: ${chunk.toString().trim()}`); });
    child.on('exit', code => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`server on port ${port} exited ${code} before reporting ready; stderr: ${stderrLines.join('')}`));
    });
    child.on('error', error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
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
// Two contribution datasets, the shape contribution_data.py:catalog() sends.
const COMMON_FIELDS = [
  {id: 'company', label: 'Company', name: 'Company', role: 'dimension', type: 'category'},
  {id: 'cycle', label: 'Election cycle', name: 'Election cycle', role: 'dimension', type: 'category'},
  {id: 'amount', label: 'Selected amount', name: 'Selected amount', role: 'measure', type: 'number', unit: 'cents'},
  {id: 'records', label: 'Observation count', name: 'Observation count', role: 'measure', type: 'number', unit: 'count'},
  {id: 'contributors', label: 'Distinct reported donor identities', name: 'Distinct reported donor identities', role: 'measure', type: 'number', unit: 'count'}
];
const BASES = ['receipt', 'original_observed', 'original_supported', 'raw', 'pac_activity']
  .map(id => ({id, label: id, status: id === 'original_observed' ? 'provisional' : id === 'original_supported' ? 'supported_subset' : 'observed'}));

function datasetEntry(industry, sha) {
  return {
    id: industry, industry, label: industry, available: true, message: null,
    fields: COMMON_FIELDS, bases: BASES, default_basis: 'receipt',
    provenance: {
      workbook: industry + ' Industry Spending Overview.xlsx', workbook_sha256: sha,
      data_workbook: null, data_workbook_sha256: null, data_lineage_matches: null,
      destination_schema: 1, database_sha256: sha, payload_sha256: sha,
      original_database_sha256: sha, accounting_engine_sha256: 'e'.repeat(64),
      cycle_basis: 'Contribution date, calendar election cycle',
      scope: 'S5 browser-test fixture. Not real collection data.'
    }
  };
}
const BANKING = datasetEntry('Banking', 'a'.repeat(64));
const CREDIT_UNION = datasetEntry('Credit Union', 'b'.repeat(64));
// The build stamps come from the registry contract itself, never from a copy.
const REGISTRY_ENTRIES = snapshotRegistry.examples[0].snapshots;

const GRAPH_COMPANIES = {Banking: 'Fixture Bank', 'Credit Union': 'Fixture Credit Union'};
const GRAPH_CYCLES = ['2022', '2024'];
const GRAPH_CATALOG = {
  industries: [{name: 'Banking', indices: [0]}, {name: 'Credit Union', indices: [1]}],
  unavailable_reports: [], report_directory: 'fixture', message: ''
};
const rows = name => [0, 1, 2].map(i => ({id: `${name}-${i}`, label: `${name} label ${i}`, amount: 1000 - i}));

// A cross-reference run this stub can serve, and the empty envelope the real
// route answers with when the named industry has no saved run at all. The
// status line has to name which of the two it is looking at.
const XREF_REPORT = {
  generated_at: '2026-09-17T00:00:00Z',
  scope: {industries: ['Banking'], companies: ['Fixture Bank'], cycles: [2024], source_count: 1},
  summary: {input_records: 5, automatic_decisions: 2, unresolved_records: 1, unresolved_amount: 40,
    observed_original_giving: 100, employee_giving: 100, corporate_giving: 0, unverified_funding: 0,
    pac_outgoing: 60, baseline_amount: 160, amount_change: -60, verified_unique: false},
  steps: [{id: 'collect', status: 'completed'}],
  unresolved_reasons: [], review_queue: [], changes: [], limitations: []
};

// The real route echoes the filters it was asked for; render() puts the
// company and cycle back on the selects from that echo, so the stub has to
// echo them too or the permalink could never land on a control.
//
// It also mirrors ONE server rule exactly, because a permalink depends on it:
// graph_data.py:317-318 replaces the requested cycle with max(years) whenever
// latest=1 is present. A stub that ignored that would let a client send both a
// cycle and latest=1 and still look correct here while the real console showed
// a different cycle from the one in the URL.
function graphView(query) {
  const industry = query.get('industry') || 'Banking';
  const cycle = query.get('latest') === '1' ? GRAPH_CYCLES[GRAPH_CYCLES.length - 1] : (query.get('cycle') || '');
  return {
    options: {companies: [GRAPH_COMPANIES[industry] || 'Fixture Bank'], cycles: GRAPH_CYCLES,
      unknown_tiers: false, recipients: []},
    filters: {company: query.get('company') || '', cycle,
      tier: query.get('tier') || '', mode: query.get('mode') || 'direct',
      direction: query.get('direction') || 'Support'},
    totals: {receipt_groups: 12, giving: 1234.5, direct: 234.5, organizations: 1000,
      unresolved_candidate_links: 0},
    candidate_available: true, candidate_note: 'Synthetic candidates, no real donor records.',
    note: 'Offline fixture only.',
    reports: [{name: industry + ' fixture workbook.xlsx', sha256: 'c'.repeat(64)}],
    candidate_details: [],
    categories: rows('categories'), organizations: rows('organizations'),
    candidates: rows('candidates'), companies: rows('companies'), levels: rows('levels')
  };
}

let stepNumber = 0;
function step(label) { stepNumber += 1; console.log(`STEP ${stepNumber}: ${label}`); }
function pass(detail) { console.log(`PASS step ${stepNumber} - ${detail}`); }
// Launching the console is not one of the ten steps the brief names, so it
// prints under its own heading and does not shift their numbers.
function setup(label) { console.log(`SETUP: ${label}`); }

(async () => {
  const playwright = resolvePlaywright();
  if (playwright.error) {
    console.error('FAIL: playwright could not be resolved at ' + playwright.modules
      + ' (' + playwright.error.message + '). This suite is a page test; there is no node-driven substitute.');
    process.exitCode = 1;
    return;
  }
  const {chromium} = playwright;

  let browser = null;
  let server = null;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-navigation-scope-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-navigation-scope-reports-'));
  let failed = false;
  const crossReferenceRequests = [];
  const graphRequests = [];
  const viewTraffic = [];
  let crossReferenceAvailable = true;

  // Every page in this run gets the same stubs. One dispatcher, matched on the
  // pathname with a predicate rather than a glob, so registration order can
  // never decide whether /api/graph-catalog is answered by the /api/graph stub.
  async function installRoutes(page) {
    await page.route(target => target.pathname.startsWith('/api/'), route => {
      const target = new URL(route.request().url());
      const where = target.pathname;
      if (where === '/api/contributions/session') {
        return route.fulfill({json: {token: 'navigation-scope-token', read_only: false,
          industry: 'Banking', industries: REGISTRY_ENTRIES}});
      }
      if (where === '/api/contributions/catalog') {
        return route.fulfill({json: {datasets: [BANKING, CREDIT_UNION], fields: COMMON_FIELDS, amount_unit: 'cents'}});
      }
      if (where === '/api/contributions/query') {
        const body = route.request().postDataJSON() || {};
        const entry = body.dataset === 'Credit Union' ? CREDIT_UNION : BANKING;
        const dims = body.dimensions && body.dimensions.length ? body.dimensions : ['company'];
        const made = [Object.assign({}, ...dims.map(d => ({[d]: d === 'cycle' ? '2023-2024' : entry.industry + ' Donor'})),
          {amount: 123400, records: 3, contributors: 3})];
        return route.fulfill({json: {status: 'ready', rows: made,
          columns: [...dims, ...(body.measures || ['amount'])].map(id => COMMON_FIELDS.find(f => f.id === id) || {id, label: id}),
          totals: {amount: 123400, records: 3, contributors: 3}, group_count: made.length,
          offset: 0, has_more: false, provenance: entry.provenance,
          basis_label: 'Published included receipts',
          reconciliation: {conserved: true, receipt_matches_workbook: true},
          notices: [], verified_unique: false}});
      }
      if (where === '/api/report-sources') {
        return route.fulfill({json: {runs: [{id: 'published-reports', name: 'Published reports',
          state: 'completed', source_directory: 'fixture'}], report_directory: 'fixture'}});
      }
      if (where === '/api/graph-catalog') return route.fulfill({json: GRAPH_CATALOG});
      if (where === '/api/graph') {
        graphRequests.push(target.pathname + target.search);
        return route.fulfill({json: graphView(target.searchParams)});
      }
      if (where === '/api/cross-reference') {
        crossReferenceRequests.push(target.pathname + target.search);
        const industry = target.searchParams.get('industry');
        if (!crossReferenceAvailable) {
          return route.fulfill({json: {available: false, status: 'empty', industry, message: 'stub'}});
        }
        return route.fulfill({json: {available: true, status: 'completed', run_id: 'nav-scope-stub',
          industry, selected_by: industry ? 'industry' : 'pointer', report: XREF_REPORT}});
      }
      return route.continue();
    });
    page.on('response', response => {
      const where = new URL(response.url()).pathname;
      if (where === '/api/studio/views') viewTraffic.push(response.request().method() + ' ' + response.status());
    });
  }

  // Playwright's waitForFunction polls on animation frames by default, and
  // Chromium throttles those in a background tab - this run keeps two pages
  // open at once. So every wait here polls on a timer, and a wait that never
  // comes true reports the page's own state rather than a bare timeout.
  async function until(target, predicate, label, diagnose, attempts = 80) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await target.evaluate(predicate)) return;
      await target.waitForTimeout(250);
    }
    const seen = diagnose ? await target.evaluate(diagnose) : null;
    throw new Error(label + ' never happened' + (seen ? '. Page: ' + JSON.stringify(seen) : '.'));
  }
  const studioState = () => ({
    search: location.search,
    error: (document.getElementById('studio-error') || {}).textContent || '',
    loading: (document.getElementById('studio-loading') || {}).textContent || '',
    status: (document.getElementById('studio-status') || {}).textContent || '',
    dataset: (document.getElementById('studio-dataset') || {}).value || '',
    datasetOptions: [...document.querySelectorAll('#studio-dataset option')].map(o => o.value),
    nameInputPresent: !!document.querySelector('input.studio-view-name'),
    exportMenuOpen: !!(document.getElementById('studio-download') || {}).open,
    scopeModule: typeof globalThis.MentatScope
  });

  const graphSelects = page => page.evaluate(() => ({
    industry: document.getElementById('graph-industry').value,
    company: document.getElementById('graph-company').value,
    cycle: document.getElementById('graph-cycle').value,
    mode: document.getElementById('graph-mode').value
  }));

  try {
    setup('pick a free port and launch the console server on a throwaway --data-root');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    const origin = `http://127.0.0.1:${port}`;
    // The console reads this suite's own report directory, not the configured one.
    const reportsSeen = await new Promise((resolve, reject) => {
      const ask = require('node:http').get(
        {host: '127.0.0.1', port: port, path: '/api/report-sources', headers: {Host: '127.0.0.1:' + port}},
        answer => {
          let body = '';
          answer.on('data', chunk => { body += chunk; });
          answer.on('end', () => { try { resolve(JSON.parse(body)); } catch (error) { reject(error); } });
        });
      ask.on('error', reject);
    });
    assert.equal(path.resolve(String(reportsSeen.report_directory || '')).toLowerCase(),
      path.resolve(reportRoot).toLowerCase(),
      `the console must read this suite's own report directory, got "${reportsSeen.report_directory}"`);

    browser = await withTimeout(
      chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}),
      35000, 'chromium.launch()');
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await installRoutes(page);
    console.log(`SETUP done - server ready on port ${port}, data root ${dataRoot}, `
      + `report directory ${reportRoot}, routes stubbed`);

    // The permalink under test is the contract's own Graph example, retargeted
    // at this fixture's company so the example and the test cannot drift.
    const example = urlScope.examples.find(entry => entry.name === 'graph_permalink_downstream_activity');
    const company = GRAPH_COMPANIES.Banking;
    const permalink = '/?industry=Banking&company=' + encodeURIComponent(company)
      + '&cycle=2024&mode=activity#graph';

    step('open the Graph permalink and read the four controls back');
    await page.goto(origin + permalink);
    await page.waitForSelector('#view-graph:not([hidden])');
    await page.waitForSelector('#graph-results:not([hidden])');
    assert.equal(await page.evaluate(() => location.hash), '#graph');
    assert.deepEqual(await graphSelects(page),
      {industry: 'Banking', company, cycle: '2024', mode: 'activity'});
    // and the scope the contract names is exactly what scope.js read here
    assert.deepEqual(await page.evaluate(() => MentatScope.read().mode), example.scope.mode);
    pass(`Graph opened on the permalink: ${JSON.stringify(await graphSelects(page))}`);

    step('switch to Chart Studio: the dataset follows the URL and company/cycle arrive as pills');
    await page.click('#tab-chart-studio');
    await page.waitForSelector('#view-chart-studio:not([hidden])');
    await until(page, () => document.querySelectorAll('#studio-dataset option').length >= 2,
      'the dataset picker listing both reports', studioState);
    await page.waitForSelector('#studio-canvas svg');
    assert.equal(await page.locator('#studio-dataset').inputValue(), 'Banking');
    await until(page, () => window.MentatStudioPane && (window.MentatStudioPane.filters() || []).length >= 2,
      'the carried company and cycle arriving as filter pills', studioState);
    const pills = await page.evaluate(() => window.MentatStudioPane.filters().map(f => [f.field, f.value]));
    assert.deepEqual(pills.find(p => p[0] === 'company'), ['company', company]);
    // Graph spells a cycle by its end year, the verified index by the pair.
    assert.deepEqual(pills.find(p => p[0] === 'cycle'), ['cycle', '2023-2024']);
    const pillText = await page.locator('#studio-filters .studio-pill').allTextContents();
    assert.ok(pillText.length >= 2, `expected two filter pills on the shelf, got ${JSON.stringify(pillText)}`);
    pass(`dataset=Banking, carried pills ${JSON.stringify(pills)}, shelf shows ${JSON.stringify(pillText)}`);

    step('back to Graph: the selects still equal the URL');
    await page.click('#tab-graph');
    await page.waitForSelector('#view-graph:not([hidden])');
    assert.deepEqual(await graphSelects(page), {industry: 'Banking', company, cycle: '2024', mode: 'activity'});
    assert.equal(await page.evaluate(() => location.search),
      '?industry=Banking&company=' + encodeURIComponent(company) + '&cycle=2024&mode=activity');
    pass('Graph selections survived the round trip through Chart Studio');

    step('reload the page: the scope comes back from the URL alone');
    await page.reload();
    await page.waitForSelector('#view-graph:not([hidden])');
    await page.waitForSelector('#graph-results:not([hidden])');
    assert.deepEqual(await graphSelects(page), {industry: 'Banking', company, cycle: '2024', mode: 'activity'});
    pass('reload restored industry, company, cycle and candidate view');

    step('Cross-reference asks the server for the industry the URL names');
    crossReferenceRequests.length = 0;
    await page.click('#tab-cross-reference');
    await page.waitForSelector('#view-cross-reference:not([hidden])');
    await until(page, () => ((document.getElementById('xref-refresh-status') || {}).textContent || '').includes('Newest run for'),
      'the cross-reference status naming the industry-selected run',
      () => ({status: (document.getElementById('xref-refresh-status') || {}).textContent || ''}));
    assert.ok(crossReferenceRequests.some(url => url === '/api/cross-reference?industry=Banking'),
      `expected an industry-scoped cross-reference request, saw ${JSON.stringify(crossReferenceRequests)}`);
    const xrefStatus = await page.locator('#xref-refresh-status').textContent();
    assert.match(xrefStatus, /^Newest run for Banking · /);
    // and the run that came back is the one on screen, not just in the status line
    assert.match(await page.locator('#xref-results').innerText(), /this view publishes no totals/i);
    pass(`request ${crossReferenceRequests[0]} and status "${xrefStatus}"`);

    step('change the Graph company select: the URL follows the control');
    await page.click('#tab-graph');
    await page.waitForSelector('#view-graph:not([hidden])');
    await page.selectOption('#graph-company', '');
    await until(page, () => !location.search.includes('company='),
      'the company key leaving the URL', () => ({search: location.search}));
    assert.ok(!(await page.evaluate(() => location.search)).includes('company='),
      'clearing the company must remove the key, not write an empty one');
    await page.selectOption('#graph-company', company);
    await until(page, () => location.search.includes('company='),
      'the company key returning to the URL', () => ({search: location.search}));
    assert.equal(await page.evaluate(() => location.search),
      '?industry=Banking&cycle=2024&mode=activity&company=' + encodeURIComponent(company));
    pass('the company key left the URL when cleared and came back when chosen');

    step('"Open in Chart Studio" carries the scope into the URL and switches the tab');
    await page.locator('#graph-more > summary').click();
    await page.click('#graph-open-builder');
    await page.waitForSelector('#view-chart-studio:not([hidden])');
    const afterOpen = await page.evaluate(() => ({search: location.search, hash: location.hash}));
    assert.equal(afterOpen.hash, '#chart-studio');
    for (const [key, value] of [['industry', 'Banking'], ['company', company], ['cycle', '2024']]) {
      assert.equal(new URLSearchParams(afterOpen.search).get(key), value, `${key} in the URL after the handoff`);
    }
    pass(`URL after the handoff: ${afterOpen.search}${afterOpen.hash}`);

    step('save a view: its id lands in the URL, and that permalink opens it on a fresh page');
    // Save view moved into the Views menu (UI clean-up 2026-09-17, item 14).
    if (!(await page.locator('#studio-views').evaluate(el => el.open))) await page.click('#studio-views > summary');
    await page.waitForSelector('#studio-save-view', {state: 'visible'});
    await page.click('#studio-save-view');
    const nameInput = page.locator('input.studio-view-name');
    await nameInput.fill('Scope test');
    await nameInput.press('Enter');
    await until(page, () => /(^|[?&])saved=[a-f0-9]{32}([&]|$)/.test(location.search),
      'the saved view id reaching the URL (saved-view traffic: ' + JSON.stringify(viewTraffic) + ')',
      studioState);
    const savedId = new URLSearchParams(await page.evaluate(() => location.search)).get('saved');
    assert.match(savedId, /^[a-f0-9]{32}$/);
    const second = await browser.newPage({viewport: {width: 1440, height: 1050}});
    second.setDefaultTimeout(20000);
    const secondErrors = [];
    second.on('pageerror', e => secondErrors.push(e.message));
    // chart-studio.js status() writes to #studio-loading, and the verified
    // query's own summary overwrites it a moment later, so every line that
    // element ever holds is recorded rather than sampled.
    await second.addInitScript(() => {
      window.__studioLines = [];
      document.addEventListener('DOMContentLoaded', () => {
        const node = document.getElementById('studio-loading');
        if (!node) return;
        const record = () => window.__studioLines.push(node.textContent || '');
        record();
        new MutationObserver(record).observe(node, {childList: true, characterData: true, subtree: true});
      });
    });
    await installRoutes(second);
    await second.goto(origin + '/?industry=Banking&saved=' + savedId + '#chart-studio');
    await second.waitForSelector('#view-chart-studio:not([hidden])');
    await until(second, () => (window.__studioLines || []).some(line => line.includes('Opened view')),
      'the saved-view permalink opening its view', studioState);
    const openedLine = await second.evaluate(() => (window.__studioLines || []).find(line => line.includes('Opened view')));
    assert.match(openedLine, /Opened view "Scope test"/);
    // The saved sheet carries the two pills the Graph handoff put on it. This
    // page's own URL names no company and no cycle, so nothing but the opened
    // view could have produced them.
    const openedFilters = await second.evaluate(() => window.MentatStudioPane.filters().map(f => [f.field, f.value]));
    assert.deepEqual(openedFilters.find(f => f[0] === 'company'), ['company', company]);
    assert.deepEqual(openedFilters.find(f => f[0] === 'cycle'), ['cycle', '2023-2024']);
    const sheetNames = await second.locator('#studio-sheet-tabs button, .studio-sheet-tab').allTextContents();
    assert.deepEqual(secondErrors, [], `saved-view permalink page errors: ${JSON.stringify(secondErrors)}`);
    // The view really reached disk on the spawned server, not just this tab.
    const onDisk = fs.readFileSync(path.join(dataRoot, 'studio', 'views', savedId + '.json'), 'utf8');
    JSON.parse(onDisk);
    await second.close();
    pass(`saved=${savedId}; the permalink page reported "${openedLine.trim()}" carrying ${JSON.stringify(openedFilters)}; sheet tabs ${JSON.stringify(sheetNames)}`);

    step('negative control: a permalink with mode=pac opens Graph on direct and reports nothing to the console');
    const control = urlScope.negative_controls.find(entry => entry.url.includes('mode=pac'));
    const before = errors.length;
    await page.goto(origin + control.url);
    await page.waitForSelector('#view-graph:not([hidden])');
    await page.waitForSelector('#graph-results:not([hidden])');
    assert.equal(await page.locator('#graph-mode').inputValue(), 'direct');
    assert.equal(await page.evaluate(() => location.search.includes('mode=')), false,
      'a refused value must not stay in the URL either');
    assert.equal(errors.length, before, `a bad permalink must not raise a page error: ${JSON.stringify(errors)}`);
    pass(`${control.url} opened Graph on mode=direct with no page error`);

    step('ruling A(ii): the Candidate view select is visible once a report is chosen');
    assert.equal(await page.locator('#graph-candidate-controls').isVisible(), true);
    assert.equal(await page.locator('#graph-mode').isVisible(), true);
    const activityOption = await page.locator('#graph-mode option[value=activity]').textContent();
    assert.equal(activityOption, 'Downstream PAC activity');
    pass(`#graph-candidate-controls visible on the overview tab, offering "${activityOption}"`);

    step('a permalink that names a cycle is not overridden by latest=1');
    // graph_data.py:317-318 replaces the requested cycle with max(years) when
    // latest=1 is present, and refresh(initial) used to send it unconditionally,
    // so the URL said one cycle while the view showed the newest one.
    graphRequests.length = 0;
    await page.goto(origin + '/?industry=Banking&cycle=2022#graph');
    await page.waitForSelector('#view-graph:not([hidden])');
    await page.waitForSelector('#graph-results:not([hidden])');
    assert.equal(await page.locator('#graph-cycle').inputValue(), '2022',
      'the cycle select must show the cycle the permalink named');
    assert.equal(new URLSearchParams(await page.evaluate(() => location.search)).get('cycle'), '2022',
      'the URL must keep the cycle the permalink named');
    const cycleRequest = graphRequests.find(url => url.includes('cycle=2022'));
    assert.ok(cycleRequest, `no /api/graph request carried cycle=2022: ${JSON.stringify(graphRequests)}`);
    assert.ok(!cycleRequest.includes('latest=1'),
      `a request that names a cycle must not also send latest=1: ${cycleRequest}`);
    pass(`request ${cycleRequest}, select 2022, URL keeps cycle=2022`);

    step('the first edit after opening a saved view drops saved= so a reload cannot overwrite the edit');
    const third = await browser.newPage({viewport: {width: 1440, height: 1050}});
    third.setDefaultTimeout(20000);
    const thirdErrors = [];
    third.on('pageerror', e => thirdErrors.push(e.message));
    await third.addInitScript(() => {
      window.__studioLines = [];
      document.addEventListener('DOMContentLoaded', () => {
        const node = document.getElementById('studio-loading');
        if (!node) return;
        const record = () => window.__studioLines.push(node.textContent || '');
        record();
        new MutationObserver(record).observe(node, {childList: true, characterData: true, subtree: true});
      });
    });
    await installRoutes(third);
    const savedLink = origin + '/?industry=Banking&saved=' + savedId + '#chart-studio';
    await third.goto(savedLink);
    await third.waitForSelector('#view-chart-studio:not([hidden])');
    await until(third, () => (window.__studioLines || []).some(line => line.includes('Opened view')),
      'the saved view opening on the third page', studioState);
    assert.equal(await third.locator('#studio-mark').inputValue(), 'bar', 'the saved view opens on its own mark type');
    // One ordinary edit, through the shipped control, which goes through commit().
    await third.selectOption('#studio-mark', 'line');
    await until(third, () => !location.search.includes('saved='),
      'saved= leaving the URL after the first edit', studioState);
    assert.equal(await third.locator('#studio-mark').inputValue(), 'line');
    await third.reload();
    await third.waitForSelector('#view-chart-studio:not([hidden])');
    await until(third, () => document.querySelectorAll('#studio-dataset option').length >= 2,
      'the third page reloading its dataset picker', studioState);
    await until(third, () => (document.getElementById('studio-mark') || {}).value === 'line',
      'the edit surviving the reload', studioState);
    assert.equal(await third.evaluate(() => location.search.includes('saved=')), false,
      'the reloaded URL must still not carry saved=');
    // The other direction: the permalink still re-opens the saved view, and the
    // id comes back with it. Both halves are pinned, so neither can regress.
    await third.goto(savedLink);
    await third.waitForSelector('#view-chart-studio:not([hidden])');
    await until(third, () => (window.__studioLines || []).some(line => line.includes('Opened view')),
      'the permalink re-opening the saved view', studioState);
    await until(third, () => (document.getElementById('studio-mark') || {}).value === 'bar',
      'the saved view replacing the edited sheet', studioState);
    assert.equal(new URLSearchParams(await third.evaluate(() => location.search)).get('saved'), savedId,
      're-opening the permalink puts the id back in the URL');
    assert.deepEqual(thirdErrors, [], `saved-view edit page errors: ${JSON.stringify(thirdErrors)}`);
    await third.close();
    pass('edit dropped saved=, survived a reload, and the permalink still re-opens the view');

    step('an industry with no cross-reference run says so, rather than "Latest run"');
    crossReferenceAvailable = false;
    crossReferenceRequests.length = 0;
    await page.goto(origin + '/?industry=Banking#cross-reference');
    await page.waitForSelector('#view-cross-reference:not([hidden])');
    await until(page, () => ((document.getElementById('xref-refresh-status') || {}).textContent || '').length > 20,
      'the cross-reference status line settling',
      () => ({status: (document.getElementById('xref-refresh-status') || {}).textContent || ''}));
    const emptyStatus = await page.locator('#xref-refresh-status').textContent();
    assert.ok(emptyStatus.startsWith('No cross-reference run for Banking'),
      `an empty industry-scoped answer must name the industry, got "${emptyStatus}"`);
    assert.ok(crossReferenceRequests.some(url => url === '/api/cross-reference?industry=Banking'),
      `expected an industry-scoped request, saw ${JSON.stringify(crossReferenceRequests)}`);
    pass(`status "${emptyStatus}"`);

    /* url_scope v6 (console redesign build 02, 2026-09-22): Graph's compare
       modes. The compared key repeats in pill order; scope 2 is read with its
       own request (never latest=1, never a company in industry mode); Copy
       link reopens the comparison; Clear comparison and the last remove leave
       one value and no `compare` in the URL. */
    const compareReady = () => document.getElementById('view-graph').dataset.compareState === 'ready'
      && !document.getElementById('graph-results').hidden;
    const compareSeen = () => ({state: window.MentatGraphCompare && MentatGraphCompare.state(), url: location.search,
      status: (document.getElementById('graph-status') || {}).textContent || ''});
    const comparePills = target => target.evaluate(() => [...document.querySelectorAll('#view-graph .chip-compare .scope-pill-text')].map(node => node.textContent));

    step('v6: an industry-compare permalink opens both scopes as pills and reads Credit Union on its own');
    graphRequests.length = 0;
    const comparePermalink = '/?industry=Banking&industry=Credit%20Union&cycle=2024&compare=industry#graph';
    await page.goto(origin + comparePermalink);
    await until(page, compareReady, 'the industry comparison settling', compareSeen);
    assert.deepEqual(await comparePills(page), ['Banking', 'Credit Union']);
    const secondScope = graphRequests.map(url => new URL(url, origin).searchParams).find(query => query.get('industry') === 'Credit Union');
    assert.ok(secondScope, `no /api/graph request for Credit Union: ${JSON.stringify(graphRequests)}`);
    assert.equal(secondScope.get('company'), '', 'industry compare shares no company across scopes');
    assert.equal(secondScope.get('cycle'), '2024', 'scope 2 uses scope 1\'s resolved cycle');
    assert.equal(secondScope.get('latest'), null, 'scope 2 never sends latest=1');
    assert.equal(await page.locator('#graph-company').isDisabled(), true, 'the Company chip is not a choice while industries are compared');
    assert.equal(await page.locator('#graph-categories .graph-half').count(), 2, 'each card splits into halves');
    assert.equal(await page.evaluate(() => (document.querySelector('.graph-trail-parts') || {}).textContent), 'Comparing industries · 2023–2024');
    assert.equal(await page.evaluate(() => MentatScope.href('graph', MentatScope.read())), comparePermalink,
      'Copy link writes the comparison back byte for byte');
    pass(`pills ${JSON.stringify(await comparePills(page))}, scope 2 request ${secondScope.toString()}`);

    step('v6: the Candidates tab becomes one sortable table with Funded by both / Only ... filters');
    await page.locator('#graph-tab-candidates').click();
    await until(page, () => !document.getElementById('graph-compare-table').hidden, 'the compare table', compareSeen);
    const headers = await page.locator('#graph-compare-grid thead th').allTextContents();
    assert.deepEqual(headers.map(text => text.replace(/[▲▼]/g, '').trim()), ['Candidate', 'Party', 'Banking', 'Credit Union', 'Difference']);
    assert.equal(await page.locator('#graph-compare-grid thead th[aria-sort=descending]').textContent().then(text => text.replace(/[▲▼]/g, '').trim()), 'Banking',
      'the default sort is scope 1, descending');
    const filters = await page.locator('#graph-compare-filters button').allTextContents();
    assert.deepEqual(filters.map(text => text.replace(/\d+/g, '').trim()), ['All', 'Funded by both', 'Only Banking', 'Only Credit Union']);
    assert.match(await page.locator('#graph-compare-range').textContent(), /^Rows 1-3 of 3$/);
    await page.locator('#graph-tab-overview').click();
    pass(`headers ${JSON.stringify(headers)}, filters ${JSON.stringify(filters)}`);

    step('v6: Copy link opens the same comparison on a fresh page; Stacked is written and Grid removes it');
    const copyPage = await browser.newPage({viewport: {width: 1440, height: 1050}});
    await installRoutes(copyPage);
    await copyPage.goto(origin + comparePermalink);
    await until(copyPage, compareReady, 'the copied comparison settling', compareSeen);
    assert.deepEqual(await comparePills(copyPage), ['Banking', 'Credit Union']);
    assert.deepEqual(await copyPage.evaluate(() => MentatGraphCompare.state().compare), {dim: 'industry', values: ['Banking', 'Credit Union']});
    await copyPage.close();
    await page.locator('#graph-layout-stacked').click();
    await until(page, () => location.search.endsWith('&layout=stacked'), 'layout=stacked in the URL', compareSeen);
    await page.locator('#graph-layout-grid').click();
    await until(page, () => !location.search.includes('layout='), 'layout= leaving the URL', compareSeen);
    pass('the copied link reopened both pills; layout=stacked came and went with the switch');

    step('v6: Clear comparison, then compare cycles from the menu by keyboard, then remove the added pill');
    await page.locator('#graph-compare-clear').click();
    await until(page, () => !document.getElementById('view-graph').dataset.compare && location.search === '?industry=Banking&cycle=2024',
      'the comparison clearing', compareSeen);
    await page.locator('#graph-compare-add').click();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await until(page, () => document.querySelectorAll('.compare-menu [role=option]').length > 0, 'the cycle list', compareSeen);
    assert.deepEqual(await page.locator('.compare-menu [role=option]').allTextContents(), ['2021–2022'], 'the current cycle is not offered');
    await page.keyboard.press('Enter');
    await until(page, compareReady, 'the cycle comparison settling', compareSeen);
    assert.equal(await page.evaluate(() => location.search), '?industry=Banking&cycle=2024&cycle=2022&compare=cycle');
    assert.deepEqual(await comparePills(page), ['2023–2024', '2021–2022']);
    // Build 02 fix round 1 (R02-M4): the headline change chip is plum, like every Graph change chip.
    const chipColour = await page.evaluate(() => {
      const chip = document.querySelector('#view-graph .graph-figure-line .change-chip');
      const probe = document.createElement('span');probe.style.color = 'var(--acu-purple)';document.body.append(probe);
      const want = getComputedStyle(probe).color;probe.remove();
      return chip ? {got: getComputedStyle(chip).color, want} : null;
    });
    assert.ok(chipColour, 'the latest cycle carries a headline change chip');
    assert.equal(chipColour.got, chipColour.want, 'the headline change chip is plum (--acu-purple)');
    await page.locator('#view-graph .chip-compare .scope-pill-remove').nth(1).focus();
    await page.keyboard.press('Enter');
    await until(page, () => !document.getElementById('view-graph').dataset.compare && location.search === '?industry=Banking&cycle=2024',
      'the last remove ending the comparison', compareSeen);
    assert.equal(await page.locator('#graph-compare-add').isVisible(), true, '"+ Compare with..." is back');
    pass('cleared, compared cycles by keyboard, and removed back to one cycle');

    assert.deepEqual(errors, [], `expected zero pageerror entries, got: ${JSON.stringify(errors)}`);
    console.log(`ALL ${stepNumber} STEPS PASSED`);
  } catch (error) {
    failed = true;
    console.error(`FAIL step ${stepNumber}: ${error && error.stack ? error.stack : error}`);
  } finally {
    if (browser) {
      // Routed pages hold their handlers open, so they are closed first; a
      // browser closed out from under them overruns its own bound and prints
      // a stray failure line after a green run.
      for (const open of browser.contexts().flatMap(context => context.pages())) {
        try { await withTimeout(open.close(), 10000, 'page.close()'); } catch (error) { console.error(error.message); }
      }
      try { await withTimeout(browser.close(), 20000, 'browser.close()'); } catch (error) { console.error(error.message); }
    }
    await stopServer(server);
    await cleanupPath(dataRoot);
    await cleanupPath(reportRoot);
  }
  process.exitCode = failed ? 1 : 0;
})();
