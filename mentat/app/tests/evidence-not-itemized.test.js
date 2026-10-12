// F6 (Paul, 2026-09-25): no dollar may count twice. A transfer a committee's
// summary reports but its saved Schedule B does not itemize is kept out of every
// total by the server (evidence_geo.transfers_not_itemized) and shown by the
// page on its own line, outside the total. These tests pin that line.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'evidence.js'), 'utf8');
const start = source.indexOf('  function notItemizedText(item) {');
const end = source.indexOf('  /* The map view is a SKELETON', start);
assert.ok(start >= 0 && end > start, 'the not-itemized transfer line exists in the shipped PAC view');
const {notItemizedText, mapListHtml} = vm.runInNewContext(
  source.slice(start, end) + '\n({notItemizedText, mapListHtml})', {
    money: value => value == null ? '—'
      : Number(value).toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2}),
    number: value => Number(value).toLocaleString('en-US'),
    cycleLabel: value => value ? `${Number(value) - 1}–${value}` : 'All cycles',
    isSenateKey: key => /-SEN$/.test(key),
    escapeHtml: value => String(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character])
  });

const line = {committee: 'C00835959', cycle: '2026', committee_name: 'FAIRSHAKE',
              reason: 'transfers reported, recipient not itemized', amount: 14000000,
              summary_amount: 65000000, itemized_amount: 51000000};

test('the line names the committee, the cycle and both figures, and says it is not spending', () => {
  const text = notItemizedText(line);
  assert.match(text, /^Transfers reported, recipient not itemized/);
  assert.match(text, /FAIRSHAKE \(C00835959, 2025–2026\)/);
  assert.match(text, /\$65,000,000\.00 on the summary, \$51,000,000\.00 itemized/);
  assert.match(text, /Not counted as spending\./);
});

test('the list shows it as its own row, outside the total, after the unlocated rows', () => {
  const html = mapListHtml({kinds: [], unlocated: [{reason: 'a gap', amount: 5, rows: 1}],
                            transfers_not_itemized: [line]}, new Set());
  assert.equal((html.match(/class="map-not-itemized"/g) || []).length, 1);
  assert.match(html, /\$14,000,000\.00<\/td><td class="num">outside the total/);
  assert.ok(html.indexOf('map-unlocated') < html.indexOf('map-not-itemized'));
});

test('a payload with nothing withheld, or written before F6, adds no row', () => {
  for (const body of [{kinds: [], unlocated: [], transfers_not_itemized: []}, {kinds: [], unlocated: []}]) {
    assert.doesNotMatch(mapListHtml(body, new Set()), /map-not-itemized/);
  }
});
