/* desk-coalition.js: overlay for original-desk.html (Member desk > Coalition builder, board #35); owner: prompt 07. Runs after the desk engine and ui-kit.js, before restored-desk.js. */
/* Build 07 (board #35). The legacy coalition engine stays in charge: the in-memory set `coal`, cbArea / cbParty /
   cbCommittee / cbBasis, coverage(), suggest(), reachEstimate(), the snapshot store (localStorage mdesk_coal_snapshots_v1)
   and every legacy handler. This file:
   1. MOVES the live legacy controls (#cbarea, #cbparty, #cbcommittee, the #cbbasis ranking segment, #cbsave, #cbclear and
      the Saved snapshots card) into the board's title row, control bar and layout; their listeners come with them;
   2. wraps renderCoal and suggest by name: the legacy render still runs (into its now hidden containers) and computes
      coverage once; the suggest() tuple it produced [member, score, gain, already, cosn, dtie, lift, sel, selc] is then
      painted as the board's chips, stat rows and suggestions table. SCORE is the legacy relative strength
      ((s-lo)/mx*100, the same formula as the legacy row), so no number is recomputed differently;
   3. adds the "Add a cosponsor" combobox (adds by member index, then the legacy renderCoal), per-row Add buttons, chip x
      buttons, and the "View in influence map" handoff (the legacy #cbnet handler, with window.gotoNetworkCoalition
      defined here so it routes through the shell instead of the 404 'Moneyball Dashboard.html?coal=' window);
   4. shows loading (skeleton while desk-support.json loads; restored-desk.js's status line moves into the view),
      empty ("Add cosponsors to see suggestions") and error (message + Retry) states;
   5. asks for the snapshot name in a kit popover (role=dialog, Escape closes) anchored to #cbsave instead of the native
      prompt(); Save runs the legacy #cbsave onclick with prompt() answering the typed name, so the snapshot record,
      the store and the list re-render stay the legacy code's own.
   Data colours (party dots, the score green) are taken by reference: PTCOL from the desk, and the legacy .nextscore class. */
