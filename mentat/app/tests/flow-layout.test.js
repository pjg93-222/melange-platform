'use strict';
/* flow-layout.js (lane F): conservation at every node, ordering, Top N plus Other (the
   server's rule, parity-tested on contracts/flow.json), balance and external and loop
   stubs, gradient geometry, the Tableau sigmoid, and board #44's FAIRSHAKE figures to the
   cent. The contract fixture is the one test_flow_route.py asserts against the producer. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const flow = require('../flow-layout.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const contract = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'flow.json'), 'utf8'));
const example = prefix => contract.examples.find(item => item.name.startsWith(prefix));
const FIXTURE = example('a committee flow').body;
const TOP3 = example('Top 3 per column').body;
const FAIRSHAKE = example('FAIRSHAKE 2023-2024').body;
const cents = value => Math.round(value * 100);
const nodeOf = (result, id) => result.nodes.find(node => node.id === id);
const close = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-6, `${message}: ${a} vs ${b}`);

test('every contract example validates and every negative control is refused', () => {
  for (const item of contract.examples) assert.deepEqual(flow.validate(item.body, item.status), [], item.name);
  assert.ok(contract.negative_controls.length >= 10);
  for (const control of contract.negative_controls) {
    const problems = flow.validate(control.payload, control.status);
    assert.ok(problems.length, control.why);
    assert.ok(problems.some(problem => problem.includes(control.expect)), `${control.why}: ${problems.join('; ')}`);
  }
});

test('conservation at every node, in whole cents, on one scale', () => {
  for (const body of [FIXTURE, TOP3, FAIRSHAKE]) {
    const result = flow.layout(body, {width: 1200, height: 560});
    const scale = result.scale / 100;
    for (const node of result.nodes) {
      const inflow = result.links.filter(link => link.target === node.id);
      const outflow = result.links.filter(link => link.source === node.id);
      const centsIn = inflow.reduce((sum, link) => sum + link.cents, 0);
      const centsOut = outflow.reduce((sum, link) => sum + link.cents, 0);
      assert.equal(cents(node.value_in), centsIn, node.id);
      assert.equal(cents(node.value_out), centsOut, node.id);
      if (inflow.length && outflow.length) assert.equal(centsIn, centsOut, `${node.id} balances`);
      if (!node.stub) {
        close(node.y1 - node.y0, Math.max(centsIn, centsOut) * scale, `${node.id} height`);
        if (inflow.length) close(inflow.reduce((sum, link) => sum + (link.ty1 - link.ty0), 0), centsIn * scale, `${node.id} in-stack`);
        if (outflow.length) close(outflow.reduce((sum, link) => sum + (link.sy1 - link.sy0), 0), centsOut * scale, `${node.id} out-stack`);
      }
    }
    for (const link of result.links) close(link.width, link.cents * scale, `${link.id} width`);
  }
});

test('named nodes by value, then buckets, the Other node and balances at the foot', () => {
  const result = flow.layout(FAIRSHAKE);
  const labels = index => result.columns[index].ids.map(id => nodeOf(result, id).label);
  assert.deepEqual(labels(0), ['Coinbase', 'AH Capital Management', 'Ripple Labs', 'Jump Crypto', 'Cameron Winklevoss',
    'Tyler Winklevoss', 'All other contributors']);
  assert.deepEqual(labels(2), ['Defend American Jobs', 'Protect Progress', 'Own ads for and against candidates',
    'Operating, net of refunds', 'Not yet spent']);
  assert.deepEqual(labels(3), ['Bernie Moreno', 'Elissa Slotkin', 'Katherine Porter', 'Ruben Gallego', 'Daniel Helmer',
    'James Justice', 'Other candidates', 'Kept by DAJ and PP']);
  const alpha = flow.layout(FAIRSHAKE, {order: 'alpha'});
  assert.deepEqual(alpha.columns[0].ids.map(id => nodeOf(alpha, id).label), ['AH Capital Management', 'Cameron Winklevoss',
    'Coinbase', 'Jump Crypto', 'Ripple Labs', 'Tyler Winklevoss', 'All other contributors']);
  for (const column of result.columns) {
    const nodes = column.ids.map(id => nodeOf(result, id));
    for (let at = 1; at < nodes.length; at += 1) assert.ok(nodes[at].y0 >= nodes[at - 1].y1, `${column.key} stacked`);
  }
});

test('Top N plus Other is the server rule (parity on the contract examples)', () => {
  const prepared = {nodes: FIXTURE.nodes.map(node => Object.assign({}, node, {vin: cents(node.value_in), vout: cents(node.value_out)})),
    links: FIXTURE.links.map(link => Object.assign({}, link, {cents: cents(link.value)}))};
  const grouped = flow.group(prepared.nodes, prepared.links, 3);
  const mine = grouped.nodes.map(node => node.id).sort();
  const theirs = TOP3.nodes.map(node => node.id).sort();
  assert.deepEqual(mine, theirs);
  const other = grouped.nodes.find(node => node.id === 'other:0');
  const server = TOP3.nodes.find(node => node.id === 'other:0');
  assert.deepEqual({label: other.label, member_count: other.member_count, members: other.members},
    {label: server.label, member_count: server.member_count, members: server.members});
  const key = link => `${link.source}|${link.target}|${link.kind}`;
  const sums = links => Object.fromEntries(links.map(link => [key(link), link.cents === undefined ? cents(link.value) : link.cents]));
  assert.deepEqual(sums(grouped.links), sums(TOP3.links));
  // Idempotent on an already-grouped answer, and top=0 draws every node.
  assert.equal(flow.layout(TOP3, {top: 3}).nodes.length, TOP3.nodes.length);
  assert.equal(flow.layout(FIXTURE, {top: 0}).nodes.length, FIXTURE.nodes.length);
});

test("FAIRSHAKE 2023-2024 reproduces board #44's real figures to the cent", () => {
  const result = flow.layout(FAIRSHAKE);
  const value = id => cents(nodeOf(result, id).value);
  const root = nodeOf(result, 'committee:C00835959');
  assert.equal(cents(root.value_in), 21742388052);
  assert.equal(cents(root.value_out), 21742388052);
  const out = target => result.links.filter(link => link.source === root.id && link.target === target)
    .reduce((sum, link) => sum + link.cents, 0);
  assert.equal(out('committee:C00836221'), 5455000000);
  assert.equal(out('committee:C00848440'), 3170000000);
  assert.equal(out('bucket:own-ads'), 4066182061);
  assert.equal(out('balance:operating:2'), 326786197);
  assert.equal(out('balance:unspent:2'), 8724419794);
  assert.equal(5455000000 + 3170000000 + 4066182061 + 326786197 + 8724419794, 21742388052);
  const unspent = nodeOf(result, 'balance:unspent:2');
  assert.deepEqual([unspent.cash, unspent.held, unspent.held_kinds], [64244197.94, 23000000, ['bitcoin']]);
  assert.equal(value('donor:COINBASE'), 6849999500);
  assert.equal(nodeOf(result, 'donor:COINBASE').held, 23000000);
  assert.equal(value('donor:AH CAPITAL MANAGEMENT'), 6700000001);
  assert.equal(value('donor:RIPPLE LABS INC'), 4500000000);
  assert.equal(value('donor:JUMP CRYPTO'), 2500000000);
  assert.equal(value('other:0'), 702388551);
  assert.equal(value('committee:C00836221'), 6032500001);
  assert.equal(cents(nodeOf(result, 'stub:external:committee:C00836221:C00836221').value), 577500001);
  assert.equal(cents(nodeOf(result, 'stub:external:committee:C00848440:C00848440').value), 627525501);
  // Downstream committees' figures are the measurement's (the fixture's own), not written here:
  // every candidate node holds exactly the ribbons into it, and Other is the rest of the column.
  const landed = result.columns[3].ids.map(id => nodeOf(result, id));
  for (const node of landed)
    assert.equal(cents(node.value), FAIRSHAKE.links.filter(link => link.target === node.id)
      .reduce((sum, link) => sum + cents(link.value), 0), node.id);
  const other = nodeOf(result, 'other:3');
  assert.equal(other.label, 'Other candidates');
  assert.notEqual((other.value / 1e6).toFixed(1), '55.0');   // the board printed $55.0M; the filings do not say that
  const jobs = nodeOf(result, 'committee:C00836221');
  assert.equal(jobs.share_from_upstream[0].share, 0.904269);
  assert.equal(nodeOf(result, 'committee:C00848440').share_from_upstream[0].share, 0.833421);
  assert.equal(flow.largestRoutes(FAIRSHAKE, 6)[0].committee_label, 'Defend American Jobs');
  assert.equal(flow.largestRoutes(FAIRSHAKE, 6)[0].value, 40134929.71);
});

test('stubs sit beside the node they attach to, never in a column', () => {
  const result = flow.layout(FIXTURE, {stubLength: 50});
  const inColumns = new Set(result.columns.flatMap(column => column.ids));
  assert.ok(result.stubs.length >= 4);
  for (const stub of result.stubs) {
    assert.ok(!inColumns.has(stub.id), stub.id);
    const host = nodeOf(result, stub.attach);
    const link = result.links.find(item => item.source === stub.id || item.target === stub.id);
    if (link.target === stub.id) {                       // leaves to the side
      assert.equal(link.x0, host.x1);
      assert.equal(link.x1, host.x1 + 50);
    } else {                                             // feeds in from the side
      assert.equal(link.x1, host.x0);
      assert.equal(link.x0, host.x0 - 50);
    }
    assert.ok(link.sy0 >= host.y0 - 1e-9 && link.sy1 <= host.y1 + 1e-9, `${stub.id} on its node`);
  }
  // The loop: "back from Bridge PAC" into the root, "returned to Root Fund" out of Bridge PAC.
  assert.equal(nodeOf(result, 'stub:return_in:committee:C90000001:C90000002').label, 'back from Bridge PAC');
  assert.equal(nodeOf(result, 'stub:return:committee:C90000002:C90000001').label, 'returned to Root Fund');
  const onward = result.links.find(link => link.target === 'stub:onward:committee:C90000002:C90000003');
  assert.ok(!('date_order_ok' in onward));
});

test('ribbons stack by the position of the node at their other end', () => {
  const result = flow.layout(FAIRSHAKE);
  for (const node of result.nodes.filter(item => !item.stub)) {
    const out = result.links.filter(link => link.source === node.id && !nodeOf(result, link.target).stub)
      .sort((a, b) => a.sy0 - b.sy0);
    for (let at = 1; at < out.length; at += 1)
      assert.ok(nodeOf(result, out[at].target).y0 >= nodeOf(result, out[at - 1].target).y0, `${node.id} out order`);
    const into = result.links.filter(link => link.target === node.id && !nodeOf(result, link.source).stub)
      .sort((a, b) => a.ty0 - b.ty0);
    for (let at = 1; at < into.length; at += 1)
      assert.ok(nodeOf(result, into[at].source).y0 >= nodeOf(result, into[at - 1].source).y0, `${node.id} in order`);
  }
});

test('gradient geometry: userSpaceOnUse from the source edge to the target edge', () => {
  const result = flow.layout(FAIRSHAKE, {width: 1400});
  for (const link of result.links) {
    const source = nodeOf(result, link.source), target = nodeOf(result, link.target);
    if (!source.stub && !target.stub) {
      assert.equal(link.gradient.x1, source.x1);
      assert.equal(link.gradient.x2, target.x0);
      assert.ok(link.gradient.x2 > link.gradient.x1, 'left to right');
    }
    assert.equal(link.gradient.from, link.source);
    assert.equal(link.gradient.to, link.target);
  }
  const columns = result.columns.map(column => column.x);
  assert.deepEqual(columns, columns.slice().sort((a, b) => a - b));
});

test("the Tableau sigmoid: 49 points, T from -6 to 6, y0 + (y1 - y0) / (1 + e^-T)", () => {
  const points = flow.sigmoidPoints(100, 10, 400, 210);
  assert.equal(points.length, 49);
  assert.equal(points[0][0], 100);
  assert.equal(points[48][0], 400);
  close(points[24][1], 110, 'the midpoint at T=0');
  close(points[0][1], 10 + 200 / (1 + Math.exp(6)), 'T=-6');
  const result = flow.layout(FIXTURE, {curve: 'sigmoid'});
  const link = result.links.find(item => !nodeOf(result, item.source).stub && !nodeOf(result, item.target).stub);
  assert.equal((link.path.match(/L/g) || []).length, 97);    // 49 top points + 49 bottom points
  assert.ok(flow.layout(FIXTURE).links[0].path.includes('C'), 'cubic by default');
});

test('a hover lights one whole route upstream and downstream', () => {
  const result = flow.layout(FAIRSHAKE);
  const lit = flow.route(result, 'candidate:S4OH00192:support');
  assert.ok(lit.nodes.has('committee:C00836221'));
  assert.ok(lit.nodes.has('committee:C00835959'));
  assert.ok(lit.nodes.has('donor:COINBASE'));
  assert.ok(!lit.nodes.has('candidate:S4MI00470:support'));
  const ribbon = result.links.find(link => link.source === 'committee:C00835959' && link.target === 'committee:C00848440');
  const route = flow.route(result, ribbon.id);
  assert.ok(route.nodes.has('candidate:S4MI00470:support'));
  assert.ok(!route.nodes.has('candidate:S4OH00192:support'));
});

test('the industry flow (#45): outcomes keep their order, companies group as Other crypto companies', () => {
  const item = contract.examples.find(example => example.producer === 'crypto-2024-industry' && example.status === 200);
  assert.deepEqual(flow.validate(item.body), []);
  const result = flow.layout(item.body);
  const last = result.columns[result.columns.length - 1].ids.map(id => nodeOf(result, id));
  const outcomes = last.filter(node => node.outcome);
  assert.deepEqual(outcomes.map(node => node.sequence), outcomes.map(node => node.sequence).slice().sort((a, b) => a - b));
  assert.equal(outcomes[0].label, 'Support Republicans');
  assert.ok(result.nodes.some(node => node.label === 'Other crypto companies'));
  const fairshake = nodeOf(result, 'committee:C00835959');
  assert.equal(fairshake.stage, 1);
  assert.equal(nodeOf(result, 'committee:C00836221').stage, 2);
  const regrouped = flow.layout(item.body, {top: 3});
  assert.ok(regrouped.nodes.some(node => node.id === 'committee:C00835959'), 'a followed committee is never regrouped');
});

test("the root rule sets aside a disclosed 'Not in the summary' stub, and nothing else", () => {
  const withStub = balance => {
    const body = JSON.parse(JSON.stringify(FIXTURE));
    const root = 'committee:' + body.root.id;
    const byId = new Map(body.nodes.map(node => [node.id, node]));
    const stub = 'stub:external:' + root + ':' + body.root.id;
    body.nodes.push({id: stub, stage: byId.get(root).stage, label: 'Not in the summary', kind: 'balance', stub: 'external',
      attach: root, balance, value_in: 0, value_out: 25000});
    body.links.push({source: stub, target: root, value: 25000, kind: 'external_in', rows: 0});
    const operating = body.links.find(link => link.source === root && link.target === 'balance:operating:2');
    operating.value = Math.round((operating.value + 25000) * 100) / 100;
    for (const [id, key] of [[root, 'value_in'], [root, 'value_out'], ['balance:operating:2', 'value_in']])
      byId.get(id)[key] = Math.round((byId.get(id)[key] + 25000) * 100) / 100;
    return body;
  };
  assert.deepEqual(flow.validate(withStub('unreconciled')), []);
  assert.ok(flow.validate(withStub('prior_cash')).some(problem => /the root takes in more or less/.test(problem)));
});

test('carryover (b): the starting cash of a carried-over committee says so under its name', () => {
  // BRIDGE PAC with starting cash, as a committee whose gate the previous cycle opened has.
  const withCash = label => {
    const body = JSON.parse(JSON.stringify(FIXTURE));
    const bridge = body.nodes.find(node => node.kind === 'committee' && !node.root && !node.stub);
    const id = `stub:prior_cash:${bridge.id}:${bridge.committee_id}`;
    body.nodes.push(Object.assign({id, stage: bridge.stage, label: 'Cash from before this window', kind: 'balance',
      stub: 'prior_cash', attach: bridge.id, balance: 'prior_cash', value_in: 0, value_out: 12000}, label ? {carryover: label} : {}));
    body.links.push({source: id, target: bridge.id, value: 12000, kind: 'prior_cash', rows: 0});
    return {body, bridge};
  };
  const carried = withCash('gate opened by 2023-2024 transfers');
  const host = flow.layout(carried.body, {width: 1200, height: 560}).nodes.find(node => node.id === carried.bridge.id);
  assert.ok(host.sublines.includes('Cash from before this window $12K'), host.sublines.join(' | '));
  assert.ok(host.sublines.includes('carried over from 2023-2024'), host.sublines.join(' | '));
  const plain = withCash('');
  const other = flow.layout(plain.body, {width: 1200, height: 560}).nodes.find(node => node.id === plain.bridge.id);
  assert.ok(other.sublines.includes('Cash from before this window $12K'), other.sublines.join(' | '));
  assert.ok(!other.sublines.some(line => /carried over/.test(line)), 'only a carried-over committee says so');
});

test("lane I: a donor charged back beyond the window's gifts is refunded, never a negative ribbon", () => {
  const body = example("donor charge-backs").body;
  assert.deepEqual(flow.validate(body), []);
  const result = flow.layout(body, {width: 1200, height: 560});
  assert.ok(result.links.every(link => link.cents > 0 && link.width > 0), 'every ribbon has a width');
  assert.ok(!result.nodes.some(node => /WEIRMAN|DYKE|REYNOLDS/.test(node.id)), 'no donor at or below zero is drawn');
  const refunded = nodeOf(result, 'balance:refunded:2');
  assert.equal(cents(refunded.value), 550000);
  assert.match(refunded.details.join(' '), /in negative receipts from 2 contributors beyond their receipts in this window/);
  const root = nodeOf(result, 'committee:' + body.root.id);
  const into = result.links.filter(link => link.target === root.id).reduce((sum, link) => sum + link.cents, 0);
  const out = result.links.filter(link => link.source === root.id).reduce((sum, link) => sum + link.cents, 0);
  assert.equal(into, out);
  assert.equal(into, cents(body.totals.in));
  // Negative control: the same answer with the charge-back drawn as a donor ribbon (before the fix) is refused,
  // and laying it out anyway draws no ribbon for it and does not throw.
  const control = contract.negative_controls.find(item => item.why.startsWith('lane I'));
  assert.ok(flow.validate(control.payload).some(problem => problem.includes('not a positive whole-cent amount')));
  const drawn = flow.layout(control.payload, {width: 1200, height: 560});
  assert.ok(!drawn.links.some(link => link.source === 'donor:WEIRMAN ROB'));
});

test('group(): a stub merged into Other keeps no one committee\u2019s carryover or gate; each member keeps its label', () => {
  const node = (id, value, extra) => Object.assign({id, stage: 1, label: id, kind: 'committee', vin: value * 100, vout: value * 100}, extra || {});
  const nodes = [node('committee:A', 900), node('committee:B', 60, {carryover: {label: 'gate opened by 2023-2024 transfers'}}),
    node('committee:C', 40), node('donor:X', 1000, {stage: 0, kind: 'donor'})];
  nodes.push({id: 'stub:prior_cash:committee:B:B', stage: 1, label: 'Cash from before this window', kind: 'balance', stub: 'prior_cash',
    attach: 'committee:B', balance: 'prior_cash', carryover: 'gate opened by 2023-2024 transfers', vin: 0, vout: 1000});
  nodes.push({id: 'stub:before_gate:committee:C:C', stage: 1, label: 'Not counted', kind: 'balance', stub: 'before_gate',
    attach: 'committee:C', balance: 'before_gate', gate: '2025-03-01', vin: 500, vout: 0});
  const links = [{source: 'donor:X', target: 'committee:A', kind: 'receipt', cents: 90000},
    {source: 'donor:X', target: 'committee:B', kind: 'receipt', cents: 5000},
    {source: 'donor:X', target: 'committee:C', kind: 'receipt', cents: 5000},
    {source: 'stub:prior_cash:committee:B:B', target: 'committee:B', kind: 'prior_cash', cents: 1000},
    {source: 'committee:C', target: 'stub:before_gate:committee:C:C', kind: 'uncounted', cents: 500}];
  const grouped = flow.group(nodes, links, 2, 0.01);
  const other = grouped.nodes.find(item => item.bucket === 'other');
  assert.ok(other, 'B and C are grouped');
  const merged = grouped.nodes.filter(item => item.stub && item.attach === other.id);
  assert.equal(merged.length, 2);
  assert.ok(merged.every(item => !('carryover' in item) && !('gate' in item)), JSON.stringify(merged));
  assert.equal(other.members.find(member => member.id === 'committee:B').carryover, 'gate opened by 2023-2024 transfers');
  assert.ok(!('carryover' in other.members.find(member => member.id === 'committee:C')));
});

test('colours come from the theme palette only', () => {
  const palette = ['#327a7a', '#57447b', '#8b6984', '#eca91c', '#de8aab', '#7d4b00', '#3cb0a3', '#83333e', '#99973f',
    '#a09eea', '#d88766', '#509ecd'];
  const result = flow.layout(FAIRSHAKE);
  const colour = flow.colours(result, palette, '#9a938c');
  for (const node of result.nodes) assert.ok(palette.includes(colour.get(node.id)) || colour.get(node.id) === '#9a938c', node.id);
  assert.equal(colour.get('committee:C00835959'), palette[1]);
  assert.equal(colour.get('candidate:S4CA00522:oppose'), palette[7]);
  assert.equal(colour.get('candidate:S4OH00192:support'), palette[0]);
  const outcome = flow.outcomes(result, colour);
  assert.equal(outcome.get('committee:C00836221'), palette[0]);   // its largest ribbon lands on a supported candidate
});

/* ------------------------------------------------ lane F2a: small plots (FL1) */
// The payload shapes that collapsed at the boards' size (1344x760: a 780 x 420 px chart), and
// board #44's FAIRSHAKE, which drew and still must.
const SHAPES = Object.assign({}, require('./flow-shapes.corpus.js'), {fairshake2024: FAIRSHAKE});
const COLLAPSED = ['slf2022', 'slf2024', 'democrats_serve2024', 'crypto2026'];
const BOARD = {width: 780, height: 420, top: 0};
const PLOT = 420 - 34 - 10;

