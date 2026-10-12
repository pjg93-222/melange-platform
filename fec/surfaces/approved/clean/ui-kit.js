/* Melange UI kit (build 01): window.MLK. Loaded by the shell (before app.js) and by every frame document (before
   that frame's overlay scripts). In a frame, MLK reaches the shell's Compare store, page head, global search and
   navigation; opened on its own, a document gets a local store over the same localStorage key. See proof/01/HANDOFF.md. */
(() => {
'use strict';
if (window.MLK && window.MLK.version) return;
const W = window, D = document, DATA = '/staffer-live/approved/data/', MLK = {};
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function sameOriginTop(){ try { if (W.top !== W && W.top.document) return W.top; } catch (e) { /* cross-origin or detached */ } return null; }
const TOP = sameOriginTop();                  // null in the shell, or in a document opened on its own
const host = () => TOP || W;
function shell(){ try { return host().MelangeShell || null; } catch (e) { return null; } }
function topKit(){ try { return TOP && TOP.MLK && TOP.MLK !== MLK ? TOP.MLK : null; } catch (e) { return null; } }
const later = fn => setTimeout(fn, 0);

const ICONS = {
 search: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5"/><path d="m11 11 3.5 3.5"/></svg>',
 chevron: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5"/></svg>',
 info: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/><path d="M8 7.2v4M8 4.8v.1"/></svg>',
 close: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="m3 3 6 6M9 3 3 9"/></svg>',
 arrow: '<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M2.5 7h9M8 3.5 11.5 7 8 10.5"/></svg>'
};
const icon = name => ICONS[name] || '';

/* ---------------- Compare store ---------------- */
const STORE_KEY = 'melange-compare', STORE_MAX = 4, FULL_MESSAGE = 'Compare holds four members';
function normId(id){ const s = String(id ?? '').trim(); return s && s.length <= 32 ? s : ''; }
function readIds(key, max){
 try {
  const v = JSON.parse(W.localStorage.getItem(key) || '[]'), out = [];
  if (!Array.isArray(v)) return [];
  for (const x of v){ const id = normId(x); if (id && !out.includes(id)) out.push(id); if (out.length >= max) break; }
  return out;
 } catch (e) { return []; }
}
function createCompareStore(options = {}){
 const key = options.key || STORE_KEY, max = options.max || STORE_MAX, subs = new Set();
 let ids = readIds(key, max);
 const save = () => { try { W.localStorage.setItem(key, JSON.stringify(ids)); } catch (e) { /* no storage: the list lives in memory */ } };
 const emit = (type, id) => {
  const list = ids.slice(), detail = {type, id: id ?? null, ids: list.slice()};
  for (const fn of [...subs]){ try { fn(list.slice(), detail); } catch (err) { later(() => { throw err; }); } }
  try { W.dispatchEvent(new CustomEvent('melange-compare-change', {detail})); } catch (e) { /* no CustomEvent */ }
 };
 const onFull = options.onFull || (() => toast(FULL_MESSAGE, {ms: 3000}));
 const store = {
  max,
  list: () => ids.slice(),
  has: id => ids.includes(normId(id)),
  add(id){ id = normId(id); if (!id) return false; if (ids.includes(id)) return true;
   if (ids.length >= max){ onFull(id); emit('full', id); return false; }
   ids.push(id); save(); emit('add', id); return true; },
  remove(id){ id = normId(id); const i = ids.indexOf(id); if (i < 0) return false; ids.splice(i, 1); save(); emit('remove', id); return true; },
  toggle(id){ return store.has(id) ? (store.remove(id), false) : store.add(id); },
  clear(){ if (!ids.length) return; ids = []; save(); emit('clear', null); },
  subscribe(fn){ if (typeof fn !== 'function') return () => {}; subs.add(fn); return () => { subs.delete(fn); }; }
 };
 W.addEventListener('storage', e => {           // another browser tab changed the list
  if (e.key !== null && e.key !== key) return;
  const next = readIds(key, max);
  if (next.join('\n') !== ids.join('\n')){ ids = next; emit('sync', null); }
 });
 return store;
}
if (!TOP && !W.MelangeCompare) W.MelangeCompare = createCompareStore();
function storeRef(){
 try { if (TOP && TOP.MelangeCompare) return TOP.MelangeCompare; } catch (e) { /* fall through */ }
 if (!W.MelangeCompare) W.MelangeCompare = createCompareStore();
 return W.MelangeCompare;
}
const compare = {
 get max(){ return storeRef().max; },
 list: () => storeRef().list(),
 has: id => storeRef().has(id),
 add: id => storeRef().add(id),
 remove: id => storeRef().remove(id),
 toggle: id => storeRef().toggle(id),
 clear: () => storeRef().clear(),
 subscribe(fn){
  const s = storeRef(), off = s.subscribe(fn);
  if (s !== W.MelangeCompare) W.addEventListener('pagehide', off, {once: true});   // frame leaves: drop its listener
  return off;
 }
};

/* ---------------- toast ---------------- */
function toast(message, opts = {}){
 const k = topKit();
 if (k && !opts.local) return k.toast(message, opts);
 if (!D.body) return null;
 let region = D.getElementById('mlk-toasts');
 if (!region){ region = D.createElement('div'); region.id = 'mlk-toasts'; region.className = 'mlk-toasts'; region.setAttribute('role', 'status'); region.setAttribute('aria-live', 'polite'); D.body.append(region); }
 const text = String(message);
 let el = [...region.children].find(n => n.textContent === text);
 if (!el){ el = D.createElement('div'); el.className = 'mlk-toast'; el.textContent = text; region.append(el); }
 clearTimeout(el.mlkTimer);
 el.mlkTimer = setTimeout(() => el.remove(), opts.ms || 3000);
 return el;
}

/* ---------------- popover / menu / drawer ---------------- */
const openPanels = [];
const byId = p => typeof p === 'string' ? D.getElementById(p) : p;
function triggerFor(panel){ return panel.id ? D.querySelector(`[aria-controls="${panel.id.replace(/["\\]/g, '\\$&')}"]`) : null; }
function kindOf(panel){
 const role = panel.getAttribute('role');
 return panel.dataset.mlkKind || (role === 'menu' ? 'menu' : role === 'dialog' ? 'dialog' : panel.classList.contains('mlk-drawer') ? 'drawer' : 'popover');
}
function prune(){ for (let i = openPanels.length - 1; i >= 0; i--) if (!openPanels[i].panel.isConnected) openPanels.splice(i, 1); }
function isOpen(p){ const panel = byId(p); return !!panel && openPanels.some(o => o.panel === panel); }
function open(p, opts = {}){
 const panel = byId(p); if (!panel) return null;
 prune();
 for (const o of [...openPanels]) if (o.panel !== panel && !o.panel.contains(panel)) close(o.panel, {restoreFocus: false});
 if (isOpen(panel)) return panel;
 const kind = opts.kind || kindOf(panel), trigger = opts.trigger || triggerFor(panel);
 panel.hidden = false; panel.classList.add('is-open');
 if (trigger) trigger.setAttribute('aria-expanded', 'true');
 openPanels.push({panel, trigger, kind, onClose: opts.onClose, returnTo: opts.returnTo || trigger || D.activeElement});
 let target = opts.focus;
 if (target === undefined) target = kind === 'menu' ? panel.querySelector('[role=menuitem]:not([disabled])') : (kind === 'drawer' || kind === 'dialog') ? panel : null;
 if (target && target.focus){ if (target === panel && !panel.hasAttribute('tabindex')) panel.tabIndex = -1; target.focus({preventScroll: true}); }
 return panel;
}
function close(p, opts = {}){
 const panel = byId(p); if (!panel) return;
 const i = openPanels.findIndex(o => o.panel === panel), o = i >= 0 ? openPanels.splice(i, 1)[0] : null;
 panel.hidden = true; panel.classList.remove('is-open');
 const trigger = (o && o.trigger) || triggerFor(panel);
 if (trigger) trigger.setAttribute('aria-expanded', 'false');
 if (o && opts.restoreFocus !== false){
  const active = D.activeElement;
  if (!active || active === D.body || panel.contains(active)){ const r = o.returnTo; if (r && r.isConnected && r.focus) r.focus({preventScroll: true}); }
 }
 if (o && typeof o.onClose === 'function'){ try { o.onClose(); } catch (err) { later(() => { throw err; }); } }
}
function toggle(p, opts){ return isOpen(p) ? (close(p, opts), false) : (open(p, opts), true); }
function closeAll(opts){ prune(); for (const o of [...openPanels].reverse()) close(o.panel, opts); }
D.addEventListener('click', e => {
 const el = e.target && e.target.closest ? e.target : null; if (!el) return;
 const t = el.closest('[data-mlk-toggle]');
 if (t){ e.preventDefault(); toggle(t.getAttribute('data-mlk-toggle'), {trigger: t}); return; }
 const c = el.closest('[data-mlk-close]');
 if (c){ const id = c.getAttribute('data-mlk-close'), panel = id ? D.getElementById(id) : c.closest('.mlk-popover,.mlk-menu,.mlk-drawer'); if (panel){ e.preventDefault(); close(panel); } return; }
 const item = el.closest('.mlk-menu [role=menuitem]');
 if (item){ const menu = item.closest('.mlk-menu'); if (menu && isOpen(menu)) close(menu, {restoreFocus: false}); }
});
D.addEventListener('pointerdown', e => {
 prune();
 for (const o of [...openPanels].reverse()){
  if (o.panel.contains(e.target) || (o.trigger && o.trigger.contains(e.target))) continue;
  if (o.kind === 'drawer' && !o.panel.classList.contains('mlk-drawer--overlay')) continue;   // in-flow drawers close by × or Escape
  close(o.panel, {restoreFocus: false});
 }
}, true);
D.addEventListener('keydown', e => {
 if (e.key === 'Escape'){ prune(); const o = openPanels[openPanels.length - 1]; if (o){ e.preventDefault(); e.stopImmediatePropagation(); close(o.panel); } return; }
 const menu = e.target && e.target.closest ? e.target.closest('[role=menu]') : null;
 if (!menu) return;
 if (e.key === 'Tab'){ close(menu, {restoreFocus: false}); return; }
 if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
 const items = [...menu.querySelectorAll('[role=menuitem]:not([disabled])')]; if (!items.length) return;
 e.preventDefault();
 let i = items.indexOf(D.activeElement);
 i = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : (i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
 items[i].focus();
});
// A click inside another document never reaches this one: close popovers and menus when focus moves into an
// iframe (shell) or back into the shell (frame). Other blurs (tab switch, screenshots) leave them open.
function focusLeftDocument(){
 try { if (TOP) return TOP.document.hasFocus(); } catch (e) { return false; }
 const a = D.activeElement; return !!a && a.tagName === 'IFRAME';
}
W.addEventListener('blur', () => later(() => { if (!focusLeftDocument()) return; prune(); for (const o of [...openPanels]) if (o.kind !== 'drawer') close(o.panel, {restoreFocus: false}); }));

/* ---------------- shell bridges (framed documents) ---------------- */
function navigate(url){
 const s = shell();
 if (s && s.navigate){ host().setTimeout(() => s.navigate(url), 0); return; }
 host().location.href = url;
}
if (TOP){
 // Ctrl/Cmd+K inside a frame focuses the shell's global search.
 D.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K')){ const s = shell(); if (s && s.focusSearch){ e.preventDefault(); s.focusSearch(); } }
 }, true);
 // Kit links (<a data-mlk-nav href="/?view=...">) navigate the shell without reloading it.
 D.addEventListener('click', e => {
  const a = e.target && e.target.closest ? e.target.closest('a[data-mlk-nav]') : null;
  if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  e.preventDefault(); e.stopImmediatePropagation(); navigate(a.getAttribute('href'));
 }, true);
}
// Any setInfo call from this document (including setInfo(null)) claims the head's "How to read this" for this view.
let infoClaimed = false;
const head = {
 // A frame passes its own <iframe> so the shell can refuse a frame that is not the current view's (fix round 2).
 setInfo(info){ infoClaimed = true; const s = shell(); return !!(s && s.setHeadInfo && s.setHeadInfo(info || null, TOP ? frameEl() : undefined) !== false); },
 title(){ const s = shell(); return s && s.title ? s.title() : ''; }
};
function focusSearch(){ const s = shell(); if (s && s.focusSearch) s.focusSearch(); }
// Interim rule-1 route (fix round 1, R01-2): the engine subtitle that the v2 CSS hides with the old title row
// (lobby .product-title p, Influence #app>header>.sub) goes verbatim behind the head's "How to read this" until
// the view's owner calls MLK.head.setInfo(...). Runs once after the frame's load handlers; framed documents only.
const INFO_FALLBACK = ['.product-header .product-title p', '#app>header>.sub'];
function fallbackInfoText(){
 for (const sel of INFO_FALLBACK){ const el = D.querySelector(sel), t = el && el.textContent.replace(/\s+/g, ' ').trim(); if (t) return t; }
 return '';
}
function shellView(){ const s = shell(); try { return s && s.view ? s.view() : ''; } catch (e) { return ''; } }
function frameEl(){ try { return W.frameElement || null; } catch (e) { return null; } }
// The shell view this frame was created for: app.js stamps it on the engine iframe as data-view (fix round 2,
// R02-1). shellView() alone is wrong when the frame's script runs after the shell has already moved on.
const BORN_VIEW = TOP ? ((frameEl() && frameEl().getAttribute('data-view')) || shellView()) : '';
function applyInfoFallback(retry){
 if (infoClaimed) return;
 try { if (!W.frameElement || !W.frameElement.isConnected) return; } catch (e) { return; }   // a replaced frame stays silent
 if (shellView() !== BORN_VIEW) return;                                                       // the shell moved on
 const text = fallbackInfoText();
 if (!text){ if (retry) setTimeout(() => applyInfoFallback(false), 1000); return; }
 const s = shell(); if (s && s.setHeadInfo) s.setHeadInfo({text}, frameEl());
}
if (TOP){
 if (D.readyState === 'complete') later(() => applyInfoFallback(true));
 else W.addEventListener('load', () => later(() => applyInfoFallback(true)), {once: true});
}

/* ---------------- data: colour maps (by reference) and the member catalog ---------------- */
let colorsP = null, COLORS = null;
function colors(){
 const k = topKit();
 if (k) return k.colors().then(c => (COLORS = c));
 if (!colorsP) colorsP = fetch(DATA + 'colors.json').then(r => { if (!r.ok) throw Error(`Colour maps could not load (${r.status}).`); return r.json(); })
  .then(c => (COLORS = c)).catch(err => { colorsP = null; throw err; });
 return colorsP;
}
function colorsSync(){ if (COLORS) return COLORS; const k = topKit(); try { return k ? k.colorsSync() : null; } catch (e) { return null; } }
function colorMap(name, group = 'desk'){ const c = colorsSync(); return (c && c[group] && c[group][name]) || null; }
function colorFor(name, key, group = 'desk'){ const m = colorMap(name, group); return m && Object.prototype.hasOwnProperty.call(m, key) ? m[key] : ''; }
function partyColor(party){
 const c = colorsSync(); if (!c) return '';
 const p = String(party ?? '');
 if (/^[DRI?]$/.test(p)) return ((c.influence && c.influence.PTCOL) || {})[p] || '';
 const m = (c.desk && c.desk.PTCOL) || {};
 return Object.prototype.hasOwnProperty.call(m, p) ? m[p] : (m[''] || '');
}
let catP = null, CAT = null;
const IDX = new WeakMap();
function shellCatalog(){ try { const ml = host().ML; return ml && ml.catalog ? ml.catalog : null; } catch (e) { return null; } }
function catalog(){
 const c = shellCatalog(); if (c){ CAT = c; return Promise.resolve(c); }
 if (!catP) catP = fetch(DATA + 'catalog.json').then(r => { if (!r.ok) throw Error(`Member catalog could not load (${r.status}).`); return r.json(); })
  .then(v => (CAT = v)).catch(err => { catP = null; throw err; });
 return catP;
}
function index(c){ let m = IDX.get(c); if (!m){ m = new Map(); for (const row of c.members || []){ const id = row.bio && row.bio.bioguide; if (id) m.set(String(id), row); } IDX.set(c, m); } return m; }
function memberById(id){ return catalog().then(c => index(c).get(String(id)) || null); }
function memberByIdSync(id){ const c = CAT || shellCatalog(); return c ? index(c).get(String(id)) || null : null; }

/* ---------------- pager ---------------- */
const fmt = n => Number(n).toLocaleString('en-US');
function pageSlice(rows, page = 0, size = 25){
 const total = rows.length, pages = Math.max(1, Math.ceil(total / size));
 page = Math.min(Math.max(0, Math.floor(page) || 0), pages - 1);
 const start = page * size;
 return {rows: rows.slice(start, start + size), page, pages, start, end: Math.min(start + size, total), total};
}
function pager({total = 0, page = 0, size = 25, sizes = [25, 50, 100]} = {}){
 const s = pageSlice({length: total, slice: () => []}, page, size);
 const range = total ? `${fmt(s.start + 1)}–${fmt(s.end)} of ${fmt(total)}` : '0 of 0';
 return `<div class="mlk-pager"><span class="mlk-pager__range">${range}</span><div class="mlk-pager__controls">`
  + `<button type="button" class="mlk-btn" data-mlk-page="prev" ${s.page <= 0 ? 'disabled' : ''}>Previous</button>`
  + `<button type="button" class="mlk-btn" data-mlk-page="next" ${s.page >= s.pages - 1 ? 'disabled' : ''}>Next</button>`
  + (sizes && sizes.length ? `<label class="mlk-select"><select data-mlk-page-size aria-label="Rows per page">${sizes.map(n => `<option value="${n}" ${n === size ? 'selected' : ''}>${n} per page</option>`).join('')}</select></label>` : '')
  + '</div></div>';
}
function bindPager(root, state, onChange){
 root.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-mlk-page]'); if (!b || !root.contains(b) || b.disabled) return; state.page = (state.page || 0) + (b.dataset.mlkPage === 'next' ? 1 : -1); onChange(state); });
 root.addEventListener('change', e => { if (!e.target.matches || !e.target.matches('[data-mlk-page-size]')) return; state.size = Number(e.target.value) || state.size; state.page = 0; onChange(state); });
}