(() => {
'use strict';
const Q = new URLSearchParams(location.search);
if ((Q.get('deskview') || 'ranked') !== 'coalition') return;
const section = document.getElementById('view-coalition');
if (!section || typeof renderCoal !== 'function' || typeof suggest !== 'function' || typeof reachEstimate !== 'function' || typeof coal === 'undefined') return;
const K = window.MLK || null;
const $id = id => document.getElementById(id);
const esc = v => K && K.esc ? K.esc(v) : (v == null ? '' : String(v)).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const icon = name => (K && K.icon ? K.icon(name) : '');
const EMPTY_LINE = 'Add cosponsors to see suggestions';

/* ---------------- data helpers (the legacy globals, read by name at call time: snapshot Load reassigns `coal`) ---------------- */
const members = () => (typeof M !== 'undefined' ? M : []);
const currentSet = () => coal;
function partyColour(party){
 const map = typeof PTCOL !== 'undefined' ? PTCOL : null;
 if (map) return map[party] || map[''] || '';
 return K && K.partyColor ? K.partyColor(party) : '';
}
const dotHtml = party => { const c = partyColour(party); return `<span class="mlk-dot dcb-dot" aria-hidden="true"${c ? ` style="--mlk-dot:${esc(c)}"` : ''}></span>`; };
function profileHref(name){
 return K && K.url ? K.url('profile', {m: name, tab: 'overview'}) : '/?view=profile&m=' + encodeURIComponent(name) + '&tab=overview';
}
const areaWord = () => esc(String(typeof cbArea !== 'undefined' ? cbArea : '').split(' ')[0]);

/* ---------------- 1. layout: build the board's structure and move the live legacy controls into it ---------------- */
const legacyTitle = section.querySelector(':scope > h2');
const legacyNote = legacyTitle && legacyTitle.nextElementSibling && legacyTitle.nextElementSibling.tagName === 'DIV' ? legacyTitle.nextElementSibling : null;
const legacyBar = section.querySelector(':scope > .areasel');
const legacyGrid = section.querySelector(':scope > .cbgrid');
const snapList = $id('cbsnapshots');
const snapCard = snapList ? snapList.closest('.card') : null;

const root = document.createElement('div');
root.className = 'dcb';
root.id = 'dcb';
root.innerHTML = `
<div class="dcb-head">
 <h2 class="dcb-title" id="dcb-title">Coalition builder</h2>
 <div class="mlk-anchor dcb-howto">
  <button type="button" class="mlk-info dcb-info" id="dcb-info" data-mlk-toggle="dcb-howto" aria-controls="dcb-howto" aria-expanded="false" aria-label="How to read this">${icon('info')}</button>
  <div class="mlk-popover dcb-howto__pop" id="dcb-howto" role="dialog" aria-label="How to read this" tabindex="-1" hidden></div>
 </div>
 <div class="dcb-actions" id="dcb-actions"></div>
</div>
<div class="dcb-controls" id="dcb-controls" role="group" aria-label="Coalition controls">
 <div class="mlk-search dcb-add">
  <input class="mlk-search__input dcb-add__input" id="dcb-add" type="text" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="dcb-addres" aria-label="Add a cosponsor" placeholder="Add a cosponsor" autocomplete="off" spellcheck="false">
  <button type="button" class="dcb-add__chev" id="dcb-addchev" tabindex="-1" aria-hidden="true">${icon('chevron')}</button>
  <div class="mlk-results dcb-results" id="dcb-addres" role="listbox" aria-label="Members" hidden></div>
 </div>
 <span class="dcb-sep" aria-hidden="true"></span>
 <span class="mlk-seg__label dcb-rankby" id="dcb-rankby">Rank by</span>
</div>
<div class="dcb-status" id="dcb-status"></div>
<div class="dcb-grid" id="dcb-grid">
 <section class="dcb-card dcb-left" aria-labelledby="dcb-cos-label">
  <div class="dcb-card__head"><h3 class="dcb-card__label" id="dcb-cos-label">Cosponsors<span id="dcb-count"></span></h3></div>
  <div class="dcb-card__body" id="dcb-left"></div>
 </section>
 <section class="dcb-card dcb-right" aria-labelledby="dcb-sugg-label">
  <div class="dcb-card__head"><h3 class="dcb-card__label" id="dcb-sugg-label">Suggested next cosponsors</h3></div>
  <div class="dcb-card__body" id="dcb-right"></div>
 </section>
</div>`;
section.prepend(root);

// the three-sentence explanation, verbatim, behind the circled i
const howto = $id('dcb-howto');
if (legacyNote){ legacyNote.removeAttribute('style'); legacyNote.classList.add('dcb-howto__text'); howto.append(legacyNote); }

// dropdowns: the legacy selects keep their onchange handlers; only the first option's words follow the board
const controls = $id('dcb-controls'), sep = controls.querySelector('.dcb-sep');
[['cbarea', 'Area bias:', 'None', 'area'], ['cbparty', 'Party:', 'All', 'party'], ['cbcommittee', 'Committee:', 'All', 'committee']].forEach(([id, label, first, key]) => {
 const sel = $id(id);
 if (!sel) return;
 const wrap = document.createElement('label');
 wrap.className = 'mlk-select dcb-sel dcb-sel--' + key;
 wrap.innerHTML = `<span class="mlk-select__label">${esc(label)}</span>`;
 if (sel.options[0] && sel.options[0].value === '') sel.options[0].textContent = first;
 wrap.append(sel);
 sep.before(wrap);
});
// ranking basis: the legacy segment (buttons keep their onclick); kit look via aria-pressed
const basis = $id('cbbasis');
if (basis){
 basis.className = 'mlk-seg dcb-seg';
 basis.setAttribute('role', 'group');
 basis.removeAttribute('aria-label');
 basis.setAttribute('aria-labelledby', 'dcb-rankby');
 basis.querySelectorAll('button').forEach(b => { b.type = 'button'; });
 const syncBasis = () => basis.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b.classList.contains('on') ? 'true' : 'false'));
 syncBasis();
 basis.addEventListener('click', () => syncBasis());
 new MutationObserver(syncBasis).observe(basis, {subtree: true, attributes: true, attributeFilter: ['class']});
 controls.append(basis);
}
// title-row actions: legacy Save snapshot and Clear (their onclick stays), then the map handoff
const actions = $id('dcb-actions');
const save = $id('cbsave'), clear = $id('cbclear');
let saveAnchor = null, savePop = null, saveName = null, saveGo = null, saveMsg = null;
if (save){
 save.className = 'mlk-btn dcb-btn'; save.type = 'button';
 saveAnchor = document.createElement('span');
 saveAnchor.className = 'mlk-anchor dcb-saveanchor';
 actions.append(saveAnchor);
 saveAnchor.append(save);
 if (K && K.open && K.close && K.isOpen){
  save.setAttribute('aria-haspopup', 'dialog');
  save.setAttribute('aria-expanded', 'false');
  save.setAttribute('aria-controls', 'dcb-savepop');
  saveAnchor.insertAdjacentHTML('beforeend', `<div class="mlk-popover mlk-popover--end dcb-savepop" id="dcb-savepop" role="dialog" aria-labelledby="dcb-savepop-label" tabindex="-1" hidden style="width:300px">
 <label class="mlk-label" id="dcb-savepop-label" for="dcb-savename">Snapshot name</label>
 <input class="mlk-search__input" id="dcb-savename" type="text" autocomplete="off" spellcheck="false" maxlength="120" aria-describedby="dcb-savemsg" style="margin-top:8px;padding:0 12px">
 <p id="dcb-savemsg" role="alert" style="margin:8px 0 0;color:var(--ml-ink);font:400 12px/1.4 var(--sans)" hidden></p>
 <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px">
  <button type="button" class="mlk-btn mlk-btn--text" id="dcb-savecancel">Cancel</button>
  <button type="button" class="mlk-btn" id="dcb-savego" disabled aria-disabled="true">Save</button>
 </div>
</div>`);
  savePop = $id('dcb-savepop'); saveName = $id('dcb-savename'); saveGo = $id('dcb-savego'); saveMsg = $id('dcb-savemsg');
 }
}
if (clear){ clear.className = 'mlk-btn mlk-btn--text dcb-btn'; clear.type = 'button'; actions.append(clear); }
const mapBtn = document.createElement('button');
mapBtn.type = 'button';
mapBtn.className = 'mlk-btn mlk-btn--primary dcb-btn';
mapBtn.id = 'dcb-map';
mapBtn.textContent = 'View in influence map';
actions.append(mapBtn);
mapBtn.addEventListener('click', () => {
 if (!currentSet().size) return;
 const legacy = $id('cbnet');
 if (legacy) legacy.click(); else window.gotoNetworkCoalition([...currentSet()].map(i => members()[i].name));
});
// The legacy handler calls window.gotoNetworkCoalition(names) when it exists; in the v2 frame it did not, so the legacy
// fallback opened 'Moneyball Dashboard.html?coal=' (404 here). Route through the shell with the coalition in ?coal=
// (network.html reads ?coal=Name1,Name2).
if (typeof window.gotoNetworkCoalition !== 'function'){
 window.gotoNetworkCoalition = names => {
  const coalParam = (names || []).join(',');
  if (K && K.navigate && K.url) K.navigate(K.url('influence', {coal: coalParam}));
  else { try { window.top.location.href = '/?view=influence&coal=' + encodeURIComponent(coalParam); } catch (e) { location.href = '/?view=influence&coal=' + encodeURIComponent(coalParam); } }
 };
}
// saved snapshots: the legacy card (Load / Delete stay on the legacy delegated handler) below the two cards
if (snapCard){ snapCard.className = 'dcb-card dcb-snaps'; section.append(snapCard); }
// everything left of the legacy layout is kept in the DOM (the legacy render still writes into it) but hidden
[legacyTitle, legacyBar, legacyGrid].forEach(n => { if (n) n.classList.add('dcb-legacy'); });
if (K && K.head && K.head.setInfo){ try { K.head.setInfo(null); } catch (e) { /* head not available */ } }

