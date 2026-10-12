/* desk-efficiency.js: overlay for original-desk.html (Member desk > Efficiency, board #34; deskview=efficiency&mode=investment|spending); owner: prompt 06. Runs before restored-desk.js. */
/* Build 06. One Efficiency view with an Investment | Spending switch (the two legacy tabs), registered in the desk view
   registry (restored-desk.js loadRegistered: ctx.mode). The legacy engines stay the engines:
   - Investment: the legacy controls #effq, #effch and #effsort are MOVED into the control row with their listeners, and the
     legacy renderEff() still filters (effState) and orders the 533 members into its (hidden) #effrows. This file wraps
     renderEff, reads that order from #effrows, and draws the board's one-line table (25 per page) from the same member
     records and legacy helpers (M, honorAt, honorLabel, honorFacts, priorityLabel, potentialChip, money, MAXROI, rowOpen).
     SUGGESTED is m.opt_alloc exactly as renderEff shows it.
   - Spending: the legacy renderEfficiencyLayers() still fills #layers-underpriced, #layers-contested and #layers-easy; those
     three nodes are MOVED into three stacked cards and their tables restyled to the same row style (today's columns).
   - Method text: the legacy intro and "How to read this" notes go behind the page head's "How to read this", verbatim.
   Data colours are taken by reference (PTCOL, STANDING_COLORS, STCOL); gain/loss use the desk's own --green-text/--red-text.
   window.MelangeEfficiency exposes setMode/refresh and the legacy hooks for later builders and proofs. */
