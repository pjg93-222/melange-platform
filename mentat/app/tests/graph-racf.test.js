/* RACF on the client (Paul, 2026-09-24): the memory tier's ETag rule, its
   bounds, the likely-next plan and the idle prefetch scheduler, asserted
   without a browser. graph.js returns at its own `typeof document` guard, so
   requiring it here costs the pure model and no DOM.

   The rule under test: a parsed /api/graph answer is reused only for the URL
   it was read for and only while the server confirms the ETag it was parsed
   from; prefetch reads one likely-next view at a time, never while another
   /api/graph read is in flight, never while the page is hidden, and stops at
   once when cancelled; its reads, and only its reads, carry the request
   header X-Mentat-Prefetch: 1 (review H4), so the server never records them
   as user accesses.

   Run this suite with the glob form; Node 24 rejects a bare directory:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/)
   MENTAT_GRAPH_MODULE points it at another copy (the negative control). */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const graph = require(process.env.MENTAT_GRAPH_MODULE ? path.resolve(process.env.MENTAT_GRAPH_MODULE) : '../graph.js');

test('[graph-racf] a held answer is reused only for its URL and only under the ETag it was parsed from', () => {
  const memory = graph.graphMemoryModel();
  const body = {filters: {cycle: '2024'}};
  assert.equal(memory.put('/api/graph?industry=A', '"e1"', body, 100), true);
  assert.equal(memory.take('/api/graph?industry=A', '"e1"'), body, 'the confirmed ETag hands back the same parsed object');
  assert.equal(memory.take('/api/graph?industry=A', '"e2"'), null, 'a changed ETag is a miss, never the old view');
  assert.equal(memory.take('/api/graph?industry=A', ''), null, 'no ETag confirms nothing');
  assert.equal(memory.take('/api/graph?industry=B', '"e1"'), null, 'another URL with the same ETag is a miss');
  assert.equal(memory.put('/api/graph?industry=C', '', {}, 10), false, 'an answer without an ETag is never held');
  assert.equal(memory.has('/api/graph?industry=C'), false);
  assert.equal(memory.peek('/api/graph?industry=A').etag, '"e1"', 'peek names the ETag the view was confirmed under');
  assert.equal(memory.peek('/api/graph?industry=B'), null);
});

test('[graph-racf] a new ETag for a URL replaces the old answer, and a dropped URL is gone', () => {
  const memory = graph.graphMemoryModel();
  const old = {v: 1}, fresh = {v: 2};
  memory.put('/u', '"old"', old, 40);
  memory.put('/u', '"new"', fresh, 25);
  assert.equal(memory.size(), 1, 'one entry per URL');
  assert.equal(memory.chars(), 25, 'the replaced answer no longer counts');
  assert.equal(memory.take('/u', '"old"'), null, 'the superseded ETag is not served');
  assert.equal(memory.take('/u', '"new"'), fresh);
  memory.drop('/u');
  assert.equal(memory.peek('/u'), null);
  assert.equal(memory.chars(), 0);
});

test('[graph-racf] the least recently used answer leaves first, by count and by JSON text length', () => {
  const byCount = graph.graphMemoryModel({entries: 3, chars: 1000});
  byCount.put('/a', '"a"', {}, 1); byCount.put('/b', '"b"', {}, 1); byCount.put('/c', '"c"', {}, 1);
  assert.ok(byCount.peek('/a'), 'reading /a makes it the most recent');
  byCount.put('/d', '"d"', {}, 1);
  assert.deepEqual(['/a', '/b', '/c', '/d'].filter(url => byCount.has(url)), ['/a', '/c', '/d'], '/b, the least recently used, left');
  const byChars = graph.graphMemoryModel({entries: 30, chars: 100});
  byChars.put('/x', '"x"', {}, 60); byChars.put('/y', '"y"', {}, 30);
  byChars.put('/z', '"z"', {}, 20);
  assert.deepEqual(['/x', '/y', '/z'].filter(url => byChars.has(url)), ['/y', '/z'], 'over 100 characters, the oldest left');
  assert.equal(byChars.chars(), 50);
  assert.equal(byChars.put('/huge', '"h"', {}, 101), false, 'an answer larger than the whole budget is not held');
  assert.equal(byChars.put('/y', '"y2"', {}, 101), false);
  assert.equal(byChars.has('/y'), false, 'and it drops the older answer for its URL rather than leave it standing');
  assert.equal(graph.MEMORY_ENTRIES, 30);
  assert.equal(graph.MEMORY_CHARS, 30 * 1024 * 1024);
});

const toUrl = scope => '/api/graph?' + new URLSearchParams({id: scope.run || '', industry: scope.industry || '', company: scope.company || '',
  tier: scope.tier || '', cycle: scope.cycle || '', funding: scope.funding || 'all', mode: scope.mode || 'direct',
  direction: scope.direction || 'Support', category: scope.category || '', recipient: scope.recipient || ''});
