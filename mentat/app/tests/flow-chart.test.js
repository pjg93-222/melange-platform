'use strict';
/* flow-chart.js (lane F): the SVG the Flow views draw, read as markup. Gradient ribbons
   (userSpaceOnUse, source colour into target colour), the other ribbon modes, the 40%
   default opacity, board #44's sublines, party rings, escaping, no inline style (CSP),
   unique ids per chart, and the pooled-money tooltip. Interaction runs in the browser pass. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const layout = require('../flow-layout.js');
const chart = require('../flow-chart.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const contract = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'flow.json'), 'utf8'));
const FAIRSHAKE = contract.examples.find(item => item.name.startsWith('FAIRSHAKE 2023-2024')).body;
const FIXTURE = contract.examples.find(item => item.name.startsWith('a committee flow')).body;
const PALETTE = ['#327a7a', '#57447b', '#8b6984', '#eca91c', '#de8aab', '#7d4b00', '#3cb0a3', '#83333e', '#99973f',
  '#a09eea', '#d88766', '#509ecd'];
const all = (text, pattern) => [...text.matchAll(pattern)];

test('gradient ribbons: one userSpaceOnUse gradient per ribbon, source colour into target colour', () => {
  const svg = chart.svgString(FAIRSHAKE, {width: 1200, height: 560, palette: PALETTE});
  const result = layout.layout(FAIRSHAKE, {width: 1200, height: 560});
  const colours = layout.colours(result, PALETTE, '#a39b92');
  const gradients = all(svg, /<linearGradient id="flow-g-(\d+)" gradientUnits="userSpaceOnUse" x1="([\d.]+)" y1="0" x2="([\d.]+)" y2="0"><stop offset="0" stop-color="(#[0-9a-f]{6})" stop-opacity="([01])"\/><stop offset="1" stop-color="(#[0-9a-f]{6})" stop-opacity="([01])"\/>/g);
  assert.equal(gradients.length, result.links.length);
  for (const match of gradients) {
    const link = result.links[Number(match[1])];
    const source = result.nodes.find(node => node.id === link.source);
    const target = result.nodes.find(node => node.id === link.target);
    assert.equal(Number(match[2]), Math.round(link.gradient.x1 * 100) / 100);
    assert.equal(Number(match[3]), Math.round(link.gradient.x2 * 100) / 100);
    if (!source.stub && !target.stub) {
      assert.equal(match[4], colours.get(source.id));
      assert.equal(match[6], colours.get(target.id));
      assert.deepEqual([match[5], match[7]], ['1', '1']);
    }
  }
  assert.equal(all(svg, /class="flow-link[^"]*"[^>]*fill-opacity="0.4"/g).length, result.links.length);
});

test('Source, Target and Outcome colour ribbons by a node, never by an original donor', () => {
  const result = layout.layout(FAIRSHAKE);
  const colours = layout.colours(result, PALETTE, '#a39b92');
  for (const mode of ['source', 'target', 'outcome']) {
    const svg = chart.svgString(FAIRSHAKE, {ribbons: mode, palette: PALETTE});
    const moreno = all(svg, /<path class="flow-link" data-link="committee:C00836221&#8594;candidate:S4OH00192:support/g);
    assert.equal(moreno.length, 0, 'data-link ids are raw arrows, escaped only when needed');
    const fill = svg.match(/data-link="committee:C00836221→candidate:S4OH00192:support→ie_support" d="[^"]+" fill="([^"]+)"/)[1];
    if (mode === 'source') assert.equal(fill, colours.get('committee:C00836221'));   // the pooling committee's colour
    if (mode === 'target') assert.equal(fill, colours.get('candidate:S4OH00192:support'));
    if (mode === 'outcome') assert.equal(fill, colours.get('candidate:S4OH00192:support'));
    assert.notEqual(fill, colours.get('donor:COINBASE'));
  }
});

test("board #44's words under the bars, party rings and short money", () => {
  const svg = chart.svgString(FAIRSHAKE, {palette: PALETTE});
  assert.ok(svg.includes('>Coinbase</text>'));
  assert.ok(svg.includes('>$68.5M</text>'));
  assert.ok(svg.includes('>incl. $23.0M bitcoin still held</text>'));
  assert.ok(svg.includes('>cash $64.2M + bitcoin $23.0M</text>'));
  assert.ok(svg.includes('>Other donors $5.8M</text>'));
  assert.ok(svg.includes('>Other donors $6.3M</text>'));
  assert.ok(svg.includes('>Bernie Moreno · support</text>'));
  assert.ok(svg.includes('>Katherine Porter · oppose</text>'));
  assert.ok(svg.includes('>Other candidates</text>'));
  assert.ok(svg.includes('>$55.8M</text>'));
  assert.ok(svg.includes('>Kept by DAJ and PP</text>'));
  assert.equal(all(svg, /class="flow-party"[^>]*stroke="#83333e"/g).length, 2);   // Moreno and Justice (R)
  assert.equal(all(svg, /class="flow-party"[^>]*stroke="#509ecd"/g).length, 4);   // Slotkin, Porter, Gallego, Helmer (D)
  assert.ok(svg.includes('>RECEIVED FROM</text>') && svg.includes('>LANDED ON</text>'));
  assert.ok(svg.includes('fill="url(#flow-hatch)"'), 'not yet spent and kept are hatched');
});

test('no inline style, labels escaped, one tab stop, unique ids per chart', () => {
  const hostile = JSON.parse(JSON.stringify(FIXTURE));
  hostile.nodes.find(node => node.id === 'donor:ACME CORP').label = '<img src=x onerror=alert(1)>';
  const svg = chart.svgString(hostile, {palette: PALETTE, idPrefix: 'chartA'});
  assert.ok(!/ style=/.test(svg) && !svg.includes('<style'), 'CSP: no inline style');
  assert.ok(!svg.includes('<img'), 'labels are escaped');
  assert.ok(svg.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.equal(all(svg, /tabindex="0"/g).length, 1);
  assert.ok(svg.includes('id="chartA-g-0"') && !svg.includes('id="flow-g-0"'));
});

test('the pooled-money tooltip names the route, the spender and the share', () => {
  const result = layout.layout(FAIRSHAKE);
  const context = {result, colours: layout.colours(result, PALETTE, '#a39b92')};
  context.outcomes = layout.outcomes(result, context.colours);
  const link = result.links.find(item => item.source === 'committee:C00836221' && item.target === 'candidate:S4OH00192:support');
  const text = chart.describe({link}, context, FAIRSHAKE);
  assert.equal(text.title, 'FAIRSHAKE → Defend American Jobs → Bernie Moreno (R-OH), support');
  assert.equal(text.lines[0], '$40,134,929.71 spent by Defend American Jobs');
  assert.equal(text.lines[1], '90.4% of its 2023–2024 receipts came from FAIRSHAKE · pooled, not traced dollars');
  assert.ok(text.lines[2].includes('% of Landed on') && text.lines[2].includes('filings'));
  const own = result.links.find(item => item.source === 'bucket:own-ads' && item.target === 'candidate:S4CA00522:oppose');
  const direct = chart.describe({link: own}, context, FAIRSHAKE);
  assert.equal(direct.title, 'FAIRSHAKE → Katherine Porter (D-CA), oppose');
  assert.ok(!direct.lines.some(line => line.includes('pooled')), "the root's own ads are not pooled by another committee");
  const node = result.nodes.find(item => item.id === 'balance:unspent:2');
  assert.ok(chart.describe({node}, context, FAIRSHAKE).lines.includes(
    'cash on hand $64,244,197.94 at the end of the window + bitcoin $23,000,000.00 still held'));
});

test('the Other bucket tooltip lists its members, and says only the count when the body dropped them (hosted, unlisted)', () => {
  const result = layout.layout(FAIRSHAKE);
  const context = {result, format: {short: chart.short, full: chart.full}};
  const node = Object.assign({}, result.nodes[0], {bucket: 'other', member_count: 399, members: [{label: 'Acme PAC', value: 1500000}]});
  const listed = chart.describe({node}, context, FAIRSHAKE).lines;
  assert.ok(listed.some(line => line.startsWith('399 grouped: Acme PAC ')), listed.join(' | '));
  delete node.members;
  const lines = chart.describe({node}, context, FAIRSHAKE).lines;
  assert.ok(lines.includes('399 grouped'), lines.join(' | '));
  assert.ok(!lines.some(line => /grouped:/.test(line)));
});

test('carryover (b): a carried-over committee\u2019s tooltip says where its gate came from, in the payload\u2019s words', () => {
  const body = JSON.parse(JSON.stringify(FIXTURE));
  const bridge = body.nodes.find(node => node.kind === 'committee' && !node.root && !node.stub);
  bridge.gate_source = 'carryover';
  bridge.carryover = {label: 'gate opened by 2023-2024 transfers', gate: '2025-01-01',
    opened_by: [{source: body.root.id, label: 'Root Fund', first_date: '2024-03-02', amount: 800000}]};
  const cash = body.nodes.find(node => node.stub === 'prior_cash');
  if (cash) cash.carryover = bridge.carryover.label;
  const context = {result: layout.layout(body, {width: 1200, height: 560}), format: {short: chart.short, full: chart.full}};
  const said = chart.describe({node: context.result.nodes.find(node => node.id === bridge.id)}, context, body).lines;
  assert.ok(said.includes('Counted from the start of the cycle: gate opened by 2023-2024 transfers'), said.join(' | '));
  assert.ok(said.some(line => line.startsWith('Opened by Root Fund $800K from 2024-03-02')), said.join(' | '));
  const before = chart.describe({node: layout.layout(FIXTURE, {width: 1200, height: 560}).nodes.find(node => node.id === bridge.id)},
    {result: layout.layout(FIXTURE, {width: 1200, height: 560}), format: {short: chart.short, full: chart.full}}, FIXTURE).lines;
  assert.ok(!before.some(line => /start of the cycle/.test(line)), 'a committee reached in the cycle says nothing of it');
});

/* ------------------------------------------------------- lane F2a (FL2-FL8) */
const SHAPES = require('./flow-shapes.corpus.js');
const close = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-6, `${message}: ${a} vs ${b}`);
const texts = (svg, name) => all(svg, new RegExp(`<text class="${name}"[^>]*>([^<]*)`, 'g')).map(match => match[1]);
const estimate = (text, size) => String(text).length * size * 0.56;
const fitNode = (text, width, size, suffix) => {
  const tail = suffix == null ? '' : String(suffix);
  const room = Math.floor(width / (size * 0.56)) - tail.length;
  return String(text).length <= room ? String(text) + tail : String(text).slice(0, Math.max(1, room - 1)).trimEnd() + '…' + tail;
};

