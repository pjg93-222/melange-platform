// FIX 4: a chart that compares companies or cycles says when the collection under it fell short.
// The report's own Data Quality check names the company x cycle cells whose stored rows are materially
// fewer than FEC reported (collection_gaps.py parses it). These tests drive the real shipped code:
// coverage-view.js decides whether a scope touches a cell and words it once; Chart Studio, Graph,
// Insights and the briefing then carry that one string into every export. Run with the glob form:
//   node --test "mentat/app/tests/**/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = file => path.join(__dirname, '..', file);
const read = file => fs.readFileSync(app(file), 'utf8');
const stripComments = text => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
function slice(file, from, to) {
  const text = read(file), start = text.indexOf(from), end = text.indexOf(to, start + from.length);
  assert.ok(start >= 0 && end > start, `${file}: ${from.trim()} ... ${to.trim()} is in the shipped source`);
  return text.slice(start, end);
}

const coverage = require('../coverage-view.js');

// The twelve itemized cells of the audited Telecom reference, exactly as collection_gaps.py returns them.
const CELLS = [
  {company: 'Lumen Technologies', cycle: '2023-2024', destination: 'contributions', held: 14133, reported: 28266, percent: 50},
  {company: 'Comcast', cycle: '2023-2024', destination: 'contributions', held: 17869, reported: 29991, percent: 60},
  {company: 'Comcast', cycle: '2021-2022', destination: 'contributions', held: 26441, reported: 34014, percent: 78},
  {company: 'Lumen Technologies', cycle: '2021-2022', destination: 'contributions', held: 7388, reported: 13779, percent: 54},
  {company: 'Verizon', cycle: '2023-2024', destination: 'contributions', held: 14783, reported: 19170, percent: 77},
  {company: 'Verizon', cycle: '2021-2022', destination: 'contributions', held: 23415, reported: 27056, percent: 87},
  {company: 'Comcast', cycle: '2025-2026', destination: 'contributions', held: 11753, reported: 15269, percent: 77},
  {company: 'Comcast', cycle: '2021-2022', destination: 'corporate', held: 1369, reported: 4365, percent: 31},
  {company: 'Comcast', cycle: '2023-2024', destination: 'corporate', held: 1299, reported: 4225, percent: 31},
  {company: 'Verizon', cycle: '2025-2026', destination: 'contributions', held: 31654, reported: 33964, percent: 93},
  {company: 'Altice USA', cycle: '2023-2024', destination: 'contributions', held: 1697, reported: 3394, percent: 50},
  {company: 'AT&T', cycle: '2021-2022', destination: 'corporate', held: 7064, reported: 8432, percent: 84}
];
const GAPS = {failed: true, cells: CELLS, unitemized: 19, source: 'Data Quality sheet'};
const COMCAST_2026 = 'Comcast 2025-26: 77% of FEC-reported records (11,753 of 15,269 contributions)';
const MORE = "19 more short cells are not itemized in the workbook's Data Quality check";
const notice = (scope, gaps = GAPS) => coverage.gapNotice([{gaps, scope}]);

test('a Comcast 2025-26 scope is told it holds 77% of what FEC reported, in the words the spec gives', () => {
  const found = notice({companies: ['Comcast'], cycles: ['2025-2026']});
  assert.equal(found.chip, 'Coverage gap');
  assert.equal(found.caption, COMCAST_2026 + '; ' + MORE);
  assert.match(found.caption, /77% of FEC-reported records/);
  assert.equal(found.text, 'Coverage gap: ' + found.caption);
  assert.deepEqual(found.cells.map(c => c.company), ['Comcast']);
});

test('the cycle can be named as an end year, a label or an en-dash label', () => {
  for (const cycle of ['2026', '2025-2026', '2025–2026']) {
    assert.match(notice({companies: ['Comcast'], cycles: [cycle]}).caption, /^Comcast 2025-26: 77%/, cycle);
  }
});

