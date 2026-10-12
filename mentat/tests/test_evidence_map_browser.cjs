/* S8 PAC evidence district map, real-client pass: a pane's Map button opens
 * the district heatmap, Raised vs Spent, the ranked list, the reconciliation
 * line, a layer chip, a district tooltip, clicking a district to filter that
 * pane's rows, Back to panes, and the URL round trip that reopens the same
 * map from a permalink.
 *
 * Spawns the actual `python app/server.py` console under a temp data root, so
 * the import really lands on disk and the permalink really restores from it.
 * Run with MENTAT_NODE_MODULES pointing at a local Playwright installation,
 * from cwd mentat/:  node tests\test_evidence_map_browser.cjs
 * Set MENTAT_EVIDENCE_MAP_SCREENSHOT to a file path to keep a PNG of the
 * finished map; unset, the run writes no image and creates no directory.
 *
 * Every wait is bounded and a top-level watchdog names the step it was on.
 * Harness copied from tests/test_evidence_panes_browser.cjs (S6): freePort(),
 * startServer/stopServer, step()/pass(), HARD_CAP_MS, withTimeout, the
 * MENTAT_NODE_MODULES playwright resolution and temp data and report roots.
 */
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const net = require('node:net');
const modules = process.env.MENTAT_NODE_MODULES
  || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = require(require.resolve('playwright', {paths: [modules]}));

const MENTAT_DIR = path.resolve(__dirname, '..');
// Paul's consoles and the ports other lanes reserve. A scratch console never
// lands on one of these.
const FORBIDDEN_PORTS = new Set([8802, 8806, 8808, 8841, 8940, 8971, 8990, 8991, 8992, 8993]);
const HARD_CAP_MS = 900_000;
const CRYPTO_BOOK = 'Crypto Industry Spending Overview.xlsx';
// Only an explicit MENTAT_EVIDENCE_MAP_SCREENSHOT path makes this run write an
// image. A plain run leaves the tracked receipts under audits/ untouched.
const SCREENSHOT_PATH = process.env.MENTAT_EVIDENCE_MAP_SCREENSHOT || '';

let stepNumber = 0, currentStepLabel = '(none)', stepStartedAt = Date.now();
let serverForWatchdog = null;
let pageForDiagnostics = null;
const watchdog = setTimeout(() => {
  console.error(`WATCHDOG: step ${stepNumber} (${currentStepLabel}) exceeded ${HARD_CAP_MS}ms`);
  try { if (serverForWatchdog && serverForWatchdog.exitCode === null) serverForWatchdog.kill(); } catch { /* best effort */ }
  process.exit(1);
}, HARD_CAP_MS);

function step(label) {
  stepNumber += 1; currentStepLabel = label; stepStartedAt = Date.now();
  console.log(`STEP ${stepNumber}: ${label}`);
}
function pass(detail) {
  console.log(`PASS step ${stepNumber} (${((Date.now() - stepStartedAt) / 1000).toFixed(1)}s) - ${detail}`);
}

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} exceeded its ${ms}ms bound`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function freePort() {
  const once = () => new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
  const pick = async () => { for (;;) { const port = await once(); if (!FORBIDDEN_PORTS.has(port)) return port; } };
  return withTimeout(pick(), 10000, 'freePort()');
}

function startServer(port, dataRoot, reportRoot, extraEnv) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.MENTAT_PYTHON || 'python',
      ['app/server.py', '--port', String(port), '--data-root', dataRoot],
      {cwd: MENTAT_DIR, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
       // Without a report directory of its own this console resolves the configured
       // one - Paul's published reports - and chdir()s into it on every run.
       env: {...process.env, ...(extraEnv || {}), MENTAT_REPORT_DIRECTORY: reportRoot}});
    const stderr = [];
    let stdout = '', settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error(`server on ${port} printed no ready line in 30s; stderr: ${stderr.join('')}`));
    }, 30000);
    child.stdout.on('data', chunk => {
      stdout += chunk.toString();
      if (!settled && new RegExp(`Mentat app: http://127\\.0\\.0\\.1:${port}\\b`).test(stdout)) {
        settled = true; clearTimeout(timer); resolve(child);
      }
    });
    child.stderr.on('data', chunk => stderr.push(chunk.toString()));
    child.on('exit', code => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      reject(new Error(`server on ${port} exited ${code}; stderr: ${stderr.join('')}`));
    });
    child.on('error', error => { if (!settled) { settled = true; clearTimeout(timer); reject(error); } });
  });
}

