/* Chart Studio r7 (2026-09-24): the one pending handler in front of
   POST /api/contributions/query and /records (chart-studio-contributions.js,
   pollPending and its helpers).

   Paul, 2026-09-24: "fix the chart studio however communicate with the other
   session". The peer session's console now answers those two endpoints at
   once, HTTP 200, with {status:'building', phase, message, processed_records,
   total_records (an int or null), elapsed_ms, retry_after_ms, dataset,
   provenance} while a query view or a records sidecar builds, and keeps the
   index build's {status:'preparing', message, processed_records,
   retry_after_ms:1500}. The client polls at the server's pace, shows the
   progress, lets the latest request win, pauses while Chart Studio is hidden,
   and after about 15 minutes stops with an honest error it offers a Retry for.
   Run: node --test mentat/app/tests/chart-studio-pending.test.js */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const contributions = require('../chart-studio-contributions.js');

const BODY = Object.freeze({dataset: 'Banking', basis: 'receipt', dimensions: ['company'], measures: ['amount'],
  filters: [], aggregate_filters: [], sort: [{field: 'amount', direction: 'desc'}], limit: 100, offset: 0});
const READY = {schema_version: 3, status: 'ready', rows: [{company: 'Alpha Bank', amount: 800000}], totals: {records: 2}};
const MESSAGE = 'Preparing the Banking query view (first use after a data or code change).';
const building = (fields = {}) => ({schema_version: 3, status: 'building', phase: 'query_view', message: MESSAGE,
  processed_records: 0, total_records: null, elapsed_ms: 0, retry_after_ms: 300, dataset: 'Banking', provenance: {}, ...fields});
const PREPARING = {schema_version: 3, status: 'preparing', message: 'Indexing published receipt identities in bounded storage.',
  processed_records: 20000, retry_after_ms: 1500, dataset: 'Banking', provenance: {}};

// A scripted server and a fake clock: each answer is taken in turn (the last
// one repeats), and sleep() moves the clock instead of waiting.
function harness(answers, start = 1000) {
  let clock = start;
  const asks = [], sleeps = [];
  return {
    asks, sleeps,
    now: () => clock,
    advance: ms => { clock += ms; },
    ask: async body => {
      asks.push(body);
      const next = answers.length > 1 ? answers.shift() : answers[0];
      if (next instanceof Error) throw next;
      return next;
    },
    sleep: async ms => { sleeps.push(ms); clock += ms; }
  };
}
const outcome = promise => promise.then(value => ({value}), error => ({error}));

test('building twice, then the ready body: asked three times at the server pace, and the ready body is returned untouched', async () => {
  const h = harness([building({processed_records: 1000, total_records: 4000, elapsed_ms: 400}),
    building({processed_records: 3000, total_records: 4000, elapsed_ms: 1100, retry_after_ms: 750}), READY]);
  const seen = [];
  const result = await contributions.pollPending(h.ask, {body: BODY, sleep: h.sleep, now: h.now, onProgress: p => seen.push(p)});
  assert.equal(result, READY);
  assert.equal(h.asks.length, 3);
  assert.ok(h.asks.every(body => body === BODY), 'every poll re-sends the very same body');
  assert.deepEqual(h.sleeps, [300, 750], 'each wait is the answer\'s own retry_after_ms');
  assert.deepEqual(seen.map(p => [p.status, p.phase, p.percent, p.processed, p.total]),
    [['building', 'query_view', 25, 1000, 4000], ['building', 'query_view', 75, 3000, 4000]]);
  assert.deepEqual(seen.map(p => p.attempt), [0, 1]);
});

