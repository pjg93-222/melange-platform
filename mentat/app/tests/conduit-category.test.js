/* Conduit (ActBlue, WinRed) in the charts: an earmark platform, never a super PAC.

   The server now says 'Conduit' where the stored facts said Super PAC. Here the
   console draws it: Conduit has a colour of its own and takes no colour slot, so
   every other category is the colour it is without it; and a Conduit mark, row or
   legend item carries the sentence that says what it is.

   Run with the glob form from mentat/:  node --test "app/tests/**"/"*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.join(__dirname, '..');
corpus.installDom();
const theme = require('../theme.js');
const TOKENS = {palette: ['#327a7a', '#57447b', '#8b6984', '#eca91c', '#9ed4d5', '#d6c8e2', '#bcaea1'],
  ink: '#342649', muted: '#62596e', grid: '#e5e1e8', sans: 'Arial, sans-serif', serif: 'Georgia, serif'};
globalThis.MentatTheme = Object.freeze(Object.assign({}, theme, TOKENS));
const plot = require('../contribution-plot.js');
const contributionCharts = require('../contribution-charts.js');

const NOTE = 'Conduit: an earmark platform (ActBlue, WinRed). These are individual donors\' earmarked contributions passed through it, not super PAC money.';
const all = (node, pick) => [node, ...(node.children || []).flatMap(child => all(child, pick))].filter(pick);
const attr = (node, key) => node.attrs.get(key);
const own = node => node._text || '';
const titles = node => all(node, n => n.tagName === 'title').map(own);

// OKLab distance x100, the measure theme.js's palette notes use.
const lin = c => { c /= 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
function oklab(hex) {
  const [r, g, b] = [1, 3, 5].map(i => lin(parseInt(hex.slice(i, i + 2), 16)));
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b);
  const m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b);
  const s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .793617785 * m - .0040720468 * s, 1.9779984951 * l - 2.428592205 * m + .4505937099 * s, .0259040371 * l + .7827717662 * m - .808675766 * s];
}
const distance = (a, b) => 100 * Math.hypot(...oklab(a).map((v, i) => v - oklab(b)[i]));

test('[conduit] theme: the note, and a colour of its own that takes no slot', () => {
  assert.equal(theme.categoryNote('Conduit'), NOTE);
  for (const other of ['Super PAC', 'Carey', 'conduit', 'Conduits', '', undefined, null]) assert.equal(theme.categoryNote(other), '', String(other));
  const without = theme.categoryColours(['Candidate', 'Carey', 'Super PAC']);
  assert.deepEqual(without, [0, 1, 2].map(i => theme.categoryColour(i)), 'with no Conduit it is categoryColour(i), as before');
  const withConduit = theme.categoryColours(['Candidate', 'Carey', 'Conduit', 'Super PAC']);
  assert.deepEqual(withConduit, [without[0], without[1], theme.conduitColour, without[2]], 'the others keep their colours');
  assert.deepEqual(theme.categoryColours([]), []);
  assert.deepEqual(theme.categoryColours(['Conduit', 'Conduit']), [theme.conduitColour, theme.conduitColour]);
  const slots = Array.from({length: 36}, (_, i) => theme.categoryColour(i));
  assert.ok(!slots.includes(theme.conduitColour), 'no categorical slot is the Conduit colour');
  for (const slot of slots.slice(0, 12)) assert.ok(distance(theme.conduitColour, slot) >= 10.6, `Conduit is ${distance(theme.conduitColour, slot).toFixed(1)} from ${slot}; the slots' own floor is 10.6 (theme-palette.test.js holds it for protan and deutan too)`);
  assert.ok(Object.isFrozen(theme));
});

const COLS = [{id: 'recipient_category', name: 'Recipient category', role: 'dimension', type: 'category'}, {id: 'amount', name: 'Amount', unit: 'cents'}];
function bars(categories, spec = {}) {
  const rows = categories.map((recipient_category, i) => ({recipient_category, amount: 100000 - i * 1000}));
  const host = document.createElement('div');
  plot.render(host, rows, {type: 'bar', dimensions: ['recipient_category'], measures: ['amount'], color: 'recipient_category', columns: COLS, ...spec}, () => {});
  return host;
}
const marksByLabel = host => Object.fromEntries(all(host, n => attr(n, 'class') === 'plot-mark').map(mark => [titles(mark)[0].split('\n')[0], mark]));
const legendItems = host => all(all(host, n => attr(n, 'data-legend') === 'series')[0], n => n.tagName === 'rect');

test('[conduit] contribution-plot: the other categories keep their colours, Conduit is slate, the legend agrees', () => {
  const before = marksByLabel(bars(['Candidate', 'Carey', 'Super PAC']));
  const host = bars(['Candidate', 'Carey', 'Conduit', 'Super PAC']);
  const after = marksByLabel(host);
  for (const name of ['Candidate', 'Carey', 'Super PAC']) assert.equal(attr(after[name], 'fill'), attr(before[name], 'fill'), name);
  assert.equal(attr(after.Conduit, 'fill'), theme.conduitColour);
  assert.deepEqual(legendItems(host).map(r => attr(r, 'fill')),
    ['Candidate', 'Carey', 'Conduit', 'Super PAC'].map(name => attr(after[name], 'fill')), 'legend swatches are the mark colours');
});

test('[conduit] contribution-plot: a Conduit mark and legend item say what a Conduit is; no other does', () => {
  const host = bars(['Candidate', 'Conduit', 'Super PAC']);
  const marks = marksByLabel(host);
  assert.ok(titles(marks.Conduit)[0].endsWith('\n' + NOTE), titles(marks.Conduit)[0]);
  assert.ok(!titles(marks.Candidate)[0].includes('Conduit:') && !titles(marks['Super PAC'])[0].includes('Conduit:'));
  assert.ok(marks.Conduit.attrs.get('aria-label').endsWith(NOTE), 'the accessible name carries it too');
  const swatches = legendItems(host);
  assert.deepEqual(swatches.map(r => titles(r)), [[], [NOTE], []]);
  const legendText = all(all(host, n => attr(n, 'data-legend') === 'series')[0], n => n.tagName === 'text' && own(n) === 'Conduit');
  assert.deepEqual(legendText.map(titles), [[NOTE]]);
});

test('[conduit] contribution-plot: the note only follows the recipient category, not a value that happens to read Conduit', () => {
  const cols = [{id: 'party', name: 'Party', role: 'dimension', type: 'category'}, ...COLS.slice(1)];
  const host = document.createElement('div');
  plot.render(host, [{party: 'Conduit', amount: 500}, {party: 'D', amount: 400}], {type: 'bar', dimensions: ['party'], measures: ['amount'], color: 'party', columns: cols}, () => {});
  assert.ok(all(host, n => n.tagName === 'title').every(n => !own(n).includes('earmark platform')));
});

test('[conduit] contribution-charts: stacked members and their legend', () => {
  const cols = [{id: 'company', name: 'Company', role: 'dimension'}, {id: 'recipient_category', name: 'Recipient category', role: 'dimension'}, {id: 'amount', name: 'Amount', unit: 'cents'}];
  const draw = categories => contributionCharts.render(document.createElement('div'),
    categories.map((recipient_category, i) => ({company: 'Verizon', recipient_category, amount: 900 - i * 10})),
    {type: 'stacked', dimensions: ['company', 'recipient_category'], measures: ['amount'], color: 'recipient_category', columns: cols}, () => {});
  const fillsOf = svg => Object.fromEntries(all(svg, n => attr(n, 'class') === 'plot-mark').map(m => [attr(m, 'aria-label').match(/Recipient category: ([^;]+)|\/ ([^;:]+)/)?.[0] || attr(m, 'aria-label'), attr(m, 'fill')]));
  const legend = svg => all(svg, n => n.tagName === 'rect' && n.attrs.has('rx')).map(r => attr(r, 'fill'));
  const without = draw(['Candidate', 'Carey', 'Super PAC']);
  const withIt = draw(['Candidate', 'Carey', 'Conduit', 'Super PAC']);
  const base = legend(without);
  assert.deepEqual(base, [0, 1, 2].map(i => theme.categoryColour(i)));
  assert.deepEqual(legend(withIt), [base[0], base[1], theme.conduitColour, base[2]]);
  const marks = all(withIt, n => attr(n, 'class') === 'plot-mark');
  const conduit = marks.filter(m => attr(m, 'fill') === theme.conduitColour);
  assert.equal(conduit.length, 1);
  assert.ok(titles(conduit[0]).some(t => t.includes(NOTE)));
  assert.ok(all(withIt, n => n.tagName === 'title' && own(n) === NOTE).length >= 1, 'the legend swatch is titled');
  assert.ok(fillsOf(without));
});

const code = file => fs.readFileSync(path.join(APP, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

test('[conduit] graph.js colours and titles the recipient-type panel through theme.js', () => {
  const graph = code('graph.js');
  assert.ok(graph.includes("name==='categories'&&typeof MentatTheme.categoryColours==='function'?MentatTheme.categoryColours(shown.map(row=>row.id)):null"));
  assert.ok(graph.includes('typed?typed[i]:colourOf(i)'), 'the bar takes the typed colour when there is one');
  assert.ok(graph.includes("name==='categories'&&MentatTheme.categoryNote&&MentatTheme.categoryNote(row.id)"), 'the row title carries the note');
});

test('[conduit] the Chart Studio records table titles a Conduit category cell', () => {
  const studio = code('chart-studio.js');
  assert.ok(studio.includes("if(id==='recipient_category'&&globalThis.MentatTheme?.categoryNote?.(value))td.title=globalThis.MentatTheme.categoryNote(value);"));
});

test('[conduit] evidence.js shows the note on the committee badge and the rail row', () => {
  const evidence = code('evidence.js');
  assert.ok(evidence.includes('globalThis.MentatTheme.categoryNote(category)'));
  assert.ok(evidence.includes('conduitNote(pane.category), pane.categorySource'), 'the badge title');
  assert.ok(evidence.includes('conduitNote(item.category)'), 'the rail row title');
});