(() => {
'use strict';
const K = window.MLK, D0 = document;
const Q = new URLSearchParams(location.search);
if (!K || typeof K.registerDeskView !== 'function' || (Q.get('deskview') || '') !== 'efficiency' || typeof M === 'undefined') return;

const esc = K.esc;
const H = typeof HONORS !== 'undefined' ? HONORS : null;
const DASH = '<span class="eff-none">—</span>';
const SIZES = [25, 50, 100];
const COLS = 10;
const state = {mode: Q.get('mode') === 'spending' ? 'spending' : 'investment', page: 0, size: 25, order: null, spendingDone: false};
const api = window.MelangeEfficiency = {
 state,
 legacyRenderEff: typeof renderEff === 'function' ? renderEff : null,
 legacyRenderLayers: typeof renderEfficiencyLayers === 'function' ? renderEfficiencyLayers : null
};
let ui = null;

/* ---------------- member facts (legacy helpers, by reference) ---------------- */
const partyColour = p => (typeof PTCOL !== 'undefined' && PTCOL[p]) || K.partyColor(p) || '';
const dot = p => { const c = partyColour(p); return `<span class="mlk-dot eff-dot"${c ? ` style="--mlk-dot:${esc(c)}"` : ''} aria-hidden="true"></span>`; };
const sub = m => `${m.chamber || '—'} · ${m.party || '—'}`;
function pill(label, colour, title){
 const t = title ? ` title="${esc(title)}"` : '';
 return colour ? `<span class="mlk-pill" style="--mlk-dot:${esc(colour)}"${t}>${esc(label)}</span>`
  : `<span class="mlk-pill mlk-pill--none"${t}>${esc(label)}</span>`;
}
function rankText(m){ return H ? (honorAt(m, '').rank ?? '—') : (m.rank ?? '—'); }              // renderEff
function tierCell(m){
 const t = H ? honorAt(m, '').priority_tier : (m.tier || 0);
 const row = H ? honorAt(m, '') : null;
 const title = H ? `${priorityLabel(m)} · ${row.rank == null ? 'No priority rank' : 'Priority rank #' + row.rank}` : (t ? 'Primacy tier ' + t : 'Not tiered');
 return t ? `<span class="mlk-tier" title="${esc(title)}">T${esc(t)}</span>` : `<span class="mlk-tier mlk-tier--none" title="${esc(title)}">—</span>`;
}
function standingCell(m){
 if (!H){ const t = m.overall_stance; return pill(stanceName(t), (typeof STCOL !== 'undefined' && STCOL[t]) || '', ''); }  // renderEff without HONORS
 const row = honorAt(m, '');
 return pill(honorLabel(m, ''), STANDING_COLORS[row.stance] || '', honorFacts(m, ''));                       // publicBadge(m,'')
}
const tipBox = D0.createElement('template');
function potentialTitle(m){ tipBox.innerHTML = potentialChip(m); const el = tipBox.content.firstElementChild; return el ? el.getAttribute('title') || '' : ''; }
function potentialCell(m){
 const p = m.demographic_potential || {};
 if (!p.qualified || p.score == null) return DASH;                                                            // potentialChip shows nothing
 return `<span title="${esc(potentialTitle(m))}">${esc(Number(p.score).toFixed(1))}</span>`;
}
const cash = v => { const t = money(v); return t === '—' ? DASH : esc(t); };
function rowHTML(i){
 const m = M[i];
 const ev = m.eff_value || 0, roi = m.roi || 0, gap = m.gap || 0;                                            // renderEff
 const gapTxt = gap > 0 ? ('+$' + Math.abs(gap).toLocaleString()) : gap < 0 ? ('−$' + Math.abs(gap).toLocaleString()) : '—';
 const gapCls = gap > 0 ? 'eff-pos' : gap < 0 ? 'eff-neg' : 'eff-zero';
 const bar = Math.max(0, Math.min(100, roi / MAXROI * 100)).toFixed(1);
 const href = K.url('profile', {m: m.name, tab: 'overview'});
 return `<tr data-i="${i}">`
  + `<td class="eff-c-rank">${esc(rankText(m))}</td>`
  + `<td class="eff-c-member"><span class="eff-member">${dot(m.party)}<span class="eff-who"><a class="mlk-name eff-name" data-mlk-nav href="${esc(href)}" title="Open ${esc(m.name)} — full profile">${esc(m.name)}</a><span class="eff-sub">${esc(sub(m))}</span></span></span></td>`
  + `<td class="eff-c-tier">${tierCell(m)}</td>`
  + `<td class="eff-c-standing">${standingCell(m)}</td>`
  + `<td class="eff-c-potential">${potentialCell(m)}</td>`
  + `<td class="eff-c-invested">${cash(m.invested)}</td>`
  + `<td class="eff-c-suggested">${m.opt_alloc > 0 ? esc(money(m.opt_alloc)) : DASH}</td>`
  + `<td class="eff-c-gap ${gapCls}">${gap ? esc(gapTxt) : DASH}</td>`
  + `<td class="eff-c-value ${ev >= 0 ? 'eff-pos' : 'eff-neg'}">${esc((ev >= 0 ? '+' : '−') + Math.abs(ev).toFixed(2))}</td>`   // U+2212, as GAP
  + `<td class="eff-c-roi"><span class="eff-roi"><span class="eff-roi__v">${esc(roi.toFixed(3))}</span><span class="mlk-bar eff-bar" style="--mlk-bar-v:${bar}%" aria-hidden="true"><i></i></span></span></td>`
  + '</tr>';
}
const SKELETON = Array.from({length: 10}, () => `<tr class="eff-skelrow" aria-hidden="true"><td colspan="${COLS}"><div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div></td></tr>`).join('');

/* ---------------- markup ---------------- */
function headRow(){
 const rankHead = H ? 'Priority rank' : 'Rank';
 const th = (cls, label, title) => `<th scope="col" class="${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</th>`;
 return '<tr>'
  + th('eff-c-rank', 'Rank', rankHead) + th('eff-c-member', 'Member') + th('eff-c-tier', 'Tier', H ? 'Overall priority tier' : 'Primacy tier')
  + th('eff-c-standing', 'Standing', H ? 'Behavioral standing' : 'Overall stance') + th('eff-c-potential', 'Demographic potential')
  + th('eff-c-invested', 'Invested') + th('eff-c-suggested', 'Suggested', 'Optimizer plan: credit-union dollars added above existing CU money')
  + th('eff-c-gap', 'Gap', 'What the plan adds above existing CU money; never negative') + th('eff-c-value', 'Value')
  + th('eff-c-roi', 'ROI · flips / $10k', 'ROI · expected weighted flips per $10k')
  + '</tr>';
}
function build(section){
 // legacy children of #view-efficiency (title, intro, .controls, .countline, .tablewrap) stay in the DOM, hidden
 [...section.children].forEach(el => el.classList.add('eff-legacy'));
 const q = D0.getElementById('effq'), ch = D0.getElementById('effch'), sort = D0.getElementById('effsort');
 const root = D0.createElement('div');
 root.className = 'eff';
 root.id = 'eff';
 root.innerHTML = `<div class="eff-controls">`
  + `<div class="mlk-seg eff-mode" role="group" aria-label="Efficiency measure"><button type="button" data-eff-mode="investment" aria-pressed="false">Investment</button><button type="button" data-eff-mode="spending" aria-pressed="false">Spending</button></div>`
  + `<div class="eff-invctl" role="group" aria-label="Filter and sort members"><div class="mlk-search eff-search">${K.icon('search').replace('<svg', '<svg class="mlk-search__icon" aria-hidden="true"')}</div><span class="eff-slot-ch"></span><label class="mlk-select eff-sort"></label></div>`
  + `<span class="eff-count" role="status" aria-live="polite"></span>`
  + `</div>`
  + `<div class="eff-panel" data-eff-panel="investment" hidden>`
  + `<div class="mlk-tablecard eff-card"><table class="mlk-table eff-table" aria-label="Investment efficiency"><thead>${headRow()}</thead><tbody class="eff-rows"></tbody></table><div class="eff-foot"></div></div>`
  + `</div>`
  + `<div class="eff-panel" data-eff-panel="spending" hidden>`
  + `<p class="mlk-state mlk-state--error eff-serror" role="alert" hidden></p>`
  + `<section class="eff-scard" aria-labelledby="eff-s-under"><header class="eff-scard__head"><h2 class="eff-scard__title" id="eff-s-under">Best value</h2></header><div class="eff-scard__body" data-eff-slot="layers-underpriced"></div></section>`
  + `<section class="eff-scard" aria-labelledby="eff-s-contested"><header class="eff-scard__head"><h2 class="eff-scard__title" id="eff-s-contested">Crowded field</h2></header><div class="eff-scard__body" data-eff-slot="layers-contested"></div></section>`
  + `<section class="eff-scard" aria-labelledby="eff-s-easy"><header class="eff-scard__head"><h2 class="eff-scard__title" id="eff-s-easy">Most accessible</h2><span class="eff-scard__sub">Top 15 easy targets</span></header><div class="eff-scard__body" data-eff-slot="layers-easy"></div></section>`
  + `</div>`;
 section.append(root);
 // move the live legacy controls (their oninput/onchange/onclick handlers update effState and call renderEff)
 const search = root.querySelector('.eff-search');
 if (q){ q.classList.add('mlk-search__input'); q.setAttribute('placeholder', 'Search member'); q.setAttribute('aria-label', 'Search member'); search.append(q); }
 else search.remove();
 const slot = root.querySelector('.eff-slot-ch');
 if (ch){ ch.classList.add('mlk-seg', 'eff-chamber'); ch.setAttribute('role', 'group'); ch.setAttribute('aria-label', 'Chamber'); ch.querySelectorAll('button').forEach(b => b.setAttribute('type', 'button')); slot.replaceWith(ch); }
 else slot.remove();
 const sel = root.querySelector('.eff-sort');
 if (sort){ sort.setAttribute('aria-label', 'Sort members'); sel.append(sort); } else sel.remove();
 // move the three legacy spending containers (renderEfficiencyLayers writes into them by id)
 root.querySelectorAll('[data-eff-slot]').forEach(box => { const src = D0.getElementById(box.dataset.effSlot); if (src){ src.classList.add('eff-layer'); box.append(src); } });
 return {
  root, section,
  modeBtns: [...root.querySelectorAll('[data-eff-mode]')],
  invctl: root.querySelector('.eff-invctl'),
  count: root.querySelector('.eff-count'),
  inv: root.querySelector('[data-eff-panel=investment]'),
  spend: root.querySelector('[data-eff-panel=spending]'),
  card: root.querySelector('.eff-card'),
  rows: root.querySelector('.eff-rows'),
  foot: root.querySelector('.eff-foot'),
  serror: root.querySelector('.eff-serror'),
  scards: [...root.querySelectorAll('.eff-scard')]
 };
}

/* ---------------- Investment ---------------- */
function syncChamber(){
 const ch = D0.getElementById('effch');
 if (ch) ch.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b.classList.contains('on') ? 'true' : 'false'));
}
function countText(n){
 const total = M.length;
 return n === total ? `${total.toLocaleString('en-US')} members` : `${n.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} members`;
}
function showLoading(){
 if (!ui) return;
 ui.card.setAttribute('aria-busy', 'true');
 ui.rows.innerHTML = SKELETON;
 ui.foot.innerHTML = '';
 ui.count.textContent = 'Loading members…';
}
function showError(message){
 if (!ui) return;
 ui.card.removeAttribute('aria-busy');
 ui.rows.innerHTML = `<tr class="eff-staterow"><td colspan="${COLS}"><p class="mlk-state mlk-state--error" role="alert">${esc(message || 'Efficiency records could not load.')}<br><button type="button" class="mlk-btn" data-eff-retry>Retry</button></p></td></tr>`;
 ui.foot.innerHTML = '';
 ui.count.textContent = '';
}
function drawRows(){
 if (!ui || !state.order) return;
 const s = K.pageSlice(state.order, state.page, state.size);
 state.page = s.page;
 ui.card.removeAttribute('aria-busy');
 ui.rows.innerHTML = s.rows.length ? s.rows.map(rowHTML).join('')
  : `<tr class="eff-staterow"><td colspan="${COLS}"><p class="mlk-state" role="status">No members match these filters</p></td></tr>`;
 ui.foot.innerHTML = s.total ? K.pager({total: s.total, page: s.page, size: state.size, sizes: SIZES}) : '';
 ui.count.textContent = countText(s.total);
 syncChamber();
}
function paintInvestment(){
 if (!ui) return;
 try {
  if (typeof api.legacyRenderEff !== 'function') throw Error('Efficiency records could not load.');
  api.legacyRenderEff();                                           // legacy filter + order (effState) into the hidden #effrows
  const legacy = D0.getElementById('effrows');
  if (!legacy) throw Error('Efficiency records could not load.');
  state.order = [...legacy.querySelectorAll('tr[data-i]')].map(tr => +tr.dataset.i).filter(i => i >= 0 && M[i]);
 } catch (e){ state.order = null; showError(e && e.message); return; }
 drawRows();
}
// every legacy caller (the search, chamber and sort handlers) resolves the global binding
function onLegacyChange(){ state.page = 0; paintInvestment(); }

