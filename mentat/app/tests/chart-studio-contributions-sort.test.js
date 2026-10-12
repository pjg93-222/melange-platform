/* What the Sort and Show-mark-labels controls claim, asserted without a browser.

   Owner: "Sort mark labels, sort ascending and descending don't do anything."
   The controls were wired; what they claimed was wrong. Three separate facts
   are pinned here, because each was a different way of being silently inert:

   1. sheet().sort opens at 'none' and this path reads anything but 'asc' as
      descending, so from the opening state "Sort descending" produced a
      byte-identical query. 'none' MEANS descending; effectiveSort() says so
      once, for the compile and the toolbar alike.
   2. The toolbar used to decide "locked" from the CLASSIC compile, which does
      not know combination, dual-line or histogram - so on those three the
      buttons stayed enabled and changed nothing. sortLock() is the one answer.
   3. spec.labels is honoured by some renderers and silently ignored by the
      rest. LABEL_SUPPORT names every producible mark and, where it is not
      supported, the true reason the button shows.

   Run with the glob form; Node 24 rejects a bare directory:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/) */
const test = require('node:test');
const assert = require('node:assert/strict');
const studio = require('../chart-studio-contributions.js');
const core = require('../chart-studio-core.js');

const catalog = [
  ...['company', 'cycle', 'date', 'year', 'zip5', 'state', 'recipient']
    .map(id => ({id, label: id, role: 'dimension'})),
  ...['amount', 'records', 'contributors', 'average_amount', 'gross_amount']
    .map(id => ({id, label: id, role: 'measure',
      unit: id === 'records' || id === 'contributors' ? 'count' : 'cents'}))];
const columns = studio.fields(catalog);
const index = id => columns.find(f => f.id === id).index;
const pill = (id, aggregation = 'sum') => ({column: index(id), aggregation});

function sheet() {
  const s = core.createSheet('A');
  s.columns = [pill('company')];
  s.rows = [pill('amount')];
  s.marks.type = 'bar';
  return s;
}
const compile = s => studio.compile(s, columns, {dataset: 'Banking', basis: 'receipt'});
// Paul, 2026-09-23, quoted by every test below that changed with his words:
// "Additionally, within the Chart Studio make sure everything is freely
// flexible, that all graph types properly register and nothing is logically
// constrained, it needs to be utterly maluable."
const PAUL = 'nothing is logically constrained, it needs to be utterly maluable';

/* ------------------------------------------------------------------- sort */

test('[studio-sort] a stored sort of none means descending, and only asc means ascending', () => {
  assert.equal(core.createSheet('A').sort, 'none', 'a new sheet opens at none');
  for (const stored of ['none', 'desc', '', null, undefined, 'nonsense'])
    assert.equal(studio.effectiveSort({sort: stored}), 'desc',
      JSON.stringify(stored) + ' is descending');
  assert.equal(studio.effectiveSort({sort: 'asc'}), 'asc');
  assert.equal(studio.effectiveSort({}), 'desc', 'a sheet with no sort at all');
  assert.equal(studio.effectiveSort(null), 'desc', 'and no sheet at all');
});

// Paul: "nothing is logically constrained". No mark refuses to be sorted any
// more (census B-S16): sortLock is retired and answers "no lock" for every
// view, time views and every transform included.
test('[studio-sort] no view is locked against sorting', () => {
  for (const type of ['line', 'area', 'combination', 'dual-line', 'histogram', 'bar', 'scatter', 'table', 'stacked', 'heatmap', 'treemap', 'box'])
    assert.equal(studio.sortLock(type, {}), '', type + ' sorts: ' + PAUL);
  for (const transform of ['annual', 'yoy', 'change', 'forecast'])
    assert.equal(studio.sortLock('bar', {analytics: {transform}}), '', transform + ' sorts too');
});