test('several cells are joined with "; " and "all companies" means every cell in the cycle', () => {
  const found = notice({companies: null, cycles: ['2025-2026']});
  assert.equal(found.caption, [COMCAST_2026, 'Verizon 2025-26: 93% of FEC-reported records (31,654 of 33,964 contributions)', MORE].join('; '));
  const everything = notice({companies: null, cycles: null});
  assert.equal(everything.cells.length, 12);
  assert.equal(everything.caption.split('; ').length, 13);
});

test('one company in one cycle can be short in two destinations; each is named', () => {
  const found = notice({companies: ['Comcast'], cycles: ['2021-2022']});
  assert.equal(found.caption, 'Comcast 2021-22: 78% of FEC-reported records (26,441 of 34,014 contributions); ' +
    'Comcast 2021-22: 31% of FEC-reported records (1,369 of 4,365 corporate); ' + MORE);
});

test('an unlisted cell is never shown as complete: the remainder is stated whatever the scope', () => {
  const found = notice({companies: ['Frontier Communications'], cycles: ['2025-2026']});
  assert.deepEqual(found.cells, []);
  assert.equal(found.caption, MORE);
  assert.equal(notice({companies: ['Comcast'], cycles: ['2025-2026']}, {...GAPS, unitemized: 1}).caption,
    COMCAST_2026 + "; 1 more short cell is not itemized in the workbook's Data Quality check");
});

test('with nothing unlisted, a scope that touches no short cell has no notice', () => {
  assert.equal(notice({companies: ['Frontier Communications'], cycles: null}, {...GAPS, unitemized: 0}), null);
  assert.equal(notice({companies: ['Comcast'], cycles: ['2024']}, {...GAPS, cells: [CELLS[6]], unitemized: 0}), null);
});

test('a passing check, an unread check and a missing payload say nothing', () => {
  assert.equal(notice({companies: null, cycles: null}, {failed: false, cells: [], unitemized: 0, source: 'Data Quality sheet'}), null);
  assert.equal(notice({companies: null, cycles: null}, {failed: false, cells: [], unitemized: 0, source: null}), null);
  assert.equal(coverage.gapNotice([{gaps: undefined, scope: {}}]), null);
  assert.equal(coverage.gapNotice(undefined), null);
});

test('a check that failed but could not be itemized is not shown as complete either', () => {
  const found = notice({companies: null, cycles: null}, {failed: true, cells: [], unitemized: 0, source: 'quality_checks.csv'});
  assert.equal(found.caption, "The workbook's Data Quality check reports short collection cells that are not itemized");
});

test('compared scopes pool their cells once each and never add the unitemized remainders', () => {
  const found = coverage.gapNotice([
    {gaps: GAPS, scope: {companies: ['Comcast'], cycles: ['2025-2026']}},
    {gaps: GAPS, scope: {companies: ['Comcast', 'Verizon'], cycles: ['2025-2026']}}]);
  assert.deepEqual(found.cells.map(c => c.company), ['Comcast', 'Verizon']);
  assert.equal(found.unitemized, 19);
  assert.equal(found.caption.split('; ').length, 3);
});

test('a query\'s filters name the scope: equals and "is one of" restrict, anything else leaves it open', () => {
  const scope = coverage.filtersScope([{field: 'company', op: 'eq', value: 'Comcast'},
    {field: 'cycle', op: 'in', value: ['2023-2024', '2025-2026']}, {field: 'state', op: 'eq', value: 'MA'}]);
  assert.deepEqual(scope, {companies: ['Comcast'], cycles: ['2023-2024', '2025-2026']});
  assert.deepEqual(coverage.filtersScope([{field: 'company', op: 'ne', value: 'Comcast'}, {field: 'cycle', op: 'contains', value: '2025'}]),
    {companies: null, cycles: null});
  assert.deepEqual(coverage.filtersScope(undefined), {companies: null, cycles: null});
  assert.equal(notice(scope).cells.length, 3);
});

