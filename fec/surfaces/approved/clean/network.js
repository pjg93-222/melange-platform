/* Original Influence engine and rankings, with only presentation/navigation changes. */
/* Build 08 (board #24): canvas first. One toolbar, the canvas takes the width, the issue summary floats on the
   canvas and the rankings live in one right rail (Targets | Budget split). Every engine control is MOVED (same
   node, same id, same listeners); the new Color / Layout / Links / Normalized controls only click the engine's own
   buttons and checkboxes. Engine layout, node colours, rings and sizes are untouched. Data colours are read from
   the engine (its rendered pills, PTCOL) by reference; this file types no colour. Hooks for 09: window.MelangeInfluence. */
/* Build 09 (board #26): selecting a member swaps the rail for a tabbed member panel (Overview | Reach | Scenario |
   Routes). The engine's #mpanel blocks are MOVED into the tabs (same nodes, ids and listeners); Overview is a new
   summary computed from the engine's own data and label functions. The canvas chip goes (Open profile replaces it),
   the hover tooltip shrinks to the name, and the issue summary becomes a legend strip along the canvas bottom. */
/* Build 10 (Paul, 2026-09-24): the Standard layout opens on the linked core (Fit still frames everyone), the legend card
   folds from its header, the Targets panel hides and reopens from the canvas, and the engine's self-check badge shows
   only with a result. The two remembered choices live in localStorage under melange-influence-*. */
