/* Cross-reference is a view of saved evidence. Opening it never starts a job.
 *
 * Dwarfed on the owner's ruling: the published report workbook is the
 * accounting of record, so this tab carries no totals. It lists evidence-backed
 * contradictions, clear refunds and unusually large counted receipts for
 * inspection. Magnitude alone never excludes a receipt.
 *
 * Laid out to board B16 (console redesign, build 05, 2026-09-22): a run strip,
 * three tabs that carry row counts, a filter row, and one table card with a
 * sticky header, a 25-row pager and the explanatory text behind "How this list
 * is built". Every money figure this view prints is one row's own amount
 * (D-040). The digest also carries linked_amount, unlinked_amount,
 * non_giving_amount and large_amount, which are sums of listed rows; nothing
 * here reads them. */
(function (root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const money = value => numeric(value) ? value.toLocaleString('en-US', {style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}) : 'Unavailable';
  const number = value => numeric(value) ? value.toLocaleString('en-US') : 'Unavailable';
  const items = value => Array.isArray(value) ? value : [];
  const LEDE = 'The published report workbook is the accounting of record. This view lists evidence-backed contradictions, clear refunds and unusually large counted receipts for inspection. Size alone never excludes a receipt; this view publishes no totals.';
  // The engine keys this view still prints, in the plain words a lobbyist
  // reads. Anything unmapped falls back to the key with its underscores
  // removed and its first letter raised, so a new receipt role is legible
  // before it is named here.
  const roles = {
    other_receipt:'Other receipt',
    asset_liquidation:'Asset or liability liquidation',
  };
  const entities = {IND:'Individual',ORG:'Organization',PAC:'Committee',COM:'Committee',CCM:'Committee',PTY:'Party committee',CAN:'Candidate'};
  const named = (table, value) => {
    const key = String(value || '').trim();
    if (!key) return 'Not stated';
    if (Object.hasOwn(table, key)) return table[key];
    const words = key.replaceAll('_', ' ');
    return words.charAt(0).toUpperCase() + words.slice(1);
  };
  const roleText = value => escape(named(roles, value));
  const entityText = value => escape(named(entities, value));

  const PAGE_SIZE = 25;
  const TABS = [
    {id:'contradictions', label:'Contradictions', detail:'Description'},
    {id:'large', label:'Large receipts', detail:'Entity'},
    {id:'refunds', label:'Refunds', detail:'Description'},
  ];
  const TAB_IDS = TABS.map(tab => tab.id);
  // Inline SVG only: the console's policy (img-src 'self' blob:) refuses data: images.
  const icon = (name, body) => `<svg class="xref-icon xref-icon--${name}" viewBox="0 0 16 16" aria-hidden="true" focusable="false">${body}</svg>`;
  const ICONS = {
    search: icon('search', '<circle cx="7" cy="7" r="4.6"/><path d="m10.4 10.4 3.6 3.6"/>'),
    open: icon('open', '<path d="M9.75 1.75h4.5v4.5"/><path d="m14.25 1.75-6.5 6.5"/><path d="M12.75 9v3.75a1.5 1.5 0 0 1-1.5 1.5h-8a1.5 1.5 0 0 1-1.5-1.5v-8a1.5 1.5 0 0 1 1.5-1.5H7"/>'),
    previous: icon('previous', '<path d="M10 3.5 5.5 8l4.5 4.5"/>'),
    next: icon('next', '<path d="M6 3.5 10.5 8 6 12.5"/>'),
    down: icon('down', '<path d="m3.5 6 4.5 4.5L12.5 6"/>'),
  };

  /* One FEC link rule for the whole console: MentatScope.fecLink - https, the
     host exactly www.fec.gov or docquery.fec.gov, no userinfo, the default
     port, the fragment stripped - and this view's own fourth rule, which it
     no longer carries alone: a query string must be an FEC image number.
     That rule guards a credential (see scope.js), so it was raised to the
     shared rule rather than dropped, and the other three callers get it
     too. With no scope.js loaded, nothing here is linked. */
  const SHORT_ID = 22;
  const shortId = text => text.length > SHORT_ID ? text.slice(0, 10) + '…' + text.slice(-9) : text;
  function recordCell(id, url) {
    const text = String(id ?? '').trim();
    const href = globalThis.MentatScope ? globalThis.MentatScope.fecLink(url) : '';
    const label = text ? `<span class="xref-id" title="${escape(text)}">${escape(shortId(text))}</span>`
      : '<span class="xref-id xref-muted">Not saved</span>';
    const link = href ? `<a class="xref-open" href="${escape(href)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer" title="Open the public FEC filing" aria-label="Open the public FEC filing for record ${escape(text || 'not saved')}">${ICONS.open}</a>` : '';
    return `<span class="xref-record">${label}${link}</span>`;
  }
  const companyText = row => escape(row.company || 'Company not stated');
  const described = row => row.description ? escape(row.description) : '<span class="xref-muted">No receipt description saved</span>';
  const COLUMNS = {
    contradictions: [
      {title:'Company', col:'company', cell:companyText},
      {title:'Cycle', col:'cycle', cell:row => escape(row.cycle)},
      {title:'Description', col:'detail', cell:described},
      {title:'Role', col:'role', cell:row => roleText(row.role)},
      {title:'Amount removed', col:'amount', number:true, cell:row => money(row.amount)},
      {title:'FEC record', col:'record', cell:row => recordCell(row.record_id, row.source_url)},
    ],
    large: [
      {title:'Company', col:'company', cell:companyText},
      {title:'Cycle', col:'cycle', cell:row => escape(row.cycle)},
      {title:'Entity', col:'detail', cell:row => entityText(row.entity)},
      {title:'Role', col:'role', cell:row => roleText(row.role)},
      {title:'Amount', col:'amount', number:true, cell:row => money(row.amount)},
      {title:'FEC record', col:'record', cell:row => recordCell(row.record_id, row.source_url)},
    ],
    linked: [
      {title:'Company', col:'company', cell:companyText},
      {title:'Cycle', col:'cycle', cell:row => escape(row.cycle)},
      {title:'Refund', col:'money', number:true, cell:row => money(row.refund_amount)},
      {title:'Original', col:'money', number:true, cell:row => money(row.original_amount)},
      {title:'Refund record', col:'record', cell:row => recordCell(row.refund_record_id, row.refund_source_url)},
      {title:'Original record', col:'record', cell:row => recordCell(row.original_record_id, row.original_source_url)},
    ],
    unlinked: [
      {title:'Company', col:'company', cell:companyText},
      {title:'Cycle', col:'cycle', cell:row => escape(row.cycle)},
      {title:'Description', col:'detail', cell:described},
      {title:'Amount', col:'amount', number:true, cell:row => money(row.amount)},
      {title:'FEC record', col:'record', cell:row => recordCell(row.record_id, row.source_url)},
    ],
  };
  const NAMES = {contradictions:'Evidence-backed contradictions to giving', large:'Unusually large receipts retained for inspection',
    linked:'Clear refunds', unlinked:'Negative adjustments without a matched original'};
  const EMPTY = {
    contradictions:'No receipt the accepted report counted was reclassified as non-giving.',
    large:'No counted receipt in this run reached the size stated below.',
    linked:'No refund in this run is matched to the gift it reverses.',
    unlinked:'Every negative adjustment in this run is matched to an original gift.',
  };
  const UNMATCHED = {
    contradictions:'No contradiction matches these filters.', large:'No large receipt matches these filters.',
    linked:'No matched refund matches these filters.', unlinked:'No negative adjustment matches these filters.',
  };

  function table(kind, rows) {
    const columns = COLUMNS[kind];
    const cls = column => `xref-col-${column.col}${column.number ? ' number' : ''}`;
    return `<table class="xref-table xref-table--${kind}" aria-label="${NAMES[kind]}"><thead><tr>` +
      columns.map(column => `<th scope="col" class="${cls(column)}">${column.title}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map(row => '<tr>' + columns.map(column => `<td class="${cls(column)}">${column.cell(row)}</td>`).join('') + '</tr>').join('') +
      '</tbody></table>';
  }

  /* ------------------------------------------------ rows, filters and pages */
  const blankFilters = () => ({company:'', cycle:'', detail:'', search:''});
  function initialState(tab) {
    return {tab: TAB_IDS.includes(tab) ? tab : 'contradictions',
      filters: Object.fromEntries(TAB_IDS.map(id => [id, blankFilters()])),
      page: Object.fromEntries(TAB_IDS.map(id => [id, 0])),
      companies:false, method:false, enter:false};
  }
  // A caller may pass part of a state (a test asks for one tab or one filter).
  function normalized(state) {
    const base = initialState(state && state.tab);
    if (!state) return base;
    for (const id of TAB_IDS) {
      base.filters[id] = {...base.filters[id], ...(state.filters && state.filters[id])};
      base.page[id] = Number.isInteger(state.page && state.page[id]) && state.page[id] > 0 ? state.page[id] : 0;
    }
    return {...base, companies:!!state.companies, method:!!state.method, enter:!!state.enter};
  }
  // Option values: '' is "all", 'none' is a blank value, 'v:<text>' is a value.
  const code = value => { const text = String(value ?? '').trim(); return text ? 'v:' + text : 'none'; };
  const detailOf = (tab, kind, row) => tab === 'large' ? named(entities, row.entity) : kind === 'linked' ? null : row.description;

  function model(digest) {
    const refunds = (digest && digest.refunds) || {}, outliers = (digest && digest.outliers) || {};
    const tagged = (kind, rows) => items(rows).filter(row => row && typeof row === 'object').map(row => ({kind, row}));
    const linked = tagged('linked', refunds.linked), unlinked = tagged('unlinked', refunds.unlinked);
    return {
      basis: (digest && digest.basis) || '',
      contradictions: {rows: tagged('contradictions', outliers.non_giving), count: outliers.non_giving_count,
        lists: [[outliers.non_giving_count, items(outliers.non_giving).length, 'reclassified receipt', 'reclassified receipts']]},
      large: {rows: tagged('large', outliers.large), count: outliers.large_count,
        lists: [[outliers.large_count, items(outliers.large).length, 'large receipt', 'large receipts']]},
      // Refunds counts rows two ways, and the tab says how many rows in all.
      refunds: {rows: [...linked, ...unlinked],
        count: numeric(refunds.linked_count) && numeric(refunds.unlinked_count) ? refunds.linked_count + refunds.unlinked_count : null,
        lists: [[refunds.linked_count, items(refunds.linked).length, 'linked refund', 'linked refunds'],
                [refunds.unlinked_count, items(refunds.unlinked).length, 'unmatched negative adjustment', 'unmatched negative adjustments']]},
    };
  }

  function options(m, tab) {
    const collect = (pick, blank) => {
      const seen = new Map();
      for (const {kind, row} of m[tab].rows) {
        const value = pick(kind, row);
        if (value === null) continue;
        const key = code(value);
        if (!seen.has(key)) seen.set(key, key === 'none' ? blank : String(value).trim());
      }
      return [...seen].map(([value, text]) => ({value, text})).sort((a, b) => a.text.localeCompare(b.text, 'en', {numeric:true}));
    };
    return {company: collect((kind, row) => row.company, 'Company not stated'),
      cycle: collect((kind, row) => row.cycle, 'Cycle not stated'),
      detail: collect((kind, row) => detailOf(tab, kind, row), 'No receipt description saved')};
  }

  // A filter whose value is gone from this run's options is dropped, so the
  // controls never claim "all" while a stale value still narrows the rows.
  function settle(m, state) {
    const next = normalized(state);
    for (const id of TAB_IDS) {
      const choices = options(m, id), filters = next.filters[id];
      for (const key of ['company', 'cycle', 'detail']) {
        if (filters[key] && !choices[key].some(option => option.value === filters[key])) filters[key] = '';
      }
      filters.search = String(filters.search || '');
    }
    return next;
  }
  const filtering = (state, tab = state.tab) => Object.values(state.filters[tab]).some(value => String(value).trim());
  function visible(m, state) {
    const tab = state.tab, filters = state.filters[tab], needle = filters.search.trim().toLowerCase();
    return m[tab].rows.filter(({kind, row}) => {
      if (filters.company && code(row.company) !== filters.company) return false;
      if (filters.cycle && code(row.cycle) !== filters.cycle) return false;
      if (filters.detail) {
        const detail = detailOf(tab, kind, row);
        if (detail === null || code(detail) !== filters.detail) return false;
      }
      if (needle) {
        const text = [row.description, row.record_id, row.refund_record_id, row.original_record_id]
          .map(value => String(value ?? '')).join('\n').toLowerCase();
        if (!text.includes(needle)) return false;
      }
      return true;
    });
  }
  function pageOf(m, state) {
    const rows = visible(m, state), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const page = Math.min(state.page[state.tab], pages - 1);
    return {rows, page, pages, slice: rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)};
  }
  // How many rows exist, and whether this page can show all of them. A count
  // of listed rows is not a total of money and never becomes one.
  function listed(count, shown, singular, plural) {
    if (!numeric(count)) return `The number of ${plural} in this run is unavailable.`;
    if (count <= shown) return '';
    return `${number(count)} ${count === 1 ? singular : plural} in this run. The ${number(shown)} largest are listed here; the saved digest holds the rest.`;
  }
  function pager(m, state, view) {
    const total = view.rows.length, first = view.page * PAGE_SIZE;
    return {
      text: total ? `Rows ${number(first + 1)}-${number(Math.min(first + PAGE_SIZE, total))} of ${number(total)}` : 'No rows',
      previous: view.page > 0, next: view.page < view.pages - 1,
      note: m[state.tab].lists.map(list => listed(...list)).filter(Boolean).join(' '),
    };
  }

  /* ------------------------------------------------------------- markup */
  function body(m, state, view) {
    const message = kind => `<p class="xref-empty">${escape((filtering(state) ? UNMATCHED : EMPTY)[kind])}</p>`;
    if (state.tab !== 'refunds') return view.slice.length ? table(state.tab, view.slice.map(entry => entry.row)) : message(state.tab);
    const linked = view.slice.filter(entry => entry.kind === 'linked').map(entry => entry.row);
    const unlinked = view.slice.filter(entry => entry.kind === 'unlinked').map(entry => entry.row);
    const unlinkedTotal = view.rows.filter(entry => entry.kind === 'unlinked').length;
    let content = linked.length ? table('linked', linked) : view.page === 0 ? message('linked') : '';
    if (unlinked.length || (!unlinkedTotal && view.page === view.pages - 1)) {
      content += '<section class="xref-adjustments" aria-labelledby="xref-unlinked-heading">' +
        '<h3 class="xref-subhead" id="xref-unlinked-heading">Negative adjustments without a matched original</h3>' +
        (unlinked.length ? table('unlinked', unlinked) : message('unlinked')) + '</section>';
    }
    return content;
  }

  const runTime = data => {
    // The runner mints run ids from the UTC second the run started
    // (20260921T055153-...); the report's own generation time stands in
    // for an id that carries no time.
    const match = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})\d{2}/.exec(String(data.run_id || ''));
    if (match) return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
    const generated = data.report && data.report.generated_at;
    const parsed = typeof generated === 'string' ? Date.parse(generated.replace(/(\.\d{3})\d+/, '$1')) : NaN;
    if (!Number.isFinite(parsed)) return '';
    const iso = new Date(parsed).toISOString();
    return iso.slice(0, 10) + ' ' + iso.slice(11, 16);
  };
  function runStrip(data, state) {
    const report = data.report, scope = (report && report.scope) || {};
    const status = data.status === 'completed' ? 'Cross-referencing complete' : data.status === 'failed' ? 'Run failed' : 'Cross-referencing';
    const facts = [];
    if (report) {
      facts.push(escape(items(scope.industries).join(', ') || 'Industry scope unavailable'));
      facts.push(items(scope.cycles).length ? 'cycles ' + escape(items(scope.cycles).join(', ')) : 'cycles unavailable');
      const companies = numeric(scope.company_count) ? scope.company_count : items(scope.companies).length;
      facts.push(`${number(companies)} ${companies === 1 ? 'company' : 'companies'}`);
    }
    const when = runTime(data);
    const about = `Run ${data.run_id || 'unknown'}${report && report.generated_at ? ' · report generated ' + report.generated_at : ''} · times in UTC`;
    facts.push(`<span class="xref-run-time" title="${escape(about)}">run ${escape(when || data.run_id || 'unknown')}</span>`);
    let content = `<section class="card xref-run" aria-labelledby="xref-run-heading"><h2 class="sr-only" id="xref-run-heading">What this run covers</h2>` +
      `<span class="badge xref-status">${status}</span><p class="xref-scope">${facts.map(fact => `<span>${fact}</span>`).join('')}</p>`;
    if (report) {
      const names = items(scope.companies);
      content += `<button type="button" class="xref-disclose xref-companies-toggle" id="xref-companies-toggle" aria-expanded="${state.companies}" aria-controls="xref-companies">Companies in scope${ICONS.next}</button>` +
        `<div class="xref-companies" id="xref-companies"${state.companies ? '' : ' hidden'}><p>${escape(names.join(', ') || 'Company scope unavailable')}</p>` +
        (scope.company_count > names.length ? '<p class="xref-muted">Company names are truncated in this display; the full scope is preserved in the saved report.</p>' : '') +
        `<p class="xref-muted">${escape(about)}</p></div>`;
    }
    return content + '</section>';
  }

  function tabs(m, state) {
    return `<div class="xref-tabs${state.enter ? ' xref-enter' : ''}" role="tablist" aria-label="Saved evidence">` + TABS.map(tab => {
      const selected = tab.id === state.tab, count = m[tab.id].count;
      // The hidden bold twin holds each tab at its bold width, so choosing a tab
      // never moves its neighbours and the count still follows its own label.
      const face = `<span class="xref-tab-label">${tab.label}</span>${numeric(count) ? `<span class="xref-tab-count">${number(count)}</span>` : ''}`;
      return `<button type="button" role="tab" class="xref-tab" id="xref-tab-${tab.id}" data-xref-tab="${tab.id}" aria-controls="xref-panel" aria-selected="${selected}" tabindex="${selected ? 0 : -1}">` +
        `<span class="xref-tab-face">${face}</span><span class="xref-tab-face xref-tab-ghost" aria-hidden="true">${face}</span></button>`;
    }).join('') + '</div>';
  }

  function filters(m, state) {
    const chosen = state.filters[state.tab], choices = options(m, state.tab), detail = TABS.find(tab => tab.id === state.tab).detail;
    const select = (key, label) => `<label class="chip-select xref-filter"><span class="sr-only">${label}</span>` +
      `<select id="xref-filter-${key}" data-xref-filter="${key}" aria-label="${label}"><option value="">${label}</option>` +
      choices[key].map(option => `<option value="${escape(option.value)}"${option.value === chosen[key] ? ' selected' : ''}>${escape(option.text)}</option>`).join('') +
      '</select></label>';
    return '<div class="xref-filters" role="search" aria-label="Filter the rows of this tab">' +
      select('company', 'Company') + select('cycle', 'Cycle') + select('detail', detail) +
      `<label class="xref-search">${ICONS.search}<span class="sr-only">Search description or record ID</span>` +
      `<input type="search" id="xref-search" data-xref-filter="search" placeholder="Search description or record ID" value="${escape(chosen.search)}" autocomplete="off" spellcheck="false"></label></div>`;
  }

  function method(m, state) {
    return `<div class="xref-method" id="xref-method"${state.method ? '' : ' hidden'}>` +
      `<p>${escape(LEDE)}</p>` +
      '<h3>Contradictions and large receipts</h3>' +
      '<h4>Evidence-backed contradictions to giving</h4>' +
      '<p>Receipts the accepted report had counted as giving that this run contradicted with explicit evidence: loans, interest, dividends, fees and transfers in.</p>' +
      '<h4>Unusually large receipts retained for inspection</h4>' +
      '<p>These receipts remain counted. Magnitude alone is not a contradiction and never excludes a receipt.</p>' +
      `<p class="xref-basis">${escape(m.basis || 'The size that selects these rows is unavailable.')}</p>` +
      '<h3>Clear refunds</h3>' +
      '<p>Money the filer gave back, matched to the gift it reverses.</p>' +
      '<h4>Negative adjustments without a matched original</h4>' +
      '<p>A negative amount alone does not establish returned money. These rows still need their counterpart before anyone calls them refunds.</p>' +
      '</div>';
  }

  function card(m, state) {
    const view = pageOf(m, state), info = pager(m, state, view), kind = state.tab === 'refunds' ? 'linked' : state.tab;
    return `<section class="card xref-table-card${state.enter ? ' xref-enter' : ''}" id="xref-panel" role="tabpanel" aria-labelledby="xref-tab-${state.tab}">` +
      `<h2 class="sr-only">${NAMES[kind]}</h2>` +
      `<div class="xref-scroll" id="xref-scroll" role="region" aria-labelledby="xref-tab-${state.tab}" tabindex="0">${body(m, state, view)}</div>` +
      '<div class="xref-foot"><div class="xref-pager">' +
      `<button type="button" class="xref-page" id="xref-previous" data-xref-page="-1" aria-label="Previous rows"${info.previous ? '' : ' disabled'}>${ICONS.previous}</button>` +
      `<span class="xref-rows" id="xref-rows">${info.text}</span>` +
      `<button type="button" class="xref-page" id="xref-next" data-xref-page="1" aria-label="Next rows"${info.next ? '' : ' disabled'}>${ICONS.next}</button></div>` +
      `<p class="xref-capped" id="xref-capped"${info.note ? '' : ' hidden'}>${escape(info.note)}</p>` +
      `<button type="button" class="xref-disclose xref-method-toggle" id="xref-method-toggle" aria-expanded="${state.method}" aria-controls="xref-method">How this list is built${ICONS.down}</button></div>` +
      method(m, state) + '</section>';
  }

  const message = (heading, lines, extra = '') => `<section class="card xref-message${extra}"><h2>${heading}</h2>${lines.join('')}</section>`;
  // The one reading of "does this run have a digest to list", for render and repaint alike.
  const digestOf = data => data && data.report && data.report.digest_available && data.report.digest ? data.report.digest : null;
  function html(data, requested) {
    if (!data || data.status === 'empty') return message('Contradictions, large receipts and clear refunds',
      [`<p>${escape(LEDE)}</p>`, '<p class="xref-muted">No run has been saved yet. Viewing this page does not start collection.</p>']);
    if (data.status === 'error' || !data.available) return `<section class="card xref-message xref-warning" role="alert"><h2>Saved evidence unavailable</h2><p>${escape(data.message || 'The latest run could not be read.')}</p><p>No totals are shown until the saved run and its evidence agree.</p></section>`;
    const report = data.report, progress = data.progress, digest = digestOf(data);
    const m = model(digest), state = settle(m, requested);
    let content = runStrip(data, state);
    const notices = [...items(data.notices)];
    if (data.status === 'failed') notices.push(progress?.message || 'The run did not complete successfully. Review its saved evidence before relying on anything below.');
    if (notices.length) content += `<div class="xref-notice" role="status">${notices.map(value => `<p>${escape(value)}</p>`).join('')}</div>`;
    if (!report) return content + message('Evidence is being cross-referenced', [
      `<p>${escape(progress?.message || 'Contradictions, refunds and unusually large counted receipts appear after the computation finishes.')}</p>`,
      `<p class="xref-muted">Last progress update: ${escape(progress?.updated_at || 'Unknown')}. A saved status is not proof that a worker is still running.</p>`]);
    if (!digest) return content + `<section class="card xref-message"><h2 class="sr-only">Contradictions, large receipts and clear refunds</h2>` +
      `<p class="xref-empty">${escape(report.digest_message || 'This run has no contradiction/refund/large-receipt digest.')}</p><p class="xref-lede">${escape(LEDE)}</p></section>`;
    return content + tabs(m, state) + filters(m, state) + card(m, state);
  }

  const api = {html, money, initialState};
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  root.MentatCrossReference = api;

  /* ------------------------------------------------ the page controller */
  const byId = id => document.getElementById(id);
  let active = false, pending = null, lastIndustry = null, lastRun = null, lastData = null, lastKey = '';
  let state = initialState(), noteTimer = 0, noteUntil = 0;
  const still = () => !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
  // The industry the URL names, through the one module that owns that
  // vocabulary (audits/mentat_failings_20260915/contracts/url_scope.json).
  const scopeIndustry = () => (globalThis.MentatScope ? globalThis.MentatScope.read().industry : '') || '';
  // Which run the console actually read, said where the refresh status is said.
  // S4's route answers selected_by: "pointer" on the latest path, "industry"
  // when a label chose the run, so the reader is never left guessing whether a
  // permalink's industry took.
  const runText = data => {
    if (!data) return 'Latest run';
    // An industry-scoped ask that found nothing answers the empty envelope
    // ({available:false, industry:<label>}), and saying "Latest run" over it
    // would name the one run it is NOT showing. Before S5 this view always had
    // the latest run to fall back on; a reader who followed a permalink is owed
    // the reason the panel is empty.
    if (data.industry && data.available === false) return 'No cross-reference run for ' + data.industry;
    if (data.selected_by === 'industry' && data.industry) return 'Newest run for ' + data.industry;
    return 'Latest run';
  };
  // #xref-refresh-status stays the status region the page announces; its text
  // is also the Refresh button's tooltip (board B16 prints no status line).
  function say(text) {
    const status = byId('xref-refresh-status'), button = byId('xref-refresh');
    if (status && status.textContent !== text) status.textContent = text;
    if (button && button.title !== text) button.title = text;
  }
  // "Refreshing..." shows beside the button only while a refresh runs: at once
  // for a click, and for the 5-second poll only if it is slow, so the poll
  // never blinks. A failed refresh leaves a short note until one succeeds.
  function busy(manual) {
    const note = byId('xref-refresh-note');
    if (!note) return;
    root.clearTimeout(noteTimer);
    const show = () => { note.textContent = 'Refreshing…'; note.classList.remove('is-failed'); note.hidden = false; noteUntil = Date.now() + 400; };
    if (manual) show(); else noteTimer = root.setTimeout(show, 400);
  }
  function settled(failed) {
    const note = byId('xref-refresh-note');
    if (!note) return;
    root.clearTimeout(noteTimer);
    const finish = () => {
      note.classList.toggle('is-failed', failed);
      note.textContent = failed ? 'Refresh failed' : '';
      note.hidden = !failed;
    };
    const wait = failed || note.hidden ? 0 : Math.max(0, noteUntil - Date.now());
    if (wait) noteTimer = root.setTimeout(finish, wait); else finish();
  }

  const focusKey = host => {
    const element = document.activeElement;
    if (!element || !host.contains(element)) return null;
    if (element.id) return {id: element.id, start: typeof element.selectionStart === 'number' ? element.selectionStart : null, end: element.selectionEnd};
    if (element.tagName === 'A') return {href: element.getAttribute('href')};
    return null;
  };
  function refocus(host, key) {
    if (!key) return;
    const element = key.id ? byId(key.id) : [...host.querySelectorAll('a')].find(node => node.getAttribute('href') === key.href);
    if (!element || !host.contains(element)) return;
    element.focus({preventScroll:true});
    if (key.start !== null && key.start !== undefined && typeof element.setSelectionRange === 'function') {
      try { element.setSelectionRange(key.start, key.end); } catch (error) { /* not a text field */ }
    }
  }
  // Repaint from saved data. The same run keeps its tab, filters, page, open
  // disclosures, focus and scroll; a new run keeps only the tab it was on.
  function paint(data, {force = false, keepScroll = true} = {}) {
    const host = byId('xref-results');
    if (!host) return;
    const key = JSON.stringify(data);
    if (!force && key === lastKey) return;
    const sameRun = lastData !== null && lastRun === (data.run_id ?? null);
    if (!sameRun) state = initialState(state.tab);
    const focus = sameRun ? focusKey(host) : null, scroller = byId('xref-scroll');
    const scroll = sameRun && keepScroll && scroller ? scroller.scrollTop : 0;
    state = settle(model(digestOf(data)), state);
    host.innerHTML = html(data, state);
    state.enter = false;
    lastKey = key; lastRun = data.run_id ?? null; lastData = data;
    const next = byId('xref-scroll');
    if (next && scroll) next.scrollTop = scroll;
    refocus(host, focus);
  }
  // A filter, search or page change repaints the rows, the pager and the note,
  // and leaves the controls themselves (and the caret in the search box) alone.
  function rows() {
    if (!lastData) return;
    const m = model(digestOf(lastData));
    state = settle(m, state);
    const view = pageOf(m, state), info = pager(m, state, view);
    state.page[state.tab] = view.page;
    const scroller = byId('xref-scroll');
    if (scroller) { scroller.innerHTML = body(m, state, view); scroller.scrollTop = 0; }
    const text = byId('xref-rows');
    if (text) text.textContent = info.text;
    const note = byId('xref-capped');
    if (note) { note.textContent = info.note; note.hidden = !info.note; }
    for (const [id, enabled, other] of [['xref-previous', info.previous, 'xref-next'], ['xref-next', info.next, 'xref-previous']]) {
      const button = byId(id);
      if (!button) continue;
      const focused = document.activeElement === button;
      button.disabled = !enabled;
      if (focused && !enabled) byId(other)?.focus({preventScroll:true});
    }
  }
  function choose(tab) {
    if (!lastData || !TAB_IDS.includes(tab)) return;
    if (state.tab !== tab) {
      state.tab = tab; state.enter = !still();
      paint(lastData, {force:true, keepScroll:false});
    }
    byId('xref-tab-' + tab)?.focus({preventScroll:true});
  }
  function disclose(button, key, panelId) {
    state[key] = !state[key];
    button.setAttribute('aria-expanded', String(state[key]));
    const panel = byId(panelId);
    if (!panel) return;
    panel.hidden = !state[key];
    if (state[key] && key === 'method') panel.scrollIntoView?.({block:'nearest', behavior: still() ? 'auto' : 'smooth'});
  }

  async function refresh(options) {
    if (!active || document.hidden) return;
    const manual = !!(options && options.manual === true);
    const industry = scopeIndustry();
    if (pending && industry === lastIndustry) { if (manual) busy(true); return; }
    pending?.abort();
    const request = new AbortController();
    pending = request;
    if (industry !== lastIndustry) {
      byId('xref-results')?.replaceChildren();
      lastKey = ''; lastData = null; lastRun = null;
    }
    lastIndustry = industry;
    if (!lastData || manual) say('Loading saved evidence for ' + (industry || 'the latest run') + '…');
    busy(manual);
    const current = () => pending === request && active && scopeIndustry() === industry;
    let failed = false;
    try {
      // industry is the only query key this route accepts (server.py:1918-1928);
      // without one it still answers for the latest saved run, as before.
      const response = await fetch('/api/cross-reference' +
        (industry ? '?industry=' + encodeURIComponent(industry) : ''), {cache:'no-store', signal:request.signal});
      if (!response.ok) throw new Error('The cross-reference view could not refresh.');
      const data = await response.json();
      if (!current()) return;
      paint(data);
      say(runText(data) + ' · Saved evidence refreshed. This page is read-only.');
    } catch (error) {
      if (!current() || error.name === 'AbortError') return;
      failed = true;
      say(error.message + (lastData
        ? ' Any previously displayed result is an older snapshot.'
        : ' No saved evidence is displayed for this scope. Use Refresh to retry.'));
    } finally { if (pending === request) { pending = null; settled(failed); } }
  }
  api.setActive = value => {
    active = !!value;
    if (active) refresh();
    else { pending?.abort(); pending = null; settled(false); }
  };
  api.refresh = refresh;

  const host = byId('xref-results');
  host?.addEventListener('click', event => {
    const button = event.target.closest && event.target.closest('button');
    if (!button || !host.contains(button) || button.disabled) return;
    if (button.dataset.xrefTab) return choose(button.dataset.xrefTab);
    if (button.dataset.xrefPage) {
      state.page[state.tab] = Math.max(0, (state.page[state.tab] || 0) + Number(button.dataset.xrefPage));
      return rows();
    }
    if (button.id === 'xref-method-toggle') return disclose(button, 'method', 'xref-method');
    if (button.id === 'xref-companies-toggle') return disclose(button, 'companies', 'xref-companies');
  });
  host?.addEventListener('change', event => {
    const key = event.target.dataset && event.target.dataset.xrefFilter;
    if (!key || key === 'search') return;
    state.filters[state.tab][key] = event.target.value;
    state.page[state.tab] = 0;
    rows();
  });
  host?.addEventListener('input', event => {
    if (!event.target.dataset || event.target.dataset.xrefFilter !== 'search') return;
    state.filters[state.tab].search = event.target.value;
    state.page[state.tab] = 0;
    rows();
  });
  host?.addEventListener('keydown', event => {
    const tab = event.target.closest && event.target.closest('[data-xref-tab]');
    if (!tab) return;
    const index = TAB_IDS.indexOf(tab.dataset.xrefTab);
    const next = {ArrowRight:(index + 1) % TAB_IDS.length, ArrowLeft:(index + TAB_IDS.length - 1) % TAB_IDS.length, Home:0, End:TAB_IDS.length - 1}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    choose(TAB_IDS[next]);
  });
  // A scope written elsewhere in this document, or a Back button, can change the
  // industry. Only a change matters, and only while this view is the one on
  // screen. Refresh replaces the pending request and clears the old scope
  // before loading the new one, so a late reply cannot relabel old evidence.
  globalThis.MentatScope?.subscribe(next => {
    if (!active) return;
    const industry = (next && next.industry) || '';
    if (industry === lastIndustry) return;
    refresh();
  });
  byId('xref-refresh')?.addEventListener('click', () => refresh({manual:true}));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  api.setActive(!byId('view-cross-reference')?.hidden);
  root.setInterval(refresh, 5000);
})(typeof window === 'undefined' ? globalThis : window);
