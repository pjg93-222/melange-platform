/* Legacy parity corpus for the r3 Chart Studio engine build (ENGINE CONTRACT,
   RULE 0: every new field is optional, and without one the engine returns
   today's results, notes, throws and SVG).

   run(dir) loads chart-math.js, grouped-chart.js, charts.js and
   contribution-plot.js from `dir` under a small deterministic DOM and returns
   [[id, output]] for a corpus of configurations that carry none of the r3
   fields: the classic dialog's quickConfig choices, funding-comparison's
   grouped views, the Chart Studio fixtures of chart-studio-core.test.js as
   they compiled before r3, the test_chart_math.cjs fixtures, and the verified
   renderer's specs. chart-engine-r3.golden.json holds the SHA-256 of each
   output as produced by the pre-r3 files, captured before the first edit.

   Ids starting with "changed:" are refusals that Paul's words retire ("nothing
   is logically constrained"): the verified renderer's size / colour / timeline
   refusals, and three values the contract makes legal (type 'dot', aggregation
   'countd', group limit 'all'). They are recorded so the before/after can be
   shown, and are excluded from the byte-identical check. */
'use strict';
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');

const THEME = Object.freeze({
  palette: ['#276749', '#477f9b', '#aa7942', '#805f87', '#69783e', '#ac6263', '#527c77', '#6f7499'],
  ink: '#202522', grid: '#e3e6e1', sans: 'Arial, sans-serif', serif: 'Georgia, serif'
});

