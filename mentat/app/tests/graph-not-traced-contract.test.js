// The consumer half of contracts/graph-not-traced.json (F4, Paul approved
// 2026-09-25): every drawer answer the server produced is fed to graph.js's
// drawerNote, the function applyDrawer writes the drawer's line with.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const graph = require('../graph.js');

const contract = JSON.parse(fs.readFileSync(path.resolve(__dirname, '..', '..', '..', 'audits',
  'mentat_failings_20260915', 'contracts', 'graph-not-traced.json'), 'utf8'));
const drawers = contract.examples.filter(example => example.name.startsWith('drawer:'));

test('every drawer answer reads as the server traced it', () => {
  assert.equal(drawers.length, 3);
  for (const {name, payload} of drawers) {
    const trace = payload.transfer_trace;
    const said = graph.drawerNote(trace, 'the companies');
    assert.ok(said.text.endsWith(trace.note), name);
    assert.equal(/and the PACs it funds/.test(said.text), trace.root_traced, name);
    assert.equal(said.command, trace.command, name);
  }
});

test('the untraced committee says so in the line, and its rebuild command is only in the tooltip', () => {
  const stale = drawers.find(example => /stale/.test(example.name)).payload;
  const said = graph.drawerNote(stale.transfer_trace, 'the companies');
  assert.equal(said.text, 'Reported activity of this committee, not money attributed to the companies. '
    + 'Transfers from this committee are not traced: no saved network, and the transfer index for 2026 is stale.');
  assert.match(said.command, /^Rebuild with: python /);
  assert.equal(stale.candidate_note_title, said.command);
});

test('negative control: without the server\'s trace the line cannot say it was not traced', () => {
  const untraced = drawers.find(example => /no Transfer Paths sheet/.test(example.name)).payload;
  assert.equal(untraced.transfer_trace.root_traced, false);
  assert.equal(graph.drawerNote(null, 'the companies').text,
    'Reported activity of this committee and the PACs it funds, not money attributed to the companies.');
});
