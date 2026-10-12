// rendered_desk_probe.mjs -- execute the SHIPPED member-desk artifacts and report what the
// page's module-scope structures ACTUALLY hold, before and after the deferred-profile merge.
//
// This is the execution half of fec/audit/audit_rendered_desk.py.  Python drives, derives every
// EXPECTED number from member_desk_bundle.json, and does all the asserting; this file only
// observes.  It deliberately reports raw counts and digests and never compares them against an
// expectation, so that the expectation can never be sourced from the same execution it measures.
//
// Why execution and not grep: fec/audit/dashboard_integrity.py validates the BUNDLE and reported
// "status: pass, bills: 5899" while the shipped page showed 0 of 464 bills, because the page built
// two module-scope indexes over fields that DESK_SPLIT defers into cu-member-desk-profiles.js.
// A structure that is frozen empty is textually indistinguishable from one that is correct; only
// running it tells them apart.
//
// Usage:  node rendered_desk_probe.mjs <job.json>
//   job = {html, profiles|null, lightLine?:int}
// Output: one line on stdout, "__PROBE_JSON__" followed by a JSON object.

import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';

const t0 = Date.now();
const ms = () => Date.now() - t0;
const timings = {};
const notes = [];

/* ------------------------------------------------------------------ minimal DOM stub ---- */
// Deliberately minimal and side-effect free: no layout, no network, no timers that outlive the
// probe.  querySelector/getElementById memoize per selector so the page's repeated $('#x') calls
// see one stable node, which is what makes rebuildBillRepository's <select> repopulation path
// actually execute instead of short-circuiting on a null.

function makeStorage() {
  const m = new Map();
  return {
    get length() { return m.size; },
    key(i) { return [...m.keys()][i] ?? null; },
    getItem(k) { return m.has(String(k)) ? m.get(String(k)) : null; },
    setItem(k, v) { m.set(String(k), String(v)); },
    removeItem(k) { m.delete(String(k)); },
    clear() { m.clear(); },
  };
}

class ClassList {
  constructor(el) { this._s = new Set(); this._el = el; }
  add(...c) { c.forEach(x => x && this._s.add(String(x))); this._sync(); }
  remove(...c) { c.forEach(x => this._s.delete(String(x))); this._sync(); }
  toggle(c, f) {
    c = String(c);
    if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
    else if (f) { this._s.add(c); } else { this._s.delete(c); }
    this._sync(); return this._s.has(c);
  }
  contains(c) { return this._s.has(String(c)); }
  item(i) { return [...this._s][i] ?? null; }
  get length() { return this._s.size; }
  _sync() { this._el.className = [...this._s].join(' '); }
  [Symbol.iterator]() { return this._s[Symbol.iterator](); }
}

class OptionList extends Array {
  add(o) { this.push(o); }
  item(i) { return this[i] ?? null; }
  namedItem() { return null; }
}

