/* The interactive PAC map (P4b).
 *
 * evidence.js owns the map VIEW - its header, toolbar and ranked list. This
 * module owns the picture: one persistent <svg>, built once per open and
 * updated in place afterwards. That is the whole reason it exists. The view it
 * replaced rebuilt the svg from an HTML string on every layer toggle and every
 * district click, so any zoom or pan a reader had set was thrown away one
 * interaction later.
 *
 * ZOOM AND PAN MOVE ONE TRANSFORM, NOT THE GEOMETRY. The chosen model is a
 * `transform` on a single viewport <g>; the `viewBox` is written once, from the
 * boundary file, and never again. (viewBox animation was the alternative; a
 * group transform was chosen because it leaves the document's own coordinate
 * system alone, so hit-testing and every bbox stay in map units. Circle radii
 * alone adjust for screen size; polygon geometry stays mounted.)
 *
 * Nothing here reaches a network. The polygons come from the boundary file the
 * console already ships and the ZIP centres from geo/zcta_points.json, which is
 * pre-projected onto the same 975x610 canvas as the paths, so a point is placed
 * without re-deriving the Albers fit in the browser.
 *
 * PRIVACY, deliberate: this module never prints a person's name. A circle is a
 * PLACE with a count and a total. Names appear only in the rows list the pane
 * already shows, which the reader has to ask for.
 */