(() => {
'use strict';
const query = new URLSearchParams(location.search);
const $ = id => document.getElementById(id);
const esc = v => (window.MLK && MLK.esc) ? MLK.esc(v) : String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const safe = (fn, fallback) => { try { return fn(); } catch (e) { return fallback; } };
const LAST = DATA.issues.length - 1;
$('speechcov').hidden = true;

// Camera-preserving member selection and the shell-profile reroute (both kept from the 09-21 overlay).
const oldSelect = selectMember;
selectMember = function(v){ const camera = {...cam}; oldSelect(v); Object.assign(cam, camera); draw(); };
document.addEventListener('click', e => {
 const a = e.target.closest && e.target.closest('a'); if (!a) return;
 const u = new URL(a.href, location.href);
 if (!(u.searchParams.has('m') && (u.pathname.includes('cu-member-desk') || u.pathname.includes('Moneyball')))) return;
 e.preventDefault();
 const name = u.searchParams.get('m');
 const href = (window.MLK && MLK.url) ? MLK.url('profile', {m: name, tab: 'overview'}) : '/?view=profile&m=' + encodeURIComponent(name);
 if (window.MLK && MLK.navigate) MLK.navigate(href); else parent.location.href = href;
});
document.addEventListener('visibilitychange', () => { if (!document.hidden){ layoutResize(); draw(); } });

const ICON_FILTER = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12l-4.6 5.4V13L6.6 14V8.4z"/></svg>';
const ICON_FIT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/><rect x="6" y="6" width="4" height="4"/></svg>';
const SEARCH_ICON = (window.MLK && MLK.icon) ? MLK.icon('search').replace('<svg', '<svg class="mlk-search__icon"') : '';
function el(tag, cls, html){ const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
function click(sel){ const b = document.querySelector(sel); if (b) b.click(); return !!b; }
function onBtn(sel){ const b = document.querySelector(sel + ' button.on'); return b ? b : null; }

let built = false, card, foot, tabsEl, lastInfo = '', cuFrameText = '';
const ui = {};
// 10: the legend fold and the Targets panel, remembered per browser; both open unless a reader closed them
const PREF_LEGEND = 'melange-influence-legend', PREF_RAIL = 'melange-influence-rail';
let legendOpen = readPref(PREF_LEGEND) !== 'closed', railOpen = readPref(PREF_RAIL) !== 'closed';

function makeSelect(label, cls, options, onPick){
 const lab = el('label', 'mlk-select ' + cls);
 lab.innerHTML = `<span class="mlk-select__label">${esc(label)}</span>`;
 const s = el('select'); s.setAttribute('aria-label', label.replace(/:$/, ''));
 s.innerHTML = options.map(([v, t, off]) => `<option value="${esc(v)}"${off ? ' disabled' : ''}>${esc(t)}</option>`).join('');
 s.addEventListener('change', () => { onPick(s.value); sync(); });
 lab.append(s); return [lab, s];
}

function build(){
 const bar = $('bar'), right = $('right'), wrap = $('netwrap'), vp = $('viewpanel'), vb = $('viewbtn');
 document.documentElement.classList.add('mlk-v8');
 $('app').classList.add('mlk-dark');

 // ---- toolbar: Issue · NETWORK · Color · Links · Layout · Find · Filters ----
 const legacy = el('div', 'mlk-v8-legacy'); legacy.hidden = true;          // emptied engine spans stay in the DOM
 const cu = [...bar.querySelectorAll('span[title]')].find(s => /CU frame/.test(s.textContent));
 cuFrameText = cu ? cu.getAttribute('title') : '';
 const issueLab = el('label', 'mlk-select mlk-v8-issue');
 issueLab.innerHTML = '<span class="mlk-select__label">Issue:</span>';
 issueLab.append(isel);

 const netcfg = $('netcfg'), netseg = $('netseg');
 netseg.classList.add('mlk-seg', 'mlk-seg--accent'); netseg.setAttribute('role', 'group'); netseg.setAttribute('aria-label', 'Network');
 const normBtn = netseg.querySelector('[data-net=holistic_norm]');
 if (normBtn) normBtn.classList.add('mlk-v8-hidden');
 const netLabel = netcfg.querySelector(':scope > label'); if (netLabel) netLabel.classList.add('mlk-v8-netlabel');
 const norm = el('label', 'mlk-v8-norm', '<input type="checkbox" class="mlk-check"><span>Normalized</span>'); norm.hidden = true;
 ui.norm = norm; ui.normBox = norm.querySelector('input');
 ui.normBox.addEventListener('change', () => { click(ui.normBox.checked ? '#netseg [data-net=holistic_norm]' : '#netseg [data-net=holistic]'); sync(); });
 if (normBtn) netcfg.append(norm);

 const colorOpts = [...document.querySelectorAll('#color button')].map(b => [b.dataset.c, b.textContent.trim()]);
 const [colorLab, colorSel] = makeSelect('Color:', 'mlk-v8-color', colorOpts, v => { colorAuto = false; click(`#color button[data-c="${v}"]`); });
 ui.color = colorSel;
 ui.colorLab = colorLab;
 // The Color default follows the issue until a colour is chosen (autoColor below); the engine's own handler still runs.
 const issueChange = isel.onchange;
 if (typeof issueChange === 'function') isel.onchange = function(){ autoColor(+isel.value); return issueChange.apply(this, arguments); };

 const layoutOpts = [...document.querySelectorAll('#ltog button')].map(b => [b.dataset.l, b.textContent.trim().replace(/ view$/, ''), b.dataset.l === 'committee' && !DATA.cx]);
 const [layoutLab, layoutSel] = makeSelect('Layout:', 'mlk-v8-layout', layoutOpts, v => click(`#ltog button[data-l="${v}"]`));
 ui.layout = layoutSel;
 ui.layoutLab = layoutLab;

 const links = el('div', 'mlk-anchor mlk-v8-links');
 links.innerHTML = '<button type="button" class="mlk-select mlk-v8-dd" id="mlk-v8-linkbtn" aria-haspopup="dialog" aria-expanded="false" aria-controls="mlk-v8-linkpop" data-mlk-toggle="mlk-v8-linkpop"><span class="mlk-select__label">Links:</span><span id="mlk-v8-linkcount"></span></button>'
  + '<div class="mlk-popover" id="mlk-v8-linkpop" role="dialog" aria-label="Links" hidden></div>';
 const pop = links.querySelector('#mlk-v8-linkpop');
 const lybar = $('lybar');
 [...lybar.querySelectorAll(':scope > label')].forEach(l => { const i = l.querySelector('input'); if (i) i.classList.add('mlk-check'); pop.append(l); });
 pop.addEventListener('change', sync);
 pop.addEventListener('change', autoReframe);                               // 10: the linked core follows the Links layers

 const find = el('div', 'mlk-search mlk-v8-find', SEARCH_ICON);
 const search = $('search'); search.classList.add('mlk-search__input'); search.placeholder = 'Find member';
 const findSpan = search.parentElement;
 find.append(search, $('names'));

 vb.className = 'mlk-btn mlk-v8-filters'; vb.innerHTML = ICON_FILTER + '<span>Filters</span>'; vb.setAttribute('aria-haspopup', 'dialog');
 vb.title = 'Mode, approach, overall priority, field honors and overall standing';
 vp.setAttribute('aria-label', 'Filters');

 [...bar.children].forEach(n => legacy.append(n));
 if (findSpan && findSpan !== bar) legacy.append(findSpan);
 bar.append(issueLab, netcfg, colorLab, links, layoutLab, find, vb, legacy);

 // ---- Filters popover (the engine's own View-options panel): Mode, Approach, Overall priority, Field honors, Overall standing ----
 const row = id => { const n = $(id); return n ? n.closest('#viewpanel > span') : null; };
 [row('color'), row('ltog'), row('lybar')].forEach(r => r && r.classList.add('mlk-v8-moved'));
 ['mode', 'postureseg', 'tierseg', 'fieldhonorseg'].forEach(id => { const s = $(id); if (s){ s.classList.add('mlk-seg'); s.setAttribute('role', 'group'); } });
 const post = $('postcard'), pr = row('postureseg');
 if (post && pr){ const det = el('details', 'mlk-v8-approach', '<summary>Approach notes</summary>'); det.append(post); pr.after(det); }
 document.addEventListener('keydown', e => { if (e.key === 'Escape' && vp.contains(document.activeElement)) setTimeout(() => vb.focus(), 0); });

 // ---- right rail: Targets | Budget split, footer ----
 right.classList.add('mlk-v8-rail');
 tabsEl = el('div', 'mlk-tabs mlk-tabs--quiet mlk-v8-tabs');
 tabsEl.setAttribute('role', 'tablist'); tabsEl.setAttribute('aria-label', 'Rankings');
 tabsEl.innerHTML = '<button type="button" role="tab" id="mlk-v8-tab-targets" data-tab="targets" aria-controls="mlk-v8-pane-targets" aria-selected="true" tabindex="0">Targets</button>'
  + '<button type="button" role="tab" id="mlk-v8-tab-budget" data-tab="budget" aria-controls="mlk-v8-pane-budget" aria-selected="false" tabindex="-1">Budget split</button>';
 const paneT = el('div', 'mlk-v8-pane'); paneT.id = 'mlk-v8-pane-targets'; paneT.setAttribute('role', 'tabpanel'); paneT.setAttribute('aria-labelledby', 'mlk-v8-tab-targets');
 const paneB = el('div', 'mlk-v8-pane'); paneB.id = 'mlk-v8-pane-budget'; paneB.setAttribute('role', 'tabpanel'); paneB.setAttribute('aria-labelledby', 'mlk-v8-tab-budget'); paneB.hidden = true;
 const rtog = $('rtog');
 paneT.append($('tpanel'));
 paneB.append($('budgetwrap'), $('optcard'));
 rtog.classList.add('mlk-seg', 'mlk-seg--accent'); rtog.setAttribute('role', 'group'); rtog.setAttribute('aria-label', 'Target list');
 foot = el('div', 'mlk-v8-foot'); foot.id = 'mlk-v8-foot';
 buildRailControls();
 right.prepend(tabsEl, ui.railHide, paneT, paneB); right.append(foot);
 tabsEl.addEventListener('click', e => { const b = e.target.closest('[role=tab]'); if (b) selectTab(b.dataset.tab); });
 tabsEl.addEventListener('keydown', e => {
  const tabs = [...tabsEl.querySelectorAll('[role=tab]')], i = tabs.indexOf(document.activeElement); if (i < 0) return;
  const k = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1}[e.key]; if (k == null) return;
  e.preventDefault(); const t = tabs[(k + tabs.length) % tabs.length]; selectTab(t.dataset.tab); t.focus();
 });
 foot.addEventListener('click', e => { if (e.target.closest('[data-tab]')) selectTab(e.target.closest('[data-tab]').dataset.tab, true); });

 // ---- canvas furniture: summary card (+ the engine's member chip) top left, engine notes top right, caption, zoom ----
 const tl = el('div', 'mlk-v8-tl');
 card = el('section', 'mlk-v8-card'); card.id = 'mlk-v8-card'; card.setAttribute('aria-label', 'Issue summary');
 card.addEventListener('click', e => { if (e.target.closest && e.target.closest('.mlk-v8-card__toggle')) toggleLegend(); });   // 10: the header folds the rows
 tl.append(card, $('mdlink'));
 renderCard({loading: true});
 const tr = el('div', 'mlk-v8-tr'); tr.append(ui.railShow, $('engchk'), $('layoutnote'), $('netnote'));
 const cap = el('p', 'mlk-v8-caption', 'white ring = funded · dot size = $ · scroll to zoom'); cap.id = 'mlk-v8-caption';
 const z = el('div', 'mlk-v8-zoom'); z.id = 'network-zoom'; z.setAttribute('role', 'group'); z.setAttribute('aria-label', 'Zoom');
 z.innerHTML = '<button type="button" class="mlk-btn mlk-btn--icon" aria-label="Zoom in">+</button><button type="button" class="mlk-btn mlk-btn--icon" aria-label="Zoom out">−</button><button type="button" class="mlk-btn mlk-btn--icon" aria-label="Fit">' + ICON_FIT + '</button>';
 z.children[0].onclick = () => zoomBy(1.2); z.children[1].onclick = () => zoomBy(1 / 1.2); z.children[2].onclick = () => { fitEveryone(); draw(); };   // 10: Fit frames all members
 wrap.append(tl, tr, cap, z);
 built = true;
 fitBar();
 if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { fitBar(); placeStrip(); }, () => {});
}

// Width floors so the default "Color: Overall standing" (board #24) and "Layout: Standard" show whole: 150px in
// network.css leaves "Overall standing" cut. They are dropped while the one-row toolbar has no room for them (All bills
// + Normalized at 1440), so the Filters button is never pushed off the bar. A wider network.css width still wins.
function fitBar(){
 const bar = $('bar'); if (!bar || !ui.colorLab || !ui.layoutLab) return;
 const floors = on => { ui.colorLab.style.minWidth = on ? '161px' : ''; ui.layoutLab.style.minWidth = on ? '133px' : ''; };
 floors(true);
 if (bar.scrollWidth > bar.clientWidth) floors(false);
}

// 09 (board #26): the legend strip sits beside the zoom group while it needs at most two lines there. The overall
// layer's eleven stances need three at 1440 (92px over the canvas), so the strip then takes the canvas width (two
// lines, 66px) and the zoom group moves just above it. Always measured in the board's place, so it cannot flap.
function placeStrip(){
 const tl = document.querySelector('.mlk-v8-tl'), z = $('network-zoom'), main = $('main');
 if (!tl || !z || !main) return;
 if (tl.classList.contains('mlk-v9-wide')) tl.classList.remove('mlk-v9-wide');
 if (z.style.bottom) z.style.bottom = '';
 if (!main.classList.contains('mlk-v9-member') || main.classList.contains('mlk-v9-nostrip')) return;
 const tops = [...tl.querySelectorAll('.mlk-v8-card__rows > li')].map(li => li.getBoundingClientRect()).filter(r => r.height > 0).map(r => r.top).sort((a, b) => a - b);
 const lines = tops.reduce((n, t, i) => n + (i && t - tops[i - 1] > 4 ? 1 : 0), tops.length ? 1 : 0);
 if (lines <= 2) return;
 tl.classList.add('mlk-v9-wide');
 const a = tl.getBoundingClientRect(), b = z.getBoundingClientRect();
 if (a.left < b.right + 12 && a.right + 12 > b.left && a.top < b.bottom + 8 && a.bottom > b.top)
  z.style.bottom = Math.ceil((parseFloat(getComputedStyle(z).bottom) || 0) + b.bottom - a.top + 8) + 'px';
}

function zoomBy(k){ const cx = W / 2, cy = H / 2; cam.tx = cx - (cx - cam.tx) * k; cam.ty = cy - (cy - cam.ty) * k; cam.s *= k; draw(); }

function selectTab(name, focus){
 if (!tabsEl) return;
 tabsEl.querySelectorAll('[role=tab]').forEach(t => { const on = t.dataset.tab === name; t.setAttribute('aria-selected', on ? 'true' : 'false'); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); });
 $('mlk-v8-pane-targets').hidden = name !== 'targets';
 $('mlk-v8-pane-budget').hidden = name !== 'budget';
}