/* ---------------- Spending ---------------- */
function decorateLayers(){
 ui.spend.querySelectorAll('.eff-layer').forEach(box => {
  box.querySelectorAll('.tablewrap').forEach(w => w.classList.add('eff-swrap'));
  box.querySelectorAll('table').forEach(t => t.classList.add('mlk-table', 'eff-stable'));
  box.querySelectorAll('.empty').forEach(p => { p.classList.add('mlk-state'); p.setAttribute('role', 'status'); });
  box.querySelectorAll('tbody tr').forEach(tr => {
   const td = tr.firstElementChild, a = td && td.querySelector('a.mlink[data-mi]');
   if (!a || td.querySelector('.eff-member')) return;
   const m = M[+a.dataset.mi];
   if (!m) return;
   a.classList.add('mlk-name', 'eff-name');
   const wrap = D0.createElement('span');
   wrap.className = 'eff-member';
   wrap.innerHTML = dot(m.party) + '<span class="eff-who"></span>';
   td.prepend(wrap);
   const who = wrap.querySelector('.eff-who');
   who.append(a);
   const s = D0.createElement('span'); s.className = 'eff-sub'; s.textContent = sub(m); who.append(s);
   td.classList.add('eff-c-member');
  });
 });
}
function paintSpending(){
 if (!ui) return;
 ui.serror.hidden = true;
 ui.scards.forEach(c => { c.hidden = false; });
 try {
  if (typeof api.legacyRenderLayers !== 'function') throw Error('Spending records could not load.');
  api.legacyRenderLayers();
  decorateLayers();
  state.spendingDone = true;
 } catch (e){
  state.spendingDone = false;
  ui.scards.forEach(c => { c.hidden = true; });
  ui.serror.innerHTML = `${esc('Spending records could not load.')}<br><button type="button" class="mlk-btn" data-eff-retry>Retry</button>`;
  ui.serror.hidden = false;
 }
}