test('FL3: a line is cut between words with an ellipsis, and an amount at its end is kept whole', () => {
  const line = 'Cash from before this window $1.3M';
  assert.equal(chart.fitLine(line, 400, 11.5, fitNode, estimate), line, 'room enough: as it is');
  const cut = chart.fitLine(line, 150, 11.5, fitNode, estimate);
  assert.match(cut, /^Cash from( before)?… \$1\.3M$/, cut);
  assert.ok(estimate(cut, 11.5) <= 150);
  assert.equal(chart.fitLine('Amalgamated Bank', 400, 13, fitNode, estimate), 'Amalgamated Bank');
  assert.equal(chart.fitLine('Coinbase Commerce (exchange)', 150, 13, fitNode, estimate), 'Coinbase Commerce…');
  // One word longer than the room: the fitter's cut, still with an ellipsis.
  assert.match(chart.fitLine('Supercalifragilisticexpialidocious', 60, 13, fitNode, estimate), /…$/);
});

test('FL1-FL6: the SLF 2023-2024 flow at 780 x 420 draws to scale, one line under a label, the rest in the tooltip', () => {
  const body = SHAPES.slf2024;
  const svg = chart.svgString(body, {width: 780, height: 420, palette: PALETTE, opacity: chart.VIEW_OPACITY});
  const heights = all(svg, /<rect class="flow-bar"[^>]*height="([\d.]+)"/g).map(match => Number(match[1]));
  assert.ok(Math.max(...heights) > 100 && heights.filter(height => height > 2).length >= heights.length / 2,
    `bars ${heights.join(',')}`);
  // Lane G1 (board #44): the views rest at 20% (was 28%) so ribbons that overlap stay pastel.
  assert.equal(all(svg, /<path class="flow-link[^"]*"[^>]*fill-opacity="0.2"/g).length, all(svg, /<path class="flow-link/g).length);
  assert.equal(chart.VIEW_OPACITY, 0.2);
  const result = layout.layout(body, {width: 780, height: 420});
  // No node draws more than one line under its label.
  for (const node of result.nodes.filter(item => !item.stub)) {
    const group = svg.slice(svg.indexOf(`data-node="${node.id.replace(/&/g, '&amp;').replace(/'/g, '&#39;')}"`));
    const own = group.slice(0, group.indexOf('</g>'));
    assert.ok((own.match(/class="flow-subline"/g) || []).length <= 1, node.id);
  }
  // The root's three "back from" lines: the first drawn, the other two in its tooltip, all three in its name.
  const root = result.nodes.find(node => node.id === 'committee:C00571703');
  const context = {result, colours: layout.colours(result, PALETTE, '#a39b92'), format: {short: chart.short, full: chart.full}};
  const said = chart.describe({node: root}, context, body).lines;
  for (const line of root.sublines) assert.ok(said.includes(line) || line === root.subline_shown, line);
  assert.deepEqual(root.sublines.filter(line => line.startsWith('back from ') && !said.includes(line)), [root.subline_shown],
    'the one back-from line not in the tooltip is the one drawn');
  const name = svg.match(/data-node="committee:C00571703"[^>]*aria-label="([^"]*)"/)[1];
  for (const line of root.sublines) assert.ok(name.includes(line.replace(/'/g, '&#39;')), line);
  // A committee whose addition lines were shed: every one of them is in its tooltip.
  const crossroads = result.nodes.find(node => node.label === 'American Crossroads');
  const lines = chart.describe({node: crossroads}, context, body).lines;
  for (const line of crossroads.sublines) assert.ok(lines.includes(line) || line === crossroads.subline_shown, line);
});

test('FL3: no halo; the header that cannot fit is cut between words and keeps its words in a title', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'flow.css'), 'utf8');
  assert.ok(!/paint-order/.test(css), 'labels carry no white halo');
  const svg = chart.svgString(SHAPES.slf2024, {width: 780, height: 420, palette: PALETTE});
  const headers = texts(svg, 'flow-header');
  assert.ok(headers.includes('RECEIVED FROM') && headers.includes('LANDED ON'));
  const cut = headers.find(text => text.startsWith('SENATE'));
  assert.ok(cut !== 'SENATE LEADERSHIP FUND' ? /…$/.test(cut) && svg.includes('<title>SENATE LEADERSHIP FUND</title>') : true, cut);
  for (const text of headers) assert.ok(!/[A-Z]…/.test(text) || text.endsWith('…'), text);
});

