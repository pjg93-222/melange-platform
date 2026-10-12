/* desk-compare.js: overlay for original-desk.html (Compare, board #31; register with MLK.registerDeskView('compare', {render(section, ctx){}})); owner: prompt 03. Runs before restored-desk.js. */
/* Build 03 (board #31). Registers the `compare` deskview. restored-desk.js creates <section id="view-compare"> and calls
   render(section, ctx). Up to four members from the shared Compare store (MLK.compare; ids are bioguide ids =
   m.honors.member_id, the same ids desk-members.js stores) are laid side by side:
   - one row per profile figure tile (openMember's .figs: rank, tier, stance, score, district opportunity, CU PAC, bank &
     financial-service PACs, return per $10,000, electoral exposure), formatted with the desk's own helpers
     (money, f2, ordinal, honorAt, honorLabel, honorRank, honorFacts);
   - one row per legislative area in D.cats order, each a recognition pill (honorLabel wording; colour by reference
     from HONOR_COLORS / STANDING_COLORS, never a typed hex).
   Differences only hides rows whose values are equal for every member shown and lists them in the footer caption.
   Baseline adds a Chamber median or Party median column computed here from every member in M (no producer, no fetch).
   Test/proof hooks: window.MelangeCompareView = {state(), repaint(), showLoading(), failNext()}. */
