'use strict';
/* Lane Z (2026-10-15): the arrangement a Flow view makes before it draws (small transfers folded, a
   group unfolded in place), the chain behind a pin, the zoom path, one read per query and the unnamed
   committee's FEC ID. None of it may change a dollar: every test below holds the column totals and runs
   the client conservation validator on what would be drawn. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const layout = require('../flow-layout.js');
const chart = require('../flow-chart.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const contract = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'flow.json'), 'utf8'));
const FIXTURE = contract.examples.find(item => item.name.startsWith('a committee flow')).body;
const FAIRSHAKE = contract.examples.find(item => item.name.startsWith('FAIRSHAKE 2023-2024')).body;
const clone = value => JSON.parse(JSON.stringify(value));
const cents = value => Math.round(value * 100);

// The column totals, in cents, of what a payload draws.
const totals = payload => layout.layout(payload, {width: 1100, height: 520}).columns.map(column => cents(column.total));

// The synthetic committee flow with extra committees in the "Passed to" column: three at a few thousand
// dollars, two at nothing, and one big enough to stay named. ROOT FUND pays each one, out of what it
// would have kept (its balance still balances).
function withSmallTransfers() {
  const body = clone(FIXTURE);
  const root = 'committee:C90000001';
  const paid = {A: 1000, B: 2500, C: 4000, BIG: 60000};
  let total = 0;
  for (const [name, value] of Object.entries(paid)) {
    const id = `committee:C900001${name}`;
    body.nodes.push({id, stage: 2, kind: 'committee', label: `Small ${name}`, committee_id: id.slice(10), value_in: value, value_out: 0});
    body.links.push({source: root, target: id, value, kind: 'transfer', rows: 1, committee: 'C90000001'});
    total += value;
  }
  for (const name of ['ZERO1', 'ZERO2'])
    body.nodes.push({id: `committee:C90000200${name}`, stage: 2, kind: 'committee', label: `Nothing ${name}`, committee_id: `C90000200${name}`,
      value_in: 0, value_out: 0});
  const unspent = body.links.find(link => link.target === 'balance:unspent:2');
  unspent.value -= total;
  body.nodes.find(node => node.id === 'balance:unspent:2').value_in -= total;
  return body;
}

test('small transfers fold into one node per column, and not a dollar moves', () => {
  const body = withSmallTransfers();
  assert.deepEqual(layout.validate(body), [], 'the fixture itself conserves');
  const made = layout.arrange(body, {small: true});
  const small = made.payload.nodes.filter(node => node.bucket === 'small');
  assert.equal(small.length, 1, 'one small-transfers node for the column');
  assert.equal(small[0].stage, 2);
  assert.equal(small[0].member_count, 5, 'three small committees and the two at nothing');
  assert.equal(small[0].label, 'Small transfers (5)');
  assert.equal(small[0].value_in, 7500, 'its total is the members’ total');
  assert.ok(made.payload.nodes.some(node => node.id === 'committee:C900001BIG'), 'a committee above the floor stays named');
  assert.ok(!made.payload.nodes.some(node => /Nothing ZERO/.test(node.label)), 'no node at nothing is left drawn');
  assert.deepEqual(totals(made.payload), totals(body), 'every column keeps its total');
  assert.deepEqual(layout.validate(made.payload), [], 'and the conservation validator still passes');
  // The members are in the node, largest first, for the tooltip.
  assert.deepEqual(small[0].members.map(member => member.value), [4000, 2500, 1000, 0, 0]);
  const said = chart.describe({node: layout.layout(made.payload).nodes.find(node => node.id === small[0].id)},
    {result: layout.layout(made.payload), format: {short: chart.short, full: chart.full}}, made.payload).lines;
  assert.ok(said.includes('5 grouped:') && said.some(line => line.startsWith('Small C ')), said.join(' | '));
});

test('a small bucket unfolds into its top members and a smaller residual, with the same totals', () => {
  const body = withSmallTransfers();
  const made = layout.arrange(body, {small: true, open: {'small:2': 2}});
  const shown = made.payload.nodes.filter(node => node.group === 'small:2' && node.kind === 'committee');
  assert.deepEqual(shown.map(node => node.label).sort(), ['Small B', 'Small C']);
  const residual = made.payload.nodes.find(node => node.bucket === 'small');
  assert.equal(residual.member_count, 3);
  assert.deepEqual(made.groups.map(group => [group.id, group.residual]), [['small:2', true]]);
  assert.deepEqual(totals(made.payload), totals(body));
  assert.deepEqual(layout.validate(made.payload), []);
  // Asking for more than there is shows them all and leaves no bucket behind.
  const all = layout.arrange(body, {small: true, open: {'small:2': 50}});
  assert.ok(!all.payload.nodes.some(node => node.bucket === 'small'));
  assert.deepEqual(totals(all.payload), totals(body));
  assert.deepEqual(layout.validate(all.payload), []);
});

// The grouped answer a server gives at Top N, from the ungrouped one: group() on the same rule.
function grouped(full, top) {
  const made = layout.layout(full, {top, width: 1100, height: 520});
  return Object.assign({}, full, {nodes: made.nodes.map(node => Object.assign({}, node)), links: made.links.map(link => Object.assign({}, link))});
}

test('unfolding an Other keeps every column’s total and its links, from the top=0 answer', () => {
  const full = clone(FIXTURE);
  const answer = grouped(full, 3);
  const other = answer.nodes.find(node => node.id === 'other:0');
  assert.ok(other && other.member_count >= 2, 'the grouped answer has an Other to unfold');
  assert.deepEqual(layout.validate(layout.arrange(answer, {}).payload), []);
  for (const count of [1, 2, 9]) {
    const made = layout.arrange(answer, {open: {'other:0': count}, full});
    assert.deepEqual(made.refused, {}, `unfolded at ${count}`);
    assert.deepEqual(totals(made.payload), totals(answer));
    assert.deepEqual(layout.validate(made.payload), []);
    const doe = made.payload.nodes.find(node => node.id === 'donor:DOE JANE');
    if (doe) assert.equal(made.payload.links.find(link => link.source === doe.id).value, 500000, 'a member’s own link, not a share of the group’s');
  }
  // Nothing to unfold with until the full answer is asked for.
  assert.deepEqual(layout.arrange(answer, {open: {'other:0': 2}}).needs, ['other:0']);
});

test('negative control: members whose links do not add up to the group are refused, not drawn', () => {
  const full = clone(FIXTURE);
  const answer = grouped(full, 3);
  const wrong = clone(full);
  wrong.links.find(link => link.source === 'donor:DOE JANE').value += 1;       // one dollar the group does not have
  const made = layout.arrange(answer, {open: {'other:0': 2}, full: wrong});
  assert.ok(made.refused['other:0'], 'refused');
  assert.ok(!made.payload.nodes.some(node => node.group === 'other:0'), 'and the group stays folded');
  assert.deepEqual(totals(made.payload), totals(answer));
});

test('a real answer: the FAIRSHAKE example folds and validates, and its totals do not move', () => {
  const made = layout.arrange(FAIRSHAKE, {small: true});
  assert.deepEqual(layout.validate(made.payload), []);
  assert.deepEqual(totals(made.payload), totals(FAIRSHAKE));
});

test('an unnamed committee shows its FEC ID as the small line, and the tooltip carries it', () => {
  const body = clone(FIXTURE);
  const node = body.nodes.find(item => item.id === 'committee:C90000002');
  Object.assign(node, {label: 'Unnamed committee', name_as_filed: 'C90000002', unnamed: true});
  const result = layout.layout(body, {width: 1100, height: 520});
  const drawn = result.nodes.find(item => item.id === node.id);
  assert.ok(drawn.sublines.includes('C90000002'));
  assert.equal(drawn.subline, 'C90000002');
  const svg = chart.svgString(body, {width: 1100, height: 520});
  assert.ok(svg.includes('>Unnamed committee</text>') && svg.includes('>C90000002</text>'));
  const said = chart.describe({node: drawn}, {result, format: {short: chart.short, full: chart.full}}, body).lines;
  assert.ok(said.includes('As filed: C90000002'), said.join(' | '));      // the ID is on the drawing and in the tooltip
});

test('a pin’s chain shows each step’s own amount, and never crosses the root’s pool', () => {
  const result = layout.layout(FAIRSHAKE, {width: 1100, height: 520});
  const pool = result.nodes.find(node => node.root && !node.stub);
  const candidate = result.nodes.find(node => node.kind === 'candidate' && result.links.some(link => link.target === node.id));
  const hops = chart.chainOf(result, candidate.id);
  assert.equal(hops[0].node.id, pool.id, 'a candidate’s chain begins at the pool that paid it, not at a donor');
  assert.ok(hops.every(hop => hop.node.kind !== 'donor'), 'no donor is on a candidate’s chain');
  assert.equal(hops[hops.length - 1].node.id, candidate.id);
  hops.forEach((hop, at) => {
    if (!at) return assert.equal(hop.via, null);
    const same = result.links.filter(link => link.source === hops[at - 1].node.id && link.target === hop.node.id);
    assert.ok(same.some(link => link.id === hop.via.id), 'each step is one real link');
  });
  const donor = result.nodes.find(node => node.kind === 'donor' && !node.stub);
  const out = chart.chainOf(result, donor.id);
  assert.equal(out[out.length - 1].node.id, pool.id, 'a donor’s chain ends at the pool it paid');
});

test('the zoom path: a step is a level to return to; going back or jumping drops the levels after it', () => {
  const trail = chart.trail();
  trail.push({label: 'Banking'}); trail.push({label: 'ActBlue'}); trail.push({label: 'DCCC'});
  assert.deepEqual(trail.labels('NRSC'), ['Banking', 'ActBlue', 'DCCC', 'NRSC']);
  assert.equal(trail.back().label, 'DCCC');
  assert.equal(trail.jump(0).label, 'Banking');
  assert.equal(trail.depth(), 0);
});

test('one read per query: concurrent asks share it, a good answer is kept, a failure is not', async () => {
  let calls = 0;
  const fetcher = async url => { calls += 1; return {ok: !url.includes('bad'), status: url.includes('bad') ? 400 : 200, json: async () => ({url})}; };
  const [a, b] = await Promise.all([chart.read('/api/flow?x=1', fetcher), chart.read('/api/flow?x=1', fetcher)]);
  assert.equal(calls, 1);
  assert.equal(a, b);
  await chart.read('/api/flow?x=1', fetcher);
  assert.equal(calls, 1, 'kept');
  await chart.read('/api/flow?bad=1', fetcher);
  await chart.read('/api/flow?bad=1', fetcher);
  assert.equal(calls, 3, 'a refusal is asked again');
});

test('the gestures are in the tooltip, and an onward marker is drawn only for the views that follow it', () => {
  const result = layout.layout(FIXTURE, {width: 1100, height: 520});
  const context = {result, format: {short: chart.short, full: chart.full}};
  const bridge = result.nodes.find(node => node.id === 'committee:C90000002');
  const hints = {zoom: true, rows: true};
  assert.equal(chart.describe({node: bridge}, context, FIXTURE, hints).hint, 'Click to zoom in · Shift-click to pin');
  assert.equal(chart.describe({node: bridge}, context, FIXTURE).hint, '', 'no gestures promised where the view has none');
  const candidate = result.nodes.find(node => node.kind === 'candidate');
  assert.equal(chart.describe({node: candidate}, context, FIXTURE, hints).hint, 'Click to open in Rows · Shift-click to pin');
  const bucket = layout.layout(grouped(clone(FIXTURE), 3), {width: 1100, height: 520}).nodes.find(node => node.id === 'other:0');
  assert.equal(chart.describe({node: bucket}, context, FIXTURE, hints).hint, 'Click to expand · Shift-click to pin');
  const plain = chart.svgString(FIXTURE, {width: 1100, height: 520});
  const explore = chart.svgString(FIXTURE, {width: 1100, height: 520, explore: true});
  assert.ok(!plain.includes('flow-onward'), 'Chart Studio’s flow draws no marker');
  assert.ok(explore.includes('data-onward="committee:C90000002"') && explore.includes('→ 1 onward'));
});

/* ------------------------------------------------------------- fix round (2026-10-15, after the review) */
const CRYPTO = contract.examples.find(item => item.name.startsWith('Crypto 2023-2024')).body;
const FAIRSHAKE_ID = 'committee:C00835959', DAJ_ID = 'committee:C00836221';

