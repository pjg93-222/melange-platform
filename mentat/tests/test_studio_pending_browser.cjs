/* Chart Studio r7 (2026-09-24): a query view or a records sidecar the server
   is still building.

   Paul, 2026-09-24: "fix the chart studio however communicate with the other
   session". The peer session's console answers POST /api/contributions/query
   and /records at once, HTTP 200, with {status:'building', phase, message,
   processed_records, total_records (an int or null), elapsed_ms,
   retry_after_ms, dataset, provenance} while it builds, keeps the index
   build's {status:'preparing', ...}, reports a failed build on the next poll as
   {error} with a non-2xx status, and accepts retry:true to start it again.
   Here page.route plays that server, for every case the peer named: building
   with a total, building with a null total, preparing, an error then Retry,
   and ready - on the query path, on the records path (Inspect all) and on the
   S10 report-number request for a conduit filing. The canvas and the records
   panel must show the progress, then draw; Cancel must stop a wait and leave
   the last good view; leaving Chart Studio must stop the polling; the only
   console errors are the browser's own lines for the two deliberate 400
   answers. */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const modules = process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = require(require.resolve('playwright', {paths: [modules]}));
const app = path.join(__dirname, '../app');

const ORIGIN = 'http://pending-studio.fixture';
const QUERY_MESSAGE = 'Preparing the Banking query view (first use after a data or code change).';
const RECORDS_MESSAGE = 'Preparing the Banking records sidecar (first use after a data or code change).';
const FIELDS = [
  {id: 'company', label: 'Company', name: 'Company', role: 'dimension', type: 'category'},
  {id: 'cycle', label: 'Election cycle', name: 'Election cycle', role: 'dimension', type: 'category', is_time: true},
  {id: 'amount', label: 'Selected amount', name: 'Selected amount', role: 'measure', type: 'number', unit: 'cents'},
  {id: 'records', label: 'Observation count', name: 'Observation count', role: 'measure', type: 'number', unit: 'count'}];
const PROVENANCE = {workbook: 'Banking.xlsx', workbook_sha256: 'w'.repeat(64), database_sha256: 'd'.repeat(64), index_token: 'index-1',
  query_engine_sha256: 'q'.repeat(64), field_semantics_sha256: 's'.repeat(64), scope: 'Offline fixture'};
const CATALOG = {datasets: [{id: 'Banking', label: 'Banking', industry: 'Banking', available: true, fields: FIELDS, provenance: PROVENANCE,
  bases: ['receipt', 'original_observed', 'original_supported', 'raw', 'pac_activity'].map(id => ({id, label: id}))}], fields: FIELDS};
const building = (fields = {}) => ({schema_version: 3, status: 'building', phase: 'query_view', message: QUERY_MESSAGE, processed_records: 0,
  total_records: null, elapsed_ms: 0, retry_after_ms: 300, dataset: 'Banking', provenance: PROVENANCE, ...fields});
const sidecar = (fields = {}) => building({phase: 'records_sidecar', message: RECORDS_MESSAGE, ...fields});
const PREPARING = {schema_version: 3, status: 'preparing', message: 'Indexing published receipt identities in bounded storage.',
  processed_records: 20000, retry_after_ms: 1500, dataset: 'Banking', provenance: PROVENANCE};
// Today's ready bodies. `status` is left off one of them: a ready answer may carry none.
function queryReady(body, status = 'ready') {
  const rows = body.dimensions.includes('company')
    ? [{company: 'Alpha Bank', amount: 800000, records: 2}, {company: 'Beta Bank', amount: 200000, records: 1}] : [{amount: 1000000, records: 3}];
  const answer = {schema_version: 3, dataset: 'Banking', basis: body.basis, rows,
    columns: [...body.dimensions, ...body.measures].map(id => FIELDS.find(f => f.id === id)).filter(Boolean),
    totals: {amount: 1000000, records: 3, contributors: 3}, comparison: {receipt: 1000000}, group_count: rows.length,
    offset: 0, limit: body.limit, has_more: false, notices: [], verified_unique: false, provenance: PROVENANCE,
    reconciliation: {receipt_matches_workbook: true, aggregate_filters_applied: false, conserved: true}};
  if (status) answer.status = status;
  return answer;
}
const RECORDS = [
  {date: '2024-03-01', contributor: 'Fixture donor A', company: 'Alpha Bank', recipient: 'Friends of Fixture', amount_cents: 500000,
    raw_cents: 500000, zip5: '02134', state: 'MA', decision_status: 'included', receipt_role: 'original', reasons: [],
    source_id: 'fixture-direct', source_url: 'https://docquery.fec.gov/cgi-bin/fecimg/?202403019000000001', committee_id: 'C00012345'},
  {date: '2024-03-02', contributor: 'Fixture donor B', company: 'Alpha Bank', recipient: 'ActBlue', amount_cents: 300000,
    raw_cents: 300000, zip5: '02134', state: 'MA', decision_status: 'included', receipt_role: 'conduit', reasons: [],
    source_id: 'fixture-actblue', source_url: '', committee_id: 'C00401224'}];
