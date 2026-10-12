/* Return navigation, asserted without a browser.

   graph.js returns at its own `typeof document === 'undefined'` guard, so
   requiring it here costs three pure functions and no DOM - the same bargain
   graph-layout.test.js already makes for the row geometry. What is asserted is
   the MODEL: which parts of the scope trail show, what pressing one clears, and
   that the Back stack drops its oldest entry rather than its newest.

   The FEC link rule is asserted from the other end, against the one validator
   the console now has (MentatScope.fecLink) and the URL shapes the four it
   replaced disagreed about.

   Run this suite with the glob form; Node 24 rejects a bare directory:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/) */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const graph = require('../graph.js');
const scope = require('../scope.js');

/* --------------------------------------------------------------- the trail */

test('[graph-nav] the trail shows only the parts that are set', () => {
  assert.deepStrictEqual(
    graph.trailModel({industry: 'Community Banks'}).map(part => part.key),
    ['industry']);
  assert.deepStrictEqual(
    graph.trailModel({industry: 'Community Banks', recipient: 'AMERICAN BANKERS ASSOCIATION PAC'})
      .map(part => part.key),
    ['industry', 'recipient'], 'a PAC with no company or type does not invent the gap');
  assert.deepStrictEqual(
    graph.trailModel({industry: 'Banking', company: 'Citibank', category: 'Super PAC',
      recipient: 'MEMBERS FIRST'}).map(part => part.key),
    graph.TRAIL_ORDER);
  // Blank, null and undefined are all "not set", never a part with no words in it.
  for (const empty of ['', null, undefined]) {
    assert.deepStrictEqual(
      graph.trailModel({industry: 'Banking', company: empty, category: 'JFC'})
        .map(part => part.key),
      ['industry', 'category'], String(empty) + ' is not a trail part');
  }
  assert.deepStrictEqual(graph.trailModel({}), [], 'nothing set, nothing shown');
  assert.deepStrictEqual(graph.trailModel(), [], 'and no scope at all does not throw');
});

test('[graph-nav] a trail part carries its place in the scope, not its place on screen', () => {
  // The teeth: with no company set, the recipient is the SECOND thing shown and
  // still the FOURTH thing in the scope. Clearing by the screen position would
  // clear the recipient type the reader can see they are still inside.
  const parts = graph.trailModel({industry: 'Banking', recipient: 'MEMBERS FIRST'});
  assert.deepStrictEqual(parts.map(part => part.index), [0, 3]);
  assert.deepStrictEqual(graph.clearedBy(parts[1].index), ['recipient']);
});

test('[graph-nav] pressing a part clears it and everything to its right', () => {
  assert.deepStrictEqual(graph.clearedBy(0), ['company', 'category', 'recipient'],
    'the industry cannot be cleared - it returns to the whole industry');
  assert.deepStrictEqual(graph.clearedBy(1), ['company', 'category', 'recipient']);
  assert.deepStrictEqual(graph.clearedBy(2), ['category', 'recipient']);
  assert.deepStrictEqual(graph.clearedBy(3), ['recipient']);
  // Nothing it returns is ever to the LEFT of the part that was pressed.
  for (let index = 0; index < graph.TRAIL_ORDER.length; index += 1) {
    for (const key of graph.clearedBy(index)) {
      assert.ok(graph.TRAIL_ORDER.indexOf(key) >= Math.max(1, index),
        'pressing ' + graph.TRAIL_ORDER[index] + ' must not clear ' + key);
    }
  }
});

/* ---------------------------------------------------------------- the stack */

test('[graph-nav] the Back stack pushes, pops and is capped at twenty', () => {
  assert.equal(graph.PAST_MAX, 20);
  let stack = [];
  for (let step = 1; step <= 25; step += 1) stack = graph.pushPast(stack, {step: step});
  assert.equal(stack.length, 20, 'the stack is capped');
  // The OLDEST entry is dropped, never the newest: a reader twenty-five steps
  // in has to be able to go back one.
  assert.deepStrictEqual(stack[stack.length - 1], {step: 25}, 'the newest entry survives');
  assert.deepStrictEqual(stack[0], {step: 6}, 'and the oldest five are gone');
  // Popping is the array's own, so Back is the last thing that was pushed.
  assert.deepStrictEqual(stack.pop(), {step: 25});
  assert.deepStrictEqual(stack.pop(), {step: 24});
  assert.equal(stack.length, 18);
  // A cap given explicitly is honoured, and pushing onto nothing works.
  assert.deepStrictEqual(graph.pushPast([], {a: 1}, 2), [{a: 1}]);
  assert.deepStrictEqual(graph.pushPast([{a: 1}, {a: 2}], {a: 3}, 2), [{a: 2}, {a: 3}]);
  assert.deepStrictEqual(graph.pushPast(undefined, {a: 1}), [{a: 1}]);
});