test('the chip node is the console\'s own quality chip and carries the caption as its tooltip', () => {
  const made = [];
  const doc = {createElement: tag => { const node = {tag, className: '', textContent: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }}; made.push(node); return node; }};
  const chip = coverage.chipNode(notice({companies: ['Comcast'], cycles: ['2025-2026']}), doc);
  assert.equal(chip.textContent, 'Coverage gap');
  assert.match(chip.className, /\bquality-chip\b/);
  assert.match(chip.className, /\bcoverage-gap-chip\b/);
  assert.match(chip.title, /77% of FEC-reported records/);
  assert.match(chip.title, /Source: Data Quality sheet$/, 'the tooltip names which of the three places the check was read from');
  const served = notice({companies: ['Comcast'], cycles: ['2025-2026']}, {...GAPS, source: 'pinned payload export_guards', unitemized: 21});
  assert.match(coverage.chipNode(served, doc).title, /21 more short cells are not itemized.*Source: pinned payload export_guards$/);
  assert.deepEqual(served.sources, ['pinned payload export_guards']);
});

/* ---------------------------------------------------------------- Chart Studio */

function studioContext() {
  const context = vm.createContext({console});
  vm.runInContext(read('coverage-view.js'), context);
  return context;
}

test('Chart Studio: the scope an image, a copy and a print carry, and the chip a slide prints, name the gap', () => {
  const context = studioContext();
  Object.assign(context, {
    contributionCatalog: {datasets: [{id: 'Telecom 2025-2026', collection_gaps: GAPS}]},
    contributions: {BASIS: {receipt: ['Published included receipt view']}},
    core: {chartSourceRecord: s => s, chartProvenanceRecord: (s, sha) => ({sha})},
    data: {sha256: 'd'.repeat(64)}, scopeText: () => 'Telecom 2025-2026 · 2025-2026 · Comcast',
    verified: () => true, basisOf: () => 'receipt'
  });
  const { sourceScope, gapNotice, withGap } = vm.runInContext(
    slice('chart-studio.js', '  function sourceScope(', '  // The charts.js family on the verified path') + '\n({sourceScope, gapNotice, withGap})', context);
  const query = {dataset: 'Telecom 2025-2026', basis: 'receipt', filters: [{field: 'company', op: 'eq', value: 'Comcast'},
    {field: 'cycle', op: 'eq', value: '2025-2026'}]};
  const result = {provenance: {workbook: 'Telecom Industry Spending Overview 2025-2026.xlsx', workbook_sha256: 'a'.repeat(64)},
    offset: 0, rows: [{}], group_count: 1};
  const found = gapNotice([query]);
  assert.match(found.caption, /^Comcast 2025-26: 77% of FEC-reported records \(11,753 of 15,269 contributions\)/);
  const surface = {state: {id: 's1', name: 'Sheet 1', source: {reportName: 'Telecom'}}, title: 'Net giving by cycle', coverage: found,
    scope: withGap(sourceScope(result, query), found), el: id => ({textContent: id === 'note' ? 'A note.' : '', querySelector: () => ({tag: 'svg'})})};
  Object.assign(context, {activeSurface: () => surface});
  const { currentChart } = vm.runInContext(
    slice('chart-studio.js', '  function currentChart(view) {', '  // The clipboard gets the same drawing') + '\n({currentChart})', context);
  const chart = currentChart();
  for (const text of [chart.scope, chart.chip]) assert.match(text, /77% of FEC-reported records/, text);
  assert.match(chart.scope, /Telecom Industry Spending Overview 2025-2026\.xlsx/, 'the provenance string is still there');
  assert.equal(chart.chip, 'Telecom 2025-2026 · 2025-2026 · Comcast · Coverage gap: ' + found.caption);
  // The same view with no gap prints exactly what it printed before.
  surface.coverage = null;
  assert.equal(currentChart().chip, 'Telecom 2025-2026 · 2025-2026 · Comcast');
});

