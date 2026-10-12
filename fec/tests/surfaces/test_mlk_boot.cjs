// mlk-boot.js (r5 loading): the gate, the in-place retry, the mount check and the visible failure, in a scripted DOM.
// A real browser run of the same paths (refused scripts, delayed overlays) is the lane's Playwright proof.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../surfaces/approved/clean/mlk-boot.js'), 'utf8');

function element(tag, attrs = {}) {
  const el = {tagName: tag.toUpperCase(), attrs: {...attrs}, children: [], parentNode: null, async: false, defer: false, rel: attrs.rel || '',
    getAttribute: n => (n in el.attrs ? el.attrs[n] : null), setAttribute(n, v) { el.attrs[n] = String(v); }, hasAttribute: n => n in el.attrs,
    removeAttribute(n) { delete el.attrs[n]; }, appendChild(c) { c.parentNode = el; el.children.push(c); return c; },
    insertBefore(c, ref) { c.parentNode = el; const i = el.children.indexOf(ref); el.children.splice(i < 0 ? el.children.length : i, 0, c); return c; },
    cloneNode() { return element(tag, {...el.attrs}); }, remove() { if (el.parentNode) el.parentNode.children.splice(el.parentNode.children.indexOf(el), 1); },
    addEventListener(type, fn) { (el.handlers = el.handlers || {})[type] = fn; }, querySelector: () => null, closest: () => null, focus() {}};
  return el;
}

function page({frame = 'desk', ready = 'body.dsk-v2', session = {}, url = 'http://127.0.0.1:8841/staffer-live/approved/original-desk.html?deskview=ranked'} = {}) {
  const u = new URL(url), timers = [], writes = [], reloads = [], winL = {}, docL = {}, present = new Set();
  const classes = new Set(), html = element('html'), head = element('head'), body = element('body');
  html.classList = {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c)};
  const store = new Map(Object.entries(session));
  const location = {origin: u.origin, href: u.href, pathname: u.pathname, search: u.search, reload: () => reloads.push(1)};
  const document = {readyState: 'loading', documentElement: html, head, body,
    currentScript: element('script', {'data-frame': frame, 'data-ready': ready}),
    createElement: element, write: s => writes.push(s), getElementById: () => null,
    querySelector: sel => (present.has(sel) ? {} : sel === '.mlk-boot-panel' ? body.children.find(c => c.className === 'mlk-boot-panel') || null : null),
    querySelectorAll: () => [], addEventListener: (t, fn) => (docL[t] = docL[t] || []).push(fn)};
  const window = {addEventListener: (t, fn, opt) => (winL[t] = winL[t] || []).push({fn, capture: opt === true}),
    sessionStorage: {getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)}};
  const ctx = {window, document, location, URL, setTimeout: (fn, ms) => timers.push({fn, ms}), MutationObserver: function () { this.observe = () => {}; }, WeakSet};
  window.document = document;
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  const api = {ctx, window, document, html, body, classes, writes, reloads, store, present, timers,
    boot: () => window.MLKBoot,
    loadError(el) { for (const l of winL.error || []) if (l.capture) l.fn({target: el}); },
    runtimeError() { for (const l of winL.error || []) l.fn({target: window, message: 'x'}); },
    dcl() { let stopped = false; const e = {stopImmediatePropagation: () => { stopped = true; }};
      document.readyState = 'interactive'; for (const fn of docL.DOMContentLoaded || []) { if (stopped) break; fn(e); } return stopped; },
    tick() { const t = timers.splice(0); for (const {fn} of t) fn(); return t; },
    load() { document.readyState = 'complete'; for (const l of winL.load || []) l.fn(); }};
  return api;
}
const script = (src, extra = {}) => element('script', {src, 'data-mlk': '', ...extra});
const fontLink = p => p.document.head.children.find(c => c.tagName === 'LINK' && 'data-mlk-fonts' in c.attrs);

test('fix2 D-1: the loader links the font kit render-blocking; a failed kit is never a failed view', () => {
  for (const frame of ['desk', 'shell']) {
    const p = page(frame === 'shell' ? {frame, ready: '', url: 'http://127.0.0.1:8841/?view=members'} : {});
    const link = fontLink(p);
    assert.ok(link, frame + ': font link present');
    assert.equal(link.getAttribute('href'), 'https://use.typekit.net/ayd3qhz.css');
    assert.equal(link.rel, 'stylesheet');
    assert.equal(link.getAttribute('blocking'), 'render');           // no flash of fallback type while it arrives
    assert.equal(p.document.head.children.indexOf(link), 1);          // right after the gate style, before anything else
    assert.equal(p.boot().state.fonts, 'loading');
    link.handlers.error();                                           // Typekit unreachable (offline, TLS failure)
    assert.equal(p.boot().state.fonts, 'failed');
    p.loadError(link);                                               // the window sees the same failed load
    assert.deepEqual([...p.boot().state.failed], []);
    assert.equal(p.writes.length + p.reloads.length, 0);
    if (frame === 'desk') { p.present.add('body.dsk-v2'); p.dcl(); p.tick(); assert.equal(p.boot().state.phase, 'ready'); }
    else { assert.equal(p.dcl(), false); p.tick(); assert.equal(p.boot().state.phase, 'ready'); }
  }
});

