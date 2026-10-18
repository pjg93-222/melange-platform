'use strict';
// Evaluate actual generated-script functions, without importing the Python
// producer, loading a personal payload, opening a browser, or running timers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const repo = process.env.FEC_SURFACE_SOURCE_ROOT || path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(repo, 'fec/surfaces/make_lobby_network_html.py'), 'utf8');

function actualFunction(name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert.ok(start >= 0 && source.indexOf(marker, start + 1) < 0, `unique ${name}`);
  const lineEnd = source.indexOf('\n', start);
  // All selected source functions close at column zero; nested closures do not.
  const end = source.slice(start, lineEnd).endsWith('}') ? lineEnd : source.indexOf('\n}', lineEnd) + 2;
  assert.ok(end > start, `complete ${name}`);
  return source.slice(start, end);
}

function fixture() {
  const nodes = new Map();
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {innerHTML: '', textContent: '', value: '',
        classList: {toggle() {}, remove() {}}, scrollIntoView() {}});
      return nodes.get(id);
    },
    querySelectorAll() { return []; },
  };
  const industry = sector => ({sector, members: [{id: 'F000001', name: 'Fixture Member', total: 2,
    change: 0, values: []}], organizations: [], roster: []});
  const company = (id, sector) => ({id, name: id, sector, bills: [], memberTotal: 1,
    members: [{i: 'F000001', n: 2, c: 0, v: []}]});
  const DATA = {industries: [industry('bank'), industry('insurance')],
    companies: [company('matched', 'insurance'), company('unmatched', null)],
    companyMembers: {F000001: {name: 'Fixture Member'}}, companyBills: {},
    years: [2025, 2026], boundaries: []};
  const context = vm.createContext({DATA, document, console,
    esc: value => String(value ?? ''), number: String, displaySector: String,
    memberLink: name => name, postureBadge: () => '', lineSpark: () => '',
    companyBoundary: () => '', renderCompanyPicker() {}, renderCompanyPublicMentions() {},
    renderPublicMentions() {}, setRowCount() {}, boundaryBanner: () => '',
    hashValue: () => '', score: String});
  vm.runInContext(`let currentSector='bank', attentionExpanded=false, publicMentionsExpanded=false,
    activeCompanyId=null, activeYear=2025, tierFilter='';
    const EM_DASH='—', INTERACTIONS_FILE='lobby-interactions.html',
    TIER_META={enabled:false,sectors:[]}, TIER_ORDER=[], ALL_YEARS='all',
    PARTIAL_YEAR=2026, THROUGH_QUARTER='';`, context);
  vm.runInContext(['industry', 'interactionLink', 'renderAttention', 'renderTierRoster',
    'openOrganization'].map(actualFunction).join('\n'), context);
  return {nodes, context, DATA, run: code => vm.runInContext(code, context)};
}

test('sectorless organization keeps industry controls usable without false filing links', () => {
  const f = fixture();
  assert.equal(f.run("openOrganization('unmatched',false,false)"), true);
  assert.equal(f.run('currentSector'), 'bank');
  f.run('attentionExpanded=true; renderAttention(); activeYear=2026; renderTierRoster()');
  assert.equal(f.nodes.get('industry-title').textContent, 'bank');
  assert.match(f.nodes.get('asof-chip').textContent, /^As of 2026/);
  assert.match(f.nodes.get('attention-body').innerHTML, /sector=bank&b=F000001/);
  assert.doesNotMatch(f.nodes.get('company-attention-body').innerHTML, /lobby-interactions\.html/);
});

test('matched organization still selects its sector and filing links', () => {
  const f = fixture();
  assert.equal(f.run("openOrganization('matched',false,false)"), true);
  assert.equal(f.run('currentSector'), 'insurance');
  assert.match(f.nodes.get('company-attention-body').innerHTML, /sector=insurance&b=F000001/);
  assert.equal(f.run("openOrganization('absent',false,false)"), false);
  assert.equal(f.run('currentSector'), 'insurance');
});

test('actual roster renderer uses singular and plural merger boundary wording', () => {
  const f = fixture();
  f.DATA.boundaries = [{s: null}];
  f.run('renderTierRoster()');
  assert.match(f.nodes.get('roster-note').textContent, /1 declared merger boundary .* therefore carries no row badge/);
  f.DATA.boundaries.push({s: null});
  f.run('renderTierRoster()');
  assert.match(f.nodes.get('roster-note').textContent, /2 declared merger boundaries .* therefore carry no row badge/);
  assert.doesNotMatch(f.nodes.get('roster-note').textContent, /boundaryies/);
});
