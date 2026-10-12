// Exercise the shipped renderers without importing live data producers.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../surfaces/templates/make_member_desk_00.tpl'), 'utf8');

function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing renderer anchors: ${start}, ${end}`);
  return source.slice(a, b);
}

const renderers = section('function cookPviSourceHTML(){', 'const ROLEBADGE=');
const method = section('function methodHTML(m){', '/* One step quieter than section()');
const safeUrl = section('function safeUrl(u){', '\n');   // the shipped link guard, not a copy
const sourceUrl = 'https://www.cookpolitical.com/cook-pvi/2026-partisan-voting-index/district-map-and-list';
const metadata = {article_publication_date: '2026-07-29', retrieval_date: '2026-10-05', source_page_url: sourceUrl};
function harness(meta = metadata) {
  const esc = s => (s == null ? '' : String(s)).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
  const ctx = {D: {cook_pvi_meta: meta}, HONORS: null, esc,
    attr: s => esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
    radar: () => '', money: () => '—', n0: () => '—', pc: () => '—',
    f2: n => Number(n).toFixed(2)};
  vm.createContext(ctx);
  vm.runInContext(safeUrl + '\n' + renderers + '\n' + method, ctx);
  return ctx;
}
function member(overrides = {}) {
  return {chamber: 'House', rank: 12, roi: 0.5, alloc: 250, cook_pvi: {
    status: 'available', district_id: 'AK-00', district_label: 'AK-AL', pvi_label: 'R+6',
    signed_points: 6, match_basis: 'district_code_only', boundary_currency_status: 'UNVERIFIED-HERE',
    ...overrides,
  }};
}

test('all inline scripts remain valid JavaScript', () => {
  const scripts = [...source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
  assert.ok(scripts.length > 0);
  scripts.forEach(match => new vm.Script(match[1]));
});

test('District renderer carries source label, both dates and boundary limits', () => {
  const ctx = harness(), html = ctx.demoHTML(member());
  assert.match(html, /Cook PVI · 2026 source snapshot/);
  assert.match(html, />R\+6<\/div>/);
  assert.match(html, /AK-AL · rounded source label/);
  assert.match(html, /Historical presidential partisan lean relative to the nation; not an election forecast/);
  assert.match(html, /Historical district-code match only\. Current member and district boundaries are unverified/);
  assert.match(html, /Article published 2026-07-29; snapshot retrieved 2026-10-05/);
  assert.ok(html.includes(`href="${sourceUrl}" target=_blank rel=noopener`));
});

test('EVEN is preserved as source label, not replaced with missing or zero', () => {
  const html = harness().cookPviHTML(member({pvi_label: 'EVEN', signed_points: 0}));
  assert.match(html, />EVEN<\/div>/);
  assert.doesNotMatch(html, /Unavailable|>0<\/div>/);
});

test('Senate outside scope and unmatched districts have no fabricated partisan value', () => {
  const ctx = harness(), senate = member({status: 'outside_scope', pvi_label: undefined, signed_points: undefined});
  senate.chamber = 'Senate';
  const senateHtml = ctx.demoHTML(senate);
  assert.match(senateHtml, /Outside scope: this snapshot covers voting House districts, with no Senate or territorial values/);
  assert.doesNotMatch(ctx.cookPviHTML(senate), /class=v|EVEN|R\+0|D\+0/);
  const unmatched = ctx.cookPviHTML(member({status: 'unmatched', pvi_label: undefined}));
  assert.match(unmatched, /Unavailable: no district-code match/);
  assert.doesNotMatch(unmatched, /class=v/);
});

test('missing artifact and malformed available row show unavailable, without fake dates', () => {
  const ctx = harness({});
  for (const row of [member({status: 'unavailable'}), member({pvi_label: ''})]) {
    const html = ctx.cookPviHTML(row);
    assert.match(html, /Unavailable:/);
    assert.doesNotMatch(html, /class=v|2026-07-29|2026-10-05/);
    assert.match(html, /date unavailable/);
  }
  assert.equal(ctx.cookPviHTML({}), '');
});

test('source fields escape markup and reject executable links', () => {
  const ctx = harness({article_publication_date: '<img src=x>', retrieval_date: '<svg onload=x>', source_page_url: 'javascript:alert(1)'});
  const html = ctx.cookPviHTML(member({district_label: '<img onerror=x>', pvi_label: '<script>alert(1)</script>'}));
  assert.doesNotMatch(html, /<img|<svg|<script|javascript:|href=/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /&lt;img onerror=x&gt;/);
  assert.match(html, /&lt;svg onload=x&gt;/);
  const quoted = harness({...metadata, source_page_url: sourceUrl + '?x=" onmouseover="alert(1)'}).cookPviSourceHTML();
  assert.match(quoted, /\?x=&quot; onmouseover=&quot;alert\(1\)/);
  assert.doesNotMatch(quoted, /" onmouseover="/);
});

test('Sources and method explains join limits and preserves score inputs', () => {
  const ctx = harness(), row = member(), before = JSON.stringify(row);
  const html = ctx.methodHTML(row);
  ctx.demoHTML(row);
  assert.match(html, /Cook PVI · 2026 source snapshot/);
  assert.match(html, /A district-code match does not verify the current member or current district boundaries/);
  assert.match(html, /Source incumbent names are not used for matching/);
  assert.match(html, /does not enter scores, ranks, allocations, receptivity, ROI, or efficiency calculations/);
  assert.ok(html.includes(sourceUrl));
  assert.equal(JSON.stringify(row), before);
});

test('ranked and efficiency renderers do not consume Cook PVI', () => {
  const ranked = section('function renderRanked(){', '/* ======================= MEMBER VIEW');
  const efficiency = section('function renderEff(){', '/* ======================= VIEW SWITCH');
  assert.doesNotMatch(ranked + efficiency, /cook_pvi|cookPvi/i);
});