test('[graph-nav] the stack is an array this module hands back, never a store', () => {
  // graph.js empties it by assignment on an industry change; nothing here or in
  // that file writes navigational state to localStorage or sessionStorage, and
  // the URL stays the only persisted scope (contracts/url_scope.json).
  const source = fs.readFileSync(path.resolve(__dirname, '..', 'graph.js'), 'utf8');
  // A USE, not a mention: two comments in that file name both stores to say
  // it writes to neither, and a bare name match reads the promise as the
  // breach. A use is a property read, a call or an index.
  assert.equal(/(local|session)Storage\s*[.[(]/.test(source), false,
    'graph.js must not persist navigation anywhere but the URL');
  assert.equal(/pushState\s*\(/.test(source), false,
    'Paul’s ruling C: replaceState only, and graph.js:672 says so in words');
});

/* -------------------------------------------------------------- the FEC rule */

test('[graph-nav] fecLink: the one rule, and what the four it replaced disagreed about', () => {
  const pass = [
    ['https://www.fec.gov/data/committee/C00000000/', 'https://www.fec.gov/data/committee/C00000000/'],
    ['https://www.fec.gov/data/candidate/H0XX00000/', 'https://www.fec.gov/data/candidate/H0XX00000/'],
    ['https://docquery.fec.gov/cgi-bin/fecimg/?12345678', 'https://docquery.fec.gov/cgi-bin/fecimg/?12345678'],
    // The fragment is stripped, so a saved anchor cannot ride out of the console.
    ['https://www.fec.gov/data/committee/C00000000/#receipts', 'https://www.fec.gov/data/committee/C00000000/'],
    // An explicit default port is the default port.
    ['https://www.fec.gov:443/data/', 'https://www.fec.gov/data/']
  ];
  for (const entry of pass) {
    assert.equal(scope.fecLink(entry[0]), entry[1], entry[0] + ' is an FEC filing link');
  }
  const refuse = [
    'http://www.fec.gov/data/',                      // not https
    'https://fec.gov/data/',                         // the bare domain is not one of the two hosts
    'https://evil.fec.gov/data/',                    // chart-studio and contribution-studio took ANY subdomain
    'https://www.fec.gov.evil.example/data/',        // and a name that merely ends in the right one
    'https://user:pass@www.fec.gov/data/',           // userinfo
    'https://www.fec.gov:8443/data/',                // a port that is not the default
    // Cross-reference's fourth rule, kept and raised to every caller: a query
    // string must be an FEC image number. The control it was written against:
    'https://www.fec.gov/file?api_key=secret',
    'https://docquery.fec.gov/cgi-bin/fecimg/?1234',        // too short to be an image number
    'https://www.fec.gov/data/receipts/?two_year_transaction_period=2026',
    'javascript:alert(1)',                           // not https
    'data:text/html,<script>alert(1)</script>',
    '//www.fec.gov/data/',                           // protocol-relative: not a URL on its own
    'www.fec.gov/data/',
    '', null, undefined, 0, {}
  ];
  for (const input of refuse) {
    assert.equal(scope.fecLink(input), '', String(input) + ' must not become a link');
  }
  assert.deepStrictEqual(scope.FEC_HOSTS, ['www.fec.gov', 'docquery.fec.gov']);
});

/* ------------------------------------------------------- the two drawer keys */

test('[graph-nav] the drawer keys are scope keys, and the contract is v6', () => {
  const contract = JSON.parse(fs.readFileSync(path.resolve(__dirname, '..', '..', '..',
    'audits', 'mentat_failings_20260915', 'contracts', 'url_scope.json'), 'utf8'));
  // v6 (build 02, 2026-09-22) added Graph's compare and layout keys; the
  // drawer keys below are unchanged. v7 (lane F) adds only the pane key <pane>.flow.
  assert.equal(contract.version, 7);
  assert.ok(scope.KEYS.includes('drawer') && scope.KEYS.includes('drawerDirection'));
  assert.ok(contract.keys.drawer && contract.keys.drawerDirection,
    'the key table names both, or the table is prose');
  // The drawer is separate from `recipient` on purpose: the page can stay
  // narrowed to a PAC with the drawer closed, and a permalink has to say which.
  assert.deepStrictEqual(
    scope.read('industry=Banking&recipient=C00004812'),
    {industry: 'Banking', recipient: 'C00004812', invalid: []});
  assert.deepStrictEqual(
    scope.read('industry=Banking&recipient=C00004812&drawer=C00004812&drawerDirection=Support'),
    {industry: 'Banking', recipient: 'C00004812', drawer: 'C00004812',
      drawerDirection: 'Support', invalid: []});
  // Every side the server will answer for, and nothing else.
  for (const side of scope.DRAWER_DIRECTIONS) {
    assert.equal(scope.read('drawerDirection=' + encodeURIComponent(side)).drawerDirection, side);
  }
  // Paul, 2026-09-23, on the drawer's "Contributions / Other" segment: "It
  // should be two separate segments". Contribution and Other are that
  // recorded bucket split by FEC transaction code; 'Contribution / Other'
  // stays for amounts whose source records no code, and for older links.
  assert.deepStrictEqual(scope.DRAWER_DIRECTIONS,
    ['Contribution', 'Other', 'Support', 'Oppose', 'Contribution / Other', 'Unknown']);
  for (const bad of ['Downstream', 'support', 'Contribution/Other', 'Oppose ']) {
    const read = scope.read('drawerDirection=' + encodeURIComponent(bad));
    assert.equal('drawerDirection' in read, false, bad + ' must be dropped, not merely reported');
    assert.equal(read.invalid.length, 1);
  }
  // The value reaches /api/graph as `recipient`, so its shape is checked here.
  for (const bad of ['../../etc', 'C0000 4812', 'C00004812/../..', '-leading']) {
    assert.equal('drawer' in scope.read('drawer=' + encodeURIComponent(bad)), false,
      bad + ' is not a committee id');
  }
  assert.equal(scope.read('drawer=C00004812').drawer, 'C00004812');
});
