/* Chart Studio pie and doughnut, and the categorical palette (Paul, 2026-09-24).

   1. On screen a pie fits its chart box: charts.js chartSVG(..., {fit: {width,
      height}}) draws at the box's shape, the whole pie and its legend inside the
      drawing, no two texts overlapping, every text at least 11 CSS px.
   2. The on-screen drawing has no title, subtitle or caption; svg.__mentatExport
      draws today's picture (title, subtitle, caption, geometry) for exports.
   3. Twelve categorical colours: slots 1-4 are the brand's teal, purple, mauve
      and gold; groups past twelve take a fixed tint (45% toward white) and then
      a fixed shade (30% toward black) of slot i mod 12.
   4. Names live in the legend only; with Labels on, each slice of 4% or more
      carries its share inside the pie, in white or plum by contrast.

   Geometry is checked with theme.js's own text metric (node has no canvas);
   the browser check measures the same drawing with getBBox. Run with
   node --test on this file. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.join(__dirname, '..');
corpus.installDom();
const theme = require('../theme.js');
// The tokens a browser reads off acu-theme.css; node has no stylesheet.
const TOKENS = {palette: ['#327a7a', '#57447b', '#8b6984', '#eca91c', '#9ed4d5', '#d6c8e2', '#bcaea1'],
  ink: '#342649', muted: '#62596e', grid: '#e5e1e8', sans: 'Arial, sans-serif', serif: 'Georgia, serif'};
globalThis.MentatTheme = Object.freeze(Object.assign({}, theme, TOKENS));
globalThis.MentatCharts = require('../chart-math.js');
for (const file of ['grouped-chart.js', 'charts.js']) vm.runInThisContext(fs.readFileSync(path.join(APP, file), 'utf8'), {filename: path.join(APP, file)});
const paint = globalThis.MentatChartRender;
const contributionCharts = require('../contribution-charts.js');

const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const attr = (node, key) => node.attrs.get(key);
const num = (node, key) => Number(attr(node, key));
const own = node => node._text || '';
const texts = svg => all(svg, n => n.tagName === 'text');
const BRAND = ['#327a7a', '#57447b', '#8b6984', '#eca91c'];
const PLUM = '#342649';

// The same per-character advance theme.js falls back to without a canvas.
const width = (text, size) => theme.measureText(text, size);
function textBox(node) {
  const size = num(node, 'font-size'), x = num(node, 'x'), y = num(node, 'y');
  const w = width(own(node), size) * (attr(node, 'font-weight') === '600' ? 1.1 : 1);
  const anchor = attr(node, 'text-anchor') || 'start';
  const x0 = anchor === 'middle' ? x - w / 2 : anchor === 'end' ? x - w : x;
  return {x0, x1: x0 + w, y0: y - .93 * size, y1: y + .25 * size, node};
}
const rectBox = n => ({x0: num(n, 'x'), y0: num(n, 'y'), x1: num(n, 'x') + num(n, 'width'), y1: num(n, 'y') + num(n, 'height')});
const overlap = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
const inside = (box, W, H) => box.x0 >= 0 && box.y0 >= 0 && box.x1 <= W && box.y1 <= H;

const lin = c => { c /= 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
const lum = hex => { const [r, g, b] = [1, 3, 5].map(i => lin(parseInt(hex.slice(i, i + 2), 16))); return .2126 * r + .7152 * g + .0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + .05) / (y + .05); };
const mix = (hex, to, w) => '#' + [1, 3, 5].map(i => { const a = parseInt(hex.slice(i, i + 2), 16), b = parseInt(to.slice(i, i + 2), 16); return Math.round(a + (b - a) * w).toString(16).padStart(2, '0'); }).join('');

const groups = (n, weights) => Array.from({length: n}, (_, i) => ({label: 'Credit union member ' + (i + 1), value: weights ? weights[i] : 1000 * (n - i) + 7, n: 1}));
const PAUL = [34.2, 18.1, 9.9, 8.2, 7.1, 6.5, 5.3, 4.4, 3.1, 2.2, 1.0];   // eleven groups, shares like the 2025-2026 view
function draw(type, list, box, options = {}) {
  const result = {type, groups: list, selected: 27519, excluded: 0, warnings: []};
  return paint.chartSVG(result, 'Selected amount (USD)', {type, aggregation: 'sum', x: 0, y: 1},
    {sheet: 'Credit Union', columns: [{name: 'Company'}, {name: 'amount'}]}, 'Selected amount by Company', 'Scope line', Object.assign({fit: box}, options));
}

// Every check a fitted pie must pass, whatever the box and the groups.
function checkFitted(svg, box, list, type, labels) {
  const [, , W, H] = attr(svg, 'viewBox').split(' ').map(Number), fit = svg.__fit;
  assert.equal(attr(svg, 'data-fit'), 'pie');
  assert.equal(W, 960);
  assert.ok(H >= 360 && H <= 670, 'height within 360-670: ' + H);
  assert.ok(fit.width <= box.width + 1e-6, 'no wider than the box');
  if (box.height) assert.ok(fit.height <= box.height - 3, 'no taller than the box: ' + fit.height + ' of ' + box.height);
  const scale = fit.scale;
  // Title, subtitle and caption are the page's, not the drawing's.
  for (const node of texts(svg)) {
    assert.doesNotMatch(own(node), /Selected amount by Company|Credit Union ·|share of/, 'no title, subtitle or caption');
    assert.ok(num(node, 'font-size') * scale >= 11 - 1e-9, 'at least 11px on screen: ' + own(node));
    // Set through the style object, so the Studio stylesheet's 18px cannot resize it; never as a
    // style attribute, which the console's Content-Security-Policy (style-src 'self') refuses.
    assert.equal(node.style.fontSize, num(node, 'font-size') + 'px', 'an inline size: ' + own(node));
    assert.equal(attr(node, 'style'), undefined, 'no style attribute');
  }
  // The pie is whole and inside the drawing.
  const slices = all(svg, n => ['path', 'circle'].includes(n.tagName) && n.children.some(c => c.tagName === 'title'));
  const disc = svg.children.find(n => n.tagName === 'path') || slices[0];
  let cx, cy, r;
  if (disc.tagName === 'circle') { cx = num(disc, 'cx'); cy = num(disc, 'cy'); r = num(disc, 'r'); }
  else { const m = /^M ([\d.e-]+) ([\d.e-]+) L ([\d.e-]+) ([\d.e-]+) A ([\d.e-]+)/.exec(attr(disc, 'd')); [cx, cy] = [Number(m[1]), Number(m[2])]; r = Number(m[5]); }
  assert.ok(cx - r >= 0 && cy - r >= 0 && cx + r <= W && cy + r <= H, 'the whole pie is in the drawing');
  assert.equal(slices.length, list.filter(g => g.value > 0).length, 'one slice per positive group');
  // No text outside the drawing and no two texts overlapping; the legend clears the pie.
  const boxes = texts(svg).map(textBox);
  for (const b of boxes) assert.ok(inside(b, W, H), 'text inside the drawing: ' + own(b.node));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++)
    assert.ok(!overlap(boxes[i], boxes[j]), 'texts overlap: "' + own(boxes[i].node) + '" / "' + own(boxes[j].node) + '"');
  const legendTexts = boxes.filter(b => attr(b.node, 'data-legend-row') != null || attr(b.node, 'data-legend-more') != null);
  for (const b of legendTexts) {
    const nx = Math.min(Math.max(cx, b.x0), b.x1), ny = Math.min(Math.max(cy, b.y0), b.y1);
    assert.ok(Math.hypot(nx - cx, ny - cy) > r, 'legend clear of the pie: ' + own(b.node));
  }
  for (const rect of all(svg, n => attr(n, 'data-legend') === 'pie')) assert.ok(inside(rectBox(rect), W, H));
  // The legend lists groups in order, "name · share%", and ends "+N more" when not all fit.
  const rows = all(svg, n => attr(n, 'data-legend-row') != null);
  const more = all(svg, n => attr(n, 'data-legend-more') != null);
  rows.forEach((node, i) => { assert.equal(num(node, 'data-legend-row'), i); assert.match(own(node), / · \d+\.\d%$/); });
  assert.equal(rows.length + (more.length ? num(more[0], 'data-legend-more') : 0), list.length, 'every group is in the legend or in "+N more"');
  assert.equal(more.length, rows.length < list.length ? 1 : 0);
  if (more.length) assert.equal(own(more[0]), '+' + (list.length - rows.length) + ' more');
  // Percentages: inside the pie (outside a doughnut's hole), only for slices of 4% or more, none elsewhere.
  const layer = all(svg, n => attr(n, 'data-mark-label') === 'slices');
  const pct = all(svg, n => n.tagName === 'text' && attr(n, 'data-mark-label') === 'true');
  const total = list.reduce((s, g) => s + g.value, 0);
  if (!labels) { assert.equal(layer.length + pct.length, 0, 'Labels off: no percentages'); return {rows, more, pct, cx, cy, r}; }
  assert.equal(layer.length, 1);
  const hole = type === 'doughnut' ? all(svg, n => n.tagName === 'circle' && attr(n, 'fill') === '#fff').at(-1) : null;
  const inner = hole ? num(hole, 'r') : 0;
  assert.ok(pct.length <= list.filter(g => g.value / total >= .04).length, 'no label on a slice under 4%');
  for (const node of pct) {
    assert.match(own(node), /^\d+(\.\d)?%$/);
    const b = textBox(node);
    for (const [x, y] of [[b.x0, b.y0], [b.x1, b.y0], [b.x0, b.y1], [b.x1, b.y1]]) assert.ok(Math.hypot(x - cx, y - cy) <= r, 'label inside the pie: ' + own(node));
    if (inner) { const nx = Math.min(Math.max(cx, b.x0), b.x1), ny = Math.min(Math.max(cy, b.y0), b.y1); assert.ok(Math.hypot(nx - cx, ny - cy) >= inner, 'label outside the hole'); }
    assert.ok([PLUM, '#ffffff'].includes(attr(node, 'fill')));
  }
  return {rows, more, pct, cx, cy, r};
}

/* ------------------------------------------------------------ the palette */