const fmtDate = s => { const d = s ? new Date(String(s).slice(0, 10) + 'T12:00:00') : null; return d && !isNaN(d) ? d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}) : ''; };

// The card reads the engine's own "This issue" rows (#istats, rebuilt by refresh()): the stance rows with a pill.
const SKELETON = '<div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span></div>'.repeat(6);
// 09: the legend strip's tail. It is the rows' last item, so a wrapped strip can carry it on its last line.
const STRIP_CAP = 'white ring = funded · dot size = $';
// 10: the card's header is a real disclosure button; the rows and the footer sit in one body it shows or hides (open by
// default, remembered). While a member is selected the rows still show as the legend strip, folded or not (network.css).
const CHEVRON = (window.MLK && MLK.icon && MLK.icon('chevron')) || '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5"/></svg>';
const cardHead = title => `<h2 class="mlk-v8-card__title"><button type="button" class="mlk-v8-card__toggle" id="mlk-v8-card-toggle" aria-controls="mlk-v8-card-body" aria-expanded="${legendOpen ? 'true' : 'false'}"><span class="mlk-v8-card__name">${esc(title)}</span>${CHEVRON}</button></h2>`;
const cardBody = html => `<div class="mlk-v8-card__body" id="mlk-v8-card-body">${html}</div>`;
function renderCard(opts){
 if (!card) return;
 if (opts && opts.loading){ card.setAttribute('aria-busy', 'true'); card.innerHTML = cardHead('Issue summary') + cardBody(SKELETON); paintFold(); return; }
 card.removeAttribute('aria-busy');
 try {
  const rows = [...document.querySelectorAll('#istats .stat')].filter(r => r.querySelector('.pill'));
  const title = (isel.options[isel.selectedIndex] || {}).textContent || '';
  const asOf = DATA.honors_meta ? fmtDate(DATA.honors_meta.as_of) : '';
  const body = rows.length ? '<ul class="mlk-v8-card__rows">' + rows.map(r => {
   const pill = r.querySelector('.pill'), label = (r.querySelector(':scope > span') || r).textContent.trim(), n = (r.querySelector('b') || {}).textContent || '';
   return `<li class="mlk-v8-card__row"><span class="mlk-dot" style="--mlk-dot:${esc(pill.style.backgroundColor)}"></span><span class="mlk-v8-card__label">${esc(label)}</span><span class="mlk-v8-card__n">${esc(n.trim())}</span></li>`;
  }).join('') + '<li class="mlk-v9-cap">' + STRIP_CAP + '</li></ul>'
   : '<p class="mlk-v8-card__state">No members counted on this issue</p><p class="mlk-v9-cap">' + STRIP_CAP + '</p>';
  card.innerHTML = cardHead(title) + cardBody(`${body}<p class="mlk-v8-card__foot">${esc(N)} members${asOf ? ' · as of ' + esc(asOf) : ''}</p>`);
 } catch (err) {
  card.innerHTML = cardHead('Issue summary') + cardBody('<p class="mlk-v8-card__state mlk-v8-card__state--error" role="alert">Summary could not be computed</p><button type="button" class="mlk-btn mlk-btn--sm" data-mlk-v8-retry>Retry</button>');
  const b = card.querySelector('[data-mlk-v8-retry]'); if (b) b.onclick = () => { refresh(); };
 }
 paintFold();
 placeStrip();
}

// ---- 10: legend fold and Targets panel hide/show, both remembered per browser (storage may throw: then per page) ----
function readPref(key){ try { return window.localStorage.getItem(key); } catch (e) { return null; } }
function writePref(key, value){ try { window.localStorage.setItem(key, value); } catch (e) { /* no storage: the choice lasts this page */ } }
function paintFold(){
 if (!card) return;
 card.classList.toggle('is-folded', !legendOpen);
 const b = card.querySelector('.mlk-v8-card__toggle'); if (b) b.setAttribute('aria-expanded', legendOpen ? 'true' : 'false');
}
function toggleLegend(){ legendOpen = !legendOpen; writePref(PREF_LEGEND, legendOpen ? 'open' : 'closed'); paintFold(); placeStrip(); autoReframe(); }   // the open card reserves the canvas's left side

const ICON_PANEL = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M10 3v10"/></svg>';
// "Hide panel" sits at the right end of the rail's Targets | Budget split row (after the tabs in focus order);
// "Targets panel" (canvas top right) reopens the rail.
function buildRailControls(){
 const hide = el('button', 'mlk-btn mlk-btn--sm mlk-btn--text mlk-v8-railbtn', ICON_PANEL + '<span>Hide panel</span>');
 hide.type = 'button'; hide.id = 'mlk-v8-railhide'; hide.setAttribute('aria-controls', 'right'); hide.setAttribute('aria-expanded', 'true');
 hide.addEventListener('click', () => setRail(false, true));
 const show = el('button', 'mlk-btn mlk-btn--sm mlk-v8-railopen', ICON_PANEL + '<span>Targets panel</span>');
 show.type = 'button'; show.id = 'mlk-v8-railshow'; show.setAttribute('aria-controls', 'right'); show.setAttribute('aria-expanded', 'false'); show.hidden = true;
 show.addEventListener('click', () => setRail(true, true));
 ui.railHide = hide; ui.railShow = show;
}
// Collapsed: #main.mlk-v8-railoff gives the canvas the whole width (network.css); a selected member's panel still opens
// (the rail shows while #main.mlk-v9-member) and the rail folds away again when it closes. The canvas follows through the
// #netwrap ResizeObserver (layoutResize), which keeps a camera the reader moved.
function setRail(open, focus){
 railOpen = !!open; writePref(PREF_RAIL, railOpen ? 'open' : 'closed');
 paintRail();
 if (focus){ const t = railOpen ? ui.railHide : ui.railShow; if (t && !t.hidden && t.offsetParent) t.focus(); }
}
function paintRail(){
 const main = $('main'); if (!main || !ui.railHide) return;
 if (main.classList.contains('mlk-v8-railoff') === railOpen) main.classList.toggle('mlk-v8-railoff', !railOpen);
 const showOpen = !railOpen && !main.classList.contains('mlk-v9-member');
 if (ui.railShow.hidden === showOpen) ui.railShow.hidden = !showOpen;
 const exp = railOpen ? 'true' : 'false';
 if (ui.railHide.getAttribute('aria-expanded') !== exp) ui.railHide.setAttribute('aria-expanded', exp);
}

// ---- 10: the engine's self-check badge shows only a real result (✓ or a warning), never the Overall view's "—" ----
function watchCheck(){
 const ec = $('engchk'); if (!ec) return;
 const paint = () => { const idle = /^[\s—–-]*$/.test(ec.textContent || ''); if (ec.classList.contains('mlk-v8-idle') !== idle) ec.classList.toggle('mlk-v8-idle', idle); };
 new MutationObserver(paint).observe(ec, {childList: true, characterData: true, subtree: true});
 paint();
}

