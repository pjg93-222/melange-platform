/* PAC evidence's map as layers (lane P, boards #48 and #55).

   The evidence map keeps its approved frame (boards 6 and 7): Raised | Spent,
   the cycle, the LAYER strip, the ranked list, and every rule lane B and C put
   into its numbers (F6, F7, F11, F12). What this module adds is the layer
   model on top of it:
     - its fixed layers become preset cards: the fill at the chosen LAYER, the
       donor circles, a donor-amount heat (a density drawn over the fill and
       under the circles), the Opposition on Spent - a marks card of its own
       (map-layers.js evidenceLayers: the pane's Spent source with stance
       'oppose', a Filled layer drawn with the hatch), compiled from this map's
       payload and drawn by map-engine.js exactly as Chart Studio draws it
       (support and oppose kept apart, every candidate summed) - and the state
       borders;
     - the Layers button (#48) opens the Layer control (#55): an eye and a
       selection pointer per layer, the heat's Intensity and Radius, and Add
       data - another industry or another committee drawn on the same map.
       The button is the last of #55's view toolbar, a column on the map's
       left (search, zoom in, zoom out, home, layers), and the control opens
       beside it; the legend card under the map folds away from its own toggle;
     - "Open in Chart Studio" (#55) hands those exact cards to Chart Studio as
       a new map sheet (map-studio.js openHandoff).
   Every toggle here is view state (D-039: no storage key); nothing is saved
   until Chart Studio saves the sheet. evidence.js calls decorate() after each
   evidence-map.js render and routes its two buttons here. */
