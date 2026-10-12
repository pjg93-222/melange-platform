/* Company sparklines' access to contribution data (perf H11, 2026-09-24),
   asserted without a browser. graph.js returns at its own `typeof document`
   guard, so requiring it here costs the pure model and no DOM.

   The contributions catalog does not depend on the industry: one read per
   page load serves every industry (it was one read per industry, after
   /api/graph). The session stays per industry. A failed read is forgotten,
   and a copy read in an earlier turn that lacks an industry is read again.

   Run this suite with the glob form; Node 24 rejects a bare directory:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/)
   MENTAT_GRAPH_MODULE points it at another copy (the negative control). */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const graph = require(process.env.MENTAT_GRAPH_MODULE ? path.resolve(process.env.MENTAT_GRAPH_MODULE) : '../graph.js');

const CATALOG = {datasets: [{id: 'Banking', label: 'Banking', available: true}, {id: 'Telecom', label: 'Telecommunications', available: true}, {id: 'Crypto', label: 'Crypto', available: false}]};
// One answer per catalog read, in order (the last repeats); an Error answers with a failure.
function reader(answers) {
  const log = {catalog: 0, sessions: []}, queue = answers.slice();
  return {log, read: {
    catalog: () => { log.catalog += 1; const next = queue.length > 1 ? queue.shift() : queue[0]; return next instanceof Error ? Promise.reject(next) : Promise.resolve(next); },
    session: industry => { log.sessions.push(industry); return Promise.resolve({token: 'token-' + industry}); }}};
}

test('[graph-spark] one catalog read serves every industry; the session stays per industry', async () => {
  const {log, read} = reader([CATALOG]);
  const access = graph.sparkAccessModel(read);
  assert.deepEqual(await access.source('Banking', 1), {token: 'token-Banking', dataset: 'Banking'});
  assert.deepEqual(await access.source('Telecommunications', 2), {token: 'token-Telecommunications', dataset: 'Telecom'}, 'a label names its dataset');
  assert.deepEqual(await access.source('Banking', 3), {token: 'token-Banking', dataset: 'Banking'}, 'an industry already read is answered from memory');
  assert.equal(log.catalog, 1, 'the industry-independent catalog is read once');
  assert.deepEqual(log.sessions, ['Banking', 'Telecommunications']);
});

test('[graph-spark] the read started beside /api/graph is the one the sparklines use', async () => {
  const {log, read} = reader([CATALOG]);
  const access = graph.sparkAccessModel(read);
  const started = access.catalog(1);
  assert.equal(access.catalog(1), started, 'callers share one pending read');
  assert.deepEqual(await access.source('Banking', 1), {token: 'token-Banking', dataset: 'Banking'});
  assert.equal(log.catalog, 1);
});

test('[graph-spark] a failed catalog read is forgotten, so the next caller reads again', async () => {
  const {log, read} = reader([new Error('Contribution catalog unavailable.'), CATALOG]);
  const access = graph.sparkAccessModel(read);
  await assert.rejects(access.source('Banking', 1), /Contribution catalog unavailable/);
  assert.deepEqual(await access.source('Banking', 2), {token: 'token-Banking', dataset: 'Banking'}, 'the failed industry is asked again');
  assert.equal(log.catalog, 2);
  await access.source('Telecommunications', 3);
  assert.equal(log.catalog, 2, 'the good read is kept');
});

test('[graph-spark] a copy from an earlier turn that lacks the industry is read again; a copy from this turn is not', async () => {
  const later = {datasets: [...CATALOG.datasets, {id: 'Fintech', label: 'Fintech', available: true}]};
  const {log, read} = reader([CATALOG, later]);
  const access = graph.sparkAccessModel(read);
  await access.source('Banking', 1);
  assert.deepEqual(await access.source('Fintech', 2), {token: 'token-Fintech', dataset: 'Fintech'}, 'a source that became available after the first read still draws');
  assert.equal(log.catalog, 2);
  await assert.rejects(access.source('Crypto', 2), /No verified quarterly contribution dataset/, 'an unavailable dataset is refused');
  assert.equal(log.catalog, 2, 'a copy read in this turn is not read again');
  await assert.rejects(access.source('Crypto', 3), /No verified quarterly contribution dataset/);
  assert.equal(log.catalog, 3, 'the next turn reads again for an industry still missing, as every turn used to');
});