test("the index build's 'preparing' answer is pending too; 'queued' (snapshot jobs) and a ready body without a status are not", async () => {
  const h = harness([PREPARING, READY]);
  const seen = [];
  assert.equal(await contributions.pollPending(h.ask, {body: BODY, sleep: h.sleep, now: h.now, onProgress: p => seen.push(p)}), READY);
  assert.deepEqual(h.sleeps, [1500]);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].status, 'preparing');
  assert.equal(seen[0].percent, null, 'no total, so no share');
  assert.equal(seen[0].processed, 20000);

  const queued = {status: 'queued', message: 'Preparing the snapshot.'};
  const q = harness([queued]);
  assert.equal(await contributions.pollPending(q.ask, {body: BODY, sleep: q.sleep, now: q.now}), queued);
  assert.equal(q.asks.length, 1);
  assert.deepEqual(q.sleeps, []);

  const bare = {rows: [], totals: {records: 0}};
  const b = harness([bare]);
  let told = 0;
  assert.equal(await contributions.pollPending(b.ask, {body: BODY, sleep: b.sleep, now: b.now, onProgress: () => { told += 1; }}), bare);
  assert.equal(b.asks.length, 1);
  assert.equal(told, 0, 'a ready answer reports no progress');

  for (const status of ['building', 'preparing']) assert.equal(contributions.pendingStatus({status}), status);
  for (const answer of [{status: 'ready'}, {status: 'queued'}, {status: 'failed'}, {}, null, undefined, 'building'])
    assert.equal(contributions.pendingStatus(answer), '');
});

test('retry_after_ms is held between 250 ms and 5 s; without a number the wait backs off 0.5, 1, 2, then 4 s', async () => {
  const delay = contributions.pendingDelay;
  assert.deepEqual([contributions.PENDING_MIN_MS, contributions.PENDING_MAX_MS], [250, 5000]);
  // The peer's schedule passes through unchanged.
  assert.equal(delay({retry_after_ms: 300}), 300);
  assert.equal(delay({retry_after_ms: 750}), 750);
  assert.equal(delay({retry_after_ms: 1500}), 1500);
  // Too eager, too lazy, or fractional.
  assert.equal(delay({retry_after_ms: 0}), 250);
  assert.equal(delay({retry_after_ms: 50}), 250);
  assert.equal(delay({retry_after_ms: -10}), 250);
  assert.equal(delay({retry_after_ms: 60000}), 5000);
  assert.equal(delay({retry_after_ms: 333.6}), 334);
  // Absent, or not a finite number: the default backoff by attempt.
  for (const answer of [{}, {retry_after_ms: null}, {retry_after_ms: '750'}, {retry_after_ms: NaN}, {retry_after_ms: Infinity}, null])
    assert.deepEqual([0, 1, 2, 3, 9].map(n => delay(answer, n)), [500, 1000, 2000, 4000, 4000], JSON.stringify(answer));

  // In the loop: a clamped first wait, then the backoff for answers without one.
  const h = harness([building({retry_after_ms: 20}), building({retry_after_ms: undefined}), building({retry_after_ms: undefined}), READY]);
  await contributions.pollPending(h.ask, {body: BODY, sleep: h.sleep, now: h.now});
  assert.deepEqual(h.sleeps, [250, 1000, 2000]);
});

test('the latest request wins: an aborted signal ends the wait at once and nothing more is asked', async () => {
  const controller = new AbortController();
  let asked = 0;
  // The real sleep (pendingSleep) and the real clock: the answer asks for 5 s.
  const poll = contributions.pollPending(async () => { asked += 1; return building({retry_after_ms: 5000}); },
    {body: BODY, signal: controller.signal});
  await new Promise(resolve => setTimeout(resolve, 30));
  const cut = Date.now();
  controller.abort();
  const {error} = await outcome(poll);
  assert.equal(error.name, 'AbortError');
  assert.equal(error.retryable, undefined, 'a superseded wait is not an error to retry');
  assert.ok(Date.now() - cut < 500, 'the 5 s wait was cut short');
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(asked, 1);
});

