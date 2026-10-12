/* Marks v3: nothing is silently inert (Paul, 2026-09-19; lane M, boards #50-#52).

   core.encodingSupport(type, engine) is each renderer's declaration of what it
   draws. For every mark type and both engines, every encoding it declares is put
   on a sheet and the sheet is compiled and drawn through the same code the
   Studio uses: chart-studio-contributions.js compile -> the verified renderers
   (contribution-plot.js, contribution-charts.js, charts.js for pies and their
   kin, fed by canvasEncodings), and chart-studio-core.js compileReport ->
   chart-math build -> charts.js chartSVG. The drawing must change. A row the
   renderer cannot draw must carry a reason instead (the Inspector greys it).

   Run with the other suites:  node --test "mentat/app/tests/*.test.js" */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const corpus = require('./chart-engine-r3.corpus.js');

const APP = path.resolve(__dirname, '..');
const {math, paint, plot} = corpus.loadEngine(APP);
const serialize = corpus.serialize;
// The Studio's own renderers for the verified path; the corpus loader already
// installed the DOM, the theme and charts.js/contribution-plot.js.
globalThis.MentatContributionCharts = require('../contribution-charts.js');
const charts = globalThis.MentatContributionCharts;
const core = require('../chart-studio-core.js');
const contributions = require('../chart-studio-contributions.js');
const clone = value => JSON.parse(JSON.stringify(value));

/* ------------------------------------------------------------ worksheet data */
const COMPANIES = ['Acme', 'Birch', 'Cedar', 'Dover'], PARTIES = ['DEM', 'REP'], STATES = ['MA', 'NY', 'TX'];
const WORKSHEET = (() => {
  const rows = [];
  COMPANIES.forEach((company, i) => PARTIES.forEach((party, j) => STATES.forEach((state, k) => {
    rows.push([company, party, state, ['2021-2022', '2023-2024'][(i + j + k) % 2],
      1000 * (4 - i) + 137 * j + 53 * k * (i % 2 ? 1 : -1) + 11 * i * k,
      ((i * 7 + j * 3 + k * 5) % 11) + 1, ((i * 5 + k * 2 + j) % 9) + 1,
      '2024-0' + (((i + k) % 8) + 1) + '-1' + j]);
  })));
  return {sheet: 'Marks', header_row: 1, sha256: 'marks', row_count: rows.length,
    row_numbers: rows.map((_, n) => n + 2), rows,
    columns: [{index: 0, name: 'Company', type: 'category'}, {index: 1, name: 'Party', type: 'category'},
      {index: 2, name: 'State', type: 'category'}, {index: 3, name: 'Election cycle', type: 'category'},
      {index: 4, name: 'Amount', type: 'number'}, {index: 5, name: 'Gift count', type: 'number'},
      {index: 6, name: 'Donors', type: 'number'}, {index: 7, name: 'Date', type: 'date'}]};
})();
const W = {company: 0, party: 1, state: 2, cycle: 3, amount: 4, records: 5, contributors: 6};

/* ------------------------------------------------------------- verified data */
const CATALOG = [
  {id: 'company', label: 'Company', role: 'dimension'}, {id: 'party', label: 'Party', role: 'dimension'},
  {id: 'state', label: 'Contributor state', role: 'dimension', geographic_role: 'state'},
  {id: 'cycle', label: 'Election cycle', role: 'dimension'},
  {id: 'amount', label: 'Selected amount', role: 'measure', unit: 'cents'},
  {id: 'records', label: 'Observation count', role: 'measure', unit: 'count'},
  {id: 'contributors', label: 'Distinct reported donor identities', role: 'measure', unit: 'count'},
  {id: 'amount_bin', label: 'Amount bin', role: 'dimension'}];
const VCOLUMNS = contributions.fields(CATALOG);
const V = Object.fromEntries(VCOLUMNS.map(f => [f.id, f.index]));
const STATS = [['average_amount', 'Average amount', 'cents'], ['median_amount', 'Median amount', 'cents'],
  ['min_amount', 'Minimum amount', 'cents'], ['max_amount', 'Maximum amount', 'cents'], ['q1_amount', 'First quartile', 'cents'],
  ['q3_amount', 'Third quartile', 'cents'], ['first_date', 'First date', 'date'], ['last_date', 'Last date', 'date'],
  ['span_days', 'Span (days)', 'days']];
const SPEC_COLUMNS = [...VCOLUMNS.map(f => ({id: f.id, name: f.name, unit: f.unit, role: f.role, type: f.type})),
  ...STATS.map(([id, name, unit]) => ({id, name, unit, role: 'measure'}))];
const MEMBERS = {company: COMPANIES, party: PARTIES, state: STATES, cycle: ['2021-2022', '2023-2024'],
  year: ['2022', '2023', '2024'], date: ['2024-01-05', '2024-03-09', '2024-06-11'], amount_bin: [0, 10000, 20000]};
