// The Giving Forecast console, schema 2: renders the calculator's golden payload (tests/fixtures/giving_forecast_v2_render.json, pinned to
// giving_forecast.py by test_giving_forecast_v2.py) through the real giving-forecast.js in a vm sandbox against a DOM stub. An in-memory copy of
// the file exposes the closure functions; the file on disk is not touched. Schema 1 must keep rendering as it did.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = path.join(__dirname, '..', 'app', 'giving-forecast.js');
const DATA = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'giving_forecast_v2_render.json'), 'utf8'));

function el(tag) {
  const classes = new Set();
  return {tag, textContent: '', className: '', children: [], dataset: {}, style: {}, value: '', hidden: false, scope: '', disabled: false,
    classList: {toggle(name, on) { (on === undefined ? !classes.has(name) : on) ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name)},
    append(...c) { this.children.push(...c); }, replaceChildren(...c) { this.children = c; }, setAttribute() {}, addEventListener() {}, after() {}, firstChild: {}};
}

function load(fetchImpl = () => {}) {
  const els = {};
  // Elements register under their id when it is set, so a value written through the input field() returns is the one getElementById reads.
  const create = tag => { const e = el(tag); let id = ''; Object.defineProperty(e, 'id', {get: () => id, set: v => { id = v; els[v] = e; }}); return e; };
  const document = {getElementById: id => els[id] || (els[id] = el('div')), createElement: create};
  let src = fs.readFileSync(SOURCE, 'utf8');
  assert.match(src, /\}\)\(\);\s*$/, 'unexpected file ending');
  src = src.replace(/\}\)\(\);\s*$/, `globalThis.__t={isV2,configureForSchema,pacCell,shareCell,lobbyCell,pacCellV2,shareCellV2,lobbyCellV2,render,renderV2,renderRefusal,renderEvidence,renderEvidenceV2,
    getRequest,getRequestV2,buildInputs,historicalExample,anchorNote,calculate,setCatalog:c=>{catalog=c;},industry};\n})();\n`);
  const ctx = vm.createContext({document, Intl, Number, Math, String, console, URL: {}, Blob: function () {}, setTimeout, clearTimeout, fetch: fetchImpl, AbortController, Object, JSON, Array, Date});
  vm.runInContext(src, ctx, {filename: SOURCE});
  return {t: ctx.__t, els, document};
}

function texts(node, acc = []) {
  if (node == null || typeof node !== 'object') return acc;
  if (node.textContent) acc.push((node.className ? '[' + node.className + '] ' : '') + node.textContent);
  node.children.forEach(child => texts(child, acc));
  return acc;
}
const flat = node => texts(node).join('\n');
const horizon = (run, index) => DATA.runs[run].horizons[index];

function session(sector = 'commercial_banks', fetchImpl) {
  const s = load(fetchImpl);
  s.els['gf-industry'] = Object.assign(el('select'), {value: sector});
  s.els['gf-date'] = Object.assign(el('input'), {value: '2026-09-30'});
  s.t.setCatalog(DATA.catalog);
  return s;
}

test('schema 2 hides the seats and slope selectors and takes the scope text from the catalog', () => {
  const {t, els} = session();
  assert.equal(t.isV2(), true);
  t.configureForSchema();
  assert.equal(els['gf-signal-field'].hidden, true);
  assert.equal(els['gf-slopes-field'].hidden, true);
  assert.equal(els['gf-signal'].value, 'control');
  assert.equal(els['giving-forecast'].classList.contains('gf-v2'), true);
  assert.match(els['gf-scope'].textContent, /Mentat levels for the industry/);
  assert.doesNotMatch(els['gf-scope'].textContent, /auto manufacturers/);
  assert.equal(els['gf-sources-summary'].textContent, 'Sources and assumption notes');
});

