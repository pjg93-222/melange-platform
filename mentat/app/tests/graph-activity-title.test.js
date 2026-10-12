/* Downstream activity is not the industry's giving (Fix 5, P1).

   The Candidate view "Downstream PAC activity" ranks the activity of every
   committee the industry's money reached. It used to be drawn, exported and
   captioned as "Top candidates", which reads as the industry's own giving
   (Telecom 2025-26: Bill Hagerty $142,494 reached against $100 direct, Jack
   Reed $153,994 reached against $0). In that view every title and caption now
   says what it measures, and each amount sits beside the industry's own direct
   amount, which the server attaches to the row.

   graph.js is a browser file, so the DOM-bound pieces are sliced out of the
   shipped source (anchors below) and run in a vm against a small fake DOM; the
   pure pieces are required. Run: node --test mentat/app/tests/graph-activity-title.test.js */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const graph = require('../graph.js');
const theme = require('../theme.js');

const TITLE = "Activity of committees that received Telecom money, not Telecom's own giving";
const source = fs.readFileSync(path.join(__dirname, '..', 'graph.js'), 'utf8');

/* --------------------------------------------------------------- the pure part */
test('the title names the industry and says whose money it is not', () => {
  assert.equal(graph.activityTitle('Telecom'), TITLE);
  assert.equal(graph.activityTitle('Telecom 2025-2026'), TITLE, 'a report name with its cycle is the industry alone');
  assert.equal(graph.activityTitle('Telecom 2025–2026'), TITLE);
  assert.equal(graph.activityTitle('Credit Union'), "Activity of committees that received Credit Union money, not Credit Union's own giving");
  assert.equal(graph.activityTitle(['Telecom', 'Banking', 'Telecom']),
    "Activity of committees that received Telecom / Banking money, not Telecom / Banking's own giving",
    'a comparison across industries names each one once');
});

test('only the activity answer is an activity answer', () => {
  assert.equal(graph.isActivity({filters: {mode: 'activity'}}), true);
  for (const data of [null, undefined, {}, {filters: {}}, {filters: {mode: 'direct'}}, {filters: {mode: 'estimates'}}])
    assert.equal(graph.isActivity(data), false);
});

test('reached sits beside direct, and a row with no direct keeps its one amount', () => {
  const money = n => '$' + Number(n).toLocaleString('en-US');
  assert.equal(graph.reachedText({amount: 142494, direct: 100}, '$142,494.00', n => '$' + Number(n).toFixed(2)),
    '$142,494.00 reached · $100.00 direct');
  assert.equal(graph.reachedText({amount: 153994, direct: 0}, '$153,994', money), '$153,994 reached · $0 direct');
  assert.equal(graph.reachedText({amount: 5}, '$5', money), '$5');
  assert.equal(graph.reachedText({amount: 5, direct: null}, '$5', money), '$5');
  assert.deepEqual(graph.reachedForms({amount: 142494, direct: 100}, money),
    ['$142,494 reached · $100 direct', '$142,494 · $100 direct', '$142,494']);
  assert.deepEqual(graph.reachedForms({amount: 142494}, money), ['$142,494']);
});

/* ----------------------------------------------------- the browser part, sliced */
class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.textContent = ''; this.value = ''; this.hidden = false;
    this.clientWidth = 920; this.className = '';
    this.style = {setProperty() {}, removeProperty() {}};
    this.classList = {add() {}, remove() {}, toggle() {}, contains: () => false};
  }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key]; }
  removeAttribute(key) { delete this.attrs[key]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, handler) { (this.listeners = this.listeners || {})[type] = handler; }
  closest() { return null; }
  find(tag, test) {
    const found = [];
    const walk = node => { for (const child of node.children) { if (child.tag === tag && (!test || test(child))) found.push(child); walk(child); } };
    walk(this);
    return found;
  }
}
const select = text => ({selectedOptions: [{textContent: text}], value: ''});