const recordsReady = body => ({schema_version: 3, status: 'ready', dataset: 'Banking', basis: body.basis, rows: RECORDS,
  total_records: RECORDS.length, offset: 0, limit: body.limit, has_more: false, provenance: PROVENANCE});
const conduitReady = body => ({schema_version: 3, status: 'ready', dataset: 'Banking', basis: body.basis,
  rows: [{source_id: 'fixture-actblue', reported_file_num: 1234567}], total_records: 1, offset: 0, limit: body.limit, has_more: false, provenance: PROVENANCE});
const REPORT_LINK = 'https://docquery.fec.gov/cgi-bin/forms/C00401224/1234567/';

// The stub console. Each endpoint answers from its own plan, in order, and
// answers ready once its plan is empty. A held step waits until the test
// releases it, so the test reads the page between two polls, not on a timer.
function stubConsole(page, html) {
  const plan = {query: [], records: [], conduit: []}, log = {query: [], records: [], conduit: []};
  const arrived = {query: [], records: [], conduit: []}, waiting = {query: [], records: [], conduit: []}, unexpected = [];
  const READY = {query: body => queryReady(body), records: recordsReady, conduit: conduitReady};
  const route = async route => {
    const request = route.request(), url = new URL(request.url());
    const reply = (status, value) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(value)}).catch(() => {});
    if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (url.pathname === '/api/contributions/session') return reply(200, {token: 'fixture-token', read_only: true});
    if (url.pathname === '/api/contributions/catalog') return reply(200, CATALOG);
    if (!['/api/contributions/query', '/api/contributions/records'].includes(url.pathname)) {
      unexpected.push(request.method() + ' ' + url.pathname);
      return reply(404, {error: 'Not in this fixture.'});
    }
    const body = JSON.parse(request.postData() || '{}');
    const kind = url.pathname.endsWith('/query') ? 'query' : (body.dimensions || []).includes('reported_file_num') ? 'conduit' : 'records';
    log[kind].push(body);
    const step = plan[kind].shift() || {answer: READY[kind]};
    if (step.hold) {
      await new Promise(release => {
        const entry = {body, release};
        if (waiting[kind].length) waiting[kind].shift()(entry); else arrived[kind].push(entry);
      });
    }
    return reply(step.status || 200, typeof step.answer === 'function' ? step.answer(body) : step.answer);
  };
  // Resolves with {body, release} once the page has sent the held request.
  const held = (kind, label) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('the page never sent the held ' + kind + ' request: ' + label)), 15000);
    const take = entry => { clearTimeout(timer); resolve(entry); };
    if (arrived[kind].length) take(arrived[kind].shift()); else waiting[kind].push(take);
  });
  return {route, plan, log, held, unexpected};
}
const answer = value => ({answer: value});
const hold = value => ({answer: value, hold: true});
const failWith = (status, message) => ({status, answer: {error: message}});