test('FL3: no knocked-out box and no halo behind a label; bars and labels are never masked', () => {
  const svg = chart.svgString(FAIRSHAKE, {width: 1100, height: 520, palette: PALETTE, idPrefix: 'm'});
  assert.ok(svg.includes('<g class="flow-links">'), 'the ribbons are painted as they are');
  assert.ok(!/<mask|<filter| mask=| filter=/.test(svg), 'no mask or filter anywhere');
  assert.ok(!/<text class="flow-(label|amount|subline)"[^>]*stroke/.test(svg), 'no stroke on any label');
});

test('FL2/FL4: the ribbons rest translucent; a hover dims only ribbons and leaves labels at 85% or more', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'flow.css'), 'utf8');
  const opacity = selector => {
    const rule = css.split('}').find(block => block.includes(selector + ' {') || block.includes(selector + '{'));
    const match = rule && /(?:fill-)?opacity:\s*([\d.]+)/.exec(rule.slice(rule.indexOf('{')));
    return match ? Number(match[1]) : null;
  };
  assert.equal(opacity('.flow-chart.is-tracing .flow-link:not(.is-lit)'), 0.12);
  assert.equal(opacity('.flow-chart.is-tracing .flow-link.is-lit'), 0.6);
  const labels = opacity('.flow-chart.is-tracing .flow-node:not(.is-lit) text');
  assert.ok(labels === null || labels >= 0.85, `labels off the route at ${labels}`);
  assert.equal(opacity('.flow-chart.is-tracing .flow-node:not(.is-lit)'), null, 'no rule fades a whole node');
});

