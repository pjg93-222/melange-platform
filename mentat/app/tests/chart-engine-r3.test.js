/* r3 Chart Studio engine (ENGINE CONTRACT, final/r3/cs/CENSUS.md section G).
   Paul, 2026-09-23: "within the Chart Studio make sure everything is freely
   flexible, that all graph types properly register and nothing is logically
   constrained, it needs to be utterly maluable".

   Part 1 is RULE 0: without an r3 field, build / temporal / quickConfig /
   chartSVG / MentatGroupedSVG / MentatContributionPlot.render return exactly
   what they returned before r3 (golden digests captured from the pre-r3 files).
   Part 2 exercises every r3 field. Run with node --test on this file. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.join(__dirname, '..');
const {math, paint, grouped, plot} = corpus.loadEngine(APP);
globalThis.MentatContributionCharts = require('../contribution-charts.js');
const {D2, D4, D5, D8} = corpus.datasets;

const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const byTag = (node, tag) => all(node, n => n.tagName === tag);
const attr = (node, key) => node.attrs.get(key);
const titled = node => all(node, n => ['rect', 'circle', 'path'].includes(n.tagName) && n.children.some(c => c.tagName === 'title'));
const titleOf = node => node.children.find(c => c.tagName === 'title').textContent;
const labelsIn = svg => all(svg, n => n.tagName === 'text' && attr(n, 'data-mark-label') === 'true');
const L = (config, data = D2) => math.build(data, Object.assign({lenient: true, filters: [], series: -1}, config));
const draw = (result, config, options, data = D2) => paint.chartSVG(result, 'Measure', config, data, 'Title', 'Scope', options);

/* ------------------------------------------------------------ Part 1: RULE 0 */

test('RULE 0: every legacy build, table, label and SVG is byte-identical to the pre-r3 engine', () => {
  const golden = JSON.parse(fs.readFileSync(path.join(__dirname, 'chart-engine-r3.golden.json'), 'utf8'));
  const outputs = corpus.run(APP);
  assert.equal(outputs.length, golden.cases, 'the corpus has the golden case count');
  assert.equal(corpus.idsDigest(outputs), golden.ids, 'the corpus lists the golden ids in order');
  const differing = [];
  outputs.forEach(([id, text], i) => {
    if (id.startsWith('changed:') || id.includes(':changed:')) return;
    if (corpus.digest(text) !== golden.digests.slice(i * 16, i * 16 + 16)) differing.push(id);
  });
  assert.deepEqual(differing, [], 'legacy outputs changed');
  // loadEngine inside run() replaced the globals; restore this file's view.
  Object.assign(globalThis, {MentatContributionCharts: require('../contribution-charts.js')});
});

test('RULE 0: the retired refusals are the only changed outputs, and they now draw', () => {
  const outputs = new Map(corpus.run(APP));
  for (const [id, text] of outputs) if (id.startsWith('changed:') || id.includes(':changed:')) assert.doesNotMatch(text, /^THROW:/, id);
});

test('W1 lenient: legacy temporal views keep their values; composition refusals become warnings', () => {
  for (const transform of ['annual', 'yoy', 'change', 'forecast']) {
    const config = {type: 'line', x: 0, y: 1, aggregation: 'sum', transform, period: 'year', horizon: 2, as_of: '2026-06-01', series: -1, filters: []};
    const legacy = math.build(D4, config), lenient = math.build(D4, {...config, lenient: true});
    delete lenient.warnings;
    assert.deepEqual(lenient, legacy, transform);
  }
  const refusals = [
    [{type: 'pie', x: 2, y: 5, aggregation: 'sum'}, /negative value is left out/],
    [{type: 'radar', x: 3, y: 6, aggregation: 'sum'}, /at least three groups/],
    [{type: 'bar', x: 2, y: 5, aggregation: 'sum', filters: [{column: 99, op: 'equals', value: 1}]}, /filter on a field/],
    [{type: 'bar', x: 2, y: 5, aggregation: 'sum', filters: [{column: 5, op: 'gte', value: 'x'}]}, /compares text/],
    [{type: 'bar', x: 2, y: 5, aggregation: 'sum', start: 9, end: 3}, /swapped/],
    [{type: 'bar', x: 2, y: 5, aggregation: 'sum', limit: 2.5}, /rounded to 2/],
    [{type: 'bar', x: 2, y: 5, aggregation: 'sum', maxGroups: -3}, /default of 60/],
    [{type: 'scatter', x: -1, y: 5, aggregation: 'sum'}, /dot plot/],
    [{type: 'bar', x: 2, y: 5, aggregation: 'sum', filters: [{column: 2, op: 'equals', value: 'none'}]}, /No rows match/],
    [{type: 'line', x: 2, y: 5, aggregation: 'sum', transform: 'rolling'}, /Unknown time analysis/],
    [{type: 'line', x: 9, y: 5, aggregation: 'sum', transform: 'annual'}, /needs a date/]
  ];
  for (const [config, pattern] of refusals) {
    const result = L(config);
    assert.ok(Array.isArray(result.warnings), JSON.stringify(config));
    assert.match(result.warnings.join(' '), pattern);
  }
  assert.equal(L({type: 'pie', x: 2, y: 5, aggregation: 'sum'}).excludedNegative, 1);
  assert.equal(L({type: 'scatter', x: -1, y: 5, aggregation: 'sum'}).type, 'dot');
  assert.deepEqual(L({type: 'pie', x: 2, y: 5, aggregation: 'sum', filters: [{column: 2, op: 'equals', value: 'none'}]}).groups, []);
  // Programming errors still throw under lenient.
  assert.throws(() => L({type: 'bar', x: 2, y: 5, aggregation: 'avg'}), /Choose an aggregation/);
  assert.throws(() => L({type: 'sparkline', x: 2, y: 5, aggregation: 'sum'}), /Choose a chart type/);
});

