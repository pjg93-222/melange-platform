// Assert the workbook sheet writer against the column-order contract in
// audits/mentat_failings_20260915/contracts/raw_contribution_columns.json.
//
// Run from the mentat/ working directory:
//   node tests/test_raw_contribution_columns.cjs
//
// The builder is an ES module that reads a payload at import time, so this
// suite reads it as TEXT and asserts the two literals that fix sheet column
// order: rawHeaders, and the records.map row projection that fills them.
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(PROJECT_ROOT, '..');
const CONTRACT = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'audits',
  'mentat_failings_20260915', 'contracts', 'raw_contribution_columns.json'), 'utf8'));
const BUILDER = path.join(PROJECT_ROOT, 'tools', 'crypto_workbook_builder.mjs');
const SOURCE = fs.readFileSync(BUILDER, 'utf8');
const SHEET = CONTRACT.surfaces.contributions_sheet;
const COMPACT = CONTRACT.surfaces.compact_contributions_sheet;
const ROW_BLOCK = ['employer', 'occupation', 'street_1', 'street_2', 'city', 'state', 'zipcode'];

function arrayLiteral(name) {
  const marker = `const ${name} = [`;
  const start = SOURCE.indexOf(marker);
  assert.notEqual(start, -1, `${name} not found in ${BUILDER}`);
  const end = SOURCE.indexOf('];', start);
  assert.notEqual(end, -1, `${name} is not terminated in ${BUILDER}`);
  return SOURCE.slice(start + marker.length, end);
}

function stringsIn(body) {
  return [...body.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((match) => JSON.parse(`"${match[1]}"`));
}

function blockAdjacent(names, block) {
  const positions = block.map((name) => names.indexOf(name));
  if (positions.some((index) => index < 0)) return false;
  return positions.every((index, offset) => index === positions[0] + offset);
}

// The row projection that fills rawHeaders is the one reading row.zipcode; the
// compact projection above it reads neither zipcode nor city.
function rowProjectionBody() {
  const headerStart = SOURCE.indexOf('const rawHeaders = [');
  assert.notEqual(headerStart, -1, 'rawHeaders not found');
  const marker = 'records.map((row) => [';
  const candidates = [];
  let cursor = SOURCE.indexOf(marker);
  while (cursor !== -1 && cursor < headerStart) {
    const end = SOURCE.indexOf(']);', cursor);
    assert.notEqual(end, -1, 'row projection is not terminated');
    candidates.push(SOURCE.slice(cursor + marker.length, end));
    cursor = SOURCE.indexOf(marker, cursor + marker.length);
  }
  const matching = candidates.filter((body) => body.includes('row.zipcode'));
  assert.equal(matching.length, 1,
    `expected exactly one row projection reading row.zipcode, found ${matching.length}`);
  return matching[0];
}

// Split an array-literal body on its top-level commas, so one entry is one
// sheet cell. Quoted and template text is copied over verbatim.
function splitCells(body) {
  const cells = [];
  let current = '';
  let depth = 0;
  let index = 0;
  while (index < body.length) {
    const character = body[index];
    if (character === '"' || character === "'" || character === '`') {
      current += character;
      index += 1;
      while (index < body.length) {
        if (body[index] === '\\') {
          current += body[index];
          index += 1;
          if (index < body.length) {
            current += body[index];
            index += 1;
          }
          continue;
        }
        current += body[index];
        const closing = body[index] === character;
        index += 1;
        if (closing) break;
      }
      continue;
    }
    if (character === '(' || character === '[' || character === '{') depth += 1;
    if (character === ')' || character === ']' || character === '}') depth -= 1;
    if (character === ',' && depth === 0) {
      cells.push(current);
      current = '';
      index += 1;
      continue;
    }
    current += character;
    index += 1;
  }
  cells.push(current);
  return cells.map((cell) => cell.trim()).filter((cell) => cell.length > 0);
}

// One entry per cell: the bare key for a cell that reads exactly one distinct
// row.<field>, otherwise null for a computed cell. Distinct rather than single
// because the Date cell names row.date twice, once inside a template literal.
function cellFields(body) {
  return splitCells(body).map((cell) => {
    const tokens = [...cell.matchAll(/\brow\.([A-Za-z_$][\w$]*)/g)].map((match) => match[1]);
    const distinct = [...new Set(tokens)];
    return distinct.length === 1 ? distinct[0] : null;
  });
}

test('rawHeaders matches the contract header order', () => {
  assert.deepEqual(stringsIn(arrayLiteral('rawHeaders')), SHEET.headers);
});

test('the row projection keeps the address block contiguous and cell aligned', () => {
  const fields = cellFields(rowProjectionBody());
  assert.ok(blockAdjacent(fields, ROW_BLOCK),
    `row projection block is not contiguous: found ${JSON.stringify(fields)}`);
  assert.equal(fields.length, SHEET.row_fields.length,
    'the row projection must emit exactly one cell per contract header');
  SHEET.row_fields.forEach((expected, index) => {
    if (expected.endsWith('_computed')) {
      assert.equal(fields[index], null,
        `cell ${index} (${SHEET.headers[index]}) must stay a computed expression`);
    } else {
      assert.equal(fields[index], expected,
        `cell ${index} (${SHEET.headers[index]}) must read row.${expected}`);
    }
  });
});

test('compactRawHeaders still drops every geography column by design', () => {
  const compact = stringsIn(arrayLiteral('compactRawHeaders'));
  for (const header of COMPACT.must_not_contain) {
    assert.ok(!compact.includes(header), `compactRawHeaders must not carry ${header}`);
  }
  for (const header of ['Employer', 'Occupation']) {
    assert.ok(compact.includes(header), `compactRawHeaders must keep ${header}`);
  }
});

test('negative control: a shuffled header block is rejected', () => {
  const shuffled = SHEET.headers.slice();
  const start = shuffled.indexOf(SHEET.header_block[0]);
  assert.notEqual(start, -1, 'contract header_block must appear in the contract headers');
  // The pre-contract layout: geography first, employer and occupation after it.
  shuffled.splice(start, SHEET.header_block.length,
    'City', 'State', 'ZIP', 'Employer', 'Occupation', 'Street 1', 'Street 2');
  assert.equal(blockAdjacent(shuffled, SHEET.header_block), false,
    'blockAdjacent accepted a shuffled block and is not discriminating');
  assert.equal(blockAdjacent(SHEET.headers, SHEET.header_block), true,
    'blockAdjacent rejected the contract order and is over-strict');
});