/* ---------------- 2. rendering ---------------- */
const left = $id('dcb-left'), right = $id('dcb-right'), count = $id('dcb-count'), grid = $id('dcb-grid'), status = $id('dcb-status');
let last = null;           // {cov, rows} from the most recent legacy suggest() call
let loaded = false;        // the legacy renderCoal has run at least once
let refocus = null;        // focus target to restore after a repaint

function skeleton(){
 grid.setAttribute('aria-busy', 'true');
 count.textContent = '';
 left.innerHTML = '<div class="dcb-skelchips">' + '<span class="mlk-skel"></span>'.repeat(4) + '</div>'
  + '<div class="dcb-stats dcb-stats--skel">' + '<div class="dcb-stat"><span class="mlk-skel"></span></div>'.repeat(3) + '</div>';
 right.innerHTML = '<div class="dcb-skelhead"></div>' + '<div class="mlk-skel-row dcb-skelrow"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div>'.repeat(7);
}
function stateLine(text, error){
 return `<p class="mlk-state dcb-state${error ? ' mlk-state--error' : ''}"${error ? ' role="alert"' : ''}>${esc(text)}</p>`;
}
function syncButtons(){
 const has = currentSet().size > 0;
 [save, clear, mapBtn].forEach(b => { if (b){ b.disabled = !has; b.setAttribute('aria-disabled', has ? 'false' : 'true'); } });
 if (!has && savePop && K.isOpen(savePop)) K.close(savePop, {restoreFocus: false});
}