function stopServer(child) {
  return withTimeout(new Promise(resolve => {
    if (!child || child.exitCode !== null) { resolve(); return; }
    child.once('exit', () => resolve());
    child.kill();
  }), 10000, 'stopServer()').catch(error => console.error(error.message));
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

// One industry-workbook fixture, built the same way the S6 fixture is
// (openpyxl, HEAD row, two_year_transaction_period decides the sheet), plus a
// State and a ZIP column so evidence_geo.py's STATE_FIELDS/ZIP_FIELDS lookup
// has contributor geography to place on the map. Real 2023-2024 (118th) ZIP
// crosswalk facts used below (checked against app/geo/zcta_cd118.json):
// 94607 (Oakland) -> CA-12 at 100% share, 94105 (SF) -> CA-11 at 100% share.
const FIXTURE_SCRIPT = `
import sys
import openpyxl

target = sys.argv[1]
HEAD = ['committee_id', 'committee_name', 'amount', 'date', 'two_year_transaction_period',
        'record_type', 'recipient_category', 'include_in_analysis', 'inclusion_reason',
        'contributor_name', 'street_1', 'city', 'state', 'zip']

def book(rows, name, sheets=('2023-2024', '2025-2026')):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for sheet_name in sheets:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(HEAD)
        for row in rows:
            if row[4] in sheet_name:
                sheet.append(list(row))
    workbook.save(target + '/' + name)

CRYPTO = [
    ('C00804179', 'COINBASE INNOVATION PAC', 383197, '2024-05-05', '2024', 'CONTRIBUTION', 'Company PAC', 'Yes', '', 'CHOI, EMILIE', '1 Frank H Ogawa Plaza', 'Oakland', 'CA', '94607'),
    ('C00804179', 'COINBASE INNOVATION PAC', 18884, '2024-05-06', '2024', 'CONTRIBUTION', 'Company PAC', 'Yes', '', 'POLLAK, JESSE', '100 Pine St', 'San Francisco', 'CA', '94105'),
    ('C00804179', 'COINBASE INNOVATION PAC', 5000, '2024-05-07', '2024', 'CONTRIBUTION', 'Company PAC', 'Yes', '', 'NO ZIP PERSON', '', '', 'NY', ''),
    ('C00804179', 'COINBASE INNOVATION PAC', 1000, '2026-01-05', '2026', 'CONTRIBUTION', 'Company PAC', 'Yes', '', 'LATER PERSON', '', '', 'CA', '94607'),
    ('C00835959', 'FAIRSHAKE', 2500, '2024-06-01', '2024', 'CONTRIBUTION', 'Super PAC', 'Yes', '', 'SECOND PAC DONOR', '', '', 'CA', '94607'),
    ('C00835959', 'FAIRSHAKE', 3500, '2026-02-02', '2026', 'CONTRIBUTION', 'Super PAC', 'Yes', '', 'SECOND PAC LATER', '', '', 'CA', '94105'),
]
book(CRYPTO, '${CRYPTO_BOOK}')
`;

// S8b: the geocoder, stubbed. The console is started with MENTAT_GEOCODE_URL
// pointing here, so no contributor address leaves this machine during a test
// and the answer is deterministic. The body is a multipart upload of
// `id,street,city,state,zip` rows; this reads the ids and the streets out of
// it and answers in the Census's own CSV shape - a MATCH for the Pine Street
// row (state 06, county 075, tract 011700, which the shipped
// geo/tract_cd118.json places in CA-11) and a No_Match for everything else,
// deliberately OUT OF ORDER, because the real service answers out of order.
const http = require('node:http');

function stubRows(body) {
  const part = body.split(/\r?\n\r?\n/).slice(1).join('\n\n');
  return part.split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line && !line.startsWith('--') && line.split(',').length >= 5)
    .map(line => {
      const fields = line.split(',');
      return {id: fields[0], street: fields[1] || '', line};
    });
}

function stubAnswer(rows) {
  const answered = rows.map(row => /PINE/i.test(row.street)
    ? `"${row.id}","${row.line}","Match","Exact","100 PINE ST, SAN FRANCISCO, CA, 94105",`
      + '"-122.39867061781,37.792455266252","192281482","R","06","075","011700","2014"'
    : `"${row.id}","${row.line}","No_Match"`);
  return answered.reverse().join('\n') + '\n';
}

function startStubGeocoder() {
  return new Promise((resolve, reject) => {
    const calls = [];
    const stub = http.createServer((request, response) => {
      const chunks = [];
      request.on('data', chunk => chunks.push(chunk));
      request.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        const rows = stubRows(body);
        calls.push({rows: rows.length, body});
        response.writeHead(200, {'Content-Type': 'text/plain'});
        response.end(stubAnswer(rows));
      });
    });
    stub.on('error', reject);
    stub.listen(0, '127.0.0.1', () => resolve({stub, calls,
      url: `http://127.0.0.1:${stub.address().port}/geographies/addressbatch`}));
  });
}