function renderFoot(){
 if (!foot) return;
 const o = DATA.opt;
 foot.innerHTML = o
  ? `<div class="mlk-v8-foot__row"><span>Budget across all issues</span><b class="mlk-v8-foot__big">$${(o.budget / 1e6).toFixed(1)}M</b></div><button type="button" class="mlk-v8-foot__win" data-tab="budget" title="Open Budget split">winnability ${o.base.toFixed(1)} → ${o.final.toFixed(1)}</button>`
  : '<div class="mlk-v8-foot__row"><span>Budget across all issues</span><b class="mlk-v8-foot__big">—</b></div><p class="mlk-v8-foot__note">Not available in this build</p>';
}

// Targets rows: party dot (PTCOL, by reference) in place of the stance pill, as the board draws them.
function paintTargets(){
 document.querySelectorAll('#targets .tgt[data-i]').forEach(r => {
  const i = +r.dataset.i, p = r.querySelector('.pill'), c = PTCOL[DATA.party[i]];
  if (p && c){ p.style.background = c; p.classList.add('mlk-v8-party'); }
 });
}

// "How to read this": the engine's method text for this view (subtitle, CU frame, the panel's cohort note,
// the overall priority counts, the target list mode and the network note), verbatim.
function updateInfo(){
 if (!window.MLK || !MLK.head) return;
 const txt = n => n ? n.textContent.replace(/\s+/g, ' ').trim() : '';
 const sub = txt(document.querySelector('#app>header>.sub'));
 const hints = [...document.querySelectorAll('#istats > .hint')];
 const note = txt(hints[hints.length - 1]);
 const prio = [...document.querySelectorAll('#istats .stat')].filter(r => !r.querySelector('.pill')).map(r => txt(r.querySelector('span')) + ' ' + txt(r.querySelector('b')));
 const mode = txt($('tgtmode')).replace(/^·\s*/, '');
 const net = $('netseg') ? $('netseg').getAttribute('title') : '';
 const issueName = (isel.options[isel.selectedIndex] || {}).textContent || '';
 const parts = [];
 if (sub) parts.push(`<p>${esc(sub)}.</p>`);
 if (note) parts.push(`<p><b>${esc(issueName)}.</b> ${esc(note)}</p>`);
 if (prio.length) parts.push(`<p><b>Overall priority.</b> ${prio.map(esc).join(' · ')}</p>`);
 if (mode) parts.push(`<p><b>Targets.</b> ${esc(mode)} · click a node to select a member.</p>`);
 if (cuFrameText) parts.push(`<p><b>CU frame.</b> ${esc(cuFrameText)}</p>`);
 if (net) parts.push(`<p><b>Network.</b> ${esc(net)}</p>`);
 const html = parts.join('');
 if (html === lastInfo) return;
 lastInfo = html;
 MLK.head.setInfo({html});
}

// The card doubles as the colour legend when the canvas is painted by stance; the engine legend shows otherwise.
function syncLegend(){
 const lg = $('legend'); if (!lg) return;
 const stanceColours = safe(() => !cascadeOn && !(route && routeCasc) && !viewFiltersActive() && !tierFilter
  && (colorBy === 'issue' || (colorBy === 'gen' && issue === LAST)), false);
 lg.classList.toggle('mlk-v8-off', !!stanceColours);
 const main = $('main'); if (main) main.classList.toggle('mlk-v9-nostrip', !stanceColours);   // 09: the bottom strip is the legend only while stance colours paint the map
 const spans = lg.querySelectorAll(':scope > span'), tail = spans[spans.length - 1];
 if (tail && /^○ white ring = funded/.test(tail.textContent)){ tail.style.display = 'none'; const br = tail.previousElementSibling; if (br && br.tagName === 'BR') br.style.display = 'none'; }
 placeStrip();
}

// Toolbar and rail state follow the engine (its .on classes, checkboxes and panel display), never the reverse.
let syncing = false;
function sync(){
 if (!built || syncing) return; syncing = true;
 try {
  const net = onBtn('#netseg'), netv = net ? net.dataset.net : '';
  // "All bills" stays the active segment while its Normalized variant runs.
  document.querySelectorAll('#netseg > button, #rtog > button, #viewpanel .mlk-seg > button, #mpanel .mlk-seg > button').forEach(b => {
   const on = b.classList.contains('on') || (b.dataset.net === 'holistic' && netv === 'holistic_norm');
   const v = on ? 'true' : 'false'; if (b.getAttribute('aria-pressed') !== v) b.setAttribute('aria-pressed', v);
  });
  if (ui.norm){ const show = netv === 'holistic' || netv === 'holistic_norm'; if (ui.norm.hidden === show){ ui.norm.hidden = !show; fitBar(); } ui.normBox.checked = netv === 'holistic_norm'; }
  const c = onBtn('#color'); if (c && ui.color.value !== c.dataset.c) ui.color.value = c.dataset.c;
  const l = onBtn('#ltog'); if (l && ui.layout.value !== l.dataset.l) ui.layout.value = l.dataset.l;
  const boxes = [...document.querySelectorAll('#mlk-v8-linkpop input[type=checkbox]')];
  const cnt = $('mlk-v8-linkcount'), txt = `${boxes.filter(b => b.checked).length} of ${boxes.length}`;
  if (cnt && cnt.textContent !== txt) cnt.textContent = txt;
  const member = $('mpanel') && $('mpanel').style.display === 'block', right = $('right');
  if (right.classList.contains('is-member') !== member){
   right.classList.toggle('is-member', member);
   const main = $('main'); if (main) main.classList.toggle('mlk-v9-member', member);
   paintRail();
   placeStrip();
   if (member) schedulePaint();
  }
 } finally { syncing = false; }
}

function afterRefresh(){ paintTargets(); renderCard(); updateInfo(); syncLegend(); sync(); }

// ---------------------------------------------------------------------------------------------------------------
// 10 (Paul, 2026-09-24): open on the linked core. In the Standard layout the automatic camera frames the bounding box of
// EVERY member with at least one drawn link (the Links layers now on; no trimming), so no linked member starts off the
// canvas, with fit()'s padding; while the legend card is open its right edge + 12 px is the frame's left edge. If that
// zooms in less than 1.15x past fit(), or the links cannot be read, the camera is fit() itself (everyone). Group by
// Policy Area and Committees keep fit(); the Fit button frames everyone. fit() is a global function of the engine's
// classic script, called unqualified by resize(), backToTargets() and the Cascade path mode, so reassigning the binding
// reaches every automatic call. Re-frames the overlay starts (layout or network switch, Links change, legend fold, a
// canvas size change from the rail, the member panel or a frame shown again) happen only while the camera is still the
// automatic one: a camera the reader moved (wheel, drag, zoom buttons, a route's fitCam) stays until the engine's own
// next auto-fit.
// ---------------------------------------------------------------------------------------------------------------
const CORE_PCT = [0, 1], MIN_GAIN = 1.15;
const view = {origFit: null, last: null, anim: null, overlayResize: false, seenW: 0, seenH: 0, tween: null, tweens: 0};
const lastKind = () => view.last ? view.last.kind : '';

