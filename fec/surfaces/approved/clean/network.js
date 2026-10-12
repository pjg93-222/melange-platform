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
document.addEventListener('visibilitychange', () => { if (!document.hidden){ resize(); draw(); } });

const ICON_FILTER = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12l-4.6 5.4V13L6.6 14V8.4z"/></svg>';
const ICON_FIT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/><rect x="6" y="6" width="4" height="4"/></svg>';
const SEARCH_ICON = (window.MLK && MLK.icon) ? MLK.icon('search').replace('<svg', '<svg class="mlk-search__icon"') : '';
function el(tag, cls, html){ const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
function click(sel){ const b = document.querySelector(sel); if (b) b.click(); return !!b; }
function onBtn(sel){ const b = document.querySelector(sel + ' button.on'); return b ? b : null; }

let built = false, card, foot, tabsEl, lastInfo = '', cuFrameText = '';
const ui = {};

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
 right.prepend(tabsEl, paneT, paneB); right.append(foot);
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
 tl.append(card, $('mdlink'));
 renderCard({loading: true});
 const tr = el('div', 'mlk-v8-tr'); tr.append($('engchk'), $('layoutnote'), $('netnote'));
 const cap = el('p', 'mlk-v8-caption', 'white ring = funded · dot size = $ · scroll to zoom'); cap.id = 'mlk-v8-caption';
 const z = el('div', 'mlk-v8-zoom'); z.id = 'network-zoom'; z.setAttribute('role', 'group'); z.setAttribute('aria-label', 'Zoom');
 z.innerHTML = '<button type="button" class="mlk-btn mlk-btn--icon" aria-label="Zoom in">+</button><button type="button" class="mlk-btn mlk-btn--icon" aria-label="Zoom out">−</button><button type="button" class="mlk-btn mlk-btn--icon" aria-label="Fit">' + ICON_FIT + '</button>';
 z.children[0].onclick = () => zoomBy(1.2); z.children[1].onclick = () => zoomBy(1 / 1.2); z.children[2].onclick = () => { fit(); draw(); };
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
function renderCard(opts){
 if (!card) return;
 if (opts && opts.loading){ card.setAttribute('aria-busy', 'true'); card.innerHTML = '<h2 class="mlk-v8-card__title">Issue summary</h2>' + SKELETON; return; }
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
  card.innerHTML = `<h2 class="mlk-v8-card__title">${esc(title)}</h2>${body}<p class="mlk-v8-card__foot">${esc(N)} members${asOf ? ' · as of ' + esc(asOf) : ''}</p>`;
 } catch (err) {
  card.innerHTML = '<h2 class="mlk-v8-card__title">Issue summary</h2><p class="mlk-v8-card__state mlk-v8-card__state--error" role="alert">Summary could not be computed</p><button type="button" class="mlk-btn mlk-btn--sm" data-mlk-v8-retry>Retry</button>';
  const b = card.querySelector('[data-mlk-v8-retry]'); if (b) b.onclick = () => { refresh(); };
 }
 placeStrip();
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
   placeStrip();
   if (member) schedulePaint();
  }
 } finally { syncing = false; }
}

function afterRefresh(){ paintTargets(); renderCard(); updateInfo(); syncLegend(); sync(); }

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
 const t = $('mlk-v8-tab-targets'); if (t) setTimeout(() => t.focus(), 0);
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
 new ResizeObserver(() => { const r = $('netwrap').getBoundingClientRect(), k = Math.round(r.width) + 'x' + Math.round(r.height); if (k !== lastBox){ lastBox = k; fitBar(); resize(); placeStrip(); } }).observe($('netwrap'));
 window.MelangeInfluence = {version: '09', sync, selectTab, renderCard, refreshInfo: () => { lastInfo = ''; updateInfo(); }, issueIndex,
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