(async () => {
  let browser = null, server = null, failed = false, stubbed = null;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-s8-data-'));
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-s8-fixtures-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-s8-reports-'));
  try {
    step('build one industry workbook fixture with contributor ZIP and state');
    execFileSync(process.env.MENTAT_PYTHON || 'python', ['-c', FIXTURE_SCRIPT, fixtureDir],
      {cwd: MENTAT_DIR, stdio: 'pipe'});
    assert.ok(fs.existsSync(path.join(fixtureDir, CRYPTO_BOOK)));
    pass(`fixture written under ${fixtureDir}`);

    step('start the stub geocoder, then a scratch console pointed at it');
    stubbed = await startStubGeocoder();
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot, {MENTAT_GEOCODE_URL: stubbed.url});
    serverForWatchdog = server;
    const origin = `http://127.0.0.1:${port}`;
    // The console reads this suite's own report directory, not the configured one.
    const reportsSeen = await withTimeout(new Promise((resolve, reject) => {
      const ask = http.get({host: '127.0.0.1', port: port, path: '/api/report-sources',
                            headers: {Host: '127.0.0.1:' + port}}, answer => {
        let body = '';
        answer.on('data', chunk => { body += chunk; });
        answer.on('end', () => { try { resolve(JSON.parse(body)); } catch (error) { reject(error); } });
      });
      ask.on('error', reject);
    }), 10000, 'GET /api/report-sources');
    assert.equal(path.resolve(String(reportsSeen.report_directory || '')).toLowerCase(),
      path.resolve(reportRoot).toLowerCase(),
      `the console must read this suite's own report directory, got "${reportsSeen.report_directory}"`);
    pass(`console ready on ${port}, data root ${dataRoot}, report directory ${reportRoot}`);

    step('launch headless Chrome and open the PAC evidence tab');
    browser = await withTimeout(chromium.launch({headless: true,
      channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}), 35000, 'chromium.launch()');
    const page = await browser.newPage({viewport: {width: 1600, height: 1200}});
    page.setDefaultTimeout(20000);
    const errors = [], consoleErrors = [];
    pageForDiagnostics = page;
    page.on('pageerror', error => {
      errors.push(error.message);
      console.error(`PAGEERROR at step ${stepNumber}: ${error.message}`);
    });
    page.on('console', message => {
      if (message.type() !== 'error') return;
      consoleErrors.push(message.text());
      console.error(`CONSOLE ERROR at step ${stepNumber}: ${message.text()}`);
    });
    const status = async () => (await page.textContent('#evidence-status')).trim();
    // Polled with evaluate() rather than waitForFunction(), matching S6: a
    // raf-polled predicate did not resolve reliably on this host.
    const until = async (predicate, timeout, what) => {
      const deadline = Date.now() + timeout;
      for (;;) {
        if (await page.evaluate(predicate.fn, predicate.arg)) return;
        if (Date.now() > deadline) {
          const seen = await page.textContent('#evidence-status').catch(() => '<unreadable>');
          throw new Error(`${what} did not happen within ${timeout}ms; `
            + `#evidence-status was ${JSON.stringify((seen || '').trim())}`);
        }
        await new Promise(resolve => setTimeout(resolve, 200));
      }
    };
    const waitStatus = (pattern, timeout = 60000) => until(
      {fn: source => new RegExp(source).test(((document.getElementById('evidence-status') || {}).textContent || '').trim()),
       arg: pattern.source}, timeout, `status matching ${pattern}`);
    const waitPanes = (count, timeout = 60000) => until(
      {fn: wanted => document.querySelectorAll('#evidence-panes [data-pane]').length === wanted
        && document.querySelectorAll('#evidence-panes .pane-message').length === 0,
       arg: count}, timeout, `${count} loaded pane(s)`);
    const waitFor = (fn, arg, timeout, what) => until({fn, arg}, timeout, what);
    const openDrawer = async id => {
      const drawer = id === 'evidence-import-open' ? 'evidence-import-drawer' : 'evidence-snapshot-drawer';
      if (await page.isVisible('#' + drawer)) return;
      await page.click('#' + id);
      await page.waitForSelector('#' + drawer + ':not([hidden])');
    };

    await page.goto(origin + '/');
    await page.click('#tab-evidence');
    await waitStatus(/Nothing imported yet|snapshot/);
    pass('evidence tab open');

    step('import the Crypto fixture through the path box');
    await openDrawer('evidence-import-open');
    await page.fill('#evidence-path', path.join(fixtureDir, CRYPTO_BOOK));
    await page.click('#evidence-path-read');
    await waitStatus(/Crypto|PACs in file/, 180000);
    pass(`imported: ${JSON.stringify(await status())}`);

    step('choose the PAC and cycle 2024 in the scope bar, and add the pane');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length >= 1, null, 20000, 'a page condition');
    await waitFor(() => [...document.querySelectorAll('#evidence-cycle option')].some(option => option.value === '2024'),
      null, 20000, 'a page condition');
    await page.selectOption('#evidence-cycle', '2024');
    // Build 06: the pane opens from its tick in the picker rail (#evidence-add is hidden).
    await page.check('#evidence-list input[type=checkbox][value="C00804179"]');
    await waitPanes(1);
    const paneSel = '#evidence-panes [data-pane=a]';
    assert.equal((await page.textContent(`${paneSel} h2`)).trim(), 'COINBASE INNOVATION PAC');
    pass('pane A open for COINBASE INNOVATION PAC, cycle 2024');

    step('the pane header carries a visible .pane-map button labelled Map');
    assert.equal(await page.locator(`${paneSel} .pane-map`).count(), 1);
    assert.equal(await page.isVisible(`${paneSel} .pane-map`), true);
    assert.equal((await page.textContent(`${paneSel} .pane-map`)).trim(), 'Map');
    pass('.pane-map exists, is visible, and reads "Map"');

    step('clicking Map hides the pane grid and shows the map view');
    await page.click(`${paneSel} .pane-map`);
    await page.waitForSelector('#evidence-map:not([hidden])');
    assert.equal(await page.isVisible('#evidence-panes'), false, '#evidence-panes should hide once the map opens');
    assert.equal(await page.isVisible('#evidence-map'), true, '#evidence-map should show once opened');
    pass('#evidence-panes hidden, #evidence-map shown');

    step('the map header carries the two direction buttons, the cycle select and Back');
    assert.equal(await page.locator('#evidence-map .map-direction').count(), 2);
    const directionTexts = await page.locator('#evidence-map .map-direction').allTextContents();
    assert.ok(directionTexts.some(text => /Raised/.test(text) && /where it came from/.test(text)), JSON.stringify(directionTexts));
    assert.ok(directionTexts.some(text => /Spent/.test(text) && /where it went/.test(text)), JSON.stringify(directionTexts));
    assert.equal(await page.locator('#evidence-map-cycle').count(), 1);
    assert.equal(await page.locator('#evidence-map-back').count(), 1);
    pass(`direction buttons ${JSON.stringify(directionTexts)}`);

    step('the map opens on State, as boards 6 and M13 draw it; choose Congressional district');
    await waitFor(() => !!document.querySelector('#evidence-map .map-layer.on'), null, 20000, 'a pressed layer');
    assert.equal(await page.getAttribute('#evidence-map .map-layer[data-map-layer="state"]', 'aria-pressed'), 'true',
      'the map opens on State');
    await page.click('#evidence-map .map-layer[data-map-layer="district"]');
    await waitFor(() => document.querySelector('#evidence-map .map-layer[data-map-layer="district"]').getAttribute('aria-pressed') === 'true',
      null, 20000, 'Congressional district pressed');
    pass('State pressed on opening; Congressional district chosen');

    step('click Raised: the heatmap paints and the ranked list carries CA-12');
    await page.click('#evidence-map .map-direction[data-direction="raised"]');
    await page.waitForSelector('#evidence-map .map-canvas svg, #evidence-map svg.map-canvas', {timeout: 30000});
    await page.waitForSelector('#evidence-map .map-list', {timeout: 30000});
    await waitFor(() => document.querySelectorAll('#evidence-map path.map-district').length > 0,
      null, 20000, 'district paths to render');
    const painted = await page.evaluate(() => [...document.querySelectorAll('#evidence-map path.map-district')]
      .some(node => {
        const fill = getComputedStyle(node).fill;
        return !!fill && fill !== 'none' && fill !== 'transparent' && fill !== 'rgba(0, 0, 0, 0)';
      }));
    assert.ok(painted, 'no path.map-district has a painted computed fill');
    const listText = await page.textContent('#evidence-map .map-list');
    assert.ok(/CA-12/.test(listText), listText);
    pass('at least one district is painted; CA-12 is present in the ranked list');

    step('the reconciliation line names the located and reported totals');
    const reconcileText = (await page.textContent('#evidence-map .map-reconcile')).trim();
    assert.ok(reconcileText.length > 0, 'empty .map-reconcile');
    assert.ok(/located/.test(reconcileText), reconcileText);
    assert.ok(/reported/.test(reconcileText), reconcileText);
    // Fix r2 (TC-07): on screen the line reads "located $X · unlocated $Y"
    // (boards 6 and M13); the reported total is in its title and its
    // screen-reader sentence, which say the same thing.
    const reconcileParts = await page.evaluate(() => {
      const line = document.querySelector('#evidence-map .map-reconcile');
      const shown = line.querySelector('.map-reconcile-figures');
      return {shown: shown ? shown.textContent.trim() : null, visible: !!shown && shown.getClientRects().length > 0,
        title: line.title, spoken: (line.querySelector('.map-reconcile-sentence') || {}).textContent};
    });
    assert.ok(reconcileParts.visible && /^located \$[\d,]+ · unlocated \$[\d,]+/.test(reconcileParts.shown), JSON.stringify(reconcileParts));
    assert.ok(/located/.test(reconcileParts.title) && /reported/.test(reconcileParts.title), JSON.stringify(reconcileParts));
    assert.equal((reconcileParts.spoken || '').trim(), reconcileParts.title, 'screen readers must hear the whole reconciliation');
    pass(`reconcile: ${JSON.stringify(reconcileText)}`);

    step('toggling a layer chip flips aria-pressed and the off class, both ways');
    // Fix round 1 (R06-05): the kind toggles are a group in the map's legend
    // card, not a line under the toolbar.
    const layerSel = '#evidence-map .map-legend-card .layer';
    assert.equal(await page.locator(layerSel).count(), 1, 'expected exactly one layer chip (kind=contributors)');
    const layerClasses = async () => ((await page.getAttribute(layerSel, 'class')) || '').split(/\s+/).filter(Boolean);
    const pressedBefore = await page.getAttribute(layerSel, 'aria-pressed');
    assert.equal(pressedBefore, 'true', `expected the layer chip to start pressed(on): ${pressedBefore}`);
    assert.equal((await layerClasses()).includes('off'), false, 'expected the layer chip to start without the off class');
    await page.click(layerSel);
    await waitFor(previous => document.querySelector('#evidence-map .map-legend-card .layer').getAttribute('aria-pressed') !== previous,
      pressedBefore, 20000, 'the layer chip aria-pressed to flip off');
    const pressedAfter = await page.getAttribute(layerSel, 'aria-pressed');
    assert.equal(pressedAfter, 'false', `aria-pressed did not flip to false: ${pressedAfter}`);
    assert.equal((await layerClasses()).includes('off'), true, 'off class was not added');
    pass(`layer toggled off: aria-pressed ${pressedBefore} -> ${pressedAfter}`);
    // Restore it before reading the tooltip and clicking a district: toggling
    // the only kind off drops it from the heat entirely (heatFrom skips
    // kinds that are off), which would blank CA-12's tooltip below.
    await page.click(layerSel);
    await waitFor(previous => document.querySelector('#evidence-map .map-legend-card .layer').getAttribute('aria-pressed') !== previous,
      pressedAfter, 20000, 'the layer chip aria-pressed to flip back on');
    const pressedRestored = await page.getAttribute(layerSel, 'aria-pressed');
    assert.equal(pressedRestored, 'true', `aria-pressed did not flip back to true: ${pressedRestored}`);
    assert.equal((await layerClasses()).includes('off'), false, 'off class was not removed on restore');
    pass('layer chip restored to on');

    step('a CA-12 tooltip names the district and a dollar amount');
    const title = await page.evaluate(() => {
      const node = document.querySelector('#evidence-map path.map-district[data-key="CA-12"]');
      const titleNode = node ? node.querySelector('title') : null;
      return titleNode ? titleNode.textContent : null;
    });
    assert.ok(title, 'no <title> child on path.map-district[data-key="CA-12"]');
    assert.ok(/CA-12/.test(title), title);
    assert.ok(/\$[\d,]+/.test(title), title);
    pass(`tooltip: ${JSON.stringify(title)}`);

    step('click the CA-12 district path: it gains class "chosen"');
    // Dispatched rather than Playwright's geometric click: the app reads the
    // event only through delegation (`event.target.closest('[data-district]')`
    // on #evidence-map), so a real bubbling click on the exact node exercises
    // the same handler without depending on where an irregular polygon's
    // bounding-box centre happens to fall.
    await page.evaluate(() => {
      const node = document.querySelector('#evidence-map path.map-district[data-key="CA-12"]');
      if (!node) throw new Error('no path.map-district[data-key="CA-12"] found to click');
      node.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
    });
    await waitFor(() => {
      const node = document.querySelector('#evidence-map .map-canvas [data-district="CA-12"]');
      return !!node && node.classList.contains('chosen');
    }, null, 20000, 'the CA-12 district path to gain class "chosen"');
    pass('CA-12 district path carries class "chosen"');

    step('Back to panes: the map closes and the pane shows the CA-12 district filter');
    await waitFor(() => {
      const ranked = document.querySelector('#evidence-map');
      return ranked && /Donors in CA-12/.test(ranked.textContent) && /383,197/.test(ranked.textContent);
    }, null, 60000, 'the independent map source-page read to finish');
    // The map's source-page response does not populate the pane drawer. Its
    // first independent load must be retryable rather than leave Reading rows
    // forever. Throw before fetch so the intended failure emits no browser
    // network error and the suite's zero-console-error check remains meaningful.
    await page.evaluate(() => {
      window.mapTestOriginalApi = window.api;
      window.api = async (...args) => {
        const url = new URL(args[0], location.origin);
        if (url.pathname === '/api/evidence-csv' && !url.searchParams.has('summary')) {
          throw new Error('Synthetic pane source failure');
        }
        return window.mapTestOriginalApi(...args);
      };
    });
    await page.click('#evidence-map-back');
    await page.waitForSelector('#evidence-panes:not([hidden])', {timeout: 20000});
    assert.equal(await page.isVisible('#evidence-map'), false, '#evidence-map should be hidden after Back to panes');
    assert.equal(await page.isVisible('#evidence-panes'), true, '#evidence-panes should be visible after Back to panes');
    await page.waitForSelector(`${paneSel} .pane-rows-retry`, {timeout: 20000});
    assert.match(await page.textContent(`${paneSel} .pane-drawer`), /Synthetic pane source failure/);
    await page.evaluate(() => { window.api = window.mapTestOriginalApi; delete window.mapTestOriginalApi; });
    await page.click(`${paneSel} .pane-rows-retry`);
    await page.waitForSelector(`${paneSel} .pane-district-filter`, {timeout: 20000});
    const filterText = (await page.textContent(`${paneSel} .pane-district-filter`)).trim();
    assert.ok(/CA-12/.test(filterText), filterText);
    assert.equal(await page.locator(`${paneSel} .pane-drawer tbody tr`).count(), 1,
      'The returned pane drawer keeps the district selected on the map.');
    pass(`district filter reads ${JSON.stringify(filterText)}`);

    step('Clear district restores every source row instead of keeping a hidden geographic filter');
    await page.click(`${paneSel} .pane-district-clear`);
    await waitFor(() => document.querySelectorAll('#evidence-panes [data-pane=a] .pane-drawer tbody tr').length === 3,
      null, 20000, 'all three source rows after clearing the selected district');
    assert.equal(await page.locator(`${paneSel} .pane-district-filter`).count(), 0);
    pass('The visible filter and its underlying focus both clear; all three source rows return.');

    step('URL round trip: a.map=raised restores the map after a fresh navigation');
    await page.click(`${paneSel} .pane-map`);
    await page.waitForSelector('#evidence-map:not([hidden])', {timeout: 20000});
    const mapUrl = page.url();
    // a.map is written last among the pane's own keys, so in practice it is
    // followed by the #hash, not another &pair; accept either terminator (and
    // end-of-string, in case a future key is ever appended after it).
    assert.ok(/[?&]a\.map=raised(&|#|$)/.test(mapUrl), mapUrl);
    await page.goto(mapUrl);
    await page.waitForSelector('#evidence-map:not([hidden])', {timeout: 60000});
    assert.equal(await page.isVisible('#evidence-map'), true, '#evidence-map should be visible after reload from the permalink');
    assert.equal(await page.isVisible('#evidence-panes'), false, '#evidence-panes should stay hidden while the map view is restored');
    const raisedPressedAfterReload = await page.getAttribute(
      '#evidence-map .map-direction[data-direction="raised"]', 'aria-pressed');
    assert.equal(raisedPressedAfterReload, 'true', `direction was not restored as raised: ${raisedPressedAfterReload}`);
    pass(`permalink ${mapUrl} restored the map in the raised direction`);

    step('RULING A: the map cycle select offers concrete cycles only, never All cycles');
    const cycleOptions = await page.evaluate(() =>
      [...document.querySelectorAll('#evidence-map-cycle option')]
        .map(option => ({value: option.value, text: option.textContent.trim()})));
    assert.ok(cycleOptions.length >= 1, JSON.stringify(cycleOptions));
    assert.equal(cycleOptions.filter(option => option.value === '').length, 0,
      `the map cycle select still offers a blank cycle: ${JSON.stringify(cycleOptions)}`);
    assert.equal(cycleOptions.filter(option => /All cycles/i.test(option.text)).length, 0,
      `the map cycle select still offers "All cycles": ${JSON.stringify(cycleOptions)}`);
    assert.equal(await page.inputValue('#evidence-map-cycle'), '2024');
    pass(`cycle options ${JSON.stringify(cycleOptions.map(option => option.value))}, selected 2024`);

    step('the geocoding offer, its Start and its privacy sentence are under the Raised list');
    await page.waitForSelector('#evidence-map .map-geocode', {timeout: 20000});
    const offerText = (await page.textContent('#evidence-map .map-geocode-offer')).trim();
    assert.equal(offerText,
      'Contributor geography is by ZIP. Geocode street addresses for exact districts.', offerText);
    const privacyText = (await page.textContent('#evidence-map .map-geocode-privacy')).trim();
    assert.ok(/street address/i.test(privacyText), privacyText);
    assert.ok(/No name, amount, employer or occupation is sent/.test(privacyText), privacyText);
    assert.ok(privacyText.includes(new URL(stubbed.url).host),
      `the privacy sentence must name the host actually called: ${privacyText}`);
    assert.equal(await page.locator('#evidence-map-geocode-start').count(), 1);
    assert.equal(await page.isDisabled('#evidence-map-geocode-start'), false,
      'Start should be live while rows are still pending');
    pass(`offer + Start + privacy naming ${new URL(stubbed.url).host}`);

    step('Start: the stub is called and the footnote becomes the matched/unmatched counts');
    await page.click('#evidence-map-geocode-start');
    await waitFor(() => {
      const node = document.querySelector('#evidence-map .map-geocode-offer');
      return !!node && /rows matched to a street address/.test(node.textContent);
    }, null, 60000, 'the geocoding footnote to become the matched counts');
    const matchedText = (await page.textContent('#evidence-map .map-geocode-offer')).trim();
    assert.match(matchedText, /^1 of 3 rows matched to a street address; the rest stay by ZIP$/,
      matchedText);
    assert.ok(stubbed.calls.length >= 1, 'the stub geocoder was never called');
    assert.equal(stubbed.calls[0].rows, 2, `expected 2 addresses uploaded: ${stubbed.calls[0].rows}`);
    for (const secret of ['CHOI', 'POLLAK', '383197', '18884']) {
      assert.ok(!stubbed.calls[0].body.includes(secret),
        `${secret} reached the geocoder; only street, city, state and ZIP may be sent`);
    }
    assert.equal(await page.isDisabled('#evidence-map-geocode-start'), true,
      'Start should be spent once no row is pending');
    pass(`footnote ${JSON.stringify(matchedText)}; upload carried 2 addresses and no name or amount`);

    step('RULING A: a pane on All cycles opens the map on the newest cycle and says so once');
    await page.click('#evidence-map-back');
    await page.waitForSelector('#evidence-panes:not([hidden])', {timeout: 20000});
    // A SECOND committee, so this is a fresh pane key with no remembered map
    // state: reusing pane A's key would carry its 2024 cycle over and the
    // fallback under test would never run.
    await page.selectOption('#evidence-cycle', '');
    // Build 06: the pane opens from its tick in the picker rail (#evidence-add is hidden).
    await page.check('#evidence-list input[type=checkbox][value="C00835959"]');
    await waitPanes(2);
    await page.click('#evidence-panes [data-pane=b] .pane-map');
    await page.waitForSelector('#evidence-map:not([hidden])', {timeout: 20000});
    await page.waitForSelector('#evidence-map .map-note', {timeout: 20000});
    const noteText = (await page.textContent('#evidence-map .map-note')).trim();
    assert.match(noteText, /^Showing 2025.2026; a map covers one Congress\.$/, noteText);
    assert.equal(await page.inputValue('#evidence-map-cycle'), '2026');
    assert.equal(await page.locator('#evidence-map .map-note').count(), 1,
      'the note must be said once, not on every render');
    // Fix round 1 (R06-05): no line between the toolbar and the map; the note
    // is screen-reader text and rides on the cycle chip's title.
    assert.equal(await page.getAttribute('#evidence-map-cycle', 'title'), noteText);
    assert.ok(await page.locator('#evidence-map .map-note').evaluate(node => node.classList.contains('sr-only')));
    const paneBCycles = await page.evaluate(() =>
      [...document.querySelectorAll('#evidence-map-cycle option')].map(option => option.value));
    assert.equal(paneBCycles.includes(''), false, JSON.stringify(paneBCycles));
    pass(`note ${JSON.stringify(noteText)}; cycle select resolved to 2026`);

    step('back to pane A');
    await page.click('#evidence-map-back');
    await page.waitForSelector('#evidence-panes:not([hidden])', {timeout: 20000});
    await page.click(`${paneSel} .pane-map`);
    await page.waitForSelector('#evidence-map:not([hidden])', {timeout: 20000});
    pass('pane A map reopened');

    step('two cycles: zoom, Reset view and Find move both maps together (fix round 1, R06-01)');
    await page.selectOption('#evidence-map-compare', '2026');
    await waitFor(() => document.querySelectorAll('#evidence-map svg.map-canvas').length === 2
      && !!document.querySelector('#evidence-map.comparing'), null, 60000, 'the second map');
    const pair = () => page.evaluate(() => {
      const [a, b] = document.querySelectorAll('#evidence-map .map-canvas-host');
      const api = window.MentatEvidenceMap, round = view => [view.scale, view.x, view.y].map(value => value.toFixed(3));
      return {a: round(api.view(a)), b: round(api.view(b)), flying: api.animating(a) || api.animating(b)};
    });
    const landed = async () => {
      await page.waitForTimeout(350);
      await waitFor(() => {
        const [a, b] = document.querySelectorAll('#evidence-map .map-canvas-host');
        return !window.MentatEvidenceMap.animating(a) && !window.MentatEvidenceMap.animating(b);
      }, null, 20000, 'both maps to settle');
      return pair();
    };
    await page.click('#evidence-map-zoom-in');
    await page.click('#evidence-map-zoom-in');
    const zoomedPair = await landed();
    assert.ok(Number(zoomedPair.a[0]) > 1.5, JSON.stringify(zoomedPair));
    assert.deepEqual(zoomedPair.b, zoomedPair.a, `the second map did not follow the zoom: ${JSON.stringify(zoomedPair)}`);
    await page.click('#evidence-map-reset');
    const resetPair = await landed();
    assert.deepEqual(resetPair.a, ['1.000', '0.000', '0.000'], `Reset view left the first map zoomed: ${JSON.stringify(resetPair)}`);
    assert.deepEqual(resetPair.b, resetPair.a, `Reset view left the second map behind: ${JSON.stringify(resetPair)}`);
    await page.fill('#evidence-map-search', 'TX');
    await page.press('#evidence-map-search', 'Enter');
    const foundPair = await landed();
    assert.ok(Number(foundPair.a[0]) > 1.5, `Find did not fly the first map: ${JSON.stringify(foundPair)}`);
    assert.deepEqual(foundPair.b, foundPair.a, `Find did not fly the second map: ${JSON.stringify(foundPair)}`);
    await page.selectOption('#evidence-map-compare', '');
    await waitFor(() => !document.querySelector('#evidence-map.comparing')
      && document.querySelectorAll('#evidence-map svg.map-canvas').length === 1, null, 30000, 'one map again');
    pass(`zoom ${JSON.stringify(zoomedPair)}; reset ${JSON.stringify(resetPair)}; find ${JSON.stringify(foundPair)}`);

    // r5, audit men-b F2: approved board 6 ends the toolbar row with "^ Collapse",
    // which folds the enlarged map back into its pane. M13's compare row draws
    // none, so there the control is the chevron alone, still named Collapse.
    const toolbar = () => page.evaluate(() => {
      const box = el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, mid: r.top + r.height / 2, width: r.width}; };
      const shown = [...document.querySelectorAll('#evidence-map .map-toolbar :is(button,select,input)')].filter(el => el.getClientRects().length);
      const input = document.querySelector('#evidence-map-search'), style = getComputedStyle(input);
      const ctx = document.createElement('canvas').getContext('2d');
      ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      const label = document.querySelector('#evidence-map-collapse .map-collapse-label');
      return {ids: shown.map(el => el.id).filter(Boolean), last: shown.at(-1).id, mids: shown.map(el => box(el).mid),
        collapse: box(document.querySelector('#evidence-map-collapse')), reset: box(document.querySelector('#evidence-map-reset')),
        inCard: !!document.querySelector('#evidence-map .map-toolbar-card #evidence-map-collapse'),
        labelWidth: label.getBoundingClientRect().width, icon: !!document.querySelector('#evidence-map-collapse svg.map-button-icon'),
        placeholderNeeds: Math.ceil(ctx.measureText(input.placeholder).width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)),
        searchWidth: input.clientWidth};
    });
    const collapseByName = () => page.locator('#evidence-map').getByRole('button', {name: 'Collapse', exact: true}).count();
    const oneRowWithCollapseLast = (row, where) => {
      assert.equal(row.inCard, true, `${where}: Collapse sits in the toolbar card`);
      assert.equal(row.last, 'evidence-map-collapse', `${where}: Collapse is the row's last control: ${JSON.stringify(row.ids)}`);
      assert.ok(row.collapse.left >= row.reset.right, `${where}: Collapse follows Reset view: ${JSON.stringify(row)}`);
      assert.ok(Math.max(...row.mids) - Math.min(...row.mids) < 12, `${where}: the toolbar stays one row: ${JSON.stringify(row.mids)}`);
      assert.ok(row.placeholderNeeds <= row.searchWidth, `${where}: "Find a state, district or ZIP" shows whole: ${row.placeholderNeeds} > ${row.searchWidth}`);
      assert.equal(row.icon, true, `${where}: Collapse carries its chevron`);
    };

    step('board 6 ends the toolbar row with Collapse; M13 keeps it as the chevron (r5, audit men-b F2)');
    await waitFor(() => !document.querySelector('#evidence-map.comparing') && !!document.querySelector('#evidence-map-collapse'),
      null, 20000, 'the single map with its toolbar');
    const single = await toolbar();
    oneRowWithCollapseLast(single, 'one map at 1600');
    assert.ok(single.labelWidth > 40, `one map at 1600 shows the word Collapse: ${single.labelWidth}`);
    assert.equal((await page.textContent('#evidence-map-collapse')).trim(), 'Collapse');
    assert.equal(await collapseByName(), 1, 'exactly one control in the map view is named Collapse');
    assert.equal(await page.getAttribute('#evidence-map-enlarge', 'aria-label'), 'Enlarge the map');
    await page.click('#evidence-map-enlarge');
    await waitFor(() => document.querySelector('#evidence-map-enlarge').getAttribute('aria-label') === 'Shrink the map',
      null, 20000, 'the enlarged map icon to read Shrink');
    assert.equal(await collapseByName(), 1, 'enlarged, the map-card icon is Shrink, so Collapse is still the toolbar\'s alone');
    await page.click('#evidence-map-enlarge');
    await waitFor(() => document.querySelector('#evidence-map-enlarge').getAttribute('aria-label') === 'Enlarge the map',
      null, 20000, 'the map icon to read Enlarge again');
    await page.selectOption('#evidence-map-compare', '2026');
    await waitFor(() => document.querySelectorAll('#evidence-map svg.map-canvas').length === 2
      && !!document.querySelector('#evidence-map.comparing'), null, 60000, 'the second map');
    const compared = await toolbar();
    oneRowWithCollapseLast(compared, 'two maps at 1600');
    assert.ok(compared.labelWidth <= 1 && compared.collapse.width <= 40, `the compare row shows the chevron alone: ${JSON.stringify(compared)}`);
    assert.equal(await collapseByName(), 1, 'the chevron is still named Collapse');
    await page.selectOption('#evidence-map-compare', '');
    await waitFor(() => !document.querySelector('#evidence-map.comparing')
      && document.querySelectorAll('#evidence-map svg.map-canvas').length === 1, null, 30000, 'one map again');
    pass(`one map: Collapse ${Math.round(single.collapse.width)}px after Reset view, search ${single.searchWidth}px >= ${single.placeholderNeeds}px; two maps: chevron ${Math.round(compared.collapse.width)}px, search ${compared.searchWidth}px`);

    step('Collapse folds the map back into its pane and lands on that pane\'s own map');
    await page.click('#evidence-map-collapse');
    await page.waitForSelector('#evidence-panes:not([hidden])', {timeout: 20000});
    assert.equal(await page.isVisible('#evidence-map'), false, 'Collapse hides the enlarged map');
    const landedOn = await page.evaluate(() => ({pane: document.activeElement?.closest('[data-pane]')?.dataset.pane || '',
      thumb: document.activeElement?.classList.contains('pane-thumb-canvas') || false, url: location.href}));
    assert.deepEqual([landedOn.pane, landedOn.thumb], ['a', true], `focus lands on pane A's map: ${JSON.stringify(landedOn)}`);
    assert.doesNotMatch(landedOn.url, /[?&]a\.map=/, 'the permalink no longer opens the map');
    await page.click(`${paneSel} .pane-map`);
    await page.waitForSelector('#evidence-map:not([hidden])', {timeout: 20000});
    pass(`focus on pane ${landedOn.pane}'s map thumbnail; map reopened`);

    step('the map holds at 1440x900');
    await page.setViewportSize({width: 1440, height: 900});
    await page.waitForSelector('#evidence-map svg.map-canvas', {timeout: 20000});
    const narrow = await toolbar();
    oneRowWithCollapseLast({...narrow, placeholderNeeds: 0}, 'one map at 1440');
    assert.ok(narrow.labelWidth <= 1 && narrow.collapse.width <= 40, `1440 shows the chevron alone: ${JSON.stringify(narrow)}`);
    assert.equal(await collapseByName(), 1, 'at 1440 the chevron is still named Collapse');
    await page.selectOption('#evidence-map-compare', '2026');
    await waitFor(() => document.querySelectorAll('#evidence-map svg.map-canvas').length === 2
      && !!document.querySelector('#evidence-map.comparing'), null, 60000, 'the second map at 1440');
    oneRowWithCollapseLast({...(await toolbar()), placeholderNeeds: 0}, 'two maps at 1440');
    await page.selectOption('#evidence-map-compare', '');
    await waitFor(() => !document.querySelector('#evidence-map.comparing')
      && document.querySelectorAll('#evidence-map svg.map-canvas').length === 1, null, 30000, 'one map again at 1440');
    if (SCREENSHOT_PATH) {
      const shotDir = path.dirname(path.resolve(SCREENSHOT_PATH));
      fs.mkdirSync(shotDir, {recursive: true});
      await page.screenshot({path: SCREENSHOT_PATH});
      assert.ok(fs.existsSync(SCREENSHOT_PATH), SCREENSHOT_PATH);
      pass(`screenshot saved to ${SCREENSHOT_PATH} (${fs.statSync(SCREENSHOT_PATH).size} bytes)`);
    } else {
      pass('map redrawn at 1440x900; no screenshot (set MENTAT_EVIDENCE_MAP_SCREENSHOT to keep one)');
    }

    step('final check: zero pageerror entries and zero console error entries');
    assert.deepEqual(errors, [], `page errors: ${JSON.stringify(errors)}`);
    assert.deepEqual(consoleErrors, [], `console errors: ${JSON.stringify(consoleErrors)}`);
    pass('no page errors, no console errors across every step');

    console.log(`ALL ${stepNumber} STEPS PASSED`);
  } catch (error) {
    failed = true;
    console.error(`FAILED at step ${stepNumber} (${currentStepLabel}): ${error.stack || error.message}`);
    if (pageForDiagnostics) {
      try {
        const seen = await pageForDiagnostics.evaluate(() => {
          const pick = selector => {
            const node = document.querySelector(selector);
            return node ? node.textContent.replace(/\s+/g, ' ').trim().slice(0, 320) : null;
          };
          return {message: pick('#evidence-map .map-message'), note: pick('#evidence-map .map-note'),
                  ranked: pick('#evidence-map .map-ranked'), trail: pick('#evidence-map .map-trail'),
                  layers: pick('#evidence-map .map-layers')};
        });
        console.error('MAP VIEW AT FAILURE: ' + JSON.stringify(seen, null, 1));
      } catch (problem) { console.error(`diagnostic dump failed: ${problem.message}`); }
    }
  } finally {
    clearTimeout(watchdog);
    if (browser) await browser.close().catch(() => {});
    await stopServer(server);
    if (stubbed) await new Promise(resolve => stubbed.stub.close(() => resolve()));
    for (const target of [dataRoot, fixtureDir, reportRoot]) await cleanupPath(target);
    process.exit(failed ? 1 : 0);
  }
})();
