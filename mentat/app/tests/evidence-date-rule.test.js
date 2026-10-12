// F12 (Paul approved 2026-09-25): the Spent map of a saved network counts each reached
// committee from the date the network's money reached it. The server states the rule in
// the map payload's date_rule (evidence_geo.py) and the page words it here: the basis of
// the reconciliation sentence, what the gate left out and what it kept undated, the
// full-period basis of the source gap, and the F6 line's full-period figures.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'evidence.js'), 'utf8');
const context = {
  money: value => value == null ? '—' : '$' + Math.round(Math.abs(Number(value))).toLocaleString('en-US'),
  number: value => Number(value).toLocaleString('en-US'),
  cycleLabel: value => value ? `${Number(value) - 1}–${value}` : 'All cycles',
  isSenateKey: key => /-SEN$/.test(key),
  escapeHtml: value => String(value)
};

// The page's wording functions, read out of the shipped module (the slice the F6 test reads).
function words(source) {
  const start = source.indexOf('  function notItemizedText(item) {');
  const end = source.indexOf('  /* The map view is a SKELETON', start);
  assert.ok(start >= 0 && end > start, 'the map wording block exists in the shipped PAC view');
  return vm.runInNewContext(source.slice(start, end) + `
    ({notItemizedText,
      spentBasis: typeof spentBasis === 'function' ? spentBasis : null,
      dateClauses: typeof dateClauses === 'function' ? dateClauses : null,
      gapClause: typeof gapClause === 'function' ? gapClause : null})`, {...context});
}

// A FAIRSHAKE-shaped 2024 Spent payload after F12 (lane B's measured figures).
const gated = {reported_total: 205029532.31, located_total: 204365760.51, unlocated_total: 663771.8,
  reconciles: false, reconciliation_residual: 1234.5, transfers_not_itemized_basis: 'full_period',
  date_rule: {rule: 'earliest_arrival', total_basis: 'from_gates', residual_basis: 'full_period',
              rows_before_gate: 393, amount_before_gate: 556618.37, rows_date_unverified: 2,
              amount_date_unverified: 1200, before_gate: []}};
const line = {committee: 'C00835959', cycle: '2026', committee_name: 'FAIRSHAKE', amount: 14000000,
              summary_amount: 65000000, itemized_amount: 51000000};

// The assertions, run on the shipped page and (negative control) on the page before F12.
function check(source) {
  const page = words(source);
  assert.equal(typeof page.spentBasis, 'function', 'the page words the dated basis');
  assert.equal(page.spentBasis(gated),
    "saved network disbursements from the date the network's money reached each committee, excluding internal transfers");
  assert.equal(page.dateClauses(gated), ' · $556,618 dated earlier is not counted · $1,200 undated, counted and flagged');
  assert.equal(page.gapClause(gated),
    ' — unresolved source gap (full-period summary vs itemized rows): $1,235. The balancing residual is not verification.');
  assert.equal(page.notItemizedText({...line, basis: gated.transfers_not_itemized_basis}),
    'Transfers reported, recipient not itemized · FAIRSHAKE (C00835959, 2025–2026): $65,000,000 on the '
    + 'full-period summary, $51,000,000 itemized over the same period. Not counted as spending.');
  return page;
}

test('the Spent map words F12 from the payload: basis, gate clauses, gap, F6 line', () => {
  check(SOURCE);
});

test('the dated basis is claimed only when the server states it', () => {
  const page = words(SOURCE);
  for (const body of [{}, {date_rule: null}, {date_rule: {total_basis: 'whole_cycle'}}]) {
    assert.equal(page.spentBasis(body), 'saved network disbursements, excluding internal transfers');
    assert.equal(page.dateClauses(body), '');
  }
  assert.equal(page.dateClauses({date_rule: {...gated.date_rule, amount_before_gate: 0, amount_date_unverified: 0}}), '');
  assert.equal(page.gapClause({reconciliation_residual: 10}),
    ' — unresolved source gap: $10. The balancing residual is not verification.');
  assert.match(page.notItemizedText(line), /\$65,000,000 on the summary, \$51,000,000 itemized\. Not counted/);
});

test('every reconciliation sentence on the page uses the one wording', () => {
  assert.equal((SOURCE.match(/ — unresolved source gap: \$\{money\(body\.reconciliation_residual\)\}/g) || []).length, 0);
  assert.ok((SOURCE.match(/gapClause\(body\)/g) || []).length >= 3);
  assert.match(SOURCE, /\? spentBasis\(body\) : 'reported disbursements'/);
  assert.match(SOURCE, /\+ \(raised \? '' : dateClauses\(body\)\)/);
  assert.equal((SOURCE.match(/notItemizedText\(\{\.\.\.item, basis: body\.transfers_not_itemized_basis\}\)/g) || []).length, 2);
});

test('negative control: the page before F12 fails the same assertions', () => {
  // The shipped block with its F12 wording taken back out: the pre-F12 line, verbatim.
  const start = SOURCE.indexOf('  function notItemizedText(item) {');
  const end = SOURCE.indexOf('  /* Item 7 of F4', start);
  assert.ok(start >= 0 && end > start);
  const before = SOURCE.slice(0, start) + [
    '  function notItemizedText(item) {',
    '    return `Transfers reported, recipient not itemized · ${item.committee_name || item.committee}`',
    '      + ` (${item.committee}, ${cycleLabel(item.cycle)}): ${money(item.summary_amount)} on the summary,`',
    '      + ` ${money(item.itemized_amount)} itemized. Not counted as spending.`;',
    '  }', '', ''].join('\n') + SOURCE.slice(end);
  assert.throws(() => check(before), /the page words the dated basis|Expected values to be strictly equal/);
});

// Carryover (b), landed by lane B 2026-09-26: FAIRSHAKE 2025-2026's Protect Progress and Defend
// American Jobs, whose gates 2023-2024 transfers opened, in the payload's own words.
const carried = {...gated, cycle: '2026', date_rule: {...gated.date_rule, carryover: {rule: 'onward', status: 'partial',
  opened: [{cycle: '2026', committee: 'C00848440', label: 'gate opened by 2023-2024 transfers'},
           {cycle: '2026', committee: 'C00836221', label: 'gate opened by 2023-2024 transfers'},
           {cycle: '2024', committee: 'C00000009', label: 'gate opened by 2021-2022 transfers'}],
  missing_units: [{cycle: '2024', committee: 'C00000001'}]}}};
function checkCarry(source) {
  const page = words(source);
  assert.equal(page.dateClauses(carried), ' · $556,618 dated earlier is not counted · $1,200 undated, counted and flagged'
    + ' · 2 counted from the cycle’s first day (gate opened by 2023-2024 transfers)'
    + ' · carryover partial: some previous-cycle snapshots are not saved');
  const quiet = rule => ({...gated, date_rule: {...gated.date_rule, amount_before_gate: 0, amount_date_unverified: 0, carryover: rule}});
  assert.equal(page.dateClauses(quiet({status: 'unknown', opened: [], missing_units: []})),
    ' · carryover unknown: the previous cycle is not saved');
  assert.equal(page.dateClauses(quiet({status: 'known', opened: [], missing_units: []})), '');
}

test('carryover (b): the Spent map says which gates the previous cycle opened, and the status', () => {
  checkCarry(SOURCE);
});

test('negative control: a page without the carryover clause fails the same assertions', () => {
  const call = "\n      + carryClause(rule, body.cycle);";
  assert.ok(SOURCE.includes(call));
  assert.throws(() => checkCarry(SOURCE.replace(call, ';')), /Expected values to be strictly equal/);
});
