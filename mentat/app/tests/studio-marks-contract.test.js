/* studio_api.json v3: marks cards (lane M, boards #50-#52, 2026-09-25).

   The contract's v3 examples were produced by writeCards/serializeCard and
   every assertion below runs those producers again, so the file and the
   client cannot drift apart (contracts/README.md, DE-021). The same examples
   and negative controls are sent to a real Console by
   tests/test_studio_marks_contract.py.

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const core = require('../chart-studio-core.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const studioApi = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'studio_api.json'), 'utf8'));
const ex = studioApi.examples;
const clone = value => JSON.parse(JSON.stringify(value));
// Lane P's map-layer controls (lane: 'P') are asserted by map-layers-contract.test.js.
const v3Controls = () => studioApi.negative_controls.filter(control => control.payload && Array.isArray(control.payload.sheets)
  && control.lane !== 'P' && control.payload.sheets.some(sheet => 'marksCards' in sheet || 'syncAxes' in sheet));

test('[marks v3] the contract is v3 and carries the marks examples and their controls', () => {
  assert.equal(studioApi.version, 3);
  for (const name of ['marks_card', 'views_save_marks_dual', 'views_get_marks_dual', 'views_save_marks_shape',
    'marks_v2_sheet', 'marks_v2_cards', 'marks_v2_written']) assert.ok(ex[name], 'missing example ' + name);
  // Lane M's fourteen and lane F's nine Flow-card controls (lane: 'F'); lane P's map controls are its own.
  assert.equal(v3Controls().length, 23);
  for (const control of v3Controls()) assert.ok(!('board' in control.payload), 'a marks control is not a board control');
});

test('[marks v3] a single v2 card is read as one card and written back byte-identical', () => {
  const sheet = clone(ex.marks_v2_sheet);
  const cards = core.readCards(sheet);
  assert.deepEqual(cards, ex.marks_v2_cards);
  assert.equal(cards.length, 1);
  const before = JSON.stringify(sheet);
  core.writeCards(sheet, cards);
  assert.equal(JSON.stringify(sheet), before, 'the v2 write-back must not change one byte');
  assert.equal(JSON.stringify(sheet), JSON.stringify(ex.marks_v2_written));
  assert.ok(!('marksCards' in sheet), 'no marksCards key for a card without v3 fields');
  // Every sheet of every v2 example keeps its bytes the same way.
  for (const view of [ex.views_save, ex.views_get, ex.views_save_board]) {
    for (const original of view.sheets) {
      const copy = clone(original);
      core.writeCards(copy, core.readCards(copy));
      assert.equal(JSON.stringify(copy), JSON.stringify(original));
    }
  }
});

test('[marks v3] the producers write the dual-axis example exactly', () => {
  const saved = ex.views_save_marks_dual.sheets[0];
  const cards = core.readCards(saved);
  assert.equal(cards.length, 2);
  assert.deepEqual(cards.map(card => card.type), ['bar', 'line']);
  const rebuilt = clone(saved);
  delete rebuilt.marksCards; delete rebuilt.syncAxes;
  rebuilt.marks = {type: 'bar', color: null, size: null, label: null, detail: [], tooltip: []};
  core.writeCards(rebuilt, cards, {syncAxes: true});
  assert.equal(JSON.stringify(rebuilt), JSON.stringify(saved));
  // marks mirrors the first card, so a console without v3 opens the bars.
  assert.deepEqual(saved.marks, core.marksFromCard(cards[0]));
  assert.equal(saved.syncAxes, true);
});

test('[marks v3] the Shape example round-trips through serializeCard and keeps its v3 fields', () => {
  const sheet = clone(ex.views_save_marks_shape.sheets[0]);
  const [card] = core.readCards(sheet);
  assert.deepEqual(core.serializeCard(card), ex.marks_card);
  assert.equal(card.type, 'shape');
  assert.deepEqual(card.encodings.shape.glyphs, {DEM: 'circle', REP: 'triangle-up'});
  assert.equal(core.hasV3Fields(card), true);
  const again = clone(sheet);
  core.writeCards(again, core.readCards(again));
  assert.equal(JSON.stringify(again), JSON.stringify(sheet));
});

test('[marks v3] the view read back holds exactly the keys views_get_marks_dual names', () => {
  assert.deepEqual(Object.keys(ex.views_get_marks_dual).sort(), Object.keys(ex.views_get).sort());
  assert.deepEqual(ex.views_get_marks_dual.sheets, ex.views_save_marks_dual.sheets);
});

test('[marks v3] migrateSheet keeps every valid v3 sheet byte-stable (save -> open -> save)', () => {
  for (const view of [ex.views_save_marks_dual, ex.views_save_marks_shape]) {
    for (const sheet of view.sheets) {
      const opened = core.migrateSheet(clone(sheet));
      assert.equal(JSON.stringify(opened), JSON.stringify(core.migrateSheet(sheet)));
      assert.deepEqual(opened.marksCards, sheet.marksCards);
      assert.deepEqual(opened.marks, sheet.marks);
    }
  }
});

test('[marks v3] negative controls with teeth: every malformed card set is refused, and dropped on open', () => {
  for (const control of v3Controls()) {
    const faults = control.payload.sheets.map(sheet => core.validateMarksCards(sheet));
    assert.ok(faults.some(Boolean), 'accepted: ' + control.why);
    for (const sheet of control.payload.sheets) {
      if (!core.validateMarksCards(sheet)) continue;
      const opened = core.migrateSheet(clone(sheet));
      assert.ok(!('marksCards' in opened) && !('syncAxes' in opened), 'kept on open: ' + control.why);
      assert.ok(opened.marks && typeof opened.marks === 'object', 'the sheet still opens as its v2 marks');
    }
  }
  // The teeth: the valid examples the controls were cut from pass.
  for (const view of [ex.views_save_marks_dual, ex.views_save_marks_shape])
    for (const sheet of view.sheets) assert.equal(core.validateMarksCards(sheet), '');
});

test('[marks v3] a dual axis follows Rows: a removed measure collapses it to one card', () => {
  const columns = Array.from({length: 14}, (_, index) => ({index, name: 'F' + index,
    type: [11, 12, 13].includes(index) ? 'number' : 'category'}));
  const sheet = clone(ex.views_save_marks_dual.sheets[0]);
  sheet.rows = sheet.rows.slice(0, 1);
  core.syncCardsToRows(sheet, columns);
  assert.ok(!('marksCards' in sheet), 'one measure left, one card left');
  assert.equal(sheet.marks.type, 'bar');
  const again = clone(ex.views_save_marks_dual.sheets[0]);
  again.rows = [...again.rows].reverse();
  core.syncCardsToRows(again, columns);
  assert.deepEqual(again.marksCards.map(card => card.type), ['line', 'bar'], 'each measure keeps its own card');
  assert.equal(core.validateMarksCards(again), '');
});