/* ---------------- method text (verbatim) behind the head's "How to read this" ---------------- */
function infoFor(mode){
 const paras = [];
 if (mode === 'investment'){
  const sec = D0.getElementById('view-efficiency');
  const intro = sec && [...sec.children].find(el => el.tagName === 'DIV' && !el.className.includes('controls') && !el.classList.contains('countline') && !el.classList.contains('tablewrap') && !el.classList.contains('eff'));
  const hint = sec && sec.querySelector('.countline > span:not(#effcount)');
  if (intro && intro.textContent.trim()) paras.push(intro.textContent.trim());
  if (hint && hint.textContent.trim()) paras.push(hint.textContent.trim() + '.');
 } else {
  D0.querySelectorAll('#view-layers .methnote .methbody > div').forEach(d => { const t = d.textContent.trim(); if (t) paras.push(t); });
 }
 return paras.length ? {html: paras.map(t => `<p>${esc(t)}</p>`).join('')} : null;
}
function setInfo(mode){
 try { if (K.head && typeof K.head.setInfo === 'function') K.head.setInfo(infoFor(mode)); } catch (e){ /* the shell refused or is gone */ }
}

/* ---------------- mode ---------------- */
function syncAddress(mode){
 // the mode is part of the address (reload and shared links keep it); replaceState, so Back leaves the view as before
 try { const u = new URL(location.href); u.searchParams.set('mode', mode); history.replaceState(history.state, '', u.pathname + u.search + u.hash); } catch (e){ /* ignore */ }
 try {
  const top = window.top;
  if (top === window || top.location.origin !== location.origin) return;
  const u = new URL(top.location.href), v = u.searchParams.get('view');
  if (!['efficiency', 'spending', 'investment'].includes(v)) return;
  if (v === 'efficiency' && (u.searchParams.get('mode') || 'investment') === mode) return;
  u.searchParams.set('view', 'efficiency');
  u.searchParams.set('mode', mode);
  top.history.replaceState(top.history.state, '', u.pathname + u.search + u.hash);
 } catch (e){ /* detached or cross-origin: leave the address as it is */ }
}
function setMode(mode, opts = {}){
 if (!ui) return;
 mode = mode === 'spending' ? 'spending' : 'investment';
 state.mode = mode;
 ui.root.dataset.mode = mode;
 ui.modeBtns.forEach(b => b.setAttribute('aria-pressed', b.dataset.effMode === mode ? 'true' : 'false'));
 const inv = mode === 'investment';
 ui.inv.hidden = !inv;
 ui.spend.hidden = inv;
 ui.invctl.hidden = !inv;
 ui.count.hidden = !inv;
 if (opts.paint !== false){
  if (inv){ if (!state.order) paintInvestment(); }
  else if (!state.spendingDone) paintSpending();
 }
 setInfo(mode);
 if (opts.address !== false) syncAddress(mode);
}

