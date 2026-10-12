/* P4b: the PAC map is INTERACTIVE, and the picture survives interacting with it.
 *
 * The point of this suite is the one thing the S8 map could not do: zoom, pan,
 * change layer and select without the <svg> being rebuilt underneath the
 * reader. So the central assertion is NODE IDENTITY - the same element objects
 * before and after a wheel zoom - not a re-query that equal markup would pass.
 *
 * It also measures. The brief set two budgets (first open under 1.5s excluding
 * network; a wheel-zoom frame under 16ms with 3,000 circles present) and this
 * run prints the numbers it measured rather than asserting a feeling.
 *
 * Harness copied from tests/test_evidence_map_browser.cjs: freePort(),
 * startServer/stopServer, step()/pass(), HARD_CAP_MS, withTimeout and the
 * MENTAT_NODE_MODULES playwright resolution.
 * Run from cwd mentat/:  node tests\test_evidence_map_interaction_browser.cjs
 * MENTAT_MAP_SHOT_DIR keeps the PNGs; unset, the run writes no image.
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
const FORBIDDEN_PORTS = new Set([8801, 8802, 8806, 8808, 8841, 8940, 8971, 8990, 8991, 8992, 8993]);
const HARD_CAP_MS = 900_000;
const BOOK = 'Crypto Industry Spending Overview.xlsx';
const SHOT_DIR = process.env.MENTAT_MAP_SHOT_DIR || '';
// How many distinct ZIPs the fixture spreads its contributors over. The brief's
// performance budget is stated at 3,000 circles, so the fixture carries enough
// ZIPs for the map to have that many to draw.
const ZIP_ROWS = 3200;

let stepNumber = 0, currentStepLabel = '(none)', stepStartedAt = Date.now();
let serverForWatchdog = null, pageForDiagnostics = null;
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
function startServer(port, dataRoot, reportRoot) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.MENTAT_PYTHON || 'python',
      ['app/server.py', '--port', String(port), '--data-root', dataRoot],
      {cwd: MENTAT_DIR, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
       env: {...process.env, MENTAT_REPORT_DIRECTORY: reportRoot}});
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

/* The fixture spreads one committee's receipts over thousands of REAL ZIPs,
   read out of the shipped geo/zcta_points.json, so every circle the map draws
   has a centre the console actually ships rather than one this test invented.
   Lubbock's 79401 is written in by name because the search step looks for it
   and for the district it sits in. */
const FIXTURE_SCRIPT = `
import json
import sys
import openpyxl

target, app_dir, wanted = sys.argv[1], sys.argv[2], int(sys.argv[3])
points = json.loads(open(app_dir + '/geo/zcta_points.json', encoding='utf-8').read())['points']
codes = [code for code in sorted(points) if points[code][3] not in ('PR', 'VI', 'GU', 'AS', 'MP')]
picked = [code for code in ('79401', '94607', '94105', '10001') if code in points]
# Every Nth ZIP in sort order, so the fixture is spread over the whole country.
# The first N in sort order are all in New England, which made a zoom into the
# middle of the map measure an empty screen.
rest = [code for code in codes if code not in picked]
stride = max(1, len(rest) // max(1, wanted - len(picked)))
picked += rest[::stride][:max(0, wanted - len(picked))]

HEAD = ['committee_id', 'committee_name', 'amount', 'date', 'two_year_transaction_period',
        'record_type', 'recipient_category', 'include_in_analysis', 'inclusion_reason',
        'contributor_name', 'street_1', 'city', 'state', 'zip']
workbook = openpyxl.Workbook()
workbook.remove(workbook.active)
sheet = workbook.create_sheet('2023-2024')
sheet.append(HEAD)
for index, code in enumerate(picked):
    x, y, region, state = points[code]
    # Two contributors in some ZIPs, one in others, so the donor-count buckets
    # the legend draws all have members.
    for person in range(1 + index % 4):
        # Keep the explicitly searched ZIPs inside the producer's 3,000-ZIP
        # display cap; otherwise 79401 is correctly reported as off this map.
        sheet.append(['C00804179', 'COINBASE INNOVATION PAC', (10000 if index < 4 else 100) + (index % 37) * 13 + person,
                      '2024-01-02', '2023-2024', 'CONTRIBUTION', 'PAC', 'Yes', 'counted',
                      'PERSON %d-%d' % (index, person), '%d Main St' % (index + 1),
                      'Town %d' % index, state, code])
workbook.save(target + '/${BOOK}')
print(len(picked))
`;

const median = values => {
  const sorted = values.slice().sort((first, second) => first - second);
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
};