test('fix2 D-1: a slow font kit blocks rendering for FONT_WAIT ms at most, then the page renders and the kit swaps in', () => {
  const p = page();
  const link = fontLink(p);
  const wait = p.timers.find(t => t.ms === 1500);
  assert.ok(wait, 'a 1500 ms cap is scheduled');
  wait.fn();
  assert.equal(link.getAttribute('blocking'), null);                // rendering goes ahead in the fallback stack
  assert.equal(p.boot().state.fonts, 'late');
  link.handlers.load();
  assert.equal(p.boot().state.fonts, 'loaded');
  const q = page();                                                  // a kit that arrived in time is left alone
  fontLink(q).handlers.load();
  q.timers.find(t => t.ms === 1500).fn();
  assert.equal(fontLink(q).getAttribute('blocking'), 'render');
  assert.equal(q.boot().state.fonts, 'loaded');
});

test('fix2 D-1: no overlay stylesheet @imports anything (a failed import fired the error event of the local sheet)', () => {
  const dir = path.join(__dirname, '../../surfaces/approved/clean');
  const sheets = fs.readdirSync(dir).filter(n => n.endsWith('.css'));
  assert.ok(sheets.length >= 15, String(sheets.length));
  for (const n of sheets) assert.doesNotMatch(fs.readFileSync(path.join(dir, n), 'utf8'), /@import/i, n);
});

test('a frame starts hidden with a delayed status line and lifts once its overlay has mounted', () => {
  const p = page();
  assert.ok(p.classes.has('mlk-boot'));
  assert.equal(p.html.getAttribute('data-mlk-boot-status'), 'Loading this view…');
  assert.match(p.document.head.children[0].textContent, /html\.mlk-boot body\{visibility:hidden!important\}/);
  assert.match(p.document.head.children[0].textContent, /animation:mlk-boot-in 0s \.3s forwards/);
  p.present.add('body.dsk-v2');
  p.dcl(); p.tick();
  assert.equal(p.boot().state.phase, 'ready');
  assert.ok(!p.classes.has('mlk-boot'));
  assert.equal(p.html.getAttribute('data-mlk-boot-status'), null);
});

test('a refused workspace script is requested again in place, in order, then the page reloads once, then says so', () => {
  const p = page();
  const s = script('/staffer-live/approved/desk-members.js');
  p.loadError(s);
  assert.deepEqual(p.writes, ['<script src="/staffer-live/approved/desk-members.js?mlk-retry=1" data-mlk><\/script>']);
  p.loadError(script('/staffer-live/approved/desk-members.js?mlk-retry=1'));
  assert.equal(p.writes[1], '<script src="/staffer-live/approved/desk-members.js?mlk-retry=2" data-mlk><\/script>');
  p.loadError(script('/staffer-live/approved/desk-members.js?mlk-retry=2'));
  assert.equal(p.writes.length, 2);                                      // two retries, then the page is reloaded
  assert.equal(p.boot().state.phase, 'reloading');
  assert.equal(p.html.getAttribute('data-mlk-boot-status'), 'Retrying…');
  p.tick();
  assert.equal(p.reloads.length, 1);
  const key = [...p.store.keys()][0];
  assert.match(key, /^mlk-boot-reload:desk:\/staffer-live\/approved\/original-desk\.html\?deskview=ranked$/);
  // the reloaded page fails again: no second reload, a visible panel instead, and the gate stays on
  const q = page({session: {[key]: '1'}});
  for (let i = 0; i < 3; i++) q.loadError(script('/staffer-live/approved/desk-members.js' + (i ? `?mlk-retry=${i}` : '')));
  assert.equal(q.boot().state.phase, 'failed');
  assert.deepEqual([...q.boot().state.failed], ['desk-members.js']);   // state lives in the page's realm
  q.dcl(); q.tick();
  const panel = q.body.children.find(c => c.className === 'mlk-boot-panel');
  assert.ok(panel, 'failure panel shown');
  assert.match(panel.innerHTML, /This view did not load/);
  assert.match(panel.innerHTML, /did not deliver desk-members\.js/);
  assert.match(panel.innerHTML, /data-mlk-boot="retry">Retry</);
  assert.match(panel.innerHTML, /data-mlk-boot="original">Show original layout</);
  assert.ok(q.classes.has('mlk-boot'), 'the legacy layout stays hidden until the reader chooses it');
  assert.equal(q.reloads.length, 0);
  assert.equal(q.store.size, 0);
});