test('R1: the answer is checked as it arrived - a cent off and a dangling link draw as received, with the problems counted', () => {
  const body = withSmallTransfers();
  const broken = clone(body);
  broken.nodes.find(node => node.id === 'committee:C900001BIG').value_in += 0.01;      // a cent its links do not carry
  broken.links.push({source: 'committee:C90000001', target: 'committee:C9999NOWHERE', value: 5, kind: 'transfer', rows: 1,
    committee: 'C90000001'});                                                          // and a ribbon to nowhere
  // The hole: arrange() drops the dangling link and re-adds every node's totals, so what it returns validates clean.
  assert.deepEqual(layout.validate(layout.arrange(broken, {small: true}).payload), []);
  const raw = layout.validate(broken);
  assert.ok(raw.length >= 2 && raw.some(item => /does not exist/.test(item)) && raw.some(item => /not the sums/.test(item)), raw.join(' | '));
  const settings = {width: 1100, height: 520, arrange: {small: true}};
  const drawn = chart.prepare(broken, settings);
  assert.equal(drawn.problems.length, raw.length, 'the notice names the count of the answer as it arrived');
  assert.equal(drawn.payload, broken, 'drawn as received');
  assert.deepEqual(drawn.groups, []);
  assert.ok(!drawn.result.nodes.some(node => node.bucket === 'small'), 'nothing is folded');
  // The same answer without the faults folds, with no problem.
  const fine = chart.prepare(body, settings);
  assert.deepEqual(fine.problems, []);
  assert.ok(fine.result.nodes.some(node => node.bucket === 'small'));
  // A view with no arrangement (Chart Studio) is not checked here.
  assert.deepEqual(chart.prepare(broken, {width: 1100, height: 520}).problems, []);
});

