/* Original Chart Studio workbench: verified contribution aggregates come from
   the local server; worksheet views retain their local chart-math path.
   Both sources share the original shelves, Marks, Show Me and SVG canvas. */
(() => {
  'use strict';
  const core = globalThis.MentatStudioCore;
  const math = globalThis.MentatCharts;
  const paint = globalThis.MentatChartRender;
  const contributions = globalThis.MentatStudioContributions;
  const contributionPlot = globalThis.MentatContributionPlot;
  const NS = 'http://www.w3.org/2000/svg';
  const el = id => document.getElementById('studio-' + id);
  const fmt = n => Number(n).toLocaleString('en-US', {maximumFractionDigits: 3});
  const compact = n => Math.abs(n) >= 1e5
    ? Number(n).toLocaleString('en-US', {notation: 'compact', maximumFractionDigits: 1})
    : Number(n).toLocaleString('en-US', {maximumFractionDigits: 2});
  const STORE = 'mentat-chart-studio-v1';
  const ZONES = [['columns', 'Columns'], ['rows', 'Rows'], ['color', 'Colour'], ['size', 'Size'],
    ['label', 'Label'], ['detail', 'Detail'], ['tooltip', 'Tooltip'], ['shape', 'Shape'], ['path', 'Path'], ['angle', 'Angle'],
    ['filters', 'Filters'], ['pages', 'Pages']];
  const AGG_LABEL = {sum: 'SUM', mean: 'AVG', count: 'COUNT', median: 'MEDIAN', min: 'MIN', max: 'MAX', countd: 'COUNTD'};
  // Every gesture a Data-pane field answers to, in the order a reader meets
  // them. The pill says this before the click, so nobody has to discover it.
  const CLICK_HINT = 'Click to add \u00b7 drag to place \u00b7 Enter for actions';
  const ANALYTICS = [
    {id: 'annual', group: 'transform', label: 'Period totals', hint: 'Group dated rows into calendar years or election cycles'},
    {id: 'yoy', group: 'transform', label: 'Year-over-year %', hint: 'Percentage change from the previous calendar year'},
    {id: 'change', group: 'transform', label: 'Period change', hint: 'Difference from the preceding period'},
    {id: 'forecast', group: 'transform', label: 'Same-phase baseline', hint: 'Repeat the last completed value from the matching election phase'},
    {id: 'totals', group: 'flag', label: 'Totals', hint: 'Add a grand total of the plotted marks'},
    {id: 'average', group: 'flag', label: 'Average line', hint: 'Dashed reference line at the mean of the marks'},
    {id: 'median', group: 'flag', label: 'Median line', hint: 'Dashed reference line at the median of the marks'}
  ];

  let sheets = [core.createSheet('Sheet 1', 's1')];
  let active = 0;
  let data = null;
  let sourceKey = '';
  let history = [], future = [];
  let dragging = null, dropped = false;
  let selection = [];
  let dataSourceTab = false;
  let loadedSources = false, revision = 0;
  let pageTimer = 0;
  let hoverTile = '';
  let lastResult = null, lastConfig = null, lastTitle = '', lastScope = '', lastCoverage = null;
  let contributionCatalog = null, contributionToken = '', contributionResult = null, contributionRequest = null;
  // The snapshot registry folded into GET /api/contributions/session as
  // industries[] (S4_DESIGN.md section 2). Left null on an older server that
  // does not send the field yet; nothing here has a reader for it beyond
  // parseSnapshotRegistry's own validation.
  let industries = null;
  let contributionOffset = 0, contributionRevision = 0;
  // One entry per question in flight: {key, promise, controller, waiters}. The
  // controller belongs to the request, not to the surface that started it, and
  // the request is only cancelled once the last surface waiting on it has moved
  // on - a tile that changes its question must not cancel the answer the tile
  // beside it is still waiting for.
  const contributionPending = new Map();
  // The single canvas needs a claim of its own, the way each tile has one.
  const mainFlight = {waiting: null};
  let contributionGeometry = null, geometryKey = '';
  // Census B-S29 (Paul, 2026-09-23: "nothing is logically constrained"): every
  // surface - the canvas and each board tile - holds its own map, keyed by the
  // canvas it is drawn in; the Census geometry cache and its in-flight requests
  // stay shared by key. A surface tears down only the map it holds, so a chart
  // tile never destroys the map beside it.
  const mapHosts = new Map();
  const mapSlot = host => { if (!mapHosts.has(host)) mapHosts.set(host, {map: null, revision: 0, timer: 0}); return mapHosts.get(host); };
  function dropMap(host) {
    const slot = mapHosts.get(host);
    if (!slot) return;
    clearTimeout(slot.timer);
    slot.map?.destroy?.();
    mapHosts.delete(host);
  }
  // Every map drawn in a canvas or inside it (compared scopes draw one map
  // each), torn down before the canvas draws something else.
  function dropMapsIn(canvas, keep) {
    for (const held of [...mapHosts.keys()]) if (held !== keep && (held === canvas || canvas.contains(held))) dropMap(held);
  }
  let recordOffset = 0, recordSelection = null, recordRevision = 0, exporting = false;
  let pageMembersKey = '';
  let pendingScope = null, appliedScope = null;
  // The saved-view id currently in the URL, and the flag that lets an open
  // install its own sheets without the edit-detector treating them as an edit.
  let openedSaved = '', holdingSavedId = false;
  let inspectorTab = 'marks', overviewMode = false, overviewRevision = 0, overviewCompany = '';
  let queryRequests = 0;
  // "+ Compare with..." (build 03): the scopes each sheet is split into, kept in
  // this tab by sheet id. None of it reaches the URL: the query string is shared
  // with Graph, and a comparison written there would open Graph in it too.
  const compareBySheet = new Map();
  let comparePills = null, compareMenu = null, compareDrawn = null;
  const compareFlights = [{waiting: null}, {waiting: null}, {waiting: null}], menuFlight = {waiting: null};
  // What "Match Graph scope" applies, worked out when the note line is drawn.
  let graphMatchTarget = null;
  // The Data source panel opens on arrival and folds itself away once, after
  // the first load that actually produced fields. sourceReady is what the
  // folded summary reports; it is cleared whenever a load starts again.
  let sourceReady = false;
  let progressStart = 0, progressTimer = 0, progressDepth = 0, progressOpened = false;
  // Canvas mode as a board of 1, 2 or 4 tiles (studio_api.json v2). null is one
  // tile, which is today's single canvas. What a sheet last drew is kept by its
  // own id, so a tile is never redrawn from another tile's answer.
  let board = null, boardSurfaces = [], boardNodes = [];
  const tileMemory = new Map();
  const contributionCache = new Map(), memberCache = new Map(), worksheetMembers = new WeakMap(), geometryCache = new Map(), geometryPending = new Map();
  let contributionCacheEpoch=0;
  const verified = () => el('source-mode').value === 'contributions';
  const basis = () => sheet().source.basis || 'receipt';
  // charts.js prints this as the canvas subtitle. A source object without it
  // puts the literal word "undefined" on the chart; 53efc2c4a named it on the
  // Company overview cards and this is the same name for the main canvas.
  const sheetName = () => (dataset() || {}).label || (dataset() || {}).id || '';
  const dataset = () => contributionCatalog?.datasets.find(d => d.id === el('dataset').value);

  const sheet = () => sheets[active];
  const columns = () => (data && data.columns) || [];

  /* ------------------------------------------------------------ storage */

  function readStore() {
    try { return JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch { return {}; }
  }
  function persist() {
    if (!sourceKey) return;
    try {
      const store = readStore();
      // Entries written before the key named the worksheet are keyed by a bare
      // workbook digest and can never be matched again, so drop them rather
      // than let them use up the twelve-worksheet cap.
      for (const key of Object.keys(store)) if (!key.includes('|')) delete store[key];
      store[sourceKey] = {sheets, active, board};
      const keys = Object.keys(store);
      // Keep the file small: only the twelve most recent worksheets are kept.
      if (keys.length > 12) delete store[keys[0]];
      localStorage.setItem(STORE, JSON.stringify(store));
    } catch { /* private browsing or a full quota simply means no persistence */ }
  }
  function restore(key) {
    const saved = readStore()[key];
    if (saved && Array.isArray(saved.sheets) && saved.sheets.length) {
      sheets = saved.sheets.map(s => Object.assign(core.createSheet(s.name, s.id), s));
      active = Math.min(Math.max(0, saved.active | 0), sheets.length - 1);
      board = core.normalizeBoard({sheets: sheets, board: saved.board});
      settleBoard();
      return true;
    }
    sheets = [core.createSheet('Sheet 1', 's1')];
    active = 0;
    board = null;
    return false;
  }

  /* ---------------------------------------------------------------- focus */

  function focusMemory() {
    const node = document.activeElement;
    if (!node || node === document.body) return null;
    if (node.classList && node.classList.contains('studio-pill')) {
      return {zone: node.dataset.zone || '', column: node.dataset.column};
    }
    return node.id ? {id: node.id} : null;
  }
  function focusRestore(memory) {
    if (!memory) return;
    if (memory.id) {
      const node = document.getElementById(memory.id);
      if (node) node.focus();
      return;
    }
    const selector = '.studio-pill[data-column="' + memory.column + '"]';
    const scope = memory.zone ? el(memory.zone) : null;
    const node = (scope && scope.querySelector(selector)) ||
      document.getElementById('studio-pane-data').querySelector(selector) ||
      document.getElementById('studio-grid').querySelector(selector);
    if (node) node.focus();
  }

  /* -------------------------------------------------------------- history */

  function commit(change) {
    history.push(JSON.stringify({sheets, active, board}));
    if (history.length > 50) history.shift();
    future = [];
    const memory = focusMemory();
    change();
    syncMarks();
    viewEdited();
    persist();
    renderAll();
    focusRestore(memory);
  }
  function step(from, to) {
    if (!from.length) return;
    to.push(JSON.stringify({sheets, active, board}));
    const snapshot = JSON.parse(from.pop());
    const memory = focusMemory();
    sheets = snapshot.sheets;
    active = Math.min(snapshot.active, sheets.length - 1);
    board = snapshot.board || null;
    settleBoard();
    viewEdited();
    persist();
    renderAll();
    focusRestore(memory);
  }

  /* --------------------------------------------------------------- source */

  const fill = (node, items) => node.replaceChildren(...items.map(([value, label]) => new Option(label, value)));
  function sourceFields() {
    return {run: el('run').value, report: el('report').value, sheet: el('sheet').value, header: Number(el('header').value) || 0};
  }
  function status(message) { el('loading').textContent = message; renderSourceSummary(); }

  // The summary is the only part of the panel that survives being folded
  // away, so the state the panel would have shown is written there rather
  // than disappearing with it.
  function renderSourceSummary() {
    const details = el('source-details'), summary = el('source-summary');
    if (!details || !summary) return;
    // A chip line, open or folded: what this panel is pointed at, then whether
    // it is ready (UI clean-up item 16). It used to read "Data source" while
    // open and a sentence while folded, so the state disappeared with the fold.
    const chips = [];
    if (verified()) {
      const chosen = dataset();
      chips.push(chosen ? (chosen.label || chosen.id) : '', (contributions.BASIS[basis()] || [])[0] || '');
    } else if (data) {
      chips.push(sheet().source.reportName || '', data.sheet || '');
    }
    summary.replaceChildren();
    for (const text of chips.filter(Boolean)) {
      const chip = document.createElement('span');
      chip.textContent = text;
      summary.append(chip);
    }
    const mark = document.createElement('span');
    mark.className = sourceReady ? 'is-ready' : 'is-waiting';
    mark.textContent = sourceReady ? '✓ Ready' : (el('loading').textContent || 'Data source');
    summary.append(mark);
    // The header row carries the same state (S1 "Ready"), so it is readable
    // while the source popover is closed.
    const ready = el('ready');
    if (ready) {
      ready.textContent = sourceReady ? 'Ready' : (el('loading').textContent || '');
      ready.classList.toggle('is-ready', sourceReady);
    }
  }

  // The panel stays open (item 16). Its selects are the way into every report,
  // and folding them away after the first load was the console's doing, not the
  // reader's; the ready mark on the summary carries what the fold used to say.
  function settleSource() {
    sourceReady = true;
    renderSourceSummary();
  }

  // A report that cannot be read says why under the select, in the server's
  // own words, instead of only inside a thrown error nobody kept.
  function renderDatasetMessage() {
    const node = el('dataset-message');
    if (!node) return;
    const datasets = (contributionCatalog && contributionCatalog.datasets) || [];
    const chosen = datasets.find(d => d.id === el('dataset').value);
    const blocked = datasets.filter(d => !d.available && d.message);
    const text = chosen && chosen.message ? chosen.message
      : blocked.map(d => (d.label || d.id) + ': ' + d.message).join(' · ');
    node.textContent = text;
    node.hidden = !text;
    node.classList.toggle('is-warning', !!text);
  }

  /* Preparing the verified index can take minutes. The reader gets the
     server's own message, the time it has taken so far, and a way out that
     keeps the view they already had instead of an empty canvas. */
  function showProgress(message) {
    // A prepare nobody can see is a console that looks frozen, and the way
    // out of it is the Cancel button in this panel, so the panel opens for
    // the duration and returns to where it was afterwards.
    const details = el('source-details');
    if (details && !details.open) { progressOpened = true; details.open = true; renderSourceSummary(); }
    if (!progressStart) { progressStart = Date.now(); progressTimer = setInterval(tickProgress, 1000); }
    el('progress-message').textContent = message;
    el('progress').hidden = false;
    tickProgress();
  }
  function tickProgress() {
    if (!progressStart) return;
    const seconds = Math.max(0, Math.floor((Date.now() - progressStart) / 1000));
    el('progress-elapsed').textContent =
      Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0') + ' elapsed';
  }
  function clearProgress() {
    clearInterval(progressTimer);
    progressTimer = 0; progressStart = 0;
    el('progress').hidden = true;
    const details = el('source-details');
    if (progressOpened && details) { details.open = false; renderSourceSummary(); }
    progressOpened = false;
  }
  // Several verified calls can be waiting at once (a view, its member lists,
  // its records), so the row stays up until the last of them is done.
  function releaseProgress() {
    progressDepth = Math.max(0, progressDepth - 1);
    if (!progressDepth) clearProgress();
  }
  function failure(message) {
    el('error').textContent = message || '';
    el('error').hidden = !message;
  }
  /* The pending panel (r7). While the server builds the view a surface asked
     for, that surface's canvas says so: the server's own sentence, a bar (a
     share when the server knows its total, a sweep when it does not) and the
     records and time so far. It sits first in the canvas, over the dimmed last
     good view, so Cancel still leaves that view exactly where it was. A wait
     that stopped (a failed request, the 15-minute ceiling) leaves the same
     panel with a Retry instead. */
  function pendingPanel(host, state) {
    let panel = host.querySelector(':scope > .studio-pending');
    if (!panel) { panel = document.createElement('div'); panel.className = 'studio-pending'; host.prepend(panel); }
    panel.dataset.state = state;
    // A worksheet table leaves the canvas hidden; the panel is never drawn where nobody can see it.
    host.hidden = false;
    return panel;
  }
  function drawPending(host, progress) {
    const panel = pendingPanel(host, 'pending');
    panel.setAttribute('role', 'status');
    panel.dataset.phase = progress.phase || '';
    let message = panel.querySelector('.studio-pending-message'), bar = panel.querySelector('.studio-pending-bar'),
      detail = panel.querySelector('.studio-pending-detail');
    if (!message || !bar || !detail) {
      message = document.createElement('p'); message.className = 'studio-pending-message';
      bar = document.createElement('div'); bar.className = 'studio-pending-bar'; bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100'); bar.append(document.createElement('span'));
      detail = document.createElement('p'); detail.className = 'studio-pending-detail';
      panel.replaceChildren(message, bar, detail);
    }
    message.textContent = progress.message;
    const known = progress.percent !== null;
    bar.classList.toggle('is-indeterminate', !known);
    bar.setAttribute('aria-label', progress.message);
    if (known) bar.setAttribute('aria-valuenow', String(progress.percent)); else bar.removeAttribute('aria-valuenow');
    bar.firstChild.style.width = known ? progress.percent + '%' : '';
    detail.textContent = contributions.pendingDetail(progress);
  }
  function drawStopped(host, message, retry) {
    const panel = pendingPanel(host, 'stopped');
    panel.setAttribute('role', 'alert');
    delete panel.dataset.phase;
    const parts = [];
    if (message) { const text = document.createElement('p'); text.className = 'studio-pending-message'; text.textContent = message; parts.push(text); }
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'studio-pending-retry'; button.textContent = 'Retry';
    button.onclick = retry;
    panel.replaceChildren(...parts, button);
  }
  function clearPending(host, state) {
    const panel = host && host.querySelector(':scope > .studio-pending');
    if (panel && (!state || panel.dataset.state === state)) panel.remove();
  }

  async function loadRuns() {
    const ticket = ++revision;
    sourceReady = false;
    status('Choosing a report folder…');
    try {
      const body = await api('/api/report-sources');
      if (ticket !== revision) return;
      fill(el('run'), body.runs.map(r => [r.id, r.name]));
      await loadReports();
    } catch (e) { if (ticket === revision) { status('Report folders unavailable.'); failure(e.message); } }
  }
  async function loadReports() {
    const ticket = ++revision;
    status('Reading reports…');
    try {
      if (!el('run').value) { status('No report folder is available.'); return; }
      const body = await api('/api/reports?id=' + encodeURIComponent(el('run').value));
      if (ticket !== revision) return;
      fillReports(body.reports || []);
      if (!body.reports.length) { status('This folder has no completed reports.'); return; }
      await loadSheets();
    } catch (e) { if (ticket === revision) { status('Reports unavailable.'); failure(e.message); } }
  }
  // Both books of an industry arrive in one catalog. The tall companion is the
  // one a chart should be built from, so it is listed first and picked first;
  // the Overview keeps its own group because its sheets are a report layout,
  // not a table. `fill` cannot hold groups, so these options are built here.
  function worksheetHint() {
    let node = document.getElementById('studio-worksheet-hint');
    if (!node) {
      node = document.createElement('p');
      node.id = 'studio-worksheet-hint';
      node.className = 'studio-note';
      el('worksheet-source').append(node);
    }
    return node;
  }
  function fillReports(reports) {
    const groups = core.groupWorksheets(reports);
    el('report').replaceChildren(...['data', 'overview']
      .filter(kind => groups[kind].length)
      .map(kind => {
        const group = document.createElement('optgroup');
        group.label = core.WORKSHEET_GROUP_LABELS[kind];
        group.append(...groups[kind].map(r => new Option(r.name, r.index)));
        return group;
      }));
    if (groups.data.length) el('report').value = String(groups.data[0].index);
    const hint = worksheetHint();
    hint.textContent = groups.data.length ? '' : core.NO_DATA_WORKBOOK_HINT;
    hint.hidden = groups.data.length > 0;
  }
  async function loadSheets() {
    const ticket = ++revision;
    status('Reading worksheets…');
    try {
      const body = await api(requestPath());
      if (ticket !== revision) return;
      fill(el('sheet'), (body.sheets || []).map(s => [s, s]));
      const preferred = ['Candidate Funding', 'Funding Cohorts', 'Allocation Estimates', 'Beneficiary Summary']
        .find(s => (body.sheets || []).includes(s));
      if (preferred) el('sheet').value = preferred;
      await scan(false);
    } catch (e) { if (ticket === revision) { status('Worksheets unavailable.'); failure(e.message); } }
  }
  function requestPath(withSheet, header) {
    const params = new URLSearchParams({id: el('run').value, index: el('report').value});
    if (withSheet) params.set('sheet', el('sheet').value);
    if (header) params.set('header', header);
    return '/api/report-data?' + params;
  }
  async function scan(manual) {
    const ticket = ++revision;
    sourceReady = false;
    status('Scanning columns…');
    failure('');
    try {
      if (manual && !el('header').reportValidity()) throw new Error('Enter a header row from 1 to 100.');
      const body = await api(requestPath(true, manual ? Number(el('header').value) : 0));
      if (ticket !== revision) return;
      data = body;
      el('header').value = data.header_row;
      const fields = sourceFields();
      const key = core.sourceKey(fields, data);
      if (key !== sourceKey) {
        sourceKey = key;
        history = []; future = [];
        // A field picked in the previous worksheet still drives Show Me, so the
        // selection belongs to the source it was made in.
        selection = [];
        restore(key);
        applyDefaultGuards();
      }
      const record = {run: fields.run, runName: text(el('run')), report: fields.report, reportName: text(el('report')),
        sheet: data.sheet, header: data.header_row, sha256: data.sha256};
      for (const s of sheets) s.source = Object.assign({}, record);
      const subject = core.subjectField(core.classifyFields(data.columns).fields);
      if (subject) for (const s of sheets) defaultSubject(s, subject);
      status(fmt(data.row_count) + ' rows · ' + data.columns.length + ' columns scanned');
      persist();
      renderAll();
      settleSource();
    } catch (e) { if (ticket === revision) { status('Column scan unavailable.'); failure(e.message); } }
  }
  // A polymorphic Overview sheet opens with the partition the report itself
  // totals already selected, on every sheet of this workbook's state, so
  // nothing sums three stacked partitions before the reader has looked. The
  // guard is an ordinary filter pill, so removing it stands for as long as this
  // worksheet stays loaded and comes back the next time it is loaded.
  let guardsKey = '', guardsList = [];
  function worksheetGuards() {
    if (!data || verified()) return [];
    if (guardsKey !== sourceKey) { guardsKey = sourceKey; guardsList = core.defaultGuards(data.columns, members); }
    return guardsList;
  }
  function applyDefaultGuards() {
    const guards = worksheetGuards();
    if (!guards.length) return;
    for (const state of sheets) {
      const filters = Array.isArray(state.filters) ? state.filters : [];
      const added = guards.filter(guard => !filters.some(f => Number(f.column) === guard.column))
        .map(guard => ({column: guard.column, op: 'equals', value: guard.value, stage: 'row'}));
      if (added.length) state.filters = [...filters, ...added];
    }
  }
  // What a report worksheet says on its own account: the guard currently
  // holding one partition, and the sheets whose money is a recipient
  // committee's rather than the industry's.
  function worksheetNotes(state) {
    if (!data || verified()) return [];
    const committee = core.committeeActivitySheet(data.sheet);
    const filters = (state || sheet()).filters || [];
    const active = worksheetGuards()
      .filter(guard => filters.some(f => Number(f.column) === guard.column &&
        f.op === 'equals' && String(f.value) === guard.value))
      .map(guard => guard.note);
    return committee ? [committee, ...active] : active;
  }
  const text = select => select.selectedOptions[0] ? select.selectedOptions[0].textContent : '';

  /* Verified contributions use the original shelves and canvas. Query results
     are reused across display edits; underlying rows never enter browser sums. */
  async function contributionAPI(path, body, signal) {
    if(path==='query'&&body)queryRequests++;
    const response=await fetch('/api/contributions/'+path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Mentat-Token':contributionToken},body:JSON.stringify(body),signal}:{signal});
    const result=await response.json();if(!response.ok||result.error)throw new Error(result.error||'Contribution data unavailable.');return result;
  }
  // Every Chart Studio call to /api/contributions/query and /records comes
  // through here, and so through the one pending handler (r7,
  // contributions.pollPending): a 'building' or 'preparing' answer is asked
  // again at the server's pace; its progress goes to the status line, the
  // progress row and the caller's onProgress (the canvas, the records panel);
  // a newer request (`signal`, `current`) or Cancel stops it; a hidden Chart
  // Studio pauses it; and after about 15 minutes it ends in an error the
  // caller offers a Retry for. An answer the server held and then returned is
  // returned exactly as before.
  const preparing = new Set();
  // The status line while answers are pending: what it said before the first
  // wait began, the waits writing it now, and the last text one of them wrote.
  let waitLine = null;
  async function contributionReady(path, query, signal, options = {}) {
    const own = new AbortController(), follow = () => own.abort();
    if (signal) { if (signal.aborted) own.abort(); else signal.addEventListener('abort', follow, {once: true}); }
    let failed = null;
    progressDepth++;
    try {
      return await contributions.pollPending(body => contributionAPI(path, body, own.signal), {
        body: query, path, signal: own.signal, current: options.current || null, retry: options.retry === true, gate: whileShown,
        onProgress: progress => {
          preparing.add(own);
          if (!waitLine) waitLine = {before: el('loading').textContent, writers: new Set(), wrote: ''};
          waitLine.writers.add(own);
          waitLine.wrote = contributions.pendingText(progress);
          status(waitLine.wrote);
          showProgress('Preparing verified data · ' + contributions.pendingText(progress, {elapsed: false}));
          if (options.onProgress) options.onProgress(progress);
        }});
    } catch (error) {
      failed = error;
      throw error;
    } finally {
      preparing.delete(own); if (signal) signal.removeEventListener('abort', follow); releaseProgress();
      // The last wait to end gives the status line back, or says why it
      // stopped, unless something else (Cancel, a new view) has written it.
      if (waitLine && waitLine.writers.delete(own) && !waitLine.writers.size) {
        if (el('loading').textContent === waitLine.wrote)
          status(failed && failed.name !== 'AbortError' ? failed.message : waitLine.before);
        waitLine = null;
      }
    }
  }
  // Leaving Chart Studio stops the polling: a pending answer waits here, asking
  // nothing, until the view is on screen again (activate() lets it go).
  const shownWaiters = new Set();
  function whileShown(signal) {
    const panel = document.getElementById('view-chart-studio');
    if (!panel || !panel.hidden) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const entry = {
        go: () => { shownWaiters.delete(entry); if (signal) signal.removeEventListener('abort', entry.stop); resolve(); },
        stop: () => { shownWaiters.delete(entry); reject(new DOMException('View changed', 'AbortError')); }};
      if (signal) { if (signal.aborted) { entry.stop(); return; } signal.addEventListener('abort', entry.stop, {once: true}); }
      shownWaiters.add(entry);
    });
  }
  async function loadContributions(refresh=false) {
    const ticket=++revision;failure('');sourceReady=false;status('Reading verified contribution fields…');
    try {
      if(!contributionCatalog||refresh){const [session,catalog]=await Promise.all([contributionAPI('session'),contributionAPI('catalog')]);if(ticket!==revision||!verified())return;contributionToken=session.token;contributionCatalog=catalog;try{if(Array.isArray(session.industries))industries=core.parseSnapshotRegistry(session.industries);}catch(error){industries=null;}}
      // The permalink's industry, read through the one module that owns the URL
      // vocabulary (contracts/url_scope.json) instead of a second raw reader.
      const previous=el('dataset').value||globalThis.MentatScope?.read().industry||document.getElementById('graph-industry')?.value;
      fill(el('dataset'),contributionCatalog.datasets.map(d=>[d.id,d.label||d.id]));
      // An unavailable report carries the reason on its own option, so it is
      // readable before it is chosen and not only after a failure.
      for(const option of el('dataset').options){const d=contributionCatalog.datasets.find(d=>d.id===option.value);option.disabled=!d.available;option.title=d.message||'';if(!d.available)option.textContent+=' — unavailable';}
      el('dataset').value=contributionCatalog.datasets.some(d=>d.id===previous&&d.available)?previous:contributionCatalog.datasets.find(d=>d.available)?.id||'';
      renderDatasetMessage();
      if(!dataset())throw new Error(['No verified sources are available in the configured report folder.',
        ...contributionCatalog.datasets.filter(d=>d.message).map(d=>(d.label||d.id)+': '+d.message)].join(' '));
      selectContributionDataset();
      // A permalink arrives with no pendingScope, so the URL supplies one; a
      // click through from Graph already set it and is left alone.
      if(!pendingScope)pendingScope=urlGraphScope();
      applyGraphScope();
      settleSource();
      await openSavedFromUrl();
    }catch(error){if(ticket===revision){status('Verified data unavailable.');failure(error.message);renderDatasetMessage();}}
  }
  function selectContributionDataset() {
    ++revision;abortContributionRequests();tileMemory.clear();contributionOffset=0;contributionResult=null;contributionRequest=null;selection=[];history=[];future=[];
    const d=dataset(),key='contributions|'+d.id+'|'+d.provenance.workbook_sha256;sourceKey=key;const restored=restore(key);
    for(const state of sheets)state.source={...state.source,mode:'contributions',dataset:d.id,industry:d.industry,basis:state.source.basis||'receipt',reportName:d.provenance.workbook,sheet:'Verified source observations',runName:'Configured completed-report folder',sha256:d.provenance.workbook_sha256};
    data={columns:contributions.fields(d.fields||contributionCatalog.fields,basis()),rows:[],row_count:0,header_row:0,sha256:d.provenance.workbook_sha256,sheet:'Verified source observations',note:d.provenance.scope};
    if(!restored){sheet().columns=[{column:data.columns.findIndex(f=>f.id==='company'),aggregation:'sum'}];sheet().rows=[{column:data.columns.findIndex(f=>f.id==='amount'),aggregation:'sum'}];sheet().marks.type='bar';}
    for(const [id,entry] of compareBySheet)if(entry.kind==='industry')compareBySheet.delete(id);
    persist();renderAll();
    // Four call sites reach this function - the Data panel picker, the shelf
    // source buttons, openView() and the permalink path - and every one of them
    // is a producer of the URL's `industry`, so the write lives here rather than
    // at four places that could drift apart (DE-021).
    globalThis.MentatScope?.write({industry:d.industry||d.id||''});
  }
  // The bin width's label names its unit: dollars on verified data, the
  // measure's own unit on a worksheet.
  function binUnit(suffix) {
    const label = el('bin-width-label');
    const text = label && [...label.childNodes].find(node => node.nodeType === 3);
    if (text) text.textContent = 'Histogram bin width' + suffix;
  }
  function syncContributionControls() {
    const on=verified();el('contribution-source').hidden=!on;el('worksheet-source').hidden=on;el('verified-summary').hidden=!on;el('result-controls').hidden=!on;
    el('marks-note').textContent=on?'Colour separates series. Detail, Label and Tooltip dimensions define each mark’s grouping. Select a mark to inspect its source records.':'Colour separates series or shades by a measure; Size, Label, Detail and Tooltip take any field.';
    for(const id of ['param-start','param-end','param-limit','param-horizon','param-split'])el(id).closest('label').hidden=on&&id!=='param-horizon';
    // A worksheet histogram takes a bin width too (contract W10), in the
    // measure's own unit; blank keeps the engine's own rule.
    if(!on){el('map-settings').hidden=true;const binWasHidden=el('bin-width-label').hidden;el('bin-width-label').hidden=sheet().marks.type!=='histogram';el('bin-width').value=sheet().params.binWidth||'';el('bin-width').placeholder='Automatic';binUnit('');if(binWasHidden&&!el('bin-width-label').hidden&&el('params-details'))el('params-details').open=true;return;}
    fill(el('mark'),[...core.MARK_TYPES,['map','Map'],...contributions.EXTRA_MARKS]);el('mark').value=sheet().marks.type;
    const d=dataset();if(!d)return;
    fill(el('basis'),d.bases.map(b=>[b.id,contributions.BASIS[b.id]?.[0]||b.label]));el('basis').value=basis();el('basis-note').textContent=contributions.BASIS[basis()]?.[1]||'';
    if(data)data.columns=contributions.fields(d.fields||contributionCatalog.fields,basis());
    if(el('destination'))el('destination').value=core.shelf(sheet().columns).map(p=>columns()[p.column]?.id).find(id=>['recipient_state','represented_state','state'].includes(id))||'';
    el('param-groups').title='Groups shown per page. Totals and CSV exports include every group.';
    const mapWasHidden=el('map-settings').hidden;
    el('map-settings').hidden=!['map','filled-map'].includes(sheet().marks.type);
    if(mapWasHidden&&!el('map-settings').hidden)el('map-settings').open=isFoldOpen('map-settings')||!foldStored('map-settings');
    const binWasHidden=el('bin-width-label').hidden;
    el('bin-width-label').hidden=sheet().marks.type!=='histogram';el('bin-width').value=sheet().params.binWidth||100;binUnit(' (USD)');
    if(binWasHidden&&!el('bin-width-label').hidden&&el('params-details'))el('params-details').open=true;
    const map=sheet().map||{layers:['states','districts'],congress:'119'};
    el('congress').value=map.congress;for(const [id,layer]of [['states','states'],['districts','districts'],['zips','zip_areas']])el('layer-'+id).checked=map.layers.includes(layer);
  }
  // A permalink's company and cycle become the same carried pills the Graph
  // handoff produces, so applyGraphScope() below stays the only consumer and
  // one code path renders both. Graph spells a cycle by its end year and the
  // verified index by the pair it covers, so the same translation the handoff
  // uses is applied here (chart-studio-core.js carriedFilterValue).
  function urlGraphScope() {
    const url=globalThis.MentatScope?.read();
    if(!url||(!url.company&&!url.cycle))return null;
    const filters=[];
    if(url.company)filters.push({field:'company',value:url.company});
    if(url.cycle)filters.push({field:'cycle',value:core.carriedFilterValue('cycle',url.cycle)});
    return {industry:url.industry||'',filters,notCarried:[]};
  }
  // The saved-view id in the URL. A view that lives only in this tab
  // (viewsMemory, id "memory-...") is not addressable, so it is never written:
  // writing it would silently remove a real id that was already there.
  function rememberSavedView(id) {
    const value=String(id||'');
    if(!/^[a-f0-9]{32}$/.test(value))return;
    openedSaved=value;
    globalThis.MentatScope?.write({saved:value});
  }
  // The first change to the working sheets after an open or a save. From here
  // the id in the URL no longer describes what is on screen, and leaving it
  // there would let the next reload re-open the saved view over the edit.
  function viewEdited() {
    if (holdingSavedId || !openedSaved) return;
    openedSaved = '';
    globalThis.MentatScope?.write({saved: ''});
  }
  // A saved-view permalink. The list is the cheapest way to get the view's
  // sourceKey, which openability() needs before openView() will touch it;
  // openView() fetches the sheets itself. A link to a view this console does
  // not have says so rather than opening the wrong thing.
  async function openSavedFromUrl() {
    const id=globalThis.MentatScope?.read().saved;
    if(!id||id===openedSaved)return;
    openedSaved=id;
    let views=[];
    try { views=(await getJSON('/api/studio/views')).views||[]; }
    catch (error) { failure(viewMessage(error)+' The saved view in this link could not be listed.'); return; }
    const view=views.find(item=>item.id===id);
    if(!view){failure('This console has no saved view '+id+'.');return;}
    await openView(view);
  }
  // Carried Graph scope becomes ordinary filter pills, so the reader can see
  // and remove every one of them. A field this dataset does not have is
  // reported, never silently dropped.
  function applyGraphScope() {
    const scope = pendingScope;
    if (!scope) return;
    // The carried scope is only consumed once it can actually be applied.
    // "Open in Chart Studio" calls this eagerly, and loadContributions()
    // calls it again when the verified data lands; on a page where Chart
    // Studio has not loaded yet the eager call arrives first, and clearing
    // pendingScope there threw the reader's company and cycle away before
    // the second call could use them.
    if (verified() && !data) return;
    pendingScope = null;
    if (!verified()) return;
    const missing = [];
    const pills = [];
    for (const {field, value} of scope.filters) {
      const column = columns().find(f => f.id === field);
      if (!column) { missing.push(field); continue; }
      pills.push({column: column.index, op: 'equals', value: String(value), stage: 'row'});
    }
    appliedScope = {carried: true, industry: scope.industry,
      company: (scope.filters.find(f => f.field === 'company') || {}).value || '',
      cycle: (scope.filters.find(f => f.field === 'cycle') || {}).value || '',
      notCarried: [...(scope.notCarried || []),
        ...missing.map(field => field + ': not carried (not in this data)')]};
    commit(() => {
      // An empty Graph control means "all", so it must remove a previously
      // carried filter too. Keep only the sheet's unrelated analytical filters.
      const controlled = new Set(['company', 'cycle', 'funding_source']);
      const kept = (sheet().filters || []).filter(f => !controlled.has((columns()[Number(f.column)] || {}).id));
      sheet().filters = [...kept, ...pills];
    });
    failure(missing.length ? 'This source has no ' + missing.join(' or ') + ' field, so that part of the Graph scope was not carried.' : '');
  }

  // A surface lets go of the answer it was waiting for. The request itself is
  // only cancelled when nobody is left waiting on it.
  function releaseFlight(claim) {
    const flight = claim && claim.waiting;
    if (!flight) return;
    claim.waiting = null;
    flight.waiters -= 1;
    if (flight.waiters > 0) return;
    if (contributionPending.get(flight.key) === flight) contributionPending.delete(flight.key);
    flight.controller.abort();
  }
  const everyClaim = () => [mainFlight, ...tileMemory.values()];
  // Cancel, Refresh and a change of source stop every surface, not just the one
  // the module happened to remember.
  function abortContributionRequests() {
    for (const claim of everyClaim()) releaseFlight(claim);
    for (const flight of [...contributionPending.values()]) flight.controller.abort();
    contributionPending.clear();
  }
  function queryCached(query,view,options={}) {
    const key=JSON.stringify(query),cached=contributionCache.get(key),epoch=contributionCacheEpoch;
    const claim=(view&&view.flight)||mainFlight;
    if(contributionResult&&contributionRequest&&JSON.stringify(contributionRequest)===key)return Promise.resolve(contributionResult);
    // A board tile's own last answer to this very request is as current as the
    // single canvas's is (the line above). Selecting a tile again redraws it at
    // once: a refetch once the 30 s cache had lapsed left the group label and the
    // pager describing the previously selected tile until the answer came back.
    if(view&&view.result&&view.request&&JSON.stringify(view.request)===key)return Promise.resolve(view.result);
    if(cached&&Date.now()-cached.at<30000)return Promise.resolve(cached.result);
    releaseFlight(claim);
    const flying=contributionPending.get(key);
    if(flying){flying.waiters+=1;claim.waiting=flying;if(options.onProgress){flying.listeners.add(options.onProgress);if(flying.progress)options.onProgress(flying.progress);}return flying.promise;}
    // A pending answer's progress reaches every surface waiting on this flight (r7).
    const flight={key,controller:new AbortController(),waiters:1,promise:null,progress:null,listeners:new Set(options.onProgress?[options.onProgress]:[])};
    flight.promise=contributionReady('query',query,flight.controller.signal,{retry:options.retry,onProgress:progress=>{flight.progress=progress;for(const listener of flight.listeners)try{listener(progress);}catch(error){console.error(error);}}}).then(result=>{if(epoch===contributionCacheEpoch){contributionCache.set(key,{result,at:Date.now()});if(contributionCache.size>12)contributionCache.delete(contributionCache.keys().next().value);}return result;}).finally(()=>{if(contributionPending.get(key)===flight&&epoch===contributionCacheEpoch)contributionPending.delete(key);});
    contributionPending.set(key,flight);claim.waiting=flight;return flight.promise;
  }
  // The scope under the chart title: the parts the header chips carry, plus the
  // basis, so the chart and the chrome cannot disagree about what is on screen.
  function scopeSubtitle(state) {
    if(!data)return '';
    const parts=scopeText().split(' · ').filter(Boolean);
    if(verified()){const label=contributions.BASIS[basisOf(state)];if(label&&label[0])parts.push(label[0].toLowerCase());}
    return parts.join(' · ');
  }
  // What one chart draws into, and whose staleness ticket it carries. The single
  // canvas is the main surface, and its storage IS the module state the Inspector,
  // the exports, the print sheet and the briefing already read, so one code path
  // writes both. A board tile passes its own nodes and its own ticket; the
  // selected tile is primary and writes the module state too, which is what
  // "Export acts on the selected tile" means.
  function mainSurface() {
    return {state: sheet(), index: active, el: el, primary: true, flight: mainFlight, canvasClass: '',
      bump: () => ++contributionRevision, stale: ticket => ticket !== contributionRevision,
      ticket: () => contributionRevision,
      get offset() { return contributionOffset; }, set offset(value) { contributionOffset = value; },
      get request() { return contributionRequest; }, set request(value) { contributionRequest = value; },
      get result() { return contributionResult; }, set result(value) { contributionResult = value; },
      get title() { return lastTitle; }, set title(value) { lastTitle = value; },
      get scope() { return lastScope; }, set scope(value) { lastScope = value; },
      get coverage() { return lastCoverage; }, set coverage(value) { setCoverage(value); }};
  }
  const basisOf = state => (state || sheet()).source.basis || 'receipt';
  function sourceScope(result,request) {
    const p=result.provenance;
    return [request.dataset,contributions.BASIS[request.basis]?.[0],p.workbook,'Workbook SHA256 '+p.workbook_sha256,'Analytical index '+p.index_token,'Query engine SHA256 '+p.query_engine_sha256,'Field semantics SHA256 '+p.field_semantics_sha256,'Groups '+(result.offset+1)+'–'+(result.offset+result.rows.length)+' of '+result.group_count,JSON.stringify({filters:request.filters,aggregate_filters:request.aggregate_filters,bin_width_cents:request.bin_width_cents}),p.scope].join(' · ');
  }
  // FIX 4 (collection gaps): the report's own Data Quality check names the company x cycle cells whose stored
  // rows are materially fewer than FEC reported. Each query says which report (dataset) and which companies
  // and cycles (filters) it reads; coverage-view.js words what they touch, once, for the chip, the subtitle,
  // the exported scope line and the CSV. The catalogue carries each report's cells (collection_gaps).
  function gapNotice(queries) {
    const api = globalThis.MentatCoverage, datasets = (contributionCatalog && contributionCatalog.datasets) || [];
    if (!api) return null;
    return api.gapNotice(queries.map(query => ({gaps: (datasets.find(d => d.id === query.dataset) || {}).collection_gaps,
      scope: api.filtersScope(query.filters)})));
  }
  const withGap = (text, notice) => (notice ? globalThis.MentatCoverage.withGap(text, notice) : text);
  // The charts.js family on the verified path: each draws one measure from the
  // server's groups, so the others ride in each mark's hover detail.
  const CANVAS_TYPES = ['pie','doughnut','waterfall','pareto','radar'];
  const SHARE_TYPES = ['pie','doughnut','pareto','radar'];
  const markTypeName = type => core.markLabel(type === 'table' ? 'text' : type === 'scatter' ? 'circle' : type);
  const measuresIn = (rows, ids) => [...new Set(ids)].filter(id => rows.every(row => row[id] == null || (typeof row[id] === 'number' && Number.isFinite(row[id]))));
  // Paul, 2026-09-23: "nothing is logically constrained". A renderer that
  // cannot draw an arrangement never leaves the canvas empty: the same groups
  // are drawn as bars (or, failing that, as a table) and its reason is a note.
  function drawFallback(node, rows, spec, reason, notes, pick) {
    const measures = measuresIn(rows, spec.measures.length ? spec.measures : ['amount']);
    const dimensions = [...new Set(spec.dimensions || [])];
    const base = {...spec, dimensions, measures: measures.length ? measures : spec.measures.slice(0, 1), size: '', colorMeasure: '',
      categorical: false, dual: false, stack: '', panelTitles: measures.length > 1, color: dimensions.includes(spec.color) ? spec.color : ''};
    notes.push(markTypeName(spec.type) + ' could not draw this arrangement (' + reason + '), so the same groups are shown as bars.');
    try { contributionPlot.render(node('canvas'), rows, {...base, type: 'bar'}, pick); return true; }
    catch (error) {
      contributionPlot.render(node('canvas'), rows, {...base, type: 'table', color: '', barPitch: 0}, pick);
      notes.push('Bars could not be drawn either (' + error.message + '), so the groups are listed as a table.');
      return false;
    }
  }
  // Contract V11: year-over-year, period change and the forecast are computed
  // per measure and per series (each dimension after the time axis), and the
  // chosen mark is kept. One measure with no other dimension is today's model.
  function periodModel(rows, spec, compiled, state, notes) {
    const time = spec.dimensions[0], others = spec.dimensions.slice(1), ids = spec.measures;
    const period = time === 'cycle' ? 'cycle' : 'year', horizon = state.analytics.horizon || 2;
    const timeColumn = {index: 0, name: time === 'cycle' ? 'Election cycle' : 'Year', type: 'category'};
    const single = input => {
      const id0 = ids[0], modelData = {columns: [timeColumn, {index: 1, name: 'Amount', type: 'number'}],
        rows: input.map(r => [r[time], r[id0]]), row_numbers: input.map((_, i) => i + 1), row_count: input.length, header_row: 0};
      // Lenient (census B-E15, B-E17; Paul: "nothing is logically constrained"):
      // year-over-year on election cycles is cycle-over-cycle, and a short
      // history draws what it has; each says so in a note.
      const model = math.build(modelData, {type: 'line', x: 0, y: 1, series: -1, aggregation: 'sum', filters: [], transform: compiled.transform, period, horizon, start: 0, end: 0, limit: 0, lenient: true});
      const money = contributions.MONEY.has(id0), id = compiled.transform === 'yoy' ? 'percent_change' : id0;
      notes.push(model.note);
      for (const warning of model.warnings || []) if (!notes.includes(warning)) notes.push(warning);
      return {model: {...model, type: spec.type, groups: model.groups.map(g => ({...g, value: g.value / (money && id !== 'percent_change' ? 100 : 1)}))},
        column: {id, name: compiled.transform === 'forecast' ? 'Observed amount and same-phase baseline' : id === 'percent_change' ? (period === 'cycle' ? 'Cycle-over-cycle change (%)' : 'Year-over-year change (%)') : 'Period change', unit: id === 'percent_change' ? 'percent' : money ? 'cents' : 'count'}};
    };
    if (!others.length && ids.length === 1) return single(rows);
    const name = id => (columns().find(f => f.id === id) || {}).name || id;
    const key = (row, id) => [...others.map(d => row[d] == null || row[d] === '' ? 'Unknown' : String(row[d])), ...(ids.length > 1 ? [name(id)] : [])].join(' · ');
    const table = [];
    for (const row of rows) for (const id of ids) if (row[id] != null) table.push([row[time], key(row, id), row[id] / (contributions.MONEY.has(id) ? 100 : 1)]);
    const modelData = {columns: [timeColumn, {index: 1, name: 'Series', type: 'category'}, {index: 2, name: 'Value', type: 'number'}],
      rows: table, row_numbers: table.map((_, i) => i + 1), row_count: table.length, header_row: 0};
    try {
      const model = math.build(modelData, {type: ['bar', 'line', 'area', 'dot'].includes(spec.type) ? spec.type : 'line', x: 0, y: 2, series: 1,
        aggregation: 'sum', filters: [], transform: compiled.transform, period, horizon, start: 0, end: 0, limit: 0, lenient: true});
      if (model.note) notes.push(model.note);
      for (const warning of model.warnings || []) notes.push(warning);
      return {model: {...model, type: model.type || spec.type}, column: {id: 'value', name: compiled.transform === 'yoy' ? (period === 'cycle' ? 'Cycle-over-cycle change (%)' : 'Year-over-year change (%)') : compiled.transform === 'forecast' ? 'Observed value and same-phase baseline' : 'Period change', unit: compiled.transform === 'yoy' ? 'percent' : 'number'}};
    } catch (error) {
      notes.push('Period analysis could not separate ' + [...others.map(name), ...(ids.length > 1 ? ['the measures'] : [])].join(' and ') + ' into series (' + error.message + '), so it shows ' + name(ids[0]) + ' in total.');
      const totals = new Map();
      for (const row of rows) totals.set(row[time], (totals.get(row[time]) || 0) + (Number(row[ids[0]]) || 0));
      return single([...totals].map(([t, v]) => ({[time]: t, [ids[0]]: v})));
    }
  }
  // Marks v3 (lane M): the Colour measure, Size, Label and Tooltip of a verified
  // pie, doughnut, waterfall, Pareto or radar reach its drawing. Each group
  // carries its own labels and charts.js lends them to the result.
  const canvasEncodings=(group,row,spec,nameOf)=>contributions.canvasEncodings(group,row,spec,nameOf,contributionPlot.format);
  // Marks v3: one card's Opacity, Border and Halo (the Colour popover) laid over
  // whatever drew it. A map and a text table take none; their rows say why.
  function applyCardStyle(target,state,type){
    if(!target||!contributionPlot||typeof contributionPlot.styleMarks!=='function')return;
    const cards=core.readCards(state);if(cards.length!==1)return;
    const card=cards[0];if(card.opacity===100&&card.border==='automatic'&&card.halo==='automatic')return;
    if(['map','filled-map','text'].includes(card.type)||type==='table'||type==='map')return;
    const svg=String(target.tagName||'').toLowerCase()==='svg'?target:target.querySelector&&target.querySelector('svg');
    if(svg)contributionPlot.styleMarks(svg,card);
  }
  async function renderContributionView(view, again=null) {
    view=view||mainSurface();const node=view.el,state=view.state;
    node('reconcile').hidden=true;
    if(!data||!dataset()){view.coverage=null;node('reconcile').hidden=true;node('canvas').replaceChildren();node('table').replaceChildren();node('verified-summary').textContent='Reading verified source fields…';node('result-controls').hidden=true;node('title').textContent='';node('subtitle').textContent='';node('note').textContent='Choose a verified report in the Data panel to begin.';if(view.primary)lastResult=null;return;}
    const ticket=view.bump(),sheetBasis=basisOf(state);
    // r7: a pending answer draws its progress on this surface's canvas and
    // status line; a panel an earlier render of it left goes first.
    clearPending(node('canvas'));
    let statusBefore=null,statusWrote=null;
    const onProgress=progress=>{if(view.stale(ticket))return;drawPending(node('canvas'),progress);if(statusWrote===null)statusBefore=node('status').textContent;statusWrote=contributions.pendingText(progress);node('status').textContent=statusWrote;};
    try {
      const compiled=contributions.compile(state,columns(),{dataset:dataset().id,industry:dataset().industry,basis:sheetBasis,offset:view.offset});
      if(view.el===el)compareDrawn=null;
      // Census B-S32 (Paul, 2026-09-23: "nothing is logically constrained"):
      // compared scopes are drawn in every mark, with Colour and with a period
      // analysis (renderCompareView).
      if(view.el===el&&compareActive(state)){
        await renderCompareView(view,compiled,ticket,{retry:again?.retry===true,onProgress});return;
      }
      const query=compiled.query,key=JSON.stringify(query),previous=view.request;
      if(previous&&JSON.stringify({...previous,offset:0})!==JSON.stringify({...query,offset:0})&&view.offset){view.offset=0;query.offset=0;}
      // A first answer on its way: the placeholder asking for a report stops being
      // true once one is chosen, and a first query can wait a long time on a
      // console still preparing its data (r6: over a minute on 8806 while it
      // rebuilt its query store after a restart), under a blank canvas.
      if(!view.result&&node('note').textContent==='Choose a verified report in the Data panel to begin.')node('note').textContent='Reading verified source observations\u2026';
      if(!previous||JSON.stringify(previous)!==key){node('canvas').setAttribute('aria-busy','true');node('canvas').__busyTicket=ticket;node('verified-summary').textContent='Reading verified totals…';if(view.primary){status('Reading verified source observations…');el('inspection').hidden=true;++recordRevision;lastResult=null;}}
      const result=await queryCached(query,view,{retry:again?.retry===true,onProgress});if(view.stale(ticket)||!verified())return;clearPending(node('canvas'));
      view.result=result;view.request=query;
      if(view.primary){failure('');lastResult=result;lastConfig=compiled;data.row_count=result.totals.records;data.rows=result.rows.map(row=>data.columns.map(f=>row[f.id]??''));}
      let title=contributions.BASIS[sheetBasis][0]+(query.dimensions.length?' by '+query.dimensions.map(id=>data.columns.find(f=>f.id===id)?.name||id).join(' / '):'');if(compiled.spec.measures.length!==1||compiled.spec.measures[0]!=='amount')title=compiled.spec.measures.map(id=>result.columns.find(c=>c.id===id)?.name||id).join(' / ')+' · '+title;const gap=gapNotice([query]);view.coverage=gap;view.scope=withGap(sourceScope(result,query),gap);view.title=showTitle(node,title,state,sheetBasis);node('subtitle').textContent=withGap(scopeSubtitle(state),gap);
      const money=value=>contributionPlot.format(value,'amount');
      node('verified-summary').replaceChildren();
      for(const [label,value]of [[sheetBasis==='original_supported'?'Selected gross amount':'Selected net amount',money(result.totals.amount)],['Source observations',fmt(result.totals.records)],['Reported donor identities',fmt(result.totals.contributors)]]){const item=document.createElement('span'),strong=document.createElement('strong');strong.textContent=value;item.append(strong,document.createTextNode(label));node('verified-summary').append(item);}
      node('verified-summary').title='Totals cover all matching source records before grouped filters. Group distinct identity counts are not added together.';
      node('group-count').textContent=result.group_count?`Groups ${fmt(result.offset+1)}–${fmt(result.offset+result.rows.length)} of ${fmt(result.group_count)}`:'No matching observations';
      node('group-count').title=result.group_count?`Totals cover all ${fmt(result.group_count)} groups`:'';
      node('groups-prev').disabled=!query.offset;node('groups-next').disabled=!result.has_more;node('result-controls').hidden=false;
      // The arrows appear only when there is another page of groups (S1).
      node('groups-prev').hidden=node('groups-next').hidden=compiled.spec.type==='map'||compiled.transform!=='none'||(!query.offset&&!result.has_more);
      node('inspect-all').disabled=false;node('inspect-all').title='';
      let rows=compiled.spec.naturalOrder?contributions.naturalRows(result.rows,compiled.spec.dimensions):result.rows,spec={...compiled.spec,columns:result.columns,title:title,barPitch:28,niceTicks:true,panelTitles:compiled.spec.measures.length!==1,width:node('canvas').clientWidth,barLeftMax:190,seriesColor:seriesColour(),...roomFit(node('canvas')),...cardFit(view,compiled.spec)},temporal=null;const notes=[...compiled.notes,...(result.notices||[])];
      if(spec.type==='table'&&!state.showLabels)notes.push(TABLE_LABELS_OFF);
      // Incomplete pages are drawn and said to be incomplete (census B-S30).
      if(spec.type==='histogram'&&result.has_more)notes.push('This histogram has more than 10,000 occupied bins and shows the first 10,000. Increase the bin width or narrow the filters to see the complete distribution.');
      if(compiled.transform!=='none'&&compiled.transform!=='annual'){
        if(result.has_more)notes.push('Period analysis read the first 100,000 groups; narrow the filters so every period is included.');
        temporal=periodModel(rows,spec,compiled,state,notes);
      }
      // One map at a time: it owns the geometry cache and the Census request, so
      // it is drawn for the tile the reader selected. An unselected map tile names
      // the gesture that brings it back rather than drawing nothing.
      if(spec.type==='map'){
        await renderContributionMap(ticket,compiled,null,view,notes);return;
      }
      dropMapsIn(node('canvas'));
      node('canvas').hidden=false;node('table').hidden=true;node('canvas').className=view.canvasClass+'studio-canvas fit-'+state.fit;delete node('canvas').dataset.pieFit;
      const options=chartOptions(state),pick=row=>{if(view.primary)inspectContributions(row);};
      try{
        if(temporal){
          const measure=temporal.column.name+(temporal.column.unit==='cents'?' (USD)':'');
          // A pie is fitted to its chart box on screen (screenOptions).
          const drawn=paint.chartSVG(temporal.model,measure,{type:temporal.model.type||spec.type,aggregation:'sum',x:0,y:1},{sheet:sheetName(),columns:[{name:'Period'},{name:measure}]},view.title,view.scope,screenOptions(view,options,temporal.model.type||spec.type));node('canvas').replaceChildren(drawn.svg);placeFitted(drawn.svg);
          const lines=core.referenceValues(temporal.model,state.analytics);if(lines.length&&drawn.plot)overlayReference(drawn.svg,drawn.plot,lines);
        }else if(CANVAS_TYPES.includes(spec.type)){
          const ids=spec.measures,id=ids[0],currency=contributions.MONEY.has(id),nameOf=m=>result.columns.find(c=>c.id===m)?.name||m;
          if(ids.length>1)notes.push(markTypeName(spec.type)+' draws one measure; '+ids.slice(1).map(nameOf).join(', ')+(ids.length===2?' is':' are')+' in each mark’s hover detail.');
          let groups=rows.map(row=>canvasEncodings({label:contributionPlot.label(row,spec.dimensions),value:row[id]/(currency?100:1),n:1,extra:Object.fromEntries(ids.slice(1).map(m=>[nameOf(m),row[m]==null?null:row[m]/(contributions.MONEY.has(m)?100:1)]))},row,spec,nameOf));
          // Shares and a radar need nonnegative values: negative groups are left
          // out and counted, never allowed to stop the drawing (census B-E07).
          if(SHARE_TYPES.includes(spec.type)){const negative=groups.filter(g=>g.value<0).length;if(negative){groups=groups.filter(g=>g.value>=0);notes.push(fmt(negative)+' group'+(negative===1?' has':'s have')+' a negative '+nameOf(id)+' and '+(negative===1?'is':'are')+' left out of the '+markTypeName(spec.type).toLowerCase()+'; bars show signed amounts.');}}
          const drawn=paint.chartSVG({type:spec.type,groups,selected:result.totals.records,excluded:0,warnings:[]},nameOf(id)+(currency?' (USD)':''),{type:spec.type,aggregation:'sum',x:0,y:1},{sheet:sheetName(),columns:[{name:'Group'},{name:id}]},view.title,view.scope,screenOptions(view,options,spec.type));node('canvas').replaceChildren(drawn.svg);placeFitted(drawn.svg);
          const lines=core.referenceValues({type:spec.type,groups},state.analytics);if(lines.length&&drawn.plot)overlayReference(drawn.svg,drawn.plot,lines);
        }else if(MentatContributionCharts.TYPES.includes(spec.type))MentatContributionCharts.render(node('canvas'),rows,spec,pick);
        else contributionPlot.render(node('canvas'),rows,spec,pick);
      }catch(error){drawFallback(node,rows,spec,error.message,notes,pick);}
      applyCardStyle(node('canvas'),state,spec.type);
      if(state.analytics.totals)notes.push('Selected source total: '+money(result.totals.amount)+'. This total excludes projections and covers every matching group before grouped filters.');
      // Say plainly when a requested reference line was not drawn, instead
      // of describing one the reader cannot see.
      if(state.analytics.average||state.analytics.median)notes.push(
        node('canvas').querySelector('[data-reference]')?'Reference lines mark the mean and median of the shown marks; source-level averages and medians are available as measures.'
        :'This chart has no value axis for a reference line, so the mean and median are not drawn; each mark carries its value on hover.');
      node('note').textContent=[contributions.BASIS[sheetBasis][1],query.aggregate_filters.length?'Grouped filters apply after aggregation.':result.has_more?'Additional groups are on later pages; source totals cover all pages.':'All selected groups shown.',...notes].join(' ');
      node('status').textContent=fmt(result.totals.records)+' source observations · '+fmt(result.group_count)+' groups · '+(result.reconciliation?.receipt_matches_workbook===true?'reconciles to the workbook':'verified source records');
      node('status').title=view.primary?fmt(queryRequests)+' verified queries this session':'';
      if(view.primary)status('Verified contribution fields · '+data.columns.length+' fields');
      if(view.primary)renderPager();
      renderReconcile(result,view,ticket).catch(()=>{node('reconcile').hidden=true;});
    }catch(error){if(!view.stale(ticket)&&error.name!=='AbortError'){
      // Final fix round (R03-10): a sheet that draws nothing keeps nothing of
      // the last answer (title, figures, counter, status); a sheet with no
      // measure yet gets muted guidance under its empty title, not the red error.
      const guidance=/^Add a dollar or count measure/.test(error.message),retry=!guidance&&error.retryable===true;
      node('reconcile').hidden=true;node('canvas').replaceChildren();node('note').textContent=guidance||retry?'':error.message;
      view.coverage=null;node('title').textContent='';node('subtitle').textContent=guidance?error.message:'';node('verified-summary').replaceChildren();node('result-controls').hidden=true;node('status').textContent='';node('status').title='';
      // r7: a request that failed, or a wait that reached its ceiling, says so on
      // the canvas, once, with a Retry that asks again with retry:true.
      if(retry)drawStopped(node('canvas'),error.message,()=>renderContributionView(view,{retry:true}));
      if(view.primary){failure(guidance||retry?'':error.message);lastResult=null;}}}
    finally{if(!view.stale(ticket)){clearPending(node('canvas'),'pending');if(statusWrote!==null&&node('status').textContent===statusWrote)node('status').textContent=statusBefore;if(!view.result&&node('note').textContent==='Reading verified source observations\u2026')node('note').textContent='Choose a verified report in the Data panel to begin.';node('canvas').setAttribute('aria-busy','false');node('note').title=node('note').textContent;}else if(node('canvas').__busyTicket===ticket)/* A render that went stale still owns the busy mark it set (opacity .45, no pointer events): a Data source switch aborts it, and a map layer or report worksheet drawn next never bumps this ticket, so nothing else would clear it (lane L2, 2026-09-26). A newer render that set its own mark owns it instead. */node('canvas').setAttribute('aria-busy','false');}
  }
  async function renderContributionMap(ticket,compiled,bbox=null,view=null,notes=[]) {
    view=view||mainSurface();const node=view.el;
    const host=node('canvas'),slot=mapSlot(host),current=++slot.revision,result=view.result,map=view.state.map||{layers:['states','districts'],congress:'119'},zips=MentatContributionMap.eligibleZips(result.rows).sort(),key=JSON.stringify({zips,congress:map.congress,bbox});
    // An incomplete page is mapped and said to be incomplete (census B-S30).
    if(result.has_more)notes=[...notes,'The map shows the first '+fmt(result.rows.length)+' of '+fmt(result.group_count)+' places; narrow the filters to map every one.'];
    const saved=slot.map?.getView();status('Loading Census boundaries…');
    const cached=geometryCache.get(key),pendingKey=key+'|'+[...map.layers].sort().join(',');let geometry=cached&&map.layers.every(layer=>cached.layers?.[layer])?cached:null;if(!geometry){if(!geometryPending.has(pendingKey))geometryPending.set(pendingKey,contributionAPI('geography',{zips,layers:map.layers,congress:map.congress,...(bbox?{bbox}:{})}).finally(()=>geometryPending.delete(pendingKey)));geometry=await geometryPending.get(pendingKey);geometryCache.set(key,geometry);}
    if(view.stale(ticket)||current!==slot.revision||mapHosts.get(host)!==slot||!verified())return;
    geometryCache.set(key,geometry);if(geometryCache.size>8)geometryCache.delete(geometryCache.keys().next().value);contributionGeometry=geometry;geometryKey=key;slot.map?.destroy?.();dropMapsIn(host,host);node('canvas').hidden=false;node('table').hidden=true;
    // Contract V10: the map shades by the sheet's own measure (`value`, in its
    // own unit); amount_cents stays the selected amount for the coverage line.
    const shaded=compiled.spec.measures[0]||'amount',unit=contributions.MONEY.has(shaded)?'cents':['records','contributors'].includes(shaded)?'count':'number';
    const drawnMap=slot.map=MentatContributionMap.render(host,{groups:result.rows.map(r=>({...r,amount_cents:r.amount,value:r[shaded]})),valueField:shaded==='amount'?'amount_cents':'value',unit,geometry,layers:map.layers,filled:compiled.spec.filled,title:view.title,valueLabel:shaded==='amount'?(basisOf(view.state)==='original_supported'?'Selected gross amount':'Selected net amount'):((result.columns.find(c=>c.id===shaded)||{}).name||shaded),showLayerControls:false,onSelect:row=>{if(view.primary)inspectContributions(contributions.mapSelection(row));},onViewport:event=>{if(!map.layers.includes('zip_areas'))return;clearTimeout(slot.timer);slot.timer=setTimeout(()=>renderContributionMap(view.ticket(),compiled,event.bbox,view,notes).catch(e=>failure(e.message)),450);}});
    if(saved)drawnMap.setView(saved);
    view.scope+=' · Congress '+map.congress+'; '+map.layers.join(', ')+'; overlays do not allocate money. Mapped '+contributionPlot.format(drawnMap.data.mapped_cents,'amount')+'; unmapped '+contributionPlot.format(drawnMap.data.unmapped_cents,'amount')+' · '+(geometry.sources||[]).map(s=>(s.vintage||s.label||s.url)+' SHA256 '+s.sha256).join(' · ');
    node('note').textContent=[contributions.BASIS[basisOf(view.state)][1],'ZIP marks combine eligible ZIP/state groups; grouped filters apply before this combination. Unmapped money remains in the selected total.',...(drawnMap.data.coverage_notes||[]),...(geometry.warnings||[]),...notes].join(' ');
    node('status').textContent=fmt(result.group_count)+' geographic groups · '+fmt(drawnMap.data.marks.length)+' mapped marks · boundaries do not change money';status('Verified ZIP/state records and Census boundaries');
    await renderReconcile(result,view,ticket).catch(()=>{node('reconcile').hidden=true;});
  }
  /* F3 (2026-09-24): ActBlue and WinRed filings have no FEC page image ("Error#1896: Image is
     not processed yet" on 14 of 14 tried), so a record filed by either links its whole
     electronic report. Its report number comes from one narrow second records request after
     the table is drawn (reported_file_num, read through the pinned source database). */
  const FEC_CONDUITS=new Set(['C00401224','C00694323']),FEC_REPORT_TITLE='FEC has no page image for this filing';
  const conduitRecord=record=>FEC_CONDUITS.has(String(record?.committee_id||'').trim().toUpperCase());
  function drawConduitFiling(td,record,fileNumber){
    const number=String(fileNumber??'').trim();
    const safe=fileNumber!==undefined&&/^[1-9]\d{0,11}$/.test(number)&&globalThis.MentatScope?globalThis.MentatScope.fecLink(`https://docquery.fec.gov/cgi-bin/forms/${String(record.committee_id).trim().toUpperCase()}/${number}/`):'';
    if(safe){const a=document.createElement('a');a.href=safe;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';a.textContent='FEC report';a.title=FEC_REPORT_TITLE;td.replaceChildren(a);td.title=FEC_REPORT_TITLE;return;}
    td.replaceChildren();td.textContent=fileNumber===undefined?'FEC report…':'Unavailable';
    td.title=FEC_REPORT_TITLE+(fileNumber===undefined?'.':', and its report number could not be read here.');
  }
  async function fillConduitFilings(query,result,cells,current){
    const ids=[...new Set(cells.map(([,record])=>String(record.source_id||'')).filter(Boolean))];let numbers=null;
    try{const reply=await contributionReady('records',{...query,dimensions:['reported_file_num'],aggregate_filters:[],
        filters:[...(query.filters||[]),{field:'source_id',op:'in',value:ids}],limit:ids.length,offset:0},null,{current});
      if(!current())return;
      if(reply.status==='ready'&&['workbook_sha256','database_sha256','index_token'].every(key=>reply.provenance?.[key]===result.provenance?.[key]))
        numbers=new Map((reply.rows||[]).map(row=>[String(row.source_id),row.reported_file_num]));
    }catch(error){if(!current())return;}
    for(const[td,record]of cells)drawConduitFiling(td,record,numbers?(numbers.get(String(record.source_id))??null):null);
  }
  let recordSource = null;
  async function inspectContributions(row=null,offset=0,source=null,again=null) {
    if(!contributionResult)return;recordSelection=row;recordOffset=offset;recordSource=source;const ticket=++recordRevision,sourceRevision=contributionRevision;el('inspection').hidden=false;el('record-count').textContent='Reading source records…';el('records').replaceChildren();
    // A compared scope that is its own report (industry) brings its own query
    // and provenance; every other drill reads the sheet's one query.
    const request=(source&&source.request)||contributionRequest,known=(source&&source.result)||contributionResult;
    try{const query={...request,filters:contributions.markFilters(request,row),aggregate_filters:[],limit:100,offset};const current=()=>ticket===recordRevision&&sourceRevision===contributionRevision;
      const result=await contributionReady('records',query,null,{retry:again?.retry===true,current,onProgress:progress=>{if(current())drawPending(el('records'),progress);}});if(ticket!==recordRevision||sourceRevision!==contributionRevision)return;if(!['workbook_sha256','database_sha256','index_token'].every(key=>result.provenance?.[key]===known.provenance?.[key]))throw new Error('Source changed before record inspection. Refresh the verified data.');
      el('records').replaceChildren();const columns=[['date','Date'],['contributor','Contributor'],['company','Company'],['recipient','Recipient'],['amount_cents','Selected amount'],['raw_cents','Raw observation'],['zip5','ZIP'],['state','State'],['decision_status','Accounting status'],['receipt_role','Receipt role'],['reasons','Reasons'],['source_id','Source ID'],['source_url','FEC filing']];for(const id of query.dimensions||[])if(!columns.some(c=>c[0]===id))columns.push([id,data.columns.find(c=>c.id===id)?.name||id]);const table=document.createElement('table'),head=document.createElement('tr'),body=document.createElement('tbody');for(const[,name]of columns){const th=document.createElement('th');th.textContent=name;head.append(th);}const thead=document.createElement('thead');thead.append(head);table.append(thead,body);
      const conduitCells=[];for(const record of result.rows){const tr=document.createElement('tr');for(const[id]of columns){const td=document.createElement('td'),value=record[id];td.textContent=id.endsWith('_cents')?contributionPlot.format(value,'amount'):Array.isArray(value)?value.join('; '):value??'';if(id==='recipient_category'&&globalThis.MentatTheme?.categoryNote?.(value))td.title=globalThis.MentatTheme.categoryNote(value);if(id==='source_url'){if(conduitRecord(record)){conduitCells.push([td,record]);drawConduitFiling(td,record,undefined);}else{const safe=globalThis.MentatScope?globalThis.MentatScope.fecLink(value):'';if(safe){const a=document.createElement('a');a.href=safe;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';a.textContent='Open filing';td.replaceChildren(a);}}}tr.append(td);}body.append(tr);}el('records').append(table);if(conduitCells.length)fillConduitFilings(query,result,conduitCells,()=>ticket===recordRevision&&sourceRevision===contributionRevision);el('record-count').textContent=`${result.total_records?offset+1:0}–${offset+result.rows.length} of ${fmt(result.total_records)} source observations`;el('record-prev').disabled=!offset;el('record-next').disabled=!result.has_more;
    }catch(error){if(ticket===recordRevision){
      // r7: a stopped wait (Cancel, the ceiling) or a failed request can be asked again, with retry:true.
      el('record-count').textContent=error.name==='AbortError'?'Stopped reading source records.':error.message;
      if(error.name==='AbortError'||error.retryable===true)drawStopped(el('records'),'',()=>inspectContributions(row,offset,source,{retry:true}));
      else el('records').replaceChildren();}}
  }
  async function downloadContributions(format) {
    const view=activeSurface();if(exporting)return;if(!view.result||!lastResult){failure('Wait for the chart to finish loading before exporting.');return;}const result=view.result,query=structuredClone(view.request),ticket=view.ticket();
    const exportStatus=message=>{status(message);el('status').textContent=message;};
    // Two reports cannot share one CSV: each row of it names one workbook's
    // provenance. The image carries both scopes in its legend and caption.
    if(format==='csv'&&compareDrawn&&compareDrawn.kind==='industry'&&view.el===el){failure('Export the CSV one industry at a time: each report carries its own workbook provenance. Clear the comparison, or export the image.');return;}
    try{if(format==='csv'){exporting=true;exportStatus('Preparing complete grouped CSV…');let rows=[],offset=0;for(;;){const page=await contributionReady('query',{...query,offset,limit:100000},null,{current:()=>!view.stale(ticket)});if(view.stale(ticket))throw new Error('View changed during export. Start the download again.');if(!contributions.sameSource(page.provenance,result.provenance))throw new Error('Source changed during export. Refresh the verified data.');rows.push(...page.rows);if(!page.has_more)break;if(!page.rows.length)throw new Error('The next export page is empty.');offset+=page.rows.length;}MentatExport.download(contributions.csv({...result,rows},query,view.coverage?view.coverage.text:''),'text/csv;charset=utf-8','mentat-chart-studio.csv');exportStatus('CSV download requested: '+fmt(rows.length)+' groups with source and accounting provenance.');}
      else{exporting=true;exportStatus('Preparing '+format.toUpperCase()+' image…');const svg=view.el('canvas').querySelector('svg');if(!svg)throw new Error('This view has no image. Download its data CSV.');await MentatExport.image(svg,format,view.title,view.scope,'mentat-chart-studio');exportStatus(format.toUpperCase()+' download requested.');}
    }catch(error){failure(error.name==='AbortError'?(view.stale(ticket)?'View changed during export. Start the download again.':'Export cancelled. Start the download again.'):error.message);}finally{exporting=false;}
  }

  /* ---------------------------------------------------------------- pills */

  function fieldLabel(field, aggregation) {
    if(verified()&&field.role==='measure'&&field.id!=='amount')return field.name;
    if (field.role !== 'measure' || !aggregation) return field.name;
    return (AGG_LABEL[aggregation] || 'SUM') + '(' + field.name + ')';
  }
  function fieldTooltip(field) {
    if(verified())return [field.name,'Data type: '+(field.data_type||field.type),'Role: '+field.role,field.discrete?'Discrete grouping':'Continuous aggregate',field.table,field.geographic_role?'Geography: '+field.geographic_role:'',field.description].filter(Boolean).join(' · ');
    const kind = field.role === 'measure' ? 'measure' : field.isTime ? 'date dimension' : 'dimension';
    return [field.name, 'detected type: ' + field.type + ' (' + kind + ')',
      'distinct values: ' + (field.distinct == null ? 'unknown' : fmt(field.distinct)),
      'missing values: ' + (field.missing == null ? 'unknown' : fmt(field.missing))].join(' · ');
  }
  function pillNode(field, options) {
    const settings = options || {};
    const node = document.createElement('span');
    node.className = 'studio-pill' + (field.role === 'measure' ? ' is-measure' : '') +
      (selection.includes(field.index) && !settings.zone ? ' is-selected' : '');
    node.draggable = true;
    node.tabIndex = 0;
    node.dataset.column = String(field.index);
    if (settings.zone) node.dataset.zone = settings.zone;
    if (settings.position != null) node.dataset.position = String(settings.position);
    // The gesture comes first in the tooltip: the field facts are useful, but
    // what a click will do is the thing a reader needs before they click.
    node.title = settings.zone
      ? fieldTooltip(field) + ' · Enter to move it, Delete to remove it'
      : CLICK_HINT + ' · ' + fieldTooltip(field);
    node.setAttribute('role', 'button');
    node.setAttribute('aria-label', fieldLabel(field, settings.aggregation) +
      (settings.zone ? ', on ' + settings.zone + '. Press Enter for actions.' : ', field. ' + CLICK_HINT + '.'));
    const glyph = document.createElement('span');
    glyph.className = 'studio-glyph';
    // S1 draws Abc on places (State, ZIP) as on any category; only a true
    // date takes a drawn glyph. The title still names the geography.
    if (field.glyph === 'cal') glyph.append(typeGlyph('cal'));
    else glyph.textContent = field.glyph === 'bool' ? 'T/F' : field.glyph === '#' ? '#' : 'Abc';
    glyph.title = field.glyph === 'cal' ? 'Date' : field.glyph === 'geo' ? 'Geography'
      : field.glyph === '#' ? 'Number' : field.glyph === 'bool' ? 'True/false' : 'Text';
    const name = document.createElement('span');
    name.className = 'studio-pill-name';
    name.textContent = fieldLabel(field, settings.aggregation);
    node.append(glyph, name);
    if (settings.zone) {
      const menu = document.createElement('button');
      menu.type = 'button';
      menu.className = 'studio-pill-menu';
      menu.textContent = '▾';
      menu.setAttribute('aria-label', 'Actions for ' + field.name);
      menu.onclick = event => { event.stopPropagation(); sendMenu(node, field, settings); };
      node.append(menu);
      node.oncontextmenu = event => { event.preventDefault(); sendMenu(node, field, settings); };
    } else {
      // Lane P: a Data-pane field's menu on right-click ("Add to new layer").
      node.oncontextmenu = event => { event.preventDefault(); sendMenu(node, field, settings); };
    }
    node.addEventListener('dragstart', event => {
      dragging = settings.zone
        ? {kind: 'pill', column: field.index, zone: settings.zone, position: settings.position}
        : {kind: 'field', column: field.index};
      dropped = false;
      node.dataset.dragging = 'true';
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', field.name);
    });
    node.addEventListener('dragend', () => {
      node.removeAttribute('data-dragging');
      if (dragging && dragging.kind === 'pill' && !dropped) commit(() => removeFrom(dragging.zone, dragging.position));
      dragging = null;
    });
    node.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sendMenu(node, field, settings); }
      else if ((event.key === 'Delete' || event.key === 'Backspace') && settings.zone) {
        event.preventDefault();
        commit(() => removeFrom(settings.zone, settings.position));
      }
    });
    node.addEventListener('click', () => {
      if (settings.zone === 'filters') { filterEditor(node, settings.position); return; }
      if (settings.zone) return;
      // A click on a Data-pane field goes through the same place()/commit()
      // path a drop uses, so it is one undo step and one query, not a
      // selection the reader then has to do something else with.
      quickAdd(field);
    });
    return node;
  }

  function popup(anchor, items) {
    document.querySelectorAll('.studio-send').forEach(node => node.remove());
    const menu = document.createElement('div');
    menu.className = 'studio-send';
    for (const [label, action, options] of items) {
      const settings = options || {};
      if (settings.group) {
        const heading = document.createElement('div');
        heading.className = 'studio-send-group';
        heading.textContent = label;
        menu.append(heading);
        continue;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      if (settings.disabled || !action) {
        button.disabled = true;
        button.title = settings.reason || '';
      } else {
        button.onclick = () => {
          menu.remove();
          if (anchor.isConnected) anchor.focus();
          action();
        };
      }
      menu.append(button);
    }
    const box = anchor.getBoundingClientRect();
    menu.style.left = Math.round(box.left + window.scrollX) + 'px';
    menu.style.top = Math.round(box.bottom + window.scrollY + 4) + 'px';
    document.body.append(menu);
    // A long menu must never run off the bottom of the window: the entries at
    // the end are the ones nobody thinks to scroll for.
    const height = menu.getBoundingClientRect().height;
    const below = window.innerHeight - box.bottom;
    if (height > below && box.top > below) {
      menu.style.top = Math.max(4, Math.round(box.top + window.scrollY - height - 4)) + 'px';
    }
    const first = menu.querySelector('button:not(:disabled)');
    if (first) first.focus();
    const dismiss = () => {
      menu.remove();
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
    };
    const outside = event => { if (!menu.contains(event.target)) dismiss(); };
    const escape = event => { if (event.key === 'Escape') { dismiss(); anchor.focus(); } };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return menu;
  }
  const engine = () => verified() ? 'verified' : 'worksheet';
  const fieldOf = column => core.fieldAt(core.classifyFields(columns(), sheet().types), Number(column));

  // Type glyphs are drawn, never typed: no emoji, and they take the pill's ink.
  function typeGlyph(kind) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 12 12');
    svg.setAttribute('aria-hidden', 'true');
    const shapes = kind === 'cal'
      ? [['rect', {x: 1, y: 2.6, width: 10, height: 8.4, rx: 1, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.2}],
         ['line', {x1: 1, y1: 5.2, x2: 11, y2: 5.2, stroke: 'currentColor', 'stroke-width': 1.2}],
         ['line', {x1: 3.6, y1: 1, x2: 3.6, y2: 3.6, stroke: 'currentColor', 'stroke-width': 1.2}],
         ['line', {x1: 8.4, y1: 1, x2: 8.4, y2: 3.6, stroke: 'currentColor', 'stroke-width': 1.2}]]
      : [['path', {d: 'M6 1.1c-2 0-3.5 1.5-3.5 3.4C2.5 7.2 6 11 6 11s3.5-3.8 3.5-6.5C9.5 2.6 8 1.1 6 1.1z',
          fill: 'none', stroke: 'currentColor', 'stroke-width': 1.2}],
         ['circle', {cx: 6, cy: 4.5, r: 1.2, fill: 'currentColor'}]];
    for (const [tag, attributes] of shapes) {
      const node = document.createElementNS(NS, tag);
      for (const name of Object.keys(attributes)) node.setAttribute(name, String(attributes[name]));
      svg.append(node);
    }
    return svg;
  }

  // Data-type actions. A verified field keeps the role its source defines, so
  // the menu says so rather than offering a change the query cannot express.
  function typeMenuItems(anchor, field, settings) {
    const items = [['Data type', null, {group: true}]];
    const overrides = sheet().types || {};
    for (const role of ['dimension', 'measure']) {
      const label = 'Treat as ' + (role === 'dimension' ? 'Dimension' : 'Measure');
      if (verified()) {
        items.push([label, null, {disabled: true,
          reason: 'Verified fields keep the role the source defines. ZIP, committee ID and election cycle are already dimensions.'}]);
        continue;
      }
      const check = core.canTreatAs(field, role);
      if (!check.ok) { items.push([label, null, {disabled: true, reason: check.reason}]); continue; }
      items.push([label, () => commit(() => {
        const next = Object.assign({}, sheet().types || {});
        next[String(field.index)] = {role: role};
        sheet().types = next;
      })]);
    }
    if (overrides[String(field.index)]) items.push(['Use the detected type', () => commit(() => {
      const next = Object.assign({}, sheet().types || {});
      delete next[String(field.index)];
      sheet().types = next;
    })]);
    return items;
  }

  // Date part is one control, not two: it replaces the older "Use <field> here"
  // swap, it sits at the top of the pill menu so it can never be the entry that
  // falls below the fold, and it reads the catalog's date hierarchy.
  function datePartItems(anchor, field, settings) {
    const parts = core.datePartsFor(field, columns(), engine(), sheet());
    if (!parts.length) return [];
    const items = [['Date part', null, {group: true}]];
    for (const part of parts) {
      if (part.current) {
        items.push(['\u2713 ' + part.label, null,
          {disabled: true, reason: 'This pill already groups by ' + part.label.toLowerCase() + '.'}]);
        continue;
      }
      items.push([part.label, () => commit(() => {
        if (!verified()) { sheet().analytics.period = part.id; sheet().analytics.transform = 'annual'; return; }
        const zone = settings.zone === 'rows' ? 'rows' : settings.zone === 'columns' ? 'columns' : '';
        if (zone) {
          const pills = core.shelf(sheet()[zone]);
          if (pills[settings.position]) {
            pills[settings.position] = {column: part.column, aggregation: 'sum'};
            sheet()[zone] = pills;
            return;
          }
        }
        place('columns', part.column);
      })]);
    }
    return items;
  }

  // The zone a single click uses, and the sentence that names it.
  const zoneLabel = zone => (ZONES.find(z => z[0] === zone) || [zone, zone])[1];
  const quickAddLabel = field => zoneLabel(core.quickAddZone(field, sheet()));
  // A drag takes the pill with it; a click has no pill to take, so a click on
  // a field already on a shelf adds it once more (census B-S26; Paul,
  // 2026-09-23: "nothing is logically constrained") - a second SUM, an AVG
  // beside a SUM, a dimension on both shelves - and the status line says so.
  const placedOn = field => ['columns', 'rows'].find(name =>
    core.shelf(sheet()[name]).some(p => p.column === field.index));
  function quickAdd(field) {
    const placed = placedOn(field);
    const zone = core.quickAddZone(field, sheet());
    commit(() => place(zone, field.index));
    status('Added ' + field.name + ' to ' + zoneLabel(zone) + (placed ? ' again.' : '.'));
  }

  function sendMenu(anchor, field, settings) {
    const items = [];
    // The first entry is the gesture a click performs, so the keyboard reaches
    // the same shelf the mouse does without reading down to the Place group.
    if (!settings.zone) {
      const placed = placedOn(field);
      items.push([(placed ? 'Add again to ' : 'Add to ') + quickAddLabel(field), () => quickAdd(field)]);
    }
    if (settings.zone === 'filters') items.push(['Edit filter\u2026', () => filterEditor(anchor, settings.position)]);
    items.push(...datePartItems(anchor, field, settings));
    // Every measure pill has its aggregation, whichever shelf or mark property
    // holds it: a Colour or Size measure is aggregated too.
    const onAxis = !!settings.zone && !['filters', 'pages'].includes(settings.zone);
    if (onAxis && field.role === 'measure') {
      const aggregations = core.aggregationsFor(field, engine());
      items.push(['Measure', null, {group: true}]);
      if (aggregations.length) items.push(['Aggregation\u2026', () => aggregationMenu(anchor, settings.zone, settings.position, aggregations)]);
      else items.push(['Aggregation\u2026', null, {disabled: true,
        reason: field.name + ' is computed from the source observations, so it has one definition.'}]);
      const others = core.classifyFields(columns(), sheet().types).measures.filter(f => f.index !== field.index);
      items.push(['Replace measure\u2026', others.length ? () => popup(anchor, others.map(other => [other.name, () => commit(() => {
        const pills = zonePills(settings.zone);
        if (pills[settings.position]) { pills[settings.position] = {column: other.index, aggregation: 'sum'}; setZonePills(settings.zone, pills); }
      })])) : null, {disabled: !others.length, reason: 'This source has no other measure.'}]);
      items.push(['Political money\u2026', () => popup(anchor, core.measurePresets(columns(), engine(), sheet().types)
        .map(preset => [preset.label,
          preset.available ? () => commit(() => { sheets[active] = core.applyMeasurePreset(preset.id, sheet(), columns(), engine()); }) : null,
          {disabled: !preset.available, reason: preset.reason}]))]);
    }
    items.push(...typeMenuItems(anchor, field, settings));
    // Lane P: a place field starts a map layer (Tableau's "Add to New Layer").
    if (!settings.zone && verified() && globalThis.MentatMapStudio && MentatMapStudio.geographyOfField(field))
      items.push(['Map', null, {group: true}], ['Add to new layer', () => MentatMapStudio.addLayerFromField(field)]);
    items.push(['Place', null, {group: true}]);
    items.push(...ZONES.filter(([zone]) => !CARD_ZONES.includes(zone) || !(el(zone) && el(zone).closest('.studio-prop') && el(zone).closest('.studio-prop').hidden)).map(([zone, label]) => ['Send to ' + label, () => commit(() => {
      if (settings.zone) removeFrom(settings.zone, settings.position);
      place(zone, field.index, null, settings.aggregation);
    })]));
    if (settings.zone) items.push(['Remove from ' + settings.zone, () => commit(() => removeFrom(settings.zone, settings.position))]);
    popup(anchor, items);
  }
  function aggregationMenu(anchor, zone, position, list) {
    popup(anchor, (list || core.AGGREGATIONS).map(([id, label]) => [label, () => commit(() => {
      const pills = zonePills(zone);
      if (pills[position]) { pills[position].aggregation = id; setZonePills(zone, pills); }
    })]));
  }
  // The pills a shelf or mark property holds, as a list, and the way back.
  const MARK_LISTS = ['detail', 'tooltip'], MARK_ONE = ['color', 'size', 'label'];
  function zonePills(zone) {
    if (viaCards(zone)) return cardZonePills(zone);
    const state = sheet();
    if (zone === 'columns' || zone === 'rows') return core.shelf(state[zone]);
    if (MARK_LISTS.includes(zone)) return core.shelf(state.marks[zone]);
    if (MARK_ONE.includes(zone)) return core.shelf(state.marks[zone] == null ? [] : [state.marks[zone]]);
    if (zone === 'pages') return core.shelf(state.pages == null ? [] : [state.pages]);
    return [];
  }
  function setZonePills(zone, list) {
    if (viaCards(zone)) { setCardZonePills(zone, list); return; }
    const state = sheet();
    if (zone === 'columns' || zone === 'rows') state[zone] = list;
    else if (MARK_LISTS.includes(zone)) state.marks[zone] = list;
    else if (MARK_ONE.includes(zone)) state.marks[zone] = list[0] || null;
    else if (zone === 'pages') state.pages = list[0] || null;
  }


  /* ----------------------------------------------------------- shelf edits */

  function removeFrom(zone, position) {
    if (viaCards(zone)) { setCardZonePills(zone, cardZonePills(zone).filter((_, i) => i !== position)); return; }
    const state = sheet();
    if (zone === 'columns' || zone === 'rows') state[zone] = core.shelf(state[zone]).filter((_, i) => i !== position);
    else if (zone === 'filters') state.filters = state.filters.filter((_, i) => i !== position);
    else if (zone === 'detail' || zone === 'tooltip') state.marks[zone] = core.shelf(state.marks[zone]).filter((_, i) => i !== position);
    else if (zone === 'pages') { state.pages = null; state.pageMember = ''; }
    else state.marks[zone] = null;
  }
  // No shelf refuses a drop (census B-S25, B-S26): a field may sit on a shelf
  // more than once - SUM and AVG of one measure side by side - and a column may
  // carry several filters, which combine with AND like every other filter.
  function place(zone, column, position, aggregation) {
    const state = sheet();
    const entry = {column, aggregation: aggregation || 'sum'};
    // Marks v3: Shape, Path and Angle, and every encoding of a dual axis, live on the cards.
    if (viaCards(zone)) {
      const list = ['detail', 'tooltip'].includes(zone) ? cardZonePills(zone) : [];
      list.splice(position == null || position > list.length ? list.length : position, 0, entry);
      setCardZonePills(zone, list);
      if (zone === 'label') state.showLabels = true;
      return true;
    }
    if (zone === 'columns' || zone === 'rows' || zone === 'detail' || zone === 'tooltip') {
      const list = zone === 'columns' || zone === 'rows' ? core.shelf(state[zone]) : core.shelf(state.marks[zone]);
      const at = position == null || position > list.length ? list.length : position;
      list.splice(at, 0, entry);
      if (zone === 'columns' || zone === 'rows') state[zone] = list; else state.marks[zone] = list;
    } else if (zone === 'filters') {
      // A new filter starts as an empty choice, which selects nothing and
      // therefore filters nothing, instead of inheriting an arbitrary value.
      const chosen = core.fieldAt(core.classifyFields(columns(), state.types), column);
      state.filters = state.filters.concat(chosen && chosen.role === 'measure'
        ? {column, op: 'between', value: ['', '']}
        : {column, op: 'in', value: []});
    } else if (zone === 'pages') {
      state.pages = entry;
      state.pageMember = firstMember(column);
    } else {
      state.marks[zone] = entry;
      if (zone === 'label') state.showLabels = true;
    }
    return true;
  }
  // The facet query behind a member list, and the key that identifies it. A
  // field's own filter never narrows its own member list, so opening the
  // checklist shows every member the rest of the scope allows.
  function facetSpec(column) {
    const field = columns().find(f => f.index === column);
    if (!field || field.role !== 'dimension' || !dataset()) return null;
    const filters = [];
    for (const f of sheet().filters || []) {
      if (f.stage === 'aggregate') continue;
      const source = columns().find(c => c.index === Number(f.column));
      if (!source || source.index === column) continue;
      if (source.role === 'measure' && source.id !== 'amount') continue;
      try { filters.push(...contributions.clauses(f, source)); }
      catch { /* an unfinished filter does not narrow the member list */ }
    }
    const facet = {dataset: dataset().id, basis: basis(), dimensions: [field.id], measures: ['records'],
      filters: filters, aggregate_filters: [], sort: [{field: field.id, direction: 'asc'}], limit: 100000, offset: 0};
    return {key: JSON.stringify(facet), facet: facet, field: field};
  }
  function membersCached(column) {
    const spec = verified() ? facetSpec(column) : null;
    if (!spec) return null;
    const entry = memberCache.get(spec.key);
    return entry && !entry.error && entry.values.length ? entry.values : null;
  }
  const memberTotal = column => verified() ? (membersCached(column) || []).length : members(column).length;
  function members(column) {
    if (!data) return [];
    if(verified()){
      const spec=facetSpec(column);if(!spec)return [];
      const {key,facet,field}=spec;
      if(core.pill(sheet().pages)?.column===column)pageMembersKey=key;
      // A facet that failed is remembered for thirty seconds instead of being
      // deleted, because deleting it re-fired the same query from every later
      // render - the page-load query burst.
      const cached=memberCache.get(key);
      if(cached&&(!cached.error||Date.now()-cached.at<30000))return cached.values||[];
      memberCache.set(key,{values:(cached&&cached.values)||[],at:Date.now()});const selectedDataset=dataset().id;
      // A field can hold more members than one 100,000-group page; read every
      // page, as the CSV export does, so Pages and Select all miss none.
      (async()=>{const values=[];for(let offset=0;;){const page=await contributionReady('query',{...facet,offset},null,{current:()=>verified()&&dataset()?.id===selectedDataset});for(const r of page.rows)values.push(String(r[field.id]??''));if(!page.has_more||!page.rows.length)return values;offset+=page.rows.length;}})().then(values=>{
        memberCache.set(key,{values,at:Date.now()});
        if(verified()&&dataset()?.id===selectedDataset&&pageMembersKey===key&&core.pill(sheet().pages)?.column===column&&!values.includes(sheet().pageMember)){sheet().pageMember=values[0]||'';persist();}
        renderAll();
      }).catch(error=>{if(error.name==='AbortError'){memberCache.delete(key);return;}memberCache.set(key,{values:[],at:Date.now(),error:error.message});failure(error.message);});return [];
    }
    // Every distinct value: Pages steps through this list, the filter checklist
    // searches it and Select all copies it into the filter, so a cap drops members.
    // Remembered per rows array (a reload assigns a new one), so a render or a
    // keystroke in the filter search does not rescan every row.
    let lists = worksheetMembers.get(data.rows);
    if (!lists) worksheetMembers.set(data.rows, lists = new Map());
    if (lists.has(column)) return lists.get(column);
    const seen = new Set();
    for (const row of data.rows) {
      const value = String(row[column] == null ? '' : row[column]);
      if (value !== '') seen.add(value);
    }
    const list = [...seen].sort(new Intl.Collator(undefined, {numeric: true}).compare);
    lists.set(column, list);
    return list;
  }
  const firstMember = column => verified() ? String(data?.rows.find(row=>row[column]!=null&&row[column]!=='')?.[column]??'') : members(column)[0] || '';

  function dropTarget(node, zone) {
    node.dataset.zone = zone;
    node.addEventListener('dragover', event => {
      if (!dragging) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      node.classList.add('is-over');
    });
    node.addEventListener('dragleave', () => node.classList.remove('is-over'));
    node.addEventListener('drop', event => {
      event.preventDefault();
      node.classList.remove('is-over');
      if (!dragging) return;
      dropped = true;
      const move = dragging;
      dragging = null;
      const before = event.target.closest ? event.target.closest('.studio-pill') : null;
      let at = before && before.dataset.position != null && before.parentElement === node ? Number(before.dataset.position) : null;
      if (move.kind === 'analytics') { applyAnalytics(move.id); return; }
      // Filters are ANDed, so their order carries no meaning: a filter pill
      // dropped back onto the Filters shelf — onto itself, another filter or the
      // empty shelf — is a silent no-op. Origin is dragging.zone; the live drag
      // marker on the still-attached pill is a fallback, so this can never fall
      // through to the "already has a filter" refusal on the pill's own column.
      // A pill from Columns/Rows/marks is in another shelf, so its column is
      // still refused with the note below.
      if (zone === 'filters' && move.kind === 'pill' &&
          (move.zone === 'filters' || el('filters').querySelector('.studio-pill[data-dragging="true"]'))) return;
      if (move.kind === 'pill' && move.zone === zone) {
        if (at === move.position) return; // dropping a pill on itself is not an edit
        // The target position was read while the pill was still on this shelf,
        // so everything after it shifts down by one once the pill leaves.
        if (at != null && move.position < at) at -= 1;
      }
      commit(() => {
        const aggregation = move.zone ? (zonePills(move.zone)[move.position] || {}).aggregation : 'sum';
        if (move.kind === 'pill') removeFrom(move.zone, move.position);
        place(zone, move.column, at, aggregation);
      });
    });
  }

  /* -------------------------------------------------------------- rendering */

  /* ------------------------------------------------------- section folds */

  // Paul, 2026-09-23: "You should be able to expand each section individually
  // like with a census panel". Every section of the builder folds on its own
  // header the way the PAC evidence map's Census "Source details" panel does:
  // the header line stays, a disclosure chevron says which way it points, and
  // the rest folds away. The sections are the Dimensions and Measures buckets
  // and their More fields, each shelf (Columns, Rows, Filters), the Marks card
  // and each of its properties, and each Inspector section. Every one opens as
  // S1 draws it (More fields and the Inspector disclosures stay folded, as S1
  // has them); each choice is this browser's, remembered per section through
  // ui-state.js and written only when the reader makes it. The whole-pane
  // hides and "Expand chart" are unchanged, and a folded shelf gives its room
  // to the chart (ruling c).
  const FOLD_KEY = 'mentat-chart-studio-fold:';
  const foldOpen = new Map();
  const FOLD_DEFAULT_CLOSED = ['more-dimensions', 'more-measures', 'showme', 'map-settings', 'pages', 'params'];
  const FOLD_SUMMARY = new Map();
  function isFoldOpen(id) {
    if (!foldOpen.has(id)) {
      const stored = globalThis.MentatState?.recall?.(FOLD_KEY + id);
      foldOpen.set(id, stored === '1' ? true : stored === '0' ? false : !FOLD_DEFAULT_CLOSED.includes(id));
    }
    return foldOpen.get(id);
  }
  const foldStored = id => { const stored = globalThis.MentatState?.recall?.(FOLD_KEY + id); return stored === '1' || stored === '0'; };
  function rememberFold(id, open) {
    foldOpen.set(id, !!open);
    globalThis.MentatState?.remember?.(FOLD_KEY + id, open ? '1' : '0');
  }
  // A header's toggle: the label keeps its words, the chevron is drawn by CSS,
  // and a folded section says what it is holding beside its name.
  function foldButton(id, name, controls) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'studio-fold';
    button.dataset.fold = id;
    button.dataset.name = name;
    if (controls) button.setAttribute('aria-controls', controls);
    const label = document.createElement('span');
    label.className = 'studio-fold-label';
    label.textContent = name;
    const count = document.createElement('span');
    count.className = 'studio-fold-count';
    button.append(label, count);
    return button;
  }
  // Every fold on the page is drawn from its state: a host (data-fold-host)
  // takes is-folded, a body (data-fold-body) folds away, and a drop zone
  // (data-fold-drop) shrinks to one line that still names its fields and still
  // takes a drop. Built once or rebuilt by a render, a section only has to
  // carry the attributes.
  function applyFolds() {
    const root = document.getElementById('view-chart-studio');
    if (!root) return;
    for (const button of root.querySelectorAll('button.studio-fold[data-fold]')) {
      const id = button.dataset.fold, open = isFoldOpen(id), name = button.dataset.name || '';
      button.setAttribute('aria-expanded', String(open));
      button.title = (open ? 'Fold ' : 'Unfold ') + name + (open ? '; the rest of the view keeps its place' : '');
      const summary = FOLD_SUMMARY.get(id);
      const text = !open && summary ? summary() : '';
      const count = button.querySelector('.studio-fold-count');
      if (count) count.textContent = text;
      button.setAttribute('aria-label', name + (open ? ', expanded' : ', folded' + (text ? ': ' + text : '')));
    }
    for (const host of root.querySelectorAll('[data-fold-host]')) host.classList.toggle('is-folded', !isFoldOpen(host.dataset.foldHost));
    for (const body of root.querySelectorAll('[data-fold-body]')) body.classList.toggle('studio-folded', !isFoldOpen(body.dataset.foldBody));
    for (const drop of root.querySelectorAll('[data-fold-drop]')) {
      const folded = !isFoldOpen(drop.dataset.foldDrop);
      drop.classList.toggle('is-folded-drop', folded);
      const names = [...drop.querySelectorAll(':scope > .studio-pill .studio-pill-name')].map(node => node.textContent.trim()).filter(Boolean);
      drop.dataset.foldSummary = names.length ? names.join(', ') : 'Empty';
    }
    const studio = document.querySelector('#view-chart-studio .studio');
    if (studio) studio.classList.toggle('studio-shelf-folded', ['shelf-columns', 'shelf-rows', 'shelf-filters'].some(id => !isFoldOpen(id)));
  }
  function toggleFold(id) {
    const before = document.querySelector('#view-chart-studio .studio')?.classList.contains('studio-shelf-folded');
    rememberFold(id, !isFoldOpen(id));
    applyFolds();
    // A shelf that folds or unfolds changes the canvas's height; the observer
    // below redraws a roomy chart into it.
    const after = document.querySelector('#view-chart-studio .studio')?.classList.contains('studio-shelf-folded');
    if (before !== after) status(after ? 'A shelf is folded; the chart takes its room.' : 'Every shelf is showing.');
  }
  document.addEventListener('click', event => {
    const button = event.target instanceof Element ? event.target.closest('#view-chart-studio button.studio-fold[data-fold]') : null;
    if (!button) return;
    event.preventDefault();
    toggleFold(button.dataset.fold);
  });
  // A header that already exists in the markup keeps its element (and every
  // rule that styles it); only its words move into the toggle.
  function foldHeader(header, id, name, controls) {
    if (!header || header.querySelector('button.studio-fold')) return null;
    const button = foldButton(id, name, controls);
    header.replaceChildren(button);
    header.classList.add('studio-fold-head');
    return button;
  }
  // The Inspector's own disclosures (Show Me, Map layers, Pages, Parameters)
  // already fold; they now remember the reader's choice the same way. Only a
  // click on the summary is remembered, never an open the console makes for
  // the reader (a Pages field placed, a map chosen).
  function rememberDisclosure(details, id) {
    if (!details) return;
    if (foldStored(id)) details.open = isFoldOpen(id);
    const summary = details.querySelector(':scope > summary');
    let asked = false;
    summary?.addEventListener('click', () => { asked = true; });
    details.addEventListener('toggle', () => {
      if (!asked) return;
      asked = false;
      rememberFold(id, details.open);
    });
  }
  function buildFolds() {

    for (const [bucket, name] of [['dimensions', 'Dimensions'], ['measures', 'Measures']]) {
      const body = el(bucket);
      const header = body && body.previousElementSibling;
      if (header && header.tagName === 'H3' && foldHeader(header, bucket, name, 'studio-' + bucket)) {
        body.dataset.foldBody = bucket;
        FOLD_SUMMARY.set(bucket, () => {
          const n = el(bucket).querySelectorAll('.studio-pill').length;
          return n + ' field' + (n === 1 ? '' : 's');
        });
      }
    }
    for (const [zone, name] of [['columns', 'Columns'], ['rows', 'Rows'], ['filters', 'Filters']]) {
      const header = el(zone + '-name'), row = el(zone) && el(zone).closest('.studio-shelf');
      if (!header || !row || !foldHeader(header, 'shelf-' + zone, name, 'studio-' + zone)) continue;
      row.dataset.foldHost = 'shelf-' + zone;
      el(zone).dataset.foldDrop = 'shelf-' + zone;
    }
    // Marks v3 (#51): the section opens on the type strip, so it folds as
    // "Mark type" and takes the strip, the rows and any dual-axis cards with it.
    const props = el('marks-block') || document.querySelector('#studio-pane-marks .studio-mark-props');
    if (props && !el('marks-head')) {
      const head = document.createElement('h3');
      head.id = 'studio-marks-head';
      head.className = 'studio-section-head';
      props.before(head);
      props.id = props.id || 'studio-mark-props';
      foldHeader(head, 'marks', 'Mark type', props.id);
      props.dataset.foldBody = 'marks';
      FOLD_SUMMARY.set('marks', () => {
        const used = ENCODINGS.filter(([zone]) => el(zone) && el(zone).querySelector('.studio-pill')).map(([, label]) => label);
        return [stripLabel(currentType() === 'mixed' ? 'automatic' : currentType()), used.length ? used.join(', ') : 'no fields'].join(' \u00b7 ');
      });
    }
    for (const [zone, label] of ENCODINGS) {
      const drop = el(zone), prop = drop && drop.closest('.studio-prop'), header = prop && prop.querySelector(':scope > span');
      if (!header || header.querySelector('button.studio-fold')) continue;
      const button = foldButton('enc-' + zone, label, 'studio-' + zone);
      // A row's options button (Colour, Shape) stays its own control beside the fold.
      const options = [...header.children].filter(node => node.classList.contains('studio-prop-options'));
      button.querySelector('.studio-fold-label').before(...[...header.childNodes].filter(node => node.nodeType === 1 && !options.includes(node)));
      header.replaceChildren(...options, button);
      header.classList.add('studio-fold-head');
      prop.dataset.foldHost = 'enc-' + zone;
      drop.dataset.foldDrop = 'enc-' + zone;
    }
    const transform = el('param-period') && el('param-period').closest('label');
    const transformHead = transform && transform.previousElementSibling;
    if (transformHead && transformHead.tagName === 'H3' && foldHeader(transformHead, 'transform-params', 'Transform parameters')) {
      for (let node = transformHead.nextElementSibling; node; node = node.nextElementSibling) node.dataset.foldBody = 'transform-params';
      FOLD_SUMMARY.set('transform-params', () => {
        const period = el('param-period');
        return (period.selectedOptions[0] ? period.selectedOptions[0].textContent : '') + ' · ' + (el('param-horizon').value || 2) + ' forecast periods';
      });
    }
    const filterHead = el('filter-rows') && el('filter-rows').previousElementSibling;
    if (filterHead && filterHead.tagName === 'H3' && foldHeader(filterHead, 'filters-active', 'Active filters', 'studio-filter-rows')) {
      for (let node = filterHead.nextElementSibling; node; node = node.nextElementSibling) node.dataset.foldBody = 'filters-active';
      FOLD_SUMMARY.set('filters-active', () => {
        const n = (sheet().filters || []).length;
        return n ? n + ' filter' + (n === 1 ? '' : 's') : 'none';
      });
    }
    rememberDisclosure(el('showme-details'), 'showme');
    rememberDisclosure(el('map-settings'), 'map-settings');
    rememberDisclosure(el('pages-details'), 'pages');
    rememberDisclosure(el('params-details'), 'params');
  }
  const ENCODINGS = [['color', 'Colour'], ['size', 'Size'], ['label', 'Label'], ['detail', 'Detail'], ['tooltip', 'Tooltip'],
    ['shape', 'Shape'], ['path', 'Path'], ['angle', 'Angle']];

  // "More fields" is a section of its bucket too: folded as S1 draws it until
  // the reader opens it, and remembered from then on.
  const moreOpen = {dimensions: isFoldOpen('more-dimensions'), measures: isFoldOpen('more-measures')};
  function moreFold(bucket, pills, inUse) {
    const fold = document.createElement('details');
    fold.className = 'studio-more-fields';
    fold.dataset.bucket = bucket;
    fold.open = !!moreOpen[bucket];
    const summary = document.createElement('summary');
    summary.className = 'studio-more-summary' + (inUse ? ' is-used' : '');
    const label = document.createElement('span');
    label.className = 'studio-more-label';
    label.textContent = 'More fields';
    const count = document.createElement('span');
    count.className = 'studio-more-count';
    count.textContent = fmt(pills.length);
    summary.append(label, count);
    summary.setAttribute('aria-label', 'More fields: ' + fmt(pills.length) + ' more ' + bucket + (inUse ? ', some in use on this sheet' : ''));
    summary.title = fmt(pills.length) + ' more ' + bucket + (inUse ? ', some in use on this sheet' : '') + '. Search finds every field.';
    const list = document.createElement('div');
    list.className = 'studio-more-list';
    list.append(...pills);
    fold.append(summary, list);
    fold.addEventListener('toggle', () => {
      if (!fold.isConnected || fold.open === moreOpen[bucket]) return;
      moreOpen[bucket] = fold.open;
      rememberFold('more-' + bucket, fold.open);
    });
    return fold;
  }
  // S1 draws no "From" row above DIMENSIONS. The related-table choice keeps
  // its label and select (ids unchanged) in a small menu at the end of the
  // search box; the menu's button names the table in use and is tinted
  // while one table narrows the list.
  function buildFromMenu() {
    const label = el('field-table-label'), search = el('search') && el('search').closest('.studio-search');
    if (!label || !search || el('from-menu')) return;
    const row = document.createElement('div');
    row.className = 'studio-search-row';
    search.before(row);
    const menu = document.createElement('details');
    menu.id = 'studio-from-menu';
    menu.className = 'studio-from-menu';
    const summary = document.createElement('summary');
    summary.className = 'studio-from-button';
    const icon = document.createElementNS(NS, 'svg');
    icon.setAttribute('viewBox', '0 0 16 16');
    icon.setAttribute('aria-hidden', 'true');
    const funnel = document.createElementNS(NS, 'path');
    funnel.setAttribute('d', 'M2.5 3.5h11l-4.3 5v4.2l-2.4 1.1V8.5z');
    icon.append(funnel);
    summary.append(icon);
    const panel = document.createElement('div');
    panel.className = 'studio-from-panel';
    panel.append(label);
    menu.append(summary, panel);
    row.append(search, menu);
    menu.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !menu.open) return;
      event.preventDefault(); menu.open = false; summary.focus();
    });
  }
  function renderFromMenu(table) {
    const menu = el('from-menu');
    if (!menu) return;
    menu.hidden = !verified();
    if (menu.hidden) menu.open = false;
    const name = (verified() && table) || 'All related tables';
    const summary = menu.querySelector('summary');
    summary.setAttribute('aria-label', 'Fields from: ' + name);
    summary.title = 'Fields from: ' + name + '. Choose one related table to list only its fields.';
    menu.classList.toggle('is-active', name !== 'All related tables');
    el('field-table').title = name;
  }

  function renderFields() {
    const meta = core.classifyFields(columns(), sheet().types);
    el('field-table-label').hidden=!verified();const table=el('field-table').value;fill(el('field-table'),[['','All related tables'],...[...new Set(meta.fields.map(f=>f.table).filter(Boolean))].map(t=>[t,t])]);el('field-table').value=table;
    const query = el('search').value.trim().toLowerCase();
    const matches = field => (!verified()||!table||field.table===table)&&(!query || [field.name,field.id,field.table,field.data_type,field.description].filter(Boolean).join(' ').toLowerCase().includes(query));
    renderFromMenu(table);
    // S1: a 6px dot marks every field the sheet already uses, on any shelf.
    const used = usedColumns(sheet());
    const pillFor = f => {
      const pill = pillNode(f, {});
      if (used.has(f.index)) { pill.classList.add('is-used'); pill.setAttribute('aria-label', pill.getAttribute('aria-label') + ' In use on this sheet.'); }
      return pill;
    };
    // S1: a verified bucket opens on the board's own fields, in the board's
    // order, and folds the rest under "More fields". A search or a chosen
    // table lists every match instead, the board's fields first, so nothing
    // it finds is folded away. A worksheet's identifier columns (IDs, codes,
    // ZIPs) are listed too (census B-S21), under its Dimensions' More fields.
    const pane = (verified() && core.paneBuckets && contributions && contributions.PANE_FIELDS) || null;
    const into = (node, fields, bucket) => {
      const split = pane ? core.paneBuckets(fields, pane[bucket])
        : {shown: fields.filter(f => !f.isIdentifier || f.isTime), more: fields.filter(f => f.isIdentifier && !f.isTime)};
      if (query || table || !split.more.length) { node.replaceChildren(...[...split.shown, ...split.more].filter(matches).map(pillFor)); return; }
      const folded = split.more.map(pillFor);
      node.replaceChildren(...split.shown.map(pillFor),
        ...(folded.length ? [moreFold(bucket, folded, split.more.some(f => used.has(f.index)))] : []));
    };
    el('search').placeholder = data ? 'Search ' + fmt(columns().length) + ' fields' : 'Search fields';
    into(el('dimensions'), meta.dimensions, 'dimensions');
    into(el('measures'), meta.measures, 'measures');
  }

  function renderShelves() {
    const meta = core.classifyFields(columns(), sheet().types);
    const state = sheet();
    const shelfInto = (node, zone, list, placeholder) => {
      const pills = core.shelf(list)
        .map((p, position) => ({p, position, field: core.fieldAt(meta, p.column)}))
        .filter(item => item.field)
        .map(item => pillNode(item.field, {zone, position: item.position, aggregation: item.p.aggregation}));
      node.replaceChildren(...pills);
      node.dataset.placeholder = placeholder;
      node.dataset.empty = pills.length ? 'false' : 'true';
    };
    shelfInto(el('columns'), 'columns', state.columns, 'Drop a field here');
    shelfInto(el('rows'), 'rows', state.rows, 'Drop a field here');
    renderDualAxisChip();
    // Marks v3: every row reads the open card (one card: `marks`, as before).
    for (const zone of CARD_ZONES) if (el(zone)) shelfInto(el(zone), zone, zonePills(zone), 'Drop a field here');
    shelfInto(el('pages'), 'pages', state.pages == null ? [] : [state.pages], 'One dimension');
    const filterPills = state.filters.map((filter, position) => {
      const field = core.fieldAt(meta, Number(filter.column));
      if (!field) return null;
      const node = pillNode(field, {zone: 'filters', position});
      const summary = core.filterSummary(filter, field, memberTotal(field.index));
      node.querySelector('.studio-pill-name').textContent = summary;
      node.title = summary + ' \u00b7 Enter for actions, Delete to remove it';
      return node;
    }).filter(Boolean);
    el('filters').replaceChildren(...filterPills);
    el('filters').dataset.placeholder = 'Drop a field to filter';
    el('filters').dataset.empty = filterPills.length ? 'false' : 'true';
    el('mark').value = state.marks.type;
    const primary=[...core.shelf(state.rows),...core.shelf(state.columns)].find(p=>core.fieldAt(meta,p.column)?.role==='measure');
    const field=primary&&core.fieldAt(meta,primary.column);
    const calculated=verified()&&field&&field.id!=='amount';
    fill(el('aggregation'),calculated?[['sum','Source statistic']]:field?core.aggregationsFor(field,engine()):core.AGGREGATIONS);
    el('aggregation').value=primary?.aggregation||'sum';el('aggregation').disabled=!primary||calculated;
    el('aggregation').title=field?field.name+(calculated?' is already computed from source observations.':' · each other measure also has its own pill menu.'):'Add a measure to Rows or Columns.';
    // Lane F (#46): a Flow's Columns are its Stages, and Rows is put away while it is drawn.
    if (window.MentatStudioFlow) window.MentatStudioFlow.decorate({flow: !verified() && core.isFlowSheet(state),
      columns: el('columns'), rows: el('rows'), columnsName: el('columns-name'), mark: el('mark'),
      aggregation: el('aggregation'), fields: meta.fields});
    renderMarksCard();
    renderPager();
    renderShelfStrip();
    // Lane P: the pieces of the studio a map sheet changes (left pane switch,
    // Geography row), and their undoing on any other sheet.
    if (globalThis.MentatMapStudio) MentatMapStudio.sync();
  }

  /* ------------------------------------------------------ marks card (v3) */

  // Boards #50-#52 (approved 2026-09-25). The Marks tab: the type strip, the
  // rows the chosen mark can use (greyed with the reason where its renderer
  // cannot draw one, never hidden), the Colour and Shape popovers, and on a
  // dual axis an All card plus one card per measure. One card without v3
  // fields is still edited in `marks`, in place, so a v2 sheet keeps its bytes.
  const CARD_ZONES = ['color', 'size', 'label', 'detail', 'tooltip', 'shape', 'path', 'angle'];
  const V3_ZONES = ['shape', 'path', 'angle'];
  let expandedCard = 0;
  // Lane P (boards #47-#55): a map sheet's cards are its layers, edited one
  // open card at a time (map-studio.js lists them).
  const mapLayers = () => !!(globalThis.MentatMapStudio && globalThis.MentatMapStudio.handles(sheet()));
  const cardMode = state => core.dualAxisOn(state || sheet()) || (!!core.mapLayersOn && core.mapLayersOn(state || sheet()));
  function cardTarget() {
    if (!cardMode()) return 0;
    if (expandedCard === 'all') return 'all';
    const count = core.readCards(sheet()).length;
    return Math.min(Math.max(0, Number(expandedCard) || 0), count - 1);
  }
  const viaCards = zone => CARD_ZONES.includes(zone) && (cardMode() || V3_ZONES.includes(zone));
  function cardZonePills(zone) {
    return core.cardEncodingPills(core.readCards(sheet()), cardTarget(), zone)
      .filter(p => p && p.column != null).map(p => ({column: Number(p.column), aggregation: p.aggregation}));
  }
  function setCardZonePills(zone, list) {
    const cards = core.readCards(sheet());
    const current = zone === 'shape' ? core.cardEncodingPills(cards, cardTarget(), 'shape')[0] : null;
    const next = core.shelf(list).map(p => zone === 'shape' && current && current.glyphs && current.column === p.column
      ? Object.assign({}, p, {glyphs: current.glyphs}) : p);
    core.setCardEncoding(cards, cardTarget(), zone, next);
    core.writeCards(sheet(), cards);
  }
  // Every commit leaves the cards true to the sheet: a dual axis follows Rows,
  // and one card's v3 record takes the v2 fields `marks` holds.
  function syncMarks() {
    const state = sheet();
    if (!state) return;
    if (core.dualAxisOn(state)) core.syncCardsToRows(state, columns());
    else if ('marksCards' in state) core.writeCards(state, core.readCards(state));
  }
  function currentType() {
    const cards = core.readCards(sheet()), target = cardTarget();
    return target === 'all' ? core.allCardType(cards) : (cards[target] || cards[0]).type;
  }
  // Automatic shows the rows of the mark it resolves to (Tableau's rule).
  function resolvedType() {
    const type = currentType(), target = cardTarget();
    if (mapLayers()) return type;
    if (cardMode()) return type === 'mixed' ? 'bar' : core.dualCardMark(type, target === 'all' ? 0 : target, false);
    if (type !== 'automatic') return type;
    try {
      if (verified()) {
        const chosen = dataset();
        return chosen ? core.markOfEngineType(contributions.compile(sheet(), columns(), {dataset: chosen.id}).spec.type) : 'bar';
      }
      const report = core.compileReport(sheet(), columns());
      return report.config ? core.markOfEngineType(report.config.type) : 'bar';
    } catch { return 'bar'; }
  }
  function currentCard() {
    const cards = core.readCards(sheet()), target = cardTarget();
    return target === 'all' ? cards[0] : cards[target] || cards[0];
  }
  // A mark type from the strip, a card's menu or the Chart menu: on a dual axis
  // it is the open card's (All: every card's); otherwise today's gesture.
  function setMarkType(type) {
    commit(() => {
      if (cardMode()) {
        const cards = core.readCards(sheet());
        core.setCardType(cards, cardTarget(), type);
        core.writeCards(sheet(), cards);
        return;
      }
      // Lane F (#46): Flow takes the sheet's dimensions as its Stages and its measure on Size.
      if (core.flowSwitch(sheet(), columns(), type) && type === 'flow') return;
      sheet().marks.type = type;
      if (verified() && type === 'histogram' && columns().some(f => f.id === 'amount_bin')) {
        sheet().columns = [{column: columns().find(f => f.id === 'amount_bin').index, aggregation: 'sum'}];
        sheet().rows = [{column: columns().find(f => f.id === 'records').index, aggregation: 'sum'}];
        sheet().marks = {type, color: null, size: null, label: null, detail: [], tooltip: []};
        sheet().analytics.transform = 'none';
      }
      if (verified() && ['map', 'filled-map'].includes(type) && (type === 'filled-map' || !core.shelf([...sheet().columns, ...sheet().rows]).some(p => ['zip5', 'state'].includes(columns()[p.column]?.id)))) {
        sheet().columns = [{column: columns().find(f => f.id === (type === 'filled-map' ? 'state' : 'zip5')).index, aggregation: 'sum'}];
        sheet().rows = [{column: columns().find(f => f.id === 'amount').index, aggregation: 'sum'}];
        sheet().marks = {type, color: null, size: null, label: null, detail: [], tooltip: []};
        sheet().map = {...sheet().map, layers: [...new Set([...(sheet().map?.layers || ['districts']), 'states'])]};
        sheet().analytics.transform = 'none';
      }
      core.adoptMarks(sheet());
    });
  }
  // Tile icons (#51), drawn in the stroke style of every other studio icon.
  const MARK_ICONS = {
    automatic: [['path', {d: 'M8 2v3M8 11v3M2 8h3M11 8h3M3.8 3.8l2.1 2.1M10.1 10.1l2.1 2.1M12.2 3.8l-2.1 2.1M5.9 10.1l-2.1 2.1'}]],
    bar: [['path', {d: 'M3.5 13.5v-5M8 13.5v-11M12.5 13.5v-7.5'}]],
    line: [['path', {d: 'M2 12.5 6 7l3 3 5-6.5'}]],
    area: [['path', {d: 'M2 13.5V10l4-4.5 3 3 5-5v10z', fill: 'currentColor', 'fill-opacity': '.22'}]],
    square: [['rect', {x: 3.5, y: 3.5, width: 9, height: 9, rx: .8}]],
    circle: [['circle', {cx: 8, cy: 8, r: 5}]],
    shape: [['path', {d: 'M4.6 2.5 7 6.8H2.2z'}], ['circle', {cx: 11.5, cy: 4.6, r: 2.1}], ['rect', {x: 2.5, y: 9.5, width: 4.5, height: 4.5}], ['path', {d: 'M11.5 9v5M9 11.5h5'}]],
    text: [['path', {d: 'M3 3.5h10M8 3.5v10'}]],
    map: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M2.5 8h11M8 2.5c-2.4 2.6-2.4 8.4 0 11M8 2.5c2.4 2.6 2.4 8.4 0 11'}]],
    pie: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M8 8V2.5M8 8l4.8 2.7'}]],
    gantt: [['path', {d: 'M2.5 4h6.5M5 8h8.5M3.5 12h6', 'stroke-width': 2.2}]],
    polygon: [['path', {d: 'M3 5.5 8.2 2.5l5 3.5-1.8 7-7.6-1z'}]],
    density: [['circle', {cx: 8, cy: 8, r: 1.8, fill: 'currentColor'}], ['circle', {cx: 8, cy: 8, r: 4}], ['circle', {cx: 8, cy: 8, r: 6.2, 'stroke-dasharray': '1.6 1.6'}]],
    flow: [['path', {d: 'M2 4.5c5 0 6.5 7 12 7M2 11.5c5 0 6.5-7 12-7M2 8h12', 'stroke-width': 1.2}]]
  };
  function markIcon(type) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'studio-icon');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    for (const [tag, attributes] of MARK_ICONS[type] || MARK_ICONS.automatic) {
      const node = document.createElementNS(NS, tag);
      for (const name of Object.keys(attributes)) node.setAttribute(name, String(attributes[name]));
      svg.append(node);
    }
    return svg;
  }
  const stripLabel = id => (core.MARK_STRIP.find(m => m[0] === id) || [id, core.markLabel(id)])[1];
  function renderMarkStrip() {
    const strip = el('mark-strip');
    if (!strip) return;
    // Lane P: a map layer takes its type from its card's Mark type menu.
    strip.hidden = mapLayers();
    if (strip.hidden) return;
    const current = currentType(), dual = cardMode();
    const inStrip = core.MARK_STRIP.some(m => m[0] === current);
    strip.replaceChildren(...core.MARK_STRIP.map(([id, label]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'studio-mark-tile';
      button.dataset.mark = id;
      button.setAttribute('role', 'radio');
      const on = id === current;
      button.setAttribute('aria-checked', String(on));
      button.tabIndex = on || (!inStrip && id === 'automatic') ? 0 : -1;
      const reason = id === 'flow' ? core.flowReason(engine(), dual) : dual && !core.DUAL_MARKS.includes(id) ? core.DUAL_REASON : '';
      if (reason) { button.setAttribute('aria-disabled', 'true'); button.classList.add('is-unavailable'); }
      button.title = label + (reason ? ': ' + reason : '');
      button.setAttribute('aria-label', button.title);
      const caption = document.createElement('span');
      caption.textContent = id === 'automatic' ? 'Auto' : label;
      button.append(markIcon(id), caption);
      button.onclick = () => {
        if (reason) { status(reason.replace(/\.?$/, '.')); return; }
        if (!on) setMarkType(id);
      };
      return button;
    }));
    // A mark the Chart menu or Show Me chose that has no tile says what it is.
    strip.dataset.current = inStrip ? '' : current === 'mixed' ? 'Mixed' : core.markLabel(current);
    strip.title = inStrip ? '' : current === 'mixed' ? 'The cards draw different marks; a tile sets every card.' : core.markLabel(current) + ' is chosen in the Chart menu; a tile replaces it.';
  }
  // Arrow keys move through the strip, as through any radio group.
  document.addEventListener('keydown', event => {
    const tile = event.target instanceof Element ? event.target.closest('#studio-mark-strip .studio-mark-tile') : null;
    if (!tile || !['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const tiles = [...tile.parentElement.children], at = tiles.indexOf(tile);
    const step = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: 7, ArrowUp: -7}[event.key];
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tiles.length - 1 : Math.min(tiles.length - 1, Math.max(0, at + step));
    event.preventDefault();
    tiles.forEach((node, i) => { node.tabIndex = i === next ? 0 : -1; });
    tiles[next].focus();
  });
  const ROW_NOTE = {shape: ['Shape', 'Shape'], path: ['Path', 'Line, Area and Polygon'], angle: ['Angle', 'Pie']};
  const SIZE_MEANS = {gantt: 'Size sets each bar’s length.', density: 'Size sets each mark’s radius.', text: 'Size sets each value’s type size.'};
  function renderMarkRows() {
    // Lane P: a map layer's rows and reasons are the map engine's (map-layers.js).
    const layered = mapLayers(), type = resolvedType();
    const rows = layered ? MentatMapStudio.markRows(currentCard()) : core.markRows(type);
    const support = layered ? MentatMapStudio.encodingSupport(currentCard()) : core.encodingSupport(type, engine());
    for (const zone of CARD_ZONES) {
      const drop = el(zone), prop = drop && drop.closest('.studio-prop');
      if (!prop) continue;
      prop.hidden = !rows.includes(zone);
      const reason = support[zone] === true || support[zone] == null ? '' : support[zone];
      prop.classList.toggle('is-unavailable', !!reason);
      if (reason) { prop.setAttribute('aria-disabled', 'true'); prop.title = reason; drop.dataset.reasonShort = 'Not drawn for ' + stripLabel(type); }
      else { prop.removeAttribute('aria-disabled'); prop.title = zone === 'size' && SIZE_MEANS[type] ? SIZE_MEANS[type] : ''; delete drop.dataset.reasonShort; }
      for (const node of drop.querySelectorAll(':scope > .studio-pill')) {
        const field = fieldOf(node.dataset.column);
        const roleOk = field ? core.roleSupport(type, engine(), zone, field.role) : true;
        const why = reason || (roleOk === true ? '' : roleOk);
        node.classList.toggle('is-inert', !!why);
        if (why) { node.title = why + ' · ' + node.title; node.setAttribute('aria-label', node.getAttribute('aria-label') + ' ' + why); }
      }
    }
    const hidden = ['shape', 'path', 'angle'].filter(zone => !rows.includes(zone));
    el('mark-rows-note').textContent = hidden.length === 2 && hidden.includes('shape') && hidden.includes('angle')
      ? 'Shape and Angle appear for Shape and Pie marks.'
      : hidden.map(zone => ROW_NOTE[zone][0] + ' appears for ' + ROW_NOTE[zone][1] + ' marks').join('; ') + (hidden.length ? '.' : '');
    const styles = layered ? MentatMapStudio.styleSupport(currentCard()) : core.styleSupport(type), colourButton = el('color-options');
    if (colourButton) {
      const none = ['opacity', 'border', 'halo'].every(key => styles[key] !== true);
      colourButton.classList.toggle('is-unavailable', none);
      colourButton.title = none ? styles.opacity : 'Colour effects: opacity, border and halo';
    }
  }
  function renderMarkCards() {
    const host = el('mark-cards'), body = el('mark-card-body'), sync = el('mark-sync');
    if (!host || !body) return;
    // Lane P: a map sheet lists its cards as layers (map-studio.js).
    if (mapLayers()) { MentatMapStudio.renderLayerCards(); return; }
    if (!cardMode()) {
      host.hidden = true;
      if (body.parentElement !== host.parentElement) host.after(body);
      host.replaceChildren();
      if (sync) sync.hidden = true;
      return;
    }
    const cards = core.readCards(sheet()), meta = core.classifyFields(columns(), sheet().types), target = cardTarget();
    const entries = [['all', 'All', core.allCardType(cards)],
      ...cards.map((card, i) => { const field = core.fieldAt(meta, card.measure.column); return [i, field ? fieldLabel(field, card.measure.aggregation) : 'Measure ' + (i + 1), card.type]; })];
    host.hidden = false;
    host.replaceChildren(...entries.map(([key, name, type]) => {
      const open = key === target, item = document.createElement('div'), head = document.createElement('div');
      item.className = 'studio-mark-card' + (open ? ' is-open' : '');
      item.setAttribute('role', 'listitem');
      item.dataset.card = String(key);
      head.className = 'studio-mark-card-head';
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'studio-mark-card-toggle';
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-controls', 'studio-mark-card-body');
      const label = document.createElement('span');
      label.textContent = name;
      toggle.append(label);
      toggle.onclick = () => { if (open) return; expandedCard = key; renderShelves(); applyFolds(); };
      head.append(toggle);
      const typeText = type === 'mixed' ? 'Mixed' : stripLabel(type);
      if (open) {
        const tag = document.createElement('span');
        tag.className = 'studio-mark-card-type';
        tag.append(markIcon(type === 'mixed' ? 'automatic' : type), document.createTextNode(typeText));
        head.append(tag);
      } else {
        // A folded card reads its own type, and can change it without opening (#52).
        const select = document.createElement('select');
        select.className = 'studio-mark-card-select';
        select.setAttribute('aria-label', 'Mark type of ' + name);
        const options = core.DUAL_MARKS.map(id => [id, stripLabel(id)]);
        if (type === 'mixed') options.unshift(['mixed', 'Mixed']);
        fill(select, options);
        if (type === 'mixed') select.options[0].disabled = true;
        select.value = type;
        select.onchange = () => {
          commit(() => { const next = core.readCards(sheet()); core.setCardType(next, key, select.value); core.writeCards(sheet(), next); });
        };
        head.append(select);
      }
      item.append(head);
      if (open) item.append(body);
      return item;
    }));
    if (sync) { sync.hidden = false; el('sync-axes').checked = sheet().syncAxes === true; }
  }
  function renderMarksCard() {
    renderMarkCards();
    renderMarkStrip();
    renderMarkRows();
    // Lane F (#46): Flow options (ribbon colour, node order, opacity, the top N) while a flow is drawn.
    if (window.MentatStudioFlow) {
      const flowCard = !verified() && core.isFlowSheet(sheet()) ? core.readCards(sheet())[0] : null;
      const colour = flowCard && flowCard.encodings.color
        ? core.fieldAt(core.classifyFields(columns(), sheet().types), flowCard.encodings.color.column) : null;
      window.MentatStudioFlow.options({host: el('mark-card-body'), flow: !!flowCard, options: flowCard && flowCard.flow,
        colourName: colour && colour.role === 'dimension' ? colour.name : '',
        update: patch => commit(() => core.setFlowOptions(sheet(), patch))});
    }
  }
  // The Dual axis chip on Rows (#52): two or more measures there can share one
  // pane, each with its own marks card.
  function renderDualAxisChip() {
    const rows = el('rows');
    if (!rows) return;
    rows.querySelector(':scope > .studio-dual-axis')?.remove();
    if (core.measuresOnRows(sheet(), columns()).length < 2) return;
    const on = cardMode(), chip = document.createElement('button');
    chip.type = 'button';
    chip.id = 'studio-dual-axis';
    chip.className = 'studio-dual-axis';
    chip.textContent = 'Dual axis';
    chip.setAttribute('aria-pressed', String(on));
    chip.title = on ? 'Turn the dual axis off: the measures go back to one marks card.'
      : 'Draw every measure on Rows in one pane, each with its own marks card.';
    chip.onclick = event => {
      event.stopPropagation();
      expandedCard = 0;
      commit(() => core.setDualAxis(sheet(), columns(), !cardMode()));
      status(cardMode() ? 'Dual axis on: each measure has its own marks card.' : 'Dual axis off: one marks card draws every measure.');
    };
    const pills = [...rows.querySelectorAll(':scope > .studio-pill')];
    const first = pills.find(node => (fieldOf(node.dataset.column) || {}).role === 'measure');
    if (first) first.after(chip); else rows.append(chip);
  }

  // The Colour popover (Tableau's, #50): Opacity, Border and Halo of the open
  // card (All: every card), each greyed with its reason where the mark
  // cannot take it. The Shape palette: one shape for every mark, or a shape
  // per member of the field on Shape.
  function closePopovers() {
    document.querySelectorAll('.studio-popover').forEach(node => node.remove());
    for (const id of ['color-options', 'shape-options']) el(id)?.setAttribute('aria-expanded', 'false');
  }
  function popover(anchor, label) {
    closePopovers();
    const pop = document.createElement('div');
    pop.className = 'studio-popover';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', label);
    const heading = document.createElement('h4');
    heading.textContent = label;
    pop.append(heading);
    anchor.setAttribute('aria-expanded', 'true');
    const box = anchor.getBoundingClientRect();
    document.body.append(pop);
    const width = pop.getBoundingClientRect().width || 292;
    pop.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, Math.round(box.left + window.scrollX - width + box.width))) + 'px';
    pop.style.top = Math.round(box.bottom + window.scrollY + 6) + 'px';
    const outside = event => { if (!pop.contains(event.target) && event.target !== anchor && !anchor.contains(event.target)) dismiss(); };
    const escape = event => { if (event.key === 'Escape') { dismiss(); anchor.focus(); } };
    const dismiss = () => { pop.remove(); anchor.setAttribute('aria-expanded', 'false'); document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape); };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    pop.dismiss = dismiss;
    return pop;
  }
  // Places a popover under its anchor again once its contents are drawn.
  function placePopover(pop, anchor) {
    const box = anchor.getBoundingClientRect(), rect = pop.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(window.innerWidth - rect.width - 8, Math.round(box.left + window.scrollX))) + 'px';
    const below = window.innerHeight - box.bottom;
    pop.style.top = (rect.height > below - 12 && box.top > below ? Math.max(8, Math.round(box.top + window.scrollY - rect.height - 6)) : Math.round(box.bottom + window.scrollY + 6)) + 'px';
    pop.querySelector('input:not(:disabled), button:not(:disabled)')?.focus();
  }
  const STYLE_NAMES = {automatic: 'Auto', none: 'None', ink: 'Ink', white: 'White'};
  function styleSwatch(value) {
    if (value === 'ink') return getComputedStyle(document.documentElement).getPropertyValue('--chart-ink').trim() || '#202522';
    if (value === 'white') return '#ffffff';
    const slot = /^c(\d+)$/.exec(value);
    return slot && globalThis.MentatTheme?.categoryColour ? globalThis.MentatTheme.categoryColour(Number(slot[1]) - 1) : '';
  }
  function applyCardStyleChoice(style, reopen) {
    commit(() => {
      const cards = core.readCards(sheet());
      core.setCardStyle(cards, cardTarget(), style);
      core.writeCards(sheet(), cards);
    });
    if (reopen) openColourPopover(el('color-options'));
  }
  function openColourPopover(anchor) {
    if (!anchor) return;
    const type = resolvedType(), styles = mapLayers() ? MentatMapStudio.styleSupport(currentCard()) : core.styleSupport(type), card = currentCard();
    const pop = popover(anchor, 'Colour effects' + (cardMode() && cardTarget() === 'all' ? ' · every card' : ''));
    const reasonOf = key => styles[key] === true ? '' : styles[key];
    const opacityRow = document.createElement('label');
    opacityRow.className = 'studio-pop-row';
    opacityRow.append(document.createTextNode('Opacity'));
    const range = document.createElement('input');
    range.type = 'range'; range.min = '0'; range.max = '100'; range.step = '5';
    range.id = 'studio-colour-opacity';
    range.value = String(card.opacity);
    range.setAttribute('aria-label', 'Opacity');
    const out = document.createElement('output');
    out.setAttribute('for', 'studio-colour-opacity');
    out.textContent = card.opacity + '%';
    range.oninput = () => { out.textContent = range.value + '%'; };
    range.onchange = () => applyCardStyleChoice({opacity: Number(range.value)}, true);
    if (reasonOf('opacity')) { range.disabled = true; range.title = reasonOf('opacity'); }
    opacityRow.append(range, out);
    pop.append(opacityRow);
    for (const [key, label] of [['border', 'Border'], ['halo', 'Halo']]) {
      const caption = document.createElement('p');
      caption.className = 'studio-pop-label';
      caption.textContent = label;
      const row = document.createElement('div');
      row.className = 'studio-swatches';
      row.setAttribute('role', 'group');
      row.setAttribute('aria-label', label);
      for (const value of core.STYLE_COLOURS) {
        const button = document.createElement('button');
        button.type = 'button';
        const name = STYLE_NAMES[value] || 'Theme colour ' + value.slice(1);
        if (STYLE_NAMES[value]) button.textContent = name;
        else { button.classList.add('is-swatch'); button.style.backgroundColor = styleSwatch(value); }
        if (value === 'white') button.style.backgroundColor = '#ffffff';
        button.setAttribute('aria-label', label + ': ' + name);
        button.title = label + ': ' + name;
        button.setAttribute('aria-pressed', String(card[key] === value));
        button.dataset.value = value;
        if (reasonOf(key)) { button.disabled = true; button.title = reasonOf(key); }
        button.onclick = () => applyCardStyleChoice({[key]: value}, true);
        row.append(button);
      }
      pop.append(caption, row);
      if (reasonOf(key)) {
        const why = document.createElement('p');
        why.className = 'studio-pop-reason';
        why.textContent = reasonOf(key);
        pop.append(why);
      }
    }
    if (reasonOf('opacity')) {
      const why = document.createElement('p');
      why.className = 'studio-pop-reason';
      why.textContent = reasonOf('opacity');
      pop.insertBefore(why, opacityRow);
    }
    placePopover(pop, anchor);
  }
  const glyphName = id => {
    const base = id.replace(/-open$/, '').replace('-', ' ');
    return base.charAt(0).toUpperCase() + base.slice(1) + (id.endsWith('-open') ? ', outline' : ', filled');
  };
  function glyphIcon(id) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', contributionPlot.glyphD(id, 8, 8, 5));
    path.setAttribute('fill', id.endsWith('-open') ? 'none' : 'currentColor');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', id.endsWith('-open') ? '1.4' : '0.6');
    svg.append(path);
    return svg;
  }
  function applyShape(value, reopenWith) {
    commit(() => {
      const cards = core.readCards(sheet());
      core.setCardEncoding(cards, cardTarget(), 'shape', value ? [value] : []);
      core.writeCards(sheet(), cards);
    });
    openShapePalette(el('shape-options'), reopenWith);
  }
  function openShapePalette(anchor, chosenMember) {
    if (!anchor || !contributionPlot || typeof contributionPlot.glyphD !== 'function') return;
    const card = currentCard(), shape = card.encodings.shape;
    const field = shape && shape.column != null ? fieldOf(shape.column) : null;
    const pop = popover(anchor, 'Shape palette');
    let member = chosenMember || null;
    // The members the chart draws (the verified answer on screen), else the
    // field's member list; sorted as the renderer sorts its shape legend.
    const drawn = field && verified() && contributionResult ? [...new Set((contributionResult.rows || []).map(row => row[field.id])
      .filter(value => value != null && value !== '').map(String))].sort((a, b) => a.localeCompare(b)) : [];
    const memberList = field ? (drawn.length ? drawn : members(field.index)).slice(0, 20) : [];
    const glyphOf = (value, index) => core.glyphFor(shape, value, index);
    if (field) {
      const caption = document.createElement('p');
      caption.className = 'studio-pop-label';
      caption.textContent = memberList.length ? 'Choose a member of ' + field.name + ', then its shape.' : 'The members of ' + field.name + ' are loading.';
      const list = document.createElement('div');
      list.className = 'studio-glyph-members';
      list.setAttribute('role', 'group');
      list.setAttribute('aria-label', field.name + ' members');
      memberList.forEach((value, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('aria-pressed', String(value === member));
        button.append(glyphIcon(glyphOf(value, index)), document.createTextNode(value));
        button.onclick = () => { member = value; for (const node of list.children) node.setAttribute('aria-pressed', String(node === button)); };
        list.append(button);
      });
      pop.append(caption, list);
    }
    const grid = document.createElement('div');
    grid.className = 'studio-glyphs';
    grid.setAttribute('role', 'group');
    grid.setAttribute('aria-label', 'Shapes');
    const current = shape && shape.glyph ? shape.glyph : '';
    for (const id of core.GLYPHS) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.glyph = id;
      button.setAttribute('aria-label', glyphName(id));
      button.title = glyphName(id);
      button.setAttribute('aria-pressed', String(!field && id === current));
      button.append(glyphIcon(id));
      button.onclick = () => {
        if (!field) { applyShape({glyph: id}); return; }
        if (!member) { status('Choose a member of ' + field.name + ' first, then its shape.'); return; }
        const glyphs = Object.assign({}, shape.glyphs || {}, {[member]: id});
        applyShape({column: shape.column, aggregation: shape.aggregation, glyphs}, member);
      };
      grid.append(button);
    }
    pop.append(grid);
    const actions = document.createElement('div');
    actions.className = 'studio-pop-actions';
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.textContent = field ? 'Palette order' : 'Automatic';
    reset.title = field ? 'Every member takes the palette in order' : 'Every mark takes the first shape, a filled circle';
    reset.onclick = () => applyShape(field ? {column: shape.column, aggregation: shape.aggregation} : null);
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = 'Done';
    close.onclick = () => pop.dismiss();
    actions.append(reset, close);
    pop.append(actions);
    placePopover(pop, anchor);
  }

  function renderPager() {
    const state = sheet();
    const pill = core.pill(state.pages);
    // A pager inside a folded disclosure is a control nobody can find, so
    // placing a Pages field opens it once.
    const pagerWasHidden = el('pager').hidden;
    el('pager').hidden = !pill;
    if (pill && pagerWasHidden && el('pages-details')) el('pages-details').open = true;
    if (!pill) { stopPlay(); return; }
    const list = members(pill.column);
    const at = Math.max(0, list.indexOf(state.pageMember));
    el('pager-label').textContent = list.length ? (at + 1) + ' of ' + list.length + ' · ' + state.pageMember : 'No members';
    el('pager-prev').disabled = !list.length;
    el('pager-next').disabled = !list.length;
  }
  function stepPage(delta) {
    const pill = core.pill(sheet().pages);
    if (!pill) return;
    const list = members(pill.column);
    if (!list.length) return;
    const at = (list.indexOf(sheet().pageMember) + delta + list.length) % list.length;
    commit(() => { sheet().pageMember = list[at]; });
  }
  function stopPlay() {
    if (pageTimer) clearInterval(pageTimer);
    pageTimer = 0;
    el('pager-play').setAttribute('aria-pressed', 'false');
    el('pager-play').textContent = '▶';
  }
  function togglePlay() {
    if (pageTimer) { stopPlay(); return; }
    pageTimer = setInterval(() => stepPage(1), 1400);
    el('pager-play').setAttribute('aria-pressed', 'true');
    el('pager-play').textContent = '❚❚';
  }

  /* --------------------------------------------------------------- show me */

  const GLYPHS = {
    'text-table': 'table', 'heat-map': 'grid', 'highlight-table': 'grid', 'symbol-map': 'map', 'filled-map': 'map',
    'pie-chart': 'pie', 'horizontal-bars': 'barsh', 'stacked-bars': 'stack', 'side-by-side-bars': 'side',
    'treemap': 'tree', 'circle-views': 'circles', 'side-by-side-circles': 'circles2', 'lines-continuous': 'line',
    'lines-discrete': 'linestep', 'dual-lines': 'line2', 'area-continuous': 'area', 'area-discrete': 'area',
    'dual-combination': 'combo', 'scatter-plot': 'scatter', 'histogram': 'hist', 'box-and-whisker': 'box',
    'gantt': 'gantt', 'bullet-graph': 'bullet', 'packed-bubbles': 'bubbles'
  };
  const SHAPES = {
    table: [['r', 2, 3, 28, 5], ['r', 2, 10, 28, 5], ['r', 2, 17, 28, 5]],
    grid: [['r', 2, 3, 8, 8], ['r', 12, 3, 8, 8], ['r', 22, 3, 8, 8], ['r', 2, 13, 8, 8], ['r', 12, 13, 8, 8], ['r', 22, 13, 8, 8]],
    map: [['p', '4,16 10,5 18,9 24,4 29,14 20,21 9,20']],
    pie: [['c', 16, 12, 10], ['l', 16, 12, 16, 2], ['l', 16, 12, 25, 17]],
    barsh: [['r', 2, 3, 26, 4], ['r', 2, 10, 18, 4], ['r', 2, 17, 10, 4]],
    stack: [['r', 4, 10, 7, 12], ['r', 4, 4, 7, 6], ['r', 14, 13, 7, 9], ['r', 14, 6, 7, 7], ['r', 24, 8, 5, 14]],
    side: [['r', 3, 9, 4, 13], ['r', 8, 5, 4, 17], ['r', 15, 12, 4, 10], ['r', 20, 7, 4, 15], ['r', 26, 10, 4, 12]],
    tree: [['r', 2, 3, 16, 12], ['r', 19, 3, 11, 7], ['r', 19, 11, 11, 4], ['r', 2, 16, 9, 6], ['r', 12, 16, 18, 6]],
    circles: [['c', 6, 17, 3], ['c', 13, 10, 3], ['c', 20, 14, 3], ['c', 27, 6, 3]],
    circles2: [['c', 6, 17, 3], ['c', 11, 8, 3], ['c', 19, 15, 3], ['c', 24, 6, 3]],
    line: [['pl', '2,20 9,12 16,15 23,6 30,9']],
    linestep: [['pl', '2,20 9,20 9,12 16,12 16,15 23,15 23,6 30,6']],
    line2: [['pl', '2,20 9,12 16,15 23,6 30,9'], ['pl', '2,13 9,17 16,8 23,14 30,4']],
    area: [['p', '2,21 2,14 9,8 16,13 23,5 30,10 30,21']],
    combo: [['r', 3, 12, 5, 10], ['r', 12, 8, 5, 14], ['r', 21, 14, 5, 8], ['pl', '5,9 14,4 23,11 29,6']],
    scatter: [['c', 5, 18, 2], ['c', 11, 11, 2], ['c', 15, 16, 2], ['c', 21, 7, 2], ['c', 26, 12, 2], ['c', 9, 6, 2]],
    hist: [['r', 3, 15, 4, 7], ['r', 8, 9, 4, 13], ['r', 13, 4, 4, 18], ['r', 18, 8, 4, 14], ['r', 23, 14, 4, 8]],
    box: [['l', 8, 4, 8, 20], ['r', 4, 8, 9, 8], ['l', 22, 6, 22, 21], ['r', 18, 10, 9, 6]],
    gantt: [['r', 3, 4, 12, 4], ['r', 9, 10, 14, 4], ['r', 16, 16, 12, 4]],
    bullet: [['r', 2, 9, 28, 7], ['r', 2, 9, 17, 7], ['l', 23, 6, 23, 19]],
    bubbles: [['c', 10, 13, 7], ['c', 22, 9, 5], ['c', 23, 19, 3], ['c', 5, 5, 3]]
  };
  function glyphSvg(kind) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 32 24');
    svg.setAttribute('aria-hidden', 'true');
    for (const shape of SHAPES[kind] || SHAPES.table) {
      let node;
      if (shape[0] === 'r') {
        node = document.createElementNS(NS, 'rect');
        node.setAttribute('x', shape[1]); node.setAttribute('y', shape[2]);
        node.setAttribute('width', shape[3]); node.setAttribute('height', shape[4]);
        node.setAttribute('fill', 'currentColor');
      } else if (shape[0] === 'c') {
        node = document.createElementNS(NS, 'circle');
        node.setAttribute('cx', shape[1]); node.setAttribute('cy', shape[2]); node.setAttribute('r', shape[3]);
        node.setAttribute('fill', 'currentColor');
      } else if (shape[0] === 'l') {
        node = document.createElementNS(NS, 'line');
        node.setAttribute('x1', shape[1]); node.setAttribute('y1', shape[2]);
        node.setAttribute('x2', shape[3]); node.setAttribute('y2', shape[4]);
        node.setAttribute('stroke', 'currentColor'); node.setAttribute('stroke-width', '1.6');
      } else {
        node = document.createElementNS(NS, shape[0] === 'p' ? 'polygon' : 'polyline');
        node.setAttribute('points', shape[1]);
        node.setAttribute('fill', shape[0] === 'p' ? 'currentColor' : 'none');
        node.setAttribute('stroke', 'currentColor');
        node.setAttribute('stroke-width', '1.6');
      }
      svg.append(node);
    }
    return svg;
  }

  function renderShowMe() {
    // Show Me reads the shelves. A click now places a field instead of marking
    // it, so there is no separate selection for a tile to read.
    const selectionCounts = core.showMeSelection(sheet(), columns(), []);
    const tiles = verified()?contributions.showMeEligibility(selectionCounts):core.showMeEligibility(selectionCounts);
    const recommended = data ? core.recommendedTile(columns()) : '';
    el('showme-grid').replaceChildren(...tiles.map(tile => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'studio-tile' + (tile.id === recommended ? ' is-recommended' : '');
      // Paul, 2026-09-23: "...all graph types properly register and nothing is
      // logically constrained". Every tile is a real mark and is enabled
      // whenever a source is loaded; what it prefers and what a click will pick
      // for it are in its title and in the foot line.
      button.disabled = !data;
      button.dataset.tile = tile.id;
      if (tile.mark) button.dataset.mark = tile.mark;
      const caption = document.createElement('span');
      caption.textContent = tile.label;
      button.append(glyphSvg(GLYPHS[tile.id]), caption);
      const detail = tile.requirement + (tile.note ? ' · ' + tile.note : '') + (tile.fill ? ' · ' + tile.fill + '.' : '');
      button.title = tile.label + ': ' + detail;
      button.setAttribute('aria-label', button.title);
      const describe = () => {
        hoverTile = tile.id;
        el('showme-foot').textContent = tile.label + ' — ' + detail;
      };
      button.addEventListener('mouseenter', describe);
      button.addEventListener('focus', describe);
      button.onclick = () => commit(() => {
        if(verified()&&['symbol-map','filled-map'].includes(tile.id)&&!sheet().columns.length&&!sheet().rows.length){
          const filled=tile.id==='filled-map';sheet().columns=[{column:columns().find(f=>f.id===(filled?'state':'zip5')).index,aggregation:'sum'}];sheet().rows=[{column:columns().find(f=>f.id==='amount').index,aggregation:'sum'}];sheet().marks={type:filled?'filled-map':'map',color:null,size:null,label:null,detail:[],tooltip:[]};sheet().analytics.transform='none';core.adoptMarks(sheet());return;
        }
        const next = (verified()?contributions:core).showMeApply(tile.id, sheet(), columns(), []);
        sheets[active] = next;
      });
      return button;
    }));
    if (!hoverTile) el('showme-foot').textContent = recommended
      ? 'Suggested from the scanned columns: ' + (tiles.find(t => t.id === recommended) || {}).label
      : 'Choose fields to see what fits.';
    // The suggestion also rides on the closed disclosure's own summary, so a
    // reader can see what fits without opening it.
    const hint = el('showme-hint');
    if (hint) hint.textContent = recommended
      ? ' · suggested: ' + ((tiles.find(t => t.id === recommended) || {}).label || '')
      : '';
  }

  /* ------------------------------------------------------------- analytics */

  // An analytics object that cannot change this view must say so rather than
  // toggle and draw nothing. The reasons are the renderers' own limits: the
  // verified path draws reference lines only in contribution-plot.js, and a
  // grand total there is a stated figure, not a mark.
  // Paul, 2026-09-23: "nothing is logically constrained" (census B-S18). Every
  // switch works on every view; where a view cannot show one - a reference
  // line on a chart with no value axis, a period analysis with no date on the
  // shelves - the note under the chart says so, and the switch stays on.
  function analyticsRefusal(item) {
    return data ? '' : 'Choose a data source first.';
  }

  function analyticsRow(item, state, role) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'studio-analytics-item';
    const refusal = analyticsRefusal(item);
    button.draggable = !refusal;
    button.disabled = !!refusal;
    const on = item.group === 'transform' ? state.analytics.transform === item.id : !!state.analytics[item.id];
    button.setAttribute('role', role);
    button.setAttribute('aria-checked', String(on));
    const text = document.createElement('span');
    text.className = 'studio-analytics-text';
    const title = document.createElement('span');
    title.textContent = item.label;
    const hint = document.createElement('small');
    hint.textContent = refusal || item.hint;
    text.append(title, hint);
    const knob = document.createElement('span');
    knob.className = 'studio-switch';
    knob.setAttribute('aria-hidden', 'true');
    if (refusal) button.title = refusal;
    button.append(text, knob);
    button.onclick = () => applyAnalytics(item.id);
    button.addEventListener('dragstart', event => {
      dragging = {kind: 'analytics', id: item.id};
      dropped = false;
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData('text/plain', item.label);
    });
    button.addEventListener('dragend', () => { dragging = null; });
    return button;
  }

  function renderAnalytics() {
    const state = sheet();
    const host = el('analytics-list');
    host.replaceChildren();
    // One period analysis at a time; the reference lines are independent.
    for (const [kind, heading, role] of [['transform', 'Period analysis', 'radio'], ['flag', 'Summarize', 'switch']]) {
      // Each group folds on its heading like every other section.
      const id = kind === 'transform' ? 'analytics-period' : 'analytics-summarize';
      const label = document.createElement('p');
      label.className = 'studio-analytics-group studio-fold-head';
      label.append(foldButton(id, heading, 'studio-' + id));
      FOLD_SUMMARY.set(id, () => ANALYTICS.filter(a => a.group === kind &&
        (kind === 'transform' ? sheet().analytics.transform === a.id : !!sheet().analytics[a.id])).map(a => a.label).join(', ') || 'none on');
      const box = document.createElement('div');
      box.id = 'studio-' + id;
      box.dataset.foldBody = id;
      if (role === 'radio') {
        box.setAttribute('role', 'radiogroup');
        box.setAttribute('aria-label', heading);
      }
      for (const item of ANALYTICS.filter(a => a.group === kind)) box.append(analyticsRow(item, state, role));
      host.append(label, box);
    }
  }
  function applyAnalytics(id) {
    const item = ANALYTICS.find(a => a.id === id);
    if (!item) return;
    commit(() => {
      const analytics = sheet().analytics;
      if (item.group === 'transform') analytics.transform = analytics.transform === id ? 'none' : id;
      else analytics[id] = !analytics[id];
    });
  }

  /* ------------------------------------------------------- company overview */

  // Four preset views of one company. No iframes: each card compiles a preset
  // sheet, runs the ordinary verified query and paints it with the same
  // renderers the builder uses, so a card and its builder view are one view.
  function overviewCycle() {
    const field = columns().find(f => f.id === 'cycle');
    if (!field) return '';
    const filter = (sheet().filters || []).find(f => Number(f.column) === field.index);
    if (!filter || !core.filterActive(filter)) return '';
    const values = core.filterValues(filter);
    return (filter.op === 'equals' || filter.op === 'in') && values.length === 1 ? values[0] : '';
  }
  function renderOverviewControls() {
    const toggle = el('overview-toggle');
    const reason = !verified() ? 'Company overview uses verified contributions.'
      : !data || !dataset() ? 'Choose a verified report source first.'
      : !columns().some(f => f.id === 'company') ? 'This source has no company field.' : '';
    toggle.disabled = !!reason;
    toggle.title = reason || 'Four preset views of one company';
    if (reason) overviewMode = false;
    syncViewMode();
    el('overview').hidden = !overviewMode;
    el('grid').hidden = dataSourceTab || overviewMode;
    const host = document.querySelector('#view-chart-studio .studio');
    if (host) host.classList.toggle('studio-overview-mode', overviewMode);
  }
  function renderOverview() {
    const field = columns().find(f => f.id === 'company');
    if (!field) return;
    const list = members(field.index);
    const carried = (appliedScope && appliedScope.company) || '';
    if (!overviewCompany || (list.length && list.indexOf(overviewCompany) === -1)) {
      overviewCompany = list.indexOf(carried) !== -1 ? carried : list[0] || '';
    }
    fill(el('overview-company'), list.length ? list.map(m => [m, m]) : [['', 'Reading companies\u2026']]);
    el('overview-company').value = overviewCompany;
    const cycle = overviewCycle();
    // FIX 4: the four cards read this company (and cycle) of this report; a short cell among them is named here.
    const gap = overviewCompany && dataset() ? gapNotice([{dataset: dataset().id, filters: [
      {field: 'company', op: 'eq', value: overviewCompany}, ...(cycle ? [{field: 'cycle', op: 'eq', value: cycle}] : [])]}]) : null;
    el('overview-scope').textContent = withGap([overviewCompany || 'No company selected',
      (dataset() || {}).label || (dataset() || {}).id || '', cycle ? 'Cycle ' + cycle : 'All cycles',
      contributions.BASIS[basis()][0]].filter(Boolean).join(' \u00b7 '), gap);
    const ticket = ++overviewRevision;
    const grid = el('overview-grid');
    grid.replaceChildren();
    const cards = core.companyOverviewPresets().map(preset => {
      const card = document.createElement('section');
      card.className = 'studio-overview-card';
      const title = document.createElement('h3');
      title.textContent = preset.title;
      const note = document.createElement('p');
      note.className = 'studio-note';
      note.textContent = preset.note;
      const body = document.createElement('div');
      body.className = 'studio-overview-body';
      body.textContent = 'Reading verified totals\u2026';
      const actions = document.createElement('div');
      actions.className = 'studio-overview-actions';
      const open = document.createElement('button');
      open.type = 'button';
      open.textContent = 'Open in builder';
      open.onclick = () => {
        overviewMode = false;
        commit(() => {
          sheets[active] = core.companyOverviewSheet(preset, columns(),
            {company: overviewCompany, cycle: overviewCycle()}, sheet());
        });
      };
      actions.append(open);
      card.append(title, note, body, actions);
      if (!grid.children.length) card.classList.add('is-focused');
      card.onmouseenter = () => {
        for (const other of grid.children) other.classList.toggle('is-focused', other === card);
      };
      grid.append(card);
      return {preset: preset, body: body};
    });
    if (!overviewCompany) {
      for (const item of cards) item.body.textContent = 'Choose a company to read its giving.';
      return;
    }
    // One query at a time: queryCached aborts the previous request, so four
    // parallel cards would cancel each other.
    (async () => {
      for (const item of cards) {
        if (ticket !== overviewRevision) return;
        try { await renderOverviewCard(item.preset, item.body, ticket); }
        catch (error) {
          if (ticket !== overviewRevision) continue;
          if (error.name !== 'AbortError') item.body.textContent = error.message;
          else if (!item.body.firstElementChild) item.body.textContent = 'Reading verified totals\u2026';
        }
      }
    })();
  }
  async function renderOverviewCard(preset, body, ticket) {
    const missing = core.companyOverviewMissing(preset, columns());
    if (missing.length) { body.textContent = 'This source has no ' + missing.join(' or ') + ' field.'; return; }
    const state = core.companyOverviewSheet(preset, columns(), {company: overviewCompany, cycle: overviewCycle()});
    const compiled = contributions.compile(state, columns(), {dataset: dataset().id, industry: dataset().industry, basis: basis(), offset: 0});
    const result = await queryCached(compiled.query, undefined,
      {onProgress: progress => { if (ticket === overviewRevision) body.textContent = contributions.pendingText(progress); }});
    if (ticket !== overviewRevision) return;
    body.replaceChildren();
    if (!result.rows.length) { body.textContent = 'No matching observations in this scope.'; return; }
    const spec = Object.assign({}, compiled.spec, {columns: result.columns, title: preset.title, seriesColor: seriesColour()});
    if (['pie', 'doughnut', 'waterfall', 'pareto', 'radar'].includes(spec.type)) {
      const id = spec.measures[0], currency = contributions.MONEY.has(id);
      const groups = result.rows.map(row => ({label: contributionPlot.label(row, spec.dimensions),
        value: row[id] / (currency ? 100 : 1), n: 1}));
      const measure = ((result.columns.find(c => c.id === id) || {}).name || id) + (currency ? ' (USD)' : '');
      const drawn = paint.chartSVG({type: spec.type, groups: groups, selected: result.totals.records, excluded: 0},
        measure, {type: spec.type, aggregation: 'sum', x: 0, y: 1},
        {sheet: (dataset() || {}).label || (dataset() || {}).id || '', columns: [{name: 'Group'}, {name: id}]},
        preset.title, '');
      body.append(drawn.svg);
    } else if (MentatContributionCharts.TYPES.includes(spec.type)) {
      MentatContributionCharts.render(body, result.rows, spec, () => {});
    } else {
      contributionPlot.render(body, result.rows, spec, () => {});
    }
  }

  /* ---------------------------------------------------------------- board */

  // Canvas mode as a board of 1, 2 or 4 tiles, each holding one sheet. Exactly
  // one tile is selected, and the selected tile is the sheet `active` names, so
  // the Inspector, Show Me, Export and "Add to briefing" have one answer to
  // "which chart?" whether a board is up or not. Nothing here reaches the URL or
  // a storage key of its own: the board rides in the workspace entry beside the
  // sheets (D-039).
  const BOARD_TILES = [1, 2, 4];
  const canvasOnly = () => {
    const host = document.querySelector('#view-chart-studio .studio');
    return !!(host && host.classList.contains('studio-options-hidden'));
  };
  const boardTiles = () => (board && !overviewMode && !dataSourceTab && canvasOnly()) ? board.tiles : 1;
  const filledSlots = () => board ? board.slots.filter(at => at != null).length : 0;

  // What a sheet last drew, kept by its own id so it survives a re-index.
  // Keyed by the source as well as the sheet: sheet ids start again at s1 in every
  // workspace, so a bare id would hand a tile the figures another report left behind.
  const tileKey = state => sourceKey + '|' + ((state && state.id) || '');
  function tileRecord(state) {
    const key = tileKey(state);
    if (!tileMemory.has(key)) {
      tileMemory.set(key, {revision: 0, waiting: null, request: null, result: null,
        title: '', scope: '', coverage: null, offset: 0});
    }
    return tileMemory.get(key);
  }
  // Nothing is remembered for a sheet that is not on the board. The map is at most
  // four entries wide, and a tile cannot be handed what it drew two arrangements ago.
  function pruneTileMemory() {
    const keep = new Set((board ? board.slots : []).filter(at => at != null).map(at => tileKey(sheets[at])));
    for (const key of [...tileMemory.keys()]) {
      if (keep.has(key)) continue;
      releaseFlight(tileMemory.get(key));
      tileMemory.delete(key);
    }
  }
  // The tile's own title, subtitle, canvas, table, note, reconciliation strip and
  // status line. Anything else a render path writes belongs to the chrome: the
  // selected tile writes the real one, an unselected tile writes a node nobody
  // can see, which is how one render path serves both without a flag per line.
  function tileNodes(tile, selected) {
    const spare = new Map();
    return id => {
      const own = tile.querySelector('.studio-board-tile-' + id);
      if (own) return own;
      if (selected) return el(id);
      if (!spare.has(id)) spare.set(id, document.createElement('div'));
      return spare.get(id);
    };
  }
  function tileSurface(tile, index, selected) {
    const state = sheets[index], record = tileRecord(state);
    return {state: state, index: index, el: tileNodes(tile, selected), primary: selected,
      flight: record, canvasClass: 'studio-board-tile-canvas ',
      // The selected tile also moves the console's own ticket, so a record
      // inspection started against the previous chart is still invalidated.
      bump: () => { if (selected) ++contributionRevision; return ++record.revision; },
      stale: ticket => ticket !== record.revision,
      ticket: () => record.revision,
      // Its own page. The group pager, the label under the chart and the caption
      // an export carries all read this one number, so a tile on page two and the
      // tile beside it on page one can never describe each other's figures.
      get offset() { return record.offset; }, set offset(value) { record.offset = value; },
      get request() { return record.request; },
      set request(value) { record.request = value; if (selected) contributionRequest = value; },
      get result() { return record.result; },
      set result(value) { record.result = value; if (selected) contributionResult = value; },
      get title() { return record.title; },
      set title(value) { record.title = value; if (selected) lastTitle = value; },
      get scope() { return record.scope; },
      set scope(value) { record.scope = value; if (selected) lastScope = value; },
      get coverage() { return record.coverage; },
      set coverage(value) { record.coverage = value; if (selected) setCoverage(value); }};
  }
  // Export, Copy image, Print and Add to briefing act on the selected tile when
  // a board is up, and on the single canvas when it is not.
  function activeSurface() {
    return boardSurfaces.find(view => view && view.primary) || mainSurface();
  }
  function syncBoardSelection() {
    if (!board || board.slots.includes(active)) return;
    const first = board.slots.find(at => at != null);
    if (first != null) active = first;
  }
  // A sheet can leave (a saved view with fewer sheets opens, an undo steps back),
  // and a slot naming one that is gone would draw a tile from nothing. The tile
  // count is the reader's choice and is kept; the slot is cleared.
  function settleBoard() {
    if (!board) return;
    const tiles = BOARD_TILES.includes(board.tiles) ? board.tiles : 1;
    if (tiles === 1) { board = null; return; }
    const taken = new Set(), slots = [];
    for (let slot = 0; slot < tiles; slot += 1) {
      const at = (board.slots || [])[slot];
      const keep = Number.isInteger(at) && at >= 0 && at < sheets.length && !taken.has(at);
      slots.push(keep ? at : null);
      if (keep) taken.add(at);
    }
    board = core.normalizeBoard({sheets: sheets, board: {tiles: tiles, slots: slots}});
    syncBoardSelection();
  }
  // A sheet inserted above a placed one shifts its index, so the board follows
  // the sheets rather than the numbers.
  function shiftBoard(at) {
    if (!board) return;
    board.slots = board.slots.map(slot => (slot == null || slot < at) ? slot : slot + 1);
  }
  // Putting a sheet in a tile, or emptying one, is an edit like any other: it goes
  // through commit(), so Undo steps back through it and Redo brings it back.
  function placeInSlot(slot, index) {
    if (!board) return;
    commit(() => {
      board.slots = board.slots.map((at, other) => (index != null && at === index && other !== slot) ? null : at);
      board.slots[slot] = index;
      if (index != null) active = index; else syncBoardSelection();
    });
  }
  function setBoard(tiles) {
    if (!BOARD_TILES.includes(tiles)) return;
    if ((board ? board.tiles : 1) === tiles) return;
    commit(() => arrangeBoard(tiles));
  }
  function arrangeBoard(tiles) {
    if (tiles === 1) board = null;
    else {
      const taken = new Set(), slots = [];
      for (let slot = 0; slot < tiles; slot += 1) {
        const at = board ? board.slots[slot] : (slot === 0 ? active : null);
        const keep = Number.isInteger(at) && at >= 0 && at < sheets.length && !taken.has(at);
        slots.push(keep ? at : null);
        if (keep) taken.add(at);
      }
      if (!slots.includes(active) && slots.indexOf(null) >= 0) {
        slots[slots.indexOf(null)] = active;
        taken.add(active);
      }
      // An empty tile beside a full one is a hole nobody asked for, so the
      // sheets not placed yet fill the rest in their own order.
      for (let slot = 0; slot < slots.length; slot += 1) {
        if (slots[slot] != null) continue;
        const next = sheets.findIndex((sheet, at) => !taken.has(at));
        if (next < 0) break;
        slots[slot] = next;
        taken.add(next);
      }
      board = {tiles: tiles, slots: slots};
      syncBoardSelection();
    }
  }
  // A sheet sits in one tile only, so a tile offers the sheet it holds and the
  // sheets no other tile has taken. The last filled tile keeps its sheet: an empty
  // board would leave no tile selected and nothing for the Inspector.
  function sheetChoices(index, slot) {
    const placed = new Set(board.slots.filter((at, other) => at != null && other !== slot));
    const options = index == null ? [['', 'Choose a sheet']]
      : filledSlots() > 1 ? [['', 'Empty this tile']] : [];
    sheets.forEach((entry, at) => { if (!placed.has(at)) options.push([String(at), entry.name]); });
    return options;
  }
  // Everything about a tile that can change while the tile itself stays: which
  // sheet it holds, whether it is the selected one, what its select offers. The
  // select is only rewritten when its choices really differ, so a reader with the
  // list open, or the keyboard in it, keeps it.
  function dressTile(tile, index, slot) {
    const state = index == null ? null : sheets[index];
    const selected = state != null && index === active;
    tile.classList.toggle('is-selected', selected);
    tile.dataset.sheet = state ? state.id : '';
    const select = tile.querySelector('.studio-board-tile-sheet');
    const options = sheetChoices(index, slot);
    const same = select.options.length === options.length
      && [...select.options].every((option, at) => option.value === options[at][0]
        && option.textContent === options[at][1]);
    if (!same) select.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
    const wanted = state == null ? '' : String(index);
    if (select.value !== wanted) select.value = wanted;
    select.onchange = () => placeInSlot(slot, select.value === '' ? null : Number(select.value));
    const head = tile.querySelector('.studio-board-tile-head');
    let mark = tile.querySelector('.studio-board-tile-mark');
    if (selected && !mark) {
      mark = document.createElement('span');
      mark.className = 'studio-board-tile-mark';
      mark.textContent = 'Selected \u00b7 the Inspector edits this tile';
      head.append(mark);
    } else if (!selected && mark) {
      mark.remove();
    }
    tile.onclick = event => {
      if (event.target.closest('.studio-board-tile-head')) return;
      if (state == null || index === active) return;
      active = index;
      persist();
      renderAll();
    };
  }
  function buildTile(index, slot) {
    const state = index == null ? null : sheets[index];
    const tile = document.createElement('section');
    tile.className = 'studio-board-tile';
    tile.setAttribute('aria-label', 'Board tile ' + (slot + 1));
    const head = document.createElement('div');
    head.className = 'studio-board-tile-head';
    const select = document.createElement('select');
    select.className = 'studio-board-tile-sheet';
    select.setAttribute('aria-label', 'Sheet in tile ' + (slot + 1));
    head.append(select);
    const title = document.createElement('h3');
    title.className = 'studio-board-tile-title';
    const subtitle = document.createElement('p');
    subtitle.className = 'studio-board-tile-subtitle';
    const canvas = document.createElement('div');
    canvas.className = 'studio-board-tile-canvas studio-canvas fit-' + (state ? state.fit : 'standard');
    const table = document.createElement('div');
    table.className = 'studio-board-tile-table table-scroll';
    table.hidden = true;
    const foot = document.createElement('div');
    foot.className = 'studio-board-tile-foot';
    const reconcile = document.createElement('p');
    reconcile.className = 'studio-board-tile-reconcile studio-reconcile';
    reconcile.setAttribute('role', 'status');
    reconcile.hidden = true;
    const note = document.createElement('p');
    note.className = 'studio-board-tile-note';
    const status = document.createElement('p');
    status.className = 'studio-board-tile-status';
    foot.append(reconcile, note, status);
    tile.append(head, title, subtitle);
    if (state == null) {
      const empty = document.createElement('p');
      empty.className = 'studio-board-tile-empty';
      empty.textContent = 'Empty tile. Choose a sheet for it above.';
      canvas.hidden = true;
      tile.append(empty);
    }
    tile.append(canvas, table, foot);
    dressTile(tile, index, slot);
    return tile;
  }
  // A tile's nodes going away for good. A map lives in its tile's canvas, so a
  // tile takes its own map with it rather than leaving the instance and its
  // listeners behind.
  function discardTile(node) {
    if (!node) return;
    for (const host of [...mapHosts.keys()]) if (node.contains(host)) dropMap(host);
    node.remove();
  }
  function renderBoard() {
    const host = el('board');
    const tiles = boardTiles();
    const studio = document.querySelector('#view-chart-studio .studio');
    if (studio) studio.classList.toggle('studio-board-on', tiles > 1);
    boardSurfaces = [];
    if (tiles < 2) {
      for (const node of boardNodes) discardTile(node);
      boardNodes = [];
      host.hidden = true;
      host.replaceChildren();
      pruneTileMemory();
      return;
    }
    pruneTileMemory();
    // The board takes the canvas over, so the canvas is emptied rather than left
    // holding a drawing that only CSS is hiding (and any map in it goes too).
    dropMapsIn(el('canvas'));
    el('canvas').replaceChildren();
    syncBoardSelection();
    host.hidden = false;
    host.className = 'studio-board studio-board-' + tiles;
    // A slot that still holds the same sheet keeps its own nodes: its drawing, its
    // title, its reconciliation line, its scroll and the focus in it all survive a
    // render, so a refresh in flight - or a cancelled one - leaves the last loaded
    // view on screen exactly as the single canvas does.
    const made = board.slots.map((index, slot) => {
      const kept = boardNodes[slot];
      const wanted = index == null ? '' : sheets[index].id;
      if (kept && kept.dataset.sheet === wanted) { dressTile(kept, index, slot); return kept; }
      discardTile(kept);
      return buildTile(index, slot);
    });
    for (const node of boardNodes.slice(tiles)) discardTile(node);
    boardNodes = made;
    // Only touch the host when the arrangement really changed: re-inserting a node
    // that is already in place would reset its scroll and drop the focus in it.
    const showing = [...host.children];
    if (showing.length !== made.length || made.some((node, at) => showing[at] !== node)) {
      host.replaceChildren(...made);
    }
    board.slots.forEach((index, slot) => {
      if (index == null) return;
      const view = tileSurface(made[slot], index, index === active);
      boardSurfaces[slot] = view;
      renderView(view);
    });
  }
  // One briefing card per filled tile, in slot order, each made by the same
  // description a single "Add to briefing" would make for that sheet. One
  // serializer, so a board slide and a single slide can never disagree.
  async function boardToBriefing() {
    if (!window.MentatBriefing) { failure('Briefing needs a console restart.'); return; }
    const filled = boardSurfaces.filter(Boolean);
    if (!filled.length) { failure('The board has no charts on it yet.'); return; }
    failure('');
    for (const view of filled) await window.MentatBriefing.capture(currentChart(view));
  }

  /* ---------------------------------------------------------------- sheets */

  function renderSheets() {
    el('sheet-source').setAttribute('aria-selected', String(dataSourceTab));
    el('sheet-tabs').replaceChildren(...sheets.map((state, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = state.name;
      button.setAttribute('aria-selected', String(!dataSourceTab && index === active));
      button.title = 'Double-click to rename this sheet';
      button.onclick = () => {
        // Selecting the sheet that is already showing changes nothing, and the
        // re-render would replace this button between the two clicks of a
        // double-click, leaving the rename to run on a detached node.
        if (!dataSourceTab && index === active) return;
        dataSourceTab = false; active = index; persist(); renderAll();
      };
      button.ondblclick = () => renameSheet(button, index);
      return button;
    }));
  }
  function renameSheet(button, index) {
    const input = document.createElement('input');
    input.type = 'text';
    input.value = sheets[index].name;
    input.setAttribute('aria-label', 'Sheet name');
    let done = false;
    const finish = keep => {
      // Enter commits and then blurs, so the name would otherwise be committed
      // twice; a detached input means the tab was replaced under us and there
      // is nothing left to put back.
      if (done || !input.isConnected) return;
      done = true;
      const name = input.value.trim();
      input.replaceWith(button);
      if (keep && name) commit(() => { sheets[index].name = name; });
    };
    input.onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      if (event.key === 'Escape') { event.preventDefault(); finish(false); }
    };
    input.onblur = () => finish(true);
    button.replaceWith(input);
    input.focus();
    input.select();
  }
  function nextName() {
    let n = sheets.length + 1;
    while (sheets.some(s => s.name === 'Sheet ' + n)) n++;
    return 'Sheet ' + n;
  }

  /* -------------------------------------------------------------- the view */

  // S1's bar teal (TB-14) is a token of its own, --chart-series-1, so the
  // measure pills and other teal chrome keep --acu-teal. Without the token
  // the plot keeps its palette. Compared scopes keep --scope-1/2/3.
  const seriesColour = () => { try { return getComputedStyle(document.documentElement).getPropertyValue('--chart-series-1').trim(); } catch { return ''; } };
  // Ruling (c), 2026-09-23: with the Data panel, the shelves or the Inspector
  // put away, or the chart expanded, a bar chart on the main canvas grows into
  // the room it was given: the pitch may rise from S1's 28px to ROOM_PITCH
  // until the bars and the axis fill the canvas height. With every pane shown
  // nothing changes, so the default view stays S1's. Desktop grid only: below
  // 1100px the panes are drawers and the canvas grows with the chart.
  const ROOM_PITCH = 56;
  // A folded shelf (Paul, 2026-09-23: "You should be able to expand each
  // section individually like with a census panel") gives its room too.
  const ROOM_CLASSES = ['studio-data-collapsed', 'studio-shelves-collapsed', 'studio-inspector-collapsed', 'studio-chart-expanded', 'studio-shelf-folded'];
  function chartRoomy() {
    const host = document.querySelector('#view-chart-studio .studio');
    if (!host || host.classList.contains('studio-options-hidden')) return false;
    if (window.matchMedia && window.matchMedia('(max-width: 1099px)').matches) return false;
    return ROOM_CLASSES.some(name => host.classList.contains(name));
  }
  // Lane F2b (boards #50-#52, 2026-09-26). The chart card's h2 is one concise line in the boards' words
  // ("Selected amount vs Observation count, by Recipient committee"). An export, a copied image, the print
  // sheet and a briefing card carry that same title (view.title); the full title the compiler built is the
  // h2's tooltip, and the accounting basis it named stays in the scope line each of them prints.
  function cardTitle(state, basis) {
    const byIndex = new Map(columns().map(field => [field.index, field]));
    const fieldsOf = list => core.shelf(list).map(pill => byIndex.get(pill.column)).filter(Boolean);
    const names = list => list.map(field => field.name);
    const across = fieldsOf(state.columns), down = fieldsOf(state.rows), marks = state.marks || {};
    const colour = fieldsOf(marks.color == null ? [] : [marks.color])[0];
    return core.conciseTitle({
      xMeasures: names(across.filter(field => field.role === 'measure')),
      yMeasures: names(down.filter(field => field.role === 'measure')),
      axisDims: names([...across, ...down].filter(field => field.role === 'dimension')),
      colorDim: colour && colour.role === 'dimension' ? colour.name : '',
      detailDims: names(fieldsOf(marks.detail).filter(field => field.role === 'dimension')),
      measureFallback: (contributions.BASIS[basis] || [])[0] || ''});
  }
  function showTitle(node, full, state, basis) {
    const heading = node('title'), short = cardTitle(state, basis) || full;
    heading.textContent = short;
    if (short !== full) heading.title = full; else heading.removeAttribute('title');
    return short;
  }
  // Lane G1 (boards #46, #50, #52, 2026-09-27): the card's one-line title steps down from its stylesheet
  // size (24px at the boards' width) a pixel at a time to 18px while it is wider than its box, as the Flow
  // headings do (flow-chart.js fitHeading); only then does it end in an ellipsis, and a title still cut
  // carries its words as its tooltip unless it has one already (the compiled title, a map's sources).
  // Every writer of the title is covered: a MutationObserver refits after any change of its words, a
  // ResizeObserver after any change of its width, and the fonts' arrival refits once more (a serif face
  // measured before it loaded). The size is set through the CSSOM (the console's CSP refuses a style
  // attribute); the line height stays the stylesheet's, so nothing below the title moves.
  const TITLE_FLOOR = 18;
  function titleOverflows(heading) {
    const range = document.createRange();
    range.selectNodeContents(heading);
    const style = getComputedStyle(heading);
    const room = heading.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
    return range.getBoundingClientRect().width > room - 0.5;
  }
  function fitTitle() {
    const heading = el('title');
    if (!heading || typeof getComputedStyle !== 'function') return;
    if (heading.dataset.fitTitle != null) {
      if (heading.getAttribute('title') === heading.dataset.fitTitle) heading.removeAttribute('title');
      delete heading.dataset.fitTitle;
    }
    heading.style.fontSize = '';
    if (!heading.textContent || !heading.clientWidth || getComputedStyle(heading).whiteSpace !== 'nowrap') return;
    let size = parseFloat(getComputedStyle(heading).fontSize) || 24;
    while (size > TITLE_FLOOR && titleOverflows(heading)) {
      size -= 1;
      heading.style.fontSize = size + 'px';
    }
    if (titleOverflows(heading) && !heading.getAttribute('title')) {
      heading.title = heading.textContent;
      heading.dataset.fitTitle = heading.textContent;
    }
  }
  function watchTitle() {
    const heading = el('title');
    if (!heading || heading.__fitWatch) return;
    heading.__fitWatch = true;
    let queued = false;
    const later = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; fitTitle(); });
    };
    if (typeof MutationObserver === 'function') new MutationObserver(() => fitTitle()).observe(heading, {childList: true, characterData: true, subtree: true});
    if (typeof ResizeObserver === 'function') new ResizeObserver(later).observe(heading);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(later);
    later();
  }
  // Lane F2b (#50, #51): a scatter or a line on the Studio's canvas is drawn as the boards' chart card -
  // its legends in a column at the plot's right, labels that never sit on one another (the extremes
  // first), each coloured line named at its end, no panel title under the card's own, and, on the
  // desktop grid, a plot as tall as the chart box. contribution-plot.js draws each only when asked; any
  // other mark ignores them.
  // The window's size and the studio's pane classes: what a refit answers (not the canvas box alone).
  function plotLayout() {
    const host = document.querySelector('#view-chart-studio .studio');
    return {viewport: window.innerWidth + 'x' + window.innerHeight,
      layout: host ? [...ROOM_CLASSES, 'studio-options-hidden'].filter(name => host.classList.contains(name)).join(' ') : ''};
  }
  function cardFit(view, spec) {
    const out = {legendPlacement: 'right', labelCollide: true, endLabels: true};
    if (spec.type === 'scatter') out.panelTitles = false;
    const canvas = view.el('canvas'), fit = (view.state && view.state.fit) || 'standard';
    if (canvas !== el('canvas') || (window.matchMedia && window.matchMedia('(max-width: 1099px)').matches) || boardTiles() > 1) return out;
    if (!['scatter', 'line', 'area'].includes(spec.type)) return out;
    const box = fitBox(canvas);
    if (!box) return out;
    // Fit: Standard draws at most 1000 units wide; with panes put away fitRoom scales it to the room's width.
    const drawnWidth = fit === 'standard' && !chartRoomy() ? Math.min(1000, box.width) : box.width;
    const height = fit === 'entire' ? Math.min(box.height, window.innerHeight - 430) : box.height;
    if (drawnWidth > 0 && height > 120) { out.plotHeight = Math.floor((height - 6) * 1000 / drawnWidth); canvas.__plotBox = Object.assign({}, box, plotLayout()); }
    // Lane G1b (#51): the clear space round a value label, in the drawing's units, that is 2px on screen at the
    // width the 1000 units are drawn (contribution-plot.js holds it to 4..6 units).
    if (drawnWidth > 0) out.labelGap = Math.round(200000 / drawnWidth) / 100;
    return out;
  }
  // The canvas's room for a chart is its content box; the figure button's strip pads its top.
  const roomHeight = canvas => { const style = getComputedStyle(canvas); return canvas.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0); };
  function roomFit(canvas) {
    return canvas === el('canvas') && chartRoomy() ? {fitHeight: roomHeight(canvas), barPitchMax: ROOM_PITCH} : {};
  }
  // Paul, 2026-09-24: a pie or doughnut on screen fits the chart box it is
  // drawn in - the whole pie, no scrolling, nothing clipped, and no title,
  // subtitle or caption of its own (the header and the figures above already
  // say them). The box is the canvas's content box, measured without the
  // scrollbar a drawing being replaced may have left behind. The main canvas
  // on the desktop grid has a height of its own; a board tile, or the canvas
  // on a narrow page, grows with its chart, so only its width is given.
  // Everything that takes the chart out of the page (downloads, Copy image,
  // the briefing and its slides) draws today's picture: chart-export.js calls
  // the drawing's __mentatExport.
  const PIE_MARKS = ['pie', 'doughnut'];
  function fitBox(canvas) {
    if (!canvas || !canvas.isConnected || typeof getComputedStyle !== 'function') return null;
    const style = getComputedStyle(canvas);
    if (style.display === 'none') return null;
    const px = name => parseFloat(style[name]) || 0;
    const width = (style.overflowY === 'scroll' ? canvas.clientWidth : canvas.offsetWidth - px('borderLeftWidth') - px('borderRightWidth'))
      - px('paddingLeft') - px('paddingRight');
    const height = (style.overflowX === 'scroll' ? canvas.clientHeight : canvas.offsetHeight - px('borderTopWidth') - px('borderBottomWidth'))
      - px('paddingTop') - px('paddingBottom');
    return width > 0 && height > 0 ? {width: Math.floor(width), height: Math.floor(height)} : null;
  }
  function pieFit(canvas, state) {
    const box = fitBox(canvas);
    if (!box) return null;
    const bounded = canvas === el('canvas') && !(window.matchMedia && window.matchMedia('(max-width: 1099px)').matches);
    // Entire view caps the drawing at the window less the chrome (chart-studio.css).
    const height = bounded ? ((state && state.fit) === 'entire' ? Math.min(box.height, window.innerHeight - 430) : box.height) : null;
    return {width: box.width, height: height != null && height >= 120 ? height : null};
  }
  // The renderer's options for this surface: a pie is fitted to its box, every
  // other mark is drawn as before. No overlay reaches a pie (it has no value
  // axis, it draws its own labels, and core.totalNote refuses its grand total),
  // so its export needs nothing added to today's picture.
  function screenOptions(view, options, type) {
    const canvas = view.el('canvas'), mainCanvas = canvas === el('canvas');
    if (!PIE_MARKS.includes(type)) { if (mainCanvas) delete canvas.dataset.pieFit; return options; }
    const fit = pieFit(canvas, view.state);
    // Drawn while the canvas could not be measured (a hidden panel): the
    // resize observer below redraws it once the canvas has a size.
    if (mainCanvas) { if (fit) delete canvas.dataset.pieFit; else canvas.dataset.pieFit = 'wanted'; }
    return fit ? Object.assign({}, options, {fit}) : options;
  }
  // A bounded pie is held at the box's height (the stylesheet gives it the
  // width that goes with it); one whose box grows with it scales with its width.
  // Set through the style object: the console's CSP refuses a style attribute.
  function placeFitted(svg) {
    const fit = svg && svg.__fit;
    if (fit && fit.height) svg.style.height = Math.floor(fit.height) + 'px';
  }
  // theme.js can fail to load (the console restarting mid-page); the ink and
  // face then fall back to chart-export.js's own instead of a ReferenceError.
  const studioTheme = () => globalThis.MentatTheme || {ink: '#202522', sans: 'Arial, sans-serif'};
  function overlayLabels(svg, result) {
    const theme = studioTheme();
    const values = core.markValues(result);
    if (!values.length) return false;
    const marks = [...svg.querySelectorAll('rect,circle')].filter(node => node.querySelector(':scope > title'));
    if (marks.length !== values.length) return false;
    const sideways = ['horizontal', 'groupedhorizontal'].includes(result.type);
    marks.forEach((mark, index) => {
      const value = values[index];
      if (typeof value !== 'number' || !Number.isFinite(value)) return;
      const label = document.createElementNS(NS, 'text');
      label.textContent = compact(value);
      label.setAttribute('fill', theme.ink);
      label.setAttribute('font-family', theme.sans);
      label.setAttribute('font-size', '10');
      label.setAttribute('data-mark-label', 'true');
      if (mark.tagName === 'circle') {
        label.setAttribute('x', mark.getAttribute('cx'));
        label.setAttribute('y', Number(mark.getAttribute('cy')) - 8);
        label.setAttribute('text-anchor', 'middle');
      } else if (sideways) {
        label.setAttribute('x', Number(mark.getAttribute('x')) + Number(mark.getAttribute('width')) + 4);
        label.setAttribute('y', Number(mark.getAttribute('y')) + Number(mark.getAttribute('height')) / 2 + 3);
      } else {
        label.setAttribute('x', Number(mark.getAttribute('x')) + Number(mark.getAttribute('width')) / 2);
        label.setAttribute('y', Number(mark.getAttribute('y')) - 4);
        label.setAttribute('text-anchor', 'middle');
      }
      svg.append(label);
    });
    return true;
  }
  function overlayReference(svg, plot, lines) {
    const theme = studioTheme();
    if (!plot || !lines.length || plot.max === plot.min) return;
    for (const line of lines) {
      if (line.value < plot.min || line.value > plot.max) continue;
      const at = plot.p0 + (line.value - plot.min) / (plot.max - plot.min) * (plot.p1 - plot.p0);
      const rule = document.createElementNS(NS, 'line');
      const vertical = plot.orient === 'vertical';
      rule.setAttribute('x1', vertical ? plot.from : at);
      rule.setAttribute('x2', vertical ? plot.to : at);
      rule.setAttribute('y1', vertical ? at : plot.from);
      rule.setAttribute('y2', vertical ? at : plot.to);
      rule.setAttribute('stroke', theme.ink);
      rule.setAttribute('stroke-width', '1.5');
      rule.setAttribute('stroke-dasharray', '7 5');
      rule.setAttribute('data-reference', line.kind);
      const note = document.createElementNS(NS, 'text');
      note.textContent = line.label + ' ' + compact(line.value);
      note.setAttribute('x', vertical ? plot.to : at + 4);
      note.setAttribute('y', vertical ? at - 5 : plot.from + 12);
      note.setAttribute('text-anchor', vertical ? 'end' : 'start');
      note.setAttribute('fill', theme.ink);
      note.setAttribute('font-family', theme.sans);
      note.setAttribute('font-size', '11');
      svg.append(rule, note);
    }
  }
  function overlayTotal(svg, total) {
    const theme = studioTheme();
    if (total == null) return false;
    const box = String(svg.getAttribute('viewBox') || '0 0 960 620').split(' ').map(Number);
    const note = document.createElementNS(NS, 'text');
    note.textContent = (lastConfig?.transform === 'forecast' ? 'Observed total ' : 'Grand total ') + compact(total);
    note.setAttribute('x', box[2] - 28);
    note.setAttribute('y', 88);
    note.setAttribute('text-anchor', 'end');
    note.setAttribute('fill', theme.ink);
    note.setAttribute('font-family', theme.sans);
    note.setAttribute('font-size', '12');
    note.setAttribute('font-weight', '600');
    svg.append(note);
    return true;
  }

  // Paul, 2026-09-23: "nothing is logically constrained". A table's marks are
  // its value cells, so Labels off hides their printed values - the way a text
  // table reads with mark labels off - and each keeps its value on hover and in
  // the data CSV. The table then lists its groups; Labels on prints the values.
  const TABLE_LABELS_OFF = 'Labels are off, so the table lists its groups without their values; hover a cell for its value, or turn Labels on.';
  // Census B-E21 / B-E22 (engine contract W8; Paul, 2026-09-23: "nothing is
  // logically constrained"): Parameters can ask every renderer for every
  // category label on the axis and every member in a legend. Unasked, the
  // options are the Labels switch alone, as before.
  function chartOptions(state) {
    const params = (state && state.params) || {}, options = {labels: !!(state && state.showLabels)};
    if (params.axisLabels === 'all') options.axisLabels = 'all';
    if (params.legend === 'all') options.legend = 'all';
    return options;
  }
  function renderTable(result, config, node, showLabels = true, source = data) {
    const rows = paint.tableRows(result, config, source);
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    for (const cell of rows[0]) {
      const th = document.createElement('th');
      th.textContent = String(cell);
      headRow.append(th);
    }
    head.append(headRow);
    const body = document.createElement('tbody');
    for (const row of rows.slice(1, 401)) {
      const tr = document.createElement('tr');
      row.forEach((cell, at) => {
        const td = document.createElement('td');
        const text = typeof cell === 'number' ? fmt(cell) : String(cell == null ? '' : cell);
        if (!showLabels && at > 0 && typeof cell === 'number') { td.title = text; td.dataset.valueHidden = 'true'; }
        else td.textContent = text;
        tr.append(td);
      });
      body.append(tr);
    }
    table.append(head, body);
    node('table').replaceChildren(table);
    if (rows.length > 401) {
      const note = document.createElement('p');
      note.className = 'studio-note';
      note.textContent = 'Showing the first 400 of ' + fmt(rows.length - 1) + ' rows. Download the data CSV for every value.';
      node('table').prepend(note);
    }
  }

  // Census C-16: Map and Filled map on a report worksheet. The compiler names
  // the place field (report.map); core.placeData reads each row's place and the
  // engine aggregates the sheet's own measure over every place; the Census
  // geometry the verified map reads draws it. Nothing assumes a selected amount:
  // the coverage line reads the sheet's own measure (or counts places, for a
  // measure that does not add up). If the geometry cannot be read, the same
  // places are drawn as bars and the reason is a note.
  let worksheetMapTicket = 0;
  const worksheetGeometry = new Map();
  async function renderWorksheetMap(view, plan, result, config, measure, notes, source) {
    const node = view.el, state = view.state, canvas = node('canvas'), ticket = String(++worksheetMapTicket);
    canvas.dataset.mapTicket = ticket;
    const current = () => canvas.dataset.mapTicket === ticket && !verified();
    const groups = core.mapGroups(result, plan.kind);
    const settings = state.map || {layers: ['states', 'districts'], congress: '119'};
    const shape = plan.kind === 'district' ? 'districts' : 'states';
    const active = [...new Set([...settings.layers, ...(plan.filled && plan.kind !== 'zip' ? [shape] : [])])];
    const layers = [...new Set([...active, 'states', ...(plan.kind === 'district' ? ['districts'] : [])])].sort();
    const zips = plan.kind === 'zip' ? [...new Set(groups.map(g => g.zip5).filter(Boolean))].sort() : [];
    const key = JSON.stringify({zips: zips, layers: layers, congress: settings.congress});
    const lead = [plan.kind === 'zip' ? 'Each ZIP is placed at its Census ZCTA point, which is not an address; ZIP+4 and ZIPs that lost a leading zero are read as their five-digit ZIP.'
      : plan.kind === 'city-state' ? 'Each city is placed at its state: ' + plan.name + ' names a city and state, and a city has no Census shape here.'
      : plan.kind === 'district' ? 'Districts are ' + settings.congress + 'th Congress Census shapes. District 0 is a statewide or at-large seat and has a district shape only in a state with one district; map by State to place the rest.'
      : 'Each state is placed at its Census shape.',
      'Darker, larger marks mean a larger absolute ' + measure + '; negative values use orange. Boundaries are visual overlays and do not allocate values.'];
    const loading = el('loading').textContent;
    try {
      let geometry = worksheetGeometry.get(key);
      if (!geometry) {
        status('Loading Census boundaries…');
        if (!contributionToken) contributionToken = (await contributionAPI('session')).token;
        geometry = await contributionAPI('geography', {zips: zips, layers: layers, congress: settings.congress});
        worksheetGeometry.set(key, geometry);
        if (worksheetGeometry.size > 6) worksheetGeometry.delete(worksheetGeometry.keys().next().value);
      }
      if (!current()) return;
      const field = core.fieldAt(core.classifyFields(columns(), state.types), config.y);
      const unit = ['count', 'countd'].includes(config.aggregation) ? 'count' : field && field.unit === 'currency' ? 'dollars' : 'number';
      const drawn = MentatContributionMap.render(canvas, {groups: groups, valueField: 'value', unit: unit,
        coverage: ['sum', 'count'].includes(config.aggregation) ? 'value' : 'places', zipOnly: true, geometry: geometry,
        layers: active, filled: plan.filled, title: view.title, valueLabel: measure, notes: lead, showLayerControls: false});
      canvas.dataset.worksheetMap = plan.kind;
      view.scope += ' · Congress ' + settings.congress + '; ' + active.join(', ') + '; overlays do not allocate values. ' +
        drawn.data.marks.length + ' located places; ' + drawn.data.unmapped.length + ' unmapped · ' +
        (geometry.sources || []).map(s => (s.vintage || s.label || s.url) + ' SHA256 ' + s.sha256).join(' · ');
      if (loading !== 'Loading Census boundaries…') status(loading);
    } catch (error) {
      if (!current()) return;
      if (loading !== 'Loading Census boundaries…') status(loading);
      const drawn = paint.chartSVG(result, measure, config, source, view.title, view.scope, chartOptions(state));
      canvas.replaceChildren(drawn.svg);
      node('note').textContent = [...notes, 'Census geometry could not be read (' + error.message + '), so the places are drawn as bars.'].join(' ');
    }
  }

  // The configuration a worksheet view falls back to when the engine cannot
  // build or draw the one compiled: plain bars of the same first grouping and
  // measure, which every engine release draws. Paul, 2026-09-23: "nothing is
  // logically constrained" - a blank canvas is never the answer.
  const ENGINE_AGGREGATIONS = ['sum', 'mean', 'count', 'median', 'min', 'max'];
  function plainConfig(config) {
    const sideways = ['horizontal', 'groupedhorizontal'].includes(config.type);
    const x = ['scatter', 'histogram'].includes(config.type) ? -1 : config.x;
    const aggregation = config.y < 0 || !ENGINE_AGGREGATIONS.includes(config.aggregation) ? 'count' : config.aggregation;
    return {type: sideways ? 'horizontal' : 'bar', x: x, y: aggregation === 'count' && config.y < 0 ? -1 : config.y,
      dimensions: x >= 0 ? [x] : [], aggregation: aggregation, transform: 'none', period: config.period, horizon: config.horizon,
      start: config.start, end: config.end, limit: config.limit,
      maxGroups: Number.isInteger(config.maxGroups) ? config.maxGroups : 100000, splitMulti: false, series: -1, filters: config.filters};
  }
  function renderView(view) {
    // Lane F2b: the h2's tooltip belongs to the chart that set it (showTitle); each drawing starts without one.
    const heading = (view || mainSurface()).el('title');
    if (heading) heading.removeAttribute('title');
    view = view || mainSurface();
    const node = view.el, state = view.state;
    // Lane P: a map whose marks cards are its layers draws through map-studio.js.
    if (verified() && globalThis.MentatMapStudio && MentatMapStudio.handles(state)) { MentatMapStudio.renderView(view); return; }
    if(verified()){renderContributionView(view);return;}
    if (view.primary) { lastResult = null; lastConfig = null; }
    node('reconcile').hidden = true;
    node('canvas').replaceChildren();
    delete node('canvas').dataset.pieFit;
    // A worksheet map still loading its geometry must not draw over this view.
    node('canvas').dataset.mapTicket = '';
    node('table').replaceChildren();
    node('status').replaceChildren();
    if (!data) {
      node('title').textContent = '';
      node('subtitle').textContent = '';
      node('note').textContent = 'Choose a report worksheet in the Data panel to begin.';
      return;
    }
    const report = core.compileReport(state, columns());
    // Lane F (#46): a Flow card is drawn by studio-flow.js with the PAC evidence Flow's layout.
    if (report.markType === 'flow' && window.MentatStudioFlow) {
      node('canvas').className = view.canvasClass + 'studio-canvas fit-' + state.fit;
      const drawn = window.MentatStudioFlow.render(view, report, data, {notes: worksheetNotes(state),
        subtitle: scopeSubtitle(state), fit: pieFit(node('canvas'), state), showLabels: !!state.showLabels,
        committee: core.committeeActivitySheet(data.sheet), source: state.source});
      if (view.primary) { lastResult = drawn.result; lastConfig = report.config; }
      return;
    }
    const markName = report.markType === 'cards' ? 'Dual axis' : core.markLabel(report.markType);
    // Provisional only: measureLabel reads the built result to tell an
    // aggregated view from one that plots individual values, so a box,
    // histogram or scatter title would read "· Sum" without it. The real title
    // is set again below, once math.build has produced that result.
    // The h2 never names the console (item 17): before a chart is drawn it is
     // empty, and an empty title is not rendered at all (chart-studio.css).
    node('title').textContent = report.config
      ? paint.measureLabel(report.config, null, data) + ' · ' + markName
      : '';
    node('subtitle').textContent = report.config ? scopeSubtitle(state) : '';
    // Paul, 2026-09-23 ("nothing is logically constrained", census B-S01): only
    // an empty sheet has nothing to draw; every other sentence the compiler
    // has is a note under the chart it drew.
    if (!report.config) {
      node('note').textContent = [...worksheetNotes(state),
        report.issues.join(' ') || 'Drag a field onto Columns or Rows.'].join(' ');
      node('canvas').hidden = false;
      node('table').hidden = true;
      return;
    }
    const notes = [...worksheetNotes(state), ...report.notes];
    let config = report.config, result, placed = null;
    const fallBack = error => {
      placed = null;
      const plain = plainConfig(report.config);
      const built = math.build(data, plain);
      notes.push(markName + ' could not be drawn from these fields (' + error.message + '), so the same groups are shown as bars.');
      config = plain;
      return built;
    };
    try {
      try {
        // Census C-16: a map reads each row's place and aggregates the sheet's
        // own measure over every place (core.placeData, core.mapConfig).
        if (report.map && report.renderMode === 'chart') {
          placed = core.placeData(data, report.map, !!report.config.splitMulti);
          config = core.mapConfig(report.config, placed.column);
          result = math.build(placed.data, config);
          result.groupingLabel = report.map.name;
        } else result = math.build(data, config);
      }
      catch (error) { result = fallBack(error); }
    } catch (error) {
      node('note').textContent = [...notes, error.message].join(' ');
      node('table').hidden = true;
      return;
    }
    for (const warning of result.warnings || []) if (!notes.includes(warning)) notes.push(warning);
    result = core.sortResult(result, state.sort);
    if (view.primary) { lastResult = result; lastConfig = config; }
    const measure = paint.measureLabel(config, result, data);
    node('title').textContent = measure + ' · ' + markName;
    node('subtitle').textContent = scopeSubtitle(state);
    const source = state.source;
    const filterText = config.filters
      .map(f => (core.fieldAt(report.fields, f.column) || {name: 'field'}).name + ' ' + f.op + ' ' + f.value).join('; ');
    view.title = node('title').textContent;
    view.scope = [source.reportName + ' / ' + data.sheet,
      (result.split ? fmt(result.selected) + ' values from ' + fmt(data.row_count) + ' worksheet rows selected (' + result.split.cells + ' multi-value cells split); '
        : result.selected + ' of ' + data.row_count + ' rows selected; ') + result.excluded + ' rows excluded for missing or non-numeric values.',
      'Group/X: ' + (result.groupingLabel || (core.fieldAt(report.fields, config.x) || {name: 'All rows'}).name) + '.',
      ['box', 'scatter', 'histogram'].includes(result.type) && config.grain !== 'group' ? 'Individual worksheet values.' : 'Aggregation: ' + config.aggregation + '.',
      'Worksheet rows: ' + (config.start || 'first data row') + '–' + (config.end || 'last data row') + '.',
      'Row count limit: ' + (config.limit || 'all matching rows') + ' (after filters).',
      'Filters: ' + (filterText || 'none') + '.',
      'Sort: ' + state.sort + '.',
      'Group limit: ' + (config.maxGroups === 'all' ? 'all' : config.maxGroups) + '.',
      'Split multi-value cells: ' + (config.splitMulti ? 'on' : 'off') + '.',
      'SHA-256: ' + data.sha256 + '.'].join(' ');
    if (result.note) view.scope += ' ' + result.note;
    // Recipient-committee activity is not the industry's money, so a grand
    // total over it would print a figure that reads as one the report
    // reconciles to. The engine's own refusal field carries the reason into
    // totalNote(), grandTotal() and the status line at once.
    const committee = core.committeeActivitySheet(data.sheet);
    if (committee && !result.totalRefusal) result.totalRefusal = committee;

    if (report.renderMode === 'table') {
      node('canvas').hidden = true;
      node('table').hidden = false;
      renderTable(result, config, node, !!state.showLabels);
      if (!state.showLabels) notes.push(TABLE_LABELS_OFF);
    } else if (placed) {
      node('canvas').hidden = false;
      node('table').hidden = true;
      node('canvas').className = view.canvasClass + 'studio-canvas fit-' + state.fit;
      if (state.showLabels) notes.push('Each map mark carries its value on hover and when selected.');
      renderWorksheetMap(view, report.map, result, config, measure, notes, placed.data)
        .catch(error => { node('note').textContent = [...notes, error.message].join(' '); });
    } else {
      // Every surface draws its own map (census B-S29), so only a chart gets here.
      const source = placed ? placed.data : data;
      node('canvas').hidden = false;
      node('table').hidden = true;
      // The canvas wears this view's fit class before a pie measures it.
      node('canvas').className = view.canvasClass + 'studio-canvas fit-' + state.fit;
      // The seventh argument (engine contract W8) asks the renderer itself for
      // a value label on every mark of every type; a pie is also fitted to its
      // chart box (screenOptions).
      const options = screenOptions(view, chartOptions(state), result.type);
      let drawn;
      try { drawn = paint.chartSVG(result, measure, config, source, view.title, view.scope, options); }
      catch (error) {
        result = core.sortResult(fallBack(error), state.sort);
        if (view.primary) { lastResult = result; lastConfig = config; }
        drawn = paint.chartSVG(result, paint.measureLabel(config, result, data), config, data, view.title, view.scope, options);
      }
      if (state.showLabels && !drawn.svg.querySelector('[data-mark-label]') && !overlayLabels(drawn.svg, result)) notes.push('Mark labels are unavailable for this view.');
      const analytics = state.analytics;
      const references = core.referenceValues(result, analytics);
      if (references.length && !drawn.plot) notes.push('Reference lines need a value axis; this view has none.');
      overlayReference(drawn.svg, drawn.plot, references);
      if (analytics.totals) {
        const refusal = core.totalNote(result, config);
        // The committee-activity caption is already in the list; it is the
        // same sentence, and saying it twice reads as two reasons.
        if (refusal) { if (!notes.includes(refusal)) notes.push(refusal); }
        else if (!overlayTotal(drawn.svg, core.grandTotal(result, config))) {
          notes.push('A grand total is not defined for this view.');
        }
      }
      node('canvas').className = view.canvasClass + 'studio-canvas fit-' + state.fit;
      applyCardStyle(drawn.svg, state, result.type);
      node('canvas').replaceChildren(drawn.svg);
      placeFitted(drawn.svg);
    }
    node('note').textContent = notes.concat(result.note ? [result.note] : []).join(' ') ||
      source.reportName + ' / ' + data.sheet + ' · ' + fmt(result.selected) + ' rows selected';
    renderStatus(result, config, node);
  }

  function renderStatus(result, config, node) {
    const summary = core.rowsToMarksSummary(result, config, columns());
    if (!summary) return;
    const parts = [
      fmt(summary.marks) + ' mark' + (summary.marks === 1 ? '' : 's'),
      summary.viewRows + ' row' + (summary.viewRows === 1 ? '' : 's') + ' by ' + summary.viewColumns + ' column' + (summary.viewColumns === 1 ? '' : 's'),
      summary.total == null ? '' : summary.measureLabel + ': ' + fmt(summary.total)
    ].filter(Boolean);
    const nodes = parts.map(part => {
      const span = document.createElement('span');
      span.textContent = part;
      return span;
    });
    const badge = document.createElement('span');
    badge.className = 'studio-local';
    badge.textContent = 'LOCAL ONLY';
    badge.title = 'Every value is read from the local report file and drawn in this browser.';
    nodes.push(badge);
    if (parts.length > 2) nodes[2].title = 'Total of the plotted marks, each aggregated as ' + config.aggregation + '.';
    node('status').replaceChildren(...nodes);
  }

  function renderDataSource() {
    const worksheet = !dataSourceTab;
    el('grid').hidden = !worksheet;
    el('datasource').hidden = worksheet;
    if (worksheet || !data) return;
    if(verified()){renderRelationshipSource();return;}
    const meta = core.classifyFields(columns(), sheet().types);
    const source = sheet().source;
    const list = document.createElement('dl');
    const pairs = [['Report folder', source.runName], ['Report', source.reportName], ['Worksheet', data.sheet],
      ['Header row', data.header_row], ['Rows', fmt(data.row_count)], ['Columns', columns().length],
      ['Dimensions', meta.dimensions.length + ' (' + meta.identifiers.length + ' identifier fields, listed under More fields)'],
      ['Measures', meta.measures.length], ['SHA-256', data.sha256], ['Note', data.note]];
    for (const [term, value] of pairs) {
      const dt = document.createElement('dt');
      dt.textContent = term;
      const dd = document.createElement('dd');
      dd.textContent = String(value == null ? '' : value);
      list.append(dt, dd);
    }
    const table = document.createElement('table');
    const head = document.createElement('thead');
    head.innerHTML = '<tr><th>Column</th><th>Detected type</th><th>Role</th><th>Distinct</th><th>Missing</th></tr>';
    const body = document.createElement('tbody');
    for (const field of meta.fields) {
      const tr = document.createElement('tr');
      for (const cell of [field.name, field.type, field.isIdentifier && !field.isTime ? field.role + ' (identifier)' : field.role,
        field.distinct == null ? '' : fmt(field.distinct), field.missing == null ? '' : fmt(field.missing)]) {
        const td = document.createElement('td');
        td.textContent = String(cell);
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(head, body);
    const heading = document.createElement('h3');
    heading.textContent = 'Scanned columns';
    el('datasource').replaceChildren(list, heading, table);
  }

  function renderRelationshipSource(){
    const model=dataset()?.model||contributionCatalog?.model,host=el('datasource');host.replaceChildren();
    const add=(tag,value,parent=host)=>{const n=document.createElement(tag);n.textContent=value;parent.append(n);return n;};
    add('h2','Sources, relationships and field definitions');add('p',model?.grain||'One preserved source observation. Measures aggregate at this grain.');
    add('h3','Available report sources');const sources=add('div','');sources.className='studio-source-choices';
    for(const item of contributionCatalog?.datasets||[]){const button=add('button',item.label||item.id,sources);button.type='button';button.disabled=!item.available;button.setAttribute('aria-pressed',String(item.id===dataset()?.id));button.onclick=()=>{el('dataset').value=item.id;selectContributionDataset();};}
    const worksheets=add('button','Explore every report worksheet');worksheets.type='button';worksheets.onclick=()=>{dataSourceTab=false;el('source-mode').value='worksheet';el('source-mode').dispatchEvent(new Event('change'));};
    add('p','Candidate funding, committee spending, coverage and evidence sheets have their own rows and measures. Select a worksheet to inspect its complete scanned field list. Alternative summary tables are never automatically added to receipt facts.');
    if(model){add('h3','Logical tables');const cards=add('div','');cards.className='studio-model-tables';
      for(const table of model.tables){const card=add('section','',cards);add('h4',table.name,card);add('p',table.grain,card);add('small','Key: '+table.key+' · '+table.source,card);}
      add('h3','Relationships');for(const r of model.relationships){const p=add('p','');add('strong',r.from+' → '+r.to,p);add('span',' · '+r.cardinality+' · '+r.matching+'. '+r.behavior,p);}
      add('p',model.notes.join(' '));add('h3','Date and geography levels');for(const [name,levels]of Object.entries(model.hierarchies))add('p',name+': '+levels.map(id=>columns().find(f=>f.id===id)?.name||id).join(' → '));
    }
    add('h3','Complete analytical field catalog');const table=add('table',''),thead=add('thead','',table),tr=add('tr','',thead);for(const title of ['Field','Logical table','Data type','Role','Interpretation','Use'])add('th',title,tr);const body=add('tbody','',table);
    for(const field of columns()){const row=add('tr','',body);for(const value of [field.name,field.table||'Observation',field.data_type||field.type,field.role+(field.discrete?' · discrete':' · continuous'),field.description||''])add('td',value,row);const cell=add('td','',row),button=add('button',field.role==='measure'?'Add to Rows':'Add to Columns',cell);button.type='button';button.onclick=()=>{dataSourceTab=false;commit(()=>place(field.role==='measure'?'rows':'columns',field.index));};}
    add('h3','Tableau references');for(const [name,url]of [['Relationships preserve table grain','https://help.tableau.com/current/pro/desktop/en-us/relate_tables.htm'],['Data types and mixed values','https://help.tableau.com/current/pro/desktop/en-us/datafields_typesandroles_datatypes.htm'],['Dimensions, measures, discrete and continuous fields','https://help.tableau.com/current/pro/desktop/en-us/datafields_typesandroles.htm']]){const p=add('p',''),a=add('a',name,p);a.href=url;a.target='_blank';a.rel='noopener noreferrer';}
  }

  // Paul, 2026-09-23: "nothing is logically constrained". Every mark sorts,
  // time axes included (census B-S15, B-S16). None is each view's own order:
  // chronological on a time axis or a histogram's bins, the source's order on
  // a worksheet, and largest first on any other verified view - where it is
  // the same query as Descending, so None is pressed there as Descending.
  // A to Z and Z to A order the groups by their label (contract W13).
  const SORT_BUTTONS = [['sort-asc', 'asc', 'Sort the grouping smallest first'],
    ['sort-desc', 'desc', 'Sort the grouping largest first']];
  const LABEL_SORTS = [['sort-az', 'az', 'A to Z'], ['sort-za', 'za', 'Z to A']];
  // The labels button's own markup title is its hint. It is cached on first
  // read, before a lock reason overwrites it, rather than restated here.
  let labelsHint = null;
  const labelHint = () => labelsHint === null ? (labelsHint = el('labels').title) : labelsHint;
  // The query the verified path will actually send. The CLASSIC compile does
  // not know combination, dual-line or histogram, so asking it whether Sort
  // applies left two enabled buttons on exactly those three marks - and they
  // did nothing. Shelves the compile refuses have no answer to give: the
  // canvas already names that error, so the toolbar leaves the buttons alone.
  function verifiedCompile() {
    if (!verified() || !data || !dataset()) return null;
    try {
      return contributions.compile(sheet(), columns(),
        {dataset: dataset().id, industry: dataset().industry, basis: basis(), offset: 0});
    } catch (error) { return null; }
  }
  // The sort reads measures[0] only, so the button says which measure that is.
  function sortedMeasureName(compiled) {
    const id = compiled && compiled.spec.measures[0];
    if (!id) return '';
    return (columns().find(f => f.id === id) || {}).name || id;
  }
  function renderSortButtons(view) {
    const compiled = verifiedCompile();
    const onVerified = verified() && !!compiled;
    const stored = sheet().sort || 'none';
    // A verified view whose first dimension is not a time axis orders 'none'
    // largest first: the same query as Descending, which is pressed for it.
    const ordered = onVerified && contributions.orderedAxis(compiled.query.dimensions);
    const same = onVerified && !ordered;
    // None is never disabled now (Paul: "nothing is logically constrained"):
    // on a verified category axis it stores 'source', the groups' own order.
    const pressed = stored === 'source' ? 'none' : same && stored === 'none' ? 'desc' : stored;
    const measure = onVerified ? sortedMeasureName(compiled)
      : view && data ? paint.measureLabel(view, null, data) : '';
    for (const [id, direction, hint] of SORT_BUTTONS) {
      el(id).disabled = !data;
      el(id).title = measure
        ? 'Sort by ' + measure + ', ' + (direction === 'asc' ? 'smallest first' : 'largest first')
        : hint;
      el(id).setAttribute('aria-pressed', String(pressed === direction));
    }
    const axis = onVerified ? ((columns().find(f => f.id === compiled.query.dimensions[0]) || {}).name || '')
      : view && view.x >= 0 ? ((core.fieldAt(core.classifyFields(columns(), sheet().types), view.x) || {}).name || '') : '';
    for (const [id, direction, words] of LABEL_SORTS) {
      if (!el(id)) continue;
      el(id).disabled = !data;
      el(id).title = 'Sort by ' + (axis || 'each group’s label') + ', ' + words;
      el(id).setAttribute('aria-pressed', String(pressed === direction));
    }
    const none = el('sort-none');
    if (none) {
      none.disabled = !data;
      none.title = same ? 'No sort: each group in its own order, A to Z with numbers ascending (a page still holds the largest groups)'
        : ordered ? 'Chronological order, as the time axis runs'
        : 'Keep the order the source gives; a time axis runs in time order';
      none.setAttribute('aria-pressed', String(pressed === 'none'));
    }
  }
  // Labels are render-only, so this changes no query: it only stops the button
  // claiming a toggle on a mark whose renderer never reads spec.labels. The
  // stored showLabels is left exactly as it was, so it returns when the reader
  // goes back to a mark that draws them.
  // A table prints every value already, so its button is disabled with that
  // reason in both sources; every chart draws its labels (contract W8, V05).
  function renderLabelButton(state) {
    const compiled = verifiedCompile();
    const locked = verified() && compiled ? contributions.labelLock(compiled.spec.type)
      : !verified() && state.marks && state.marks.type === 'text' ? contributions.labelLock('text') : '';
    const table = verified() && compiled ? compiled.spec.type === 'table' : !!(state.marks && state.marks.type === 'text');
    el('labels').disabled = !!locked;
    el('labels').title = locked || (table ? 'Print each value in its cell; off lists the groups without their values' : labelHint());
    el('labels').setAttribute('aria-pressed', String(!locked && !!state.showLabels));
  }

  // The classic dialog picks the first subject by itself, so the studio settles
  // on one too. That is a derived default, not a user edit: it is written
  // straight to the sheet with no commit, so it costs no undo step.
  function defaultSubject(state, field) {
    const list = members(field.index);
    if (!list.includes(String(state.params.subject || ''))) state.params.subject = list[0] || '';
    return list;
  }

  // Time analysis adds one subject's periods together and the engine refuses a
  // worksheet holding several, so the studio offers the same Subject choice the
  // classic dialog does. The control appears only where that column exists.
  function renderSubjectParam() {
    const field = data ? core.subjectField(core.classifyFields(columns(), sheet().types).fields) : null;
    el('param-subject-label').hidden = !field;
    if (!field) return;
    const list = defaultSubject(sheet(), field);
    fill(el('param-subject'), list.length ? list.map(m => [m, m]) : [['', 'Choose a subject…']]);
    el('param-subject').value = String(sheet().params.subject || '');
  }

  function renderToolbar() {
    const state = sheet();
    el('undo').disabled = !history.length;
    el('redo').disabled = !future.length;
    renderSortButtons(data ? core.compileReport(state, columns()).config : null);
    renderLabelButton(state);
    el('fit').value = state.fit;
    el('showme-toggle').setAttribute('aria-pressed', String(!!el('showme-details').open));
    for (const tiles of BOARD_TILES) {
      el('board-' + tiles).setAttribute('aria-pressed', String((board ? board.tiles : 1) === tiles));
    }
    el('param-start').value = state.params.start || '';
    el('param-end').value = state.params.end || '';
    el('param-limit').value = state.params.limit || '';
    const everyGroup = state.params.maxGroups === 'all';
    el('param-groups').value = everyGroup ? '' : state.params.maxGroups || '';
    el('param-groups').placeholder = everyGroup ? 'All' : String(core.STUDIO_GROUP_LIMIT);
    if (el('param-groups-all')) el('param-groups-all').checked = everyGroup;
    el('param-split').checked = !!state.params.splitMulti;
    if (el('param-axis-labels')) el('param-axis-labels').checked = state.params.axisLabels === 'all';
    if (el('param-legend')) el('param-legend').checked = state.params.legend === 'all';
    el('param-horizon').value = state.analytics.horizon || 2;
    el('param-period').value = state.analytics.period || '';
    renderSubjectParam();
  }

  function renderAll() {
    settleBoard();
    syncContributionControls();
    renderToolbar();
    renderFields();
    renderShelves();
    renderAnalytics();
    renderShowMe();
    renderSheets();
    renderDataSource();
    renderScopeChip();
    renderCompareChip();
    renderDatasetMessage();
    renderSourceSummary();
    renderFilterRows();
    renderOverviewControls();
    applyFolds();
    renderBoard();
    if (overviewMode) renderOverview();
    else if (!dataSourceTab && boardTiles() < 2) renderView();
  }

  /* ------------------------------------------------------------- downloads */

  function download(format) {
    if(verified()){downloadContributions(format);return;}
    if (!lastResult) { failure('Build a view before downloading.'); return; }
    failure('');
    // Everything a download carries comes off the surface the reader is looking
    // at: its drawing, its title and the scope line that names its page.
    const view = activeSurface();
    if (format === 'csv') {
      const quote = v => '"' + String(typeof v === 'string' && /^[=+\-@\t\r]/.test(v) ? "'" + v : v).replaceAll('"', '""') + '"';
      const rows = [['Chart', view.title], ['Scope', view.scope], ['Note', view.el('note').textContent],
        ...(lastConfig && lastConfig.type === 'flow' && window.MentatStudioFlow
          ? window.MentatStudioFlow.tableRows(lastResult) : paint.tableRows(lastResult, lastConfig, data))];
      MentatExport.download('\uFEFF' + rows.map(r => r.map(quote).join(',')).join('\r\n'),
        'text/csv;charset=utf-8', 'mentat-chart-studio.csv');
      return;
    }
    const svg = view.el('canvas').firstChild;
    if (!svg) { failure('This view has no image to export. Download the data CSV instead.'); return; }
    MentatExport.image(svg, format, view.title, view.scope, 'mentat-chart-studio').catch(e => failure(e.message));
  }

  // Everything that takes a chart out of this console needs the same account
  // of it. One reader of the live view, so a download, a clipboard image, a
  // print sheet and a slide can never disagree about what they are showing.
  function currentChart(view) {
    view = view || activeSurface();
    const state = view.state, source = state.source || {};
    const basisLabel = verified()
      ? (contributions.BASIS[basisOf(state)] || [])[0] || ''
      : 'Worksheet values';
    // Both of these leave the console: into the briefing the server stores,
    // and from there into every slide's speaker notes. The server keeps each
    // one as an object and replaces anything else with an empty one, so
    // sending a caption here threw the workbook digest away and left the
    // exports printing "[object Obje" where a SHA-256 belongs. Send the
    // objects, and keep the captions beside them for the print sheet, which
    // has one line and no room for either.
    const sourceLabel = source.reportName || '';
    const sha = source.sha256 || (data && data.sha256) || '';
    return {
      svg: view.el('canvas').querySelector('svg'),
      title: view.title || view.el('title').textContent || 'Chart Studio',
      scope: view.scope || scopeText() || '',
      // Lane P: a map sheet's caption names its layers' sources, as its chip does.
      // FIX 4: the slide, the printed sheet and the drawer print this line first; the gap rides on it.
      chip: withGap(((verified() && globalThis.MentatMapStudio && MentatMapStudio.scopeChip && MentatMapStudio.scopeChip(state)) || {}).text || scopeText(),
        verified() ? view.coverage : null),
      note: view.el('note').textContent || '',
      basis: basisLabel,
      sheetId: state.id,
      sheetName: state.name,
      source: core.chartSourceRecord(source),
      provenance: core.chartProvenanceRecord(source, sha),
      sourceLabel: sourceLabel,
      sha: sha
    };
  }

  // The clipboard gets the same drawing the download would write, caption and
  // all: an image pasted into a message has to carry its own scope.
  async function copyImage() {
    const chart = currentChart();
    if (!chart.svg) { failure('No image to copy. Export the CSV instead.'); return; }
    failure('');
    try {
      const size = await MentatExport.copy(chart.svg, chart.title, chart.scope);
      status('Copied image.');
    } catch (error) { failure(error.message); }
  }

  // A printed sheet is still on a desk long after this window is closed, so
  // it states its own title, scope, basis, workbook hash and date instead of
  // relying on whoever printed it to remember them.
  function printChart() {
    const chart = currentChart();
    if (!chart.svg) { failure('No image to print. Export the CSV instead.'); return; }
    failure('');
    el('print-title').textContent = chart.title;
    el('print-scope').textContent = [chart.scope, chart.note].filter(Boolean).join(' \u00b7 ');
    el('print-source').textContent = [chart.basis,
      chart.sourceLabel ? 'Source ' + chart.sourceLabel : '',
      chart.sha ? 'Workbook SHA-256 ' + chart.sha : '',
      'Printed ' + new Date().toLocaleDateString('en-US', {year: 'numeric', month: 'long', day: 'numeric'})]
      .filter(Boolean).join(' \u00b7 ');
    document.body.classList.add('studio-printing');
    const done = () => {
      document.body.classList.remove('studio-printing');
      window.removeEventListener('afterprint', done);
      clearTimeout(timer);
    };
    // A dialog dismissed in a way that never fires afterprint must not leave
    // the console wearing its print skin.
    const timer = setTimeout(done, 60000);
    window.addEventListener('afterprint', done);
    window.print();
  }

  /* ---------------------------------------------------------- saved views */

  // Views the server refused to keep still open in this tab, so a read-only
  // preview is a console that cannot persist, not one that cannot work.
  const VIEW_MIGRATION = 'mentat-chart-studio-migrated-v1';
  let viewsMemory = [];
  const previewReadOnly = () => { try { return !!readOnly; } catch { return false; } };
  function viewMessage(error) {
    if (previewReadOnly()) return 'Read-only preview. Save views from the main console.';
    const text = String((error && error.message) || '');
    if (!text || /JSON|Unexpected token|Failed to fetch|NetworkError|<!DOCTYPE/i.test(text)) return 'Server unavailable.';
    return text;
  }
  async function getJSON(path) {
    const response = await fetch(path, {credentials: 'same-origin'});
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) throw new Error((body && body.error) || 'Server unavailable (' + response.status + ').');
    return body || {};
  }
  const viewSourceLabel = () => {
    if (verified()) { const chosen = dataset(); return chosen ? (chosen.label || chosen.id) : ''; }
    return (sheet().source || {}).reportName || '';
  };

  // A saved view carries the source it was built from, and the server keeps
  // that as an object: a view reopened next year has to name the report, its
  // digest and the folder it came from, not just the words that happened to be
  // on screen. Sending the caption alone was refused outright with 400. The
  // shaping lives in chart-studio-core.js so the payload this console puts on
  // the wire is the payload a test can read, and every caller goes through it.
  const viewSourceRecord = () => core.viewSourceRecord(sheet().source, verified() ? dataset() : null);
  // Views kept in this tab by an older build, and any view a future build
  // shapes differently, still have to print something.
  const viewSourceText = view => core.sourceLabelOf(view && view.source);
  const defaultViewName = () => [sheet().name, viewSourceLabel()].filter(Boolean).join(' \u00b7 ');
  function storeKeyLabel(key) {
    const parts = String(key || '').split('|');
    if (parts[0] === 'contributions') {
      const entry = ((contributionCatalog && contributionCatalog.datasets) || []).find(d => d.id === parts[1]);
      return entry ? (entry.label || entry.id) : (parts[1] || key);
    }
    return parts.filter(Boolean).pop() || 'worksheet';
  }
  const viewLabel = view => [view.name,
    view.sheet_count ? view.sheet_count + ' sheet' + (view.sheet_count === 1 ? '' : 's') : '',
    view.saved_at ? new Date(typeof view.saved_at === 'number'
      ? view.saved_at * 1000 : view.saved_at).toLocaleString() : '',
    view.memory ? 'this tab only' : ''].filter(Boolean).join(' \u00b7 ');

  // The name is typed in place of the menu entry, the way a sheet is renamed,
  // so naming a view never moves the reader out of the menu they opened.
  function saveView() {
    const button = el('save-view');
    if (!button || !button.isConnected) return;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'studio-view-name';
    input.value = defaultViewName();
    input.setAttribute('aria-label', 'View name');
    let done = false;
    const finish = keep => {
      if (done || !input.isConnected) return;
      done = true;
      const name = input.value.trim().slice(0, 80);
      input.replaceWith(button);
      if (keep && name) saveViewNamed(name);
    };
    input.onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      if (event.key === 'Escape') { event.preventDefault(); finish(false); }
    };
    input.onblur = () => finish(true);
    button.replaceWith(input);
    input.focus();
    input.select();
  }
  async function saveViewNamed(name) {
    const record = {name, sourceKey, source: viewSourceRecord(),
      sheets: JSON.parse(JSON.stringify(sheets)), active};
    // Written only for a board of 2 or 4 tiles, so a view saved on the single
    // canvas is the payload it has always been (studio_api.json v2).
    const shape = core.normalizeBoard({sheets: sheets, board: board});
    if (shape && shape.tiles > 1) record.board = shape;
    failure('');
    try {
      const body = await api('/api/studio/views', record);
      rememberSavedView(body && body.id);
      status('Saved view "' + name + '" \u00b7 ' + record.sheets.length +
        ' sheet' + (record.sheets.length === 1 ? '' : 's') + '.');
      return body;
    } catch (error) {
      viewsMemory.unshift(Object.assign({}, record, {id: 'memory-' + Date.now(),
        saved_at: new Date().toISOString(), sheet_count: record.sheets.length, memory: true}));
      failure(viewMessage(error) + ' "' + name + '" stays in this tab.');
      return null;
    }
  }

  // What a view needs before it can be opened here, said before the click
  // rather than after it.
  function openability(view) {
    if (view.sourceKey === sourceKey) return {ok: true};
    const parts = String(view.sourceKey || '').split('|');
    if (parts[0] !== 'contributions') return {ok: false,
      reason: 'Open ' +
        (viewSourceText(view) || 'that report') + ' in the Data panel first. This view was saved there.'};
    if (!verified()) return {ok: false, reason: 'Switch to Verified contributions to open this view.'};
    const entry = ((contributionCatalog && contributionCatalog.datasets) || []).find(d => d.id === parts[1]);
    if (!entry) return {ok: false, reason: 'This console has no report called ' + parts[1] + '.'};
    if (!entry.available) return {ok: false, reason: entry.message || 'That report is unavailable on this console.'};
    // A view whose sheets fan out across more than one industry (S4_DESIGN.md
    // section 4, sheet.sources[]) names every industry it needs at the view
    // level too, so opening it can be refused up front rather than partway
    // through a sheet that turns out to name an unregistered or unavailable
    // report.
    if (Array.isArray(view.industries)) {
      const datasets = (contributionCatalog && contributionCatalog.datasets) || [];
      for (const label of view.industries) {
        const match = datasets.find(d => d.industry === label || d.id === label);
        if (!match) return {ok: false, reason: 'This console has no report called ' + label + '.'};
        if (!match.available) return {ok: false, reason: match.message || 'That report is unavailable on this console.'};
      }
    }
    return {ok: true, dataset: parts[1]};
  }
  // Opening a view is a request for its sheets and then one commit. The open the
  // reader asked for last is the one that lands: an earlier open that answers
  // later (a busy console answers out of order) is dropped, not drawn over it.
  // While the sheets are on their way the studio says so, in the status line
  // and as aria-busy on the studio, because what is on screen is about to be
  // replaced; a browser test waits on that mark rather than on a chart that
  // may already have been drawn before the open was asked for.
  let openTicket = 0;
  function viewOpening(name) {
    const host = document.querySelector('#view-chart-studio .studio');
    if (host) { if (name == null) host.removeAttribute('aria-busy'); else host.setAttribute('aria-busy', 'true'); }
    if (name != null) status('Opening view "' + name + '"\u2026');
  }
  async function openView(view) {
    failure('');
    const state = openability(view);
    if (!state.ok) { failure(state.reason); return; }
    const ticket = ++openTicket;
    let full = view;
    if (!Array.isArray(full.sheets)) {
      viewOpening(view.name || 'Saved view');
      try { full = (await getJSON('/api/studio/views?id=' + encodeURIComponent(view.id))).view; }
      catch (error) { if (ticket === openTicket) { viewOpening(null); failure(viewMessage(error)); } return; }
      if (ticket !== openTicket) return;
    }
    viewOpening(null);
    if (!full || !Array.isArray(full.sheets) || !full.sheets.length) { failure('That saved view has no sheets in it.'); return; }
    // Another dataset is selected first, exactly as the Data panel would, so
    // the shelves land on the fields that view was built against.
    if (state.dataset && state.dataset !== (dataset() || {}).id) {
      el('dataset').value = state.dataset;
      selectContributionDataset();
    }
    holdingSavedId = true;
    try {
      commit(() => {
        sheets = full.sheets.map(s => core.migrateSheet(Object.assign(core.createSheet(s.name, s.id), s)));
        active = Math.min(Math.max(0, full.active | 0), sheets.length - 1);
        // A view without a board opens as one sheet, exactly as before. A board
        // that does not describe its own sheets is dropped with a word about it,
        // because failing the whole open over a layout would lose the sheets too.
        const opened = core.normalizeBoard(full);
        board = opened && opened.tiles > 1 ? opened : null;
        if (board) { syncBoardSelection(); if (!canvasOnly()) setOptionsHidden(true); }
        dataSourceTab = false;
      });
    } finally { holdingSavedId = false; }
    rememberSavedView(full.id);
    status('Opened view "' + full.name + '".'
      + (full.board && !board ? ' Its board did not match its sheets, so it opened as one sheet.' : '')
      + (full.sheets.some(s => s && typeof s === 'object' && 'marksCards' in s && !core.normalizeMarksCards(s))
        ? ' A sheet\u2019s marks cards did not match it, so it opened as its first card.' : ''));
  }

  // The views that lived in this browser before the server had a place for
  // them are offered back once, under their own names, rather than left to
  // rot behind a feature that replaced them.
  async function migrateStoredViews() {
    let migrated = true;
    try { migrated = localStorage.getItem(VIEW_MIGRATION) === '1'; } catch { migrated = true; }
    if (migrated || previewReadOnly()) return [];
    const store = readStore();
    const saved = [];
    for (const key of Object.keys(store)) {
      const entry = store[key];
      if (!entry || !Array.isArray(entry.sheets) || !entry.sheets.length) continue;
      const label = storeKeyLabel(key);
      const name = ('Recovered \u00b7 ' + label + ' \u00b7 ' + entry.sheets.length +
        ' sheet' + (entry.sheets.length === 1 ? '' : 's')).slice(0, 80);
      // The old store never recorded anything but the key, so the key itself
      // is all there is to carry - as an object, because a bare string is
      // refused. A contributions key still carries its dataset (the dataset
      // id IS the industry label), so captionSourceForKey recovers industry
      // and dataset here instead of losing them the way a bare caption did.
      const source = core.captionSourceForKey(key);
      try {
        const body = await api('/api/studio/views',
          {name, sourceKey: key, source, sheets: entry.sheets, active: entry.active | 0});
        saved.push({id: body.id, saved_at: body.saved_at, name, sourceKey: key,
          source, sheet_count: entry.sheets.length});
      } catch (error) {
        // Returning quietly here left the reader believing their old views had
        // been recovered when the console had refused every one of them, and
        // the migration flag below was never set so it silently retried for ever.
        failure(viewMessage(error) + ' Browser-saved views were not moved to this console.');
        return saved;
      }
    }
    try { localStorage.setItem(VIEW_MIGRATION, '1'); } catch {}
    return saved;
  }

  async function openViews() {
    const anchor = el('open-views');
    failure('');
    let views = [], notice = '';
    try { views = (await getJSON('/api/studio/views')).views || []; }
    catch (error) { notice = viewMessage(error); }
    if (!notice && !views.length) views = await migrateStoredViews();
    const all = [...views, ...viewsMemory.filter(saved => !views.some(v => v.id === saved.id))];
    const here = all.filter(view => view.sourceKey === sourceKey);
    const others = all.filter(view => view.sourceKey !== sourceKey);
    const items = [];
    if (notice) items.push([notice, null, {disabled: true, reason: notice}]);
    items.push(['This source', null, {group: true}]);
    if (here.length) for (const view of here) items.push([viewLabel(view), () => openView(view)]);
    else items.push(['No saved views for this source yet', null,
      {disabled: true, reason: 'Save one with Export \u203a Save view.'}]);
    items.push(['Other sources', null, {group: true}]);
    if (others.length) for (const view of others) {
      const state = openability(view);
      const sourceText = viewSourceText(view);
      items.push([viewLabel(view) + (sourceText ? ' \u00b7 ' + sourceText : ''),
        state.ok ? () => openView(view) : null,
        state.ok ? {} : {disabled: true, reason: state.reason}]);
    }
    else items.push(['None', null, {disabled: true, reason: 'Views saved on other reports appear here.'}]);
    popup(anchor, items);
  }

  /* --------------------------------------------------------------- filters */

  // The filter pill's editor: a searchable member checklist for dimensions, a
  // pair of bounds for numbers and dates, and the verified engine's stage
  // choice. Apply goes through commit(), so a filter edit is one undo step.
  function filterEditor(anchor, position) {
    const stored = sheet().filters[position];
    if (!stored) return;
    const filter = core.normalizeFilter(stored);
    const field = fieldOf(filter.column);
    if (!field) return;
    const menu = popup(anchor, []);
    const form = document.createElement('div');
    form.className = 'studio-filter-editor';
    const head = document.createElement('div');
    head.className = 'studio-filter-head';
    const title = document.createElement('strong');
    title.textContent = field.name;
    head.append(title);
    const numeric = field.role === 'measure';
    const dated = !numeric && (field.type === 'date' || field.unit === 'date');
    const choices = numeric
      ? [['between', 'Between'], ['gte', 'At least'], ['lte', 'At most'], ['equals', 'Equals']]
      : dated ? [['between', 'Between'], ['equals', 'Equals']]
      : [['in', 'Is one of'], ['notin', 'Is not one of'], ['equals', 'Equals'], ['contains', 'Contains']];
    const operator = document.createElement('select');
    operator.setAttribute('aria-label', 'Filter operation');
    for (const [id, label] of choices) operator.append(new Option(label, id));
    operator.value = choices.some(choice => choice[0] === filter.op) ? filter.op : choices[0][0];
    let stage = null;
    if (verified()) {
      stage = document.createElement('select');
      stage.setAttribute('aria-label', 'Filter stage');
      if (field.role === 'dimension' || field.id === 'amount') stage.append(new Option('Source records, before grouping', 'rows'));
      if (field.role === 'measure') stage.append(new Option('Grouped values, after aggregation', 'aggregate'));
      stage.value = stored.stage || (field.role === 'measure' && field.id !== 'amount' ? 'aggregate' : 'rows');
    }
    const body = document.createElement('div');
    const hint = document.createElement('small');
    hint.textContent = verified()
      ? (numeric ? 'Money is entered in USD; counts are whole observations.'
        : 'Exact text preserves identifiers such as leading-zero ZIPs.')
      : 'Worksheet filters apply to source rows before aggregation.';
    let read = () => ({op: operator.value, value: ''});
    let poll = 0;
    const stopPoll = () => { if (poll) { clearInterval(poll); poll = 0; } };

    function drawScalar(count) {
      const inputs = [];
      for (let i = 0; i < count; i++) {
        const input = document.createElement('input');
        input.type = dated ? 'date' : numeric ? 'number' : 'text';
        input.setAttribute('aria-label', count === 1 ? 'Filter value' : i ? 'Upper bound' : 'Lower bound');
        if (count === 2) input.placeholder = i ? 'Up to' : 'From';
        inputs.push(input);
      }
      const bounds = core.filterBounds(filter);
      if (count === 2) inputs.forEach((input, i) => { input.value = filter.op === 'between' ? bounds[i] : ''; });
      else inputs[0].value = Array.isArray(filter.value) ? '' : filter.value;
      body.replaceChildren(...inputs);
      read = () => ({op: operator.value,
        value: count === 2 ? [inputs[0].value.trim(), inputs[1].value.trim()] : inputs[0].value.trim()});
      inputs[0].focus();
    }

    function drawList() {
      const chosen = new Set(core.filterValues(filter));
      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search values';
      search.setAttribute('aria-label', 'Search values');
      const links = document.createElement('div');
      links.className = 'studio-filter-links-row';
      const all = document.createElement('button');
      all.type = 'button';
      all.textContent = 'All';
      const none = document.createElement('button');
      none.type = 'button';
      none.textContent = 'None';
      links.append(all, none);
      const list = document.createElement('div');
      list.className = 'studio-filter-members';
      body.replaceChildren(search, links, list);
      const paint = () => {
        const available = members(field.index);
        const query = search.value.trim().toLowerCase();
        const matches = available.filter(value => !query || value.toLowerCase().includes(query));
        const shown = matches.slice(0, 400);
        list.replaceChildren();
        if (!available.length) {
          const note = document.createElement('p');
          note.textContent = verified() ? 'Reading the values of this field\u2026' : 'This column has no values.';
          list.append(note);
        }
        for (const value of shown) {
          const row = document.createElement('label');
          const box = document.createElement('input');
          box.type = 'checkbox';
          box.checked = chosen.has(value);
          box.onchange = () => { if (box.checked) chosen.add(value); else chosen.delete(value); };
          row.append(box, document.createTextNode(value === '' ? '(blank)' : value));
          list.append(row);
        }
        if (matches.length > shown.length) {
          const note = document.createElement('p');
          note.textContent = (matches.length - shown.length) + ' more values \u2014 narrow the search.';
          list.append(note);
        }
        return available.length;
      };
      search.oninput = paint;
      all.onclick = () => { for (const value of members(field.index)) chosen.add(value); paint(); };
      none.onclick = () => { chosen.clear(); paint(); };
      // members() memoises its facet query, so polling for the answer costs no
      // extra request; the negative cache stops a failed facet from re-firing.
      if (!paint() && verified()) {
        poll = setInterval(() => { if (!menu.isConnected || paint()) stopPoll(); }, 400);
        setTimeout(stopPoll, 20000);
      }
      read = () => ({op: operator.value, value: [...chosen]});
      search.focus();
    }

    const draw = () => {
      stopPoll();
      const op = operator.value;
      if (op === 'in' || op === 'notin') drawList(); else drawScalar(op === 'between' ? 2 : 1);
    };
    operator.onchange = draw;
    const actions = document.createElement('div');
    actions.className = 'studio-editor-actions';
    const apply = document.createElement('button');
    apply.type = 'button';
    apply.className = 'is-primary';
    apply.textContent = 'Apply';
    apply.onclick = () => {
      const next = read();
      stopPoll();
      menu.remove();
      commit(() => {
        sheet().filters[position] = core.normalizeFilter(Object.assign({column: Number(filter.column)}, next,
          stage ? {stage: stage.value} : {}));
      });
    };
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Cancel';
    cancel.onclick = () => { stopPoll(); menu.remove(); if (anchor.isConnected) anchor.focus(); };
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'Remove';
    remove.onclick = () => { stopPoll(); menu.remove(); commit(() => removeFrom('filters', position)); };
    actions.append(apply, cancel, remove);
    form.append(head, operator, body);
    if (stage) form.append(stage);
    form.append(hint, actions);
    menu.append(form);
    draw();
  }

  // The Inspector's Filters tab and the Filters shelf are the same filters.
  function renderFilterRows() {
    const meta = core.classifyFields(columns(), sheet().types);
    const rows = (sheet().filters || []).map((filter, position) => {
      const field = core.fieldAt(meta, Number(filter.column));
      if (!field) return null;
      const row = document.createElement('div');
      row.className = 'studio-filter-row';
      const name = document.createElement('strong');
      name.textContent = field.name;
      const summary = document.createElement('span');
      summary.textContent = core.filterSummary(filter, field, memberTotal(field.index));
      const actions = document.createElement('div');
      actions.className = 'studio-editor-actions';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = 'Edit';
      edit.onclick = () => filterEditor(edit, position);
      const drop = document.createElement('button');
      drop.type = 'button';
      drop.textContent = 'Remove';
      drop.onclick = () => commit(() => removeFrom('filters', position));
      actions.append(edit, drop);
      row.append(name, summary, actions);
      return row;
    }).filter(Boolean);
    el('filter-rows').replaceChildren(...rows);
    // Any field can be filtered, and filtered again (census B-S25): a second
    // filter on the same field narrows it further.
    fill(el('filter-add'), [['', 'Add a field\u2026'],
      ...meta.dimensions.concat(meta.measures).map(f => [String(f.index), f.name])]);
    el('tab-filters').textContent = rows.length ? 'Filters (' + rows.length + ')' : 'Filters';
    // Below 1100px the Inspector is behind a button, so the number of filters
    // shaping this view has to be readable without opening it.
    const badge = el('inspector-count');
    badge.textContent = rows.length ? String(rows.length) : '';
    badge.hidden = !rows.length;
    el('inspector-toggle').setAttribute('aria-label', rows.length
      ? 'Inspector · ' + rows.length + ' active filter' + (rows.length === 1 ? '' : 's')
      : 'Inspector');
  }

  // The scope chip states the industry, cycle and company this view is reading,
  // and says when that scope arrived from the Graph tab.
  function scopeText() {
    if (!data) return '';
    if (!verified()) {
      const source = sheet().source;
      return [source.reportName, data.sheet].filter(Boolean).join(' \u00b7 ');
    }
    const meta = core.classifyFields(columns(), sheet().types);
    const valueOf = id => {
      const field = meta.fields.find(f => f.id === id);
      if (!field) return '';
      const filter = (sheet().filters || []).find(f => Number(f.column) === field.index);
      if (!filter || !core.filterActive(filter)) return '';
      const values = core.filterValues(filter);
      return values.length === 1 ? values[0] : values.length ? values.length + ' selected' : '';
    };
    const carried = !!(appliedScope && appliedScope.carried);
    const company = valueOf('company');
    const parts = [(dataset() || {}).label || (dataset() || {}).id || '', valueOf('cycle') || 'All cycles'];
    if (carried || company) parts.push(company || 'All companies');
    return (carried ? 'Carried from Graph \u00b7 ' : '') + parts.filter(Boolean).join(' \u00b7 ');
  }
  /* ------------------------------------------------ compare (build 03, S1) */

  // One sheet split into two or three scopes, one coloured series each. Cycle
  // and company are ONE verified query with an 'in' filter; industry is one
  // query per report, because a report is one industry's workbook. The split
  // itself is a browser-only 'scope' series: the server refuses a dimension it
  // does not define (contribution_semantics.py validate), so it is never sent.
  const COMPARE_KINDS = ['industry', 'cycle', 'company'];
  const COMPARE_NOUN = {industry: 'industries', cycle: 'cycles', company: 'companies'};
  const compareState = state => (verified() && compareBySheet.get((state || sheet()).id)) || null;
  const compareActive = state => { const entry = compareState(state); return !!entry && entry.values.length >= 2; };
  // --scope-1/2/3 resolved to colours, because an exported SVG keeps no CSS.
  function scopeColours() {
    const style = getComputedStyle(document.documentElement);
    return ['#57447b', '#327a7a', '#8d6a9f'].map((fallback, i) => (style.getPropertyValue('--scope-' + (i + 1)) || '').trim() || fallback);
  }
  function usedColumns(state) {
    const used = new Set();
    const add = list => { for (const p of core.shelf(list)) used.add(Number(p.column)); };
    add(state.columns); add(state.rows);
    const marks = state.marks || {};
    for (const zone of ['color', 'size', 'label']) if (marks[zone] != null) add([marks[zone]]);
    add(marks.detail || []); add(marks.tooltip || []);
    // Marks v3: every card's encodings (Shape, Path and Angle included) and measures.
    for (const card of core.readCards(state)) {
      for (const zone of CARD_ZONES) { const value = card.encodings[zone]; add(Array.isArray(value) ? value : value && value.column != null ? [value] : []); }
      if (card.measure) add([card.measure]);
    }
    if (state.pages != null) add([state.pages]);
    for (const filter of state.filters || []) used.add(Number(filter.column));
    return used;
  }
  // The one value a sheet's row filter pins a field to, or '' when it does not.
  function pinnedValue(state, id) {
    const meta = core.classifyFields(columns(), state.types);
    for (const filter of state.filters || []) {
      if (!core.filterActive(filter) || filter.stage === 'aggregate') continue;
      const field = meta.fields.find(f => f.index === Number(filter.column));
      if (!field || field.id !== id) continue;
      const values = core.filterValues(filter);
      return values.length === 1 ? String(values[0]) : '';
    }
    return '';
  }
  // The menu's values: reports for industry, the verified index's own members
  // for cycle and company (company by giving, largest first).
  async function compareValues(kind) {
    if (kind === 'industry') return ((contributionCatalog && contributionCatalog.datasets) || [])
      .filter(d => d.available).map(d => ({value: d.id, label: d.label || d.id}));
    if (!dataset()) return [];
    const query = {dataset: dataset().id, basis: basis(), dimensions: [kind], measures: ['amount'], filters: [],
      aggregate_filters: [], sort: [{field: kind === 'cycle' ? 'cycle' : 'amount', direction: kind === 'cycle' ? 'asc' : 'desc'}],
      limit: kind === 'cycle' ? 200 : 1000, offset: 0};
    const result = await queryCached(query, {flight: menuFlight});
    return result.rows.map(row => String(row[kind] ?? '')).filter(Boolean).map(value => ({value, label: value}));
  }
  // Scope 1 is what the sheet already shows: its report, its one cycle (else
  // the newest, which is what Graph opens on), its one company (else the
  // largest company it draws).
  async function compareBase(kind) {
    if (kind === 'industry') { const d = dataset(); return d ? {value: d.id, label: d.label || d.id} : null; }
    const pinned = pinnedValue(sheet(), kind);
    if (pinned) return {value: pinned, label: pinned};
    if (kind === 'cycle') { const list = (await compareValues('cycle')).map(v => v.value).sort(); return list.length ? {value: list.at(-1), label: list.at(-1)} : null; }
    const row = lastResult && contributionRequest && contributionRequest.dimensions.includes('company') ? lastResult.rows[0] : null;
    return row && row.company ? {value: String(row.company), label: String(row.company)} : null;
  }
  async function pickCompare(kind, value) {
    const label = kind === 'industry' ? (((contributionCatalog.datasets || []).find(d => d.id === value) || {}).label || value) : value;
    const current = compareState();
    let entry;
    if (current && current.kind === kind) {
      if (current.values.some(v => v.value === value) || current.values.length >= 3) return;
      entry = {kind, values: [...current.values, {value, label}]};
    } else {
      let base = null;
      try { base = await compareBase(kind); } catch (error) { base = null; }
      entry = {kind, values: base && base.value !== value ? [base, {value, label}] : [{value, label}]};
    }
    compareBySheet.set(sheet().id, entry);
    contributionOffset = 0;
    renderAll();
  }
  let compareOpening = 0;
  async function openCompare(anchor, kinds) {
    const menu = globalThis.MentatCompareMenu;
    if (!menu) { failure('Compare needs a console restart.'); return; }
    const token = ++compareOpening;
    const entry = compareState(), exclude = {};
    if (entry) exclude[entry.kind] = entry.values.map(v => v.value);
    if (dataset()) exclude.industry = [...new Set([...(exclude.industry || []), dataset().id])];
    for (const kind of ['cycle', 'company']) { const pinned = pinnedValue(sheet(), kind); if (pinned && !(entry && entry.kind === kind)) exclude[kind] = [pinned]; }
    // Final fix round (R03-4): scope 1 (the sheet's one cycle, else the newest;
    // its one company, else the largest it draws) is already on the sheet, so
    // the menu never offers it (build 02's menu leaves out the current value too).
    // It is worked out when a kind's list is asked for, after that list (the
    // newest cycle is then a cache hit on the same query), so the menu opens at
    // once and the list shows "Loading…" meanwhile. A list for a menu that has
    // since been replaced resolves to nothing.
    const valuesFor = async kind => {
      const list = await compareValues(kind);
      if (!['cycle', 'company'].includes(kind) || (entry && entry.kind === kind)) return list;
      let base = null;
      try { base = await compareBase(kind); } catch (error) { base = null; }
      if (token !== compareOpening) return [];
      return base ? list.filter(v => String(v.value) !== String(base.value)) : list;
    };
    if (compareMenu) compareMenu.close();
    compareMenu = menu.open(anchor, {kinds: kinds || COMPARE_KINDS, valuesFor, exclude,
      onPick: (kind, value) => { pickCompare(kind, value); }, onClose: () => { compareMenu = null; }});
  }
  function clearCompare() {
    compareBySheet.delete(sheet().id);
    contributionOffset = 0;
    renderAll();
  }
  function renderCompareChip() {
    const entry = compareState(), on = !!entry;
    el('compare-add').hidden = on;
    el('compare-add').disabled = !verified() || !data;
    el('compare-add').title = verified() ? 'Add another industry, cycle or company to this sheet as a coloured series'
      : 'Comparing scopes needs Verified contributions';
    el('compare-pills').hidden = !on;
    el('compare-clear').hidden = !on;
    // Two reports cannot share one CSV or one briefing slide's provenance, so
    // while industries are compared both say so and wait (explicit state).
    const twoReports = on && entry.kind === 'industry' && entry.values.length >= 2;
    el('download-csv').disabled = twoReports;
    el('download-csv').title = twoReports ? 'Export the CSV one industry at a time: each report carries its own workbook provenance' : '';
    el('briefing-add').disabled = twoReports;
    el('briefing-add').title = twoReports ? 'Add to briefing takes one report at a time; clear the comparison first' : 'Add this chart to the briefing';
    const note = el('compare-note');
    note.hidden = !(on && entry.values.length < 2);
    note.textContent = on && entry.values.length < 2 ? 'Choose a second ' + (entry.kind === 'company' ? 'company' : entry.kind) + ' to compare' : '';
    if (!on) { if (comparePills) { comparePills.destroy(); comparePills = null; } return; }
    if (!globalThis.MentatCompareMenu) return;
    if (comparePills && comparePills.kind !== entry.kind) { comparePills.destroy(); comparePills = null; }
    if (!comparePills) {
      comparePills = globalThis.MentatCompareMenu.pills(el('compare-pills'), {kind: entry.kind, values: entry.values, max: 3,
        label: 'Compared ' + COMPARE_NOUN[entry.kind],
        onRemove: at => {
          const now = compareState();
          if (!now) return;
          const values = now.values.filter((_, i) => i !== at);
          if (values.length < 2) { clearCompare(); el('compare-add').focus(); return; }
          compareBySheet.set(sheet().id, {kind: now.kind, values});
          renderAll();
        },
        onAdd: button => openCompare(button, [entry.kind])});
      comparePills.kind = entry.kind;
    } else comparePills.update(entry.values);
  }
  // The compared sheet: one query per scope for industry, one 'in' query for
  // cycle and company, merged here under the 'scope' series with the pills'
  // own colours. Drill-down, CSV, totals, reconciliation and the briefing each
  // say what they do with more than one scope instead of assuming one source.
  // Census B-S32: a map compares its scopes side by side - one map per scope,
  // each its own map instance (B-S29), on one set of Census boundaries and one
  // shading scale, so an equal colour means an equal value on every map.
  async function drawCompareMaps(view, compiled, rows, values, colours, queries, results, notes, ticket) {
    const node = view.el, host = node('canvas'), settings = view.state.map || {layers: ['states', 'districts'], congress: '119'};
    const shaded = compiled.spec.measures[0] || 'amount', unit = contributions.MONEY.has(shaded) ? 'cents' : ['records', 'contributors'].includes(shaded) ? 'count' : 'number';
    const zips = MentatContributionMap.eligibleZips(rows).sort(), key = JSON.stringify({zips, congress: settings.congress, bbox: null});
    const cached = geometryCache.get(key);
    let geometry = cached && settings.layers.every(layer => cached.layers?.[layer]) ? cached : null;
    if (!geometry) {
      status('Loading Census boundaries…');
      geometry = await contributionAPI('geography', {zips, layers: settings.layers, congress: settings.congress});
      geometryCache.set(key, geometry);
      if (geometryCache.size > 8) geometryCache.delete(geometryCache.keys().next().value);
    }
    if (view.stale(ticket) || !verified()) return;
    dropMapsIn(host);
    const scopeOf = row => String(row.scope).split(' \u00b7 ')[0];
    const groupsOf = v => rows.filter(row => scopeOf(row) === v.label).map(row => ({...row, amount_cents: row.amount, value: row[shaded]}));
    const valueLabel = shaded === 'amount' ? (basisOf(view.state) === 'original_supported' ? 'Selected gross amount' : 'Selected net amount') : ((results[0].columns.find(c => c.id === shaded) || {}).name || shaded);
    const shared = {valueField: shaded === 'amount' ? 'amount_cents' : 'value', unit, geometry, layers: settings.layers, filled: compiled.spec.filled, showLayerControls: false, valueLabel};
    const scaleMax = Math.max(1e-9, ...values.map(v => MentatContributionMap.prepare(groupsOf(v), geometry, shared).marks.reduce((top, mark) => Math.max(top, Math.abs(mark.value)), 0)));
    const grid = document.createElement('div');
    grid.className = 'studio-compare-maps';
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(' + values.length + ',minmax(0,1fr));gap:12px;align-items:start';
    host.replaceChildren(grid);
    values.forEach((v, i) => {
      const cell = document.createElement('section'), head = document.createElement('h4'), holder = document.createElement('div');
      cell.className = 'studio-compare-map';
      head.className = 'studio-compare-map-title';
      head.textContent = v.label;
      head.style.cssText = 'margin:0 0 4px;font-size:13px;font-weight:600;color:' + colours[i];
      cell.append(head, holder);
      grid.append(cell);
      mapSlot(holder).map = MentatContributionMap.render(holder, Object.assign({}, shared, {groups: groupsOf(v), scaleMax, title: view.title + ' \u00b7 ' + v.label,
        onSelect: row => { if (view.primary) inspectContributions(contributions.mapSelection(row), 0, queries.length > 1 ? {request: queries[i], result: results[i]} : null); }}));
    });
    notes.push('Each compared scope is its own map on one shading scale, so an equal colour means an equal value.');
  }
  async function renderCompareView(view, compiled, ticket, again = {}) {
    const node = view.el, state = view.state, entry = compareState(state), sheetBasis = basisOf(state);
    const values = entry.values.slice(0, 3), kind = entry.kind, colours = scopeColours();
    if (kind !== 'industry' && !columns().some(f => f.id === kind)) throw new Error('This report has no ' + kind + ' field to compare.');
    const base = compiled.query;
    let queries;
    if (kind === 'industry') {
      queries = values.map(v => {
        const report = ((contributionCatalog && contributionCatalog.datasets) || []).find(d => d.id === v.value);
        if (!report || !report.available) throw new Error(v.label + ' has no verified report on this console.');
        return Object.assign(structuredClone(base), {dataset: report.id, industry: report.industry || report.id});
      });
    } else {
      const query = structuredClone(base);
      query.filters = (query.filters || []).filter(f => f.field !== kind);
      query.filters.push({field: kind, op: 'in', value: values.map(v => v.value)});
      if (!query.dimensions.includes(kind)) query.dimensions = [...query.dimensions, kind];
      queries = [query];
    }
    if (!view.request || JSON.stringify(view.request) !== JSON.stringify(queries[0])) {
      node('canvas').setAttribute('aria-busy', 'true'); node('canvas').__busyTicket = ticket; node('verified-summary').textContent = 'Reading verified totals…';
      status('Reading verified source observations…'); el('inspection').hidden = true; ++recordRevision; lastResult = null;
    }
    const results = await Promise.all(queries.map((query, i) => queryCached(query, {flight: compareFlights[i]}, again)));
    if (view.stale(ticket) || !verified()) return;
    view.result = results[0]; view.request = queries[0];
    failure(''); lastResult = results[0]; lastConfig = compiled;
    data.row_count = results.reduce((n, r) => n + r.totals.records, 0);
    data.rows = results[0].rows.map(row => data.columns.map(f => row[f.id] ?? ''));
    compareDrawn = {kind, values, queries, results};
    const labelOf = new Map(values.map(v => [String(v.value), v.label]));
    // A Colour field splits each scope further (census B-S32): a mark's series
    // is the scope and its Colour member together, "Goldman Sachs Bank NA · DEM".
    const colourDim = compiled.spec.color && compiled.spec.color !== kind ? compiled.spec.color : '';
    const plotted = compiled.spec.dimensions.filter(d => d !== kind && d !== colourDim);
    const memberOf = row => colourDim ? ' \u00b7 ' + (row[colourDim] == null || row[colourDim] === '' ? 'Unknown' : String(row[colourDim])) : '';
    const rank = new Map(values.map((v, i) => [v.label, i]));
    let rows = [];
    if (kind === 'industry') results.forEach((result, i) => { for (const row of result.rows) rows.push(Object.assign({}, row, {scope: values[i].label + memberOf(row), scopeOrder: i})); });
    else for (const row of results[0].rows) { const label = labelOf.get(String(row[kind])) || String(row[kind] ?? 'Unknown'); rows.push(Object.assign({}, row, {scope: label + memberOf(row), scopeOrder: rank.has(label) ? rank.get(label) : values.length})); }
    // Groups keep the order their largest scope gave them; inside a group the
    // scopes follow the pills, so each group reads as one cluster of bars.
    const groupKey = row => JSON.stringify(plotted.map(d => row[d] ?? null)), order = new Map();
    for (const row of rows) if (!order.has(groupKey(row))) order.set(groupKey(row), order.size);
    rows.sort((a, b) => order.get(groupKey(a)) - order.get(groupKey(b)) || a.scopeOrder - b.scopeOrder || String(a.scope).localeCompare(String(b.scope)));
    // The pills' own colours; with Colour, each scope-and-member series takes its
    // own palette colour and the legend names it.
    const colourMap = colourDim ? null : {};
    if (colourMap) values.forEach((v, i) => { colourMap[v.label] = colours[i]; });
    const scopeNames = [...new Set(rows.map(row => row.scope))];
    const name = id => (data.columns.find(f => f.id === id) || {}).name || id;
    // Final fix round (R03-5): when the compared field is the sheet's only
    // dimension, the bars are the scopes themselves: the title says so and
    // each bar carries its scope's name, not "Selected population".
    let title = contributions.BASIS[sheetBasis][0] + ' by ' + (plotted.length ? plotted.map(name).join(' / ') : kind === 'industry' ? 'Industry' : name(kind));
    if (compiled.spec.measures.length !== 1 || compiled.spec.measures[0] !== 'amount')
      title = compiled.spec.measures.map(id => (results[0].columns.find(c => c.id === id) || {}).name || id).join(' / ') + ' · ' + title;
    view.title = title;
    const gap = gapNotice(queries);
    view.coverage = gap;
    view.scope = withGap(results.map((result, i) => sourceScope(result, queries[i])).join(' | '), gap);
    node('title').textContent = title;
    node('title').title = title;
    node('subtitle').textContent = withGap(scopeSubtitle(state) + ' · compared: ' + values.map(v => v.label).join(' vs '), gap);
    // Totals: each report's own for industry; one query's combined totals for
    // cycle and company, said as combined (group identities are not additive).
    const money = value => contributionPlot.format(value, 'amount');
    const summary = node('verified-summary');
    summary.replaceChildren();
    const figures = [[sheetBasis === 'original_supported' ? 'Selected gross amount' : 'Selected net amount', r => money(r.totals.amount)],
      ['Source observations', r => fmt(r.totals.records)], ['Reported donor identities', r => fmt(r.totals.contributors)]];
    for (const [label, read] of figures) {
      const item = document.createElement('span');
      if (kind === 'industry') {
        results.forEach((result, i) => {
          const strong = document.createElement('strong'), dot = document.createElement('i');
          dot.className = 'studio-scope-dot studio-scope-dot--' + (i + 1); dot.setAttribute('aria-hidden', 'true');
          strong.append(dot, document.createTextNode(read(result)));
          strong.title = values[i].label;
          item.append(strong);
        });
        item.append(document.createTextNode(label));
      } else {
        const strong = document.createElement('strong');
        strong.textContent = read(results[0]);
        item.append(strong, document.createTextNode(label + ' · combined'));
        item.title = 'One total across the ' + values.length + ' compared ' + COMPARE_NOUN[kind];
      }
      summary.append(item);
    }
    summary.title = kind === 'industry' ? 'Each figure is its own report\u2019s total, in pill order.'
      : 'One query covers every compared ' + (kind === 'company' ? 'company' : kind) + '; identity counts are not added per scope.';
    const range = result => result.group_count ? fmt(result.offset + 1) + '–' + fmt(result.offset + result.rows.length) + ' of ' + fmt(result.group_count) : 'none';
    // Final fix round (R03-9): two or three reports say only their counts on
    // the note line (the full ranges, per industry, are in the title), so the
    // note text keeps the row instead of wrapping to three lines.
    const short = result => !result.group_count ? 'none' : result.offset || result.has_more ? range(result) : fmt(result.group_count);
    node('group-count').textContent = 'Groups ' + (results.length > 1 ? results.map(short) : results.map(range)).join(' · ');
    node('group-count').title = results.map((result, i) => (kind === 'industry' ? values[i].label + ' ' : '') + range(result)).join(' · ') + '. Totals cover every matching group of each scope';
    const more = results.some(result => result.has_more);
    node('groups-prev').disabled = !base.offset; node('groups-next').disabled = !more; node('result-controls').hidden = false;
    node('groups-prev').hidden = node('groups-next').hidden = !base.offset && !more;
    // One scope's records at a time for industry: pick a bar. The one query of
    // cycle or company already carries every compared scope.
    node('inspect-all').disabled = kind === 'industry';
    node('inspect-all').title = kind === 'industry' ? 'Choose a bar: each industry is its own report' : '';
    node('canvas').hidden = false; node('table').hidden = true;
    node('canvas').className = view.canvasClass + 'studio-canvas fit-' + state.fit;
    delete node('canvas').dataset.pieFit;
    // Compared scopes multiply the bars (groups x scopes). On the desktop grid
    // the main canvas is a fixed region, so the pitch shrinks from S1's 28px
    // toward 14px until the bars, the axis and the legend fit it; a board tile
    // or a narrow page grows with the chart instead. With a pane put away
    // (ruling c) the pitch may also grow, up to ROOM_PITCH, to fill the room.
    const fitHeight = node('canvas') === el('canvas') && !(window.matchMedia && window.matchMedia('(max-width: 1099px)').matches) ? roomHeight(node('canvas')) : 0;
    const scopeColumn = {id: 'scope', name: (kind === 'industry' ? 'Industry' : kind === 'cycle' ? 'Election cycle' : 'Company') + (colourDim ? ' \u00b7 ' + name(colourDim) : ''), role: 'dimension', type: 'category'};
    const spec = Object.assign({}, compiled.spec, {dimensions: [...plotted, 'scope'], labelDimensions: plotted.length ? plotted : ['scope'], color: 'scope', barPitch: 28, barPitchMin: 14, barPitchMax: fitHeight && chartRoomy() ? ROOM_PITCH : 0, fitHeight: fitHeight, niceTicks: true, panelTitles: compiled.spec.measures.length !== 1, width: node('canvas').clientWidth, barLeftMax: 190,
      colorMap: colourMap || undefined, title: title,
      columns: [...results[0].columns.filter(c => c.id !== kind || plotted.includes(kind)), scopeColumn]});
    const scopeAt = row => { const label = colourDim ? String(row.scope).split(' \u00b7 ')[0] : row.scope; return values.findIndex(v => v.label === label); };
    const pickBar = row => {
      const at = scopeAt(row);
      if (kind === 'industry' && at >= 0) inspectContributions(row, 0, {request: queries[at], result: results[at]});
      else inspectContributions(row);
    };
    const type = compiled.spec.type, notes = [...compiled.notes], options = chartOptions(state);
    const TIME_AXES = ['date', 'year', 'cycle', 'quarter', 'month'];
    let transform = compiled.transform;
    if (transform !== 'none' && transform !== 'annual' && !TIME_AXES.includes(plotted[0])) {
      notes.push('Period analysis needs a time axis other than the compared field, so the compared scopes are drawn without it.');
      transform = 'none';
    }
    if (type !== 'map') dropMapsIn(node('canvas'));
    try {
      if (transform !== 'none' && transform !== 'annual') {
        // Each compared scope (and Colour member) is its own series over the periods.
        const temporal = periodModel(rows, Object.assign({}, spec, {dimensions: [...plotted, 'scope']}), compiled, state, notes);
        const measure = temporal.column.name + (temporal.column.unit === 'cents' ? ' (USD)' : '');
        const drawn = paint.chartSVG(temporal.model, measure, {type: temporal.model.type || type, aggregation: 'sum', x: 0, y: 1}, {sheet: sheetName(), columns: [{name: 'Period'}, {name: measure}]}, title, view.scope, screenOptions(view, options, temporal.model.type || type));
        node('canvas').replaceChildren(drawn.svg);
        placeFitted(drawn.svg);
        const lines = core.referenceValues(temporal.model, state.analytics);
        if (lines.length && drawn.plot) overlayReference(drawn.svg, drawn.plot, lines);
      } else if (type === 'map') {
        await drawCompareMaps(view, compiled, rows, values, colours, queries, results, notes, ticket);
        if (view.stale(ticket) || !verified()) return;
      } else if (CANVAS_TYPES.includes(type)) {
        // A pie, doughnut, waterfall, Pareto or radar: each group and scope is one
        // mark, coloured by its scope.
        const ids = compiled.spec.measures, id = ids[0], currency = contributions.MONEY.has(id), nameOf = m => (results[0].columns.find(c => c.id === m) || {}).name || m;
        if (ids.length > 1) notes.push(markTypeName(type) + ' draws one measure; ' + ids.slice(1).map(nameOf).join(', ') + (ids.length === 2 ? ' is' : ' are') + ' in each mark\u2019s hover detail.');
        let groups = rows.map(row => ({label: [...plotted.map(d => row[d] == null || row[d] === '' ? 'Unknown' : String(row[d])), row.scope].join(' / '), value: row[id] / (currency ? 100 : 1), n: 1,
          seriesIndex: scopeNames.indexOf(row.scope), extra: Object.fromEntries(ids.slice(1).map(m => [nameOf(m), row[m] == null ? null : row[m] / (contributions.MONEY.has(m) ? 100 : 1)]))}));
        if (SHARE_TYPES.includes(type)) { const negative = groups.filter(g => g.value < 0).length; if (negative) { groups = groups.filter(g => g.value >= 0); notes.push(fmt(negative) + ' group' + (negative === 1 ? ' has' : 's have') + ' a negative ' + nameOf(id) + ' and ' + (negative === 1 ? 'is' : 'are') + ' left out of the ' + markTypeName(type).toLowerCase() + '; bars show signed amounts.'); } }
        const drawn = paint.chartSVG({type, groups, selected: results.reduce((n, r) => n + r.totals.records, 0), excluded: 0, warnings: []}, nameOf(id) + (currency ? ' (USD)' : ''), {type, aggregation: 'sum', x: 0, y: 1}, {sheet: sheetName(), columns: [{name: 'Group'}, {name: id}]}, title, view.scope, screenOptions(view, options, type));
        node('canvas').replaceChildren(drawn.svg);
        placeFitted(drawn.svg);
        const lines = core.referenceValues({type, groups}, state.analytics);
        if (lines.length && drawn.plot) overlayReference(drawn.svg, drawn.plot, lines);
      } else if (MentatContributionCharts.TYPES.includes(type)) {
        // The analytical families draw the scope as their Colour (contract V08).
        MentatContributionCharts.render(node('canvas'), rows, spec, pickBar);
      } else {
        contributionPlot.render(node('canvas'), rows, spec, pickBar);
        // Still taller than the region at 14px: the canvas scrolls with a visible
        // bar (drawn again at the width the bar leaves) and the counter says so.
        if (fitHeight && node('canvas').scrollHeight > node('canvas').clientHeight + 1) {
          node('canvas').classList.add('studio-canvas--scrolls');
          spec.width = node('canvas').clientWidth;
          contributionPlot.render(node('canvas'), rows, spec, pickBar);
          node('group-count').textContent += ' · scroll the chart';
          node('group-count').title += '. The chart is taller than its region: scroll it for every bar and the legend';
        }
      }
    } catch (error) { drawFallback(node, rows, spec, error.message, notes, pickBar); }
    if (state.analytics.average || state.analytics.median) notes.push(node('canvas').querySelector('[data-reference]')
      ? 'Reference lines mark the mean and median of the shown marks.' : 'This chart has no value axis for a reference line, so the mean and median are not drawn; each mark carries its value on hover.');
    const records = results.reduce((n, result) => n + result.totals.records, 0), groupCount = results.reduce((n, result) => n + result.group_count, 0);
    node('note').textContent = [contributions.BASIS[sheetBasis][1], 'Comparing ' + values.map(v => v.label).join(', ') + '.',
      kind === 'industry' ? 'Each industry is its own report and its own query.' : 'One verified query covers every compared ' + (kind === 'company' ? 'company' : kind) + '.',
      more ? 'Additional groups are on later pages; each scope pages its own top groups.' : 'All selected groups shown.', ...notes].join(' ');
    node('status').textContent = fmt(records) + ' source observations · ' + fmt(groupCount) + ' groups · ' +
      (results.every(result => result.reconciliation?.receipt_matches_workbook === true) ? 'reconciles to the workbook' : 'verified source records');
    node('status').title = fmt(queryRequests) + ' verified queries this session';
    status('Verified contribution fields · ' + data.columns.length + ' fields');
    renderPager();
    renderReconcile(results[0], view, ticket).catch(() => { node('reconcile').hidden = true; });
  }

  /* ------------------------------------------------------- reconciliation */

  // The Graph controls that have no verified field. They are read at the
  // moment of the comparison, because they decide which question Graph was
  // asked, and therefore which difference has to be explained.
  // Graph's current scope, read through MentatScope (the URL scope Graph
  // writes, contracts/url_scope.json) and, where that module is not loaded,
  // from Graph's own controls.
  function currentGraphScope() {
    const url = globalThis.MentatScope && typeof globalThis.MentatScope.read === 'function' ? globalThis.MentatScope.read() : null;
    const dom = id => (document.getElementById('graph-' + id) || {}).value || '';
    const pick = key => url ? String(url[key] || '') : dom(key);
    return {company: pick('company'), cycle: pick('cycle'), tier: pick('tier'), funding: pick('funding') || 'all'};
  }
  const graphFilterState = () => {
    const now = currentGraphScope();
    return {tier: now.tier, funding: now.funding};
  };

  // The same question, asked of Graph. One request per scope, remembered for
  // the session, because the strip is redrawn on every display edit and a
  // reconciliation must never cost a query the reader did not ask for.
  const graphTotalsCache = new Map();
  // The strip is often the first thing a reader opens, on a console where the
  // Graph view has never rendered and #graph-run is therefore still empty - and
  // an empty select made every reconciliation refuse itself. It cannot guess a
  // run either: reconciling against a run Graph would not display is worse than
  // no strip at all. So it calls the very chooser Graph calls, which prefers the
  // selected run, skips runs that are only ready or awaiting review, and walks
  // on past any run whose catalog has no industries.
  let graphRunPromise = null;
  function graphRunId() {
    const chosen = (document.getElementById('graph-run') || {}).value || '';
    if (chosen) return Promise.resolve(chosen);
    const chooser = globalThis.MentatGraphRun;
    if (!chooser) return Promise.resolve('');
    if (!graphRunPromise) {
      graphRunPromise = chooser.choose()
        .then(result => {
          // Only a run Graph would actually display counts, and only a real
          // answer is worth remembering: caching '' would leave the strip dead
          // for the rest of the session over one bad moment.
          const id = (result && result.catalog && result.run) ? result.run.id : '';
          if (!id) graphRunPromise = null;
          return id;
        })
        .catch(() => { graphRunPromise = null; return ''; });
    }
    return graphRunPromise;
  }
  async function graphTotals(company, cycle) {
    const run = await graphRunId();
    const industry = (dataset() || {}).id || '';
    if (!run || !industry) return Promise.reject(new Error('Graph has no run selected.'));
    const chosen = graphFilterState();
    const params = new URLSearchParams({id: run, industry: industry, company: company || '',
      tier: chosen.tier, cycle: core.graphCycleValue(cycle), funding: chosen.funding,
      mode: 'direct', direction: '', category: '', recipient: ''});
    // Graph's own first paint asks for latest=1, and the server answers it by
    // pinning the newest cycle it holds. A sheet that carries no cycle of its own
    // has to ask that same question, or the strip quotes a figure covering every
    // cycle at once and calls it what Graph is showing on screen.
    if (!core.graphCycleValue(cycle)) params.set('latest', '1');
    const key = params.toString();
    if (graphTotalsCache.has(key)) return graphTotalsCache.get(key);
    const promise = Promise.resolve(api('/api/graph?' + params))
      .catch(error => { graphTotalsCache.delete(key); throw error; });
    graphTotalsCache.set(key, promise);
    return promise;
  }

  // The strip only means anything when both views were asked the same
  // question, so it fires for a sheet whose row filters are a company, a
  // cycle, or both - each one exact single value - and nothing else.
  function reconcileScope(state) {
    const meta = core.classifyFields(columns(), state.types);
    const scope = {company: '', cycle: '', funding_source: ''};
    for (const filter of state.filters || []) {
      if (!core.filterActive(filter)) continue;
      const field = meta.fields.find(f => f.index === Number(filter.column));
      const id = field ? field.id : '';
      const values = core.filterValues(filter);
      if ((id !== 'company' && id !== 'cycle' && id !== 'funding_source') || filter.stage === 'aggregate' ||
          ['equals', 'in'].indexOf(filter.op) === -1 || values.length !== 1 || scope[id]) return null;
      scope[id] = values[0];
    }
    return scope;
  }

  // A Graph request that fails, or a scope Graph cannot express, leaves the
  // strip away entirely. An absent strip is honest; a guessed one is not.
  const FUNDING_OF_SOURCE = {employee_original: 'personal', corporate_original: 'corporate'};
  const FUNDING_TEXT = {all: 'all giving', personal: 'personal giving', corporate: 'corporate giving'};
  const cyclePair = value => value ? String(core.carriedFilterValue('cycle', String(value))) : '';
  function setWhy(open) {
    el('reconcile-detail').hidden = !open;
    el('reconcile-why').setAttribute('aria-expanded', String(!!open));
  }
  async function renderReconcile(result, view, ticket) {
    view = view || mainSurface();
    const node = view.el('reconcile');
    const main = node === el('reconcile');
    if (main) return renderNoteLine(result, view, ticket);
    node.hidden = true;
    if (!verified() || !result) return;
    const scope = reconcileScope(view.state);
    if (!scope) return;
    let graphView;
    try { graphView = await graphTotals(scope.company, scope.cycle); }
    catch { return; }
    if (view.stale(ticket) || !verified()) return;
    // Whatever cycle Graph pinned is the cycle this sentence has to name. Saying
    // "All cycles" while Graph displays one was the strip describing a screen
    // nobody was looking at.
    const graphCycle = String(((graphView || {}).filters || {}).cycle || '');
    const state = contributions.reconcile(result, basisOf(view.state), graphView,
      Object.assign({}, graphFilterState(), {studioCycle: scope.cycle, graphCycle: graphCycle}));
    if (state.reasonId === 'unavailable') return;
    const money = value => contributionPlot.format(value, 'amount');
    const where = [scope.company || 'All companies',
      scope.cycle || graphCycle || 'All cycles'].join(' · ');
    const lead = document.createElement('span');
    lead.textContent = 'Graph shows ' + money(state.graphCents) + ' for ' + where +
      '. Studio shows ' + money(state.studioCents) + '. ';
    const verdict = document.createElement('strong');
    verdict.className = state.agree ? 'studio-reconcile-agree' : 'studio-reconcile-differ';
    verdict.textContent = state.agree ? 'Agree.'
      : 'They differ by ' + money(Math.abs(state.differenceCents)) + ' because ' + state.reason + '.';
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'studio-reconcile-link';
    link.textContent = 'Why Cross-reference differs';
    link.onclick = () => {
      globalThis.MentatReturn?.arrive('chart-studio', 'cross-reference');
      if (typeof showView === 'function') showView('cross-reference');
      else location.hash = '#cross-reference';
    };
    node.replaceChildren(lead, verdict, document.createTextNode(' '), link);
    node.hidden = false;
  }
  // The main canvas's note line. It names how the sheet's scope differs from
  // Graph's, offers to match it, and keeps today's full explanation behind
  // "Why". It hides when the scopes and the totals agree. No red.
  async function renderNoteLine(result, view, ticket) {
    const node = el('reconcile'), text = el('reconcile-text'), match = el('match-graph'), detail = el('reconcile-detail');
    node.hidden = true; match.hidden = true; graphMatchTarget = null;
    if (!verified() || !result) { setWhy(false); return; }
    if (compareActive(view.state)) {
      text.textContent = 'Graph reconciliation pauses while this sheet compares scopes.';
      detail.replaceChildren(); el('reconcile-why').hidden = true; setWhy(false);
      node.hidden = false;
      return;
    }
    el('reconcile-why').hidden = false;
    const scope = reconcileScope(view.state);
    if (!scope) { setWhy(false); return; }
    const graphNow = currentGraphScope();
    const studioFunding = FUNDING_OF_SOURCE[scope.funding_source] || 'all';
    let graphView, graphHere;
    try {
      [graphView, graphHere] = await Promise.all([graphTotals(scope.company, scope.cycle), graphTotals(graphNow.company, graphNow.cycle)]);
    } catch { return; }
    if (view.stale(ticket) || !verified()) return;
    const graphCycle = String(((graphView || {}).filters || {}).cycle || '');
    const shownCycle = cyclePair(graphNow.cycle || ((graphHere || {}).filters || {}).cycle || '');
    const state = contributions.reconcile(result, basisOf(view.state), graphView,
      Object.assign({}, graphFilterState(), {funding: studioFunding === graphNow.funding ? 'all' : graphNow.funding,
        fundingClassification: studioFunding !== 'all' && studioFunding === graphNow.funding,
        studioCycle: scope.cycle, graphCycle: graphCycle}));
    // Why: the sentence and the verdict the strip used to print, the way to
    // Cross-reference, and the caveat paragraph that sat under the chart.
    const money = value => contributionPlot.format(value, 'amount');
    const lead = document.createElement('span');
    const verdict = document.createElement('strong');
    if (state.reasonId === 'unavailable') {
      lead.textContent = 'Graph has no total for this scope.';
      verdict.textContent = '';
    } else {
      const where = [scope.company || 'All companies', scope.cycle || cyclePair(graphCycle) || 'All cycles'].join(' · ');
      lead.textContent = 'Graph shows ' + money(state.graphCents) + ' for ' + where + '. Studio shows ' + money(state.studioCents) + '. ';
      verdict.className = state.agree ? 'studio-reconcile-agree' : 'studio-reconcile-diff';
      verdict.textContent = state.agree ? 'Agree.'
        : 'They differ by ' + money(Math.abs(state.differenceCents)) + ' because ' + state.reason + '.';
    }
    const why = document.createElement('p');
    why.append(lead, verdict);
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'studio-reconcile-link';
    link.textContent = 'Why Cross-reference differs';
    link.onclick = () => {
      setWhy(false);
      globalThis.MentatReturn?.arrive('chart-studio', 'cross-reference');
      if (typeof showView === 'function') showView('cross-reference');
      else location.hash = '#cross-reference';
    };
    const caveat = document.createElement('p');
    caveat.className = 'studio-reconcile-caveat';
    caveat.textContent = el('note').textContent;
    detail.replaceChildren(why, link, caveat);
    // The scopes, named from the sheet's filters and Graph's current scope.
    const differ = [];
    if (scope.cycle !== shownCycle) differ.push([scope.cycle || 'all cycles', shownCycle || 'all cycles']);
    if (scope.company !== graphNow.company) differ.push([scope.company || 'all companies', graphNow.company || 'all companies']);
    if (studioFunding !== graphNow.funding) differ.push([FUNDING_TEXT[studioFunding] || studioFunding, FUNDING_TEXT[graphNow.funding] || graphNow.funding]);
    const level = graphNow.tier ? ' Graph also narrows to the ' + graphNow.tier + ' employee level, which verified data cannot match.' : '';
    if (!differ.length && !graphNow.tier && state.agree) { setWhy(false); return; }
    if (differ.length) {
      text.textContent = 'This sheet covers ' + differ.map(d => d[0]).join(' and ') + '. Graph shows ' + differ.map(d => d[1]).join(' · ') + '.' + level;
      graphMatchTarget = {company: graphNow.company, cycle: shownCycle,
        funding: Object.keys(FUNDING_OF_SOURCE).find(key => FUNDING_OF_SOURCE[key] === graphNow.funding) || ''};
      match.hidden = false;
    } else if (level) {
      text.textContent = level.trim();
    } else {
      text.textContent = 'Same scope as Graph; the totals differ by ' + money(Math.abs(state.differenceCents || 0)) + '.'
        + (state.reasonId === 'funding_classification' ? ' Graph uses published giving categories; this sheet uses verified funding classifications.' : '');
    }
    node.hidden = false;
  }
  // Match Graph scope: the sheet's cycle, company and giving type become
  // Graph's. Employee level has no verified field, so it is left as it is and
  // the note line says so.
  function matchGraphScope() {
    const target = graphMatchTarget;
    if (!target) return;
    commit(() => {
      const idOf = filter => (columns()[Number(filter.column)] || {}).id;
      const kept = (sheet().filters || []).filter(filter => !['company', 'cycle', 'funding_source'].includes(idOf(filter)));
      const add = [];
      for (const [id, value] of [['company', target.company], ['cycle', target.cycle], ['funding_source', target.funding]]) {
        const column = columns().find(f => f.id === id);
        if (column && value) add.push({column: column.index, op: 'equals', value: String(value), stage: 'row'});
      }
      sheet().filters = [...kept, ...add];
    });
    setWhy(false);
  }

  // FIX 4: the Coverage gap chip sits with the scope chips whenever the selected chart reads a short cell.
  // Its caption is the subtitle under the chart's title; the chip's tooltip carries it and its source.
  function paintGapChip(host) {
    for (const old of host.querySelectorAll('.coverage-gap-chip')) old.remove();
    if (verified() && lastCoverage && globalThis.MentatCoverage) host.append(globalThis.MentatCoverage.chipNode(lastCoverage));
  }
  function setCoverage(value) {
    lastCoverage = value;
    if (el('scope-chip')) paintGapChip(el('scope-chip'));
  }
  function renderScopeChip() {
    // The Data source button is permanent. The one way back to the report
    // picker must never depend on there being a scope to describe.
    const host = el('scope-chip');
    host.replaceChildren();
    // Lane P: a map sheet's layers read their own sources, so the chip names them
    // (map-studio.js scopeChip); any other sheet, or a map of the builder's own
    // industry and basis, keeps today's scope exactly.
    const mapScope = verified() && globalThis.MentatMapStudio && MentatMapStudio.scopeChip ? MentatMapStudio.scopeChip(sheet()) : null;
    for (const part of (mapScope ? mapScope.text : scopeText()).split(' · ').filter(Boolean)) {
      const chip = document.createElement('span');
      chip.textContent = part;
      host.append(chip);
    }
    paintGapChip(host);
    // What the hand-off could not bring across is stated beside the scope,
    // not left for the reader to meet as a number that looks too large.
    const notes = (appliedScope && appliedScope.notCarried) || [];
    el('scope-note').textContent = notes.join(' · ');
    el('scope-note').hidden = !notes.length;
    const basisLabel = mapScope ? mapScope.basis : verified() ? ((contributions.BASIS[basis()] || [])[0] || '') : (data ? 'Worksheet values' : '');
    el('scope-basis').textContent = host.children.length ? basisLabel : 'Choose data';
    // Lane P: the button's title lists a map's sources in full, each with its basis (the chip
    // may be cut short); the title the template gave it comes back for any other sheet.
    const change = el('scope-change');
    if (mapScope) { if (change.__sourceTitle == null) change.__sourceTitle = change.title; change.title = mapScope.title; }
    else if (change.__sourceTitle != null) { change.title = change.__sourceTitle; change.__sourceTitle = null; }
    renderDataSources();
  }

  // Lanes G1/G1b (board #47, 2026-09-27): a sheet that reads more than one source lists them at the top of
  // the Data pane, one row each - a swatch in the colour its first layer draws, its name and what kind of
  // source it is ("Crypto · published workbook", "PAC evidence · FAIRSHAKE · Spent 2023–2024"; core.
  // sheetSources). Only a map's layers read sources of their own; a sheet of one source shows no list.
  // The list sits above the field search (under the Data | Map layers switch when a map shows it); the
  // Map layers side of the pane (map-studio.js) hides it with the rest of the Data side and brings it
  // back with it (its data-map-hidden mark).
  function renderDataSources() {
    const pane = document.getElementById('studio-pane-data');
    if (!pane) return;
    const rows = verified() && core.sheetSources ? core.sheetSources(sheet(), globalThis.MentatMapLayers, contributions.BASIS, columns()) : [];
    const want = rows.length > 1;
    let section = el('data-sources');
    if (!section && !want) return;
    if (!section) {
      section = document.createElement('section');
      section.id = 'studio-data-sources';
      section.className = 'studio-data-sources';
      section.setAttribute('aria-labelledby', 'studio-data-sources-title');
      const heading = document.createElement('h3');
      heading.id = 'studio-data-sources-title';
      heading.textContent = 'Data sources';
      const list = document.createElement('ul');
      list.className = 'studio-data-source-list';
      section.append(heading, list);
      // buildFromMenu wraps the search in a .studio-search-row; before either, else after the side switch.
      const search = pane.querySelector(':scope > .studio-search-row, :scope > .studio-search');
      const after = pane.querySelector(':scope > .studio-map-pane') || pane.querySelector(':scope > .studio-side-switch');
      if (search) pane.insertBefore(section, search);
      else if (after) after.after(section);
      else pane.prepend(section);
    }
    section.querySelector('.studio-data-source-list').replaceChildren(...rows.map(row => {
      const item = document.createElement('li');
      item.className = 'studio-data-source';
      item.title = row.title;
      const swatch = document.createElement('span');
      swatch.className = 'studio-data-source-swatch';
      swatch.setAttribute('aria-hidden', 'true');
      swatch.style.backgroundColor = row.colour;
      const words = document.createElement('span');
      words.className = 'studio-data-source-words';
      const name = document.createElement('span');
      name.className = 'studio-data-source-name';
      name.textContent = row.name;
      const kind = document.createElement('span');
      kind.className = 'studio-data-source-kind';
      kind.textContent = ' \u00b7 ' + row.kind;
      words.append(name, kind);
      item.append(swatch, words);
      return item;
    }));
    const mapSide = !!pane.querySelector(':scope > .studio-side-switch #studio-side-map[aria-selected="true"]');
    if (mapSide) {
      section.hidden = true;
      if (want) section.dataset.mapHidden = '1'; else delete section.dataset.mapHidden;
    } else {
      section.hidden = !want;
      delete section.dataset.mapHidden;
    }
  }

  // Canvas only hides the builder surfaces; the control that does it never
  // hides, so there is always a way back.
  function setOptionsHidden(hidden) {
    const host = document.querySelector('#view-chart-studio .studio');
    if (host) host.classList.toggle('studio-options-hidden', !!hidden);
    syncViewMode();
    // Canvas only puts the Inspector behind its header button the way a narrow
    // window does: the filter count stays readable and the panel stays off the
    // chart until it is asked for.
    const narrow = !!(window.matchMedia && window.matchMedia('(max-width: 1099px)').matches);
    const closed = !!hidden || narrow;
    el('inspector').hidden = closed;
    el('inspector-toggle').setAttribute('aria-expanded', String(!closed));
  }
  // Builder, Company overview and Canvas are three states of one thing, so they
  // are one radiogroup (item 13). Before this, Company overview was a separate
  // toggle beside a two-way segment and both could look "on" at once.
  function syncViewMode() {
    const host = document.querySelector('#view-chart-studio .studio');
    const hidden = !!(host && host.classList.contains('studio-options-hidden'));
    const state = overviewMode ? 'overview' : hidden ? 'canvas' : 'builder';
    for (const [id, name] of [['view-builder', 'builder'], ['overview-toggle', 'overview'], ['view-canvas', 'canvas']]) {
      const on = state === name;
      el(id).setAttribute('aria-checked', String(on));
      if (id === 'overview-toggle') el(id).setAttribute('aria-pressed', String(on));
      el(id).tabIndex = on ? 0 : -1;
    }
  }
  function setViewMode(mode) {
    const was = overviewMode;
    overviewMode = mode === 'overview';
    if (overviewMode) el('inspection').hidden = true;
    if (!overviewMode) setOptionsHidden(mode === 'canvas');
    if (overviewMode !== was || (board && board.tiles > 1)) renderAll(); else syncViewMode();
  }
  function setInspectorTab(name) {
    inspectorTab = ['marks', 'analytics', 'filters'].includes(name) ? name : 'marks';
    for (const id of ['marks', 'analytics', 'filters']) {
      const selected = id === inspectorTab;
      el('tab-' + id).setAttribute('aria-selected', String(selected));
      el('tab-' + id).tabIndex = selected ? 0 : -1;
      el('pane-' + id).hidden = !selected;
    }
  }


  /* ----------------------------------------------------------------- wiring */

  // One mark list in both sources (census A-W06).
  fill(el('mark'), core.ALL_MARK_TYPES);
  for (const [zone] of ZONES) dropTarget(el(zone), zone);
  // The view applies Analytics objects dropped on it. It also accepts a pill and
  // does nothing with it: the drop has to fire so that dragend does not read the
  // gesture as "dragged off the shelves" and delete the field. The effect stays
  // 'move' because a dropEffect of 'none' cancels the drop event itself.
  el('canvas-wrap').addEventListener('dragover', event => {
    if (!dragging) return;
    if (dragging.kind === 'analytics') {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
      el('canvas-wrap').classList.add('is-over');
      return;
    }
    if (dragging.kind !== 'pill') return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  });
  el('canvas-wrap').addEventListener('dragleave', () => el('canvas-wrap').classList.remove('is-over'));
  el('canvas-wrap').addEventListener('drop', event => {
    el('canvas-wrap').classList.remove('is-over');
    if (!dragging) return;
    event.preventDefault();
    dropped = true;
    const move = dragging;
    dragging = null;
    if (move.kind === 'analytics') applyAnalytics(move.id);
  });

  el('undo').onclick = () => step(history, future);
  el('redo').onclick = () => step(future, history);
  el('swap').onclick = () => commit(() => { sheets[active] = core.swapShelves(sheet()); });
  // Verified: a radio group. Pressing an order that asks the server the same
  // question as the one on screen is not an edit, so it costs no undo step,
  // no persist and no re-render - and 'none' stays a valid stored value, so no
  // saved view is migrated. Worksheet: the existing toggle-back-to-none.
  const chooseSort = direction => {
    const current = sheet().sort || 'none';
    if (verified()) {
      const compiled = verifiedCompile();
      // None on a category axis is the groups' own order ('source'): the same
      // query as Descending, listed client-side, so it is a real edit.
      if (direction === 'none' && compiled && !contributions.orderedAxis(compiled.query.dimensions)) direction = 'source';
      const same = current === direction || (compiled && JSON.stringify(contributions.querySort({sort: direction},
        compiled.query.dimensions, compiled.spec.measures)) === JSON.stringify(compiled.query.sort) &&
        contributions.naturalOrder({sort: direction}, compiled.query.dimensions) === contributions.naturalOrder({sort: current}, compiled.query.dimensions));
      if (same) return;
      commit(() => { sheet().sort = direction; });
      return;
    }
    commit(() => { sheet().sort = current === direction ? 'none' : direction; });
  };
  el('sort-asc').onclick = () => chooseSort('asc');
  el('sort-desc').onclick = () => chooseSort('desc');
  el('sort-none').onclick = () => chooseSort('none');
  if (el('sort-az')) el('sort-az').onclick = () => chooseSort('az');
  if (el('sort-za')) el('sort-za').onclick = () => chooseSort('za');
  // A choice closes the Sort menu, the way a menu does.
  el('sort').addEventListener('click', event => { if (event.target.closest('#studio-sort > div button')) el('sort').open = false; });
  el('labels').onclick = () => commit(() => { sheet().showLabels = !sheet().showLabels; });
  el('fit').onchange = () => commit(() => { sheet().fit = el('fit').value; });
  el('clear').onclick = () => commit(() => { sheets[active] = core.clearSheet(sheet()); });
  el('duplicate').onclick = () => commit(() => {
    const copy = JSON.parse(JSON.stringify(sheet()));
    copy.id = 's' + Date.now();
    copy.name = nextName();
    sheets.splice(active + 1, 0, copy);
    // The copy takes the selected tile, because the copy is what the reader is
    // about to edit; every placed sheet above it moves up one with the splice.
    shiftBoard(active + 1);
    if (board) { const at = board.slots.indexOf(active); if (at >= 0) board.slots[at] = active + 1; }
    active += 1;
    dataSourceTab = false;
  });
  el('new-sheet').onclick = () => commit(() => {
    const fresh = core.createSheet(nextName(), 's' + Date.now());
    fresh.source = Object.assign({}, sheet().source);
    sheets.push(fresh);
    if (board) { const at = board.slots.indexOf(active); board.slots[at >= 0 ? at : 0] = sheets.length - 1; }
    active = sheets.length - 1;
    dataSourceTab = false;
  });
  el('sheet-source').onclick = () => { dataSourceTab = true; renderAll(); };
  el('download-png').onclick = () => download('png');
  el('download-svg').onclick = () => download('svg');
  el('download-csv').onclick = () => download('csv');
  el('copy-image').onclick = () => copyImage();
  el('print-chart').onclick = () => printChart();
  for (const tiles of BOARD_TILES) el('board-' + tiles).onclick = () => setBoard(tiles);
  el('board-briefing').onclick = () => boardToBriefing();
  el('save-view').onclick = () => saveView();
  el('open-views').onclick = () => openViews();
  // The tray itself lives in chart-briefing.js. A console that is not yet
  // serving it says so rather than answering the click with nothing.
  const toBriefing = () => {
    if (!window.MentatBriefing) {
      failure('Briefing needs a console restart.');
      return;
    }
    window.MentatBriefing.capture(currentChart());
  };
  el('briefing-add').onclick = toBriefing;
  // Show Me lives in exactly one place in the DOM - the Inspector's own
  // disclosure (item 18) - and the toolbar button opens that disclosure rather
  // than a second copy of it: it brings the Inspector back if it is closed,
  // puts the Marks pane in front, and scrolls the block into view.
  el('showme-toggle').onclick = () => {
    const details = el('showme-details'), open = !details.open;
    // Show Me lives in the Inspector, so opening it also ends Expand chart.
    if (open) { setInspectorCollapsed(false, true); if (roomState.expand) setRoom('expand', false, true); }
    details.open = open;
    rememberFold('showme', open);
    if (open) {
      if (el('inspector').hidden) {
        el('inspector').hidden = false;
        el('inspector-toggle').setAttribute('aria-expanded', 'true');
      }
      setInspectorTab('marks');
      details.scrollIntoView({block: 'nearest'});
    }
    renderToolbar();
  };
  el('showme-close').onclick = () => { el('showme-details').open = false; rememberFold('showme', false); renderToolbar(); };
  el('classic').onclick = () => {
    // The classic dialog only scans its report list while the Graph view is
    // showing (charts.js activate()), so go there first and then open it.
    globalThis.MentatReturn?.arrive('chart-studio', 'graph');
    if (typeof showView === 'function') showView('graph');
    const dialog = document.getElementById('graph-builder');
    // Dismissing the dialog used to leave the reader standing in Graph, which
    // is not the tab they pressed Classic in. The dialog hands the tab back.
    dialog.addEventListener('close', () => {
      if (typeof showView === 'function') showView('chart-studio');
      else location.hash = '#chart-studio';
    }, {once: true});
    dialog.showModal();
    dialog.dispatchEvent(new Event('mentat:open'));
  };
  el('mark').onchange = () => setMarkType(el('mark').value);
  // Marks v3: Synchronize axes, and the Colour and Shape popovers.
  el('sync-axes').onchange = () => commit(() => { if (el('sync-axes').checked) sheet().syncAxes = true; else delete sheet().syncAxes; });
  el('color-options').onclick = () => openColourPopover(el('color-options'));
  el('shape-options').onclick = () => openShapePalette(el('shape-options'));
  el('aggregation').onchange=()=>{const value=el('aggregation').value;commit(()=>{const meta=core.classifyFields(columns(),sheet().types);for(const zone of ['rows','columns']){const pills=core.shelf(sheet()[zone]),at=pills.findIndex(p=>core.fieldAt(meta,p.column)?.role==='measure');if(at>=0){pills[at].aggregation=value;sheet()[zone]=pills;break;}}});};
  buildFromMenu();
  el('search').oninput = renderFields;
  el('field-table').onchange=renderFields;
  el('bin-width').onchange=()=>{if(el('bin-width').reportValidity())commit(()=>{sheet().params.binWidth=Number(el('bin-width').value);});};
  el('run').onchange = loadReports;
  el('report').onchange = loadSheets;
  el('sheet').onchange = () => scan(false);
  el('rescan').onclick = () => scan(true);
  el('source-mode').onchange=()=>{compareBySheet.clear();persist();data=null;sourceKey='';for(const host of [...mapHosts.keys()])dropMap(host);abortContributionRequests();tileMemory.clear();++contributionRevision;el('inspection').hidden=true;renderAll();if(verified())loadContributions();else{fill(el('mark'),core.ALL_MARK_TYPES);loadRuns();}};
  el('dataset').onchange=()=>{persist();selectContributionDataset();};
  el('basis').onchange=()=>commit(()=>{sheet().source.basis=el('basis').value;activeSurface().offset=0;});
  el('refresh-data').onclick=()=>{++contributionCacheEpoch;abortContributionRequests();contributionCache.clear();memberCache.clear();graphTotalsCache.clear();loadContributions(true);};
    // The pager pages the tile the reader is looking at, and redraws that tile.
  // A page is the group limit, or the server's 100,000 when every group is asked for.
  const pageStep=view=>view.state.params.maxGroups==='all'?100000:Number(view.state.params.maxGroups)||60;
  el('groups-prev').onclick=()=>{const view=activeSurface();view.offset=Math.max(0,view.offset-pageStep(view));renderView(view);};
  el('groups-next').onclick=()=>{const view=activeSurface();view.offset+=pageStep(view);renderView(view);};
  el('inspect-all').onclick=()=>inspectContributions();
  el('inspection-close').onclick=()=>{++recordRevision;el('inspection').hidden=true;};
  el('record-prev').onclick=()=>inspectContributions(recordSelection,Math.max(0,recordOffset-100),recordSource);
  el('record-next').onclick=()=>inspectContributions(recordSelection,recordOffset+100,recordSource);
  for(const id of ['layer-states','layer-districts','layer-zips','congress'])el(id).onchange=()=>commit(()=>{sheet().map={congress:el('congress').value,layers:[['states','states'],['districts','districts'],['zips','zip_areas']].filter(([id])=>el('layer-'+id).checked).map(([,layer])=>layer)};});
  el('pager-prev').onclick = () => stepPage(-1);
  el('pager-next').onclick = () => stepPage(1);
  el('pager-play').onclick = togglePlay;
  for (const [id, apply] of [
    ['param-start', v => { sheet().params.start = v; }],
    ['param-end', v => { sheet().params.end = v; }],
    ['param-limit', v => { sheet().params.limit = v; }],
    ['param-groups', v => { sheet().params.maxGroups = v; }],
    ['param-horizon', v => { sheet().analytics.horizon = v || 2; }]
  ]) el(id).onchange = () => commit(() => apply(Number(el(id).value) || 0));
  el('param-period').onchange = () => commit(() => { sheet().analytics.period = el('param-period').value; });
  el('param-subject').onchange = () => commit(() => { sheet().params.subject = el('param-subject').value; });
  el('param-split').onchange = () => commit(() => { sheet().params.splitMulti = el('param-split').checked; });
  // Census B-E21 / B-E22: every category label on the axis, every legend member.
  if (el('param-axis-labels')) el('param-axis-labels').onchange = () => commit(() => { sheet().params.axisLabels = el('param-axis-labels').checked ? 'all' : ''; });
  if (el('param-legend')) el('param-legend').onchange = () => commit(() => { sheet().params.legend = el('param-legend').checked ? 'all' : ''; });
  // "All groups" (census B-S14, contract W10): the worksheet draws every group
  // and the verified query asks for up to the server's 100,000 per page.
  if (el('param-groups-all')) el('param-groups-all').onchange = () => commit(() => {
    sheet().params.maxGroups = el('param-groups-all').checked ? 'all' : 0;
  });
  const inspectorTabs = ['marks', 'analytics', 'filters'];
  for (const id of inspectorTabs) el('tab-' + id).onclick = () => setInspectorTab(id);
  document.querySelector('.studio-inspector-tabs').addEventListener('keydown', event => {
    const index = inspectorTabs.findIndex(id => event.target === el('tab-' + id));
    if (index < 0) return;
    const next = {ArrowRight: (index + 1) % inspectorTabs.length,
      ArrowLeft: (index + inspectorTabs.length - 1) % inspectorTabs.length,
      Home: 0, End: inspectorTabs.length - 1}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setInspectorTab(inspectorTabs[next]);
    el('tab-' + inspectorTabs[next]).focus();
  });
  el('view-builder').onclick = () => setViewMode('builder');
  el('view-canvas').onclick = () => setViewMode('canvas');
  // A radiogroup the keyboard can cross: the unselected options carry
  // tabIndex -1, so without this they could only be reached with a mouse.
  el('view-mode').addEventListener('keydown', event => {
    const order = ['view-builder', 'overview-toggle', 'view-canvas'];
    const at = order.indexOf(event.target.id);
    const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
    if (at < 0 || !step) return;
    event.preventDefault();
    for (let i = 1; i <= order.length; i += 1) {
      const next = el(order[(at + step * i + order.length * order.length) % order.length]);
      if (next.disabled) continue;
      next.focus();
      next.click();
      return;
    }
  });
  const setDataPanel = open => {
    document.querySelector('#view-chart-studio .studio').classList.toggle('studio-data-open', open);
    el('data-toggle').setAttribute('aria-expanded', String(open));
  };
  el('data-toggle').onclick = () => setDataPanel(el('data-toggle').getAttribute('aria-expanded') !== 'true');
  // The source chip (S1) opens a popover holding the Data source controls.
  // Their ids are unchanged; the disclosure inside stays open, and its summary
  // is the popover's heading line.
  const setSourcePopover = open => {
    el('source-popover').hidden = !open;
    el('scope-change').setAttribute('aria-expanded', String(!!open));
  };
  el('scope-change').onclick = () => {
    const open = el('source-popover').hidden;
    setSourcePopover(open);
    if (!open) return;
    // The source controls are in the popover now, so the chip no longer opens
    // the narrow-screen fields drawer.
    // An explicit open outlives the prepare that may have opened the panel
    // for its own reasons, so nothing closes it behind the reader's back.
    progressOpened = false;
    el('source-details').open = true;
    renderSourceSummary();
    el('source-details').querySelector('summary').focus();
  };
  el('source-summary').addEventListener('click', event => event.preventDefault());
  el('source-details').addEventListener('toggle', renderSourceSummary);
  el('source-popover').addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault(); setSourcePopover(false); el('scope-change').focus();
  });
  el('reconcile').addEventListener('keydown', event => {
    if (event.key !== 'Escape' || el('reconcile-detail').hidden) return;
    event.preventDefault(); setWhy(false); el('reconcile-why').focus();
  });
  // A click outside a popover or menu puts it away. On click, not on
  // pointerdown: closing a popover must not move what the click lands on.
  document.addEventListener('click', event => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!el('source-popover').hidden && !target.closest('.studio-source-anchor')) setSourcePopover(false);
    if (!el('reconcile-detail').hidden && !target.closest('#studio-reconcile-why, #studio-reconcile-detail')) setWhy(false);
    if (el('sort').open && !target.closest('#studio-sort')) el('sort').open = false;
    if (el('from-menu') && el('from-menu').open && !target.closest('#studio-from-menu')) el('from-menu').open = false;
  }, true);
  el('compare-add').onclick = () => openCompare(el('compare-add'));
  el('compare-clear').onclick = () => { clearCompare(); el('compare-add').focus(); };
  el('reconcile-why').onclick = () => setWhy(el('reconcile-detail').hidden);
  el('match-graph').onclick = matchGraphScope;
  // Collapse folds the docked Inspector to a 56px rail of three buttons that
  // bring it back on Marks, Analytics or Filters; the choice is remembered in
  // this browser through ui-state.js (which guards the storage itself).
  const INSPECTOR_KEY = 'mentat-chart-studio-inspector-collapsed';
  function setInspectorCollapsed(collapsed, remember) {
    const host = document.querySelector('#view-chart-studio .studio');
    if (host) host.classList.toggle('studio-inspector-collapsed', !!collapsed);
    el('inspector-collapse').setAttribute('aria-expanded', String(!collapsed));
    if (remember) globalThis.MentatState?.remember?.(INSPECTOR_KEY, collapsed ? '1' : '0');
  }
  el('inspector-collapse').onclick = () => { setInspectorCollapsed(true, true); el('rail-marks').focus(); };
  for (const id of ['marks', 'analytics', 'filters']) el('rail-' + id).onclick = () => {
    setInspectorCollapsed(false, true);
    setInspectorTab(id);
    el('tab-' + id).focus();
  };
  setInspectorCollapsed(globalThis.MentatState?.recall?.(INSPECTOR_KEY) === '1', false);
  // Ruling (c), 2026-09-23 ("the graph should take up more size of the
  // screen ... be able to hide the buckets"): the Data panel folds to a rail
  // as the Inspector does, the shelves fold to one line that still names what
  // is on them, and "Expand chart" puts all three away at once and brings
  // them back as they were. Each choice is this browser's (ui-state.js guards
  // the storage) and is written only when the reader makes it; with nothing
  // stored the view is S1's, every pane shown. The controls are built here,
  // as the From menu is, so the golden index.html does not change.
  const ROOM_KEYS = {data: 'mentat-chart-studio-data-collapsed', shelves: 'mentat-chart-studio-shelves-collapsed', expand: 'mentat-chart-studio-chart-expanded'};
  const ROOM_ICONS = {left: 'M10 3.5 5.5 8l4.5 4.5', right: 'M6 3.5 10.5 8 6 12.5', up: 'M3.5 10 8 5.5l4.5 4.5', down: 'M3.5 6 8 10.5 12.5 6',
    search: 'M11.3 7A4.3 4.3 0 1 1 2.7 7a4.3 4.3 0 0 1 8.6 0zM10.1 10.1l3.4 3.4',
    expand: 'M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10', restore: 'M6 2.5V6H2.5M13.5 6H10V2.5M10 13.5V10h3.5M2.5 10H6v3.5'};
  const roomState = {data: false, shelves: false, expand: false};
  function roomIcon(name) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'studio-icon');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', ROOM_ICONS[name]);
    svg.append(path);
    return svg;
  }
  function roomButton(id, className, iconName, title, text) {
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'studio-' + id;
    if (className) button.className = className;
    button.title = title;
    button.append(roomIcon(iconName));
    if (text) {
      const label = document.createElement('span');
      label.className = 'studio-room-label';
      label.textContent = text;
      button.append(label);
    } else button.setAttribute('aria-label', title);
    return button;
  }
  function buildRoomControls() {
    if (el('expand')) return;
    // Data panel: a chevron at the end of the DIMENSIONS line, and a 56px rail
    // (expand, or expand and search) while it is folded.
    const collapse = roomButton('data-collapse', 'studio-pane-collapse', 'left', 'Hide the Data panel');
    collapse.setAttribute('aria-controls', 'studio-pane-data');
    (el('search').closest('.studio-search-row') || el('pane-data')).append(collapse);
    const rail = document.createElement('div');
    rail.id = 'studio-data-rail';
    rail.className = 'studio-data-rail';
    rail.setAttribute('role', 'group');
    rail.setAttribute('aria-label', 'Data (collapsed)');
    const open = roomButton('data-rail-open', '', 'right', 'Expand the Data panel');
    const find = roomButton('data-rail-search', '', 'search', 'Expand the Data panel and search its fields');
    for (const button of [open, find]) { button.setAttribute('aria-controls', 'studio-pane-data'); button.setAttribute('aria-expanded', 'false'); }
    rail.append(open, find);
    el('data-panel').prepend(rail);
    // Shelves: a chevron under the Chart select, and one line while folded.
    // It rides in the Filters row, which stays the card's last child.
    const fold = roomButton('shelves-collapse', 'studio-shelves-collapse', 'up', 'Hide the Columns, Rows and Filters shelves');
    fold.setAttribute('aria-controls', 'studio-shelves');
    (el('filters').closest('.studio-shelf') || el('shelves')).append(fold);
    const strip = document.createElement('button');
    strip.type = 'button';
    strip.id = 'studio-shelves-strip';
    strip.className = 'studio-shelves-strip';
    strip.title = 'Show the Columns, Rows and Filters shelves';
    strip.setAttribute('aria-controls', 'studio-shelves');
    strip.setAttribute('aria-expanded', 'false');
    const summary = document.createElement('span');
    summary.id = 'studio-shelves-summary';
    summary.className = 'studio-shelves-summary';
    const show = document.createElement('span');
    show.className = 'studio-shelves-show';
    show.textContent = 'Show shelves';
    strip.append(summary, show, roomIcon('down'));
    el('shelves').before(strip);
    // Expand chart: on the chart card, level with its title.
    const expand = roomButton('expand', 'studio-expand', 'expand', '', 'Expand chart');
    expand.setAttribute('aria-controls', 'studio-canvas-wrap');
    el('canvas-wrap').prepend(expand);
  }
  // What the folded shelves line says: the fields on Columns, Rows and
  // Filters, read from the pills just drawn, so it never disagrees with them.
  function renderShelfStrip() {
    const summary = el('shelves-summary');
    if (!summary) return;
    const parts = [['Columns', 'columns'], ['Rows', 'rows'], ['Filters', 'filters']]
      .map(([label, zone]) => [label, [...el(zone).querySelectorAll('.studio-pill-name')].map(node => node.textContent.trim()).filter(Boolean)])
      .filter(([, names]) => names.length);
    summary.replaceChildren();
    parts.forEach(([label, names], i) => {
      if (i) { const sep = document.createElement('span'); sep.className = 'studio-shelves-sep'; sep.textContent = '\u00b7'; summary.append(sep); }
      const name = document.createElement('span');
      name.className = 'studio-shelves-name';
      name.textContent = label;
      const value = document.createElement('b');
      value.textContent = names.join(', ');
      summary.append(name, value);
    });
    if (!parts.length) summary.textContent = 'The shelves are empty';
    el('shelves-strip').setAttribute('aria-label', 'Show the Columns, Rows and Filters shelves. ' +
      (parts.length ? parts.map(([label, names]) => label + ': ' + names.join(', ')).join('; ') : 'They are empty') + '.');
  }
  function applyRoom() {
    const host = document.querySelector('#view-chart-studio .studio');
    if (host) {
      host.classList.toggle('studio-data-collapsed', roomState.data);
      host.classList.toggle('studio-shelves-collapsed', roomState.shelves);
      host.classList.toggle('studio-chart-expanded', roomState.expand);
    }
    el('data-collapse').setAttribute('aria-expanded', String(!roomState.data));
    el('shelves-collapse').setAttribute('aria-expanded', String(!roomState.shelves));
    const expand = el('expand');
    expand.setAttribute('aria-expanded', String(roomState.expand));
    expand.querySelector('.studio-room-label').textContent = roomState.expand ? 'Show panes' : 'Expand chart';
    expand.title = roomState.expand ? 'Bring back the Data panel, the shelves and the Inspector (Esc)'
      : 'Put the Data panel, the shelves and the Inspector away so the chart fills the space';
    expand.querySelector('path').setAttribute('d', ROOM_ICONS[roomState.expand ? 'restore' : 'expand']);
    fitRoomNow();
  }
  // Round 5 (ruling c: "the graph should take up more size of the screen"):
  // with panes put away every drawing grows into the room, not only the bars
  // that redraw at the canvas's own size. A chart drawn at a fixed size (heat
  // map, treemap, line, pie, a worksheet chart) scales to the canvas and keeps
  // its proportions; one far taller than the room keeps its width and
  // scrolls. Fit: Width and Entire view keep their own rules, and a board or a
  // narrow page, whose canvas grows with its chart, is left as drawn.
  function fitRoom() {
    const canvas = el('canvas');
    const svg = canvas && [...canvas.children].find(node => String(node.tagName).toLowerCase() === 'svg' && !node.hasAttribute('data-native'));
    if (!svg) return;
    for (const property of ['width', 'max-width', 'margin']) svg.style.removeProperty(property);
    if (!chartRoomy() || ((sheet() || {}).fit || 'standard') !== 'standard' || boardTiles() > 1) return;
    if (window.matchMedia && window.matchMedia('(max-width: 1099px)').matches) return;
    const box = (svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
    if (box.length !== 4 || !(box[2] > 0) || !(box[3] > 0)) return;
    // The room is measured again after each sizing. The chart card's foot
    // gives up a few pixels while a drawing overflows the canvas and takes
    // them back once it fits (a treemap sized to a 500px room then had 496px
    // and scrolled), so the size settles here, in at most three passes,
    // rather than on a later frame a background tab never runs.
    for (let pass = 0; pass < 3; pass++) {
      const room = canvas.clientWidth, tall = roomHeight(canvas);
      if (!room || !tall) return;
      const contain = Math.min(room, tall * box[2] / box[3]);
      if (contain < 0.7 * Math.min(room, box[2])) {
        for (const property of ['width', 'max-width', 'margin']) svg.style.removeProperty(property);
        return;
      }
      const width = Math.floor(contain) + 'px';
      if (svg.style.width === width) return;
      svg.style.width = width;
      svg.style.maxWidth = 'none';
      svg.style.margin = '0 auto';
    }
  }
  // r5 re-audit MEN-A-REAUDIT-1: fitRoom used to run only on the next
  // animation frame, and a document the browser is not painting (a background
  // tab, a minimised window) runs none. A chart expanded or redrawn there kept
  // its drawn size: a treemap stayed 1000x560 in a 1171x501 room and scrolled,
  // a line stayed 1000x420. It now runs at once as well (reading the canvas's
  // size lays the page out on demand) and again on the next frame, for
  // anything that changes the room before the next paint.
  function fitRoomNow() {
    fitRoom();
    requestAnimationFrame(fitRoom);
  }
  if (typeof MutationObserver === 'function') new MutationObserver(fitRoomNow).observe(el('canvas'), {childList: true});
  function setRoom(name, value, remember) {
    roomState[name] = !!value;
    applyRoom();
    if (remember) globalThis.MentatState?.remember?.(ROOM_KEYS[name], value ? '1' : '0');
  }
  buildRoomControls();
  buildFolds();
  el('data-collapse').onclick = () => { setRoom('data', true, true); el('data-rail-open').focus(); };
  el('data-rail-open').onclick = () => { setRoom('data', false, true); el('data-collapse').focus(); };
  el('data-rail-search').onclick = () => { setRoom('data', false, true); el('search').focus(); };
  el('shelves-collapse').onclick = () => { setRoom('shelves', true, true); el('shelves-strip').focus(); };
  el('shelves-strip').onclick = () => { setRoom('shelves', false, true); el('shelves-collapse').focus(); };
  el('expand').onclick = () => setRoom('expand', !roomState.expand, true);
  // Escape brings the panes back from Expand chart, as it closes any expanded
  // view; a menu, a popover or a field being typed in keeps its own Escape.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || !roomState.expand) return;
    const view = document.getElementById('view-chart-studio');
    if (!view || view.hidden) return;
    const target = event.target;
    if (target && target.closest && target.closest('input, select, textarea, [role="menu"], [role="dialog"], .studio-send, .compare-menu')) return;
    if (document.querySelector('.studio-send, .compare-menu')) return;
    setRoom('expand', false, true);
    el('expand').focus();
  });
  for (const name of Object.keys(roomState)) roomState[name] = globalThis.MentatState?.recall?.(ROOM_KEYS[name]) === '1';
  applyRoom();
  renderShelfStrip();
  // A bar chart is drawn at the canvas's own width (S1), so a change of that
  // width - the window, the Inspector's collapse, Canvas mode - redraws it
  // from the answer already held, which costs no query. While the chart grows
  // into a put-away pane's room (ruling c) its height counts too, and so does
  // the first change after the panes come back, which returns it to S1's pitch.
  if (typeof ResizeObserver === 'function') {
    let drawnWidth = 0, drawnHeight = 0, drawnRoomy = false, resizeTimer = 0;
    new ResizeObserver(entries => {
      const width = Math.round(entries[0].contentRect.width), height = Math.round(entries[0].contentRect.height);
      requestAnimationFrame(fitRoom);
      const roomy = chartRoomy(), taller = Math.abs(height - drawnHeight) >= 8 && (roomy || drawnRoomy);
      if (!width || (Math.abs(width - drawnWidth) < 8 && !taller)) return;
      const first = !drawnWidth;
      drawnWidth = width; drawnHeight = height; drawnRoomy = roomy;
      if (first) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (!el('canvas').querySelector('svg[data-native]') || !verified() || overviewMode || dataSourceTab || boardTiles() > 1) return;
        renderView();
      }, 150);
    }).observe(el('canvas'));
    // A pie fitted to its chart box is drawn again, from the answer already
    // held, once the box has changed by a few px and the resize has settled:
    // the window, the Data panel, the shelves, the Inspector, Expand chart.
    // A pie drawn while the canvas could not be measured (a hidden panel) is
    // drawn again as soon as it can be.
    let refitTimer = 0;
    const refit = () => {
      const canvas = el('canvas');
      if (overviewMode || dataSourceTab || boardTiles() > 1 || canvas.hidden) return;
      const svg = canvas.querySelector(':scope > svg[data-fit]'), drawn = svg && svg.__fit;
      if (!drawn && canvas.dataset.pieFit !== 'wanted') return;
      const fit = pieFit(canvas, sheet());
      if (!fit) return;
      // (2026-09-25) A pie whose box grows with it (height null) already scales
      // with that box's width through CSS, so it is drawn again only for a
      // change wider than a classic 15-17px scrollbar: a page scrollbar that
      // comes and goes with the pie's own height no longer redraws it every
      // 150ms. A bounded pie keeps the 4px step.
      if (drawn && Math.abs(fit.width - drawn.box.width) < (fit.height == null ? 24 : 4) && (fit.height == null) === (drawn.box.height == null) &&
        (fit.height == null || Math.abs(fit.height - drawn.box.height) < 4)) return;
      renderView();
    };
    new ResizeObserver(() => { clearTimeout(refitTimer); refitTimer = setTimeout(refit, 150); }).observe(el('canvas'));
    // Lane F2b: a scatter or line drawn to the chart box's height (cardFit) is drawn again, from the answer
    // already held, once that box has changed by 10px or more and the resize has settled.
    let plotTimer = 0;
    new ResizeObserver(() => {
      clearTimeout(plotTimer);
      plotTimer = setTimeout(() => {
        const canvas = el('canvas'), drawn = canvas.__plotBox, box = fitBox(canvas);
        if (!drawn || !box || canvas.hidden || !verified() || overviewMode || dataSourceTab || boardTiles() > 1) return;
        if (!canvas.querySelector(':scope > svg[data-plot-fit]')) return;
        if (Math.abs(box.width - drawn.width) < 10 && Math.abs(box.height - drawn.height) < 10) return;
        const now = plotLayout();
        if (now.viewport === drawn.viewport && now.layout === drawn.layout) return;
        renderView();
      }, 200);
    }).observe(el('canvas'));
  }
  // Cancelling aborts the request that is preparing and leaves the last good
  // view exactly where it was: renderContributionView ignores AbortError.
  el('progress-cancel').onclick = () => {
    abortContributionRequests();
    // r7: and every other wait on a pending answer (records, report numbers,
    // members, an export), which no surface's flight holds.
    for (const control of preparing) control.abort();
    preparing.clear();
    progressDepth = 0;
    clearProgress();
    status('Cancelled. Showing the last loaded view.');
  };
  el('inspector-toggle').onclick = () => {
    const open = el('inspector').hidden;
    el('inspector').hidden = !open;
    el('inspector-toggle').setAttribute('aria-expanded', String(open));
  };
  el('inspector-close').onclick = () => {
    el('inspector').hidden = true;
    el('inspector-toggle').setAttribute('aria-expanded', 'false');
    el('inspector-toggle').focus();
  };
  el('overview-toggle').onclick = () => setViewMode('overview');
  el('overview-company').onchange = () => { overviewCompany = el('overview-company').value; renderOverview(); };
  // The Inspector is a column on a wide window and a drawer on a narrow one.
  // Crossing that line applies the same rule the first paint applies, so a
  // resized window never leaves the drawer sitting on top of its own toggle.
  // The toggle states what it is showing from the start, not only after a
  // click: a wide window arrives with the Inspector already open.
  const narrowStudio = window.matchMedia ? window.matchMedia('(max-width: 1099px)') : null;
  const syncInspector = isNarrow => {
    const host = document.querySelector('#view-chart-studio .studio');
    const closed = !!isNarrow || !!(host && host.classList.contains('studio-options-hidden'));
    el('inspector').hidden = closed;
    el('inspector-toggle').setAttribute('aria-expanded', String(!closed));
  };
  if (narrowStudio) {
    syncInspector(narrowStudio.matches);
    narrowStudio.addEventListener('change', event => syncInspector(event.matches));
  } else {
    el('inspector-toggle').setAttribute('aria-expanded', String(!el('inspector').hidden));
  }
  el('filter-add').onchange = () => {
    const column = Number(el('filter-add').value);
    el('filter-add').value = '';
    if (!Number.isInteger(column)) return;
    commit(() => { place('filters', column); });
    setInspectorTab('filters');
  };

  // An industry the URL changed while this panel was hidden, applied the moment
  // it is shown. Held rather than dropped when the panel is not ready yet, so a
  // change that arrives during the first load is not lost.
  let pendingIndustry = '';
  function followScopeIndustry() {
    if (!pendingIndustry || !loadedSources || !verified() || !contributionCatalog) return;
    const label = pendingIndustry;
    if (label === el('dataset').value) { pendingIndustry = ''; return; }
    if (!(contributionCatalog.datasets || []).some(entry => entry.id === label && entry.available)) { pendingIndustry = ''; return; }
    pendingIndustry = '';
    el('dataset').value = label;
    selectContributionDataset();
  }
  function activate() {
    const view = document.getElementById('view-chart-studio');
    if (!view || view.hidden) { stopPlay(); return; }
    for (const entry of [...shownWaiters]) entry.go();
    if (!loadedSources) { loadedSources = true; if(verified())loadContributions();else loadRuns(); }
    followScopeIndustry();
  }
  // The Graph tab's "Create chart" button now opens Chart Studio; the classic
  // dialog stays one click away in the Chart Studio toolbar.
  // The Graph scope a verified contribution dataset can express. Employee tier
  // and the transfer mode/direction have no field in the contribution source,
  // and the drill-down category/recipient live only inside graph.js, so none of
  // them are carried. The button title says what does travel.
  const GRAPH_SCOPE = [['company', 'company'], ['cycle', 'cycle']];
  // What Graph can narrow by and this source cannot. Naming each one on the
  // scope chip is the difference between a total that looks wrong and a
  // total the reader knows is wider than the one they just left.
  // Giving type IS verified data: funding_source ("Funding source",
  // contribution_data.py DIMENSIONS), the field Graph's own sparklines narrow
  // with for personal and corporate giving (graph.js sparkRequest). So it is
  // carried with that same mapping, and only employee level stays behind.
  const GRAPH_FUNDING = {personal: 'employee_original', corporate: 'corporate_original'};
  const GRAPH_NOT_CARRIED = [
    ['tier', 'Employee level', 'not in verified data', ''],
    ['mode', 'Candidate view', 'Graph only', 'direct'],
    ['direction', 'Support or opposition', 'Graph only', 'Support']
  ];
  function graphScope() {
    const read = id => (document.getElementById('graph-' + id) || {}).value || '';
    const scope = {industry: read('industry'), filters: [], notCarried: []};
    for (const [control, field] of GRAPH_SCOPE) {
      const value = read(control);
      // Graph spells a cycle by its end year and the verified index spells it
      // as the pair it covers, so the carried value is translated here rather
      // than filtered on a spelling the index never holds.
      if (value) scope.filters.push({field, value: core.carriedFilterValue(field, value)});
    }
    if (GRAPH_FUNDING[read('funding')]) scope.filters.push({field: 'funding_source', value: GRAPH_FUNDING[read('funding')]});
    for (const [control, label, why, ignored] of GRAPH_NOT_CARRIED) {
      const value = read(control);
      if (value && value !== ignored) scope.notCarried.push(label + ': not carried (' + why + ')');
    }
    return scope;
  }
  const openBuilder = document.getElementById('graph-open-builder');
  if (openBuilder) {
    openBuilder.textContent = 'Open in Chart Studio';
    openBuilder.title = 'Carry the industry, company and cycle now showing into Chart Studio';
    openBuilder.removeAttribute('aria-haspopup');
    openBuilder.onclick = () => {
      pendingScope = graphScope();
      // The URL carries Graph's own spelling of the cycle (the end year, per
      // contracts/url_scope.json); the carried pill keeps the verified index's
      // pair spelling. One scope, two vocabularies, and the contract says which
      // is which - so the scope is read from the Graph controls, not from the
      // already-translated pendingScope.
      const read = id => (document.getElementById('graph-' + id) || {}).value || '';
      globalThis.MentatScope?.write({industry: pendingScope.industry, company: read('company'), cycle: read('cycle')});
      globalThis.MentatReturn?.arrive('graph', 'chart-studio');
      if (typeof showView === 'function') showView('chart-studio'); else location.hash = '#chart-studio';
      if (loadedSources && verified()) applyGraphScope();
    };
  }
  // Back, or a scope written by another view in this document, can change the
  // industry in the URL. Chart Studio follows it, but only when it disagrees
  // with the picker, so its own write can never loop back into a second
  // selection - and only once this panel is on screen, because re-selecting a
  // dataset fires a verified query and nobody asked for one behind a hidden
  // tab. A dataset this console does not have available is left alone;
  // openability() and the Data panel already say why.
  globalThis.MentatScope?.subscribe(next => {
    const label = (next && next.industry) || '';
    if (!label || label === el('dataset').value) return;
    pendingIndustry = label;
    const panel = document.getElementById('view-chart-studio');
    if (panel && !panel.hidden) followScopeIndustry();
  });
  window.addEventListener('mentat:view', activate);
  window.addEventListener('hashchange', activate);
  if(new URLSearchParams(location.search).has('worksheet'))el('source-mode').value='worksheet';
  // What the rest of the console may ask this studio about its chart. The
  // filter reader is also what a browser test reads the carried pills from.
  // Lane P: what map-studio.js reads and edits through; every edit is this
  // studio's own commit(), so it undoes and saves like any other.
  if (globalThis.MentatMapStudio) MentatMapStudio.init({
    sheet: () => sheet(), columns: () => columns(), commit: change => commit(change), renderAll: () => renderAll(),
    status: message => status(message), failure: message => failure(message), verified: () => verified(),
    ready: () => loadedSources && verified() && !!data && !!dataset(),
    dataset: () => dataset(), datasets: () => (contributionCatalog && contributionCatalog.datasets) || [],
    basis: () => basis(), query: (query, claim) => queryCached(query, {flight: claim}),
    expanded: () => expandedCard, setExpanded: value => { expandedCard = value; },
    addSheet: fresh => commit(() => {
      sheets.push(fresh);
      if (board) { const at = board.slots.indexOf(active); board.slots[at >= 0 ? at : 0] = sheets.length - 1; }
      active = sheets.length - 1;
      dataSourceTab = false;
    })});
  window.MentatStudioPane = {
    filters() { return (sheet().filters || []).map(f=>({...f,field:columns()[f.column]?.id || columns()[f.column]?.name})); },
    // The briefing drawer and the share dialog live in chart-briefing.js and
    // read the chart through the same one description the studio uses.
    currentChart,
    saveView,
    openViews,
    // The payload builders, so a browser test can assert what this console puts
    // on the wire rather than what a fixture claims it puts there.
    viewSourceRecord,
    viewSourceText
  };
  // A state location view swaps the state field on the shelves and keeps every
  // other pill, the mark and the analysis (census B-S27); with no state field
  // placed it joins Columns, and a sheet with no measure gets Selected amount.
  if(el('destination'))el('destination').onchange=()=>{const id=el('destination').value;if(!id)return;const at=columns().findIndex(f=>f.id===id),amount=columns().findIndex(f=>f.id==='amount');if(at<0){failure('Regenerate this workbook to create its destination fields.');return;}const geo=['recipient_state','represented_state','state'];commit(()=>{let swapped=false;for(const zone of ['columns','rows'])sheet()[zone]=core.shelf(sheet()[zone]).map(p=>{if(!swapped&&geo.includes(columns()[p.column]?.id)){swapped=true;return {column:at,aggregation:'sum'};}return p;});if(!swapped)sheet().columns=[...core.shelf(sheet().columns),{column:at,aggregation:'sum'}];if(amount>=0&&![...core.shelf(sheet().columns),...core.shelf(sheet().rows)].some(p=>columns()[p.column]?.role==='measure'))sheet().rows=[...core.shelf(sheet().rows),{column:amount,aggregation:'sum'}];});};
  watchTitle();
  renderAll();
  activate();
})();