test('W1 lenient: honesty guards draw and withhold the total with their own sentence', () => {
  const split = {columns: [{name: 'ZIPs', type: 'category'}, {name: 'Amount', type: 'number'}], rows: [['1; 2', 10], ['1', 5]], row_numbers: [2, 3]};
  const r = math.build(split, {type: 'bar', x: 0, y: 1, aggregation: 'sum', splitMulti: true, lenient: true, filters: []});
  assert.equal(r.totalRefusal, 'Grand total is not shown: split cells repeat the measure across values.');
  assert.deepEqual(r.groups.map(g => [g.label, g.value]), [['1', 15], ['2', 10]]);
  const unsafe = {columns: [{name: 'Company'}, {name: 'Amount', type: 'number', accounting: {safe_to_sum: false, reason: 'Choose Analysis Amount.'}}], rows: [['A', 1]], row_numbers: [2]};
  const guarded = math.build(unsafe, {type: 'bar', x: 0, y: 1, aggregation: 'sum', lenient: true, filters: []});
  assert.equal(guarded.totalRefusal, 'Choose Analysis Amount.');
  assert.equal(guarded.groups[0].value, 1);
  assert.throws(() => math.build(unsafe, {type: 'bar', x: 0, y: 1, aggregation: 'sum', filters: []}), /Choose Analysis Amount/, 'legacy keeps the refusal');
  const pairs = {columns: [{name: 'ZIPs', type: 'category'}, {name: 'States', type: 'category'}], rows: [['1; 2', 'NY; NJ']], row_numbers: [2]};
  assert.throws(() => math.build(pairs, {type: 'bar', x: 0, y: -1, aggregation: 'count', dimensions: [0, 1], splitMulti: true, filters: []}), /Do not split multiple fields/);
  const twoFields = math.build(pairs, {type: 'bar', x: 0, y: -1, aggregation: 'count', dimensions: [0, 1], splitMulti: true, lenient: true, filters: []});
  assert.equal(twoFields.groups.length, 4, 'the cartesian split');
  assert.match(twoFields.warnings.join(' '), /every pairing/);
});

/* ------------------------------------------------------ Part 2: worksheet engine */

test('W2 several measures become series; with Colour they pair member · measure', () => {
  const two = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'mean'}]});
  assert.equal(two.type, 'grouped');
  assert.equal(two.measureSeries, true);
  assert.deepEqual(two.series, ['SUM(Amount)', 'AVG(Cost)']);
  assert.deepEqual(two.seriesConfig, [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'mean'}]);
  assert.deepEqual(two.groups[0].values.map(v => v.value), [2200, 56.5]);
  for (const [type, mapped] of [['horizontal', 'groupedhorizontal'], ['line', 'multiline'], ['area', 'multiarea'], ['dot', 'multidot']])
    assert.equal(L({type, x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'sum'}]}).type, mapped);
  const paired = L({type: 'bar', x: 2, series: 3, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'max'}]});
  assert.deepEqual(paired.series, ['R · SUM(Amount)', 'R · MAX(Cost)', 'D · SUM(Amount)', 'D · MAX(Cost)']);
  const pie = L({type: 'pie', x: 2, y: 6, aggregation: 'sum', measures: [{column: 6, aggregation: 'sum'}, {column: 4, aggregation: 'max'}]});
  assert.equal(pie.type, 'pie');
  assert.deepEqual(pie.extraLabels, ['MAX(Receipt count)']);
  assert.match(pie.warnings.join(' '), /shows one measure/);
  const scatter = L({type: 'scatter', x: 6, y: 5, aggregation: 'sum', measures: [{column: 6, aggregation: 'sum'}, {column: 5, aggregation: 'sum'}, {column: 4, aggregation: 'sum'}, {column: 1, aggregation: 'sum'}]});
  assert.equal(scatter.points[0].x, 10);
  assert.equal(scatter.points[0].y, 100);
  assert.equal(scatter.points[0].size, 1);
  assert.deepEqual(Object.keys(scatter.points[0].extra), ['SUM(Election year)']);
  const same = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 5, aggregation: 'sum'}]});
  assert.deepEqual(same.series, ['SUM(Amount)', 'SUM(Amount) (2)']);
});

