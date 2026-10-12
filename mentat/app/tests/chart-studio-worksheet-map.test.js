/* Census C-16: Map and Filled map on a report worksheet draw real maps.

   Paul, 2026-09-23: "Additionally, within the Chart Studio make sure
   everything is freely flexible, that all graph types properly register and
   nothing is logically constrained, it needs to be utterly maluable."

   Until this round a worksheet drew both maps as bars ("Maps draw from
   Verified contributions"). Now any worksheet with a ZIP, state, district or
   city/state field is mapped: every row is placed, the sheet's own measure is
   aggregated per place by the engine, and MentatContributionMap draws it with
   a coverage line in that measure (never a selected amount). A sheet with no
   place field keeps the honest bars-with-a-note fallback.

   Run: node --test "app/tests/**"/"*.test.js"   (cwd: mentat/) */
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../chart-studio-core.js');
const math = require('../chart-math.js');
const map = require('../contribution-map.js');

// Shaped like the published Beneficiary Summary and Employee List sheets.
const COLUMNS = [
  {index: 0, name: 'Cycle', type: 'category'},
  {index: 1, name: 'Candidate / Officeholder', type: 'category'},
  {index: 2, name: 'Office', type: 'category'},
  {index: 3, name: 'State', type: 'category'},
  {index: 4, name: 'District', type: 'category'},
  {index: 5, name: 'Downstream Activity', type: 'number', unit: 'currency'},
  {index: 6, name: 'Cities / States', type: 'category'},
  {index: 7, name: 'ZIPs', type: 'category'}
];
const ROWS = [
  ['2025-2026', 'A', 'House', 'MN', '5', 100, 'MINNEAPOLIS, MN', '55401; 554011234'],
  ['2025-2026', 'B', 'House', 'MN', '5', 300, 'SAINT PAUL, MN; ST PAUL, MN', '55101'],
  ['2025-2026', 'C', 'Senate', 'PA', '0', 50, 'NEW YORK, NY; BOSTON, MA', '10019; 02134'],
  ['2025-2026', 'D', 'House', 'AK', '0', 20, 'ANCHORAGE, AK', '99501'],
  ['2025-2026', 'E', 'House', 'new york', '12', 30, 'LONDON, ZZ', 'W25JR'],
  ['2025-2026', 'F', 'House', 'NJ', '7', 10, 'ESSEX FELLS, NJ', '7021; 70211709']
];
const DATA = {sheet: 'Beneficiary Summary', header_row: 1, columns: COLUMNS, rows: ROWS,
  row_numbers: ROWS.map((_, i) => i + 2), row_count: ROWS.length, sha256: 'test'};
const [CYCLE, NAME, OFFICE, STATE, DISTRICT, ACTIVITY, CITY, ZIP] = [0, 1, 2, 3, 4, 5, 6, 7];
const PAUL = 'nothing is logically constrained, it needs to be utterly maluable';
const sheet = patch => Object.assign(core.createSheet('Sheet 1', 's1'), patch);
const marks = type => Object.assign(core.createSheet('x').marks, {type});
const pill = column => ({column, aggregation: 'sum'});

test('geoKind reads the place a worksheet field holds from its name or role', () => {
  const kind = name => core.geoKind({name});
  assert.equal(kind('ZIPs'), 'zip');
  assert.equal(kind('Contributor ZIP'), 'zip');
  assert.equal(kind('zip5'), 'zip');
  assert.equal(kind('Cities / States'), 'city-state');
  assert.equal(kind('District'), 'district');
  assert.equal(kind('State'), 'state');
  assert.equal(kind('Recipient State'), 'state');
  assert.equal(kind('Statement Date'), '', 'a word that only starts with "state" is not a place');
  assert.equal(kind('Recipient'), '');
  assert.equal(core.geoKind({name: 'Where', geographic_role: 'state'}), 'state', 'a geographic role wins over the name');
});

