/* desk-bills.js: overlay for original-desk.html (Member desk > Bills, board #33; the shell passes ?bill=<congress:type:number>); owner: prompt 05. Runs before restored-desk.js. */
/* Build 05 (board #33). The legacy bill engine stays: BILLS / BILL_BY_KEY (rebuilt by rebuildBillRepository after the
   desk-support merge), billStateFilter, billState / setBillState (localStorage mdesk_bill_tracker_v1), the legacy filter
   controls (#billq, #billarea, #billcongress, #billtracked) and the Load-more button (#billmore) are MOVED into the new
   filter bar with their handlers. renderBills is wrapped by name: instead of 40 tall cards it paints one-line table rows
   with the legacy filter predicate, and the selected bill opens in an in-flow drawer (the table narrows). Pin uses the
   legacy [data-pin] click handler; the legacy card body (coalition, lobbying, press, amendments, tracking notes) is kept
   inside the drawer behind "Coalition, lobbying and notes" and keeps its legacy delegated handlers.
   Data colours (party dots) are read by reference from the desk's PTCOL; no colour literal is typed here. */
(() => {
'use strict';
const Q = new URLSearchParams(location.search);
if ((Q.get('deskview') || 'ranked') !== 'bills') return;
const section = document.getElementById('view-bills');
if (!section || typeof renderBills !== 'function' || typeof billStateFilter === 'undefined') return;
const K = window.MLK || null;
const esc = v => (v == null ? '' : String(v)).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const $id = id => document.getElementById(id);

/* ---------------- data helpers (same sources as the legacy billCard / renderBills) ---------------- */
const PARTY = {D: 'Democrat', R: 'Republican', I: 'Independent', ID: 'Independent', L: 'Independent'};
function partyColour(party){
 const name = PARTY[String(party || '').toUpperCase()] || party || '';
 const map = typeof PTCOL !== 'undefined' ? PTCOL : (K && K.colorMap ? K.colorMap('PTCOL') : null) || {};
 return map[name] || map[''] || '';
}
const dot = party => { const c = partyColour(party); return `<span class="mlk-dot dbl-dot" aria-hidden="true"${c ? ` style="--mlk-dot:${esc(c)}"` : ''}></span>`; };
/* "Rep. Gonzalez, Vicente [D-TX-34]" -> "Rep. Gonzalez [D-TX-34]" (display only; the full name stays in the drawer) */
function shortName(name){
 const s = String(name || '').trim();
 const m = s.match(/^((?:Rep|Sen|Del)\.|Resident Commissioner)\s+([^,\[]+?)\s*,[^\[]*?(\[[^\]]*\])\s*$/);
 return m ? `${m[1]} ${m[2]} ${m[3]}` : s;
}
function memberIndex(bioguide, name){
 if (bioguide && K && K.memberByIdSync){ const row = K.memberByIdSync(bioguide); if (row && typeof byName !== 'undefined' && byName[row.name] != null) return byName[row.name]; }
 if (bioguide){
  if (!memberIndex.map){ memberIndex.map = {}; M.forEach((m, i) => { const b = (m.bio || {}).bioguide || m.bioguide; if (b) memberIndex.map[b] = i; }); }
  if (memberIndex.map[bioguide] != null) return memberIndex.map[bioguide];
 }
 return name != null && typeof byName !== 'undefined' && byName[name] != null ? byName[name] : null;
}
function sponsorOf(bill){
 const live = bill.live || {}, s = live.sponsor || {};
 if (s.name) return {name: s.name, party: s.party || '', bioguide: s.bioguide || ''};
 const i = (bill.sponsors || [])[0];
 if (i != null && M[i]) return {name: M[i].name, party: M[i].party || '', mi: i};
 return bill.sponsor ? {name: bill.sponsor, party: ''} : null;
}
/* the legacy billCard count: unique originals + later cosponsors not already original (live lists first) */
function cosponsorsOf(bill){
 const live = bill.live || {};
 const uniq = rows => { const seen = new Set(), out = []; rows.forEach(r => { const n = String(r.name || '').trim(); if (n && !seen.has(n)){ seen.add(n); out.push(r); } }); return out; };
 const fromIdx = idx => (idx || []).map(i => ({name: M[i].name, party: M[i].party || '', mi: i}));
 const originals = uniq(Array.isArray(live.original_cosponsors) ? live.original_cosponsors : fromIdx(bill.originals));
 const on = new Set(originals.map(r => String(r.name).trim()));
 const later = uniq(Array.isArray(live.cosponsors) ? live.cosponsors : fromIdx(bill.cosponsors)).filter(r => !on.has(String(r.name).trim()));
 return {originals, later, count: originals.length + later.length};
}
/* the legacy renderBills predicate, unchanged */
function matchesFor(){
 const f = billStateFilter, q = f.q;
 return BILLS.filter(b => (!q || (`${b.display} ${b.title} ${b.sponsor}`).toLowerCase().includes(q)) && (!f.area || (b.areas || []).includes(f.area)) && (!f.congress || String(b.congress) === f.congress) && (!f.tracked || billState(b.key).pinned));
}
function cacheStamp(bill){
 const live = bill && bill.live, all = (typeof D !== 'undefined' && D.bill_live) || {};
 const at = (live && live.updated_at) || all.updated_at;
 if (!at) return 'Congress.gov live cache not yet synced';
 return `Congress.gov ${live && live.stale ? 'last good copy' : 'live cache'} ${at}`;
}
function resolveBill(raw){
 const v = String(raw || '').trim(); if (!v) return null;
 if (BILL_BY_KEY[v]) return BILL_BY_KEY[v];
 const low = v.toLowerCase(); if (BILL_BY_KEY[low]) return BILL_BY_KEY[low];
 const flat = low.replace(/[^a-z0-9]/g, '');
 return BILLS.find(b => String(b.display).toLowerCase().replace(/[^a-z0-9]/g, '') === flat) || null;
}

/* ---------------- layout (built once; legacy nodes moved in) ---------------- */
document.body.classList.add('dbl-page');
const legacyNodes = [...section.children];
legacyNodes.forEach(n => n.classList.add('dbl-legacy'));
const layout = document.createElement('div');
layout.className = 'dbl-layout';
layout.innerHTML = `
<div class="dbl-main">
 <div class="dbl-filters" role="search" aria-label="Bill filters">
  <div class="mlk-search dbl-search"></div>
  <label class="mlk-select dbl-sel dbl-sel--area"><span class="mlk-select__label">Area:</span></label>
  <label class="mlk-select dbl-sel dbl-sel--congress"><span class="mlk-select__label">Congress:</span></label>
  <label class="dbl-pinned"><span class="dbl-pinned__text">Pinned only</span></label>
  <span class="dbl-count" id="dbl-count" role="status" aria-live="polite"></span>
 </div>
 <div class="mlk-tablecard dbl-card">
  <table class="mlk-table dbl-table" aria-describedby="dbl-count">
   <colgroup><col class="dbl-c-bill"><col class="dbl-c-title"><col class="dbl-c-areas"><col class="dbl-c-sponsor"><col class="dbl-c-congress"><col class="dbl-c-cos"></colgroup>
   <thead><tr><th scope="col">Bill</th><th scope="col">Title</th><th scope="col">Areas</th><th scope="col">Sponsor</th><th scope="col">Congress</th><th scope="col">Cosponsors</th></tr></thead>
   <tbody id="dbl-rows"></tbody>
  </table>
  <div class="dbl-status" id="dbl-status"></div>
 </div>
 <div class="dbl-more"></div>
</div>
<aside class="dbl-drawer" id="dbl-drawer" aria-label="Bill detail" tabindex="-1" hidden></aside>`;
section.append(layout);
const rowsEl = $id('dbl-rows'), statusEl = $id('dbl-status'), drawer = $id('dbl-drawer'), countEl = $id('dbl-count');

const q = $id('billq'), area = $id('billarea'), congress = $id('billcongress'), tracked = $id('billtracked'), more = $id('billmore');
const searchBox = layout.querySelector('.dbl-search');
if (K && K.icon) searchBox.innerHTML = K.icon('search').replace('<svg', '<svg class="mlk-search__icon" aria-hidden="true"');
if (q){ q.classList.add('mlk-search__input'); q.placeholder = 'Bill, title, sponsor'; q.setAttribute('aria-label', 'Search bills by bill, title or sponsor'); searchBox.append(q); }
if (area){ area.setAttribute('aria-label', 'Area'); layout.querySelector('.dbl-sel--area').append(area); }
if (congress){ congress.setAttribute('aria-label', 'Congress'); layout.querySelector('.dbl-sel--congress').append(congress); }
if (tracked){ tracked.classList.add('dbl-switch'); tracked.setAttribute('role', 'switch'); tracked.setAttribute('aria-label', 'Pinned only'); layout.querySelector('.dbl-pinned').prepend(tracked); }
if (more){ more.className = 'mlk-btn dbl-morebtn'; layout.querySelector('.dbl-more').append(more); }
function relabel(){
 if (area && area.options[0] && area.options[0].value === '') area.options[0].textContent = 'All areas';
 if (congress) [...congress.options].forEach(o => { o.textContent = o.value === '' ? 'All' : `${o.value}th`; });
}

/* ---------------- states ---------------- */
const SKELETON = Array.from({length: 8}, () => '<tr class="dbl-skelrow" aria-hidden="true"><td colspan="6"><div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div></td></tr>').join('');
let loaded = false;
function showLoading(){ loaded = false; layout.classList.remove('is-error'); layout.setAttribute('aria-busy', 'true'); rowsEl.innerHTML = SKELETON; countEl.textContent = ''; if (more){ more.style.display = 'none'; more.hidden = true; } }
/* restored-desk.js appends p.restore-loading ("Loading bill and staff records…"; on failure the message + Retry). It is
   moved into the table card; its Retry keeps restored-desk's handler (it removes the line and loads again). */
function adopt(node){
 if (!(node instanceof HTMLElement) || !node.classList.contains('restore-loading')) return;
 statusEl.append(node); node.classList.add('mlk-state');
 const sync = () => { const failed = !!node.querySelector('button'); node.classList.toggle('mlk-state--error', failed); layout.classList.toggle('is-error', failed); if (failed){ layout.removeAttribute('aria-busy'); rowsEl.innerHTML = ''; node.setAttribute('role', 'alert'); node.querySelectorAll('button').forEach(b => b.classList.add('mlk-btn')); } };
 sync(); new MutationObserver(sync).observe(node, {childList: true, subtree: true, characterData: true});
}
new MutationObserver(list => list.forEach(r => r.addedNodes.forEach(n => { if (n.parentNode === document.body) adopt(n); }))).observe(document.body, {childList: true});
[...document.querySelectorAll('body > p.restore-loading')].forEach(adopt);

/* ---------------- table ---------------- */
let selected = null;   // key of the bill in the drawer
function areaChips(list, max){
 const a = list || [], shown = max ? a.slice(0, max) : a, rest = a.length - shown.length;
 return shown.map(x => `<span class="dbl-area">${esc(x)}</span>`).join('') + (rest > 0 ? `<span class="dbl-area dbl-area--more" aria-label="${rest} more">+${rest}</span>` : '');
}
function rowHTML(b){
 const s = sponsorOf(b), c = cosponsorsOf(b), sel = b.key === selected;
 const title = b.title || b.display, areas = (b.areas || []).join(', ');
 return `<tr data-key="${esc(b.key)}" tabindex="0" class="dbl-row${sel ? ' is-selected' : ''}"${sel ? ' aria-current="true"' : ''}>
<td class="dbl-bill">${esc(b.display)}</td>
<td class="dbl-title"><span class="dbl-ellip" title="${esc(title)}">${esc(title)}</span></td>
<td class="dbl-areas"${areas ? ` title="${esc(areas)}"` : ''} data-n="${(b.areas || []).length}"><span class="dbl-areas__in">${areaChips(b.areas, 2) || '<span class="dbl-none">—</span>'}</span></td>
<td class="dbl-sponsor">${s ? `<span class="dbl-ellip" title="${esc(s.name)}">${dot(s.party)}${esc(shortName(s.name))}</span>` : '<span class="dbl-none">—</span>'}</td>
<td class="dbl-congress">${esc(b.congress)}th</td>
<td class="dbl-cos">${c.count.toLocaleString('en-US')}</td></tr>`;
}
function paint(){
 relabel();
 if (!BILLS.length && !window.__profilesReady){ if (!layout.classList.contains('is-error')) showLoading(); return; }
 loaded = true; layout.removeAttribute('aria-busy'); layout.classList.remove('is-error');
 const matches = matchesFor(), rows = matches.slice(0, billStateFilter.limit), filtered = matches.length !== BILLS.length;
 countEl.textContent = `Showing ${rows.length.toLocaleString('en-US')} of ${matches.length.toLocaleString('en-US')} ${filtered ? 'matching bills · ' + BILLS.length.toLocaleString('en-US') + ' total' : 'bills'}`;
 rowsEl.innerHTML = rows.length ? rows.map(rowHTML).join('') : '<tr class="dbl-emptyrow"><td colspan="6"><p class="mlk-state" role="status">No bills match these filters</p></td></tr>';
 if (more){ const left = matches.length - rows.length; more.style.display = left > 0 ? '' : 'none'; more.hidden = !(left > 0); more.textContent = `Load ${Math.min(40, left)} more bills`; }
 if (selected && !drawer.hidden) refreshDrawer();
 openDeepLink();
 info();
 fitChips();
}
/* AREAS shows at most two chips then +N; when two chips do not fit the column, the row shows one chip then +N */
let chipsQueued = false;
function fitChips(){
 if (chipsQueued) return; chipsQueued = true;
 requestAnimationFrame(() => {
  chipsQueued = false;
  const cells = [...rowsEl.querySelectorAll('td.dbl-areas[data-n]')].filter(td => +td.dataset.n > 1);
  cells.forEach(td => { if (td.dataset.fit === '1'){ const b = BILL_BY_KEY[td.parentNode.dataset.key]; if (b){ td.firstElementChild.innerHTML = areaChips(b.areas, 2); td.dataset.fit = '2'; } } });
  const tight = cells.filter(td => [...td.querySelectorAll('.dbl-area:not(.dbl-area--more)')].some(c => c.scrollWidth > c.clientWidth + 1));
  tight.forEach(td => { const b = BILL_BY_KEY[td.parentNode.dataset.key]; if (b){ td.firstElementChild.innerHTML = areaChips(b.areas, 1); td.dataset.fit = '1'; } });
 });
}
window.renderBills = renderBills = paint;   // legacy callers (setView, filters, pin, rebuildBillRepository) resolve the global binding

/* ---------------- drawer ---------------- */
function peopleHTML(b){
 const c = cosponsorsOf(b);
 const person = r => { const i = memberIndex(r.bioguide, r.name), label = esc(r.name), when = r.date ? `<span class="dbl-when">${esc(r.date)}</span>` : '', gone = r.withdrawn_date ? `<span class="dbl-when">withdrawn ${esc(r.withdrawn_date)}</span>` : '';
  const name = i != null && K && K.url ? `<a class="mlk-name" data-mlk-nav href="${esc(K.url('profile', {m: M[i].name, tab: 'overview'}))}">${label}</a>` : `<span>${label}</span>`;
  return `<li>${dot(r.party || (i != null ? M[i].party : ''))}${name}${when}${gone}</li>`; };
 const group = (label, rows) => rows.length ? `<h4 class="dbl-d-sublabel">${label} (${rows.length.toLocaleString('en-US')})</h4><ul class="dbl-d-people">${rows.map(person).join('')}</ul>` : '';
 return c.count ? group('Original cosponsors', c.originals) + group('Additional cosponsors', c.later) : '<p class="dbl-d-empty">No cosponsors</p>';
}
function drawerHTML(b){
 const s = sponsorOf(b), c = cosponsorsOf(b), live = b.live || {}, url = b.url || live.url || '';
 const pinned = !!billState(b.key).pinned;
 return `<div class="dbl-d-scroll">
 <div class="dbl-d-top"><p class="dbl-d-label">${esc(b.display)} · ${esc(b.congress)}th Congress</p><button type="button" class="mlk-close dbl-d-close" aria-label="Close bill detail">${K && K.icon ? K.icon('close') : '×'}</button></div>
 <h2 class="dbl-d-title" id="dbl-d-title">${esc(b.title || b.display)}</h2>
 ${s ? `<p class="dbl-d-sponsor">${dot(s.party)}<span>${esc(s.name)}</span></p>` : ''}
 <p class="dbl-d-cos" data-dbl-cos>${c.count.toLocaleString('en-US')} cosponsor${c.count === 1 ? '' : 's'}</p>
 ${(b.areas || []).length ? `<div class="dbl-d-areas">${areaChips(b.areas, 0)}</div>` : ''}
 <h3 class="dbl-d-label dbl-d-h">Summary</h3>
 <p class="dbl-d-summary">${esc(b.summary || 'No summary available.')}</p>
 <p class="dbl-d-stamp">${esc(cacheStamp(b))}</p>
 <div class="dbl-d-people-wrap" id="dbl-d-people" hidden></div>
 <details class="dbl-d-more"><summary>Coalition, lobbying and notes</summary><div class="dbl-d-legacy"></div></details>
</div>
<div class="dbl-d-actions">
 <button type="button" class="mlk-btn mlk-btn--primary dbl-d-show" aria-expanded="false" aria-controls="dbl-d-people">Show cosponsors</button>
 <button type="button" class="mlk-btn dbl-d-pin" data-pin="${esc(b.key)}" aria-pressed="${pinned}">${PIN}<span>${pinned ? 'Pinned' : 'Pin'}</span></button>
 ${url ? `<a class="dbl-d-link" href="${esc(url)}" target="_blank" rel="noopener">Open on Congress.gov</a>` : ''}
</div>`;
}
const PIN = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2h4l-.6 4 2.6 2.4V10H4V8.4L6.6 6z"/><path d="M8 10v4.5"/></svg>';
function openBill(key, {focus = false, fromKeyboard = false} = {}){
 const b = BILL_BY_KEY[key]; if (!b) return;
 const same = selected === key && !drawer.hidden;
 selected = key;
 if (!same){ drawer.innerHTML = drawerHTML(b); drawer.dataset.key = key; drawer.setAttribute('aria-labelledby', 'dbl-d-title'); }
 drawer.hidden = false; layout.classList.add('has-drawer');
 rowsEl.querySelectorAll('tr.dbl-row').forEach(tr => { const on = tr.dataset.key === key; tr.classList.toggle('is-selected', on); if (on) tr.setAttribute('aria-current', 'true'); else tr.removeAttribute('aria-current'); });
 setShellBill(key); fit(); fitChips();
 if (focus || fromKeyboard) drawer.focus({preventScroll: true});
}
function notFound(raw){
 selected = null;
 drawer.innerHTML = `<div class="dbl-d-scroll"><div class="dbl-d-top"><p class="dbl-d-label">Bill</p><button type="button" class="mlk-close dbl-d-close" aria-label="Close bill detail">${K && K.icon ? K.icon('close') : '×'}</button></div><p class="mlk-state" role="status">No bill matches this link</p></div>`;
 drawer.removeAttribute('aria-labelledby'); drawer.dataset.key = '';
 drawer.hidden = false; layout.classList.add('has-drawer'); fit();
}
function closeDrawer(){
 const key = selected;
 drawer.hidden = true; layout.classList.remove('has-drawer'); selected = null; drawer.dataset.key = '';
 rowsEl.querySelectorAll('tr.is-selected').forEach(tr => { tr.classList.remove('is-selected'); tr.removeAttribute('aria-current'); });
 setShellBill(null); fitChips();
 const row = key && rowsEl.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
 if (row && (drawer.contains(document.activeElement) || document.activeElement === document.body)) row.focus({preventScroll: true});
}
/* a repaint (pin, filters) keeps the open drawer: only the pin button and the count follow the data */
function refreshDrawer(){
 const b = BILL_BY_KEY[selected]; if (!b){ closeDrawer(); return; }
 const pin = drawer.querySelector('.dbl-d-pin'), pinned = !!billState(b.key).pinned;
 if (pin){ pin.setAttribute('aria-pressed', String(pinned)); pin.querySelector('span').textContent = pinned ? 'Pinned' : 'Pin'; }
}
function setShellBill(key){
 try {
  if (parent === window) return;
  const u = new URL(parent.location.href);
  if (u.searchParams.get('view') !== 'member-bills') return;
  if (key) u.searchParams.set('bill', key); else u.searchParams.delete('bill');
  if (u.href !== parent.location.href) parent.history.replaceState(parent.history.state, '', u.pathname + u.search + u.hash);
 } catch (e) { /* cross-origin or blocked: the drawer still works */ }
}
/* the drawer is in flow beside the table; it sticks to the frame top and fills the visible height */
let fitQueued = false;
function fit(){
 if (fitQueued) return; fitQueued = true;
 requestAnimationFrame(() => { fitQueued = false; if (drawer.hidden) return; const top = Math.max(0, layout.getBoundingClientRect().top); drawer.style.height = Math.max(240, innerHeight - top - 24) + 'px'; });
}
addEventListener('scroll', fit, {passive: true}); addEventListener('resize', () => { fit(); fitChips(); });

/* ---------------- events ---------------- */
rowsEl.addEventListener('click', e => {
 const tr = e.target.closest('tr.dbl-row'); if (!tr) return;
 if (e.target.closest('a,button,input')) return;
 const sel = getSelection && getSelection(); if (sel && String(sel).trim() && tr.contains(sel.anchorNode)) return;
 openBill(tr.dataset.key);
});
rowsEl.addEventListener('keydown', e => {
 const tr = e.target.closest && e.target.closest('tr.dbl-row'); if (!tr || e.target !== tr) return;
 if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openBill(tr.dataset.key, {fromKeyboard: true}); }
 else if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){ const n = e.key === 'ArrowDown' ? tr.nextElementSibling : tr.previousElementSibling; if (n && n.matches('tr.dbl-row')){ e.preventDefault(); n.focus(); } }
});
drawer.addEventListener('click', e => {
 if (e.target.closest('.dbl-d-close')){ e.preventDefault(); closeDrawer(); return; }
 const show = e.target.closest('.dbl-d-show');
 if (show){
  const box = $id('dbl-d-people'), open = box.hidden, b = BILL_BY_KEY[drawer.dataset.key];
  box.hidden = !open; show.setAttribute('aria-expanded', String(open)); show.textContent = open ? 'Hide cosponsors' : 'Show cosponsors';
  if (open && b && !box.dataset.done){
   box.dataset.done = '1'; box.innerHTML = peopleHTML(b);
   if (K && K.catalog) Promise.resolve(K.catalog()).then(() => { if (box.isConnected && drawer.dataset.key === b.key) box.innerHTML = peopleHTML(b); }).catch(() => {});
  }
  if (open) box.scrollIntoView({block: 'nearest'});
 }
});
drawer.addEventListener('toggle', e => {
 const d = e.target; if (!d.matches || !d.matches('.dbl-d-more') || !d.open) return;
 const slot = d.querySelector('.dbl-d-legacy'); if (slot.dataset.done) return;
 const b = BILL_BY_KEY[drawer.dataset.key]; if (!b || typeof billCard !== 'function') return;
 try {
  const t = document.createElement('template'); t.innerHTML = billCard(b);
  const body = t.content.querySelector('.bbody');
  if (body){ [...body.children].slice(0, 3).forEach(n => n.remove()); slot.append(...body.childNodes); }
  slot.dataset.done = '1';
 } catch (err) { slot.innerHTML = '<p class="mlk-state mlk-state--error" role="alert">This section could not load.</p>'; }
}, true);
document.addEventListener('keydown', e => {
 if (e.key !== 'Escape' || drawer.hidden || e.defaultPrevented) return;
 const t = e.target; if (t && t.closest && t.closest('select,.mlk-menu,.mlk-popover')) return;
 e.preventDefault(); closeDrawer();
});

/* ---------------- deep link ?bill=<congress:type:number> ---------------- */
let linkDone = !Q.get('bill');
function openDeepLink(){
 if (linkDone || !loaded || !BILLS.length) return;
 linkDone = true;
 const b = resolveBill(Q.get('bill'));
 if (!b){ notFound(Q.get('bill')); return; }
 let idx = matchesFor().findIndex(x => x.key === b.key);
 if (idx >= billStateFilter.limit){ billStateFilter.limit = Math.ceil((idx + 1) / 40) * 40; }
 selected = b.key;
 if (idx >= 0 && !rowsEl.querySelector(`tr[data-key="${CSS.escape(b.key)}"]`)){ paint(); }
 openBill(b.key);
 requestAnimationFrame(() => { const row = rowsEl.querySelector(`tr[data-key="${CSS.escape(b.key)}"]`); if (row) row.scrollIntoView({block: 'nearest'}); });
}

/* ---------------- "How to read this": the hidden legacy intro and the cache / culling line, verbatim ---------------- */
let infoSet = false;
function info(){
 if (infoSet || !K || !K.head || typeof K.head.setInfo !== 'function') return;
 const intro = legacyNodes.find(n => n.tagName === 'DIV' && !n.id && !n.className.replace('dbl-legacy', '').trim() && n.textContent.trim());
 const live = D.bill_live || {}, cull = D.legislation_culling || {}, cc = cull.counts || {};
 const cullText = cull.status ? ` | culling ${cc.scoring_ready_category_edges || 0} scoring edges, ${cc.held_decision_units || 0} held visible/non-scoring` : '';
 const fresh = (live.updated_at ? `Congress.gov cache ${live.updated_at}${Object.keys(live.failures || {}).length ? ' | ' + Object.keys(live.failures).length + ' last-good fallbacks' : ''}` : 'Congress.gov live cache not yet synced') + cullText;
 infoSet = true;
 K.head.setInfo({html: `${intro ? `<p>${esc(intro.textContent.trim())}</p>` : ''}<p>${esc(fresh)}</p>`});
}

/* ---------------- first paint: the view shows at once with loading rows ---------------- */
section.hidden = false;
paint();

window.MelangeBills = {
 open: key => openBill(key, {focus: true}), close: closeDrawer, selected: () => selected,
 rows: () => [...rowsEl.querySelectorAll('tr.dbl-row')].map(tr => tr.dataset.key), shortName, paint,
 showLoading, failNext(){ const p = document.createElement('p'); p.className = 'restore-loading'; p.textContent = 'Records could not load. '; const b = document.createElement('button'); b.textContent = 'Retry'; b.onclick = () => { p.remove(); paint(); }; p.append(b); showLoading(); document.body.append(p); }
};
})();
