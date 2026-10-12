// The Lobbying card of Insights > Forecast giving (D-064, Paul): the in-house series (LDA expenses_total) and the fees outside firms report (LDA income_total) are shown side by
// side and are NEVER summed, because an in-house filer's expenses already include what it pays outside firms. The card model and its drawing come from the shipped
// giving-forecast.js (a vm slice of the real source, helpers copied line for line), fed a Telecom fixture: Melange's 2021-2025 annual rows and the 2026 partial year through Q2
// (values of sector_spend.csv) and the lobbying nowcast rows of the calculator.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(APP, 'giving-forecast.js'), 'utf8');
const lf = text => text.replace(/\r\n/g, '\n');
// A declaration as shipped: the marker's line, and (for a block function) everything up to its closing brace at two-space indent.
const declaration = marker => {
  const at = source.indexOf(marker);
  assert.ok(at >= 0, 'shipped declaration exists: ' + marker);
  const eol = source.indexOf('\n', at), head = source.slice(at, eol);
  if (!/\{\s*$/.test(head)) return head;
  const close = source.indexOf('\n  }\n', at);
  return source.slice(at, close + 4);
};
const between = (from, to) => {
  const a = source.indexOf(from), b = source.indexOf(to, a);
  assert.ok(a >= 0 && b > a, `shipped slice exists: ${from} .. ${to}`);
  return source.slice(a, b);
};
const stripComments = code => code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\s\/\/ .*$/gm, '');

// ---- a small DOM: enough for svg(), node(), flip(), cssVar() and drawLobby ------------------------------------------------------------------------------------------------
function element(tag) {
  const classes = new Set();
  const e = {tag, attrs: {}, children: [], dataset: {}, style: {setProperty() {}}, className: '', textContent: '', clientWidth: 900, clientHeight: 400,
    classList: {toggle(name, on) { (on === undefined ? !classes.has(name) : on) ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name)},
    append(...c) { e.children.push(...c); }, replaceChildren(...c) { e.children = c; }, setAttribute(name, value) { e.attrs[name] = String(value); }};
  return e;
}
const walk = (e, out = []) => { if (e && typeof e === 'object') { out.push(e); (e.children || []).forEach(c => walk(c, out)); } return out; };
const klass = e => (e.attrs && e.attrs.class) || e.className || '';
const hasClass = (e, name) => klass(e).split(/\s+/).includes(name);
const textOf = e => typeof e === 'string' ? e : (e.textContent || '') + (e.children || []).map(textOf).join('');

function load() {
  const ids = {};
  const document = {getElementById: id => ids[id] || (ids[id] = element('div')), createElement: element, createElementNS: (ns, tag) => element(tag)};
  const script = [
    "'use strict';",
    declaration('  const money = '), declaration('  const number = '), declaration('  const signed = '), declaration('  const percent = '),
    "  const SVGNS = 'http://www.w3.org/2000/svg';", "  const $ = id => document.getElementById(id);", declaration("  const WARN = 'warn'"),
    declaration('  const pct = '), declaration('  const range = '), declaration('  const readyRun = '), declaration('  const H = text =>'),
    declaration('  function node('), declaration('  function svg('), declaration('  function cssVar('), declaration('  function flip('),
    between('  const axisTops = {};', '  function axisLabel('),
    'const remainderText = m => m.remainder_reason; const waiverChips = () => []; let catalog = null;',
    between('  const LD_SEGMENTS', '  function footerFacts('),
    'globalThis.__t = {lobbyModel, drawLobby, ldMoney, setCatalog: c => { catalog = c; }};'
  ].join('\n');
  const ctx = vm.createContext({document, Intl, Number, Math, String, Object, Array, JSON, Map, Set});
  vm.runInContext(script, ctx);
  return {t: ctx.__t, ids};
}