// What FL1 asks of a drawing: one scale above zero, bars their money times that scale, the
// fullest column's bars at least 35% of the plot, 90% of the money in ribbons 1 px wide or
// more, and labels that neither meet nor leave the drawing, with none left to the tooltip.
function smallPlotProblems(result) {
  const problems = [];
  const scale = result.scale / 100;
  if (!(scale > 0)) problems.push('the scale is zero: every bar is drawn at its 2 px floor');
  const drawn = result.nodes.filter(node => !node.stub);
  for (const node of drawn)
    if (Math.abs((node.y1 - node.y0) - Math.max(cents(node.value_in), cents(node.value_out)) * scale) > 1e-6)
      problems.push(`${node.id} is not its money times the scale`);
  const bars = result.columns.map(column => column.ids.reduce((sum, id) => sum + nodeOf(result, id).y1 - nodeOf(result, id).y0, 0));
  if (Math.max(...bars) < 0.35 * PLOT) problems.push(`the fullest column's bars take ${Math.max(...bars).toFixed(1)} of ${PLOT} px`);
  const ribbons = result.links.filter(link => nodeOf(result, link.target).stub !== 'before_gate');
  const money = ribbons.reduce((sum, link) => sum + link.cents, 0);
  const shown = ribbons.filter(link => link.width >= 1).reduce((sum, link) => sum + link.cents, 0);
  if (shown < 0.9 * money) problems.push(`${(shown / money * 100).toFixed(1)}% of the money is in ribbons 1 px wide or more`);
  for (const column of result.columns) {
    const nodes = column.ids.map(id => nodeOf(result, id)).sort((a, b) => a.label_y - b.label_y);
    for (let at = 1; at < nodes.length; at += 1)
      if (nodes[at].label_y < nodes[at - 1].label_y + nodes[at - 1].block - 0.01) problems.push(`${nodes[at].id}'s label meets the one above`);
    for (const node of nodes) if (node.label_y + node.block > result.height + 0.01) problems.push(`${node.id}'s label leaves the drawing`);
  }
  if (result.hidden_labels) problems.push(`${result.hidden_labels} label(s) left to the tooltip`);
  return problems;
}

