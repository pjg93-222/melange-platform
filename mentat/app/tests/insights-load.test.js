/* Insights first open and status polling, asserted without a browser (perf,
   2026-09-24). insights.js returns at its own `module.exports` guard, so
   requiring it here costs the pure helpers and no DOM.

   pollDelay: the wait between status polls honours the server's
   retry_after_ms down to 200 ms (the floor was 500 ms), keeps the 5 s cap
   and the 5 s backoff after 40 polls, and still polls an old server - which
   answers 1500 ms while preparing - every 1.5 s.
   earlyQuery / sameQuery: the first-open body formed before the catalog
   arrives, and the rule load() uses to decide whether that early reply
   answers the body it forms once the catalog is installed.
   The request order itself (the query leaving beside the catalog) is a page
   behaviour: tests/test_insights_first_open_browser.cjs.

   Run this suite with the glob form; Node 24 rejects a bare directory:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/)
   MENTAT_INSIGHTS_MODULE points it at another copy (the negative control). */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const insights = require(process.env.MENTAT_INSIGHTS_MODULE ? path.resolve(process.env.MENTAT_INSIGHTS_MODULE) : '../insights.js');

test('[insights-poll] the server pace is honoured down to 200 ms', () => {
  assert.equal(insights.pollDelay(0, 300), 300, 'a genuine build asks for 300 ms first');
  assert.equal(insights.pollDelay(1, 750), 750, 'then 750 ms');
  assert.equal(insights.pollDelay(0, 200), 200);
  assert.equal(insights.pollDelay(0, 120), 200, 'nothing below the 200 ms floor');
  assert.equal(insights.pollDelay(0, 1), 200);
});

test('[insights-poll] an old server is still polled every 1.5 s; the cap and the backoff are unchanged', () => {
  assert.equal(insights.pollDelay(0, 1500), 1500, 'an old server answers 1500 ms while preparing');
  assert.equal(insights.pollDelay(39, 1500), 1500);
  assert.equal(insights.pollDelay(0, 2000), 2000, 'the busy answer: other requests are finishing');
  assert.equal(insights.pollDelay(0, undefined), 1500, 'no hint: 1.5 s');
  assert.equal(insights.pollDelay(0, 0), 1500, 'a zero hint is no hint');
  assert.equal(insights.pollDelay(0, 60000), 5000, 'the 5 s cap');
  for (const hint of [undefined, 200, 300, 1500, 60000]) assert.equal(insights.pollDelay(40, hint), 5000, 'after 40 polls, every 5 s');
  assert.equal(insights.pollDelay(219, 300), 5000);
});

test('[insights-first-open] the early body is the formed body with the known industry', () => {
  const formed = {dataset: '', basis: 'receipt', panels: ['favorites', 'trends', 'changes']};
  assert.equal(insights.earlyQuery(formed, '', false), null, 'no known industry: the catalog chooses, so no early body');
  assert.equal(insights.earlyQuery(formed, undefined, false), null);
  assert.deepEqual(insights.earlyQuery(formed, 'Banking', false), {dataset: 'Banking', basis: 'receipt', panels: ['favorites', 'trends', 'changes']});
  assert.deepEqual(insights.earlyQuery(formed, 'Banking', true), {dataset: 'Banking', basis: 'receipt', panels: ['favorites', 'trends', 'changes'], retry: true},
    'an explicit retry travels with the early body, as it does with the formed one');
  assert.deepEqual(formed, {dataset: '', basis: 'receipt', panels: ['favorites', 'trends', 'changes']}, 'the formed body is not mutated');
  const compared = {dataset: '', basis: 'receipt', panels: ['favorites'], comparison_dataset: 'Banking', as_of: '2026-06-30'};
  assert.deepEqual(insights.earlyQuery(compared, 'Banking', false), {dataset: 'Banking', basis: 'receipt', panels: ['favorites'], as_of: '2026-06-30'},
    'an industry never compares with itself (updateComparisonOptions clears it)');
  assert.equal(insights.earlyQuery(compared, 'Telecom', false).comparison_dataset, 'Banking');
});

test('[insights-first-open] an early reply answers only exactly the body formed after the catalog', () => {
  const body = {dataset: 'Banking', basis: 'receipt', panels: ['favorites', 'trends', 'changes']};
  assert.equal(insights.sameQuery(body, {panels: ['favorites', 'trends', 'changes'], basis: 'receipt', dataset: 'Banking'}), true, 'key order does not matter');
  assert.equal(insights.sameQuery({...body, company: 'Citibank'}, {...body, company: 'Citibank'}), true);
  assert.equal(insights.sameQuery(body, {...body, dataset: 'Telecom'}), false, 'another industry');
  assert.equal(insights.sameQuery(body, {...body, panels: ['trends', 'favorites', 'changes']}), false, 'panel order is part of the body');
  assert.equal(insights.sameQuery(body, {...body, comparison_dataset: 'Telecom'}), false, 'an extra field');
  assert.equal(insights.sameQuery(body, {...body, retry: true}), false, 'a retry resets a failed job, so it is part of the body');
});