test('Chart Studio: a comparison names every compared dataset\'s cells once', () => {
  const context = studioContext();
  Object.assign(context, {contributionCatalog: {datasets: [
    {id: 'Telecom 2025-2026', collection_gaps: GAPS}, {id: 'Telecom 2023-2024', collection_gaps: GAPS}, {id: 'Banking', collection_gaps: undefined}]}});
  const { gapNotice } = vm.runInContext(
    slice('chart-studio.js', '  function sourceScope(', '  // The charts.js family on the verified path') + '\n({gapNotice})', context);
  const found = gapNotice([{dataset: 'Telecom 2025-2026', filters: []}, {dataset: 'Telecom 2023-2024', filters: []}, {dataset: 'Banking', filters: []}]);
  assert.equal(found.cells.length, 12);
  assert.equal(gapNotice([{dataset: 'Banking', filters: []}]), null);
});

test('Chart Studio: the Coverage gap chip joins the scope chips for the selected chart, and leaves with it', () => {
  const context = studioContext();
  const host = {children: [], querySelectorAll(selector) { return this.children.filter(c => selector === '.coverage-gap-chip' && /coverage-gap-chip/.test(c.className)); },
    append(...added) { this.children.push(...added); }};
  const attach = node => { node.remove = () => { host.children.splice(host.children.indexOf(node), 1); }; return node; };
  for (const child of ['a', 'b']) host.children.push(attach({className: '', textContent: child}));
  let isVerified = true;
  Object.assign(context, {lastCoverage: null, verified: () => isVerified, el: id => (id === 'scope-chip' ? host : null),
    document: {createElement: tag => attach({tag, className: '', textContent: '', title: ''})}});
  const { setCoverage } = vm.runInContext(
    slice('chart-studio.js', '  function paintGapChip(host) {', '  function renderScopeChip() {') + '\n({setCoverage})', context);
  setCoverage(notice({companies: ['Comcast'], cycles: ['2025-2026']}));
  assert.deepEqual(host.children.map(c => c.textContent), ['a', 'b', 'Coverage gap']);
  setCoverage(notice({companies: ['Verizon'], cycles: ['2025-2026']}));
  assert.equal(host.children.filter(c => /coverage-gap-chip/.test(c.className)).length, 1, 'repainting never stacks chips');
  setCoverage(null);
  assert.deepEqual(host.children.map(c => c.textContent), ['a', 'b']);
  isVerified = false;
  setCoverage(notice({companies: null, cycles: null}));
  assert.deepEqual(host.children.map(c => c.textContent), ['a', 'b'], 'a worksheet source shows no verified-contribution chip');
});

test('Chart Studio: the verified CSV gets a coverage_gap column only when there is a gap', () => {
  const contributions = require('../chart-studio-contributions.js');
  const result = {columns: [{id: 'cycle'}, {id: 'amount', unit: 'cents'}], rows: [{cycle: '2025-2026', amount: 12300}],
    provenance: {workbook_sha256: 'w', database_sha256: 'd', index_token: 'i', query_engine_sha256: 'q', field_semantics_sha256: 'f'}};
  const request = {basis: 'receipt', filters: [], aggregate_filters: []};
  const plain = contributions.csv(result, request);
  assert.ok(!plain.includes('coverage_gap'));
  assert.equal(contributions.csv(result, request, ''), plain);
  const lines = contributions.csv(result, request, 'Coverage gap: ' + COMCAST_2026).split('\r\n');
  assert.match(lines[0], /,"coverage_gap"$/);
  assert.match(lines[1], /,"Coverage gap: Comcast 2025-26: 77% of FEC-reported records \(11,753 of 15,269 contributions\)"$/);
});

/* ----------------------------------------------------------------------- Graph */