test('two requests for one surface: starting B aborts A (the queryCached claim), so only B is polled to its answer', async () => {
  let claim = null;
  const counts = {A: 0, B: 0};
  const start = (name, answers) => {
    if (claim) claim.abort();
    claim = new AbortController();
    const h = harness(answers);
    const ask = async body => { counts[name] += 1; return h.ask(body); };
    // A real (abortable) wait, kept short.
    const sleep = (ms, signal) => new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, 20);
      signal.addEventListener('abort', () => { clearTimeout(timer); reject(Object.assign(new Error('View changed'), {name: 'AbortError'})); }, {once: true});
    });
    return contributions.pollPending(ask, {body: {...BODY, dimensions: [name]}, signal: claim.signal, sleep});
  };
  const a = outcome(start('A', [building()]));
  await new Promise(resolve => setTimeout(resolve, 5));
  const bAnswer = {...READY, rows: [{company: 'Beta Bank', amount: 5}]};
  const b = outcome(start('B', [building(), building(), bAnswer]));
  const [ra, rb] = await Promise.all([a, b]);
  assert.equal(ra.error && ra.error.name, 'AbortError');
  assert.equal(rb.value, bAnswer);
  assert.equal(counts.B, 3);
  const aAsks = counts.A;
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(counts.A, aAsks, 'A asks nothing after B started');
});

test('current() turning false (a newer inspection, another dataset) ends the poll before its next request', async () => {
  let latest = 1;
  const mine = 1;
  const h = harness([building(), building(), READY]);
  const seen = [];
  const {error} = await outcome(contributions.pollPending(h.ask, {body: BODY, now: h.now, current: () => latest === mine,
    sleep: async ms => { h.sleeps.push(ms); latest = 2; }, onProgress: p => seen.push(p)}));
  assert.equal(error.name, 'AbortError');
  assert.equal(h.asks.length, 1);
  assert.equal(seen.length, 1);
});

test('about 15 minutes of pending answers ends in an honest, retryable error, and time spent hidden does not count', async () => {
  assert.equal(contributions.PENDING_CEILING_MS, 15 * 60 * 1000);
  const h = harness([building({retry_after_ms: 1500})]);
  const {error} = await outcome(contributions.pollPending(h.ask, {body: BODY, sleep: h.sleep, now: h.now}));
  assert.equal(error.name, 'PendingCeiling');
  assert.equal(error.retryable, true);
  assert.equal(error.message, 'The server is still preparing this query view after about 15 minutes, so Chart Studio stopped checking. It may still finish there; Retry checks again.');
  // Asked at 0 s, 1.5 s, ... 900 s: the answer at 900 s is the one that stops it.
  assert.equal(h.asks.length, 601);
  assert.equal(h.sleeps.length, 600);
  assert.equal(error.progress.waitedMs, 900000);

  const r = harness([building({phase: 'records_sidecar', retry_after_ms: 1500})]);
  const records = await outcome(contributions.pollPending(r.ask, {body: BODY, path: 'records', sleep: r.sleep, now: r.now}));
  assert.match(records.error.message, /^The server is still preparing these source records after about 15 minutes/);

  const short = harness([building()]);
  const brief = await outcome(contributions.pollPending(short.ask, {body: BODY, sleep: short.sleep, now: short.now, ceilingMs: 2000}));
  assert.match(brief.error.message, /after about 2 seconds/);

  // Twenty minutes hidden between two polls is not twenty minutes of polling.
  const g = harness([building({retry_after_ms: 1500}), building({retry_after_ms: 1500}), READY]);
  let gates = 0;
  const result = await contributions.pollPending(g.ask, {body: BODY, sleep: g.sleep, now: g.now,
    gate: async () => { gates += 1; if (gates === 1) g.advance(20 * 60 * 1000); }});
  assert.equal(result, READY);
  assert.equal(gates, 2, 'the gate is asked between polls, never before the first request');
});

