/* P6, batch 2 (2026-09-25): the standalone /contribution-studio page polls a building query view or
   records sidecar at the server's pace, treats 'building' and 'preparing' as pending (not 'queued'),
   stops after 15 minutes with a retryable error, and sends retry:true only on a Retry's first request.
   Code review finding 7 (2026-09-25): the page adapts Chart Studio's pollPending
   (chart-studio-contributions.js) instead of keeping a poller of its own, so these tests pin the
   adapters and this page's parts; chart-studio-pending.test.js pins the contract itself.
   Run: node --test mentat/app/tests/contribution-studio-pending.test.js
   MENTAT_CONTRIBUTION_STUDIO_MODULE points the test at another copy (negative controls). */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');

const ui = require(process.env.MENTAT_CONTRIBUTION_STUDIO_MODULE || path.join(__dirname, '..', 'contribution-studio.js'));
const studio = require(path.join(__dirname, '..', 'chart-studio-contributions.js'));

const building = (extra = {}) => ({schema_version: 3, status: 'building', phase: 'query_view',
  message: 'Preparing the query view for this report.', processed_records: 0, total_records: null,
  elapsed_ms: 0, retry_after_ms: 300, dataset: 'Banking', provenance: {}, ...extra});

// A scripted server: answers in order (the last repeats), recording every body it was sent.
function server(answers) {
  const sent = [];
  let index = 0;
  const ask = async body => { if (sent.length >= 5000) throw new Error('the poll never stopped'); sent.push(body); const answer = answers[Math.min(index++, answers.length - 1)]; if (answer instanceof Error) throw answer; return answer; };
  return {ask, sent};
}
function clock(start = 1000) { let at = start; return {now: () => at, sleep: async ms => { at += ms; }, advance: ms => { at += ms; }}; }

test('finding 7: this page and Chart Studio share one pending contract', () => {
  assert.equal(ui.PENDING, studio.PENDING);
  assert.equal(ui.pendingDelay, studio.pendingDelay);
  assert.equal(ui.PENDING_CEILING_MS, studio.PENDING_CEILING_MS);
});

// Finding 7 repair (2026-09-25): without chart-studio-contributions.js the page still loads and takes
// a ready answer, but it never polls: a pending answer stops at once and says to reload.
test('finding 7: without the shared poller the page loads, takes ready answers and refuses pending ones', async () => {
  const source = fs.readFileSync(process.env.MENTAT_CONTRIBUTION_STUDIO_MODULE || path.join(__dirname, '..', 'contribution-studio.js'), 'utf8');
  const box = {module: {exports: {}}, require: id => { assert.equal(id, './chart-studio-contributions.js'); return undefined; }};
  vm.runInNewContext(source, box);
  const alone = box.module.exports;
  assert.equal(alone.PENDING, undefined);
  const ready = {rows: [1]};
  const {ask, sent} = server([ready]);
  assert.equal(await alone.pollReady(ask, {body: {q: 1}, retry: true}), ready);
  assert.deepEqual(sent.map(item => [item.q, item.retry]), [[1, true]]);
  assert.deepEqual((await alone.pollReady(server([{status: 'ready', rows: []}]).ask, {body: {}})).rows, []);
  const pending = server([building(), {status: 'ready'}]);
  await assert.rejects(alone.pollReady(pending.ask, {body: {}}), error => /could not load chart-studio-contributions\.js/.test(error.message) && /Reload the page/.test(error.message) && !error.retryable);
  assert.equal(pending.sent.length, 1);
});

test('P6: building and preparing are polled until ready; queued is not a pending answer', async () => {
  const ready = {status: 'ready', rows: [1]};
  for (const status of ['building', 'preparing']) {
    const {ask, sent} = server([building({status}), building({status}), ready]);
    const time = clock();
    assert.equal(await ui.pollReady(ask, {body: {q: 1}, now: time.now, sleep: time.sleep}), ready, status);
    assert.equal(sent.length, 3, status);
  }
  const queued = {status: 'queued', message: 'Snapshot job queued.'};
  const {ask, sent} = server([queued, ready]);
  assert.equal(await ui.pollReady(ask, {body: {}}), queued);
  assert.equal(sent.length, 1);
  assert.equal(ui.isPending(queued), false);
  assert.deepEqual([...ui.PENDING].sort(), ['building', 'preparing']);
});

