/* The "+ Compare with..." menu and the pill group, asserted without a browser.

   graph.js publishes the component as window.MentatCompareMenu (Graph uses it,
   and Chart Studio - build 03 - calls the same two functions with its own
   lists). What the component DECIDES lives in a pure model that graph.js
   exports above its document guard as `compareMenu`: which kinds it offers,
   which values survive `exclude` and the search, where the arrow keys land,
   that onPick fires exactly once, and how many pills a group may hold. Every
   controller keeps its own state, so two menus can be open at once.

   Run with the glob form from mentat/ (see graph-layout.test.js):
     node --test "app/tests/**"/"*.test.js" */
const test = require('node:test');
const assert = require('node:assert/strict');
const graph = require('../graph.js');

const menu = graph.compareMenu;

test('[compare-menu] the default kinds and labels are the three Graph compares', () => {
  assert.deepStrictEqual(menu.KINDS, ['industry', 'cycle', 'company']);
  assert.equal(menu.MAX, 3);
  assert.deepStrictEqual(menu.menuKinds().map(entry => entry.label),
    ['Another industry', 'Another cycle', 'Another company']);
});

test('[compare-menu] kinds: a host subset keeps its order, drops unknown kinds and repeats, and may relabel', () => {
  assert.deepStrictEqual(menu.menuKinds(['company', 'industry', 'party', 'company']).map(entry => entry.kind),
    ['company', 'industry']);
  assert.deepStrictEqual(menu.menuKinds(['cycle'], {cycle: 'Another election cycle'}),
    [{kind: 'cycle', label: 'Another election cycle'}]);
});

test('[compare-menu] values: exclude compares as strings, drops blanks and repeats, keeps labels', () => {
  const values = menu.menuValues([
    {value: '2022', label: '2021–2022'}, {value: 2024, label: '2023–2024'}, {value: '2026', label: '2025–2026'},
    {value: '', label: 'All cycles'}, {value: '2022', label: 'again'}, null], ['2024', 2026]);
  assert.deepStrictEqual(values, [{value: '2022', label: '2021–2022'}]);
  assert.deepStrictEqual(menu.menuValues([{value: 'Banking'}], []), [{value: 'Banking', label: 'Banking'}]);
});

test('[compare-menu] search matches the label or the value, case-insensitively', () => {
  const items = [{value: 'Credit Union', label: 'Credit Union'}, {value: 'Credit Arms', label: 'Credit Arms'},
    {value: '2026', label: '2025–2026 · to date'}];
  assert.deepStrictEqual(menu.menuFilter(items, 'CRED').map(item => item.value), ['Credit Union', 'Credit Arms']);
  assert.deepStrictEqual(menu.menuFilter(items, '2026').map(item => item.value), ['2026']);
  assert.equal(menu.menuFilter(items, '  ').length, 3);
});

test('[compare-menu] arrow keys wrap, Home and End jump, an empty list has no active row', () => {
  assert.equal(menu.menuMove(-1, 'ArrowDown', 3), 0);
  assert.equal(menu.menuMove(2, 'ArrowDown', 3), 0);
  assert.equal(menu.menuMove(0, 'ArrowUp', 3), 2);
  assert.equal(menu.menuMove(-1, 'ArrowUp', 3), 2);
  assert.equal(menu.menuMove(1, 'Home', 3), 0);
  assert.equal(menu.menuMove(1, 'End', 3), 2);
  assert.equal(menu.menuMove(0, 'ArrowDown', 0), -1);
});

function controller(overrides) {
  const calls = {picked: [], closed: [], asked: []};
  const options = Object.assign({
    kinds: ['industry', 'cycle', 'company'],
    valuesFor: kind => { calls.asked.push(kind); return Promise.resolve(
      kind === 'industry' ? [{value: 'Banking', label: 'Banking'}, {value: 'Credit Union', label: 'Credit Union'}, {value: 'Crypto', label: 'Crypto'}]
        : kind === 'cycle' ? [{value: '2024', label: '2023–2024'}, {value: '2026', label: '2025–2026 · to date'}]
          : [{value: 'Goldman Sachs Bank NA', label: 'Goldman Sachs Bank NA'}]); },
    exclude: {industry: ['Banking'], cycle: ['2026']},
    onPick: (kind, value) => calls.picked.push([kind, value]),
    onClose: reason => calls.closed.push(reason)
  }, overrides || {});
  return {menu: menu.createMenuController(options), calls};
}

test('[compare-menu] kinds first, then the chosen kind\'s values without the excluded one', async () => {
  const {menu: m, calls} = controller();
  assert.equal(m.state().stage, 'kinds');
  assert.equal(m.state().active, 0);
  const state = await m.choose('industry');
  assert.equal(state.stage, 'values');
  assert.equal(state.kind, 'industry');
  assert.deepStrictEqual(state.items.map(item => item.value), ['Credit Union', 'Crypto']);
  assert.equal(state.active, 0);
  assert.deepStrictEqual(calls.asked, ['industry']);
});

test('[compare-menu] keyboard: ArrowDown and Enter choose a kind, typing filters, Enter picks once and closes', async () => {
  const {menu: m, calls} = controller();
  assert.equal(m.key('ArrowDown').action, 'move');
  assert.equal(m.state().active, 1);
  const answer = m.key('Enter');
  assert.equal(answer.action, 'choose');
  const state = await answer.then;
  assert.equal(state.kind, 'cycle');
  assert.deepStrictEqual(state.items.map(item => item.value), ['2024']);
  m.type('2023');
  assert.equal(m.state().items.length, 1);
  const picked = m.key('Enter');
  assert.equal(picked.action, 'pick');
  assert.deepStrictEqual(calls.picked, [['cycle', '2024']]);
  assert.deepStrictEqual(calls.closed, ['pick']);
  // closed: nothing fires twice
  assert.equal(m.key('Enter').action, 'none');
  m.pick('2024');
  m.close('escape');
  assert.deepStrictEqual(calls.picked, [['cycle', '2024']]);
  assert.deepStrictEqual(calls.closed, ['pick']);
});

