/* The Chart Studio board (option C), real-client pass.
 *
 * What this proves that a unit test cannot: Canvas mode draws 1, 2 or 4 sheet
 * tiles; two tiles carry two different titles and each keeps its own
 * reconciliation line, so no figure loses its denominator; a sheet already on
 * the board is not offered to a second tile; the selected tile is what the
 * Inspector, Export and "Add to briefing" act on; a board saves and reopens
 * into the same slots; a view saved without a board still opens as one sheet;
 * Board 1 is today's single canvas; and "Add board to briefing" adds one card
 * per filled tile, in slot order.
 *
 * Design: audits/mentat_failings_20260915/UI_CLEANUP_DESIGN_2026-09-17.md
 * section 4 (Multiple charts, option C) and section 11 (the board build).
 * Contract: contracts/studio_api.json v2, the view-level board {tiles, slots}.
 *
 * Spawn pattern and stubbing: tests/test_ui_cleanup_browser.cjs - a real
 * `python app/server.py` on a free ephemeral port with a throwaway --data-root,
 * the contribution dataset stubbed because no real snapshot exists in a
 * throwaway root. /api/studio/views is NOT stubbed: saving and reopening a
 * board is a real write to a real console, which is what DE-021 asks for.
 *
 * Run: node tests/test_studio_board_browser.cjs   (cwd: mentat/)
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
const studioApi = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'studio_api.json'), 'utf8'));

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
  {id: 'recipient', label: 'Recipient', name: 'Recipient', role: 'dimension', type: 'category'},
  {id: 'zip5', label: 'Contributor ZIP', name: 'Contributor ZIP', role: 'dimension', type: 'category',
    geographic_role: 'zip_code'},
  {id: 'state', label: 'Contributor state', name: 'Contributor state', role: 'dimension', type: 'category',
    geographic_role: 'state'},
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
    scope: 'Board browser-test fixture. Not real collection data.'
  }
};
const CREDIT_UNION = Object.assign({}, BANKING, {
  id: 'Credit Union', industry: 'Credit Union', label: 'Credit Union',
  provenance: Object.assign({}, BANKING.provenance, {
    workbook: 'Credit Union Industry Spending Overview.xlsx', workbook_sha256: 'c'.repeat(64),
    database_sha256: 'c'.repeat(64), payload_sha256: 'c'.repeat(64)})
});
const REGISTRY_ENTRIES = snapshotRegistry.examples[0].snapshots;
// Each grouping gets its own row count and its own total, so two tiles that group
// differently cannot accidentally print the same reconciliation line. Company has
// more members than one page holds, because a pager with nothing to page through
// is what hid the board's worst defect.
const MEMBERS = {
  company: ['Goldman Fixture', 'Wells Fixture', 'Morgan Fixture', 'PNC Fixture',
    'Citi Fixture', 'Truist Fixture', 'Fifth Fixture', 'Ally Fixture'],
  cycle: ['2021-2022', '2023-2024', '2025-2026', '2019-2020'],
  recipient: ['Fixture Committee', 'Second Committee'],
  zip5: ['02134', '99501'],
  state: ['MA', 'AK']
};
const PAGE = 3;
// The second report answers with its own amounts, so a tile that shows a cached
// result from the first one is visible as a number, not as a subtle wrong label.
const AMOUNT = {Banking: 260398435, 'Credit Union': 111000000};
const GEOMETRY = {
  congress: '119',
  points: [{zip5: '02134', longitude: -71.13, latitude: 42.36},
    {zip5: '99501', longitude: -149.86, latitude: 61.22}],
  layers: {
    states: {type: 'FeatureCollection', features: [{type: 'Feature',
      properties: {GEOID: '25', NAME: 'Massachusetts', STUSAB: 'MA', INTPTLON: -71.5, INTPTLAT: 42.2},
      geometry: {type: 'Polygon', coordinates: [[[-73, 42], [-71, 42], [-71, 43], [-73, 42]]]}}]},
    districts: {type: 'FeatureCollection', features: []},
    zip_areas: {type: 'FeatureCollection', features: []}
  },
  coverage: {unmatched_zips: []}, warnings: [], sources: []
};

let stepNumber = 0, passes = 0, failures = 0;
function setup(label) { console.log(`SETUP: ${label}`); }
function pass(detail) { passes += 1; console.log(`PASS step ${stepNumber} - ${detail}`); }
// Every step runs and every step reports. A step that throws is one red line,
// not the end of the run, so a failing-first pass shows all of them at once.
async function step(label, body) {
  stepNumber += 1;
  console.log(`STEP ${stepNumber}: ${label}`);
  try { await body(); }
  catch (error) {
    failures += 1;
    console.log(`FAIL step ${stepNumber} - ${(error && error.message) || error}`);
  }
}

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
  const pageErrorsStore = [], consoleErrorsStore = [];
  let pageErrors = pageErrorsStore, consoleErrors = consoleErrorsStore;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-studio-board-'));
  // A report directory of this suite's own. Without one the console resolves the
  // configured folder - Paul's real published reports - and chdir()s into it.
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-studio-board-reports-'));
  // Requests the stub answers, and the hold that keeps one of them in the air.
  const queries = [];
  let holdQuery = null, holdView = null, refreshed = false;
  const released = [];
  const releaseHeld = () => { while (released.length) released.pop()(); };

  async function installRoutes(page) {
    await page.route(target => target.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url());
      const where = target.pathname;
      if (where === '/api/contributions/session') {
        return route.fulfill({json: {token: 'board-token', read_only: false,
          industry: 'Banking', industries: REGISTRY_ENTRIES}});
      }
      if (where === '/api/contributions/catalog') {
        return route.fulfill({json: {datasets: [BANKING, CREDIT_UNION], fields: COMMON_FIELDS, amount_unit: 'cents'}});
      }
      if (where === '/api/contributions/geography') {
        return route.fulfill({json: GEOMETRY});
      }
      if (where === '/api/contributions/query') {
        const body = route.request().postDataJSON() || {};
        if (holdQuery && holdQuery(body)) await new Promise(resolve => { released.push(resolve); });
        const dataset = body.dataset === 'Credit Union' ? CREDIT_UNION : BANKING;
        const dims = body.dimensions && body.dimensions.length ? body.dimensions : ['company'];
        const members = MEMBERS[dims[0]] || MEMBERS.company;
        const offset = Number(body.offset) || 0;
        // A page is as big as the console asked for, or PAGE when it asked for
        // nothing: the console steps its offset by the sheet's group limit, and a
        // stub that pages by a different number prints labels nobody could read.
        const size = Number(body.limit) || PAGE;
        const page = members.slice(offset, offset + size);
        const made = page.map((who, i) => Object.assign(
          {},
          ...dims.map(d => ({[d]: d === dims[0] ? who : (MEMBERS[d] || members)[0]})),
          // A ZIP is only placeable beside the state it sits in, so a geographic
          // row carries both whether or not the sheet grouped by both.
          dims[0] === 'zip5' ? {state: MEMBERS.state[(offset + i) % MEMBERS.state.length]} : {},
          {amount: (AMOUNT[dataset.id] - (offset + i) * 7000000) * (refreshed ? 3 : 1),
            records: 9 - i, contributors: 9 - i}));
        queries.push({dataset: dataset.id, dims, offset, rows: made.length});
        return route.fulfill({json: {status: 'ready', rows: made,
          columns: [...dims, ...(body.measures || ['amount'])].map(id => COMMON_FIELDS.find(f => f.id === id) || {id, label: id}),
          totals: {amount: 1200318112, records: 100 + members.length, contributors: 9},
          group_count: members.length,
          offset: offset, has_more: offset + made.length < members.length,
          provenance: dataset.provenance,
          basis_label: 'Published included receipts',
          reconciliation: {conserved: true, receipt_matches_workbook: true},
          notices: [], verified_unique: false}});
      }
      if (where === '/api/report-sources') {
        return route.fulfill({json: {runs: [{id: 'published-reports', name: 'Published reports',
          state: 'completed', source_directory: 'fixture'}], report_directory: 'fixture'}});
      }
      if (where === '/api/graph') {
        // What the reconciliation strip asks for. The figures agree with the
        // query stub's totals, so the strip reads "Agree" rather than inventing
        // a difference out of an empty report folder.
        return route.fulfill({json: {
          options: {companies: [], cycles: ['2026'], unknown_tiers: false, recipients: []},
          filters: {company: '', cycle: '', tier: '', mode: 'direct', direction: 'Support'},
          totals: {receipt_groups: 8, giving: 12003181, direct: 12003181, organizations: 0,
            unresolved_candidate_links: 0},
          candidate_available: false, candidate_note: '', note: 'Board fixture.',
          reports: [], candidate_details: [], categories: [], organizations: [],
          candidates: [], companies: [], levels: []}});
      }
      if (where === '/api/graph-catalog') {
        return route.fulfill({json: {industries: [{name: 'Banking', indices: [0]}],
          unavailable_reports: [], report_directory: 'fixture', message: ''}});
      }
      if (where === '/api/cross-reference') {
        return route.fulfill({json: {available: false, status: 'empty', industry: 'Banking', message: 'stub'}});
      }
      // A saved view's sheets, held when a step asks (step 20), as a busy console
      // holds them. The request itself still reaches the real console.
      if (where === '/api/studio/views' && target.searchParams.get('id') && holdView && holdView()) {
        await new Promise(resolve => { released.push(resolve); });
      }
      // /api/studio/* and everything else reach the real console.
      return route.continue();
    });
  }

  // `argument` is forwarded to the predicate, so a wait can be about one tile
   // rather than about the board as a whole.
  async function until(target, predicate, label, diagnose, attempts = 120, argument) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await target.evaluate(predicate, argument)) return;
      await target.waitForTimeout(250);
    }
    const seen = diagnose ? await target.evaluate(diagnose) : null;
    throw new Error(label + ' never happened' + (seen ? '. Page: ' + JSON.stringify(seen) : '.'));
  }

  // The board's own reader: what is on screen, tile by tile.
  const BOARD_STATE = `() => {
    const board = document.getElementById('studio-board');
    const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
    return {
      boardHidden: !board || board.hidden,
      tiles: tiles.length,
      selected: tiles.map(tile => tile.classList.contains('is-selected')),
      titles: tiles.map(tile => (tile.querySelector('.studio-board-tile-title') || {}).textContent || ''),
      subtitles: tiles.map(tile => (tile.querySelector('.studio-board-tile-subtitle') || {}).textContent || ''),
      reconciliations: tiles.map(tile => (tile.querySelector('.studio-board-tile-status') || {}).textContent || ''),
      charts: tiles.map(tile => !!tile.querySelector('.studio-board-tile-canvas svg')),
      empties: tiles.map(tile => (tile.querySelector('.studio-board-tile-empty') || {}).textContent || ''),
      selects: tiles.map(tile => [...(tile.querySelector('.studio-board-tile-sheet') || {options: []}).options]
        .map(option => option.textContent)),
      chosen: tiles.map(tile => (tile.querySelector('.studio-board-tile-sheet') || {}).value),
      mainCanvasSvg: !!document.querySelector('#studio-canvas svg'),
      mainTitle: (document.getElementById('studio-title') || {}).textContent || '',
      pressed: [...document.querySelectorAll('#studio-board-tiles button')]
        .map(node => node.textContent.trim() + ':' + node.getAttribute('aria-pressed'))
    };
  }`;

  try {
    setup('pick a free port and launch the console server on a throwaway --data-root');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    const origin = `http://127.0.0.1:${port}`;
    browser = await withTimeout(
      chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}),
      35000, 'chromium.launch()');
    console.log(`SETUP done - server ready on port ${port}, data root ${dataRoot}`);

    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(20000);
    pageErrors = pageErrorsStore; consoleErrors = consoleErrorsStore;
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    await installRoutes(page);
    // Undo, Redo and Duplicate belong to Builder's toolbar - Canvas mode keeps only
    // Export, Views and Board, which test_studio_canvas_only_browser.cjs pins - so
    // the steps below press the control itself rather than a pixel on the Canvas.
    const press = id => page.evaluate(name => {
      const node = document.getElementById(name);
      if (!node) throw new Error('no control called ' + name);
      node.click();
    }, id);
    await page.goto(origin + '/?industry=Banking#chart-studio', {waitUntil: 'domcontentloaded'});
    await until(page, () => !!document.getElementById('studio-title'), 'the studio markup arriving');
    await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the first chart drawing',
      () => ({note: (document.getElementById('studio-note') || {}).textContent}));
    const storageBefore = await page.evaluate(() => Object.keys(localStorage).sort());
    // Read in one step and asserted in another, so they live outside the steps.
    let two = null, reopened = null;
    const urlBefore = page.url();

    setup('a second sheet grouped by Election cycle, so the board has two different charts');
    await page.click('#studio-new-sheet');
    await page.evaluate(() => {
      const pill = [...document.querySelectorAll('#studio-dimensions .studio-pill')]
        .find(node => node.textContent.includes('Election cycle'));
      if (!pill) throw new Error('no Election cycle dimension pill');
      pill.click();
    });
    await page.evaluate(() => {
      const pill = [...document.querySelectorAll('#studio-measures .studio-pill')]
        .find(node => node.textContent.includes('Selected amount'));
      if (!pill) throw new Error('no Selected amount measure pill');
      pill.click();
    });
    await until(page, () => /Election cycle/.test((document.getElementById('studio-title') || {}).textContent || ''),
      'the second sheet drawing its own chart',
      () => ({title: (document.getElementById('studio-title') || {}).textContent}));

    /* --------------------------------------------------------------- 1 */
    await step('Canvas mode carries a Board control of 1, 2 and 4, and 2 draws two tiles', async () => {
      const read = () => page.evaluate(visibleSource => {
        const visible = eval(visibleSource);
        const host = document.getElementById('studio-board-tiles');
        return {
          present: !!host,
          visible: visible(host),
          labels: [...(host ? host.querySelectorAll('button') : [])].map(node => node.textContent.trim()),
          legend: (host && host.querySelector('.studio-board-label') || {}).textContent || '',
          // Toolbar children are centred and differ in height, so a row is measured
          // by the toolbar's own height against its tallest control, not by tops.
          toolbarHeight: Math.round(document.querySelector('.studio-toolbar').getBoundingClientRect().height),
          tallestControl: Math.round(Math.max(...[...document.querySelector('.studio-toolbar').children]
            .map(node => node.getBoundingClientRect().height)))
        };
      }, VISIBLE_SOURCE);
      // Builder's toolbar is the one the 2026-09-17 clean-up was reviewed against,
      // so the board's control lives where the board does: on the Canvas.
      const inBuilder = await read();
      assert.equal(inBuilder.present, true, 'the Board control must exist in the toolbar');
      assert.equal(inBuilder.visible, false, 'Builder mode does not carry it');
      assert.ok(inBuilder.toolbarHeight < inBuilder.tallestControl * 2,
        `Builder's toolbar stays one row: ${inBuilder.toolbarHeight}px for a ${inBuilder.tallestControl}px control`);
      await page.click('#studio-view-canvas');
      const control = await read();
      assert.equal(control.visible, true, 'Canvas mode carries it');
      assert.deepEqual(control.labels, ['1', '2', '4'], 'the Board control offers 1, 2 and 4');
      assert.equal(control.legend, 'Board', 'under the word a reader can look for');
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'two tiles drawing two charts',
        () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length}));
      two = await page.evaluate(eval(BOARD_STATE));
      assert.equal(two.tiles, 2, 'two tiles');
      assert.deepEqual(two.charts, [true, true], 'both tiles drew a chart');
      assert.notEqual(two.titles[0].trim(), two.titles[1].trim(),
        `two tiles must show two different titles, got ${JSON.stringify(two.titles)}`);
      assert.ok(two.titles.every(title => title.trim().length > 0), 'each tile names its own chart');
      for (const line of two.reconciliations) {
        assert.match(line, /reconcile/i, `each tile keeps its reconciliation line, got "${line}"`);
        assert.match(line, /groups/, `and its own group count, got "${line}"`);
      }
      assert.notEqual(two.reconciliations[0], two.reconciliations[1],
        'each tile reconciles its own figures, not the other tile\'s');
      assert.equal(two.selected.filter(Boolean).length, 1, 'exactly one tile is selected');
      assert.ok(two.subtitles.every(text => /Banking/.test(text)), 'each tile carries its scope subtitle');
      assert.equal(two.mainCanvasSvg, false, 'the single canvas steps aside while the board is up');
      pass(`Builder toolbar ${inBuilder.toolbarHeight}px (one row), tiles ${JSON.stringify(two.titles)}, `
        + `reconciliation ${JSON.stringify(two.reconciliations)}`);
    });

    /* --------------------------------------------------------------- 2 */
    await step('a sheet already on the board is not offered to another tile', async () => {
      const offered = await page.evaluate(eval(BOARD_STATE));
      assert.equal(offered.selects.length, 2, 'each tile has its own sheet select');
      const firstChoice = offered.chosen[0], secondChoice = offered.chosen[1];
      assert.notEqual(firstChoice, secondChoice, 'the two tiles hold two different sheets');
      const repeats = await page.evaluate(() => {
        const values = tile => [...tile.querySelector('.studio-board-tile-sheet').options]
          .map(option => option.value).filter(value => value !== '');
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const chosen = tiles.map(tile => tile.querySelector('.studio-board-tile-sheet').value);
        return tiles.map((tile, index) => values(tile).filter(value =>
          chosen.some((other, at) => at !== index && other === value)));
      });
      assert.deepEqual(repeats, [[], []],
        `no tile may offer a sheet another tile already holds, got ${JSON.stringify(repeats)}`);
      pass(`tile sheets ${JSON.stringify(offered.chosen)}, no select offers the other tile's sheet`);
    });

    /* --------------------------------------------------------------- 3 */
    await step('the selected tile drives the Inspector, Export and Add to briefing', async () => {
      await page.click('#studio-board .studio-board-tile:nth-child(2) .studio-board-tile-canvas');
      await until(page, () => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        return tiles.length === 2 && tiles[1].classList.contains('is-selected');
      }, 'the second tile becoming the selected one');
      const followed = await page.evaluate(() => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const chart = window.MentatStudioPane.currentChart();
        const select = tiles[1].querySelector('.studio-board-tile-sheet');
        return {
          selected: tiles.map(tile => tile.classList.contains('is-selected')),
          tileTitle: (tiles[1].querySelector('.studio-board-tile-title') || {}).textContent || '',
          tileSheet: select.options[select.selectedIndex].textContent,
          otherTitle: (tiles[0].querySelector('.studio-board-tile-title') || {}).textContent || '',
          chartTitle: chart.title,
          chartSheet: chart.sheetName,
          chartNote: chart.note,
          hasSvg: !!chart.svg,
          shelfPills: [...document.querySelectorAll('#studio-columns .studio-pill')].map(node => node.textContent.trim())
        };
      });
      assert.deepEqual(followed.selected, [false, true], 'the clicked tile is the selected one');
      assert.equal(followed.chartTitle.trim(), followed.tileTitle.trim(),
        'Export and Add to briefing read the selected tile\'s chart');
      assert.notEqual(followed.chartTitle.trim(), followed.otherTitle.trim(),
        'and not the tile beside it');
      assert.equal(followed.chartSheet, followed.tileSheet, 'and name that tile\'s sheet');
      assert.equal(followed.hasSvg, true, 'and its drawing, not an empty canvas');
      // "Selected amount by Company" names the dimension the shelves must hold.
      const grouping = followed.tileTitle.split(' by ').pop().trim();
      assert.ok(followed.shelfPills.some(text => text.includes(grouping)),
        `the Inspector and shelves follow the selected tile (${grouping}), got ${JSON.stringify(followed.shelfPills)}`);
      pass(`selected tile "${followed.tileTitle}" (${followed.tileSheet}), currentChart "${followed.chartTitle}"`);
    });

    /* --------------------------------------------------------------- 4 */
    await step('four tiles: two filled, two honest empty tiles with their own sheet select', async () => {
      await page.click('#studio-board-4');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 4,
        'four tiles appearing');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'the two filled tiles drawing');
      const four = await page.evaluate(eval(BOARD_STATE));
      assert.equal(four.tiles, 4, 'four tiles');
      assert.deepEqual(four.charts, [true, true, false, false], 'the two filled tiles drew, the two empty did not');
      assert.equal(four.selected.filter(Boolean).length, 1, 'still exactly one selected tile');
      for (const index of [2, 3]) {
        assert.ok(four.empties[index].trim().length > 0,
          `an empty tile says so in words, got "${four.empties[index]}"`);
        // Both sheets are already on the board, so the no-repeat rule leaves an
        // empty tile with its invitation and nothing it is allowed to offer.
        assert.deepEqual(four.selects[index], ['Choose a sheet'],
          `an empty tile keeps its sheet select and offers no placed sheet, got ${JSON.stringify(four.selects[index])}`);
      }
      const grid = await page.evaluate(() => getComputedStyle(document.getElementById('studio-board')).gridTemplateColumns);
      assert.equal(grid.split(' ').length, 2, `four tiles are two by two, got "${grid}"`);
      pass(`four tiles, charts ${JSON.stringify(four.charts)}, empty text "${four.empties[2].trim()}", grid ${grid}`);
    });

    /* --------------------------------------------------------------- 5 */
    await step('save a two-tile board, and the console keeps the board the contract describes', async () => {
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 2,
        'the board returning to two tiles');
      // What is on screen is what has to reach the console: the slots are read
      // off the tiles rather than assumed, because which sheet sits where is the
      // reader's arrangement, not this test's.
      const onScreen = await page.evaluate(() => [...document.querySelectorAll('#studio-board .studio-board-tile')]
        .map(tile => { const value = tile.querySelector('.studio-board-tile-sheet').value; return value === '' ? null : Number(value); }));
      const saved = await saveNamed(page, 'Board two tiles');
      assert.ok(saved.id, `Save view must reach the console, got ${JSON.stringify(saved)}`);
      assert.deepEqual(saved.board, {tiles: 2, slots: onScreen},
        `the saved view carries the board that is on screen, got ${JSON.stringify(saved.board)}`);
      assert.equal(saved.sheets, 2, 'with both sheets');
      pass(`saved view ${saved.id} board ${JSON.stringify(saved.board)}`);
    });

    /* --------------------------------------------------------------- 6 */
    await step('Board 1 is today\'s canvas again, with the selected tile\'s sheet on it', async () => {
      const before1 = await page.evaluate(eval(BOARD_STATE));
      const keptTitle = before1.titles[before1.selected.indexOf(true)];
      await page.click('#studio-board-1');
      await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the single canvas drawing again');
      const single = await page.evaluate(eval(BOARD_STATE));
      assert.equal(single.boardHidden, true, 'the board is put away');
      assert.equal(single.mainCanvasSvg, true, 'the single canvas is drawing');
      assert.equal(single.mainTitle.trim(), keptTitle.trim(),
        `the selected tile's sheet stays on the canvas, got "${single.mainTitle}" for "${keptTitle}"`);
      pass(`Board 1 restored the single canvas with "${single.mainTitle}"`);
    });

    /* --------------------------------------------------------------- 7 */
    await step('a view saved with no board opens as one sheet, and the board view reopens into the same slots', async () => {
      const plain = await saveNamed(page, 'No board at all');
      assert.equal(plain.board, undefined, `a one-tile board writes no board key, got ${JSON.stringify(plain.board)}`);
      await openNamed(page, 'Board two tiles');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'the saved board reopening with two drawn tiles',
        () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
          note: (document.getElementById('studio-note') || {}).textContent}));
      reopened = await page.evaluate(eval(BOARD_STATE));
      assert.equal(reopened.tiles, 2, 'the board came back with two tiles');
      assert.deepEqual(reopened.titles.map(t => t.trim()), two.titles.map(t => t.trim()),
        'the same sheets came back in the same slots');
      await openNamed(page, 'No board at all');
      await until(page, () => !!document.querySelector('#studio-canvas svg'),
        'the boardless view opening as one sheet');
      const asOneSheet = await page.evaluate(eval(BOARD_STATE));
      assert.equal(asOneSheet.boardHidden, true, 'a view without a board opens on the single canvas');
      pass(`board view reopened as ${JSON.stringify(reopened.titles)}, the boardless view as one sheet`);
    });

    /* --------------------------------------------------------------- 8 */
    await step('"Add board to briefing" adds one card per filled tile, in slot order', async () => {
      await openNamed(page, 'Board two tiles');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'the board back on screen for the briefing');
      const briefingBefore = await page.evaluate(() => window.MentatBriefing.list().length);
      await page.evaluate(() => {
        document.getElementById('studio-views').open = true;
        document.getElementById('studio-board-briefing').click();
      });
      await until(page, () => window.MentatBriefing.list().length >= 2, 'two briefing cards arriving',
        () => ({notice: (document.getElementById('studio-briefing-notice') || {}).textContent}));
      const cards = await page.evaluate(() => window.MentatBriefing.list().map(item => ({
        title: item.title, sheetName: item.sheetName, hasSvg: !!item.svg})));
      const added = cards.slice(briefingBefore);
      assert.equal(added.length, 2, `one card per filled tile, got ${JSON.stringify(added)}`);
      assert.deepEqual(added.map(card => card.title.trim()), reopened.titles.map(title => title.trim()),
        'the cards arrive in slot order, each identical to that tile\'s chart');
      assert.ok(added.every(card => card.hasSvg), 'each card carries its own drawing');
      pass(`briefing cards ${JSON.stringify(added.map(card => card.title))}`);
    });

    /* --------------------------------------------------------------- 9 */
    await step('the board is nowhere in the URL and adds no storage key, and nothing raised an error', async () => {
      const storageAfter = await page.evaluate(() => Object.keys(localStorage).sort());
      assert.deepEqual(storageAfter, storageBefore,
        `the board must add no storage key, got ${JSON.stringify(storageAfter)} for ${JSON.stringify(storageBefore)}`);
      // Opening a saved view writes `saved` (S5, url_scope.json): that key is
      // the view's id and says nothing about the board. Every other key must be
      // the one it was, and no key names a board, a tile or a slot.
      const keysOf = href => [...new URL(href).searchParams.keys()].sort();
      const after = keysOf(page.url()), before = keysOf(urlBefore);
      assert.deepEqual(after.filter(key => key !== 'saved'), before,
        `nothing about the board enters the query string, got ${page.url()}`);
      assert.ok(!/board|tile|slot/i.test(page.url()), `nor the hash, got ${page.url()}`);
      assert.equal(new URL(page.url()).hash, '#chart-studio', 'the hash still names the tab and nothing else');
      assert.deepEqual(pageErrors, [], `the page raised ${JSON.stringify(pageErrors)}`);
      assert.deepEqual(consoleErrors, [], `the console logged ${JSON.stringify(consoleErrors)}`);
      pass(`URL ${page.url()}, storage keys ${JSON.stringify(storageAfter)}, no errors`);
    });

    /* -------------------------------------------------------------- 10 */
    await step('the contract the board saves against is v3 and names the board producers', async () => {
      assert.equal(studioApi.version, 3, 'contracts/studio_api.json is at v3');
      for (const name of ['views_save_board', 'views_get_board', 'views_save_board_four']) {
        assert.ok(studioApi.examples[name], `the contract carries ${name}`);
      }
      assert.ok(studioApi.produced_by.some(entry => /normalizeBoard/.test(entry)), 'normalizeBoard is a named producer');
      assert.ok(studioApi.consumed_by.some(entry => /renderBoard/.test(entry)), 'renderBoard is a named consumer');
      pass(`studio_api v${studioApi.version}, ${Object.keys(studioApi.examples).length} examples, `
        + `${studioApi.negative_controls.length} controls`);
    });


    /* -------------------------------------------------------------- 11 */
    await step('the group pager moves the selected tile, and only that tile', async () => {
      await openNamed(page, 'Board two tiles');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'the two-tile board back on screen');
      const readBoard = () => page.evaluate(() => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        return {
          selected: tiles.map(tile => tile.classList.contains('is-selected')),
          drawn: tiles.map(tile => (tile.querySelector('.studio-board-tile-canvas svg') || {}).textContent || ''),
          label: (document.getElementById('studio-group-count') || {}).textContent || '',
          mainSvg: !!document.querySelector('#studio-canvas svg'),
          nextDisabled: (document.getElementById('studio-groups-next') || {}).disabled,
          caption: window.MentatStudioPane.currentChart().scope
        };
      });
      // The company sheet has eight members; three to a page leaves a second page
      // to go to. That tile is the one to select.
      await page.evaluate(() => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const wanted = tiles.find(tile => /by Company/.test(
          (tile.querySelector('.studio-board-tile-title') || {}).textContent || ''));
        wanted.querySelector('.studio-board-tile-canvas').click();
      });
      await until(page, () => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        return tiles.some(tile => tile.classList.contains('is-selected')
          && /by Company/.test((tile.querySelector('.studio-board-tile-title') || {}).textContent || ''));
      }, 'the company tile becoming the selected one');
      await page.evaluate(() => {
        const limit = document.getElementById('studio-param-groups');
        limit.value = '3';
        limit.dispatchEvent(new Event('change'));
      });
      // r5 merge: wait for page one of three, not any "of 8". The tile's label read
      // "Groups 1-8 of 8" (next disabled) until the three-group answer arrived, so
      // /of 8/ matched the state before the change and step 11 failed whenever that
      // answer was slow (reproduced by holding it 1.5 s: final/r5/men-merge/race).
      await until(page, () => /1.{1,3}3 of 8/.test((document.getElementById('studio-group-count') || {}).textContent || ''),
        'the selected tile paging three groups at a time',
        () => ({label: (document.getElementById('studio-group-count') || {}).textContent}));
      const first = await readBoard();
      const at = first.selected.indexOf(true), other = at === 0 ? 1 : 0;
      assert.equal(first.nextDisabled, false, 'there is a second page to go to');
      assert.match(first.label, /1.{1,3}3 of 8/, 'page one is on screen, got "' + first.label + '"');
      assert.match(first.caption, /Groups 1.{1,3}3 of 8/, 'and the export caption agrees, got "' + first.caption + '"');
      await page.click('#studio-groups-next');
      await until(page, () => [...document.querySelectorAll('#studio-board .studio-board-tile-canvas svg')]
        .some(svg => svg.textContent.includes('PNC Fixture')), 'the selected tile drawing page two',
        () => ({label: (document.getElementById('studio-group-count') || {}).textContent,
          drawn: [...document.querySelectorAll('#studio-board .studio-board-tile-canvas svg')]
            .map(svg => svg.textContent.slice(0, 60))}));
      const second = await readBoard();
      assert.notEqual(second.drawn[at], first.drawn[at], 'the selected tile draws the page the reader asked for');
      assert.equal(second.drawn[other], first.drawn[other], 'the tile beside it is untouched');
      assert.equal(second.mainSvg, false, 'nothing is drawn into the canvas the board replaced');
      assert.match(second.label, /4.{1,3}6 of 8/, 'the label names the page that is drawn, got "' + second.label + '"');
      assert.ok(second.drawn[at].includes('PNC Fixture'),
        'page two members are the ones on screen, got "' + second.drawn[at].slice(0, 80) + '"');
      assert.match(second.caption, /Groups 4.{1,3}6 of 8/,
        'an exported image carries the caption of the page that is drawn, got "' + second.caption + '"');
      // The other tile keeps its own page: selecting it shows page one again.
      assert.ok(at >= 0, 'a tile is selected before the pager step, got ' + JSON.stringify(first.selected));
      await page.evaluate(index => {
        const tiles = document.querySelectorAll('#studio-board .studio-board-tile');
        if (!tiles[index]) throw new Error('tile ' + index + ' is gone; the board has ' + tiles.length);
        tiles[index].querySelector('.studio-board-tile-canvas').click();
      }, other);
      await until(page, index => {
        const tiles = document.querySelectorAll('#studio-board .studio-board-tile');
        return !!tiles[index] && tiles[index].classList.contains('is-selected');
      }, 'the other tile becoming the selected one',
        () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
          titles: [...document.querySelectorAll('.studio-board-tile-title')].map(node => node.textContent)}),
        40, other);
      const back = await readBoard();
      assert.match(back.label, /1.{1,3}[0-9] of/, 'each tile keeps its own page, got "' + back.label + '"');
      pass('page one "' + first.label + '" then page two "' + second.label + '", the other tile untouched, caption "'
        + second.caption.slice(0, 44) + '"');
    });

    /* -------------------------------------------------------------- 12 */
    // Round 5 (census B-S29; Paul, 2026-09-23: "nothing is logically constrained"): every tile
    // holds its own map, so a map tile keeps drawing when another tile is selected.
    await step('a map sheet draws in its tile, selected or not', async () => {
      await page.click('#studio-board-1');
      await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the single canvas back');
      await page.click('#studio-new-sheet');
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-dimensions .studio-pill')]
          .find(node => node.textContent.includes('Contributor ZIP'));
        if (!pill) throw new Error('no Contributor ZIP dimension pill');
        pill.click();
      });
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-measures .studio-pill')]
          .find(node => node.textContent.includes('Selected amount'));
        pill.click();
      });
      await page.evaluate(() => {
        const mark = document.getElementById('studio-mark');
        mark.value = 'map';
        mark.dispatchEvent(new Event('change'));
      });
      await until(page, () => !!document.querySelector('#studio-canvas svg'),
        'the map drawing on the single canvas',
        () => ({note: (document.getElementById('studio-note') || {}).textContent}));
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 2,
        'the board coming back with the map sheet on it');
      await until(page, () => {
        const chosen = [...document.querySelectorAll('#studio-board .studio-board-tile')]
          .find(tile => tile.classList.contains('is-selected'));
        return !!(chosen && chosen.querySelector('.studio-board-tile-canvas svg'));
      }, 'the selected tile drawing its map',
        () => ({
          selected: [...document.querySelectorAll('#studio-board .studio-board-tile')]
            .map(tile => tile.classList.contains('is-selected')),
          titles: [...document.querySelectorAll('.studio-board-tile-title')].map(node => node.textContent),
          svgs: [...document.querySelectorAll('#studio-board .studio-board-tile')]
            .map(tile => tile.querySelectorAll('svg').length),
          maps: document.querySelectorAll('#studio-board .contribution-map').length,
          mainMap: document.querySelectorAll('#studio-canvas .contribution-map').length,
          loading: (document.getElementById('studio-loading') || {}).textContent}));
      const mapState = await page.evaluate(() => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const at = tiles.findIndex(tile => tile.classList.contains('is-selected'));
        return {
          drew: !!tiles[at].querySelector('.studio-board-tile-canvas svg'),
          shapes: tiles[at].querySelectorAll('.studio-board-tile-canvas circle, .studio-board-tile-canvas path').length,
          loading: (document.getElementById('studio-loading') || {}).textContent || ''
        };
      });
      assert.equal(mapState.drew, true, 'the selected tile drew its map rather than waiting for ever');
      assert.ok(mapState.shapes > 0, 'with boundaries and marks on it, got ' + mapState.shapes);
      assert.doesNotMatch(mapState.loading, /Loading Census boundaries/,
        'and the console is not still loading, got "' + mapState.loading + '"');
      await page.evaluate(() => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const at = tiles.findIndex(tile => tile.classList.contains('is-selected'));
        tiles[at === 0 ? 1 : 0].querySelector('.studio-board-tile-canvas').click();
      });
      await until(page, () => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const mapTile = tiles.find(tile => tile.querySelector('.contribution-map'));
        return !!mapTile && !mapTile.classList.contains('is-selected');
      }, 'the map tile, no longer selected, still drawing its own map',
        () => ({selected: [...document.querySelectorAll('#studio-board .studio-board-tile')].map(tile => tile.classList.contains('is-selected')),
          maps: document.querySelectorAll('#studio-board .contribution-map').length,
          notes: [...document.querySelectorAll('.studio-board-tile-note')].map(node => node.textContent)}));
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('#studio-board .studio-board-tile-note')]
        .some(node => /select this tile/i.test(node.textContent))), false, 'no tile is told to be selected before it draws');
      pass('the map drew ' + mapState.shapes + ' shapes in the selected tile and kept drawing once another tile was selected');
    });

    /* -------------------------------------------------------------- 13 */
    await step('a board change is an edit: Undo steps through it and never drops it', async () => {
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 2,
        'two tiles for the undo step');
      const boardNow = () => page.evaluate(() => ({
        tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
        pressed: [...document.querySelectorAll('#studio-board-tiles button')]
          .filter(node => node.getAttribute('aria-pressed') === 'true').map(node => node.textContent.trim()),
        labels: document.getElementById('studio-labels').getAttribute('aria-pressed')
      }));
      const withBoard = await boardNow();
      assert.equal(withBoard.tiles, 2, 'the board is up before the unrelated edit');
      // An edit that has nothing to do with the board: mark labels on the selected
      // tile's sheet. One commit, one undo step, plainly readable either way.
      await page.evaluate(() => document.getElementById('studio-labels').click());
      await until(page, was => document.getElementById('studio-labels').getAttribute('aria-pressed') !== was,
        'the unrelated edit taking effect', null, 40, withBoard.labels);
      await press('studio-undo');
      await page.waitForTimeout(400);
      const afterOne = await boardNow();
      assert.equal(afterOne.tiles, 2, 'one Undo of an unrelated edit leaves the board standing');
      assert.deepEqual(afterOne.pressed, ['2'], 'and the Board control still says 2');
      assert.equal(afterOne.labels, withBoard.labels, 'while the edit itself is undone');
      await press('studio-undo');
      await page.waitForTimeout(400);
      const afterTwo = await boardNow();
      assert.ok(afterTwo.tiles < 2 || afterTwo.pressed[0] !== '2',
        'the next Undo steps back through the board change itself, got ' + JSON.stringify(afterTwo));
      // The discriminator. Undoing a board change looks the same whether or not the
      // change was ever in the history - the board goes either way. Only a change
      // that entered the history can be brought back.
      await press('studio-redo');
      await page.waitForTimeout(400);
      const redone = await boardNow();
      assert.equal(redone.tiles, 2, 'Redo brings the board change back');
      assert.deepEqual(redone.pressed, ['2'], 'with the tile count it had');
      // The sharp one: an edit, then a board change, then ONE Undo. The board change
      // is its own step, so the edit before it survives. A board change that never
      // entered the history takes the edit before it down with it.
      const labelsBefore = (await boardNow()).labels;
      await page.evaluate(() => document.getElementById('studio-labels').click());
      await until(page, was => document.getElementById('studio-labels').getAttribute('aria-pressed') !== was,
        'the edit before the board change', null, 40, labelsBefore);
      const labelsOn = (await boardNow()).labels;
      await page.click('#studio-board-4');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 4,
        'four tiles after the edit');
      await press('studio-undo');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 2,
        'one Undo stepping back through the board change alone',
        () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
          labels: document.getElementById('studio-labels').getAttribute('aria-pressed')}));
      const afterBoardUndo = await boardNow();
      assert.equal(afterBoardUndo.labels, labelsOn,
        'the edit made before the board change survives that one Undo');
      // And once more, from a board that is the only thing that changed.
      await page.click('#studio-board-4');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 4,
        'four tiles for the lone board change');
      await press('studio-undo');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length !== 4,
        'the lone board change being undone', () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length}));
      await press('studio-redo');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 4,
        'Redo bringing the four tiles back',
        () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
          pressed: [...document.querySelectorAll('#studio-board-tiles button')]
            .map(node => node.textContent.trim() + ':' + node.getAttribute('aria-pressed'))}));
      const four = await boardNow();
      assert.equal(four.tiles, 4, 'a board change on its own is one undo step, and Redo returns it');
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 2,
        'back to two tiles for the steps that follow');
      pass('Undo kept the board (' + afterOne.tiles + ' tiles), stepped through it ('
        + afterTwo.tiles + '), and Redo returned both it and a lone 4-tile change');
    });

    /* -------------------------------------------------------------- 14 */
    await step('two tiles asking the same question: changing one never blanks the other', async () => {
      // Two tiles holding a sheet and its copy, whose queries are identical.
      await page.click('#studio-board-1');
      await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the single canvas for the setup');
      await page.click('#studio-new-sheet');
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-dimensions .studio-pill')]
          .find(node => node.textContent.includes('Election cycle'));
        pill.click();
      });
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-measures .studio-pill')]
          .find(node => node.textContent.includes('Selected amount'));
        pill.click();
      });
      await until(page, () => /Election cycle/.test((document.getElementById('studio-title') || {}).textContent || ''),
        'the cycle sheet drawing',
        () => ({title: (document.getElementById('studio-title') || {}).textContent,
          note: (document.getElementById('studio-note') || {}).textContent}));
      // The sheet this copy came from, by name, so the second tile can be put onto
      // it deliberately rather than onto whatever the board offered first.
      const originalName = await page.evaluate(() => {
        const tab = [...document.querySelectorAll('#studio-sheet-tabs button')]
          .find(node => node.getAttribute('aria-selected') === 'true');
        return tab ? tab.textContent : '';
      });
      await press('studio-duplicate');
      await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the duplicate drawing');
      const copyName = await page.evaluate(() => {
        const tab = [...document.querySelectorAll('#studio-sheet-tabs button')]
          .find(node => node.getAttribute('aria-selected') === 'true');
        return tab ? tab.textContent : '';
      });
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile').length === 2,
        'two tiles for the shared-request step');
      // The sheet and its copy are the pair whose queries are identical. Whichever
      // of the two a tile already holds, the tile beside it takes the other.
      await page.evaluate(pair => {
        const tiles = [...document.querySelectorAll('#studio-board .studio-board-tile')];
        const held = tiles.map(tile => {
          const select = tile.querySelector('.studio-board-tile-sheet');
          return (select.options[select.selectedIndex] || {}).textContent || '';
        });
        if (pair.every(name => held.includes(name))) return;
        const wanted = pair.find(name => !held.includes(name));
        const spare = tiles.find((tile, at) => !pair.includes(held[at])
          && [...tile.querySelector('.studio-board-tile-sheet').options]
            .some(option => option.textContent === wanted));
        if (!spare) throw new Error('no tile can take "' + wanted + '"; tiles hold ' + JSON.stringify(held));
        const select = spare.querySelector('.studio-board-tile-sheet');
        select.value = [...select.options].find(option => option.textContent === wanted).value;
        select.dispatchEvent(new Event('change'));
      }, [originalName, copyName]);
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'both tiles drawing before the shared request is held',
        () => ({titles: [...document.querySelectorAll('.studio-board-tile-title')].map(node => node.textContent)}));
      const pairedTitles = await page.evaluate(() =>
        [...document.querySelectorAll('#studio-board .studio-board-tile-title')].map(node => node.textContent));
      // What the tiles are showing now. A tile whose shared answer was cancelled
      // under it does not go blank - the AbortError is swallowed and this very
      // drawing stays on screen - so this is what the step compares against.
      const beforeRefresh = await page.evaluate(() =>
        [...document.querySelectorAll('#studio-board .studio-board-tile')]
          .map(tile => (tile.querySelector('.studio-board-tile-canvas svg') || {}).textContent || ''));
      // Without this the step would pass on two tiles asking two questions, which
      // is not the case the defect lives in.
      assert.equal(pairedTitles[0], pairedTitles[1],
        'both tiles must be asking the console the same question, got ' + JSON.stringify(pairedTitles));
      // From here the console must ask again rather than answer from its cache, and
      // the answer both tiles are waiting for is held in the air.
      holdQuery = body => (body.dimensions || []).length === 1 && (body.dimensions || [])[0] === 'cycle';
      // The answer that comes back after the refresh is visibly a different answer,
      // so a tile that never received it is a tile still showing the old one. A
      // cancelled shared request does not blank a tile: the AbortError is swallowed
      // and the old drawing stays, which is what this reads.
      refreshed = true;
      await press('studio-refresh-data');
      await page.waitForTimeout(1500);
      // One tile changes its question while that shared answer is still in flight.
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-dimensions .studio-pill')]
          .find(node => node.textContent.includes('Recipient'));
        pill.click();
      });
      await page.waitForTimeout(500);
      // Stop holding before letting go, or a render that starts between the two
      // queues a request nobody will ever release.
      holdQuery = null;
      releaseHeld();
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'both tiles drawing after one of them changed its question',
        () => ({drawn: [...document.querySelectorAll('#studio-board .studio-board-tile')]
          .map(tile => !!tile.querySelector('.studio-board-tile-canvas svg')),
          titles: [...document.querySelectorAll('.studio-board-tile-title')].map(node => node.textContent),
          busy: [...document.querySelectorAll('.studio-board-tile-canvas')]
            .map(node => node.getAttribute('aria-busy')),
          error: (document.getElementById('studio-error') || {}).textContent,
          mainSvg: document.querySelectorAll('#studio-canvas svg').length,
          anySvg: document.querySelectorAll('#view-chart-studio svg').length,
          tileKids: [...document.querySelectorAll('.studio-board-tile-canvas')]
            .map(node => node.children.length + ':' + (node.firstElementChild || {}).tagName),
          notes: [...document.querySelectorAll('.studio-board-tile-note')]
            .map(node => node.textContent.slice(-70))}));
      const drawn = await page.evaluate(() => [...document.querySelectorAll('#studio-board .studio-board-tile')]
        .map(tile => ({drew: !!tile.querySelector('.studio-board-tile-canvas svg'),
          title: (tile.querySelector('.studio-board-tile-title') || {}).textContent || '',
          text: (tile.querySelector('.studio-board-tile-canvas svg') || {}).textContent || ''})));
      assert.deepEqual(drawn.map(tile => tile.drew), [true, true],
        'the tile that did not change still has its answer, got ' + JSON.stringify(drawn));
      const at = drawn.findIndex(tile => /Election cycle$/.test(tile.title.trim()));
      assert.ok(at >= 0, 'the tile that did not change is still on its own sheet, got ' + JSON.stringify(drawn));
      assert.notEqual(drawn[at].text, beforeRefresh[at],
        'that tile received the refreshed answer rather than keeping the drawing it already had');
      refreshed = false;
      pass('both tiles kept their drawing and the untouched one shows the refreshed answer '
        + JSON.stringify(pairedTitles));
    });

    /* -------------------------------------------------------------- 15 */
    await step('a new report starts on its own first page, not the last one', async () => {
      const money = /2,603,984|2\.6M/;
      const drawnText = () => page.evaluate(() =>
        [...document.querySelectorAll('#studio-board .studio-board-tile-canvas svg, #studio-canvas svg')]
          .map(svg => svg.textContent));
      // Put the selected tile on a later page of this report first: what a tile
      // remembers is keyed by its sheet, and a fresh report's sheets carry the same
      // ids, so an unkeyed memory would hand them this page.
      await page.evaluate(() => {
        const limit = document.getElementById('studio-param-groups');
        limit.value = '3';
        limit.dispatchEvent(new Event('change'));
      });
      await until(page, () => /of /.test((document.getElementById('studio-group-count') || {}).textContent || ''),
        'the selected tile paging three at a time');
      await page.click('#studio-groups-next');
      await until(page, () => !/^Groups 1.{1,3}3 /.test((document.getElementById('studio-group-count') || {}).textContent || ''),
        'the selected tile leaving its first page',
        () => ({label: (document.getElementById('studio-group-count') || {}).textContent}));
      const movedOn = await page.evaluate(() => (document.getElementById('studio-group-count') || {}).textContent);
      const banking = await drawnText();
      assert.ok(banking.some(text => money.test(text)) || /of /.test(movedOn),
        'the first report is on a later page before the switch, got "' + movedOn + '"');
      await page.evaluate(() => {
        const picker = document.getElementById('studio-dataset');
        picker.value = 'Credit Union';
        picker.dispatchEvent(new Event('change'));
      });
      // The new report has arrived when the console says so and something is drawn,
      // which is a signal this step can wait for instead of a fixed pause.
      await until(page, () => /Credit Union/.test(
          [...document.querySelectorAll('#studio-scope-chip span')].map(node => node.textContent).join(' '))
        && !!document.querySelector('#studio-canvas svg, #studio-board .studio-board-tile-canvas svg'),
        'the Credit Union report drawing',
        () => ({chips: [...document.querySelectorAll('#studio-scope-chip span')].map(node => node.textContent),
          note: (document.getElementById('studio-note') || {}).textContent}));
      // A board again on the new report: its sheets are fresh and carry the same
      // ids the Banking sheets had, which is exactly the collision.
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length >= 1,
        'the new report drawing in a tile',
        () => ({titles: [...document.querySelectorAll('.studio-board-tile-title')].map(node => node.textContent)}));
      const after = await drawnText();
      const label = await page.evaluate(() => (document.getElementById('studio-group-count') || {}).textContent || '');
      assert.ok(after.length >= 1, 'something is drawn on the new report');
      assert.ok(!after.some(text => money.test(text)),
        'no tile may keep the other report figures, got '
        + JSON.stringify(after.map(text => text.slice(0, 60))));
      assert.match(label, /^Groups 1.{1,3}[0-9]+ of/,
        'the new report opens on its own first page, got "' + label + '" after "' + movedOn + '"');
      pass('the first report was on "' + movedOn + '"; the new one opens on "' + label + '"');
    });

    /* -------------------------------------------------------------- 16 */
    await step('the console this suite spawns uses a report directory of its own', async () => {
      const seen = await new Promise((resolve, reject) => {
        const request = require('node:http').get(
          {host: '127.0.0.1', port: port, path: '/api/report-sources', headers: {Host: '127.0.0.1:' + port}},
          response => {
            let body = '';
            response.on('data', chunk => { body += chunk; });
            response.on('end', () => { try { resolve(JSON.parse(body)); } catch (error) { reject(error); } });
          });
        request.on('error', reject);
      });
      const same = (a, b) => path.resolve(String(a || '')).toLowerCase() === path.resolve(String(b || '')).toLowerCase();
      assert.ok(same(seen.report_directory, reportRoot),
        'the console must read this suite own report directory (' + reportRoot + '), got "'
        + seen.report_directory + '"');
      pass('report directory ' + seen.report_directory);
    });

    /* -------------------------------------------------------------- 17 */
    await step('a board keeps its drawings while a refresh is in flight, and after Cancel', async () => {
      await page.click('#studio-board-1');
      await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the single canvas before the board');
      // This report may have arrived with one sheet, and a board needs two.
      const sheetsHere = await page.evaluate(() => document.querySelectorAll('#studio-sheet-tabs button').length);
      if (sheetsHere < 2) {
        await page.click('#studio-new-sheet');
        await page.evaluate(() => {
          const pill = [...document.querySelectorAll('#studio-dimensions .studio-pill')]
            .find(node => node.textContent.includes('Election cycle'));
          pill.click();
        });
        await page.evaluate(() => {
          const pill = [...document.querySelectorAll('#studio-measures .studio-pill')]
            .find(node => node.textContent.includes('Selected amount'));
          pill.click();
        });
        await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the second sheet drawing');
      }
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'two drawn tiles before the refresh',
        () => ({tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
          drawn: [...document.querySelectorAll('#studio-board .studio-board-tile')]
            .map(tile => !!tile.querySelector('.studio-board-tile-canvas svg'))}));
      const readTiles = () => page.evaluate(() =>
        [...document.querySelectorAll('#studio-board .studio-board-tile')].map(tile => ({
          drew: !!tile.querySelector('.studio-board-tile-canvas svg'),
          text: (tile.querySelector('.studio-board-tile-canvas svg') || {}).textContent || '',
          title: (tile.querySelector('.studio-board-tile-title') || {}).textContent || '',
          status: (tile.querySelector('.studio-board-tile-status') || {}).textContent || ''
        })));
      const before = await readTiles();
      assert.deepEqual(before.map(tile => tile.drew), [true, true], 'both tiles are drawing before the refresh');
      // Every answer is held from here, so the refresh is a load that does not finish.
      holdQuery = () => true;
      await press('studio-refresh-data');
      await page.waitForTimeout(1500);
      const during = await readTiles();
      assert.deepEqual(during.map(tile => tile.drew), [true, true],
        'both tiles still show their last drawing while the refresh is in flight, got '
        + JSON.stringify(during.map(tile => tile.drew)));
      assert.deepEqual(during.map(tile => tile.text), before.map(tile => tile.text),
        'and it is the same drawing, not a new one');
      assert.deepEqual(during.map(tile => tile.title), before.map(tile => tile.title),
        'with the titles they had');
      assert.deepEqual(during.map(tile => tile.status), before.map(tile => tile.status),
        'and their reconciliation lines');
      // Cancel says the last loaded view is showing. It has to be true on a board.
      await press('studio-progress-cancel');
      await page.waitForTimeout(500);
      const after = await readTiles();
      const said = await page.evaluate(() => (document.getElementById('studio-loading') || {}).textContent || '');
      assert.match(said, /Cancelled\. Showing the last loaded view\./,
        'the console says the last loaded view is showing, got "' + said + '"');
      assert.deepEqual(after.map(tile => tile.drew), [true, true],
        'and it is: both tiles still show their drawing after Cancel, got '
        + JSON.stringify(after.map(tile => tile.drew)));
      assert.deepEqual(after.map(tile => tile.text), before.map(tile => tile.text),
        'the same drawing the reader had before pressing Refresh');
      holdQuery = null;
      releaseHeld();
      await page.waitForTimeout(400);
      pass('during the load and after Cancel both tiles still showed "' + before[0].text.slice(0, 40) + '"');
    });

    /* -------------------------------------------------------------- 18 */
    await step('a refresh that answers the same data leaves the untouched tile byte-identical', async () => {
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'two drawn tiles before the quiet refresh');
      const readMarkup = () => page.evaluate(() =>
        [...document.querySelectorAll('#studio-board .studio-board-tile')].map(tile => ({
          selected: tile.classList.contains('is-selected'),
          svg: (tile.querySelector('.studio-board-tile-canvas svg') || {}).outerHTML || '',
          chosen: (tile.querySelector('.studio-board-tile-sheet') || {}).value
        })));
      const before = await readMarkup();
      const other = before.findIndex(tile => !tile.selected);
      assert.ok(other >= 0, 'there is an unselected tile to watch');
      await press('studio-refresh-data');
      await until(page, () => document.querySelectorAll('#studio-board .studio-board-tile-canvas svg').length === 2,
        'both tiles drawing again after the quiet refresh');
      await page.waitForTimeout(600);
      const after = await readMarkup();
      assert.equal(after[other].svg, before[other].svg,
        'the unselected tile is the same drawing, character for character');
      assert.equal(after[other].chosen, before[other].chosen, 'holding the same sheet');
      assert.deepEqual(after.map(tile => tile.selected), before.map(tile => tile.selected),
        'and the outline did not move');
      pass('the unselected tile survived a refresh unchanged (' + before[other].svg.length + ' characters)');
    });

    /* -------------------------------------------------------------- 19 */
    await step('emptying a map tile destroys the map it was hosting', async () => {
      // A spy on the map renderer, because an orphaned instance is not visible in
      // the DOM - it is the listeners it leaves behind.
      await page.evaluate(() => {
        if (window.__mapSpy) return;
        window.__mapSpy = {made: 0, destroyed: 0};
        const real = window.MentatContributionMap.render;
        window.MentatContributionMap.render = (container, options) => {
          const instance = real(container, options);
          window.__mapSpy.made += 1;
          const destroy = instance.destroy;
          instance.destroy = () => { window.__mapSpy.destroyed += 1; return destroy.call(instance); };
          return instance;
        };
      });
      await page.click('#studio-board-1');
      await until(page, () => !!document.querySelector('#studio-canvas svg'), 'the single canvas for the map sheet');
      await page.click('#studio-new-sheet');
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-dimensions .studio-pill')]
          .find(node => node.textContent.includes('Contributor ZIP'));
        pill.click();
      });
      await page.evaluate(() => {
        const pill = [...document.querySelectorAll('#studio-measures .studio-pill')]
          .find(node => node.textContent.includes('Selected amount'));
        pill.click();
      });
      await page.evaluate(() => {
        const mark = document.getElementById('studio-mark');
        mark.value = 'map';
        mark.dispatchEvent(new Event('change'));
      });
      await page.click('#studio-board-2');
      await until(page, () => document.querySelectorAll('#studio-board .contribution-map').length === 1,
        'the map drawing inside a tile',
        () => ({maps: document.querySelectorAll('.contribution-map').length,
          spy: window.__mapSpy}));
      const made = await page.evaluate(() => window.__mapSpy.destroyed);
      // Empty the tile holding the map: its nodes go, and the map has to go with them.
      await page.evaluate(() => {
        const tile = [...document.querySelectorAll('#studio-board .studio-board-tile')]
          .find(node => node.querySelector('.contribution-map'));
        if (!tile) throw new Error('no tile is hosting a map');
        const select = tile.querySelector('.studio-board-tile-sheet');
        const blank = [...select.options].find(option => option.value === '');
        if (!blank) throw new Error('the map tile cannot be emptied: '
          + [...select.options].map(option => option.textContent).join(' | '));
        select.value = '';
        select.dispatchEvent(new Event('change'));
      });
      await until(page, () => document.querySelectorAll('#studio-board .contribution-map').length === 0,
        'the map leaving the board',
        () => ({maps: document.querySelectorAll('.contribution-map').length, spy: window.__mapSpy}));
      const spy = await page.evaluate(() => window.__mapSpy);
      assert.ok(spy.destroyed > made,
        'the emptied tile destroyed the map instance it was hosting, spy ' + JSON.stringify(spy));
      pass('map instances made ' + spy.made + ', destroyed ' + spy.destroyed + '; none left on the board');
    });

    /* -------------------------------------------------------------- 20 */
    // The step-11 flake (r6, 2026-09-24): openNamed() returned before the view's
    // sheets arrived, the board already on screen satisfied the wait for a drawn
    // board, and the view landed after the group limit was changed, replacing it
    // ("Groups 1-8 of 8" for ever). A view that is opening now says so, and an
    // earlier open that answers last is dropped rather than drawn over a later one.
    await step('a view that is opening says so, and the view opened last is the one that stays', async () => {
      let asked = 0;
      // Only the first open's sheets are held: they answer after the second open.
      holdView = () => (asked += 1) === 1;
      await clickViewEntry(page, 'Board two tiles');
      const opening = await page.evaluate(() => ({
        busy: !!document.querySelector('#view-chart-studio .studio[aria-busy="true"]'),
        loading: (document.getElementById('studio-loading') || {}).textContent || ''}));
      assert.equal(opening.busy, true, 'the studio is marked busy while a view is opening');
      assert.match(opening.loading, /Opening view "Board two tiles"/,
        'and the status line names the view that is opening, got "' + opening.loading + '"');
      await openNamed(page, 'No board at all');
      await until(page, () => !!document.querySelector('#studio-canvas svg')
          && document.getElementById('studio-board').hidden,
        'the view opened last drawing on the single canvas',
        () => ({boardHidden: document.getElementById('studio-board').hidden,
          loading: (document.getElementById('studio-loading') || {}).textContent}));
      assert.equal(asked, 2, 'both opens asked the console for their sheets, got ' + asked);
      // Now the first open answers, last. It must not replace the view opened after it.
      const late = page.waitForResponse(response => /\/api\/studio\/views\?id=/.test(response.url()));
      holdView = null;
      releaseHeld();
      await (await late).finished();
      // Something that must not happen is watched for a bounded while after the
      // answer has arrived; the replacement it guards against lands within
      // milliseconds of the answer.
      const watched = [];
      for (let look = 0; look < 8; look += 1) {
        await page.waitForTimeout(200);
        watched.push(await page.evaluate(() => ({
          boardHidden: document.getElementById('studio-board').hidden,
          tiles: document.querySelectorAll('#studio-board .studio-board-tile').length,
          busy: !!document.querySelector('#view-chart-studio .studio[aria-busy="true"]'),
          loading: (document.getElementById('studio-loading') || {}).textContent || ''})));
      }
      assert.ok(watched.every(seen => seen.boardHidden && seen.tiles === 0),
        'the late answer for the board view must not replace the view opened after it, saw '
        + JSON.stringify(watched.find(seen => !seen.boardHidden || seen.tiles) || watched[watched.length - 1]));
      assert.ok(watched.every(seen => !seen.busy), 'and the studio is no longer marked busy');
      assert.ok(watched.every(seen => !/Opened view "Board two tiles"/.test(seen.loading)),
        'nor does the status line claim the dropped view opened, got "' + watched[watched.length - 1].loading + '"');
      pass('busy and "' + opening.loading + '" while opening; the late board answer was dropped, '
        + 'the single canvas stayed (' + watched.length + ' looks)');
    });
  } catch (error) {
    failed = true;
    console.error('FAIL: ' + (error && error.stack ? error.stack : error));
  } finally {
    if (browser) await browser.close().catch(() => {});
    await stopServer(server);
    await cleanupPath(dataRoot);
    await cleanupPath(reportRoot);
  }
  if (pageErrors.length) console.log(`PAGE ERRORS: ${JSON.stringify(pageErrors.slice(0, 6))}`);
  if (failed || failures) { process.exitCode = 1; console.log(`RESULT: ${passes}/${stepNumber} steps passed, exit 1`); }
  else console.log(`RESULT: ${passes}/${stepNumber} steps passed, exit 0`);

  /* The two saved-view gestures, driven exactly as a reader drives them: the
     Views menu, the name typed in place of the menu entry, Enter. */
  async function saveNamed(target, name) {
    await target.evaluate(() => { document.getElementById('studio-views').open = true; });
    await target.click('#studio-save-view');
    await target.fill('.studio-view-name', name);
    await target.press('.studio-view-name', 'Enter');
    // The save is a fetch. Wait for the console to hold the view rather than for
    // a fixed delay, and bound the wait like every other step here.
    const listing = () => target.evaluate(async () => {
      const body = await (await fetch('/api/studio/views', {credentials: 'same-origin'})).json();
      return body.views || [];
    });
    let row = null, views = [];
    for (let attempt = 0; attempt < 40 && !row; attempt += 1) {
      views = await listing();
      row = views.find(view => view.name === name) || null;
      if (!row) await target.waitForTimeout(250);
    }
    if (!row) throw new Error(`the console has no saved view called "${name}": ${JSON.stringify(views.map(v => v.name))}`
      + ' - ' + await target.evaluate(() => (document.getElementById('studio-error') || {}).textContent || ''));
    const full = await target.evaluate(async id => {
      const body = await (await fetch('/api/studio/views?id=' + encodeURIComponent(id),
        {credentials: 'same-origin'})).json();
      return body.view || {};
    }, row.id);
    return {id: row.id, board: full.board, sheets: (full.sheets || []).length, active: full.active};
  }
  // Opening a view fetches its sheets and then replaces the workspace. openNamed
  // waits for that to land - the studio is aria-busy until it has - rather than
  // for a drawn chart, because the chart on screen may be the very view being
  // reopened (step 11), and an edit made before the open lands is replaced by it.
  async function openNamed(target, name) {
    await clickViewEntry(target, name);
    await until(target, () => !document.querySelector('#view-chart-studio .studio[aria-busy="true"]'),
      'the saved view "' + name + '" opening',
      () => ({loading: (document.getElementById('studio-loading') || {}).textContent || '',
        error: (document.getElementById('studio-error') || {}).textContent || ''}), 80);
  }
  async function clickViewEntry(target, name) {
    await target.evaluate(() => { document.getElementById('studio-views').open = true; });
    await target.click('#studio-open-views');
    await until(target, () => !!document.querySelector('.studio-send button'),
      'the saved-view menu opening', null, 40);
    await target.evaluate(viewName => {
      const entries = [...document.querySelectorAll('.studio-send button')];
      const entry = entries.find(node => node.textContent.includes(viewName) && !node.disabled);
      if (!entry) throw new Error('no menu entry for ' + viewName + ': '
        + entries.map(node => node.textContent).join(' | '));
      entry.click();
    }, name);
  }
})();