const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start + from.length);
  assert.ok(start >= 0 && end > start, 'the shipped graph.js still has ' + JSON.stringify(from.trim()) + ' ... ' + JSON.stringify(to.trim()));
  return source.slice(start, end);
};
// Everything graph.js defines before it asks for a document: the pure geometry and models.
const PURE = between("'use strict';", "  if (typeof document === 'undefined') return;");
const SLICES = [
  between('  const dollars = n =>', "  let analysis = 'overview';"),
  between('  function fitForm(', '  function fitCompareRow('),
  between('  function svgNode(', '  // Item 11: employee level is one stacked bar'),
  between('  function labelValues(', "  document.querySelectorAll('[data-analysis-tab]')"),
  between("  let unresolvedNote='',serverNote=''", '  function applyAnalysis() {'),
  between('  function exportSubject() {', "  get('export').onclick="),
  between('  function exportChart(name) {', "  get('run').onchange="),
  between('  function rowId(name,row)', '  // The per-row marks a ranked half carries'),
  between('  function tableRows(kind,scopes) {', '  function tableFilters('),
  between('  function tableApply(rows,scopes) {', '  function signedDollars('),
  between('  const TABLE_TEXT={', '  const TABLE_PAGE='),
  between('  function exportCompareTable() {', "  if(get('compare-add')){")
].join('\n');

function harness() {
  const csv = [];
  const nodes = {};
  const state = {};
  const node = name => nodes[name] || (nodes[name] = new Node('div'));
  const context = {
    MentatTheme: theme, console, URLSearchParams, Map, Set, Math, Number, String, Object, Array, JSON, Boolean, Infinity,
    document: {
      createElementNS: (ns, tag) => new Node(tag),
      createElement: tag => {
        const element = new Node(tag);
        if (tag === 'a') element.click = () => { state.download = element.download; };
        return element;
      },
      getElementById: () => null, querySelector: () => null, querySelectorAll: () => []
    },
    Blob: class { constructor(parts) { this.text = parts.join(''); } },
    URL: {createObjectURL: blob => { csv.push(blob.text); return 'blob:x'; }, revokeObjectURL() {}},
    setTimeout() {}, getComputedStyle: () => ({getPropertyValue: () => '0ms'}),
    MentatExport: {}, node, state
  };
  vm.createContext(context);
  const code = `(function () {${PURE}
    let view = null, compare = null, compareAll = null, category = '', recipient = '', analysisName = 'candidates';
    let scopes = [];
    const get = name => node(name);
    const compareScopes = () => scopes;
    const table = {filter: 'all', query: '', sort: 's0', dir: 'desc', page: 0};
    const DIM_CAPTION = {industry: 'Industry', cycle: 'Cycle', company: 'Company'};
    const tableKind = () => 'candidates';
    ${SLICES}
    return {set: (key, value) => { ({view: () => { view = value; }, compare: () => { compare = value; },
      compareAll: () => { compareAll = value; }, scopes: () => { scopes = value; }})[key](); },
      draw, exportSubject, exportChart, exportCompareTable, candidateAside, titleOf, appendValues};
  })()`;
  const api = vm.runInContext(code, context);
  return {api, csv, nodes, node, state};
}

const HAGERTY = {id: 'S0TN00169', label: 'HAGERTY, BILL', party: 'R', amount: 142494, direct: 100};
const REED = {id: 'S6RI00163', label: 'REED, JOHN F.', party: 'D', amount: 153994, direct: 0};
const answer = (mode, candidates) => ({
  filters: {mode, direction: 'Support', cycle: '2026', company: '', tier: '', funding: 'all'},
  candidate_available: true, candidate_note: 'Saved observed activity across reachable committees.', note: 'Included net giving.',
  candidates, categories: [{id: 'PAC', label: 'PAC', amount: 1}], reports: [{name: 'Telecom Industry Spending Overview.xlsx', sha256: 'abc'}],
  totals: {direct: 100}
});
const parse = text => text.replace(/^﻿/, '').split('\r\n').map(line => line.match(/"(?:[^"]|"")*"/g).map(cell => cell.slice(1, -1).replaceAll('""', '"')));