test('schema 1 keeps its selectors: nothing is hidden when the catalog is not schema 2', () => {
  const {t, els} = session();
  t.setCatalog({...DATA.catalog, schema_version: undefined, scope: 'Research industry totals.'});
  assert.equal(t.isV2(), false);
  t.configureForSchema();
  assert.equal(els['gf-signal-field'].hidden, false);
  assert.equal(els['gf-slopes-field'].hidden, false);
  assert.equal(els['giving-forecast'].classList.contains('gf-v2'), false);
  assert.equal(els['gf-sources-summary'].textContent, 'Sources and remainder amounts');   // schema 1 keeps its own wording
});

test('PAC is shown twice side by side: the roster PACs\' own giving and Mentat general giving, with both intervals', () => {
  const {t} = session();
  const cell = t.pacCellV2(horizon('banks', 0).pac, true);
  const pairs = cell.children.filter(c => c.className === 'gf-pair');
  assert.equal(pairs.length, 1);
  const columns = pairs[0].children;
  assert.equal(columns.length, 2);
  assert.match(flat(columns[0]), /Roster companies' PACs: own giving to House and Senate candidates/);
  assert.match(flat(columns[1]), /Mentat general giving \(all channels\)/);
  const all = flat(cell);
  assert.match(all, /Remainder from 2026-07-01/);
  assert.match(all, /\$19\.2M/);                                   // general-giving remainder 19,202,438
  assert.match(all, /\$2\.04M/);                                   // roster-PAC remainder 2,036,026
  assert.match(all, /Observed through 2026-06-30: \$11\.57M \(face value; no 2026 CPI yet\)/);
  assert.match(all, /Full-cycle coefficient interval: \$22\.65M to \$41\.81M/);
  assert.match(all, /Full-cycle prediction interval: \$16\.83M to \$56\.25M/);
  assert.match(all, /\+13\.21% vs the Mentat 2023–2024 level \(previous cycle\); coefficient interval -16\.68% to \+53\.81%; prediction interval -38\.08% to \+106\.96%/);
  assert.match(all, /industry-specific slope · ordinary p = 0\.3838/);
});

test('the slope labels stay: pooled slope / industry-specific slope', () => {
  const {t} = session();
  const banks = flat(t.pacCellV2(horizon('banks', 0).pac, true));
  assert.match(banks, /industry-specific slope/);
  const pooled = JSON.parse(JSON.stringify(horizon('banks', 0).pac));
  pooled.slope.label = 'pooled slope';
  assert.match(flat(t.pacCellV2(pooled, true)), /pooled slope · ordinary p/);
});

test('the next cycle is labelled as chained on a projection and has no remainder', () => {
  const {t} = session();
  const cell = t.pacCellV2(horizon('banks', 1).pac, false);
  const all = flat(cell);
  assert.match(all, /chained on a projection, 2 steps/);
  assert.doesNotMatch(all, /Remainder from/);
  assert.match(all, /Full-cycle projection/);
});

test('the credit-union PAC channel is flagged as a single-PAC thin base', () => {
  const {t} = session('credit_unions');
  const all = flat(t.pacCellV2(horizon('credit_unions', 0).pac, true));
  assert.match(all, /\[gf-detail gf-error\] One PAC \(SchoolsFirst Federal Credit Union\): a thin base/);
  assert.doesNotMatch(flat(t.pacCellV2(horizon('banks', 0).pac, true)), /One PAC/);
});

test('a share shows both bases side by side with the pp change, both intervals and the 131/182-day flag', () => {
  const {t} = session();
  const cell = t.shareCellV2(horizon('banks', 0).shares.House, true, 'House');
  const columns = cell.children.find(c => c.className === 'gf-pair').children;
  assert.equal(columns.length, 2);
  assert.match(flat(columns[0]), /Employees' candidate giving/);
  assert.match(flat(columns[1]), /Roster PACs' candidate giving/);
  const all = flat(cell);
  assert.match(all, /2023–2024 Mentat share 34\.35% → 33\.41% \(-0\.94 pp\)/);       // employees, Polymarket-to-Polymarket
  assert.match(all, /2023–2024 Mentat share 54\.36% → 53\.42% \(-0\.94 pp\)/);       // roster PACs
  assert.match(all, /Coefficient interval: 31\.34% to 35\.47%/);
  assert.match(all, /Prediction interval: /);
  assert.match(all, /2024 House control input: 44\.92 \(polymarket January–June, 131\/182 days\)/);
  assert.match(all, /\[gf-detail gf-error\] 131\/182 days of the 2024 window exist \(from 2024-02-21\)/);
  assert.match(all, /Materiality test toward the expected advantaged party: Democrats/);
});

test('each chamber\'s share names its anchor and the recorded reason: same phase for House, previous cycle for Senate', () => {
  const {t} = session();
  const run = DATA.runs.banks_same_phase_house;
  const house = flat(t.shareCellV2(run.horizons[0].shares.House, true, 'House'));
  assert.match(house, /\[gf-detail\] Anchor: Mentat 2021–2022 \(same phase\)\./);
  assert.match(house, /\[gf-detail\] same_phase chosen: its MAE is 20% \(LOEO\) and 30% \(chronological\) below previous_cycle/);
  assert.match(house, /2021–2022 Mentat share 38\.75% → 48\.59% \(\+9\.84 pp\)/);               // the share follows the anchor, not "previous"
  assert.match(house, /2021–2022 House control input: 86\.09 \(the value the model was fitted on for that cycle\)/);
  assert.doesNotMatch(house, /2023–2024|polymarket|days of the/);
  const next = flat(t.shareCellV2(run.horizons[1].shares.House, false, 'House'));
  assert.match(next, /Anchor: Mentat 2023–2024 \(same phase\)\./);
  assert.match(next, /2024 House control input: 44\.92 \(polymarket January–June, 131\/182 days\)/);
  const senate = flat(t.shareCellV2(run.horizons[0].shares.Senate, true, 'Senate'));
  assert.match(senate, /\[gf-detail\] Anchor: Mentat 2023–2024 \(previous cycle\)\./);
  assert.match(senate, /previous_cycle kept: same_phase does not beat it by 5% in every design/);
  assert.match(senate, /2023–2024 Mentat share/);
  assert.doesNotMatch(senate, /same phase\)/);
});

test('a previous-cycle share names its anchor too, also before the inputs are entered; a withheld share does not', () => {
  const {t} = session();
  assert.match(flat(t.shareCellV2(horizon('banks', 0).shares.House, true, 'House')), /Anchor: Mentat 2023–2024 \(previous cycle\)\./);
  assert.match(flat(t.shareCellV2(horizon('banks', 1).shares.House, false, 'House')), /Anchor: Mentat 2023–2024 \(previous cycle\)\./);
  const blank = flat(t.shareCellV2(horizon('banks_blank', 0).shares.House, true, 'House'));
  assert.match(blank, /Enter House current-control assumption, House control score\.\n\[gf-detail\] Anchor: Mentat 2023–2024 \(previous cycle\)\./);
  assert.doesNotMatch(flat(t.shareCellV2(horizon('property_casualty', 0).shares.House, true, 'House')), /Anchor:/);
});

test('the coverage note says each candidate mix names its own anchor only when one uses the same phase', () => {
  const same = session();
  same.t.render(DATA.runs.banks_same_phase_house);
  assert.match(flat(same.els['gf-results']), /Anchor cycle 2023–2024\. Each candidate mix names its own anchor\./);
  const prev = session();
  prev.t.render(DATA.runs.banks);
  assert.doesNotMatch(flat(prev.els['gf-results']), /names its own anchor/);
});

test('the historical example says which candidate mix does not reduce to the Mentat level', () => {
  const {t, els} = session();
  const catalog = JSON.parse(JSON.stringify(DATA.catalog));
  t.buildInputs();
  t.historicalExample();
  // corrected 2026-10-01 (owner review: follows the run it describes; none is loaded in this harness): the model's change is zero and its projected amounts equal the Mentat level
  // before any conditioning on the giving already observed; the party mixes still move (midterm cycle, auto-filled prior-cycle growth outside the fitted range)
  assert.match(els['gf-status'].textContent, /The model's change in the amounts is zero \(growth and control as in 2023–2024\)\. The model's projected amounts equal the 2023–2024 Mentat level before any conditioning on the giving already observed\. Party mixes also move: 2026 is a midterm, and the blank prior-cycle growth uses 2024's growth, which is outside the range the share models were fitted on\. Change them to explore\./);
  assert.doesNotMatch(els['gf-status'].textContent, /so the projection equals/);
  catalog.share_models.control_House.anchors.commercial_banks.anchor = 'same_phase';
  t.setCatalog(catalog);
  t.historicalExample();
  assert.match(els['gf-status'].textContent, /The model's change in the amounts is zero \(growth and control as in 2023–2024\)\. The model's projected amounts equal the 2023–2024 Mentat level before any conditioning on the giving already observed\. Party mixes also move: the blank prior-cycle growth uses 2024's growth, which is outside the range the share models were fitted on\. The 2026 House candidate mix is anchored on Mentat 2021–2022, the cycle of the same phase\. Change them to explore\./);
});

test('thin bases are flagged per basis and budget_inconsistent is flagged, not clipped', () => {
  const {t} = session('credit_unions');
  const house = flat(t.shareCellV2(horizon('credit_unions', 0).shares.House, true, 'House'));
  assert.match(house, /Thin base: \$55\.12K of two-party House giving in 2023–2024 \(under \$100K\)/);
  assert.match(house, /Thin base: \$75\.8K of two-party House giving/);
  assert.match(house, /budget_inconsistent: /);
  const senate = flat(t.shareCellV2(horizon('credit_unions', 0).shares.Senate, true, 'Senate'));
  assert.match(senate, /Thin base: \$5K of two-party Senate giving/);
  assert.doesNotMatch(senate.split('Roster PACs')[0], /Thin base/);                  // the employee Senate base ($193K) is not thin
});

test('a remaining allocation shows R and D amounts on the bases that can carry one', () => {
  const {t} = session();
  const all = flat(t.shareCellV2(horizon('banks', 0).shares.Senate, true, 'Senate'));
  assert.match(all, /Remaining allocation: R \$[\d.]+[KM]? · D \$[\d.]+[KM]? of a projected \$[\d.]+M two-party total/);
  assert.match(all, /Observed through 2026-06-30: R \$/);
});

test('both lobbying series are side by side, never summed, with observed quarters and the remainder', () => {
  const {t} = session();
  const cell = t.lobbyCellV2(horizon('banks', 0).lobbying);
  const columns = cell.children.find(c => c.className === 'gf-pair').children;
  assert.equal(columns.length, 2);
  assert.match(flat(columns[0]), /Outside-firm fees/);
  assert.match(flat(columns[1]), /In-house expenses/);
  const all = flat(cell);
  assert.match(all, /Observed Q1\+Q2: \$13\.37M/);
  assert.match(all, /Observed Q1\+Q2: \$38\.98M/);
  assert.match(all, /never added/);
  assert.match(all, /Melange LDA bank sector \(total, not a bank tier\), nominal dollars/);
  assert.match(all, /Nowcast, Q2 cut: \$13\.37M filed \+ 51\.58% of 2025 \(\$23\.82M\) still to come\./);
  assert.match(all, /80% band: \$[\d.]+M to \$[\d.]+M\./);
  assert.match(all, /~95% band \(largest blind miss\): \$[\d.]+M to \$[\d.]+M\./);
  assert.match(all, /80% band: the 80th percentile of the blind nowcast errors \(n=32 blind tests\)\./);
  assert.match(all, /~95% band: the largest blind miss \(n=32 blind tests\); with so few tests it is a worst-observed bound, not an estimated 95% interval\./);
  assert.doesNotMatch(all, /Coefficient interval|Prediction interval/);
  assert.doesNotMatch(all, /pooled slope · ordinary p/, 'no growth change entered: the slope plays no part');
  assert.doesNotMatch(all, /[Tt]otal lobbying/);
});

test('property and casualty lobbying is hidden and flagged; its other models are withheld with their reason', () => {
  const {t} = session('property_casualty');
  const h = horizon('property_casualty', 0);
  assert.match(flat(t.lobbyCellV2(h.lobbying)), /\[gf-detail gf-error\] Not shown: No Melange-LDA sector/);
  assert.match(flat(t.pacCellV2(h.pac, true)), /F3400/);
  assert.match(flat(t.shareCellV2(h.shares.House, true, 'House')), /F3400/);
});

test('an anchor_stale response renders a refusal with the reason, not numbers', () => {
  const {t, els} = session();
  t.renderV2(DATA.runs.banks_stale);
  const all = flat(els['gf-results']);
  assert.match(all, /not calculated/);
  assert.match(all, /Rebuild the anchors/);
  assert.match(all, /refuses rather than mix old levels with a new report/);
  assert.doesNotMatch(all, /Remainder from/);
});

test('a refusal is reported as not calculated: no success message and no export', async () => {
  const reply = data => () => Promise.resolve({ok: true, status: 200, headers: {get: () => 'application/json'}, json: () => Promise.resolve(data)});
  const stale = session('commercial_banks', reply(DATA.runs.banks_stale));
  stale.t.buildInputs();
  await stale.t.calculate();
  assert.equal(stale.els['gf-export'].disabled, true);
  assert.match(stale.els['gf-status'].textContent, /Rebuild the anchors/);
  assert.doesNotMatch(stale.els['gf-status'].textContent, /calculated/);
  assert.equal(stale.els['gf-status'].classList.contains('gf-error'), true);
  const fine = session('commercial_banks', reply(DATA.runs.banks));
  fine.t.buildInputs();
  await fine.t.calculate();
  assert.equal(fine.els['gf-export'].disabled, false);
  assert.match(fine.els['gf-status'].textContent, /Both horizons calculated/);
});

test('the full render shows the four outcome rows, the coverage note and the notices', () => {
  const {t, els} = session();
  t.render(DATA.runs.banks);
  const root = els['gf-results'];
  const all = flat(root);
  for (const title of ['PAC giving', 'House candidate mix', 'Senate candidate mix', 'Lobbying (Melange LDA)']) assert.match(all, new RegExp(title.replace(/[()]/g, '\\$&')));
  assert.match(all, /Mentat anchors: Banking \(Banking Industry Spending Overview\.xlsx\), collected 2026-09-09; observed through 2026-06-30; the remainder starts the next day\. Anchor cycle 2023–2024\./);
  assert.match(all, /41 of 67 2026 collection work units are stored_complete/);
  assert.match(all, /\$436,376\.35/);
  assert.match(all, /The two lobbying series are never summed|never added/);
  assert.match(all, /Levels come from Mentat/);
});

test('credit unions show the June cut-back in the coverage note', () => {
  const {t, els} = session('credit_unions');
  t.render(DATA.runs.credit_unions);
  const all = flat(els['gf-results']);
  assert.match(all, /observed through 2026-05-31 \(cut back from 2026-06-30: June incomplete/);
});

test('blank inputs stay unknown in the giving outcomes, never zero; lobbying is the nowcast', () => {
  const {t} = session();
  const h = horizon('banks_blank', 0);
  assert.match(flat(t.pacCellV2(h.pac, true)), /Enter expected two-year cycle revenue growth\./);
  assert.match(flat(t.shareCellV2(h.shares.House, true, 'House')), /Enter House current-control assumption, House control score\./);
  assert.match(flat(t.lobbyCellV2(h.lobbying)), /Nowcast, Q2 cut/, 'lobbying needs no input: blank is no change from the nowcast');
});

test('the request has no seat signal, slope selector, observed amounts or budgets', () => {
  const {t, els} = session();
  t.buildInputs();
  assert.equal(Object.keys(els).some(id => id !== 'gf-observed' && /observed|budget/.test(id)), false);    // gf-observed is the static, now empty, container
  const request = t.getRequest();
  assert.deepEqual(Object.keys(request).sort(), ['as_of', 'assumption_date', 'assumption_source', 'horizons', 'industry']);
  const a = request.horizons[2026];
  assert.deepEqual(Object.keys(a).sort(), ['annual_forecast_changes', 'control_scores', 'current_control', 'growth_pct', 'prior_growth_pct']);
  assert.equal(Number(a.control_scores.House), DATA.catalog.saved_outlook.control.House.value);      // the saved Polymarket January-June 2026 outlook is prefilled, editable
  assert.equal(a.current_control.Senate, 1);
  assert.equal(request.horizons[2028].control_scores.House, '');
});

test('the historical example loads the anchor cycle, so the model change is zero', () => {
  const {t, els} = session();
  t.buildInputs();
  t.historicalExample();
  const request = t.getRequest();
  assert.equal(Number(request.horizons[2026].growth_pct), 48.8853332525);
  assert.equal(Number(request.horizons[2026].control_scores.House), DATA.catalog.political_inputs.anchor_cycles['2024'].House.p_rep * 100);
  assert.equal(request.horizons[2026].current_control.House, 1);
  assert.equal(request.horizons[2026].current_control.Senate, 0);
});

test('the evidence panel reads the bundle: applied slopes per industry, seats as no signal, no hard-coded counts', () => {
  const {t, els} = session();
  t.renderEvidence();
  const all = flat(els['gf-evidence-content']);
  assert.match(all, /3 growth\/PAC industries, 2012–2024; control\/share samples end in 2022 and seat\/share samples in 2024/);
  assert.match(all, /PAC amount · Commercial banks/);
  assert.match(all, /PAC amount · Auto manufacturers/);                  // not offered, but still in the estimation: its applied slope is listed under its label
  assert.match(all, /industry-specific slope \(applied\)/);
  assert.match(all, /71 annual pairs across 6 industries; pooled slope/);
  assert.match(all, /seats \(no signal\)/);
  assert.match(all, /Seat regressions stay here as evidence only: no signal/);
  assert.match(all, /The selected Senate control effect is negative: -4\.25 pp Republican share per \+10 score points/);
  assert.match(all, /2024 House: P\(R control\) 44\.92 \(131\/182 days from 2024-02-21\)/);
  assert.match(all, /Not shown: Auto manufacturers \(No Mentat data for this industry/);
  assert.match(all, /Saved political outlook: 2026-09-25/);
  assert.match(all, /equal-weight Kalshi \+ Polymarket sensitivity 19\.03/);
  assert.doesNotMatch(all, /Three growth\/PAC industries/);
});

test('schema 1 still renders with its own functions (v1 text preserved)', () => {
  const {t, els} = session();
  const v1Pac = {status: 'ready', projected_remaining_real_2025: 1.5e6, projected_cycle_real_2025: 4e6, observed: {amount_real_2025: 2.5e6}, baseline_cycle: 2024,
    change_pct: 18.3, cycle_coefficient_low: 3e6, cycle_coefficient_high: 5e6, ordinary_p: 0.0006, extrapolation: false};
  const all = flat(t.pacCell(v1Pac, true));
  assert.match(all, /pooled growth slope/);
  assert.match(all, /Observed through cutoff: \$2\.5M/);
  const v1 = {schema_version: 1, industry_label: 'Credit unions', assumption_source: 'test', assumption_date: '2026-09-27', as_of: '2026-09-27',
    horizons: [{cycle: 2026, horizon: 'current_remainder', starts: '2026-09-28', ends: '2026-12-31', pac: {status: 'needs_inputs', reason: 'Enter growth.'},
      shares: {House: {status: 'needs_inputs', reason: 'Enter House.', ordinary_p: 0.007, coefficient_p_scope: 'Pooled forecast term'},
               Senate: {status: 'needs_inputs', reason: 'Enter Senate.', ordinary_p: 0.0007, coefficient_p_scope: 'Pooled forecast term'}},
      lobbying: [{year: 2026, status: 'needs_inputs'}]}]};
  t.render(v1);
  const page = flat(els['gf-results']);
  assert.match(page, /Whole-industry PAC amount; candidate-share models do not allocate this total\./);
  assert.match(page, /Lobbying response/);
  assert.match(page, /Pooled slope ordinary p = 0\.0070/);
});