function paintLeft(cov){
 const set = currentSet(), list = [...set].map(i => [i, members()[i]]).filter(([, m]) => m);
 count.textContent = set.size ? ` · ${set.size}` : '';
 if (!set.size){ left.innerHTML = stateLine(EMPTY_LINE); return; }
 const chips = list.map(([i, m]) => `<span class="mlk-chip mlk-chip--removable dcb-chip" role="listitem">${dotHtml(m.party)}<span class="mlk-chip__label">${esc(m.name)}</span><button type="button" class="mlk-close" data-dcb-remove="${i}" aria-label="Remove ${esc(m.name)} from the coalition">${icon('close')}</button></span>`).join('');
 const dd = list.filter(([, m]) => m.party === 'Democrat').length, rr = list.filter(([, m]) => m.party === 'Republican').length, ii = set.size - dd - rr;
 let reach = '';
 try { reach = `~${reachEstimate(cov || undefined).toFixed(0)} members`; } catch (e) { reach = '—'; }
 const area = typeof cbArea !== 'undefined' ? cbArea : '';
 const dom = area && typeof domainOf === 'function' ? domainOf(area) : null;
 const sens = dom ? `<div class="dcb-stat"><dt>${esc(area)} sensitivity</dt><dd>IC ×${Number(dom.ic_edge_scale || 1).toFixed(2)} · LT ×${Number(dom.lt_threshold_scale || 1).toFixed(2)}<small class="dcb-stat__note">LT context only; ranking is marginal reach</small></dd></div>` : '';
 left.innerHTML = `<div class="dcb-chips" role="list" aria-label="Cosponsors">${chips}</div>
<dl class="dcb-stats">
 <div class="dcb-stat"><dt>Coalition size</dt><dd>${set.size}</dd></div>
 <div class="dcb-stat"><dt title="Party split (D · R · I)">Party split</dt><dd class="dcb-split">${dd} D<span class="dcb-split__sep"> · </span>${rr} R<span class="dcb-split__sep"> · </span>${ii} I</dd></div>
 <div class="dcb-stat"><dt title="Expected issue-adaptive reach">Expected reach</dt><dd>${esc(reach)}</dd></div>${sens}
</dl>`;
}

