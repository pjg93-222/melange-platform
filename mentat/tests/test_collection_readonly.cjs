const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the shipped renderer without starting a collector or a browser.
const source = fs.readFileSync(path.join(__dirname, '../app/app.js'), 'utf8');
const renderSource = source.match(/function render\(run\) \{[\s\S]*?\n\}/)[0];
function renderRun(readOnly, state = 'running') {
  const controls = new Map();
  const node = id => {
    if (!controls.has(id)) controls.set(id, {classList: {toggle() {}}, textContent: '', innerHTML: ''});
    return controls.get(id);
  };
  const context = vm.createContext({
    $: node, readOnly, selectedRun: 'fixture', selectedLane: '', runCatalog: [], resumeBusy: false,
    MentatCoverage: {html: () => ''}, setHtml() {}, setRunPill() {}, runPill: x => x,
    number: x => String(x ?? 0), label: x => x, escapeHtml: x => String(x ?? ''),
    duration: x => x, renderRunCard() {}, updateTimer() {}, performance: {now: () => 0},
    run: {id: 'fixture', name: 'Disposable fixture', state, age_seconds: 1,
      status: {stage: state, fanout: {total: 2, completed: 1, running: 1}},
      reports: [], lanes: [], events: [], assurance: []},
  });
  vm.runInContext(renderSource + '\nrender(run);', context);
  return controls;
}

test('read-only active collection can be monitored but cannot pause or change workers', () => {
  const nodes = renderRun(true);
  for (const id of ['stop', 'worker-target', 'apply-workers']) {
    assert.equal(nodes.get(id).disabled, true, id);
    assert.match(nodes.get(id).title, /read-only/i, id);
  }
  assert.equal(nodes.get('monitor').hidden, false);
});
test('writable active collection retains its controls', () => {
  const nodes = renderRun(false);
  for (const id of ['stop', 'worker-target', 'apply-workers']) assert.equal(nodes.get(id).disabled, false, id);
});
test('completed collection cannot be paused or resized', () => {
  const nodes = renderRun(false, 'completed');
  for (const id of ['stop', 'worker-target', 'apply-workers']) assert.equal(nodes.get(id).disabled, true, id);
});