// ---- the Telecom fixture --------------------------------------------------------------------------------------------------------------------------------------------------
// Melange's rows (sector_spend.csv, telecom): hired_firms = income_total (fees reported by outside firms), in_house = expenses_total.
const HISTORY = {
  sector: 'telecom', source_file: 'sector_spend.csv', through: '2026-06-30',
  annual: [
    {year: 2021, hired_firms: 35530250, in_house: 95333890, orgs: 25, lobbyists: 537},
    {year: 2022, hired_firms: 37020000, in_house: 98212019, orgs: 23, lobbyists: 554},
    {year: 2023, hired_firms: 36448000, in_house: 100554138, orgs: 24, lobbyists: 533},
    {year: 2024, hired_firms: 36967000, in_house: 101098642, orgs: 23, lobbyists: 525},
    {year: 2025, hired_firms: 37000500, in_house: 102385884.55, orgs: 24, lobbyists: 532}],
  partial: {year: 2026, through_quarter: 'Q2', hired_firms: 17588500, in_house: 52066170, orgs: 23, lobbyists: 479}
};
const NOWCAST = {
  income_total: {label: 'Outside-firm fees', base: 37000500, point: 36127286.85245313, observed: 17588500, share: 0.4989584775218408},
  expenses_total: {label: 'In-house expenses', base: 102385884.55, point: 103991096.56684443, observed: 52066170, share: 0.4928507304003711}
};
const HALF = {2026: [0.14, 0.27], 2027: [0.19, 0.33], 2028: [0.25, 0.38]};          // the half-widths of the 80% and ~95% bands on the log scale, as the calculator reports them
function projectedRow(year) {
  const [h80, h95] = HALF[year], ahead = year - 2026;
  const series = Object.entries(NOWCAST).map(([id, n]) => {
    const point = n.point, s = {id, label: n.label, base_level: n.base, projected: point, change_pct: 100 * (point / n.base - 1), years_after_nowcast: ahead, half_width_80: h80, half_width_95: h95,
      band80_low: point * Math.exp(-h80), band80_high: point * Math.exp(h80), band95_low: point * Math.exp(-h95), band95_high: point * Math.exp(h95),
      nowcast: {year: 2026, through_quarter: 'Q2', observed: n.observed, prior_total: n.base, share: n.share, point}};
    if (year === 2026) Object.assign(s, {observed: {through: '2026-06-30', through_quarter: 'Q2', amount_nominal: n.observed, face_value: true}, remainder_starts: '2026-07-01',
      projected_remaining: point - n.observed, remaining_band80_low: (point - n.observed) * 0.9, remaining_band80_high: (point - n.observed) * 1.1, remainder_status: 'ready'});
    return s;
  });
  return {year, status: 'ready', chained: ahead > 0, steps: ahead || 1, series};
}
const lobbying = rows => ({status: 'ready', base_year: 2025, sector_label: 'Melange LDA telecom sector', source_file: 'sector_spend.csv', note: 'Nominal dollars.',
  bands: {label_80: '80% band: the 80th percentile of the blind nowcast errors (n=10 blind tests)', label_95: '~95% band: the largest blind miss (n=10 blind tests)'}, rows});
const RUN = {status: 'ready', industry: 'telecom', as_of: '2026-09-30', horizons: [{lobbying: lobbying([projectedRow(2026)])}, {lobbying: lobbying([projectedRow(2027), projectedRow(2028)])}]};
const clone = value => JSON.parse(JSON.stringify(value));
const session = () => {
  const s = load();
  s.t.setCatalog({anchors: {status: 'ready', industries: {telecom: {status: 'ready', lda_history: clone(HISTORY)}}}});
  return s;
};
const plain = clone;

// Every number anywhere in a value, with the path it sits at.
function numbers(value, at = '', out = []) {
  if (typeof value === 'number') out.push([at, value]);
  else if (value && typeof value === 'object') for (const [k, v] of Object.entries(value)) numbers(v, at + '/' + k, out);
  return out;
}
// The forbidden sums: for every year of the history and every projected year, the two series added, and the two projected band ends added.
const sumsOfTheTwoSeries = () => {
  const sums = HISTORY.annual.concat([HISTORY.partial]).map(r => r.hired_firms + r.in_house);
  for (const horizon of RUN.horizons) for (const row of horizon.lobbying.rows) {
    const [fees, house] = [row.series.find(s => s.id === 'income_total'), row.series.find(s => s.id === 'expenses_total')];
    for (const k of ['projected', 'band80_low', 'band80_high', 'band95_low', 'band95_high']) sums.push(fees[k] + house[k]);
  }
  return sums;
};