test('FL1: at a 420 px plot every bar is to scale and the ribbons show, on the shapes that collapsed', () => {
  for (const [name, body] of Object.entries(SHAPES)) {
    const result = flow.layout(body, BOARD);
    assert.deepEqual(smallPlotProblems(result), [], name);
    assert.equal(result.height, 420, `${name}: drawn in the plot it was given`);
    // Proportional bars in one column: heights in the ratio of their money.
    const column = result.columns.find(item => item.ids.length > 2);
    const [a, b] = column.ids.map(id => nodeOf(result, id)).filter(node => node.value > 0);
    close((a.y1 - a.y0) / (b.y1 - b.y0), a.value / b.value, `${name}: ${a.label} to ${b.label}`);
    // Conservation still holds: a node's ribbons stack to its bar.
    for (const node of result.nodes.filter(item => !item.stub)) {
      const out = result.links.filter(link => link.source === node.id);
      if (out.length) close(out.reduce((sum, link) => sum + link.width, 0), node.value_out * 100 * result.scale / 100, `${name} ${node.id} out`);
    }
  }
});

test('FL1 negative control: with the bars’ floor off (labels first, the rule before 2026-09-26) the same shapes fail', () => {
  for (const name of COLLAPSED) {
    const problems = smallPlotProblems(flow.layout(SHAPES[name], Object.assign({barShare: 0}, BOARD)));
    assert.ok(problems.length, `${name}: the checks have teeth`);
  }
  for (const name of ['slf2022', 'slf2024', 'democrats_serve2024'])
    assert.equal(flow.layout(SHAPES[name], Object.assign({barShare: 0}, BOARD)).scale, 0, `${name}: the collapse, reproduced`);
});