test('only parser-blocking workspace scripts are written again; everything else is judged, not rewritten', () => {
  const p = page();
  p.loadError(element('script', {src: '/staffer-live/staffer-live.js'}));   // a legacy tag without data-mlk
  assert.equal(p.writes.length, 0);
  assert.equal(p.boot().state.phase, 'reloading');
  const q = page();
  const late = script('/staffer-live/approved/research.js');
  q.document.readyState = 'interactive';                                   // after parsing: a write would wipe the page
  q.loadError(late);
  assert.equal(q.writes.length, 0);
  assert.equal(q.boot().state.phase, 'reloading');
  const r = page();
  const dynamic = script('/staffer-live/approved/x.js'); dynamic.async = true;
  r.loadError(dynamic);
  assert.equal(r.writes.length, 0);
});

test('a failed stylesheet is linked again right after itself', () => {
  const p = page();
  const link = element('link', {rel: 'stylesheet', href: '/staffer-live/approved/desk-members.css'});
  p.document.head.appendChild(link);
  p.loadError(link);
  const copy = p.document.head.children[p.document.head.children.indexOf(link) + 1];
  assert.equal(copy.getAttribute('href'), '/staffer-live/approved/desk-members.css?mlk-retry=1');
  assert.equal(p.boot().state.phase, 'loading');
  assert.deepEqual([...p.boot().state.retried], ['desk-members.css']);
});

test('a relative stylesheet under <base href> is linked again at the address the browser used', () => {
  const p = page({frame: 'people', ready: 'nav.ppl-tabs', url: 'http://127.0.0.1:8841/staffer-live/approved/people.html?tab=directory'});
  p.document.baseURI = 'http://127.0.0.1:8841/staffer-live/';
  const link = element('link', {rel: 'stylesheet', href: 'approved/restored-people.css'});
  p.document.head.appendChild(link);
  p.loadError(link);
  const copy = p.document.head.children[p.document.head.children.indexOf(link) + 1];
  assert.equal(copy.getAttribute('href'), '/staffer-live/approved/restored-people.css?mlk-retry=1');
});

test('fonts, other origins, other paths and runtime errors never count as a failed load', () => {
  const p = page();
  p.loadError(element('link', {rel: 'stylesheet', href: 'https://use.typekit.net/abc.css'}));
  p.loadError(element('script', {src: 'http://example.com/staffer-live/x.js', 'data-mlk': ''}));
  p.loadError(element('img', {src: '/staffer-live/approved/pic.png'}));
  p.loadError(element('script', {src: '/api/staffer-live'}));
  p.runtimeError();
  assert.equal(p.writes.length, 0);
  assert.equal(p.boot().state.phase, 'loading');
  assert.deepEqual([...p.boot().state.failed], []);
});

test('an overlay that did not mount is reported after the page has loaded, never shown silently', () => {
  const p = page({frame: 'champions', ready: 'html.chm-ready'});
  p.dcl(); p.tick();
  assert.equal(p.boot().state.phase, 'loading');                         // waits for the load event first
  p.load(); p.tick();
  assert.equal(p.boot().state.phase, 'reloading');
  assert.equal(p.boot().state.missing, 'html.chm-ready');
  const key = [...p.store.keys()][0];
  const q = page({frame: 'champions', ready: 'html.chm-ready', session: {[key]: '1'}});
  q.dcl(); q.tick(); q.load(); q.tick();
  assert.equal(q.boot().state.phase, 'failed');
  assert.match(q.body.children.find(c => c.className === 'mlk-boot-panel').innerHTML, /redesigned layout did not start/);
  const r = page({frame: 'research-bills', ready: ''});                  // styles only: ready at DOMContentLoaded
  r.dcl(); r.tick();
  assert.equal(r.boot().state.phase, 'ready');
});

test('the shell is never hidden; a failed shell script stops its first render and reports in place', () => {
  const p = page({frame: 'shell', ready: '', url: 'http://127.0.0.1:8841/?view=members'});
  assert.ok(!p.classes.has('mlk-boot'));
  assert.equal(p.dcl(), false);                                          // a healthy shell renders normally
  p.tick();
  assert.equal(p.boot().state.phase, 'ready');
  const content = element('main');
  const q = page({frame: 'shell', ready: '', url: 'http://127.0.0.1:8841/?view=members', session: {'mlk-boot-reload:shell:/?view=members': '1'}});
  q.document.getElementById = id => (id === 'content' ? content : null);
  for (let i = 0; i < 3; i++) q.loadError(script('/staffer-live/approved/research.js' + (i ? `?mlk-retry=${i}` : '')));
  assert.equal(q.boot().state.phase, 'failed');
  assert.equal(q.dcl(), true, 'app.js does not render a shell with a missing script');
  assert.match(content.innerHTML, /mlk-state mlk-state--error/);
  assert.match(content.innerHTML, /did not deliver research\.js/);
  assert.doesNotMatch(content.innerHTML, /Show original layout/);
});
