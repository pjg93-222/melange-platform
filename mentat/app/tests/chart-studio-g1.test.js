/* Lane G1 (boards #47, #50, #52, 2026-09-27): the chart card's title in shorter words, and the sources a
   sheet reads, listed at the top of the Data pane when there are two or more.

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../chart-studio-core.js');
const layers = require('../map-layers.js');
const contributions = require('../chart-studio-contributions.js');

/* ------------------------------------------------------------ the title */

test('conciseTitle: a measure takes its shorter name - a sum is its field, "Distinct reported" is dropped (#50, #52)', () => {
  assert.equal(core.conciseTitle({yMeasures: ['Selected amount', 'Distinct reported donor identities'], axisDims: ['Contribution year']}),
    'Selected amount and Donor identities by Contribution year');
  assert.equal(core.conciseTitle({xMeasures: ['Distinct reported donor identities'], yMeasures: ['Selected amount'],
    detailDims: ['Recipient committee']}), 'Selected amount vs Donor identities, by Recipient committee');
  assert.equal(core.conciseTitle({yMeasures: ['SUM(Analysis Amount)'], axisDims: ['Company']}), 'Analysis Amount by Company');
  assert.equal(core.conciseTitle({yMeasures: ['AVG(Analysis Amount)'], axisDims: ['Company']}), 'AVG(Analysis Amount) by Company',
    'another statistic keeps its function');
  assert.equal(core.conciseTitle({yMeasures: ['SUM(Amount)', 'Amount']}), 'Amount', 'one measure is said once');
  assert.equal(core.conciseTitle({xMeasures: ['Observation count'], yMeasures: ['Selected amount'], colorDim: 'Recipient party',
    detailDims: ['Recipient committee']}), 'Selected amount vs Observation count, by Recipient committee', 'a name with no shorter form is kept');
});

/* ------------------------------------------------------ the Data sources list */

const INDUSTRY = (industry, basis = 'receipt') => ({kind: 'industry', industry, basis, label: industry});
const FAIRSHAKE = {kind: 'pac', source: 'snapshot', id: 'snapshot-1', committee: 'C00835959', cycle: '2024', direction: 'spent', name: 'FAIRSHAKE'};
const DENSITY = {radius: layers.DEFAULT_RADIUS.density, intensity: layers.DEFAULT_INTENSITY, ramp: 'heat'};
const mapSheet = cards => ({marks: {type: 'map'}, marksCards: cards.map((card, at) => core.serializeCard(layers.newLayerCard(
  Object.assign({id: 'layer-' + (at + 1)}, card))))});
// Board #47: Community Banks ZIPs, Crypto density, FAIRSHAKE's spending by district.
const BOARD = mapSheet([{type: 'filled-map', geography: 'zip', source: INDUSTRY('Community Banks')},
  {type: 'density', geography: 'point', source: INDUSTRY('Crypto'), map: DENSITY},
  {type: 'filled-map', geography: 'district', source: FAIRSHAKE}]);

test('sheetSources: a map of three sources lists each once, in card order, with its name, kind and swatch (#47)', () => {
  assert.equal(core.validateMarksCards(BOARD), '', 'the board’s sheet is a valid map sheet');
  const rows = core.sheetSources(BOARD, layers, contributions.BASIS);
  assert.deepEqual(rows.map(row => [row.name, row.kind]), [['Community Banks', 'published workbook'], ['Crypto', 'published workbook'],
    ['PAC evidence', 'FAIRSHAKE · Spent 2023–2024']]);
  const teal = layers.rampStops('teal'), heat = layers.rampStops('heat');
  assert.deepEqual(rows.map(row => row.colour), [teal[3], heat[1], teal[4]],
    'teal, the density’s purple, and a darker teal for the second teal layer (the board’s three swatches)');
  assert.equal(new Set(rows.map(row => row.colour)).size, rows.length, 'no two sources share a swatch');
  assert.equal(rows[0].title, 'Community Banks · published workbook · Published included receipts', 'the tooltip names the basis');
  assert.equal(rows[2].title, 'PAC evidence · FAIRSHAKE · Spent 2023–2024');
});

