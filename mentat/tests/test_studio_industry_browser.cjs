/* Cross-industry Chart Studio saved views (S4, Area E), real-client pass.
 *
 * The DE-021 gap this closes: viewSourceRecord() dropped `industry` at save
 * time, so a saved view's source never named which industry it came from.
 * S4_DESIGN.md section 4 fixes the producers in chart-studio-core.js and
 * chart-studio.js; this test proves the fix from the real client rather than
 * from a hand-built payload, per contracts/README.md's "real-client pass"
 * rule: it spawns the actual `python app/server.py` console on a free port
 * with a throwaway --data-root (mirroring the spawn pattern in
 * tests/test_evidence_csv_browser.cjs - see the note below on
 * test_studio_briefing_tray_browser.cjs, which the brief named for this
 * pattern but which does not spawn a server at all), saves views from the
 * real page, reloads, and reads the saved <id>.json back off real disk.
 *
 * Two available "contributions" datasets are needed and none exist in a
 * throwaway --data-root (a real one requires a hydrated .cache payload with
 * hashes matching a real workbook - see contribution_data.py:_sources()).
 * Exactly like test_original_studio_contributions_browser.cjs and
 * test_contribution_studio_state_browser.cjs, which are the existing browser
 * tests that obtain an "available" contribution dataset for Chart Studio,
 * this test stubs /api/contributions/session, /catalog and /query (extended
 * here to two datasets instead of one). Every other request - in particular
 * /api/studio/views* - is left unrouted and reaches the real spawned server,
 * so a saved view really lands on disk.
 *
 * Finding worth recording before the steps below: selectContributionDataset()
 * (chart-studio.js) rewrites `state.source` for every sheet in the CURRENT
 * view when the Report picker changes (`for (const state of sheets) ...`),
 * and `sourceKey` is one value for the whole view. There is no control in
 * the shipped UI that sets a second, different industry on a second sheet of
 * the same view - sheet.sources[] (the true multi-industry fan-out field,
 * S4_DESIGN.md section 4) has no UI writer yet ("S4 delivers the model, not
 * the chart"). So "two industries in one saved view" is not reachable by
 * real clicks today; this test saves two views instead (one per industry),
 * which is the fallback the brief names ("two views if the UI ties one
 * dataset per view"), and separately exercises openView()/openability() by
 * opening the view for the industry that is NOT the console's current one.
 *
 * Run: node tests/test_studio_industry_browser.cjs   (cwd: mentat/)
 * Needs MENTAT_NODE_MODULES pointing at a Playwright install, or the
 * default ~/.cache/codex-runtimes/... path used by every other .cjs test
 * here.
 */
'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const net = require('node:net');

const MENTAT_DIR = path.resolve(__dirname, '..');
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

/* --------------------------------------------------- two fabricated datasets */
// Same shape contribution_data.py:catalog() sends for a real snapshot
// (id/industry/label/available/fields/bases/provenance), with two distinct
// workbook_sha256 values so the two views get two distinct sourceKeys.

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
      scope: 'S4 browser-test fixture. Not real collection data.'
    }
  };
}
const BANKING = datasetEntry('Banking', 'a'.repeat(64));
const CREDIT_UNION = datasetEntry('Credit Union', 'b'.repeat(64));

function queryResponseFor(entry, body) {
  const dims = body.dimensions && body.dimensions.length ? body.dimensions : ['company'];
  const rows = [Object.assign({}, ...dims.map(d => ({[d]: d === 'cycle' ? '2025-2026' : entry.industry + ' Donor'})),
    {amount: 123400, records: 3, contributors: 3})];
  return {
    status: 'ready', rows, columns: [...dims, ...(body.measures || ['amount'])].map(id => COMMON_FIELDS.find(f => f.id === id) || {id, label: id}),
    totals: {amount: 123400, records: 3, contributors: 3}, group_count: rows.length,
    offset: 0, has_more: false, provenance: entry.provenance, basis_label: 'Published included receipts',
    reconciliation: {conserved: true, receipt_matches_workbook: true}, notices: [], verified_unique: false
  };
}