test('W3 stacking: result.stack, and shares of the positive total for percent', () => {
  const stacked = L({type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum', stack: 'stacked'});
  assert.equal(stacked.stack, 'stacked');
  const percent = L({type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum', stack: 'percent'});
  assert.equal(percent.units, 'percent');
  assert.deepEqual(percent.groups[0].values.map(v => v.share), [800 / 2200, 1400 / 2200]);
  assert.deepEqual(percent.groups[1].values.map(v => v.share), [1, -500 / 1600]);
  assert.match(L({type: 'bar', x: 2, y: 5, aggregation: 'sum', stack: 'stacked'}).warnings.join(' '), /Stacking needs several series/);
});

test('W4 dual axis: result.dual carries left, right and the right-axis mark', () => {
  const dual = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 4, aggregation: 'count'}], dualAxis: true, dualMark: 'bar'});
  assert.deepEqual(dual.dual, {left: [0], right: [1], mark: 'bar'});
  const chosen = L({type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum', dualAxis: true, dualRight: [0]});
  assert.deepEqual(chosen.dual, {left: [1], right: [0], mark: 'line'});
  assert.match(L({type: 'bar', x: 2, y: 5, aggregation: 'sum', dualAxis: true}).warnings.join(' '), /second value axis needs two series/);
});

test('W5 time analysis by series: per-series change, forecast and period totals', () => {
  const yoy = L({type: 'line', x: 0, y: 1, aggregation: 'sum', series: 2, transform: 'yoy', period: 'year'}, D4);
  assert.equal(yoy.type, 'multiline');
  assert.deepEqual(yoy.series, ['D', 'R']);
  assert.equal(yoy.temporal, true);
  assert.equal(yoy.observedTotal, null);
  assert.deepEqual(yoy.groups[0].values.map(v => [v.series, v.prior]), [['D', 130], ['R', 56]]);
  const forecast = L({type: 'bar', x: 0, y: 1, aggregation: 'sum', series: 2, transform: 'forecast', period: 'year', horizon: 12, as_of: '2026-06-01'}, D4);
  assert.equal(forecast.type, 'grouped');
  assert.equal(forecast.groups.filter(g => g.forecast).length, 12);
  assert.match(forecast.warnings.join(' '), /beyond 10 periods/);
  const cycles = L({type: 'line', x: 0, y: 2, aggregation: 'sum', transform: 'yoy', period: 'year'}, D8);
  assert.equal(cycles.units, 'percent');
  assert.deepEqual(cycles.groups.map(g => g.value), [50, 2250 / 1500 * 100 - 100 + 16.666666666666657]);
  const pie = L({type: 'pie', x: 0, y: 1, aggregation: 'sum', series: 2, transform: 'annual', period: 'year'}, D4);
  assert.equal(pie.type, 'pie');
  assert.equal(pie.groups[0].value, 186);
  const subjects = {columns: [{index: 0, name: 'Year', type: 'number'}, {index: 1, name: 'Amount', type: 'number'}, {index: 2, name: 'Subject', type: 'category'}],
    rows: [['2020', 1, 'A'], ['2021', 2, 'A'], ['2020', 5, 'B'], ['2021', 7, 'B']], row_numbers: [2, 3, 4, 5]};
  const bySubject = math.build(subjects, {type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'change', period: 'year', lenient: true, series: -1, filters: []});
  assert.deepEqual(bySubject.series, ['A', 'B']);
  assert.deepEqual(bySubject.groups[0].values.map(v => v.value), [1, 2]);
  const short = L({type: 'line', x: 0, y: 1, aggregation: 'sum', transform: 'forecast', period: 'year', as_of: '2026-06-01', filters: [{column: 0, op: 'gte', value: 2020}]}, D4);
  assert.equal(short.groups.some(g => g.forecast), false);
  assert.match(short.warnings.join(' '), /No baseline is projected/);
});

test('W6 new mark types build: dot, multidot, multiarea, heatmap, highlight, treemap, bubbles, bullet, gantt', () => {
  for (const type of ['dot', 'multidot', 'multiarea', 'heatmap', 'highlight', 'treemap', 'bubbles', 'bullet', 'gantt']) assert.ok(math.chartTypes.includes(type), type);
  assert.deepEqual(math.chartTypes.slice(0, 15), ['bar', 'pie', 'line', 'scatter', 'box', 'histogram', 'area', 'doughnut', 'waterfall', 'radar', 'horizontal', 'pareto', 'grouped', 'groupedhorizontal', 'multiline']);
  assert.deepEqual(L({type: 'dot', x: 2, y: 5, aggregation: 'median'}).groups.map(g => g.value), [550, 350, 750]);
  const heat = L({type: 'heatmap', x: 2, y: 5, aggregation: 'sum', dimensions: [2, 3]});
  assert.deepEqual(heat.groupColumns, [2, 3]);
  const gantt = L({type: 'gantt', x: 1, y: -1, aggregation: 'count', dateColumn: 6}, D8);
  assert.deepEqual(gantt.groups.map(g => [g.label, g.first, g.last, g.span]), [['Company PAC', '2021-03-01', '2025-02-01', 1433], ['Trade Association', '2021-06-01', '2025-07-01', 1491], ['Leadership PAC', '2023-09-01', '2023-09-01', 0]]);
  assert.equal(L({type: 'gantt', x: 2, y: -1, aggregation: 'count'}).type, 'horizontal', 'no date field: bars with a warning');
  const bullet = L({type: 'bullet', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'sum'}]});
  assert.equal(bullet.type, 'bullet');
  assert.deepEqual(bullet.series, ['SUM(Amount)', 'SUM(Cost)']);
});