test('sheetSources: one source, a side of one source, or a sheet that is not a map lists nothing to show', () => {
  // Two layers of one source are one source.
  const one = mapSheet([{type: 'filled-map', geography: 'state', source: INDUSTRY('Crypto')},
    {type: 'density', geography: 'point', source: INDUSTRY('Crypto'), map: DENSITY}]);
  assert.equal(core.sheetSources(one, layers, contributions.BASIS).length, 1);
  // Support and opposition of one committee's Spent are one source (map-layers.js sourceKey).
  const sides = mapSheet([{type: 'filled-map', geography: 'district', source: Object.assign({stance: 'support'}, FAIRSHAKE)},
    {type: 'filled-map', geography: 'district', source: Object.assign({stance: 'oppose'}, FAIRSHAKE)}]);
  assert.equal(core.sheetSources(sides, layers, contributions.BASIS).length, 1);
  // Another basis of an industry is another source.
  const bases = mapSheet([{type: 'filled-map', geography: 'state', source: INDUSTRY('Crypto')},
    {type: 'filled-map', geography: 'state', source: INDUSTRY('Crypto', 'original_observed')}]);
  assert.equal(core.sheetSources(bases, layers, contributions.BASIS).length, 2);
  // Not a map: no sources of its own.
  assert.deepEqual(core.sheetSources({marks: {type: 'bar'}, columns: [], rows: []}, layers, contributions.BASIS), []);
  assert.deepEqual(core.sheetSources(BOARD, null, contributions.BASIS), [], 'without the layers module there is no list');
});

test('sheetSources (G1b): a circle with no colour measure shows its slot’s one colour; with a colour measure, its ramp (#47)', () => {
  const FIELDS = [{index: 0, id: 'amount', name: 'Selected amount', role: 'measure'}];
  const colourPill = {color: {column: 0, aggregation: 'sum'}};
  const circles = mapSheet([{type: 'circle', geography: 'zip', source: INDUSTRY('Community Banks')},
    {type: 'circle', geography: 'zip', source: INDUSTRY('Crypto')}]);
  assert.equal(core.validateMarksCards(circles), '');
  // map-studio.js compileSheet deals each layer its slot (0, 1, ...): a flat circle draws in slotColour(slot).
  assert.deepEqual(core.sheetSources(circles, layers, contributions.BASIS, FIELDS).map(row => row.colour),
    [layers.slotColour(0), layers.slotColour(1)]);
  const coloured = mapSheet([{type: 'circle', geography: 'zip', source: INDUSTRY('Community Banks'), encodings: colourPill},
    {type: 'circle', geography: 'zip', source: INDUSTRY('Crypto'), encodings: colourPill}]);
  assert.equal(core.validateMarksCards(coloured), '');
  const teal = layers.rampStops('teal');
  assert.deepEqual(core.sheetSources(coloured, layers, contributions.BASIS, FIELDS).map(row => row.colour), [teal[3], teal[4]],
    'a colour measure draws the teal ramp: its hue, then a darker stop for the second source');
});

/* ------------------------------------------------------ the value labels (#51) */

const path = require('node:path');
const corpus = require('./chart-engine-r3.corpus.js');
const {plot} = corpus.loadEngine(path.resolve(__dirname, '..'));
const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const attr = (node, key) => node.attrs && node.attrs.get(key);
const PLOT_COLUMNS = [{id: 'party', name: 'Recipient party', role: 'dimension'}, {id: 'year', name: 'Contribution year', role: 'dimension'},
  {id: 'amount', name: 'Selected amount', role: 'measure', unit: 'cents'}, {id: 'records', name: 'Observation count', role: 'measure', unit: 'count'}];
// Board #51's shape (round-1 capture, Community Banks by party): one tall line and two low ones that cross.
const VALUES = {'Other / Unclassified': [2495247, 8759723, 4918299, 22949615, 8070000, 3630000],
  R: [1380000, 4018500, 792000, 3595296, 3581031, 750000], D: [437200, 3070000, 1600000, 3500000, 3300000, 2060000]};
const LINE_ROWS = ['2021', '2022', '2023', '2024', '2025', '2026'].flatMap((year, i) =>
  Object.entries(VALUES).map(([party, values]) => ({year, party, amount: values[i], records: 10 + i})));
