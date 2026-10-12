// The consumer half of two contracts (contracts/README.md): every example the
// server produces is fed to the shipped PAC evidence page (evidence.js) and map
// (evidence-map.js), not to a copy.
//  * evidence-geo.json v4 (item 7 of F4): a refund is a reversal listed once,
//    signed, outside the shares; shares are of the positive located money the
//    server names (share_base), which the page and the map both read.
//  * evidence-network.json v1 (F4 items 3 and 4): every committee in a network
//    shows its own coverage and its own reconciled / differs mark.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const map = require('../evidence-map.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const load = name => JSON.parse(fs.readFileSync(path.join(CONTRACTS, name + '.json'), 'utf8'));
const geo = load('evidence-geo');
const network = load('evidence-network');

const source = fs.readFileSync(path.join(__dirname, '..', 'evidence.js'), 'utf8');
function slice(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start + 1);
  assert.ok(start >= 0 && end > start, `evidence.js still has ${from.trim()}`);
  return source.slice(start, end);
}
const page = vm.runInNewContext(
  slice('  function notItemizedText(item) {', '  /* The map view is a SKELETON')
  + slice('  function rankedRows(state) {', '  function mapFocusHtml(')
  + slice('  const checkMark = check =>', '  function networkDrawerHtml(pane) {')
  + '\n({shareBase, reversalLine, shareRuleText, mapListHtml, rankedRows, networkChecks, networkCommitteesHtml})', {
    // evidence.js's own whole-dollar formatter and app.js's currency, as the page has them.
    money: value => value == null ? '—' : (value < 0 ? '-' : '') + '$' + Math.round(Math.abs(value)).toLocaleString('en-US'),
    currency: value => value == null ? 'Unresolved'
      : Number(value).toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2}),
    number: value => value == null ? '—' : Number(value).toLocaleString(),
    cycleLabel: value => value ? `${Number(value) - 1}–${value}` : 'All cycles',
    isSenateKey: key => /-SEN$/.test(String(key || '')),
    escapeHtml: value => String(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[character])});

const v4 = geo.examples.filter(example => example.name.endsWith('(v4)'));
const refund = v4.find(example => /refund/.test(example.name)).payload;
const copy = value => JSON.parse(JSON.stringify(value));

test('the page and the map read the one share base the server named, on every layer', () => {
  assert.equal(v4.length, 3);
  for (const {name, payload} of v4) {
    for (const layer of ['region', 'state', 'district', 'zip', 'sld']) {
      const family = ['zip', 'sld'].includes(layer) ? 'zips' : 'places';
      const named = payload.share_base[family];
      assert.equal(page.shareBase(payload, family), named == null ? null : named, `${name} ${layer}`);
      assert.equal(map.shareBaseFor({layer, body: payload}), named == null ? 0 : named, `${name} ${layer}`);
    }
  }
});

test('a refund is one signed line outside the shares, and the shares stand', () => {
  const html = page.mapListHtml(refund, new Set());
  assert.equal((html.match(/class="map-reversal"/g) || []).length, 1);
  assert.match(html, /<strong>Refunds and reversals<\/strong>/);
  assert.match(html, /<td colspan="2">outside the shares · [^<]*22Z[^<]*<\/td><td class="num">-\$5,000<\/td><td class="num">1 rows<\/td>/);
  assert.doesNotMatch(html, /map-unlocated/, 'the refund is not an Unlocated line');
  assert.doesNotMatch(html, /Unavailable/);
  // The place that nets below zero (VA, -$2,500) is a reversal with no share;
  // it does not withdraw anyone else's.
  assert.match(html, /-\$2,500<\/td><td class="num">Reversal<\/td>/);
  const rows = page.rankedRows({body: refund, layer: 'district', direction: 'spent', off: new Set(), sldRows: []});
  assert.ok(rows.length > 0 && rows.every(row => typeof row.share === 'number'));
  assert.equal(rows[0].share, 1);
  const states = page.rankedRows({body: refund, layer: 'state', direction: 'spent', off: new Set(), sldRows: []});
  assert.deepEqual(states.map(row => [row.key, row.amount, row.share]), [['CA', 1000, 1], ['VA', -2500, null]]);
  assert.equal(page.shareRuleText(refund, 'district'),
    'Shares are of the $1,000 of positive located spending on this map; a place that nets below zero is marked '
    + 'Reversal and has no share. Unlocated money and -$5,000 of refunds and reversals are listed with the places, '
    + 'outside the shares.');
});

