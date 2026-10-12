/* The N charts strip is gone, and an old pane link opens the ordinary console.
 *
 * Paul's ruling, 2026-09-19: "Remove the N charts strip, keep the Board". The
 * strip was the 2026-09-13 split view: a row above the Chart Studio header
 * offering 1 / 2 / 3 / 4 charts, "Canvas only in every chart" and "Linked
 * filters", where each extra chart was an iframe of this same page carrying
 * ?studioPane=B|C|D.
 *
 * What this proves that a unit test cannot: the strip's controls are not on
 * the page and no iframe of this console is created; a bookmarked
 * ?studioPane=B opens the ordinary Chart Studio with the whole navigation
 * visible, which is the 2026-09-13 trap (a top-level pane page hid every tab
 * and left no way back); the Board still draws two tiles with two titles and
 * two reconciliation lines, so removing the strip did not take the Board with
 * it; and none of it raises a page or console error.
 *
 * Design: audits/mentat_failings_20260915/UI_CLEANUP_DESIGN_2026-09-17.md
 * section 11 (the Board) and its 2026-09-19 retirement note.
 * Contract: contracts/url_scope.json v5, retaining the v4 studioPane retirement.
 *
 * Spawn pattern and stubbing: tests/test_ui_cleanup_browser.cjs - a real
 * `python app/server.py` on a free ephemeral port with a throwaway
 * --data-root and a throwaway MENTAT_REPORT_DIRECTORY, the contribution
 * dataset stubbed because no real snapshot exists in a throwaway root.
 *
 * Run: node tests/test_studio_strip_removed_browser.cjs   (cwd: mentat/)
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
const urlScope = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'url_scope.json'), 'utf8'));

const FORBIDDEN_PORTS = new Set([8801, 8802, 8806, 8808, 8841]);
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
// The report folder is the server's working directory: server.py chdir()s into its report
// directory. With MENTAT_PYTHON set to a venv's python.exe, the child stopServer() kills is
// the venv launcher (bpo-35797) and the console is the launcher's child, still exiting - and
// still in that folder - when the launcher's exit is reported. Node 24's rmSync then throws
// EPERM "Permission denied" at once, whatever maxRetries and retryDelay say (measured: 1 ms),
// which left an empty %TEMP%\mentat-strip-reports-* after every such run. So wait here for
// the folder to be free, up to 10 s, pass or fail.
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
    scope: 'Strip-removal browser-test fixture. Not real collection data.'
  }
};
const REGISTRY_ENTRIES = snapshotRegistry.examples[0].snapshots;
const graphRows = (name, n = 3) => Array.from({length: n}, (_, i) =>
  ({id: `${name}-${i}`, label: `${name} label ${i}`, amount: 400000000 - i * 90000000}));

/* The strings and selectors the strip owned. Every one of them is asserted
   absent; the list is the census, so a future re-introduction fails here. */
const STRIP_LABELS = ['1 chart', '2 charts', '3 charts', '4 charts',
  'Canvas only in every chart', 'Builder in every chart', 'Linked filters'];
const STRIP_SELECTORS = ['.studio-comparison-toolbar', '#studio-comparison-grid',
  '.studio-comparison-grid', '.studio-comparison-frame', '.studio-count-toggle',
  '.studio-filter-links', '.studio-link-status', '.studio-pane-tag'];

