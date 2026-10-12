/* Execute mentat-pocket.html's script against a minimal DOM stub and drive
 * every scope the page can reach: 8 industries x (all companies + 10 companies)
 * x 4 cycle options x 3 measures, each in chart and table mode, clicking every
 * mark. Asserts no exception and that the headline tile agrees with a value
 * computed here, independently of the page's own arithmetic.
 *
 * Run: node render_harness.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const PAGE = process.env.POCKET_PAGE || path.join(HERE, 'mentat-pocket.html');
const page = fs.readFileSync(PAGE, 'utf8');
const script = page.split('<script>')[1].split('</script>')[0];
const DATA = process.env.POCKET_DATA || path.join(HERE, 'pocket-data.json');
const snapshot = JSON.parse(fs.readFileSync(DATA, 'utf8'));

/* ------------------------------------------------------------- DOM stub */
let nodeCount = 0;
function makeNode(tag){
  const node = {
    tagName: String(tag).toUpperCase(),
    _tag: String(tag).toLowerCase(),
    attrs: Object.create(null),
    children: [],
    listeners: Object.create(null),
    style: {},
    _text: '',
    value: '',
    type: '',
    id: ''
  };
  nodeCount++;
  Object.defineProperty(node, 'className', {
    get(){ return node.attrs['class'] || ''; },
    set(value){ node.attrs['class'] = String(value); }
  });
  Object.defineProperty(node, 'textContent', {
    get(){
      if (node.children.length === 0) return node._text;
      return node.children.map(child => child.textContent).join('');
    },
    set(value){ node._text = String(value); node.children = []; }
  });
  node.setAttribute = (key, value) => { node.attrs[key] = String(value); };
  node.getAttribute = key => (key in node.attrs ? node.attrs[key] : null);
  node.appendChild = child => { node.children.push(child); return child; };
  node.addEventListener = (name, handler) => {
    (node.listeners[name] = node.listeners[name] || []).push(handler);
  };
  node.dispatch = (name, event) => {
    (node.listeners[name] || []).forEach(handler => handler(event || { target: node }));
  };
  node.descendants = function*(){
    for (const child of node.children){ yield child; yield* child.descendants(); }
  };
  node.matches = selector => matches(node, selector);
  node.querySelector = selector => {
    for (const child of node.descendants()) if (matches(child, selector)) return child;
    return null;
  };
  node.querySelectorAll = selector => {
    const found = [];
    for (const child of node.descendants()) if (matches(child, selector)) found.push(child);
    found.item = i => found[i];
    return found;
  };
  return node;
}

function matches(node, selector){
  /* Only the three selector shapes the page actually uses. */
  if (selector === '.toggle') return (node.attrs['class'] || '').split(/\s+/).includes('toggle');
  if (selector === 'g[role="button"]') return node._tag === 'g' && node.attrs.role === 'button';
  if (selector === 'rect.mark') {
    return node._tag === 'rect' && (node.attrs['class'] || '').split(/\s+/).includes('mark');
  }
  throw new Error('harness: unsupported selector ' + selector);
}

const IDS = ['industries','company','cycles','measures','tile-scope','tile-value','tile-under',
  'tile-means','sub-categories','plot-categories','read-categories','sub-cycles','plot-cycles',
  'read-cycles','sub-recipients','plot-recipients','read-recipients','sub-party','plot-party',
  'legend-party','read-party','note-party','foot-line','foot-caveat','tile-gap','gap-cycles'];
const byId = Object.create(null);
const root = makeNode('div');
IDS.forEach(id => { const node = makeNode('div'); node.id = id; byId[id] = node; root.appendChild(node); });
/* The four Table toggles live in the markup, not in script-built nodes. */
const toggles = ['categories','cycles','recipients','party'].map(card => {
  const button = makeNode('button');
  button.setAttribute('class', 'toggle');
  button.setAttribute('data-card', card);
  button.setAttribute('aria-pressed', 'false');
  button.textContent = 'Table';
  root.appendChild(button);
  return button;
});

const store = Object.create(null);
global.window = {
  localStorage: {
    getItem: key => (key in store ? store[key] : null),
    setItem: (key, value) => { store[key] = String(value); },
  }
};
global.document = {
  readyState: 'loading',
  listeners: Object.create(null),
  getElementById: id => {
    if (!byId[id]) throw new Error('harness: unknown id ' + id);
    return byId[id];
  },
  createElement: makeNode,
  createElementNS: (ns, tag) => makeNode(tag),
  querySelectorAll: selector => root.querySelectorAll(selector),
  addEventListener: (name, handler) => {
    (global.document.listeners[name] = global.document.listeners[name] || []).push(handler);
  }
};