test('W7 Size, Tooltip and Label fields aggregate per mark', () => {
  const r = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', size: {column: 6, aggregation: 'sum'},
    tooltip: [{column: 4, aggregation: 'count'}, {column: 3, aggregation: 'countd'}], label: {column: 3, aggregation: 'first'}});
  assert.deepEqual(r.groups[0], {label: 'A', value: 2200, n: 4, size: 226, extra: {'COUNT(Receipt count)': 4, 'COUNTD(Party)': 2}, labelValue: 'R'});
  assert.equal(r.sizeLabel, 'SUM(Cost)');
  assert.deepEqual(r.extraLabels, ['COUNT(Receipt count)', 'COUNTD(Party)']);
  const series = L({type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum', size: {column: 6, aggregation: 'max'}});
  assert.equal(series.groups[0].values[0].size, 72);
  assert.match(L({type: 'pie', x: 2, y: 6, aggregation: 'sum', size: {column: 6, aggregation: 'sum'}}).warnings.join(' '), /Size is not drawn on pie/);
});

test('W9 grain group: scatter points, boxes and bins over Detail-group aggregates', () => {
  const scatter = L({type: 'scatter', x: 6, y: 5, aggregation: 'sum', xAggregation: 'mean', dimensions: [2, 3], grain: 'group'});
  assert.equal(scatter.points.length, 6);
  assert.deepEqual(scatter.points[0], {x: 41, y: 800, label: '["A","R"]'});
  const boxed = L({type: 'box', x: 2, y: 5, aggregation: 'sum', dimensions: [2, 3], grain: 'group'});
  assert.deepEqual(boxed.groups.map(g => [g.label, g.n, g.median]), [['A', 2, 1100], ['B', 2, 550], ['C', 2, 1500]]);
  const counted = L({type: 'histogram', x: -1, y: 5, aggregation: 'count', dimensions: [2], grain: 'group'});
  assert.equal(counted.groups.reduce((n, g) => n + g.value, 0), 3);
});

test('W10 group limit All, bin count and bin width', () => {
  assert.equal(math.groupLimit({maxGroups: 'all'}), Infinity);
  const everything = L({type: 'bar', x: 0, y: 1, aggregation: 'sum', maxGroups: 'all'}, D5);
  assert.equal(everything.groups.length, 70);
  assert.equal(everything.note, undefined);
  assert.equal(L({type: 'histogram', x: -1, y: 5, aggregation: 'sum', bins: 4}).groups.length, 4);
  const wide = L({type: 'histogram', x: -1, y: 5, aggregation: 'sum', binWidth: 250});
  assert.equal(wide.groups[0].label, '-750.00 – -500.00');
  assert.equal(wide.groups.reduce((n, g) => n + g.value, 0), 12);
});

test('W11 Colour on every mark type, and a Colour measure as a sequential fill', () => {
  const scatter = L({type: 'scatter', x: 6, y: 5, aggregation: 'sum', series: 3});
  assert.deepEqual(scatter.series, ['R', 'D']);
  assert.equal(scatter.points[1].series, 'D');
  const histogram = L({type: 'histogram', x: -1, y: 5, aggregation: 'sum', series: 3, bins: 3, stack: 'stacked'});
  assert.deepEqual(histogram.groups[1].values, [{series: 'R', value: 3, n: 3}, {series: 'D', value: 2, n: 2}]);
  assert.equal(histogram.stack, 'stacked');
  const box = L({type: 'box', x: 2, y: 5, aggregation: 'sum', series: 3});
  assert.deepEqual(box.series, ['R', 'D']);
  assert.deepEqual(box.groups.map(g => g.seriesIndex), [0, 1, 0, 1, 0, 1]);
  for (const type of ['pie', 'doughnut', 'waterfall', 'pareto', 'radar']) assert.ok(L({type, x: 2, y: 6, aggregation: 'sum', series: 3}).groups.every(g => Number.isInteger(g.seriesIndex)), type);
  assert.equal(L({type: 'area', x: 1, y: 5, aggregation: 'sum', series: 3}).type, 'multiarea');
  assert.equal(L({type: 'dot', x: 2, y: 5, aggregation: 'sum', series: 3}).type, 'multidot');
  const sameAsAxis = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', series: 2});
  assert.equal(sameAsAxis.type, 'bar');
  assert.equal(sameAsAxis.warnings, undefined, 'Colour repeating the axis is silent');
  const heat = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', colorMeasure: {column: 6, aggregation: 'mean'}});
  assert.deepEqual(heat.colorRange, [56.5, 76.5]);
  assert.deepEqual(heat.groups.map(g => g.colorValue), [56.5, 66.5, 76.5]);
});

test('W12 countd counts distinct trimmed values, and Other pools raw rows first', () => {
  const d = {columns: [{name: 'Group', type: 'category'}, {name: 'Donor', type: 'category'}], rows: [['A', 'x'], ['A', ' x '], ['A', 'y'], ['B', 'x'], ['C', 'x'], ['C', 'z']], row_numbers: [1, 2, 3, 4, 5, 6]};
  const r = math.build(d, {type: 'bar', x: 0, y: 1, aggregation: 'countd', maxGroups: 1, filters: []});
  assert.deepEqual(r.groups.map(g => [g.label, g.value]), [['A', 2], ['Other (2 groups)', 2]]);
  assert.match(r.note, /by distinct count/);
});

/* ---------------------------------------------------------- Part 2: renderers */