test('the card shows the in-house and outside-firm series side by side: no bar, figure, whisker or sentence carries their sum', () => {
  const {t} = session(), model = t.lobbyModel(RUN, 2026, 2028);
  assert.equal(model.state, 'ready');
  const forbidden = sumsOfTheTwoSeries();
  assert.ok(forbidden.includes(HISTORY.annual[4].hired_firms + HISTORY.annual[4].in_house), 'the 2025 sum is among the forbidden numbers');
  const found = numbers(plain(model)).filter(([, v]) => forbidden.some(sum => Math.abs(v - sum) < 0.005));
  assert.deepEqual(found, [], 'no number in the model equals the two series added');
  const json = JSON.stringify(plain(model));
  assert.ok(!json.includes('139.4M'), '"$139.4M" (2025 in-house + outside-firm fees) appears nowhere in the model');
  for (const bar of model.bars) { assert.ok(!('total' in bar), bar.label + ': no total'); assert.ok(!('whisker' in bar), bar.label + ': no combined whisker'); }
  assert.ok(!/total/i.test(model.aria), 'the spoken description never says total');
});

test('the headline is two figures for the last complete year, the partial year a secondary line, the counts those of the headline year', () => {
  const {t} = session(), model = t.lobbyModel(RUN, 2026, 2028);
  // The headline figures are always one decimal of millions; ldMoney (Melange's own money(), a trailing ".0" dropped) stays on the axis, the bar labels and the tooltips.
  assert.deepEqual(plain(model.figs), [{value: '$102.4M', label: '2025 in-house lobbying'}, {value: '$37.0M', label: '2025 fees reported by outside firms'}]);
  assert.equal(t.ldMoney(37000500), '$37M');
  assert.equal(model.partialLine, '2026 through Q2: in-house $52.1M · outside-firm fees $17.6M');
  assert.equal(model.subtitle, '2025: 24 organizations · 532 named lobbyists');
  assert.ok(!model.subtitle.includes('479') && !model.subtitle.includes('23 organizations'), 'not the partial year\'s counts beside the 2025 dollars');
  // only a partial year on file: the counts say which quarter they run through; counts that are missing leave the line out
  const only = clone(HISTORY); only.annual = [];
  const s = load(); s.t.setCatalog({anchors: {industries: {telecom: {lda_history: only}}}});
  const partialOnly = s.t.lobbyModel(RUN, 2026, 2028);
  assert.equal(partialOnly.subtitle, '2026 through Q2: 23 organizations · 479 named lobbyists');
  assert.deepEqual(plain(partialOnly.figs), [{value: '—', label: 'No complete year on file'}]);
  const bare = clone(HISTORY); for (const r of [...bare.annual, bare.partial]) { r.orgs = null; r.lobbyists = null; }
  const b = load(); b.t.setCatalog({anchors: {industries: {telecom: {lda_history: bare}}}});
  assert.equal(b.t.lobbyModel(RUN, 2026, 2028).subtitle, '', 'no counts, no subtitle');
});

