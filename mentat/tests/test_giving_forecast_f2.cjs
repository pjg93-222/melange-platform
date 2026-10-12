// The cycle view of Forecast giving (board F2): the models behind the chart, the candidate-mix cards, the lobbying card and the footer line, the
// historical-example message, and the request the scenario panel builds. Real responses of the console's own calculator go through the real
// giving-forecast.js in a vm sandbox against a DOM stub; the file on disk is not touched. The DOM itself is covered by test_giving_forecast_f2_browser.cjs.
//
// Two data paths, both pinned here (the model fix P1 is live, but a console that has not been updated yet still answers without it, so the view is right with and without it):
//   * tests/fixtures/giving_forecast_f2_live.json     the calculator as it was published before the model fix (no P1): intervals, remainders and remaining amounts are the
//                                                     unconditioned ones, a remainder can be negative and a share row can be budget_inconsistent;
//   * tests/fixtures/giving_forecast_f2_live_p1.json  the same requests answered with P1 applied: new fields condition the open cycle on the giving already
//                                                     observed. The legacy fields keep the unconditioned values, so the view has to read the new ones.
//
//   * tests/fixtures/giving_forecast_cycle_live.json the cycle-aware calculator (50/50 model for general giving, the panel nowcast R8 for company PACs, D-116; recorded 2026-10-08): the real catalog (cycle_model_view) and the real answers to the page's
//                                                     default requests. The two older fixtures' catalogs carry that cycle_model_view too, so every session opens as the page does
//                                                     today; session({cycle: false}) takes it away again, which is what a console on the earlier module (the rollback) serves.
//
// What these tests pin (spec F2 section 3c and 6): one dollar basis on the chart, intervals drawn exactly as the model returned them (never clipped),
// no account ever added to another, a blank input staying unknown, every flag the model raises showing up as a chip (looked up by name), and the corrected
// historical-example message.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = path.join(__dirname, '..', 'app', 'giving-forecast.js');
const LEGACY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_f2_live.json'), 'utf8'));
const WITH_P1 = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_f2_live_p1.json'), 'utf8'));
const LANDED = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_f2_live_landed.json'), 'utf8'));
const CYCLE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_cycle_live.json'), 'utf8'));
// Melange's own LDA rows as the console's catalog carries them (catalog.anchors.industries.<id>.lda_history; the real values, reconciled to Melange's sector_spend.json by test_giving_lda_history.py).
// The recorded catalogs above predate the field, so every session adds it, as a console on the current anchor snapshot serves it.
const LDA_HISTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_lda_history.json'), 'utf8')).industries;
const FIXTURE = LEGACY;                 // the catalog (identical for both paths apart from the P1 `waivers` key) and the runs of the calculator without the fix
const RUN = LEGACY.runs, P1 = WITH_P1.runs;
const AMBER = 'warn', NEUTRAL = 'info';
const RANGE_TEXT = 'An input is outside the range this model was fitted on.';
const INTERVAL_TEXT = 'Mentat\'s own last change for this series was far outside this band; treat the band as too narrow.';
const BUDGET_TEXT = 'Budget inconsistent — revise scenario';

function el(tag) {
  const classes = new Set();
  return {tag, textContent: '', className: '', children: [], dataset: {}, style: {}, value: '', hidden: false, scope: '', disabled: false,
    classList: {toggle(name, on) { (on === undefined ? !classes.has(name) : on) ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name)},
    append(...c) { this.children.push(...c); }, replaceChildren(...c) { this.children = c; }, setAttribute(name, value) { (this.attrs = this.attrs || {})[name] = String(value); }, removeAttribute(name) { if (this.attrs) delete this.attrs[name]; }, addEventListener(type, fn) { (this.on = this.on || {})[type] = fn; }, after() {}, before() {}, prepend() {}, remove() {}, dispatchEvent() {}, focus() {}, firstChild: {},
    querySelector: () => null, querySelectorAll: () => [], closest: () => null, getBoundingClientRect: () => ({width: 800, height: 300, left: 0, top: 0})};
}

function load(fetchImpl = () => {}, timers = {setTimeout, clearTimeout}, storage = undefined) {
  const els = {};
  const create = tag => { const e = el(tag); let id = ''; Object.defineProperty(e, 'id', {get: () => id, set: v => { id = v; els[v] = e; }}); return e; };
  const document = {getElementById: id => els[id] || (els[id] = el('div')), createElement: create, createElementNS: create, createTextNode: text => ({textContent: text}), querySelector: () => null, querySelectorAll: () => [], addEventListener() {}};
  let src = fs.readFileSync(SOURCE, 'utf8');
  assert.match(src, /\}\)\(\);\s*$/, 'unexpected file ending');
  src = src.replace(/\}\)\(\);\s*$/, `globalThis.__t={render,renderEvidence,niceScale,axisLabel,chartModel,mixModel,lobbyModel,footerFacts,referenceRequest,getRequest,buildInputs,historicalExample,industry,anchorExample,
    refreshControls,parseNumber,formatNumber,exampleText,exampleParts,exampleTip,conditioning,refreshExample,waiverChips,waiverEntries,conditionedTables,givenOf,allocationOf,hasFlag,generalName,getControls:()=>controls,getPills:()=>pills,clearSaved,resetDefaults,stickyMax,shortPeriod,chartMoney,outlookSource,setAccount,setDollars,getDollars:()=>dollars,paintView,getPainted:()=>painted,paintWarnings,drawMix,drawChart,calculate,anchorNote,paintAccountChrome,
    setCatalog:c=>{catalog=c;},setRuns:(r,b)=>{result=r;refRun=b;}};\n})();\n`);
  const globals = {document, Intl, Number, Math, String, console, URL: {}, Blob: function () {}, setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, fetch: fetchImpl, AbortController, Object, JSON, Array, Date, Map, Set, Event: function Event(type, init) { this.type = type; Object.assign(this, init); }};
  if (storage) globals.localStorage = storage;          // without one the page's localStorage is undeclared, as when the browser blocks it
  const ctx = vm.createContext(globals);
  vm.runInContext(src, ctx, {filename: SOURCE});
  return {t: ctx.__t, els};
}
const clone = value => JSON.parse(JSON.stringify(value));
const plain = clone;    // a value built inside the vm has the sandbox's Object and Array: compare it as plain data
function session({sector = 'commercial_banks', outlook = true, waivers = undefined, source = FIXTURE, cycle = true, history = true, fetchImpl, timers} = {}) {
  const s = load(fetchImpl, timers);
  const catalog = clone(source.catalog);
  if (!cycle) delete catalog.cycle_model_view;      // a console on the earlier module (rollback): schema 2 without the cycle model
  if (!outlook) delete catalog.saved_outlook;
  if (waivers) catalog.waivers = clone(waivers);
  if (history) {
    catalog.anchors = catalog.anchors || {status: 'ready', industries: {}};
    for (const [id, lda] of Object.entries(LDA_HISTORY)) catalog.anchors.industries[id] = Object.assign({status: 'ready'}, catalog.anchors.industries[id], {lda_history: clone(lda)});
  }
  s.els['gf-industry'] = Object.assign(el('select'), {value: sector});
  s.els['gf-date'] = Object.assign(el('input'), {value: source === FIXTURE ? '2026-09-30' : source.catalog.as_of});
  s.t.setCatalog(catalog);
  return s;
}
const measure = (run, horizon, id) => run.horizons[horizon].pac.measures.find(m => m.id === id);
const basis = (run, horizon, chamber, id) => run.horizons[horizon].shares[chamber].bases.find(b => b.id === id);
const texts = lines => lines.map(line => line.head || (line.row ? line.row.join(': ') : line.note)).join('\n');
const row = (lines, label) => (lines.find(line => line.row && line.row[0] === label) || {}).row;
const chipTexts = chips => plain(chips.map(c => c.text));
// The three owner waivers of the House share the registry (mentat/forecast/refit/registry/gate_waivers.json) lists with show_warning true, and the lobbying one with false.
const REGISTRY_WAIVERS = [
  {gate: 'G6', cell: 'lobby / commercial_banks', term: 'lobby', industry: 'commercial_banks', reason: 'owner ruling: keep log refit', owner: 'Paul Goff', date: '2026-09-30', show_warning: false},
  {gate: 'G6', cell: 'control_House / commercial_banks', term: 'control_House', industry: 'commercial_banks', reason: 'Owner explicitly authorized the three House-share LOEO degradations measured by pod-final-20260930: "Waive these three cells and publish".', owner: 'Paul Goff', date: '2026-09-30', show_warning: true, new_MAE: 1.9, published_MAE: 1.6},
  {gate: 'G6', cell: 'control_House / credit_unions', term: 'control_House', industry: 'credit_unions', reason: 'Owner explicitly authorized the three House-share LOEO degradations measured by pod-final-20260930: "Waive these three cells and publish".', owner: 'Paul Goff', date: '2026-09-30', show_warning: true},
  {gate: 'G6', cell: 'control_House / auto_light_duty_manufacturing', term: 'control_House', industry: 'auto_light_duty_manufacturing', reason: 'Owner explicitly authorized the three House-share LOEO degradations measured by pod-final-20260930: "Waive these three cells and publish".', owner: 'Paul Goff', date: '2026-09-30', show_warning: true}];

test('the fixtures are the two calculators\' own answers to the same requests', () => {
  assert.equal(P1.banks.model_version, RUN.banks.model_version);
  assert.deepEqual(Object.keys(P1).sort(), Object.keys(RUN).sort());
  assert.deepEqual(plain(WITH_P1.catalog_waivers), [], 'the recording of the fix alone carries no waivers (they come with the refit registry)');
  // the model fix adds fields; it changes none of the legacy ones
  assert.equal(measure(P1.banks, 0, 'pac_to_candidates').cycle_prediction_low, measure(RUN.banks, 0, 'pac_to_candidates').cycle_prediction_low);
  assert.equal(measure(RUN.banks, 0, 'pac_to_candidates').cycle_given_observed_median, undefined, 'the calculator without the fix has no conditioned fields');
  assert.ok(measure(P1.banks, 0, 'pac_to_candidates').cycle_given_observed_median > 0, 'the one with the fix does');
});

test('the axis is a nice scale with at most eleven intervals, and its labels are compact dollars', () => {
  const {t} = session();
  assert.deepEqual(plain(t.niceScale(52197936.48)), {step: 5e6, top: 55e6, ticks: [0, 5e6, 10e6, 15e6, 20e6, 25e6, 30e6, 35e6, 40e6, 45e6, 50e6, 55e6]});   // the board's $0-$55M in $5M steps
  assert.equal(t.niceScale(11381408.4).step, 2e6);
  assert.equal(t.niceScale(11381408.4).top, 12e6);
  assert.equal(t.niceScale(811090).step, 1e5);
  assert.equal(t.niceScale(0).top, 1);
  for (const max of [1234, 0.9e6, 3.3e6, 27.2e6, 52.2e6, 99.99e6, 123e6]) assert.ok(t.niceScale(max).ticks.length <= 12, 'at most eleven intervals for ' + max);
  assert.deepEqual([0, 2.5e6, 55e6, 5e5, 1.2e6].map(v => t.axisLabel(v)), ['$0', '$2.5M', '$55M', '$500K', '$1.2M']);
});

test('one dollar basis: the Mentat level is the 2025-dollar anchor; the nominal amount lives in the tooltip', () => {
  for (const [name, runs] of [['without the model fix', RUN], ['with the model fix P1', P1]]) {
    const {t} = session();
    const general = measure(runs.banks, 0, 'general_giving');
    const model = t.chartModel(runs.banks, null, 'general');
    const anchor = model.columns[0];
    assert.equal(anchor.value, general.anchor_cycle_real_2025, name);
    assert.notEqual(anchor.value, general.anchor_cycle_nominal, name);
    assert.equal(model.basis, '2025 dollars');
    assert.deepEqual(plain(row(anchor.tip, 'In 2025 USD (this chart)')), ['In 2025 USD (this chart)', '$27.18M']);
    assert.deepEqual(plain(row(anchor.tip, 'Nominal')), ['Nominal', '$26.11M']);
    assert.match(anchor.aria, /\$27\.18M in 2025 dollars, \$26\.11M nominal/);
    // every dollar the chart scales to is a 2025-dollar amount from the response: the anchor, observed at face value, the totals and the intervals
    const cur = model.columns[1], next = model.columns[2];
    assert.equal(model.axisMax, Math.max(anchor.value, cur.observed, cur.total, cur.high, next.total, next.high));
    assert.equal(next.total, measure(runs.banks, 1, 'general_giving').projected_cycle_real_2025);
  }
});

test('without the model fix: the open cycle is observed at face value plus the model\'s own remainder, with the date the remainder starts', () => {
  const {t} = session();
  const general = measure(RUN.banks, 0, 'general_giving');
  const model = t.chartModel(RUN.banks, null, 'general'), cur = model.columns[1];
  assert.equal(cur.state, 'stacked');
  assert.equal(cur.conditioned, false);
  assert.equal(cur.observed, general.observed.amount_real_2025);
  assert.equal(cur.remainder, general.projected_remaining);
  assert.equal(cur.total, general.projected_cycle_real_2025);
  assert.ok(Math.abs(cur.observed + cur.remainder - cur.total) < 1e-6);
  assert.equal(cur.through, '2026-06-30');
  assert.deepEqual(plain(row(cur.observedTip, 'Basis')), ['Basis', 'Face value: no 2026 CPI yet']);
  assert.match(texts(cur.observedTip), /remainder starts the next day, 2026-07-01/);
  assert.match(texts(cur.remainderTip), /Full cycle: \$27\.18M = \$11\.57M observed \+ \$15\.61M remainder/);
  assert.ok(row(cur.remainderTip, 'Remainder prediction interval'));
});

test('without the model fix: an interval is drawn exactly as returned and never clipped; one that starts below observed giving is flagged amber', () => {
  const {t} = session();
  const general = measure(RUN.banks, 0, 'general_giving'), pac = measure(RUN.banks, 0, 'pac_to_candidates');
  const generalModel = t.chartModel(RUN.banks, null, 'general'), pacModel = t.chartModel(RUN.banks, null, 'pac');
  assert.equal(generalModel.columns[1].low, general.cycle_prediction_low);
  assert.equal(generalModel.columns[1].high, general.cycle_prediction_high);
  assert.equal(generalModel.columns[2].low, measure(RUN.banks, 1, 'general_giving').cycle_prediction_low);
  assert.equal(generalModel.columns[2].high, measure(RUN.banks, 1, 'general_giving').cycle_prediction_high);
  assert.deepEqual(plain(generalModel.flags), [], 'general giving: the interval stays above what is observed, so no flag');
  // roster PACs: the returned full-cycle interval starts under the $4.67M already observed
  assert.ok(pac.cycle_prediction_low < pac.observed.amount_real_2025);
  assert.equal(pacModel.columns[1].low, pac.cycle_prediction_low, 'not raised to the observed amount');
  assert.equal(pacModel.columns[1].high, pac.cycle_prediction_high);
  assert.equal(pacModel.columns[1].remainderLow, pac.remaining_prediction_low);
  assert.ok(pacModel.columns[1].remainderLow < 0, 'the negative remainder low end is kept');
  const flag = pacModel.flags.find(f => f.text === 'Prediction interval starts below giving already observed');
  assert.equal(flag.kind, AMBER);
  assert.match(texts(flag.tip), /drawn exactly as returned, not clipped/);
  assert.match(texts(flag.tip), new RegExp(BUDGET_TEXT));
  assert.match(texts(pacModel.columns[1].remainderTip), /reaches below zero; it is shown as the model returned it/);
});

test('model fix P1: the whisker is the conditioned full-cycle interval, the remainder segment the conditioned remainder, and nothing is clipped', () => {
  const {t} = session();
  const pac = measure(P1.banks, 0, 'pac_to_candidates'), general = measure(P1.banks, 0, 'general_giving');
  const pacModel = t.chartModel(P1.banks, null, 'pac'), generalModel = t.chartModel(P1.banks, null, 'general');
  for (const [model, m] of [[pacModel, pac], [generalModel, general]]) {
    const cur = model.columns[1];
    assert.equal(model.conditioned, true);
    assert.equal(cur.conditioned, true);
    assert.equal(cur.state, 'stacked');
    assert.equal(cur.low, m.cycle_given_observed_low, 'the whisker starts at the conditioned low end, exactly');
    assert.equal(cur.high, m.cycle_given_observed_high);
    assert.equal(cur.total, m.cycle_given_observed_median);
    assert.equal(cur.remainder, m.remaining_given_observed_median, 'the remainder segment is the conditioned remainder');
    assert.equal(cur.remainderLow, m.remaining_given_observed_low);
    assert.equal(cur.remainderHigh, m.remaining_given_observed_high);
    assert.ok(Math.abs(cur.observed + cur.remainder - cur.total) < 1e-6, 'observed + remainder is the drawn total');
    assert.ok(cur.low >= cur.observed - 1e-6, 'the conditioned interval cannot start below what is observed');
    assert.ok(cur.remainder > 0 && cur.remainderLow >= 0, 'no negative remainder');
  }
  // the legacy fields of the same response still hold the unconditioned values (negative low end): the view must not draw them
  assert.ok(pac.remaining_prediction_low < 0);
  assert.notEqual(pacModel.columns[1].low, pac.cycle_prediction_low);
  assert.deepEqual(chipTexts(pacModel.flags.filter(f => f.kind === AMBER)), [], 'nothing amber: the drawn figures are consistent with the giving observed');
  assert.match(texts(pacModel.columns[1].intervalTip), /conditioned on the giving already observed/);
  assert.ok(row(pacModel.columns[1].intervalTip, 'Unconditioned model'));
  assert.ok(Math.abs(Number(row(pacModel.columns[1].intervalTip, 'Unconditioned chance below observed')[1].replace('%', '')) - 12.15) < 0.01, 'the 12.1% of the unconditioned mass below observed is said');
  // the next cycle has nothing observed: it is the model's own interval in both paths
  assert.equal(pacModel.columns[2].low, measure(P1.banks, 1, 'pac_to_candidates').cycle_prediction_low);
});

test('with and without P1 the same inputs give the same observed amount, the same anchor and the same next-cycle column', () => {
  const a = session().t.chartModel(RUN.banks, null, 'pac'), b = session().t.chartModel(P1.banks, null, 'pac');
  assert.equal(a.columns[0].value, b.columns[0].value);
  assert.equal(a.columns[1].observed, b.columns[1].observed);
  assert.deepEqual(plain(a.columns[2]).total, plain(b.columns[2]).total);
  assert.equal(a.columns[1].conditioned, false);
  assert.equal(b.columns[1].conditioned, true);
});

test('model fix P1: giving observed above the unconditioned projection is drawn from the conditioned figures and said in a neutral chip', () => {
  const {t} = session();
  const run = clone(P1.banks), m = measure(run, 0, 'pac_to_candidates');
  m.projected_cycle_real_2025 = m.observed.amount_real_2025 * 0.8;                // the synthetic case the flag exists for: projection below observed
  m.flags.push('observed_exceeds_unconditional_projection');
  const model = t.chartModel(run, null, 'pac');
  assert.equal(model.columns[1].state, 'stacked');
  assert.equal(model.columns[1].total, m.cycle_given_observed_median);
  const flag = model.flags.find(f => f.text === 'Observed giving exceeds the unconditioned projection');
  assert.equal(flag.kind, NEUTRAL);
  assert.match(texts(flag.tip), /conditioned on what is observed/);
});

test('zero input change is said, not drawn: the coefficient interval is zero-width by construction', () => {
  const {t} = session();
  const cur = t.chartModel(RUN.banks, null, 'general').columns[1];
  assert.equal(row(cur.intervalTip, 'Coefficient interval')[1], '$27.18M to $27.18M (no input change)');
  const moved = t.chartModel(RUN.banks_growth_down, null, 'general').columns[1];
  const [, text] = row(moved.intervalTip, 'Coefficient interval');
  assert.doesNotMatch(text, /no input change/);
});

test('D4(b): the name of the general-giving account, and the note about the receipts left out, come from the own label of the calculator - nothing claims an exclusion the model does not state', () => {
  const NEW_LABEL = 'Mentat general giving (all channels), excluding receipts Mentat marks non-giving (FEC interest, refunds, insurance claims, loans)';
  const {t} = session();
  // before the change: the old label, no exclusion claimed anywhere
  const old = t.chartModel(RUN.banks, null, 'general');
  assert.equal(old.accountLabel, 'Mentat general giving (all channels)');
  assert.doesNotMatch(texts(old.columns[0].tip), /non-giving/);
  assert.equal(t.generalName(RUN.banks), 'Mentat general giving (all channels)');
  // after it: the new label is the account name, the anchor tooltip says what is left out, and the PAC account has no such note
  for (const runs of [RUN, P1]) {
    const run = clone(runs.banks);
    for (const h of run.horizons) for (const m of h.pac.measures) if (m.id === 'general_giving') m.label = NEW_LABEL;
    const model = t.chartModel(run, null, 'general');
    assert.equal(model.accountLabel, NEW_LABEL);
    assert.match(NEW_LABEL, /^Mentat general giving \(all channels\)/, 'the prefix the existing tests match is kept');
    assert.match(texts(model.columns[0].tip), /Receipts the accounting engine marks non-giving \(interest, refunds, insurance claims, loans\) are left out; the amounts are in Sources and notes\./);
    assert.doesNotMatch(texts(t.chartModel(run, null, 'pac').columns[0].tip), /non-giving/, 'the roster PAC account has no such exclusion');
    assert.equal(t.generalName(run), NEW_LABEL);
  }
  assert.equal(t.generalName(null), 'Mentat general giving');
});