test('placeOf reads one place per cell, and says when a cell names several', () => {
  assert.equal(core.placeOf('zip', '55401; 554011234'), '55401', 'ZIP+4 is its five-digit ZIP');
  assert.equal(core.placeOf('zip', '7021; 70211709'), '07021', 'a dropped leading zero is restored');
  assert.equal(core.placeOf('zip', 7021), '07021', 'a ZIP read as a number too');
  assert.equal(core.placeOf('zip', '00000; W25JR'), core.NO_PLACE);
  assert.equal(core.placeOf('zip', '10019; 02134'), core.SEVERAL_PLACES);
  assert.equal(core.placeOf('zip', '10019; 02134', null, true), '10019; 02134', 'split multi-value cells keeps each place');
  assert.equal(core.placeOf('state', 'new york'), 'NY', 'a state name is its postal code');
  assert.equal(core.placeOf('state', 'ZZ'), core.NO_PLACE);
  assert.equal(core.placeOf('city-state', 'SAINT PAUL, MN; ST PAUL, MN'), 'MN', 'a city is placed at its state');
  assert.equal(core.placeOf('city-state', 'LONDON, ZZ; NEW YORK, NY'), 'NY', 'a foreign entry is not a US place');
  assert.equal(core.placeOf('district', '5', 'MN'), 'MN-05', 'a bare district number takes its row state');
  assert.equal(core.placeOf('district', 'MN-AL'), 'MN-00');
  assert.equal(core.placeOf('district', '5', ''), core.NO_PLACE, 'a district with no state has no place');
});

test('a worksheet map with a place field compiles to a real map, not bars with a refusal', () => {
  const report = core.compileReport(sheet({columns: [pill(STATE)], rows: [pill(ACTIVITY)], marks: marks('map')}), COLUMNS);
  assert.deepEqual(report.issues, [], PAUL);
  assert.deepEqual(report.map, {kind: 'state', column: STATE, stateColumn: -1, filled: false, name: 'State'});
  assert.ok(!report.notes.some(n => /Maps (draw|need)/.test(n)), 'no bars fallback note: ' + report.notes.join(' | '));
  assert.equal(report.config.type, 'bar', 'the fallback configuration is still bars of the same places');
});

test('the finest place wins on a symbol map, a shape on a filled map; a district takes its state', () => {
  const both = type => core.compileReport(sheet({columns: [pill(STATE), pill(ZIP)], rows: [pill(ACTIVITY)], marks: marks(type)}), COLUMNS).map;
  assert.equal(both('map').kind, 'zip');
  assert.equal(both('filled-map').kind, 'state');
  const district = core.compileReport(sheet({columns: [pill(DISTRICT)], rows: [pill(ACTIVITY)], marks: marks('filled-map')}), COLUMNS);
  assert.deepEqual(district.map, {kind: 'district', column: DISTRICT, stateColumn: STATE, filled: true, name: 'District'});
  const combined = core.compileReport(sheet({columns: [pill(STATE), pill(OFFICE)], rows: [pill(ACTIVITY)], marks: marks('map')}), COLUMNS);
  assert.ok(combined.notes.some(n => /combines every Office group at its place/.test(n)), combined.notes.join(' | '));
});

test('with no place on the shelves the sheet’s own place field is picked and named', () => {
  const report = core.compileReport(sheet({columns: [pill(NAME)], rows: [pill(ACTIVITY)], marks: marks('filled-map')}), COLUMNS);
  assert.equal(report.map.kind, 'state', 'a picked filled map prefers the state, which every row has');
  assert.ok(report.notes.some(n => /uses State/.test(n)), report.notes.join(' | '));
});

test('a worksheet with no place field keeps the honest bars-with-a-note fallback', () => {
  const plain = [{index: 0, name: 'Company', type: 'category'}, {index: 1, name: 'Total Giving', type: 'number'}];
  const report = core.compileReport(sheet({columns: [pill(0)], rows: [pill(1)], marks: marks('map')}), plain);
  assert.equal(report.map, null);
  assert.equal(report.config.type, 'bar');
  assert.ok(report.notes.some(n => /^Maps need a ZIP, state, congressional district or city field and this worksheet has none, so it is drawn as bars by Company\.$/.test(n)),
    report.notes.join(' | '));
});

const mapped = state => {
  const report = core.compileReport(state, COLUMNS);
  const placed = core.placeData(DATA, report.map, !!report.config.splitMulti);
  const config = core.mapConfig(report.config, placed.column);
  return {report, config, result: math.build(placed.data, config), placed};
};

test('the engine aggregates the sheet’s own measure per place, over every place', () => {
  const {config, result} = mapped(sheet({columns: [pill(CITY)], rows: [pill(ACTIVITY)], marks: marks('map')}));
  assert.equal(config.maxGroups, 'all', 'a map has no top-N cutoff');
  const groups = Object.fromEntries(core.mapGroups(result, 'city-state').map(g => [g.label, g]));
  assert.equal(groups.MN.value, 400, 'two Minnesota rows add up at their state');
  assert.deepEqual([groups.MN.state, groups.AK.state, groups.NJ.state], ['MN', 'AK', 'NJ']);
  assert.equal(groups[core.SEVERAL_PLACES].unplaced, 'several_places');
  // An average is taken over the place's rows, never added up from pieces.
  const mean = mapped(sheet({columns: [pill(STATE)], rows: [{column: ACTIVITY, aggregation: 'mean'}], marks: marks('map')}));
  assert.equal(core.mapGroups(mean.result, 'state').find(g => g.label === 'MN').value, 200);
});

