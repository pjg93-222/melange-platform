/* Streets (offline): the same map layers drawn over offline street tiles with
   MapLibre GL 6.11.2 (lane P, boards #49 and #54; Paul's C3 "A and B").

   Loaded by map-engine.js with import('/map-streets.mjs') only when a map's
   background is Streets, so a console that never opens one pays nothing.
   Everything is on the console's own origin (CSP default-src 'self'):
     MapLibre   /basemap/vendor/maplibre-gl.mjs (its worker is a same-origin module)
     pmtiles    /basemap/vendor/pmtiles.js (a classic script: it defines `pmtiles`)
     style      /basemap/style/light.json (built once by tools/build_basemap_style.mjs)
     tiles      pmtiles:// + /basemap/tiles/nyc-metro-z15.pmtiles, read by HTTP Range
     glyphs     /basemap/fonts/{fontstack}/{range}.pbf, sprite /basemap/sprites/v4/light
   MapLibre's CSS is a stylesheet file linked on first use; the library injects
   no style element, so style-src 'self' holds.

   Layers compile from map-layers.js compileLayer output: a card becomes a fill,
   line, circle, symbol or heatmap layer over lane G's WGS84 GeoJSON (feature id
   = GEOID, promoteId 'id'). Card order is draw order, as in the SVG engine. */
import * as maplibregl from '/basemap/vendor/maplibre-gl.mjs';

const L = globalThis.MentatMapLayers;
const EXTRACT = [-74.30, 40.45, -73.65, 40.95];
const ATTRIBUTION = '© OpenStreetMap contributors';
let pmtilesLoading = null, protocolAdded = false;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const tag = document.createElement('script');
    tag.src = src;
    tag.addEventListener('load', resolve);
    tag.addEventListener('error', () => reject(new Error('Could not load ' + src)));
    document.head.appendChild(tag);
  });
}
function ensurePmtiles() {
  if (globalThis.pmtiles) return Promise.resolve(globalThis.pmtiles);
  if (!pmtilesLoading) pmtilesLoading = loadScript('/basemap/vendor/pmtiles.js').then(() => globalThis.pmtiles);
  return pmtilesLoading;
}
function ensureCss() {
  if (document.querySelector('link[data-maplibre-css]')) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/basemap/vendor/maplibre-gl.css';
  link.dataset.maplibreCss = '1';
  document.head.appendChild(link);
}
const absolute = path => new URL(path, location.origin).href;
async function getJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(path + ' answered ' + response.status);
  return response.json();
}

// Base layers of the Map Layers pane, as the street style's own layer ids.
const STYLE_BASE = {
  water: id => /^water/.test(id),
  parks: id => id === 'landuse_park' || id === 'landuse_urban_green',
  streets: id => /^roads_/.test(id),
  'state-borders': id => /^boundaries/.test(id),
  'state-names': id => id === 'places_region',
  cities: id => id === 'places_locality' || id === 'places_subplace'};
// What the street renderer cannot switch off or does not draw.
export const STREETS_FIXED = {coastlines: 'The street map draws the coast with its water.',
  'county-names': 'County names draw on the Boundaries map.'};

