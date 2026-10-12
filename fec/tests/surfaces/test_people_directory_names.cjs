/* People & connections > Directory: office names (r5, audit D-2).

   The office-names block of people-ui.js is sliced out of the file and run in a vm, the same shape as the other .cjs
   suites here: no browser, no server, no temp files. The names below are the served payload's own forms
   (/api/staffer-groups-index, 2026-09-24): every House office spelling carries the House Statement of Disbursements'
   document year, two carry its continuation mark, and House and Senate share committee names. */
'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../surfaces/approved/clean/people-ui.js'), 'utf8');
const start = source.indexOf('  /* office-names:start'), end = source.indexOf('/* office-names:end */');
function load() {
  assert.ok(start > 0 && end > start, 'people-ui.js carries the office-names block');
  const ctx = {Map, String};
  vm.createContext(ctx);
  vm.runInContext(source.slice(start, end) + '\nthis.officeNames = officeNames; this.chamberOf = chamberOf;', ctx);
  return ctx;
}
const office = (chamber, key, name) => ({id: 'container:office:' + chamber + ':' + key, kind: 'office', name});

test('House page marks leave the shown name; the chamber is named; Senate spellings stay as stored', () => {
  const {officeNames} = load();
  const offices = [
    office('house', 'COMMITTEE ON APPROPRIATIONS', '2009 COMMITTEE ON APPROPRIATIONS'),
    office('senate', 'APPROPRIATIONS', 'APPROPRIATIONS'),
    office('house', 'COMMITTEE ON JUDICIARY', '2007 COMMITTEE ON JUDICIARY'),
    office('senate', 'JUDICIARY', 'JUDICIARY'),
    office('house', 'HON:M001', '2009 HON. JOHN P MURTHA—Con.'),
    office('house', 'HON:J000294', '2013 HON. HAKEEM S. JEFFRIES'),
    office('senate', 'SENATOR RICK SCOTT', 'SENATOR RICK SCOTT'),
  ];
  const names = officeNames(offices);
  const got = id => names.get(id);
  assert.deepEqual({...got('container:office:house:COMMITTEE ON APPROPRIATIONS')},
    {name: 'COMMITTEE ON APPROPRIATIONS', stored: '2009 COMMITTEE ON APPROPRIATIONS', chamber: 'House'});
  assert.deepEqual({...got('container:office:senate:APPROPRIATIONS')}, {name: 'APPROPRIATIONS', stored: 'APPROPRIATIONS', chamber: 'Senate'});
  assert.equal(got('container:office:house:COMMITTEE ON JUDICIARY').name, 'COMMITTEE ON JUDICIARY');
  assert.equal(got('container:office:senate:JUDICIARY').chamber, 'Senate');
  assert.equal(got('container:office:house:HON:M001').name, 'HON. JOHN P MURTHA', 'the continuation mark is a page mark too');
  assert.equal(got('container:office:house:HON:J000294').name, 'HON. HAKEEM S. JEFFRIES');
  assert.equal(got('container:office:senate:SENATOR RICK SCOTT').name, 'SENATOR RICK SCOTT');
  for (const g of offices) assert.equal(got(g.id).stored, g.name, 'the stored spelling is kept verbatim');
  // Nothing is re-cased: the shown name is a substring of the stored one.
  for (const g of offices) assert.ok(g.name.includes(got(g.id).name));
});

test('nothing is merged: one entry per office, and two offices that would read alike keep their stored spellings', () => {
  const {officeNames} = load();
  const offices = [
    office('house', 'HON:R000575', '2016 HON. MIKE ROGERS'),
    office('house', 'HON:MICHAEL|ROGERS', '2009 HON. MIKE ROGERS'),
    office('house', 'INTELLIGENCE', '2011 INTELLIGENCE'),
    office('senate', 'INTELLIGENCE', 'INTELLIGENCE'),
  ];
  const names = officeNames(offices);
  assert.equal(names.size, offices.length);
  assert.equal(names.get('container:office:house:HON:R000575').name, '2016 HON. MIKE ROGERS');
  assert.equal(names.get('container:office:house:HON:MICHAEL|ROGERS').name, '2009 HON. MIKE ROGERS');
  // The same committee name in both chambers is two offices, told apart by the chamber the row names.
  assert.equal(names.get('container:office:house:INTELLIGENCE').name, 'INTELLIGENCE');
  assert.equal(names.get('container:office:house:INTELLIGENCE').chamber, 'House');
  assert.equal(names.get('container:office:senate:INTELLIGENCE').chamber, 'Senate');
});

test('only House office ids lose a leading year: organisations and other ids keep names that start with digits', () => {
  const {officeNames, chamberOf} = load();
  const names = officeNames([
    {id: 'organization:lda:registrant:1', kind: 'organization', name: '1776 HEALTHCARE'},
    {id: 'container:office:senate:X', kind: 'office', name: '2009 SOMETHING'},
    office('house', 'EMPTY', '2009 '),
  ]);
  assert.equal(names.get('organization:lda:registrant:1').name, '1776 HEALTHCARE');
  assert.equal(names.get('organization:lda:registrant:1').chamber, '');
  assert.equal(names.get('container:office:senate:X').name, '2009 SOMETHING');
  assert.equal(names.get('container:office:house:EMPTY').name, '2009 ', 'a spelling with nothing after the year is shown as stored');
  assert.equal(chamberOf({id: 'container:office:house:A'}), 'House');
  assert.equal(chamberOf({id: 'organization:x'}), '');
  assert.equal(chamberOf(null), '');
});

test('the list row, the right-pane name, its caption and search all use the office names', () => {
  assert.match(source, /S\.names = officeNames\(payload\.offices\);/);
  assert.match(source, /'<span class="ppl-row__name"' \+ nameTitle\(g\) \+ '>' \+ esc\(labelOf\(g\)\.name\) \+ '<\/span>/);
  assert.doesNotMatch(source, /ppl-row__name">' \+ esc\(g\.name\)/);
  assert.match(source, /if \(kind === 'offices' && chamberOf\(g\)\) bits\.unshift\(chamberOf\(g\)\);/);
  assert.match(source, /nameEl\.textContent = label\.name;/);
  assert.match(source, /captionEl\.textContent = \[label\.chamber, plural\(total, 'person', 'people'\)/);
  assert.match(source, /S\.hay\[kind\] = groupsOf\(kind\)\.map\(g => \[groupText\(g\)\]\.concat\(/);
  assert.match(source, /const nameHit = g => S\.words\.every\(w => groupText\(g\)\.includes\(w\)\);/);
});

test('the Sort control is as wide as the chosen sort, so the right-pane name keeps its row (board #43)', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../surfaces/approved/clean/people-ui.css'), 'utf8');
  assert.ok(css.includes('.ppl-sort:not(#mlk)>select{field-sizing:content}'));
  assert.ok(css.includes('.ppl-sort:not(#mlk){height:32px;min-width:128px;font-size:14px}'), 'the board width stays the floor');
});
