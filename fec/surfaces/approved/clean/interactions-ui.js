/* interactions-ui.js: overlay for original-interactions.html (Reported interactions #41, member summary #42); owners: prompts 14, 15. Runs after the page engine, restored-links.js and ui-kit.js. */
/* ---- 14 · Members this industry lobbies: the members table (board #41) ----
   Replaces the legacy card picker (#picker-list) with a ranked, paged table inside the live #picker-panel.
   Data and actions stay the engine's: DATA (page-data), memberActs(), postureDetail(), openMember(), the
   #sector-picker change handler and renderPicker() (wrapped, so every engine call repaints this table).
   Rank = the engine's sectorMembers() order (interaction count desc, then name). Colours by reference only:
   party dots from MLK.partyColor (influence PTCOL), posture and change colours from the page's own
   .postbadge.<bucket> .pdot rule. The member page (board #42, prompt 15) is the second section below. */
(function () {
  'use strict';
  const W = window, doc = document, MLK = W.MLK;
  const panel = doc.getElementById('picker-panel');
  if (!panel || !MLK || W.MelangeInteractions) return;

  const EMD = '—';
  const esc = MLK.esc;
  const fmt = n => Number(n).toLocaleString('en-US');
  const PARTY = {D: 'Democrat', R: 'Republican', I: 'Independent'};
  const CHAMBER = {rep: 'House', sen: 'Senate'};
  const FLAG = {former: 'Former', unresolved: 'Name unresolved'};
  const FLAG_TITLE = {former: 'No longer serving in Congress', unresolved: 'No roster or Voteview record names this bioguide'};
  const NONE = '__none';
  const S = {q: '', chamber: '', posture: '', page: 0, size: 25, cache: new Map(), colors: {}, timer: 0, built: false, out: null, refocus: null};

  /* ---------- engine access (classic-script globals of the page) ---------- */
  const data = () => (typeof DATA !== 'undefined' ? DATA : null); // eslint-disable-line no-undef
  const sector = () => (typeof currentSector !== 'undefined' ? currentSector : ''); // eslint-disable-line no-undef
  const mode = () => (typeof postureMode !== 'undefined' ? postureMode : 'posture'); // eslint-disable-line no-undef
  const sectorLabel = s => (typeof displaySector === 'function' ? displaySector(s) : String(s)); // eslint-disable-line no-undef
  function postureColor(bucket) { // by reference: the page's own .postbadge.<bucket> .pdot colour
    if (S.colors[bucket] !== undefined) return S.colors[bucket];
    const probe = doc.createElement('span');
    probe.className = `postbadge ${bucket}`;
    probe.innerHTML = '<span class="pdot"></span>';
    probe.hidden = true;
    doc.body.appendChild(probe);
    const colour = getComputedStyle(probe.firstChild).backgroundColor || '';
    probe.remove();
    return (S.colors[bucket] = colour);
  }
  function stateOf(v) { const D = data(); return v && D && D.postureStates ? D.postureStates[v[0]] || null : null; }
  function postureLabel(v) { const st = stateOf(v); return st ? (mode() === 'posture' ? st.p : st.t) : ''; }

  // One row per member the industry's filings name, in the engine's own rank order.
  function rowsFor(sec) {
    if (S.cache.has(sec)) return S.cache.get(sec);
    const D = data();
    if (!D || !D.membersBySector || !D.members || typeof W.memberActs !== 'function') throw new Error('page data unavailable');
    const idx = (D.membersBySector[sec] || []).slice();
    const count = new Map(idx.map(i => [i, W.memberActs(i, sec).length]));
    idx.sort((a, b) => count.get(b) - count.get(a) || D.members[a].name.localeCompare(D.members[b].name));
    const rows = idx.map((i, k) => ({i, rank: k + 1, m: D.members[i], n: count.get(i), v: (D.sectorPosture || {})[`${sec}:${i}`] || null}));
    rows.max = rows.length ? rows[0].n : 0;
    S.cache.set(sec, rows);
    return rows;
  }
  let YEARS = null;
  function years() { // the filing window, from the activities themselves
    if (YEARS !== null) return YEARS;
    const D = data();
    let lo = Infinity, hi = -Infinity;
    for (const a of (D && D.acts) || []) { const y = a[0]; if (y < lo) lo = y; if (y > hi) hi = y; }
    return (YEARS = lo <= hi ? (lo === hi ? String(lo) : `${lo}–${hi}`) : '');
  }

  /* ---------- markup ---------- */
  const skeleton = n => Array.from({length: n}, () => '<tr class="ixt-skelrow" aria-hidden="true"><td colspan="6"><div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div></td></tr>').join('');
  function build() {
    const D = data();
    const sectors = (D && D.sectors) || [];
    const root = doc.createElement('div');
    root.className = 'ixt';
    root.id = 'ixt';
    root.innerHTML = `<div class="ixt-head"><h2 class="ixt-title">Members this industry lobbies</h2>`
      + `<div class="mlk-anchor ixt-howto"><button type="button" class="mlk-info" aria-expanded="false" aria-controls="ixt-howpop" data-mlk-toggle="ixt-howpop">${MLK.icon('info')}<span>How to read this</span></button>`
      + `<div class="mlk-popover mlk-popover--end ixt-howpop" id="ixt-howpop" role="dialog" aria-label="How to read this" tabindex="-1" hidden></div></div></div>`
      + `<div class="ixt-controls">`
      + `<span class="mlk-seg__label ixt-seglabel" id="ixt-seglabel">Industry</span>`
      + `<div class="mlk-seg ixt-seg" role="group" aria-labelledby="ixt-seglabel">${sectors.map(s => `<button type="button" data-ixt-sector="${esc(s)}" aria-pressed="false">${esc(sectorLabel(s))}</button>`).join('')}</div>`
      + `<div class="mlk-search ixt-search">${MLK.icon('search').replace('<svg', '<svg class="mlk-search__icon"')}<input class="mlk-search__input" id="ixt-q" type="search" placeholder="Find a member" aria-label="Find a member" autocomplete="off"></div>`
      + `<label class="mlk-select ixt-select"><span class="mlk-select__label">Chamber:</span><select id="ixt-chamber"><option value="">All</option><option value="rep">House</option><option value="sen">Senate</option></select></label>`
      + `<label class="mlk-select ixt-select"><span class="mlk-select__label">Posture:</span><select id="ixt-posture"><option value="">All</option></select></label>`
      + `<p class="ixt-caption" id="ixt-caption" aria-live="polite"></p></div>`
      + `<div class="mlk-tablecard ixt-card" id="ixt-card"><table class="mlk-table ixt-table"><caption class="ixt-sr">Members this industry lobbies, ranked by reported interactions</caption>`
      + `<colgroup><col class="ixt-c-rank"><col class="ixt-c-member"><col class="ixt-c-posture"><col class="ixt-c-change"><col class="ixt-c-count"><col class="ixt-c-open"></colgroup>`
      + `<thead><tr><th scope="col">Rank</th><th scope="col">Member</th><th scope="col">Posture</th><th scope="col">Change</th><th scope="col" aria-sort="descending">Interactions</th><th scope="col"><span class="ixt-sr">Filings</span></th></tr></thead>`
      + `<tbody id="ixt-body"></tbody></table><div class="ixt-state" id="ixt-state" hidden></div></div>`
      + `<div class="ixt-pager" id="ixt-pager"></div>`;
    panel.insertBefore(root, panel.firstChild);
    panel.classList.add('ixt-host');
    doc.documentElement.classList.add('ixt-on');

    // "How to read this": today's explanation box and methodology note, verbatim (copies; the originals stay
    // in the page for the member view and are hidden only while this table shows).
    const pop = root.querySelector('#ixt-howpop');
    for (const sel of ['#main > .honesty', '#main > details.methnote']) {
      const node = doc.querySelector(sel);
      if (node) { const copy = node.cloneNode(true); copy.classList.add('ixt-copy'); pop.appendChild(copy); }
    }
    if (!pop.children.length) root.querySelector('.ixt-howto').hidden = true;

    root.querySelector('.ixt-seg').addEventListener('click', e => {
      const b = e.target.closest('[data-ixt-sector]');
      if (!b || b.getAttribute('aria-pressed') === 'true') return;
      const picker = doc.getElementById('sector-picker');
      if (!picker) return;
      S.page = 0;
      picker.value = b.dataset.ixtSector;
      picker.dispatchEvent(new Event('change', {bubbles: true})); // engine: currentSector = value; closeMember() -> renderPicker()
    });
    const q = root.querySelector('#ixt-q');
    q.addEventListener('input', () => { S.q = q.value.trim().toLocaleLowerCase(); S.page = 0; render(); });
    q.addEventListener('keydown', e => { if (e.key === 'Escape' && q.value) { e.stopPropagation(); q.value = ''; S.q = ''; S.page = 0; render(); } });
    root.querySelector('#ixt-chamber').addEventListener('change', e => { S.chamber = e.target.value; S.page = 0; render(); });
    root.querySelector('#ixt-posture').addEventListener('change', e => { S.posture = e.target.value; S.page = 0; render(); });
    root.querySelector('#ixt-card').addEventListener('click', e => {
      const open = e.target.closest('[data-ixt-open]');
      if (open) { openFilings(Number(open.dataset.ixtOpen)); return; }
      if (e.target.closest('[data-ixt-clear]')) { clearFilters(); return; }
      if (e.target.closest('[data-ixt-retry]')) { S.cache.delete(sector()); schedule(); }
    });
    MLK.bindPager(root.querySelector('#ixt-pager'), S, () => { render(); const card = doc.getElementById('ixt-card'); if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({block: 'start'}); });
    S.built = true;
  }

  function clearFilters() {
    S.q = ''; S.chamber = ''; S.posture = ''; S.page = 0;
    doc.getElementById('ixt-q').value = '';
    doc.getElementById('ixt-chamber').value = '';
    doc.getElementById('ixt-posture').value = '';
    render();
    doc.getElementById('ixt-q').focus();
  }
  // Open filings -> the engine's member page for this member and the industry on screen.
  function openFilings(index) {
    if (typeof W.openMember !== 'function' || !data() || !data().members[index]) return;
    W.openMember(index);
  }

  function syncControls(sec, rows) {
    for (const b of doc.querySelectorAll('#ixt [data-ixt-sector]')) b.setAttribute('aria-pressed', String(b.dataset.ixtSector === sec));
    const sel = doc.getElementById('ixt-posture');
    const D = data(), seen = new Set(rows.map(r => postureLabel(r.v)));
    const labels = [];
    for (const st of (D && D.postureStates) || []) { const l = mode() === 'posture' ? st.p : st.t; if (seen.has(l) && !labels.includes(l)) labels.push(l); }
    const opts = [['', 'All'], ...labels.map(l => [l, l])];
    if (seen.has('')) opts.push([NONE, 'No posture']);
    if (!opts.some(([v]) => v === S.posture)) S.posture = '';
    sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}"${v === S.posture ? ' selected' : ''}>${esc(l)}</option>`).join('');
  }

  function memberCell(r) {
    const m = r.m, dot = `<span class="mlk-dot ixt-dot" style="--mlk-dot:${esc(MLK.partyColor(m.party || ''))}"></span>`;
    const cat = !m.flag && MLK.memberByIdSync ? MLK.memberByIdSync(m.id) : null;
    const name = cat
      ? `<a class="mlk-name ixt-name" data-mlk-nav href="${esc(MLK.url('profile', {m: cat.name, tab: 'overview'}))}" title="Open ${esc(cat.name)} in the member desk">${esc(m.name)}</a>`
      : `<span class="ixt-name ixt-name--plain">${esc(m.name)}</span>`;
    const sub = [PARTY[m.party] || m.party, CHAMBER[m.chamber] || m.chamber].filter(Boolean).join(' · ');
    const flag = m.flag ? `<span class="ixt-flag" title="${esc(FLAG_TITLE[m.flag] || '')}">${esc(FLAG[m.flag] || m.flag)}</span>` : '';
    return `${dot}<span class="ixt-who">${name}<span class="mlk-sub">${esc(sub)}${sub && flag ? ' · ' : ''}${flag}${!sub && !flag ? EMD : ''}</span></span>`;
  }
  function postureCell(r) {
    const st = stateOf(r.v);
    if (!st) return `<span class="mlk-pill mlk-pill--none">${EMD}</span>`;
    const tip = typeof W.postureDetail === 'function' ? W.postureDetail(r.v) : '';
    return `<span class="mlk-pill ixt-pill${st.n === 'adversarial' ? ' ixt-pill--adv' : ''}" style="--mlk-dot:${esc(postureColor(st.b))}" title="${esc(tip)}">${esc(postureLabel(r.v))}</span>`;
  }
  function changeCell(r) {
    const d = r.v ? String(r.v[4] == null ? '' : r.v[4]) : '';
    if (!d || d === EMD) return `<span class="ixt-change ixt-change--none">${EMD}</span>`;
    const bucket = /^>?\+/.test(d) ? 'cultivating' : /^>?-/.test(d) ? 'disengaging' : '';
    return `<span class="ixt-change"${bucket ? ` style="--ixt-c:${esc(postureColor(bucket))}"` : ''}>${esc(d)}</span>`;
  }
  function row(r, max) {
    const pct = max > 0 ? Math.max(r.n > 0 ? 1 : 0, Math.round(r.n / max * 1000) / 10) : 0;
    return `<tr data-bioguide="${esc(r.m.id)}" data-member="${r.i}">`
      + `<td class="ixt-rank">${fmt(r.rank)}</td>`
      + `<td class="ixt-member">${memberCell(r)}</td>`
      + `<td>${postureCell(r)}</td>`
      + `<td>${changeCell(r)}</td>`
      + `<td class="ixt-count"><span class="ixt-n">${fmt(r.n)}</span><span class="mlk-bar ixt-bar" style="--mlk-bar-v:${pct}%" aria-hidden="true"><i></i></span></td>`
      + `<td class="ixt-open-cell"><button type="button" class="ixt-open" data-ixt-open="${r.i}" aria-label="Open filings for ${esc(r.m.name)}">Open filings${MLK.icon('chevron')}</button></td></tr>`;
  }

  function setState(kind, html) {
    const card = doc.getElementById('ixt-card'), st = doc.getElementById('ixt-state'), body = doc.getElementById('ixt-body');
    card.setAttribute('aria-busy', kind === 'loading' ? 'true' : 'false');
    card.dataset.state = kind;
    if (kind === 'loading') { body.innerHTML = skeleton(8); st.hidden = true; st.innerHTML = ''; return; }
    if (kind === 'rows') { st.hidden = true; st.innerHTML = ''; return; }
    body.innerHTML = '';
    st.innerHTML = html;
    st.hidden = false;
  }
  function showLoading() {
    if (!S.built) return;
    setState('loading');
    doc.getElementById('ixt-caption').textContent = 'Loading members…';
    doc.getElementById('ixt-pager').innerHTML = '';
  }
  function showError() {
    setState('error', '<p class="mlk-state mlk-state--error" role="alert">The members table could not be built from this page’s filings.<br><button type="button" class="mlk-btn mlk-btn--sm" data-ixt-retry>Retry</button></p>');
    doc.getElementById('ixt-caption').textContent = '';
    doc.getElementById('ixt-pager').innerHTML = '';
  }

  // Back from the member page: focus the row whose "Open filings" opened it, on whichever page holds it now
  // (the filters and page are kept); a member no longer listed (other industry, filtered out) falls back to the search box.
  function refocusRow() {
    const i = S.refocus;
    S.refocus = null;
    if (i === null || i === undefined || member() !== null) return;
    const find = () => doc.querySelector(`#ixt-body tr[data-member="${i}"] .ixt-open`);
    let btn = find();
    const at = btn || !S.out ? -1 : S.out.findIndex(r => r.i === i);
    if (at >= 0) { S.page = Math.floor(at / S.size); render(); btn = find(); }
    const target = btn || doc.getElementById('ixt-q');
    if (target) target.focus();
  }

  function render() {
    if (!S.built) return;
    const sec = sector();
    let rows;
    try { rows = rowsFor(sec); } catch (err) { S.out = null; showError(); refocusRow(); return; }
    syncControls(sec, rows);
    const out = rows.filter(r => {
      if (S.chamber && r.m.chamber !== S.chamber) return false;
      if (S.posture) { const l = postureLabel(r.v); if (S.posture === NONE ? l !== '' : l !== S.posture) return false; }
      if (S.q && !r.m.name.toLocaleLowerCase().includes(S.q) && !String(r.m.id).toLocaleLowerCase().includes(S.q)) return false;
      return true;
    });
    S.out = out;
    const filtered = !!(S.q || S.chamber || S.posture);
    const span = years();
    doc.getElementById('ixt-caption').textContent = `${fmt(out.length)} ${out.length === 1 ? 'member' : 'members'}${span ? ` · filings ${span}` : ''}`;
    const pager = doc.getElementById('ixt-pager');
    if (!out.length) {
      setState('empty', filtered
        ? '<p class="mlk-state">No member matches these filters.<br><button type="button" class="mlk-btn mlk-btn--sm" data-ixt-clear>Clear filters</button></p>'
        : '<p class="mlk-state">No member is named in this industry’s filings.</p>');
      pager.innerHTML = '';
      refocusRow();
      return;
    }
    const slice = MLK.pageSlice(out, S.page, S.size);
    S.page = slice.page;
    setState('rows');
    doc.getElementById('ixt-body').innerHTML = slice.rows.map(r => row(r, rows.max)).join('');
    pager.innerHTML = MLK.pager({total: out.length, page: slice.page, size: S.size});
    refocusRow();
  }
  // Paint the loading rows first when a sector's table is not computed yet, then compute.
  function schedule() {
    if (!S.built) return;
    clearTimeout(S.timer);
    if (S.cache.has(sector())) { render(); return; }
    showLoading();
    S.timer = setTimeout(render, 30);
  }

  build();
  // Every engine repaint of the picker (industry change, "All members", wording toggle) now repaints this table.
  W.renderPicker = function renderPicker() { schedule(); };
  // Board #41 puts "How to read this" in the view, so the shell head shows none for this view.
  try { MLK.head.setInfo(null); } catch (e) { /* not framed */ }
  schedule();
  // Profile links need the shell catalog; repaint once it is available.
  if (MLK.catalog) MLK.catalog().then(() => { if (doc.getElementById('ixt-card').dataset.state === 'rows') render(); }, () => {});

  /* ---- 15 · Reported interactions, one member, summary first (board #42) ----
     The engine keeps the member page's state (currentMember, currentSector, postureMode) and its actions
     (openMember, closeMember, switchMemberSector, the filter listeners, the wording buttons). This overlay,
     div#ixm as the first child of the live #member-panel, replaces the view: breadcrumb row with the wording
     toggle, header, industry segment, posture line, six figures, BY ORGANIZATION, BY YEAR and a paged FILINGS
     table. window.renderInteractions is wrapped, so every engine repaint (open, industry switch, filter change,
     wording) repaints this overlay instead of the card list. The live filter controls are MOVED into the
     filings card (their engine listeners stay). Row expansion is the engine's own interactionMarkup(), verbatim.
     Figures and the filings table follow the filters (visibleActs(), as today); BY ORGANIZATION and BY YEAR show
     the member's whole industry total and drive the Organization and Year filters. Colours by reference only:
     party dot from MLK.partyColor, posture and position pills from the page's .postbadge.<bucket> .pdot rule. */
  const mpanel = doc.getElementById('member-panel');
  const M = {built: false, page: 0, size: 25, key: '', sig: '', more: false, open: new Set(), timer: 0, info: false};
  const WORDING_KEY = 'melange.interactions.wording';
  const PARTIAL_YEAR = 2026; // the engine's own literal (renderInteractions, populateFilters)
  const ORG_TOP = 8;
  const FILTER_IDS = ['text-search', 'org-filter', 'year-filter', 'issue-filter', 'position-filter'];
  const REFOCUS = ['data-ixm-sector', 'data-ixm-wording', 'data-ixm-org', 'data-ixm-year', 'data-ixm-more', 'data-ixm-exp'];
  const member = () => (typeof currentMember !== 'undefined' ? currentMember : null); // eslint-disable-line no-undef
  /* eslint-disable no-undef */ // the engine's top-level consts (activity tuple layout, position maps and wording)
  const COL = typeof A_YEAR === 'undefined' ? {YEAR: 0, DATE: 1, ORG: 2, LOBS: 4, BILL: 6, POSITION: 12}
    : {YEAR: A_YEAR, DATE: A_DATE, ORG: A_ORG, LOBS: A_LOBS, BILL: A_BILL, POSITION: A_POSITION};
  const THREE = typeof THREE_CLASS === 'undefined' ? {} : THREE_CLASS;
  const POSCLASS = typeof POSITION_CLASS === 'undefined' ? {} : POSITION_CLASS;
  const NOT_DISCLOSED = typeof POSITION_LABEL === 'undefined' ? 'Not disclosed' : POSITION_LABEL;
  const INF_TIP = typeof INFERRED_TIP === 'undefined' ? '' : INFERRED_TIP;
  /* eslint-enable no-undef */
  const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? '' : 's'}`;
  const ORD = ['th', 'st', 'nd', 'rd'];
  const ordinal = n => { const v = n % 100; return `${n}${ORD[(v - 20) % 10] || ORD[v] || ORD[0]}`; };
  function place(c) { // the Members table's reading of the catalog district (desk-members.js place())
    const d = String(c.district || '');
    let x = d.match(/^Congressional District (\d+) \(\d+\w* Congress\), (.+)$/);
    if (x) return `${x[2]} ${ordinal(Number(x[1]))}`;
    x = d.match(/District \(at Large\) \(\d+\w* Congress\), (.+)$/);
    if (x) return `${x[1]} at large`;
    return d || c.state || '';
  }
  let SPAN = null;
  function corpusYears() { // every year the filings cover, so a member's quiet years show as zero
    if (SPAN) return SPAN;
    const D = data();
    let lo = Infinity, hi = -Infinity;
    for (const a of (D && D.acts) || []) { const y = a[COL.YEAR]; if (y < lo) lo = y; if (y > hi) hi = y; }
    SPAN = [];
    for (let y = lo; y <= hi; y++) SPAN.push(y);
    return SPAN;
  }
  function storedWording() { try { const v = W.localStorage.getItem(WORDING_KEY); return v === 'posture' || v === 'trend' ? v : ''; } catch (e) { return ''; } }
  function storeWording(v) { try { W.localStorage.setItem(WORDING_KEY, v); } catch (e) { /* storage blocked: the choice lasts for this page only */ } }
  function infoHtml() { // today's explanation box and methodology note, verbatim, behind the head's "How to read this"
    return ['#main > .honesty', '#main > details.methnote'].map(sel => doc.querySelector(sel)).filter(Boolean).map(n => n.outerHTML).join('');
  }

  const skelRows = (cols, n) => Array.from({length: n}, () => `<tr class="ixm-skelrow" aria-hidden="true"><td colspan="${cols}"><div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div></td></tr>`).join('');
  const FIGS = ['interactions', 'organizations', 'bills', 'named lobbyists', 'years active', 'inferred support / oppose'];
  const fig = (value, label, title) => `<div class="ixm-fig"${title ? ` title="${esc(title)}"` : ''}><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;

  function mBuild() {
    const D = data();
    if (!mpanel || !D || typeof W.visibleActs !== 'function' || typeof W.interactionMarkup !== 'function' || typeof W.switchMemberSector !== 'function') return;
    const legacyWording = [...mpanel.querySelectorAll('[data-posture-mode]')];
    const root = doc.createElement('div');
    root.className = 'ixm';
    root.id = 'ixm';
    root.innerHTML = `<div class="ixm-nav">`
      + `<button type="button" class="ixm-back" data-ixm-close>${MLK.icon('arrow')}<span>Members this industry lobbies</span></button>`
      + `<nav class="ixm-crumbs" aria-label="Breadcrumb"><ol><li><button type="button" class="ixm-crumb" data-ixm-close>Reported interactions</button></li><li aria-hidden="true" class="ixm-slash">/</li><li><span id="ixm-crumb-name" aria-current="page"></span></li></ol></nav>`
      + `<div class="ixm-navend"><span class="mlk-seg__label" id="ixm-wlabel">Wording</span>`
      + `<div class="mlk-seg ixm-wording" role="group" aria-labelledby="ixm-wlabel">${legacyWording.map(b => `<button type="button" data-ixm-wording="${esc(b.dataset.postureMode)}" aria-pressed="false" title="${esc(b.textContent)}">${esc(b.textContent.split(' / ')[0])}</button>`).join('')}</div>`
      + `<a class="ixm-desk" id="ixm-desk" data-mlk-nav href="#" hidden>Open in member desk</a></div></div>`
      + `<div class="ixm-who"><h2 class="ixm-name" id="ixm-name"></h2><span class="ixm-meta" id="ixm-meta"></span></div>`
      + `<div class="ixm-sectors"><span class="mlk-seg__label" id="ixm-slabel">Industry</span><div class="mlk-seg ixm-seg" id="ixm-seg" role="group" aria-labelledby="ixm-slabel"></div></div>`
      + `<div class="ixm-posture" id="ixm-posture"></div>`
      + `<div class="ixm-figs" id="ixm-figs" role="group" aria-label="Interaction summary"></div>`
      + `<div class="ixm-cards">`
      + `<section class="ixm-card ixm-orgs" aria-labelledby="ixm-otitle"><h3 class="ixm-cardtitle" id="ixm-otitle">By organization</h3>`
      + `<table class="mlk-table ixm-otable"><colgroup><col class="ixm-c-org"><col class="ixm-c-bar"><col class="ixm-c-n"><col class="ixm-c-yrs"></colgroup>`
      + `<thead><tr><th scope="col">Organization</th><th class="ixm-obarh" aria-hidden="true"></th><th scope="col">Interactions</th><th scope="col">Years</th></tr></thead><tbody id="ixm-obody"></tbody></table>`
      + `<div class="ixm-cardstate" id="ixm-ostate" hidden></div><div class="ixm-more" id="ixm-more"></div></section>`
      + `<section class="ixm-card ixm-years" aria-labelledby="ixm-ytitle"><h3 class="ixm-cardtitle" id="ixm-ytitle">By year</h3><div class="ixm-chart" id="ixm-chart" role="group" aria-labelledby="ixm-ytitle"></div></section>`
      + `</div>`
      + `<section class="ixm-card ixm-filings" id="ixm-filings" aria-labelledby="ixm-ftitle"><h3 class="ixm-cardtitle" id="ixm-ftitle">Filings<span class="ixm-fcount" id="ixm-fcount" aria-live="polite"></span></h3>`
      + `<div class="ixm-filters" id="ixm-filters" role="group" aria-label="Filing filters"></div>`
      + `<table class="mlk-table ixm-ftable"><caption class="ixt-sr">Filings that cited a bill this member sponsored or cosponsored, newest first</caption>`
      + `<colgroup><col class="ixm-c-date"><col class="ixm-c-forg"><col class="ixm-c-bill"><col class="ixm-c-role"><col class="ixm-c-lobs"><col class="ixm-c-pos"></colgroup>`
      + `<thead><tr><th scope="col">Date</th><th scope="col">Organization</th><th scope="col">Bill</th><th scope="col">Member’s role</th><th scope="col">Lobbyists</th><th scope="col">Position (best guess)</th></tr></thead>`
      + `<tbody id="ixm-fbody"></tbody></table><div class="ixm-state" id="ixm-state" hidden></div><div class="ixm-pager" id="ixm-pager"></div></section>`;

    // Today's filters, moved (not copied) so the engine's own listeners keep driving renderInteractions().
    const bar = root.querySelector('#ixm-filters');
    const labelOf = id => { const l = doc.querySelector(`label[for="${id}"]`); return l ? l.textContent.trim() : ''; };
    const q = doc.getElementById('text-search');
    if (q) {
      const box = doc.createElement('div');
      box.className = 'mlk-search ixm-search';
      box.innerHTML = MLK.icon('search').replace('<svg', '<svg class="mlk-search__icon"');
      q.classList.add('mlk-search__input');
      q.setAttribute('aria-label', labelOf('text-search') || 'Search');
      box.appendChild(q);
      bar.appendChild(box);
    }
    for (const id of FILTER_IDS.slice(1)) {
      const sel = doc.getElementById(id);
      if (!sel) continue;
      const wrap = doc.createElement('label');
      wrap.className = 'mlk-select ixm-select';
      wrap.innerHTML = `<span class="mlk-select__label">${esc(labelOf(id))}:</span>`;
      wrap.appendChild(sel);
      bar.appendChild(wrap);
    }
    const clear = doc.getElementById('clear-filters');
    if (clear) { clear.classList.add('mlk-btn', 'mlk-btn--text', 'ixm-clear'); bar.appendChild(clear); }

    mpanel.insertBefore(root, mpanel.firstChild);
    mpanel.classList.add('ixm-host');

    root.addEventListener('click', e => {
      const t = e.target;
      if (t.closest('[data-ixm-close]')) { W.closeMember(); return; }
      const w = t.closest('[data-ixm-wording]');
      if (w) { if (w.getAttribute('aria-pressed') !== 'true') { const b = mpanel.querySelector(`[data-posture-mode="${w.dataset.ixmWording}"]`); if (b) b.click(); } return; }
      const s = t.closest('[data-ixm-sector]');
      if (s) { if (s.getAttribute('aria-pressed') !== 'true') W.switchMemberSector(s.dataset.ixmSector); return; }
      const o = t.closest('#ixm-obody tr[data-org]');
      if (o) { const b = o.querySelector('[data-ixm-org]'); setFilter('org-filter', b.getAttribute('aria-pressed') === 'true' ? '' : o.dataset.org); return; }
      const y = t.closest('[data-ixm-year]');
      if (y) { if (y.getAttribute('aria-disabled') !== 'true') setFilter('year-filter', y.getAttribute('aria-pressed') === 'true' ? '' : y.dataset.ixmYear); return; }
      if (t.closest('[data-ixm-more]')) { M.more = !M.more; mRender(); return; }
      const x = t.closest('[data-ixm-exp]');
      if (x) { toggleRow(x.closest('tr')); return; }
      const r = t.closest('#ixm-fbody tr[data-act]');
      if (r && !t.closest('a,button,input,select')) { toggleRow(r); return; }
      if (t.closest('[data-ixm-clear]')) { if (clear) clear.click(); return; }
      if (t.closest('[data-ixm-retry]')) { M.key = ''; mSchedule(); }
    });
    MLK.bindPager(root.querySelector('#ixm-pager'), M, () => {
      mRender();
      const card = doc.getElementById('ixm-filings');
      if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({block: 'start'});
    });
    M.built = true;
  }

  function setFilter(id, value) {
    const sel = doc.getElementById(id);
    if (!sel) return;
    sel.value = value;
    sel.dispatchEvent(new Event('change', {bubbles: true})); // engine listener -> renderInteractions()
  }
  function xrow(a) { return `<tr class="ixm-xrow" id="ixm-x-${a}"><td colspan="6">${W.interactionMarkup(a)}</td></tr>`; }
  function toggleRow(tr) {
    if (!tr || tr.dataset.act === undefined) return;
    const a = Number(tr.dataset.act), btn = tr.querySelector('[data-ixm-exp]'), next = tr.nextElementSibling;
    if (M.open.has(a)) {
      M.open.delete(a);
      if (next && next.classList.contains('ixm-xrow')) next.remove();
    } else {
      M.open.add(a);
      tr.insertAdjacentHTML('afterend', xrow(a));
    }
    tr.classList.toggle('is-open', M.open.has(a));
    if (btn) btn.setAttribute('aria-expanded', String(M.open.has(a)));
  }

  /* ---------- painting ---------- */
  function paintStatic(D, i) {
    const m = D.members[i], cat = !m.flag && MLK.memberByIdSync ? MLK.memberByIdSync(m.id) : null;
    doc.getElementById('ixm-crumb-name').textContent = m.name;
    doc.getElementById('ixm-name').textContent = m.name;
    const bits = [cat ? cat.chamber : CHAMBER[m.chamber], cat ? cat.party : PARTY[m.party], cat ? place(cat) : ''].filter(Boolean);
    doc.getElementById('ixm-meta').innerHTML = `<span class="mlk-dot ixm-dot" style="--mlk-dot:${esc(MLK.partyColor(m.party || ''))}"></span>`
      + (bits.length ? bits.map(b => `<span>${esc(b)}</span>`).join('<span class="ixm-sep" aria-hidden="true">·</span>') : `<span>${EMD}</span>`)
      + (m.flag ? `<span class="ixm-flag" title="${esc(FLAG_TITLE[m.flag] || '')}">${esc(FLAG[m.flag] || m.flag)}</span>` : '');
    const desk = doc.getElementById('ixm-desk');
    if (cat) { desk.href = MLK.url('profile', {m: cat.name, tab: 'overview'}); desk.title = `Open ${cat.name} in the member desk`; desk.hidden = false; } else desk.hidden = true;
    for (const b of doc.querySelectorAll('#ixm [data-ixm-wording]')) b.setAttribute('aria-pressed', String(b.dataset.ixmWording === mode()));
    const sec = sector();
    doc.getElementById('ixm-seg').innerHTML = (D.sectors || []).map(s => `<button type="button" data-ixm-sector="${esc(s)}" aria-pressed="${s === sec}">${esc(sectorLabel(s))}<span class="ixm-segn"><span aria-hidden="true"> · </span><span class="ixt-sr">, </span>${fmt(W.memberActs(i, s).length)}</span></button>`).join('');
    const v = (D.sectorPosture || {})[`${sec}:${i}`], st = stateOf(v), box = doc.getElementById('ixm-posture');
    if (!v || !st) { box.innerHTML = ''; box.hidden = true; return; }
    const tip = typeof W.postureDetail === 'function' ? W.postureDetail(v) : '';
    box.hidden = false;
    box.innerHTML = `<span class="mlk-pill ixm-pill${st.n === 'adversarial' ? ' ixt-pill--adv' : ''}" style="--mlk-dot:${esc(postureColor(st.b))}" title="${esc(tip)}">${esc(postureLabel(v))}</span>`
      + `<span class="ixm-ptext" title="${esc(tip)}">${esc(v[4] == null ? EMD : v[4])}<span class="ixm-sep" aria-hidden="true">·</span>${esc(plural(v[1], 'bill'))} in 2024–2025 vs ${esc(fmt(v[2]))} in 2022–2023</span>`;
  }
  function paintFigures(D, acts) {
    const orgs = new Set(), bills = new Set(), lobs = new Set(), yrs = new Set();
    let support = 0, oppose = 0, classified = 0;
    for (const a of acts) {
      const act = D.acts[a];
      orgs.add(act[COL.ORG]); bills.add(act[COL.BILL]); yrs.add(act[COL.YEAR]);
      for (const l of act[COL.LOBS]) lobs.add(l);
      const label = THREE[(D.positions[act[COL.POSITION]] || ['', ''])[0]] || '';
      if (label) classified++;
      if (label === 'Support') support++; else if (label === 'Oppose') oppose++;
    }
    const years = [...yrs];
    // Same figures and tooltip as the engine's stat tiles (renderInteractions).
    const dirTitle = classified
      ? `${fmt(classified)} of ${fmt(acts.length)} shown interactions carry an inferred position. Support and Oppose are the classifier's weakest classes (F1 0.58 and 0.53).`
      : 'No interaction in this view carries an inferred position.';
    const values = [fmt(acts.length), fmt(orgs.size), fmt(bills.size), fmt(lobs.size), years.length ? `${Math.min(...years)}–${Math.max(...years)}` : EMD, classified ? `${fmt(support)} / ${fmt(oppose)}` : EMD];
    doc.getElementById('ixm-figs').innerHTML = values.map((v, k) => fig(v, FIGS[k], k === 5 ? dirTitle : '')).join('');
  }
  function paintOrgs(D, all) {
    const agg = new Map();
    for (const a of all) {
      const act = D.acts[a];
      let r = agg.get(act[COL.ORG]);
      if (!r) agg.set(act[COL.ORG], (r = {o: act[COL.ORG], n: 0, y: new Set()}));
      r.n++; r.y.add(act[COL.YEAR]);
    }
    const rows = [...agg.values()].sort((x, y) => y.n - x.n || D.orgs[x.o].name.localeCompare(D.orgs[y.o].name));
    const sel = doc.getElementById('org-filter') ? doc.getElementById('org-filter').value : '';
    const max = rows.length ? rows[0].n : 0;
    const shown = M.more ? rows : rows.slice(0, ORG_TOP);
    doc.getElementById('ixm-obody').innerHTML = shown.map(r => {
      const on = String(r.o) === sel, pct = max > 0 ? Math.max(1, Math.round(r.n / max * 1000) / 10) : 0, name = D.orgs[r.o].name;
      return `<tr data-org="${r.o}"${on ? ' class="is-selected"' : ''}><td class="ixm-oname"><button type="button" class="ixm-obtn" data-ixm-org="${r.o}" aria-pressed="${on}" title="${esc(name)}">${esc(name)}</button></td>`
        + `<td class="ixm-obar"><span class="mlk-bar ixm-bar" style="--mlk-bar-v:${pct}%" aria-hidden="true"><i></i></span></td><td class="ixm-on">${fmt(r.n)}</td><td class="ixm-oy">${fmt(r.y.size)}</td></tr>`;
    }).join('');
    const st = doc.getElementById('ixm-ostate');
    st.hidden = rows.length > 0;
    st.innerHTML = rows.length ? '' : '<p class="mlk-state">No organizations</p>';
    doc.getElementById('ixm-more').innerHTML = rows.length > ORG_TOP
      ? `<button type="button" class="mlk-btn mlk-btn--text ixm-morebtn" data-ixm-more aria-expanded="${M.more}">${M.more ? 'Show fewer' : `Show ${fmt(rows.length - ORG_TOP)} more`}</button>` : '';
  }
  function paintYears(D, all) {
    const count = new Map();
    for (const a of all) { const y = D.acts[a][COL.YEAR]; count.set(y, (count.get(y) || 0) + 1); }
    const max = Math.max(0, ...count.values());
    const sel = doc.getElementById('year-filter') ? doc.getElementById('year-filter').value : '';
    doc.getElementById('ixm-chart').innerHTML = corpusYears().map(y => {
      const n = count.get(y) || 0, partial = y === PARTIAL_YEAR, h = max > 0 ? Math.max(n ? 1.5 : 0, Math.round(n / max * 820) / 10) : 0;
      return `<button type="button" class="ixm-ybar${partial ? ' is-partial' : ''}" data-ixm-year="${y}" aria-pressed="${String(y) === sel}"${n ? '' : ' aria-disabled="true"'} aria-label="${y}${partial ? ' partial year' : ''}: ${plural(n, 'interaction')}">`
        + `<span class="ixm-yplot" style="--ixm-h:${h}%"><span class="ixm-yn">${fmt(n)}</span><span class="ixm-ycol"></span></span>`
        + `<span class="ixm-yl">${y}${partial ? '<em>(partial)</em>' : ''}</span></button>`;
    }).join('');
  }
  function posCell(text, basis) {
    if (!text) return `<span class="ixm-none" title="The lobbying filing has no place to state a position, so no filing in this corpus states one.">${esc(NOT_DISCLOSED)}</span>`;
    const tip = `Collapses to "${THREE[text] || ''}" in the three-class view. ${INF_TIP}`;
    return `<span class="mlk-pill ixm-ppill" style="--mlk-dot:${esc(postureColor(POSCLASS[text] || 'unknown'))}" title="${esc(tip)}">${esc(text)}</span><span class="ixm-inferred" title="${esc(basis)}">inferred</span>`;
  }
  function frow(D, a) {
    const act = D.acts[a], org = D.orgs[act[COL.ORG]] || {}, bill = D.bills[act[COL.BILL]] || {};
    const role = (D.memberRoles || {})[`${member()}:${act[COL.BILL]}`];
    const roleText = role ? `${role[0] === 1 ? 'Sponsor' : 'Cosponsor'}${role[1] ? `, ${role[1]}` : ''}` : ''; // engine's reading of memberRoles
    const lobs = (act[COL.LOBS] || []).map(l => D.lobs[l]);
    const [pText, pBasis] = D.positions[act[COL.POSITION]] || ['', ''];
    const open = M.open.has(a), date = act[COL.DATE] || EMD;
    const billTitle = bill.t ? `${bill.d} · ${bill.t}` : (bill.d || '');
    return `<tr data-act="${a}"${open ? ' class="is-open"' : ''}>`
      + `<td class="ixm-date"><button type="button" class="ixm-exp" data-ixm-exp="${a}" aria-expanded="${open}" aria-controls="ixm-x-${a}">${MLK.icon('chevron')}<span>${esc(date)}</span><span class="ixt-sr"> details</span></button></td>`
      + `<td class="ixm-clip" title="${esc(org.name || '')}">${esc(org.name || EMD)}</td>`
      + `<td class="ixm-clip" title="${esc(billTitle)}"><span class="ixm-billno">${esc(bill.d || EMD)}</span> ${esc(bill.t || '')}</td>`
      + `<td class="ixm-clip">${roleText ? esc(roleText) : `<span class="ixm-none">${EMD}</span>`}</td>`
      + `<td class="ixm-clip" title="${esc(lobs.join(', '))}">${lobs.length ? `${esc(lobs[0])}${lobs.length > 1 ? `<span class="ixm-plus"> +${fmt(lobs.length - 1)}</span>` : ''}` : `<span class="ixm-none" title="No individual lobbyist named on this activity.">${EMD}</span>`}</td>`
      + `<td class="ixm-clip">${posCell(pText, pBasis)}</td></tr>`
      + (open ? xrow(a) : '');
  }
  function paintFilings(D, acts, total) {
    doc.getElementById('ixm-fcount').innerHTML = `<span aria-hidden="true"> · </span><span class="ixt-sr">: </span>${fmt(acts.length)}${acts.length !== total ? ` of ${fmt(total)}` : ''}`;
    const body = doc.getElementById('ixm-fbody'), st = doc.getElementById('ixm-state'), pager = doc.getElementById('ixm-pager');
    if (!acts.length) {
      const filtered = FILTER_IDS.some(id => doc.getElementById(id) && doc.getElementById(id).value);
      body.innerHTML = '';
      st.innerHTML = filtered && total
        ? '<p class="mlk-state">No interaction matches these filters.<br><button type="button" class="mlk-btn mlk-btn--sm" data-ixm-clear>Clear filters</button></p>'
        : '<p class="mlk-state">No filings for this member in this industry.</p>';
      st.hidden = false;
      pager.innerHTML = '';
      return;
    }
    const slice = MLK.pageSlice(acts, M.page, M.size);
    M.page = slice.page;
    st.hidden = true; st.innerHTML = '';
    body.innerHTML = slice.rows.map(a => frow(D, a)).join('');
    pager.innerHTML = MLK.pager({total: acts.length, page: slice.page, size: M.size});
  }

  /* ---------- states ---------- */
  function mShowLoading() {
    const root = doc.getElementById('ixm');
    if (!root) return;
    root.dataset.state = 'loading';
    root.setAttribute('aria-busy', 'true');
    doc.getElementById('ixm-figs').innerHTML = FIGS.map(l => fig(EMD, l)).join('');
    doc.getElementById('ixm-obody').innerHTML = skelRows(4, 5);
    doc.getElementById('ixm-ostate').hidden = true;
    doc.getElementById('ixm-more').innerHTML = '';
    doc.getElementById('ixm-chart').innerHTML = '<span class="mlk-skel ixm-chartskel" aria-hidden="true"></span>';
    doc.getElementById('ixm-fcount').innerHTML = '';
    doc.getElementById('ixm-fbody').innerHTML = skelRows(6, 8);
    doc.getElementById('ixm-state').hidden = true;
    doc.getElementById('ixm-pager').innerHTML = '';
  }
  function mShowError() {
    const root = doc.getElementById('ixm');
    if (!root) return;
    root.dataset.state = 'error';
    root.setAttribute('aria-busy', 'false');
    doc.getElementById('ixm-figs').innerHTML = FIGS.map(l => fig(EMD, l)).join('');
    doc.getElementById('ixm-obody').innerHTML = '';
    const os = doc.getElementById('ixm-ostate');
    os.innerHTML = '<p class="mlk-state">Not available</p>'; os.hidden = false;
    doc.getElementById('ixm-more').innerHTML = '';
    doc.getElementById('ixm-chart').innerHTML = '<p class="mlk-state">Not available</p>';
    doc.getElementById('ixm-fcount').innerHTML = '';
    doc.getElementById('ixm-fbody').innerHTML = '';
    const st = doc.getElementById('ixm-state');
    st.innerHTML = '<p class="mlk-state mlk-state--error" role="alert">The filings could not be built from this page’s data.<br><button type="button" class="mlk-btn mlk-btn--sm" data-ixm-retry>Retry</button></p>';
    st.hidden = false;
    doc.getElementById('ixm-pager').innerHTML = '';
  }

  function mRender() {
    if (!M.built) return;
    const i = member();
    if (i === null) return;
    const root = doc.getElementById('ixm'), D = data();
    const ae = doc.activeElement, attr = ae && root.contains(ae) ? REFOCUS.find(k => ae.hasAttribute(k)) : '';
    const refocus = attr ? `[${attr}="${CSS.escape(ae.getAttribute(attr))}"]` : '';
    try {
      if (!D || !D.members[i]) throw new Error('member unavailable');
      const all = W.memberActs(i, sector()), acts = W.visibleActs();
      const sig = [i, sector(), ...FILTER_IDS.map(id => (doc.getElementById(id) || {}).value || '')].join('|');
      if (sig !== M.sig) { M.sig = sig; M.page = 0; }
      paintStatic(D, i);
      paintFigures(D, acts);
      paintOrgs(D, all);
      paintYears(D, all);
      paintFilings(D, acts, all.length);
      root.dataset.state = 'ready';
      root.setAttribute('aria-busy', 'false');
    } catch (err) {
      mShowError();
    }
    if (refocus) { const n = root.querySelector(refocus); if (n) n.focus(); }
  }
  // A new member or industry paints its loading state first, then computes; filter changes repaint at once.
  function mSchedule() {
    if (!M.built || member() === null) return;
    clearTimeout(M.timer);
    if (!M.info) { M.info = true; try { MLK.head.setInfo({html: infoHtml()}); } catch (e) { /* not framed */ } }
    const key = `${member()}:${sector()}`;
    if (key === M.key) { mRender(); return; }
    M.key = key; M.open.clear(); M.more = false; M.page = 0;
    try { paintStatic(data(), member()); } catch (e) { /* mRender reports it */ }
    mShowLoading();
    M.timer = setTimeout(mRender, 30);
  }

  /* ---------- address ----------
     The engine records each open / close / industry switch as an in-frame history entry ('?sector=…&b=…'), but it
     resolves that relative URL against the injected <base href="/">, so the entry points at the shell ('/?sector=…')
     rather than this page, and the shell address never learns about it (reload, Back from another view and shared
     links lost the member page). After every engine change: point the entry back at this page (same query; the first
     entry also names its industry, so Back to it restores that industry), and mirror sector and b into the shell
     address with replaceState, exactly as the shell's own /?view=interactions&sector=…&b=… deep link reads them.
     The engine's inline script is untouched. */
  const HOME = W.location.pathname;
  function syncAddress() {
    try {
      const q = new URLSearchParams(W.location.search);
      if (!q.has('sector') && sector()) q.set('sector', sector());
      const qs = q.toString(), here = HOME + (qs ? `?${qs}` : '') + W.location.hash;
      if (here !== W.location.pathname + W.location.search + W.location.hash) W.history.replaceState(W.history.state, '', here);
    } catch (e) { /* the in-frame entry keeps the engine's URL */ }
    try {
      const top = W.top;
      if (!top || top === W) return;
      const u = new URL(top.location.href), view = u.searchParams.get('view');
      if (view ? view !== 'interactions' : u.pathname !== '/lobby-interactions.html') return;
      const D = data(), i = member(), sec = sector();
      const b = i !== null && D && D.members[i] ? D.members[i].id : '';
      // the engine's default industry (DATA.sectors[0]) stays implicit, as on the rail's own link, unless the address names it
      if (sec && D && (sec !== (D.sectors || [])[0] || u.searchParams.has('sector'))) u.searchParams.set('sector', sec); else u.searchParams.delete('sector');
      if (b) u.searchParams.set('b', b); else u.searchParams.delete('b');
      u.searchParams.delete('m'); // the engine reads b first and m (a name) only without b; a stale m would reopen a member
      const next = u.pathname + u.search + u.hash;
      if (next !== top.location.pathname + top.location.search + top.location.hash) top.history.replaceState(top.history.state, '', next);
    } catch (e) { /* cross-document access refused: the in-frame view still changes */ }
  }

  mBuild();
  if (M.built) {
    const engineRender = W.renderInteractions;
    W.renderInteractions = function renderInteractions() { mSchedule(); };
    // The engine registered its filter listeners with the function object itself (addEventListener(type,
    // renderInteractions)), so they would keep calling the original card renderer. Swap each for a listener that
    // calls the (wrapped) global, exactly as the engine's other callers do.
    for (const id of FILTER_IDS) {
      const el = doc.getElementById(id), type = id === 'text-search' ? 'input' : 'change';
      if (!el) continue;
      el.removeEventListener(type, engineRender);
      el.addEventListener(type, () => W.renderInteractions());
    }
    // Focus follows the view: opening a member from the table moves focus to the member's name; closing it returns
    // focus to that member's "Open filings" (refocusRow, run by the table repaint the engine's closeMember triggers).
    // Focus that is elsewhere (another control of this page, or outside this frame) is never taken.
    const lostFocus = box => { const ae = doc.activeElement; return doc.hasFocus() && (!ae || ae === doc.body || box.contains(ae)); };
    const engineOpen = W.openMember;
    W.openMember = function openMember() {
      const moveFocus = lostFocus(panel);
      const out = engineOpen.apply(this, arguments);
      if (moveFocus && member() !== null) { const h = doc.getElementById('ixm-name'); if (h) { h.tabIndex = -1; h.focus({preventScroll: true}); } }
      syncAddress();
      return out;
    };
    const engineClose = W.closeMember;
    W.closeMember = function closeMember() {
      const was = member();
      if (was !== null && lostFocus(mpanel)) S.refocus = was;
      const out = engineClose.apply(this, arguments);
      M.key = ''; M.info = false; clearTimeout(M.timer);
      try { MLK.head.setInfo(null); } catch (e) { /* not framed */ }
      syncAddress();
      return out;
    };
    const engineSwitch = W.switchMemberSector;
    W.switchMemberSector = function switchMemberSector() {
      const out = engineSwitch.apply(this, arguments);
      syncAddress();
      return out;
    };
    // Wording (Cultivating | Rising) is remembered per viewer and applied through the engine's own buttons.
    const want = storedWording();
    if (want && want !== mode()) { const b = mpanel.querySelector(`[data-posture-mode="${want}"]`); if (b) b.click(); }
    for (const b of mpanel.querySelectorAll('[data-posture-mode]')) b.addEventListener('click', () => storeWording(b.dataset.postureMode));
    syncAddress(); // a history step can reload this frame at an entry the shell address does not name
    mSchedule();
    if (MLK.catalog) MLK.catalog().then(() => { if (member() !== null && doc.getElementById('ixm').dataset.state === 'ready') paintStatic(data(), member()); }, () => {});
  }

  W.MelangeInteractions = {render, schedule, showLoading, showError, clearFilters, state: S,
    member: {render: mRender, schedule: mSchedule, showLoading: mShowLoading, showError: mShowError, state: M}};
})();