let indexRequest = null;
async function wgs84Paths(level, states) {
  if (!indexRequest) indexRequest = getJson('/geo/v2/index.json');
  const index = await indexRequest;
  const files = index.files || [];
  const pick = (tier, state) => files.filter(file => file.form === 'wgs84' && file.tier === tier && file.level === level && (file.state || '') === (state || ''))
    .sort((a, b) => (a.part || 1) - (b.part || 1)).map(file => '/geo/v2/' + file.path);
  if (['state', 'county', 'cd116', 'cd118', 'cd119'].includes(level)) return pick('z0', '');
  if (level === 'hex25' || level === 'hex50') return pick('hex', '');
  const out = [];
  for (const state of states || []) {
    const found = level === 'hex10' ? pick('hex', state) : pick('z10', state).length ? pick('z10', state) : pick('z5', state);
    out.push(...found);
  }
  return out;
}
async function featuresFor(level, congress, states) {
  const fileLevel = level === 'cd' ? 'cd' + congress : level;
  const files = await wgs84Paths(fileLevel, states);
  const bodies = await Promise.all(files.map(getJson));
  return bodies.flatMap(body => body.features || []);
}
// Area-weighted centre of a polygon's largest ring, for circles and labels.
function centreOf(geometry) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  let best = null, bestArea = 0;
  for (const polygon of polygons) {
    const ring = polygon[0] || [];
    let area = 0, cx = 0, cy = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
      area += cross; cx += (ring[j][0] + ring[i][0]) * cross; cy += (ring[j][1] + ring[i][1]) * cross;
    }
    if (Math.abs(area) > bestArea && area !== 0) { bestArea = Math.abs(area); best = [cx / (3 * area), cy / (3 * area)]; }
  }
  return best;
}
function boundsOf(features) {
  let box = null;
  const visit = coords => {
    if (typeof coords[0] === 'number') {
      box = box ? [Math.min(box[0], coords[0]), Math.min(box[1], coords[1]), Math.max(box[2], coords[0]), Math.max(box[3], coords[1])] : [coords[0], coords[1], coords[0], coords[1]];
      return;
    }
    for (const part of coords) visit(part);
  };
  for (const feature of features) if (feature.geometry) visit(feature.geometry.coordinates);
  return box;
}
const intersects = (a, b) => a && b && a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
// A hatched Filled layer (map.pattern, #55's Opposition): the same diagonal ink as the
// SVG engine, as a MapLibre fill-pattern image built from pixels (no sprite, no network).
// Returns false when the image cannot be added; the layer then draws a light tint in
// the hatch ink with an outline, which still reads as "this place, this side".
function addHatch(map) {
  if (map.hasImage('mm-hatch')) return true;
  const size = 8, data = new Uint8Array(size * size * 4);
  const ink = [0x9b, 0x3d, 0x5b];
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const at = (y * size + x) * 4, on = (x + y) % size < 2;
      data[at] = on ? ink[0] : 255; data[at + 1] = on ? ink[1] : 255; data[at + 2] = on ? ink[2] : 255;
      data[at + 3] = on ? 255 : 90;
    }
  }
  try { map.addImage('mm-hatch', {width: size, height: size, data}); return true; } catch (error) { return false; }
}

