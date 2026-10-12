/* desk-shell.js: overlay for original-desk.html (desk frame chrome, Member desk tab group, deskaction=...); owner: prompt 02. Runs after the desk engine and ui-kit.js, before restored-desk.js. */
/* Build 02 (board #27). The legacy title row (header > .brand + .topnav) is hidden by desk-shell.css; its controls stay in
   the DOM and keep their listeners. This file:
   1. renders the Member desk tab group (MLK.deskTabs, the same markup and kit classes as the shell's native group on
      Contact log / Notes & exports) at the top of every desk tab view; the profile (deskview=member) has none (board #29);
   2. runs "Import notes", "Export stance overrides" and "Import overrides" from that group's menu by triggering the legacy
      controls (#importfile, #povexport, #povimport) in place, under the reader's click;
   3. consumes ?deskaction=import-notes|export-overrides|import-overrides (the shell's own menu on the native views routes
      there): with a live user activation it triggers the control at once, otherwise it shows a one-line action strip
      whose button triggers it (browsers open a file chooser only under a user gesture);
   4. mirrors the legacy #povhint status (pending stance overrides) beside the menu;
   5. stamps body classes dsk-v2 and dsk-view-<deskview> for the other desk overlays (03-07). */
(() => {
'use strict';
const K = window.MLK, D0 = document, body = D0.body;
if (!K || !body || !body.classList.contains('restored-desk')) return;
const Q = new URLSearchParams(location.search);
const VIEW = (Q.get('deskview') || 'ranked').replace(/[^a-z0-9-]/gi, '');
body.classList.add('dsk-v2', 'dsk-view-' + VIEW);

// deskview -> the shell view whose tab is current
const ACTIVE = {ranked: 'members', bills: 'member-bills', efficiency: 'efficiency', layers: 'efficiency', coalition: 'coalition', compare: 'compare', contact: 'contact'};
const MENU_ID = 'dsk-deskmenu';
// ?deskaction value -> legacy control (template header .topnav) and the words of the action strip
const ACTIONS = {
 'import-notes': {control: 'importfile', label: 'Import notes', button: 'Choose file'},
 'export-overrides': {control: 'povexport', label: 'Export stance overrides', button: 'Download'},
 'import-overrides': {control: 'povimport', label: 'Import overrides', button: 'Choose file'}
};

function trigger(action){
 const a = ACTIONS[action], el = a && D0.getElementById(a.control);
 if (!el) return false;
 el.click();                     // the legacy onchange/onclick handlers do the work (FileReader import, Blob download)
 return true;
}

/* ---- 1. tab group ---- */
let bar = null;
if (VIEW !== 'member'){
 bar = D0.createElement('div');
 bar.className = 'dsk-bar';
 bar.id = 'dsk-bar';
 bar.innerHTML = K.deskTabs(ACTIVE[VIEW] || 'members', {menuId: MENU_ID});
 body.prepend(bar);
 // 4. pending-override status from the hidden legacy #povhint, beside the menu
 const src = D0.getElementById('povhint'), anchor = bar.querySelector('.mlk-anchor');
 if (src && anchor){
  const hint = D0.createElement('span');
  hint.className = 'dsk-povhint';
  hint.setAttribute('role', 'status');
  const sync = () => { const t = src.textContent.trim(); hint.textContent = t; hint.hidden = !t; };
  sync();
  new MutationObserver(sync).observe(src, {childList: true, characterData: true, subtree: true});
  anchor.before(hint);
 }
}

/* ---- 2. menu items run the legacy controls in place (window capture: before the kit's data-mlk-nav handler) ---- */
function actionOf(a){
 try { return new URL(a.getAttribute('href'), location.origin).searchParams.get('deskaction'); } catch (e) { return null; }
}
window.addEventListener('click', e => {
 const a = e.target && e.target.closest ? e.target.closest('#dsk-bar a[href]') : null;
 if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
 const action = actionOf(a);
 if (!ACTIONS[action]) return;
 e.preventDefault();
 e.stopImmediatePropagation();
 K.close(MENU_ID, {restoreFocus: false});
 const btn = bar && bar.querySelector('.mlk-deskbar__menu');
 if (btn) btn.focus({preventScroll: true});
 dismissStrip();
 trigger(action);
}, true);

/* ---- 3. ?deskaction= from the shell ---- */
let strip = null;
function dismissStrip(){ if (strip){ strip.remove(); strip = null; } }
function showStrip(action){
 const a = ACTIONS[action];
 strip = D0.createElement('div');
 strip.className = 'dsk-action';
 strip.id = 'dsk-action';
 strip.setAttribute('role', 'group');
 strip.setAttribute('aria-label', a.label);
 strip.innerHTML = `<span class="dsk-action__label">${K.esc(a.label)}</span>`
  + `<button type="button" class="mlk-btn mlk-btn--sm dsk-action__go">${K.esc(a.button)}</button>`
  + `<button type="button" class="mlk-close dsk-action__close" aria-label="Close">${K.icon('close')}</button>`;
 strip.querySelector('.dsk-action__go').addEventListener('click', () => { dismissStrip(); trigger(action); });
 strip.querySelector('.dsk-action__close').addEventListener('click', dismissStrip);
 strip.addEventListener('keydown', e => { if (e.key === 'Escape'){ e.preventDefault(); dismissStrip(); } });
 if (bar) bar.after(strip); else body.prepend(strip);
 strip.querySelector('.dsk-action__go').focus({preventScroll: true});
}
function stripShellParam(){
 // one-shot: drop deskaction from the shell address so a reload does not ask again (restored-desk.js edits the shell URL the same way)
 try {
  const top = window.top;
  if (top === window || top.location.origin !== location.origin) return;
  const u = new URL(top.location.href);
  if (!u.searchParams.has('deskaction')) return;
  u.searchParams.delete('deskaction');
  top.history.replaceState(top.history.state, '', u.pathname + u.search + u.hash);
 } catch (e) { /* detached or cross-origin: leave the address as it is */ }
}
const pending = Q.get('deskaction');
if (Object.prototype.hasOwnProperty.call(ACTIONS, pending)){
 const live = !!(navigator.userActivation && navigator.userActivation.isActive);
 if (!(live && trigger(pending))) showStrip(pending);
 stripShellParam();
}

window.MelangeDeskShell = {view: VIEW, active: ACTIVE[VIEW] || null, actions: Object.keys(ACTIONS), trigger, showStrip, dismissStrip};
})();
