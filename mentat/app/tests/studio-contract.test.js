/* The Chart Studio HTTP contract, from both ends, against the same worked example
   payloads the Python suite sends to the server (mentat/app/studio-contract-examples.json).

   Two defects made this file necessary, and neither was visible to a per-side unit test.
   The server required a saved view's `source` to be an object and the client sent a
   caption, so every Save view answered 400. The server keeps a briefing item's
   provenance as an object and the export code did String(item.provenance), so every
   .pptx and HTML bundle printed "[object Obje" where the workbook SHA-256 belongs.
   Both sides passed their own tests throughout.

   So this file tests both directions. The PRODUCER tests run the real builders the
   browser uses and deep-equal what they make against the payloads below - they fail if
   `source` ever goes back to being a string. The READER tests prove nothing that comes
   back is ever stringified into a slide. */

// Run this suite with the glob form; Node 24 rejects a bare directory:
//   node --test "mentat/app/tests/**/*.test.js"
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../chart-briefing.js');
const core = require('../chart-studio-core.js');

const examples = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'studio-contract-examples.json'), 'utf8'));
const item = examples.briefing_add;
const expected = examples.expected;
const producer = examples.producer;

// The S4 cross-industry contract (audits/mentat_failings_20260915/contracts):
// one file both the node and python suites import, per contracts/README.md.
// Never copy a payload out of it into a literal here - read it at runtime.
const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
function loadContract(name) {
  return JSON.parse(fs.readFileSync(path.join(CONTRACTS, name + '.json'), 'utf8'));
}
const studioApi = loadContract('studio_api');
const snapshotRegistry = loadContract('snapshot_registry');

/* ------------------------------------------------------- the producer: what we send */

test('the builder makes the saved-view payload the server accepts, from the live sheet', () => {
  const made = core.viewSourceRecord(producer.view_verified.sheet_source,
    producer.view_verified.chosen_dataset);
  assert.deepStrictEqual(made, examples.views_save.source);
  // The point of the whole fixture: this is an object on the wire, not a caption.
  assert.strictEqual(typeof made, 'object');
  assert.notStrictEqual(typeof examples.views_save.source, 'string',
    'the contract payload itself must not regress to a caption string');
});

test('a chosen dataset that names its own industry wins over its id', () => {
  // id and industry are the same string in today's catalog, so only a catalog
  // entry that disagrees can show which one the builder actually reads.
  const made = core.viewSourceRecord({}, {id: 'Banking', label: 'Banking', industry: 'Credit Union'});
  assert.strictEqual(made.industry, 'Credit Union');
  assert.strictEqual(made.dataset, 'Banking');
  // No industry on the entry falls back to the id; neither present sets no key.
  assert.strictEqual(core.viewSourceRecord({}, {id: 'Banking', label: 'Banking'}).industry, 'Banking');
  assert.ok(!('industry' in core.viewSourceRecord({}, {id: '', label: ''})));
  // The blank that studio_api.json's negative controls refuse is never written.
  assert.ok(!('industry' in core.chartSourceRecord({reportName: 'Book.xlsx'})));
  assert.ok(!('industry' in core.chartSourceRecord({reportName: 'Book.xlsx', industry: ''})));
});

test('a worksheet with no verified dataset still names its report and digest', () => {
  assert.deepStrictEqual(
    core.viewSourceRecord(producer.view_worksheet.sheet_source,
      producer.view_worksheet.chosen_dataset),
    producer.view_worksheet.expected_source);
});

test('a view recovered from the old browser store sends its caption as an object', () => {
  // This is the path that was still sending a bare string after the first fix, so every
  // migrated view was refused 400 and the refusal was swallowed by a bare catch.
  const made = core.captionSource(producer.view_recovered.label);
  assert.deepStrictEqual(made, producer.view_recovered.expected_source);
  assert.strictEqual(typeof made, 'object');
  assert.deepStrictEqual(core.captionSource(''), {});
});

test('the builder makes the briefing source and provenance the exports read back', () => {
  assert.deepStrictEqual(core.chartSourceRecord(producer.chart.sheet_source), item.source);
  assert.deepStrictEqual(
    core.chartProvenanceRecord(producer.chart.sheet_source, producer.chart.sha,
      producer.chart.generated_at),
    item.provenance);
});

test('no builder can be talked into emitting a string, whatever it is handed', () => {
  const inputs = [undefined, null, {}, 'a caption', {reportName: 'Book.xlsx'}];
  for (const input of inputs) {
    for (const made of [core.viewSourceRecord(input, null), core.chartSourceRecord(input),
                        core.chartProvenanceRecord(input, ''), core.captionSource(input)]) {
      assert.strictEqual(typeof made, 'object', 'builder returned ' + typeof made);
      assert.ok(made !== null);
    }
  }
});