/* ------------------------------------------------------- run the page */
(0, eval)(script);
(global.document.listeners['DOMContentLoaded'] || []).forEach(fn => fn());

/* ----------------------------------------------- independent expectations */
/* The harness does NOT re-implement the page's formatter -- that would only
 * prove the two copies agree. It PARSES what the page rendered and checks the
 * number back against the raw cents, inside the tolerance the chosen unit
 * allows. A wrong unit (999,999 shown as "$1,000K") fails this. */
function parseRendered(text){
  const cleaned = String(text).replace(/,/g, '');
  const match = /^(-?)\$?(\d+(?:\.\d+)?)([MK]?)$/.exec(cleaned);
  if (!match) return null;
  const sign = match[1] === '-' ? -1 : 1;
  const unit = match[3] === 'M' ? 1000000 : match[3] === 'K' ? 1000 : 1;
  return { dollars: sign * Number(match[2]) * unit, unit: match[3] || '', money: /\$/.test(cleaned) };
}

function scopeTotals(industry, companyIndex, cycleIndex){
  const scope = companyIndex < 0 ? industry.S : industry.C[companyIndex];
  let amount = 0, records = 0;
  scope.t.forEach(row => {
    if (cycleIndex >= 0 && row[0] !== cycleIndex) return;
    amount += row[1]; records += row[2];
  });
  return { amount, records };
}

function checkTile(tag, industry, companyIndex, cycleIndex, measureIndex){
  const totals = scopeTotals(industry, companyIndex, cycleIndex);
  const text = byId['tile-value'].textContent;
  const parsed = parseRendered(text);
  if (!parsed){ note(`${tag}: tile value "${text}" is not a number`); return totals; }
  if (measureIndex === 1){
    if (parsed.money || parsed.unit) note(`${tag}: receipt count rendered as money: ${text}`);
    if (parsed.dollars !== totals.records) note(`${tag}: count ${text} != ${totals.records}`);
  } else {
    const truth = measureIndex === 0
      ? totals.amount / 100
      : (totals.records ? (totals.amount / totals.records) / 100 : 0);
    const tolerance = parsed.unit === 'M' ? 50000 : parsed.unit === 'K' ? 500 : 0.5;
    if (!parsed.money) note(`${tag}: money rendered without a dollar sign: ${text}`);
    if (Math.abs(parsed.dollars - truth) > tolerance){
      note(`${tag}: ${text} is outside ${tolerance} of ${truth}`);
    }
    if (measureIndex === 2 && parsed.unit){
      note(`${tag}: an average should not be abbreviated: ${text}`);
    }
  }
  const under = byId['tile-under'].textContent;
  if (!under.startsWith(totals.records.toLocaleString('en-US'))){
    note(`${tag}: tile under ${under} does not open with ${totals.records}`);
  }
  const expectCompanies = companyIndex < 0 ? industry.co.length + ' companies' : '1 company';
  if (!under.endsWith(expectCompanies)) note(`${tag}: tile under ${under} != ...${expectCompanies}`);
  return totals;
}

/* ---------------------------------------------------------------- drive */
let combinations = 0, marksClicked = 0, emptyPlots = 0;
const problems = [];

function note(message){ problems.push(message); }

function clickNth(hostId, index){
  const host = byId[hostId];
  const button = host.children[index];
  if (!button) throw new Error('harness: no control ' + index + ' in ' + hostId);
  button.dispatch('click');
}

function everyMark(plotId){
  const groups = byId[plotId].querySelectorAll('g[role="button"]');
  groups.forEach(group => {
    group.dispatch('click');
    marksClicked++;
    const readout = byId['read-' + plotId.replace('plot-', '')];
    if (!readout.textContent || readout.textContent.length < 8){
      note(plotId + ': readout empty after selecting a mark');
    }
    group.dispatch('click'); /* deselect */
  });
  return groups.length;
}

const CARDS = ['categories','cycles','recipients','party'];

/* ------------------------------------------------- layout geometry checks
 * No browser is available, so overflow is checked against the viewBox using
 * the same character-width model the page wraps with, plus 6% slack for the
 * model's own error. A value label pushed past the right edge, a bar drawn
 * wider than the track, or a name line wider than the box all fail here. */
