/* Lane F2b (boards #50-#52, 2026-09-26): the Chart Studio chart card at the boards' fidelity.

   core.conciseTitle is the card's one-line title. contribution-plot.js takes four opt-in fields for a
   scatter and a one-panel line or area - plotHeight, legendPlacement 'right', labelCollide, endLabels -
   and without them draws exactly as before (tests/chart-engine-r3.test.js RULE 0 holds the goldens;
   the last test here holds the gating itself).

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.resolve(__dirname, '..');
const {plot} = corpus.loadEngine(APP);
const core = require('../chart-studio-core.js');

const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const attr = (node, key) => node.attrs && node.attrs.get(key);
const byData = (svg, key, value) => all(svg, n => n.attrs && n.attrs.has(key) && (value == null || attr(n, key) === value));
// A text element's own words (its <title> child repeats them for the tooltip).
const textOf = node => node._text ?? '';

/* ------------------------------------------------------------ the title */

test('conciseTitle: a measure on each axis reads "<Rows> vs <Columns>, by <Detail>" (#50)', () => {
  assert.equal(core.conciseTitle({xMeasures: ['Observation count'], yMeasures: ['Selected amount'], axisDims: [],
    colorDim: 'Recipient party', detailDims: ['Recipient committee']}), 'Selected amount vs Observation count, by Recipient committee');
  assert.equal(core.conciseTitle({xMeasures: ['Gift count'], yMeasures: ['Selected amount'], colorDim: 'Party'}),
    'Selected amount vs Gift count, by Party', 'with no Detail, Colour names the marks');
});

test('conciseTitle: a dimension on an axis reads "<Measure> by <Dimension>, by <Colour>" (#51)', () => {
  assert.equal(core.conciseTitle({yMeasures: ['Selected amount'], axisDims: ['Contribution month'], colorDim: 'Party'}),
    'Selected amount by Contribution month, by Party');
  assert.equal(core.conciseTitle({yMeasures: ['Selected amount'], axisDims: ['Party'], colorDim: 'Party'}),
    'Selected amount by Party', 'Colour already on an axis is not said twice');
  // Lane G1 (#52): a measure takes its shorter name in the title (was "... and Distinct reported donor identities by ...").
  assert.equal(core.conciseTitle({yMeasures: ['Selected amount', 'Distinct reported donor identities'], axisDims: ['Contribution year']}),
    'Selected amount and Donor identities by Contribution year', 'a dual axis names both measures (#52)');
  assert.equal(core.conciseTitle({yMeasures: ['A', 'B', 'C'], axisDims: ['Year', 'Party']}), 'A, B and C by Year and Party');
});

test('conciseTitle: no measure placed takes the fallback; nothing at all answers empty', () => {
  assert.equal(core.conciseTitle({axisDims: ['Election cycle'], measureFallback: 'Published included receipts'}),
    'Published included receipts by Election cycle');
  assert.equal(core.conciseTitle({}), '');
  assert.equal(core.conciseTitle(null), '');
  assert.equal(core.conciseTitle({yMeasures: [' ', null], axisDims: ['X']}), '', 'blank names are not names');
});

/* ------------------------------------------------------ the chart card */

const COLUMNS = [{id: 'party', name: 'Recipient party', role: 'dimension'}, {id: 'committee', name: 'Recipient committee', role: 'dimension'},
  {id: 'year', name: 'Contribution year', role: 'dimension'}, {id: 'amount', name: 'Selected amount', role: 'measure', unit: 'cents'},
  {id: 'records', name: 'Observation count', role: 'measure', unit: 'count'},
  {id: 'contributors', name: 'Distinct reported donor identities', role: 'measure', unit: 'count'}];
// Many close points around a few outliers, so labels must collide.
const SCATTER_ROWS = Array.from({length: 40}, (_, i) => ({party: ['D', 'R', 'Other'][i % 3], committee: 'Committee ' + i,
  records: i < 36 ? 10 + (i % 6) : 400 + i * 30, amount: i < 36 ? 100000 + (i % 5) * 900 : 5000000 + i * 90000, contributors: 5 + (i * 7) % 60}));
const SCATTER = {type: 'scatter', dimensions: ['party', 'committee'], measures: ['records', 'amount'], color: 'party', size: 'contributors',
  labelFields: [], tooltip: [], labels: true, columns: COLUMNS, references: {}};
const LINE_ROWS = ['2021', '2022', '2023', '2024', '2025', '2026'].flatMap((year, i) =>
  ['D', 'R'].map((party, j) => ({year, party, amount: 100000 + i * 20000 + j * 5000 + (i === 3 ? 90000 : 0)})));
const LINE = {type: 'line', dimensions: ['year', 'party'], measures: ['amount'], color: 'party', labelFields: [], tooltip: [],
  labels: true, columns: COLUMNS, categorical: true, references: {}};
