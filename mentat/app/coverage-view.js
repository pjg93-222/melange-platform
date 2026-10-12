/* Shared, offline-testable presentation for saved collection coverage. */
(function (root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function select(rows, sectors, cycles) {
    const names = sectors.map(s => s.trim().toLocaleLowerCase()).filter(Boolean);
    const selected = rows.filter(r => (!names.length || names.includes(r.industry.toLocaleLowerCase())) && cycles.includes(r.cycle));
    for (const sector of sectors.filter(s => s.trim())) for (const cycle of cycles) {
      if (!selected.some(r => r.industry.toLocaleLowerCase() === sector.trim().toLocaleLowerCase() && r.cycle === cycle)) {
        selected.push({industry:sector,cycle,freshness:'unverifiable',reason:'No saved collection evidence for this sector/cycle.'});
      }
    }
    return selected;
  }
  function html(rows) {
    if (!rows.length) return '<p class="coverage-unverifiable">Unverifiable · No saved FEC coverage evidence available.</p>';
    return '<div class="table-scroll coverage-table"><table><thead><tr><th>Sector / cycle</th><th>Verified FEC data-through</th><th>Coverage status</th><th>Latest observed transaction</th><th>Evidence</th></tr></thead><tbody>' + rows.map(row => {
      const verified = !!row.verified_data_through && ['current','stale'].includes(row.freshness);
      const state = verified ? row.freshness : 'unverifiable';
      const status = state === 'current' ? 'Current within policy' : state === 'stale' ? 'Stale coverage' : 'Unverifiable coverage';
      const lag = verified ? `<small>${escape(row.lag_days)} days behind ${escape(row.target_date)}</small>` : '';
      const evidence = (row.evidence || []).map(e => `<li>${escape(e.company)} · ${escape(e.kind)} · ${escape(e.verified_data_through)}<small>${escape((e.verification_evidence || []).join('; '))}</small></li>`).join('');
      return `<tr><td data-label="Sector / cycle">${escape(row.industry)}<small>${row.cycle - 1}–${escape(row.cycle)}</small></td><td data-label="Verified FEC data-through">${escape(verified ? row.verified_data_through : 'Unknown')}</td><td data-label="Coverage status" class="coverage-${state}"><strong>${status}</strong>${lag}</td><td data-label="Latest observed transaction">${escape(row.latest_observed_transaction_date || 'Unknown')}<small>Observed only; does not establish coverage.</small></td><td data-label="Evidence">${escape(row.reason)}<small>${escape(row.run_name || '')}${row.company_count != null ? ' · ' + escape(row.company_count) + ' companies' : ''}</small><details><summary>Coverage evidence</summary><p>${escape(row.scope || 'FEC employer and corporate searches')}<br>${escape(row.receipt || 'No receipt')}</p><p>Database file modified: ${escape(row.database_modified_at || 'Unknown')} · file event only.</p>${evidence ? '<ul>' + evidence + '</ul>' : '<p>No dated evidence for the full scope.</p>'}</details></td></tr>`;
    }).join('') + '</tbody></table></div>';
  }
  /* Loading's sector x cycle grid (prompt 07, board L4). One row per sector
     exactly as the saved data names it (never merged: "Credit Union" and
     "Credit Unions" stay two rows), plus the roster's own sectors when nothing
     is saved for them yet, and one column per supported cycle. A cell is
     current, stale, unverifiable (a saved row whose data-through date cannot
     be verified) or none (nothing saved for that sector and cycle). */
  const CYCLES = [2022, 2024, 2026];
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const STATUS = {current:'Current within policy', stale:'Stale coverage', unverifiable:'Unverifiable coverage', none:'Not collected'};
  const NO_EVIDENCE = 'No saved collection evidence for this sector/cycle.';
  function cycleLabel(cycle) { const value = Number(cycle); return `${value - 1}-${String(value).slice(2)}`; }
  function formatDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ''));
    return match ? `${MONTHS[Number(match[2]) - 1]} ${Number(match[3])}, ${match[1]}` : String(value ?? '');
  }
  function cellState(row) {
    if (!row) return 'none';
    return row.verified_data_through && ['current','stale'].includes(row.freshness) ? row.freshness : 'unverifiable';
  }
  function matrix(rows, rosterSectors = [], cycles = CYCLES) {
    const sectors = [];
    for (const row of rows) if (!sectors.some(s => s.name === row.industry)) sectors.push({name:row.industry, roster:false});
    sectors.sort((a, b) => a.name.localeCompare(b.name, undefined, {sensitivity:'base'}) || (a.name < b.name ? -1 : 1));
    for (const raw of rosterSectors) {
      const name = String(raw ?? '').trim();
      if (!name) continue;
      const lower = name.toLocaleLowerCase();
      const exact = sectors.find(s => s.name === name) || sectors.find(s => s.name.toLocaleLowerCase() === lower);
      if (exact) exact.roster = true; else sectors.push({name, roster:true});
    }
    return sectors.map(sector => ({sector:sector.name, roster:sector.roster, cells:cycles.map(cycle => {
      const row = rows.find(r => r.industry === sector.name && Number(r.cycle) === cycle) || null;
      return {sector:sector.name, cycle, row, state:cellState(row)};
    })}));
  }
  function dateTag(value) { return `<time datetime="${escape(value)}">${escape(formatDate(value))}</time>`; }
  function chipText(cell) {
    const row = cell.row || {};
    if (cell.state === 'current') return `Verified · to ${dateTag(row.verified_data_through)}`;
    if (cell.state === 'stale') return `Stale · to ${dateTag(row.verified_data_through)}`;
    return cell.state === 'unverifiable' ? 'Unverifiable' : 'Not collected';
  }
  function sameCell(a, b) { return !!a && !!b && a.sector === b.sector && Number(a.cycle) === Number(b.cycle); }
  function detail(cell) {
    if (!cell) return '<p class="coverage-detail-empty">Choose a cell to see what is saved for that sector and cycle.</p>';
    const row = cell.row || {}, saved = !!cell.row;
    const verified = cell.state === 'current' || cell.state === 'stale';
    const lag = verified && row.lag_days != null ? `<small>${escape(row.lag_days)} days behind ${escape(formatDate(row.target_date))}</small>` : '';
    const evidence = (row.evidence || []).map(e => `<li>${escape(e.company)} · ${escape(e.kind)} · ${escape(e.verified_data_through)}<small>${escape((e.verification_evidence || []).join('; '))}</small></li>`).join('');
    const roster = saved ? escape(row.run_name || 'Unknown') + (row.company_count != null ? ` · ${escape(row.company_count)} companies` : '') : 'None saved';
    return `<h3 class="coverage-detail-title">${escape(cell.sector)} · ${cycleLabel(cell.cycle)}</h3>` +
      '<dl class="coverage-detail-list">' +
      `<div><dt>Verified data-through:</dt><dd>${verified ? dateTag(row.verified_data_through) : 'Unknown'}</dd></div>` +
      `<div><dt>Status:</dt><dd>${STATUS[cell.state]}${lag}</dd></div>` +
      `<div><dt>Latest observed transaction:</dt><dd>${row.latest_observed_transaction_date ? dateTag(row.latest_observed_transaction_date) : 'Unknown'}<small>Observed only; does not establish coverage.</small></dd></div>` +
      `<div><dt>Roster:</dt><dd>${roster}</dd></div></dl>` +
      `<p class="coverage-detail-reason">${escape(saved ? row.reason : NO_EVIDENCE)}</p>` +
      (saved ? `<details class="coverage-evidence"><summary class="secondary">Coverage evidence</summary><p>${escape(row.scope || 'FEC employer and corporate searches')}<br>${escape(row.receipt || 'No receipt')}</p><p>Database file modified: ${escape(row.database_modified_at || 'Unknown')} · file event only.</p>${evidence ? '<ul>' + evidence + '</ul>' : '<p>No dated evidence for the full scope.</p>'}</details>` : '');
  }
  function grid(model, selected, cycles = CYCLES) {
    if (!model.length) return '<p class="coverage-grid-empty">Nothing is saved on this computer yet. Coverage appears here after the first collection.</p>';
    const head = '<thead><tr><th scope="col"><span class="sr-only">Sector</span></th>' + cycles.map(cycle => `<th scope="col">${cycleLabel(cycle)}</th>`).join('') + '</tr></thead>';
    const body = model.map(line => `<tr${line.roster ? ' class="is-roster"' : ''}><th scope="row">${escape(line.sector)}${line.roster && line.cells.every(cell => !cell.row) ? '<small>This roster</small>' : ''}</th>` +
      line.cells.map(cell => `<td><button type="button" class="coverage-cell" data-state="${cell.state}" data-sector="${escape(cell.sector)}" data-cycle="${cell.cycle}" aria-pressed="${sameCell(cell, selected)}"><span class="sr-only">${escape(cell.sector)}, ${cycleLabel(cell.cycle)}: ${STATUS[cell.state]}. </span><span class="coverage-chip">${chipText(cell)}</span></button></td>`).join('') + '</tr>').join('');
    const legend = '<p class="coverage-legend">' + [['current','Verified · data available'],['stale','Stale · behind the policy date'],['unverifiable','Unverifiable'],['none','Not collected']].map(([state, text]) => `<span><i class="coverage-key" data-state="${state}" aria-hidden="true"></i>${text}</span>`).join('') + '</p>';
    return `<div class="coverage-grid-layout"><div class="coverage-grid-main"><div class="table-scroll coverage-grid-scroll"><table class="coverage-grid">${head}<tbody>${body}</tbody></table></div>${legend}</div><div class="coverage-detail">${detail(selected)}</div></div>`;
  }
  /* Collection gaps (FIX 4). A report's own Data Quality check names the company x cycle cells whose stored
     rows are materially fewer than FEC reported (the server parses it: collection_gaps.py). Chart Studio,
     Graph and Insights ask this module whether the company x cycle scope on screen touches one, and what to
     say. The wording is built once, here, so the chip, the caption under a chart, an image, a CSV row and a
     slide cannot disagree. Nothing here counts rows or touches an amount.
     gaps: {failed, cells: [{company, cycle, destination, held, reported, percent}], unitemized}.
     scope: {companies, cycles}; a missing or empty list is "all". A cycle may be 2026 or 2025-2026. */
  const GAP_CHIP = 'Coverage gap';
  const endYear = value => { const found = String(value ?? '').match(/(\d{4})\D*$/); return found ? found[1] : ''; };
  const asList = value => (Array.isArray(value) ? value : value == null || value === '' ? [] : [value]).map(String).filter(Boolean);
  const gapCount = value => Number(value).toLocaleString('en-US');
  const gapCycle = cycle => { const found = String(cycle).match(/^(\d{4})\D+(\d{4})$/); return found ? `${found[1]}-${found[2].slice(2)}` : String(cycle); };
  function gapLine(cell) {
    const percent = Number.isFinite(cell.percent) ? cell.percent : Math.round(cell.held / cell.reported * 100);
    return `${cell.company} ${gapCycle(cell.cycle)}: ${percent}% of FEC-reported records (${gapCount(cell.held)} of ${gapCount(cell.reported)} ${cell.destination})`;
  }
  // The company and cycle a contribution query's filters name. Equals and "is one of" restrict; any other
  // operation leaves that field open, which only ever warns about more.
  function filtersScope(filters) {
    const scope = {companies: null, cycles: null};
    for (const filter of filters || []) {
      const key = filter && filter.field === 'company' ? 'companies' : filter && filter.field === 'cycle' ? 'cycles' : '';
      const values = key ? asList(filter.value) : [];
      if (!key || !['eq', 'equals', 'in'].includes(filter.op) || !values.length) continue;
      scope[key] = scope[key] ? scope[key].filter(value => values.includes(value)) : values;
    }
    return scope;
  }
  // parts: one {gaps, scope} or a list of them (compared scopes, compared reports). A cell shared by two
  // parts is named once; the unitemized remainder is the largest one, never a sum (the same check is
  // printed in every workbook of an industry). Null when nothing about this scope is short or unlisted.
  function gapNotice(parts) {
    const cells = new Map(), sources = new Set();
    let more = 0, unlisted = false;
    for (const part of [].concat(parts || [])) {
      const gaps = part && part.gaps;
      if (!gaps || !gaps.failed) continue;
      if (gaps.source) sources.add(gaps.source);
      const scope = part.scope || {}, companies = asList(scope.companies), years = asList(scope.cycles).map(endYear);
      for (const cell of gaps.cells || []) {
        if ((companies.length && !companies.includes(cell.company)) || (years.length && !years.includes(endYear(cell.cycle)))) continue;
        cells.set([cell.company, cell.cycle, cell.destination].join('\u0001'), cell);
      }
      more = Math.max(more, Number(gaps.unitemized) || 0);
      unlisted = unlisted || (!(gaps.cells || []).length && !gaps.unitemized);
    }
    if (!cells.size && !more && !unlisted) return null;
    const lines = [...cells.values()].map(gapLine);
    if (more) lines.push(`${gapCount(more)} more ${more === 1 ? 'short cell is' : 'short cells are'} not itemized in the workbook's Data Quality check`);
    else if (unlisted && !lines.length) lines.push("The workbook's Data Quality check reports short collection cells that are not itemized");
    const caption = lines.join('; ');
    return {chip: GAP_CHIP, caption, text: `${GAP_CHIP}: ${caption}`, cells: [...cells.values()], unitemized: more, sources: [...sources]};
  }
  const withGap = (text, notice) => (notice && notice.text ? (text ? `${text} · ` : '') + notice.text : text);
  // The console's own quality chip (style.css); the caption, and where the check was read from, are its
  // tooltip, and the caption is printed beside it.
  function chipNode(notice, doc = document) {
    const chip = doc.createElement('span');
    chip.className = 'quality-chip coverage-gap-chip';
    chip.textContent = notice.chip;
    chip.title = notice.caption + ((notice.sources || []).length ? ` · Source: ${notice.sources.join(', ')}` : '');
    return chip;
  }
  const api = {select, html, matrix, grid, detail, cycleLabel, formatDate, CYCLES, GAP_CHIP, filtersScope, gapNotice, withGap, chipNode};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MentatCoverage = api;
})(typeof window === 'undefined' ? globalThis : window);
