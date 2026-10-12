/* The dwarfed tab: refunds, contradictions, large counted receipts, no totals.
   Laid out to board B16 (build 05, 2026-09-22): run strip, three tabs with row
   counts, a filter row, one table card with a 25-row pager, and the explanatory
   text behind "How this list is built". */
const assert = require('node:assert/strict');
// The page publishes the shared URL module on the window before this view's
// script runs (scope.js is in the head script list; cross-reference.js is
// loaded with the view), and the view reads the one FEC link rule from it.
// Under node nothing publishes it, so it is published here.
globalThis.MentatScope = require('../app/scope.js');
const view = require('../app/cross-reference.js');

const DIGEST = {
  basis: 'A receipt is listed as large at $1,000,000.00 or more, or at $100,000.00 or more when an individual sent it; both thresholds are a proposed default and await the owner’s ratification.',
  refunds: {
    linked: [{refund_record_id:'R1', original_record_id:'O1', company:'A Bank', cycle:'2024',
      refund_amount:-2000, original_amount:50000,
      refund_source_url:'https://docquery.fec.gov/pdf/333/three.pdf',
      original_source_url:'https://docquery.fec.gov/pdf/000/orig.pdf'}],
    linked_count: 1, linked_amount: -2000,
    unlinked: [{record_id:'U1', company:'A Bank', cycle:'2024', amount:-7500,
      description:'RETURN OF ERRONEOUS DEBIT', source_url:'https://docquery.fec.gov/pdf/222/two.pdf'}],
    unlinked_count: 1, unlinked_amount: -7500,
  },
  outliers: {
    non_giving: [{record_id:'N1', company:'A Bank', cycle:'2024', amount:15000, role:'other_receipt',
      description:'LOAN', source_url:'https://docquery.fec.gov/pdf/111/one.pdf'}],
    non_giving_count: 1, non_giving_amount: 15000,
    large: [{record_id:'L1', company:'A Bank', cycle:'2024', amount:120000, entity:'IND', role:'contribution',
      source_url:'https://docquery.fec.gov/pdf/444/four.pdf'}],
    large_count: 1, large_amount: 120000,
  },
};
const fixture = {available:true, status:'completed', run_id:'20260910-safe', selected_by:'pointer', report:{
  run_id:'20260910-safe', status:'completed', generated_at:'2026-09-10T12:00:00Z',
  scope:{industries:['Banking'], companies:['A Bank'], cycles:[2024], company_count:1},
  digest_available:true, digest:DIGEST, digest_message:'',
  limitations:['Collection coverage remains unverified.'],
}};
const on = tab => ({...view.initialState(), tab});
const rendered = view.html(fixture);
const large = view.html(fixture, on('large'));
const refunds = view.html(fixture, on('refunds'));

// The tab says what it is, and no total can be read off it.
assert.match(rendered,/The published report workbook is the accounting of record\. This view lists evidence-backed contradictions, clear refunds and unusually large counted receipts for inspection\. Size alone never excludes a receipt; this view publishes no totals\./);
for (const html of [rendered, large, refunds]) {
  assert.doesNotMatch(html,/Observed original giving/);
  assert.doesNotMatch(html,/PAC outgoing|Previous receipt total|Partisan composition|Records needing review|Collection coverage follow-up|Provisional totals/);
  assert.doesNotMatch(html,/xref-step/);
}

// Today's section headings now head the explanatory text behind "How this list
// is built", and each tab's table carries its section's name.
assert.match(rendered,/<button type="button" class="xref-disclose xref-method-toggle" id="xref-method-toggle" aria-expanded="false" aria-controls="xref-method">How this list is built/);
assert.match(rendered,/<div class="xref-method" id="xref-method" hidden>/);
assert.match(rendered,/<h3>Clear refunds<\/h3>/);
assert.match(rendered,/<h3>Contradictions and large receipts<\/h3>/);
assert.match(rendered,/<h4>Negative adjustments without a matched original<\/h4>/);
assert.match(rendered,/<h4>Evidence-backed contradictions to giving<\/h4>/);
assert.match(rendered,/<h4>Unusually large receipts retained for inspection<\/h4>/);
assert.match(rendered,/These receipts remain counted\. Magnitude alone is not a contradiction and never excludes a receipt\./);
assert.match(rendered,/<h2 class="sr-only">Evidence-backed contradictions to giving<\/h2>/);
assert.match(large,/<h2 class="sr-only">Unusually large receipts retained for inspection<\/h2>/);
assert.match(refunds,/<h2 class="sr-only">Clear refunds<\/h2>/);
assert.match(refunds,/<h3 class="xref-subhead" id="xref-unlinked-heading">Negative adjustments without a matched original<\/h3>/);

