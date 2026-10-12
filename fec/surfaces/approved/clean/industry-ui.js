/* industry-ui.js: overlay for original-industry.html (Industry Overview #37, Organizations #38; the shell passes ?tab= and ?org=<company id>); owners: prompts 10, 11. Runs after the page engine, restored-links.js and ui-kit.js. */
/* Build 10 · Industry › Overview (board #37) and the Overview | Organizations tab group (?tab=overview|organizations).
   Reads only #page-data (industries[].spend, companies[].bills, companyBills). Industry colours are taken at run time
   from the page's own .sector-dot classes; the Bank tier view calls the page's own tierChart(). The page's inline
   scripts and #page-data are untouched; legacy nodes are hidden with CSS or moved (the .sitenav link, .hero-stats). */
/* Build 11 · Industry › Organizations (board #38): the Organizations tab lists every pickable organization (search +
   industry filter; row → page) and ?org=<companies[].id> opens one organization's page (header, attention by year,
   Bills lobbied | Members | Champion detail | Mentions, top members). Reads companies[], companyBills, companyMembers,
   industries[].organizations[].rows, orgMentions and postureStates from #page-data; calls the page's own postureDetail,
   lineSpark, memberFlag and companyBoundary; posture colours are read from the page's .postbadge classes, party colours
   from the kit (colors.json). Roster organisation links (lobby-network.html#org=) open the page in place. */
/* Fix round 1 (MD-1, MB-S-3, MD-4, MD-5): below the Overview grid the page's industry panel is three collapsed folds
   (Member attention with the Posture wording segmented control, Public mentions, the roster); its legacy head, callouts and
   method lines, and the build receipts, are hidden and read verbatim into "How to read this" (Sources & method last). */
/* Fix round 2 (MEL-DBG-1): member links resolve by bioguide through the kit's catalog (the desk's name in the link, the row's
   lobbying name as the label), on the organization page and on the page's own Overview tables; members the desk lacks are plain text. */