export async function render(host, model, hooks, previous) {
  // A redraw of the same layers (a restyle, the washout, a card's colour) keeps the reader's view, as the
  // boundary map keeps it; a new set of layers is fitted afresh. The previous map stays up until the new
  // one is about to replace it, so a move made meanwhile is the view that is kept.
  const signature = (model.layers || []).map(layer => [layer.id, layer.sourceKey, layer.level, layer.zoomExtent].join(':')).join('|');
  ensureCss();
  const pmtiles = await ensurePmtiles();
  if (!protocolAdded) {
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);
    protocolAdded = true;
  }
  const settings = model.settings || L.settingsOf({});
  const style = await getJson('/basemap/style/light.json');
  style.glyphs = absolute('/basemap/fonts/') + '{fontstack}/{range}.pbf';
  style.sprite = absolute('/basemap/sprites/v4/light');
  style.sources.protomaps.url = 'pmtiles://' + absolute('/basemap/tiles/nyc-metro-z15.pmtiles');
  const base = new Set(settings.base || []);
  for (const layer of style.layers) {
    for (const [id, test] of Object.entries(STYLE_BASE)) {
      if (test(layer.id) && !base.has(id)) layer.layout = Object.assign({}, layer.layout, {visibility: 'none'});
    }
  }
  const lookup = L.createLookup(getJson);
  await lookup.ready([]);
  let kept = null;
  if (previous) {
    try { if (previous.signature === signature && typeof previous.camera === 'function') kept = previous.camera(); } catch (error) { kept = null; }
    previous.destroy();
  }
  host.replaceChildren();
  const container = document.createElement('div');
  container.className = 'mm-streets-map';
  host.append(container);
  const camera = kept ? {center: kept.center, zoom: kept.zoom} : {bounds: [EXTRACT[0], EXTRACT[1], EXTRACT[2], EXTRACT[3]]};
  const map = new maplibregl.Map(Object.assign({container, style, attributionControl: false,
    maxZoom: 18, minZoom: 2, dragRotate: false, pitchWithRotate: false, touchPitch: false, cooperativeGestures: false}, camera));
  // Where the camera stood when the map was made: a reader (or a script) who moves the map while its
  // style loads and its layers are added is not thrown back by the fit at the end.
  const started = {center: map.getCenter(), zoom: map.getZoom()};
  const movedSinceStart = () => {
    const now = map.getCenter();
    return Math.abs(now.lng - started.center.lng) > 1e-7 || Math.abs(now.lat - started.center.lat) > 1e-7 || Math.abs(map.getZoom() - started.zoom) > 1e-7;
  };
  // The instance on its own container (no global): what a browser check reads (lane P real client).
  container.__maplibre = map;
  map.addControl(new maplibregl.AttributionControl({compact: false, customAttribution: ATTRIBUTION}), 'bottom-right');
  map.touchZoomRotate.disableRotation();
  if (((settings.options || {}).panZoom) === false) { map.dragPan.disable(); map.scrollZoom.disable(); map.doubleClickZoom.disable(); map.keyboard.disable(); }
  await new Promise(resolve => map.once('load', resolve));

  const layerIds = new Map();           // card id -> MapLibre layer ids
  const itemsById = new Map();          // card id -> Map(feature id -> item)
  // A per-state layer whose places reach states the street extract has no geometry for:
  // {layer, words, boxes} - the boxes of the features it DID draw, so the note shows only
  // where none of them is in view (MP2: the New York and New Jersey ZIP areas do draw).
  let firstDataLayer = null, extent = null;
  const partial = [];
  // Washout: a white wash between the street tiles and the data layers.
  if (settings.washout > 0) {
    map.addSource('mm-washout', {type: 'geojson', data: {type: 'Feature', properties: {},
      geometry: {type: 'Polygon', coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]]}}});
    map.addLayer({id: 'mm-washout', type: 'fill', source: 'mm-washout', paint: {'fill-color': '#ffffff', 'fill-opacity': settings.washout / 100}});
  }
  // Base boundary lines the street tiles do not carry.
  const addBaseLines = async (id, level, minzoom, colour, states) => {
    const features = await featuresFor(level, settings.congress || '119', states);
    if (!features.length) return;
    map.addSource('mm-base-' + id, {type: 'geojson', data: {type: 'FeatureCollection', features}});
    map.addLayer({id: 'mm-base-' + id, type: 'line', source: 'mm-base-' + id, minzoom,
      paint: {'line-color': colour, 'line-width': 0.8, 'line-opacity': 0.8}});
  };
  const metro = ['NJ', 'NY'];
  if (base.has('county-borders')) await addBaseLines('county', 'county', 6, '#9c8f84');
  if (base.has('cd')) await addBaseLines('cd', 'cd', 0, '#8b6984');
  if (base.has('sld')) await addBaseLines('sld', 'sldu', 7, '#a29bb8', metro);
  if (base.has('zcta')) await addBaseLines('zcta', 'zcta', 10, '#b8b0a6', metro);

  const uspsOfFips = fips => lookup.usps(fips);
  const layers = (model.layers || []).slice().reverse();
  for (const layer of layers) {
    const ids = [];
    const byId = new Map(layer.items.map(item => [String(item.id), item]));
    itemsById.set(layer.id, byId);
    const sourceId = 'mm-src-' + layer.id;
    // Circles, shapes and text need only a point: a ZIP, county or district layer whose
    // items all carry their centre (the lookup's) draws anywhere, not just where the
    // per-state polygons exist.
    const pointMark = layer.mark === 'circle' || layer.mark === 'shape' || layer.mark === 'text';
    if (layer.mark === 'density' || layer.level === 'point' || (pointMark && layer.items.length > 0 && layer.items.every(item => item.point))) {
      const maxWeight = Math.max(1e-9, ...layer.points.map(point => point.weight || 0));
      const points = layer.mark === 'density'
        ? layer.points.map(point => ({type: 'Feature', properties: {w: (point.weight || 0) / maxWeight},
          geometry: {type: 'Point', coordinates: [point.lon, point.lat]}}))
        : layer.items.filter(item => item.point).map(item => ({type: 'Feature', id: item.id,
          properties: {id: String(item.id), c: item.colour || '#327a7a', r: item.radius, l: item.label || (layer.mark === 'text' ? String(item.id) : '')},
          geometry: {type: 'Point', coordinates: [item.point[1], item.point[0]]}}));
      map.addSource(sourceId, {type: 'geojson', data: {type: 'FeatureCollection', features: points}, promoteId: 'id'});
      if (layer.mark === 'density') {
        const stops = layer.ramp;
        const intensity = 0.2 + (layer.intensity / 100) * 2.3;
        // The boundary map's curve (map-layers.js densityColourStops): a purple halo round a gold
        // core, the low end transparent, so its hotspots glow here as they do there (MP5).
        map.addLayer({id: sourceId + '-heat', type: 'heatmap', source: sourceId,
          paint: {'heatmap-weight': ['get', 'w'], 'heatmap-radius': layer.radius, 'heatmap-intensity': intensity,
            'heatmap-opacity': layer.opacity,
            'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], ...L.densityColourStops(stops, layer.intensity)]}});
        ids.push(sourceId + '-heat');
      } else if (layer.mark === 'text') {
        map.addLayer({id: sourceId + '-label', type: 'symbol', source: sourceId,
          layout: {'text-field': ['get', 'l'], 'text-font': ['Noto Sans Medium'], 'text-size': 11, 'text-allow-overlap': false},
          paint: {'text-color': '#2c2a28', 'text-halo-color': '#ffffff', 'text-halo-width': 1.2, 'text-opacity': layer.opacity}});
        ids.push(sourceId + '-label');
      } else {
        map.addLayer({id: sourceId + '-circle', type: 'circle', source: sourceId,
          paint: {'circle-color': ['get', 'c'], 'circle-radius': ['get', 'r'], 'circle-opacity': layer.opacity,
            'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.8}});
        ids.push(sourceId + '-circle');
      }
      const box = boundsOf(points);
      if (layer.zoomExtent && box) extent = extent ? [Math.min(extent[0], box[0]), Math.min(extent[1], box[1]), Math.max(extent[2], box[2]), Math.max(extent[3], box[3])] : box;
    } else {
      const states = [...new Set(layer.items.map(item => item.statefp ? uspsOfFips(item.statefp) : /^[A-Z]{2}/.test(String(item.id)) ? String(item.id).slice(0, 2) : '').filter(Boolean))];
      const perState = L.PER_STATE_LEVELS.includes(layer.level);
      const drawable = perState ? states.filter(usps => metro.includes(usps)) : states;
      const note = perState && drawable.length < states.length
        ? {layer, words: layer.name + ' (' + L.levelLabel(layer.level).replace(/^[A-Z](?=[a-z])/, letter => letter.toLowerCase()) + ' geometry for the street map exists for New Jersey and New York only)', boxes: []}
        : null;
      if (note) partial.push(note);
      const raw = await featuresFor(layer.level, layer.congress || settings.congress || '119', drawable);
      const keyOf = feature => {
        const id = String(feature.properties.id);
        if (layer.level === 'state') return uspsOfFips(id) || id;
        if (layer.level === 'cd') return lookup.districtKey(id) || id;
        return id;
      };
      const features = [], centres = [];
      for (const feature of raw) {
        const key = keyOf(feature), item = byId.get(key);
        if (!item) continue;
        const properties = {id: key, c: item.colour || '#327a7a', r: item.radius, l: item.label || (layer.mark === 'text' ? key : '')};
        features.push({type: 'Feature', properties, geometry: feature.geometry});
        const centre = centreOf(feature.geometry);
        if (centre) centres.push({type: 'Feature', properties, geometry: {type: 'Point', coordinates: centre}});
        // The place's centre stands for it in the "Not drawn here" check: no second walk over its rings.
        if (note && centre) note.boxes.push([centre[0], centre[1], centre[0], centre[1]]);
      }
      if (layer.mark === 'filled-map' || layer.mark === 'line') {
        map.addSource(sourceId, {type: 'geojson', data: {type: 'FeatureCollection', features}, promoteId: 'id'});
        if (layer.mark === 'filled-map' && layer.pattern === 'hatch') {
          const hatched = addHatch(map);
          map.addLayer({id: sourceId + '-fill', type: 'fill', source: sourceId, paint: hatched
            ? {'fill-pattern': 'mm-hatch', 'fill-opacity': layer.opacity}
            : {'fill-color': L.HATCH_INK, 'fill-opacity': 0.25 * layer.opacity}});
          map.addLayer({id: sourceId + '-edge', type: 'line', source: sourceId, paint: {'line-color': L.HATCH_INK, 'line-width': hatched ? 0.6 : 1.2, 'line-opacity': 0.8}});
          ids.push(sourceId + '-fill', sourceId + '-edge');
        } else if (layer.mark === 'filled-map') {
          map.addLayer({id: sourceId + '-fill', type: 'fill', source: sourceId, paint: {'fill-color': ['get', 'c'], 'fill-opacity': layer.opacity}});
          map.addLayer({id: sourceId + '-edge', type: 'line', source: sourceId, paint: {'line-color': '#ffffff', 'line-width': 0.4, 'line-opacity': 0.7}});
          ids.push(sourceId + '-fill', sourceId + '-edge');
        } else {
          map.addLayer({id: sourceId + '-line', type: 'line', source: sourceId, paint: {'line-color': ['get', 'c'], 'line-width': 1.4, 'line-opacity': layer.opacity}});
          ids.push(sourceId + '-line');
        }
      }
      if (layer.mark === 'circle' || layer.mark === 'shape' || layer.mark === 'text' || features.some(feature => feature.properties.l)) {
        map.addSource(sourceId + '-pt', {type: 'geojson', data: {type: 'FeatureCollection', features: centres}, promoteId: 'id'});
        if (layer.mark === 'circle' || layer.mark === 'shape') {
          map.addLayer({id: sourceId + '-circle', type: 'circle', source: sourceId + '-pt',
            paint: {'circle-color': ['get', 'c'], 'circle-radius': ['get', 'r'], 'circle-opacity': layer.opacity,
              'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.8}});
          ids.push(sourceId + '-circle');
        }
        map.addLayer({id: sourceId + '-label', type: 'symbol', source: sourceId + '-pt',
          layout: {'text-field': ['get', 'l'], 'text-font': ['Noto Sans Medium'], 'text-size': 11, 'text-allow-overlap': false},
          paint: {'text-color': '#2c2a28', 'text-halo-color': '#ffffff', 'text-halo-width': 1.2}});
        ids.push(sourceId + '-label');
      }
      const box = boundsOf(features);
      if (layer.zoomExtent && box) extent = extent ? [Math.min(extent[0], box[0]), Math.min(extent[1], box[1]), Math.max(extent[2], box[2]), Math.max(extent[3], box[3])] : box;
    }
    if (!firstDataLayer && ids.length) firstDataLayer = ids[0];
    layerIds.set(layer.id, ids);
  }

  let recheck = null;                   // the banner's check, once it exists (below)
  const handle = {
    map, layerIds, signature,
    camera() { const centre = map.getCenter(); return {center: [centre.lng, centre.lat], zoom: map.getZoom()}; },
    destroy() { try { map.remove(); } catch (error) { /* already gone */ } host.replaceChildren(); },
    zoom(factor) { map.easeTo({zoom: map.getZoom() + Math.log2(factor), duration: 200}); },
    reset() { fit(); },
    setLayerState(view) {
      for (const layer of model.layers || []) {
        const visible = layer.visible && !(view && view.hidden && view.hidden.has(layer.id));
        for (const id of layerIds.get(layer.id) || []) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
      }
      // A layer shown or hidden in the Layer control changes what "Not drawn here" names.
      if (recheck) recheck();
    }};
  const fit = () => {
    if (extent && extent[2] - extent[0] < 60) map.fitBounds([[extent[0], extent[1]], [extent[2], extent[3]]], {padding: 36, maxZoom: 13, duration: 0});
    else map.fitBounds([[EXTRACT[0], EXTRACT[1]], [EXTRACT[2], EXTRACT[3]]], {padding: 12, duration: 0});
  };
  // The first view fits the layers - unless this redraw kept the reader's view, or the map was moved while
  // its layers were being added. Home (reset) always fits.
  if (!kept && !movedSinceStart()) fit();
  handle.setLayerState(model.viewState);
  // Outside the offline extract there are no streets: say so and offer the
  // Boundaries map, which draws the whole country. A per-state layer whose
  // places reach states the street map has no geometry for is named "Not drawn
  // here" only while none of the features it did draw is in view (MP2: in the
  // New York view its New York and New Jersey ZIP areas draw, so nothing is
  // missing there); a layer the reader has hidden is not named at all.
  const check = () => {
    const bounds = map.getBounds();
    const box = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
    const center = map.getCenter();
    const covered = intersects(box, EXTRACT) && center.lng >= EXTRACT[0] - 0.5 && center.lng <= EXTRACT[2] + 0.5
      && center.lat >= EXTRACT[1] - 0.5 && center.lat <= EXTRACT[3] + 0.5;
    const notes = [];
    if (!covered) notes.push('No street data for this area');
    const view = model.viewState || {};
    const missing = partial.filter(entry => entry.layer.visible && !(view.hidden && view.hidden.has && view.hidden.has(entry.layer.id))
      && !entry.boxes.some(found => intersects(found, box))).map(entry => entry.words);
    if (missing.length) notes.push('Not drawn here: ' + missing.join('; '));
    if (notes.length && hooks.onBanner) hooks.onBanner(notes.join('. ') + '.', !covered && model.onBoundaries ? {label: 'Show Boundaries', run: model.onBoundaries} : null);
    else if (hooks.onBanner) hooks.onBanner('', null);
    if (hooks.onView) hooks.onView({zoom: map.getZoom(), metersPerPixel: 40075016.686 * Math.cos(center.lat * Math.PI / 180) / Math.pow(2, map.getZoom() + 9)});
  };
  recheck = check;
  map.on('moveend', check);
  check();
  const interactive = () => [...layerIds.entries()].filter(([id]) => {
    const layer = (model.layers || []).find(item => item.id === id);
    return layer && layer.selectable && !(model.viewState && model.viewState.locked && model.viewState.locked.has(id));
  }).flatMap(([, ids]) => ids).filter(id => !/-heat$|-label$|-edge$/.test(id));
  const pick = event => {
    const found = map.queryRenderedFeatures(event.point, {layers: interactive().filter(id => map.getLayer(id))})[0];
    if (!found) return null;
    const cardId = [...layerIds.entries()].find(([, ids]) => ids.includes(found.layer.id));
    if (!cardId) return null;
    const layer = (model.layers || []).find(item => item.id === cardId[0]);
    const item = itemsById.get(cardId[0]).get(String(found.properties.id));
    return layer && item ? {layer, item} : null;
  };
  map.on('mousemove', event => {
    const hit = pick(event);
    map.getCanvas().style.cursor = hit ? 'pointer' : '';
    if (hit && hooks.tooltip) hooks.tooltip(event.originalEvent, hit.layer, hit.item); else if (hooks.hideTooltip) hooks.hideTooltip();
  });
  map.on('click', event => { const hit = pick(event); if (hit && hooks.onSelect) hooks.onSelect(hit.layer.id, String(hit.item.id), hit.item); });
  return handle;
}