// Answer a compiled verified query with deterministic aggregate rows: every
// combination of the queried dimensions' members, every queried measure.
function synthesize(query) {
  let combos = [{}];
  for (const dim of query.dimensions) combos = combos.flatMap(c => (MEMBERS[dim] || ['x', 'y']).map(m => Object.assign({}, c, {[dim]: m})));
  return combos.slice(0, query.limit || 100).map((row, n) => {
    const seed = n * 7 + 3;
    for (const id of query.measures) {
      if (id.endsWith('_date')) row[id] = id === 'first_date' ? '2024-0' + ((n % 5) + 1) + '-01' : '2024-0' + ((n % 3) + 6) + '-15';
      else if (id === 'span_days') row[id] = 40 + (n * 13) % 90;
      else if (id === 'min_amount') row[id] = 100 + seed;
      else if (id === 'q1_amount') row[id] = 400 + seed * 2;
      else if (id === 'median_amount') row[id] = 900 + seed * 3;
      else if (id === 'q3_amount') row[id] = 1500 + seed * 4;
      else if (id === 'max_amount') row[id] = 3000 + seed * 5;
      else if (id === 'records') row[id] = ((n * 5) % 7) + 1;
      else if (id === 'contributors') row[id] = ((n * 3) % 5) + 2;
      else row[id] = 100000 + ((n * 7919) % 50000) - (n % 4) * 3000;
    }
    return row;
  });
}

/* ------------------------------------------------------------------ drawing */
const CANVAS = ['pie', 'doughnut', 'waterfall', 'pareto', 'radar'];
function drawVerified(state) {
  const compiled = contributions.compile(state, VCOLUMNS, {dataset: 'Banking', industry: 'Banking'});
  if (compiled.spec.type === 'map') return 'MAP';
  const rows = synthesize(compiled.query);
  const spec = Object.assign({}, compiled.spec, {columns: SPEC_COLUMNS, title: 'T', panelTitles: compiled.spec.measures.length !== 1});
  const container = document.createElement('div');
  if (CANVAS.includes(spec.type)) {
    // chart-studio.js renderContributionView's pie/doughnut/waterfall/Pareto/radar path.
    const ids = spec.measures, id = ids[0], nameOf = m => (SPEC_COLUMNS.find(c => c.id === m) || {}).name || m;
    const groups = rows.map(row => contributions.canvasEncodings({label: plot.label(row, spec.dimensions),
      value: row[id] / (contributions.MONEY.has(id) ? 100 : 1), n: 1,
      extra: Object.fromEntries(ids.slice(1).map(m => [nameOf(m), row[m]]))}, row, spec, nameOf, plot.format));
    const drawn = paint.chartSVG({type: spec.type, groups, selected: rows.length, excluded: 0, warnings: []}, nameOf(id),
      {type: spec.type, aggregation: 'sum', x: 0, y: 1}, {sheet: 'S', columns: [{name: 'Group'}, {name: id}]}, 'T', 'S', {labels: !!state.showLabels});
    return serialize(drawn.svg);
  }
  if (charts.TYPES.includes(spec.type)) charts.render(container, rows, spec, () => {});
  else plot.render(container, rows, spec, () => {});
  return serialize(container);
}
function drawWorksheet(state) {
  const report = core.compileReport(state, WORKSHEET.columns);
  if (!report.config) return 'EMPTY';
  if (report.map) return 'MAP';
  const result = math.build(WORKSHEET, report.config);
  if (report.renderMode === 'table') return JSON.stringify(paint.tableRows(result, report.config, WORKSHEET));
  const drawn = paint.chartSVG(result, paint.measureLabel(report.config, result, WORKSHEET), report.config, WORKSHEET, 'T', 'S', {labels: !!state.showLabels});
  return serialize(drawn.svg);
}
const DRAW = {verified: drawVerified, worksheet: drawWorksheet};
const FIELDS = {verified: V, worksheet: W};
const p = (column, aggregation = 'sum') => ({column, aggregation});

