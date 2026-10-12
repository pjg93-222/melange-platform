const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'evidence.js'), 'utf8');
const start = source.indexOf('  const filingCitations = row => {');
const end = source.indexOf('  const render =', start);
assert.ok(start >= 0 && end > start, 'citation renderer exists in the shipped PAC view');
const filingCitations = vm.runInNewContext(source.slice(start, end) + '\nfilingCitations', {
  filingRecords: row => row.filings || [],
  currency: value => Number(value).toLocaleString('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2}),
  escapeHtml: value => String(value).replace(/[&<>"']/g, character => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[character])
});

test('the CULAC aggregate gets one numbered link per distinct FEC image', () => {
  const image = number => `https://docquery.fec.gov/cgi-bin/fecimg/?${number}`;
  const row = {filings:[
    {source_url:image('202501309748316998'), amount:178200, date:'2024-09-27'},
    {source_url:image('202501309748316997'), amount:31050, date:'2024-09-27'},
    {source_url:image('202501309748316997'), amount:190750, date:'2024-09-27'},
    {source_url:image('202401299600546010'), amount:5000, date:'2023-12-28'},
    {source_url:image('202307199583829453'), amount:5000, date:'2023-06-26'}
  ]};
  const html = filingCitations(row);
  assert.match(html, /FEC<sup>/);
  assert.equal((html.match(/<a /g) || []).length, 4);
  assert.equal((html.match(/202501309748316997/g) || []).length, 1);
  assert.match(html, /2 transactions/);
  assert.match(html, /\$31,050/);
  assert.match(html, /\$190,750/);
});

test('a missing or non-filing URL cannot masquerade as an aggregate citation', () => {
  const valid = 'https://docquery.fec.gov/cgi-bin/fecimg/?202501309748316997';
  assert.match(filingCitations({filings:[{source_url:valid}, {source_url:''}]}), /link unavailable/);
  assert.doesNotMatch(filingCitations({filings:[{source_url:valid}, {source_url:''}]}), /<a /);
  assert.match(filingCitations({source_url:'https://www.fec.gov/files/bulk-downloads/2024/pas224.zip'}), /link unavailable/);
  assert.match(filingCitations({source_url:valid}), /FEC<sup>/);
});

// Fix r2 (TC-01): board 7's one-line donor rows cite their filing compactly.
const citeStart = source.indexOf('  const FEC_RECORD_LINK =');
const citeEnd = source.indexOf('  const filingCitations = row => {', citeStart);
assert.ok(citeStart >= 0 && citeEnd > citeStart, 'the compact donor citation exists in the shipped PAC view');
const filingCite = vm.runInNewContext(source.slice(citeStart, citeEnd) + '\nfilingCite', {
  filingRecords: row => row.filings || [],
  currency: value => Number(value).toLocaleString('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2}),
  escapeHtml: value => String(value).replace(/[&<>"']/g, character => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[character])
});

test('a donor row with no FEC filing link says so on one line, to eyes and to screen readers', () => {
  const html = filingCite({raw: {}});
  assert.match(html, /fec-citations-none/);
  assert.match(html, /title="FEC filing link unavailable"/);
  assert.match(html, /<span class="sr-only">FEC filing link unavailable<\/span>/);
  assert.match(html, /<span aria-hidden="true">FEC n\/a<\/span>/);
  assert.doesNotMatch(html, /<a /);
  // A link that is not an FEC filing is not a citation either.
  assert.match(filingCite({source_url: 'https://example.com/x'}), /fec-citations-none/);
});

test('a donor row with FEC filing links numbers them, and a record without one keeps its number unlinked', () => {
  const image = number => `https://docquery.fec.gov/cgi-bin/fecimg/?${number}`;
  const one = filingCite({raw: {image_number: '202501309748316998'}});
  assert.match(one, /FEC<sup><a href="https:\/\/docquery\.fec\.gov\/cgi-bin\/fecimg\/\?202501309748316998"/);
  assert.match(one, /aria-label="View FEC record"/);
  const two = filingCite({filings: [{source_url: image('1'), amount: 40, date: '2026-01-02'}, {source_url: ''}]});
  assert.equal((two.match(/<a /g) || []).length, 1);
  assert.match(two, /title="2026-01-02 · \$40\.00"/);
  assert.match(two, /aria-label="Record 2: FEC filing link unavailable">2<\/span>/);
});