class El {
  constructor(tag = 'div') {
    this.tagName = String(tag).toUpperCase();
    this.nodeName = this.tagName;
    this.nodeType = 1;
    this.children = [];
    this.childNodes = this.children;
    this.style = { setProperty() {}, getPropertyValue() { return ''; }, removeProperty() {} };
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.id = '';
    this.textContent = '';
    this.innerHTML = '';
    this.innerText = '';
    this.outerHTML = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.selected = false;
    this.href = ''; this.src = ''; this.title = ''; this.type = ''; this.placeholder = '';
    this.files = [];
    this.options = new OptionList();
    this.parentNode = null; this.parentElement = null;
    this.scrollTop = 0; this.scrollLeft = 0; this.scrollHeight = 0;
    this.offsetWidth = 0; this.offsetHeight = 0; this.offsetTop = 0; this.offsetLeft = 0;
    this.clientWidth = 0; this.clientHeight = 0;
    this.classList = new ClassList(this);
    this.onclick = null; this.onchange = null; this.oninput = null; this.onkeydown = null;
  }
  appendChild(c) {
    if (!c) return c;
    this.children.push(c);
    c.parentNode = this; c.parentElement = this;
    if (c.tagName === 'OPTION') this.options.push(c);
    return c;
  }
  append(...cs) { cs.forEach(c => { if (c && c.tagName) this.appendChild(c); }); }
  prepend(...cs) { cs.forEach(c => { if (c && c.tagName) { this.children.unshift(c); c.parentNode = this; } }); }
  insertBefore(c, ref) {
    const i = this.children.indexOf(ref);
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    c.parentNode = this; return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1);
    const j = this.options.indexOf(c); if (j >= 0) this.options.splice(j, 1);
    return c;
  }
  replaceChildren(...cs) { this.children.length = 0; this.options.length = 0; cs.forEach(c => this.appendChild(c)); }
  // HTMLSelectElement.remove(index) and ChildNode.remove() share a name in the DOM; the page uses
  // both (sel.remove(1) when repopulating #billcongress, el.remove() for the profiles note).
  remove(i) {
    if (typeof i === 'number') {
      const o = this.options[i];
      if (o) { this.options.splice(i, 1); const j = this.children.indexOf(o); if (j >= 0) this.children.splice(j, 1); }
      return;
    }
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  setAttribute(k, v) {
    this.attributes[String(k)] = String(v);
    if (k === 'id') this.id = String(v);
    if (k === 'class') this.className = String(v);
  }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, String(k)) ? this.attributes[String(k)] : null; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, String(k)); }
  removeAttribute(k) { delete this.attributes[String(k)]; }
  addEventListener() {} removeEventListener() {} dispatchEvent() { return true; }
  focus() {} blur() {} click() {} scrollIntoView() {} select() {} setSelectionRange() {}
  closest() { return null; }
  matches() { return false; }
  contains() { return false; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getElementsByTagName() { return []; }
  getElementsByClassName() { return []; }
  getBoundingClientRect() { return { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }; }
  cloneNode() { return new El(this.tagName); }
  insertAdjacentHTML() {} insertAdjacentElement(_p, e) { return e; }
  getContext() { return null; }
  get firstChild() { return this.children[0] ?? null; }
  get lastChild() { return this.children[this.children.length - 1] ?? null; }
  get firstElementChild() { return this.children[0] ?? null; }
  get nextSibling() { return null; }
  get previousSibling() { return null; }
  get selectedOptions() { return this.options.filter(o => o.selected); }
}

// The page gates two of its re-render calls on `!el.hidden` (rebuildStaffIndex -> renderContact,
// rebuildBillRepository -> renderBills).  A stub whose nodes are all visible therefore runs render
// paths the real page does not run at load, and in the monolith shape that reaches a pathfinder
// helper before its module-scope table exists.  So the stub takes its initial hidden state from the
// SHIPPED markup rather than inventing one.
function hiddenIdsOf(html) {
  const ids = new Set();
  for (const m of html.matchAll(/<[a-zA-Z][^>]*>/g)) {
    const tag = m[0];
    if (!/(?<![-\w])hidden(?=[\s>=])/.test(tag)) continue;
    const id = /\bid=(?:"([^"]+)"|'([^']+)'|([^\s>"']+))/.exec(tag);
    if (id) ids.add(id[1] || id[2] || id[3]);
  }
  return ids;
}