function prepared(mode, rows, extra = {}) {
  const h = harness();
  h.node('industry').value = 'Telecom';
  h.node('scope').textContent = 'Telecom · All companies · All levels · All giving · 2025–2026';
  h.node('mode').selectedOptions = [{textContent: mode === 'activity' ? 'Downstream PAC activity' : 'Direct contributions'}];
  h.node('mode').value = mode;
  h.node('export-target').value = 'candidates';
  const view = answer(mode, rows);
  h.api.set('view', view);
  const host = h.node('candidates');
  host.querySelector = selector => (selector === 'svg' ? host.children.find(child => child.tag === 'svg') || null : null);
  host.querySelectorAll = () => [];
  h.api.draw('candidates', view.candidates, () => {}, {foot: null, values: false, ...extra});
  return {h, view, host};
}

test('draw: the activity chart is titled for what it measures, and the tooltip and label carry the direct amount', () => {
  const {host} = prepared('activity', [REED, HAGERTY]);
  const svg = host.children[0];
  assert.equal(svg.attrs['aria-label'], TITLE);
  assert.equal(svg.find('title').find(node => node.textContent === TITLE).textContent, TITLE, 'the SVG <title>');
  const desc = svg.find('desc')[0].textContent;
  assert.ok(desc.startsWith(TITLE + '. Telecom · All companies'), 'the SVG <desc> opens with the title: ' + desc);
  const rows = svg.find('g');
  const hagerty = rows.find(row => /HAGERTY/.test(row.find('title')[0].textContent));
  assert.match(hagerty.find('title')[0].textContent, /^HAGERTY, BILL · R · \$142,494\.00 reached · \$100\.00 direct · /);
  assert.match(hagerty.attrs['aria-label'], /^HAGERTY, BILL · R: \$142,494\.00 reached · \$100\.00 direct$/);
  assert.equal(hagerty.find('text', t => t.attrs.class === 'graph-value')[0].textContent, '$142,494 reached · $100 direct');
  const reed = rows.find(row => /REED/.test(row.find('title')[0].textContent));
  assert.match(reed.find('title')[0].textContent, /\$153,994\.00 reached · \$0\.00 direct/);
  assert.equal(reed.find('text', t => t.attrs.class === 'graph-value')[0].textContent, '$153,994 reached · $0 direct');
});

test('draw: a direct chart is exactly what it was', () => {
  const {host} = prepared('direct', [{id: 'S0TN00169', label: 'HAGERTY, BILL', party: 'R', amount: 100}]);
  const svg = host.children[0];
  assert.equal(svg.attrs['aria-label'], 'Top candidates');
  assert.equal(svg.find('title')[0].textContent, 'Top candidates');
  assert.doesNotMatch(svg.find('desc')[0].textContent, /Activity of committees/);
  const row = svg.find('g')[0];
  assert.match(row.find('title')[0].textContent, /^HAGERTY, BILL · R · \$100\.00 · /);
  assert.doesNotMatch(row.find('title')[0].textContent + row.attrs['aria-label'], /reached|\$[\d,.]+ direct/);
  assert.equal(row.find('text', t => t.attrs.class === 'graph-value')[0].textContent, '$100.00');
});

test('draw: a row that is narrower than its label gives up words, never the amounts', () => {
  const h = harness();
  h.node('industry').value = 'Telecom'; h.node('scope').textContent = 'Telecom';
  h.api.set('view', answer('activity', [HAGERTY]));
  const narrow = new Node('div'); narrow.clientWidth = 420;   // the narrowest panel Graph draws
  h.api.draw('candidates', [HAGERTY], () => {}, {foot: null, values: false, container: narrow});
  const label = narrow.children[0].find('text', t => t.attrs.class === 'graph-value')[0].textContent;
  assert.ok(['$142,494 reached · $100 direct', '$142,494 · $100 direct', '$142,494'].includes(label), label);
});