test('progress text: a share when the total is known, the records so far when not, the server elapsed time, and plain defaults', () => {
  const read = contributions.pendingProgress, text = contributions.pendingText, detail = contributions.pendingDetail;
  const known = read(building({processed_records: 296285, total_records: 592570, elapsed_ms: 12400}));
  assert.equal(known.percent, 50);
  assert.equal(text(known), MESSAGE + ' 50% · 296,285 of 592,570 records · 0:12 elapsed');
  assert.equal(text(known, {elapsed: false}), MESSAGE + ' 50% · 296,285 of 592,570 records');
  assert.equal(detail(known), '50% · 296,285 of 592,570 records · 0:12 elapsed');

  const unknown = read(building({elapsed_ms: 61000}));
  assert.equal(unknown.percent, null);
  assert.equal(text(unknown), MESSAGE + ' 1:01 elapsed', 'a null total: the message and the elapsed time only');
  assert.equal(text(unknown, {elapsed: false}), MESSAGE);

  // Today's preparing answer has no elapsed_ms: the page's own wait is shown.
  const preparing = read(PREPARING, {waitedMs: 3400});
  assert.equal(text(preparing), 'Indexing published receipt identities in bounded storage. 20,000 records so far · 0:03 elapsed');

  assert.equal(text(read({status: 'building'}, {waitedMs: 5000})), 'Preparing the query view… 0:05 elapsed');
  assert.equal(text(read({status: 'building', phase: 'records_sidecar'})), 'Preparing the source records… 0:00 elapsed');
  assert.equal(text(read({status: 'building', message: '   '}, {path: 'records'})), 'Preparing the source records… 0:00 elapsed');

  // Read strictly: over-full is 100%, a zero total or a string is not a total, a negative count is 0.
  assert.equal(read(building({processed_records: 5000, total_records: 4000})).percent, 100);
  assert.equal(read(building({processed_records: 10, total_records: 0})).percent, null);
  assert.equal(read(building({processed_records: '12', total_records: '40'})).percent, null);
  assert.equal(read(building({processed_records: -3})).processed, 0);
  assert.equal(read(building({elapsed_ms: -5}), {waitedMs: 2000}).elapsedMs, 2000);
});

test('Retry sends retry:true on its first request only, and never touches the caller\'s body', async () => {
  const h = harness([building(), building(), READY]);
  const body = {...BODY};
  await contributions.pollPending(h.ask, {body, retry: true, sleep: h.sleep, now: h.now});
  assert.equal(h.asks[0].retry, true);
  assert.deepEqual({...h.asks[0], retry: undefined}, {...BODY, retry: undefined});
  assert.equal(h.asks[1], body);
  assert.equal(h.asks[2], body);
  assert.equal('retry' in body, false);
});

test('a build that failed arrives on the next poll as an error: thrown as sent and marked retryable; an AbortError is not', async () => {
  const h = harness([building(), new Error('The Banking query view could not be built.')]);
  const {error} = await outcome(contributions.pollPending(h.ask, {body: BODY, sleep: h.sleep, now: h.now}));
  assert.equal(error.message, 'The Banking query view could not be built.');
  assert.equal(error.retryable, true);
  assert.equal(h.asks.length, 2);

  const aborted = Object.assign(new Error('The user aborted a request.'), {name: 'AbortError'});
  const a = harness([aborted]);
  const stopped = await outcome(contributions.pollPending(a.ask, {body: BODY, sleep: a.sleep, now: a.now}));
  assert.equal(stopped.error, aborted);
  assert.equal(stopped.error.retryable, undefined);

  const f = harness([{status: 'failed', message: 'Source verification failed: fixture.'}]);
  const failed = await outcome(contributions.pollPending(f.ask, {body: BODY, sleep: f.sleep, now: f.now}));
  assert.equal(failed.error.message, 'Source verification failed: fixture.');
  assert.equal(failed.error.retryable, true);
});

test('a display that throws while showing progress is reported, and the poll still reaches its answer', async () => {
  const h = harness([building(), READY]);
  const logged = [], original = console.error;
  console.error = error => logged.push(error);
  try {
    const result = await contributions.pollPending(h.ask, {body: BODY, sleep: h.sleep, now: h.now, onProgress: () => { throw new Error('display broke'); }});
    assert.equal(result, READY);
  } finally { console.error = original; }
  assert.equal(logged.length, 1);
  assert.equal(logged[0].message, 'display broke');
});