// The run strip: status pill, sector, cycles, companies and the run's time.
assert.match(rendered,/<h2 class="sr-only" id="xref-run-heading">What this run covers<\/h2>/);
assert.match(rendered,/<span class="badge xref-status">Cross-referencing complete<\/span>/);
assert.match(rendered,/<span>Banking<\/span><span>cycles 2024<\/span><span>1 company<\/span>/);
assert.match(rendered,/>run 2026-09-10 12:00<\/span>/);
assert.match(rendered,/title="Run 20260910-safe · report generated 2026-09-10T12:00:00Z · times in UTC"/);
assert.match(rendered,/id="xref-companies-toggle" aria-expanded="false" aria-controls="xref-companies">Companies in scope/);
assert.match(rendered,/<div class="xref-companies" id="xref-companies" hidden><p>A Bank<\/p>/);
// A run id minted from its start second names that time, as the board does.
const stamped = structuredClone(fixture);
stamped.run_id = '20260921T055153-885792788fa7';
assert.match(view.html(stamped),/>run 2026-09-21 05:51<\/span>/);

// Three tabs carry row counts, never money; Refunds counts matched and unmatched rows.
assert.match(rendered,/role="tablist" aria-label="Saved evidence"/);
assert.match(rendered,/id="xref-tab-contradictions" data-xref-tab="contradictions" aria-controls="xref-panel" aria-selected="true" tabindex="0"><span class="xref-tab-face"><span class="xref-tab-label">Contradictions<\/span><span class="xref-tab-count">1<\/span><\/span>/);
assert.match(rendered,/id="xref-tab-large" data-xref-tab="large" aria-controls="xref-panel" aria-selected="false" tabindex="-1"><span class="xref-tab-face"><span class="xref-tab-label">Large receipts<\/span><span class="xref-tab-count">1<\/span>/);
assert.match(rendered,/<span class="xref-tab-label">Refunds<\/span><span class="xref-tab-count">2<\/span>/);
assert.match(rendered,/class="xref-tab-face xref-tab-ghost" aria-hidden="true"/);
assert.match(large,/id="xref-tab-large" data-xref-tab="large" aria-controls="xref-panel" aria-selected="true"/);
assert.match(large,/id="xref-panel" role="tabpanel" aria-labelledby="xref-tab-large"/);

// The filter row: Company, Cycle, Description (Entity on Large receipts), search.
assert.match(rendered,/<select id="xref-filter-company" data-xref-filter="company" aria-label="Company"><option value="">Company<\/option><option value="v:A Bank">A Bank<\/option><\/select>/);
assert.match(rendered,/<select id="xref-filter-cycle" data-xref-filter="cycle" aria-label="Cycle"><option value="">Cycle<\/option><option value="v:2024">2024<\/option>/);
assert.match(rendered,/<select id="xref-filter-detail" data-xref-filter="detail" aria-label="Description"><option value="">Description<\/option><option value="v:LOAN">LOAN<\/option>/);
assert.match(large,/aria-label="Entity"><option value="">Entity<\/option><option value="v:Individual">Individual<\/option>/);
assert.match(rendered,/<input type="search" id="xref-search" data-xref-filter="search" placeholder="Search description or record ID"/);

