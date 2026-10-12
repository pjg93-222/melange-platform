/* mlk-boot.js: the workspace loader. build_clean_workspace.py puts it first in the <head> of every frame document it
   writes, and index.html puts it first in the shell's <head>; it is a plain blocking script, so it runs before anything
   else on the page. It makes loading fail loudly instead of leaving the legacy layout on screen without a word:
   1. Gate (frames). The page stays hidden (html.mlk-boot) until its v2 overlay has mounted, so the legacy layout the
      page's own engine draws first never paints (audit r4 mel-b F-3). A status line shows after a short delay.
   2. Retry. A workspace script (/staffer-live/...) marked data-mlk that fails to load is requested again in place and
      in order: its error event runs inside the parser's script step, where document.write inserts the new tag right
      after the failed one, so the overlays still run before the scripts that follow them (restored-desk.js wraps what
      they install). A stylesheet is linked again in place. RETRIES times each (audit r4 mel-a F1: refused bursts).
   3. Verify. After DOMContentLoaded (after the overlays' own ready handlers) the frame's mount marker, data-ready on this
      tag (e.g. body.dsk-v2), must be present; then the gate lifts.
   4. Fail visibly. A file that still does not load, or an overlay that did not mount, reloads the document once
      (sessionStorage-bounded), then shows "This view did not load" with Retry, and in a frame "Show original layout".
   In the shell (data-frame="shell") nothing is hidden (the shell has its own loading line); a failed shell script
   stops the shell's first render instead, and the view iframe is watched: a frame whose document did not arrive at all
   (a refused connection leaves the browser's error page) is requested again, then reported in place.
   window.MLKBoot.state reports what happened (frame, phase, retried, failed, missing). */
/* Fonts (fix2, r5 audit D-1): the web-font kit is linked here, not @imported by the overlay stylesheets. An @import made
   every document's scripts wait for use.typekit.net (Typekit 3 s slow = 7-8 s per view), and a failed import fired the
   local sheet's error event, so an unreachable Typekit read as "did not deliver app.css" and no view loaded. The link is
   render-blocking (no flash of fallback type, as before) but not script-blocking, and blocks for FONT_WAIT ms at most;
   then the page renders in the fallback stack (Aptos / Segoe UI, Georgia) and the kit swaps in when it arrives. */