function makeGlobals(hiddenIds = new Set()) {
  const byId = new Map();
  const bySel = new Map();
  const timers = [];
  const doc = {
    nodeType: 9,
    body: new El('body'),
    head: new El('head'),
    documentElement: new El('html'),
    readyState: 'complete',
    title: '',
    cookie: '',
    createElement: t => new El(t),
    createElementNS: (_ns, t) => new El(t),
    createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
    createDocumentFragment: () => new El('fragment'),
    createRange: () => ({ selectNodeContents() {}, setStart() {}, setEnd() {} }),
    getElementById(id) {
      const k = String(id);
      if (!byId.has(k)) { const e = new El('div'); e.id = k; e.hidden = hiddenIds.has(k); byId.set(k, e); }
      return byId.get(k);
    },
    querySelector(sel) {
      const s = String(sel);
      if (/^#[A-Za-z0-9_-]+$/.test(s)) return doc.getElementById(s.slice(1));
      if (!bySel.has(s)) bySel.set(s, new El('div'));
      return bySel.get(s);
    },
    querySelectorAll() { return []; },
    getElementsByTagName() { return []; },
    getElementsByClassName() { return []; },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    execCommand() { return false; },
  };

  const g = {};
  g.window = g;
  g.self = g;
  g.top = g;
  g.parent = g;
  g.document = doc;
  g.localStorage = makeStorage();
  g.sessionStorage = makeStorage();
  g.matchMedia = q => ({
    matches: false, media: String(q), onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
  });
  g.history = { state: null, length: 1, pushState() {}, replaceState() {}, back() {}, forward() {}, go() {} };
  g.location = {
    href: 'file:///cu-member-desk.html', search: '', hash: '', pathname: '/cu-member-desk.html',
    origin: 'file://', host: '', hostname: '', protocol: 'file:', port: '',
    assign() {}, replace() {}, reload() {}, toString() { return this.href; },
  };
  g.navigator = { userAgent: 'melange-rendered-desk-probe', language: 'en-US', languages: ['en-US'], clipboard: { writeText: () => Promise.resolve() } };
  g.screen = { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040 };
  g.innerWidth = 1440; g.innerHeight = 900; g.devicePixelRatio = 1;
  g.scrollX = 0; g.scrollY = 0;
  g.addEventListener = () => {}; g.removeEventListener = () => {}; g.dispatchEvent = () => true;
  g.scrollTo = () => {}; g.scrollBy = () => {};
  g.getComputedStyle = () => ({ getPropertyValue: () => '', width: '0px', height: '0px' });
  g.alert = () => {}; g.confirm = () => false; g.prompt = () => null;
  g.open = () => null; g.close = () => {}; g.print = () => {};
  // Timers are QUEUED, not dropped, and drained explicitly between phases: a page that finishes
  // its board render inside a setTimeout(0) must still be observed, and a probe that silently
  // discarded that work would report a half-initialised page as if it were the real one.
  g.setTimeout = (fn, _d, ...a) => { if (typeof fn === 'function') timers.push(() => fn(...a)); return timers.length; };
  g.setInterval = () => 0;
  g.clearTimeout = () => {}; g.clearInterval = () => {};
  g.queueMicrotask = fn => { if (typeof fn === 'function') timers.push(fn); };
  g.requestAnimationFrame = fn => { if (typeof fn === 'function') timers.push(() => fn(0)); return timers.length; };
  g.cancelAnimationFrame = () => {};
  g.requestIdleCallback = fn => { if (typeof fn === 'function') timers.push(() => fn({ timeRemaining: () => 0, didTimeout: true })); return timers.length; };
  g.cancelIdleCallback = () => {};
  const URLShim = function (...a) { return new URL(...a); };
  URLShim.createObjectURL = () => 'blob:probe'; URLShim.revokeObjectURL = () => {};
  g.URL = URLShim;
  g.URLSearchParams = URLSearchParams;
  g.DOMParser = class { parseFromString() { return { body: new El('body'), querySelector: () => null, querySelectorAll: () => [] }; } };
  g.Node = El; g.Element = El; g.HTMLElement = El;
  g.getSelection = () => ({ toString: () => '', removeAllRanges() {}, addRange() {} });
  g.btoa = s => Buffer.from(String(s), 'binary').toString('base64');
  g.atob = s => Buffer.from(String(s), 'base64').toString('binary');
  g.Blob = class Blob { constructor(p, o) { this.parts = p; this.type = (o || {}).type || ''; } };
  g.File = class File { constructor(p, n, o) { this.parts = p; this.name = n; this.type = (o || {}).type || ''; } };
  g.FileReader = class FileReader { readAsDataURL() {} readAsText() {} abort() {} };
  g.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
  g.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  g.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
  g.CustomEvent = class CustomEvent { constructor(t, o) { this.type = t; Object.assign(this, o || {}); } };
  g.Event = g.CustomEvent;
  g.console = console;
  g.crypto = crypto.webcrypto;
  g.performance = performance;
  g.TextEncoder = TextEncoder; g.TextDecoder = TextDecoder;
  g.structuredClone = structuredClone;
  g.fetch = () => Promise.reject(new Error('rendered-desk probe: network disabled'));
  g.XMLHttpRequest = class { open() {} setRequestHeader() {} send() { throw new Error('rendered-desk probe: network disabled'); } };
  g.__probeDrainTimers = () => { let n = 0; while (timers.length) { const fn = timers.shift(); try { fn(); n++; } catch (e) { notes.push('timer threw: ' + (e && e.message)); } } return n; };
  return g;
}

function describeError(e) {
  if (!e) return 'unknown error';
  const msg = (e.name ? e.name + ': ' : '') + (e.message || String(e));
  const frames = (e.stack ? String(e.stack).split('\n') : []).filter(l => /^\s+at /.test(l)).slice(0, 3);
  const site = (e.stack ? String(e.stack).split('\n').slice(0, 2).join(' | ') : '');
  return msg + ' || site: ' + site + ' || ' + frames.join(' | ');
}

/* ------------------------------------------------------------------ canonical digests ---- */
// Order-insensitive for Set/Map/plain-object keys, order-sensitive for arrays.  Property INSERTION
// ORDER differs between the split path (light keys, then heavy keys appended by the merge loop)
// and the monolith path (bundle order), so a raw JSON.stringify comparison would report a
// difference that is not one.  Sorting keys is what makes the two paths comparable at all.

function digestOf(x, budget) {
  const h = crypto.createHash('sha256');
  h.update(digestOfInner(x, budget, new Set()));
  return h.digest('hex');
}

// Memoised by object identity.  BILLS and BILL_BY_KEY hold the same 464 row objects, and the
// instrumented and monolith contexts share the heavy field objects delivered by the one parse of
// the profiles payload, so without this the same subgraphs are walked several times over.
// Memoisation is skipped for any subtree that touched a back-edge, where the digest is
// context-dependent and caching it would be unsound.
function digestOfInner(x, budget, seen) {
  budget.n++;
  if (budget.n > budget.limit) { budget.truncated = true; return 'BUDGET'; }
  if (x === null) return 'null';
  const t = typeof x;
  if (t === 'undefined') return 'undef';
  if (t === 'boolean') return 'b' + x;
  if (t === 'number') return 'n' + (Object.is(x, -0) ? '0' : String(x));
  if (t === 'bigint') return 'B' + x;
  if (t === 'string') return 's' + crypto.createHash('sha256').update(x).digest('hex');
  if (t === 'symbol') return 'y' + String(x);
  if (t === 'function') return 'f' + (x.name || '') + '/' + x.length;
  if (seen.has(x)) { budget.cycles++; return 'CYCLE'; }
  const cached = budget.memo.get(x);
  if (cached !== undefined) return cached;
  const cyclesBefore = budget.cycles;
  seen.add(x);
  let out;
  const tag = Object.prototype.toString.call(x);
  try {
    if (tag === '[object Set]') {
      const parts = [...x].map(v => digestOfInner(v, budget, seen)).sort();
      out = 'SET(' + parts.join(',') + ')';
    } else if (tag === '[object Map]') {
      const parts = [...x].map(([k, v]) => digestOfInner(k, budget, seen) + '=' + digestOfInner(v, budget, seen)).sort();
      out = 'MAP(' + parts.join(',') + ')';
    } else if (tag === '[object Date]') {
      out = 'D' + x.getTime();
    } else if (tag === '[object RegExp]') {
      out = 'R' + x.source + '/' + x.flags;
    } else if (ArrayBuffer.isView(x)) {
      out = 'TA' + tag + ':' + crypto.createHash('sha256').update(Buffer.from(x.buffer, x.byteOffset, x.byteLength)).digest('hex');
    } else if (Array.isArray(x)) {
      const parts = [];
      for (let i = 0; i < x.length; i++) parts.push(digestOfInner(x[i], budget, seen));
      out = 'A[' + parts.join(',') + ']';
    } else {
      const keys = Object.keys(x).sort();
      const parts = keys.map(k => k + ':' + digestOfInner(x[k], budget, seen));
      out = 'O{' + parts.join(',') + '}';
    }
  } finally {
    seen.delete(x);
  }
  // Collapse long expansions so the recursion cost stays linear in node count, not in string length.
  const packed = out.length > 96 ? 'H' + crypto.createHash('sha256').update(out).digest('hex') : out;
  if (budget.cycles === cyclesBefore && !budget.truncated) budget.memo.set(x, packed);
  return packed;
}

function newBudget(limit = 40000000) {
  return { n: 0, limit, truncated: false, cycles: 0, memo: new WeakMap() };
}

function sizeOf(x) {
  if (x === null || typeof x !== 'object') return null;
  const tag = Object.prototype.toString.call(x);
  if (tag === '[object Set]' || tag === '[object Map]') return x.size;
  if (Array.isArray(x) || ArrayBuffer.isView(x)) return x.length;
  return Object.keys(x).length;
}

function kindOf(x) {
  if (x === null) return 'null';
  const t = typeof x;
  if (t !== 'object') return t;
  const tag = Object.prototype.toString.call(x);
  return tag.slice(8, -1);
}

/* ------------------------------------------------------------------ artifact surgery ---- */

function extractScript(html) {
  const m = /<script(?![^>]*\bsrc=)[^>]*>/.exec(html);
  if (!m) throw new Error('no inline <script> in artifact');
  const start = m.index + m[0].length;
  const end = html.indexOf('</script>', start);
  if (end < 0) throw new Error('unterminated inline <script>');
  return { src: html.slice(start, end), start, end };
}

// The page's bundle literal is emitted by make_member_desk.py as one line, `const D={...};`.
// Assert that shape rather than regex-hunting inside 3MB of JSON: if the producer ever changes
// the emission the probe must fail loudly, not silently audit the wrong bytes.
function splitBundleLine(src) {
  const lines = src.split('\n');
  let idx = -1;
  for (let i = 0; i < Math.min(lines.length, 20); i++) {
    if (lines[i].startsWith('const D={') && lines[i].endsWith('};')) { idx = i; break; }
  }
  if (idx < 0) throw new Error('bundle literal line `const D={...};` not found in the first 20 lines');
  return { idx, lines, json: lines[idx].slice('const D='.length, -1) };
}

// Module scope in this artifact is column 0 -- every nested statement is either indented or on the
// same line as its opener.  Rewriting only the column-0 const/let to var makes the page's
// module-scope bindings own properties of the vm global, which is the only way to ENUMERATE them
// (lexical declarations are not reflectable).  The rewrite is cross-validated: the transformed run
// must reproduce the untransformed run's index counts exactly, or the probe reports the mismatch.
// Descriptive companion to the execution evidence: every column-0 declaration, the identifiers it
// binds, and whether its statement text reads the member roster.  A statement runs from its
// column-0 opening line through every indented line before the next column-0 statement, which is
// how this artifact is formatted (DEGREE/STRATKEY/STRATMEM are two-line IIFEs).  This LISTS; it
// does not assert -- the assertions in audit_rendered_desk.py run on observed values.
function moduleScopeDecls(lines, skipIdx) {
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    if (i === skipIdx) continue;
    if (!/^(const|let|var)\s/.test(lines[i])) continue;
    let text = lines[i];
    let j = i + 1;
    while (j < lines.length && (lines[j] === '' || /^\s/.test(lines[j]))) { text += '\n' + lines[j]; j++; }
    const names = [];
    const re = /(?:^(?:const|let|var)\s+|,\s*)([A-Za-z_$][\w$]*)\s*=/g;
    let m;
    while ((m = re.exec(lines[i])) !== null) names.push(m[1]);
    // Quoted literals and line comments are stripped first: compactDollars' '$…M' suffix is not a
    // reference to the roster.  Spread dots are blanked so `...M.map(...)` (MAXROI) still counts,
    // while a real property access `X.M` still does not.
    const clean = text
      .replace(/\/\/[^\n]*/g, ' ')
      .replace(/'(?:\\.|[^'\\])*'/g, "''")
      .replace(/"(?:\\.|[^"\\])*"/g, '""')
      .replace(/\.\.\./g, ' ');
    out.push({
      line: i + 1,
      names,
      reads_members: /(?<![\w$.])(?:M|D\.members)(?![\w$])/.test(clean),
      text: text.length > 320 ? text.slice(0, 320) + ' ...' : text,
    });
  }
  return out;
}

