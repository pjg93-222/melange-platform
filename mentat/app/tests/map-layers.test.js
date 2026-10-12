/* Map layers (lane P, boards #47-#55): the model, the placement arithmetic,
   the hexagon and frame math, density and the presets.

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const layers = require('../map-layers.js');
const core = require('../chart-studio-core.js');

const APP = path.resolve(__dirname, '..');
const V2 = path.join(APP, 'geo', 'v2');
const CONTRACTS = path.resolve(APP, '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const haveV2 = fs.existsSync(path.join(V2, 'lookup', 'hex_pins.json'));
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));

// A lookup over hand-made tables, shaped as geo/v2/lookup is.
function fakeLookup() {
  const tables = {
    '/geo/v2/lookup/states.json': {rows: {'36': ['NY', 'New York', 'lower48', true, 42.9, -75.5], '06': ['CA', 'California', 'lower48', true, 37.3, -120.0]},
      by_usps: {NY: '36', CA: '06'}},
    '/geo/v2/lookup/zcta/1.json': {fields: ['lat', 'lon', 'state', 'county', 'tract', 'place', 'cd116', 'cd118', 'cd119', 'sldu', 'sldl', 'h10', 'h25', 'h50'],
      rows: {'10001': [40.75065, -73.9973, '36', '36061', '36061010300', '3651000', '3612', '3612', '3612', '36047', '36075', 'h10:35:157', 'h25:14:63', 'h50:7:31'],
        '10003': [40.73186, -73.98912, '36', '36061', '36061004200', '3651000', '3612', '3610', '3610', '36027', '36066', 'h10:35:157', 'h25:14:63', 'h50:7:31']}},
    '/geo/v2/lookup/zcta/9.json': {fields: ['lat', 'lon', 'state', 'county', 'tract', 'place', 'cd116', 'cd118', 'cd119', 'sldu', 'sldl', 'h10', 'h25', 'h50'],
      rows: {'94607': [37.8, -122.28, '06', '06001', '06001402800', '0653000', '0613', '0612', '0612', '06007', '06018', 'h10:-150:130', 'h25:-60:52', 'h50:-30:26']}}};
  return layers.createLookup(async name => tables[name] || null);
}
const ROWS = [
  {zip: '10001', state: 'NY', amount: 100, rows: 2, contributors: 2, precision: 'zip'},
  {zip: '10003', state: 'NY', amount: 50, rows: 1, contributors: 1, precision: 'zip'},
  {zip: '94607', state: 'CA', amount: 25, rows: 1, contributors: 1, precision: 'zip'},
  {zip: '99999', state: 'CA', amount: 7, rows: 1, contributors: 1, precision: 'zip'},
  {zip: '', state: 'NY', amount: 5, rows: 1, contributors: 1, precision: 'state'},
  {zip: '', state: '', amount: 3, rows: 1, contributors: 0, precision: 'none', reason: 'no usable ZIP or state'}];

test('[map layers] the EPSG:5070 port and the hexagon ids reproduce every pyproj pin', {skip: !haveV2 && 'geo/v2 not installed'}, () => {
  const pins = readJson(path.join(V2, 'lookup', 'hex_pins.json')).points;
  assert.equal(pins.length, 1000);
  let worst = 0;
  for (const [lat, lon, x, y, h10, h25, h50] of pins) {
    const [X, Y] = layers.albers5070(lat, lon);
    worst = Math.max(worst, Math.abs(X - x), Math.abs(Y - y));
    assert.equal(layers.hexId(lat, lon, 10), h10);
    assert.equal(layers.hexId(lat, lon, 25), h25);
    assert.equal(layers.hexId(lat, lon, 50), h50);
  }
  assert.ok(worst < 0.001, 'EPSG:5070 within 1 mm, worst ' + worst);
  assert.equal(layers.hexId(0, 0, 25), null, 'the geocoder’s no-answer point has no hexagon');
  assert.equal(layers.hexId(40, -74, 20), null, 'only 10, 25 and 50 mile grids exist');
});

test('[map layers] the frame projection places every ZCTA where geo/zcta_points.json put it', {skip: !haveV2 && 'geo/v2 not installed'}, () => {
  const shipped = readJson(path.join(APP, 'geo', 'zcta_points.json')).points;
  let checked = 0, worst = 0;
  for (const digit of '0123456789') {
    const table = readJson(path.join(V2, 'lookup', 'zcta', digit + '.json'));
    const at = name => table.fields.indexOf(name);
    for (const [zip, values] of Object.entries(table.rows)) {
      const expected = shipped[zip];
      if (!expected) continue;
      const point = layers.projectFrame(values[at('lat')], values[at('lon')], values[at('state')]);
      worst = Math.max(worst, Math.hypot(point[0] - expected[0], point[1] - expected[1]));
      checked += 1;
    }
  }
  assert.ok(checked > 33000, 'checked ' + checked);
  assert.ok(worst <= 0.1, 'within the shipped file’s 0.1 px rounding, worst ' + worst);
});

test('[map layers] FRAME is the frame cd119.json ships: Alaska at 0.35 of the lower 48, Attu at the west end of the Aleutians', () => {
  // Lane L2, 2026-09-26: d3's geoAlbersUsa draws Alaska at 0.35 x the lower-48 scale on a conic whose
  // rotation wraps longitude. Unwrapped, the Near Islands (east of 180 degrees) drew at the top left of
  // the inset and the fit that held them shrank Alaska to 0.236 x.
  const shipped = readJson(path.join(APP, 'geo', 'cd119.json'));
  for (const [name, region] of Object.entries(shipped.projection.regions)) {
    const mine = layers.FRAME.regions[name];
    assert.ok(mine, name + ' is in FRAME');
    for (const key of ['parallels', 'meridian', 'origin', 'scale', 'translate', 'statefp', 'clip']) assert.deepEqual(mine[key], region[key], name + '.' + key);
  }
  const {alaska, lower48} = layers.FRAME.regions;
  assert.ok(Math.abs(alaska.scale / lower48.scale - 0.35) < 1e-6, 'Alaska scale ' + alaska.scale);
  const [x0, y0, x1, y1] = shipped.states.AK.bbox;
  const attu = layers.projectFrame(52.9, 172.9, '02'), adak = layers.projectFrame(51.9, -176.6, '02');
  assert.ok(attu[0] >= x0 && attu[0] <= x1 && attu[1] >= y0 && attu[1] <= y1, 'Attu inside the Alaska outline box: ' + attu);
  assert.ok(attu[0] < adak[0] && Math.abs(attu[1] - adak[1]) < 30, 'Attu west of Adak on the chain: ' + attu + ' / ' + adak);
});

test('[map layers] Hawaii is clipped as d3 clips it: the Northwestern Hawaiian Islands are not placed, the main islands and Kaula are', () => {
  // Lane L3, 2026-09-26: the Hawaii conic drew the Northwestern Hawaiian Islands west of the Hawaii box,
  // French Frigate Shoals inside the Alaska box. The clip extent is the canvas round the Hawaii box, a
  // pixel clear of the Alaska, lower-48 and American Samoa boxes; the main islands and Kaula stay.
  const shipped = readJson(path.join(APP, 'geo', 'cd119.json'));
  const hawaii = layers.FRAME.regions.hawaii;
  assert.deepEqual(hawaii.clip, shipped.projection.regions.hawaii.clip, 'FRAME carries the clip cd119.json ships');
  const ALASKA_BOX = [10, 366, 246, 600];                 // build_district_geo REGIONS['alaska']['box']
  const inside = (p, box) => p[0] >= box[0] && p[0] <= box[2] && p[1] >= box[1] && p[1] <= box[3];
  for (const [name, lat, lon] of [['Honolulu', 21.3069, -157.8583], ['Hilo', 19.7241, -155.0868],
    ['Kaula', 21.656, -160.542], ['Cape Kumukahi', 19.5163, -154.8108]]) {
    const at = layers.projectFrame(lat, lon, '15');
    assert.ok(at && inside(at, hawaii.clip) && !inside(at, ALASKA_BOX), name + ' is placed in the Hawaii inset: ' + at);
  }
  const shoals = [23.8697, -166.2847];
  for (const [name, lat, lon] of [['Nihoa', 23.0605, -161.9218], ['French Frigate Shoals', ...shoals], ['Kure Atoll', 28.3921, -178.2937]]) {
    assert.equal(layers.projectFrame(lat, lon, '15'), null, name + ' is clipped');
  }
  assert.ok(layers.projectFrame(61.2181, -149.9003, '02'), 'Anchorage is still placed');
  // Teeth: the same conic without the clip puts French Frigate Shoals inside the Alaska box.
  const clip = hawaii.clip;
  delete hawaii.clip;
  let unclipped;
  try { unclipped = layers.projectFrame(shoals[0], shoals[1], '15'); } finally { hawaii.clip = clip; }
  assert.ok(unclipped && inside(unclipped, ALASKA_BOX), 'unclipped, French Frigate Shoals lands in the Alaska box: ' + unclipped);
});

test('[map layers] rows are placed or listed, never dropped, at every level', async () => {
  const lookup = await fakeLookup().ready(ROWS.map(row => row.zip));
  for (const level of layers.LEVEL_IDS) {
    const result = layers.aggregate(ROWS, level, lookup, {congress: '119'});
    const listed = result.unplaced.reduce((sum, entry) => ({rows: sum.rows + entry.rows, amount: sum.amount + entry.amount}), {rows: 0, amount: 0});
    assert.equal(result.placed.rows + listed.rows, 7, level + ': rows conserve');
    assert.equal(Math.round((result.placed.amount + listed.amount) * 100), 19000, level + ': dollars conserve');
    assert.equal(result.total.rows, 7);
  }
  const state = layers.aggregate(ROWS, 'state', lookup);
  assert.deepEqual([...state.features.keys()].sort(), ['CA', 'NY']);
  assert.equal(state.features.get('NY').amount, 155);
  assert.equal(state.line, '6 of 7 located');
  const county = layers.aggregate(ROWS, 'county', lookup);
  assert.equal(county.features.get('36061').amount, 150);
  assert.ok(county.unplaced.some(entry => /99999 is not a Census ZCTA/.test(entry.reason)));
  assert.ok(county.unplaced.some(entry => /needs a ZIP, city or street address/.test(entry.reason)), 'a state-only row is listed');
  const d119 = layers.aggregate(ROWS, 'cd', lookup, {congress: '119'});
  const d116 = layers.aggregate(ROWS, 'cd', lookup, {congress: '116'});
  assert.equal(d119.features.get('NY-10').amount, 50, 'ZIP 10003 is NY-10 on the 119th map');
  assert.equal(d116.features.get('NY-12').amount, 150, 'and NY-12 on the 116th');
  const hex = layers.aggregate(ROWS, 'hex25', lookup);
  assert.equal(hex.features.get('h25:14:63').amount, 150);
  const tract = layers.aggregate(ROWS, 'tract', lookup);
  assert.equal(tract.features.size, 0, 'a ZIP row cannot place a census tract');
  assert.equal(tract.line, '0 of 7 located');
  assert.ok(tract.unplaced.some(entry => entry.reason === 'needs a street address' && entry.rows === 6),
    'every ZIP and state row is listed for a tract view');
  const points = layers.aggregate(ROWS, 'point', lookup);
  assert.equal(points.points.length, 3);
  assert.ok(points.points.every(point => Number.isFinite(point.lat) && Number.isFinite(point.lon)));
});

test('[map layers] the PAC payload keeps the server’s own places and adds up to its reported total', () => {
  const contract = readJson(path.join(CONTRACTS, 'evidence-geo.json'));
  for (const example of contract.examples) {
    const body = example.payload;
    for (const level of layers.LEVEL_IDS) {
      for (const off of [undefined, new Set()]) {
        const rows = layers.rowsFromEvidence(body, level, {off});
        const total = rows.reduce((sum, row) => sum + row.amount, 0);
        assert.ok(Math.abs(total - body.reported_total) < 0.01, example.name + ' ' + level + ': ' + total + ' vs ' + body.reported_total);
      }
    }
    const everything = {off: new Set()};
    const state = layers.aggregate(layers.rowsFromEvidence(body, 'state', everything), 'state', null);
    for (const row of state.features.values()) {
      const byState = (body.by_state || []).find(entry => entry.key === row.id);
      if (byState) assert.ok(Math.abs(byState.amount - row.amount) < 0.01, example.name + ' ' + row.id + ' ' + byState.amount + ' vs ' + row.amount);
    }
    assert.ok(Math.abs(state.placed.amount - (body.located_total || 0)) < 0.01, example.name + ': the state layer places the located total');
    const cd = layers.aggregate(layers.rowsFromEvidence(body, 'cd', everything), 'cd', null);
    for (const [key, feature] of cd.features) assert.ok(/^[A-Z]{2}-(\d{2}|AL)$/.test(key) && feature.amount !== 0, key);
    if (body.direction === 'spent') {
      const county = layers.aggregate(layers.rowsFromEvidence(body, 'county', everything), 'county', null);
      assert.equal(county.features.size, 0, 'spending is placed by seat or state, not by county');
      const oppose = [...cd.features.values()].reduce((sum, feature) => sum + feature.oppose, 0);
      const opposeRows = (body.kinds || []).flatMap(kind => kind.districts || []).filter(row => !/-SEN$/.test(row.key))
        .reduce((sum, row) => sum + (row.oppose || 0), 0);
      assert.ok(Math.abs(oppose - opposeRows) < 0.01, 'support and oppose travel apart');
    }
  }
});

test('[map layers] layer cards and map settings: the shapes accepted and the faults named', () => {
  const industry = {kind: 'industry', industry: 'Crypto', basis: 'receipt'};
  const pac = {kind: 'pac', source: 'snapshot', id: 'snapshot-2eb1b6d618fa4595b4ddbe8043753ede', committee: 'C00835959', cycle: '2024', direction: 'spent', name: 'FAIRSHAKE'};
  const card = layers.newLayerCard({id: 'layer-1', source: industry, geography: 'county', type: 'filled-map'});
  assert.equal(layers.layerFault(card), '');
  assert.equal(core.validateMarksCards({rows: [], marksCards: [card]}), '');
  assert.equal(layers.layerFault(Object.assign({}, card, {source: pac})), '');
  assert.match(layers.layerFault(Object.assign({}, card, {geography: 'galaxy'})), /map level/);
  assert.match(layers.layerFault(Object.assign({}, card, {type: 'pie'})), /Filled, Circle/);
  assert.match(layers.layerFault(Object.assign({}, card, {source: {kind: 'web', url: 'x'}})), /industry or a PAC/);
  assert.match(layers.layerFault(Object.assign({}, card, {source: Object.assign({}, pac, {committee: 'FAIRSHAKE'})})), /committee/);
  assert.match(layers.layerFault(Object.assign({}, card, {map: {radius: 400}})), /radius/);
  const two = [card, Object.assign(layers.newLayerCard({id: 'layer-2', source: pac, geography: 'cd', type: 'filled-map'}))];
  assert.equal(core.validateMarksCards({rows: [], marksCards: two}), '', 'layers need no measure on Rows');
  assert.equal(core.dualAxisOn({marksCards: two}), false);
  assert.equal(core.mapLayersOn({marksCards: two}), true);
  const mixed = [two[0], {id: 'card-2', name: '', type: 'bar', encodings: {color: null, size: null, label: null, detail: [], tooltip: [], shape: null, path: null, angle: null},
    opacity: 100, border: 'automatic', halo: 'automatic', visible: true, selectable: true, zoomExtent: true}];
  assert.match(core.validateMarksCards({rows: [], marksCards: mixed}), /every card of a map is a layer/);
  assert.equal(layers.mapSettingsFault({layers: ['states'], congress: '119', background: 'streets', washout: 40,
    base: ['coastlines', 'state-borders'], data: null, options: {scale: true, units: 'us'}}), '');
  assert.match(layers.mapSettingsFault({background: 'satellite'}), /background/);
  assert.match(layers.mapSettingsFault({washout: 140}), /washout/);
  assert.match(layers.mapSettingsFault({base: ['coastlines', 'coastlines']}), /base layers/);
  assert.match(layers.mapSettingsFault({options: {units: 'furlongs'}}), /units/);
  assert.match(layers.mapSettingsFault({tiles: 'x'}), /unknown key/);
});

test('[map layers] a map sheet round-trips through lane M’s writers byte-stable, with its layers in order', () => {
  const industry = {kind: 'industry', industry: 'Crypto', basis: 'receipt'};
  const cards = [
    layers.newLayerCard({id: 'layer-1', name: 'Donor addresses', source: industry, geography: 'point', type: 'circle', map: {radius: 6}}),
    layers.newLayerCard({id: 'layer-2', name: 'Census tracts', source: industry, geography: 'county', type: 'filled-map', opacity: 75}),
    layers.newLayerCard({id: 'layer-3', name: 'Donor density', source: industry, geography: 'point', type: 'density', visible: false, map: {radius: 18, intensity: 70}})];
  const sheet = core.createSheet('NY metro', 'sheet-1');
  core.writeCards(sheet, cards);
  assert.equal(sheet.marksCards.length, 3);
  assert.deepEqual(sheet.marksCards.map(card => card.id), ['layer-1', 'layer-2', 'layer-3'], 'card order is draw order');
  assert.equal(sheet.marks.type, 'circle', 'marks mirrors the top card for a console without layers');
  assert.equal(core.validateMarksCards(sheet), '');
  const opened = core.migrateSheet(JSON.parse(JSON.stringify(sheet)));
  assert.equal(JSON.stringify(opened.marksCards), JSON.stringify(sheet.marksCards));
  assert.equal(core.syncCardsToRows(opened, []).marksCards.length, 3, 'Rows never collapses a map’s layers');
  const report = core.compileReport(opened, [{index: 0, name: 'State', type: 'category'}, {index: 1, name: 'Amount', type: 'number'}]);
  assert.notEqual(report.markType, 'cards', 'a map is not compiled as a dual axis');
  const single = core.createSheet('One layer', 'sheet-2');
  core.writeCards(single, [cards[1]]);
  assert.equal(single.marksCards.length, 1, 'one layer keeps its card (it has v3 fields)');
  assert.equal(core.readCards(single)[0].geography, 'county');
});

test('[map layers] density: a kernel that ends at its radius, and intensity kept apart from opacity', () => {
  const grid = layers.densityGrid([{x: 20, y: 20, weight: 1}], 40, 40, 8, 1);
  const at = (x, y) => grid.grid[y * grid.columns + x];
  assert.ok(at(19, 19) > 0.9 * grid.max, 'the peak sits on the point');
  assert.equal(at(35, 20), 0, 'nothing beyond the radius');
  assert.equal(at(0, 0), 0);
  const stops = layers.rampStops('heat');
  const low = layers.densityPixels(grid, stops, 10, 1), high = layers.densityPixels(grid, stops, 90, 1);
  const alpha = (pixels, x, y) => pixels[(y * grid.columns + x) * 4 + 3];
  assert.equal(alpha(low, 0, 0), 0, 'low density fades to the plain base');
  assert.ok(alpha(high, 24, 20) >= alpha(low, 24, 20), 'more intensity saturates sooner');
  const faint = layers.densityPixels(grid, stops, 90, 0.5);
  assert.ok(alpha(faint, 19, 19) < alpha(high, 19, 19), 'opacity is its own control');
  assert.equal(layers.densityGrid([], 10, 10, 5, 2).max, 0);
});

test('[map layers] a compiled layer colours from the theme’s own ramps and marks a reversal', async () => {
  const lookup = await fakeLookup().ready(ROWS.map(row => row.zip));
  const fields = [{index: 0, id: 'state', name: 'State'}, {index: 1, id: 'amount', name: 'Selected amount'}, {index: 2, id: 'contributors', name: 'Contributors'}];
  const card = layers.newLayerCard({id: 'layer-1', source: {kind: 'industry', industry: 'Crypto'}, geography: 'state', type: 'filled-map',
    encodings: {color: {column: 1, aggregation: 'sum'}}});
  const rows = ROWS.concat([{zip: '', state: 'TX', amount: -40, rows: 1, contributors: 1, precision: 'state'}]);
  const compiled = layers.compileLayer(card, layers.aggregate(rows, 'state', lookup), {fields});
  const byId = Object.fromEntries(compiled.items.map(item => [item.id, item]));
  assert.equal(byId.TX.colour, layers.REVERSAL);
  assert.ok(/^#[0-9a-f]{6}$/.test(byId.NY.colour) && byId.NY.colour !== byId.CA.colour);
  assert.equal(compiled.legend.kind, 'ramp');
  assert.equal(compiled.legend.measure, 'Selected amount');
  assert.match(compiled.legend.located, /of 8 located/);
  const theme = require('../theme.js');
  assert.equal(layers.rampStops('teal')[3], theme.categorical[0], 'the ramp is built on the theme’s teal slot');
  const circles = layers.compileLayer(Object.assign({}, card, {type: 'circle', encodings: Object.assign({}, card.encodings, {color: null, size: {column: 2, aggregation: 'sum'}})}),
    layers.aggregate(ROWS, 'state', lookup), {fields});
  const radii = Object.fromEntries(circles.items.map(item => [item.id, item.radius]));
  assert.ok(radii.NY > radii.CA, 'Size follows donors');
});

test('[map layers] the viewer’s layer control and the status line read the same cards', () => {
  const compiled = [{id: 'a', name: 'Donor addresses', mark: 'circle', visible: true, selectable: true, sourceKey: 'pac|x'},
    {id: 'b', name: 'Census tracts', mark: 'filled-map', visible: true, selectable: true, sourceKey: 'industry|Crypto|receipt'},
    {id: 'c', name: 'Donor density', mark: 'density', visible: false, selectable: true, sourceKey: 'industry|Crypto|receipt'}];
  const rows = layers.controlRows(compiled, {hidden: new Set(['b']), locked: new Set(['a'])});
  assert.deepEqual(rows.map(row => [row.id, row.visible, row.selectable]), [['a', true, false], ['b', false, true], ['c', false, true]]);
  assert.equal(layers.statusLine(compiled, 12408), '3 marks layers · 2 sources · 1 hidden · 12,408 marks');
  assert.equal(layers.subtitleOf(compiled), 'two sources · layers drawn top to bottom as listed in Marks');
});

test('[map layers] PAC evidence presets are valid layer cards that hand over as a map sheet', () => {
  const fields = [{index: 0, id: 'state'}, {index: 5, id: 'amount'}, {index: 6, id: 'records'}, {index: 7, id: 'contributors'}];
  const pane = {source: 'snapshot', id: 'snapshot-2eb1b6d618fa4595b4ddbe8043753ede', committee: 'C00835959', name: 'FAIRSHAKE', industry: 'Crypto'};
  for (const direction of ['raised', 'spent']) {
    const cards = layers.evidencePresets(pane, direction, '2024', fields);
    // Raised: donor ZIP circles, the heat (off until switched on) and the state fill.
    // Spent: the Opposition (its own card: the Spent source's oppose side, hatched) over the fill by district.
    assert.deepEqual(cards.map(card => [card.type, card.geography, (card.map && card.map.pattern) || '', card.source.stance || '']), direction === 'raised'
      ? [['circle', 'zcta', '', ''], ['density', 'point', '', ''], ['filled-map', 'state', '', '']]
      : [['filled-map', 'cd', 'hatch', 'oppose'], ['filled-map', 'cd', '', '']]);
    for (const card of cards) assert.equal(layers.layerFault(card), '', card.name);
    const sheet = core.createSheet('FAIRSHAKE', 'sheet-1');
    core.writeCards(sheet, cards);
    assert.equal(core.validateMarksCards(sheet), '', direction);
    assert.ok(cards.every(card => card.source.direction === direction && card.source.cycle === '2024'));
  }
  // The Opposition is drawn where the evidence map hatches it (District, State, Region), and starts unselectable.
  for (const [layer, count] of [['district', 2], ['state', 2], ['region', 2], ['zip', 1], ['sld', 1]]) {
    const cards = layers.evidencePresets(pane, 'spent', '2024', null, {layer});
    assert.equal(cards.length, count, layer);
    assert.equal(cards.filter(card => card.source.stance === 'oppose').every(card => card.selectable === false), true, layer);
  }
  const spent = layers.evidencePresets(pane, 'spent', '2024', null, {layer: 'district', hidden: new Set(['hatch']), locked: new Set()});
  assert.deepEqual(spent.map(card => [card.name, card.visible, card.selectable]),
    [['Opposition · hatch', false, true], ['Spending by district · filled', true, true]], 'the view state travels with the cards');
});

test('[map layers] one side of Spent: every candidate summed, support and oppose never netted', () => {
  const contract = readJson(path.join(CONTRACTS, 'evidence-geo.json'));
  let checked = 0;
  for (const example of contract.examples.filter(item => item.payload && item.payload.direction === 'spent')) {
    const body = example.payload;
    const rows = (body.kinds || []).flatMap(kind => [...(kind.districts || []), ...(kind.states || [])]);
    for (const stance of ['oppose', 'support']) {
      const other = stance === 'oppose' ? 'support' : 'oppose';
      const side = layers.rowsFromEvidence(body, 'state', {off: new Set(), stance});
      const expected = rows.reduce((sum, row) => sum + (row[stance] || 0), 0);
      assert.ok(Math.abs(side.reduce((sum, row) => sum + row.amount, 0) - expected) < 0.01, example.name + ': ' + stance + ' conserves');
      assert.ok(side.every(row => row[other] === 0 && row.rows === null), 'the other side is never carried, rows are not counted by side');
      const state = layers.aggregate(side, 'state', null);
      assert.equal(state.unit, side.length ? 'amount' : 'rows', 'a side counts dollars');
      assert.ok(Math.abs(state.placed.amount - expected) < 0.01, 'every place with this side is on the State layer');
      const cd = layers.aggregate(layers.rowsFromEvidence(body, 'cd', {off: new Set(), stance}), 'cd', null);
      const house = rows.filter(row => /^[A-Z]{2}-(\d{2}|AL)$/.test(row.key)).reduce((sum, row) => sum + (row[stance] || 0), 0);
      assert.ok(Math.abs(cd.placed.amount - house) < 0.01, 'District places the House seats');
      const listed = cd.unplaced.reduce((sum, entry) => sum + entry.amount, 0);
      assert.ok(Math.abs(cd.placed.amount + listed - expected) < 0.01, 'and lists the rest (Senate races, states), never drops it');
      if (listed > 0) assert.match(cd.line, /^\$[\d.,KMB]+ of \$[\d.,KMB]+ located$/);
      checked += 1;
    }
  }
  // A place with both sides keeps both, apart: a hand-made payload with one such seat.
  const body = {direction: 'spent', kinds: [{kind: 'candidates', districts: [{key: 'AL-02', amount: 100, rows: 5, support: 70, oppose: 30},
    {key: 'UT-SEN', amount: 50, rows: 2, support: 0, oppose: 50}], states: []}], unlocated: [{reason: 'no place', amount: 9, rows: 1}]};
  const oppose = layers.aggregate(layers.rowsFromEvidence(body, 'cd', {stance: 'oppose'}), 'cd', null);
  assert.equal(oppose.features.get('AL-02').amount, 30, 'oppose is the oppose sum, not amount minus support');
  assert.equal(oppose.features.get('AL-02').support, 0);
  assert.ok(oppose.unplaced.some(entry => /Senate race/.test(entry.reason) && entry.amount === 50));
  assert.equal(oppose.total.amount, 80, 'unplaced money is not split by side, so a side carries none of it');
  const support = layers.aggregate(layers.rowsFromEvidence(body, 'state', {stance: 'support'}), 'state', null);
  assert.deepEqual([...support.features.keys()], ['AL'], 'a place with no support is not a support place');
  assert.equal(layers.rowsFromEvidence(Object.assign({}, body, {direction: 'raised'}), 'state', {stance: 'oppose'}).some(row => row.rows === null), false,
    'Raised has no sides');
  assert.ok(checked >= 8, 'every Spent example of evidence-geo.json, both sides: ' + checked);
});

test('[map layers] a side and a hatch: the card faults, the labels and the compiled layer', () => {
  const pac = {kind: 'pac', source: 'snapshot', id: 'fairshake-20260908', committee: 'C00835959', cycle: '2024', direction: 'spent', name: 'FAIRSHAKE'};
  const card = layers.newLayerCard({id: 'layer-1', source: Object.assign({}, pac, {stance: 'oppose'}), geography: 'cd', type: 'filled-map', map: {pattern: 'hatch'}});
  assert.equal(layers.layerFault(card), '');
  assert.equal(layers.layerFault(Object.assign({}, card, {type: 'automatic'})), '', 'Automatic fills a district');
  assert.match(layers.layerFault(Object.assign({}, card, {source: Object.assign({}, pac, {stance: 'net'})})), /support or oppose/);
  assert.match(layers.layerFault(Object.assign({}, card, {source: Object.assign({}, pac, {stance: 'oppose', direction: 'raised'})})), /sides of Spent/);
  assert.match(layers.layerFault(Object.assign({}, card, {map: {pattern: 'plaid'}})), /pattern is a hatch/);
  assert.match(layers.layerFault(Object.assign({}, card, {type: 'circle'})), /Filled layer/);
  assert.match(layers.layerFault(Object.assign({}, card, {type: 'automatic', geography: 'point'})), /Filled layer/, 'Automatic at points draws circles');
  assert.equal(layers.sourceKey(card.source), layers.sourceKey(pac), 'a side of a source is the same source');
  assert.equal(layers.sourceLabel(card.source), 'FAIRSHAKE · Spent 2023–2024 · opposition only');
  assert.equal(layers.paneLabel(card.source), 'FAIRSHAKE · Spent 2023–2024');
  assert.equal(layers.sourceBadge(Object.assign({}, pac, {source: 'csv', id: 'csv-0123456789abcdef0123456789abcdef'})), 'Imported file');
  assert.equal(layers.layerName(Object.assign({}, card, {name: ''}), []), 'Opposition by congressional district');
  assert.match(layers.encodingSupport(card).color, /hatch/i, 'Colour is greyed with its reason on a hatch');
  const rows = [{zip: '', state: 'AL', key: 'AL-02', level: 'cd', amount: 30, rows: null, contributors: 0, support: 0, oppose: 30, precision: 'seat'}];
  const compiled = layers.compileLayer(card, layers.aggregate(rows, 'cd', null), {fields: []});
  assert.deepEqual([compiled.pattern, compiled.stance, compiled.legend.kind, compiled.legend.count, compiled.legend.note],
    ['hatch', 'oppose', 'pattern', 'amount', 'Opposition, every candidate']);
  assert.equal(compiled.legend.located, '$30 of $30 located');
  const empty = layers.compileLayer(card, layers.aggregate([], 'cd', null), {fields: []});
  assert.deepEqual([empty.legend.located, empty.legend.count], ['$0 of $0 located', 'amount'], 'an empty side still counts dollars');
  const solid = layers.compileLayer(Object.assign({}, card, {map: undefined}), layers.aggregate(rows, 'cd', null), {fields: []});
  assert.equal(solid.pattern, '');
  assert.equal(solid.legend.kind, 'ramp');
});

test('[map layers] G2b #47/#48: the glow curve is 1 - 0.95 x I^0.75, and a place at a thousandth of the peak still shows', () => {
  const gamma = layers.densityGamma;
  assert.ok(Math.abs(gamma(70) - (1 - 0.95 * Math.pow(0.7, 0.75))) < 1e-12);
  assert.ok(Math.abs(gamma(70) - 0.2729) < 1e-3, 'the 70% default: ' + gamma(70));
  assert.ok(Math.abs(gamma(100) - 0.05) < 1e-12);
  assert.ok(Math.abs(gamma(1) - (1 - 0.95 * Math.pow(0.01, 0.75))) < 1e-12);
  assert.equal(gamma(0), gamma(70), 'no intensity is the default');
  assert.equal(gamma(250), gamma(100), 'intensity is capped at 100');
  for (let i = 1; i < 100; i += 1) assert.ok(gamma(i + 1) < gamma(i), 'more intensity lifts the curve: ' + i);
  // Four cells: the peak, a hundredth of it, a thousandth, and far below the cut-off; drawn at the default.
  const density = {grid: Float32Array.from([1, 0.01, 0.001, 0.00001]), columns: 4, rows: 1, cell: 1, max: 1};
  const pixels = layers.densityPixels(density, layers.rampStops('heat'), 70, 1);
  const alpha = at => pixels[at * 4 + 3] / 255;
  assert.ok(alpha(0) > 0.99, 'the peak is opaque');
  assert.ok(Math.abs(alpha(1) - 0.40) < 0.02, 'a hundredth of the peak: ' + alpha(1).toFixed(3));
  assert.ok(Math.abs(alpha(2) - 0.19) < 0.02, 'a thousandth of the peak: ' + alpha(2).toFixed(3));
  assert.equal(alpha(3), 0, 'far below the cut-off stays transparent');
  // The order of places never changes: alpha falls with density.
  assert.ok(alpha(0) > alpha(1) && alpha(1) > alpha(2) && alpha(2) > alpha(3));
});

test('[map layers] G2b: the street heatmap stops rise strictly at every intensity (MapLibre refuses anything else)', () => {
  const stops = layers.rampStops('heat');
  for (let intensity = 1; intensity <= 100; intensity += 1) {
    const out = layers.densityColourStops(stops, intensity);
    assert.equal(out.length, 14, 'the transparent start and six stops');
    assert.equal(out[0], 0);
    const inputs = out.filter((_, at) => at % 2 === 0);
    for (let k = 1; k < inputs.length; k += 1) assert.ok(inputs[k] > inputs[k - 1], 'intensity ' + intensity + ': ' + inputs.join(', '));
    assert.ok(Math.abs(inputs[inputs.length - 1] - 1) < 1e-9, 'the peak is the last stop');
  }
  // Where the curve is gentle each stop sits where the gamma puts it, as before.
  const gentle = layers.densityColourStops(stops, 20).filter((_, at) => at % 2 === 0);
  const gamma = layers.densityGamma(20);
  assert.ok(Math.abs(gentle[2] - Math.pow(layers.DENSITY_CUT + (1 - layers.DENSITY_CUT) / 5, 1 / gamma)) < 1e-12);
});

test('[map layers] G2b #53: a Filled or Hexbin colour scale over three decades is log, clamped low, with its real ends', () => {
  // 100 places from $1 to $10M: seven decades.
  const values = Array.from({length: 100}, (_, at) => Math.pow(10, 7 * at / 99));
  const plain = layers.scaleFor(values), clamped = layers.scaleFor(values, {clamp: true});
  assert.equal(plain.log, true);
  assert.equal(plain.floor, plain.min, 'without clamp the scale starts at the smallest place');
  assert.equal(clamped.min, 1);
  assert.equal(clamped.max, values[99]);
  assert.equal(clamped.floor, values[Math.floor(layers.CLAMP_LOW * 99)], 'the floor is a real place: the 10th percentile');
  assert.equal(clamped.ceiling, values[Math.floor(layers.CLAMP_HIGH * 99)], 'clamped at both ends: the ceiling is a real place, the 95th percentile');
  assert.equal(layers.CLAMP_HIGH, 0.95);
  assert.equal(clamped.position(values[0]), 0, 'below the floor takes the first colour');
  assert.equal(clamped.position(clamped.floor), 0);
  assert.equal(clamped.position(clamped.max), 1);
  assert.equal(clamped.position(clamped.ceiling), 1, 'at or above the ceiling takes the last colour');
  for (let at = 1; at < values.length; at += 1) assert.ok(clamped.position(values[at]) >= clamped.position(values[at - 1]), 'monotone');
  // Unclamped positions are exactly the earlier formula.
  const old = value => (Math.log(value) - Math.log(plain.min)) / (Math.log(plain.max) - Math.log(plain.min));
  for (const value of values) assert.equal(plain.position(value), old(value));
  // Three decades or fewer: no clamp, even when asked.
  const narrow = layers.scaleFor([1, 10, 100, 1000], {clamp: true});
  assert.equal(narrow.floor, 1);
  assert.equal(narrow.ceiling, 1000);
  assert.equal(narrow.log, true);
  // The legend: real ends, never a plus on a log ramp, and the words.
  const legend = {kind: 'ramp', unit: 'amount', min: clamped.min, max: clamped.max, log: true, floor: clamped.floor, ceiling: clamped.ceiling};
  assert.deepEqual(layers.legendRange(legend), ['≤' + layers.money(clamped.floor, 'amount'), '≥' + layers.money(clamped.ceiling, 'amount')]);
  assert.deepEqual(layers.legendRange(Object.assign({}, legend, {floor: legend.min, ceiling: legend.max})), ['$1', '$10.0M']);
  assert.deepEqual(layers.legendRange({kind: 'ramp', unit: 'amount', min: 5, max: 50, log: false}), ['$0', '$50+'], 'a linear ramp is unchanged');
  assert.match(layers.legendScaleNote(legend), /log scale/);
  assert.match(layers.legendScaleNote(legend), /the places run from \$1 to \$10\.0M/);
  assert.equal(layers.legendScaleNote(Object.assign({}, legend, {kind: 'density'})), '');
  assert.equal(layers.legendScaleNote(Object.assign({}, legend, {log: false})), '');
});

test('[map layers] G2b #53: compileLayer clamps a Filled layer’s colours, never a circle’s, and moves no value', () => {
  const states = ['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD'];
  const rows = states.map((state, at) => ({zip: '', state, amount: Math.round(Math.pow(10, 1 + 5 * at / 19)), rows: 1, contributors: 1, precision: 'state'}));
  const filled = layers.newLayerCard({id: 'layer-1', source: {kind: 'industry', industry: 'Crypto'}, geography: 'state', type: 'filled-map'});
  const circle = layers.newLayerCard({id: 'layer-2', source: {kind: 'industry', industry: 'Crypto'}, geography: 'state', type: 'circle',
    encodings: {color: {field: 'amount'}}});
  const a = layers.compileLayer(filled, layers.aggregate(rows, 'state', null), {fields: []});
  const b = layers.compileLayer(circle, layers.aggregate(rows, 'state', null), {fields: []});
  assert.equal(a.items.length, 20);
  assert.ok(a.legend.log && a.legend.floor > a.legend.min, 'the Filled layer is clamped low');
  assert.ok(a.legend.ceiling < a.legend.max, 'the Filled layer is clamped high too');
  assert.equal(b.legend.floor, b.legend.min, 'a circle layer keeps its whole range');
  // Values, amounts and the located line are the aggregate's own.
  const byState = Object.fromEntries(rows.map(row => [row.state, row.amount]));
  for (const item of a.items) { assert.equal(item.value, byState[item.id]); assert.equal(item.amount, byState[item.id]); }
  assert.equal(a.legend.located, b.legend.located);
  // Every place at or below the floor shares the first colour; above it each has its own shade.
  const low = a.items.filter(item => item.value <= a.legend.floor).map(item => item.colour);
  assert.ok(low.length >= 2 && new Set(low).size === 1, 'the clamped places share one colour');
  const mid = a.items.filter(item => item.value > a.legend.floor && item.value < a.legend.ceiling).map(item => item.colour);
  assert.equal(new Set(mid).size, mid.length, 'every place between floor and ceiling has its own shade');
  const top = a.items.filter(item => item.value >= a.legend.ceiling).map(item => item.colour);
  assert.ok(top.length >= 1 && new Set(top).size === 1, 'places at or above the ceiling share the last colour');
});
