/* Chart Studio's Flow mark (lane F, board #46): the card model, the compile, the builder
   and the drawing, each with a negative control that has teeth.

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const core = require('../chart-studio-core.js');
const math = require('../chart-math.js');
const layout = require('../flow-layout.js');
const chart = require('../flow-chart.js');
const studioFlow = require('../studio-flow.js');

const clone = value => JSON.parse(JSON.stringify(value));
const p = (column, aggregation = 'sum') => ({column, aggregation});
const COMPANY = 0, TIER = 1, CATEGORY = 2, PARTY = 3, AMOUNT = 4, COUNT = 5, RAW = 6;
const COLUMNS = [
  {index: 0, name: 'Company', type: 'category'}, {index: 1, name: 'Employee Tier', type: 'category'},
  {index: 2, name: 'Recipient Category', type: 'category'}, {index: 3, name: 'Party', type: 'category'},
  {index: 4, name: 'Analysis Amount', type: 'number', unit: 'currency', accounting: {safe_to_sum: true}},
  {index: 5, name: 'Transaction Count', type: 'number'},
  {index: 6, name: 'Amount', type: 'number', unit: 'currency',
    accounting: {safe_to_sum: false, reason: 'Raw observations include excluded or repeated records. Choose Analysis Amount or the contribution dataset.'}}];
// Ten companies (so the top N rule has work), four tiers, three categories, three parties.
const COMPANIES = ['Coinbase', 'Ripple', 'Kraken', 'Gemini', 'Circle', 'Robinhood', 'Paxos', 'Anchorage', 'Bakkt', 'tZero'];
const TIERS = ['Chief', 'Middle Management', 'Employee', 'Corporate'];
const CATEGORIES = ['Company PAC', 'Candidate', 'Super PAC'];
const PARTIES = ['DEM', 'REP', 'Other / Unclassified'];
function worksheet() {
  const rows = [];
  COMPANIES.forEach((company, c) => {
    for (let i = 0; i < 24 - c * 2; i += 1) {
      const amount = Math.round((1000 * (10 - c) + i * 37.25) * 100) / 100;
      rows.push([company, TIERS[(c + i) % 4], CATEGORIES[(c * 3 + i) % 3], PARTIES[(c + 2 * i) % 3], amount, 1 + (i % 3), amount * 2]);
    }
  });
  rows.push(['Coinbase', null, 'Candidate', 'DEM', 250, 1, 250]);          // a blank stage member
  rows.push(['Ripple', 'Chief', 'Super PAC', 'REP', 'n/a', 1, 0]);          // no number
  rows.push(['Kraken', 'Employee', 'Candidate', 'IND', -5000, 1, -5000]);  // a refund on a path of its own
  rows.push(['Paxos', 'Chief', 'Candidate', 'LIB', 0, 1, 0]);             // a path that totals exactly $0
  return {sheet: '2023-2024', columns: COLUMNS, rows, row_numbers: rows.map((_, i) => i + 5), row_count: rows.length,
    accounting: {safe_to_sum: true, row_role_column: null, independent_dimensions: []}};
}
const DATA = worksheet();
const baseSheet = () => {
  const sheet = core.createSheet('Flow', 'flow-1');
  sheet.columns = [p(COMPANY), p(TIER), p(CATEGORY), p(PARTY)];
  sheet.marks = {type: 'flow', color: null, size: p(AMOUNT), label: null, detail: [], tooltip: []};
  core.writeCards(sheet, core.readCards(sheet));
  return sheet;
};
const compile = sheet => core.compileReport(sheet, COLUMNS);
const built = (sheet, data = DATA) => studioFlow.build(data, compile(sheet).config, {math});
const byId = payload => new Map(payload.nodes.map(node => [node.id, node]));
const cents = value => Math.round(value * 100);

/* ------------------------------------------------------------------ model */
test('[flow] a Flow card is a stored v3 card with its four options, written in the contract order', () => {
  const sheet = baseSheet();
  assert.equal(sheet.marksCards.length, 1);
  const card = sheet.marksCards[0];
  assert.deepEqual(card.flow, {ribbons: 'source', order: 'value', opacity: 40, groupAfter: 8});
  assert.equal(Object.keys(card).at(-1), 'flow');
  assert.equal(core.validateMarksCards(sheet), '');
  assert.equal(core.hasV3Fields(core.readCards(sheet)[0]), true);
  assert.equal(sheet.marks.type, 'flow', 'a console without v3 reads the type from marks');
});