function valuesTable(h, rows) {
  const container = new Node('div');
  h.api.appendValues(container, 'candidates', rows, () => {});
  const details = container.children[0];
  details.open = true;
  details.listeners.toggle();
  const table = details.children[1].children[0];
  return {head: table.children[0].innerHTML, rows: table.children[1].children.map(tr => tr.children.map(td => (td.children[0] ? td.children[0].textContent : td.textContent)))};
}

test('draw: a compare half keeps its one amount on the bar and puts the direct amount in the tooltip', () => {
  const h = harness();
  h.node('industry').value = 'Telecom'; h.node('scope').textContent = 'Telecom';
  const view = answer('activity', [HAGERTY]);
  h.api.set('view', view);
  const half = new Node('div'); half.clientWidth = 330;
  h.api.draw('candidates', [HAGERTY], () => {}, {data: view, foot: null, values: false, container: half, compact: true,
    money: n => '$' + Number(n).toLocaleString('en-US'), industry: 'Banking'});
  const svg = half.children[0];
  assert.equal(svg.attrs['aria-label'], "Activity of committees that received Banking money, not Banking's own giving");
  assert.equal(svg.find('text', t => t.attrs.class === 'graph-value')[0].textContent, '$142,494');
  assert.match(svg.find('g')[0].find('title')[0].textContent, /\$142,494\.00 reached · \$100\.00 direct/);
});

test('the All values table shows the direct amount in a column of its own in the activity view only', () => {
  const h = harness();
  const activity = valuesTable(h, [HAGERTY, REED]);
  assert.equal(activity.head, '<tr><th>Name</th><th>Net amount reached</th><th>Direct</th></tr>');
  assert.deepEqual(activity.rows, [['HAGERTY, BILL', '$142,494.00', '$100.00'], ['REED, JOHN F.', '$153,994.00', '$0.00']]);
  const plain = valuesTable(h, [{id: 'S0TN00169', label: 'HAGERTY, BILL', amount: 100}]);
  assert.equal(plain.head, '<tr><th>Name</th><th>Net amount</th></tr>');
  assert.deepEqual(plain.rows, [['HAGERTY, BILL', '$100.00']]);
});

test('exportSubject: the image, the copy and the briefing are titled for the activity', () => {
  const {h} = prepared('activity', [REED, HAGERTY]);
  const subject = h.api.exportSubject();
  assert.equal(subject.title, TITLE);
  assert.match(subject.context, /Downstream PAC activity\. Support\. Saved observed activity/);
  assert.equal(subject.chip, 'Telecom · All companies · All levels · All giving · 2025–2026 · ' + TITLE);
  assert.equal(subject.name, 'candidates');
  const direct = prepared('direct', [{id: 'S0TN00169', label: 'HAGERTY, BILL', amount: 100}]);
  const plain = direct.h.api.exportSubject();
  assert.equal(plain.title, 'Top candidates');
  assert.equal(plain.chip, 'Telecom · All companies · All levels · All giving · 2025–2026');
});

test('exportSubject: a panel that is not the candidates keeps its own title in the activity view', () => {
  const {h, view, host} = prepared('activity', [REED]);
  h.node('export-target').value = 'categories';
  const categories = h.node('categories');
  categories.querySelector = () => ({tag: 'svg'});
  categories.querySelectorAll = () => [];
  const subject = h.api.exportSubject();
  assert.equal(subject.title, 'Recipient types');
  assert.equal(subject.chip, 'Telecom · All companies · All levels · All giving · 2025–2026');
  assert.ok(view && host);
});

