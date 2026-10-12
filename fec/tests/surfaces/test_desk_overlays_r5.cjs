// Member desk overlays, round 5 audit fixes (mel-desk): shell focus ring (F2), Coalition party split (F3),
// profile Engagement card (F4), and the board-proportion pins for Compare, Profile, Coalition, Efficiency and the head.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const CLEAN = path.join(__dirname, '../../surfaces/approved/clean');
// CRLF is folded to LF, so a checkout with core.autocrlf=true reads the same text as the LF sources.
const read = name => fs.readFileSync(path.join(CLEAN, name), 'utf8').replace(/\r\n/g, '\n');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const text = html => html.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&');

// every CSS rule as [selector, body], comments stripped, @media blocks flattened
function rules(css){
 const out = [], src = css.replace(/\/\*[\s\S]*?\*\//g, '');
 const re = /([^{}]+)\{([^{}]*)\}/g; let m;
 while ((m = re.exec(src))) out.push([m[1].trim().replace(/^@media[^{]*\{\s*/, ''), m[2]]);
 return out;
}

test('F2: every :focus-visible rule in the shell stylesheet draws the amber accent, never the purple active token', () => {
 const css = read('app.css');
 const focus = rules(css).filter(([sel, body]) => /:focus-visible/.test(sel) && /outline\s*:/.test(body));
 assert.ok(focus.length >= 1, 'the blanket shell rule exists');
 for (const [sel, body] of focus){
  assert.doesNotMatch(body, /var\(--active\)/, `${sel} must not ring in --active`);
  assert.match(body, /outline\s*:\s*2px solid var\(--(ml-)?accent\)/, `${sel} rings in the accent`);
 }
 // the blanket rule (the one the left rail, wordmark, skip link and tray names fall through to) is the accent
 assert.ok(focus.some(([sel]) => sel.split(/\s*,\s*/).includes(':focus-visible')), 'a bare :focus-visible rule is present');
 // the shell's --accent is the kit's --ml-accent (#eca91c), so the rail ring and every kit ring are one colour
 const accent = css.match(/--accent:\s*(#[0-9a-f]{6})/i)[1].toLowerCase();
 const kitAccent = read('ui-kit.css').match(/--ml-accent:\s*(#[0-9a-f]{6})/i)[1].toLowerCase();
 assert.equal(accent, kitAccent);
});

test('F3: the Coalition party split keeps its spaces and widens the gaps around the letters and dots', () => {
 const js = read('desk-coalition.js');
 const line = js.split('\n').find(l => l.includes('>Party split</dt>'));
 assert.ok(line, 'party split stat row present');
 const tpl = line.trim();
 const render = new Function('dd', 'rr', 'ii', 'return `' + tpl.replace(/`/g, '\\`') + '`;');
 const html = render(3, 2, 0);
 assert.equal(text(html.match(/<dd[^>]*>([\s\S]*?)<\/dd>/)[1]), '3 D · 2 R · 0 I', 'textContent reads "3 D · 2 R · 0 I" (prompt 07)');
 assert.match(html, /<dd class="dcb-split">/);
 assert.equal((html.match(/class="dcb-split__sep"/g) || []).length, 2, 'both separators are spaced spans');
 const css = rules(read('desk-coalition.css'));
 const sep = css.find(([sel]) => sel === '.dcb-split__sep');
 assert.ok(sep && /padding\s*:\s*0\s+\.?\d*\.?\d+em/.test(sep[1]) && !/padding\s*:\s*0\s+0/.test(sep[1]), 'separator has horizontal padding');
 const dd = css.find(([sel]) => sel === '.dcb-stat dd.dcb-split');
 assert.ok(dd && /word-spacing\s*:\s*\.?\d/.test(dd[1]), 'word spacing widened');
 assert.ok(!/letter-spacing/.test(dd[1]), 'not letter-spaced (prompt 07 Don\'t-copy)');
});

// A small stand-in DOM for engagementCard(): the card's innerHTML decides which of its parts start hidden.
function fakeCard(){
 const card = {hidden: false, className: '', attrs: {}, parts: {}, html: '', classList: {toggle(){}}};
 card.setAttribute = (k, v) => { card.attrs[k] = v; };
 Object.defineProperty(card, 'innerHTML', {set(h){ card.html = h; card.parts = {}; }, get(){ return card.html; }});
 const tagFor = sel => {
  const re = sel === 'dl' ? /<dl\b[^>]*>/ : new RegExp('<[a-z]+\\b[^>]*class="[^"]*\\b' + sel.slice(1).replace(/[-_]/g, m => '\\' + m) + '\\b[^"]*"[^>]*>');
  const m = card.html.match(re); return m && m[0];
 };
 card.querySelector = sel => {
  if (card.parts[sel]) return card.parts[sel];
  const tag = tagFor(sel); if (!tag) return null;
  const el = {tag, hidden: /\shidden(\s|>|=)/.test(tag), attrs: {}, listeners: {}, textContent: '', innerHTML: '', classList: {add(){}, toggle(){}}};
  el.setAttribute = (k, v) => { el.attrs[k] = v; };
  el.addEventListener = (t, f) => { el.listeners[t] = f; };
  el.querySelector = () => null; el.querySelectorAll = () => []; el.append = () => {}; el.focus = () => {};
  return (card.parts[sel] = el);
 };
 return card;
}
function engagement(withForm){
 const js = read('desk-profile.js');
 const start = js.indexOf('function engagementCard(');
 const end = js.indexOf('\n}\n', start);
 assert.ok(start > 0 && end > start, 'engagementCard found');
 const src = js.slice(start, end + 2);
 const card = fakeCard();
 const document = {createElement: () => card};
 const make = new Function('document', 'esc', 'shellUrl', 'tracker', 'lastContact', src + '\nreturn engagementCard;');
 const fn = make(document, esc, (view, q) => `/?view=${view}&m=${encodeURIComponent(q.m)}&tab=${q.tab}`, () => ({}), () => '');
 const trackcard = withForm ? {className: '', removeAttribute(){}, querySelectorAll: () => []} : null;
 fn({name: 'Alex Padilla'}, trackcard);
 return card;
}

test('F4: the profile Engagement card shows Edit alone (board #29); Notes & follow-up opens inside the Edit flow', () => {
 const card = engagement(true);
 const edit = card.querySelector('.mlp-edit'), notes = card.querySelector('.mlp-eng__notes'), form = card.querySelector('.mlp-eng__form');
 assert.ok(notes, 'the Notes & follow-up link is still rendered (capability kept)');
 assert.match(notes.tag, /href="\/\?view=profile&amp;m=Alex%20Padilla&amp;tab=notes"/, 'it still opens the notes page');
 assert.equal(edit.hidden, false, 'Edit shows');
 assert.equal(notes.hidden, true, 'the link is not on the card at rest');
 edit.listeners.click();
 assert.equal(form.hidden, false, 'Edit opens the tracker form in place');
 assert.equal(notes.hidden, false, 'the link shows beside Done while the form is open');
 assert.equal(edit.textContent, 'Done');
 edit.listeners.click();
 assert.equal(notes.hidden, true, 'closing the form hides it again');
 assert.equal(edit.textContent, 'Edit');
 const bare = engagement(false);
 assert.equal(bare.querySelector('.mlp-edit').hidden, true, 'no tracker form: no Edit');
 assert.equal(bare.querySelector('.mlp-eng__notes').hidden, false, 'no tracker form: the link takes Edit\'s place, so notes stay reachable');
 const css = rules(read('desk-profile.css'));
 assert.ok(css.some(([sel, body]) => sel === '.mlp-eng__notes[hidden]' && /display\s*:\s*none/.test(body)));
});

test('profile stance rank keeps its text while its separator is spaced', () => {
 const js = read('desk-profile.js');
 const m = js.match(/<span class="mlp-area__rank">\$\{([^`]*?)\}<\/span><\/button>/);
 assert.ok(m, 'rank cell expression found');
 const html = new Function('a', 'esc', 'return ' + m[1] + ';')({rankText: '#1 of 390 · 12 actions'}, esc);
 assert.equal(text(html), '#1 of 390 · 12 actions');
 assert.match(html, /<span class="mlp-ranksep"> · <\/span>/);
 const noSep = new Function('a', 'esc', 'return ' + m[1] + ';')({rankText: '#12'}, esc);
 assert.equal(noSep, '#12');
});

test('board proportions: head Compare button, Compare grid, profile header, Coalition table, Efficiency tier', () => {
 const kit = rules(read('ui-kit.css'));
 assert.ok(kit.some(([sel, body]) => sel === '.mlk-pagehead:not(#mlk) .mlk-comparebtn' && /min-width\s*:\s*116px/.test(body)), 'head Compare button is 116px');
 const cmpJs = read('desk-compare.js');
 assert.match(cmpJs, /dc-colhead__sub">\$\{esc\(m\.chamber \|\| DASH\)\}<span class="dc-sep"> · <\/span>\$\{esc\(place\(m\)\)\}/, 'column head sub-line spaces its dot');
 const cmp = rules(read('desk-compare.css'));
 const get = sel => (cmp.find(([s]) => s === sel) || [])[1] || '';
 assert.match(get('#view-compare .dc-select:not(#mlk)>select'), /field-sizing\s*:\s*content/, 'Baseline sizes to its chosen option');
 assert.match(get('#view-compare .dc-select:not(#mlk)'), /min-width\s*:\s*161px/);
 assert.match(get('#view-compare .dc-bar.sbar'), /height\s*:\s*8px/);
 assert.match(get('#view-compare .dc-grid .mlk-pill:not(#mlk)::before'), /width\s*:\s*9px/);
 assert.doesNotMatch(read('desk-compare.css'), /#[0-9a-f]{3,6}\b/i, 'no colour literal typed in the Compare overlay');
 const prof = rules(read('desk-profile.css'));
 const pget = sel => (prof.find(([s]) => s === sel) || [])[1] || '';
 assert.match(pget('.mlp-name'), /40px\/44px/);
 assert.match(pget('.mlp-fig__v'), /27px\/30px/);
 const coal = rules(read('desk-coalition.css'));
 const cget = sel => (coal.find(([s]) => s === sel) || [])[1] || '';
 assert.match(cget('.dcb-head'), /margin\s*:\s*0 0 1px/, 'title row at board #35\'s height');
 const widths = ['chamber', 'rank', 'bills', 'reach', 'covered', 'score', 'add'].map(k => Number((cget('.dcb-table col.dcb-w-' + k).match(/width\s*:\s*(\d+)px/) || [])[1]));
 assert.deepEqual(widths, [93, 82, 110, 100, 86, 65, 84]);
 const shell = rules(read('desk-shell.css'));
 assert.ok(shell.some(([sel, body]) => sel === 'body.dsk-v2:not(.dsk-view-ranked) .dsk-bar .mlk-anchor:not(#mlk)' && /display\s*:\s*none/.test(body)),
  'the Notes and overrides menu is board #27\'s (Members) only; #31 #33 #34 #35 draw the tab row without it');
 assert.match(read('desk-shell.js'), /body\.classList\.add\('dsk-v2', 'dsk-view-' \+ VIEW\)/, 'the frame body carries dsk-view-<deskview>');
 const eff = rules(read('desk-efficiency.css'));
 assert.ok(eff.some(([sel, body]) => sel === '.eff-table .mlk-tier:not(#mlk)' && /min-width\s*:\s*34px/.test(body) && /height\s*:\s*20px/.test(body)));
});

// fix2 D-2 (r5 re-audit): the boards end the content column at x 1420.7-1423.9 of 1440 (mode 1422.3); the build ended it at
// 1416. The left edge already matched (192 against 193.4-194.5; the 168px rail stays, prompt 01), so only the end gutter moves.
const boxSides = v => { const p = v.replace(/!important/, '').trim().split(/\s+/); return p.length === 1 ? [p[0], p[0], p[0], p[0]]
 : p.length === 2 ? [p[0], p[1], p[0], p[1]] : p.length === 3 ? [p[0], p[1], p[2], p[1]] : p; };   // [top, right, bottom, left]
test('fix2 D-2: an 18px end gutter closes the content column wherever the 24px gutter used to, left edge unchanged', () => {
 const kit = read('ui-kit.css');
 assert.match(kit, /--ml-gutter:24px;--ml-gutter-end:18px;/);
 assert.match(kit, /@media \(max-width:760px\)\{:root\{--ml-gutter-end:var\(--ml-gutter\)\}\}/, 'phone width: equal gutters');
 const files = ['app.css', 'ui-kit.css', 'champions-ui.css', 'desk-profile.css', 'desk-shell.css', 'industry-ui.css', 'interactions-ui.css',
  'restored.css', 'people-ui.css', 'network.css'];
 let ends = 0;
 for (const f of files) for (const [sel, body] of rules(read(f))) {
  for (const m of body.matchAll(/(?:^|;)\s*(padding|margin)\s*:\s*([^;]+)/g)) {
   const [, right, , left] = boxSides(m[2]);
   assert.notEqual(right, 'var(--ml-gutter)', `${f} ${sel}: the right side uses the end gutter`);
   if (right === 'var(--ml-gutter-end)') { ends++; assert.equal(left, 'var(--ml-gutter)', `${f} ${sel}: the left side keeps the 24px gutter`); }
  }
  assert.doesNotMatch(body, /(?:^|;)\s*right\s*:\s*var\(--ml-gutter\)/, `${f} ${sel}: fixed elements end at the end gutter`);
 }
 assert.ok(ends >= 14, `every content column edge moved (${ends})`);
 const app = rules(read('app.css')), aget = sel => (app.findLast(([s, b]) => s === sel && /--ml-gutter/.test(b)) || [])[1] || '';
 assert.match(aget('#content'), /padding:0 var\(--ml-gutter-end\) var\(--ml-gutter\) var\(--ml-gutter\)/);
 assert.match(aget('#content.embedded>.mlk-pagehead'), /margin:0 var\(--ml-gutter-end\) 0 var\(--ml-gutter\)/);
 assert.match(kit, /\.mlk-tray:not\(#mlk\)\{[^}]*left:calc\(var\(--rail,0px\) \+ var\(--ml-gutter\)\);right:var\(--ml-gutter-end\)/);
 assert.match(read('research.js'), /padding:14px var\(--ml-gutter-end\) 40px var\(--ml-gutter\)/, 'the Industry skeleton lines up with the page it stands in for');
 const ppl = rules(read('people-ui.css')), pget = sel => (ppl.findLast(([s, b]) => s === sel && /--ml-gutter/.test(b)) || [])[1] || '';
 assert.match(pget('body.unified-network #app>header'), /padding:0 var\(--ml-gutter-end\) 0 var\(--ml-gutter\)/);
 assert.match(pget('#ppl-directory'), /padding:12px var\(--ml-gutter-end\) 24px var\(--ml-gutter\)/);
 const net = rules(read('network.css')), nget = sel => (net.findLast(([s, b]) => s === sel && /--ml-gutter/.test(b)) || [])[1] || '';
 assert.match(nget('html.mlk-v8 #bar'), /padding:14px var\(--ml-gutter-end\) 14px var\(--ml-gutter\)/);
});