(async () => {
  let failed = false, browser = null, server = null;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-p4b-data-'));
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-p4b-fixtures-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-p4b-reports-'));
  try {
    step(`build one workbook spread over ${ZIP_ROWS} real ZIP centres`);
    const written = execFileSync(process.env.MENTAT_PYTHON || 'python',
      ['-c', FIXTURE_SCRIPT, fixtureDir, path.join(MENTAT_DIR, 'app'), String(ZIP_ROWS)],
      {cwd: MENTAT_DIR, encoding: 'utf8'}).trim();
    assert.ok(fs.existsSync(path.join(fixtureDir, BOOK)));
    pass(`${written} ZIPs in ${path.join(fixtureDir, BOOK)}`);

    step('start a scratch console on a free port');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    serverForWatchdog = server;
    pass(`console ready on ${port}`);

    step('open the PAC evidence tab in a FRESH browser context');
    browser = await chromium.launch({channel: 'chrome', headless: true});
    const context = await browser.newContext({viewport: {width: 1600, height: 1000}});
    const page = await context.newPage();
    const selectLayer = async key => {
      const button=page.locator(`#evidence-map .map-layer[data-map-layer="${key}"]`);
      if(await button.isVisible())await button.click();else await page.selectOption('#evidence-map-layer-select',key);
    };
    pageForDiagnostics = page;
    const errors = [], consoleErrors = [];
    page.on('pageerror', error => { errors.push(error.message); console.error(`PAGEERROR: ${error.message}`); });
    page.on('console', message => {
      if (message.type() !== 'error') return;
      consoleErrors.push(message.text());
      console.error(`CONSOLE ERROR: ${message.text()}`);
    });
    await page.goto(`http://127.0.0.1:${port}/`, {waitUntil: 'load'});
    await page.click('#tab-evidence');
    await page.waitForSelector('#view-evidence:not([hidden])', {timeout: 30000});
    pass('evidence tab open');

    step('import the workbook and add the pane');
    await page.click('#evidence-import-open');
    await page.waitForSelector('#evidence-import-drawer:not([hidden])');
    await page.fill('#evidence-path', path.join(fixtureDir, BOOK));
    await page.click('#evidence-path-read');
    await page.waitForFunction(() => document.querySelectorAll('#evidence-pac option').length >= 1,
      null, {timeout: 180000});
    await page.waitForFunction(() =>
      [...document.querySelectorAll('#evidence-cycle option')].some(option => option.value === '2024'),
      null, {timeout: 120000});
    await page.selectOption('#evidence-cycle', '2024');
    // Build 06: the pane opens from its tick in the picker rail (#evidence-add is hidden).
    await page.check('#evidence-list input[type=checkbox][value="C00804179"]');
    // A pane that is still reading has the figure boxes but an em dash in
    // them, and the distinct-contributor count arrives on its own request, so
    // this waits for BOTH to carry a value rather than for the box to exist.
    await page.waitForFunction(() => {
      const values = [...document.querySelectorAll('#evidence-panes [data-pane=a] .pane-figure strong')]
        .map(node => node.textContent.trim());
      return values.length === 4 && values.every(value => value && value !== '\u2014');
    }, null, {timeout: 180000});
    pass('pane A open');

    step('the stat strip is four figures and none of them is a bare dash for a real zero');
    const figures = await page.locator('#evidence-panes [data-pane=a] .pane-figure').evaluateAll(
      nodes => nodes.map(node => ({
        label: node.querySelector('.label').textContent.trim(),
        value: node.querySelector('strong').textContent.trim()})));
    assert.equal(figures.length, 4, JSON.stringify(figures));
    assert.deepEqual(figures.map(item => item.label),
      ['Receipts', 'Contributors', 'Rows in file', 'Cycles covered'], JSON.stringify(figures));
    for (const item of figures) assert.notEqual(item.value, '—', JSON.stringify(figures));
    assert.ok(Number(figures[1].value.replace(/,/g, '')) > 1000, JSON.stringify(figures));
    pass(JSON.stringify(figures));

    step('the pane header is one segmented control: Summary | Map | Flow | Rows');
    const seg = await page.locator('#evidence-panes [data-pane=a] .pane-view button').allTextContents();
    // Lane F (board #44) adds Flow, the Sankey of where the money went.
    assert.deepEqual(seg.map(text => text.trim()), ['Summary', 'Map', 'Flow', 'Rows'], JSON.stringify(seg));
    assert.equal(await page.getAttribute('#evidence-panes [data-pane=a] .pane-summary', 'aria-pressed'), 'true');
    await page.click('#evidence-panes [data-pane=a] .pane-rows');
    await page.waitForSelector('#evidence-panes [data-pane=a] .pane-drawer', {timeout: 60000});
    assert.equal(await page.getAttribute('#evidence-panes [data-pane=a] .pane-rows', 'aria-pressed'), 'true');
    await page.click('#evidence-panes [data-pane=a] .pane-summary');
    await page.waitForSelector('#evidence-panes [data-pane=a] .pane-drawer', {state: 'detached', timeout: 30000});
    pass('Summary | Map | Flow | Rows, with aria-pressed following the view');

    step('Flow opens from the pane header and returns to Map; an import collects its flow directly');
    const flowRequests = [];
    const onFlowRequest = request => { if (request.url().includes('/api/flow')) flowRequests.push(request.url()); };
    page.on('request', onFlowRequest);
    // Since 7b1cc259d (evidence-flow.js load()) an import pane no longer explains that a flow needs a saved
    // snapshot: it asks /api/flow for a direct flow (direct=1), which collects the committee's FEC filings.
    // Hold that request so the status stays on "Collecting FEC filings…" and the run never reaches FEC.
    const heldFlowRoutes = [];
    const isFlowUrl = url => url.pathname === '/api/flow';
    await page.route(isFlowUrl, route => { heldFlowRoutes.push(route); });
    await page.click('#evidence-panes [data-pane=a] .pane-flow');
    await page.waitForFunction(() => document.querySelector('#view-evidence').classList.contains('flow-open')
      && !document.getElementById('evidence-flow').hidden && !document.getElementById('evidence-flow-status').hidden,
      null, {timeout: 30000});
    const flowOpen = await page.evaluate(() => ({
      pressed: [...document.querySelectorAll('#evidence-flow .flow-views [aria-pressed=true]')].map(node => node.dataset.view),
      status: document.getElementById('evidence-flow-status').textContent.trim(),
      panesHidden: document.getElementById('evidence-panes').hidden, url: location.search}));
    assert.deepEqual(flowOpen.pressed, ['flow'], JSON.stringify(flowOpen));
    assert.match(flowOpen.status, /Collecting FEC filings and drawing the flow/, 'an import pane says its flow is collected from FEC filings');
    assert.equal(flowOpen.panesHidden, true, 'the Flow view takes the evidence view');
    assert.match(flowOpen.url, /[?&]a\.flow=reported(&|$)/, 'the URL remembers the Flow view (D-039)');
    assert.equal(flowRequests.length, 1, 'an import pane asks /api/flow once: ' + JSON.stringify(flowRequests));
    const flowQuery = new URL(flowRequests[0]).searchParams;
    assert.equal(flowQuery.get('direct'), '1', 'an import pane asks for a direct flow');
    assert.match(flowQuery.get('root'), /^committee:C\d{8}$/, 'the direct flow is rooted on the pane’s committee');
    assert.equal(flowQuery.get('id'), null, 'an import pane names no saved snapshot');
    await page.click('#evidence-flow .flow-views [data-view="map"]');
    await page.waitForSelector('#evidence-map svg.map-canvas', {timeout: 120000});
    const flowClosed = await page.evaluate(() => ({flowOpen: document.querySelector('#view-evidence').classList.contains('flow-open'),
      flowKey: /[?&]a\.flow=/.test(location.search), flowHidden: document.getElementById('evidence-flow').hidden}));
    assert.deepEqual(flowClosed, {flowOpen: false, flowKey: false, flowHidden: true}, JSON.stringify(flowClosed));
    page.off('request', onFlowRequest);
    await page.unroute(isFlowUrl);
    await Promise.all(heldFlowRoutes.map(route => route.abort().catch(() => {})));
    // Back to the panes: the next step opens the map from the pane header.
    await page.click('#evidence-map-back');
    await page.waitForSelector('#evidence-panes:not([hidden])', {timeout: 30000});
    pass(`Flow pressed, "${flowOpen.status.slice(0, 60)}…", one direct /api/flow request; Map from the Flow view closed it`);

    step('open the map: it opens on the side that HAS data, and the empty side says why');
    const openedAt = Date.now();
    await page.click('#evidence-panes [data-pane=a] .pane-map');
    await page.waitForSelector('#evidence-map svg.map-canvas', {timeout: 120000});
    await page.waitForFunction(() => document.querySelectorAll('#evidence-map .map-circle').length > 0,
      null, {timeout: 120000});
    const openMs = Date.now() - openedAt;
    const raisedPressed = await page.getAttribute('#evidence-map .map-direction[data-direction="raised"]', 'aria-pressed');
    const spentDisabled = await page.isDisabled('#evidence-map .map-direction[data-direction="spent"]');
    const spentTitle = await page.getAttribute('#evidence-map .map-direction[data-direction="spent"]', 'title');
    assert.equal(raisedPressed, 'true', 'the map did not open on Raised, which is the side with money');
    assert.equal(spentDisabled, true, 'the empty Spent side was not disabled');
    assert.ok(/outbound/i.test(spentTitle || ''), `Spent's title is not the route's refusal: ${spentTitle}`);
    // Fix round 1 (R06-07): the map opens on State, as boards 6 and M13 draw
    // it; the steps below read district paths, so they choose that layer.
    assert.equal(await page.getAttribute('#evidence-map .map-layer[data-map-layer="state"]', 'aria-pressed'), 'true',
      'the map opens on State');
    await selectLayer('district');
    await page.waitForSelector('#evidence-map path.map-district', {state: 'attached', timeout: 60000});
    pass(`opened on Raised in ${openMs}ms; Spent disabled with "${(spentTitle || '').slice(0, 60)}…"`);


    step('ordinary scrolling stays on the page and new navigation interrupts a fly');
    const gestures=await page.evaluate(async()=>{
      const host=document.querySelector('.map-canvas-host'),svg=host.querySelector('svg.map-canvas'),api=window.MentatEvidenceMap;
      const scroll=new WheelEvent('wheel',{deltaY:120,bubbles:true,cancelable:true});
      svg.dispatchEvent(scroll);
      api.flyToKey(host,'TX'); await new Promise(resolve=>setTimeout(resolve,70));
      api.zoom(host,1.4); await new Promise(resolve=>requestAnimationFrame(resolve));
      const requested=api.view(host);await new Promise(resolve=>setTimeout(resolve,300));
      const landed=api.view(host);
      api.reset(host);
      return {scrollCaptured:scroll.defaultPrevented,requested,landed,touchAction:getComputedStyle(svg).touchAction};
    });
    assert.equal(gestures.scrollCaptured,false,'normal scrolling must not zoom the map');
    assert.deepEqual(gestures.landed,gestures.requested,'fly animation must yield to a new zoom');
    assert.match(gestures.touchAction,/pan-y/,'touch users must be able to scroll the page');
    await page.waitForFunction(()=>!window.MentatEvidenceMap.animating(document.querySelector('.map-canvas-host')));
    pass(JSON.stringify(gestures));
    step('a wheel zoom moves the transform and keeps the SAME nodes');
    await page.evaluate(() => {
      window.__probe = {
        svg: document.querySelector('#evidence-map svg.map-canvas'),
        viewport: document.querySelector('#evidence-map .map-viewport'),
        path: document.querySelector('#evidence-map path.map-district'),
      };
      window.__probe.pathKey = window.__probe.path.getAttribute('data-key');
    });
    // hover() scrolls the card into view first, so the wheel below lands on
    // the map rather than wherever the page happened to be.
    await page.locator('#evidence-map svg.map-canvas').hover();
    const before = await page.evaluate(() =>
      document.querySelector('#evidence-map .map-viewport').getAttribute('transform'));
    await page.keyboard.down('Control');
    for (let turn = 0; turn < 6; turn += 1) {
      await page.mouse.wheel(0, -120);
      await page.waitForTimeout(40);
    }
    await page.keyboard.up('Control');
    await page.waitForFunction(previous =>
      document.querySelector('#evidence-map .map-viewport').getAttribute('transform') !== previous,
      before, {timeout: 15000});
    const identity = await page.evaluate(() => {
      const probe = window.__probe;
      const path = document.querySelector(`#evidence-map path.map-district[data-key="${probe.pathKey}"]`);
      return {
        svg: probe.svg === document.querySelector('#evidence-map svg.map-canvas'),
        viewport: probe.viewport === document.querySelector('#evidence-map .map-viewport'),
        path: probe.path === path,
        transform: probe.viewport.getAttribute('transform'),
        connected: probe.svg.isConnected,
      };
    });
    assert.equal(identity.svg, true, 'the <svg> was replaced by the zoom');
    assert.equal(identity.viewport, true, 'the viewport <g> was replaced by the zoom');
    assert.equal(identity.path, true, 'a district <path> was replaced by the zoom');
    assert.equal(identity.connected, true, 'the probed <svg> is no longer in the document');
    assert.notEqual(identity.transform, before, `the transform did not move: ${before}`);
    pass(`same svg, same viewport <g>, same path node; transform ${before} -> ${identity.transform}`);

    step('30 wheel updates with a 3,000-ZIP source; viewport culling remains enabled');
    // Start at national extent. Offscreen ZIPs are now culled during zoom,
    // without removing them from the totals or searchable list.
    await page.click('#evidence-map-reset');
    await page.waitForTimeout(600);
    await selectLayer('zip');
    await page.waitForFunction(() =>
      window.MentatEvidenceMap.circleCount(document.querySelector('#evidence-map .map-canvas-host')) > 0,
      null, {timeout: 60000});
    const circles = await page.evaluate(() =>
      window.MentatEvidenceMap.circleCount(document.querySelector('#evidence-map .map-canvas-host')));
    // The pointer is on the layer button after the click above; the wheel goes
    // wherever the pointer is, so it is put back on the map first.
    await page.locator('#evidence-map svg.map-canvas').hover();
    await page.evaluate(() => {
      const host = document.querySelector('#evidence-map .map-canvas-host');
      window.__framesBefore = window.MentatEvidenceMap.frames(host).length;
    });
    await page.keyboard.down('Control');
    for (let turn = 0; turn < 30; turn += 1) {
      await page.mouse.wheel(0, turn % 2 ? 120 : -120);
      await page.waitForTimeout(16);
    }
    await page.keyboard.up('Control');
    const frames = await page.evaluate(() => {
      const host = document.querySelector('#evidence-map .map-canvas-host');
      return window.MentatEvidenceMap.frames(host).slice(window.__framesBefore);
    });
    const zipRows = await page.evaluate(() => {
      const host = document.querySelector('#evidence-map .map-canvas-host');
      return {drawn: window.MentatEvidenceMap.circleCount(host),
              unplaced: window.MentatEvidenceMap.unplaced(host).length};
    });
    const frameMedian = median(frames), frameMax = Math.max(...frames);
    console.log(`MEASURED first-open-to-interactive ${openMs}ms (includes localhost fetches)`);
    console.log(`MEASURED wheel transform JS (not total paint/frame time) over ${frames.length} samples: median ${frameMedian.toFixed(2)}ms,`
      + ` max ${frameMax.toFixed(2)}ms, circles in the DOM ${circles} -> ${zipRows.drawn},`
      + ` ZIPs with no centre ${zipRows.unplaced}`);
    assert.ok(frames.length >= 20, `only ${frames.length} frames were recorded`);
    pass(`median ${frameMedian.toFixed(2)}ms, max ${frameMax.toFixed(2)}ms over ${frames.length} frames`);

    step('ZIP rankings page all available rows, filter beyond the old 200-row cap, and pin places');
    assert.equal(await page.locator('#evidence-map .map-row').count(),40);
    await page.locator('[data-map-page="1"]').click();
    assert.match(await page.locator('.map-list-pager').textContent(),/41–80/);
    // Fix round 1 (R06-16): filter, sort and pin are in "Filter, sort and pin".
    await page.click('#evidence-map .map-list-menu > summary');
    await page.waitForSelector('#evidence-map-list-sort', {state: 'visible'});
    await page.selectOption('#evidence-map-list-sort','ascending');
    const lastZip=await page.locator('#evidence-map .map-row').first().getAttribute('data-focus');
    await page.fill('#evidence-map-list-search',lastZip);
    assert.equal(await page.locator('#evidence-map .map-row').count(),1);
    await page.locator('#evidence-map .map-row button').first().click();
    await page.click('#evidence-map-pin');
    assert.equal(await page.locator('.map-pin-grid article').count(),1);
    assert.match(await page.locator('.map-pin-grid').textContent(),new RegExp(lastZip));
    await page.locator('[data-map-source-page="1"]').click();
    await page.waitForFunction(()=>document.querySelector('.map-list-pager')?.textContent.includes('Source page 2'));
    assert.match(await page.locator('#evidence-map .map-reconcile').filter({hasText:'not all donors'}).textContent(),/not all donors/);
    await page.click('.map-clear-focus');
    await page.selectOption('#evidence-map-list-sort','amount');
    pass('all ZIPs searchable, 40 DOM rows, pinned original scope, exact-cycle source pagination');

    step('every geography layer is selectable');
    const layers = await page.locator('#evidence-map .map-layer').evaluateAll(nodes => nodes.map(node => ({
      key: node.dataset.mapLayer, label: node.textContent.trim(),
      disabled: node.disabled, title: node.getAttribute('title')})));
    assert.deepEqual(layers.map(item => item.key), ['region', 'state', 'district', 'sld', 'zip'],
      JSON.stringify(layers));
    const sld = layers.find(item => item.key === 'sld');
    assert.equal(sld.disabled, false, 'State district must be available');
    for (const key of ['region', 'state', 'zip', 'district']) {
      await selectLayer(key);
      await page.waitForFunction(wanted =>
        document.querySelector(`#evidence-map .map-layer[data-map-layer="${wanted}"]`)
          .getAttribute('aria-pressed') === 'true', key, {timeout: 30000});
      await page.waitForSelector('#evidence-map svg.map-canvas', {timeout: 30000});
    }
    pass(`layers ${JSON.stringify(layers.map(item => item.label))}, all four enabled ones drew`);


    step('legislative overlays load one state/chamber, preserve provenance and bound paths');
    const bundles = [];
    page.on('request', request => {
      if (/\/geo\/sld\/(upper|lower)\/[A-Z]{2}\.json$/.test(request.url())) bundles.push(request.url());
    });
    await selectLayer('sld');
    await page.waitForSelector('#evidence-map-state option[value="TX"]', {state:'attached'});
    await page.focus('#evidence-map-state');
    await page.selectOption('#evidence-map-state', 'TX');
    await page.waitForSelector('.map-sld[data-key^="TX-upper-"]');
    await page.waitForFunction(()=>!window.MentatEvidenceMap.animating(document.querySelector('.map-canvas-host')));
    assert.ok(await page.locator('.map-sld').count() <= 31);
    assert.equal(await page.evaluate(()=>document.activeElement.id),'evidence-map-state','state selection must keep keyboard focus');
    assert.match(await page.locator('.map-provenance').textContent(), /Census 2024.*ZIP internal-point estimate/s);
    assert.match(await page.locator('.map-provenance a').getAttribute('href'), /sldu_500k/);
    const upperKey = await page.locator('.map-row').first().getAttribute('data-focus');
    await page.locator('.map-row button').first().click();
    await page.waitForSelector('.map-sld.chosen');
    assert.equal(await page.locator('.map-sld.chosen').getAttribute('data-key'), upperKey);
    assert.match(await page.locator('#evidence-map .map-trail').textContent(), /District/);
    await page.selectOption('#evidence-map-chamber', 'lower');
    await page.waitForSelector('.map-sld[data-key^="TX-lower-"]');
    assert.equal(await page.locator('.map-sld[data-key^="TX-upper-"]').count(), 0);
    assert.ok(await page.locator('.map-sld').count() <= 150);
    assert.match(await page.locator('.map-provenance a').getAttribute('href'), /sldl_500k/);
    await page.selectOption('#evidence-map-state', 'NE');
    await page.waitForFunction(()=>document.querySelector('.map-provenance').textContent.includes('No lower chamber'));
    assert.equal(await page.locator('.map-sld').count(),0);
    assert.equal(bundles.some(url=>url.endsWith('/lower/NE.json')),false,'absent chamber must not request a missing file');
    assert.ok(bundles.length<=3, 'opening SLD must not download national geometry: '+bundles.length);
    await page.selectOption('#evidence-map-state','TX');
    await page.waitForSelector('.map-sld[data-key^="TX-lower-"]');
    await page.waitForFunction(()=>!window.MentatEvidenceMap.animating(document.querySelector('.map-canvas-host')));
    await page.click('#evidence-map-enlarge');
    await page.locator('.map-figure').scrollIntoViewIfNeeded();
    if (SHOT_DIR) await page.locator('#evidence-map').screenshot({path:path.join(SHOT_DIR,'06_state_legislative_enlarged.png')});
    if (SHOT_DIR) {
      const clip = await page.evaluate(()=>{
        const root=document.querySelector('#evidence-map').getBoundingClientRect();
        const figure=document.querySelector('.map-figure').getBoundingClientRect();
        return {x:root.x+scrollX,y:root.y+scrollY,width:root.width,height:figure.bottom-root.y+12};
      });
      await page.screenshot({path:path.join(SHOT_DIR,'pac-map-overlays.png'),fullPage:true,clip});
    }

    const legendLayout=await page.evaluate(()=>{
      const svg=document.querySelector('svg.map-canvas').getBoundingClientRect();
      const legend=document.querySelector('.map-legend-card').getBoundingClientRect();
      return {svgBottom:svg.bottom,legendTop:legend.top,legendWidth:legend.width,svgWidth:svg.width};
    });
    assert.ok(legendLayout.legendTop>=legendLayout.svgBottom,'heat index must not overlap geography');
    assert.ok(legendLayout.legendWidth<=legendLayout.svgWidth+1,'legend must fit its card');
    await page.click('#evidence-map-enlarge');
    pass(`${bundles.length} state/chamber requests; absent chamber explicit; heat index below the map`);

    step('ZIP overlay hides congressional heat, keeps its source and respects the marker budget');
    await selectLayer('zip');
    await page.click('#evidence-map-reset');
    await page.waitForFunction(()=>!window.MentatEvidenceMap.animating(document.querySelector('.map-canvas-host')));
    const zipCheck=await page.evaluate(()=>({
      circles:document.querySelectorAll('.map-circle').length,
      budget:window.MentatEvidenceMap.MAX_CIRCLES,
      visibleHouse:[...document.querySelectorAll('.map-district')].some(node=>getComputedStyle(node).display!=='none'),
      colors:new Set([...document.querySelectorAll('.map-circle')].map(node=>node.style.fill)).size
    }));
    assert.ok(zipCheck.circles>0&&zipCheck.circles<=zipCheck.budget,JSON.stringify(zipCheck));
    assert.equal(zipCheck.visibleHouse,false);
    assert.ok(zipCheck.colors>1,'ZIP heat must reflect its own amounts');
    assert.match(await page.locator('.map-provenance').textContent(),/not the population centroid/);
    assert.match(await page.locator('.map-provenance a').getAttribute('href'),/2024_Gaz_zcta_national/);
    pass(JSON.stringify(zipCheck));
    step('late legislative responses cannot replace a newly selected ZIP layer');
    await page.route('**/geo/sld/lower/VA.json', async route=>{
      await new Promise(resolve=>setTimeout(resolve,500));
      await route.continue();
    });
    await selectLayer('sld');
    await page.waitForSelector('#evidence-map-state');
    await page.selectOption('#evidence-map-state','VA');
    await selectLayer('zip');
    await page.waitForTimeout(800);
    assert.equal(await page.locator('.map-sld').count(),0);
    assert.equal(await page.locator('.map-layer[data-map-layer="zip"]').getAttribute('aria-pressed'),'true');
    assert.match(await page.locator('.map-provenance').textContent(),/ZIP\/ZCTA/);
    await page.unroute('**/geo/sld/lower/VA.json');
    const viewport=page.viewportSize();
    await page.setViewportSize({width:900,height:900});
    const fits=await page.locator('.map-legend-card').evaluate(node=>node.scrollWidth<=node.clientWidth+1);
    assert.equal(fits,true,'heat index must wrap within the narrower card');
    await page.setViewportSize(viewport);
    pass('latest layer wins; heat index wraps in a narrow viewport');


    step('search TX-19, then a ZIP: each flies there and selects it');
    await selectLayer('district');
    await page.fill('#evidence-map-search', 'TX-19');
    await page.press('#evidence-map-search', 'Enter');
    await page.waitForFunction(() => {
      const node = document.querySelector('#evidence-map .map-canvas [data-key="TX-19"]');
      return !!node && node.classList.contains('chosen');
    }, null, {timeout: 60000});
    const trailAfterDistrict = (await page.textContent('#evidence-map .map-trail')).replace(/\s+/g, ' ').trim();
    // Fix round 1 (R06-08): zoomed to a district, the district is the title
    // (approved board 7) and the PAC is the overline.
    assert.equal((await page.textContent('#evidence-map .map-head h2')).trim(), 'TX-19: Congressional District 19');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'evidence-map-search','search must retain keyboard focus');
    await page.press('#evidence-map-search','Enter');
    await page.waitForTimeout(300);
    assert.equal(await page.locator('.map-district[data-key="TX-19"]').getAttribute('class').then(x=>x.includes('chosen')),true,
      'repeating a search must not deselect the result');
    await page.fill('#evidence-map-search','TX');
    // Fix round 1 (R06-04): no Find button; Enter runs the search.
    assert.equal(await page.locator('#evidence-map-find').count(), 0);
    await page.press('#evidence-map-search','Enter');
    await page.waitForFunction(()=>document.querySelector('.map-trail').textContent.includes('Texas'));
    assert.match(await page.locator('.map-head .map-overline').textContent(),/COINBASE/,'inspection must retain committee context');
    assert.equal((await page.textContent('#evidence-map .map-head h2')).trim(), 'Texas');

    assert.ok(/United States/.test(trailAfterDistrict) && /Texas/.test(trailAfterDistrict)
      && /TX-19/.test(trailAfterDistrict), trailAfterDistrict);
    if (SHOT_DIR) await page.screenshot({path: path.join(SHOT_DIR, '03_map_zoomed_tx19.png')});
    await page.fill('#evidence-map-search', '79401');
    await page.press('#evidence-map-search', 'Enter');
    await page.waitForFunction(() => {
      const node = document.querySelector('#evidence-map .map-circles [data-key="79401"]');
      return !!node && node.classList.contains('chosen');
    }, null, {timeout: 60000});
    await page.fill('#evidence-map-search', 'Nowhereland');
    await page.press('#evidence-map-search', 'Enter');
    await page.waitForSelector('#evidence-map .map-search-message', {timeout: 30000});
    const refusal = (await page.textContent('#evidence-map .map-search-message')).trim();
    assert.ok(/Nowhereland/.test(refusal), refusal);
    pass(`trail "${trailAfterDistrict}"; a ZIP selected; an unknown name answered "${refusal}"`);

    step('click a state: the ranked card filters and the totals on screen still reconcile');
    await selectLayer('state');
    await page.waitForFunction(() =>
      document.querySelector('#evidence-map .map-layer[data-map-layer="state"]')
        .getAttribute('aria-pressed') === 'true', null, {timeout: 30000});
    const reconcile = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#evidence-map .map-row')].map(row => ({
        key: row.dataset.focus,
        amount: Number(row.querySelector('td.num').textContent.replace(/[^0-9.]/g, ''))}));
      // Fix r2 (TC-07): the line shows "located $X · unlocated $Y" (boards 6
      // and M13); the whole reconciliation, with the reported total, is its
      // title and its screen-reader text, and the two must agree.
      const node = document.querySelector('#evidence-map .map-reconcile');
      const shown = node.querySelector('.map-reconcile-figures').textContent.match(/located \$([\d,]+) · unlocated \$([\d,]+)/);
      const found = node.title.match(/\$([\d,]+) located \+ \$([\d,]+) unlocated = \$([\d,]+)/);
      const spoken = node.querySelector('.map-reconcile-sentence').textContent.trim();
      const n = text => Number(text.replace(/,/g, ''));
      return {rows, located: n(shown[1]), unlocated: n(shown[2]), reported: n(found[3]),
              titleLocated: n(found[1]), titleUnlocated: n(found[2]), spokenMatches: spoken === node.title};
    });
    assert.equal(reconcile.titleLocated, reconcile.located, 'the shown and titled located totals differ');
    assert.equal(reconcile.titleUnlocated, reconcile.unlocated, 'the shown and titled unlocated totals differ');
    assert.ok(reconcile.spokenMatches, 'screen readers must hear the whole reconciliation');
    const allStateRows=[...reconcile.rows];
    while(!await page.locator('[data-map-page="1"]').isDisabled()) {
      await page.locator('[data-map-page="1"]').click();
      allStateRows.push(...await page.locator('#evidence-map .map-row').evaluateAll(nodes=>nodes.map(row=>({key:row.dataset.focus,amount:Number(row.querySelector('td.num').textContent.replace(/[^0-9.]/g,''))}))));
    }
    while(!await page.locator('[data-map-page="-1"]').isDisabled())await page.locator('[data-map-page="-1"]').click();
    const listed = allStateRows.reduce((total, row) => total + row.amount, 0);
    assert.equal(reconcile.located + reconcile.unlocated, reconcile.reported,
      JSON.stringify(reconcile).slice(0, 300));
    // Every state row is in the list, so the list adds to `located` to the
    // dollar the page rounds to.
    assert.ok(Math.abs(listed - reconcile.located) <= allStateRows.length,
      `the state rows add to ${listed}, the line says ${reconcile.located}`);
    const first = reconcile.rows[0];
    await page.click(`#evidence-map .map-row[data-focus="${first.key}"]`);
    await page.waitForFunction(() => /(Donors|Rows) in /.test(
      document.querySelector('#evidence-map .map-ranked .panel-heading h2').textContent), null, {timeout: 60000});
    const heading = (await page.textContent('#evidence-map .map-ranked .panel-heading h2')).trim();
    assert.ok(heading.includes('in '), heading);
    if (SHOT_DIR) await page.screenshot({path: path.join(SHOT_DIR, '04_map_state_selected.png')});
    pass(`${reconcile.rows.length} states add to $${listed} against a located line of $${reconcile.located}; "${heading}"`);

    step('Enlarge then Collapse: the card grows and the zoom survives both');
    await page.click('#evidence-map .map-clear-focus');
    await page.waitForFunction(() => !window.MentatEvidenceMap.animating(
      document.querySelector('#evidence-map .map-canvas-host')));
    const zoomBefore = await page.evaluate(() =>
      window.MentatEvidenceMap.view(document.querySelector('#evidence-map .map-canvas-host')));
    const narrow = (await page.locator('#evidence-map svg.map-canvas').boundingBox()).height;
    await page.click('#evidence-map-enlarge');
    await page.waitForTimeout(600);
    const wide = (await page.locator('#evidence-map svg.map-canvas').boundingBox()).height;
    const zoomWide = await page.evaluate(() =>
      window.MentatEvidenceMap.view(document.querySelector('#evidence-map .map-canvas-host')));
    assert.ok(wide > narrow, `enlarge did not increase the map area: ${narrow} -> ${wide}`);
    assert.equal(zoomWide.scale.toFixed(4), zoomBefore.scale.toFixed(4), 'the zoom was lost on enlarge');
    if (SHOT_DIR) await page.screenshot({path: path.join(SHOT_DIR, '02_map_enlarged.png')});
    await page.click('#evidence-map-enlarge');
    await page.waitForTimeout(600);
    const back = (await page.locator('#evidence-map svg.map-canvas').boundingBox()).height;
    const zoomBack = await page.evaluate(() =>
      window.MentatEvidenceMap.view(document.querySelector('#evidence-map .map-canvas-host')));
    assert.ok(Math.abs(back - narrow) < 40, `collapse did not restore the width: ${narrow} -> ${back}`);
    assert.equal(zoomBack.scale.toFixed(4), zoomBefore.scale.toFixed(4), 'the zoom was lost on collapse');
    pass(`card ${Math.round(narrow)}px -> ${Math.round(wide)}px -> ${Math.round(back)}px, zoom held at ${zoomBack.scale.toFixed(3)}x`);

    step('national view for the record');
    await page.click('#evidence-map-reset');
    await page.waitForTimeout(800);
    await page.click('#evidence-map-enlarge');
    await page.waitForTimeout(700);
    await page.evaluate(() => window.scrollTo(0, 0));
    const tableFits = await page.locator('#evidence-map .map-list').evaluate(node => node.scrollWidth <= node.clientWidth + 1);
    assert.ok(tableFits, 'ranked amounts and shares must fit without horizontal scrolling');
    assert.equal(await page.locator('#evidence-map .map-locator').isVisible(), false,
      'the locator is needed only when zoomed in');
    if (SHOT_DIR) await page.screenshot({path: path.join(SHOT_DIR, '01_map_enlarged_national.png')});
    const paneShot = SHOT_DIR ? path.join(SHOT_DIR, '00_panes.png') : '';
    await page.click('#evidence-map-back');
    await page.waitForSelector('#evidence-panes:not([hidden])', {timeout: 30000});
    await page.waitForTimeout(400);
    await page.evaluate(() => window.scrollTo(0, 0));
    if (paneShot) await page.screenshot({path: paneShot});
    pass(SHOT_DIR ? `PNGs under ${SHOT_DIR}` : 'no PNGs (set MENTAT_MAP_SHOT_DIR to keep them)');


    step('phone controls expose a native layer picker and keep the map clear');
    await page.click('#evidence-panes [data-pane=a] .pane-map');
    await page.waitForSelector('svg.map-canvas');
    await page.setViewportSize({width:390,height:844});
    if(!await page.locator('#evidence-map-layer-select').isVisible())await page.locator('.map-scope-disclosure > summary').click();
    await selectLayer('sld');
    await page.waitForSelector('#evidence-map-state option[value="TX"]',{state:'attached'});
    await page.focus('#evidence-map-state');
    await page.selectOption('#evidence-map-state','TX');
    await page.waitForSelector('.map-sld[data-key^="TX-"]');
    await page.waitForFunction(()=>!window.MentatEvidenceMap.animating(document.querySelector('.map-canvas-host')));
    const mobile=await page.locator('#evidence-map').evaluate(node=>({
      width:node.clientWidth,scroll:node.scrollWidth,map:node.querySelector('svg.map-canvas').getBoundingClientRect().width,
      toolbar:node.querySelector('.map-toolbar').getBoundingClientRect().height,
      focus:document.activeElement.id
    }));
    assert.ok(mobile.scroll<=mobile.width+1,JSON.stringify(mobile));
    assert.equal(await page.locator('.map-locator').isVisible(),false,'locator must not cover the phone map');
    assert.equal(mobile.focus,'evidence-map-state');
    if(SHOT_DIR) await page.locator('#evidence-map').screenshot({path:path.join(SHOT_DIR,'polished-mobile.png')});
    await page.setViewportSize({width:1440,height:1050});
    if(SHOT_DIR) await page.locator('#evidence-map').screenshot({path:path.join(SHOT_DIR,'polished-desktop.png')});
    pass(JSON.stringify(mobile));

    step('with reduced motion, the map answers instantly and animates nothing');
    const still = await browser.newContext({viewport: {width: 1600, height: 1000},
                                            reducedMotion: 'reduce'});
    const stillPage = await still.newPage();
    const stillErrors = [];
    stillPage.on('pageerror', error => stillErrors.push(error.message));
    await stillPage.goto(`http://127.0.0.1:${port}/`, {waitUntil: 'load'});
    await stillPage.click('#tab-evidence');
    await stillPage.waitForSelector('#view-evidence:not([hidden])', {timeout: 30000});
    await stillPage.waitForFunction(() => document.querySelectorAll('#evidence-source option').length > 1,
      null, {timeout: 60000});
    await stillPage.selectOption('#evidence-source', {index: 1});
    await stillPage.waitForFunction(() => document.querySelectorAll('#evidence-pac option').length >= 1,
      null, {timeout: 60000});
    await stillPage.waitForFunction(() =>
      [...document.querySelectorAll('#evidence-cycle option')].some(option => option.value === '2024'),
      null, {timeout: 60000});
    await stillPage.selectOption('#evidence-cycle', '2024');
    // Build 06: the pane opens from its tick in the picker rail (#evidence-add is hidden).
    await stillPage.check('#evidence-list input[type=checkbox][value="C00804179"]');
    await stillPage.waitForSelector('#evidence-panes [data-pane=a] .pane-map', {timeout: 120000});
    await stillPage.click('#evidence-panes [data-pane=a] .pane-map');
    await stillPage.waitForSelector('#evidence-map svg.map-canvas', {timeout: 120000});
    const instant = await stillPage.evaluate(async () => {
      const host = document.querySelector('#evidence-map .map-canvas-host');
      const before = window.MentatEvidenceMap.view(host);
      const circle = document.querySelector('#evidence-map .map-circle');
      const transition = circle ? getComputedStyle(circle).transitionDuration : 'none';
      const shape = document.querySelector('#evidence-map .map-canvas [data-bbox]');
      shape.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, cancelable: true, view: window}));
      // No frame is waited for: under reduced motion the fly is applied in the
      // same task, so the transform has already moved when this line runs.
      const after = window.MentatEvidenceMap.view(host);
      return {moved: before.scale !== after.scale || before.x !== after.x,
              scale: after.scale,
              transition};
    });
    assert.equal(instant.moved, true, 'the fly-to did not land in the same task under reduced motion');
    assert.equal(instant.transition, '0s', `circles still animate under reduced motion: ${instant.transition}`);
    if (SHOT_DIR) await stillPage.screenshot({path: path.join(SHOT_DIR, '05_map_reduced_motion.png')});
    assert.deepEqual(stillErrors, [], JSON.stringify(stillErrors));
    await still.close();
    pass(`fly-to landed at ${instant.scale.toFixed(2)}x with transition-duration ${instant.transition}`);

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
            return node ? node.textContent.replace(/\s+/g, ' ').trim().slice(0, 300) : null;
          };
          return {message: pick('#evidence-map .map-message'), ranked: pick('#evidence-map .map-ranked'),
                  trail: pick('#evidence-map .map-trail'), search: pick('#evidence-map .map-search-message')};
        });
        console.error('MAP VIEW AT FAILURE: ' + JSON.stringify(seen, null, 1));
      } catch (problem) { console.error(`diagnostic dump failed: ${problem.message}`); }
    }
  } finally {
    clearTimeout(watchdog);
    if (browser) await withTimeout(browser.close(), 10000, 'browser.close()').catch(error=>console.error(error.message));
    await stopServer(server);
    for (const target of [dataRoot, fixtureDir, reportRoot]) await cleanupPath(target);
    process.exit(failed ? 1 : 0);
  }
})();