function graphContext(view, compare = null, compareAll = null) {
  const context = studioContext();
  const nodes = {};
  const get = id => nodes[id] || (nodes[id] = {textContent: '', value: '', selectedOptions: [{textContent: 'Direct contributions'}], appended: [],
    querySelector: () => ({tag: 'svg'}), querySelectorAll: () => [1, 2, 3], replaceChildren() {}, append(...added) { this.appended.push(...added); }});
  const element = tag => ({tag, className: '', textContent: '', title: '', children: [], append(...added) { this.children.push(...added); }});
  Object.assign(context, {view, compare, compareAll, get, compareScopes: () => (compare ? compare.scopes : []),
    titleFor: {companies: 'Company comparison'}, titleOf: name => ({companies: 'Company comparison'})[name], compareIndustries: () => '',
    COMPARE_IMAGE_NOTE: 'x', document: {createElement: element}});
  nodes['export-target'] = {value: 'companies'};
  nodes.chips = {appended: [], querySelectorAll: () => [], append(...added) { this.appended.push(...added); }};
  return {context, nodes};
}
const graphView = (filters, extra = {}) => ({filters: {mode: 'direct', direction: 'Support', ...filters}, options: {cycles: ['2026']},
  collection_gaps: {...GAPS, cells: GAPS.cells.filter(c => c.cycle === '2025-2026')}, note: 'Included net giving.', candidate_note: 'n',
  candidate_available: true, companies: [{}, {}, {}], reports: [{name: 'Telecom Industry Spending Overview 2025-2026.xlsx', sha256: 'c'.repeat(64)}],
  ...extra});

test('Graph: the scope line every export quotes carries the gap, so the image, copy and briefing context do too', () => {
  const view = graphView({company: 'Comcast', cycle: '2026'});
  const { context, nodes } = graphContext(view);
  const code = slice('graph.js', '  function coverageNotice() {', '  function render() {') +
    slice('graph.js', '  function exportSubject() {', "  get('export').onclick=");
  const { paintScope, exportSubject } = vm.runInContext(code + '\n({paintScope, exportSubject})', context);
  paintScope('Telecom · Comcast · All levels · All giving · 2025–2026');
  assert.match(nodes.scope.textContent, /^Telecom · Comcast .* · Coverage gap: Comcast 2025-26: 77% of FEC-reported records/);
  const chips = nodes.chips.appended;
  assert.equal(chips.length, 1);
  assert.equal(chips[0].children[0].textContent, 'Coverage gap');
  assert.match(chips[0].children[1].textContent, /^Comcast 2025-26: 77% of FEC-reported records \(11,753 of 15,269 contributions\); 19 more/);
  const subject = exportSubject();
  assert.match(subject.context, /^Telecom · Comcast .*77% of FEC-reported records \(11,753 of 15,269 contributions\)/);
  assert.match(subject.context, /Included net giving\./);
  assert.match(subject.chip, /77% of FEC-reported records/, 'the briefing chip is the scope line');
  // Another company in the same cycle: Verizon's cell, not Comcast's.
  view.filters.company = 'Verizon';
  paintScope('Telecom · Verizon');
  assert.match(nodes.scope.textContent, /Verizon 2025-26: 93%/);
  assert.doesNotMatch(nodes.scope.textContent, /Comcast 2025-26/);
});

test('Graph: a comparison reads the gaps of every scope it draws, the all-company ranking included', () => {
  const cycleView = graphView({company: '', cycle: '2026'});
  const compare = {dim: 'company', scopes: [
    {data: graphView({company: 'Comcast', cycle: '2026'})}, {data: graphView({company: 'Altice USA', cycle: '2026'})}, {data: null}]};
  const everyone = {data: cycleView};
  const { context, nodes } = graphContext(compare.scopes[0].data, compare, everyone);
  const code = slice('graph.js', '  function coverageNotice() {', '  function render() {');
  const { paintScope } = vm.runInContext(code + '\n({paintScope})', context);
  paintScope('Telecom · comparing');
  assert.match(nodes.scope.textContent, /Comcast 2025-26: 77%/);
  assert.match(nodes.scope.textContent, /Verizon 2025-26: 93%/, 'the all-company Company comparison card is a scope too');
});