test('W8 labels: every mark type carries data-mark-label text', () => {
  const cases = [['bar', {type: 'bar', x: 2, y: 5, aggregation: 'sum'}], ['horizontal', {type: 'horizontal', x: 2, y: 5, aggregation: 'sum'}],
    ['line', {type: 'line', x: 1, y: 5, aggregation: 'sum'}], ['area', {type: 'area', x: 1, y: 5, aggregation: 'sum'}], ['dot', {type: 'dot', x: 2, y: 5, aggregation: 'sum'}],
    ['pie', {type: 'pie', x: 2, y: 6, aggregation: 'sum'}], ['doughnut', {type: 'doughnut', x: 2, y: 6, aggregation: 'sum'}], ['waterfall', {type: 'waterfall', x: 2, y: 5, aggregation: 'sum'}],
    ['pareto', {type: 'pareto', x: 2, y: 6, aggregation: 'sum'}], ['radar', {type: 'radar', x: 2, y: 6, aggregation: 'sum'}], ['box', {type: 'box', x: 2, y: 5, aggregation: 'sum'}],
    ['histogram', {type: 'histogram', x: -1, y: 5, aggregation: 'sum', bins: 3}], ['scatter', {type: 'scatter', x: 6, y: 5, aggregation: 'sum'}],
    ['grouped', {type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum'}], ['multiline', {type: 'multiline', x: 2, series: 3, y: 5, aggregation: 'sum'}],
    ['multidot', {type: 'multidot', x: 2, series: 3, y: 5, aggregation: 'sum'}], ['multiarea', {type: 'multiarea', x: 1, series: 3, y: 5, aggregation: 'sum'}]];
  for (const [name, config] of cases) {
    const result = L(config), svg = draw(result, config, {labels: true}).svg;
    const marks = result.points ? result.points.length : result.series ? result.groups.flatMap(g => g.values).filter(v => v.value != null).length : result.groups.length;
    assert.equal(labelsIn(svg).length, marks, name);
    assert.equal(labelsIn(draw(result, config).svg).length, 0, name + ' without options');
  }
  const labelled = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', label: {column: 3, aggregation: 'first'}});
  assert.deepEqual(labelsIn(draw(labelled, {x: 2, y: 5, aggregation: 'sum'}, {labels: true}).svg).map(n => n.textContent), ['R', 'D', 'R']);
  // Paul, 2026-09-24: a pie names its groups in the legend only; each slice of 4% or more carries
  // its share inside the pie (this fixture's slices all do). The label used to read "A · 38.3%",
  // drawn outside the pie, where such labels ran off the drawing's left edge and into the legend.
  const pie = labelsIn(draw(L({type: 'pie', x: 2, y: 6, aggregation: 'sum'}), {x: 2, y: 6, aggregation: 'sum'}, {labels: true}).svg);
  assert.ok(pie.length > 0);
  for (const node of pie) {
    assert.match(node.textContent, /^\d+(\.\d)?%$/);
    assert.ok(Math.hypot(Number(attr(node, 'x')) - 285, Number(attr(node, 'y')) - 290) < 175, 'inside the pie');
  }
});

test('W8 axis labels and pie legend options', () => {
  const config = {type: 'bar', x: 0, y: 1, aggregation: 'sum', maxGroups: 'all'};
  const result = L(config, D5);
  const auto = all(draw(result, config, {}, D5).svg, n => attr(n, 'data-category-label') === 'true');
  const every = all(draw(result, config, {axisLabels: 'all'}, D5).svg, n => attr(n, 'data-category-label') === 'true');
  assert.equal(auto.length, 8, 'every ninth of 70');
  assert.equal(every.length, 70);
  assert.match(attr(every[0], 'transform'), /^rotate\(-30 /);
  const pie = L({type: 'pie', x: 0, y: 1, aggregation: 'sum', maxGroups: 'all'}, D5);
  const legend = svg => byTag(svg, 'rect').filter(n => attr(n, 'x') === '510').length;
  assert.equal(legend(draw(pie, config, {}, D5).svg), 16);
  const full = draw(pie, config, {legend: 'all'}, D5).svg;
  assert.equal(legend(full), 70);
  assert.equal(attr(full, 'viewBox'), `0 0 960 ${90 + 70 * 32 + 80}`);
});

test('W3/W4 grouped view: stacked segments, percent axis and a right-hand axis', () => {
  const stacked = L({type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum', stack: 'stacked'});
  const svg = grouped(stacked, 'Stacked', '');
  const rects = titled(svg).filter(n => n.tagName === 'rect');
  assert.equal(rects.length, 6);
  const [a0, a1] = rects;
  assert.equal(Number(attr(a0, 'x')), Number(attr(a1, 'x')), 'segments of a group share one column');
  assert.ok(Math.abs(Number(attr(a1, 'y')) + Number(attr(a1, 'height')) - Number(attr(a0, 'y'))) < 1e-9, 'the second segment sits on the first');
  assert.equal(attr(a0, 'stroke'), '#fff');
  assert.equal(svg.__plot.max, 3000);
  assert.equal(svg.__plot.min, -500);
  const percent = grouped(L({type: 'grouped', x: 2, series: 3, y: 5, aggregation: 'sum', stack: 'percent'}), 'Percent', '');
  assert.ok(all(percent, n => n.tagName === 'text' && /^100%$/.test(n.textContent)).length >= 1);
  assert.match(titleOf(titled(percent)[0]), /\(36\.4%\)/);
  const dual = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 4, aggregation: 'count'}], dualAxis: true});
  const dualSvg = grouped(dual, 'Dual', '');
  assert.equal(all(dualSvg, n => attr(n, 'data-axis-tick') === 'right').length, 6);
  assert.equal(attr(all(dualSvg, n => attr(n, 'data-axis-tick') === 'right')[0], 'text-anchor'), 'start');
  assert.ok(all(dualSvg, n => n.tagName === 'text' && n.textContent === 'COUNT(Receipt count) (right axis)').length === 1);
  assert.equal(titled(dualSvg).filter(n => n.tagName === 'circle').length, 3, 'the right series is a line with points');
  const chart = paint.chartSVG(dual, 'Measure', {x: 2, measures: [{column: 5, aggregation: 'sum'}, {column: 4, aggregation: 'count'}]}, D2, 'T', 'S');
  assert.ok(all(chart.svg, n => attr(n, 'data-axis-title') === 'right').length === 1);
});

test('W6 renderers: dot, multidot, multiarea draw; delegated types draw through contribution-charts', () => {
  const dot = draw(L({type: 'dot', x: 2, y: 5, aggregation: 'sum'}), {x: 2, y: 5}).svg;
  assert.equal(titled(dot).filter(n => n.tagName === 'circle').length, 3);
  assert.equal(byTag(dot, 'polyline').length, 0, 'a dot plot draws no line');
  const multidot = grouped(L({type: 'multidot', x: 2, series: 3, y: 5, aggregation: 'sum'}), 'D', '');
  assert.equal(byTag(multidot, 'line').filter(n => attr(n, 'stroke-width') === '2').length, 0);
  const area = grouped(L({type: 'multiarea', x: 1, series: 3, y: 5, aggregation: 'sum', stack: 'stacked'}), 'A', '');
  assert.ok(byTag(area, 'polygon').length >= 2);
  for (const [type, config] of [['heatmap', {dimensions: [2, 3]}], ['highlight', {dimensions: [2]}], ['heatmap', {dimensions: [2, 3, 1]}], ['treemap', {dimensions: [2, 3]}], ['bubbles', {dimensions: [2]}]]) {
    const full = {type, x: config.dimensions[0], y: 6, aggregation: 'sum', ...config};
    const result = L(full), svg = draw(result, full, {labels: true}).svg;
    assert.equal(all(svg, n => attr(n, 'class') === 'plot-mark').length, result.groups.length, type + ' ' + config.dimensions.length);
    assert.equal(attr(svg, 'aria-label'), 'Title');
  }
  const bullet = L({type: 'bullet', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'sum'}]});
  assert.equal(all(draw(bullet, {x: 2}).svg, n => attr(n, 'class') === 'plot-mark').length, 3);
  // Paul, 2026-09-23: "nothing is logically constrained". A one-measure bullet draws as a bullet
  // (its bars, no comparison marker, a note), no longer as a bars fallback (contract V07/B-E33).
  const single = L({type: 'bullet', x: 2, y: 5, aggregation: 'sum'});
  const singleSvg = draw(single, {x: 2, y: 5}).svg;
  assert.doesNotMatch(singleSvg.textContent, /drawn as bars/);
  assert.match(singleSvg.textContent, /Add a second measure in the same unit/);
  assert.equal(all(singleSvg, n => attr(n, 'class') === 'plot-mark').length, single.groups.length);
  const gantt = L({type: 'gantt', x: 1, y: -1, aggregation: 'count', dateColumn: 6}, D8);
  assert.equal(all(draw(gantt, {x: 1}, {}, D8).svg, n => attr(n, 'class') === 'plot-mark').length, 3);
  const saved = globalThis.MentatContributionCharts;
  delete globalThis.MentatContributionCharts;
  try { assert.throws(() => draw(L({type: 'treemap', x: 2, y: 6, aggregation: 'sum'}), {x: 2}), /treemap needs contribution-charts\.js/); }
  finally { globalThis.MentatContributionCharts = saved; }
});