const LINE_SPEC = {type: 'line', dimensions: ['year', 'party'], measures: ['amount'], color: 'party', labelFields: ['amount'], tooltip: ['records'],
  labels: true, columns: PLOT_COLUMNS, categorical: true, references: {}, path: 'year',
  legendPlacement: 'right', labelCollide: true, endLabels: true};
// A label's glyphs as the renderer measures them without a canvas (length x 18px; ascent .78, descent .2).
function glyphs(node) {
  const text = node._text ?? '', w = text.length * 18, x = Number(attr(node, 'x')), y = Number(attr(node, 'y'));
  const anchor = attr(node, 'text-anchor') || 'start';
  return {text, x: anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x, y: y - 18 * .78, w, h: 18 * .98};
}
const pointRect = (px, py, r) => Math.hypot(Math.max(r.x - px, 0, px - r.x - r.w), Math.max(r.y - py, 0, py - r.y - r.h));
const pointSeg = (px, py, [a, b, c, d]) => {
  const len = (c - a) ** 2 + (d - b) ** 2, t = len ? Math.max(0, Math.min(1, ((px - a) * (c - a) + (py - b) * (d - b)) / len)) : 0;
  return Math.hypot(px - (a + t * (c - a)), py - (b + t * (d - b)));
};
const crossing = ([a, b, c, d], r) => {
  for (let t = 0; t <= 1; t += 0.001) { const x = a + (c - a) * t, y = b + (d - b) * t; if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) return true; }
  return false;
};
// The clear space between a line's ink and a label's glyphs.
const inkGap = (s, r) => (crossing(s, r) ? 0 : Math.min(pointRect(s[0], s[1], r), pointRect(s[2], s[3], r),
  ...[[r.x, r.y], [r.x + r.w, r.y], [r.x, r.y + r.h], [r.x + r.w, r.y + r.h]].map(([x, y]) => pointSeg(x, y, s)))) - s[4];
const boxGap = (p, q) => Math.hypot(Math.max(0, q.x - (p.x + p.w), p.x - (q.x + q.w)), Math.max(0, q.y - (p.y + p.h), p.y - (q.y + q.h)));

test('labelCollide (#51): each value label keeps 4 units (2px on the board) from the other labels and from every line', () => {
  for (const plotHeight of [500, 640, 760]) {
    const container = document.createElement('div');
    plot.render(container, LINE_ROWS, {...LINE_SPEC, plotHeight}, () => {});
    const svg = container.children.find(node => node.tagName === 'svg');
    const labels = all(svg, n => n.tagName === 'text' && attr(n, 'data-label') === 'value').map(glyphs);
    const segs = all(svg, n => n.tagName === 'path' && attr(n, 'fill') === 'none' && attr(n, 'stroke') && attr(n, 'stroke') !== 'none')
      .flatMap(n => { const p = (attr(n, 'd').match(/-?\d+(?:\.\d+)?/g) || []).map(Number), half = (Number(attr(n, 'stroke-width')) || 1) / 2, out = [];
        for (let i = 2; i + 1 < p.length; i += 2) out.push([p[i - 2], p[i - 1], p[i], p[i + 1], half]); return out; });
    assert.ok(segs.length >= 15, 'three lines are drawn');
    assert.ok(labels.some(label => label.text.startsWith('$229,4')), `${plotHeight}: the highest point keeps its label (fitted to its room)`);
    for (const label of labels) {
      const gap = Math.min(...segs.map(s => inkGap(s, label)));
      assert.ok(gap >= 4 - 0.05, `${plotHeight}: "${label.text}" is ${gap.toFixed(2)} units from a line`);
    }
    for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++)
      assert.ok(boxGap(labels[i], labels[j]) >= 4 - 0.05, `${plotHeight}: "${labels[i].text}" and "${labels[j].text}" are closer than 4 units`);
  }
});