test('exportChart: the CSV names the activity and gives the direct amount in a column of its own', () => {
  const {h} = prepared('activity', [REED, HAGERTY]);
  h.api.exportChart('candidates');
  const lines = parse(h.csv[0]);
  assert.deepEqual(lines[0], ['Graph', TITLE]);
  assert.deepEqual(lines.find(line => line[0] === 'Candidate view'), ['Candidate view', 'Downstream PAC activity']);
  const at = lines.findIndex(line => line[0] === 'Name');
  assert.deepEqual(lines[at], ['Name', 'Reached (USD)', 'Direct (USD)', 'Identifier']);
  assert.deepEqual(lines[at + 1], ['REED, JOHN F.', '153994', '0', 'S6RI00163']);
  assert.deepEqual(lines[at + 2], ['HAGERTY, BILL', '142494', '100', 'S0TN00169']);
  assert.equal(h.state.download, 'mentat-candidates.csv');
});

test('exportChart: a direct CSV and the other cards are byte for byte what they were', () => {
  const {h} = prepared('direct', [{id: 'S0TN00169', label: 'HAGERTY, BILL', amount: 100}]);
  h.api.exportChart('candidates');
  const lines = parse(h.csv[0]);
  assert.deepEqual(lines[0], ['Graph', 'Top candidates']);
  const at = lines.findIndex(line => line[0] === 'Name');
  assert.deepEqual(lines[at], ['Name', 'USD', 'Identifier']);
  assert.deepEqual(lines[at + 1], ['HAGERTY, BILL', '100', 'S0TN00169']);
  const activity = prepared('activity', [REED]);
  activity.h.api.exportChart('categories');
  const other = parse(activity.h.csv[0]);
  assert.deepEqual(other[0], ['Graph', 'Recipient types']);
  assert.deepEqual(other[other.findIndex(line => line[0] === 'Name')], ['Name', 'USD', 'Identifier']);
});

test('exportChart: the comparison CSV names the activity and carries every scope’s direct amount', () => {
  const {h, view} = prepared('activity', [REED, HAGERTY]);
  const second = answer('activity', [{...HAGERTY, amount: 90000, direct: 250}]);
  h.api.set('compare', {dim: 'cycle', values: ['2026', '2024']});
  h.api.set('scopes', [
    {index: 0, label: '2025–2026', status: 'ready', data: view},
    {index: 1, label: '2023–2024', status: 'ready', data: second}]);
  h.api.exportChart('candidates');
  const lines = parse(h.csv[0]);
  assert.deepEqual(lines[0], ['Graph', TITLE]);
  const at = lines.findIndex(line => line[0] === 'Name');
  assert.deepEqual(lines[at], ['Name', '2025–2026 reached (USD)', '2023–2024 reached (USD)',
    '2025–2026 direct (USD)', '2023–2024 direct (USD)', 'Identifier']);
  assert.deepEqual(lines.slice(at + 1), [
    ['REED, JOHN F.', '153994', '', '0', '', 'S6RI00163'],
    ['HAGERTY, BILL', '142494', '90000', '100', '250', 'S0TN00169']]);
  assert.equal(h.state.download, 'mentat-compare-candidates.csv');
});

test('exportChart: comparing industries names every industry once', () => {
  const {h, view} = prepared('activity', [REED]);
  h.api.set('compare', {dim: 'industry', values: ['Telecom', 'Banking']});
  h.api.set('scopes', [{index: 0, label: 'Telecom', status: 'ready', data: view},
    {index: 1, label: 'Banking', status: 'ready', data: answer('activity', [HAGERTY])}]);
  h.api.exportChart('candidates');
  assert.deepEqual(parse(h.csv[0])[0],
    ['Graph', "Activity of committees that received Telecom / Banking money, not Telecom / Banking's own giving"]);
});