test('R1: an arrangement that breaks conservation is caught after it, and the answer is drawn as received', () => {
  const body = withSmallTransfers();
  const real = layout.arrange;
  layout.arrange = (payload, spec) => {
    const made = real(payload, spec);
    made.payload.nodes[0].value_in += 0.01;
    return made;
  };
  try {
    const drawn = chart.prepare(body, {width: 1100, height: 520, arrange: {small: true}});
    assert.equal(drawn.problems.length, 1);
    assert.equal(drawn.payload, body);
    assert.ok(!drawn.result.nodes.some(node => node.bucket === 'small'));
  } finally { layout.arrange = real; }
});

test('R2/R3: a read replaces the one before it, and a cancelled one writes nothing', t => {
  t.mock.timers.enable({apis: ['setInterval', 'Date']});
  let line = '';
  const set = text => { line = text; };
  const loader = chart.loader();
  const cold = loader.begin(set, 'Reading the flow');
  assert.equal(line, 'Reading the flow…');
  t.mock.timers.tick(3000);
  assert.equal(line, 'Reading the flow… 3 s');
  // Top changes while the cold read is still in flight: a new read begins.
  const next = loader.begin(set, 'Reading the flow');
  assert.equal(cold.live(), false);
  assert.equal(next.live(), true);
  t.mock.timers.tick(2000);
  assert.equal(line, 'Reading the flow… 2 s', 'only the new read ticks; the old interval is gone');
  // The new answer draws and the line is set to what it says (an offer, say); no timer may write over it.
  next.end();
  line = 'Attributed estimate is not drawn yet. [Show reported activity]';
  cold.end();                                                      // the old read finishing late stops nothing of the new one's
  t.mock.timers.tick(12000);
  assert.equal(line, 'Attributed estimate is not drawn yet. [Show reported activity]');
  // Back or Escape: cancel() ends the read in flight, timer and answer.
  const pending = loader.begin(set, 'Opening FAIRSHAKE');
  loader.cancel();
  line = '';
  t.mock.timers.tick(5000);
  assert.equal(line, '');
  assert.equal(pending.live(), false, 'a drill resolving after Escape is dropped, so it pushes no level');
});