// Lane G1b: every other piece of ink a value label must keep clear of, read off the drawing as the renderer reads it.
function inkOf(svg) {
  const texts = [], leaders = [], circles = [];
  const visit = (node, legend) => {
    const inLegend = legend || attr(node, 'data-legend') != null || attr(node, 'data-reference') != null;
    if (node.tagName === 'text' && attr(node, 'data-label') !== 'value' && !attr(node, 'transform') && !inLegend && (node._text || '').trim())
      texts.push(glyphs(node));
    if (node.tagName === 'line' && attr(node, 'data-legend') == null && legend === 'line-ends')
      leaders.push(['x1', 'y1', 'x2', 'y2'].map(key => Number(attr(node, key))).concat(0.6));
    if (node.tagName === 'circle' && String(attr(node, 'class') || '').includes('plot-mark'))
      circles.push({x: Number(attr(node, 'cx')) - Number(attr(node, 'r')), y: Number(attr(node, 'cy')) - Number(attr(node, 'r')),
        w: 2 * Number(attr(node, 'r')), h: 2 * Number(attr(node, 'r')), label: String(attr(node, 'aria-label') || '')});
    for (const child of node.children || []) visit(child, attr(node, 'data-legend') || legend);
  };
  visit(svg, null);
  return {texts, leaders, circles};
}
const fullText = node => ((node.children || []).find(child => child.tagName === 'title') || {})._text || node._text || '';

test('labelCollide (G1b, #51): the same gap from the axis text, the other points and each line name’s leader; spec.labelGap widens it to 6', () => {
  // labelGap: Chart Studio's gap for 2px at the drawn size; 9 is held to 6 units (a label above its point sits 7.4 over it).
  for (const [labelGap, want] of [[undefined, 4], [5, 5], [9, 6]]) {
    const container = document.createElement('div');
    plot.render(container, LINE_ROWS, {...LINE_SPEC, plotHeight: 640, labelGap}, () => {});
    const svg = container.children.find(node => node.tagName === 'svg');
    const nodes = all(svg, n => n.tagName === 'text' && attr(n, 'data-label') === 'value');
    const labels = nodes.map(node => Object.assign(glyphs(node), {full: fullText(node)}));
    const {texts, leaders, circles} = inkOf(svg);
    assert.ok(labels.length >= (want > 4 ? 1 : 3) && texts.length >= 8 && leaders.length === 3 && circles.length === 18,
      `${want}: ${labels.length} labels, ${texts.length} texts, ${leaders.length} leaders, ${circles.length} points`);
    assert.ok(labels.some(label => label.full === '$229,496.15'), `${want}: the highest point keeps its label`);
    const tol = want - 0.05;
    for (const label of labels) {
      for (const text of texts) assert.ok(boxGap(label, text) >= tol, `${want}: "${label.text}" is ${boxGap(label, text).toFixed(2)} from "${text.text}"`);
      for (const leader of leaders) assert.ok(inkGap(leader, label) >= tol, `${want}: "${label.text}" is ${inkGap(leader, label).toFixed(2)} from a leader`);
      // Its own point is the one whose tooltip carries its value (with any point drawn on top of it, as the
      // renderer's own-mark rule counts one: its box holds the own point's centre); every other point keeps the gap.
      const own = circles.find(item => item.label.includes(': ' + label.full));
      assert.ok(own, `${want}: "${label.full}" has its point`);
      const at = {x: own.x + own.w / 2, y: own.y + own.h / 2};
      const coincident = item => at.x >= item.x - 1 && at.x <= item.x + item.w + 1 && at.y >= item.y - 1 && at.y <= item.y + item.h + 1;
      for (const circle of circles.filter(item => !coincident(item)))
        assert.ok(boxGap(label, circle) >= tol, `${want}: "${label.text}" is ${boxGap(label, circle).toFixed(2)} from a point`);
    }
    if (want > 4) {
      for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++)
        assert.ok(boxGap(labels[i], labels[j]) >= tol, `${want}: "${labels[i].text}" and "${labels[j].text}"`);
      const segs = all(svg, n => n.tagName === 'path' && attr(n, 'fill') === 'none' && attr(n, 'stroke') && attr(n, 'stroke') !== 'none')
        .flatMap(n => { const p = (attr(n, 'd').match(/-?\d+(?:\.\d+)?/g) || []).map(Number), half = (Number(attr(n, 'stroke-width')) || 1) / 2, out = [];
          for (let i = 2; i + 1 < p.length; i += 2) out.push([p[i - 2], p[i - 1], p[i], p[i + 1], half]); return out; });
      for (const label of labels) assert.ok(Math.min(...segs.map(s => inkGap(s, label))) >= tol, `${want}: "${label.text}" and a line`);
    }
  }
});