function detailBits(i, m, lift, sel, selc){
 // the legacy row's area-dependent evidence (role, area score, signed-of-eligible, concentration), same wording
 const bits = [], area = typeof cbArea !== 'undefined' ? cbArea : '';
 try { if (typeof roleCodeAt === 'function' && roleCodeAt(i, area) !== '-' && typeof rolePriorAt === 'function') bits.push(esc(rolePriorAt(i, area).label)); } catch (e) { /* no role data */ }
 if (area && typeof CATS !== 'undefined' && typeof f2 === 'function' && m.radar) bits.push(`${esc(f2(m.radar[CATS.indexOf(area)]))} ${areaWord()}`);
 if (selc) bits.push(`signed ${esc(selc[0])} of ${esc(selc[1])} eligible ${areaWord()} bills${sel != null ? ` (sel ${Number(sel).toFixed(2)})` : ''}`);
 if (lift != null) bits.push(`${lift >= 1.15 ? 'concentrates' : lift <= 0.85 ? 'spread across issues' : 'broad'} on ${areaWord()} (${Number(lift).toFixed(1)}×)`);
 return bits;
}

function paintRight(rows){
 if (!currentSet().size){ right.innerHTML = stateLine(EMPTY_LINE); return; }
 if (!rows.length){ right.innerHTML = stateLine('No eligible members remain under the current party and committee filters.'); return; }
 // the legacy strength scale: relative to the best candidate on the basis showing (shifted by the minimum when signed)
 const lo = Math.min(0, ...rows.map(s => s[1])), mx = Math.max(0.001, ...rows.map(s => s[1] - lo));
 const body = rows.map(([i, s, gain, already, cosn, dtie, lift, sel, selc]) => {
  const m = members()[i];
  if (!m) return '';
  const check = dtie != null && dtie > 0 ? `<span class="dcb-check nextscore" role="img" aria-label="Has cosponsored this set" title="Has cosponsored this set"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg></span>` : '';
  const bits = detailBits(i, m, lift, sel, selc);
  const score = ((s - lo) / mx * 100).toFixed(0);
  return `<tr data-i="${i}">
<td class="dcb-c-member"><span class="dcb-member">${dotHtml(m.party)}<a class="mlk-name" data-mlk-nav href="${esc(profileHref(m.name))}">${esc(m.name)}</a>${check}</span>${bits.length ? `<span class="mlk-sub dcb-sub">${bits.join(' · ')}</span>` : ''}</td>
<td>${esc(m.chamber || '—')}</td>
<td>#${esc(m.rank != null ? m.rank : '—')}</td>
<td>${cosn != null ? esc(cosn) : '—'}</td>
<td>+${Number(gain).toFixed(1)}</td>
<td>${(Number(already) * 100).toFixed(0)}%</td>
<td><span class="dcb-score nextscore">+${esc(score)}</span></td>
<td class="dcb-c-add"><button type="button" class="mlk-btn mlk-btn--sm dcb-addbtn" data-dcb-add="${i}" aria-label="Add ${esc(m.name)} to the coalition">Add</button></td>
</tr>`;
 }).join('');
 right.innerHTML = `<div class="dcb-tablewrap"><table class="mlk-table dcb-table" aria-labelledby="dcb-sugg-label">
<colgroup><col><col class="dcb-w-chamber"><col class="dcb-w-rank"><col class="dcb-w-bills"><col class="dcb-w-reach"><col class="dcb-w-covered"><col class="dcb-w-score"><col class="dcb-w-add"></colgroup>
<thead><tr><th scope="col">Member</th><th scope="col">Chamber</th><th scope="col">Priority rank</th><th scope="col">Bills cosponsored</th><th scope="col">New reach</th><th scope="col">Already covered</th><th scope="col">Score</th><th scope="col"><span class="dcb-vh">Add</span></th></tr></thead>
<tbody>${body}</tbody></table></div>`;
}