// What each stored order asks the server for (census B-S16): 'none' is the
// view's own order - chronological on a time axis or a histogram's bins,
// largest first otherwise - asc/desc order by the first measure on every
// mark, and az/za by the first dimension's label.
test('[studio-sort] querySort states the order for every stored value', () => {
  const q = (sort, dims, measures = ['amount']) => studio.querySort({sort}, dims, measures);
  assert.deepEqual(q('none', ['cycle']), [{field: 'cycle', direction: 'asc'}]);
  assert.deepEqual(q('none', ['amount_bin'], ['records']), [{field: 'amount_bin', direction: 'asc'}]);
  assert.deepEqual(q('none', ['company']), [{field: 'amount', direction: 'desc'}]);
  assert.deepEqual(q('asc', ['cycle']), [{field: 'amount', direction: 'asc'}]);
  assert.deepEqual(q('desc', ['cycle']), [{field: 'amount', direction: 'desc'}]);
  assert.deepEqual(q('az', ['company']), [{field: 'company', direction: 'asc'}]);
  assert.deepEqual(q('za', ['company']), [{field: 'company', direction: 'desc'}]);
  assert.deepEqual(q('az', []), [{field: 'amount', direction: 'desc'}], 'no dimension, nothing to spell');
  assert.equal(studio.orderedAxis(['year', 'company']), true);
  assert.equal(studio.orderedAxis(['company', 'year']), false);
});

test('[studio-sort] a bar orders by the first measure: none and desc descend, asc ascends', () => {
  for (const stored of ['none', 'desc', undefined]) {
    const s = sheet(); s.sort = stored;
    assert.deepEqual(compile(s).query.sort, [{field: 'amount', direction: 'desc'}],
      JSON.stringify(stored) + ' must produce the descending query');
  }
  const ascending = sheet(); ascending.sort = 'asc';
  assert.deepEqual(compile(ascending).query.sort, [{field: 'amount', direction: 'asc'}]);
  // measures[0] only - the second measure is not a tie-break, and the button
  // titles say which measure the order is by for exactly this reason.
  const two = sheet(); two.rows = [pill('contributors'), pill('amount')];
  assert.deepEqual(compile(two).query.sort, [{field: 'contributors', direction: 'desc'}]);
});

// Paul: "nothing is logically constrained". A time view (and a histogram's
// bins) opens in its own order and sorts by its measure when asked; a time
// axis ordered by a measure is drawn in that order, not as elapsed time
// (contract V01, spec.categorical).
test('[studio-sort] a time view opens chronological and sorts by its measure when asked', () => {
  const time = () => { const s = sheet(); s.columns = [pill('cycle')]; return s; };
  const cases = [
    ['line', time, 'cycle', 'amount'],
    ['area', time, 'cycle', 'amount'],
    ['combination', () => { const s = time(); s.rows = [pill('amount'), pill('contributors')]; return s; }, 'cycle', 'amount'],
    ['dual-line', () => { const s = time(); s.rows = [pill('amount'), pill('contributors')]; return s; }, 'cycle', 'amount'],
    ['histogram', sheet, 'amount_bin', 'records']];
  for (const [type, build, axis, measure] of cases) {
    const opening = build(); opening.marks.type = type; opening.sort = 'none';
    const opened = compile(opening);
    assert.equal(opened.spec.type, type, type + ' compiled to ' + opened.spec.type);
    assert.deepEqual(opened.query.sort, [{field: axis, direction: 'asc'}], type + ' opens in its axis order');
    assert.ok(!opened.spec.categorical, type + ' opens on real elapsed time');
    for (const stored of ['asc', 'desc']) {
      const s = build(); s.marks.type = type; s.sort = stored;
      const result = compile(s);
      assert.deepEqual(result.query.sort, [{field: measure, direction: stored}], type + ' sorts ' + stored + ': ' + PAUL);
      if (type !== 'histogram') assert.equal(result.spec.categorical, true, type + ' is drawn in the sorted order');
    }
  }
  for (const stored of ['asc', 'desc']) {
    const s = sheet(); s.columns = [pill('cycle')]; s.sort = stored;
    s.analytics = {...s.analytics, transform: 'annual', period: 'cycle'};
    assert.deepEqual(compile(s).query.sort, [{field: 'amount', direction: stored}], 'a period view sorts too');
  }
  const s = sheet(); s.columns = [pill('cycle')]; s.analytics = {...s.analytics, transform: 'annual', period: 'cycle'};
  assert.deepEqual(compile(s).query.sort, [{field: 'cycle', direction: 'asc'}], 'and opens in period order');
});

/* ----------------------------------------------------------------- labels */