test('what the builder makes round-trips back through the readers', () => {
  const source = core.viewSourceRecord(producer.view_verified.sheet_source,
    producer.view_verified.chosen_dataset);
  assert.strictEqual(core.sourceLabelOf(source), expected.view_source_caption);
  const provenance = core.chartProvenanceRecord(producer.chart.sheet_source, producer.chart.sha,
    producer.chart.generated_at);
  assert.strictEqual(model.provenanceSha({provenance}), expected.workbook_sha256);
  assert.strictEqual(model.shortSha({provenance}), expected.short_sha);
});

/* ------------------------------------------------------- the readers: what comes back */

test('the example item yields the real workbook digest, not a stringified object', () => {
  assert.strictEqual(model.provenanceSha(item), expected.workbook_sha256);
  assert.strictEqual(model.shortSha(item), expected.short_sha);
});

test('the source caption comes off the object, so the picker never prints one', () => {
  assert.strictEqual(model.sourceText(item), expected.source_caption);
});

test('the export source line carries the true short SHA and never the word object', () => {
  const line = model.sourceNoteText(item, 'September 14, 2026');
  assert.ok(line.includes(expected.short_sha),
    'the short SHA-256 must appear verbatim in the line every export carries: ' + line);
  assert.ok(!line.includes('[object'),
    'a stringified object reached the export line: ' + line);
  assert.ok(line.includes(expected.source_caption), line);
  assert.ok(line.includes(item.basis), line);
});

test('no reader ever stringifies an object, whatever shape the item arrives in', () => {
  const shapes = [
    item,
    {provenance: {}, source: {}},
    {provenance: {workbook_sha256: expected.workbook_sha256}, source: {reportName: 'Book.xlsx'}},
    {provenance: expected.workbook_sha256, source: expected.source_caption},
    {provenance: null, source: undefined},
    {},
    {provenance: {generatedAt: '2026-09-14T16:30:00.000Z'}, source: {mode: 'contributions'}}
  ];
  for (const shape of shapes) {
    const line = model.sourceNoteText(shape, 'September 14, 2026');
    assert.ok(!line.includes('[object'), 'stringified object for ' + JSON.stringify(shape));
    assert.strictEqual(typeof model.provenanceSha(shape), 'string');
    assert.strictEqual(typeof model.sourceText(shape), 'string');
    assert.strictEqual(typeof core.sourceLabelOf(shape.source), 'string');
  }
});

test('an item with no digest at all says so in words rather than printing nothing', () => {
  const line = model.sourceNoteText({provenance: {}, source: {}, basis: ''}, 'September 14, 2026');
  assert.ok(line.includes('SHA-256 unstated'), line);
  assert.ok(line.includes('basis unstated'), line);
});

test('what the browser sends the server is an object, so a caption is wrapped not dropped', () => {
  // The server replaces a non-object with an empty one, which is how a digest sent as
  // a bare string vanished between the console and the slide.
  assert.deepStrictEqual(model.provenanceRecord(expected.workbook_sha256),
    {sha: expected.workbook_sha256});
  assert.deepStrictEqual(model.sourceRecord(expected.source_caption),
    {label: expected.source_caption});
  assert.deepStrictEqual(model.provenanceRecord(item.provenance), item.provenance);
  assert.deepStrictEqual(model.sourceRecord(item.source), item.source);
  assert.deepStrictEqual(model.provenanceRecord(''), {});
  assert.deepStrictEqual(model.sourceRecord(null), {});
});

test('a record that survives the round trip still reads back as the same digest', () => {
  const sent = model.provenanceRecord(item.provenance);
  assert.strictEqual(model.provenanceSha({provenance: sent}), expected.workbook_sha256);
  const wrapped = model.provenanceRecord(expected.workbook_sha256);
  assert.strictEqual(model.provenanceSha({provenance: wrapped}), expected.workbook_sha256);
});

/* --------------------------------------------------- S4 cross-industry contract */
// contracts/studio_api.json and contracts/snapshot_registry.json (S4_DESIGN.md,
// Area E). Every assertion below reads its expected values out of the loaded
// contract file rather than a literal copied into this file, per
// contracts/README.md's "assertions run against producers" rule.

test('[studio_api] viewSourceRecord emits industry for a contributions source, matching the fixture exactly', () => {
  const want = studioApi.examples.views_save.source;
  const chosen = {id: want.dataset, label: want.datasetLabel, industry: want.industry};
  const state = {reportName: want.reportName, sha256: want.sha256, runName: want.runName,
    sheet: want.sheet, mode: want.mode, industry: want.industry};
  const made = core.viewSourceRecord(state, chosen);
  assert.strictEqual(made.industry, want.industry);
  assert.deepStrictEqual(made, want);
});

test('[studio_api] createSheet carries an empty ordered series-sources array', () => {
  assert.deepStrictEqual(core.createSheet('Sheet 1', 's1').sources, []);
});