let stepNumber = 0, passes = 0;
function step(label) { stepNumber += 1; console.log(`STEP ${stepNumber}: ${label}`); }
function pass(detail) { passes += 1; console.log(`PASS step ${stepNumber} - ${detail}`); }
function setup(label) { console.log(`SETUP: ${label}`); }

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
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-strip-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-strip-reports-'));

  async function installRoutes(page) {
    await page.route(target => target.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url());
      const where = target.pathname;
      if (where === '/api/contributions/session') {
        return route.fulfill({json: {token: 'strip-token', read_only: false,
          industry: 'Banking', industries: REGISTRY_ENTRIES}});
      }
      if (where === '/api/contributions/catalog') {
        return route.fulfill({json: {datasets: [BANKING], fields: COMMON_FIELDS, amount_unit: 'cents'}});
      }
      if (where === '/api/contributions/query') {
        const body = route.request().postDataJSON() || {};
        const dims = body.dimensions && body.dimensions.length ? body.dimensions : ['company'];
        const made = ['Goldman Fixture', 'Wells Fixture', 'Morgan Fixture'].map((who, i) => Object.assign(
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
      // A tile's reconciliation line asks Graph for the same scope, so this
      // route is what makes "two reconciliation lines" observable at all.
      if (where === '/api/graph') {
        return route.fulfill({json: {
          options: {companies: ['Goldman Fixture', 'Wells Fixture'], cycles: ['2022', '2024', '2026'],
            unknown_tiers: false, recipients: []},
          filters: {company: '', cycle: '', tier: '', mode: 'direct', direction: 'Support'},
          totals: {receipt_groups: 9, giving: 1200318112, direct: 216000, organizations: 982000,
            unresolved_candidate_links: 0},
          candidate_available: true, candidate_note: 'Fixture.', note: 'Offline fixture only.',
          reports: [{name: 'Banking fixture workbook.xlsx', sha256: 'c'.repeat(64), freshness: {sources: []}}],
          candidate_details: [], categories: graphRows('categories'), organizations: graphRows('organizations'),
          candidates: graphRows('candidates'), companies: graphRows('companies'), levels: graphRows('levels')}});
      }
      if (where === '/api/cross-reference') {
        return route.fulfill({json: {available: false, status: 'empty', industry: 'Banking', message: 'stub'}});
      }
      return route.continue();
    });
  }

  async function until(target, predicate, label, diagnose, attempts = 160) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await target.evaluate(predicate)) return;
      await target.waitForTimeout(250);
    }
    const seen = diagnose ? await target.evaluate(diagnose) : null;
    throw new Error(label + ' never happened' + (seen ? '. Page: ' + JSON.stringify(seen) : '.'));
  }

  try {
    setup('pick a free port and launch the console on a throwaway --data-root and report directory');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    const origin = `http://127.0.0.1:${port}`;
    browser = await withTimeout(
      chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}),
      35000, 'chromium.launch()');
    console.log(`SETUP done - server ready on port ${port}, data root ${dataRoot}`);

    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(20000);
    const pageErrors = [], consoleErrors = [], refusals = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    // Step 2 asks for the retired script on purpose and wants the 404, so that
    // one request is the only thing excused from the "no refusals" step.
    const deliberate = url => String(url).includes('/chart-comparison.js');
    page.on('response', response => {
      if (response.status() >= 400 && !deliberate(response.url())) {
        refusals.push(`${response.status()} ${response.url()}`);
      }
    });
    await installRoutes(page);
    await page.goto(origin + '/?industry=Banking#chart-studio', {waitUntil: 'domcontentloaded'});
    await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the first chart drawing',
      () => ({note: (document.getElementById('studio-note') || {}).textContent}));

    step('the strip\'s controls are not on the page, and no script asks for chart-comparison.js');
    const census = await page.evaluate(([visibleSource, labels, selectors]) => {
      const visible = eval(visibleSource);
      const view = document.getElementById('view-chart-studio');
      const texts = [...document.querySelectorAll('button, summary')].map(node => node.textContent.trim());
      return {
        labelsPresent: labels.filter(label => texts.includes(label)),
        selectorsPresent: selectors.filter(selector => document.querySelector(selector)),
        frames: document.querySelectorAll('iframe').length,
        scripts: [...document.querySelectorAll('script[src]')].map(node => node.getAttribute('src')),
        controls: [...view.querySelectorAll('button, select, input, a[role=button], summary')].filter(visible).length,
        canvasTop: Math.round(document.getElementById('studio-canvas').getBoundingClientRect().top + window.scrollY),
        markVisible: visible(document.getElementById('studio-mark')),
        // The label's own words, not the select's option list behind them.
        markLabel: (document.querySelector('label[for="studio-mark"]') || {}).firstChild?.textContent
      };
    }, [VISIBLE_SOURCE, STRIP_LABELS, STRIP_SELECTORS]);
    assert.deepEqual(census.labelsPresent, [], 'no strip control label may remain on the page');
    assert.deepEqual(census.selectorsPresent, [], 'no strip element may remain in the DOM');
    assert.equal(census.frames, 0, 'the console must frame no copy of itself');
    assert.ok(!census.scripts.some(src => String(src).includes('chart-comparison')),
      'index.html must not load chart-comparison.js: ' + JSON.stringify(census.scripts));
    assert.equal(census.markVisible, true, 'the chart variant select survives the strip that hosted it');
    pass(`no strip controls, no iframes, ${census.controls} visible controls, `
      + `#studio-canvas top ${census.canvasTop}px, chart variant labelled "${String(census.markLabel).trim()}"`);

    step('chart-comparison.js is not served, so a cached page cannot resurrect the strip');
    const asset = await page.evaluate(async () => {
      const response = await fetch('/chart-comparison.js');
      return response.status;
    });
    assert.equal(asset, 404, 'GET /chart-comparison.js must be 404, not silently served');
    // The browser logs that refusal, and this test is the one that asked for it.
    const probe = consoleErrors.findIndex(text => text.includes('404'));
    if (probe >= 0) consoleErrors.splice(probe, 1);
    pass('GET /chart-comparison.js answers 404');

    step('the Board still draws two tiles, two titles and two reconciliation lines');
    await page.click('#studio-duplicate');
    await page.waitForTimeout(1200);
    await page.click('#studio-view-canvas');
    await page.click('#studio-board-2');
    await until(page, () => document.querySelectorAll('#studio-board svg').length >= 2,
      'both board tiles drawing', () => ({tiles: document.getElementById('studio-board').children.length}));
    const board = await page.evaluate(() => {
      const node = document.getElementById('studio-board');
      const text = list => [...list].map(item => item.textContent.trim()).filter(Boolean);
      return {
        tiles: node.children.length,
        drawings: node.querySelectorAll('svg').length,
        titles: text(node.querySelectorAll('h3, .studio-tile-title')),
        reconciles: text(node.querySelectorAll('.studio-reconcile'))
      };
    });
    assert.equal(board.tiles, 2, 'Board 2 is two tiles');
    assert.equal(board.drawings, 2, 'and both of them draw');
    assert.equal(board.titles.length, 2, 'two titles');
    assert.equal(board.reconciles.length, 2, 'and two reconciliation lines, so no figure loses its denominator');
    pass(`Board 2: ${board.tiles} tiles, ${board.drawings} drawings, ${board.titles.length} titles, `
      + `${board.reconciles.length} reconciliation lines`);

    step('a bookmarked ?studioPane=B opens the ordinary Chart Studio with the navigation visible');
    await page.goto(origin + '/?industry=Banking&studioPane=B#chart-studio', {waitUntil: 'domcontentloaded'});
    await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the drawing on the pane permalink');
    const pane = await page.evaluate(([visibleSource, selectors]) => {
      const visible = eval(visibleSource);
      return {
        navVisible: visible(document.querySelector('.app-navigation')),
        headingVisible: visible(document.querySelector('.workspace-heading')),
        tabs: [...document.querySelectorAll('.workspace-tabs button')].filter(visible).map(n => n.textContent.trim()),
        embedded: document.body.classList.contains('studio-embedded'),
        strip: selectors.filter(selector => document.querySelector(selector)),
        search: location.search,
        store: Object.keys(localStorage).filter(key => key.includes('-pane-') || key.includes('comparison'))
      };
    }, [VISIBLE_SOURCE, STRIP_SELECTORS]);
    assert.equal(pane.navVisible, true, 'the whole console navigation is on screen - the 2026-09-13 trap hid it');
    assert.equal(pane.headingVisible, true, 'and so is the workspace heading');
    assert.ok(pane.tabs.includes('Graph') && pane.tabs.includes('Chart Studio'),
      'every workspace tab is reachable: ' + JSON.stringify(pane.tabs));
    assert.equal(pane.embedded, false, 'the page never puts itself into the embedded pane mode');
    assert.deepEqual(pane.strip, [], 'and the pane key brings no strip back with it');
    assert.equal(pane.search, '?industry=Banking&studioPane=B',
      'the retired key is left in the query untouched, like any other unknown key');
    assert.deepEqual(pane.store, [], 'and no per-pane workspace store is written');
    pass(`?studioPane=B: ${pane.tabs.length} tabs visible, not embedded, query preserved as "${pane.search}"`);

    step('the contract records the retirement and the module still never reads the key');
    // v6 (build 02, 2026-09-22) added Graph's compare and layout keys and kept
    // the studioPane retirement as it was. v7 (lane F, 2026-09-26) adds only the
    // PAC evidence Flow view's pane keys <pane>.flow; the studioPane entry below
    // is unchanged, so the pin follows the contract's version.
    assert.equal(urlScope.version, 7, 'url_scope is v7, retaining the retired studioPane contract');
    assert.ok(/retired/i.test(urlScope.keys.studioPane.value),
      'the key table says studioPane is retired: ' + urlScope.keys.studioPane.value);
    assert.deepEqual(urlScope.keys.studioPane.readers, [], 'nothing reads it');
    assert.deepEqual(urlScope.keys.studioPane.writers, [], 'and nothing writes it');
    pass(`url_scope v${urlScope.version}: studioPane retired, no readers, no writers`);

    step('none of that raised a page error, a console error or a refusal');
    assert.deepEqual(pageErrors, [], 'no page errors');
    assert.deepEqual(consoleErrors, [], 'no console errors');
    assert.deepEqual(refusals, [], 'no 4xx or 5xx responses');
    pass('zero page errors, zero console errors, zero refusals');
  } catch (error) {
    failed = true;
    console.error(`FAIL step ${stepNumber}: ${error && error.message}`);
    if (error && error.stack) console.error(error.stack);
  } finally {
    if (browser) await withTimeout(browser.close(), 15000, 'browser.close()').catch(() => {});
    await stopServer(server);
    await cleanupPath(dataRoot);
    await cleanupPath(reportRoot);
  }
  console.log(failed ? `RESULT: FAILED after ${passes} passing steps`
    : `RESULT: ${passes} of ${stepNumber} steps passed`);
  process.exitCode = failed ? 1 : 0;
})();
