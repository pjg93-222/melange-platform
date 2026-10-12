/* S11, contribution-studio.html records ("FEC evidence"): an ActBlue or WinRed
   record links its whole FEC report (FEC has no page image for those filings),
   read with one narrow second records request; every other row keeps its
   page-image link. Paul's ruling 2026-09-24: console-side, one link per row.
   Run: node --test mentat/app/tests/contribution-studio-fec-links.test.js */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const ui = require(path.join(__dirname, '..', 'contribution-studio.js'));
const scope = require(path.join(__dirname, '..', 'scope.js'));

const ACTBLUE = {source_id: '4070720261523748648', committee_id: 'C00401224',
  source_url: 'https://docquery.fec.gov/cgi-bin/fecimg/?202606209870960511'};

test('S11: an ActBlue or WinRed record links its FEC report once its file number is read', () => {
  assert.deepEqual(ui.filingLink(ACTBLUE, undefined), {pending: true});
  const link = ui.filingLink(ACTBLUE, '1986105');
  assert.equal(link.href, 'https://docquery.fec.gov/cgi-bin/forms/C00401224/1986105/');
  assert.equal(link.text, 'FEC report');
  assert.equal(link.title, 'FEC has no page image for this filing');
  // The shared FEC link gate (scope.js) accepts the report URL: https, docquery, no query.
  assert.equal(scope.fecLink(link.href), link.href);
  assert.equal(ui.filingLink({...ACTBLUE, committee_id: ' c00694323 '}, 2009053).href,
    'https://docquery.fec.gov/cgi-bin/forms/C00694323/2009053/');
  // No number, a conflicting list of numbers, or nonsense: no link, never the dead image.
  for (const bad of [null, '', '0', '["1671120","1686601"]', 'abc', '1671120/../x'])
    assert.equal(ui.filingLink(ACTBLUE, bad), null, String(bad));
  assert.equal(ui.filingLink({...ACTBLUE, committee_id: 'C00004812'}, '1685294'), null,
    'a PAC row is not a conduit row and keeps its page-image link');
});

test('S11: the report numbers come from one narrow records request for the page\'s conduit rows', () => {
  const query = {dataset: 'Banking', basis: 'receipt', dimensions: ['company'], measures: ['amount'],
    filters: [{field: 'cycle', op: 'eq', value: '2025-2026'}], aggregate_filters: [], sort: [], limit: 100, offset: 200};
  const rows = [{source_id: '1', committee_id: 'C00401224'}, {source_id: '2', committee_id: 'C00004812'},
    {source_id: '3', committee_id: 'c00694323'}, {source_id: '1', committee_id: 'C00401224'}];
  const narrow = ui.reportNumberQuery(query, rows);
  assert.deepEqual(narrow.dimensions, ['reported_file_num']);
  assert.deepEqual(narrow.filters, [{field: 'cycle', op: 'eq', value: '2025-2026'},
    {field: 'source_id', op: 'in', value: ['1', '3']}]);
  assert.deepEqual([narrow.limit, narrow.offset, narrow.dataset, narrow.basis], [2, 0, 'Banking', 'receipt']);
  assert.deepEqual(narrow.aggregate_filters, []);
  assert.equal(query.filters.length, 1, 'the page query itself is not changed');
  assert.equal(ui.reportNumberQuery(query, [{source_id: '2', committee_id: 'C00004812'}]), null,
    'a page with no conduit rows makes no second request');
});
