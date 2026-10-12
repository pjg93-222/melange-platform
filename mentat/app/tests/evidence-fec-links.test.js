/* FEC filing links on imported-workbook rows (Paul's ruling 2026-09-24:
   console-side, no rebuild, one link per row). The server resolves each row's
   sub_id to its filing (fec_citation.py) and sends `fec_link`; these tests
   drive the shipped renderers in evidence.js with the rows the server sends -
   a resolved page-image row, an ActBlue report row, and an old-server row that
   has neither - plus the S3 spend-row pointer. The "unavailable" wording for a
   row with no resolvable link stays pinned by evidence-citations.test.js.
   Run: node --test mentat/app/tests/evidence-fec-links.test.js */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'evidence.js'), 'utf8');
const context = {
  currency: value => Number(value).toLocaleString('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2}),
  escapeHtml: value => String(value).replace(/[&<>"']/g, character => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[character])
};
// Everything from sourceAnchor to just before render(), as shipped: sourceAnchor,
// filingRecords, filingLedger, filingCite, filingCitations and their helpers.
const start = source.indexOf('  const sourceAnchor = ');
const end = source.indexOf('  const render = (element, html)', start);
assert.ok(start >= 0 && end > start, 'the citation renderers exist in the shipped PAC view');
const {filingLedger, filingCite, filingCitations} = vm.runInNewContext(
  source.slice(start, end) + '\n({filingLedger, filingCite, filingCitations})', context);

const IMAGE = 'https://docquery.fec.gov/cgi-bin/fecimg/?202301319575502599';
const REPORT = 'https://docquery.fec.gov/cgi-bin/forms/C00401224/1671120/';
const TITLE = 'FEC has no page image for this filing';
// Rows exactly as the server now sends them (evidence_csv.select + fec_citation.attach).
const imageRow = {raw: {'Source ID': '4020820231723392929'}, committee_id: 'C00004812', amount: 20,
  record_id: '4020820231723392929', source_url: IMAGE,
  fec_link: {url: IMAGE, kind: 'image', text: 'FEC filing page', title: 'The FEC page image', committee_id: 'C00004812',
    file_num: '1685294', sub_id: '4020820231723392929', date: '2023-01-15', source: 'pinned'}};
const reportRow = {raw: {'Source ID': '4010420231660204345'}, committee_id: 'C00401224', amount: 25,
  record_id: '4010420231660204345', source_url: REPORT,
  fec_link: {url: REPORT, kind: 'report', text: 'FEC report', title: TITLE, committee_id: 'C00401224',
    file_num: '1671120', sub_id: '4010420231660204345', date: '2022-11-28', source: 'pinned'}};
// An older server: no fec_link, no source_url, record_id blank - for the same ActBlue row.
const oldServerRow = {raw: {'Source ID': '4010420231660204345'}, committee_id: 'C00401224', amount: 25,
  record_id: '', source_url: ''};
const anchors = html => (html.match(/<a /g) || []).length;

test('S1 donors: an ActBlue row links its FEC report, labelled as such; a PAC row keeps its page image', () => {
  const report = filingCite(reportRow);
  assert.match(report, /<a href="https:\/\/docquery\.fec\.gov\/cgi-bin\/forms\/C00401224\/1671120\/"/);
  assert.match(report, />FEC report<\/a>/);
  assert.match(report, new RegExp(`title="${TITLE}"`));
  assert.match(report, /target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(report, /fecimg|<sup>|FEC n\/a/);
  assert.equal(anchors(report), 1, 'one link per row');
  const image = filingCite(imageRow);
  assert.match(image, /FEC<sup><a href="https:\/\/docquery\.fec\.gov\/cgi-bin\/fecimg\/\?202301319575502599"/);
  assert.doesNotMatch(image, /FEC report|FEC n\/a/);
  assert.equal(anchors(image), 1, 'one link per row');
});

test('S2 source rows: the ActBlue row links its FEC report; the PAC row still opens its page image', () => {
  const report = filingLedger(reportRow);
  assert.match(report, /<a href="https:\/\/docquery\.fec\.gov\/cgi-bin\/forms\/C00401224\/1671120\/"/);
  assert.match(report, new RegExp(`title="${TITLE}">FEC report`));
  assert.doesNotMatch(report, /fecimg|View record|unavailable/);
  assert.equal(anchors(report), 1, 'one link per row');
  const image = filingLedger(imageRow);
  assert.match(image, /<a href="https:\/\/docquery\.fec\.gov\/cgi-bin\/fecimg\/\?202301319575502599"[^>]*>View record/);
  assert.doesNotMatch(image, /FEC report|unavailable/);
});

test('only a docquery report URL is ever drawn as the report link', () => {
  assert.match(filingCite({...oldServerRow, fec_link: reportRow.fec_link}), />FEC report<\/a>/);
  for (const url of ['javascript:alert(1)', 'https://example.com/cgi-bin/forms/C00401224/1/',
                     'https://docquery.fec.gov/cgi-bin/forms/C00401224/1671120/?x=1',
                     'https://docquery.fec.gov/cgi-bin/forms/C00401224/0/', IMAGE]) {
    const row = {...oldServerRow, fec_link: {url, kind: 'report', text: 'FEC report', title: TITLE}};
    assert.doesNotMatch(filingCite(row), /FEC report/, url);
    assert.doesNotMatch(filingLedger(row), /FEC report/, url);
  }
});

test('old server: an ActBlue row with no fec_link and no source_url renders exactly as today', () => {
  const cite = filingCite(oldServerRow);
  assert.equal(cite, '<span class="fec-citations fec-citations-none" title="FEC filing link unavailable">'
    + '<span aria-hidden="true">FEC n/a</span><span class="sr-only">FEC filing link unavailable</span></span>');
  assert.equal(filingLedger(oldServerRow), '<span class="muted">FEC filing link unavailable</span>');
  assert.equal(filingCitations({filings: []}), '<span class="muted">FEC filing link unavailable</span>');
});

const pointer = {note: 'Filing links are not in this workbook', url: 'https://www.fec.gov/data/committee/C00004812/',
  text: 'Committee on FEC', title: 'Filing links are not in this workbook. This opens the committee on FEC, not a filing.'};

test('S3 spend rows: a workbook without filing links says so and points at the committee on FEC', () => {
  const html = filingCitations({name: 'SMITH, JANE', amount: 5000, filings: [], filing_fallback: pointer});
  assert.match(html, /Filing links are not in this workbook/);
  assert.match(html, /<a href="https:\/\/www\.fec\.gov\/data\/committee\/C00004812\/" target="_blank" rel="noopener noreferrer"/);
  assert.match(html, />Committee on FEC /);
  assert.doesNotMatch(html, /FEC filing link unavailable/);
  assert.equal(anchors(html), 1, 'one link per row');
  // The bulk ZIP a published row carries is still not a citation.
  const zip = filingCitations({source_url: 'https://www.fec.gov/files/bulk-downloads/2022/pas222.zip',
    filing_fallback: pointer});
  assert.match(zip, /Filing links are not in this workbook/);
  assert.doesNotMatch(zip, /pas222\.zip/);
});

test('S3: real filing citations win over the pointer, and a pointer off fec.gov is refused', () => {
  assert.match(filingCitations({filings: [], filing_fallback: pointer}), /Committee on FEC/);
  const cited = filingCitations({filings: [{source_url: IMAGE, amount: 5000}], filing_fallback: pointer});
  assert.match(cited, /FEC<sup><a /);
  assert.doesNotMatch(cited, /Filing links are not in this workbook/);
  const foreign = filingCitations({filings: [], filing_fallback: {...pointer, url: 'https://example.com/'}});
  assert.equal(foreign, '<span class="muted">FEC filing link unavailable</span>');
});
