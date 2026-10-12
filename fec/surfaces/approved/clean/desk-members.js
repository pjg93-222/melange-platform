/* desk-members.js: overlay for original-desk.html (Members list, board #27); owner: prompt 02. Runs after desk-shell.js. */
/* Build 02. The legacy ranked view stays the engine: its filter controls (#q, #chamber, #area, #stancefilter,
   #priorityfilter, #fundedOnly, #potentialOnly) are MOVED into the new filter bar with their listeners, and the legacy
   renderRanked() still filters, orders and ranks the 533 members into its (now hidden) #rows. This file wraps
   renderRanked, reads the legacy order and rank text from #rows, and draws the board's table from the same member records
   (M, honorAt, STANDING_COLORS, HONOR_COLORS, money, f2, ordinal, potentialChip, priorityLabel...): named columns with a
   Columns menu (localStorage 'melange-members-columns'), header sorting, Group by, row checkboxes feeding the shared
   Compare store (MLK.compare), and 50-row paging. Data colours are taken by reference from the legacy maps. */
(() => {
'use strict';
const K = window.MLK, D0 = document;
const Q = new URLSearchParams(location.search);
if (!K || (Q.get('deskview') || 'ranked') !== 'ranked') return;
const section = D0.getElementById('view-ranked'), legacyRows = D0.getElementById('rows');
if (!section || !legacyRows || typeof renderRanked !== 'function' || typeof M === 'undefined') return;

const esc = K.esc;
const PAGE = 50, COL_KEY = 'melange-members-columns';
const H = typeof HONORS !== 'undefined' ? HONORS : null;
const DASH = '<span class="dm-none">—</span>';

/* ---------------- member facts (legacy helpers, by reference) ---------------- */
const idOf = m => String((m.honors && m.honors.member_id) || (m.bio && m.bio.bioguide) || '');
const overall = m => (H ? honorAt(m, '') : null);
function place(m){
 const d = String(m.district || '');
 let x = d.match(/^Congressional District (\d+) \(\d+\w* Congress\), (.+)$/);
 if (x) return `${x[2]} ${ordinal(x[1])}`;
 x = d.match(/District \(at Large\) \(\d+\w* Congress\), (.+)$/);
 if (x) return `${x[1]} at large`;
 return d || m.state || '—';
}
const subline = m => [m.chamber || '—', m.party || '—', place(m)].join(' · ');
function pill(label, colour, title){
 const t = title ? ` title="${esc(title)}"` : '';
 return colour ? `<span class="mlk-pill" style="--mlk-dot:${esc(colour)}"${t}>${esc(label)}</span>`
  : `<span class="mlk-pill mlk-pill--none"${t}>${esc(label)}</span>`;
}
function stanceCell(m){
 if (!H){ const t = stanceAt(m, ''); return pill(stanceName(t), STCOL[t] || '', ''); }
 const row = overall(m);
 return pill(row.stance, STANDING_COLORS[row.stance] || '', honorFacts(m, ''));
}
function tierCell(m){
 const t = H ? overall(m).priority_tier : (m.tier || 0);
 const title = H ? priorityLabel(m) : (t ? 'Primacy tier ' + t : 'Not tiered');
 return t ? `<span class="mlk-tier" title="${esc(title)}">T${t}</span>` : `<span class="mlk-tier mlk-tier--none" title="${esc(title)}">—</span>`;
}
const pct = v => Math.max(0, Math.min(100, (Number(v) || 0) * 100));
function scoreCell(m){
 // .sbar keeps today's score-bar fill (the desk stylesheet's gradient); desk-members.css sets the board's size and track
 return `<span class="dm-score"><span class="dm-score__v">${esc(f2(m.score))}</span><span class="sbar dm-bar" aria-hidden="true"><i style="width:${pct(m.score).toFixed(1)}%"></i></span></span>`;
}
const tipBox = D0.createElement('template');
function potentialTitle(m){ tipBox.innerHTML = potentialChip(m); const el = tipBox.content.firstElementChild; return el ? el.getAttribute('title') || '' : ''; }
function potentialCell(m){
 const p = m.demographic_potential || {};
 if (!p.qualified || p.score == null) return DASH;
 return `<span title="${esc(potentialTitle(m))}">${esc(Number(p.score).toFixed(1))}</span>`;
}
const unresolved = m => String(m.money_source_status || '').startsWith('UNRESOLVED');
const moneyCell = (m, v) => unresolved(m) ? 'Unresolved' : (v > 0 ? esc(money(v)) : DASH);
function electoralCell(m){
 const v = m.ev_value, play = m.ev_play;
 const val = v > 0 ? esc(money(v)) : DASH;
 return play && play !== 'No active play' ? `${val}<span class="mlk-sub">${esc(play)}</span>` : val;
}
function areaCell(m, area){
 if (!area) return DASH;
 if (!H){ const t = stanceAt(m, area); return pill(stanceName(t), STCOL[t] || '', ''); }
 const row = honorAt(m, area);
 const detail = `${honorRank(row)}${row.evidence_status === 'observed' ? ' of ' + row.cohort_size : ''} · ${row.action_count} actions`;
 return pill(honorLabel(m, area), HONOR_COLORS[row.recognition] || '', honorFacts(m, area)) + `<span class="mlk-sub">${esc(detail)}</span>`;
}
function committeeCell(m){
 const list = m.committees || [];
 if (!list.length) return DASH;
 const names = [...new Set(list.map(c => committeeBase(c.name)).filter(Boolean))];
 const title = list.map(c => c.name + (c.role ? ' · ' + c.role : '')).join('\n');
 return `<span title="${esc(title)}">${list.length}</span><span class="mlk-sub dm-clip">${esc(names.join(' · '))}</span>`;
}

/* ---------------- columns ---------------- */
// key, header, menu label, default on, first sort direction, comparator (a,b rows; ascending sense)
const num = (f) => (a, b) => (f(a.m) || 0) - (f(b.m) || 0);
const COLS = [
 {key: 'rank', head: 'Rank', menu: 'Priority rank', def: true, dir: 'asc', cmp: null, cell: r => esc(r.rk).replace(/\d+–\d+/, '<span class="dm-nw">$&</span>')},   // a tie range breaks only after "Tied"
 {key: 'member', head: 'Member', menu: null, def: true, dir: 'asc', cmp: (a, b) => a.m.name.localeCompare(b.m.name), cell: r => `<a class="mlk-name dm-name" data-mlk-nav href="${esc(K.url('profile', {m: r.m.name, tab: 'overview'}))}">${esc(r.m.name)}</a><span class="mlk-sub">${esc(subline(r.m))}</span>`},
 {key: 'stance', head: 'Stance overall', menu: 'Stance overall', def: true, dir: 'asc',
  cmp: (a, b) => H ? OVERALL_STANDINGS.indexOf(overall(a.m).stance) - OVERALL_STANDINGS.indexOf(overall(b.m).stance) : STANCE_ORDER.indexOf(stanceAt(a.m, '')) - STANCE_ORDER.indexOf(stanceAt(b.m, '')),
  cell: r => stanceCell(r.m)},
 {key: 'tier', head: 'Tier', menu: 'Priority tier', def: true, dir: 'asc',
  cmp: (a, b) => H ? overallRankOrder(a.m, b.m, 'tier') : ((a.m.tier || 99) - (b.m.tier || 99) || (a.m.tierrank || 9999) - (b.m.tierrank || 9999)),
  cell: r => tierCell(r.m)},
 {key: 'score', head: 'Model score', menu: 'Model score', def: true, dir: 'desc', cmp: num(m => m.score), cell: r => scoreCell(r.m)},
 {key: 'potential', head: 'Demographic potential', menu: 'Demographic Potential', def: true, dir: 'desc',
  // the legacy "District opportunity" sort, ascending sense (score, then market rank, then name)
  cmp: (a, b) => ((a.m.demographic_potential?.score ?? -1) - (b.m.demographic_potential?.score ?? -1))
   || ((b.m.demographic_potential?.rank ?? 9999) - (a.m.demographic_potential?.rank ?? 9999)) || b.m.name.localeCompare(a.m.name),
  cell: r => potentialCell(r.m)},
 {key: 'invested', head: 'CU PAC contributions', menu: 'CU PAC contributions', def: false, dir: 'desc', cmp: num(m => m.invested), cell: r => moneyCell(r.m, r.m.invested)},
 {key: 'indmoney', head: 'Bank and financial PACs', menu: 'Bank and financial PACs', def: false, dir: 'desc', cmp: num(m => m.ind_money), cell: r => moneyCell(r.m, r.m.ind_money)},
 {key: 'roi', head: 'Return per $10,000', menu: 'Return per $10,000', def: false, dir: 'desc', cmp: num(m => m.roi), cell: r => esc((r.m.roi || 0).toFixed(3))},
 {key: 'electoral', head: 'Electoral exposure', menu: 'Electoral exposure', def: false, dir: 'desc', cmp: num(m => m.ev_value), cell: r => electoralCell(r.m)},
 {key: 'area', head: 'Stance in selected area', menu: 'Stance in selected area', def: false, dir: 'asc',
  cmp: (a, b) => rstate.area ? (H ? honorRankOrder(a.m, b.m, rstate.area) : (b.m.radar[CATS.indexOf(rstate.area)] || 0) - (a.m.radar[CATS.indexOf(rstate.area)] || 0)) : 0,
  cell: r => areaCell(r.m, rstate.area)},
 {key: 'committees', head: 'Committee seats', menu: 'Committee seats', def: false, dir: 'desc', cmp: num(m => (m.committees || []).length), cell: r => committeeCell(r.m)}
];
const BY_KEY = Object.fromEntries(COLS.map(c => [c.key, c]));
const MENU_COLS = COLS.filter(c => c.menu);
const DEFAULT_ON = MENU_COLS.filter(c => c.def).map(c => c.key);
function loadColumns(){
 try {
  const v = JSON.parse(localStorage.getItem(COL_KEY));
  if (Array.isArray(v)) return new Set(v.filter(k => MENU_COLS.some(c => c.key === k)));
 } catch (e) { /* blocked or bad JSON: defaults */ }
 return new Set(DEFAULT_ON);
}
function saveColumns(){ try { localStorage.setItem(COL_KEY, JSON.stringify(MENU_COLS.filter(c => shown.has(c.key)).map(c => c.key))); } catch (e) { /* no storage: this page only */ } }
let shown = loadColumns();
const visibleCols = () => COLS.filter(c => !c.menu || shown.has(c.key));

/* ---------------- group by ---------------- */
const GROUPS = {
 none: null,
 chamber: {label: 'Chamber', key: m => m.chamber || '—', name: k => k},
 party: {label: 'Party', key: m => m.party || '—', name: k => k},
 state: {label: 'State', key: m => m.state || '—', name: k => k},
 tier: {label: 'Priority tier', key: m => { const t = H ? overall(m).priority_tier : (m.tier || 0); return t ? t : 9; }, name: (k, m) => H ? priorityLabel(m) : (k === 9 ? 'Not tiered' : 'Tier ' + k)}
};

/* ---------------- state ---------------- */
const st = {sort: 'rank', dir: 'asc', group: 'none', limit: PAGE, rows: [], shownRows: [], fail: false};

/* ---------------- build the filter bar from the legacy controls ---------------- */
const $id = id => D0.getElementById(id);
const legacy = {controls: section.querySelector('.controls'), countline: section.querySelector('.countline'), tablewrap: section.querySelector('.tablewrap'),
 q: $id('q'), chamber: $id('chamber'), area: $id('area'), stance: $id('stancefilter'), prio: $id('priorityfilter'), funded: $id('fundedOnly'), potential: $id('potentialOnly')};
for (const k of ['controls', 'countline', 'tablewrap']) if (legacy[k]) legacy[k].classList.add('dm-legacy');

const filters = D0.createElement('div');
filters.className = 'dm-filters';
filters.setAttribute('role', 'search');
filters.setAttribute('aria-label', 'Filter members');
filters.innerHTML = '<div class="dm-row dm-row--1"></div><div class="dm-row dm-row--2"><span class="dm-spacer"></span></div>';
const row1 = filters.firstElementChild, row2 = filters.lastElementChild, spacer = row2.firstElementChild;

function selectWrap(select, label, cls){
 const w = D0.createElement('label');
 w.className = 'mlk-select dm-sel ' + cls;
 w.innerHTML = `<span class="mlk-select__label">${esc(label)}</span>`;
 if (select) w.append(select);
 return w;
}
if (legacy.q){
 const s = D0.createElement('div');
 s.className = 'mlk-search dm-search';
 s.innerHTML = K.icon('search').replace('<svg', '<svg class="mlk-search__icon"');
 legacy.q.classList.add('mlk-search__input');
 legacy.q.setAttribute('placeholder', 'Search member or district');
 s.append(legacy.q);
 row1.append(s);
}
if (legacy.chamber){
 legacy.chamber.classList.add('mlk-seg', 'dm-seg');
 legacy.chamber.setAttribute('role', 'group');
 legacy.chamber.setAttribute('aria-label', 'Chamber');
 const syncSeg = () => legacy.chamber.querySelectorAll('button').forEach(b => { b.type = 'button'; b.setAttribute('aria-pressed', String(b.classList.contains('on'))); });
 syncSeg();
 legacy.chamber.addEventListener('click', () => setTimeout(syncSeg, 0));
 row1.append(legacy.chamber);
}
const firstOption = (sel, text) => { const o = sel && sel.querySelector('option[value=""]'); if (o) o.textContent = text; };
if (legacy.area){ firstOption(legacy.area, 'All areas'); row1.append(selectWrap(legacy.area, 'Area:', 'dm-sel--area')); }
if (legacy.stance){ firstOption(legacy.stance, 'All'); row1.append(selectWrap(legacy.stance, 'Stance:', 'dm-sel--stance')); }
if (legacy.prio){ firstOption(legacy.prio, 'All tiers'); row1.append(selectWrap(legacy.prio, 'Priority:', 'dm-sel--prio')); }
// the legacy area handler rebuilds the stance options; keep the board's "All" wording on each rebuild
if (typeof configureRecognitionFilter === 'function'){
 const legacyConfigure = configureRecognitionFilter;
 configureRecognitionFilter = function(){ const r = legacyConfigure.apply(this, arguments); firstOption(legacy.stance, 'All'); return r; };
}
function toggleChip(input, label){
 const w = D0.createElement('label');
 w.className = 'mlk-chip dm-toggle';
 if (input){ input.classList.add('dm-toggle__input'); w.append(input); }
 w.insertAdjacentHTML('beforeend', `<span>${esc(label)}</span>`);
 return w;
}
spacer.before(toggleChip(legacy.funded, 'Funded'), toggleChip(legacy.potential, 'Demographic Potential'));

const groupSel = D0.createElement('select');
groupSel.id = 'dm-group';
groupSel.innerHTML = Object.entries(GROUPS).map(([k, g]) => `<option value="${k}">${esc(g ? g.label : 'None')}</option>`).join('');
row2.append(selectWrap(groupSel, 'Group by:', 'dm-sel--group'));
// Group by fits its current choice (board: "Group by: None" in a 125px box) instead of the widest option
const measure = D0.createElement('canvas').getContext('2d');
function fitSelect(sel){
 try {
  const cs = getComputedStyle(sel), text = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : '';
  measure.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  sel.style.width = Math.ceil(measure.measureText(text).width + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 2) + 'px';
 } catch (e) { sel.style.width = ''; }
}

const colAnchor = D0.createElement('div');
colAnchor.className = 'mlk-anchor dm-colanchor';
colAnchor.innerHTML = `<button type="button" class="mlk-btn dm-colbtn" id="dm-colbtn" aria-haspopup="dialog" aria-expanded="false" aria-controls="dm-colmenu" data-mlk-toggle="dm-colmenu">Columns ${K.icon('chevron')}</button>`;
row2.append(colAnchor);
const count = D0.createElement('span');
count.className = 'dm-count';
count.id = 'dm-count';
count.setAttribute('role', 'status');
row2.append(count);

const menu = D0.createElement('div');
menu.className = 'mlk-popover dm-colmenu';
menu.id = 'dm-colmenu';
menu.setAttribute('role', 'dialog');
menu.setAttribute('aria-label', 'Columns');
menu.hidden = true;
menu.innerHTML = '<p class="mlk-label dm-colmenu__title" aria-hidden="true">Columns</p><ul class="dm-colmenu__list">'
 + MENU_COLS.map(c => `<li><label class="dm-colmenu__item"><input type="checkbox" class="mlk-check" value="${c.key}"><span>${esc(c.menu)}</span></label></li>`).join('')
 + '</ul><div class="dm-colmenu__sep" role="separator"></div><button type="button" class="dm-reset" id="dm-colreset">Reset to default</button>';
filters.append(menu);

/* ---------------- table card ---------------- */
const card = D0.createElement('div');
card.className = 'mlk-tablecard dm-card';
card.innerHTML = '<table class="mlk-table dm-table"><thead><tr></tr></thead><tbody id="dm-rows"></tbody></table>'
 + '<div class="dm-more" hidden><button type="button" class="mlk-btn dm-more__btn"></button></div>'
 + '<div class="dm-state" hidden></div>';
const thead = card.querySelector('thead tr'), tbody = card.querySelector('tbody'), more = card.querySelector('.dm-more'), stateBox = card.querySelector('.dm-state');
const table = card.querySelector('table');

section.prepend(filters, card);

/* ---------------- rendering ---------------- */
/* Width (fix round B02-D1). Any optional column on -> .dm-table--dense (fixed layout, narrower columns, headers wrap).
   If the table is still wider than the card (e.g. all six optional columns in a 1440 window, or any frame under ~1000px),
   the card gets .dm-card--wide and becomes the scroller (desk-members.css), so every column stays reachable. The card's
   border-box width is used, so a scrollbar appearing in the wide state cannot flip the decision back. */
function setLayout(cols){ table.classList.toggle('dm-table--dense', cols.some(c => c.menu && !c.def)); }
function fit(){
 const inner = card.getBoundingClientRect().width - 2 * card.clientLeft;
 card.classList.toggle('dm-card--wide', table.offsetWidth > inner + 0.5);
}
let fitQueued = false;
function queueFit(){ if (fitQueued) return; fitQueued = true; requestAnimationFrame(() => { fitQueued = false; fit(); }); }
function syncMenu(){ menu.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = shown.has(cb.value); }); }
function headHTML(cols){
 const areaHead = rstate.area ? 'Stance · ' + rstate.area : 'Stance in selected area';
 const rankTitle = (D0.getElementById('th-rank') || {}).textContent || 'Priority rank';
 return `<th class="dm-c-check" scope="col"><input type="checkbox" class="mlk-check" id="dm-pickall" aria-label="Select visible members for Compare"></th>`
  + cols.map((c, i) => {
   const sorted = st.sort === c.key, aria = sorted ? (st.dir === 'asc' ? 'ascending' : 'descending') : 'none';
   const label = c.key === 'area' ? areaHead : c.key === 'rank' && rstate.area ? 'Field rank' : c.head;
   const title = c.key === 'rank' ? ` title="${esc(rankTitle)}"` : '';
   return `<th scope="col" class="dm-c-${c.key}${i === cols.length - 1 ? ' dm-last' : ''}" aria-sort="${aria}"${title}><button type="button" class="dm-sort" data-sort="${c.key}">${esc(label)}</button></th>`;
  }).join('');
}
function readLegacy(){
 const out = [];
 for (const tr of legacyRows.children){
  const i = Number(tr.getAttribute('data-i'));
  if (!Number.isInteger(i) || !M[i]) continue;
  const rk = tr.querySelector('.rk');
  out.push({i, m: M[i], id: idOf(M[i]), ord: out.length, rk: rk ? rk.textContent.trim() || '—' : '—'});
 }
 return out;
}
function ordered(rows){
 // comparators are written in the ascending sense; ties keep the legacy order (rank) in both directions
 const col = BY_KEY[st.sort], sgn = st.dir === 'desc' ? -1 : 1;
 let list = rows.slice();
 if (col && col.cmp) list.sort((a, b) => sgn * col.cmp(a, b) || a.ord - b.ord);
 else if (sgn < 0) list.reverse();
 const g = GROUPS[st.group];
 if (!g) return {list, groups: null};
 const buckets = new Map();
 for (const r of list){ const k = g.key(r.m); if (!buckets.has(k)) buckets.set(k, []); buckets.get(k).push(r); }
 const keys = [...buckets.keys()].sort((a, b) => (typeof a === 'number' && typeof b === 'number') ? a - b : String(a).localeCompare(String(b)));
 return {list: keys.flatMap(k => buckets.get(k)), groups: keys.map(k => ({k, rows: buckets.get(k), name: g.name(k, buckets.get(k)[0].m)}))};
}
function rowHTML(r, cols, selected){
 const on = selected.has(r.id);
 return `<tr data-i="${r.i}" data-id="${esc(r.id)}"${on ? ' class="is-selected"' : ''}>`
  + `<td class="dm-c-check"><input type="checkbox" class="mlk-check dm-pick" data-id="${esc(r.id)}" aria-label="${esc('Compare ' + r.m.name)}"${on ? ' checked' : ''}${r.id ? '' : ' disabled'}></td>`
  + cols.map(c => `<td class="dm-c-${c.key}">${c.cell(r)}</td>`).join('') + '</tr>';
}
function showState(kind, err){
 stateBox.hidden = !kind;
 card.classList.toggle('dm-card--state', !!kind);
 if (!kind){ stateBox.innerHTML = ''; return; }
 if (kind === 'empty') stateBox.innerHTML = '<p class="mlk-state" role="status">No members match these filters <button type="button" class="dm-clear">Clear filters</button></p>';
 else stateBox.innerHTML = `<p class="mlk-state mlk-state--error" role="alert">${esc((err && err.message) || 'Members could not load.')} <button type="button" class="mlk-btn dm-retry">Retry</button></p>`;
}
function showLoading(){
 const cols = visibleCols();
 setLayout(cols);
 thead.innerHTML = headHTML(cols);
 card.setAttribute('aria-busy', 'true');
 showState(null);
 more.hidden = true;
 tbody.innerHTML = `<tr class="dm-skel"><td colspan="${cols.length + 1}">${'<div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div>'.repeat(8)}</td></tr>`;
 count.textContent = 'Loading members…';
 fit();
}
function paint(){
 const cols = visibleCols();
 try {
  if (st.fail){ st.fail = false; throw new Error('Members could not load.'); }
  if (st.sort !== 'rank' && !visibleCols().some(c => c.key === st.sort)){ st.sort = 'rank'; st.dir = 'asc'; }
  st.rows = readLegacy();
  const {list, groups} = ordered(st.rows);
  const selected = new Set(K.compare.list());
  const visible = list.slice(0, st.limit);
  const inView = new Set(visible);
  let html = '';
  if (groups){
   for (const g of groups){
    const rows = g.rows.filter(r => inView.has(r));
    if (!rows.length) continue;
    html += `<tr class="mlk-grouprow dm-grouprow"><td colspan="${cols.length + 1}"><span class="dm-group__name">${esc(g.name)}</span><span class="dm-group__count">${g.rows.length.toLocaleString('en-US')}</span></td></tr>`;
    html += rows.map(r => rowHTML(r, cols, selected)).join('');
   }
  } else html = visible.map(r => rowHTML(r, cols, selected)).join('');
  setLayout(cols);
  thead.innerHTML = headHTML(cols);
  tbody.innerHTML = html;
  st.shownRows = visible;
  const n = st.rows.length;
  count.textContent = `${n.toLocaleString('en-US')} member${n === 1 ? '' : 's'}`;
  const rest = n - visible.length;
  more.hidden = rest <= 0;
  if (rest > 0) more.firstElementChild.textContent = `Show ${Math.min(PAGE, rest)} more`;
  showState(n ? null : 'empty');
  syncPickAll(selected);
 } catch (err) {
  setLayout(cols);
  thead.innerHTML = headHTML(cols);
  tbody.innerHTML = '';
  more.hidden = true;
  count.textContent = '';
  showState('error', err);
 }
 card.removeAttribute('aria-busy');
 fit();
}
function syncPickAll(selected){
 const all = D0.getElementById('dm-pickall');
 if (!all) return;
 const ids = st.shownRows.map(r => r.id).filter(Boolean), on = ids.filter(id => selected.has(id));
 const full = selected.size >= K.compare.max;
 all.checked = on.length > 0 && (on.length === ids.length || full);
 all.indeterminate = on.length > 0 && !all.checked;
 all.disabled = !ids.length;
}
function syncSelection(ids){
 const selected = new Set(ids || K.compare.list());
 for (const tr of tbody.querySelectorAll('tr[data-id]')){
  const on = selected.has(tr.dataset.id);
  tr.classList.toggle('is-selected', on);
  const cb = tr.querySelector('.dm-pick'); if (cb) cb.checked = on;
 }
 syncPickAll(selected);
}