// Headers per tab, amounts right-aligned.
const heads = html => [...html.matchAll(/<th scope="col" class="([^"]+)">([^<]+)<\/th>/g)].map(match => match[2]);
assert.deepEqual(heads(rendered), ['Company','Cycle','Description','Role','Amount removed','FEC record']);
assert.deepEqual(heads(large), ['Company','Cycle','Entity','Role','Amount','FEC record']);
assert.deepEqual(heads(refunds), ['Company','Cycle','Refund','Original','Refund record','Original record','Company','Cycle','Description','Amount','FEC record']);
assert.match(rendered,/<th scope="col" class="xref-col-amount number">Amount removed<\/th>/);
assert.match(rendered,/<td class="xref-col-amount number">\$15,000\.00<\/td>/);

// A refund pair links each side of the pair from its own record cell.
assert.match(refunds,/<span class="xref-id" title="R1">R1<\/span><a class="xref-open" href="https:\/\/docquery\.fec\.gov\/pdf\/333\/three\.pdf"[^>]*aria-label="Open the public FEC filing for record R1">/);
assert.match(refunds,/<span class="xref-id" title="O1">O1<\/span><a class="xref-open" href="https:\/\/docquery\.fec\.gov\/pdf\/000\/orig\.pdf"[^>]*aria-label="Open the public FEC filing for record O1">/);
assert.match(refunds,/rel="noopener noreferrer" referrerpolicy="no-referrer"/);
assert.match(refunds,/-\$2,000\.00/);
assert.match(refunds,/\$50,000\.00/);
assert.match(refunds,/-\$7,500\.00/);
assert.match(refunds,/RETURN OF ERRONEOUS DEBIT/);

// Contradictions and informational large rows print their evidence and size.
assert.match(rendered,/LOAN/);
assert.match(rendered,/Other receipt/);
for (const html of [rendered, large, refunds]) assert.doesNotMatch(html,/other_receipt/);
assert.match(rendered,/\$15,000\.00/);
assert.match(large,/Individual/);
assert.doesNotMatch(large,/>IND</);
assert.match(large,/\$120,000\.00/);
// An unmapped role reads as words, never as an engine key.
assert.match(large,/<td class="xref-col-role">Contribution<\/td>/);
assert.match(rendered,/both thresholds are a proposed default and await the owner’s ratification/);
assert.match(rendered,/<span class="xref-rows" id="xref-rows">Rows 1-1 of 1<\/span>/);
assert.match(refunds,/<span class="xref-rows" id="xref-rows">Rows 1-2 of 2<\/span>/);
assert.match(rendered,/id="xref-previous" data-xref-page="-1" aria-label="Previous rows" disabled>/);
assert.match(rendered,/id="xref-next" data-xref-page="1" aria-label="Next rows" disabled>/);
assert.match(rendered,/<p class="xref-capped" id="xref-capped" hidden><\/p>/);

// A capped list says how many rows the saved digest holds, and pages 25 at a time.
const many = structuredClone(fixture);
many.report.digest.outliers.non_giving = Array.from({length:200}, (_, i) => ({record_id:'N'+i, company:i % 2 ? 'B Bank' : 'A Bank', cycle:i % 3 ? '2024' : '2022', amount:100 - i/1000, role:'other_receipt', description:i === 7 ? 'DIVIDEND' : 'INTEREST'}));
many.report.digest.outliers.non_giving_count = 4778;
const manyHtml = view.html(many);
assert.match(manyHtml,/<p class="xref-capped" id="xref-capped">4,778 reclassified receipts in this run\. The 200 largest are listed here; the saved digest holds the rest\.<\/p>/);
assert.match(manyHtml,/<span class="xref-tab-count">4,778<\/span>/);
assert.match(manyHtml,/Rows 1-25 of 200/);
assert.equal([...manyHtml.matchAll(/<tr><td class="xref-col-company">/g)].length, 25);
assert.match(manyHtml,/>N0<\/span>/);
assert.match(manyHtml,/>N24<\/span>/);
assert.doesNotMatch(manyHtml,/>N25<\/span>/);
assert.match(manyHtml,/id="xref-previous" data-xref-page="-1" aria-label="Previous rows" disabled>/);
assert.doesNotMatch(manyHtml,/id="xref-next" data-xref-page="1" aria-label="Next rows" disabled>/);
const page = (state, number) => ({...state, page:{...state.page, contradictions:number}});
const second = view.html(many, page(view.initialState(), 1));
assert.match(second,/Rows 26-50 of 200/);
assert.match(second,/>N25<\/span>/);
assert.doesNotMatch(second,/>N24<\/span>/);
const last = view.html(many, page(view.initialState(), 7));
assert.match(last,/Rows 176-200 of 200/);
assert.match(last,/>N199<\/span>/);
assert.match(last,/id="xref-next" data-xref-page="1" aria-label="Next rows" disabled>/);
// A page past the end is held to the last page.
assert.match(view.html(many, page(view.initialState(), 99)),/Rows 176-200 of 200/);