const FIELDS = {legendPlacement: 'right', labelCollide: true, endLabels: true};

function draw(rows, spec) {
  const container = document.createElement('div');
  plot.render(container, rows, spec, () => {});
  return container.children.find(node => node.tagName === 'svg');
}
// The label's box as the renderer measures it without a canvas (length x size), at the 18px the Studio draws.
function box(node) {
  const text = textOf(node).length, width = text * 18, x = Number(attr(node, 'x')), y = Number(attr(node, 'y'));
  const anchor = attr(node, 'text-anchor') || 'start';
  return {x: anchor === 'end' ? x - width : anchor === 'middle' ? x - width / 2 : x, y: y - 14.4, w: width, h: 18};
}
const overlap = (a, b) => a.x < b.x + b.w - 1 && b.x < a.x + a.w - 1 && a.y < b.y + b.h - 1 && b.y < a.y + a.h - 1;

test('legendPlacement right: Colour and Size legends are cards at the plot\'s right, none under it (#50)', () => {
  const svg = draw(SCATTER_ROWS, {...SCATTER, ...FIELDS});
  const cards = byData(svg, 'data-legend-card');
  assert.deepEqual(cards.map(card => attr(card, 'data-legend-card')), ['series', 'size']);
  assert.equal(byData(svg, 'data-legend', 'series').length, 0, 'no legend row under the plot');
  // Each text carries its full words in its <title> (a long name is fitted with an ellipsis).
  const series = cards[0], texts = all(series, n => n.tagName === 'text').map(n => n.children.find(c => c.tagName === 'title').textContent);
  assert.deepEqual(texts, ['Recipient party', 'D', 'Other', 'R'], 'the Colour card names its field and each member');
  const sizes = all(cards[1], n => n.tagName === 'circle').map(n => Number(attr(n, 'r')));
  assert.equal(sizes.length, 3, 'three reference sizes');
  assert.ok(sizes[0] < sizes[1] && sizes[1] < sizes[2], 'growing as the marks grow');
  const rect = all(series, n => n.tagName === 'rect')[0];
  const gridRight = Math.max(...all(svg, n => n.tagName === 'line' && attr(n, 'x1') === attr(n, 'x2')).map(n => Number(attr(n, 'x1'))));
  assert.ok(gridRight < Number(attr(rect, 'x')), `the plot (to ${gridRight}) ends before the legend column (${attr(rect, 'x')})`);
});

test('labelCollide: no two mark labels overlap, and the extremes keep theirs (#50)', () => {
  const svg = draw(SCATTER_ROWS, {...SCATTER, ...FIELDS});
  const labels = byData(svg, 'data-label', 'value');
  assert.ok(labels.length > 0 && labels.length < SCATTER_ROWS.length, `some labels are left off (${labels.length} of ${SCATTER_ROWS.length})`);
  const boxes = labels.map(box);
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++)
    assert.ok(!overlap(boxes[i], boxes[j]), `labels "${textOf(labels[i])}" and "${textOf(labels[j])}" overlap`);
  const top = SCATTER_ROWS.reduce((a, r) => r.amount > a.amount ? r : a), right = SCATTER_ROWS.reduce((a, r) => r.records > a.records ? r : a);
  const shown = labels.map(textOf).join('|');
  for (const row of [top, right]) assert.ok(shown.includes(row.committee), row.committee + ', an extreme, keeps its label');
  // Without the field every label is drawn, as before.
  const plain = draw(SCATTER_ROWS, {...SCATTER});
  assert.equal(all(plain, n => n.tagName === 'text' && attr(n, 'fill') === '#35483b').length, SCATTER_ROWS.length);
});

test('endLabels: each coloured line is named at its end, clear of the legend card (#51)', () => {
  const svg = draw(LINE_ROWS, {...LINE, ...FIELDS});
  const names = byData(svg, 'data-end-label');
  assert.deepEqual(names.map(n => attr(n, 'data-end-label')).sort(), ['D', 'R']);
  const card = all(byData(svg, 'data-legend-card', 'series')[0], n => n.tagName === 'rect')[0];
  const cardBox = {x: Number(attr(card, 'x')), y: Number(attr(card, 'y')), w: Number(attr(card, 'width')), h: Number(attr(card, 'height'))};
  for (const name of names) assert.ok(!overlap(box(name), cardBox), 'the name ' + textOf(name) + ' is clear of the legend card');
  const labels = byData(svg, 'data-label', 'value').map(box);
  for (const name of names) for (const label of labels) assert.ok(!overlap(box(name), label), 'no value label sits on a line name');
  assert.equal(byData(draw(LINE_ROWS, {...LINE, legendPlacement: 'right', labelCollide: true}), 'data-end-label').length, 0,
    'without endLabels no line is named');
});