function paint(err){
 loaded = true;
 grid.removeAttribute('aria-busy');
 root.classList.remove('is-loading');
 syncButtons();
 const cov = last ? last.cov : null;
 try { paintLeft(cov); } catch (e) { left.innerHTML = stateLine('Cosponsors could not be shown.', true); }
 if (err){
  right.innerHTML = stateLine('Suggestions could not load.', true);
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'mlk-btn';
  retry.textContent = 'Retry';
  retry.addEventListener('click', () => { refocus = 'retry'; window.renderCoal(); });
  right.querySelector('.dcb-state').append(document.createElement('br'), retry);
 } else {
  try { paintRight(last ? last.rows : []); } catch (e) { right.innerHTML = stateLine('Suggestions could not load.', true); }
 }
 restoreFocus();
}
function restoreFocus(){
 const r = refocus;
 refocus = null;
 if (!r) return;
 let el = null;
 if (r.kind === 'add'){ const b = right.querySelectorAll('[data-dcb-add]'); el = b[Math.min(r.index, b.length - 1)] || null; }
 if (r.kind === 'remove'){ const b = left.querySelectorAll('[data-dcb-remove]'); el = b[Math.min(r.index, b.length - 1)] || null; }
 if (r === 'retry') el = right.querySelector('.mlk-btn, a');
 (el || $id('dcb-add')).focus({preventScroll: true});
}

// wrap by name: every legacy caller (selects, basis, Clear, snapshot Load, setView('coalition')) reaches these
const legacyRender = window.renderCoal, legacySuggest = window.suggest;
window.suggest = function(cov){
 const rows = legacySuggest.apply(this, arguments);
 last = {cov: cov || null, rows: Array.isArray(rows) ? rows : []};
 return rows;
};
window.renderCoal = function(){
 last = null;
 let err = null;
 try { legacyRender.apply(this, arguments); } catch (e) { err = e; }
 paint(err);
};

/* ---------------- 3. interactions ---------------- */
function addMember(i){
 i = Number(i);
 if (!Number.isInteger(i) || !members()[i]) return;
 currentSet().add(i);
 window.renderCoal();
}
right.addEventListener('click', e => {
 const b = e.target.closest ? e.target.closest('[data-dcb-add]') : null;
 if (!b) return;
 const all = [...right.querySelectorAll('[data-dcb-add]')];
 refocus = {kind: 'add', index: all.indexOf(b)};
 addMember(b.dataset.dcbAdd);
});
left.addEventListener('click', e => {
 const b = e.target.closest ? e.target.closest('[data-dcb-remove]') : null;
 if (!b) return;
 const all = [...left.querySelectorAll('[data-dcb-remove]')];
 refocus = {kind: 'remove', index: all.indexOf(b)};
 currentSet().delete(Number(b.dataset.dcbRemove));
 window.renderCoal();
});