const SCOPE = {run: 'r', industry: 'Banking', company: '', tier: '', cycle: '2024', funding: 'all', mode: 'direct', direction: 'Support', category: 'PAC', recipient: 'C001'};
const VIEW = {filters: {company: '', cycle: '2024'}, options: {companies: ['A', 'B', 'C', 'D', 'E'], cycles: ['2022', '2024', '2026']},
  companies: [{id: 'B', amount: 50}, {id: 'A', amount: 90}, {id: 'Z', amount: 999}, {id: 'C', amount: 40}, {id: 'D', amount: 30}, {id: 'E', amount: 20}]};

test('[graph-racf] the plan is the other top companies at this cycle, then the adjacent cycle, as the controls would ask', () => {
  const plan = graph.prefetchPlan(VIEW, SCOPE, toUrl);
  const read = plan.map(url => Object.fromEntries(new URLSearchParams(url.slice('/api/graph?'.length))));
  assert.equal(plan.length, 4, 'at most four');
  assert.equal(graph.PREFETCH_MAX, 4);
  assert.deepEqual(read.map(q => [q.company, q.cycle]), [['A', '2024'], ['B', '2024'], ['C', '2024'], ['', '2022']],
    'ranked by amount, a company the select does not offer (Z) skipped, the earlier cycle last');
  assert.ok(read.every(q => q.category === '' && q.recipient === ''), 'a company or cycle change clears category and recipient, as the selects do');
  assert.ok(read.every(q => q.industry === 'Banking' && q.id === 'r' && q.mode === 'direct'));
  assert.equal(plan[0], toUrl({...SCOPE, company: 'A', category: '', recipient: ''}), 'byte-identical to the request refresh() would send');
});

test('[graph-racf] a company view skips itself; the first cycle looks forward; All cycles has no adjacent cycle', () => {
  const own = graph.prefetchPlan({...VIEW, filters: {company: 'A', cycle: '2022'}}, {...SCOPE, company: 'A', cycle: '2022'}, toUrl);
  const read = own.map(url => Object.fromEntries(new URLSearchParams(url.slice('/api/graph?'.length))));
  assert.deepEqual(read.map(q => [q.company, q.cycle]), [['B', '2022'], ['C', '2022'], ['D', '2022'], ['A', '2024']]);
  const all = graph.prefetchPlan({...VIEW, filters: {company: '', cycle: ''}}, {...SCOPE, cycle: ''}, toUrl);
  assert.equal(all.length, 4);
  assert.ok(all.every(url => new URLSearchParams(url.slice('/api/graph?'.length)).get('cycle') === ''), 'no cycle is invented');
  assert.deepEqual(graph.prefetchPlan(null, SCOPE, toUrl), []);
  assert.deepEqual(graph.prefetchPlan(VIEW, SCOPE, toUrl, 0), []);
});

// A scheduler whose idle periods, visibility and in-flight reads the test drives.
function harness() {
  const env = {idles: [], reads: [], visibleNow: true, busyNow: false, held: new Set()};
  env.idle = callback => { env.idles.push(callback); return env.idles.length; };
  env.cancelIdle = handle => { env.idles[handle - 1] = null; };
  env.visible = () => env.visibleNow;
  env.busy = () => env.busyNow;
  env.has = url => env.held.has(url);
  env.read = (url, signal) => new Promise((resolve, reject) => {
    const entry = {url, signal, resolve};
    env.reads.push(entry);
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
  });
  env.flush = () => { const pending = env.idles.splice(0).filter(Boolean); pending.forEach(callback => callback()); return pending.length; };
  env.settle = () => new Promise(resolve => setImmediate(resolve));
  return env;
}

test('[graph-racf] prefetch reads one view at a time, and only in an idle period', async () => {
  const env = harness(), scheduler = graph.prefetchScheduler(env);
  scheduler.start(['/1', '/2', '/3']);
  assert.equal(env.reads.length, 0, 'nothing is read before the page is idle');
  env.flush(); await env.settle();
  assert.deepEqual(env.reads.map(r => r.url), ['/1']);
  env.flush(); await env.settle();
  assert.deepEqual(env.reads.map(r => r.url), ['/1'], 'no second read while the first is under way');
  env.reads[0].resolve(); await env.settle();
  env.flush(); await env.settle();
  assert.deepEqual(env.reads.map(r => r.url), ['/1', '/2'], 'the next read waits for the first to end and for idle time');
});

test('[graph-racf] prefetch never runs beside another /api/graph read, skips what memory holds, and stops when hidden', async () => {
  const env = harness(), scheduler = graph.prefetchScheduler(env);
  env.busyNow = true; env.held.add('/1');
  scheduler.start(['/1', '/2', '/3']);
  env.flush(); await env.settle(); env.flush(); await env.settle();
  assert.equal(env.reads.length, 0, 'a read in flight holds the plan back');
  env.busyNow = false;
  env.flush(); await env.settle();
  assert.deepEqual(env.reads.map(r => r.url), [], 'a URL already in memory is skipped without a read');
  env.flush(); await env.settle();
  assert.deepEqual(env.reads.map(r => r.url), ['/2']);
  env.reads[0].resolve(); await env.settle();
  env.visibleNow = false;
  env.flush(); await env.settle();
  assert.equal(env.reads.length, 1, 'a hidden page reads nothing more');
  assert.deepEqual(scheduler.pending(), [], 'and drops the rest of the plan');
});