test('[flow] the save rule refuses options on another card, a Flow card without them, and every bad option', () => {
  const sheet = baseSheet();
  const fault = mutate => { const copy = clone(sheet); mutate(copy.marksCards[0], copy); return core.validateMarksCards(copy); };
  assert.equal(fault(card => { card.type = 'bar'; }), 'flow options belong to a Flow card only');
  assert.equal(fault(card => { delete card.flow; }), 'a Flow card needs its flow options');
  assert.match(fault(card => { card.flow.ribbons = 'rainbow'; }), /ribbons/);
  assert.match(fault(card => { card.flow.order = 'random'; }), /order/);
  assert.match(fault(card => { card.flow.opacity = 140; }), /opacity/);
  assert.match(fault(card => { card.flow.groupAfter = 1; }), /groupAfter/);
  assert.match(fault(card => { card.flow.groupAfter = '8'; }), /groupAfter/);
  assert.match(fault(card => { card.flow.curve = 'sigmoid'; }), /ribbons, order, opacity and groupAfter/);
  assert.match(fault((card, copy) => {
    const second = clone(card); second.id = 'card-2'; second.type = 'line'; delete second.flow;
    copy.rows = [p(AMOUNT), p(COUNT)]; card.measure = p(AMOUNT); second.measure = p(COUNT); copy.marksCards.push(second);
  }), /a flow draws alone/);
  // Teeth: the sheet they were cut from passes, and a malformed set is dropped on open.
  assert.equal(core.validateMarksCards(sheet), '');
  const broken = clone(sheet); broken.marksCards[0].flow.ribbons = 'rainbow';
  const opened = core.migrateSheet(broken);
  assert.ok(!('marksCards' in opened), 'a malformed flow card is dropped on open');
  assert.equal(core.readCards(opened)[0].flow.ribbons, 'source', 'the sheet opens as a flow with default options');
});

test('[flow] choosing Flow moves dimensions to Stages and the measure to Size; leaving it puts Size back on Rows', () => {
  const sheet = core.createSheet('S', 's');
  sheet.columns = [p(COMPANY), p(AMOUNT)];
  sheet.rows = [p(PARTY)];
  assert.equal(core.flowSwitch(sheet, COLUMNS, 'flow'), true);
  assert.deepEqual(sheet.columns, [p(COMPANY), p(PARTY)]);
  assert.deepEqual(sheet.rows, []);
  assert.deepEqual(sheet.marks.size, p(AMOUNT));
  assert.equal(sheet.marksCards[0].type, 'flow');
  assert.equal(core.isFlowSheet(sheet), true);
  assert.equal(core.flowSwitch(sheet, COLUMNS, 'flow'), false, 'no boundary crossed');
  assert.equal(core.flowSwitch(sheet, COLUMNS, 'bar'), true);
  assert.deepEqual(sheet.rows, [p(AMOUNT)]);
  assert.equal(sheet.marks.size, null);
  assert.equal(sheet.marks.type, 'bar');
  assert.ok(!('marksCards' in sheet), 'leaving Flow leaves no flow options behind');
  assert.equal(core.isFlowSheet(sheet), false);
});

test('[flow] Swap reverses the stages, a click puts a dimension on Stages and a measure on Size', () => {
  const sheet = baseSheet();
  const swapped = core.swapShelves(sheet);
  assert.deepEqual(swapped.columns, [p(PARTY), p(CATEGORY), p(TIER), p(COMPANY)]);
  assert.deepEqual(swapped.rows, []);
  const dimension = {role: 'dimension'}, measure = {role: 'measure'};
  assert.equal(core.quickAddZone(dimension, sheet), 'columns');
  assert.equal(core.quickAddZone(measure, sheet), 'tooltip', 'Size is taken');
  sheet.marks.size = null;
  assert.equal(core.quickAddZone(measure, sheet), 'size');
  sheet.columns.push(p(PARTY));
  assert.equal(core.quickAddZone(dimension, sheet), 'tooltip', 'five stages already');
  const bar = core.createSheet('B', 'b');
  assert.equal(core.quickAddZone(dimension, bar), 'columns');
  assert.equal(core.quickAddZone(measure, bar), 'rows');
});