// Filters narrow the active tab's rows; the pager counts what is left.
const filtered = (filters, tab = 'contradictions') => view.html(many, {...view.initialState(), tab, filters:{...view.initialState().filters, [tab]:{...view.initialState().filters[tab], ...filters}}});
assert.match(filtered({company:'v:B Bank'}),/Rows 1-25 of 100/);
assert.match(filtered({company:'v:B Bank'}),/<option value="v:B Bank" selected>B Bank<\/option>/);
assert.match(filtered({cycle:'v:2022'}),/Rows 1-25 of 67/);
assert.match(filtered({detail:'v:DIVIDEND'}),/Rows 1-1 of 1/);
assert.match(filtered({detail:'v:DIVIDEND'}),/>N7<\/span>/);
assert.match(filtered({search:'n19'}),/Rows 1-11 of 11/);
assert.match(filtered({search:'dividend'}),/>N7<\/span>/);
assert.match(filtered({search:'dividend'}),/value="dividend"/);
// A value that is not among this run's options is dropped, never silently kept.
assert.match(filtered({company:'v:Gone Bank'}),/Rows 1-25 of 200/);
// No row matching the filters is said as such, not as "none in this run".
assert.match(filtered({search:'no such receipt'}),/No contradiction matches these filters\./);
assert.match(filtered({search:'no such receipt'}),/<span class="xref-rows" id="xref-rows">No rows<\/span>/);

// Record ids are shortened in the middle, with the whole id in the tooltip.
const longId = structuredClone(fixture);
longId.report.digest.outliers.non_giving[0].record_id = 'SA11AI.4050820251189302163.ABCDEF';
assert.match(view.html(longId),/<span class="xref-id" title="SA11AI\.4050820251189302163\.ABCDEF">SA11AI\.405…63\.ABCDEF<\/span>/);

// Empty tables say what was not found rather than printing nothing.
const bare = structuredClone(fixture);
bare.report.digest = {basis:'', refunds:{linked:[], linked_count:0, unlinked:[], unlinked_count:0},
  outliers:{non_giving:[], non_giving_count:0, large:[], large_count:0}};
assert.match(view.html(bare),/<p class="xref-empty">No receipt the accepted report counted was reclassified as non-giving\.<\/p>/);
assert.match(view.html(bare, on('large')),/No counted receipt in this run reached the size stated below/);
const bareRefunds = view.html(bare, on('refunds'));
assert.match(bareRefunds,/No refund in this run is matched to the gift it reverses/);
assert.match(bareRefunds,/Every negative adjustment in this run is matched to an original gift/);
assert.match(bareRefunds,/<span class="xref-tab-count">0<\/span>/);
assert.match(bareRefunds,/No rows/);
assert.match(view.html(bare),/The size that selects these rows is unavailable/);

// A run with no digest says how to build one, and shows no tables.
const noDigest = structuredClone(fixture);
noDigest.report.digest = null;
noDigest.report.digest_available = false;
noDigest.report.digest_message = 'This run has no contradiction/refund/large-receipt digest. Build it with tools/cross_reference_digest.py <run_dir>.';
const noDigestHtml = view.html(noDigest);
assert.match(noDigestHtml,/This run has no contradiction\/refund\/large-receipt digest\. Build it with tools\/cross_reference_digest\.py &lt;run_dir&gt;\./);
assert.doesNotMatch(noDigestHtml,/xref-table|role="tablist"|xref-filters/);
assert.match(noDigestHtml,/Run 20260910-safe/);
assert.match(noDigestHtml,/publishes no totals/);

