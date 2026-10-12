'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const contributions = require('../chart-studio-contributions.js');
const source = fs.readFileSync(path.join(__dirname, '../chart-studio.js'), 'utf8');
function shipped(start, end) {
  const at = source.indexOf(start);
  assert.ok(at >= 0);
  const until = source.indexOf(end, at);
  assert.ok(until > at);
  return source.slice(at, until);
}
const fields = ['company', 'cycle', 'funding_source', 'state'].map((id, index) => ({id, index}));
function handoff(filters, incoming) {
  const sheet = {filters};
  const scope = {industry: 'Pilot', filters: incoming, notCarried: []};
  const context = vm.createContext({pendingScope: scope, appliedScope: null, data: {},
    verified: () => true, columns: () => fields, sheet: () => sheet,
    commit: change => change(), failure: () => {}});
  vm.runInContext(shipped('  function applyGraphScope()', '\n  // A surface lets go'), context);
  context.applyGraphScope();
  return JSON.parse(JSON.stringify(sheet.filters));
}
test('a broader Graph scope clears old company/funding filters but retains an analytical state filter', () => {
  const actual = handoff([
    {column: 0, op: 'equals', value: 'Ripple Labs'},
    {column: 2, op: 'equals', value: 'employee_original'},
    {column: 3, op: 'equals', value: 'CA'}
  ], [{field: 'cycle', value: '2023-2024'}]);
  assert.deepEqual(actual, [
    {column: 3, op: 'equals', value: 'CA'},
    {column: 1, op: 'equals', value: '2023-2024', stage: 'row'}
  ]);
});
test('All companies, all cycles, all giving clears every previously carried filter', () => {
  assert.deepEqual(handoff(fields.slice(0, 3).map(f => ({column: f.index, op: 'equals', value: 'old'})), []), []);
});
test('a rendered map refreshes reconciliation rather than retaining a previous chart message', async () => {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); };
  const host = node('canvas'), slot = {revision: 0};
  const result = {rows: [], group_count: 0, columns: []};
  const view = {el: node, state: {}, result, title: 'Current map', scope: 'Current scope', stale: () => false};
  const seen = [];
  const context = vm.createContext({mainSurface: () => view, mapSlot: () => slot,
    mapHosts: new Map([[host, slot]]), geometryCache: new Map(), geometryPending: new Map(),
    contributionAPI: async () => ({sources: [], layers: {states: [], districts: []}}),
    contributionGeometry: null, geometryKey: '', verified: () => true,
    status: () => {}, fmt: String, dropMapsIn: () => {}, basisOf: () => 'receipt',
    contributions, contributionPlot: {format: String},
    MentatContributionMap: {eligibleZips: () => [], render: () => ({data: {marks: [], mapped_cents: 0, unmapped_cents: 0}})},
    renderReconcile: async (...args) => seen.push(args),
    clearTimeout, setTimeout});
  vm.runInContext(shipped('  async function renderContributionMap(', '\n  /* F3'), context);
  await context.renderContributionMap(8, {spec: {measures: ['amount']}}, null, view);
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], result);
  assert.equal(seen[0][1], view);
  assert.equal(seen[0][2], 8);
});
test('published corporate receipts versus classified corporate funding names the definition difference', () => {
  const result = contributions.reconcile({totals: {amount: 0}}, 'receipt',
    {totals: {giving: 51000000}}, {studioCycle: '2023-2024', graphCycle: '2024', funding: 'all', fundingClassification: true});
  assert.equal(result.agree, false);
  assert.equal(result.differenceCents, -5100000000);
  assert.equal(result.reasonId, 'funding_classification');
  assert.match(result.reason, /leave organization receipts unverified/);
});
test('CSV completion reaches the visible status and retains source provenance', async () => {
  const statusNode = {}, downloads = [];
  const result = {rows: [{company: 'Ripple Labs', amount: 5115297530}],
    columns: [{id: 'company'}, {id: 'amount', unit: 'cents'}], provenance: {workbook_sha256: 'verified-workbook'}};
  const view = {result, request: {basis: 'receipt', filters: []}, ticket: () => 1, stale: () => false};
  const context = vm.createContext({activeSurface: () => view, lastResult: result, exporting: false,
    structuredClone, status: () => {}, el: () => statusNode, compareDrawn: null,
    contributionReady: async () => result, contributions, fmt: String,
    MentatExport: {download: (...args) => downloads.push(args)}, failure: message => assert.fail(message)});
  vm.runInContext(shipped('  async function downloadContributions(', '\n  /* ---------------------------------------------------------------- pills'), context);
  await context.downloadContributions('csv');
  assert.match(statusNode.textContent, /CSV download requested: 1 groups/);
  assert.equal(downloads.length, 1);
  assert.match(downloads[0][0], /51152975.3/);
  assert.match(downloads[0][0], /verified-workbook/);
  assert.equal(downloads[0][2], 'mentat-chart-studio.csv');
  assert.equal(context.exporting, false);
});