function varTransform(lines) {
  let n = 0;
  const out = lines.map(l => {
    const m = /^(const|let)(\s)/.exec(l);
    if (!m) return l;
    n++;
    return 'var' + m[2] + l.slice(m[0].length);
  });
  return { lines: out, rewritten: n };
}

/* ------------------------------------------------------------------ run helpers ---- */

function newContext(hiddenIds) {
  const g = makeGlobals(hiddenIds);
  vm.createContext(g);
  return g;
}

const READOUT = `
;globalThis.__probeReadout=function(){
  var r={};
  try{ r.bills = (typeof BILLS!=='undefined' && BILLS) ? BILLS.length : null; }catch(e){ r.bills=null; }
  try{ r.billByKey = (typeof BILL_BY_KEY!=='undefined' && BILL_BY_KEY) ? Object.keys(BILL_BY_KEY).length : null; }catch(e){ r.billByKey=null; }
  try{ r.stfidx = (typeof STFIDX!=='undefined' && STFIDX) ? Object.keys(STFIDX).length : null; }catch(e){ r.stfidx=null; }
  try{ r.dsplit = (typeof DSPLIT!=='undefined') ? DSPLIT : null; }catch(e){ r.dsplit=null; }
  r.profilesReady = !!globalThis.__profilesReady;
  r.hookType = typeof globalThis.__melangeProfileMerge;
  r.members = D.members.length;
  r.membersWithBills = D.members.filter(function(m){return Array.isArray(m.bills);}).length;
  r.membersWithStaffdir = D.members.filter(function(m){return Array.isArray(m.staffdir);}).length;
  r.billRows = D.members.reduce(function(a,m){return a+((m.bills&&m.bills.length)||0);},0);
  r.staffRows = D.members.reduce(function(a,m){return a+((m.staffdir&&m.staffdir.length)||0);},0);
  r.billLobbying = !!D.bill_lobbying;
  r.names = D.members.map(function(m){return m.name;});
  // The ranked board as the reader would see it. renderRanked() runs once at module scope and the
  // merge hook does NOT re-run it, so if the board's markup depends on any deferred field it is
  // permanently stale -- and unlike an index, nothing about its length would look wrong.
  try{ var _rows=document.getElementById('rows'); r.rowsHtml = _rows ? String(_rows.innerHTML) : null; }
  catch(e){ r.rowsHtml = null; }
  return r;
};
`;