test('R4: the pooling committees are the ones that take money from more than one source', () => {
  const result = layout.layout(CRYPTO, {width: 1100, height: 520});
  assert.deepEqual([...chart.pools(result)].sort(), ['committee:C00835959', 'committee:C00836221', 'committee:C00848440', 'committee:C00867937']);
  assert.ok(!chart.pools(result).has('committee:C00669259'), 'FF PAC takes in from one company only');
});

test('R4: a pinned company is in its colour up to and into a pooling committee, and never through one', () => {
  const result = layout.layout(CRYPTO, {width: 1100, height: 520});
  const pools = chart.pools(result);
  const linkOf = id => result.links.find(item => item.id === id);
  const donors = result.nodes.filter(node => node.kind === 'donor' && !node.stub);
  assert.ok(donors.length >= 2);
  for (const donor of donors) {
    const {kinds} = chart.pinRoute(result, donor.id);
    const tracedLinks = [...kinds].filter(([, kind]) => kind === 'traced').map(([id]) => linkOf(id));
    assert.ok(tracedLinks.length, donor.label);
    for (const pool of pools) {
      const into = tracedLinks.filter(item => item.target === pool), out = tracedLinks.filter(item => item.source === pool);
      assert.ok(!(into.length && out.length), `${donor.label}: one pin-coloured chain runs through ${pool}`);
      assert.deepEqual(out.map(item => item.id), [], `${donor.label}: a pin-coloured ribbon leaves ${pool}`);
    }
    // what a pooling committee spends is drawn, as pooled
    for (const [id, kind] of kinds) if (pools.has(linkOf(id).source)) assert.equal(kind, 'pooled', id);
  }
  const coinbase = chart.pinRoute(result, 'company:Coinbase').kinds;
  assert.equal(coinbase.get('company:Coinbase→committee:C00835959→receipt'), 'traced');
  assert.equal(coinbase.get('committee:C00835959→committee:C00836221→transfer'), 'pooled');
  assert.equal(coinbase.get('committee:C00836221→outcome:support:R→ie_support'), 'pooled');
  assert.equal(coinbase.get('company:Coinbase→committee:C00669259→receipt'), 'traced', 'a committee with one source is not a pool');
  assert.equal(coinbase.get('committee:C00669259→balance:untraced:2→untraced'), 'traced');
});

