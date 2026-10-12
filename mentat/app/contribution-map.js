/* Independent Census geometry and exact-cent contribution marks. No external JS. */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const WIDTH = 1000, HEIGHT = 640;
  const zip = value => typeof value === 'string' && /^[0-9]{5}$/.test(value.trim()) ? value.trim() : '';
  const postalStates = new Set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI AA AE AP FM MH PW'.split(' '));
  const stateCode = value => String(value ?? '').trim().toUpperCase();
  function zipEligibility(group) {
    const zip5=zip(group.zip5??group.zip),state=stateCode(group.state);
    const reason=!state||['UNKNOWN','UNK','N/A','NA','--','??'].includes(state)?'state_unknown':!postalStates.has(state)?'state_not_us_postal':!zip5?'invalid_zip':'';
    return {eligible:!reason,zip5,state,reason};
  }
  const eligibleZips = groups => [...new Set((groups||[]).map(zipEligibility).filter(item=>item.eligible).map(item=>item.zip5))];
  const cents = value => {
    if (!Number.isSafeInteger(value)) throw new Error('Map amounts must be integer cents from the contribution query.');
    return value;
  };
  const plus = (a, b) => cents(a + b);
  const dollars = value => (value / 100).toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2});
  // Contract V10: a map can shade by any measure. `valueField` names the group
  // field (default amount_cents) and `unit` how it reads: 'cents', 'count' (whole
  // numbers with separators) or 'number' (2 dp). amount_cents keeps its integer-
  // cent assertion; another shaded field (an average can hold fractional cents)
  // only has to be finite. The coverage line keeps amount_cents, the selected
  // amount, whatever is shaded.
  // Census C-16 (report worksheets): 'dollars' reads a worksheet's own currency
  // measure, which is in dollars, not cents. `coverage: 'value'` (an additive
  // measure) or 'places' (an average, median, minimum, maximum or distinct
  // count) replaces the selected-amount coverage line, since a worksheet has no
  // selected amount; `zipOnly` places a ZIP with no recorded state; groups may
  // carry `district` (Census GEOID) or `unplaced` (a reason) instead of a ZIP.
  const unitOf = options => ['cents', 'count', 'number', 'dollars'].includes(options?.unit) ? options.unit : 'cents';
  const shown = (value, unit) => unit === 'cents' ? dollars(value) : unit === 'count' ? Math.round(value).toLocaleString('en-US') :
    unit === 'dollars' ? Number(value).toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2}) :
    Number(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const valueModeOf = options => options?.coverage === 'value' || options?.coverage === 'places';
  const placeName = item => item.kind === 'state' ? item.state : item.kind === 'district' ? item.label : 'ZIP ' + item.zip5;
  const plural = (n, one, many) => n.toLocaleString('en-US') + ' ' + (n === 1 ? one : many);
  // Composite equirectangular display: the same projection is used for marks and
  // boundaries. Insets preserve local geography; their relative size is not area.
  function project(longitude, latitude) {
    let lon = Number(longitude), lat = Number(latitude);
    if (!Number.isFinite(lon) || !Number.isFinite(lat) || lon < -180 || lon > 180 || lat < -90 || lat > 90) return null;
    if (lat > 50 && (lon < -129 || lon > 170)) {
      if (lon > 0) lon -= 360;
      return [28 + (lon + 190) * 4.5, 440 + (72 - lat) * 5.5];
    }
    if (lon < -150 && lat > 15 && lat < 32) return [345 + (lon + 180) * 7, 445 + (30 - lat) * 7];
    if (lon > -69 && lon < -64 && lat > 16 && lat < 20) return [650 + (lon + 68) * 32, 478 + (19 - lat) * 28];
    if (lon > 130 && lat > 0 && lat < 35) return [805 + (lon - 130) * 4, 465 + (25 - lat) * 4];
    if (lon < -160 && lat < 0) return [905 + (lon + 171) * 12, 545 + (-13 - lat) * 10];
    return [35 + (lon + 125) * 15.5, 28 + (50 - lat) * 14.5];
  }
  function prepare(groups, geometry, options = {}) {
    const valueField = options.valueField || 'amount_cents', unit = unitOf(options), legacy = valueField === 'amount_cents';
    const valueOf = group => { const v = group[valueField]; return typeof v === 'number' && Number.isFinite(v) ? v : 0; };
    const locations = new Map((geometry?.points || []).map(p => [zip(p.zip5 || p.zip), p]));
    const states = new Map((geometry?.layers?.states?.features || []).map(f => [f.properties.STUSAB, f.properties]));
    const districts = new Map((geometry?.layers?.districts?.features || []).map(f => [String(f.properties?.GEOID), f.properties]));
    const zipOnly = options.zipOnly === true, valueMode = valueModeOf(options);
    const combined = new Map();
    for (const group of groups || []) {
      const {zip5,state,reason:zipReason,eligible}=zipEligibility(group),hasZip=Object.hasOwn(group,'zip5')||Object.hasOwn(group,'zip');
      const unplaced=typeof group.unplaced==='string'&&group.unplaced?group.unplaced:'',hasDistrict=Object.hasOwn(group,'district');
      const kind=unplaced?'unmapped':hasDistrict?(group.district?'district':'unmapped'):hasZip?(eligible||(zipOnly&&zip5)?'zip':'unmapped'):postalStates.has(state)?'state':'unmapped';
      const reason=kind==='unmapped'?(unplaced||(hasDistrict?'location_unavailable':zipReason)):'';
      const id = kind === 'zip' ? zip5 : kind === 'state' ? state : kind === 'district' ? String(group.district) : `${reason}:${zip5||'(Missing ZIP)'}:${state||'(Missing state)'}`;
      const key = kind + ':' + id, value = legacy || group.amount_cents != null ? cents(group.amount_cents) : 0, shade = legacy ? value : valueOf(group);
      if (!combined.has(key)) combined.set(key, {key, kind, zip5, state, states:[], reason, label: kind === 'district' && group.label ? String(group.label) : id, amount_cents: 0, records: 0, value: 0});
      const item = combined.get(key);
      const recordedState=String(group.state??'');if(!item.states.includes(recordedState))item.states.push(recordedState);
      item.amount_cents = plus(item.amount_cents, value);
      item.value = legacy ? plus(item.value, shade) : item.value + shade;
      item.records += Number.isFinite(group.records) ? group.records : 0;
    }
    let selected = 0, mapped = 0, unmapped = 0, selectedValue = 0, mappedValue = 0, unmappedValue = 0;
    const marks = [], missing = [], reasons={};
    for (const item of combined.values()) {
      item.states.sort();
      selected = plus(selected, item.amount_cents);
      selectedValue += item.value;
      const location = item.kind === 'zip' ? locations.get(item.zip5) : item.kind==='state'?states.get(item.state):item.kind==='district'?districts.get(item.key.slice('district:'.length)):null;
      const longitude = location?.longitude ?? location?.INTPTLON;
      const latitude = location?.latitude ?? location?.INTPTLAT;
      const point = longitude != null && latitude != null ? project(longitude, latitude) : null;
      if (point) {
        mapped = plus(mapped, item.amount_cents);
        mappedValue += item.value;
        marks.push({...item, longitude: Number(longitude), latitude: Number(latitude), point});
      } else {
        unmapped = plus(unmapped, item.amount_cents);
        unmappedValue += item.value;
        missing.push(item);
        const reason=item.reason||'location_unavailable';
        if(!reasons[reason])reasons[reason]={groups:0,amount_cents:0,value:0};
        reasons[reason].groups++;reasons[reason].amount_cents=plus(reasons[reason].amount_cents,item.amount_cents);reasons[reason].value+=item.value;
      }
    }
    const coverageNotes=[];
    if(valueMode){
      const worth=r=>options.coverage==='places'?'':` (${shown(r.value,unit)})`;
      if(reasons.several_places)coverageNotes.push(`${plural(reasons.several_places.groups,'group names','groups name')} several different places in one cell${worth(reasons.several_places)} and stay${reasons.several_places.groups===1?'s':''} unmapped; Split multi-value cells in Parameters places each one.`);
      if(reasons.no_place)coverageNotes.push(`${plural(reasons.no_place.groups,'group has','groups have')} no recognisable place${worth(reasons.no_place)} and stay${reasons.no_place.groups===1?'s':''} unmapped.`);
      if(reasons.location_unavailable)coverageNotes.push(`${plural(reasons.location_unavailable.groups,'place has','places have')} no Census location${worth(reasons.location_unavailable)} and stay${reasons.location_unavailable.groups===1?'s':''} unmapped.`);
    }else{
    if(reasons.state_not_us_postal)coverageNotes.push(`${reasons.state_not_us_postal.groups} ZIP/state groups (${dollars(reasons.state_not_us_postal.amount_cents)}) have foreign or unrecognized state codes and remain unmapped.`);
    if(reasons.state_unknown)coverageNotes.push(`${reasons.state_unknown.groups} ZIP/state groups (${dollars(reasons.state_unknown.amount_cents)}) have missing or unknown recorded state; their US location is uncertain and they remain unmapped.`);
    }
    return {marks, unmapped: missing, selected_cents: selected, mapped_cents: mapped, unmapped_cents: unmapped, unmapped_reasons:reasons,coverage_notes:coverageNotes,
      value_field: valueField, unit, selected_value: selectedValue, mapped_value: mappedValue, unmapped_value: unmappedValue};
  }
  function featurePath(feature) {
    const geometry = feature?.geometry;
    if (!geometry) return '';
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
    return polygons.map(polygon => polygon.map(ring => {
      const points = ring.map(p => project(p[0], p[1])).filter(Boolean);
      return points.length ? 'M' + points.map(p => p.map(v => v.toFixed(2)).join(',')).join('L') + 'Z' : '';
    }).join('')).join('');
  }
  function node(tag, attributes, text) {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes || {})) element.setAttribute(key, String(value));
    if (text != null) element.textContent = text;
    return element;
  }
  function element(tag, text) {
    const result = document.createElement(tag);
    if (text != null) result.textContent = text;
    return result;
  }
  function render(container, options) {
    if (!container) throw new Error('A map container is required.');
    const geometry = options.geometry || {}, data = prepare(options.groups || [], geometry, options);
    const show = value => shown(value, data.unit), legacyValue = data.value_field === 'amount_cents', valueMode = valueModeOf(options);
    let active = Array.isArray(options.layers) ? new Set(options.layers) : new Set(Object.entries(options.layers || {states: true, districts: true}).filter(([,v]) => v).map(([k]) => k));
    let view = [0, 0, WIDTH, HEIGHT], pointer = null, moved = false;
    let viewportTimer;
    container.replaceChildren();
    const frame = element('section'); frame.className = 'contribution-map';
    frame.setAttribute('aria-label', 'Contribution origin map');
    const toolbar = element('div'); toolbar.className = 'contribution-map-toolbar';
    Object.assign(toolbar.style, {display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem'});
    function button(label, callback) {
      const control = element('button', label); control.type = 'button'; control.className = 'secondary';
      control.onclick = callback; toolbar.append(control); return control;
    }
    const mapTitle = options.title || 'Recorded contributor ZIP amounts';
    const svg = node('svg', {viewBox: view.join(' '), role: 'group', tabindex: '0', 'aria-label': mapTitle + ' with optional Census boundaries. Use plus and minus to zoom; arrow keys pan.', width: '100%', height: HEIGHT});
    Object.assign(svg.style, {display: 'block', maxHeight: '68vh', minHeight: '340px', background: '#eef3f4', border: '1px solid #cad4d8', borderRadius: '8px', touchAction: 'none'});
    const title = node('title', {}, mapTitle + '. Census ZCTA representative points and area boundaries.'); svg.append(title);
    const geographyLayer = node('g'), markLayer = node('g'), labelLayer = node('g');
    svg.append(geographyLayer, markLayer, labelLayer);
    const selectedStatus = element('p'); selectedStatus.setAttribute('role', 'status'); selectedStatus.setAttribute('aria-live', 'polite');
    const coverage = element('p'); coverage.className = 'contribution-map-coverage';
    coverage.textContent = options.coverage === 'value' ? (options.valueLabel || 'Value') + ' ' + show(data.selected_value) + ' · Located marks ' + show(data.mapped_value) + ' · Unmapped ' + show(data.unmapped_value) + '. ' + data.marks.length.toLocaleString() + ' locations; no top-N cutoff.'
      : options.coverage === 'places' ? (options.valueLabel || 'Value') + ' by place · ' + plural(data.marks.length, 'located place', 'located places') + ' · ' + data.unmapped.length.toLocaleString() + ' unmapped; no top-N cutoff.'
      : (options.valueLabel || 'Selected amount') + ' ' + (legacyValue ? dollars(data.selected_cents) : show(data.selected_value) + ' · Selected amount ' + dollars(data.selected_cents)) + ' · Located ZIP/state marks ' + dollars(data.mapped_cents) + ' · Unmapped ' + dollars(data.unmapped_cents) + '. ' + data.marks.length.toLocaleString() + ' locations; no top-N cutoff.';
    const notes = element('p'); notes.style.fontSize = '.88rem'; notes.style.color = '#52646e';
    notes.textContent = [...new Set([...(Array.isArray(options.notes) ? options.notes : [options.filled ? 'State fill represents recorded contributor-state totals. Darker fill indicates a larger absolute amount; negative values use orange.' : 'Census ZCTA points and areas approximate postal ZIP geography; they are not donor addresses. Negative values use orange.', 'Recorded postal state codes determine mapping eligibility. Alaska, Hawaii, Caribbean and Pacific areas use labeled insets.']), ...data.coverage_notes, ...(geometry.warnings || [])])].join(' ');
    const sourceLine = element('p'); sourceLine.style.fontSize = '.82rem';
    for (const source of geometry.sources || []) {
      const label = element('span', source.vintage + ' · SHA-256 ' + String(source.sha256 || '').slice(0, 12) + ' ');
      sourceLine.append(label);
    }
    if (!(geometry.sources || []).length) sourceLine.textContent = 'Census geography has not loaded. All selected amounts remain available in the data table.';
    function getBounds() {
      // Only the contiguous map has a single inverse projection; exclude insets.
      if (view[1] + view[3] > 438 || view[2] > 930) return null;
      return [Math.max(-125, view[0] / 15.5 - 125 - 35 / 15.5), Math.max(24, 50 - (view[1] + view[3] - 28) / 14.5),
              Math.min(-65, (view[0] + view[2] - 35) / 15.5 - 125), Math.min(50, 50 - (view[1] - 28) / 14.5)];
    }
    function updateView(notify = true) {
      svg.setAttribute('viewBox', view.join(' '));
      if (notify && options.onViewport) {
        clearTimeout(viewportTimer);
        viewportTimer = setTimeout(() => {const bbox = getBounds(); if (bbox && bbox[0] < bbox[2] && bbox[1] < bbox[3]) options.onViewport({bbox});}, 250);
      }
    }
    function zoom(factor, center) {
      const nextWidth = Math.min(WIDTH * 1.4, Math.max(22, view[2] * factor)), ratio = nextWidth / view[2];
      const cx = center?.[0] ?? view[0] + view[2] / 2, cy = center?.[1] ?? view[1] + view[3] / 2;
      view = [cx - (cx - view[0]) * ratio, cy - (cy - view[1]) * ratio, nextWidth, view[3] * ratio];
      updateView();
    }
    function screenPoint(event) {
      const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
      return point.matrixTransform(svg.getScreenCTM().inverse());
    }
    button('Zoom in', () => zoom(.65)); button('Zoom out', () => zoom(1.5));
    button('Reset map', () => {view = [0, 0, WIDTH, HEIGHT]; updateView();});
    const regions = element('select'); regions.setAttribute('aria-label', 'Focus map region');
    const extents = {'US postal areas': [0,0,1000,640], 'Contiguous states': [10,0,970,430], 'Alaska': [10,405,310,180], 'Hawaii': [325,430,200,160], 'Puerto Rico and Virgin Islands': [625,440,180,130], 'Pacific postal areas': [800,430,185,160]};
    for (const name of Object.keys(extents)) {const option = element('option', options.filled&&name==='US postal areas'?'US states and territories':name); option.value = name; regions.append(option);}
    regions.onchange = () => {view = extents[regions.value].slice(); updateView();}; toolbar.append(regions);
    function select(item) {
      selectedStatus.textContent = placeName(item) + ': ' + (legacyValue ? dollars(item.amount_cents) : (options.valueLabel || 'Value') + ' ' + show(item.value) + (valueMode ? '' : ' · amount ' + dollars(item.amount_cents))) + (valueMode ? '.' : '. Select the location in the data table for underlying contributions.');
      if (options.onSelect) options.onSelect(item.kind === 'state' ? {kind:'state', state: item.state, states:item.states.slice(), amount_cents: item.amount_cents} : {zip5: item.zip5, states:item.states.slice(), amount_cents: item.amount_cents});
    }
    function accessibleMark(shape, item) {
      const label = placeName(item) + ': ' + (legacyValue ? dollars(item.amount_cents) : (options.valueLabel || 'Value') + ' ' + show(item.value) + (valueMode ? '' : ' · amount ' + dollars(item.amount_cents)));
      shape.setAttribute('role', 'button'); shape.setAttribute('tabindex', '0'); shape.setAttribute('aria-label', label);
      shape.append(node('title', {}, label));
      shape.addEventListener('click', () => {if (!moved) select(item);});
      shape.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); select(item);}});
    }
    const markByZip = new Map(data.marks.filter(m => m.kind === 'zip').map(m => [m.zip5, m]));
    const markByState = new Map(data.marks.filter(m=>m.kind==='state').map(m=>[m.state,m]));
    const markByDistrict = new Map(data.marks.filter(m=>m.kind==='district').map(m=>[m.key.slice('district:'.length),m]));
    // options.scaleMax (Chart Studio compare, census B-S32): maps drawn side by
    // side share one shading scale. Absent, each map scales to its own marks.
    const largest = Number.isFinite(options.scaleMax) && options.scaleMax > 0 ? options.scaleMax
      : data.marks.reduce((value, mark) => Math.max(value, Math.abs(mark.value)), legacyValue ? 1 : 1e-9);
    function drawLayers() {
      geographyLayer.replaceChildren(); labelLayer.replaceChildren();
      // Order is deliberate: fill under district lines under state lines. None
      // of these geometry records participates in prepare()'s monetary totals.
      for (const layer of ['zip_areas', 'districts', 'states']) {
        if (!active.has(layer)) continue;
        for (const feature of geometry.layers?.[layer]?.features || []) {
          const p = feature.properties || {}, item = layer === 'zip_areas' ? markByZip.get(p.GEOID) : layer==='states'&&options.filled?markByState.get(p.STUSAB):layer==='districts'&&options.filled?markByDistrict.get(String(p.GEOID)):null;
          const path = featurePath(feature); if (!path) continue;
          const colour = item?.value < 0 ? '#c5682a' : '#137e91';
          const shape = node('path', {d: path, fill: item ? colour : 'none', 'fill-opacity': item ? .22 + .6 * Math.sqrt(Math.abs(item.value) / largest) : 0,
            stroke: layer === 'states' ? '#3a5568' : layer === 'districts' ? '#8a6caa' : '#c0d7dc', 'stroke-width': layer === 'states' ? 1.05 : .65,
            'vector-effect': 'non-scaling-stroke', 'fill-rule': 'evenodd', 'pointer-events': item ? 'visiblePainted' : 'none'});
          if (item) accessibleMark(shape, item); else shape.append(node('title', {}, p.NAME || p.GEOID || layer));
          geographyLayer.append(shape);
          if (layer === 'states') {
            const point = project(p.INTPTLON, p.INTPTLAT);
            if (point) labelLayer.append(node('text', {x: point[0], y: point[1], 'text-anchor': 'middle', 'font-size': '9', fill: '#29485b', 'pointer-events': 'none', 'paint-order': 'stroke', stroke: '#eef3f4', 'stroke-width': 2}, p.STUSAB || p.NAME));
          }
        }
      }
      for (const [text, x, y] of [['Alaska',30,423],['Hawaii',344,450],['Puerto Rico / VI',649,456],['Pacific postal areas',815,454]]) {
        labelLayer.append(node('text', {x, y, fill:'#52646e', 'font-size':10, 'pointer-events':'none'}, text));
      }
    }
    const layerInputs=new Map();
    for (const [layer, label] of (options.showLayerControls === false ? [] : [['states','States'],['districts','Congress ' + (geometry.congress || '119') + ' districts'],['zip_areas','ZIP areas (ZCTA)']])) {
      const wrap = element('label'), input = element('input'); input.type = 'checkbox'; input.checked = active.has(layer);
      input.disabled = !(geometry.layers?.[layer]?.features?.length);
      input.onchange = () => {input.checked ? active.add(layer) : active.delete(layer); drawLayers();};
      layerInputs.set(layer,input);
      wrap.append(input, document.createTextNode(' ' + label)); toolbar.append(wrap);
    }
    drawLayers();
    // Draw smaller marks last so large totals cannot obscure all nearby marks.
    for (const item of data.marks.slice().sort((a,b) => Math.abs(b.value) - Math.abs(a.value))) {
      if(options.filled&&item.kind==='state'&&active.has('states'))continue;
      if(options.filled&&item.kind==='district'&&active.has('districts'))continue;
      const circle = node('circle', {cx:item.point[0], cy:item.point[1], r:2.3 + 10 * Math.sqrt(Math.abs(item.value) / largest),
        fill:item.value < 0 ? '#c5682a' : '#0b7893', 'fill-opacity':.68, stroke:'#ffffff', 'stroke-width':.6, 'vector-effect':'non-scaling-stroke'});
      accessibleMark(circle, item); markLayer.append(circle);
    }
    svg.addEventListener('wheel', event => {
      if (!event.ctrlKey) return;
      event.preventDefault(); const point=screenPoint(event);
      const center = [point.x,point.y];
      zoom(event.deltaY < 0 ? .85 : 1.18, center);
    }, {passive: false});
    svg.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      moved = false; const matrix=svg.getScreenCTM();
      pointer = {x:event.clientX,y:event.clientY,view:view.slice(),id:event.pointerId,scaleX:matrix.a,scaleY:matrix.d};
    });
    svg.addEventListener('pointermove', event => {
      if (!pointer) return;
      const dx = event.clientX-pointer.x, dy=event.clientY-pointer.y;
      if (Math.abs(dx)+Math.abs(dy)>5) {moved=true;svg.setPointerCapture(pointer.id);}
      view=[pointer.view[0]-dx/pointer.scaleX,pointer.view[1]-dy/pointer.scaleY,pointer.view[2],pointer.view[3]]; updateView(false);
    });
    const release = () => {if(pointer) {pointer=null;updateView();}};
    svg.addEventListener('pointerup',release); svg.addEventListener('pointercancel',release);
    svg.addEventListener('keydown', event => {
      if (event.target !== svg) return;
      if (event.key === '+' || event.key === '=') zoom(.7);
      else if (event.key === '-') zoom(1.4);
      else if (event.key === 'Home') {view=[0,0,WIDTH,HEIGHT];updateView();}
      else if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) {
        view[0] += event.key==='ArrowLeft' ? -view[2]*.12 : event.key==='ArrowRight' ? view[2]*.12 : 0;
        view[1] += event.key==='ArrowUp' ? -view[3]*.12 : event.key==='ArrowDown' ? view[3]*.12 : 0;updateView();
      } else return;
      event.preventDefault();
    });
    frame.append(toolbar,coverage,svg,selectedStatus,notes,sourceLine); container.append(frame);
    return {data, svg, getBounds, getView:()=>view.slice(), setView(next){if(Array.isArray(next)&&next.length===4&&next.every(Number.isFinite)&&next[2]>0&&next[3]>0){view=next.slice();updateView(false);}},
      setLayers(layers){active=new Set(layers);for(const [name,input] of layerInputs)input.checked=active.has(name);drawLayers();}, destroy(){clearTimeout(viewportTimer);container.replaceChildren();}};
  }
  const api = {render, prepare, project, featurePath,zipEligibility,eligibleZips};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MentatContributionMap = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
