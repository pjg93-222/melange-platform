// S4: assert the real buildContributorDetail() producer in
// mentat/tools/crypto_workbook_builder.mjs against the contributor_detail_sheet
// surface of audits/mentat_failings_20260915/contracts/raw_contribution_columns.json.
//
// Run from the mentat/ working directory:
//   node tests/test_contributor_detail_header.cjs
//
// crypto_workbook_builder.mjs is a top-level CLI script (reads process.argv and
// an artifact-tool workbook at import time; see tests/test_raw_contribution_columns.cjs's
// header comment), so it cannot be `import`-ed directly in a test process. The
// smallest real entry point is the function body itself: this suite extracts
// buildContributorDetail and the small real helpers its body calls
// (titleBlock, styleTable, detailPlaceholder, detailSourceNote, excelColumn,
// plus the colors/currencyFormat/countFormat constants they read) VERBATIM
// from the live source text and runs them, unmodified, in a vm context. The
// only stand-in is the worksheet object buildContributorDetail asks for via
// workbook.worksheets.getItem(...): a minimal recording stub that captures
// whatever range values the real code writes, the same shape artifact-tool's
// real worksheet exposes (getRange().values / .format, tables.add, freezePanes).
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(PROJECT_ROOT, '..');
const CONTRACT = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'audits',
  'mentat_failings_20260915', 'contracts', 'raw_contribution_columns.json'), 'utf8'));
const SURFACE = CONTRACT.surfaces.contributor_detail_sheet;

const BUILDER = path.join(PROJECT_ROOT, 'tools', 'crypto_workbook_builder.mjs');
const SOURCE = fs.readFileSync(BUILDER, 'utf8');

// Scan forward from `start`, skipping over quoted/backtick strings and // line
// comments, tracking bracket depth, and returning the index one PAST the
// character that first brings depth back to zero (relative to `start`).
function findBalancedEnd(start, openChar, closeChar, stopAtSemicolon) {
  let depth = 0;
  let index = start;
  while (index < SOURCE.length) {
    const ch = SOURCE[index];
    if (ch === '"' || ch === "'" || ch === '`') {
      const quote = ch;
      index += 1;
      while (index < SOURCE.length) {
        if (SOURCE[index] === '\\') { index += 2; continue; }
        if (SOURCE[index] === quote) { index += 1; break; }
        index += 1;
      }
      continue;
    }
    if (ch === '/' && SOURCE[index + 1] === '/') {
      index = SOURCE.indexOf('\n', index);
      if (index === -1) index = SOURCE.length;
      continue;
    }
    if (ch === openChar) depth += 1;
    if (ch === closeChar) {
      depth -= 1;
      if (depth === 0 && !stopAtSemicolon) return index + 1;
    }
    if (stopAtSemicolon && ch === ';' && depth === 0) return index + 1;
    index += 1;
  }
  throw new Error(`unbalanced source starting at ${start}`);
}

// Extract a real `function <name>(...) { ... }` declaration, verbatim.
function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = SOURCE.indexOf(marker);
  assert.notEqual(start, -1, `${marker} not found in ${BUILDER}`);
  const braceStart = SOURCE.indexOf('{', start);
  const end = findBalancedEnd(braceStart, '{', '}', false);
  return SOURCE.slice(start, end);
}

// Extract a real `const <name> = ...;` statement, verbatim (handles both a
// brace object literal like `colors` and a bare string literal like
// `currencyFormat`, whose value itself contains semicolons).
function extractConstStatement(name) {
  const marker = `const ${name} = `;
  const start = SOURCE.indexOf(marker);
  assert.notEqual(start, -1, `${marker} not found in ${BUILDER}`);
  const end = findBalancedEnd(start, '(', ')', true);
  return SOURCE.slice(start, end);
}

const REAL_SOURCE = [
  extractConstStatement('colors'),
  extractConstStatement('currencyFormat'),
  extractConstStatement('countFormat'),
  extractFunction('excelColumn'),
  extractFunction('titleBlock'),
  extractFunction('styleTable'),
  extractFunction('detailPlaceholder'),
  extractFunction('detailSourceNote'),
  extractFunction('buildContributorDetail'),
].join('\n');

