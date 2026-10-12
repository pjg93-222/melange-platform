const assert = require('node:assert/strict');
const view = require('../app/coverage-view.js');
const browserContext = {window:{}};
require('node:vm').runInNewContext(require('node:fs').readFileSync(require.resolve('../app/coverage-view.js'),'utf8'),browserContext);
assert.equal(typeof browserContext.window.MentatCoverage?.html,'function');
assert.equal(typeof browserContext.window.MentatCoverage?.select,'function');
const rows = [
  {industry:'Fintech',cycle:2026,freshness:'stale',verified_data_through:'2026-06-30',latest_observed_transaction_date:'2026-09-01',lag_days:72,target_date:'2026-09-10',reason:'Old evidence'},
  {industry:'Fintech',cycle:2024,freshness:'current',verified_data_through:'2024-12-31',lag_days:0,target_date:'2024-12-31'},
  {industry:'Other',cycle:2026,freshness:'unverifiable',reason:'Missing receipt'},
];
assert.equal(view.select(rows,[' fintech '],[2026]).length,1);
assert.equal(view.select(rows,['New sector'],[2022,2026]).length,2);
assert.equal(view.select(rows,['New sector'],[2026])[0].freshness,'unverifiable');
assert.match(view.html(rows),/Stale coverage/);
assert.match(view.html(rows),/Current within policy/);
assert.match(view.html(rows),/Unverifiable coverage/);
assert.match(view.html(rows),/Observed only; does not establish coverage/);
assert.match(view.html(rows),/2026-06-30/);
assert.match(view.html([]),/Unverifiable/);
const unsafe = view.html([{...rows[0],industry:'<img src=x onerror=alert(1)>',reason:'<script>oops</script>',receipt:'<b>bad</b>'}]);
assert.doesNotMatch(unsafe,/<img|<script>|<b>bad/);
assert.match(unsafe,/&lt;img/);
assert.match(view.html([{...rows[0],freshness:'unverifiable'}]),/<td data-label="Verified FEC data-through">Unknown<\/td>/);
// Build 07: Loading's sector x cycle grid (board L4).
assert.equal(typeof browserContext.window.MentatCoverage?.grid,'function');
const gridRows = [...rows,
  {industry:'Credit Union',cycle:2022,freshness:'unverifiable',reason:'Missing receipt'},
  {industry:'Credit Unions',cycle:2022,freshness:'current',verified_data_through:'2022-12-31'}];
const model = view.matrix(gridRows, ['credit unions', 'Brand New']);
assert.deepEqual(model.map(line => line.sector), ['Credit Union','Credit Unions','Fintech','Other','Brand New'], 'one row per sector exactly as the data names it; a new roster sector is appended');
assert.deepEqual(model.map(line => line.roster), [false,true,false,false,true], 'the roster sector matches its saved row case-insensitively');
assert.deepEqual(model.find(line => line.sector === 'Fintech').cells.map(cell => cell.state), ['none','current','stale']);
assert.deepEqual(model.find(line => line.sector === 'Other').cells.map(cell => cell.state), ['none','none','unverifiable']);
assert.equal(view.matrix([{industry:'X',cycle:2024,freshness:'current'}]).at(0).cells[1].state, 'unverifiable', 'current without a verified date is not verified');
const fintech2026 = model.find(line => line.sector === 'Fintech').cells[2];
const grid = view.grid(model, fintech2026);
assert.match(grid, /Stale · to <time datetime="2026-06-30">Jun 30, 2026<\/time>/);
assert.match(grid, /Verified · to <time datetime="2024-12-31">Dec 31, 2024<\/time>/);
assert.match(grid, />Not collected</);
assert.match(grid, />Unverifiable</);
assert.equal((grid.match(/aria-pressed="true"/g) || []).length, 1, 'exactly one selected cell');
assert.match(grid, /data-sector="Fintech" data-cycle="2026" aria-pressed="true"/);
assert.match(grid, /<h3 class="coverage-detail-title">Fintech · 2025-26<\/h3>/);
assert.match(grid, /Latest observed transaction:<\/dt><dd><time datetime="2026-09-01">Sep 1, 2026<\/time>/);
assert.match(grid, /72 days behind Sep 10, 2026/);
assert.match(grid, /<small>This roster<\/small>/);
assert.match(view.detail(model.at(-1).cells[0]), /No saved collection evidence for this sector\/cycle\./);
assert.doesNotMatch(view.detail(model.at(-1).cells[0]), /Coverage evidence/, 'nothing saved, no evidence button');
assert.match(view.grid([], null), /Nothing is saved on this computer yet/);
assert.equal(view.cycleLabel(2026), '2025-26');
assert.equal(view.formatDate('2022-12-31'), 'Dec 31, 2022');
const unsafeGrid = view.grid(view.matrix([{industry:'<img src=x>',cycle:2026,freshness:'unverifiable',reason:'<script>x</script>'}]), null);
assert.doesNotMatch(unsafeGrid, /<img|<script>/);
console.log('Coverage view checks passed.');