// Members with at least one link drawn now, by the engine's draw() layer rules. View filters are left out on purpose:
// a tier or standing filter only fades members and must not re-aim the camera.
function linkedMembers(){
 const on = id => { const b = $(id); return !b || b.checked; };
 const lay = {c: on('ly_c'), s: on('ly_s'), h: on('ly_h'), e: on('ly_e'), p: on('ly_a')}; lay.m = lay.c || lay.s;
 const out = new Set();
 for (let k = 0; k < EDGES.length; k++){ if (ELAYS && !lay[ELAYS[k]]) continue; out.add(EDGES[k][0]); out.add(EDGES[k][1]); }
 const a = $('ly_a'), r = $('ly_r');
 if (a && a.checked && DATA.coap) for (const e of DATA.coap){ out.add(e[0]); out.add(e[1]); }
 if (r && r.checked && DATA.refs) for (const e of DATA.refs){ out.add(e[0]); out.add(e[1]); }
 return out;
}
// Percentile box (the engine's own index rule: floor at the low end, ceil at the high end) and fit()'s camera for a box.
function boxOf(ids, X, Y, lo, hi){
 const xs = ids.map(i => X[i]).filter(Number.isFinite).sort((a, b) => a - b), ys = ids.map(i => Y[i]).filter(Number.isFinite).sort((a, b) => a - b);
 if (!xs.length || !ys.length) return null;
 const at = (v, p, up) => v[up ? Math.ceil(p * (v.length - 1)) : Math.floor(p * (v.length - 1))];
 return [at(xs, lo), at(xs, hi, true), at(ys, lo), at(ys, hi, true)];
}
// fit()'s camera for a box: 24 px each side, 36 px top and bottom. `left` (the open legend's reserve) moves the left edge
// right; a reserve that would leave less than half the canvas is dropped (narrow windows keep the whole width).
function camFor(b, left){
 const x1 = W - 24, x0 = left > 24 && x1 - left >= W / 2 ? left : 24;
 const s = Math.min((x1 - x0) / Math.max(1, b[1] - b[0]), (H - 72) / Math.max(1, b[3] - b[2]));
 return {s, tx: (x0 + x1) / 2 - (b[0] + b[1]) * s / 2, ty: H / 2 - (b[2] + b[3]) * s / 2};
}
// The open legend card's right edge relative to #cv, + 12 px. 0 while it is folded, while a member is selected (the card
// is then the legend strip along the bottom) or while it is not shown.
function legendInset(){
 const main = $('main');
 if (!card || !legendOpen || (main && main.classList.contains('mlk-v9-member'))) return 0;
 const c = card.getBoundingClientRect(), v = cv.getBoundingClientRect();
 return c.width && c.height ? c.right - v.left + 12 : 0;
}
// The automatic camera for positions X/Y. Default: where a tween in flight takes the nodes (the engine moves them only by
// tweenTo, and a tween's frames can lag far behind the clock while the page is busy), else where they are.
function autoCamera(X, Y, everyone){
 if (!X){ const t = view.tween; [X, Y] = t ? [t.X, t.Y] : [LX, LY]; }
 const all = [...Array(N).keys()], whole = boxOf(all, X, Y, 0, 1);
 if (!whole) return null;
 const fitAll = Object.assign(camFor(whole), {kind: everyone ? 'all' : 'fit'});
 if (everyone || safe(() => layout, 'cosp') !== 'cosp') return fitAll;
 const core = safe(() => { const ids = [...linkedMembers()], b = ids.length > 1 ? boxOf(ids, X, Y, CORE_PCT[0], CORE_PCT[1]) : null; return b ? camFor(b, legendInset()) : null; }, null);
 return core && core.s >= MIN_GAIN * fitAll.s ? Object.assign(core, {kind: 'core'}) : fitAll;
}
function putCamera(c){ cam.s = c.s; cam.tx = c.tx; cam.ty = c.ty; view.last = {kind: c.kind, s: c.s, tx: c.tx, ty: c.ty}; }
// True once anything but this code moved the camera since the last automatic framing (or the glide in progress).
function readerMoved(){ const w = view.anim ? view.anim.wrote : view.last; return !!w && (cam.s !== w.s || cam.tx !== w.tx || cam.ty !== w.ty); }
const autoHeld = () => !readerMoved() && safe(() => sel, -1) < 0 && !safe(() => route, null);
function stopGlide(){ if (view.anim){ cancelAnimationFrame(view.anim.raf); view.anim = null; } }
// Ease the camera to c on the engine's tween curve; any other camera write (the reader) ends the glide where it is.
function glide(c, ms){
 stopGlide();
 if (safe(() => RMQ, false) || !(ms > 16)){ putCamera(c); draw(); return; }
 const from = {s: cam.s, tx: cam.tx, ty: cam.ty}, t0 = performance.now(), a = {wrote: from, raf: 0};
 view.anim = a;
 const step = now => {
  if (view.anim !== a) return;
  if (cam.s !== a.wrote.s || cam.tx !== a.wrote.tx || cam.ty !== a.wrote.ty){ view.anim = null; return; }
  const k = Math.min(1, Math.max(0, (now - t0) / ms)), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
  if (k >= 1){ view.anim = null; putCamera(c); }
  else { cam.s = from.s + (c.s - from.s) * e; cam.tx = from.tx + (c.tx - from.tx) * e; cam.ty = from.ty + (c.ty - from.ty) * e; a.wrote = {s: cam.s, tx: cam.tx, ty: cam.ty}; a.raf = requestAnimationFrame(step); }
  draw();
 };
 a.raf = requestAnimationFrame(step);
}
// The Fit button: every member, by the engine's own fit(). A later canvas size change keeps everyone framed.
function fitEveryone(){ if (!Number.isFinite(W) || !Number.isFinite(H) || W <= 48 || H <= 72) return; stopGlide(); (view.origFit || fit)(); view.last = {kind: 'all', s: cam.s, tx: cam.tx, ty: cam.ty}; }   // fit()'s full guard: the served engine fit() checks only W <= 48 || H <= 72 (2026-09-25)
// A canvas size change the overlay caused (rail, member panel, a frame shown again) runs the engine's resize() with this
// flag, so the fit() inside it keeps a moved camera (same scale, same view centre) instead of framing again.
function layoutResize(){ view.overlayResize = true; try { resize(); } finally { view.overlayResize = false; } }
// A Links change or a legend fold changes the Standard layout's automatic frame; the camera glides only if still automatic.
function autoReframe(){ if (view.origFit && autoHeld() && lastKind() !== 'all' && safe(() => layout, '') === 'cosp'){ const c = autoCamera(); if (c) glide(c, 450); } }

function installFraming(){
 const engineFit = typeof fit === 'function' ? fit : null, engineTween = typeof tweenTo === 'function' ? tweenTo : null,
  engineNet = typeof setNetConfig === 'function' ? setNetConfig : null;          // typeof: an engine without one never throws here
 if (!engineFit) throw new Error('engine fit() not found');
 view.origFit = engineFit;
 fit = function(){
  if (!Number.isFinite(W) || !Number.isFinite(H) || W <= 48 || H <= 72) return;   // the engine's own guard, exactly: a hidden, collapsed or unmeasured canvas (2026-09-25)
  const pw = view.seenW, ph = view.seenH; view.seenW = W; view.seenH = H;
  if (view.overlayResize && view.last){
   if (readerMoved()){ stopGlide(); if (pw && ph){ cam.tx += (W - pw) / 2; cam.ty += (H - ph) / 2; } return; }
   if (lastKind() === 'all'){ stopGlide(); const e = autoCamera(null, null, true); if (e) putCamera(e); return; }
  }
  stopGlide();
  const c = autoCamera(); if (c) putCamera(c);
 };
 // Layout and network switches tween the nodes; a camera that is still automatic glides along to the new frame.
 if (engineTween) tweenTo = function(tx, ty, dur){
  const held = autoHeld(), r = engineTween.apply(this, arguments), ms = safe(() => RMQ, false) ? 1 : (dur || 850);
  const t = view.tween = {X: tx, Y: ty}; view.tweens++;
  // The engine keeps no in-flight flag: view.tween goes on the frame LX/LY reach every finite target, to rounding, never by the clock (2026-09-25).
  const off = (a, b) => Number.isFinite(b) && Math.abs(a - b) > 1e-9 * (1 + Math.abs(b)), land = () => { if (view.tween !== t) return; for (let i = 0; i < N; i++) if (off(LX[i], tx[i]) || off(LY[i], ty[i])) return void requestAnimationFrame(land); view.tween = null; }; requestAnimationFrame(land);
  if (held){ const c = autoCamera(tx, ty, lastKind() === 'all'); if (c) glide(c, ms); }
  return r;
 };
 // A network switch that keeps the positions (no tween) still changes the drawn links: the core is framed again.
 if (engineNet) setNetConfig = function(){
  const held = autoHeld(), n = view.tweens, r = engineNet.apply(this, arguments);
  if (held && n === view.tweens && lastKind() !== 'all' && safe(() => layout, '') === 'cosp'){ const c = autoCamera(); if (c) glide(c, 600); }
  return r;
 };
}