test('FL8: board #46, the first stage labelled left of its bars and the stages spread over the whole width', () => {
  const body = SHAPES.crypto2026;
  const options = {width: 900, height: 480, headers: false, labelSize: 13, labelLine: 16, labelLines: 1, minPadding: 8,
    nodeWidth: 10, firstLabels: 'left', firstLabelWidth: 150, labelWidth: 170, margin: {top: 8, right: 4, bottom: 10, left: 4},
    palette: PALETTE, idPrefix: 's'};
  const result = layout.layout(body, options);
  const xs = result.columns.map(column => column.x);
  assert.equal(xs[0], 4 + 150, 'the first stage starts after its labels');
  const steps = xs.slice(1).map((x, at) => x - xs[at]);
  for (const step of steps) close(step, steps[0], 'even stage spacing');
  close(xs[xs.length - 1] + 10 + 170, 900 - 4, 'the last stage and its labels reach the right edge');
  const first = result.columns[0].ids.map(id => result.nodes.find(node => node.id === id));
  assert.ok(first.every(node => node.label_side === 'left' && node.x1 - node.x0 === 10));
  const svg = chart.svgString(body, options);
  for (const node of first) {
    const group = svg.slice(svg.indexOf(`data-node="${node.id}"`));
    const own = group.slice(0, group.indexOf('</g>'));
    if (node.label_level === 4) continue;
    assert.match(own, /<text class="flow-label" x="([\d.]+)" y="[\d.]+" text-anchor="end">/, node.id);
    assert.equal(Number(/<text class="flow-label" x="([\d.]+)"/.exec(own)[1]), xs[0] - 8, `${node.id}: right-aligned 8 px left of its bar`);
  }
});

