/* The URL scope contract, from both ends.

   Every payload here is read from the one fixture both suites import
   (audits/mentat_failings_20260915/contracts/url_scope.json, contracts/README.md's
   loader convention). Nothing below hand-builds a URL or a scope: a copied
   literal is how DE-021 happened. */

// Run this suite with the glob form; Node 24 rejects a bare directory, and the
// glob cannot live in the block comment above because it closes it:
//   node --test "app/tests/**/*.test.js"      (cwd: mentat/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const scope = require('../scope.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
function loadContract(name) {
  return JSON.parse(fs.readFileSync(path.join(CONTRACTS, name + '.json'), 'utf8'));
}
const contract = loadContract('url_scope');

// "/?industry=Banking#graph" -> {query: 'industry=Banking', view: 'graph'}
function split(url) {
  const hash = url.indexOf('#');
  const view = hash < 0 ? '' : url.slice(hash + 1);
  const head = hash < 0 ? url : url.slice(0, hash);
  const mark = head.indexOf('?');
  return {query: mark < 0 ? '' : head.slice(mark + 1), view, pathname: mark < 0 ? head : head.slice(0, mark)};
}

// A location/history pair with no document behind it, so write() can be driven
// from node exactly as the browser drives it.
function fakeWindow(url) {
  const parts = split(url);
  const state = {
    location: {pathname: parts.pathname || '/', search: parts.query ? '?' + parts.query : '', hash: parts.view ? '#' + parts.view : ''},
    replaced: [],
    events: new EventTarget()
  };
  state.history = {
    replaceState(_data, _title, next) {
      state.replaced.push(next);
      const now = split(next);
      state.location.pathname = now.pathname || '/';
      state.location.search = now.query ? '?' + now.query : '';
      state.location.hash = now.view ? '#' + now.view : '';
    }
  };
  return state;
}

const urlExamples = contract.examples.filter(example => example.url);
const writeExamples = contract.examples.filter(example => example.write);

test('[url_scope] the contract itself is shaped the way contracts/README.md requires', () => {
  assert.equal(contract.contract, 'url_scope');
  assert.equal(contract.version, 7);
  assert.equal(contract.owner, 'S5; pane keys S6');
  assert.ok(contract.produced_by.length >= 1, 'produced_by lists every producer');
  assert.ok(contract.consumed_by.length >= 1, 'consumed_by lists every consumer');
  assert.ok(contract.negative_controls.length >= 5, 'negative_controls is mandatory and non-empty');
  assert.ok(urlExamples.length >= 5 && writeExamples.length >= 1);
  // A url example carries its scope at the top level; the `expected` envelope
  // belongs to negative_controls. S8 (491570a0b) wrapped one, and the three
  // subtests below then read `undefined` as the whole scope.
  for (const example of urlExamples) {
    assert.ok(example.scope, `${example.name} must carry a top-level scope, not an "expected" wrapper`);
  }
  // The key table and the module must name the same keys, or the table is prose.
  assert.deepStrictEqual(
    Object.keys(contract.keys).filter(key => key !== 'studioPane').sort(),
    [...scope.KEYS].sort());
  assert.deepStrictEqual(contract.reserved, [...scope.RESERVED]);
});

for (const example of urlExamples) {
  test(`[url_scope] read() answers the contract scope for ${example.name}`, () => {
    const parts = split(example.url);
    assert.equal(parts.view, example.view, 'the hash names the tab');
    assert.deepStrictEqual(scope.read(parts.query), Object.assign({}, example.scope, {invalid: []}));
    // read() with a leading '?' is the same read; location.search carries one.
    assert.deepStrictEqual(scope.read('?' + parts.query), Object.assign({}, example.scope, {invalid: []}));
  });

  test(`[url_scope] href() rebuilds ${example.name} byte for byte`, () => {
    assert.equal(scope.href(example.view, example.scope, {pathname: '/'}), example.url);
    // and serialize() is the pure half of the same rule
    assert.equal(scope.serialize(example.scope), split(example.url).query);
  });

  test(`[url_scope] ${example.name} round-trips through write() onto an empty URL`, () => {
    // The producer path: write() must be able to put every example on the wire.
    const state = fakeWindow('/#' + example.view);
    const written = scope.write(example.scope, {location: state.location, history: state.history, target: state.events});
    assert.deepStrictEqual(written, Object.assign({}, example.scope, {invalid: []}));
    assert.equal(state.location.search, '?' + split(example.url).query);
    assert.equal(state.location.hash, '#' + example.view, 'write() keeps the hash');
  });
}

for (const example of writeExamples) {
  test(`[url_scope] write() merge semantics: ${example.name}`, () => {
    const state = fakeWindow(example.write.before);
    scope.write(example.write.partial, {location: state.location, history: state.history, target: state.events});
    assert.equal(state.replaced.length, 1, 'one replaceState, never pushState');
    assert.equal(state.replaced[0], example.write.after);
  });
}

