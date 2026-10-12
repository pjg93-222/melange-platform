/* Industry > Overview loading state (r5, audit F-2).

   The Industry view is an engine frame whose page (original-industry.html, about 2.8 MB) can take seconds to arrive;
   the pane used to stay blank meanwhile. research.js (loaded by the shell) now puts a board-shaped loading state behind
   the frame, and industry-ui.css keeps the frame transparent and its legacy page unpainted until the overlay mounts.
   The research.js block is sliced out and run in a vm against a DOM stub, like the other .cjs suites here. */
'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const clean = path.join(__dirname, '../../surfaces/approved/clean');
const research = fs.readFileSync(path.join(clean, 'research.js'), 'utf8');
const css = fs.readFileSync(path.join(clean, 'industry-ui.css'), 'utf8');
const overlay = fs.readFileSync(path.join(clean, 'industry-ui.js'), 'utf8');

function load() {
  const start = research.indexOf('/* r5 (audit F-2)'), end = research.indexOf("if(document.getElementById('content'))watchIndustryFrames");
  assert.ok(start > 0 && end > start, 'research.js carries the industry loading-state block');
  const timers = [];
  const ctx = {WeakSet, setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout: id => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    MutationObserver: class { constructor(fn) { ctx.observed = fn; } observe(target, opts) { ctx.observeArgs = [target, opts]; } }};
  vm.createContext(ctx);
  vm.runInContext(research.slice(start, end) + '\nthis.mount = mountIndustrySkeleton; this.markup = industryFrameSkeleton; this.watch = watchIndustryFrames;', ctx);
  ctx.timers = timers;
  return ctx;
}
function stubHost() {
  const host = {children: [], html: [],
    insertAdjacentHTML(where, html) { assert.equal(where, 'afterbegin'); const el = {html, removed: false, remove() { this.removed = true; host.children.splice(host.children.indexOf(this), 1); }}; host.children.unshift(el); host.html.push(html); },
    get firstElementChild() { return host.children[0] || null; }};
  const classes = new Set();
  const frame = {parentElement: host, listeners: {}, contentDocument: {documentElement: {classList: {contains: c => classes.has(c)}}},
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
    removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(f => f !== fn); },
    fire(name) { for (const fn of [...(this.listeners[name] || [])]) fn(); }, mount() { classes.add('ind-v2'); }};
  return {host, frame};
}

test('the loading state is the page\'s own loading copy and kit skeletons, in board #37\'s shape', () => {
  const {markup} = load();
  const html = markup();
  assert.match(html, /<p class="mlk-state" role="status">Loading industry spend…<\/p>/);
  assert.ok(overlay.includes('Loading industry spend…'), 'the same words the frame shows once its overlay is up');
  assert.equal((html.match(/class="mlk-skel-row"/g) || []).length, 12, 'six rows in the focus card, two in each of three side cards');
  assert.match(html, /grid-template-columns:minmax\(0,1fr\) 332px/, 'the focus card and the 332px other-industries column');
  assert.match(html, /position:absolute;inset:0/);
  assert.match(html, /pointer-events:none/);
  assert.doesNotMatch(html, /#[0-9a-f]{3,6}\b|rgb\(/i, 'chrome tokens only, no colour literals');
});

test('it sits behind the frame from insertion until a load ends with the overlay mounted, once per frame', () => {
  const ctx = load();
  const {host, frame} = stubHost();
  const skel = ctx.mount(frame);
  assert.equal(host.children.length, 1);
  assert.equal(host.firstElementChild, skel, 'inserted first, so the frame (absolutely placed after it) paints over it');
  assert.equal(ctx.mount(frame), null, 'a second scan does not add a second one');
  assert.equal(host.children.length, 1);
  frame.fire('load');                      // e.g. an error page the loader then requests again: not our page yet
  assert.equal(skel.removed, false, 'kept while the frame holds no mounted Industry page');
  frame.mount();
  frame.fire('load');
  assert.equal(skel.removed, true);
  assert.equal(host.children.length, 0);
  assert.equal(ctx.timers[0].cleared, true, 'the fallback timer is cleared');
  assert.equal((frame.listeners.load || []).length, 0, 'the load listener is released');
});

test('a frame that never fires load does not keep the loading state forever', () => {
  const ctx = load();
  const {host, frame} = stubHost();
  const skel = ctx.mount(frame);
  assert.equal(ctx.timers[0].ms, 30000);
  ctx.timers[0].fn();
  assert.equal(skel.removed, true);
  assert.equal(host.children.length, 0);
});

test('the shell watches #content and mounts it for the Industry frame only', () => {
  const ctx = load();
  const {frame} = stubHost();
  let selector = '';
  const content = {querySelector(sel) { selector = sel; return frame; }};
  ctx.watch(content);
  assert.equal(selector, ':scope>.mlk-frame-host>iframe.engine-frame[data-view="industry"]');
  assert.equal(frame.parentElement.children.length, 1, 'a frame already in place when the script runs is covered');
  assert.equal(ctx.observeArgs[0], content);
  assert.deepEqual({...ctx.observeArgs[1]}, {childList: true});
  ctx.observed();
  assert.equal(frame.parentElement.children.length, 1, 'a re-scan of the same frame adds nothing');
});

test('the frame stays transparent and unpainted until its overlay mounts, with a fallback reveal', () => {
  assert.ok(css.includes('html:not(.ind-v2),html:not(.ind-v2) body{background:transparent!important}'));
  assert.ok(css.includes('html:not(.ind-v2) body{visibility:hidden;animation:ind-reveal 0s linear 8s forwards}'));
  assert.ok(css.includes('@keyframes ind-reveal{to{visibility:visible}}'));
  assert.doesNotMatch(css, /html:not\(\.ind-v2\)[^{]*\{[^}]*display:\s*none/, 'layout still runs while hidden');
  assert.ok(css.includes('html.mlk-boot:not(.ind-v2)::after{content:none}'), "one loading message: the loader's status line gives way to the shell's");
  assert.ok(overlay.includes("root.classList.add('ind-v2');"), 'industry-ui.js marks the mount that ends the hidden state');
});