test('[flow] setFlowOptions keeps every other option and a known value only', () => {
  const sheet = baseSheet();
  core.setFlowOptions(sheet, {ribbons: 'gradient', opacity: 65});
  assert.deepEqual(sheet.marksCards[0].flow, {ribbons: 'gradient', order: 'value', opacity: 65, groupAfter: 8});
  core.setFlowOptions(sheet, {groupAfter: 1, order: 'alphabetical'});
  assert.deepEqual(sheet.marksCards[0].flow, {ribbons: 'gradient', order: 'alphabetical', opacity: 65, groupAfter: 8});
  assert.equal(core.validateMarksCards(sheet), '');
});

test('[flow] the Marks card greys what a flow does not draw, each with a sentence', () => {
  const support = core.encodingSupport('flow', 'worksheet');
  assert.deepEqual(Object.keys(support), ['color', 'size', 'label', 'detail', 'tooltip']);
  assert.equal(support.color, true); assert.equal(support.size, true); assert.equal(support.label, true);
  assert.equal(support.tooltip, true);
  assert.match(support.detail, /\.$/);
  for (const [encoding, role] of [['color', 'measure'], ['size', 'dimension'], ['label', 'dimension']])
    assert.match(String(core.roleSupport('flow', 'worksheet', encoding, role)), /flow.*\.$/);
  const styles = core.styleSupport('flow');
  assert.ok(['opacity', 'border', 'halo'].every(key => /Flow options/.test(styles[key])));
  assert.equal(core.flowReason('worksheet', false), '');
  assert.match(core.flowReason('verified', false), /Report worksheet/);
  assert.match(core.flowReason('worksheet', true), /Dual axis/);
});

test('[flow] the compile names every pill it cannot use, and needs two stages', () => {
  const sheet = baseSheet();
  sheet.columns = [p(COMPANY), p(AMOUNT), p(TIER), p(CATEGORY), p(PARTY), p(COMPANY), p(TIER)];
  sheet.marks.size = p(AMOUNT, 'mean');
  sheet.marks.color = p(COUNT);
  sheet.marks.detail = [p(PARTY)];
  const report = compile(sheet);
  assert.equal(report.markType, 'flow');
  assert.deepEqual(report.config.stages.map(stage => stage.column), [COMPANY, TIER, CATEGORY, PARTY, COMPANY]);
  assert.equal(report.config.measure.aggregation, 'sum');
  const notes = report.notes.join(' ');
  for (const words of ['Analysis Amount is not a stage', 'up to five stages', 'drawn as a sum', 'does not change them', 'Detail does not split'])
    assert.ok(notes.includes(words), words);
  sheet.rows = [p(COUNT)];
  assert.ok(compile(sheet).notes.join(' ').includes('Transaction Count on Rows is not drawn'), 'a field on Rows is named');
  sheet.rows = [];
  sheet.columns = [p(COMPANY)];
  const one = compile(sheet);
  assert.equal(one.config, null);
  assert.match(one.issues.join(' '), /two to five dimensions/);
});

/* ---------------------------------------------------------------- builder */
function conserved(payload) {
  const incoming = new Map(), outgoing = new Map();
  for (const link of payload.links) {
    outgoing.set(link.source, (outgoing.get(link.source) || 0) + cents(link.value));
    incoming.set(link.target, (incoming.get(link.target) || 0) + cents(link.value));
  }
  const last = payload.stages.length - 1;
  const problems = [];
  for (const node of payload.nodes) {
    const inflow = incoming.get(node.id) || 0, outflow = outgoing.get(node.id) || 0;
    if (node.stage > 0 && node.stage < last && inflow !== outflow) problems.push(`${node.id} in ${inflow} out ${outflow}`);
    if (node.stage > 0 && inflow !== cents(node.value_in)) problems.push(`${node.id} value_in`);
    if (node.stage < last && outflow !== cents(node.value_out)) problems.push(`${node.id} value_out`);
  }
  const stageTotal = stage => payload.nodes.filter(node => node.stage === stage)
    .reduce((sum, node) => sum + cents(stage ? node.value_in : node.value_out), 0);
  for (let stage = 1; stage <= last; stage += 1) if (stageTotal(stage) !== stageTotal(0)) problems.push(`stage ${stage} total`);
  return problems;
}