function baseSheet(type, engine, layout) {
  const f = FIELDS[engine], sheet = core.createSheet('Sheet 1', 's1');
  sheet.marks.type = type;
  sheet.columns = layout === 'two' ? [p(f.records)] : [p(f.company)];
  sheet.rows = [p(f.amount)];
  // Two measures place one mark per group: Detail gives a polygon its vertices.
  if (layout === 'two') sheet.marks.detail = [p(f.company)];
  sheet.showLabels = true;
  return sheet;
}
// What each encoding is set to. A card-only encoding (Shape, Path, Angle) is
// written through core.writeCards, exactly as the Inspector writes it.
function withEncoding(sheet, encoding, variant, engine) {
  const f = FIELDS[engine], next = clone(sheet);
  const set = (zone, value) => {
    const cards = core.readCards(next);
    cards[0].encodings[zone] = value;
    core.writeCards(next, cards);
  };
  if (encoding === 'color') next.marks.color = variant === 'measure' ? p(f.records) : p(f.state);
  else if (encoding === 'size') next.marks.size = p(f.records);
  else if (encoding === 'label') next.marks.label = p(f.records);
  else if (encoding === 'detail') next.marks.detail = [p(f.state)];
  else if (encoding === 'tooltip') next.marks.tooltip = [p(f.contributors)];
  else if (encoding === 'shape') set('shape', variant === 'glyph' ? {glyph: 'diamond'} : p(f.party));
  else if (encoding === 'path') set('path', variant === 'dimension' ? p(f.state) : p(f.records));
  else if (encoding === 'angle') set('angle', p(f.records));
  return next;
}
const VARIANTS = {color: ['dimension', 'measure'], size: ['measure'], label: ['measure'], detail: ['dimension'],
  tooltip: ['measure'], shape: ['dimension', 'glyph'], path: ['measure'], angle: ['measure']};
// Colour, Size, Label, Shape, Path and Angle must change what is drawn, not only
// the hover text: the comparison drops every <title>, aria-label and tooltip
// attribute first. Tooltip is hover text by nature, and Detail changes the marks.
const HOVER_ONLY = ['tooltip', 'detail'];
const visible = text => text.replace(/<title>.*?<\/title>/g, '').replace(/ (aria-label|data-tooltip|title)="(?:[^"\\]|\\.)*"/g, '');
const differs = (encoding, a, b) => HOVER_ONLY.includes(encoding) ? a !== b : visible(a) !== visible(b);
const POINT_TYPES = ['circle', 'square', 'shape', 'polygon', 'density'];
const TYPES = core.ALL_MARK_IDS;

for (const engine of ['verified', 'worksheet']) {
  for (const type of TYPES) {
    test('[marks v3] ' + engine + ' ' + type + ': every declared encoding changes the drawing', () => {
      const support = core.encodingSupport(type, engine);
      assert.deepEqual(Object.keys(support), core.markRows(type), 'the rows a ' + type + ' shows');
      const declared = Object.entries(support).filter(([, value]) => value === true).map(([id]) => id);
      for (const [id, value] of Object.entries(support)) {
        if (value !== true) assert.ok(typeof value === 'string' && value.length > 20 && /[.]$/.test(value), type + ' ' + id + ' is greyed with a sentence');
      }
      const layouts = POINT_TYPES.includes(type) ? ['one', 'two'] : ['one'];
      const inert = [];
      for (const layout of layouts) {
        const base = baseSheet(type, engine, layout);
        const before = DRAW[engine](base);
        assert.notEqual(before, 'EMPTY');
        for (const encoding of declared) {
          // Two measures: a polygon's vertices come from a Path dimension (Tableau's point order).
          for (const variant of encoding === 'path' && layout === 'two' ? ['dimension'] : VARIANTS[encoding]) {
            // A role the renderer cannot draw carries its reason (the Inspector marks the pill).
            const roleOk = core.roleSupport(type, engine, encoding, variant === 'glyph' ? 'dimension' : variant);
            if (roleOk !== true) { assert.ok(/[.]$/.test(roleOk)); continue; }
            const after = DRAW[engine](withEncoding(base, encoding, variant, engine));
            if (!differs(encoding, before, after)) inert.push(encoding + ' as a ' + variant + ' (' + layout + ' axis)');
          }
        }
      }
      assert.deepEqual(inert, [], engine + ' ' + type + ' declares encodings it does not draw: ' + inert.join('; '));
    });
  }
}

test('[marks v3] maps keep today’s drawing and grey every row with the reason', () => {
  for (const type of ['map', 'filled-map']) {
    for (const engine of ['verified', 'worksheet']) {
      const support = core.encodingSupport(type, engine);
      assert.ok(Object.values(support).every(value => typeof value === 'string' && /map layers/.test(value)), type);
    }
  }
});

