/* desk-profile.js: overlay for original-desk.html (member profile, board #29); owner: prompt 04. Runs after the desk engine and ui-kit.js, before restored-desk.js. */
/* The member profile as a page with six tabs. restored-desk.js loads the member, runs the legacy openMember(i) and then
   calls window.MelangeProfile.mount(i). mount() MOVES the legacy dossier nodes (their listeners stay attached) into the
   tabs and re-renders the header, the figures row, the stance list, the pillar bars, the engagement card and the bill
   table from the same M[i] / D / HONORS records the legacy renderer reads. Data colours are read by reference from the
   desk's own maps (STANDING_COLORS, HONOR_COLORS, STCOL, PTCOL, PILL, ROLEBADGE); no colour value is written here. */
(() => {
'use strict';
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const TABS = [['overview', 'Overview'], ['bills', 'Bills'], ['access', 'Access'], ['district', 'District'], ['coverage', 'Press & coverage'], ['sources', 'Sources & method']];
// Addresses minted before build 04 (nine tabs) still land on the tab that now holds their content.
const TAB_ALIAS = {background: 'overview', behavior: 'overview', positions: 'bills', demographics: 'district', press: 'coverage', method: 'sources'};
const AREAS_SHOWN = 9;                       // board #29: nine rows, then "Show N more"
const RETURN_KEY = 'mlk-members-return';     // written by restored-desk.js while the Members list is open
const ICON_BACK = '<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M11.5 7h-9M6 3.5 2.5 7 6 10.5"/></svg>';
const ICON_PRINT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6V2.5h7V6M4.5 11.5H3A1.5 1.5 0 0 1 1.5 10V7.5A1.5 1.5 0 0 1 3 6h10a1.5 1.5 0 0 1 1.5 1.5V10a1.5 1.5 0 0 1-1.5 1.5h-1.5"/><path d="M4.5 9.5h7v4h-7z"/></svg>';
const kit = () => window.MLK || null;
const shellUrl = (view, extra) => { const k = kit(); if (k && k.url) return k.url(view, extra); const q = new URLSearchParams({view, ...extra}); return '/?' + q; };
const plural = (n, word) => `${Number(n || 0).toLocaleString('en-US')} ${word}${Number(n) === 1 ? '' : 's'}`;

/* ---------------- data helpers (same fields and formulas as the legacy renderer) ---------------- */
function overallRow(m){ try { return HONORS ? honorAt(m, '') : null; } catch (e) { return null; } }
function fieldRow(m, cat){ try { return HONORS ? honorAt(m, cat) : null; } catch (e) { return null; } }
function titleOf(html){ const t = document.createElement('div'); t.innerHTML = html || ''; const el = t.querySelector('[title]'); return el ? el.getAttribute('title') : ''; }
function tracker(name){
 let all = null;
 try { all = JSON.parse(localStorage.getItem('mdesk_manual_v1')); } catch (e) { all = null; }
 let mem = null; try { mem = MAN; } catch (e) { mem = null; }
 const rec = (all && all[name]) || (mem && mem[name]) || {};
 return rec.tracker || {};
}
function lastContact(t){
 if (t.last) return t.last;
 const dates = (t.contacts || []).map(c => c && c.date).filter(Boolean).sort();
 return dates.length ? dates[dates.length - 1] : '';
}
function returnHref(){
 let v = '';
 try { v = sessionStorage.getItem(RETURN_KEY) || ''; } catch (e) { v = ''; }
 try {
  const u = new URL(v, location.origin);
  if (v && u.origin === location.origin && u.pathname === '/' && u.searchParams.get('view') === 'members') return u.pathname + u.search;
 } catch (e) { /* bad value: fall through */ }
 return shellUrl('members', {});
}

/* ---------------- header, figures ---------------- */
function headHTML(m){
 const row = overallRow(m);
 let pill;
 if (row) pill = `<span class="mlk-pill" style="--mlk-dot:${esc(STANDING_COLORS[row.stance] || '')}" title="${esc(honorFacts(m, ''))}">${esc(row.stance)}</span>`;
 else {
  const code = m.overall_stance || '-', name = stanceName(code);
  pill = name === '—' ? '<span class="mlk-pill mlk-pill--none">No stance on record</span>' : `<span class="mlk-pill" style="--mlk-dot:${esc(STCOL[code] || '')}">${esc(name)}</span>`;
 }
 const tier = m.tier ? `<span class="mlk-tier" title="Primacy tier ${esc(m.tier)}${m.tierrank ? ' · #' + esc(m.tierrank) : ''}">T${esc(m.tier)}</span>` : '';
 const play = m.ev_play && m.ev_play !== 'No active play' ? m.ev_play : '';
 const elec = play ? `<span class="mlk-chip mlp-elec" title="Electoral exposure · ${esc(play)}">Electoral: ${esc(play)}${m.ev_value > 0 ? ' · ' + esc(money(m.ev_value)) : ''}</span>` : '';
 const party = Object.prototype.hasOwnProperty.call(PTCOL, m.party || '') ? PTCOL[m.party || ''] : PTCOL[''];
 const meta = [m.chamber, m.party || '—', m.district].filter(Boolean).map(esc).join('<span class="mlp-sep" aria-hidden="true">·</span>');
 return `<div class="mlp-head"><div class="mlp-id"><h2 class="mlp-name">${esc(m.name)}</h2>${tier}${pill}${elec}</div>`
  + `<p class="mlp-meta"><span class="mlk-dot" style="--mlk-dot:${esc(party)}"></span>${meta}</p></div>`;
}
function figuresHTML(m){
 const row = overallRow(m), unresolved = String(m.money_source_status || '').startsWith('UNRESOLVED');
 const tiles = [
  ['#' + (row ? (row.rank ?? '—') : (m.rank ?? '—')), row ? 'Overall priority rank' : 'Overall rank', ''],
  [m.tier ? 'Tier ' + m.tier : 'Not tiered', 'Primacy tier', m.tier && m.tierrank ? 'Primacy tier · #' + m.tierrank : ''],
  [f2(m.score), row ? 'Model score' : 'Overall score', '']
 ];
 const p = m.demographic_potential || {};
 if (p.score != null) tiles.push([Number(p.score).toFixed(1), `District opportunity · ${ordinal(p.rank)} of ${p.market_count}`, titleOf(potentialChip(m))]);
 tiles.push([unresolved ? 'Unresolved' : money(m.invested), 'Credit-union PAC contributions', unresolved ? m.money_source_status : '']);
 tiles.push([unresolved ? 'Unresolved' : money(m.ind_money), 'Bank & financial-service PACs', '']);
 tiles.push([(m.roi || 0).toFixed(3), 'Return per $10,000', '']);
 return `<div class="mlp-figs" role="list" style="--mlp-figs:${tiles.length}">` + tiles.map(([v, l, t]) =>
  `<div class="mlp-fig" role="listitem"${t ? ` title="${esc(t)}"` : ''}><span class="mlp-fig__v">${esc(v)}</span><span class="mlp-fig__l">${esc(l)}</span></div>`).join('') + '</div>';
}

/* ---------------- stance by legislative area ---------------- */
function areaRows(m){
 const bands = HONORS ? honorBands() : [];
 return CATS.map((cat, ix) => {
  if (HONORS){
   const row = fieldRow(m, cat);
   if (!row) return {cat, ix, label: 'Evidence unavailable', colour: '', order: 999, rank: Infinity, noAction: false, rankText: '—', row: null};
   let label; try { label = honorLabel(m, cat); } catch (e) { label = row.recognition || 'No recorded action'; }
   const observed = row.evidence_status === 'observed', bi = bands.indexOf(row.recognition);
   return {cat, ix, row, label, colour: row.recognition ? (HONOR_COLORS[row.recognition] || '') : '',
    order: bi >= 0 ? bi : (label === 'No recorded action' ? 1001 : 1000), rank: row.rank_start ?? Infinity,
    noAction: label === 'No recorded action',
    rankText: `${honorRank(row)}${observed ? ' of ' + row.cohort_size : ''} · ${plural(row.action_count, 'action')}`};
  }
  const st = (m.stances && m.stances[ix]) || '-', order = STANCE_ORDER.indexOf(st);
  return {cat, ix, row: null, label: stanceName(st), colour: st === '-' ? '' : (STCOL[st] || ''), order: order < 0 ? 99 : order,
   rank: (m.adjrank && m.adjrank[cat]) || Infinity, noAction: st === '-', rankText: '#' + ((m.adjrank && m.adjrank[cat]) || '—')};
 });
}
function areaDetail(m, a){
 const row = a.row;
 if (!row) return `<p>${esc(a.label)} · ${esc(a.rankText)}</p>`;
 const denominator = row.sub_cohort_size || row.cohort_size;
 const rank = row.evidence_status === 'observed'
  ? (row.rank_start == null ? `Net neutral · ${row.cohort_size} members with recorded action` : `${honorRank(row)} of ${denominator} with recorded action`)
  : `No field rank · ${row.cohort_size} members with recorded action`;
 const window_ = 'Congresses ' + ((HONORS.evidence_window && HONORS.evidence_window.congresses) || []).join(', ');
 const line = [row.alignment, rank, plural(row.action_count, 'recorded action'), window_].filter(Boolean).map(esc).join(' · ');
 const KIND = {legislative: 'Bill', letter: 'Letter', vote: 'Floor vote', press: 'Press'};
 const acts = row.field_actions || [];
 const ledger = acts.length ? `<table class="mlp-ledger"><thead><tr><th scope="col">Measure</th><th scope="col">Title</th><th scope="col">Kind</th><th scope="col">Role</th><th scope="col">Direction</th></tr></thead><tbody>`
  + acts.map(x => `<tr><td>${esc(x.m)}</td><td>${esc(measureTitle(x.m))}</td><td>${esc(KIND[x.k] || x.k)}</td><td>${esc(x.r)}</td><td>${esc(x.d > 0 ? 'supports CU position' : x.d < 0 ? 'opposes CU position' : 'neutral direction')}</td></tr>`).join('')
  + '</tbody></table>' : '';
 return `<p>${line}</p>${row.reason ? `<p class="mlp-muted">Reason: ${esc(String(row.reason).replaceAll('_', ' '))}</p>` : ''}${ledger}`;
}
function stanceCard(m){
 const card = document.createElement('section');
 card.className = 'mlk-card mlp-card mlp-areas';
 card.setAttribute('aria-labelledby', 'mlp-areas-title');
 const state = {sort: 'rank', hide: true, all: false, open: new Set()};
 let rows;
 try { rows = areaRows(m); } catch (e) { rows = null; }
 card.innerHTML = `<div class="mlp-areas__head"><h3 class="mlk-label" id="mlp-areas-title">Stance by legislative area</h3>`
  + `<label class="mlk-select mlp-sort"><span class="mlk-select__label">Sort:</span><select aria-label="Sort legislative areas"><option value="rank">Rank</option><option value="name">Area name</option></select></label>`
  + `<span class="mlp-switch"><button type="button" class="mlk-switch" role="switch" aria-checked="true" id="mlp-hide-none" aria-labelledby="mlp-hide-none-label"></button><span id="mlp-hide-none-label">Hide no recorded action</span></span></div>`
  + '<div class="mlp-areas__list" role="list"></div><div class="mlp-areas__foot"></div>';
 const list = card.querySelector('.mlp-areas__list'), foot = card.querySelector('.mlp-areas__foot');
 const sw = card.querySelector('.mlk-switch'), sel = card.querySelector('select');
 function paint(){
  if (!rows){ list.innerHTML = '<p class="mlk-state mlk-state--error" role="alert">Stance records could not be read for this member.</p>'; foot.innerHTML = ''; return; }
  const sorted = rows.slice().sort(state.sort === 'name' ? (a, b) => a.cat.localeCompare(b.cat) : (a, b) => (a.order - b.order) || (a.rank - b.rank) || (a.ix - b.ix));
  const hidden = state.hide ? sorted.filter(r => r.noAction).length : 0;
  const visible = state.hide ? sorted.filter(r => !r.noAction) : sorted;
  const shown = state.all ? visible : visible.slice(0, AREAS_SHOWN), more = visible.length - shown.length;
  list.innerHTML = shown.length ? shown.map(a => {
   const id = 'mlp-area-' + a.ix, open = state.open.has(a.ix);
   const pill = a.colour ? `<span class="mlk-pill" style="--mlk-dot:${esc(a.colour)}">${esc(a.label)}</span>` : `<span class="mlk-pill mlk-pill--none">${esc(a.label)}</span>`;
   return `<div class="mlp-area" role="listitem"><button type="button" class="mlp-area__row" aria-expanded="${open}" aria-controls="${id}" data-area="${a.ix}">`
    + `<span class="mlp-area__name">${esc(a.cat)}</span><span class="mlp-area__pill">${pill}</span><span class="mlp-area__rank">${String(a.rankText).split(' · ').map(esc).join('<span class="mlp-ranksep"> · </span>')}</span></button>`
    + `<div class="mlp-area__detail" id="${id}"${open ? '' : ' hidden'}>${open ? areaDetail(m, a) : ''}</div></div>`;
  }).join('') : `<p class="mlk-state">No recorded action in any legislative area${hidden ? ` · ${plural(hidden, 'area')} hidden` : ''}</p>`;
  const bits = [];
  if (more > 0) bits.push(`<button type="button" class="mlp-linkbtn" data-more>Show ${more} more</button>`);
  else if (state.all && visible.length > AREAS_SHOWN) bits.push('<button type="button" class="mlp-linkbtn" data-more>Show fewer</button>');
  if (hidden && shown.length) bits.push(`<span>${plural(hidden, 'area')} with no recorded action hidden</span>`);
  foot.innerHTML = bits.join('<span class="mlp-dotsep" aria-hidden="true">·</span>');
  foot.hidden = !bits.length;
  grown();
 }
 // At rest the Overview's two columns end together (desk-profile.css); an open area or the full list lets this card grow alone.
 const grown = () => card.classList.toggle('is-grown', state.all || [...state.open].some(ix => list.querySelector(`[data-area="${ix}"][aria-expanded="true"]`)));
 list.addEventListener('click', e => {
  const b = e.target.closest('.mlp-area__row'); if (!b) return;
  const ix = Number(b.dataset.area), a = rows.find(r => r.ix === ix), d = document.getElementById('mlp-area-' + ix);
  const open = b.getAttribute('aria-expanded') !== 'true';
  if (open){ state.open.add(ix); d.innerHTML = areaDetail(m, a); } else state.open.delete(ix);
  b.setAttribute('aria-expanded', String(open)); d.hidden = !open;
  grown();
 });
 foot.addEventListener('click', e => { if (!e.target.closest('[data-more]')) return; state.all = !state.all; paint(); const b = foot.querySelector('[data-more]'); if (b) b.focus(); });
 sw.addEventListener('click', () => { state.hide = !state.hide; sw.setAttribute('aria-checked', String(state.hide)); paint(); });
 card.querySelector('#mlp-hide-none-label').addEventListener('click', () => sw.click());
 sel.addEventListener('change', () => { state.sort = sel.value; paint(); });
 paint();
 return card;
}

/* ---------------- pillars, engagement ---------------- */
function pillarCard(m, legacyGen){
 const card = document.createElement('section');
 card.className = 'mlk-card mlp-card mlp-pillars';
 card.setAttribute('aria-labelledby', 'mlp-pillars-title');
 const rows = ['press', 'comm', 'leg', 'demo'].map(k => {
  const P = PILL[k] || {}, v = m[k + '_n'], w = Number(P.w), width = Math.max(0, Math.min(100, (Number(v) || 0) * 100));
  const label = String(P.l || k).replace(/\s*\(diminished\)\s*$/, '');
  return `<div class="mlp-pillar" role="listitem"${label !== P.l ? ` title="${esc(P.l)}"` : ''}><span class="mlp-pillar__label">${esc(label)} <span class="mlp-muted">×${Number.isFinite(w) ? w.toFixed(2) : '—'}</span></span>`
   + `<span class="mlp-pillar__bar" style="--mlp-v:${width.toFixed(1)}%;--mlp-c:${esc(P.c || '')}" role="img" aria-label="${esc(label)} ${esc(f2(v))}"><i></i></span><span class="mlp-pillar__val">${esc(f2(v))}</span></div>`;
 }).join('');
 card.innerHTML = `<h3 class="mlk-label" id="mlp-pillars-title">Pillar composition</h3><div class="mlp-pillars__list" role="list">${rows}</div>`;
 // The legacy card's formal-role note (party leadership office) stays with the pillars it qualifies.
 const note = legacyGen && [...legacyGen.querySelectorAll('div')].find(d => /titled public-exposure context/.test(d.textContent) && !d.querySelector('div'));
 if (note){ note.className = 'mlp-note'; note.removeAttribute('style'); card.append(note); }
 return card;
}
function engagementCard(m, trackcard){
 const card = document.createElement('section');
 card.className = 'mlk-card mlp-card mlp-eng';
 card.setAttribute('aria-labelledby', 'mlp-eng-title');
 card.innerHTML = '<h3 class="mlk-label" id="mlp-eng-title">Engagement</h3><dl class="mlp-kv"></dl><div class="mlp-eng__form" id="mlp-eng-form" hidden></div>'
  + '<div class="mlp-eng__actions"><button type="button" class="mlk-btn mlp-edit" aria-expanded="false" aria-controls="mlp-eng-form">Edit</button>'
  // r5 F4: board #29 draws Edit alone. Notes & follow-up (the native notes page) stays one step away, inside the Edit flow:
  // it shows beside Done while the tracker form is open, and in place of Edit when there is no tracker form to open.
  + `<a class="mlp-textlink mlp-eng__notes" data-mlk-nav href="${esc(shellUrl('profile', {m: m.name, tab: 'notes'}))}" hidden>Notes &amp; follow-up</a><span class="mlp-eng__status" role="status"></span></div>`;
 const dl = card.querySelector('dl'), form = card.querySelector('.mlp-eng__form'), edit = card.querySelector('.mlp-edit'), status = card.querySelector('.mlp-eng__status'), notes = card.querySelector('.mlp-eng__notes');
 const paint = () => {
  const t = tracker(m.name);
  const rows = [['Status', t.status && t.status !== '—' ? t.status : ''], ['Last contact', lastContact(t)], ['Owner', t.owner], ['Next step', t.next]];
  dl.innerHTML = rows.map(([k, v]) => `<div class="mlp-kv__row"><dt>${k}</dt><dd>${esc(v || 'none')}</dd></div>`).join('');
 };
 if (trackcard){
  // Today's inline tracker form (legacy trackerHTML + wireTracker), moved in place; its change handlers save to
  // localStorage mdesk_manual_v1 exactly as before. A failed save is reported instead of thrown.
  trackcard.className = 'mlp-trackform'; trackcard.removeAttribute('style');
  trackcard.querySelectorAll('.trk').forEach(el => {
   const save = el.onchange;
   el.classList.add('mlp-input');
   if (typeof save === 'function') el.onchange = e => { try { save.call(el, e); status.textContent = 'Saved on this device'; } catch (err) { status.textContent = 'Save failed. Free browser storage and retry.'; } };
  });
  form.append(trackcard);
 } else { edit.hidden = true; notes.hidden = false; }
 const setOpen = open => {
  form.hidden = !open; dl.hidden = open; notes.hidden = !open; edit.setAttribute('aria-expanded', String(open)); edit.textContent = open ? 'Done' : 'Edit';
  card.classList.toggle('is-open', open);
  if (open){ const first = form.querySelector('select,input'); if (first) first.focus(); }
  else { paint(); status.textContent = ''; }
 };
 edit.addEventListener('click', () => setOpen(form.hidden));
 form.addEventListener('keydown', e => { if (e.key === 'Escape'){ e.preventDefault(); e.stopPropagation(); setOpen(false); edit.focus(); } });
 paint();
 return card;
}

/* ---------------- bills table (board #33 style) ---------------- */
// Sponsor as the Bills view shows it (desk-bills.js shortName): 'Rep. Clyde [R-GA-9]'. The member bill list carries the
// Congress.gov form 'Padilla, Alex [Sen.-D-CA]' (sometimes with an amendment's '(Submitted ... )' note) and, for a few
// rows, the live form 'Sen. Feinstein, Dianne [D-CA]'. Display only: the full string stays in the cell's tooltip.
const PARTY_NAME = {D: 'Democrat', R: 'Republican', I: 'Independent', ID: 'Independent', L: 'Independent'};
function sponsorShort(raw){
 const s = String(raw || '').replace(/\s*\(.*\)\s*$/, '').trim();
 let mm = s.match(/^([^,\[]+?)\s*,[^\[]*?\[(Sen|Rep|Del)\.-([A-Z]+)-([^\]]+)\]$/);
 if (mm) return {text: `${mm[2]}. ${mm[1]} [${mm[3]}-${mm[4]}]`, party: mm[3]};
 mm = s.match(/^([^,\[]+?)\s*,[^\[]*?\[Resident Commissioner-([A-Z]+)-([^\]]+)\]$/);
 if (mm) return {text: `Resident Commissioner ${mm[1]} [${mm[2]}-${mm[3]}]`, party: mm[2]};
 mm = s.match(/^((?:Rep|Sen|Del)\.|Resident Commissioner)\s+([^,\[]+?)\s*,[^\[]*?(\[([A-Z]+)-[^\]]*\])$/);
 if (mm) return {text: `${mm[1]} ${mm[2]} ${mm[3]}`, party: mm[4]};
 return {text: s, party: ''};
}
function partyColour(party){
 let map = null; try { map = PTCOL; } catch (e) { map = null; }
 if (!map) return '';
 const name = PARTY_NAME[String(party || '').toUpperCase()] || party || '';
 return map[name] || map[''] || '';
}
// The same source order as the Bills view (desk-bills.js sponsorOf): the Congress.gov live cache for the measure
// (D.bill_live, keyed by the legacy billIdentity), then the member list's own string; a plain member name takes that
// member's party for its dot.
function billSponsor(b){
 const raw = String(b.sponsor || '').trim();
 let live = null;
 try { const id = billIdentity(b), all = (D.bill_live || {}).bills || {}, rec = id ? all[id.key] : null; live = rec && rec.sponsor && rec.sponsor.name ? rec.sponsor : null; } catch (e) { live = null; }
 if (live){ const s = sponsorShort(live.name); return {text: s.text, party: live.party || s.party, full: raw || live.name}; }
 const s = sponsorShort(raw);
 if (!s.party && raw){ let i = null; try { i = byName[raw]; } catch (e) { i = null; } if (i != null && M[i]) return {text: raw, party: M[i].party || '', full: raw}; }
 return {text: s.text, party: s.party, full: raw};
}
const BILL_CHIPS = 2;
function billChips(list, max){
 const a = list || [], shown = a.slice(0, max), rest = a.length - shown.length;
 return shown.map(x => `<span class="mlp-chip">${esc(x)}</span>`).join('') + (rest > 0 ? `<span class="mlp-chip mlp-chip--more" aria-label="${rest} more">+${rest}</span>` : '');
}
function billsPanel(m){
 const wrap = document.createElement('div');
 wrap.className = 'mlp-stack';
 const bills = m.bills || [];
 const lead = m.effectiveness_shadow_html ? `<div class="mlp-card mlk-card mlp-legacy-html">${m.effectiveness_shadow_html}</div>` : '';
 if (!bills.length){ wrap.innerHTML = lead + '<section class="mlk-card mlp-card"><p class="mlk-state">No tracked CU-relevant bills for this member (as sponsor, original cosponsor, or cosponsor).</p></section>'; return wrap; }
 const roles = [['', 'All roles'], ['sponsor', 'Sponsored'], ['original', 'Orig. cosponsor'], ['cosponsor', 'Cosponsor']];
 const areas = [...new Set(bills.flatMap(b => b.areas || []))].sort();
 wrap.innerHTML = lead + '<section class="mlk-card mlp-card mlp-bills" aria-label="Bills">'
  + `<div class="mlp-bills__controls"><div class="mlk-seg" role="group" aria-label="Role">${roles.map(([v, l]) => `<button type="button" data-role="${v}" aria-pressed="${v === ''}">${esc(l)}</button>`).join('')}</div>`
  + `<label class="mlk-select"><span class="mlk-select__label">Area:</span><select aria-label="Legislative area"><option value="">All areas</option>${areas.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join('')}</select></label>`
  + '<span class="mlp-bills__count" role="status"></span></div><div class="mlp-bills__body"></div></section>';
 const st = {role: '', area: ''}, body = wrap.querySelector('.mlp-bills__body'), count = wrap.querySelector('.mlp-bills__count');
 let shown = [];
 function paint(){
  const rows = shown = bills.filter(b => (!st.area || (b.areas || []).includes(st.area)) && (!st.role || b.role === st.role));
  count.textContent = plural(rows.length, 'bill');
  if (!rows.length){ const r = ROLEBADGE[st.role]; body.innerHTML = `<p class="mlk-state">No bills${r ? ' as ' + esc(r[0].toLowerCase()) : ''}${st.area ? ' in ' + esc(st.area) : ''}.</p>`; return; }
  body.innerHTML = '<div class="mlk-tablecard mlp-bills__table"><table class="mlk-table">'
   + '<colgroup><col class="mlp-c-role"><col class="mlp-c-bill"><col class="mlp-c-title"><col class="mlp-c-areas"><col class="mlp-c-sponsor"><col class="mlp-c-congress"><col class="mlp-c-cos"></colgroup>'
   + '<thead><tr><th scope="col">Role</th><th scope="col">Bill</th><th scope="col">Title</th><th scope="col">Areas</th><th scope="col">Sponsor</th><th scope="col">Congress</th><th scope="col">Cosponsors</th></tr></thead><tbody>'
   + rows.map((b, i) => {
    const r = ROLEBADGE[b.role] || [b.role || '—', ''], id = 'mlp-billsum-' + i;
    const role = r[1] ? `<span class="mlk-pill" style="--mlk-dot:${esc(r[1])}">${esc(r[0])}</span>` : `<span class="mlk-pill mlk-pill--none">${esc(r[0])}</span>`;
    const num = b.url ? `<a class="mlp-billno" href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.bill)}</a>` : `<span class="mlp-billno">${esc(b.bill)}</span>`;
    const title = b.summary ? `<button type="button" class="mlp-billtitle mlp-ellip" aria-expanded="false" aria-controls="${id}" title="${esc(b.title)}">${esc(b.title)}</button>` : `<span class="mlp-billtitle mlp-ellip" title="${esc(b.title)}">${esc(b.title)}</span>`;
    const areas = b.areas || [], sp = billSponsor(b), colour = sp.party ? partyColour(sp.party) : '';
    const dotHTML = colour ? `<span class="mlk-dot" aria-hidden="true" style="--mlk-dot:${esc(colour)}"></span>` : '';
    return `<tr><td>${role}</td><td>${num}</td><td class="mlp-bills__title">${title}</td>`
     + `<td class="mlp-bills__areas"${areas.length ? ` title="${esc(areas.join(', '))}"` : ''} data-row="${i}" data-n="${areas.length}"><span class="mlp-chips">${billChips(areas, BILL_CHIPS) || '<span class="mlp-none">—</span>'}</span></td>`
     + `<td class="mlp-bills__sponsor">${sp.text ? `<span class="mlp-ellip" title="${esc(sp.full)}">${dotHTML}<span>${esc(sp.text)}</span></span>` : '<span class="mlp-none">—</span>'}</td>`
     + `<td>${b.congress ? esc(b.congress) + 'th' : '—'}</td><td>${b.ncos != null ? esc(Number(b.ncos).toLocaleString('en-US')) : '—'}</td></tr>`
     + (b.summary ? `<tr class="mlp-billsum" id="${id}" hidden><td colspan="7">${esc(b.summary)}</td></tr>` : '');
   }).join('') + '</tbody></table></div>';
  fitChips();
 }
 // Areas show two chips then +N; a row whose two chips do not fit its column shows one chip then +N (the Bills view's
 // rule). Measured whenever the table gets a size: first paint, the tab being opened, a resize.
 function fitChips(){
  const cells = [...body.querySelectorAll('td.mlp-bills__areas[data-n]')].filter(td => +td.dataset.n > 1);
  if (!cells.length || !body.offsetWidth) return;
  cells.forEach(td => { if (td.dataset.fit === '1'){ td.firstElementChild.innerHTML = billChips(shown[+td.dataset.row].areas, BILL_CHIPS); td.dataset.fit = '2'; } });
  cells.filter(td => [...td.querySelectorAll('.mlp-chip:not(.mlp-chip--more)')].some(c => c.scrollWidth > c.clientWidth + 1))
   .forEach(td => { td.firstElementChild.innerHTML = billChips(shown[+td.dataset.row].areas, 1); td.dataset.fit = '1'; });
 }
 if (typeof ResizeObserver === 'function'){
  let queued = false;
  const ro = new ResizeObserver(() => {
   if (!wrap.isConnected){ ro.disconnect(); return; }
   if (queued) return; queued = true;
   requestAnimationFrame(() => { queued = false; fitChips(); });
  });
  ro.observe(body);
 }
 wrap.addEventListener('click', e => {
  const seg = e.target.closest('[data-role]');
  if (seg){ st.role = seg.dataset.role; wrap.querySelectorAll('[data-role]').forEach(b => b.setAttribute('aria-pressed', String(b === seg))); paint(); return; }
  const t = e.target.closest('button.mlp-billtitle');
  if (t){ const row = document.getElementById(t.getAttribute('aria-controls')), open = t.getAttribute('aria-expanded') !== 'true'; t.setAttribute('aria-expanded', String(open)); if (row) row.hidden = !open; }
 });
 wrap.querySelector('select').addEventListener('change', e => { st.area = e.target.value; paint(); });
 paint();
 return wrap;
}

/* ---------------- moving legacy groups ---------------- */
// A legacy sub-fold (details.sub) becomes an open sub-section: a heading plus the original body node, moved.
function subTitle(summary){ return [...summary.childNodes].filter(n => !(n.nodeType === 1 && n.classList.contains('chev'))).map(n => n.textContent).join('').trim(); }
function openSubs(root){
 root.querySelectorAll('details.sub').forEach(d => {
  const summary = d.querySelector(':scope>summary'), body = d.querySelector(':scope>.subbody');
  const sec = document.createElement('section'); sec.className = 'mlk-card mlp-card mlp-sub';
  const h = document.createElement('h3'); h.className = 'mlp-subhead'; h.textContent = summary ? subTitle(summary) : '';
  sec.append(h); if (body){ body.classList.add('mlp-subbody'); sec.append(body); }
  d.replaceWith(sec);
 });
}
function card(title, nodes, extraClass){
 const sec = document.createElement('section'); sec.className = 'mlk-card mlp-card' + (extraClass ? ' ' + extraClass : '');
 if (title){ const h = document.createElement('h3'); h.className = 'mlp-subhead'; h.textContent = title; sec.append(h); }
 for (const n of nodes) if (n) sec.append(n);
 return sec;
}
function sectionBody(body, key){ const s = body.querySelector(`:scope>details.sec[data-key="${key}"]`); return s ? s.querySelector(':scope>.body') : null; }
// The 'who' group: the approach brief (bare, eyebrow + box) followed by five sub-folds.
function profileGroup(who){
 const out = [];
 if (!who) return out;
 const loose = [];
 for (const n of [...who.childNodes]){
  if (n.nodeType === 1 && n.matches('details.sub')) break;
  loose.push(n);
 }
 const eyebrow = loose.find(n => n.nodeType === 1 && n.classList.contains('eyebrow'));
 if (loose.some(n => n.nodeType === 1)){
  const title = eyebrow ? eyebrow.textContent.trim() : '';
  if (eyebrow) eyebrow.remove();
  const box = document.createElement('div'); box.className = 'mlp-subbody';
  loose.filter(n => n !== eyebrow).forEach(n => box.append(n));
  out.push(card(title, [box], 'mlp-sub'));
 }
 openSubs(who);
 out.push(...who.querySelectorAll(':scope>.mlp-sub'));
 return out;
}
function rawScoresCard(m){
 if (!HONORS) return null;
 const sec = document.createElement('section'); sec.className = 'mlk-card mlp-card mlp-raw';
 sec.innerHTML = '<h3 class="mlp-subhead">Per-area scores</h3><p class="mlp-muted">Raw action score and tensor status for each legislative area, with the cutoffs of the cohort it is ranked in. Open an area for its recorded actions and their contributions.</p>'
  + '<div class="mlk-tablecard"><table class="mlk-table mlp-rawtable"><thead><tr><th scope="col">Legislative area</th><th scope="col">Recognition</th><th scope="col" class="mlk-num">Raw action score</th><th scope="col">Tensor</th><th scope="col">Cohort</th></tr></thead><tbody>'
  + CATS.map((cat, ix) => {
   const row = fieldRow(m, cat);
   if (!row) return `<tr><td>${esc(cat)}</td><td colspan="4" class="mlp-muted">Record unavailable</td></tr>`;
   let facts = ''; try { facts = honorFacts(m, cat); } catch (e) { facts = ''; }
   let label = ''; try { label = honorLabel(m, cat); } catch (e) { label = row.recognition || ''; }
   const cohort = row.sub_cohort ? `Ranked among this field's ${row.sub_cohort === 'positive' ? 'supportive' : row.sub_cohort === 'negative' ? 'opposing' : 'net-neutral'} records` : `${row.cohort_size} members with recorded action`;
   let ledger = ''; try { ledger = actionLedger(m, cat); } catch (e) { ledger = ''; }
   return `<tr><td><button type="button" class="mlp-rawtoggle" aria-expanded="false" aria-controls="mlp-raw-${ix}">${esc(cat)}</button></td><td>${esc(label)}</td><td class="mlk-num">${esc(row.raw_field_score ?? '—')}</td><td>${esc(row.tensor_status ?? '—')}</td><td>${esc(cohort)}</td></tr>`
    + `<tr class="mlp-rawdetail" id="mlp-raw-${ix}" hidden><td colspan="5"><p>${esc(facts)}</p>${ledger}<p class="mlp-muted">Generation ${esc(HONORS.generation_id)} · ${esc(m.honors && m.honors.member_id)} · ${esc(cat)}</p></td></tr>`;
  }).join('') + '</tbody></table></div>';
 sec.addEventListener('click', e => {
  const b = e.target.closest('.mlp-rawtoggle'); if (!b) return;
  const open = b.getAttribute('aria-expanded') !== 'true', row = document.getElementById(b.getAttribute('aria-controls'));
  b.setAttribute('aria-expanded', String(open)); if (row) row.hidden = !open;
 });
 return sec;
}

/* ---------------- scores out of the reading tabs ---------------- */
// Prompt Don't-copy: no raw decimal scores outside Sources & method. The moved Profile and Access groups keep their
// qualitative reads (percentiles, bands, names, counts, durations). Each score or measured value they carried is
// copied with its label into 'Trait and access measures' on Sources & method, then removed from the moved node.
// Matching is on today's labels and table headers (original-desk.html committeeHTML, psychHTML, speechHTML,
// respHTML, relHTML); an unmatched label is left as it is.
const txt = n => (n ? n.textContent : '').replace(/\s+/g, ' ').trim();
const SCORE_ROWS = /^(Ideology, left to right|Bill advancement|Record-based|Presentation-based|Typical pitch)/;
const SCORE_COLS = new Set(['Value', 'Standardized value (z)', 'Pitch', 'Pitch var.', 'Volume dyn.']);
const SCORE_BLOCKS = /^(Within-member setting contrasts|Observed subject reactions)/;
const LEAD_NUM = /^[-+−]?\d*\.\d+(?:\/sec| sec)?\s*·\s*(.+)$/;     // '2.13 · 93th pct' -> '93th pct'
const NET = /^[-+−]?(?:\d*\.\d+|\d+)$/;
// An eyebrow's own words, without its muted qualifier span ('Interview-only detail', not '... · House floor excluded').
const ownText = e => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').replace(/\s+/g, ' ').trim();
function eyebrowOf(el){
 for (let n = el.previousElementSibling; n; n = n.previousElementSibling) if (n.classList.contains('eyebrow')) return ownText(n);
 return '';
}
function mrow(label, value){
 const r = document.createElement('div'); r.className = 'mrow';
 const s = document.createElement('span'), b = document.createElement('b'); s.textContent = label; b.textContent = value;
 r.append(s, b); return r;
}
// A line made only of text and <b> values; values are carried as \x01value\x02 so clause matching never splits one.
const CUE = /([A-Za-z][^.;:\x01\x02]*?\bcues):\s*\x01([-+−]?\d*\.?\d+)\x02([^;.\x01]*)(;\s*|(?=\.))/g;
function scrubCueClauses(d){
 if (!/\bcues:/i.test(d.textContent) || [...d.children].some(c => c.tagName !== 'B' || c.children.length)) return [];
 const flat = [...d.childNodes].map(n => n.nodeType === 1 ? '\x01' + n.textContent + '\x02' : n.nodeType === 3 ? n.textContent : '').join('');
 const rows = [], starts = [];
 let removed = 0;
 let out = flat.replace(CUE, (all, label, value, unit, sep, at, whole) => {
  rows.push([label.charAt(0).toUpperCase() + label.slice(1), value + unit.trim()]);
  if (/(^|\.\s*)$/.test(whole.slice(0, at))) starts.push(at - removed);   // the clause began a sentence: capitalise what follows
  removed += all.length;
  return '';
 });
 if (!rows.length) return [];
 starts.forEach(k => { if (k < out.length) out = out.slice(0, k) + out.charAt(k).toUpperCase() + out.slice(k + 1); });
 out = out.replace(/;\s*(?=\.)/g, '').replace(/\s+\./g, '.').replace(/^\s+|\s+$/g, '');
 if (!/[A-Za-z0-9]/.test(out.replace(/[\x01\x02]/g, ''))){ d.remove(); return rows; }
 const parts = out.split(/\x01([^\x02]*)\x02/);
 d.replaceChildren(...parts.map((p, k) => { if (k % 2 === 0) return document.createTextNode(p); const b = document.createElement('b'); b.textContent = p; return b; }));
 return rows;
}
function scrubScores(sec){
 const taken = [];
 const take = (el, sub) => taken.push({sub: sub ?? eyebrowOf(el), node: el.cloneNode(true)});
 // Committee positions: '· committee score 0.45' at the end of the seats eyebrow.
 sec.querySelectorAll('.eyebrow span').forEach(s => {
  const mm = s.textContent.match(/\s*·\s*committee score\s+(\S+)\s*$/i);
  if (!mm) return;
  if (mm[1] !== '—') taken.push({sub: '', node: mrow('Committee score', mm[1])});
  s.textContent = s.textContent.slice(0, mm.index);
 });
 // Blocks that are only measured differences: setting contrasts, subject reactions.
 sec.querySelectorAll('.eyebrow').forEach(e => {
  if (!SCORE_BLOCKS.test(txt(e))) return;
  const sub = ownText(e), rows = [];
  for (let n = e.nextElementSibling; n && n.classList.contains('mrow'); n = n.nextElementSibling) rows.push(n);
  rows.forEach(r => { take(r, sub); r.remove(); });
  e.remove();
 });
 // Label-value rows: score-only rows go; 'value · percentile' keeps the percentile.
 sec.querySelectorAll('.mrow').forEach(r => {
  const b = r.querySelector(':scope>b'); if (!b) return;
  if (SCORE_ROWS.test(txt(r.firstElementChild))){ take(r); r.remove(); return; }
  const mm = txt(b).match(LEAD_NUM);
  if (mm){ take(r); b.textContent = mm[1]; }
 });
 // Tables: the value columns go; a table left with only its label column goes whole.
 sec.querySelectorAll('table.tbl').forEach(t => {
  const drop = [...t.querySelectorAll('thead th')].map(th => SCORE_COLS.has(txt(th)));
  if (!drop.some(Boolean)) return;
  const box = t.parentElement && t.parentElement.children.length === 1 && t.parentElement.tagName === 'DIV' ? t.parentElement : t;
  take(box);
  if (drop.filter(x => !x).length <= 1){ box.remove(); return; }
  t.querySelectorAll('tr').forEach(tr => [...tr.children].forEach((c, k) => { if (drop[k]) c.remove(); }));
 });
 // Partner rows: '#50 · 6.32' keeps the rank; a cross-media net tone '+0.67' becomes its word (legacy +/-0.15 cut).
 sec.querySelectorAll('.partner').forEach(p => {
  const cells = [...p.children], last = cells[cells.length - 1];
  const mm = txt(last).match(/^(#\S+)\s*·\s*[-+−]?[\d.]+$/);
  if (mm){ take(p); last.textContent = mm[1]; return; }
  const net = cells.find((c, k) => k > 0 && NET.test(txt(c)));
  if (net){
   take(p);
   const v = Number(txt(net).replace('−', '-'));
   net.textContent = v > 0.15 ? 'Positive' : v < -0.15 ? 'Negative' : 'Neutral';
  }
 });
 // Measured cue rates inside a prose line (speechHTML's interview-text line: 'Warmth cues: <b>3.009</b>/1,000 words;
 // positive-energy/excitement cues: <b>0.722</b>/1,000; ...'): each 'cues:' clause goes, as a label-value row; the rest
 // of the line (word counts, shares) stays.
 sec.querySelectorAll('div').forEach(d => { const rows = scrubCueClauses(d); rows.forEach(([label, value]) => taken.push({sub: eyebrowOf(d), node: mrow(label, value)})); });
 if (taken.length){
  const p = document.createElement('p'); p.className = 'mlp-muted mlp-measures-note';
  p.innerHTML = 'Scores and measured values for this section are in <button type="button" class="mlp-linkbtn" data-goto-tab="sources">Sources &amp; method</button>.';
  const h = sec.querySelector(':scope>.mlp-subhead');
  if (h) h.after(p); else sec.prepend(p);
 }
 return taken;
}
function measuresCard(groups){
 const list = groups.filter(g => g.taken.length);
 if (!list.length) return null;
 const sec = document.createElement('section'); sec.className = 'mlk-card mlp-card mlp-measures';
 sec.innerHTML = '<h3 class="mlp-subhead">Trait and access measures</h3><p class="mlp-muted">The scores and measured values behind the Profile sections on Overview and the Access tab, with today\'s labels. Those tabs show the qualitative reads.</p>';
 for (const g of list){
  const box = document.createElement('div'); box.className = 'mlp-measures__group';
  const h = document.createElement('h4'); h.className = 'mlp-measures__head'; h.textContent = `${g.title} · ${g.tab}`;
  box.append(h);
  let sub = null;
  for (const t of g.taken){
   if (t.sub && t.sub !== sub){ const e = document.createElement('div'); e.className = 'eyebrow'; e.textContent = t.sub; box.append(e); }
   sub = t.sub; box.append(t.node);
  }
  sec.append(box);
 }
 return sec;
}

/* ---------------- tabs ---------------- */
function tabKey(key){ key = TAB_ALIAS[key] || key; return TABS.some(([k]) => k === key) ? key : 'overview'; }
function selectTab(root, key, {update = true, focus = false} = {}){
 key = tabKey(key);
 root.querySelectorAll('[data-dossier-panel]').forEach(p => { p.hidden = p.dataset.dossierPanel !== key; });
 root.querySelectorAll('[data-dossier-tab]').forEach(b => { const on = b.dataset.dossierTab === key; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; if (on && focus) b.focus(); });
 if (!update) return;
 try {
  if (!window.frameElement || !window.frameElement.isConnected) return;         // a replaced frame leaves the address alone
  const p = new URLSearchParams(parent.location.search);
  if (p.get('view') !== 'profile') return;
  p.set('tab', key); parent.history.replaceState(parent.history.state, '', '/?' + p);
 } catch (e) { /* opened on its own: nothing to update */ }
}
function tabsHTML(m){
 const counts = {bills: (m.bills || []).length, coverage: (m.articles || []).length};
 // The legacy group title carries '*' when the district was redrawn; the tab keeps that mark.
 const redrawn = !!(m.industry && m.industry.redrawn);
 return '<div class="mlp-tabbar"><div class="mlk-tabs mlp-tabs" role="tablist" aria-label="Member profile sections">' + TABS.map(([k, l]) =>
  `<button type="button" role="tab" id="mlp-tab-${k}" aria-controls="mlp-panel-${k}" aria-selected="false" tabindex="-1" data-dossier-tab="${k}">${esc(l)}${k === 'district' && redrawn ? '*' : ''}${k in counts ? ` <span class="mlp-count" aria-label="${esc(plural(counts[k], k === 'bills' ? 'bill' : 'item'))}">${esc(counts[k].toLocaleString('en-US'))}</span>` : ''}</button>`).join('') + '</div></div>';
}

/* ---------------- mount ---------------- */
function mount(i, ctx = {}){
 const m = M[i];
 const body = document.getElementById('memberbody');
 if (!m || !body) throw Error('Member record could not load.');
 const legacy = document.createElement('div'); legacy.className = 'mlp-legacy'; legacy.hidden = true;
 const nodes = [...body.children];
 const find = pred => nodes.find(pred);
 const mhead = find(n => n.classList.contains('mhead'));
 const gen = find(n => n.classList.contains('gen'));
 const evCard = find(n => n.classList.contains('card') && /Electoral history/.test((n.querySelector('.eyebrow') || n).textContent));
 const conduct = find(n => n.classList.contains('card') && /Primacy conduct ledger/.test((n.querySelector('.eyebrow') || n).textContent));
 const trackcard = document.getElementById('trackcard');
 const who = sectionBody(body, 'who'), reach = sectionBody(body, 'reach'), district = sectionBody(body, 'district'), money_ = sectionBody(body, 'money'), method = sectionBody(body, 'method');
 const radarCard = gen && gen.children[0], recogCard = gen && gen.children[1];

 const root = document.createElement('div');
 root.className = 'mlp';
 const id = (m.bio && m.bio.bioguide) || '';
 root.innerHTML = `<div class="mlp-bar"><nav class="mlp-crumbs" aria-label="Breadcrumb"><a class="mlp-back" data-mlk-nav href="${esc(returnHref())}">${ICON_BACK}<span>Members</span></a>`
  + `<span class="mlp-trail">Member desk<span class="mlp-slash" aria-hidden="true">/</span><span aria-current="page">${esc(m.name)}</span></span></nav>`
  + `<div class="mlp-actions"><button type="button" class="mlk-btn mlp-pin" aria-pressed="false"${id ? '' : ' disabled title="No member ID on record"'}>Pin to compare</button>`
  + `<button type="button" class="mlk-btn mlp-print">${ICON_PRINT}<span>Print</span></button>`
  + `<a class="mlp-maplink" data-mlk-nav href="${esc(shellUrl('influence', {member: m.name}))}">Show on map</a></div></div>`
  + headHTML(m) + figuresHTML(m) + tabsHTML(m)
  + TABS.map(([k]) => `<div class="mlp-panel" role="tabpanel" id="mlp-panel-${k}" aria-labelledby="mlp-tab-${k}" tabindex="0" data-dossier-panel="${k}" hidden></div>`).join('');
 const panel = k => root.querySelector(`#mlp-panel-${k}`);

 // Overview: stance list + pillars/engagement, then the old Profile group as open sub-sections.
 const ov = document.createElement('div'); ov.className = 'mlp-ov';
 const side = document.createElement('div'); side.className = 'mlp-side';
 side.append(pillarCard(m, radarCard), engagementCard(m, trackcard));
 ov.append(stanceCard(m), side);
 const prof = document.createElement('div'); prof.className = 'mlp-stack mlp-profile';
 prof.append(...profileGroup(who));
 panel('overview').append(ov, prof);
 // Scores in the moved groups go to Sources & method (see scrubScores).
 const measures = [];
 const scrubAll = (els, tab) => els.forEach(s => { if (s.classList.contains('mlp-sub')) measures.push({title: txt(s.querySelector(':scope>.mlp-subhead')), tab, taken: scrubScores(s)}); });
 scrubAll([...prof.children], 'Overview');

 // Bills: one-line table with role and area filters.
 panel('bills').append(billsPanel(m));

 // Access, District, Press & coverage: today's content, moved; sub-folds open as sub-sections.
 const stack = (k, els) => { const s = document.createElement('div'); s.className = 'mlp-stack'; els.filter(Boolean).forEach(e => s.append(e)); panel(k).append(s); };
 if (reach){ openSubs(reach); scrubAll([...reach.children], 'Access'); stack('access', [...reach.children]); } else stack('access', [card('', [Object.assign(document.createElement('p'), {className: 'mlk-state', textContent: 'No access records for this member.'})])]);
 if (evCard){ evCard.classList.add('mlp-card'); evCard.removeAttribute('style'); }
 stack('district', [evCard, district ? card('', [district], 'mlp-district') : null]);
 stack('coverage', [money_ ? card('', [money_], 'mlp-press') : null]);

 // Sources & method: the method section, the classification evidence and recognition policy, the raw per-area
 // scores, the analytical field signals radar (moved from the top of the page) and the conduct ledger.
 const src = [];
 if (method) src.push(card('Sources & method', [method], 'mlp-method'));
 const evidence = mhead && mhead.querySelector('details');
 const policy = recogCard && [...recogCard.children].find(n => !n.classList.contains('eyebrow') && !n.classList.contains('arealist'));
 if (evidence || policy){
  if (evidence){ evidence.open = true; evidence.removeAttribute('style'); evidence.classList.add('mlp-evidence'); }
  if (policy){ policy.removeAttribute('style'); policy.classList.add('mlp-policy'); }
  src.push(card('Classification and recognition', [evidence, policy]));
 }
 src.push(rawScoresCard(m), measuresCard(measures));
 if (radarCard){
  const eyebrow = radarCard.querySelector('.eyebrow'), svg = radarCard.querySelector('svg');
  const note = [...radarCard.children].find(n => n.tagName === 'DIV' && /Model signals combine/.test(n.textContent));
  if (svg){ const fig = document.createElement('div'); fig.className = 'mlp-radar'; fig.append(svg); if (note){ note.removeAttribute('style'); note.className = 'mlp-note'; fig.append(note); } src.push(card(eyebrow ? eyebrow.textContent.trim() : 'Analytical field signals', [fig])); }
 }
 if (conduct){ conduct.classList.add('mlp-card'); conduct.removeAttribute('style'); src.push(conduct); }
 stack('sources', src);

 // Everything not placed stays in the document (hidden), so legacy handlers that look nodes up still find them.
 nodes.forEach(n => { if (!root.contains(n) && n.isConnected) legacy.append(n); });
 body.replaceChildren(root, legacy);

 // Actions
 const pin = root.querySelector('.mlp-pin');
 const paintPin = () => { const on = !!id && !!kit() && kit().compare.has(id); pin.setAttribute('aria-pressed', String(on)); pin.textContent = on ? 'Pinned' : 'Pin to compare'; };
 if (id && kit()){ pin.addEventListener('click', () => { kit().compare.toggle(id); paintPin(); }); kit().compare.subscribe(paintPin); }
 paintPin();
 root.querySelector('.mlp-print').addEventListener('click', () => {
  const legacyPrint = document.getElementById('printbtn');
  if (legacyPrint && typeof legacyPrint.onclick === 'function') legacyPrint.onclick(); else window.print();
 });

 // Tabs: click, arrows / Home / End (selection follows focus).
 const tablist = root.querySelector('[role=tablist]');
 tablist.addEventListener('click', e => { const b = e.target.closest('[data-dossier-tab]'); if (b) selectTab(root, b.dataset.dossierTab); });
 tablist.addEventListener('keydown', e => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const buttons = [...tablist.querySelectorAll('[role=tab]')], i0 = buttons.indexOf(document.activeElement);
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : (Math.max(0, i0) + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
  selectTab(root, buttons[next].dataset.dossierTab, {focus: true});
 });
 // 'Sources & method' links in the scrubbed sections open that tab at the measures card.
 root.addEventListener('click', e => {
  const g = e.target.closest('[data-goto-tab]'); if (!g) return;
  selectTab(root, g.dataset.gotoTab, {focus: true});
  const c = root.querySelector('.mlp-measures'); if (c) c.scrollIntoView({block: 'start'});
 });
 const q = ctx.query || new URLSearchParams(location.search);
 selectTab(root, q.get('tab') || 'overview', {update: false});
 return root;
}

// Unknown member: the board's empty state.
function unknown(container){
 container.innerHTML = `<div class="mlp mlp-unknown"><div class="mlk-state" role="status"><p class="mlp-unknown__title">No member matches this link</p>`
  + `<a class="mlk-btn" data-mlk-nav href="${esc(returnHref())}">${ICON_BACK}<span>Members</span></a></div></div>`;
}

window.MelangeProfile = {mount, unknown, selectTab, tabKey, TABS, RETURN_KEY};
})();
