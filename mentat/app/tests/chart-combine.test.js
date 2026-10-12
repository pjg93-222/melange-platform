/* Code review F2 (2026-09-24): repeated aggregate groups are recombined statistic
   by statistic, never by adding every number.

   Before: contribution-charts.js combine() summed every finite measure of two
   rows with the same drawn key, so two groups with means $1.00 and $2.00 drew one
   mark reading $3.00 under a note that said "their measures are summed". The
   same happened on contribution-plot.js timelines. Now amounts and counts add, a
   mean is weighted by observation count, a population standard deviation is
   pooled from each group's count, mean and deviation, minimum and maximum take
   the extremes, dates widen the span, and a median, quartile or distinct count
   (which group summaries cannot rebuild) is left blank and named in the note.
   Run with node --test on this file. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.join(__dirname, '..');
const {paint, plot} = corpus.loadEngine(APP);
const charts = require('../contribution-charts.js');
globalThis.MentatContributionCharts = charts;

const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const attr = (node, key) => node.attrs.get(key);
const marks = tree => all(tree, n => attr(n, 'class') === 'plot-mark');
const titleOf = node => node.children.find(c => c.tagName === 'title').textContent;

// Two raw groups, known to the cent, so every recombined statistic can be checked
// against the statistic of the pooled raw values themselves.
const RAW = {MN: [100, 300], WI: [50, 50, 350]};
const stats = values => {
  const n = values.length, sum = values.reduce((a, b) => a + b, 0), mean = sum / n, sorted = [...values].sort((a, b) => a - b);
  return {amount: sum, records: n, average_amount: Math.round(mean), stddev_amount: Math.round(Math.sqrt(values.reduce((a, v) => a + (v - mean) ** 2, 0) / n)),
    min_amount: sorted[0], max_amount: sorted.at(-1), median_amount: n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2};
};
const COLUMNS = [{id: 'company', name: 'Company', role: 'dimension'}, {id: 'state', name: 'State', role: 'dimension'},
  {id: 'amount', name: 'Net amount', role: 'measure', unit: 'cents'}, {id: 'records', name: 'Observation count', role: 'measure', unit: 'count'},
  {id: 'average_amount', name: 'Average observation amount', role: 'measure', unit: 'cents'},
  {id: 'stddev_amount', name: 'Population standard deviation of amounts', role: 'measure', unit: 'cents'},
  {id: 'min_amount', name: 'Minimum amount', role: 'measure', unit: 'cents'}, {id: 'max_amount', name: 'Maximum amount', role: 'measure', unit: 'cents'},
  {id: 'median_amount', name: 'Median observation amount', role: 'measure', unit: 'cents'},
  {id: 'contributors', name: 'Distinct reported donor identities', role: 'measure', unit: 'count'}];
const rowsFor = company => Object.entries(RAW).map(([state, values]) => ({company, state, ...stats(values), contributors: values.length}));

test('F2: two groups with means $1.00 and $2.00 are not drawn as $3.00', () => {
  const input = [{company: 'A', state: 'MN', average_amount: 100, records: 1, amount: 100}, {company: 'A', state: 'WI', average_amount: 200, records: 3, amount: 600}];
  const {rows, combined} = charts.combine(input, ['company'], COLUMNS);
  assert.equal(combined, 1);
  assert.equal(rows.length, 1);
  assert.notEqual(rows[0].average_amount, 300, 'the old combine() added the two means');
  assert.equal(rows[0].average_amount, 175, 'weighted by observation count: (1 x 100 + 3 x 200) / 4');
  assert.equal(rows[0].amount, 700);
  assert.equal(rows[0].records, 4);
});

test('F2: every source statistic is recombined by its own rule and matches the pooled raw values', () => {
  const pooled = stats([...RAW.MN, ...RAW.WI]);
  const {rows, combined, outcomes} = charts.combine(rowsFor('A'), ['company'], COLUMNS);
  const row = rows[0];
  assert.equal(combined, 1);
  assert.equal(row.amount, pooled.amount, 'amounts add');
  assert.equal(row.records, pooled.records, 'observation counts add');
  assert.equal(row.average_amount, pooled.average_amount, 'mean = net amount over observations');
  assert.equal(row.stddev_amount, pooled.stddev_amount, 'population deviation pooled from count, mean and deviation');
  assert.equal(row.min_amount, pooled.min_amount);
  assert.equal(row.max_amount, pooled.max_amount);
  assert.equal(row.median_amount, null, 'a median cannot be rebuilt from two medians');
  assert.equal(row.contributors, null, 'a distinct count cannot be added: one donor can be in both groups');
  assert.deepEqual(row.state, ['MN', 'WI'], 'the undrawn field keeps both values, so drill-through asks for both groups');
  assert.deepEqual(outcomes, {amount: 'added', records: 'added', average_amount: 'weighted', stddev_amount: 'pooled', min_amount: 'lowest',
    max_amount: 'highest', median_amount: 'blank', contributors: 'blank'});
  const note = plot.combineNote(combined, outcomes, COLUMNS);
  assert.match(note, /^1 duplicate group was combined because a queried field is not drawn: /);
  assert.match(note, /Net amount and Observation count are added/);
  assert.match(note, /Average observation amount is weighted by observation count/);
  assert.match(note, /Population standard deviation of amounts is pooled/);
  assert.match(note, /Median observation amount and Distinct reported donor identities cannot be rebuilt from group summaries/);
  assert.doesNotMatch(note, /summed/);
});

test('F2: a mean or deviation without observation counts is left blank, never added or guessed', () => {
  const {rows, outcomes} = charts.combine([{company: 'A', state: 'MN', average_amount: 100, stddev_amount: 5}, {company: 'A', state: 'WI', average_amount: 200, stddev_amount: 9}], ['company'], COLUMNS);
  assert.equal(rows[0].average_amount, null);
  assert.equal(rows[0].stddev_amount, null);
  assert.deepEqual(outcomes, {average_amount: 'blank', stddev_amount: 'blank'});
});

test('F2: quartiles are blank, dates widen and the span is recomputed from them', () => {
  const {rows, outcomes} = charts.combine([
    {company: 'A', state: 'MN', q1_amount: 10, q3_amount: 30, first_date: '2024-03-01', last_date: '2024-03-11', span_days: 10, records: 2},
    {company: 'A', state: 'WI', q1_amount: 20, q3_amount: 40, first_date: '2024-02-20', last_date: '2024-03-05', span_days: 14, records: 3}], ['company'], COLUMNS);
  assert.equal(rows[0].q1_amount, null);
  assert.equal(rows[0].q3_amount, null);
  assert.equal(rows[0].first_date, '2024-02-20');
  assert.equal(rows[0].last_date, '2024-03-11');
  assert.equal(rows[0].span_days, 20, 'the combined span, not 10 + 14');
  assert.equal(outcomes.span_days, 'widened');
});

test('F2: without repeats the rows are returned untouched', () => {
  const input = rowsFor('A').map((r, i) => ({...r, company: 'C' + i}));
  const out = charts.combine(input, ['company'], COLUMNS);
  assert.equal(out.rows, input);
  assert.equal(out.combined, 0);
});

test('F2: a worksheet column names its aggregation, so a SUM adds and a MEAN is not added', () => {
  const cols = [{id: 'g', name: 'g'}, {id: 'SUM(Amount)', name: 'SUM(Amount)', aggregation: 'sum'}, {id: 'AVG(Amount)', name: 'AVG(Amount)', aggregation: 'mean'},
    {id: 'MIN(Amount)', name: 'MIN(Amount)', aggregation: 'min'}, {id: 'Distinct', name: 'Distinct', aggregation: 'countd'}];
  const {rows, outcomes} = charts.combine([{g: 'x', 'SUM(Amount)': 5, 'AVG(Amount)': 1, 'MIN(Amount)': 3, Distinct: 2}, {g: 'x', 'SUM(Amount)': 7, 'AVG(Amount)': 9, 'MIN(Amount)': 1, Distinct: 2}], ['g'], cols);
  assert.deepEqual(rows[0], {g: 'x', 'SUM(Amount)': 12, 'AVG(Amount)': null, 'MIN(Amount)': 1, Distinct: null});
  assert.deepEqual(outcomes, {'SUM(Amount)': 'added', 'AVG(Amount)': 'blank', 'MIN(Amount)': 'lowest', Distinct: 'blank'});
  // charts.js hands contribution-charts the worksheet aggregation on each value column.
  const seen = [];
  globalThis.MentatContributionCharts = {render: (container, rows, spec) => { seen.push(spec.columns); return null; }};
  try {
    paint.chartSVG({type: 'heatmap', groups: [{label: '["a","b"]', value: 4, n: 1}], groupColumns: [0, 1], selected: 1, excluded: 0},
      'Amount · Mean', {type: 'heatmap', aggregation: 'mean', x: 0, y: 1, dimensions: [0, 1]},
      {sheet: 'S', columns: [{name: 'A'}, {name: 'B'}, {name: 'Amount', type: 'number'}]}, 'T', 'S', {});
  } finally { globalThis.MentatContributionCharts = charts; }
  assert.equal(seen.length, 1);
  assert.equal(seen[0].find(c => c.name === 'Amount · Mean').aggregation, 'mean');
});

test('F2: a drawn heat map and circle view show the weighted mean and name every rule', () => {
  const input = [{company: 'A', state: 'MN', average_amount: 100, records: 1}, {company: 'A', state: 'WI', average_amount: 200, records: 3}, {company: 'B', state: 'MN', average_amount: 50, records: 2}];
  for (const type of ['heatmap', 'dot']) {
    const container = document.createElement('div');
    const svg = charts.render(container, input, {type, dimensions: ['company'], measures: ['average_amount'], columns: COLUMNS, labels: true}, () => {});
    const a = marks(svg).find(n => /^A;/.test(titleOf(n)));
    assert.match(titleOf(a), /Average observation amount: \$1\.75/, type + ': the weighted mean');
    assert.doesNotMatch(titleOf(a), /\$3\.00/, type);
    assert.match(svg.textContent, /1 duplicate group was combined because a queried field is not drawn: Observation count is added; Average observation amount is weighted by observation count\./, type);
    assert.doesNotMatch(svg.textContent, /summed/, type);
  }
});

test('F2: a combined box keeps its whiskers and draws no quartiles it does not have', () => {
  const input = [...rowsFor('A').map(r => ({...r, q1_amount: 1, q3_amount: 2})), {company: 'B', state: 'MN', ...stats([10, 20, 30]), q1_amount: 15, q3_amount: 25}];
  const container = document.createElement('div');
  const svg = charts.render(container, input, {type: 'box', dimensions: ['company'], measures: ['median_amount'], columns: COLUMNS}, () => {});
  const a = marks(svg).find(n => /^A;/.test(titleOf(n)));
  assert.equal(attr(a, 'data-quartiles'), 'unavailable');
  assert.match(titleOf(a), /Minimum amount: \$0\.50; .*Maximum amount: \$3\.50; quartiles cannot be rebuilt for a combined group/);
  const b = marks(svg).find(n => /^B;/.test(titleOf(n)));
  assert.equal(attr(b, 'data-quartiles'), undefined, 'an uncombined group draws its box');
  const bad = all(svg, n => [...n.attrs.values()].some(v => /NaN|undefined|Infinity/.test(v)));
  assert.deepEqual(bad.map(n => n.tagName), [], 'no attribute reads NaN, undefined or Infinity');
  assert.match(svg.textContent, /Median observation amount, .*q1 amount and q3 amount cannot be rebuilt from group summaries/);
});

test('F2: a timeline with a repeated period weights the mean instead of adding it', () => {
  const container = document.createElement('div');
  plot.render(container, [{year: '2022', state: 'MN', average_amount: 100, records: 1}, {year: '2022', state: 'WI', average_amount: 200, records: 3}, {year: '2023', state: 'MN', average_amount: 50, records: 1}],
    {type: 'line', dimensions: ['year'], measures: ['average_amount'], columns: [{id: 'year', name: 'Year', role: 'dimension'}, ...COLUMNS]}, () => {});
  const first = marks(container)[0];
  assert.match(titleOf(first), /\$1\.75/);
  assert.doesNotMatch(titleOf(first), /\$3\.00/);
  assert.match(container.textContent, /1 duplicate group was combined because a queried field is not drawn: Observation count is added; Average observation amount is weighted by observation count\./);
});
