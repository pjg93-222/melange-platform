/* Forecast giving, board A (picked by Paul 2026-10-07), in a real browser: the real console page and CSS (served by console_browser_fixture.py), the real
   giving-forecast.js and pac-giving-forecast.js, and the calculator's real responses (tests/fixtures/giving_forecast_f2_live*.json) answered by route stubs, so the
   data is exact and the run is deterministic. Every scenario runs on BOTH data paths:
     * the calculator as it was published before the model fix (no P1): unconditioned intervals and remainders, a budget_inconsistent Senate row;
     * the calculator with the model fix P1 (live): the same requests answered with the open cycle conditioned on the giving already observed.
   Each assertion encodes a requirement of the board, not whatever the code happens to print:
     1 header: the Insights subtitle is gone in forecast mode, the switch sits beside the title (in Observed insights too: it never moves), Export at the right;
     2 the scope row is on the canvas (no card): Industry, As of, and the account control "General giving | Company PACs | Lobbying" (Lobbying draws the lobbying
       chart in the main chart area); the hidden schema-1 fields stay hidden;
     3 ONE main card, chart ~68% | rail ~32% with a hairline between and no nested card; serif "Giving by cycle", "2025 dollars", the legend; observed solid, expected
       hatched, a thin range line with end caps and values, the one gold underline; a FIXED axis (it only grows for the same industry, account and date);
       the rail "Assumptions": both revenue-growth horizons, House and Senate chance of Republican control, "More assumptions", "Reset to defaults"; no Calculate button,
       every input recalculates after a pause;
     4 the Party split card: House | Senate, Employees and Company PACs, 100% bars, Democratic left, a tick at the 2023-24 share; no chips anywhere;
     5 the foot: "Methods, tests and all populations" (collapsed) with a Sources link; inside, in order: the published PAC table, the partisan baseline panel, Sources
       and notes, the moved warnings text, "Use historical example";
     6 the type scale (12 / 13 / 15 / 20 / 28 px), party colours from tokens, one gold mark, none of the dead selectors;
     7 roles and keyboard use, and one column with no horizontal scroll at 390 px;
     8 the review fixes of 2026-10-07 (F1-F9): the sticky axis follows the run drawn, Reset restores everything, the Methods fold survives a failing catalog, "Try again", the
       horizon in every duplicated control name, the switch in one place, the form listener, "Company PACs", the Lobbying warnings.
   Run: node tests/test_giving_forecast_f2_browser.cjs   (cwd: the checkout root; MENTAT_PYTHON = a python with the console's requirements;
   MENTAT_NODE_MODULES = a Playwright install, or the default ~/.cache/codex-runtimes/... path used by every other .cjs test here) */
'use strict';
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const modules = process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = require(require.resolve('playwright', {paths: [modules]}));

const LEGACY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_f2_live.json'), 'utf8'));
const WITH_P1 = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_f2_live_p1.json'), 'utf8'));
const LANDED = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_f2_live_landed.json'), 'utf8'));   // the live calculator's whole catalog and answers
// The cycle-aware calculator (50/50 model, applied 2026-10-07): the real catalog and its real answers to the page's default requests. The catalogs of the two fixtures above carry its
// cycle_model_view too (growth defaults, notes, layers), so the page opens as it does today; open({cycle: false}) takes it away: a console still on the earlier module (rollback).
const CYCLE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_cycle_live.json'), 'utf8'));
const PATHS = {legacy: LEGACY.runs, p1: WITH_P1.runs, landed: LANDED.runs, cycle: CYCLE.runs};
// Melange's own LDA rows as the console's catalog carries them (catalog.anchors.industries.<id>.lda_history; real values, reconciled to sector_spend.json by test_giving_lda_history.py).
// The recorded catalogs predate the field, so every page gets it, as a console on the current anchor snapshot serves it.
const LDA_HISTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_lda_history.json'), 'utf8')).industries;
const addLdaHistory = catalog => { catalog.anchors = catalog.anchors || {status: 'ready', industries: {}}; for (const [id, lda] of Object.entries(LDA_HISTORY)) catalog.anchors.industries[id] = Object.assign({status: 'ready'}, catalog.anchors.industries[id], {lda_history: JSON.parse(JSON.stringify(lda))}); return catalog; };
const BUDGET_TEXT = 'Budget inconsistent — revise scenario';
const RANGE_TEXT = 'An input is outside the range this model was fitted on.';
const INTERVAL_TEXT = "Mentat's own last change for this series was far outside this band; treat the band as too narrow.";
const WIDE_TEXT = 'Share interval extends outside 0-100% (linear share model on a thin base); treat it as unreliable.';
// What the example's tooltip, aria-label, status line and notes say follows the run they describe: where the calculator conditions the open cycle on the giving already
// observed the drawn amounts differ from the Mentat level, and the sentence says why instead of claiming they are equal.
const EXAMPLE_HEAD = "The model's change in the amounts is zero (growth and control as in 2023–2024).";
const EXAMPLE_MIXES = "Party mixes also move: 2026 is a midterm, and the blank prior-cycle growth uses 2024's growth, which is outside the range the share models were fitted on.";
const EXAMPLE_PLAIN = `${EXAMPLE_HEAD} The 2025–2026 amounts equal the 2023–2024 Mentat level. ${EXAMPLE_MIXES}`;
const EXAMPLE_CONDITIONED = `${EXAMPLE_HEAD} The 2025–2026 amounts differ from the 2023–2024 Mentat level only because they are conditioned on the giving already observed. ${EXAMPLE_MIXES}`;
// The caption under a 2026 control row (the as-of date is in its tooltip).
const SAVED_TEXT = 'Polymarket, Jan–Jun 2026';
const IBIS_TEXT = 'IBIS 2026-08 · avg 2023–2025 · drives 2026 (one-year lag)', IBIS_TEXT_2028 = 'IBIS 2026-08 · avg 2025–2027 · drives 2028 (one-year lag)';
const BANKS_DEFAULT = 11.778;      // the banks' 2026 growth default of the catalog: the recorded older runs answer lower growth with "growth down" and higher growth with "growth up"
const GOLD = 'rgb(236, 169, 28)', PLUM = 'rgb(52, 38, 73)', DEM = 'rgb(59, 109, 181)', REP = 'rgb(184, 67, 58)';
const SCALE = ['12px', '13px', '15px', '20px', '28px'];
const num = value => (Math.abs(value) < 0.005 ? 0 : value).toLocaleString('en-US', {maximumFractionDigits: 2});
const M1 = value => '$' + (value / 1e6).toFixed(1) + 'M';        // the chart prints one decimal; the tooltips keep the exact amount
const REGISTRY_WAIVERS = [
  {gate: 'G6', cell: 'lobby / commercial_banks', term: 'lobby', industry: 'commercial_banks', reason: 'owner ruling: keep log refit', owner: 'Paul Goff', date: '2026-09-30', show_warning: false},
  {gate: 'G6', cell: 'control_House / commercial_banks', term: 'control_House', industry: 'commercial_banks', reason: 'Owner explicitly authorized the three House-share LOEO degradations: "Waive these three cells and publish".', owner: 'Paul Goff', date: '2026-09-30', show_warning: true},
  {gate: 'G6', cell: 'control_House / credit_unions', term: 'control_House', industry: 'credit_unions', reason: 'Owner explicitly authorized the three House-share LOEO degradations: "Waive these three cells and publish".', owner: 'Paul Goff', date: '2026-09-30', show_warning: true},
  {gate: 'G6', cell: 'control_House / auto_light_duty_manufacturing', term: 'control_House', industry: 'auto_light_duty_manufacturing', reason: 'Owner explicitly authorized the three House-share LOEO degradations: "Waive these three cells and publish".', owner: 'Paul Goff', date: '2026-09-30', show_warning: true}];

// The entry the live calculator carries in its catalog for the House share of commercial banks (display text, published and comparator values with their unit).
const DISPLAY_WAIVER = JSON.parse(JSON.stringify(LANDED.catalog.waivers.find(w => w.industry === 'commercial_banks' && w.chamber === 'House' && w.show_warning === true)));
const measuredText = w => `Measured.*published ${w.published_value.toFixed(3)} pp . comparator ${w.comparator_value.toFixed(3)} pp`;

let fixture, origin, browser;
const stderr = [];
before(async () => {
  fixture = spawn(process.env.MENTAT_PYTHON || 'python', [path.join(__dirname, 'console_browser_fixture.py')], {stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true});
  fixture.stderr.on('data', chunk => stderr.push(chunk.toString()));
  origin = await new Promise((resolve, reject) => {
    let output = '';
    fixture.stdout.on('data', chunk => { output += chunk; if (output.includes('\n')) resolve(output.trim().split('\n')[0]); });
    fixture.on('exit', code => reject(new Error(`Fixture exited ${code}: ${stderr.join('')}`)));
    fixture.on('error', reject);
  });
  browser = await chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome'});
});
after(async () => {
  if (browser) await browser.close();
  if (fixture) { fixture.stdin.write('\n'); fixture.stdin.end(); setTimeout(() => fixture.kill(), 3000).unref(); }
});

// The run the calculator would answer to a request: the same selection the console's own examples use (blank growth -> nothing entered, lobbying entered -> the lobbying
// run); with byGrowth a lower 2026 growth answers with the recorded "growth down" run and a higher one with "growth up" (the banks only).
function answerFor(runs, body, byGrowth) {
  const horizons = Object.values(body.horizons);
  const blank = horizons.every(h => h.growth_pct === '' || h.growth_pct == null);
  const lobbying = horizons.some(h => Object.values(h.annual_forecast_changes || {}).some(v => v !== '' && v != null));
  if (body.industry === 'property_casualty') return runs.property_casualty;
  if (body.industry === 'telecom') return runs.telecom;
  if (body.industry === 'credit_unions') return (lobbying && runs.credit_unions_lobby) || runs.credit_unions;
  if (blank) return runs.banks_blank || runs.banks;
  const g = Number(body.horizons['2026'].growth_pct);
  if (byGrowth && !lobbying && runs.banks_growth_down && Math.abs(g - BANKS_DEFAULT) > 1e-9) return g < BANKS_DEFAULT ? runs.banks_growth_down : runs.banks_growth_up;
  return (lobbying && runs.banks_lobby) || runs.banks;
}

