/* A Chart Studio render that goes stale clears the busy mark it set on the canvas.
 *
 * renderContributionView marks #studio-canvas aria-busy="true" while a verified query is
 * on its way (chart-studio.css fades it to .45 and turns pointer events off), and cleared
 * the mark only in its finally while it was still the current render. A render made stale
 * without a newer contribution render taking over left the mark behind: switching the Data
 * source to Report worksheet aborts every flight and bumps the ticket, and the worksheet
 * path never touches aria-busy, so every worksheet chart drawn after it was faded and could
 * not be clicked. Lane P (map-studio.js) and lane F (studio-flow.js) had cleared the mark
 * for their own drawings; lane L2 (2026-09-26) records which render set the mark
 * (canvas.__busyTicket) and lets that render clear it when it goes stale, unless a newer
 * render has set its own.
 *
 * What this proves that a unit test cannot: the mark is set while a real verified query is
 * pending; after the Data source switch the canvas is neither marked nor faded nor inert;
 * back on verified data the next answer draws and clears the mark as before; no page error.
 *
 * Spawn pattern and stubbing: tests/test_studio_strip_removed_browser.cjs - a real
 * `python app/server.py` on a free ephemeral port with a throwaway --data-root and a
 * throwaway MENTAT_REPORT_DIRECTORY, the contribution API stubbed per request.
 *
 * Run: node tests/test_studio_busy_mark_browser.cjs   (cwd: mentat/)
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
const FORBIDDEN_PORTS = new Set([8801, 8802, 8806, 8808, 8841]);

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
// The report folder is the server's working directory, and a venv-launched server is still
// exiting when its launcher's exit is reported; Node 24's rmSync fails such a folder at once
// (tests/test_studio_strip_removed_browser.cjs has the measurement). Wait for it, up to 10 s.
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
const FIELDS = [
  {id: 'company', label: 'Company', name: 'Company', role: 'dimension', type: 'category'},
  {id: 'cycle', label: 'Election cycle', name: 'Election cycle', role: 'dimension', type: 'category'},
  {id: 'amount', label: 'Selected amount', name: 'Selected amount', role: 'measure', type: 'number', unit: 'cents'},
  {id: 'records', label: 'Observation count', name: 'Observation count', role: 'measure', type: 'number', unit: 'count'},
  {id: 'contributors', label: 'Distinct reported donor identities', name: 'Distinct reported donor identities', role: 'measure', type: 'number', unit: 'count'}
];
const BANKING = {
  id: 'Banking', industry: 'Banking', label: 'Banking', available: true, message: null,
  fields: FIELDS, bases: ['receipt', 'raw'].map(id => ({id, label: id, status: 'observed'})), default_basis: 'receipt',
  provenance: {workbook: 'Banking fixture workbook.xlsx', sha256: 'b'.repeat(64)}
};
function answer(body) {
  const dims = body.dimensions && body.dimensions.length ? body.dimensions : ['company'];
  const rows = ['Goldman Fixture', 'Wells Fixture'].map((who, i) => Object.assign(
    {}, ...dims.map(d => ({[d]: d === 'cycle' ? '2023-2024' : who})),
    {amount: 260398435 - i * 70000000, records: 2 - i, contributors: 2 - i}));
  return {status: 'ready', rows,
    columns: [...dims, ...(body.measures || ['amount'])].map(id => FIELDS.find(f => f.id === id) || {id, label: id}),
    totals: {amount: 450796870, records: 3, contributors: 3}, group_count: rows.length,
    offset: 0, has_more: false, provenance: BANKING.provenance, basis_label: 'Published included receipts',
    reconciliation: {conserved: true, receipt_matches_workbook: true}, notices: [], verified_unique: false};
}

/* ------------------------------------------------------------- the harness */
let stepNumber = 0, passes = 0;
const step = label => { stepNumber += 1; console.log(`STEP ${stepNumber}: ${label}`); };
const pass = detail => { passes += 1; console.log(`PASS step ${stepNumber} - ${detail}`); };
const setup = label => console.log(`SETUP: ${label}`);

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
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-busy-mark-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-busy-mark-reports-'));
  const held = [];
  let holdQueries = true;

  async function installRoutes(page) {
    await page.route(target => target.pathname.startsWith('/api/'), async route => {
      const where = new URL(route.request().url()).pathname;
      if (where === '/api/contributions/session') {
        return route.fulfill({json: {token: 'busy-token', read_only: false, industry: 'Banking',
          industries: [{name: 'Banking', label: 'Banking'}]}});
      }
      if (where === '/api/contributions/catalog') {
        return route.fulfill({json: {datasets: [BANKING], fields: FIELDS, amount_unit: 'cents'}});
      }
      if (where === '/api/contributions/query') {
        // Held: the render that asked waits, as it does on a console still preparing its data.
        if (holdQueries) { held.push(route); return undefined; }
        return route.fulfill({json: answer(route.request().postDataJSON() || {})});
      }
      if (where === '/api/report-sources') {
        return route.fulfill({json: {runs: [], report_directory: 'fixture'}});
      }
      if (where === '/api/cross-reference') {
        return route.fulfill({json: {available: false, status: 'empty', industry: 'Banking', message: 'stub'}});
      }
      return route.continue();
    });
  }
  async function until(page, predicate, label, attempts = 80) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await page.evaluate(predicate)) return;
      await page.waitForTimeout(250);
    }
    throw new Error(label + ' never happened');
  }
  const canvasState = page => page.evaluate(() => {
    const canvas = document.getElementById('studio-canvas'), style = getComputedStyle(canvas);
    return {busy: canvas.getAttribute('aria-busy'), opacity: style.opacity, pointer: style.pointerEvents,
      mode: document.getElementById('studio-source-mode').value, drawings: canvas.querySelectorAll('svg').length};
  });
  // The Data panel's select, driven the way the app's own "Explore every report worksheet"
  // button drives it: a value and a change event (chart-studio.js).
  const chooseSource = (page, value) => page.evaluate(mode => {
    const select = document.getElementById('studio-source-mode');
    select.value = mode;
    select.dispatchEvent(new Event('change', {bubbles: true}));
  }, value);

  try {
    setup('pick a free port and launch the console on a throwaway --data-root and report directory');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    browser = await withTimeout(
      chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}),
      35000, 'chromium.launch()');
    console.log(`SETUP done - server ready on port ${port}, data root ${dataRoot}`);
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(20000);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await installRoutes(page);
    await page.goto(`http://127.0.0.1:${port}/?industry=Banking#chart-studio`, {waitUntil: 'domcontentloaded'});

    step('a verified query on its way marks the canvas busy');
    for (let wait = 0; wait < 80 && !held.length; wait += 1) await page.waitForTimeout(250);
    assert.ok(held.length > 0, 'the first verified query reached the stub');
    await until(page, () => document.getElementById('studio-canvas').getAttribute('aria-busy') === 'true',
      'the busy mark on the first verified render');
    const pending = await canvasState(page);
    assert.equal(pending.opacity, '0.45', 'a busy canvas is faded (chart-studio.css)');
    pass(`aria-busy="${pending.busy}", opacity ${pending.opacity}, pointer-events ${pending.pointer}`);

    step('switching the Data source leaves no busy mark: the render it made stale clears its own');
    await chooseSource(page, 'worksheet');
    await until(page, () => document.getElementById('studio-source-mode').value === 'worksheet'
      && document.getElementById('studio-canvas').getAttribute('aria-busy') !== 'true',
      'the stale render clearing its busy mark', 40);
    const switched = await canvasState(page);
    assert.notEqual(switched.busy, 'true', 'the canvas is not marked busy');
    assert.equal(switched.opacity, '1', 'the canvas is not faded');
    assert.notEqual(switched.pointer, 'none', 'the canvas takes pointer events');
    pass(`mode ${switched.mode}: aria-busy="${switched.busy}", opacity ${switched.opacity}, `
      + `pointer-events ${switched.pointer}`);

    step('back on verified data, the next answer draws and clears the mark as before');
    holdQueries = false;
    for (const route of held.splice(0)) await route.fulfill({json: answer(route.request().postDataJSON() || {})}).catch(() => {});
    await chooseSource(page, 'contributions');
    await until(page, () => !!document.querySelector('#studio-canvas svg')
      && document.getElementById('studio-canvas').getAttribute('aria-busy') === 'false', 'a verified drawing');
    const drawn = await canvasState(page);
    assert.equal(drawn.opacity, '1');
    pass(`mode ${drawn.mode}: ${drawn.drawings} drawing(s), aria-busy="${drawn.busy}", opacity ${drawn.opacity}`);

    step('none of that raised a page error');
    assert.deepEqual(pageErrors, [], 'no page errors');
    pass('zero page errors');
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