// The envelopes this view already had.
assert.match(view.html({status:'empty'}),/No run has been saved yet/);
assert.match(view.html({status:'empty'}),/publishes no totals/);
assert.match(view.html({status:'error',message:'Mismatch'}),/No totals are shown/);
assert.doesNotMatch(view.html({status:'error',message:'Mismatch'}),/\$/);
assert.match(view.html({available:true,status:'running',run_id:'active',progress:{updated_at:'2026-09-10T12:00:00Z'}}),/Evidence is being cross-referenced/);
const failed = {available:true,status:'failed',run_id:'bad',notices:[],progress:{message:'Failure'}};
assert.equal(view.html(failed),view.html(failed)); // Rendering does not mutate saved notices.
assert.match(view.html(failed),/Run failed/);

// D-040: every money figure on every tab is one row's own amount (or the
// threshold sentence that says which rows are large). The digest's *_amount
// sums are never printed, even when they differ from every row.
const sums = structuredClone(fixture);
Object.assign(sums.report.digest.outliers, {
  non_giving:[{record_id:'N1', company:'A Bank', cycle:'2024', amount:15000, role:'other_receipt', description:'LOAN'},
              {record_id:'N2', company:'A Bank', cycle:'2024', amount:2500, role:'other_receipt', description:'INTEREST'}],
  non_giving_count:2, non_giving_amount:17500,
  large:[{record_id:'L1', company:'A Bank', cycle:'2024', amount:120000, entity:'IND'}, {record_id:'L2', company:'A Bank', cycle:'2024', amount:30000, entity:'ORG'}],
  large_count:2, large_amount:150000});
Object.assign(sums.report.digest.refunds, {
  linked:[{refund_record_id:'R1', original_record_id:'O1', company:'A Bank', cycle:'2024', refund_amount:-2000, original_amount:50000},
          {refund_record_id:'R2', original_record_id:'O2', company:'A Bank', cycle:'2024', refund_amount:-1000, original_amount:9000}],
  linked_count:2, linked_amount:-3000,
  unlinked:[{record_id:'U1', company:'A Bank', cycle:'2024', amount:-7500}, {record_id:'U2', company:'A Bank', cycle:'2024', amount:-500}],
  unlinked_count:2, unlinked_amount:-8000});
for (const tab of ['contradictions', 'large', 'refunds']) {
  const html = view.html(sums, {...on(tab), method:true});
  for (const total of ['17,500.00', '150,000.00', '3,000.00', '8,000.00', '59,000.00']) assert.doesNotMatch(html, new RegExp('\\$' + total.replace(/[.,]/g, '\\$&')), tab + ' printed a sum: ' + total);
  const outside = html.replace(/<td class="[^"]*\bnumber\b[^"]*">[^<]*<\/td>/g, '').replace(/<p class="xref-basis">[^<]*<\/p>/g, '');
  assert.doesNotMatch(outside, /\$\s?\d/, tab + ' shows a money figure outside a row amount');
}

