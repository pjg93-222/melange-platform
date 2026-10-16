/* Optional industry intelligence. Queries, record inspection and exports all
   retain one server-resolved scope; changing it invalidates every old reply.
   Design reference: synthetic Higgsfield c67f4e78-17a0-436d-abb3-87f21ae1e450,
   2026-09-20: ivory surfaces, plum serif, teal marks and compact controls. */
(function (root) {
  'use strict';
  const PANELS = [
    ['favorites', 'Industry favorites', 'Amount, company breadth, persistence and relative preference.'],
    ['trends', 'Comparable-cycle trends', 'Spending pace through matching points in the cycle.'],
    ['changes', 'Explain the change', 'Separate continuing companies, coverage and accounting changes.'],
    ['concentration', 'Concentration & breadth', 'See how much the largest sources and recipients account for.'],
    ['relationships', 'Relationship persistence', 'Recurring, new and no-longer-observed recipient relationships.'],
    ['overlap', 'Company portfolio overlap', 'Compare companies’ distributions of support across recipients.'],
    ['timing', 'Timing patterns', 'Monthly peaks, recurring rhythms and unusual activity.'],
    ['profiles', 'Recipient profiles', 'Dated candidate and committee identities with financial context.'],
    ['spending', 'Outgoing spending', 'Committee expenditures and vendor patterns where supported.'],
    ['legislation', 'Legislative context', 'Legislative activity beside contribution timelines.'],
    ['forecast', 'Forecasts', 'Historical baselines, backtests and supported projections.']
  ];
  const CORE = ['favorites', 'trends', 'changes'];
  const SHORT_TITLES = {favorites:'Top recipients',trends:'Trends',changes:'Changes',concentration:'Concentration',relationships:'Relationships',overlap:'Company overlap',timing:'Timing',profiles:'Recipient profiles',spending:'Outgoing spending',legislation:'Legislative context',forecast:'Forecasts'};
  const KEYS = PANELS.map(p => p[0]);
  const BASES = ['receipt', 'original_observed', 'original_supported', 'pac_activity'];
  const STORAGE = 'mentat.insights.panels.v1', FORECAST_STORAGE = 'mentat.insights.forecast-card.v1', FORECAST = 'forecast';
  const PANEL_CONTEXT = {profiles:'Dated FEC identity context; no contribution allocation',spending:'Saved outgoing payments and full campaign or committee finances; separate from industry giving',legislation:'Dated legislative context; no contribution dollars assigned to bills'};
  const FUNDING_LABELS = {employee_original:'Employee giving',employee:'Employee giving',corporate_original:'Corporate giving',corporate:'Corporate giving',pac_activity:'PAC activity',organization_unverified:'Unverified organization',funding_unverified:'Unverified funding',unverified:'Unverified funding',conflicting_source_evidence:'Conflicting source evidence',linked_refund:'Linked refunds',non_giving:'Non-giving records',excluded_source_policy:'Excluded by source policy',excluded_company_match:'Excluded company matches',connected_pac:'Connected PAC'};
  const STATUS_LABELS = {ready:'Available',partial:'Partial coverage',unavailable:'Not available',empty:'No observed rows',insufficient_history:'More history needed',observed:'Observed'};
  const chartChannels = {};
  const PAGE_SIZE = 50;
  const money = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2});
  const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
  function fundingLabel(value) { return FUNDING_LABELS[value] || String(value).replace(/_/g,' ').replace(/^./,letter=>letter.toUpperCase()); }
  function display(value, column) {
    if (value === null || value === undefined || value === '') return '—';
    if (['funding_source','channel'].includes(column.key)) return fundingLabel(value);
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (column.type === 'money' && Number.isFinite(Number(value))) return money.format(Number(value) / (column.unit === 'dollars' ? 1 : 100));
    if (column.type === 'percent' && Number.isFinite(Number(value))) return number.format(Number(value) * (column.unit === 'percent' ? 1 : 100)) + '%';
    if (column.type === 'number' && Number.isFinite(Number(value))) return number.format(Number(value));
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  function csvCell(value) {
    let text = value === null || value === undefined ? '' : String(value);
    // Spreadsheet applications interpret formulas even when a CSV cell is quoted.
    if (/^[\s\uFEFF]*[=+@-]/.test(text) && typeof value !== 'number') text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  function inferredColumns(rows) {
    const keys = [...new Set(rows.flatMap(row => Object.keys(row)))].filter(key => !['filters','comparison_scopes','backtests'].includes(key));
    return keys.map(key => ({key,label:key.replace(/_/g,' '),type:key.endsWith('_cents') ? 'money' : /(?:share|retention|overlap)$/.test(key) ? 'percent' : rows.some(row => typeof row[key] === 'number') ? 'number' : 'text'}));
  }
  function supplementary(panel) {
    const tables = [];
    for (const [key,title] of [['cards','Summary measures'],['company_changes','Company change details'],['series','Monthly observations'],['quarterly_series','Quarterly observations'],['diagnostics','Forecast requirements and validation'],['vendor_patterns','Outgoing payee patterns'],['purpose_patterns','Reported payment purposes'],['spending_summaries','Saved outgoing-payment summaries']]) {
      const rows = panel[key]; if (Array.isArray(rows) && rows.length) tables.push({key,title,rows,columns:inferredColumns(rows),total_rows:panel[key+'_total_rows'] || panel[key+'_total']});
    }
    const backtests = (panel.diagnostics || []).flatMap(d => (d.backtests || []).map(row => ({funding_source:d.funding_source,...row})));
    if (backtests.length) tables.push({key:'backtests',title:'Held-out cycle errors',rows:backtests,columns:inferredColumns(backtests)});
    return tables;
  }
  function exportCSV(result) {
    const scope = result.scope || {};
    const provenance = JSON.stringify(result.provenance || {});
    const tables = Object.entries(result.panels || {}).flatMap(([key,panel]) => [[key,panel],...supplementary(panel).map(table => [key+'.'+table.key,{...table,status:panel.status,notices:panel.notices,sources:panel.sources}])]);
    const columns = [];
    for (const [,panel] of tables) for (const column of panel.columns || []) {
      const key = column.key + (column.type === 'money' ? column.unit === 'dollars' ? ' (dollars)' : ' (cents)' : column.type === 'percent' && column.unit !== 'percent' ? ' (ratio)' : '');
      if (!columns.includes(key)) columns.push(key);
    }
    const headers = ['panel', 'panel_status', 'scope.dataset', 'scope.contribution_basis', 'panel_accounting_context', 'scope.as_of', 'scope.baseline_cycle', 'scope.current_cycle', 'scope.company', 'scope.funding_source', 'scope.comparison_dataset', 'provenance', 'panel_sources', 'notices', ...columns];
    const lines = [headers];
    for (const [key, panel] of tables) {
      const notices = [...(result.notices || []), ...(panel.notices || [])].map(noticeText).join(' | ');
      for (const row of (panel.rows || []).length ? panel.rows : [{}]) {
        const values = {};
        for (const column of panel.columns || []) values[column.key + (column.type === 'money' ? column.unit === 'dollars' ? ' (dollars)' : ' (cents)' : column.type === 'percent' && column.unit !== 'percent' ? ' (ratio)' : '')] = row[column.key];
        lines.push([key, panel.status || '', scope.dataset || '', scope.basis || '', PANEL_CONTEXT[key.split('.')[0]] || scope.basis_label || scope.basis || '',scope.as_of || '', scope.baseline_cycle || '', scope.current_cycle || '', scope.company || '', scope.funding_source || '', scope.comparison_dataset || '', provenance, JSON.stringify(panel.sources || []), notices, ...columns.map(c => values[c])]);
      }
    }
    return '\uFEFF' + lines.map(row => row.map(csvCell).join(',')).join('\r\n');
  }
  function noticeText(value) { return typeof value === 'string' ? value : value && (value.message || value.description || value.label) || JSON.stringify(value); }
  // Console redesign, build 04 (2026-09-22, board I1). Channels stay separate:
  // the engine never adds funding channels, so the page picks one giving channel
  // for the summary and every card, never a review/diagnostic channel.
  const DIAGNOSTIC_CHANNELS = new Set(['conflicting_source_evidence','funding_unverified','unverified','organization_unverified','non_giving','linked_refund','excluded_source_policy','excluded_company_match','Unknown']);
  const GIVING_ORDER = ['employee_original','employee','corporate_original','corporate','connected_pac','pac_activity'];
  const DAY = 86400000;
  function cycleEnd(value) {
    const text = String(value || '').trim(), range = text.match(/^(\d{4})[-–](\d{4})$/);
    if (range) { const start = Number(range[1]), end = Number(range[2]); return end === start + 1 && end % 2 === 0 ? end : null; }
    if (/^\d{4}$/.test(text)) { const year = Number(text); return year + year % 2; }
    return null;
  }
  // General election: the Tuesday after the first Monday in November.
  function electionDay(end) { const weekday = new Date(Date.UTC(end, 10, 1)).getUTCDay(); return Date.UTC(end, 10, 2 + (8 - weekday) % 7); }
  function isoDay(value) { const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/); return match ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null; }
  function daysBefore(time, end) { return Math.round((electionDay(end) - time) / DAY); }
  function sameChannel(value, channel) { return String(value ?? '') === String(channel ?? ''); }
  /* Running total of one channel within one cycle from the trends panel's dated
     monthly series. Each month's observed net lands on its last day; the cycle
     starts at zero on 1 January of its first year. `through` ends the line at a
     cutoff (the later cycle); `exactAt` adds the exact same-stage point to the
     earlier cycle. Missing months stay missing: points are joined, never filled. */
  function runningTotals(series, channel, end, options = {}) {
    const first = (end - 1) + '-01', last = end + '-12', cutoff = isoDay(options.through), exactAt = isoDay(options.exactAt);
    const exact = Number.isSafeInteger(options.exact) ? options.exact : null;
    const months = (series || []).filter(entry => entry && sameChannel(entry.funding_source, channel) && /^\d{4}-\d{2}$/.test(String(entry.month || '')) &&
      entry.month >= first && entry.month <= last && Number.isSafeInteger(entry.amount_cents)).sort((a, b) => a.month < b.month ? -1 : a.month > b.month ? 1 : 0);
    if (!months.length && exact === null) return [];
    const points = [{time: Date.UTC(end - 1, 0, 1), value: 0, start: true}];
    let total = 0;
    for (const entry of months) {
      const [year, month] = entry.month.split('-').map(Number), time = Date.UTC(year, month, 0);
      total += entry.amount_cents;
      if (cutoff !== null && time >= cutoff) break;
      points.push({time, value: total});
    }
    if (cutoff !== null) points.push({time: cutoff, value: exact === null ? total : exact, end: true});
    else if (exactAt !== null && exact !== null) {
      const at = points.findIndex(point => point.time >= exactAt), point = {time: exactAt, value: exact, matched: true};
      if (at < 0) points.push(point); else if (points[at].time === exactAt) points[at] = {...points[at], ...point}; else points.splice(at, 0, point);
    }
    return points.map(point => ({...point, days: daysBefore(point.time, end)}));
  }
  function channelMagnitudes(data) {
    const totals = new Map(), panels = data && data.panels || {}, measured = data && data.metadata && data.metadata.totals_by_channel;
    const add = (channel, value) => { if (channel === undefined || channel === null) return; const key = String(channel); totals.set(key, (totals.get(key) || 0) + Math.abs(Number(value) || 0)); };
    if (Array.isArray(measured) && measured.length) for (const row of measured) add(row.funding_source, row.gross_cents ?? row.amount_cents);
    else {
      for (const card of (panels.changes && panels.changes.cards) || []) add(card.funding_source, Math.abs(Number(card.baseline_cents) || 0) + Math.abs(Number(card.current_cents) || 0));
      for (const row of (panels.trends && panels.trends.rows) || []) add(row.funding_source, row.amount_cents);
      for (const row of (panels.favorites && panels.favorites.rows) || []) add(row.funding_source, row.gross_cents ?? row.amount_cents);
    }
    return totals;
  }
  // Giving channels first (largest first), review channels last.
  function rankChannels(channels, totals) {
    const order = channel => { const index = GIVING_ORDER.indexOf(channel); return index < 0 ? GIVING_ORDER.length : index; };
    const size = channel => totals && totals.get(channel) || 0;
    return [...new Set((channels || []).map(channel => String(channel ?? '')))].sort((a, b) =>
      Number(DIAGNOSTIC_CHANNELS.has(a)) - Number(DIAGNOSTIC_CHANNELS.has(b)) || size(b) - size(a) || order(a) - order(b) || a.localeCompare(b));
  }
  // The channel the summary strip reports and every card opens on.
  function defaultChannel(data) {
    const scope = data && data.scope || {};
    if (scope.funding_source) return String(scope.funding_source);
    const totals = channelMagnitudes(data), giving = [...totals.keys()].filter(channel => channel && !DIAGNOSTIC_CHANNELS.has(channel));
    return rankChannels(giving, totals)[0] ?? null;
  }
  function niceTicks(min, max, count) {
    const span = max - min || Math.abs(max) || 100, raw = span / Math.max(1, count - 1), power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(factor => factor * power).find(value => value >= raw - 1e-9) || 10 * power, ticks = [];
    for (let value = Math.floor(min / step) * step; value <= Math.ceil(max / step) * step + step / 2; value += step) ticks.push(Math.round(value));
    return ticks.length > 1 ? ticks : [ticks[0] || 0, (ticks[0] || 0) + Math.round(step)];
  }
  // Board I1 writes amounts in millions with one decimal ("$0.5M", "+$1.6M").
  function millions(cents, signed) {
    const dollars = Number(cents) / 100, size = Math.abs(dollars), sign = dollars < 0 ? '-' : signed && dollars > 0 ? '+' : '';
    return sign + '$' + (size >= 1e5 ? (size / 1e6).toFixed(1) + 'M' : size >= 1e3 ? (size / 1e3).toFixed(1) + 'K' : size.toFixed(0));
  }
  function tickMoney(cents, dollarSign) {
    const dollars = Number(cents) / 100, size = Math.abs(dollars), trim = value => String(Number(value.toFixed(2)));
    return (dollars < 0 ? '-' : '') + (dollarSign ? '$' : '') + (size >= 1e6 ? trim(size / 1e6) + 'M' : size >= 1e3 ? trim(size / 1e3) + 'K' : trim(size));
  }
  // The wait before the next status poll: the server's retry_after_ms, held
  // between 200 ms and 5 s; after 40 polls, every 5 s. The 200 ms floor lets a
  // server that expects a result shortly say so (perf H5, 2026-09-24); a server
  // that always answers 1500 ms is still polled every 1.5 s.
  function pollDelay(attempts, retryAfterMs) { return attempts >= 40 ? 5000 : Math.max(200, Math.min(5000, retryAfterMs || 1500)); }
  // First open (perf, 2026-09-24): the only field of the query body the catalog
  // decides is the industry, and installCatalog() keeps whatever industry it is
  // given, an unavailable one included. So a known industry forms the body
  // before the catalog arrives: the controls as they stand, with a comparison
  // industry equal to the industry cleared, as updateComparisonOptions() does.
  function earlyQuery(formed, industry, retry) {
    if (!industry) return null;
    const out = {...formed, dataset: industry};
    if (out.comparison_dataset === industry) delete out.comparison_dataset;
    if (retry) out.retry = true;
    return out;
  }
  // Two query bodies ask the same thing: the same fields and values, in any key order.
  function sameQuery(a, b) { const canonical = value => JSON.stringify(Object.keys(value || {}).sort().map(key => [key, value[key]])); return canonical(a) === canonical(b); }
  // FIX 4 (collection gaps): the report's own Data Quality check names the company x cycle cells whose stored
  // rows are materially fewer than FEC reported (the catalogue's dataset carries them as collection_gaps).
  // The company is this scope's. The cycles are all of the dataset's for the Sources copy, the notices and
  // the card source lines, because the trends and favourites panels draw every observed cycle; `periods`
  // narrows them to the two cycles being compared, which is what the period summary above the cards states.
  // coverage-view.js words it.
  function coverageNotice(result, catalog, periods = false) {
    const api = root.MentatCoverage, scope = result && result.scope || {}, datasets = catalog && catalog.datasets || [];
    if (!api) return null;
    const compared = periods ? [scope.baseline_cycle, scope.current_cycle].filter(Boolean) : [];
    const part = id => ({gaps: (datasets.find(d => d.id === id) || {}).collection_gaps, scope: {companies: scope.company ? [scope.company] : null, cycles: compared.length ? compared : null}});
    return api.gapNotice([part(scope.dataset), ...(scope.comparison_dataset ? [part(scope.comparison_dataset)] : [])]);
  }
  // The answer as every surface reads it: the caption is among its scope-and-coverage notices, which the
  // screen lists, the CSV puts in its notices column and the JSON export carries. Extending twice adds it once.
  function withCoverage(result, catalog) {
    const notice = coverageNotice(result, catalog);
    if (!notice || (result.notices || []).includes(notice.text)) return result;
    return {...result, notices: [...(result.notices || []), notice.text]};
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = {coverageNotice, withCoverage, display, csvCell, exportCSV, PANELS, SHORT_TITLES, cycleEnd, electionDay, daysBefore, runningTotals, channelMagnitudes, rankChannels, defaultChannel, niceTicks, millions, tickMoney, DIAGNOSTIC_CHANNELS, pollDelay, earlyQuery, sameQuery}; return; }
  if (!root.document.getElementById('view-insights')) return;
  const el = id => root.document.getElementById('insights-' + id);
  function node(tag, text, className) { const out = document.createElement(tag); if (text !== undefined) out.textContent = text; if (className) out.className = className; return out; }
  let active = false, catalog = null, token = '', result = null, revision = 0, sourceRevision = 0;
  let request = null, sourceRequest = null, pollTimer = null, loading = false, sourceContext = null, sourceReturnFocus = null;
  let analysisView = 'overview', summaryChannel = null, channelScope = '', magnitudes = new Map(), clipCounter = 0;
  let resolvedPeriods = {baseline: '', current: ''}, shownPeriods = {'baseline-cycle': '', 'current-cycle': ''};
  let enabled = CORE.slice(), storageAvailable = true;
  // Owner ruling 2026-09-30: the historical-baseline forecast card is hidden behind a per-browser setting
  // (More options), off until chosen. While it is off the card, its switch and its place in All are absent,
  // and a saved choice of it is dropped, so it is never requested. panelShown answers for every list below.
  let showForecast = false;
  try { showForecast = JSON.parse(localStorage.getItem(FORECAST_STORAGE)) === true; } catch (_) { storageAvailable = false; }
  const panelShown = key => showForecast || key !== FORECAST;
  try { const saved = JSON.parse(localStorage.getItem(STORAGE)); if (Array.isArray(saved)) enabled = KEYS.filter(key => panelShown(key) && saved.includes(key)); } catch (_) { storageAvailable = false; }
  const sorts = {}, tablesShown = {}, methodsShown = {};
  const ANALYSIS_VIEWS = {overview: 'results', sources: 'sources'};
  // Board I1 order: the running total on top, then What changed beside Top recipients.
  const CARD_ORDER = ['trends', 'changes', 'favorites', 'concentration', 'relationships', 'overlap', 'timing', 'profiles', 'spending', 'legislation', 'forecast'];
  const CARD_TITLES = {trends: 'Giving through the same point in each cycle', changes: 'What changed', favorites: 'Top recipients'};
  const CHANNEL_SELECT_LABELS = {trends: 'Trend chart funding source', changes: 'Change chart funding source', favorites: 'Top recipients funding source', concentration: 'Concentration chart funding source', timing: 'Timing chart funding source'};
  const COMPONENT_LABELS = {'Continuing observed recipients': ['Continuing'], 'Newly observed recipients': ['Newly', 'observed'], 'No longer observed recipients': ['No longer', 'observed'], 'Unattributed recipient observations': ['Unattributed']};
  const EMPTY_MESSAGE = 'No receipts in this period for this scope.';
  const wholeMoney = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0});
  const signedMoney = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0, signDisplay: 'exceptZero'});
  const signedPercent = new Intl.NumberFormat('en-US', {style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: 'exceptZero'});
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svgNode(tag, attrs, text) { const out = document.createElementNS(SVG_NS,tag); for (const [key,value] of Object.entries(attrs || {})) out.setAttribute(key,String(value)); if (text !== undefined) out.textContent = text; return out; }
  const plotDrawers=new WeakMap();
  const plotObserver=typeof ResizeObserver==='undefined'?null:new ResizeObserver(entries=>{
    for(const {target,contentRect}of entries){const draw=plotDrawers.get(target),width=Math.round(contentRect.width);if(draw&&width>0&&target.dataset.width!==String(width)){target.dataset.width=width;draw(width);}}
  });
  function responsiveFigure(label,draw){
    const figure=node('figure',undefined,'insights-chart');
    const repaint=width=>{figure.replaceChildren();draw(figure,width);};plotDrawers.set(figure,repaint);
    plotObserver?.observe(figure);requestAnimationFrame(()=>{if(figure.isConnected)repaint(Math.max(240,figure.clientWidth));});return figure;
  }
  const compactMoney=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:1}).format(value/100);
  function selectAnalysisView(name) {
    if (!Object.prototype.hasOwnProperty.call(ANALYSIS_VIEWS, name)) return;
    analysisView = name;
    for (const [key, panelId] of Object.entries(ANALYSIS_VIEWS)) el(panelId).hidden = key !== name;
    el('page-sources').setAttribute('aria-expanded', String(name === 'sources'));
  }
  function preferences() {
    try { localStorage.setItem(STORAGE, JSON.stringify(enabled)); localStorage.setItem(FORECAST_STORAGE, JSON.stringify(showForecast)); storageAvailable = true; } catch (_) { storageAvailable = false; }
    el('preference-note').textContent = storageAvailable ? 'Choices saved in this browser.' : 'Choices apply to this visit; browser storage is unavailable.';
    el('preference-note').hidden = storageAvailable;
  }
  // The rail: one switch per analysis, short labels, descriptions for assistive tech.
  function renderToggles() {
    el('toggles').replaceChildren();
    for (const [key, title, description] of PANELS.filter(panel => panelShown(panel[0]))) {
      const metadata = (catalog && catalog.panels || []).find(p => p.id === key) || {};
      const label = node('label', undefined, 'insights-toggle'), checkbox = node('input'), text = node('span', SHORT_TITLES[key] || metadata.title || title, 'insights-toggle-label');
      checkbox.type = 'checkbox'; checkbox.setAttribute('role', 'switch'); checkbox.value = key; checkbox.checked = enabled.includes(key); checkbox.id = 'insights-toggle-' + key;
      text.append(node('small', ': ' + (metadata.description || description), 'insights-visually-hidden'));
      label.title = (metadata.title || title) + ': ' + (metadata.description || description); label.append(text, checkbox);
      checkbox.addEventListener('change', () => { enabled = KEYS.filter(k => panelShown(k) && (k === key ? checkbox.checked : enabled.includes(k))); preferences(); load(); });
      el('toggles').append(label);
    }
  }
  function status(message, state) { el('status').textContent = message; el('status').dataset.state = state || ''; }
  function exporting(allowed) { el('export-csv').disabled = !allowed; el('export-json').disabled = !allowed; }
  function invalidate() {
    revision++; if (request) request.abort(); request = null;
    clearTimeout(pollTimer); pollTimer = null; loading = false;
    result = null; exporting(false); plotObserver?.disconnect(); el('results').replaceChildren(); el('results').setAttribute('aria-busy', 'false');
    el('scope').hidden = true; el('scope').replaceChildren(); closeSources(); renderSummary(null);
    return revision;
  }
  function fillSelect(target, values, emptyLabel, current, preserveMissing = false) {
    const wanted = current === undefined ? target.value : String(current || '');
    target.replaceChildren(); if (emptyLabel !== null) target.append(new Option(emptyLabel, ''));
    for (const value of values || []) {
      const id = typeof value === 'object' ? value.id === undefined ? value.value : value.id : value;
      if (id === undefined || id === null) continue;
      const label = typeof value === 'object' ? value.label || String(id) : String(value);
      target.append(new Option(label, String(id)));
    }
    if (wanted && preserveMissing && !Array.from(target.options).some(o => o.value === wanted)) target.append(new Option((target.id==='insights-funding-source'?fundingLabel(wanted):wanted)+(target.id==='insights-dataset'?' · unavailable':' · not in current options'),wanted));
    if (Array.from(target.options).some(o => o.value === wanted)) target.value = wanted;
  }
  function updateComparisonOptions() {
    for(const option of el('comparison-dataset').options) option.disabled=Boolean(option.value) && option.value===el('dataset').value;
    if(el('comparison-dataset').value===el('dataset').value)el('comparison-dataset').value='';
  }
  function resetIndustryFilters() {
    // Company/funding choices belong to an industry. Accounting and the two
    // comparison periods are independent Insights controls, not Graph's cycle.
    fillSelect(el('company'),[],'All covered companies','');
    fillSelect(el('funding-source'),[],'All reported sources','');
    updateComparisonOptions();
  }
  function adoptSharedIndustry(scope) {
    const industry=scope && scope.industry;
    if(!industry || !catalog || industry===el('dataset').value)return false;
    if(!Array.from(el('dataset').options).some(option=>option.value===industry))el('dataset').append(new Option(industry+' · unavailable',industry));
    el('dataset').value=industry;resetIndustryFilters();return true;
  }
  // The industry the catalog should select: the shared URL scope's, else the
  // current choice, else Graph's. load() asks the same question before the
  // catalog arrives (earlyQuery), so both ask it here.
  function wantedIndustry() { return root.MentatScope?.read()?.industry || el('dataset').value || document.getElementById('graph-industry')?.value || ''; }
  function installCatalog(data) {
    catalog = data;
    const datasets = data.datasets || [];
    const wanted = wantedIndustry();
    fillSelect(el('dataset'), datasets.map(d => ({id: d.id, label: (d.label || d.id) + (d.available === false ? ' · unavailable' : '')})), datasets.length ? null : 'No industries available', wanted,true);
    fillSelect(el('comparison-dataset'), datasets.filter(d => d.available !== false), 'No industry comparison');
    updateComparisonOptions();
    renderToggles();
  }
  function installOptions(data) {
    const options = data.options || {}, scope = data.scope || {};
    if (options.companies) fillSelect(el('company'), options.companies, 'All covered companies', scope.company === undefined ? el('company').value : scope.company,true);
    if (options.funding_sources) fillSelect(el('funding-source'), options.funding_sources.map(value=>typeof value==='string'?{id:value,label:fundingLabel(value)}:value), 'All reported sources', scope.funding_source === undefined ? el('funding-source').value : scope.funding_source,true);
    if (options.cycles) {
      // Resolved automatic periods describe the result, not a user filter edit.
      // Keep the request stable so showing another panel reuses its analysis.
      fillSelect(el('baseline-cycle'), options.cycles, 'Automatic', el('baseline-cycle').value,true);
      fillSelect(el('current-cycle'), options.cycles, 'Automatic', el('current-cycle').value,true);
    }
  }
  // Period chips. An automatic chip shows the cycle the server resolved; the
  // request keeps it automatic. A swapped (later-first) pair is requested in time
  // order, because the engine compares an earlier baseline with a later cycle.
  function chipValue(control) { return el(control).value || resolvedPeriods[control === 'baseline-cycle' ? 'baseline' : 'current'] || ''; }
  function labelPeriods() {
    for (const [control, resolved] of [['baseline-cycle', resolvedPeriods.baseline], ['current-cycle', resolvedPeriods.current]]) {
      const select = el(control), automatic = select.options[0];
      if (automatic && automatic.value === '') automatic.textContent = resolved ? 'Automatic (' + resolved + ')' : 'Automatic';
      shownPeriods[control] = chipValue(control);
      select.parentElement.querySelector('.insights-period-text').textContent = shownPeriods[control] || 'Automatic';
    }
  }
  function setChip(control, value) {
    const select = el(control);
    if (!Array.from(select.options).some(option => option.value === value)) select.append(new Option(value, value));
    select.value = value; labelPeriods();
  }
  // Period chips always hold two different cycles (see the chip change handler).
  function sameCycle(a, b) { const x = cycleEnd(a), y = cycleEnd(b); return x !== null && y !== null ? x === y : String(a) === String(b); }
  function periodOptions() { return [...new Set(['baseline-cycle', 'current-cycle'].flatMap(control => Array.from(el(control).options, option => option.value)).filter(value => cycleEnd(value)))].sort((a, b) => cycleEnd(a) - cycleEnd(b)); }
  function neighbourCycle(value, preferLater) {
    const end = cycleEnd(value), cycles = periodOptions(), before = cycles.filter(cycle => cycleEnd(cycle) < end).pop(), after = cycles.find(cycle => cycleEnd(cycle) > end);
    return (preferLater ? after || before : before || after) || '';
  }
  // The server resolves an automatic chip as the scope does (current: the latest
  // cycle through the cutoff; baseline: the latest cycle before it) and refuses a
  // pair that is not earlier -> later, so such a pair is never requested.
  function periodConflict(query) {
    const phase = cycleEnd(String(query.as_of || localDate()).slice(0, 4)), cycles = periodOptions().filter(cycle => !phase || cycleEnd(cycle) <= phase);
    const current = query.current_cycle || cycles[cycles.length - 1] || '';
    const baseline = query.baseline_cycle || (current ? cycles.filter(cycle => cycleEnd(cycle) < cycleEnd(current)).pop() : '') || '';
    const earlier = cycleEnd(baseline), later = cycleEnd(current);
    return earlier !== null && later !== null && earlier >= later;
  }
  function scopeQuery() {
    const out = {dataset: el('dataset').value, basis: BASES.includes(el('basis').value) ? el('basis').value : 'receipt', panels: enabled.slice()};
    for (const [field, control] of [['company','company'],['funding_source','funding-source'],['as_of','as-of'],['comparison_dataset','comparison-dataset']]) if (el(control).value) out[field] = el(control).value;
    const left = el('baseline-cycle').value, right = el('current-cycle').value;
    if (left && right && cycleEnd(left) > cycleEnd(right)) { out.baseline_cycle = right; out.current_cycle = left; }
    else { if (left) out.baseline_cycle = left; if (right) out.current_cycle = right; }
    return out;
  }
  // `auth` is the page's session token unless a caller passes one: a first-open
  // query sent before load() stores the token passes the one its session reply carried.
  // GETs revalidate (RACF, 2026-09-24): a route the server answers with an
  // ETag and `private, no-cache` (/api/insights/catalog) is kept by the
  // browser and asked again with If-None-Match, so an unchanged catalog comes
  // back as a bodiless 304. A route the server still answers `no-store` (the
  // session token) is never stored, whatever this asks.
  async function api(path, body, signal, auth = token) {
    const response = await fetch(path, body === undefined ? {signal, cache: 'no-cache'} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Mentat-Token': auth}, body: JSON.stringify(body), signal});
    let data; try { data = await response.json(); } catch (_) { throw new Error('The server returned an unreadable response.'); }
    if (!response.ok || data.error || data.status === 'failed') throw new Error(data.error || data.message || 'The analysis could not be loaded.');
    return data;
  }
  // First open (perf, 2026-09-24): when earlyQuery() can form the body, it is
  // sent as soon as the session token arrives instead of after the catalog -
  // unless the catalog has already arrived, when load() forms and sends it at
  // once. load() uses the reply only if the body it forms after
  // installCatalog() is identical (sameQuery); drop() abandons the request.
  // `at` stamps the reply's arrival, so time already spent waiting for the
  // catalog counts toward the first poll's delay.
  function sendEarly(query, catalogRead, sessionRead, signal) {
    if (!query || periodConflict(query)) return null;
    const own = new AbortController(), drop = () => own.abort();
    signal.addEventListener('abort', drop, {once: true});
    let catalogSettled = false; const settled = () => { catalogSettled = true; };
    catalogRead.then(settled, settled);
    const reply = sessionRead.then(session => catalogSettled || !session.token || own.signal.aborted ? null
      : api('/api/insights/query', query, own.signal, session.token).then(data => ({data, at: performance.now()})));
    reply.catch(() => {});
    return {query, reply, drop};
  }
  async function load(retry = false) {
    const ticket = invalidate();
    if (!active) return;
    if (!enabled.length) { status('All analyses are off. Enable an analysis to explore this industry.'); el('results').append(node('p', 'Turn on an analysis in the list to build your workspace.', 'insights-empty')); return; }
    loading = true; request = new AbortController(); const signal = request.signal;
    el('results').setAttribute('aria-busy', 'true'); status('Loading industry scope and source information…', 'loading'); shells('loading');
    let early = null;
    try {
      if (!catalog || !token) {
        const catalogRead = api('/api/insights/catalog', undefined, signal), sessionRead = api('/api/contributions/session', undefined, signal);
        if (!catalog) early = sendEarly(earlyQuery(scopeQuery(), wantedIndustry(), retry), catalogRead, sessionRead, signal);
        const [data, session] = await Promise.all([catalogRead, sessionRead]);
        if (ticket !== revision || !active) return;
        token = session.token; if (!token) throw new Error('The local session did not provide an analysis token.'); installCatalog(data);
      }
      const query = scopeQuery();
      if (retry) query.retry = true;
      if (!query.dataset) { status('No industry data is available. Load an industry report to begin.'); loading = false; el('results').setAttribute('aria-busy','false'); el('results').replaceChildren(node('p', 'No industry data is available. Load an industry report to begin.', 'insights-empty')); return; }
      if (periodConflict(query)) { const message = 'Choose two different cycles to compare.'; labelPeriods(); loading = false; el('results').setAttribute('aria-busy', 'false'); status(message, 'error'); shells('invalid', message); return; }
      // The early reply answers this load only when it was asked exactly this
      // body; otherwise it is abandoned now and the formed body is sent.
      const first = early && sameQuery(early.query, query) ? early.reply : null;
      if (early && !first) early.drop();
      await queryReady(query, ticket, signal, 0, first);
    } catch (error) { fail(error, ticket); }
    // An early query this load did not use (another body, a refusal, a failure) is abandoned.
    finally { if (early) early.drop(); }
  }
  // `first` is the reply sendEarly() received for exactly this body on a first
  // open (it resolves to null when none was sent); every later poll asks again.
  async function queryReady(query, ticket, signal, attempts, first) {
    const held = first ? await first : null;
    const data = held ? held.data : await api('/api/insights/query', query, signal);
    if (ticket !== revision || !active) return;
    if (['building', 'queued', 'preparing'].includes(data.status)) {
      // A first build of an industry can take several minutes: poll at the
      // server's pace (pollDelay) for 40 polls, then every 5 s, for about a
      // quarter of an hour in all.
      if (attempts >= 220) throw new Error('Preparation is still running on the server after about 15 minutes. Try again to check its progress.');
      const message = (data.message || 'Preparing the selected analysis…') + (attempts >= 40 ? ' Preparing an industry for the first time can take several minutes; this page keeps checking.' : '');
      status(message, 'loading');
      for (const line of el('results').querySelectorAll('.insights-card[data-state="loading"] .insights-card-message p')) line.textContent = message;
      const nextQuery = {...query}; delete nextQuery.retry;
      // Time already spent waiting for the catalog after an early reply counts toward its delay.
      pollTimer = setTimeout(() => { queryReady(nextQuery, ticket, signal, attempts + 1).catch(error => fail(error, ticket)); }, Math.max(0, pollDelay(attempts, data.retry_after_ms) - (held ? performance.now() - held.at : 0)));
      return;
    }
    result = withCoverage(data, catalog); result._request = query; loading = false; el('results').setAttribute('aria-busy','false');
    installOptions(data); renderResult(result);
  }
  // A failed query reports inside each card, with its own "Try again".
  function fail(error, ticket) {
    if (ticket !== revision || !active || error.name === 'AbortError') return;
    loading = false; el('results').setAttribute('aria-busy', 'false'); status(error.message, 'error'); exporting(false);
    shells('error', error.message);
  }
  // The card's source line (what the per-figure Image header prints as the source) carries the gap too.
  function gapTail() { const gap = coverageNotice(result, catalog); return gap ? ' · ' + gap.text : ''; }
  function addNotices(target, notices) { for (const notice of notices || []) target.append(node('p', noticeText(notice), 'insights-notice')); }
  function renderScope(data) {
    const scope = data.scope || {}, provenance = data.provenance || {};
    const target = el('scope'); target.hidden = false; target.replaceChildren();
    const dataset = (catalog.datasets || []).find(d => d.id === scope.dataset);
    target.append(node('strong', (dataset && dataset.label || scope.dataset || 'Selected industry') + ' · ' + (scope.basis==='receipt'?'Net workbook receipts':scope.basis_label || scope.basis || 'Accounting basis not supplied')));
    const parts = [scope.company,scope.funding_source ? fundingLabel(scope.funding_source) : 'All giving'].filter(Boolean);
    if (scope.baseline_cycle || scope.current_cycle) parts.push('Cycles: ' + (scope.baseline_cycle || 'automatic') + ' → ' + (scope.current_cycle || 'automatic'));
    if (scope.comparison_dataset) parts.push('Industry comparison: ' + scope.comparison_dataset);
    target.append(node('div', parts.join(' · ')+' · Through '+(scope.as_of||'latest available date')+' · Coverage unverified'));
    // FIX 4: a short collection cell under this company's scope gets the console's quality chip and its caption.
    const gap = coverageNotice(data, catalog);
    if (gap) { const line = node('div', undefined, 'insights-coverage-gap'); line.append(root.MentatCoverage.chipNode(gap), node('span', gap.caption, 'chart-note')); target.append(line); }
    if (enabled.some(key=>PANEL_CONTEXT[key])) target.append(node('div','Identity, spending and legislative panels use this selection to find related entities. Each has its own source coverage and accounting context.'));
    const details = node('details'), summary = node('summary','Source and accounting provenance'), list = node('dl',undefined,'insights-provenance');
    for (const [key, value] of Object.entries(provenance)) { list.append(node('dt',key.replace(/_/g,' ')), node('dd', typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value === null ? 'not supplied' : value))); }
    if (!list.children.length) list.append(node('dt','Source'),node('dd','No source fingerprint was supplied.'));
    details.append(summary,list); target.append(details);
    if (data.history || data.source_history) {
      const history=node('details'), body=node('pre',undefined,'insights-history');
      history.append(node('summary','Inspection history and source snapshots'));
      history.append(node('p','Inspection timestamps record when this Insights workspace encountered evidence. They are not filing dates or the dates records first became public.'));
      body.textContent=JSON.stringify({inspection:data.history,source_snapshots:data.source_history},null,2); history.append(body);target.append(history);
    }
  }
  function renderResult(data) {
    renderScope(data);
    if((data.notices || []).length){const notes=node('details',undefined,'insights-global-notes');notes.append(node('summary','Scope and coverage notes · '+data.notices.length));addNotices(notes,data.notices);el('scope').append(notes);}
    const scope = data.scope || {};
    resolvedPeriods = {baseline: scope.baseline_cycle || '', current: scope.current_cycle || ''}; labelPeriods();
    // A new scope reopens every card on the summary's channel.
    const scopeKey = JSON.stringify({...scope, panels: undefined});
    if (scopeKey !== channelScope) { channelScope = scopeKey; for (const key of Object.keys(chartChannels)) delete chartChannels[key]; }
    magnitudes = channelMagnitudes(data); summaryChannel = defaultChannel(data);
    renderSummary(data);
    plotObserver?.disconnect(); el('results').replaceChildren();
    for (const key of orderedKeys()) renderPanel(key, (data.panels || {})[key] || {status:'unavailable',summary:'This analysis was not returned by the source.'});
    const count = enabled.filter(key => (data.panels || {})[key]?.rows?.length).length;
    status(count ? count + ' analyses contain results.' : 'Analysis complete. No observed rows are available for this selection; missing evidence does not establish zero activity.');
    exporting(true);
  }
  // Same-stage values for one channel: the change cards, else the trend rows.
  function periodValues(data, channel) {
    const scope = data && data.scope || {}, panels = data && data.panels || {}, out = {baseline: null, current: null};
    const card = ((panels.changes && panels.changes.cards) || []).find(item => sameChannel(item.funding_source, channel));
    if (card && Number.isSafeInteger(card.baseline_cents) && Number.isSafeInteger(card.current_cents)) return {baseline: card.baseline_cents, current: card.current_cents};
    for (const [field, cycle] of [['baseline', scope.baseline_cycle], ['current', scope.current_cycle]]) {
      const row = ((panels.trends && panels.trends.rows) || []).find(item => sameChannel(item.funding_source, channel) && item.cycle === cycle);
      if (row && Number.isSafeInteger(row.matched_amount_cents)) out[field] = row.matched_amount_cents;
    }
    return out;
  }
  // The strip and every card read in time order (earlier -> later) whatever the
  // chips' order: Swap exchanges the chips only, never the sign of a change.
  function timeOrdered() { const a = chipValue('baseline-cycle'), b = chipValue('current-cycle'), x = cycleEnd(a), y = cycleEnd(b); return x !== null && y !== null && x > y ? [b, a] : [a, b]; }
  function samePair(scope, a, b) { const [earlier, later] = cycleEnd(a) > cycleEnd(b) ? [b, a] : [a, b]; return Boolean(scope && earlier && later && sameCycle(earlier, scope.baseline_cycle) && sameCycle(later, scope.current_cycle)); }
  // FIX 4: the Coverage gap chip and caption for the company and the two compared periods, in the summary
  // card that is always on the page. (The copy in the Sources panel is hidden until Sources is pressed.)
  // Rebuilt on every summary render, so a refresh replaces it and a scope with no gap leaves none behind.
  function renderCoverageLine(data) {
    const host = el('summary'), gap = data ? coverageNotice(data, catalog, true) : null;
    for (const old of host.querySelectorAll('.insights-coverage-gap')) old.remove();
    if (!gap) return;
    const line = node('div', undefined, 'insights-coverage-gap');
    line.id = 'insights-coverage';
    line.append(root.MentatCoverage.chipNode(gap), node('span', gap.caption, 'chart-note'));
    host.append(line);
  }
  function renderSummary(data) {
    renderCoverageLine(data);
    const [left, right] = timeOrdered(), channel = data ? summaryChannel : null;
    // The channel phrase wraps as one unit when a narrow strip cannot hold the label.
    for (const [id, cycle] of [['summary-left-label', left], ['summary-right-label', right]]) {
      const label = el(id); label.replaceChildren('Net giving' + (cycle ? ' ' + cycle : '') + (channel ? ' ' : ''));
      if (channel) label.append(node('span', '· ' + fundingLabel(channel), 'insights-summary-channel'));
      label.title = label.textContent;
    }
    const scope = data && data.scope || {}, values = data && channel !== null ? periodValues(data, channel) : {baseline: null, current: null};
    const valueFor = cycle => cycle && cycle === scope.baseline_cycle ? values.baseline : cycle && cycle === scope.current_cycle ? values.current : null;
    const leftValue = valueFor(left), rightValue = valueFor(right), change = el('summary-change');
    el('summary-left').textContent = leftValue === null ? '—' : wholeMoney.format(leftValue / 100);
    el('summary-right').textContent = rightValue === null ? '—' : wholeMoney.format(rightValue / 100);
    change.replaceChildren(); change.className = '';
    if (leftValue === null || rightValue === null) change.textContent = '—';
    else {
      const delta = rightValue - leftValue; change.className = delta > 0 ? 'insights-up' : delta < 0 ? 'insights-down' : '';
      change.append(node('span', signedMoney.format(delta / 100)));
      if (leftValue > 0) change.append(node('span', signedPercent.format(delta / leftValue), 'insights-summary-percent'));
    }
    // Recipients: the What changed card's same-stage recipient counts for the strip's channel.
    const card = data && channel !== null ? ((data.panels && data.panels.changes && data.panels.changes.cards) || []).find(item => sameChannel(item.funding_source, channel)) : null;
    const counts = card ? [card.continuing_recipients, card.newly_observed_recipients, card.no_longer_observed_recipients] : [], recipients = el('summary-recipients');
    if (counts.length && counts.every(Number.isSafeInteger)) {
      recipients.textContent = number.format(counts[0]) + ' continuing · ' + number.format(counts[1]) + ' new · ' + number.format(counts[2]) + ' no longer observed';
      recipients.title = fundingLabel(channel) + ' recipients (committee ID, else reported name) with same-stage records in both periods, only in ' + right + ', or only in ' + left + '.';
    } else {
      recipients.textContent = '—';
      recipients.title = !data ? '' : enabled.includes('changes') ? 'This response does not count recipients by period for this channel.' : 'Turn on Changes to count recipients by period.';
    }
  }
  function orderedKeys() { return CARD_ORDER.filter(key => enabled.includes(key)); }
  function cardTitle(key, panel) { return CARD_TITLES[key] || panel && panel.title || (PANELS.find(item => item[0] === key) || [key, key])[1]; }
  function infoIcon() { const svg = svgNode('svg', {viewBox: '0 0 16 16', width: 15, height: 15, 'aria-hidden': 'true', focusable: 'false'}); svg.append(svgNode('circle', {cx: 8, cy: 8, r: 6.6}), svgNode('path', {d: 'M8 7.3v4.1M8 4.8v.2'})); return svg; }
  // One chrome for every card: serif title, (i) for method and sources, Chart | Table.
  function cardShell(key, panel, state) {
    const card = node('article', undefined, 'card insights-card'); card.id = 'insights-panel-' + key; card.dataset.state = state;
    if (key === 'trends' || (!CORE.includes(key) && ((panel && panel.columns) || []).length > 6)) card.classList.add('insights-card-wide');
    const heading = node('div', undefined, 'insights-panel-heading'), title = node('h3', cardTitle(key, panel), 'card-title insights-card-title');
    title.id = card.id + '-title'; card.setAttribute('aria-labelledby', title.id);
    const info = node('button', undefined, 'insights-info'); info.type = 'button'; info.title = 'Method and sources';
    info.setAttribute('aria-label', 'Method and sources: ' + title.textContent); info.setAttribute('aria-expanded', 'false'); info.append(infoIcon());
    const tools = node('div', undefined, 'segmented insights-card-tools'), chartButton = node('button', 'Chart'), tableButton = node('button', 'Table');
    tools.setAttribute('role', 'group'); tools.setAttribute('aria-label', title.textContent + ' presentation');
    chartButton.type = tableButton.type = 'button'; chartButton.setAttribute('aria-pressed', 'true'); tableButton.setAttribute('aria-pressed', 'false'); tableButton.setAttribute('aria-expanded', 'false');
    tools.append(chartButton, tableButton); heading.append(title, info, tools); card.append(heading);
    return {card, heading, title, info, tools, chartButton, tableButton};
  }
  function messageBlock(text, kind) { const block = node('div', undefined, 'insights-card-message'); block.append(node('p', text, kind === 'error' ? 'insights-card-error' : 'insights-card-muted')); return block; }
  // Loading and failure keep every enabled card in place.
  function shells(state, message) {
    plotObserver?.disconnect(); el('results').replaceChildren();
    for (const key of orderedKeys()) {
      const shell = cardShell(key, null, state), block = messageBlock(message || 'Loading…', state === 'error' ? 'error' : 'muted');
      shell.info.disabled = shell.chartButton.disabled = shell.tableButton.disabled = true;
      if (state === 'error') { const retry = node('button', 'Try again', 'insights-retry-link'); retry.type = 'button'; retry.addEventListener('click', () => { token = ''; load(true); }); block.append(retry); }
      shell.card.append(block); el('results').append(shell.card);
    }
  }
  function forecastReason(panel) { const reasons = [...new Set((panel.diagnostics || []).map(item => item.reason).filter(Boolean))]; return reasons[0] || 'No forecast is available for this scope.'; }
  function cardChannels(key, panel) {
    const found = [];
    if (key === 'changes') for (const card of panel.cards || []) found.push(card.funding_source);
    else {
      for (const row of panel.rows || []) found.push(row.funding_source);
      if (key === 'trends') for (const entry of panel.series || []) found.push(entry.funding_source);
    }
    return rankChannels(found.map(value => value ?? ''), magnitudes);
  }
  // A card opens on the summary's channel; a reader's pick holds until the scope changes.
  function cardChannel(key, channels) {
    if (channels.length === 1) return channels[0];
    if (channels.includes(chartChannels[key])) return chartChannels[key];
    if (summaryChannel !== null && channels.includes(summaryChannel)) return summaryChannel;
    return channels.find(channel => channel && !DIAGNOSTIC_CHANNELS.has(channel)) ?? null;
  }
  function channelControl(key, channels, selected, onChange) {
    const control = node('label', undefined, 'insights-chart-filter'), select = node('select');
    control.append(node('span', 'Funding channel', 'insights-visually-hidden'), select); select.setAttribute('aria-label', CHANNEL_SELECT_LABELS[key] || 'Chart funding source');
    if (selected === null) { const placeholder = new Option('Choose a funding channel', ''); placeholder.disabled = true; select.append(placeholder); }
    for (const channel of channels) select.append(new Option(channel ? fundingLabel(channel) : 'Unspecified channel', channel));
    select.value = selected === null ? '' : selected;
    select.addEventListener('change', () => onChange(select.value));
    return control;
  }
  function methodRegion(key, panel, card, info) {
    const method = node('div', undefined, 'insights-method'); method.id = card.id + '-method'; method.hidden = !methodsShown[key];
    info.setAttribute('aria-controls', method.id); info.setAttribute('aria-expanded', String(!method.hidden));
    info.addEventListener('click', () => { const open = method.hidden; method.hidden = !open; methodsShown[key] = open; info.setAttribute('aria-expanded', String(open)); });
    if (key === 'trends') method.append(node('p', 'Each line is a running total of observed net giving from 1 January of the cycle’s first year, drawn through monthly event-date points joined by straight segments. The marked point is the exact same-stage amount through the cutoff. Days count back to the general election.', 'insights-card-summary'));
    if (panel.summary) method.append(node('p', noticeText(panel.summary), 'insights-card-summary'));
    if (panel.status && panel.status !== 'ready' && STATUS_LABELS[panel.status]) method.append(node('p', 'Status: ' + STATUS_LABELS[panel.status], 'insights-card-status'));
    if ((panel.notices || []).length) { const notes = node('details', undefined, 'insights-method-notes'); notes.append(node('summary', 'Method and coverage · ' + panel.notices.length)); addNotices(notes, panel.notices); method.append(notes); }
    for (const table of supplementary(panel)) {
      const details = node('details', undefined, 'insights-extra-table'); details.append(node('summary', table.title + ' · ' + table.rows.length + ' rows'), makeTable(table.columns, table.rows, {key}));
      if (table.total_rows > table.rows.length) details.append(node('p', 'Showing ' + table.rows.length + ' of ' + table.total_rows + ' rows. Exports include this returned subset. Narrow the scope to inspect the remainder.', 'insights-card-muted'));
      method.append(details);
    }
    const context = result && result.scope || {};
    if (PANEL_CONTEXT[key]) {
      method.append(node('p', PANEL_CONTEXT[key] + ' · cutoff ' + (context.as_of || 'latest available') + (key === 'spending' ? ' · amounts in USD' : ''), 'insights-card-source'));
      const sources = node('details', undefined, 'insights-extra-table'); sources.append(node('summary', 'Source snapshots · ' + (panel.sources || []).length));
      if (!(panel.sources || []).length) sources.append(node('p', 'No verified source snapshot is available for this panel.'));
      for (const source of panel.sources || []) { const list = node('dl', undefined, 'insights-provenance'); for (const [field, value] of Object.entries(source)) list.append(node('dt', field.replace(/_/g, ' ')), node('dd', typeof value === 'object' ? JSON.stringify(value) : String(value))); sources.append(list); }
      method.append(sources);
    } else method.append(node('p', (context.basis_label || context.basis || '') + ' · cutoff ' + (context.as_of || 'latest available') + ' · amounts in USD' + (result && result.provenance && result.provenance.workbook ? '\nSource: ' + result.provenance.workbook : '') + gapTail(), 'insights-card-source'));
    return method;
  }
  // Chart view for one card and one channel: {node, legend} or null (no chart).
  function buildChart(key, panel, channel) {
    if (key === 'trends') return trendChart(panel, channel);
    if (key === 'changes') { const host = waterfall(panel, channel); return host ? {node: host} : null; }
    if (key === 'favorites') { const list = rankedList(panel, channel); return list ? {node: list} : null; }
    if (key === 'concentration' || key === 'timing') {
      const rows = (panel.rows || []).filter(row => sameChannel(row.funding_source, channel)), chart = makeChart(key, {...panel, rows});
      return chart ? {node: chart} : rows.length ? null : {node: messageBlock(EMPTY_MESSAGE)};
    }
    return null;
  }
  function renderPanel(key, panel) {
    const shell = cardShell(key, panel, 'ready'), {card, heading, info, tools, chartButton, tableButton} = shell;
    const rows = (panel.rows || []).slice(), method = methodRegion(key, panel, card, info), serverOrder = new Map(rows.map((row, index) => [row, index]));
    // Table view opens on the card's channel too: its rows first, review channels last.
    const channelRank = row => !row || !Object.prototype.hasOwnProperty.call(row, 'funding_source') || sameChannel(row.funding_source, selected) ? 0 : DIAGNOSTIC_CHANNELS.has(String(row.funding_source ?? '')) ? 2 : 1;
    if (!rows.length) {
      card.dataset.state = 'empty'; chartButton.disabled = tableButton.disabled = true;
      card.append(messageBlock(key === 'forecast' ? forecastReason(panel) : EMPTY_MESSAGE), method); el('results').append(card); return;
    }
    const subtitle = node('div', undefined, 'insights-card-subtitle'), chartArea = node('div', undefined, 'insights-chart-area'), tableHost = node('div', undefined, 'insights-table-host');
    const channels = cardChannels(key, panel);
    let selected = cardChannel(key, channels), legend = null, view = null;
    const draw = () => {
      if (key === 'changes' && view && view.node.classList.contains('insights-waterfall')) { showChannelFigure(view.node, selected); return; }
      view = selected === null && key !== 'changes' ? {node: messageBlock('Only review channels are observed in this scope. Choose one to inspect.')} : buildChart(key, panel, selected);
      chartArea.replaceChildren(...(view ? [view.node] : []));
      legend?.remove(); legend = view && view.legend || null; if (legend) heading.insertBefore(legend, tools);
    };
    draw();
    const hasChart = Boolean(view);
    subtitle.replaceChildren();
    if (hasChart && channels.length > 1) subtitle.append(channelControl(key, channels, selected, value => { selected = value; chartChannels[key] = value; draw(); if (!sorts[key]) renderTable(); }));
    else if (hasChart && channels[0]) subtitle.append(node('span', fundingLabel(channels[0]), 'insights-card-channel'));
    if (key === 'favorites' && hasChart) { const scope = result && result.scope || {}; subtitle.append(node('span', periodRanking(panel) ? scope.current_cycle + ' vs ' + scope.baseline_cycle + ', same stage' : 'All observed cycles', 'insights-card-note')); }
    tableHost.id = 'insights-table-' + key; tableButton.setAttribute('aria-controls', tableHost.id);
    const renderTable = () => {
      const sort = sorts[key], previous = tableHost.querySelector('.insights-table-wrap'), scrollLeft = previous ? previous.scrollLeft : 0, scrollTop = previous ? previous.scrollTop : 0;
      if(sort)rows.sort((a,b)=>{const av=a[sort.key],bv=b[sort.key];if(av===null||av===undefined)return bv===null||bv===undefined?0:1;if(bv===null||bv===undefined)return -1;return(typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),undefined,{numeric:true}))*sort.direction;});else rows.sort((a,b)=>channelRank(a)-channelRank(b)||serverOrder.get(a)-serverOrder.get(b));
      const wrap = makeTable(panel.columns || [], rows, {key, sort, onSort: column => { sorts[key] = {key: column.key, direction: sort && sort.key === column.key ? -sort.direction : -1}; renderTable(); Array.from(tableHost.querySelectorAll('.insights-sort')).find(button => button.dataset.column === column.key)?.focus({preventScroll: true}); }});
      const parts = [wrap], total = Number(panel.total_rows);
      if (key === 'favorites') {
        const scope = result && result.scope || {};
        const pair = periodRanking(panel) && scope.baseline_cycle && scope.current_cycle ? ' Baseline/current columns compare ' + scope.baseline_cycle + ' and ' + scope.current_cycle + ' at matching same-stage cutoffs.' : '';
        parts.unshift(node('p', 'Net amounts and shares cover every observed cycle in the analysis data.' + pair, 'insights-card-muted'));
      }
      if (panel.truncated || (Number.isFinite(total) && total > rows.length)) parts.push(node('p', 'Showing ' + number.format(rows.length) + ' of ' + number.format(Number.isFinite(total) ? total : rows.length) + ' rows', 'insights-table-count'));
      tableHost.replaceChildren(...parts); wrap.scrollLeft = scrollLeft; wrap.scrollTop = scrollTop;
    };
    renderTable();
    const setView = table => {
      tablesShown[key] = table; tableHost.hidden = !table; chartArea.hidden = table; subtitle.hidden = table || !subtitle.childElementCount; if (legend) legend.hidden = table;
      chartButton.setAttribute('aria-pressed', String(!table)); tableButton.setAttribute('aria-pressed', String(table)); tableButton.setAttribute('aria-expanded', String(table));
    };
    chartButton.disabled = !hasChart; if (!hasChart) chartButton.title = key === 'trends' ? 'This response has no dated series for a running total.' : 'This analysis has no chart view.';
    chartButton.addEventListener('click', () => setView(false)); tableButton.addEventListener('click', () => setView(true));
    setView(!hasChart || Boolean(tablesShown[key]));
    heading.append(subtitle); card.append(chartArea, tableHost, method); el('results').append(card);
  }
  function makeTable(columns, rows, config) {
    const wrap = node('div',undefined,'insights-table-wrap'); wrap.tabIndex = 0; wrap.setAttribute('role','region'); wrap.setAttribute('aria-label',config && config.key ? 'Data for ' + config.key : 'Underlying records');
    const table = node('table',undefined,'insights-table'), thead = node('thead'), head = node('tr'), tbody = node('tbody');
    const sources = Boolean(config && rows.some(row => row.filters && typeof row.filters === 'object' && Object.keys(row.filters).length));
    for (const column of columns) {
      const th = node('th'); th.scope = 'col'; if (['money','percent','number'].includes(column.type)) th.className = 'insights-numeric';
      if (config && config.onSort) { const button = node('button', column.label || column.key,'insights-sort'); button.type = 'button'; button.dataset.column=column.key; button.setAttribute('aria-label','Sort by ' + (column.label || column.key)); button.addEventListener('click',() => config.onSort(column)); th.append(button); if (config.sort?.key === column.key) th.setAttribute('aria-sort',config.sort.direction === 1 ? 'ascending' : 'descending'); } else th.textContent = column.label || column.key;
      head.append(th);
    }
    if (sources) head.append(node('th','Evidence')); thead.append(head);
    for (const row of rows) {
      const tr = node('tr');
      for (const column of columns) {
        const td=node('td',display(row[column.key],column),['money','percent','number'].includes(column.type)?'insights-numeric':undefined);
        if(column.type==='url' && /^https?:\/\//i.test(String(row[column.key] || ''))) {const link=node('a','Open source','insights-source-link');link.href=String(row[column.key]);link.target='_blank';link.rel='noopener noreferrer';link.title=String(row[column.key]);td.replaceChildren(link);}tr.append(td);
      }
      if (sources) {
        const td = node('td');
        if (row.filters && typeof row.filters === 'object' && Object.keys(row.filters).length) {
          const choices = row.comparison_scopes ? Object.entries(row.comparison_scopes).map(([label,filters]) => ({label:label==='baseline'?'Baseline records':'Current records',filters})) : [{label:'View records',filters:row.filters}];
          if (config.key === 'trends' && row.matched_through && Array.isArray(row.filters)) { choices[0].label = 'Full observed'; choices.push({label:'Same-stage records',filters:row.matched_filters || [...row.filters,{field:'date',op:'lte',value:row.matched_through},{field:'date',op:'gte',value:String(row.cycle || '').slice(0,4)+'-01-01'}]}); }
          for (const choice of choices) { const button=node('button',choice.label,'insights-source-button'); button.type='button'; button.addEventListener('click',()=>openSources(config.key,{...row,filters:choice.filters},choice.label,button)); td.append(button); }
        } else td.textContent = 'No record filter';
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(thead,tbody); wrap.append(table); return wrap;
  }
  function chartMark(svg,mark,key,row,label,amount,value,filters){
    const context=[label,display(amount,value),result?.scope?.basis_label,result?.scope?.as_of?'through '+result.scope.as_of:'',row.coverage||'Saved coverage; completeness unverified'].filter(Boolean).join(' · ');
    const group=svgNode('g',{'aria-label':context,role:filters?'button':'img',...(filters?{tabindex:0}:{}),class:'insights-mark'});group.append(svgNode('title',{},context),mark);
    if(filters){const select=()=>{document.querySelectorAll('.insights-mark[aria-pressed]').forEach(n=>n.removeAttribute('aria-pressed'));group.setAttribute('aria-pressed','true');openSources(key,{...row,filters},label,group);};group.onclick=select;group.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select();}};}
    svg.append(group);return group;
  }
  function localDate() { const now = new Date(); return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0'); }
  function cutoffLabel(time, scope) {
    const iso = new Date(time).toISOString().slice(0, 10);
    return iso === scope.as_of && iso === localDate() ? 'Today' : new Date(time).toLocaleDateString('en-US', {month: 'short', day: 'numeric', timeZone: 'UTC'});
  }
  /* "Giving through the same point in each cycle": running totals by days before
     the general election, 600 on the left to 0 on election day at the right. The
     earlier cycle is dashed taupe, the later solid teal ending at the cutoff dot.
     Drawn only from the trends panel's dated monthly series, never from totals. */
  function trendChart(panel, channel) {
    const scope = result && result.scope || {}, earlierCycle = scope.baseline_cycle, laterCycle = scope.current_cycle;
    const earlierEnd = cycleEnd(earlierCycle), laterEnd = cycleEnd(laterCycle);
    // Monthly points; a channel with none uses quarterly_series (quarter-end
    // points joined the same way, named in the legend). Never undated totals.
    const monthly = (panel.series || []).filter(entry => entry && /^\d{4}-\d{2}$/.test(String(entry.month || '')));
    const quarterly = (panel.quarterly_series || []).map(entry => { const match = String(entry && entry.quarter || '').match(/^(\d{4})-Q([1-4])$/); return match ? {...entry, month: match[1] + '-' + String(Number(match[2]) * 3).padStart(2, '0')} : null; }).filter(Boolean);
    if ((!monthly.length && !quarterly.length) || !earlierEnd || !laterEnd) return null;
    const byQuarter = !monthly.some(entry => sameChannel(entry.funding_source, channel)) && quarterly.some(entry => sameChannel(entry.funding_source, channel)), series = byQuarter ? quarterly : monthly;
    const row = cycle => (panel.rows || []).find(item => sameChannel(item.funding_source, channel) && item.cycle === cycle);
    const earlierRow = row(earlierCycle), laterRow = row(laterCycle);
    const earlier = runningTotals(series, channel, earlierEnd, {exactAt: earlierRow && earlierRow.matched_through, exact: earlierRow && earlierRow.matched_amount_cents});
    const later = runningTotals(series, channel, laterEnd, {through: laterRow && laterRow.matched_through || scope.as_of, exact: laterRow && laterRow.matched_amount_cents});
    const legend = node('div', undefined, 'insights-chart-legend');
    for (const [cycle, className] of [[earlierCycle, 'insights-legend-earlier'], [laterCycle, 'insights-legend-later']]) { const entry = node('span', undefined, className); entry.append(node('i'), document.createTextNode(cycle + ' cycle')); legend.append(entry); }
    if (byQuarter) legend.append(node('span', 'quarterly points', 'insights-legend-quarterly'));
    if (!earlier.length && !later.length) return {node: messageBlock(EMPTY_MESSAGE), legend};
    const figure = responsiveFigure('Running totals', (target, width) => drawTrend(target, width, {earlier, later, earlierCycle, laterCycle, laterRow, channel, scope}));
    figure.classList.add('insights-trend');
    return {node: figure, legend};
  }
  function drawTrend(figure, width, lines) {
    const height = 219, left = 49, right = 72, top = 18, bottom = 42, plotWidth = Math.max(160, width - left - right), plotHeight = height - top - bottom;
    const endPoint = lines.later[lines.later.length - 1] || null, span = Math.max(600, endPoint ? Math.ceil(endPoint.days / 100) * 100 : 600);
    const x = days => left + (span - days) / span * plotWidth, values = [0];
    for (const line of [lines.earlier, lines.later]) line.forEach((point, index) => {
      if (point.days <= span && point.days >= 0) values.push(point.value);
      const next = line[index + 1];
      if (next) for (const edge of [span, 0]) if ((point.days - edge) * (next.days - edge) < 0) values.push(point.value + (next.value - point.value) * (point.days - edge) / (point.days - next.days));
    });
    const ticks = niceTicks(Math.min(...values), Math.max(...values), 7), low = ticks[0], high = ticks[ticks.length - 1];
    const y = value => top + (high - value) / (high - low || 1) * plotHeight, label = endPoint ? cutoffLabel(endPoint.time, lines.scope) : '';
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': 'Cumulative net giving by days before the general election, ' + (lines.channel ? fundingLabel(lines.channel) : 'selected channel') + '. ' + lines.earlierCycle + ' dashed, ' + lines.laterCycle + ' solid' + (endPoint ? ', ' + wholeMoney.format(endPoint.value / 100) + ' through ' + label : '') + '.'});
    const clipId = 'insights-trend-clip-' + (++clipCounter), defs = svgNode('defs'), clip = svgNode('clipPath', {id: clipId});
    clip.append(svgNode('rect', {x: left, y: top - 3, width: plotWidth, height: plotHeight + 6})); defs.append(clip); svg.append(defs);
    for (const tick of ticks) {
      svg.append(svgNode('line', {x1: left, x2: Math.max(left + plotWidth, width - 6), y1: y(tick), y2: y(tick), class: tick === low ? 'insights-axis' : 'insights-grid'}));
      svg.append(svgNode('text', {x: left - 15, y: y(tick) + 4, 'text-anchor': 'end'}, tickMoney(tick, true)));
    }
    for (let days = span; days >= 0; days -= 100) {
      const px = x(days);
      svg.append(svgNode('line', {x1: px, x2: px, y1: top, y2: top + plotHeight, class: days === span ? 'insights-axis' : 'insights-grid'}));
      svg.append(svgNode('text', {x: px, y: top + plotHeight + 19, 'text-anchor': 'middle'}, String(days)));
    }
    svg.append(svgNode('text', {x: left + plotWidth / 2, y: top + plotHeight + 37, 'text-anchor': 'middle', class: 'insights-axis-title'}, 'Days before the election'));
    const path = points => points.map((point, index) => (index ? 'L' : 'M') + x(point.days).toFixed(1) + ' ' + y(point.value).toFixed(1)).join(' ');
    // The reveal animates the outer group; the inner one keeps the plot clip.
    const reveal = svgNode('g', {class: 'insights-trend-lines'}), group = svgNode('g', {'clip-path': 'url(#' + clipId + ')'});
    if (lines.earlier.length) group.append(svgNode('path', {d: path(lines.earlier), class: 'insights-line-earlier'}));
    if (lines.later.length) group.append(svgNode('path', {d: path(lines.later), class: 'insights-line-later'}));
    reveal.append(group); svg.append(reveal);
    if (endPoint && endPoint.days >= 0 && endPoint.days <= span) {
      const px = x(endPoint.days), py = y(endPoint.value), flip = px + 12 + 66 > width, lx = flip ? px - 12 : px + 12;
      svg.append(svgNode('line', {x1: px, x2: px, y1: top - 5, y2: top + plotHeight, class: 'insights-today-rule'}));
      svg.append(svgNode('text', {x: px, y: top - 11, 'text-anchor': 'middle', class: 'insights-today-top'}, label));
      chartMark(svg, svgNode('circle', {cx: px, cy: py, r: 4.5, class: 'insights-today-dot'}), 'trends', lines.laterRow || {}, lines.laterCycle + ' · same-stage net through ' + label, endPoint.value, {type: 'money'}, lines.laterRow && lines.laterRow.matched_filters);
      const text = svgNode('text', {x: lx, y: py + 1, 'text-anchor': flip ? 'end' : 'start', class: 'insights-today-label'});
      text.append(svgNode('tspan', {x: lx, dy: 0}, label), svgNode('tspan', {x: lx, dy: 18, class: 'insights-today-value'}, millions(endPoint.value)));
      svg.append(text);
    }
    figure.append(svg);
  }
  /* "What changed": Baseline <earlier> -> continuing, newly observed and
     no-longer-observed recipients -> Current <later>, one exactly reconciled
     figure per funding channel. The company decomposition stays in the table. */
  function waterfall(panel, selected) {
    const host = node('div', undefined, 'insights-waterfall'), scope = result && result.scope || {};
    for (const card of panel.cards || []) {
      const channel = String(card.funding_source ?? ''), components = (panel.recipient_components || []).filter(row => sameChannel(row.funding_source, channel));
      if (!components.length) continue;
      const reconciled = Number.isSafeInteger(card.baseline_cents) && Number.isSafeInteger(card.current_cents) && components.every(row => Number.isSafeInteger(row.change_cents)) && card.baseline_cents + components.reduce((sum, row) => sum + row.change_cents, 0) === card.current_cents;
      const item = reconciled ? responsiveFigure('Change waterfall', (figure, width) => drawWaterfall(figure, width, card, components, scope, channel)) : messageBlock('Change components do not reconcile; see exact returned values in the table.');
      item.dataset.channel = channel; host.append(item);
    }
    if (!host.children.length) return null;
    showChannelFigure(host, selected);
    return host;
  }
  function showChannelFigure(host, selected) {
    let shown = false;
    for (const item of host.children) {
      if (item.classList.contains('insights-waterfall-missing')) { item.remove(); continue; }
      item.hidden = item.dataset.channel !== String(selected ?? ''); if (!item.hidden) { shown = true; if (item.matches('figure')) plotDrawers.get(item)?.(Math.max(240, item.clientWidth)); }
    }
    if (!shown) { const missing = messageBlock(selected === null ? 'Only review channels are observed in this scope. Choose one to inspect.' : EMPTY_MESSAGE); missing.classList.add('insights-waterfall-missing'); host.append(missing); }
  }
  // Steps in board order. A no-longer-observed step opens its earlier-period
  // records (it has none in the later period); the others open the later period.
  function componentSteps(components) {
    const order = Object.keys(COMPONENT_LABELS), rank = row => { const index = order.indexOf(row.component); return index < 0 ? order.length : index; };
    return components.slice().sort((a, b) => rank(a) - rank(b)).map(row => ({row, component: row.component, change: row.change_cents, filters: row.component === 'No longer observed recipients' && row.comparison_scopes ? row.comparison_scopes.baseline : row.filters}));
  }
  function drawWaterfall(figure, width, card, components, scope, channel) {
    const first = card.baseline_cents, last = card.current_cents;
    let running = first;
    const bars = [{label: ['Baseline', scope.baseline_cycle || ''], from: 0, to: running, amount: running, total: true}];
    for (const step of componentSteps(components)) {
      bars.push({label: COMPONENT_LABELS[step.component] || [String(step.component || '').replace(/ recipients$/, '')], from: running, to: running + step.change, amount: step.change, row: step.row, filters: step.filters});
      running += step.change;
    }
    bars.push({label: ['Current', scope.current_cycle || ''], from: 0, to: last, amount: last, total: true});
    const height = 199, left = 40, right = 4, top = 12, bottom = 40, plotWidth = Math.max(120, width - left - right), plotHeight = height - top - bottom;
    const levels = bars.flatMap(bar => [bar.from, bar.to]), peak = Math.max(1, ...levels), ticks = niceTicks(Math.min(0, ...levels), peak, 6);
    // Head-room: a bar that reaches the top tick keeps its value label inside the plot.
    if (ticks.length > 1 && (ticks[ticks.length - 1] - peak) / (ticks[ticks.length - 1] - ticks[0]) * plotHeight < 10) ticks.push(ticks[ticks.length - 1] + ticks[1] - ticks[0]);
    const low = ticks[0], high = ticks[ticks.length - 1];
    const y = value => top + (high - value) / (high - low || 1) * plotHeight, step = plotWidth / bars.length, barWidth = Math.min(64, step * .61);
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': 'Change waterfall for ' + (channel ? fundingLabel(channel) : 'the selected channel') + ': ' + money.format(first / 100) + ' to ' + money.format(last / 100) + ' by recipient, reconciled in signed cents.'});
    for (const tick of ticks) {
      svg.append(svgNode('line', {x1: left, x2: left + plotWidth, y1: y(tick), y2: y(tick), class: tick === 0 ? 'insights-axis' : 'insights-grid'}));
      svg.append(svgNode('text', {x: left - 10, y: y(tick) + 4, 'text-anchor': 'end'}, tickMoney(tick, false)));
    }
    bars.forEach((bar, index) => {
      const bx = left + index * step + (step - barWidth) / 2, upper = y(Math.max(bar.from, bar.to)), extent = Math.abs(y(bar.from) - y(bar.to)), size = Math.max(1.5, extent);
      // A zero-height bar still shows as a hairline at its level.
      const rect = svgNode('rect', {x: bx, y: extent < 1.5 ? upper - .75 : upper, width: barWidth, height: size, class: bar.total ? 'insights-waterfall-total' : bar.amount < 0 ? 'insights-bar-negative' : 'insights-bar'});
      chartMark(svg, rect, 'changes', bar.row || {}, bar.label.join(' '), bar.amount, {type: 'money'}, bar.filters);
      // A falling step is labelled under its bar unless that would reach the x
      // labels (a step that ends at the axis); then it is labelled above, like the rest.
      const foot = y(Math.min(bar.from, bar.to)), below = !bar.total && bar.amount < 0 && foot + 15 <= top + plotHeight - 3;
      svg.append(svgNode('text', {x: bx + barWidth / 2, y: below ? foot + 15 : upper - 7, 'text-anchor': 'middle', class: bar.total ? 'insights-value-total' : bar.amount > 0 ? 'insights-value-up' : bar.amount < 0 ? 'insights-value-down' : 'insights-value-zero'}, millions(bar.amount, !bar.total)));
      if (index < bars.length - 1) svg.append(svgNode('line', {x1: bx + barWidth, x2: bx + step, y1: y(bar.to), y2: y(bar.to), class: 'insights-connector'}));
      const text = svgNode('text', {x: bx + barWidth / 2, y: top + plotHeight + 17, 'text-anchor': 'middle', class: 'insights-xlabel'});
      bar.label.filter(Boolean).forEach((word, line) => text.append(svgNode('tspan', {x: bx + barWidth / 2, dy: line ? 14 : 0}, word))); svg.append(text);
    });
    figure.append(svg);
  }
  // "Top recipients", Chart view: the ranked list on board I1 (# | Recipient |
  // Amount | Change). With a period pair, Amount is the later period's same-stage
  // net and Change its difference from the earlier one, ranked by the engine over
  // every recipient. Without a pair the returned all-cycle amounts are ranked.
  function periodRanking(panel) { return Array.isArray(panel.current_period_leaders) && (panel.rows || []).some(row => Number.isSafeInteger(row.change_cents)); }
  function rankedList(panel, channel) {
    if (periodRanking(panel)) return periodList(panel, channel);
    const columns = panel.columns || [], label = columns.find(column => column.key === 'recipient') || columns.find(column => column.type === 'text');
    const value = columns.find(column => column.key === 'amount_cents') || columns.find(column => column.type === 'money');
    if (!label || !value) return null;
    const cents = row => Number(row[value.key]) * (value.unit === 'dollars' ? 100 : 1);
    const rows = (panel.rows || []).filter(row => sameChannel(row.funding_source, channel) && row[value.key] !== null && row[value.key] !== '' && Number.isFinite(Number(row[value.key]))).sort((a, b) => cents(b) - cents(a)).slice(0, 8);
    const figure = node('figure', undefined, 'insights-chart insights-ranked-figure');
    if (!rows.length) { figure.append(messageBlock(EMPTY_MESSAGE)); return figure; }
    const table = node('table', undefined, 'insights-ranked'), head = node('tr'), body = node('tbody');
    table.append(node('caption', 'Top recipients by net amount, all observed cycles' + (channel ? ', ' + fundingLabel(channel) : ''), 'insights-visually-hidden'));
    for (const [text, className] of [['#', 'insights-rank'], ['Recipient', 'insights-recipient'], ['Amount', 'insights-numeric']]) { const th = node('th', text, className); th.scope = 'col'; head.append(th); }
    const thead = node('thead'); thead.append(head);
    rows.forEach((row, index) => {
      const tr = node('tr'), name = String(row[label.key] ?? '—'), amount = node('td', millions(cents(row)), 'insights-numeric'), recipient = node('td', name, 'insights-recipient');
      recipient.title = name; amount.title = display(row[value.key], value);
      tr.append(node('td', (index + 1) + '.', 'insights-rank'), recipient, amount); body.append(tr);
    });
    table.append(thead, body); figure.append(table); return figure;
  }
  function periodList(panel, channel) {
    const scope = result && result.scope || {}, figure = node('figure', undefined, 'insights-chart insights-ranked-figure');
    const rows = panel.current_period_leaders.filter(row => sameChannel(row.funding_source, channel) && Number.isSafeInteger(row.current_cents) && Number.isSafeInteger(row.change_cents)).sort((a, b) => b.current_cents - a.current_cents).slice(0, 8);
    if (!rows.length) { figure.append(messageBlock(EMPTY_MESSAGE)); return figure; }
    const table = node('table', undefined, 'insights-ranked'), head = node('tr'), body = node('tbody');
    table.append(node('caption', 'Top recipients by same-stage net amount in ' + (scope.current_cycle || 'the later period') + ', with the change from ' + (scope.baseline_cycle || 'the earlier period') + (channel ? ', ' + fundingLabel(channel) : ''), 'insights-visually-hidden'));
    for (const [text, className] of [['#', 'insights-rank'], ['Recipient', 'insights-recipient'], ['Amount', 'insights-numeric'], ['Change', 'insights-numeric']]) { const th = node('th', text, className); th.scope = 'col'; head.append(th); }
    const thead = node('thead'); thead.append(head);
    rows.forEach((row, index) => {
      const name = String(row.recipient ?? '—'), recipient = node('td', name, 'insights-recipient'), amount = node('td', millions(row.current_cents), 'insights-numeric');
      const change = node('td', millions(row.change_cents, true), 'insights-numeric' + (row.change_cents > 0 ? ' insights-up' : row.change_cents < 0 ? ' insights-down' : ''));
      recipient.title = name; amount.title = money.format(row.current_cents / 100); change.title = signedMoney.format(row.change_cents / 100) + ' vs ' + (scope.baseline_cycle || 'the earlier period');
      const tr = node('tr'); tr.append(node('td', (index + 1) + '.', 'insights-rank'), recipient, amount, change); body.append(tr);
    });
    table.append(thead, body); figure.append(table); return figure;
  }
  // Bars for Concentration and Timing, one channel at a time. A group title sits
  // outside its responsive figure, whose repaint replaces the figure's children.
  function makeChart(key,panel){
    if(key==='concentration'&&panel.rows?.some(r=>r.dimension&&r.name)){
      const host=node('div',undefined,'insights-bar-groups');for(const dimension of new Set(panel.rows.map(r=>r.dimension))){const chart=makeChart('bars',{...panel,rows:panel.rows.filter(r=>r.dimension===dimension),columns:[{key:'name',label:dimension,type:'text'},...panel.columns.filter(c=>c.key!=='name'&&c.key!=='dimension')]});if(chart){const group=node('div',undefined,'insights-bar-group');group.append(node('p',dimension,'insights-bar-group-title'),chart);host.append(group);}}return host.children.length?host:null;
    }
    const cols=panel.columns||[],label=cols.find(c=>c.key===(key==='timing'?'month':'name'))||cols.find(c=>!['money','number','percent'].includes(c.type)),value=cols.find(c=>c.type==='money')||cols.find(c=>['number','percent'].includes(c.type));
    if(!label||!value)return null;
    const valid=(panel.rows||[]).filter(r=>r[value.key]!=null&&r[value.key]!==''&&Number.isFinite(Number(r[value.key])));
    const rows=(key==='timing'?valid:valid.slice().sort((a,b)=>b[value.key]-a[value.key])).slice(0,key==='timing'?30:5);if(!rows.length)return null;
    return responsiveFigure(panel.title||key,(figure,width)=>{
      const compact=width<430,left=compact?0:Math.min(210,width*.36),right=82;
      const layouts=rows.map(row=>{const name=String(row[label.key]),wrapped=root.MentatTheme?.wrapText?.(name,compact?width-4:left-12,13,root.MentatTheme.sans)||[name],lines=compact?wrapped:wrapped.slice(0,2);if(!compact&&wrapped.length>2)lines[1]=root.MentatTheme.fitText(wrapped.slice(1).join(' '),left-12,13,root.MentatTheme.sans);return {name,lines,pitch:compact?56+(lines.length-1)*16:Math.max(34,lines.length*16+18)};});
      let cursor=18;for(const layout of layouts){layout.y=cursor;cursor+=layout.pitch;}const height=cursor+18;
      const low=Math.min(0,...rows.map(r=>r[value.key])),high=Math.max(0,...rows.map(r=>r[value.key])),span=high-low||1,space=width-left-right,zero=left-low/span*space;
      const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,width,height,role:'img','aria-label':(panel.title||key)+' · '+value.label});
      svg.append(svgNode('line',{x1:zero,x2:zero,y1:0,y2:height-24,class:'insights-axis'}));
      rows.forEach((row,i)=>{const amount=row[value.key],{name,y,lines}=layouts[i],by=compact?y+(lines.length-1)*16+12:y-12;
        const text=svgNode('text',{x:0,y});lines.forEach((line,index)=>text.append(svgNode('tspan',{x:0,dy:index?16:0},line)));text.append(svgNode('title',{},name));svg.append(text);
        const rect=svgNode('rect',{x:Math.min(zero,zero+amount/span*space),y:by,width:Math.abs(amount/span*space),height:18,class:amount<0?'insights-bar-negative':'insights-bar'});
        chartMark(svg,rect,key,row,name,amount,value,row.filters);
        svg.append(svgNode('text',{x:width,y:by+14,'text-anchor':'end'},value.type==='money'?compactMoney(amount):display(amount,value)));
      });
      svg.append(svgNode('text',{x:zero,y:height-5},value.type==='money'?'$0':'0'));
      figure.append(svg,node('figcaption',value.label+' · '+rows.length+' of '+(panel.rows||[]).length+' observed rows. Select a mark for exact source records.'));
    });
  }
  function closeSources(restoreFocus=false) { const returnFocus=sourceReturnFocus;sourceRevision++; if (sourceRequest) sourceRequest.abort(); sourceRequest = null; sourceContext = null; el('sources-table').replaceChildren();if(analysisView==='sources')selectAnalysisView('overview');if(restoreFocus){document.querySelectorAll('.insights-mark[aria-pressed]').forEach(mark=>mark.removeAttribute('aria-pressed'));if(returnFocus?.isConnected)returnFocus.focus();}sourceReturnFocus=null; }
  function openSources(key,row,label,trigger) {
    closeSources(); sourceReturnFocus=trigger || null;
    if (!result) {
      el('sources-title').textContent = 'Source records';
      el('sources-status').textContent = 'Run an analysis before opening its contributing records.';
      el('sources-prev').disabled = true; el('sources-next').disabled = true; el('sources-retry').hidden = true;
      selectAnalysisView('sources'); el('sources').focus(); return;
    }
    const resolved = {...result._request};
    // Automatic cycle/cutoff choices become explicit in the evidence request.
    for (const field of ['dataset','basis','company','funding_source','as_of','baseline_cycle','current_cycle','comparison_dataset']) if (Object.prototype.hasOwnProperty.call(result.scope || {},field)) { if(result.scope[field]===null || result.scope[field]==='')delete resolved[field];else resolved[field] = result.scope[field]; }
    delete resolved.retry;
    sourceContext = {query:{...resolved, filters:row.filters,index_token:result.provenance?.index_token}, offset:0};
    el('sources-title').textContent = key ? (label || 'Contributing records') + ' · ' + cardTitle(key, (result.panels || {})[key]) : label || 'Contributing records';
    selectAnalysisView('sources'); el('sources').focus(); loadSources();
  }
  async function loadSources(retry=false) {
    if (!sourceContext || !active) return;
    const ticket = ++sourceRevision; if (sourceRequest) sourceRequest.abort(); sourceRequest = new AbortController();
    const context = sourceContext; el('sources-table').replaceChildren(); el('sources-status').textContent = 'Loading records for the selected analysis row…'; el('sources-retry').hidden = true; el('sources-prev').disabled = true; el('sources-next').disabled = true;
    try {
      const data = await api('/api/insights/records',{...context.query,offset:context.offset,limit:PAGE_SIZE,...(retry===true?{retry:true}:{})},sourceRequest.signal);
      if (ticket !== sourceRevision || !active || !sourceContext) return;
      if (['building','queued','preparing'].includes(data.status)) throw new Error(data.message || 'The record source is still preparing. Retry in a moment.');
      const rows = data.rows || data.records || [];
      const columns = (data.columns || inferredColumns(rows)).map(column=>({...column}));
      for (const [key,label] of [['first_seen_by_insights','First inspected here'],['last_seen_by_insights','Last inspected here']]) {
        const column=columns.find(c=>c.key===key); if(column) column.label=label; else if(rows.some(row=>row[key])) columns.push({key,label,type:'text'});
      }
      if (rows.length) el('sources-table').append(makeTable(columns,rows));
      const total = data.total === undefined ? data.total_rows === undefined ? data.total_records : data.total_rows : data.total;
      el('sources-status').textContent = rows.length ? 'Records ' + (context.offset+1) + '–' + (context.offset+rows.length) + (Number.isFinite(Number(total)) ? ' of ' + total : '') + '. Same industry, basis and contribution cutoff as the analysis.' : 'No source records matched this filter. This does not prove zero giving.';
      el('sources-prev').disabled = context.offset === 0;
      el('sources-next').disabled = data.has_more === undefined ? total === undefined ? rows.length < PAGE_SIZE : context.offset+rows.length >= total : !data.has_more;
      if (data.notices) addNotices(el('sources-table'),data.notices);
      if (rows.some(row=>row.first_seen_by_insights || row.last_seen_by_insights)) el('sources-table').append(node('p','Inspection timestamps describe this workspace’s history, not filing or publication dates.','muted'));
    } catch (error) { if (ticket !== sourceRevision || !active || error.name === 'AbortError') return; el('sources-status').textContent = error.message; el('sources-retry').hidden = false;el('sources-prev').disabled=context.offset===0; }
  }
  function download(kind,panelKey) {
    if (!result) return;
    const clean = {...result}; delete clean._request;
    if(panelKey) clean.panels={[panelKey]:clean.panels[panelKey]};
    const text = kind === 'csv' ? exportCSV(clean) : JSON.stringify({...clean,exported_at:new Date().toISOString()},null,2);
    const blob = new Blob([text],{type:kind === 'csv' ? 'text/csv;charset=utf-8' : 'application/json'}), url = URL.createObjectURL(blob), link = node('a');
    link.href = url; link.download = 'mentat-insights-' + String(result.scope?.dataset || 'industry').replace(/[^a-z0-9_-]/gi,'-') + (panelKey?'-'+panelKey:'') + '.' + kind; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  // Header: Sources opens the records view for the current scope; one Export menu.
  el('page-sources').addEventListener('click',()=>{
    if(analysisView==='sources'){closeSources(true);return;}
    // Not tied to a card: every record in the current scope.
    openSources(null,{filters:[]},'Source records for the current scope',el('page-sources'));
  });
  const exportMenu=el('export-menu');
  document.addEventListener('click',event=>{if(exportMenu.open&&!exportMenu.contains(event.target))exportMenu.open=false;});
  exportMenu.addEventListener('keydown',event=>{if(event.key==='Escape'&&exportMenu.open){exportMenu.open=false;exportMenu.querySelector('summary').focus();}});
  el('export-csv').addEventListener('click',()=>{download('csv');exportMenu.open=false;});
  el('export-json').addEventListener('click',()=>{download('json');exportMenu.open=false;});
  el('more-options').addEventListener('click',()=>{const open=el('options').hidden;el('options').hidden=!open;el('more-options').setAttribute('aria-expanded',String(open));});
  // Swap exchanges the chips only: the time-ordered pair is unchanged, so a held
  // result is redrawn without a request (the strip and cards stay in time order).
  el('swap-periods').addEventListener('click',()=>{const left=chipValue('baseline-cycle'),right=chipValue('current-cycle');if(!left||!right)return;setChip('baseline-cycle',right);setChip('current-cycle',left);if(result&&!loading&&samePair(result.scope,left,right)){renderSummary(result);return;}load();});
  // Period chips always hold two different cycles. Picking the cycle the other
  // chip shows exchanges the pair, as Swap does: the other chip takes this chip's
  // previous cycle (else the neighbouring one). Picking a cycle past the other
  // chip's automatic cycle pins that chip at the cycle it shows, so a later-first
  // pair stays a pair; forward edits leave the other chip automatic.
  for(const [id,other,later] of [['baseline-cycle','current-cycle',true],['current-cycle','baseline-cycle',false]])el(id).addEventListener('change',()=>{
    const value=chipValue(id),previous=shownPeriods[id],shown=chipValue(other);
    if(value&&shown&&sameCycle(value,shown)){const replacement=previous&&!sameCycle(previous,value)?previous:neighbourCycle(value,later);if(replacement)setChip(other,replacement);else labelPeriods();}
    else if(el(id).value&&shown&&!el(other).value&&(later?cycleEnd(value)>cycleEnd(shown):cycleEnd(value)<cycleEnd(shown)))setChip(other,shown);
    else labelPeriods();
    load();
  });
  el('controls').addEventListener('submit',event => { event.preventDefault(); if (!el('controls').reportValidity()) return; load(); });
  for (const id of ['dataset','basis','company','funding-source','as-of','comparison-dataset']) el(id).addEventListener('change',() => {
    if (id === 'dataset') { resetIndustryFilters();root.MentatScope?.write({industry:el('dataset').value}); }
    load();
  });
  for (const [id,value] of [['enable-all',() => KEYS],['core-only',() => CORE]]) el(id).addEventListener('click',() => { enabled = value().filter(panelShown); preferences(); renderToggles(); load(); });
  // The forecast setting. Switching it off also turns the card off, which changes the request; switching it on only
  // brings the switch back, still off, so nothing is fetched until the card itself is chosen.
  el('show-forecast').checked = showForecast;
  el('show-forecast').addEventListener('change', () => {
    showForecast = el('show-forecast').checked;
    const before = enabled.length; enabled = enabled.filter(panelShown);
    preferences(); renderToggles(); if (enabled.length !== before) load();
  });
  el('sources-close').addEventListener('click',()=>closeSources(true)); el('sources-retry').addEventListener('click',()=>loadSources(true));
  el('sources-prev').addEventListener('click',() => { if (sourceContext) { sourceContext.offset = Math.max(0,sourceContext.offset-PAGE_SIZE); loadSources(); } });
  el('sources-next').addEventListener('click',() => { if (sourceContext) { sourceContext.offset += PAGE_SIZE; loadSources(); } });
  renderToggles(); preferences(); selectAnalysisView('overview'); renderSummary(null);
  root.MentatInsights = {setActive(value) { const next = Boolean(value); if (next === active) return; active = next; if (active) {adoptSharedIndustry(root.MentatScope?.read());load();} else { invalidate(); status('Open Insights to load the selected analyses.'); } }};
  root.MentatScope?.subscribe(scope=>{if(adoptSharedIndustry(scope) && active)load();});
  // app.js can resolve a direct #insights link before this deferred module runs.
  if (!document.getElementById('view-insights').hidden) root.MentatInsights.setActive(true);
})(typeof window === 'undefined' ? globalThis : window);
