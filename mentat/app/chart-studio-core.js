/* Chart Studio: shelf state -> chart configuration. Pure functions only; no DOM,
   no network, no timers. Field classification and chart choice come from
   chart-math.js so the rules are never forked. Exercised by node:test. */
(function (root) {
  'use strict';

  const math = (typeof module !== 'undefined' && module.exports) ? require('./chart-math.js') : root.MentatCharts;
  // Lane P (boards #47-#55): the map layer model - a card that names a
  // geography is a layer of a map sheet. Optional here: without it a layer
  // card's shape is checked and its map fields are left to the server.
  const mapModel = (typeof module !== 'undefined' && module.exports) ? require('./map-layers.js') : root.MentatMapLayers;

  // Paul, 2026-09-23: "Additionally, within the Chart Studio make sure everything
  // is freely flexible, that all graph types properly register and nothing is
  // logically constrained, it needs to be utterly maluable." Count distinct
  // (engine contract W12) is how a text field is read as a measure.
  const AGGREGATIONS = [['sum','Sum'],['mean','Mean'],['count','Count'],['median','Median'],['min','Minimum'],['max','Maximum'],['countd','Count distinct']];
  const AGGREGATION_IDS = AGGREGATIONS.map(a => a[0]);
  const TRANSFORMS = ['none','annual','yoy','change','forecast'];

  // Marks card. Doughnut is not a Tableau mark; it is here so that every chart
  // the classic dialog could draw stays reachable from the shelves.
  const MARK_TYPES = [
    ['automatic','Automatic'],['bar','Bar'],['line','Line'],['area','Area'],['square','Square'],['circle','Circle'],['shape','Shape'],
    ['pie','Pie'],['doughnut','Doughnut'],['text','Text'],['box','Box'],['histogram','Histogram'],
    ['waterfall','Waterfall'],['pareto','Pareto'],['radar','Radar'],['polygon','Polygon'],['density','Density']
  ];
  const MARK_IDS = MARK_TYPES.map(m => m[0]);
  // One mark list for both sources (census A-W06): every chart the Verified
  // menu offers is offered for a report worksheet too, in the same order and
  // with the same names, so a mark never exists in one mode only.
  const EXTRA_MARK_TYPES = [['map','Map'],['heatmap','Heat map'],['highlight','Highlight table'],['stacked','Stacked bars'],
    ['dot','Circle view'],['series-dot','Side-by-side circles'],['treemap','Treemap'],['bubbles','Packed bubbles'],
    ['gantt','Observed date span'],['bullet','Bullet comparison'],['combination','Dual combination'],['dual-line','Dual lines'],
    ['filled-map','Filled state map']];
  const ALL_MARK_TYPES = MARK_TYPES.concat(EXTRA_MARK_TYPES);
  const ALL_MARK_IDS = ALL_MARK_TYPES.map(m => m[0]);
  const markLabel = id => (ALL_MARK_TYPES.find(m => m[0] === id) || [id, 'Automatic'])[1];

  /* ---------------------------------------------------------------- fields */

  // A sheet may re-read a column as a dimension or a measure. The override is
  // per sheet and persisted with it. A text or date column read as a measure
  // is counted: COUNT and COUNT DISTINCT are the aggregations it offers.
  function canTreatAs(field, role) {
    if (!field) return {ok: false, reason: 'That field is not in this source.'};
    if (role === 'dimension') {
      if (field.role === 'dimension') return {ok: false, reason: field.name + ' is already a dimension.'};
      return {ok: true, reason: ''};
    }
    if (role !== 'measure') return {ok: false, reason: 'Choose Dimension or Measure.'};
    if (field.role === 'measure') return {ok: false, reason: field.name + ' is already a measure.'};
    if (field.type !== 'number') {
      return {ok: true, reason: '', counted: true,
        note: field.name + ' holds ' + (field.type === 'date' ? 'dates' : 'text') + ', so as a measure it is counted: Count or Count distinct.'};
    }
    return {ok: true, reason: ''};
  }

  function classifyFields(columns, overrides) {
    const fields = (columns || []).map((column, position) => {
      const field = {
        index: Number.isInteger(column.index) ? column.index : position,
        name: String(column.name == null ? 'Column ' + (position + 1) : column.name),
        type: column.type || 'category', id: column.id, unit: column.unit,
        data_type:column.data_type,table:column.table,geographic_role:column.geographic_role,description:column.description,discrete:column.discrete,
        distinct: column.distinct == null ? null : column.distinct,
        missing: column.missing == null ? null : column.missing
      };
      if (column.accounting) field.accounting = column.accounting;
      field.isTime = column.is_time === true || !!math.timeColumn(field);
      field.isIdentifier = !!math.identifierColumn(field);
      field.role = column.role || (field.type === 'number' && !field.isTime && !field.isIdentifier ? 'measure' : 'dimension');
      // Identifier columns are real fields, listed like any other (census
      // B-S21): the Data pane puts them under its "More fields" fold rather
      // than behind a checkbox. recommend() still ignores them.
      field.hidden = false;
      // The calendar marks a date (type 'date'), as S1 draws it; a cycle, year,
      // quarter or month is still a time axis (isTime) but its glyph is Abc.
      field.glyph = field.geographic_role ? 'geo' : field.data_type==='boolean'?'bool':field.role === 'measure' ? '#' : field.type === 'date' ? 'cal' : 'Abc';
      return field;
    });
    const types = overrides && typeof overrides === 'object' ? overrides : {};
    for (const field of fields) {
      const wanted = types[String(field.index)];
      const role = wanted && wanted.role;
      if ((role === 'dimension' || role === 'measure') && role !== field.role && canTreatAs(field, role).ok) {
        field.role = role;
        field.coerced = true;
        field.hidden = false;
        if (role === 'measure' && field.type !== 'number') field.counted = true;
        field.glyph = field.geographic_role ? 'geo' : field.data_type === 'boolean' ? 'bool'
          : field.role === 'measure' ? '#' : field.type === 'date' ? 'cal' : 'Abc';
      }
    }
    return {
      fields,
      dimensions: fields.filter(f => f.role === 'dimension' && !f.hidden),
      measures: fields.filter(f => f.role === 'measure' && !f.hidden),
      hidden: fields.filter(f => f.hidden),
      identifiers: fields.filter(f => f.role === 'dimension' && f.isIdentifier && !f.isTime)
    };
  }

  const fieldAt = (meta, index) => meta.fields.find(f => f.index === index) || null;

  // Board S1 opens each Data-pane bucket on a short list of named fields, in
  // the board's order; the bucket's other fields fold under "More fields".
  // An id the catalog does not have is skipped, never stood in for.
  function paneBuckets(fields, ids) {
    const list = fields || [];
    const shown = (ids || []).map(id => list.find(f => f.id === id)).filter(Boolean);
    return {shown, more: list.filter(f => !shown.includes(f))};
  }

  // The classic dialog and the engine both find the subject by an exact
  // 'Subject' column name (charts.js prepareSubjects, chart-math temporal), so
  // the studio reads it the same way rather than inventing a looser rule.
  const subjectField = fields => (fields || []).find(f => f.name === 'Subject') || null;

  /* ----------------------------------------------------------------- state */

  function pill(value) {
    if (value == null) return null;
    if (typeof value === 'number') return Number.isInteger(value) ? {column: value, aggregation: 'sum'} : null;
    if (typeof value === 'string' && /^-?\d+$/.test(value)) return {column: Number(value), aggregation: 'sum'};
    if (typeof value !== 'object' || !Number.isInteger(Number(value.column))) return null;
    return {column: Number(value.column), aggregation: AGGREGATION_IDS.includes(value.aggregation) ? value.aggregation : 'sum'};
  }

  const shelf = list => (Array.isArray(list) ? list : []).map(pill).filter(Boolean);

  function createSheet(name, id) {
    return {
      id: id || '',
      name: name || 'Sheet 1',
      // No dataset or industry key by default: a report worksheet has neither,
      // and an EMPTY industry is the shape the save gate refuses. Contributions
      // mode and migrateSheet() set both when a dataset exists.
      source: {run: '', runName: '', report: '', reportName: '', sheet: '', header: 0, sha256: ''},
      // The ordered series sources a sheet can fan out across - empty means
      // the sheet reads one series, its own `source`. Each entry is
      // {industry, dataset, label}. Populated by contributions mode and by
      // migrateSheet(); nothing renders more than one source yet (S4 delivers
      // the model, not the chart).
      sources: [],
      columns: [],
      rows: [],
      marks: {type: 'automatic', color: null, size: null, label: null, detail: [], tooltip: []},
      filters: [],
      pages: null,
      pageMember: '',
      analytics: {transform: 'none', period: '', horizon: 2, totals: false, average: false, median: false},
      types: {},
      params: {start: 0, end: 0, limit: 0, subject: '', maxGroups: 0, splitMulti: false},
      sort: 'none',
      // S1 (build 03): a new sheet writes each bar's value at its end. Labels
      // stays a toggle, and a saved sheet keeps its own choice.
      showLabels: true,
      fit: 'standard'
    };
  }

  const clone = value => JSON.parse(JSON.stringify(value));

  function swapShelves(state) {
    const next = clone(state);
    // Lane F (#46): a Flow has no Rows; Swap reverses its stages.
    if (isFlowSheet(state)) { next.columns = clone(state.columns || []).reverse(); return next; }
    next.columns = clone(state.rows || []);
    next.rows = clone(state.columns || []);
    return next;
  }

  function clearSheet(state) {
    const next = createSheet(state.name, state.id);
    next.source = clone(state.source || {});
    return next;
  }

  // Backfills a sheet loaded from disk or the browser-local store before the
  // industry parameter existed: a source that only ever recorded `dataset`
  // gets `industry = dataset` (S4_DESIGN.md section 0.2 - the dataset id IS
  // the industry label), and `sources` is normalized to an array of
  // {industry, dataset, label} entries, dropping anything that cannot resolve
  // to an industry rather than carrying a fan-out entry openability() could
  // never check against the registry.
  function migrateSheet(sheet) {
    const next = sheet && typeof sheet === 'object' ? clone(sheet) : {};
    const source = next.source && typeof next.source === 'object' ? next.source : {};
    if (source.dataset && !source.industry) source.industry = source.dataset;
    next.source = source;
    const rawSources = Array.isArray(next.sources) ? next.sources : [];
    next.sources = rawSources
      .filter(entry => entry && typeof entry === 'object')
      .map(entry => ({
        industry: typeof entry.industry === 'string' ? entry.industry : '',
        dataset: typeof entry.dataset === 'string' ? entry.dataset : '',
        label: typeof entry.label === 'string' ? entry.label : ''
      }))
      .filter(entry => entry.industry);
    // Marks v3 (studio_api.json v3): malformed cards are dropped on open and
    // the sheet opens as its v2 marks, as a malformed board is dropped; a dual
    // axis gets `marks` mirroring its first card.
    if ('marksCards' in next || 'syncAxes' in next) {
      if (!normalizeMarksCards(next)) { delete next.marksCards; delete next.syncAxes; }
      else { const cards = readCards(next); writeCards(next, cards, {syncAxes: cards.length >= 2 ? next.syncAxes === true : null}); }
    }
    return next;
  }

  // Where a single click puts a field. A grouping goes across the top unless
  // something is already there; a dollar or count measure goes down the side
  // unless something is already there. Only the shelves are read, so a click
  // chooses the zone a drop onto that same empty shelf would choose.
  function quickAddZone(field, state) {
    // Lane F (#46): on a Flow a dimension joins Stages (Columns) and a measure goes to Size;
    // past five stages, or with Size taken, the field waits on Tooltip.
    if (isFlowSheet(state)) {
      if (field && field.role === 'measure') return state.marks && state.marks.size != null ? 'tooltip' : 'size';
      return shelf(state.columns).length < FLOW_MAX_STAGES ? 'columns' : 'tooltip';
    }
    const columnsEmpty = shelf(state && state.columns).length === 0;
    const rowsEmpty = shelf(state && state.rows).length === 0;
    if (field && field.role === 'measure') return rowsEmpty ? 'rows' : 'columns';
    return columnsEmpty ? 'columns' : 'rows';
  }

  // One worksheet, one saved session. data.sha256 digests the whole workbook,
  // so it is identical for every sheet in the file and cannot tell them apart;
  // the run, the report, the worksheet and the header row can.
  function sourceKey(fields, data) {
    const source = fields || {}, body = data || {};
    return [source.run, source.report, body.sheet || source.sheet,
      String(body.header_row ?? ''), body.sha256 || ''].join('|');
  }

  /* ------------------------------------------------------- carried scope */

  // Graph names an election cycle by the year it ends in ('2024'); the
  // verified index stores the pair that cycle covers ('2023-2024'). A scope
  // carried from Graph without this translation filtered on a value the
  // index never holds, so the studio answered "No matching observations"
  // for a company that plainly has receipts. Every other field is carried
  // unchanged: this is a spelling difference, not a rule.
  function carriedFilterValue(fieldId, value) {
    const text = value == null ? '' : String(value);
    if (fieldId !== 'cycle' || !/^\d{4}$/.test(text)) return text;
    return (Number(text) - 1) + '-' + text;
  }

  // The reverse, for the Graph query the reconciliation strip fires. A label
  // that is not a consecutive pair is not a cycle Graph can answer for, so
  // it returns '' rather than a year the reader never chose.
  function graphCycleValue(cycleLabel) {
    const text = cycleLabel == null ? '' : String(cycleLabel);
    const pair = /^(\d{4})-(\d{4})$/.exec(text);
    if (pair) return Number(pair[2]) === Number(pair[1]) + 1 ? pair[2] : '';
    return /^\d{4}$/.test(text) ? text : '';
  }

  /* ------------------------------------------------- report-workbook guards */

  // The published `<Industry> Industry Spending Overview.xlsx` is a report
  // layout, not a table. Its cycle sheets stack three partitions of the same
  // money under `Fact Type` - Recipient, Employee and Master - and only the
  // Recipient partition matches the report's own Total Giving; `Recipient List`
  // repeats every recipient once under an Industry view and again under a
  // Company view. Summing such a sheet whole triples or doubles it, so a
  // worksheet that carries one of those columns opens with the partition the
  // report itself totals already selected. The guard is an ordinary filter
  // pill: one click removes it, and the note says what removing it shows.
  // Each guard needs its own value to be present in the column: a sheet that
  // names the column but never holds the value would otherwise open empty.
  const WORKSHEET_GUARDS = [
    {column: 'Fact Type', value: 'Recipient', needsValue: true,
      note: 'Cycle sheets stack three fact partitions (Recipient, Employee, Master) of the same money. Only one partition matches the report total; Recipient is selected. Remove this filter to see the others.'},
    {column: 'View', value: 'Industry', needsValue: true,
      note: 'This sheet repeats every recipient under an Industry view and a Company view. Industry is selected so amounts are not doubled.'}
  ];

  // These four sheets carry the recipient committees' own receipts and
  // disbursements. That money is the committee's, not the industry's, and it
  // is orders of magnitude larger, so it is never presented as an industry
  // total.
  const COMMITTEE_ACTIVITY_SHEETS = ['Downstream Activity', 'Beneficiary Summary',
    'Beneficiary Bridge', 'Transfer Paths'];
  const COMMITTEE_ACTIVITY_NOTE = 'This sheet reports recipient committees\u2019 own activity, not the industry\u2019s giving. Totals here are not industry money and do not reconcile to the report\u2019s Total Giving.';

  const WORKSHEET_GROUP_LABELS = {data: 'Data workbook (tall, chart-ready)',
    overview: 'Overview workbook (report layout)'};
  const NO_DATA_WORKBOOK_HINT = 'The chart-ready Data workbook has not been published for this industry yet.';

  const guardText = value => String(value == null ? '' : value).trim();
  const guardSame = (a, b) => guardText(a).toLowerCase() === guardText(b).toLowerCase();

  // `columns` is the scanned column metadata. `values` reads one column's
  // values: either a function of the column index, or anything indexable by it.
  // Only the `View` guard reads values, and only for the column it has already
  // matched by name, so a caller never scans a sheet this returns nothing for.
  function defaultGuards(columns, values) {
    const read = index => {
      const list = typeof values === 'function' ? values(index) : (values || {})[index];
      return Array.isArray(list) ? list : [];
    };
    const guards = [];
    (columns || []).forEach((column, position) => {
      const index = Number.isInteger(column && column.index) ? column.index : position;
      for (const guard of WORKSHEET_GUARDS) {
        if (!guardSame(column && column.name, guard.column)) continue;
        // A column of either name can appear elsewhere without holding the
        // value this guard selects, and filtering on a value the sheet does
        // not have would empty the chart instead of correcting it.
        if (guard.needsValue && !read(index).some(value => guardSame(value, guard.value))) continue;
        guards.push({column: index, name: guardText(column && column.name),
          value: guard.value, note: guard.note});
      }
    });
    return guards;
  }

  // S1 (Paul, 2026-09-26): the Data workbook splits a sheet over its row
  // threshold into one sheet per cycle named `<Sheet> <election year>`
  // ('Transfer Paths 2022'); books published before keep `<Sheet> <cycle label>`
  // ('Transfer Paths 2021-2022') until they are rebuilt. Either part is its
  // sheet and keeps its sheet's caption; a bare cycle label is not a part. The
  // one sheet whose old part name broke Excel's 31 characters splits under a
  // shorter base (report_insights.py split_sheet_base is the server's copy).
  const SPLIT_SHEET_SUFFIX = /\s+(?:\d{4}-)?\d{4}$/;
  const SPLIT_SHEET_BASES = {'Network Candidates': 'Network Candidate Activity'};
  function splitSheetBase(name) {
    const text = guardText(name);
    const base = text.replace(SPLIT_SHEET_SUFFIX, '').trim();
    if (!base || base === text) return text;
    const known = Object.keys(SPLIT_SHEET_BASES).find(key => guardSame(key, base));
    return known ? SPLIT_SHEET_BASES[known] : base;
  }

  const committeeActivitySheet = name =>
    COMMITTEE_ACTIVITY_SHEETS.some(sheet => guardSame(sheet, splitSheetBase(name))) ? COMMITTEE_ACTIVITY_NOTE : null;

  // The catalog tags every workbook with `kind` (server.py published_reports).
  // A server that does not send it still names the companion in the filename,
  // which is the same rule read from the other end.
  const DATA_WORKBOOK_NAME = /\s+Industry Spending Data\.xlsx$/i;
  function workbookKind(entry) {
    const kind = guardText(entry && entry.kind).toLowerCase();
    if (kind === 'data' || kind === 'overview') return kind;
    return DATA_WORKBOOK_NAME.test(guardText(entry && entry.name)) ? 'data' : 'overview';
  }
  function groupWorksheets(catalog) {
    const rows = Array.isArray(catalog) ? catalog
      : Array.isArray(catalog && catalog.reports) ? catalog.reports : [];
    const data = [], overview = [];
    for (const entry of rows) (workbookKind(entry) === 'data' ? data : overview).push(entry);
    return {data: data, overview: overview};
  }

  /* --------------------------------------------------- marks cards (v3) */

  // Boards #50-#52 (approved 2026-09-25). The Marks card opens on a type strip:
  // fourteen tiles, two rows of seven, in the boards' order. Flow is drawn by
  // the Sankey build (lane F, #46) from a report worksheet's rows (flowReason).
  const MARK_STRIP = [['automatic', 'Automatic'], ['bar', 'Bar'], ['line', 'Line'], ['area', 'Area'],
    ['square', 'Square'], ['circle', 'Circle'], ['shape', 'Shape'], ['text', 'Text'], ['map', 'Map'],
    ['pie', 'Pie'], ['gantt', 'Gantt'], ['polygon', 'Polygon'], ['density', 'Density'], ['flow', 'Flow']];
  const FLOW_REASON = 'Flow draws a report worksheet\u2019s rows; switch the source to Report worksheet to draw one.';
  // The marks v3 adds. Each draws points: two measures place them on two axes,
  // one measure places them along the first grouping.
  const V3_MARK_IDS = ['square', 'shape', 'polygon', 'density'];
  const ENCODING_IDS = ['color', 'size', 'label', 'detail', 'tooltip', 'shape', 'path', 'angle'];
  const ENCODING_LABELS = {color: 'Colour', size: 'Size', label: 'Label', detail: 'Detail', tooltip: 'Tooltip',
    shape: 'Shape', path: 'Path', angle: 'Angle'};
  const LIST_ENCODINGS = ['detail', 'tooltip'];
  // Paul's Tableau reference (#50): eleven shapes, each filled and outline.
  const GLYPH_BASES = ['circle', 'square', 'plus', 'times', 'asterisk', 'diamond', 'triangle-up', 'triangle-down',
    'triangle-left', 'triangle-right', 'star'];
  const GLYPHS = [...GLYPH_BASES, ...GLYPH_BASES.map(g => g + '-open')];
  // Border and halo take Automatic (the renderer's own drawing), None, the
  // chart ink, white, or one of theme.js's twelve categorical slots.
  const STYLE_COLOURS = ['automatic', 'none', 'ink', 'white', ...Array.from({length: 12}, (_, i) => 'c' + (i + 1))];
  const CARD_KEYS = ['id', 'name', 'measure', 'type', 'encodings', 'opacity', 'border', 'halo', 'visible',
    'selectable', 'zoomExtent', 'source', 'geography', 'map'];
  const CARD_TYPES = [...ALL_MARK_IDS];
  const MAX_CARDS = 12, MAX_LIST_PILLS = 24;
  const CARDS_REFUSAL = 'The saved view’s marks cards are malformed.';
  // Lane F (#46): Flow is a stored card type with its own options (`flow`) without being a
  // Chart-menu mark: the menu lists the marks both sources draw, and a flow is drawn from
  // report-worksheet rows only.
  CARD_KEYS.push('flow');
  CARD_TYPES.push('flow');

  // A row appears only when the mark can use it (#51, and Tableau): Colour,
  // Size, Label, Detail and Tooltip always; Shape on Shape marks; Path on Line,
  // Area and Polygon; Angle on Pie. Automatic shows the rows of the mark it
  // resolves to.
  const PATH_TYPES = ['line', 'area', 'polygon'];
  const ANGLE_TYPES = ['pie', 'doughnut'];
  function markRows(type) {
    return ['color', 'size', 'label', 'detail', 'tooltip',
      ...(type === 'shape' ? ['shape'] : []), ...(PATH_TYPES.includes(type) ? ['path'] : []),
      ...(ANGLE_TYPES.includes(type) ? ['angle'] : [])];
  }
  // The mark a compiled engine type draws, for Automatic's rows and label.
  const ENGINE_MARK = {bar: 'bar', horizontal: 'bar', grouped: 'bar', groupedhorizontal: 'bar', line: 'line',
    multiline: 'line', area: 'area', multiarea: 'area', scatter: 'circle', dot: 'circle', multidot: 'circle',
    table: 'text'};
  const markOfEngineType = type => ENGINE_MARK[type] || type || 'bar';

  // Paul, 2026-09-19: nothing is silently inert. Each renderer declares what it
  // draws; a row it cannot draw is greyed with the reason (never hidden), and
  // app/tests/marks-v3-encodings.test.js draws every declared encoding and
  // fails if the drawing does not change. `engine` is 'verified' or 'worksheet'.
  const MAP_REASON = 'A map keeps today’s drawing: each place is shaded and sized by its measure. Colour, Size, Label, Detail and Tooltip on a map card arrive with map layers.';
  const GREYED = {
    size: {
      polygon: 'A polygon takes its area from its vertices, so Size does not apply.',
      pie: 'A pie fills its chart box, so Size does not apply.',
      doughnut: 'A doughnut fills its chart box, so Size does not apply.',
      box: 'A box plot keeps one box width, so Size does not apply.',
      histogram: 'Histogram bins keep their width, so Size does not apply.',
      waterfall: 'Waterfall steps keep one width, so Size does not apply.',
      radar: 'A radar draws one outline, so Size does not apply.',
      heatmap: 'Heat-map cells keep one size, so Size does not apply.',
      highlight: 'Highlight-table cells keep one size, so Size does not apply.',
      stacked: {verified: 'Stacked segments keep one thickness, so Size does not apply.'},
      bullet: 'Bullet bars keep one thickness, so Size does not apply.',
      combination: 'Dual-combination marks keep one thickness, so Size does not apply.',
      'dual-line': 'Dual lines keep one width, so Size does not apply.'
    },
    label: {
      density: 'Density draws how concentrated the marks are, not single marks, so Label does not apply. Circle, Square and Shape label each mark.',
      histogram: {verified: 'A histogram labels each bin with its observation count; Labels in the toolbar turns the counts on.',
        worksheet: 'A worksheet histogram labels each bin with its row count; a Label field is not computed per bin.'},
      stacked: {verified: 'A stacked bar labels its total; Labels in the toolbar turns the totals on.'},
      // The pie's slice labels belong to another lane's uncommitted drawing
      // (charts.js placePieLabels), so a Label field stays in the hover detail.
      pie: 'A pie prints each slice\u2019s share when Labels is on; a Label field is listed in each slice\u2019s hover detail, not printed on it.',
      doughnut: 'A doughnut prints each slice\u2019s share when Labels is on; a Label field is listed in each slice\u2019s hover detail, not printed on it.'
    },
    detail: {
      histogram: {verified: 'Bins count individual observations, so Detail does not split them. Put a field on Colour to stack the bins by it.'}
    },
    tooltip: {
      histogram: {worksheet: 'A worksheet histogram\u2019s bins carry their range and row count; a Tooltip field is not computed per bin.'}
    }
  };
  // A row that draws one kind of field and not the other: the Inspector keeps
  // the row and marks a placed pill of the other kind with the reason.
  const ROLE_GREYED = {
    color: {measure: {
      histogram: {worksheet: 'A worksheet histogram colours its bins by a Colour dimension; a Colour measure is not computed per bin.'},
      stacked: {worksheet: 'Stacked segments take their breakdown\u2019s colours, so a Colour measure does not change them; put a dimension on Colour to choose the breakdown.'},
      'series-dot': {worksheet: 'Side-by-side circles take their breakdown\u2019s colours, so a Colour measure does not change them; put a dimension on Colour to choose the breakdown.'}
    }}
  };
  function roleSupport(type, engine, encoding, role) {
    const entry = ((ROLE_GREYED[encoding] || {})[role] || {})[type];
    const reason = typeof entry === 'string' ? entry : entry && typeof entry === 'object' ? entry[engine] : null;
    return reason || true;
  }
  // The Colour popover's Opacity, Border and Halo (a card's style) per mark.
  const STYLE_REASON = {
    map: 'A map keeps today\u2019s drawing; opacity, border and halo per map card arrive with map layers.',
    'filled-map': 'A map keeps today\u2019s drawing; opacity, border and halo per map card arrive with map layers.',
    text: 'A text table\u2019s cells take no opacity, border or halo.'
  };
  // Lane F (#46): what a Flow card does not draw, each with its reason.
  GREYED.detail.flow = 'A flow splits its ribbons by its stages, so Detail does not split them. Put the field on Stages or on Colour.';
  ROLE_GREYED.color.measure.flow = 'A flow colours its ribbons by a dimension\u2019s members, so a Colour measure does not change them.';
  ROLE_GREYED.size = {dimension: {flow: 'A flow sizes its ribbons by a measure, so a dimension on Size does not change them.'}};
  ROLE_GREYED.label = {dimension: {flow: 'A flow node is labelled with its member; put a measure on Label to print its total under the name.'}};
  STYLE_REASON.flow = 'A flow sets its ribbon opacity in Flow options; its bars take no border or halo.';
  function styleSupport(type) {
    const reason = STYLE_REASON[type];
    if (reason) return {opacity: reason, border: reason, halo: reason};
    if (type === 'density') {
      const why = 'Density draws a heat, not outlined marks, so it takes Opacity only.';
      return {opacity: true, border: why, halo: why};
    }
    return {opacity: true, border: true, halo: true};
  }
  function encodingSupport(type, engine) {
    const mark = ALL_MARK_IDS.includes(type) || type === 'flow' ? type : 'automatic';
    const out = {};
    for (const id of markRows(mark)) {
      if (mark === 'map' || mark === 'filled-map') { out[id] = MAP_REASON; continue; }
      const entry = (GREYED[id] || {})[mark];
      const reason = typeof entry === 'string' ? entry : entry && typeof entry === 'object' ? entry[engine] : null;
      out[id] = reason || true;
    }
    return out;
  }

  // A pill, a list of pills, and the Shape encoding: a field whose members take
  // palette shapes (with optional member -> glyph choices), or one glyph for
  // every mark.
  const plainObject = value => !!value && typeof value === 'object' && !Array.isArray(value);
  const pillOf = value => value == null ? null : pill(value);
  function shapeEncoding(value) {
    if (!plainObject(value)) return null;
    if (value.column != null) {
      const p = pill(value);
      if (!p) return null;
      const glyphs = plainObject(value.glyphs)
        ? Object.fromEntries(Object.entries(value.glyphs).filter(([, glyph]) => GLYPHS.includes(glyph))) : null;
      return glyphs && Object.keys(glyphs).length ? Object.assign(p, {glyphs}) : p;
    }
    return typeof value.glyph === 'string' && GLYPHS.includes(value.glyph) ? {glyph: value.glyph} : null;
  }
  const blankEncodings = () => ({color: null, size: null, label: null, detail: [], tooltip: [], shape: null, path: null, angle: null});

  // Every card of today's sheets: one, read from the v2 `marks` object.
  function cardFromMarks(marks) {
    const m = plainObject(marks) ? marks : {};
    return flowFit({id: 'card-1', name: '', type: ALL_MARK_IDS.includes(m.type) ? m.type : (typeof m.type === 'string' && m.type ? m.type : 'automatic'),
      encodings: Object.assign(blankEncodings(), {color: pillOf(m.color), size: pillOf(m.size), label: pillOf(m.label),
        detail: shelf(m.detail), tooltip: shelf(m.tooltip)}),
      opacity: 100, border: 'automatic', halo: 'automatic', visible: true, selectable: true, zoomExtent: true});
  }
  // The v2 `marks` a card is read as by a console without v3, in createSheet's
  // key order. `base` keeps a v2-only key (orientation) the card does not own.
  function marksFromCard(card, base) {
    const e = card.encodings || blankEncodings();
    const out = {type: card.type, color: e.color ? clone(e.color) : null, size: e.size ? clone(e.size) : null,
      label: e.label ? clone(e.label) : null, detail: clone(e.detail || []), tooltip: clone(e.tooltip || [])};
    if (plainObject(base) && base.orientation) out.orientation = base.orientation;
    return out;
  }
  // What only a v3 console can carry. A card without any of it is written in
  // the v2 shape, so a sheet that never used them saves byte-identical.
  function hasV3Fields(card) {
    const e = (card && card.encodings) || {};
    if (card && card.flow != null) return true;      // lane F: a Flow card's options
    return !!(e.shape || e.path || e.angle || card.measure || (card.name || '') !== '' ||
      card.opacity !== 100 || card.border !== 'automatic' || card.halo !== 'automatic' ||
      card.visible === false || card.selectable === false || card.zoomExtent === false ||
      card.source != null || card.geography != null || card.map != null);
  }
  // One card on disk, keys in the contract's order.
  function serializeCard(card) {
    const e = card.encodings || blankEncodings();
    const out = {id: card.id, name: card.name || ''};
    if (card.measure) out.measure = clone(card.measure);
    out.type = card.type;
    out.encodings = {color: e.color ? clone(e.color) : null, size: e.size ? clone(e.size) : null,
      label: e.label ? clone(e.label) : null, detail: clone(e.detail || []), tooltip: clone(e.tooltip || []),
      shape: e.shape ? clone(e.shape) : null, path: e.path ? clone(e.path) : null, angle: e.angle ? clone(e.angle) : null};
    out.opacity = card.opacity; out.border = card.border; out.halo = card.halo;
    out.visible = card.visible; out.selectable = card.selectable; out.zoomExtent = card.zoomExtent;
    if (card.source != null) out.source = clone(card.source);
    if (card.geography != null) out.geography = card.geography;
    if (card.map != null) out.map = clone(card.map);
    if (card.flow != null) out.flow = flowOptions(card.flow);
    return out;
  }

  // The shape a saved sheet's `marksCards` must have (studio_api.json v3).
  // server.py validate_view_marks applies the same rules on save, so a card
  // that opens here is a card that saves. Returns '' or the first fault.
  const isInt = value => typeof value === 'number' && Number.isInteger(value);
  function pillFault(value, what, allowGlyphs) {
    if (!plainObject(value)) return what + ' is not a pill';
    const allowed = allowGlyphs ? ['column', 'aggregation', 'glyphs'] : ['column', 'aggregation'];
    if (Object.keys(value).some(key => !allowed.includes(key))) return what + ' has an unknown key';
    if (!isInt(value.column) || value.column < 0) return what + ' needs a column index';
    if (!AGGREGATION_IDS.includes(value.aggregation)) return what + ' needs a known aggregation';
    if (allowGlyphs && value.glyphs != null) {
      if (!plainObject(value.glyphs) || Object.keys(value.glyphs).length > 64) return what + ' glyphs are malformed';
      if (Object.entries(value.glyphs).some(([member, glyph]) => member.length > 200 || !GLYPHS.includes(glyph))) return what + ' names an unknown shape';
    }
    return '';
  }
  function cardFault(card) {
    if (!plainObject(card)) return 'a card is not an object';
    if (Object.keys(card).some(key => !CARD_KEYS.includes(key))) return 'a card has an unknown key';
    if (typeof card.id !== 'string' || !card.id || card.id.length > 64) return 'a card needs an id';
    if (typeof card.name !== 'string' || card.name.length > 120) return 'a card name must be text';
    if (!CARD_TYPES.includes(card.type)) return 'a card names an unknown mark type';
    if ('flow' in card || card.type === 'flow') { const fault = flowFault(card); if (fault) return fault; }
    if (!plainObject(card.encodings)) return 'a card needs its encodings';
    const keys = Object.keys(card.encodings);
    if (keys.length !== ENCODING_IDS.length || ENCODING_IDS.some(id => !keys.includes(id))) return 'a card must carry all eight encodings';
    for (const id of ['color', 'size', 'label', 'path', 'angle']) {
      const value = card.encodings[id];
      if (value !== null) { const fault = pillFault(value, id); if (fault) return fault; }
    }
    for (const id of LIST_ENCODINGS) {
      const list = card.encodings[id];
      if (!Array.isArray(list) || list.length > MAX_LIST_PILLS) return id + ' must be a list of pills';
      for (const value of list) { const fault = pillFault(value, id); if (fault) return fault; }
    }
    const shape = card.encodings.shape;
    if (shape !== null) {
      if (plainObject(shape) && 'glyph' in shape) {
        if (Object.keys(shape).length !== 1 || !GLYPHS.includes(shape.glyph)) return 'shape names an unknown shape';
      } else { const fault = pillFault(shape, 'shape', true); if (fault) return fault; }
    }
    if (!isInt(card.opacity) || card.opacity < 0 || card.opacity > 100) return 'opacity must be a whole percent';
    if (!STYLE_COLOURS.includes(card.border)) return 'border names an unknown colour';
    if (!STYLE_COLOURS.includes(card.halo)) return 'halo names an unknown colour';
    for (const id of ['visible', 'selectable', 'zoomExtent']) if (typeof card[id] !== 'boolean') return id + ' must be true or false';
    if ('measure' in card) { const fault = pillFault(card.measure, 'measure'); if (fault) return fault; }
    if ('source' in card && !plainObject(card.source)) return 'source must be an object';
    if ('geography' in card && (typeof card.geography !== 'string' || !card.geography || card.geography.length > 64)) return 'geography must be a level name';
    // Lane P: a layer's source, level and map options (map-layers.js
    // layerFault; server.py _card_ok applies the same rules on save).
    if (mapModel && ('source' in card || 'geography' in card || 'map' in card)) {
      const fault = mapModel.layerFault(card);
      if (fault) return fault;
    } else if ('map' in card && !plainObject(card.map)) return 'map options must be an object';
    return '';
  }
  function marksCardsFault(sheet) {
    if (!plainObject(sheet)) return '';
    if ('syncAxes' in sheet && typeof sheet.syncAxes !== 'boolean') return 'syncAxes must be true or false';
    if (!('marksCards' in sheet)) return '';
    const cards = sheet.marksCards;
    if (!Array.isArray(cards) || !cards.length || cards.length > MAX_CARDS) return 'marksCards must be a list of 1 to 12 cards';
    for (const card of cards) { const fault = cardFault(card); if (fault) return fault; }
    if (new Set(cards.map(card => card.id)).size !== cards.length) return 'two cards share an id';
    if (cards.length >= 2 && cards.some(card => card.type === 'flow')) return 'a flow draws alone; it shares no axis and no map';
    // Lane P: a sheet whose every card names a geography is a map. Its cards
    // are its layers, each with its own source, so the dual-axis rule below
    // is not theirs; a card set mixing layers and axis cards is refused.
    const layered = cards.map(card => 'geography' in card);
    if (layered.some(Boolean) && !layered.every(Boolean)) return 'every card of a map is a layer';
    if (cards.length >= 2 && !layered.every(Boolean)) {
      const rows = shelf(sheet.rows).map(p => p.column + '|' + p.aggregation);
      const measures = cards.map(card => card.measure ? card.measure.column + '|' + card.measure.aggregation : '');
      if (measures.some(key => !key)) return 'every card of a dual axis names its measure';
      if (new Set(measures).size !== measures.length) return 'two cards name the same measure';
      if (measures.some(key => !rows.includes(key))) return 'a card names a measure that is not on Rows';
    }
    return '';
  }
  const validateMarksCards = sheet => marksCardsFault(sheet);
  function normalizeCard(card) {
    const e = card.encodings;
    return {id: card.id, name: card.name, ...(card.measure ? {measure: pill(card.measure)} : {}), type: card.type,
      ...(card.flow != null ? {flow: flowOptions(card.flow)} : {}),
      encodings: {color: pillOf(e.color), size: pillOf(e.size), label: pillOf(e.label), detail: shelf(e.detail),
        tooltip: shelf(e.tooltip), shape: shapeEncoding(e.shape), path: pillOf(e.path), angle: pillOf(e.angle)},
      opacity: card.opacity, border: card.border, halo: card.halo, visible: card.visible,
      selectable: card.selectable, zoomExtent: card.zoomExtent,
      ...(card.source != null ? {source: clone(card.source)} : {}), ...(card.geography != null ? {geography: card.geography} : {}),
      ...(card.map != null ? {map: clone(card.map)} : {})};
  }
  // The sheet's cards, validated and copied, or null when it has none (a v2
  // sheet) or when the ones it has are malformed (dropped on open, like a board).
  function normalizeMarksCards(sheet) {
    if (!plainObject(sheet) || !Array.isArray(sheet.marksCards) || marksCardsFault(sheet)) return null;
    return sheet.marksCards.map(normalizeCard);
  }
  // The cards an edit works on: each card well formed, without the save-time
  // check that every measure is still on Rows (an edit to Rows is what
  // syncCardsToRows repairs before anything is drawn or saved).
  function looseCards(sheet) {
    const list = plainObject(sheet) && Array.isArray(sheet.marksCards) ? sheet.marksCards : null;
    if (!list || !list.length || list.length > MAX_CARDS || list.some(card => cardFault(card))) return null;
    return list.map(normalizeCard);
  }
  // Every card a sheet draws, v2 or v3. One card: `marks` is canonical for the
  // v2 fields (every v2 gesture still edits it in place) and the card adds what
  // only v3 carries. Several cards (a dual axis): the cards are canonical and
  // `marks` mirrors the first.
  function readCards(sheet) {
    const state = plainObject(sheet) ? sheet : {};
    const cards = looseCards(state);
    if (!cards) return [cardFromMarks(state.marks)];
    if (cards.length === 1) {
      const v2 = cardFromMarks(state.marks);
      cards[0].type = v2.type;
      for (const id of ['color', 'size', 'label', 'detail', 'tooltip']) cards[0].encodings[id] = v2.encodings[id];
      flowFit(cards[0]);
    }
    return cards;
  }
  // Writes cards back. One card without v3 fields leaves the v2 shape alone
  // (no marksCards key), which is what keeps old views byte-identical.
  function writeCards(sheet, cards, options) {
    const list = (cards || []).filter(Boolean);
    if (!list.length) return sheet;
    if (list.length >= 2) {
      sheet.marksCards = list.map(serializeCard);
      sheet.marks = marksFromCard(list[0], sheet.marks);
      if (options && options.syncAxes != null) {
        if (options.syncAxes) sheet.syncAxes = true; else delete sheet.syncAxes;
      }
      return sheet;
    }
    const card = Object.assign({}, list[0]);
    delete card.measure;
    const marks = plainObject(sheet.marks) ? sheet.marks : (sheet.marks = {});
    const v2 = marksFromCard(card, marks);
    for (const key of ['type', 'color', 'size', 'label', 'detail', 'tooltip']) {
      if (JSON.stringify(marks[key]) !== JSON.stringify(v2[key])) marks[key] = v2[key];
    }
    if (hasV3Fields(card)) sheet.marksCards = [serializeCard(card)];
    else delete sheet.marksCards;
    delete sheet.syncAxes;
    return sheet;
  }
  // A v2 producer (Show Me, the Chart menu, Company overview) just set `marks`.
  // One card keeps its v3 fields; a dual axis collapses to the view Show Me drew.
  function adoptMarks(sheet) {
    if (!plainObject(sheet) || !('marksCards' in sheet)) { if (plainObject(sheet)) delete sheet.syncAxes; return sheet; }
    const cards = normalizeMarksCards(sheet);
    const first = cards ? cards[0] : null;
    const card = cardFromMarks(sheet.marks);
    if (first && cards.length === 1) {
      for (const key of ['name', 'opacity', 'border', 'halo', 'visible', 'selectable', 'zoomExtent', 'source', 'geography', 'map'])
        if (first[key] != null) card[key] = first[key];
      card.id = first.id;
      for (const id of ['shape', 'path', 'angle']) card.encodings[id] = first.encodings[id];
      if (card.type === 'flow' && first.flow) card.flow = flowOptions(first.flow);
    }
    delete sheet.marksCards;
    return writeCards(sheet, [card]);
  }

  // Dual axis (#52): two or more measures on Rows, each with its own card.
  function measuresOnRows(sheet, columns) {
    const meta = classifyFields(columns, sheet && sheet.types);
    return shelf(sheet && sheet.rows).filter(p => { const f = fieldAt(meta, p.column); return f && f.role === 'measure'; });
  }
  const measureKey = p => p ? p.column + '|' + p.aggregation : '';
  // Lane P: a map sheet's cards are its layers (every card names a geography),
  // and layers are not a dual axis.
  const mapLayersOn = sheet => Array.isArray(sheet && sheet.marksCards) && sheet.marksCards.length >= 1
    && sheet.marksCards.every(card => plainObject(card) && typeof card.geography === 'string');
  const dualAxisOn = sheet => Array.isArray(sheet && sheet.marksCards) && sheet.marksCards.length >= 2 && !mapLayersOn(sheet);
  function setDualAxis(sheet, columns, on) {
    const cards = readCards(sheet);
    if (!on) {
      const first = Object.assign({}, cards[0]);
      delete first.measure;
      delete sheet.marksCards;
      writeCards(sheet, [first]);
      return sheet;
    }
    const measures = measuresOnRows(sheet, columns);
    if (measures.length < 2) return sheet;
    const base = cards[0];
    const next = measures.map((p, i) => {
      const card = JSON.parse(JSON.stringify(base));
      card.id = 'card-' + (i + 1);
      card.measure = {column: p.column, aggregation: p.aggregation};
      // #52: the first measure keeps the mark; the next ones start as lines.
      if (i > 0 && ['automatic', 'bar'].includes(base.type)) card.type = 'line';
      return card;
    });
    return writeCards(sheet, next, {syncAxes: false});
  }
  // The cards follow Rows: a measure moved or re-aggregated keeps its card, a
  // new measure gets one copied from the first, a removed one loses it, and a
  // dual axis left with fewer than two measures goes back to one card.
  function syncCardsToRows(sheet, columns) {
    if (!dualAxisOn(sheet)) return sheet;
    const cards = looseCards(sheet);
    if (!cards) { delete sheet.marksCards; delete sheet.syncAxes; return sheet; }
    const measures = measuresOnRows(sheet, columns);
    const pool = cards.slice();
    const take = (test) => { const at = pool.findIndex(test); return at < 0 ? null : pool.splice(at, 1)[0]; };
    const next = measures.map(p => take(card => measureKey(card.measure) === measureKey(p))
      || take(card => card.measure && card.measure.column === p.column) || null);
    next.forEach((card, i) => {
      if (!card) {
        const copy = JSON.parse(JSON.stringify(cards[0]));
        copy.id = 'card-' + (i + 1) + '-' + measures[i].column;
        next[i] = copy;
      }
      next[i].measure = {column: measures[i].column, aggregation: measures[i].aggregation};
    });
    if (next.length < 2) {
      const keep = Object.assign({}, next[0] || cards[0]);
      delete keep.measure;
      delete sheet.marksCards;
      return writeCards(sheet, [keep]);
    }
    const ids = new Set();
    next.forEach((card, i) => { if (ids.has(card.id)) card.id = 'card-' + (i + 1) + '-' + card.measure.column; ids.add(card.id); });
    return writeCards(sheet, next);
  }

  // The All card (#52): what every card shares. A single-pill encoding is
  // shown when every card has the same one; a list shows the pills every card
  // has. Editing All edits every card.
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function cardEncodingPills(cards, target, zone) {
    if (target !== 'all') {
      const card = cards[target];
      if (!card) return [];
      const value = card.encodings[zone];
      return LIST_ENCODINGS.includes(zone) ? clone(value || []) : value ? [clone(value)] : [];
    }
    if (LIST_ENCODINGS.includes(zone)) {
      return clone((cards[0].encodings[zone] || []).filter(p => cards.every(card => (card.encodings[zone] || []).some(q => same(p, q)))));
    }
    const first = cards[0].encodings[zone];
    return first && cards.every(card => same(card.encodings[zone], first)) ? [clone(first)] : [];
  }
  function setCardEncoding(cards, target, zone, list) {
    const targets = target === 'all' ? cards : [cards[target]].filter(Boolean);
    const before = target === 'all' ? cardEncodingPills(cards, 'all', zone) : null;
    for (const card of targets) {
      if (LIST_ENCODINGS.includes(zone)) {
        if (target === 'all') {
          // Pills All shows are replaced; each card keeps its own others.
          const own = (card.encodings[zone] || []).filter(p => !before.some(q => same(p, q)));
          card.encodings[zone] = [...clone(list), ...own];
        } else card.encodings[zone] = clone(list);
      } else card.encodings[zone] = list.length ? clone(list[0]) : null;
    }
    return cards;
  }
  function setCardType(cards, target, type) {
    for (const card of target === 'all' ? cards : [cards[target]].filter(Boolean)) card.type = type;
    return cards;
  }
  function setCardStyle(cards, target, style) {
    for (const card of target === 'all' ? cards : [cards[target]].filter(Boolean)) {
      for (const key of ['opacity', 'border', 'halo']) if (style && style[key] != null) card[key] = style[key];
    }
    return cards;
  }
  // The type All shows: the shared one, or 'mixed' when the cards differ.
  const allCardType = cards => cards.every(card => card.type === cards[0].type) ? cards[0].type : 'mixed';
  // The glyph each member of a Shape field takes: its chosen one, else the
  // palette in order. One glyph for every mark when no field is on Shape.
  function glyphFor(shape, member, index) {
    if (shape && shape.glyph) return shape.glyph;
    const chosen = shape && shape.glyphs && shape.glyphs[member];
    return chosen || GLYPHS[Math.max(0, index) % GLYPHS.length];
  }
  // The marks a dual axis can share one pane with. Pie, Gantt, Map and Flow
  // need their own frame, so their tiles are greyed on a card of a dual axis.
  const DUAL_MARKS = ['automatic', 'bar', 'line', 'area', 'square', 'circle', 'shape', 'text', 'polygon', 'density'];
  const DUAL_REASON = 'A dual-axis card draws Bar, Line, Area, Square, Circle, Shape, Text, Polygon or Density on the shared axis.';
  // Automatic on a dual axis: the first measure as bars (lines over a date),
  // every other measure as a line, as #52 draws them.
  const dualCardMark = (type, index, timeAxis) => type === 'automatic'
    ? (index === 0 && !timeAxis ? 'bar' : 'line') : DUAL_MARKS.includes(type) ? type : (index === 0 ? 'bar' : 'line');

  // A worksheet dual axis. The shelves compile once, as one bar view grouped
  // by the axis and every card's dimensions, with every other card's measure
  // and every measure a card encodes carried per group; charts.js then draws
  // each card's measure with its own mark (config.cards).
  function compileCards(state, columns, cards) {
    const meta = classifyFields(columns, state && state.types);
    const role = p => { const f = p && fieldAt(meta, p.column); return f ? f.role : ''; };
    const proxy = clone(state);
    delete proxy.marksCards; delete proxy.syncAxes;
    const measures = cards.map(card => card.measure).filter(Boolean);
    const dims = [], carried = [];
    const keep = (list, p) => { if (p && !list.some(q => q.column === p.column && q.aggregation === p.aggregation)) list.push({column: p.column, aggregation: p.aggregation}); };
    for (const card of cards) {
      const e = card.encodings;
      const shapePill = e.shape && e.shape.column != null ? {column: e.shape.column, aggregation: e.shape.aggregation} : null;
      for (const p of [e.color, e.size, e.label, e.path, shapePill, ...e.detail, ...e.tooltip]) {
        if (!p || !role(p)) continue;
        keep(role(p) === 'dimension' ? dims : carried, p);
      }
    }
    proxy.rows = [...shelf(state.rows).filter(p => role(p) !== 'measure'), ...measures.slice(0, 1)];
    proxy.marks = {type: 'bar', color: null, size: null, label: null, detail: dims,
      tooltip: [...measures.slice(1).map(p => ({column: p.column, aggregation: p.aggregation})), ...carried]};
    proxy.analytics = Object.assign({}, proxy.analytics, {transform: 'none'});
    const report = compileReport(proxy, columns);
    const notes = report.notes.filter(note => !/^Detail, Label and Tooltip/.test(note));
    if ((state.analytics || {}).transform && state.analytics.transform !== 'none') notes.push('A dual axis draws each measure with its own mark, so period analysis is left off.');
    if (dims.length) notes.push('The cards share one level of detail: ' + [...new Set(dims.map(p => (fieldAt(meta, p.column) || {name: 'a field'}).name))].join(' / ') + ' splits every card’s marks.');
    const timeAxis = !!(report.config && fieldAt(meta, report.config.x) && fieldAt(meta, report.config.x).isTime);
    if (report.config) {
      report.config.mark = 'cards';
      report.config.series = -1;
      report.config.synchronize = !!state.syncAxes;
      report.config.cards = cards.map((card, i) => {
        const e = card.encodings;
        const shapeField = e.shape && e.shape.column != null ? fieldAt(meta, e.shape.column) : null;
        if (!DUAL_MARKS.includes(card.type)) notes.push(markLabel(card.type) + ' cannot share a dual axis, so ' + ((fieldAt(meta, card.measure.column) || {}).name || 'its measure') + ' is drawn as ' + (i === 0 ? 'bars' : 'a line') + '.');
        return {id: card.id, type: dualCardMark(card.type, i, timeAxis), measure: {column: card.measure.column, aggregation: card.measure.aggregation},
          glyph: card.type === 'square' ? 'square' : (e.shape && e.shape.glyph) || '',
          shape: shapeField && shapeField.role === 'dimension' ? shapeField.index : -1, glyphs: (e.shape && e.shape.glyphs) || null,
          color: e.color ? {column: e.color.column, aggregation: e.color.aggregation, role: role(e.color)} : null,
          size: e.size && role(e.size) === 'measure' ? clone(e.size) : null,
          label: e.label ? {column: e.label.column, aggregation: e.label.aggregation, role: role(e.label)} : null,
          tooltip: e.tooltip.filter(p => role(p) === 'measure').map(p => clone(p)),
          path: e.path ? {column: e.path.column, aggregation: e.path.aggregation, role: role(e.path)} : null,
          opacity: card.opacity, border: card.border, halo: card.halo, visible: card.visible};
      });
    }
    return Object.assign(report, {notes: notes, markType: 'cards', cards: cards});
  }

  /* ------------------------------------------------------- flow (lane F) */

  // Board #46 (approved 2026-09-25). Flow draws a Sankey from a report worksheet's rows:
  // Stages are the Columns shelf (two to five dimensions, in order); Size is the measure
  // every ribbon carries (a row count without one); Colour splits and colours the ribbons
  // by a dimension's members; Label prints a measure under each node's name; Tooltip lists
  // fields per ribbon. The card keeps its options as `flow` (studio_api.json v4), and
  // studio-flow.js draws it with the PAC evidence Flow's layout (flow-layout.js).
  const FLOW_RIBBONS = ['source', 'target', 'outcome', 'gradient'];
  const FLOW_ORDERS = ['value', 'alphabetical'];
  const FLOW_OPTION_KEYS = ['ribbons', 'order', 'opacity', 'groupAfter'];
  const FLOW_DEFAULTS = {ribbons: 'source', order: 'value', opacity: 40, groupAfter: 8};
  const FLOW_MIN_STAGES = 2, FLOW_MAX_STAGES = 5, FLOW_MIN_GROUP = 2, FLOW_MAX_GROUP = 50;
  const FLOW_DUAL_REASON = 'Flow draws alone, so it is not offered on a dual axis; turn Dual axis off to draw a flow.';
  // The strip's Flow tile: offered on a report worksheet, greyed with the reason elsewhere.
  const flowReason = (engine, dual) => engine === 'verified' ? FLOW_REASON : dual ? FLOW_DUAL_REASON : '';
  const flowGroupOk = value => isInt(value) && (value === 0 || (value >= FLOW_MIN_GROUP && value <= FLOW_MAX_GROUP));
  // A card's options with every key present and known (anything else takes the default).
  function flowOptions(value) {
    const v = plainObject(value) ? value : {};
    return {ribbons: FLOW_RIBBONS.includes(v.ribbons) ? v.ribbons : FLOW_DEFAULTS.ribbons,
      order: FLOW_ORDERS.includes(v.order) ? v.order : FLOW_DEFAULTS.order,
      opacity: isInt(v.opacity) && v.opacity >= 0 && v.opacity <= 100 ? v.opacity : FLOW_DEFAULTS.opacity,
      groupAfter: flowGroupOk(v.groupAfter) ? v.groupAfter : FLOW_DEFAULTS.groupAfter};
  }
  // The save-time rule (server.py _card_ok applies it too): options on a Flow card only,
  // and a Flow card always carries all four.
  function flowFault(card) {
    if (card.type !== 'flow') return 'flow options belong to a Flow card only';
    const value = card.flow;
    if (!plainObject(value)) return 'a Flow card needs its flow options';
    const keys = Object.keys(value);
    if (keys.length !== FLOW_OPTION_KEYS.length || FLOW_OPTION_KEYS.some(key => !keys.includes(key)))
      return 'flow options are ribbons, order, opacity and groupAfter';
    if (!FLOW_RIBBONS.includes(value.ribbons)) return 'flow ribbons must be source, target, outcome or gradient';
    if (!FLOW_ORDERS.includes(value.order)) return 'flow order must be value or alphabetical';
    if (!isInt(value.opacity) || value.opacity < 0 || value.opacity > 100) return 'flow opacity must be a whole percent';
    if (!flowGroupOk(value.groupAfter)) return 'flow groupAfter must be 0 or a whole number from 2 to 50';
    return '';
  }
  // A Flow card always has its options; any other card has none.
  function flowFit(card) {
    if (card.type === 'flow') card.flow = flowOptions(card.flow);
    else delete card.flow;
    return card;
  }
  // Lane P's map layers are a separate uncommitted lane: a sheet whose cards are layers is never a
  // flow, and without that lane there are no layers (typeof keeps this block committable alone).
  const layersOn = sheet => typeof mapLayersOn === 'function' && mapLayersOn(sheet);
  const isFlowSheet = state => plainObject(state) && !dualAxisOn(state) && !layersOn(state)
    && readCards(state)[0].type === 'flow';
  function setFlowOptions(sheet, patch) {
    const cards = readCards(sheet);
    if (cards.length !== 1 || cards[0].type !== 'flow') return sheet;
    cards[0].flow = flowOptions(Object.assign({}, cards[0].flow, patch));
    return writeCards(sheet, cards);
  }
  // Choosing Flow (the strip, the Chart menu): the sheet's dimensions become its Stages in
  // shelf order and its first measure goes to Size; leaving Flow puts Size's measure back
  // on an empty Rows. One edit, undone as one. False when no Flow boundary is crossed.
  function flowSwitch(sheet, columns, type) {
    if (!plainObject(sheet) || dualAxisOn(sheet) || layersOn(sheet)) return false;
    const was = readCards(sheet)[0].type === 'flow';
    if (was === (type === 'flow')) return false;
    const meta = classifyFields(columns, sheet.types);
    const role = p => { const field = p && fieldAt(meta, p.column); return field ? field.role : ''; };
    if (!plainObject(sheet.marks)) sheet.marks = {type: 'automatic', color: null, size: null, label: null, detail: [], tooltip: []};
    const marks = sheet.marks;
    if (type === 'flow') {
      const placed = [...shelf(sheet.columns), ...shelf(sheet.rows)];
      const measure = placed.find(p => role(p) === 'measure');
      sheet.columns = placed.filter(p => role(p) === 'dimension');
      sheet.rows = [];
      if (measure && marks.size == null) marks.size = measure;
      marks.type = 'flow';
      adoptMarks(sheet);
      writeCards(sheet, readCards(sheet));
      return true;
    }
    const size = pillOf(marks.size);
    if (!shelf(sheet.rows).length && size && role(size) === 'measure') { sheet.rows = [size]; marks.size = null; }
    marks.type = type;
    adoptMarks(sheet);
    return true;
  }
  const aggregateName = id => (AGGREGATIONS.find(entry => entry[0] === id) || [id, id])[1];
  // What studio-flow.js draws, or the issue that stops it (fewer than two stages). A pill
  // the flow cannot use is named in a note, never dropped in silence.
  function compileFlow(state, columns, card) {
    const meta = classifyFields(columns, state && state.types);
    const issues = [], notes = [];
    const attach = list => shelf(list).map(p => Object.assign({}, p, {field: fieldAt(meta, p.column)})).filter(p => p.field);
    const one = value => value == null ? null : attach([value])[0] || null;
    const named = p => ({column: p.column, aggregation: p.aggregation, name: p.field.name, role: p.field.role,
      unit: p.field.unit || ''});
    const placed = attach(state.columns);
    const dimensions = placed.filter(p => p.field.role === 'dimension');
    for (const p of placed.filter(p => p.field.role !== 'dimension'))
      notes.push('Stages take dimensions; ' + p.field.name + ' is not a stage. Put it on Size to size the ribbons.');
    if (dimensions.length > FLOW_MAX_STAGES) {
      const left = dimensions.slice(FLOW_MAX_STAGES).map(p => p.field.name);
      notes.push('A flow draws up to five stages; ' + left.join(', ') + (left.length === 1 ? ' is' : ' are') + ' not drawn.');
    }
    const stages = dimensions.slice(0, FLOW_MAX_STAGES);
    const onRows = attach(state.rows).map(p => p.field.name);
    if (onRows.length) notes.push('A flow draws its Stages and Size; ' + onRows.join(', ') + ' on Rows '
      + (onRows.length === 1 ? 'is' : 'are') + ' not drawn. Move ' + (onRows.length === 1 ? 'it' : 'them') + ' to Stages or Size.');
    if (stages.length < FLOW_MIN_STAGES)
      issues.push('A flow needs two to five dimensions on Stages, in order: where the money starts, then each place it goes.');
    const e = card.encodings;
    const size = one(e.size), colour = one(e.color), label = one(e.label);
    let measure = null;
    if (size && size.field.role === 'measure') {
      const additive = size.aggregation === 'sum' || size.aggregation === 'count';
      measure = Object.assign(named(size), {aggregation: additive ? size.aggregation : size.aggregation === 'countd' ? 'count' : 'sum'});
      if (!additive) notes.push('A flow adds its ribbons together, so ' + size.field.name + ' is drawn as '
        + (measure.aggregation === 'count' ? 'a count' : 'a sum') + '; ' + aggregateName(size.aggregation) + ' does not add up across a stage.');
    } else if (size) notes.push('Size takes a measure; ' + size.field.name + ' does not size the ribbons.');
    else notes.push('No measure is on Size, so each ribbon counts worksheet rows.');
    if (colour && colour.field.role !== 'dimension')
      notes.push('A flow colours its ribbons by a dimension; ' + colour.field.name + ' on Colour does not change them.');
    if (label && label.field.role !== 'measure')
      notes.push('Label prints a measure under each node\u2019s name; ' + label.field.name + ' is a dimension, so each node keeps its name only.');
    if (e.detail.length) notes.push('Detail does not split a flow; put the field on Stages to add a stage, or on Colour to colour the ribbons by it.');
    const filters = (state.filters || [])
      .filter(f => f && filterActive(f) && fieldAt(meta, Number(f.column)))
      .map(f => normalizeFilter(f));
    if (filters.some(f => ['gte', 'lte'].includes(f.op))) notes.push('Number filters apply to rows, not totals. Filter totals in Verified contributions.');
    const pagePill = pill(state.pages);
    if (pagePill && fieldAt(meta, pagePill.column) && state.pageMember)
      filters.push({column: pagePill.column, op: 'equals', value: String(state.pageMember)});
    const analytics = state.analytics || {};
    if (analytics.transform && analytics.transform !== 'none') notes.push('A flow draws the selected rows as one period, so period analysis is left off.');
    const params = state.params || {};
    const config = stages.length >= FLOW_MIN_STAGES ? {
      type: 'flow', x: -1, y: measure ? measure.column : -1, aggregation: measure ? measure.aggregation : 'count',
      transform: 'none', stages: stages.map(named), measure,
      colour: colour && colour.field.role === 'dimension' ? named(colour) : null,
      label: label && label.field.role === 'measure' ? named(label) : null,
      tooltip: e.tooltip.map(one).filter(Boolean).map(named),
      filters, start: positiveInteger(params.start), end: positiveInteger(params.end), limit: positiveInteger(params.limit),
      options: flowOptions(card.flow), lenient: true} : null;
    return {config, issues, notes, renderMode: 'chart', markType: 'flow', map: null, fields: meta};
  }

  /* --------------------------------------------------------------- compile */

  const positiveInteger = value => {
    const number = Number(value);
    return Number.isFinite(number) && number >= 1 ? Math.trunc(number) : 0;
  };

  // The Studio's own default: sixty marks per view, which is the engine's
  // standing contract too. Every compiled configuration carries its limit
  // explicitly, and the Group limit box in Parameters raises it - to "All"
  // (engine contract W10) when the reader wants every group.
  const STUDIO_GROUP_LIMIT = 60;
  const groupLimitOf = value => value === 'all' ? 'all' : positiveInteger(value) || STUDIO_GROUP_LIMIT;

  // Colour on these types is a series the engine draws per member (contract
  // W11); on every other type it joins the grouping tuple instead.
  const SERIES_TYPES = ['bar', 'horizontal', 'line', 'area', 'dot', 'scatter', 'histogram', 'box',
    'pie', 'doughnut', 'waterfall', 'pareto', 'radar'];
  const PAIRED = {bar: 'grouped', horizontal: 'groupedhorizontal', line: 'multiline'};

  // Paul, 2026-09-23: "Additionally, within the Chart Studio make sure
  // everything is freely flexible, that all graph types properly register and
  // nothing is logically constrained, it needs to be utterly maluable." So the
  // compiler never refuses a composition. Every mark draws from whatever is on
  // the shelves; a field a mark prefers but the shelves lack is picked from the
  // source and named in a quiet note; every sentence that used to block the
  // drawing is a note under it. Only an empty sheet has nothing to draw.
  function compileReport(state, columns) {
    // Marks v3 (#52): a dual axis compiles through compileCards; one card
    // reads its v3 encodings (Shape, Path, Angle) through readCards.
    const cardSet = readCards(state);
    if (cardSet.length >= 2 && !mapLayersOn(state)) return compileCards(state, columns, cardSet);
    const card = cardSet[0];
    // Lane F (#46): a Flow card compiles to its own configuration; studio-flow.js draws it.
    if (card.type === 'flow' && cardSet.length === 1) return compileFlow(state, columns, card);
    const meta = classifyFields(columns, state && state.types);
    const issues = [], notes = [];
    const attach = list => shelf(list)
      .map(p => Object.assign({}, p, {field: fieldAt(meta, p.column)}))
      .filter(p => p.field);
    const columnPills = attach(state.columns);
    const rowPills = attach(state.rows);
    const isDimension = p => p.field.role === 'dimension';
    const isMeasure = p => p.field.role === 'measure';
    const colDimensions = columnPills.filter(isDimension);
    const colMeasures = columnPills.filter(isMeasure);
    const rowDimensions = rowPills.filter(isDimension);
    const rowMeasures = rowPills.filter(isMeasure);
    const allMeasures = colMeasures.concat(rowMeasures);
    const allDimensions = colDimensions.concat(rowDimensions);
    const marks = state.marks || {};
    const markType = ALL_MARK_IDS.includes(marks.type) ? marks.type : 'automatic';
    const markName = markLabel(markType);
    const encoded = channel => attach(Array.isArray(marks[channel]) ? marks[channel] : [marks[channel]].filter(v => v != null));
    const colorPill = encoded('color')[0] || null;
    const color = colorPill ? colorPill.field : null;
    const sizePill = encoded('size')[0] || null;
    const labelPill = encoded('label')[0] || null;
    const tooltipPills = encoded('tooltip');
    const details = encoded('detail');
    // Dimensions on Detail, Tooltip, Label and Size join the grouping behind
    // each mark, as they do on the verified path; a measure on Detail or
    // Tooltip is carried into each mark's tooltip (census B-S03).
    // Marks v3 (#50-#52): Shape on Shape marks, Path on Line, Area and
    // Polygon, Angle on Pie. A dimension on any of them joins each mark's
    // grouping; on Square, Shape, Polygon and Density a Colour dimension does
    // too, so every point keeps its member instead of becoming a series.
    const v3Pill = zone => {
      const value = card.encodings[zone];
      return value && value.column != null ? attach([{column: value.column, aggregation: value.aggregation}])[0] || null : null;
    };
    const shapePill = markType === 'shape' ? v3Pill('shape') : null;
    const pathPill = PATH_TYPES.includes(markType) ? v3Pill('path') : null;
    const anglePill = ANGLE_TYPES.includes(markType) ? v3Pill('angle') : null;
    const v3Mark = V3_MARK_IDS.includes(markType);
    const v3Dimensions = [shapePill, pathPill, anglePill, v3Mark ? colorPill : null].filter(p => p && isDimension(p));
    const markDimensions = [...details, ...tooltipPills, ...(labelPill ? [labelPill] : []),
      ...(sizePill ? [sizePill] : []), ...v3Dimensions].filter(isDimension);
    const anything = columnPills.length || rowPills.length || colorPill || sizePill || labelPill ||
      tooltipPills.length || details.length;

    const firstDimension = allDimensions[0] || null;
    const firstMeasure = rowMeasures[0] || colMeasures[0] || null;
    const timeDimension = allDimensions.find(p => p.field.isTime) || null;
    // A dimension on Columns draws upright bars; a dimension only on Rows lays
    // the same view on its side, which is what Tableau's Rows shelf means here.
    const orientation = marks.orientation || (colDimensions.length ? 'vertical' : rowDimensions.length ? 'horizontal' : 'none');

    const chosen = new Set([...allDimensions, ...allMeasures, ...details, ...tooltipPills,
      ...(colorPill ? [colorPill] : []), ...(sizePill ? [sizePill] : []), ...(labelPill ? [labelPill] : [])].map(p => p.column));
    function pick(kind, prefer) {
      const free = (kind === 'measure' ? meta.measures : meta.dimensions).filter(f => !f.hidden && !chosen.has(f.index));
      const plain = free.filter(f => !f.isTime && !f.isIdentifier);
      const order = kind === 'measure' ? free
        : prefer === 'time' ? [...free.filter(f => f.isTime), ...plain, ...free]
        : prefer === 'geo' ? [...free.filter(f => f.geographic_role || /zip|postal|state/i.test(f.name)), ...plain, ...free]
        : [...plain, ...free];
      const field = order[0] || null;
      if (!field) return null;
      chosen.add(field.index);
      return {column: field.index, aggregation: 'sum', field: field, picked: true};
    }
    const pickedNote = (p, why, where) => notes.push(markName + ' ' + why + ', so it uses ' + p.field.name +
      '. Put ' + where + ' to choose another.');
    const extraMeasures = [];
    const extra = {};

    let type = '', xPill = null, yPill = null, seriesPill = null, map = null;
    const needMeasure = why => {
      if (!yPill && (yPill = pick('measure'))) pickedNote(yPill, why, 'a measure on the shelves');
    };
    const needAxis = (why, prefer) => {
      if (!xPill && (xPill = pick('dimension', prefer))) pickedNote(xPill, why, 'a dimension on the shelves');
    };
    const secondMeasure = why => {
      if (allMeasures.length + extraMeasures.length >= 2 || (color && color.role === 'dimension')) return;
      const second = pick('measure');
      if (second) { extraMeasures.push(second); pickedNote(second, why, 'a second measure on the shelves'); }
    };
    const barLike = () => {
      type = orientation === 'horizontal' ? 'horizontal' : 'bar';
      xPill = orientation === 'horizontal' ? (rowDimensions[0] || firstDimension) : (colDimensions[0] || firstDimension);
      yPill = orientation === 'horizontal' ? (colMeasures[0] || rowMeasures[0] || null) : firstMeasure;
    };

    if (!anything) {
      issues.push('Add a grouping (company, recipient, cycle) and a dollar measure.');
    } else if (markType === 'automatic') {
      if (colMeasures.length && rowMeasures.length) {
        type = 'scatter'; xPill = colMeasures[0]; yPill = rowMeasures[0];
      } else if (orientation === 'none') {
        type = 'bar'; yPill = firstMeasure;
      } else {
        const dimension = (orientation === 'vertical' ? colDimensions[0] : rowDimensions[0]) || firstDimension;
        const measure = orientation === 'vertical'
          ? (rowMeasures[0] || colMeasures[0] || null)
          : (colMeasures[0] || rowMeasures[0] || null);
        xPill = dimension; yPill = measure;
        type = orientation === 'horizontal' ? 'horizontal' : (dimension?.field.isTime && measure ? 'line' : 'bar');
      }
    } else if (markType === 'circle') {
      yPill = rowMeasures[0] || allMeasures[0] || null;
      xPill = colMeasures.find(p => p !== yPill) || allMeasures.find(p => p !== yPill) || null;
      if (xPill && yPill) type = 'scatter';
      else {
        // One measure: one circle per group at its value (contract W6 'dot').
        type = 'dot'; xPill = firstDimension;
        needAxis('needs a grouping for its circles');
      }
    } else if (markType === 'histogram') {
      type = 'histogram'; yPill = firstMeasure;
      needMeasure('needs a measure to bin');
      // A shelf dimension stacks the bins by its members (contract W11, W3).
      if (firstDimension && !(color && color.role === 'dimension')) {
        seriesPill = firstDimension; extra.stack = 'stacked';
        notes.push('Histogram bins are stacked by ' + firstDimension.field.name + '.');
      }
    } else if (markType === 'box') {
      type = 'box'; xPill = firstDimension; yPill = firstMeasure;
      needMeasure('needs a measure to summarise');
    } else if (markType === 'line' || markType === 'area' || markType === 'dual-line') {
      // The first dimension placed is the axis: a category axis draws in its
      // own order, and a time axis stays chronological.
      type = markType === 'area' ? 'area' : 'line';
      xPill = firstDimension; yPill = firstMeasure;
      needAxis('needs an axis', 'time');
      if (markType === 'dual-line') {
        needMeasure('needs a measure');
        secondMeasure('needs a second measure for its second axis');
        extra.dualAxis = true; extra.dualMark = 'line';
      }
    } else if (markType === 'dot' || markType === 'series-dot') {
      type = 'dot'; xPill = firstDimension; yPill = firstMeasure;
      needAxis('needs a grouping for its circles');
      if (markType === 'series-dot' && !(color && color.role === 'dimension')) {
        seriesPill = allDimensions.find(p => p !== xPill) || null;
        if (!seriesPill && (seriesPill = pick('dimension'))) pickedNote(seriesPill, 'needs a breakdown for its circle series', 'a dimension on Colour');
      }
    } else if (markType === 'gantt') {
      type = 'gantt'; xPill = firstDimension; yPill = firstMeasure;
      needAxis('needs a grouping for its spans');
      const date = meta.fields.find(f => !f.hidden && f.type === 'date');
      if (date) extra.dateColumn = date.index;
      else {
        type = 'bar';
        notes.push('An observed date span needs a date column and this worksheet has none, so it is drawn as bars.');
      }
    } else if (markType === 'bullet') {
      type = 'bullet'; xPill = firstDimension; yPill = firstMeasure;
      needMeasure('needs a measure');
      secondMeasure('needs a second measure to compare');
    } else if (v3Mark) {
      // Square, Shape, Polygon and Density place points: two measures put them
      // on two axes (a point per group, or per worksheet row when nothing
      // groups them), one measure along the first grouping.
      yPill = rowMeasures[0] || allMeasures[0] || null;
      xPill = colMeasures.find(p => p !== yPill) || allMeasures.find(p => p !== yPill) || null;
      if (xPill && yPill) type = 'scatter';
      else {
        type = 'dot'; xPill = firstDimension; yPill = firstMeasure;
        needMeasure('needs a measure to place its marks');
        needAxis('needs a grouping for its marks');
      }
    } else if (['bar', 'text', 'stacked', 'combination', 'map', 'filled-map'].includes(markType)) {
      barLike();
      if (markType === 'stacked') {
        extra.stack = 'stacked';
        if (!(color && color.role === 'dimension') && allMeasures.length < 2) {
          seriesPill = allDimensions.find(p => p !== xPill) || null;
          if (!seriesPill && (seriesPill = pick('dimension'))) pickedNote(seriesPill, 'needs a breakdown to stack', 'a dimension on Colour');
        }
      } else if (markType === 'combination') {
        needAxis('needs an axis', 'time');
        needMeasure('needs a measure for its bars');
        secondMeasure('needs a second measure for its line');
        extra.dualAxis = true; extra.dualMark = 'line';
      } else if (markType === 'map' || markType === 'filled-map') {
        // Census C-16: a worksheet with a place field draws a real map. A
        // place on the shelves wins (the finest for a symbol map, a shape for
        // a filled map); with none placed, the sheet's own place field is
        // picked and named. Only a sheet with no place field at all is drawn
        // as bars, and it says so.
        const filled = markType === 'filled-map';
        const rank = f => geoRank(f, filled);
        let geography = allDimensions.filter(p => geoKind(p.field)).sort((a, b) => rank(a.field) - rank(b.field))[0] || null;
        if (!geography) {
          const field = meta.dimensions.filter(f => !f.hidden && geoKind(f) && !chosen.has(f.index))
            .sort((a, b) => geoRank(a, filled, true) - geoRank(b, filled, true))[0];
          if (field) {
            chosen.add(field.index);
            geography = {column: field.index, aggregation: 'sum', field: field, picked: true};
            pickedNote(geography, 'needs a place to group by', 'a ZIP, state, district or city field on the shelves');
          }
        }
        if (geography) {
          xPill = geography;
          const kind = geoKind(geography.field);
          let stateColumn = -1;
          if (kind === 'district') {
            const onShelf = allDimensions.find(p => geoKind(p.field) === 'state');
            const state = onShelf ? onShelf.field : meta.dimensions.find(f => !f.hidden && geoKind(f) === 'state');
            if (state) stateColumn = state.index;
            else notes.push('A district is placed with its state and this worksheet has no state field, so only districts written like MN-05 are mapped.');
          }
          map = {kind: kind, column: geography.column, stateColumn: stateColumn, filled: filled, name: geography.field.name};
          const combined = [...allDimensions, ...(color && color.role === 'dimension' ? [colorPill] : [])]
            .filter(p => p.column !== geography.column && p.column !== stateColumn);
          if (combined.length) notes.push('Each map mark combines every ' +
            [...new Set(combined.map(p => p.field.name))].join(' / ') + ' group at its place.');
          if ((color && color.role === 'measure') || sizePill || labelPill) notes.push('A map shades and sizes each place by ' +
            (firstMeasure ? firstMeasure.field.name : 'its row count') + '; Colour, Size and Label do not change it.');
        } else {
          needAxis('needs a place to group by');
          notes.push('Maps need a ZIP, state, congressional district or city field and this worksheet has none, so it is drawn as bars' +
            (xPill ? ' by ' + xPill.field.name : '') + '.');
        }
      }
    } else {
      // pie, doughnut, waterfall, pareto, radar, heatmap, highlight, treemap, bubbles
      type = markType; xPill = firstDimension; yPill = firstMeasure;
      needAxis('needs a grouping');
    }

    // Colour: a dimension separates series (or joins the grouping), a measure
    // shades each mark (contract W11 colorMeasure). Colour that repeats the
    // axis gives every mark its own colour, which the bars already have.
    // Angle (Pie, #51): the measure on Angle sets each wedge.
    if (type && anglePill) {
      if (isMeasure(anglePill)) yPill = anglePill;
      else notes.push('Angle takes a measure; ' + anglePill.field.name + ' joins the grouping instead.');
    }
    if (type && shapePill && isMeasure(shapePill)) notes.push('Shape takes a dimension; ' + shapePill.field.name + ' is in each mark’s tooltip.');
    if (type && color) {
      if (color.role === 'measure') extra.colorMeasure = {column: colorPill.column, aggregation: colorPill.aggregation};
      else if (!v3Mark && (!xPill || color.index !== xPill.column)) seriesPill = colorPill;
    }
    if (type && sizePill) {
      if (isMeasure(sizePill)) extra.size = {column: sizePill.column, aggregation: sizePill.aggregation};
      else notes.push('Size follows a measure; ' + sizePill.field.name + ' joins the grouping instead.');
    }
    if (type && labelPill) extra.label = {column: labelPill.column, aggregation: isMeasure(labelPill) ? labelPill.aggregation : 'first'};
    const tips = [...tooltipPills, ...details, ...[shapePill, pathPill].filter(Boolean)].filter(isMeasure).map(p => ({column: p.column, aggregation: p.aggregation}));
    if (type && tips.length) extra.tooltip = tips;

    const analytics = state.analytics || {};
    let transform = TRANSFORMS.includes(analytics.transform) ? analytics.transform : 'none';
    if (transform !== 'none' && type && v3Mark) {
      notes.push(markName + ' places each mark by its value, so period analysis is left off.');
      transform = 'none';
    }
    if (transform !== 'none' && type && map) {
      // A map has no time axis: it draws each place's total over the rows
      // selected, so period analysis is left off and said to be.
      notes.push('A map draws each place’s total over the selected rows; period analysis needs a chart with a time axis, so it is left off.');
      transform = 'none';
    } else if (transform !== 'none' && type) {
      if (!timeDimension) {
        notes.push('Time analysis needs a date, year or election-cycle dimension on Columns or Rows, so the chart is drawn without it.');
        transform = 'none';
      } else {
        // Contract W5: the chosen mark is kept, and Colour and every other
        // dimension become series over the periods.
        xPill = timeDimension;
      }
    }

    let series = -1;
    const seriesType = SERIES_TYPES.includes(type);
    if (type && seriesPill && seriesType) {
      series = seriesPill.column;
      if (transform === 'none' && PAIRED[type]) type = PAIRED[type];
    }

    let x = xPill ? xPill.column : -1;
    let y = yPill ? yPill.column : -1;
    let aggregation = yPill ? yPill.aggregation : 'count';
    const selectedAccounting = y >= 0 ? fieldAt(meta, y)?.accounting : null;
    if (aggregation === 'sum' && selectedAccounting?.safe_to_sum === false) notes.push(selectedAccounting.reason || 'This raw measure is not safe to sum. Choose the analysis amount.');
    if (type === 'histogram') x = -1;
    if (y === -1) aggregation = 'count';
    let grouping = [...new Set([...allDimensions, ...markDimensions].map(p => p.column))];
    if (seriesPill && !seriesType && !grouping.includes(seriesPill.column)) grouping.push(seriesPill.column);
    // A scatter over dimensions plots one circle per group of them, each an
    // aggregate (contract W9); with none it plots individual rows, as before.
    // Census B-S07 / C-04 (Paul, 2026-09-23: "nothing is logically
    // constrained"): a dimension on Detail (or Tooltip, Label, Size) sets a box's
    // or a histogram's level of detail the same way - each value is that group's
    // aggregate - so a box of recipient totals per company, or a histogram of
    // committee totals, is one gesture. Without one they read individual rows.
    const detailGrain = (type === 'box' || type === 'histogram') && markDimensions.length > 0;
    const grain = (type === 'scatter' && grouping.length) || detailGrain ? 'group' : 'row';
    const AGGREGATE_WORD = {sum: 'total', mean: 'average', count: 'row count', median: 'median', min: 'minimum', max: 'maximum', countd: 'distinct count'};
    // Marks v3: a Colour dimension on a scatter of groups splits the points by
    // member and colours them (contribution-plot draws it); the engine's group
    // grain would otherwise drop the colour silently.
    let scatterColour = -1;
    if (grain === 'group' && type === 'scatter' && color && color.role === 'dimension' && !v3Mark) {
      scatterColour = colorPill.column;
      if (!grouping.includes(scatterColour)) grouping.push(scatterColour);
      series = -1;
    }
    if (grain === 'group' && type === 'scatter') {
      extra.grain = 'group';
      extra.xAggregation = xPill.aggregation;
      notes.push('Each circle is one ' + grouping.map(column => (fieldAt(meta, column) || {name: 'group'}).name).join(' / ') +
        ' group; both measures are aggregated over it.');
    } else if (grain === 'group') {
      extra.grain = 'group';
      const detailNames = [...new Set(markDimensions.map(p => p.field.name))].join(' / ');
      const what = (AGGREGATE_WORD[aggregation] || aggregation) + ' of ' + (yPill ? yPill.field.name : 'rows');
      notes.push(type === 'box' ? 'Each box summarises the ' + what + ' of every ' + detailNames + ' group.'
        : 'The bins count ' + detailNames + ' groups by their ' + what + '.');
      // Each group must carry one Colour member for the bins to stack by it.
      if (type === 'histogram') grouping = [...new Set([...markDimensions.map(p => p.column), ...(series >= 0 ? [series] : [])])];
    } else if (['scatter', 'box', 'histogram'].includes(type)) {
      if (aggregation === 'count') aggregation = 'sum';
    }
    if ((type === 'histogram' && grain === 'row') || (type === 'scatter' && grain === 'row')) grouping = [];
    if (x < 0 && grouping.length && type !== 'scatter' && type !== 'histogram') x = grouping[0];
    if (x >= 0 && !['scatter', 'histogram'].includes(type)) grouping = [x, ...grouping.filter(column => column !== x && column !== series)];

    const measurePills = type === 'scatter'
      ? [xPill, yPill, ...allMeasures.filter(p => p !== xPill && p !== yPill)]
      : [yPill, ...allMeasures.filter(p => p !== yPill), ...extraMeasures];
    const seen = new Set(), measures = [];
    for (const p of measurePills) {
      if (!p || p.column < 0) continue;
      const entry = {column: p.column, aggregation: p === yPill ? aggregation : p.aggregation};
      const key = entry.column + '|' + entry.aggregation;
      if (seen.has(key)) continue;
      seen.add(key);
      measures.push(entry);
    }

    const timeField = xPill && xPill.field.isTime ? xPill.field : null;
    const period = ['year', 'cycle'].includes(analytics.period) ? analytics.period
      : timeField && /cycle/i.test(timeField.name) ? 'cycle' : 'year';
    const horizon = Number.isFinite(Number(analytics.horizon)) && Number(analytics.horizon) >= 1
      ? Math.trunc(Number(analytics.horizon)) : 2;

    const filters = (state.filters || [])
      .filter(f => f && filterActive(f) && fieldAt(meta, Number(f.column)))
      .map(f => normalizeFilter(f));
    if (filters.some(f => ['gte','lte'].includes(f.op))) notes.push('Number filters apply to rows, not totals. Filter totals in Verified contributions.');
    const pagePill = pill(state.pages);
    if (pagePill && fieldAt(meta, pagePill.column) && state.pageMember) {
      filters.push({column: pagePill.column, op: 'equals', value: String(state.pageMember)});
    }

    const params = state.params || {};
    // Several subjects over time are drawn as one series each (contract W5);
    // the Subject choice in Parameters still narrows time analysis to one.
    const subject = subjectField(meta.fields);
    if (subject && transform !== 'none' && !filters.some(f => f.column === subject.index)) {
      const chosenSubject = params.subject == null ? '' : String(params.subject);
      if (chosenSubject) filters.push({column: subject.index, op: 'equals', value: chosenSubject});
      else notes.push('Every Subject is drawn as its own series over time; choose one in Parameters to add up a single Subject.');
    }

    const config = type ? {
      type: type,
      x: x,
      y: y,
      dimensions: grouping,
      aggregation: aggregation,
      transform: transform,
      period: period,
      horizon: horizon,
      start: positiveInteger(params.start),
      end: positiveInteger(params.end),
      limit: positiveInteger(params.limit),
      maxGroups: groupLimitOf(params.maxGroups),
      splitMulti: !!params.splitMulti,
      series: series,
      filters: filters,
      lenient: true
    } : null;
    if (config) {
      if (measures.length) config.measures = measures;
      for (const key of Object.keys(extra)) config[key] = extra[key];
      // Marks v3: charts.js draws these marks through contribution-plot.js
      // (config.mark), so only a view that uses them takes the new path.
      if (scatterColour >= 0) {
        config.mark = 'circle';
        config.v3 = {glyph: '', shape: -1, glyphs: null, color: scatterColour, path: null};
      }
      if (v3Mark || pathPill) {
        const shape = card.encodings.shape;
        config.mark = markType;
        config.v3 = {glyph: markType === 'square' ? 'square' : (shape && shape.glyph) || '',
          shape: shapePill && isDimension(shapePill) ? shapePill.column : -1, glyphs: (shape && shape.glyphs) || null,
          color: colorPill && isDimension(colorPill) ? colorPill.column : -1,
          path: pathPill ? {column: pathPill.column, aggregation: pathPill.aggregation, role: pathPill.field.role} : null};
      }
      const width = Number(params.binWidth);
      if (type === 'histogram' && Number.isFinite(width) && width > 0) config.binWidth = width;
      if (type === 'histogram' && positiveInteger(params.bins)) config.bins = Math.min(1000, positiveInteger(params.bins));
    }

    if (config && transform === 'forecast' && (config.start || config.end || config.limit)) {
      notes.push('The same-phase baseline reads every worksheet row; clear the row range and row limit in Parameters.');
    }
    if (config && config.splitMulti && config.aggregation !== 'count') notes.push('Split multi-value cells repeats the measure across values, so the grand total is not shown. Choose Count for coverage, or use transaction-level ZIP data for money.');
    // Splitting rewrites the grouping cell, so a view with nothing on the axis
    // has no cell to split and the parameter would silently do nothing.
    if (config && config.splitMulti && config.x < 0 && config.series < 0) {
      notes.push('Splitting multi-value cells applies to the grouping field; put a dimension on Columns or Rows.');
    }

    return {
      config: config,
      issues: issues,
      notes: notes,
      renderMode: markType === 'text' ? 'table' : 'chart',
      markType: markType,
      map: config ? map : null,
      fields: meta
    };
  }

  const compileConfig = (state, columns) => compileReport(state, columns).config;

  /* -------------------------------------------------------- worksheet maps */

  // Census C-16. Which kind of place a worksheet field holds, read from its
  // geographic role or its name: 'zip', 'city-state', 'district', 'state', or
  // '' for a field that is not a place.
  function geoKind(field) {
    if (!field) return '';
    const role = String(field.geographic_role || '').toLowerCase(), name = String(field.name || '');
    if (/zip|postal/.test(role) || /\bzip|postal|zcta/i.test(name)) return 'zip';
    if (/city/.test(role) || /\bcit(y|ies)\b/i.test(name)) return 'city-state';
    if (/district/.test(role) || /\bdistricts?\b/i.test(name)) return 'district';
    if (/state/.test(role) || /\bstates?\b/i.test(name)) return 'state';
    return '';
  }
  // Among places the reader put on the shelves, a symbol map takes the finest
  // and a filled map a place with a shape (a district over its state). When
  // the Studio picks one itself it prefers the place every row can have: a
  // ZIP for symbols, a state for a filled map ("Filled state map").
  const GEO_ORDER = {symbol: ['zip', 'district', 'city-state', 'state'], filled: ['district', 'state', 'city-state', 'zip'],
    pickSymbol: ['zip', 'state', 'city-state', 'district'], pickFilled: ['state', 'district', 'city-state', 'zip']};
  const geoRank = (field, filled, picking) => {
    const order = picking ? (filled ? GEO_ORDER.pickFilled : GEO_ORDER.pickSymbol) : (filled ? GEO_ORDER.filled : GEO_ORDER.symbol);
    const at = order.indexOf(geoKind(field));
    return at < 0 ? 99 : at;
  };
  // Postal code, Census state FIPS and name for every US state and territory
  // the Census boundary layers carry.
  const US_STATES = ('AL01Alabama|AK02Alaska|AZ04Arizona|AR05Arkansas|CA06California|CO08Colorado|CT09Connecticut|' +
    'DE10Delaware|DC11District of Columbia|FL12Florida|GA13Georgia|HI15Hawaii|ID16Idaho|IL17Illinois|IN18Indiana|' +
    'IA19Iowa|KS20Kansas|KY21Kentucky|LA22Louisiana|ME23Maine|MD24Maryland|MA25Massachusetts|MI26Michigan|' +
    'MN27Minnesota|MS28Mississippi|MO29Missouri|MT30Montana|NE31Nebraska|NV32Nevada|NH33New Hampshire|' +
    'NJ34New Jersey|NM35New Mexico|NY36New York|NC37North Carolina|ND38North Dakota|OH39Ohio|OK40Oklahoma|' +
    'OR41Oregon|PA42Pennsylvania|RI44Rhode Island|SC45South Carolina|SD46South Dakota|TN47Tennessee|TX48Texas|' +
    'UT49Utah|VT50Vermont|VA51Virginia|WA53Washington|WV54West Virginia|WI55Wisconsin|WY56Wyoming|' +
    'AS60American Samoa|GU66Guam|MP69Northern Mariana Islands|PR72Puerto Rico|VI78U.S. Virgin Islands')
    .split('|').map(s => ({code: s.slice(0, 2), fips: s.slice(2, 4), name: s.slice(4)}));
  const STATE_BY_CODE = new Map(US_STATES.map(s => [s.code, s]));
  const STATE_BY_NAME = new Map(US_STATES.map(s => [s.name.toUpperCase(), s]));
  const stateOf = text => {
    const value = String(text == null ? '' : text).trim().toUpperCase();
    return STATE_BY_CODE.get(value) || STATE_BY_NAME.get(value) || null;
  };
  // A five-digit ZIP from a ZIP, ZIP+4 or a ZIP whose leading zero a
  // spreadsheet dropped (4 or 8 digits); '' for anything else.
  const zipOf = token => {
    const digits = String(token).trim().replace(/^(\d{5})-(\d{4})$/, '$1$2');
    if (!/^\d+$/.test(digits)) return '';
    const padded = digits.length === 4 || digits.length === 8 ? '0' + digits : digits;
    const zip = padded.length === 5 || padded.length === 9 ? padded.slice(0, 5) : '';
    return zip === '00000' ? '' : zip;
  };
  // 'MN-05' from 'MN-05', 'MN05', 'MN-AL', or a bare district number with its
  // row's state; district 0 and at-large are '00', as the Census codes them.
  const districtOf = (token, stateCell) => {
    const text = String(token == null ? '' : token).trim().toUpperCase();
    let found = /^([A-Z]{2})[-\s]?(\d{1,2}|AL)$/.exec(text), state = null, number = null;
    if (found) { state = stateOf(found[1]); number = found[2]; }
    else if ((found = /^(\d{1,2})(?:\.0+)?$/.exec(text)) || text === 'AL') {
      state = stateOf(stateCell); number = found ? found[1] : 'AL';
    }
    if (!state || number == null) return '';
    return state.code + '-' + (number === 'AL' ? '00' : number.padStart(2, '0'));
  };
  const NO_PLACE = '(No place)', SEVERAL_PLACES = '(Several places)';
  // The one place a worksheet cell names. A cell naming several different
  // places is '(Several places)', or every place ('a; b') when the reader
  // split multi-value cells, for the engine to split as it splits any cell.
  function placeOf(kind, cell, stateCell, split) {
    const tokens = String(cell == null ? '' : cell).split(';').map(s => s.trim()).filter(Boolean);
    const read = kind === 'zip' ? zipOf
      : kind === 'state' ? t => (stateOf(t) || {}).code || ''
      : kind === 'city-state' ? t => (stateOf(t.split(',').pop()) || {}).code || ''
      : kind === 'district' ? t => districtOf(t, stateCell)
      : () => '';
    const places = [...new Set(tokens.map(read).filter(Boolean))];
    if (!places.length) return NO_PLACE;
    if (places.length === 1) return places[0];
    return split ? places.join('; ') : SEVERAL_PLACES;
  }
  // The worksheet with one more column: each row's place. The engine then
  // aggregates the sheet's own measure per place, so an average or a median is
  // taken over the place's rows, never added up from pieces.
  function placeData(data, map, split) {
    const column = data.columns.length;
    const stateColumn = Number.isInteger(map.stateColumn) ? map.stateColumn : -1;
    const rows = data.rows.map(row => row.concat([placeOf(map.kind, row[map.column],
      stateColumn >= 0 ? row[stateColumn] : null, split)]));
    const columns = data.columns.concat([{index: column, name: map.name, type: 'category'}]);
    return {data: Object.assign({}, data, {columns: columns, rows: rows}), column: column};
  }
  // The configuration a worksheet map is built from: its first measure over
  // every place (no top-N cutoff), with the sheet's filters and row range.
  function mapConfig(config, placeColumn) {
    const out = {type: 'bar', x: placeColumn, y: config.y, dimensions: [placeColumn], aggregation: config.aggregation,
      transform: 'none', period: config.period, horizon: config.horizon, start: config.start, end: config.end,
      limit: config.limit, maxGroups: 'all', splitMulti: !!config.splitMulti, series: -1, filters: config.filters, lenient: true};
    if (Array.isArray(config.measures) && config.measures.length) out.measures = [config.measures[0]];
    return out;
  }
  // Engine groups (one per place) as MentatContributionMap groups.
  function mapGroups(result, kind) {
    return ((result && result.groups) || []).filter(g => !g.other).map(g => {
      const label = String(g.label);
      const base = {label: label, value: typeof g.value === 'number' && Number.isFinite(g.value) ? g.value : 0,
        records: Number.isFinite(g.n) ? g.n : 0};
      if (label === NO_PLACE || label === SEVERAL_PLACES) {
        return Object.assign(base, {unplaced: label === NO_PLACE ? 'no_place' : 'several_places'});
      }
      if (kind === 'zip') return Object.assign(base, {zip5: label});
      if (kind === 'district') {
        const state = STATE_BY_CODE.get(label.slice(0, 2));
        return Object.assign(base, {state: label.slice(0, 2), district: state ? state.fips + label.slice(3) : ''});
      }
      return Object.assign(base, {state: label});
    });
  }

  /* -------------------------------------------------------------- show me */

  const tile = (id, label, requirement, rule) => Object.assign(
    {id: id, label: label, requirement: requirement, mark: '', note: '',
     needDimensions: 0, needMeasures: 0, needTime: false}, rule);

  // Paul, 2026-09-23: "...all graph types properly register and nothing is
  // logically constrained". Every tile is a real mark in both sources and is
  // never greyed: the requirement is what the tile PREFERS, and a click draws
  // with what is on the shelves, seeding what it can and letting the compiler
  // pick the rest (and say so).
  const SHOW_ME = [
    tile('text-table', 'Text table', '1 or more Dimensions, 1 or more Measures', {mark: 'text', needDimensions: 1, needMeasures: 1}),
    tile('heat-map', 'Heat map', '1 or more Dimensions, 1 or 2 Measures', {mark: 'heatmap', needDimensions: 2, needMeasures: 1}),
    tile('highlight-table', 'Highlight table', '1 or more Dimensions, 1 Measure', {mark: 'highlight', needDimensions: 2, needMeasures: 1}),
    tile('symbol-map', 'Symbol map', '1 geographic Dimension, 0 to 2 Measures', {mark: 'map', needDimensions: 1,
      note: 'Draws from a ZIP, state, congressional district or city field; a worksheet with none shows the same groups as bars.'}),
    tile('filled-map', 'Filled map', '1 geographic Dimension, 0 or 1 Measures', {mark: 'filled-map', needDimensions: 1,
      note: 'Fills states or districts from the sheet’s place field; a worksheet with none shows the same groups as bars.'}),
    tile('pie-chart', 'Pie chart', '1 or more Dimensions, 1 or 2 Measures', {mark: 'pie', needDimensions: 1, needMeasures: 1,
      note: 'Shares need nonnegative values; negative groups are left out and counted in a note.'}),
    tile('horizontal-bars', 'Horizontal bars', '0 or more Dimensions, 1 or more Measures', {mark: 'bar', needDimensions: 1, needMeasures: 1}),
    tile('stacked-bars', 'Stacked bars', '1 or more Dimensions, 1 or more Measures', {mark: 'stacked', needDimensions: 2, needMeasures: 1,
      note: 'Each bar stacks its Colour breakdown, or its measures when there are several.'}),
    tile('side-by-side-bars', 'Side-by-side bars', '1 or more Dimensions, 1 or more Measures', {mark: 'bar', needDimensions: 2, needMeasures: 1}),
    tile('treemap', 'Treemap', '1 or more Dimensions, 1 or 2 Measures', {mark: 'treemap', needDimensions: 1, needMeasures: 1}),
    tile('circle-views', 'Circle views', '1 or more Dimensions, 1 or more Measures', {mark: 'dot', needDimensions: 1, needMeasures: 1,
      note: 'One circle per group at its value.'}),
    tile('side-by-side-circles', 'Side-by-side circles', '1 or more Dimensions, 1 or more Measures', {mark: 'series-dot', needDimensions: 2, needMeasures: 1,
      note: 'One circle series per Colour member.'}),
    tile('lines-continuous', 'Lines (continuous)', '1 Date, 1 or more Measures', {mark: 'line', needDimensions: 1, needMeasures: 1, needTime: true}),
    tile('lines-discrete', 'Lines (discrete)', '1 Date, 1 or more Measures', {mark: 'line', needDimensions: 1, needMeasures: 1, needTime: true,
      note: 'Groups the dates into calendar-year or election-cycle periods.'}),
    tile('dual-lines', 'Dual lines', '1 Date, 2 Measures', {mark: 'dual-line', needDimensions: 1, needMeasures: 2, needTime: true,
      note: 'Two measures on two axes, or the Colour breakdown on one.'}),
    tile('area-continuous', 'Area chart (continuous)', '1 Date, 1 or more Measures', {mark: 'area', needDimensions: 1, needMeasures: 1, needTime: true}),
    tile('area-discrete', 'Area chart (discrete)', '1 Date, 1 or more Measures', {mark: 'area', needDimensions: 1, needMeasures: 1, needTime: true,
      note: 'Groups the dates into calendar-year or election-cycle periods.'}),
    tile('dual-combination', 'Dual combination', '1 Date, 2 Measures', {mark: 'combination', needDimensions: 1, needMeasures: 2, needTime: true,
      note: 'The first measure as bars, the second as a line on its own axis.'}),
    tile('scatter-plot', 'Scatter plot', '0 or more Dimensions, 2 to 4 Measures', {mark: 'circle', needMeasures: 2}),
    tile('histogram', 'Histogram', '0 Dimensions, 1 Measure', {mark: 'histogram', needMeasures: 1}),
    tile('box-and-whisker', 'Box-and-whisker', '0 or more Dimensions, 1 or more Measures', {mark: 'box', needMeasures: 1}),
    tile('gantt', 'Gantt', '1 Date, 0 or 1 Measures', {mark: 'gantt', needDimensions: 1,
      note: 'First-to-last observed date of each group.'}),
    tile('bullet-graph', 'Bullet graph', '0 or more Dimensions, 2 Measures', {mark: 'bullet', needMeasures: 2}),
    tile('packed-bubbles', 'Packed bubbles', '1 or more Dimensions, 1 or 2 Measures', {mark: 'bubbles', needDimensions: 1, needMeasures: 1})
  ];

  function showMeSelection(state, columns, selected) {
    const meta = classifyFields(columns, state && state.types);
    const indices = [];
    const add = value => {
      const p = pill(value);
      if (p && indices.indexOf(p.column) === -1) indices.push(p.column);
    };
    (state && state.columns || []).forEach(add);
    (state && state.rows || []).forEach(add);
    const marks = (state && state.marks) || {};
    ['color', 'size', 'label'].forEach(key => add(marks[key]));
    (marks.detail || []).forEach(add);
    (selected || []).forEach(add);
    const fields = indices.map(index => fieldAt(meta, index)).filter(Boolean);
    return {
      fields: fields,
      dimensions: fields.filter(f => f.role === 'dimension').length,
      measures: fields.filter(f => f.role === 'measure').length,
      times: fields.filter(f => f.role === 'dimension' && f.isTime).length
    };
  }

  const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');

  // Every tile is enabled. `fits` says whether the shelves already hold what
  // the tile prefers; `fill` says, in the reader's words, what a click will
  // pick from the source when they do not.
  function showMeEligibility(selection) {
    return SHOW_ME.map(t => {
      const missing = [];
      if (selection.dimensions < t.needDimensions) missing.push(plural(t.needDimensions - selection.dimensions, 'dimension'));
      if (selection.measures < t.needMeasures) missing.push(plural(t.needMeasures - selection.measures, 'measure'));
      if (t.needTime && selection.times < 1) missing.push('a date, year or election-cycle dimension');
      return {id: t.id, label: t.label, requirement: t.requirement, note: t.note, mark: t.mark, enabled: true, reason: '',
        fits: missing.length === 0, missing: missing,
        fill: missing.length ? 'Draws with the fields on the shelves and picks ' + missing.join(' and ') + ' from the source' : ''};
    });
  }

  // A tile changes the mark and keeps every pill, filter, aggregate and
  // encoding on the sheet (census B-S28). Shelves that are empty are seeded
  // from the fields in use, then from the source itself, so a click on an
  // empty sheet still draws.
  function showMeApplyV2(tileId, state, columns, selected) {
    const t = SHOW_ME.find(item => item.id === tileId);
    if (!t) return clone(state);
    const meta = classifyFields(columns, state && state.types);
    const selection = showMeSelection(state, columns, selected);
    const next = clone(state);
    next.marks = Object.assign({}, next.marks, {type: t.mark});
    if (tileId === 'horizontal-bars') next.marks.orientation = 'horizontal';
    else if (next.marks.orientation) delete next.marks.orientation;
    // Discrete lines and areas are period totals; the continuous tiles take a
    // period grouping away again. Nothing else about the analytics changes.
    const transform = (next.analytics || {}).transform;
    if (['lines-discrete', 'area-discrete'].includes(tileId)) next.analytics = Object.assign({}, next.analytics, {transform: 'annual'});
    else if (['lines-continuous', 'area-continuous'].includes(tileId) && transform === 'annual') next.analytics = Object.assign({}, next.analytics, {transform: 'none'});
    if (shelf(state.columns).length || shelf(state.rows).length) return next;

    const usable = f => !f.hidden;
    const inUse = selection.fields;
    const dimensions = [...inUse.filter(f => f.role === 'dimension'),
      ...meta.dimensions.filter(f => usable(f) && !inUse.includes(f))];
    const plain = dimensions.filter(f => !f.isTime && !f.isIdentifier);
    const times = dimensions.filter(f => f.isTime);
    const measures = [...inUse.filter(f => f.role === 'measure'),
      ...meta.measures.filter(f => usable(f) && !inUse.includes(f))];
    const measurePill = index => {
      const existing = shelf(state.columns).concat(shelf(state.rows)).find(p => p.column === index);
      return {column: index, aggregation: existing ? existing.aggregation : 'sum'};
    };
    const dim = f => ({column: f.index, aggregation: 'sum'});
    const dimension = position => (plain[position] || dimensions.filter(f => !plain.includes(f))[position - plain.length] || null);
    const colour = () => {
      if (next.marks.color != null) return;
      const used = next.columns.concat(next.rows).map(p => p.column);
      // A date belongs on the axis, so Colour prefers a plain dimension.
      const candidate = plain.find(f => used.indexOf(f.index) === -1)
        || dimensions.find(f => used.indexOf(f.index) === -1);
      if (candidate) next.marks.color = {column: candidate.index, aggregation: 'sum'};
    };
    const axis = preferTime => (preferTime ? times[0] : null) || dimension(0) || times[0] || null;
    next.columns = []; next.rows = [];
    const id = t.id;
    if (id === 'horizontal-bars') {
      if (dimension(0)) next.rows = [dim(dimension(0))];
      if (measures[0]) next.columns = [measurePill(measures[0].index)];
    } else if (id === 'scatter-plot' || id === 'bullet-graph') {
      if (measures[0]) next.columns = [measurePill(measures[0].index)];
      if (measures[1]) next.rows = [measurePill(measures[1].index)];
      if (id === 'bullet-graph' && dimension(0)) next.columns.unshift(dim(dimension(0)));
    } else if (id === 'histogram') {
      if (measures[0]) next.rows = [measurePill(measures[0].index)];
    } else if (['lines-continuous', 'lines-discrete', 'dual-lines', 'area-continuous', 'area-discrete',
      'dual-combination', 'gantt'].includes(id)) {
      const x = axis(true);
      if (x) next.columns = [dim(x)];
      if (measures[0]) next.rows = [measurePill(measures[0].index)];
      if ((id === 'dual-lines' || id === 'dual-combination') && measures[1]) next.rows.push(measurePill(measures[1].index));
      if (id === 'dual-lines' && !measures[1]) colour();
    } else if (id === 'heat-map' || id === 'highlight-table') {
      if (dimension(0)) next.columns = [dim(dimension(0))];
      if (dimension(1)) next.rows = [dim(dimension(1))];
      if (measures[0]) next.rows.push(measurePill(measures[0].index));
    } else if (id === 'symbol-map' || id === 'filled-map') {
      const place = dimensions.filter(f => geoKind(f)).sort((a, b) => geoRank(a, id === 'filled-map', true) - geoRank(b, id === 'filled-map', true))[0]
        || dimension(0);
      if (place) next.columns = [dim(place)];
      if (measures[0]) next.rows = [measurePill(measures[0].index)];
    } else {
      // text, pie, stacked, side-by-side, treemap, circles, box, bubbles
      if (dimension(0)) next.columns = [dim(dimension(0))];
      if (measures[0]) next.rows = [measurePill(measures[0].index)];
      if (['stacked-bars', 'side-by-side-bars', 'side-by-side-circles'].includes(id)) colour();
    }
    return next;
  }

  // Show Me draws a view of its own: one card keeps its v3 fields, and a dual
  // axis collapses to the mark the tile chose (core.adoptMarks).
  const showMeApply = (tileId, state, columns, selected) => SHOW_ME.some(t => t.id === tileId)
    ? adoptMarks(showMeApplyV2(tileId, state, columns, selected)) : clone(state);

  const RECOMMENDED_TILE = {bar: 'horizontal-bars', pie: 'pie-chart', line: 'lines-continuous',
    scatter: 'scatter-plot', box: 'box-and-whisker', histogram: 'histogram'};

  function recommendedTile(columns) {
    const choices = math.recommend(columns || []) || [];
    for (const choice of choices) if (RECOMMENDED_TILE[choice.type]) return RECOMMENDED_TILE[choice.type];
    return '';
  }

  /* --------------------------------------------------------------- filters */

  // Include/exclude member lists join the four original scalar operations. A
  // list filter with no members chosen is not a filter at all: it is dropped
  // before it reaches either engine, so an empty choice never means "nothing".
  const FILTER_OPS = [['equals', 'Equals'], ['contains', 'Contains'], ['gte', 'At least'],
    ['lte', 'At most'], ['between', 'Between'], ['in', 'Is one of'], ['notin', 'Is not one of']];
  const LIST_OPS = ['in', 'notin'];
  const RANGE_OPS = ['between'];

  const filterValues = filter => !filter ? []
    : Array.isArray(filter.value) ? filter.value.map(String)
    : filter.value == null || filter.value === '' ? [] : [String(filter.value)];

  function filterActive(filter) {
    if (!filter || !filter.op) return false;
    if (LIST_OPS.indexOf(filter.op) !== -1) return filterValues(filter).length > 0;
    if (RANGE_OPS.indexOf(filter.op) !== -1) return filterBounds(filter).some(bound => bound !== '');
    return filter.value !== '' && filter.value != null;
  }

  // A range keeps both ends in one value, so an open end stays an empty string
  // rather than a zero that would quietly exclude everything below it.
  function filterBounds(filter) {
    const value = filter && filter.value;
    const list = Array.isArray(value) ? value : [value, ''];
    return [list[0] == null ? '' : String(list[0]), list[1] == null ? '' : String(list[1])];
  }

  function normalizeFilter(filter) {
    const op = FILTER_OPS.some(o => o[0] === (filter && filter.op)) ? filter.op : 'equals';
    const out = {column: Number(filter.column), op: op,
      value: LIST_OPS.indexOf(op) !== -1 ? filterValues(filter)
        : RANGE_OPS.indexOf(op) !== -1 ? filterBounds(filter)
        : String(filter && filter.value == null ? '' : filter.value)};
    if (filter && filter.stage) out.stage = filter.stage;
    return out;
  }

  // The one-line description a filter pill and the Inspector row both show.
  function filterSummary(filter, field, memberCount) {
    if (!filter) return '';
    const name = field ? field.name : 'Field';
    const money = !!(field && (field.unit === 'cents' || /amount|receipt|giving|dollar/i.test(field.name)));
    const show = value => {
      const number = Number(value);
      return money && Number.isFinite(number)
        ? number.toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0})
        : String(value);
    };
    const values = filterValues(filter);
    if (RANGE_OPS.indexOf(filter.op) !== -1) {
      const [low, high] = filterBounds(filter);
      if (low !== '' && high !== '') return name + ' ' + show(low) + ' to ' + show(high);
      if (low !== '') return name + ' \u2265 ' + show(low);
      if (high !== '') return name + ' \u2264 ' + show(high);
      return name + ': any value';
    }
    if (LIST_OPS.indexOf(filter.op) !== -1) {
      if (!values.length) return name + ': any value';
      const total = Number.isFinite(Number(memberCount)) && Number(memberCount) >= values.length ? Number(memberCount) : null;
      const listed = values.length <= 3 ? values.join(', ')
        : total ? values.length + ' of ' + total + ' values' : values.length + ' values';
      return name + (filter.op === 'in' ? ': ' : ' excludes ') + listed;
    }
    if (filter.op === 'contains') return name + ' contains ' + show(filter.value);
    if (filter.op === 'gte') return name + ' \u2265 ' + show(filter.value);
    if (filter.op === 'lte') return name + ' \u2264 ' + show(filter.value);
    return name + ' = ' + show(filter.value);
  }

  /* ---------------------------------------------------- measures and types */

  // Only offer what the active engine can actually compute. The verified server
  // aggregates one money column six ways; every other verified measure is a
  // source aggregate that is already defined, so it has no aggregation menu.
  const VERIFIED_AGGREGATED = 'amount';

  // A worksheet measure offers every aggregation, Count distinct included; a
  // text or date column read as a measure is counted (Count, Count distinct).
  // The verified server has no distinct count of money, so amount keeps its six.
  const COUNTED = AGGREGATIONS.filter(a => a[0] === 'count' || a[0] === 'countd');
  function aggregationsFor(field, engine) {
    if (!field || field.role !== 'measure') return [];
    if (engine !== 'verified') return field.type === 'number' ? AGGREGATIONS.slice() : COUNTED.slice();
    return field.id === VERIFIED_AGGREGATED ? AGGREGATIONS.filter(a => a[0] !== 'countd') : [];
  }

  // Political-money shortcuts. Each one either exists in this source or says
  // why it does not, instead of failing when the query runs.
  const MEASURE_PRESETS = [
    {id: 'total-giving', label: 'Total giving', aggregation: 'sum'},
    {id: 'gift-count', label: 'Gift count', aggregation: 'count'},
    {id: 'average-gift', label: 'Average gift', aggregation: 'mean'},
    {id: 'distinct-contributors', label: 'Distinct contributors', aggregation: 'sum'}
  ];

  function measurePresets(columns, engine, overrides) {
    const meta = classifyFields(columns, overrides);
    const byId = id => meta.fields.find(f => f.id === id) || null;
    const firstMeasure = meta.measures[0] || null;
    return MEASURE_PRESETS.map(preset => {
      const view = Object.assign({}, preset, {available: false, reason: '', column: -1});
      if (engine === 'verified') {
        const wanted = preset.id === 'gift-count' ? 'records'
          : preset.id === 'distinct-contributors' ? 'contributors' : VERIFIED_AGGREGATED;
        const field = byId(wanted);
        if (!field) { view.reason = 'This source has no ' + wanted + ' measure.'; return view; }
        view.column = field.index;
        view.aggregation = preset.id === 'average-gift' ? 'mean' : 'sum';
        view.available = true;
        return view;
      }
      if (preset.id === 'distinct-contributors') {
        view.reason = 'Distinct contributor counts are computed from source records; use Verified contributions.';
        return view;
      }
      if (!firstMeasure) { view.reason = 'This worksheet has no numeric measure.'; return view; }
      view.column = firstMeasure.index;
      view.available = true;
      return view;
    });
  }

  function applyMeasurePreset(id, state, columns, engine) {
    const preset = measurePresets(columns, engine, state && state.types).find(p => p.id === id);
    const next = clone(state);
    if (!preset || !preset.available) return next;
    const entry = {column: preset.column, aggregation: preset.aggregation};
    const onColumns = shelf(next.columns).some(p => p.column === preset.column);
    const measures = classifyFields(columns, state && state.types).measures.map(f => f.index);
    const strip = list => shelf(list).filter(p => measures.indexOf(p.column) === -1);
    if (onColumns) next.columns = strip(next.columns).concat(entry);
    else next.rows = strip(next.rows).concat(entry);
    return next;
  }

  // Date parts. Verified sources carry real derived columns, so the pill simply
  // moves to that column and the grouping stays on the server. A worksheet has
  // one date column, so the part is expressed with the existing period
  // transform instead of inventing a column the worksheet does not have.
  const DATE_PARTS = [['cycle', 'Election cycle'], ['year', 'Year'], ['quarter', 'Quarter'],
    ['month', 'Month'], ['date', 'Exact date']];
  const DATE_FIELD_IDS = DATE_PARTS.map(part => part[0]);

  // Date-likeness is read from the catalog's own date hierarchy first. The
  // detected time flag is only a fallback, so a date part cannot disappear
  // because that flag was lost somewhere between the catalog and the pill.
  const isDateLike = field => !!field && field.role === 'dimension' &&
    (DATE_FIELD_IDS.indexOf(field.id) !== -1 || !!field.isTime);

  function datePartsFor(field, columns, engine, state) {
    if (!isDateLike(field)) return [];
    if (engine === 'verified') {
      const meta = classifyFields(columns);
      return DATE_PARTS
        .map(([id, label]) => {
          const target = meta.fields.find(f => f.id === id);
          return {id: id, label: label, column: target ? target.index : -1, current: field.id === id};
        })
        .filter(part => part.column >= 0);
    }
    // A worksheet has one date column, so the part is the period the existing
    // transform groups by rather than a column the worksheet does not have.
    const analytics = (state && state.analytics) || {};
    const grouped = analytics.transform === 'annual';
    return [['cycle', 'Election cycle'], ['year', 'Year']]
      .map(([id, label]) => ({id: id, label: label, column: field.index, current: grouped && analytics.period === id}));
  }

  /* ------------------------------------------------------ company overview */

  // One company, four questions. Read-only presets: no shelf editing, and each
  // one names the field it groups by so "Open in builder" is the same view.
  const COMPANY_OVERVIEW = [
    {id: 'recipient-categories', title: 'Recipient categories', dimension: 'recipient_category',
     measure: 'amount', mark: 'bar', limit: 10, sort: 'desc',
     note: 'Net selected amount by recipient category, largest first.'},
    {id: 'giving-by-cycle', title: 'Giving by election cycle', dimension: 'cycle',
     measure: 'amount', mark: 'line', limit: 40, sort: 'none',
     note: 'Net selected amount by election cycle, in chronological order.'},
    {id: 'top-recipients', title: 'Top recipients', dimension: 'recipient',
     measure: 'amount', mark: 'bar', limit: 8, sort: 'desc',
     note: 'The eight largest recipient committees in this scope.'},
    {id: 'party-split', title: 'Party split', dimension: 'party',
     measure: 'amount', mark: 'doughnut', limit: 10, sort: 'desc',
     note: 'The split is by recipient party. This source has no employee-level field.'}
  ];

  const companyOverviewPresets = () => COMPANY_OVERVIEW.map(preset => Object.assign({}, preset));

  function companyOverviewMissing(preset, columns) {
    const meta = classifyFields(columns);
    return [preset.dimension, preset.measure].filter(id => !meta.fields.some(f => f.id === id));
  }

  function companyOverviewSheet(preset, columns, scope, base) {
    const meta = classifyFields(columns);
    const find = id => meta.fields.find(f => f.id === id) || null;
    const next = base ? clone(base) : createSheet(preset.title, 'overview');
    const dimension = find(preset.dimension);
    const measure = find(preset.measure);
    next.columns = dimension ? [{column: dimension.index, aggregation: 'sum'}] : [];
    next.rows = measure ? [{column: measure.index, aggregation: 'sum'}] : [];
    next.marks = {type: preset.mark, color: null, size: null, label: null, detail: [], tooltip: []};
    next.analytics = Object.assign({}, next.analytics || {},
      {transform: 'none', totals: false, average: false, median: false});
    next.params = Object.assign({}, next.params || {},
      {start: 0, end: 0, limit: 0, maxGroups: preset.limit, splitMulti: false});
    next.pages = null;
    next.pageMember = '';
    next.sort = preset.sort;
    next.filters = [];
    const scoped = scope || {};
    const company = find('company');
    const cycle = find('cycle');
    if (company && scoped.company) next.filters.push({column: company.index, op: 'equals', value: String(scoped.company), stage: 'row'});
    if (cycle && scoped.cycle) next.filters.push({column: cycle.index, op: 'equals', value: String(scoped.cycle), stage: 'row'});
    return next;
  }

  /* -------------------------------------------------------- result helpers */

  const groupValue = group => group.value != null ? group.value : group.median != null ? group.median : 0;

  // Paul, 2026-09-23: "nothing is logically constrained". Sort works on every
  // mark, time axes included (census B-S15, contract W13); 'none' is the
  // engine's own order, which on a time axis is chronological, so a time view
  // still opens in time order. sortLocked is kept for its callers and answers
  // false for every view.
  const sortLocked = () => false;
  // 'az' and 'za' order the groups by their label (contract W13 {by: 'label'}).
  const SORTS = ['none', 'asc', 'desc', 'az', 'za'];

  function sortResult(result, direction, options) {
    if (!result || !Array.isArray(result.groups)) return result;
    const byLabel = (options && options.by === 'label') || direction === 'az' || direction === 'za';
    const ascending = direction === 'asc' || direction === 'az';
    if (!['asc', 'desc', 'az', 'za'].includes(direction)) return result;
    // A scatter's points are not an axis order; there is nothing to reorder.
    if (result.type === 'scatter') return result;
    const size = group => seriesResult(result)
      ? (group.values || []).reduce((total, v) => total + (v.value == null ? 0 : v.value), 0)
      : groupValue(group);
    const compare = byLabel
      ? (a, b) => String(a.label).localeCompare(String(b.label), undefined, {numeric: true})
      : (a, b) => size(a) - size(b);
    // An Other rollup is the tail the engine folded away, not a member of the
    // view, so it keeps the last position however the real groups are ordered.
    const rollup = result.groups.filter(g => g.other);
    const groups = result.groups.filter(g => !g.other)
      .sort((a, b) => ascending ? compare(a, b) : compare(b, a));
    return Object.assign({}, result, {groups: groups.concat(rollup)});
  }

  // Mark values in the order the renderer appends them, so a caller can label
  // marks without knowing how the SVG was drawn. Empty where labels do not fit.
  // A result is drawn as series only when every group carries its per-series
  // values. Scatter (points, no groups) and box (groups without values) results
  // also name their Colour members in `series` (contract W11) and are not series
  // results here; this is the predicate chartSVG and tableRows use.
  const seriesResult = result => !!(result && result.series && Array.isArray(result.groups) &&
    result.groups.every(g => g && Array.isArray(g.values)));

  function markValues(result) {
    if (!result) return [];
    if (seriesResult(result)) return result.groups.reduce((all, g) => all.concat(g.values.filter(v => v.value != null).map(v => v.value)), []);
    if (['scatter', 'box', 'pie', 'doughnut', 'radar'].indexOf(result.type) !== -1) return [];
    return (result.groups || []).map(g => g.value);
  }

  // A view separated by Colour or by several measures gets the mean and median
  // of every mark it draws (census B-S19): the rule is labelled as such, so it
  // is never read as one series' own average.
  function referenceValues(result, analytics) {
    const wanted = analytics || {};
    const values = markValues(result).filter(v => typeof v === 'number' && Number.isFinite(v));
    const lines = [];
    if (!values.length) return lines;
    const every = seriesResult(result) ? ' of every mark' : '';
    if (wanted.average) lines.push({kind: 'average', label: 'Average' + every, value: values.reduce((a, b) => a + b, 0) / values.length});
    if (wanted.median) lines.push({kind: 'median', label: 'Median' + every, value: math.quantile(values.slice().sort((a, b) => a - b), .5)});
    return lines;
  }

  // A grand total only means something when the marks are additive parts of one
  // whole. Independent series, bin counts, five-number summaries, shares of a
  // circle and a sum of means are not, so the studio says why instead of
  // printing a number that reads as money.
  const UNTOTALLED = ['histogram', 'scatter', 'box', 'pie', 'doughnut', 'radar'];

  function totalNote(result, config) {
    if (!result) return '';
    if (result.totalRefusal) return result.totalRefusal;
    if (result.split) return 'Grand total is not shown: split coverage groups can overlap.';
    if (['yoy', 'change'].includes(result.transform || config?.transform)) return 'Grand total is not shown for percentages or period changes.';
    if (result.accounting?.safe_to_sum === false) return result.accounting.reason || 'Grand total is not shown for this accounting grain.';
    if (seriesResult(result)) return 'Grand total is not shown: series are independent.';
    if (result.type === 'histogram') return 'Grand total is not shown for histograms (bins are row counts).';
    if (UNTOTALLED.indexOf(result.type) !== -1) return 'Grand total is not shown for ' + result.type + ' views.';
    if (config && ['sum', 'count'].indexOf(config.aggregation) === -1) return 'Grand total is only available for SUM and COUNT.';
    return '';
  }

  function grandTotal(result, config) {
    if (totalNote(result, config)) return null;
    if ((result?.transform || config?.transform) === 'forecast') return Number.isFinite(result.observedTotal) ? result.observedTotal : null;
    const values = markValues(result).filter(v => typeof v === 'number' && Number.isFinite(v));
    return values.length ? values.reduce((a, b) => a + b, 0) : null;
  }

  const AGGREGATION_LABEL = {sum: 'SUM', mean: 'AVG', count: 'CNT', median: 'MED', min: 'MIN', max: 'MAX', countd: 'CNTD'};

  function rowsToMarksSummary(result, config, columns) {
    if (!result || !config) return null;
    const meta = classifyFields(columns);
    const measure = fieldAt(meta, config.y);
    const marks = result.type === 'scatter' ? (result.points || []).length
      : seriesResult(result) ? result.groups.reduce((n, g) => n + g.values.filter(v => v.value != null).length, 0)
      : (result.groups || []).length;
    const viewColumns = result.type === 'scatter' ? 1 : (result.groups || []).length;
    const viewRows = result.series ? result.series.length : 1;
    const total = grandTotal(result, config);
    const name = config.aggregation === 'count' && config.y === -1 ? 'rows' : measure ? measure.name : 'value';
    return {
      marks: marks,
      viewRows: viewRows,
      viewColumns: viewColumns,
      selected: result.selected == null ? null : result.selected,
      excluded: result.excluded == null ? null : result.excluded,
      measureLabel: ((result.transform || config.transform) === 'forecast' ? 'Observed ' : '') +
        ((result.transform || config.transform) === 'yoy' ? 'YoY %(' + name + ')' :
        (result.transform || config.transform) === 'change' ? 'Period change(' + name + ')' :
        (result.split ? 'Coverage ' : '') + AGGREGATION_LABEL[config.aggregation] + '(' + name + ')'),
      total: total
    };
  }

  /* ------------------------------------------- what a payload says about its source */

  // The server keeps a saved view's source, and a briefing item's source and
  // provenance, as objects, and replaces anything else with an empty one. A
  // caption sent bare is therefore either refused outright (a saved view came
  // back 400) or silently thrown away (a briefing item's workbook digest, which
  // then printed as "[object Obje" in every deck). The shapes are built here,
  // in the one module both the browser and the test runner can load, so a test
  // can read exactly what the client will put on the wire without a document.

  // The caption a reader sees for a source, from any shape it has ever had:
  // today's object, an older bare string, or nothing at all. It never hands an
  // object to string concatenation.
  function sourceLabelOf(value) {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    return value.label || value.datasetLabel || value.reportName || '';
  }

  // A source known only by its caption - a view recovered from the old
  // browser-local store, which never recorded anything else.
  function captionSource(label) {
    return label ? {label: String(label)} : {};
  }

  // The same recovery, from a store key rather than a resolved label. This
  // module never touches the network, so a contributions key
  // ('contributions|<dataset>|<sha>') cannot look the dataset up in a
  // catalog - but the dataset id IS the industry label (S4_DESIGN.md section
  // 0.2), so the key alone is enough to recover industry and dataset instead
  // of losing them the way a bare caption always did.
  function captionSourceForKey(key) {
    const parts = String(key || '').split('|');
    if (parts[0] === 'contributions') {
      const label = parts[1] || '';
      return label ? {label: label, dataset: label, industry: label} : {};
    }
    return captionSource(parts.filter(Boolean).pop() || '');
  }

  // What a saved view carries: enough to name the report, its digest and the
  // folder it came from a year from now, plus the caption the menu prints.
  function viewSourceRecord(state, chosen) {
    const source = state || {};
    const record = {};
    const label = chosen ? (chosen.label || chosen.id) : (source.reportName || '');
    if (label) record.label = label;
    for (const key of ['reportName', 'sha256', 'runName', 'sheet', 'mode', 'dataset', 'industry']) {
      if (source[key]) record[key] = source[key];
    }
    if (chosen) {
      record.dataset = chosen.id;
      if (chosen.label) record.datasetLabel = chosen.label;
      // The DE-021 fix: a chosen catalog entry resolves to an industry,
      // falling back to its id when an older catalog has not been extended
      // with the field yet (id and industry are the same string today -
      // S4_DESIGN.md section 0.2). A catalog entry with neither sets no key:
      // an empty industry is the shape studio_api.json refuses, so writing
      // one here would send the server a payload it must reject.
      const industry = chosen.industry || chosen.id;
      if (typeof industry === 'string' && industry) record.industry = industry;
    }
    return record;
  }

  // What a chart carries into the briefing, and from there into a slide.
  function chartSourceRecord(state) {
    const source = state || {};
    const label = source.reportName || '';
    const record = {label: label, reportName: label, runName: source.runName || '',
      sheet: source.sheet || '', mode: source.mode || '', dataset: source.dataset || ''};
    // Every worksheet-mode chart reaches the briefing through here, and an
    // empty industry is exactly the payload studio_api.json's third negative
    // control says must be refused. The key is written only when it carries
    // a value - the same truthy-only rule viewSourceRecord above follows.
    if (source.industry) record.industry = source.industry;
    return record;
  }

  function chartProvenanceRecord(state, sha, generatedAt) {
    const label = (state || {}).reportName || '';
    return {sha: sha || '', reportName: label, source: label,
      generatedAt: generatedAt || new Date().toISOString()};
  }

  /* --------------------------------------------------------- snapshot registry */

  const BUILD_STAMP_RE = /^[0-9a-f]{64}$/;

  // Reads the snapshot registry envelope (contracts/snapshot_registry.json)
  // or the bare `industries[]` array GET /api/contributions/session folds it
  // into, and throws in plain English on anything that is not a usable
  // entry. Consumed by industry_scope.resolve server-side and by the picker
  // here; both need the same refusal the first time a bad entry is seen,
  // not a silent `undefined` two calls later.
  // The board a saved view may carry (studio_api.json v2): 1, 2 or 4 tiles, each
  // slot holding one sheet or standing empty. Returns the board, or null when the
  // view has none and when the one it has does not describe its own sheets - a
  // dangling index would draw a tile from a sheet that is not there. server.py's
  // save_view applies the same rules, so a board that opens here is a board that
  // saves, and a malformed one is dropped on open rather than failing the open.
  function normalizeBoard(view) {
    const board = view && view.board;
    if (!board || typeof board !== 'object' || Array.isArray(board)) return null;
    const tiles = board.tiles;
    if (typeof tiles !== 'number' || ![1, 2, 4].includes(tiles)) return null;
    const slots = board.slots;
    if (!Array.isArray(slots) || slots.length !== tiles) return null;
    const sheets = (view && view.sheets) || [];
    const placed = new Set();
    for (const slot of slots) {
      if (slot === null) continue;
      if (typeof slot !== 'number' || !Number.isInteger(slot) || slot < 0 || slot >= sheets.length) return null;
      if (placed.has(slot)) return null;
      placed.add(slot);
    }
    return {tiles: tiles, slots: slots.slice()};
  }

  function parseSnapshotRegistry(payload) {
    const list = Array.isArray(payload) ? payload
      : (payload && Array.isArray(payload.snapshots)) ? payload.snapshots
      : null;
    if (!list) throw new Error('The snapshot registry must be a list of entries or an envelope with a snapshots array.');
    for (const entry of list) {
      if (!entry || typeof entry !== 'object') {
        throw new Error('Each snapshot entry must be an object.');
      }
      if (typeof entry.industry !== 'string' || !entry.industry) {
        throw new Error('Each snapshot entry needs a non-empty industry label.');
      }
      if (typeof entry.slug !== 'string') {
        throw new Error(entry.industry + ' is missing its slug.');
      }
      if (typeof entry.available !== 'boolean') {
        throw new Error(entry.industry + ' is missing its available flag.');
      }
      // A workbook the console could not open has no hash and therefore no
      // stamp (contracts/snapshot_registry.json v2, the Telecom example). That
      // is the ONLY entry allowed to omit it: an entry claiming to be available
      // without a stamp is negative_controls[0], and still throws here.
      const unreadable = entry.available === false && entry.build_stamp == null;
      if (!unreadable && (typeof entry.build_stamp !== 'string' || !BUILD_STAMP_RE.test(entry.build_stamp))) {
        throw new Error(entry.industry + ' is missing a valid build_stamp.');
      }
      if (entry.workbook && typeof entry.workbook === 'object' && typeof entry.workbook.name === 'string'
          && /[\\/:]/.test(entry.workbook.name)) {
        throw new Error(entry.industry + '’s workbook name must be relative to the report directory, not a path.');
      }
      // Not in the field list S4_DESIGN.md section 2 gives this function, but
      // required to fail negative_controls[2] in contracts/snapshot_registry.json
      // (an entry whose industry is the collector slug 'credit-unions' while
      // dataset still reads the published label 'Credit Union'): section 1
      // states dataset carries "the same value as industry", so the two
      // disagreeing is exactly the DE-021 shape this contract exists to catch.
      if (typeof entry.dataset === 'string' && entry.dataset && entry.dataset !== entry.industry) {
        throw new Error(entry.industry + ' does not match its dataset (' + entry.dataset + ').');
      }
      // Also not in that field list, but required to fail negative_controls[4]
      // (a companion sent as a bare filename string): section 2 defines
      // companion, when present, as an object carrying its own sha256/bytes/
      // modified_at/lineage_matches, the same DE-021 shape as industry-as-object.
      if (entry.companion != null && typeof entry.companion !== 'object') {
        throw new Error(entry.industry + '’s companion must be an object, not a bare filename.');
      }
    }
    return list;
  }

  /* Lane F2b (boards #50-#52, 2026-09-26): the chart card's title on one line, in the boards' words -
     "Selected amount vs Gift count, by Candidate" (a measure on each axis), "Selected amount by
     Contribution month, by Party" (a dimension on an axis, Colour on a dimension). Field names are kept
     as the reader sees them on the shelves. Each argument is a list of display names; an empty answer
     means the caller keeps the title it built. */
  function conciseTitle(parts) {
    const given = parts || {};
    const list = value => (Array.isArray(value) ? value : []).map(item => String(item == null ? '' : item).trim()).filter(Boolean);
    const unique = values => values.filter((value, i) => values.indexOf(value) === i);
    const join = values => values.length < 3 ? values.join(' and ') : values.slice(0, -1).join(', ') + ' and ' + values[values.length - 1];
    // Lane G1 (boards #50, #52): a measure takes its shorter name where one says the same thing - a sum is
    // its field ("SUM(Analysis Amount)" -> "Analysis Amount"), and "Distinct reported donor identities"
    // drops "Distinct reported" ("Donor identities"; the figures above the chart count "Reported donor
    // identities"). The shelves, the legends and the h2's tooltip keep the full names.
    const shortMeasure = name => {
      const sum = /^SUM\((.+)\)$/i.exec(name);
      const base = sum ? sum[1].trim() : name;
      const rest = /^distinct reported\s+(\S.*)$/i.exec(base);
      return rest ? rest[1].charAt(0).toUpperCase() + rest[1].slice(1) : base;
    };
    const across = unique(list(given.xMeasures).map(shortMeasure)), down = unique(list(given.yMeasures).map(shortMeasure));
    const axes = unique(list(given.axisDims)), detail = unique(list(given.detailDims));
    const colour = String(given.colorDim == null ? '' : given.colorDim).trim();
    const lead = across.length && down.length ? join(down) + ' vs ' + join(across)
      : join(unique([...down, ...across])) || String(given.measureFallback == null ? '' : given.measureFallback).trim();
    if (!lead) return '';
    if (axes.length) return lead + ' by ' + join(axes) + (colour && !axes.includes(colour) ? ', by ' + colour : '');
    const by = detail.length ? detail : colour ? [colour] : [];
    return by.length ? lead + ', by ' + join(by) : lead;
  }

  /* Lanes G1/G1b (board #47, 2026-09-27): the sources a sheet reads, one row each, for the Data pane's
     list. Only a map's layers read sources of their own (lane P), so any other sheet answers none, and
     the list is drawn for two sources or more. A row is the source's key, its swatch - the colour its
     first layer draws in (map-layers.js compileLayer): a circle or shape with no colour measure in its
     slot's one colour, a density in its ramp's purple, any other layer in its ramp's own hue, or a
     darker, then a lighter stop of it where an earlier source already took that colour - its name, its
     kind and its tooltip. `layers` is MentatMapLayers (map-layers.js); `bases` names each basis of an
     industry (MentatStudioContributions.BASIS); `fields` are the sheet's fields (a colour measure is
     read off them). */
  function sheetSources(sheet, layers, bases, fields) {
    if (!layers || typeof layers.sourceKey !== 'function' || !mapLayersOn(sheet)) return [];
    const rows = [], taken = new Set();
    readCards(sheet).forEach((card, slot) => {
      const source = card && card.source;
      const key = layers.sourceKey(source);
      if (!key || rows.some(row => row.key === key)) return;
      const mark = layers.markOf(card);
      const flat = (mark === 'circle' || mark === 'shape') && typeof layers.slotColour === 'function'
        && !(typeof layers.measureField === 'function' && layers.measureField(card, 'color', Array.isArray(fields) ? fields : []));
      let colour = flat ? layers.slotColour(slot % 4) : '';
      if (!flat) {
        const ramp = (card.map && card.map.ramp) || (mark === 'density' ? 'heat' : 'teal');
        const stops = layers.rampStops(ramp) || [];
        const order = ramp === 'heat' ? [1, 2, 4] : [3, 4, 2];
        colour = order.map(at => stops[at]).find(value => value && !taken.has(value)) || stops[order[0]];
      }
      colour = colour || '#a39b92';
      taken.add(colour);
      const industry = source.kind === 'industry';
      const basis = industry ? ((bases && bases[source.basis || 'receipt']) || [])[0] || '' : '';
      rows.push({key, colour, name: industry ? layers.paneLabel(source) : layers.sourceBadge(source),
        kind: industry ? 'published workbook' : layers.sourceLabel(source),
        title: industry ? layers.sourceLabel(source) + ' · published workbook' + (basis ? ' · ' + basis : '')
          : layers.sourceBadge(source) + ' · ' + layers.sourceLabel(source)});
    });
    return rows;
  }

  const api = {
    AGGREGATIONS, AGGREGATION_IDS, MARK_TYPES, MARK_IDS, TRANSFORMS, SHOW_ME, STUDIO_GROUP_LIMIT,
    EXTRA_MARK_TYPES, ALL_MARK_TYPES, ALL_MARK_IDS, markLabel, groupLimitOf, SORTS,
    FILTER_OPS, LIST_OPS, RANGE_OPS, MEASURE_PRESETS, DATE_PARTS, COMPANY_OVERVIEW,
    canTreatAs, isDateLike, filterValues, filterActive, filterBounds, normalizeFilter, filterSummary,
    aggregationsFor, measurePresets, applyMeasurePreset, datePartsFor,
    companyOverviewPresets, companyOverviewMissing, companyOverviewSheet,
    classifyFields, fieldAt, paneBuckets, subjectField, pill, shelf, createSheet, clearSheet, swapShelves, sourceKey,
    carriedFilterValue, graphCycleValue, quickAddZone, migrateSheet,
    WORKSHEET_GROUP_LABELS, NO_DATA_WORKBOOK_HINT, COMMITTEE_ACTIVITY_NOTE,
    defaultGuards, committeeActivitySheet, splitSheetBase, workbookKind, groupWorksheets,
    compileConfig, compileReport, geoKind, placeOf, placeData, mapConfig, mapGroups, NO_PLACE, SEVERAL_PLACES,
    showMeSelection, showMeEligibility, showMeApply, recommendedTile,
    sortResult, sortLocked, markValues, referenceValues, grandTotal, totalNote, rowsToMarksSummary,
    sourceLabelOf, captionSource, captionSourceForKey, viewSourceRecord, chartSourceRecord, chartProvenanceRecord,
    normalizeBoard,
    parseSnapshotRegistry,
    // Marks v3 (boards #50-#52, studio_api.json v3).
    MARK_STRIP, FLOW_REASON, V3_MARK_IDS, ENCODING_IDS, ENCODING_LABELS, GLYPHS, GLYPH_BASES, STYLE_COLOURS,
    CARDS_REFUSAL, DUAL_MARKS, DUAL_REASON, PATH_TYPES, ANGLE_TYPES,
    markRows, markOfEngineType, encodingSupport, roleSupport, styleSupport, shapeEncoding, cardFromMarks, marksFromCard, hasV3Fields,
    serializeCard, validateMarksCards, normalizeMarksCards, readCards, writeCards, adoptMarks, measuresOnRows,
    dualAxisOn, mapLayersOn, setDualAxis, syncCardsToRows, cardEncodingPills, setCardEncoding, setCardType, setCardStyle,
    allCardType, glyphFor, dualCardMark, compileCards,
    // Flow (lane F, board #46, studio_api.json v4).
    FLOW_RIBBONS, FLOW_ORDERS, FLOW_DEFAULTS, FLOW_MIN_STAGES, FLOW_MAX_STAGES, FLOW_MAX_GROUP, flowReason, flowOptions,
    flowFault, flowFit, isFlowSheet, flowSwitch, setFlowOptions, compileFlow,
    // Lane F2b (boards #50-#52): the chart card's one-line title.
    conciseTitle,
    // Lane G1 (board #47): the sources a sheet reads, for the Data pane's list.
    sheetSources
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MentatStudioCore = api;
})(globalThis);