// ---- URL parameters from the shell (01): issue, net, color, links, layout, member ----
function issueIndex(v){
 const s = String(v || '').trim().toLowerCase();
 if (!s || ['overall', 'overall cu record', 'overall cu stance'].includes(s)) return LAST;
 const i = DATA.issues.findIndex(n => String(n).trim().toLowerCase() === s);
 return i >= 0 ? i : LAST;
}
// Color default (boards #24 and #26): "Overall standing" at Overall CU record, "Field record" on a single field. It
// follows the issue until a colour is chosen, by ?color= (a real engine mode) or the Color control. At Overall CU
// record both modes paint the same stance fills; the engine only fades no-recorded-action nodes under Field record.
let colorAuto = true;
function autoColor(target){
 if (!colorAuto) return;
 const want = target === LAST ? 'gen' : 'issue';
 if (safe(() => colorBy, want) !== want) click(`#color button[data-c="${want}"]`);
}
function applyParams(){
 const net = query.get('net'); if (net) click(`#netseg button[data-net="${CSS.escape(net)}"]`);
 const col = query.get('color'), colBtn = col ? document.querySelector(`#color button[data-c="${CSS.escape(col)}"]`) : null;
 if (colBtn) colorAuto = false;
 const want = issueIndex(query.get('issue'));
 if (want !== issue){ isel.value = String(want); isel.onchange(); }
 const lay = ({standard: 'cosp', cosp: 'cosp', area: 'area', 'policy-area': 'area', committee: 'committee', committees: 'committee'})[String(query.get('layout') || '').toLowerCase()];
 if (lay && lay !== 'cosp') click(`#ltog button[data-l="${lay}"]`);
 if (colBtn) colBtn.click(); else autoColor(issue);
 const lk = query.get('links');
 if (lk != null && lk !== ''){
  const on = lk === 'all' ? null : new Set(lk === 'none' ? [] : lk.split(',').map(s => s.trim().toLowerCase()));
  ['c', 's', 'h', 'e', 'r', 'a'].forEach(k => { const b = $('ly_' + k); if (b) b.checked = on ? on.has(k) : true; });
  draw();
 }
 const m = query.get('member'); if (m){ const i = memberIndex(m); if (i >= 0) selectMember(i); }
}
// ?member= : the exact engine name first, then the same name ignoring case and surrounding spaces.
function memberIndex(name){
 const s = String(name || '').trim(); if (!s) return -1;
 const i = DATA.name.indexOf(s); if (i >= 0) return i;
 const l = s.toLowerCase(); return DATA.name.findIndex(n => String(n).trim().toLowerCase() === l);
}

// ---------------------------------------------------------------------------------------------------------------
// Build 09: the member panel (board #26)
// ---------------------------------------------------------------------------------------------------------------
const PANEL_TABS = [['overview', 'Overview'], ['reach', 'Reach'], ['scenario', 'Scenario'], ['routes', 'Routes']];
const PARTY_NAME = {D: 'Democrat', R: 'Republican', I: 'Independent'};
const mp = {built: false, tab: 'overview', cat: 'idle', byName: null, pending: 0, rows: null, reachRows: null, who: null, speech: null};
const capFirst = s => { s = String(s == null ? '' : s); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; };
// The engine's text as one line: each <br> becomes a space (textContent alone would join "to go.<br>Cheapest").
function flatText(node){
 if (!node) return '';
 const c = node.cloneNode(true);
 c.querySelectorAll('br').forEach(b => b.replaceWith(' '));
 return (c.textContent || '').replace(/\s+/g, ' ').trim();
}

// The member catalog (shell's ML.catalog through the kit) gives chamber, state and the bioguide id the Compare
// store keys on. The engine's DATA has neither.
function loadCatalog(){
 if (mp.cat !== 'idle') return;                 // one load per frame; an error stays shown (no retry loop)
 if (!window.MLK || !MLK.catalog){ mp.cat = 'error'; return; }
 mp.cat = 'loading';
 Promise.resolve().then(() => MLK.catalog()).then(c => {
  const map = new Map(); (c && c.members || []).forEach(r => { if (r && r.name) map.set(String(r.name), r); });
  mp.byName = map; mp.cat = 'ready';
 }).catch(() => { mp.cat = 'error'; }).then(() => { if (sel >= 0) paintPanel(); });
}
const catalogRow = v => (mp.byName && v >= 0) ? (mp.byName.get(String(DATA.name[v])) || null) : null;
const bioguideOf = v => { const r = catalogRow(v); return r && r.bio && r.bio.bioguide ? String(r.bio.bioguide) : ''; };