test('P6: the wait follows retry_after_ms, held between 250 ms and 5 s', async () => {
  assert.equal(ui.pendingDelay(building({retry_after_ms: 300})), 300);
  assert.equal(ui.pendingDelay(building({retry_after_ms: 750})), 750);
  assert.equal(ui.pendingDelay(building({retry_after_ms: 1500})), 1500);
  assert.equal(ui.pendingDelay(building({retry_after_ms: 10})), 250);
  assert.equal(ui.pendingDelay(building({retry_after_ms: 60000})), 5000);
  // Finding 7: without a number the wait backs off 0.5, 1, 2, then 4 s (Chart Studio's), not a fixed 1.5 s.
  assert.deepEqual([0, 1, 2, 3, 9].map(n => ui.pendingDelay(building({retry_after_ms: undefined}), n)), [500, 1000, 2000, 4000, 4000]);
  const waits = [];
  const {ask} = server([building({retry_after_ms: 300}), building({retry_after_ms: 750}), building({retry_after_ms: 1500}), {status: 'ready'}]);
  await ui.pollReady(ask, {body: {}, sleep: async ms => { waits.push(ms); }});
  assert.deepEqual(waits, [300, 750, 1500]);
  const backoff = [];
  const bare = server([building({retry_after_ms: 20}), building({retry_after_ms: undefined}), building({retry_after_ms: undefined}), {status: 'ready'}]);
  await ui.pollReady(bare.ask, {body: {}, sleep: async ms => { backoff.push(ms); }});
  assert.deepEqual(backoff, [250, 1000, 2000]);
});

test('P6: after 15 minutes of pending answers the poll stops with a retryable error', async () => {
  assert.equal(ui.PENDING_CEILING_MS, 15 * 60 * 1000);
  const {ask, sent} = server([building({retry_after_ms: 1500})]);
  const time = clock();
  await assert.rejects(ui.pollReady(ask, {body: {}, path: 'records', now: time.now, sleep: time.sleep}), error => {
    assert.equal(error.name, 'PendingCeiling');
    assert.equal(error.retryable, true);
    assert.match(error.message, /15 minutes/);
    assert.match(error.message, /source records/);
    assert.match(error.message, /Retry/);
    assert.match(error.message, /so this page stopped checking/);
    assert.doesNotMatch(error.message, /Chart Studio/);
    return true;
  });
  assert.equal(ui.pendingCeiling('query', 2000).message,
    'The server is still preparing this query view after about 2 seconds, so this page stopped checking. It may still finish there; Retry checks again.');
  // 15 minutes at 1.5 s per poll, plus the answer that crossed the ceiling.
  assert.equal(sent.length, 15 * 60 / 1.5 + 1);
});

test('P6: a Retry sends retry:true on its first request only; the caller body is not changed', async () => {
  const body = {dataset: 'Banking', limit: 100};
  const {ask, sent} = server([building(), building(), {status: 'ready'}]);
  await ui.pollReady(ask, {body, retry: true, sleep: async () => {}});
  assert.deepEqual(sent.map(item => item.retry === true), [true, false, false]);
  assert.equal('retry' in body, false);
  const plain = server([building(), {status: 'ready'}]);
  await ui.pollReady(plain.ask, {body, sleep: async () => {}});
  assert.deepEqual(plain.sent.map(item => 'retry' in item), [false, false]);
});

test('P6: progress names the share when the total is known, else the count, and the elapsed time', async () => {
  assert.equal(ui.pendingText(building({phase: 'records_sidecar', message: 'Copying the source records for inspection.',
    processed_records: 300000, total_records: 849399, elapsed_ms: 75000}), 'records'),
    'Copying the source records for inspection. 35% · 300,000 of 849,399 records · 1:15 elapsed');
  assert.equal(ui.pendingText(building({processed_records: 1200, elapsed_ms: 3000})),
    'Preparing the query view for this report. 1,200 records so far · 0:03 elapsed');
  // Finding 7: the elapsed time is always shown, the server's or else this page's own wait (Chart Studio's text).
  assert.equal(ui.pendingText({status: 'preparing'}, 'records'), 'Preparing the source records… 0:00 elapsed');
  const heard = [];
  const {ask} = server([building({processed_records: 5, total_records: 10, elapsed_ms: 1000}), {status: 'ready'}]);
  await ui.pollReady(ask, {body: {}, sleep: async () => {}, onProgress: (text, progress) => heard.push([text, progress.status])});
  assert.deepEqual(heard, [['Preparing the query view for this report. 50% · 5 of 10 records · 0:01 elapsed', 'building']]);
});

test('P6: a newer view stops the poll; errors and failed answers are retryable', async () => {
  let live = true;
  const {ask, sent} = server([building(), {status: 'ready'}]);
  await assert.rejects(ui.pollReady(ask, {body: {}, current: () => live, sleep: async () => { live = false; }}), {name: 'AbortError'});
  assert.equal(sent.length, 1);
  const failing = server([new Error('Contribution service unavailable.')]);
  await assert.rejects(ui.pollReady(failing.ask, {body: {}}), error => error.retryable === true);
  const failed = server([{status: 'failed', message: 'Source verification failed.'}]);
  await assert.rejects(ui.pollReady(failed.ask, {body: {}}), error => error.retryable === true && /verification failed/.test(error.message));
  const silent = server([{status: 'failed'}]);
  await assert.rejects(ui.pollReady(silent.ask, {body: {}}), error => error.retryable === true && error.message === 'Source verification failed.');
  const aborted = Object.assign(new Error('The user aborted a request.'), {name: 'AbortError'});
  await assert.rejects(ui.pollReady(server([aborted]).ask, {body: {}}), error => error === aborted && error.retryable === undefined);
});