test('bars: two values per year, the history exactly Melange\'s rows, every projected bar with the whisker of its OWN 80% band', () => {
  const {t} = session(), model = t.lobbyModel(RUN, 2026, 2028), bars = plain(model.bars);
  assert.deepEqual(bars.map(b => b.label), ['2021', '2022', '2023', '2024', '2025', '2026 partial', '2026 projected', '2027 projected', '2028 projected']);
  assert.deepEqual(bars.map(b => b.kind), ['annual', 'annual', 'annual', 'annual', 'annual', 'partial', 'projected', 'projected', 'projected']);
  HISTORY.annual.forEach((r, i) => assert.deepEqual([bars[i].inhouse, bars[i].hired], [r.in_house, r.hired_firms], String(r.year)));
  assert.deepEqual([bars[5].inhouse, bars[5].hired], [52066170, 17588500]);
  assert.equal(bars[4].tips.inhouse, '2025 · In-house · $102.4M');
  assert.equal(bars[4].tips.hired, '2025 · Fees reported by outside firms · $37M');
  for (const bar of bars.slice(0, 6)) assert.equal(bar.whiskers, undefined, bar.label + ': observed levels have no band');
  [2026, 2027, 2028].forEach((year, i) => {
    const row = RUN.horizons[year === 2026 ? 0 : 1].lobbying.rows.find(r => r.year === year), bar = bars[6 + i];
    for (const [id, key, name] of [['expenses_total', 'inhouse', 'In-house'], ['income_total', 'hired', 'Fees reported by outside firms']]) {
      const s = row.series.find(item => item.id === id), w = bar.whiskers[key];
      assert.equal(bar[key], s.projected, `${year} ${key} value`);
      assert.deepEqual([w.low, w.high], [s.band80_low, s.band80_high], `${year} ${key}: the series' own 80% band`);
      assert.ok(w.low < bar[key] && bar[key] < w.high);
      assert.match(bar.tips[key], new RegExp(`^${year} projected · ${name}\\n`));
      assert.match(bar.tips[key], /\n80% band: \$/);
      assert.match(w.tip, new RegExp(`^${year} projected · ${name} · 80% band\\n`));
      assert.match(w.tip, /\n80% band: \$/);
      assert.match(w.tip, /The 80% band of this series alone\. The other series has its own, and the two are never merged\./);
    }
    assert.deepEqual(Object.keys(bar.whiskers).sort(), ['hired', 'inhouse'], year + ': one whisker per series and no third, combined one');
  });
  assert.match(bars[6].tips.hired, /Nowcast, Q2 cut: \$17\.59M filed \+ 50\.1% of 2025/);
  assert.match(bars[7].tips.hired, /Carried 1 year past the nowcast/);
  // the axis: the largest single value or whisker top, never a sum
  const tops = bars.flatMap(b => [b.inhouse, b.hired, ...Object.values(b.whiskers || {}).map(w => w.high)]);
  assert.equal(model.axisMax, Math.max(...tops));
  assert.ok(model.axisMax < HISTORY.annual[4].in_house + HISTORY.annual[4].hired_firms);
  const high2028 = RUN.horizons[1].lobbying.rows[1].series.find(s => s.id === 'expenses_total').band80_high;
  assert.equal(model.axisMax, high2028, 'the tallest thing is the 2028 in-house whisker top');
});