class FakeNode {
  constructor(tag) {
    this.tagName = tag; this.attrs = new Map(); this.children = []; this._text = null;
    this.style = {}; this.events = {}; this.hidden = true; this.open = false;
  }
  setAttribute(key, value) { this.attrs.set(key, String(value)); }
  getAttribute(key) { return this.attrs.has(key) ? this.attrs.get(key) : null; }
  hasAttribute(key) { return this.attrs.has(key); }
  removeAttribute(key) { this.attrs.delete(key); }
  append(...nodes) { for (const n of nodes) this.children.push(typeof n === 'string' ? textNode(n) : n); }
  appendChild(n) { this.append(n); return n; }
  prepend(...nodes) { this.children.unshift(...nodes.map(n => typeof n === 'string' ? textNode(n) : n)); }
  insertBefore(n, ref) { const at = this.children.indexOf(ref); if (at < 0) this.children.push(n); else this.children.splice(at, 0, n); return n; }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); }
  replaceChildren(...nodes) { this.children = []; this._text = null; this.append(...nodes); }
  set textContent(value) { this.children = []; this._text = String(value ?? ''); }
  get textContent() { return (this._text ?? '') + this.children.map(c => c.textContent).join(''); }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children.at(-1) || null; }
  addEventListener(type, handler) { this.events[type] = handler; }
  removeEventListener(type) { delete this.events[type]; }
  querySelectorAll(selector) {
    const tags = String(selector).split(',').map(s => s.trim());
    const out = [];
    const walk = node => { for (const c of node.children) { if (tags.includes(c.tagName)) out.push(c); walk(c); } };
    walk(this); return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function textNode(text) { const n = new FakeNode('#text'); n._text = String(text); return n; }

function installDom() {
  const ids = new Map();
  const document = {
    createElement: tag => new FakeNode(tag),
    createElementNS: (_, tag) => new FakeNode(tag),
    createTextNode: text => textNode(text),
    getElementById: id => { if (!ids.has(id)) ids.set(id, new FakeNode('div')); return ids.get(id); },
    querySelector: () => new FakeNode('div'),
    querySelectorAll: () => []
  };
  const window = {addEventListener() {}, removeEventListener() {}, matchMedia: () => ({matches: false})};
  Object.assign(globalThis, {document, window, MentatTheme: THEME, escapeHtml: s => String(s)});
  return {document, window};
}

function serialize(node) {
  if (node == null) return 'null';
  if (node.tagName === '#text') return JSON.stringify(node._text);
  const attrs = [...node.attrs].map(([k, v]) => ' ' + k + '=' + JSON.stringify(v)).join('');
  const style = Object.keys(node.style || {}).length ? ' style=' + JSON.stringify(node.style) : '';
  const plot = node.__plot ? ' __plot=' + JSON.stringify(node.__plot) : '';
  const text = node._text ? JSON.stringify(node._text) : '';
  return '<' + node.tagName + attrs + style + plot + '>' + text + node.children.map(serialize).join('') + '</' + node.tagName + '>';
}

function loadEngine(dir) {
  installDom();
  const fresh = file => { const p = path.join(dir, file); delete require.cache[require.resolve(p)]; return require(p); };
  const math = fresh('chart-math.js');
  globalThis.MentatCharts = math;
  vm.runInThisContext(fs.readFileSync(path.join(dir, 'grouped-chart.js'), 'utf8'), {filename: path.join(dir, 'grouped-chart.js')});
  vm.runInThisContext(fs.readFileSync(path.join(dir, 'charts.js'), 'utf8'), {filename: path.join(dir, 'charts.js')});
  const plot = fresh('contribution-plot.js');
  globalThis.MentatContributionPlot = plot;
  return {math, paint: globalThis.MentatChartRender, grouped: globalThis.MentatGroupedSVG, plot};
}

/* ------------------------------------------------------------- datasets */
const D1 = {sheet: 'Math', columns: [{index: 0, name: 'Company', type: 'category', distinct: 2}, {index: 1, name: 'Amount', type: 'number', distinct: 4}, {index: 2, name: 'Date', type: 'date', distinct: 3}],
  rows: [['A', 10, '2026-01-02'], ['A', 30, '2026-01-01'], ['B', 60, '2026-01-03'], ['B', null, '2026-01-01']], row_numbers: [6, 7, 8, 9], row_count: 4};
const D2 = {sheet: 'Seven', columns: [{index: 0, name: 'Transaction ID', type: 'number', distinct: 12}, {index: 1, name: 'Election year', type: 'number', distinct: 6},
  {index: 2, name: 'Region', type: 'category', distinct: 3}, {index: 3, name: 'Party', type: 'category', distinct: 2},
  {index: 4, name: 'Receipt count', type: 'number', distinct: 12}, {index: 5, name: 'Amount', type: 'number', distinct: 12}, {index: 6, name: 'Cost', type: 'number', distinct: 12}],
  rows: Array.from({length: 12}, (_, i) => [1000 + i, 2018 + i % 6, ['A', 'B', 'C'][i % 3], i % 2 ? 'D' : 'R', i + 1, (i + 1) * 100 - (i === 7 ? 1500 : 0), (i + 1) * 10 + (i % 4)]),
  row_numbers: Array.from({length: 12}, (_, i) => i + 2), row_count: 12};
const D3 = {sheet: 'Funding comparison', columns: [{index: 0, name: 'Recipient', type: 'category'}, {index: 1, name: 'Source / activity', type: 'category'}, {index: 2, name: 'Amount', type: 'number'}, {index: 3, name: 'Election cycle', type: 'number'}],
  rows: [['A', 'Company', 100, 2024], ['A', 'Company', -10, 2024], ['A', 'PAC support', 777, 2024], ['B', 'PAC support', 250, 2024], ['B', 'Company', null, 2024], ['C', 'PAC support', 5, 2024]],
  row_numbers: [6, 7, 8, 9, 10, 11], row_count: 6, independent_series: 1, series_order: ['Company', 'PAC support'], note: 'Funding note.'};
const YEARS = Array.from({length: 18}, (_, i) => 2008 + i);
const D4 = {sheet: 'Years', columns: [{index: 0, name: 'Year', type: 'number'}, {index: 1, name: 'Amount', type: 'number'}, {index: 2, name: 'Party', type: 'category'}, {index: 3, name: 'Date', type: 'date'}],
  rows: YEARS.flatMap(y => [[String(y), (y - 2000) * 10 + (y % 4 === 0 ? 50 : 0), 'D', y + '-03-01'], [String(y), (y - 2000) * 7, 'R', y + '-09-15T10:00:00Z']]),
  row_numbers: YEARS.flatMap((_, i) => [2 * i + 2, 2 * i + 3]), row_count: YEARS.length * 2};
const D4b = {sheet: 'Cycles', columns: [{index: 0, name: 'Election cycle', type: 'category'}, {index: 1, name: 'Amount', type: 'number'}],
  rows: [2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026].map((y, i) => [(y - 1) + '-' + y, 100 + i * 25 - (i === 3 ? 60 : 0)]), row_numbers: [2, 3, 4, 5, 6, 7, 8, 9], row_count: 8};
const WIDE_ROWS = Array.from({length: 70}, (_, n) => { const i = n + 1; return ['R' + i, i, i % 2 ? 'D' : 'R', i === 5 || i === 6 ? '10001; 10002' : i === 7 ? '10001; 10002; 10003' : '10001']; });
const D5 = {sheet: 'Wide', columns: [{index: 0, name: 'Recipient', type: 'category', distinct: 70}, {index: 1, name: 'Amount', type: 'number', distinct: 70}, {index: 2, name: 'Party', type: 'category', distinct: 2}, {index: 3, name: 'ZIPs', type: 'category', distinct: 5}],
  rows: WIDE_ROWS, row_numbers: WIDE_ROWS.map((_, n) => n + 2), row_count: 70, sha256: 'wide'};
const D6 = {sheet: 'Split', columns: [{name: 'ZIPs', type: 'category'}, {name: 'Amount', type: 'number'}], rows: [['10001; 10001; 10002', 100], ['10001', 50]], row_numbers: [2, 3], row_count: 2};
const D7 = {sheet: 'Basis', columns: [{name: 'Company'}, {name: 'Amount', type: 'number'}, {name: 'Basis'}], rows: [['A', 100, 'receipt'], ['A', 100, 'attribution'], ['B', 40, 'receipt']], row_numbers: [2, 3, 4], row_count: 3,
  note: 'Same money appears in two accounting layers.', accounting: {safe_to_sum: true, independent_dimensions: [2]}};
const D7b = {sheet: 'Roles', columns: [{name: 'Role'}, {name: 'Amount', type: 'number'}], rows: [['fact', 10], ['fact', 20], ['total', 30]], row_numbers: [2, 3, 4], row_count: 3,
  accounting: {safe_to_sum: true, row_role_column: 0, included_row_roles: ['fact']}};
const D7c = {sheet: 'Unsafe', columns: [{name: 'Company'}, {name: 'Amount', type: 'number', accounting: {safe_to_sum: false, reason: 'Choose Analysis Amount.'}}], rows: [['A', 1]], row_numbers: [2], row_count: 1};
const D8 = {sheet: 'Facts', header_row: 1, columns: [
  {index: 0, name: 'Election Cycle', type: 'category', distinct: 3, missing: 0}, {index: 1, name: 'Recipient Category', type: 'category', distinct: 4, missing: 0},
  {index: 2, name: 'Amount', type: 'number', distinct: 9, missing: 0}, {index: 3, name: 'Employee Tier', type: 'category', distinct: 2, missing: 0},
  {index: 4, name: 'Contribution Count', type: 'number', distinct: 5, missing: 0}, {index: 5, name: 'Committee ID', type: 'category', distinct: 9, missing: 0},
  {index: 6, name: 'Date', type: 'date', distinct: 9, missing: 0}],
  rows: [['2021-2022', 'Company PAC', 1000, 'Chief', 4, 'C001', '2021-03-01'], ['2021-2022', 'Trade Association', 500, 'Employees', 2, 'C002', '2021-06-01'],
    ['2023-2024', 'Company PAC', 2000, 'Chief', 6, 'C001', '2023-03-01'], ['2023-2024', 'Leadership PAC', 250, 'Employees', 1, 'C003', '2023-09-01'],
    ['2025-2026', 'Company PAC', 3000, 'Chief', 9, 'C001', '2025-02-01'], ['2025-2026', 'Trade Association', 750, 'Employees', 3, 'C002', '2025-07-01']],
  row_numbers: [2, 3, 4, 5, 6, 7], row_count: 6, sha256: 'test'};
const D9 = {sheet: '2025-2026', header_row: 1, columns: [{index: 0, name: 'Fact Type', type: 'category'}, {index: 1, name: 'Recipient', type: 'category'}, {index: 2, name: 'Amount', type: 'number'}],
  rows: [['Recipient', 'Alpha PAC', 70], ['Recipient', 'Beta PAC', 30], ['Employee', 'Alpha PAC', 55], ['Employee', 'Beta PAC', 25], ['Master', 'Alpha PAC', 70], ['Master', 'Beta PAC', 30]],
  row_numbers: [2, 3, 4, 5, 6, 7], row_count: 6, sha256: 'fact'};
const D10 = {sheet: 'Signed', columns: [{index: 0, name: 'Group', type: 'category'}, {index: 1, name: 'Amount', type: 'number'}, {index: 2, name: 'Subject', type: 'category'}, {index: 3, name: 'Year', type: 'number'}],
  rows: [['A', 5, 'S1', '2020'], ['B', -3, 'S1', '2021'], ['C', 0, 'S2', '2022'], ['D', 7, 'S2', '2023'], ['E', 2, 'S1', '2024']], row_numbers: [2, 3, 4, 5, 6], row_count: 5};

const LEGACY_TYPES = ['bar', 'pie', 'line', 'scatter', 'box', 'histogram', 'area', 'doughnut', 'waterfall', 'radar', 'horizontal', 'pareto', 'grouped', 'groupedhorizontal', 'multiline'];
const AGGS = ['sum', 'mean', 'count', 'median', 'min', 'max'];

function mathCases() {
  const cases = [];
  const add = (id, data, config) => cases.push({id, data, config});
  const sets = [['D1', D1, [[0, 1], [2, 1], [-1, 1], [0, -1], [1, 1]], 2], ['D2', D2, [[2, 5], [3, 6], [1, 5], [5, 6], [2, -1]], 3],
    ['D8', D8, [[1, 2], [0, 2], [3, 4], [6, 2], [1, -1]], 3], ['D10', D10, [[0, 1], [2, 1], [-1, 1]], 2], ['D9', D9, [[1, 2], [0, 2]], 0]];
  for (const [name, data, pairs, seriesColumn] of sets) {
    for (const type of LEGACY_TYPES) for (const aggregation of AGGS) for (const [x, y] of pairs) {
      const base = {type, x, y, aggregation, filters: [], series: ['grouped', 'groupedhorizontal', 'multiline'].includes(type) ? seriesColumn : -1};
      add(`${name}:${type}:${aggregation}:${x}:${y}`, data, base);
      if (aggregation === 'sum' || aggregation === 'count') {
        add(`${name}:${type}:${aggregation}:${x}:${y}:dims`, data, {...base, dimensions: [x, seriesColumn].filter(c => c >= 0)});
        add(`${name}:${type}:${aggregation}:${x}:${y}:fold1`, data, {...base, maxGroups: 1});
        add(`${name}:${type}:${aggregation}:${x}:${y}:limit2`, data, {...base, limit: 2, start: 3});
      }
    }
  }
  // Wide: folds, limits, splits (chart-studio-core.test.js fixtures).
  const wide = patch => Object.assign({type: 'bar', x: 0, y: 1, aggregation: 'sum', transform: 'none', series: -1, filters: []}, patch);
  for (const type of LEGACY_TYPES) for (const maxGroups of [undefined, 40, 60, 100, 1]) for (const aggregation of AGGS)
    add(`wide:${type}:${maxGroups}:${aggregation}`, D5, wide({type, maxGroups, aggregation, series: ['grouped', 'groupedhorizontal', 'multiline'].includes(type) ? 2 : -1, x: type === 'scatter' ? 1 : 0}));
  for (const maxGroups of [0, -1, 1.5, '3', null]) add(`wide:badlimit:${maxGroups}`, D5, wide({maxGroups}));
  add('wide:zips:count', D5, wide({x: 3, y: -1, aggregation: 'count'}));
  add('wide:zips:split', D5, wide({x: 3, y: -1, aggregation: 'count', splitMulti: true}));
  add('wide:zips:split:sum', D5, wide({x: 3, splitMulti: true}));
  add('wide:zips:split:scatter', D5, wide({type: 'scatter', x: 1, splitMulti: true, aggregation: 'count'}));
  add('wide:zips:split:grouped', D5, wide({type: 'grouped', x: 3, series: 2, splitMulti: true, aggregation: 'count', y: -1}));
  add('wide:zips:split:twofields', D5, wide({dimensions: [3, 0], x: 3, y: -1, aggregation: 'count', splitMulti: true}));
  add('wide:zips:grouped:nosplit', D5, wide({type: 'grouped', x: 3, series: 2}));
  add('split:money', D6, {type: 'bar', x: 0, y: 1, aggregation: 'sum', splitMulti: true, filters: []});
  add('split:count', D6, {type: 'bar', x: 0, y: -1, aggregation: 'count', splitMulti: true, filters: []});
  // Filters.
  const filters = [[{column: 0, op: 'equals', value: 'a'}], [{column: 1, op: 'gte', value: 20}], [{column: 1, op: 'lte', value: '30'}], [{column: 0, op: 'contains', value: 'b'}],
    [{column: 0, op: 'in', value: ['A']}], [{column: 0, op: 'notin', value: ['A']}], [{column: 1, op: 'between', value: [15, '']}], [{column: 2, op: 'between', value: ['2026-01-02', '2026-01-03']}],
    [{column: 1, op: 'gte', value: 'oops'}], [{column: 9, op: 'equals', value: 'x'}], [{column: 0, op: 'regex', value: 'x'}], [{column: 0, op: 'in', value: []}], [{column: 1, op: 'between', value: ['', null]}],
    [{column: 0, op: 'equals', value: 'absent'}]];
  filters.forEach((f, i) => add(`filter:${i}`, D1, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: f}));
  for (const [start, end, limit] of [[7, 9, 0], [9, 6, 0], [0, 0, 1], [0, 0, -1], [0, 0, 1.5], [7, 0, 2]]) add(`bounds:${start}:${end}:${limit}`, D1, {type: 'bar', x: 0, y: 1, aggregation: 'sum', start, end, limit, filters: []});
  // Accounting.
  add('acct:unsafe', D7c, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('acct:unsafe:mean', D7c, {type: 'bar', x: 0, y: 1, aggregation: 'mean', filters: []});
  add('acct:roles', D7b, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('acct:roles:fact', D7b, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: [{column: 0, op: 'equals', value: 'fact'}]});
  add('acct:indep', D7, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('acct:indep:dims', D7, {type: 'bar', x: 0, y: 1, aggregation: 'sum', dimensions: [0, 2], filters: []});
  add('acct:indep:fold', D7, {type: 'bar', x: 0, y: 1, aggregation: 'sum', dimensions: [0, 2], maxGroups: 1, filters: []});
  add('acct:indep:series', D7, {type: 'grouped', x: 0, series: 2, y: 1, aggregation: 'sum', filters: []});
  add('acct:indep:receipt', D7, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: [{column: 2, op: 'equals', value: 'receipt'}]});
  add('acct:unsafe:whole', {...D1, accounting: {safe_to_sum: false, reason: 'Mixed summary grain.'}}, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  // Funding comparison (funding-comparison.js:68-72 default_config shapes).
  for (const type of ['grouped', 'groupedhorizontal', 'multiline', 'bar', 'line', 'pie'])
    for (const patch of [{}, {x: 1, series: 0}, {start: 9}, {transform: 'annual'}, {aggregation: 'mean'}, {aggregation: 'count', y: -1}, {maxGroups: 1}])
      add(`funding:${type}:${JSON.stringify(patch)}`, D3, {type, x: 0, series: 1, y: 2, aggregation: 'sum', filters: [], ...patch});
  add('funding:noorder', {...D3, series_order: undefined}, {type: 'grouped', x: 0, series: 1, y: 2, aggregation: 'sum', filters: []});
  add('grouped:noseries', D1, {type: 'grouped', x: 0, series: -1, y: 1, aggregation: 'sum', filters: []});
  add('grouped:badagg', D1, {type: 'grouped', x: 0, series: 2, y: 1, aggregation: 'avg', filters: []});
  add('changed:type:dot', D1, {type: 'dot', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('changed:agg:countd', D1, {type: 'bar', x: 0, y: 1, aggregation: 'countd', filters: []});
  add('bad:dims', D1, {type: 'bar', x: 0, y: 1, aggregation: 'sum', dimensions: [7], filters: []});
  add('bad:measure', D1, {type: 'bar', x: 0, y: 9, aggregation: 'sum', filters: []});
  add('bad:count', D1, {type: 'bar', x: 0, y: 9, aggregation: 'count', filters: []});
  add('bad:scatterx', D1, {type: 'scatter', x: -1, y: 1, aggregation: 'sum', filters: []});
  add('bad:allnull', {...D1, rows: [['A', null]], row_numbers: [6]}, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('pie:zero', {...D1, rows: [['A', 0]], row_numbers: [6]}, {type: 'pie', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('hist:const', {...D1, rows: [['A', 2], ['B', 2]], row_numbers: [6, 7]}, {type: 'histogram', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('evil', {...D1, rows: [['<script>alert(1)</script>', 3], ['__proto__', 4]], row_numbers: [6, 7]}, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  add('accounting%', {...D1, rows: [['A', '(25%)'], ['A', '75%'], ['A', '20)']], row_numbers: [6, 7, 8]}, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []});
  // Time analysis.
  const temporal = [];
  for (const [name, data, x] of [['years', D4, 0], ['dates', D4, 3], ['cycles', D4b, 0], ['signed', D10, 3]])
    for (const type of ['bar', 'line', 'area', 'pie', 'scatter']) for (const transform of ['annual', 'yoy', 'change', 'forecast'])
      for (const period of ['year', 'cycle']) for (const aggregation of ['sum', 'mean', 'count', 'median', 'min', 'max'])
        temporal.push([`time:${name}:${type}:${transform}:${period}:${aggregation}`, data, {type, x, y: 1, aggregation, transform, period, horizon: 2, as_of: '2026-06-01', series: -1, filters: []}]);
  for (const t of temporal) add(...t);
  for (const horizon of [1, 4, 10, 11, 0, 2.5]) add(`time:horizon:${horizon}`, D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'forecast', period: 'year', horizon, as_of: '2026-06-01', series: -1, filters: []});
  add('time:forecast:bounds', D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'forecast', period: 'year', horizon: 2, as_of: '2026-06-01', start: 3, series: -1, filters: []});
  add('time:forecast:partial', {...D4, partial_periods: [2015], complete_periods: YEARS, period_coverage: {2020: 'partial'}, verified_data_through: '2025-12-31'}, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'forecast', period: 'year', horizon: 3, as_of: '2026-06-01', series: -1, filters: []});
  add('time:forecast:badasof', D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'forecast', period: 'year', horizon: 2, as_of: 'never', series: -1, filters: []});
  add('time:otherdims', D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', dimensions: [0, 2], series: -1, filters: []});
  add('time:subjects', D10, {type: 'line', x: 3, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', series: -1, filters: []});
  add('time:subject:one', D10, {type: 'line', x: 3, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', series: -1, filters: [{column: 2, op: 'equals', value: 'S1'}]});
  add('time:split', D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', splitMulti: true, series: -1, filters: []});
  add('time:badtransform', D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'rolling', period: 'year', series: -1, filters: []});
  add('time:nodate', D4, {type: 'line', x: 9, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', series: -1, filters: []});
  add('time:series:ignored', D4, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', series: 2, filters: []});
  add('time:empty', D4, {type: 'line', x: 2, y: 1, aggregation: 'sum', transform: 'annual', period: 'year', series: -1, filters: []});
  return cases;
}

function quickCases(math) {
  const out = [];
  const choices = [...LEGACY_TYPES, 'annual', 'yoy', 'change', 'forecast', 'nonsense'];
  const currents = [{}, {x: 2, y: 5, aggregation: 'median'}, {y: -1, aggregation: 'count'}, {x: 3, series: 2}, {y: 0}];
  for (const [name, data] of [['D1', D1], ['D2', D2], ['D3', D3], ['D4', D4], ['D4b', D4b], ['D8', D8], ['D10', D10], ['cols', {columns: D2.columns}]])
    for (const choice of choices) for (const [i, current] of currents.entries()) out.push([`quick:${name}:${choice}:${i}`, data, current, choice]);
  return out;
}

const PLOT_COLUMNS = [{id: 'amount', name: 'Net giving', unit: 'cents'}, {id: 'records', name: 'Observations', unit: 'count'}, {id: 'company', name: 'Company'},
  {id: 'party', name: 'Party'}, {id: 'gross_amount', name: 'Gross amount', unit: 'cents'}, {id: 'year', name: 'Year'}, {id: 'cycle', name: 'Election cycle'}, {id: 'date', name: 'Date', unit: 'date'}];
function plotCases() {
  const bar = [{company: 'A', party: 'D', amount: -200, records: 3, gross_amount: 400}, {company: 'B', party: 'R', amount: 100, records: 7, gross_amount: 150},
    {company: 'C', party: 'D', amount: 12345678, records: 1, gross_amount: 12345678}, {company: 'D', party: null, amount: null, records: 0, gross_amount: 0}];
  const time = [{year: '2022', company: 'B', amount: 200, records: 2}, {year: '2022', company: 'A', amount: 100, records: 1}, {year: '2023', company: 'A', amount: 150, records: 4},
    {year: '2025', company: 'A', amount: null, records: 0}, {year: '2026', company: 'A', amount: 500, records: 9}, {year: 'bad', company: 'B', amount: 5, records: 1}];
  const cycles = [{cycle: '2019-2020', amount: 1}, {cycle: '2021-2022', amount: 2}, {cycle: '2025-2026', amount: 3}];
  const dates = [{date: '2024-01-01', amount: 100}, {date: '2024-01-02', amount: 200}, {date: '2024-01-05', amount: 300}, {date: null, amount: 1}];
  const base = {type: 'bar', dimensions: ['company'], measures: ['amount'], columns: PLOT_COLUMNS};
  const S = [];
  const add = (id, rows, spec) => S.push([id, rows, spec]);
  add('bar', bar, base);
  add('bar:labels', bar, {...base, labels: true});
  add('bar:two', bar, {...base, measures: ['amount', 'records'], labels: true});
  add('bar:color', bar, {...base, dimensions: ['company', 'party'], color: 'party', labels: true});
  add('bar:colormap', bar, {...base, dimensions: ['company', 'party'], color: 'party', colorMap: {R: '#111111', D: '#222222'}, labelDimensions: ['company']});
  add('bar:studio', bar, {...base, title: 'T', barPitch: 28, niceTicks: true, panelTitles: false, width: 900, barLeftMax: 190, seriesColor: '#2a8a8a', labels: true});
  add('bar:studio:fit', bar, {...base, title: 'T', barPitch: 28, niceTicks: true, panelTitles: false, width: 700, barLeftMax: 190, seriesColor: '#2a8a8a', fitHeight: 600, barPitchMax: 60, barPitchMin: 18, labels: true});
  add('bar:studio:fitcolor', bar, {...base, dimensions: ['company', 'party'], color: 'party', title: 'T', barPitch: 28, niceTicks: true, width: 700, barLeftMax: 190, fitHeight: 300, barPitchMin: 12});
  add('bar:refs', bar, {...base, references: {average: true, median: true}});
  add('bar:refs:two', bar, {...base, measures: ['amount', 'records'], references: {average: true}});
  add('bar:tooltip', bar, {...base, tooltip: ['gross_amount'], labelFields: ['company', 'records'], labels: true});
  add('changed:line:year:duplicates', time, {...base, type: 'line', dimensions: ['year']}); // two 2022 rows: summed with a note since r3 (V07)
  add('line:year:company', time.filter(r => r.year !== '2022' || r.company === 'A'), {...base, type: 'line', dimensions: ['year', 'company'], labels: true});
  add('line:year:color', time.filter(r => r.year !== '2022' || r.company === 'A'), {...base, type: 'line', dimensions: ['year', 'company'], color: 'company', references: {median: true}});
  add('area:year', time.filter(r => r.company === 'A'), {...base, type: 'area', dimensions: ['year'], measures: ['amount', 'records']});
  add('line:cycle', cycles, {...base, type: 'line', dimensions: ['cycle']});
  add('line:date', dates, {...base, type: 'line', dimensions: ['date']});
  add('area:date:labels', dates, {...base, type: 'area', dimensions: ['date'], labels: true, tooltip: ['amount']});
  add('scatter', [...bar, {company: 'E', amount: 1e12, records: null}], {...base, type: 'scatter', measures: ['amount', 'records'], labels: true});
  add('scatter:size', bar, {...base, type: 'scatter', measures: ['amount', 'records'], size: 'gross_amount', labelFields: ['company'], tooltip: ['gross_amount'], labels: true});
  add('scatter:color', bar, {...base, dimensions: ['company', 'party'], type: 'scatter', measures: ['amount', 'records'], color: 'party'});
  add('table', dates, {...base, type: 'table', dimensions: ['date']});
  add('table:dates', [{company: 'A', date: '2024-01-01', amount: 5}], {...base, type: 'table', measures: ['amount', 'date']});
  add('empty', [], base);
  add('line:nodates', [{year: 'x', amount: 1}], {...base, type: 'line', dimensions: ['year']});
  add('scatter:noPairs', [{company: 'A', amount: 1, records: null}], {...base, type: 'scatter', measures: ['amount', 'records']});
  add('bad:type', bar, {...base, type: 'pie'});
  add('bad:nomeasure', bar, {...base, measures: []});
  add('bad:dupmeasure', bar, {...base, measures: ['amount', 'amount']});
  add('bad:dims', bar, {...base, dimensions: 'company'});
  add('bad:color', bar, {...base, color: 'party'});
  add('bad:scatter1', bar, {...base, type: 'scatter'});
  add('bad:sizeNonNumeric', bar.map(r => ({...r, gross_amount: 'x'})), {...base, type: 'scatter', measures: ['amount', 'records'], size: 'gross_amount'});
  add('bad:rowshape', [[1]], base);
  add('bad:nonnumeric', [{company: 'A', amount: '100'}], base);
  add('bad:range', [{company: 'A', amount: -Number.MAX_VALUE}, {company: 'B', amount: Number.MAX_VALUE}], base);
  // The refusals below are the ones Paul's words retire; they are recorded so
  // ENGINE.md can show the before/after, and are not part of the parity check.
  add('changed:size:bar', bar, {...base, size: 'gross_amount'});
  add('changed:color:time', time, {...base, type: 'line', dimensions: ['year', 'company'], color: 'year'});
  add('changed:line:category', bar, {...base, type: 'line'});
  add('changed:area:category', bar, {...base, type: 'area'});
  add('changed:scatter3', bar, {...base, type: 'scatter', measures: ['amount', 'records', 'gross_amount']});
  add('changed:timeline:duplicates', [{year: '2022', amount: 100}, {year: '2022', amount: 200}], {...base, type: 'line', dimensions: ['year']});
  return S;
}

function run(dir) {
  const {math, paint, grouped, plot} = loadEngine(dir);
  const out = [];
  const record = (id, fn) => {
    let text;
    try { text = fn(); } catch (e) { text = 'THROW:' + e.message; }
    out.push([id, text]);
    return text;
  };
  for (const c of mathCases()) {
    let result = null;
    record('math:' + c.id, () => { result = math.build(c.data, c.config); return JSON.stringify(result); });
    if (!result || c.id.startsWith('changed:')) continue;
    record('label:' + c.id, () => paint.measureLabel(c.config, result, c.data));
    record('table:' + c.id, () => JSON.stringify(paint.tableRows(result, c.config, c.data)));
    record('svg:' + c.id, () => {
      const drawn = paint.chartSVG(result, paint.measureLabel(c.config, result, c.data), c.config, c.data, 'Title ' + c.id, 'Scope ' + c.id);
      return serialize(drawn.svg) + '|plot=' + JSON.stringify(drawn.plot);
    });
    if (result.series) record('grouped:' + c.id, () => serialize(grouped(result, 'Funding comparison', 'Note ' + c.id)));
  }
  for (const [id, data, current, choice] of quickCases(math)) {
    let next = null;
    record(id, () => { next = math.quickConfig(data, current, choice); return JSON.stringify(next); });
    if (next && data.rows) record('built:' + id, () => JSON.stringify(math.build(data, {...next, as_of: '2026-06-01', filters: next.filters || []})));
  }
  for (const [id, config] of [['filter:bounds', {start: 7, limit: 2}], ['filter:eq', {filters: [{column: 1, op: 'equals', value: 'Company'}], limit: 2}], ['filter:zero', {limit: 0}],
    ['filter:neg', {limit: -1}], ['filter:frac', {limit: 1.5}], ['filter:swap', {start: 9, end: 7}], ['filter:badcol', {filters: [{column: 8, op: 'equals', value: 'x'}]}]])
    record('filterRows:' + id, () => JSON.stringify(math.filterRows(D3, config)));
  for (const n of [undefined, null, 0, 1, 60, 100000, -1, 1.5, '4', Infinity]) record('groupLimit:' + String(n), () => JSON.stringify(math.groupLimit({maxGroups: n})));
  // W10 makes 'all' a group limit (Infinity); before r3 it was refused.
  record('changed:groupLimit:all', () => JSON.stringify(math.groupLimit({maxGroups: 'all'})));
  for (const [id, data] of [['D1', D1], ['D2', D2], ['D8', D8], ['D5', D5]]) record('recommend:' + id, () => JSON.stringify(math.recommend(data.columns)));
  record('explode:wide', () => JSON.stringify(math.explodeRows(D5, {x: 3, splitMulti: true})));
  record('explode:two', () => JSON.stringify(math.explodeRows(D5, {dimensions: [3, 0], splitMulti: true})));
  record('types', () => JSON.stringify([math.chartTypes.slice(0, 15), math.aggregations.slice(0, 6), math.DEFAULT_GROUP_LIMIT]));
  record('temporal:direct', () => JSON.stringify(math.temporal(D4, {type: 'bar', x: 0, y: 1, aggregation: 'sum', transform: 'yoy', period: 'year', series: -1, filters: []})));
  for (const [id, rows, spec] of plotCases()) {
    record('plot:' + id, () => {
      const container = document.createElement('div');
      const svg = plot.render(container, rows, spec, () => {});
      return serialize(container) + '|returned=' + (svg ? 'svg' : 'null');
    });
    record('validate:' + id, () => { plot.validate(rows, spec); return 'ok'; });
  }
  record('plot:timeline:dup', () => JSON.stringify(plot.timeline([{year: '2022', amount: 100}, {year: '2022', amount: 200}], {type: 'line', dimensions: ['year'], measures: ['amount']}, 'amount')));
  return out;
}

// 16 hex digits of SHA-256 per output keeps the golden file small; a collision
// that hides a real change is out of reach at this corpus size.
const digest = text => crypto.createHash('sha256').update(text).digest('hex').slice(0, 16);
const idsDigest = outputs => crypto.createHash('sha256').update(outputs.map(([id]) => id).join('\n')).digest('hex');
module.exports = {run, mathCases, digest, idsDigest, serialize, installDom, loadEngine, FakeNode, THEME, datasets: {D1, D2, D3, D4, D4b, D5, D6, D7, D8, D9, D10}};

if (require.main === module) {
  // node chart-engine-r3.corpus.js <engine dir> <golden.json> [full.json]
  const [dir, target, full] = process.argv.slice(2);
  const outputs = run(path.resolve(dir));
  fs.writeFileSync(target, JSON.stringify({captured_from: 'pre-r3 engine files (the backup copies taken before the first r3 edit)',
    cases: outputs.length, ids: idsDigest(outputs), digests: outputs.map(([, text]) => digest(text)).join('')}) + '\n');
  if (full) fs.writeFileSync(full, JSON.stringify(Object.fromEntries(outputs)));
  console.log(outputs.length + ' cases written');
}