let stepNumber = 0;
function step(label) { stepNumber += 1; console.log(`STEP ${stepNumber}: ${label}`); }
function pass(detail) { console.log(`PASS step ${stepNumber} - ${detail}`); }

/* ---------------------------------------------- node-driven fallback (no browser) */
// Step 6 of the brief: if the copy lacks the browser driver package, drive the real
// producers (createSheet, migrateSheet, viewSourceRecord) from node instead of a page,
// and POST the record to the spawned writable server, GET it back and compare. Kept in
// this one file - the brief names a single new file for this task.
async function nodeDrivenFallback(reason) {
  console.log('PENDING BROWSER DRIVER: ' + reason);
  console.log('Falling back to node-driven real producers, not a page.');
  const http = require('node:http');
  const core = require(path.join(MENTAT_DIR, 'app', 'chart-studio-core.js'));
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-studio-industry-node-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-studio-industry-node-reports-'));
  let server = null;
  let failed = false;
  try {
    step('pick a free port and launch the console server on a throwaway --data-root');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
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
    pass(`server ready on port ${port}, data root ${dataRoot}, report directory ${reportRoot}`);

    step('build two sheets with the real producers (createSheet + viewSourceRecord), one per industry');
    const bankingChosen = {id: 'Banking', label: 'Banking', industry: 'Banking'};
    const creditUnionChosen = {id: 'Credit Union', label: 'Credit Union', industry: 'Credit Union'};
    const bankingSheetSource = {reportName: 'Banking Industry Spending Overview.xlsx', sha256: 'a'.repeat(64),
      runName: 'Configured completed-report folder', sheet: 'Verified source observations', mode: 'contributions'};
    const creditUnionSheetSource = {reportName: 'Credit Union Industry Spending Overview.xlsx', sha256: 'b'.repeat(64),
      runName: 'Configured completed-report folder', sheet: 'Verified source observations', mode: 'contributions'};
    const bankingSheet = Object.assign(core.createSheet('Recipients', 'sheet-1'), {source: Object.assign({}, bankingSheetSource, {dataset: 'Banking', industry: 'Banking'})});
    const creditUnionSheet = Object.assign(core.createSheet('Recipients', 'sheet-1'), {source: Object.assign({}, creditUnionSheetSource, {dataset: 'Credit Union', industry: 'Credit Union'})});
    const bankingRecord = {name: 'S4 node-driven Banking', sourceKey: 'contributions|Banking|' + 'a'.repeat(64),
      source: core.viewSourceRecord(bankingSheetSource, bankingChosen), sheets: [bankingSheet], active: 0};
    const creditUnionRecord = {name: 'S4 node-driven Credit Union', sourceKey: 'contributions|Credit Union|' + 'b'.repeat(64),
      source: core.viewSourceRecord(creditUnionSheetSource, creditUnionChosen), sheets: [creditUnionSheet], active: 0};
    assert.equal(bankingRecord.source.industry, 'Banking');
    assert.equal(creditUnionRecord.source.industry, 'Credit Union');
    pass('viewSourceRecord() resolved industry for both records, from the real producer');

    function post(pathname, body) {
      return new Promise((resolve, reject) => {
        const data = JSON.stringify(body);
        const req = http.request({host: '127.0.0.1', port, path: pathname, method: 'POST',
          headers: {'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data)}}, res => {
          let raw = '';
          res.on('data', chunk => raw += chunk);
          res.on('end', () => { try { resolve({status: res.statusCode, body: JSON.parse(raw)}); } catch (error) { reject(error); } });
        });
        req.on('error', reject);
        req.end(data);
      });
    }
    function get(pathname) {
      return new Promise((resolve, reject) => {
        http.get({host: '127.0.0.1', port, path: pathname}, res => {
          let raw = '';
          res.on('data', chunk => raw += chunk);
          res.on('end', () => { try { resolve({status: res.statusCode, body: JSON.parse(raw)}); } catch (error) { reject(error); } });
        }).on('error', reject);
      });
    }

    step('POST both records to the spawned writable server');
    const bankingSaved = await post('/api/studio/views', bankingRecord);
    const creditUnionSaved = await post('/api/studio/views', creditUnionRecord);
    assert.equal(bankingSaved.status, 200, `Banking save failed: ${JSON.stringify(bankingSaved.body)}`);
    assert.equal(creditUnionSaved.status, 200, `Credit Union save failed: ${JSON.stringify(creditUnionSaved.body)}`);
    pass(`both records saved: ${bankingSaved.body.id}, ${creditUnionSaved.body.id}`);

    step('GET each view back and compare against what was sent');
    const bankingBack = await get('/api/studio/views?id=' + bankingSaved.body.id);
    const creditUnionBack = await get('/api/studio/views?id=' + creditUnionSaved.body.id);
    assert.equal(bankingBack.body.view.source.industry, 'Banking');
    assert.equal(bankingBack.body.view.sheets[0].source.industry, 'Banking');
    assert.equal(creditUnionBack.body.view.source.industry, 'Credit Union');
    assert.equal(creditUnionBack.body.view.sheets[0].source.industry, 'Credit Union');
    pass('both GETs echo source.industry unchanged from what viewSourceRecord() sent');

    step('read the two saved <id>.json files straight off the throwaway data root');
    const viewsDir = path.join(dataRoot, 'studio', 'views');
    const bankingOnDisk = fs.readFileSync(path.join(viewsDir, bankingSaved.body.id + '.json'), 'utf8');
    const creditUnionOnDisk = fs.readFileSync(path.join(viewsDir, creditUnionSaved.body.id + '.json'), 'utf8');
    console.log('--- on-disk ' + bankingSaved.body.id + '.json (S4 node-driven Banking) ---');
    console.log(bankingOnDisk);
    console.log('--- on-disk ' + creditUnionSaved.body.id + '.json (S4 node-driven Credit Union) ---');
    console.log(creditUnionOnDisk);
    pass('both on-disk view files printed above verbatim');

    console.log(`ALL ${stepNumber} STEPS PASSED (node-driven real producers, not a page)`);
  } catch (error) {
    failed = true;
    console.error(`FAIL step ${stepNumber}: ${error && error.stack ? error.stack : error}`);
  } finally {
    await stopServer(server);
    await cleanupPath(dataRoot);
    await cleanupPath(reportRoot);
  }
  return failed ? 1 : 0;
}

(async () => {
  const playwright = resolvePlaywright();
  if (playwright.error) {
    process.exitCode = await nodeDrivenFallback('playwright could not be resolved at '
      + playwright.modules + ' (' + playwright.error.message + ').');
    return;
  }
  const {chromium} = playwright;

  let browser = null;
  let server = null;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-studio-industry-data-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-studio-industry-data-reports-'));
  let failed = false;

  try {
    step('pick a free port and launch the console server on a throwaway --data-root');
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
    pass(`server ready on port ${port}, data root ${dataRoot}, report directory ${reportRoot}`);

    step('launch headless Chrome and stub the two contribution datasets');
    browser = await withTimeout(
      chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}),
      35000, 'chromium.launch()');
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const queryBodies = [];
    await page.route('**/api/contributions/session', route => route.fulfill({json: {token: 'studio-industry-browser-token', read_only: false}}));
    await page.route('**/api/contributions/catalog', route => route.fulfill({json: {datasets: [BANKING, CREDIT_UNION], fields: COMMON_FIELDS, amount_unit: 'cents'}}));
    await page.route('**/api/contributions/query', route => {
      const body = route.request().postDataJSON();
      queryBodies.push(body);
      const entry = body.dataset === 'Credit Union' ? CREDIT_UNION : BANKING;
      route.fulfill({json: queryResponseFor(entry, body)});
    });
    pass('routes stubbed for session, catalog (Banking + Credit Union, both available) and query');

    step('open Chart Studio in Verified contributions mode and wait for the dataset picker');
    await page.goto(origin + '/');
    await page.click('#tab-chart-studio');
    await page.waitForSelector('#view-chart-studio:not([hidden])');
    await page.waitForFunction(() => document.querySelectorAll('#studio-dataset option').length >= 2);
    assert.equal(await page.locator('#studio-source-mode').inputValue(), 'contributions');
    const datasetOptions = await page.locator('#studio-dataset option').allTextContents();
    assert.ok(datasetOptions.some(t => t.includes('Banking')), `expected a Banking option, got ${JSON.stringify(datasetOptions)}`);
    assert.ok(datasetOptions.some(t => t.includes('Credit Union')), `expected a Credit Union option, got ${JSON.stringify(datasetOptions)}`);
    await page.waitForSelector('#studio-canvas svg');
    pass('Chart Studio open, both datasets listed, first chart rendered');

    async function openExportMenu() {
      // Save view and Open view live in the Views menu since the 2026-09-17
      // UI clean-up (item 14). <details> toggles on every click of its
      // summary, so this only clicks when it is actually closed - a second
      // save's blind click on an open menu would close it instead.
      if (!(await page.locator('#studio-views').evaluate(el => el.open))) await page.click('#studio-views > summary');
      await page.waitForSelector('#studio-save-view', {state: 'visible'});
    }
    async function saveCurrentViewAs(name) {
      await openExportMenu();
      await page.click('#studio-save-view');
      const input = page.locator('input.studio-view-name');
      await input.fill(name);
      await input.press('Enter');
      await page.waitForFunction(text => (document.getElementById('studio-status') || {}).textContent?.includes(text)
          || document.body.textContent.includes('Saved view "' + text + '"'),
        name, {timeout: 10000}).catch(() => {});
    }

    step('save the Banking sheet as its own view (dataset defaults to the first available report)');
    assert.equal(await page.locator('#studio-dataset').inputValue(), 'Banking');
    const bankingIndustryInQuery = queryBodies.at(-1) && queryBodies.at(-1).industry;
    await saveCurrentViewAs('S4 browser Banking');
    pass(`Banking view saved; last query body carried industry=${JSON.stringify(bankingIndustryInQuery)}`);

    step('switch the Report picker to Credit Union and save that as a second view');
    // The Data source panel folds itself away after the first successful load
    // (chart-studio.js el('source-details').addEventListener('toggle', ...)), so
    // it has to be reopened with the "Data source" button before a second pick.
    // Build 03 (S1): the pickers live in the source chip's popover.
    if (!(await page.locator('#studio-dataset').isVisible())) await page.click('#studio-scope-change');
    await page.waitForSelector('#studio-dataset', {state: 'visible'});
    await page.selectOption('#studio-dataset', 'Credit Union');
    await page.waitForFunction(() => document.querySelector('#studio-canvas svg'));
    const creditUnionIndustryInQuery = queryBodies.at(-1) && queryBodies.at(-1).industry;
    await saveCurrentViewAs('S4 browser Credit Union');
    pass(`Credit Union view saved; last query body carried industry=${JSON.stringify(creditUnionIndustryInQuery)}`);
    assert.equal(bankingIndustryInQuery, 'Banking', 'compile() must send industry beside dataset for the Banking sheet');
    assert.equal(creditUnionIndustryInQuery, 'Credit Union', 'compile() must send industry beside dataset for the Credit Union sheet');

    step('reload the console and read the saved views back over HTTP, from the real server');
    await page.reload();
    await page.click('#tab-chart-studio');
    await page.waitForSelector('#view-chart-studio:not([hidden])');
    await page.waitForFunction(() => document.querySelectorAll('#studio-dataset option').length >= 2);
    const listed = await page.evaluate(async () => (await (await fetch('/api/studio/views')).json()).views);
    const bankingItem = listed.find(v => v.name === 'S4 browser Banking');
    const creditUnionItem = listed.find(v => v.name === 'S4 browser Credit Union');
    assert.ok(bankingItem, `S4 browser Banking not in list_views response: ${JSON.stringify(listed)}`);
    assert.ok(creditUnionItem, `S4 browser Credit Union not in list_views response: ${JSON.stringify(listed)}`);
    for (const [label, item] of [['Banking', bankingItem], ['Credit Union', creditUnionItem]]) {
      // server.py Console.list_views emits this now, so the check is
      // unconditional: a branch that only logs when the field is missing
      // cannot fail, and a test that cannot fail proves nothing.
      assert.ok(Array.isArray(item.industries),
        `list_views item for "${label}" carries no industries[]: ${JSON.stringify(item)}`);
      assert.deepEqual(item.industries, [label], `list_views industries for ${label} view`);
      console.log(`list_views industries[] for "${label}" view: ${JSON.stringify(item.industries)}`);
    }
    pass('both saved views found in GET /api/studio/views from the real server');

    step('read each saved view over HTTP and assert every sheet’s source.industry survived');
    const bankingView = await page.evaluate(async id => (await (await fetch('/api/studio/views?id=' + id)).json()).view, bankingItem.id);
    const creditUnionView = await page.evaluate(async id => (await (await fetch('/api/studio/views?id=' + id)).json()).view, creditUnionItem.id);
    assert.equal(bankingView.source.industry, 'Banking', 'Banking view: top-level source.industry');
    for (const sheet of bankingView.sheets) assert.equal(sheet.source.industry, 'Banking', `Banking view sheet "${sheet.name}" source.industry`);
    assert.equal(creditUnionView.source.industry, 'Credit Union', 'Credit Union view: top-level source.industry');
    for (const sheet of creditUnionView.sheets) assert.equal(sheet.source.industry, 'Credit Union', `Credit Union view sheet "${sheet.name}" source.industry`);
    pass('every sheet in both saved views carries the correct source.industry');

    step('open the Credit Union view through the real UI (not the current console industry) and confirm openView() switches the report');
    await openExportMenu();
    await page.click('#studio-open-views');
    const openItem = page.locator('.studio-send button', {hasText: 'S4 browser Credit Union'});
    await openItem.waitFor({timeout: 10000});
    await openItem.click();
    await page.waitForFunction(() => document.getElementById('studio-dataset').value === 'Credit Union');
    pass('openView()/openability() opened the Credit Union view from the Banking source and switched the Report picker');

    step('read the two saved <id>.json files straight off the throwaway data root');
    const viewsDir = path.join(dataRoot, 'studio', 'views');
    const bankingPath = path.join(viewsDir, bankingItem.id + '.json');
    const creditUnionPath = path.join(viewsDir, creditUnionItem.id + '.json');
    const bankingOnDisk = fs.readFileSync(bankingPath, 'utf8');
    const creditUnionOnDisk = fs.readFileSync(creditUnionPath, 'utf8');
    JSON.parse(bankingOnDisk); JSON.parse(creditUnionOnDisk); // must be valid JSON
    console.log('--- on-disk ' + bankingItem.id + '.json (S4 browser Banking) ---');
    console.log(bankingOnDisk);
    console.log('--- on-disk ' + creditUnionItem.id + '.json (S4 browser Credit Union) ---');
    console.log(creditUnionOnDisk);
    pass('both on-disk view files are valid JSON and printed above verbatim');

    step('final check: zero pageerror entries');
    assert.equal(errors.length, 0, `expected zero pageerror entries, got: ${JSON.stringify(errors)}`);
    pass('zero pageerror entries across the whole run');

    console.log(`ALL ${stepNumber} STEPS PASSED`);
  } catch (error) {
    failed = true;
    console.error(`FAIL step ${stepNumber}: ${error && error.stack ? error.stack : error}`);
  } finally {
    if (browser) { try { await withTimeout(browser.close(), 10000, 'browser.close()'); } catch (error) { console.error(error.message); } }
    await stopServer(server);
    await cleanupPath(dataRoot);
    await cleanupPath(reportRoot);
  }
  process.exitCode = failed ? 1 : 0;
})();