/* ---------------- mount (desk view registry) ---------------- */
function mount(section){
 if (ui) return;
 ui = build(section);
 renderEff = onLegacyChange;                                     // wrap: legacy handlers call renderEff()
 ui.modeBtns.forEach(b => b.addEventListener('click', () => { if (state.mode !== b.dataset.effMode) setMode(b.dataset.effMode); }));
 K.bindPager(ui.foot, state, () => { drawRows(); const top = ui.card.getBoundingClientRect().top; if (top < 0) window.scrollBy(0, top - 8); });
 ui.rows.addEventListener('click', e => { const tr = e.target.closest && e.target.closest('tr[data-i]'); if (tr) rowOpen(e, tr); });
 ui.root.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-eff-retry]');
  if (!b) return;
  if (state.mode === 'investment'){ showLoading(); setTimeout(paintInvestment, 0); } else paintSpending();
 });
 ui.root.addEventListener('keydown', e => {
  if (e.key === 'Escape' && e.target === D0.getElementById('effq') && e.target.value){ e.preventDefault(); e.target.value = ''; e.target.dispatchEvent(new Event('input', {bubbles: true})); }
 });
 if (state.mode === 'investment') showLoading();
 // Investment paints after one frame (render() below), so the loading rows show while the 533 rows are ordered
 setMode(state.mode, {address: false, paint: state.mode !== 'investment'});
}
const firstPaint = () => new Promise(res => requestAnimationFrame(() => setTimeout(res, 0)));
K.registerDeskView('efficiency', {
 async render(section, ctx){
  state.mode = ctx && ctx.mode === 'spending' ? 'spending' : 'investment';
  mount(section);
  if (state.mode === 'investment' && !state.order){ await firstPaint(); if (!state.order) paintInvestment(); }
 }
});
Object.assign(api, {setMode, refresh: () => (state.mode === 'investment' ? paintInvestment() : paintSpending()), showLoading, showError, paintSpending, paintInvestment});
})();