test('[flow] every node balances, every stage carries the same total, and it is the rows’ own sum', () => {
  const sheet = baseSheet();
  core.setFlowOptions(sheet, {groupAfter: 0});
  const {payload} = built(sheet);
  assert.deepEqual(conserved(payload), []);
  const rows = math.filterRows(DATA, {filters: [], lenient: true});
  const numeric = rows.map(row => math.numeric(row[AMOUNT])).filter(value => value !== null);
  const sum = numeric.reduce((total, value) => total + cents(value), 0);
  assert.equal(cents(payload.totals.rows_total), sum);
  assert.equal(payload.totals.excluded, 1, 'the row with no number');
  assert.equal(payload.totals.dropped_paths, 1, 'the refund path nets below zero');
  assert.equal(payload.totals.zero_paths, 1, 'the $0 path draws no ribbon and loses no money');
  assert.equal(cents(payload.totals.drawn), sum + 500000, 'drawn = the rows less the path a width cannot show');
  assert.equal(payload.totals.reconciles, false);
  assert.ok(byId(payload).has('s1:m:(Missing)'), 'a blank member is drawn as (Missing)');
  assert.equal(byId(payload).get('s1:m:(Missing)').missing, true);
});

test('[flow] the top N rule is flow-layout.js otherMembers, and Other lists its members', () => {
  const sheet = baseSheet();
  core.setFlowOptions(sheet, {groupAfter: 5});
  const {payload} = built(sheet);
  const every = built((() => { const s = baseSheet(); core.setFlowOptions(s, {groupAfter: 0}); return s; })()).payload;
  const column = every.nodes.filter(node => node.stage === 0)
    .map(node => ({id: node.label, label: node.label, kind: 'committee', vin: cents(node.value_out), vout: cents(node.value_out)}));
  const rest = layout.otherMembers(column, 5).map(node => node.id).sort();
  const other = byId(payload).get('s0:other');
  assert.ok(other, 'stage one has an Other node');
  assert.deepEqual(other.members.map(member => member.label).sort(), rest);
  assert.equal(other.member_count, rest.length);
  assert.equal(payload.nodes.filter(node => node.stage === 0).length, 5, 'four named companies and Other');
  assert.deepEqual(conserved(payload), []);
  assert.equal(payload.totals.drawn, every.totals.drawn, 'grouping moves money into Other, never out of the flow');
});

test('[flow] Colour splits each ribbon by its member without changing any total', () => {
  const plain = baseSheet();
  const coloured = baseSheet();
  coloured.marks.color = p(PARTY);
  core.writeCards(coloured, core.readCards(coloured));
  const a = built(plain), b = built(coloured);
  assert.equal(b.split.name, 'Party');
  assert.ok(b.payload.links.length > a.payload.links.length);
  const sums = links => {
    const out = new Map();
    for (const link of links) out.set(link.source + '>' + link.target, (out.get(link.source + '>' + link.target) || 0) + cents(link.value));
    return out;
  };
  assert.deepEqual(sums(b.payload.links), sums(a.payload.links));
  // Party is a stage: each piece takes its Party node's colour.
  assert.ok(b.payload.links.every(link => link.key_node && link.key_node.startsWith('s3:')));
  // Split ribbons between the same two nodes stack in one order on both ends.
  const firstPair = b.payload.links.filter(link => link.source === b.payload.links[0].source && link.target === b.payload.links[0].target);
  assert.ok(firstPair.length >= 2);
});