test('[marks v3] a card’s Opacity, Border and Halo change every drawn mark type', () => {
  const unstyled = [];
  for (const engine of ['verified', 'worksheet']) {
    for (const type of TYPES.filter(t => !['map', 'filled-map', 'text'].includes(t))) {
      const base = baseSheet(type, engine, 'one');
      const drawnSvg = () => {
        const text = DRAW[engine](base);
        return text;
      };
      const before = drawnSvg();
      const allowed = core.styleSupport(type);
      for (const style of [{opacity: 50}, {border: 'ink'}, {halo: 'white'}].filter(s => allowed[Object.keys(s)[0]] === true)) {
        // Draw again, then lay the style over the drawing the way the Studio does.
        const compiledRoot = (() => {
          if (engine === 'worksheet') {
            const report = core.compileReport(base, WORKSHEET.columns), result = math.build(WORKSHEET, report.config);
            return paint.chartSVG(result, 'M', report.config, WORKSHEET, 'T', 'S', {labels: true}).svg;
          }
          const compiled = contributions.compile(base, VCOLUMNS, {dataset: 'Banking'}), rows = synthesize(compiled.query);
          const spec = Object.assign({}, compiled.spec, {columns: SPEC_COLUMNS});
          if (CANVAS.includes(spec.type)) return paint.chartSVG({type: spec.type, groups: rows.map(r => ({label: plot.label(r, spec.dimensions), value: r[spec.measures[0]] / 100, n: 1})), selected: 1, excluded: 0, warnings: []},
            'M', {type: spec.type, aggregation: 'sum', x: 0, y: 1}, {sheet: 'S', columns: [{name: 'G'}, {name: 'V'}]}, 'T', 'S', {labels: true}).svg;
          const container = document.createElement('div');
          (charts.TYPES.includes(spec.type) ? charts.render : plot.render)(container, rows, spec, () => {});
          return container.children.find(node => node.tagName === 'svg') || container;
        })();
        const plain = serialize(compiledRoot);
        plot.styleMarks(compiledRoot, Object.assign({opacity: 100, border: 'automatic', halo: 'automatic'}, style));
        if (serialize(compiledRoot) === plain) unstyled.push(engine + ' ' + type + ' ' + JSON.stringify(style));
      }
      assert.ok(before);
    }
  }
  assert.deepEqual(unstyled, [], 'these drawings did not take the card style: ' + unstyled.join('; '));
});

test('[marks v3] the 22 shapes are 22 different drawings, and an open shape is unfilled', () => {
  const drawings = core.GLYPHS.map(id => plot.glyphD(id, 50, 50, 10));
  assert.equal(core.GLYPHS.length, 22);
  assert.deepEqual(plot.GLYPH_IDS, core.GLYPHS, 'the renderer and the model list the same shapes');
  const filled = core.GLYPHS.filter(id => !id.endsWith('-open'));
  assert.equal(new Set(filled.map(id => plot.glyphD(id, 50, 50, 10))).size, 11);
  for (const id of core.GLYPHS) assert.equal(plot.glyphOpen(id), id.endsWith('-open'));
  assert.ok(drawings.every(d => /^M[\d.]+,[\d.]+/.test(d) && d.endsWith('Z')));
});

test('[marks v3] a dual axis draws each card’s measure with its own mark, and Synchronize axes changes the scale', () => {
  for (const engine of ['verified', 'worksheet']) {
    const f = FIELDS[engine];
    const sheet = core.createSheet('Dual', 'd');
    sheet.columns = [p(f.company)];
    sheet.rows = [p(f.amount), p(f.records)];
    sheet.marks.type = 'bar';
    const columns = engine === 'verified' ? VCOLUMNS : WORKSHEET.columns;
    core.setDualAxis(sheet, columns, true);
    assert.equal(sheet.marksCards.length, 2, engine);
    assert.deepEqual(sheet.marksCards.map(card => card.type), ['bar', 'line']);
    const draw = DRAW[engine];
    const bars = draw(sheet);
    assert.match(bars, /data-card-type="bar"/, engine + ' draws the bar card');
    assert.match(bars, /data-card-type="line"/, engine + ' draws the line card');
    for (const type of ['area', 'square', 'circle', 'shape', 'text', 'polygon', 'density']) {
      const other = clone(sheet), cards = core.readCards(other);
      core.setCardType(cards, 1, type);
      core.writeCards(other, cards);
      assert.notEqual(draw(other), bars, engine + ': the second card as ' + type);
      assert.match(draw(other), new RegExp('data-card-type="' + type + '"'));
    }
    const synced = clone(sheet);
    core.writeCards(synced, core.readCards(synced), {syncAxes: true});
    assert.notEqual(draw(synced), bars, engine + ': Synchronize axes');
    const inert = [];
    for (const zone of ['color', 'size', 'label', 'tooltip']) {
      const other = clone(sheet), cards = core.readCards(other);
      core.setCardEncoding(cards, 1, zone, [zone === 'color' ? p(f.party) : zone === 'size' ? p(f.records) : p(f.contributors)]);
      core.writeCards(other, cards);
      other.showLabels = true;
      const plain = clone(sheet); plain.showLabels = true;
      if (draw(other) === draw(plain)) inert.push(zone);
    }
    assert.deepEqual(inert, [], engine + ': the second card does not draw its ' + inert.join(', '));
    // Negative control with teeth: turning the dual axis off draws one card again.
    const single = clone(sheet);
    core.setDualAxis(single, columns, false);
    assert.ok(!('marksCards' in single));
    assert.doesNotMatch(draw(single), /data-card-type=/);
  }
});