test('[studio-labels] every mark type this path can accept or produce has an entry', () => {
  // The accepted ids, plus the two the compile produces but never accepts:
  // 'circle' becomes 'scatter' and 'text' becomes 'table'.
  const producible = new Set([...studio.MARK_TYPES, 'scatter', 'table']);
  const missing = [...producible].filter(type => !(type in studio.LABEL_SUPPORT));
  assert.deepEqual(missing, [], 'LABEL_SUPPORT is missing: ' + missing.join(', '));
  const stale = Object.keys(studio.LABEL_SUPPORT).filter(type => !producible.has(type));
  assert.deepEqual(stale, [], 'LABEL_SUPPORT names types this path cannot produce: ' + stale.join(', '));
  for (const [type, value] of Object.entries(studio.LABEL_SUPPORT)) {
    assert.ok(value === true || (typeof value === 'string' && value.length > 10),
      type + ' must be true or carry a reason a reader can act on');
    if (typeof value === 'string')
      assert.ok(/[a-z]/.test(value) && !/^[a-z]/.test(value), type + ': the reason reads as a sentence');
  }
});

// Paul: "nothing is logically constrained". Every renderer draws value labels
// now (engine contract V05, W8), period analysis included, so the button is
// enabled on every mark. A table included (r4): its marks are its value cells,
// so Labels shows or hides their printed values instead of being disabled.
test('[studio-labels] labelLock answers with the empty string on every mark, a table included', () => {
  for (const type of ['bar', 'line', 'area', 'scatter', 'heatmap', 'dot', 'series-dot', 'stacked', 'histogram',
    'bubbles', 'highlight', 'treemap', 'box', 'gantt', 'bullet', 'combination', 'dual-line', 'pie', 'doughnut',
    'waterfall', 'pareto', 'radar', 'map', 'filled-map'])
    assert.equal(studio.labelLock(type, 'none'), '', type + ' draws labels: ' + PAUL);
  for (const type of ['table', 'text'])
    assert.equal(studio.labelLock(type, 'none'), '', type + ' shows or hides its values: ' + PAUL);
  for (const transform of ['annual', 'yoy', 'change', 'forecast'])
    assert.equal(studio.labelLock('bar', transform), '', transform + ' keeps its labels');
  assert.deepEqual(studio.LABELLED_TRANSFORMS, ['none', 'annual', 'yoy', 'change', 'forecast']);
  // A type nobody has heard of is refused, not silently allowed.
  assert.ok(studio.labelLock('invented-mark', 'none'), 'an unknown mark is not labellable');
});

// Paul: "nothing is logically constrained". None used to be disabled wherever
// it asked the server the same question as Descending. A stored 'none' keeps
// its meaning (every new sheet and saved view opens largest first), and the
// None button stores 'source': the same query, listed in the groups' own order.
test('[studio-sort] None on a category axis is the groups own order, on the same query', () => {
  const opened = sheet(), natural = sheet(), time = sheet();
  natural.sort = 'source';
  time.sort = 'source'; time.columns = [pill('cycle')];
  assert.deepEqual(compile(natural).query, compile(opened).query, 'the page still holds the largest groups');
  assert.equal(compile(natural).spec.naturalOrder, true, PAUL);
  assert.equal(compile(opened).spec.naturalOrder, undefined, 'a new sheet still opens largest first');
  assert.equal(compile(time).spec.naturalOrder, undefined, 'a time axis is already in its own order');
  assert.deepEqual(compile(time).query.sort, [{field: 'cycle', direction: 'asc'}]);
  assert.deepEqual(studio.naturalRows([{company: 'b10'}, {company: 'B2'}, {company: 'a'}, {company: 'B2', n: 2}], ['company']),
    [{company: 'a'}, {company: 'B2'}, {company: 'B2', n: 2}, {company: 'b10'}], 'A to Z, numbers ascending, ties kept in place');
});

test('[studio-labels] the labels toggle is render-only: it never changes the query', () => {
  const off = sheet(), on = sheet();
  // Build 03 (S1): a new sheet starts with its labels on, so "off" is set.
  off.showLabels = false;
  on.showLabels = true;
  assert.deepEqual(compile(on).query, compile(off).query, 'the query must be identical');
  assert.equal(compile(on).spec.labels, true);
  assert.equal(compile(off).spec.labels, false);
});
