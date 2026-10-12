// A report worksheet has no dataset and no industry, so the sheet the Studio
// creates for it must not carry those keys at all: an EMPTY industry is the
// shape POST /api/studio/views refuses (contracts/studio_api.json negative
// control 3), and the S4 review reproduced a 400 on every worksheet-mode save
// while createSheet() defaulted the key to ''. Run with the other suites:
//   node --test "app/tests/**/*.test.js"   (from C:\Users\pgoff\Mentat\mentat)
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const core = require(path.join(__dirname, '..', 'chart-studio-core.js'));

test('a fresh sheet carries no industry or dataset key on its source', () => {
  const sheet = core.createSheet('Sheet 1', '');
  assert.equal(Object.prototype.hasOwnProperty.call(sheet.source, 'industry'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sheet.source, 'dataset'), false);
  assert.deepEqual(sheet.sources, []);
});

test('migrating a fresh worksheet sheet adds no industry key either', () => {
  const migrated = core.migrateSheet(core.createSheet('Sheet 1', ''));
  assert.equal(Object.prototype.hasOwnProperty.call(migrated.source, 'industry'), false);
  assert.deepEqual(migrated.sources, []);
});

test('negative control: a source that names an empty industry keeps it, so the server can refuse it', () => {
  const sheet = core.createSheet('Sheet 1', '');
  sheet.source.industry = '';
  assert.equal(sheet.source.industry, '');
});