// Every value reaching the page is escaped, whichever table it lands in.
const injected = structuredClone(fixture), bad = '<img src=x onerror="alert(1)">';
injected.run_id = bad;
injected.report.scope.companies = [bad];
injected.report.digest.outliers.non_giving[0].description = '<script>alert(1)</script> RETURN OF ERRONEOUS DEBIT';
injected.report.digest.outliers.non_giving[0].role = bad;
injected.report.digest.outliers.large[0].entity = bad;
injected.report.digest.refunds.linked[0].refund_record_id = bad;
injected.report.digest.refunds.unlinked[0].company = bad;
injected.report.digest.basis = bad;
for (const tab of ['contradictions', 'large', 'refunds']) {
  const injectedHtml = view.html(injected, on(tab));
  assert.doesNotMatch(injectedHtml,/<img|<script|onerror="/, tab);
  assert.match(injectedHtml,/&lt;img/, tab);
}
assert.match(view.html(injected),/&lt;script&gt;alert\(1\)&lt;\/script&gt; RETURN OF ERRONEOUS DEBIT/);

// Only public FEC filings become links.
for (const url of ['javascript:alert(1)','https://evil.test/file','https://www.fec.gov/file?api_key=secret']) {
  const unsafe = structuredClone(fixture);
  unsafe.report.digest = {basis:'', refunds:{linked:[], linked_count:0, unlinked:[{record_id:'U1', company:'A Bank', cycle:'2024', amount:-1, source_url:url}], unlinked_count:1},
    outliers:{non_giving:[{record_id:'N1', company:'A Bank', cycle:'2024', amount:1, source_url:url}], non_giving_count:1, large:[], large_count:0}};
  for (const tab of ['contradictions', 'refunds']) {
    const html = view.html(unsafe, on(tab));
    assert.doesNotMatch(html,/href=/, tab + ' ' + url);
    assert.match(html,/<span class="xref-id" title="(U1|N1)">/);
  }
}

assert.equal(view.money(null),'Unavailable');
assert.equal(view.money('60'),'Unavailable');
assert.equal(view.money(Infinity),'Unavailable');
assert.equal(view.money(0),'$0.00');
console.log('Cross-reference view checks passed.');

// Opt-in browser integration verifies tab activation, rendering and refresh.
if (process.env.MENTAT_CROSS_REFERENCE_BROWSER === '1') (async () => {
  const {spawn} = require('node:child_process'), {once} = require('node:events'), path = require('node:path'), os = require('node:os');
  const modules = process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
  const {chromium} = require(require.resolve('playwright', {paths:[modules]}));
  const server = spawn(process.env.MENTAT_PYTHON || 'python', [path.join(__dirname,'console_browser_fixture.py')], {stdio:['pipe','pipe','pipe'],windowsHide:true});
  let browser;
  const stderr = []; server.stderr.on('data', chunk => stderr.push(chunk.toString()));
  try {
    const origin = await new Promise((resolve,reject) => {
      let output = '';
      server.stdout.on('data', chunk => { output += chunk; if (output.includes('\n')) resolve(output.trim().split('\n')[0]); });
      server.on('exit', code => reject(new Error(`Fixture exited ${code}: ${stderr.join('')}`)));
      server.on('error', reject);
    });
    browser = await chromium.launch({headless:true, channel:process.env.MENTAT_BROWSER_CHANNEL || 'chrome'});
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    page.setDefaultTimeout(20000);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    // 60 contradictions (three pages), two companies, one of them on every fifth row.
    const browserFixture = structuredClone(fixture);
    browserFixture.report.digest.outliers.non_giving = Array.from({length:60}, (_, i) => ({record_id:'N' + String(i).padStart(2, '0'), company:i % 5 ? 'A Bank' : 'Harbor Bank', cycle:'2024', amount:9000 - i, role:'other_receipt', description:i % 7 ? 'INTEREST' : 'DIVIDENDS', source_url:'https://docquery.fec.gov/pdf/111/one' + i + '.pdf'}));
    browserFixture.report.digest.outliers.non_giving_count = 60;
    let data = browserFixture, calls = 0, delay = 0;
    const answer = async route => { calls++; if (delay) await new Promise(resolve => setTimeout(resolve, delay)); return route.fulfill({json:data}); };
    await page.route('**/api/cross-reference?*', answer);
    await page.route('**/api/cross-reference', answer);
    await page.goto(origin + '/#cross-reference');
    await page.locator('#xref-panel table').waitFor();
    assert.equal(await page.locator('#tab-cross-reference').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('#view-collection').isVisible(),false);
    assert.equal(await page.locator('#view-graph').isVisible(),false);
    assert.equal(await page.locator('#view-cross-reference').getByText('Observed original giving',{exact:true}).count(),0);
    assert.equal(await page.locator('#xref-tab-contradictions').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-25 of 60');
    // The explanatory text, the LEDE among it, sits behind "How this list is built".
    assert.equal(await page.locator('#xref-method').isVisible(),false);
    await page.locator('#xref-method-toggle').click();
    assert.equal(await page.locator('#xref-method-toggle').getAttribute('aria-expanded'),'true');
    assert.equal(await page.getByRole('heading',{name:'Contradictions and large receipts'}).count(),1);
    assert.match(await page.locator('#xref-results').innerText(),/publishes no totals/);
    await page.locator('#xref-method-toggle').click();
    assert.equal(await page.locator('#xref-method').isVisible(),false);
    if (process.env.MENTAT_CROSS_REFERENCE_SCREENSHOT) await page.screenshot({path:process.env.MENTAT_CROSS_REFERENCE_SCREENSHOT,fullPage:true});

    // D-040 in the DOM: every money figure the view shows is one row's own amount.
    const moneyOutsideRows = () => page.evaluate(() => {
      const found = [], walker = document.createTreeWalker(document.getElementById('view-cross-reference'), NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!/\$\s?\d/.test(node.textContent)) continue;
        if (node.parentElement.closest('td.number, .xref-basis')) continue;
        found.push(node.textContent.trim().slice(0, 80));
      }
      return found;
    });
    assert.deepEqual(await moneyOutsideRows(), []);

    // The header row stays on screen while the rows scroll inside the card.
    await page.locator('#xref-scroll').evaluate(element => { element.scrollTop = 300; });
    const stuck = await page.evaluate(() => Math.abs(document.querySelector('#xref-panel th').getBoundingClientRect().top - document.getElementById('xref-scroll').getBoundingClientRect().top));
    assert.ok(stuck < 1, 'the header row is sticky inside the table card, off by ' + stuck);

    // Pages of 25, previous and next.
    await page.locator('#xref-next').click();
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 26-50 of 60');
    assert.match(await page.locator('#xref-panel tbody tr').first().innerText(),/N25/);
    assert.equal(await page.locator('#xref-scroll').evaluate(element => element.scrollTop),0);
    await page.locator('#xref-next').click();
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 51-60 of 60');
    assert.equal(await page.locator('#xref-next').isDisabled(),true);
    assert.equal(await page.evaluate(() => document.activeElement.id),'xref-previous','focus moves to the enabled pager button');
    await page.locator('#xref-previous').click();
    await page.locator('#xref-previous').click();
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-25 of 60');

    // A company filter and a search narrow the active tab, and keep the caret.
    await page.selectOption('#xref-filter-company', {label:'Harbor Bank'});
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-12 of 12');
    await page.locator('#xref-search').fill('n4');
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-2 of 2');
    assert.deepEqual(await page.locator('#xref-panel .xref-id').allTextContents(), ['N40','N45']);
    assert.equal(await page.evaluate(() => document.activeElement.id),'xref-search');
    // A refresh of the same run with new bytes keeps the filters, the text and the caret.
    data = structuredClone(browserFixture); data.report.generated_at = '2026-09-11T00:00:00Z';
    await page.evaluate(() => MentatCrossReference.refresh());
    await page.waitForFunction(() => document.querySelector('.xref-run-time').title.includes('2026-09-11'));
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-2 of 2');
    assert.equal(await page.locator('#xref-search').inputValue(),'n4');
    assert.deepEqual(await page.evaluate(() => [document.activeElement.id, document.activeElement.selectionStart]), ['xref-search', 2]);
    await page.locator('#xref-search').fill('no such record');
    assert.match(await page.locator('#xref-panel .xref-empty').innerText(),/No contradiction matches these filters\./);
    await page.locator('#xref-search').fill('');
    await page.selectOption('#xref-filter-company', '');
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-25 of 60');

    // Tabs: click, arrow keys, and each tab keeps its own filters.
    await page.locator('#xref-tab-large').click();
    assert.equal(await page.locator('#xref-tab-large').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('#xref-panel').getAttribute('aria-labelledby'),'xref-tab-large');
    assert.equal(await page.locator('#xref-filter-detail').getAttribute('aria-label'),'Entity');
    assert.match(await page.locator('#xref-panel').innerText(),/Individual/);
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#xref-tab-refunds').getAttribute('aria-selected'),'true');
    assert.equal(await page.evaluate(() => document.activeElement.id),'xref-tab-refunds');
    assert.match(await page.locator('.xref-adjustments').innerText(),/-\$7,500\.00/);
    assert.equal(await page.locator('#xref-rows').textContent(),'Rows 1-2 of 2');
    await page.keyboard.press('Home');
    assert.equal(await page.locator('#xref-tab-contradictions').getAttribute('aria-selected'),'true');
    // The tabs never move when one is chosen: each holds its bold width.
    const lefts = () => page.locator('.xref-tab').evaluateAll(tabs => tabs.map(tab => Math.round(tab.getBoundingClientRect().left)));
    const before = await lefts();
    await page.locator('#xref-tab-refunds').click();
    assert.deepEqual(await lefts(), before);
    await page.locator('#xref-tab-contradictions').click();

    // Companies in scope opens under the strip and survives a refresh of the same run.
    await page.locator('#xref-companies-toggle').click();
    assert.match(await page.locator('#xref-companies').innerText(),/A Bank/);
    data = structuredClone(browserFixture); data.report.generated_at = '2026-09-12T00:00:00Z';
    const link = page.locator('#xref-results a.xref-open').first();
    await link.focus();
    await page.evaluate(() => MentatCrossReference.refresh());
    await page.waitForFunction(() => document.querySelector('.xref-run-time').title.includes('2026-09-12'));
    assert.equal(await page.locator('#xref-companies').isVisible(),true,'the same run keeps its open disclosure');
    assert.equal(await link.evaluate(element => element === document.activeElement),true,'filing links keep focus across a snapshot refresh');
    // A new run does not inherit the old run's open disclosure.
    data = structuredClone(browserFixture); data.run_id = 'another-run';
    await page.evaluate(() => MentatCrossReference.refresh());
    await page.waitForFunction(() => document.querySelector('.xref-run-time').textContent.includes('another-run') || document.querySelector('.xref-run-time').title.includes('another-run'));
    assert.equal(await page.locator('#xref-companies').isVisible(),false);

    // The status line is the Refresh button's tooltip; "Refreshing…" shows only while one runs.
    assert.equal(await page.locator('#xref-refresh').getAttribute('title'), await page.locator('#xref-refresh-status').textContent());
    assert.equal(await page.locator('#xref-refresh-note').isVisible(),false);
    delay = 900;
    await page.locator('#xref-refresh').click();
    await page.locator('#xref-refresh-note').waitFor({state:'visible'});
    assert.equal(await page.locator('#xref-refresh-note').textContent(),'Refreshing…');
    await page.locator('#xref-refresh-note').waitFor({state:'hidden'});
    delay = 0;

    // Motion is off under reduced motion.
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.locator('#xref-tab-large').click();
    assert.equal(await page.locator('#xref-scroll').evaluate(element => getComputedStyle(element).animationName),'none');
    await page.emulateMedia({reducedMotion:'no-preference'});

    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#view-cross-reference').evaluate(element => element.scrollWidth <= element.clientWidth),true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    const tabIds = await page.locator('.workspace-tabs [role=tab]').evaluateAll(tabs => tabs.map(tab => tab.id));
    const previousTab = tabIds[(tabIds.indexOf('tab-cross-reference') + tabIds.length - 1) % tabIds.length];
    await page.locator('#' + previousTab).click();
    await page.locator('#' + previousTab).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-cross-reference').getAttribute('aria-selected'),'true');
    data = {status:'error',message:'Saved report fingerprint mismatch.'};
    await page.locator('#xref-refresh').click();
    await page.getByRole('heading',{name:'Saved evidence unavailable'}).waitFor();
    assert.equal(await page.locator('#xref-panel').count(),0);
    assert.equal(await page.getByRole('heading',{name:'Clear refunds'}).count(),0);
    assert.ok(calls >= 2);
    assert.deepEqual(errors,[]);
    console.log('Cross-reference browser checks passed.');
  } finally {
    if (browser) await browser.close();
    const exited = once(server,'exit'); server.stdin.end('\n'); await exited;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