test('negative control: the page never recomputes a base the server withheld', () => {
  const withheld = copy(refund);
  withheld.share_base.places = null;
  const rows = page.rankedRows({body: withheld, layer: 'state', direction: 'spent', off: new Set(), sldRows: []});
  assert.ok(rows.every(row => row.share === null));
  assert.match(page.mapListHtml(withheld, new Set()), /Unavailable/);
  assert.match(page.shareRuleText(withheld, 'state'), /^Shares are unavailable/);
});

test('negative control: a payload from before v4 keeps the rule that withdrew every share for a refund', () => {
  const old = copy(refund);
  delete old.share_base; delete old.reversals_total;
  for (const line of old.unlocated) delete line.kind;
  assert.equal(page.shareBase(old, 'places'), null);
  assert.equal(map.shareBaseFor({layer: 'state', body: old}), 0);
  assert.doesNotMatch(page.mapListHtml(old, new Set()), /map-reversal/);
  // Static files are served from disk, so a console not yet restarted onto v4
  // hands this page such payloads: no v4 rule is stated for them, and the map's
  // tooltip keeps the old words, since their base is the reported total.
  assert.equal(page.shareRuleText(old, 'state'), '');
  const placed = copy(old);
  placed.unlocated = [];
  assert.equal(page.shareRuleText(placed, 'state'), '');
  assert.equal(map.shareOf(old, false), '% of spend');
  assert.equal(map.shareOf(old, true), '% of receipts');
  assert.equal(map.shareOf(refund, false), '% of positive located spending');
  assert.equal(map.shareOf(refund, true), '% of positive located receipts');
});

test('the network example still lists its not-itemized transfer outside the total', () => {
  const body = v4.find(example => /saved network/.test(example.name)).payload;
  assert.match(page.mapListHtml(body, new Set()), /class="map-not-itemized"/);
});

test('every committee in a network shows its own coverage and its own marks', () => {
  const answer = network.examples[0].payload;
  const html = page.networkCommitteesHtml(answer.committees);
  const rows = html.split('<tr data-committee=').slice(1);
  assert.equal(rows.length, answer.committees.length);
  const row = id => rows.find(text => text.startsWith(`"${id}"`));
  assert.match(row('C00836221'), /2026-06-29[\s\S]*2026-07-31/);
  assert.match(row('C00836221'), /Differs -\$3,000\.00[\s\S]*No summary saved/);
  assert.match(row('C00835959'), /Reconciled[\s\S]*Differs -\$12,000\.00/);
  assert.equal((row('C00848440').match(/recon-ok/g) || []).length, 2);
  assert.match(row('C00571703'), /No saved snapshot[\s\S]*No summary saved[\s\S]*No summary saved/);
  const lines = page.networkChecks(answer.committees);
  assert.equal(lines.length, answer.committees.length);
  assert.ok(lines.some(line => /C00836221, 2025–2026\): independent expenditures differ from its summary by -\$3,000\.00/.test(line)
    && /itemized to 2026-06-29 · summary to 2026-07-31/.test(line)));
});

test('a network that reaches many committees with no snapshot gives them one row and one sentence', () => {
  const answer = network.examples[0].payload;
  const unsaved = answer.committees.find(row => !row.saved);
  const many = [...answer.committees,
    ...Array.from({length: 12}, (_, index) => ({...unsaved, committee: `C9${String(index).padStart(7, '0')}`}))];
  const html = page.networkCommitteesHtml(many);
  assert.equal(html.split('<tr data-committee=').length - 1, answer.committees.filter(row => row.saved).length);
  assert.match(html, /class="network-unsaved"[\s\S]*13 committees with no saved snapshot[\s\S]*No summary saved[\s\S]*No summary saved/);
  const lines = page.networkChecks(many);
  assert.equal(lines.length, answer.committees.filter(row => row.saved).length + 1);
  assert.match(lines.at(-1), /^No summary saved for the 13 committees in this network with no saved snapshot: .* and 3 more\.$/);
  // Negative control: a handful are still listed one by one.
  assert.equal(page.networkChecks(answer.committees).length, answer.committees.length);
});

test('negative control: an answer from before F4 draws no committee table and claims no check', () => {
  assert.equal(page.networkCommitteesHtml([]), '');
  assert.deepEqual(page.networkChecks([]), []);
});
