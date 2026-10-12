/* Maps whose marks cards are its layers (lane P, boards #47, #48, #49, #53, #54, #55).

   Paul, 2026-09-25: "I should be able to toggle things ... select the layers or
   what is shown on the maps via the marks, it all weaves together." So a map
   sheet's marks cards ARE its layers (Tableau's marks layers): card order is
   draw order (the first card draws on top), and each card has its own source,
   geography level, mark type, encodings, opacity, visibility and selectability.
   The Layer control that viewers see (Builder, Canvas, Board tiles, PAC
   evidence) is the same card list with only the eye and the pointer.

   This module is the model and the arithmetic only: no DOM, no network, no
   timers. map-engine.js draws it (the Albers SVG engine and the density canvas),
   map-streets.mjs compiles it to MapLibre layers, map-studio.js wires it into
   Chart Studio, evidence-map.js uses its presets. Exercised by node:test
   (app/tests/map-layers.test.js), which pins the hexagon math to
   geo/v2/lookup/hex_pins.json and the frame projection to geo/zcta_points.json.

   Money never gets a second calculation here. A layer's rows come from routes
   that already reconcile: the verified contribution query (industry=, D-030)
   or the PAC evidence payload (/api/evidence-geo, lane B's F6/F7/F12 rules).
   This module only regroups those rows by place with the v2 lookup tables, and
   a row without the precision a level needs is listed, never placed ("N of M
   located"). */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------ vocabulary */

  // The geography a layer is drawn at. Keys are the v2 file levels.
  const LEVELS = [
    ['state', 'State'], ['county', 'County'], ['cd', 'Congressional district'],
    ['sldu', 'State senate district'], ['sldl', 'State house district'], ['place', 'City'],
    ['zcta', 'ZIP code'], ['tract', 'Census tract'], ['hex10', 'Hexagon, 10 mi'],
    ['hex25', 'Hexagon, 25 mi'], ['hex50', 'Hexagon, 50 mi'], ['point', 'Points']];
  const LEVEL_IDS = LEVELS.map(level => level[0]);
  const levelLabel = id => (LEVELS.find(level => level[0] === id) || [id, id])[1];
  // A level inside a sentence: "by state", "by congressional district", but "by ZIP code" (an acronym keeps its case).
  const levelWord = level => levelLabel(level).replace(/^[A-Z](?=[a-z])/, letter => letter.toLowerCase());
  const HEX_LEVELS = ['hex10', 'hex25', 'hex50'];
  const PER_STATE_LEVELS = ['sldu', 'sldl', 'place', 'zcta', 'tract', 'hex10'];
  const isHex = level => HEX_LEVELS.includes(level);

  // The marks a layer can draw (Tableau's map marks). Filled is stored as
  // 'filled-map', lane M's id for a filled map, so an older console opens card
  // one of a map sheet as the map it already knows.
  const MARKS = [['automatic', 'Automatic'], ['filled-map', 'Filled'], ['circle', 'Circle'], ['shape', 'Shape'],
    ['density', 'Density'], ['text', 'Text'], ['line', 'Line']];
  const MARK_IDS = MARKS.map(mark => mark[0]);
  const markName = id => (MARKS.find(mark => mark[0] === id) || [id, id])[1];
  // Automatic draws what Tableau draws: a filled shape where the level has
  // shapes, a circle per point where it has only points.
  const markOf = card => {
    const type = card && MARK_IDS.includes(card.type) ? card.type : 'automatic';
    if (type !== 'automatic') return type;
    return card && card.geography === 'point' ? 'circle' : 'filled-map';
  };

  // Aggregate by (#53, #49's strip): what each choice sets on the open layer.
  const AGGREGATE_BY = [
    {id: 'state', label: 'State', geography: 'state'},
    {id: 'county', label: 'County', geography: 'county'},
    {id: 'district', label: 'District', geography: 'cd'},
    {id: 'zip', label: 'ZIP code', geography: 'zcta'},
    {id: 'tract', label: 'Tract', geography: 'tract'},
    {id: 'hexbin', label: 'Hexbin', geography: 'hex25'},
    {id: 'density', label: 'Density', geography: 'point', type: 'density'},
    {id: 'points', label: 'Points', geography: 'point', type: 'circle'}];
  const HEX_SIZES = [10, 25, 50];
  function aggregateOf(card) {
    if (!card) return 'state';
    if (isHex(card.geography)) return 'hexbin';
    if (card.geography === 'point') return markOf(card) === 'density' ? 'density' : 'points';
    const found = AGGREGATE_BY.find(entry => entry.geography === card.geography);
    return found ? found.id : 'state';
  }

  // What each level can place, by the precision a row carries. Mirrors
  // geo_lookup.PLACEABLE (the console side of lane G): a tract view places
  // only street addresses, a ZIP view addresses and ZIPs, a state view every
  // row that names a state.
  const PLACEABLE = {
    point: ['address', 'zip', 'city'], hexbin: ['address', 'zip', 'city'], tract: ['address'],
    zip: ['address', 'zip'], place: ['address', 'zip', 'city'], county: ['address', 'zip', 'city'],
    district: ['address', 'zip', 'city'], sld: ['address', 'zip', 'city'],
    state: ['address', 'zip', 'city', 'state']};
  const FAMILY = {state: 'state', county: 'county', cd: 'district', sldu: 'sld', sldl: 'sld', place: 'place',
    zcta: 'zip', tract: 'tract', hex10: 'hexbin', hex25: 'hexbin', hex50: 'hexbin', point: 'point'};
  const canPlace = (precision, level) => (PLACEABLE[FAMILY[level]] || []).includes(precision);
  const NEEDS = {tract: 'a street address', zip: 'a ZIP code', point: 'a ZIP, city or street address',
    hexbin: 'a ZIP, city or street address', place: 'a ZIP, city or street address',
    county: 'a ZIP, city or street address', district: 'a ZIP, city or street address',
    sld: 'a ZIP, city or street address', state: 'a state'};

  // The Map Layers pane (#54). Nothing here is a layer: it is the backdrop.
  const BACKGROUNDS = [['boundaries', 'Boundaries (Albers)'], ['light', 'Light'], ['dark', 'Dark'],
    ['streets', 'Streets (offline)'], ['none', 'None']];
  // Base layers, each drawn only where the renderer has it. `scale` is the
  // Albers zoom (1 = the whole country) a line needs before it draws; `zoom`
  // is the MapLibre zoom. `streets: false` marks what only the SVG draws.
  const BASE_LAYERS = [
    {id: 'coastlines', label: 'Coastlines', albers: true, streets: true},
    {id: 'state-borders', label: 'State borders', albers: true, streets: true},
    {id: 'state-names', label: 'State names', albers: true, streets: true},
    {id: 'county-borders', label: 'County borders', albers: true, scale: 2, streets: true, zoom: 6},
    {id: 'county-names', label: 'County names', albers: true, scale: 5, streets: true, zoom: 8},
    {id: 'cd', label: 'Congressional districts', albers: true, streets: true},
    {id: 'sld', label: 'State legislative districts', albers: true, scale: 3, streets: true, zoom: 7},
    {id: 'zcta', label: 'ZIP code boundaries', albers: true, scale: 6, streets: true, zoom: 10},
    {id: 'cities', label: 'Cities', albers: true, scale: 3, streets: true},
    {id: 'water', label: 'Water', albers: false, streets: true},
    {id: 'parks', label: 'Parks', albers: false, streets: true},
    {id: 'streets', label: 'Streets and highways', albers: false, streets: true, zoom: 10}];
  const BASE_IDS = BASE_LAYERS.map(layer => layer.id);
  const DEFAULT_BASE = ['coastlines', 'state-borders', 'state-names', 'county-borders', 'water', 'parks', 'streets'];
  const DATA_BY = [['state', 'State'], ['county', 'County'], ['zcta', 'ZIP code'], ['tract', 'Census tract']];
  const RAMP_IDS = ['teal', 'purple', 'mauve', 'gold', 'heat'];
  const RAMP_LABELS = {teal: 'Teal', purple: 'Purple', mauve: 'Mauve', gold: 'Gold', heat: 'Heat'};
  const DEFAULT_OPTIONS = {scale: true, units: 'us', search: true, toolbar: true, panZoom: true, layerControl: true};
  const OPTION_LABELS = {scale: 'Show map scale', search: 'Show map search', toolbar: 'Show view toolbar',
    panZoom: 'Allow pan and zoom', layerControl: 'Layer control'};
  const DEFAULT_RADIUS = {density: 18, circle: 10, shape: 10};
  const DEFAULT_INTENSITY = 70;
  const MAX_CARDS = 12;
  const BASES = ['receipt', 'original_observed', 'original_supported', 'raw', 'pac_activity'];
  // One side of a PAC's spending (#55's Opposition): a Spent source may take
  // `stance`, and its layer then carries only that side's money - the server's
  // own per-place sum over every candidate, never netted against the other
  // side. A Filled layer may draw its places with the hatch (`map.pattern`)
  // instead of a colour ramp, in the evidence map's opposition ink.
  const STANCES = ['support', 'oppose'];
  const PATTERN_IDS = ['hatch'];
  const HATCH_INK = '#9b3d5b';

  /* ------------------------------------------------------- cards as layers */

  const plain = value => !!value && typeof value === 'object' && !Array.isArray(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const text = (value, most) => typeof value === 'string' && value.trim().length >= 1 && value.trim().length <= most;
  const whole = value => typeof value === 'number' && Number.isInteger(value);

  const isLayerCard = card => plain(card) && typeof card.geography === 'string';
  // A map sheet: every card names a geography. (A card set that mixes layers
  // and dual-axis cards is malformed; chart-studio-core.js refuses it.)
  const mapLayersOn = sheet => plain(sheet) && Array.isArray(sheet.marksCards) && sheet.marksCards.length > 0
    && sheet.marksCards.every(isLayerCard);

  // A layer's own source: an industry snapshot addressed by its label (D-030),
  // or one PAC evidence pane (the /api/evidence-geo request).
  function sourceFault(source) {
    if (!plain(source)) return 'a layer needs a source';
    if (source.kind === 'industry') {
      if (Object.keys(source).some(key => !['kind', 'industry', 'basis', 'label'].includes(key))) return 'an industry source has an unknown key';
      if (!text(source.industry, 120)) return 'an industry source names its industry';
      if ('basis' in source && !BASES.includes(source.basis)) return 'an industry source names a known basis';
      if ('label' in source && !text(source.label, 120)) return 'a source label must be text';
      return '';
    }
    if (source.kind === 'pac') {
      if (Object.keys(source).some(key => !['kind', 'source', 'id', 'committee', 'cycle', 'direction', 'name', 'industry', 'stance'].includes(key))) return 'a PAC source has an unknown key';
      if (!['snapshot', 'csv'].includes(source.source)) return 'a PAC source is a snapshot or an imported file';
      if (typeof source.id !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,99}$/.test(source.id)) return 'a PAC source names its snapshot or file';
      if (typeof source.committee !== 'string' || !/^C\d{8}$/.test(source.committee)) return 'a PAC source names its committee';
      if (typeof source.cycle !== 'string' || !/^\d{4}$/.test(source.cycle)) return 'a PAC source names one cycle';
      if (!['raised', 'spent'].includes(source.direction)) return 'a PAC source is Raised or Spent';
      for (const key of ['name', 'industry']) if (key in source && !text(source[key], 120)) return 'a PAC source ' + key + ' must be text';
      if ('stance' in source) {
        if (!STANCES.includes(source.stance)) return 'a PAC source takes one side, support or oppose, never the two netted';
        if (source.direction !== 'spent') return 'support and oppose are sides of Spent; a Raised source has neither';
      }
      return '';
    }
    return 'a layer source is an industry or a PAC network';
  }
  function cardMapFault(options) {
    if (!plain(options) || Object.keys(options).some(key => !['radius', 'intensity', 'ramp', 'pattern'].includes(key))) return 'a layer’s map options are malformed';
    if ('radius' in options && (!whole(options.radius) || options.radius < 2 || options.radius > 80)) return 'a layer radius is 2 to 80 px';
    if ('intensity' in options && (!whole(options.intensity) || options.intensity < 1 || options.intensity > 100)) return 'a layer intensity is 1 to 100';
    if ('ramp' in options && !RAMP_IDS.includes(options.ramp)) return 'a layer ramp is teal, purple, mauve, gold or heat';
    if ('pattern' in options && !PATTERN_IDS.includes(options.pattern)) return 'a layer pattern is a hatch';
    return '';
  }
  // The map fields of one card; lane M's cardFault checks everything else.
  function layerFault(card) {
    if (!plain(card)) return 'a card is not an object';
    if ('source' in card) { const fault = sourceFault(card.source); if (fault) return fault; }
    if ('geography' in card) {
      if (!LEVEL_IDS.includes(card.geography)) return 'geography must be a map level';
      if (!MARK_IDS.includes(card.type)) return 'a map layer draws Filled, Circle, Shape, Density, Text or Line';
    }
    if ('map' in card) {
      if (!('geography' in card)) return 'map options belong to a map layer';
      const fault = cardMapFault(card.map);
      if (fault) return fault;
      if ('pattern' in card.map && markOf(card) !== 'filled-map') return 'a hatch fills shapes: it belongs to a Filled layer';
    }
    return '';
  }
  // A sheet's `map`: today's boundary choices plus the Map Layers pane.
  const MAP_KEYS = ['layers', 'congress', 'background', 'washout', 'base', 'data', 'options'];
  function mapSettingsFault(map) {
    if (!plain(map)) return 'map settings are an object';
    if (Object.keys(map).some(key => !MAP_KEYS.includes(key))) return 'map settings have an unknown key';
    if ('layers' in map && (!Array.isArray(map.layers) || !map.layers.every(item => typeof item === 'string'))) return 'map layers are names';
    if ('congress' in map && typeof map.congress !== 'string') return 'congress is text';
    if ('background' in map && !BACKGROUNDS.some(entry => entry[0] === map.background)) return 'background is Boundaries, Light, Dark, Streets or None';
    if ('washout' in map && (!whole(map.washout) || map.washout < 0 || map.washout > 100)) return 'washout is 0 to 100';
    if ('base' in map) {
      const base = map.base;
      if (!Array.isArray(base) || base.length > BASE_IDS.length || new Set(base).size !== base.length || !base.every(id => BASE_IDS.includes(id))) return 'base layers are names from the Map Layers pane';
    }
    if ('data' in map && map.data !== null) {
      const data = map.data;
      if (!plain(data) || Object.keys(data).sort().join() !== 'by,measure,using') return 'a data layer is {measure, by, using}';
      if (!plain(data.measure) || !whole(data.measure.column) || data.measure.column < 0 || typeof data.measure.aggregation !== 'string'
        || Object.keys(data.measure).some(key => !['column', 'aggregation'].includes(key))) return 'a data layer measure is a pill';
      if (!DATA_BY.some(entry => entry[0] === data.by)) return 'a data layer is by state, county, ZIP or tract';
      if (!RAMP_IDS.includes(data.using)) return 'a data layer names a ramp';
    }
    if ('options' in map) {
      const options = map.options;
      if (!plain(options) || Object.keys(options).some(key => !(key in DEFAULT_OPTIONS))) return 'map options have an unknown key';
      for (const key of Object.keys(options)) {
        if (key === 'units' ? !['us', 'metric'].includes(options.units) : typeof options[key] !== 'boolean') return 'map option ' + key + ' is malformed';
      }
    }
    return '';
  }
  // Every setting the pane shows, with defaults for what the sheet leaves out.
  function settingsOf(sheet) {
    const map = plain(sheet) && plain(sheet.map) ? sheet.map : {};
    return {
      background: BACKGROUNDS.some(entry => entry[0] === map.background) ? map.background : 'boundaries',
      washout: whole(map.washout) ? Math.max(0, Math.min(100, map.washout)) : 0,
      base: Array.isArray(map.base) ? map.base.filter(id => BASE_IDS.includes(id)) : DEFAULT_BASE.slice(),
      data: plain(map.data) ? clone(map.data) : null,
      options: Object.assign({}, DEFAULT_OPTIONS, plain(map.options) ? map.options : {}),
      congress: typeof map.congress === 'string' ? map.congress : '119'};
  }
  // Writes pane settings back, keeping today's keys (layers, congress).
  function writeSettings(sheet, settings) {
    const map = plain(sheet.map) ? sheet.map : {};
    // Today's two keys stay present: the studio's boundary panel reads both.
    const next = Object.assign({layers: ['states', 'districts'], congress: '119'}, map);
    for (const key of ['background', 'washout', 'base', 'data', 'options']) if (settings[key] !== undefined) next[key] = clone(settings[key]);
    sheet.map = next;
    return sheet;
  }

  const blankEncodings = () => ({color: null, size: null, label: null, detail: [], tooltip: [], shape: null, path: null, angle: null});
  // A complete card in lane M's schema, with the map fields. `fields` is the
  // sheet's field list, used to put the measure on Colour (or Size).
  function newLayerCard(spec) {
    const settings = spec || {};
    const card = {id: settings.id || 'layer-1', name: settings.name || '', type: MARK_IDS.includes(settings.type) ? settings.type : 'automatic',
      encodings: Object.assign(blankEncodings(), settings.encodings ? clone(settings.encodings) : {}),
      opacity: whole(settings.opacity) ? settings.opacity : 100, border: 'automatic', halo: 'automatic',
      visible: settings.visible !== false, selectable: settings.selectable !== false, zoomExtent: settings.zoomExtent !== false,
      source: clone(settings.source), geography: LEVEL_IDS.includes(settings.geography) ? settings.geography : 'state'};
    if (plain(settings.map) && Object.keys(settings.map).length) card.map = clone(settings.map);
    return card;
  }
  // The next free card id in a sheet ('layer-1', 'layer-2', ...).
  function nextLayerId(cards) {
    const taken = new Set((cards || []).map(card => card.id));
    for (let n = 1; n <= MAX_CARDS + 1; n += 1) if (!taken.has('layer-' + n)) return 'layer-' + n;
    return 'layer-' + Date.now();
  }
  // One source is one payload: a side of Spent (`stance`) is the same source read
  // for one side, so a fill and its opposition count as one source.
  const sourceKey = source => !plain(source) ? '' : source.kind === 'industry'
    ? ['industry', source.industry, source.basis || 'receipt'].join('|')
    : ['pac', source.source, source.id, source.committee, source.cycle, source.direction].join('|');
  const cycleLabel = cycle => /^\d{4}$/.test(String(cycle || '')) ? (Number(cycle) - 1) + '–' + cycle : String(cycle || '');
  // The source as the data-source chip names it: the pane, without a side.
  function paneLabel(source) {
    if (!plain(source)) return 'No source';
    if (source.kind === 'industry') return source.label || source.industry;
    return (source.name || source.committee) + ' · ' + (source.direction === 'raised' ? 'Raised' : 'Spent') + ' ' + cycleLabel(source.cycle);
  }
  const STANCE_WORDS = {support: 'support only', oppose: 'opposition only'};
  const sourceLabel = source => paneLabel(source) + (plain(source) && STANCE_WORDS[source.stance] ? ' · ' + STANCE_WORDS[source.stance] : '');
  // An imported workbook is PAC evidence read from a file (evidence.js calls it an imported file).
  const sourceBadge = source => !plain(source) ? '' : source.kind === 'industry' ? (source.label || source.industry)
    : source.source === 'csv' ? 'Imported file' : 'PAC evidence';
  // A layer's name: its own, else "<what it shows> · <how it draws>".
  function layerName(card, fields) {
    if (card && card.name) return card.name;
    const measure = measureField(card, 'color', fields) || measureField(card, 'size', fields);
    const pac = card && card.source && card.source.kind === 'pac';
    // A PAC layer's amount is its receipts or its spending (or one side of it), so it says which.
    const stance = pac ? card.source.stance : '';
    const what = pac ? (card.source.direction === 'raised' ? (measure && measure.id === 'contributors' ? 'Donors' : 'Receipts')
      : stance === 'oppose' ? 'Opposition' : stance === 'support' ? 'Support spending' : 'Spending')
      : measure ? measure.name : 'Giving';
    const by = card && card.geography === 'point' ? (markOf(card) === 'density' ? 'density' : 'points') : 'by ' + levelWord(card && card.geography);
    return what + ' ' + by;
  }

  /* ---------------------------------------------------------- encodings */

  // Which encodings each map mark draws. Paul, 2026-09-19: nothing silently
  // inert - a row a mark cannot draw is greyed with the reason, never hidden.
  const ENCODING_REASON = {
    size: {'filled-map': 'A filled shape takes its size from its boundary, so Size does not apply.',
      line: '', text: '', density: ''},
    label: {density: 'Density draws how concentrated the money is, not single marks, so Label does not apply.'},
    detail: {all: 'A layer’s level of detail is its geography: choose it under Aggregate by.'},
    tooltip: {density: 'Density draws concentration, not single marks, so it has no tooltip; Points and Hexbin do.'}};
  function markRows(card) {
    return ['color', 'size', 'label', 'detail', 'tooltip', ...(markOf(card) === 'shape' ? ['shape'] : [])];
  }
  const HATCH_COLOUR_REASON = 'A hatch draws every place this layer holds the same way; its amount is in the tooltip, so Colour does not apply. Choose Solid colour under Pattern to colour it.';
  // What a layer draws, in one word (#53: a hexbin layer's card reads "Hexbin"): a Filled
  // layer at a hexagon level is Tableau's hexbin; every other layer is its mark.
  const markSummary = card => markOf(card) === 'filled-map' && card && isHex(card.geography) ? 'Hexbin' : markName(markOf(card));
  // The level a layer is aggregated by, as the Aggregate by popover names it ("Hexbin, 25 mi").
  function aggregateLabel(card) {
    const id = aggregateOf(card);
    if (id === 'hexbin') return 'Hexbin, ' + ((card && isHex(card.geography) ? card.geography.slice(3) : '25')) + ' mi';
    if (id === 'state' && card && card.geography !== 'state') return levelLabel(card.geography);
    return (AGGREGATE_BY.find(entry => entry.id === id) || {label: levelLabel(card && card.geography)}).label;
  }
  function encodingSupport(card) {
    const mark = markOf(card), out = {};
    for (const zone of markRows(card)) {
      const table = ENCODING_REASON[zone] || {};
      const reason = table.all || table[mark] || '';
      out[zone] = reason || true;
    }
    // Detail names the level this layer is drawn at (a hexbin says Hexbin, never a state map).
    if (out.detail !== true) out.detail = 'A layer’s level of detail is its geography (here ' + aggregateLabel(card) + '): choose it under Aggregate by.';
    if (mark === 'filled-map' && card && plain(card.map) && PATTERN_IDS.includes(card.map.pattern)) out.color = HATCH_COLOUR_REASON;
    return out;
  }
  // The greyed row's short words (Chart Studio shows them in the empty drop zone).
  function reasonShort(card, zone) {
    if (zone === 'detail') return 'Set by Aggregate by: ' + aggregateLabel(card);
    return 'Not drawn for ' + markSummary(card);
  }
  const SIZE_MEANS = {density: 'Size sets the density radius.', circle: 'Size sets each circle’s area by its measure.',
    shape: 'Size sets each shape’s area by its measure.', text: 'Size sets the label size.', line: 'Size sets the line width.'};
  // The field ids a layer can show. Every source carries these three, so a
  // pill placed from the Data pane means the same money on every layer.
  const MEASURES = {amount: 'amount', records: 'rows', rows: 'rows', contributors: 'contributors'};
  function measureField(card, zone, fields) {
    const pill = card && card.encodings ? card.encodings[zone] : null;
    if (!pill || pill.column == null || !Array.isArray(fields)) return null;
    const field = fields.find(f => f.index === pill.column) || fields[pill.column];
    if (!field || !(field.id in MEASURES)) return null;
    return {id: MEASURES[field.id], name: field.name || field.id, aggregation: pill.aggregation || 'sum'};
  }
  const dimensionName = (card, zone, fields) => {
    const pill = card && card.encodings ? card.encodings[zone] : null;
    if (!pill || pill.column == null || !Array.isArray(fields)) return null;
    const field = fields.find(f => f.index === pill.column) || fields[pill.column];
    return field && !(field.id in MEASURES) ? field : null;
  };

  /* -------------------------------------------------------- the geometry */

  // The 975x610 frame lane G's v2 Albers paths are drawn in (build_district_geo
  // REGIONS; geo/v2/index.json frame.projection): spherical Albers, one conic
  // per region; Hawaii's also carries its clip extent (lane L3). Pinned to
  // geo/zcta_points.json and geo/cd119.json by the node tests.
  const FRAME = {width: 975, height: 610, regions: {
    alaska: {parallels: [55.0, 65.0], meridian: -154.0, origin: 50.0, scale: 376.541954, translate: [147.942585, 565.23175], statefp: ['02']},
    caribbean: {parallels: [18.0, 18.5], meridian: -66.4, origin: 17.8, scale: 1075.834155, translate: [903.882975, 578.763317], statefp: ['72', '78']},
    hawaii: {parallels: [8.0, 18.0], meridian: -157.0, origin: 13.0, scale: 1054.448054, translate: [313.986915, 696.598075], statefp: ['15'], clip: [247.0, 497.0, 371.0, 610.0]},
    lower48: {parallels: [29.5, 45.5], meridian: -96.0, origin: 37.5, scale: 1075.834155, translate: [495.794672, 269.296591], statefp: null}}};
  const RAD = Math.PI / 180;
  function regionOf(statefp) {
    const code = String(statefp || '');
    for (const [name, region] of Object.entries(FRAME.regions)) if (region.statefp && region.statefp.includes(code)) return name;
    return 'lower48';
  }
  function projectFrame(latitude, longitude, statefp) {
    const lat = Number(latitude), lon = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    const region = FRAME.regions[regionOf(statefp)];
    const f1 = region.parallels[0] * RAD, f2 = region.parallels[1] * RAD, f0 = region.origin * RAD, l0 = region.meridian * RAD;
    const n = (Math.sin(f1) + Math.sin(f2)) / 2, C = Math.cos(f1) * Math.cos(f1) + 2 * n * Math.sin(f1);
    const p = Math.sqrt(Math.max(0, C - 2 * n * Math.sin(lat * RAD))) / n;
    const p0 = Math.sqrt(Math.max(0, C - 2 * n * Math.sin(f0))) / n;
    // The longitude difference is wrapped into [-180, 180] degrees, as build_district_geo's
    // Albers and d3's rotation wrap it: Alaska's islands east of 180 degrees sit at the
    // west end of the Aleutian chain, not on the far side of the cone.
    let delta = lon * RAD - l0;
    if (delta > Math.PI) delta -= 2 * Math.PI;
    else if (delta < -Math.PI) delta += 2 * Math.PI;
    const t = n * delta;
    const x = p * Math.sin(t), y = p0 - p * Math.cos(t);
    const at = [region.translate[0] + region.scale * x, region.translate[1] - region.scale * y];
    // A region with a clip extent places nothing outside it, as d3's clipExtent: Hawaii's
    // Northwestern Islands, which its conic draws inside the Alaska box, come back null.
    const clip = region.clip;
    if (clip && !(at[0] >= clip[0] && at[0] <= clip[2] && at[1] >= clip[1] && at[1] <= clip[3])) return null;
    return at;
  }

  // EPSG:5070 (NAD83 / Conus Albers, GRS80 ellipsoid) and the pointy-top
  // hexagon id, ported from geo_lookup.py line for line; pinned to
  // geo/v2/lookup/hex_pins.json (1,000 pyproj points) by the node test.
  const GRS80_A = 6378137.0, GRS80_F = 1 / 298.257222101, E2 = GRS80_F * (2 - GRS80_F), E = Math.sqrt(E2);
  const LAT0 = 23.0, LON0 = -96.0, LAT1 = 29.5, LAT2 = 45.5, MILE = 1609.344, SQRT3 = Math.sqrt(3);
  const qOf = sinPhi => { const con = E * sinPhi; return (1 - E2) * (sinPhi / (1 - con * con) - (0.5 / E) * Math.log((1 - con) / (1 + con))); };
  const mOf = phi => { const s = Math.sin(phi); return Math.cos(phi) / Math.sqrt(1 - E2 * s * s); };
  const M1 = mOf(LAT1 * RAD), M2 = mOf(LAT2 * RAD), Q0 = qOf(Math.sin(LAT0 * RAD)), Q1 = qOf(Math.sin(LAT1 * RAD)), Q2 = qOf(Math.sin(LAT2 * RAD));
  const N5070 = (M1 * M1 - M2 * M2) / (Q2 - Q1), C5070 = M1 * M1 + N5070 * Q1, RHO0 = GRS80_A * Math.sqrt(C5070 - N5070 * Q0) / N5070;
  function albers5070(latitude, longitude) {
    const delta = (((Number(longitude) - LON0 + 180) % 360) + 360) % 360 - 180;
    const inner = C5070 - N5070 * qOf(Math.sin(Number(latitude) * RAD));
    const rho = GRS80_A * Math.sqrt(Math.max(inner, 0)) / N5070;
    const theta = N5070 * delta * RAD;
    return [rho * Math.sin(theta), RHO0 - rho * Math.cos(theta)];
  }
  function hexAxial(x, y, sizeMiles) {
    const side = Number(sizeMiles) * MILE / SQRT3;
    const qf = (SQRT3 / 3 * x - y / 3) / side, rf = (2 / 3 * y) / side, sf = -qf - rf;
    let q = Math.floor(qf + 0.5), r = Math.floor(rf + 0.5);
    const s = Math.floor(sf + 0.5);
    const dq = Math.abs(q - qf), dr = Math.abs(r - rf), ds = Math.abs(s - sf);
    if (dq > dr && dq > ds) q = -r - s;
    else if (dr > ds) r = -q - s;
    return [q === 0 ? 0 : q, r === 0 ? 0 : r];
  }
  function hexId(latitude, longitude, sizeMiles) {
    const lat = Number(latitude), lon = Number(longitude), size = Number(sizeMiles);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !HEX_SIZES.includes(size)) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180 || (lat === 0 && lon === 0)) return null;
    const [x, y] = albers5070(lat, lon);
    const [q, r] = hexAxial(x, y, size);
    return 'h' + size + ':' + q + ':' + r;
  }

  /* ------------------------------------------------------------ lookups */

  // The v2 lookup tables, loaded on demand through `fetchJson(path)` (the
  // browser passes fetch; the node test passes files). zcta/<first digit>.json
  // holds every ZCTA's internal point and its GEOIDs at every level.
  function createLookup(fetchJson, base) {
    const prefix = base || '/geo/v2/lookup/';
    let states = null, statesRequest = null;
    const zcta = new Map(), zctaRequests = new Map();
    const uspsOf = {}, fipsOf = {};
    async function loadStates() {
      if (states) return states;
      if (!statesRequest) statesRequest = Promise.resolve(fetchJson(prefix + 'states.json')).then(body => {
        states = body || {rows: {}, by_usps: {}};
        for (const [fips, row] of Object.entries(states.rows || {})) { uspsOf[fips] = row[0]; fipsOf[row[0]] = fips; }
        return states;
      });
      return statesRequest;
    }
    async function loadDigit(digit) {
      if (zcta.has(digit)) return zcta.get(digit);
      if (!zctaRequests.has(digit)) zctaRequests.set(digit, Promise.resolve(fetchJson(prefix + 'zcta/' + digit + '.json')).then(body => {
        const table = body || {fields: [], rows: {}};
        zcta.set(digit, table);
        return table;
      }));
      return zctaRequests.get(digit);
    }
    const zipOf = value => { const digits = String(value == null ? '' : value).replace(/\D/g, '').slice(0, 5); return digits.length === 5 ? digits : ''; };
    return {
      // Loads every table the given ZIPs need; resolves when all are ready.
      async ready(zips) {
        await loadStates();
        const digits = new Set();
        for (const zip of zips || []) { const code = zipOf(zip); if (code) digits.add(code[0]); }
        await Promise.all([...digits].map(loadDigit));
        return this;
      },
      zcta(zip) {
        const code = zipOf(zip);
        const table = code ? zcta.get(code[0]) : null;
        const values = table && table.rows ? table.rows[code] : null;
        if (!values) return null;
        const row = {zcta: code};
        (table.fields || []).forEach((field, at) => { row[field] = values[at]; });
        return row;
      },
      usps: fips => uspsOf[String(fips || '')] || '',
      fips: usps => fipsOf[String(usps || '').toUpperCase()] || '',
      // 'NY-12' / 'AK-AL' from a 4-digit district GEOID, as geo/cd*.json key them.
      districtKey(geoid) {
        const value = Array.isArray(geoid) ? (Array.isArray(geoid[0]) ? geoid[0][0] : geoid[0]) : geoid;
        const code = String(value || '');
        if (!/^\d{4}$/.test(code)) return '';
        const usps = uspsOf[code.slice(0, 2)], seat = code.slice(2);
        if (!usps) return '';
        return seat === '00' || seat === '98' ? usps + '-AL' : usps + '-' + seat;
      },
      loaded: () => ({states: !!states, zcta: [...zcta.keys()].sort()})
    };
  }
  // A lookup value may be a GEOID or [[geoid, share], ...] (a split); the
  // largest share is first, as geo_lookup._primary reads it.
  const primary = value => Array.isArray(value) ? (Array.isArray(value[0]) ? String(value[0][0]) : String(value[0])) : value == null ? '' : String(value);

  /* ----------------------------------------------------------------- rows */

  // The rows a layer places, one per place the source already grouped:
  // {zip, state, amount (dollars), rows, contributors, support, oppose,
  //  precision, key?, level?}. `key`/`level` carry a place the SERVER already
  // placed (a district, a state), which the layer uses as-is.
  function rowsFromContributions(result) {
    const out = [];
    for (const row of (result && result.rows) || []) {
      const zip = /^\d{5}$/.test(String(row.zip5 || '').trim()) ? String(row.zip5).trim() : '';
      const state = String(row.state || '').trim().toUpperCase();
      const amount = Number(row.amount);
      out.push({zip, state, amount: Number.isFinite(amount) ? amount / 100 : 0,
        rows: Number(row.records) || 0, contributors: Number(row.contributors) || 0, support: 0, oppose: 0,
        precision: zip ? 'zip' : /^[A-Z]{2}$/.test(state) ? 'state' : 'none'});
    }
    return out;
  }
  // A PAC evidence payload, for one level. The state and district levels use
  // the server's own placements (`kinds`: a street address first, then the
  // ZIP's land-area shares, then the state; for Spent the candidate's seat or
  // the committee's state, support and oppose kept apart), so they add up to
  // exactly what the evidence map and its ranked list show. Every finer level
  // of Raised money uses the contributor ZIPs (`by_zip`, the evidence map's
  // ZIP layer), and what no ZIP places - a row placed by its state or its
  // street address alone - is listed with that reason, never dropped.
  // Kinds the evidence map ships switched off (conversions, S6 ruling 12)
  // are listed under their own reason unless `options.off` says otherwise.
  function rowsFromEvidence(body, level, options) {
    const out = [], payload = body || {};
    const settings = options || {};
    const off = settings.off instanceof Set ? settings.off
      : new Set((payload.kinds || []).filter(kind => kind.default_on === false).map(kind => kind.kind));
    const serverLevel = level === 'state' || level === 'cd';
    // One side of Spent (`options.stance`, #55's Opposition): each place's support or
    // oppose sum as the server made it - over every candidate there, the other side
    // kept apart, never netted. The server counts rows per place, not per side, and
    // does not split its unplaced money by side, so a side's rows carry no row count
    // (its legend counts dollars) and no unlocated rows.
    const stance = payload.direction === 'spent' && STANCES.includes(settings.stance) ? settings.stance : '';
    if (stance) {
      const side = amount => ({support: stance === 'support' ? amount : 0, oppose: stance === 'oppose' ? amount : 0});
      for (const kind of payload.kinds || []) {
        if (off.has(kind.kind)) {
          const amount = [...(kind.districts || []), ...(kind.states || [])].reduce((sum, row) => sum + (Number(row[stance]) || 0), 0);
          if (Math.abs(amount) > 0.004) out.push({zip: '', state: '', amount, rows: null, contributors: 0, support: 0, oppose: 0,
            precision: 'none', reason: (kind.label || kind.kind) + ' is switched off on this map'});
          continue;
        }
        for (const row of kind.districts || []) {
          const amount = Number(row[stance]) || 0;
          if (!amount) continue;
          const senate = /-SEN$/.test(row.key);
          out.push(Object.assign({zip: '', state: String(row.key).slice(0, 2), key: senate ? String(row.key).slice(0, 2) : row.key,
            level: senate ? 'state' : 'cd', seat: senate ? 'senate' : 'house', kind: kind.kind, amount, rows: null, contributors: 0,
            precision: 'seat'}, side(amount)));
        }
        for (const row of kind.states || []) {
          const amount = Number(row[stance]) || 0;
          if (!amount) continue;
          out.push(Object.assign({zip: '', state: row.key, key: row.key, level: 'state', kind: kind.kind, amount, rows: null,
            contributors: 0, precision: 'state'}, side(amount)));
        }
      }
      return out;
    }
    if (payload.direction === 'raised' && level === 'state' && Array.isArray(payload.by_state) && !off.size) {
      // The contributors' own state table: the server's placement, with donors.
      for (const row of payload.by_state) {
        out.push({zip: '', state: row.key, key: row.key, level: 'state', kind: 'contributors', amount: Number(row.amount) || 0,
          rows: Number(row.rows) || 0, contributors: Number(row.contributors) || 0, support: 0, oppose: 0, precision: 'state'});
      }
    } else if (payload.direction === 'raised' && !serverLevel) {
      let zipAmount = 0, zipRows = 0;
      for (const row of payload.by_zip || []) {
        const amount = Number(row.amount) || 0, rows = Number(row.rows) || 0;
        out.push({zip: String(row.zip || ''), state: String(row.state || '').toUpperCase(), amount, rows,
          contributors: Number(row.contributors) || 0, support: 0, oppose: 0, precision: row.zip ? 'zip' : 'state',
          city: row.city || ''});
        zipAmount += amount; zipRows += rows;
      }
      const locatedRows = (payload.kinds || []).reduce((sum, kind) => sum + (Number(kind.rows) || 0), 0);
      const residual = (Number(payload.located_total) || 0) - zipAmount, residualRows = Math.max(0, locatedRows - zipRows);
      if (Math.abs(residual) > 0.004 || residualRows > 0) out.push({zip: '', state: '', amount: residual, rows: residualRows,
        contributors: 0, support: 0, oppose: 0, precision: 'none', reason: 'placed by its state or street address, not by a ZIP'});
    } else {
      for (const kind of payload.kinds || []) {
        if (off.has(kind.kind)) {
          const amount = [...(kind.districts || []), ...(kind.states || [])].reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
          if (Math.abs(amount) > 0.004 || Number(kind.rows) > 0) out.push({zip: '', state: '', amount, rows: Number(kind.rows) || 0,
            contributors: 0, support: 0, oppose: 0, precision: 'none', reason: (kind.label || kind.kind) + ' is switched off on this map'});
          continue;
        }
        for (const row of kind.districts || []) {
          const senate = /-SEN$/.test(row.key);
          out.push({zip: '', state: String(row.key).slice(0, 2), key: senate ? String(row.key).slice(0, 2) : row.key,
            level: senate ? 'state' : 'cd', seat: senate ? 'senate' : 'house', kind: kind.kind, amount: Number(row.amount) || 0, rows: Number(row.rows) || 0,
            contributors: 0, support: Number(row.support) || 0, oppose: Number(row.oppose) || 0, precision: 'seat'});
        }
        for (const row of kind.states || []) {
          out.push({zip: '', state: row.key, key: row.key, level: 'state', kind: kind.kind, amount: Number(row.amount) || 0,
            rows: Number(row.rows) || 0, contributors: 0, support: Number(row.support) || 0, oppose: Number(row.oppose) || 0,
            precision: 'state'});
        }
      }
      // Kinds the reader switched off stay out of the map; `off` names them.
    }
    for (const row of payload.unlocated || []) {
      out.push({zip: '', state: '', amount: Number(row.amount) || 0, rows: Number(row.rows) || 0, contributors: 0,
        support: 0, oppose: 0, precision: 'none', reason: row.reason || 'no usable place'});
    }
    return out;
  }

  /* ---------------------------------------------------------- aggregation */

  // Rows -> places at one level. Returns every place's totals, what was
  // placed, and what was listed instead (with the reason). The totals of
  // placed + listed always equal the rows given: nothing is dropped.
  function aggregate(rows, level, lookup, options) {
    const settings = options || {};
    const congress = String(settings.congress || '119');
    const features = new Map(), points = [], listed = new Map();
    let placedRows = 0, placedAmount = 0, totalRows = 0, totalAmount = 0, placedPlaces = 0;
    // A row with no row count (one side of Spent) makes the located line count dollars.
    let counted = true;
    const rowsOf = row => Number(row.rows) || 0;
    const add = (key, row, extra) => {
      if (!features.has(key)) features.set(key, {id: key, amount: 0, rows: 0, contributors: 0, support: 0, oppose: 0, places: 0, label: ''});
      const feature = features.get(key);
      feature.amount += row.amount; feature.rows += rowsOf(row); feature.contributors += row.contributors;
      feature.support += row.support || 0; feature.oppose += row.oppose || 0; feature.places += 1;
      if (extra && extra.label && !feature.label) feature.label = extra.label;
      if (extra && extra.point && !feature.point) feature.point = extra.point;
      placedRows += rowsOf(row); placedAmount += row.amount;
    };
    const list = (reason, row) => {
      const entry = listed.get(reason) || {reason, rows: 0, amount: 0, places: 0};
      entry.rows += rowsOf(row); entry.amount += row.amount; entry.places += 1;
      listed.set(reason, entry);
    };
    for (const row of rows || []) {
      if (row.rows == null) counted = false;
      totalRows += rowsOf(row); totalAmount += row.amount;
      if (row.precision === 'none') { list(row.reason || 'no usable place', row); continue; }
      // A place the server already made (a seat, a state) is used as it is.
      if (row.level) {
        if (level === row.level) { add(row.key, row); continue; }
        if (level === 'state' && row.state && /^[A-Z]{2}$/.test(row.state)) { add(row.state, row); continue; }
        list(row.level === 'cd' ? 'placed by candidate seat, not by ' + levelWord(level)
          : row.seat === 'senate' ? 'a Senate race, placed by its state, not by ' + levelWord(level)
            : 'placed by state, not by ' + levelWord(level), row);
        continue;
      }
      if (level === 'state') {
        if (/^[A-Z]{2}$/.test(row.state)) add(row.state, row); else list('no state', row);
        continue;
      }
      if (!canPlace(row.precision, level)) { list('needs ' + (NEEDS[FAMILY[level]] || 'a finer place'), row); continue; }
      const found = row.zip && lookup ? lookup.zcta(row.zip) : null;
      if (!found) { list('ZIP ' + (row.zip || '(none)') + ' is not a Census ZCTA', row); continue; }
      const fips = String(found.state || '');
      const point = [Number(found.lat), Number(found.lon)];
      let key = '';
      if (level === 'county') key = primary(found.county);
      else if (level === 'cd') key = lookup.districtKey(found['cd' + congress] || found.cd119);
      else if (level === 'sldu' || level === 'sldl') key = primary(found[level]);
      else if (level === 'place') key = primary(found.place);
      else if (level === 'zcta') key = found.zcta;
      else if (level === 'tract') key = primary(found.tract);
      else if (isHex(level)) key = primary(found['h' + level.slice(3)]) || hexId(point[0], point[1], Number(level.slice(3)));
      else if (level === 'point') key = 'z' + found.zcta;
      if (!key) { list('ZIP ' + row.zip + ' has no ' + levelWord(level), row); continue; }
      add(key, row, {point, label: level === 'zcta' ? found.zcta : ''});
      if (level === 'point') {
        const feature = features.get(key);
        feature.statefp = fips; feature.zip = found.zcta;
      } else {
        const feature = features.get(key);
        if (!feature.statefp) feature.statefp = fips;
      }
      points.push({lat: point[0], lon: point[1], statefp: fips, amount: row.amount, rows: rowsOf(row), contributors: row.contributors, key});
    }
    for (const feature of features.values()) placedPlaces += 1;
    const unplaced = [...listed.values()].sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
    return {level, features, points, unplaced,
      placed: {rows: placedRows, amount: placedAmount, places: placedPlaces},
      total: {rows: totalRows, amount: totalAmount},
      unit: counted ? 'rows' : 'amount',
      line: counted ? locatedLine(placedRows, totalRows) : amountLine(placedAmount, totalAmount)};
  }
  const count = value => Math.round(Number(value) || 0).toLocaleString('en-US');
  const locatedLine = (placed, total) => count(placed) + ' of ' + count(total) + ' located';
  const amountLine = (placed, total) => money(placed, 'amount') + ' of ' + money(total, 'amount') + ' located';

  /* -------------------------------------------------------------- colour */

  const THEME = () => (root && root.MentatTheme) || (typeof module !== 'undefined' && module.exports ? safeTheme() : null);
  function safeTheme() { try { return require('./theme.js'); } catch (error) { return null; } }
  const FALLBACK = ['#327a7a', '#57447b', '#8b6984', '#eca91c'];
  const slotColour = at => { const theme = THEME(); return (theme && theme.categorical && theme.categorical[at]) || FALLBACK[at]; };
  function mix(hex, toward, weight) {
    const channel = (value, at) => parseInt(value.slice(1 + at, 3 + at), 16);
    return '#' + [0, 2, 4].map(at => {
      const from = channel(hex, at);
      return Math.round(from + (channel(toward, at) - from) * weight).toString(16).padStart(2, '0');
    }).join('');
  }
  // Sequential ramps built from MentatTheme's own slots (never a new
  // palette): the plain base, then the slot, then a darker shade of it. Heat
  // is the density ramp: purple into mauve into gold (#47's legend).
  function rampStops(id) {
    const slot = {teal: 0, purple: 1, mauve: 2, gold: 3}[id];
    if (slot != null) {
      const hue = slotColour(slot);
      return [mix(hue, '#ffffff', 0.9), mix(hue, '#ffffff', 0.6), mix(hue, '#ffffff', 0.25), hue, mix(hue, '#000000', 0.35)];
    }
    return [mix(slotColour(1), '#ffffff', 0.35), slotColour(1), slotColour(2), mix(slotColour(3), '#8b6984', 0.35), slotColour(3)];
  }
  const REVERSAL = '#b85c67';
  // A card's Border and Halo (lane M's style colours) as a stroke colour:
  // null keeps the renderer's own drawing (Automatic).
  function styleColour(value) {
    if (!value || value === 'automatic') return null;
    if (value === 'none') return 'none';
    if (value === 'ink') return '#342649';
    if (value === 'white') return '#ffffff';
    const slot = /^c(\d+)$/.exec(value);
    return slot ? slotColour(Math.max(0, Math.min(11, Number(slot[1]) - 1))) : null;
  }
  function colourAt(stops, position) {
    const t = Math.max(0, Math.min(1, Number(position) || 0)), span = stops.length - 1;
    const slot = Math.min(span - 1, Math.floor(t * span));
    return mix(stops[slot], stops[slot + 1], t * span - slot);
  }
  // A continuous scale over a layer's positive values: logarithmic for
  // money and counts that span decades, linear when the range is narrow.
  // With `clamp` (a Filled or Hexbin layer's colour, #53) and values that span
  // more than CLAMP_DECADES orders of magnitude, the log scale is clamped low
  // (the lead's call on lane F2c's note): it starts at the CLAMP_LOW quantile of
  // the places - a real value of the layer - and every place at or below that
  // floor takes the ramp's first colour, so a few near-empty places cannot
  // stretch the scale. The top stays the largest place unless CLAMP_HIGH names
  // a quantile (then every place at or above it takes the last colour).
  // `floor` and `ceiling` are the scale's ends; the legend prints them.
  const CLAMP_DECADES = 3, CLAMP_LOW = 0.1, CLAMP_HIGH = 0.95;
  function scaleFor(values, options) {
    const positive = [...values].filter(value => Number.isFinite(value) && value > 0);
    if (!positive.length) return {min: 0, max: 0, log: false, floor: 0, ceiling: 0, clamped: false, position: () => 0};
    const min = Math.min(...positive), max = Math.max(...positive);
    const log = max / min > 20;
    let floor = min, ceiling = max;
    if (log && options && options.clamp && max / min > Math.pow(10, CLAMP_DECADES)) {
      const sorted = positive.slice().sort((a, b) => a - b);
      const at = share => sorted[Math.max(0, Math.min(sorted.length - 1, Math.floor(share * (sorted.length - 1))))];
      floor = at(CLAMP_LOW);
      if (CLAMP_HIGH != null) ceiling = at(CLAMP_HIGH);
      if (!(ceiling > floor)) { floor = min; ceiling = max; }
    }
    const clamped = floor > min || ceiling < max;
    const position = value => {
      if (!(value > 0) || max <= 0) return 0;
      if (max === min) return 1;
      if (!log) return (value - min) / (max - min);
      if (!clamped) return (Math.log(value) - Math.log(min)) / (Math.log(max) - Math.log(min));
      return Math.max(0, Math.min(1, (Math.log(value) - Math.log(floor)) / (Math.log(ceiling) - Math.log(floor))));
    };
    return {min, max, log, floor, ceiling, clamped, position};
  }

  /* ---------------------------------------------------------- the layer */

  // What one card draws, compiled from its aggregate: every place's value,
  // colour, radius and label, and the legend that explains them. Both
  // renderers draw from this object.
  function compileLayer(card, aggregateResult, options) {
    const settings = options || {};
    const fields = settings.fields || [];
    const mark = markOf(card);
    // A hatch draws on a Filled layer only (layerFault refuses it elsewhere); a side of Spent names itself.
    const pattern = mark === 'filled-map' && card.map && PATTERN_IDS.includes(card.map.pattern) ? card.map.pattern : '';
    const stance = card.source && card.source.kind === 'pac' && STANCES.includes(card.source.stance) ? card.source.stance : '';
    const colourMeasure = measureField(card, 'color', fields) || (mark === 'circle' || mark === 'shape' ? null : {id: 'amount', name: 'Amount', aggregation: 'sum'});
    const sizeMeasure = measureField(card, 'size', fields) || (mark === 'circle' || mark === 'shape' ? {id: 'amount', name: 'Amount', aggregation: 'sum'} : null);
    const labelMeasure = measureField(card, 'label', fields);
    const labelDimension = dimensionName(card, 'label', fields);
    const ramp = rampStops((card.map && card.map.ramp) || (mark === 'density' ? 'heat' : settings.ramp || 'teal'));
    const features = [...(aggregateResult ? aggregateResult.features.values() : [])];
    const valueOf = (feature, measure) => {
      if (!measure) return feature.amount;
      const raw = feature[measure.id];
      return measure.aggregation === 'mean' && feature.places ? raw / feature.places : raw;
    };
    // A Filled (or Hexbin) layer's colours clamp a log scale that spans more than three decades (#53).
    const colourScale = scaleFor(features.map(feature => valueOf(feature, colourMeasure || {id: 'amount'})), {clamp: mark === 'filled-map'});
    const sizeScale = scaleFor(features.map(feature => Math.abs(valueOf(feature, sizeMeasure || {id: 'amount'}))));
    const maxRadius = (card.map && card.map.radius) || DEFAULT_RADIUS[mark] || 10;
    const items = features.map(feature => {
      const value = valueOf(feature, colourMeasure || {id: 'amount'});
      const sized = Math.abs(valueOf(feature, sizeMeasure || {id: 'amount'}));
      const colour = colourMeasure ? (value < 0 ? REVERSAL : value > 0 ? colourAt(ramp, 0.12 + 0.88 * colourScale.position(value)) : '')
        : slotColour(settings.slot == null ? 0 : settings.slot);
      const radius = sizeMeasure ? Math.max(2, maxRadius * Math.sqrt(sizeScale.max > 0 ? sized / sizeScale.max : 0)) : maxRadius;
      const label = labelMeasure ? money(valueOf(feature, labelMeasure), labelMeasure.id) : labelDimension ? (feature.label || feature.id) : '';
      return {id: feature.id, value, colour, radius, label, amount: feature.amount, rows: feature.rows,
        contributors: feature.contributors, support: feature.support, oppose: feature.oppose, point: feature.point || null,
        statefp: feature.statefp || '', zip: feature.zip || ''};
    });
    const legend = {
      title: card.name || layerName(card, fields),
      mark, kind: mark === 'density' ? 'density' : pattern ? 'pattern' : colourMeasure ? 'ramp' : 'swatch',
      measure: colourMeasure ? colourMeasure.name : sizeMeasure ? sizeMeasure.name : '',
      unit: colourMeasure ? colourMeasure.id : 'amount',
      stops: mark === 'density' ? ramp.slice() : ramp.slice(), swatch: colourMeasure ? '' : slotColour(settings.slot == null ? 0 : settings.slot),
      min: colourScale.min, max: colourScale.max, log: colourScale.log, floor: colourScale.floor, ceiling: colourScale.ceiling,
      // One side of Spent counts dollars (the server counts rows per place, not per side), even when it holds none.
      located: !aggregateResult ? '' : stance ? amountLine(aggregateResult.placed.amount, aggregateResult.total.amount) : aggregateResult.line,
      unplaced: aggregateResult ? aggregateResult.unplaced : [],
      count: stance || (aggregateResult && aggregateResult.unit === 'amount') ? 'amount' : 'rows',
      pattern, ink: HATCH_INK,
      note: stance === 'oppose' ? 'Opposition, every candidate' : stance === 'support' ? 'Support, every candidate' : pattern ? 'Each place in this layer' : '',
      sizeMax: sizeScale.max, sizeMeasure: sizeMeasure ? sizeMeasure.name : ''};
    return {id: card.id, name: legend.title, mark, level: card.geography, congress: String(settings.congress || '119'),
      pattern, stance, visible: card.visible !== false,
      selectable: card.selectable !== false, zoomExtent: card.zoomExtent !== false,
      opacity: Math.max(0, Math.min(100, whole(card.opacity) ? card.opacity : 100)) / 100,
      glyph: (card.encodings && card.encodings.shape && card.encodings.shape.glyph) || 'circle',
      border: styleColour(card.border), halo: styleColour(card.halo),
      radius: maxRadius, intensity: (card.map && card.map.intensity) || DEFAULT_INTENSITY,
      items, points: aggregateResult ? aggregateResult.points.map(point => Object.assign({}, point,
        {weight: Math.abs(sizeMeasure || colourMeasure ? valueOfPoint(point, sizeMeasure || colourMeasure) : point.amount)})) : [],
      legend, ramp, source: card.source, sourceKey: sourceKey(card.source), sourceBadge: sourceBadge(card.source)};
  }
  const valueOfPoint = (point, measure) => measure && measure.id in point ? point[measure.id] : point.amount;
  function money(value, id) {
    if (id === 'amount') {
      const number = Number(value) || 0, abs = Math.abs(number);
      const short = abs >= 1e9 ? (abs / 1e9).toFixed(1) + 'B' : abs >= 1e6 ? (abs / 1e6).toFixed(1) + 'M' : abs >= 1e4 ? Math.round(abs / 1e3) + 'K' : Math.round(abs).toLocaleString('en-US');
      return (number < 0 ? '-$' : '$') + short;
    }
    return count(value);
  }
  function fullMoney(value) {
    const number = Number(value) || 0;
    return (number < 0 ? '-$' : '$') + Math.abs(number).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }
  // Legend end labels (#54: "$0 ... $250K+"): the low end, and the high end
  // with a plus when the ramp is capped at the largest place. A log ramp (#53)
  // shows its real ends instead: its lowest and largest places, or where it is
  // clamped, "≤" the floor every smaller place shares (and "≥" a ceiling).
  function legendRange(legend) {
    if (!(legend.max > 0)) return ['No positive amount', ''];
    const format = value => legend.unit === 'amount' ? money(value, 'amount') : count(value);
    if (!legend.log) return [format(0), format(legend.max) + '+'];
    const floor = legend.floor > 0 ? legend.floor : legend.min, ceiling = legend.ceiling > 0 ? legend.ceiling : legend.max;
    return [(floor > legend.min ? '≤' : '') + format(floor), (ceiling < legend.max ? '≥' : '') + format(ceiling)];
  }
  // A log ramp's words for its legend card's tooltip: the scale, its clamp, the layer's real range.
  function legendScaleNote(legend) {
    if (!legend || legend.kind !== 'ramp' || !legend.log || !(legend.max > 0)) return '';
    const format = value => legend.unit === 'amount' ? money(value, 'amount') : count(value);
    const floor = legend.floor > 0 ? legend.floor : legend.min, ceiling = legend.ceiling > 0 ? legend.ceiling : legend.max;
    const clamp = [floor > legend.min ? 'places at or below ' + format(floor) + ' share the first colour' : '',
      ceiling < legend.max ? 'places at or above ' + format(ceiling) + ' share the last' : ''].filter(Boolean).join('; ');
    return ' Colours on a log scale' + (clamp ? ' (' + clamp + ')' : '') + '; the places run from '
      + format(legend.min) + ' to ' + format(legend.max) + '.';
  }

  /* -------------------------------------------------------------- density */

  // A kernel density on a grid of `cell`-px cells (Tableau's Density mark):
  // each point spreads its weight over `radius` px with a quartic kernel. The
  // grid is recomputed whenever the view moves, so the pattern follows zoom.
  function densityGrid(points, width, height, radius, cell) {
    const step = Math.max(1, cell || 2);
    const columns = Math.max(1, Math.ceil(width / step)), rows = Math.max(1, Math.ceil(height / step));
    const grid = new Float32Array(columns * rows);
    const reach = Math.max(1, radius / step), reach2 = reach * reach, span = Math.ceil(reach);
    for (const point of points || []) {
      const weight = Number(point.weight) || 0;
      if (!(weight > 0)) continue;
      const cx = point.x / step, cy = point.y / step;
      const x0 = Math.max(0, Math.floor(cx - span)), x1 = Math.min(columns - 1, Math.ceil(cx + span));
      const y0 = Math.max(0, Math.floor(cy - span)), y1 = Math.min(rows - 1, Math.ceil(cy + span));
      for (let y = y0; y <= y1; y += 1) {
        const dy = y + 0.5 - cy;
        for (let x = x0; x <= x1; x += 1) {
          const dx = x + 0.5 - cx, d2 = (dx * dx + dy * dy) / reach2;
          if (d2 >= 1) continue;
          const k = 1 - d2;
          grid[y * columns + x] += weight * k * k;
        }
      }
    }
    let max = 0;
    for (let i = 0; i < grid.length; i += 1) if (grid[i] > max) max = grid[i];
    return {grid, columns, rows, cell: step, max};
  }
  // RGBA for a density grid: hotspots glow (#47, #48). The grid - the
  // measure-weighted kernel density - is left as it is; only its drawing
  // follows a gamma transfer against the view's peak (a monotone curve, so the
  // order of places never changes): a metro a thousandth of the peak still
  // glows, overlapping hotspots build up to the ramp's gold core, and every
  // hotspot keeps a gradient - a gold core in a purple halo that fades out into
  // the plain base - rather than a flat disc. Intensity (1-100) is the curve's
  // lift: how soon a place reaches the core (kept apart from the layer's
  // opacity, as Tableau keeps it). Density below the cut-off stays transparent,
  // so the whole country is never tinted (#48's Don't copy).
  // The curve (#47, #48; the lead's call on lane F2c's option): gamma =
  // 1 - 0.95 x I^0.75, I the intensity as a fraction. At the 70% default gamma
  // is 0.27, so a place at a thousandth of the peak draws at about 0.19 opacity
  // (0.05 under the earlier 1 - 0.9 x I) and one at a hundredth at about 0.40.
  // The same curve places the street map's heatmap stops (densityColourStops).
  const DENSITY_CUT = 0.06;
  const densityGamma = intensity => 1 - 0.95 * Math.pow(Math.max(1, Math.min(100, intensity || DEFAULT_INTENSITY)) / 100, 0.75);
  const densityAlpha = t => Math.min(1, Math.pow((t - DENSITY_CUT) / (1 - DENSITY_CUT), 0.8) * 1.25);
  function densityPixels(density, stops, intensity, opacity) {
    const {grid, max} = density;
    const out = new Uint8ClampedArray(grid.length * 4);
    if (!(max > 0)) return out;
    const gamma = densityGamma(intensity);
    const parsed = stops.map(hex => [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16)));
    const alphaScale = Math.max(0, Math.min(1, opacity == null ? 1 : opacity));
    // The halo starts at the ramp's purple (its second stop), as the boards draw it.
    const span = parsed.length - 1, start = parsed.length > 2 ? 1 / span : 0;
    for (let i = 0; i < grid.length; i += 1) {
      const v = grid[i];
      if (!(v > 0)) continue;
      const t = Math.pow(v / max, gamma);
      if (t < DENSITY_CUT) continue;
      const position = Math.min(1, start + (1 - start) * t);
      const slot = Math.min(span - 1, Math.floor(position * span)), f = position * span - slot;
      const a = parsed[slot], b = parsed[slot + 1];
      out[i * 4] = a[0] + (b[0] - a[0]) * f;
      out[i * 4 + 1] = a[1] + (b[1] - a[1]) * f;
      out[i * 4 + 2] = a[2] + (b[2] - a[2]) * f;
      out[i * 4 + 3] = 255 * alphaScale * densityAlpha(t);
    }
    return out;
  }
  // The same curve for the street map's heatmap, whose density MapLibre accumulates
  // itself: the colour stops of 'heatmap-color', [density, colour, ...], placed where
  // the gamma puts them, so its hotspots glow as the boundary map's do. MapLibre
  // refuses an 'interpolate' whose stops do not rise strictly, and a steep curve
  // (intensity above about 86%) puts the lowest stops under 1e-4 together, so each
  // stop sits at least 1e-4 above the one before it.
  const STOP_STEP = 1e-4;
  function densityColourStops(stops, intensity) {
    const gamma = densityGamma(intensity), span = stops.length - 1, start = stops.length > 2 ? 1 / span : 0;
    const out = [0, 'rgba(0,0,0,0)'];
    let last = 0;
    for (let k = 0; k <= 5; k += 1) {
      const t = DENSITY_CUT + (1 - DENSITY_CUT) * k / 5;
      const hex = colourAt(stops, Math.min(1, start + (1 - start) * t));
      const rgb = [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16));
      last = Math.max(last + STOP_STEP, Math.pow(t, 1 / gamma));
      out.push(last, 'rgba(' + rgb.join(',') + ',' + densityAlpha(t).toFixed(3) + ')');
    }
    return out;
  }

  /* -------------------------------------------------- the layer control */

  // The viewer's list (#49, #55): every card, top first, with its eye and its
  // pointer. `state` holds view-only toggles (D-039: no storage key).
  function controlRows(layers, state) {
    const view = state || {};
    return (layers || []).map(layer => ({id: layer.id, name: layer.name, mark: markName(layer.mark).toLowerCase(),
      visible: view.hidden ? !view.hidden.has(layer.id) && layer.visible : layer.visible,
      selectable: view.locked ? !view.locked.has(layer.id) && layer.selectable : layer.selectable}));
  }
  function statusLine(layers, marks) {
    const all = layers || [];
    const sources = new Set(all.map(layer => layer.sourceKey).filter(Boolean)).size;
    const hidden = all.filter(layer => !layer.visible).length;
    const parts = [all.length + ' marks layer' + (all.length === 1 ? '' : 's'), sources + ' source' + (sources === 1 ? '' : 's')];
    if (hidden) parts.push(hidden + ' hidden');
    if (marks != null) parts.push(count(marks) + ' marks');
    return parts.join(' · ');
  }
  function subtitleOf(layers) {
    const sources = new Set((layers || []).map(layer => layer.sourceKey).filter(Boolean)).size;
    const words = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
    return (words[sources] || String(sources)) + ' source' + (sources === 1 ? '' : 's') + ' · layers drawn top to bottom as listed in Marks';
  }

  /* ------------------------------------------------------------- presets */

  // PAC evidence's fixed layers as cards (#48, #55): what its map draws, one
  // card each, so "Open in Chart Studio" hands over exactly these. `layer` is
  // the evidence map's LAYER (region, state, district, sld, zip). With
  // Chart Studio's `fields` a pill names its column; without them it names
  // its field id and Chart Studio resolves it on arrival (map-studio.js).
  const EVIDENCE_LEVEL = {region: 'state', state: 'state', district: 'cd', sld: 'sldu', zip: 'zcta'};
  // The evidence map's own words for its places (board #55: "Spending by district \u00b7 filled").
  const EVIDENCE_WORD = {state: 'state', cd: 'district', sldu: 'state district', zcta: 'ZIP code'};
  // The LAYER choices on which the evidence map draws its Opposition (a hatch over the fill).
  const OPPOSITION_LAYERS = ['district', 'state', 'region'];
  // Each preset with the key the evidence map's view state (eye, pointer) uses for it:
  // circles, heat and fill, and on Spent the Opposition - the SAME card the evidence
  // map compiles and draws its hatch from (map-evidence.js), so what it shows and
  // what "Open in Chart Studio" hands over cannot differ. `options.locked` lists the
  // presets the reader cannot select; without it the Opposition starts unselectable
  // (its hatch lies over the fill, whose places the evidence map selects).
  function evidenceLayers(pane, direction, cycle, fields, options) {
    const settings = options || {};
    const hidden = settings.hidden instanceof Set ? settings.hidden : new Set();
    const locked = settings.locked instanceof Set ? settings.locked : new Set(['hatch']);
    const heat = settings.heat || null;
    const find = id => (fields || []).find(field => field.id === id);
    const pill = id => { if (!fields) return {field: id}; const field = find(id); return field ? {column: field.index, aggregation: 'sum'} : null; };
    const source = {kind: 'pac', source: pane.source === 'csv' ? 'csv' : 'snapshot', id: pane.id, committee: pane.committee,
      cycle: String(cycle || ''), direction: direction === 'raised' ? 'raised' : 'spent'};
    if (pane.name) source.name = String(pane.name).slice(0, 120);
    if (pane.industry) source.industry = String(pane.industry).slice(0, 120);
    const level = EVIDENCE_LEVEL[settings.layer] || (direction === 'raised' ? 'state' : 'cd');
    const shown = id => !hidden.has(id), pickable = id => !locked.has(id);
    if (direction === 'raised') {
      const out = [
        {key: 'circles', card: newLayerCard({id: 'layer-1', name: 'Donor ZIPs \u00b7 circles', type: 'circle', geography: 'zcta', source,
          encodings: {color: null, size: pill('contributors')}, map: {radius: 11}, visible: shown('circles'), selectable: pickable('circles')})},
        {key: 'heat', card: newLayerCard({id: 'layer-2', name: 'Heat: donor amount', type: 'density', geography: 'point', source,
          encodings: {color: pill('amount')}, visible: !!(heat && heat.on) && shown('heat'), selectable: false,
          map: {radius: (heat && heat.radius) || 18, intensity: (heat && heat.intensity) || DEFAULT_INTENSITY, ramp: 'heat'}})}];
      if (level !== 'zcta') out.push({key: 'fill', card: newLayerCard({id: 'layer-3', name: 'Receipts by ' + (EVIDENCE_WORD[level] || levelWord(level)) + ' \u00b7 filled',
        type: 'filled-map', geography: level, source, encodings: {color: pill('amount')}, opacity: 85, visible: shown('fill'), selectable: pickable('fill')})});
      return out;
    }
    const spendLevel = level === 'zcta' || level === 'sldu' ? 'state' : level;
    const out = [];
    // #55's Opposition: the spending that opposed a candidate, summed over every candidate at each place
    // (the server's per-place oppose sum; support is never subtracted), hatched over the fill.
    if (!settings.layer || OPPOSITION_LAYERS.includes(settings.layer)) {
      out.push({key: 'hatch', card: newLayerCard({id: 'layer-1', name: 'Opposition \u00b7 hatch', type: 'filled-map', geography: spendLevel,
        source: Object.assign({}, source, {stance: 'oppose'}), encodings: {color: null}, map: {pattern: 'hatch'},
        visible: shown('hatch'), selectable: pickable('hatch')})});
    }
    out.push({key: 'fill', card: newLayerCard({id: 'layer-' + (out.length + 1), name: 'Spending by ' + EVIDENCE_WORD[spendLevel] + ' \u00b7 filled', type: 'filled-map',
      geography: spendLevel, source, encodings: {color: pill('amount')}, opacity: 90, visible: shown('fill'), selectable: pickable('fill')})});
    return out;
  }
  const evidencePresets = (pane, direction, cycle, fields, options) => evidenceLayers(pane, direction, cycle, fields, options).map(entry => entry.card);
  const api = {
    LEVELS, LEVEL_IDS, levelLabel, HEX_LEVELS, PER_STATE_LEVELS, isHex, MARKS, MARK_IDS, markName, markOf,
    AGGREGATE_BY, HEX_SIZES, aggregateOf, PLACEABLE, FAMILY, canPlace, BACKGROUNDS, BASE_LAYERS, BASE_IDS, DEFAULT_BASE,
    DATA_BY, RAMP_IDS, RAMP_LABELS, DEFAULT_OPTIONS, OPTION_LABELS, DEFAULT_RADIUS, DEFAULT_INTENSITY, MAX_CARDS,
    STANCES, PATTERN_IDS, HATCH_INK,
    isLayerCard, mapLayersOn, sourceFault, cardMapFault, layerFault, mapSettingsFault, settingsOf, writeSettings,
    newLayerCard, nextLayerId, sourceKey, sourceLabel, paneLabel, sourceBadge, cycleLabel, layerName,
    markRows, encodingSupport, markSummary, aggregateLabel, reasonShort, SIZE_MEANS, measureField,
    FRAME, regionOf, projectFrame, albers5070, hexAxial, hexId, createLookup, primary,
    rowsFromContributions, rowsFromEvidence, aggregate, locatedLine, amountLine,
    rampStops, colourAt, scaleFor, mix, slotColour, REVERSAL, styleColour, compileLayer, money, fullMoney, legendRange, legendScaleNote,
    CLAMP_DECADES, CLAMP_LOW, CLAMP_HIGH,
    densityGrid, densityPixels, densityColourStops, densityGamma, DENSITY_CUT, controlRows, statusLine, subtitleOf, evidenceLayers, evidencePresets, EVIDENCE_LEVEL,
    EVIDENCE_WORD, OPPOSITION_LAYERS};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MentatMapLayers = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
