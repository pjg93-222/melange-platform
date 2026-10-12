// F4 (Paul approved 2026-09-25): the Graph drawer says when money is not
// traced. graph.js's drawerNote is pure and exported, so this suite runs it on
// the shapes graph_data.transfer_trace answers with, without a browser.
const test = require('node:test');
const assert = require('node:assert/strict');
const graph = require('../graph.js');

const NO_SHEET = 'Transfers from this committee are not traced: no saved network, '
  + 'and this workbook has no Transfer Paths sheet.';

test('an untraced committee no longer claims the PACs it funds, and says why', () => {
  const said = graph.drawerNote({root_traced: false, note: NO_SHEET, command: ''}, 'the companies');
  assert.equal(said.text, 'Reported activity of this committee, not money attributed to the companies. ' + NO_SHEET);
  assert.equal(said.command, '');
});

test('a stale transfer index puts the rebuild command in the tooltip, not the line', () => {
  const command = 'Rebuild with: python "C:/Mentat/tools/fec_transfer_graph.py" --cycles 2024';
  const said = graph.drawerNote({root_traced: false, command,
    note: 'Transfers from this committee are not traced: no saved network, and the transfer index for 2024 is stale.'},
  'the company');
  assert.match(said.text, /^Reported activity of this committee, not money attributed to the company\. /);
  assert.match(said.text, /the transfer index for 2024 is stale\.$/);
  assert.doesNotMatch(said.text, /Rebuild with/);
  assert.equal(said.command, command);
});

test('committees past a traced one are named, and the line keeps its PACs', () => {
  const note = 'Transfers from 11 other committees in this network are not traced: no saved network, '
    + 'and this workbook has no Transfer Paths sheet.';
  const said = graph.drawerNote({root_traced: true, note, command: ''}, 'the companies');
  assert.equal(said.text, 'Reported activity of this committee and the PACs it funds, not money attributed to the companies. ' + note);
});

test('negative control: an answer with no trace (or an older console) reads exactly as before', () => {
  for (const trace of [undefined, null, {root_traced: true, note: '', command: ''}]) {
    assert.equal(graph.drawerNote(trace, 'the companies').text,
      'Reported activity of this committee and the PACs it funds, not money attributed to the companies.');
  }
});