(() => {
'use strict';
const K = window.MLK, D0 = document;
if (!K || typeof K.registerDeskView !== 'function' || typeof M === 'undefined' || typeof CATS === 'undefined') return;

const esc = K.esc;
const H = typeof HONORS !== 'undefined' ? HONORS : null;
const DASH = '—';

/* ---------------- member facts (legacy desk helpers, by reference) ---------------- */
const idOf = m => String((m && m.honors && m.honors.member_id) || (m && m.bio && m.bio.bioguide) || '');
let INDEX = null;
function index(){
 if (!INDEX){ INDEX = new Map(); M.forEach((m, i) => { const id = idOf(m); if (id && !INDEX.has(id)) INDEX.set(id, i); }); }
 return INDEX;
}
function place(m){
 const d = String(m.district || '');
 let x = d.match(/^Congressional District (\d+) \(\d+\w* Congress\), (.+)$/);
 if (x) return `${x[2]} ${ordinal(x[1])}`;
 x = d.match(/District \(at Large\) \(\d+\w* Congress\), (.+)$/);
 if (x) return `${x[1]} at large`;
 return d || m.state || DASH;
}
const subline = m => [m.chamber || DASH, place(m)].join(' · ');
const num = v => { if (v == null || v === '') return null; const x = Number(v); return Number.isFinite(x) ? x : null; };
const overall = m => (H ? honorAt(m, '') : null);
const unresolved = m => String(m.money_source_status || '').startsWith('UNRESOLVED');
const dollars = v => '$' + Math.round(v).toLocaleString();
function pill(label, colour, title){
 const t = title ? ` title="${esc(title)}"` : '';
 return colour ? `<span class="mlk-pill" style="--mlk-dot:${esc(colour)}"${t}>${esc(label)}</span>`
  : `<span class="mlk-pill mlk-pill--none"${t}>${esc(label)}</span>`;
}
function ordinalHTML(v){
 const s = ordinal(v), x = String(s).match(/^(-?\d+)(st|nd|rd|th)$/);
 return x ? `${esc(x[1])}<sup>${x[2]}</sup>` : esc(s);
}

/* ---------------- rows: [label, how to read one member, best, median format] ---------------- */
// cell(m) -> {text (display + caption), key (equality; defaults to text), html (optional), title (optional)}
const FIGS = [
 {key: 'rank', label: 'Overall priority rank', best: 'min',
  num: m => num(H ? overall(m).rank : m.rank),
  cell: m => { const v = num(H ? overall(m).rank : m.rank); return {text: v == null ? DASH : '#' + v}; },
  base: v => '#' + (Number.isInteger(v) ? v : v.toFixed(1))},
 {key: 'tier', label: 'Primacy tier',
  cell: m => ({text: m.tier ? 'Tier ' + m.tier : 'Not tiered', title: m.tier && m.tierrank ? 'Primacy tier · #' + m.tierrank : ''})},
 {key: 'stance', label: 'Stance, overall', cap: 'Stance overall', stance: true,
  cell: m => {
   if (!H){ const t = stanceAt(m, ''); const l = stanceName(t); return {text: l, html: pill(l, STCOL[t] || '', '')}; }
   const row = overall(m);
   return {text: row.stance, html: pill(row.stance, STANDING_COLORS[row.stance] || '', honorFacts(m, ''))};
  }},
 {key: 'score', label: 'Model score', best: 'max', num: m => num(m.score),
  cell: m => {
   const w = Math.max(0, Math.min(100, (Number(m.score) || 0) * 100)).toFixed(1);
   // .sbar keeps today's score-bar fill (the desk stylesheet's own gradient), as desk-members.js does
   return {text: f2(m.score), html: `<span class="dc-score"><span class="dc-score__v">${esc(f2(m.score))}</span><span class="sbar dc-bar" aria-hidden="true"><i style="width:${w}%"></i></span></span>`};
  },
  base: v => f2(v)},
 {key: 'potential', label: 'District opportunity', best: 'max',
  num: m => num((m.demographic_potential || {}).score),
  cell: m => {
   const p = m.demographic_potential || {};
   if (p.score == null) return {text: DASH};
   const s = Number(p.score).toFixed(1);
   return {text: `${s} · ${ordinal(p.rank)} of ${p.market_count}`,
    html: `${esc(s)}<span class="dc-sep"> · </span>${ordinalHTML(p.rank)} of ${esc(p.market_count)}`};
  },
  base: v => v.toFixed(1)},
 {key: 'invested', label: 'Credit-union PAC contributions', best: 'max',
  num: m => (unresolved(m) ? null : (num(m.invested) || 0)),
  cell: m => ({text: unresolved(m) ? 'Unresolved' : money(m.invested)}),
  base: dollars},
 {key: 'indmoney', label: 'Bank & financial-service PACs',
  num: m => (unresolved(m) ? null : (num(m.ind_money) || 0)),
  cell: m => ({text: unresolved(m) ? 'Unresolved' : money(m.ind_money)}),
  base: dollars},
 {key: 'roi', label: 'Return per $10,000', best: 'max', num: m => num(m.roi) || 0,
  cell: m => ({text: (m.roi || 0).toFixed(3)}),
  base: v => v.toFixed(3)},
 {key: 'electoral', label: 'Electoral exposure',
  cell: m => {
   const play = m.ev_play || DASH, v = num(m.ev_value);
   return {text: play, key: play + '|' + (v || 0),
    html: esc(play) + (v > 0 ? `<span class="dc-sep"> · </span>${esc(money(v))}` : '')};
  }}
];
function areaCell(m, cat){
 if (!H){ const t = stanceAt(m, cat), l = stanceName(t); return {text: l, html: pill(l, STCOL[t] || '', '')}; }
 const row = honorAt(m, cat), label = honorLabel(m, cat);
 const colour = (row && row.recognition && HONOR_COLORS[row.recognition]) || '';
 // the same detail line publicBadge prints under the legacy badge ("#x of y · n actions"), here as the pill's title
 const detail = row ? `${honorRank(row)}${row.evidence_status === 'observed' ? ' of ' + row.cohort_size : ''} · ${row.action_count} actions` : '';
 return {text: label, html: pill(label, colour, detail)};
}

/* ---------------- baselines: medians over every member in M ---------------- */
const MED = new Map();
function median(vals){
 const a = vals.filter(v => v != null && Number.isFinite(v)).sort((x, y) => x - y);
 if (!a.length) return null;
 const h = a.length >> 1;
 return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2;
}
function groupMedian(spec, field, value){
 const k = spec.key + '|' + field + '|' + value;
 if (!MED.has(k)){
  const vals = [];
  for (const m of M){ if ((m[field] || '') !== value) continue; try { vals.push(spec.num(m)); } catch (e) { /* no record: left out */ } }
  MED.set(k, {v: median(vals), n: vals.filter(v => v != null).length});
 }
 return MED.get(k);
}
const BASE_LABEL = {chamber: 'Chamber median', party: 'Party median'};

/* ---------------- view state ---------------- */
const S = {baseline: 'none', diff: true, failNext: false, subscribed: false, focus: null, last: null};
let els = null;

function titleRowHTML(){
 const plus = '<svg class="dc-plus" viewBox="0 0 14 14" aria-hidden="true" focusable="false"><path d="M7 2v10M2 7h10"/></svg>';
 return `<div class="dc-titlerow">
 <h2 class="dc-title">Compare members</h2>
 <div class="dc-tools">
  <span class="dc-tool" data-dc-needs-members><label class="mlk-select dc-select"><span class="mlk-select__label">Baseline:</span><select id="dc-baseline"><option value="none">None</option><option value="chamber">Chamber median</option><option value="party">Party median</option></select></label></span>
  <span class="dc-tool dc-switchwrap" data-dc-needs-members><button type="button" class="mlk-switch" role="switch" id="dc-diff" aria-checked="true" aria-labelledby="dc-diff-label"></button><label class="dc-switchlabel" id="dc-diff-label" for="dc-diff">Differences only</label></span>
  <span class="mlk-anchor dc-addanchor"><button type="button" class="mlk-btn dc-addbtn" id="dc-add" aria-haspopup="dialog" aria-expanded="false" aria-controls="dc-addpop">${plus}<span>Add member</span></button>
   <div class="mlk-popover mlk-popover--end dc-addpop" id="dc-addpop" role="dialog" aria-label="Add a member to compare" hidden>
    <div class="mlk-search dc-addsearch">${K.icon('search').replace('<svg', '<svg class="mlk-search__icon"')}<input class="mlk-search__input" id="dc-addq" type="search" role="combobox" aria-expanded="true" aria-controls="dc-addres" aria-autocomplete="list" autocomplete="off" spellcheck="false" placeholder="Search member or state" aria-label="Search member or state"></div>
    <div class="mlk-results dc-addres" id="dc-addres" role="listbox" aria-label="Members"></div>
   </div>
  </span>
 </div>
</div>`;
}

function plusHTML(){ return '<svg class="dc-plus" viewBox="0 0 14 14" aria-hidden="true" focusable="false"><path d="M7 2v10M2 7h10"/></svg>'; }

function skeletonHTML(){
 return `<div class="mlk-card dc-card dc-card--loading" aria-busy="true" role="status" aria-label="Loading the comparison">`
  + '<div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div>'.repeat(9)
  + '</div>';
}

function colHead(m, id){
 const name = esc(m.name);
 return `<th scope="col" class="dc-colhead"><div class="dc-colhead__in">`
  + `<span class="mlk-dot dc-dot" style="--mlk-dot:${esc(K.partyColor(m.party || ''))}" title="${esc(m.party || 'Party unknown')}"></span>`
  + `<span class="dc-colhead__text"><a class="mlk-name dc-name" data-mlk-nav href="${esc(K.url('profile', {m: m.name, tab: 'overview'}))}">${name}</a>`
  + `<span class="dc-colhead__sub">${esc(m.chamber || DASH)}<span class="dc-sep"> · </span>${esc(place(m))}</span></span>`
  + `<button type="button" class="mlk-close dc-remove" data-dc-remove="${esc(id)}" aria-label="Remove ${name} from compare">${K.icon('close')}</button>`
  + `</div></th>`;
}

// numeric row -> {col: baseline column html, per: [muted second line per member]}
function baselineFor(spec, members){
 if (S.baseline === 'none') return null;
 const field = S.baseline, label = BASE_LABEL[field];
 if (!spec.num || !spec.base) return {col: `<span class="dc-none">${DASH}</span>`, per: []};
 const groups = [];
 members.forEach(m => { const g = m[field] || ''; if (!groups.includes(g)) groups.push(g); });
 const fmt = r => (r.v == null ? DASH : spec.base(r.v));
 const col = groups.length === 1
  ? esc(fmt(groupMedian(spec, field, groups[0])))
  : groups.map(g => `<span class="dc-baseline__line"><span class="dc-baseline__grp">${esc(g || 'Unknown')}</span> ${esc(fmt(groupMedian(spec, field, g)))}</span>`).join('');
 const per = (field === 'party' || groups.length > 1)
  ? members.map(m => `<span class="dc-base">${esc(label)} ${esc(fmt(groupMedian(spec, field, m[field] || '')))}</span>`)
  : [];
 return {col, per};
}

function bestSet(spec, members){
 if (!spec.best || members.length < 2) return new Set();
 const vals = members.map(m => { try { return spec.num(m); } catch (e) { return null; } });
 const real = vals.filter(v => v != null);
 if (real.length < 2) return new Set();
 const target = spec.best === 'min' ? Math.min(...real) : Math.max(...real);
 if (real.every(v => v === target)) return new Set();
 return new Set(vals.map((v, i) => (v === target ? i : -1)).filter(i => i >= 0));
}

function rowHTML(label, cells, best, base, cls){
 const tds = cells.map((c, i) => {
  const extra = base && base.per[i] ? base.per[i] : '';
  const t = c.title ? ` title="${esc(c.title)}"` : '';
  return `<td class="dc-cell${best.has(i) ? ' dc-best' : ''}"${t}><span class="dc-v">${c.html != null ? c.html : esc(c.text)}</span>${extra}</td>`;
 }).join('');
 return `<tr class="${cls}"><th scope="row" class="dc-label">${esc(label)}</th>${tds}`;
}

function paint(){
 if (!els || !els.body.isConnected) return;
 const body = els.body;
 try {
  if (S.failNext){ S.failNext = false; throw new Error('The comparison could not load.'); }
  const ids = K.compare.list();
  const idx = index();
  const found = [], missing = [];
  ids.forEach(id => { const i = idx.get(id); if (i == null) missing.push(id); else found.push({id, i, m: M[i]}); });
  const members = found.map(f => f.m);
  els.root.querySelectorAll('[data-dc-needs-members]').forEach(el => { el.hidden = members.length < 2; });
  let html = '';
  if (missing.length){
   html += `<p class="dc-note" role="status">${missing.length === 1 ? '1 selected member is' : missing.length + ' selected members are'} not in this build (${esc(missing.join(', '))}). `
    + `<button type="button" class="mlk-btn mlk-btn--sm" data-dc-drop="${esc(missing.join(' '))}">Remove</button></p>`;
  }
  if (!members.length){
   html += `<div class="mlk-card dc-emptycard"><p class="dc-emptycard__text">Pick members from the Members list, a profile or the map to compare them.</p>`
    + `<button type="button" class="mlk-btn" data-dc-add>${plusHTML()}<span>Add member</span></button></div>`;
   body.innerHTML = html;
   body.removeAttribute('aria-busy');
   S.last = {ids, members: [], shown: [], hidden: [], baseline: S.baseline, diff: S.diff, best: {}};
   settleFocus();
   return;
  }
  const one = members.length === 1;
  const diffOn = S.diff && !one;
  const baseOn = S.baseline !== 'none' && !one;
  const hidden = [], shown = [], bestLog = {};
  let rows = '';
  for (const spec of FIGS){
   const cells = members.map(m => spec.cell(m));
   const same = members.length > 1 && cells.every(c => (c.key || c.text) === (cells[0].key || cells[0].text));
   if (diffOn && same){ hidden.push(`${spec.cap || spec.label} (${cells[0].text})`); continue; }
   shown.push(spec.label);
   const best = bestSet(spec, members);
   if (best.size) bestLog[spec.key] = [...best].map(i => found[i].id);
   const base = baseOn ? baselineFor(spec, members) : null;
   rows += rowHTML(spec.label, cells, best, base, 'dc-row') + (one ? '<td class="dc-cell dc-cell--placeholder"></td>' : '')
    + (base ? `<td class="dc-cell dc-cell--baseline">${base.col}</td>` : '') + '</tr>';
  }
  let areaRows = '';
  for (const cat of CATS){
   const cells = members.map(m => areaCell(m, cat));
   const same = members.length > 1 && cells.every(c => c.text === cells[0].text);
   if (diffOn && same){ hidden.push(`${cat} (${cells[0].text})`); continue; }
   shown.push(cat);
   areaRows += rowHTML(cat, cells, new Set(), null, 'dc-row dc-row--area') + (one ? '<td class="dc-cell dc-cell--placeholder"></td>' : '')
    + (baseOn ? `<td class="dc-cell dc-cell--baseline"><span class="dc-none">${DASH}</span></td>` : '') + '</tr>';
  }
  const ncols = 1 + members.length + (one ? 1 : 0) + (baseOn ? 1 : 0);
  if (areaRows) rows += `<tr class="dc-section"><th scope="colgroup" colspan="${ncols}" class="dc-section__label">Stance by legislative area</th></tr>` + areaRows;
  const colgroup = `<colgroup><col class="dc-col-label">${members.map(() => '<col class="dc-col-member">').join('')}${one ? '<col class="dc-col-member">' : ''}${baseOn ? '<col class="dc-col-baseline">' : ''}</colgroup>`;
  const head = `<thead><tr class="dc-headrow"><td class="dc-corner"></td>${found.map(f => colHead(f.m, f.id)).join('')}`
   + (one ? `<th scope="col" class="dc-colhead dc-colhead--placeholder"><button type="button" class="dc-placeholder" data-dc-add>${plusHTML()}<span>Add a member to compare</span></button></th>` : '')
   + (baseOn ? `<th scope="col" class="dc-colhead dc-colhead--baseline"><div class="dc-colhead__in"><span class="dc-colhead__text"><span class="dc-basehead">${esc(BASE_LABEL[S.baseline])}</span><span class="dc-colhead__sub">${esc(baseSub(members))}</span></span></div></th>` : '')
   + '</tr></thead>';
  let foot = '';
  if (diffOn){
   foot = hidden.length
    ? `<p class="dc-foot">${hidden.length} identical ${hidden.length === 1 ? 'row' : 'rows'} hidden: ${esc(hidden.join(', '))}</p>`
    : '<p class="dc-foot">No identical rows</p>';
  }
  html += `<div class="mlk-card dc-card"><div class="dc-scroll"><table class="dc-grid dc-grid--n${members.length}${baseOn ? ' dc-grid--base' : ''}" aria-label="Members compared side by side">${colgroup}${head}<tbody>${rows}</tbody></table></div>${foot}</div>`;
  body.innerHTML = html;
  body.removeAttribute('aria-busy');
  S.last = {ids, members: found.map(f => f.id), shown, hidden, baseline: S.baseline, diff: S.diff, best: bestLog};
  settleFocus();
 } catch (e) {
  body.removeAttribute('aria-busy');
  body.innerHTML = `<p class="mlk-state mlk-state--error dc-error" role="alert">${esc((e && e.message) || 'The comparison could not load.')} <button type="button" class="mlk-btn" data-dc-retry>Retry</button></p>`;
  S.last = {error: String((e && e.message) || e)};
 }
}
function baseSub(members){
 const field = S.baseline, groups = [];
 members.forEach(m => { const g = m[field] || ''; if (!groups.includes(g)) groups.push(g); });
 if (groups.length > 1) return field === 'party' ? 'By party, all members' : 'By chamber, all members';
 const n = M.filter(m => (m[field] || '') === groups[0]).length;
 return `${groups[0] || 'Unknown'}, ${n.toLocaleString()} members`;
}

// after a column is removed, keep focus in the grid: the next column's ×, else the previous one, else Add member
function settleFocus(){
 const f = S.focus; S.focus = null;
 if (!f || !els) return;
 const xs = [...els.body.querySelectorAll('[data-dc-remove]')];
 const target = xs[Math.min(f.idx, xs.length - 1)] || els.body.querySelector('[data-dc-add]') || els.add;
 if (target) target.focus({preventScroll: true});
}

/* ---------------- + Add member: search the desk's own members ---------------- */
const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
let NAMES = null;
function names(){ if (!NAMES) NAMES = M.map(m => ({n: norm(m.name), s: norm(m.state), w: norm(m.name).split(/[\s.'-]+/)})); return NAMES; }
let active = -1, options = [];
function search(q){
 const s = norm(q).trim();
 if (!s) return [];
 const out = [];
 names().forEach((x, i) => {
  const r = x.n.startsWith(s) ? 0 : x.w.some(w => w.startsWith(s)) ? 1 : x.n.includes(s) ? 2 : x.s.startsWith(s) ? 3 : x.s.includes(s) ? 4 : -1;
  if (r >= 0) out.push([r, i]);
 });
 out.sort((a, b) => a[0] - b[0] || M[a[1]].name.localeCompare(M[b[1]].name));
 return out.slice(0, 8).map(x => x[1]);
}
function renderResults(){
 if (!els) return;
 const q = els.q.value, list = K.compare.list(), full = list.length >= K.compare.max;
 options = search(q);
 let html = full ? '<div class="mlk-results__empty">Compare holds four members</div>' : '';
 if (!norm(q).trim()) html += '<div class="mlk-results__empty">Type a member name or state</div>';
 else if (!options.length) html += '<div class="mlk-results__empty">No matches</div>';
 else {
  html += '<div class="mlk-results__group">Members</div>' + options.map((i, k) => {
   const m = M[i], id = idOf(m), inStore = list.includes(id);
   return `<div class="mlk-results__item dc-opt" role="option" id="dc-opt-${k}" data-i="${i}" aria-selected="false"${inStore || full ? ' aria-disabled="true"' : ''}>`
    + `<span class="mlk-dot" style="--mlk-dot:${esc(K.partyColor(m.party || ''))}"></span><span class="mlk-results__main">${esc(m.name)}</span>`
    + `<span class="mlk-results__meta">${esc(subline(m))}${inStore ? ' · In compare' : ''}</span></div>`;
  }).join('');
 }
 els.res.innerHTML = html;
 setActive(options.length ? 0 : -1);
}
function setActive(k){
 active = k;
 els.res.querySelectorAll('.dc-opt').forEach((o, j) => o.setAttribute('aria-selected', String(j === k)));
 if (k >= 0){ els.q.setAttribute('aria-activedescendant', 'dc-opt-' + k); const o = D0.getElementById('dc-opt-' + k); if (o && o.scrollIntoView) o.scrollIntoView({block: 'nearest'}); }
 else els.q.removeAttribute('aria-activedescendant');
}
function pick(i){
 const m = M[i]; if (!m) return;
 const id = idOf(m);
 if (!id || K.compare.has(id)) return;
 if (K.compare.add(id)){ els.q.value = ''; K.close(els.pop); }   // a refused 5th add shows the store's toast and keeps the search open
}
function openAdd(returnTo){
 if (!els) return;
 els.q.value = '';
 renderResults();
 K.open(els.pop, {trigger: els.add, kind: 'popover', focus: els.q, returnTo: returnTo || els.add});
}

/* ---------------- wiring ---------------- */
function wire(root){
 els.add.addEventListener('click', e => { e.preventDefault(); if (K.isOpen(els.pop)) K.close(els.pop); else openAdd(els.add); });
 els.q.addEventListener('input', renderResults);
 els.q.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
   if (!options.length) return;
   e.preventDefault();
   setActive((active + (e.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
  } else if (e.key === 'Enter'){
   e.preventDefault();
   if (options.length) pick(options[active >= 0 ? active : 0]);
  }
 });
 els.res.addEventListener('mousedown', e => { if (e.target.closest('.dc-opt')) e.preventDefault(); });   // keep focus in the input
 els.res.addEventListener('click', e => { const o = e.target.closest('.dc-opt'); if (o) pick(Number(o.dataset.i)); });
 els.baseline.addEventListener('change', () => { S.baseline = els.baseline.value; paint(); });
 els.diff.addEventListener('click', () => { S.diff = !S.diff; els.diff.setAttribute('aria-checked', String(S.diff)); paint(); });
 root.addEventListener('click', e => {
  const t = e.target.closest ? e.target : null; if (!t) return;
  const add = t.closest('[data-dc-add]');
  if (add){ e.preventDefault(); openAdd(add); return; }
  const rm = t.closest('[data-dc-remove]');
  if (rm){
   e.preventDefault();
   S.focus = {idx: [...els.body.querySelectorAll('[data-dc-remove]')].indexOf(rm)};
   K.compare.remove(rm.getAttribute('data-dc-remove'));
   return;
  }
  const drop = t.closest('[data-dc-drop]');
  if (drop){ e.preventDefault(); drop.getAttribute('data-dc-drop').split(' ').forEach(id => K.compare.remove(id)); return; }
  if (t.closest('[data-dc-retry]')){ e.preventDefault(); paint(); }
 });
}

function howToRead(){
 const n = M.length.toLocaleString();
 return {text: `Each column is one member from the Compare list (up to four), with values from the member's desk record: the profile figures and the recognition in each legislative area, in the desk's own wording. `
  + `Bold marks the best value in rows that have one: the lowest overall priority rank, and the highest model score, district opportunity, credit-union PAC contributions and return per $10,000. `
  + `Differences only hides rows whose values are the same for every member shown; the caption under the table lists them. `
  + `Chamber median and Party median are computed from all ${n} members in the desk; members without a value are left out, and stance rows have no median.`};
}

K.registerDeskView('compare', {
 async render(section){
  section.classList.add('dc-view');
  section.innerHTML = titleRowHTML() + `<div class="dc-body" id="dc-body" aria-live="polite">${skeletonHTML()}</div>`;
  els = {
   root: section, body: section.querySelector('#dc-body'), add: section.querySelector('#dc-add'), pop: section.querySelector('#dc-addpop'),
   q: section.querySelector('#dc-addq'), res: section.querySelector('#dc-addres'),
   baseline: section.querySelector('#dc-baseline'), diff: section.querySelector('#dc-diff')
  };
  els.body.setAttribute('aria-busy', 'true');
  els.baseline.value = S.baseline;
  els.diff.setAttribute('aria-checked', String(S.diff));
  wire(section);
  try { K.head.setInfo(howToRead()); } catch (e) { /* the head is optional outside the shell */ }
  if (!S.subscribed){
   S.subscribed = true;
   K.compare.subscribe(() => { paint(); if (els && K.isOpen(els.pop)) renderResults(); });
  }
  await new Promise(r => (window.requestAnimationFrame ? requestAnimationFrame(() => r()) : setTimeout(r, 0)));
  index();
  paint();
 }
});

window.MelangeCompareView = {
 state: () => (S.last ? JSON.parse(JSON.stringify(S.last)) : null),
 repaint: paint,
 showLoading(){ if (els){ els.body.setAttribute('aria-busy', 'true'); els.body.innerHTML = skeletonHTML(); } },
 failNext(){ S.failNext = true; }
};
})();