test('[graph-racf] cancel aborts the read under way and drops the plan; start keeps at most four', async () => {
  const env = harness(), scheduler = graph.prefetchScheduler(env);
  scheduler.start(['/1', '/2', '/3', '/4', '/5', '/6']);
  assert.deepEqual(scheduler.pending(), ['/1', '/2', '/3', '/4'], 'at most N=4 likely-next views');
  env.flush(); await env.settle();
  assert.equal(scheduler.reading(), true);
  scheduler.cancel();
  assert.equal(env.reads[0].signal.aborted, true, 'user interaction aborts the read under way');
  assert.equal(scheduler.reading(), false);
  assert.deepEqual(scheduler.pending(), []);
  await env.settle(); env.flush(); await env.settle();
  assert.equal(env.reads.length, 1, 'nothing further is read after a cancel');
  scheduler.start(['/7']);
  env.flush(); await env.settle();
  assert.deepEqual(env.reads.map(r => r.url), ['/1', '/7'], 'a new plan after the cancel runs on its own');
});

/* Review H4 (2026-09-24): the idle prefetch must not count as a user access.
   Prefetch reads carry the request header X-Mentat-Prefetch: 1 (server.py
   then answers the same but does not call record_access); a reader's own
   read sends exactly what it sent before. Both use the same URL, so the
   browser's cache entry, the ETag and the memory tier are shared. */
const PREFETCH = 'X-Mentat-Prefetch';
// A stand-in fetch that notes each request's URL and init and answers with
// one ETag; while hold(true), answers wait for release().
function fakeFetch(etag = '"e1"') {
  const calls = [], waiting = [];
  let holding = false;
  const fetcher = (url, init) => {
    calls.push({url, init});
    const answer = {ok: true, headers: {get: name => (name === 'ETag' ? etag : null)},
      text: () => Promise.resolve(JSON.stringify({url})), body: {cancel: () => Promise.resolve()}};
    return holding ? new Promise(resolve => waiting.push(() => resolve(answer))) : Promise.resolve(answer);
  };
  return {fetcher, calls, hold: on => { holding = on; }, release: () => waiting.splice(0).forEach(go => go())};
}
const marked = init => new Headers((init && init.headers) || {}).get(PREFETCH);

test('[graph-racf] a prefetch read carries X-Mentat-Prefetch: 1; a reader\'s own read of the same URL sends nothing new', async () => {
  const {fetcher, calls} = fakeFetch();
  const reader = graph.graphReader(graph.graphMemoryModel(), fetcher);
  const url = '/api/graph?industry=Banking&company=A&cycle=2024';
  const signal = new AbortController().signal;
  const guessed = await reader.prefetch(url, signal);
  const clicked = await reader.read(url, signal);
  assert.deepEqual(calls.map(call => call.url), [url, url], 'the same URL, so one browser cache entry and one ETag');
  assert.equal(marked(calls[0].init), '1', 'the prefetch read is marked');
  assert.equal(marked(calls[1].init), null, 'the reader\'s own read is not');
  assert.deepEqual(Object.keys(calls[1].init).sort(), ['cache', 'signal'], 'and sends exactly what it sent before the fix');
  assert.equal(calls[1].init.signal, signal);
  assert.ok(calls.every(call => call.init.cache === 'no-cache'), 'both revalidate by ETag');
  assert.equal(guessed.reused, false);
  assert.equal(clicked.reused, true, 'the click reuses the answer the prefetch parsed (one memory tier)');
  assert.equal(clicked.body, guessed.body);
  assert.equal(graph.PREFETCH_HEADER, PREFETCH);
});

test('[graph-racf] the page\'s prefetcher reads only through the marked path, and a reader\'s read beside it holds it back', async () => {
  const env = harness(), {fetcher, calls, hold, release} = fakeFetch();
  const reader = graph.graphReader(graph.graphMemoryModel(), fetcher);
  const scheduler = graph.graphPrefetcher(reader, env);
  hold(true);
  const own = reader.read('/api/graph?company=Z', undefined);
  scheduler.start(['/api/graph?company=A', '/api/graph?company=B']);
  env.flush(); await env.settle(); env.flush(); await env.settle();
  assert.deepEqual(calls.map(call => call.url), ['/api/graph?company=Z'], 'no prefetch while the reader\'s own read is in flight');
  release(); await own; await env.settle();
  for (let turn = 0; turn < 6 && calls.length < 3; turn++) { env.flush(); await env.settle(); release(); await env.settle(); }
  hold(false);
  assert.deepEqual(calls.map(call => call.url), ['/api/graph?company=Z', '/api/graph?company=A', '/api/graph?company=B']);
  assert.deepEqual(calls.map(call => marked(call.init)), [null, '1', '1'], 'every planned read is marked, the reader\'s own read is not');
  scheduler.start(['/api/graph?company=A']);
  env.flush(); await env.settle();
  assert.equal(calls.length, 3, 'a prefetched answer is held, so the plan skips it');
});