test('Graph: a clean report adds nothing to the scope line', () => {
  const view = graphView({company: 'Comcast', cycle: '2026'}, {collection_gaps: {failed: false, cells: [], unitemized: 0, source: null}});
  const { context, nodes } = graphContext(view);
  const { paintScope } = vm.runInContext(slice('graph.js', '  function coverageNotice() {', '  function render() {') + '\n({paintScope})', context);
  paintScope('Telecom · Comcast');
  assert.equal(nodes.scope.textContent, 'Telecom · Comcast');
  assert.deepEqual(nodes.chips.appended, [], 'no chip when the scope touches nothing short');
});

test('Graph: the CSV carries the scope line, and with it the gap', () => {
  const view = graphView({company: 'Comcast', cycle: '2026'});
  const { context, nodes } = graphContext(view);
  const blobs = [];
  Object.assign(context, {URL: {createObjectURL: blob => { blobs.push(blob.parts.join('')); return 'blob:x'; }, revokeObjectURL() {}},
    Blob: class { constructor(parts) { this.parts = parts; } }, setTimeout() {},
    document: {createElement: tag => ({tag, className: '', textContent: '', title: '', children: [], click() {}, append(...added) { this.children.push(...added); }})},
    dollars: v => String(v)});
  view.companies = [{label: 'Comcast', amount: 1, id: 'Comcast'}];
  const { paintScope, exportChart } = vm.runInContext(slice('graph.js', '  function coverageNotice() {', '  function render() {') +
    slice('graph.js', '  function exportChart(name) {', "  get('run').onchange=loadCatalog;") + '\n({paintScope, exportChart})', context);
  paintScope('Telecom · Comcast');
  exportChart('companies');
  assert.match(blobs[0], /"Scope","Telecom · Comcast · Coverage gap: Comcast 2025-26: 77% of FEC-reported records \(11,753 of 15,269 contributions\)/);
  assert.equal(nodes.scope.textContent.includes('77%'), true);
});

/* -------------------------------------------------------------------- Insights */

test('Insights: the caption joins the scope-and-coverage notices that the screen, the CSV and the JSON all print', () => {
  globalThis.MentatCoverage = coverage;
  const insights = require('../insights.js');
  const catalog = {datasets: [{id: 'Telecom 2025-2026', collection_gaps: {...GAPS, cells: GAPS.cells.filter(c => c.cycle === '2025-2026')}}]};
  const result = {scope: {dataset: 'Telecom 2025-2026', company: 'Comcast', baseline_cycle: '2023-2024', current_cycle: '2025-2026', basis: 'receipt'},
    provenance: {}, notices: ['Server notice.'], panels: {favorites: {status: 'ready', columns: [{key: 'recipient', type: 'text'}], rows: [{recipient: 'A'}]}}};
  const found = insights.withCoverage(result, catalog);
  assert.notEqual(found, result, 'the answer is extended, not mutated');
  assert.deepEqual(result.notices, ['Server notice.']);
  assert.equal(found.notices[0], 'Server notice.');
  assert.match(found.notices[1], /^Coverage gap: Comcast 2025-26: 77% of FEC-reported records/);
  assert.equal(insights.withCoverage(found, catalog), found, 'extending twice adds it once');
  assert.match(insights.exportCSV(found), /77% of FEC-reported records \(11,753 of 15,269 contributions\)/);
  assert.equal(insights.withCoverage({...result, scope: {...result.scope, company: 'Frontier Communications'}}, {datasets: [{id: 'Telecom 2025-2026',
    collection_gaps: {...GAPS, cells: [CELLS[6]], unitemized: 0}}]}).notices.length, 1, 'a company with no short cell and nothing unlisted adds nothing');
  assert.match(insights.coverageNotice(result, catalog).sources.join(), /Data Quality sheet/);
  // The period summary names the company and the two compared cycles; the notices and card source lines
  // keep every cycle, because the trends panel draws them all.
  const every = {datasets: [{id: 'Telecom 2025-2026', collection_gaps: GAPS}]};
  assert.match(insights.coverageNotice(result, every).caption, /Comcast 2021-22: 78%/);
  const compared = insights.coverageNotice(result, every, true);
  assert.doesNotMatch(compared.caption, /2021-22/);
  assert.match(compared.caption, /Comcast 2023-24: 60% .*Comcast 2025-26: 77%/);
  assert.equal(insights.coverageNotice({...result, scope: {...result.scope, baseline_cycle: '', current_cycle: ''}}, every, true).cells.length, 5,
    'with no resolved periods the summary falls back to every cycle');
});

/* ------------------------------------------------------------------- Briefing */

test('Briefing: the record a chart becomes keeps the gap in the scope and the chip every export prints', () => {
  const model = require('../chart-briefing.js');
  const found = notice({companies: ['Comcast'], cycles: ['2025-2026']});
  const chart = {svg: {tag: 'svg'}, title: 'Net giving by cycle', scope: 'Telecom 2025-2026 · provenance · Coverage gap: ' + found.caption,
    chip: 'Telecom 2025-2026 · Comcast · Coverage gap: ' + found.caption, note: '', basis: 'Published included receipt view'};
  const context = vm.createContext({root: {MentatExport: {compose: () => ({svg: {}, width: 1000, height: 600})}},
    XMLSerializer: class { serializeToString() { return '<svg/>'; } },
    sourceRecord: model.sourceRecord, provenanceRecord: model.provenanceRecord});
  const { describe } = vm.runInContext(slice('chart-briefing.js', '  function describe(chart) {', '  async function capture(chart) {') + '\n({describe})', context);
  const record = describe(chart);
  for (const text of [record.scope, record.chip]) assert.match(text, /77% of FEC-reported records/);
});

test('Briefing: print, HTML, slide, slide notes and the drawer all print the chip, else the scope', () => {
  const code = stripComments(read('chart-briefing.js'));
  assert.equal((code.match(/item\.chip \|\| item\.scope/g) || []).length, 5, 'drawer line, print sheet, HTML sheet, HTML table, slide');
  assert.match(code, /item\.chip \|\| '',\s*item\.scope \|\| ''/, 'the speaker notes carry both');
});

test('Briefing: a slide leaves a short scope line its old layout and gives a long Coverage gap line the room it needs', () => {
  const model = require('../chart-briefing.js');
  assert.deepEqual(model.slideLayout('Telecom 2025-2026 · Comcast'), {pictureH: 5, ruleY: 6.15, scopeY: 6.3, scopeH: 0.35});
  assert.deepEqual(model.slideLayout(''), model.slideLayout('x'.repeat(330)));
  const everything = notice({companies: null, cycles: null}).text;
  assert.ok(everything.length > 1000);
  const long = model.slideLayout('Telecom · ' + everything);
  assert.ok(long.pictureH < 5 && long.pictureH >= 4.3 && long.scopeH > 0.35);
  for (const text of ['', 'a', 'x'.repeat(400), everything, everything.repeat(3)]) {
    const fit = model.slideLayout(text);
    assert.ok(Math.abs(fit.scopeY + fit.scopeH - 6.65) < 1e-9, 'the scope box always ends where the source note begins');
    assert.ok(Math.abs(fit.ruleY + 0.15 - fit.scopeY) < 1e-9);
    assert.ok(fit.pictureH >= 4.3);
  }
  const code = stripComments(read('chart-briefing.js'));
  assert.match(code, /const scopeLine = item\.chip \|\| item\.scope \|\| '', fit = slideLayout\(scopeLine\);/);
  assert.match(code, /slide\.addText\(scope\.text,\s*\{x: 0\.5, y: fit\.scopeY, w: 12\.3, h: fit\.scopeH, fontSize: scope\.fontSize/);
});

test('Briefing: a long scope or Coverage gap caption never runs on into the source note under it', () => {
  const model = require('../chart-briefing.js');
  const everything = notice({companies: null, cycles: null}, {...GAPS, unitemized: 21}).text;   // 12 cells and "21 more"
  // The all-company Telecom scope line: the scope chips, then the gap caption (about 1,300 characters in all).
  const caption = 'Telecom · ' + everything + ' · ' + 'Compared with the 2021-22 and 2023-24 cycles in the same industry. '.repeat(4);
  assert.ok(caption.length > 1250 && caption.length < 1400, `${caption.length} characters`);
  const layout = model.slideLayout(caption);
  // What the 9 pt face needs for it (about 165 characters a line, 0.15 in a line) is more than the box has: the old overflow.
  assert.ok(Math.ceil(caption.length / 165) * 0.15 > layout.scopeH + 1e-9, 'at 9 pt the caption overflows its box');
  // The size the caption is printed at, measured the same way for any face: characters a line, line height.
  const needed = scope => Math.ceil(scope.text.length / Math.floor(165 * 9 / scope.fontSize)) * scope.fontSize * 1.2 / 72;
  const fitted = model.slideScope(caption, layout);
  assert.ok(fitted.fontSize < 9 && fitted.fontSize >= 7, `shrunk to ${fitted.fontSize} pt`);
  assert.equal(fitted.truncated, false);
  assert.equal(fitted.text, caption.trim());
  assert.ok(needed(fitted) <= layout.scopeH + 1e-9, 'the shrunk caption fits its box');
  assert.ok(layout.scopeY + layout.scopeH <= 6.8 - 0.15 + 1e-9, 'and the box ends before the source note at 6.8 in');
  // A short line keeps its 9 pt face, untouched.
  assert.deepEqual(model.slideScope('Telecom 2025-2026 · Comcast', model.slideLayout('Telecom 2025-2026 · Comcast')),
    {fontSize: 9, text: 'Telecom 2025-2026 · Comcast', truncated: false});
  // Past the 7 pt floor the slide gets a cut caption that says where the whole one is.
  for (const text of [caption.repeat(2), caption.repeat(5), 'x'.repeat(5000), `${caption}\n${caption}\n${caption}`]) {
    const cut = model.slideScope(text, model.slideLayout(text));
    assert.equal(cut.truncated, true);
    assert.equal(cut.fontSize, 7);
    assert.ok(cut.text.endsWith(model.SCOPE_CUT) && model.SCOPE_CUT.startsWith('… (full coverage note'), cut.text.slice(-80));
    assert.ok(needed(cut) <= model.slideLayout(text).scopeH + 1e-9, 'the cut caption fits its box');
    assert.ok(cut.text.length < text.length && !cut.text.includes('\n'));
  }
  // Whatever its length, the caption on the slide fits the box it is drawn in, and is whole until the floor.
  for (let length = 0; length <= 4200; length += 37) {
    const text = ('Comcast 2025-26: 77% of FEC-reported records; ').repeat(100).slice(0, length);
    const shown = model.slideScope(text, model.slideLayout(text));
    assert.ok(needed(shown) <= model.slideLayout(text).scopeH + 1e-9 || text.length === 0, `length ${length}`);
    assert.ok(shown.fontSize >= 7 && shown.fontSize <= 9);
    if (!shown.truncated) assert.equal(shown.text, text.trim());
  }
  // The slide draws that text at that size, and the speaker notes keep the whole caption (chip, then scope).
  const code = stripComments(read('chart-briefing.js'));
  assert.match(code, /const scope = slideScope\(scopeLine, fit\);/);
  assert.match(code, /item\.chip \|\| '',\s*item\.scope \|\| ''/);
});

test('Print and the image header read the same scope the view built', () => {
  const code = stripComments(read('chart-studio.js'));
  assert.match(code, /el\('print-scope'\)\.textContent = \[chart\.scope, chart\.note\]/);
  assert.match(code, /MentatExport\.copy\(chart\.svg, chart\.title, chart\.scope\)/);
  assert.match(code, /MentatExport\.image\(svg, format, view\.title, view\.scope, 'mentat-chart-studio'\)/);
});