/* ------------------------------------------------------- lanes G1/G1b (#44) */
test('lane G1: a hover in a committee flow lights the hovered side of the root\u2019s pool, not every donor', () => {
  const result = layout.layout(FAIRSHAKE);
  const root = result.nodes.find(node => node.root && !node.stub);
  const moreno = result.links.find(link => link.source === 'committee:C00836221' && link.target === 'candidate:S4OH00192:support');
  const whole = layout.route(result, moreno.id), lit = chart.traced(result, moreno.id);
  const intoRoot = result.links.filter(link => link.target === root.id);
  assert.ok(intoRoot.length >= 5 && intoRoot.every(link => whole.links.has(link.id)), 'the whole route still reaches every donor');
  assert.ok(intoRoot.every(link => !lit.links.has(link.id)), 'no ribbon into the root is lit for a candidate\u2019s route');
  const feeder = result.links.find(link => link.source === root.id && link.target === 'committee:C00836221');
  assert.ok(lit.links.has(moreno.id) && lit.links.has(feeder.id), 'FAIRSHAKE -> Defend American Jobs -> Moreno is lit');
  assert.ok(lit.nodes.has(root.id) && lit.nodes.has('committee:C00836221') && !lit.nodes.has('donor:COINBASE'));
  for (const id of lit.links) assert.ok(whole.links.has(id), 'the lit ribbons are part of the route');
  // A donor's ribbon lights its way into the pool, not what the pool paid out.
  const coinbase = result.links.find(link => link.source === 'donor:COINBASE' && link.target === root.id);
  const up = chart.traced(result, coinbase.id);
  assert.ok(up.links.has(coinbase.id));
  assert.ok(![...up.links].some(id => result.links.find(link => link.id === id).source === root.id), 'nothing out of the root');
  // The root itself, and a flow without a root (the industry flow), light the whole route as before.
  const sorted = set => [...set].sort();
  assert.deepEqual(sorted(chart.traced(result, root.id).links), sorted(layout.route(result, root.id).links));
  const industry = contract.examples.find(example => example.producer === 'crypto-2024-industry' && example.status === 200).body;
  const drawn = layout.layout(industry);
  for (const link of drawn.links) assert.deepEqual(sorted(chart.traced(drawn, link.id).links), sorted(layout.route(drawn, link.id).links));
});