test('plotHeight: the panel takes the height asked (420 to 900) and says so', () => {
  const svg = draw(SCATTER_ROWS, {...SCATTER, ...FIELDS, plotHeight: 610});
  assert.equal(attr(svg, 'viewBox'), '0 0 1000 610');
  assert.equal(attr(svg, 'data-plot-fit'), '610');
  assert.equal(attr(draw(SCATTER_ROWS, {...SCATTER, ...FIELDS, plotHeight: 5000}), 'viewBox'), '0 0 1000 900', 'capped at 900');
  assert.equal(attr(draw(SCATTER_ROWS, {...SCATTER, ...FIELDS, plotHeight: 100}), 'viewBox'), '0 0 1000 420', 'never under 420');
});

test('the fields are opt-in: falsy values draw exactly what no field draws (RULE 0)', () => {
  for (const [rows, spec] of [[SCATTER_ROWS, SCATTER], [LINE_ROWS, LINE]]) {
    const plain = corpus.serialize(draw(rows, {...spec}));
    const off = corpus.serialize(draw(rows, {...spec, legendPlacement: '', labelCollide: false, endLabels: false, plotHeight: 0}));
    assert.equal(off, plain, spec.type + ' draws the same drawing with every field off');
  }
  // Another mark type ignores them.
  const bars = {type: 'bar', dimensions: ['party'], measures: ['amount'], labelFields: [], tooltip: [], labels: true, columns: COLUMNS, references: {}};
  const barRows = [{party: 'D', amount: 5000}, {party: 'R', amount: 7000}];
  assert.equal(corpus.serialize(draw(barRows, {...bars, ...FIELDS, plotHeight: 700})), corpus.serialize(draw(barRows, bars)));
});

test('labelCollide: a label on a steep line is left off; the lowest and highest points keep theirs (#51)', () => {
  // One line rising steeply: above each middle point the next segment runs through its label, below it the
  // previous one does. The lowest point is labelled below (its line runs above), the highest above.
  const rows = ['2021', '2022', '2023', '2024', '2025'].map((year, i) => ({year, amount: 100000 + i * 400000}));
  const spec = {type: 'line', dimensions: ['year'], measures: ['amount'], labelFields: [], tooltip: [], labels: true,
    columns: COLUMNS, categorical: true, references: {}, ...FIELDS};
  const svg = draw(rows, spec);
  const labels = byData(svg, 'data-label', 'value');
  assert.deepEqual(labels.map(textOf), ['$17K', '$1K'], 'only the extremes, the highest placed first');
  const [high, low] = labels, point = y => Number(y);
  assert.ok(point(attr(low, 'y')) > point(attr(high, 'y')), 'the lowest point\'s label sits below its point');
  const plain = all(draw(rows, {...spec, labelCollide: false}), n => n.tagName === 'text' && textOf(n).startsWith('$') && attr(n, 'text-anchor') === 'middle');
  assert.equal(plain.length, 5, 'without the field every point is labelled, as before');
});

test('labelCollide on the dual-axis cards: labels never meet, and a bar label never sits on the line (#52)', () => {
  const rows = ['2021', '2022', '2023', '2024', '2025', '2026'].map((year, i) => ({year, amount: [4000000, 18000000, 7000000, 30000000, 17000000, 8000000][i],
    contributors: [60, 140, 55, 250, 120, 40][i]}));
  const cards = [{id: 'a', measure: 'amount', type: 'bar'}, {id: 'b', measure: 'contributors', type: 'line'}];
  const spec = {type: 'cards', dimensions: ['year'], measures: ['amount', 'contributors'], cards, labelFields: [], tooltip: [], labels: true,
    columns: COLUMNS, references: {}, labelCollide: true};
  const svg = draw(rows, spec);
  const labels = byData(svg, 'data-label', 'value'), boxes = labels.map(box);
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) assert.ok(!overlap(boxes[i], boxes[j]), 'labels meet');
  const lines = all(svg, n => n.tagName === 'path' && attr(n, 'fill') === 'none');
  const segs = lines.flatMap(n => { const p = (attr(n, 'd').match(/-?\d+(?:\.\d+)?/g) || []).map(Number), out = [];
    for (let i = 2; i + 1 < p.length; i += 2) out.push([p[i - 2], p[i - 1], p[i], p[i + 1]]); return out; });
  const crosses = (b, [x1, y1, x2, y2]) => { for (let t = 0; t <= 1; t += 0.02) { const x = x1 + (x2 - x1) * t, y = y1 + (y2 - y1) * t;
    if (x > b.x && x < b.x + b.w && y > b.y && y < b.y + b.h) return true; } return false; };
  const onLine = labels.filter((label, i) => segs.some(s => crosses(boxes[i], s)));
  // The two extremes of each card may sit on the line when no side is free; no other label does.
  assert.ok(onLine.length <= 4, 'labels on the line: ' + onLine.map(textOf).join(', '));
  assert.ok(labels.length > 0, 'some labels are drawn');
});