test('[compare-menu] Escape closes without a pick; a search with no match picks nothing', async () => {
  const {menu: m, calls} = controller();
  await m.choose('industry');
  m.type('zzz');
  assert.equal(m.state().active, -1);
  assert.equal(m.key('Enter').action, 'none');
  assert.equal(m.key('Escape').action, 'close');
  assert.deepStrictEqual(calls.picked, []);
  assert.deepStrictEqual(calls.closed, ['escape']);
});

test('[compare-menu] one kind skips the kinds stage; a failed value list reports and does not pick', async () => {
  const {menu: m} = controller({kinds: ['company']});
  assert.equal(m.state().stage, 'values');
  const state = await m.start();
  assert.deepStrictEqual(state.items.map(item => item.value), ['Goldman Sachs Bank NA']);
  const {menu: failing, calls} = controller({kinds: ['cycle'], valuesFor: () => Promise.reject(new Error('Cycles unavailable.'))});
  const failed = await failing.start();
  assert.equal(failed.error, 'Cycles unavailable.');
  assert.equal(failed.items.length, 0);
  assert.equal(failing.key('Enter').action, 'none');
  assert.deepStrictEqual(calls.picked, []);
});

test('[compare-menu] a slower answer to an earlier kind never overwrites the later choice', async () => {
  let release;
  const slow = new Promise(resolve => { release = resolve; });
  const {menu: m} = controller({valuesFor: kind => kind === 'industry' ? slow : Promise.resolve([{value: '2024', label: '2023–2024'}])});
  const first = m.choose('industry');
  await m.choose('cycle');
  release([{value: 'Crypto', label: 'Crypto'}]);
  await first;
  assert.equal(m.state().kind, 'cycle');
  assert.deepStrictEqual(m.state().items.map(item => item.value), ['2024']);
});

test('[compare-menu] two controllers keep separate state', async () => {
  const a = controller().menu, b = controller().menu;
  await a.choose('industry');
  assert.equal(a.state().stage, 'values');
  assert.equal(b.state().stage, 'kinds');
});

test('[compare-menu] pills: scope numbers in order, a remove name per pill, "+" only below max', () => {
  const two = menu.pillsModel([{value: 'Banking', label: 'Banking'}, {value: 'Credit Union', label: 'Credit Union'}], 3);
  assert.deepStrictEqual(two.pills.map(pill => [pill.scope, pill.label, pill.removeLabel]),
    [[1, 'Banking', 'Remove Banking'], [2, 'Credit Union', 'Remove Credit Union']]);
  assert.equal(two.canAdd, true);
  assert.equal(menu.pillsModel(['A', 'B', 'C'], 3).canAdd, false);
  // Chart Studio asks for at most two scopes
  const studio = menu.pillsModel(['A', 'B', 'C'], 2);
  assert.equal(studio.pills.length, 2);
  assert.equal(studio.canAdd, false);
  // a host cannot raise the ceiling past three
  assert.equal(menu.pillsModel(['A', 'B', 'C', 'D'], 9).pills.length, 3);
});

/* A compare half (02b) draws the same six columns in half or a third of a
   card: they stay pairwise disjoint and inside the half at every width the
   halves are drawn at (1440: ~265, 1920: ~390; thirds ~370 stacked at 1440),
   and the value column is never cut while the label can still yield. */
test('[compare-menu] compact half columns stay disjoint and inside the half', () => {
  const boxes = box => {
    const named = [['label', box.labelLeft, box.labelRight], ['bar', box.barLeft, box.barLeft + box.barMax]];
    if (box.dotColumn > 0) named.push(['dot', 0, box.dotColumn]);
    if (box.valueWidth > 0) named.push(['value', box.valueRight - box.valueWidth, box.valueRight]);
    named.push(['share', box.shareRight - box.shareWidth, box.shareRight]);
    if (box.sparkWidth > 0) named.push(['spark', box.sparkX, box.sparkX + box.sparkWidth]);
    return named;
  };
  // 200 is the floor a half is ever drawn at; from 265 (a half at 1440) up,
  // an amount as wide as $12,003,181 (78px at 13px) is never cut.
  for (const width of [200, 240, 265, 300, 370, 390, 520]) {
    for (const leftRead of [false, true]) {
      for (const spark of width >= 380 ? [false, true] : [false]) {
        for (const valueWidth of [0, 42, 64, 78]) {
          const box = graph.layoutColumns(width, {compact: true, party: leftRead, leftRead, spark, valueWidth});
          const where = `W=${width} leftRead=${leftRead} spark=${spark} value=${valueWidth}`;
          if (width >= 265) assert.equal(box.valueWidth, valueWidth, where + ': the amount column was cut');
          assert.ok(box.barMax >= graph.COMPACT_BAR_MIN, where + ': the bar lost its minimum');
          const named = boxes(box);
          for (const [name, from, to] of named) {
            assert.ok(from >= 0 && to <= box.width, `${where}: ${name} [${from}, ${to}] leaves the half`);
          }
          for (let i = 0; i < named.length; i += 1) {
            for (let j = i + 1; j < named.length; j += 1) {
              const a = named[i], b = named[j];
              assert.ok(a[2] <= b[1] || b[2] <= a[1], `${where}: ${a[0]} [${a[1]}, ${a[2]}] overlaps ${b[0]} [${b[1]}, ${b[2]}]`);
            }
          }
        }
      }
    }
  }
});
