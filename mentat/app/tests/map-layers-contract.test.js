/* studio_api.json: maps whose marks cards are layers (lane P, boards #47-#55, 2026-09-26).

   The contract's map examples were written by the client's own producers -
   map-layers.js newLayerCard / writeSettings / evidencePresets and
   chart-studio-core.js writeCards / serializeCard. Every assertion below runs
   those producers again, so the file and the client cannot drift apart
   (contracts/README.md, DE-021). The same examples and lane P's negative
   controls are sent to a real Console by tests/test_map_layers_server.py,
   which also checks each refusal's sentence.

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const core = require('../chart-studio-core.js');
const L = require('../map-layers.js');

const CONTRACTS = path.resolve(__dirname, '..', '..', '..', 'audits', 'mentat_failings_20260915', 'contracts');
const studioApi = JSON.parse(fs.readFileSync(path.join(CONTRACTS, 'studio_api.json'), 'utf8'));
const ex = studioApi.examples;
const clone = value => JSON.parse(JSON.stringify(value));
const laneP = () => studioApi.negative_controls.filter(control => control.lane === 'P');
const pill = (column, aggregation = 'sum') => ({column, aggregation});
const AMOUNT = 11, CONTRIBUTORS = 13;   // catalog positions lane M's examples use

const FAIRSHAKE = {kind: 'pac', source: 'snapshot', id: 'fairshake-20260908', committee: 'C00835959', cycle: '2024',
  direction: 'spent', name: 'FAIRSHAKE', industry: 'Crypto'};
const CRYPTO = {kind: 'industry', industry: 'Crypto', basis: 'receipt', label: 'Crypto'};
const BANKS = {kind: 'industry', industry: 'Community Banks', basis: 'receipt', label: 'Community Banks'};

// The three-source map the real-client pass builds: FAIRSHAKE's spending filled
// by district, Crypto giving as circles by ZIP, Community Banks as a density.
function threeLayerSheet() {
  const sheet = clone(ex.views_save.sheets[0]);
  Object.assign(sheet, {id: 'sheet-map', name: 'Where the money lives, and where it lands', columns: [], rows: []});
  core.writeCards(sheet, [
    L.newLayerCard({id: 'layer-1', name: 'Crypto donor ZIPs', source: CRYPTO, geography: 'zcta', type: 'circle',
      encodings: {size: pill(CONTRIBUTORS), tooltip: [pill(AMOUNT)]}, map: {radius: 12}}),
    L.newLayerCard({id: 'layer-2', name: 'Community Banks density', source: BANKS, geography: 'point', type: 'density',
      encodings: {color: pill(AMOUNT)}, opacity: 80, map: {radius: 18, intensity: 70, ramp: 'heat'}}),
    L.newLayerCard({id: 'layer-3', name: 'FAIRSHAKE spending by district', source: FAIRSHAKE, geography: 'cd', type: 'filled-map',
      encodings: {color: pill(AMOUNT)}, opacity: 90, selectable: true, zoomExtent: true})]);
  L.writeSettings(sheet, Object.assign(L.settingsOf({}), {background: 'boundaries', washout: 40,
    base: ['coastlines', 'state-borders', 'state-names', 'county-borders'], data: null,
    options: {scale: true, units: 'us', search: true, toolbar: true, panZoom: true, layerControl: true}}));
  return sheet;
}

test('[map layers] the contract carries lane P\'s examples and nineteen controls, each naming its refusal', () => {
  for (const name of ['map_layer_card', 'views_save_map_layers', 'views_get_map_layers', 'views_save_map_one',
    'views_save_map_streets', 'views_save_map_opposition', 'map_settings', 'map_handoff']) assert.ok(ex[name], 'missing example ' + name);
  assert.equal(laneP().length, 19);
  for (const control of laneP()) assert.ok(control.refusal === 'cards' || control.refusal === 'map', control.why);
  assert.deepEqual(laneP().map(control => control.refusal).filter(kind => kind === 'map').length, 6);
});

test('[map layers] the producers write the three-source map exactly', () => {
  const sheet = threeLayerSheet();
  assert.equal(JSON.stringify(sheet), JSON.stringify(ex.views_save_map_layers.sheets[0]));
  assert.equal(JSON.stringify(sheet.map), JSON.stringify(ex.map_settings));
  assert.equal(JSON.stringify(core.serializeCard(core.readCards(sheet)[2])), JSON.stringify(ex.map_layer_card));
  // Card order is draw order, top first; every card is a layer with its own source and level.
  const cards = core.readCards(sheet);
  assert.deepEqual(cards.map(card => [card.geography, card.type, L.sourceKey(card.source)]),
    [['zcta', 'circle', L.sourceKey(CRYPTO)], ['point', 'density', L.sourceKey(BANKS)], ['cd', 'filled-map', L.sourceKey(FAIRSHAKE)]]);
  assert.deepEqual(ex.views_get_map_layers.sheets, ex.views_save_map_layers.sheets, 'the saved view reads back as sent');
});

test('[map layers] one layer mirrors marks, and the Streets map is the same model with another background', () => {
  const one = ex.views_save_map_one.sheets[0];
  const cards = core.readCards(one);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].geography, 'hex25');
  assert.equal(one.marksCards.length, 1, 'a map layer keeps its v3 fields in marksCards');
  assert.equal(L.settingsOf(one).background, 'light');
  const rebuilt = clone(one);
  core.writeCards(rebuilt, core.readCards(rebuilt));
  assert.equal(JSON.stringify(rebuilt), JSON.stringify(one), 'read then write changes no byte');
  const streets = ex.views_save_map_streets.sheets[0];
  assert.equal(streets.map.background, 'streets');
  assert.deepEqual(streets.marksCards, ex.views_save_map_layers.sheets[0].marksCards);
});

test('[map layers] PAC evidence hands Chart Studio its presets as cards', () => {
  const cards = L.evidencePresets({source: 'snapshot', id: 'fairshake-20260908', committee: 'C00835959', name: 'FAIRSHAKE',
    industry: 'Crypto'}, 'spent', '2024', null, {layer: 'district'});
  assert.equal(JSON.stringify(cards), JSON.stringify(ex.map_handoff.cards));
  for (const card of cards) {
    assert.equal(card.source.kind, 'pac');
    assert.equal(card.source.committee, 'C00835959');
    assert.equal(L.layerFault(card), '', card.name);
  }
  // #55's Opposition travels as its own card: the Spent source's oppose side, hatched, over the fill.
  assert.deepEqual(cards.map(card => [card.name, card.source.stance || '', (card.map || {}).pattern || '']),
    [['Opposition · hatch', 'oppose', 'hatch'], ['Spending by district · filled', '', '']]);
  const saved = ex.views_save_map_opposition.sheets[0];
  assert.deepEqual(core.readCards(saved).map(card => [card.source.stance || '', (card.map || {}).pattern || '', card.encodings.color]),
    [['oppose', 'hatch', null], ['', '', pill(AMOUNT)]], 'saved with its pills resolved, the hatch carries no colour');
});

test('[map layers] every example is accepted by the client gates the server mirrors', () => {
  for (const name of ['views_save_map_layers', 'views_save_map_one', 'views_save_map_streets', 'views_save_map_opposition']) {
    for (const sheet of ex[name].sheets) {
      assert.equal(core.validateMarksCards(sheet), '', name);
      assert.equal(L.mapSettingsFault(sheet.map), '', name);
    }
  }
});

test('[map layers] each negative control is refused by exactly the gate it names, one fault from an accepted example', () => {
  const accepted = JSON.stringify(ex.views_save_map_layers.sheets[0]);
  for (const control of laneP()) {
    const sheet = control.payload.sheets[0];
    assert.notEqual(JSON.stringify(sheet), accepted, 'a control differs from the example it breaks');
    const cardFault = core.validateMarksCards(sheet);
    const mapFault = L.mapSettingsFault(sheet.map);
    if (control.refusal === 'cards') {
      assert.ok(cardFault, 'cards gate must refuse: ' + control.why);
      assert.equal(mapFault, '', 'map settings are untouched: ' + control.why);
    } else {
      assert.ok(mapFault, 'map gate must refuse: ' + control.why);
      assert.equal(cardFault, '', 'cards are untouched: ' + control.why);
    }
  }
});