test('R4: the panel splits at the pool - the donor’s block holds the donor’s step and the pool’s own total, nothing downstream', () => {
  const result = layout.layout(CRYPTO, {width: 1100, height: 520});
  const {blocks} = chart.pinBlocks(result, 'company:Coinbase');
  assert.deepEqual(blocks.map(block => block.kind), ['traced', 'pooled']);
  const [donor, pooled] = blocks;
  assert.equal(donor.head, 'Into FAIRSHAKE from Coinbase: $69.5M');
  assert.equal(pooled.head, "FAIRSHAKE's spending from its pool (all donors)");
  // the donor's block: Coinbase, the one step into FAIRSHAKE, FAIRSHAKE with its own total (not Coinbase's share of it)
  assert.deepEqual(donor.rows.map(row => row.kind), ['node', 'step', 'node']);
  assert.equal(donor.rows[1].amount, 69506174.52);
  assert.equal(donor.rows[2].node.id, FAIRSHAKE_ID);
  assert.equal(donor.rows[2].pool, true);
  assert.equal(donor.rows[2].amount, result.nodes.find(node => node.id === FAIRSHAKE_ID).value);
  assert.notEqual(donor.rows[2].amount, donor.rows[1].amount);
  const downstream = new Set(result.links.filter(item => item.source === FAIRSHAKE_ID).map(item => item.value));
  for (const row of donor.rows) assert.ok(!downstream.has(row.amount), `a downstream amount (${row.amount}) is in the donor's block`);
  // the pool's block lists each onward hop's own amount
  assert.ok(pooled.rows[0].kind === 'step' && downstream.has(pooled.rows[0].amount));
  // a company that reaches another pool is split the same way
  const gemini = chart.pinBlocks(result, 'company:Gemini').blocks;
  assert.equal(gemini[0].head, 'Into Trump 47 Committee from Gemini: $6.9M');
  assert.equal(gemini[1].head, "Trump 47 Committee's spending from its pool (all donors)");
});

