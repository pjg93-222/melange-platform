/* Chart Studio's maps whose marks cards are its layers (lane P, boards #47, #49,
   #53, #54, and #55's "Open in Chart Studio").

   Paul, 2026-09-25: "I should be able to toggle things ... select the layers or
   what is shown on the maps via the marks, it all weaves together." In a map
   sheet the Inspector's Marks tab lists the layers (the marks cards, top card
   drawn on top), each with its eye, its mark type, its source badge and its
   caret menu (Rename, Disable selection, Add to zoom extent, Move up, Move down,
   Remove); the open layer shows lane M's encoding rows. The left pane switches
   between Data and Map layers (#54's backdrop: style, washout, base layers,
   data layer, map options). The shelf row becomes Geography and Aggregate by
   (#49, #53). A layer is added by dropping a geographic field on the map's
   "Add a marks layer" target, by a field's "Add to new layer", or with
   "+ Add layer" and a source.

   chart-studio.js hands this module its state through init(hooks) and calls
   it from renderView, renderMarkCards, renderMarkRows and renderShelves; the
   sheet is still edited through the studio's own commit(), so every gesture is
   one undo step and saves with the view. */
(function (root) {
  'use strict';
  if (typeof document === 'undefined') return;
  const L = root.MentatMapLayers, E = root.MentatMapEngine;
  const core = () => root.MentatStudioCore;
  const NS = 'http://www.w3.org/2000/svg';
  let hooks = null;
  const viewStates = new Map();      // sheet id -> {hidden, locked}: view state only (D-039)
  const handles = new Map();         // canvas host -> engine handle
  const evidenceBodies = new Map();  // /api/evidence-geo request -> Promise<payload>
  const claims = new Map();          // card id -> query claim (chart-studio queryCached)
  let lookup = null, sidePane = 'data', pendingHandoff = null, snapshotList = null;
  let dragField = null;

  const $ = id => document.getElementById('studio-' + id);
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const svg = (paths, viewBox) => {
    const node = document.createElementNS(NS, 'svg');
    node.setAttribute('viewBox', viewBox || '0 0 16 16'); node.setAttribute('aria-hidden', 'true'); node.setAttribute('focusable', 'false');
    for (const [tag, attributes] of paths) {
      const child = document.createElementNS(NS, tag);
      for (const [key, value] of Object.entries(attributes)) child.setAttribute(key, String(value));
      node.append(child);
    }
    return node;
  };
  const ICON = {
    eye: [['path', {d: 'M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z'}], ['circle', {cx: 8, cy: 8, r: 2}]],
    eyeOff: [['path', {d: 'M2.5 2.5l11 11M6.2 4c.6-.3 1.2-.5 1.8-.5 4.1 0 6.5 4.5 6.5 4.5s-.6 1.2-1.8 2.4M4 5.6C2.4 6.8 1.5 8 1.5 8s2.4 4.5 6.5 4.5c.9 0 1.7-.2 2.4-.5'}]],
    caret: [['path', {d: 'M4.5 6.5 8 10l3.5-3.5'}]],
    grip: [['circle', {cx: 6, cy: 4, r: 1}], ['circle', {cx: 10, cy: 4, r: 1}], ['circle', {cx: 6, cy: 8, r: 1}], ['circle', {cx: 10, cy: 8, r: 1}], ['circle', {cx: 6, cy: 12, r: 1}], ['circle', {cx: 10, cy: 12, r: 1}]],
    globe: [['circle', {cx: 8, cy: 8, r: 5.8}], ['path', {d: 'M2.2 8h11.6M8 2.2c-2.5 2.7-2.5 8.9 0 11.6M8 2.2c2.5 2.7 2.5 8.9 0 11.6'}]],
    plus: [['path', {d: 'M8 3v10M3 8h10'}]],
    'filled-map': [['path', {d: 'M3 4.5 7 2.8l6 2.2v7.5l-4 1.7-6-2.2z'}]],
    circle: [['circle', {cx: 8, cy: 8, r: 5}]],
    shape: [['path', {d: 'M8 2.5 13.5 8 8 13.5 2.5 8z'}]],
    density: [['circle', {cx: 8, cy: 8, r: 1.6}], ['circle', {cx: 8, cy: 8, r: 4}], ['circle', {cx: 8, cy: 8, r: 6.2, 'stroke-dasharray': '1.6 1.6'}]],
    text: [['path', {d: 'M3 3.5h10M8 3.5v10'}]],
    line: [['path', {d: 'M2.5 12.5 6.5 5l3.5 5 3.5-6.5'}]],
    hexbin: [['path', {d: 'M8 2.2 13 5.1v5.8L8 13.8 3 10.9V5.1z'}]],
    info: [['circle', {cx: 8, cy: 8, r: 6}], ['path', {d: 'M8 7.2v4M8 4.9v.1'}]],
    automatic: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M2.5 8h11'}]]};
  const icon = name => svg(ICON[name] || ICON.automatic);

  /* ------------------------------------------------------------- the sheet */

  const handlesSheet = sheet => !!(sheet && core() && core().mapLayersOn && core().mapLayersOn(sheet));
  const cardsOf = sheet => core().readCards(sheet);
  function viewStateFor(sheet) {
    const key = (sheet && sheet.id) || 'sheet';
    if (!viewStates.has(key)) viewStates.set(key, {hidden: new Set(), locked: new Set()});
    return viewStates.get(key);
  }
  function openIndex() {
    const cards = cardsOf(hooks.sheet());
    const at = Number(hooks.expanded());
    return Number.isInteger(at) && at >= 0 && at < cards.length ? at : 0;
  }
  const fields = () => hooks.columns();
  const fieldById = id => fields().find(field => field.id === id);
  const pillFor = id => { const field = fieldById(id); return field ? {column: field.index, aggregation: 'sum'} : null; };
  const GEO_FIELDS = {state: 'state', recipient_state: 'state', represented_state: 'state', zip5: 'zcta'};
  function geographyOfField(field) {
    if (!field) return '';
    if (GEO_FIELDS[field.id]) return GEO_FIELDS[field.id];
    const role = String(field.geographic_role || '').toLowerCase();
    if (/zip|postal/.test(role) || /zip/i.test(field.name || '')) return 'zcta';
    if (/state/.test(role) || /^state$/i.test(field.name || '')) return 'state';
    if (/district/.test(role)) return 'cd';
    if (/county/.test(role)) return 'county';
    return '';
  }
  // The current dataset as a layer source (an industry, D-030).
  function currentSource() {
    const dataset = hooks.dataset() || {};
    const industry = dataset.industry || dataset.id || '';
    return industry ? {kind: 'industry', industry, basis: hooks.basis(), label: dataset.label || industry} : null;
  }
  function commitCards(change, message) {
    hooks.commit(() => {
      const sheet = hooks.sheet();
      const cards = cardsOf(sheet);
      const result = change(cards, sheet);
      const next = Array.isArray(result) ? result : cards;
      if (!next.length) { delete sheet.marksCards; sheet.marks = {type: 'bar', color: null, size: null, label: null, detail: [], tooltip: []}; return; }
      delete sheet.marksCards;
      core().writeCards(sheet, next);
    });
    if (message) hooks.status(message);
  }

  /* ------------------------------------------------------------ the data */

  function getLookup() { if (!lookup) lookup = L.createLookup(E.fetchJson); return lookup; }
  function evidenceBody(source) {
    const params = new URLSearchParams({source: source.source, id: source.id, committee: source.committee, cycle: source.cycle, direction: source.direction});
    const key = params.toString();
    if (!evidenceBodies.has(key)) {
      const request = fetch('/api/evidence-geo?' + key).then(async response => {
        const body = await response.json();
        if (!response.ok || body.error) throw new Error(body.error || 'The PAC map could not be read.');
        return body;
      });
      evidenceBodies.set(key, request);
      request.catch(() => evidenceBodies.delete(key));
      while (evidenceBodies.size > 16) evidenceBodies.delete(evidenceBodies.keys().next().value);
    }
    return evidenceBodies.get(key);
  }
  // A verified query for one industry's ZIP groups, with the sheet's own
  // filters (fields share their ids across industries), through the studio's
  // one pending handler and 30-second cache.
  function industryQuery(source, sheet) {
    const datasets = hooks.datasets();
    const chosen = datasets.find(entry => entry.industry === source.industry || entry.id === source.industry || entry.label === source.industry);
    if (!chosen) throw new Error(source.industry + ' has no verified source on this console.');
    if (chosen.available === false) throw new Error(source.industry + ': ' + (chosen.message || 'unavailable'));
    const notes = [], filters = [], aggregate = [];
    const contributions = root.MentatStudioContributions;
    for (const filter of sheet.filters || []) {
      const field = fields().find(item => item.index === Number(filter.column));
      if (!field || !contributions) continue;
      const target = filter.stage === 'aggregate' || (field.role === 'measure' && field.id !== 'amount') ? aggregate : filters;
      try { for (const clause of contributions.clauses(filter, field, notes)) target.push(clause); } catch (error) { notes.push(field.name + ' filter is left out: ' + error.message); }
    }
    return {query: {dataset: chosen.id, basis: source.basis || 'receipt', dimensions: ['zip5', 'state'], measures: ['amount', 'records', 'contributors'],
      filters, aggregate_filters: aggregate, sort: [{field: 'amount', direction: 'desc'}], limit: 100000, offset: 0, industry: chosen.industry || chosen.id}, notes};
  }
  async function layerRows(card, sheet) {
    const source = card.source || {};
    if (source.kind === 'industry') {
      const {query, notes} = industryQuery(source, sheet);
      if (!claims.has(card.id)) claims.set(card.id, {waiting: null});
      const result = await hooks.query(query, claims.get(card.id));
      if (result && result.has_more) notes.push(L.sourceLabel(source) + ': the first ' + (result.rows || []).length.toLocaleString() + ' ZIP groups are mapped.');
      return {rows: L.rowsFromContributions(result), congress: String(((sheet.map || {}).congress) || '119'), notes,
        total: result && result.totals ? result.totals : null};
    }
    if (source.kind === 'pac') {
      const body = await evidenceBody(source);
      const notes = body.reason ? [body.reason] : [];
      // One side of Spent: the server's per-place side sums; what it could not place is not split by side.
      if (source.stance) notes.push(L.layerName(Object.assign({}, card, {name: ''}), fields()) + ': ' + (source.stance === 'oppose' ? 'opposition' : 'support')
        + ' is each place’s sum over every candidate, the other side kept apart; money the evidence could not place is not split by side, so it is not in this layer.');
      return {rows: L.rowsFromEvidence(body, card.geography, {stance: source.stance}), congress: String(body.vintage || '119'), notes};
    }
    throw new Error('This layer has no source.');
  }
  async function compileSheet(sheet) {
    const cards = cardsOf(sheet), notes = [], layers = [];
    const settings = L.settingsOf(sheet);
    let slot = 0;
    for (const card of cards) {
      try {
        const data = await layerRows(card, sheet);
        const table = getLookup();
        await table.ready(data.rows.map(row => row.zip).filter(Boolean));
        const aggregated = L.aggregate(data.rows, card.geography, table, {congress: data.congress});
        layers.push(L.compileLayer(card, aggregated, {fields: fields(), congress: data.congress, slot: slot++ % 4}));
        notes.push(...data.notes);
      } catch (error) {
        const failed = L.compileLayer(card, null, {fields: fields()});
        failed.error = error.message;
        layers.push(failed);
        notes.push((card.name || L.layerName(card, fields())) + ': ' + error.message);
      }
    }
    let dataLayer = null;
    if (settings.data && settings.data.measure) {
      const source = currentSource();
      if (source) {
        const card = L.newLayerCard({id: 'data-layer', name: 'Data layer', source, geography: settings.data.by, type: 'filled-map',
          encodings: {color: settings.data.measure}, map: {ramp: settings.data.using}});
        try {
          const data = await layerRows(card, sheet);
          await getLookup().ready(data.rows.map(row => row.zip).filter(Boolean));
          dataLayer = L.compileLayer(card, L.aggregate(data.rows, card.geography, getLookup(), {congress: data.congress}), {fields: fields(), congress: data.congress});
        } catch (error) { notes.push('Data layer: ' + error.message); }
      }
    }
    return {layers, dataLayer, settings, notes};
  }

  /* ------------------------------------------------------------- the view */

  function titleOf(sheet, layers) {
    if (sheet.name && !/^Sheet \d+$/.test(sheet.name)) return sheet.name;
    const names = layers.filter(layer => layer.visible).map(layer => layer.name);
    if (!names.length) return 'Map';
    if (names.length === 1) return names[0];
    return names[0] + ' and ' + (names.length - 1) + ' more layer' + (names.length === 2 ? '' : 's');
  }
  async function renderView(view) {
    const node = view.el, sheet = view.state;
    const canvas = node('canvas');
    // A Board tile that went away took its canvas with it; its map (a MapLibre one holds a
    // WebGL context) is released here rather than left running behind the page.
    for (const [host, handle] of handles) {
      if (host.isConnected) continue;
      try { if (handle && handle.destroy) handle.destroy(); } catch (error) { /* already gone */ }
      handles.delete(host);
    }
    // The ticket lives on the canvas: a surface object is made afresh for each
    // render, so only the node can tell a stale answer from the latest.
    const surface = canvas;
    const ticket = (surface.__mapTicket = (surface.__mapTicket || 0) + 1);
    surface.hidden = false;
    if (node('table')) node('table').hidden = true;
    // A render of the sheet shown before this map may still be in flight; it may write
    // that source's totals and scope strip after this map starts. While the builder shows
    // a map, map.css hides both (the view carries is-map-layers); nothing in flight is touched.
    if (view.primary !== false) { const root = document.getElementById('view-chart-studio'); if (root) root.classList.add('is-map-layers'); }
    for (const id of ['reconcile', 'result-controls']) if (node(id)) node(id).hidden = true;
    if (node('verified-summary')) node('verified-summary').replaceChildren();
    surface.className = (view.canvasClass || '') + 'studio-canvas fit-' + (sheet.fit || 'standard') + ' is-map-layers';
    node('status').textContent = 'Reading the layers…';
    if (!surface.querySelector('.mentat-map')) node('note').textContent = 'Reading each layer’s source…';
    let compiled;
    try { compiled = await compileSheet(sheet); } catch (error) { node('note').textContent = error.message; return; }
    if (surface.__mapTicket !== ticket) return;
    const title = titleOf(sheet, compiled.layers);
    node('title').textContent = title;
    // A map's title stays on one line (map.css); the whole of it is in the heading's tooltip
    // (sync() takes the tooltip away again when the sheet is not a map).
    node('title').title = title;
    node('title').dataset.mapTitle = title;
    // The canvas is this map's now: a render of the previous sheet that went stale never clears
    // the busy mark it set (which fades the canvas), so the map clears it once its layers are read.
    canvas.removeAttribute('aria-busy');
    node('subtitle').textContent = L.subtitleOf(compiled.layers);
    view.title = title;
    view.scope = compiled.layers.map(layer => layer.name + ' (' + L.sourceLabel(layer.source) + ', ' + L.levelLabel(layer.level) + '; ' + (layer.legend.located || 'no rows') + ')').join('; ');
    // The view toolbar is a column on the map's left, and the Layer control opens beside it (#49, #55).
    const model = {layers: compiled.layers, dataLayer: compiled.dataLayer, settings: compiled.settings, viewState: viewStateFor(sheet),
      toolbar: 'left', controlOpen: false,   // Tableau's Layer control opens from the view toolbar; it starts closed so it never covers the map
      onBoundaries: () => setSettings({background: 'boundaries'}, 'Showing the Boundaries map, which draws the whole country.')};
    const callbacks = {
      onSelect: (layerId, key, item) => { hooks.status((item ? L.fullMoney(item.amount) + ' · ' : '') + key + ' · ' + ((compiled.layers.find(layer => layer.id === layerId) || {}).name || '')); },
      onStatus: text => { node('status').textContent = text; },
      onAuthorHidden: layerId => hooks.status('This layer is hidden on its marks card; its eye in Marks shows it.')};
    const handle = E.render(surface, model, callbacks);
    handles.set(surface, handle);
    if (view.primary !== false) surface.__mapHandle = handle;
    node('note').textContent = [...new Set(compiled.notes)].join(' ') || 'Each layer reads its own source; rows a level cannot place are listed in its legend, not placed.';
  }

  /* ----------------------------------------------------- the Marks tab */

  const MARK_OPTIONS = () => L.MARKS.map(([id, label]) => [id, label]);
  function renderLayerCards() {
    const host = $('mark-cards'), body = $('mark-card-body'), sync = $('mark-sync');
    if (!host || !body) return;
    const sheet = hooks.sheet(), cards = cardsOf(sheet), open = openIndex();
    if (sync) sync.hidden = true;
    host.hidden = false;
    host.classList.add('studio-layer-list');
    host.setAttribute('aria-label', 'Map layers, drawn top to bottom');
    const items = cards.map((card, index) => layerCard(card, index, index === open, cards.length, body));
    const add = el('button', 'studio-layer-add');
    add.type = 'button';
    add.id = 'studio-layer-add';
    add.append(icon('plus'), document.createTextNode('Add layer'));
    add.addEventListener('click', () => sourceMenu(add, source => addLayer({source}), 'Add a layer from'));
    const from = el('label', 'studio-layer-from');
    from.append(el('span', '', 'From:'));
    const select = el('select');
    select.id = 'studio-layer-source';
    select.setAttribute('aria-label', 'Source of the open layer');
    fillSources(select, cards[open] && cards[open].source);
    select.addEventListener('change', () => {
      const chosen = JSON.parse(select.value || 'null');
      if (!chosen) return;
      commitCards(list => { if (list[open]) list[open].source = chosen; }, 'The layer now reads ' + L.sourceLabel(chosen) + '.');
    });
    from.append(select);
    const foot = el('p', 'studio-layer-foot', 'Drawn top to bottom as listed. Drop a geographic field on the map to add a layer.');
    host.replaceChildren(...items, add, from, foot);
  }
  function layerCard(card, index, open, total, body) {
    const item = el('div', 'studio-layer-card' + (open ? ' is-open' : ''));
    item.setAttribute('role', 'listitem');
    item.dataset.card = String(index);
    item.dataset.layer = card.id;
    const head = el('div', 'studio-layer-head');
    const grip = el('span', 'studio-layer-grip'); grip.append(icon('grip')); grip.title = 'Drag to change the draw order';
    grip.draggable = true;
    const eye = el('button', 'studio-layer-eye');
    eye.type = 'button';
    eye.setAttribute('aria-pressed', String(card.visible !== false));
    eye.setAttribute('aria-label', (card.visible !== false ? 'Hide ' : 'Show ') + displayName(card));
    eye.title = card.visible !== false ? 'Control layer visibility: shown' : 'Control layer visibility: hidden';
    eye.append(icon(card.visible !== false ? 'eye' : 'eyeOff'));
    eye.addEventListener('click', () => commitCards(list => { list[index].visible = list[index].visible === false; }));
    // A Filled layer at a hexagon level reads Hexbin (#53), by its icon and its word.
    const summary = L.markSummary(card);
    const mark = el('span', 'studio-layer-mark'); mark.append(icon(summary === 'Hexbin' ? 'hexbin' : L.markOf(card))); mark.title = summary;
    const name = el('button', 'studio-layer-name', displayName(card));
    name.type = 'button';
    name.title = displayName(card) + ' · ' + summary + ' · ' + L.sourceLabel(card.source);
    name.setAttribute('aria-expanded', String(open));
    name.addEventListener('click', () => { if (!open) { hooks.setExpanded(index); hooks.renderAll(); } });
    name.addEventListener('dblclick', () => rename(item, index));
    const badge = el('span', 'studio-layer-badge', L.sourceBadge(card.source));
    badge.title = L.sourceLabel(card.source);
    const caret = el('button', 'studio-layer-caret');
    caret.type = 'button';
    caret.setAttribute('aria-haspopup', 'menu');
    caret.setAttribute('aria-label', 'Layer menu for ' + displayName(card));
    caret.append(icon('caret'));
    caret.addEventListener('click', event => { event.stopPropagation(); layerMenu(caret, card, index, total); });
    head.append(grip, eye, mark, name, badge, caret);
    item.append(head);
    wireDrag(item, grip, index);
    if (open) {
      const inner = el('div', 'studio-layer-body');
      const typeRow = el('label', 'studio-layer-type');
      typeRow.append(el('span', '', 'Mark type'));
      const select = el('select');
      select.id = 'studio-layer-type';
      // On a hexagon level the Filled mark is the hexbin, and the menu says so.
      const hex = L.isHex(card.geography);
      for (const [id, label] of MARK_OPTIONS()) select.append(new Option(hex && id === 'filled-map' ? 'Hexbin' : label, id));
      select.value = L.MARK_IDS.includes(card.type) ? card.type : 'automatic';
      select.addEventListener('change', () => commitCards(list => { list[index].type = select.value; keepPatternValid(list[index]); }));
      typeRow.append(select);
      inner.append(typeRow, el('div', 'studio-layer-subhead', 'Encodings'), body);
      inner.append(slidersFor(card, index));
      const choices = choicesFor(card, index);
      if (choices) inner.append(choices);
      item.append(inner);
    }
    return item;
  }
  // A hatch belongs to a Filled layer (layerFault); a mark or level change that leaves
  // Filled drops it, so the sheet still saves, and says so.
  function keepPatternValid(card) {
    if (!card || !card.map || !card.map.pattern || L.markOf(card) === 'filled-map') return;
    delete card.map.pattern;
    if (!Object.keys(card.map).length) delete card.map;
    hooks.status('The hatch fills shapes only, so this layer is drawn in colour now.');
  }
  // The open layer's Pattern (Filled layers) and, for a PAC network's Spent, its Side:
  // all spending, or one side of it - support or opposition, never the two netted.
  function choicesFor(card, index) {
    const rows = [];
    const row = (label, id, options, value, apply) => {
      const wrap = el('label', 'studio-layer-type');
      wrap.append(el('span', '', label));
      const select = el('select');
      select.id = id;
      for (const [key, text] of options) select.append(new Option(text, key));
      select.value = value;
      select.addEventListener('change', () => apply(select.value));
      wrap.append(select);
      rows.push(wrap);
    };
    if (L.markOf(card) === 'filled-map') {
      row('Pattern', 'studio-layer-pattern', [['', 'Solid colour'], ['hatch', 'Hatch']], (card.map && card.map.pattern) || '', value => commitCards(list => {
        const target = list[index];
        const map = Object.assign({}, target.map);
        if (value) map.pattern = value; else delete map.pattern;
        if (Object.keys(map).length) target.map = map; else delete target.map;
      }));
    }
    const source = card.source || {};
    if (source.kind === 'pac' && source.direction === 'spent') {
      row('Side', 'studio-layer-side', [['', 'All spending'], ['support', 'Support only'], ['oppose', 'Opposition only']], source.stance || '', value => commitCards(list => {
        const next = Object.assign({}, list[index].source);
        if (value) next.stance = value; else delete next.stance;
        list[index].source = next;
      }, value ? 'The layer now draws ' + (value === 'oppose' ? 'opposition' : 'support') + ' only: each place’s sum over every candidate, the other side kept apart.' : 'The layer now draws all spending.'));
    }
    if (!rows.length) return null;
    const box = el('div', 'studio-layer-choices');
    box.append(...rows);
    return box;
  }
  const displayName = card => L.layerName(card, fields());
  function slidersFor(card, index) {
    const box = el('div', 'studio-layer-sliders');
    const row = (label, id, min, max, step, value, suffix, apply) => {
      const wrap = el('label');
      const input = el('input'); input.type = 'range'; input.min = String(min); input.max = String(max); input.step = String(step); input.value = String(value); input.id = id;
      const output = el('output', '', value + suffix);
      input.addEventListener('input', () => { output.textContent = input.value + suffix; });
      input.addEventListener('change', () => apply(Number(input.value)));
      wrap.append(el('span', '', label), input, output);
      box.append(wrap);
    };
    const mark = L.markOf(card), options = card.map || {};
    row('Opacity', 'studio-layer-opacity', 0, 100, 5, card.opacity == null ? 100 : card.opacity, '%',
      value => commitCards(list => { list[index].opacity = value; }));
    if (mark === 'density' || ((mark === 'circle' || mark === 'shape') && !(card.encodings && card.encodings.size))) {
      row(mark === 'density' ? 'Radius' : 'Size', 'studio-layer-radius', 2, 80, 1, options.radius || L.DEFAULT_RADIUS[mark] || 10, ' px',
        value => commitCards(list => { list[index].map = Object.assign({}, list[index].map, {radius: value}); }));
    }
    if (mark === 'density') {
      row('Intensity', 'studio-layer-intensity', 1, 100, 1, options.intensity || L.DEFAULT_INTENSITY, '%',
        value => commitCards(list => { list[index].map = Object.assign({}, list[index].map, {intensity: value}); }));
    }
    return box;
  }
  function rename(item, index) {
    const name = item.querySelector('.studio-layer-name');
    const input = el('input');
    input.value = cardsOf(hooks.sheet())[index].name || '';
    input.placeholder = displayName(cardsOf(hooks.sheet())[index]);
    input.setAttribute('aria-label', 'Layer name');
    name.replaceChildren(input);
    input.focus(); input.select();
    const done = keep => { if (keep) commitCards(list => { list[index].name = input.value.trim().slice(0, 120); }); else hooks.renderAll(); };
    input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); done(true); } else if (event.key === 'Escape') done(false); });
    input.addEventListener('blur', () => done(true), {once: true});
  }
  // Tableau's caret menu, in Tableau's order and words.
  function layerMenu(anchor, card, index, total) {
    const items = [
      ['Rename', () => { const node = document.querySelector('#studio-mark-cards [data-card="' + index + '"]'); if (node) rename(node, index); }],
      [(card.selectable === false ? '✓ ' : '') + 'Disable selection', () => commitCards(list => { list[index].selectable = list[index].selectable === false; })],
      [(card.zoomExtent !== false ? '✓ ' : '') + 'Add to zoom extent', () => commitCards(list => { list[index].zoomExtent = list[index].zoomExtent === false; })],
      null,
      ['Move up', index > 0 ? () => { commitCards(list => { list.splice(index - 1, 0, list.splice(index, 1)[0]); }); hooks.setExpanded(index - 1); } : null],
      ['Move down', index < total - 1 ? () => { commitCards(list => { list.splice(index + 1, 0, list.splice(index, 1)[0]); }); hooks.setExpanded(index + 1); } : null],
      null,
      ['Remove', () => { hooks.setExpanded(Math.max(0, index - 1)); commitCards(list => { list.splice(index, 1); return list; }, 'Layer removed.'); }, 'is-danger']];
    // Compact (#54: 12.5px words on 22px rows) and beside its card, so it hides none of
    // the card's own controls (menuBeside).
    menuBeside(menu(anchor, items, 'Layer', 'studio-layer-caret-menu'), anchor);
  }
  // A layer card's caret menu opens beside the card, on the chart's side, level with the
  // card's head row (the eye, name, source and caret stay in view, and the mark type and
  // encodings under them stay free). Only when there is no room there does it drop under
  // the head row, lined up with the card's right edge, covering what it must.
  const MENU_GAP = 6;
  function menuBeside(box, anchor) {
    const card = anchor.closest('.studio-layer-card');
    if (!box || !card) return;
    const edge = card.getBoundingClientRect(), head = (card.querySelector('.studio-layer-head') || card).getBoundingClientRect();
    const size = box.getBoundingClientRect();
    let left = edge.left - MENU_GAP - size.width, top = head.top, placed = 'beside';
    if (left < 8) {
      left = Math.max(8, Math.min(window.innerWidth - size.width - 8, edge.right - size.width));
      top = head.bottom + 2;
      placed = 'under';
    }
    top = Math.max(8, Math.min(top, window.innerHeight - size.height - 8));
    box.style.left = (left + window.scrollX) + 'px';
    box.style.top = (top + window.scrollY) + 'px';
    box.dataset.placed = placed;
  }
  function menu(anchor, items, label, className) {
    closeMenus();
    const box = el('div', 'studio-layer-menu' + (className ? ' ' + className : ''));
    box.setAttribute('role', 'menu');
    box.setAttribute('aria-label', label);
    for (const entry of items) {
      if (!entry) { box.append(el('hr')); continue; }
      if (entry.group) { box.append(el('div', 'studio-layer-group', entry.group)); continue; }
      const [text, action, extra] = entry;
      const item = el('button', extra || '', text);
      item.type = 'button';
      item.setAttribute('role', 'menuitem');
      if (!action) item.disabled = true;
      else item.addEventListener('click', () => { closeMenus(); action(); });
      box.append(item);
    }
    document.body.append(box);
    const rect = anchor.getBoundingClientRect(), width = box.getBoundingClientRect().width;
    box.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right + window.scrollX - width)) + 'px';
    // Below the anchor when it fits, else above it (a card near the bottom of the Inspector).
    const height = box.getBoundingClientRect().height;
    const below = rect.bottom + 4 + height <= window.innerHeight - 8;
    box.style.top = (below ? rect.bottom + window.scrollY + 4 : Math.max(window.scrollY + 8, rect.top + window.scrollY - height - 4)) + 'px';
    const first = box.querySelector('button:not(:disabled)');
    if (first) first.focus();
    const outside = event => { if (!box.contains(event.target)) closeMenus(); };
    const escape = event => { if (event.key === 'Escape') { closeMenus(); anchor.focus(); } };
    box.dismiss = () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape); };
    setTimeout(() => { document.addEventListener('mousedown', outside); document.addEventListener('keydown', escape); }, 0);
    return box;
  }
  function closeMenus() { for (const node of document.querySelectorAll('.studio-layer-menu')) { if (node.dismiss) node.dismiss(); node.remove(); } }
  // Drag a layer by its grip to change the draw order (Tableau's gesture).
  function wireDrag(item, grip, index) {
    grip.addEventListener('dragstart', event => {
      event.dataTransfer.setData('application/x-mentat-layer', String(index));
      event.dataTransfer.effectAllowed = 'move';
      item.classList.add('is-dragging');
    });
    grip.addEventListener('dragend', () => item.classList.remove('is-dragging'));
    item.addEventListener('dragover', event => {
      if (![...event.dataTransfer.types].includes('application/x-mentat-layer')) return;
      event.preventDefault();
      const box = item.getBoundingClientRect(), above = event.clientY < box.top + box.height / 2;
      item.classList.toggle('is-drop-above', above); item.classList.toggle('is-drop-below', !above);
    });
    item.addEventListener('dragleave', () => item.classList.remove('is-drop-above', 'is-drop-below'));
    item.addEventListener('drop', event => {
      const from = Number(event.dataTransfer.getData('application/x-mentat-layer'));
      item.classList.remove('is-drop-above', 'is-drop-below');
      if (!Number.isInteger(from)) return;
      event.preventDefault();
      const box = item.getBoundingClientRect(), above = event.clientY < box.top + box.height / 2;
      let to = above ? index : index + 1;
      if (from < to) to -= 1;
      if (to === from) return;
      commitCards(list => { list.splice(to, 0, list.splice(from, 1)[0]); }, 'Layer moved; the top layer draws on top.');
      hooks.setExpanded(to);
    });
  }

  /* ------------------------------------------------------------- sources */

  async function snapshots() {
    if (!snapshotList) snapshotList = fetch('/api/evidence').then(response => response.json()).then(body => body.snapshots || []).catch(() => []);
    return snapshotList;
  }
  // PAC workbooks imported on the PAC evidence tab (GET /api/evidence-csv, the same
  // listing its Source picker reads). Asked afresh each time the menu opens: an
  // import made a minute ago on the other tab is listed without a reload.
  async function imports() {
    try {
      const response = await fetch('/api/evidence-csv');
      const body = await response.json();
      return response.ok && Array.isArray(body.sources) ? body.sources : [];
    } catch (error) { return []; }
  }
  function fillSources(select, chosen) {
    const options = [];
    for (const dataset of hooks.datasets()) {
      if (dataset.available === false) continue;
      const industry = dataset.industry || dataset.id;
      options.push([{kind: 'industry', industry, basis: hooks.basis(), label: dataset.label || industry}, (dataset.label || industry) + ' (verified contributions)']);
    }
    if (chosen && chosen.kind === 'pac') options.push([chosen, L.sourceLabel(chosen)]);
    select.replaceChildren(...options.map(([source, label]) => new Option(label, JSON.stringify(source))));
    const current = chosen ? options.find(([source]) => L.sourceKey(source) === L.sourceKey(chosen)) : null;
    if (current) select.value = JSON.stringify(current[0]);
  }
  // "+ Add layer" and the Map Layers pane's sources: every industry this
  // console can address (D-030) and every saved PAC network, one committee,
  // cycle and direction at a time.
  async function sourceMenu(anchor, choose, label) {
    const items = [{group: 'Industries'}];
    for (const dataset of hooks.datasets()) {
      const industry = dataset.industry || dataset.id;
      items.push([(dataset.label || industry), dataset.available === false ? null
        : () => choose({kind: 'industry', industry, basis: hooks.basis(), label: dataset.label || industry})]);
    }
    items.push({group: 'PAC networks'});
    const [list, files] = await Promise.all([snapshots(), imports()]);
    if (!list.length) items.push(['No saved PAC snapshot', null]);
    // Several snapshots can share a company (three Coinbase networks today): the save date tells them apart.
    const snapshotName = snapshot => (snapshot.company || snapshot.id) + (snapshot.retrieved_at ? ' · saved ' + String(snapshot.retrieved_at).slice(0, 10) : '');
    for (const snapshot of list) items.push([snapshotName(snapshot), () => committeeMenu(anchor, snapshot, choose)]);
    // Imported PAC workbooks, their own group: one committee, cycle and direction at a time,
    // read by /api/evidence-geo with source=csv exactly as the PAC evidence tab reads them.
    items.push({group: 'Imported PAC files'});
    if (!files.length) items.push(['No imported PAC file (import one on the PAC evidence tab)', null]);
    for (const file of files) {
      const name = String(file.name || file.id);
      // An import being re-read under newer rules cannot be opened until it is back (evidence.js disables it too).
      items.push([name + (file.imported_at ? ' · imported ' + String(file.imported_at).slice(0, 10) : ''),
        file.reread ? null : () => importMenu(anchor, file, choose)]);
    }
    menu(anchor, items, label, 'studio-layer-source-menu');
  }
  // One imported file's committees, each cycle it has rows in, Spent and Raised.
  function importMenu(anchor, file, choose) {
    const items = [{group: String(file.name || file.id) + ' · committees'}];
    const committees = (file.committees || []).filter(row => /^C\d{8}$/.test(String(row.id || '')));
    for (const row of committees) {
      // The cycles this committee has figures in (the listing's per-cycle totals), else the file's cycles.
      const own = Object.keys(row.receipts_by_cycle || {});
      const cycles = (own.length ? own : (file.cycles || []).map(String)).filter(cycle => /^\d{4}$/.test(cycle)).sort().reverse();
      for (const cycle of cycles) {
        for (const direction of ['spent', 'raised']) {
          const source = {kind: 'pac', source: 'csv', id: String(file.id), committee: row.id, cycle, direction,
            name: String(row.name || row.id).slice(0, 120)};
          if (file.industry && String(file.industry).trim()) source.industry = String(file.industry).trim().slice(0, 120);
          items.push([source.name + ' · ' + (direction === 'spent' ? 'Spent' : 'Raised') + ' ' + L.cycleLabel(cycle), () => choose(source)]);
        }
      }
    }
    if (items.length === 1) items.push(['This file names no FEC committee with a cycle', null]);
    menu(anchor, items, 'Committees', 'studio-layer-source-menu');
  }
  async function committeeMenu(anchor, snapshot, choose) {
    let body = null;
    try { body = await (await fetch('/api/evidence?' + new URLSearchParams({id: snapshot.id}))).json(); } catch (error) { body = null; }
    const rows = (body && body.committee_finances) || [];
    const items = [{group: (snapshot.company || snapshot.id) + ' · committees'}];
    for (const row of rows) {
      for (const direction of ['spent', 'raised']) {
        const source = {kind: 'pac', source: 'snapshot', id: snapshot.id, committee: row.committee_id, cycle: String(row.cycle),
          direction, name: String(row.committee_name || row.committee_id).slice(0, 120)};
        items.push([source.name + ' · ' + (direction === 'spent' ? 'Spent' : 'Raised') + ' ' + L.cycleLabel(row.cycle), () => choose(source)]);
      }
    }
    if (rows.length === 0) items.push(['This snapshot lists no committee', null]);
    menu(anchor, items, 'Committees', 'studio-layer-source-menu');
  }
  function addLayer(spec) {
    const sheet = hooks.sheet();
    const source = spec.source || currentSource();
    if (!source) { hooks.failure('Choose a verified report before adding a map layer.'); return; }
    const geography = spec.geography || (source.kind === 'pac' && source.direction === 'spent' ? 'cd' : 'state');
    const type = spec.type || (geography === 'zcta' || geography === 'point' ? 'circle' : 'filled-map');
    hooks.commit(() => {
      const existing = handlesSheet(sheet) ? cardsOf(sheet) : legacyLayers(sheet);
      const card = L.newLayerCard({id: L.nextLayerId(existing), source, geography, type,
        encodings: type === 'circle' ? {size: pillFor('amount')} : {color: pillFor('amount')},
        map: type === 'density' ? {radius: L.DEFAULT_RADIUS.density, intensity: L.DEFAULT_INTENSITY, ramp: 'heat'} : undefined});
      const next = [card, ...existing].slice(0, L.MAX_CARDS);
      delete sheet.marksCards;
      core().writeCards(sheet, next);
      if (!sheet.map || !sheet.map.background) L.writeSettings(sheet, L.settingsOf(sheet));
      hooks.setExpanded(0);
    });
    hooks.status('Layer added on top: ' + L.sourceLabel(source) + ', ' + L.levelLabel(geography) + '.');
  }
  // A map sheet made from today's single-card map (a Symbol or Filled map):
  // its map becomes the first layer, so nothing it drew is lost.
  function legacyLayers(sheet) {
    const type = sheet.marks && sheet.marks.type;
    if (type !== 'map' && type !== 'filled-map') return [];
    const source = currentSource();
    if (!source) return [];
    const columnIds = [...core().shelf(sheet.columns), ...core().shelf(sheet.rows)].map(p => (fields().find(f => f.index === p.column) || {}).id);
    const geography = type === 'filled-map' || !columnIds.includes('zip5') ? 'state' : 'zcta';
    return [L.newLayerCard({id: 'layer-1', source, geography, type: type === 'filled-map' ? 'filled-map' : 'circle',
      encodings: type === 'filled-map' ? {color: pillFor('amount')} : {size: pillFor('amount')}})];
  }
  function addLayerFromField(field) {
    const geography = geographyOfField(field);
    if (!geography) { hooks.failure(field.name + ' is not a place, so it cannot start a map layer.'); return; }
    addLayer({geography, type: geography === 'zcta' ? 'circle' : 'filled-map'});
  }

  /* ------------------------------------------------ the Map Layers pane */

  function setSettings(patch, message) {
    hooks.commit(() => {
      const sheet = hooks.sheet();
      const next = Object.assign(L.settingsOf(sheet), patch);
      L.writeSettings(sheet, next);
    });
    if (message) hooks.status(message);
  }
  function renderSideSwitch(on) {
    const pane = document.getElementById('studio-pane-data');
    if (!pane) return;
    let bar = pane.querySelector(':scope > .studio-side-switch');
    let mapPane = document.getElementById('studio-pane-maplayers');
    if (!on) {
      if (bar) bar.remove();
      if (mapPane) mapPane.remove();
      for (const child of pane.children) if (child.dataset.mapHidden) { child.hidden = false; delete child.dataset.mapHidden; }
      sidePane = 'data';
      return;
    }
    if (!bar) {
      bar = el('div', 'studio-side-switch');
      bar.setAttribute('role', 'tablist');
      bar.setAttribute('aria-label', 'Left pane');
      for (const [id, label] of [['data', 'Data'], ['map', 'Map layers']]) {
        const tab = el('button', '', label);
        tab.type = 'button'; tab.id = 'studio-side-' + id; tab.setAttribute('role', 'tab');
        tab.addEventListener('click', () => { sidePane = id; renderSideSwitch(true); });
        bar.append(tab);
      }
      pane.prepend(bar);
    }
    for (const tab of bar.children) tab.setAttribute('aria-selected', String(tab.id === 'studio-side-' + sidePane));
    if (!mapPane) { mapPane = el('div', 'studio-map-pane'); mapPane.id = 'studio-pane-maplayers'; mapPane.setAttribute('role', 'tabpanel'); bar.after(mapPane); }
    for (const child of pane.children) {
      if (child === bar || child === mapPane) continue;
      if (sidePane === 'map') { if (!child.hidden) { child.hidden = true; child.dataset.mapHidden = '1'; } }
      else if (child.dataset.mapHidden) { child.hidden = false; delete child.dataset.mapHidden; }
    }
    mapPane.hidden = sidePane !== 'map';
    if (sidePane === 'map') renderMapPane(mapPane);
  }
  // A row's helper note, as an info mark (#54: one line per item). The words are the
  // row's tooltip, and screen readers read them with the row.
  function infoMark(note) {
    const mark = el('span', 'studio-map-info');
    mark.title = note;
    mark.append(icon('info'), el('span', 'sr-only', note));
    return mark;
  }
  function paneHeading(text, note) {
    const head = el('h4', '', text);
    if (note) head.append(infoMark(note));
    return head;
  }
  function renderMapPane(host) {
    const sheet = hooks.sheet(), settings = L.settingsOf(sheet);
    const streets = settings.background === 'streets';
    const parts = [];
    parts.push(paneHeading('Background', 'Nothing here is a layer: this is the backdrop the layers draw over.'));
    const styleRow = el('label', 'studio-map-row');
    styleRow.append(el('span', '', 'Style'));
    const style = el('select'); style.id = 'studio-map-style';
    for (const [id, label] of L.BACKGROUNDS) style.append(new Option(label, id));
    style.value = settings.background;
    style.addEventListener('change', () => setSettings({background: style.value}, style.value === 'streets'
      ? 'Streets (offline): New York metro street tiles; the layers draw over them.' : ''));
    styleRow.append(style);
    const washRow = el('div', 'studio-map-row');
    washRow.append(el('span', '', 'Washout'));
    const washBox = el('div', 'studio-map-washout');
    const wash = el('input'); wash.type = 'range'; wash.min = '0'; wash.max = '100'; wash.step = '5'; wash.value = String(settings.washout); wash.id = 'studio-map-washout';
    wash.setAttribute('aria-label', 'Washout');
    const washOut = el('output', '', settings.washout + '%');
    wash.addEventListener('input', () => { washOut.textContent = wash.value + '%'; });
    wash.addEventListener('change', () => setSettings({washout: Number(wash.value)}));
    washBox.append(wash, washOut); washRow.append(washBox);
    parts.push(styleRow, washRow, el('h4', '', 'Map layers'));
    const fixed = streets ? {coastlines: 'The street map draws the coast with its water.', 'county-names': 'County names draw on the Boundaries map.'} : {};
    for (const layer of L.BASE_LAYERS) {
      const drawn = streets ? layer.streets : layer.albers;
      const reason = !drawn ? 'Streets (offline) only' : fixed[layer.id] || '';
      const row = el('label', 'studio-map-check' + (reason ? ' is-unavailable' : ''));
      const box = el('input'); box.type = 'checkbox'; box.dataset.base = layer.id;
      box.checked = !reason ? settings.base.includes(layer.id) : (layer.id === 'coastlines' && streets);
      box.disabled = !!reason;
      box.addEventListener('change', () => {
        const next = new Set(L.settingsOf(hooks.sheet()).base);
        if (box.checked) next.add(layer.id); else next.delete(layer.id);
        setSettings({base: L.BASE_IDS.filter(id => next.has(id))});
      });
      const text = el('span', 'studio-map-label', layer.label);
      row.append(box, text);
      // One line per layer: a zoom gate or an unavailable renderer is an info mark with its words.
      const gated = streets ? layer.zoom : layer.scale;
      const note = reason || (gated ? 'shows when zoomed in' : '');
      if (note) { row.title = layer.label + ': ' + note; row.append(infoMark(note)); }
      parts.push(row);
    }
    parts.push(paneHeading('Data layer', 'A measure drawn as a translucent choropleth under every layer.'));
    const measures = fields().filter(field => ['amount', 'records', 'contributors'].includes(field.id));
    const dataRow = (label, id, options, value, apply) => {
      const row = el('label', 'studio-map-row');
      row.append(el('span', '', label));
      const select = el('select'); select.id = id;
      for (const [key, text] of options) select.append(new Option(text, key));
      select.value = value;
      select.addEventListener('change', () => apply(select.value));
      row.append(select);
      return row;
    };
    const current = settings.data;
    parts.push(dataRow('Layer', 'studio-map-data-layer', [['', 'None'], ...measures.map(field => [String(field.index), field.name])],
      current && current.measure ? String(current.measure.column) : '', value => setSettings({data: value === '' ? null
        : {measure: {column: Number(value), aggregation: 'sum'}, by: current ? current.by : 'county', using: current ? current.using : 'teal'}})));
    parts.push(dataRow('By', 'studio-map-data-by', L.DATA_BY, current ? current.by : 'county',
      value => current && setSettings({data: Object.assign({}, current, {by: value})})));
    parts.push(dataRow('Using', 'studio-map-data-using', L.RAMP_IDS.filter(id => id !== 'heat').map(id => [id, L.RAMP_LABELS[id]]), current ? current.using : 'teal',
      value => current && setSettings({data: Object.assign({}, current, {using: value})})));
    parts.push(el('h4', '', 'Map options'));
    for (const key of ['scale', 'search', 'toolbar', 'panZoom', 'layerControl']) {
      const row = el('label', 'studio-map-check');
      const box = el('input'); box.type = 'checkbox'; box.dataset.option = key; box.checked = settings.options[key] !== false;
      box.addEventListener('change', () => setSettings({options: Object.assign({}, L.settingsOf(hooks.sheet()).options, {[key]: box.checked})}));
      row.append(box, el('span', 'studio-map-label', L.OPTION_LABELS[key]));
      if (key === 'scale') {
        // Short choices so "Show map scale" keeps its words on the pane's one line; the select is named Units.
        const units = el('select', 'studio-map-units'); units.id = 'studio-map-units';
        units.setAttribute('aria-label', 'Map scale units'); units.title = 'Map scale units';
        units.append(new Option('U.S.', 'us'), new Option('Metric', 'metric'));
        units.value = settings.options.units === 'metric' ? 'metric' : 'us';
        units.addEventListener('change', () => setSettings({options: Object.assign({}, L.settingsOf(hooks.sheet()).options, {units: units.value})}));
        units.addEventListener('click', event => event.preventDefault());
        row.append(units);
      }
      parts.push(row);
    }
    host.replaceChildren(...parts);
  }

  /* ---------------------------------------- Geography and Aggregate by */

  const LEVEL_STRIP = [['state', 'State', 'state'], ['county', 'County', 'county'], ['district', 'District', 'cd'], ['zip', 'ZIP', 'zcta'],
    ['tract', 'Tract', 'tract'], ['hexbin', 'Hexbin', 'hex25'], ['points', 'Points', 'point']];
  function tractReason(card) {
    const source = card && card.source;
    if (!source) return 'Choose a layer first.';
    return 'Tract needs street addresses; ' + (source.kind === 'industry' ? 'verified contributions carry ZIP codes' : 'this PAC network is placed by ZIP, seat or state') + ', so no row could be placed.';
  }
  function renderGeographyRow(on) {
    const shelves = $('shelves');
    if (!shelves) return;
    shelves.classList.toggle('is-map-layers', on);
    const viewRoot = document.getElementById('view-chart-studio');
    if (viewRoot) viewRoot.classList.toggle('is-map-layers', on);
    let row = shelves.querySelector(':scope > .studio-map-geo');
    if (!on) { if (row) row.remove(); return; }
    if (!row) { row = el('div', 'studio-map-geo'); row.setAttribute('role', 'group'); row.setAttribute('aria-label', 'Geography of the open layer'); shelves.prepend(row); }
    const cards = cardsOf(hooks.sheet()), open = openIndex(), card = cards[open];
    const button = el('button', 'studio-map-geo-button');
    button.type = 'button'; button.id = 'studio-map-geography';
    button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false');
    button.append(icon('globe'), document.createTextNode('Geography'));
    // Which layer the row sets is said in the button's tooltip, not as words in the row (#49, #53).
    const which = card ? displayName(card) + ' · ' + L.sourceLabel(card.source) : '';
    button.title = (which ? 'The open layer, ' + which + ': ' : '') + 'aggregate it by ZIP, county, district, hexagon, density or points';
    row.setAttribute('aria-label', 'Geography of the open layer' + (which ? ', ' + which : ''));
    button.addEventListener('click', () => aggregatePopover(button));
    const strip = el('div', 'studio-map-levels');
    const current = L.aggregateOf(card);
    for (const [id, label, geography] of LEVEL_STRIP) {
      const choice = el('button', '', label);
      choice.type = 'button'; choice.dataset.aggregate = id;
      choice.setAttribute('aria-pressed', String(current === id || (id === 'points' && current === 'density')));
      if (id === 'tract') { choice.disabled = true; choice.title = tractReason(card); }
      choice.addEventListener('click', () => setAggregate(id, geography));
      strip.append(choice);
    }
    row.replaceChildren(button, strip);
  }
  function setAggregate(id, geography, sizeMiles) {
    const open = openIndex();
    commitCards(list => {
      const card = list[open];
      if (!card) return;
      const entry = L.AGGREGATE_BY.find(item => item.id === id);
      let level = geography || (entry ? entry.geography : card.geography);
      if (id === 'hexbin') level = 'hex' + (sizeMiles || (L.isHex(card.geography) ? card.geography.slice(3) : 25));
      card.geography = level;
      if (entry && entry.type) card.type = entry.type;
      else if (id === 'points') card.type = 'circle';
      else if (L.markOf(card) === 'density' || (level !== 'point' && card.type === 'circle' && id !== 'zip')) card.type = 'filled-map';
      if (card.type === 'density') card.map = Object.assign({radius: L.DEFAULT_RADIUS.density, intensity: L.DEFAULT_INTENSITY, ramp: 'heat'}, card.map || {});
      keepPatternValid(card);
    }, 'The open layer is aggregated by ' + (L.AGGREGATE_BY.find(item => item.id === id) || {label: id}).label.toLowerCase() + '.');
  }
  // #53's six tiles, each a mini-map of the country in its own aggregation, drawn from the
  // geo v2 files the map itself reads (state, county and district shapes, the 50-mile
  // hexagons; county centres for the ZIP, point and density tiles) once per page, in the
  // theme's teal ramp and the density's heat ramp. Tract is not a tile: no source carries
  // street addresses today, and the strip's greyed Tract button says so.
  const AGGREGATE_TILES = [['zip', 'ZIP code'], ['county', 'County'], ['district', 'Congressional district'],
    ['hexbin', 'Hexbin'], ['density', 'Density'], ['points', 'Points']];
  const THUMB_W = 84, THUMB_H = 48, THUMB_DENSITY = 2;
  const thumbs = new Map();   // tile id -> Promise<canvas>
  function hashOf(value) {
    let h = 2166136261;
    const text = String(value);
    for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) / 4294967295;
  }
  function thumbFor(id) {
    if (!thumbs.has(id)) {
      const made = drawThumb(id);
      thumbs.set(id, made);
      made.catch(() => thumbs.delete(id));
    }
    return thumbs.get(id);
  }
  async function drawThumb(id) {
    const canvas = document.createElement('canvas');
    canvas.width = THUMB_W * THUMB_DENSITY; canvas.height = THUMB_H * THUMB_DENSITY;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('no canvas');
    // The 975x610 frame fitted into the tile, as the map fits it: lower 48 and the insets.
    const k = Math.min(canvas.width / E.FRAME_W, canvas.height / E.FRAME_H);
    const ox = (canvas.width - E.FRAME_W * k) / 2, oy = (canvas.height - E.FRAME_H * k) / 2, px = 1 / k;
    const teal = L.rampStops('teal');
    const shade = value => L.colourAt(teal, 0.1 + 0.82 * value);
    const dark = id === 'density';
    const states = await E.albersFeatures('state');
    const land = new Path2D();
    for (const shape of Object.values(states)) if (shape && shape.path) land.addPath(new Path2D(shape.path));
    if (dark) { context.fillStyle = '#3d2f60'; context.fillRect(0, 0, canvas.width, canvas.height); }
    context.setTransform(k, 0, 0, k, ox, oy);
    context.fillStyle = dark ? '#4e3f78' : '#e6efed';
    context.fill(land);
    const centres = async () => Object.values(await E.albersFeatures('county')).map(shape => shape && shape.anchor).filter(Boolean);
    const shapes = async (level, curve) => {
      const found = await E.albersFeatures(level, {congress: '119'});
      context.lineWidth = 0.45 * px;
      context.strokeStyle = 'rgba(255,255,255,0.9)';
      for (const [key, shape] of Object.entries(found)) {
        if (!shape || !shape.path) continue;
        const path = new Path2D(shape.path);
        context.fillStyle = shade(Math.pow(hashOf(key), curve));
        context.fill(path);
        context.stroke(path);
      }
    };
    if (id === 'county') await shapes('county', 1.5);
    else if (id === 'district') await shapes('cd', 1.2);
    else if (id === 'hexbin') await shapes('hex50', 1.7);
    else if (id === 'zip') {
      for (const [x, y] of await centres()) {
        context.fillStyle = shade(Math.pow(hashOf(x.toFixed(1) + ',' + y.toFixed(1)), 1.3));
        context.fillRect(x - 1.2 * px, y - 1.2 * px, 2.4 * px, 2.4 * px);
      }
    } else if (id === 'points') {
      context.fillStyle = teal[3];
      let at = 0;
      for (const [x, y] of await centres()) {
        if ((at += 1) % 3) continue;
        context.beginPath(); context.arc(x, y, 1.1 * px, 0, 2 * Math.PI); context.fill();
      }
    } else if (dark) {
      // The Density mark itself at tile size: county centres of equal weight, the heat ramp.
      context.setTransform(1, 0, 0, 1, 0, 0);
      const points = (await centres()).map(([x, y]) => ({x: ox + x * k, y: oy + y * k, weight: 1}));
      const grid = L.densityGrid(points, canvas.width, canvas.height, 9, 1);
      const glow = document.createElement('canvas');
      glow.width = grid.columns; glow.height = grid.rows;
      glow.getContext('2d').putImageData(new ImageData(L.densityPixels(grid, L.rampStops('heat'), 45, 1), grid.columns, grid.rows), 0, 0);
      context.drawImage(glow, 0, 0);
    }
    context.setTransform(1, 0, 0, 1, 0, 0);
    return canvas;
  }
  // Inside the chart card's top-right corner, over the map's right side, as #53 draws it (the
  // lead's call: beside the shelf row it covered the top of the Inspector). The chart is the
  // map's own box (#studio-canvas), so the card's title, subtitle and Expand chart stay clear.
  // Below the Geography button when the chart is too narrow or too short to hold it.
  const AGGREGATE_WIDTH = [236, 280];   // the popover's narrowest and widest (px)
  const AGGREGATE_INSET = 10;           // px in from the chart's top and right edges
  function placeAggregate(pop, anchor) {
    const canvas = $('canvas');
    const chart = canvas && !canvas.hidden ? canvas.getBoundingClientRect() : null;
    const rect = anchor.getBoundingClientRect();
    const room = chart ? chart.width - 2 * AGGREGATE_INSET : 0;
    pop.style.width = (room >= AGGREGATE_WIDTH[0] ? Math.min(AGGREGATE_WIDTH[1], room) : AGGREGATE_WIDTH[1]) + 'px';
    const box = pop.getBoundingClientRect();
    const inside = !!chart && room >= AGGREGATE_WIDTH[0] && chart.height >= box.height + 2 * AGGREGATE_INSET;
    let left, top;
    if (inside) { left = chart.right - AGGREGATE_INSET - box.width; top = chart.top + AGGREGATE_INSET; }
    else { left = Math.max(8, Math.min(window.innerWidth - 8 - box.width, rect.left)); top = rect.bottom + 6; }
    left = Math.max(8, Math.min(left, window.innerWidth - 8 - box.width));
    top = Math.max(8, Math.min(top, window.innerHeight - 8 - box.height));
    pop.style.left = (left + window.scrollX) + 'px';
    pop.style.top = (top + window.scrollY) + 'px';
    pop.dataset.placed = inside ? 'chart' : 'below';
  }
  async function aggregatePopover(anchor) {
    closeMenus();
    for (const node of document.querySelectorAll('.studio-map-aggregate')) node.remove();
    const cards = cardsOf(hooks.sheet()), open = openIndex(), card = cards[open];
    if (!card) return;
    const pop = el('div', 'studio-popover studio-map-aggregate');
    pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Aggregate by');
    pop.append(el('h4', '', 'Aggregate by'));
    const tiles = el('div', 'studio-map-tiles');
    const current = L.aggregateOf(card);
    for (const [id, label] of AGGREGATE_TILES) {
      const tile = el('button', 'studio-map-tile');
      tile.type = 'button'; tile.dataset.kind = id; tile.dataset.aggregate = id;
      tile.setAttribute('aria-pressed', String(current === id));
      const thumb = el('canvas', 'studio-map-thumb');
      thumb.width = THUMB_W * THUMB_DENSITY; thumb.height = THUMB_H * THUMB_DENSITY;
      thumb.setAttribute('aria-hidden', 'true');
      thumbFor(id).then(drawn => {
        const context = thumb.getContext('2d');
        if (context) context.drawImage(drawn, 0, 0);
        tile.classList.add('has-thumb');
      }).catch(() => { /* the tile keeps its plain box and its word */ });
      tile.append(thumb, el('span', 'studio-map-tile-label', label));
      if (current === id) tile.append(el('span', 'studio-map-tick', '✓'));
      tile.addEventListener('click', () => { close(); setAggregate(id); });
      tiles.append(tile);
    }
    pop.append(tiles);
    const hexRow = el('label', 'studio-map-hex');
    hexRow.append(el('span', '', 'Hexagon size'));
    const size = el('input'); size.type = 'range'; size.min = '0'; size.max = '2'; size.step = '1'; size.id = 'studio-map-hex-size';
    const sizes = L.HEX_SIZES, chosen = L.isHex(card.geography) ? Number(card.geography.slice(3)) : 25;
    size.value = String(Math.max(0, sizes.indexOf(chosen)));
    const out = el('output', '', chosen + ' mi');
    size.addEventListener('input', () => { out.textContent = sizes[Number(size.value)] + ' mi'; });
    size.addEventListener('change', () => { close(); setAggregate('hexbin', null, sizes[Number(size.value)]); });
    hexRow.append(size, out);
    pop.append(hexRow);
    const note = el('p', 'studio-map-note', 'ZIP centres from the Census gazetteer; rows without the place a level needs are listed, not placed.');
    pop.append(note);
    document.body.append(pop);
    placeAggregate(pop, anchor);
    anchor.setAttribute('aria-expanded', 'true');
    // Closing takes its document listeners with it (a tile, the size slider, a click outside or Escape).
    function close() {
      pop.remove(); anchor.setAttribute('aria-expanded', 'false');
      document.removeEventListener('mousedown', dismiss); document.removeEventListener('keydown', escape);
    }
    const dismiss = event => { if (!pop.contains(event.target) && event.target !== anchor) close(); };
    const escape = event => { if (event.key === 'Escape' && pop.isConnected) { close(); anchor.focus(); } };
    setTimeout(() => { if (pop.isConnected) { document.addEventListener('mousedown', dismiss); document.addEventListener('keydown', escape); } }, 0);
    // The listed count, from the layer as drawn (#53: "312 rows without a ZIP are listed, not placed").
    try {
      const data = await layerRows(card, hooks.sheet());
      await getLookup().ready(data.rows.map(row => row.zip).filter(Boolean));
      const placed = L.aggregate(data.rows, 'zcta', getLookup(), {congress: data.congress});
      const listed = placed.unplaced.reduce((sum, entry) => sum + entry.rows, 0);
      note.textContent = 'ZIP centres from the Census gazetteer; ' + listed.toLocaleString('en-US') + ' row' + (listed === 1 ? '' : 's') + ' without a usable ZIP ' + (listed === 1 ? 'is' : 'are') + ' listed, not placed.';
    } catch (error) { /* the general sentence stays */ }
  }

  /* ------------------------------------------------ Add a marks layer */

  function mapCanvas() { const canvas = $('canvas'); return canvas && canvas.__mapHandle ? canvas.__mapHandle : null; }
  function isMapSheet(sheet) { return handlesSheet(sheet) || (sheet && sheet.marks && ['map', 'filled-map'].includes(sheet.marks.type)); }
  function wireDropTarget() {
    document.addEventListener('dragstart', event => {
      const pill = event.target && event.target.closest ? event.target.closest('#studio-pane-data .studio-pill') : null;
      if (!pill || pill.dataset.zone || !hooks || !hooks.verified()) return;
      const field = fields().find(item => item.index === Number(pill.dataset.column));
      if (!geographyOfField(field) || !isMapSheet(hooks.sheet())) return;
      dragField = field;
      const handle = mapCanvas();
      if (handle) handle.showDropTarget(true, 'Add a marks layer');
      else if (hooks.sheet().marks && ['map', 'filled-map'].includes(hooks.sheet().marks.type)) legacyDropTarget(true);
    });
    document.addEventListener('dragend', () => {
      dragField = null;
      const handle = mapCanvas();
      if (handle) handle.showDropTarget(false);
      legacyDropTarget(false);
    });
    document.addEventListener('dragover', event => {
      const target = event.target && event.target.closest ? event.target.closest('.mm-drop, .studio-map-legacy-drop') : null;
      if (!target || !dragField) return;
      event.preventDefault();
      target.classList.add('is-over');
    });
    document.addEventListener('dragleave', event => {
      const target = event.target && event.target.closest ? event.target.closest('.mm-drop, .studio-map-legacy-drop') : null;
      if (target) target.classList.remove('is-over');
    });
    document.addEventListener('drop', event => {
      const target = event.target && event.target.closest ? event.target.closest('.mm-drop, .studio-map-legacy-drop') : null;
      if (!target || !dragField) return;
      event.preventDefault();
      const field = dragField;
      dragField = null;
      addLayerFromField(field);
    });
  }
  // Today's single-card map (contribution-map.js) has no overlay of its own;
  // the target is laid over its canvas while a place field is dragged.
  function legacyDropTarget(on) {
    const canvas = $('canvas');
    if (!canvas) return;
    let target = canvas.querySelector(':scope > .studio-map-legacy-drop');
    if (!on) { if (target) target.remove(); return; }
    if (!target) { target = el('div', 'mm-drop studio-map-legacy-drop', 'Add a marks layer'); canvas.style.position = 'relative'; canvas.prepend(target); }
  }

  /* ----------------------------------------------------- the handoff */

  // PAC evidence's "Open in Chart Studio" (#55): the evidence map's cards
  // become a new map sheet, exactly as they were (their names, sources,
  // levels and switches). Applied once the studio has its data, so the
  // sheet list it restores cannot overwrite it.
  function openHandoff(handoff) {
    pendingHandoff = handoff;
    applyHandoff();
  }
  function applyHandoff() {
    if (!pendingHandoff || !hooks || !hooks.ready()) return false;
    const handoff = pendingHandoff;
    pendingHandoff = null;
    const sheet = core().createSheet(String(handoff.name || 'PAC map').slice(0, 80), 's' + Date.now());
    const cards = (handoff.cards || []).map(card => JSON.parse(JSON.stringify(card)));
    for (const card of cards) {
      for (const zone of ['color', 'size', 'label']) {
        const pill = card.encodings && card.encodings[zone];
        if (pill && pill.field) card.encodings[zone] = pillFor(pill.field);
      }
    }
    core().writeCards(sheet, cards);
    L.writeSettings(sheet, Object.assign(L.settingsOf({}), handoff.settings || {}));
    if (handoff.settings && /^(116|118|119)$/.test(String(handoff.settings.congress || ''))) sheet.map.congress = String(handoff.settings.congress);
    hooks.addSheet(sheet);
    hooks.setExpanded(0);
    hooks.status('Opened ' + cards.length + ' layer' + (cards.length === 1 ? '' : 's') + ' from PAC evidence: each card can now be edited in Marks.');
    return true;
  }

  /* ------------------------------------------------------------- sync */

  // Called at the end of every renderShelves: the pieces of the studio a
  // map sheet changes, and their undoing when the sheet is not a map.
  function sync() {
    if (!hooks) return;
    // Applied on the next turn: sync runs inside renderAll, and the handoff
    // commits (which renders again).
    if (pendingHandoff) setTimeout(applyHandoff, 0);
    const on = hooks.verified() && handlesSheet(hooks.sheet());
    renderSideSwitch(on);
    renderGeographyRow(on);
    const legacy = document.getElementById('studio-map-settings');
    if (legacy && on) legacy.hidden = true;
    const list = $('mark-cards');
    if (list && !on) { list.classList.remove('studio-layer-list'); list.removeAttribute('aria-label'); }
    // Only the tooltip this module set is taken away (a chart's own tooltip is its renderer's).
    const title = $('title');
    if (title && !on && 'mapTitle' in title.dataset) {
      if (title.getAttribute('title') === title.dataset.mapTitle) title.removeAttribute('title');
      delete title.dataset.mapTitle;
    }
    if (on) layerReasons();
  }
  // The open layer's greyed encoding rows say what the layer is (#53: a hexbin says Hexbin,
  // and Detail says it is set under Aggregate by), not the chart mark the sheet had before.
  // Runs after renderMarkRows, which sync() always follows (chart-studio.js renderShelves).
  function layerReasons() {
    const card = cardsOf(hooks.sheet())[openIndex()];
    if (!card) return;
    for (const zone of L.markRows(card)) {
      const drop = $(zone);
      if (drop && drop.dataset.reasonShort) drop.dataset.reasonShort = L.reasonShort(card, zone);
    }
  }
  function init(given) {
    if (hooks) { hooks = given; return; }
    hooks = given;
    wireDropTarget();
  }

  // The data-source chip at the top of Chart Studio (chart-studio.js scopeText and
  // renderScopeChip) for a map sheet: its layers read their own sources, so the chip
  // names them - one source by its label, several as "N sources: ..." with the full
  // list, each with its basis, in the button's title. A map whose one source is the
  // builder's own industry and basis returns null: today's chip (industry, cycles,
  // basis) already says exactly what it reads.
  function scopeChip(sheet) {
    if (!hooks || !hooks.verified() || !handlesSheet(sheet)) return null;
    const found = new Map();
    for (const card of cardsOf(sheet)) {
      const key = L.sourceKey(card.source);
      if (key && !found.has(key)) found.set(key, card.source);
    }
    const sources = [...found.values()];
    if (!sources.length) return null;
    const dataset = hooks.dataset() || {};
    const own = source => source.kind === 'industry' && (source.basis || 'receipt') === hooks.basis()
      && [dataset.industry, dataset.id, dataset.label].filter(Boolean).includes(source.industry);
    if (sources.length === 1 && own(sources[0])) return null;
    const bases = (root.MentatStudioContributions && root.MentatStudioContributions.BASIS) || {};
    const basisOf = source => source.kind === 'industry' ? ((bases[source.basis || 'receipt'] || [])[0] || '') : L.sourceBadge(source);
    const labels = sources.map(L.paneLabel);
    const kinds = [...new Set(sources.map(basisOf).filter(Boolean))];
    const listed = sources.map((source, at) => labels[at] + (basisOf(source) ? ' (' + basisOf(source) + ')' : '')).join('; ');
    const text = sources.length === 1 ? labels[0] : sources.length + ' sources: ' + labels.join(', ');
    return {text, basis: kinds.length === 1 ? kinds[0] : '', sources: labels,
      title: (sources.length === 1 ? 'This map reads one source: ' : 'This map reads ' + sources.length + ' sources, one per layer: ') + listed
        + '. Each layer’s source is chosen on its marks card; open to change the builder’s data source.'};
  }

  // The Colour popover's Opacity, Border and Halo for a layer: every map mark
  // takes Opacity; Density has no outline; a label's outline is its Halo.
  function styleSupport(card) {
    const mark = L.markOf(card);
    if (mark === 'density') { const why = 'Density draws a heat, not outlined marks, so it takes Opacity only.'; return {opacity: true, border: why, halo: why}; }
    if (mark === 'text') return {opacity: true, border: 'A label has no border; Halo sets the outline around its letters.', halo: true};
    return {opacity: true, border: true, halo: true};
  }

  root.MentatMapStudio = {init, handles: handlesSheet, renderView, renderLayerCards, sync, addLayerFromField, addLayer,
    openHandoff, styleSupport, encodingSupport: card => L.encodingSupport(card), markRows: card => L.markRows(card),
    sizeMeans: card => L.SIZE_MEANS[L.markOf(card)] || '', geographyOfField, viewStateFor, scopeChip};
})(typeof globalThis !== 'undefined' ? globalThis : this);