test('FL1: labels shed detail smallest node first, and a scrollable view grows before one is dropped', () => {
  const result = flow.layout(SHAPES.slf2024, BOARD);
  const passed = result.columns[2].ids.map(id => nodeOf(result, id));
  const own = passed.find(node => node.bucket === 'own_ads');
  assert.equal(own.label_level, 0, 'a bar that holds its label keeps all of it');
  const shed = passed.filter(node => node.label_level > 0);
  assert.ok(shed.length, 'the short plot sheds some detail');
  for (const node of passed) assert.ok(node.label_level < 4, `${node.label} keeps a label`);
  // Of two labels taller than their bars, the larger node's says at least as much.
  const overflows = node => node.y1 - node.y0 < node.block;
  for (const node of passed) for (const other of passed)
    if (node.value > other.value && overflows(node) && overflows(other))
      assert.ok(node.label_level <= other.label_level, `${node.label} vs ${other.label}`);
  // Every node drawn: 40 committees in one column overfill 420 px even at one line each; grow lets it scroll.
  const tall = flow.layout(SHAPES.slf2024, {width: 780, height: 420, top: 0, grow: true});
  assert.equal(tall.height, 420, 'Top 8 needs no growth');
  const many = JSON.parse(JSON.stringify(SHAPES.slf2024));
  for (let index = 0; index < 40; index += 1) {
    many.nodes.push({id: `committee:X${index}`, stage: 2, label: `Committee number ${index}`, kind: 'committee', value_in: 1000, value_out: 0});
    many.links.push({source: 'committee:C00571703', target: `committee:X${index}`, kind: 'transfer', value: 1000, rows: 1});
  }
  const cramped = flow.layout(many, BOARD);
  assert.ok(cramped.hidden_labels > 0, 'a fixed 420 px plot must leave some labels to the tooltip');
  assert.ok(cramped.scale > 0, 'but never the bars');
  const grown = flow.layout(many, Object.assign({grow: true}, BOARD));
  assert.equal(grown.hidden_labels, 0, 'grown, every node keeps a label');
  assert.ok(grown.height > 420 && grown.height <= 420 + PLOT * 3 + 1, `grown to ${grown.height}`);
});