// Save snapshot: the kit popover asks for the name. The click listener runs in the capture phase on #cbsave itself, so it
// runs before the legacy onclick and stops it (that handler would open prompt()). Save then calls the legacy onclick, read
// at call time, with window.prompt answering the typed name for that one synchronous call and restored right after.
function setSaveMsg(text){ saveMsg.textContent = text || ''; saveMsg.hidden = !text; }
function syncSaveGo(){ const ok = !!saveName.value.trim(); saveGo.disabled = !ok; saveGo.setAttribute('aria-disabled', ok ? 'false' : 'true'); }
function openSave(){
 saveName.value = '';
 setSaveMsg('');
 syncSaveGo();
 savePop.classList.add('mlk-popover--end');
 K.open(savePop, {trigger: save, focus: saveName, kind: 'dialog'});
 // right-aligned to the button; where that would cross the left edge (narrow widths), align it to the button's left
 if (savePop.getBoundingClientRect().left < 8) savePop.classList.remove('mlk-popover--end');
}
function commitSave(){
 const name = saveName.value.trim();
 if (!name){ syncSaveGo(); saveName.focus({preventScroll: true}); return; }
 if (!currentSet().size){ K.close(savePop); return; }
 const handler = save.onclick;
 if (typeof handler !== 'function'){ setSaveMsg('Snapshot could not be saved.'); return; }
 const store = () => (typeof CBSNAPS !== 'undefined' && Array.isArray(CBSNAPS) ? CBSNAPS : null);
 const head = store() ? store()[0] : undefined;
 const nativePrompt = window.prompt;
 let failed = false;
 window.prompt = () => name;
 try { handler.call(save, new MouseEvent('click')); } catch (e) { failed = true; }
 finally { window.prompt = nativePrompt; }
 if (failed){
  // the legacy handler adds the record before it writes localStorage: take the unsaved record back out of the list
  const s = store();
  if (s && s[0] && s[0] !== head && s[0].name === name) s.shift();
  setSaveMsg('Snapshot could not be saved.');
  saveName.focus({preventScroll: true});
  return;
 }
 setSaveMsg('');
 K.close(savePop);
 if (K.toast){ try { K.toast('Snapshot saved'); } catch (e) { /* toast region unavailable */ } }
}
if (savePop){
 save.addEventListener('click', e => {
  e.preventDefault();
  e.stopImmediatePropagation();
  if (save.disabled || !currentSet().size) return;
  if (K.isOpen(savePop)) K.close(savePop); else openSave();
 }, true);
 saveName.addEventListener('input', () => { setSaveMsg(''); syncSaveGo(); });
 saveName.addEventListener('keydown', e => { if (e.key === 'Enter'){ e.preventDefault(); commitSave(); } });
 saveGo.addEventListener('click', commitSave);
 $id('dcb-savecancel').addEventListener('click', () => K.close(savePop));
 // Tabbing out of the popover closes it (a pointer press outside is closed by the kit)
 savePop.addEventListener('focusout', e => {
  const to = e.relatedTarget;
  if (to && !saveAnchor.contains(to) && K.isOpen(savePop)) K.close(savePop, {restoreFocus: false});
 });
}

