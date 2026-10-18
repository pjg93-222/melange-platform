'use strict';
/* The FEC bulk file's section of the spending panel (candidate-spending.js bulkHtml, app/bulk_oppexp.py): drawn for committees
   whose API Schedule B is not complete, labeled with the file's own date, memo rows hidden, never inside the ring; once the API
   is complete only the one line saying so is drawn. */
const test = require('node:test');
const assert = require('node:assert/strict');
const spend = require('../candidate-spending.js');

const CTX = {name: 'Garland Barr', cycle: 2024, palette: null, neutral: '#a39b92'};
const BULK = {shown: true, coverage_class: 'bulk_vintage_operating_spend', label: 'FEC bulk file, as of Oct 12, 2026',
  note: 'Not API-collected; not added to the spending figures above',
  scope: 'Operating expenditures only (Form 3 line 17, Form 3P line 23, Form 3X line 21B)', total: 2, memo_rows_hidden: 1,
  rows: [{date: '2024-06-30', payee: 'PRINT <SHOP>', amount: 250, purpose: 'PRINTING'},
    {date: '2023-02-01', payee: 'OFFICE LANDLORD', amount: 1000.5, purpose: 'RENT'}]};
const API = {status: 'ok', committee_name: 'CAMPAIGN', cycle: 2024, coverage_status: 'partial', coverage_reasons: ['A: partial'],
  total_itemized: 625, reported_total: null, coverage: null, coverage_unknown: true, positive_total: 625,
  categories: [{key: 'consulting', label: 'Consulting', amount: 625, share: 1, payments: 2}], dropped_negative: [], payees: {}, payments: {}};
const strip = html => html.replace(/sp[dk]\d+/g, '');       // per-render SVG ids

test('bulk rows: label with the source date, four simplified columns, memo rows counted as hidden', () => {
  const html = spend.bulkHtml(BULK);
  assert.match(html, /Operating expenditures · FEC bulk file, as of Oct 12, 2026/);
  assert.match(html, /Bulk coverage · Not API-collected; not added to the spending figures above/);
  assert.deepEqual([...html.matchAll(/<th scope="col"[^>]*>([^<]+)<\/th>/g)].map(m => m[1]), ['Date', 'Payee', 'Purpose as filed', 'Amount']);
  assert.match(html, /\$1,000\.50/);
  assert.match(html, /1 memo row hidden\./);
  assert.match(html, /PRINT &lt;SHOP&gt;/, 'payee text is escaped');
});

test('no recorded date: the label says so', () => {
  assert.match(spend.bulkHtml(Object.assign({}, BULK, {label: undefined})), /FEC bulk file, source date unavailable/);
});

test('empty bulk answer draws an explicit empty row, not a zero', () => {
  const html = spend.bulkHtml(Object.assign({}, BULK, {rows: [], total: 0, memo_rows_hidden: 0}));
  assert.match(html, /No operating expenditures in the bulk file for these committees/);
  assert.doesNotMatch(html, /\$0/);
});

test('API not collected (missing): the panel shows the bulk section under the not-collected card', () => {
  const html = spend.panelHtml({kind: 'missing', message: 'Spending not collected for 2023–24', body: {status: 'not_collected', bulk: BULK}}, CTX);
  assert.match(html, /Spending not collected for 2023–24/);
  assert.match(html, /data-bulk="shown"/);
});

test('API partial: everything the API draws is unchanged; bulk is one separate section added after it', () => {
  const html = strip(spend.panelHtml({kind: 'ok', body: Object.assign({}, API, {bulk: BULK})}, CTX));
  const without = strip(spend.panelHtml({kind: 'ok', body: API}, CTX));
  const section = strip(spend.bulkHtml(BULK));
  assert.equal(html.replace(section, ''), without);
  assert.ok(html.indexOf('data-bulk="shown"') > html.indexOf('flow-spend-donut'), 'bulk comes after the ring');
});

test('API complete: no bulk rows, only the line that says why', () => {
  const done = {shown: false, reason: 'api_complete', message: 'API spending is complete for every committee; FEC bulk rows are not shown'};
  const html = spend.panelHtml({kind: 'ok', body: Object.assign({}, API, {coverage_status: 'complete', bulk: done})}, CTX);
  assert.match(html, /data-bulk="api_complete"/);
  assert.doesNotMatch(html, /data-bulk="shown"/);
  assert.doesNotMatch(html, /OFFICE LANDLORD/);
});

test('no bulk key (hover answers, older servers): nothing extra is drawn', () => {
  assert.equal(spend.bulkHtml(undefined), '');
  assert.doesNotMatch(spend.panelHtml({kind: 'ok', body: API}, CTX), /data-bulk/);
});