function buildPanel(){
 const panel = $('mpanel'), mname = $('mname'), mtier = $('mtier'), mstance = $('mstance'), mprime = $('mprime');
 if (!panel || !mname || !mprime) throw new Error('engine member panel not found');
 panel.classList.add('mlk-v9-panel');
 panel.setAttribute('role', 'region'); panel.setAttribute('aria-labelledby', 'mname');
 const h3 = re => [...panel.querySelectorAll(':scope > h3')].find(h => re.test(h.textContent));
 const [scnBlock, rteBlock] = panel.querySelectorAll(':scope > .scc');
 const whoH = h3(/^Who can reach/), scnH = h3(/^Scenario/), rteH = h3(/^Route to/);

 // head: ×, name, party · chamber · state, tier badge + stance pill, Open profile + Pin to compare
 const head = el('div', 'mlk-v9-head');
 const close = el('button', 'mlk-close mlk-v9-close', (window.MLK && MLK.icon) ? MLK.icon('close') : '×');
 close.type = 'button'; close.setAttribute('aria-label', 'Close member panel'); close.title = 'Close (Esc)';
 close.addEventListener('click', closePanel);
 mname.classList.add('mlk-v9-name');
 const meta = el('p', 'mlk-v9-meta'); meta.id = 'mlk-v9-meta';
 const badges = el('div', 'mlk-v9-badges'); badges.id = 'mlk-v9-badges';
 const actions = el('div', 'mlk-v9-actions',
  '<a class="mlk-btn mlk-v9-open" id="mlk-v9-open" data-mlk-nav href="/?view=profile">Open profile</a>'
  + '<button type="button" class="mlk-btn mlk-v9-pin" id="mlk-v9-pin" aria-pressed="false">Pin to compare</button>');
 head.append(close, mname, meta, badges, actions);
 actions.querySelector('#mlk-v9-pin').addEventListener('click', togglePin);

 // tabs
 const tabs = el('div', 'mlk-tabs mlk-tabs--quiet mlk-v9-tabs'); tabs.id = 'mlk-v9-tabs';
 tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Member details');
 tabs.innerHTML = PANEL_TABS.map(([k, label], n) => `<button type="button" role="tab" id="mlk-v9-tab-${k}" data-tab="${k}" aria-controls="mlk-v9-pane-${k}" aria-selected="${n ? 'false' : 'true'}" tabindex="${n ? -1 : 0}">${label}</button>`).join('');
 tabs.addEventListener('click', e => { const b = e.target.closest('[role=tab]'); if (b) selectPanelTab(b.dataset.tab); });
 tabs.addEventListener('keydown', e => {
  const list = [...tabs.querySelectorAll('[role=tab]')], i = list.indexOf(document.activeElement); if (i < 0) return;
  const k = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: list.length - 1}[e.key]; if (k == null) return;
  e.preventDefault(); const t = list[(k + list.length) % list.length]; selectPanelTab(t.dataset.tab); t.focus();
 });
 const pane = k => { const p = el('div', 'mlk-v9-pane'); p.id = 'mlk-v9-pane-' + k; p.setAttribute('role', 'tabpanel'); p.setAttribute('aria-labelledby', 'mlk-v9-tab-' + k); p.tabIndex = 0; p.hidden = k !== 'overview'; return p; };
 const pO = pane('overview'), pR = pane('reach'), pS = pane('scenario'), pT = pane('routes');

 // Overview: label/value rows + who can reach them + observed speech; the engine's own summary (with its primacy
 // evidence and method text) stays whole under "Sources & method".
 pO.innerHTML = '<dl class="mlk-v9-rows" id="mlk-v9-rows"></dl>'
  + '<section class="mlk-v9-sec"><h3 class="mlk-v9-label">Who can reach them</h3><p class="mlk-v9-text" id="mlk-v9-who"></p></section>'
  + '<section class="mlk-v9-sec"><h3 class="mlk-v9-label">Observed speech</h3><p class="mlk-v9-text" id="mlk-v9-speech"></p></section>';
 const method = el('details', 'mlk-v9-method', '<summary>Sources &amp; method</summary>'); method.id = 'mlk-v9-method';
 if (mtier) method.append(mtier); if (mstance) method.append(mstance);
 pO.append(method);

 // Reach: the engine's colleague-influence block (moved), then conf and reporting rank as their own rows.
 if (whoH) pR.append(whoH);
 pR.append(mprime);
 pR.insertAdjacentHTML('beforeend', '<dl class="mlk-v9-rows mlk-v9-rows--reach" id="mlk-v9-reachrows"></dl>'
  + '<details class="mlk-v9-method"><summary>How to read this</summary><p><b>Data coverage (conf).</b> Per-member data coverage, 0–100, read from data/members/confidence.json: legislation breadth (up to 50) + press presence (up to 30) + vote presence (up to 20); 50 when a member has no entry.</p><p><b>Reporting rank.</b> The member’s rank in the reporting order (DATA.rank).</p></details>');

 // Scenario: the live what-if block, then "What if…" (drop-outs, executive branch) moved out of the route block.
 if (scnH) pS.append(scnH);
 if (scnBlock) pS.append(scnBlock);
 const whatIf = el('div', 'scc mlk-v9-whatif');
 const wh = rteBlock ? [...rteBlock.querySelectorAll(':scope > h3')].find(h => /^What if/.test(h.textContent)) : null;
 if (wh){ const stop = $('shockout'), nodes = []; for (let n = wh; n; n = n.nextElementSibling){ nodes.push(n); if (n === stop) break; } whatIf.append(...nodes); pS.append(whatIf); }

 // Routes: Path / Best / Multi / Budget / Cascade, the cascade sub-modes, target search and Build.
 if (rteH) pT.append(rteH);
 if (rteBlock) pT.append(rteBlock);
 panel.append(head, tabs, pO, pR, pS, pT);                                   // re-attached: getElementById sees the moved nodes again
 ['pmode', 'cascsub', 'execseg'].forEach(id => { const s = $(id); if (s){ s.classList.add('mlk-seg'); s.setAttribute('role', 'group'); } });
 const pb = $('pathbtn'); if (pb) pb.classList.add('mlk-btn', 'mlk-btn--sm');
 const bb = $('backbtn'); if (bb) bb.classList.add('mlk-v9-hidden');          // "← Targets": the × replaces it (kept in the DOM)
 mp.rows = $('mlk-v9-rows'); mp.reachRows = $('mlk-v9-reachrows'); mp.who = $('mlk-v9-who'); mp.speech = $('mlk-v9-speech');

 // repaint whenever the engine rewrites its member blocks (selection, issue, mode, budget, route)
 const mo = new MutationObserver(schedulePaint);
 [mname, mtier, mstance, mprime].forEach(n => n && mo.observe(n, {childList: true, subtree: true, characterData: true}));
 if (window.MLK && MLK.compare && MLK.compare.subscribe) MLK.compare.subscribe(() => paintPin());

 // canvas: the issue summary doubles as a legend strip along the bottom while a member is selected (network.css;
 // renderCard adds the strip's tail, placeStrip places the strip)

 // hover tooltip: the name only (plus the one live percentage while a cascade or route colours the map)
 cv.addEventListener('mousemove', shrinkTip);

 // Escape closes the panel and returns to Targets, unless a popover, menu or the Filters panel is open
 window.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || sel < 0 || !$('right').classList.contains('is-member')) return;
  if (document.querySelector('.mlk-popover:not([hidden]), .mlk-menu:not([hidden]), #viewpanel.open')) return;
  closePanel();
 }, true);
 mp.built = true;
}

function selectPanelTab(name, focus){
 mp.tab = name;
 document.querySelectorAll('#mlk-v9-tabs [role=tab]').forEach(t => { const on = t.dataset.tab === name; t.setAttribute('aria-selected', on ? 'true' : 'false'); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); });
 PANEL_TABS.forEach(([k]) => { const p = $('mlk-v9-pane-' + k); if (p) p.hidden = k !== name; });
}

function closePanel(){
 backToTargets();
 sync();
 const t = railOpen ? $('mlk-v8-tab-targets') : ui.railShow; if (t) setTimeout(() => t.focus(), 0);   // 10: a collapsed rail folds away again
}

function shrinkTip(){
 const tip = $('tip'); if (!tip || hi < 0 || tip.style.display === 'none') return;
 const keep = tip.innerHTML.split('<br>').filter(s => /^(Joins cascade|Leverage to flip|Activates):/.test(s));
 const html = `<b>${esc(DATA.name[hi])}</b>` + keep.map(s => '<br>' + s).join('');
 if (tip.innerHTML !== html) tip.innerHTML = html;
 tip.classList.add('mlk-v9-tip');
}

function schedulePaint(){
 if (mp.pending) return;
 mp.pending = requestAnimationFrame(() => { mp.pending = 0; paintPanel(); });
}

const row = (label, value, opts = {}) => `<div class="mlk-v9-row"${opts.title ? ` title="${esc(opts.title)}"` : ''}><dt>${esc(label)}</dt><dd>${opts.dot ? `<span class="mlk-dot" style="--mlk-dot:${esc(opts.dot)}"></span>` : ''}${esc(value)}</dd></div>`;