(function () {
  'use strict';

  const SVG = 'http://www.w3.org/2000/svg';
  const MIN_SCALE = 1;
  const MAX_SCALE = 40;
  const FLY_MS = 240;
  const DRAG_THRESHOLD = 4;
  const MERGE_PIXELS = 6;
  const DETAIL_SCALE = 2.5;       // below this, only the largest circles draw
  const COARSE_CIRCLES = 150;
  const MAX_CIRCLES = 800;       // adaptive clustering bounds SVG work at every zoom
  const LOCATOR_SCALE = 1.5;      // above this, the inset locator appears
  const TEAL_RAMP = ['#e8f3f1', '#b9ddd8', '#7cbfb7', '#4c9e95', '#327a7a'];
  const FRAME_GAP = 10;           // px kept between the framed map and what covers it
  const FRAME_PAD = 6;            // canvas units a frame keeps inside the canvas's edge
  const FRAME_MIN = 0.3;          // below this the map keeps its full fit (too small to read)
  const IDENTITY = Object.freeze({scale: 1, x: 0, y: 0});

  /* Circle size is the number of DISTINCT contributors, not the amount
     (owner's ruling on board 6): the amount is already carried by the heat fill
     and said again in the tooltip, so spending the second channel on donor
     count tells the reader something the map does not otherwise say. */
  const DONOR_BUCKETS = [
    {max: 1, radius: 3, label: '1'},
    {max: 5, radius: 5, label: '2–5'},
    {max: 10, radius: 8, label: '6–10'},
    {max: Infinity, radius: 11, label: '11+'}
  ];

  const clampScale = value => Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(value) || MIN_SCALE));

  function donorRadius(count) {
    const wanted = Math.max(1, Number(count) || 1);
    return (DONOR_BUCKETS.find(bucket => wanted <= bucket.max) || DONOR_BUCKETS[3]).radius;
  }

  /* The transform that brings one bbox to the middle of the canvas, padded.
     Returned rather than applied so a test can assert the arithmetic without a
     document, and so the caller can ease towards it. */
  function flyToTransform(bbox, width, height, padding) {
    const pad = Number(padding) || 0;
    const box = (bbox || []).map(Number);
    const spanX = Math.max(1e-6, box[2] - box[0]);
    const spanY = Math.max(1e-6, box[3] - box[1]);
    const scale = clampScale(Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY));
    return {scale: scale, x: width / 2 - scale * (box[0] + box[2]) / 2,
            y: height / 2 - scale * (box[1] + box[3]) / 2};
  }

  /* THE FRAME (boards #48 and #55). With PAC evidence's Layer control open, the
     whole map is fitted into the room the control and its toolbar leave, as the
     boards compose it: the lower 48 beside the control, Alaska and Hawaii below
     it when they fit there. The frame is a second, outer transform: the viewport
     draws translate(frame) scale(frame) round the reader's own zoom and pan, which
     it never changes, so Home, the zoom limits, Find and a saved view keep their
     meaning inside the room. With no cover (the control closed) it is the
     identity, the full fit.
     regionBoxes: the map's separate pieces - the lower 48 and each inset - from
     the region the boundary file tags each state with; a file without regions
     is one piece. */
  function regionBoxes(shapes, width, height) {
    const boxes = new Map();
    for (const entry of Object.values((shapes && shapes.states) || {})) {
      const box = ((entry && entry.bbox) || []).map(Number);
      if (box.length !== 4 || !box.every(Number.isFinite)) continue;
      const key = (entry && entry.region) || 'lower48', found = boxes.get(key);
      boxes.set(key, found ? [Math.min(found[0], box[0]), Math.min(found[1], box[1]),
                              Math.max(found[2], box[2]), Math.max(found[3], box[3])] : box);
    }
    return boxes.size ? [...boxes.values()] : [[0, 0, width || 975, height || 610]];
  }

  /* The largest frame - never above the full fit, scale 1 - that keeps every
     piece (`regions`, map units) inside the canvas, `pad` in from its edge, and
     clear of every box in `covers` (canvas units). The offsets tried are the
     edges the canvas, the pieces and the covers make, so the fit is exact rather
     than a guess on a grid (a feasible offset can always slide to one of them),
     and the middle; of those that fit, the one nearest the middle wins. Below FRAME_MIN, or with
     the full fit already clear, the answer is the identity. Returned rather than
     applied, as flyToTransform is. */
  function frameAround(regions, covers, width, height, pad) {
    const pieces = (regions || []).filter(box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite));
    const blocks = (covers || []).filter(box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)
                                               && box[2] > box[0] && box[3] > box[1]);
    const margin = Number(pad) || 0, eps = 1e-6;
    const clear = (s, tx, ty) => pieces.every(p => blocks.every(o => s * p[2] + tx <= o[0] + eps
      || s * p[0] + tx >= o[2] - eps || s * p[3] + ty <= o[1] + eps || s * p[1] + ty >= o[3] - eps));
    if (!pieces.length || !blocks.length || clear(1, 0, 0)) return {scale: 1, x: 0, y: 0};
    const all = [Math.min(...pieces.map(p => p[0])), Math.min(...pieces.map(p => p[1])),
                 Math.max(...pieces.map(p => p[2])), Math.max(...pieces.map(p => p[3]))];
    const place = s => {
      const x0 = margin - s * all[0], x1 = width - margin - s * all[2];
      const y0 = margin - s * all[1], y1 = height - margin - s * all[3];
      if (x0 > x1 + eps || y0 > y1 + eps) return null;
      // The middle itself is tried too, so a map with room to spare sits centred on that axis.
      const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
      const xs = [x0, x1, mx], ys = [y0, y1, my];
      for (const p of pieces) {
        for (const o of blocks) { xs.push(o[0] - s * p[2], o[2] - s * p[0]); ys.push(o[1] - s * p[3], o[3] - s * p[1]); }
      }
      let best = null;
      for (const tx of xs) {
        if (tx < x0 - eps || tx > x1 + eps) continue;
        for (const ty of ys) {
          if (ty < y0 - eps || ty > y1 + eps || !clear(s, tx, ty)) continue;
          const distance = (tx - mx) * (tx - mx) + (ty - my) * (ty - my);
          if (!best || distance < best.distance) best = {x: tx, y: ty, distance: distance};
        }
      }
      return best;
    };
    let low = FRAME_MIN, high = 1;
    if (!place(low)) return {scale: 1, x: 0, y: 0};
    for (let round = 0; round < 24; round += 1) {
      const mid = (low + high) / 2;
      if (place(mid)) low = mid; else high = mid;
    }
    const spot = place(low);
    return {scale: low, x: spot.x, y: spot.y};
  }

  /* Circles whose centres land within MERGE_PIXELS of each other on SCREEN are
     drawn once, with the counts and dollars summed and the count printed in the
     middle. A merged circle is still a PLACE - every ZIP in that spot - so it
     is named by how many ZIPs it stands for, never by a person. */
  function mergeCircles(points, scale, pixels) {
    const step = Math.max(1e-6, (Number(pixels) || MERGE_PIXELS) / Math.max(1e-6, scale));
    const cells = new Map();
    const order = [];
    for (const point of points) {
      const cell = Math.floor(point.x / step) + ':' + Math.floor(point.y / step);
      const found = cells.get(cell);
      if (!found) {
        const fresh = {x: point.x, y: point.y, amount: point.amount || 0,
                       contributors: point.contributors || 0, rows: point.rows || 0,
                       places: 1, member: point};
        cells.set(cell, fresh);
        order.push(fresh);
        continue;
      }
      found.amount += point.amount || 0;
      found.contributors += point.contributors || 0;
      found.rows += point.rows || 0;
      found.places += 1;
      if ((point.amount || 0) > (found.member.amount || 0)) found.member = point;
    }
    // Largest FIRST in the DOM, so the small ones land on top of them and stay
    // clickable - the brief's rule, and the opposite of painting order for a
    // heat map.
    return order.sort((first, second) => (second.contributors - first.contributors)
                                      || (second.amount - first.amount));
  }

  function indexPoints(points, step = 32) {
    const cells = new Map();
    const bounds = [Infinity, Infinity, -Infinity, -Infinity];
    for (const point of points) {
      const x = Math.floor(point.x / step), y = Math.floor(point.y / step);
      const key = x + ':' + y;
      bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y);
      bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y);
      if (!cells.has(key)) cells.set(key, []);
      cells.get(key).push(point);
    }
    return {cells, step, bounds};
  }

  function pointsInBox(index, box) {
    const found = [];
    for (let x=Math.max(index.bounds[0],Math.floor(box[0]/index.step));x<=Math.min(index.bounds[2],Math.floor(box[2]/index.step));x++) {
      for (let y=Math.max(index.bounds[1],Math.floor(box[1]/index.step));y<=Math.min(index.bounds[3],Math.floor(box[3]/index.step));y++) {
        for (const point of index.cells.get(x+':'+y)||[]) {
          if (point.x>=box[0]&&point.x<=box[2]&&point.y>=box[1]&&point.y<=box[3]) found.push(point);
        }
      }
    }
    return found;
  }

  /* "TX-19", "tx 19", "79401", "Texas", "TX". Returns what the reader meant,
     never a guess dressed as a match: an unrecognised string answers kind ''
     and the caller says so in words. */
  function parseSearch(text) {
    const raw = String(text == null ? '' : text).trim();
    if (!raw) return {kind: '', value: ''};
    const district = raw.toUpperCase().match(/^([A-Z]{2})[\s-]*(\d{1,2}|AL|SEN)$/);
    if (district) {
      const seat = /^\d+$/.test(district[2]) ? String(district[2]).padStart(2, '0') : district[2];
      return {kind: 'district', value: district[1] + '-' + seat};
    }
    if (/^\d{5}$/.test(raw)) return {kind: 'zip', value: raw};
    if (/^[A-Za-z]{2}$/.test(raw)) return {kind: 'state', value: raw.toUpperCase()};
    return {kind: 'name', value: raw.toLowerCase()};
  }

  function heatDomain(values) {
    const positive = Array.from(values).filter(value => Number.isFinite(value) && value > 0);
    return positive.length ? [Math.min(...positive), Math.max(...positive)] : [0, 0];
  }

  // A display estimate from the packaged internal-point crosswalk, never an
  // address-level match or a rewrite of the source's congressional allocation.
  function legislativeRows(body, bundle, crosswalk, chamber, off = new Set()) {
    if (!bundle || off.has('contributors')) return [];
    const rows = new Map((bundle.districts || []).map(d => [d.id,
      {kind: 'sld', key: `${bundle.state}-${chamber}-${d.id}`, id: d.id,
       name: `${bundle.state} · ${d.name}`, amount: 0, rows: 0, zips: 0}]));
    for (const zip of body.by_zip || []) {
      const id = (crosswalk?.[zip.zip] || [])[chamber === 'upper' ? 0 : 1];
      const row = rows.get(id);
      if (row) { row.amount += zip.amount || 0; row.rows += zip.rows || 0; row.zips++; }
    }
    return [...rows.values()].filter(row => row.zips);
  }

  const intersects = (a, b) => a && b && a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];

  function heatColour(value, ramp, domain) {
    if (value < 0) return '#b85c67'; // A net reversal is evidence, not an empty place.
    if (!(value > 0)) return '';
    const stops = ramp || TEAL_RAMP;
    const span = stops.length - 1;
    const range = domain || [100, 10000000];
    const position = range[1] <= range[0] ? 1 : Math.min(1, Math.max(0,
      (Math.log(value) - Math.log(range[0])) / (Math.log(range[1]) - Math.log(range[0]))));
    const slot = Math.min(span - 1, Math.floor(position * span));
    const parts = hex => [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16));
    const start = parts(stops[slot]), end = parts(stops[slot + 1]);
    const at = position * span - slot;
    return '#' + start.map((channel, index) => Math.round(channel + (end[index] - channel) * at)
      .toString(16).padStart(2, '0')).join('');
  }

  /* Item 7 of F4 (Paul approved 2026-09-25): a tooltip's share is of positive
     located money, the base the server names (share_base.places, or
     share_base.zips on the ZIP and state-district layers); a place that nets
     below zero is a reversal with no share, and a refund among the unlocated
     lines does not withdraw the others. A payload from before the server named
     its base (a console not yet restarted onto v4) keeps the old rule and the
     old words (shareOf). evidence.js's shareBase reads the same field. */
  function shareBaseFor(view) {
    const body = (view && view.body) || {};
    const zips = Boolean(view) && (view.layer === 'zip' || view.layer === 'sld');
    if (body.share_base) {
      const base = body.share_base[zips ? 'zips' : 'places'];
      return base == null ? 0 : (Number(base) || 0);
    }
    const reversed = (body.kinds || []).some(kind => [...(kind.districts || []), ...(kind.states || [])].some(row => row.amount < 0))
      || (body.by_zip || []).some(row => row.amount < 0) || (body.unlocated || []).some(row => row.amount < 0);
    return body.reported_total > 0 && !reversed ? body.reported_total : 0;
  }

  function shareOf(body, raised) {
    return body && body.share_base ? '% of positive located ' + (raised ? 'receipts' : 'spending')
      : '% of ' + (raised ? 'receipts' : 'spend');
  }

  const PURE = {clampScale: clampScale, donorRadius: donorRadius, flyToTransform: flyToTransform, shareBaseFor: shareBaseFor, shareOf: shareOf,
                regionBoxes: regionBoxes, frameAround: frameAround, FRAME_GAP: FRAME_GAP, FRAME_PAD: FRAME_PAD, FRAME_MIN: FRAME_MIN,
                mergeCircles: mergeCircles, indexPoints, pointsInBox, parseSearch: parseSearch, heatColour: heatColour,
                heatDomain: heatDomain, amountsFor: amountsFor, domainFor: domainFor, legislativeRows,
                unionDomain: unionDomain, rangeFor: rangeFor,
                MIN_SCALE: MIN_SCALE, MAX_SCALE: MAX_SCALE, MERGE_PIXELS: MERGE_PIXELS,
                DETAIL_SCALE: DETAIL_SCALE, COARSE_CIRCLES: COARSE_CIRCLES, MAX_CIRCLES,
                DONOR_BUCKETS: DONOR_BUCKETS, LOCATOR_SCALE: LOCATOR_SCALE, TEAL_RAMP: TEAL_RAMP};

  if (typeof module !== 'undefined' && module.exports) module.exports = PURE;
  if (typeof document === 'undefined') return;

  /* ------------------------------------------------------------ the drawing */

  const money = value => (value == null || !isFinite(value)) ? '—'
    : '$' + Math.round(Number(value)).toLocaleString('en-US');
  const number = value => Number(value || 0).toLocaleString('en-US');
  const node = (name, attributes) => {
    const element = document.createElementNS(SVG, name);
    for (const key of Object.keys(attributes || {})) {
      const value = attributes[key];
      if (value !== null && value !== undefined) element.setAttribute(key, String(value));
    }
    return element;
  };
  const escapeKey = value => (window.CSS && CSS.escape) ? CSS.escape(String(value)) : String(value);

  const views = new WeakMap();

  function build(host, view) {
    host.textContent = '';
    const shapes = view.shapes || {};
    const state = {scale: 1, x: 0, y: 0, frame: IDENTITY, width: shapes.width || 975, height: shapes.height || 610,
                   layers: {}, points: [], missing: [], frames: [], shapesKey: '',
                   reduced: Boolean(window.matchMedia
                     && window.matchMedia('(prefers-reduced-motion: reduce)').matches)};
    views.set(host, state);

    const plot = document.createElement('div');
    plot.className = 'map-plot';
    const svg = node('svg', {
      'class': 'map-canvas', viewBox: '0 0 ' + state.width + ' ' + state.height,
      preserveAspectRatio: 'xMidYMid meet', tabindex: '0', role: 'application',
      'aria-label': 'Map of ' + (view.direction === 'raised' ? 'receipts' : 'spending')
        + '. Arrow keys pan, plus and minus zoom, Enter opens the shape under focus.'});
    const viewport = node('g', {'class': 'map-viewport'});
    for (const name of ['states', 'areas', 'outlines', 'legislative', 'circles', 'badges', 'selection', 'labels']) {
      state.layers[name] = node('g', {'class': 'map-' + name});
      viewport.appendChild(state.layers[name]);
    }
    svg.appendChild(viewport);
    plot.appendChild(svg);

    const tooltip = document.createElement('div');
    tooltip.className = 'map-tooltip';
    tooltip.hidden = true;
    plot.appendChild(tooltip);
    plot.appendChild(legendCard(view));
    plot.appendChild(locatorCard(state, shapes));
    host.appendChild(plot);

    state.svg = svg;
    state.viewport = viewport;
    state.tooltip = tooltip;
    state.plot = plot;
    wire(host, svg);
    return state;
  }

  /* Boards 6 and M13 (console redesign build 06, fix round 1): two groups,
     "Amount (heat)" with the ramp and the view's own range under it, and
     "Donors (circles)". What the heat measures, the area it is scaled to and
     the logarithmic scale ride on the card's title and on screen-reader text;
     `.map-legend-range` keeps the full sentence the render suite reads. A view
     that passes `kinds` ({kind, label, total, off, colour}) gets its kind
     toggles (`.layer`, the old line under the toolbar) as a third group. */
  function legendCard(view, domain, scope = 'this view') {
    const raised = view.direction === 'raised';
    const card = document.createElement('div');
    card.className = 'map-legend-card';
    const heat = document.createElement('div');
    heat.className = 'map-legend-group map-heat-legend';
    const heatTitle = document.createElement('span');
    heatTitle.className = 'map-legend-title';
    heatTitle.textContent = 'Amount (heat)';
    const ramp = document.createElement('div');
    ramp.className = 'map-ramp';
    for(const color of TEAL_RAMP){const swatch=document.createElement('i');swatch.style.backgroundColor=color;ramp.append(swatch);}
    const stops = domain || rangeFor(view);
    const positive = stops[1] > 0;
    const values = document.createElement('span');
    values.className = 'map-legend-values';
    if (positive) {
      const low = document.createElement('span'), high = document.createElement('span');
      low.className = 'map-legend-low'; high.className = 'map-legend-high';
      low.textContent = money(stops[0]); high.textContent = money(stops[1]);
      values.appendChild(low);
      values.appendChild(document.createTextNode(' – '));
      values.appendChild(high);
    } else {
      values.textContent = 'No positive amount';
    }
    const scaled = (positive ? scope : 'No positive amount on this layer') + ' · logarithmic scale';
    const keyText = 'Rose: net reversal · Unfilled: no mapped amount';
    card.title = 'Heat: net amount, ' + scaled + '. ' + keyText + '.';
    const scopeLabel = document.createElement('span');
    scopeLabel.className = 'map-legend-scope sr-only';
    scopeLabel.textContent = scaled;
    const range = document.createElement('span');
    range.className = 'map-legend-range sr-only';
    range.textContent = positive
      ? money(stops[0]) + ' – ' + money(stops[1]) + ' · ' + scope
      : 'No positive amount on this layer · ' + scope;
    range.textContent += ' · logarithmic scale';
    heat.appendChild(heatTitle);
    heat.appendChild(ramp);
    heat.appendChild(values);
    heat.appendChild(scopeLabel);
    heat.appendChild(range);
    const key = document.createElement('span');
    key.className = 'map-legend-key sr-only';
    key.textContent = keyText;
    heat.appendChild(key);
    card.appendChild(heat);
    if (raised && view.layer !== 'sld') {
      const donors = document.createElement('div');
      donors.className = 'map-legend-group';
      const title = document.createElement('span');
      title.className = 'map-legend-title';
      title.textContent = 'Donors (circles)';
      donors.appendChild(title);
      const swatches = document.createElement('div');
      swatches.className = 'map-legend-swatches';
      for (const bucket of DONOR_BUCKETS) {
        const cell = document.createElement('span');
        cell.className = 'map-legend-swatch';
        const dot = node('svg', {width: 26, height: 26, viewBox: '0 0 26 26'});
        dot.appendChild(node('circle', {cx: 13, cy: 13, r: bucket.radius, 'class': 'map-legend-dot'}));
        cell.appendChild(dot);
        const caption = document.createElement('small');
        caption.textContent = bucket.label;
        cell.appendChild(caption);
        swatches.appendChild(cell);
      }
      donors.appendChild(swatches);
      card.appendChild(donors);
    }
    if (Array.isArray(view.kinds) && view.kinds.length) {
      const kinds = document.createElement('div');
      kinds.className = 'map-legend-group map-legend-kinds';
      const title = document.createElement('span');
      title.className = 'map-legend-title';
      title.textContent = 'Shown';
      kinds.appendChild(title);
      const list = document.createElement('div');
      list.className = 'map-legend-toggles';
      for (const kind of view.kinds) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'layer' + (kind.off ? ' off' : '');
        button.dataset.layer = kind.kind;
        button.setAttribute('aria-pressed', String(!kind.off));
        button.title = (kind.off ? 'Show ' : 'Hide ') + kind.label + ' on the map';
        const dot = document.createElement('span');
        dot.className = 'dot';
        if (kind.colour) dot.style.background = kind.colour;
        button.appendChild(dot);
        button.appendChild(document.createTextNode(kind.label + ' · ' + money(kind.total) + (kind.off ? ' · off' : '')));
        list.appendChild(button);
      }
      kinds.appendChild(list);
      card.appendChild(kinds);
    }
    return card;
  }

  function locatorCard(state, shapes) {
    const card = node('svg', {'class': 'map-locator',
                              viewBox: '0 0 ' + state.width + ' ' + state.height,
                              role: 'button', tabindex: '0',
                              'aria-label': 'Zoom back out to the whole country'});
    for (const key of Object.keys(shapes.states || {})) {
      card.appendChild(node('path', {d: (shapes.states[key] || {}).path, 'class': 'map-locator-state'}));
    }
    card.appendChild(node('rect', {'class': 'map-locator-window', x: 0, y: 0,
                                   width: state.width, height: state.height}));
    card.hidden = true;
    return card;
  }

  /* ------------------------------------------------------------- the layers */

  function regionOfState(body, key) {
    for (const row of body.by_region || []) {
      if ((row.states || []).indexOf(key) >= 0) return row.key;
    }
    return '';
  }

  /* A kind the reader has switched off is subtracted from a number the SERVER
     computed; the browser never re-does the geography. */
  function heatAmount(row, view) {
    const off = view.off || new Set();
    if (!off.size) return row.amount || 0;
    let total = 0;
    for (const kind of Object.keys(row.kinds || {})) {
      if (!off.has(kind)) total += row.kinds[kind];
    }
    return total;
  }

  function amountsFor(view) {
    const districts = new Map(), states = new Map();
    const body = view.body || {};
    const off = view.off || new Set();
    for (const kind of body.kinds || []) {
      if (off.has(kind.kind)) continue;
      if (view.direction === 'spent' && view.layer === 'district' && kind.kind !== 'candidates') continue;
      for (const row of kind.districts || []) {
        const senate = /-SEN$/.test(row.key);
        if (view.direction === 'spent' && view.layer === 'district' && senate) continue;
        const table = senate ? states : districts;
        const key = senate ? row.key.slice(0, 2) : row.key;
        table.set(key, (table.get(key) || 0) + (row.amount || 0));
      }
      for (const row of kind.states || []) {
        if (view.direction === 'spent' && view.layer === 'district') continue;
        states.set(row.key, (states.get(row.key) || 0) + (row.amount || 0));
      }
    }
    return {districts: districts, states: states};
  }

  function focusBounds(view) {
    const focus = view.focus, shapes = view.shapes || {};
    if (!focus) return null;
    if (focus.kind === 'state') return shapes.states?.[focus.key]?.bbox;
    if (focus.kind === 'district') return shapes.districts?.[focus.key]?.bbox;
    if (focus.kind === 'sld') return view.sldBundle?.districts.find(d =>
      `${view.sldBundle.state}-${view.chamber}-${d.id}` === focus.key)?.bbox;
    if (focus.kind === 'zip') {
      const point = view.points?.points?.[focus.key];
      return point ? [point[0], point[1], point[0], point[1]] : null;
    }
    const boxes = (view.body?.by_region?.find(row=>row.key===focus.key)?.states || [])
      .map(key=>shapes.states?.[key]?.bbox).filter(Boolean);
    return boxes.length ? [Math.min(...boxes.map(b=>b[0])), Math.min(...boxes.map(b=>b[1])),
      Math.max(...boxes.map(b=>b[2])), Math.max(...boxes.map(b=>b[3]))] : null;
  }

  function activeHeatFocus(view, bounds) {
    // A comparison-bar selection keeps the countrywide scale for like-for-like inspection.
    if(view.comparisonScale)return null;
    const box = focusBounds(view);
    // Inspecting one ZIP/seat must not flatten every other visible place to
    // the same color. Broad state/region focus still scopes its local range.
    if (view.focus && (!['state','region'].includes(view.focus.kind) || view.focus.kind === view.layer)) return null;
    // Panning away from a selection makes the visible map authoritative.
    return view.focus && (!bounds || !box || intersects(box, bounds)) ? view.focus : null;
  }

  function domainFor(view, bounds) {
    const body = view.body || {};
    const shapes = view.shapes || {}, focus = activeHeatFocus(view, bounds);
    const selectedBox = focus ? focusBounds(view) : null;
    const focusStates = focus?.kind === 'state' ? [focus.key]
      : focus?.kind === 'region' ? (body.by_region || []).find(row=>row.key===focus.key)?.states : null;
    let entries;
    if (view.layer === 'sld') {
      const geometry = new Map((view.sldBundle?.districts || []).map(d=>[d.id,d]));
      entries = (view.sldRows || []).map(row=>({...row, states:[view.sldBundle?.state], bbox:geometry.get(row.id)?.bbox}));
    } else if (view.layer === 'zip') {
      entries = view.off?.has('contributors') ? [] : (body.by_zip || []).map(row=>{
        const point = view.points?.points?.[row.zip];
        return {key:row.zip, amount:row.amount, states:[point?.[3] || row.state],
          bbox:point ? [point[0],point[1],point[0],point[1]] : null};
      });
    } else if (view.layer === 'state' || view.layer === 'region') {
      entries = (body[view.layer === 'state' ? 'by_state' : 'by_region'] || []).map(row=>{
        const states = view.layer === 'state' ? [row.key] : row.states || [];
        return {key:row.key, amount:heatAmount(row,view), states,
          boxes:states.map(key=>shapes.states?.[key]?.bbox).filter(Boolean)};
      });
    } else {
      const amounts = amountsFor(view);
      entries = [...amounts.districts].map(([key,amount])=>
        ({key,amount,states:[key.slice(0,2)],bbox:shapes.districts?.[key]?.bbox}));
      if (view.direction !== 'spent') entries.push(...[...amounts.states].map(([key,amount])=>
        ({key,amount,states:[key],bbox:shapes.states?.[key]?.bbox})));
    }
    return heatDomain(entries.filter(entry=>{
      const boxes = entry.boxes || (entry.bbox ? [entry.bbox] : []);
      if (bounds && boxes.length && !boxes.some(box=>intersects(box,bounds))) return false;
      if (!focus) return true;
      if (focusStates) return entry.states.some(state=>focusStates.includes(state));
      if (focus.kind === view.layer) return entry.key === focus.key;
      return !selectedBox || boxes.some(box=>intersects(box,selectedBox));
    }).map(entry=>entry.amount));
  }

  // Repaint fills and the legend together, retaining the SVG and every polygon.
  // Navigation calls this only once the gesture/fly settles, never per frame.
  function updateHeat(state) {
    const view = state.view, bounds = viewportBox(state);
    state.domain = rangeFor(view, bounds);
    for (const shape of state.svg.querySelectorAll('[data-heat-amount]')) {
      shape.style.fill = heatColour(Number(shape.dataset.heatAmount), null, state.domain)
        || shape.dataset.emptyFill || 'none';
    }
    const focus = activeHeatFocus(view, bounds);
    const scope = focus ? (focus.label || focus.key) + ' · visible area'
      : view.layer === 'sld' && view.sldBundle ? view.sldBundle.state + ' · visible area' : 'visible map';
    const legend = state.plot.querySelector('.map-legend-card');
    if (legend) legend.replaceWith(legendCard(view, state.domain, scope));
    if (typeof view.onLegend === 'function') view.onLegend(state.plot.querySelector('.map-legend-card'));
    // Lane P: the layer overlays (map-evidence.js) redraw when the view settles.
    if (state.decor && typeof state.decor.onSettle === 'function') { try { state.decor.onSettle(); } catch (error) { /* overlays are optional */ } }
  }

  /* Build 06, two-cycle compare (board M13). Two maps side by side must share
     ONE heat scale or their colours cannot be compared, so a view that names a
     `companion` (the other cycle's view) takes the union of both ranges over
     the same visible area. Both maps sit at the same zoom and pan, so the two
     unions are the same and each map paints to the same legend. */
  function unionDomain(first, second) {
    const a = first || [0, 0], b = second || [0, 0];
    if (!(a[1] > 0)) return b.slice ? b.slice() : [b[0], b[1]];
    if (!(b[1] > 0)) return a.slice ? a.slice() : [a[0], a[1]];
    return [Math.min(a[0], b[0]), Math.max(a[1], b[1])];
  }

  function rangeFor(view, bounds) {
    const own = domainFor(view, bounds);
    const other = view && view.companion;
    if (!other) return own;
    return unionDomain(own, domainFor(Object.assign({}, other, {companion: null, layer: view.layer,
      off: view.off, direction: view.direction, focus: view.focus, comparisonScale: view.comparisonScale}), bounds));
  }

  function drawPolygons(state, view, shapes) {
    const heat = amountsFor(view);
    const domain = state.domain || rangeFor(view);
    const colour = value => heatColour(value, null, domain);
    state.layers.states.textContent = '';
    state.layers.areas.textContent = '';
    state.layers.outlines.textContent = '';
    const body = view.body || {};
    const byState = new Map((body.by_state || []).map(row => [row.key, row]));
    const byRegion = new Map((body.by_region || []).map(row => [row.key, row]));
    const regionBoxes = new Map();
    for (const key of Object.keys(shapes.states || {})) {
      const entry = shapes.states[key] || {};
      const base = node('path', {'class': 'map-shape map-state-base', d: entry.path, 'data-key': key});
      base.style.fill = ['zip', 'sld'].includes(view.layer) ? '#f1f2f1' : (view.layer === 'district' && view.direction === 'spent'
        ? '' : colour(heat.states.get(key) || 0)) || TEAL_RAMP[0];
      if (!['zip','sld'].includes(view.layer) && !(view.layer === 'district' && view.direction === 'spent')) {
        base.dataset.heatAmount = heat.states.get(key) || 0; base.dataset.emptyFill = TEAL_RAMP[0];
      }
      base.setAttribute('data-bbox', (entry.bbox || []).join(' '));
      state.layers.states.appendChild(base);
      state.layers.outlines.appendChild(node('path', {'class': 'map-outline', d: entry.path}));
      const region = regionOfState(body, key);
      if (region && entry.bbox) {
        const found = regionBoxes.get(region);
        regionBoxes.set(region, found ? [Math.min(found[0], entry.bbox[0]), Math.min(found[1], entry.bbox[1]),
                                         Math.max(found[2], entry.bbox[2]), Math.max(found[3], entry.bbox[3])]
                                      : entry.bbox.slice());
      }
    }
    // The district paths are ALWAYS in the document, painted or not: the map
    // browser suite reads a computed fill off path.map-district, and a reader
    // on the state layer still needs the district under the pointer to exist
    // when they zoom into it.
    for (const key of Object.keys(shapes.districts || {})) {
      const entry = shapes.districts[key] || {};
      const amount = heat.districts.get(key) || 0;
      const path = node('path', {'class': 'map-shape map-district', d: entry.path,
                                 'data-key': key, 'data-district': key, 'data-kind': 'district',
                                 tabindex: '-1', 'data-bbox': (entry.bbox || []).join(' ')});
      path.style.fill = amount !== 0 ? colour(amount) : 'none';
      path.dataset.heatAmount = amount;
      path.style.display = view.layer === 'district' ? '' : 'none';
      const title = node('title', {});
      title.textContent = key + '\n' + (amount !== 0 ? money(amount) + (amount < 0 ? ' · net reversal' : '') : 'No net amount on this map');
      path.appendChild(title);
      state.layers.areas.appendChild(path);
    }
    if (view.layer === 'state' || view.layer === 'region') {
      for (const key of Object.keys(shapes.states || {})) {
        const entry = shapes.states[key] || {};
        const region = regionOfState(body, key);
        const row = (view.layer === 'state' ? byState.get(key) : byRegion.get(region)) || {};
        const amount = heatAmount(row, view);
        const name = view.layer === 'state' ? (row.name || key) : (region || 'Unplaced');
        const shapeKey = view.layer === 'state' ? key : region;
        const bbox = view.layer === 'state' ? (entry.bbox || []) : (regionBoxes.get(region) || entry.bbox || []);
        const path = node('path', {'class': 'map-shape map-area', d: entry.path,
                                   'data-key': shapeKey, 'data-kind': view.layer,
                                   'data-name': name, tabindex: '-1',
                                   'data-bbox': bbox.join(' ')});
        path.style.fill = colour(amount) || TEAL_RAMP[0];
        path.dataset.heatAmount = amount; path.dataset.emptyFill = TEAL_RAMP[0];
        const title = node('title', {});
        title.textContent = name + '\n' + money(amount)
          + (row.contributors ? ' · ' + number(row.contributors) + ' donors' : '');
        path.appendChild(title);
        state.layers.areas.appendChild(path);
      }
    }
    state.layers.areas.classList.toggle('on-district', view.layer === 'district');
    state.layers.areas.classList.toggle('on-zip', view.layer === 'zip');
  }

  function drawLegislative(host, state, view) {
    const group = state.layers.legislative;
    if (view.layer !== 'sld' || !view.sldBundle) { group.textContent = ''; return; }
    const box = viewportBox(state), domain = state.domain || rangeFor(view);
    const amounts = new Map((view.sldRows || []).map(row => [row.id, row.amount]));
    const wanted = new Set();
    for (const entry of view.sldBundle.districts || []) {
      if (!intersects(entry.bbox, box)) continue;
      const key = `${view.sldBundle.state}-${view.chamber}-${entry.id}`;
      wanted.add(key);
      let path = group.querySelector(`[data-key="${escapeKey(key)}"]`);
      if (!path) {
        path = node('path', {'class':'map-shape map-sld', d:entry.path, 'data-kind':'sld',
          'data-key':key, 'data-name':entry.name, 'data-bbox':entry.bbox.join(' '), tabindex:'-1'});
        path.appendChild(node('title'));
        group.appendChild(path);
      }
      const amount = amounts.get(entry.id);
      path.style.fill = heatColour(amount, null, domain) || '#f3f1ed';
      path.dataset.heatAmount = amount || 0; path.dataset.emptyFill = '#f3f1ed';
      path.querySelector('title').textContent = `${view.sldBundle.state} · ${entry.name}\n`
        + (amount == null ? 'No mapped receipt amount' : money(amount) + ' · ZIP internal-point estimate');
    }
    for (const path of [...group.children]) if (!wanted.has(path.dataset.key)) path.remove();
    api.select(host, view.focus);
  }

  /* ------------------------------------------------------------ the circles */

  function circlePoints(view) {
    const body = view.body || {};
    const table = (view.points || {}).points || {};
    const placed = [], missing = [];
    if (view.direction !== 'raised' || view.off?.has('contributors')) return {placed, missing};
    for (const row of body.by_zip || []) {
      const entry = table[row.zip];
      if (!entry) { missing.push(row); continue; }
      placed.push({zip: row.zip, city: row.city || '', state: row.state || entry[3] || '',
                   amount: row.amount || 0, rows: row.rows || 0,
                   contributors: row.contributors || 0, x: entry[0], y: entry[1]});
    }
    return {placed: placed, missing: missing};
  }

  // The drawn zoom and offset: the reader's zoom and pan inside the frame (the
  // whole canvas, or the room an open Layer control leaves). Every screen-sized
  // thing - circle radii, badge text, the merge distance, the visible box - reads
  // these; the reader's own state.scale/x/y keep their meaning inside the frame.
  const zoomOf = state => state.scale * (state.frame || IDENTITY).scale;
  const offsetOf = state => {
    const frame = state.frame || IDENTITY;
    return {x: frame.x + frame.scale * state.x, y: frame.y + frame.scale * state.y};
  };
  function viewportBox(state) {
    const inverse = 1 / zoomOf(state), at = offsetOf(state);
    return [(0 - at.x) * inverse, (0 - at.y) * inverse,
            (state.width - at.x) * inverse, (state.height - at.y) * inverse];
  }

  function drawCircles(host, state, view) {
    const group = state.layers.circles;
    group.textContent = '';
    state.layers.badges.textContent = '';
    state.layers.badges.style.visibility = 'visible';
    state.circleNodes = [];
    state.badgeNodes = [];
    const all = state.points || [];
    if (!all.length || view.layer === 'sld') return;
    let wanted = all;
    if (view.layer === 'zip') {
      // Every visible ZIP participates; offscreen points stay in the data and
      // totals, but do not cost DOM work at a deep zoom.
      wanted = state.pointIndex ? pointsInBox(state.pointIndex, viewportBox(state)) : all;
    } else if (state.scale < DETAIL_SCALE) {
      wanted = state.coarsePoints || all;
    } else {
      const box = viewportBox(state);
      wanted = state.pointIndex ? pointsInBox(state.pointIndex, box) : all.filter(point => point.x >= box[0] && point.x <= box[2]
        && point.y >= box[1] && point.y <= box[3]);
    }
    // Screen sizes (the merge distance, radii, badge text) follow the drawn zoom, frame included.
    const zoom = zoomOf(state);
    let spacing = MERGE_PIXELS;
    let merged = mergeCircles(wanted, zoom, spacing);
    while (merged.length > MAX_CIRCLES) {
      spacing *= 1.4;
      merged = mergeCircles(wanted, zoom, spacing);
    }
    const zipDomain = state.domain || rangeFor(view);
    merged.forEach((point, index) => {
      const radius = donorRadius(point.contributors);
      const circle = node('circle', {'class': 'map-circle', cx: point.x.toFixed(2),
                                     cy: point.y.toFixed(2), r: radius, tabindex: '-1',
                                     'data-kind': 'zip', 'data-key': point.member.zip,
                                     'data-places': point.places, 'data-amount':point.amount,
                                     'data-contributors':point.contributors});
      circle.style.setProperty('--circle-r', radius + 'px');
      if (view.layer === 'zip') {
        circle.style.fill = heatColour(point.member.amount, null, zipDomain) || '#f3f1ed';
        circle.dataset.heatAmount = point.member.amount; circle.dataset.emptyFill = '#f3f1ed';
      }
      circle.setAttribute('r', (radius / zoom).toFixed(4));
      state.circleNodes.push({node:circle, radius});
      const title = node('title', {});
      title.textContent = point.places > 1
        ? number(point.places) + ' ZIP codes · ' + number(point.contributors)
          + ' donors · ' + money(point.amount)
        : point.member.zip + (point.member.city ? ' · ' + point.member.city : '')
          + (point.member.state ? ', ' + point.member.state : '')
          + ' · ' + number(point.contributors) + ' donors · ' + money(point.amount);
      circle.appendChild(title);
      group.appendChild(circle);
      if (point.contributors >= 4) {
        const badge = node('text', {'class': 'map-circle-count', x: point.x.toFixed(2),
                                    y: point.y.toFixed(2), 'text-anchor': 'middle',
                                    'dominant-baseline': 'central'});
        badge.textContent = String(point.contributors);
        badge.style.fontSize = (8 / zoom).toFixed(4) + 'px';
        state.badgeNodes.push(badge);
        state.layers.badges.appendChild(badge);
      }
    });
    // Re-clustering is part of navigation: restarting arrival animations for
    // thousands of circles on every wheel event stalls subsequent frames.
    state.circleScale = zoom;
    if (view.focus) api.select(host, view.focus);
  }

  /* -------------------------------------------------------- transform + fly */

  function applyTransform(state) {
    const started = performance.now();
    // Keep some of the national canvas reachable, even after a long drag.
    state.x = Math.max(state.width * (1-state.scale)-40, Math.min(40,state.x));
    state.y = Math.max(state.height * (1-state.scale)-40, Math.min(40,state.y));
    // One transform still: the reader's zoom and pan drawn inside the frame.
    const zoom = zoomOf(state), at = offsetOf(state);
    state.viewport.setAttribute('transform',
      'translate(' + at.x.toFixed(3) + ' ' + at.y.toFixed(3) + ') scale('
      + zoom.toFixed(5) + ')');
    // An inherited zoom CSS variable invalidated every map descendant's
    // style on each frame. Only circle radii and badge text need resizing.
    if (state.circleScale !== zoom) {
      for (const entry of state.circleNodes || [])
        entry.node.setAttribute('r', (entry.radius / zoom).toFixed(4));
      // Re-laying out thousands of numeric labels dominates wheel frames.
      // Restore their exact size with the settled cluster pass instead.
      state.layers.badges.style.visibility = 'hidden';
      state.circleScale = zoom;
    }
    // Layout profiling is opt-in. Forcing it during every live wheel/pan frame
    // defeats the browser's frame batching and makes instrumentation the stall.
    if (window.MENTAT_MAP_PROFILE) void state.viewport.getBoundingClientRect().width;
    state.frames.push(performance.now() - started);
    if (state.frames.length > 400) state.frames.shift();
    const locator = state.plot.querySelector('.map-locator');
    if (locator) {
      locator.toggleAttribute('hidden', state.scale <= LOCATOR_SCALE);
      const box = viewportBox(state);
      const window_ = locator.querySelector('.map-locator-window');
      if (window_) {
        window_.setAttribute('x', box[0].toFixed(2));
        window_.setAttribute('y', box[1].toFixed(2));
        window_.setAttribute('width', Math.max(1, box[2] - box[0]).toFixed(2));
        window_.setAttribute('height', Math.max(1, box[3] - box[1]).toFixed(2));
      }
    }
    if (!state.silent && state.view && typeof state.view.onView === 'function') {
      // `target` is where a fly in progress is going, so a follower flying
      // to the same place is not stopped by its leader's frames.
      state.view.onView({scale: state.scale, x: state.x, y: state.y,
                         target: state.animation ? state.flightTarget : null});
    }
  }

  function redrawCircles(host, state) {
    clearTimeout(state.circleTimer);
    // Recluster after the wheel gesture settles, not between adjacent events.
    state.circleTimer = setTimeout(() => {
      const current = views.get(host);
      if (current) {
        updateHeat(current);
        drawCircles(host, current, current.view || {});
        drawLegislative(host, current, current.view || {});
      }
    }, 160);
  }

  function queueTransform(state) {
    if (state.transformFrame) return;
    state.transformFrame = requestAnimationFrame(() => {
      state.transformFrame = 0;
      applyTransform(state);
    });
  }

  function flyTo(host, state, target) {
    stopFlight(state);
    const from = {scale: state.scale, x: state.x, y: state.y};
    const to = {scale: clampScale(target.scale), x: target.x, y: target.y};
    state.flightTarget = to;
    if (state.reduced) {
      state.scale = to.scale; state.x = to.x; state.y = to.y;
      applyTransform(state);
      updateHeat(state);
      drawCircles(host, state, state.view || {});
      drawLegislative(host, state, state.view || {});
      return;
    }
    const started = performance.now();
    const ease = at => 1 - Math.pow(1-at,3);
    const step = now => {
      // A frame's timestamp can precede `started` (it is the frame's start,
      // taken before this call); a negative elapsed time eased the scale
      // below zero and drew circles with a negative radius. Clamp at 0.
      const at = Math.max(0, Math.min(1, (now - started) / FLY_MS));
      const eased = ease(at);
      state.scale = from.scale + (to.scale - from.scale) * eased;
      state.x = from.x + (to.x - from.x) * eased;
      state.y = from.y + (to.y - from.y) * eased;
      applyTransform(state);
      if (at < 1) { state.animation = requestAnimationFrame(step); return; }
      state.animation = 0;
      state.flightTarget = null;
      updateHeat(state);
      drawCircles(host, state, state.view || {});
      drawLegislative(host, state, state.view || {});
    };
    state.animation = requestAnimationFrame(step);
  }

  function zoomAbout(host, state, factor, point) {
    stopFlight(state);
    const next = clampScale(state.scale * factor);
    if (next === state.scale) return;
    // `point` is in the canvas's units; the zoom and pan it moves live inside the frame.
    const frame = state.frame || IDENTITY;
    const at = point ? {x: (point.x - frame.x) / frame.scale, y: (point.y - frame.y) / frame.scale}
      : {x: state.width / 2, y: state.height / 2};
    const mapX = (at.x - state.x) / state.scale;
    const mapY = (at.y - state.y) / state.scale;
    state.scale = next;
    state.x = at.x - mapX * next;
    state.y = at.y - mapY * next;
    queueTransform(state);
    redrawCircles(host, state);
  }

  function stopFlight(state) {
    if (state.animation) cancelAnimationFrame(state.animation);
    state.animation = 0;
    state.flightTarget = null;
    clearTimeout(state.circleTimer);
  }

  /* Move the frame (api.frame), easing as a fly does; the reader's own zoom and
     pan are not touched. A map's first frame lands at once (a map drawn with its
     Layer control already open does not slide), as does any frame under reduced
     motion. Fills, circles and the overlays settle when the frame does. */
  function setFrame(host, state, target) {
    const first = !state.framed;
    state.framed = true;
    const near = (a, b) => Math.abs(a.scale - b.scale) < 1e-4 && Math.abs(a.x - b.x) < 0.05 && Math.abs(a.y - b.y) < 0.05;
    if (state.frameAnimation) {
      if (state.frameTarget && near(state.frameTarget, target)) return;   // already easing there
      cancelAnimationFrame(state.frameAnimation);
      state.frameAnimation = 0;
    } else if (near(state.frame || IDENTITY, target)) return;
    const from = state.frame || IDENTITY;
    state.frameTarget = target;
    const settle = () => {
      state.frameAnimation = 0;
      state.frameTarget = null;
      state.frame = target.scale === 1 && target.x === 0 && target.y === 0 ? IDENTITY
        : Object.freeze({scale: target.scale, x: target.x, y: target.y});
      applyTransform(state);
      updateHeat(state);
      drawCircles(host, state, state.view || {});
      drawLegislative(host, state, state.view || {});
    };
    if (first || state.reduced) { settle(); return; }
    const started = performance.now();
    const step = now => {
      const at = Math.max(0, Math.min(1, (now - started) / FLY_MS));
      if (at >= 1) { settle(); return; }
      const eased = 1 - Math.pow(1 - at, 3);
      state.frame = {scale: from.scale + (target.scale - from.scale) * eased,
                     x: from.x + (target.x - from.x) * eased, y: from.y + (target.y - from.y) * eased};
      applyTransform(state);
      state.frameAnimation = requestAnimationFrame(step);
    };
    state.frameAnimation = requestAnimationFrame(step);
  }

  function pointIn(state, event) {
    const matrix = state.svg.getScreenCTM();
    if (!matrix) return {x: state.width / 2, y: state.height / 2};
    const point = state.svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const mapped = point.matrixTransform(matrix.inverse());
    return {x: mapped.x, y: mapped.y};
  }

  /* ------------------------------------------------------------- the wiring */

  function wire(host, svg) {
    // Cooperative zoom: normal scrolling belongs to the page. Trackpad pinch
    // arrives as ctrl+wheel; the same modifier is discoverable in the help.
    svg.addEventListener('wheel', event => {
      const state = views.get(host);
      if (!state || !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 120 : 1);
      zoomAbout(host, state, Math.exp(-Math.max(-240,Math.min(240,delta))*.0014), pointIn(state, event));
    }, {passive: false});

    let dragging = null;
    svg.addEventListener('pointerdown', event => {
      const state = views.get(host);
      if (!state || event.button !== 0) return;
      stopFlight(state);
      hide(state);
      const matrix = svg.getScreenCTM();
      // A screen pixel in the reader's own units: the canvas's scale, then the frame's.
      dragging = {id: event.pointerId, x: event.clientX, y: event.clientY,
                  fromX: state.x, fromY: state.y, moved: false,
                  unit: (matrix?.a ? 1 / matrix.a : 1) / (state.frame || IDENTITY).scale};
    });
    svg.addEventListener('pointermove', event => {
      const state = views.get(host);
      if (!state) return;
      if (!dragging || dragging.id !== event.pointerId) { hover(state, event); return; }
      const dx = event.clientX - dragging.x;
      const dy = event.clientY - dragging.y;
      if (!dragging.moved && Math.sqrt(dx * dx + dy * dy) < DRAG_THRESHOLD) return;
      if (!dragging.moved) {
        dragging.moved = true;
        svg.classList.add('panning');
        try { svg.setPointerCapture(event.pointerId); } catch (error) { /* still pans */ }
      }
      const unit = dragging.unit;
      state.x = dragging.fromX + dx * unit;
      state.y = dragging.fromY + dy * unit;
      queueTransform(state);
    });
    const release = () => {
      if (!dragging) return;
      const moved = dragging.moved;
      try { svg.releasePointerCapture(dragging.id); } catch (error) { /* never captured */ }
      dragging = null;
      svg.classList.remove('panning');
      if (!moved) return;
      // A drag must not also select. The flag is cleared on the next tick, so a
      // click DISPATCHED at the element (with no pointerdown before it) still
      // selects, which is how the map browser suite drives this view.
      svg.dataset.dragged = '1';
      const state = views.get(host);
      if (state) redrawCircles(host, state);
      setTimeout(() => { delete svg.dataset.dragged; }, 0);
    };
    svg.addEventListener('pointerup', release);
    svg.addEventListener('pointercancel', release);
    svg.addEventListener('pointerleave', () => {
      const state = views.get(host);
      if (state) hide(state);
    });

    svg.addEventListener('click', event => {
      if (svg.dataset.dragged || event.detail > 1) return;
      const state = views.get(host);
      const shape = event.target.closest ? event.target.closest('[data-kind]') : null;
      if (!state || !shape || !state.view || typeof state.view.onSelect !== 'function') return;
      state.view.onSelect(shape.getAttribute('data-kind'), shape.getAttribute('data-key'));
    });
    svg.addEventListener('dblclick', event => {
      const state = views.get(host);
      const shape = event.target.closest ? event.target.closest('[data-bbox]') : null;
      if (!state || !shape) return;
      const bbox = (shape.getAttribute('data-bbox') || '').split(' ').map(Number);
      if (bbox.length === 4) flyTo(host, state, flyToTransform(bbox, state.width, state.height, 40));
    });
    svg.addEventListener('keydown', event => {
      const state = views.get(host);
      if (!state) return;
      const step = 40;
      const moves = {ArrowLeft: [step, 0], ArrowRight: [-step, 0],
                     ArrowUp: [0, step], ArrowDown: [0, -step]};
      if (moves[event.key]) {
        event.preventDefault();
        stopFlight(state);
        state.x += moves[event.key][0];
        state.y += moves[event.key][1];
        applyTransform(state);
        redrawCircles(host, state);
        return;
      }
      if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomAbout(host, state, 1.4); }
      else if (event.key === '-' || event.key === '_') { event.preventDefault(); zoomAbout(host, state, 1 / 1.4); }
      else if (event.key === '0') { event.preventDefault(); api.reset(host); }
      else if (event.key === 'Enter' && document.activeElement
               && document.activeElement.getAttribute
               && document.activeElement.getAttribute('data-bbox')) {
        event.preventDefault();
        const bbox = document.activeElement.getAttribute('data-bbox').split(' ').map(Number);
        if (bbox.length === 4) flyTo(host, state, flyToTransform(bbox, state.width, state.height, 40));
      }
    });

    const locator = host.querySelector('.map-locator');
    if (locator) locator.addEventListener('click', () => api.reset(host));
  }

  function hover(state, event) {
    const shape = event.target.closest ? event.target.closest('[data-kind]') : null;
    if (!shape) { hide(state); return; }
    const view = state.view || {};
    const body = view.body || {};
    const raised = view.direction === 'raised';
    const total = state.shareBase || 0;
    const kind = shape.getAttribute('data-kind');
    const key = shape.getAttribute('data-key');
    let name = key, amount = 0, donors = 0;
    const lines = [];
    if (kind === 'zip') {
      const point = (state.points || []).filter(item => item.zip === key)[0];
      if (point) {
        name = point.zip + (point.city ? ' · ' + point.city : '')
          + (point.state ? ', ' + point.state : '');
        amount = point.amount; donors = point.contributors;
        lines.push(number(donors) + ' donors · ' + money(amount));
        if (Number(shape.dataset.places) > 1) {
          name = shape.dataset.places + ' ZIPs · largest ZIP ' + key;
          lines[0] = number(shape.dataset.contributors) + ' contributors across ZIPs · ' + money(Number(shape.dataset.amount));
          lines.push('Heat: largest ZIP · click opens ' + key);
        }
      }
    } else if (kind === 'sld') {
      const row = (view.sldRows || []).find(item => item.key === key);
      name = shape.getAttribute('data-name') || key;
      lines.push(row ? money(row.amount) + ' · ZIP internal-point estimate' : 'No mapped receipt amount');
      lines.push('2024 Census boundaries · ' + (view.chamber === 'upper' ? 'upper' : 'lower') + ' chamber');
    } else {
      const table = kind === 'state' ? body.by_state : (kind === 'region' ? body.by_region : null);
      const row = table ? table.filter(item => item.key === key)[0] : null;
      if (row) {
        name = row.name || row.key;
        amount = heatAmount(row, view);
        donors = row.contributors || 0;
      } else {
        amount = amountsFor(view).districts.get(key) || 0;
      }
      lines.push(money(amount) + (donors ? ' · ' + number(donors) + ' donors' : ''));
      if (total > 0 && amount>=0 && amount<=total) {
        lines.push((amount / total * 100).toFixed(1) + shareOf(body, raised));
      } else {
        lines.push(amount < 0 ? 'Reversal: nets below zero, no share' : 'Net share unavailable');
      }
    }
    const tooltip = state.tooltip;
    tooltip.textContent = '';
    const heading = document.createElement('strong');
    heading.textContent = name;
    tooltip.appendChild(heading);
    for (const line of lines) {
      const row = document.createElement('span');
      row.textContent = line;
      tooltip.appendChild(row);
    }
    tooltip.hidden = false;
    const box = state.plot.getBoundingClientRect();
    tooltip.style.left = Math.max(8, Math.min(Math.max(8, box.width - 210),
      event.clientX - box.left + 14)) + 'px';
    tooltip.style.top = Math.max(8, event.clientY - box.top - 64) + 'px';
  }

  function hide(state) {
    if (state.tooltip) state.tooltip.hidden = true;
  }

  /* -------------------------------------------------------------- the entry */

  const api = {
    /* Lane P (boards #48, #55): what map-evidence.js draws its layers into -
       the svg, the zoomed viewport and its groups, the card - and the ZIP
       points already placed, read after each render. Nothing here draws. */
    internals: function (host) {
      const state = views.get(host);
      if (!state || !state.svg) return null;
      if (!state.decor) state.decor = {svg: state.svg, viewport: state.viewport, layers: state.layers, plot: state.plot,
        width: state.width, height: state.height, scale: () => zoomOf(state), visibleBox: () => viewportBox(state)};
      state.decor.shapes = (state.view && state.view.shapes) || {};
      state.decor.points = state.points || [];
      return state.decor;
    },

    /* Build once, update in place afterwards. `shapesKey` is the only thing
       that forces a rebuild: a different boundary file, or the card changing
       size, is a different picture. A layer change, a kind toggle, a selection
       and a fresh payload all reuse the same <svg>, which is what keeps the
       reader's zoom. */
    render: function (host, view) {
      if (!host) return null;
      let state = views.get(host);
      const key = String((view.body && view.body.boundary_file) || '');
      if (!state || !state.svg || !host.contains(state.svg) || state.shapesKey !== key) {
        const kept = state ? {scale: state.scale, x: state.x, y: state.y} : null;
        state = build(host, view);
        state.shapesKey = key;
        if (kept && state.shapesKey === key) {
          state.scale = kept.scale; state.x = kept.x; state.y = kept.y;
        }
      }
      const signature = [view.layer, view.direction, [...(view.off || [])].sort().join(',')].join('|');
      const changed = state.body !== view.body || state.shapes !== view.shapes || state.sldBundle !== view.sldBundle
        || state.paintSignature !== signature || state.pointSource !== view.points;
      state.view = view;
      state.domain = rangeFor(view, viewportBox(state));
      if (changed) {
        state.shareBase = shareBaseFor(view);
        const found = circlePoints(view);
        state.points = found.placed;
        state.pointIndex = indexPoints(state.points);
        state.coarsePoints = state.points.slice().sort((a,b) => b.contributors-a.contributors || b.amount-a.amount).slice(0, COARSE_CIRCLES);
        state.missing = found.missing;
        drawPolygons(state, view, view.shapes || {});
        drawCircles(host, state, view);
        drawLegislative(host, state, view);
        const legend = state.plot.querySelector('.map-legend-card');
        if (legend) legend.replaceWith(legendCard(view));
        state.body = view.body; state.shapes = view.shapes; state.pointSource = view.points;
        state.paintSignature = signature;
        state.sldBundle = view.sldBundle;
      }
      api.select(host, view.focus);
      updateHeat(state);
      // Build 06, fix round 1 (R06-01). A re-render does not move the map, so
      // a view marked `quiet` (the second map of a compare) does not announce
      // its transform: echoed, it mirrored its stale transform onto the first
      // map and cancelled the fly Reset view or Find had just started there.
      state.silent = Boolean(view.quiet);
      try { applyTransform(state); } finally { state.silent = false; }
      return {unplaced: state.missing || []};
    },

    /* The selection outline and the dimming, as their own pass: choosing a
       shape must not cost a rebuild, because a rebuild is exactly what threw
       the reader's view away before. */
    select: function (host, focus) {
      const state = views.get(host);
      if (!state || !state.svg) return;
      const chosen = focus && focus.key ? String(focus.key) : '';
      state.svg.classList.toggle('has-selection', Boolean(chosen));
      state.layers.selection.textContent = '';
      const marked = state.svg.querySelectorAll('.chosen');
      for (const shape of marked) shape.classList.remove('chosen');
      if (!chosen) return;
      const matches = state.svg.querySelectorAll('.map-legislative [data-key="' + escapeKey(chosen) + '"], .map-areas [data-key="' + escapeKey(chosen) + '"], '
        + '.map-circles [data-key="' + escapeKey(chosen) + '"]');
      for (const shape of matches) {
        shape.classList.add('chosen');
        if (shape.tagName.toLowerCase() !== 'path') continue;
        state.layers.selection.appendChild(
          node('path', {'class': 'map-selected', d: shape.getAttribute('d')}));
      }
    },

    flyToKey: function (host, key) {
      const state = views.get(host);
      if (!state || !state.svg) return false;
      const district = (state.view.sldBundle?.districts || []).find(d =>
        `${state.view.sldBundle.state}-${state.view.chamber}-${d.id}` === key);
      if (district) {
        flyTo(host, state, flyToTransform(district.bbox, state.width, state.height, 40));
        return true;
      }
      const shape = state.svg.querySelector('[data-key="' + escapeKey(key) + '"][data-bbox]');
      if (shape && shape.getAttribute('data-bbox')) {
        const bbox = shape.getAttribute('data-bbox').split(' ').map(Number);
        if (bbox.length === 4) {
          flyTo(host, state, flyToTransform(bbox, state.width, state.height, 40));
          return true;
        }
      }
      const point = (state.points || []).filter(item => item.zip === String(key))[0];
      if (!point) return false;
      flyTo(host, state, flyToTransform([point.x - 14, point.y - 14, point.x + 14, point.y + 14],
                                        state.width, state.height, 40));
      return true;
    },

    zoom: function (host, factor) {
      const state = views.get(host);
      if (state) zoomAbout(host, state, factor);
    },

    reset: function (host) {
      const state = views.get(host);
      if (state) flyTo(host, state, {scale: 1, x: 0, y: 0});
    },

    /* Boards #48 and #55: fit the whole map into the room `covers` leave - the
       client rects of what floats over the map (PAC evidence's open Layer control
       and the toolbar beside it) - as the frame round the reader's own zoom and
       pan (see frameAround). No covers, or covers clear of the full fit, is the
       full fit. Returns the frame, in the canvas's units. */
    frame: function (host, covers) {
      const state = views.get(host);
      if (!state || !state.svg) return null;
      let target = IDENTITY;
      const rects = (covers || []).filter(rect => rect && rect.width > 0 && rect.height > 0);
      const matrix = rects.length ? state.svg.getScreenCTM() : null;
      if (matrix && matrix.a) {
        const inverse = matrix.inverse(), gap = FRAME_GAP / matrix.a;
        const toCanvas = (x, y) => {
          const point = state.svg.createSVGPoint();
          point.x = x; point.y = y;
          return point.matrixTransform(inverse);
        };
        const blocks = rects.map(rect => {
          const a = toCanvas(rect.left, rect.top), b = toCanvas(rect.right, rect.bottom);
          return [Math.min(a.x, b.x) - gap, Math.min(a.y, b.y) - gap, Math.max(a.x, b.x) + gap, Math.max(a.y, b.y) + gap];
        });
        target = frameAround(regionBoxes((state.view && state.view.shapes) || {}, state.width, state.height),
                             blocks, state.width, state.height, FRAME_PAD);
      }
      setFrame(host, state, target);
      return {scale: target.scale, x: target.x, y: target.y};
    },

    /* The frame now drawn (identity when none), for the suites and captures. */
    framing: function (host) {
      const state = views.get(host);
      const frame = state ? (state.frame || IDENTITY) : null;
      return frame ? {scale: frame.scale, x: frame.x, y: frame.y, animating: Boolean(state.frameAnimation)} : null;
    },

    /* JS transform-update duration, not total paint/frame time. Tests may set
       MENTAT_MAP_PROFILE to include a forced layout; production never does. */
    frames: function (host) {
      const state = views.get(host);
      return state ? state.frames.slice() : [];
    },

    circleCount: function (host) {
      const state = views.get(host);
      return state ? state.layers.circles.querySelectorAll('circle').length : 0;
    },

    unplaced: function (host) {
      const state = views.get(host);
      return state ? (state.missing || []).slice() : [];
    },

    view: function (host) {
      const state = views.get(host);
      return state ? {scale: state.scale, x: state.x, y: state.y} : null;
    },

    animating: function (host) {
      const state = views.get(host);
      return Boolean(state && state.animation);
    },

    /* Build 06: follow another map's zoom and pan. The transform lands in the
       same frame, silently (no onView echo back to the leader); fills and
       circles settle once the gesture does, as they do for a map's own. */
    mirror: function (host, saved) {
      const state = views.get(host);
      if (!state || !state.svg || !saved) return;
      // Already flying to where the leader is going: let the fly land.
      const going = state.flightTarget, wanted = saved.target;
      if (state.animation && going && wanted && going.scale === clampScale(wanted.scale)
          && going.x === wanted.x && going.y === wanted.y) return;
      stopFlight(state);
      state.scale = clampScale(saved.scale);
      state.x = Number(saved.x) || 0;
      state.y = Number(saved.y) || 0;
      state.silent = true;
      try { applyTransform(state); } finally { state.silent = false; }
      redrawCircles(host, state);
    },

    restore: function (host, saved) {
      const state = views.get(host);
      if (!state || !saved) return;
      stopFlight(state);
      state.scale = clampScale(saved.scale);
      state.x = Number(saved.x) || 0;
      state.y = Number(saved.y) || 0;
      applyTransform(state);
      updateHeat(state);
      drawCircles(host, state, state.view || {});
      drawLegislative(host, state, state.view || {});
    }
  };

  for (const key of Object.keys(PURE)) api[key] = PURE[key];
  window.MentatEvidenceMap = api;
})();