/* ---------------- legacy engine hook ---------------- */
const legacyRender = renderRanked;
renderRanked = function(){                       // every legacy filter handler calls renderRanked() by name
 const r = legacyRender.apply(this, arguments);
 st.limit = PAGE;
 paint();
 return r;
};

/* ---------------- events ---------------- */
card.addEventListener('click', e => {
 const t = e.target;
 const sortBtn = t.closest('.dm-sort');
 if (sortBtn){
  const key = sortBtn.dataset.sort, col = BY_KEY[key];
  if (st.sort === key) st.dir = st.dir === 'asc' ? 'desc' : 'asc';
  else { st.sort = key; st.dir = col.dir; }
  st.limit = PAGE;
  paint();
  const again = thead.querySelector(`.dm-sort[data-sort="${key}"]`); if (again) again.focus({preventScroll: true});
  return;
 }
 if (t.closest('.dm-more__btn')){ st.limit += PAGE; paint(); return; }
 if (t.closest('.dm-clear')){ clearFilters(); return; }
 if (t.closest('.dm-retry')){ showLoading(); setTimeout(() => renderRanked(), 0); return; }
 const tr = t.closest('tbody tr[data-i]');
 if (!tr || t.closest('a,button,input,label')) return;
 const sel = window.getSelection && window.getSelection();
 if (sel && String(sel).length > 0) return;       // never hijack a text selection (legacy rowOpen rule)
 const m = M[Number(tr.dataset.i)];
 if (m) K.navigate(K.url('profile', {m: m.name, tab: 'overview'}));
});
card.addEventListener('change', e => {
 const t = e.target;
 if (t.classList.contains('dm-pick')){
  const id = t.dataset.id;
  if (t.checked){ if (!K.compare.add(id)) t.checked = false; }
  else K.compare.remove(id);
  syncSelection();
  return;
 }
 if (t.id === 'dm-pickall'){
  const selected = new Set(K.compare.list()), ids = st.shownRows.map(r => r.id).filter(Boolean);
  const on = ids.filter(id => selected.has(id));
  const wasChecked = on.length > 0 && (on.length === ids.length || selected.size >= K.compare.max);
  if (wasChecked) on.forEach(id => K.compare.remove(id));
  else for (const id of ids){ if (selected.has(id)) continue; if (!K.compare.add(id)) break; }   // the store refuses a 5th and shows its toast
  syncSelection();
 }
});
K.compare.subscribe(ids => syncSelection(ids));
groupSel.addEventListener('change', () => { st.group = groupSel.value in GROUPS ? groupSel.value : 'none'; st.limit = PAGE; fitSelect(groupSel); paint(); });
menu.addEventListener('change', e => {
 const cb = e.target;
 if (!cb.matches('input[type=checkbox]')) return;
 if (cb.checked) shown.add(cb.value); else shown.delete(cb.value);
 saveColumns();
 paint();
});
menu.querySelector('#dm-colreset').addEventListener('click', () => {
 shown = new Set(DEFAULT_ON);
 try { localStorage.removeItem(COL_KEY); } catch (e) { /* no storage */ }
 syncMenu();
 paint();
});
// the menu opens with focus on its first checkbox (the kit's declarative toggle would focus the panel)
colAnchor.querySelector('#dm-colbtn').addEventListener('click', () => setTimeout(() => {
 if (K.isOpen(menu)){ const first = menu.querySelector('input'); if (first) first.focus({preventScroll: true}); }
}, 0));
function clearFilters(){
 const fire = (el, type) => el && el.dispatchEvent(new Event(type, {bubbles: true}));
 if (legacy.q && legacy.q.value){ legacy.q.value = ''; fire(legacy.q, 'input'); }
 const all = legacy.chamber && legacy.chamber.querySelector('button[data-v="all"]');
 if (all && !all.classList.contains('on')) all.click();
 if (legacy.area && legacy.area.value){ legacy.area.value = ''; fire(legacy.area, 'change'); }
 if (legacy.stance && legacy.stance.value){ legacy.stance.value = ''; fire(legacy.stance, 'change'); }
 if (legacy.prio && legacy.prio.value){ legacy.prio.value = ''; fire(legacy.prio, 'change'); }
 for (const cb of [legacy.funded, legacy.potential]) if (cb && cb.checked){ cb.checked = false; fire(cb, 'change'); }
 renderRanked();
 if (legacy.q) legacy.q.focus({preventScroll: true});
}

/* ---------------- first paint: skeleton, then the table ---------------- */
syncMenu();
showLoading();
setTimeout(paint, 0);
fitSelect(groupSel);
if (D0.fonts && D0.fonts.ready) D0.fonts.ready.then(() => { fitSelect(groupSel); fit(); }, () => {});
window.addEventListener('resize', queueFit);

// Test and proof hooks (read-only views of the state, plus the two non-data states on demand)
window.MelangeMembers = {
 columns: () => visibleCols().map(c => c.key),
 sort: () => ({key: st.sort, dir: st.dir}),
 group: () => st.group,
 rows: () => st.shownRows.map(r => ({name: r.m.name, id: r.id, rank: r.rk})),
 total: () => st.rows.length,
 showLoading,
 failNext(){ st.fail = true; renderRanked(); },
 repaint: paint,
 layout: () => ({dense: table.classList.contains('dm-table--dense'), wide: card.classList.contains('dm-card--wide'), table: table.offsetWidth, card: Math.round(card.getBoundingClientRect().width)})
};
})();