test('[palette] twelve slots: 1-4 are the brand teal, purple, mauve and gold; all twelve distinct', () => {
  assert.equal(theme.categorical.length, 12);
  assert.deepEqual(theme.categorical.slice(0, 4), BRAND);
  assert.equal(new Set(theme.categorical).size, 12);
  for (const hex of theme.categorical) assert.match(hex, /^#[0-9a-f]{6}$/);
  assert.ok(Object.isFrozen(theme.categorical));
  for (let i = 0; i < 12; i++) assert.equal(theme.categoryColour(i), theme.categorical[i]);
  assert.equal(theme.palette.length, 7, 'MentatTheme.palette itself is unchanged (Graph reads its last slot)');
});

test('[palette] groups 13-24 take slot i mod 12 mixed 45% toward white; 25 on, 30% toward black', () => {
  for (let i = 12; i < 24; i++) assert.equal(theme.categoryColour(i), mix(theme.categorical[i - 12], '#ffffff', .45), 'group ' + (i + 1));
  for (let i = 24; i < 60; i++) assert.equal(theme.categoryColour(i), mix(theme.categorical[i % 12], '#000000', .30), 'group ' + (i + 1));
  assert.equal(theme.categoryColour(12), '#8eb6b6', 'teal, 45% toward white');
  assert.equal(theme.categoryColour(24), '#235555', 'teal, 30% toward black');
  assert.equal(new Set(Array.from({length: 36}, (_, i) => theme.categoryColour(i))).size, 36, 'the first 36 groups never share a colour');
  for (const odd of [-1, NaN, undefined, null, 'x']) assert.equal(theme.categoryColour(odd), BRAND[0]);
  assert.equal(theme.categoryColour(13.7), theme.categoryColour(13), 'a fractional index is its floor');
});

test('[palette] a pie of four groups or fewer is coloured exactly as before; eleven groups take eleven colours', () => {
  const box = {width: 623, height: 300};
  for (const n of [1, 2, 3, 4]) {
    const svg = draw('pie', groups(n), box).svg;
    const fills = all(svg, n2 => ['path', 'circle'].includes(n2.tagName) && n2.children.some(c => c.tagName === 'title')).map(s => attr(s, 'fill'));
    assert.deepEqual(fills, BRAND.slice(0, n));
    // and the export, which no fit touches, too
    const exported = svg.__mentatExport();
    assert.deepEqual(all(exported, n2 => ['path', 'circle'].includes(n2.tagName) && n2.children.some(c => c.tagName === 'title')).map(s => attr(s, 'fill')), BRAND.slice(0, n));
  }
  const svg = draw('pie', PAUL.map((v, i) => ({label: 'Company ' + (i + 1) + ' / Contributions', value: v, n: 1})), box).svg;
  const fills = all(svg, n => n.tagName === 'path').map(s => attr(s, 'fill'));
  assert.deepEqual(fills, theme.categorical.slice(0, 11));
  const swatches = all(svg, n => attr(n, 'data-legend') === 'pie').map(r => attr(r, 'fill'));
  assert.deepEqual(swatches, fills, 'each legend swatch is its slice\'s colour');
  // Colour follows a Colour field's member (seriesIndex) as before.
  const coloured = draw('pie', [{label: 'a', value: 5, n: 1, seriesIndex: 13}, {label: 'b', value: 5, n: 1, seriesIndex: 0}], box).svg;
  assert.deepEqual(all(coloured, n => n.tagName === 'path').map(s => attr(s, 'fill')), [theme.categoryColour(13), BRAND[0]]);
});

test('[palette] group i is one colour everywhere: pie, grouped chart and contribution plot, i = 0..14', () => {
  const names = Array.from({length: 15}, (_, i) => 'M' + String(i).padStart(2, '0'));
  const expected = names.map((_, i) => theme.categoryColour(i));
  assert.equal(new Set(expected).size, 15);
  const fills = nodes => nodes.map(n => attr(n, 'fill'));
  // charts.js: slice i, on screen and in the export.
  const pie = draw('pie', names.map(label => ({label, value: 10, n: 1})), {width: 900, height: 520}).svg;
  assert.deepEqual(fills(all(pie, n => n.tagName === 'path')), expected, 'pie slices');
  assert.deepEqual(fills(all(pie, n => attr(n, 'data-legend') === 'pie')), expected, 'pie legend');
  assert.deepEqual(fills(all(pie.__mentatExport(), n => n.tagName === 'path')), expected, 'pie export');
  // grouped-chart.js: series i as bars, as the legend, and as lines (dashed only past the twelve plain slots).
  const values = k => names.map((series, i) => ({series, value: i + k}));
  const grouped = globalThis.MentatGroupedSVG({type: 'grouped', series: names, groups: [{label: 'All', values: values(1)}]}, 'T', '');
  assert.deepEqual(fills(all(grouped, n => n.tagName === 'rect' && n.children.some(c => c.tagName === 'title'))), expected, 'grouped bars');
  assert.deepEqual(fills(all(grouped, n => n.tagName === 'rect' && attr(n, 'x') === '20' && attr(n, 'width') === '12')), expected, 'grouped legend');
  const lines = globalThis.MentatGroupedSVG({type: 'multiline', series: names, groups: [{label: '2020', values: values(1)}, {label: '2022', values: values(2)}]}, 'T', '');
  const segments = all(lines, n => n.tagName === 'line' && attr(n, 'stroke-width') === '2');
  assert.deepEqual(segments.map(n => attr(n, 'stroke')), expected, 'multiline strokes');
  assert.deepEqual(segments.map(n => attr(n, 'stroke-dasharray')), names.map((_, i) => i >= 12 ? '5 3' : 'none'), 'tinted series are dashed');
  // contribution-plot.js: Colour member i as bars and legend, and line series i.
  const plot = require('../contribution-plot.js');
  const cols = [{id: 'company', name: 'Company'}, {id: 'member', name: 'Member'}, {id: 'year', name: 'Year'}, {id: 'amount', name: 'Amount', unit: 'cents'}];
  const bars = document.createElement('div');
  plot.render(bars, names.map((member, i) => ({company: 'C' + i, member, amount: 100 + i})), {type: 'bar', dimensions: ['company', 'member'], measures: ['amount'], color: 'member', columns: cols}, () => {});
  assert.deepEqual(fills(all(bars, n => attr(n, 'class') === 'plot-mark')), expected, 'contribution bars');
  const legendOf = host => fills(all(all(host, n => attr(n, 'data-legend') === 'series')[0], n => n.tagName === 'rect'));
  assert.deepEqual(legendOf(bars), expected, 'contribution legend');
  const timeline = document.createElement('div');
  plot.render(timeline, names.flatMap((member, i) => [{year: '2020', member, amount: 100 + i}, {year: '2021', member, amount: 200 + i}]), {type: 'line', dimensions: ['year', 'member'], measures: ['amount'], columns: cols}, () => {});
  assert.deepEqual(all(timeline, n => n.tagName === 'path' && attr(n, 'fill') === 'none' && attr(n, 'stroke-width') === '2.5').map(n => attr(n, 'stroke')), expected, 'contribution line series');
  assert.deepEqual(legendOf(timeline), expected, 'contribution line legend');
  // A colorMap still wins over the palette.
  const mapped = document.createElement('div');
  plot.render(mapped, [{company: 'A', member: 'M00', amount: 1}, {company: 'B', member: 'M01', amount: 2}], {type: 'bar', dimensions: ['company', 'member'], measures: ['amount'], color: 'member', colorMap: {M01: '#abcdef'}, columns: cols}, () => {});
  assert.ok(fills(all(mapped, n => attr(n, 'class') === 'plot-mark')).includes('#abcdef'));
});

test('[palette] contribution-charts: members, series and stacks take the twelve colours, legend and marks alike', () => {
  const rows = Array.from({length: 14}, (_, i) => ({company: 'C' + i, party: 'P' + String(i).padStart(2, '0'), amount: 100 + i}));
  const cols = [{id: 'company', name: 'Company', role: 'dimension'}, {id: 'party', name: 'Party', role: 'dimension'}, {id: 'amount', name: 'Amount', unit: 'cents'}];
  const svg = contributionCharts.render(document.createElement('div'), rows, {type: 'stacked', dimensions: ['company', 'party'], measures: ['amount'], color: 'party', columns: cols}, () => {});
  const members = [...new Set(rows.map(r => r.party))].sort();
  const legend = all(svg, n => n.tagName === 'rect' && n.attrs.has('rx')).map(r => attr(r, 'fill'));
  assert.deepEqual(legend, members.map((_, i) => theme.categoryColour(i)));
  const marks = all(svg, n => attr(n, 'class') === 'plot-mark').map(m => attr(m, 'fill'));
  assert.deepEqual([...new Set(marks)].sort(), [...new Set(legend)].sort(), 'every mark colour is a legend colour');
  // A colour map (compared scopes) still wins over the palette.
  const mapped = contributionCharts.render(document.createElement('div'), rows.slice(0, 2), {type: 'stacked', dimensions: ['company', 'party'], measures: ['amount'], color: 'party', columns: cols, colorMap: {P00: '#abcdef'}}, () => {});
  assert.ok(all(mapped, n => attr(n, 'class') === 'plot-mark').some(m => attr(m, 'fill') === '#abcdef'));
});

/* ----------------------------------------------------- the fitted pie */

const BOXES = [
  {width: 623, height: 300},    // Paul's 1440x900 Standard box
  {width: 1123, height: 353},   // Data pane and Inspector hidden
  {width: 800, height: 520},
  {width: 1400, height: 300},   // flatter than the drawing can be: centred, narrower
  {width: 420, height: 640},    // tall and narrow
  {width: 300, height: 180},    // the canvas's floor
  {width: 700, height: null}    // a board tile: the box grows with the chart
];
const LISTS = {one: groups(1), two: groups(2, [70, 30]), three: groups(3, [50, 30, 20]), paul: PAUL.map((v, i) => ({label: 'Company ' + (i + 1) + ' / Contributions', value: v, n: 1})),
  sixteen: groups(16), many: groups(40), tiny: groups(30, Array.from({length: 30}, (_, i) => i ? 1 : 71)), long: groups(6).map(g => ({...g, label: g.label + ' Federal Credit Union of the Greater Metropolitan Area'}))};

test('[fit] every box, group count and mark: the whole pie and legend inside the drawing, no overlapping text, >= 11px', () => {
  for (const box of BOXES) for (const [name, list] of Object.entries(LISTS)) for (const type of ['pie', 'doughnut']) for (const labels of [false, true]) {
    const where = JSON.stringify(box) + ' ' + name + ' ' + type + (labels ? ' labels' : '');
    try { checkFitted(draw(type, list, box, {labels}).svg, box, list, type, labels); }
    catch (error) { error.message = where + ': ' + error.message; throw error; }
  }
});

test('[fit] Paul\'s case: eleven groups in the 623x300 box - every group listed, eleven colours, labels on the big slices', () => {
  const box = {width: 623, height: 300}, list = LISTS.paul;
  const svg = draw('pie', list, box, {labels: true}).svg;
  const {rows, more, pct} = checkFitted(svg, box, list, 'pie', true);
  assert.equal(rows.length, 11, 'no "+N more": all eleven fit');
  assert.equal(more.length, 0);
  assert.equal(pct.length, list.filter(g => g.value / 100 >= .04).length, 'every slice of 4% or more carries its share');
  const [, , , H] = attr(svg, 'viewBox').split(' ').map(Number);
  assert.ok(Math.abs(960 * (300 - 4) / 623 - H) < .01, 'the drawing has the box\'s shape');
});

test('[fit] the legend tightens, then takes a second column, then ends "+N more"', () => {
  const box = {width: 900, height: 300};
  const xs = svg => [...new Set(all(svg, n => attr(n, 'data-legend') === 'pie').map(r => Math.round(num(r, 'x'))))];
  assert.equal(xs(draw('pie', groups(8), box).svg).length, 1, 'eight rows: one column');
  const two = draw('pie', groups(16), box).svg;
  assert.equal(xs(two).length, 2, 'sixteen rows: two columns');
  assert.equal(all(two, n => attr(n, 'data-legend-more') != null).length, 0);
  const capped = draw('pie', groups(40), box).svg;
  assert.equal(all(capped, n => attr(n, 'data-legend-row') != null).length + num(all(capped, n => attr(n, 'data-legend-more') != null)[0], 'data-legend-more'), 40);
  // Parameters' "every legend member" lists as many as fit, then "+N more".
  const every = draw('pie', groups(40), {width: 1400, height: 700}, {legend: 'all'}).svg;
  const shown = all(every, n => attr(n, 'data-legend-row') != null).length;
  assert.ok(shown > 16, 'more than the default sixteen when asked: ' + shown);
  // A narrow box has no room for two columns: one column, then "+N more".
  const narrow = draw('pie', groups(16), {width: 420, height: 300}).svg;
  assert.equal(xs(narrow).length, 1);
  assert.equal(all(narrow, n => attr(n, 'data-legend-more') != null).length, 1);
});

test('[fit] a long name is cut in the legend and keeps its share; the full name is its tooltip', () => {
  const svg = draw('pie', LISTS.long, {width: 623, height: 300}).svg;
  const row = all(svg, n => attr(n, 'data-legend-row') === '0')[0];
  assert.match(own(row), /… · \d+\.\d%$/);
  assert.equal(row.children.find(c => c.tagName === 'title').textContent, LISTS.long[0].label + ' · ' + (100 * LISTS.long[0].value / LISTS.long.reduce((s, g) => s + g.value, 0)).toFixed(1) + '%');
});

test('[fit] a label colour is white or plum, whichever reads better on its slice', () => {
  for (const type of ['pie', 'doughnut']) {
    const svg = draw(type, groups(12, Array(12).fill(1)), {width: 900, height: 520}, {labels: true}).svg;
    const labels = all(svg, n => n.tagName === 'text' && attr(n, 'data-mark-label') === 'true');
    assert.equal(labels.length, 12, 'twelve slices of 8.3%: each labelled');
    const slices = all(svg, n => n.tagName === 'path');
    for (const node of labels) {
      // The slice whose angle holds the label.
      const {cx, cy} = {cx: Number(/^M ([\d.]+)/.exec(attr(slices[0], 'd'))[1]), cy: Number(/^M [\d.]+ ([\d.]+)/.exec(attr(slices[0], 'd'))[1])};
      let a = Math.atan2(num(node, 'y') - cy, num(node, 'x') - cx) + Math.PI / 2;
      a = (a + 2 * Math.PI) % (2 * Math.PI);
      const fill = attr(slices[Math.floor(a / (2 * Math.PI / 12))], 'fill');
      const best = ratio(fill, '#ffffff') >= ratio(fill, PLUM) ? '#ffffff' : PLUM;
      assert.equal(attr(node, 'fill'), best, fill);
      assert.ok(ratio(fill, attr(node, 'fill')) >= 4.5, 'at least 4.5:1 on ' + fill);
    }
  }
});

test('[fit] a slice over half the pie carries its share out of the hub, where the other slices meet', () => {
  for (const [type, box] of [['pie', {width: 623, height: 300}], ['pie', {width: 700, height: null}]]) {
    const list = groups(3, [90, 6, 4]), svg = draw(type, list, box, {labels: true}).svg;
    const {pct, cx, cy, r} = checkFitted(svg, box, list, type, true);
    const big = pct.find(n => /^90(\.0)?%$/.test(own(n)));
    assert.ok(big, 'the 90% slice is labelled');
    assert.ok(Math.hypot(num(big, 'x') - cx, num(big, 'y') - cy) >= .35 * r, 'at 40% of the radius, not on the hub');
  }
});

test('[fit] Labels off draws no percentages; a pie with no slice of 4% still answers the Labels switch', () => {
  assert.equal(all(draw('pie', LISTS.paul, {width: 623, height: 300}).svg, n => attr(n, 'data-mark-label') != null).length, 0);
  const flat = draw('pie', groups(40, Array(40).fill(1)), {width: 623, height: 300}, {labels: true}).svg;
  assert.equal(all(flat, n => attr(n, 'data-mark-label') === 'slices').length, 1);
  assert.equal(all(flat, n => attr(n, 'data-mark-label') === 'true').length, 0, '2.5% slices carry no label');
});

test('[fit] a Colour measure\'s scale heads the legend column', () => {
  const list = PAUL.slice(0, 5).map((v, i) => ({label: 'M' + i, value: v, n: 1, colorValue: i}));
  const result = {type: 'pie', groups: list, selected: 5, excluded: 0, colorRange: [0, 4], colorLabel: 'SUM(Records)'};
  for (const box of [{width: 623, height: 300}, {width: 300, height: 180}]) {
    const svg = paint.chartSVG(result, 'Amount', {type: 'pie', aggregation: 'sum', x: 0, y: 1}, {sheet: 'S', columns: [{name: 'G'}, {name: 'A'}]}, 'T', 'S', {fit: box}).svg;
    checkFitted(svg, box, list, 'pie', false);
    const swatches = all(svg, n => attr(n, 'data-legend') === 'colour');
    assert.deepEqual(swatches.map(n => attr(n, 'fill-opacity')), ['0.25', '1'], 'the scale\'s lightest and darkest shade');
    const title = all(svg, n => attr(n, 'data-legend') === 'colour-title')[0];
    const first = Math.min(...all(svg, n => attr(n, 'data-legend-row') != null).map(n => num(n, 'y')));
    assert.ok(num(title, 'y') < first && Math.max(...swatches.map(n => num(n, 'y'))) < first, 'above the legend rows');
  }
});

/* ------------------------------------------------------- exports */

test('[export] the export draws today\'s picture: title, subtitle, caption and 960x670 geometry', () => {
  for (const type of ['pie', 'doughnut']) for (const labels of [false, true]) {
    const options = {labels};
    const svg = draw(type, LISTS.paul, {width: 623, height: 300}, options).svg;
    const exported = svg.__mentatExport();
    assert.equal(attr(exported, 'viewBox'), '0 0 960 670');
    assert.equal(attr(exported, 'data-fit'), undefined);
    const words = texts(exported).map(n => n.textContent).join('\n');
    assert.match(words, /Selected amount by Company/, 'title');
    assert.match(words, /Credit Union · 27,519 source rows/, 'subtitle');
    assert.match(words, /Company · share of Selected amount \(USD\)/, 'caption');
    // Byte for byte what the renderer draws without a fit.
    const plain = paint.chartSVG({type, groups: LISTS.paul, selected: 27519, excluded: 0, warnings: []}, 'Selected amount (USD)', {type, aggregation: 'sum', x: 0, y: 1},
      {sheet: 'Credit Union', columns: [{name: 'Company'}, {name: 'amount'}]}, 'Selected amount by Company', 'Scope line', options).svg;
    assert.equal(corpus.serialize(exported), corpus.serialize(plain));
    assert.equal(exported.__mentatExport, undefined, 'an export is final');
  }
});

test('[export] every other mark ignores the fit option', () => {
  const result = {type: 'bar', groups: groups(5), selected: 5, excluded: 0};
  const args = [result, 'Amount', {type: 'bar', aggregation: 'sum', x: 0, y: 1}, {sheet: 'S', columns: [{name: 'G'}, {name: 'A'}]}, 'T', 'S'];
  const fitted = paint.chartSVG(...args, {fit: {width: 623, height: 300}}).svg, plain = paint.chartSVG(...args, {}).svg;
  assert.equal(corpus.serialize(fitted), corpus.serialize(plain));
  assert.equal(fitted.__mentatExport, undefined);
});
