/* The reconciliation strip's cycle scope.

   Graph's first paint sends latest=1, and the server answers it by pinning the newest
   cycle it holds (graph_data.py: selected['cycle'] = max(years)). A Studio sheet with
   no cycle filter used to ask for every cycle at once and then print "All cycles" as
   though that were what Graph had on screen. On a one-cycle report the two numbers
   happened to match, so it looked right; on a report with more than one cycle it
   quoted a total nobody was looking at.

   So the strip now asks the same question, and when the two totals part because of it
   the difference is named rather than left as "unexplained - report it", which is the
   worst possible thing to tell a reader about a number that has a plain explanation. */

// Run this suite with the glob form; Node 24 rejects a bare directory:
//   node --test "mentat/app/tests/**/*.test.js"
const test = require('node:test');
const assert = require('node:assert');
const contributions = require('../chart-studio-contributions.js');

const studio = cents => ({totals: {amount: cents}, provenance: {workbook_sha256: 'a'.repeat(64)},
  reconciliation: {aggregate_filters_applied: false}});
const graph = (dollars, cycle) => ({totals: {giving: dollars},
  filters: {cycle: cycle || '', company: '', tier: ''},
  reports: [{sha256: 'a'.repeat(64)}]});
const scope = extra => Object.assign({tier: '', funding: 'all'}, extra);

test('a one-cycle report still agrees, and the pinned cycle raises no reason at all', () => {
  // The whole report is one cycle, so Graph pinning it changes nothing.
  const state = contributions.reconcile(studio(24994948317), 'receipt',
    graph(249949483.17, '2026'), scope({studioCycle: '', graphCycle: '2026'}));
  assert.strictEqual(state.agree, true);
  assert.strictEqual(state.reasonId, '');
  assert.strictEqual(state.differenceCents, 0);
});

test('when Graph pinned one cycle and the sheet covers them all, that is the reason', () => {
  const state = contributions.reconcile(studio(24994948317), 'receipt',
    graph(100000000.00, '2026'), scope({studioCycle: '', graphCycle: '2026'}));
  assert.strictEqual(state.agree, false);
  assert.strictEqual(state.reasonId, 'cycle');
  assert.match(state.reason, /shows one election cycle/);
  assert.match(state.reason, /this sheet shows all of them/);
  // The reader must never be told a plainly explicable gap is a mystery.
  assert.notStrictEqual(state.reasonId, 'unexplained');
});

test('a sheet with its own cycle is never explained away by the cycle reason', () => {
  const state = contributions.reconcile(studio(24994948317), 'receipt',
    graph(100000000.00, '2026'), scope({studioCycle: '2026', graphCycle: '2026'}));
  assert.strictEqual(state.reasonId, 'unexplained');
});

test('the basis outranks the cycle, because a wrong basis explains more', () => {
  const state = contributions.reconcile(studio(24994948317), 'original',
    graph(100000000.00, '2026'), scope({studioCycle: '', graphCycle: '2026'}));
  assert.strictEqual(state.reasonId, 'basis');
});

test('the cycle outranks a Graph filter, since the pinned cycle is the bigger cut', () => {
  const state = contributions.reconcile(studio(24994948317), 'receipt',
    graph(100000000.00, '2026'), scope({studioCycle: '', graphCycle: '2026', tier: 'Executive'}));
  assert.strictEqual(state.reasonId, 'cycle');
});

test('no pinned cycle at all leaves the older reasons exactly as they were', () => {
  const state = contributions.reconcile(studio(24994948317), 'receipt',
    graph(100000000.00, ''), scope({studioCycle: '', graphCycle: '', tier: 'Executive'}));
  assert.strictEqual(state.reasonId, 'graph_filter');
});

test('every reason id the chain can produce has wording to print', () => {
  for (const id of ['basis', 'cycle', 'graph_filter', 'workbook', 'aggregate',
                    'unavailable', 'unexplained']) {
    assert.strictEqual(typeof contributions.RECONCILE_REASONS[id], 'string', id);
    assert.ok(contributions.RECONCILE_REASONS[id].length > 0, id);
  }
});