function paintPanel(){
 if (!mp.built || sel < 0 || !mp.rows) return;
 const v = sel;
 loadCatalog();
 try {
  const honors = safe(() => HONORS, false), ov = honors ? DATA.overall_honors[v] : null;
  // head
  const party = DATA.party[v], ptc = safe(() => PTCOL[party], '') || '', crow = catalogRow(v);
  const meta = $('mlk-v9-meta');
  meta.removeAttribute('aria-busy');
  if (crow) meta.innerHTML = `<span class="mlk-dot" style="--mlk-dot:${esc(ptc)}"></span><span>${[crow.chamber, crow.party, crow.state].filter(Boolean).map(esc).join('<span class="mlk-v9-sep">·</span>')}</span>`;
  else if (mp.cat === 'loading' || mp.cat === 'idle'){ meta.setAttribute('aria-busy', 'true'); meta.innerHTML = `<span class="mlk-dot" style="--mlk-dot:${esc(ptc)}"></span><span class="mlk-skel mlk-v9-skel" aria-label="Loading chamber and state"></span>`; }
  else meta.innerHTML = `<span class="mlk-dot" style="--mlk-dot:${esc(ptc)}"></span><span>${esc(PARTY_NAME[party] || party || '')}</span><span class="mlk-v9-sep">·</span><span class="mlk-v9-muted">Chamber and state unavailable</span>`;

  const stanceLabel = publicLabel(v, LAST), stanceColour = publicColor(v, LAST);
  const tier = ov ? ov.priority_tier : 0;
  const tb = document.querySelector('#mtier .mrow b'), tierColour = tb ? tb.style.color : '';   // the engine's own tier colour
  const tierBadge = ov ? (tier ? `<span class="mlk-tier mlk-v9-tier" title="${esc(priorityLabel(v))}"${tierColour ? ` style="--mlk-v9-tier:${esc(tierColour)}"` : ''}>T${esc(tier)}</span>` : `<span class="mlk-tier mlk-tier--none mlk-v9-tier">No tier</span>`) : '';
  $('mlk-v9-badges').innerHTML = tierBadge + `<span class="mlk-pill mlk-v9-pill" style="--mlk-dot:${esc(stanceColour)}">${esc(stanceLabel)}</span>`;
  const open = $('mlk-v9-open'), name = DATA.name[v];
  open.href = (window.MLK && MLK.url) ? MLK.url('profile', {m: name, tab: 'overview'}) : '/?view=profile&m=' + encodeURIComponent(name) + '&tab=overview';
  open.setAttribute('aria-label', 'Open profile: ' + name);
  paintPin();

  // Overview rows
  const rows = [];
  if (ov){
   rows.push(row('Priority rank', ov.rank == null ? 'No priority rank' : `#${ov.rank} of ${ov.stance_cohort_size || N}`));
   rows.push(row('Priority tier', priorityLabel(v)));
   rows.push(row('Stance, overall', stanceLabel + mixedRecordText(v)));
  } else rows.push(row('Stance, overall', stanceLabel));
  if (issue !== LAST) rows.push(row('On ' + DATA.issues[issue], publicLabel(v)));
  rows.push(row('Reach on this issue', `Top ${Math.max(1, 100 - Math.round(CENTPCT[v] * 100))}% of Congress`, {title: 'recomputed from the issue-adjusted social-influence kernel'}));
  if (ov) rows.push(row('Action record', ov.alignment));
  rows.push(row('Bipartisan share', `${DATA.bp[v]}%`));
  const play = DATA.play[v];
  rows.push(row('Electoral play', capFirst(PLAYLBL[play] || play || '—'), {dot: play && play !== 'n' ? safe(() => ELCOL[play], '') : ''}));
  const a = allocOf(v), coal = mode === 'coal';
  rows.push(row(coal ? 'Coalition entry' : 'Persuasion funding', a > 0 ? `$${a}k` : 'Not funded at this budget'));
  mp.rows.innerHTML = rows.join('');

  const mprime = $('mprime'), note = mprime.querySelector('.pgnote');
  mp.who.textContent = flatText(note || mprime) || 'No modeled route on this issue';
  const sp = DATA.speech && DATA.speech[v];
  if (sp && (sp[7] != null || sp[8] != null)){
   const parts = [];
   if (sp[7] != null) parts.push(`<b>${esc(Number(sp[7]).toLocaleString('en-US'))}</b> attributed public words`);
   if (sp[8] != null) parts.push(`<b>${esc(Number(sp[8]).toLocaleString('en-US'))}</b> min verified voice`);
   mp.speech.innerHTML = parts.join('<span class="mlk-v9-sep">·</span>');
   mp.speech.title = (sp[5] ? sp[5] + ' · ' : '') + 'descriptive only';
  } else { mp.speech.textContent = 'No observed speech on record'; mp.speech.removeAttribute('title'); }

  // Reach rows: the other two parts of "Bipartisan · conf · reporting rank"
  const cf = DATA.cf ? DATA.cf[v] : null;
  mp.reachRows.innerHTML = row('Data coverage (conf)', cf == null ? '—' : `${cf} of 100`)
   + row(honors ? 'Reporting rank' : 'Rank', DATA.rank && DATA.rank[v] != null ? `#${DATA.rank[v]}` : '—');
 } catch (err) {
  console.warn('Influence member panel:', err);
  mp.rows.innerHTML = '<div class="mlk-v9-state mlk-v9-state--error" role="alert">Member details could not be shown</div><button type="button" class="mlk-btn mlk-btn--sm" data-mlk-v9-retry>Retry</button>';
  const b = mp.rows.querySelector('[data-mlk-v9-retry]'); if (b) b.onclick = () => paintPanel();
 }
}

function paintPin(){
 const b = $('mlk-v9-pin'); if (!b || sel < 0) return;
 const id = bioguideOf(sel), store = window.MLK && MLK.compare;
 if (!id || !store){
  const busy = mp.cat === 'loading' || mp.cat === 'idle';
  b.disabled = true; b.setAttribute('aria-pressed', 'false'); b.textContent = 'Pin to compare';
  if (busy) b.setAttribute('aria-busy', 'true'); else b.removeAttribute('aria-busy');
  b.title = busy ? 'Loading the member catalog' : 'Not in the member catalog';
  return;
 }
 const on = !!store.has(id);
 b.disabled = false; b.removeAttribute('aria-busy');
 b.setAttribute('aria-pressed', on ? 'true' : 'false');
 b.textContent = on ? 'Pinned' : 'Pin to compare';
 b.title = on ? 'Remove from Compare' : 'Add to Compare';
}

function togglePin(){
 const id = bioguideOf(sel); if (!id || !window.MLK || !MLK.compare) return;
 MLK.compare.toggle(id);
 paintPin();
}

try {
 build();
 try { installFraming(); } catch (err) { console.warn('Influence framing (10) not applied:', err); }
 try { watchCheck(); } catch (err) { console.warn('Influence self-check badge (10) not applied:', err); }
 paintRail();
 const oldRefresh = refresh;
 refresh = function(){ const r = oldRefresh.apply(this, arguments); afterRefresh(); return r; };
 const oldLegend = legend;
 legend = function(){ const r = oldLegend.apply(this, arguments); syncLegend(); return r; };
 renderFoot();
 try { buildPanel(); } catch (err) { console.warn('Influence member panel (09) not applied:', err); }
 new MutationObserver(sync).observe($('app'), {subtree: true, attributes: true, attributeFilter: ['class', 'style']});
 applyParams();
 afterRefresh();
 let lastBox = '';
 new ResizeObserver(() => { const r = $('netwrap').getBoundingClientRect(), k = Math.round(r.width) + 'x' + Math.round(r.height); if (k !== lastBox){ lastBox = k; fitBar(); layoutResize(); placeStrip(); } }).observe($('netwrap'));
 window.MelangeInfluence = {version: '10', sync, selectTab, renderCard, refreshInfo: () => { lastInfo = ''; updateInfo(); }, issueIndex,
  camera: () => ({kind: lastKind(), moved: readerMoved(), gliding: !!view.anim, s: cam.s}), fitEveryone,   // 10: proof hooks
  rail: {open: () => railOpen, set: open => setRail(open)}, legend: {open: () => legendOpen, toggle: toggleLegend},
  memberIndex, memberPanel: {paint: paintPanel, selectTab: selectPanelTab, close: closePanel, tab: () => mp.tab, catalogState: () => mp.cat,
  _forceCatalog: s => { mp.cat = s; if (s !== 'ready') mp.byName = null; paintPanel(); }}};   // proof hook: loading / error states
} catch (err) {
 // Fallback: the unrestyled engine layout (all CSS in network.css is scoped to html.mlk-v8).
 document.documentElement.classList.remove('mlk-v8');
 console.warn('Influence overlay 08 not applied:', err);
 const requested = DATA.issues.indexOf(query.get('issue')); if (requested >= 0){ isel.value = String(requested); isel.onchange(); }
}
resize(); draw();
})();