test('W7/W11 renderers: size, tooltip lines, colour measure and Colour legends', () => {
  const sized = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', size: {column: 6, aggregation: 'sum'}, tooltip: [{column: 4, aggregation: 'count'}]});
  const bars = titled(draw(sized, {x: 2, y: 5}).svg);
  assert.ok(Number(attr(bars[2], 'width')) > Number(attr(bars[0], 'width')));
  assert.match(titleOf(bars[0]), /\nSUM\(Cost\): 226\nCOUNT\(Receipt count\): 4$/);
  const scatter = L({type: 'scatter', x: 6, y: 5, aggregation: 'sum', series: 3, size: {column: 4, aggregation: 'sum'}});
  const points = titled(draw(scatter, {x: 6, y: 5}).svg);
  assert.ok(Number(attr(points[11], 'r')) > Number(attr(points[0], 'r')));
  assert.notEqual(attr(points[0], 'fill'), attr(points[1], 'fill'), 'points take their Colour member');
  assert.equal(all(draw(scatter, {x: 6, y: 5}).svg, n => attr(n, 'data-legend') === 'series').length, 2);
  const lined = L({type: 'line', x: 1, y: 5, aggregation: 'sum', size: {column: 6, aggregation: 'sum'}});
  const widths = byTag(draw(lined, {x: 1, y: 5}).svg, 'line').filter(n => attr(n, 'stroke-linecap') === 'round').map(n => Number(attr(n, 'stroke-width')));
  assert.equal(widths.length, 5);
  assert.ok(widths.every(w => w >= 1.5 && w <= 6));
  const heat = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', colorMeasure: {column: 6, aggregation: 'mean'}});
  const heated = titled(draw(heat, {x: 2, y: 5}).svg);
  assert.deepEqual(heated.map(n => Number(attr(n, 'fill-opacity'))), [.25, .625, 1]);
  assert.equal(all(draw(heat, {x: 2, y: 5}).svg, n => attr(n, 'data-legend') === 'colour').length, 2);
  const box = draw(L({type: 'box', x: 2, y: 5, aggregation: 'sum', series: 3}), {x: 2, y: 5}).svg;
  assert.equal(all(box, n => attr(n, 'data-legend') === 'series').length, 2);
});