test('[flow] Outcome ribbons split by the last stage; a Colour field off the stages gets a legend', () => {
  const outcome = baseSheet();
  core.setFlowOptions(outcome, {ribbons: 'outcome'});
  const b = built(outcome);
  assert.equal(b.split.stage, 3);
  assert.ok(b.payload.links.every(link => link.key_node.startsWith('s3:')));
  const offStage = baseSheet();
  offStage.columns = [p(COMPANY), p(CATEGORY)];
  offStage.marks.color = p(TIER);
  core.writeCards(offStage, core.readCards(offStage));
  const c = built(offStage);
  assert.equal(c.split.stage, -1);
  assert.ok(c.payload.links.every(link => Number.isInteger(link.key_rank)));
  assert.ok(c.payload.links.some(link => link.details[0].startsWith('Employee Tier: ')));
});

test('[flow] Label prints a measure per node, Tooltip lists fields per ribbon, no Size counts rows', () => {
  const sheet = baseSheet();
  sheet.marks.label = p(COUNT);
  sheet.marks.tooltip = [p(PARTY), p(COUNT, 'max')];
  core.writeCards(sheet, core.readCards(sheet));
  const {payload} = built(sheet);
  const coinbase = byId(payload).get('s0:m:Coinbase');
  const expected = DATA.rows.filter(row => row[COMPANY] === 'Coinbase').reduce((sum, row) => sum + row[COUNT], 0);
  assert.equal(coinbase.amount_text, studioFlow.shortNumber(expected));
  assert.ok(coinbase.details[0].startsWith('SUM(Transaction Count): '));
  const link = payload.links[0];
  assert.ok(link.details.some(line => line.startsWith('Party: ')));
  assert.ok(link.details.some(line => line.startsWith('MAX(Transaction Count): ')));
  const rowsOnly = baseSheet();
  rowsOnly.marks.size = null;
  core.writeCards(rowsOnly, core.readCards(rowsOnly));
  const counted = built(rowsOnly).payload;
  assert.equal(counted.unit, 'number');
  assert.equal(counted.totals.drawn, DATA.rows.length, 'every selected row counts once');
});

test('[flow] a filter narrows the rows, and a measure not safe to sum is drawn with its reason and no total', () => {
  const sheet = baseSheet();
  sheet.filters = [{column: PARTY, op: 'in', value: ['DEM']}];
  const {payload} = built(sheet);
  assert.ok(payload.nodes.filter(node => node.stage === 3).every(node => node.label === 'DEM'));
  const raw = baseSheet();
  raw.marks.size = p(RAW);
  core.writeCards(raw, core.readCards(raw));
  const out = built(raw);
  assert.match(out.payload.totals.refusal, /Analysis Amount/);
  assert.equal(out.payload.totals.reconciles, false);
  assert.ok(out.notes.includes(out.payload.totals.refusal));
});

/* ---------------------------------------------------------------- drawing */
const drawOptions = (extra = {}) => Object.assign({width: 1000, height: 480, headers: false, labelSize: 18, subSize: 18,
  labelLine: 22, subLine: 22, labelLines: 1, minPadding: 8, margin: {top: 8, right: 8, bottom: 10, left: 8}, idPrefix: 't',
  palette: Array.from({length: 12}, (_, i) => '#' + (0x100000 + i * 0x0f0f0f).toString(16).slice(-6))}, extra);
const draw = (sheet, extra) => {
  const out = built(sheet);
  const options = drawOptions(Object.assign({format: studioFlow.formats(out.payload.unit),
    ribbons: (compile(sheet).config.options || {}).ribbons}, extra || {}));
  return chart.svgString(out.payload, options);
};
const visible = text => text.replace(/<title>.*?<\/title>/g, '').replace(/ (aria-label|data-node|data-link)="(?:[^"\\]|\\.)*"/g, '');

