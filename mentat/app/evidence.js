/* PAC evidence: a picker rail, up to three PAC panes side by side from any
   imported industry file or saved FEC snapshot, and a comparison strip
   (build 06, 2026-09-22: board B19's page with approved board 3's pane).

   This module owns the whole tab. It replaces evidence-csv.js (the import
   mechanics moved into the Import drawer here) and the snapshot half that
   used to live in app.js: one heading, one status region, one picker.

   Pane state is `{source: 'csv'|'snapshot', id, committee, cycle}` per pane,
   round-tripped through the URL under the prefixed keys a.* - d.* (scope.js;
   D-039). The single-pane keys the HANDOFF reserved for S6 (`evidence`, `id`,
   `committee`, `offset`, `limit`) are superseded and never written.

   Figures come from two routes and nothing is recomputed from rows the page
   never asked for: GET /api/evidence-csv?...&summary=1 for an import's
   committee, GET /api/evidence?id= for a snapshot. Rows stay on the paginated
   SELECT, inside the pane that asked for them. */
(() => {
  const PANE_KEYS = ['a', 'b', 'c', 'd'];
  // Build 06 (prompt 06): three panes at most, ticked from the rail. The URL
  // keeps all four prefixes, so a d.* key left by an older permalink is still
  // cleared by writeScope() rather than left behind.
  const MAX_PANES = 3;
  const PAGE_SIZE = 100;
  const POLL_DELAY = 500;
  const POLL_FAILURE_LIMIT = 5;
  const NOT_REPORTED = 'Not reported';
  // F4, build 06. Boards 3 and B19 show three in the pane; the summary answers
  // ten and the rest are spelled out in the About popover, so the depth is
  // reachable, not gone.
  const TOP_IN_PANE = 3;

  /* ------------------------------------------------------------- formatting */
  const channelName = value => String(value || 'Unclassified').replaceAll('_', ' ');
  const money = value => value == null ? '—'
    : (value < 0 ? '-' : '') + '$' + Math.round(Math.abs(value)).toLocaleString('en-US');
  const megabytes = value => (Number(value || 0) / 1e6).toFixed(1);
  const cycleLabel = value => value ? `${Number(value) - 1}–${value}` : 'All cycles';
  // F1. A catalog name is whatever the filing carries: CULAC's is
  // "AMERICA'S CREDIT UNIONS PAC OF CREDIT UNION NATIONAL ASSOCIATION, INC. /
  // CULAC THE PAC OF CREDIT UNION NATIONAL ASSOCIATION", five lines in a pane
  // title and a chip row of its own. Everything the reader SCANS - chip, pane
  // title, compare heading, PAC option - gets the text before the first " / ",
  // capped at 34 characters; the full name rides on a title attribute and is
  // spelled out in the About popover, so nothing is hidden, only folded.
  const NAME_LIMIT = 34;
  const shortName = value => {
    const text = String(value == null ? '' : value).trim();
    const head = (text.split(' / ')[0] || text).trim() || text;
    return head.length > NAME_LIMIT ? head.slice(0, NAME_LIMIT - 1).trimEnd() + '\u2026' : head;
  };
  const suffixOf = name => { const at = String(name || '').lastIndexOf('.'); return at === -1 ? '' : String(name).slice(at).toLowerCase(); };
  const directoryOf = value => {
    const trimmed = String(value || '').trim();
    if (!trimmed) return '';
    const stripped = trimmed.replace(/[\\/]+$/, '');
    if (!stripped) return trimmed;
    const at = Math.max(stripped.lastIndexOf('\\'), stripped.lastIndexOf('/'));
    return at === -1 ? '' : trimmed.slice(0, at + 1);
  };
  // Only FEC-hosted filing links are turned into anchors; anything else is
  // shown as text, so a snapshot can never plant a link in the page.
  const sourceAnchor = (url, text) => /^https:\/\/(docquery\.fec\.gov|www\.fec\.gov)\//.test(url || '')
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)} ↗</a>`
    : escapeHtml(text);
  const filingRecords = row => {
    if (Array.isArray(row?.filings)) return row.filings;
    if (Array.isArray(row?.source_urls) && row.source_urls.length) {
      return row.source_urls.map(source_url => ({
        source_url,
        date: row.first_date || row.last_date || '',
        activity_type: 'Filed record'
      }));
    }
    const raw = row?.raw || {};
    const value = raw.filing_records || raw['filing records'] || '';
    if (!value) return [];
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
      return [];
    }
  };
  const filingLedger = (row, fallbackUrl = '') => {
    const report = reportLink(row);
    if (report) return `<span class="record-links">${reportAnchor(report, ' ↗')}</span>`;
    const records = filingRecords(row);
    if (!records.length) {
      const raw = row?.raw || {};
      const image = String(raw.image_number || raw['image number'] || '');
      const link = fallbackUrl || row?.source_url || row?.source_urls?.[0] || raw.pdf_url ||
        (/^\d+$/.test(image) ? 'https://docquery.fec.gov/cgi-bin/fecimg/?' + image : '');
      return link ? `<span class="record-links">${sourceAnchor(link, 'View record')}</span>`
        : '<span class="muted">FEC filing link unavailable</span>';
    }
    return `<span class="record-links">${records.map((record, index) =>
      `<span title="${escapeHtml([record.date, record.amount == null ? '' : currency(record.amount), record.purpose].filter(Boolean).join(' · '))}">${record.source_url
        ? sourceAnchor(record.source_url, records.length === 1 ? 'View record' : `View record ${index + 1}`)
        : '<span class="muted">FEC filing link unavailable</span>'}</span>`).join('')}</span>`;
  };
  /* Board 7's donor rows are one line (fix r2, TC-01): a row's filing link, or
     the words saying there is none, rides after the amount as the compact
     "FEC 1" citation the spend lists use (`.fec-citations`), not as a block
     under it. The links and wording are filingLedger's; a record without a
     link keeps its number, and "FEC filing link unavailable" stays on the
     marker's title and in screen-reader text. */
  const FEC_RECORD_LINK = /^https:\/\/(docquery\.fec\.gov|www\.fec\.gov)\//;
  /* F3, 2026-09-24 (Paul: "FEC: Whatever is most efficient", one link per
     row). ActBlue and WinRed filings have no FEC page image - all 14 tried
     answered "Error#1896: Image is not processed yet" - so the server
     (fec_citation.py, the one place these URLs are built) links such a row
     to its whole electronic report and marks the link kind "report". The
     page only draws that link, and only when it is a docquery report URL.
     A row without `fec_link` (an older server, or a sub_id that resolved
     nowhere) renders exactly as before. filingLedger uses these too; it is
     only called once the page renders, after this line has run. */
  const FEC_REPORT_URL = /^https:\/\/docquery\.fec\.gov\/cgi-bin\/forms\/C\d{8}\/[1-9]\d{0,11}\/$/;
  const reportLink = row => {
    const link = row?.fec_link;
    if (!link || link.kind !== 'report' || !FEC_REPORT_URL.test(String(link.url || ''))) return null;
    return {url: String(link.url), text: String(link.text || 'FEC report'),
      title: String(link.title || 'FEC has no page image for this filing')};
  };
  const reportAnchor = (report, suffix = '') => `<a href="${escapeHtml(report.url)}" target="_blank" `
    + `rel="noopener noreferrer" title="${escapeHtml(report.title)}">${escapeHtml(report.text)}${suffix}</a>`;
  const filingCite = (row, fallbackUrl = '') => {
    const report = reportLink(row);
    if (report) return `<span class="fec-citations fec-citations-report">${reportAnchor(report)}</span>`;
    const raw = row?.raw || {};
    const image = String(raw.image_number || raw['image number'] || '');
    const link = fallbackUrl || row?.source_url || row?.source_urls?.[0] || raw.pdf_url ||
      (/^\d+$/.test(image) ? 'https://docquery.fec.gov/cgi-bin/fecimg/?' + image : '');
    const records = filingRecords(row);
    const items = records.length ? records : link ? [{source_url: link}] : [];
    const unavailable = 'FEC filing link unavailable';
    if (!items.some(record => FEC_RECORD_LINK.test(String(record.source_url || '')))) {
      return `<span class="fec-citations fec-citations-none" title="${unavailable}"><span aria-hidden="true">FEC n/a</span>`
        + `<span class="sr-only">${unavailable}</span></span>`;
    }
    const marks = items.map((record, index) => {
      const url = String(record.source_url || '');
      const detail = [record.date, record.amount == null ? '' : currency(record.amount), record.purpose].filter(Boolean).join(' · ');
      const label = items.length === 1 ? 'View FEC record' : `View FEC record ${index + 1} of ${items.length}`;
      return FEC_RECORD_LINK.test(url)
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(label)}" `
          + `title="${escapeHtml(detail || label)}">${index + 1}</a>`
        : `<span title="${unavailable}" aria-label="Record ${index + 1}: ${unavailable}">${index + 1}</span>`;
    });
    return `<span class="fec-citations">FEC<sup>${marks.join(',')}</sup></span>`;
  };
  const filingCitations = row => {
    const records = filingRecords(row);
    const raw = row?.raw || {};
    const image = String(raw.image_number || raw['image number'] || '');
    const fallback = row?.source_url || raw.pdf_url ||
      (/^\d+$/.test(image) ? 'https://docquery.fec.gov/cgi-bin/fecimg/?' + image : '');
    const sources = records.length ? records : fallback ? [{source_url:fallback}] : [];
    const groups = new Map();
    // S3, 2026-09-24: a spend row of a workbook that carries no filing links
    // says so and points at its committee on FEC (workbookPointer, below).
    // Without the server's pointer the row reads exactly as before.
    const unavailable = workbookPointer(row?.filing_fallback)
      || '<span class="muted">FEC filing link unavailable</span>';
    for (const record of sources) {
      const url = String(record.source_url || '');
      // A bulk-download URL or a missing image does not cite this aggregate.
      if (!/^https:\/\/docquery\.fec\.gov\/cgi-bin\/fecimg\/\?\d+$/.test(url))
        return unavailable;
      if (!groups.has(url)) groups.set(url, []);
      groups.get(url).push(record);
    }
    if (!groups.size) return unavailable;
    const links = [...groups].map(([url, items], index) => {
      const detail = items.map(item => [item.date, item.amount == null ? '' : currency(item.amount),
        item.purpose].filter(Boolean).join(' · ')).filter(Boolean).join('; ');
      const label = `Open FEC filing image ${index + 1} of ${groups.size}` +
        (items.length > 1 ? `, ${items.length} transactions` : '');
      return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" ` +
        `aria-label="${escapeHtml(label)}" title="${escapeHtml(detail || label)}">${index + 1}</a>`;
    });
    return `<span class="fec-citations">FEC<sup>${links.join(',')}</sup></span>`;
  };
  /* The pointer is the server's (fec_citation.workbook_pointer, sent only for
     an imported workbook's spend map when that workbook carries no filing
     links at all). It is drawn only for an FEC committee page URL. */
  const FEC_COMMITTEE_PAGE = /^https:\/\/www\.fec\.gov\/data\/committee\/C\d{8}\/$/;
  const workbookPointer = pointer => pointer && FEC_COMMITTEE_PAGE.test(String(pointer.url || ''))
    ? `<span class="muted fec-workbook-note">${escapeHtml(pointer.note || 'Filing links are not in this workbook')}</span> `
      + `<a href="${escapeHtml(pointer.url)}" target="_blank" rel="noopener noreferrer" `
      + `title="${escapeHtml(pointer.title || pointer.note || '')}">${escapeHtml(pointer.text || 'Committee on FEC')} ↗</a>`
    : '';
  const render = (element, html) => { if (element && element.innerHTML !== html) element.innerHTML = html; };
  const say = text => {
    const node = $('evidence-status');
    node.textContent = text; node.title = ''; node.classList.remove('is-scope');
  };
  // The bar in a figure or compare cell: normalised per row, a full bar for the
  // largest value and a visible sliver for anything positive, so $178M beside
  // $37K reads as the difference it is rather than as two full bars.
  //
  // The width rides on data-bar-width and is applied through the CSSOM after
  // the markup lands, because the console serves
  // `Content-Security-Policy: ... style-src 'self'` (server.py:1944): a
  // style="width:..%" attribute written into innerHTML is BLOCKED, and every
  // bar in the page silently measured zero. graph.js:163 sets its bars the same
  // way. Measured, not assumed - a real-client pass read style.width as ''.
  const barWidth = (value, peak) => !value || value <= 0 ? 0 : Math.max(1, Math.round(value / peak * 100));
  const bar = (value, peak, thin) =>
    `<div class="bartrack"><div class="bar${thin ? ' thin' : ''}" data-bar-width="${barWidth(value, peak)}"></div></div>`;
  function applyBars(root) {
    if (!root) return;
    for (const node of root.querySelectorAll('[data-bar-width]')) node.style.width = node.dataset.barWidth + '%';
  }

  /* ------------------------------------------------------------------ state */
  let imports = [], snapshots = [], limits = null;
  const snapshotBodies = new Map();       // snapshot id -> /api/evidence?id= body
  const panes = new Map();                // 'a'..'d' -> pane state
  let revision = 0, importRevision = 0;
  let pendingUploadFile = null, pendingPathRequest = null;
  let rereadTimer = null, rereadDeadline = 0;
  let graphContext = null;
  let startup = null, restored = false;
  let collectorAvailable = false, collectorReadOnly = false;
  let evidenceJob = '', evidenceJobTimer = 0;
  let directTicket = 0, directTimer = 0;

  const scope = () => (typeof MentatScope === 'undefined' ? null : MentatScope);
  const sourceValue = () => $('evidence-source').value;
  const parseSourceValue = value => {
    const at = String(value || '').indexOf(':');
    return at === -1 ? {kind: '', id: ''} : {kind: value.slice(0, at), id: value.slice(at + 1)};
  };
  const importById = id => imports.find(item => item.id === id) || null;
  const snapshotById = id => snapshots.find(item => item.id === id) || null;

  /* ---------------------------------------------------- the committee lists */
  // A file can hold 523 PACs (the Crypto workbook does), so the picker is a
  // committee-type filter and a search over one rebuilt select. A committee the
  // file never categorised reads as Unclassified, the same word the FEC
  // committee master falls back to, so one option covers both.
  const categoryOf = item => item.category || 'Unclassified';
  // The hover note a Conduit committee (ActBlue, WinRed) carries; theme.js owns the words.
  const conduitNote = category => (globalThis.MentatTheme && globalThis.MentatTheme.categoryNote ? globalThis.MentatTheme.categoryNote(category) : '');
  const committeeOrder = item => item.id === 'unassigned' ? '￿' : String(item.name || item.id).toLowerCase();

  function chosenCommittees() {
    const chosen = parseSourceValue(sourceValue());
    if (chosen.kind === 'csv') {
      const source = importById(chosen.id);
      return source ? (source.committees || []) : [];
    }
    if (chosen.kind === 'snapshot') {
      const body = snapshotBodies.get(chosen.id);
      if (!body) return [];
      const seen = new Map();
      for (const row of body.committee_finances || []) {
        const current = seen.get(row.committee_id);
        const rows = (current ? current.rows : 0) + 1;
        // The rail ranks a snapshot's PACs by receipts as reported, per cycle. A
        // cycle whose figure is blank stays unknown (never $0), and an unknown
        // cycle makes the all-cycles figure unknown too.
        const receipts = Object.assign({}, current ? current.receipts_by_cycle : {});
        const reported = row.receipts_as_reported;
        const known = reported != null && reported !== '' && Number.isFinite(Number(reported));
        if (known) receipts[String(row.cycle)] = (receipts[String(row.cycle)] || 0) + Number(reported);
        seen.set(row.committee_id, {id: row.committee_id, name: row.committee_name || row.committee_id,
          category: row.category || '', category_source: row.category_source || '',
          rows: rows, supported_rows: rows, excluded_rows: 0,
          receipts_by_cycle: receipts, receipts_complete: (current ? current.receipts_complete : true) && known});
      }
      return [...seen.values()];
    }
    return [];
  }

  function chosenCategories() {
    const chosen = parseSourceValue(sourceValue());
    if (chosen.kind === 'csv') {
      const source = importById(chosen.id);
      return source ? (source.categories || []) : [];
    }
    const buckets = new Map();
    for (const item of chosenCommittees()) {
      if (!item.category) continue;
      const bucket = buckets.get(item.category) || {category: item.category, committees: 0};
      bucket.committees += 1;
      buckets.set(item.category, bucket);
    }
    return [...buckets.values()];
  }

  function chosenCycles() {
    const chosen = parseSourceValue(sourceValue());
    if (chosen.kind === 'csv') {
      const source = importById(chosen.id);
      return source ? (source.cycles || []).map(String) : [];
    }
    const body = snapshotBodies.get(chosen.id);
    if (!body) return [];
    return [...new Set((body.committee_finances || []).map(row => String(row.cycle)))].sort().reverse();
  }

  function visibleCommittees() {
    const type = $('evidence-type').value;
    const text = $('evidence-find').value.trim().toLowerCase();
    return chosenCommittees()
      .filter(item => !type || categoryOf(item) === type)
      .filter(item => !text || String(item.name || '').toLowerCase().includes(text)
                            || String(item.id || '').toLowerCase().includes(text))
      .sort((a, b) => committeeOrder(a).localeCompare(committeeOrder(b)));
  }

  function fillTypes() {
    const select = $('evidence-type'), previous = select.value;
    const items = chosenCategories();
    select.replaceChildren(new Option('All types', ''),
      ...items.map(item => new Option(`${item.category} · ${number(item.committees)}`, item.category)));
    select.value = items.some(item => item.category === previous) ? previous : '';
    select.disabled = !items.length;
    $('evidence-find').disabled = !chosenCommittees().length;
  }

  function fillCycles() {
    const select = $('evidence-cycle'), previous = select.value;
    const cycles = chosenCycles();
    select.replaceChildren(new Option('All cycles', ''), ...cycles.map(value => new Option(cycleLabel(value), value)));
    if (cycles.includes(previous)) select.value = previous;
    select.disabled = !cycles.length;
  }

  function fillPacs() {
    const select = $('evidence-pac'), previous = select.value;
    const visible = visibleCommittees();
    select.replaceChildren(...visible.map(item => new Option(
      `${shortName(item.name || item.id)} · ${item.id} · ${categoryOf(item)}`
      + (item.rows == null ? '' : ` · ${number(item.rows)} row${Number(item.rows) === 1 ? '' : 's'}`), item.id)));
    select.disabled = !visible.length;
    if (visible.some(item => item.id === previous)) select.value = previous;
    else if (visible.length) select.value = visible[0].id;
  }

  /* --------------------------------------------------------------- the rail */
  // Build 06: the rail lists the chosen source's PACs, filtered by Type and Find
  // exactly as #evidence-pac is (visibleCommittees), each with a tick that opens
  // or closes its pane. #evidence-pac stays in the page, hidden, as the data
  // behind the rail; openCommittee is the programmatic open path.
  //
  // The figure at the right is receipts: a saved snapshot's receipts as
  // reported, or an import's `receipts` / `receipts_by_cycle` from the listing
  // (fix round 1, R06-02: evidence_csv.Store.committee_figures, the lead
  // channel each pane opens on). A PAC with no figure in the chosen scope
  // reads "—" and sorts last; only a source with no figure at all (an import
  // listed by an older console) falls back to rows in the file.
  let railSignature = '';
  const receiptsOf = (item, cycle) => {
    if (cycle) {
      const value = item.receipts_by_cycle ? item.receipts_by_cycle[cycle] : undefined;
      return Number.isFinite(value) ? value : null;
    }
    if (Number.isFinite(item.receipts)) return item.receipts;
    if (item.receipts_by_cycle && item.receipts_complete !== false) {
      const values = Object.values(item.receipts_by_cycle).filter(Number.isFinite);
      return values.length ? values.reduce((total, value) => total + value, 0) : null;
    }
    return null;
  };
  function railRows() {
    const cycle = $('evidence-cycle').value;
    const rows = visibleCommittees().map(item => ({item, amount: receiptsOf(item, cycle)}));
    const byReceipts = rows.some(row => row.amount != null);
    const ranked = row => (row.amount == null ? -Infinity : row.amount);
    rows.sort((first, second) => (byReceipts ? ranked(second) - ranked(first) || 0
      : Number(second.item.rows || 0) - Number(first.item.rows || 0))
      || committeeOrder(first.item).localeCompare(committeeOrder(second.item)));
    return rows.map(row => ({...row, figure: byReceipts ? (row.amount == null ? '—' : money(row.amount))
      : `${number(row.item.rows || 0)} row${Number(row.item.rows) === 1 ? '' : 's'}`}));
  }
  // Fix round 1 (R06-03): a tick is the PAC, not the PAC on the Cycle chip's
  // cycle. A pane stays ticked whatever the chip says (its own cycle is on the
  // row's title when the two differ), and the chip sets only the cycle a NEW
  // tick opens on.
  const panesFor = (kind, id, committee) => [...panes.values()].filter(pane => pane.source === kind
    && pane.id === id && pane.committee === committee);
  function renderRail() {
    const host = $('evidence-list');
    const chosen = parseSourceValue(sourceValue());
    const cycle = $('evidence-cycle').value;
    let html;
    if (!chosen.kind) {
      html = `<p class="evidence-list-empty">${imports.length || snapshots.length
        ? 'Choose a source above to list its PACs.'
        : 'Choose a sector or search above for a PAC flow. Import a file or collect a snapshot for detailed panes.'}</p>`;
    } else if (chosen.kind === 'snapshot' && !snapshotBodies.has(chosen.id)) {
      // While a saved snapshot is read, the rail lists nothing: the previous
      // source's rows left under the new Source chip opened a pane for a
      // committee the snapshot does not hold (fix round 1).
      html = '<p class="evidence-list-empty">Reading the saved snapshot…</p>';
    } else {
      const rows = railRows();
      html = rows.length ? rows.map(({item, figure}) => {
        const open = panesFor(chosen.kind, chosen.id, item.id);
        const elsewhere = open.filter(pane => String(pane.cycle || '') !== String(cycle || ''))
          .map(pane => cycleLabel(pane.cycle));
        const title = `${item.name || item.id} · ${item.id} · ${categoryOf(item)}${conduitNote(item.category) ? ` (${conduitNote(item.category)})` : ''}`
          + (elsewhere.length ? ` · open on ${elsewhere.join(' and ')}` : '');
        return `<label class="evidence-rail-row${open.length ? ' on' : ''}" data-committee="${escapeHtml(item.id)}" title="${escapeHtml(title)}">`
          + `<input type="checkbox" value="${escapeHtml(item.id)}"${open.length ? ' checked' : ''}>`
          + `<span class="evidence-rail-name">${escapeHtml(shortName(item.name || item.id))}</span>`
          + `<span class="evidence-rail-figure">${escapeHtml(figure)}</span></label>`;
      }).join('') : '<p class="evidence-list-empty">No PAC in this source matches.</p>';
    }
    if (html === railSignature) return;
    const active = document.activeElement;
    const focused = host.contains(active) && active.matches('input[type=checkbox]') ? active.value : null;
    railSignature = html;
    host.innerHTML = html;
    if (focused !== null) {
      const again = [...host.querySelectorAll('input[type=checkbox]')].find(box => box.value === focused);
      if (again) again.focus({preventScroll: true});
    }
  }
  function railNote(text) {
    const note = $('evidence-rail-note');
    note.textContent = text || '';
    note.hidden = !text;
  }

  function scopeStatus() {
    const chosen = parseSourceValue(sourceValue());
    if (!chosen.kind) return imports.length || snapshots.length
      ? `${number(imports.length)} imported file${imports.length === 1 ? '' : 's'} and ${number(snapshots.length)} saved snapshot${snapshots.length === 1 ? '' : 's'} on this computer · choose a source`
      : 'Explore a PAC flow above, or import a file for detailed panes.';
    const all = chosenCommittees(), visible = visibleCommittees();
    const type = $('evidence-type').value, text = $('evidence-find').value.trim();
    const source = chosen.kind === 'csv' ? importById(chosen.id) : snapshotById(chosen.id);
    const name = source ? (source.name || source.company || chosen.id) : chosen.id;
    // Fix round 1 (R06-16): the counts lead, so the one line the rail foot
    // shows is the counts; the source name and the rest are on its title.
    return `${number(all.length)} PACs in file · ${number(visible.length)} shown`
      + (type ? ` (${type})` : '') + (text ? ` matching "${text}"` : '')
      // The pane ceiling is part of the status, not a replacement for it: the
      // region also carries import progress, and with four panes open it used
      // to swallow every "Reading x.xlsx" line the reader was waiting on.
      + (panes.size >= MAX_PANES ? ` · ${MAX_PANES} panes open · close one to open another` : '')
      + (chosen.kind === 'csv' && source ? ` · ${number(source.row_count)} rows in file` : '')
      + ` · ${name}`;
  }
  function sayScope() {
    const node = $('evidence-status'), text = scopeStatus();
    node.textContent = text; node.title = text; node.classList.add('is-scope');
  }

  function applyFilter() {
    fillPacs();
    renderRail();
    sayScope();
  }

  /* ------------------------------------------------------------ the sources */
  function sourceLabel(item) {
    return `${item.industry || item.name} · ${number(item.row_count)} rows`
      + (item.reread ? ' · re-reading under the current rules' : '');
  }

  // An <option> cannot carry a title of its own and the Source select is one of
  // six columns, so a long file or snapshot name is clipped by the control. The
  // chosen option's full text rides on the select's own title instead.
  function syncSourceTitle() {
    const select = $('evidence-source');
    const chosen = select.selectedOptions[0];
    select.title = chosen ? chosen.textContent : '';
  }

  function fillSourceSelect(keep) {
    const select = $('evidence-source');
    const options = [new Option('Choose a file or a snapshot', '')];
    options[0].label = 'Source';
    // Build 06: the Source chip is 80px wide, so each option's label (what a
    // select shows) is the industry or company alone; the text keeps the rows
    // and cycles, and rides on the chip's title (syncSourceTitle).
    for (const item of imports) {
      const option = new Option(sourceLabel(item), 'csv:' + item.id);
      option.disabled = !!item.reread;
      if (!item.reread) option.label = item.industry || item.name || item.id;
      options.push(option);
    }
    for (const item of snapshots) {
      const option = new Option(`Snapshot · ${item.company || item.id} · ${(item.cycles || []).join(', ') || 'no cycles'}`,
        'snapshot:' + item.id);
      option.label = item.company || item.id;
      options.push(option);
    }
    select.replaceChildren(...options);
    select.value = [...select.options].some(option => option.value === keep && !option.disabled) ? keep : '';
    syncSourceTitle();
  }

  async function refreshSources() {
    const keep = sourceValue();
    const [listing, catalog] = await Promise.all([
      api('/api/evidence-csv').catch(error => ({error: error.message})),
      api('/api/evidence').catch(() => ({snapshots: []})),
    ]);
    if (listing.error) { say('GET /api/evidence-csv failed: ' + listing.error); }
    else { limits = listing.limits || limits; imports = listing.sources || []; }
    snapshots = (catalog.snapshots || []).slice()
      .sort((a, b) => String(b.retrieved_at || '').localeCompare(String(a.retrieved_at || '')));
    fillSourceSelect(keep);
    watchRereads();
    return imports;
  }

  // An import read under an older rule set is re-read by the console on the
  // first listing, so its entry is briefly unusable. Poll until it is back, and
  // give up after ten minutes rather than polling a console that is never going
  // to answer differently.
  function watchRereads() {
    const waiting = imports.filter(item => item.reread).map(item => item.id);
    if (!waiting.length) { rereadDeadline = 0; return; }
    if (!rereadDeadline) rereadDeadline = Date.now() + 600000;
    if (Date.now() > rereadDeadline || rereadTimer) return;
    rereadTimer = setTimeout(async () => {
      rereadTimer = null;
      await refreshSources();
      applyFilter();
      for (const pane of panes.values()) if (pane.source === 'csv' && waiting.includes(pane.id)) loadPane(pane);
    }, 2000);
  }

  async function chooseSource() {
    const chosen = parseSourceValue(sourceValue());
    $('evidence-type').value = ''; $('evidence-find').value = '';
    if (chosen.kind === 'snapshot' && !snapshotBodies.has(chosen.id)) {
      say('Reading the saved snapshot…');
      renderRail();
      try { snapshotBodies.set(chosen.id, await api('/api/evidence?id=' + encodeURIComponent(chosen.id))); }
      catch (error) { say(`GET /api/evidence?id=${chosen.id}: ${error.message}`); return; }
    }
    fillTypes(); fillCycles(); applyFilter();
  }

  /* -------------------------------------------------------------- the panes */
  function freeKey() { return panes.size >= MAX_PANES ? '' : (PANE_KEYS.find(key => !panes.has(key)) || ''); }

  function paneFrom(key, source, id, committee, cycle) {
    return {key, source, id, committee, cycle: cycle || '', follows: false,
      offset: 0, rowsOpen: false, rows: null, rowsLoading: false, rowsError: '', rowsTicket: 0,
      loaded: false, message: '',
      name: committee, industry: '', sourceKind: '', category: '', categorySource: '',
      totals: [], totalsByCycle: {}, counterparties: [], cycles: [],
      rowCount: null, supported: null, excluded: null, unsupported: null,
      provenance: '', warnings: [], legs: [], detail: '',
      // P4b. `contributors` is the distinct-counterparty count, which the CSV
      // summary does not carry (it ranks the top ten); `thumb` is the small
      // national choropleth in the card, drawn from the map route's by_state.
      contributors: null, legCount: null, thumb: null, thumbDirection: 'raised',
      thumbLoading: false, focus: null};
  }

  function loadPane(pane) {
    if (pane.loading) return pane.loading;
    pane.loading = readPane(pane).finally(() => { pane.loading = null; });
    return pane.loading;
  }

  async function readPane(pane) {
    const ticket = ++revision;
    pane.loaded = false; pane.message = 'Reading…';
    renderPanes();
    try {
      if (pane.source === 'csv') await loadCsvPane(pane);
      else await loadSnapshotPane(pane);
      pane.message = '';
      pane.loaded = true;
      // AFTER the figures: the card is readable without the thumbnail, so the
      // map payload is never on the pane's critical path.
      loadThumb(pane);
    } catch (error) {
      pane.message = error.message;
    }
    if (ticket > revision) return;
    renderPanes(); renderRail(); renderCompare(); sayScope();
  }

  async function loadCsvPane(pane) {
    const query = new URLSearchParams({id: pane.id, committee: pane.committee, cycle: pane.cycle, summary: '1'});
    const body = await api('/api/evidence-csv?' + query);
    pane.name = body.committee.name || body.committee.id;
    pane.category = body.committee.category || 'Unclassified';
    pane.categorySource = body.committee.category_source || 'Not recorded';
    pane.rowCount = body.committee.rows;
    pane.supported = body.committee.supported_rows;
    pane.excluded = body.committee.excluded_rows;
    pane.unsupported = body.counts.unsupported;
    pane.totals = body.totals || [];
    pane.totalsByCycle = body.totals_by_cycle || {};
    pane.counterparties = body.counterparties || [];
    pane.cycles = (body.cycles || []).map(String);
    pane.industry = body.source.industry || '';
    pane.sourceKind = body.source.industry ? 'Spending workbook' : 'Imported file';
    const source = importById(pane.id);
    pane.provenance = `${body.source.name}`
      + (source ? ` · ${number(source.row_count)} rows · sha ${String(source.sha256 || '').slice(0, 8)}` : '');
    pane.warnings = source ? (source.warnings || []) : [];
    pane.detail = source ? fileDetail(source) : '';
    pane.legs = [];
    pane.legCount = null;
    // The fourth figure. Counted where the rows are, over exactly the supported
    // rows the map places, so the pane and the map never print two different
    // numbers for the same file. A refusal costs the figure, never the pane.
    try {
      const stats = await api('/api/evidence-stats?' + new URLSearchParams({
        id: pane.id, committee: pane.committee, cycle: pane.cycle}));
      pane.contributors = Number(stats.contributors || 0);
    } catch (error) {
      pane.contributors = null;
      pane.contributorsProblem = error.message;
    }
  }

  function fileDetail(source) {
    return `${source.name} · Imported UTF-8 text SHA-256 ${source.sha256} · ${number(source.row_count)} file rows.`
      + (source.source_kind === 'path' && source.source_path ? ` Read from ${source.source_path}.` : '')
      + ' Stored on this computer under the console data root; visible to every Mentat console that uses it.'
      + ' Values are observed filings or labeled cycle aggregates, not donor allocations or reconciled giving totals.';
  }

  async function loadSnapshotPane(pane) {
    let body = snapshotBodies.get(pane.id);
    if (!body) {
      body = await api('/api/evidence?id=' + encodeURIComponent(pane.id));
      snapshotBodies.set(pane.id, body);
    }
    const finances = (body.committee_finances || []).filter(row => row.committee_id === pane.committee);
    if (!finances.length) throw new Error(`${pane.committee} is not in the snapshot ${pane.id}.`);
    const cycles = [...new Set(finances.map(row => String(row.cycle)))].sort().reverse();
    const inCycle = pane.cycle ? finances.filter(row => String(row.cycle) === pane.cycle) : finances;
    if (!inCycle.length) throw new Error(`No saved financial coverage for ${pane.committee} in ${pane.cycle}.`);
    const sum = (rows, field) => !rows.length || rows.some(row => row[field] == null || row[field] === '' || !Number.isFinite(Number(row[field])))
      ? null : rows.reduce((total, row) => total + Number(row[field]), 0);
    pane.name = finances[0].committee_name || pane.committee;
    pane.category = finances[0].category || 'Unclassified';
    pane.categorySource = finances[0].category_source || 'From the saved snapshot';
    pane.cycles = cycles;
    pane.industry = body.company || '';
    pane.sourceKind = `Saved FEC snapshot · ${String(body.retrieved_at || '').slice(0, 10) || 'undated'}`;
    pane.rowCount = null; pane.supported = null; pane.excluded = null; pane.unsupported = null;
    // P4b, honesty: a REAL zero is $0, not a dash. `.filter(item => item.amount)`
    // dropped a reported zero and the strip then showed an em dash, which reads
    // as "not reported" - the opposite of what the filing said.
    pane.totals = [
      {channel: 'Receipts as reported', amount: sum(inCycle, 'receipts_as_reported'), row_count: 0},
      {channel: 'Independent spending · reported', amount: sum(inCycle, 'regular_ie_total'), row_count: 0},
    ];
    pane.totalsByCycle = {};
    for (const cycle of cycles) {
      const rows = finances.filter(row => String(row.cycle) === cycle);
      pane.totalsByCycle[cycle] = [
        {channel: 'Receipts as reported', amount: sum(rows, 'receipts_as_reported'), row_count: 0},
      ];
    }
    const receipts = (body.receipts || []).filter(row => row.committee_id === pane.committee
      && (!pane.cycle || String(row.cycle) === pane.cycle));
    const outgoing = (body.transfers || []).filter(row => !row.candidate_id && row.source === pane.committee
      && (!pane.cycle || String(row.cycle) === pane.cycle));
    const incoming = (body.transfers || []).filter(row => row.destination === pane.committee
      && (!pane.cycle || String(row.cycle) === pane.cycle));
    const activity = (body.candidate_activity || []).filter(row => row.committee_id === pane.committee
      && (!pane.cycle || String(row.cycle) === pane.cycle));
    const support = activity.filter(row => row.direction === 'Support');
    const oppose = activity.filter(row => row.direction === 'Oppose');
    const contributions = activity.filter(row => row.direction === 'Contribution / Other');
    const unknown = activity.filter(row => !['Support','Oppose','Contribution / Other'].includes(row.direction));
    pane.counterparties = [
      {name: `${body.company || 'Company'} receipt legs`, amount: sum(receipts,'amount'), rows: receipts.length},
      {name: 'Transfers out to other committees', amount: sum(outgoing,'amount'), rows: outgoing.length},
      {name: 'Transfers in from other committees', amount: sum(incoming,'amount'), rows: incoming.length},
      {name: 'Candidate activity · support', amount: sum(support,'committee_spending'), rows: support.length},
      {name: 'Candidate activity · oppose', amount: sum(oppose,'committee_spending'), rows: oppose.length},
      {name: 'Candidate activity · contribution / other', amount: sum(contributions,'committee_spending'), rows: contributions.length},
      {name: 'Candidate activity · unknown direction', amount: sum(unknown,'committee_spending'), rows: unknown.length},
    ].filter(item => item.rows);
    pane.contributors = new Set((body.receipts || [])
      .filter(row => row.committee_id === pane.committee
        && (!pane.cycle || String(row.cycle) === pane.cycle))
      .map(row => row.reported_name || row.contributor_name || NOT_REPORTED)).size;
    pane.legCount = (pane.counterparties || []).reduce((total, item) => total + (item.rows || 0), 0);
    pane.provenance = `Snapshot ${pane.id} · openFEC · receipts as reported`;
    pane.warnings = [inCycle.some(row => row.regular_ie_reconciles == null)
      ? 'The expenditure/financial-summary comparison is unavailable for part of this selection.'
      : inCycle.every(row => row.regular_ie_reconciles)
      ? 'The collected regular-report expenditure total matches the committee financial summary.'
      : 'The collected regular-report expenditure total differs from the financial summary. Coverage remains unresolved; no donor allocation is calculated.'];
    const gaps = (body.downstream_coverage || []).filter(row => !pane.cycle || String(row.cycle) === pane.cycle);
    if (gaps.length) pane.warnings.push('Downstream collection gaps: ' + gaps.map(row => `${row.committee_id} (${row.cycle}: ${row.status})`).join(', ') + '. Missing evidence is not zero spending.');
    if ((body.temporal_routes || []).some(row => row.status === 'depth_limit' && (!pane.cycle || String(row.cycle) === pane.cycle)))
      pane.warnings.push('Some dated routes exceed the temporal search depth. Their downstream activity is unresolved by that search.');
    pane.detail = `Evidence collected ${String(body.retrieved_at || '').slice(0, 10) || 'date unavailable'}. ${body.scope || ''}`;
    pane.legs = {receipts, outgoing, incoming, activity, finances: inCycle, body};
    // The root-only table cannot answer where a recipient PAC spent onward.
    // Use the same saved-network measurement as Graph and the spending map.
    pane.network = null; pane.networkProblem = '';
    try {
      pane.network = await api('/api/evidence-network?' + new URLSearchParams({
        id:pane.id, committee:pane.committee, cycle:pane.cycle || ''}));
    } catch (error) {
      pane.networkProblem = error.message;
    }
    // F4 (Paul approved 2026-09-25): every committee in the network against its
    // own FEC summary, not only this pane's committee (networkChecks, below).
    if (pane.network && (pane.network.committees || []).length)
      pane.warnings.splice(0, 1, ...networkChecks(pane.network.committees));
  }

  /* ------------------------------------------------------------- rendering  */
  function mainFigure(pane) {
    const leading = (pane.totals || []).slice().sort((a, b) => b.amount - a.amount)[0];
    return leading || {channel: 'Receipts', amount: null, row_count: 0};
  }

  /* Four figures, not two, and never a bare dash for a real zero.
     A dash here means the figure DOES NOT EXIST for this kind of source (an
     imported file has rows, a snapshot has legs); a reported zero reads $0 or
     0, which is what the filing says. The money formatter is whole dollars
     across this console, so a real zero is "$0". */
  function paneStats(pane) {
    const cycles = (pane.cycles || []).map(String).filter(Boolean);
    const cycleNote = cycles.length ? cycles.map(cycleLabel).join(' · ')
                                    : 'no cycle is recorded on these rows';
    const covered = {label: 'Cycles covered', value: number(cycles.length), note: cycleNote};
    if (pane.source === 'snapshot') {
      const find = name => (pane.totals || []).find(item => item.channel === name);
      const receipts = find('Receipts as reported');
      const independent = find('Independent spending · reported');
      return [
        {label: 'Receipts', value: receipts ? money(receipts.amount) : '—',
         note: 'FEC financial summary for the cycle'},
        {label: 'Independent spending', value: independent ? money(independent.amount) : '—',
         note: 'reported regular-report total'},
        {label: 'Legs', value: pane.legCount == null ? '—' : number(pane.legCount),
         note: 'snapshot: legs, not file rows'},
        covered];
    }
    const leading = mainFigure(pane);
    const rowsNote = pane.rowCount == null ? 'snapshot: legs, not file rows'
      : (pane.excluded ? `${number(pane.supported)} supported · ${number(pane.excluded)} excluded by the workbook`
                       : `${number(pane.supported)} supported`);
    return [
      {label: channelName(leading.channel), value: money(leading.amount),
       note: `${number(leading.row_count)} supported rows`},
      {label: 'Contributors', value: pane.contributors == null ? '—' : number(pane.contributors),
       note: pane.contributors == null
         ? (pane.contributorsProblem || 'not counted for this source')
         : 'distinct counterparties in these rows'},
      {label: 'Rows in file', value: pane.rowCount == null ? '—' : number(pane.rowCount),
       note: rowsNote},
      covered];
  }

  /* The small national choropleth in the pane card. It is NOT a second map: it
     draws `by_state` from the map route's own payload, has no zoom, no
     tooltip and no selection, and clicking it opens the real map. */
  function thumbHtml(pane) {
    const state = mapStateFor(pane.key);
    const raised = pane.thumbDirection !== 'spent';
    return `<div class="pane-thumb" data-thumb="${pane.key}">
      <div class="pane-thumb-head"><span class="label">Where it ${raised ? 'came from' : 'went'}</span>
        <div class="seg small" role="group" aria-label="Thumbnail direction">
          <button type="button" class="pane-thumb-side${raised ? ' on' : ''}" data-side="raised"
            aria-pressed="${raised}">Raised</button>
          <button type="button" class="pane-thumb-side${raised ? '' : ' on'}" data-side="spent"
            aria-pressed="${!raised}">Spent</button></div></div>
      <button type="button" class="pane-thumb-canvas" aria-label="Open the full map for ${escapeHtml(shortName(pane.name))}">
        ${pane.thumb ? '' : `<span class="muted">${escapeHtml(pane.thumbLoading ? 'Reading the map…'
          : (pane.thumbProblem || 'No map for this side'))}</span>`}</button>
      <span class="muted pane-thumb-note">${escapeHtml(state.direction === pane.thumbDirection && state.body
        ? '' : '')}</span></div>`;
  }

  function paneHtml(pane) {
    const cycles = Object.keys(pane.totalsByCycle || {}).sort().reverse();
    const cycleTotal = cycle => (pane.totalsByCycle[cycle] || []).reduce((total, item) => total + item.amount, 0);
    const peak = Math.max(1, ...cycles.map(cycleTotal));
    const isSnapshot = pane.source === 'snapshot';
    const stats = paneStats(pane);
    const topLabel = isSnapshot ? 'Snapshot legs' : 'Top contributors in this file';
    return `<section class="card evidence-pane" data-pane="${pane.key}" data-committee="${escapeHtml(pane.committee)}" data-cycle="${escapeHtml(pane.cycle || '')}" aria-label="${escapeHtml(pane.name)} pane">
      <div class="pane-head">
        <div class="pane-title">
          <div class="pane-kind" title="${escapeHtml(pane.industry || 'Imported file')} · ${escapeHtml(pane.sourceKind)}">${escapeHtml(pane.industry || 'Imported file')} · ${escapeHtml(pane.sourceKind)}</div>
          <h2 title="${escapeHtml(pane.name)}">${escapeHtml(pane.name)}</h2>
          <div class="pane-tags">
            <span class="badge pane-category" title="${escapeHtml([conduitNote(pane.category), pane.categorySource].filter(Boolean).join(' '))}">${escapeHtml(pane.category)}</span>
            <span class="muted pane-committee">${escapeHtml(pane.committee)}</span>
            <span class="muted pane-cycle-tag"><span class="pane-tag-dot" aria-hidden="true">&middot;&nbsp; </span>${escapeHtml(cycleLabel(pane.cycle))}</span>
            ${pane.follows ? '<span class="badge">follows Graph</span>' : ''}
          </div>
        </div>
        <div class="pane-buttons">
          <div class="seg segmented pane-view" role="group" aria-label="Pane view">
            <button type="button" class="pane-summary${pane.rowsOpen ? '' : ' on'}"
              aria-pressed="${!pane.rowsOpen}">Summary</button>
            <button type="button" class="pane-map" aria-pressed="false">Map</button>
            <button type="button" class="pane-flow" aria-pressed="false">Flow</button>
            <button type="button" class="pane-rows${pane.rowsOpen ? ' on' : ''}"
              aria-pressed="${pane.rowsOpen}" aria-expanded="${pane.rowsOpen}">${pane.source === 'snapshot' ? 'PAC routes & candidates' : 'Rows'}</button>
          </div>
          <button type="button" class="secondary pane-remove" aria-label="Remove ${escapeHtml(shortName(pane.name))}">×</button>
        </div>
      </div>
      ${pane.message ? `<p class="muted pane-message">${escapeHtml(pane.message)}</p>` : ''}
      <div class="pane-figures">${stats.map(stat => `<div class="pane-figure" title="${escapeHtml(`${channelName(stat.label)}: ${stat.value} · ${stat.note}`)}" data-note="${escapeHtml(stat.note)}">
        <span class="label">${escapeHtml(channelName(stat.label))}</span>
        <strong>${stat.value}</strong></div>`).join('')}</div>
      <div class="pane-split">
        <div class="pane-cycles"><span class="label">By cycle</span>
          ${cycles.length ? cycles.map(cycle => `<div class="pane-cycle"><span class="muted">${escapeHtml(cycleLabel(cycle))}</span>
            ${bar(cycleTotal(cycle), peak)}<span class="num">${money(cycleTotal(cycle))}</span></div>`).join('')
            : '<p class="muted">No cycle is recorded for these rows.</p>'}</div>
        ${thumbHtml(pane)}
      </div>
      <div class="pane-top"><span class="label">${topLabel}</span>
        ${(pane.counterparties || []).length ? `<table><thead><tr><th scope="col">Name</th><th scope="col" class="num">Amount</th></tr></thead><tbody>${(pane.counterparties || []).slice(0, TOP_IN_PANE).map(item =>
          `<tr><td title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td><td class="num">${money(item.amount)}</td></tr>`).join('')}</tbody></table>`
          : '<p class="muted">No counterparty is named on these rows.</p>'}</div>
      ${pane.rowsOpen ? paneDrawerHtml(pane) : ''}
      <p class="muted pane-provenance">${escapeHtml(pane.provenance)} ·
        <button type="button" class="link-button pane-about" aria-expanded="false">About these figures</button></p>
      <div class="pane-about-body" hidden><p><strong>${escapeHtml(pane.name)}</strong> · ${escapeHtml(pane.committee)}</p>
        <p>${escapeHtml(pane.detail)}</p>
        <dl class="pane-about-figures">${stats.map(stat => `<dt>${escapeHtml(channelName(stat.label))}</dt><dd>${escapeHtml(stat.value)} · ${escapeHtml(stat.note)}</dd>`).join('')}</dl><ul>${(pane.warnings || [])
        .map(warning => `<li>${escapeHtml(typeof warning === 'string' ? warning : warning.message || JSON.stringify(warning))}</li>`).join('')
        || '<li>No parser warnings. Source coverage and filing completeness remain unverified.</li>'}</ul>
        ${(pane.counterparties || []).length > TOP_IN_PANE ? `<p>Also in the top ten: ${(pane.counterparties || [])
          .slice(TOP_IN_PANE).map(item => `${escapeHtml(item.name)} ${money(item.amount)}`).join(' · ')}.</p>` : ''}
        <p class="muted">Sums reflect the selected rows. Receipts, disbursements and independent spending stay separate; amended or duplicate filings may overlap.</p></div>
    </section>`;
  }

  function paneDrawerHtml(pane) {
    return pane.source === 'snapshot' ? snapshotDrawerHtml(pane) : csvDrawerHtml(pane);
  }

  function csvDrawerHtml(pane) {
    const body = pane.rows;
    if (!body) return pane.rowsError
      ? `<div class="pane-drawer"><p role="status">${escapeHtml(pane.rowsError)}</p><button type="button" class="secondary pane-rows-retry">Retry source rows</button></div>`
      : '<div class="pane-drawer"><p class="muted" role="status">Reading rows…</p></div>';
    // S8. A district clicked on the map filters this page of rows by the
    // contributor ZIP, through the same crosswalk that drew the map; the
    // header says which district and on what basis, and Clear puts it back.
    const rows = rowsInFocus(pane, body.rows || []);
    const total = body.total_rows ?? rows.length;
    const excluded = body.excluded_rows ?? 0;
    return `<div class="pane-drawer">
      ${pane.district ? `<p class="pane-district-filter">Filtered to <strong>${escapeHtml(pane.district)}</strong>`
        + `${pane.districtBasis ? ' by ' + escapeHtml(pane.districtBasis) : ''} · ${number(rows.length)} of ${number((body.rows || []).length)} rows on this page`
        + ` <button type="button" class="link-button pane-district-clear">Clear</button></p>` : ''}
      <span class="label">Source rows · ${total ? `${number(pane.offset + 1)}–${number(pane.offset + rows.length)} of ${number(total)}` : 'no matching rows'}</span>
      <p class="muted">${number(body.supported_rows ?? 0)} supported${excluded ? ` · ${number(excluded)} excluded by the workbook` : ''} · ${number(body.unsupported_rows ?? 0)} need interpretation. All selected rows contribute to the figures above, across pages.</p>
      <div class="table-scroll"><table><thead><tr><th>Row</th><th>Date / cycle</th><th class="num">Amount</th><th>Counterparty / activity</th><th>Status</th></tr></thead><tbody>
      ${rows.map(row => `<tr><td>${number(row.row_number)}</td>
        <td>${escapeHtml(row.date || 'Date unavailable')}<small>${escapeHtml(row.cycle || 'Cycle unavailable')}</small></td>
        <td class="num">${row.amount == null ? 'Not reported' : currency(row.amount)}</td>
        <td>${escapeHtml(row.counterparty || row.candidate_name || row.candidate_id || NOT_REPORTED)}<small>${escapeHtml(channelName(row.channel || row.kind))}</small></td>
        <td>${row.status === 'excluded' ? '<span class="badge excluded">excluded by the workbook</span>'
              : `<span class="muted">${escapeHtml(row.status || '')}</span>`}
          ${filingLedger(row)}
          <details><summary>Original fields</summary><dl class="csv-raw">${Object.entries(row.raw || {})
            .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join('')}</dl>
          ${(row.issues || []).map(issue => `<p>${escapeHtml(issue)}</p>`).join('')}</details></td></tr>`).join('')}
      </tbody></table></div>
      <div class="csv-pagination"><button type="button" class="secondary pane-prev"${pane.offset === 0 ? ' disabled' : ''}>Previous rows</button>
        <button type="button" class="secondary pane-next"${body.has_more ? '' : ' disabled'}>Next rows</button></div></div>`;
  }

  function snapshotDrawerHtml(pane) {
    const legs = pane.legs || {};
    const body = legs.body || {};
    const estimate = (body.cash_estimates?.cycles || []).find(row => String(row.cycle) === (pane.cycle || String(row.cycle)));
    const names = new Map((body.committee_finances || []).map(row => [row.committee_id, row.committee_name || row.committee_id]));
    return `<div class="pane-drawer">
      <span class="label">Company → PAC → candidates</span>
      <p class="muted">Explore the recorded legs. Spending from a pooled PAC is committee activity; it cannot be assigned to a particular donor without further evidence.</p>
      <h3 class="evidence-subheading">Committee transfers</h3>
      ${(legs.incoming || []).map(row => `<p>${escapeHtml(names.get(row.source) || row.source)} → ${escapeHtml(pane.name)} <b>${currency(row.amount)}</b> ${filingLedger(row)}</p>`).join('')}
      ${(legs.outgoing || []).map(row => `<p>${escapeHtml(pane.name)} → ${escapeHtml(row.destination_name || names.get(row.destination) || row.destination)} <b>${currency(row.amount)}</b> ${filingLedger(row)}</p>`).join('')
        || '<p class="muted">No contribution or transfer disbursements found in this snapshot.</p>'}
      ${networkDrawerHtml(pane)}
      <div class="allocation-panel"><h3>Conditional company cash estimate</h3>
        <p role="status">${estimate?.status === 'estimated' ? 'Estimated from the collected cash shares.'
          : 'Awaiting reconciliation. No donor allocation is calculated.'}</p>
        <details><summary>Estimation method &amp; assumptions</summary>
          <p>At each payment, assign the same fraction as the company's share of the PAC's available cash. Carry that fraction through PAC transfers; leave retained cash and operating expenses in their own categories.</p>
          <p class="formula">Estimated company-funded spending = payment × company cash share before payment</p>
          <p class="muted">Example only: a 25% cash share × $10 million of candidate spending gives a $2.5 million estimate. This is an accounting assumption, not proof of donor intent or influence.</p></details></div>
      <details><summary>Company receipt evidence and excluded conversions</summary><div class="table-scroll"><table>
        <thead><tr><th>Reported name / role</th><th>Date</th><th class="num">Amount</th><th>Filing explanation</th></tr></thead><tbody>
        ${(legs.receipts || []).map(row => `<tr><td>${escapeHtml(row.reported_name)}<small>${escapeHtml(label(row.role))} · ${row.included_company_contribution ? 'Included contribution' : 'Excluded from company contribution total'}</small></td><td>${escapeHtml(row.date)}</td><td class="num">${currency(row.amount)}</td><td>${sourceAnchor(row.source_url, row.description || 'Original filing')}<small>Line ${escapeHtml(row.line_number)} · ${escapeHtml(row.sub_id)}</small></td></tr>`).join('')
          || '<tr><td colspan="4">No matching company receipts in this committee and cycle.</td></tr>'}
        </tbody></table></div></details>
      <details class="candidate-activity"><summary>Selected PAC’s candidate activity as filed</summary>
      <p class="muted">Amounts below belong to this committee. Support and opposition stay separate. Expand a name to compare the filing with the candidate register.</p>
      <div class="table-scroll"><table><thead><tr><th>Candidate / identity check</th><th>Direction</th><th class="num">Committee spending</th><th>Activity dates</th><th>Evidence</th></tr></thead><tbody>
        ${(legs.activity || []).slice().sort((a, b) => b.committee_spending - a.committee_spending).map(row =>
          `<tr><td data-label="Candidate"><details><summary>${escapeHtml(row.candidate_name_as_filed || 'Candidate name missing')}</summary><p>As-filed ID: ${escapeHtml(row.candidate_id_as_filed)}<br>Register: ${escapeHtml(row.registered_candidate_name || 'Unresolved')}<br>${row.identity_status === 'name_matches_registration' ? 'Name matches the candidate register.' : 'Name / ID needs review. Aliases and filing errors can cause differences; no automatic reassignment.'}</p></details></td>
          <td data-label="Direction">${escapeHtml(row.direction)}</td><td class="num" data-label="Committee spending">${currency(row.committee_spending)}</td>
          <td data-label="Activity dates">${escapeHtml(row.first_date)}<small>to ${escapeHtml(row.last_date)}</small></td>
          <td data-label="Evidence">${filingLedger(row)}<small>${number(row.source_rows)} reported ${row.source_rows === 1 ? 'transaction' : 'transactions'}</small></td></tr>`).join('')
          || '<tr><td colspan="5">No candidate activity in this committee and cycle.</td></tr>'}
      </tbody></table></div></details>
      <details><summary>What is needed for a donor allocation?</summary><p>Reconcile all receipts and disbursements, opening and retained cash, asset conversions and refunds. Enforce donation → transfer → expenditure dates, including transfers between cycles. Document any earmark before calling a dollar amount traced. Otherwise, show assumptions and uncertainty with the estimate.</p></details></div>`;
  }

  /* F4 (Paul approved 2026-09-25). The network showed one coverage window per
     committee - its summary's, which runs past its itemized rows (Defend
     American Jobs 2026: summary to 7/31, itemized spending to June) - and
     checked only the pane's own committee against its FEC summary. The server
     now measures each committee (downstream.snapshot_view `committees`): the
     last itemized transaction and the summary's coverage end, and its itemized
     independent expenditures and lines 22-23 transfers against its own summary,
     as itemized minus summary. These only put them in words. */
  const checkMark = check => {
    if (!check || check.status === 'no_summary')
      return '<span class="recon-mark recon-none">No summary saved</span>';
    const detail = `Itemized ${currency(check.itemized)} · summary ${currency(check.summary)} · itemized − summary ${currency(check.difference)}`;
    return check.status === 'reconciled'
      ? `<span class="recon-mark recon-ok" title="${escapeHtml(detail)}">Reconciled</span>`
      : `<span class="recon-mark recon-differs" title="${escapeHtml(detail)}">Differs ${escapeHtml(currency(check.difference))}</span>`;
  };
  const checkWords = check => !check || check.status === 'no_summary' ? 'no summary saved'
    : check.status === 'reconciled' ? 'reconciled to its summary'
    : `differ from its summary by ${currency(check.difference)} (itemized ${currency(check.itemized)}, summary ${currency(check.summary)})`;
  const committeeWho = row => (row.committee_name && row.committee_name !== row.committee ? row.committee_name + ' ' : '')
    + `(${row.committee}, ${cycleLabel(row.cycle)})`;
  const itemizedTitle = row => row.saved
    ? `Last itemized: receipts ${(row.last_itemized || {}).receipts || 'none'} · disbursements ${(row.last_itemized || {}).disbursements || 'none'} · independent expenditures ${(row.last_itemized || {}).expenditures || 'none'} · summary ${row.coverage_start || 'unknown'} to ${row.coverage_end || 'unknown'}`
    : 'No saved snapshot for this committee and cycle';
  const coverageWords = row => !row ? '' : row.saved
    ? `itemized to ${row.last_itemized_date || 'no dated row'} · summary to ${row.coverage_end || 'unknown'}`
    : 'no saved snapshot';
  // Up to UNSAVED_ROWS committees with no saved snapshot get a row and a
  // sentence each; past that they share one, still "No summary saved", and the
  // missing-evidence line above names every one of them.
  const UNSAVED_ROWS = 8;
  const groupUnsaved = committees => committees.filter(row => !row.saved).length > UNSAVED_ROWS;
  function networkChecks(committees) {
    const unsaved = committees.filter(row => !row.saved);
    const lines = (groupUnsaved(committees) ? committees.filter(row => row.saved) : committees)
      .map(row => `${committeeWho(row)}: independent expenditures ${checkWords((row.reconciliation || {}).independent_expenditures)}; `
        + `transfers on lines 22-23 ${checkWords((row.reconciliation || {}).transfers)}; ${coverageWords(row)}.`);
    if (groupUnsaved(committees))
      lines.push(`No summary saved for the ${number(unsaved.length)} committees in this network with no saved snapshot: `
        + unsaved.slice(0, 10).map(row => `${row.committee} (${cycleLabel(row.cycle)})`).join(', ')
        + (unsaved.length > 10 ? ` and ${number(unsaved.length - 10)} more.` : '.'));
    return lines;
  }
  function networkCommitteesHtml(committees) {
    if (!committees.length) return '';
    const grouped = groupUnsaved(committees);
    const unsaved = committees.filter(row => !row.saved);
    return `<h4 class="network-committees-title">Committees in this network</h4>
      <p class="muted">Each committee's own coverage, and its own itemized rows against its FEC summary (itemized − summary).</p>
      <div class="table-scroll"><table class="network-committees"><thead><tr><th scope="col">Committee</th><th scope="col">Itemized to</th>
        <th scope="col">Summary to</th><th scope="col">Independent expenditures</th><th scope="col">Transfers, lines 22–23</th></tr></thead><tbody>
      ${(grouped ? committees.filter(row => row.saved) : committees).map(row => `<tr data-committee="${escapeHtml(row.committee)}" data-cycle="${escapeHtml(row.cycle)}">
        <td>${escapeHtml(row.committee_name || row.committee)}<small>${escapeHtml(row.committee)} · ${escapeHtml(cycleLabel(row.cycle))}</small></td>
        <td title="${escapeHtml(itemizedTitle(row))}">${escapeHtml(row.saved ? (row.last_itemized_date || 'No dated row') : 'No saved snapshot')}</td>
        <td>${escapeHtml(row.saved ? (row.coverage_end || 'Unknown') : '—')}</td>
        <td>${checkMark((row.reconciliation || {}).independent_expenditures)}</td>
        <td>${checkMark((row.reconciliation || {}).transfers)}</td></tr>`).join('')}
      ${grouped ? `<tr class="network-unsaved" data-committee="unsaved"><td>${number(unsaved.length)} committees with no saved snapshot<small>each named under Missing recipient PAC evidence above</small></td>
        <td>No saved snapshot</td><td>—</td><td>${checkMark(null)}</td><td>${checkMark(null)}</td></tr>` : ''}
      </tbody></table></div>`;
  }

  function networkDrawerHtml(pane) {
    const network = pane.network;
    if (!network) return `<h3 class="evidence-subheading">Candidate spending across PAC network</h3>
      <p role="status">Downstream evidence unavailable: ${escapeHtml(pane.networkProblem || 'not loaded')}. Missing evidence is not zero spending.</p>`;
    const committees = network.committees || [];
    const own = new Map(committees.map(row => [`${row.cycle}|${row.committee}`, row]));
    return `<section class="evidence-network"><h3 class="evidence-subheading">${escapeHtml(pane.name)}-backed candidate spending</h3>
      <p class="muted">Backing is assumed from recorded PAC transfers. Each spending PAC is counted once per cycle.</p>
      ${(network.missing || []).length ? `<p role="status">Missing recipient PAC evidence: ${network.missing.map(row => `${escapeHtml(row.committee)} (${escapeHtml(row.cycle)})`).join(', ')}. Missing evidence is not zero spending.</p>` : ''}
      ${networkCommitteesHtml(committees)}
      <label>Find candidate or PAC<input type="search" class="network-search" placeholder="Name or FEC ID" value="${escapeHtml(pane.networkQuery || '')}"></label>
      <div class="table-scroll"><table class="network-routes"><thead><tr><th>Candidate</th><th>Observed PAC route / spender</th><th>Cycle</th><th>Direction</th><th class="num">PAC spending</th></tr></thead><tbody>
      ${(network.relationships || []).map(row => `<tr><td>${sourceAnchor('https://www.fec.gov/data/candidate/' + row.candidate_id + '/', row.candidate)}<small>${escapeHtml(row.candidate_id)}</small></td>
        <td>${escapeHtml((row.route_names || row.route).join(' → '))}<small>${escapeHtml(row.route.join(' → '))}</small>${own.has(`${row.cycle}|${row.committee}`) ? `<small class="network-coverage">Spender ${escapeHtml(coverageWords(own.get(`${row.cycle}|${row.committee}`)))}</small>` : ''}</td>
        <td>${escapeHtml(cycleLabel(row.cycle))}</td><td>${escapeHtml(row.direction)}</td><td class="num">${currency(row.amount)}${filingLedger(row)}</td></tr>`).join('')
        || '<tr><td colspan="5">No candidate activity recorded in the available network evidence.</td></tr>'}
      </tbody></table></div>
      <details><summary>Network source coverage and hashes</summary><p>${escapeHtml(network.note)} Transfer amounts are not added to candidate spending.</p>${(network.sources || []).map(row => `<p>${escapeHtml(own.has(`${row.cycle}|${row.committee}`) ? committeeWho(own.get(`${row.cycle}|${row.committee}`)) : row.committee)} · ${escapeHtml(cycleLabel(row.cycle))} · summary ${escapeHtml(String(row.coverage_start || 'Unknown start').slice(0, 10))} to ${escapeHtml(String(row.coverage_end || 'Unknown end').slice(0, 10))} · last itemized transaction ${escapeHtml((own.get(`${row.cycle}|${row.committee}`) || {}).last_itemized_date || 'not dated')}<small>${escapeHtml(row.source)}<br>SHA-256 ${escapeHtml(row.sha256)}</small></p>`).join('')}</details></section>`;
  }

  function filterNetworkRows(root, query) {
    const terms = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
    for (const row of root.querySelectorAll('.evidence-network .network-routes tbody tr'))
      row.hidden = !terms.every(term => row.textContent.toLowerCase().includes(term));
  }

  function renderPanes() {
    const open = PANE_KEYS.filter(key => panes.has(key)).map(key => panes.get(key));
    const host = $('evidence-panes');
    // F3. The figure type and the card grid narrow with the number of panes; the
    // count rides on the host so the CSS can key off it without a container query.
    host.dataset.panes = String(open.length);
    host.style.gridTemplateColumns = `repeat(${Math.max(1, splitWithGraph() ? 1 : open.length)}, minmax(0, 1fr))`;
    render(host, open.map(paneHtml).join(''));
    applyBars(host);
    for (const pane of open) {
      paintThumb(pane);
      const root = host.querySelector(`[data-pane="${pane.key}"]`);
      if (root) filterNetworkRows(root, pane.networkQuery);
    }
    host.hidden = Boolean(mapPane || flowPane);
    $('evidence-empty').hidden = open.length > 0 || Boolean(mapPane || flowPane);
  }

  /* The pane thumbnail, drawn straight into the card. One <svg> of the shipped
     state paths filled from `by_state`; no interaction, no second projection,
     no second data path - if the map route refused this side, the card says so
     in the route's own words instead of drawing an empty country. */
  function paintThumb(pane) {
    const host = document.querySelector(`[data-thumb="${CSS.escape(pane.key)}"] .pane-thumb-canvas`);
    if (!host || !pane.thumb || !pane.thumb.shapes) return;
    const body = pane.thumb.body || {};
    const amounts = new Map((body.by_state || []).map(row => [row.key, row.amount || 0]));
    const positive = [...amounts.values()].filter(value => value > 0);
    const domain = positive.length ? [Math.min(...positive), Math.max(...positive)] : [0, 0];
    const shapes = pane.thumb.shapes;
    const paths = Object.entries(shapes.states || {}).map(([key, entry]) =>
      `<path d="${escapeHtml(entry.path)}" data-fill="${escapeHtml(thumbColour(amounts.get(key) || 0, domain))}"></path>`);
    render(host, `<svg viewBox="0 0 ${shapes.width || 975} ${shapes.height || 610}" role="img"
      aria-label="${escapeHtml(money(body.reported_total))} across ${number((body.by_state || []).length)} states"
      preserveAspectRatio="xMidYMid meet" class="pane-thumb-svg">${paths.join('')}</svg>`);
    for (const node of host.querySelectorAll('[data-fill]')) node.style.fill = node.dataset.fill;
  }

  const THUMB_RAMP = ['#e8f3f1', '#b9ddd8', '#7cbfb7', '#4c9e95', '#327a7a'];
  function thumbColour(value, domain) {
    if (!(value > 0)) return THUMB_RAMP[0];
    const span = THUMB_RAMP.length - 1;
    const position = domain[0] === domain[1] ? 1 : Math.min(1, Math.max(0,
      (Math.log(value) - Math.log(domain[0])) / (Math.log(domain[1]) - Math.log(domain[0]))));
    return THUMB_RAMP[Math.min(span, Math.round(position * span))];
  }

  /* One map payload per pane, for the thumbnail. Fetched AFTER the pane's own
     figures are on screen, never before: the card is readable without it. */
  async function loadThumb(pane) {
    const cycles = mapCycles(pane);
    const cycle = String(pane.cycle || cycles[0] || '');
    if (!cycle) { pane.thumbProblem = 'This source records no cycle to draw.'; renderPanes(); return; }
    pane.thumbLoading = true;
    pane.thumbProblem = '';
    // A later read (the reader flipped Raised | Spent meanwhile) wins.
    const ticket = pane.thumbTicket = (pane.thumbTicket || 0) + 1;
    renderPanes();
    try {
      const read = direction => api('/api/evidence-geo?' + new URLSearchParams({
        source: pane.source, id: pane.id, committee: pane.committee, cycle, direction}));
      const asked = pane.thumbDirection;
      let body = await read(asked);
      // Fix r2 (TC-11): like the enlarged map (openOnTheSideWithData), the
      // thumbnail opens on the side that HAS the money until the reader picks
      // one: `sides` comes back with the first payload, so this costs a second
      // read only when the first side was the thinner one.
      const other = asked === 'raised' ? 'spent' : 'raised';
      if (ticket === pane.thumbTicket && !pane.thumbChosen && body.sides
          && ((body.sides[other] || {}).reported_total || 0) > ((body.sides[asked] || {}).reported_total || 0)) {
        body = await read(other);
        if (ticket === pane.thumbTicket) pane.thumbDirection = other;
      }
      const shapes = await vintageFile(body.boundary_file);
      if (ticket !== pane.thumbTicket) return;
      pane.thumb = {body, shapes};
      pane.thumbProblem = body.reason || '';
    } catch (error) {
      if (ticket !== pane.thumbTicket) return;
      pane.thumb = null;
      pane.thumbProblem = error.message;
    }
    pane.thumbLoading = false;
    renderPanes();
  }

  const splitWithGraph = () => document.querySelector('.workspace')?.classList.contains('split-workspace') || false;

  function compareRows(open) {
    const channels = [];
    for (const pane of open) for (const item of pane.totals || []) if (!channels.includes(item.channel)) channels.push(item.channel);
    const lead = channels.includes('Receipts') ? 'Receipts' : (channels[0] || 'Receipts');
    const others = channels.filter(channel => channel !== lead);
    const cycles = [...new Set(open.flatMap(pane => Object.keys(pane.totalsByCycle || {})))].sort();
    const channelAmount = (pane, channel) => {
      const item = (pane.totals || []).find(entry => entry.channel === channel);
      return item ? item.amount : 0;
    };
    const cycleAmount = (pane, cycle) => (pane.totalsByCycle[cycle] || []).reduce((total, item) => total + item.amount, 0);
    const rows = [[`${channelName(lead)} · ${cycleLabel(open[0].cycle)}`, pane => channelAmount(pane, lead), 'money']];
    for (const channel of others) rows.push([channelName(channel), pane => channelAmount(pane, channel), 'money']);
    for (const cycle of cycles) rows.push([cycleLabel(cycle), pane => cycleAmount(pane, cycle), 'money']);
    rows.push(['Rows supported', pane => pane.supported, 'count']);
    rows.push(['Rows excluded by the workbook', pane => pane.excluded, 'count']);
    rows.push(['Top contributor', pane => (pane.counterparties || [])[0]?.name || '—', 'text']);
    rows.push(['Category', pane => pane.category, 'text']);
    rows.push(['Source', pane => `${pane.industry || 'Imported file'} · ${pane.sourceKind}`, 'text']);
    return rows;
  }

  function renderCompare() {
    const open = PANE_KEYS.filter(key => panes.has(key)).map(key => panes.get(key));
    const host = $('evidence-compare');
    host.hidden = open.length < 2 || Boolean(mapPane || flowPane);
    if (open.length < 2) { render(host, ''); return; }
    const heads = open.map(pane => `<th title="${escapeHtml(pane.name)}">${escapeHtml(shortName(pane.name))}`
      + `<br><span class="th-sub">${escapeHtml(pane.industry || 'Imported file')} · ${escapeHtml(pane.category)}</span></th>`).join('');
    const body = compareRows(open).map(([metric, read, kind]) => {
      const values = open.map(read);
      const peak = Math.max(1, ...values.map(value => typeof value === 'number' ? value : 0));
      const cells = values.map(value => {
        if (kind === 'text' || value == null) return `<td><span>${escapeHtml(value == null ? '—' : String(value))}</span></td>`;
        return `<td><div class="compare-cell">${bar(value, peak, true)}<span class="num">${kind === 'money' ? money(value) : number(value)}</span></div></td>`;
      }).join('');
      return `<tr><td class="compare-metric">${escapeHtml(metric)}</td>${cells}</tr>`;
    }).join('');
    render(host, `<div class="panel-heading"><h2>Compare</h2>
      <button type="button" class="secondary" id="evidence-compare-map">Top districts &middot; Map</button>
      <span class="muted">Bars are scaled per row to the largest pane. Figures are observed filings from each source; a bar across industries compares sources, not FEC totals.</span></div>
      <div class="table-scroll"><table><thead><tr><th class="compare-metric">Metric</th>${heads}</tr></thead><tbody>${body}</tbody></table></div>`);
    applyBars(host);
  }

  /* --------------------------------------------------------------- pane rows */
  async function loadPaneRows(pane) {
    if (panes.get(pane.key) !== pane) return;
    if (pane.source === 'snapshot') { renderPanes(); return; }
    const ticket = ++pane.rowsTicket;
    const requested = {source: pane.source, id: pane.id, committee: pane.committee, cycle: pane.cycle, offset: pane.offset};
    const current = () => panes.get(pane.key) === pane && pane.rowsTicket === ticket
      && Object.entries(requested).every(([key, value]) => pane[key] === value);
    pane.rows = null; pane.rowsLoading = true; pane.rowsError = '';
    renderPanes();
    try {
      const body = await api('/api/evidence-csv?' + new URLSearchParams({id: requested.id, committee: requested.committee,
        cycle: requested.cycle, offset: String(requested.offset), limit: String(PAGE_SIZE)}));
      if (!current()) return;
      pane.rows = body;
    } catch (error) {
      if (!current()) return;
      pane.rows = null; pane.rowsError = error.message;
    }
    if (!current()) return;
    pane.rowsLoading = false;
    renderPanes();
  }

  /* ----------------------------------------------------------------- the URL */
  function writeScope() {
    const api = scope();
    if (!api || restored === false) return;
    const partial = {};
    for (const key of PANE_KEYS) {
      const pane = panes.get(key);
      partial[key + '.source'] = pane ? pane.source : '';
      partial[key + '.id'] = pane ? pane.id : '';
      partial[key + '.committee'] = pane ? pane.committee : '';
      partial[key + '.cycle'] = pane ? pane.cycle : '';
      // S8. `<pane>.map` is the open map's direction, and absent when the pane
      // grid is showing: a permalink reopens exactly the view it was cut from.
      partial[key + '.map'] = (pane && mapPane === key) ? mapStateFor(key).direction : '';
      // Fix round 1 (R06-09): the second cycle of an open map compare. scope.js
      // drops a key it does not list, so this reaches the URL once `compare`
      // is one of its PANE_FIELDS (requested of scope.js's owner).
      partial[key + '.compare'] = (pane && mapPane === key && mapStateFor(key).compare) || '';
      // Lane F: `<pane>.flow` is the basis of the Flow view open on the pane, absent otherwise.
      const flowState = pane && flowPane === key && window.MentatEvidenceFlow && window.MentatEvidenceFlow.state();
      partial[key + '.flow'] = flowState ? flowState.basis : '';
    }
    partial.category = $('evidence-type').value || '';
    api.write(partial);
  }

  async function restorePanes() {
    const api = scope();
    restored = true;
    if (!api) return;
    const current = api.read();
    if (current.category) {
      $('evidence-type').value = current.category;
      if ($('evidence-type').value !== current.category) $('evidence-type').value = '';
    }
    let opened = 0;
    for (const key of PANE_KEYS) {
      if (panes.size >= MAX_PANES) break;
      const source = current[key + '.source'], id = current[key + '.id'], committee = current[key + '.committee'];
      if (!source || !id || !committee) continue;
      if (source === 'snapshot' && !snapshotBodies.has(id)) {
        try { snapshotBodies.set(id, await api2Evidence(id)); } catch { continue; }
      }
      if (source === 'csv' && !importById(id)) continue;
      const pane = paneFrom(key, source, id, committee, current[key + '.cycle'] || '');
      panes.set(key, pane);
      opened += 1;
      await loadPane(pane);
    }
    const wantsMap = PANE_KEYS.find(key => panes.has(key) && current[key + '.map']);
    if (wantsMap) {
      mapStateFor(wantsMap).direction = current[wantsMap + '.map'];
      // A compare cycle is restored only when the pane's source records it.
      const compare = String(current[wantsMap + '.compare'] || '');
      if (/^\d{4}$/.test(compare) && mapCycles(panes.get(wantsMap)).includes(compare)) {
        mapStateFor(wantsMap).compare = compare;
      }
    }
    if (opened) {
      const first = panes.get(PANE_KEYS.find(key => panes.has(key)));
      $('evidence-source').value = first.source + ':' + first.id;
      await chooseSource();
      $('evidence-cycle').value = first.cycle || '';
    }
    renderPanes(); renderRail(); renderCompare(); applyFilter();
    // The permalink NAMES the side, so restoring one is the reader's choice
    // and the map must not second-guess it by opening on the other.
    if (wantsMap) await openMap(wantsMap, current[wantsMap + '.map'], true);
    else {
      const wantsFlow = PANE_KEYS.find(key => panes.has(key) && current[key + '.flow']);
      if (wantsFlow) openFlow(wantsFlow, current[wantsFlow + '.flow']);
    }
  }

  const api2Evidence = id => api('/api/evidence?id=' + encodeURIComponent(id));

  /* ------------------------------------------------------------ Follow Graph */
  // Ported from app.js: Graph publishes its company and cycle on mentat:graph,
  // and pane A tracks it through the snapshot catalog when the box is checked.
  async function followGraph() {
    if (!$('evidence-follow-graph').checked || !graphContext) return;
    const companies = graphContext.filters?.company ? [graphContext.filters.company]
      : (graphContext.options?.companies || (graphContext.companies || []).map(row => row.id));
    const wanted = new Set((companies || []).map(MentatState.normalizeName));
    const cycle = graphContext.filters?.cycle || '';
    const match = snapshots.find(row => wanted.has(MentatState.normalizeName(row.company))
      && (!cycle || (row.cycles || []).some(value => String(value) === cycle)));
    if (!match) {
      const sector = graphContext.industry || '';
      if (sector && [...$('pac-direct-sector').options].some(option => option.value === sector)) {
        $('pac-direct-sector').value = sector;
        if (cycle) $('pac-direct-cycle').value = cycle;
        refreshDirectCatalog();
        say('Choose a PAC from this sector above to open its flow.');
      } else say('Choose a sector or search for a PAC above to open its flow.');
      return;
    }
    let body = snapshotBodies.get(match.id);
    if (!body) { body = await api2Evidence(match.id); snapshotBodies.set(match.id, body); }
    const finances = (body.committee_finances || []).filter(row => !cycle || String(row.cycle) === cycle);
    if (!finances.length) { say(`Snapshot ${match.id} covers no ${cycleLabel(cycle)} activity.`); return; }
    const pane = paneFrom('a', 'snapshot', match.id, finances[0].committee_id, cycle);
    pane.follows = true;
    panes.set('a', pane);
    await loadPane(pane);
    writeScope();
  }

  /* ------------------------------------------------------- import mechanics  */
  function hideSheetPicker() { $('evidence-sheet-label').hidden = true; }
  function showSheets(job) {
    const sheets = job.sheets || [];
    if (sheets.length > 1) {
      $('evidence-sheet').replaceChildren(...sheets.map(name => new Option(name, name)));
      $('evidence-sheet').value = sheets.includes(job.sheet) ? job.sheet : sheets[0];
      $('evidence-sheet-label').hidden = false;
    } else hideSheetPicker();
  }

  // app.js assigns the page's token when GET /api/session?light=1 answers, a
  // different round trip from the listing this module fetches on load, so a
  // reader who chose a file in that window got HTTP 403 'Refresh the app before
  // continuing.' and had to reload. `token` is a top-level `let` in app.js and
  // both files are classic scripts, so they share the binding.
  async function awaitToken(name) {
    if (token) return true;
    say(`${name}: Waiting for the console session…`);
    const deadline = Date.now() + 5000;
    while (!token && Date.now() < deadline) {
      try { const session = await api('/api/session?light=1'); if (!token && session && session.token) token = session.token; }
      catch { /* app.js is asking for the same thing; try again below */ }
      if (token) return true;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (token) return true;
    say(`${name}: The console session is not ready. Reload the page, then choose the file again.`);
    return false;
  }

  async function followJob(job, ticket, chosenName) {
    let current = job, failures = 0;
    while (ticket === importRevision) {
      const name = current.name || chosenName;
      if (current.state === 'failed') { showSheets(current); say(`${name}: ${current.message || 'The file could not be read.'}`); return; }
      if (current.state === 'ready') {
        showSheets(current);
        await refreshSources();
        if (ticket !== importRevision) return;
        $('evidence-source').value = 'csv:' + current.id;
        await chooseSource();
        return;
      }
      say(current.bytes_read
        ? `Reading ${name} · ${number(current.rows_read)} rows · ${megabytes(current.bytes_read)} of ${megabytes(current.bytes)} MB`
        : (!current.rows_read && (current.format === 'xlsx' || current.format === 'json'))
          ? `Reading ${name} · opening the ${current.format === 'xlsx' ? 'workbook' : 'file'}…`
          : `Reading ${name} · ${number(current.rows_read)} rows`);
      await new Promise(resolve => setTimeout(resolve, POLL_DELAY));
      if (ticket !== importRevision) return;
      try { current = await api('/api/evidence-csv?' + new URLSearchParams({job: job.id})); failures = 0; }
      catch (error) {
        // One failed poll is not a failed import: the read can lose a race with
        // the progress the reader itself is writing. Keep the last good state.
        failures += 1;
        say(`${name}: GET /api/evidence-csv?job=${job.id} failed: ${error.message}`
          + (failures < POLL_FAILURE_LIMIT ? ' Retrying.' : ''));
        if (failures >= POLL_FAILURE_LIMIT) return;
      }
    }
  }

  async function importFile(file, sheet) {
    const ticket = ++importRevision; hideSheetPicker();
    const suffix = suffixOf(file.name);
    if (limits && limits.accepted_suffixes && !limits.accepted_suffixes.includes(suffix)) {
      say(`${file.name}: This file type (${suffix}) is not supported. Choose CSV, TSV, TXT, XLSX, JSON or an FEC bulk ZIP.`);
      return;
    }
    if (limits && file.size > limits.max_bytes) { say(`${file.name} (${megabytes(file.size)} MB): ${limits.max_bytes_message}`); return; }
    pendingUploadFile = file; pendingPathRequest = null;
    if (!await awaitToken(file.name)) return;
    if (ticket !== importRevision) return;
    say(`Uploading ${file.name}…`);
    let response;
    try {
      const headers = {'Content-Type': suffix === '.csv' ? 'text/csv' : 'application/octet-stream',
        'X-Mentat-Token': token, 'X-Evidence-Name': encodeURIComponent(file.name)};
      if (sheet) headers['X-Evidence-Sheet'] = encodeURIComponent(sheet);
      response = await fetch('/api/evidence-csv', {method: 'POST', headers, body: file});
    } catch (error) { if (ticket === importRevision) say(`${file.name}: upload to /api/evidence-csv failed: ${error.message}`); return; }
    if (ticket !== importRevision) return;
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (ticket !== importRevision) return;
    if (!body || typeof body !== 'object') { say(`${file.name}: /api/evidence-csv answered HTTP ${response.status} with no error message.`); return; }
    if (!response.ok) { say(`${file.name}: ${body.error || `/api/evidence-csv answered HTTP ${response.status} with no error message.`}`); return; }
    await followJob(body, ticket, file.name);
  }

  async function importPath(path, sheet, headerPath) {
    const ticket = ++importRevision; hideSheetPicker();
    pendingPathRequest = {path, header_path: headerPath || ''}; pendingUploadFile = null;
    $('evidence-path').value = path;
    if (!await awaitToken(path)) return;
    if (ticket !== importRevision) return;
    say(`Reading ${path}…`);
    let job;
    try { job = await api('/api/evidence-csv', {path, sheet: sheet || '', header_path: headerPath || ''}); }
    catch (error) { if (ticket === importRevision) say(`${path}: ${error.message}`); return; }
    if (ticket !== importRevision) return;
    await followJob(job, ticket, path);
  }

  function readPathBox() {
    const path = $('evidence-path').value.trim();
    if (!path) { say('Enter a path on this computer, then choose Read this file.'); return; }
    importPath(path, '', $('evidence-header-path').value.trim());
  }

  function renderBrowser(body) {
    const header = `<p class="muted">${escapeHtml(body.path || 'This computer')}</p>`;
    const up = body.parent ? `<button type="button" class="secondary" data-browse-path="${escapeHtml(body.parent)}">Up</button>` : '';
    const roots = (body.roots || []).map(root => `<button type="button" class="secondary" data-browse-path="${escapeHtml(root)}">${escapeHtml(root)}</button>`).join('');
    const shortcuts = (body.shortcuts || []).map(item => `<button type="button" class="secondary" data-browse-path="${escapeHtml(item.path)}">${escapeHtml(item.name)}</button>`).join('');
    const rows = (body.entries || []).map(entry => {
      const size = entry.kind === 'dir' ? '' : number(entry.size);
      const cells = `<td>${escapeHtml(entry.name)}</td><td>${escapeHtml(entry.kind)}</td><td class="num">${size}</td><td>${escapeHtml(String(entry.modified || '').slice(0, 10))}</td>`;
      if (entry.kind === 'dir') return `<tr data-browse-path="${escapeHtml(entry.path)}">${cells}</tr>`;
      if (entry.supported) return `<tr data-open-path="${escapeHtml(entry.path)}">${cells}</tr>`;
      return `<tr class="muted" title="Not a supported file type">${cells}</tr>`;
    }).join('');
    render($('evidence-browser'), `${header}<div class="evidence-toolbar">${up}${roots}${shortcuts}</div>`
      + `<div class="table-scroll"><table><thead><tr><th>Name</th><th>Kind</th><th>Size</th><th>Modified</th></tr></thead><tbody>${rows}</tbody></table></div>`
      + (body.truncated ? '<p class="muted">Showing the first 2,000 entries.</p>' : ''));
  }

  async function browse(path) {
    try { renderBrowser(await api('/api/evidence-files', {path: path || ''})); }
    catch (error) { say(error.message); }
  }

  /* -------------------------------------------------------- the collector    */
  function collectorState() {
    $('evidence-collect').disabled = collectorReadOnly || !collectorAvailable || !!evidenceJob;
    if (!collectorAvailable) {
      $('evidence-job-status').textContent =
        'Restart the MENTAT server to enable new snapshot collection. Saved evidence controls are ready to use.';
    }
  }

  async function pollEvidenceJob() {
    if (!evidenceJob) return;
    try {
      const job = await api('/api/evidence-job?id=' + encodeURIComponent(evidenceJob));
      $('evidence-job-status').textContent = job.state === 'collecting'
        ? `Collecting ${job.company} evidence… Saved responses are retained as collection progresses.`
        : job.message || label(job.state);
      if (job.state !== 'collecting') {
        clearTimeout(evidenceJobTimer);
        const id = evidenceJob; evidenceJob = '';
        try { localStorage.removeItem('mentat-evidence-job'); } catch {}
        collectorState();
        if (job.state === 'completed') {
          await refreshSources();
          if ([...$('evidence-source').options].some(option => option.value === 'snapshot:' + id)) {
            $('evidence-source').value = 'snapshot:' + id;
            await chooseSource();
          }
        }
        return;
      }
    } catch (error) { $('evidence-job-status').textContent = error.message + ' · retrying'; }
    evidenceJobTimer = setTimeout(pollEvidenceJob, 3000);
  }

  /* ------------------------------------------------------------ the map (S8) */
  /* One map per pane, opened from the pane header or from the Compare heading.

     Geography is static and served from /geo (built once by
     tools/build_district_geo.py); the money comes from GET /api/evidence-geo,
     which has already reconciled located + unlocated back to what the committee
     reported. This module draws what that route balanced and never re-adds it.

     Heat: candidate spending colours the DISTRICT; Senate races, transfers,
     operating payments and asset conversions colour the whole STATE, because
     that is as far as their basis reaches (S6_DESIGN). Support and oppose stay
     apart in the list and the tooltip; the map heats the amount. */
  const MAP_RAMP = ['#f1e9e3', '#d6c8e2', '#8b6984', '#57447b', '#342649'];
  const MAP_OUTLINE = '#bcaea1';
  let mapPane = '';                    // the pane key whose map is open, or ''
  let flowPane = '';                   // lane F: the pane key whose Flow is open, or ''
  let mapRenderTicket = 0;
  const mapStates = new Map();         // pane key -> map view state
  const vintages = new Map();          // '118' -> the parsed boundary file
  let crosswalk = null, crosswalkVintage = '';

  function mapStateFor(key) {
    if (!mapStates.has(key)) {
      mapStates.set(key, {direction: 'spent', cycle: null, body: null, off: null,
                          district: '', message: '', loading: false,
                          note: '', geocode: null, geocodeTimer: 0, geocodeMessage: '',
                          // P4b. `layer` is the geography being drawn; `focus` is what
                          // the reader picked on it. `view` is the zoom and pan, kept
                          // here rather than in the URL - see openMap's note.
                          // Fix round 1 (R06-07): the map opens on State, as approved
                          // board 6 and M13 draw it (the boards are binding; the map
                          // suites now choose Congressional district themselves).
                          layer: 'state', layerChosen: false, focus: null, view: null,
                          enlarged: false, search: '', searchMessage: '',
                          chamber: 'upper', sldState: '', sldBundle: null, sldRows: [],
                          listQuery: '', listSort: 'amount', listPage: 0, pins: [],
                          sourceRows: null, sourceOffset: 0, sourceTicket: 0, requestTicket: 0,
                          directionChosen: false, unplaced: [], skeleton: false,
                          // Build 06 (board M13): a second cycle drawn beside the
                          // first at the same zoom and pan. Written as the flat key
                          // `<pane>.compare` (D-039), which the URL keeps once
                          // scope.js lists `compare` among its pane fields.
                          compare: '', compareBody: null, compareSldRows: [],
                          compareTicket: 0, compareMessage: ''});
    }
    return mapStates.get(key);
  }

  // Legislative geometry is loaded one state/chamber at a time.
  const MAP_LAYERS = [
    {key: 'region', label: 'Region'},
    {key: 'state', label: 'State'},
    {key: 'district', label: 'Congressional district'},
    {key: 'sld', label: 'State district'},
    {key: 'zip', label: 'ZIP code'},
  ];

  /* evidence-map.js is not in a script tag, because this lane may not edit the
     page's templates. It is appended on first use as a same-origin <script>,
     which the console's `script-src 'self'` allows, so a reader who never opens
     a map never fetches it. */
  let mapModule = null;
  function ensureMapModule() {
    if (window.MentatEvidenceMap) return Promise.resolve(window.MentatEvidenceMap);
    if (mapModule) return mapModule;
    mapModule = new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = '/evidence-map.js';
      tag.addEventListener('load', () => resolve(window.MentatEvidenceMap));
      tag.addEventListener('error', () => reject(new Error('The interactive map could not be loaded.')));
      document.head.appendChild(tag);
    });
    return mapModule;
  }

  let zipPoints = null;
  let sldIndex = null, sldCrosswalk = null;
  const sldBundles = new Map();
  const geoRequests = new Map();
  // Deduplicate concurrent renders and retain at most six detailed bundles.
  async function legislativeFile(name) {
    if (sldBundles.has(name)) {
      const body = sldBundles.get(name);
      sldBundles.delete(name); sldBundles.set(name, body);
      return body;
    }
    if (geoRequests.has(name)) return geoRequests.get(name);
    const request = (async () => {
      const response = await fetch('/geo/' + name);
      if (!response.ok) throw new Error('Legislative geography could not be loaded. Select the layer again to retry.');
      const body = await response.json();
      sldBundles.set(name, body);
      while (sldBundles.size > 6) sldBundles.delete(sldBundles.keys().next().value);
      return body;
    })();
    geoRequests.set(name, request);
    try { return await request; } finally { geoRequests.delete(name); }
  }
  async function zipPointsFile(name) {
    if (zipPoints) return zipPoints;
    zipPoints = await vintageFile(name || 'zcta_points.json');
    return zipPoints;
  }

  /* RULING A (Paul, 2026-09-18): "There should be no all cycles" on the map.

     A district map is drawn on ONE Congress, so the cycle select offers only
     the concrete cycles the pane's source records - never the pane's own
     "All cycles". A pane sitting on All cycles opens on the newest cycle the
     source carries and the map header says so once, rather than the map
     quietly disagreeing with the pane it was opened from. The route refuses a
     blank cycle outright, so this is not decoration: without it the map would
     answer 400. */
  const mapCycles = pane => [...new Set((pane.cycles || []).map(String).filter(Boolean))]
    .sort((first, second) => Number(second) - Number(first));

  function chooseMapCycle(pane, state) {
    const cycles = mapCycles(pane);
    const wanted = String(state.cycle ?? pane.cycle ?? '');
    // A valid cycle leaves `note` ALONE rather than clearing it: openMap and
    // loadMap both call this, and the second call always sees the cycle the
    // first one chose, so clearing here would wipe the note the moment it was
    // earned. The reader clears it themselves by picking a cycle.
    if (wanted && cycles.includes(wanted)) return wanted;
    state.note = cycles.length
      ? `Showing ${cycleLabel(cycles[0])}; a map covers one Congress.`
      : 'This source records no cycle, so there is no Congress to draw.';
    return cycles[0] || '';
  }

  // A continuous ramp on a log scale, so $10,000 and $10,000,000 both read.
  // The five published stops sit at $1,000 .. $10,000,000+; the legend below
  // the map draws the same gradient and labels them from body.stops.
  const mixColour = (from, to, position) => {
    const parts = value => [1, 3, 5].map(at => parseInt(value.slice(at, at + 2), 16));
    const start = parts(from), end = parts(to);
    return '#' + start.map((value, index) => Math.round(value + (end[index] - value) * position)
      .toString(16).padStart(2, '0')).join('');
  };
  function heatColour(value) {
    if (!(value > 0)) return '';
    const span = MAP_RAMP.length - 1;
    const position = Math.min(1, Math.max(0, (Math.log10(value) - 2) / 5));
    const slot = Math.min(span - 1, Math.floor(position * span));
    return mixColour(MAP_RAMP[slot], MAP_RAMP[slot + 1], position * span - slot);
  }

  const isSenateKey = key => /-SEN$/.test(String(key || ''));
  const kindsOn = (body, off) => (body.kinds || []).filter(kind => !off.has(kind.kind));

  // Every visible row, folded onto the shape that carries it. A Senate row
  // keeps its CA-SEN key in the list and heats the state CA on the map.
  function heatFrom(body, off) {
    const districts = new Map(), states = new Map(), notes = new Map();
    const fold = (table, key, row, kind) => {
      table.set(key, (table.get(key) || 0) + (row.amount || 0));
      const lines = notes.get(key) || [];
      lines.push(`${kind.label} ${money(row.amount)} · ${number(row.rows)} rows · `
        + `${row.share == null ? 'Share unavailable' : (row.share * 100).toFixed(1) + '% of the kind'}`
        + (row.support ? ` · support ${money(row.support)}` : '')
        + (row.oppose ? ` · oppose ${money(row.oppose)}` : '')
        + (row.who ? ` · ${row.who}` : '') + ` · ${kind.basis}`);
      notes.set(key, lines);
    };
    for (const kind of kindsOn(body, off)) {
      for (const row of kind.districts || []) fold(isSenateKey(row.key) ? states : districts,
        isSenateKey(row.key) ? row.key.slice(0, 2) : row.key, row, kind);
      for (const row of kind.states || []) fold(states, row.key, row, kind);
    }
    return {districts, states, notes};
  }

  async function vintageFile(name) {
    if (!name) return null;
    if (vintages.has(name)) return vintages.get(name);
    // Static assets need no token (server.py do_GET gates only on the host).
    const response = await fetch('/geo/' + name);
    if (!response.ok) throw new Error(`The ${name} district boundaries could not be loaded.`);
    const body = await response.json();
    vintages.set(name, body);
    return body;
  }

  function mapSvgHtml(shapes, heat) {
    const state = [];
    const district = [];
    const outline = [];
    for (const [key, entry] of Object.entries(shapes.states || {})) {
      const value = heat.states.get(key) || 0;
      state.push(`<path class="map-shape" d="${escapeHtml(entry.path)}" data-key="${escapeHtml(key)}"`
        + ` data-fill="${escapeHtml(heatColour(value) || MAP_RAMP[0])}"></path>`);
      outline.push(`<path class="map-outline" d="${escapeHtml(entry.path)}" data-stroke="${MAP_OUTLINE}"></path>`);
    }
    for (const [key, entry] of Object.entries(shapes.districts || {})) {
      const value = heat.districts.get(key) || 0;
      district.push(`<path class="map-shape map-district" d="${escapeHtml(entry.path)}"`
        + ` data-key="${escapeHtml(key)}" data-district="${escapeHtml(key)}"`
        + ` data-fill="${escapeHtml(value > 0 ? heatColour(value) : 'none')}"></path>`);
    }
    return `<svg class="map-canvas" viewBox="0 0 ${shapes.width || 975} ${shapes.height || 610}"`
      + ` role="img" aria-label="Spending by congressional district" preserveAspectRatio="xMidYMid meet">`
      + `<g class="map-states">${state.join('')}</g>`
      + `<g class="map-districts">${district.join('')}</g>`
      + `<g class="map-outlines">${outline.join('')}</g></svg>`;
  }

  /* F6 (Paul, 2026-09-25): no dollar may count twice. A transfer a committee's
     summary reports on lines 22-23 but its saved Schedule B does not itemize has
     no known recipient, so the server keeps it out of every total - reported,
     located and unlocated alike - and the page lists it on its own line. */
  function notItemizedText(item) {
    // F12: a server that reads the saved network by date names its F6 figures' basis, and
    // the line says so (the caller hands the basis over on the item).
    const full = item.basis === 'full_period';
    return `Transfers reported, recipient not itemized · ${item.committee_name || item.committee}`
      + ` (${item.committee}, ${cycleLabel(item.cycle)}): ${money(item.summary_amount)} on the ${full ? 'full-period ' : ''}summary,`
      + ` ${money(item.itemized_amount)} itemized${full ? ' over the same period' : ''}. Not counted as spending.`;
  }

  /* F12 (Paul approved 2026-09-25): the Spent map of a saved network counts each reached
     committee from the date the network's money reached it. The server says so in
     date_rule (total_basis 'from_gates'; its source gap is on the full-period summary,
     residual_basis 'full_period'), and these three are the page's whole wording of it.
     A payload without date_rule (the raised side, an older server) keeps the old words. */
  function spentBasis(body) {
    return body.date_rule && body.date_rule.total_basis === 'from_gates'
      ? "saved network disbursements from the date the network's money reached each committee, excluding internal transfers"
      : 'saved network disbursements, excluding internal transfers';
  }

  function dateClauses(body) {
    const rule = body.date_rule;
    if (!rule) return '';
    return (Number(rule.amount_before_gate) ? ` · ${money(rule.amount_before_gate)} dated earlier is not counted` : '')
      + (Number(rule.amount_date_unverified) ? ` · ${money(rule.amount_date_unverified)} undated, counted and flagged` : '')
      + carryClause(rule, body.cycle);
  }

  /* Carryover (option (b), landed 2026-09-26), in the payload's own words: how many reached
     committees the previous cycle's transfers opened (date_rule.carryover.opened[].label says
     which transfers) and the status when the previous cycle is not, or not wholly, saved. */
  function carryClause(rule, cycle) {
    const carried = rule.carryover;
    if (!carried || typeof carried !== 'object') return '';
    const opened = (carried.opened || []).filter(entry => !cycle || String(entry.cycle) === String(cycle));
    return (opened.length ? ` · ${opened.length} counted from the cycle’s first day (${opened[0].label})` : '')
      + (carried.status === 'unknown' ? ' · carryover unknown: the previous cycle is not saved'
        : carried.status === 'partial' ? ' · carryover partial: some previous-cycle snapshots are not saved' : '');
  }

  function gapClause(body) {
    const full = body.date_rule && body.date_rule.residual_basis === 'full_period';
    return ` — unresolved source gap${full ? ' (full-period summary vs itemized rows)' : ''}: `
      + `${money(body.reconciliation_residual)}. The balancing residual is not verification.`;
  }

  /* Item 7 of F4 (Paul approved 2026-09-25). Shares are computed on positive
     located spending: the server names each base (share_base.places, or
     share_base.zips for a raised map's ZIP and state-district layers), and
     there is none while a placed row nets below zero. An unlocated line that
     nets below zero is a refund or reversal, not missing data (kind
     'reversal'): it is listed once, signed, as "Refunds and reversals",
     outside the shares, and no longer withdraws every share. A payload from a
     console before the server named its base keeps the old rule. */
  function shareBase(body, family) {
    body = body || {};
    if (body.share_base) {
      const base = body.share_base[family === 'zips' ? 'zips' : 'places'];
      return base == null ? null : Number(base);
    }
    const reversed = (body.kinds || []).some(kind => [...(kind.districts || []), ...(kind.states || [])].some(row => row.amount < 0))
      || (body.by_zip || []).some(row => row.amount < 0) || (body.unlocated || []).some(row => row.amount < 0);
    return body.reported_total > 0 && !reversed ? body.reported_total : null;
  }
  const isReversal = item => item.kind === 'reversal';
  function reversalLine(body) {
    const items = ((body || {}).unlocated || []).filter(isReversal);
    if (!items.length) return null;
    return {total: body.reversals_total != null ? Number(body.reversals_total) : items.reduce((sum, item) => sum + item.amount, 0),
      rows: items.reduce((sum, item) => sum + (item.rows || 0), 0),
      text: items.map(item => `${item.reason} (${money(item.amount)})`).join('; ')};
  }
  function shareRuleText(body, layer) {
    // A payload from a console not yet restarted onto evidence_geo v4 names no
    // base; its shares are of the reported total, so no v4 rule is stated for it.
    if (!body || !body.share_base) return '';
    const zips = layer === 'zip' || layer === 'sld';
    const base = shareBase(body, zips ? 'zips' : 'places');
    const reversal = reversalLine(body);
    const outside = reversal ? `Unlocated money and ${money(reversal.total)} of refunds and reversals are listed with the places, outside the shares.`
      : 'Unlocated money is listed with the places, outside the shares.';
    if (base == null) return `Shares are unavailable: nothing placed on this map nets above zero. ${outside}`;
    return `Shares are of the ${money(base)} of positive located ${body.direction === 'raised' ? 'receipts' : 'spending'}`
      + `${zips ? ' placed by ZIP' : ' on this map'}; a place that nets below zero is marked Reversal and has no share. ${outside}`;
  }

  function mapListHtml(body, off) {
    const base = shareBase(body, 'places');
    const groups = (body.kinds || []).map(kind => {
      const rows = [...(kind.districts || []), ...(kind.states || [])]
        .sort((first, second) => second.amount - first.amount);
      const share = base && kind.total >= 0 ? kind.total / base * 100 : null;
      const lines = rows.map(row => {
        const tone = row.oppose > 0 && !(row.support > 0) ? 'oppose' : (row.support > 0 ? 'support' : '');
        const label = isSenateKey(row.key) ? `${row.key.slice(0, 2)} · Senate` : row.key;
        return `<tr class="map-row" data-district="${escapeHtml(row.key)}">`
          + `<td><strong>${escapeHtml(label)}</strong>${row.split ? ' <span class="badge">split</span>' : ''}</td>`
          + `<td>${escapeHtml(row.who || '—')}</td>`
          + `<td class="${tone}">${escapeHtml(tone || kind.kind)}</td>`
          + `<td class="num">${money(row.amount)}</td>`
          + `<td class="num">${row.share == null ? (row.amount < 0 ? 'Reversal' : 'Unavailable') : (row.share * 100).toFixed(1) + '%'}</td></tr>`;
      }).join('');
      return `<tr class="group"><td colspan="3">${escapeHtml(kind.label)} · ${money(kind.total)} · `
        + `${escapeHtml(kind.basis)}${off.has(kind.kind) ? ' · off the heat' : ''}</td>`
        + `<td class="num">${money(kind.total)}</td><td class="num">${share == null ? (kind.total < 0 ? 'Reversal' : 'Unavailable') : share.toFixed(1) + '%'}</td></tr>${lines}`;
    }).join('');
    const unlocated = (body.unlocated || []).filter(item => !isReversal(item)).map(item =>
      `<tr class="map-unlocated"><td><strong>Unlocated</strong></td><td colspan="2">${escapeHtml(item.reason)}</td>`
      + `<td class="num">${money(item.amount)}</td><td class="num">${number(item.rows)} rows</td></tr>`).join('');
    const reversal = reversalLine(body);
    const reversals = reversal ? `<tr class="map-reversal"><td><strong>Refunds and reversals</strong></td><td colspan="2">outside the shares · ${escapeHtml(reversal.text)}</td>`
      + `<td class="num">${money(reversal.total)}</td><td class="num">${number(reversal.rows)} rows</td></tr>` : '';
    const notItemized = (body.transfers_not_itemized || []).map(item =>
      `<tr class="map-not-itemized"><td><strong>Not counted</strong></td><td colspan="2">${escapeHtml(notItemizedText({...item, basis: body.transfers_not_itemized_basis}))}</td>`
      + `<td class="num">${money(item.amount)}</td><td class="num">outside the total</td></tr>`).join('');
    return `<div class="table-scroll map-list"><table>
      <thead><tr><th>Where</th><th>To</th><th>Kind</th><th class="num">Amount</th><th class="num">Share</th></tr></thead>
      <tbody>${groups}${unlocated || ''}${reversals}${notItemized}</tbody></table></div>`;
  }

  /* The map view is a SKELETON built once per open, then updated region by
     region. It has to be: the <svg> the reader zoomed lives inside it, and the
     view this replaced rebuilt the whole thing - svg included - on every layer
     toggle and every click, which is exactly why no zoom could survive. */
  const MAP_SKELETON = `<nav class="map-trail" aria-label="Where you are"></nav>
    <div class="map-head"></div>
    <div class="map-toolbar-card"><div class="map-toolbar"></div></div>
    <div class="map-note-host"></div>
    <div class="map-grid">
      <section class="map-figure" aria-label="Map">
        <button type="button" class="map-enlarge" id="evidence-map-enlarge" aria-pressed="false"></button>
        <div class="map-compare-grid">
          <div class="map-compare-card map-compare-card--a"><span class="scope-pill scope-pill--1 map-cycle-pill" hidden></span><div class="map-canvas-host"></div></div>
          <div class="map-compare-card map-compare-card--b" hidden><span class="scope-pill scope-pill--2 map-cycle-pill"></span><div class="map-canvas-host map-canvas-host--b"></div></div>
        </div>
        <div class="map-compare-legend" hidden></div>
        <p class="muted map-share-rule" hidden></p>
        <details class="map-source-details"><summary class="map-source-summary">Sources &amp; geographic estimates</summary><p class="muted map-provenance" aria-live="polite"></p><p class="muted">Map controls: drag to pan; use + / − or Ctrl / ⌘ + scroll to zoom. Search for a state, district or ZIP and press Enter.</p></details>
        <p class="muted map-unplaced" hidden></p>
      </section>
      <section class="map-ranking" aria-label="Explore places">
        <section class="map-ranked" aria-label="Ranked list"></section>
        <details class="map-list-menu"><summary>Filter, sort and pin</summary>
        <div class="map-list-tools">
          <label>Filter list<input type="search" id="evidence-map-list-search" placeholder="Place / recipient"></label>
          <label>Sort<select id="evidence-map-list-sort"><option value="amount">Largest net amount</option>
            <option value="absolute">Largest magnitude</option><option value="ascending">Smallest / reversals</option>
            <option value="name">Name</option></select></label>
          <button type="button" class="secondary" id="evidence-map-pin">Pin selected place</button>
        </div>
        <p class="muted map-pin-hint">Select a place and pin it to compare up to four places, cycles, or directions. Pins keep their original scope.</p>
        </details>
      </section>
    </div>
    <section class="map-pins" aria-label="Pinned place comparisons" hidden></section>
    `;

  const MAP_LIST_PAGE = 40;
  function mapListPage(rows, state, size = MAP_LIST_PAGE) {
    const query = state.listQuery.trim().toLowerCase();
    const selected = rows.filter(row => !query || [row.name,row.key,row.kind,row.place].join(' ').toLowerCase().includes(query));
    selected.sort((a,b) => (state.listSort === 'name' ? String(a.name).localeCompare(String(b.name))
      : state.listSort === 'absolute' ? Math.abs(b.amount)-Math.abs(a.amount)
      : state.listSort === 'ascending' ? a.amount-b.amount : b.amount-a.amount) || String(a.key||a.name).localeCompare(String(b.key||b.name)));
    state.listPage = Math.max(0, Math.min(state.listPage, Math.ceil(selected.length/size)-1));
    const start=state.listPage*size;
    return {rows:selected.slice(start,start+size),start,total:selected.length};
  }
  function mapPager(page, state, size = MAP_LIST_PAGE) {
    // One page is no paging: the pager stays in the page (its buttons are
    // disabled), out of sight, as board 6 draws no pager.
    return `<div class="map-list-pager${page.total <= size ? ' single' : ''}"><span role="status">${number(page.total)} matches · ${page.total?page.start+1:0}–${Math.min(page.total,page.start+size)}</span>
      <button type="button" class="secondary" data-map-page="-1"${state.listPage===0?' disabled':''}>Previous</button>
      <button type="button" class="secondary" data-map-page="1"${page.start+size>=page.total?' disabled':''}>Next</button></div>`;
  }
  function mapPinsHtml(state) {
    if (!state.pins.length) return '';
    return `<h3>Pinned places</h3><div class="map-pin-grid">${state.pins.map((pin,index)=>`<article>
      <strong>${escapeHtml(pin.name)}</strong><p class="num">${money(pin.amount)}</p>
      <p class="muted">${escapeHtml(pin.cycle)} · ${escapeHtml(pin.direction)} · ${escapeHtml(pin.kind)}<br>${escapeHtml(pin.scope)}</p>
      <button type="button" class="link-button" data-map-unpin="${index}" aria-label="Remove ${escapeHtml(pin.name)}">Remove</button>
      </article>`).join('')}</div><p class="muted">Observed net amounts from this source, not donor-attributed spending. Overlapping places are not additive. Pins are retained snapshots; a refresh does not update them.</p>`;
  }

  /* Fix round 1 (R06-08): zoomed to a place, the place is the title, as
     approved board 7 draws it ("TX-19: Congressional District 19"), and the
     PAC's name moves to the overline, so the screen still says whose money it
     is. Comparing two cycles keeps the PAC as the title (M13). */
  function placeTitle(focus) {
    const key = String(focus.key || '');
    const district = key.match(/^([A-Z]{2})-(\d{2}|AL)$/);
    if (focus.kind === 'district' && district) {
      return `${key}: Congressional District ${district[2] === 'AL' ? '(at large)' : Number(district[2])}`;
    }
    if (/^[A-Z]{2}-SEN$/.test(key)) return `${key.slice(0, 2)} · Senate`;
    return focus.label || key;
  }
  /* MP11 (lane F2c, 2026-09-26): the notes above the map are one line - their first
     sentence - with the whole text under Details, as the boards set secondary
     information. Nothing is dropped: the network's scope, the missing snapshots, the
     date rule (F12), the carryover and the located/unlocated gap all stay, whole, in
     .map-message. A note of one short sentence stays a line of its own. */
  function mapMessageHtml(state) {
    const text = String(state.message || '').trim();
    const first = (text.match(/^[\s\S]*?[.!?](?=\s|$)/) || [text])[0];
    if (first.length >= text.length && text.length <= 160) return `<p class="muted map-message">${escapeHtml(text)}</p>`;
    return `<details class="map-message-details"${state.messageOpen ? ' open=""' : ''}><summary class="map-message-line">`
      + `<span class="map-message-summary">${escapeHtml(first)}</span><span class="map-message-more">Details</span></summary>`
      + `<p class="muted map-message">${escapeHtml(text)}</p></details>`;
  }
  const wiredMessages = new WeakSet();

  function mapHeadHtml(pane, state) {
    const place = Boolean(state.focus && !state.compare);
    const overline = place ? pane.name : `${pane.industry || 'Imported file'} · ${pane.sourceKind}`;
    // MP12 (#55): Spent drawn from the PAC's saved downstream network is titled "<PAC> network".
    const network = state.direction === 'spent' && Boolean(state.body) && state.body.direction !== 'raised'
      && state.body.spend_basis === 'consolidated_saved_network';
    const title = place ? placeTitle(state.focus) : pane.name + (network ? ' network' : '');
    return `<div class="pane-title">
        <div class="map-overline">${escapeHtml(overline)}</div>
        <h2 title="${escapeHtml(pane.name)} · ${escapeHtml(pane.committee)}">${escapeHtml(title)}</h2>
      </div>
      ${window.MentatMapEvidence && window.MentatMapStudio ? '<button type="button" class="secondary map-open-studio" id="evidence-map-open-studio" title="Hand these map layers to Chart Studio as marks cards">Open in Chart Studio</button>' : ''}
      <button type="button" class="secondary" id="evidence-map-back">Back to panes</button>`;
  }

  function sideButton(state, direction, label, note) {
    const sides = (state.body && state.body.sides) || {};
    const side = sides[direction] || {};
    const known = Boolean(state.body);
    const empty = known && !side.reported_total && !side.rows;
    const on = state.direction === direction;
    return `<button type="button" class="map-direction${on ? ' on' : ''}" data-direction="${direction}"
      aria-pressed="${on}"${empty && !on ? ' disabled' : ''}${empty && side.reason
        ? ` title="${escapeHtml(side.reason)}"` : ''}>${label}<small class="seg-note"> · ${note}</small></button>`;
  }

  function cycleSelectHtml(cycles, state) {
    // RULING A's "Showing <newest>" note rides on the cycle chip's title (and
    // on the screen-reader note under the toolbar), not on a line of its own.
    return `<label class="map-cycle-label" for="evidence-map-cycle"><span class="sr-only">Cycle</span>
        <select id="evidence-map-cycle"${state.note ? ` title="${escapeHtml(state.note)}"` : ''}>${cycles.map(value =>
          `<option value="${escapeHtml(value)}"${value === state.cycle ? ' selected' : ''}>${escapeHtml(cycleLabel(value))}</option>`).join('')}</select></label>`;
  }

  /* "Compare with [None]" on one map; "Compare [A] with [B]" on two. Only the
     source's own concrete cycles are offered (RULING A), never the one drawn. */
  function compareControlsHtml(cycles, state) {
    const others = cycles.filter(value => value !== state.cycle);
    if (!others.length && !state.compare) return '';
    // Fix round 1 (R06-04): with one map the picker is a single select whose
    // blank choice reads "Compare with…" (board 6 draws no compare control, so
    // it takes the least room the row allows); comparing, it is M13's phrase
    // and the blank choice reads None.
    const second = `<label class="map-compare-label" for="evidence-map-compare"><span class="sr-only">Compare with</span>
        <select id="evidence-map-compare"${state.compare ? '' : ' title="Compare this cycle with another"'}><option value=""${state.compare ? '' : ' selected'}>${state.compare ? 'None' : 'Compare with…'}</option>${others.map(value =>
          `<option value="${escapeHtml(value)}"${value === state.compare ? ' selected' : ''}>${escapeHtml(cycleLabel(value))}</option>`).join('')}</select></label>`;
    return state.compare
      ? `<div class="map-compare-controls on"><span class="map-compare-word">Compare</span>${cycleSelectHtml(cycles, state)}<span class="map-compare-word">with</span>${second}</div>`
      : `<div class="map-compare-controls">${second}</div>`;
  }

  const mapButtonIcon = name => `<svg viewBox="0 0 20 20" aria-hidden="true" class="map-button-icon" fill="none" stroke="currentColor" stroke-width="1.5">${name==='expand'
    ? '<path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4"/>'
    : name==='chevron' ? '<path d="M5 12.5l5-5 5 5"/>'
    : '<path d="M3 7h4V3m6 0v4h4M7 17v-4H3m10 4v-4h4"/>'}</svg>`;
  function mapToolbarHtml(pane, state) {
    const cycles = mapCycles(pane);
    return `<div class="map-context-controls"><div class="map-scope-controls">
      <div class="seg" role="group" aria-label="Direction">
        ${sideButton(state, 'raised', 'Raised', 'where it came from')}
        ${sideButton(state, 'spent', 'Spent', 'where it went')}</div>
      ${state.compare ? '' : cycleSelectHtml(cycles, state)}</div>
      <div class="map-layer-group"><span class="map-layer-caption" id="evidence-map-layer-caption">Layer</span>
        <div class="seg" role="group" aria-labelledby="evidence-map-layer-caption">
          ${MAP_LAYERS.map(layer => `<button type="button" class="map-layer${state.layer === layer.key ? ' on' : ''}"
            data-map-layer="${layer.key}" aria-pressed="${state.layer === layer.key}">${escapeHtml(layer.label)}</button>`).join('')}
        </div></div>
      <label class="map-mobile-layer">Map layer<select id="evidence-map-layer-select">
        ${MAP_LAYERS.map(layer=>`<option value="${layer.key}"${state.layer===layer.key?' selected':''}>${escapeHtml(layer.label)}</option>`).join('')}
      </select></label>
      ${compareControlsHtml(cycles, state)}
      ${state.layer === 'sld' ? `<div class="map-legislative-controls">
        <label class="map-geo-select">Chamber<select id="evidence-map-chamber">
        <option value="upper"${state.chamber==='upper'?' selected':''}>Upper / unicameral</option>
        <option value="lower"${state.chamber==='lower'?' selected':''}>Lower</option></select></label>
        <label class="map-geo-select">State<select id="evidence-map-state">
        ${Object.keys(sldIndex?.chambers?.upper?.states || {}).sort().map(code => `<option value="${code}"${state.sldState===code?' selected':''}>${code}</option>`).join('')}
        </select></label>
      </div>` : ''}</div>
      ${window.MentatMapEvidence ? `<button type="button" class="map-layers-button" id="evidence-map-layers" aria-haspopup="dialog" aria-expanded="false" aria-controls="evidence-map-control" title="Layer control: show, hide and add map layers"><svg viewBox="0 0 16 16" aria-hidden="true" class="map-layers-glyph"><path d="M8 2 1.5 5.5 8 9l6.5-3.5z"></path><path d="M1.5 8.2 8 11.7l6.5-3.5M1.5 10.9 8 14.4l6.5-3.5"></path></svg>Layers</button>` : ''}
      <div class="map-navigation-controls">
      <div class="map-search-controls"><label class="map-search" for="evidence-map-search"><span class="sr-only">Find a state, district or ZIP</span>
        <svg viewBox="0 0 16 16" aria-hidden="true" class="map-search-glyph"><circle cx="7" cy="7" r="5"></circle><line x1="10.6" y1="10.6" x2="15" y2="15"></line></svg>
        <input type="search" id="evidence-map-search" placeholder="Find a state, district or ZIP" aria-describedby="map-search-help"
          value="${escapeHtml(state.search)}"></label></div>
      <div class="map-view-controls"><div class="map-zoom-buttons" role="group" aria-label="Map zoom" title="Use + / − or Ctrl / ⌘ + scroll to zoom" aria-describedby="map-search-help">
        <button type="button" class="round" id="evidence-map-zoom-out" aria-label="Zoom out">−</button>
        <output class="map-zoom-level" aria-label="Zoom level">${(state.view?.scale || 1).toFixed(1)}×</output>
        <button type="button" class="round" id="evidence-map-zoom-in" aria-label="Zoom in">+</button>
      </div>
      <button type="button" class="secondary" id="evidence-map-reset">Reset view</button>
      <button type="button" class="secondary map-collapse" id="evidence-map-collapse" title="Collapse the map back into its pane">${mapButtonIcon('chevron')}<span class="map-collapse-label">Collapse</span></button></div></div>
      <p class="map-controls-help" id="map-search-help">Find a state, TX-19, or 79401, then press Enter. Drag to pan; use + / − or Ctrl / ⌘ + scroll to zoom.</p>`;
  }

  function renderMapToolbar(host, pane, state) {
    const active = document.activeElement, toolbar = host.querySelector('.map-toolbar');
    const id = toolbar.contains(active) ? active.id : '';
    const control = toolbar.contains(active) ? (id ? `#${id}` : active.dataset.mapLayer
      ? `[data-map-layer="${active.dataset.mapLayer}"]` : active.dataset.direction
        ? `[data-direction="${active.dataset.direction}"]` : '') : '';
    const searchValue = id === 'evidence-map-search' ? active.value : null;
    const selection = searchValue === null ? null : [active.selectionStart, active.selectionEnd];
    render(toolbar, mapToolbarHtml(pane, state));
    if (control && !active.isConnected) {
      const replacement = toolbar.querySelector(control);
      if (replacement) {
        if (searchValue !== null) replacement.value = searchValue;
        replacement.focus({preventScroll:true});
        if (selection) replacement.setSelectionRange(...selection);
      }
    }
  }

  const TRAIL_LABELS = {region: 'Region', state: 'State', district: 'District', zip: 'ZIP', sld: 'State district'};

  function trailHtml(state) {
    const focus = state.focus || (state.layer === 'sld' ? {kind:'state',key:state.sldState} : null);
    const steps = [{key: '', label: 'United States'}];
    if (focus) {
      if (focus.kind === 'region') steps.push({key: focus.key, label: focus.key});
      if (['state', 'district', 'zip', 'sld'].includes(focus.kind)) {
        const code = focus.kind === 'zip' ? ((state.body?.by_zip || []).find(row=>row.zip===focus.key)?.state || '') : String(focus.key).slice(0, 2);
        const row = ((state.body || {}).by_state || []).find(item => item.key === code);
        // Fix round 1 (R06-08): board 7's trail goes United States › Texas ›
        // TX-19; a region is a step only when it is the place chosen.
        if (row) steps.push({key: row.key, label: row.name || row.key, kind: 'state'});
        if (focus.kind !== 'state') steps.push({key: focus.key, label: focus.label || focus.key, kind: focus.kind});
      }
    }
    return `${state.focus ? '<button type="button" class="secondary map-back-step">← Back</button>' : ''}
      <ol class="map-crumbs">${steps.map((step, index) =>
        `<li>${index ? '<span aria-hidden="true">›</span>' : ''}<button type="button" class="link-button map-crumb"
          data-crumb-kind="${escapeHtml(step.kind || '')}" data-crumb="${escapeHtml(step.key)}">${escapeHtml(step.label)}</button></li>`).join('')}</ol>
      <span class="map-trail-layer sr-only">${escapeHtml(TRAIL_LABELS[state.layer] || '')}</span>`;
  }

  /* The ranked card. With nothing selected it is the places, ranked; with a
     shape selected it becomes this pane's own rows for that place - which is
     where a person's name is allowed to appear, and the only place. */
  function mapRankedHtml(pane, state) {
    const body = state.body;
    if (!body) {
      return `<div class="panel-heading"><h2>Loading</h2></div>
        <p class="muted map-loading">Loading ${state.direction === 'raised' ? 'Raised' : 'Spent'}
          for ${escapeHtml(cycleLabel(state.cycle))}…</p>`;
    }
    const raised = state.direction === 'raised';
    const reversal = reversalLine(body);
    const reconciliation = `${money(body.located_total)} located + ${money(reversal ? body.unlocated_total - reversal.total : body.unlocated_total)} unlocated`
      + (reversal ? ` - ${money(Math.abs(reversal.total))} refunds and reversals` : '') + ' = '
      + `${money(body.reported_total)} ${raised ? 'reported receipts' : body.spend_basis === 'consolidated_saved_network'
        ? spentBasis(body) : 'reported disbursements'}`
      + (raised ? '' : dateClauses(body))
      + (body.reconciles ? '' : gapClause(body))
      + (body.transfers_not_itemized_total ? ` · ${money(body.transfers_not_itemized_total)} of transfers reported with no itemized recipient is outside this total.` : '');
    if (state.focus) return mapFocusHtml(pane, state, reconciliation);
    const allRows = rankedRows(state);
    // Fix r2 (TC-14): board 6 lists the states with no pager and the list
    // scrolls inside the card, so the state and region layers (at most the
    // states, DC, the territories and five regions) come on one page; the
    // district and ZIP layers keep the 40-row pages.
    const pageSize = ['state', 'region'].includes(state.layer) ? Math.max(MAP_LIST_PAGE, allRows.length) : MAP_LIST_PAGE;
    const page = mapListPage(allRows, state, pageSize), rows = page.rows;
    const peak = Math.max(1, ...allRows.map(row => Math.abs(row.amount)));
    const placed = (body.kinds || []).reduce((total, kind) => total + (kind.rows || 0), 0);
    return `<div class="panel-heading"><h2>${escapeHtml(state.layer === 'sld' ? (raised ? 'ZIP estimates in ' + state.sldState : 'Boundary reference') : raised ? 'All receipts' : state.layer === 'district'
      ? 'House candidate spend' : 'All spend')}, ranked</h2>
        <span class="muted">${state.layer === 'sld' ? `${number(state.sldRows.reduce((sum,row)=>sum+row.rows,0))} rows mapped by ZIP internal point` : `${number(placed)} placed rows`}</span></div>
      <div class="table-scroll map-list"><table>
        <thead><tr><th class="map-rank-head"></th><th>Place</th><th class="num">Amount</th><th class="num">Share</th></tr></thead>
        <tbody>${rows.map((row, index) => `<tr class="map-row" data-focus-kind="${escapeHtml(row.kind)}"
          data-district="${escapeHtml(row.key)}" data-focus="${escapeHtml(row.key)}">
          <td class="map-rank">${page.start + index + 1}.</td>
          <td><button type="button" class="link-button">${escapeHtml(row.name)}</button>${row.split ? ' <span class="badge">split</span>' : ''}
            ${bar(row.amount, peak, true)}</td>
          <td class="num">${money(row.amount)}</td>
          <td class="num">${row.share == null ? (row.amount < 0 ? 'Reversal' : 'Unavailable') : (row.share * 100).toFixed(1) + '%'}</td></tr>`).join('')
          || '<tr><td colspan="4" class="muted">Nothing on this layer carries money.</td></tr>'}
        ${(body.unlocated || []).filter(item => !isReversal(item)).map(item => `<tr class="map-unlocated"><td></td>
          <td><strong>Unlocated</strong> <span class="muted">${escapeHtml(item.reason)}</span></td>
          <td class="num">${money(item.amount)}</td><td class="num">${number(item.rows)} rows</td></tr>`).join('')}
        ${reversal ? `<tr class="map-reversal"><td></td>
          <td><strong>Refunds and reversals</strong> <span class="muted">outside the shares · ${escapeHtml(reversal.text)}</span></td>
          <td class="num">${money(reversal.total)}</td><td class="num">${number(reversal.rows)} rows</td></tr>` : ''}
        ${(body.transfers_not_itemized || []).map(item => `<tr class="map-not-itemized"><td></td>
          <td><strong>Not counted</strong> <span class="muted">${escapeHtml(notItemizedText({...item, basis: body.transfers_not_itemized_basis}))}</span></td>
          <td class="num">${money(item.amount)}</td><td class="num">outside the total</td></tr>`).join('')}
        </tbody></table></div>
      ${mapPager(page,state,pageSize)}
      ${['zip','sld'].includes(state.layer)&&body.zip_truncated ? `<p class="muted">Source payload contains the largest ${number((body.by_zip||[]).length)} ZIPs. ${number(body.zip_truncated)} more ZIPs (${money(body.zip_truncated_amount)}) remain in totals, outside this list.</p>` : ''}
      <p class="muted map-reconcile" title="${escapeHtml(reconciliation)}">${reconcileFoot(body, reconciliation)}${
        raised && state.layer === 'district' ? ' · Contributor geography is by ZIP, approximate, and a ZIP that straddles districts is split by its land-area shares and marked “split”.' : ''}</p>
      ${raised && state.layer !== 'sld' ? geocodeHtml(pane, state) : ''}`;
  }

  /* Fix r2 (TC-07): the ranked card's foot reads as boards 6 and M13 draw it,
     "located $X · unlocated $Y" (the compare foot already does). The whole
     reconciliation, with the reported total, is on the line's title and in
     screen-reader text; an unresolved source gap stays on screen. */
  function reconcileFoot(body, reconciliation) {
    const reversal = reversalLine(body);
    return `<span class="map-reconcile-figures" aria-hidden="true">located ${money(body.located_total)} · unlocated ${money(reversal ? body.unlocated_total - reversal.total : body.unlocated_total)}${
      reversal ? ` · refunds and reversals ${money(reversal.total)}` : ''}${
      body.reconciles ? '' : gapClause(body)}</span>`
      + `<span class="sr-only map-reconcile-sentence">${escapeHtml(reconciliation)}</span>`;
  }

  /* Build 06 (board M13): places on the active layer ranked by the absolute
     change between the two cycles, from each cycle's own server folds (the
     same rankedRows the one-map list uses). The first cycle is "Compare [A]",
     so a change is A minus B: up in teal, down in --chart-negative. */
  const MAP_CHANGES = 8;
  const LAYER_WORDS = {region: 'region', state: 'state', district: 'district',
                       sld: 'state district', zip: 'ZIP code'};
  function mapChangeRows(state) {
    const first = rankedRows(state);
    const second = rankedRows({...state, body: state.compareBody, sldRows: state.compareSldRows || []});
    const rows = new Map();
    for (const row of first) rows.set(row.key, {key: row.key, kind: row.kind, name: row.name, a: row.amount || 0, b: 0});
    for (const row of second) {
      const found = rows.get(row.key);
      if (found) found.b += row.amount || 0;
      else rows.set(row.key, {key: row.key, kind: row.kind, name: row.name, a: 0, b: row.amount || 0});
    }
    return [...rows.values()].map(row => ({...row, change: row.a - row.b}))
      .filter(row => row.change !== 0)
      .sort((x, y) => Math.abs(y.change) - Math.abs(x.change) || String(x.name).localeCompare(String(y.name)));
  }
  function mapChangesHtml(pane, state) {
    const title = `Biggest changes by ${LAYER_WORDS[state.layer] || 'place'}`;
    if (!state.body || !state.compareBody) {
      return `<div class="panel-heading"><h2>${escapeHtml(title)}</h2></div>
        <p class="muted map-loading">${escapeHtml(state.compareMessage
          || `Loading ${cycleLabel(state.compare)} to compare with ${cycleLabel(state.cycle)}…`)}</p>`;
    }
    const all = mapChangeRows(state), rows = all.slice(0, MAP_CHANGES);
    const peak = Math.max(1, ...rows.flatMap(row => [Math.abs(row.a), Math.abs(row.b)]));
    const chosen = state.focus ? state.focus.key : '';
    const signed = value => (value > 0 ? '+' : '') + money(value);
    const foot = (label, body) => `${cycleLabel(label)}: located ${money(body.located_total)} · unlocated ${money(body.unlocated_total)}`;
    const count = all.length > rows.length ? `${number(rows.length)} of ${number(all.length)} places that changed` : '';
    return `<div class="panel-heading"><h2${count ? ` title="${escapeHtml(count)}"` : ''}>${escapeHtml(title)}</h2></div>
      <ol class="map-changes">${rows.map((row, index) => `<li class="map-change${row.key === chosen ? ' chosen' : ''}"
          data-focus-kind="${escapeHtml(row.kind)}" data-focus="${escapeHtml(row.key)}">
        <span class="map-change-rank">${index + 1}.</span>
        <div class="map-change-body"><button type="button" class="link-button map-change-name"
            title="${escapeHtml(`${row.name}: ${cycleLabel(state.cycle)} ${money(row.a)} · ${cycleLabel(state.compare)} ${money(row.b)}`)}">${escapeHtml(row.name)}</button>
          <div class="map-change-bars" aria-hidden="true"><div class="bartrack"><div class="bar change-a" data-bar-width="${barWidth(row.a, peak)}"></div></div>
            <div class="bartrack"><div class="bar change-b" data-bar-width="${barWidth(row.b, peak)}"></div></div></div></div>
        <span class="map-change-value ${row.change > 0 ? 'up' : 'down'}">${escapeHtml(signed(row.change))}</span></li>`).join('')
        || '<li class="map-change-none muted">No place on this layer changed between the two cycles.</li>'}</ol>
      ${count ? `<p class="map-change-count sr-only">${escapeHtml(count)}</p>` : ''}
      <p class="map-change-foot" title="${escapeHtml(`${foot(state.cycle, state.body)} · ${foot(state.compare, state.compareBody)}`)}"><span class="map-change-foot-cycle">${escapeHtml(cycleLabel(state.cycle))}</span> located ${money(state.body.located_total)} · unlocated ${money(state.body.unlocated_total)}</p>`;
  }

  /* One ranked list per layer, all of it from the server's own folds. */
  function rankedRows(state) {
    const body = state.body || {};
    const off = state.off || new Set();
    const subtract = row => {
      if (!off.size) return row.amount || 0;
      let total = 0;
      for (const [kind, value] of Object.entries(row.kinds || {})) if (!off.has(kind)) total += value;
      return total;
    };
    // Item 7 of F4: on positive located spending (shareBase, above); a refund
    // among the unlocated lines, or a place that nets below zero, no longer
    // withdraws the shares - the place just has none (the list says Reversal).
    const base = shareBase(body, ['zip', 'sld'].includes(state.layer) ? 'zips' : 'places');
    const share = amount => base > 0 && amount >= 0 && amount <= base ? amount / base : null;
    if (state.layer === 'sld') return (state.sldRows || []).map(row => ({...row, share: share(row.amount)}));
    if (state.layer === 'region') {
      return (body.by_region || []).map(row => ({kind: 'region', key: row.key, name: row.name || row.key,
        amount: subtract(row), share: share(subtract(row))}))
        .filter(row => row.amount).sort((first, second) => second.amount - first.amount);
    }
    if (state.layer === 'state') {
      return (body.by_state || []).map(row => ({kind: 'state', key: row.key, name: row.name || row.key,
        amount: subtract(row), share: share(subtract(row))}))
        .filter(row => row.amount).sort((first, second) => second.amount - first.amount);
    }
    if (state.layer === 'zip') {
      if (off.has('contributors')) return [];
      return (body.by_zip || []).map(row => ({kind: 'zip', key: row.zip,
        name: `${row.zip}${row.city ? ' · ' + row.city : ''}${row.state ? ', ' + row.state : ''}`,
        amount: row.amount || 0, share: share(row.amount || 0)}))
        .sort((first, second) => second.amount - first.amount);
    }
    const rows = [];
    for (const kind of (body.kinds || [])) {
      if (off.has(kind.kind)) continue;
      if (state.direction === 'spent' && kind.kind !== 'candidates') continue;
      for (const row of (kind.districts || [])) {
        if (state.direction === 'spent' && isSenateKey(row.key)) continue;
        rows.push({kind: isSenateKey(row.key) ? 'state' : 'district', key: row.key,
                   name: isSenateKey(row.key) ? `${row.key.slice(0, 2)} · Senate` : row.key,
                   amount: row.amount || 0, split: row.split, share: share(row.amount || 0)});
      }
    }
    return rows.sort((first, second) => second.amount - first.amount);
  }

  function mapFocusHtml(pane, state, reconciliation) {
    if (state.direction === 'spent') {
      const body = state.body || {}, focus = state.focus;
      const region = (body.by_region || []).find(row => row.key === focus.key);
      const states = new Set(focus.kind === 'region' ? (region?.states || []) : [focus.key]);
      const entries = [];
      for (const kind of kindsOn(body, state.off || new Set())) {
        if (state.layer === 'district' && kind.kind !== 'candidates') continue;
        for (const row of [...(kind.districts || []), ...(kind.states || [])]) {
          if (state.layer === 'district' && isSenateKey(row.key)) continue;
          if (!(focus.kind === 'district' ? row.key === focus.key : states.has(row.key.slice(0, 2)))) continue;
          for (const recipient of row.recipients || [{name: row.who || row.key, amount:row.amount}]) {
            entries.push({...recipient, kind:kind.label, place:row.key, filing_fallback:body.filing_fallback});
          }
        }
      }
      const page = mapListPage(entries,state);
      return `<div class="panel-heading"><h2>Spend in ${escapeHtml(focus.label || focus.key)}</h2>
        <button type="button" class="link-button map-clear-focus">Clear</button></div>
        <div class="table-scroll map-list"><table><thead><tr><th>Recipient / spending PAC</th><th>Kind</th><th>Place</th><th class="num">Amount</th></tr></thead>
        <tbody>${page.rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.kind)}</td>
          <td>${escapeHtml(row.place)}</td><td class="num map-cited-amount"><span class="map-amount-value">${money(row.amount)}</span>${filingCitations(row)}</td></tr>`).join('')
          || '<tr><td colspan="4">No spending on this layer.</td></tr>'}</tbody></table></div>
        ${mapPager(page,state)}<p class="muted map-reconcile">${escapeHtml(reconciliation)}</p>`;
    }
    if (pane.source === 'snapshot') {
      const body = state.body || {}, focus = state.focus;
      const region = (body.by_region || []).find(row => row.key === focus.key);
      const states = new Set(focus.kind === 'region' ? (region?.states || []) : [focus.key]);
      const records = new Map();
      for (const kind of kindsOn(body, state.off || new Set())) {
        for (const place of [...(kind.districts || []), ...(kind.states || [])]) {
          if (focus.kind === 'district' && place.key !== focus.key) continue;
          if (['state', 'region'].includes(focus.kind) && !states.has(place.key.slice(0, 2))) continue;
          for (const recipient of place.recipients || []) for (const record of recipient.filings || []) {
            if (focus.kind === 'zip' && String(record.zip).slice(0, 5) !== focus.key) continue;
            const raw = {contributor_city:record.city, contributor_state:record.state,
              contributor_zip:record.zip, contributor_employer:record.employer};
            const item = {...record, name:record.counterparty || recipient.name, raw,
              place:[record.city,record.state,record.zip,record.employer].filter(Boolean).join(' ')};
            if (focus.kind === 'sld' && !rowsInFocus({...pane,focus}, [item]).length) continue;
            const id = record.sub_id || JSON.stringify([record.source_url,record.transaction_id,record.date,record.amount,item.name]);
            records.set(id, item);
          }
        }
      }
      const page = mapListPage([...records.values()], state);
      return `<div class="panel-heading"><h2>Donations in ${escapeHtml(focus.label || focus.key)}</h2>
        <button type="button" class="link-button map-clear-focus">Clear</button></div>
        <div class="table-scroll map-list"><table class="map-donors"><thead><tr><th>Contributor</th><th>Date / place</th><th class="num">Amount / record</th></tr></thead>
        <tbody>${page.rows.map(row => `<tr><td><strong>${escapeHtml(row.name || NOT_REPORTED)}</strong><small>${escapeHtml(row.employer)}</small></td>
          <td>${escapeHtml(row.date)}<small>${escapeHtml(rowPlace(row))}</small></td>
          <td class="num">${currency(row.amount)}${filingCite(row)}</td></tr>`).join('')
          || '<tr><td colspan="3">No recorded donations in this place.</td></tr>'}</tbody></table></div>
        ${mapPager(page,state)}<p class="muted map-reconcile">Original receipt amounts. Split-ZIP map estimates may allocate part of a receipt to this place. ${escapeHtml(reconciliation)}</p>`;
    }
    const source = state.sourceRows;
    const focused = source ? rowsInFocus({...pane,focus:state.focus}, source.rows || []) : null;
    const rows = focused && focused.filter(row=>!state.listQuery || [row.counterparty,rowPlace(row),rowField(row,['employer','contributor_employer'])].join(' ').toLowerCase().includes(state.listQuery.toLowerCase()));
    const label = state.focus.label || state.focus.key;
    return `<div class="panel-heading"><h2>${escapeHtml(pane.source === 'csv' && state.direction === 'raised'
        ? 'Donors in ' + label : 'Rows in ' + label)}</h2>
        <button type="button" class="link-button map-clear-focus">Clear</button></div>
      ${rows ? `<div class="table-scroll map-list"><table class="map-donors map-donor-lines">
        <thead><tr><th>Contributor</th><th>City · ZIP</th><th>Employer</th><th class="num">Amount</th></tr></thead>
        <tbody>${rows.map(row => {
          // One line per donor (board 7, fix r2 TC-01): a cell that does not fit
          // ends in an ellipsis and carries its whole text on its title.
          const name = row.counterparty || NOT_REPORTED, place = rowPlace(row);
          const employer = rowField(row, ['employer', 'contributor_employer']);
          return `<tr><td title="${escapeHtml(name)}"><strong>${escapeHtml(name)}</strong></td>
          <td class="muted" title="${escapeHtml(place)}">${escapeHtml(place)}</td>
          <td class="muted" title="${escapeHtml(employer)}">${escapeHtml(employer)}</td>
          <td class="num">${row.amount == null ? 'Not reported' : currency(row.amount)}${filingCite(row)}</td></tr>`;
        }).join('')
          || '<tr><td colspan="4" class="muted">No row on this page of the file falls in this place.</td></tr>'}
        </tbody></table></div>
        <div class="map-list-pager"><span>Source page ${Math.floor(state.sourceOffset/PAGE_SIZE)+1} · cycle ${escapeHtml(state.cycle)}</span>
          <button type="button" class="secondary" data-map-source-page="-1"${state.sourceOffset===0?' disabled':''}>Previous source page</button>
          <button type="button" class="secondary" data-map-source-page="1"${!source.has_more?' disabled':''}>Next source page</button></div>
        <p class="muted map-reconcile">${number(rows.length)} matches on this ${number((source.rows || []).length)}-row source page, not all donors in this place.
          Source has ${number(source.total_rows)} rows in this cycle. · ${escapeHtml(reconciliation)}</p>`
        : `<p class="muted map-loading">${escapeHtml(state.sourceError || (pane.source==='csv'?'Reading this file’s rows…':'Individual receipt rows are not available from this saved snapshot.'))}</p>`}
      ${pane.source === 'csv' && state.direction === 'raised' && state.layer !== 'sld' ? geocodeHtml(pane, state) : ''}`;
  }

  const rowField = (row, names) => {
    const raw = row.raw || {};
    const key = Object.keys(raw).find(name => names.some(wanted => name.toLowerCase().includes(wanted)));
    return key ? String(raw[key] || '') : '';
  };
  const rowPlace = row => {
    const city = rowField(row, ['city']);
    const state = rowField(row, ['state', ' st']);
    const zip = rowField(row, ['zip', 'postal']);
    return [[city, state].filter(Boolean).join(', '), zip].filter(Boolean).join(' · ');
  };

  function mapHtml(pane, state) {
    const body = state.body;
    const cycles = mapCycles(pane);
    const off = state.off || new Set();
    const raised = state.direction === 'raised';
    const reconciliation = body
      ? `${money(body.located_total)} located + ${money(body.unlocated_total)} unlocated = `
        + `${money(body.reported_total)} ${raised ? 'reported receipts' : body.spend_basis === 'consolidated_saved_network' ? 'saved network net outflow' : 'reported disbursements'}`
        + (body.reconciles ? '' : gapClause(body))
      : '';
    return `<div class="map-head">
      <div class="pane-title">
        <div class="pane-kind">${escapeHtml(pane.industry || 'Imported file')} · ${escapeHtml(pane.sourceKind)}</div>
        <h2 title="${escapeHtml(pane.name)}">${escapeHtml(shortName(pane.name))}
          <span class="muted">${escapeHtml(pane.committee)}</span></h2>
      </div>
      <div class="map-controls">
        <div class="seg" role="group" aria-label="Direction">
          <button type="button" class="map-direction${raised ? ' on' : ''}" data-direction="raised"
            aria-pressed="${raised}">Raised · where it came from</button>
          <button type="button" class="map-direction${raised ? '' : ' on'}" data-direction="spent"
            aria-pressed="${!raised}">Spent · where it went</button></div>
        <label class="map-cycle-label" for="evidence-map-cycle">Cycle
          <select id="evidence-map-cycle">${cycles.map(value =>
            `<option value="${escapeHtml(value)}"${value === state.cycle ? ' selected' : ''}>${escapeHtml(cycleLabel(value))}</option>`).join('')}</select></label>
        <button type="button" class="secondary" id="evidence-map-back">Back to panes</button>
      </div></div>
    ${state.note ? `<p class="muted map-note">${escapeHtml(state.note)}</p>` : ''}
    ${state.message ? `<p class="muted map-message">${escapeHtml(state.message)}</p>` : ''}
    ${body ? `<div class="map-layers">
      <span class="muted">${escapeHtml(raised ? 'All receipts' : 'All spend')}, ${escapeHtml(cycleLabel(body.cycle))}:
        <strong>${money(body.reported_total)}</strong> ${body.spend_basis === 'consolidated_saved_network' ? 'saved network net outflow' : 'as reported'} · located ${money(body.located_total)} ·
        unlocated ${money(body.unlocated_total)}</span>
      ${(body.kinds || []).map((kind, index) => `<button type="button" class="layer${off.has(kind.kind) ? ' off' : ''}"
        data-layer="${escapeHtml(kind.kind)}" aria-pressed="${!off.has(kind.kind)}">
        <span class="dot" data-fill="${MAP_RAMP[Math.min(MAP_RAMP.length - 1, index + 1)]}"></span>
        ${escapeHtml(kind.label)} · ${money(kind.total)}${off.has(kind.kind) ? ' · off' : ''}</button>`).join('')}
    </div>
    <div class="map-grid">
      <section class="map-figure" aria-label="Spend heatmap">
        <div class="panel-heading"><h2>${escapeHtml(raised ? 'Receipts' : 'Spend')} heatmap ·
          by congressional district and state</h2>
          <span class="muted">Hover for amount, share and kind · click a district to filter this pane&rsquo;s rows</span></div>
        <div class="map-canvas-host" data-district-filter="${escapeHtml(state.district)}"></div>
        <div class="map-legend"><span class="muted">Heat</span><div class="map-ramp"></div>
          <span class="muted">${(body.stops || []).map(stop => money(stop)).join(' · ')}+</span>
          <span class="muted map-basis">${escapeHtml(body.congress ? body.congress + ' boundaries' : '')}${
            body.zip_basis ? ' · ZIP shares by ' + escapeHtml(body.zip_basis) : ''}</span></div>
      </section>
      <section class="map-ranked" aria-label="Ranked list">
        <div class="panel-heading"><h2>${escapeHtml(raised ? 'All receipts' : 'All spend')}, ranked</h2>
          <span class="muted">${number((body.kinds || []).reduce((total, kind) => total + (kind.rows || 0), 0))} placed rows</span></div>
        ${mapListHtml(body, off)}
        <p class="muted map-reconcile">${escapeHtml(reconciliation)}${
          raised && state.layer === 'district' ? ' · Contributor geography is by ZIP, approximate, and a ZIP that straddles districts is split by its land-area shares and marked “split”.' : ''}</p>
        ${raised ? geocodeHtml(pane, state) : ''}
      </section>
    </div>` : '<p class="muted">Loading this map…</p>'}`;
  }

  function paintMap(host, shapes, heat) {
    for (const node of host.querySelectorAll('[data-fill]')) {
      // CSP style-src 'self': a style attribute written through innerHTML is
      // blocked, so every fill is set through the CSSOM, as graph.js does.
      node.style.fill = node.dataset.fill;
    }
    for (const node of host.querySelectorAll('[data-stroke]')) {
      node.style.fill = 'none';
      node.style.stroke = node.dataset.stroke;
    }
    for (const node of host.querySelectorAll('.map-shape')) {
      const lines = heat.notes.get(node.dataset.key);
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = lines ? `${node.dataset.key}\n${lines.join('\n')}`
        : `${node.dataset.key}\nNo spending on this map`;
      node.appendChild(title);
    }
    void shapes;
  }

  /* The precise layer (S8b, ruling 10). One line under the Raised list, a
     Start, and while it runs a progress line and a Stop. Nothing here is
     automatic: contributor street addresses leave this machine only when the
     reader presses Start, and the sentence beside it says exactly that and
     exactly which service is called. A saved snapshot has no import to
     geocode, so it gets the footnote and no button. */
  const GEOCODE_OFFER = 'Contributor geography is by ZIP. Geocode street addresses for exact districts.';
  const GEOCODE_PRIVACY = 'Start sends the contributor street address, city, state and ZIP of this '
    + 'import to the US Census Geocoder (geocoding.geo.census.gov). No name, amount, employer or '
    + 'occupation is sent.';

  function geocodeFootnote(body) {
    const matched = Number(body.geocoded_rows || 0);
    const total = matched + Number(body.zip_rows || 0) + Number(body.pending_rows || 0);
    return matched > 0
      ? `${number(matched)} of ${number(total)} rows matched to a street address; the rest stay by ZIP`
      : GEOCODE_OFFER;
  }

  /* Fix r2 (TC-07): with nothing to geocode, the ZIP-centre sentence is source
     detail, so it sits in the map's "Source details" disclosure (see renderMap)
     rather than under the ranked card's foot; an offer to geocode stays here. */
  const GEOCODE_BASIS = 'Contributor locations use ZIP centres. No pending street addresses are available for geocoding.';
  const geocodeIsBasis = (pane, body) => pane.source !== 'csv' || !(Number(body.pending_rows) > 0 || Number(body.geocoded_rows) > 0);
  function geocodeBasisHtml(pane, state) {
    const body = state.body;
    if (!body || state.compare || state.direction !== 'raised' || state.layer === 'sld' || !geocodeIsBasis(pane, body)) return '';
    return `<br><span class="map-geocode-note">${escapeHtml(GEOCODE_BASIS)}</span>`;
  }
  function geocodeHtml(pane, state) {
    const body = state.body;
    if (!body) return '';
    if (geocodeIsBasis(pane, body)) return '';
    const job = state.geocode || {};
    const running = job.state === 'running';
    const pending = Number(body.pending_rows || 0);
    // The server names the host it would actually call, so this sentence can
    // never claim the Census about a console pointed somewhere else.
    const privacy = job.privacy || GEOCODE_PRIVACY;
    return `<div class="map-geocode">
      <p class="map-geocode-offer">${escapeHtml(geocodeFootnote(body))}</p>
      <p class="muted map-geocode-privacy">${escapeHtml(privacy)}</p>
      <div class="map-geocode-actions">
        <button type="button" class="secondary" id="evidence-map-geocode-start"${
          running || collectorReadOnly || !pending ? ' disabled' : ''}>Start</button>
        ${running ? '<button type="button" class="secondary" id="evidence-map-geocode-stop">Stop</button>' : ''}
        ${collectorReadOnly ? '<span class="muted">This preview is read-only; start geocoding from the console that owns these imports.</span>' : ''}
      </div>
      ${job.progress || state.geocodeMessage
        ? `<p class="muted map-geocode-progress">${escapeHtml(state.geocodeMessage || job.progress)}</p>`
        : ''}</div>`;
  }

  async function readGeocode(key) {
    const pane = panes.get(key);
    const state = mapStateFor(key);
    if (!pane || pane.source !== 'csv') return null;
    try {
      state.geocode = await api('/api/evidence-geocode?' + new URLSearchParams({id: pane.id}));
    } catch (error) {
      state.geocode = null;
      state.geocodeMessage = error.message;
    }
    return state.geocode;
  }

  function pollGeocode(key) {
    const state = mapStateFor(key);
    clearTimeout(state.geocodeTimer);
    state.geocodeTimer = setTimeout(async () => {
      if (mapPane !== key) return;
      const job = await readGeocode(key);
      if (job && job.state === 'running') { await renderMap(); pollGeocode(key); return; }
      // Finished, stopped or failed: the mix has changed, so the map is read
      // again rather than left showing the counts from before the run.
      state.geocodeMessage = '';
      await loadMap();
    }, POLL_DELAY);
  }

  async function startGeocode() {
    const key = mapPane;
    const pane = panes.get(key);
    const state = mapStateFor(key);
    if (!pane || pane.source !== 'csv') return;
    state.geocodeMessage = 'Starting the geocoding job\u2026';
    await renderMap();
    try {
      state.geocode = await api('/api/evidence-geocode',
        {id: pane.id, committee: pane.committee, cycle: state.cycle || '', action: 'start'});
      state.geocodeMessage = '';
    } catch (error) {
      state.geocodeMessage = error.message;
      await renderMap();
      return;
    }
    await renderMap();
    pollGeocode(key);
  }

  async function stopGeocode() {
    const key = mapPane;
    const pane = panes.get(key);
    const state = mapStateFor(key);
    if (!pane) return;
    try {
      state.geocode = await api('/api/evidence-geocode', {id: pane.id, action: 'stop'});
    } catch (error) {
      state.geocodeMessage = error.message;
    }
    await renderMap();
  }

  /* Fix round 1 (R06-05): the kind toggles are a group in the map's legend
     card (evidence-map.js legendCard), not a line between the toolbar and the
     map; the reconciliation they sat beside is the ranked card's foot. */
  const kindToggles = state => (state.body?.kinds || []).map((kind, index) => ({
    kind: kind.kind, label: kind.label, total: kind.total, off: (state.off || new Set()).has(kind.kind),
    colour: MAP_RAMP[Math.min(MAP_RAMP.length - 1, index + 1)]}));

  function mapProvenance(state, shapes, points) {
    const sourceLink = (url, label) => /^https:\/\//.test(url || '')
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>` : escapeHtml(label);
    if (state.layer === 'sld') {
      const chamber = sldIndex?.chambers?.[state.chamber];
      const missing = !chamber?.states?.[state.sldState];
      const mapped = state.sldRows.reduce((sum,row)=>sum+row.amount,0);
      return `${sourceLink(chamber?.source, 'Census 2024 state legislative boundaries')} · ${escapeHtml(state.sldState)} · ${state.chamber} chamber.
        ${missing ? 'No lower chamber is supplied for this state (Nebraska is unicameral; DC has Council wards only).' : ''}
        ${state.direction === 'spent' ? 'Boundary reference only: this source has no state-legislative spending allocation.'
          : `ZIP internal-point estimate: ${money(mapped)} mapped in this state/chamber. ${escapeHtml(sldIndex?.basis || '')}.
          These are 2024 reference boundaries, including when viewing another cycle; not verified donor addresses or historical district assignments.
          ZIPs without a district, missing ZIPs and truncated ZIPs remain in source totals; no amount is inferred for them.`}`;
    }
    if (state.layer === 'zip') return `${sourceLink(points?.build?.source, 'Census 2024 ZIP/ZCTA internal points')}.
      ${escapeHtml(points?.build?.point || 'Approximate ZIP locations, not donor addresses')}.
      ${state.direction === 'spent' ? 'No contributor ZIP spending allocation is available.' : 'Nearby ZIPs cluster at this zoom; click selects the largest ZIP in a cluster. Circle size sums contributor counts across its ZIPs; heat is the largest ZIP’s net amount.'}`;
    return `${sourceLink(shapes.source, 'Census congressional boundaries')} · Congress ${escapeHtml(shapes.vintage || '')}.
      ${sourceLink(shapes.state_source, 'Census state boundaries')}.
      ${state.direction === 'raised' ? escapeHtml(state.body.zip_basis || 'Contributor ZIP geography is approximate.') : 'Spending retains the geographic basis of each source kind.'}`;
  }

  /* The skeleton is written ONCE. Everything after that is a region update, so
     the <svg> the reader is zoomed into is never replaced by a layer toggle, a
     cycle change, a geocode tick or a selection. */
  async function renderMap() {
    const ticket=++mapRenderTicket, paneKey=mapPane;
    const host = $('evidence-map');
    const pane = panes.get(mapPane);
    if (!pane) { host.hidden = true; return; }
    const state = mapStateFor(mapPane);
    host.hidden = false;
    if (host.dataset.skeleton !== '1') { host.innerHTML = MAP_SKELETON; host.dataset.skeleton = '1'; }
    host.classList.toggle('enlarged', Boolean(state.enlarged));
    // Enlarge / Shrink is an icon on the map card (fix round 1, R06-04 and
    // R06-14). Board 6's "Collapse" is the toolbar's (r5, audit men-b F2):
    // it folds the map back into its pane, so this icon's other state is
    // "Shrink" and no two controls share a name.
    const enlarge = host.querySelector('#evidence-map-enlarge');
    const enlargeLabel = state.enlarged ? 'Shrink the map' : 'Enlarge the map';
    enlarge.setAttribute('aria-pressed', String(Boolean(state.enlarged)));
    enlarge.setAttribute('aria-label', enlargeLabel);
    enlarge.title = enlargeLabel;
    render(enlarge, mapButtonIcon(state.enlarged ? 'collapse' : 'expand'));
    // Build 06: comparing shows the second map card and a cycle pill on each.
    host.classList.toggle('comparing', Boolean(state.compare));
    const pills = host.querySelectorAll('.map-cycle-pill');
    host.querySelector('.map-compare-card--b').hidden = !state.compare;
    pills[0].hidden = !state.compare;
    pills[0].textContent = cycleLabel(state.cycle);
    pills[1].textContent = state.compare ? cycleLabel(state.compare) : '';
    if (!state.compare) { host.querySelector('.map-compare-legend').hidden = true; host.querySelector('.map-compare-legend').textContent = ''; }
    $('view-evidence').classList.add('map-open');
    render(host.querySelector('.map-head'), mapHeadHtml(pane, state));
    renderMapToolbar(host, pane, state);
    // RULING A's note ("Showing 2025-2026; a map covers one Congress.") is
    // said once, to screen readers, and on the cycle chip's title; boards 6,
    // 7 and M13 draw no line between the toolbar and the map (R06-05).
    render(host.querySelector('.map-note-host'),
      (state.note ? `<p class="map-note sr-only">${escapeHtml(state.note)}</p>` : '')
      + (state.message ? mapMessageHtml(state) : '')
      + (state.searchMessage ? `<p class="muted map-search-message">${escapeHtml(state.searchMessage)}</p>` : ''));
    const messageDetails = host.querySelector('.map-message-details');
    if (messageDetails && !wiredMessages.has(messageDetails)) {
      wiredMessages.add(messageDetails);
      messageDetails.addEventListener('toggle', () => { const shown = panes.has(mapPane) ? mapStateFor(mapPane) : null; if (shown) shown.messageOpen = messageDetails.open; });
    }
    render(host.querySelector('.map-trail'), trailHtml(state));
    render(host.querySelector('.map-ranked'), state.compare ? mapChangesHtml(pane, state) : mapRankedHtml(pane, state));
    render(host.querySelector('.map-pins'),mapPinsHtml(state));
    host.querySelector('.map-pins').hidden = !state.pins.length;
    const listSearch=host.querySelector('#evidence-map-list-search');
    if(document.activeElement!==listSearch)listSearch.value=state.listQuery;
    host.querySelector('#evidence-map-list-sort').value=state.listSort;
    host.querySelector('#evidence-map-list-sort').disabled=state.direction==='raised'&&Boolean(state.focus);
    host.querySelector('#evidence-map-pin').disabled=!state.focus||state.pins.length>=4||!state.body;
    applyBars(host);
    const canvas = host.querySelector('.map-canvas-host');
    canvas.classList.toggle('skeleton', !state.body);
    if (!state.body) {
      // While a side loads, the PREVIOUS side's picture is taken down. Leaving
      // it up under the new heading would present last direction's districts as
      // this one's - and measured, it did: a Raised click left the Spent map's
      // unpainted districts on screen long enough to read them as Raised's.
      // The <svg> is only kept across a layer toggle, a selection or a geocode
      // tick, which are the changes that must not cost the reader their zoom.
      canvas.textContent = '';
      delete canvas.dataset.pane;
      const second = host.querySelector('.map-canvas-host--b');
      second.textContent = '';
      delete second.dataset.pane;
      return;
    }
    const body=state.body;
    const current=()=>ticket===mapRenderTicket&&mapPane===paneKey&&state.body===body;
    let shapes = null;
    try {
      shapes = await vintageFile(state.body.boundary_file);
    } catch (error) {
      if(current())render(canvas, `<p class="muted">${escapeHtml(error.message)}</p>`);
      return;
    }
    if(!current())return;
    if (!shapes) {
      render(canvas, '<p class="muted">No district boundaries are built for this cycle.</p>');
      return;
    }
    let points = null, pointsError = '';
    if (state.layer === 'zip' || state.direction === 'raised' && ((state.body.by_zip || []).length
        || (state.compare && state.compareBody && (state.compareBody.by_zip || []).length))) {
      try { points = await zipPointsFile(state.body.zip_points_file); }
      catch (error) { pointsError = error.message; }
    }
    let drawing = null;
    try { drawing = await ensureMapModule(); }
    catch (error) { if(current())render(canvas, `<p class="muted">${escapeHtml(error.message)}</p>`); return; }
    if(!current())return;
    const fresh = canvas.dataset.pane !== mapPane;
    canvas.dataset.pane = mapPane;
    if (state.layer === 'sld') {
      canvas.setAttribute('aria-busy', 'true');
      // Keep the map's dimensions stable while a different bundle loads.
      canvas.classList.add('map-fetching');
      try {
        const [index, crosswalk] = await Promise.all([
          sldIndex || legislativeFile('sld/index.json'),
          sldCrosswalk || legislativeFile('zcta_sld.json')]);
        if (!current()) return;
        sldIndex = index; sldCrosswalk = crosswalk;
        if (!state.sldState) state.sldState = (body.by_state || []).find(row => index.chambers.upper.states[row.key])?.key || 'CA';
        const entry = index.chambers[state.chamber].states[state.sldState];
        const bundle = entry ? await legislativeFile(entry.file) : null;
        if (!current()) return;
        state.sldBundle = bundle;
        state.sldRows = state.direction === 'raised'
          ? drawing.legislativeRows(body, bundle, crosswalk, state.chamber, state.off || new Set()) : [];
        state.compareSldRows = state.direction === 'raised' && state.compare && state.compareBody
          ? drawing.legislativeRows(state.compareBody, bundle, crosswalk, state.chamber, state.off || new Set()) : [];
      } catch (error) {
        if (current()) {
          state.sldBundle = null; state.sldRows = [];
          host.querySelector('.map-provenance').textContent = error.message;
          render(canvas, `<p role="status">${escapeHtml(error.message)}</p>`);
          render(host.querySelector('.map-ranked'), '<p>Legislative amounts unavailable until geography loads.</p>');
          canvas.classList.remove('map-fetching'); canvas.removeAttribute('aria-busy');
        }
        return;
      }
      renderMapToolbar(host, pane, state);
      render(host.querySelector('.map-ranked'), state.compare ? mapChangesHtml(pane, state) : mapRankedHtml(pane, state));
      applyBars(host);
    }
    canvas.classList.remove('map-fetching'); canvas.removeAttribute('aria-busy');
    host.querySelector('.map-source-summary').textContent = state.layer === 'sld'
      ? `Census 2024 state legislative boundaries · ${state.sldState} · ${state.chamber} chamber. ${state.direction === 'raised' ? 'ZIP internal-point estimates; not verified donor addresses.' : 'Boundary reference only.'} Source details`
      : state.layer === 'zip' ? 'Census 2024 ZIP/ZCTA internal points · Approximate locations, not donor addresses. Source details'
        : `Census congressional and state boundaries · Congress ${shapes.vintage || ''}. Source details`;
    render(host.querySelector('.map-provenance'), mapProvenance(state, shapes, points)
      + (pointsError ? ' ' + escapeHtml(pointsError) : '') + geocodeBasisHtml(pane, state));
    // Build 06 (board M13): with a compare cycle the second map is drawn from
    // its own payload and boundary file. The two share one heat range (each
    // names the other as its companion) and each follows the other's zoom and
    // pan (mirror), whichever one the reader drags, wheels or keys.
    const hostB = host.querySelector('.map-canvas-host--b');
    let viewB = null;
    if (state.compare && state.compareBody) {
      let shapesB = null;
      try { shapesB = await vintageFile(state.compareBody.boundary_file); }
      catch (error) { state.compareMessage = error.message; }
      if (!current()) return;
      if (shapesB) viewB = {body: state.compareBody, shapes: shapesB, points, layer: state.layer, off: state.off || new Set(),
        sldBundle: state.layer === 'sld' ? state.sldBundle : null, sldRows: state.compareSldRows || [], chamber: state.chamber,
        direction: state.direction, focus: state.focus, enlarged: state.enlarged};
    }
    const zoomReadout = view => {
      const zoom = host.querySelector('.map-zoom-level'); if (zoom) zoom.textContent = view.scale.toFixed(1)+'×';
      const minus = host.querySelector('#evidence-map-zoom-out'), plus = host.querySelector('#evidence-map-zoom-in');
      if (minus) minus.disabled = view.scale <= 1; if (plus) plus.disabled = view.scale >= 40;
    };
    const sharedLegend = host.querySelector('.map-compare-legend');
    const viewA = {
      body: state.body, shapes, points, layer: state.layer, off: state.off || new Set(),
      sldBundle: state.layer === 'sld' ? state.sldBundle : null, sldRows: state.sldRows, chamber: state.chamber,
      direction: state.direction, focus: state.focus, enlarged: state.enlarged, companion: viewB,
      kinds: kindToggles(state),
      onSelect: (kind, key) => { chooseFocus(kind, key); },
      // One legend under both maps: the first map's card, which already
      // carries the shared range, cloned whenever the heat is repainted.
      onLegend: card => {
        if (!viewB || !card) { sharedLegend.hidden = true; sharedLegend.textContent = ''; return; }
        sharedLegend.replaceChildren(card.cloneNode(true));
        sharedLegend.hidden = false;
      },
      onView: view => {
        state.view = view;
        zoomReadout(view);
        if (viewB) drawing.mirror(hostB, view);
      }};
    const result = drawing.render(canvas, viewA);
    // Lane P (#48, #55): the map's layers - heat, hatch, added data, the Layer control.
    if (window.MentatMapEvidence) window.MentatMapEvidence.decorate(canvas, pane, state, drawing);
    if (fresh) {
      if (state.view) drawing.restore(canvas, state.view); else drawing.reset(canvas);
    }
    if (viewB) {
      hostB.dataset.pane = mapPane;
      // `quiet` (fix round 1, R06-01): re-rendering the second map must not
      // announce its old transform, which mirrored onto the first map and
      // cancelled the fly Reset view or Find had just started. The second map
      // takes the first map's transform instead.
      drawing.render(hostB, {...viewB, companion: viewA, quiet: true,
        onSelect: (kind, key) => { chooseFocus(kind, key); },
        onView: view => { state.view = view; zoomReadout(view); drawing.mirror(canvas, view); }});
      drawing.mirror(hostB, drawing.view(canvas));
    } else if (hostB.dataset.pane) {
      hostB.textContent = '';
      delete hostB.dataset.pane;
    }
    if (state.layer === 'sld' && state.sldNavigate) {
      state.sldNavigate = false;
      drawing.flyToKey(canvas, state.sldState);
    }
    const rule = host.querySelector('.map-share-rule');
    if (rule) { rule.textContent = state.compare ? '' : shareRuleText(state.body, state.layer); rule.hidden = !rule.textContent; }
    const unplaced = host.querySelector('.map-unplaced');
    const missing = pointsError ? [] : (result && result.unplaced) || [];
    unplaced.hidden = !missing.length;
    if (missing.length) {
      const total = missing.reduce((sum, row) => sum + (row.amount || 0), 0);
      unplaced.textContent = `Not placed on the map: ${number(missing.length)} ZIP`
        + `${missing.length === 1 ? '' : 's'} worth ${money(total)}`
        + ' (a PO-box-only or retired ZIP has no ZCTA centre). Their dollars are still in the totals above.';
    }
  }

  async function loadMap(reuseThumb = false) {
    const paneKey=mapPane, pane = panes.get(paneKey);
    if (!pane) return;
    const state = mapStateFor(paneKey), ticket=++state.requestTicket;
    state.loading = true;
    state.message = '';
    try {
      // RULING A: never send a blank cycle. The route refuses one, and the
      // pane's "All cycles" has already been resolved to the newest concrete
      // cycle, with the note in the header saying which.
      const cycle = chooseMapCycle(pane, state);
      state.cycle = cycle;
      const thumb = pane.thumb?.body;
      const direction=state.direction;
      const body = reuseThumb && thumb && thumb.cycle === cycle && thumb.direction === direction
        ? thumb : await api('/api/evidence-geo?' + new URLSearchParams({
        source: pane.source, id: pane.id, committee: pane.committee,
        cycle: cycle, direction}));
      if(ticket!==state.requestTicket||cycle!==state.cycle||direction!==state.direction)return;
      state.body=body;
      state.cycle = state.body.cycle || cycle;
      if (state.direction === 'raised' && pane.source === 'csv') await readGeocode(paneKey);
      if(ticket!==state.requestTicket)return;
      if (state.off === null) {
        state.off = new Set((state.body.kinds || []).filter(kind => !kind.default_on).map(kind => kind.kind));
      }
      if (state.body.reason) state.message = state.body.reason;
      if (!state.body.reconciles) {
        state.message = (state.message ? state.message + ' ' : '')
          + 'Saved itemized records leave an unresolved source gap; the amount is listed below.';
      }
    } catch (error) {
      if(ticket!==state.requestTicket)return;
      state.body = null;
      state.message = error.message;
    }
    state.loading = false;
    if(mapPane===paneKey)await renderMap();
    if (mapPane === paneKey && state.compare && state.body && !compareCurrent(state)) await loadCompare();
  }

  const compareCurrent = state => Boolean(state.compareBody)
    && String(state.compareBody.cycle || '') === String(state.compare || '')
    && (!state.compareBody.direction || state.compareBody.direction === state.direction);

  /* Build 06: the second cycle's payload, from the same route with the same
     source, id, committee and direction (prompt 06, item 8). The per-place
     change is computed in the browser (mapChangeRows). */
  async function loadCompare() {
    const paneKey = mapPane, pane = panes.get(paneKey);
    if (!pane) return;
    const state = mapStateFor(paneKey), ticket = ++state.compareTicket;
    const cycle = state.compare, direction = state.direction;
    state.compareBody = null; state.compareSldRows = []; state.compareMessage = '';
    if (mapPane === paneKey) await renderMap();
    if (!cycle) return;
    try {
      const body = await api('/api/evidence-geo?' + new URLSearchParams({
        source: pane.source, id: pane.id, committee: pane.committee, cycle, direction}));
      if (ticket !== state.compareTicket || cycle !== state.compare || direction !== state.direction) return;
      state.compareBody = body;
      if (body.reason) state.compareMessage = body.reason;
    } catch (error) {
      if (ticket !== state.compareTicket) return;
      state.compareMessage = error.message;
    }
    if (mapPane === paneKey) await renderMap();
  }

  async function loadMapRows(pane,state) {
    if(pane.source!=='csv')return;
    const ticket=++state.sourceTicket, cycle=state.cycle, offset=state.sourceOffset;
    state.sourceRows=null;state.sourceError='';
    try {
      const body=await api('/api/evidence-csv?'+new URLSearchParams({id:pane.id,committee:pane.committee,
        cycle,offset:String(offset),limit:String(PAGE_SIZE)}));
      if(ticket!==state.sourceTicket||cycle!==state.cycle||offset!==state.sourceOffset)return;
      state.sourceRows=body;
    } catch(error) {if(ticket===state.sourceTicket)state.sourceError=error.message;}
    if(mapPane===pane.key)await renderMap();
  }

  async function openMap(key, direction, chosen) {
    if (!panes.has(key)) return;
    const pane = panes.get(key);
    // A reader can press Map while the new pane is still fetching its cycles.
    // Share that read; an empty cycle list would otherwise produce a blank map.
    if (!pane.loaded) await loadPane(pane);
    if (!panes.has(key)) return;
    mapPane = key;
    const state = mapStateFor(key);
    if (direction) state.direction = direction;
    if (chosen) state.directionChosen = true;
    state.cycle = chooseMapCycle(panes.get(key), state);
    $('evidence-panes').hidden = true;
    $('evidence-compare').hidden = true;
    $('evidence-empty').hidden = true;
    $('evidence-map').hidden = false;
    await renderMap();
    writeScope();
    if (!state.body) await loadMap(true);
    await openOnTheSideWithData(state);
  }

  /* The map opens on the side that HAS data. `sides` came back with the first
     payload and was computed without drawing anything, so this costs one extra
     read only when the first side turned out to be the empty one, and never
     when the reader picked a direction themselves. */
  async function openOnTheSideWithData(state) {
    if (state.directionChosen || !state.body || !state.body.sides) return;
    const sides = state.body.sides;
    const other = state.direction === 'raised' ? 'spent' : 'raised';
    const here = sides[state.direction] || {};
    const there = sides[other] || {};
    if ((there.reported_total || 0) <= (here.reported_total || 0)) return;
    state.direction = other;
    state.body = null; state.off = null; state.focus = null; state.compareBody = null;
    await renderMap();
    await loadMap(true);
    writeScope();
  }

  function closeMap() {
    if (flowPane) closeFlow();
    const pane = panes.get(mapPane);
    if (mapPane) clearTimeout(mapStateFor(mapPane).geocodeTimer);
    mapPane = '';
    $('view-evidence').classList.remove('map-open');
    $('evidence-map').hidden = true;
    $('evidence-panes').hidden = false;
    renderPanes(); renderRail(); renderCompare();
    // The map's paged drill has its own response/cache. Its district selection
    // also opens the pane drawer, which must fetch its own scoped source rows.
    if (pane && pane.rowsOpen && !pane.rows && !pane.rowsLoading) loadPaneRows(pane);
    writeScope();
  }

  /* Lane F (board #44): Flow, the pane's third view beside Summary, Map and Rows.
     evidence-flow.js draws it from GET /api/flow; this module hands it one pane and
     takes the reader back for Summary, Map or Rows (a candidate opens Rows filtered to it). */
  function openFlow(key, basis) {
    const pane = panes.get(key);
    if (!pane || !window.MentatEvidenceFlow) return;
    if (mapPane) closeMap();
    flowPane = key;
    $('view-evidence').classList.add('flow-open');
    $('evidence-panes').hidden = true;
    $('evidence-compare').hidden = true;
    $('evidence-empty').hidden = true;
    window.MentatEvidenceFlow.open({
      pane: {key, source: pane.source, id: pane.id, committee: pane.committee, cycle: pane.cycle,
        cycles: pane.cycles, name: pane.name, industry: pane.industry, industryLabel: pane.industry,
        category: pane.category},
      basis, onChange: writeScope,
      onBack: closeFlow,
      onView: (view, detail) => {
        closeFlow();
        if (!panes.has(key)) return;
        if (view === 'map') { openMap(key, mapStateFor(key).direction); return; }
        pane.rowsOpen = view === 'rows';
        if (detail && detail.query) pane.networkQuery = detail.query;
        if (pane.rowsOpen && !pane.rows && !pane.rowsLoading) loadPaneRows(pane); else renderPanes();
        document.querySelector(`[data-pane="${CSS.escape(key)}"]`)?.scrollIntoView({block: 'nearest'});
      }});
    writeScope();
  }

  function closeFlow() {
    if (!flowPane) return;
    flowPane = '';
    $('view-evidence').classList.remove('flow-open');
    if (window.MentatEvidenceFlow) window.MentatEvidenceFlow.close();
    $('evidence-panes').hidden = false;
    renderPanes(); renderRail(); renderCompare(); writeScope();
  }

  async function refreshDirectCatalog() {
    const ticket = ++directTicket;
    const industry = $('pac-direct-sector').value;
    const q = $('pac-direct-search').value.trim();
    const results = $('pac-direct-results');
    if (!industry && !q) {
      results.replaceChildren();
      $('pac-direct-status').textContent = 'Choose a sector or search by PAC name or FEC ID.';
      return;
    }
    $('pac-direct-status').textContent = 'Finding PACs…';
    try {
      const body = await api('/api/pac-flow-catalog?' + new URLSearchParams({industry, q}));
      if (ticket !== directTicket) return;
      results.replaceChildren();
      for (const item of body.committees || []) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'pac-direct-result';
        button.dataset.committee = item.id;
        button.dataset.name = item.name;
        button.dataset.cycles = (item.cycles || []).join(',');
        const name = document.createElement('span'); name.textContent = item.name;
        const meta = document.createElement('small');
        meta.textContent = [item.id, item.category, (item.cycles || []).join(', ')].filter(Boolean).join(' · ');
        button.append(name, meta);
        results.append(button);
      }
      $('pac-direct-status').textContent = body.total
        ? `${number(body.total)} PAC${body.total === 1 ? '' : 's'} found${body.total > 500 ? ' · first 500 shown; narrow the search' : ''}. Choose one to open its flow.`
        : 'No PAC matches this search.';
    } catch (error) {
      if (ticket === directTicket) $('pac-direct-status').textContent = error.message;
    }
  }

  function openDirectFlow(committee, name, cycles) {
    if (!window.MentatEvidenceFlow) return;
    const industry = $('pac-direct-sector').value;
    const cycle = $('pac-direct-cycle').value;
    if (!/^\d{4}$/.test(cycle) || Number(cycle) % 2 || Number(cycle) < 1980 || Number(cycle) > 2100) {
      $('pac-direct-status').textContent = 'Enter an even election year from 1980 to 2100.';
      $('pac-direct-cycle').focus();
      return;
    }
    if (mapPane) closeMap();
    flowPane = 'direct';
    $('view-evidence').classList.add('flow-open');
    $('evidence-panes').hidden = true;
    $('evidence-compare').hidden = true;
    $('evidence-empty').hidden = true;
    window.MentatEvidenceFlow.open({pane: {key: 'direct', source: 'direct', id: '', committee, cycle,
      cycles: [...new Set([cycle, ...cycles])], name, industry, industryLabel: industry, category: 'PAC'},
      onBack: closeFlow, onView: closeFlow});
  }

  $('pac-direct-sector').addEventListener('change', refreshDirectCatalog);
  $('pac-direct-search').addEventListener('input', () => {
    clearTimeout(directTimer);
    directTimer = setTimeout(refreshDirectCatalog, 250);
  });
  $('pac-direct-results').addEventListener('click', event => {
    const button = event.target.closest('[data-committee]');
    if (button) openDirectFlow(button.dataset.committee, button.dataset.name,
      (button.dataset.cycles || '').split(',').filter(Boolean));
  });

  /* Board 6's "Collapse" (r5, audit men-b F2): the enlarged map folds back into
     the pane it was opened from, and the reader lands on that pane's small map
     ("Where it came from"), where a second press opens it again. "Back to
     panes" leaves the reader at the pane board instead. */
  function collapseMap() {
    const key = mapPane;
    closeMap();
    const pane = key ? document.querySelector(`#evidence-panes [data-pane="${CSS.escape(key)}"]`) : null;
    if (!pane) return;
    pane.scrollIntoView({block: 'nearest'});
    (pane.querySelector('.pane-thumb-canvas') || pane.querySelector('.pane-map'))?.focus({preventScroll: true});
  }

  // The rows drawer's district filter. An imported file carries the
  // contributor's ZIP, so the crosswalk that built the map answers which rows
  // belong to the district that was clicked; nothing is guessed from a name.
  async function crosswalkFor(vintage) {
    if (!vintage) return null;
    if (crosswalk && crosswalkVintage === vintage) return crosswalk;
    const body = await vintageFile('zcta_cd' + vintage + '.json');
    crosswalk = body; crosswalkVintage = vintage;
    return body;
  }

  function focusLabel(state, kind, key) {
    if (kind === 'sld') return (state.sldRows || []).find(row=>row.key===key)?.name
      || state.sldBundle?.districts.find(row=>key.endsWith('-'+row.id))?.name || key;
    if (kind === 'state') {
      const row = ((state.body || {}).by_state || []).find(item => item.key === key);
      return row ? (row.name || key) : key;
    }
    if (kind === 'zip') {
      const row = ((state.body || {}).by_zip || []).find(item => item.zip === key);
      return row ? `${row.zip}${row.city ? ' · ' + row.city : ''}${row.state ? ', ' + row.state : ''}` : key;
    }
    return key;
  }

  const FOCUS_BASIS = {district: 'contributor ZIP', state: 'contributor state',
                       region: 'contributor state', zip: 'contributor ZIP'};

  /* One selection for every layer. The district case is what chooseDistrict
     did; state, region and ZIP are the same idea one step wider, and the pane's
     rows drawer filters by the same rule the map heated by. */
  async function chooseFocus(kind, key, toggle = false) {
    const pane = panes.get(mapPane);
    const state = mapStateFor(mapPane);
    if (!pane || !state.body || !key) return;
    // Comparing: a place is picked on both maps and in the changes list; the
    // pane's row drawer is left alone (it belongs to one cycle).
    if (state.compare) {
      const again = state.focus && state.focus.kind === kind && state.focus.key === key;
      state.focus = again && toggle ? null : {kind, key, label: focusLabel(state, kind, key)};
      await renderMap();
      return;
    }
    const same = state.focus && state.focus.kind === kind && state.focus.key === key;
    state.focus = same && toggle ? null : {kind, key, label: focusLabel(state, kind, key)};
    state.listPage=0;state.listQuery='';
    state.district = state.focus ? state.focus.key : '';
    pane.district = state.district;
    pane.focus = state.focus;
    pane.districtBasis = '';
    if (state.focus && pane.source === 'csv') {
      try {
        await crosswalkFor(state.body.vintage);
        pane.districtBasis = FOCUS_BASIS[kind] || '';
      } catch (error) { pane.districtBasis = ''; state.message = error.message; }
    }
    pane.rowsOpen = Boolean(state.focus);
    if (pane.rowsOpen && state.direction==='raised' && !state.sourceRows) await loadMapRows(pane,state);
    await renderMap();
  }

  const chooseDistrict = key => chooseFocus('district', key);

  const districtOfZip = value => {
    const digits = String(value == null ? '' : value).replace(/\D/g, '').slice(0, 5);
    if (!crosswalk || digits.length < 5) return [];
    return (crosswalk[digits] || []).map(part => part[0]);
  };

  const zipOfRow = row => {
    const raw = row.raw || {};
    const field = Object.keys(raw).find(name => /zip|postal/i.test(name));
    return field ? String(raw[field] == null ? '' : raw[field]).replace(/\D/g, '').slice(0, 5) : '';
  };
  const stateOfRow = row => {
    const raw = row.raw || {};
    const field = Object.keys(raw).find(name => /^(contributor[ _]?)?(state|st)$/i.test(String(name).trim()));
    const text = field ? String(raw[field] || '').trim().toUpperCase() : '';
    if (/^[A-Z]{2}$/.test(text)) return text;
    return (districtOfZip(zipOfRow(row))[0] || '').slice(0, 2);
  };

  function rowsInFocus(pane, rows) {
    const focus = pane.focus || (pane.district ? {kind: 'district', key: pane.district} : null);
    if (!focus || pane.source !== 'csv') return rows;
    const state = mapStates.get(pane.key) || {};
    const region = focus.kind === 'region'
      ? new Set((((state.body || {}).by_region || []).find(row => row.key === focus.key) || {}).states || [])
      : null;
    return rows.filter(row => {
      if (focus.kind === 'zip') return zipOfRow(row) === focus.key;
      if (focus.kind === 'sld') {
        const parts = focus.key.split('-');
        return (sldCrosswalk?.[zipOfRow(row)] || [])[parts[1]==='upper'?0:1] === parts.slice(2).join('-');
      }
      if (focus.kind === 'district') return crosswalk ? districtOfZip(zipOfRow(row)).includes(focus.key) : false;
      if (focus.kind === 'state') return stateOfRow(row) === focus.key;
      if (focus.kind === 'region') return region.has(stateOfRow(row));
      return true;
    });
  }

  /* ---------------------------------------------------------------- wiring   */
  function toggleDrawer(button, drawer) {
    const opening = $(drawer).hidden;
    $(drawer).hidden = !opening;
    $(button).setAttribute('aria-expanded', String(opening));
    if (opening && drawer === 'evidence-import-drawer') $('evidence-csv-file').focus({preventScroll: true});
  }

  function removePane(key) {
    panes.delete(key);
    // Fix round 1 (D6): the "Close a pane" note is only true at the ceiling.
    if (panes.size < MAX_PANES) railNote('');
    renderPanes(); renderRail(); renderCompare(); sayScope(); writeScope();
  }

  async function addPane() {
    const chosen = parseSourceValue(sourceValue());
    const key = freeKey();
    if (!chosen.kind || !key) return;
    const pane = paneFrom(key, chosen.kind, chosen.id, $('evidence-pac').value, $('evidence-cycle').value);
    panes.set(key, pane);
    await loadPane(pane);
    writeScope();
  }

  $('evidence-source').addEventListener('change', () => { syncSourceTitle(); chooseSource().then(writeScope); });
  $('evidence-type').addEventListener('change', () => { applyFilter(); writeScope(); });
  $('evidence-find').addEventListener('input', applyFilter);
  $('evidence-cycle').addEventListener('change', () => renderRail());
  $('evidence-import-open').addEventListener('click', () => toggleDrawer('evidence-import-open', 'evidence-import-drawer'));
  $('evidence-snapshot-open').addEventListener('click', () => toggleDrawer('evidence-snapshot-open', 'evidence-snapshot-drawer'));
  $('evidence-follow-graph').addEventListener('change', () => {
    if ($('evidence-follow-graph').checked) followGraph();
    else { const pane = panes.get('a'); if (pane) { pane.follows = false; renderRail(); renderPanes(); } }
  });
  // A tick opens the PAC's pane on the rail's cycle; an untick closes it. The
  // fourth tick opens nothing and says why, in the rail itself.
  $('evidence-list').addEventListener('change', event => {
    const box = event.target.closest('input[type=checkbox]');
    if (!box) return;
    const chosen = parseSourceValue(sourceValue());
    if (!box.checked) {
      railNote('');
      for (const pane of panesFor(chosen.kind, chosen.id, box.value)) removePane(pane.key);
      return;
    }
    // Already open (the rail was redrawn under the pointer): nothing to open,
    // and certainly nothing to refuse.
    if (panesFor(chosen.kind, chosen.id, box.value).length) { railSignature = ''; renderRail(); return; }
    if (panes.size >= MAX_PANES) {
      box.checked = false;
      railNote('Close a pane to open another.');
      return;
    }
    railNote('');
    $('evidence-pac').value = box.value;
    if ($('evidence-pac').value !== box.value) { box.checked = false; return; }
    box.closest('.evidence-rail-row')?.classList.add('on');
    addPane().then(renderRail);
  });
  $('evidence-panes').addEventListener('input', event => {
    if (!event.target.matches('.network-search')) return;
    const root = event.target.closest('[data-pane]');
    const pane = panes.get(root.dataset.pane);
    pane.networkQuery = event.target.value;
    filterNetworkRows(root, pane.networkQuery);
  });
  $('evidence-panes').addEventListener('click', event => {
    const root = event.target.closest('[data-pane]');
    if (!root) return;
    const pane = panes.get(root.dataset.pane);
    if (!pane) return;
    if (event.target.closest('.pane-flow')) { openFlow(pane.key); return; }
    if (event.target.closest('.pane-map')) { openMap(pane.key, mapStateFor(pane.key).direction); return; }
    if (event.target.closest('.pane-summary')) {
      pane.rowsOpen = false; renderPanes(); return;
    }
    if (event.target.closest('.pane-thumb-canvas')) { openMap(pane.key, pane.thumbDirection, true); return; }
    const side = event.target.closest('.pane-thumb-side');
    if (side) {
      if (pane.thumbDirection === side.dataset.side) return;
      pane.thumbDirection = side.dataset.side;
      pane.thumbChosen = true;
      pane.thumb = null;
      loadThumb(pane);
      return;
    }
    if (event.target.closest('.pane-district-clear')) {
      pane.district = ''; pane.focus = null;
      const state = mapStateFor(pane.key); state.district = ''; state.focus = null;
      renderPanes(); return;
    }
    if (event.target.closest('.pane-remove')) { removePane(pane.key); return; }
    if (event.target.closest('.pane-rows-retry')) { if (!pane.rowsLoading) loadPaneRows(pane); return; }
    if (event.target.closest('.pane-rows')) {
      pane.rowsOpen = !pane.rowsOpen;
      if (pane.rowsOpen && !pane.rows && !pane.rowsLoading) loadPaneRows(pane); else renderPanes();
      return;
    }
    if (event.target.closest('.pane-prev')) { pane.offset = Math.max(0, pane.offset - PAGE_SIZE); loadPaneRows(pane); return; }
    if (event.target.closest('.pane-next')) { pane.offset += PAGE_SIZE; loadPaneRows(pane); return; }
    const about = event.target.closest('.pane-about');
    if (about) {
      const body = root.querySelector('.pane-about-body');
      body.hidden = !body.hidden;
      about.setAttribute('aria-expanded', String(!body.hidden));
    }
  });
  function mapCanvas() { return $('evidence-map').querySelector('.map-canvas-host'); }

  function runSearch(state) {
    const parsed = window.MentatEvidenceMap ? window.MentatEvidenceMap.parseSearch(state.search)
                                            : {kind: '', value: ''};
    state.searchMessage = '';
    if (!parsed.kind) { state.searchMessage = 'Type a state, a district like TX-19, or a five-digit ZIP.'; return; }
    if (parsed.kind === 'name') {
      const row = ((state.body || {}).by_state || [])
        .find(item => String(item.name || '').toLowerCase() === parsed.value);
      if (!row) { state.searchMessage = `Nothing on this map is called \u201c${state.search}\u201d.`; return; }
      parsed.kind = 'state';
      parsed.value = row.key;
    }
    const flew = window.MentatEvidenceMap.flyToKey(mapCanvas(), parsed.value);
    if (!flew) {
      state.searchMessage = `${parsed.value} is not on this map.`;
      return;
    }
    if (parsed.kind === 'district') state.layer = 'district';
    if (parsed.kind === 'zip') state.layer = 'zip';
    if (parsed.kind === 'state' && state.layer === 'sld' && sldIndex?.chambers?.upper?.states?.[parsed.value]) {
      state.sldState = parsed.value; state.sldNavigate = true;
    }
    chooseFocus(parsed.kind === 'zip' ? 'zip' : (parsed.kind === 'district' ? 'district' : 'state'),
                parsed.value);
  }

  $('evidence-map').addEventListener('click', event => {
    const state = mapStateFor(mapPane);
    const page=event.target.closest('[data-map-page]');
    if(page){state.listPage+=Number(page.dataset.mapPage);renderMap();return;}
    const sourcePage=event.target.closest('[data-map-source-page]');
    if(sourcePage){state.sourceOffset=Math.max(0,state.sourceOffset+Number(sourcePage.dataset.mapSourcePage)*PAGE_SIZE);loadMapRows(panes.get(mapPane),state);return;}
    const unpin=event.target.closest('[data-map-unpin]');
    if(unpin){state.pins.splice(Number(unpin.dataset.mapUnpin),1);renderMap();return;}
    if(event.target.closest('#evidence-map-pin')) {
      if(!state.focus||state.pins.length>=4)return;
      const rows=rankedRows({...state,layer:state.focus.kind}).filter(row=>row.key===state.focus.key);
      if(rows.length)state.pins.push({...rows[0],amount:rows.reduce((sum,row)=>sum+row.amount,0),cycle:state.cycle,
        direction:state.direction,scope:state.focus.kind==='sld'?'ZIP internal-point estimate · Census 2024 · '+state.chamber+' chamber':state.off?.size?'Hidden kinds: '+[...state.off].join(', '):'All available kinds on this layer'});
      else state.message='This place has no measured amount on the selected layer; nothing was pinned.';
      renderMap();return;
    }
    if (event.target.closest('#evidence-map-layers') && window.MentatMapEvidence) { window.MentatMapEvidence.toggleControl(mapCanvas(), panes.get(mapPane), state); return; }
    if (event.target.closest('#evidence-map-open-studio') && window.MentatMapEvidence) { window.MentatMapEvidence.openInStudio(panes.get(mapPane), state); return; }
    if (event.target.closest('#evidence-map-back')) { closeMap(); return; }
    if (event.target.closest('#evidence-map-collapse')) { collapseMap(); return; }
    if (event.target.closest('#evidence-map-geocode-start')) { startGeocode(); return; }
    if (event.target.closest('#evidence-map-geocode-stop')) { stopGeocode(); return; }
    if (event.target.closest('#evidence-map-zoom-in')) { window.MentatEvidenceMap.zoom(mapCanvas(), 1.4); return; }
    if (event.target.closest('#evidence-map-zoom-out')) { window.MentatEvidenceMap.zoom(mapCanvas(), 1 / 1.4); return; }
    if (event.target.closest('#evidence-map-reset')) {
      state.search = ''; state.searchMessage = ''; state.focus = null;state.listPage=0;state.listQuery='';
      const pane=panes.get(mapPane);if(pane){pane.focus=null;pane.district='';}
      window.MentatEvidenceMap.reset(mapCanvas()); renderMap(); return;
    }
    if (event.target.closest('#evidence-map-enlarge')) {
      // FLIP, not a second map: the same <svg> stays mounted, so the zoom
      // survives growing the card to the full workspace and shrinking it back.
      state.enlarged = !state.enlarged;
      renderMap();
      return;
    }
    if (event.target.closest('.map-clear-focus') || event.target.closest('.map-back-step')) {
      if (state.focus) chooseFocus(state.focus.kind, state.focus.key, true);
      return;
    }
    const crumb = event.target.closest('.map-crumb');
    if (crumb) {
      if (!crumb.dataset.crumb) {
        if (state.focus) chooseFocus(state.focus.kind, state.focus.key, true);
        window.MentatEvidenceMap.reset(mapCanvas());
      } else {
        window.MentatEvidenceMap.flyToKey(mapCanvas(), crumb.dataset.crumb);
        chooseFocus(crumb.dataset.crumbKind || 'state', crumb.dataset.crumb);
      }
      return;
    }
    const direction = event.target.closest('.map-direction');
    if (direction) {
      if (state.direction !== direction.dataset.direction) {
        state.direction = direction.dataset.direction;
        state.directionChosen = true;
        state.body = null; state.off = null; state.district = ''; state.focus = null; state.compareBody = null;
        state.listPage=0;state.listQuery='';state.sourceRows=null;state.sourceOffset=0;state.sourceTicket++;
        renderMap().then(loadMap).then(writeScope);
      }
      return;
    }
    const geography = event.target.closest('.map-layer');
    if (geography && !geography.disabled) {
      state.layer = geography.dataset.mapLayer;
      if (state.layer === 'sld') state.sldNavigate = true;
      state.layerChosen = true;
      state.focus = null; state.district = '';
      state.listPage=0;state.listQuery='';
      const pane = panes.get(mapPane);
      if (pane) { pane.district = ''; pane.focus = null; }
      renderMap();
      return;
    }
    const layer = event.target.closest('.layer');
    if (layer) {
      const kind = layer.dataset.layer;
      if (state.off.has(kind)) state.off.delete(kind); else state.off.add(kind);
      renderMap();
      return;
    }
    const listed = event.target.closest('[data-focus]');
    if (listed) {
      window.MentatEvidenceMap.flyToKey(mapCanvas(), listed.dataset.focus);
      chooseFocus(listed.dataset.focusKind || 'district', listed.dataset.focus);
    }
    // A click on a SHAPE is not handled here. evidence-map.js owns the <svg>
    // and calls back through onSelect; handling it in both places selected the
    // shape and then immediately deselected it (measured: the district never
    // kept `chosen`, because the same click ran chooseFocus twice).
  });
  $('evidence-map').addEventListener('keydown', event => {
    if (event.target.id !== 'evidence-map-search' || event.key !== 'Enter') return;
    event.preventDefault();
    const state = mapStateFor(mapPane);
    state.search = event.target.value;
    runSearch(state);
    renderMap();
  });
  $('evidence-map').addEventListener('change', event => {
    if (event.target.id === 'evidence-map-layer-select') {
      const button = $('evidence-map').querySelector(`[data-map-layer="${event.target.value}"]`);
      if (button) button.click(); return;
    }
    if (['evidence-map-chamber', 'evidence-map-state'].includes(event.target.id)) {
      const state = mapStateFor(mapPane);
      if (event.target.id === 'evidence-map-chamber') state.chamber = event.target.value;
      else { state.sldState = event.target.value; state.sldNavigate = true; }
      state.focus = null; state.district = ''; state.listPage = 0; state.listQuery = '';
      const pane = panes.get(mapPane); if (pane) { pane.focus = null; pane.district = ''; }
      renderMap(); return;
    }
    if(event.target.id==='evidence-map-list-sort') {
      const state=mapStateFor(mapPane);state.listSort=event.target.value;state.listPage=0;renderMap();return;
    }
    if (event.target.id === 'evidence-map-compare') {
      const state = mapStateFor(mapPane);
      state.compare = event.target.value;
      // Two maps side by side are the whole width already; Enlarge is a one-map control.
      if (state.compare) state.enlarged = false;
      state.focus = null; state.district = ''; state.listPage = 0;
      const pane = panes.get(mapPane); if (pane) { pane.focus = null; pane.district = ''; }
      writeScope();
      loadCompare();
      return;
    }
    if (event.target.id !== 'evidence-map-cycle') return;
    const state = mapStateFor(mapPane);
    const previous = state.cycle;
    state.cycle = event.target.value;
    // A cycle compared with itself is no comparison: the two swap instead.
    if (state.compare && state.compare === state.cycle) { state.compare = previous; state.compareBody = null; writeScope(); }
    // The reader has now chosen a cycle, so the "showing the newest" note has
    // done its job and goes.
    state.note = '';
    state.body = null; state.off = null; state.district = '';state.focus=null;
    state.listPage=0;state.sourceRows=null;state.sourceOffset=0;state.sourceTicket++;
    const pane=panes.get(mapPane);if(pane){pane.focus=null;pane.district='';}
    renderMap();
    loadMap();
  });
  $('evidence-map').addEventListener('input',event=>{
    if (event.target.id === 'evidence-map-search') { mapStateFor(mapPane).search = event.target.value; return; }
    if(event.target.id!=='evidence-map-list-search')return;
    const state=mapStateFor(mapPane);state.listQuery=event.target.value;state.listPage=0;
    const host=$('evidence-map');
    render(host.querySelector('.map-ranked'),mapRankedHtml(panes.get(mapPane),state));applyBars(host);
  });
  // The compare strip is rendered from JS, so its Map button is reached by
  // delegation from the static host rather than by id at load time.
  $('evidence-compare').addEventListener('click', event => {
    if (!event.target.closest('#evidence-compare-map')) return;
    const first = PANE_KEYS.find(key => panes.has(key));
    if (first) openMap(first, mapStateFor(first).direction);
  });
  $('evidence-csv-file').addEventListener('change', () => {
    const file = $('evidence-csv-file').files[0];
    if (file) importFile(file, '');
  });
  $('evidence-sheet').addEventListener('change', () => {
    const sheet = $('evidence-sheet').value;
    if (pendingUploadFile) importFile(pendingUploadFile, sheet);
    else if (pendingPathRequest) importPath(pendingPathRequest.path, sheet, pendingPathRequest.header_path);
  });
  $('evidence-path-read').addEventListener('click', readPathBox);
  $('evidence-path').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); readPathBox(); }
  });
  $('evidence-browse').addEventListener('click', () => {
    const opening = $('evidence-browser').hidden;
    $('evidence-browser').hidden = !opening;
    if (opening) browse(directoryOf($('evidence-path').value));
  });
  $('evidence-browser').addEventListener('click', event => {
    const open = event.target.closest('[data-open-path]');
    if (open) { importPath(open.dataset.openPath, '', $('evidence-header-path').value.trim()); return; }
    const step = event.target.closest('[data-browse-path]');
    if (step) browse(step.dataset.browsePath);
  });
  $('evidence-refresh').addEventListener('click', () => refreshSources().then(chooseSource));
  $('evidence-use-config').addEventListener('click', () => {
    const chosen = parseSourceValue(sourceValue());
    const body = chosen.kind === 'snapshot' ? snapshotBodies.get(chosen.id) : null;
    if (!body) { $('evidence-job-status').textContent = 'Choose a saved snapshot first.'; return; }
    $('evidence-company-input').value = body.company || '';
    $('evidence-cycles-input').value = [...new Set((body.committee_finances || []).map(row => row.cycle))].join(', ');
    $('evidence-committees-input').value = [...new Set((body.committee_finances || []).map(row => row.committee_id))].join(', ');
    $('evidence-aliases-input').value = (body.aliases || []).join('\n');
  });
  $('evidence-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (evidenceJob) return;
    $('evidence-collect').disabled = true;
    try {
      const job = await api('/api/evidence-collect', {company: $('evidence-company-input').value,
        aliases: $('evidence-aliases-input').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
        committees: $('evidence-committees-input').value, cycles: $('evidence-cycles-input').value});
      evidenceJob = job.id;
      try { localStorage.setItem('mentat-evidence-job', evidenceJob); } catch {}
      pollEvidenceJob();
    } catch (error) { $('evidence-job-status').textContent = error.message; collectorState(); }
  });
  window.addEventListener('mentat:graph', event => { graphContext = event.detail; followGraph(); });
  // Only this tab's own view pays for the catalogs. app.js dispatches mentat:view on
  // every tab change, so an unguarded start() made a reader who opened Chart Studio
  // fetch the import listing and the snapshot catalog they never asked for. `split`
  // is true when Graph is paired with this pane, which shows it too.
  window.addEventListener('mentat:view', event => {
    renderPanes();
    const detail = event.detail || {};
    if (detail.view === 'evidence' || detail.split) start();
  });

  /* ------------------------------------------------------------------ start */
  function start() {
    if (!startup) startup = initialize();
    return startup;
  }
  async function initialize() {
    say('Finding imported files and saved snapshots…');
    await refreshSources();
    try {
      const catalog = await api('/api/pac-flow-catalog');
      $('pac-direct-sector').replaceChildren(new Option('Any PAC', ''),
        ...(catalog.sectors || []).map(name => new Option(name, name)));
    } catch (error) { $('pac-direct-status').textContent = error.message; }
    await restorePanes();
    if (!panes.size) { fillTypes(); fillCycles(); applyFilter(); }
    sayScope();
  }

  // Resolve an actual saved source; never substitute another PAC or cycle.
  // Snapshots carry committee-wide evidence, so prefer the newest matching
  // snapshot before falling back to an imported workbook's narrower scope.
  async function openCommittee(committee, cycle = '') {
    await start();
    committee = String(committee || ''); cycle = String(cycle || '');
    const existing = [...panes.values()].find(pane => pane.committee === committee
      && String(pane.cycle || '') === cycle);
    if (existing) {
      closeMap();
      if (!existing.loaded) await loadPane(existing);
      document.querySelector(`[data-pane="${existing.key}"]`)?.scrollIntoView({block: 'nearest'});
      return existing.key;
    }
    say('Finding saved evidence for ' + committee + '…');
    let chosen = null;
    const saved = snapshots.find(source => (source.committees || []).some(row =>
      row.id === committee && (!cycle || row.cycle === cycle)));
    if (saved) chosen = {kind: 'snapshot', id: saved.id};
    if (!chosen) {
      const source = imports.find(item => !item.reread
        && (!cycle || (item.cycles || []).map(String).includes(cycle))
        && (item.committees || []).some(row => row.id === committee));
      if (source) chosen = {kind: 'csv', id: source.id};
    }
    if (!chosen) {
      // Graph can name a committee from a published sector report even when no
      // PAC evidence snapshot or import has ever been made. Open its direct
      // Flow instead of turning that valid selection into a dead end.
      const sector = graphContext?.industry || '';
      if (sector && [...$('pac-direct-sector').options].some(option => option.value === sector))
        $('pac-direct-sector').value = sector;
      if (cycle) $('pac-direct-cycle').value = cycle;
      let name = committee;
      try {
        const listing = await api('/api/pac-flow-catalog?' + new URLSearchParams({q: committee}));
        name = (listing.committees || []).find(item => item.id === committee)?.name || committee;
      } catch { /* The FEC ID is enough to collect and draw the Flow. */ }
      openDirectFlow(committee, name, cycle ? [cycle] : []);
      return 'direct';
    }
    // Source lookup awaits I/O; recheck capacity and duplicate requests here.
    const duplicate = [...panes.values()].find(pane => pane.committee === committee && pane.cycle === cycle);
    if (duplicate) return duplicate.key;
    const key = freeKey();
    if (!key) { say('Three panes are open. Close a pane to open ' + committee + '.'); return null; }
    closeMap();
    $('evidence-source').value = chosen.kind + ':' + chosen.id;
    syncSourceTitle();
    await chooseSource();
    $('evidence-pac').value = committee; $('evidence-cycle').value = cycle;
    const pane = paneFrom(key, chosen.kind, chosen.id, committee, cycle);
    panes.set(key, pane);
    await loadPane(pane); writeScope();
    document.querySelector(`[data-pane="${key}"]`)?.scrollIntoView({block: 'nearest'});
    return key;
  }

  window.MentatEvidence = {
    openCommittee,
    setCollector(available, readOnly) { collectorAvailable = !!available; collectorReadOnly = !!readOnly; collectorState(); },
    panes: () => [...panes.values()].map(pane => ({key: pane.key, source: pane.source, id: pane.id,
      committee: pane.committee, cycle: pane.cycle})),
  };

  try { evidenceJob = localStorage.getItem('mentat-evidence-job') || ''; } catch {}
  if (evidenceJob) { $('evidence-collect').disabled = true; pollEvidenceJob(); }
  if (!$('view-evidence').hidden) start();
})();