(function () {
'use strict';
var W = window, D = document, H = D.documentElement;
if (W.MLKBoot) return;
var me = D.currentScript;
var FRAME = (me && me.getAttribute('data-frame')) || 'frame';
var READY = (me && me.getAttribute('data-ready')) || '';
var SHELL = FRAME === 'shell';
var SCOPE = '/staffer-live/';
var FRAMES = /^\/staffer-live\/approved\/(original-[a-z-]+|network|people)\.html$/;   // the frame documents the build writes
var RETRIES = 2, RELOADS = 1, FONTS = 'https://use.typekit.net/ayd3qhz.css', FONT_WAIT = 1500;
var KEY = 'mlk-boot-reload:' + FRAME + ':' + location.pathname + location.search;
var state = {frame: FRAME, phase: 'loading', ready: READY, retried: [], failed: [], missing: '', reloads: 0, ms: 0, fonts: 'loading'};
var started = Date.now(), tries = {}, listeners = [];

function store(fn){ try { return fn(W.sessionStorage); } catch (e) { return null; } }
state.reloads = +(store(function (s) { return s.getItem(KEY); }) || 0);

/* ---- gate (frames only): hidden until mounted; the status line fades in after 300 ms so a fast load shows nothing ---- */
var style = D.createElement('style');
style.id = 'mlk-boot-style';
style.textContent =
  'html.mlk-boot body{visibility:hidden!important}' +
  'html.mlk-boot::after{content:attr(data-mlk-boot-status);position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;' +
  'justify-content:center;visibility:visible;pointer-events:none;font:400 14px/1.45 var(--sans,sans-serif);color:var(--ml-muted);' +
  'opacity:0;animation:mlk-boot-in 0s .3s forwards}' +
  'html.mlk-boot[data-mlk-boot-frame=network]::after{color:var(--ml-on-dark-muted)}' +
  '@keyframes mlk-boot-in{to{opacity:1}}' +
  '.mlk-boot-panel{position:fixed;inset:0;z-index:2147483647;box-sizing:border-box;display:flex;align-items:flex-start;' +
  'justify-content:center;padding:15vh 24px 24px;visibility:visible;background:var(--ml-page);overflow:auto}' +
  '.mlk-boot-panel>section{max-width:520px}';
(D.head || H).appendChild(style);
fonts();
if (!SHELL) {
  H.classList.add('mlk-boot');
  H.setAttribute('data-mlk-boot-frame', FRAME);
  status('Loading this view…');
}

function status(text){ if (!SHELL) H.setAttribute('data-mlk-boot-status', text); }
function emit(){ state.ms = Date.now() - started; for (var i = 0; i < listeners.length; i++) { try { listeners[i](state); } catch (e) { /* a listener's fault */ } } }
function nameOf(u){ return u.pathname.slice(u.pathname.lastIndexOf('/') + 1) || u.pathname; }

/* ---- fonts: render-blocking for FONT_WAIT ms at most, never script-blocking, never a load failure ---- */
function fonts(){
  var link = D.createElement('link');
  link.rel = 'stylesheet';
  link.setAttribute('href', FONTS);
  link.setAttribute('data-mlk-fonts', '');
  link.setAttribute('blocking', 'render');           // render-blocking, not script-blocking; set before <body> exists
  link.addEventListener('load', function () { settle('loaded'); });
  link.addEventListener('error', function () { settle('failed'); });
  (D.head || H).appendChild(link);
  setTimeout(function () {
    if (state.fonts !== 'loading') return;
    link.removeAttribute('blocking');                 // render in the fallback stack now; the kit swaps in on arrival
    state.fonts = 'late';
    emit();
  }, FONT_WAIT);
  function settle(result){ if (state.fonts === 'loading' || state.fonts === 'late') { state.fonts = result; emit(); } }
}

/* ---- retry: every same-origin /staffer-live/ script or stylesheet that fails to load ---- */
W.addEventListener('error', function (e) {
  var el = e.target;
  if (!el || el === W || !el.tagName) return;                    // a runtime error, not a failed load
  var tag = el.tagName, raw = tag === 'SCRIPT' ? el.getAttribute('src') : tag === 'LINK' && /\bstylesheet\b/i.test(el.rel || '') ? el.getAttribute('href') : null;
  if (!raw) return;
  var u; try { u = new URL(raw, D.baseURI || location.href); } catch (err) { return; }   // as the browser resolved it (<base>)
  if (u.origin !== location.origin || u.pathname.indexOf(SCOPE) !== 0) return;
  var key = u.pathname, n = (tries[key] || 0) + 1;
  tries[key] = n;
  var name = nameOf(u), again = u.pathname + '?mlk-retry=' + n;
  if (n <= RETRIES) {
    // In place and in order: only a parser-inserted, parser-blocking script we authored (data-mlk) while the parser is
    // still running. Its error event fires inside the parser's script step, so the written tag lands right after it.
    if (tag === 'SCRIPT' && el.hasAttribute('data-mlk') && D.readyState === 'loading' && !el.async && !el.defer) {
      state.retried.push(name);
      D.write('<script src="' + again + '" data-mlk><\/script>');
      return;
    }
    if (tag === 'LINK' && el.parentNode) {
      state.retried.push(name);
      var copy = el.cloneNode(false);
      copy.setAttribute('href', again);
      el.parentNode.insertBefore(copy, el.nextSibling);
      return;
    }
  }
  if (state.failed.indexOf(name) < 0) state.failed.push(name);
  fail('The local server did not deliver ' + name + '.');
}, true);

/* ---- verify at DOMContentLoaded, after every other ready handler ---- */
var pending = '';
D.addEventListener('DOMContentLoaded', function (e) {
  if (SHELL && state.phase !== 'loading') e.stopImmediatePropagation();   // no first render of a broken shell
  if (pending) { panel(pending); pending = ''; }
  if (state.phase === 'loading') setTimeout(check, 0);
});
function check(){
  if (state.phase !== 'loading') return;
  if (READY && !D.querySelector(READY)) {
    if (D.readyState !== 'complete') { W.addEventListener('load', function () { setTimeout(check, 0); }, {once: true}); return; }
    state.missing = READY;
    return fail('The redesigned layout did not start on this page.');
  }
  state.phase = 'ready';
  H.classList.remove('mlk-boot');
  H.removeAttribute('data-mlk-boot-status');
  store(function (s) { s.removeItem(KEY); });
  emit();
}

/* ---- fail: one bounded reload, then a visible panel ---- */
function fail(detail){
  if (state.phase === 'failed' || state.phase === 'reloading') return;
  var late = state.phase === 'ready';                       // e.g. a stylesheet that failed after the gate lifted
  if (!late && state.reloads < RELOADS && store(function (s) { s.setItem(KEY, String(state.reloads + 1)); return true; })) {
    state.phase = 'reloading';
    status('Retrying…');
    emit();
    setTimeout(function () { location.reload(); }, 400 + 600 * state.reloads);
    return;
  }
  store(function (s) { s.removeItem(KEY); });
  state.phase = 'failed';
  status('');
  emit();
  if (D.readyState === 'loading') pending = detail;       // shown at DOMContentLoaded, when the body exists
  else panel(detail);
}
function panel(detail){
  var section = '<section class="mlk-state mlk-state--error" role="alert"><h2 class="mlk-viewtitle">This view did not load</h2>' +
    '<p class="missing">' + detail.replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }) + '</p>' +
    '<button type="button" class="mlk-btn mlk-btn--primary" data-mlk-boot="retry">Retry</button>' +
    (SHELL ? '' : ' <button type="button" class="mlk-btn mlk-btn--text" data-mlk-boot="original">Show original layout</button>') + '</section>';
  var host;
  if (SHELL && D.getElementById('content')) { host = D.getElementById('content'); host.removeAttribute('aria-busy'); host.innerHTML = section; }
  else { host = D.querySelector('.mlk-boot-panel') || D.createElement('div'); host.className = 'mlk-boot-panel'; host.innerHTML = section; if (!host.parentNode) (D.body || H).appendChild(host); }
  host.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-mlk-boot]');
    if (!b) return;
    if (b.getAttribute('data-mlk-boot') === 'retry') { location.reload(); return; }
    var p = D.querySelector('.mlk-boot-panel'); if (p) p.remove();
    H.classList.remove('mlk-boot');
    H.setAttribute('data-mlk-boot-original', 'shown');
  });
  var first = host.querySelector('[data-mlk-boot=retry]');
  if (first && !SHELL) try { first.focus({preventScroll: true}); } catch (e) { /* not focusable yet */ }
}