test('exportCompareTable: the drill-down CSV is titled and carries direct only in the activity view', () => {
  const {h, view} = prepared('activity', [REED, HAGERTY]);
  const second = answer('activity', [{...HAGERTY, amount: 90000, direct: 250}]);
  h.api.set('compare', {dim: 'cycle', values: ['2026', '2024']});
  h.api.set('scopes', [{index: 0, label: '2026', status: 'ready', data: view}, {index: 1, label: '2024', status: 'ready', data: second}]);
  h.api.exportCompareTable();
  const lines = parse(h.csv[0]);
  assert.deepEqual(lines[0], ['Graph', TITLE]);
  assert.deepEqual(lines[1], ['Candidate', 'Party', '2026 reached (USD)', '2024 reached (USD)', 'Difference', '2026 direct (USD)', '2024 direct (USD)']);
  assert.deepEqual(lines[3], ['HAGERTY, BILL', 'R', '142494', '90000', '52494', '100', '250']);
  const direct = prepared('direct', [{id: 'S0TN00169', label: 'HAGERTY, BILL', party: 'R', amount: 100}]);
  direct.h.api.set('compare', {dim: 'cycle', values: ['2026', '2024']});
  direct.h.api.set('scopes', [{index: 0, label: '2026', status: 'ready', data: direct.view}, {index: 1, label: '2024', status: 'ready', data: direct.view}]);
  direct.h.api.exportCompareTable();
  assert.deepEqual(parse(direct.h.csv[0])[0], ['Candidate', 'Party', '2026', '2024', 'Difference']);
});

test('the panel heading (and so the Image button, which prints the card’s own <h2>) says what the card measures', () => {
  const {h} = prepared('activity', [REED]);
  const heading = new Node('h2'); heading.textContent = 'Top candidates';
  const panel = h.node('candidate-panel');
  panel.querySelector = selector => (selector === ':scope>.panel-heading>h2' ? heading : null);
  h.node('candidate-note'); h.node('unresolved').setAttribute = () => {};
  h.api.candidateAside();
  assert.equal(heading.textContent, TITLE);
  h.api.set('compare', {dim: 'industry', values: ['Telecom 2025-2026', 'Banking']});
  h.api.candidateAside();
  assert.equal(heading.textContent, "Activity of committees that received Telecom / Banking money, not Telecom / Banking's own giving");
  h.api.set('compare', null);
  h.api.set('view', answer('direct', []));
  h.node('mode').value = 'direct';
  h.api.candidateAside();
  assert.equal(heading.textContent, 'Top candidates');
  h.api.set('view', null);
  h.api.candidateAside();
  assert.equal(heading.textContent, 'Top candidates', 'no answer yet, no claim');
});

/* ------------------------------------------------- the markup and the other callers */
const stripComments = text => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/([^:'"`\\])\/\/[^\n]*/g, '$1');

test('Graph asks for Direct contributions unless the reader chose otherwise, in every place it chooses', () => {
  const code = stripComments(source);
  assert.match(code, /const SCOPE_DEFAULTS=\{mode:'direct',/);
  assert.match(code, /get\('reset'\)\.onclick=\(\)=>\{[^}]*get\('mode'\)\.value='direct'/);
  assert.match(code, /get\('funding'\)\.value='all';get\('mode'\)\.value='direct';reportChosen=false/);
  assert.match(code, /mode:s\.mode\|\|'direct'/);
  assert.match(code, /name==='overview'&&get\('mode'\)\.value!=='direct'\)\{get\('mode'\)\.value='direct'/);
});

test('a compare half is titled for its own industry', () => {
  const code = stripComments(source);
  assert.match(code, /const options=\{data,container:body,foot:null,values:false,animate:grow,topN:aligned\?aligned\.length:top,caption,\s*industry:compare\.dim==='industry'\?scope\.value:undefined,/);
});

test('the image header for the Graph view takes the card’s own heading, with no title of its own to override it', () => {
  const figure = stripComments(fs.readFileSync(path.join(__dirname, '..', 'figure-pdf.js'), 'utf8'));
  const spec = figure.match(/\{view: 'graph', scope: \(\) => query\('#graph-scope'\)[^]*?host: '([^']*)'\}/);
  assert.ok(spec, 'the Graph panels spec is there');
  assert.ok(spec[0].split('host:')[0].indexOf('title:') < 0, 'no title override');
  assert.match(spec[1], /#graph-candidate-panel/);
  assert.match(spec[1], /#graph-compare-table/);
  assert.match(figure, /node = host\.querySelector\('h1, h2, h3, h4, figcaption'\)/);
});