test('FL3/FL6: names break between words; one line under a label, every other line kept', () => {
  const result = flow.layout(SHAPES.slf2024, {width: 1231, height: 712, top: 0});
  const root = nodeOf(result, 'committee:C00571703');
  assert.equal(root.sublines.filter(line => line.startsWith('back from ')).length, 3);
  // The line drawn is the largest addition (what a node holds comes first), word for word.
  for (const node of result.nodes.filter(item => !item.stub && item.sublines.length)) {
    assert.ok(node.sublines.includes(node.subline), node.id);
    if ((node.held || 0) > 0) continue;
    const amounts = result.stubs.filter(stub => stub.attach === node.id)
      .filter(stub => node.sublines.includes(`${stub.label} ${flow.short(stub.value)}`)).map(stub => stub.value);
    if (amounts.length) assert.ok(node.subline.endsWith(flow.short(Math.max(...amounts))), `${node.id}: ${node.subline}`);
  }
  const crypto = flow.layout(SHAPES.crypto2026, BOARD);
  const slfPac = crypto.nodes.find(node => node.label === 'SLF PAC');
  assert.ok(slfPac.sublines.some(line => /^back from /.test(line)), 'it has a small loop line too');
  assert.match(slfPac.subline, /^Other donors \$251\.6M$/, 'the largest addition, not the first listed');
  assert.match(root.subline, /^back from American Crossroads \$1\.3M$/);
  for (const node of result.nodes.filter(item => !item.stub)) {
    assert.ok(!node.subline_shown || node.subline_shown === node.subline, node.id);
    const words = (node.kind === 'candidate' && !node.outcome && node.direction ? `${node.label} · ${node.direction}` : node.label).split(/\s+/);
    for (const line of node.label_text) for (const word of line.split(/\s+/)) assert.ok(words.includes(word), `${node.id}: "${word}" is a whole word`);
  }
  // A name longer than its lines keeps its words: the rest joins the last line (the renderer adds the ellipsis).
  const long = JSON.parse(JSON.stringify(FIXTURE));
  long.nodes.find(node => node.id === 'donor:ACME CORP').label = 'The Extraordinarily Long Named Political Action Committee For Testing';
  const cut = flow.layout(long, {width: 700, height: 560}).nodes.find(node => node.id === 'donor:ACME CORP');
  assert.equal(cut.label_text.length, 2);
  assert.equal(cut.label_text.join(' '), 'The Extraordinarily Long Named Political Action Committee For Testing');
});