test('Chart Studio polls a building query view or records sidecar, shows its progress, then draws', {timeout: 180000}, async t => {
  const browser = await chromium.launch({headless: true, channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome'});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(12000);
  const pageErrors = [], consoleErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  const html = fs.readFileSync(path.join(app, 'index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
  const server = stubConsole(page, html);
  await page.route(ORIGIN + '/**', server.route);
  await page.goto(ORIGIN + '/#chart-studio');
  for (const file of ['style.css', 'acu-theme.css', 'workspace.css', 'chart-studio.css']) await page.addStyleTag({path: path.join(app, file)});
  await page.evaluate(() => {
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel => { panel.hidden = panel.id !== 'view-chart-studio'; });
    window.currentView = 'chart-studio'; window.selectedRun = 'published-reports';
    window.escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    window.api = async url => { throw Error('Unexpected api call ' + url); };
    window.MentatExport = {download: () => {}, image: async () => {}};
  });

  const canvasPanel = (host = '#studio-canvas') => page.evaluate(host => {
    const panel = document.querySelector(host + ' > .studio-pending');
    if (!panel) return null;
    const bar = panel.querySelector('.studio-pending-bar'), button = panel.querySelector('button.studio-pending-retry');
    return {state: panel.dataset.state, phase: panel.dataset.phase || '', role: panel.getAttribute('role'),
      message: (panel.querySelector('.studio-pending-message') || {}).textContent || '',
      detail: (panel.querySelector('.studio-pending-detail') || {}).textContent || '',
      valuenow: bar ? bar.getAttribute('aria-valuenow') : null, indeterminate: bar ? bar.classList.contains('is-indeterminate') : null,
      width: bar && bar.firstElementChild ? bar.firstElementChild.style.width : null, retry: button ? button.textContent : '',
      first: panel.parentElement.firstElementChild === panel, opacity: getComputedStyle(panel).opacity,
      hostOpacity: getComputedStyle(panel.parentElement).opacity};
  }, host);
  const chrome = () => page.evaluate(() => {
    const svg = document.querySelector('#studio-canvas svg');
    return {loading: document.getElementById('studio-loading').textContent, status: document.getElementById('studio-status').textContent,
      progressShown: !document.getElementById('studio-progress').hidden, progress: document.getElementById('studio-progress-message').textContent,
      busy: document.getElementById('studio-canvas').getAttribute('aria-busy'), svg: document.querySelectorAll('#studio-canvas svg').length,
      svgOpacity: svg ? getComputedStyle(svg).opacity : null, note: document.getElementById('studio-note').textContent,
      error: document.getElementById('studio-error').hidden ? '' : document.getElementById('studio-error').textContent};
  });
  const drawn = () => page.waitForFunction(() => document.querySelector('#studio-canvas svg') &&
    document.querySelector('#studio-canvas').getAttribute('aria-busy') !== 'true' && !document.querySelector('#studio-canvas > .studio-pending'));
  const setBasis = id => page.evaluate(id => { const select = document.getElementById('studio-basis'); select.value = id; select.dispatchEvent(new Event('change')); }, id);

  /* 1. First load, building with a total: 25%, then 75%, then the chart. */
  server.plan.query.push(answer(building({processed_records: 1000, total_records: 4000, elapsed_ms: 400})),
    hold(building({processed_records: 3000, total_records: 4000, elapsed_ms: 1100})), hold(body => queryReady(body)));
  for (const file of ['theme.js', 'chart-math.js', 'scope.js', 'grouped-chart.js', 'charts.js', 'chart-studio-core.js', 'contribution-plot.js',
    'contribution-charts.js', 'contribution-map.js', 'chart-studio-contributions.js', 'chart-studio.js']) await page.addScriptTag({path: path.join(app, file)});
  const second = await server.held('query', 'the second poll of the first load');
  let panel = await canvasPanel();
  assert.ok(panel, 'the canvas shows the server\'s progress while the query view builds, not a blank');
  assert.deepEqual({state: panel.state, phase: panel.phase, role: panel.role, message: panel.message},
    {state: 'pending', phase: 'query_view', role: 'status', message: QUERY_MESSAGE});
  assert.deepEqual({valuenow: panel.valuenow, indeterminate: panel.indeterminate, width: panel.width}, {valuenow: '25', indeterminate: false, width: '25%'});
  assert.equal(panel.detail, '25% · 1,000 of 4,000 records · 0:00 elapsed');
  let ui = await chrome();
  assert.equal(ui.busy, 'true');
  assert.equal(ui.svg, 0);
  assert.equal(ui.loading, QUERY_MESSAGE + ' 25% · 1,000 of 4,000 records · 0:00 elapsed', 'the status line carries the progress');
  assert.equal(ui.status, QUERY_MESSAGE + ' 25% · 1,000 of 4,000 records · 0:00 elapsed', 'and so does the canvas status bar');
  assert.equal(ui.progressShown, true);
  assert.equal(ui.progress, 'Preparing verified data · ' + QUERY_MESSAGE + ' 25% · 1,000 of 4,000 records');
  assert.equal(panel.opacity, '1', 'the panel is not dimmed with the busy canvas');
  assert.equal(second.body.retry, undefined);
  assert.deepEqual(second.body, server.log.query[0], 'each poll re-sends the same body');
  second.release();
  const third = await server.held('query', 'the third poll of the first load');
  panel = await canvasPanel();
  assert.deepEqual([panel.valuenow, panel.width, panel.detail], ['75', '75%', '75% · 3,000 of 4,000 records · 0:01 elapsed']);
  third.release();
  await drawn();
  ui = await chrome();
  assert.equal(ui.svg, 1);
  assert.equal(ui.progressShown, false, 'the progress row goes once the answer is in');
  assert.match(ui.loading, /^Verified contribution fields/);
  assert.match(ui.status, /^3 source observations · 2 groups/);
  assert.equal(server.log.query.length, 3, 'building twice, then the data: three requests');

  /* 2. Building with a null total: a sweep, the message and the elapsed time,
        over the last good chart, which stays on the canvas. */
  server.plan.query.push(answer(building({elapsed_ms: 2000})), hold(building({elapsed_ms: 2600})), hold(body => queryReady(body)));
  await setBasis('original_observed');
  const nullTotal = await server.held('query', 'the null-total poll');
  panel = await canvasPanel();
  assert.deepEqual({state: panel.state, message: panel.message, indeterminate: panel.indeterminate, valuenow: panel.valuenow, width: panel.width, detail: panel.detail},
    {state: 'pending', message: QUERY_MESSAGE, indeterminate: true, valuenow: null, width: '', detail: '0:02 elapsed'});
  ui = await chrome();
  assert.equal(ui.svg, 1, 'the last good chart is still there');
  assert.equal(panel.first, true, 'the panel is first in the canvas');
  assert.equal(ui.svgOpacity, '0.45', 'the last chart is dimmed');
  assert.deepEqual([panel.opacity, panel.hostOpacity], ['1', '1'], 'the panel is not');
  assert.equal(ui.loading, QUERY_MESSAGE + ' 0:02 elapsed');
  assert.equal(nullTotal.body.basis, 'original_observed');
  nullTotal.release();
  const nullTotalLast = await server.held('query', 'the answer after the null-total polls');
  assert.equal((await canvasPanel()).detail, '0:02 elapsed');
  nullTotalLast.release();
  await drawn();

  /* 3. The index build's 'preparing' answer: the server's own words and the
        records so far, at the answer's 1.5 s pace. */
  server.plan.query.push(answer(PREPARING), hold(body => queryReady(body)));
  const beforePreparing = server.log.query.length;
  await page.evaluate(() => document.getElementById('studio-refresh-data').click());
  const preparing = await server.held('query', 'the poll after a preparing answer');
  panel = await canvasPanel();
  assert.equal(panel.message, 'Indexing published receipt identities in bounded storage.');
  assert.equal(panel.indeterminate, true);
  assert.match(panel.detail, /^20,000 records so far · 0:0\d elapsed$/);
  ui = await chrome();
  assert.equal(ui.progress, 'Preparing verified data · Indexing published receipt identities in bounded storage. 20,000 records so far');
  assert.equal(server.log.query.length - beforePreparing, 2);
  preparing.release();
  await drawn();

  /* 4. An error on the next poll, then Retry: the error is said once, on the
        canvas, with a Retry that sends retry:true on its first request only. */
  const FAILED = 'The Banking query view could not be built: fixture failure.';
  server.plan.query.push(answer(building({processed_records: 500, total_records: 4000, elapsed_ms: 300})), failWith(400, FAILED));
  // A worksheet table render leaves the canvas hidden; the panel must not be drawn where nobody sees it.
  await page.evaluate(() => { document.getElementById('studio-canvas').hidden = true; });
  await setBasis('raw');
  await page.waitForFunction(() => document.querySelector('#studio-canvas > .studio-pending[data-state="stopped"]'));
  assert.equal(await page.locator('#studio-canvas .studio-pending-retry').isVisible(), true, 'the Retry can be seen');
  panel = await canvasPanel();
  assert.deepEqual({state: panel.state, role: panel.role, message: panel.message, retry: panel.retry}, {state: 'stopped', role: 'alert', message: FAILED, retry: 'Retry'});
  ui = await chrome();
  assert.equal(ui.svg, 0, 'a sheet that draws nothing keeps nothing of the last answer');
  assert.equal(ui.busy, 'false');
  assert.equal(ui.error, '', 'the message is said once, on the canvas');
  assert.equal(ui.note, '');
  assert.equal(ui.progressShown, false);
  const beforeRetry = server.log.query.length;
  server.plan.query.push(answer(building({processed_records: 2000, total_records: 4000, elapsed_ms: 800})), hold(body => queryReady(body)));
  await page.locator('#studio-canvas .studio-pending-retry').click();
  const afterRetry = await server.held('query', 'the poll after Retry');
  assert.equal(server.log.query[beforeRetry].retry, true, 'Retry asks the server to start the build again');
  assert.equal(server.log.query[beforeRetry].basis, 'raw');
  assert.equal(afterRetry.body.retry, undefined, 'and only on its first request');
  assert.equal((await canvasPanel()).detail, '50% · 2,000 of 4,000 records · 0:00 elapsed');
  afterRetry.release();
  await drawn();

  /* 5. Ready, from a server that holds the request: today's path, untouched.
        No pending panel ever appears, and one request is made. */
  await page.evaluate(() => {
    window.pendingSeen = false;
    new MutationObserver(() => { if (document.querySelector('.studio-pending')) window.pendingSeen = true; })
      .observe(document.getElementById('view-chart-studio'), {subtree: true, childList: true});
  });
  const beforeReady = server.log.query.length;
  server.plan.query.push(hold(body => queryReady(body, null)));
  await setBasis('pac_activity');
  const heldReady = await server.held('query', 'the held ready answer');
  await page.waitForTimeout(400);
  ui = await chrome();
  assert.deepEqual({busy: ui.busy, progressShown: ui.progressShown}, {busy: 'true', progressShown: false});
  heldReady.release();
  await drawn();
  assert.equal(await page.evaluate(() => window.pendingSeen), false, 'a held, then ready, answer never shows a pending panel');
  assert.equal(server.log.query.length - beforeReady, 1);

  /* 6. Records: Inspect all on a sidecar that builds twice, then the table;
        then the S10 report-number request for the ActBlue row, pending once. */
  server.plan.records.push(answer(sidecar({processed_records: 200, total_records: 800, elapsed_ms: 500})),
    hold(sidecar({processed_records: 600, total_records: 800, elapsed_ms: 900})), hold(recordsReady));
  server.plan.conduit.push(answer(sidecar({elapsed_ms: 100})), hold(conduitReady));
  await page.evaluate(() => document.getElementById('studio-inspect-all').click());
  const records2 = await server.held('records', 'the second records poll');
  panel = await canvasPanel('#studio-records');
  assert.ok(panel, 'the records panel shows the sidecar\'s progress');
  assert.deepEqual({state: panel.state, phase: panel.phase, message: panel.message, valuenow: panel.valuenow, detail: panel.detail},
    {state: 'pending', phase: 'records_sidecar', message: RECORDS_MESSAGE, valuenow: '25', detail: '25% · 200 of 800 records · 0:00 elapsed'});
  assert.equal(await page.locator('#studio-record-count').textContent(), 'Reading source records…');
  assert.equal((await chrome()).loading, RECORDS_MESSAGE + ' 25% · 200 of 800 records · 0:00 elapsed');
  records2.release();
  const records3 = await server.held('records', 'the third records poll');
  assert.equal((await canvasPanel('#studio-records')).valuenow, '75');
  records3.release();
  await page.waitForFunction(() => document.querySelector('#studio-records table'));
  assert.equal(await canvasPanel('#studio-records'), null, 'the table replaced the progress');
  assert.equal(await page.locator('#studio-record-count').textContent(), '1–2 of 2 source observations');
  assert.equal(server.log.records.length, 3);
  const conduit = await server.held('conduit', 'the report-number poll');
  assert.equal(await page.evaluate(() => [...document.querySelectorAll('#studio-records td')].filter(td => td.textContent === 'FEC report…').length), 1,
    'the conduit cell waits for its report number');
  assert.deepEqual(conduit.body.dimensions, ['reported_file_num']);
  assert.deepEqual(conduit.body.filters.at(-1), {field: 'source_id', op: 'in', value: ['fixture-actblue']});
  conduit.release();
  await page.waitForFunction(link => document.querySelector('#studio-records a[href="' + link + '"]'), REPORT_LINK);
  assert.equal(await page.locator('#studio-records a[href="' + REPORT_LINK + '"]').textContent(), 'FEC report');
  assert.equal(server.log.conduit.length, 2, 'the report-number request was polled, not dropped as Unavailable');

  /* 7. Records: an error on the next poll, then Retry. */
  const RECORDS_FAILED = 'The Banking records sidecar could not be built: fixture failure.';
  server.plan.records.push(answer(sidecar({processed_records: 100, total_records: 800})), failWith(400, RECORDS_FAILED));
  await page.evaluate(() => document.getElementById('studio-inspect-all').click());
  await page.waitForFunction(() => document.querySelector('#studio-records > .studio-pending[data-state="stopped"]'));
  assert.equal(await page.locator('#studio-record-count').textContent(), RECORDS_FAILED);
  assert.equal((await canvasPanel('#studio-records')).retry, 'Retry');
  const beforeRecordsRetry = server.log.records.length;
  await page.locator('#studio-records .studio-pending-retry').click();
  await page.waitForFunction(() => document.querySelector('#studio-records table'));
  assert.equal(server.log.records[beforeRecordsRetry].retry, true);
  assert.equal(server.log.records.length - beforeRecordsRetry, 1);
  await page.waitForFunction(link => document.querySelector('#studio-records a[href="' + link + '"]'), REPORT_LINK);

  /* 8. Cancel stops a pending wait at once and leaves the last good view,
        its status bar included, exactly where it was. */
  const lastLine = (await chrome()).status;
  assert.match(lastLine, /^3 source observations · 2 groups/);
  server.plan.query.push(answer(building({processed_records: 100, total_records: 4000, elapsed_ms: 200, retry_after_ms: 1500})));
  const beforeCancel = server.log.query.length;
  await setBasis('original_supported');
  await page.waitForFunction(() => document.querySelector('#studio-canvas > .studio-pending[data-state="pending"]'));
  assert.equal((await chrome()).status, QUERY_MESSAGE + ' 2% · 100 of 4,000 records · 0:00 elapsed');
  await page.locator('#studio-progress-cancel').click();
  await page.waitForFunction(() => !document.querySelector('#studio-canvas > .studio-pending') &&
    document.querySelector('#studio-canvas').getAttribute('aria-busy') !== 'true');
  ui = await chrome();
  assert.deepEqual({svg: ui.svg, status: ui.status, progressShown: ui.progressShown}, {svg: 1, status: lastLine, progressShown: false});
  assert.equal(ui.loading, 'Cancelled. Showing the last loaded view.');
  await page.waitForTimeout(2000);
  assert.equal(server.log.query.length - beforeCancel, 1, 'nothing more is asked after Cancel');

  /* 9. Leaving Chart Studio stops the polling; coming back resumes it. */
  server.plan.query.push(answer(building({processed_records: 100, total_records: 4000, retry_after_ms: 1500})));
  if (await page.locator('#studio-source-popover').isVisible()) await page.locator('#studio-scope-change').click();
  await page.locator('#studio-measures .studio-pill').filter({hasText: 'Observation count'}).first().click();
  await page.waitForFunction(() => document.querySelector('#studio-canvas > .studio-pending[data-state="pending"]'));
  assert.ok(server.log.query.at(-1).measures.includes('records'), 'a new question: the measure was added');
  await page.evaluate(() => {
    document.getElementById('view-chart-studio').hidden = true;
    window.dispatchEvent(new CustomEvent('mentat:view', {detail: {view: 'graph', split: false}}));
  });
  const whileAway = server.log.query.length;
  await page.waitForTimeout(3000);
  assert.equal(server.log.query.length, whileAway, 'no poll while Chart Studio is not on screen');
  await page.evaluate(() => {
    document.getElementById('view-chart-studio').hidden = false;
    window.dispatchEvent(new CustomEvent('mentat:view', {detail: {view: 'chart-studio', split: false}}));
  });
  await drawn();
  assert.equal(server.log.query.length, whileAway + 1, 'the poll resumed once it was back');

  assert.deepEqual(server.unexpected, []);
  assert.deepEqual(pageErrors, []);
  // The browser logs its own line for each deliberate 400; nothing else may be logged.
  const deliberate = consoleErrors.filter(text => /status of 400/.test(text));
  assert.equal(deliberate.length, 2, JSON.stringify(consoleErrors));
  assert.deepEqual(consoleErrors.filter(text => !/status of 400/.test(text)), []);
});