test('[studio_api] migrateSheet backfills industry and normalizes sources, matching migration_input -> migration_output exactly', () => {
  assert.deepStrictEqual(core.migrateSheet(studioApi.examples.migration_input), studioApi.examples.migration_output);
});

test('[studio_api] chartSourceRecord emits industry for a briefing source', () => {
  const sheetSource = studioApi.examples.views_save.sheets[1].source; // the Credit Union sheet
  const made = core.chartSourceRecord(sheetSource);
  assert.strictEqual(made.industry, sheetSource.industry);
  assert.strictEqual(made.dataset, sheetSource.dataset);
});

test('[studio_api] negative control with teeth: a source without industry yields no industry key at all', () => {
  const made = core.viewSourceRecord({reportName: 'Book.xlsx'}, null);
  assert.ok(!('industry' in made), 'viewSourceRecord must not invent an industry key when nothing supplied one');
});

test('[studio_api] negative control with teeth: a sources[] entry lacking industry is dropped by migrateSheet', () => {
  // negative_controls[1] in the contract: a sources[] entry with dataset/label but no
  // industry, which openability() could never resolve against the registry.
  const control = studioApi.negative_controls[1];
  const sheet = Object.assign({}, studioApi.examples.migration_input, {sources: control.payload});
  const migrated = core.migrateSheet(sheet);
  assert.deepStrictEqual(migrated.sources, [], control.why);
});

test('[snapshot_registry] parseSnapshotRegistry accepts every example envelope', () => {
  assert.strictEqual(snapshotRegistry.examples.length > 0, true, 'the contract must carry at least one example');
  for (const envelope of snapshotRegistry.examples) {
    assert.doesNotThrow(() => core.parseSnapshotRegistry(envelope), 'a valid registry envelope must parse');
    assert.doesNotThrow(() => core.parseSnapshotRegistry(envelope.snapshots),
      'the bare snapshots array (session.industries shape) must parse too');
  }
});

test('[snapshot_registry] parseSnapshotRegistry rejects every negative control, wrapped as a single-entry list', () => {
  assert.strictEqual(snapshotRegistry.negative_controls.length > 0, true,
    'the contract must carry at least one negative control');
  for (const control of snapshotRegistry.negative_controls) {
    assert.throws(() => core.parseSnapshotRegistry([control.payload]), Error, control.why);
  }
});

/* ------------------------------------------------- studio_api v2: the board */
// contracts/studio_api.json v2 (UI_CLEANUP_DESIGN_2026-09-17.md section 4,
// option C): a saved view MAY carry a top-level board {tiles, slots}.
// normalizeBoard is the producer the contract names, so every example and every
// control below goes through it rather than through the rule written out again.

test('[studio_api] the contract is v2 and carries the board examples and controls', () => {
  // v3 (lane M) adds marks cards; every v2 board example and control is kept.
  assert.strictEqual(studioApi.version, 3);
  for (const name of ['views_save_board', 'views_get_board', 'views_save_board_four']) {
    assert.ok(studioApi.examples[name], 'the contract must carry example ' + name);
  }
});

test('[studio_api] normalizeBoard returns the two-tile board exactly as the view carries it', () => {
  const view = studioApi.examples.views_save_board;
  assert.deepStrictEqual(core.normalizeBoard(view), view.board);
  assert.deepStrictEqual(view.board, {tiles: 2, slots: [0, 1]});
});

test('[studio_api] normalizeBoard reads back the board a saved view returns', () => {
  const view = studioApi.examples.views_get_board;
  assert.deepStrictEqual(core.normalizeBoard(view), view.board);
});

test('[studio_api] normalizeBoard accepts four tiles with two of them empty', () => {
  const view = studioApi.examples.views_save_board_four;
  assert.deepStrictEqual(core.normalizeBoard(view), view.board);
  assert.strictEqual(view.board.slots.filter(slot => slot === null).length, 2);
  // active names the sheet, and in board mode the selected tile is the slot
  // holding it: sheet 1 sits in slot 0 here.
  assert.strictEqual(view.board.slots.indexOf(view.active), 0);
});

test('[studio_api] a view with no board is never given one', () => {
  assert.strictEqual(core.normalizeBoard(studioApi.examples.views_save), null);
  assert.strictEqual(core.normalizeBoard(studioApi.examples.views_get), null);
});

test('[studio_api] negative control with teeth: normalizeBoard refuses every malformed board', () => {
  const controls = studioApi.negative_controls.filter(control => control.payload
    && typeof control.payload === 'object' && !Array.isArray(control.payload) && 'board' in control.payload);
  assert.strictEqual(controls.length, 6, 'the contract carries six board controls');
  for (const control of controls) {
    assert.strictEqual(core.normalizeBoard(control.payload), null, control.why);
  }
});