test('lanes G1/G1b: the tooltip takes the nearest spot 4 px clear of all text, never the point, inside the drawing', () => {
  const meets = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const grown = (item, by) => ({x: item.x - by, y: item.y - by, w: item.w + 2 * by, h: item.h + 2 * by});
  const under = (spot, width, height, x, y) => x > spot.left - 6 && x < spot.left + width + 6 && y > spot.top - 6 && y < spot.top + height + 6;
  // A column of labels right of the point, a header band along the top.
  const labels = [0, 1, 2, 3, 4].map(at => ({x: 220, y: 100 + at * 40, w: 150, h: 14}));
  const text = [...labels, {x: 0, y: 0, w: 800, h: 20}];
  const spot = chart.placeTip({x: 200, y: 140, width: 300, height: 90, room: 800, top: 4, bottom: 600, obstacles: text});
  assert.equal(spot.cover, 0, 'a spot that covers no text exists and is taken');
  // Not only off the words: 4 px of clear space around every one of them (G1b's margin). With no margin
  // the same sweep sits flush under the last label (top 274); the margin is what moves it, to the pixel (278).
  for (const item of text) assert.ok(!meets({x: spot.left, y: spot.top, w: 300, h: 90}, grown(item, 4)), JSON.stringify(item));
  const flush = chart.placeTip({x: 200, y: 140, width: 300, height: 90, room: 800, top: 4, bottom: 600, obstacles: text, margin: 0});
  assert.ok(flush.cover === 0 && flush.top === 274 && spot.top === 278, `flush ${JSON.stringify(flush)}, kept clear ${JSON.stringify(spot)}`);
  assert.ok(!under(spot, 300, 90, 200, 140), 'not over the point');
  assert.ok(spot.left >= 4 && spot.left + 300 <= 796 && spot.top >= 4 && spot.top + 90 <= 596, 'inside the drawing');
  // No clear spot near the point (a clear one 400 px off): the tooltip stays by the point, not across the drawing.
  const gapTo = (spot, width, height, x, y) => Math.hypot(Math.max(0, spot.left - x, x - spot.left - width), Math.max(0, spot.top - y, y - spot.top - height));
  const wall = Array.from({length: 30}, (_, at) => ({x: 0, y: at * 20, w: 700, h: 14}));
  const near = chart.placeTip({x: 300, y: 300, width: 80, height: 40, room: 1000, top: 4, bottom: 600, obstacles: wall});
  assert.ok(near.cover > 0 && gapTo(near, 80, 40, 300, 300) <= 16, `by the point, over the wall: ${JSON.stringify(near)}`);
  // With words everywhere near it, the tooltip covers other labels rather than the hovered route's own or a header.
  for (const kind of ['route', 'header']) {
    const bands = [{x: 0, y: 0, w: 1000, h: 288, kind}, {x: 0, y: 312, w: 1000, h: 288, kind: 'text'}];
    const spared = chart.placeTip({x: 400, y: 300, width: 100, height: 40, room: 1000, top: 4, bottom: 600, obstacles: bands});
    assert.ok(spared.cover > 0 && !meets({x: spared.left, y: spared.top, w: 100, h: 40}, grown(bands[0], 4)) && gapTo(spared, 100, 40, 400, 300) <= 16,
      `${kind} spared: ${JSON.stringify(spared)}`);
    // The same two bands, both plain labels: a label touched counts whole, so the tooltip takes one band, not the
    // 16 px between them (which would touch both) - above the point, by it, as the board sets it.
    const plain = chart.placeTip({x: 400, y: 300, width: 100, height: 40, room: 1000, top: 4, bottom: 600,
      obstacles: bands.map(band => Object.assign({}, band, {kind: 'text'}))});
    assert.ok(plain.top + 40 <= 300 && plain.cover > 0 && !meets({x: plain.left, y: plain.top, w: 100, h: 40}, grown(bands[1], 4))
      && gapTo(plain, 100, 40, 400, 300) <= 16, `plain labels: ${JSON.stringify(plain)}`);
  }
  // Open room above: the tooltip sits above the point, near it, as on board #44.
  const open = chart.placeTip({x: 400, y: 300, width: 200, height: 60, room: 800, top: 4, bottom: 600, obstacles: []});
  assert.ok(open.top + 60 <= 300 && 300 - (open.top + 60) <= 16, `above the point: ${JSON.stringify(open)}`);
  // It never reaches above `top` (the title and subtitle sit above the drawing).
  const low = chart.placeTip({x: 400, y: 230, width: 200, height: 60, room: 800, top: 200, bottom: 600, obstacles: []});
  assert.ok(low.top >= 200 && !under(low, 200, 60, 400, 230), JSON.stringify(low));
  // A bar is avoided when a clean spot is as near; text never loses to a bar.
  const bar = {x: 380, y: 200, w: 12, h: 300, soft: 0.1};
  const beside = chart.placeTip({x: 395, y: 330, width: 200, height: 60, room: 800, top: 4, bottom: 600, obstacles: [bar]});
  assert.ok(!meets({x: beside.left, y: beside.top, w: 200, h: 60}, bar), JSON.stringify(beside));
  // Text everywhere: still a spot, off the point, inside the drawing and near the point; its cover is the words it
  // covers (each box with its 4 px margin), and no spot the sweep weighs is cheaper (distance + a hundredth of the
  // whole of every label it touches).
  const crowded = Array.from({length: 48}, (_, at) => ({x: (at % 8) * 100, y: Math.floor(at / 8) * 100, w: 90, h: 14}));
  const packed = chart.placeTip({x: 390, y: 250, width: 280, height: 100, room: 800, top: 4, bottom: 600, obstacles: crowded});
  assert.ok(packed.left >= 4 && packed.top >= 4 && packed.left + 280 <= 796 && !under(packed, 280, 100, 390, 250), JSON.stringify(packed));
  const cover = (left, top) => crowded.map(item => grown(item, 4)).reduce((sum, item) => {
    const w = Math.min(left + 280, item.x + item.w) - Math.max(left, item.x), h = Math.min(top + 100, item.y + item.h) - Math.max(top, item.y);
    return sum + (w > 0 && h > 0 ? w * h : 0);
  }, 0);
  assert.equal(packed.cover, cover(packed.left, packed.top));
  const touched = (left, top) => crowded.map(item => grown(item, 4)).filter(item => meets({x: left, y: top, w: 280, h: 100}, item))
    .reduce((sum, item) => sum + item.w * item.h, 0);
  const price = (left, top) => gapTo({left, top}, 280, 100, 390, 250) + (top + 100 <= 250 ? 0 : 3) + 0.01 * touched(left, top);
  for (const [left, top] of [[404, 138], [96, 138], [404, 264], [96, 264]])
    assert.ok(price(packed.left, packed.top) <= price(left, top) + 1e-9, `the old spot ${left},${top} is cheaper`);
  assert.ok(gapTo(packed, 280, 100, 390, 250) <= 40, `by the point: ${JSON.stringify(packed)}`);
  // The lit route's ribbon (soft samples, a px per px^2): with free space on both sides of the point, the tooltip
  // takes a side the traced ribbon does not run through - not above, the board's side, where the ribbon is.
  const ribbon = Array.from({length: 30}, (_, at) => ({x: 300 + at * 10, y: 220 + at * 0.5, w: 6, h: 6, soft: 1}));
  const offRoute = chart.placeTip({x: 400, y: 260, width: 160, height: 50, room: 800, top: 4, bottom: 600, obstacles: ribbon});
  assert.ok(offRoute.top + 50 > 260 && !ribbon.some(sample => meets({x: offRoute.left, y: offRoute.top, w: 160, h: 50}, sample))
    && gapTo(offRoute, 160, 50, 400, 260) <= 16, `by the point, clear of the lit ribbon above it: ${JSON.stringify(offRoute)}`);
  const noRibbon = chart.placeTip({x: 400, y: 260, width: 160, height: 50, room: 800, top: 4, bottom: 600, obstacles: []});
  assert.ok(noRibbon.top + 50 <= 260, `without it, above: ${JSON.stringify(noRibbon)}`);
});
