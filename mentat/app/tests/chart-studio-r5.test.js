/* Chart Studio, round 5 (2026-09-24): the constraints a full live retest of
   final/r3/cs/CENSUS.md still found, each now drawn.

   Paul, 2026-09-23: "Additionally, within the Chart Studio make sure
   everything is freely flexible, that all graph types properly register and
   nothing is logically constrained, it needs to be utterly maluable."

   - B-S07 / C-04: a Detail dimension sets a box's or a histogram's level of
     detail (engine W9), and Colour still splits it (W11).
   - B-E21 / B-E22: Parameters ask every renderer for every axis label and
     every legend member (W8).
   - C-07: a histogram, box or date span keeps its Label and Tooltip measures
     in the verified query.
   - C-09: a dual combination draws its reference lines.
   - B-S32: compared scopes keep the pills' colours in the analytical families
     (spec.colorMap).
   Run with node --test on this file. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.join(__dirname, '..');
const {math, paint, plot} = corpus.loadEngine(APP);
const charts = require('../contribution-charts.js');
globalThis.MentatContributionCharts = charts;
const core = require('../chart-studio-core.js');
const contributions = require('../chart-studio-contributions.js');

const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const attr = (node, key) => node.attrs.get(key);

// A worksheet shaped like Funding Cohorts: company, committee, party, amount.
const DATA = {sheet: 'Cohorts', columns: [{index: 0, name: 'Company', type: 'category'}, {index: 1, name: 'Recipient Committee', type: 'category'},
  {index: 2, name: 'Party', type: 'category'}, {index: 3, name: 'Amount', type: 'number'}],
  rows: [['A', 'c1', 'D', 10], ['A', 'c1', 'D', 5], ['A', 'c2', 'R', 30], ['B', 'c3', 'D', 7], ['B', 'c4', 'R', 1], ['B', 'c4', 'R', 2]],
  row_numbers: [1, 2, 3, 4, 5, 6], row_count: 6};
const worksheet = (mark, extra = {}) => Object.assign(core.createSheet('S', 's'), {columns: [{column: 0, aggregation: 'sum'}], rows: [{column: 3, aggregation: 'sum'}]}, extra, {marks: Object.assign(core.createSheet('S', 's').marks, {type: mark}, extra.marks || {})});

test('B-S07/C-04: Detail sets a box’s level of detail - each value is a committee’s total', () => {
  const report = core.compileReport(worksheet('box', {marks: {detail: [{column: 1, aggregation: 'sum'}]}}), DATA.columns);
  assert.equal(report.config.grain, 'group');
  assert.match(report.notes.join(' '), /Each box summarises the total of Amount of every Recipient Committee group\./);
  const result = math.build(DATA, report.config);
  // A: committees c1 = 15, c2 = 30 -> median 22.5. B: c3 = 7, c4 = 3 -> median 5.
  assert.deepEqual(result.groups.map(g => [g.label, g.median, g.n]), [['A', 22.5, 2], ['B', 5, 2]]);
  // Without Detail the same box reads individual rows, as before.
  const rows = math.build(DATA, core.compileReport(worksheet('box'), DATA.columns).config);
  assert.deepEqual(rows.groups.map(g => [g.label, g.n]), [['A', 3], ['B', 3]]);
});

test('B-S07/W11: Colour splits a group-grain box by member', () => {
  const report = core.compileReport(worksheet('box', {marks: {detail: [{column: 1, aggregation: 'sum'}], color: {column: 2, aggregation: 'sum'}}}), DATA.columns);
  const result = math.build(DATA, report.config);
  assert.deepEqual(result.series, ['D', 'R']);
  assert.deepEqual(result.groups.map(g => [g.label, g.median, g.seriesIndex]), [['["A","D"]', 15, 0], ['["A","R"]', 30, 1], ['["B","D"]', 7, 0], ['["B","R"]', 3, 1]]);
});

test('B-S07/C-04: Detail makes a histogram of group totals, stacked by Colour', () => {
  const plain = core.compileReport(worksheet('histogram', {columns: [], marks: {detail: [{column: 1, aggregation: 'sum'}]}}), DATA.columns);
  assert.equal(plain.config.grain, 'group');
  assert.equal(plain.config.x, -1, 'a histogram keeps no axis dimension');
  assert.deepEqual(plain.config.dimensions, [1]);
  assert.match(plain.notes.join(' '), /The bins count Recipient Committee groups by their total of Amount\./);
  const bins = math.build(DATA, plain.config);
  assert.deepEqual(bins.groups.map(g => g.value), [3, 1], 'four committee totals 15, 30, 7, 3 fall in two bins');
  const coloured = core.compileReport(worksheet('histogram', {columns: [], marks: {detail: [{column: 1, aggregation: 'sum'}], color: {column: 2, aggregation: 'sum'}}}), DATA.columns);
  assert.deepEqual(coloured.config.dimensions, [1, 2], 'each group carries one Colour member');
  const stacked = math.build(DATA, coloured.config);
  assert.deepEqual(stacked.series, ['D', 'R']);
  assert.deepEqual(stacked.groups.map(g => g.values.map(v => v.value)), [[2, 1], [0, 1]]);
  // Without Detail the histogram still bins individual rows.
  assert.equal(core.compileReport(worksheet('histogram', {columns: []}), DATA.columns).config.grain, undefined);
});

test('C-07: a box, a date span and a histogram keep their Label and Tooltip measures in the verified query', () => {
  const columns = contributions.fields([{id: 'company', role: 'dimension'}, {id: 'amount', role: 'measure'}, {id: 'records', role: 'measure'}, {id: 'contributors', role: 'measure'}, {id: 'amount_bin', role: 'dimension'}]);
  for (const type of ['box', 'gantt', 'histogram']) {
    const state = {columns: [{column: 0, aggregation: 'sum'}], rows: [{column: 1, aggregation: 'sum'}], filters: [], analytics: {transform: 'none'}, params: {},
      marks: {type, tooltip: [{column: 2, aggregation: 'sum'}], label: {column: 3, aggregation: 'sum'}}};
    const {query, spec} = contributions.compile(state, columns, {dataset: 'B'});
    assert.ok(query.measures.includes('records') && query.measures.includes('contributors'), type + ': ' + JSON.stringify(query.measures));
    assert.deepEqual(spec.tooltip, ['records'], type);
    assert.deepEqual(spec.labelFields, ['contributors'], type);
  }
});

test('B-E21/B-E22: Parameters ask for every axis label and every legend member', () => {
  const columns = contributions.fields([{id: 'recipient', role: 'dimension'}, {id: 'amount', role: 'measure'}]);
  const state = {columns: [{column: 0, aggregation: 'sum'}], rows: [{column: 1, aggregation: 'sum'}], filters: [], analytics: {transform: 'none'}, params: {axisLabels: 'all', legend: 'all'}, marks: {type: 'line'}};
  const {spec} = contributions.compile(state, columns, {dataset: 'B'});
  assert.equal(spec.axisLabels, 'all');
  assert.equal(spec.legend, 'all');
  const unasked = contributions.compile({...state, params: {}}, columns, {dataset: 'B'}).spec;
  assert.equal(unasked.axisLabels, undefined, 'unasked, the spec is as before');
  // contribution-plot: a categorical line over 40 recipients labels every one of them, turned.
  const rows = Array.from({length: 40}, (_, i) => ({recipient: 'Recipient committee ' + i, amount: 1000 + i}));
  const container = document.createElement('div');
  plot.render(container, rows, {type: 'line', dimensions: ['recipient'], measures: ['amount'], axisLabels: 'all'}, () => {});
  const turned = all(container, n => attr(n, 'data-axis-label') === 'all');
  assert.equal(turned.length, 40);
  assert.ok(turned.every(n => /^rotate\(-35 /.test(attr(n, 'transform'))));
  const auto = document.createElement('div');
  plot.render(auto, rows, {type: 'line', dimensions: ['recipient'], measures: ['amount']}, () => {});
  assert.equal(all(auto, n => attr(n, 'data-axis-label') === 'all').length, 0, 'unasked, ticks are thinned as before');
  // contribution-charts: a dual combination over 20 periods labels each one.
  const periods = Array.from({length: 20}, (_, i) => ({company: 'Company ' + i, amount: 100 * (i + 1), records: i + 1}));
  const svg = charts.render(document.createElement('div'), periods, {type: 'combination', dimensions: ['company'], measures: ['amount', 'records'], axisLabels: 'all', categorical: true}, () => {});
  assert.equal(all(svg, n => attr(n, 'data-axis-label') === 'all').length, 20);
  // charts.js: the pie legend lists every member when asked (engine W8).
  const pie = {type: 'pie', groups: Array.from({length: 24}, (_, i) => ({label: 'Member ' + i, value: 10 + i, n: 1})), selected: 24, excluded: 0};
  const legendOf = options => all(paint.chartSVG(pie, 'Amount', {type: 'pie', x: 0, y: 1, aggregation: 'sum'}, {sheet: 'S', columns: [{name: 'Member'}, {name: 'Amount'}]}, 'T', 'S', options).svg,
    n => n.tagName === 'text' && /^Member \d+/.test(n.textContent)).length;
  assert.ok(legendOf({legend: 'all'}) > legendOf({}), 'every member is listed');
});

test('C-09: a dual combination draws the mean and median of its left measure', () => {
  const rows = [{cycle: '2021-2022', amount: 100, records: 1}, {cycle: '2023-2024', amount: 300, records: 5}, {cycle: '2025-2026', amount: 500, records: 2}];
  const svg = charts.render(document.createElement('div'), rows, {type: 'combination', dimensions: ['cycle'], measures: ['amount', 'records'], references: {average: true, median: true}}, () => {});
  const kinds = all(svg, n => attr(n, 'data-reference')).map(n => attr(n, 'data-reference'));
  assert.deepEqual(kinds, ['average', 'median']);
  assert.match(svg.textContent, /Mean of shown marks: \$3(?!\d)/);
  assert.match(svg.textContent, /Median of shown marks: \$3(?!\d)/);
});

test('B-S32: compared scopes keep the pills’ colours in the analytical families', () => {
  const rows = [{party: 'DEM', scope: 'Alpha', amount: 100}, {party: 'REP', scope: 'Alpha', amount: 50}, {party: 'DEM', scope: 'Beta', amount: 70}];
  const colorMap = {Alpha: '#111111', Beta: '#222222'};
  for (const type of ['dot', 'treemap', 'stacked', 'bubbles']) {
    const svg = charts.render(document.createElement('div'), rows, {type, dimensions: ['party', 'scope'], measures: ['amount'], color: 'scope', colorMap}, () => {});
    const fills = new Set(all(svg, n => attr(n, 'class') === 'plot-mark').map(n => attr(n, 'fill')));
    assert.deepEqual([...fills].sort(), ['#111111', '#222222'], type);
    const legend = all(svg, n => attr(n, 'data-legend') === 'series')[0];
    assert.ok(legend && all(legend, n => n.tagName === 'rect').map(n => attr(n, 'fill')).join() === '#111111,#222222', type + ' legend');
  }
});