/* ---- shell: watch the view iframe for a document that never arrived ---- */
function watchFrames(){
  var seen = typeof WeakSet === 'function' ? new WeakSet() : null;
  function arrived(f){
    // Every frame document the build writes defines MLKBoot (this file is its first script); an error page is opaque.
    try {
      var w = f.contentWindow, path = w.location.pathname;
      if (!FRAMES.test(path)) return true;                  // about:blank, or a document this loader does not own
      return !!(f.contentDocument && w.MLKBoot);
    } catch (e) { return false; }
  }
  function attach(f){
    if (!seen || seen.has(f)) return;
    seen.add(f);
    var attempts = 0;
    f.addEventListener('load', function () {
      if (!f.isConnected || arrived(f)) return;
      var src = f.getAttribute('src');
      if (!src) return;
      if (attempts++ < RETRIES) { setTimeout(function () { if (f.isConnected) f.setAttribute('src', src); }, 300 * attempts); return; }
      var note = D.createElement('section');
      note.className = 'mlk-state mlk-state--error mlk-boot-framefail';
      note.setAttribute('role', 'alert');
      note.innerHTML = '<h2 class="mlk-viewtitle">This view did not load</h2><p class="missing">The local server did not return this view.</p>' +
        '<button type="button" class="mlk-btn mlk-btn--primary">Retry</button>';
      note.querySelector('button').addEventListener('click', function () { attempts = 0; note.remove(); f.hidden = false; f.setAttribute('src', src); });
      f.hidden = true;
      f.parentNode.insertBefore(note, f);
    });
  }
  function scan(){ var list = D.querySelectorAll('iframe.engine-frame'); for (var i = 0; i < list.length; i++) attach(list[i]); }
  if (typeof MutationObserver === 'function') new MutationObserver(scan).observe(H, {childList: true, subtree: true});
}
if (SHELL) watchFrames();

W.MLKBoot = {
  version: '1',
  state: state,
  onChange: function (fn) { if (typeof fn === 'function') listeners.push(fn); }
};
})();