(() => {
  'use strict';
  if (window.MelangeIndustry || !document.getElementById('industries')) return;
  const doc = document, root = doc.documentElement;
  const params = new URLSearchParams(location.search);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => (Number(v) || 0).toLocaleString('en-US');
  const plural = (n, word) => `${num(n)} ${word}${Number(n) === 1 ? '' : 's'}`;
  const kit = () => window.MLK || null;
  const TABS = [['overview', 'Overview'], ['organizations', 'Organizations']];
  const PLURAL = { credit_union: 'credit unions', bank: 'banks', crypto: 'crypto organizations', fintech: 'fintech organizations', telecom: 'telecom organizations' };
  const POS = { support: ['▲', 'Support'], oppose: ['▼', 'Oppose'], neutral: ['◆', 'Neutral'] };
  const S = { data: null, tab: 'overview', focus: '', compare: false, byTier: false, colors: {}, bills: {}, orgCount: null, sitenav: null };
  let els = null, uid = 0, redrawQueued = false;

  // ---- data (from #page-data only) ------------------------------------------------------------------------------
  function readData() {
    const node = doc.getElementById('page-data');
    if (!node) throw new Error('#page-data is missing');
    const data = JSON.parse(node.textContent);
    if (!data || !Array.isArray(data.industries)) throw new Error('#page-data has no industries');
    return data;
  }
  const industries = () => (S.data && S.data.industries) || [];
  const bySector = sector => industries().find(item => item.sector === sector) || null;
  const rowsOf = item => (item && Array.isArray(item.spend) ? item.spend.slice().sort((a, b) => a.year - b.year) : []);
  const complete = item => rowsOf(item).filter(row => !row.partial);
  const lastOf = list => list[list.length - 1] || null;
  const lastComplete = item => lastOf(complete(item));
  const partialRow = item => lastOf(rowsOf(item).filter(row => row.partial));
  const displaySector = v => String(v || 'unclassified').replaceAll('_', ' ').replaceAll(' | ', ' / ').split(' ').map(w => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(' ');
  const defaultSector = () => (industries()[0] || {}).sector || '';
  function money(value, tenths) { // same rounding as the page's money(); tenths keeps a trailing .0 ("$37.0M") for headline figures
    const n = Number(value) || 0, a = Math.abs(n);
    let amount, suffix;
    if (a >= 1e9) { amount = n / 1e9; suffix = 'B'; } else if (a >= 1e6) { amount = n / 1e6; suffix = 'M'; } else if (a >= 1e3) { amount = n / 1e3; suffix = 'k'; } else return `$${num(Math.round(n))}`;
    let text = amount.toFixed(1);
    if (!tenths && text.endsWith('.0')) text = text.slice(0, -2);
    return `$${text}${suffix}`;
  }
  function legacyCard(sector) {
    return [...doc.querySelectorAll('#industries .industry-card[data-sector]')].find(card => card.dataset.sector === sector) || null;
  }
  function colorOf(sector) { // by reference: the page's own .sector-dot.dot-N colour for this industry
    if (S.colors[sector]) return S.colors[sector];
    const card = legacyCard(sector);
    let dot = card && card.querySelector('.sector-dot'), probe = null;
    if (!dot) {
      const index = Math.max(0, industries().findIndex(item => item.sector === sector));
      probe = doc.createElement('span');
      probe.className = `sector-dot dot-${index % 6}`;
      probe.hidden = true;
      doc.body.appendChild(probe);
      dot = probe;
    }
    const colour = getComputedStyle(dot).backgroundColor || '';
    if (probe) probe.remove();
    return (S.colors[sector] = colour);
  }
  const colorStyle = sector => `--ind-c:${esc(colorOf(sector))}`;
  function partialLabel(row) { // the quarter is data (row.through_quarter, from the quarter rows); the page's own text is only the fallback for an older payload
    let quarter = /^Q[1-4]$/.test(row.through_quarter || '') ? row.through_quarter : '';
    if (!quarter) {
      const source = [doc.querySelector('#industries .industry-card .partial'), doc.querySelector('.hero .banner')].map(node => (node ? node.textContent : '')).join(' ');
      const match = /through\s+(Q[1-4])/i.exec(source);
      quarter = match ? match[1].toUpperCase() : '';
    }
    return quarter ? `${row.year} through ${quarter}` : `${row.year} so far (partial year)`;
  }
  // In-house expenses and fees reported by outside firms are two series, never added: an in-house filer reports what it
  // pays outside firms, so a sum counts those dollars twice. Every chart and figure below keeps them apart.
  const SERIES = [['in_house', 'In-house lobbying', 'i'], ['hired_firms', 'Fees reported by outside firms', 'h']];
  const val = (row, field) => Number(row && row[field]) || 0;
  function orgCounts() { // organizations lobbying each bill, across every company on the page
    if (S.orgCount) return S.orgCount;
    const counts = new Map();
    for (const company of S.data.companies || []) {
      const seen = new Set();
      for (const bill of company.bills || []) if (bill && bill.key && !seen.has(bill.key)) { seen.add(bill.key); counts.set(bill.key, (counts.get(bill.key) || 0) + 1); }
    }
    return (S.orgCount = counts);
  }
  function topBills(sector, limit = 4) {
    if (S.bills[sector]) return S.bills[sector];
    const counts = orgCounts(), meta = S.data.companyBills || {}, keys = new Set();
    for (const company of S.data.companies || []) if (company.sector === sector) for (const bill of company.bills || []) if (bill && bill.key) keys.add(bill.key);
    const rows = [...keys].filter(key => meta[key]).map(key => ({ ...meta[key], key, n: counts.get(key) || 0 }))
      .sort((a, b) => b.n - a.n || String(a.key).localeCompare(String(b.key)));
    return (S.bills[sector] = { total: rows.length, rows: rows.slice(0, limit) });
  }
  function canTier(item) {
    const meta = S.data && S.data.tierMeta;
    return !!(item && meta && meta.enabled && (meta.sectors || []).includes(item.sector) && rowsOf(item).length && rowsOf(item).every(row => row.tiers) && typeof window.tierChart === 'function');
  }

  // ---- charts -----------------------------------------------------------------------------------------------------
  function nice(max, target = 6) {
    if (!(max > 0)) return { top: 1, step: 1 };
    const raw = max / target, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw * (1 - 1e-9));
    return { top: Math.ceil(max / step - 1e-9) * step, step };
  }
  function axes(W, H, L, R, T, B, top, step) {
    const plotH = H - T - B, y = v => T + plotH - plotH * v / top;
    let s = '';
    for (let i = 0; i * step <= top * (1 + 1e-9); i++) {
      const v = i * step, yy = y(v).toFixed(1);
      s += `<line class="ind-gl" x1="${L}" y1="${yy}" x2="${W - R}" y2="${yy}"/><line class="ind-ax" x1="${L - 5}" y1="${yy}" x2="${L}" y2="${yy}"/>`
        + `<text class="ind-yt" x="${L - 9}" y="${(+yy + 5).toFixed(1)}" text-anchor="end">${esc(money(v))}</text>`;
    }
    s += `<line class="ind-ax" x1="${L}" y1="${T}" x2="${L}" y2="${T + plotH}"/>`;
    return { s, y, plotH };
  }
  function barChart(item, W) { // grouped by year: in-house (industry colour) beside fees reported by outside firms (tint), one bar and one label each; partial year hatched
    const rows = rowsOf(item), H = 212, R = 2, T = 22, B = 30, gap = 3;
    const max = Math.max(0, ...rows.flatMap(row => SERIES.map(([field]) => val(row, field)))); // the axis fits the larger single series, never their sum
    const { top, step } = nice(max), L = Math.round(14 + 8.2 * money(top).length);
    const ax = axes(W, H, L, R, T, B, top, step), base = T + ax.plotH;
    const col = (W - L - R) / (rows.length || 1), bw = Math.min((col * 0.94 - gap) / 2, 56), id = `ind-hatch-${++uid}`;
    const pattern = (suffix, cls) => `<pattern id="${id}${suffix}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect class="${cls}" width="7" height="7"/><line class="ind-hl" x1="0" y1="0" x2="0" y2="7"/></pattern>`;
    let marks = '';
    rows.forEach((row, i) => {
      const partial = !!row.partial, year = partial ? `${row.year} partial` : String(row.year), x0 = L + i * col + (col - (2 * bw + gap)) / 2;
      SERIES.forEach(([field, name, kind], k) => {
        const value = val(row, field), h = ax.plotH * value / top, x = x0 + k * (bw + gap), text = money(value);
        const fill = partial ? ` fill="url(#${id}${kind})"` : ` class="ind-seg-${kind}"`;
        if (h > 0) marks += `<rect x="${x.toFixed(1)}" y="${(base - h).toFixed(2)}" width="${bw.toFixed(1)}" height="${h.toFixed(2)}"${fill}><title>${esc(year)} · ${esc(name)} · ${esc(text)}</title></rect>`;
        if (text.length * 6.2 <= bw + gap) marks += `<text class="ind-lab" x="${(x + bw / 2).toFixed(1)}" y="${(base - h - 5).toFixed(1)}" text-anchor="middle" aria-hidden="true">${esc(text)}</text>`; // a label wider than the bar-centre spacing (bw + gap, about 6.2px a character) would touch its neighbour, so it is left to the <title>
      });
      marks += `<text class="ind-xt" x="${(L + (i + 0.5) * col).toFixed(1)}" y="${H - 8}" text-anchor="middle">${esc(year)}</text>`;
    });
    const label = `${displaySector(item.sector)} lobbying by year: in-house lobbying beside fees reported by outside firms, two separate series that are never added. `
      + rows.map(row => `${row.year}${row.partial ? ' partial year' : ''}: in-house ${money(row.in_house)}, outside-firm fees ${money(row.hired_firms)}`).join('; ');
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(label)}"><defs>${pattern('h', 'ind-seg-h')}${pattern('i', 'ind-seg-i')}</defs>${ax.s}${marks}</svg>`;
  }
  function linePanel(list, field, name, W) { // one Compare-all panel: one line per industry for ONE series on its own axis; the partial year dashed
    const H = 176, R = 2, T = 8, B = 30;
    const years = [...new Set(list.flatMap(item => rowsOf(item).map(row => row.year)))].sort((a, b) => a - b);
    const partialYears = new Set(list.flatMap(item => rowsOf(item).filter(row => row.partial).map(row => row.year)));
    const max = Math.max(0, ...list.flatMap(item => rowsOf(item).map(row => val(row, field))));
    const { top, step } = nice(max), L = Math.round(14 + 8.2 * money(top).length);
    const ax = axes(W, H, L, R, T, B, top, step), col = (W - L - R) / (years.length || 1);
    const X = year => L + (years.indexOf(year) + 0.5) * col;
    let marks = '';
    for (const item of list) {
      const sector = displaySector(item.sector), rows = rowsOf(item), full = rows.filter(row => !row.partial), last = lastOf(full);
      const pt = row => [X(row.year).toFixed(1), ax.y(val(row, field)).toFixed(1)];
      marks += `<g style="${colorStyle(item.sector)}">`;
      if (full.length > 1) marks += `<polyline class="ind-line" style="stroke:var(--ind-c)" points="${full.map(row => pt(row).join(',')).join(' ')}"/>`;
      if (last) rows.filter(row => row.partial).forEach(row => { const a = pt(last), b = pt(row); marks += `<line class="ind-line ind-line--partial" style="stroke:var(--ind-c)" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`; });
      rows.forEach(row => { const p = pt(row); marks += `<circle class="ind-pt" style="fill:var(--ind-c)" cx="${p[0]}" cy="${p[1]}" r="3.5"><title>${esc(sector)} · ${esc(row.year)}${row.partial ? ' partial' : ''} · ${esc(name)} · ${esc(money(val(row, field)))}</title></circle>`; });
      marks += '</g>';
    }
    years.forEach(year => { marks += `<text class="ind-xt" x="${X(year).toFixed(1)}" y="${H - 8}" text-anchor="middle">${esc(year)}${partialYears.has(year) ? ' partial' : ''}</text>`; });
    const label = `${name} by year, one line per industry on a shared axis. `
      + list.map(item => `${displaySector(item.sector)}: ` + rowsOf(item).map(row => `${row.year}${row.partial ? ' partial' : ''} ${money(val(row, field))}`).join(', ')).join('; ');
    return `<figure class="ind-panel"><figcaption class="ind-paneltitle">${esc(name)}</figcaption><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(label)}">${ax.s}${marks}</svg></figure>`;
  }
  function lineChart(W) { // Compare all: two titled panels, in-house and fees reported by outside firms, never one summed line
    const list = industries();
    return SERIES.map(([field, name]) => linePanel(list, field, name, W)).join('');
  }
  function spark(item, W, field, name) { // one series, complete years only (2021–2025 today), own min–max scale per card
    const rows = complete(item);
    if (!rows.length) return '';
    const H = 72, x0 = 6, x1 = W - 8, top = 4, bottom = 42, axisY = 52;
    const values = rows.map(row => val(row, field)), lo = Math.min(...values), hi = Math.max(...values);
    const X = i => (rows.length === 1 ? (x0 + x1) / 2 : x0 + i * (x1 - x0) / (rows.length - 1));
    const Y = v => (hi === lo ? (top + bottom) / 2 : bottom - (bottom - top) * (v - lo) / (hi - lo));
    let s = `<line class="ind-gl" x1="${x0}" y1="${axisY}" x2="${x1}" y2="${axisY}"/>`;
    rows.forEach((row, i) => { const x = X(i).toFixed(1); s += `<line class="ind-ax" x1="${x}" y1="${axisY}" x2="${x}" y2="${axisY + 4}"/><text class="ind-sparkx" x="${x}" y="${H - 4}" text-anchor="middle">${esc(row.year)}</text>`; });
    s += `<polyline class="ind-line" points="${rows.map((row, i) => `${X(i).toFixed(1)},${Y(values[i]).toFixed(1)}`).join(' ')}"/>`;
    s += rows.map((row, i) => `<circle class="ind-pt" cx="${X(i).toFixed(1)}" cy="${Y(values[i]).toFixed(1)}" r="2.6"/>`).join('');
    const label = `${displaySector(item.sector)} ${name.toLowerCase()} ${rows[0].year}–${lastOf(rows).year}: ` + rows.map(row => `${row.year} ${money(val(row, field))}`).join('; ');
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(label)}">${s}</svg>`;
  }
  const SPARK_NAMES = { in_house: 'In-house', hired_firms: 'Outside-firm fees' };
  function sparkPair(item, W) { // two small sparklines side by side, one labelled per series, each on its own scale
    const half = Math.max(70, Math.floor((W - 12) / 2));
    const cols = SERIES.map(([field, name, kind]) => {
      const svg = spark(item, half, field, name);
      return svg ? `<figure class="ind-sparkcol ind-sparkcol--${kind}"><figcaption class="ind-sparklabel">${esc(SPARK_NAMES[field])}</figcaption>${svg}</figure>` : '';
    }).join('');
    return cols ? `<div class="ind-sparkpair">${cols}</div>` : '';
  }
  function tierBlock(item) { // "By tier" (Bank): today's tier breakdown from the page's own tierChart()
    let hired, inHouse;
    try {
      hired = window.tierChart(item, 'hired_firms', 'Outside-firm fees by tier');
      inHouse = window.tierChart(item, 'in_house', 'In-house by tier');
    } catch (err) {
      console.warn('industry-ui: tier breakdown', err);
      return '<p class="mlk-state mlk-state--error">The tier breakdown could not be drawn.</p>';
    }
    const card = legacyCard(item.sector), legend = card && card.querySelector('.tierlegend'), note = card && card.querySelector('.tiernote');
    return `<div class="ind-tiers"><div class="ind-tiercol"><h4 class="ind-tiertitle">Outside-firm fees by tier</h4>${hired}</div><div class="ind-tiercol"><h4 class="ind-tiertitle">In-house by tier</h4>${inHouse}</div></div>`
      + `<div class="ind-tierfoot">${legend ? legend.outerHTML : ''}${note ? note.outerHTML : ''}</div>`;
  }

  // ---- render -----------------------------------------------------------------------------------------------------
  function position(marking) {
    if (!marking || !POS[marking.state]) return '<span class="ind-none" aria-label="No position on file">—</span>';
    const [shape, label] = POS[marking.state], category = marking.category ? ` · ${esc(marking.category)}` : '';
    return `<span class="ind-pos is-${esc(marking.state)}"><span class="ind-pos__shape" aria-hidden="true">${shape}</span>${label}${category}</span>`;
  }
  function billsBlock(item) {
    const who = PLURAL[item.sector] || `${displaySector(item.sector)} organizations`, { rows, total } = topBills(item.sector);
    const head = `<div class="ind-billshead"><h3 class="ind-label" id="ind-bills-label">Bills ${esc(who)} lobbied</h3></div>`;
    if (!rows.length) return `${head}<p class="mlk-state">No bills lobbied by these organizations.</p>`;
    return `${head}<div class="mlk-tablecard ind-billscard"><table class="mlk-table ind-bills" aria-labelledby="ind-bills-label">`
      + `<caption class="ind-sr">Top ${rows.length} of ${num(total)} bills, by organizations lobbying each bill</caption>`
      + '<colgroup><col style="width:13.5%"><col style="width:36.6%"><col style="width:15%"><col style="width:17.8%"><col></colgroup>'
      + '<thead><tr><th scope="col">Bill</th><th scope="col">Title</th><th scope="col">Congress</th><th scope="col">Organizations</th><th scope="col">CU position</th></tr></thead><tbody>'
      + rows.map(row => `<tr data-bill="${esc(row.key)}"><td class="ind-bill">${esc(row.bill || row.key)}</td><td>${esc(row.title || '—')}</td><td>${esc(row.congress)}</td><td>${num(row.n)}</td><td>${position(row.marking)}</td></tr>`).join('')
      + '</tbody></table></div>';
  }
  function placeSitenav() { // today's "Open bill explorer" link, moved (listeners and routing kept)
    if (!S.sitenav) return;
    const slot = els.focus.querySelector('.ind-billshead') || els.focus;
    if (S.sitenav.parentNode !== slot) slot.appendChild(S.sitenav);
  }
  function renderFocus() {
    const el = els.focus, item = bySector(S.focus);
    el.setAttribute('aria-busy', 'false');
    el.style.setProperty('--ind-c', colorOf(S.focus));
    if (!item) { el.innerHTML = '<p class="mlk-state">No lobbying spend recorded for this industry.</p>'; placeSitenav(); return; }
    const done = lastComplete(item), part = partialRow(item), counted = done || part, tierable = canTier(item);
    if (!tierable) S.byTier = false;
    const figure = (value, text) => `<div class="ind-fig"><strong class="ind-big">${esc(money(value, true))}</strong><span class="ind-figlabel">${esc(text)}</span></div>`;
    const figs = '<div class="ind-figs">'
      + (done ? figure(done.in_house, `${done.year} in-house lobbying`) + figure(done.hired_firms, `${done.year} fees reported by outside firms`)
        : '<div class="ind-fig"><strong class="ind-big">—</strong><span class="ind-figlabel">No complete year on file</span></div>')
      + '</div>'
      + (part ? `<p class="ind-partialline">${esc(partialLabel(part))}: in-house ${esc(money(part.in_house, true))} · outside-firm fees ${esc(money(part.hired_firms, true))}</p>` : '');
    const left = S.compare ? '<button type="button" class="ind-textbtn" data-ind-back>Back to focus</button>'
      : tierable ? `<button type="button" class="mlk-chip mlk-chip--toggle ind-chip" data-ind-bytier aria-pressed="${S.byTier}">By tier</button>` : '';
    const legend = S.compare ? industries().map(other => `<span class="ind-key ind-c" style="${colorStyle(other.sector)}"><i aria-hidden="true"></i>${esc(displaySector(other.sector))}</span>`).join('')
      : S.byTier ? '' : '<span class="ind-key"><i aria-hidden="true"></i>In-house lobbying</span><span class="ind-key ind-key--h"><i aria-hidden="true"></i>Fees reported by outside firms</span>';
    el.innerHTML = `<h2 class="ind-ftitle" id="ind-focus-title" tabindex="-1"><span class="ind-dot" aria-hidden="true"></span>${esc(displaySector(item.sector))}</h2>`
      + (counted ? `<p class="ind-fsub">${esc(done ? done.year : partialLabel(counted))}: ${esc(plural(counted.orgs, 'organization'))} · ${esc(num(counted.lobbyists))} named lobbyists</p>` : '') // counts come from the headline figure's year, never the partial year beside full-year dollars
      + figs + `<div class="ind-chartbar">${left}<div class="ind-legend">${legend}</div></div><div class="ind-chart" id="ind-chart"></div>` + billsBlock(item);
    placeSitenav();
    drawChart();
  }
  function drawChart() {
    const box = doc.getElementById('ind-chart');
    if (!box) return;
    const item = bySector(S.focus), W = Math.max(280, Math.floor(box.clientWidth || 0) || 800);
    box.dataset.w = String(W);
    if (S.compare) box.innerHTML = lineChart(W);
    else if (S.byTier && canTier(item)) box.innerHTML = tierBlock(item);
    else if (!rowsOf(item).length) box.innerHTML = `<p class="mlk-state">No lobbying spend recorded for ${esc(displaySector(S.focus))}.</p>`;
    else box.innerHTML = barChart(item, W);
  }
  function renderOthers() {
    const others = industries().filter(item => item.sector !== S.focus);
    const year = Math.max(0, ...others.map(item => (lastComplete(item) || {}).year || 0)); // one year for the sort key and for every card figure
    const at = item => complete(item).find(row => row.year === year) || null;
    others.sort((a, b) => val(at(b), 'in_house') - val(at(a), 'in_house') || displaySector(a.sector).localeCompare(displaySector(b.sector)));
    els.cards.innerHTML = others.length ? (year ? `<p class="ind-sortnote">Sorted by ${year} in-house lobbying</p>` : '') + others.map(item => {
      const done = at(item), name = displaySector(item.sector), id = `ind-card-${esc(item.sector)}`;
      return `<article class="ind-card ind-c" data-sector="${esc(item.sector)}" style="${colorStyle(item.sector)}" aria-labelledby="${id}">`
        + `<div class="ind-cardhead"><h3 class="ind-cardname" id="${id}"><span class="ind-dot" aria-hidden="true"></span>${esc(name)}</h3>`
        + `<button type="button" class="ind-textbtn" data-ind-focus="${esc(item.sector)}" aria-label="Focus ${esc(name)}">Focus</button></div>`
        + `<div class="ind-cardfigs"><strong class="ind-cardfig">${done ? esc(money(done.in_house, true)) : '—'}</strong>${done ? `<span class="ind-cardfee">${esc(money(done.hired_firms, true))} outside-firm fees</span>` : ''}</div>`
        + `<span class="ind-sr">${done ? `${esc(done.year)} in-house lobbying` : 'No complete year on file'}</span>`
        + `<div class="ind-spark" data-sector="${esc(item.sector)}"></div></article>`;
    }).join('') : '<p class="mlk-state">No other industries.</p>';
    drawSparks();
    const count = industries().length;
    els.compareAll.textContent = count === 4 ? 'Compare all four' : `Compare all ${count}`;
    els.compareAll.setAttribute('aria-pressed', String(S.compare));
    els.compareAll.hidden = count < 2;
  }
  function drawSparks() {
    els.cards.querySelectorAll('.ind-spark').forEach(box => {
      const W = Math.max(160, Math.floor(box.clientWidth || 0) || 288);
      box.dataset.w = String(W);
      box.innerHTML = sparkPair(bySector(box.dataset.sector), W) || '<p class="ind-none">No complete years on file.</p>';
    });
  }
  function redraw() {
    redrawQueued = false;
    if (!els || !S.data || S.tab !== 'overview') return;
    const box = doc.getElementById('ind-chart'), stale = node => node && node.clientWidth && Math.abs(Math.max(280, node.clientWidth) - (Number(node.dataset.w) || 0)) > 2;
    if (stale(box)) drawChart();
    if ([...els.cards.querySelectorAll('.ind-spark')].some(node => node.clientWidth && Math.abs(Math.max(160, node.clientWidth) - (Number(node.dataset.w) || 0)) > 2)) drawSparks();
  }
  const queueRedraw = () => { if (!redrawQueued) { redrawQueued = true; requestAnimationFrame(redraw); } };

  // ---- states -----------------------------------------------------------------------------------------------------
  const SKEL = '<div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span></div>';
  function showLoading() {
    els.focus.setAttribute('aria-busy', 'true');
    els.focus.innerHTML = `<p class="mlk-state" role="status">Loading industry spend…</p>${SKEL.repeat(6)}`;
    els.cards.innerHTML = SKEL.repeat(3);
    els.compareAll.hidden = true;
  }
  function showError() {
    els.focus.setAttribute('aria-busy', 'false');
    els.focus.innerHTML = '<p class="mlk-state mlk-state--error" role="alert">Industry spend could not be read.</p><button type="button" class="mlk-btn ind-retry" data-ind-retry>Retry</button>';
    els.cards.innerHTML = '';
    els.compareAll.hidden = true;
    placeSitenav();
  }
  function showEmpty() {
    els.focus.setAttribute('aria-busy', 'false');
    els.focus.innerHTML = '<p class="mlk-state">No lobbying spend recorded for any industry.</p>';
    els.cards.innerHTML = '<p class="mlk-state">No other industries.</p>';
    els.compareAll.hidden = true;
    placeSitenav();
  }

  // ---- organizations (build 11) -----------------------------------------------------------------------------------
  const EMD = '—';
  const OTABS = [['bills', 'Bills lobbied'], ['members', 'Members'], ['champions', 'Champion detail'], ['mentions', 'Mentions']];
  const PARTY = { D: 'Democrat', R: 'Republican', I: 'Independent' };
  const O = { id: null, sub: 'bills', q: '', sector: '', page: 0, size: 25, pages: {}, postColors: {}, party: null, kitReady: false, overviewInfo: null, legacyInfo: null, userMoved: false, hold: null, catalog: null, byName: null, nameIds: null, relink: null };
  const companies = () => (S.data && Array.isArray(S.data.companies) ? S.data.companies : []);
  const companyById = id => (id ? companies().find(item => item.id === id) || null : null);
  const minus = v => String(v ?? '').replace(/^-/, '−');
  const signed = v => `${Number(v) > 0 ? '+' : ''}${num(v)}`;
  const pageFn = name => (typeof window[name] === 'function' ? window[name] : null); // the page's own function declarations
  function pageYears(count) {
    const years = S.data && Array.isArray(S.data.years) && S.data.years.length ? S.data.years.slice() : [];
    while (years.length < (count || 0)) years.push((years.length ? years[years.length - 1] : 2020) + 1);
    return years;
  }
  function isPartial(year) { return industries().some(item => rowsOf(item).some(row => row.partial && row.year === year)); }
  const yearLabel = year => (isPartial(year) ? `${year} partial` : String(year));
  function windowText() { const ys = pageYears(); return ys.length ? `${ys[0]}–${ys[ys.length - 1]}` : ''; }
  function postureColor(bucket) { // by reference: the page's own .postbadge.<bucket> .pdot colour
    if (O.postColors[bucket] !== undefined) return O.postColors[bucket];
    const probe = doc.createElement('span');
    probe.className = `postbadge ${bucket}`;
    probe.innerHTML = '<span class="pdot"></span>';
    probe.hidden = true;
    doc.body.appendChild(probe);
    const colour = getComputedStyle(probe.firstChild).backgroundColor || '';
    probe.remove();
    return (O.postColors[bucket] = colour);
  }
  function posture(p, { delta = false } = {}) {
    const states = (S.data && S.data.postureStates) || [], st = Array.isArray(p) ? states[p[0]] : null;
    if (!st) return `<span class="ind-none">${EMD}</span>`;
    let mode = 'posture';
    try { if (typeof postureMode !== 'undefined') mode = postureMode; } catch (err) { /* the page default */ }
    let title = '';
    try { const fn = pageFn('postureDetail'); if (fn) title = fn(p); } catch (err) { title = ''; }
    const pillHtml = `<span class="mlk-pill ind-pill${st.n === 'adversarial' ? ' ind-pill--adv' : ''}" style="--mlk-dot:${esc(postureColor(st.b))}"${title ? ` title="${esc(title)}"` : ''}>${esc(mode === 'posture' ? st.p : st.t)}</span>`;
    return delta ? `<span class="ind-post">${pillHtml}<span class="ind-postdelta">${esc(minus(p[4]))}</span></span>` : pillHtml;
  }
  function tierText(tiers, short) {
    if (!Array.isArray(tiers) || !tiers.length) return '';
    const meta = (S.data && S.data.tierMeta) || {}, labels = meta.labels || {}, shorts = meta.short || {};
    return tiers.length > 1 || short ? tiers.map(t => shorts[t] || labels[t] || t).join(' / ') : (labels[tiers[0]] || shorts[tiers[0]] || tiers[0]);
  }
  const sectorName = sector => (sector ? displaySector(sector) : 'Not matched to the sector roster');
  function champParty() { // party letters carried by the champion rows (covers former members the catalog lacks)
    if (O.party) return O.party;
    O.party = new Map();
    for (const item of industries()) for (const org of item.organizations || []) for (const row of org.rows || []) if (row && row.i && row.p && !O.party.has(row.i)) O.party.set(row.i, String(row.p));
    return O.party;
  }
  function partyOf(bioguide) {
    const K = kit();
    let row = null;
    try { row = K && K.memberByIdSync ? K.memberByIdSync(bioguide) : null; } catch (err) { row = null; }
    if (row && row.party) return String(row.party);
    const letter = champParty().get(bioguide);
    return letter ? (PARTY[letter.toUpperCase()] || letter) : '';
  }
  function partyDot(bioguide) {
    const K = kit(), party = partyOf(bioguide), letter = party ? party[0].toUpperCase() : '?';
    let colour = '';
    try { colour = K && K.partyColor ? K.partyColor(party || '?') : ''; } catch (err) { colour = ''; }
    const label = party || 'Party not on file';
    return `<span class="ind-party${party ? '' : ' ind-party--none'}"${colour ? ` style="--ind-party:${esc(colour)}"` : ''} title="${esc(label)}"><span aria-hidden="true">${esc(letter)}</span><span class="ind-sr">${esc(label)}</span></span>`;
  }
  function profileHref(name) {
    try { const K = kit(); if (K && K.url) return K.url('profile', { m: name, tab: 'overview' }); } catch (err) { /* fall through */ }
    return `/?view=profile&m=${encodeURIComponent(name)}&tab=overview`;
  }
  // Fix round 2 (MEL-DBG-1): the member desk finds a member by its own name only, and a row's lobbying (LDA) name can differ
  // ("William R. Timmons IV" is "William Timmons" in the desk). A member is resolved by bioguide through the kit's catalog,
  // as interactions-ui.js does; the link carries the desk's name and the row keeps its stored label. A member the desk does
  // not carry stays plain text (the page's own rule for former/unresolved names). Before the catalog has loaded, the name
  // link is kept, and whenKit() re-renders once it is in.
  function catalogByName() { // desk names, case-insensitive (the desk's own address rule); a name two members share is left out
    if (O.byName) return O.byName;
    const map = new Map(), dup = new Set();
    for (const row of (O.catalog && O.catalog.members) || []) {
      const key = String((row && row.name) || '').toLowerCase();
      if (!key) continue;
      if (map.has(key)) dup.add(key); else map.set(key, row);
    }
    for (const key of dup) map.delete(key);
    return (O.byName = map);
  }
  function deskMember(bioguide, name) { // the catalog row for a bioguide; by desk name only when no bioguide is known
    if (bioguide) {
      const K = kit();
      try { return (K && K.memberByIdSync ? K.memberByIdSync(bioguide) : null) || null; } catch (err) { return null; }
    }
    return name && O.catalog ? catalogByName().get(String(name).toLowerCase()) || null : null;
  }
  function memberCell(name, bioguide, flag) { // same rule as the page's memberLink: former/unresolved names stay plain text
    const text = esc(name ?? '');
    if (!text) return EMD;
    if (flag) { let badge = ''; try { const fn = pageFn('memberFlag'); badge = fn ? fn(flag) : ''; } catch (err) { badge = ''; } return `<span class="ind-mname">${text}</span>${badge}`; }
    if (bioguide != null && String(name) === String(bioguide)) return text;
    const cat = deskMember(bioguide, name);
    if (cat && cat.name) return `<a class="mlk-name" data-mlk-nav href="${esc(profileHref(cat.name))}" title="Open ${esc(cat.name)} in the member desk">${text}</a>`;
    if (O.catalog) return `<span class="ind-mname">${text}</span>`; // not in the member desk
    return `<a class="mlk-name" data-mlk-nav href="${esc(profileHref(name))}" title="Open ${text} in the member desk">${text}</a>`;
  }
  const LEGACY_DESK = 'a[href^="cu-member-desk.html?m="]'; // the page's memberLink(): cu-member-desk.html?m=<lobbying name>
  const BIOGUIDE = /^[A-Z]\d{6}$/;
  function legacyIds() { // lobbying name -> bioguide from #page-data's own rows, read once; a name with two bioguides is left out
    if (O.nameIds) return O.nameIds;
    const seen = new Map(), add = (name, id) => {
      if (typeof name !== 'string' || !name || !BIOGUIDE.test(String(id || ''))) return;
      const key = name.toLowerCase();
      if (!seen.has(key)) seen.set(key, new Set());
      seen.get(key).add(String(id));
    };
    const PAIRS = [['name', 'id'], ['n', 'i'], ['member', 'bioguide'], ['name', 'bioguide']];
    const walk = (value, depth) => {
      if (!value || typeof value !== 'object' || depth > 10) return;
      if (Array.isArray(value)) { for (const item of value) walk(item, depth + 1); return; }
      for (const [a, b] of PAIRS) if (a in value && b in value) add(value[a], value[b]);
      for (const item of Object.values(value)) if (item && typeof item === 'object') walk(item, depth + 1);
    };
    walk(S.data, 0);
    for (const [id, who] of Object.entries((S.data && S.data.companyMembers) || {})) if (who) add(who.name, id);
    O.nameIds = new Map([...seen].filter(([, ids]) => ids.size === 1).map(([key, ids]) => [key, [...ids][0]]));
    return O.nameIds;
  }
  function rowBioguide(a) { // the row's own "Open filings" link (the page's interactionLink(): ...&b=<bioguide>), when the row holds one member
    const row = a.closest('tr');
    if (!row || row.querySelectorAll(LEGACY_DESK).length !== 1) return null;
    for (const link of row.querySelectorAll('a[href*="b="]')) {
      let id = null;
      try { id = new URL(link.getAttribute('href'), location.href).searchParams.get('b'); } catch (err) { id = null; }
      if (id && BIOGUIDE.test(id)) return id;
    }
    return null;
  }
  function relinkLegacy() { // the page's own member links (Overview folds, and every re-render of them) get the same rule as memberCell()
    if (!O.catalog || !S.data) return;
    const links = doc.querySelectorAll(LEGACY_DESK);
    if (!links.length) return;
    const ids = legacyIds();
    for (const a of links) {
      let name = '';
      try { name = new URLSearchParams(a.getAttribute('href').split('?').slice(1).join('?')).get('m') || ''; } catch (err) { name = ''; }
      const id = rowBioguide(a) || ids.get(name.toLowerCase()) || null;
      const cat = deskMember(id, id ? '' : name);
      if (cat && cat.name) {
        a.setAttribute('href', profileHref(cat.name));
        a.setAttribute('data-mlk-nav', '');
        a.title = `Open ${cat.name} in the member desk`;
      } else a.replaceWith(doc.createTextNode(a.textContent)); // not in the member desk: plain text, as the page renders former members
    }
  }
  function watchLegacy() { // the page re-renders its tables (sector, posture wording, "Show all"); relink before they paint
    if (O.relink || !window.MutationObserver || !doc.body) return;
    O.relink = new MutationObserver(() => { try { relinkLegacy(); } catch (err) { console.warn('industry-ui: member links could not be resolved', err); } });
    O.relink.observe(doc.body, { childList: true, subtree: true });
  }
  function filingsCell(company, bioguide) { // the page's interactionLink(): ledger addressed by sector + bioguide
    if (!bioguide || !bySector(company.sector)) return EMD;
    const href = '/?' + new URLSearchParams({ view: 'interactions', sector: company.sector, b: bioguide });
    return `<a class="ind-link" data-mlk-nav href="${esc(href)}" title="Every disclosed filing behind this count">Open filings</a>`;
  }
  function orgHref(id) {
    try { const K = kit(); if (K && K.url) return K.url('industry', { tab: 'organizations', org: id }); } catch (err) { /* fall through */ }
    return `/?view=industry&tab=organizations&org=${encodeURIComponent(id)}`;
  }
  function billRows(company) {
    const meta = (S.data && S.data.companyBills) || {};
    return (company.bills || []).map(link => ({ link, bill: meta[link.key] })).filter(row => row.bill);
  }
  function channelText(channels) {
    let labels = {};
    try { if (typeof CHANNEL_LABELS !== 'undefined') labels = CHANNEL_LABELS; } catch (err) { labels = {}; }
    return (channels || []).map(channel => labels[channel] || channel).join(', ') || EMD;
  }
  function champModel(company) {
    const item = bySector(company.sector);
    return item ? (item.organizations || []).find(org => org.id === company.id) || null : null;
  }
  function mentionsOf(company) {
    const om = (S.data && S.data.orgMentions) || {};
    return { built: !!om.built, note: om.note || '', channels: om.channels || ['press', 'tweet', 'floor', 'interview', 'bluesky', 'x'], item: (om.companies || {})[company.id] || null };
  }
  function orgCounts2(company) {
    const model = champModel(company), m = mentionsOf(company);
    return {
      bills: billRows(company).length,
      members: Number.isFinite(Number(company.memberTotal)) ? Number(company.memberTotal) : (company.members || []).length,
      champions: model ? (model.rows || []).length : 0,
      mentions: m.built && m.item ? (m.item.members || []).length : 0,
    };
  }
  const MENTION_LABELS = { press: 'Press', tweet: 'Tweets', floor: 'Floor', interview: 'Interviews', bluesky: 'Bluesky', x: 'X' };
  function table(cls, caption, cols, body) {
    return `<div class="mlk-tablecard ind-otablecard"><table class="mlk-table ind-otable ${cls}"><caption class="ind-sr">${esc(caption)}</caption>`
      + `<colgroup>${cols.map(col => `<col${col.w ? ` style="width:${col.w}"` : ''}>`).join('')}</colgroup>`
      + `<thead><tr>${cols.map(col => `<th scope="col"${col.num ? ' class="mlk-num"' : ''}${col.sort ? ` aria-sort="${col.sort}"` : ''}>${esc(col.label)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function paged(key, rows) { // Previous/Next only when a table is longer than one page
    const K = kit(), st = O.pages[key] || (O.pages[key] = { page: 0, size: 25 });
    if (rows.length <= st.size || !K || !K.pageSlice) return { rows, pager: '' };
    const slice = K.pageSlice(rows, st.page, st.size);
    st.page = slice.page;
    return { rows: slice.rows, start: slice.start, pager: `<div class="ind-pager" data-ind-pager="${esc(key)}">${K.pager({ total: rows.length, page: slice.page, size: st.size })}</div>` };
  }
  function billsPanel(company) {
    const all = billRows(company);
    if (!all.length) return '<p class="mlk-state">No bills in the complete lobbying payload for this organization.</p>';
    const { rows, pager } = paged('bills', all);
    const body = rows.map(({ link, bill }) => { // MATCH holds one channel label on one line (MD-5); a multi-channel link keeps both labels, clamped to two lines
      const match = channelText(link.channels), multi = (link.channels || []).length > 1;
      return `<tr><td class="ind-bill">${esc(bill.bill || link.key)}</td><td>${esc(bill.congress)}</td>`
        + `<td class="ind-ttl" title="${esc(bill.title || 'Title unavailable')}"><span>${bill.title ? esc(bill.title) : EMD}</span></td>`
        + (multi ? `<td class="ind-ttl ind-match" title="${esc(match)}"><span>${esc(match)}</span></td>` : `<td class="ind-match" title="${esc(match)}">${esc(match)}</td>`)
        + `<td>${position(bill.marking)}</td></tr>`;
    }).join('');
    const forecasts = all.filter(row => row.bill.forecast);
    const fc = forecasts.length ? `<p class="ind-onote">Forecast: ${forecasts.map(({ bill }) => `<a class="ind-link" href="lobby-forecast.html#bill=${encodeURIComponent(bill.key)}">${esc(bill.bill)}</a>`).join(' · ')}</p>` : '';
    return table('ind-obills', `${plural(all.length, 'bill')} lobbied by this organization`, [ // TITLE takes the remainder
      { label: 'Bill', w: '14%' }, { label: 'Congress', w: '11.5%' }, { label: 'Title' }, { label: 'Match', w: '232px' }, { label: 'CU position', w: '13.5%' },
    ], body) + pager + fc;
  }
  function membersPanel(company) {
    const rows = company.members || [], people = (S.data && S.data.companyMembers) || {}, ys = pageYears(6), last = ys[ys.length - 1];
    if (!rows.length) return '<p class="mlk-state">No member attention rows for this organization.</p>';
    const total = Number(company.memberTotal) || rows.length;
    let spark = null;
    try { spark = pageFn('lineSpark'); } catch (err) { spark = null; }
    const body = rows.map((row, i) => {
      const who = people[row.i] || {}, c = Number(row.c) || 0;
      let trend = EMD;
      try { if (spark && Array.isArray(row.v)) trend = spark(row.v); } catch (err) { trend = EMD; }
      return `<tr><td class="mlk-num">${i + 1}</td><td class="ind-mcell">${partyDot(row.i)}${memberCell(who.name || row.i, row.i, who.flag)}</td><td>${posture(row.p, { delta: true })}</td>`
        + `<td class="mlk-num">${num(row.n)}</td><td class="mlk-num ${c > 0 ? 'pos' : c < 0 ? 'neg' : 'zero'}">${esc(signed(c))}</td><td class="ind-trend">${trend}</td><td>${filingsCell(company, row.i)}</td></tr>`;
    }).join('');
    const note = total > rows.length ? `Top ${num(rows.length)} of ${num(total)} member rows` : plural(rows.length, 'member row');
    return table('ind-omembers', `Member attention: ${note}`, [
      { label: '#', w: '4.5%', num: true }, { label: 'Member', w: '24%' }, { label: 'Posture', w: '21%' }, { label: windowText(), w: '10.5%', num: true },
      { label: `${last} change`, w: '12.5%', num: true }, { label: 'Trend', w: '13%' }, { label: 'Filings' },
    ], body) + `<p class="ind-onote">${esc(note)}; the page data carries the top ${num(rows.length)}.</p>`;
  }
  function championsPanel(company) {
    const model = champModel(company);
    const all = model ? [...(model.rows || [])].sort((a, b) => a.r - b.r || String(a.i).localeCompare(String(b.i))) : [];
    if (!all.length) return `<p class="mlk-state">${esc((model && model.skipped) || 'No scored champion model')}</p>`;
    const { rows, pager } = paged('champions', all);
    const sc = v => (v === null || v === undefined ? EMD : Number(v).toFixed(3));
    const body = rows.map(row => `<tr><td class="mlk-num">${esc(row.r)}</td><td class="ind-mcell">${partyDot(row.i)}${memberCell(row.n, row.i, row.f)}</td><td>${posture(row.g, { delta: true })}</td>`
      + `<td>${esc(row.p)} / ${esc(row.h)}</td><td class="mlk-num">${esc(sc(row.c))}</td><td class="mlk-num">${esc(sc(row.s))}</td><td class="mlk-num">${num(row.b)}</td>`
      + `<td class="mlk-num">${num(row.y)}</td><td>${esc(row.d)}</td><td>${filingsCell(company, row.i)}</td></tr>`).join('');
    const limit = S.data.receipts && S.data.receipts.champions ? S.data.receipts.champions.leader_limit : '';
    const receipt = `Size receipt: ${num(model.rows.length)} leaders retained from ${num(model.total)} scored members; ${num(model.omitted)} lower-ranked members aggregated, representing ${num(model.omitted_bills)} raw bill-attention counts. Retention is the union of the top ${esc(limit)} raw and org-specific ranks.`;
    return table('ind-ochamps', `Champion model detail: ${plural(all.length, 'leader row')}`, [
      { label: 'Rank', w: '6.4%', num: true }, { label: 'Member', w: '18.9%' }, { label: 'Posture', w: '20.5%' }, { label: 'Party / chamber', w: '8.75%' }, { label: 'Score', w: '6.65%', num: true },
      { label: 'Org score', w: '6.65%', num: true }, { label: 'Bills', w: '5.6%', num: true }, { label: 'Years', w: '6.5%', num: true }, { label: 'Direction', w: '9.5%' }, { label: 'Filings' },
    ], body) + pager + `<p class="ind-onote">${receipt}</p>`;
  }
  function mentionsPanel(company) {
    const m = mentionsOf(company);
    if (!m.built) return `<p class="mlk-state">${esc(m.note || 'Public mentions artifact not built yet.')}</p>`;
    if (!m.item) return '<p class="mlk-state">No public organization mentions were measured for this company.</p>';
    const totals = `<div class="ind-mtotals">${m.channels.map(ch => `<span class="ind-ochip">${esc(MENTION_LABELS[ch] || ch)} <b>${num((m.item.c || {})[ch] || 0)}</b></span>`).join('')}</div>`;
    const rows = m.item.members || [];
    if (!rows.length) return `${totals}<p class="mlk-state">No mentioning-member rows for this organization.</p>`;
    const body = rows.map(row => `<tr><td class="ind-mcell">${partyDot(row.i)}${memberCell(row.n, row.i)}</td>${m.channels.map(ch => `<td class="mlk-num">${num((row.c || {})[ch] || 0)}</td>`).join('')}</tr>`).join('');
    return totals + table('ind-omentions', `Public mentions: ${plural(rows.length, 'member row')}`, [{ label: 'Member', w: '34%' }, ...m.channels.map(ch => ({ label: MENTION_LABELS[ch] || ch, num: true }))], body);
  }
  const PANELS = { bills: billsPanel, members: membersPanel, champions: championsPanel, mentions: mentionsPanel };
  function renderOrgPanel(company) {
    const panel = els.org.querySelector('#ind-opanel');
    if (!panel) return;
    panel.setAttribute('aria-labelledby', `ind-otab-${O.sub}`);
    panel.innerHTML = PANELS[O.sub](company);
    els.org.querySelectorAll('[data-ind-otab]').forEach(tab => {
      const on = tab.dataset.indOtab === O.sub;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    });
  }
  function yearsCard(company) {
    const traj = company.trajectory, head = '<h3 class="ind-label" id="ind-years-label">Attention by year</h3>';
    if (!traj || !Array.isArray(traj.values)) return `${head}<p class="mlk-state">No member attention exists, so no posture trajectory can be calculated.</p>`;
    const values = traj.values.map(v => Number(v) || 0), ys = pageYears(values.length), max = Math.max(1, ...values);
    return head + `<ol class="ind-years">${values.map((v, i) => `<li><span class="ind-yl">${esc(yearLabel(ys[i]))}</span>`
      + `<span class="ind-ybar" aria-hidden="true"><i style="width:${v > 0 ? `max(4px,${(100 * v / max).toFixed(2)}%)` : '0'}"></i></span><span class="ind-yv">${num(v)}</span></li>`).join('')}</ol>`;
  }
  function topCard(company, counts) {
    const rows = (company.members || []).slice(0, 5), people = (S.data && S.data.companyMembers) || {};
    const head = '<h3 class="ind-label" id="ind-top-label">Top members by attention</h3>';
    if (!rows.length) return `${head}<p class="mlk-state ind-topempty">No member attention rows for this organization.</p>`;
    return head + `<ol class="ind-toplist">${rows.map(row => {
      const who = people[row.i] || {};
      return `<li>${partyDot(row.i)}<span class="ind-topname">${memberCell(who.name || row.i, row.i, who.flag)}</span>${posture(row.p)}<span class="ind-topn" title="${esc(windowText())} bill-attention count">${num(row.n)}</span></li>`;
    }).join('')}</ol><button type="button" class="ind-textbtn ind-allmem" data-ind-otab-go="members">All ${num(counts.members)} members</button>`;
  }
  function renderOrg() {
    const el = els.org, company = companyById(O.id);
    el.setAttribute('aria-busy', 'false');
    if (!company) {
      el.innerHTML = `<p class="mlk-state" role="status">No organization matches this link.</p><p><a class="ind-link" href="${esc(orgHref(''))}" data-ind-orgs>All organizations</a></p>`;
      setHeadInfo();
      return;
    }
    const traj = company.trajectory, counts = orgCounts2(company), ys = pageYears();
    const prev = ys.length > 1 ? ys[ys.length - 2] : '', lastYear = ys[ys.length - 1];
    const tier = tierText(company.tier);
    const chips = `<span class="ind-ochip">${esc(sectorName(company.sector))}<span class="ind-sr"> (industry)</span></span>`
      + `<span class="ind-ochip${tier ? '' : ' ind-ochip--muted'}"${company.tier && company.tier.length > 1 ? ' title="Changed size during this period: this family held more than one tier inside the window, so no single tier is asserted"' : ''}>${esc(tier || 'No tier assigned')}</span>`
      + `<span class="ind-ochip" title="Roster activity window">${company.first && company.last ? `${esc(company.first)}–${esc(company.last)}` : 'Roster years unavailable'}</span>`;
    const trajHtml = traj ? `<div class="ind-traj">${posture(traj.posture)}<div class="ind-trajfig"><strong class="ind-delta">${esc(minus(traj.posture && traj.posture[4]))}</strong>`
      + `<span class="ind-trajcap" title="${esc(`${signed(traj.change)} in ${lastYear} partial versus ${prev}`)}">${num(traj.total)} bill-attention counts, ${esc(windowText())}</span></div></div>` : '';
    let boundary = '';
    if ((company.boundaryKeys || []).length) { try { const fn = pageFn('companyBoundary'); boundary = fn ? `<div class="ind-oboundary">${fn(company)}</div>` : ''; } catch (err) { boundary = ''; } }
    el.innerHTML = `<header class="ind-ohead"><h2 class="ind-oname" id="ind-org-title" tabindex="-1">${esc(company.name)}</h2>`
      + `<div class="ind-ometa"><div class="ind-ochips">${chips}</div>${trajHtml}</div>${boundary}</header>`
      + `<section class="mlk-card ind-oyears" aria-labelledby="ind-years-label">${yearsCard(company)}</section>`
      + `<div class="mlk-tabs ind-otabs" role="tablist" aria-label="Organization tables">${OTABS.map(([key, label]) => `<button type="button" role="tab" id="ind-otab-${key}" data-ind-otab="${key}" aria-controls="ind-opanel" aria-selected="${key === O.sub}" tabindex="${key === O.sub ? 0 : -1}">${label}<span class="ind-tabn">${num(counts[key])}</span></button>`).join('')}</div>`
      + `<div class="ind-opanel" id="ind-opanel" role="tabpanel" aria-labelledby="ind-otab-${O.sub}"></div>`
      + `<aside class="mlk-card ind-topcard" aria-labelledby="ind-top-label">${topCard(company, counts)}</aside>`;
    renderOrgPanel(company);
    setHeadInfo();
  }
  function listData() {
    const q = O.q.trim().toLocaleLowerCase(), eligible = companies().filter(item => item.pickable);
    const total = item => (item.trajectory ? Number(item.trajectory.total) || 0 : -1);
    const rows = eligible.filter(item => (!O.sector || item.sector === O.sector) && (!q || String(item.name).toLocaleLowerCase().includes(q)))
      .sort((a, b) => total(b) - total(a) || String(a.name).localeCompare(String(b.name)));
    return { eligible, rows };
  }
  function renderListTools() {
    const tools = els.list.querySelector('.ind-orgtools');
    const sectors = [...new Set([...industries().map(item => item.sector), ...companies().filter(item => item.pickable).map(item => item.sector)])].filter(sector => companies().some(item => item.pickable && item.sector === sector));
    const select = tools.querySelector('#ind-org-sector');
    select.innerHTML = '<option value="">All</option>' + sectors.map(sector => `<option value="${esc(sector)}">${esc(sectorName(sector))}</option>`).join('');
    if (!sectors.includes(O.sector)) O.sector = '';
    select.value = O.sector;
  }
  function renderList() {
    const box = els.listBody;
    box.setAttribute('aria-busy', 'false');
    const { eligible, rows } = listData(), K = kit();
    els.listCount.textContent = `${num(rows.length)} of ${plural(eligible.length, 'organization')}`;
    if (!eligible.length) { box.innerHTML = '<p class="mlk-state">No organizations on file.</p>'; return; }
    if (!rows.length) { box.innerHTML = '<p class="mlk-state">No organizations match this search.</p>'; return; }
    const slice = K && K.pageSlice ? K.pageSlice(rows, O.page, O.size) : { rows, page: 0, start: 0 };
    O.page = slice.page;
    const max = Math.max(1, ...rows.map(item => (item.trajectory ? Number(item.trajectory.total) || 0 : 0)));
    const body = slice.rows.map(item => {
      const traj = item.trajectory, total = traj ? Number(traj.total) || 0 : 0;
      return `<tr data-ind-org="${esc(item.id)}"><td class="ind-orgname"><a href="${esc(orgHref(item.id))}" data-ind-open="${esc(item.id)}">${esc(item.name)}</a></td>`
        + `<td>${item.sector ? `<span class="ind-dot" style="--ind-c:${esc(colorOf(item.sector))}" aria-hidden="true"></span>` : ''}${esc(sectorName(item.sector))}</td>`
        + `<td>${esc(tierText(item.tier, true) || EMD)}</td><td>${traj ? posture(traj.posture, { delta: true }) : `<span class="ind-none">${EMD}</span>`}</td>`
        + `<td class="ind-attn">${traj ? `<span class="mlk-bar" style="--mlk-bar-v:${(100 * total / max).toFixed(2)}%" aria-hidden="true"><i></i></span><span>${num(total)}</span>` : `<span class="ind-none">${EMD}</span>`}</td>`
        + `<td class="mlk-num">${num(item.memberTotal || 0)}</td></tr>`;
    }).join('');
    box.innerHTML = table('ind-orgtable', `${plural(rows.length, 'organization')}, by bill attention ${windowText()}`, [
      { label: 'Organization', w: '32%' }, { label: 'Industry', w: '14%' }, { label: 'Tier', w: '11%' }, { label: 'Posture', w: '17%' },
      { label: `Bill attention ${windowText()}`, w: '16%', sort: 'descending' }, { label: 'Members', num: true },
    ], body) + (K && K.pager && rows.length > O.size ? `<div class="ind-pager" data-ind-pager="list">${K.pager({ total: rows.length, page: O.page, size: O.size })}</div>` : '');
  }
  function renderOrgs() { // whichever of the list / one organization is showing
    if (!els || !S.data) return;
    showOrgMode();
    if (O.id) renderOrg(); else { renderListTools(); renderList(); setHeadInfo(); }
  }
  function showOrgMode() {
    const open = !!O.id && S.tab === 'organizations';
    els.list.hidden = open;
    els.org.hidden = !open;
    els.back.hidden = !open;
    if (open) root.setAttribute('data-ind-orgpage', ''); else root.removeAttribute('data-ind-orgpage');
  }
  function toTop() { // behavior:'instant' overrides the page's html{scroll-behavior:smooth} and aborts any smooth scroll in progress
    try { window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); } catch (err) { try { window.scrollTo(0, 0); } catch (err2) { /* ignore */ } }
  }
  function scrollTop() {
    toTop();
    try { const fe = window.frameElement; if (fe && fe.getBoundingClientRect().top < 0) fe.scrollIntoView({ block: 'start' }); } catch (err) { /* cross-document */ }
  }
  const HOLD_USER = ['wheel', 'touchstart', 'keydown', 'pointerdown', 'mousedown'];
  function holdTop(ms, { fresh = false } = {}) { // the page's own openOrganization() smooth-scrolls toward its (now hidden) #organization-panel
    if (fresh) O.userMoved = false;              // on load and on hashchange, and that animation can start after scrollTop(); keep an org page
    if (O.userMoved || !(S.tab === 'organizations' && O.id)) return; // at the top until the deadline, unless the reader scrolls, clicks or types
    const until = performance.now() + ms;
    if (O.hold) { O.hold.until = Math.max(O.hold.until, until); return; }
    const hold = O.hold = { until, raf: 0 };
    const zero = () => { if (!O.userMoved && S.tab === 'organizations' && O.id && window.scrollY !== 0) toTop(); };
    const user = () => { O.userMoved = true; stop(); };
    const tick = () => { zero(); if (performance.now() < hold.until && !O.userMoved) hold.raf = requestAnimationFrame(tick); else stop(); };
    function stop() {
      cancelAnimationFrame(hold.raf);
      removeEventListener('scroll', zero);
      removeEventListener('scrollend', zero);
      HOLD_USER.forEach(type => removeEventListener(type, user, true));
      if (O.hold === hold) O.hold = null;
    }
    addEventListener('scroll', zero, { passive: true });
    addEventListener('scrollend', zero, { passive: true });
    HOLD_USER.forEach(type => addEventListener(type, user, { capture: true, passive: true }));
    zero();
    hold.raf = requestAnimationFrame(tick);
  }
  function openOrg(id, { push = false, replace = false, focus = false } = {}) {
    if (O.id !== id) { O.sub = 'bills'; O.pages = {}; }
    O.id = id;
    if (S.tab !== 'organizations') setTab('organizations');
    else if (S.data) renderOrgs();
    if (push || replace) shellUrl({ tab: 'organizations', org: id }, !push);
    scrollTop();
    holdTop(1500, { fresh: true });
    if (focus) { const title = doc.getElementById('ind-org-title'); if (title) title.focus({ preventScroll: true }); }
  }
  function closeOrg({ push = false, focus = false } = {}) {
    O.id = null;
    if (S.tab !== 'organizations') setTab('organizations'); else if (S.data) renderOrgs(); else showOrgMode();
    if (push) shellUrl({ tab: 'organizations', org: null }, false);
    if (focus) { const input = doc.getElementById('ind-org-q'); if (input) input.focus({ preventScroll: true }); }
  }
  const ORG_SKEL = '<div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span></div>';
  function orgState(kind, scope) { // loading / error / empty for the list ('orgs') or the page ('org')
    const page = scope === 'org';
    const box = page ? els.org : els.listBody;
    if (page && !O.id) O.id = (companies().find(item => item.pickable) || {}).id || 'missing';
    showOrgMode();
    if (kind === 'ready') { renderOrgs(); return kind; }
    box.setAttribute('aria-busy', kind === 'loading' ? 'true' : 'false');
    if (kind === 'loading') box.innerHTML = `<p class="mlk-state" role="status">${page ? 'Loading organization…' : 'Loading organizations…'}</p>${ORG_SKEL.repeat(6)}`;
    else if (kind === 'error') box.innerHTML = `<p class="mlk-state mlk-state--error" role="alert">${page ? 'This organization could not be read.' : 'Organizations could not be read.'}</p><button type="button" class="mlk-btn ind-retry" data-ind-retry>Retry</button>`;
    else if (kind === 'empty') {
      if (page) box.innerHTML = `<p class="mlk-state" role="status">No organization matches this link.</p><p><a class="ind-link" href="${esc(orgHref(''))}" data-ind-orgs>All organizations</a></p>`;
      else { els.listCount.textContent = ''; box.innerHTML = '<p class="mlk-state">No organizations on file.</p>'; }
    }
    return kind;
  }
  function orgInfoHtml() { // today's org-panel method text, verbatim, behind "How to read this"
    const parts = [];
    doc.querySelectorAll('#organization-panel .subsection').forEach(section => {
      const h = section.querySelector('h3'), texts = [...section.querySelectorAll('.tabletop .subtle, .honesty p')]
        .filter(node => !node.closest('#company-public-mentions-empty') && node.id !== 'organization-receipt').map(node => node.innerHTML.trim()).filter(Boolean);
      if (texts.length) parts.push(`<p><strong>${esc(h ? h.textContent.trim() : '')}.</strong> ${[...new Set(texts)].join(' ')}</p>`);
    });
    const company = companyById(O.id);
    if (company) { try { const fn = pageFn('companyBoundary'); if (fn && !(company.boundaryKeys || []).length) parts.unshift(`<p>${fn(company).replace(/<[^>]+>/g, '')}</p>`); } catch (err) { /* optional */ } }
    return parts.join('');
  }

  // ---- state changes ----------------------------------------------------------------------------------------------
  function shellUrl(extra, replace) { // keep the shell address in step (?tab=, ?sector=) without reloading the frame
    try {
      const top = window.top;
      if (!top || top === window) return;
      const u = new URL(top.location.href);
      if (u.searchParams.get('view') !== 'industry') return;
      for (const [key, value] of Object.entries(extra)) { if (value === null || value === undefined || value === '') u.searchParams.delete(key); else u.searchParams.set(key, value); }
      const next = u.pathname + u.search + u.hash;
      if (next !== top.location.pathname + top.location.search + top.location.hash) top.history[replace ? 'replaceState' : 'pushState'](top.history.state, '', next);
    } catch (err) { /* the in-frame switch still happens */ }
  }
  function syncLegacy(sector) { // the page's own industry panel (member attention, roster) follows the focus
    try {
      if (typeof currentSector === 'undefined' || currentSector === sector) return;
      currentSector = sector;
      attentionExpanded = false;
      publicMentionsExpanded = false;
      if (typeof renderAttention === 'function') renderAttention();
      if (typeof renderTierRoster === 'function') renderTierRoster();
    } catch (err) { console.warn('industry-ui: the industry panel did not follow the focus', err); }
  }
  function setFocus(sector, { user = false } = {}) {
    if (!bySector(sector)) return false;
    S.focus = sector;
    S.compare = false;
    renderFocus();
    renderOthers();
    syncLegacy(sector);
    if (user) {
      shellUrl({ sector: sector === defaultSector() ? null : sector }, true);
      const title = doc.getElementById('ind-focus-title');
      if (title) title.focus({ preventScroll: true });
    }
    return true;
  }
  function setCompare(on) {
    S.compare = !!on;
    renderFocus();
    els.compareAll.setAttribute('aria-pressed', String(S.compare));
  }
  function setTab(key, { push = false, focus = false } = {}) {
    if (!TABS.some(([k]) => k === key)) key = 'overview';
    S.tab = key;
    if (key === 'overview') O.id = null;
    root.setAttribute('data-ind-view', key);
    els.tabs.forEach(tab => {
      const on = tab.dataset.indTab === key;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
      if (on) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
      if (on && focus) tab.focus();
    });
    els.panelOverview.hidden = key !== 'overview';
    els.panelOrgs.hidden = key !== 'organizations';
    if (push) shellUrl({ tab: key, org: key === 'organizations' ? O.id : null }, false);
    if (key === 'overview') { queueRedraw(); if (S.data) syncLegacy(S.focus); }
    if (key === 'organizations' && S.data) renderOrgs(); else showOrgMode();
    if (S.data) setHeadInfo();
  }
  function start() {
    showLoading();
    if (S.tab === 'organizations') orgState('loading', O.id ? 'org' : 'orgs');
    requestAnimationFrame(() => setTimeout(() => {
      try {
        S.data = readData();
        S.orgCount = null;
        S.bills = {};
        O.party = null;
        O.nameIds = null;
        if (!industries().length) { showEmpty(); renderOrgs(); return; }
        let focus = params.get('sector');
        if (!bySector(focus)) { try { focus = typeof currentSector === 'undefined' ? '' : currentSector; } catch (err) { focus = ''; } }
        if (!bySector(focus)) focus = defaultSector();
        S.focus = focus;
        renderFocus();
        renderOthers();
        syncLegacy(focus);
        renderOrgs();
        if (S.tab === 'organizations' && O.id && !O.userMoved) { scrollTop(); holdTop(2000); } // the page's own openOrganization() may have scrolled to its hidden panel
        whenKit();
      } catch (err) {
        console.warn('industry-ui: render failed', err);
        showError();
        if (S.tab === 'organizations') orgState('error', O.id ? 'org' : 'orgs');
      }
    }, 0));
  }
  function whenKit() { // party colours (colors.json) and the member catalog arrive asynchronously; redraw once both are in
    const K = kit();
    if (O.kitReady || !K || !K.colors) return;
    const cat = K.catalog ? K.catalog().catch(() => null) : Promise.resolve(null);
    cat.then(c => { // MEL-DBG-1: the catalog decides which member links resolve (memberCell, and the page's own links)
      if (!c || !Array.isArray(c.members) || O.catalog === c) return;
      O.catalog = c;
      O.byName = null;
      try { relinkLegacy(); watchLegacy(); } catch (err) { console.warn('industry-ui: member links could not be resolved', err); }
    });
    Promise.all([K.colors(), cat]).then(() => {
      O.kitReady = true;
      if (S.tab === 'organizations' && O.id && S.data) {
        const company = companyById(O.id);
        if (company) { const card = els.org.querySelector('.ind-topcard'); if (card) card.innerHTML = topCard(company, orgCounts2(company)); renderOrgPanel(company); holdTop(800); }
      }
    }).catch(err => console.warn('industry-ui: party colours unavailable', err));
  }
  function tabHref(key) {
    try { const K = kit(); if (K && K.url) return K.url('industry', { tab: key }); } catch (err) { /* fall through */ }
    return `/?view=industry&tab=${key}`;
  }
  // ---- method text behind "How to read this" (fix round 1: MD-1, MD-4, MB-S-3) ------------------------------------
  // The popover lives in the shell document, where this file's CSS is not loaded, so its few layout rules are inline.
  // Sections fold (<details>) and the whole body scrolls inside the viewport, however much method text a section holds.
  // app.css gives every shell <details> a top rule and 12px padding; the inline padding keeps the folded list compact.
  // The summaries carry an mlk- class only so the kit's accent focus ring applies to them.
  const INFO_FOLD = 'style="margin-top:10px;padding:8px 0 0"', INFO_SUM = 'class="mlk-infofold" style="cursor:pointer;font-weight:600"';
  const infoFold = (title, html) => (html ? `<details ${INFO_FOLD}><summary ${INFO_SUM}>${esc(title)}</summary><div style="margin-top:6px">${html}</div></details>` : '');
  const infoWrap = html => `<div style="max-height:calc(100vh - 150px);overflow-y:auto;overscroll-behavior:contain;overflow-wrap:anywhere">${html}</div>`;
  const normText = html => String(html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  function methodParas(selectors, seen) { // each node's paragraphs, verbatim (innerHTML), once per text across the whole popover
    const out = [];
    for (const sel of selectors) {
      doc.querySelectorAll(sel).forEach(node => {
        const paras = node.matches('p') ? [node] : node.querySelectorAll('p').length ? [...node.querySelectorAll('p')] : [node];
        paras.forEach(p => { const html = p.innerHTML.trim(), key = normText(html); if (key && !seen.has(key)) { seen.add(key); out.push(`<p>${html}</p>`); } });
      });
    }
    return out.join('');
  }
  function legacyInfo() { // read once at mount: static nodes of the page's industry panel and build receipts (all stay in the DOM, hidden by CSS)
    if (O.legacyInfo) return O.legacyInfo;
    const seen = new Set(); // what the banner and the cards' notes already say is not repeated
    doc.querySelectorAll('.hero .banner, #industries .section-head .subtle, #industries .figure-notes p').forEach(node => seen.add(normText(node.innerHTML)));
    const heading = (sel, fallback) => { const node = doc.querySelector(sel); return (node && node.textContent.trim()) || fallback; };
    const panel = infoFold(heading('#industry-panel>.tabletop h3, #ind-fold-attention .ind-fold__title', 'Member attention'),
      methodParas(['#industry-panel>.honesty', '#industry-panel>.tabletop .subtle', '#industry-panel>details.methnote .methbody'], seen))
      + infoFold(heading('#public-mentions .tabletop h3, #ind-fold-mentions .ind-fold__title', 'Public mentions'),
        methodParas(['#public-mentions .tabletop .subtle', '#public-mentions-built>.honesty', '#public-mentions-built>details.methnote .methbody'], seen))
      + infoFold(heading('#tier-roster>details.tierfold>.tabletop h4', 'Roster organizations'),
        methodParas(['#tier-roster>details.tierfold>.honesty', '#tier-block>.honesty', '#tier-block>details.methnote .methbody', '#tier-block>.tabletop .subtle', '#tier-roster>details.tierfold>p.receipt:not([id])'], seen));
    const rec = doc.getElementById('receipts');
    let sources = '';
    if (rec) {
      const paras = [...rec.querySelectorAll(':scope>p')].map(p => `<p>${p.innerHTML.trim()}</p>`).join('');
      const hashes = rec.querySelector('ul.hashes'), hashHead = hashes && hashes.previousElementSibling && /^h[2-4]$/i.test(hashes.previousElementSibling.tagName) ? hashes.previousElementSibling.textContent.trim() : 'Input SHA-256';
      const list = hashes ? `<details ${INFO_FOLD}><summary ${INFO_SUM}>${esc(hashHead)}</summary><ul style="margin:6px 0 0;padding-left:16px;font-size:12px;line-height:1.45">${[...hashes.children].map(li => `<li>${li.innerHTML}</li>`).join('')}</ul></details>` : '';
      const recHead = rec.querySelector(':scope>h3');
      sources = (paras || list) ? `<details ${INFO_FOLD}><summary ${INFO_SUM}>Sources &amp; method</summary><div style="margin-top:6px">${recHead ? `<p><strong>${esc(recHead.textContent.trim())}</strong></p>` : ''}${paras}${list}</div></details>` : '';
    }
    return (O.legacyInfo = { panel, sources });
  }
  function baseInfo() { // today's banner, verbatim, plus the industry cards' caption and notes
    if (O.overviewInfo === null) {
      const banner = doc.querySelector('.hero .banner'), caption = doc.querySelector('#industries .section-head .subtle'), notes = doc.querySelector('#industries .figure-notes');
      O.overviewInfo = [banner && `<p>${banner.innerHTML}</p>`, caption && `<p>${caption.innerHTML}</p>`, notes && notes.innerHTML].filter(Boolean).join('');
    }
    return O.overviewInfo;
  }
  function setHeadInfo() { // Overview: banner + notes + the industry panel's method text; Organizations list: banner + notes;
    const K = kit();          // an organization's page: the organization panel's method text (build 11). Every tab ends with Sources & method.
    if (!K || !K.head || !K.head.setInfo) return;
    const { panel, sources } = legacyInfo();
    if (S.tab === 'organizations' && companyById(O.id)) {
      const orgHtml = orgInfoHtml();
      if (orgHtml) { K.head.setInfo({ html: infoWrap(orgHtml + sources) }); return; }
    }
    const html = baseInfo() + (S.tab === 'overview' ? panel : '') + sources;
    if (html) K.head.setInfo({ html: infoWrap(html) });
  }

  // ---- the page's own industry panel below the grid: three collapsed kit folds (fix round 1: MD-1, MB-S-3) ------------
  // Board #37 ends at the grid. The panel's legacy head (eyebrows, duplicate title, window line), its callout, method
  // lines and methodology link are hidden by CSS and read into the head popover; Member attention, Public mentions and the
  // roster move (listeners and ids kept) into folds; the Posture wording toggle becomes a kit segmented control there.
  function foldLegacy() {
    const panel = doc.getElementById('industry-panel');
    if (!panel || panel.querySelector(':scope>.ind-folds')) return;
    legacyInfo(); // read the method text before anything moves
    const box = doc.createElement('div');
    box.className = 'ind-folds';
    const fold = (id, titleNode, text) => {
      const d = doc.createElement('details'), s = doc.createElement('summary'), body = doc.createElement('div');
      d.className = 'ind-fold';
      d.id = id;
      s.className = 'ind-fold__sum';
      body.className = 'ind-fold__body';
      const title = titleNode || Object.assign(doc.createElement('h3'), { textContent: text });
      title.classList.add('ind-fold__title');
      s.appendChild(title);
      d.append(s, body);
      box.appendChild(d);
      return body;
    };
    const top = panel.querySelector(':scope>.tabletop'), table = top && top.nextElementSibling;
    if (top && table && table.classList.contains('tablewrap')) {
      const body = fold('ind-fold-attention', top.querySelector('h3'), 'Member attention'), head = doc.createElement('div');
      head.className = 'ind-foldhead';
      const toggle = panel.querySelector(':scope>.panelhead .toggle');
      if (toggle && toggle.querySelector('[data-posture-mode]')) {
        const group = doc.createElement('div'), label = toggle.previousElementSibling;
        group.className = 'ind-postgroup';
        if (label && label.classList.contains('eyebrow')) { label.className = 'mlk-seg__label'; label.id = 'ind-posture-label'; group.appendChild(label); }
        toggle.classList.remove('toggle'); // the page's .toggle rules (and restored.css's !important active fill) stay off the kit control
        toggle.classList.add('mlk-seg', 'ind-postseg');
        toggle.setAttribute('role', 'group');
        if (label && label.id === 'ind-posture-label') { toggle.removeAttribute('aria-label'); toggle.setAttribute('aria-labelledby', 'ind-posture-label'); }
        group.appendChild(toggle);
        head.appendChild(group);
      }
      const count = doc.getElementById('attention-count');
      if (count) head.appendChild(count);
      body.append(head, table);
    }
    const mentions = doc.getElementById('public-mentions');
    if (mentions) fold('ind-fold-mentions', mentions.querySelector('.tabletop h3'), 'Public mentions').appendChild(mentions);
    const roster = doc.getElementById('tier-roster');
    if (roster) {
      const inner = roster.querySelector(':scope>details.tierfold');
      if (inner) inner.open = true; // its own summary is hidden; the fold is the one disclosure
      fold('ind-fold-roster', doc.getElementById('roster-title'), 'Roster organizations').appendChild(roster);
    }
    panel.appendChild(box);
  }

  // ---- mount ------------------------------------------------------------------------------------------------------
  function mount() {
    const main = doc.getElementById('main') || doc.querySelector('main');
    if (!main) return;
    root.classList.add('ind-v2');
    const wrap = doc.createElement('div');
    wrap.className = 'ind-root';
    wrap.id = 'ind-root';
    const K0 = kit(), searchIcon = K0 && K0.icon ? String(K0.icon('search')).replace('<svg', '<svg class="mlk-search__icon"') : '';
    wrap.innerHTML = `<div class="ind-tabrow"><nav class="mlk-tabs ind-tabs" role="tablist" aria-label="Industry views">${TABS.map(([key, label]) => `<a role="tab" id="ind-tab-${key}" data-ind-tab="${key}" aria-controls="ind-panel-${key}" aria-selected="false" tabindex="-1" href="${esc(tabHref(key))}">${label}</a>`).join('')}</nav>`
      + `<a class="ind-orgback" id="ind-org-back" href="${esc(orgHref(''))}" data-ind-orgs hidden><span aria-hidden="true">←</span> Organizations</a></div>`
      + '<div class="ind-overview" id="ind-panel-overview" role="tabpanel" aria-labelledby="ind-tab-overview"><div class="ind-grid">'
      + '<section class="ind-focus ind-c" id="ind-focus" aria-labelledby="ind-focus-title" aria-busy="true"></section>'
      + '<aside class="ind-others" aria-labelledby="ind-others-label"><h2 class="ind-label" id="ind-others-label">Other industries</h2><div class="ind-cards" id="ind-cards"></div>'
      + '<button type="button" class="mlk-btn ind-compareall" id="ind-compareall" aria-pressed="false" aria-controls="ind-focus">Compare all four</button></aside></div></div>'
      + '<div class="ind-orghead" id="ind-panel-organizations" role="tabpanel" aria-labelledby="ind-tab-organizations">'
      + '<div class="ind-orglist" id="ind-orglist"><div class="ind-orgtools">'
      + `<div class="mlk-search ind-orgsearch">${searchIcon}<input class="mlk-search__input" id="ind-org-q" type="search" placeholder="Find an organization" aria-label="Find an organization" autocomplete="off"></div>`
      + '<label class="mlk-select"><span class="mlk-select__label">Industry:</span><select id="ind-org-sector" aria-label="Industry"><option value="">All</option></select></label>'
      + '<p class="ind-orgcount" id="ind-org-count" aria-live="polite"></p></div><div class="ind-orgbody" id="ind-orgbody" aria-busy="false"></div></div>'
      + '<article class="ind-org" id="ind-org" aria-labelledby="ind-org-title" hidden></article></div>';
    main.insertBefore(wrap, main.firstChild);
    els = {
      wrap, tablist: wrap.querySelector('.ind-tabs'), tabs: [...wrap.querySelectorAll('[data-ind-tab]')],
      panelOverview: wrap.querySelector('#ind-panel-overview'), panelOrgs: wrap.querySelector('#ind-panel-organizations'),
      focus: wrap.querySelector('#ind-focus'), cards: wrap.querySelector('#ind-cards'), compareAll: wrap.querySelector('#ind-compareall'),
      list: wrap.querySelector('#ind-orglist'), listBody: wrap.querySelector('#ind-orgbody'), listCount: wrap.querySelector('#ind-org-count'),
      org: wrap.querySelector('#ind-org'), back: wrap.querySelector('#ind-org-back'),
    };
    S.sitenav = doc.querySelector('.product-header .sitenav');
    const stats = doc.querySelector('.hero .hero-stats');
    if (stats) els.list.insertBefore(stats, els.list.firstChild); // the three coverage tiles head the Organizations list
    try { foldLegacy(); } catch (err) { console.warn('industry-ui: the industry panel could not be folded', err); }

    const plainClick = event => event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
    doc.addEventListener('click', event => { // roster organisation links (lobby-network.html#org=<id>) open the page in place
      if (event.defaultPrevented || !plainClick(event) || !S.data) return;
      const a = event.target.closest && event.target.closest('a[href]');
      if (!a || wrap.contains(a)) return;
      let u;
      try { u = new URL(a.getAttribute('href'), location.href); } catch (err) { return; }
      const file = decodeURIComponent(u.pathname.split('/').pop()), id = new URLSearchParams(u.hash.slice(1)).get('org');
      if (!id || u.origin !== location.origin || file !== 'lobby-network.html' || !companyById(id)) return;
      event.preventDefault();
      event.stopPropagation();
      openOrg(id, { push: true, focus: true });
    }, true);
    els.panelOrgs.addEventListener('input', event => {
      if (event.target.id !== 'ind-org-q') return;
      O.q = event.target.value;
      O.page = 0;
      if (S.data) renderList();
    });
    els.panelOrgs.addEventListener('change', event => {
      if (event.target.id === 'ind-org-sector') { O.sector = event.target.value; O.page = 0; if (S.data) renderList(); return; }
      if (event.target.matches && event.target.matches('[data-mlk-page-size]')) {
        const key = event.target.closest('[data-ind-pager]').dataset.indPager, size = Number(event.target.value) || 25;
        if (key === 'list') { O.size = size; O.page = 0; renderList(); } else { O.pages[key] = { page: 0, size }; renderOrgPanel(companyById(O.id)); }
      }
    });
    els.panelOrgs.addEventListener('keydown', event => {
      const tab = event.target.closest && event.target.closest('[data-ind-otab]');
      if (!tab) return;
      const tabs = [...els.org.querySelectorAll('[data-ind-otab]')], i = tabs.indexOf(tab), n = tabs.length;
      const next = { ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      O.sub = tabs[next].dataset.indOtab;
      renderOrgPanel(companyById(O.id));
      els.org.querySelector(`#ind-otab-${O.sub}`).focus();
    });

    wrap.addEventListener('click', event => {
      const target = event.target.closest('[data-ind-tab],[data-ind-focus],[data-ind-bytier],[data-ind-back],[data-ind-retry],#ind-compareall,[data-ind-orgs],[data-ind-open],[data-ind-org],[data-ind-otab],[data-ind-otab-go],[data-ind-pager] [data-mlk-page]');
      if (!target || !wrap.contains(target)) return;
      if (target.hasAttribute('data-ind-tab')) {
        if (!plainClick(event)) return;
        event.preventDefault();
        if (target.dataset.indTab === 'organizations' && S.tab === 'organizations' && O.id) closeOrg({ push: true });
        else setTab(target.dataset.indTab, { push: true });
      } else if (target.hasAttribute('data-ind-orgs')) {
        if (!plainClick(event)) return;
        event.preventDefault();
        closeOrg({ push: true, focus: true });
      } else if (target.hasAttribute('data-ind-open') || target.hasAttribute('data-ind-org')) {
        if (target.hasAttribute('data-ind-open') && !plainClick(event)) return;
        if (event.target.closest('a[href]:not([data-ind-open])')) return;
        event.preventDefault();
        openOrg(target.dataset.indOpen || target.dataset.indOrg, { push: true, focus: true });
      } else if (target.hasAttribute('data-ind-otab') || target.hasAttribute('data-ind-otab-go')) {
        O.sub = target.dataset.indOtab || target.dataset.indOtabGo;
        renderOrgPanel(companyById(O.id));
        const tab = els.org.querySelector(`#ind-otab-${O.sub}`);
        if (tab) tab.focus();
      } else if (target.hasAttribute('data-mlk-page')) {
        const key = target.closest('[data-ind-pager]').dataset.indPager, step = target.dataset.mlkPage === 'next' ? 1 : -1;
        if (key === 'list') { O.page += step; renderList(); } else { const st = O.pages[key] || (O.pages[key] = { page: 0, size: 25 }); st.page += step; renderOrgPanel(companyById(O.id)); }
      } else if (target.hasAttribute('data-ind-retry') && S.tab === 'organizations') start();
      else if (target.hasAttribute('data-ind-focus')) setFocus(target.dataset.indFocus, { user: true });
      else if (target.hasAttribute('data-ind-bytier')) {
        S.byTier = !S.byTier;
        renderFocus();
        const toggle = els.focus.querySelector('[data-ind-bytier]');
        if (toggle) toggle.focus();
      } else if (target.hasAttribute('data-ind-back')) { setCompare(false); els.compareAll.focus(); }
      else if (target.hasAttribute('data-ind-retry')) start();
      else if (target === els.compareAll) setCompare(!S.compare);
    });
    els.tablist.addEventListener('keydown', event => {
      const i = els.tabs.findIndex(tab => tab.dataset.indTab === S.tab), n = els.tabs.length;
      const next = { ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1 }[event.key];
      if (event.key === ' ') { event.preventDefault(); event.target.click(); return; }
      if (next === undefined) return;
      event.preventDefault();
      setTab(els.tabs[next].dataset.indTab, { push: true, focus: true });
    });
    addEventListener('hashchange', () => {
      const id = new URLSearchParams(location.hash.slice(1)).get('org');
      if (!id) return;
      if (S.data && companyById(id)) openOrg(id, { replace: true }); else setTab('organizations');
    });
    if (window.ResizeObserver) { const ro = new ResizeObserver(queueRedraw); ro.observe(els.focus); ro.observe(els.cards); } else addEventListener('resize', queueRedraw);

    const org = params.get('org') || new URLSearchParams(location.hash.slice(1)).get('org');
    const tab = params.get('tab');
    const first = TABS.some(([k]) => k === tab) ? tab : org ? 'organizations' : 'overview';
    O.id = first === 'organizations' && org ? org : null;
    setTab(first);
    if (O.id) { scrollTop(); holdTop(2500, { fresh: true }); } // entry on an org page: undo the page's load-time scrollIntoView (smooth) toward #organization-panel
    setHeadInfo();
    start();
  }

  window.MelangeIndustry = {
    version: '11',
    current: () => ({ tab: S.tab, focus: S.focus, compare: S.compare, byTier: S.byTier, org: O.id, orgTab: O.id ? O.sub : null, query: O.q, sector: O.sector }),
    org: id => { if (!companyById(id)) return false; openOrg(id, { push: true, focus: true }); return true; },
    organizations: () => closeOrg({ push: true }),
    orgTab(key) { if (!PANELS[key] || !O.id) return false; O.sub = key; renderOrgPanel(companyById(O.id)); return true; },
    orgState: (kind, scope = 'org') => orgState(kind, scope), // proof hook: 'loading' | 'error' | 'empty' | 'ready' for 'org' or 'orgs'
    focus: sector => setFocus(sector, { user: true }),
    compare: on => setCompare(on),
    byTier(on) { S.byTier = !!on; renderFocus(); return S.byTier; },
    tab: key => setTab(key, { push: true }),
    state(kind) { // proof hook: show a state without changing data ('loading' | 'error' | 'empty' | 'ready')
      if (kind === 'loading') showLoading(); else if (kind === 'error') showError(); else if (kind === 'empty') showEmpty();
      else { renderFocus(); renderOthers(); }
      return kind;
    },
    retry: () => start(),
    topBills: sector => topBills(sector || S.focus),
  };
  mount();
})();