test('PAC giving and general giving are separate accounts: nothing is stacked, added or blended (either data path)', () => {
  for (const runs of [RUN, P1]) {
    const {t} = session();
    const general = t.chartModel(runs.banks, null, 'general'), pac = t.chartModel(runs.banks, null, 'pac');
    assert.notEqual(general.columns[0].value, pac.columns[0].value);
    assert.equal(pac.columns[0].value, measure(runs.banks, 0, 'pac_to_candidates').anchor_cycle_real_2025);
    const sums = [general.columns[0].value + pac.columns[0].value, general.columns[1].total + pac.columns[1].total, general.columns[2].total + pac.columns[2].total];
    for (const model of [general, pac]) {
      const values = [model.columns[0].value, model.columns[1].observed, model.columns[1].total, model.columns[1].high, model.columns[2].total, model.columns[2].high];
      for (const sum of sums) assert.ok(values.every(v => Math.abs(v - sum) > 1), 'a column equals the sum of the two accounts');
    }
    assert.ok(pac.axisMax < general.axisMax / 3, 'each account has its own scale');
    assert.match(pac.accountLabel, /Roster companies' PACs/);
    assert.equal(general.accountLabel, measure(runs.banks, 0, 'general_giving').label, 'the name of the account is the one the model gives it');
  }
});

test('no pooled figure of the two candidate-share bases is ever read or drawn: no model, row, tip or chip mentions one', () => {
  for (const runs of [RUN, P1]) {
    const {t} = session();
    const everything = JSON.stringify([t.chartModel(runs.banks, null, 'pac'), t.chartModel(runs.banks, null, 'general'), t.mixModel(runs.banks, null, 'House'), t.mixModel(runs.banks, null, 'Senate'), t.lobbyModel(runs.banks_lobby, 2026, 2028)]);
    assert.doesNotMatch(everything, /combined|pooled reference/i);
    const house = t.mixModel(runs.banks, null, 'House');
    assert.equal(house.rows.length, 2);
    assert.deepEqual(plain(house.rows.map(r => r.id)), ['employee_candidate', 'pac_candidate']);
  }
});

test('before a calculation the chart holds real data only: the Mentat level and the observed segment', () => {
  const {t} = session();
  const model = t.chartModel(null, RUN.banks, 'general');
  const [anchor, cur, next] = model.columns;
  assert.equal(anchor.value, measure(RUN.banks, 0, 'general_giving').anchor_cycle_real_2025);
  assert.equal(cur.state, 'pending');
  assert.equal(cur.observed, measure(RUN.banks, 0, 'general_giving').observed.amount_real_2025);
  for (const column of [cur, next]) { assert.equal(column.total, undefined); assert.equal(column.low, undefined); assert.equal(column.high, undefined); }
  assert.equal(next.state, 'pending');
  assert.equal(model.axisMax, anchor.value, 'the scale follows the data on the chart, not a projection that is not there');
  assert.match(cur.aria, /observed through 2026-06-30; Set growth to see a projection/);
});

test('a missing input shows a dash with the reason in its tooltip; the observed segment stays', () => {
  const {t} = session();
  const model = t.chartModel(RUN.banks_blank, RUN.banks, 'general');
  const [, cur, next] = model.columns;
  assert.equal(cur.state, 'pending'); assert.equal(next.state, 'pending');
  assert.equal(cur.reason, 'Enter expected two-year cycle revenue growth.');
  assert.match(texts(cur.pendingTip), /Enter expected two-year cycle revenue growth\./);
  assert.equal(cur.observed, measure(RUN.banks, 0, 'general_giving').observed.amount_real_2025);
  assert.match(next.aria, /Enter expected two-year cycle revenue growth\./);
});

test('an account the industry does not have is said, not drawn as a zero', () => {
  const {t} = session({sector: 'property_casualty'});
  const pac = t.chartModel(RUN.property_casualty, null, 'pac');
  assert.equal(pac.empty, true);
  assert.equal(pac.message, 'No roster PAC committees are resolved for this industry');
  assert.deepEqual(plain(pac.flags.map(f => f.text)), ['No roster PAC committees are resolved for this industry']);
  const general = t.chartModel(RUN.property_casualty, null, 'general');
  assert.equal(general.empty, false);
  assert.equal(general.columns[0].value, measure(RUN.property_casualty, 0, 'general_giving').anchor_cycle_real_2025);
});

test('credit unions: the single-PAC thin base is a neutral chip and the June cut-back too; the model\'s own flag text is used', () => {
  for (const runs of [RUN, P1]) {
    const {t} = session({sector: 'credit_unions'});
    const pac = t.chartModel(runs.credit_unions, null, 'pac');
    const thin = pac.flags.find(f => /^One PAC \(SchoolsFirst Federal Credit Union\)/.test(f.text));
    assert.equal(thin.kind, NEUTRAL);
    assert.equal(thin.text, measure(runs.credit_unions, 0, 'pac_to_candidates').flag_text);
    const cut = pac.flags.find(f => f.text === 'Coverage cut back to 2026-05-31');
    assert.equal(cut.kind, NEUTRAL);
    assert.match(texts(cut.tip), /June incomplete/);
    assert.equal(pac.columns[1].through, '2026-05-31');
  }
});

test('flags are looked up by NAME, never by position: the model fix inserts a new first flag on the credit-union PAC', () => {
  const {t} = session({sector: 'credit_unions'});
  const baseline = measure(P1.credit_unions, 0, 'pac_to_candidates').flags;
  assert.deepEqual(plain(baseline), ['interval_not_supported_by_own_history', 'single_pac_thin_base']);
  for (const order of [baseline, [...baseline].reverse(), ['some_future_flag', ...baseline], [...baseline, 'another_future_flag'], ['some_future_flag', ...[...baseline].reverse()]]) {
    const run = clone(P1.credit_unions);
    for (const h of run.horizons) h.pac.measures.find(m => m.id === 'pac_to_candidates').flags = [...order];
    const flags = t.chartModel(run, null, 'pac').flags;
    assert.equal(flags.filter(f => f.kind === AMBER && f.text === INTERVAL_TEXT).length, 1, 'amber chip for ' + order.join(','));
    assert.equal(flags.filter(f => /^One PAC \(/.test(f.text)).length, 1, 'thin-base chip for ' + order.join(','));
  }
  const without = clone(P1.credit_unions);
  for (const h of without.horizons) h.pac.measures.find(m => m.id === 'pac_to_candidates').flags = ['some_future_flag'];
  assert.deepEqual(chipTexts(t.chartModel(without, null, 'pac').flags).filter(text => text === INTERVAL_TEXT || /^One PAC/.test(text)), [], 'a flag that is not there raises no chip');
  const model = t.hasFlag({flags: ['a', 'b']}, 'b');
  assert.equal(model, true);
  assert.equal(t.hasFlag({flags: ['a', 'b']}, 'c'), false);
  assert.equal(t.hasFlag({}, 'c'), false);
});

test('an interval Mentat\'s own history contradicts is an amber chip with the owner\'s wording, for the account it concerns', () => {
  const {t} = session({sector: 'credit_unions'});
  for (const account of ['pac', 'general']) {
    const model = t.chartModel(P1.credit_unions, null, account);
    const flag = model.flags.find(f => f.kind === AMBER);
    assert.equal(flag.text, INTERVAL_TEXT, account);
    assert.match(texts(flag.tip), /standard deviations of the step the band is built on/);
    assert.match(texts(flag.tip), /Own last step, Mentat 2021–2022 to 2023–2024/);
  }
  const banks = session().t;
  assert.deepEqual(chipTexts(banks.chartModel(P1.banks, null, 'pac').flags).filter(text => text === INTERVAL_TEXT), [], 'banks is not flagged');
  assert.deepEqual(chipTexts(banks.chartModel(P1.banks, null, 'general').flags).filter(text => text === INTERVAL_TEXT), []);
  // a calculator without the fix never raises it: nothing to show
  assert.deepEqual(chipTexts(t.chartModel(RUN.credit_unions, null, 'pac').flags).filter(text => text === INTERVAL_TEXT), []);
});

test('the scope caveats of an industry are one neutral chip on the chart (both accounts), listing every caveat in its tooltip', () => {
  const {t} = session({sector: 'property_casualty'});
  const caveats = FIXTURE.catalog.industries.find(i => i.id === 'property_casualty').caveats;
  assert.equal(caveats.length, 2);
  for (const run of [RUN, P1]) {
    const model = t.chartModel(run.property_casualty, null, 'general');
    const chip = model.flags.find(f => f.text === caveats[0]);
    assert.equal(chip.kind, NEUTRAL);
    assert.match(texts(chip.tip), /F09 = all insurance/);
    assert.match(texts(chip.tip), /IBISWorld's current-year estimate/);
  }
  assert.deepEqual(chipTexts(session().t.chartModel(RUN.banks, null, 'general').flags).filter(text => /F09/.test(text)), [], 'banks has none');
});

test('candidate mix, without the model fix: the bar is the projected FULL-cycle Republican share, never the share of what remains', () => {
  const {t} = session();
  const house = t.mixModel(RUN.banks, null, 'House');
  const bases = RUN.banks.horizons[0].shares.House.bases;
  assert.deepEqual(plain(house.rows.map(r => r.label)), ['Employees', 'Company PACs']);
  assert.equal(house.rows[0].share, bases[0].projected_cycle_R_share);
  assert.equal(house.rows[0].conditioned, false);
  assert.notEqual(house.rows[0].share, bases[0].projected_remaining_R_share, 'the remaining share (42.16%) is a different denominator');
  assert.equal(house.rows[1].share, bases[1].projected_cycle_R_share);
  assert.deepEqual(plain(row(house.rows[0].tip, 'Share of the remaining giving')), ['Share of the remaining giving', '42.16% (from 2026-07-01)']);
  assert.equal(house.rows[0].next, RUN.banks.horizons[1].shares.House.bases[0].projected_cycle_R_share, 'the teal tick is the next cycle\'s full-cycle share');
  assert.equal(house.caption, '2023–2024: 34.35% and 54.36%');
  assert.deepEqual(plain(house.rows.map(r => r.id)), ['employee_candidate', 'pac_candidate'], 'two bases, never a pooled third');
});

test('candidate mix, without the model fix: budget_inconsistent outlines the affected bar and raises one amber chip, no clipping', () => {
  const {t} = session();
  const senate = t.mixModel(RUN.banks, null, 'Senate');
  assert.equal(senate.rows[0].flagged, false);
  assert.equal(senate.rows[1].flagged, true);
  assert.equal(senate.rows[1].share, RUN.banks.horizons[0].shares.Senate.bases[1].projected_cycle_R_share, 'the bar still shows the full-cycle share');
  const amber = senate.chips.filter(c => c.kind === AMBER);
  assert.equal(amber.length, 1);
  assert.equal(amber[0].text, BUDGET_TEXT);
  assert.match(texts(amber[0].tip), /budget_inconsistent: The projected total and share are incompatible/);
  assert.match(texts(amber[0].tip), /R -\$135\.67K · D \$32\.16K/);
  assert.equal(senate.chips[0].kind, AMBER, 'amber first');
  assert.equal(senate.conditioned, false);
});

test('candidate mix, model fix P1: the bar is the implied full-cycle share (observed + the projected share on the money still to come); no amber', () => {
  const {t} = session();
  const house = t.mixModel(P1.banks, null, 'House'), senate = t.mixModel(P1.banks, null, 'Senate');
  for (const [model, chamber] of [[house, 'House'], [senate, 'Senate']]) {
    assert.equal(model.conditioned, true);
    model.rows.forEach((r, i) => {
      const b = P1.banks.horizons[0].shares[chamber].bases[i], a = b.remaining_allocation;
      assert.equal(r.conditioned, true);
      assert.equal(r.share, a.implied_cycle_R_share, 'the bar is the P1 implied share');
      assert.notEqual(r.share, b.projected_cycle_R_share);
      assert.equal(r.projectedShare, b.projected_cycle_R_share);
      assert.equal(r.low, a.implied_cycle_R_share_prediction_low);
      assert.equal(r.high, a.implied_cycle_R_share_prediction_high);
      assert.equal(r.flagged, false, 'no amber outline: the remaining amounts are not negative');
      assert.ok(a.remaining_R_real_2025 >= 0 && a.remaining_D_real_2025 >= 0);
      // the share is exactly what the amounts imply
      const seen = b.observed;
      assert.ok(Math.abs((seen.R_real_2025 + a.remaining_R_real_2025) / (seen.R_real_2025 + seen.D_real_2025 + a.remaining_total_real_2025) - r.share) < 1e-9);
    });
    assert.deepEqual(chipTexts(model.chips.filter(c => c.kind === AMBER)), []);
  }
  // the Senate roster PACs were budget_inconsistent before conditioning: neutral chip and a tooltip note instead of the amber state
  assert.equal(P1.banks.horizons[0].shares.Senate.bases[1].remainder_status, 'budget_inconsistent', 'the legacy field still says so');
  assert.deepEqual(chipTexts(senate.chips), [RANGE_TEXT], 'no chip of its own: the bar tooltip and the notes say what was conditioned');
  assert.match(texts(senate.rows[1].tip), /Before conditioning, the model's projected two-party total \(\$886\.99K\) was below the giving already observed/);
  assert.match(texts(senate.rows[1].tip), /Remaining from 2026-07-01 \(median\): R \$150\.68K · D \$62\.88K of \$213\.56K/);
  assert.match(texts(senate.rows[1].tip), /Projected share, applied to the remaining giving: 70\.56%/);
  // the teal tick (next cycle) and the caption are the same in both paths
  assert.equal(house.rows[0].next, RUN.banks.horizons[1].shares.House.bases[0].projected_cycle_R_share);
  assert.equal(house.caption, '2023–2024: 34.35% and 54.36%');
});

test('out-of-range input: a neutral chip with the model\'s own sentence; the model fix names the input, how far and which input moved the share', () => {
  const {t} = session();
  for (const [name, runs] of [['published', RUN], ['P1', P1]]) {
    const house = t.mixModel(runs.banks, null, 'House');
    const chip = house.chips.find(c => c.text === RANGE_TEXT);
    assert.equal(chip.kind, NEUTRAL, name);
    assert.match(texts(chip.tip), new RegExp(RANGE_TEXT.replace(/\./g, '\\.')));
  }
  const p1 = t.mixModel(P1.banks, null, 'House').chips.find(c => c.text === RANGE_TEXT).tip;
  assert.match(texts(p1), /prior-cycle growth: 48\.89% \(fitted 2012–2022: -9\.87% to 14\.1%\)/);
  assert.match(texts(p1), /Model change by input, before conditioning on the giving already observed: .*presidential or midterm cycle -1\.88 pp/);
  assert.match(texts(p1), /Shares the model was fitted on: 42\.85% to 72\.81%/);
  // a calculator without the fix knows only yes or no
  assert.doesNotMatch(texts(t.mixModel(RUN.banks, null, 'House').chips.find(c => c.text === RANGE_TEXT).tip), /fitted 2012/);
});

test('candidate mix: thin bases and out-of-range inputs are neutral; a share outside 0-100% is not drawn and not clipped', () => {
  const {t} = session({sector: 'credit_unions'});
  const house = t.mixModel(RUN.credit_unions, null, 'House');
  assert.deepEqual(plain(house.chips.filter(c => c.kind === NEUTRAL).map(c => c.text).sort()), ['An input is outside the range this model was fitted on.', 'Thin base: Employees and Company PACs'], 'one chip per kind of flag; the tooltip names each base');
  assert.equal(house.chips.filter(c => c.kind === AMBER).length, 1, 'the House employee basis is budget_inconsistent');
  const senate = t.mixModel(RUN.credit_unions_growth_down, null, 'Senate');
  const pacRow = senate.rows[1];
  assert.equal(RUN.credit_unions_growth_down.horizons[1].shares.Senate.bases[1].status, 'outside_share_range');
  assert.equal(pacRow.next, null, 'no tick is drawn for a share the model put outside 0-100%');
  assert.ok(pacRow.share != null, 'the current-cycle bar is still real');
  // with P1 the same House card has no amber state: the budget_inconsistent row is conditioned
  const withFix = session({sector: 'credit_unions'}).t.mixModel(P1.credit_unions, null, 'House');
  assert.equal(withFix.chips.filter(c => c.kind === AMBER).length, 0);
  assert.equal(withFix.rows.filter(r => r.flagged).length, 0);
});

test('candidate mix: a blank scenario draws no bar and says which input is missing; the anchor shares still caption the card', () => {
  const {t} = session();
  const house = t.mixModel(RUN.banks_blank, RUN.banks, 'House');
  assert.deepEqual(plain(house.rows.map(r => r.state)), ['missing', 'missing']);
  assert.equal(house.rows[0].reason, 'Enter House current-control assumption, House control score.');
  assert.match(texts(house.rows[0].tip), /Enter House current-control assumption/);
  assert.equal(house.caption, '2023–2024: 34.35% and 54.36%');
  const before = t.mixModel(null, RUN.banks, 'House');
  assert.deepEqual(plain(before.rows.map(r => r.state)), ['pending', 'pending']);
  assert.equal(before.caption, '2023–2024: 34.35% and 54.36%');
});

test('candidate mix: an industry with one basis captions that basis by name and shows a dash for the other', () => {
  const {t} = session({sector: 'property_casualty'});
  const house = t.mixModel(RUN.property_casualty, null, 'House');
  assert.deepEqual(plain(house.rows.map(r => r.state)), ['ready', 'missing']);
  assert.match(house.rows[1].reason, /No roster PAC committees are resolved/);
  assert.match(house.caption, /^2023–2024: 43\.37% \(employees\)$/);
});

test('owner waivers: a neutral chip on the card of the term the waiver concerns, for the selected industry only', () => {
  const {t} = session({waivers: REGISTRY_WAIVERS});
  for (const [name, runs] of [['published', RUN], ['P1', P1]]) {
    const house = t.mixModel(runs.banks, null, 'House');
    const waiver = house.chips.filter(c => /^Gate waived by owner/.test(c.text));
    assert.deepEqual(plain(waiver.map(c => c.text)), ['Gate waived by owner: House share'], name);
    assert.equal(waiver[0].kind, NEUTRAL);
    assert.match(texts(waiver[0].tip), /Cell: control_House \/ commercial_banks/);
    assert.match(texts(waiver[0].tip), /Gate: G6/);
    assert.match(texts(waiver[0].tip), /Measured: refit 1\.900 · earlier published calculator 1\.600/);
    assert.match(texts(waiver[0].tip), /Owner: Paul Goff, 2026-09-30/);
    assert.match(texts(waiver[0].tip), /Waive these three cells and publish/);
    assert.deepEqual(chipTexts(t.mixModel(runs.banks, null, 'Senate').chips).filter(text => /Gate waived/.test(text)), [], 'the Senate share has no waiver');
  }
  // show_warning false (the lobbying waiver): listed in the notes, no chip on the card
  assert.deepEqual(plain(t.lobbyModel(RUN.banks_lobby, 2026, 2028).chips.map(c => c.text).filter(text => /Gate waived/.test(text))), []);
  assert.equal(t.waiverEntries().length, 4, 'all four stay listed in "Sources and notes"');
  // another industry's waiver is not shown on this one's card
  const credit = session({sector: 'credit_unions', waivers: REGISTRY_WAIVERS}).t.mixModel(RUN.credit_unions, null, 'House');
  assert.equal(credit.chips.filter(c => /^Gate waived by owner/.test(c.text)).length, 1);
  // a waiver the response itself carries counts too, and the chart takes the PAC one
  const run = clone(P1.banks); run.waivers = [{gate: 'G6', cell: 'pac / commercial_banks', term: 'pac', industry: 'commercial_banks', reason: 'test', owner: 'Paul Goff', date: '2026-09-30', show_warning: true}];
  assert.deepEqual(chipTexts(session().t.chartModel(run, null, 'pac').flags).filter(text => /Gate waived/.test(text)), ['Gate waived by owner: PAC giving']);
  // none carried: none shown
  assert.deepEqual(chipTexts(session().t.mixModel(RUN.banks, null, 'House').chips).filter(text => /Gate waived/.test(text)), []);
});

test('owner waivers: the entry shape is read generically - the display text becomes the chip, the chamber finds the card, every figure goes in the tooltip', () => {
  const entry = {industry: 'commercial_banks', chamber: 'House', term: 'control', metric: 'leave-one-election-out error (pp)', published: 1.9, comparator: 1.6, date: '2026-09-30',
    display_text: 'House share: published under an owner waiver (leave-one-election-out error 1.9 pp against 1.6 pp)', show_warning: true};
  const {t} = session({waivers: [entry]});
  const house = t.mixModel(RUN.banks, null, 'House');
  const chip = house.chips.find(c => /owner waiver/.test(c.text));
  assert.equal(chip.text, entry.display_text, 'the display text of the entry is the chip');
  assert.equal(chip.kind, NEUTRAL);
  assert.match(texts(chip.tip), /Metric: leave-one-election-out error \(pp\)/);
  assert.match(texts(chip.tip), /Measured: published 1\.900 · comparator 1\.600/);
  assert.match(texts(chip.tip), /Owner: 2026-09-30/);
  assert.deepEqual(chipTexts(t.mixModel(RUN.banks, null, 'Senate').chips).filter(text => /owner waiver|Gate waived/.test(text)), [], 'a House waiver is not shown on the Senate card');
  // without display text the generic wording names the card
  const bare = session({waivers: [{industry: 'commercial_banks', chamber: 'Senate', term: 'control', date: '2026-09-30'}]}).t;
  assert.deepEqual(chipTexts(bare.mixModel(RUN.banks, null, 'Senate').chips).filter(text => /Gate waived/.test(text)), ['Gate waived by owner: Senate share']);
  // an entry that names no card is listed in the notes and drawn nowhere
  const stray = session({waivers: [{industry: 'commercial_banks', cell: 'something / else', date: '2026-09-30'}]}).t;
  assert.equal(stray.waiverEntries().length, 1);
  for (const chamber of ['House', 'Senate']) assert.deepEqual(chipTexts(stray.mixModel(RUN.banks, null, chamber).chips).filter(text => /waive/i.test(text)), []);
});

/// The entries the live calculator puts in its catalog (recorded: fixtures/giving_forecast_f2_live_landed.json): the House-share cells the owner waived, shown for the industries they name.
const CATALOG_WAIVERS = LANDED.catalog.waivers;
const pp3 = value => value.toFixed(3);

test('owner waivers: the entries the calculator catalog carries - the display text is the chip, the figures and the comparator are in the tooltip, one chip per offered industry', () => {
  assert.ok(CATALOG_WAIVERS.length >= 2, 'the landed catalog carries the owner waivers');
  const landed = sector => session({sector, source: LANDED});
  const entryOf = sector => CATALOG_WAIVERS.find(w => w.industry === sector && w.chamber === 'House');
  const banks = landed('commercial_banks').t.mixModel(LANDED.runs.banks, null, 'House');
  const entry = entryOf('commercial_banks');
  const chip = banks.chips.find(c => c.text === entry.display_text);
  assert.ok(chip, 'the display text of the entry is a chip of the House card');
  assert.equal(chip.kind, NEUTRAL);
  const tip = texts(chip.tip);
  assert.ok(tip.includes('Cell: ' + entry.cell), 'the cell is in the tooltip');
  assert.ok(tip.includes('Metric: ' + entry.metric), 'the metric is in the tooltip');
  assert.ok(tip.includes(`Measured: published ${pp3(entry.published_value)} ${entry.unit === 'percentage points' ? 'pp' : entry.unit} · comparator ${pp3(entry.comparator_value)} ${entry.unit === 'percentage points' ? 'pp' : entry.unit}`), tip);
  assert.ok(tip.includes('Comparator: ' + entry.comparator_label), 'the comparator is named');
  assert.ok(tip.includes(`Owner: ${entry.owner}, ${entry.date}`), 'the owner and the date are in the tooltip');
  assert.deepEqual(chipTexts(landed('commercial_banks').t.mixModel(LANDED.runs.banks, null, 'Senate').chips).filter(text => /waive/i.test(text)), [], 'a House waiver is not shown on the Senate card');
  // every industry the entries name shows its own entry; one the entries do not name shows none
  for (const sector of new Set(CATALOG_WAIVERS.filter(w => w.show_warning === true).map(w => w.industry))) {
    const run = sector === 'credit_unions' ? LANDED.runs.credit_unions : LANDED.runs.banks;
    const shown = chipTexts(landed(sector).t.mixModel(run, null, 'House').chips).filter(text => /waived/i.test(text));
    assert.deepEqual(shown, CATALOG_WAIVERS.filter(w => w.industry === sector && w.chamber === 'House' && w.show_warning === true).map(w => w.display_text), sector);
  }
  assert.deepEqual(chipTexts(landed('property_casualty').t.mixModel(LANDED.runs.property_casualty, null, 'House').chips).filter(text => /waive/i.test(text)), [], 'not named, not shown');
  assert.equal(landed('commercial_banks').t.waiverEntries().length, CATALOG_WAIVERS.length, 'every entry stays listed in "Sources and notes"');
});

test('the live calculator: the general-giving name and its exclusion come from its own label, and nothing pooled or invented is in its responses', () => {
  for (const [name, run] of Object.entries(LANDED.runs)) {
    if (run.status !== 'ready') continue;
    const {t} = session({source: LANDED});
    const general = run.horizons[0].pac.measures.find(m => m.id === 'general_giving');
    if (!general) continue;
    assert.equal(t.generalName(run), general.label, name + ': the account is named by the response label');
    assert.match(general.label, /^Mentat general giving \(all channels\), excluding receipts Mentat marks non-giving/, name + ': the label states what the measure leaves out');
  }
  assert.doesNotMatch(JSON.stringify(LANDED), /combined|pooled_reference/i, 'the landed responses carry no pooled reference');
  const {t} = session({source: LANDED});
  const model = t.chartModel(LANDED.runs.banks, null, 'general');
  assert.equal(model.accountLabel, LANDED.runs.banks.horizons[0].pac.measures.find(m => m.id === 'general_giving').label);
  assert.match(texts(model.columns[0].tip), /Receipts the accounting engine marks non-giving \(interest, refunds, insurance claims, loans\) are left out; the amounts are in Sources and notes\./, 'the Mentat-level tooltip says what the level leaves out');
  assert.doesNotMatch(texts(t.chartModel(LANDED.runs.banks, null, 'pac').columns[0].tip), /non-giving/, 'the roster PAC account has no such exclusion');
});

test('lobbying (Melange card, 2026-10-09; D-064 side by side, never summed): the history is Melange\'s own rows, in-house beside the fees outside firms report, then the projected years; empty, closed and missing-history states say so', () => {
  const {t} = session();
  const lobby = t.lobbyModel(RUN.banks_lobby, 2026, 2028), banks = LDA_HISTORY.commercial_banks, projected = RUN.banks_lobby.horizons.flatMap(h => h.lobbying.rows).filter(r => r.status === 'ready');
  assert.equal(lobby.state, 'ready');
  assert.deepEqual([lobby.sector, lobby.title], ['bank', 'Bank']);
  assert.deepEqual(plain(lobby.bars.map(b => b.label)), ['2021', '2022', '2023', '2024', '2025', '2026 partial', '2026 projected', '2027 projected', '2028 projected']);
  assert.deepEqual(plain(lobby.bars.map(b => b.kind)), ['annual', 'annual', 'annual', 'annual', 'annual', 'partial', 'projected', 'projected', 'projected']);
  // the history bars ARE the catalog's rows (Melange's: hired firms = income_total, in-house = expenses_total); the model's base level agrees with the last complete year
  banks.annual.forEach((r, i) => assert.deepEqual([lobby.bars[i].hired, lobby.bars[i].inhouse], [r.hired_firms, r.in_house], String(r.year)));
  assert.ok(lobby.bars.every(b => !('total' in b) && !('whisker' in b)), 'no bar carries the two series added or a combined band');
  assert.deepEqual([lobby.bars[5].hired, lobby.bars[5].inhouse], [banks.partial.hired_firms, banks.partial.in_house]);
  assert.deepEqual([lobby.bars[4].hired, lobby.bars[4].inhouse], [RUN.banks_lobby.horizons[0].lobbying.rows[0].series[0].base_level, RUN.banks_lobby.horizons[0].lobbying.rows[0].series[1].base_level]);
  // the projected bars are the model's own projections, one per series, and each whisker is the 80% band of its own series (no band of a sum)
  projected.forEach((r, i) => {
    const bar = lobby.bars[6 + i], fees = r.series.find(s => s.id === 'income_total'), house = r.series.find(s => s.id === 'expenses_total');
    assert.deepEqual([bar.year, bar.hired, bar.inhouse], [r.year, fees.projected, house.projected]);
    assert.deepEqual([bar.whiskers.hired.low, bar.whiskers.hired.high, bar.whiskers.inhouse.low, bar.whiskers.inhouse.high], [fees.band80_low, fees.band80_high, house.band80_low, house.band80_high]);
    assert.match(bar.whiskers.inhouse.tip, new RegExp(`^${r.year} projected · In-house · 80% band\\n`)); assert.match(bar.whiskers.hired.tip, new RegExp(`^${r.year} projected · Fees reported by outside firms · 80% band\\n`));
    assert.match(bar.tips.hired, new RegExp(`^${r.year} projected · Fees reported by outside firms\\n`)); assert.match(bar.tips.inhouse, new RegExp(`^${r.year} projected · In-house\\n`));
    assert.ok(/\n80% band: \$/.test(bar.tips.hired) && /\n80% band: \$/.test(bar.tips.inhouse), 'each bar\'s tooltip gives its band');
  });
  // the card's words and figures
  assert.equal(lobby.subtitle, '2025: 100 organizations · 495 named lobbyists', 'the counts are those of the headline year, named (not the 2026 partial year\'s 97 and 463)');
  assert.deepEqual(plain(lobby.figs), [{value: '$65.7M', label: '2025 in-house lobbying'}, {value: '$23.8M', label: '2025 fees reported by outside firms'}]);
  assert.equal(lobby.partialLine, '2026 through Q2: in-house $39.0M · outside-firm fees $13.4M');          // headline format: always one decimal; the tooltip below keeps ldMoney ($39M)
  assert.equal(lobby.bars[5].tips.inhouse, '2026 partial · In-house · $39M');
  assert.match(lobby.aria, /^Bank lobbying by year, in-house and fees reported by outside firms side by side and never added, nominal dollars\. 2021: in-house \$/); assert.match(lobby.aria, /2026 partial \(partial year\): in-house \$39M, fees reported by outside firms \$13\.4M(;|$)/);
  assert.match(lobby.aria, /2026 projected: in-house \$[\d.]+[MBk] \(80% band \$[\d.]+[MBk] to \$[\d.]+[MBk]\), fees reported by outside firms \$[\d.]+[MBk] \(80% band /); assert.doesNotMatch(lobby.aria, /total/i);
  // the axis covers history, projections and the whisker tops of the single series
  assert.equal(lobby.axisMax, Math.max(...lobby.bars.flatMap(b => [b.inhouse, b.hired, ...Object.values(b.whiskers || {}).map(w => w.high)])));
  assert.equal(lobby.dollars, 'nominal dollars'); assert.equal(lobby.caption, undefined, 'the old "Bars: 80% band" caption is gone');
  assert.equal(t.lobbyModel(RUN.banks, 2026, 2028).state, 'ready', 'blank growth inputs are no change: the nowcast is drawn');
  assert.deepEqual(plain(t.lobbyModel(null, 2026, 2028)), {state: 'empty', text: 'No lobbying projection for 2026–2028'});
  assert.match(t.lobbyModel(RUN.property_casualty, 2026, 2028).text, /^Not shown: No Melange-LDA sector for this industry/);
  // a console on the earlier anchor snapshot carries no lda_history: the card says so instead of drawing a made-up history
  const none = session({history: false}).t.lobbyModel(RUN.banks_lobby, 2026, 2028);
  assert.equal(none.state, 'unavailable'); assert.match(none.text, /^Not shown: the Melange LDA history is not available \(this console or its anchor snapshot predates it\)\. Restart the console and rebuild the anchors/);
});

test('lobbying (Melange card): credit unions, and a snapshot without the roster counts leaves the subtitle out', () => {
  const {t} = cycleSession('credit_unions'), lobby = t.lobbyModel(CYC.credit_unions, 2026, 2028);
  assert.deepEqual([lobby.sector, lobby.title, lobby.subtitle], ['credit_union', 'Credit Union', '2025: 25 organizations · 83 named lobbyists']);
  assert.deepEqual(plain(lobby.figs), [{value: '$6.2M', label: '2025 in-house lobbying'}, {value: '$2.1M', label: '2025 fees reported by outside firms'}]);
  assert.equal(lobby.partialLine, '2026 through Q2: in-house $3.4M · outside-firm fees $1.3M');
  const catalog = clone(CYCLE.catalog), history = clone(LDA_HISTORY.credit_unions);
  for (const r of [...history.annual, history.partial]) { r.orgs = null; r.lobbyists = null; }
  catalog.anchors.industries.credit_unions = Object.assign({status: 'ready'}, catalog.anchors.industries.credit_unions, {lda_history: history});
  t.setCatalog(catalog);
  assert.equal(t.lobbyModel(CYC.credit_unions, 2026, 2028).subtitle, '', 'no counts, no subtitle (nothing is invented)');
});

test('lobbying (owner rulings 2026-10-08): the open year is the nowcast with an 80% and a ~95% band and the observed quarters; later years carry it with the persistence error', () => {
  const {t} = session();
  const lobby = t.lobbyModel(P1.banks_lobby, 2026, 2028);
  const s = P1.banks_lobby.horizons[0].lobbying.rows[0].series[0];
  const [open, next] = lobby.bars.filter(b => b.kind === 'projected');
  assert.equal(open.hired, s.projected);
  assert.match(open.tips.hired, /^2026 projected · Fees reported by outside firms\n/);
  assert.ok(s.band95_low < s.band80_low && s.band80_low < s.projected && s.projected < s.band80_high && s.band80_high < s.band95_high, 'the ~95% band (largest blind miss) encloses the 80% band');
  assert.equal(lobby.dollars, 'nominal dollars');
  assert.match(open.tips.hired, /Nowcast, Q2 cut: \$13\.37M filed \+ 51\.58% of 2025/);
  assert.match(open.tips.hired, /\n80% band: \$/); assert.match(open.tips.hired, /~95% band \(largest blind miss\)/);
  assert.match(open.tips.hired, /Observed Q1\+Q2: \$13\.37M \(face value\)/);
  assert.match(open.tips.hired, /Remainder after the observed quarters: /);
  assert.match(open.tips.hired, /80% band: the 80th percentile of the blind nowcast errors \(n=32 blind tests\)/);
  assert.doesNotMatch(open.tips.hired, /Carried/); assert.match(next.tips.hired, /Carried 1 year past the nowcast: the bands add the LobbyView persistence error, in quadrature/);
  assert.deepEqual(plain(lobby.chips), []);
});

test('a lobbying-only industry (the mechanism; Telecom was the first, until the owner ruling of 2026-10-08 made it a giving industry): the giving chart says why it has no giving forecast; the lobbying chart explains its two bands', () => {
  const {t} = session();
  const run = clone(RUN.banks);
  for (const h of run.horizons) h.pac = {status: 'withheld', reason: 'This industry is offered for lobbying only.', measures: []};
  const model = t.chartModel(run, null, 'general');
  assert.equal(model.empty, true);
  assert.equal(model.message, 'This industry is offered for lobbying only.');
  assert.deepEqual(plain(model.flags.map(f => f.text)), ['This industry is offered for lobbying only.']);
  assert.match(t.lobbyModel(RUN.banks_lobby, 2026, 2028).bars[6].tips.hired, /~95% band: the largest blind miss \(n=32 blind tests\)/, 'the segment tooltip explains its two bands');
});

test('lobbying: a ~95% band that starts below the observed quarters is an amber chip (synthetic case)', () => {
  const {t} = session();
  const run = clone(RUN.banks_lobby), s = run.horizons[0].lobbying.rows[0].series[0];
  s.band95_low = s.observed.amount_nominal * 0.9;
  const chips = t.lobbyModel(run, 2026, 2028).chips;
  assert.equal(chips.length, 1);
  assert.equal(chips[0].kind, AMBER);
  assert.match(chips[0].text, /~95% band starts below the amount already observed/);
});

test('the notes carry the conditioned figures only when the model fix is present, and every waiver', () => {
  const {t} = session({waivers: REGISTRY_WAIVERS});
  const fix = t.conditionedTables(P1.banks_lobby);
  assert.equal(fix.amounts.length, 2);
  assert.equal(fix.mixes.length, 4);
  assert.match(fix.mixes[3][5], /negative before conditioning/);
  assert.match(fix.amounts[0][0], /Roster companies' PACs/);
  assert.deepEqual(plain(t.conditionedTables(RUN.banks_lobby)), {amounts: [], mixes: []});
});

test('logical validity across every scenario of the fixtures: with the model fix nothing drawn contradicts the giving already observed; without it every contradiction carries an amber chip', () => {
  const industries = {banks: 'commercial_banks', banks_lobby: 'commercial_banks', banks_growth_up: 'commercial_banks', banks_growth_down: 'commercial_banks', credit_unions: 'credit_unions',
    credit_unions_lobby: 'credit_unions', credit_unions_growth_down: 'credit_unions', property_casualty: 'property_casualty'};
  let checked = 0, contradictions = 0;
  for (const [name, sector] of Object.entries(industries)) {
    for (const [pathName, runs, source] of [['P1', P1, FIXTURE], ['landed', LANDED.runs, LANDED], ['published', RUN, FIXTURE]]) {
      const {t} = session({sector, source});
      const run = runs[name];
      for (const account of ['pac', 'general']) {
        const model = t.chartModel(run, null, account);
        if (model.empty) continue;
        const cur = model.columns[1];
        if (cur.state !== 'stacked' && cur.state !== 'conflict') continue;
        checked++;
        const amber = model.flags.filter(f => f.kind === AMBER);
        if (pathName !== 'published') {
          assert.ok(cur.low >= cur.observed - 1e-6, `${name}/${account}: the whisker starts at ${cur.low}, under the ${cur.observed} observed`);
          assert.equal(cur.state, 'stacked', `${name}/${account}`);
          assert.ok(cur.remainder > 0 && cur.remainderLow >= 0, `${name}/${account}: negative remainder`);
          assert.ok(Math.abs(cur.observed + cur.remainder - cur.total) < 1e-6, `${name}/${account}: observed + remainder is not the total`);
          assert.ok(cur.low <= cur.total && cur.total <= cur.high, `${name}/${account}: the median is outside its own interval`);
          assert.ok(!amber.some(f => /starts below|exceeds the projected/.test(f.text)), `${name}/${account}: an inconsistency chip with conditioned figures`);
        } else if (cur.low < cur.observed - 0.5 || cur.state === 'conflict' || (cur.remainderLow != null && cur.remainderLow < 0)) {
          contradictions++;
          assert.ok(amber.some(f => /starts below giving already observed|exceeds the projected cycle total/.test(f.text)), `${name}/${account}: a contradiction drawn as returned without the amber chip`);
        }
      }
      for (const chamber of ['House', 'Senate']) {
        const mix = t.mixModel(run, null, chamber);
        mix.rows.forEach((row, i) => {
          if (row.state !== 'ready') return;
          checked++;
          const b = run.horizons[0].shares[chamber].bases[i];
          assert.ok(row.share >= 0 && row.share <= 1, `${name}/${chamber}/${row.label}: share ${row.share} outside 0-100%`);
          if (pathName !== 'published') {
            assert.equal(row.conditioned, true, `${name}/${chamber}/${row.label}`);
            assert.ok(row.low <= row.share + 1e-12 && row.share <= row.high + 1e-12, `${name}/${chamber}/${row.label}: the implied share is outside its own interval`);
            assert.ok(b.remaining_allocation.remaining_R_real_2025 >= 0 && b.remaining_allocation.remaining_D_real_2025 >= 0, `${name}/${chamber}/${row.label}: negative remaining amount`);
            assert.equal(row.flagged, false);
            assert.ok(!mix.chips.some(c => c.kind === AMBER && c.text === BUDGET_TEXT), `${name}/${chamber}: an amber budget chip next to conditioned amounts`);
          } else if (b.remainder_status === 'budget_inconsistent' || (b.projected_remaining_R_real_2025 != null && b.projected_remaining_R_real_2025 < 0)) {
            contradictions++;
            assert.equal(row.flagged, true, `${name}/${chamber}/${row.label}: a negative remaining amount drawn without the amber outline`);
            assert.ok(mix.chips.some(c => c.kind === AMBER && c.text === BUDGET_TEXT), `${name}/${chamber}: no amber budget chip`);
          }
        });
      }
    }
  }
  assert.ok(checked > 90, 'the sweep covered ' + checked + ' drawn marks');
  assert.ok(contradictions >= 4, 'the calculator without the fix contradicts itself in ' + contradictions + ' places the sweep must have flagged');
});

test('the footer line states only what the response says: coverage end, work units, CPI', () => {
  const {t} = session();
  assert.deepEqual(plain(t.footerFacts(RUN.banks.anchors)), ['Observed through 2026-06-30', '41 of 67 work units complete', 'No 2026 CPI yet']);
  assert.deepEqual(plain(t.footerFacts(P1.banks.anchors)), ['Observed through 2026-06-30', '41 of 67 work units complete', 'No 2026 CPI yet']);
  assert.deepEqual(plain(t.footerFacts(RUN.credit_unions.anchors)), ['Observed through 2026-05-31 (cut back from 2026-06-30)', 'No 2026 CPI yet']);
  assert.deepEqual(plain(t.footerFacts(FIXTURE.catalog.anchors.industries.commercial_banks)), ['Observed through 2026-06-30', '41 of 67 work units complete', 'No 2026 CPI yet'], 'before a calculation the same facts come from the catalog');
  assert.deepEqual(plain(t.footerFacts(null)), []);
});

test('unset inputs send no value: nothing defaults a slider to a number, except revenue growth at its IBIS default', () => {
  const {t} = session({outlook: false});
  t.buildInputs();
  const controls = t.getControls();
  assert.equal(controls.length, 6, 'growth and two control scores for each of the two horizons');
  assert.deepEqual(plain(controls.map(c => c.holder.value)), ['11.778', '-1.848', '', '', '', ''], 'only the two growth holders open filled (banks default); every other holder starts blank');
  const request = t.getRequest();
  for (const end of [2026, 2028]) {
    const a = request.horizons[end];
    assert.equal(a.growth_pct, end === 2026 ? '11.778' : '-1.848'); assert.equal(a.prior_growth_pct, '');
    assert.deepEqual({...a.control_scores}, {House: '', Senate: ''});
    assert.deepEqual({...a.current_control}, {House: '', Senate: ''});
  }
  assert.deepEqual({...request.horizons[2026].annual_forecast_changes}, {2026: ''});
  assert.deepEqual({...request.horizons[2028].annual_forecast_changes}, {2027: '', 2028: ''});
  t.refreshControls();
  assert.deepEqual(plain(controls.map(c => c.slider.classList.contains('is-unset'))), [false, false, true, true, true, true], 'every slider but the two growth ones is in its unset state');
});

test('the saved outlook stays an editable prefill of the 2026 control scores; growth opens at its IBIS default and the 2027-2028 control inputs stay blank', () => {
  const {t} = session();
  t.buildInputs();
  const request = t.getRequest();
  assert.equal(Number(request.horizons[2026].control_scores.House), FIXTURE.catalog.saved_outlook.control.House.value);
  assert.equal(request.horizons[2026].growth_pct, '11.778');
  assert.equal(request.horizons[2028].control_scores.House, '');
  t.refreshControls();
  const [growth2026, , house2026] = t.getControls();
  assert.equal(growth2026.slider.classList.contains('is-unset'), false);
  assert.equal(house2026.slider.classList.contains('is-unset'), false);
});

test('revenue growth opens at the industry IBIS default with its caption; a change says so, Reset restores it, blank stays blank; an input recalculates once after a pause', async () => {
  const sources = {commercial_banks: 'IBIS 2026-08 · avg 2023–2025 · drives 2026 (one-year lag)', credit_unions: 'IBIS 2026-05 · avg 2023–2025 · drives 2026 (one-year lag)', property_casualty: 'IBIS 2024-11 · avg 2023–2025 · drives 2026 (one-year lag)'};
  const defaults = {commercial_banks: ['11.778', '-1.848'], credit_unions: ['13.362', '1.665'], property_casualty: ['3.657', '1.832']};
  for (const [sector, [g26, g28]] of Object.entries(defaults)) {
    const {t} = session({sector}); t.buildInputs();
    const [a, b] = t.getControls(), request = t.getRequest();
    assert.deepEqual([a.holder.value, b.holder.value, request.horizons[2026].growth_pct, request.horizons[2028].growth_pct], [g26, g28, g26, g28], sector);
    assert.equal(a.dflt.label.textContent, sources[sector], sector); assert.equal(a.dflt.reset.hidden, true);
    assert.equal(a.view.value, t.formatNumber(Number(g26)), 'the box shows the same number the holder holds');
  }
  const {t} = session(); t.buildInputs();
  const [g26] = t.getControls();
  g26.holder.value = '5'; t.refreshControls();
  assert.equal(g26.dflt.label.textContent, 'Changed from default 11.78% ·'); assert.equal(g26.dflt.reset.hidden, false);
  g26.holder.value = ''; t.refreshControls();
  assert.equal(t.getRequest().horizons[2026].growth_pct, '', 'a cleared row stays blank: nothing re-fills it'); assert.equal(g26.dflt.reset.hidden, false);
  g26.dflt.reset.on.click();
  assert.equal(g26.holder.value, '11.778'); assert.equal(g26.dflt.reset.hidden, true); assert.equal(g26.slider.classList.contains('is-unset'), false);
  // the form's own input listener: three quick edits make one timer of 300 ms; nothing is asked before it fires, one calculation after
  const queue = new Map(), requests = []; let next = 1;
  const reply = (path, init) => { requests.push(JSON.parse(init.body)); return Promise.resolve({ok: true, status: 200, headers: {get: () => 'application/json'}, json: () => Promise.resolve(RUN.banks)}); };
  const live = session({fetchImpl: reply, timers: {setTimeout: (fn, ms) => { queue.set(next, {fn, ms}); return next++; }, clearTimeout: id => queue.delete(id)}});
  live.t.buildInputs();
  for (let i = 0; i < 3; i++) live.els['gf-form'].on.input();
  assert.equal(queue.size, 1); assert.equal([...queue.values()][0].ms, 300); assert.equal(requests.length, 0);
  await [...queue.values()][0].fn();
  assert.equal(requests.length, 1); assert.equal(requests[0].horizons[2026].growth_pct, '11.778');
});

test('rollback (no cycle model): the reference request reads Mentat\'s levels with the anchor cycle\'s own growth and control, never what the user typed', () => {
  const {t, els} = session({cycle: false});
  t.buildInputs();
  const typed = t.getControls()[0].holder; typed.value = '99';
  els['gf-industry'].value = 'commercial_banks';
  const body = t.referenceRequest();
  assert.equal(body.industry, 'commercial_banks');
  assert.equal(body.as_of, '2026-09-30');
  assert.match(body.assumption_source, /^Reference levels only: the 2023–2024 growth and control, no scenario/);
  const anchorInputs = FIXTURE.catalog.political_inputs.anchor_cycles['2024'];
  const history = FIXTURE.catalog.industries.find(i => i.id === 'commercial_banks').history.find(r => r.cycle === 2024);
  for (const end of [2026, 2028]) {
    const a = body.horizons[end];
    assert.equal(a.growth_pct, history.growth_pct);
    assert.equal(a.control_scores.House, anchorInputs.House.p_rep * 100);
    assert.equal(a.current_control.Senate, anchorInputs.current_control.Senate);
    assert.deepEqual({...a.annual_forecast_changes}, {}, 'no lobbying value is invented');
  }
  assert.notEqual(body.horizons[2026].growth_pct, '99');
  assert.equal('growth_basis' in body, false, 'the earlier calculator knows no growth basis');
});


// ---- the cycle model (owner 2026-10-07): the page reads the cycle-aware calculator's answers and still reads the earlier one's -----------------------------------------------
// CYCLE holds the console's own answers to the page's default requests (port 8806 after the apply): the cycle_model_view the catalog carries, a pac block without change_pct,
// slope, ordinary_p or log-change intervals (model_kind 'cycle_model', growth_input, interval_kind), per-measure model{} blocks, layer R8 (the panel nowcast, D-116; no budget change) on the roster PAC and P1 on general giving, and a
// chained 2027-28 step whose coefficient interval is null. The first render of exactly this answer threw "Cannot read properties of undefined (reading '0')": pacCellV2 read
// p.coefficient_interval_pct[0] of a block that has none.
const textOf = node => node == null ? '' : typeof node === 'string' ? node : [node.textContent || '', ...(node.children || []).map(textOf)].join(' ');
const CYC = CYCLE.runs, CYC_SECTORS = [['commercial_banks', CYC.banks], ['credit_unions', CYC.credit_unions], ['property_casualty', CYC.property_casualty]];
// The recorded cycle answers carry the calculator's nominal twin (D-092); the page opens in nominal dollars. The older tests below pin the 2025-dollar figures, so this session asks for that basis
// explicitly (the same control the page offers); nominalSession is the page as a viewer with no stored choice sees it.
const cycleSession = (sector, dollars = 'real') => { const s = session({sector, source: CYCLE}); s.t.setDollars(dollars); return s; };
const nominalSession = sector => session({sector, source: CYCLE});

test('the recorded cycle answers are what the apply report says (company PACs on the panel nowcast R8, D-116): schema 2, cycle_model, R8 and P1, no old pac fields, a null chained coefficient interval', () => {
  assert.equal(CYC.banks.cycle_model.layers.pac_to_candidates, 'R8'); assert.equal(CYC.banks.cycle_model.layers.general_giving, 'P1');
  assert.deepEqual(plain(CYC.banks.cycle_model.growth_applies), {pac_to_candidates: false, general_giving: true});
  for (const [, run] of CYC_SECTORS) {
    assert.equal(run.status, 'ready');
    for (const h of run.horizons) {
      assert.equal(h.pac.model_kind, 'cycle_model'); assert.equal(h.pac.slope, null); assert.equal(h.pac.change_pct, undefined); assert.equal(h.pac.coefficient_interval_pct, undefined);
      for (const m of h.pac.measures.filter(m => m.status === 'ready')) assert.ok(m.model && Number.isFinite(m.model.budget_growth_pct));
    }
    for (const m of run.horizons[1].pac.measures.filter(m => m.status === 'ready')) { assert.equal(m.cycle_coefficient_low, null); assert.ok(m.chained_from); }
  }
  assert.equal(measure(CYC.banks, 0, 'pac_to_candidates').layer, 'R8'); assert.equal(measure(CYC.banks, 0, 'general_giving').layer, 'P1');
  // company PACs: no budget change on either horizon, a range of the three prior cycles (not a 95% interval) in the open cycle, and a chain from the nowcast
  for (const [, run] of CYC_SECTORS) for (const h of run.horizons) {
    const pac = h.pac.measures.find(m => m.id === 'pac_to_candidates'); if (pac.status !== 'ready') continue;
    assert.deepEqual([pac.model.budget_change, pac.model.growth_applies], [0, false]);
    assert.ok(/not a 95% interval/.test(pac.interval_kind) || /chained on the panel nowcast/.test(pac.interval_kind));
  }
  assert.deepEqual(plain(measure(CYC.banks, 1, 'pac_to_candidates').chained_from), {layer: 'R8', cycle: 2026});
});

test('the cycle answers render for every offered industry: no throw, the growth line and the interval definition replace the old change line, nothing prints NaN, null or Unavailable', () => {
  for (const [sector, run] of CYC_SECTORS) {
    const {t, els} = cycleSession(sector); t.buildInputs();
    assert.doesNotThrow(() => t.render(clone(run)), sector);
    const text = textOf(els['gf-results']);
    assert.match(text, /Industry revenue growth [+-][\d.]+% \(IBIS \d{4}-\d{2} default\), on the Mentat 2023–2024 level\./, sector + ': open cycle growth line');
    assert.match(text, /Industry revenue growth [+-][\d.]+% \(IBIS \d{4}-\d{2} default\), chained on the open cycle\./, sector + ': next cycle growth line');
    assert.ok(text.includes(run.horizons[0].pac.interval_kind + '.'), sector + ': the interval says what it excludes');
    assert.doesNotMatch(text, /NaN|undefined|null|Unavailable to/, sector);
    assert.doesNotMatch(text, /vs the Mentat 2023–2024 level \(|ordinary p = not retained/, sector + ': none of the earlier model\'s PAC lines');
  }
  // an entered growth says so
  const user = clone(CYC.banks); for (const h of user.horizons) h.pac.growth_input = {...h.pac.growth_input, source: 'user'};
  const {t, els} = cycleSession('commercial_banks'); t.buildInputs(); t.render(user);
  assert.match(textOf(els['gf-results']), /Industry revenue growth [+-][\d.]+% \(entered\)/);
});

test('the earlier calculator\'s answers still render: with its own catalog (rollback) and, as during the switch-over, under a catalog that already carries the cycle model', () => {
  for (const [name, cycle] of [['rollback catalog', false], ['cycle catalog', true]]) {
    for (const [sector, run] of [['commercial_banks', RUN.banks], ['credit_unions', RUN.credit_unions], ['property_casualty', RUN.property_casualty], ['commercial_banks', P1.banks], ['commercial_banks', LANDED.runs.banks]]) {
      const {t, els} = session({sector, cycle}); t.buildInputs();
      assert.doesNotThrow(() => t.render(clone(run)), name + ' ' + sector);
      const text = textOf(els['gf-results']);
      assert.match(text, /vs the Mentat 2023–2024 level \((previous cycle|chained on a projection, \d steps)\); coefficient interval [+-]?[\d.]+% to [+-]?[\d.]+%; prediction interval/, name + ' ' + sector + ': the earlier model\'s change line');
      assert.match(text, /pooled slope|industry-specific slope|partially pooled slope|ordinary p = /, name + ' ' + sector + ': and its slope line');
      assert.doesNotMatch(text, /Industry revenue growth [+-]/, name + ' ' + sector);
    }
  }
});

test('the chart reads the cycle answer: banks 2025-26 general giving $14.47M (D-121; was $13.86M) and company PACs $6.06M (the panel nowcast, D-116), 2027-28 the chained step; credit unions likewise', () => {
  const {t} = cycleSession('commercial_banks');
  const general = t.chartModel(CYC.banks, CYC.banks, 'general'), pac = t.chartModel(CYC.banks, CYC.banks, 'pac');
  assert.equal(general.basis, '2025 dollars');
  assert.equal(general.columns[1].total, measure(CYC.banks, 0, 'general_giving').cycle_given_observed_median);
  assert.equal(pac.columns[1].total, measure(CYC.banks, 0, 'pac_to_candidates').cycle_given_observed_median);
  assert.equal(general.columns[1].conditioned, true);
  assert.equal((general.columns[1].total / 1e6).toFixed(2), '14.47'); assert.equal((pac.columns[1].total / 1e6).toFixed(2), '6.06');
  assert.equal(general.columns[2].total, measure(CYC.banks, 1, 'general_giving').projected_cycle_real_2025);
  assert.equal(pac.columns[2].total, measure(CYC.banks, 1, 'pac_to_candidates').projected_cycle_real_2025);
  const cu = cycleSession('credit_unions').t;
  assert.equal(cu.chartModel(CYC.credit_unions, CYC.credit_unions, 'pac').columns[1].total, measure(CYC.credit_unions, 0, 'pac_to_candidates').cycle_given_observed_median);
  // the tooltip's model-change line: the industry budget change, half applied; no change_pct to read
  assert.match(texts(general.columns[2].tip), /chained on the open cycle, industry budget change [+-][\d.]+%, half applied/);
  assert.match(texts(general.columns[1].tip), /industry budget change \+12\.89%, half applied/, 'the open cycle: the budget change the model derived from the default growth');
  // company PACs (D-116): no budget change on either horizon, and the tooltip says so
  assert.match(texts(pac.columns[2].tip), /chained on the open cycle, no industry budget change: growth does not affect company PACs/);
  assert.match(texts(pac.columns[1].tip), /no industry budget change: growth does not affect company PACs/);
  assert.doesNotMatch(texts(pac.columns[1].tip) + texts(pac.columns[2].tip), /half applied/);
});

test('company PACs: the 2025-26 whisker is the range of the three prior cycles (not a 95% interval) and says so everywhere it is named; the 2027-28 whisker and every general-giving label keep the 95% interval', () => {
  const {t} = cycleSession('commercial_banks');
  const m26 = measure(CYC.banks, 0, 'pac_to_candidates'), m28 = measure(CYC.banks, 1, 'pac_to_candidates');
  const general = t.chartModel(CYC.banks, CYC.banks, 'general'), pac = t.chartModel(CYC.banks, CYC.banks, 'pac');
  // the whisker is exactly what the response returned: the range, converted once
  assert.equal(pac.columns[1].low, m26.cycle_given_observed_low); assert.equal(pac.columns[1].high, m26.cycle_given_observed_high);
  assert.ok(pac.columns[1].low < pac.columns[1].total && pac.columns[1].total < pac.columns[1].high);
  assert.equal(pac.rangeLabel, 'Range of prior cycles'); assert.equal(general.rangeLabel, '95% range');
  // tooltip rows, aria, remainder tip
  assert.ok(row(pac.columns[1].tip, 'Range of prior cycles') && !row(pac.columns[1].tip, '95% prediction interval'), texts(pac.columns[1].tip));
  assert.ok(texts(pac.columns[1].tip).includes("Range of the three prior cycles' pace (2020, 2022, 2024), not a 95% interval"));
  assert.match(pac.columns[1].aria, /range of prior cycles \$[\d.]+M to \$[\d.]+M/); assert.doesNotMatch(pac.columns[1].aria, /95% prediction interval/);
  assert.ok(row(pac.columns[1].remainderTip, 'Remainder, range of prior cycles') && !row(pac.columns[1].remainderTip, 'Remainder prediction interval'));
  // 2027-28 chains with a step interval, said in its own words
  assert.ok(row(pac.columns[2].tip, '95% prediction interval')); assert.ok(texts(pac.columns[2].tip).includes(m28.interval_kind));
  assert.match(pac.columns[2].aria, /95% prediction interval/);
  // general giving is untouched
  assert.ok(row(general.columns[1].tip, '95% prediction interval')); assert.match(general.columns[1].aria, /95% prediction interval/);
  // the Methods table of the conditioned open-cycle amounts names the range
  const rows = plain(t.conditionedTables(CYC.banks).amounts), pacRow = rows.find(r => r[0].startsWith('Roster companies')), genRow = rows.find(r => !r[0].startsWith('Roster companies'));
  assert.match(pacRow[2], /\(range of prior cycles\)$/); assert.match(pacRow[4], /\(range of prior cycles\)$/); assert.doesNotMatch(genRow[2], /range of prior cycles/);
  // the earlier layers keep their wording (an answer without R8)
  const earlier = clone(CYC.banks); delete measure(earlier, 0, 'pac_to_candidates').layer;
  assert.equal(t.chartModel(earlier, earlier, 'pac').rangeLabel, '95% range');
});

test('under the Company PACs account the growth captions say growth does not affect company PACs; General giving keeps its own, and an older catalog (no growth_applies) keeps them under both', () => {
  const PAC_TEXT = 'Growth does not affect company PACs (panel nowcast)', IBIS = ['IBIS 2026-08 · avg 2023–2025 · drives 2026 (one-year lag)', 'IBIS 2026-08 · avg 2025–2027 · drives 2028 (one-year lag)'];
  const {t} = cycleSession('commercial_banks'); t.buildInputs(); t.refreshControls();
  const growth = () => t.getControls().filter(c => c.dflt), captions = () => growth().map(c => c.dflt.label.textContent);
  assert.equal(growth().length, 2); assert.deepEqual(plain(captions()), IBIS);
  t.setAccount('pac');
  assert.deepEqual(plain(captions()), [PAC_TEXT, PAC_TEXT]); assert.ok(growth().every(c => c.dflt.reset.hidden && c.hint.textContent.startsWith(PAC_TEXT + '.')));
  growth()[0].holder.value = '14'; t.setAccount('pac'); assert.equal(growth()[0].dflt.label.textContent, PAC_TEXT); assert.equal(growth()[0].dflt.reset.hidden, true);        // a changed growth says the same under Company PACs
  t.setAccount('lobby'); assert.deepEqual(plain(captions()), ['Changed from default 11.78% ·', IBIS[1]], 'Lobbying is not company PACs');
  t.setAccount('general'); assert.equal(growth()[0].dflt.label.textContent, 'Changed from default 11.78% ·'); assert.equal(growth()[0].dflt.reset.hidden, false); assert.equal(growth()[1].dflt.label.textContent, IBIS[1]);
  // an older catalog does not say growth is ignored: the captions stay
  const old = session({sector: 'commercial_banks', source: CYCLE}), view = clone(CYCLE.catalog.cycle_model_view); delete view.growth_applies;
  old.t.setCatalog(Object.assign(clone(CYCLE.catalog), {cycle_model_view: view})); old.t.buildInputs(); old.t.refreshControls(); old.t.setAccount('pac');
  assert.deepEqual(plain(old.t.getControls().filter(c => c.dflt).map(c => c.dflt.label.textContent)), IBIS);
  // the help under the growth input says the same
  const {t: help, els} = cycleSession('commercial_banks'); help.buildInputs(); help.renderEvidence();
  assert.match(textOf(els['gf-input-notes']), /the projection moves by half of it \(the 50\/50 rule\), except company PACs, which it does not affect \(panel nowcast\)\. Blank uses the IBIS default\./);
});

test('company PACs, credit unions (D-117): the chart and the Methods fold name the population ("16 PACs, published panel cohort"; banks stay "19 bank companies"), each account keeps its own observed-to date, and the party split\'s Company PACs row names the same cohort (no single-PAC note)', () => {
  const PANEL = 'Credit unions: 16 PACs (published panel cohort)', BANKS19 = 'Banks: 19 bank companies';
  const OLD_NOTE = 'Credit-union company-PAC party split still reflects the earlier single-PAC roster.';
  const {t, els} = cycleSession('credit_unions');
  const pac = t.chartModel(CYC.credit_unions, CYC.credit_unions, 'pac'), general = t.chartModel(CYC.credit_unions, CYC.credit_unions, 'general');
  // the population, from the response, on the account that is a total of it (the anchor column's tooltip) and nowhere on general giving
  assert.equal(pac.population.label, PANEL); assert.equal(general.population, null);
  assert.equal(row(pac.columns[0].tip, 'Population')[1], PANEL); assert.equal(row(general.columns[0].tip, 'Population'), undefined);
  assert.ok(texts(pac.columns[0].tip).includes("The published PAC panel's cohort"));
  assert.equal(pac.columns[0].nominal, measure(CYC.credit_unions, 0, 'pac_to_candidates').anchor_cycle_nominal);          // the 2023-24 bar is the cohort's total, not one committee's
  // each account is observed to its own date; the cut-back chip is general giving's and is not on the panel's account
  assert.equal(pac.columns[1].through, '2026-09-26'); assert.equal(general.columns[1].through, '2026-05-31');
  assert.ok(texts(pac.columns[1].observedTip).includes('2026-09-26') && !texts(pac.columns[1].observedTip).includes('2026-05-31'));
  assert.ok(texts(general.columns[1].observedTip).includes('2026-05-31') && !texts(general.columns[1].observedTip).includes('2026-09-26'));
  assert.ok(!pac.flags.some(f => /^Coverage cut back/.test(f.text)), 'the panel account is not cut back'); assert.ok(general.flags.some(f => f.text === 'Coverage cut back to 2026-05-31'));
  assert.ok(!pac.flags.some(f => /One PAC|single-PAC/i.test(f.text)), 'no single-PAC thin-base flag on the cohort');
  // the line under the chart title: the population and the account's own date; General giving keeps "2025 dollars"
  t.setAccount('pac'); t.paintAccountChrome({chart: pac});
  assert.equal(els['gf-basis'].textContent, '2025 dollars · ' + PANEL + ', observed to 2026-09-26');
  t.setAccount('general'); t.paintAccountChrome({chart: general});
  assert.equal(els['gf-basis'].textContent, '2025 dollars');
  const banks = cycleSession('commercial_banks'), bankPac = banks.t.chartModel(CYC.banks, CYC.banks, 'pac');
  assert.equal(bankPac.population.label, BANKS19);
  assert.equal(pac.accountLabel, 'Panel cohort PACs: own giving to House and Senate candidates'); assert.match(bankPac.accountLabel, /^Roster companies' PACs: /);          // the cohort is not "roster companies"
  banks.t.setAccount('pac'); banks.t.paintAccountChrome({chart: bankPac});
  assert.equal(banks.els['gf-basis'].textContent, '2025 dollars · ' + BANKS19 + ', observed to 2026-06-30');
  // the Methods fold (Sources and notes): the population with the panel's own date (the split's too, the server notice says so); no split note any more; banks name theirs
  t.anchorNote(t.industry());
  const notes = textOf(els['gf-anchors-note']);
  assert.ok(notes.includes('Company PACs: ' + PANEL + '; observed to 2026-09-26 (the published PAC panel\'s own date).'), notes);
  assert.ok(!notes.includes(OLD_NOTE) && !notes.includes('single-PAC')); assert.ok(notes.includes('observed through 2026-05-31'), 'general giving keeps its own date');
  banks.t.anchorNote(banks.t.industry());
  const bankNotes = textOf(banks.els['gf-anchors-note']);
  assert.ok(bankNotes.includes('Company PACs: ' + BANKS19 + '; observed to 2026-06-30.') && !bankNotes.includes('single-PAC'));
  // the party split: the Company PACs bar names the cohort in its tooltip (the very population of the dollars) and the results table does too; the Employees bar does not; banks do not; no single-PAC note anywhere
  for (const chamber of ['House', 'Senate']) {
    const mix = t.mixModel(CYC.credit_unions, CYC.credit_unions, chamber), pacRow = mix.rows.find(r => r.id === 'pac_candidate'), empRow = mix.rows.find(r => r.id === 'employee_candidate');
    assert.equal(row(pacRow.tip, 'Population')[1], PANEL, chamber); assert.equal(row(empRow.tip, 'Population'), undefined, chamber);
    assert.ok(!texts(pacRow.tip).includes(OLD_NOTE) && !texts(empRow.tip).includes(OLD_NOTE), chamber);
    assert.equal(pacRow.state, 'ready', chamber); assert.ok(pacRow.anchorShare > 0.4 && pacRow.anchorShare < 0.5, chamber + ': the 2023-24 tick is the cohort\'s share, no longer 0% for the Senate');
    assert.equal(row(banks.t.mixModel(CYC.banks, CYC.banks, chamber).rows.find(r => r.id === 'pac_candidate').tip, 'Population'), undefined, chamber);
  }
  t.buildInputs(); t.render(clone(CYC.credit_unions));
  assert.ok(!textOf(els['gf-results']).includes(OLD_NOTE)); assert.ok(textOf(els['gf-results']).includes('Population: ' + PANEL + '.')); assert.ok(textOf(els['gf-results']).includes('The 16 PACs of the published panel cohort: own giving to House and Senate candidates'));
  banks.t.buildInputs(); banks.t.render(clone(CYC.banks));
  assert.ok(!textOf(banks.els['gf-results']).includes('Population: ') && !textOf(banks.els['gf-results']).includes(OLD_NOTE)); assert.ok(textOf(banks.els['gf-results']).includes("The roster companies' PACs' own giving"));
  // an answer from the earlier calculator (no population, the single-PAC note) draws as it did
  const earlier = clone(CYC.credit_unions);
  for (const h of earlier.horizons) { for (const m of h.pac.measures) delete m.population; for (const c of ['House', 'Senate']) for (const b of h.shares[c].bases || []) { delete b.population; if (b.id === 'pac_candidate') b.roster_note = OLD_NOTE; } }
  assert.ok(texts(t.mixModel(earlier, earlier, 'House').rows.find(r => r.id === 'pac_candidate').tip).includes(OLD_NOTE), 'an older answer still carries its own note');
  const old = cycleSession('credit_unions'), oldPac = old.t.chartModel(earlier, earlier, 'pac');
  assert.equal(oldPac.population, null); assert.equal(row(oldPac.columns[0].tip, 'Population'), undefined);
  old.t.setAccount('pac'); old.t.paintAccountChrome({chart: oldPac}); assert.equal(old.els['gf-basis'].textContent, '2025 dollars');
  assert.ok(oldPac.flags.some(f => /^Coverage cut back/.test(f.text)));
});

test('Telecom (owner ruling 2026-10-08, OpenSecrets B09 and IBISWorld wireless + cable): general giving and company PACs read like the other industries, the company PACs name "Telecom: 8 PACs (published panel cohort)", lobbying is there, and the party split is scored on the pooled share models', () => {
  const TEL = CYC.telecom, PANEL = 'Telecom: 8 PACs (published panel cohort)';
  assert.equal(TEL.status, 'ready');
  const {t, els} = cycleSession('telecom');
  const pac = t.chartModel(TEL, TEL, 'pac'), general = t.chartModel(TEL, TEL, 'general');
  assert.ok(!pac.empty && !general.empty, 'both giving charts are drawn');
  assert.equal(general.columns[1].total, measure(TEL, 0, 'general_giving').cycle_given_observed_median); assert.equal(general.columns[1].conditioned, true);
  assert.equal(pac.columns[1].total, measure(TEL, 0, 'pac_to_candidates').cycle_given_observed_median);
  assert.equal((pac.columns[1].total / 1e6).toFixed(2), '5.75'); assert.equal((pac.columns[2].total / 1e6).toFixed(2), '5.77');          // the panel's nowcast (2025 USD) and the 2027-28 step with the +9%
  assert.equal(pac.columns[2].total, measure(TEL, 1, 'pac_to_candidates').projected_cycle_real_2025);
  assert.equal(pac.rangeLabel, 'Range of prior cycles'); assert.equal(general.rangeLabel, '95% range');
  // the population, from the response: the published panel's 8 PACs, with the panel's own date; general giving keeps its own
  assert.equal(pac.population.label, PANEL); assert.equal(general.population, null);
  assert.equal(row(pac.columns[0].tip, 'Population')[1], PANEL); assert.equal(pac.accountLabel, 'Panel cohort PACs: own giving to House and Senate candidates');
  assert.equal(pac.columns[1].through, '2026-09-26'); assert.equal(general.columns[1].through, '2026-06-30');
  t.setAccount('pac'); t.paintAccountChrome({chart: pac}); assert.equal(els['gf-basis'].textContent, '2025 dollars · ' + PANEL + ', observed to 2026-09-26');
  t.setAccount('general'); t.paintAccountChrome({chart: general}); assert.equal(els['gf-basis'].textContent, '2025 dollars');
  // the growth input: the wireless + cable window average of the IBIS path, which moves general giving only
  const view = CYCLE.catalog.cycle_model_view;
  assert.deepEqual(plain([view.growth.telecom['2026'].growth_default, view.growth.telecom['2028'].growth_default]), [2.049, 4.241]);
  assert.equal(view.growth.telecom['2026'].edition, '2024-10 (51721) + 2024-12 (51711a)');
  assert.deepEqual(plain(view.growth_applies), {pac_to_candidates: false, general_giving: true});
  assert.equal(view.company_pacs.telecom.label, PANEL);
  // lobbying is as it was: the nowcast chart is drawn
  const lobby = t.lobbyModel(TEL, 2026, 2028);
  assert.equal(lobby.state, 'ready'); assert.deepEqual([lobby.sector, lobby.title, lobby.subtitle], ['telecom', 'Telecom', '2025: 24 organizations · 532 named lobbyists']);
  assert.deepEqual(plain(lobby.figs), [{value: '$102.4M', label: '2025 in-house lobbying'}, {value: '$37.0M', label: '2025 fees reported by outside firms'}]);
  assert.equal(lobby.partialLine, '2026 through Q2: in-house $52.1M · outside-firm fees $17.6M'); assert.ok(!JSON.stringify(plain(lobby)).includes('139.4M'), 'the two series added ($139.4M) appear nowhere');
  assert.deepEqual(plain(lobby.bars.map(b => b.label)), ['2021', '2022', '2023', '2024', '2025', '2026 partial', '2026 projected', '2027 projected', '2028 projected']);
  // the party split (owner ruling 2026-10-09, option A): scored on the pooled share models, so both bases of both chambers draw a bar, the drawn share is the implied full-cycle one, the tick is
  // Mentat's 2023-24 share, and the Methods fold says in one note that the pooled model was used
  const drawn = {House: {employee_candidate: 0.40814284221468833, pac_candidate: 0.48535643138790063}, Senate: {employee_candidate: 0.3231239808469534, pac_candidate: 0.7086811641968696}};
  for (const chamber of ['House', 'Senate']) {
    const mix = t.mixModel(TEL, TEL, chamber);
    assert.equal(mix.rows.length, 2, chamber);
    for (const r of mix.rows) {
      const b = basis(TEL, 0, chamber, r.id);
      assert.equal(r.state, 'ready', chamber + ' ' + r.id);
      assert.equal(r.share, b.remaining_allocation.implied_cycle_R_share); assert.ok(Math.abs(r.share - drawn[chamber][r.id]) < 1e-12, chamber + ' ' + r.id);
      assert.equal(r.anchorShare, b.anchor_share); assert.equal(r.conditioned, true);
    }
    const note = mix.chips.find(c => c.text === 'Scored on the pooled share model.');
    assert.ok(note && note.kind === NEUTRAL, chamber + ': the pooled-model note');
    assert.match(texts(note.tip), /no share-model rows of its own/);
    assert.equal(mix.chips.filter(c => c.kind === AMBER).length, 0, chamber + ': no warning');
    assert.equal(chipTexts(t.mixModel(CYC.banks, CYC.banks, chamber).chips).includes('Scored on the pooled share model.'), false, 'an industry with rows of its own carries no such note');
  }
  // the whole answer renders, nothing prints NaN, null or undefined
  t.buildInputs(); assert.doesNotThrow(() => t.render(clone(TEL)));
  const text = textOf(els['gf-results']);
  assert.doesNotMatch(text, /NaN|undefined|null|Unavailable to/); assert.ok(text.includes('The 8 PACs of the published panel cohort: own giving to House and Senate candidates'));          // the results table names the cohort
  t.anchorNote(t.industry());
  const notes = textOf(els['gf-anchors-note']);
  assert.ok(notes.includes('Company PACs: ' + PANEL + '; observed to 2026-09-26'), notes);
});

test('presidential-cycle factors (D-118, D-119, D-121): company PACs +9% on the 2027-28 tooltip, general giving ×1.86 on both tooltips, one Methods line each; an answer or catalog without them says nothing', () => {
  const PAC_WORDS = '+9% (owner rule; national PAC data show 0.98)', PAC_LINE = 'Presidential cycle uplift for company PACs: ' + PAC_WORDS;
  const GEN_WORDS = '×1.86 (owner rule; OpenSecrets total election cost)', GEN_LINE = 'Presidential cycle factor for general giving: ' + GEN_WORDS;
  const count = (text, line) => text.split(line).length - 1;
  for (const [sector, run] of [['commercial_banks', CYC.banks], ['credit_unions', CYC.credit_unions], ['property_casualty', CYC.property_casualty]]) {
    const {t, els} = cycleSession(sector);
    const pac = t.chartModel(run, run, 'pac'), general = t.chartModel(run, run, 'general');
    // general giving: the factor on BOTH columns, the midterm step said to divide by it; no company-PAC wording
    const g26 = measure(run, 0, 'general_giving').model.presidential_uplift, g28 = measure(run, 1, 'general_giving').model.presidential_uplift;
    assert.ok(Math.abs(g26.factor - 12.56 / 6.74) < 1e-12 && Object.keys(g26.ratio_by_pair).length === 8 && JSON.stringify(g26) === JSON.stringify(g28), sector + ': the eight totals and 12.56 / 6.74');
    assert.equal(row(general.columns[1].tip, 'Presidential cycle factor')[1], GEN_WORDS + '; this midterm step divides by it', sector);
    assert.equal(row(general.columns[2].tip, 'Presidential cycle factor')[1], GEN_WORDS, sector);
    assert.equal(row(general.columns[1].tip, 'Presidential cycle uplift'), undefined, sector);
    t.anchorNote(t.industry());
    const notes = textOf(els['gf-anchors-note']);
    assert.equal(count(notes, GEN_LINE), 1, sector + ': one general-giving Methods line');
    if (sector === 'property_casualty') { assert.ok(!notes.includes('Presidential cycle uplift'), 'no company PACs, no company-PAC line'); continue; }
    // company PACs: the uplift only on the step into 2027-28
    const uplift = measure(run, 1, 'pac_to_candidates').model.presidential_uplift;
    assert.equal(uplift.factor, 1.09); assert.equal(uplift.source, 'owner rule D-118/D-121: +9% (PAC spending less cyclical; national FEC all-PAC ratio 0.98)');
    assert.equal(row(pac.columns[2].tip, 'Presidential cycle uplift')[1], PAC_WORDS, sector);
    assert.equal(row(pac.columns[1].tip, 'Presidential cycle uplift'), undefined, sector + ': the 2025-26 column is the nowcast');
    assert.equal(row(pac.columns[2].tip, 'Presidential cycle factor'), undefined, sector);
    assert.equal(count(notes, PAC_LINE), 1, sector + ': one company-PAC Methods line');
    // an answer from before the rules (no factor in the model blocks) draws as it did
    const earlier = clone(run);
    for (const [h, id] of [[0, 'general_giving'], [1, 'general_giving'], [1, 'pac_to_candidates']]) delete measure(earlier, h, id).model.presidential_uplift;
    assert.equal(row(t.chartModel(earlier, earlier, 'pac').columns[2].tip, 'Presidential cycle uplift'), undefined);
    assert.equal(row(t.chartModel(earlier, earlier, 'general').columns[2].tip, 'Presidential cycle factor'), undefined);
  }
  // a catalog without the rules: no line at all, the rest of the note is unchanged
  const old = cycleSession('commercial_banks'), view = clone(CYCLE.catalog.cycle_model_view); delete view.presidential_uplift;
  old.t.setCatalog(Object.assign(clone(CYCLE.catalog), {cycle_model_view: view})); old.t.anchorNote(old.t.industry());
  const oldNotes = textOf(old.els['gf-anchors-note']);
  assert.ok(!oldNotes.includes('Presidential cycle'));
  assert.ok(oldNotes.includes('Company PACs: Banks: 19 bank companies'), 'the rest of the line is unchanged');
});

test('presidential-cycle factor (D-121, display only): the industry\'s own ratio from its audited workbooks stands beside the owner rule only when the catalog carries both totals; the rule itself is untouched', () => {
  const OWN = {dataset: 'Telecom', presidential_cycle: '2023-2024', presidential_total: 17978153.890000038, midterm_cycle: '2021-2022', midterm_total: 12463873.750000043, ratio: 1.4424210522832017};
  const GEN_LINE = 'Presidential cycle factor for general giving: ×1.86 (owner rule; OpenSecrets total election cost)';
  const OWN_LINE = 'Telecom\'s own giving: 2023-24 $17,978,154 ÷ 2021-22 $12,463,874 = ×1.44 (audited Mentat workbooks; display only, the forecast uses the owner rule)';
  const noteOf = own => {
    const {t, els} = cycleSession('telecom'), catalog = clone(CYCLE.catalog);
    catalog.anchors.industries.telecom = Object.assign({status: 'ready', workbook: 'Telecom Industry Spending Overview.xlsx', collection_date: '2026-09-26', coverage_end: '2026-06-30', current_cycle: 2026}, catalog.anchors.industries.telecom, own ? {own_cycle_ratio: own} : {});
    t.setCatalog(catalog); t.anchorNote(t.industry());
    return els['gf-anchors-note'].children.map(textOf);
  };
  const without = noteOf(null), withOwn = noteOf(OWN);
  assert.ok(without.includes(GEN_LINE) && !without.some(line => line.includes('own giving')), 'no totals, no line');
  assert.equal(withOwn.filter(line => line === OWN_LINE).length, 1);
  assert.equal(withOwn.indexOf(OWN_LINE), withOwn.indexOf(GEN_LINE) + 1, 'beside the owner rule');
  assert.deepEqual(withOwn.filter(line => line !== OWN_LINE), without, 'the rest of the note is unchanged');
  assert.ok(Math.abs(measure(CYC.telecom, 0, 'general_giving').model.presidential_uplift.factor - 12.56 / 6.74) < 1e-12, 'the forecast still takes the owner factor');
});

test('banks 2025-26 general giving (P1, observed $11.4M above the unconditioned $11.1M): the Methods table and the chart say what is drawn, not "already exceeds"; an answer without a layer keeps the server\'s text', () => {
  // The live recording no longer has this case (D-121: the unconditioned banks point rose from $11.1M to $12.5M, above the $11.4M observed). The page's wording for it stays covered by the previous
  // recording's own values for exactly those fields, laid over the current answer.
  const OLD = 'Observed giving already exceeds the projected cycle total';
  const NEW = 'The model\'s unconditioned total is below what has already been given, so the chart shows the total conditioned on observed giving (the model conditioned on the giving already observed).';
  const exceeded = clone(CYC.banks);
  Object.assign(measure(exceeded, 0, 'general_giving'), {projected_cycle_real_2025: 11095557.125896035, flags: ['observed_exceeds_unconditional_projection'], excess_observed: 257273.3606386222, projected_remaining: null,
    remainder_status: 'baseline_exceeded', remainder_reason: OLD + '. Revise the scenario; no zero or negative future-giving forecast is substituted.',
    remaining_coefficient_low: null, remaining_coefficient_high: null, remaining_prediction_low: null, remaining_prediction_high: null});
  const general = measure(exceeded, 0, 'general_giving');
  assert.equal(general.layer, 'P1'); assert.equal(general.remainder_status, 'baseline_exceeded'); assert.ok(general.remainder_reason.startsWith(OLD), 'the recorded server text is the unconditioned sentence');
  assert.ok(general.projected_cycle_real_2025 < general.observed.amount_real_2025, 'the unconditioned total is below what is observed');
  const {t, els} = cycleSession('commercial_banks'); t.buildInputs(); t.render(clone(exceeded));
  const table = textOf(els['gf-results']);
  assert.ok(table.includes(NEW), 'the Methods table'); assert.ok(!table.includes(OLD));
  const chart = JSON.stringify(t.chartModel(exceeded, exceeded, 'general'));
  assert.ok(!chart.includes('already exceeds'), 'no tooltip or flag of the conditioned chart prints it');
  // the earlier schema-2 answer has no layer: the server's text is printed as before
  const earlier = clone(exceeded); delete measure(earlier, 0, 'general_giving').layer;
  const old = cycleSession('commercial_banks'); old.t.buildInputs(); old.t.render(earlier);
  const oldText = textOf(old.els['gf-results']);
  assert.ok(oldText.includes(OLD) && !oldText.includes('unconditioned total is below'));
  // under the current answer (unconditioned total above what is observed) there is nothing to say
  assert.equal(measure(CYC.banks, 0, 'general_giving').remainder_status, 'ready');
  const live = cycleSession('commercial_banks'); live.t.buildInputs(); live.t.render(clone(CYC.banks));
  assert.ok(!textOf(live.els['gf-results']).includes('unconditioned total is below') && !textOf(live.els['gf-results']).includes(OLD));
});

test('under the cycle model every project request carries growth_basis "ibis_window_avg"; the earlier calculator\'s requests do not', async () => {
  const {t} = cycleSession('commercial_banks'); t.buildInputs();
  assert.equal(t.getRequest().growth_basis, 'ibis_window_avg');
  assert.equal(t.referenceRequest().growth_basis, 'ibis_window_avg');
  const queue = new Map(), requests = []; let next = 1;
  const reply = (path, init) => { requests.push(JSON.parse(init.body)); return Promise.resolve({ok: true, status: 200, headers: {get: () => 'application/json'}, json: () => Promise.resolve(CYC.banks)}); };
  const live = session({source: CYCLE, fetchImpl: reply, timers: {setTimeout: (fn, ms) => { queue.set(next, {fn, ms}); return next++; }, clearTimeout: id => queue.delete(id)}});
  live.t.buildInputs(); live.els['gf-form'].on.input();
  await [...queue.values()][0].fn();
  assert.equal(requests.length, 1); assert.equal(requests[0].growth_basis, 'ibis_window_avg'); assert.equal(requests[0].horizons[2026].growth_pct, '11.778');
  const old = session({cycle: false}); old.t.buildInputs();
  assert.equal('growth_basis' in old.t.getRequest(), false); assert.equal('growth_basis' in old.t.referenceRequest(), false);
});

test('the cycle model\'s reference request asks for the catalog growth defaults, not the anchor cycle\'s two-year growth (banks 48.885 would be read as an average annual growth)', () => {
  for (const [sector, [g26, g28]] of Object.entries({commercial_banks: ['11.778', '-1.848'], credit_unions: ['13.362', '1.665'], property_casualty: ['3.657', '1.832']})) {
    const {t} = session({sector, source: CYCLE}); t.buildInputs();
    t.getControls()[0].holder.value = '99';                                  // what the user typed never goes into the reference run either
    const body = t.referenceRequest(), anchorInputs = CYCLE.catalog.political_inputs.anchor_cycles['2024'];
    assert.deepEqual([body.horizons[2026].growth_pct, body.horizons[2028].growth_pct], [g26, g28], sector);
    assert.match(body.assumption_source, /^Reference levels only: the 2023–2024 control and the industry growth defaults, no scenario/);
    assert.equal(body.horizons[2026].control_scores.House, anchorInputs.House.p_rep * 100);        // the anchor shares come from the anchor cycle's own control
    assert.equal(body.horizons[2028].prior_growth_pct, '');
  }
  const history = CYCLE.catalog.industries.find(i => i.id === 'commercial_banks').history.find(r => r.cycle === 2024);
  assert.ok(history.growth_pct > 40, 'the number the old reference sent (' + history.growth_pct + ') is a two-year revenue growth');
});

test('under the cycle model the growth slider spans the default -10 to +10 points (never below -99.9) and names no fitted range; the earlier path keeps the fitted range and its warning', () => {
  for (const [sector, defaults] of Object.entries({commercial_banks: [11.778, -1.848], credit_unions: [13.362, 1.665], property_casualty: [3.657, 1.832]})) {
    const {t} = session({sector, source: CYCLE}); t.buildInputs();
    const [g26, g28] = t.getControls();
    [g26, g28].forEach((c, i) => {
      assert.ok(Math.abs(c.min - (defaults[i] - 10)) < 1e-9 && Math.abs(c.max - (defaults[i] + 10)) < 1e-9, `${sector} ${i}: ${c.min}..${c.max}`);
      t.refreshControls();
      assert.equal(c.slider.classList.contains('is-outside'), false, `${sector} ${i}: the default is inside its own range`);
      assert.doesNotMatch(c.hint.textContent, /fitted range/);
      c.holder.value = String(defaults[i] + 25); t.refreshControls();                      // typed beyond the slider: allowed, and nothing claims a fitted range
      assert.equal(c.slider.classList.contains('is-outside'), false); assert.doesNotMatch(c.hint.textContent, /fitted range/);
      assert.doesNotMatch(c.slider.attrs['aria-valuetext'], /fitted range/);
    });
  }
  const {t} = session({sector: 'property_casualty', source: CYCLE}); t.buildInputs();
  assert.equal(t.getControls()[1].holder.value, '1.832'); assert.equal(t.getControls()[1].hint.textContent.includes('Outside the fitted range'), false, 'the P&C 2027-28 default opens inside its range');
  // the floor: a default below -89.9 would put the lower end under -99.9
  const view = clone(CYCLE.catalog.cycle_model_view); view.growth.commercial_banks['2026'].growth_default = -95;
  const edge = session({source: CYCLE}); edge.t.setCatalog(Object.assign(clone(CYCLE.catalog), {cycle_model_view: view})); edge.t.buildInputs();
  assert.equal(edge.t.getControls()[0].min, -99.9); assert.equal(edge.t.getControls()[0].max, -85);
  // the earlier calculator: the fitted range of the industry, and the warning outside it
  const old = session({sector: 'property_casualty', cycle: false}); old.t.buildInputs();
  const [o26] = old.t.getControls(), sector = old.t.industry();
  assert.deepEqual([o26.min, o26.max], [sector.growth_min, sector.growth_max]);
  o26.holder.value = String(sector.growth_max + 5); old.t.refreshControls();
  assert.equal(o26.slider.classList.contains('is-outside'), true); assert.match(o26.hint.textContent, /^Outside the fitted range/);
});

test('copy under the cycle model: a blank 2027-28 prior growth says it uses the bundle\'s IBIS cycle growth (not the current-cycle growth above); the Methods fold names each measure\'s open-cycle layer', () => {
  const {t, els} = cycleSession('property_casualty'); t.buildInputs(); t.renderEvidence();
  const notes = textOf(els['gf-input-notes']);
  assert.doesNotMatch(notes, /current-cycle growth assumption above/);
  assert.match(notes, /Prior cycle: blank \(auto\) uses the previous completed cycle when available\./);
  assert.match(notes, /Prior cycle: blank \(auto\) uses the bundle's IBIS cycle growth \(4\.77%\) for 2025–2026; it feeds the party split\./);
  assert.match(els['gf-2028-prior'].attrs['aria-label'], /^Prior-cycle growth override, .*Blank \(auto\) uses the bundle's IBIS cycle growth \(4\.77%\)/);
  assert.match(els['gf-2026-prior'].attrs['aria-label'], /Blank \(auto\) uses the previous completed cycle when available\.$/);
  const methods = textOf(els['gf-evidence-content']);
  assert.match(methods, /Company PACs: panel nowcast \(R8\)\./);
  assert.doesNotMatch(methods, /Company PACs: open-cycle layer/);
  assert.match(methods, /Company PACs: Open cycle: the published PAC panel's nowcast \(R8\) O \+ \(1 - s\) B[^]*not a 95% interval[^]*does not affect company PACs/);
  assert.match(methods, /General giving: open-cycle layer P1, the model conditioned on the giving already observed\./);
  // the earlier path keeps its own wording, and has no cycle fold
  const old = session({cycle: false}); old.t.buildInputs(); old.t.renderEvidence();
  assert.match(textOf(old.els['gf-input-notes']), /Prior cycle: blank \(auto\) uses the current-cycle growth assumption above\./);
  assert.match(old.els['gf-2028-prior'].attrs['aria-label'], /Blank uses the current-cycle growth assumption above\.$/);
  assert.doesNotMatch(textOf(old.els['gf-evidence-content']), /open-cycle layer/);
});

test('the historical example fills the controls and the request with the anchor cycle', () => {
  const {t} = session();
  t.buildInputs(); t.historicalExample();
  const request = t.getRequest();
  const history = FIXTURE.catalog.industries.find(i => i.id === 'commercial_banks').history.find(r => r.cycle === 2024);
  assert.equal(Number(request.horizons[2026].growth_pct), history.growth_pct);
  assert.equal(Number(request.horizons[2028].control_scores.Senate), FIXTURE.catalog.political_inputs.anchor_cycles['2024'].Senate.p_rep * 100);
  assert.equal(request.horizons[2026].current_control.House, 1);
  assert.equal(request.horizons[2026].current_control.Senate, 0);
  assert.equal(request.horizons[2026].prior_growth_pct, '', 'the prior-cycle growth stays blank: the server fills it');
  assert.doesNotMatch(request.assumption_source, /no change from the Mentat previous cycle/);
});

// The example sentence and the texts beside the marks follow what is DRAWN (owner review 2026-10-01): the model's own change is the unconditioned one, so
// where the model fix conditions the open cycle on the giving already observed, every text that quotes a change either says it is the model's change before
// conditioning or prints the change actually drawn. These helpers format like the page does (number() / signed() / percent()).
const num = value => (Math.abs(value) < 0.005 ? 0 : value).toLocaleString('en-US', {maximumFractionDigits: 2});
const sgn = value => (value >= 0.005 ? '+' : '') + num(value);
const pctTxt = value => num(value * 100) + '%';
// the change between two shares in points, from the shares as displayed (two decimals): 51.44% to 48.17% is -3.27 pp, as the numbers beside it read
const shownPp = (from, to) => Number(num(to * 100)) - Number(num(from * 100));
const EXAMPLE_HEAD = "The model's change in the amounts is zero (growth and control as in 2023–2024).";
const EXAMPLE_MIXES = "Party mixes also move: 2026 is a midterm, and the blank prior-cycle growth uses 2024's growth, which is outside the range the share models were fitted on.";
const EXAMPLE_UNKNOWN = `${EXAMPLE_HEAD} The model's projected amounts equal the 2023–2024 Mentat level before any conditioning on the giving already observed. ${EXAMPLE_MIXES}`;
const EXAMPLE_PLAIN = `${EXAMPLE_HEAD} The 2025–2026 amounts equal the 2023–2024 Mentat level. ${EXAMPLE_MIXES}`;
const EXAMPLE_CONDITIONED = `${EXAMPLE_HEAD} The 2025–2026 amounts differ from the 2023–2024 Mentat level only because they are conditioned on the giving already observed. ${EXAMPLE_MIXES}`;
const THREE_PATHS = [['without the model fix', RUN, FIXTURE], ['with the model fix P1', P1, FIXTURE], ['the live calculator', LANDED.runs, LANDED]];
const SECTORS = {banks: 'commercial_banks', credit_unions: 'credit_unions', property_casualty: 'property_casualty'};

test('the historical-example message says only what is true: the model\'s change is zero, and the drawn amounts equal the Mentat level only where nothing conditions them', () => {
  const {t, els} = session();
  assert.equal(t.conditioning(), null);
  assert.equal(t.exampleText(), EXAMPLE_UNKNOWN, 'no run yet: only the model\'s own amounts are claimed to equal the level, before any conditioning (true on both paths)');
  t.buildInputs(); t.historicalExample();
  assert.equal(els['gf-status'].textContent, 'Anchor-cycle values loaded as an example: growth and control as in 2023–2024. ' + EXAMPLE_UNKNOWN + ' Change them to explore.');
  assert.doesNotMatch(els['gf-status'].textContent, /so the projection equals/);
  assert.equal(els['gf-example-note'].textContent, EXAMPLE_UNKNOWN);
  assert.equal(els['gf-example-pill'].textContent, 'Historical example: 2023–2024 growth and control');
  // the run the message describes arrives after the example was loaded: the texts that carry it follow (the status line only while it still shows the example message)
  t.setRuns(null, P1.banks); t.refreshExample();
  assert.equal(els['gf-example-note'].textContent, EXAMPLE_CONDITIONED);
  assert.equal(els['gf-example-pill'].dataset.note, EXAMPLE_CONDITIONED);
  assert.equal(els['gf-status'].textContent, 'Anchor-cycle values loaded as an example: growth and control as in 2023–2024. ' + EXAMPLE_CONDITIONED + ' Change them to explore.');
  els['gf-status'].textContent = 'Something else the page said meanwhile.';
  t.setRuns(null, RUN.banks); t.refreshExample();
  assert.equal(els['gf-example-note'].textContent, EXAMPLE_PLAIN);
  assert.equal(els['gf-status'].textContent, 'Something else the page said meanwhile.', 'a status line that no longer shows the example message is left alone');
  // each state, from the run that is known
  assert.equal(t.conditioning(), false); assert.equal(t.exampleText(), EXAMPLE_PLAIN);
  t.setRuns(null, P1.banks);
  assert.equal(t.conditioning(), true); assert.equal(t.exampleText(), EXAMPLE_CONDITIONED);
  t.setRuns(P1.banks, null);
  assert.equal(t.exampleText(), EXAMPLE_CONDITIONED, 'the calculation counts as much as the reference levels');
});

test('the example message agrees with the amounts drawn beside it: the Mentat level only where the drawn total equals it, "conditioned" where it differs (all three calculators)', () => {
  let checked = 0;
  for (const [pathName, runs, source] of THREE_PATHS) {
    for (const name of Object.keys(SECTORS)) {
      const run = runs[name], {t} = session({sector: SECTORS[name], source});
      t.setRuns(run, run);
      const sentence = t.exampleText();
      assert.doesNotMatch(sentence, /so the projection equals/, 'the false sentence is gone');
      for (const account of ['pac', 'general']) {
        const model = t.chartModel(run, run, account);
        if (model.empty) continue;
        const cur = model.columns[1];
        if (cur.state !== 'stacked' && cur.state !== 'conflict') continue;
        const level = model.columns[0].value, equal = Math.abs(cur.total - level) < 0.5;     // the total drawn for 2025-2026 against the 2023-2024 Mentat level, to the cent
        checked++;
        if (pathName === 'without the model fix') {
          assert.ok(equal, `${pathName}/${name}/${account}: the model's projection equals the level (${cur.total} vs ${level})`);
          assert.match(sentence, /The 2025–2026 amounts equal the 2023–2024 Mentat level\./);
        } else {
          assert.ok(!equal, `${pathName}/${name}/${account}: the conditioned total differs from the level (${cur.total} vs ${level}); the message must not say it equals it`);
          assert.match(sentence, /The 2025–2026 amounts differ from the 2023–2024 Mentat level only because they are conditioned on the giving already observed\./);
          assert.doesNotMatch(sentence, /amounts equal/);
        }
      }
    }
  }
  assert.ok(checked >= 15, 'the comparison covered ' + checked + ' drawn totals');
  // the review's numbers (live calculator, banks): the PAC column draws about +2.9% over the level and the general-giving column about +0.06%
  const live = session({source: LANDED}).t;
  const pac = live.chartModel(LANDED.runs.banks, null, 'pac').columns, general = live.chartModel(LANDED.runs.banks, null, 'general').columns;
  assert.ok(pac[1].total > pac[0].value * 1.02, 'PAC: ' + pac[1].total + ' against ' + pac[0].value);
  assert.ok(general[1].total > general[0].value && general[1].total < general[0].value * 1.01, 'general giving: ' + general[1].total + ' against ' + general[0].value);
});

test('the example message says only what is true of the industry: each clause follows the catalog', () => {
  // growth inside the fitted range: the clause stays but no longer says "outside"
  const inside = session();
  const catalog = clone(FIXTURE.catalog);
  for (const m of Object.values(catalog.share_models)) m.history.filter(r => r.industry === 'commercial_banks').forEach((r, i) => { r.prior_growth_pct = i % 2 ? 60 : 0; });
  inside.t.setCatalog(catalog);
  assert.match(inside.t.exampleText(), /the blank prior-cycle growth uses 2024's growth\.$/);
  assert.doesNotMatch(inside.t.exampleText(), /outside the range/);
  // a chamber anchored on the same phase: the midterm effect is zero for it, and the sentence names the chamber's own anchor
  const same = session();
  const c2 = clone(FIXTURE.catalog);
  for (const m of Object.values(c2.share_models)) m.anchors.commercial_banks.anchor = 'same_phase';
  same.t.setCatalog(c2);
  assert.doesNotMatch(same.t.exampleText(), /midterm/);
  assert.match(same.t.exampleText(), /The 2026 House and Senate candidate mix is anchored on Mentat 2021–2022, the cycle of the same phase\.$/);
  // only the House model carries the presidential/midterm term (the Senate model has none): a same-phase House anchor removes the clause, a same-phase Senate anchor does not
  assert.equal(FIXTURE.catalog.share_models.control_House.terms.includes('Pres'), true);
  assert.equal(FIXTURE.catalog.share_models.control_Senate.terms.includes('Pres'), false);
  const onlyHouse = session(), c3 = clone(FIXTURE.catalog);
  c3.share_models.control_House.anchors.commercial_banks.anchor = 'same_phase';
  onlyHouse.t.setCatalog(c3);
  assert.doesNotMatch(onlyHouse.t.exampleText(), /midterm/, 'the House model, the only one with the term, is anchored on the same phase');
  assert.match(onlyHouse.t.exampleText(), /The 2026 House candidate mix is anchored on Mentat 2021–2022/);
  const onlySenate = session(), c4 = clone(FIXTURE.catalog);
  c4.share_models.control_Senate.anchors.commercial_banks.anchor = 'same_phase';
  onlySenate.t.setCatalog(c4);
  assert.match(onlySenate.t.exampleText(), /2026 is a midterm/, 'the House model still carries the cycle');
});

test('the projection tooltip agrees with the column it sits on: the change against the Mentat level is the one drawn, and the model\'s own change says what it is (all three calculators)', () => {
  let checked = 0;
  for (const [pathName, runs, source] of THREE_PATHS) for (const name of Object.keys(SECTORS)) for (const account of ['pac', 'general']) {
    const run = runs[name], {t} = session({sector: SECTORS[name], source}), model = t.chartModel(run, null, account);
    if (model.empty) continue;
    const cur = model.columns[1];
    if (cur.state !== 'stacked' && cur.state !== 'conflict') continue;
    const level = model.columns[0].value, drawn = (cur.total / level - 1) * 100, where = `${pathName}/${name}/${account}`;
    checked++;
    if (cur.conditioned) {
      assert.deepEqual(plain(row(cur.tip, 'Change vs Mentat 2023–2024, drawn (conditioned)')), ['Change vs Mentat 2023–2024, drawn (conditioned)', sgn(drawn) + '%'], where);
      const own = row(cur.tip, 'Model change before conditioning on the giving already observed');
      assert.ok(own && /^[+-]?[\d.,]+% \(previous cycle\)$/.test(own[1]), where + ': ' + JSON.stringify(own));
      assert.equal(row(cur.tip, 'Change vs Mentat 2023–2024'), undefined, where + ': the unlabelled model change is not printed beside a conditioned total');
      assert.ok(row(cur.tip, 'Coefficient interval (before conditioning)'), where);
    } else {
      const same = row(cur.tip, 'Change vs Mentat 2023–2024');
      assert.ok(same, where);
      assert.ok(Math.abs(parseFloat(same[1].replace(/,/g, '')) - drawn) < 0.01, `${where}: the change printed (${same[1]}) is the change drawn (${drawn})`);
    }
  }
  assert.ok(checked >= 15, 'the comparison covered ' + checked + ' drawn columns');
  // the review's case: the PAC column of banks on the live calculator draws about +2.9% over the Mentat level while the model's own change is 0%
  const pac = session({source: LANDED}).t.chartModel(LANDED.runs.banks, null, 'pac').columns[1];
  assert.match(row(pac.tip, 'Change vs Mentat 2023–2024, drawn (conditioned)')[1], /^\+2\.9\d?%$/);
  assert.equal(row(pac.tip, 'Model change before conditioning on the giving already observed')[1], '0% (previous cycle)');
});

test('the candidate-mix bar tooltip agrees with the bar: the change in share is the one drawn, and the model\'s own change says what it is (all three calculators)', () => {
  let checked = 0;
  for (const [pathName, runs, source] of THREE_PATHS) for (const name of Object.keys(SECTORS)) for (const chamber of ['House', 'Senate']) {
    const run = runs[name], {t} = session({sector: SECTORS[name], source}), mix = t.mixModel(run, null, chamber), s0 = run.horizons[0].shares[chamber];
    for (const r of mix.rows) {
      if (r.state !== 'ready' || r.anchorShare == null) continue;
      const drawn = shownPp(r.anchorShare, r.share), where = `${pathName}/${name}/${chamber}/${r.label}`;
      checked++;
      if (r.conditioned) {
        assert.deepEqual(plain(row(r.tip, 'Change in Republican share, drawn (conditioned)')), ['Change in Republican share, drawn (conditioned)', sgn(drawn) + ' pp'], where);
        assert.deepEqual(plain(row(r.tip, 'Model change before conditioning on the giving already observed')), ['Model change before conditioning on the giving already observed', sgn(s0.change_pp) + ' pp on each basis'], where);
        assert.equal(row(r.tip, 'Change in Republican share'), undefined, where + ': the unlabelled model change is not printed beside a conditioned bar');
        if (s0.change_by_term_pp) assert.ok(row(r.tip, 'Model change by input, before conditioning on the giving already observed'), where);
      } else {
        const own = row(r.tip, 'Change in Republican share');
        assert.ok(own && Math.abs(parseFloat(own[1]) - drawn) < 0.011, `${where}: the change printed (${own && own[1]}) is the change drawn (${drawn.toFixed(3)})`);
      }
    }
  }
  assert.ok(checked >= 20, 'the comparison covered ' + checked + ' drawn bars');
  // the review's cases on the live calculator: banks House employees draw 29.56% against 34.35% (-4.79 pp, where the model says -1.63 pp); the Senate bars move
  // 51.44% -> 48.17% and 64.5% -> 75.76% (the model says +6.06 pp)
  const live = session({source: LANDED}).t;
  const house = live.mixModel(LANDED.runs.banks, null, 'House').rows[0], senate = live.mixModel(LANDED.runs.banks, null, 'Senate').rows;
  assert.equal(row(house.tip, 'Change in Republican share, drawn (conditioned)')[1], '-4.79 pp');
  assert.equal(row(house.tip, 'Model change before conditioning on the giving already observed')[1], '-1.63 pp on each basis');
  assert.deepEqual(plain(senate.map(r => [pctTxt(r.anchorShare), pctTxt(r.share)])), [['51.44%', '48.17%'], ['64.5%', '75.76%']]);
  assert.equal(row(senate[0].tip, 'Model change before conditioning on the giving already observed')[1], '+6.06 pp on each basis');
});

test('the scope pill tooltip agrees with the bars: the model\'s change is labelled before conditioning, and once calculated the change drawn per basis is printed beside it', () => {
  const rowsOf = lines => lines.filter(line => line.row).map(line => line.row);
  for (const [pathName, runs, source] of THREE_PATHS.slice(1)) {
    const run = runs.banks, {t} = session({source});
    t.setRuns(null, run);                                          // before Calculate: the reference levels only, nothing is drawn yet
    const before = rowsOf(t.exampleTip());
    for (const chamber of ['House', 'Senate']) {
      const model = before.find(r => r[0] === `${chamber} change in R share, model, before conditioning on the giving already observed`);
      assert.ok(model, `${pathName}/${chamber}`);
      assert.equal(model[1].split(' · ')[0], sgn(run.horizons[0].shares[chamber].change_pp) + ' pp');
    }
    assert.equal(before.some(r => /drawn/.test(r[0])), false, pathName + ': nothing is drawn before Calculate, so nothing claims to be');
    t.setRuns(run, run);                                           // calculated
    const after = rowsOf(t.exampleTip());
    for (const chamber of ['House', 'Senate']) {
      const drawn = after.find(r => r[0] === `${chamber} change in R share, drawn (conditioned)`), bars = t.mixModel(run, run, chamber).rows.filter(r => r.state === 'ready');
      assert.ok(drawn, `${pathName}/${chamber}`);
      assert.equal(drawn[1], bars.map(r => `${r.label} ${pctTxt(r.anchorShare)} to ${pctTxt(r.share)} (${sgn(shownPp(r.anchorShare, r.share))} pp)`).join(' · '), pathName + '/' + chamber + ': the bars drawn');
    }
  }
  // the review's case on the live calculator
  const live = session({source: LANDED}).t;
  live.setRuns(LANDED.runs.banks, LANDED.runs.banks);
  const senate = rowsOf(live.exampleTip()).find(r => r[0] === 'Senate change in R share, drawn (conditioned)');
  assert.equal(senate[1], 'Employees 51.44% to 48.17% (-3.27 pp) · Company PACs 64.5% to 75.76% (+11.26 pp)');
  assert.match(rowsOf(live.exampleTip()).find(r => r[0] === 'Senate change in R share, model, before conditioning on the giving already observed')[1], /^\+6\.06 pp · prior-cycle growth \+6\.06 pp$/);
  // a calculator without the fix names no input and conditions nothing: no change rows at all
  const plainTip = session().t; plainTip.setRuns(RUN.banks, RUN.banks);
  assert.deepEqual(plain(rowsOf(plainTip.exampleTip())), []);
});

test('the lobbying tooltip agrees with the bar it sits on: the change against the base level is the one drawn', () => {
  let checked = 0;
  for (const [pathName, runs, source] of THREE_PATHS) {
    const {t} = session({sector: 'commercial_banks', source}), model = t.lobbyModel(runs.banks_lobby, 2026, 2028), done = model.bars[4];
    for (const bar of model.bars.filter(b => b.kind === 'projected')) {
      for (const [key, base] of [['hired', done.hired], ['inhouse', done.inhouse]]) {
        const drawn = (bar[key] / base - 1) * 100, where = `${pathName}/${key}/${bar.year}`, change = /Change vs the 2025 level: ([-+]?[\d.,]+)%/.exec(bar.tips[key]);
        checked++;
        assert.ok(change && Math.abs(parseFloat(change[1].replace(/,/g, '')) - drawn) < 0.01, `${where}: ${change && change[1]} against ${drawn}`);
        assert.ok(/\n80% band: /.test(bar.tips[key]) && /\n~95% band \(largest blind miss\): /.test(bar.tips[key]), where + ': both bands are in the tooltip');
      }
    }
  }
  assert.ok(checked >= 6, 'the comparison covered ' + checked + ' drawn segments');
});

const WIDE_TEXT = 'Share interval extends outside 0-100% (linear share model on a thin base); treat it as unreliable.';
test('a mix row whose drawn share interval leaves 0-100% gets an amber chip and an amber outline and is not clipped (credit unions on the live calculator and on the others)', () => {
  for (const [pathName, runs, source] of THREE_PATHS) {
    const {t} = session({sector: 'credit_unions', source}), senate = t.mixModel(runs.credit_unions, null, 'Senate');
    const wide = senate.rows.filter(r => r.state === 'ready' && (r.low < 0 || r.high > 1));
    assert.ok(wide.length >= 1, `${pathName}: a Senate row of the credit-union card has an interval outside 0-100%`);
    const chips = senate.chips.filter(c => c.text === WIDE_TEXT);
    assert.equal(chips.length, 1, pathName + ': one amber chip however many rows');
    assert.equal(chips[0].kind, AMBER);
    assert.equal(senate.chips[0].text, WIDE_TEXT, pathName + ': amber chips come first');
    assert.deepEqual(plain(senate.rows.filter(r => r.wide).map(r => r.label)), plain(wide.map(r => r.label)));
    for (const r of wide) {
      assert.match(texts(chips[0].tip), new RegExp(r.label + ': .*' + (r.conditioned ? 'implied full-cycle share, 95% interval' : '95% prediction interval')));
      assert.ok(texts(chips[0].tip).includes(`${pctTxt(r.low)} to ${pctTxt(r.high)}`), pathName + ': the interval is printed as returned (' + pctTxt(r.low) + ' to ' + pctTxt(r.high) + ')');
      assert.match(r.aria, /Share interval extends outside 0-100%/);
      if (r.conditioned) assert.equal(r.flagged, false, 'the budget state is a different flag');
    }
    // not clipped: the interval is the model's own
    const base = runs.credit_unions.horizons[0].shares.Senate.bases.find(b => b.id === 'pac_candidate'), alloc = base.remaining_allocation;
    const own = senate.rows.find(r => r.id === 'pac_candidate');
    assert.equal(own.low, alloc ? alloc.implied_cycle_R_share_prediction_low : base.cycle_prediction_low);
  }
  // the live calculator's numbers (the review): -1.89% to 4.69%
  const live = session({sector: 'credit_unions', source: LANDED}).t.mixModel(LANDED.runs.credit_unions, null, 'Senate').rows.find(r => r.id === 'pac_candidate');
  assert.equal(`${pctTxt(live.low)} to ${pctTxt(live.high)}`, '-1.89% to 4.69%');
  // banks: no row leaves the range, so no chip; a synthetic row above 100% gets one
  for (const [pathName, runs, source] of THREE_PATHS) for (const chamber of ['House', 'Senate']) {
    assert.deepEqual(plain(session({source}).t.mixModel(runs.banks, null, chamber).chips.filter(c => c.text === WIDE_TEXT)), [], `${pathName}/banks/${chamber}`);
  }
  const run = clone(P1.banks), alloc = run.horizons[0].shares.House.bases[0].remaining_allocation;
  alloc.implied_cycle_R_share_prediction_high = 1.2;
  const synthetic = session().t.mixModel(run, null, 'House');
  assert.equal(synthetic.chips.filter(c => c.text === WIDE_TEXT).length, 1);
  assert.deepEqual(plain(synthetic.rows.map(r => r.wide)), [true, false]);
});

test('the 2026 control rows open filled from the saved outlook and say where the numbers come from; every other input opens blank (revenue growth opens at its IBIS default)', () => {
  for (const [name, source] of [['the published catalog', FIXTURE], ['the live catalog', LANDED]]) {
    const {t} = session({source}), snapshot = source.catalog.saved_outlook;
    t.buildInputs();
    const [g26, g28, h26, s26, h28, s28] = t.getControls(), [ph26, ps26, ph28, ps28] = t.getPills();
    assert.equal(Number(h26.holder.value), snapshot.control.House.value, name);
    assert.equal(Number(s26.holder.value), snapshot.control.Senate.value, name);
    assert.deepEqual([ph26.value, ps26.value].map(String), [String(snapshot.control.House.current_control), String(snapshot.control.Senate.current_control)]);
    assert.deepEqual([g26, g28].map(c => c.holder.value), ['11.778', '-1.848'], name + ': growth opens at the banks default');
    for (const c of [g26, g28, h28, s28]) assert.equal(c.saved, undefined, name + ': no saved outlook behind it');
    for (const c of [h28, s28]) assert.equal(c.holder.value, '', name + ': blank');
    assert.deepEqual([ph28.value, ps28.value].map(String), ['', ''], name + ': the 2027-2028 pills are unset');
    // the source is said beside each row, from the catalog
    const text = 'Polymarket, Jan–Jun 2026';
    for (const c of [h26, s26]) {
      assert.equal(c.saved.source.children[0].textContent, text, name);
      assert.equal(c.saved.source.title, c.saved.title);
      assert.ok(c.saved.title.startsWith(`Saved ${snapshot.cycle} outlook, as of ${snapshot.control.House.as_of}: Polymarket mean of the daily probability of Republican control from ${snapshot.control.House.window.start} to ${snapshot.control.House.window.end}.`), name);
    }
    t.refreshControls();
    assert.deepEqual([h26, s26].map(c => c.saved.source.classList.contains('is-quiet')), [false, false], 'shown while the rows hold the saved values');
    assert.deepEqual([h26, s26].map(c => c.slider.classList.contains('is-unset')), [false, false]);
    // the request on open: the saved scores and pills for 2026 only
    const request = t.getRequest();
    assert.equal(Number(request.horizons[2026].control_scores.House), snapshot.control.House.value);
    assert.equal(String(request.horizons[2026].current_control.Senate), String(snapshot.control.Senate.current_control));
    assert.deepEqual({...request.horizons[2028].control_scores}, {House: '', Senate: ''});
    assert.equal(request.horizons[2026].growth_pct, '11.778');
    // typing another score, or changing the pill, ends the claim for that row only; typing the saved score back restores it
    h26.holder.value = '30'; t.refreshControls();
    assert.deepEqual([h26, s26].map(c => c.saved.source.classList.contains('is-quiet')), [true, false]);
    assert.equal(h26.saved.clear.classList.contains('is-quiet'), true);
    h26.holder.value = String(snapshot.control.House.value); t.refreshControls();
    assert.equal(h26.saved.source.classList.contains('is-quiet'), false);
    ps26.value = 0; t.refreshControls();
    assert.deepEqual([h26, s26].map(c => c.saved.source.classList.contains('is-quiet')), [false, true]);
    ps26.value = snapshot.control.Senate.current_control; t.refreshControls();
    // clearing the House row puts the score and the pill back to the real unset state: nothing is sent for them, the Senate row is untouched
    t.clearSaved(h26);
    assert.equal(h26.holder.value, ''); assert.equal(ph26.value, '');
    assert.equal(h26.slider.classList.contains('is-unset'), true); assert.equal(h26.saved.source.classList.contains('is-quiet'), true); assert.equal(h26.saved.clear.classList.contains('is-quiet'), true);
    const cleared = t.getRequest();
    assert.equal(cleared.horizons[2026].control_scores.House, ''); assert.equal(cleared.horizons[2026].current_control.House, '');
    assert.equal(Number(cleared.horizons[2026].control_scores.Senate), snapshot.control.Senate.value, 'the other row is untouched');
    assert.equal(s26.saved.source.classList.contains('is-quiet'), false);
    t.clearSaved(s26);
    const none = t.getRequest();
    assert.deepEqual({...none.horizons[2026].control_scores}, {House: '', Senate: ''}); assert.deepEqual({...none.horizons[2026].current_control}, {House: '', Senate: ''});
  }
  // a catalog without a saved outlook, or one for another cycle: every row opens blank and no caption exists
  for (const strip of [catalog => { delete catalog.saved_outlook; }, catalog => { catalog.saved_outlook.cycle = 2024; }]) {
    const {t} = session(), catalog = clone(FIXTURE.catalog);
    strip(catalog); t.setCatalog(catalog); t.buildInputs();
    assert.ok(t.getControls().every((c, i) => c.saved === undefined && (c.holder.value === '') === ![0, 1].includes(i)), 'only the two growth rows open filled');
  }
});

test('a refusal or a missing run draws nothing and invents nothing', () => {
  const {t} = session();
  assert.deepEqual(plain(t.chartModel({status: 'anchor_stale', reason: 'old'}, null, 'general')), {empty: true, account: 'general', message: ''});
  assert.deepEqual(plain(t.mixModel({status: 'anchor_stale'}, null, 'House').rows), []);
  assert.equal(t.lobbyModel({status: 'anchor_stale'}, 2026, 2028).state, 'empty');
});

// ---- board A: the fixed axis, the rail's Reset, the party split and the warnings text -------------------------------------------------------------------------------------------

test('the axis top is sticky per industry, account and as-of date: it only grows while those stay the same, starts over when one changes, and never clips an interval', () => {
  const {t, els} = session();
  const small = () => t.chartModel(null, RUN.banks, 'general').axisMax, big = () => t.chartModel(RUN.banks, null, 'general').axisMax;
  const first = small(), grown = big();
  assert.ok(grown > first * 1.5, 'a calculation with an interval reaches well above the reference levels');
  assert.equal(small(), grown, 'the same industry, account and date: the top never comes back down (the root cause of "moving an assumption has no effect": the scale followed the data)');
  assert.equal(t.chartModel(RUN.banks_blank, RUN.banks, 'general').axisMax, grown);
  const model = t.chartModel(RUN.banks, null, 'general'), values = [model.columns[0].value, model.columns[1].total, model.columns[1].high, model.columns[2].total, model.columns[2].high];
  assert.ok(t.niceScale(model.axisMax).top >= Math.max(...values), 'every interval end fits under the axis top');
  // the other account has its own top; the unchanged account is not affected by it
  const pac = t.chartModel(RUN.banks, null, 'pac').axisMax;
  assert.ok(pac < grown / 3);
  assert.equal(small(), grown);
  // a different as-of date starts over (the date of the run being drawn, not the form's), and so does a different industry
  const later = clone(RUN.banks); later.as_of = '2026-10-02';
  assert.equal(t.chartModel(null, later, 'general').axisMax, first, 'a run for a new as-of date resets the top');
  assert.equal(big(), grown, 'and the old date starts over again too: one key per account');
  const cuSmall = t.chartModel(null, RUN.credit_unions, 'general').axisMax;
  assert.ok(cuSmall < grown, 'a different industry starts over: the banks top is gone');
  assert.equal(t.chartModel(null, RUN.credit_unions, 'general').axisMax, cuSmall);
  // the lobbying chart takes its axis the same way
  const lobby = t.lobbyModel(RUN.banks_lobby, 2026, 2028).axisMax;
  assert.equal(t.lobbyModel(RUN.banks_lobby, 2026, 2028).axisMax, lobby);
  assert.equal(els['gf-industry'].value, 'commercial_banks', 'the form was never consulted');
});

test('the sticky axis is keyed on the run being drawn: the form names the new industry while the old industry\'s run is still on screen, and its scale must not carry over', () => {
  for (const to of ['credit_unions', 'property_casualty']) for (const account of ['general', 'pac']) {
    const {t, els} = session();
    const own = session().t.chartModel(null, RUN[to], account).axisMax;     // the new industry's levels on a fresh scale
    const stale = t.chartModel(RUN.banks, null, account).axisMax;
    assert.notEqual(own, stale, `${to} ${account}: the two scales differ, so the test can tell them apart`);
    // what the page did: the form changes first, the old run is repainted once with the new form value, then the new industry's levels arrive
    els['gf-industry'].value = to;
    t.chartModel(RUN.banks, null, account);
    assert.equal(t.chartModel(null, RUN[to], account).axisMax, own, `${to} ${account}: drawn on its own scale, not on the banks one`);
    assert.equal(t.niceScale(t.chartModel(RUN[to], null, account).axisMax).top >= t.niceScale(own).top, true, 'and it only grows from there');
  }
  // the lobbying chart too
  const {t, els} = session(), fresh = session().t.lobbyModel(RUN.credit_unions_lobby, 2026, 2028).axisMax;
  t.lobbyModel(RUN.banks_lobby, 2026, 2028); els['gf-industry'].value = 'credit_unions'; t.lobbyModel(RUN.banks_lobby, 2026, 2028);
  assert.equal(t.lobbyModel(RUN.credit_unions_lobby, 2026, 2028).axisMax, fresh, 'credit unions have their own lobbying scale');
});

test('Reset to defaults puts EVERYTHING back: growth defaults, the saved outlook (both pills), and every input that opens blank (prior-cycle growth, the 2027-28 scores and selects, lobbying by year); one recalculation', () => {
  const queue = new Map(); let next = 1;
  const {t, els} = session({timers: {setTimeout: (fn, ms) => { queue.set(next, {fn, ms}); return next++; }, clearTimeout: id => queue.delete(id)}});
  t.buildInputs();
  const [g26, g28, h26, s26, h28, s28] = t.getControls(), [ph26, ps26, ph28, ps28] = t.getPills(), saved = FIXTURE.catalog.saved_outlook.control;
  g26.holder.value = '9'; g28.holder.value = ''; h26.holder.value = '30'; ph26.value = '0'; s26.holder.value = ''; ps26.value = ''; h28.holder.value = '44'; s28.holder.value = '55'; ph28.value = '1'; ps28.value = '0';
  els['gf-2026-prior'].value = '4'; els['gf-2028-prior'].value = '-2';
  for (const id of ['gf-2026-lobby', 'gf-2027-lobby', 'gf-2028-lobby']) els[id].value = '1.5';
  t.refreshControls();
  t.resetDefaults();
  assert.deepEqual(plain([g26, g28].map(c => c.holder.value)), ['11.778', '-1.848']);
  assert.equal(Number(h26.holder.value), saved.House.value); assert.equal(Number(s26.holder.value), saved.Senate.value);
  assert.deepEqual([ph26.value, ps26.value].map(String), [String(saved.House.current_control), String(saved.Senate.current_control)]);
  assert.deepEqual(plain([h28, s28].map(c => c.holder.value)), ['', ''], 'the 2027-2028 control scores go back to blank');
  assert.deepEqual([ph28.value, ps28.value], ['', ''], 'and so do both of its control-before-election selects');
  assert.deepEqual(['gf-2026-prior', 'gf-2028-prior'].map(id => els[id].value), ['', ''], 'prior-cycle growth is blank again');
  assert.deepEqual(['gf-2026-lobby', 'gf-2027-lobby', 'gf-2028-lobby'].map(id => els[id].value), ['', '', ''], 'so is lobbying by year');
  const request = t.getRequest();
  assert.equal(request.horizons[2026].prior_growth_pct, ''); assert.equal(request.horizons[2028].control_scores.House, ''); assert.equal(request.horizons[2028].current_control.Senate, '');
  assert.deepEqual({...request.horizons[2028].annual_forecast_changes}, {2027: '', 2028: ''});
  assert.equal(g26.view.value, '11.78', 'the visible numbers follow (two decimals; the request keeps the exact default)');
  assert.equal(g26.dflt.reset.hidden, true);
  assert.equal(queue.size, 1, 'one recalculation is queued'); assert.equal([...queue.values()][0].ms, 300);
  // an industry without a growth default and a catalog without a saved outlook: the same rows reset to blank
  const bare = session({outlook: false, timers: {setTimeout: () => 1, clearTimeout() {}}});
  bare.t.buildInputs();
  const rows = bare.t.getControls();
  rows.forEach(c => { c.holder.value = '7'; }); bare.t.getPills().forEach(select => { select.value = '1'; });
  bare.t.resetDefaults();
  assert.deepEqual(plain(rows.map(c => c.holder.value)), ['11.778', '-1.848', '', '', '', ''], 'only the growth defaults come back');
  assert.deepEqual(plain(bare.t.getPills().map(select => select.value)), ['', '', '', '']);
});

test('the form-wide input listener ignores the industry, the date and the assumption-source fields and the published PAC panels; any other input recalculates after one pause', () => {
  const queue = new Map(); let next = 1;
  const {els} = session({timers: {setTimeout: (fn, ms) => { queue.set(next, {fn, ms}); return next++; }, clearTimeout: id => queue.delete(id)}});
  const listener = els['gf-form'].on.input;
  for (const id of ['gf-industry', 'gf-date', 'gf-source', 'gf-source-date']) { listener({target: {id, closest: () => null}}); assert.equal(queue.size, 0, id + ' schedules nothing'); }
  listener({target: {id: 'pac-political-variant', closest: selector => selector === '.pac-selected' ? {} : null}}); assert.equal(queue.size, 0, 'the published PAC panels schedule nothing');
  listener({target: {id: 'gf-2026-growth', closest: () => null}}); listener({target: {id: 'gf-2026-House-political', closest: () => null}});
  assert.equal(queue.size, 1, 'a scenario input schedules one timer, however many edits'); assert.equal([...queue.values()][0].ms, 300);
});

test('calculate() refuses silently, without a request, when the as-of date is not valid; with a valid date it asks once', async () => {
  const requests = [];
  const reply = (path, init) => { requests.push(JSON.parse(init.body)); return Promise.resolve({ok: true, status: 200, headers: {get: () => 'application/json'}, json: () => Promise.resolve(RUN.banks)}); };
  const {t, els} = session({fetchImpl: reply});
  t.buildInputs();
  els['gf-date'].validity = {valid: false};
  els['gf-alert'] = Object.assign(el('div'), {hidden: true}); els['gf-status'] = el('div');
  const status = els['gf-status'].textContent;
  await t.calculate({preventDefault() { this.prevented = true; }});
  await t.calculate();
  assert.equal(requests.length, 0, 'no request leaves for an incomplete or out-of-range date');
  assert.equal(els['gf-alert'].hidden, true, 'and nothing is said about it'); assert.equal(els['gf-status'].textContent, status);
  els['gf-date'].validity = {valid: true};
  await t.calculate();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].as_of, '2026-09-30');
});

test('a failed calculation says so in the alert with a "Try again" button that runs it again, once; the copy no longer tells anyone to calculate', async () => {
  let failing = true; const requests = [];
  const reply = (path, init) => { requests.push(JSON.parse(init.body)); return Promise.resolve(failing
    ? {ok: false, status: 500, headers: {get: () => 'application/json'}, json: () => Promise.resolve({error: 'The forecast service could not finish this calculation.'})}
    : {ok: true, status: 200, headers: {get: () => 'application/json'}, json: () => Promise.resolve(RUN.banks)}); };
  const {t, els} = session({fetchImpl: reply});
  t.buildInputs();
  await t.calculate();
  assert.equal(requests.length, 1);
  const alert = els['gf-alert'], flat = node => [node.textContent || '', ...(node.children || []).map(flat)].join('');
  assert.equal(alert.hidden, false);
  const retry = alert.children.find(child => child.tag === 'button');
  assert.ok(retry, 'the alert has a button'); assert.equal(retry.textContent, 'Try again'); assert.equal(retry.type, 'button');
  assert.match(flat(alert), /The forecast service could not finish this calculation\./); assert.match(flat(alert), /then try again\./);
  assert.doesNotMatch(flat(alert), /calculate again/i);
  failing = false;
  await retry.on.click();
  assert.equal(requests.length, 2, 'pressing it sends exactly one request');
  assert.equal(alert.hidden, true, 'and the alert is gone once it succeeds');
  // no instruction left says "calculate"
  const source = fs.readFileSync(SOURCE, 'utf8');
  assert.doesNotMatch(source.replace(/schema 1|Schema 1|Calculate both horizons to update the comparison/g, ''), /(?:and|then|to) calculate\b(?! both)/);
  assert.match(source, /Inputs changed\. Set growth to see a projection\./);
});

test('under Lobbying the warnings list does not repeat the bar chart\'s flags (the Party split\'s notes and the lobbying flags stay); the Party split caption has a real space before it', () => {
  const {t, els} = session();
  const model = {chart: t.chartModel(RUN.banks, null, 'pac'), mixes: ['House', 'Senate'].map(chamber => t.mixModel(RUN.banks, null, chamber)), lobby: t.lobbyModel(RUN.banks, 2026, 2028)};
  assert.ok(model.chart.flags.length > 0);
  t.setAccount('pac'); t.paintWarnings(model);
  const lines = () => els['gf-warnings-list'].children.map(li => li.children.map(c => c.textContent).join(''));
  assert.ok(lines().some(line => line.includes('· Company PACs: ')), 'the PAC account\'s own flags are listed under it: ' + lines().join(' | '));
  t.setAccount('lobby'); t.paintWarnings(model);
  assert.equal(lines().filter(line => /· (Company PACs|General giving|PAC to candidates): /.test(line)).length, 0, 'nothing of the bar chart under Lobbying');
  assert.ok(lines().some(line => /· (House|Senate): /.test(line)), 'the Party split stays on screen, so its notes stay');
});

test('chart figures are one-decimal millions, periods are 2025–26 style, and a caption reads "Polymarket, Jan–Jun 2026"', () => {
  const {t} = session();
  assert.deepEqual(plain([27.0e6, 30.2e6 - 4, 53.6e6, 999499, 999500, 12345, 800, 62.94e6].map(t.chartMoney)), ['$27.0M', '$30.2M', '$53.6M', '$999K', '$1.0M', '$12K', '$800', '$62.9M']);
  assert.equal(t.shortPeriod(2026), '2025–26'); assert.equal(t.shortPeriod(2028), '2027–28');
  const snapshot = FIXTURE.catalog.saved_outlook;
  assert.equal(t.outlookSource(snapshot, snapshot.control.House).text, 'Polymarket, Jan–Jun 2026');
  assert.match(t.outlookSource(snapshot, snapshot.control.House).title, /^Saved 2026 outlook, as of /);
});

test('the party split draws one 100% bar per basis: Democratic from the left, Republican from the right, a tick at the anchor cycle\'s Republican share, one-decimal shares inside the ends', () => {
  const {t, els} = session();
  els['gf-house-body'] = el('div'); els['gf-senate-body'] = el('div');
  const model = t.mixModel(P1.banks, null, 'House'), host = els['gf-house-body'];
  t.drawMix(model, host, false);
  const groups = host.children;
  assert.equal(groups.length, 2, 'Employees and Company PACs, never a pooled third');
  assert.deepEqual(plain(groups.map(g => g.children[0].textContent)), ['Employees', 'Company PACs']);
  groups.forEach((group, i) => {
    const track = group.children[1], [dem, rep, tick] = track.children, row = model.rows[i];
    assert.equal(parseFloat(dem.style.width).toFixed(1), ((1 - row.share) * 100).toFixed(1));
    assert.equal(parseFloat(rep.style.width).toFixed(1), (row.share * 100).toFixed(1));
    assert.equal(dem.children[0].textContent, ((1 - row.share) * 100).toFixed(1) + '%'); assert.equal(rep.children[0].textContent, (row.share * 100).toFixed(1) + '%');
    assert.equal(dem.className, 'gf-pd'); assert.equal(rep.className, 'gf-pr');
    assert.equal(tick.className, 'gf-ptick'); assert.equal(parseFloat(tick.style.left).toFixed(1), ((1 - row.anchorShare) * 100).toFixed(1), 'the tick is the anchor cycle share, measured from the left like the Democratic part');
    assert.equal(track.tabIndex, 0, 'a bar can be focused for its tooltip');
  });
  // nothing to draw yet: an empty track and no tick
  t.drawMix(t.mixModel(null, null, 'Senate'), els['gf-senate-body'], false);
  assert.ok(els['gf-senate-body'].children.every(g => g.children[1].children.every(c => c.className !== 'gf-ptick' && c.className !== 'gf-pd')));
});

test('every flag the old chips carried is a line of plain text, scope first, warnings before notes', () => {
  const {t, els} = session({waivers: REGISTRY_WAIVERS});
  const model = {chart: t.chartModel(P1.banks, null, 'general'), mixes: ['House', 'Senate'].map(chamber => t.mixModel(P1.banks, null, chamber)), lobby: t.lobbyModel(P1.banks, 2026, 2028)};
  t.paintWarnings(model);
  const list = els['gf-warnings-list'], lines = list.children.map(li => li.children.map(c => c.textContent).join(''));
  assert.ok(lines.length >= 2);
  assert.equal(els['gf-warnings'].hidden, false);
  for (const chip of [...model.chart.flags, ...model.mixes[0].chips, ...model.mixes[1].chips]) assert.ok(lines.some(line => line.includes(chip.text)), 'no flag is lost: ' + chip.text);
  assert.ok(lines.every(line => /^(Warning|Note) · /.test(line)));
  assert.ok(lines.some(line => /House: .*Gate waived/.test(line)), 'a waiver is a note under its chamber');
  const warnAt = lines.findIndex(l => l.startsWith('Warning')), noteAt = lines.findIndex(l => l.startsWith('Note'));
  if (warnAt >= 0) assert.ok(warnAt < noteAt || noteAt < 0);
  t.paintWarnings({chart: t.chartModel(null, null, 'general'), mixes: [{chamber: 'House', chips: []}, {chamber: 'Senate', chips: []}], lobby: {state: 'empty', text: ''}});
  assert.equal(els['gf-warnings'].hidden, true, 'no warnings: the section is not drawn');
});

// ---- nominal dollars by default (D-092, Paul 2026-10-09) ---------------------------------------------------------------------------------------------------------------------------
// The calculator sends every cycle-model measure with its nominal twin (`nominal`); the chart lays it over the 2025-dollar fields, the page does no inflation arithmetic, and the choice is a
// Nominal | 2025 dollars control that is remembered per viewer. The 2023-24 bar and the observed amount are Mentat's own nominal values.
const F_2026 = 1.012863218, F_2028 = 1.0656483025330452;          // 2.5726436% a year at the cycle mid-point against 2025 (D-092)
const near = (actual, expected, tolerance = 1e-9) => Math.abs(actual / expected - 1) <= tolerance;

test('a viewer with no stored choice sees nominal dollars: the caption, the control and every column are nominal, and the toggle gives 2025 dollars back', () => {
  const {t, els} = nominalSession('commercial_banks');
  const run = CYC.banks, g = measure(run, 0, 'general_giving'), g28 = measure(run, 1, 'general_giving');
  let model = t.chartModel(run, run, 'general');
  assert.deepEqual([model.basis, model.dollars, model.nominalAvailable], ['Nominal dollars', 'nominal', true]);
  const [anchor, cur, next] = model.columns;
  // the 2023-24 bar is the anchors' own nominal level, not the 2025-dollar level converted back; the observed amount likewise
  assert.equal(anchor.value, g.anchor_cycle_nominal); assert.equal(anchor.value, g.nominal.anchor_cycle); assert.notEqual(anchor.value, g.anchor_cycle_real_2025);
  assert.equal(cur.observed, g.observed.amount_nominal);
  // one value per horizon, by hand: nominal = 2025 dollars x the mid-cycle expected-inflation factor
  assert.ok(near(cur.total, g.cycle_given_observed_median * F_2026), '2025-26 full cycle'); assert.equal(cur.total, g.nominal.cycle_given_observed_median);
  assert.ok(near(cur.low, g.cycle_given_observed_low * F_2026) && near(cur.high, g.cycle_given_observed_high * F_2026), '2025-26 interval');
  assert.ok(near(cur.remainder, g.remaining_given_observed_median * F_2026), '2025-26 remainder'); assert.ok(Math.abs(cur.observed + cur.remainder - cur.total) < 1e-6, 'the stack adds up in nominal dollars');
  assert.ok(near(next.total, g28.projected_cycle_real_2025 * F_2028), '2027-28 full cycle'); assert.equal(next.total, g28.nominal.projected_cycle);
  assert.ok(near(next.low, g28.cycle_prediction_low * F_2028) && near(next.high, g28.cycle_prediction_high * F_2028), '2027-28 interval');
  assert.equal(model.summary.startsWith('Giving by cycle, General giving, nominal dollars. '), true);
  // the words: the anchor's tooltip says both amounts, this chart's first; the projection tooltips name the unit
  assert.deepEqual(plain(row(anchor.tip, 'Nominal (this chart)')), ['Nominal (this chart)', '$25.92M']); assert.deepEqual(plain(row(anchor.tip, 'In 2025 USD')), ['In 2025 USD', '$26.98M']);
  assert.ok(row(cur.intervalTip, 'Full cycle, median (nominal)') && row(next.intervalTip, 'Projected full cycle (nominal)'));
  assert.match(texts(anchor.tip), /in the nominal dollars Mentat holds; the projections are converted at the expected inflation/);
  t.paintAccountChrome({chart: model});
  assert.equal(els['gf-basis'].textContent, 'Nominal dollars'); assert.equal(els['gf-dollars'].hidden, false);
  assert.deepEqual([els['gf-dollars-nominal'].attrs['aria-pressed'], els['gf-dollars-real'].attrs['aria-pressed']], ['true', 'false']);
  // the toggle: the same response, back in 2025 dollars, exactly as before
  t.setDollars('real');
  model = t.chartModel(run, run, 'general');
  assert.deepEqual([model.basis, model.dollars], ['2025 dollars', 'real']);
  assert.equal(model.columns[0].value, g.anchor_cycle_real_2025); assert.equal(model.columns[1].total, g.cycle_given_observed_median); assert.equal(model.columns[2].total, g28.projected_cycle_real_2025);
  assert.deepEqual(plain(row(model.columns[0].tip, 'In 2025 USD (this chart)')), ['In 2025 USD (this chart)', '$26.98M']);
  t.paintAccountChrome({chart: model});
  assert.equal(els['gf-basis'].textContent, '2025 dollars'); assert.deepEqual([els['gf-dollars-nominal'].attrs['aria-pressed'], els['gf-dollars-real'].attrs['aria-pressed']], ['false', 'true']);
  t.setDollars('nominal'); assert.equal(t.chartModel(run, run, 'general').columns[1].total, g.nominal.cycle_given_observed_median);
});

test('the axis follows the basis it is drawn in: switching back to 2025 dollars does not keep the larger nominal scale', () => {
  const {t} = nominalSession('commercial_banks');
  const nominalTop = t.chartModel(CYC.banks, CYC.banks, 'general').axisMax;
  t.setDollars('real');
  const realTop = t.chartModel(CYC.banks, CYC.banks, 'general').axisMax;
  assert.ok(realTop < nominalTop, `${realTop} < ${nominalTop}`);
  assert.equal(realTop, Math.max(...[measure(CYC.banks, 0, 'general_giving').anchor_cycle_real_2025, measure(CYC.banks, 0, 'general_giving').observed.amount_real_2025, measure(CYC.banks, 0, 'general_giving').cycle_given_observed_median,
    measure(CYC.banks, 0, 'general_giving').cycle_given_observed_high, measure(CYC.banks, 1, 'general_giving').projected_cycle_real_2025, measure(CYC.banks, 1, 'general_giving').cycle_prediction_high]));
});

test('company PACs, Telecom: the 2023-24 bar is the cohort total as the panel holds it and the open cycle is the panel\'s own nominal nowcast, in nominal dollars', () => {
  const {t, els} = nominalSession('telecom');
  const run = CYC.telecom, m = measure(run, 0, 'pac_to_candidates'), model = t.chartModel(run, run, 'pac');
  assert.equal(model.columns[0].value, 7834150); assert.equal(model.columns[1].observed, 4427500);          // the panel's 2023-24 total and its observed amount, nominal
  assert.ok(Math.abs(model.columns[1].total - 5823026.33) < 0.01, String(model.columns[1].total));          // the panel's published nowcast, to the cent: no conversion at all
  t.setAccount('pac'); t.paintAccountChrome({chart: model});
  assert.equal(els['gf-basis'].textContent, 'Nominal dollars · Telecom: 8 PACs (published panel cohort), observed to 2026-09-26');
  assert.equal(m.nominal.cycle_given_observed_median, model.columns[1].total);
});

test('the choice is remembered per viewer in localStorage; blocked storage falls back to nominal and the toggle still works for the page', () => {
  const key = 'mentat.givingForecast.dollars';
  const kept = {};
  const storage = {getItem: k => (k in kept ? kept[k] : null), setItem: (k, v) => { kept[k] = String(v); }};
  let s = load(undefined, undefined, storage); s.t.setCatalog(clone(CYCLE.catalog));
  assert.equal(s.t.getDollars(), 'nominal');          // nothing stored: nominal
  s.t.setDollars('real'); assert.equal(kept[key], 'real');
  s.t.setDollars('nominal'); assert.equal(kept[key], 'nominal');
  s.t.setDollars('real');
  s = load(undefined, undefined, storage); assert.equal(s.t.getDollars(), 'real', 'a later page opens in the stored basis');
  kept[key] = 'junk'; s = load(undefined, undefined, storage); assert.equal(s.t.getDollars(), 'nominal', 'a stored value that is not one of the two is ignored');
  const blocked = {getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('QuotaExceededError'); }};
  s = load(undefined, undefined, blocked); assert.equal(s.t.getDollars(), 'nominal');
  assert.doesNotThrow(() => s.t.setDollars('real')); assert.equal(s.t.getDollars(), 'real');
  s = load(); assert.equal(s.t.getDollars(), 'nominal', 'no storage object at all');
});

test('no control and 2025 dollars where the answer has no nominal twin (the rollback module) and under Lobbying', () => {
  const {t, els} = session();
  const old = t.chartModel(RUN.banks, RUN.banks, 'general');
  assert.deepEqual([old.basis, old.dollars, old.nominalAvailable], ['2025 dollars', 'real', false]);
  t.paintAccountChrome({chart: old});
  assert.equal(els['gf-dollars'].hidden, true); assert.equal(els['gf-basis'].textContent, '2025 dollars');
  const {t: u, els: ue} = nominalSession('commercial_banks');
  u.paintAccountChrome({chart: u.chartModel(CYC.banks, CYC.banks, 'general')}); assert.equal(ue['gf-dollars'].hidden, false);
  u.setAccount('lobby'); u.paintAccountChrome({chart: u.chartModel(CYC.banks, CYC.banks, 'general'), lobby: u.lobbyModel(CYC.banks, 2026, 2028)});
  assert.equal(ue['gf-dollars'].hidden, true, 'the lobbying chart is nominal already');
  assert.match(ue['gf-basis'].textContent, /^Annual, nominal dollars/);
});

test('the Party split\'s dollars follow the dollar basis: 2025-dollar amount x the open cycle\'s nominal.factor in nominal dollars, as sent in 2025 dollars; the toggle redraws the split', () => {
  const {t} = nominalSession('commercial_banks');
  const run = CYC.banks, F = measure(run, 0, 'pac_to_candidates').nominal.factor, usd = v => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 2}).format(v);
  const house = basis(run, 0, 'House', 'pac_candidate'), a = house.remaining_allocation, seen = house.observed, sen = basis(run, 0, 'Senate', 'pac_candidate');
  const want = k => [`Remaining from 2026-07-01 (median): R ${usd(a.remaining_R_real_2025 * k)} · D ${usd(a.remaining_D_real_2025 * k)} of ${usd(a.remaining_total_real_2025 * k)}`, `Observed through ${seen.through}: R ${usd(seen.R_real_2025 * k)} · D ${usd(seen.D_real_2025 * k)}`];
  const senate = k => `projected two-party total (${usd(sen.projected_total_real_2025 * k)})`, tip = chamber => texts(t.mixModel(run, run, chamber).rows[1].tip), has = (text, k) => want(k).every(w => text.includes(w));
  assert.ok(F > 1 && usd(a.remaining_R_real_2025 * F) !== usd(a.remaining_R_real_2025), 'the two bases differ in print');
  assert.ok(has(tip('House'), F) && tip('Senate').includes(senate(F)), 'nominal: real x factor'); assert.ok(!tip('House').includes(want(1)[0]));
  assert.ok(Math.abs(seen.R_real_2025 * F / seen.R - 1) < 1e-9, 'the factor turns the observed 2025-dollar amount back into the nominal one Mentat holds');
  t.setRuns(run, run); t.paintView(false);          // the toggle redraws the split from the same response
  assert.ok(has(texts(t.getPainted().mixes[0].rows[1].tip), F));
  t.setDollars('real');
  assert.ok(has(texts(t.getPainted().mixes[0].rows[1].tip), 1), 'the painted split follows the toggle');
  assert.ok(has(tip('House'), 1) && tip('Senate').includes(senate(1)), '2025 dollars: as sent'); assert.ok(!tip('House').includes(want(F)[0]));
  assert.equal(t.mixModel(run, run, 'House').rows[1].share, house.remaining_allocation.implied_cycle_R_share, 'percentages do not move');
});