// A minimal recording worksheet: real code writes through
// sheet.getRange(ref).values / .format and sheet.tables.add(...); this stub
// captures those writes (keyed and cached by range reference, the same way a
// real worksheet returns a stable range object for a given ref) without
// rendering anything.
function makeRecordingSheet() {
  const ranges = new Map();
  function getRange(ref) {
    if (!ranges.has(ref)) {
      ranges.set(ref, {format: {}, conditionalFormats: {add(){}, addCustom(){}}, merge(){}});
    }
    return ranges.get(ref);
  }
  return {
    showGridLines: true,
    getRange,
    tables: {add: (ref, hasHeaders, name) => ({name, style: null, showHeaders: null, showFilterButton: null})},
    freezePanes: {freezeRows(){}},
    __ranges: ranges,
  };
}

// Values built inside the vm sandbox are arrays from a different realm; the
// content is identical but node:assert/strict's deepEqual treats a foreign
// Array as "same structure but not reference-equal" and fails regardless of
// content. Rebuild with this (host-realm) Array before asserting.
function toHostArray(value) {
  if (Array.isArray(value)) return Array.from(value, toHostArray);
  return value;
}

// Run the real, verbatim buildContributorDetail() against a synthetic payload
// and return the [headers, ...bodyRows] 2-D array it wrote to the sheet.
function runBuildContributorDetail(contributorDetailRows) {
  const sheet = makeRecordingSheet();
  const sandbox = {
    payload: {metadata: {detail_row_cap: 500}, contributor_detail: contributorDetailRows},
    sourceSha256: 'TEST-SENTINEL-SHA-256',
    cycleLabelByNumber: new Map([[2026, '2025-2026']]),
    workbook: {worksheets: {getItem: () => sheet}},
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${REAL_SOURCE}\nbuildContributorDetail();`, sandbox, {filename: 'buildContributorDetail-harness.js'});
  const written = [...sheet.__ranges.entries()]
    .filter(([ref, range]) => ref.startsWith('A5:') && Array.isArray(range.values) && range.values.length > 1)
    .map(([, range]) => range.values);
  assert.equal(written.length, 1,
    `expected exactly one A5:* table-body write, found ${written.length}`);
  return toHostArray(written[0]);
}

const ROWS = [
  {
    cycle: 2026, cycle_label: '2025-2026', company: 'Acme Co',
    employer: 'Acme Employer', occupation: 'Engineer',
    street_1: '123 Main St', street_2: 'Suite 4',
    city: 'Springfield', state: 'IL', zip5: '62704',
    total_giving: 500, gift_count: 2, contributors: 2, folded_rows: 0,
  },
  {
    // street_1 intentionally omitted: negative control for the '' coalesce.
    cycle: 2026, cycle_label: '2025-2026', company: 'Acme Co',
    employer: 'Beta Employer', occupation: 'Analyst',
    street_2: '',
    city: 'Chicago', state: 'IL', zip5: '60601',
    total_giving: 100, gift_count: 1, contributors: 1, folded_rows: 1,
  },
];

test('buildContributorDetail header matches the contract required_headers exactly', () => {
  const [headers] = runBuildContributorDetail(ROWS);
  assert.deepEqual(headers, SURFACE.required_headers);
});

test('negative control: a header with the two Street columns removed does not equal the produced header', () => {
  const [headers] = runBuildContributorDetail(ROWS);
  const withoutStreet = SURFACE.required_headers.filter((h) => h !== 'Street 1' && h !== 'Street 2');
  assert.notDeepEqual(withoutStreet, headers,
    'a header missing the Street columns must not equal the real producer output, or the exact-match assertion above is not discriminating');
});

test("a row lacking street_1 yields '' in the Street 1 cell, not undefined", () => {
  const [headers, ...body] = runBuildContributorDetail(ROWS);
  const streetOneIndex = headers.indexOf('Street 1');
  assert.notEqual(streetOneIndex, -1, 'Street 1 must be a header');
  // ROWS[1] (Beta Employer, no street_1) sorts second: lower total_giving.
  const betaRow = body.find((row) => row[headers.indexOf('Employer')] === 'Beta Employer');
  assert.ok(betaRow, 'the Beta Employer row must be present in the output');
  assert.notEqual(betaRow[streetOneIndex], undefined, 'a missing street_1 must not surface as undefined');
  assert.equal(betaRow[streetOneIndex], '', "a missing street_1 must coalesce to ''");
});

test('the row block stays cell-aligned with the header for every row', () => {
  const [headers, ...body] = runBuildContributorDetail(ROWS);
  for (const row of body) {
    assert.equal(row.length, headers.length, 'every row must have one cell per header');
  }
});
