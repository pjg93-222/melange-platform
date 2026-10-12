/* The map a sheet's layers draw into (lane P, boards #47, #48, #49, #53, #54, #55).

   One layer model, two renderers (Paul, C3: "A and B"):
     Boundaries, Light, Dark and None draw here, as SVG in the 975x610 Albers
     frame of lane G's geo/v2 files (the frame every shipped map uses);
     Streets (offline) hands the same compiled layers to map-streets.mjs, which
     draws them with MapLibre over the offline street tiles.
   Either way this module owns what surrounds the drawing and is the same for
   both: the Layer control (a floating, draggable list with an eye and a
   selection pointer per layer, beside the view toolbar), the legends (one
   compact card per visible layer, in a band BELOW the drawing, so a legend
   never covers the map or its Alaska and Hawaii insets; the band collapses),
   the view toolbar, map search, the scale bar and the "Add a marks layer"
   drop target.

   Draw order is card order: the first card draws on top. A Density layer is a
   canvas kernel density drawn UNDER the SVG marks and over the base (build
   packet s3), recomputed whenever the view settles, fading to transparent where
   the density is low so the plain base shows through.

   The Layer control's toggles are view state only (D-039: no storage key); the
   Marks card's eye is the author's, saved with the sheet. Nothing here fetches
   money: the caller hands in compiled layers (map-layers.js compileLayer). */