const CHART_W = 340;
function estimate(text, size){
  let total = 0;
  const WIDE = 'ABCDEFGHKNOPQRSUVXYZ0123456789&@%#$';
  const NARROW = 'ijlrtfI().,;:\'"!|/-';
  for (const ch of String(text)){
    if (ch === ' ') total += 0.27;
    else if (ch === 'W' || ch === 'M') total += 0.88;
    else if (NARROW.includes(ch)) total += 0.34;
    else if (WIDE.includes(ch)) total += 0.67;
    else total += 0.56;
  }
  return total * size;
}
let geometryChecks = 0;
function checkGeometry(tag, plotId){
  const svg = byId[plotId].children[0];
  if (!svg || svg._tag !== 'svg') return;
  const box = (svg.getAttribute('viewBox') || '0 0 0 0').split(/\s+/).map(Number);
  const height = box[3];
  for (const node of svg.descendants()){
    if (node._tag === 'rect' && (node.attrs['class'] || '').includes('mark')){
      const right = Number(node.attrs.x) + Number(node.attrs.width);
      const bottom = Number(node.attrs.y) + Number(node.attrs.height);
      geometryChecks++;
      if (right > CHART_W + 0.5) note(`${tag}/${plotId}: bar ends at ${right.toFixed(1)} > ${CHART_W}`);
      if (bottom > height + 0.5) note(`${tag}/${plotId}: bar bottom ${bottom.toFixed(1)} > ${height}`);
      if (Number(node.attrs.width) < 0) note(`${tag}/${plotId}: negative bar width`);
    }
    if (node._tag === 'text'){
      const size = Number(node.attrs['font-size'] || 12);
      const width = estimate(node.textContent, size) * 1.06;
      const anchor = node.attrs['text-anchor'];
      const x = Number(node.attrs.x);
      const left = anchor === 'middle' ? x - width / 2 : x;
      const right = anchor === 'middle' ? x + width / 2 : x + width;
      geometryChecks++;
      if (right > CHART_W + 0.5){
        note(`${tag}/${plotId}: text "${node.textContent}" ends at ${right.toFixed(1)} > ${CHART_W}`);
      }
      if (left < -0.5) note(`${tag}/${plotId}: text "${node.textContent}" starts at ${left.toFixed(1)}`);
      if (Number(node.attrs.y) > height + 0.5){
        note(`${tag}/${plotId}: text baseline ${node.attrs.y} below the viewBox ${height}`);
      }
    }
  }
}

/* Collection gaps. The expectations come from the RAW cells in their own words
 * (a rounded percent and comma-grouped counts), never from the page's gapNotice:
 * a gap line must name each cell the chosen company and cycle touch, name no cell
 * it does not touch, say how many more cells the check did not itemize, and be
 * absent for a report whose check passed. */
let gapScopes = 0;
const gapSeen = [];
const shortCycle = raw => {
  const found = /^(\d{4})\D+(\d{4})$/.exec(String(raw));
  return found ? found[1] + '-' + found[2].slice(2) : String(raw);
};
const grouped = n => Number(n).toLocaleString('en-US');
function checkGapLine(tag, node, industry, company, year, label){
  const gaps = industry.gp;
  const text = node.textContent;
  if (!gaps || !gaps.f){
    if (!node.hidden || text) note(`${tag}: ${label} shows a gap line for a report with no failed check: "${text}"`);
    return;
  }
  gapScopes++;
  const touched = gaps.c.filter(c => (!company || c[0] === company) && (!year || String(c[1]).slice(-4) === year));
  const lines = touched.length + (gaps.u ? 1 : (gaps.c.length ? 0 : 1));
  if (!lines){
    if (!node.hidden || text) note(`${tag}: ${label} shows a gap line where the scope touches no short cell: "${text}"`);
    return;
  }
  if (node.hidden || !text.startsWith('Coverage gap')){
    note(`${tag}: ${label} does not show the Coverage gap line: "${text}"`);
    return;
  }
  for (const c of gaps.c){
    const label_ = `${c[0]} ${shortCycle(c[1])}: `;
    const phrase = `${label_}${Math.round(c[3] / c[4] * 100)}% of FEC-reported records (${grouped(c[3])} of ${grouped(c[4])} ${c[2]})`;
    if (touched.includes(c) && !text.includes(phrase)) note(`${tag}: ${label} does not say "${phrase}"`);
    if (!touched.includes(c) && text.includes(label_)) note(`${tag}: ${label} names ${label_.trim()} though this scope does not touch it`);
  }
  if (gaps.u && !text.includes(`${grouped(gaps.u)} more short cell`)) note(`${tag}: ${label} drops the ${gaps.u} cells the check did not itemize`);
}