test('R4: a pin on an outcome or a committee gets the same split where its route enters a pool upstream', () => {
  const result = layout.layout(CRYPTO, {width: 1100, height: 520});
  const outcome = chart.pinBlocks(result, 'outcome:support:R').blocks;
  assert.deepEqual(outcome.map(block => block.kind), ['pooled', 'traced']);
  assert.equal(outcome[0].head, 'What reached Defend American Jobs (pooled, all donors)');
  assert.equal(outcome[1].head, 'From Defend American Jobs into Support Republicans: $56.3M');
  assert.deepEqual(outcome[1].rows.map(row => row.kind), ['step', 'node'], 'the pin’s own block: the step into it and it');
  const kinds = chart.pinRoute(result, 'outcome:support:R').kinds;
  assert.equal(kinds.get('company:Coinbase→committee:C00835959→receipt'), 'pooled');
  assert.equal(kinds.get('committee:C00836221→outcome:support:R→ie_support'), 'traced');
  const middle = chart.pinBlocks(result, DAJ_ID).blocks;
  assert.deepEqual(middle.map(block => block.kind), ['pooled', 'traced']);
  assert.equal(middle[0].head, 'What reached FAIRSHAKE (pooled, all donors)');
  // each hop still shows its own step amount
  assert.deepEqual(middle[1].rows.filter(row => row.kind === 'step').map(row => row.amount), [54550000, 56278158.68]);
});

test('R4: in a committee’s own flow a pin ends at the root’s pool, as before, with no pooled block', () => {
  const result = layout.layout(FAIRSHAKE, {width: 1100, height: 520});
  const pool = result.nodes.find(node => node.root && !node.stub);
  const candidate = result.nodes.find(node => node.kind === 'candidate' && result.links.some(item => item.target === node.id));
  const {blocks} = chart.pinBlocks(result, candidate.id);
  assert.deepEqual(blocks.map(block => block.kind), ['traced']);
  assert.equal(blocks[0].rows[0].node.id, pool.id);
  assert.equal(blocks[0].rows[0].pool, true);
  assert.equal(blocks[0].rows[0].amount, pool.value, 'the pool shows its own total');
});

test('R5: a master-sourced name says which year’s file it is; a filed name says "As filed"', () => {
  const result = layout.layout(FIXTURE, {width: 1100, height: 520});
  const context = {result, format: {short: chart.short, full: chart.full}};
  const node = Object.assign({}, result.nodes.find(item => item.id === 'committee:C90000002'), {name_as_filed: 'TIPPINGPOINT 2020'});
  const said = extra => chart.describe({node: Object.assign({}, node, extra)}, context, FIXTURE).lines;
  assert.ok(said({}).includes('As filed: TIPPINGPOINT 2020'));
  assert.ok(said({name_master: '2022'}).includes('Name from FEC committee master, 2022: TIPPINGPOINT 2020'));
  assert.ok(!said({name_master: '2022'}).some(line => line.startsWith('As filed')));
});

test('R6: the focused or pinned committee is never folded into Small transfers', () => {
  const body = withSmallTransfers();
  const kept = 'committee:C900001A';
  const folded = layout.arrange(body, {small: true});
  assert.ok(!folded.payload.nodes.some(node => node.id === kept));
  const made = layout.arrange(body, {small: true, keep: [kept, 'committee:C90000200ZERO1']});
  assert.ok(made.payload.nodes.some(node => node.id === kept), 'the focus is still drawn');
  assert.ok(made.payload.nodes.some(node => node.id === 'committee:C90000200ZERO1'), 'so is a pinned node at nothing');
  assert.equal(made.payload.nodes.find(node => node.bucket === 'small').member_count, 3);
  assert.deepEqual(totals(made.payload), totals(body));
  assert.deepEqual(layout.validate(made.payload), []);
  const lit = chart.traced(layout.layout(made.payload, {width: 1100, height: 520}), kept);
  assert.ok(lit.nodes.size > 0 && lit.links.size > 0, 'a trace of it finds its route');
});

test('R8: the zoom strip draws no column headers of its own, so they cannot meet the chart’s', () => {
  const before = layout.arrange(FIXTURE, {small: true}).payload;
  const svg = chart.svgString(FIXTURE, {width: 1100, height: 520, headers: true, stripWidth: 150,
    context: {payload: before, nodeId: 'committee:C90000002', label: 'Root Fund'}});
  assert.ok(svg.includes('class="flow-context"') && svg.includes('flow-ctx-bar'), 'the strip is drawn');
  assert.ok(!svg.includes('flow-ctx-header'));
  assert.equal((svg.match(/class="flow-header"/g) || []).length + (svg.match(/class="flow-header-cut"/g) || []).length,
    layout.layout(FIXTURE, {width: 1100 - 150, height: 520}).columns.length, 'each column of the chart keeps its one header');
});