// "Add a cosponsor" combobox (every member not already in the set, by name or state)
const input = $id('dcb-add'), results = $id('dcb-addres'), chev = $id('dcb-addchev');
const LIMIT = 50;
let sorted = null, active = -1, shown = [];
function allSorted(){
 if (!sorted) sorted = members().map((m, i) => [i, m]).filter(([, m]) => m && m.name).sort((a, b) => a[1].name.localeCompare(b[1].name));
 return sorted;
}
function openList(){
 const q = input.value.trim().toLowerCase(), set = currentSet();
 const hits = allSorted().filter(([i, m]) => !set.has(i) && (!q || `${m.name} ${m.state || ''}`.toLowerCase().includes(q)));
 shown = hits.slice(0, LIMIT);
 active = shown.length ? 0 : -1;
 results.innerHTML = shown.length
  ? '<div class="mlk-results__group" role="presentation">Members</div>' + shown.map(([i, m], k) => `<a class="mlk-results__item" role="option" id="dcb-opt-${k}" data-i="${i}" aria-selected="${k === active}">${dotHtml(m.party)}<span class="mlk-results__main">${esc(m.name)}</span><span class="mlk-results__meta">${esc([m.chamber, m.state].filter(Boolean).join(' · '))}</span></a>`).join('')
   + (hits.length > LIMIT ? `<div class="mlk-results__empty dcb-more" role="presentation">${hits.length - LIMIT} more · type to narrow</div>` : '')
  : '<div class="mlk-results__empty" role="presentation">No matches</div>';
 results.hidden = false;
 input.setAttribute('aria-expanded', 'true');
 setActive(active);
}
function closeList(){
 results.hidden = true;
 input.setAttribute('aria-expanded', 'false');
 input.removeAttribute('aria-activedescendant');
 active = -1;
}
function setActive(k){
 active = k;
 results.querySelectorAll('[role=option]').forEach((o, n) => o.setAttribute('aria-selected', n === k ? 'true' : 'false'));
 const o = k >= 0 ? $id('dcb-opt-' + k) : null;
 if (o){ input.setAttribute('aria-activedescendant', o.id); o.scrollIntoView({block: 'nearest'}); } else input.removeAttribute('aria-activedescendant');
}
function choose(i){
 input.value = '';
 closeList();
 refocus = null;
 addMember(i);
 input.focus({preventScroll: true});
}
input.addEventListener('focus', openList);
input.addEventListener('click', () => { if (results.hidden) openList(); });
input.addEventListener('input', openList);
input.addEventListener('keydown', e => {
 if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
  e.preventDefault();
  if (results.hidden){ openList(); return; }
  if (!shown.length) return;
  setActive(e.key === 'ArrowDown' ? Math.min(shown.length - 1, active + 1) : Math.max(0, active - 1));
 } else if (e.key === 'Home' && !results.hidden && shown.length && !input.value){ e.preventDefault(); setActive(0); }
 else if (e.key === 'End' && !results.hidden && shown.length && !input.value){ e.preventDefault(); setActive(shown.length - 1); }
 else if (e.key === 'Enter'){
  e.preventDefault();
  const exact = typeof byName !== 'undefined' ? byName[input.value.trim()] : null;
  if (exact != null && !currentSet().has(exact)) choose(exact);
  else if (active >= 0 && shown[active]) choose(shown[active][0]);
 } else if (e.key === 'Escape'){
  if (!results.hidden){ e.preventDefault(); e.stopPropagation(); closeList(); }
  else if (input.value){ e.preventDefault(); input.value = ''; }
 } else if (e.key === 'Tab') closeList();
});
results.addEventListener('mousedown', e => e.preventDefault());   // keep focus in the input
results.addEventListener('click', e => {
 const o = e.target.closest ? e.target.closest('[role=option]') : null;
 if (o) choose(o.dataset.i);
});
chev.addEventListener('mousedown', e => { e.preventDefault(); if (results.hidden){ input.focus(); openList(); } else closeList(); });
document.addEventListener('pointerdown', e => { if (!results.hidden && !e.target.closest('.dcb-add')) closeList(); }, true);
window.addEventListener('blur', closeList);

/* ---------------- 4. loading / error from restored-desk.js (desk-support.json) ---------------- */
// restored-desk.js appends p.restore-loading to <body> ("Loading bill and staff records…", then the error text and a
// Retry button). It is moved into the view; its own code still removes it on success and re-creates it on Retry.
function adoptStatus(p){
 if (!p || p.parentNode === status) return;
 status.append(p);
 const sync = () => {
  const failed = !!p.querySelector('button');
  root.classList.toggle('is-error', failed);
  p.classList.toggle('mlk-state--error', failed);
  if (failed){ grid.removeAttribute('aria-busy'); left.innerHTML = ''; right.innerHTML = ''; p.setAttribute('role', 'alert'); }
 };
 sync();
 new MutationObserver(sync).observe(p, {childList: true, subtree: true, characterData: true});
}
new MutationObserver(recs => recs.forEach(r => r.addedNodes.forEach(n => {
 if (n.nodeType === 1 && n.classList.contains('restore-loading')){
  adoptStatus(n);
  if (!loaded){ root.classList.remove('is-error'); root.classList.add('is-loading'); skeleton(); }
 }
}))).observe(document.body, {childList: true});

// first paint: the legacy view is shown by restored-desk.js after desk-support.json merges; until then, the skeleton
section.hidden = false;
root.classList.add('is-loading');
skeleton();
syncButtons();

window.MelangeCoalition = {
 render: () => window.renderCoal(),
 add: addMember,
 showLoading(){ loaded = false; root.classList.add('is-loading'); skeleton(); },
 get last(){ return last; },
 root
};
})();