test('[flow] Colour, Size and Label change what is drawn; Tooltip changes what a ribbon says', () => {
  const base = baseSheet();
  const before = visible(draw(base));
  const colour = baseSheet(); colour.marks.color = p(PARTY); core.writeCards(colour, core.readCards(colour));
  const recolour = studioFlow.__test.recolourer(built(colour));
  assert.notEqual(visible(draw(colour, {recolour})), before, 'Colour');
  const size = baseSheet(); size.marks.size = p(COUNT); core.writeCards(size, core.readCards(size));
  assert.notEqual(visible(draw(size)), before, 'Size');
  const label = baseSheet(); label.marks.label = p(COUNT); core.writeCards(label, core.readCards(label));
  assert.notEqual(visible(draw(label)), before, 'Label');
  const tip = baseSheet(); tip.marks.tooltip = [p(PARTY)]; core.writeCards(tip, core.readCards(tip));
  const said = sheet => {
    const out = built(sheet);
    const context = {result: layout.layout(out.payload, drawOptions()), format: studioFlow.formats(out.payload.unit)};
    return chart.describe({link: context.result.links[0]}, context, out.payload).lines.join(' | ');
  };
  assert.notEqual(said(tip), said(base), 'Tooltip');
  assert.match(said(base), /worksheet rows?/);
});

test('[flow] the ribbon options draw differently, and opacity is the card’s', () => {
  const fills = ribbons => { const s = baseSheet(); core.setFlowOptions(s, {ribbons}); return draw(s).match(/class="flow-link[^"]*"[^>]*fill="[^"]+"/g).join(); };
  const source = fills('source'), target = fills('target'), gradient = fills('gradient');
  assert.notEqual(source, target); assert.notEqual(source, gradient); assert.match(gradient, /url\(#t-g-/);
  const sheet = baseSheet(); core.setFlowOptions(sheet, {opacity: 65});
  assert.match(draw(sheet, {opacity: 0.65}), /fill-opacity="0.65"/);
});

test('[flow] at 18px no two labels in a column meet', () => {
  const sheet = baseSheet();
  const out = built(sheet);
  const result = layout.layout(out.payload, drawOptions());
  for (const column of result.columns) {
    const nodes = column.ids.map(id => result.nodes.find(node => node.id === id)).sort((a, b) => a.label_y - b.label_y);
    for (let at = 1; at < nodes.length; at += 1)
      assert.ok(nodes[at].label_y >= nodes[at - 1].label_y + nodes[at - 1].block - 0.01,
        `${nodes[at - 1].label} and ${nodes[at].label} overlap`);
    assert.ok(nodes.every(node => node.label_y + node.block <= 480 + 0.01), 'inside the drawing');
  }
});

/* ------------------------------------------------------ negative controls */
function sabotaged(replace) {
  const file = path.join(__dirname, '..', 'studio-flow.js');
  let source = fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  for (const [from, to] of replace) { assert.ok(source.includes(from), 'the sabotage finds its line'); source = source.replace(from, to); }
  const module = {exports: {}};
  vm.runInNewContext(source, {module, require: name => require(path.join(__dirname, '..', name)), globalThis: {}, console});
  return module.exports;
}

test('[flow] negative controls: a builder that leaks a cent, or groups one member too few, is caught', () => {
  const leaky = sabotaged([['        link.cents += entry.cents;\n', '        link.cents += entry.cents + (index === 0 ? 1 : 0);\n']]);
  const sheet = baseSheet(); core.setFlowOptions(sheet, {groupAfter: 0});
  assert.notDeepEqual(conserved(leaky.build(DATA, compile(sheet).config, {math}).payload), [], 'a leak breaks conservation');
  const greedy = sabotaged([['layoutApi().otherMembers(column, top)', 'layoutApi().otherMembers(column, top + 1)']]);
  const five = baseSheet(); core.setFlowOptions(five, {groupAfter: 5});
  const other = byId(greedy.build(DATA, compile(five).config, {math}).payload).get('s0:other');
  const every = built((() => { const s = baseSheet(); core.setFlowOptions(s, {groupAfter: 0}); return s; })()).payload;
  const column = every.nodes.filter(node => node.stage === 0)
    .map(node => ({id: node.label, label: node.label, kind: 'committee', vin: cents(node.value_out), vout: cents(node.value_out)}));
  assert.notDeepEqual(other.members.map(member => member.label).sort(), layout.otherMembers(column, 5).map(node => node.id).sort(),
    'a different top N rule is not the PAC evidence Flow’s');
});

/* ---------------------------------------------------- studio_api.json */
const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const studioApi = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'studio_api.json'), 'utf8'));