function readout(ctx) {
  const r = vm.runInContext('__probeReadout()', ctx);
  // The board markup is hundreds of KB; carry its length and hash across the wire, not the markup.
  const html = r.rowsHtml;
  const out = {};
  for (const k of Object.keys(r)) if (k !== 'rowsHtml') out[k] = r[k];
  out.rowsHtmlLen = html == null ? null : html.length;
  out.rowsHtmlSha = html == null ? null : crypto.createHash('sha256').update(html).digest('hex');
  return out;
}

/* ------------------------------------------------------------------ main ---- */

function main() {
  const jobPath = process.argv[2];
  if (!jobPath) throw new Error('usage: node rendered_desk_probe.mjs <job.json>');
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));

  const html = fs.readFileSync(job.html, 'utf8');
  const hiddenIds = hiddenIdsOf(html);
  timings.read_html_ms = ms();
  const { src } = extractScript(html);
  const bundleLine = splitBundleLine(src);
  const result = {
    ok: true,
    html: job.html,
    profiles: job.profiles || null,
    script_bytes: src.length,
    hidden_ids: [...hiddenIds].sort(),
    notes,
    timings,
  };

  /* ---- phase 1: the shipped bytes, executed verbatim (only a readout epilogue appended) ---- */
  const shipped = newContext(hiddenIds);
  let compiled;
  try {
    compiled = new vm.Script(src + READOUT, { filename: 'cu-member-desk.html#inline' });
    compiled.runInContext(shipped, { displayErrors: true });
  } catch (e) {
    result.ok = false;
    result.error = 'shipped script threw at module scope: ' + describeError(e);
    return result;
  }
  const drained0 = vm.runInContext('__probeDrainTimers()', shipped);
  timings.shipped_init_ms = ms();
  const pre = readout(shipped);
  result.shipped = { drained_timers_init: drained0, pre };
  result.split = pre.dsplit === 1 && pre.profilesReady === false;

  // A monolith artifact is a legitimate input, not a failure -- but every deferred-field assertion
  // below is VACUOUS against one, so it is reported as such and the merge phases are not run.
  if (!result.split) {
    result.split_reason = 'DSPLIT=' + JSON.stringify(pre.dsplit) + ', __profilesReady at init=' +
      JSON.stringify(pre.profilesReady) + ' -- artifact does not defer profile fields';
    return result;
  }
  if (!job.profiles) {
    result.ok = false;
    result.error = 'artifact is split (DSPLIT=1) but no profiles payload was supplied';
    return result;
  }

  /* ---- phase 2: the real profiles file, executed against the real page ---- */
  const profilesSrc = fs.readFileSync(job.profiles, 'utf8');
  result.profiles_bytes = profilesSrc.length;
  timings.read_profiles_ms = ms();

  // A4 evidence: wrap the page's own rebuild functions and its hook, then let the SHIPPED profiles
  // file invoke them.  Nothing here greps for a name; the call list is produced by execution.
  vm.runInContext(`
    globalThis.__probeCalls=[];
    globalThis.__probeCapture=null;
    (function(){
      var names=['rebuildBillRepository','rebuildStaffIndex'];
      globalThis.__probePatched=[];
      names.forEach(function(n){
        var orig=globalThis[n];
        if(typeof orig!=='function')return;
        globalThis.__probePatched.push(n);
        globalThis[n]=function(){ globalThis.__probeCalls.push(n); return orig.apply(this,arguments); };
      });
      var hook=globalThis.__melangeProfileMerge;
      globalThis.__melangeProfileMerge=function(D2){ globalThis.__probeCapture=D2; globalThis.__probeCalls.push('__melangeProfileMerge'); return hook.call(this,D2); };
    })();
  `, shipped);

  let pscript;
  try {
    pscript = new vm.Script(profilesSrc, { filename: 'cu-member-desk-profiles.js' });
    timings.profiles_compile_ms = ms();
    pscript.runInContext(shipped, { displayErrors: true });
  } catch (e) {
    result.ok = false;
    result.error = 'profiles payload threw: ' + describeError(e);
    return result;
  }
  const drained1 = vm.runInContext('__probeDrainTimers()', shipped);
  timings.merge_ms = ms();
  const post = readout(shipped);
  result.shipped.post = post;
  result.shipped.drained_timers_merge = drained1;
  result.shipped.calls = vm.runInContext('__probeCalls.slice()', shipped);
  result.shipped.patched = vm.runInContext('__probePatched.slice()', shipped);

  const captured = vm.runInContext('__probeCapture', shipped);
  if (!captured || !Array.isArray(captured.members_heavy)) {
    result.ok = false;
    result.error = 'profiles payload did not deliver members_heavy through the page hook';
    return result;
  }
  const heavy = captured.members_heavy;
  const deferred = new Set();
  for (const row of heavy) for (const k of Object.keys(row)) deferred.add(k);
  result.deferred_fields = [...deferred].sort();
  result.heavy_rows = heavy.length;
  timings.capture_ms = ms();

  /* ---- phase 3: instrumented split run -- which deferred fields are READ, and when ---- */
  // The light bundle is re-parsed from the artifact and every member wrapped in a recording Proxy.
  // A get on a field that does not exist still fires the trap, so the split run alone reveals the
  // full set of deferred fields the page touches at module scope -- no monolith needed for this.
  const readLog = { phase: 'init', enabled: true, init: new Set(), merge: new Set() };
  const lightForProbe = JSON.parse(bundleLine.json);
  lightForProbe.members = lightForProbe.members.map(m => new Proxy(m, {
    get(t, k, r) {
      if (readLog.enabled && typeof k === 'string' && deferred.has(k)) readLog[readLog.phase].add(k);
      return Reflect.get(t, k, r);
    },
  }));

  const tf = varTransform(bundleLine.lines.slice());
  tf.lines[bundleLine.idx] = 'var D=__INJECTED_D;';
  result.var_rewritten = tf.rewritten;

  const inst = newContext(hiddenIds);
  inst.__INJECTED_D = lightForProbe;
  let instOk = true;
  try {
    new vm.Script(tf.lines.join('\n') + READOUT, { filename: 'cu-member-desk.html#instrumented' })
      .runInContext(inst, { displayErrors: true });
  } catch (e) {
    instOk = false;
    result.instrumented_error = 'instrumented run threw: ' + describeError(e);
  }
  if (instOk) {
    // __probeReadout() itself touches m.bills and m.staffdir to count them, so the log must be
    // muted around it.  Left unmuted, the readout alone put `bills` and `staffdir` into the
    // merge-phase set, which would mask exactly the two defects this audit exists to catch: a hook
    // that stopped rebuilding one of them would still look like it had read the field.
    const readoutQuiet = ctx => {
      const prev = readLog.enabled;
      readLog.enabled = false;
      try { return readout(ctx); } finally { readLog.enabled = prev; }
    };
    vm.runInContext('__probeDrainTimers()', inst);
    const instPre = readoutQuiet(inst);
    readLog.phase = 'merge';
    let mergeErr = null;
    try {
      vm.runInContext('globalThis.__probeMerge=function(p){ return __melangeProfileMerge(p); }', inst);
      inst.__probeMerge(captured);
      vm.runInContext('__probeDrainTimers()', inst);
    } catch (e) {
      mergeErr = 'instrumented merge threw: ' + (e && e.message);
    }
    const instPost = readoutQuiet(inst);
    readLog.enabled = false;
    result.instrumented = {
      pre: instPre,
      post: instPost,
      merge_error: mergeErr,
      reads_at_init: [...readLog.init].sort(),
      reads_at_merge: [...readLog.merge].sort(),
    };
    timings.instrumented_ms = ms();

    // Enumerate every module-scope binding the transformed script created, then digest it.
    const BASE = new Set(Object.getOwnPropertyNames(makeGlobals()));
    // `globalThis` is the vm's self-reference, not a page binding: digesting it would walk the whole
    // context (every stub node included) and would make every real binding a subgraph of one blob.
    const isBinding = n => !BASE.has(n) && !n.startsWith('__probe') && n !== '__INJECTED_D' && n !== 'globalThis';
    const bindingNames = vm.runInContext('Object.getOwnPropertyNames(globalThis)', inst).filter(isBinding);
    const budget = newBudget();
    const instD = vm.runInContext('D', inst);
    const instM = vm.runInContext('M', inst);
    const snapshot = (ctx, names, D_, M_) => {
      const out = {};
      for (const n of names) {
        let v;
        try { v = vm.runInContext('globalThis[' + JSON.stringify(n) + ']', ctx); } catch (e) { out[n] = { error: String(e && e.message) }; continue; }
        if (v === D_ || v === M_) { out[n] = { skip: 'raw payload alias (D / D.members), not a derived structure' }; continue; }
        if (typeof v === 'function') { out[n] = { kind: 'function' }; continue; }
        out[n] = { kind: kindOf(v), size: sizeOf(v), digest: digestOf(v, budget) };
      }
      return out;
    };
    result.instrumented.bindings = snapshot(inst, bindingNames, instD, instM);
    result.instrumented.binding_names = bindingNames;
    result.module_scope_decls = moduleScopeDecls(bundleLine.lines, bundleLine.idx);
    result.budget_truncated = budget.truncated;
    result.digest_nodes = budget.n;
    result.digest_cycles = budget.cycles;
    timings.instrumented_digest_ms = ms();

    /* ---- phase 4: monolith-equivalent run (the DESK_SPLIT=0 shape) ---- */
    // Reconstructed rather than produced: the split producer's own receipt is that reunion is
    // sha256-identical to the monolith payload, and DSPLIT gates nothing at module scope except
    // window.__profilesReady.  Building it with the producer would be a deployment action, not an
    // audit.  Reported as `monolith_source: "reconstructed"` so the claim is never overstated.
    const fullD = JSON.parse(bundleLine.json);
    for (let i = 0; i < fullD.members.length; i++) Object.assign(fullD.members[i], heavy[i] || {});
    fullD.bill_lobbying = captured.bill_lobbying;
    const monoLines = tf.lines.slice();
    let dsplitPatched = false;
    for (let i = 0; i < monoLines.length; i++) {
      if (/^var DSPLIT=1;/.test(monoLines[i])) { monoLines[i] = monoLines[i].replace('var DSPLIT=1;', 'var DSPLIT=0;'); dsplitPatched = true; break; }
    }
    const mono = newContext(hiddenIds);
    mono.__INJECTED_D = fullD;
    let monoOk = true;
    try {
      new vm.Script(monoLines.join('\n') + READOUT, { filename: 'cu-member-desk.html#monolith' })
        .runInContext(mono, { displayErrors: true });
      vm.runInContext('__probeDrainTimers()', mono);
    } catch (e) {
      monoOk = false;
      result.monolith_error = 'monolith run threw: ' + describeError(e);
    }
    if (monoOk) {
      const monoD = vm.runInContext('D', mono);
      const monoM = vm.runInContext('M', mono);
      const monoNames = vm.runInContext('Object.getOwnPropertyNames(globalThis)', mono).filter(isBinding);
      result.monolith = {
        source: 'reconstructed',
        dsplit_patched: dsplitPatched,
        init: readout(mono),
        binding_names: monoNames,
        bindings: snapshot(mono, monoNames, monoD, monoM),
      };
      result.budget_truncated = result.budget_truncated || budget.truncated;
      result.digest_nodes = budget.n;
      result.digest_cycles = budget.cycles;
      timings.monolith_ms = ms();
    }
  }

  timings.total_ms = ms();
  return result;
}

let out;
try {
  out = main();
} catch (e) {
  out = { ok: false, error: String(e && e.stack ? e.stack : e), notes, timings };
}
process.stdout.write('__PROBE_JSON__' + JSON.stringify(out) + '\n');