test('W1 renderers: empty and thin lenient results draw instead of throwing', () => {
  for (const type of ['bar', 'horizontal', 'line', 'pie', 'box', 'scatter', 'histogram', 'radar', 'dot', 'waterfall', 'grouped']) {
    const config = {type, x: 2, y: 5, series: type === 'grouped' ? 3 : -1, aggregation: 'sum', filters: [{column: 2, op: 'equals', value: 'none'}]};
    assert.doesNotThrow(() => draw(L(config), config), type);
  }
  const thin = L({type: 'radar', x: 3, y: 6, aggregation: 'sum'});
  assert.doesNotThrow(() => draw(thin, {x: 3, y: 6}));
  assert.throws(() => draw({...thin, warnings: undefined}, {x: 3, y: 6}), /at least three groups/, 'a legacy radar result still refuses');
});

test('tableRows and measureLabel read several measures, countd and the r3 columns', () => {
  const two = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'mean'}]});
  const config = {x: 2, y: 5, aggregation: 'sum', measures: [{column: 5, aggregation: 'sum'}, {column: 6, aggregation: 'mean'}]};
  assert.deepEqual(paint.tableRows(two, config, D2)[0], ['Region', 'SUM(Amount)', 'AVG(Cost)']);
  assert.equal(paint.measureLabel(config, two, D2), 'Amount · Sum / Cost · Mean');
  assert.equal(paint.measureLabel({y: 3, aggregation: 'countd'}, {type: 'bar'}, D2), 'Distinct count of Party');
  const encoded = L({type: 'bar', x: 2, y: 5, aggregation: 'sum', size: {column: 6, aggregation: 'sum'}, tooltip: [{column: 4, aggregation: 'count'}]});
  assert.deepEqual(paint.tableRows(encoded, {x: 2, y: 5, aggregation: 'sum'}, D2)[0], ['Region', 'Amount · Sum', 'Size · SUM(Cost)', 'COUNT(Receipt count)']);
  const scatter = L({type: 'scatter', x: 6, y: 5, aggregation: 'sum', series: 3});
  assert.deepEqual(paint.tableRows(scatter, {x: 6, y: 5}, D2)[0], ['Cost', 'Amount', 'Colour']);
  const gantt = L({type: 'gantt', x: 1, y: -1, aggregation: 'count', dateColumn: 6}, D8);
  assert.deepEqual(paint.tableRows(gantt, {x: 1, y: -1, aggregation: 'count'}, D8)[0], ['Recipient Category', 'Span (days)', 'First date', 'Last date', 'Span (days)']);
});

/* ------------------------------------------------------ Part 2: verified renderer */

const PLOT_COLUMNS = [{id: 'amount', name: 'Net giving', unit: 'cents'}, {id: 'records', name: 'Observations', unit: 'count'}, {id: 'company', name: 'Company'}, {id: 'party', name: 'Party'}, {id: 'year', name: 'Year'}, {id: 'gross_amount', name: 'Gross', unit: 'cents'}];
const plotted = (rows, spec) => { const container = document.createElement('div'); plot.render(container, rows, {columns: PLOT_COLUMNS, ...spec}, () => {}); return container; };
const plotMarks = tree => all(tree, n => attr(n, 'class') === 'plot-mark');
const ROWS = [{company: 'A', party: 'D', amount: 100, records: 2, gross_amount: 150}, {company: 'A', party: 'R', amount: 300, records: 8, gross_amount: 350},
  {company: 'B', party: 'D', amount: -50, records: 1, gross_amount: 20}, {company: 'B', party: 'R', amount: 200, records: 4, gross_amount: 220}];

test('V01 categorical lines and areas: category positions in row order, one segment per series', () => {
  const line = plotted(ROWS, {type: 'line', dimensions: ['company', 'party'], measures: ['amount']});
  const marks = plotMarks(line);
  assert.equal(marks.length, 4);
  assert.deepEqual(marks.map(m => Number(attr(m, 'cx'))), [100, 940, 100, 940]);
  assert.equal(all(line, n => n.tagName === 'path' && attr(n, 'stroke-width') === '2.5').length, 2);
  const years = plotted([{year: '2024', amount: 1}, {year: '2020', amount: 2}, {year: '2022', amount: 3}], {type: 'area', dimensions: ['year'], measures: ['amount'], categorical: true});
  assert.deepEqual(plotMarks(years).map(m => Number(attr(m, 'cx'))), [100, 520, 940], 'a value-sorted time axis keeps the delivered order');
});