/* A page on the real console with the forecast calls stubbed. options: path ('legacy' | 'p1' | 'landed'), width, height, waivers, reducedMotion, delay (ms), failure (status). */
async function open(options = {}) {
  const {path: dataPath = 'legacy', width = 1600, height = 900, waivers = null, reducedMotion = 'no-preference', delay = 0, failure = 0, malformed = false, byGrowth = false, catalogFailure = 0, pac = null, cycle = true, history = true, dollars = 'real'} = options;
  const context = await browser.newContext({viewport: {width, height}, deviceScaleFactor: 1, reducedMotion, acceptDownloads: true});
  // The dollar basis of the bars (D-092) is a stored per-viewer choice. Most tests pin 2025-dollar figures, so a page opens with that choice stored ('real'); 'nominal' stores the other;
  // 'default' stores nothing (a viewer on a first visit); 'blocked' makes the browser's storage throw, as a private window or blocked site data does.
  if (dollars === 'blocked') await context.addInitScript(() => { Storage.prototype.getItem = Storage.prototype.setItem = function () { throw new Error('blocked'); }; });
  else if (dollars !== 'default') await context.addInitScript(choice => { try { if (!localStorage.getItem('mentat.givingForecast.dollars')) localStorage.setItem('mentat.givingForecast.dollars', choice); } catch (error) { /* blocked */ } }, dollars);
  const page = await context.newPage();
  page.errors = []; page.requests = []; page.setDefaultTimeout(20000);
  page.on('pageerror', error => page.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' && !/Failed to load resource/.test(message.text())) page.errors.push(message.text()); });
  const catalog = JSON.parse(JSON.stringify(dataPath === 'landed' ? LANDED.catalog : dataPath === 'cycle' ? CYCLE.catalog : LEGACY.catalog));
  if (!cycle) delete catalog.cycle_model_view;       // a console on the earlier module: schema 2 without the cycle model
  if (history) addLdaHistory(catalog);
  if (dataPath === 'p1') catalog.waivers = WITH_P1.catalog_waivers;       // the one key the model fix adds to the catalog
  if (waivers) catalog.waivers = waivers;
  page.stub = {catalog, runs: PATHS[dataPath], delay, failure, malformed, byGrowth};
  await page.route('**/api/forecast/catalog', route => catalogFailure ? route.fulfill({status: catalogFailure, json: {error: 'The reviewed models could not be loaded.'}}) : route.fulfill({json: page.stub.catalog}));
  if (pac) await page.route('**/api/forecast/pac', route => route.fulfill({json: pac}));
  await page.route('**/api/forecast/project', async route => {
    const body = route.request().postDataJSON();
    page.requests.push(body);
    if (page.stub.delay) await new Promise(resolve => setTimeout(resolve, page.stub.delay));
    if (page.stub.failure) return route.fulfill({status: page.stub.failure, json: {error: 'The forecast service could not finish this calculation.'}});
    if (page.stub.malformed) return route.fulfill({json: {status: 'ready', industry_label: 'Commercial banks', horizons: [{}, {}], anchors: {}, notices: []}});
    return route.fulfill({json: answerFor(page.stub.runs, body, page.stub.byGrowth)});
  });
  await page.route('**/api/insights/catalog', route => route.fulfill({json: {datasets: [{id: 'Credit Union Sample', label: 'Credit Union Sample', available: true}], panels: []}}));
  await page.route('**/api/insights/query', route => route.fulfill({json: {status: 'ready', scope: {}, panels: {}, options: {}, provenance: {}, notices: []}}));
  await page.goto(origin + '/#insights');
  await page.waitForSelector('#giving-forecast-open', {state: 'visible'});
  await page.click('#giving-forecast-open');
  if (catalogFailure) { await page.waitForFunction(() => /to retry/.test(document.getElementById('gf-load-status').textContent)); return page; }
  await page.waitForSelector('#gf-form:not([hidden])');
  await settled(page);
  return page;
}
// The chart has drawn (an svg or an empty message) and no calculation is running.
const settled = page => page.waitForFunction(() => {
  const chart = document.getElementById('gf-chart');
  return chart && chart.getAttribute('aria-busy') === 'false' && (chart.querySelector('svg') || (chart.querySelector('.gf-chart-empty') && !/Loading/.test(chart.textContent)));
});
const own = page => page.requests.filter(r => !/^Reference levels only/.test(r.assumption_source || ''));
// An input was changed: nothing leaves before the pause; after it ONE request leaves and its answer is drawn. Returns the number of own requests before.
async function recalculated(page, before) {
  const start = Date.now();
  while (own(page).length <= before) { if (Date.now() - start > 6000) throw new Error('no request left after the pause'); await page.waitForTimeout(40); }
  await page.waitForFunction(() => document.getElementById('gf-chart').getAttribute('aria-busy') === 'false');
  await page.waitForTimeout(150);
}
const type = async (page, id, text) => { const before = own(page).length; await page.fill(id, text); await recalculated(page, before); };
const lastOwn = page => own(page)[own(page).length - 1];
const openMethods = async page => { if (!await page.$eval('#gf-methods', el => el.open)) await page.click('#gf-methods > summary'); };
const closePage = page => page.context().close();
const chartText = page => page.$eval('#gf-chart svg', el => el.textContent);
const axisLabels = page => page.$$eval('#gf-chart .gf-axis-label', all => all.map(el => el.textContent));
async function hoverTip(page, selector, options = {}) {
  // Scroll first and let the scroll settle: a scroll hides the tooltip (by design), and Playwright scrolls an element into view before it hovers.
  const target = page.locator(selector).first();
  await target.scrollIntoViewIfNeeded();
  await page.waitForTimeout(80);
  await target.hover(options);
  await page.waitForSelector('#gf-tip:not([hidden])');
  return page.$eval('#gf-tip', el => el.textContent);
}
const warnings = page => page.$$eval('#gf-warnings-list li', all => all.map(li => ({text: li.textContent, warn: li.classList.contains('is-warn'), line: li.firstElementChild.textContent + li.children[1].textContent, detail: li.children[2] ? li.children[2].textContent : ''})));
const measureOf = (run, horizon, id) => run.horizons[horizon].pac.measures.find(m => m.id === id);
// The share a Party split bar draws for a basis: the implied full-cycle share where the run carries it (P1), else the model's projected full-cycle share.
const drawnShare = b => b.remaining_allocation && b.remaining_allocation.implied_cycle_R_share != null ? b.remaining_allocation.implied_cycle_R_share : b.projected_cycle_R_share;

for (const dataPath of ['legacy', 'p1']) {
  const withFix = dataPath === 'p1';
  const label = withFix ? 'with the model fix P1' : 'without the model fix';

  test(`[${label}] header: no subtitle in forecast mode, the switch beside the title, Export at the right; no Calculate button anywhere`, async () => {
    const page = await open({path: dataPath});
    try {
      assert.equal(await page.getAttribute('#giving-forecast-open', 'aria-pressed'), 'true');
      assert.equal(await page.getAttribute('#gf-view-insights', 'aria-pressed'), 'false');
      assert.equal(await page.$eval('#giving-forecast', el => el.hidden), false);
      assert.equal(await page.$eval('.insights-subtitle', el => getComputedStyle(el).display), 'none', 'the subtitle "Compare periods and explain observed changes." is hidden in forecast mode');
      const g = await page.evaluate(() => {
        const box = el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height}; };
        const style = getComputedStyle(document.getElementById('gf-export'));
        return {title: box(document.getElementById('workspace-title')), sw: box(document.getElementById('gf-view-switch')), exp: box(document.getElementById('gf-export')), vw: document.documentElement.clientWidth,
          export: {text: document.getElementById('gf-export').textContent, background: style.backgroundColor, color: style.color, border: style.borderTopWidth + ' ' + style.borderTopStyle, secondary: document.getElementById('gf-export').classList.contains('secondary'), primary: document.getElementById('gf-export').classList.contains('primary'), disabled: document.getElementById('gf-export').disabled},
          calculate: document.getElementById('gf-calculate'), calculateText: /\bCalculate\b/.test(document.getElementById('giving-forecast').textContent)};
      });
      assert.ok(g.sw.left - g.title.right >= 20 && g.sw.left - g.title.right <= 40, 'the switch sits beside the title, about 28 px after it: ' + (g.sw.left - g.title.right));
      assert.ok(Math.abs((g.sw.top + g.sw.bottom) / 2 - (g.title.top + g.title.bottom) / 2) <= 8, 'on the title row');
      assert.ok(Math.abs(g.exp.right - (g.vw - 22)) <= 3, 'Export keeps the right edge: ' + g.exp.right);
      const switchBox = () => page.$eval('#gf-view-switch', el => { const r = el.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; });
      const inForecast = await switchBox();
      assert.deepEqual(g.export, {text: 'Export', background: 'rgb(255, 255, 255)', color: PLUM, border: '1px solid', secondary: true, primary: false, disabled: false});
      assert.equal(g.calculate, null, 'the Calculate button is gone');
      assert.equal(g.calculateText, false, 'and no text of the view offers it');
      // the two segments switch the Insights sub-view exactly as before
      await page.click('#gf-view-insights');
      assert.equal(await page.$eval('#giving-forecast', el => el.hidden), true);
      assert.equal(await page.$eval('#gf-export', el => el.hidden), true, 'Export belongs to the forecast view');
      assert.deepEqual(await switchBox(), inForecast, 'the switch does not move: the same place beside the title in Observed insights and in Forecast giving');
      assert.notEqual(await page.$eval('.insights-subtitle', el => getComputedStyle(el).display), 'none', 'the subtitle is back in observed mode');
      assert.equal(await page.$eval('#insights-controls', el => getComputedStyle(el).display !== 'none'), true, 'the observed-insights scope is back');
      await page.click('#giving-forecast-open');
      assert.equal(await page.$eval('#giving-forecast', el => el.hidden), false);
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the view switch never jumps between Observed insights and Forecast giving, at any width; no title width is written into the stylesheet`, async () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'giving-forecast.css'), 'utf8');
    assert.ok(!/130px/.test(css), 'the hard-coded title width is gone');
    for (const width of [1600, 1100, 900, 390]) {
      const page = await open({path: dataPath, width});
      try {
        const read = () => page.evaluate(() => {
          const box = el => { const r = el.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right)}; };
          return {sw: box(document.getElementById('gf-view-switch')), title: box(document.getElementById('workspace-title')), anchored: CSS.supports('anchor-name', '--x'), heading: getComputedStyle(document.getElementById('gf-view-switch')).position};
        });
        const forecast = await read();
        await page.click('#gf-view-insights');
        const observed = await read();
        const dy = Math.abs(observed.sw.y - forecast.sw.y), dx = Math.abs(observed.sw.x - forecast.sw.x);
        assert.ok(dx <= 1 && observed.sw.w === forecast.sw.w, `${width}: same left edge and width in both modes: ${JSON.stringify([forecast.sw, observed.sw])}`);
        assert.ok(dy <= (width >= 761 ? 1 : 3), `${width}: same height on the page in both modes: ${dy}`);
        if (width >= 761 && forecast.anchored) {
          assert.equal(forecast.heading, 'absolute');
          assert.ok(Math.abs(forecast.sw.x - forecast.title.right - 28) <= 1, `${width}: 28 px after the title, measured from the title itself: ${forecast.sw.x - forecast.title.right}`);
          assert.ok(Math.abs((forecast.sw.y + forecast.sw.h / 2) - (forecast.title.y + forecast.title.h / 2)) <= 2, `${width}: centred on the title's line`);
        }
        if (width < 761) assert.ok(Math.abs(forecast.sw.w - (width - 32)) <= 2, 'a phone: the switch takes the full width, like the account control');
        await page.click('#giving-forecast-open');
        assert.deepEqual(page.errors, []);
      } finally { await closePage(page); }
    }
  });

  test(`[${label}] scope row on the canvas, ONE main card of two columns with a hairline, Party split under it, one disclosure line at the foot`, async () => {
    const page = await open({path: dataPath});
    try {
      const g = await page.evaluate(() => {
        const q = s => document.querySelector(s), box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom}; };
        const row = getComputedStyle(q('.gf-scope-row')), main = q('.gf-main'), rail = getComputedStyle(q('.gf-rail'));
        return {oldCard: document.querySelectorAll('.gf-scope-card, .gf-hero, .gf-row3').length, row: {bg: row.backgroundColor, shadow: row.boxShadow, border: row.borderTopWidth},
          industryLabel: q('.gf-industry-field > span').textContent, dateLabel: q('.gf-date-field > span').textContent, scopeInRow: q('.gf-scope-row').contains(q('#gf-industry')) && q('.gf-scope-row').contains(q('#gf-date')),
          account: [...document.querySelectorAll('.gf-account > button')].map(b => ({text: b.textContent, pressed: b.getAttribute('aria-pressed'), id: b.id})), accountBox: box(q('.gf-account')), signal: q('#gf-signal-field').hidden, slopes: q('#gf-slopes-field').hidden,
          main: box(main), isCard: main.classList.contains('card'), nested: main.querySelectorAll('.card').length, chartCol: box(q('.gf-chart-col')), railBox: box(q('.gf-rail')), railBorder: rail.borderLeftWidth + ' ' + rail.borderLeftStyle, tablists: document.querySelectorAll('#giving-forecast [role=tablist], #giving-forecast [role=tab]').length,
          party: box(q('#gf-party')), partyCard: q('#gf-party').classList.contains('card'), partyNested: q('#gf-party').querySelectorAll('.card').length, foot: box(q('.gf-foot')), summary: q('#gf-methods > summary').textContent, methodsOpen: q('#gf-methods').open, sources: box(q('#gf-sources-link')), sourcesText: q('#gf-sources-link').textContent, summaryBox: box(q('#gf-methods > summary')),
          sidebar: box(q('.gf-industry-field select')), houseSenate: [box(q('#gf-house')), box(q('#gf-senate'))]};
      });
      assert.equal(g.oldCard, 0, 'no scope card, no hero card, no three-card row');
      assert.deepEqual(g.row, {bg: 'rgba(0, 0, 0, 0)', shadow: 'none', border: '0px'}, 'the scope row sits directly on the canvas');
      assert.equal(g.industryLabel, 'Industry'); assert.equal(g.dateLabel, 'As of'); assert.equal(g.scopeInRow, true);
      assert.deepEqual(g.account.map(b => b.text), ['General giving', 'Company PACs', 'Lobbying'], 'the PAC account is the roster companies\' own PACs, named as the Party split row names them');
      assert.deepEqual(g.account.map(b => b.pressed), ['true', 'false', 'false']);
      assert.deepEqual(g.account.map(b => b.id), ['gf-account-general', 'gf-account-pac', 'gf-account-lobby']);
      assert.ok(Math.abs(g.accountBox.right - g.main.right) <= 2, 'the account control ends at the right edge of the card below it');
      assert.equal(g.signal, true, 'the schema-1 field stays hidden'); assert.equal(g.slopes, true);
      assert.equal(g.isCard, true); assert.equal(g.nested, 0, 'no card inside the card');
      assert.ok(Math.abs(g.chartCol.w / g.main.w - 0.68) <= 0.006, 'the chart column is about 68%: ' + g.chartCol.w / g.main.w);
      assert.ok(Math.abs(g.railBox.w / g.main.w - 0.32) <= 0.006, 'the rail column is about 32%: ' + g.railBox.w / g.main.w);
      assert.equal(g.railBorder, '1px solid', 'one hairline between the columns');
      assert.equal(g.tablists, 0, 'the horizon tabs are gone');
      assert.ok(g.main.y > g.accountBox.bottom, 'the card is under the scope row');
      assert.equal(g.partyCard, true); assert.equal(g.partyNested, 0);
      assert.ok(Math.abs(g.party.y - g.main.bottom - 16) <= 1.5, 'Party split sits 16 px under the main card: ' + (g.party.y - g.main.bottom));
      assert.ok(Math.abs(g.houseSenate[0].w - g.houseSenate[1].w) <= 1 && g.houseSenate[1].x > g.houseSenate[0].right, 'House | Senate side by side');
      assert.equal(g.summary, 'Methods, tests and all populations'); assert.equal(g.methodsOpen, false, 'collapsed by default');
      assert.equal(g.sourcesText, 'Sources'); assert.ok(g.sources.x > g.summaryBox.right && Math.abs(g.sources.right - g.foot.right) <= 1, 'the Sources link is at the right of the same line');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the chart card: serif title, caption, legend, observed solid, expected hatched, a thin range with end caps and values, the one gold underline`, async () => {
    const page = await open({path: dataPath});
    try {
      const head = await page.evaluate(() => {
        const title = document.getElementById('gf-hero-title'), style = getComputedStyle(title);
        return {title: title.textContent, font: style.fontFamily, size: style.fontSize, basis: document.getElementById('gf-basis').textContent, legend: [...document.querySelectorAll('#gf-legend li')].map(li => li.textContent), lobbyLegendHidden: document.getElementById('gf-legend-lobby').hidden};
      });
      assert.equal(head.title, 'Giving by cycle'); assert.match(head.font, /serif|Georgia|Freight/); assert.equal(head.size, '20px');
      assert.equal(head.basis, '2025 dollars'); assert.deepEqual(head.legend, ['Observed', 'Expected', '95% range']); assert.equal(head.lobbyLegendHidden, true);
      const run = answerFor(page.stub.runs, lastOwn(page)), general = measureOf(run, 0, 'general_giving'), next = measureOf(run, 1, 'general_giving');
      const total = withFix ? general.cycle_given_observed_median : general.projected_cycle_real_2025, low = withFix ? general.cycle_given_observed_low : general.cycle_prediction_low, high = withFix ? general.cycle_given_observed_high : general.cycle_prediction_high;
      const text = await chartText(page);
      // x labels: the period, then what it is
      assert.match(text, /2023–24Mentat level/); assert.ok(text.includes('2025–26' + M1(general.observed.amount_real_2025) + ' observed to Jun 30'), text); assert.match(text, /2027–28Projection/);
      for (const value of [general.anchor_cycle_real_2025, total, high, low, next.projected_cycle_real_2025, next.cycle_prediction_high, next.cycle_prediction_low]) assert.ok(text.includes(M1(value)), M1(value) + ' is drawn: ' + text);
      assert.ok(!text.includes(M1(general.anchor_cycle_nominal)) || M1(general.anchor_cycle_nominal) === M1(general.anchor_cycle_real_2025), 'the nominal amount is in the tooltip, not on the chart');
      if (withFix) assert.ok(!text.includes(M1(general.cycle_prediction_low)), 'the unconditioned low end is not drawn');
      // marks: one solid observed segment, one hatched remainder, one hatched projection, the anchor, two ranges
      const g = await page.evaluate(() => {
        const fill = s => { const e = document.querySelector(s); return e ? getComputedStyle(e).fill : null; };
        const q = s => document.querySelectorAll('#gf-chart ' + s).length;
        const whisker = document.querySelector('#gf-chart .gf-mark-current .gf-whisker');
        const lines = whisker.querySelectorAll('line.gf-range');
        return {observed: q('.gf-col-observed'), remainder: q('.gf-col-remainder'), projection: q('.gf-col-projection'), anchor: q('.gf-col-anchor'), whiskers: q('.gf-whisker'), obsFill: fill('#gf-chart .gf-col-observed'), anchorFill: fill('#gf-chart .gf-col-anchor'), hatch: fill('#gf-chart .gf-col-remainder'),
          hatchStroke: getComputedStyle(document.querySelector('#gf-chart .gf-col-remainder')).stroke, hatchPattern: !!document.querySelector('#gf-chart pattern#gf-hatch'), rangeWidth: getComputedStyle(lines[0]).strokeWidth, rangeLines: lines.length,
          caps: [...lines].slice(1).map(l => Math.abs(l.getAttribute('x2') - l.getAttribute('x1'))), median: !!whisker.querySelector('circle.gf-median'), connector: getComputedStyle(whisker.querySelector('.gf-connector')).strokeDasharray,
          sides: [...whisker.querySelectorAll('text.gf-val-side')].map(t => t.textContent), sideSize: getComputedStyle(whisker.querySelector('text.gf-val-side')).fontSize, valSize: getComputedStyle(document.querySelector('#gf-chart text.gf-val')).fontSize, valWeight: getComputedStyle(document.querySelector('#gf-chart text.gf-val')).fontWeight,
          xSize: [getComputedStyle(document.querySelector('#gf-chart .gf-x-label')).fontSize, getComputedStyle(document.querySelector('#gf-chart .gf-x-sub')).fontSize], axisSize: getComputedStyle(document.querySelector('#gf-chart .gf-axis-label')).fontSize,
          band: q('.gf-band'), gold: [...document.querySelectorAll('#gf-chart .gf-gold')].map(r => ({x: +r.getAttribute('x') + +r.getAttribute('width') / 2, fill: getComputedStyle(r).fill, w: r.getAttribute('width'), h: r.getAttribute('height'), y: +r.getAttribute('y')})),
          obsCenter: (() => { const r = document.querySelector('#gf-chart .gf-mark-current .gf-col-observed'); return +r.getAttribute('x') + +r.getAttribute('width') / 2; })(), valueLabelY: +document.querySelector('#gf-chart .gf-mark-current text.gf-val').getAttribute('y')};
      });
      assert.deepEqual({observed: g.observed, remainder: g.remainder, projection: g.projection, anchor: g.anchor, whiskers: g.whiskers, band: g.band}, {observed: 1, remainder: 1, projection: 1, anchor: 1, whiskers: 2, band: 0}, 'no interval band any more');
      assert.equal(g.obsFill, PLUM, 'observed is solid plum'); assert.equal(g.anchorFill, PLUM);
      assert.match(g.hatch, /url\(.*gf-hatch/, 'expected is hatched'); assert.equal(g.hatchStroke, PLUM); assert.equal(g.hatchPattern, true);
      assert.equal(g.rangeWidth, '1.5px', 'a thin range line'); assert.equal(g.rangeLines, 3); assert.deepEqual(g.caps, [10, 10], 'with end caps'); assert.equal(g.median, true); assert.match(g.connector, /2/);
      assert.deepEqual(g.sides.map(String), [M1(high), M1(low)], 'the range value labels');
      assert.deepEqual([g.sideSize, g.valSize, g.valWeight, g.xSize, g.axisSize], ['12px', '20px', '700', ['15px', '12px'], '12px']);
      assert.equal(g.gold.length, 1, 'the one gold mark'); assert.equal(g.gold[0].fill, GOLD); assert.deepEqual([g.gold[0].w, g.gold[0].h], ['30', '3']);
      assert.ok(Math.abs(g.gold[0].x - g.obsCenter) <= 1, 'under the 2025–26 value'); assert.ok(g.gold[0].y > g.valueLabelY, 'below the value');
      // hover: the observed segment, the remainder, the whisker and the anchor column each carry a tooltip
      assert.match(await hoverTip(page, '#gf-chart .gf-mark-current .gf-col-observed'), /Observed through2026-06-30/);
      assert.match(await hoverTip(page, '#gf-chart .gf-mark-current .gf-col-remainder'), withFix ? /Remainder, median \(given the giving already observed\)\$15\.63M/ : /Projected remainder\$15\.61M/);
      assert.match(await hoverTip(page, '#gf-chart .gf-mark-current .gf-whisker .gf-hit'), /95% prediction interval/);
      const anchorTip = await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor');
      assert.match(anchorTip, /In 2025 USD \(this chart\)\$27\.18M/); assert.match(anchorTip, /Nominal\$26\.11M/);
      await page.mouse.move(5, 5);
      await page.waitForSelector('#gf-tip', {state: 'hidden'});
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the fixed axis: moving an assumption moves the bars, not the scale; the top only grows for the same industry and account, and starts over when either changes`, async () => {
    const page = await open({path: dataPath, byGrowth: true});
    try {
      const barHeight = () => page.$eval('#gf-chart .gf-mark-next .gf-col-projection', el => Number(el.getAttribute('height')));
      // The oracle for the axis top: the nice scale of the largest figure the chart draws for an industry's run, computed here from the response (not from the page)
      const niceTop = max => { const power = Math.pow(10, Math.floor(Math.log10(max)) - 1); for (const base of [1, 2, 2.5, 5, 10, 20, 25, 50, 100]) { const step = base * power, count = Math.ceil(max / step - 1e-9); if (count <= 11) return count * step; } return max; };
      const maxOf = (run, id) => {
        const [c, n] = run.horizons.map(h => h.pac.measures.find(m => m.id === id)), given = c.cycle_given_observed_median != null && c.remaining_given_observed_median != null;
        return Math.max(...[c.anchor_cycle_real_2025, c.observed && c.observed.amount_real_2025, given ? c.cycle_given_observed_median : c.projected_cycle_real_2025, given ? c.cycle_given_observed_high : c.cycle_prediction_high, n.projected_cycle_real_2025, n.cycle_prediction_high].filter(Number.isFinite));
      };
      const labelValue = text => { const m = /^\$([\d.]+)([MK]?)$/.exec(text); return Number(m[1]) * (m[2] === 'M' ? 1e6 : m[2] === 'K' ? 1e3 : 1); };
      const topNow = async () => { const labels = await axisLabels(page); return labelValue(labels[labels.length - 1]); };
      const switchTo = async industry => { const before = own(page).length; await page.selectOption('#gf-industry', industry); await recalculated(page, before); };
      const topOf = labels => Number(labels[labels.length - 1].replace(/[$M]/g, ''));
      const first = await axisLabels(page), h0 = await barHeight();
      // lower growth: a recorded run with higher giving and a taller interval -> the axis may only grow
      await type(page, '#gf-2026-growth-view', '2');
      const down = await axisLabels(page), h1 = await barHeight();
      assert.ok(topOf(down) > topOf(first), 'a taller interval grows the top: ' + [topOf(first), topOf(down)]);
      // higher growth: a run with less giving; the bar is shorter against the SAME scale
      await type(page, '#gf-2026-growth-view', '20');
      const up = await axisLabels(page), h2 = await barHeight();
      assert.deepEqual(up, down, 'the same industry, account and date: the scale is fixed, only the bars move');
      assert.ok(h0 > 0 && h2 > 0 && h2 < h1, 'on the SAME scale the bars move: lower growth gives more giving than higher growth (' + [h1, h2] + ')');
      // blank growth: the chart keeps its scale (a calculation that shrinks the data must not rescale the page)
      await type(page, '#gf-2026-growth-view', '');
      assert.deepEqual(await axisLabels(page), up, 'blank growth keeps the scale: a calculation that shrinks the data must not rescale the page');
      // every interval end fits under the top
      await type(page, '#gf-2026-growth-view', '2');
      const top = await page.$eval('#gf-chart', el => { const t = [...el.querySelectorAll('.gf-axis-label')].map(x => x.textContent); return t[t.length - 1]; });
      const topValue = Number(top.replace(/[$M]/g, '')) * 1e6, ends = await page.$$eval('#gf-chart .gf-val-side', all => all.map(el => el.textContent));
      for (const text of ends) assert.ok(Number(text.replace(/[$M]/g, '')) * 1e6 <= topValue, `${text} fits under ${top}`);
      // the other account has its own scale; switching never mixes them, and the unchanged account keeps its own
      const general = await axisLabels(page);
      await page.click('#gf-account-pac'); await page.waitForTimeout(300);
      const pac = await axisLabels(page);
      assert.notDeepEqual(pac, general, 'each account has its own scale');
      await page.click('#gf-account-general'); await page.waitForTimeout(300);
      assert.deepEqual(await axisLabels(page), general);
      // another industry starts over, on ITS OWN scale: the top is exactly the nice scale of that industry's largest figure, never the previous industry's carried over
      // (the form names the new industry while the old run is still drawn; banks -> credit unions -> P&C on General giving, and the same on the PAC account)
      const bankTop = await topNow();
      await switchTo('credit_unions');
      assert.notDeepEqual(await axisLabels(page), general, 'a different industry gets its own scale');
      const cuTop = niceTop(maxOf(page.stub.runs.credit_unions, 'general_giving'));
      assert.equal(await topNow(), cuTop, 'credit unions, General giving: the top is the nice scale of the credit-union maximum');
      assert.ok(cuTop < bankTop, 'and it is smaller than the banks top, so the old scale did not carry over: ' + [cuTop, bankTop]);
      await switchTo('commercial_banks');
      await switchTo('property_casualty');
      const pcTop = niceTop(maxOf(page.stub.runs.property_casualty, 'general_giving'));
      assert.equal(await topNow(), pcTop, 'P&C, General giving: its own maximum, not the banks one');
      assert.ok(pcTop < bankTop, 'and again smaller than the banks top: ' + [pcTop, bankTop]);
      // the PAC account: banks -> credit unions on the PAC scale; P&C has no PAC account, which is said, not drawn
      await switchTo('commercial_banks');
      await page.click('#gf-account-pac'); await page.waitForTimeout(300);
      const bankPacTop = await topNow();
      await switchTo('credit_unions');
      const cuPacTop = niceTop(maxOf(page.stub.runs.credit_unions, 'pac_to_candidates'));
      assert.equal(await topNow(), cuPacTop, 'credit unions, the PAC account: the nice scale of the credit-union maximum');
      assert.ok(cuPacTop < bankPacTop, 'smaller than the banks PAC top, so the old scale did not carry over: ' + [cuPacTop, bankPacTop]);
      await switchTo('property_casualty');
      assert.deepEqual(await axisLabels(page), [], 'P&C has no PAC account: no axis is drawn');
      assert.match(await page.textContent('#gf-chart'), /\S/, 'and the chart says why');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the rail: Assumptions with both growth horizons and the two control rows, label left, big value right, slider beneath, one muted caption; More assumptions and Reset to defaults`, async () => {
    const page = await open({path: dataPath});
    try {
      const g = await page.evaluate(() => {
        const rail = document.querySelector('.gf-rail'), rows = [...document.querySelectorAll('#gf-horizons > .gf-row')];
        const box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom}; };
        return {title: document.getElementById('gf-scenario-title').textContent, titleFont: getComputedStyle(document.getElementById('gf-scenario-title')).fontFamily, titleSize: getComputedStyle(document.getElementById('gf-scenario-title')).fontSize,
          rows: rows.map(row => {
            const head = row.querySelector('.gf-row-head'), view = row.querySelector('.gf-num'), slider = row.querySelector('.gf-slider'), caption = row.querySelector('.gf-source'), value = getComputedStyle(row.querySelector('.gf-numbox'));
            return {label: row.querySelector('.gf-row-label').textContent, view: view.value, unit: row.querySelector('.gf-unit').textContent, caption: caption.querySelector('.gf-source-text').textContent, valueSize: value.fontSize, valueWeight: value.fontWeight, captionSize: getComputedStyle(caption).fontSize, captionColor: getComputedStyle(caption).color,
              labelLeftOfValue: box(row.querySelector('.gf-row-label')).right <= box(row.querySelector('.gf-numbox')).x + 1, sliderBelow: box(slider).y >= box(head).bottom - 1, captionBelow: box(caption).y >= box(slider).bottom - 1, role: slider.getAttribute('role'), valuenow: slider.getAttribute('aria-valuenow'), unset: slider.classList.contains('is-unset')};
          }),
          more: {open: document.getElementById('gf-more').open, text: document.querySelector('#gf-more > summary').textContent}, reset: {text: document.getElementById('gf-reset').textContent, bottom: box(document.getElementById('gf-reset')).bottom, railBottom: box(rail).bottom, last: rail.lastElementChild.id, belowRows: box(document.getElementById('gf-reset')).y > box(rows[rows.length - 1]).bottom},
          railText: rail.textContent, buttons: [...rail.querySelectorAll('button')].filter(b => b.offsetParent).map(b => b.textContent), tabs: rail.querySelectorAll('[role=tab],[role=tablist],[role=tabpanel]').length, hiddenPanels: document.querySelectorAll('#gf-horizons .gf-tabpanel, #gf-horizons .is-off').length};
      });
      assert.equal(g.title, 'Assumptions'); assert.match(g.titleFont, /serif|Georgia|Freight/); assert.equal(g.titleSize, '20px');
      assert.deepEqual(g.rows.map(r => r.label), ['Industry revenue growth, avg 2023–25', 'Industry revenue growth, avg 2025–27', 'House: chance of Republican control', 'Senate: chance of Republican control'], 'both horizons at once, in the board order');
      assert.deepEqual(g.rows.map(r => r.view), [num(11.778), num(-1.848), num(LEGACY.catalog.saved_outlook.control.House.value), num(LEGACY.catalog.saved_outlook.control.Senate.value)], 'the box shows the held number to two decimals');
      assert.deepEqual(g.rows.map(r => r.unit), ['%', '%', '%', '%']);
      assert.deepEqual(g.rows.map(r => r.caption), [IBIS_TEXT + ' Reset', IBIS_TEXT_2028 + ' Reset', SAVED_TEXT, SAVED_TEXT], 'one muted caption each (the Reset link is hidden until the row changes)');
      assert.deepEqual(await page.$$eval('#gf-2026-House-source, #gf-2026-Senate-source', all => all.map(el => el.textContent)), [SAVED_TEXT + ' · Clear', SAVED_TEXT + ' · Clear'], 'a control row\'s caption carries its Clear link');
      for (const r of g.rows) {
        assert.deepEqual([r.valueSize, r.valueWeight, r.captionSize, r.captionColor], ['20px', '700', '12px', 'rgb(98, 89, 110)'], r.label);
        assert.deepEqual([r.labelLeftOfValue, r.sliderBelow, r.captionBelow, r.role, r.unset], [true, true, true, 'slider', false], r.label);
      }
      assert.equal(g.more.open, false); assert.equal(g.more.text, 'More assumptions');
      assert.equal(g.reset.text, 'Reset to defaults'); assert.equal(g.reset.last, 'gf-reset', 'the last thing in the rail'); assert.equal(g.reset.belowRows, true); assert.ok(g.reset.railBottom - g.reset.bottom <= 24);
      assert.ok(!/Calculate|historical example/i.test(g.railText), 'no Calculate and no "Use historical example" in the rail');
      assert.equal(g.tabs, 0); assert.equal(g.hiddenPanels, 0, 'no horizon panels: both horizons are on screen');
      // More assumptions holds the rest, unchanged in function: the control before the election, prior-cycle growth, the 2027-28 control scores, lobbying by year
      await page.click('#gf-more > summary');
      const more = await page.evaluate(() => [...document.querySelectorAll('#gf-more-body .gf-more-group')].map(group => ({title: group.querySelector('h3').textContent, fields: [...group.children].slice(1).map(c => c.className.split(' ')[0] + ':' + (c.querySelector('.gf-row-label, .gf-prior-label, .gf-pillfield > span, .gf-lobby-link') || c).textContent.trim())})));
      assert.deepEqual(more, [
        {title: '2025–26', fields: ['gf-pillfield:House control before election', 'gf-pillfield:Senate control before election', 'gf-prior:Prior-cycle growth', 'gf-lobby-wrap:Lobbying by year']},
        {title: '2027–28', fields: ['gf-pillfield:House control before election', 'gf-pillfield:Senate control before election', 'gf-prior:Prior-cycle growth', 'gf-row:House: chance of Republican control', 'gf-row:Senate: chance of Republican control', 'gf-lobby-wrap:Lobbying by year']}]);
      assert.deepEqual(await page.$$eval('#gf-more-body .gf-pill-select', all => all.map(el => el.id)), ['gf-2026-House-majority', 'gf-2026-Senate-majority', 'gf-2028-House-majority', 'gf-2028-Senate-majority']);
      assert.deepEqual(await page.$$eval('#gf-more-body .gf-slider', all => all.map(el => el.classList.contains('is-unset'))), [true, true], '2027–28 control scores open unset');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] growth opens at its IBIS default and recalculates after a pause; clearing makes the real unset state (a hollow thumb, "-", nothing sent); Reset to defaults restores it`, async () => {
    const page = await open({path: dataPath});
    try {
      const first = own(page);
      assert.equal(first.length, 1, 'one calculation on open, no click needed');
      assert.equal(first[0].horizons['2026'].growth_pct, '11.778'); assert.equal(first[0].horizons['2028'].growth_pct, '-1.848'); assert.equal(first[0].growth_basis, 'ibis_window_avg', 'every request names the growth basis');
      assert.equal(await page.$eval('#gf-export', el => el.disabled), false, 'a projection is on screen and exportable');
      const slider = () => page.$eval('#gf-horizons .gf-row:first-child .gf-slider', el => ({unset: el.classList.contains('is-unset'), text: el.getAttribute('aria-valuetext'), thumb: getComputedStyle(el.querySelector('.gf-thumb')).borderTopStyle, hasDefault: el.classList.contains('has-default')}));
      assert.deepEqual(await slider(), {unset: false, text: '11.78%', thumb: 'solid', hasDefault: true});
      const caption = () => page.$eval('#gf-horizons .gf-row:first-child .gf-source', el => ({text: el.querySelector('.gf-source-text > span').textContent, reset: getComputedStyle(el.querySelector('button')).display !== 'none'}));
      assert.deepEqual(await caption(), {text: IBIS_TEXT, reset: false});
      // a typed change: nothing before the pause, one request after it, the previous chart kept while it runs
      page.stub.delay = 900;
      const before = own(page).length;
      await page.fill('#gf-2026-growth-view', '15');       // above the default: the fill then starts at the default tick
      await page.waitForTimeout(120); assert.equal(own(page).length, before, 'nothing is sent before the pause');
      await page.waitForTimeout(450); assert.equal(own(page).length, before + 1, 'one request after the pause');
      assert.equal(await page.$eval('#gf-chart', el => el.getAttribute('aria-busy')), 'true');
      assert.equal(await page.$eval('#gf-progress', el => el.hidden), false, 'a thin progress bar while it runs');
      assert.ok(await page.$eval('#gf-chart svg', el => el.querySelectorAll('rect,path').length > 3), 'the previous chart stays drawn, never a blank');
      await page.waitForFunction(() => document.getElementById('gf-chart').getAttribute('aria-busy') === 'false'); page.stub.delay = 0;
      assert.equal(lastOwn(page).horizons['2026'].growth_pct, '15');
      // the changed state of the board: the caption says what it changed from, the thumb is gold, the value is purple
      assert.deepEqual(await caption(), {text: 'Changed from default 11.78% ·', reset: true});
      const changed = await page.$eval('#gf-horizons .gf-row:first-child', el => ({thumb: getComputedStyle(el.querySelector('.gf-thumb')).backgroundColor, value: getComputedStyle(el.querySelector('.gf-numbox')).color, fillLeft: el.querySelector('.gf-fill').getBoundingClientRect().left, fillW: el.querySelector('.gf-fill').getBoundingClientRect().width, tick: el.querySelector('.gf-default').getBoundingClientRect().left}));
      assert.equal(changed.thumb, GOLD); assert.equal(changed.value, 'rgb(87, 68, 123)');
      assert.ok(changed.fillW > 5 && Math.abs(changed.fillLeft - changed.tick) <= 2, 'the fill runs from the default tick to the thumb');
      // rapid typing: one request
      const b2 = own(page).length;
      await page.fill('#gf-2026-growth-view', ''); await page.keyboard.type('3.5', {delay: 40}); await page.waitForTimeout(700);
      assert.equal(own(page).length, b2 + 1, 'typing quickly makes a single request'); assert.equal(lastOwn(page).horizons['2026'].growth_pct, '3.5');
      // clearing: the real unset state, and the request carries '' (nothing is invented)
      await type(page, '#gf-2026-growth-view', '');
      assert.deepEqual(await slider(), {unset: true, text: 'Not set', thumb: 'dashed', hasDefault: true});
      assert.equal(await page.$eval('#gf-2026-growth-view', el => el.placeholder), '-');
      assert.equal(lastOwn(page).horizons['2026'].growth_pct, ''); assert.equal(lastOwn(page).horizons['2026'].prior_growth_pct, '');
      assert.deepEqual(await caption(), {text: 'Changed from default 11.78% ·', reset: true}, 'a blank stays blank: nothing re-fills it');
      // the keyboard works the slider: an arrow key on an unset slider starts a step in, Delete clears it
      await page.focus('#gf-horizons .gf-row:first-child .gf-slider');
      await page.keyboard.press('ArrowRight');
      assert.notEqual(await page.$eval('#gf-2026-growth', el => el.value), '');
      await page.keyboard.press('Delete');
      assert.equal(await page.$eval('#gf-2026-growth', el => el.value), '');
      // Reset (the caption's own link), then Reset to defaults for everything
      await page.click('#gf-horizons .gf-row:first-child .gf-caption-link');
      assert.equal(await page.$eval('#gf-2026-growth-view', el => el.value), '11.78');
      await page.fill('#gf-2028-growth-view', '9'); await page.fill('#gf-2026-House-political-view', '40');
      // the inputs that open blank are filled too (More assumptions, the lobbying dialogs): Reset must empty them again
      await page.click('#gf-more > summary');
      await page.fill('#gf-2026-prior', '3'); await page.fill('#gf-2028-prior', '-2'); await page.fill('#gf-2028-House-political-view', '55');
      await page.selectOption('#gf-2026-Senate-majority', '0'); await page.selectOption('#gf-2028-House-majority', '1'); await page.selectOption('#gf-2028-Senate-majority', '0');
      await page.evaluate(() => { for (const id of ['gf-2026-lobby', 'gf-2027-lobby', 'gf-2028-lobby']) { const el = document.getElementById(id); el.value = '1.5'; el.dispatchEvent(new Event('input', {bubbles: true})); } });
      await page.waitForTimeout(450);
      assert.equal(lastOwn(page).horizons['2028'].annual_forecast_changes['2027'], '1.5', 'the lobbying inputs are really in force before the reset');
      const b3 = own(page).length;
      await page.click('#gf-reset');
      assert.equal(await page.$eval('#gf-2028-growth', el => el.value), '-1.848');
      assert.equal(Number(await page.$eval('#gf-2026-House-political', el => el.value)), LEGACY.catalog.saved_outlook.control.House.value, 'the saved outlook is back');
      await recalculated(page, b3);
      const sent = lastOwn(page);
      assert.deepEqual([sent.horizons['2026'].growth_pct, sent.horizons['2028'].growth_pct], ['11.778', '-1.848'], 'Reset to defaults recalculates');
      assert.equal(Number(sent.horizons['2026'].control_scores.House), LEGACY.catalog.saved_outlook.control.House.value);
      // ... and everything else is back to blank, on the screen and in the request
      assert.deepEqual(await page.$$eval('#gf-2026-prior, #gf-2028-prior, #gf-2028-House-political-view, #gf-2028-Senate-political-view', all => all.map(el => el.value)), ['', '', '', '']);
      assert.deepEqual(await page.$$eval('#gf-2026-Senate-majority, #gf-2028-House-majority, #gf-2028-Senate-majority', all => all.map(el => el.value)).then(v => v.slice(1)), ['', ''], 'both 2027–28 selects read Choose again');
      assert.equal(await page.$eval('#gf-2026-Senate-majority', el => el.value), String(LEGACY.catalog.saved_outlook.control.Senate.current_control), 'the saved Senate control is back');
      assert.deepEqual(await page.$$eval('#gf-2026-lobby, #gf-2027-lobby, #gf-2028-lobby', all => all.map(el => el.value)), ['', '', '']);
      assert.deepEqual([sent.horizons['2026'].prior_growth_pct, sent.horizons['2028'].prior_growth_pct], ['', '']);
      assert.deepEqual({...sent.horizons['2028'].control_scores, ...Object.fromEntries(Object.entries(sent.horizons['2028'].current_control).map(([k, v]) => ['c' + k, v]))}, {House: '', Senate: '', cHouse: '', cSenate: ''});
      assert.deepEqual([...Object.values(sent.horizons['2026'].annual_forecast_changes), ...Object.values(sent.horizons['2028'].annual_forecast_changes)], ['', '', ''], 'lobbying by year is blank');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the saved outlook: the two 2026 control rows open filled with "Polymarket, Jan–Jun 2026" and a Clear link, and clear back to the unset state; the 2027–28 inputs open blank`, async () => {
    const page = await open({path: dataPath});
    try {
      const snapshot = (dataPath === 'landed' ? LANDED : LEGACY).catalog.saved_outlook;
      const rowState = chamber => page.evaluate(chamber => {
        const view = document.getElementById(`gf-2026-${chamber}-political-view`), pill = document.getElementById(`gf-2026-${chamber}-majority`), source = document.getElementById(`gf-2026-${chamber}-source`);
        const slider = view.closest('.gf-row').querySelector('.gf-slider'), clear = source.querySelector('.gf-source-clear');
        const visible = el => { const s = getComputedStyle(el), r = el.getBoundingClientRect(); return s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
        return {shown: view.value, held: document.getElementById(`gf-2026-${chamber}-political`).value, pill: pill.value, pillText: pill.options[pill.selectedIndex].text, caption: source.querySelector('.gf-source-text').textContent, captionVisible: visible(source), clearVisible: visible(clear), clearText: clear.textContent,
          unset: slider.classList.contains('is-unset'), valuetext: slider.getAttribute('aria-valuetext'), focused: document.activeElement === view, title: source.title};
      }, chamber);
      for (const chamber of ['House', 'Senate']) {
        const s = await rowState(chamber), saved = snapshot.control[chamber];
        assert.equal(Number(s.held), saved.value, chamber); assert.equal(s.shown, num(saved.value), chamber + ': the visible number is the saved one');
        assert.equal(s.pill, String(saved.current_control)); assert.equal(s.pillText, 'Republican');
        assert.equal(s.caption, SAVED_TEXT); assert.equal(s.captionVisible, true); assert.equal(s.clearVisible, true); assert.equal(s.clearText, 'Clear'); assert.equal(s.unset, false);
        assert.match(s.title, /^Saved 2026 outlook, as of 2026-09-24: Polymarket mean of the daily probability of Republican control from 2026-01-01 to 2026-06-30\./);
      }
      await page.click('#gf-more > summary');
      assert.deepEqual(await page.$$eval('#gf-2028-growth-view, #gf-2028-House-political-view, #gf-2028-Senate-political-view', all => all.map(el => el.value)).then(v => v.slice(1)), ['', ''], '2027–28 control scores are blank');
      assert.deepEqual(await page.$$eval('#gf-more-body .gf-pill-select', all => all.slice(2).map(el => el.value)), ['', ''], '... and both of its pills read Choose');
      assert.equal(await page.$$eval('#gf-more-body .gf-source', all => all.length), 0, 'no saved outlook is claimed for 2027–28');
      assert.equal(await page.$eval('#gf-2026-prior', el => el.value), '');
      await page.click('#gf-more > summary');
      // the request carries the saved scores and pills for 2026 and nothing for the rest
      const first = lastOwn(page);
      assert.equal(Number(first.horizons['2026'].control_scores.House), snapshot.control.House.value); assert.equal(first.horizons['2026'].current_control.House, String(snapshot.control.House.current_control));
      assert.deepEqual({...first.horizons['2028'].control_scores}, {House: '', Senate: ''}); assert.deepEqual({...first.horizons['2028'].current_control}, {House: '', Senate: ''});
      // clear the House row: the score and the pill go back to the real unset state; the Senate row is untouched; nothing below moves
      const place = () => page.$eval('#gf-reset', el => Math.round(el.getBoundingClientRect().top * 10) / 10);
      const resetAt = await place();
      const before = own(page).length;
      await page.click('[aria-label="Clear the saved House control score and its control before the election: leave both unset"]');
      const house = await rowState('House'), senate = await rowState('Senate');
      assert.deepEqual({shown: house.shown, held: house.held, pill: house.pill, pillText: house.pillText, unset: house.unset, valuetext: house.valuetext, focused: house.focused}, {shown: '', held: '', pill: '', pillText: 'Choose', unset: true, valuetext: 'Not set', focused: true});
      assert.equal(await page.$eval('#gf-2026-House-political-view', el => el.placeholder), '-');
      assert.equal(house.captionVisible, false, 'the caption goes quiet with the value it described'); assert.equal(house.clearVisible, false);
      assert.deepEqual({held: Number(senate.held), pill: senate.pill, captionVisible: senate.captionVisible, unset: senate.unset}, {held: snapshot.control.Senate.value, pill: String(snapshot.control.Senate.current_control), captionVisible: true, unset: false});
      assert.equal(await place(), resetAt, 'the rail keeps its height: Reset to defaults does not move');
      await recalculated(page, before);
      const cleared = lastOwn(page);
      assert.equal(cleared.horizons['2026'].control_scores.House, ''); assert.equal(cleared.horizons['2026'].current_control.House, '', 'clearing sends no value');
      assert.equal(Number(cleared.horizons['2026'].control_scores.Senate), snapshot.control.Senate.value);
      // typing in the cleared row does not bring the claim back; editing the Senate score ends its claim; neither moves the rail
      await page.fill('#gf-2026-House-political-view', '40');
      assert.equal((await rowState('House')).captionVisible, false);
      await page.fill('#gf-2026-Senate-political-view', '60');
      const edited = await rowState('Senate');
      assert.deepEqual({captionVisible: edited.captionVisible, clearVisible: edited.clearVisible, held: edited.held}, {captionVisible: false, clearVisible: false, held: '60'});
      assert.equal(await place(), resetAt);
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the historical example (earlier module; the cycle model hides it) is in the Methods fold and goes through the same pause-then-calculate path; its sentence follows the run`, async () => {
    const page = await open({path: dataPath, cycle: false});
    try {
      assert.equal(await page.$eval('#gf-example-pill', el => el.hidden), true, 'the example text is shown only while the example is loaded');
      assert.equal(await page.$eval('#gf-reference', el => el.closest('#gf-methods') !== null && !el.closest('.gf-rail')), true, '"Use historical example" lives in the Methods fold');
      await openMethods(page);
      const before = own(page).length;
      const said = await page.evaluate(() => { document.getElementById('gf-reference').click(); return {status: document.getElementById('gf-status').textContent, pill: document.getElementById('gf-example-pill').textContent, hidden: document.getElementById('gf-example-pill').hidden}; });
      const sentence = withFix ? EXAMPLE_CONDITIONED : EXAMPLE_PLAIN;
      assert.equal(said.hidden, false); assert.equal(said.pill, 'Historical example: 2023–2024 growth and control');
      assert.equal(own(page).length, before, 'nothing is sent yet: the pause comes first');
      await recalculated(page, before);
      assert.equal(own(page).length, before + 1, 'one calculation, started by the example itself, no button');
      const tip = await hoverTip(page, '#gf-example-pill');
      assert.ok(tip.includes(sentence), 'the tooltip carries the sentence of this calculator: ' + tip);
      assert.doesNotMatch(tip, /so the projection equals/);
      if (withFix) {
        assert.match(tip, /House change in R share, model, before conditioning on the giving already observed-1\.63 pp .*presidential or midterm cycle -1\.88 pp/);
        assert.match(tip, /Senate change in R share, model, before conditioning on the giving already observed\+6\.06 pp .*prior-cycle growth \+6\.06 pp/);
        assert.match(tip, /Senate change in R share, drawn \(conditioned\)/, 'once calculated the tooltip prints the change drawn');
      } else assert.doesNotMatch(tip, /change in R share/, 'a calculator without the fix names no input and conditions nothing');
      assert.match(await page.$eval('#gf-example-pill', el => el.getAttribute('aria-label')), new RegExp(sentence.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'the aria-label carries it too');
      assert.equal(await page.textContent('#gf-example-note'), sentence, 'also in Sources and notes');
      assert.doesNotMatch(await page.textContent('#giving-forecast'), /so the projection equals the Mentat level/);
      assert.equal(await page.$eval('#gf-2026-House-political-view', el => el.value), '44.92', 'the anchor cycle\'s control, as the example says');
      // the sentence agrees with the amounts DRAWN beside it
      const measure = measureOf(answerFor(page.stub.runs, lastOwn(page)), 0, 'general_giving');
      const total = withFix ? measure.cycle_given_observed_median : measure.projected_cycle_real_2025, level = measure.anchor_cycle_real_2025;
      assert.ok(await chartText(page).then(text => text.includes(M1(total)) && text.includes(M1(level))), 'the chart draws the total and the level compared here');
      const equal = Math.abs(total - level) < 0.5;
      assert.equal(equal, !withFix, 'with the fix the drawn total differs from the level, without it it equals it');
      const note = await page.$eval('#gf-example-note', el => el.textContent);
      if (equal) assert.match(note, /The 2025–2026 amounts equal the 2023–2024 Mentat level\./); else assert.match(note, /differ from the 2023–2024 Mentat level only because they are conditioned on the giving already observed\./);
      // changing an input ends the example: the text goes
      await page.fill('#gf-2026-growth-view', '12');
      assert.equal(await page.$eval('#gf-example-pill', el => el.hidden), true);
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the account control: General giving | PAC to candidates | Lobbying swap the main chart area, never stack or add; the amber state exists only without the model fix`, async () => {
    const page = await open({path: dataPath});
    try {
      const run = answerFor(page.stub.runs, lastOwn(page));
      assert.equal(await page.getAttribute('#gf-account-general', 'aria-pressed'), 'true');
      const general = await chartText(page);
      await page.click('#gf-account-pac');
      assert.deepEqual(await page.$$eval('.gf-account > button', all => all.map(b => b.getAttribute('aria-pressed'))), ['false', 'true', 'false']);
      await page.waitForTimeout(900);
      const pac = await chartText(page), pacMeasure = measureOf(run, 0, 'pac_to_candidates');
      assert.notEqual(pac, general);
      assert.equal(await page.$$eval('#gf-chart .gf-col-anchor', all => all.length), 1, 'one set of columns, not two stacked accounts');
      assert.ok(!pac.includes(M1(measureOf(run, 0, 'general_giving').anchor_cycle_real_2025)));
      assert.ok(pac.includes(M1(pacMeasure.anchor_cycle_real_2025)), 'the anchor level of the PACs own giving, in 2025 dollars: ' + M1(pacMeasure.anchor_cycle_real_2025));
      assert.ok(!pac.includes('$55') && !pac.includes('$33.1'), 'no sum of the two accounts and no shared scale');
      const lines = await warnings(page), pacLines = lines.filter(l => l.line.includes('· Company PACs: '));
      if (withFix) {
        assert.deepEqual(pacLines.filter(l => l.warn), [], 'nothing amber: the interval is conditioned on the giving already observed');
        assert.ok(pac.includes(M1(pacMeasure.cycle_given_observed_low)) && pac.includes(M1(pacMeasure.cycle_given_observed_high)));
        assert.ok(!pac.includes(M1(pacMeasure.cycle_prediction_low)));
      } else {
        assert.equal(pacLines.filter(l => l.warn).length, 1);
        assert.match(pacLines.find(l => l.warn).text, /Prediction interval starts below giving already observed/);
        assert.ok(pac.includes(M1(pacMeasure.cycle_prediction_low)) && pac.includes(M1(pacMeasure.cycle_prediction_high)), 'the unconditioned interval, drawn as returned (below the observed amount)');
        assert.match(pacLines.find(l => l.warn).detail, /not clipped/);
      }
      // Lobbying: the lobbying card takes the main chart area; the bars and their legend step aside
      await page.click('#gf-account-lobby'); await page.waitForTimeout(400);
      const area = await page.evaluate(() => {
        const box = el => { const r = el.getBoundingClientRect(); return {w: r.width, h: r.height, x: r.x, y: r.y}; };
        return {chartHidden: document.getElementById('gf-chart').hidden, lobby: box(document.getElementById('gf-lobby-chart')), chartCol: box(document.querySelector('.gf-chart-col')), title: document.getElementById('gf-hero-title').textContent, legend: [...document.querySelectorAll('#gf-legend-lobby li')].map(li => li.textContent),
          barLegendHidden: document.getElementById('gf-legend').hidden, svgs: document.querySelectorAll('#gf-lobby-chart svg').length, pressed: [...document.querySelectorAll('.gf-account > button')].map(b => b.getAttribute('aria-pressed')), text: document.getElementById('gf-lobby-chart').textContent};
      });
      assert.equal(area.chartHidden, true); assert.ok(area.lobby.w > area.chartCol.w - 70 && area.lobby.h >= 340, 'it fills the main chart area: ' + JSON.stringify(area.lobby));
      assert.equal(area.title, 'Lobbying by year'); assert.deepEqual(area.legend, ['In-house', 'Fees reported by outside firms', 'Projected (dashed)']); assert.equal(area.barLegendHidden, true);
      assert.doesNotMatch(area.text, /total lobbying spend|hired firms/i, 'no summed total and no "hired firms" anywhere on the card');
      assert.deepEqual(area.pressed, ['false', 'false', 'true']); assert.doesNotMatch(area.text, /Bars: 80% band/, 'the old caption is gone'); assert.equal(await page.$$eval('#gf-lobby-chart .gf-line', all => all.length), 0, 'no line chart any more');
      assert.equal(await page.$$eval('#gf-lobby-chart .gf-ld-bar', all => all.length), 9, 'blank growth is no change: the nowcast is drawn, with the history');
      // enter yearly lobbying growth in More assumptions (both horizons): the projected bars are drawn again from the answer
      await page.click('#gf-more > summary');
      await page.click('#gf-more-body .gf-more-group:nth-child(1) .gf-lobby-link');
      assert.equal(await page.$eval('#gf-lobby-pop-current', el => el.hidden), false);
      let before = own(page).length;
      await page.fill('#gf-2026-lobby', '0'); await page.click('#gf-lobby-pop-current .gf-pop-done');
      await page.click('#gf-more-body .gf-more-group:nth-child(2) .gf-lobby-link');
      await page.fill('#gf-2027-lobby', '0'); await page.fill('#gf-2028-lobby', '0'); await page.click('#gf-lobby-pop-next .gf-pop-done');
      await recalculated(page, before);
      // the Melange card (D-064): per year two bars side by side on one baseline (in-house, then the fees outside firms report; never stacked, never added), the partial year hatched, three projected bars with a whisker each, the two big figures
      const card = await page.evaluate(() => {
        const text = el => (el ? el.textContent : '');
        const bars = [...document.querySelectorAll('#gf-lobby-chart .gf-ld-bar')].map(g => ({kind: g.dataset.kind, year: g.dataset.year, rects: [...g.querySelectorAll('rect.gf-ld-seg')].map(r => ({fill: r.getAttribute('fill') || getComputedStyle(r).fill, dash: getComputedStyle(r).strokeDasharray, opacity: getComputedStyle(r).fillOpacity, title: text(r.querySelector('title')), x: +r.getAttribute('x'), w: +r.getAttribute('width'), foot: +r.getAttribute('y') + +r.getAttribute('height')})),
          whiskers: g.querySelectorAll('path.gf-ld-whisker').length, whiskerTitles: [...g.querySelectorAll('.gf-ld-whisker-hit title')].map(text), tick: text(g.querySelector('text.gf-ld-xt'))}));
        const svg = document.querySelector('#gf-lobby-chart svg');
        return {bars, title: text(document.querySelector('.gf-ld-title')), sub: text(document.querySelector('.gf-ld-sub')), partial: text(document.querySelector('.gf-ld-partial')), figs: [...document.querySelectorAll('.gf-ld-fig')].map(f => [text(f.querySelector('.gf-ld-big')), text(f.querySelector('.gf-ld-figlabel'))]),
          role: svg.getAttribute('role'), aria: svg.getAttribute('aria-label'), yTicks: [...svg.querySelectorAll('.gf-ld-yt')].map(t => t.textContent), patterns: svg.querySelectorAll('pattern').length, dot: getComputedStyle(document.querySelector('.gf-ld-dot')).backgroundColor,
          titleFont: getComputedStyle(document.querySelector('.gf-ld-title')).fontFamily, bigSize: getComputedStyle(document.querySelector('.gf-ld-big')).fontSize, stacks: document.querySelectorAll('#gf-lobby-chart .gf-line, #gf-lobby-chart .gf-series').length};
      });
      assert.deepEqual(card.bars.map(b => [b.kind, b.year, b.tick]), [['annual', '2021', '2021'], ['annual', '2022', '2022'], ['annual', '2023', '2023'], ['annual', '2024', '2024'], ['annual', '2025', '2025'], ['partial', '2026', '2026 partial'],
        ['projected', '2026', '2026 projected'], ['projected', '2027', '2027 projected'], ['projected', '2028', '2028 projected']]);
      for (const bar of card.bars) {
        assert.equal(bar.rects.length, 2, `${bar.tick}: two bars`); assert.ok(Math.abs(bar.rects[0].foot - bar.rects[1].foot) < 0.02, `${bar.tick}: one baseline, not stacked`);
        assert.ok(bar.rects[0].x + bar.rects[0].w <= bar.rects[1].x + 1e-6, `${bar.tick}: side by side, in-house first`);
        assert.ok(bar.rects.every(r => r.title.length > 0), `${bar.tick}: a <title> on each bar`);
        assert.match(bar.rects[0].title, /In-house/); assert.match(bar.rects[1].title, /Fees reported by outside firms/);
        if (bar.kind === 'partial') assert.ok(bar.rects.every(r => /^url\(#gf-ld-hatch-\d+[hi]\)$/.test(r.fill)), '2026 partial is hatched: ' + bar.rects.map(r => r.fill));
        if (bar.kind === 'projected') {
          assert.ok(bar.rects.every(r => r.opacity === '0.55' && r.dash !== 'none'), `${bar.tick}: 55% fill and a dashed outline`);
          assert.equal(bar.whiskers, 2, `${bar.tick}: one whisker per series, each the 80% band of its own series`); assert.equal(bar.whiskerTitles.length, 2);
          assert.match(bar.whiskerTitles[0], /^\d{4} projected · In-house · 80% band\n/); assert.match(bar.whiskerTitles[1], /^\d{4} projected · Fees reported by outside firms · 80% band\n/);
          assert.ok(bar.whiskerTitles.every(t => /\n80% band: \$/.test(t) && /the two are never merged/.test(t) && !/total/i.test(t)), `${bar.tick}: no band of a total`);
          assert.ok(bar.rects.every(r => /80% band: \$/.test(r.title)), `${bar.tick}: each bar's tooltip gives value and band`);
        } else assert.equal(bar.whiskers, 0);
      }
      assert.equal(card.patterns, 2); assert.equal(card.title, 'Bank'); assert.equal(card.sub, '2025: 100 organizations · 495 named lobbyists', 'the counts of the headline year, not the partial year\'s 97 and 463');
      assert.deepEqual(card.figs, [['$65.7M', '2025 in-house lobbying'], ['$23.8M', '2025 fees reported by outside firms']]);
      assert.equal(card.partial, '2026 through Q2: in-house $39.0M · outside-firm fees $13.4M');
      assert.equal(card.role, 'img'); assert.match(card.aria, /^Bank lobbying by year, in-house and fees reported by outside firms side by side and never added, nominal dollars\. 2021: in-house \$50\.2M, fees reported by outside firms \$18\.2M;/); assert.doesNotMatch(card.aria, /total/i);
      assert.equal(card.yTicks[0], '$0'); assert.equal(card.dot, 'rgb(118, 83, 153)', "Melange's bank colour (dot-1)"); assert.match(card.titleFont, /Freight|Georgia|serif/); assert.equal(card.bigSize, '50px'); assert.equal(card.stacks, 0);
      assert.match(await page.textContent('#gf-basis'), /^Annual, nominal dollars$/);
      assert.deepEqual(page.errors, []);
      // back to General giving: the bars return
      await page.click('#gf-account-general'); await page.waitForTimeout(300);
      assert.equal(await page.$eval('#gf-lobby-chart', el => el.hidden), true); assert.equal(await page.$eval('#gf-chart svg', el => !!el), true);
      assert.equal(await page.textContent('#gf-hero-title'), 'Giving by cycle'); assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] Party split: one card, House | Senate, Employees and Company PACs as 100% bars, Democratic left, a tick at the 2023–24 share, shares inside the ends; no chips anywhere`, async () => {
    const page = await open({path: dataPath});
    try {
      const run = answerFor(page.stub.runs, lastOwn(page));
      await page.waitForTimeout(1500);      // the entrance sweep is over
      const g = await page.evaluate(() => {
        const party = document.getElementById('gf-party');
        const cols = [...party.querySelectorAll('.gf-pcol')].map(col => ({title: col.querySelector('h3').textContent, size: getComputedStyle(col.querySelector('h3')).fontSize, rows: [...col.querySelectorAll('.gf-pb')].map(pb => {
          const track = pb.querySelector('.gf-ptrack'), d = track.querySelector('.gf-pd'), r = track.querySelector('.gf-pr'), tick = track.querySelector('.gf-ptick'), tb = track.getBoundingClientRect();
          return {label: pb.querySelector('.gf-pbl').textContent, labelSize: getComputedStyle(pb.querySelector('.gf-pbl')).fontSize, d: d.textContent, r: r.textContent, dBg: getComputedStyle(d).backgroundColor, rBg: getComputedStyle(r).backgroundColor, dLeft: d.getBoundingClientRect().left, rLeft: r.getBoundingClientRect().left, dW: d.getBoundingClientRect().width / tb.width, rW: r.getBoundingClientRect().width / tb.width, tickAt: (tick.getBoundingClientRect().left + tick.getBoundingClientRect().width / 2 - tb.left) / tb.width, h: tb.height, labelSizeIn: getComputedStyle(d.querySelector('em')).fontSize, labelWeight: getComputedStyle(d.querySelector('em')).fontWeight, tickBg: getComputedStyle(tick).backgroundColor, role: track.getAttribute('role'), aria: track.getAttribute('aria-label'), tab: track.tabIndex};
        })}));
        return {title: party.querySelector('h2').firstChild.textContent, heading: party.querySelector('h2').textContent, sub: document.getElementById('gf-party-sub').textContent, cap: document.getElementById('gf-party-cap').textContent, cols, chips: document.querySelectorAll('#giving-forecast .gf-chip, #giving-forecast .gf-pill, #giving-forecast [class*=chip]').length, h2Size: getComputedStyle(party.querySelector('h2')).fontSize};
      });
      assert.equal(g.title.trim(), 'Party split'); assert.equal(g.heading, 'Party split 2025–26, expected', 'a real space before the caption: the heading reads as words'); assert.equal(g.sub, '2025–26, expected'); assert.equal(g.cap, 'Tick = 2023–24'); assert.equal(g.h2Size, '20px');
      assert.deepEqual(g.cols.map(c => [c.title, c.size]), [['House', '15px'], ['Senate', '15px']]);
      for (const [c, chamber] of [[g.cols[0], 'House'], [g.cols[1], 'Senate']]) {
        assert.deepEqual(c.rows.map(r => r.label), ['Employees', 'Company PACs']);
        const bases = run.horizons[0].shares[chamber].bases, anchors = bases.map(b => b.anchor_share);
        c.rows.forEach((r, i) => {
          const R = drawnShare(bases.find(b => b.id === (i ? 'pac_candidate' : 'employee_candidate'))), anchor = bases.find(b => b.id === (i ? 'pac_candidate' : 'employee_candidate')).anchor_share;
          assert.equal(r.r, (R * 100).toFixed(1) + '%', `${chamber} ${r.label}: the Republican share inside the right end`); assert.equal(r.d, ((1 - R) * 100).toFixed(1) + '%', 'the Democratic share inside the left end');
          assert.ok(r.dLeft < r.rLeft, 'Democratic on the left'); assert.equal(r.dBg, DEM, 'party colours are the tokens'); assert.equal(r.rBg, REP);
          assert.ok(Math.abs(r.dW + r.rW - 1) < 0.002 && Math.abs(r.rW - R) < 0.002, '100% stacked');
          assert.ok(Math.abs(r.tickAt - (1 - anchor)) < 0.004, `${chamber} ${r.label}: the dark tick is the 2023–24 Republican share measured from the left: ${r.tickAt} vs ${1 - anchor}`);
          assert.deepEqual([r.h, r.labelSize, r.labelSizeIn, r.labelWeight, r.tickBg, r.role, r.tab], [24, '12px', '13px', '700', PLUM, 'img', 0]);
          assert.match(r.aria, new RegExp(`^${chamber} `));
        });
      }
      assert.equal(g.chips, 0, 'no chips or pills anywhere in the view');
      assert.equal(await page.$$eval('#gf-party .gf-pb', all => all.length), 4);
      assert.doesNotMatch(await page.textContent('#gf-party'), /combined|pooled|Roster/i);
      // a bar answers a hover with the numbers behind it; with the fix the model's own change is labelled as such
      for (const chamber of ['house', 'senate']) for (let i = 0; i < 2; i++) {
        const tip = await hoverTip(page, `#gf-${chamber}-body .gf-pb >> nth=${i} >> .gf-ptrack`);
        if (withFix) { assert.match(tip, /Change in Republican share, drawn \(conditioned\)/); assert.match(tip, /Model change before conditioning on the giving already observed[+-]?[\d.]+ pp on each basis/); } else assert.match(tip, /Change in Republican share[+-]?[\d.]+ pp on each basis/);
      }
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the foot: one collapsed disclosure; its Sources link opens it at Sources and notes; inside, in order, the published PAC table, the partisan baseline, Sources and notes, the warnings text, Use historical example`, async () => {
    const page = await open({path: dataPath, waivers: REGISTRY_WAIVERS});
    try {
      assert.equal(await page.$eval('#gf-methods', el => el.open), false);
      assert.equal(await page.$eval('#gf-sources-link', el => el.tagName), 'BUTTON', 'a text link that does not touch the page hash the console routes by');
      assert.equal(await page.evaluate(() => location.hash), '#insights');
      await page.click('#gf-sources-link');
      assert.equal(await page.$eval('#gf-methods', el => el.open), true, 'Sources opens the same fold');
      assert.equal(await page.evaluate(() => location.hash), '#insights');
      const where = await page.evaluate(() => { const r = document.getElementById('gf-notes').getBoundingClientRect(); return {top: r.top, vh: innerHeight, focus: document.activeElement.id}; });
      assert.ok(where.top >= -2 && where.top < where.vh - 80, 'scrolled to the Sources and notes section: ' + JSON.stringify(where)); assert.equal(where.focus, 'gf-notes-title');
      // the order inside the fold
      const order = await page.evaluate(() => ['pac-selected-forecast', 'pac-political-forecast', 'gf-notes', 'gf-warnings', 'gf-reference'].map(id => { const el = document.getElementById(id); return el && el.closest('#gf-methods') ? id : null; }));
      assert.deepEqual(order, ['pac-selected-forecast', 'pac-political-forecast', 'gf-notes', 'gf-warnings', 'gf-reference'], 'every piece is inside the fold');
      const ids = ['pac-selected-forecast', 'pac-political-forecast', 'gf-notes', 'gf-warnings', 'gf-reference'];
      const pairs = await page.evaluate(ids => ids.slice(1).map((id, i) => !!(document.getElementById(ids[i]).compareDocumentPosition(document.getElementById(id)) & Node.DOCUMENT_POSITION_FOLLOWING)), ids);
      assert.deepEqual(pairs, [true, true, true, true], 'in this order');
      assert.equal(await page.$eval('#pac-selected-forecast', el => el.previousElementSibling === null && el.parentElement.id === 'gf-methods-panels'), true);
      assert.equal(await page.$eval('#pac-political-forecast h2', el => el.textContent.length > 0), true, 'the partisan panel is whole');
      assert.equal(await page.$eval('#giving-forecast', el => [...el.children].some(c => c.id === 'pac-selected-forecast' || c.id === 'pac-political-forecast')), false, 'neither panel sits above the view any more');
      assert.equal(await page.$eval('#gf-account-pac', el => el.textContent), 'Company PACs', 'the runtime relabel to "Company-roster PAC scenario" is gone');
      // two populations, two names: the account button is the companies' own PACs; the table in the fold is the published forecast for the industry-category rosters
      const tableTitle = await page.$eval('#pac-selected-title', el => el.textContent);
      assert.match(tableTitle, /^Published PAC-to-candidate forecast for the industry-category rosters/); assert.notEqual(tableTitle, 'Company PACs'); assert.doesNotMatch(tableTitle, /Company PACs/);
      assert.equal(await page.$eval('.gf-foot', el => el.closest('#gf-form') === null), true, 'the fold is not inside the form that stays hidden until the catalog loads');
      // the panels' own controls do not recalculate the scenario
      const before = own(page).length;
      await page.selectOption('#pac-political-variant', 'baseline'); await page.waitForTimeout(500);
      assert.equal(own(page).length, before, 'changing the partisan comparison asks the calculator for nothing');
      // Sources and notes keeps everything the old panel printed, the old footnote line included
      await page.click('#gf-account-general');
      const notes = await page.textContent('#gf-notes');
      assert.match(await page.textContent('#gf-footnote'), /Mentat levels; OpenSecrets data only estimates the relationships\. Observed through 2026-06-30 · 41 of 67 work units complete · No 2026 CPI yet$/);
      for (const needle of ['Mentat levels for the industry\'s tracked companies', 'Mentat anchors: Banking Industry Spending Overview.xlsx', 'Included receipts the accounting engine buckets as non_giving',
        'Full numbers by outcome', 'Model evidence and ordinary p-values', 'Industry revenue growth: the average annual growth', 'Lobbying by year: optional. Blank is no change from the Melange LDA nowcast', 'must not be added', 'Observed through 2026-06-30', 'Assumption source', 'Assumptions dated']) {
        assert.ok(notes.includes(needle) || notes.replace(/\s+/g, ' ').includes(needle), 'Sources and notes keeps: ' + needle);
      }
      assert.match(notes, /Remainder prediction interval: -\$937\.21K to \$4\.73M/, 'the model\'s own unconditioned numbers are in the table behind the notes');
      assert.match(notes, /budget_inconsistent: The projected total and share are incompatible/); assert.match(notes, /Ordinary p = |ordinary p = /);
      if (withFix) { assert.match(notes, /Conditioned on the giving already observed/); assert.match(notes, /Remainder, median/); assert.match(notes, /negative before conditioning/); assert.match(notes, /The table below is the model's own unconditioned arithmetic/); } else assert.doesNotMatch(notes, /Conditioned on the giving already observed/);
      await page.click('#gf-evidence summary');
      assert.match(await page.textContent('#gf-evidence-content'), /PAC amount · Commercial banks/); assert.match(await page.textContent('#gf-evidence-content'), /Ordinary p/);
      // the fold closes again by its own line, with the keyboard too
      await page.focus('#gf-methods > summary'); await page.keyboard.press('Enter');
      assert.equal(await page.$eval('#gf-methods', el => el.open), false);
      await page.focus('#gf-sources-link'); await page.keyboard.press('Enter');
      assert.equal(await page.$eval('#gf-methods', el => el.open), true, 'the Sources link opens it from the keyboard');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] the model's flags are plain text in the fold (warnings before notes, scope first), the candidate-mix amber state belongs to the calculator without the fix; nothing is a chip`, async () => {
    const page = await open({path: dataPath});
    try {
      const lines = await warnings(page);
      assert.ok(lines.length >= 1); assert.equal(await page.$eval('#gf-warnings', el => el.hidden), false);
      assert.ok(lines.every(l => /^(Warning|Note) · /.test(l.text)), 'each line says what it is and what it concerns');
      assert.equal(await page.$$eval('#giving-forecast .gf-chip', all => all.length), 0);
      const house = lines.filter(l => l.line.startsWith('Note · House') || l.line.startsWith('Warning · House')), senate = lines.filter(l => /· Senate: /.test(l.line));
      assert.ok(house.some(l => l.line.includes(RANGE_TEXT) && !l.warn), 'out-of-range input: a note, in the model\'s sentence');
      if (withFix) {
        assert.deepEqual(senate.filter(l => l.warn), [], 'no amber: the remaining amounts are not negative');
        assert.deepEqual(senate.map(l => l.line.replace(/^Note · Senate: /, '')), [RANGE_TEXT], 'the conditioned row has no line of its own: its tooltip says what was conditioned');
      } else {
        assert.deepEqual(senate.filter(l => l.warn).map(l => l.line.replace(/^Warning · Senate: /, '')), [BUDGET_TEXT]);
        assert.match(senate.find(l => l.warn).detail, /budget_inconsistent: The projected total and share are incompatible/);
      }
      assert.match(await hoverTip(page, '#gf-senate-body .gf-pb >> nth=1 >> .gf-ptrack'), withFix ? /Remaining from 2026-07-01 \(median\)R \$150\.68K · D \$62\.88K of \$213\.56K/ : /R -\$135\.67K · D \$32\.16K/);
      assert.equal(await page.$$eval('#gf-house-body .gf-pb', all => all.length), 2, 'two bases, never a pooled third');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] owner waivers: a note under House in the fold, the full list in Sources and notes (registry-shaped entries, then the shape the calculator catalog carries)`, async () => {
    const none = await open({path: dataPath});
    try { assert.equal((await warnings(none)).filter(l => /Gate waived/.test(l.text)).length, 0); } finally { await closePage(none); }
    const page = await open({path: dataPath, waivers: REGISTRY_WAIVERS});
    try {
      const lines = (await warnings(page)).filter(l => /Gate waived/.test(l.text));
      assert.deepEqual(lines.map(l => [l.warn, l.line]), [[false, 'Note · House: Gate waived by owner: House share']]);
      assert.match(lines[0].detail, /Cell: control_House \/ commercial_banks.*Gate: G6.*Owner: Paul Goff, 2026-09-30/);
      assert.equal((await warnings(page)).filter(l => /· Senate: .*waived/.test(l.line)).length, 0);
      const waivers = await page.textContent('#gf-waivers');
      assert.match(waivers, /Owner waivers of the refit gates/);
      for (const cell of REGISTRY_WAIVERS.map(w => w.cell)) assert.ok(waivers.includes(cell), cell + ' is listed');
      assert.match(waivers, /listed here only/);
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
    const shaped = await open({path: dataPath, waivers: [DISPLAY_WAIVER], width: 1440});
    try {
      const line = (await warnings(shaped)).find(l => /waived by owner/i.test(l.text));
      assert.ok(line.text.includes(DISPLAY_WAIVER.display_text), 'the entry\'s own display text');
      assert.match(line.detail, new RegExp(measuredText(DISPLAY_WAIVER)));
      assert.deepEqual(shaped.errors, []);
    } finally { await closePage(shaped); }
  });

  test(`[${label}] credit unions: the single-PAC thin base, the own-history band warning and the thin-base mix rows are warning text`, async () => {
    const page = await open({path: dataPath});
    try {
      let before = own(page).length;
      await page.selectOption('#gf-industry', 'credit_unions');
      await recalculated(page, before);
      await page.click('#gf-account-pac');
      await page.waitForTimeout(900);
      const lines = await warnings(page);
      assert.ok(lines.some(l => /^Note · Company PACs: One PAC \(SchoolsFirst Federal Credit Union\): a thin base/.test(l.line) && !l.warn), 'single_pac_thin_base is a note');
      assert.ok(lines.some(l => l.line === 'Note · Company PACs: Coverage cut back to 2026-05-31'));
      const band = lines.filter(l => l.line.endsWith(INTERVAL_TEXT));
      assert.equal(band.length, withFix ? 1 : 0, 'the own-history warning is the model fix\'s flag');
      if (withFix) assert.equal(band[0].warn, true);
      const house = lines.filter(l => /· House: /.test(l.line));
      assert.deepEqual(house.filter(l => /Thin base/.test(l.line)).map(l => l.line.replace(/^(Note|Warning) · House: /, '')), ['Thin base: Employees and Company PACs']);
      assert.equal(house.filter(l => l.warn && !l.line.includes(WIDE_TEXT)).length, withFix ? 0 : 1, 'the House employee basis is budget_inconsistent without the fix');
      // a row whose drawn share interval leaves 0-100% is not clipped: a warning says so; the response decides
      for (const chamber of ['House', 'Senate']) {
        const response = answerFor(page.stub.runs, lastOwn(page)).horizons[0].shares[chamber].bases;
        const wide = response.filter(b => b.status === 'ready').map(b => { const a = b.remaining_allocation; const low = a ? a.implied_cycle_R_share_prediction_low : b.cycle_prediction_low, high = a ? a.implied_cycle_R_share_prediction_high : b.cycle_prediction_high; return low < 0 || high > 1; });
        const mine = lines.filter(l => l.line.includes(`· ${chamber}: `) && l.line.includes(WIDE_TEXT));
        assert.equal(mine.length > 0, wide.some(Boolean), `${chamber}: the warning follows the interval of the response`);
        if (wide.some(Boolean)) { assert.equal(mine[0].warn, true); assert.match(mine[0].detail, /not clipped to 0–100%/); }
        assert.equal(await page.$$eval(`#gf-${chamber.toLowerCase()}-body .gf-ptrack`, all => all.length), 2);
      }
      // F9: under Lobbying the bar chart is not on screen, so its flags are not listed (the Party split stays, and so do its notes)
      await page.click('#gf-account-lobby'); await page.waitForTimeout(300);
      const lobbying = await warnings(page);
      assert.deepEqual(lobbying.filter(l => /· (Company PACs|General giving): /.test(l.line)), [], 'no flag of the last bar account under Lobbying');
      assert.ok(lobbying.some(l => /· House: /.test(l.line)), 'the Party split\'s notes stay');
      await page.click('#gf-account-pac'); await page.waitForTimeout(300);
      assert.ok((await warnings(page)).some(l => /· Company PACs: /.test(l.line)), 'and the PAC account lists its own again');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] states: a calculation in flight keeps the columns and shows progress; a failure is said in the card; a blank scenario draws dashes, not zeros`, async () => {
    const page = await open({path: dataPath});
    try {
      const before = await chartText(page);
      page.stub.delay = 900;
      const n = own(page).length;
      await page.fill('#gf-2026-growth-view', '6');
      await page.waitForFunction(() => document.getElementById('gf-chart').getAttribute('aria-busy') === 'true');
      assert.equal(await page.$eval('#gf-progress', el => el.hidden), false, 'a thin progress bar under the title');
      assert.equal(await chartText(page), before, 'the columns keep their last values while it runs');
      await page.waitForFunction(() => document.getElementById('gf-chart').getAttribute('aria-busy') === 'false');
      assert.equal(await page.$eval('#gf-progress', el => el.hidden), true);
      assert.equal(own(page).length, n + 1);
      // failure: an inline message that names what failed and what to change
      page.stub.delay = 0; page.stub.failure = 500;
      await page.fill('#gf-2026-growth-view', '7');
      await page.waitForFunction(() => !document.getElementById('gf-alert').hidden);
      const alert = await page.textContent('#gf-alert');
      assert.match(alert, /Not calculated\./); assert.match(alert, /The forecast service could not finish this calculation\./); assert.match(alert, /Change the input it names, or reload the page, then try again\./); assert.doesNotMatch(alert, /calculate again/i);
      assert.equal(await page.$eval('#gf-export', el => el.disabled), true);
      assert.equal(await page.$eval('#gf-alert', el => getComputedStyle(el).borderLeftStyle), 'solid', 'an alert line, not a card');
      // "Try again": a text button inside the alert; pressing it sends ONE request (here the service has recovered, so the answer is drawn and the alert goes)
      assert.deepEqual(await page.$$eval('#gf-alert button', all => all.map(el => [el.textContent, el.type, el.className.includes('gf-link')])), [['Try again', 'button', true]]);
      await page.waitForTimeout(450);
      page.stub.failure = 0;
      const beforeRetry = own(page).length;
      await page.click('#gf-alert button');
      await page.waitForFunction(() => document.getElementById('gf-alert').hidden);
      await page.waitForTimeout(600);
      assert.equal(own(page).length, beforeRetry + 1, 'Try again sends exactly one request');
      assert.equal(lastOwn(page).horizons['2026'].growth_pct, '7', 'the same scenario');
      assert.equal(await page.$eval('#gf-export', el => el.disabled), false, 'and the projection is back and exportable');
      // a service that is still down: the button is still there, one more press is one more request
      page.stub.failure = 500;
      await page.fill('#gf-2026-growth-view', '7.5');
      await page.waitForFunction(() => !document.getElementById('gf-alert').hidden);
      await page.waitForTimeout(450);
      const stillDown = own(page).length;
      await page.click('#gf-alert button'); await page.waitForTimeout(500);
      assert.equal(own(page).length, stillDown + 1); assert.equal(await page.$$eval('#gf-alert button', all => all.length), 1, 'still one Try again, not a pile of them');
      page.stub.failure = 0;
      const recovered = own(page).length;
      await page.fill('#gf-2026-growth-view', '7');
      await recalculated(page, recovered);
      await page.waitForFunction(() => document.getElementById('gf-alert').hidden);
      // a blank scenario: the open cycle keeps its observed segment and the missing input is a dash with the reason in its tooltip
      await page.fill('#gf-2026-growth-view', ''); await page.fill('#gf-2028-growth-view', '');
      const b2 = own(page).length; await recalculated(page, b2);
      await page.waitForFunction(() => document.getElementById('gf-chart').getAttribute('aria-busy') === 'false' && document.getElementById('gf-alert').hidden);
      const text = await chartText(page);
      assert.ok(text.includes(M1(measureOf(page.stub.runs.banks, 0, 'general_giving').observed.amount_real_2025)), 'the observed segment is real data and stays');
      assert.ok(!/\$15\.6/.test(text), 'no remainder is drawn without growth');
      assert.equal((await page.$$eval('#gf-chart .gf-dash', all => all.map(el => el.textContent))).join(''), '--', 'a dash in each open slot');
      assert.match(await hoverTip(page, '#gf-chart .gf-mark-current'), /Enter expected two-year cycle revenue growth\./);
      assert.deepEqual(await page.$$eval('#gf-house-body .gf-bar-dash', all => all.map(el => el.textContent)), ['-', '-']);
      assert.equal(await page.$$eval('#gf-chart .gf-gold', all => all.length), 0, 'no value, no gold mark');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] layout: two columns at 1600 and 1440; one column, stacked party columns, no horizontal scroll and 44 px targets at 390; the lobbying dialog stays inside the rail`, async () => {
    for (const [width, height] of [[1600, 900], [1440, 900], [390, 844]]) {
      const page = await open({path: dataPath, width, height});
      try {
        const sizes = await page.evaluate(() => {
          const box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom}; };
          const q = s => document.querySelector(s);
          return {main: box(q('.gf-main')), chart: box(q('.gf-chart-col')), rail: box(q('.gf-rail')), house: box(q('#gf-house')), senate: box(q('#gf-senate')), party: box(q('#gf-party')), chartSvg: box(q('#gf-chart svg')), account: box(q('.gf-account')),
            targets: [...document.querySelectorAll('.gf-account > button, #gf-view-switch > button, #gf-dollars > button, #gf-more > summary, #gf-methods > summary, #gf-reset, #gf-sources-link, #gf-export')].map(el => Math.round(el.getBoundingClientRect().height)),
            scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth, overflowing: [...document.querySelectorAll('#giving-forecast *')].filter(el => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1 && el.getBoundingClientRect().width > 0 && !el.closest('svg') && !el.closest('.gf-sr') && !el.closest('#gf-tip') && !el.closest('[hidden]') && !el.closest('details:not([open])') && !el.closest('.pac-selected-scroll, .gf-table-wrap')).map(el => el.id || el.className).slice(0, 5)};
        });
        assert.ok(sizes.scrollWidth <= sizes.clientWidth, `no horizontal scroll at ${width}: ${sizes.scrollWidth} > ${sizes.clientWidth}`);
        assert.deepEqual(sizes.overflowing, [], 'nothing sticks out of the page');
        if (width >= 1100) {
          assert.ok(sizes.rail.x > sizes.chart.x + sizes.chart.w - 2, 'the rail sits beside the chart');
          assert.ok(Math.abs(sizes.chart.w / sizes.main.w - 0.68) <= 0.006 && Math.abs(sizes.rail.w / sizes.main.w - 0.32) <= 0.006, 'about 68% | 32%');
          assert.ok(sizes.house.x < sizes.senate.x && Math.abs(sizes.house.y - sizes.senate.y) <= 1, 'House | Senate in two columns');
          assert.ok(sizes.chartSvg.w <= sizes.chart.w - 40, 'the chart fits its column');
        } else {
          assert.ok(sizes.rail.y >= sizes.chart.y + sizes.chart.h - 2, 'one column: the rail is under the chart');
          assert.ok(sizes.senate.y > sizes.house.y + sizes.house.h - 2, 'the party columns stack');
          assert.ok(sizes.main.w > width - 60, 'full width card');
          assert.ok(sizes.targets.every(h => h >= 44 || h === 0), 'touch targets are at least 44 px: ' + sizes.targets);
        }
        // the lobbying dialogs open inside the rail, never over its heading
        await page.click('#gf-more > summary');
        for (const which of ['current', 'next']) {
          await page.click(`#gf-more-body .gf-more-group:nth-child(${which === 'current' ? 1 : 2}) .gf-lobby-link`);
          const geometry = await page.evaluate(which => {
            const rect = el => el.getBoundingClientRect();
            const pop = rect(document.getElementById('gf-lobby-pop-' + which)), rail = rect(document.querySelector('.gf-rail'));
            const meets = el => { const r = rect(el); return pop.left < r.right && r.left < pop.right && pop.top < r.bottom && r.top < pop.bottom; };
            return {open: !document.getElementById('gf-lobby-pop-' + which).hidden, coversHeading: meets(document.getElementById('gf-scenario-title')), insideRail: pop.left >= rail.left - 1 && pop.right <= rail.right + 1 && pop.left >= 0 && pop.right <= innerWidth};
          }, which);
          assert.deepEqual(geometry, {open: true, coversHeading: false, insideRail: true}, `the ${which} lobbying dialog at ${width} px`);
          await page.keyboard.press('Escape');
          assert.equal(await page.$eval('#gf-lobby-pop-' + which, el => el.hidden), true, 'Escape closes it');
        }
        assert.deepEqual(page.errors, []);
      } finally { await closePage(page); }
    }
  });

  test(`[${label}] type scale 12 / 13 / 15 / 20 / 28 px only, one gold mark, party colours from tokens, no dead selectors`, async () => {
    const page = await open({path: dataPath});
    try {
      await openMethods(page); await page.click('#gf-more > summary'); await page.click('#gf-evidence summary');
      const scan = () => page.evaluate(() => {
        const root = document.getElementById('giving-forecast'), sizes = {}, gold = [];
        for (const el of root.querySelectorAll('*')) {
          if (el.closest('[hidden]') || el.closest('.gf-sr') || el.getClientRects().length === 0) continue;
          const cs = getComputedStyle(el), text = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
          if (text || ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) (sizes[cs.fontSize] = sizes[cs.fontSize] || []).push(el.tagName.toLowerCase() + '.' + String(el.className.baseVal != null ? el.className.baseVal : el.className).split(' ')[0]);
          for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'fill', 'stroke']) if (cs[prop] === 'rgb(236, 169, 28)') gold.push(el.tagName + '.' + (el.className.baseVal != null ? el.className.baseVal : el.className) + ':' + prop);
        }
        return {sizes, gold};
      });
      const at = await scan(), off = Object.entries(at.sizes).filter(([size]) => !SCALE.includes(size)).map(([size, who]) => size + ' ' + who.slice(0, 4).join(','));
      assert.deepEqual(off, [], 'only the type scale is used: ' + JSON.stringify(Object.keys(at.sizes)));
      assert.deepEqual(at.gold.filter(g => !/gf-gold/.test(g)), [], 'gold is used once, for the underline');
      assert.equal(at.gold.length, 1);
      // one serif for titles
      const fonts = await page.$$eval('#giving-forecast h2', all => [...new Set(all.filter(h => h.offsetParent).map(h => getComputedStyle(h).fontFamily))]);
      assert.equal(fonts.length, 1, 'one serif for titles: ' + fonts); assert.match(fonts[0], /serif|Georgia|Freight/);
      // party colours are the tokens, in the view and in the partisan panel
      const tokens = await page.evaluate(() => { const r = getComputedStyle(document.documentElement); return {dem: r.getPropertyValue('--party-dem').trim(), rep: r.getPropertyValue('--party-rep').trim(), d: getComputedStyle(document.querySelector('.pac-party-d')).getPropertyValue('--party-bar').trim(), r: getComputedStyle(document.querySelector('.pac-party-r')).getPropertyValue('--party-bar').trim()}; });
      assert.deepEqual([tokens.d, tokens.r], [tokens.dem, tokens.rep], 'the partisan panel draws with the same tokens (its own --party-bar values are gone)');
      const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'giving-forecast.css'), 'utf8'), pacCss = fs.readFileSync(path.join(__dirname, '..', 'app', 'pac-giving-forecast.css'), 'utf8');
      for (const dead of ['.gf-settings', '.gf-heading', '.gf-actions', '.gf-detail strong', '.gf-scope-card', '.gf-hero', '.gf-row3', '.gf-chip', '.gf-tabs', '.gf-scenario', '.gf-footnote-link', '.gf-notes-link', '.gf-text-button']) assert.ok(!css.includes(dead), 'dead selector removed: ' + dead);
      assert.ok(!/#3268a8|#bc584e/i.test(pacCss), 'pac-giving-forecast.css keeps none of its own party hexes');
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });

  test(`[${label}] roles and keyboard: sliders, the pressed account, disclosures as <details>, the whole rail reachable`, async () => {
    const page = await open({path: dataPath});
    try {
      assert.deepEqual(await page.$$eval('#gf-horizons .gf-slider', all => all.map(el => [el.getAttribute('role'), el.tabIndex, el.getAttribute('aria-valuemin') !== null, (el.getAttribute('aria-label') || '').length > 0])), [['slider', 0, true, true], ['slider', 0, true, true], ['slider', 0, true, true], ['slider', 0, true, true]]);
      // every control that appears in both horizons carries its horizon in its name; no two controls share a name; each More-assumptions group is a labelled group
      await page.click('#gf-more > summary');
      const names = await page.evaluate(() => [...document.querySelectorAll('#gf-horizons .gf-slider, #gf-horizons .gf-num, #gf-more-body .gf-slider, #gf-more-body .gf-num, #gf-more-body select, #gf-more-body .gf-inline, #gf-more-body .gf-lobby-link')].map(el => ({role: el.getAttribute('role') || el.tagName.toLowerCase(), name: el.getAttribute('aria-label')})));
      assert.ok(names.every(n => n.name), 'every one has an explicit name: ' + JSON.stringify(names.filter(n => !n.name)));
      const key = n => n.role + '|' + n.name;
      assert.equal(new Set(names.map(key)).size, names.length, 'no two controls of one kind share a name: ' + names.map(key).join(' || '));
      for (const needle of ['House: chance of Republican control, 2025–26', 'House: chance of Republican control, 2027–28', 'Senate: chance of Republican control, 2027–28', 'House control before election, 2025–26', 'House control before election, 2027–28', 'Senate control before election, 2027–28', 'Lobbying by year, 2025–26', 'Lobbying by year, 2027–28']) assert.ok(names.some(n => n.name === needle), 'a control is named: ' + needle);
      assert.ok(names.filter(n => /^Prior-cycle growth override/.test(n.name)).map(n => n.name.split(',')[1].trim()).join() === '2025–26,2027–28', 'prior-cycle growth names its horizon too');
      assert.deepEqual(await page.$$eval('#gf-more-body .gf-more-group', all => all.map(g => [g.getAttribute('role'), document.getElementById(g.getAttribute('aria-labelledby')).textContent, document.getElementById(g.getAttribute('aria-labelledby')).tagName])), [['group', '2025–26', 'H3'], ['group', '2027–28', 'H3']]);
      assert.equal(await page.getByRole('slider', {name: 'House: chance of Republican control, 2027–28', exact: true}).count(), 1);
      assert.equal(await page.getByRole('group', {name: '2027–28', exact: true}).count(), 1);
      await page.click('#gf-more > summary');
      assert.deepEqual(await page.$$eval('#gf-more, #gf-methods', all => all.map(el => el.tagName)), ['DETAILS', 'DETAILS']);
      assert.equal(await page.$eval('.gf-account', el => el.getAttribute('role')), 'group');
      assert.equal(await page.$eval('.gf-main', el => el.getAttribute('aria-label')), 'Giving by cycle and assumptions');
      // Tab walks the scope row, the account control, the four rows, More assumptions, Reset, the party bars, the foot
      await page.focus('#gf-industry');
      const seen = [];
      for (let i = 0; i < 30; i++) { await page.keyboard.press('Tab'); seen.push(await page.evaluate(() => document.activeElement.id || document.activeElement.className.baseVal || document.activeElement.className || document.activeElement.tagName)); }
      const joined = seen.join('|');
      for (const id of ['gf-date', 'gf-account-general', 'gf-account-pac', 'gf-account-lobby', 'gf-2026-growth-view', 'gf-2028-growth-view', 'gf-2026-House-political-view', 'gf-2026-Senate-political-view', 'gf-reset', 'gf-sources-link']) assert.ok(joined.includes(id), id + ' is reachable by Tab: ' + joined);
      // the disclosure opens with Enter on its summary
      await page.focus('#gf-more > summary'); await page.keyboard.press('Enter');
      assert.equal(await page.$eval('#gf-more', el => el.open), true);
      assert.deepEqual(page.errors, []);
    } finally { await closePage(page); }
  });
}