test('districts are Census GEOIDs from the row state; split cells map each place', () => {
  const {result} = mapped(sheet({columns: [pill(DISTRICT)], rows: [pill(ACTIVITY)], marks: marks('filled-map')}));
  const groups = Object.fromEntries(core.mapGroups(result, 'district').map(g => [g.label, g]));
  assert.deepEqual([groups['MN-05'].district, groups['MN-05'].value], ['2705', 400]);
  assert.equal(groups['NY-12'].district, '3612', 'a state written as a name still places its district');
  assert.equal(groups['AK-00'].district, '0200');
  const splitSheet = sheet({columns: [pill(ZIP)], rows: [pill(ACTIVITY)], marks: marks('map')});
  splitSheet.params = {...splitSheet.params, splitMulti: true};
  const split = mapped(splitSheet);
  const labels = core.mapGroups(split.result, 'zip').map(g => g.label).sort();
  assert.ok(labels.includes('10019') && labels.includes('02134'), labels.join(','));
});

test('the map reads a worksheet measure: value coverage, ZIPs without a state, districts', () => {
  const geometry = {points: [{zip5: '55401', longitude: -93.27, latitude: 44.98}],
    layers: {states: {features: [{properties: {STUSAB: 'MN', INTPTLON: -94.3, INTPTLAT: 46.3}}]},
      districts: {features: [{properties: {GEOID: '2705', INTPTLON: -93.3, INTPTLAT: 45.0}}]}}};
  const zips = map.prepare([{label: '55401', zip5: '55401', value: 400, records: 2}, {label: '10019', zip5: '10019', value: 50, records: 1},
    {label: core.SEVERAL_PLACES, unplaced: 'several_places', value: 7, records: 1}],
    geometry, {valueField: 'value', unit: 'dollars', coverage: 'value', zipOnly: true});
  assert.equal(zips.marks.length, 1, 'a ZIP with no recorded state is placed on a worksheet');
  assert.deepEqual([zips.selected_value, zips.mapped_value, zips.unmapped_value], [457, 400, 57]);
  assert.deepEqual([zips.selected_cents, zips.mapped_cents], [0, 0], 'no selected amount is invented');
  assert.match(zips.coverage_notes.join(' '), /1 group names several different places in one cell \(\$7\.00\)/);
  assert.match(zips.coverage_notes.join(' '), /1 place has no Census location \(\$50\.00\)/);
  const districts = map.prepare([{label: 'MN-05', state: 'MN', district: '2705', value: 3}, {label: 'PA-00', state: 'PA', district: '4200', value: 1}],
    geometry, {valueField: 'value', unit: 'count', coverage: 'places'});
  assert.deepEqual(districts.marks.map(m => [m.kind, m.label]), [['district', 'MN-05']]);
  assert.equal(districts.unmapped.length, 1);
  assert.match(districts.coverage_notes.join(' '), /^1 place has no Census location and stays unmapped\.$/, 'a places count names no summed value');
});

test('a map keeps its place when period analysis is chosen, and says the analysis is left off', () => {
  const state = sheet({columns: [pill(STATE), pill(CYCLE)], rows: [pill(ACTIVITY)], marks: marks('map')});
  state.analytics = {...state.analytics, transform: 'annual'};
  const report = core.compileReport(state, COLUMNS);
  assert.equal(report.map.kind, 'state');
  assert.equal(report.config.transform, 'none');
  assert.equal(report.config.x, STATE, 'the place stays the grouping');
  assert.ok(report.notes.some(n => /period analysis needs a chart with a time axis, so it is left off/.test(n)), report.notes.join(' | '));
});

test('Show Me seeds a worksheet map with its place field', () => {
  const symbol = core.showMeApply('symbol-map', sheet({}), COLUMNS, []);
  const filled = core.showMeApply('filled-map', sheet({}), COLUMNS, []);
  assert.deepEqual(core.shelf(symbol.columns), [pill(ZIP)], 'a symbol map starts from the finest place');
  assert.deepEqual(core.shelf(filled.columns), [pill(STATE)], 'a filled map starts from the state, which every row has');
  assert.equal(filled.marks.type, 'filled-map');
  assert.doesNotMatch(core.SHOW_ME.find(t => t.id === 'symbol-map').note, /Maps draw from Verified/);
});