(function (root) {
  'use strict';
  if (typeof document === 'undefined') return;
  const L = root.MentatMapLayers, E = root.MentatMapEngine;
  const NS = 'http://www.w3.org/2000/svg';
  const panes = new Map();   // pane key -> view state
  let token = '', industryList = null, snapshotList = null;

  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const svgEl = (tag, attributes) => { const node = document.createElementNS(NS, tag); for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value)); return node; };
  const money = value => L.fullMoney(value);

  // The Opposition starts unselectable ('hatch' in locked): its hatch lies over the
  // fill, whose places this map selects; its pointer in the Layer control turns it on.
  function stateFor(key) {
    if (!panes.has(key)) panes.set(key, {hidden: new Set(), locked: new Set(['hatch']), heat: {on: false, radius: 18, intensity: L.DEFAULT_INTENSITY},
      extra: [], compiled: new Map(), open: false, position: null, bordersOff: false, oppositionTicket: 0, legendFolded: false});
    return panes.get(key);
  }
  const keyOf = pane => pane && (pane.key || [pane.source, pane.id, pane.committee].join(':'));

  /* ------------------------------------------------------ the preset rows */

  const LAYER_WORD = {region: 'region', state: 'state', district: 'district', sld: 'state district', zip: 'ZIP code'};
  function presetRows(mapState) {
    const raised = mapState.direction === 'raised';
    const rows = [];
    if (mapState.layer !== 'zip') rows.push({id: 'fill', name: (raised ? 'Receipts' : 'Spending') + ' by ' + (LAYER_WORD[mapState.layer] || 'place'), mark: 'filled'});
    if (raised && mapState.layer !== 'sld') rows.push({id: 'circles', name: 'Donor ZIPs', mark: 'circles'});
    if (raised) rows.push({id: 'heat', name: 'Heat: donor amount', mark: 'density', density: true});
    // The Opposition row is its marks card (map-layers.js evidenceLayers), named as the card is.
    if (!raised && L.OPPOSITION_LAYERS.includes(mapState.layer)) rows.push({id: 'hatch', name: 'Opposition', mark: 'hatch', card: true});
    // Listed top first, as drawn (and as Chart Studio lists the same layers as cards).
    const rank = {circles: 0, heat: 1, hatch: 2, fill: 3};
    return rows.sort((a, b) => rank[a.id] - rank[b.id]);
  }
  const isOn = (view, id) => id === 'heat' ? view.heat.on && !view.hidden.has(id) : !view.hidden.has(id);

  /* ----------------------------------------------------------- decorate */

  // After every evidence-map.js render: overlays, visibility and the control.
  function decorate(host, pane, mapState, drawing) {
    if (!host || !drawing || typeof drawing.internals !== 'function') return;
    const inner = drawing.internals(host);
    if (!inner || !inner.svg) return;
    const view = stateFor(keyOf(pane));
    view.pane = pane; view.mapState = mapState; view.drawing = drawing; view.host = host;
    placeToolbar(host);
    const layers = inner.layers;
    const after = (group, name) => {
      let node = inner.viewport.querySelector(':scope > g.' + name);
      if (!node) { node = svgEl('g', {class: name}); group.after(node); }
      return node;
    };
    const extraGroup = after(layers.areas, 'ev-extra');
    const heatGroup = after(extraGroup, 'ev-heat');
    const hatchGroup = after(heatGroup, 'ev-hatch-layer');
    drawOpposition(hatchGroup, inner, mapState, view).catch(error => { hatchGroup.dataset.error = error.message; });
    drawExtras(extraGroup, inner, mapState, view).catch(() => {});
    applyVisibility(inner, view);
    inner.onSettle = () => {
      drawHeat(heatGroup, inner, view); rescale(inner); addHeatLegend(inner, view); addOppositionLegend(inner, view); foldLegend(inner, view);
      placeToolbar(view.host);   // its zoom buttons follow the toolbar row's after a zoom
    };
    drawHeat(heatGroup, inner, view);
    addHeatLegend(inner, view);
    addOppositionLegend(inner, view);
    foldLegend(inner, view);
    renderControl(inner, pane, mapState, view);
  }

  // The legend card under the map (MP1): its first control folds its groups away and
  // back (view state, kept per pane while the page is open). The card is remade on every
  // repaint (evidence-map.js updateHeat), so the toggle is put back each time.
  function foldLegend(inner, view) {
    const card = inner.plot.querySelector('.map-legend-card');
    if (!card) return;
    let toggle = card.querySelector(':scope > .ev-legend-toggle');
    if (!toggle) {
      toggle = el('button', 'ev-legend-toggle');
      toggle.type = 'button';
      toggle.append(E.icon('chevron'), el('span', 'ev-legend-toggle-text', 'Legend'));
      toggle.addEventListener('click', event => { event.stopPropagation(); view.legendFolded = !view.legendFolded; foldLegend(inner, view); });
      card.prepend(toggle);
    }
    const folded = !!view.legendFolded;
    card.classList.toggle('is-folded', folded);
    toggle.setAttribute('aria-expanded', String(!folded));
    toggle.title = folded ? 'Show the legend' : 'Hide the legend';
    toggle.setAttribute('aria-label', folded ? 'Show the legend' : 'Hide the legend');
    for (const group of card.querySelectorAll(':scope > .map-legend-group')) group.hidden = folded;
  }

  function applyVisibility(inner, view) {
    const svg = inner.svg, layers = inner.layers;
    svg.classList.toggle('ev-fill-off', view.hidden.has('fill'));
    layers.areas.classList.toggle('ev-locked', view.locked.has('fill'));
    for (const name of ['circles', 'badges']) if (layers[name]) layers[name].style.display = view.hidden.has('circles') ? 'none' : '';
    if (layers.circles) layers.circles.classList.toggle('ev-locked', view.locked.has('circles'));
    if (layers.outlines) layers.outlines.style.display = view.bordersOff ? 'none' : '';
    const hatch = svg.querySelector('g.ev-hatch-layer');
    if (hatch) {
      hatch.style.display = view.hidden.has('hatch') ? 'none' : '';
      hatch.classList.toggle('ev-locked', view.locked.has('hatch'));
    }
    for (const node of svg.querySelectorAll('g.ev-extra > g[data-layer]')) {
      node.style.display = view.hidden.has(node.dataset.layer) ? 'none' : '';
      node.classList.toggle('ev-locked', view.locked.has(node.dataset.layer));
    }
  }

  // #55's Opposition, drawn from its own marks card: the preset "Open in Chart
  // Studio" hands over (map-layers.js evidenceLayers), compiled from the payload
  // this map already holds and drawn by map-engine.js - so the hatch here and the
  // hatch Chart Studio draws are one layer. Support and oppose are kept apart: the
  // fill is the amount the server placed; each hatched place carries the server's
  // oppose sum over every candidate there (never only the largest, never netted).
  // Kinds the reader switched off are listed in the card's legend, not drawn. The
  // card has one level: on District a Senate race is listed (placed by its state),
  // as the district fill leaves it out; on State and Region it is hatched.
  async function drawOpposition(group, inner, mapState, view) {
    const body = mapState.body;
    const entry = mapState.direction === 'spent' && body && Array.isArray(body.kinds)
      ? L.evidenceLayers(view.pane || {}, 'spent', mapState.cycle, null, {layer: mapState.layer}).find(item => item.key === 'hatch') : null;
    if (!entry) { group.replaceChildren(); delete group.dataset.key; view.compiled.delete('hatch'); return; }
    const card = entry.card;
    const off = mapState.off instanceof Set ? mapState.off : undefined;
    const key = [keyOf(view.pane), mapState.cycle, card.geography, body.vintage || '', off ? [...off].sort().join(',') : '-', body.located_total].join('|');
    if (group.dataset.key === key && view.compiled.has('hatch')) return;
    group.dataset.key = key;
    const ticket = ++view.oppositionTicket;
    const congress = String(body.vintage || '119');
    const compiled = L.compileLayer(card, L.aggregate(L.rowsFromEvidence(body, card.geography, {off, stance: 'oppose'}), card.geography, null, {congress}),
      {fields: [], congress});
    // Drawn aside and swapped in whole, so two quick redraws never leave both drawings.
    const scratch = svgEl('g', {class: 'ev-hatch-scratch'});
    scratch.style.display = 'none';
    group.after(scratch);
    try {
      await E.drawOverlay(scratch, compiled);
      if (ticket !== view.oppositionTicket) return;
      for (const mark of scratch.querySelectorAll('.mm-mark')) {
        const item = compiled.items.find(row => String(row.id) === mark.dataset.key);
        if (!item) continue;
        const title = svgEl('title', {});
        title.textContent = mark.dataset.key + '\nOpposition ' + money(item.amount) + ' (every candidate, support kept apart)';
        mark.append(title);
      }
      group.replaceChildren(...scratch.childNodes);
      group.setAttribute('opacity', String(compiled.opacity));
      group.dataset.layer = compiled.id;
      view.compiled.set('hatch', compiled);
    } finally { scratch.remove(); }
    applyVisibility(inner, view);
    addOppositionLegend(inner, view);
    if (view.open) renderControl(inner, view.pane, mapState, view);
  }
  // The Opposition swatch in the map's legend (#55: "Opposition" beside its hatch), with
  // how much of it this level places; the rest is named in its title, never dropped.
  function addOppositionLegend(inner, view) {
    const card = inner.plot.querySelector('.map-legend-card');
    if (!card) return;
    const existing = card.querySelector('.ev-oppose-legend');
    if (existing) existing.remove();
    const compiled = view.compiled.get('hatch');
    if (!compiled || view.hidden.has('hatch') || !view.mapState || view.mapState.direction !== 'spent') return;
    const group = el('div', 'map-legend-group ev-oppose-legend');
    group.hidden = !!view.legendFolded;
    group.append(el('span', 'map-legend-title', 'Opposition'));
    const row = el('div', 'ev-oppose-row');
    row.append(el('i', 'mm-legend-hatch'), el('span', 'map-legend-values', compiled.legend.located));
    group.append(row);
    const listed = (compiled.legend.unplaced || []).map(entry => money(entry.amount) + ' ' + entry.reason).join('; ');
    group.title = 'Spending that opposed a candidate, summed over every candidate at each place; support is kept apart.'
      + (listed ? ' Listed, not placed: ' + listed + '.' : '');
    card.append(group);
  }

  // The donor-amount heat: a kernel density of the placed ZIPs, weighted by
  // amount, drawn over the fill and under the circles. It is a raster of the
  // visible area, placed in the map's own coordinates, redrawn when the view
  // settles; low density stays transparent.
  function drawHeat(group, inner, view) {
    const old = group.querySelector('image');
    if (!isOn(view, 'heat') || view.mapState.direction !== 'raised') { group.replaceChildren(); if (old && old.dataset.url) URL.revokeObjectURL(old.dataset.url); return; }
    const box = inner.visibleBox(), rect = inner.svg.getBoundingClientRect();
    const k = Math.min(rect.width / inner.width, rect.height / inner.height);
    const width = Math.max(1, Math.round(inner.width * k)), height = Math.max(1, Math.round(inner.height * k));
    const sx = width / (box[2] - box[0]), sy = height / (box[3] - box[1]);
    const points = (inner.points || []).map(point => ({x: (point.x - box[0]) * sx, y: (point.y - box[1]) * sy, weight: Math.abs(point.amount || 0)}));
    const density = L.densityGrid(points, width, height, view.heat.radius, 2);
    const pixels = L.densityPixels(density, L.rampStops('heat'), view.heat.intensity, 0.9);
    const small = document.createElement('canvas');
    small.width = density.columns; small.height = density.rows;
    small.getContext('2d').putImageData(new ImageData(pixels, density.columns, density.rows), 0, 0);
    small.toBlob(blob => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const image = svgEl('image', {href: url, x: box[0], y: box[1], width: box[2] - box[0], height: box[3] - box[1],
        preserveAspectRatio: 'none', class: 'ev-heat-image'});
      image.dataset.url = url;
      const previous = group.querySelector('image');
      group.replaceChildren(image);
      if (previous && previous.dataset.url) setTimeout(() => URL.revokeObjectURL(previous.dataset.url), 1000);
    });
  }
  function addHeatLegend(inner, view) {
    const card = inner.plot.querySelector('.map-legend-card');
    if (!card) return;
    const existing = card.querySelector('.ev-heat-legend');
    if (existing) existing.remove();
    if (!isOn(view, 'heat') || view.mapState.direction !== 'raised') return;
    const group = el('div', 'map-legend-group ev-heat-legend');
    group.hidden = !!view.legendFolded;
    group.append(el('span', 'map-legend-title', 'Heat: donor amount'));
    const ramp = el('div', 'ev-heat-ramp');
    ramp.style.backgroundImage = 'linear-gradient(90deg,' + L.rampStops('heat').join(',') + ')';
    const values = el('span', 'map-legend-values');
    values.append(el('span', '', 'low'), document.createTextNode(' – '), el('span', '', 'high'));
    group.append(ramp, values);
    card.append(group);
  }
  function rescale(inner) {
    const scale = inner.scale() || 1;
    for (const node of inner.svg.querySelectorAll('g.ev-extra circle.mm-circle')) node.setAttribute('r', (Number(node.dataset.radius) / scale).toFixed(4));
    for (const node of inner.svg.querySelectorAll('g.ev-extra text[data-size]')) node.setAttribute('font-size', (Number(node.dataset.size) / scale).toFixed(4));
    // The Opposition's hatch keeps its screen size as the reader zooms (map-engine.js scaleHatch).
    if (E.scaleHatch) E.scaleHatch(inner.svg, scale);
  }

  /* ---------------------------------------------------------- Add data */

  async function sessionToken() {
    if (token) return token;
    const body = await (await fetch('/api/contributions/session')).json();
    token = body.token || '';
    industryList = (body.industries || []).filter(entry => entry.available !== false);
    return token;
  }
  async function industryRows(source, cycle) {
    const contributions = root.MentatStudioContributions;
    await sessionToken();
    const query = {dataset: source.industry, industry: source.industry, basis: source.basis || 'receipt', dimensions: ['zip5', 'state'],
      measures: ['amount', 'records', 'contributors'], filters: cycle ? [{field: 'cycle', op: 'eq', value: L.cycleLabel(cycle).replace('–', '-')}] : [],
      aggregate_filters: [], sort: [{field: 'amount', direction: 'desc'}], limit: 100000, offset: 0};
    const call = async body => {
      const response = await fetch('/api/contributions/query', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Mentat-Token': token}, body: JSON.stringify(body)});
      const result = await response.json();
      if (!response.ok || result.error) throw new Error(result.error || 'Contribution data unavailable.');
      return result;
    };
    const result = contributions && contributions.pollPending ? await contributions.pollPending(call, {body: query, path: 'query'}) : await call(query);
    return L.rowsFromContributions(result);
  }
  async function extraRows(card, cycle) {
    if (card.source.kind === 'industry') return {rows: await industryRows(card.source, cycle), congress: null};
    const params = new URLSearchParams({source: card.source.source, id: card.source.id, committee: card.source.committee, cycle: card.source.cycle, direction: card.source.direction});
    const response = await fetch('/api/evidence-geo?' + params);
    const body = await response.json();
    if (!response.ok || body.error) throw new Error(body.error || 'That committee’s map could not be read.');
    return {rows: L.rowsFromEvidence(body, card.geography), congress: String(body.vintage || '')};
  }
  async function drawExtras(group, inner, mapState, view) {
    const wanted = new Set(view.extra.map(card => card.id));
    for (const node of [...group.children]) if (!wanted.has(node.dataset.layer)) node.remove();
    const lookup = E.lookup ? (view.lookup || (view.lookup = E.lookup())) : null;
    for (const card of view.extra) {
      const key = card.id + '|' + mapState.cycle + '|' + card.geography;
      let node = group.querySelector(':scope > g[data-layer="' + card.id + '"]');
      if (node && node.dataset.key === key) continue;
      if (!node) { node = svgEl('g', {'data-layer': card.id}); group.append(node); }
      node.dataset.key = key;
      try {
        const data = await extraRows(card, mapState.cycle);
        if (lookup) await lookup.ready(data.rows.map(row => row.zip).filter(Boolean));
        const congress = data.congress || String((mapState.body && mapState.body.vintage) || '119');
        const compiled = L.compileLayer(card, L.aggregate(data.rows, card.geography, lookup, {congress}), {fields: [], congress, slot: 1 + view.extra.indexOf(card)});
        view.compiled.set(card.id, compiled);
        await E.drawOverlay(node, compiled);
        for (const mark of node.querySelectorAll('.mm-mark')) {
          const item = compiled.items.find(entry => String(entry.id) === mark.dataset.key);
          if (!item) continue;
          const title = svgEl('title', {});
          title.textContent = (card.name || L.sourceLabel(card.source)) + '\n' + mark.dataset.key + ' · ' + money(item.amount);
          mark.append(title);
        }
      } catch (error) {
        node.dataset.error = error.message;
      }
    }
    rescale(inner);
    applyVisibility(inner, view);
    if (view.open) renderControl(inner, view.pane, mapState, view);
  }
  function addExtra(view, source, mapState) {
    const level = L.EVIDENCE_LEVEL[mapState.layer] || 'state';
    const geography = source.kind === 'pac' && source.direction === 'spent' && level === 'zcta' ? 'state' : level;
    const id = 'extra-' + (view.extra.length + 1) + '-' + Date.now().toString(36);
    const card = L.newLayerCard({id, name: L.sourceLabel(source) + ' · ' + (geography === 'zcta' ? 'circles' : 'filled'), source, geography,
      type: geography === 'zcta' ? 'circle' : 'filled-map', opacity: 70,
      encodings: geography === 'zcta' ? {size: {field: 'amount'}} : {color: {field: 'amount'}}});
    view.extra.push(card);
    redecorate(view);
  }
  function redecorate(view) {
    if (view.host && view.drawing) decorate(view.host, view.pane, view.mapState, view.drawing);
  }

  function menu(anchor, items, label) {
    for (const node of document.querySelectorAll('.studio-layer-menu')) node.remove();
    const box = el('div', 'studio-layer-menu studio-layer-source-menu');
    box.setAttribute('role', 'menu'); box.setAttribute('aria-label', label);
    for (const entry of items) {
      if (entry.group) { box.append(el('div', 'studio-layer-group', entry.group)); continue; }
      const item = el('button', '', entry[0]);
      item.type = 'button'; item.setAttribute('role', 'menuitem');
      if (!entry[1]) item.disabled = true; else item.addEventListener('click', () => { box.remove(); entry[1](); });
      box.append(item);
    }
    document.body.append(box);
    const rect = anchor.getBoundingClientRect();
    box.style.left = Math.max(8, rect.left + window.scrollX) + 'px';
    box.style.top = (rect.bottom + window.scrollY + 4) + 'px';
    const outside = event => { if (!box.contains(event.target)) { box.remove(); document.removeEventListener('mousedown', outside); } };
    setTimeout(() => document.addEventListener('mousedown', outside), 0);
  }
  async function industryMenu(anchor, view) {
    try { await sessionToken(); } catch (error) { industryList = []; }
    const items = [{group: 'Another industry'}];
    for (const entry of industryList || []) items.push([entry.industry, () => addExtra(view, {kind: 'industry', industry: entry.industry, basis: 'receipt'}, view.mapState)]);
    if (!(industryList || []).length) items.push(['No verified industry on this console', null]);
    menu(anchor, items, 'Another industry');
  }
  async function committeeMenu(anchor, view) {
    if (!snapshotList) snapshotList = fetch('/api/evidence').then(response => response.json()).then(body => body.snapshots || []).catch(() => []);
    const items = [{group: 'Another committee'}];
    const cycle = String(view.mapState.cycle || '');
    for (const snapshot of await snapshotList) {
      let body = null;
      try { body = await (await fetch('/api/evidence?' + new URLSearchParams({id: snapshot.id}))).json(); } catch (error) { body = null; }
      for (const row of (body && body.committee_finances) || []) {
        if (cycle && String(row.cycle) !== cycle) continue;
        if (view.pane && row.committee_id === view.pane.committee && snapshot.id === view.pane.id) continue;
        const source = {kind: 'pac', source: 'snapshot', id: snapshot.id, committee: row.committee_id, cycle: String(row.cycle),
          direction: view.mapState.direction === 'raised' ? 'raised' : 'spent', name: String(row.committee_name || row.committee_id).slice(0, 120)};
        items.push([source.name + ' · ' + (snapshot.company || snapshot.id) + (snapshot.retrieved_at ? ' · saved ' + String(snapshot.retrieved_at).slice(0, 10) : ''), () => addExtra(view, source, view.mapState)]);
      }
    }
    if (items.length === 1) items.push(['No other committee has this cycle', null]);
    menu(anchor, items, 'Another committee');
  }

  /* ------------------------------------------------------ Layer control */

  function renderControl(inner, pane, mapState, view) {
    let panel = inner.plot.querySelector(':scope > .mm-control.ev-control');
    if (!view.open) { if (panel) panel.remove(); syncButton(view); frameMap(inner, view); return; }
    if (!panel) {
      panel = el('section', 'mm-control ev-control'); panel.id = 'evidence-map-control'; panel.setAttribute('aria-label', 'Layer control'); inner.plot.append(panel);
      // Dropped somewhere else, the control leaves another room: the map follows it once the drag ends.
      panel.addEventListener('pointerup', () => { if (view.open) requestAnimationFrame(() => frameMap(inner, view)); });
    }
    const rows = presetRows(mapState).map(row => {
      const spec = {id: row.id, name: row.name, mark: row.mark, visible: isOn(view, row.id), selectable: !view.locked.has(row.id),
        onEye: () => {
          if (row.id === 'heat') { if (!view.heat.on) { view.heat.on = true; view.hidden.delete('heat'); } else view.heat.on = false; }
          else if (view.hidden.has(row.id)) view.hidden.delete(row.id); else view.hidden.add(row.id);
          redecorate(view);
        },
        // The Opposition is a marks card: its pointer is the card's selectability, handed over with it.
        onPointer: row.density ? null : () => { if (view.locked.has(row.id)) view.locked.delete(row.id); else view.locked.add(row.id); redecorate(view); }};
      if (row.density && isOn(view, 'heat')) spec.extra = heatSliders(view);
      if (row.card) {
        const compiled = view.compiled.get(row.id);
        spec.note = 'Opposition · hatch: spending that opposed a candidate, every candidate summed, support kept apart · '
          + (compiled ? compiled.legend.located : 'Reading…');
      }
      return spec;
    });
    for (const card of view.extra) {
      const compiled = view.compiled.get(card.id);
      rows.push({id: card.id, name: card.name, mark: card.type === 'circle' ? 'circles' : 'filled', visible: !view.hidden.has(card.id),
        selectable: !view.locked.has(card.id), note: compiled ? compiled.legend.located : 'Reading…',
        onEye: () => { if (view.hidden.has(card.id)) view.hidden.delete(card.id); else view.hidden.add(card.id); redecorate(view); },
        onPointer: () => { if (view.locked.has(card.id)) view.locked.delete(card.id); else view.locked.add(card.id); redecorate(view); }});
    }
    E.buildControl(panel, {rows, base: [{label: 'State borders', on: !view.bordersOff, toggle: () => { view.bordersOff = !view.bordersOff; redecorate(view); }}],
      addData: [{label: 'Another industry', onClick: (event, button) => industryMenu(button, view)},
        {label: 'Another committee', onClick: (event, button) => committeeMenu(button, view)}],
      footer: root.MentatMapStudio ? {id: 'evidence-map-edit-studio', label: 'Edit these layers in Chart Studio', onClick: () => openInStudio(pane, mapState)} : null,
      onClose: () => { view.open = false; redecorate(view); }, frame: inner.plot, position: view.position, onMove: position => { view.position = position; }});
    syncButton(view);
    frameMap(inner, view);
  }
  // Boards #48 and #55: with the Layer control open, the whole map is fitted into the room the
  // control and the toolbar leave (evidence-map.js frame) - the lower 48 beside the control, an
  // inset below it when it fits there. Closed, the map has its full fit again. With two cycles
  // compared the map keeps its full fit, so the two maps stay one picture at one zoom. The
  // reader's own zoom and pan are kept either way. A change of the map's size re-fits it.
  function frameMap(inner, view) {
    const drawing = view.drawing;
    if (!drawing || typeof drawing.frame !== 'function' || !view.host || !view.host.isConnected) return;
    const panel = view.open ? inner.plot.querySelector(':scope > .mm-control.ev-control') : null;
    const comparing = !!(view.mapState && view.mapState.compare && view.mapState.compareBody);
    const covers = [];
    if (panel && !comparing) {
      covers.push(panel.getBoundingClientRect());
      const bar = view.host.querySelector(':scope > .ev-toolbar');
      if (bar) covers.push(bar.getBoundingClientRect());
    }
    drawing.frame(view.host, covers);
    inner.frameView = view;
    watchSize(inner);
  }
  // One observer per map canvas; it re-fits for whichever pane last framed that canvas.
  const watched = new WeakSet();
  function watchSize(inner) {
    if (typeof ResizeObserver !== 'function' || !inner.svg || watched.has(inner.svg)) return;
    watched.add(inner.svg);
    let last = '', queued = 0;
    const observer = new ResizeObserver(entries => {
      const box = entries[entries.length - 1].contentRect, size = Math.round(box.width) + 'x' + Math.round(box.height);
      if (size === last) return;
      const first = !last;
      last = size;
      if (first || queued) return;
      queued = requestAnimationFrame(() => {
        queued = 0;
        if (!inner.svg.isConnected) { observer.disconnect(); return; }
        const view = inner.frameView;
        if (view && view.open) frameMap(inner, view);
      });
    });
    observer.observe(inner.svg);
  }
  function heatSliders(view) {
    const box = el('div', 'ev-heat-sliders');
    const slider = (label, key, min, max, suffix) => {
      const row = el('label', 'ev-heat-slider');
      const input = el('input'); input.type = 'range'; input.min = String(min); input.max = String(max); input.value = String(view.heat[key]);
      input.id = 'evidence-map-heat-' + key;
      const out = el('output', '', view.heat[key] + suffix);
      input.addEventListener('input', () => { out.textContent = input.value + suffix; });
      input.addEventListener('change', () => { view.heat[key] = Number(input.value); redecorate(view); });
      row.append(el('span', '', label), input, out);
      box.append(row);
    };
    slider('Intensity', 'intensity', 1, 100, '%');
    slider('Radius', 'radius', 4, 60, ' px');
    return box;
  }
  // #55's view toolbar: a column on the map's left - find, zoom in, zoom out, home and
  // the Layers toggle - with the Layer control opening beside it. Board 6's toolbar row
  // keeps its own zoom, Reset view and search; these buttons work those same controls,
  // so the two can never disagree. evidence.js renders the Layers toggle in the toolbar
  // row each time; it moves here, its word kept for screen readers.
  const TOOL_ICONS = {search: 'search', 'zoom-in': 'plus', 'zoom-out': 'minus', home: 'home'};
  function placeToolbar(host) {
    if (!host) return;
    host.classList.add('ev-has-layers');
    let bar = host.querySelector(':scope > .ev-toolbar');
    if (!bar) {
      bar = el('div', 'ev-toolbar');
      bar.setAttribute('role', 'toolbar');
      bar.setAttribute('aria-label', 'Map');
      bar.setAttribute('aria-orientation', 'vertical');
      const tool = (id, label, target, act) => {
        const node = el('button', 'ev-tool');
        node.type = 'button'; node.dataset.tool = id;
        node.setAttribute('aria-label', label); node.title = label;
        node.append(E.icon(TOOL_ICONS[id]));
        node.addEventListener('click', event => {
          event.stopPropagation();
          const control = document.getElementById(target);
          if (control && !control.disabled) act(control);
        });
        return node;
      };
      // Named apart from the toolbar row's own controls, so each name is one control.
      bar.append(
        tool('search', 'Find a place on the map', 'evidence-map-search', input => { input.focus(); input.select(); }),
        tool('zoom-in', 'Zoom the map in', 'evidence-map-zoom-in', control => control.click()),
        tool('zoom-out', 'Zoom the map out', 'evidence-map-zoom-out', control => control.click()),
        tool('home', 'Back to the whole map', 'evidence-map-reset', control => control.click()));
      host.append(bar);
    }
    // The zoom buttons follow the toolbar row's (out is off at the whole map, in at the closest zoom).
    for (const [id, target] of [['zoom-in', 'evidence-map-zoom-in'], ['zoom-out', 'evidence-map-zoom-out']]) {
      const source = document.getElementById(target), mine = bar.querySelector(`[data-tool="${id}"]`);
      if (source && mine) mine.disabled = !!source.disabled;
    }
    const button = document.getElementById('evidence-map-layers');
    if (!button || button.parentNode === bar) return;
    for (const old of host.querySelectorAll('.map-layers-button')) if (old !== button) old.remove();
    for (const text of [...button.childNodes]) {
      if (text.nodeType === 3 && text.textContent.trim()) text.replaceWith(el('span', 'ev-tool-label', text.textContent.trim()));
    }
    button.classList.add('ev-tool');
    bar.append(button);
  }
  function syncButton(view) {
    const button = document.getElementById('evidence-map-layers');
    if (!button) return;
    button.setAttribute('aria-expanded', String(!!view.open));
    button.classList.toggle('on', !!view.open);
  }
  function toggleControl(host, pane, mapState) {
    const view = stateFor(keyOf(pane));
    view.open = !view.open;
    view.pane = pane; view.mapState = mapState;
    redecorate(view);
    if (!view.drawing) syncButton(view);
  }

  /* ------------------------------------------------------- the handoff */

  // #55: the cards as they are drawn now - the preset layers at the chosen
  // LAYER with their eyes and pointers, the heat with its settings, and every
  // layer added with Add data - become a new Chart Studio map sheet.
  function cardsFor(pane, mapState) {
    const view = stateFor(keyOf(pane));
    const presets = L.evidencePresets(pane, mapState.direction, mapState.cycle, null,
      {layer: mapState.layer, hidden: view.hidden, locked: view.locked, heat: view.heat});
    const extras = view.extra.map((card, index) => Object.assign(JSON.parse(JSON.stringify(card)), {id: 'layer-' + (presets.length + index + 1),
      visible: !view.hidden.has(card.id), selectable: !view.locked.has(card.id)}));
    return [...extras, ...presets];
  }
  function openInStudio(pane, mapState) {
    if (!root.MentatMapStudio) return;
    const view = stateFor(keyOf(pane));
    const cards = cardsFor(pane, mapState);
    root.MentatMapStudio.openHandoff({name: String(pane.name || 'PAC') + ' map · ' + (mapState.direction === 'raised' ? 'Raised' : 'Spent') + ' ' + L.cycleLabel(mapState.cycle),
      cards, settings: {background: 'boundaries', base: view.bordersOff ? ['coastlines', 'state-names'] : ['coastlines', 'state-borders', 'state-names'],
        congress: String((mapState.body && mapState.body.vintage) || '119')}});
    if (root.MentatReturn && typeof root.MentatReturn.arrive === 'function') root.MentatReturn.arrive('evidence', 'chart-studio');
    if (typeof root.showView === 'function') root.showView('chart-studio'); else location.hash = '#chart-studio';
  }

  root.MentatMapEvidence = {decorate, toggleControl, openInStudio, cardsFor, stateFor, presetRows};
})(typeof globalThis !== 'undefined' ? globalThis : this);