(function (root) {
  'use strict';
  if (typeof document === 'undefined') return;
  const L = root.MentatMapLayers;
  const NS = 'http://www.w3.org/2000/svg';
  const FRAME_W = 975, FRAME_H = 610;
  const MAX_ZOOM = 40;
  const KM_PER_PX = 6371.0088 / 1075.834155;          // lower-48 conic, at zoom 1
  const EXTRACT_BOUNDS = [-74.30, 40.45, -73.65, 40.95];  // the offline street tiles (lane E)

  /* ------------------------------------------------------------ geometry */

  const cache = new Map();
  let indexRequest = null;
  function fetchJson(path) {
    if (cache.has(path)) { const hit = cache.get(path); cache.delete(path); cache.set(path, hit); return hit; }
    const request = fetch(path).then(response => {
      if (!response.ok) throw new Error('Map geography ' + path + ' could not be read (' + response.status + ').');
      return response.json();
    });
    cache.set(path, request);
    request.catch(() => cache.delete(path));
    while (cache.size > 40) cache.delete(cache.keys().next().value);
    return request;
  }
  // geo/v2/index.json lists every file with its form, tier, level, state and
  // part, so a split file (CA and TX tracts) is read whole.
  function geoIndex() {
    if (!indexRequest) indexRequest = fetchJson('/geo/v2/index.json').then(body => {
      const table = new Map();
      for (const file of body.files || []) {
        const key = [file.form, file.tier, file.level, file.state || ''].join('|');
        if (!table.has(key)) table.set(key, []);
        table.get(key).push(file);
      }
      for (const list of table.values()) list.sort((a, b) => (a.part || 1) - (b.part || 1));
      return {table, frame: body.frame};
    });
    return indexRequest;
  }
  async function paths(form, tier, level, state) {
    const index = await geoIndex();
    return (index.table.get([form, tier, level, state || ''].join('|')) || []).map(file => '/geo/v2/' + file.path);
  }
  // Albers features at one level: {id: {path, anchor, bbox, name}}.
  async function albersFeatures(level, options) {
    const settings = options || {};
    const merge = bodies => {
      const out = {};
      for (const body of bodies) {
        const features = body.features || body.districts || body.states || {};
        for (const [id, feature] of Object.entries(features)) out[id] = feature;
      }
      return out;
    };
    if (level === 'state') return merge([await fetchJson('/geo/v2/albers/national/state.json')]);
    if (level === 'county') return merge(await Promise.all((await paths('albers', 'national', 'county')).map(fetchJson)));
    if (level === 'cd') {
      const congress = ['116', '118', '119'].includes(String(settings.congress)) ? String(settings.congress) : '119';
      return merge([await fetchJson('/geo/v2/albers/national/cd' + congress + '.json')]);
    }
    if (level === 'hex25' || level === 'hex50') return merge(await Promise.all((await paths('albers', 'hex', level)).map(fetchJson)));
    const states = settings.states || [];
    const tier = level === 'hex10' ? 'hex' : settings.detail ? 'detail' : 'national';
    const lists = await Promise.all(states.map(usps => paths('albers', tier, level, usps)));
    return merge(await Promise.all(lists.flat().map(fetchJson)));
  }
  let statesLookup = null;
  function stateAnchors() {
    if (!statesLookup) statesLookup = fetchJson('/geo/v2/lookup/states.json').then(body => {
      const out = {};
      for (const [fips, row] of Object.entries(body.rows || {})) {
        const point = L.projectFrame(row[4], row[5], fips);
        if (point) out[row[0]] = {fips, name: row[1], point};
      }
      return out;
    });
    return statesLookup;
  }
  // The states a set of place ids lies in (per-state files are read only for
  // those): a GEOID starts with its state FIPS, a district key with its postal
  // code, a ZIP or hexagon carries its state on the compiled item.
  function statesOf(layer, uspsOf) {
    const found = new Set();
    for (const item of layer.items || []) {
      const id = String(item.id);
      const fips = item.statefp || (/^\d{5,}$/.test(id) && layer.level !== 'zcta' ? id.slice(0, 2) : '');
      const usps = fips ? uspsOf(fips) : /^[A-Z]{2}-/.test(id) ? id.slice(0, 2) : /^[A-Z]{2}$/.test(id) ? id : '';
      if (usps) found.add(usps);
    }
    return [...found].sort();
  }

  /* -------------------------------------------------------------- helpers */

  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const svgEl = (tag, attributes) => { const node = document.createElementNS(NS, tag); for (const [key, value] of Object.entries(attributes || {})) if (value != null) node.setAttribute(key, String(value)); return node; };
  const money = value => L.fullMoney(value);
  const count = value => Math.round(Number(value) || 0).toLocaleString('en-US');
  const ICONS = {
    eye: '<path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
    eyeOff: '<path d="M2.5 2.5l11 11M6.2 4c.6-.3 1.2-.5 1.8-.5 4.1 0 6.5 4.5 6.5 4.5s-.6 1.2-1.8 2.4M4 5.6C2.4 6.8 1.5 8 1.5 8s2.4 4.5 6.5 4.5c.9 0 1.7-.2 2.4-.5"/>',
    pointer: '<path d="M4 2.5l8 6-3.6.6 2 4.2-1.5.7-2-4.2L4 12.5z"/>',
    search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4l3.6 3.6"/>',
    plus: '<path d="M8 3v10M3 8h10"/>', minus: '<path d="M3 8h10"/>',
    home: '<path d="M2.5 8 8 3l5.5 5M4 7v6h8V7"/>',
    hand: '<path d="M5.5 8V3.8a1 1 0 0 1 2 0V8m0-.5V2.8a1 1 0 0 1 2 0V8m0-.2V4a1 1 0 0 1 2 0v5.5c0 2.8-1.8 4.5-4.2 4.5-1.8 0-2.8-.9-3.8-2.5L2.3 8.6a1 1 0 0 1 1.6-1.2L5.5 9"/>',
    layers: '<path d="M8 2 1.5 5.5 8 9l6.5-3.5z"/><path d="M1.5 8.2 8 11.7l6.5-3.5M1.5 10.9 8 14.4l6.5-3.5"/>',
    grip: '<circle cx="6" cy="4" r=".9"/><circle cx="10" cy="4" r=".9"/><circle cx="6" cy="8" r=".9"/><circle cx="10" cy="8" r=".9"/><circle cx="6" cy="12" r=".9"/><circle cx="10" cy="12" r=".9"/>',
    close: '<path d="M4 4l8 8M12 4l-8 8"/>',
    chevron: '<path d="M4.5 6.5 8 10l3.5-3.5"/>'};
  function icon(name) {
    const svg = svgEl('svg', {viewBox: '0 0 16 16', 'aria-hidden': 'true', focusable: 'false', class: 'mm-icon'});
    const holder = document.createElementNS(NS, 'g');
    // Paths are static strings from this file, parsed as SVG markup.
    holder.innerHTML = ICONS[name] || '';
    svg.append(...holder.childNodes);
    return svg;
  }
  function button(className, label, iconName, onClick) {
    const node = el('button', className);
    node.type = 'button';
    node.setAttribute('aria-label', label);
    node.title = label;
    if (iconName) node.append(icon(iconName));
    node.addEventListener('click', event => { event.stopPropagation(); onClick(event); });
    return node;
  }
  // Glyphs for the Shape mark, in a unit box centred on 0,0.
  const GLYPH_PATHS = {
    circle: r => `M${-r} 0a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`,
    square: r => `M${-r} ${-r}h${2 * r}v${2 * r}h${-2 * r}z`,
    diamond: r => `M0 ${-r * 1.3}L${r * 1.3} 0L0 ${r * 1.3}L${-r * 1.3} 0z`,
    'triangle-up': r => `M0 ${-r * 1.2}L${r * 1.15} ${r * 0.85}H${-r * 1.15}z`,
    'triangle-down': r => `M0 ${r * 1.2}L${r * 1.15} ${-r * 0.85}H${-r * 1.15}z`,
    'triangle-left': r => `M${-r * 1.2} 0L${r * 0.85} ${-r * 1.15}V${r * 1.15}z`,
    'triangle-right': r => `M${r * 1.2} 0L${-r * 0.85} ${-r * 1.15}V${r * 1.15}z`,
    plus: r => `M${-r * 0.35} ${-r}h${r * 0.7}v${r * 0.65}h${r * 0.65}v${r * 0.7}h${-r * 0.65}v${r * 0.65}h${-r * 0.7}v${-r * 0.65}h${-r * 0.65}v${-r * 0.7}h${r * 0.65}z`,
    times: r => `M${-r} ${-r * 0.5}L${-r * 0.5} ${-r}L0 ${-r * 0.5}L${r * 0.5} ${-r}L${r} ${-r * 0.5}L${r * 0.5} 0L${r} ${r * 0.5}L${r * 0.5} ${r}L0 ${r * 0.5}L${-r * 0.5} ${r}L${-r} ${r * 0.5}L${-r * 0.5} 0z`,
    asterisk: r => `M${-r * 0.2} ${-r}h${r * 0.4}v${r * 0.65}l${r * 0.55} ${-r * 0.35}l${r * 0.2} ${r * 0.35}l${-r * 0.55} ${r * 0.35}l${r * 0.55} ${r * 0.35}l${-r * 0.2} ${r * 0.35}l${-r * 0.55} ${-r * 0.35}v${r * 0.65}h${-r * 0.4}v${-r * 0.65}l${-r * 0.55} ${r * 0.35}l${-r * 0.2} ${-r * 0.35}l${r * 0.55} ${-r * 0.35}l${-r * 0.55} ${-r * 0.35}l${r * 0.2} ${-r * 0.35}l${r * 0.55} ${r * 0.35}z`,
    star: r => { let d = ''; for (let i = 0; i < 10; i += 1) { const a = -Math.PI / 2 + i * Math.PI / 5, k = i % 2 ? r * 0.45 : r * 1.15; d += (i ? 'L' : 'M') + (k * Math.cos(a)).toFixed(3) + ' ' + (k * Math.sin(a)).toFixed(3); } return d + 'z'; }};
  const glyphPath = (glyph, r) => (GLYPH_PATHS[String(glyph || '').replace(/-open$/, '')] || GLYPH_PATHS.circle)(r);

  // The hatch a Filled layer may draw with (map.pattern, #55's Opposition): the
  // evidence map's own pattern, defined once in each SVG that draws one, under an
  // id of its own (a Board shows several maps; a pattern in a hidden one would not paint).
  let serial = 0;
  const nextId = prefix => prefix + '-' + (++serial);
  function hatchFill(group) {
    const svg = group.ownerSVGElement || group;
    let pattern = svg.querySelector(':scope > defs.mm-defs > pattern.mm-hatch-pattern');
    if (!pattern) {
      let defs = svg.querySelector(':scope > defs.mm-defs');
      if (!defs) { defs = svgEl('defs', {class: 'mm-defs'}); svg.prepend(defs); }
      pattern = svgEl('pattern', {id: nextId('mm-hatch'), class: 'mm-hatch-pattern', width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)'});
      pattern.append(svgEl('rect', {width: 6, height: 6, fill: 'rgba(255,255,255,0.35)'}),
        svgEl('line', {x1: 0, y1: 0, x2: 0, y2: 6, stroke: L.HATCH_INK, 'stroke-width': 2.4}));
      defs.append(pattern);
    }
    return 'url(#' + pattern.id + ')';
  }
  // The hatch keeps its screen size at every zoom (6 frame units at the whole-country
  // view), so zooming into a hatched district never thickens its lines into bars.
  function scaleHatch(svg, zoom) {
    const k = Math.max(1e-6, Number(zoom) || 1), size = (6 / k).toFixed(4);
    for (const pattern of svg.querySelectorAll(':scope > defs.mm-defs > pattern.mm-hatch-pattern')) {
      pattern.setAttribute('width', size); pattern.setAttribute('height', size);
      const rect = pattern.querySelector('rect'), line = pattern.querySelector('line');
      if (rect) { rect.setAttribute('width', size); rect.setAttribute('height', size); }
      if (line) { line.setAttribute('y2', size); line.setAttribute('stroke-width', (2.4 / k).toFixed(4)); }
    }
  }

  const BACKDROP = {
    boundaries: {land: '#f3f0ea', line: '#b9aea2', coast: '#d3dee2', label: '#5a5048', halo: '#f7f5f1', canvas: '#fbfaf7'},
    light: {land: '#f8f7f4', line: '#d5ccc2', coast: '#e4ecef', label: '#7a7069', halo: '#ffffff', canvas: '#ffffff'},
    dark: {land: '#2f3136', line: '#5d5f66', coast: '#1d2430', label: '#c9c4bd', halo: '#24262a', canvas: '#1f2125'},
    none: {land: 'none', line: '#cfc6bb', coast: 'none', label: '#5a5048', halo: '#ffffff', canvas: 'transparent'}};

  /* ------------------------------------------------------------- engine */

  const states = new WeakMap();

  function render(host, model, callbacks) {
    if (!host) return null;
    let state = states.get(host);
    if (!state || !host.contains(state.frame)) state = build(host, callbacks || {});
    state.callbacks = callbacks || state.callbacks || {};
    state.model = model;
    update(state);
    return state.handle;
  }

  function build(host, callbacks) {
    host.replaceChildren();
    const frame = el('div', 'mentat-map');
    frame.tabIndex = -1;
    const stage = el('div', 'mm-stage');
    const baseSvg = svgEl('svg', {class: 'mm-base', viewBox: `0 0 ${FRAME_W} ${FRAME_H}`, preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true'});
    const canvas = el('canvas', 'mm-density');
    canvas.setAttribute('aria-hidden', 'true');
    const marksSvg = svgEl('svg', {class: 'mm-marks', viewBox: `0 0 ${FRAME_W} ${FRAME_H}`, preserveAspectRatio: 'xMidYMid meet',
      role: 'group', tabindex: '0', 'aria-label': 'Map. Arrow keys pan, plus and minus zoom, 0 resets.'});
    const groups = {};
    for (const name of ['coast', 'land', 'washout', 'data', 'county', 'district', 'sld', 'zcta', 'states']) {
      groups[name] = svgEl('g', {class: 'mm-g-' + name}); baseSvg.append(groups[name]);
    }
    for (const name of ['layers', 'borders', 'basenames', 'labels', 'selection']) { groups[name] = svgEl('g', {class: 'mm-g-' + name}); marksSvg.append(groups[name]); }
    stage.append(baseSvg, canvas, marksSvg);
    const streets = el('div', 'mm-streets');
    streets.hidden = true;
    stage.append(streets);
    const overlay = el('div', 'mm-overlay');
    const tooltip = el('div', 'mm-tooltip'); tooltip.hidden = true; tooltip.setAttribute('role', 'status');
    // The legends' band sits under the drawing (the frame's second row), never over it.
    const legends = el('div', 'mm-legends');
    legends.setAttribute('role', 'group'); legends.setAttribute('aria-label', 'Legends');
    const scale = el('div', 'mm-scale');
    const toolbar = el('div', 'mm-toolbar'); toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', 'Map');
    // Its own id: a Board can show several maps, each with its control and its toolbar button pointing at it.
    const control = el('section', 'mm-control'); control.hidden = true; control.setAttribute('aria-label', 'Layer control'); control.id = nextId('mm-control');
    const drop = el('div', 'mm-drop'); drop.hidden = true;
    const banner = el('div', 'mm-banner'); banner.hidden = true; banner.setAttribute('role', 'status');
    const search = el('form', 'mm-search'); search.hidden = true;
    overlay.append(toolbar, search, control, scale, banner, drop, tooltip);
    frame.append(stage, overlay, legends);
    host.append(frame);
    const state = {host, frame, stage, baseSvg, canvas, marksSvg, groups, streets, overlay, tooltip, legends, scale, toolbar,
      control, drop, banner, search, callbacks, view: [0, 0, FRAME_W, FRAME_H], geometry: {}, drawToken: 0, densityTimer: 0,
      controlOpen: null, controlPosition: null, legendsCollapsed: false, renderer: 'albers', streetsHandle: null};
    state.handle = {
      update: model => { state.model = model; return update(state); },
      getView: () => ({x: state.view[0], y: state.view[1], width: state.view[2], height: state.view[3], scale: FRAME_W / state.view[2]}),
      setView: view => { if (view && [view.x, view.y, view.width, view.height].every(Number.isFinite)) { state.view = [view.x, view.y, view.width, view.height]; applyView(state, true); } },
      reset: () => flyTo(state, [0, 0, FRAME_W, FRAME_H]),
      zoom: factor => zoomAbout(state, factor),
      showDropTarget: (on, label) => showDrop(state, on, label),
      openControl: open => toggleControl(state, open),
      layerState: () => ({hidden: new Set(state.model.viewState.hidden), locked: new Set(state.model.viewState.locked)}),
      svg: marksSvg, frame, ready: Promise.resolve(),
      destroy: () => { clearTimeout(state.densityTimer); if (state.streetsHandle) state.streetsHandle.destroy(); host.replaceChildren(); states.delete(host); }};
    marksSvg.__mentatExport = () => exportDrawing(state);
    wire(state);
    states.set(host, state);
    return state;
  }

  function viewState(model) {
    if (!model.viewState) model.viewState = {hidden: new Set(), locked: new Set()};
    if (!(model.viewState.hidden instanceof Set)) model.viewState.hidden = new Set(model.viewState.hidden || []);
    if (!(model.viewState.locked instanceof Set)) model.viewState.locked = new Set(model.viewState.locked || []);
    return model.viewState;
  }
  const shown = (model, layer) => layer.visible && !viewState(model).hidden.has(layer.id);
  const pickable = (model, layer) => layer.selectable && !viewState(model).locked.has(layer.id);

  async function update(state) {
    const model = state.model;
    viewState(model);
    const settings = model.settings || L.settingsOf({});
    const token = ++state.drawToken;
    state.frame.dataset.background = settings.background;
    state.frame.classList.toggle('mm-toolbar-left', model.toolbar === 'left');
    renderToolbar(state);
    renderControl(state);
    renderLegends(state);
    renderSearch(state);
    if (settings.background === 'streets') {
      state.renderer = 'streets';
      state.baseSvg.style.display = 'none'; state.marksSvg.style.display = 'none'; state.canvas.style.display = 'none';
      state.streets.hidden = false;
      try {
        const streets = await import('/map-streets.mjs');
        if (token !== state.drawToken) return state.handle;
        state.streetsHandle = await streets.render(state.streets, model, {
          onBanner: (text, action) => (text ? showBanner(state, text, action) : hideBanner(state)),
          onSelect: state.callbacks.onSelect, onView: view => { state.streetsView = view; renderScale(state); },
          tooltip: (event, layer, item) => showTooltip(state, event, layer, item), hideTooltip: () => hideTooltip(state)}, state.streetsHandle);
        state.frame.dataset.renderer = 'streets';
      } catch (error) {
        showBanner(state, 'The street map could not be drawn here (' + error.message + '). Showing boundaries instead.', null);
        state.renderer = 'albers';
      }
      if (state.renderer === 'streets') { renderScale(state); renderStatus(state); return state.handle; }
    }
    if (state.streetsHandle) { state.streetsHandle.destroy(); state.streetsHandle = null; }
    state.renderer = 'albers';
    state.frame.dataset.renderer = 'albers';
    state.streets.hidden = true;
    state.baseSvg.style.display = ''; state.marksSvg.style.display = ''; state.canvas.style.display = '';
    hideBanner(state);
    try {
      await drawAlbers(state, token);
    } catch (error) {
      if (token === state.drawToken) { showBanner(state, error.message, null); if (root.console) root.console.error(error); }
    }
    return state.handle;
  }

  /* ------------------------------------------------------- the Albers draw */

  async function drawAlbers(state, token) {
    const model = state.model, settings = model.settings || L.settingsOf({});
    const theme = BACKDROP[settings.background] || BACKDROP.boundaries;
    const anchors = await stateAnchors();
    const uspsOf = fips => Object.keys(anchors).find(usps => anchors[usps].fips === fips) || '';
    const stateShapes = await albersFeatures('state');
    if (token !== state.drawToken) return;
    state.geometry.state = stateShapes;
    state.anchors = anchors;
    const g = state.groups;
    for (const name of Object.keys(g)) g[name].replaceChildren();
    state.gatedDrawn = {};
    state.stage.style.backgroundColor = theme.canvas;
    const base = new Set(settings.base || []);
    // The plain base: state shapes. Coastlines are the outline halo left where
    // no neighbouring state covers a thick stroke of every state.
    if (settings.background !== 'none') {
      for (const [usps, shape] of Object.entries(stateShapes)) {
        if (base.has('coastlines')) g.coast.append(svgEl('path', {d: shape.path, class: 'mm-coast', stroke: theme.coast}));
        g.land.append(svgEl('path', {d: shape.path, class: 'mm-land', fill: theme.land, 'data-state': usps}));
      }
    }
    if (settings.washout > 0 && settings.background !== 'none') {
      g.washout.append(svgEl('rect', {x: -FRAME_W, y: -FRAME_H, width: FRAME_W * 3, height: FRAME_H * 3, fill: '#ffffff', 'fill-opacity': settings.washout / 100, class: 'mm-washout'}));
    }
    // The data layer (Map Layers pane): a translucent choropleth under every mark.
    if (model.dataLayer && model.dataLayer.items.length) {
      const shapes = await albersFeatures(model.dataLayer.level, {congress: model.dataLayer.congress, states: statesOf(model.dataLayer, uspsOf)});
      if (token !== state.drawToken) return;
      for (const item of model.dataLayer.items) {
        const shape = shapes[item.id];
        if (!shape || !item.colour) continue;
        g.data.append(svgEl('path', {d: shape.path, fill: item.colour, 'fill-opacity': 0.55, class: 'mm-data-shape'}));
      }
    }
    // Base lines, zoom-gated where Tableau gates them.
    if (base.has('county-borders') || base.has('county-names')) {
      const counties = await albersFeatures('county');
      if (token !== state.drawToken) return;
      state.geometry.county = counties;
      if (base.has('county-borders')) for (const shape of Object.values(counties)) g.county.append(svgEl('path', {d: shape.path, class: 'mm-county', stroke: theme.line}));
    }
    if (base.has('cd')) {
      const districts = await albersFeatures('cd', {congress: settings.congress || '119'});
      if (token !== state.drawToken) return;
      for (const shape of Object.values(districts)) g.district.append(svgEl('path', {d: shape.path, class: 'mm-district-line'}));
    }
    if (base.has('state-borders')) for (const shape of Object.values(stateShapes)) g.borders.append(svgEl('path', {d: shape.path, class: 'mm-state-line', stroke: theme.line}));
    if (base.has('state-names')) {
      for (const [usps, anchor] of Object.entries(anchors)) {
        if (!stateShapes[usps]) continue;
        const name = svgEl('text', {x: anchor.point[0], y: anchor.point[1], class: 'mm-state-name', fill: theme.label, 'data-size': 9});
        name.textContent = usps;
        g.labels.append(name);
      }
    }
    // The layers, bottom card first so the first card draws on top.
    const layers = (model.layers || []).slice().reverse();
    for (const layer of layers) {
      const group = svgEl('g', {class: 'mm-layer mm-mark-' + layer.mark, 'data-layer': layer.id, opacity: layer.opacity});
      g.layers.append(group);
      layer.group = group;
      // A density's group holds its raster (drawDensity), in its card's place.
      if (layer.mark === 'density') continue;
      await drawLayer(state, layer, group, uspsOf, token);
      if (token !== state.drawToken) return;
    }
    state.drawnLayers = model.layers || [];
    applyLayerState(state);
    // The first draw of a set of layers fits them (Add to zoom extent); a
    // redraw of the same set (a toggle, a restyle) keeps the reader's view.
    const signature = (model.layers || []).map(layer => [layer.id, layer.sourceKey, layer.level, layer.zoomExtent].join(':')).join('|');
    if (model.view && [model.view.x, model.view.y, model.view.width, model.view.height].every(Number.isFinite)) {
      state.view = clampView([model.view.x, model.view.y, model.view.width, model.view.height]);
      state.signature = signature;
      model.view = null;
      applyView(state, true);
    } else if (state.signature !== signature) {
      state.signature = signature;
      flyTo(state, extentOf(state));
    } else applyView(state, true);
    renderStatus(state);
  }

  async function drawLayer(state, layer, group, uspsOf, token) {
    const mark = layer.mark;
    const pointLevel = layer.level === 'point';
    let shapes = {};
    if (!pointLevel) shapes = await albersFeatures(layer.level, {congress: layer.congress, states: statesOf(layer, uspsOf)});
    if (token !== state.drawToken) return;
    state.geometry[layer.id] = shapes;
    const anchorOf = item => {
      if (pointLevel || !shapes[item.id]) return item.point ? L.projectFrame(item.point[0], item.point[1], item.statefp) : null;
      const shape = shapes[item.id];
      if (shape.anchor) return shape.anchor;
      if (shape.centroid) return shape.centroid;
      if (layer.level === 'state' && state.anchors[item.id]) return state.anchors[item.id].point;
      const box = shape.bbox;
      return box ? [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2] : null;
    };
    const items = layer.items.slice();
    if (mark === 'circle' || mark === 'shape') items.sort((a, b) => b.radius - a.radius);
    // The card's Border and Halo (Colour popover): a stroke on every mark, and
    // the outline around every label; Automatic keeps the drawing's own.
    if (layer.border) group.dataset.border = layer.border;
    if (layer.halo) group.dataset.halo = layer.halo;
    const stroke = layer.border;
    for (const item of items) {
      const shape = shapes[item.id];
      const attributes = {'data-layer': layer.id, 'data-key': item.id, class: 'mm-mark'};
      if (stroke && mark !== 'line' && mark !== 'text') { attributes.stroke = stroke; attributes['stroke-width'] = stroke === 'none' ? 0 : 1.2; }
      if (mark === 'filled-map' || mark === 'line') {
        if (!shape) continue;
        const node = svgEl('path', Object.assign({d: shape.path}, attributes));
        // A hatched layer marks every place it holds the same way; its amount is in the tooltip.
        if (mark === 'filled-map' && layer.pattern === 'hatch') { node.setAttribute('fill', hatchFill(group)); node.classList.add('mm-fill', 'mm-hatch'); }
        else if (mark === 'filled-map') { node.setAttribute('fill', item.colour || 'none'); node.classList.add('mm-fill'); }
        else { node.setAttribute('fill', 'none'); node.setAttribute('stroke', item.colour || '#57447b'); node.classList.add('mm-line'); }
        group.append(node);
        if (item.label) group.append(labelNode(anchorOf(item), item.label, layer.id, item.id, false, layer.halo));
        continue;
      }
      const at = anchorOf(item);
      if (!at) continue;
      if (mark === 'text') { group.append(labelNode(at, item.label || item.id, layer.id, item.id, true, layer.halo)); continue; }
      const node = mark === 'shape'
        ? svgEl('path', Object.assign({'data-glyph': layer.glyph, 'data-x': at[0], 'data-y': at[1]}, attributes))
        : svgEl('circle', Object.assign({cx: at[0].toFixed(2), cy: at[1].toFixed(2)}, attributes));
      node.dataset.radius = item.radius;
      node.setAttribute('fill', item.colour || '#327a7a');
      node.classList.add(mark === 'shape' ? 'mm-glyph' : 'mm-circle');
      if (/-open$/.test(layer.glyph || '')) { node.setAttribute('fill', 'none'); node.setAttribute('stroke', item.colour || '#327a7a'); }
      group.append(node);
      if (item.label) group.append(labelNode([at[0], at[1]], item.label, layer.id, item.id, false, layer.halo));
    }
  }
  function labelNode(at, text, layerId, key, primary, halo) {
    const node = svgEl('text', {x: at ? at[0] : 0, y: at ? at[1] : 0, class: primary ? 'mm-text-mark mm-mark' : 'mm-label',
      'data-layer': layerId, 'data-key': key, 'data-size': primary ? 11 : 9});
    if (halo) { node.setAttribute('stroke', halo === 'none' ? 'transparent' : halo); node.dataset.halo = '1'; }
    node.textContent = String(text);
    return node;
  }

  /* ------------------------------------------------------------ the view */

  function unitsPerPixel(state) {
    const box = state.marksSvg.getBoundingClientRect();
    if (!box.width || !box.height) return state.view[2] / FRAME_W;
    return Math.max(state.view[2] / box.width, state.view[3] / box.height);
  }
  function applyView(state, settle) {
    const viewBox = state.view.map(value => value.toFixed(3)).join(' ');
    state.baseSvg.setAttribute('viewBox', viewBox);
    state.marksSvg.setAttribute('viewBox', viewBox);
    if (settle) settleView(state);
    else { clearTimeout(state.settleTimer); state.settleTimer = setTimeout(() => settleView(state), 140); }
    renderScale(state);
  }
  // Circle radii, glyphs and label sizes stay in screen pixels, and the zoom
  // gates open and close, once a gesture settles.
  function settleView(state) {
    const unit = unitsPerPixel(state), zoom = FRAME_W / state.view[2];
    for (const node of state.marksSvg.querySelectorAll('circle.mm-circle')) node.setAttribute('r', (Number(node.dataset.radius) * unit).toFixed(4));
    for (const node of state.marksSvg.querySelectorAll('path.mm-glyph')) {
      const r = Number(node.dataset.radius) * unit;
      node.setAttribute('d', glyphPath(node.dataset.glyph, r));
      node.setAttribute('transform', `translate(${node.dataset.x} ${node.dataset.y})`);
    }
    for (const node of state.frame.querySelectorAll('text[data-size]')) node.setAttribute('font-size', (Number(node.dataset.size) * unit).toFixed(4));
    scaleHatch(state.marksSvg, zoom);
    const base = new Set(((state.model && state.model.settings) || {}).base || []);
    const gate = id => { const entry = L.BASE_LAYERS.find(layer => layer.id === id); return !entry || !entry.scale || zoom >= entry.scale; };
    state.groups.county.style.display = base.has('county-borders') && gate('county-borders') ? '' : 'none';
    state.frame.dataset.zoom = zoom.toFixed(2);
    drawGatedBase(state).catch(() => {});
    scheduleDensity(state);
    if (state.callbacks.onView) state.callbacks.onView({x: state.view[0], y: state.view[1], width: state.view[2], height: state.view[3], scale: zoom});
  }
  // The zoom-gated base layers (#54: "shows when zoomed in"): state
  // legislative districts, ZIP boundaries, county names and cities appear
  // once the view is close enough, read for the states in view only.
  const intersectsBox = (a, b) => a && b && a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
  const gateOf = id => { const entry = L.BASE_LAYERS.find(layer => layer.id === id); return entry && entry.scale ? entry.scale : 1; };
  async function drawGatedBase(state) {
    const model = state.model || {};
    if (state.renderer !== 'albers' || !state.geometry.state) return;
    const base = new Set(((model.settings || {}).base) || []);
    const zoom = FRAME_W / state.view[2], unit = unitsPerPixel(state);
    const box = [state.view[0], state.view[1], state.view[0] + state.view[2], state.view[1] + state.view[3]];
    const inView = Object.entries(state.geometry.state).filter(([, shape]) => shape.bbox && intersectsBox(shape.bbox, box)).map(([usps]) => usps);
    const token = state.drawToken;
    const want = id => base.has(id) && zoom >= gateOf(id) && inView.length <= 8;
    state.gatedDrawn = state.gatedDrawn || {};
    for (const [id, level, group, className] of [['sld', 'sldu', 'sld', 'mm-sld-line'], ['zcta', 'zcta', 'zcta', 'mm-zcta-line']]) {
      const node = state.groups[group];
      node.style.display = want(id) ? '' : 'none';
      if (!want(id)) continue;
      for (const usps of inView) {
        const key = id + ':' + usps;
        if (state.gatedDrawn[key]) continue;
        state.gatedDrawn[key] = true;
        const shapes = await albersFeatures(level, {states: [usps]});
        if (token !== state.drawToken) return;
        for (const shape of Object.values(shapes)) node.append(svgEl('path', {d: shape.path, class: className}));
      }
    }
    const names = state.groups.basenames;
    names.replaceChildren();
    const theme = BACKDROP[(model.settings || {}).background] || BACKDROP.boundaries;
    const label = (at, text, size, className) => {
      const node = svgEl('text', {x: at[0], y: at[1], class: className, fill: theme.label, 'font-size': (size * unit).toFixed(4)});
      node.textContent = text;
      names.append(node);
    };
    if (want('county-names') && state.geometry.county) {
      const counties = Object.values(state.geometry.county).filter(shape => shape.anchor && intersectsBox(shape.bbox, box))
        .sort((a, b) => (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) - (a.bbox[2] - a.bbox[0]) * (a.bbox[3] - a.bbox[1])).slice(0, 300);
      for (const shape of counties) label(shape.anchor, String(shape.name || '').replace(/ (County|Parish|Borough|Census Area|Municipality)$/, ''), 8.5, 'mm-county-name');
    }
    if (want('cities')) {
      for (const usps of inView) {
        const places = await albersFeatures('place', {states: [usps]});
        if (token !== state.drawToken) return;
        const big = Object.values(places).filter(shape => shape.anchor && shape.bbox && intersectsBox(shape.bbox, box)
          && (shape.bbox[2] - shape.bbox[0]) / unit >= 22).slice(0, 120);
        for (const shape of big) label(shape.anchor, shape.name || shape.label || '', 9.5, 'mm-city-name');
      }
    }
  }
  function clampView(view) {
    const width = Math.max(FRAME_W / MAX_ZOOM, Math.min(FRAME_W * 1.25, view[2]));
    const height = width * FRAME_H / FRAME_W;
    const x = Math.max(-FRAME_W * 0.25, Math.min(FRAME_W * 1.25 - width, view[0]));
    const y = Math.max(-FRAME_H * 0.25, Math.min(FRAME_H * 1.25 - height, view[1]));
    return [x, y, width, height];
  }
  function zoomAbout(state, factor, center) {
    if (state.renderer === 'streets' && state.streetsHandle) { state.streetsHandle.zoom(factor); return; }
    const c = center || [state.view[0] + state.view[2] / 2, state.view[1] + state.view[3] / 2];
    const width = state.view[2] / factor, ratio = width / state.view[2];
    state.view = clampView([c[0] - (c[0] - state.view[0]) * ratio, c[1] - (c[1] - state.view[1]) * ratio, width, state.view[3] * ratio]);
    applyView(state, false);
  }
  function flyTo(state, box) {
    if (state.renderer === 'streets' && state.streetsHandle) { state.streetsHandle.reset(); return; }
    // A thin margin: the map fills its card (MP4); the legends have their own band below it.
    const pad = 0.025;
    let width = Math.max(FRAME_W / MAX_ZOOM, (box[2] - box[0]) * (1 + pad * 2));
    let height = Math.max(width * FRAME_H / FRAME_W, (box[3] - box[1]) * (1 + pad * 2));
    width = height * FRAME_W / FRAME_H;
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    state.view = clampView([cx - width / 2, cy - height / 2, width, height]);
    applyView(state, true);
  }
  // Add to zoom extent (Tableau's per-layer toggle): the reset box covers
  // only the layers that take part.
  function extentOf(state) {
    let box = null;
    for (const layer of state.drawnLayers || []) {
      if (!layer.zoomExtent || !shown(state.model, layer)) continue;
      const shapes = state.geometry[layer.id] || {};
      for (const item of layer.items) {
        const b = shapes[item.id] && shapes[item.id].bbox
          || (item.point ? (p => p ? [p[0], p[1], p[0], p[1]] : null)(L.projectFrame(item.point[0], item.point[1], item.statefp)) : null);
        if (!b) continue;
        box = box ? [Math.min(box[0], b[0]), Math.min(box[1], b[1]), Math.max(box[2], b[2]), Math.max(box[3], b[3])] : b.slice();
      }
    }
    return box && box[2] - box[0] > 1 ? box : [0, 0, FRAME_W, FRAME_H];
  }

  /* ------------------------------------------------------------- density */

  function scheduleDensity(state) {
    clearTimeout(state.densityTimer);
    state.densityTimer = setTimeout(() => drawDensity(state), 60);
  }
  function drawDensity(state) {
    const canvas = state.canvas, model = state.model;
    const layers = (model.layers || []).filter(layer => layer.mark === 'density' && shown(model, layer));
    const box = state.marksSvg.getBoundingClientRect();
    const ratio = Math.min(2, root.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(box.width)), height = Math.max(1, Math.round(box.height));
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
    const context = canvas.getContext('2d');
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    state.densityImages = [];
    if (!layers.length) { canvas.dataset.layers = '0'; return; }
    // The SVG letterboxes its viewBox (xMidYMid meet); the canvas follows it.
    const scale = Math.min(width / state.view[2], height / state.view[3]);
    const offsetX = (width - state.view[2] * scale) / 2, offsetY = (height - state.view[3] * scale) / 2;
    // Each density is drawn as an <image> inside its own layer's group, so it
    // sits where its card sits in the draw order (a density card above a filled
    // card draws over it), pans with the view until the next recompute, and is
    // exported with the drawing. The canvas stays sized, empty, for measurement.
    for (const url of state.densityUrls || []) URL.revokeObjectURL(url);
    state.densityUrls = [];
    state.densityRasters = new Map();
    for (const layer of layers) {
      const group = state.groups.layers.querySelector(`[data-layer="${cssEscape(layer.id)}"]`);
      if (!group) continue;
      const points = [];
      for (const point of layer.points) {
        const at = L.projectFrame(point.lat, point.lon, point.statefp);
        if (!at) continue;
        points.push({x: (at[0] - state.view[0]) * scale + offsetX, y: (at[1] - state.view[1]) * scale + offsetY, weight: point.weight});
      }
      const cell = 2;
      const density = L.densityGrid(points, width, height, layer.radius, cell);
      // The group carries the layer's opacity; the raster itself is drawn opaque.
      const pixels = L.densityPixels(density, layer.ramp, layer.intensity, 1);
      const small = document.createElement('canvas');
      small.width = density.columns; small.height = density.rows;
      small.getContext('2d').putImageData(new ImageData(pixels, density.columns, density.rows), 0, 0);
      state.densityRasters.set(layer.id, small);
      for (const old of group.querySelectorAll(':scope > image.mm-density-image')) old.remove();
      const image = svgEl('image', {class: 'mm-density-image', 'data-density': 'raster', preserveAspectRatio: 'none', 'pointer-events': 'none',
        x: state.view[0] - offsetX / scale, y: state.view[1] - offsetY / scale,
        width: density.columns * cell / scale, height: density.rows * cell / scale});
      group.prepend(image);
      small.toBlob(blob => {
        if (!blob || !image.isConnected) return;
        const url = URL.createObjectURL(blob);
        state.densityUrls.push(url);
        image.setAttribute('href', url);
      });
      state.densityImages.push(layer.id);
    }
    canvas.dataset.layers = '0';
    canvas.dataset.densityLayers = String(state.densityImages.length);
  }

  /* ---------------------------------------------------- visibility, picks */

  function applyLayerState(state) {
    const model = state.model;
    for (const layer of model.layers || []) {
      const node = state.groups.layers.querySelector(`[data-layer="${cssEscape(layer.id)}"]`);
      if (!node) continue;
      node.style.display = shown(model, layer) ? '' : 'none';
      node.classList.toggle('mm-locked', !pickable(model, layer));
    }
    scheduleDensity(state);
    renderLegends(state);
    renderControl(state);
    if (state.streetsHandle) state.streetsHandle.setLayerState(model.viewState);
    // The status line ("... · 1 hidden · N marks") follows the viewer's eyes too.
    renderStatus(state);
  }
  const cssEscape = value => (root.CSS && root.CSS.escape) ? root.CSS.escape(String(value)) : String(value).replace(/["\\]/g, '\\$&');

  function showTooltip(state, event, layer, item) {
    const tip = state.tooltip;
    tip.replaceChildren();
    const name = placeName(state, layer, item);
    tip.append(el('strong', '', name));
    const lines = [];
    // One side of Spent: that side's money over every candidate here, the other side kept apart.
    if (layer.stance) lines.push((layer.stance === 'oppose' ? 'Opposition ' : 'Support ') + money(item.amount) + ' \u00b7 every candidate, the other side kept apart');
    else lines.push(money(item.amount) + (item.contributors ? ' \u00b7 ' + count(item.contributors) + ' donors' : '') + (item.rows ? ' \u00b7 ' + count(item.rows) + ' rows' : ''));
    if (!layer.stance && (item.support || item.oppose)) lines.push('Support ' + money(item.support) + ' \u00b7 Oppose ' + money(item.oppose));
    lines.push(layer.name + (layer.sourceBadge ? ' \u00b7 ' + layer.sourceBadge : ''));
    for (const line of lines) tip.append(el('span', '', line));
    tip.hidden = false;
    const box = state.frame.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(box.width - 230, event.clientX - box.left + 14)) + 'px';
    tip.style.top = Math.max(8, event.clientY - box.top - 70) + 'px';
  }
  function hideTooltip(state) { state.tooltip.hidden = true; }
  function placeName(state, layer, item) {
    const shapes = state.geometry[layer.id] || {};
    const shape = shapes[item.id];
    if (layer.level === 'state') return (state.anchors && state.anchors[item.id] && state.anchors[item.id].name) || item.id;
    if (layer.level === 'cd') return item.id;
    if (layer.level === 'zcta') return 'ZIP ' + item.id;
    if (layer.level === 'point') return item.zip ? 'ZIP ' + item.zip : 'Point';
    if (L.isHex(layer.level)) return L.levelLabel(layer.level) + ' ' + item.id.split(':').slice(1).join(', ');
    return (shape && (shape.label || shape.name)) || L.levelLabel(layer.level) + ' ' + item.id;
  }

  /* --------------------------------------------------------- the overlays */

  function renderToolbar(state) {
    const model = state.model, options = (model.settings || {}).options || L.DEFAULT_OPTIONS;
    const bar = state.toolbar;
    bar.replaceChildren();
    bar.hidden = model.toolbar === 'none' || options.toolbar === false;
    if (bar.hidden) return;
    if (options.search !== false) bar.append(button('mm-tool', 'Find a place', 'search', () => { state.search.hidden = !state.search.hidden; if (!state.search.hidden) state.search.querySelector('input').focus(); }));
    if (options.panZoom !== false) {
      bar.append(button('mm-tool', 'Zoom in', 'plus', () => zoomAbout(state, 1.5)));
      bar.append(button('mm-tool', 'Zoom out', 'minus', () => zoomAbout(state, 1 / 1.5)));
      bar.append(button('mm-tool', 'Zoom home: fit the layers', 'home', () => flyTo(state, extentOf(state))));
    }
    if (options.layerControl !== false) {
      const toggle = button('mm-tool mm-tool-layers', 'Layer control', 'layers', () => toggleControl(state));
      toggle.setAttribute('aria-pressed', String(isControlOpen(state)));
      toggle.setAttribute('aria-controls', state.control.id);
      bar.append(toggle);
    }
  }
  const isControlOpen = state => state.controlOpen == null ? !!state.model.controlOpen : state.controlOpen;
  function toggleControl(state, open) {
    state.controlOpen = open == null ? !isControlOpen(state) : !!open;
    renderControl(state);
    renderToolbar(state);
  }
  // The Layer control (#49, #55): the same cards, top first, each with its eye
  // (Visibility) and its pointer (Interaction). Dragged by its head; its
  // position resets with the page, as Tableau's does. buildControl draws it
  // for any map (PAC evidence builds its own rows with map-evidence.js).
  //   spec = {rows: [{id, name, mark, visible, selectable, onEye, onPointer, note?, extra?}],
  //           base: [{label, on, toggle}], addData: [{label, onClick}], footer: {label, onClick},
  //           onClose, frame, position, onMove}
  function buildControl(panel, spec) {
    panel.replaceChildren();
    panel.id = panel.id || nextId('mm-control');
    const head = el('div', 'mm-control-head');
    const grip = el('span', 'mm-control-grip'); grip.append(icon('grip')); grip.title = 'Drag to move';
    head.append(grip, el('h3', '', 'Layer control'));
    if (spec.onClose) head.append(button('mm-control-close', 'Close the layer control', 'close', spec.onClose));
    panel.append(head);
    const list = el('ul', 'mm-control-list');
    for (const row of spec.rows || []) {
      const item = el('li', 'mm-control-row');
      item.dataset.layer = row.id;
      const label = el('span', 'mm-control-name', row.name + (row.mark ? ' \u00b7 ' + row.mark : ''));
      label.title = row.note || label.textContent;
      const eye = button('mm-control-eye' + (row.visible ? ' is-on' : ''), (row.visible ? 'Hide ' : 'Show ') + row.name, row.visible ? 'eye' : 'eyeOff', () => row.onEye());
      eye.setAttribute('aria-pressed', String(!!row.visible));
      if (row.eyeTitle) eye.title = row.eyeTitle;
      const pointer = row.onPointer
        ? button('mm-control-pointer' + (row.selectable ? ' is-on' : ''), (row.selectable ? 'Stop selecting in ' : 'Allow selecting in ') + row.name, 'pointer', () => row.onPointer())
        : el('span', 'mm-control-pointer mm-control-none');
      if (row.onPointer) pointer.setAttribute('aria-pressed', String(!!row.selectable));
      item.append(label, eye, el('span', 'mm-control-sep'), pointer);
      if (row.extra) { const more = el('div', 'mm-control-extra'); more.append(row.extra); item.append(more); }
      list.append(item);
    }
    for (const base of spec.base || []) {
      const item = el('li', 'mm-control-row mm-control-base');
      const eye = button('mm-control-eye' + (base.on ? ' is-on' : ''), (base.on ? 'Hide ' : 'Show ') + base.label, base.on ? 'eye' : 'eyeOff', () => base.toggle());
      eye.setAttribute('aria-pressed', String(!!base.on));
      item.append(el('span', 'mm-control-name', base.label + ' \u00b7 base'), eye, el('span', 'mm-control-sep'), el('span', 'mm-control-pointer mm-control-none'));
      list.append(item);
    }
    panel.append(list);
    if (spec.addData && spec.addData.length) {
      const add = el('div', 'mm-control-add');
      add.append(el('span', 'mm-control-add-title', 'Add data'));
      for (const entry of spec.addData) {
        const choice = el('button', 'mm-control-add-button', '+ ' + entry.label);
        choice.type = 'button';
        choice.addEventListener('click', event => entry.onClick(event, choice));
        add.append(choice);
      }
      panel.append(add);
    }
    if (spec.footer) {
      const link = el('button', 'mm-control-edit link-button', spec.footer.label + ' \u2192');
      link.type = 'button';
      link.id = spec.footer.id || '';
      link.addEventListener('click', () => spec.footer.onClick());
      panel.append(link);
    }
    if (spec.position) { panel.style.left = spec.position[0] + 'px'; panel.style.top = spec.position[1] + 'px'; }
    const drag = event => { if (event.target.closest('button')) return; startDrag(panel, spec.frame || panel.parentElement, event, spec.onMove); };
    head.addEventListener('pointerdown', drag);
    return panel;
  }
  function renderControl(state) {
    const model = state.model, options = (model.settings || {}).options || L.DEFAULT_OPTIONS;
    const panel = state.control;
    panel.hidden = options.layerControl === false || !isControlOpen(state);
    if (panel.hidden) return;
    const rows = L.controlRows(model.layers || [], model.viewState).map(row => {
      const layer = (model.layers || []).find(item => item.id === row.id);
      return Object.assign({}, row, {
        eyeTitle: layer.visible ? '' : row.name + ' is hidden on its marks card',
        onEye: () => {
          const hidden = model.viewState.hidden;
          if (!layer.visible) { if (state.callbacks.onAuthorHidden) state.callbacks.onAuthorHidden(layer.id); return; }
          if (hidden.has(row.id)) hidden.delete(row.id); else hidden.add(row.id);
          applyLayerState(state);
          if (state.callbacks.onLayerState) state.callbacks.onLayerState(model.viewState);
        },
        onPointer: () => {
          const locked = model.viewState.locked;
          if (!layer.selectable) return;
          if (locked.has(row.id)) locked.delete(row.id); else locked.add(row.id);
          applyLayerState(state);
          if (state.callbacks.onLayerState) state.callbacks.onLayerState(model.viewState);
        }});
    });
    buildControl(panel, {rows, base: model.controlBase, addData: model.controlAddData, footer: model.controlFooter,
      onClose: () => toggleControl(state, false), frame: state.frame, position: state.controlPosition,
      onMove: position => { state.controlPosition = position; }});
  }
  function startDrag(panel, frameNode, event, onMove) {
    const frame = frameNode.getBoundingClientRect(), box = panel.getBoundingClientRect();
    const dx = event.clientX - box.left, dy = event.clientY - box.top;
    try { panel.setPointerCapture(event.pointerId); } catch (error) { /* still drags */ }
    const move = moveEvent => {
      const left = Math.max(0, Math.min(frame.width - box.width, moveEvent.clientX - frame.left - dx));
      const top = Math.max(0, Math.min(frame.height - box.height, moveEvent.clientY - frame.top - dy));
      panel.style.left = left + 'px'; panel.style.top = top + 'px';
      if (onMove) onMove([left, top]);
    };
    const up = () => { panel.removeEventListener('pointermove', move); panel.removeEventListener('pointerup', up); };
    panel.addEventListener('pointermove', move);
    panel.addEventListener('pointerup', up);
    event.preventDefault();
  }
  // One compact legend card per visible layer (#47, #48, #54), in the band
  // under the drawing: the layer's name on the first line; its ramp (with its
  // range at the two ends) or its swatch on the second, ending with how many
  // rows it could place. The band's first control folds the cards away (view state).
  function renderLegends(state) {
    const model = state.model;
    const host = state.legends;
    host.replaceChildren();
    if (model.legends === false) { host.hidden = true; return; }
    const layers = (model.layers || []).filter(layer => shown(model, layer));
    const cards = [];
    for (const layer of [...(model.dataLayer ? [model.dataLayer] : []), ...layers]) {
      const legend = layer.legend;
      if (!legend || !layer.items.length && layer.mark !== 'density') continue;
      const card = el('div', 'mm-legend mm-legend--' + legend.kind);
      card.dataset.layer = layer.id;
      if (legend.kind === 'ramp' && legend.log) {
        // A ramp whose colours follow the log of the value says so (#53), on its title line,
        // so the ends under it keep their room.
        const head = el('div', 'mm-legend-head');
        head.append(el('span', 'mm-legend-title', legend.title), el('span', 'mm-legend-scale', 'log scale'));
        card.append(head);
      } else card.append(el('span', 'mm-legend-title', legend.title));
      let row;
      if (legend.kind === 'ramp' || legend.kind === 'density') {
        const bar = el('div', 'mm-legend-ramp' + (legend.kind === 'density' ? ' is-density' : ''));
        bar.style.backgroundImage = 'linear-gradient(90deg,' + legend.stops.join(',') + ')';
        const range = legend.kind === 'density' ? ['low', 'high'] : L.legendRange(legend);
        row = el('div', 'mm-legend-values');
        row.append(el('span', 'mm-legend-low', range[0]), bar, el('span', 'mm-legend-high', range[1]));
      } else if (legend.kind === 'pattern') {
        // The hatch itself (#55's legend: "Opposition" beside its hatch).
        row = el('div', 'mm-legend-swatch');
        row.append(el('i', 'mm-legend-hatch'), el('span', '', legend.note || layer.name));
      } else {
        row = el('div', 'mm-legend-swatch');
        const dot = el('i', 'mm-legend-dot');
        dot.style.backgroundColor = legend.swatch || '#327a7a';
        row.append(dot, el('span', '', legend.sizeMeasure ? 'Size: ' + legend.sizeMeasure : layer.name));
      }
      card.append(row);
      let listedText = '';
      if (legend.located) {
        const located = el('span', 'mm-legend-located', legend.located);
        // A layer that counts dollars (one side of Spent) lists dollars, not rows.
        const listed = (legend.unplaced || []).map(entry => (legend.count === 'amount' ? money(entry.amount) : count(entry.rows)) + ' ' + entry.reason).join('; ');
        if (listed) { located.title = 'Listed, not placed: ' + listed; listedText = ' Listed, not placed: ' + listed + '.'; }
        row.append(located);
      }
      card.title = legend.title + (legend.located ? ' · ' + legend.located + '.' : '') + listedText + L.legendScaleNote(legend);
      cards.push(card);
    }
    host.hidden = !cards.length;
    if (!cards.length) return;
    const collapsed = !!state.legendsCollapsed;
    const toggle = el('button', 'mm-legend-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', (collapsed ? 'Show the legends (' : 'Hide the legends (') + cards.length + ')');
    toggle.title = collapsed ? 'Show the legends' : 'Hide the legends';
    toggle.append(icon('chevron'), el('span', 'mm-legend-toggle-text', collapsed ? 'Legends · ' + cards.length : 'Legend'));
    toggle.addEventListener('click', event => { event.stopPropagation(); state.legendsCollapsed = !state.legendsCollapsed; renderLegends(state); });
    host.classList.toggle('is-collapsed', collapsed);
    for (const card of cards) card.hidden = collapsed;
    host.append(toggle, ...cards);
  }

  function renderScale(state) {
    const options = ((state.model || {}).settings || {}).options || L.DEFAULT_OPTIONS;
    const bar = state.scale;
    bar.hidden = options.scale === false;
    if (bar.hidden) return;
    let kmPerPx;
    if (state.renderer === 'streets') {
      const view = state.streetsView;
      if (!view) { bar.hidden = true; return; }
      kmPerPx = view.metersPerPixel / 1000;
    } else {
      kmPerPx = KM_PER_PX * unitsPerPixel(state) * (state.view[2] ? 1 : 1);
    }
    const metric = options.units === 'metric';
    const unitKm = metric ? 1 : 1.609344;
    const target = 90 * kmPerPx / unitKm;
    const nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000].find(step => step >= target * 0.55) || 2000;
    const width = nice * unitKm / kmPerPx;
    bar.replaceChildren();
    const rule = el('span', 'mm-scale-rule');
    rule.style.width = Math.round(width) + 'px';
    bar.append(rule, el('span', 'mm-scale-text', nice + (metric ? ' km' : ' mi') + (state.renderer === 'albers' ? ' \u00b7 lower 48' : '')));
  }

  function renderSearch(state) {
    const form = state.search;
    if (form.dataset.built) return;
    form.dataset.built = '1';
    const input = el('input');
    input.type = 'search'; input.placeholder = 'Find a state, district or ZIP';
    input.setAttribute('aria-label', 'Find a state, district or ZIP');
    const message = el('span', 'mm-search-message');
    form.append(input, message);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      message.textContent = await findPlace(state, input.value) ? '' : 'No place on this map matches \u201c' + input.value + '\u201d.';
    });
  }
  async function findPlace(state, query) {
    const text = String(query || '').trim();
    if (!text) return false;
    const upper = text.toUpperCase();
    const districtMatch = upper.match(/^([A-Z]{2})[\s-]*(\d{1,2}|AL)$/);
    if (/^[A-Z]{2}$/.test(upper) && state.geometry.state && state.geometry.state[upper]) { flyTo(state, state.geometry.state[upper].bbox); return true; }
    const byName = Object.entries(state.anchors || {}).find(([, entry]) => entry.name.toUpperCase() === upper);
    if (byName && state.geometry.state[byName[0]]) { flyTo(state, state.geometry.state[byName[0]].bbox); return true; }
    if (districtMatch) {
      const key = districtMatch[1] + '-' + (/^\d+$/.test(districtMatch[2]) ? districtMatch[2].padStart(2, '0') : 'AL');
      const districts = await albersFeatures('cd', {congress: ((state.model || {}).settings || {}).congress || '119'});
      if (districts[key]) { flyTo(state, districts[key].bbox); return true; }
    }
    if (/^\d{5}$/.test(text)) {
      const lookup = L.createLookup(fetchJson);
      await lookup.ready([text]);
      const row = lookup.zcta(text);
      const at = row ? L.projectFrame(row.lat, row.lon, row.state) : null;
      if (at) { flyTo(state, [at[0] - 6, at[1] - 4, at[0] + 6, at[1] + 4]); return true; }
    }
    const county = state.geometry.county && Object.values(state.geometry.county).find(shape => String(shape.name || '').toUpperCase() === upper);
    if (county) { flyTo(state, county.bbox); return true; }
    return false;
  }

  function renderStatus(state) {
    const model = state.model;
    const marks = (model.layers || []).filter(layer => shown(model, layer)).reduce((sum, layer) => sum + layer.items.length, 0);
    state.frame.dataset.marks = String(marks);
    state.frame.dataset.status = L.statusLine((model.layers || []).map(layer => Object.assign({}, layer, {visible: shown(model, layer)})), marks);
    if (state.callbacks.onStatus) state.callbacks.onStatus(state.frame.dataset.status);
  }

  function showBanner(state, text, action) {
    const banner = state.banner;
    banner.replaceChildren(el('span', '', text));
    if (action) {
      const go = el('button', 'secondary mm-banner-action', action.label);
      go.type = 'button';
      go.addEventListener('click', () => action.run());
      banner.append(go);
    }
    banner.hidden = false;
  }
  function hideBanner(state) { state.banner.hidden = true; }
  function showDrop(state, on, label) {
    state.drop.hidden = !on;
    state.drop.textContent = label || 'Add a marks layer';
  }

  /* --------------------------------------------------------------- wiring */

  function wire(state) {
    const svg = state.marksSvg;
    let pointer = null;
    const point = event => {
      const matrix = svg.getScreenCTM();
      if (!matrix) return [state.view[0] + state.view[2] / 2, state.view[1] + state.view[3] / 2];
      const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY;
      const m = p.matrixTransform(matrix.inverse());
      return [m.x, m.y];
    };
    const allowPan = () => (((state.model || {}).settings || {}).options || {}).panZoom !== false;
    svg.addEventListener('wheel', event => {
      if (!allowPan() || !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      zoomAbout(state, event.deltaY < 0 ? 1.18 : 1 / 1.18, point(event));
    }, {passive: false});
    svg.addEventListener('dblclick', event => { if (allowPan()) zoomAbout(state, event.shiftKey ? 1 / 1.6 : 1.6, point(event)); });
    svg.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !allowPan()) return;
      const matrix = svg.getScreenCTM();
      pointer = {id: event.pointerId, x: event.clientX, y: event.clientY, view: state.view.slice(), unit: matrix && matrix.a ? 1 / matrix.a : 1, moved: false};
    });
    svg.addEventListener('pointermove', event => {
      if (pointer && pointer.id === event.pointerId) {
        const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
        if (!pointer.moved && Math.hypot(dx, dy) < 4) return;
        if (!pointer.moved) { pointer.moved = true; try { svg.setPointerCapture(event.pointerId); } catch (error) { /* still pans */ } svg.classList.add('mm-panning'); hideTooltip(state); }
        state.view = clampView([pointer.view[0] - dx * pointer.unit, pointer.view[1] - dy * pointer.unit, pointer.view[2], pointer.view[3]]);
        applyView(state, false);
        return;
      }
      const target = event.target.closest ? event.target.closest('.mm-mark[data-layer]') : null;
      if (!target) { hideTooltip(state); return; }
      const layer = (state.model.layers || []).find(item => item.id === target.dataset.layer);
      const item = layer && layer.items.find(entry => String(entry.id) === target.dataset.key);
      if (layer && item) showTooltip(state, event, layer, item);
    });
    const release = () => { if (!pointer) return; const moved = pointer.moved; pointer = null; svg.classList.remove('mm-panning'); if (moved) { svg.dataset.dragged = '1'; setTimeout(() => delete svg.dataset.dragged, 0); } };
    svg.addEventListener('pointerup', release);
    svg.addEventListener('pointercancel', release);
    svg.addEventListener('pointerleave', () => hideTooltip(state));
    svg.addEventListener('click', event => {
      if (svg.dataset.dragged) return;
      const target = event.target.closest ? event.target.closest('.mm-mark[data-layer]') : null;
      if (!target) return;
      const layer = (state.model.layers || []).find(item => item.id === target.dataset.layer);
      if (!layer || !pickable(state.model, layer)) return;
      state.groups.selection.replaceChildren();
      if (target.tagName.toLowerCase() === 'path' && target.getAttribute('d')) state.groups.selection.append(svgEl('path', {d: target.getAttribute('d'), class: 'mm-selected', transform: target.getAttribute('transform')}));
      const item = layer.items.find(entry => String(entry.id) === target.dataset.key);
      if (state.callbacks.onSelect) state.callbacks.onSelect(layer.id, target.dataset.key, item);
    });
    svg.addEventListener('keydown', event => {
      if (!allowPan()) return;
      const step = state.view[2] * 0.1;
      const moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]};
      if (moves[event.key]) { state.view = clampView([state.view[0] + moves[event.key][0], state.view[1] + moves[event.key][1], state.view[2], state.view[3]]); applyView(state, true); }
      else if (event.key === '+' || event.key === '=') zoomAbout(state, 1.4);
      else if (event.key === '-' || event.key === '_') zoomAbout(state, 1 / 1.4);
      else if (event.key === '0') flyTo(state, extentOf(state));
      else return;
      event.preventDefault();
    });
    if (typeof ResizeObserver === 'function') {
      state.resize = new ResizeObserver(() => { if (state.renderer === 'albers') settleView(state); });
      state.resize.observe(state.stage);
    }
  }

  // One compiled layer drawn into someone else's SVG group in the same
  // 975x610 frame (PAC evidence's "Add data"): radii and label sizes at
  // scale 1, for the caller to rescale with its own zoom (data-radius).
  async function drawOverlay(group, layer) {
    const anchors = await stateAnchors();
    const uspsOf = fips => Object.keys(anchors).find(usps => anchors[usps].fips === fips) || '';
    const scratch = {geometry: {}, anchors, drawToken: 1};
    group.replaceChildren();
    group.setAttribute('opacity', String(layer.opacity));
    group.dataset.layer = layer.id;
    await drawLayer(scratch, layer, group, uspsOf, 1);
    for (const node of group.querySelectorAll('circle.mm-circle')) node.setAttribute('r', node.dataset.radius);
    for (const node of group.querySelectorAll('path.mm-glyph')) {
      node.setAttribute('d', glyphPath(node.dataset.glyph, Number(node.dataset.radius)));
      node.setAttribute('transform', `translate(${node.dataset.x} ${node.dataset.y})`);
    }
    for (const node of group.querySelectorAll('text[data-size]')) node.setAttribute('font-size', node.dataset.size);
    return {group, geometry: scratch.geometry[layer.id] || {}};
  }

  /* --------------------------------------------------------------- export */

  // Export (chart-export.js compose): one SVG with the base, the density
  // raster embedded as an image where the canvas draws it, the marks, and
  // each visible layer's legend, all at the current view.
  function exportDrawing(state) {
    const view = state.view;
    const drawing = svgEl('svg', {xmlns: NS, viewBox: view.join(' '), width: 1000});
    const background = BACKDROP[(state.model.settings || {}).background] || BACKDROP.boundaries;
    drawing.append(svgEl('rect', {x: view[0], y: view[1], width: view[2], height: view[3], fill: background.canvas === 'transparent' ? '#ffffff' : background.canvas}));
    for (const child of state.baseSvg.childNodes) drawing.append(child.cloneNode(true));
    if (state.canvas.width > 1 && Number(state.canvas.dataset.layers || 0) > 0) {
      const box = state.marksSvg.getBoundingClientRect();
      const scale = Math.min(box.width / view[2], box.height / view[3]);
      const offsetX = (box.width - view[2] * scale) / 2, offsetY = (box.height - view[3] * scale) / 2;
      drawing.append(svgEl('image', {href: state.canvas.toDataURL('image/png'), x: view[0] - offsetX / scale, y: view[1] - offsetY / scale,
        width: box.width / scale, height: box.height / scale, preserveAspectRatio: 'none', 'data-density': 'raster'}));
    }
    for (const child of state.marksSvg.childNodes) drawing.append(child.cloneNode(true));
    // A density's page raster is a blob: URL, which means nothing outside this
    // page; the exported drawing carries the same pixels as a PNG data URL.
    for (const image of drawing.querySelectorAll('image.mm-density-image')) {
      const holder = image.closest('[data-layer]');
      const raster = holder && state.densityRasters && state.densityRasters.get(holder.getAttribute('data-layer'));
      if (raster) image.setAttribute('href', raster.toDataURL('image/png')); else image.remove();
    }
    for (const hidden of drawing.querySelectorAll('[style*="display: none"]')) hidden.remove();
    const unit = view[2] / 1000;
    let y = view[1] + view[3] - 8 * unit;
    for (const card of [...state.legends.querySelectorAll(':scope > .mm-legend')].reverse()) {
      const title = card.querySelector('.mm-legend-title');
      const values = [...card.querySelectorAll('.mm-legend-low, .mm-legend-high')].map(node => node.textContent);
      const located = card.querySelector('.mm-legend-located'), scale = card.querySelector('.mm-legend-scale');
      const group = svgEl('g', {class: 'mm-export-legend'});
      const text = (value, x, dy, weight) => { const node = svgEl('text', {x, y: y + dy, 'font-size': 11 * unit, fill: '#2c2a28', 'font-weight': weight || 400, 'font-family': 'Arial, sans-serif'}); node.textContent = value; group.append(node); };
      text((title ? title.textContent : '') + (values.length ? '  ' + values.join(' \u2013 ') : '') + (scale ? ' (' + scale.textContent + ')' : '')
        + (located ? '  \u00b7 ' + located.textContent : ''), view[0] + 10 * unit, 0, 600);
      drawing.append(group);
      y -= 16 * unit;
    }
    return drawing;
  }

  const api = {render, buildControl, drawOverlay, scaleHatch, albersFeatures, geoIndex, fetchJson, FRAME_W, FRAME_H, EXTRACT_BOUNDS, glyphPath, icon,
    lookup: () => L.createLookup(fetchJson)};
  root.MentatMapEngine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