test('[flow] studio_api: the Flow example is what the producers write, and every lane F control is refused and dropped on open', () => {
  const ex = studioApi.examples;
  const saved = ex.views_save_marks_flow.sheets[0];
  assert.deepEqual(core.serializeCard(core.readCards(saved)[0]), ex.marks_card_flow);
  assert.equal(core.validateMarksCards(saved), '');
  const again = clone(saved);
  core.writeCards(again, core.readCards(again));
  assert.equal(JSON.stringify(again), JSON.stringify(saved), 'written back byte-identical');
  assert.equal(JSON.stringify(core.migrateSheet(clone(saved))), JSON.stringify(core.migrateSheet(saved)));
  assert.deepEqual(ex.views_get_marks_flow.sheets, ex.views_save_marks_flow.sheets);
  assert.equal(saved.marks.type, 'flow', 'a console without Flow reads the v2 mirror');
  const controls = studioApi.negative_controls.filter(control => control.lane === 'F');
  assert.equal(controls.length, 9);
  for (const control of controls) {
    const sheet = control.payload.sheets[0];
    assert.ok(core.validateMarksCards(sheet), 'accepted: ' + control.why);
    assert.ok(!('marksCards' in core.migrateSheet(clone(sheet))), 'kept on open: ' + control.why);
  }
  // The example compiles to board #46's flow: four stages, Size SUM(Analysis Amount), Colour Company.
  const columns = Array.from({length: 35}, (_, index) => ({index, name: 'F' + index,
    type: [7, 8, 29, 30, 31, 32, 33].includes(index) ? 'number' : 'category'}));
  const config = core.compileReport(saved, columns).config;
  assert.deepEqual(config.stages.map(stage => stage.column), [4, 6, 10, 9]);
  assert.deepEqual([config.measure.column, config.measure.aggregation, config.colour.column], [8, 'sum', 4]);
  assert.deepEqual(config.options, {ribbons: 'source', order: 'value', opacity: 35, groupAfter: 8});
});

/* ------------------------------------------------------- lanes G1/G1b (#46) */
test('[flow] lane G1: the automatic title is board #46\u2019s - whose money, by stage', () => {
  const amount = {name: 'Analysis Amount', aggregation: 'sum'};
  const crypto = {reportName: 'Crypto Industry Spending Overview.xlsx', sheet: '2025-2026'};
  assert.equal(studioFlow.titleOf(crypto, amount, 'money'), 'Crypto money by stage');
  assert.equal(studioFlow.titleOf({reportName: 'Credit Union Industry Spending Overview.xlsx'}, amount, 'money'), 'Credit Union money by stage');
  assert.equal(studioFlow.titleOf({industry: 'Community Banks', reportName: 'Other.xlsx'}, amount, 'money'), 'Community Banks money by stage',
    'the industry a source names comes first');
  // Lane G1b: a file name that leads with no industry is no subject (the subtitle names the file already).
  for (const reportName of ['FAIRSHAKE network.xlsx', 'Giving.xlsx', 'workbook.xlsx', 'Industry Overview.xlsx'])
    assert.equal(studioFlow.titleOf({reportName}, amount, 'money'), 'Money by stage', reportName);
  assert.equal(studioFlow.titleOf({}, amount, 'money'), 'Money by stage');
  // A flow sized by anything but money names its measure: a sum by its field, a count by its function.
  assert.equal(studioFlow.titleOf(crypto, null, 'number'), 'Crypto: Row count by stage');
  assert.equal(studioFlow.titleOf(crypto, {name: 'Transaction Count', aggregation: 'sum'}, 'number'), 'Crypto: Transaction Count by stage');
  assert.equal(studioFlow.titleOf(crypto, {name: 'Recipient', aggregation: 'count'}, 'number'), 'Crypto: COUNT(Recipient) by stage');
  // This suite's compiled worksheet flow (Size SUM(Analysis Amount)) is a money flow and takes the board's words.
  const config = compile(baseSheet()).config;
  assert.equal(built(baseSheet()).payload.unit, 'money');
  assert.equal(studioFlow.titleOf(crypto, config.measure, 'money'), 'Crypto money by stage');
  assert.ok(!/SUM\(|· Flow/.test(studioFlow.titleOf(crypto, amount, 'money')), 'no SUM( and no mark name in the title');
});
