// FIX 4 in the phone snapshot: mentat-pocket.html words a collection gap itself (it is one offline
// file and cannot load coverage-view.js), so this holds that copy to the console's. The same gaps, the
// same company and cycle scopes, and the two must say exactly the same thing -- or both say nothing.
// Run with the glob form: node --test "mentat/app/tests/**/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const coverage = require('../coverage-view.js');
const page = fs.readFileSync(path.join(__dirname, '..', '..', 'tools', 'pocket', 'mentat-pocket.html'), 'utf8').replace(/\r\n/g, '\n');

function between(from, to) {
  const start = page.indexOf(from), end = page.indexOf(to, start + from.length);
  assert.ok(start >= 0 && end > start, `${from} ... ${to} is in the shipped pocket page`);
  return page.slice(start, end);
}
const context = vm.createContext({});
vm.runInContext(between('  function commas(n){', '  function dollars(cents){') + between('  function gapYear(value){', '  function renderGap(id, notice){') +
  '\nthis.gapNotice = gapNotice;', context);
const pocketNotice = context.gapNotice;

const CELLS = [
  ['Lumen Technologies', '2023-2024', 'contributions', 14133, 28266, 50],
  ['Comcast', '2023-2024', 'contributions', 17869, 29991, 60],
  ['Comcast', '2021-2022', 'contributions', 26441, 34014, 78],
  ['Verizon', '2021-2022', 'contributions', 23415, 27056, 87],
  ['Comcast', '2025-2026', 'contributions', 11753, 15269, 77],
  ['Comcast', '2021-2022', 'corporate', 1369, 4365, 31],
  ['Verizon', '2025-2026', 'contributions', 31654, 33964, 93],
  ['AT&T', '2021-2022', 'corporate', 7064, 8432, 84]
];
// The snapshot's compact form (extract_pocket_data.compact_gaps) and the console's catalogue form.
const pocket = (cells, more, failed = true) => ({f: failed ? 1 : 0, u: more, s: 'pinned payload export_guards', c: cells});
const console_ = (cells, more, failed = true) => ({failed, unitemized: more, source: 'pinned payload export_guards',
  cells: cells.map(([company, cycle, destination, held, reported, percent]) => ({company, cycle, destination, held, reported, percent}))});

const VARIANTS = [[CELLS, 21], [CELLS, 0], [CELLS, 1], [CELLS.slice(0, 2), 3], [[], 0], [[], 4], [CELLS.slice(4, 5), 0]];
const COMPANIES = ['', 'Comcast', 'Verizon', 'Lumen Technologies', 'AT&T', 'Frontier Communications'];
const CYCLES = [null, ['2021-2022'], ['2023-2024'], ['2025-2026'], ['2023-2024', '2025-2026'], ['2019-2020']];

test('the pocket page says what the console says, for every company and cycle it can be asked about', () => {
  let compared = 0, said = 0;
  for (const [cells, more] of VARIANTS) {
    for (const company of COMPANIES) {
      for (const cycles of CYCLES) {
        const here = pocketNotice(pocket(cells, more), company, cycles);
        const there = coverage.gapNotice([{gaps: console_(cells, more), scope: {companies: company ? [company] : null, cycles}}]);
        assert.equal(here ? here.caption : null, there ? there.caption : null, JSON.stringify({cells: cells.length, more, company, cycles}));
        assert.equal(here ? here.chip : null, there ? there.chip : null);
        compared += 1;
        said += here ? 1 : 0;
      }
    }
  }
  assert.ok(compared > 200 && said > 100 && said < compared, `${said} of ${compared} scopes say something`);
});

test('Comcast 2025-26 reads 77% of FEC-reported records, with the cells the check did not itemize', () => {
  const found = pocketNotice(pocket(CELLS, 21), 'Comcast', ['2025-2026']);
  assert.equal(found.caption, 'Comcast 2025-26: 77% of FEC-reported records (11,753 of 15,269 contributions); ' +
    "21 more short cells are not itemized in the workbook's Data Quality check");
  assert.equal(found.source, 'pinned payload export_guards');
});

test('a check that itemizes every short cell (export_guards.R no longer stops at twelve) reads the same in both copies and names them all', () => {
  const all = Array.from({length: 31}, (_, n) => [`Company ${n + 1}`, '2025-2026', 'contributions', 500, 1000 + n, Math.round(50000 / (1000 + n))]);
  const here = pocketNotice(pocket(all, 0), '', null);
  const there = coverage.gapNotice([{gaps: console_(all, 0), scope: {companies: null, cycles: null}}]);
  assert.equal(here.caption, there.caption);
  assert.equal(there.cells.length, 31);
  assert.equal(there.unitemized, 0);
  assert.equal(there.caption.split('; ').length, 31, 'one line per cell, none summarised');
  assert.doesNotMatch(there.caption, /not itemized/);
  assert.match(there.caption, /Company 31 2025-26: 49% of FEC-reported records \(500 of 1,030 contributions\)$/);
  // A build made before that change still prints the first twelve and a count: same cells, plus the sentence.
  const truncated = coverage.gapNotice([{gaps: console_(all.slice(0, 12), 19), scope: {}}]);
  assert.equal(truncated.caption, there.caption.split('; ').slice(0, 12).join('; ') +
    "; 19 more short cells are not itemized in the workbook's Data Quality check");
  assert.equal(pocketNotice(pocket(all.slice(0, 12), 19), '', null).caption, truncated.caption);
});

test('a report whose check passed, or a snapshot from before the field, says nothing', () => {
  assert.equal(pocketNotice(pocket(CELLS, 21, false), '', null), null);
  assert.equal(pocketNotice(undefined, 'Comcast', ['2025-2026']), null);
  assert.equal(pocketNotice(null, '', null), null);
});

test('a check that itemized only other cycles, with none left unitemized, says nothing in either copy', () => {
  const elsewhere = CELLS.filter(cell => cell[1] === '2023-2024');
  for (const cycles of [['2025-2026'], ['2021-2022']]) {
    assert.equal(pocketNotice(pocket(elsewhere, 0), '', cycles), null);
    assert.equal(coverage.gapNotice([{gaps: console_(elsewhere, 0), scope: {companies: null, cycles}}]), null);
  }
  // What the console sends for a cycle report scoped to a cycle the check never named (collection_gaps.scoped):
  // no failure at all, so neither copy has a "not itemized" line to print.
  assert.equal(pocketNotice(pocket([], 0, false), '', null), null);
  assert.equal(coverage.gapNotice([{gaps: console_([], 0, false), scope: {}}]), null);
  // A check that itemized nothing still says so, in both, and in the same words.
  const said = pocketNotice(pocket([], 0), '', null), there = coverage.gapNotice([{gaps: console_([], 0), scope: {}}]);
  assert.equal(said.caption, "The workbook's Data Quality check reports short collection cells that are not itemized");
  assert.equal(said.caption, there.caption);
});

test('the page shows the line on the tile (company and cycle chosen) and on the cycles card (company, every cycle)', () => {
  const code = page.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(code, /renderGap\('tile-gap', gapNotice\(industry\.gp, gapCompany\(industry\),\s*state\.cycle < 0 \? null : \[rawCycle\(industry, state\.cycle\)\]\)\);/);
  assert.match(code, /renderGap\('gap-cycles', gapNotice\(industry\.gp, gapCompany\(industry\), null\)\);/);
  assert.match(code, /<p class="gapline" id="tile-gap" hidden><\/p>/);
  assert.match(code, /<p class="gapline" id="gap-cycles" hidden><\/p>/);
});