test('V02 stacked bars: one row per group, Colour segments from zero, row totals as labels', () => {
  const tree = plotted(ROWS, {type: 'bar', dimensions: ['company', 'party'], measures: ['amount'], color: 'party', stack: 'stacked', labels: true});
  const marks = plotMarks(tree);
  assert.equal(marks.length, 4);
  assert.equal(attr(marks[0], 'y'), attr(marks[1], 'y'), 'A: both segments on one row');
  assert.ok(Math.abs(Number(attr(marks[0], 'x')) + Number(attr(marks[0], 'width')) - Number(attr(marks[1], 'x'))) < 1e-9, 'R starts where D ends');
  assert.ok(Number(attr(marks[2], 'x')) < Number(attr(marks[3], 'x')), 'B: the negative segment lies left of zero');
  assert.deepEqual(all(tree, n => attr(n, 'data-label') === 'value').map(n => n._text), ['$4', '$2'], 'row totals in whole dollars');
  const percent = plotted(ROWS, {type: 'bar', dimensions: ['company', 'party'], measures: ['amount'], color: 'party', stack: 'percent'});
  assert.deepEqual(all(percent, n => n.tagName === 'text' && /%$/.test(n._text || '')).map(n => n._text), ['-25%', '0%', '25%', '50%', '75%', '100%'], 'a 0-100% axis');
});

test('V03 dual axis: one panel, measures[1..] on a right-hand axis', () => {
  const tree = plotted([{company: 'A', amount: 100, records: 2}, {company: 'B', amount: 300, records: 9}], {type: 'bar', dimensions: ['company'], measures: ['amount', 'records'], dual: true});
  const svg = byTag(tree, 'svg')[0];
  assert.equal(all(svg, n => n.tagName === 'g' && /translate/.test(attr(n, 'transform') || '')).length, 1, 'one panel');
  assert.equal(all(svg, n => attr(n, 'data-axis') === 'right').length, 6);
  assert.equal(plotMarks(tree).filter(n => n.tagName === 'rect').length, 2);
  assert.equal(plotMarks(tree).filter(n => n.tagName === 'circle').length, 2);
  const bars = plotted([{company: 'A', amount: 100, records: 2}], {type: 'line', dimensions: ['company'], measures: ['amount', 'records'], dual: true, dualMark: 'bar'});
  assert.equal(plotMarks(bars).filter(n => n.tagName === 'rect').length, 1);
});

test('V04 size on bars and lines; V07 scatter with three measures; V09 colour measure', () => {
  const bars = plotMarks(plotted(ROWS.slice(0, 2), {type: 'bar', dimensions: ['company', 'party'], measures: ['amount'], size: 'records'}));
  assert.ok(Number(attr(bars[1], 'height')) > Number(attr(bars[0], 'height')));
  const line = plotted([{year: '2020', amount: 1, records: 1}, {year: '2021', amount: 2, records: 9}, {year: '2022', amount: 3, records: 4}], {type: 'line', dimensions: ['year'], measures: ['amount'], size: 'records'});
  const strokes = all(line, n => n.tagName === 'path' && attr(n, 'stroke-linecap') === 'round').map(n => Number(attr(n, 'stroke-width')));
  assert.equal(strokes.length, 2);
  assert.ok(strokes[0] > strokes[1] - 1 && strokes.every(w => w >= 1.5 && w <= 6));
  const scatter = plotMarks(plotted(ROWS, {type: 'scatter', dimensions: ['company', 'party'], measures: ['amount', 'records', 'gross_amount', 'amount_extra'].slice(0, 3)}));
  assert.equal(scatter.length, 4);
  assert.ok(Number(attr(scatter[1], 'r')) > Number(attr(scatter[2], 'r')), 'the third measure sizes the points');
  const heated = plotMarks(plotted(ROWS, {type: 'bar', dimensions: ['company', 'party'], measures: ['amount'], colorMeasure: 'records'}));
  assert.deepEqual(heated.map(n => Number(attr(n, 'fill-opacity')).toFixed(3)), ['0.357', '1.000', '0.250', '0.571']);
});

test('V07 repeated groups are summed with a note; the direct timeline() still refuses them', () => {
  const tree = plotted([{year: '2022', amount: 100}, {year: '2022', amount: 200}, {year: '2023', amount: 50}], {type: 'line', dimensions: ['year'], measures: ['amount']});
  // Code review F2: repeats are combined statistic by statistic; an amount is added and the note says which rule ran.
  assert.match(tree.textContent, /1 duplicate group was combined because a queried field is not drawn: Net giving is added\./);
  assert.match(titleOf(plotMarks(tree)[0]), /\$3\.00/);
  assert.throws(() => plot.timeline([{year: '2022', amount: 1}, {year: '2022', amount: 2}], {type: 'line', dimensions: ['year'], measures: ['amount']}, 'amount'), /duplicate aggregate groups/);
});

test('V06 reference lines follow the stacked scale', () => {
  const tree = plotted(ROWS, {type: 'bar', dimensions: ['company', 'party'], measures: ['amount'], color: 'party', stack: 'stacked', references: {average: true}});
  // Totals 400 and 150 cents: mean 275, drawn on the stacked axis [-50, 400].
  assert.match(tree.textContent, /Mean of shown marks: \$2\.8/);
  const rule = all(tree, n => attr(n, 'data-reference') === 'average')[0].children.find(n => n.tagName === 'line');
  assert.ok(Math.abs(Number(attr(rule, 'x1')) - (255 + (275 + 50) / 450 * 620)) < 1e-9);
});