test('the live calculator as recorded now (model fix P1, owner waivers, no pooled reference, general giving without the non-giving receipts): the page draws its own answers', async () => {
  const general = run => run.horizons[0].pac.measures.find(m => m.id === 'general_giving');
  const page = await open({path: 'landed'});
  try {
    // the saved 2026 outlook of the live catalog opens in both control rows with its source beside them
    assert.deepEqual(await page.$$eval('#gf-2026-House-source .gf-source-text, #gf-2026-Senate-source .gf-source-text', all => all.map(el => el.textContent)), [SAVED_TEXT, SAVED_TEXT]);
    // banks: the conditioned open cycle leaves nothing to flag amber; the owner waiver of the House share is the entry's own display text, a note
    const lines = await warnings(page);
    assert.equal(lines.filter(l => l.warn).length, 0, 'nothing contradicts the giving already observed');
    assert.deepEqual(lines.filter(l => /waived/i.test(l.text)).map(l => [l.warn, l.line]), [[false, 'Note · House: ' + DISPLAY_WAIVER.display_text]]);
    assert.equal(lines.filter(l => /· Senate: .*waived/i.test(l.line)).length, 0);
    assert.match(lines.find(l => /waived/i.test(l.text)).detail, new RegExp(measuredText(DISPLAY_WAIVER)));
    // the Mentat level says what the general-giving measure leaves out; the PAC account says nothing of the kind
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor'), /Receipts the accounting engine marks non-giving \(interest, refunds, insurance claims, loans\) are left out/);
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    assert.doesNotMatch(await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor'), /non-giving/);
    assert.match(general(LANDED.runs.banks).label, /^Mentat general giving \(all channels\), excluding receipts Mentat marks non-giving/);
    // the notes name the measure by that label and list every waiver the catalog carries
    await openMethods(page);
    assert.ok((await page.textContent('#gf-notes')).includes(general(LANDED.runs.banks).label), 'the comparison table names the measure by the response label');
    const waivers = await page.textContent('#gf-waivers');
    for (const entry of LANDED.catalog.waivers) assert.ok(waivers.includes(entry.cell), entry.cell + ' is listed');
    // credit unions: the flags the response raises (looked up by name) are warning text; its House waiver is its own entry
    const before = own(page).length;
    await page.selectOption('#gf-industry', 'credit_unions'); await recalculated(page, before);
    await page.click('#gf-account-pac'); await page.waitForTimeout(600);
    const flags = general(LANDED.runs.credit_unions).flags || [], cu = await warnings(page);
    assert.equal(cu.some(l => l.warn && l.line.endsWith(INTERVAL_TEXT)), (flags.includes('interval_not_supported_by_own_history')), 'the own-history band warning follows the flag of the response');
    const cuWaiver = LANDED.catalog.waivers.find(w => w.industry === 'credit_unions' && w.chamber === 'House' && w.show_warning === true);
    assert.equal(cu.filter(l => /waived/i.test(l.text) && /· House: /.test(l.line)).length, cuWaiver ? 1 : 0);
    if (cuWaiver) assert.ok(cu.some(l => l.line.includes(cuWaiver.display_text)));
    // the credit-union Senate roster-PAC share interval of the live calculator leaves 0-100% (-1.89% to 4.69%): not clipped, said so in a warning
    assert.ok(cu.some(l => l.warn && /· Senate: /.test(l.line) && l.line.includes(WIDE_TEXT)), JSON.stringify(cu.map(l => l.line)));
    assert.match(await hoverTip(page, '#gf-senate-body .gf-pb >> nth=1 >> .gf-ptrack'), /95% prediction interval-1\.89% to 4\.69%/);
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

// ---- the cycle-aware calculator (applied 2026-10-07): its own recorded answers (tests/fixtures/giving_forecast_cycle_live.json) -------------------------------------------------
// The first render of this answer threw "Cannot read properties of undefined (reading '0')" (pacCellV2 read p.coefficient_interval_pct[0]); the card showed "Not calculated."
test('the cycle-aware calculator as recorded now: the page draws its answers (banks 2025-26 general giving $14.47M, company PACs $6.06M on the panel nowcast), no alert, captions, range, copy and Methods in place', async () => {
  const page = await open({path: 'cycle'});
  try {
    // one calculation on open, with the IBIS defaults and the marker; the reference run asks for the defaults as well
    const first = own(page);
    assert.equal(first.length, 1, 'one calculation on open');
    assert.equal(first[0].growth_basis, 'ibis_window_avg');
    assert.deepEqual([first[0].horizons['2026'].growth_pct, first[0].horizons['2028'].growth_pct], ['11.778', '-1.848']);
    const refs = page.requests.filter(r => /^Reference levels only/.test(r.assumption_source || ''));
    assert.ok(refs.length >= 1 && refs.every(r => r.growth_basis === 'ibis_window_avg' && r.horizons['2026'].growth_pct === '11.778' && r.horizons['2028'].growth_pct === '-1.848'), 'the reference run: the defaults, never the anchor cycle\'s two-year growth');
    assert.equal(await page.$eval('#gf-alert', el => el.hidden), true, 'no alert');
    assert.match(await page.textContent('#gf-status'), /^Both horizons calculated/);
    assert.equal(await page.$eval('#gf-export', el => el.disabled), false);
    // the chart: general giving 14.47M (D-121; was 13.86M) and company PACs 6.06M (panel nowcast, D-116) for 2025-26, the chained step for 2027-28
    const general = measureOf(CYCLE.runs.banks, 0, 'general_giving'), pac = measureOf(CYCLE.runs.banks, 0, 'pac_to_candidates');
    assert.equal(M1(general.cycle_given_observed_median), '$14.5M'); assert.equal(M1(pac.cycle_given_observed_median), '$6.1M');
    let text = await chartText(page);
    assert.ok(text.includes('$14.5M') && text.includes(M1(measureOf(CYCLE.runs.banks, 1, 'general_giving').projected_cycle_real_2025)), 'general giving: ' + text);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-next .gf-col-projection'), /\$21\.15M/);
    assert.deepEqual(await page.$$eval('#gf-legend li', all => all.map(li => li.textContent.trim())), ['Observed', 'Expected', '95% range']);
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    text = await chartText(page);
    assert.ok(text.includes('$6.1M') && text.includes(M1(measureOf(CYCLE.runs.banks, 1, 'pac_to_candidates').projected_cycle_real_2025)), 'company PACs: ' + text);
    assert.equal(await page.$eval('#gf-alert', el => el.hidden), true);
    // D-116: under Company PACs the growth captions say growth does not affect them and the range is named for what it is; General giving keeps its own
    assert.deepEqual(await page.$$eval('#gf-horizons > .gf-row:nth-child(-n+2) .gf-source-text > span', all => all.map(el => el.textContent)), ['Growth does not affect company PACs (panel nowcast)', 'Growth does not affect company PACs (panel nowcast)']);
    assert.deepEqual(await page.$$eval('#gf-horizons > .gf-row:nth-child(-n+2) .gf-caption-link', all => all.map(el => el.hidden)), [true, true]);
    assert.deepEqual(await page.$$eval('#gf-legend li', all => all.map(li => li.textContent.trim())), ['Observed', 'Expected', 'Range of prior cycles']);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-current .gf-whisker .gf-hit'), /Range of prior cycles[^]*not a 95% interval/);
    await page.click('#gf-account-general');
    assert.deepEqual(await page.$$eval('#gf-legend li', all => all.map(li => li.textContent.trim())), ['Observed', 'Expected', '95% range']);
    // the rail: label, caption (edition, window, cycle it drives), slider = default -10..+10, no fitted-range wording, the example is gone
    const rows = await page.$$eval('#gf-horizons > .gf-row:nth-child(-n+2)', all => all.map(row => ({label: row.querySelector('.gf-row-label').textContent, caption: row.querySelector('.gf-source-text').textContent, min: row.querySelector('.gf-slider').getAttribute('aria-valuemin'), max: row.querySelector('.gf-slider').getAttribute('aria-valuemax')})));
    assert.deepEqual(rows.map(r => r.label), ['Industry revenue growth, avg 2023–25', 'Industry revenue growth, avg 2025–27']);
    assert.deepEqual(rows.map(r => r.caption), [IBIS_TEXT + ' Reset', IBIS_TEXT_2028 + ' Reset']);
    assert.deepEqual(rows.map(r => [Number(r.min), Number(r.max)].map(v => Math.round(v * 1000) / 1000)), [[1.778, 21.778], [-11.848, 8.152]]);
    assert.equal(await page.$eval('#gf-example', el => el.hidden), true, 'no historical example: the anchor cycle\'s growth is not an input of the cycle model');
    assert.equal(await page.$eval('#gf-horizons', el => /fitted range/i.test(el.textContent)), false);
    // the Methods fold: the cycle model's notes, each measure's open-cycle layer, and the blank prior-cycle growth said truthfully
    await openMethods(page);
    const methods = await page.textContent('#gf-methods');
    assert.match(methods, /Cycle model: how the projection is built and what it leaves out/);
    assert.match(methods, /Company PACs: panel nowcast \(R8\)\./);
    assert.match(methods, /General giving: open-cycle layer P1, the model conditioned on the giving already observed/);
    assert.match(methods, /Prior cycle: blank \(auto\) uses the bundle's IBIS cycle growth \(-1\.55%\) for 2025–2026; it feeds the party split\./);
    assert.doesNotMatch(methods, /uses the current-cycle growth assumption above/);
    assert.match(await page.textContent('#gf-notes'), /Industry revenue growth: the average annual growth of this industry's real revenue/);
    // credit unions and property and casualty: their own defaults, answers drawn, the P&C 2027-28 default inside its range (no "Outside the fitted range")
    for (const [industry, g26, g28, caption, general26] of [['credit_unions', '13.362', '1.665', 'IBIS 2026-05 · avg 2023–2025 · drives 2026 (one-year lag)', CYCLE.runs.credit_unions], ['property_casualty', '3.657', '1.832', 'IBIS 2024-11 · avg 2023–2025 · drives 2026 (one-year lag)', CYCLE.runs.property_casualty]]) {
      const before = own(page).length;
      await page.selectOption('#gf-industry', industry); await recalculated(page, before);
      const sent = lastOwn(page);
      assert.deepEqual([sent.horizons['2026'].growth_pct, sent.horizons['2028'].growth_pct, sent.growth_basis], [g26, g28, 'ibis_window_avg'], industry);
      assert.equal(await page.$eval('#gf-alert', el => el.hidden), true, industry + ': no alert');
      assert.equal(await page.$eval('#gf-horizons .gf-row:first-child .gf-source-text > span', el => el.textContent), caption, industry);
      assert.equal(await page.$eval('#gf-2028-growth', el => el.value), g28);
      assert.equal(await page.$eval('#gf-horizons .gf-row:nth-child(2) .gf-slider', el => el.classList.contains('is-outside')), false, industry + ': 2027-28 default is inside its range');
      assert.doesNotMatch(await page.$eval('#gf-horizons', el => el.textContent), /fitted range/i, industry);
      const total = measureOf(general26, 0, 'general_giving').cycle_given_observed_median;
      const fmt = v => v >= 1e6 ? M1(v) : '$' + Math.round(v / 1e3) + 'K', drawn = await chartText(page);
      assert.ok(drawn.includes(fmt(total)), industry + ': the chart draws the run of this industry, ' + fmt(total) + ' in ' + drawn);
    }
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('credit-union company PACs (D-117): the page names the 16-PAC panel cohort and its own observed-to date, General giving keeps May 31, banks stay "19 bank companies", and the party split\'s Company PACs row names the same cohort (no single-PAC note)', async () => {
  const PANEL = 'Credit unions: 16 PACs (published panel cohort)', OLD_NOTE = 'Credit-union company-PAC party split still reflects the earlier single-PAC roster.';
  const page = await open({path: 'cycle'});
  try {
    const cuPac = measureOf(CYCLE.runs.credit_unions, 0, 'pac_to_candidates'), cuGeneral = measureOf(CYCLE.runs.credit_unions, 0, 'general_giving');
    let before = own(page).length;
    await page.selectOption('#gf-industry', 'credit_unions'); await recalculated(page, before);
    // General giving: its own date, no population line
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    assert.ok((await chartText(page)).includes('observed to May 31'), await chartText(page));
    // Company PACs: the cohort's total, the population and the panel's own date
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars · ' + PANEL + ', observed to 2026-09-26');
    const text = await chartText(page);
    assert.ok(text.includes(M1(cuPac.cycle_given_observed_median)) && text.includes(M1(cuPac.anchor_cycle_real_2025)) && text.includes('observed to Sep 26') && !text.includes('May 31'), text);
    assert.equal(M1(cuPac.cycle_given_observed_median), '$2.5M'); assert.equal(M1(cuGeneral.cycle_given_observed_median), '$0.4M');          // the two accounts stay in their own figures
    const anchor = await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor');
    assert.match(anchor, /PopulationCredit unions: 16 PACs \(published panel cohort\)/); assert.match(anchor, /published PAC panel's cohort/);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-current .gf-col-observed'), /Observed through2026-09-26/);
    // no cut-back chip on the panel's account (it is general giving's), and the date is in the Methods fold, with the population (and no single-PAC split note any more)
    await openMethods(page);
    const warnings = await page.$$eval('#gf-warnings-list li', all => all.map(li => li.textContent));
    assert.ok(!warnings.some(line => /Coverage cut back/.test(line) && /Company PACs/.test(line)), warnings.join(' | '));
    const notes = await page.textContent('#gf-anchors-note');
    assert.ok(notes.includes('Company PACs: ' + PANEL + '; observed to 2026-09-26 (the published PAC panel\'s own date).'), notes);
    assert.ok(!notes.includes(OLD_NOTE) && !notes.includes('single-PAC')); assert.ok(notes.includes('observed through 2026-05-31'));
    const results = await page.textContent('#gf-results');
    assert.ok(results.includes('The 16 PACs of the published panel cohort: own giving to House and Senate candidates') && results.includes('Population: ' + PANEL + '.') && !results.includes(OLD_NOTE), results);
    // back on General giving the chrome is the old one; banks name their 19
    await page.click('#gf-account-general'); assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    before = own(page).length;
    await page.selectOption('#gf-industry', 'commercial_banks'); await recalculated(page, before);
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars · Banks: 19 bank companies, observed to 2026-06-30');
    const bankNotes = await page.textContent('#gf-anchors-note');
    assert.ok(bankNotes.includes('Company PACs: Banks: 19 bank companies; observed to 2026-06-30.') && !bankNotes.includes('single-PAC'), bankNotes);
    assert.ok(!(await page.textContent('#gf-results')).includes('Population: '), 'banks name no population on the split row');
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('Telecom (owner ruling 2026-10-08): it is offered with the wireless + cable IBIS defaults, General giving and Company PACs draw (the 8-PAC panel cohort named, its own observed-to date), Lobbying draws, and the party split is scored on the pooled share models', async () => {
  const PANEL = 'Telecom: 8 PACs (published panel cohort)';
  const page = await open({path: 'cycle'});
  try {
    const pac = measureOf(CYCLE.runs.telecom, 0, 'pac_to_candidates'), general = measureOf(CYCLE.runs.telecom, 0, 'general_giving');
    assert.ok((await page.$$eval('#gf-industry option', all => all.map(o => o.value))).includes('telecom'), 'the industry control offers Telecom');
    let before = own(page).length;
    await page.selectOption('#gf-industry', 'telecom'); await recalculated(page, before);
    const sent = lastOwn(page);
    assert.deepEqual([sent.industry, sent.horizons['2026'].growth_pct, sent.horizons['2028'].growth_pct, sent.growth_basis], ['telecom', '2.049', '4.241', 'ibis_window_avg']);
    assert.equal(await page.$eval('#gf-horizons .gf-row:first-child .gf-source-text > span', el => el.textContent), 'IBIS 2024-10 (51721) + 2024-12 (51711a) · avg 2023–2025 · drives 2026 (one-year lag)');
    assert.equal(await page.$eval('#gf-alert', el => el.hidden), true, 'no alert');
    // General giving: drawn, observed to June 30, no population line
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    let text = await chartText(page);
    assert.ok(text.includes(M1(general.cycle_given_observed_median)) && text.includes('observed to Jun 30'), text);
    // Company PACs: the panel cohort, its own date
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars · ' + PANEL + ', observed to 2026-09-26');
    text = await chartText(page);
    assert.ok(text.includes(M1(pac.cycle_given_observed_median)) && text.includes(M1(pac.anchor_cycle_real_2025)) && text.includes('observed to Sep 26') && !text.includes('Jun 30'), text);
    assert.equal(M1(pac.cycle_given_observed_median), '$5.7M'); assert.equal(M1(general.cycle_given_observed_median), '$10.1M');          // the two accounts stay in their own figures
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor'), /PopulationTelecom: 8 PACs \(published panel cohort\)/);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-current .gf-col-observed'), /Observed through2026-09-26/);
    // Lobbying draws its own chart
    await page.click('#gf-account-lobby'); await page.waitForTimeout(500);
    const lobby = await chartText(page);
    assert.ok(lobby.length > 20 && !/NaN|undefined/.test(lobby), lobby);
    // the party split (owner ruling 2026-10-09, option A): scored on the pooled share models, so each chamber draws a bar and a tick for each basis, and the caption is the tick's, not a reason
    const shares = {house: ['40.8%', '48.5%'], senate: ['32.3%', '70.9%']};
    for (const chamber of ['house', 'senate']) {
      assert.equal(await page.$$eval('#gf-' + chamber + '-body .gf-bar-dash', all => all.length), 0, chamber + ': no dash');
      assert.deepEqual(await page.$$eval('#gf-' + chamber + '-body .gf-pr em', all => all.map(el => el.textContent)), shares[chamber], chamber + ': the drawn Republican shares');
      assert.equal(await page.$$eval('#gf-' + chamber + '-body .gf-ptick', all => all.length), 2, chamber + ': a tick on each basis');
    }
    assert.equal(await page.textContent('#gf-party-cap'), 'Tick = 2023–24');
    assert.doesNotMatch(await page.textContent('#gf-results'), /general-giving model only/);
    await openMethods(page);
    assert.ok((await page.textContent('#gf-warnings')).includes('Scored on the pooled share model.'), 'the Methods fold says the pooled model was used');
    const notes = await page.textContent('#gf-anchors-note');
    assert.ok(notes.includes('Company PACs: ' + PANEL + '; observed to 2026-09-26'), notes);
    assert.ok((await page.textContent('#gf-results')).includes('The 8 PACs of the published panel cohort: own giving to House and Senate candidates'));
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('presidential-cycle factors (D-118, D-119, D-121): General giving ×1.86 on both tooltips, Company PACs +9% on the 2027-28 tooltip only, one Methods line each (property and casualty has no company PACs)', async () => {
  const PAC_WORDS = '+9% (owner rule; national PAC data show 0.98)', PAC_LINE = 'Presidential cycle uplift for company PACs: ' + PAC_WORDS;
  const GEN_WORDS = '×1.86 (owner rule; OpenSecrets total election cost)', GEN_LINE = 'Presidential cycle factor for general giving: ' + GEN_WORDS;
  const count = (text, line) => text.split(line).length - 1;
  const page = await open({path: 'cycle'});
  try {
    // General giving (the default account): the factor on the 2025-26 whisker (the midterm step divides by it) and on the 2027-28 column
    assert.ok((await hoverTip(page, '#gf-chart .gf-mark-current .gf-whisker .gf-hit')).includes('Presidential cycle factor' + GEN_WORDS + '; this midterm step divides by it'));
    const next = await hoverTip(page, '#gf-chart .gf-mark-next .gf-col-projection');
    assert.ok(next.includes('Presidential cycle factor' + GEN_WORDS) && !next.includes('divides by it') && !next.includes('Presidential cycle uplift'), next);
    // Company PACs: the +9% on the step into 2027-28 only
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    const pacNext = await hoverTip(page, '#gf-chart .gf-mark-next .gf-col-projection');
    assert.ok(pacNext.includes('Presidential cycle uplift' + PAC_WORDS) && !pacNext.includes('Presidential cycle factor'), pacNext);
    assert.ok((await chartText(page)).includes(M1(measureOf(CYCLE.runs.banks, 1, 'pac_to_candidates').projected_cycle_real_2025)));
    assert.doesNotMatch(await hoverTip(page, '#gf-chart .gf-mark-current .gf-whisker .gf-hit'), /Presidential cycle/);
    await openMethods(page);
    let notes = await page.textContent('#gf-anchors-note');
    assert.equal(count(notes, PAC_LINE), 1, 'one company-PAC line in the Methods fold'); assert.equal(count(notes, GEN_LINE), 1, 'one general-giving line');
    let before = own(page).length;
    await page.selectOption('#gf-industry', 'credit_unions'); await recalculated(page, before);
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    assert.ok((await hoverTip(page, '#gf-chart .gf-mark-next .gf-col-projection')).includes('Presidential cycle uplift' + PAC_WORDS));
    notes = await page.textContent('#gf-anchors-note');
    assert.equal(count(notes, PAC_LINE), 1); assert.equal(count(notes, GEN_LINE), 1);
    before = own(page).length;
    await page.selectOption('#gf-industry', 'property_casualty'); await recalculated(page, before);
    notes = await page.textContent('#gf-anchors-note');
    assert.equal(count(notes, GEN_LINE), 1); assert.ok(!notes.includes('Presidential cycle uplift'), 'no company PACs, no company-PAC line');
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('a console on the earlier module (catalog without cycle_model_view): growth opens blank on the fitted range, the example is offered, nothing carries growth_basis, and its answers render', async () => {
  const page = await open({path: 'legacy', cycle: false});
  try {
    assert.equal(own(page).length, 0, 'nothing is asked until both growth inputs hold a number');
    assert.deepEqual(await page.$$eval('#gf-2026-growth-view, #gf-2028-growth-view', all => all.map(el => el.value)), ['', '']);
    assert.deepEqual(await page.$$eval('#gf-horizons > .gf-row:nth-child(-n+2) .gf-row-label', all => all.map(el => el.textContent)), ['Revenue growth, 2025–26', 'Revenue growth, 2027–28']);
    assert.equal(await page.$eval('#gf-example', el => el.hidden), false, 'the historical example is offered');
    assert.ok(page.requests.every(r => !('growth_basis' in r)), 'the reference run carries no growth basis either');
    const before = own(page).length;
    await page.fill('#gf-2026-growth-view', '5'); await page.fill('#gf-2028-growth-view', '6');
    await recalculated(page, before);
    const sent = lastOwn(page);
    assert.deepEqual([sent.horizons['2026'].growth_pct, sent.horizons['2028'].growth_pct], ['5', '6']); assert.equal('growth_basis' in sent, false);
    assert.equal(await page.$eval('#gf-alert', el => el.hidden), true, 'its answer renders');
    assert.match(await page.textContent('#gf-status'), /^Both horizons calculated/);
    await openMethods(page);
    assert.doesNotMatch(await page.textContent('#gf-methods'), /open-cycle layer/);
    assert.match(await page.textContent('#gf-notes'), /Revenue growth: 100 ×/);
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('reduced motion: nothing animates; with motion on, the columns grow from the baseline and the party bars sweep', async () => {
  const still = await open({path: 'p1', reducedMotion: 'reduce'});
  try {
    const names = await still.$$eval('#gf-chart .gf-grow, #gf-house-body .gf-pd, #gf-chart .gf-fade', all => all.map(el => getComputedStyle(el).animationName));
    assert.ok(names.length > 5);
    assert.deepEqual([...new Set(names)], ['none'], 'prefers-reduced-motion disables every animation');
    assert.equal(await still.$eval('.gf-col-observed', el => getComputedStyle(el).opacity), '1', 'everything is visible at rest');
  } finally { await closePage(still); }
  const moving = await open({path: 'p1'});
  try {
    await moving.click('#gf-account-pac');
    assert.equal(await moving.$eval('#gf-chart .gf-grow', el => getComputedStyle(el).animationName), 'gf-grow');
    assert.equal(await moving.$eval('#gf-house-body .gf-pd', el => getComputedStyle(el).animationName), 'gf-sweep');
    await moving.waitForTimeout(1500);
    assert.equal(await moving.$eval('.gf-col-observed', el => getComputedStyle(el).opacity), '1', 'everything is visible once the entrance is over');
  } finally { await closePage(moving); }
});

test('every horizon keeps its own inputs on one screen, a pill sets the control before the election, and the request always asks for both horizons', async () => {
  const page = await open({path: 'legacy'});
  try {
    await page.fill('#gf-2026-growth-view', '8'); await page.fill('#gf-2028-growth-view', '-3');
    assert.equal(await page.isVisible('#gf-2026-growth-view'), true); assert.equal(await page.isVisible('#gf-2028-growth-view'), true, 'both horizons are visible at once');
    await page.click('#gf-more > summary');
    const before = own(page).length;
    await page.selectOption('#gf-2028-House-majority', '0');
    assert.equal(await page.$eval('#gf-2028-House-majority', el => el.dataset.value), '0');
    await recalculated(page, before);
    assert.equal(await page.inputValue('#gf-2026-growth-view'), '8', 'both horizons keep their values');
    const body = lastOwn(page);
    assert.deepEqual(Object.keys(body.horizons).sort(), ['2026', '2028'], 'both horizons');
    assert.equal(body.horizons['2026'].growth_pct, '8'); assert.equal(body.horizons['2028'].growth_pct, '-3');
    assert.equal(body.horizons['2028'].current_control.House, '0'); assert.equal(body.horizons['2028'].current_control.Senate, '');
    assert.equal(body.industry, 'commercial_banks');
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('the published PAC table and the partisan panel render whatever the catalog does: with the catalog answering 500 both are in the Methods fold, visible; the scenario parts wait', async () => {
  const pac = {schema_version: 1, currency: 'USD', accounting_basis: 'nominal_pac_to_house_senate_net', sectors: [
    {sector: 'commercial_banks', label: 'Commercial banks', cohort: {n_ids: 12}, current: {status: 'ready', target: 2026, product: 'NOWCAST', issue: '2026-09-30', information_as_of: '2026-09-30', observed_cents: 123400000, remaining_cents: 50000000, prediction_cents: 173400000}, forecasts: []}]};
  const page = await open({path: 'legacy', catalogFailure: 500, pac});
  try {
    assert.match(await page.textContent('#gf-load-status'), /The reviewed models could not be loaded\./);
    assert.equal(await page.$eval('#gf-form', el => el.hidden), true, 'the scenario form stays hidden');
    assert.equal(await page.$eval('.gf-foot', el => el.closest('#gf-form') === null && getComputedStyle(el).display !== 'none'), true, 'the fold line is outside it and shown');
    assert.equal(await page.isVisible('#gf-methods > summary'), true);
    await openMethods(page);
    await page.waitForFunction(() => document.querySelector('#pac-selected-forecast table'));
    assert.equal(await page.isVisible('#pac-selected-forecast'), true, 'the published PAC table is visible inside the fold');
    assert.equal(await page.$eval('#pac-selected-forecast', el => el.closest('#gf-methods') !== null && el.parentElement.id === 'gf-methods-panels'), true);
    const table = await page.textContent('#pac-selected-forecast table');
    assert.ok(table.includes('Commercial banks') && table.includes('$1,234,000') && table.includes('$500,000') && table.includes('$1,734,000'), table);
    assert.match(await page.textContent('#pac-selected-title'), /^Published PAC-to-candidate forecast for the industry-category rosters, 2026 cycle$/);
    assert.equal(await page.isVisible('#pac-political-forecast'), true, 'and so is the partisan panel');
    assert.equal(await page.isVisible('#pac-political-forecast h2'), true);
    assert.equal(await page.isVisible('#gf-notes'), false, 'the scenario\'s Sources and notes wait for the catalog');
    assert.equal(await page.isVisible('#gf-reference'), false, '... and so does "Use historical example"');
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

// The lobbying card as Melange draws it (fec/surfaces/approved/clean/industry-ui.js renderFocus, 2026-10-09; D-064: in-house beside the fees outside firms report, never added): the same figures, colours and bars for the
// three industries that have a Melange sector, and the refusal for the one that has none. MENTAT_LOBBY_SHOTS=<folder> also saves the card at 1440 px (lobby_telecom.png, lobby_banks.png, lobby_credit_unions.png).
test('the Lobbying account draws Melange\'s card, in-house beside outside-firm fees, for Telecom, banks and credit unions (figures, colours, bars; screenshots on request) and refuses property and casualty with its reason', async () => {
  const cases = [['telecom', 'lobby_telecom', 'Telecom', '2025: 24 organizations · 532 named lobbyists', [['$102.4M', '2025 in-house lobbying'], ['$37.0M', '2025 fees reported by outside firms']], '2026 through Q2: in-house $52.1M · outside-firm fees $17.6M', 'rgb(161, 46, 57)', ['$0', '$25M', '$50M', '$75M', '$100M', '$125M', '$150M']],
    ['commercial_banks', 'lobby_banks', 'Bank', '2025: 100 organizations · 495 named lobbyists', [['$65.7M', '2025 in-house lobbying'], ['$23.8M', '2025 fees reported by outside firms']], '2026 through Q2: in-house $39.0M · outside-firm fees $13.4M', 'rgb(118, 83, 153)', null],
    ['credit_unions', 'lobby_credit_unions', 'Credit Union', '2025: 25 organizations · 83 named lobbyists', [['$6.2M', '2025 in-house lobbying'], ['$2.1M', '2025 fees reported by outside firms']], '2026 through Q2: in-house $3.4M · outside-firm fees $1.3M', 'rgb(88, 166, 255)', null]];
  const page = await open({path: 'cycle', width: 1440, height: 1000, reducedMotion: 'reduce'});          // no entrance animation: the screenshots show the settled card
  try {
    for (const [industry, shot, title, sub, figs, partial, colour, ticks] of cases) {
      const before = own(page).length;
      await page.selectOption('#gf-industry', industry); await recalculated(page, before);
      await page.click('#gf-account-lobby'); await page.waitForTimeout(500);
      const got = await page.evaluate(() => {
        const text = el => (el ? el.textContent : '');
        const bars = [...document.querySelectorAll('#gf-lobby-chart .gf-ld-bar')].map(g => [g.dataset.kind, text(g.querySelector('text.gf-ld-xt')), g.querySelectorAll('rect.gf-ld-seg').length, g.querySelectorAll('.gf-ld-whisker').length, [...g.querySelectorAll('rect.gf-ld-seg')].map(r => +r.getAttribute('y') + +r.getAttribute('height'))]);
        const inhouse = [...document.querySelectorAll('#gf-lobby-chart .gf-ld-bar[data-kind=annual] rect.gf-ld-seg-i')].map(r => getComputedStyle(r).fill);
        return {title: text(document.querySelector('.gf-ld-title')), sub: text(document.querySelector('.gf-ld-sub')), partial: text(document.querySelector('.gf-ld-partial')), figs: [...document.querySelectorAll('.gf-ld-fig')].map(f => [text(f.querySelector('.gf-ld-big')), text(f.querySelector('.gf-ld-figlabel'))]),
          all: text(document.querySelector('#gf-lobby-chart')), bars, inhouse, dot: getComputedStyle(document.querySelector('.gf-ld-dot')).backgroundColor, ticks: [...document.querySelectorAll('#gf-lobby-chart .gf-ld-yt')].map(t => t.textContent),
          keys: [...document.querySelectorAll('#gf-legend-lobby li i')].map(i => getComputedStyle(i).backgroundColor), overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      assert.deepEqual([got.title, got.sub, got.figs, got.partial], [title, sub, figs, partial], industry);
      assert.doesNotMatch(got.all, /total lobbying spend|hired firms/i, industry + ': no summed total anywhere on the card');
      assert.deepEqual(got.bars.map(b => b[0]), ['annual', 'annual', 'annual', 'annual', 'annual', 'partial', 'projected', 'projected', 'projected'], industry);
      assert.deepEqual(got.bars.map(b => b[1]), ['2021', '2022', '2023', '2024', '2025', '2026 partial', '2026 projected', '2027 projected', '2028 projected'], industry);
      assert.ok(got.bars.every(b => b[2] === 2) && got.bars.slice(0, 6).every(b => b[3] === 0) && got.bars.slice(6).every(b => b[3] === 2), industry + ': two bars each, a whisker per series on the projected bars only');
      assert.ok(got.bars.every(b => Math.abs(b[4][0] - b[4][1]) < 0.02), industry + ': the two bars of a year stand on one baseline (side by side, not stacked)');
      assert.ok(got.inhouse.length === 5 && got.inhouse.every(fill => fill === colour) && got.dot === colour, industry + ": in-house and the dot are Melange's colour " + colour + ': ' + got.inhouse + ' ' + got.dot);
      assert.equal(got.keys[0] === got.keys[1], false, 'the outside-firm fees are the tint, in-house the colour'); assert.equal(got.keys[0], colour);
      if (ticks) assert.deepEqual(got.ticks, ticks, industry + ': the axis reaches the tallest single whisker top, never a sum (nice(): $25M steps)');
      assert.equal(got.overflow, 0, 'no horizontal scroll');
      if (process.env.MENTAT_LOBBY_SHOTS) { fs.mkdirSync(process.env.MENTAT_LOBBY_SHOTS, {recursive: true}); await page.locator('.gf-chart-col').screenshot({path: path.join(process.env.MENTAT_LOBBY_SHOTS, shot + '.png')}); }
    }
    // property and casualty has no Melange LDA sector: today's refusal text, no card
    const before = own(page).length;
    await page.selectOption('#gf-industry', 'property_casualty'); await recalculated(page, before);
    assert.match(await page.textContent('#gf-lobby-chart'), /Not shown: No Melange-LDA sector for this industry/);
    assert.equal(await page.$$eval('#gf-lobby-chart .gf-ld', all => all.length), 0);
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('a response the cycle view cannot read says so in the card and never blanks the page; Sources opens the notes', async () => {
  const page = await open({path: 'p1', malformed: true});
  try {
    await page.waitForFunction(() => !document.getElementById('gf-alert').hidden);
    const alert = await page.textContent('#gf-alert');
    assert.match(alert, /The cycle view could not be drawn\./); assert.match(alert, /The full numbers are under Sources and notes\./);
    assert.equal(await page.$eval('#gf-form', el => el.hidden), false, 'the form is still there');
    await page.click('#gf-sources-link');
    assert.equal(await page.$eval('#gf-methods', el => el.open), true);
    assert.equal(await page.isVisible('#gf-notes'), true);
    assert.ok(page.errors.every(text => /could not be drawn/.test(text)), 'only the logged, caught error: ' + page.errors.join(' | '));
  } finally { await closePage(page); }
});

test('Export downloads the calculated scenario and the model evidence', async () => {
  const page = await open({path: 'p1'});
  try {
    const [download] = await Promise.all([page.waitForEvent('download'), page.click('#gf-export')]);
    assert.match(download.suggestedFilename(), /^Mentat-commercial_banks-\d{4}-\d{2}-\d{2}-scenario\.json$/);
    const saved = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
    assert.equal(saved.industry, 'commercial_banks'); assert.ok(saved.model_evidence && saved.model_evidence.model_version); assert.ok(saved.exported_at);
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

// ---- nominal dollars by default (D-092, Paul 2026-10-09) ---------------------------------------------------------------------------------------------------------------------------
// Every other test of this file opens the page with the 2025-dollar choice stored (open() default), because they pin 2025-dollar figures; these open it as a viewer with no stored choice sees it.
const F_2026 = 1.012863218, F_2028 = 1.0656483025330452;          // 2.5726436% a year at the cycle mid-point against 2025 (D-092)
const STORE = 'mentat.givingForecast.dollars';
const stored = page => page.evaluate(key => { try { return localStorage.getItem(key); } catch (error) { return 'blocked'; } }, STORE);

test('nominal dollars are the default: the caption, the control and the figures; the toggle switches to 2025 dollars and the choice persists across a reload', async () => {
  const page = await open({path: 'cycle', dollars: 'default'});
  try {
    const g = measureOf(CYCLE.runs.banks, 0, 'general_giving'), g28 = measureOf(CYCLE.runs.banks, 1, 'general_giving'), pac = measureOf(CYCLE.runs.banks, 0, 'pac_to_candidates'), pac28 = measureOf(CYCLE.runs.banks, 1, 'pac_to_candidates');
    assert.equal(await stored(page), null, 'nothing stored yet');
    assert.equal(await page.textContent('#gf-basis'), 'Nominal dollars');
    assert.equal(await page.$eval('#gf-dollars', el => el.hidden), false);
    assert.deepEqual(await page.$$eval('#gf-dollars > button', all => all.map(el => [el.textContent, el.getAttribute('aria-pressed')])), [['Nominal', 'true'], ['2025 dollars', 'false']]);
    assert.equal(await page.$eval('#gf-dollars', el => el.classList.contains('gf-seg')), true, 'the existing segmented control, not a pill');
    // General giving: the 2023-24 bar is the anchors' own nominal level, the open cycle and 2027-28 are nominal = 2025 dollars x the mid-cycle factor
    let text = await chartText(page);
    assert.ok(text.includes(M1(g.anchor_cycle_nominal)) && text.includes(M1(g.nominal.cycle_given_observed_median)) && text.includes(M1(g28.nominal.projected_cycle)), text);
    assert.equal(M1(g.anchor_cycle_nominal), '$25.9M'); assert.equal(M1(g.nominal.cycle_given_observed_median), '$14.7M'); assert.equal(M1(g28.nominal.projected_cycle), '$22.5M');
    assert.ok(Math.abs(g.nominal.cycle_given_observed_median / (g.cycle_given_observed_median * F_2026) - 1) < 1e-9 && Math.abs(g28.nominal.projected_cycle / (g28.projected_cycle_real_2025 * F_2028) - 1) < 1e-9);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor'), /Nominal \(this chart\)\$25\.92M[^]*In 2025 USD\$26\.98M/);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-next .gf-col-projection'), /Projected full cycle \(nominal\)\$22\.54M/);
    assert.match(await page.$eval('#gf-chart .gf-mark-next', el => el.getAttribute('aria-label')), /in nominal dollars/);
    // Company PACs: nominal as well, the population on the caption line
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    assert.equal(await page.textContent('#gf-basis'), 'Nominal dollars · Banks: 19 bank companies, observed to 2026-06-30');
    text = await chartText(page);
    assert.ok(text.includes(M1(pac.nominal.cycle_given_observed_median)) && text.includes(M1(pac28.nominal.projected_cycle)) && text.includes(M1(pac.anchor_cycle_nominal)), text);
    assert.equal(await page.$eval('#gf-dollars', el => el.hidden), false);
    // the toggle: 2025 dollars, the figures the page showed before, the caption, the pressed state
    await page.click('#gf-dollars-real'); await page.waitForTimeout(500);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars · Banks: 19 bank companies, observed to 2026-06-30');
    assert.deepEqual(await page.$$eval('#gf-dollars > button', all => all.map(el => el.getAttribute('aria-pressed'))), ['false', 'true']);
    text = await chartText(page);
    assert.ok(text.includes(M1(pac.cycle_given_observed_median)) && text.includes(M1(pac28.projected_cycle_real_2025)) && text.includes(M1(pac.anchor_cycle_real_2025)), text);
    await page.click('#gf-account-general'); await page.waitForTimeout(400);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    text = await chartText(page);
    assert.ok(text.includes(M1(g.anchor_cycle_real_2025)) && text.includes(M1(g.cycle_given_observed_median)) && text.includes(M1(g28.projected_cycle_real_2025)), text);
    assert.match(await hoverTip(page, '#gf-chart .gf-mark-anchor .gf-col-anchor'), /In 2025 USD \(this chart\)\$26\.98M[^]*Nominal\$25\.92M/);
    assert.equal(await stored(page), 'real', 'the choice is stored');
    // a reload (a later visit): the page opens in 2025 dollars, and choosing nominal again is stored too
    await page.reload(); await page.waitForSelector('#giving-forecast-open', {state: 'visible'}); await page.click('#giving-forecast-open'); await page.waitForSelector('#gf-form:not([hidden])'); await settled(page);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    assert.deepEqual(await page.$$eval('#gf-dollars > button', all => all.map(el => el.getAttribute('aria-pressed'))), ['false', 'true']);
    assert.ok((await chartText(page)).includes(M1(g.cycle_given_observed_median)));
    await page.click('#gf-dollars-nominal'); await page.waitForTimeout(400);
    assert.equal(await page.textContent('#gf-basis'), 'Nominal dollars'); assert.equal(await stored(page), 'nominal');
    assert.ok((await chartText(page)).includes(M1(g.nominal.cycle_given_observed_median)));
    // Lobbying is nominal already: no control, its own caption
    await page.click('#gf-account-lobby'); await page.waitForTimeout(400);
    assert.equal(await page.$eval('#gf-dollars', el => el.hidden), true);
    assert.match(await page.textContent('#gf-basis'), /^Annual, nominal dollars/);
    await page.click('#gf-account-general'); await page.waitForTimeout(400);
    assert.equal(await page.$eval('#gf-dollars', el => el.hidden), false); assert.equal(await page.textContent('#gf-basis'), 'Nominal dollars');
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('the published PAC table and the party-split percentages are not touched by the dollar basis; the Telecom panel bar is the cohort\'s own nominal total', async () => {
  const page = await open({path: 'cycle', dollars: 'default'});
  try {
    let before = own(page).length;
    await page.selectOption('#gf-industry', 'telecom'); await recalculated(page, before);
    await page.click('#gf-account-pac'); await page.waitForTimeout(500);
    const pac = measureOf(CYCLE.runs.telecom, 0, 'pac_to_candidates');
    assert.equal(await page.textContent('#gf-basis'), 'Nominal dollars · Telecom: 8 PACs (published panel cohort), observed to 2026-09-26');
    const text = await chartText(page);
    assert.ok(text.includes('$7.8M') && text.includes(M1(pac.nominal.cycle_given_observed_median)), text);          // the cohort's 2023-24 total as the panel holds it, and its nowcast, both nominal
    assert.equal(M1(pac.nominal.cycle_given_observed_median), '$5.8M');
    const shares = async () => page.$$eval('#gf-house-body .gf-pr em, #gf-senate-body .gf-pr em', all => all.map(el => el.textContent));
    const nominalShares = await shares();
    await page.click('#gf-dollars-real'); await page.waitForTimeout(400);
    assert.deepEqual(await shares(), nominalShares, 'percentages do not move with the dollar basis');
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('where the browser blocks storage the page still opens in nominal dollars and the toggle works for the page', async () => {
  const page = await open({path: 'cycle', dollars: 'blocked'});
  try {
    assert.equal(await stored(page), 'blocked');
    assert.equal(await page.textContent('#gf-basis'), 'Nominal dollars');
    const g = measureOf(CYCLE.runs.banks, 0, 'general_giving');
    assert.ok((await chartText(page)).includes(M1(g.nominal.cycle_given_observed_median)));
    await page.click('#gf-dollars-real'); await page.waitForTimeout(400);
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    assert.ok((await chartText(page)).includes(M1(g.cycle_given_observed_median)));
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('an answer with no nominal twin (the earlier module) is drawn in 2025 dollars: no control, the caption says so', async () => {
  const page = await open({path: 'legacy', dollars: 'default'});
  try {
    assert.equal(await page.textContent('#gf-basis'), '2025 dollars');
    assert.equal(await page.$eval('#gf-dollars', el => el.hidden), true);
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});

test('at 390 px the dollar control stays inside the card, its buttons are 44 px targets and nothing scrolls sideways', async () => {
  const page = await open({path: 'cycle', width: 390, height: 844, dollars: 'default'});
  try {
    const got = await page.evaluate(() => ({heights: [...document.querySelectorAll('#gf-dollars > button')].map(el => Math.round(el.getBoundingClientRect().height)), right: document.getElementById('gf-dollars').getBoundingClientRect().right,
      width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth}));
    assert.ok(got.heights.length === 2 && got.heights.every(h => h >= 44), String(got.heights));
    assert.ok(got.right <= got.width && got.scroll <= got.width, JSON.stringify(got));
    assert.deepEqual(page.errors, []);
  } finally { await closePage(page); }
});