test('drawn: two side-by-side bars on one baseline per year, a label and a tooltip each, whiskers only on projected bars, one per series', () => {
  const {t, ids} = session(), model = t.lobbyModel(RUN, 2026, 2028), host = element('div');
  t.drawLobby(model, host, false);
  const all = walk(host), groups = all.filter(e => hasClass(e, 'gf-ld-bar'));
  assert.equal(groups.length, 9);
  for (const g of groups) {
    const rects = g.children.filter(e => e.tag === 'rect' && hasClass(e, 'gf-ld-seg')), bar = model.bars[groups.indexOf(g)];
    assert.equal(rects.length, 2, bar.label + ': two bars');
    const [house, fees] = rects;
    assert.equal(textOf(house.children.find(c => c.tag === 'title')), bar.tips.inhouse, bar.label + ': in-house tooltip');
    assert.equal(textOf(fees.children.find(c => c.tag === 'title')), bar.tips.hired, bar.label + ': outside-firm tooltip');
    assert.ok(Number(house.attrs.x) + Number(house.attrs.width) <= Number(fees.attrs.x) + 1e-6, bar.label + ': side by side, not stacked');
    const feet = r => Number(r.attrs.y) + Number(r.attrs.height);
    assert.ok(Math.abs(feet(house) - feet(fees)) < 0.011, bar.label + ': one baseline');
    assert.ok(Math.abs(Number(house.attrs.height) / Number(fees.attrs.height) - bar.inhouse / bar.hired) < 0.02, bar.label + ': heights follow the two values');
    const labels = g.children.filter(e => e.tag === 'text' && /^gf-ld-lab/.test(klass(e)));
    assert.deepEqual(labels.map(textOf), [t.ldMoney(bar.inhouse), t.ldMoney(bar.hired)], bar.label + ': a value label on each bar, in-house first');
    const whiskers = g.children.filter(e => e.tag === 'path' && hasClass(e, 'gf-ld-whisker')), hits = g.children.filter(e => hasClass(e, 'gf-ld-whisker-hit'));
    assert.equal(whiskers.length, bar.kind === 'projected' ? 2 : 0, bar.label + ': whiskers');
    if (bar.kind === 'projected') {
      assert.deepEqual(hits.map(h => textOf(h.children[0])), [bar.whiskers.inhouse.tip, bar.whiskers.hired.tip], bar.label + ': each whisker explains its own band');
      const x = d => Number(/^M([\d.]+) /.exec(d)[1]);
      assert.ok(x(whiskers[0].attrs.d) < x(whiskers[1].attrs.d), bar.label + ': a whisker on each bar, in-house first');
    }
  }
  // a projected bar is dashed, the partial year hatched
  assert.ok(groups[8].children.filter(e => e.tag === 'rect' && hasClass(e, 'gf-ld-seg')).every(e => hasClass(e, 'gf-ld-proj')));
  assert.ok(groups[5].children.filter(e => e.tag === 'rect' && hasClass(e, 'gf-ld-seg')).every(e => /^url\(#gf-ld-hatch-\d+[hi]\)$/.test(e.attrs.fill)));
  // the headline and the secondary line
  const figs = all.filter(e => hasClass(e, 'gf-ld-fig')).map(f => f.children.map(textOf));
  assert.deepEqual(figs, [['$102.4M', '2025 in-house lobbying'], ['$37.0M', '2025 fees reported by outside firms']]);
  assert.deepEqual(all.filter(e => hasClass(e, 'gf-ld-partial')).map(textOf), ['2026 through Q2: in-house $52.1M · outside-firm fees $17.6M']);
  assert.deepEqual(all.filter(e => hasClass(e, 'gf-ld-sub')).map(textOf), ['2025: 24 organizations · 532 named lobbyists']);
  assert.equal(ids['gf-legend-lobby'].dataset.sector, 'telecom');
});

test('no code in the card adds the two series (comments stripped)', () => {
  const code = stripComments(between('  const LD_SEGMENTS', '  function footerFacts('));
  assert.ok(code.includes('function lobbyModel') && code.includes('function drawLobby'));
  assert.doesNotMatch(code, /hired_firms\s*\+|\+\s*[\w.]*in_house|\.hired\s*\+|\+\s*[\w.]*\.inhouse|\btotal\b/);
});

test('the legend names the two series and the dashed projection, in the page and in the template it is rendered from', () => {
  const legend = file => {
    const html = lf(fs.readFileSync(path.join(APP, file), 'utf8')), at = html.indexOf('<ul id="gf-legend-lobby"');
    assert.ok(at >= 0, file + ' has the lobbying legend');
    return html.slice(at, html.indexOf('</ul>', at) + 5);
  };
  const page = legend('index.html'), template = legend('templates/view_insights.html');
  assert.equal(page, template, 'index.html and the template mirror each other');
  assert.deepEqual([...page.matchAll(/<li[^>]*>(?:<i[^>]*><\/i>)?([^<]+)<\/li>/g)].map(m => m[1]), ['In-house', 'Fees reported by outside firms', 'Projected (dashed)']);
  assert.match(page, /aria-label="Lobbying series: in-house, fees reported by outside firms and the projection, each with its own 80% band"/);
});

test('the presidential-cycle note sets the industry\'s own ratio beside the owner rule, from its audited workbooks, and says it is display only', () => {
  const at = source.indexOf('  const ownCycleText = ');
  assert.ok(at >= 0, 'the shipped note builder exists');
  const ownCycleText = vm.runInNewContext(source.slice(at, source.indexOf('\n', at)) + '\nownCycleText', {Math, Number, String});
  const text = ownCycleText({dataset: 'Telecom', presidential_cycle: '2023-2024', presidential_total: 17978153.890000038, midterm_cycle: '2021-2022', midterm_total: 12463873.750000043});
  assert.equal(text, 'Telecom\'s own giving: 2023-24 $17,978,154 ÷ 2021-22 $12,463,874 = ×1.44 (audited Mentat workbooks; display only, the forecast uses the owner rule)');
  assert.ok(text.includes('×1.44'));
});