snapshot.ind.forEach((industry, industryIndex) => {
  clickNth('industries', industryIndex);
  for (let cycleIndex = -1; cycleIndex < industry.cyc.length; cycleIndex++){
    clickNth('cycles', cycleIndex + 1);
    for (let measureIndex = 0; measureIndex < 3; measureIndex++){
      clickNth('measures', measureIndex);
      for (let companyIndex = -1; companyIndex < industry.co.length; companyIndex++){
        byId['company'].value = String(companyIndex);
        byId['company'].dispatch('change', { target: byId['company'] });
        combinations++;

        const tag = `${industry.n}/co${companyIndex}/cy${cycleIndex}/m${measureIndex}`;
        checkTile(tag, industry, companyIndex, cycleIndex, measureIndex);
        const gapCompany = companyIndex < 0 ? '' : industry.co[companyIndex];
        checkGapLine(tag, byId['tile-gap'], industry, gapCompany,
          cycleIndex < 0 ? '' : String((industry.cyc_raw || industry.cyc)[cycleIndex]).slice(-4), 'tile gap line');
        checkGapLine(tag, byId['gap-cycles'], industry, gapCompany, '', 'cycles card gap line');
        CARDS.forEach(card => {
          const sub = byId['sub-' + card].textContent;
          if (!sub || sub.length < 20) note(`${tag}: ${card} subline too short: ${sub}`);
          const marks = byId['plot-' + card].querySelectorAll('rect.mark').length;
          if (marks === 0){
            emptyPlots++;
            /* An empty card must say so in words, never show a blank box. */
            const words = byId['plot-' + card].textContent;
            if (!/no included receipts|no recipient party|no split/i.test(words)){
              note(`${tag}: ${card} is empty and says nothing: "${words}"`);
            }
          }
        });
        CARDS.forEach(card => checkGeometry(tag, 'plot-' + card));
        everyMark('plot-categories');
        everyMark('plot-cycles');
        everyMark('plot-recipients');
        everyMark('plot-party');

        /* Table mode on, then back to charts. */
        toggles.forEach(button => button.dispatch('click'));
        CARDS.forEach(card => {
          if (byId['plot-' + card].querySelectorAll('rect.mark').length){
            note(`${industry.n}: ${card} still has marks in table mode`);
          }
        });
        toggles.forEach(button => button.dispatch('click'));
      }
    }
  }
});

/* The scopes the check itemizes, chosen the way a reader would (industry, then
 * cycle, then company), printed as the tile then says them. */
const gapNamed = new Set();
snapshot.ind.forEach((industry, industryIndex) => {
  if (!industry.gp || !industry.gp.f) return;
  clickNth('industries', industryIndex);
  clickNth('measures', 0);
  for (const cell of industry.gp.c){
    const companyIndex = industry.co.indexOf(cell[0]);
    const cycleIndex = (industry.cyc_raw || []).indexOf(cell[1]);
    const key = industry.n + '/' + cell[0] + '/' + cell[1];
    if (companyIndex < 0 || cycleIndex < 0 || gapNamed.has(key)) continue;
    gapNamed.add(key);
    clickNth('cycles', cycleIndex + 1);
    byId['company'].value = String(companyIndex);
    byId['company'].dispatch('change', { target: byId['company'] });
    checkGapLine(key, byId['tile-gap'], industry, cell[0], String(cell[1]).slice(-4), 'tile gap line');
    gapSeen.push(`gap scope  : ${industry.n} / ${cell[0]} / ${shortCycle(cell[1])} -> ${byId['tile-gap'].textContent}`);
  }
});

/* Persistence: the page must have written its scope to storage. */
const saved = store['mentat-pocket-scope-v1'];
if (!saved) note('no scope written to localStorage');
else {
  const parsed = JSON.parse(saved);
  if (typeof parsed.industry !== 'number' || typeof parsed.measure !== 'number'){
    note('persisted scope is malformed: ' + saved);
  }
}

/* Footer copy */
if (!byId['foot-line'].textContent.includes('Published included receipts')){
  note('footer does not name the basis');
}
if (!byId['foot-caveat'].textContent.includes('Economic uniqueness remains unresolved')){
  note('footer does not carry the console caveat');
}

console.log('combinations rendered : ' + combinations);
console.log('marks clicked         : ' + marksClicked);
console.log('nodes built           : ' + nodeCount);
console.log('empty plots seen      : ' + emptyPlots + ' (a scope with no receipts in a cycle)');
console.log('geometry assertions   : ' + geometryChecks);
console.log('coverage gaps checked : ' + gapScopes + ' scopes');
gapSeen.forEach(line => console.log(line));
if (problems.length){
  console.log('\nPROBLEMS (' + problems.length + '):');
  problems.slice(0, 40).forEach(message => console.log('  - ' + message));
  process.exit(1);
}
console.log('\nRENDER HARNESS PASSED');