for (const control of contract.negative_controls) {
  test(`[url_scope] negative control: ${control.why.split('.')[0]}`, () => {
    const parts = split(control.url);
    const result = scope.read(parts.query);
    assert.deepStrictEqual(result, Object.assign({}, control.expected.scope, {invalid: control.expected.invalid}));
    for (const bad of control.expected.invalid) {
      assert.ok(!Object.prototype.hasOwnProperty.call(result, bad.key),
        `${bad.key} must be absent from the scope, not merely reported`);
    }
    // Teeth: a value read() refuses must also be one write() will not produce,
    // or a producer could mint the permalink this control forbids.
    for (const bad of control.expected.invalid) {
      if (!bad.value) continue;
      const state = fakeWindow('/?' + bad.key + '=keep-me#' + control.view);
      scope.write({[bad.key]: bad.value}, {location: state.location, history: state.history, target: state.events});
      assert.equal(state.location.search, '', `write() must refuse to write ${bad.key}=${bad.value}`);
    }
  });
}

test('[url_scope] write() preserves unknown keys and their order, and appends new ones', () => {
  const state = fakeWindow('/?evidence=csv&industry=Banking&offset=40&studioPane=B#evidence');
  scope.write({industry: 'Credit Union', cycle: '2024'},
    {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?evidence=csv&industry=Credit%20Union&offset=40&studioPane=B&cycle=2024');
  // S6's reserved keys survived untouched, which is the whole point of the list.
  for (const key of contract.reserved) {
    if (state.location.search.includes(key + '=')) assert.ok(true);
  }
  assert.ok(state.location.search.includes('evidence=csv'));
  assert.ok(state.location.search.includes('offset=40'));
});

test('[url_scope] studioPane is retired: never read, never written, never dropped', () => {
  // v4, 2026-09-19. Paul removed the N charts strip, so the key that chose a
  // pane's store belongs to nothing. It is not added to KEYS and it is not
  // special-cased away either: an old bookmark keeps working because write()
  // preserves every key this module does not own.
  assert.ok(!scope.KEYS.includes('studioPane'), 'studioPane is not a key scope.js owns');
  assert.match(contract.keys.studioPane.value, /^RETIRED/);
  assert.deepStrictEqual(contract.keys.studioPane.readers, [], 'the contract names no reader');
  assert.deepStrictEqual(contract.keys.studioPane.writers, [], 'and no writer');
  assert.deepStrictEqual(scope.read('industry=Banking&studioPane=B'), {industry: 'Banking', invalid: []});
  const state = fakeWindow('/?studioPane=B&industry=Banking#chart-studio');
  scope.write({cycle: '2024'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?studioPane=B&industry=Banking&cycle=2024');
});

test('[url_scope] build 06: a pane map compare cycle is the flat key <pane>.compare', () => {
  const state = fakeWindow('/?a.source=csv&a.id=csv-1&a.committee=C00000001&a.map=raised#evidence');
  scope.write({'a.compare': '2024'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?a.source=csv&a.id=csv-1&a.committee=C00000001&a.map=raised&a.compare=2024');
  assert.equal(scope.read(state.location.search.slice(1))['a.compare'], '2024');
  // Not a cycle: refused on read and never written.
  const refused = scope.read('a.source=csv&a.id=csv-1&a.committee=C00000001&a.compare=2023-2024');
  assert.equal(refused['a.compare'], undefined);
  assert.ok(refused.invalid.some(item => item.key === 'a.compare'));
  scope.write({'a.compare': ''}, {location: state.location, history: state.history, target: state.events});
  assert.ok(!state.location.search.includes('a.compare'));
});

test('[url_scope] write() never writes a reserved key, even when asked', () => {
  const state = fakeWindow('/#graph');
  const asked = Object.fromEntries(contract.reserved.map(key => [key, 'x']));
  scope.write(asked, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '');
  assert.equal(state.replaced.length, 0);
});

test('[url_scope] subscribe fires on a write, and not on a no-op write', () => {
  const state = fakeWindow('/?industry=Banking#graph');
  const seen = [];
  const stop = scope.subscribe(value => seen.push(value), state.events);
  scope.write({cycle: '2024'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(seen.length, 1, 'a change announces itself');
  assert.deepStrictEqual(seen[0], {industry: 'Banking', cycle: '2024', invalid: []});
  scope.write({cycle: '2024'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(seen.length, 1, 'writing the same value again announces nothing');
  assert.equal(state.replaced.length, 1, 'and does not touch history either');
  stop();
  scope.write({cycle: '2022'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(seen.length, 1, 'unsubscribe stops the listener');
});

/* v6 (console redesign build 02, 2026-09-22): Graph's comparison. The four
   url examples and three negative controls in the contract cover industry
   (2 and 3 values), cycle, company with layout=stacked, a one-value compare,
   an unknown compare and an unknown layout; these add what an example
   cannot express. */
test('[url_scope] v6: a repeated key without compare reads its first value, as before', () => {
  assert.deepStrictEqual(scope.read('industry=Banking&industry=Credit%20Union'), {industry: 'Banking', invalid: []});
  assert.deepStrictEqual(scope.read('cycle=2024&cycle=2026'), {cycle: '2024', invalid: []});
});

test('[url_scope] v6: every compare mode round-trips with two and with three values', () => {
  const cases = [
    ['industry', ['Banking', 'Credit Union']],
    ['industry', ['Banking', 'Credit Union', 'Property & Casualty Insurance']],
    ['cycle', ['2024', '2026']],
    ['cycle', ['2022', '2024', '2026']],
    ['company', ['Goldman Sachs Bank NA', 'JP Morgan Chase Bank NA']],
    ['company', ['Goldman Sachs Bank NA', 'JP Morgan Chase Bank NA', 'Wells Fargo Bank NA']]
  ];
  for (const [dim, values] of cases) {
    const base = dim === 'industry' ? {cycle: '2026'} : {industry: 'Banking'};
    const wanted = Object.assign({}, base, {[dim]: values[0], compare: dim, compareValues: values, invalid: []});
    // The producer's own spelling: an array on the compared key, the keys in
    // KEYS order as graph.js:writeScope passes them (write() appends new keys
    // in the order it is given them; serialize() follows KEYS).
    const partial = dim === 'industry'
      ? Object.assign({[dim]: values.slice()}, base, {compare: dim})
      : Object.assign({}, base, {[dim]: values.slice(), compare: dim});
    const state = fakeWindow('/#graph');
    scope.write(partial, {location: state.location, history: state.history, target: state.events});
    assert.deepStrictEqual(scope.read(state.location.search), wanted, dim + ' x' + values.length);
    // The key repeats in pill order, and nothing else does.
    const pairs = [...new URLSearchParams(state.location.search)];
    assert.deepStrictEqual(pairs.filter(pair => pair[0] === dim).map(pair => pair[1]), values);
    // read()'s own shape serializes back to the same query (Copy link).
    assert.equal('?' + scope.serialize(scope.read(state.location.search)), state.location.search);
  }
});

test('[url_scope] v6: layout=stacked rides with a two-scope comparison and is removed by an empty write', () => {
  const state = fakeWindow('/?industry=Banking&industry=Credit%20Union&compare=industry#graph');
  scope.write({layout: 'stacked'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?industry=Banking&industry=Credit%20Union&compare=industry&layout=stacked');
  assert.equal(scope.read(state.location.search).layout, 'stacked');
  scope.write({layout: ''}, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?industry=Banking&industry=Credit%20Union&compare=industry');
});

test('[url_scope] v6: a comparison holds three values; a fourth is refused and reported', () => {
  const read = scope.read('industry=A&industry=B&industry=C&industry=D&compare=industry');
  assert.deepStrictEqual(read.compareValues, ['A', 'B', 'C']);
  assert.equal(read.industry, 'A');
  assert.deepStrictEqual(read.invalid, [{key: 'industry', value: 'D', reason: 'a comparison holds at most 3 values'}]);
  // and write() never mints a fourth
  const state = fakeWindow('/#graph');
  scope.write({industry: ['A', 'B', 'C', 'D'], compare: 'industry'},
    {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?industry=A&industry=B&industry=C&compare=industry');
});

test('[url_scope] v6: a repeat of the same value is one pill, and two copies of one value compare nothing', () => {
  assert.deepStrictEqual(scope.read('industry=A&industry=B&industry=A&compare=industry').compareValues, ['A', 'B']);
  const same = scope.read('industry=A&industry=A&compare=industry');
  assert.equal(same.compare, undefined);
  assert.equal(same.industry, 'A');
  assert.deepStrictEqual(same.invalid.map(entry => entry.key), ['compare']);
  // write() drops a compare its key cannot carry
  const state = fakeWindow('/?industry=A#graph');
  scope.write({industry: ['A', 'A'], compare: 'industry'}, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?industry=A');
});

test('[url_scope] v6: ending a comparison leaves the first value and nothing of the comparison', () => {
  const state = fakeWindow('/?industry=Banking&industry=Credit%20Union&industry=Crypto&cycle=2026&compare=industry#graph');
  scope.write({industry: 'Credit Union', compare: '', layout: ''},
    {location: state.location, history: state.history, target: state.events});
  assert.equal(state.location.search, '?industry=Credit%20Union&cycle=2026');
  assert.deepStrictEqual(scope.read(state.location.search), {industry: 'Credit Union', cycle: '2026', invalid: []});
});

test('[url_scope] removing the last key drops the question mark rather than leaving "?"', () => {
  const state = fakeWindow('/?industry=Banking#graph');
  scope.write({industry: ''}, {location: state.location, history: state.history, target: state.events});
  assert.equal(state.replaced[0], '/#graph');
  assert.equal(state.location.search, '');
});