/* ---------------- Member desk tab group (identical in the shell and inside the desk frame) ---------------- */
// Shell URL that keeps the research scope, exactly like app.js url(): issue, congress, period and bill ride along
// from the shell's current address (read from the top document when framed), then `extra` is applied.
const SCOPE_KEYS = ['issue', 'congress', 'period', 'bill'];
function url(view, extra = {}){
 let cur; try { cur = new URLSearchParams(host().location.search); } catch (e) { cur = new URLSearchParams(); }
 const q = new URLSearchParams(); q.set('view', view);
 for (const k of SCOPE_KEYS) if (cur.get(k)) q.set(k, cur.get(k));
 for (const [k, v] of Object.entries(extra || {})) if (v != null && v !== '') q.set(k, v);
 return '/?' + q;
}
const DESK_TABS = [['members', 'Members'], ['member-bills', 'Bills'], ['efficiency', 'Efficiency'], ['coalition', 'Coalition builder'], ['compare', 'Compare'], ['contact', 'Contact log']];
const DESK_MENU = [['exports', null, 'Notes & exports'], ['members', {deskaction: 'import-notes'}, 'Import notes'], ['members', {deskaction: 'export-overrides'}, 'Export stance overrides'], ['members', {deskaction: 'import-overrides'}, 'Import overrides']];
const DESK_ALIAS = {spending: 'efficiency', investment: 'efficiency'};
let seq = 0;
function deskTabs(active, opts = {}){
 const current = DESK_ALIAS[active] || active, id = opts.menuId || `mlk-deskmenu-${++seq}`, href = typeof opts.href === 'function' ? opts.href : url;
 const tabs = DESK_TABS.map(([v, label]) => `<a class="mlk-tab" href="${esc(href(v, {}))}" data-route data-mlk-nav${v === current ? ' aria-current="page"' : ''}>${esc(label)}</a>`).join('');
 const items = DESK_MENU.map(([v, extra, label]) => `<a role="menuitem" class="mlk-menu__item" href="${esc(href(v, extra || {}))}" data-route data-mlk-nav tabindex="-1"${active === 'exports' && v === 'exports' && !extra ? ' aria-current="page"' : ''}>${esc(label)}</a>`).join('');
 return `<div class="mlk-deskbar"><nav class="mlk-tabs" aria-label="Member desk">${tabs}</nav>`
  + `<div class="mlk-anchor"><button type="button" class="mlk-btn mlk-btn--text mlk-deskbar__menu" aria-haspopup="menu" aria-expanded="false" aria-controls="${id}" data-mlk-toggle="${id}">Notes and overrides ${ICONS.chevron}</button>`
  + `<div class="mlk-menu" id="${id}" role="menu" aria-label="Notes and overrides" hidden>${items}</div></div></div>`;
}
function registerDeskView(name, def){ (W.MelangeDeskViews = W.MelangeDeskViews || {})[name] = def; return def; }
function onReady(fn){ if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', () => fn(), {once: true}); else fn(); }

Object.assign(MLK, {
 version: '01', framed: !!TOP, esc, icon,
 compare, createCompareStore, toast,
 open, close, toggle, isOpen, closeAll,
 popover: {open, close, toggle}, menu: {open, close, toggle}, drawer: {open, close, toggle},
 pager, pageSlice, bindPager,
 colors, colorsSync, colorMap, colorFor, partyColor,
 catalog, memberById, memberByIdSync,
 navigate, url, head, focusSearch, deskTabs, DESK_TABS, registerDeskView, onReady
});
W.MLK = MLK;
})();
