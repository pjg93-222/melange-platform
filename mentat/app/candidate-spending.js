/* How a candidate's campaign spent its money: the hover card and the docked panel of the Flow views.
 *
 * The numbers are the campaign's own (FEC Schedule B, GET /api/candidate-spending, app/candidate_spending.py), never
 * this flow's dollars: every card says so. This module is loaded by flow-chart.js the first time a candidate is
 * hovered (like evidence-map.js), so a console that never draws a Flow pays nothing for it.
 *
 * Like flow-chart.js it is pure where it can be: ring() is the donut's arithmetic (shares that sum to 1, segments that
 * span 360 degrees), donutSvg() / cardHtml() / panelHtml() return markup, so node:test reads exactly what the page
 * draws. client() is one read per candidate and cycle, kept in memory, one request in flight per key. controller()
 * is the only part that touches the DOM: the tooltip's spending section (debounced 150 ms) and the right-hand panel.
 *
 * CSP (style-src 'self'): no style attributes and no <style>; presentation attributes and classes only.
 * Colours come from MentatTheme.categorical (flow-chart.js's palette), one fixed slot per category so a category keeps
 * its colour from campaign to campaign.
 */
(function (root) {
  'use strict';

  const TOP = 7;                       // the hover card names this many categories; the rest are Other
  const LOW_COVERAGE = 0.9;            // below this, the card says so and draws what was not itemized
  const PEERS_MIN = 5;
  const DEBOUNCE_MS = 150;
  const NEUTRAL = '#a39b92';
  const SAND = '#c9b79c';
  const FALLBACK = ['#327a7a', '#57447b', '#8b6984', '#eca91c', '#de8aab', '#7d4b00', '#3cb0a3', '#83333e', '#99973f',
    '#a09eea', '#d88766', '#509ecd'];
  // Server order (candidate_spending.CATEGORIES) and one palette slot each; Other is the flow's own neutral grey.
  const CATEGORIES = [['advertising', 'Advertising & media', 1], ['consulting', 'Consulting', 3], ['legal_compliance', 'Legal & compliance', 7],
    ['polling_data', 'Polling & data', 9], ['payroll', 'Payroll & staff', 0], ['fundraising_fees', 'Fundraising & processing fees', 4],
    ['events', 'Events & meetings', 11], ['travel', 'Travel', 8], ['printing_mail', 'Printing & mail', 5], ['office', 'Office & admin', 10],
    ['contributions_transfers', 'Contributions & transfers', 2], ['refunds', 'Refunds to donors', 6],
    ['personal_reimb', 'Candidate reimbursements', -1], ['other', 'Other', -2]];
  const LABELS = Object.fromEntries(CATEGORIES.map(item => [item[0], item[1]]));
  const SLOT = Object.fromEntries(CATEGORIES.map(item => [item[0], item[2]]));
  const NOTE = "Categories assigned from each payment's stated purpose · Refunds and transfers to other committees are "
    + "their own slices · The campaign's own spending, not a trace of this flow's dollars";

  const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, character =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
  const f = number => String(Math.round(number * 100) / 100);
  const grouped = number => Math.round(number).toLocaleString('en-US');

  // $1.05M, $558K, $434 (the same rule flow-chart.js short() prints, tenths of a million rounded half up)
  function money(value) {
    const size = Math.abs(value), sign = value < 0 ? '-' : '';
    if (size >= 999950) return `${sign}$${(Math.round(size / 1e5) / 10).toFixed(1)}M`;
    if (size >= 1000) return `${sign}$${grouped(size / 1000)}K`;
    return `${sign}$${grouped(size)}`;
  }
  const whole = value => (value < 0 ? '-' : '') + '$' + grouped(Math.abs(value));
  const cents = value => (value < 0 ? '-' : '') + '$' + Math.abs(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const percent = share => (share > 0 && share < 0.005 ? '<1%' : `${Math.round(share * 100)}%`);
  const cycleText = cycle => (cycle ? `${Number(cycle) - 1}–${String(cycle).slice(2)}` : '');
  const plural = (count, word) => `${grouped(count)} ${word}${count === 1 ? '' : 's'}`;

  // "N%" for coverage, always rounded DOWN (the server floors it too): 89.6% reads 89%, 99.996% reads 99%, so the "under 90%"
  // chip can never say 90% and nothing short of all reads as all. The 1e-9 is float dust (0.29 * 100 is 28.999999999999996).
  // Above 100% (a scope bug, shown as it is) a hundred-and-a-bit keeps its decimals, so it never reads as exactly 100%: 100.04%.
  function coverageText(coverage) {
    const value = coverage * 100 + 1e-9;
    if (value >= 100 && value < 101) return `${(Math.floor(value * 100) / 100).toFixed(2).replace(/\.?0+$/, '')}%`;
    return `${Math.floor(value)}%`;
  }
  const exceeds = body => body.coverage != null && body.coverage > 1;

  function colourOf(key, palette, neutral) {
    const slot = SLOT[key];
    if (slot === -1) return SAND;
    if (slot === undefined || slot === -2) return neutral || NEUTRAL;
    return (palette || FALLBACK)[slot] || FALLBACK[slot];
  }

  /* ---------------------------------------------------------------- the donut's arithmetic */
  // The ring's items: the top `top` named categories (default 7) and the rest folded into Other. With low
  // coverage the ring is drawn over the reported total and what was not itemized is its last segment.
  function ring(body, options) {
    options = options || {};
    const top = options.top == null ? TOP : options.top;
    const palette = options.palette || FALLBACK, neutral = options.neutral || NEUTRAL;
    const categories = (body.categories || []).filter(item => item.amount > 0);
    const named = categories.filter(item => item.key !== 'other');
    const own = categories.find(item => item.key === 'other');
    const kept = named.slice(0, top), folded = named.slice(top);
    const rest = folded.reduce((sum, item) => sum + item.amount, 0) + (own ? own.amount : 0);
    const items = kept.map(item => ({key: item.key, label: item.label || LABELS[item.key] || item.key, amount: item.amount,
      payments: item.payments || 0}));
    if (rest > 0) items.push({key: 'other', label: 'Other', amount: Math.round(rest * 100) / 100, folded: folded.length,
      payments: folded.reduce((sum, item) => sum + (item.payments || 0), 0) + (own ? own.payments || 0 : 0)});
    const drawn = items.reduce((sum, item) => sum + item.amount, 0);
    const reported = Number(body.reported_total) || 0;
    const partial = body.coverage != null && body.coverage < LOW_COVERAGE && reported > drawn;
    const base = partial ? reported : drawn;
    const model = {items, partial, base, drawn, coverage: body.coverage, remainder: null,
      centre: {amount: base, words: partial ? 'reported' : 'spent'}};
    if (!base) return Object.assign(model, {segments: [], base: 0});
    let at = 0;
    const segments = items.map(item => {
      item.share = item.amount / base;
      item.colour = colourOf(item.key, palette, neutral);
      const start = at;
      at += item.share * 2 * Math.PI;
      return Object.assign(item, {start, end: at});
    });
    if (partial) {
      const left = Math.round((base - drawn) * 100) / 100;
      model.remainder = {key: 'not_itemized', label: 'Not itemized', amount: left, share: left / base, start: at, end: 2 * Math.PI};
      segments.push(model.remainder);
    } else if (segments.length) segments[segments.length - 1].end = 2 * Math.PI;      // no float dust: it closes on its first
    model.segments = segments;
    return model;
  }

  // The annular sector between angles a0 and a1 (radians clockwise from twelve o'clock).
  function arcPath(cx, cy, outer, inner, a0, a1) {
    const point = (angle, radius) => `${f(cx + radius * Math.sin(angle))} ${f(cy - radius * Math.cos(angle))}`;
    if (a1 - a0 >= 2 * Math.PI - 1e-9) {                    // a whole ring: two half arcs each way (one arc cannot close)
      const circle = radius => `M${point(0, radius)}A${f(radius)} ${f(radius)} 0 1 1 ${point(Math.PI, radius)}A${f(radius)} ${f(radius)} 0 1 1 ${point(0, radius)}Z`;
      return circle(outer) + circle(inner);
    }
    if (a1 - a0 > Math.PI) {                                // past half a turn: two arcs, never one. A single arc whose ends are
      const mid = (a0 + a1) / 2;                           // (nearly) the same point draws nothing, and a slice of 99.9999% is that.
      return `M${point(a0, outer)}A${f(outer)} ${f(outer)} 0 0 1 ${point(mid, outer)}A${f(outer)} ${f(outer)} 0 0 1 ${point(a1, outer)}`
        + `L${point(a1, inner)}A${f(inner)} ${f(inner)} 0 0 0 ${point(mid, inner)}A${f(inner)} ${f(inner)} 0 0 0 ${point(a0, inner)}Z`;
    }
    return `M${point(a0, outer)}A${f(outer)} ${f(outer)} 0 0 1 ${point(a1, outer)}L${point(a1, inner)}`
      + `A${f(inner)} ${f(inner)} 0 0 0 ${point(a0, inner)}Z`;
  }

  // The words a screen reader gets for the ring: the three largest categories.
  function summary(body, model, cycle) {
    const top3 = model.items.filter(item => item.key !== 'other' || model.items.length === 1).slice(0, 3)
      .map(item => `${item.label} ${percent(item.share)}`).join(', ');
    const name = body.committee_name ? `${body.committee_name}, ` : '';
    return `How the campaign spent its money, ${name}${cycleText(cycle || body.cycle)}. Largest: ${top3 || 'nothing itemized'}.`
      + (model.partial ? ` Itemized payments cover ${coverageText(body.coverage)} of reported spending; the rest is not itemized.` : '');
  }

  const serial = {n: 0};
const uid = prefix => `${prefix}${++serial.n}`;
  // The ring as SVG. size: its width and height in px. interactive: segments are buttons (the panel).
  function donutSvg(body, model, options) {
    options = options || {};
    const size = options.size || 124, prefix = uid('spd');
    const cx = size / 2, cy = size / 2, outer = size / 2 - (options.interactive ? 7 : 3), inner = outer * 0.6;
    const centre = model.centre.amount ? `<text class="flow-spend-total" x="${f(cx)}" y="${f(cy + 2)}" text-anchor="middle">${escape(money(model.centre.amount))}</text>`
      + `<text class="flow-spend-unit" x="${f(cx)}" y="${f(cy + 16)}" text-anchor="middle">${model.centre.words}</text>` : '';
    const parts = [`<svg class="flow-spend-donut${options.interactive ? ' is-big' : ''}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}"`
      + ` width="${size}" height="${size}" role="img" aria-label="${escape(summary(body, model, options.cycle))}">`];
    parts.push(`<defs><pattern id="${prefix}-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
      + `<rect width="6" height="6" fill="#f1eee9"/><line x1="0" y1="0" x2="0" y2="6" stroke="${NEUTRAL}" stroke-width="2"/></pattern></defs>`);
    for (const segment of model.segments) {
      const hatch = segment.key === 'not_itemized';
      const middle = (segment.start + segment.end) / 2;
      const d = arcPath(cx, cy, outer, inner, segment.start, segment.end);
      const fill = hatch ? `url(#${prefix}-hatch)` : segment.colour;
      const label = `${segment.label} ${percent(segment.share)}, ${money(segment.amount)}${segment.payments != null && !hatch ? `, ${plural(segment.payments, 'payment')}` : ''}`;
      const extra = options.interactive
        ? ` data-cat="${escape(segment.key)}" data-pull="${f(5 * Math.sin(middle))} ${f(-5 * Math.cos(middle))}" tabindex="${hatch ? -1 : 0}" role="${hatch ? 'img' : 'button'}" aria-label="${escape(label)}"` : '';
      parts.push(`<path class="flow-spend-seg${hatch ? ' is-hatched' : ''}${options.selected === segment.key ? ' is-selected' : ''}"${extra} d="${d}" fill="${fill}" fill-rule="evenodd" stroke="#fff" stroke-width="1.4"><title>${escape(label)}</title></path>`);
    }
    parts.push(centre, '</svg>');
    return parts.join('');
  }

  // A swatch that needs no style attribute: an SVG dot, hatched for what was not itemized.
  function dot(segment) {
    const prefix = uid('spk');
    return segment.key === 'not_itemized'
      ? `<svg class="flow-spend-dot" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><defs><pattern id="${prefix}" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="4" fill="#f1eee9"/><line x1="0" y1="0" x2="0" y2="4" stroke="${NEUTRAL}" stroke-width="1.6"/></pattern></defs><circle cx="6" cy="6" r="6" fill="url(#${prefix})" stroke="${NEUTRAL}" stroke-width="0.8"/></svg>`
      : `<svg class="flow-spend-dot" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="6" fill="${segment.colour}"/></svg>`;
  }

  // The chip: itemized payments cover under 90% of what was reported, the source says the campaign is only partly
  // collected, or the reported total is unknown (no totals: coverage cannot be said at all). Its title says why.
  const needsChip = body => body.coverage == null || body.coverage < LOW_COVERAGE || exceeds(body) || body.coverage_status === 'partial';
  const chipWords = body => (body.coverage == null ? 'Coverage unknown — itemized sample only'
    : exceeds(body) ? `Itemized exceeds reported (${coverageText(body.coverage)}) — check scope`
      : `Itemized payments cover ${coverageText(body.coverage)} of reported spending`);
  // the line under a card / panel that says how much of what was reported the itemized payments are
  const coversWords = body => (body.coverage == null ? 'Itemized FEC Schedule B · coverage unknown'
    : exceeds(body) ? `Itemized FEC Schedule B · exceeds reported spending (${coverageText(body.coverage)})`
      : `Itemized FEC Schedule B · covers ${coverageText(body.coverage)} of reported spending`);

  // What the answer left out or merged, as small lines under the legend: the card gets the short form, the panel the full.
  // Everything comes from the server's own counts; a line appears only when there is something to say.
  function footnotes(body, short) {
    const out = [], skipped = [];
    const left = body.excluded || {};
    for (const [key, long, brief] of [['loan_repayments', 'loan repayments', 'loans'],
      ['transfers_to_authorized', "transfers to the candidate's other committees", 'transfers']]) {
      const item = left[key] || {};
      if (!(item.itemized > 0 || item.reported > 0)) continue;
      const amount = item.itemized > 0 ? item.itemized : item.reported;
      skipped.push(short ? `${brief} ${money(amount)}`
        : `${long} ${money(amount)}${item.reported != null && item.itemized > 0 && Math.abs(item.reported - item.itemized) >= 0.5 ? ` (${money(item.reported)} reported)` : ''}`);
    }
    if (skipped.length) out.push(short ? `Left out: ${skipped.join(', ')}` : `Left out of the ring and of the reported total: ${skipped.join('; ')}.`);
    const own = body.own_transfers || {};
    if (own.rows > 0 || Math.abs(own.amount || 0) >= 0.5) out.push(short ? `${money(own.amount || 0)} between own committees counted once`
      : `Payments between the candidate's own committees (${money(own.amount || 0)}, ${plural(own.rows || 0, 'payment')}) are left out so each dollar is counted once.`);
    const negative = body.dropped_negative || [];
    if (negative.length) out.push(short ? `${negative.map(item => item.label).join(', ')} not drawn (nets to $0 or less)`
      : negative.map(item => `${item.label} is not drawn: its refunds and voids net to ${money(item.amount)}.`).join(' '));
    const repeated = (body.source && body.source.duplicate_rows_dropped) || 0;
    if (repeated > 0) out.push(short ? `${plural(repeated, 'repeated row')} dropped`
      : `${plural(repeated, 'repeated filing row')} in the source ${repeated === 1 ? 'was' : 'were'} counted once.`);
    return out;
  }
  const chip = body => `<p class="flow-spend-chip" role="note"${(body.coverage_reasons || []).length ? ` title="${escape(body.coverage_reasons.join('; '))}"` : ''}>`
    + `<span aria-hidden="true">&#9888;</span> ${escape(chipWords(body))}</p>`;

  /* ---------------------------------------------------------------- the hover card */
  // state: {kind: 'loading' | 'ok' | 'missing' | 'error', body, message}; ctx: {name, cycle, palette, neutral}
  function cardHtml(state, ctx) {
    const sub = `How the campaign spent its money · ${cycleText(ctx.cycle)}`;
    const body = state.body;
    const head = kind => `<div class="flow-spend-head"><strong class="flow-spend-name">${escape(kind === 'ok' && body.committee_name ? body.committee_name : ctx.name)}</strong>`
      + `<span class="flow-spend-sub">${escape(sub)}</span></div>`;
    if (state.kind === 'loading') {
      const ghost = `<svg class="flow-spend-donut is-ghost" viewBox="0 0 124 124" width="124" height="124" aria-hidden="true"><circle cx="62" cy="62" r="45" fill="none" stroke="#e4dfe8" stroke-width="22"/>`
        + `<circle cx="62" cy="62" r="45" fill="none" stroke="#f4f1f6" stroke-width="22" stroke-dasharray="40 243" stroke-dashoffset="-20"/></svg>`;
      return `<div class="flow-spend" data-state="loading">${head('loading')}<div class="flow-spend-body">${ghost}`
        + '<div class="flow-spend-ghost"><p class="flow-spend-say">Reading FEC payments…</p><span class="flow-spend-bar"></span><span class="flow-spend-bar"></span><span class="flow-spend-bar"></span></div></div></div>';
    }
    if (state.kind === 'missing' || state.kind === 'error') {
      const words = state.kind === 'missing' ? (state.message || `Spending not collected for ${cycleText(ctx.cycle)}`)
        : (state.message || 'The campaign’s spending could not be read');
      const dashed = `<svg class="flow-spend-donut is-dashed" viewBox="0 0 124 124" width="124" height="124" aria-hidden="true"><circle cx="62" cy="62" r="50" fill="none" stroke="#c4bccd" stroke-width="2" stroke-dasharray="6 6"/></svg>`;
      return `<div class="flow-spend" data-state="${state.kind}">${head(state.kind)}<div class="flow-spend-body">${dashed}`
        + `<p class="flow-spend-say${state.kind === 'error' ? ' is-error' : ''}" role="${state.kind === 'error' ? 'alert' : 'status'}">${escape(words)}</p></div></div>`;
    }
    const model = ring(body, {palette: ctx.palette, neutral: ctx.neutral});
    if (!model.segments.length) return cardHtml({kind: 'missing', message: 'No itemized spending on file for this campaign'}, ctx);
    const legend = model.segments.map(segment => `<li class="flow-spend-row${segment.key === 'not_itemized' ? ' is-not-itemized' : ''}">${dot(segment)}`
      + `<span class="flow-spend-cat">${escape(segment.label)}</span><span class="flow-spend-pct">${percent(segment.share)}</span>`
      + `<span class="flow-spend-amt">${escape(money(segment.amount))}</span></li>`).join('');
    const covers = coversWords(body);
    const notes = footnotes(body, true);
    return `<div class="flow-spend" data-state="ok">${head('ok')}${needsChip(body) ? chip(body) : ''}`
      + `<div class="flow-spend-body">${donutSvg(body, model, {size: 124, cycle: ctx.cycle})}<ul class="flow-spend-legend" aria-label="Spending by category">${legend}</ul></div>`
      + (notes.length ? `<p class="flow-spend-fine flow-spend-notes" data-spend-notes>${escape(notes.join(' · '))}</p>` : '')
      + `<p class="flow-spend-foot">${escape(covers)} · the campaign's own spending, not this flow's dollars</p></div>`;
  }

  /* ---------------------------------------------------------------- the comparison bar */
  // The campaign's shares beside the median of the peer campaigns already collected for the same cycle and office.
  // Fewer than five peers: no bar, and the note says why (the server applies the same rule; this one is the guard).
  function compare(body) {
    const peers = body.peers;
    if (!peers || peers.too_few || !(peers.n >= PEERS_MIN) || !peers.shares)
      return {hidden: true, note: (peers && peers.note) || 'Too few collected campaigns to compare', n: peers ? peers.n || 0 : 0};
    const mine = Object.fromEntries((body.categories || []).filter(item => item.amount > 0).map(item => [item.key, item.share]));
    return {hidden: false, n: peers.n, label: peers.label, mine, median: peers.shares};
  }

  // A 100% stacked bar: the categories in the server's fixed order, so the same colour sits in the same place on both bars.
  function barSvg(shares, label, palette, neutral) {
    const width = 300, high = 16;
    let x = 0;
    const rects = CATEGORIES.map(([key, words]) => {
      const share = shares[key] || 0;
      if (share <= 0) return '';
      const w = share * width;
      const out = `<rect class="flow-spend-barseg" x="${f(x)}" y="0" width="${f(w)}" height="${high}" fill="${colourOf(key, palette, neutral)}"><title>${escape(words)} ${percent(share)}</title></rect>`;
      x += w;
      return out;
    }).join('');
    return `<svg class="flow-spend-stack" viewBox="0 0 ${width} ${high}" width="100%" height="${high}" preserveAspectRatio="none" role="img" aria-label="${escape(label)}">${rects}</svg>`;
  }

  function compareHtml(body, ctx) {
    const mode = compare(body);
    if (mode.hidden) return `<h4 class="flow-spend-h">Spending by category</h4><p class="flow-spend-note-line" data-compare="hidden">${escape(mode.note)}</p>`;
    const said = (shares, name) => `${name}: ` + CATEGORIES.filter(([key]) => (shares[key] || 0) > 0.005).sort((a, b) => shares[b[0]] - shares[a[0]]).slice(0, 4)
      .map(([key, words]) => `${words} ${percent(shares[key])}`).join(', ');
    const mine = `This campaign · ${cycleText(ctx.cycle)}`;
    return `<h4 class="flow-spend-h">Spending by category</h4><div class="flow-spend-compare" data-compare="shown">`
      + `<p class="flow-spend-barname">${escape(mine)}</p>${barSvg(mode.mine, said(mode.mine, mine), ctx.palette, ctx.neutral)}`
      + `<p class="flow-spend-barname">${escape(mode.label)}</p>${barSvg(mode.median, said(mode.median, mode.label), ctx.palette, ctx.neutral)}`
      + '<p class="flow-spend-fine">Median shares of each category, rescaled to 100%. Collected campaigns only; none were fetched for this.</p></div>';
  }

  /* ---------------------------------------------------------------- the panel */
  // view: {category, payments (the list is open)}; the default category is the largest.
  function chosen(body, model, view) {
    const keys = model.items.map(item => item.key);
    return keys.includes(view && view.category) ? view.category : (keys[0] || '');
  }

  function payeesHtml(body, category) {
    const list = (body.payees && body.payees[category]) || [];
    const words = LABELS[category] || category;
    const rows = list.length ? list.map(item => `<tr><td class="flow-spend-payee">${escape(item.payee)}</td><td class="flow-spend-num">${escape(whole(item.amount))}</td>`
      + `<td class="flow-spend-num">${grouped(item.payments)}</td></tr>`).join('')
      : '<tr><td colspan="3" class="flow-spend-none">No itemized payees</td></tr>';
    return `<h4 class="flow-spend-h">Top payees in ${escape(words)}</h4><table class="flow-spend-table"><thead><tr><th scope="col">Payee</th>`
      + `<th scope="col" class="flow-spend-num">Amount</th><th scope="col" class="flow-spend-num">Payments</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  // Each payment links to its FEC filing when the row carries one (vetted by MentatScope.fecLink in the page).
  function paymentsHtml(body, category, link) {
    const set = (body.payments && body.payments[category]) || {total: 0, rows: []};
    const rows = set.rows.map(item => {
      const url = link ? link(item.url) : '';
      const date = escape(item.date || '');
      return `<tr><td class="flow-spend-date">${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${date} ↗</a>` : date}</td>`
        + `<td class="flow-spend-payee">${escape(item.payee)}</td><td class="flow-spend-purpose">${escape(item.purpose)}</td>`
        + `<td class="flow-spend-num">${escape(cents(item.amount))}</td></tr>`;
    }).join('');
    const more = set.total > set.rows.length ? `<p class="flow-spend-fine">Showing the first ${set.rows.length} of ${grouped(set.total)} payments, newest first.</p>` : '';
    return `<h4 class="flow-spend-h">${escape(LABELS[category] || category)} · ${plural(set.total, 'payment')}</h4>`
      + `<div class="flow-spend-scroll" tabindex="0" role="region" aria-label="Payments in ${escape(LABELS[category] || category)}">`
      + `<table class="flow-spend-table flow-spend-pay"><thead><tr><th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Purpose as filed</th>`
      + `<th scope="col" class="flow-spend-num">Amount</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="flow-spend-none">No payments</td></tr>'}</tbody></table></div>${more}`;
  }

  // The FEC bulk file's operating expenditures (body.bulk, app/bulk_oppexp.py) for committees whose API Schedule B is not
  // complete: its own labeled section under everything else, never part of the ring, the totals or the comparison. Once the
  // API is complete the server sends shown: false (api_complete) and only that one line is drawn. Memo rows are hidden.
  function bulkHtml(bulk) {
    if (!bulk) return '';
    if (!bulk.shown) {
      return bulk.message ? `<p class="flow-spend-fine" data-bulk="${escape(bulk.reason || 'hidden')}">${escape(bulk.message)}</p>` : '';
    }
    const rows = (bulk.rows || []).map(item => `<tr><td class="flow-spend-date">${escape(item.date || '')}</td>`
      + `<td class="flow-spend-payee">${escape(item.payee)}</td><td class="flow-spend-purpose">${escape(item.purpose)}</td>`
      + `<td class="flow-spend-num">${item.amount == null ? '' : escape(cents(item.amount))}</td></tr>`).join('');
    const total = bulk.total || 0, shown = (bulk.rows || []).length;
    const fine = [shown < total ? `Showing the newest ${shown} of ${grouped(total)} payments.` : '',
      bulk.memo_rows_hidden ? `${plural(bulk.memo_rows_hidden, 'memo row')} hidden.` : '', bulk.scope ? `${bulk.scope}.` : ''].filter(Boolean).join(' ');
    return `<section class="flow-spend-bulk" data-bulk="shown" aria-label="Operating expenditures from the FEC bulk file">`
      + `<h4 class="flow-spend-h">Operating expenditures · ${escape(bulk.label || 'FEC bulk file, source date unavailable')}</h4>`
      + `<p class="flow-spend-chip" role="note"><span aria-hidden="true">&#9432;</span> Bulk coverage · ${escape(bulk.note || 'Not API-collected; not added to the spending figures above')}</p>`
      + `<div class="flow-spend-scroll" tabindex="0" role="region" aria-label="Bulk file payments"><table class="flow-spend-table flow-spend-pay"><thead><tr>`
      + `<th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Purpose as filed</th><th scope="col" class="flow-spend-num">Amount</th></tr></thead>`
      + `<tbody>${rows || '<tr><td colspan="4" class="flow-spend-none">No operating expenditures in the bulk file for these committees</td></tr>'}</tbody></table></div>`
      + (fine ? `<p class="flow-spend-fine">${escape(fine)}</p>` : '') + '</section>';
  }

  // The whole panel. state as for cardHtml; ctx adds {link(url), rows (an Open in Rows button is offered)}.
  function panelHtml(state, ctx, view) {
    view = view || {};
    const title = `${state.body && state.body.committee_name ? state.body.committee_name : ctx.name} · ${cycleText(ctx.cycle)} spending`;
    const head = `<div class="flow-spend-panel-head"><h3 class="flow-rail-title flow-spend-title">${escape(title)}</h3>`
      + '<button type="button" class="flow-pin-x" data-spend-close aria-label="Close spending panel">×</button></div>';
    const foot = `<p class="flow-spend-note">${escape(NOTE)}</p>`;
    const rowsButton = ctx.rows ? '<button type="button" class="secondary flow-spend-rows" data-spend-rows>Open in Rows</button>' : '';
    const bulk = bulkHtml(state.body && state.body.bulk);
    if (state.kind !== 'ok') {
      return `${head}${cardHtml(state, ctx)}${bulk}${foot}<div class="flow-spend-actions">${rowsButton}<button type="button" class="secondary" data-spend-close>Close</button></div>`;
    }
    const body = state.body;
    const model = ring(body, {top: Infinity, palette: ctx.palette, neutral: ctx.neutral});
    if (!model.segments.length) return `${head}${cardHtml({kind: 'missing', message: 'No itemized spending on file for this campaign'}, ctx)}${bulk}${foot}`;
    const category = chosen(body, model, view);
    const covers = `<p class="flow-spend-fine">${escape(coversWords(body))}</p>`;
    const notes = footnotes(body, false);
    const noteList = notes.length ? `<ul class="flow-spend-notes" data-spend-notes aria-label="Notes on these figures">${notes.map(line => `<li>${escape(line)}</li>`).join('')}</ul>` : '';
    const legend = model.segments.map(segment => {
      const hatch = segment.key === 'not_itemized';
      const inner = `${dot(segment)}<span class="flow-spend-cat">${escape(segment.label)}</span><span class="flow-spend-pct">${percent(segment.share)}</span>`
        + `<span class="flow-spend-amt">${escape(money(segment.amount))}</span>`;
      return hatch ? `<li class="flow-spend-row is-not-itemized">${inner}</li>`
        : `<li><button type="button" class="flow-spend-row" data-cat="${escape(segment.key)}" aria-pressed="${segment.key === category}">${inner}</button></li>`;
    }).join('');
    const picked = model.segments.find(segment => segment.key === category);
    const readout = picked ? `${picked.label} ${percent(picked.share)} · ${money(picked.amount)} · ${plural(picked.payments || 0, 'payment')}` : '';
    const open = Boolean(view.payments);
    return head + (needsChip(body) ? chip(body) : '')
      + `<div class="flow-spend-ringbox">${donutSvg(body, model, {size: 220, interactive: true, cycle: ctx.cycle, selected: category})}<p class="flow-spend-read" role="status" aria-live="polite" data-default="${escape(readout)}">${escape(readout)}</p></div>`
      + `<ul class="flow-spend-legend is-panel" aria-label="Spending by category">${legend}</ul>${noteList}${covers}`
      + compareHtml(body, ctx) + payeesHtml(body, category)
      + `<div class="flow-spend-actions"><button type="button" data-spend-payments aria-expanded="${open}">${open ? 'Hide payments' : 'Open payments'}</button>${rowsButton}</div>`
      + (open ? `<div class="flow-spend-payments">${paymentsHtml(body, category, ctx.link)}</div>` : '') + bulk + foot;
  }

  /* ---------------------------------------------------------------- one read per candidate and cycle */
  // A kept answer is for the page's lifetime; a failure is not kept (the next hover asks again). One request in flight
  // per key; the detail answer (payees, payments, peers) also serves the light one. options: {detail, name, peers}.
  // `name` is the candidate's name as filed (the chart knows it), used only to spot payments to the candidate in person;
  // `peers` are the other candidate ids of the same office the chart draws, the only campaigns a comparison may read.
  function client(fetcher) {
    const kept = new Map(), pending = new Map(), latest = new Map();    // latest: the newest detail answer per candidate and cycle
    const peersOf = options => ((options && options.peers) || []).slice().sort().join(',');
    const ask = (candidate, cycle, options) => `/api/candidate-spending?candidate=${encodeURIComponent(candidate)}&cycle=${encodeURIComponent(cycle)}`
      + (options && options.name ? `&name=${encodeURIComponent(options.name)}` : '')
      + (options && options.detail ? '&detail=1' + (peersOf(options) ? `&peers=${encodeURIComponent(peersOf(options))}` : '') : '');
    const key = (candidate, cycle, options) => `${candidate}|${cycle}|${options && options.detail ? 1 : 0}|${options && options.detail ? peersOf(options) : ''}`;
    function peek(candidate, cycle, options) {
      if (options && options.detail) return kept.get(key(candidate, cycle, options)) || null;
      return latest.get(`${candidate}|${cycle}`) || kept.get(key(candidate, cycle, options)) || null;
    }
    function load(candidate, cycle, options) {
      const have = peek(candidate, cycle, options);
      if (have) return Promise.resolve(have);
      const id = key(candidate, cycle, options);
      if (pending.has(id)) return pending.get(id);
      const run = Promise.resolve().then(() => (fetcher || root.fetch.bind(root))(ask(candidate, cycle, options), {headers: {Accept: 'application/json'}}))
        .then(async response => {
          let body = null;
          try { body = await response.json(); } catch (error) { body = null; }
          if (response.ok && body) return {kind: 'ok', body};
          // nothing to draw: not collected, the campaign committee not identified, or no source installed (say so, never a zero)
          if (body && ['not_collected', 'mapping_unknown', 'source_unavailable'].includes(body.status) && (response.status === 404 || response.status === 503))
            return {kind: 'missing', message: body.error, body};
          return {kind: 'error', message: (body && body.error) || 'The campaign’s spending could not be read'};
        }, error => ({kind: 'error', message: 'The campaign’s spending could not be read: ' + (error && error.message)}))
        .then(value => {
          pending.delete(id);
          if (value.kind !== 'error') { kept.set(id, value); if (options && options.detail) latest.set(`${candidate}|${cycle}`, value); }
          return value;
        });
      pending.set(id, run);
      return run;
    }
    return {load, peek, url: ask};
  }

  // One answer cache for the page: a chart redrawn (a resize) keeps what it has already read.
  let kept = null;
  const shared = () => kept || (kept = client(null));

  // A node the card and panel can speak for: an individual candidate (not the industry flow's aggregate outcome).
  const eligible = (node, cycle) => Boolean(node && node.kind === 'candidate' && !node.outcome && !node.stub
    && /^[HSP][0-9A-Z]{8}$/.test(String(node.candidate_id || '')) && Number(cycle) % 2 === 0 && Number(cycle) > 1900);

  /* ---------------------------------------------------------------- the DOM: tooltip section and panel */
  // host: the chart's host element (for its document). options: {fetcher, panel, link, onRows(node), peersOf(node) (the
  // other candidate ids of the same office the chart draws), view (the chart's persistent view state), after() (the
  // tooltip changed size: place it again), restoreFocus(node) (focus the node that opened the panel; false if it is no
  // longer drawn), neutral}
  function controller(host, options) {
    const doc = host.ownerDocument;
    const api = options.fetcher ? client(options.fetcher) : shared();
    const palette = () => (root.MentatTheme && Array.isArray(root.MentatTheme.categorical) && root.MentatTheme.categorical.length >= 12
      ? root.MentatTheme.categorical : FALLBACK);
    let timer = 0, shown = null, token = 0, current = null, view = options.view || {};
    const nameOf = node => String(node.name_as_filed || node.label || '');
    const ctxOf = (node, cycle, extra) => Object.assign({name: node.label, cycle, palette: palette(), neutral: options.neutral || NEUTRAL}, extra || {});

    function section(tip, node, cycle) {
      cancel();
      shown = {node, cycle};
      const box = doc.createElement('div');
      box.className = 'flow-spend-section';
      tip.appendChild(box);
      tip.classList.add('has-spend');
      const draw = state => { if (shown && shown.node === node) { box.innerHTML = cardHtml(state, ctxOf(node, cycle)); options.after && options.after(); } };
      const ask = {name: nameOf(node)};
      const have = api.peek(node.candidate_id, cycle, ask);
      if (have) { draw(have); return; }
      draw({kind: 'loading'});
      // 150 ms of staying on the node before anything is asked: sweeping the pointer across the column asks for nothing
      timer = setTimeout(() => { timer = 0; api.load(node.candidate_id, cycle, ask).then(draw); }, DEBOUNCE_MS);
    }
    function cancel() { clearTimeout(timer); timer = 0; shown = null; }

    /* the panel */
    function drawPanel(state) {
      const panel = options.panel;
      if (!panel || !current) return;
      const keep = doc.activeElement && doc.activeElement.dataset ? doc.activeElement.dataset.cat : '';
      panel.innerHTML = panelHtml(state, ctxOf(current.node, current.cycle, {link: options.link, rows: Boolean(options.onRows)}), view);
      panel.hidden = false;
      current.state = state;
      if (keep) { const again = panel.querySelector(`[data-cat="${keep}"]`); if (again) again.focus(); }
    }
    function open(node, cycle) {
      if (!options.panel || !eligible(node, cycle)) return false;
      view.spend = {candidate: node.candidate_id, cycle};
      if (current && current.node.candidate_id !== node.candidate_id) { view.category = ''; view.payments = false; }
      const mine = current = {node, cycle, state: {kind: 'loading'}};
      drawPanel(mine.state);
      api.load(node.candidate_id, cycle, {detail: true, name: nameOf(node), peers: options.peersOf ? options.peersOf(node) : []})
        .then(state => { if (current === mine) drawPanel(state); });
      return true;
    }
    // Closing the panel (its ✕ or Close button, or Escape) gives focus back to the node that opened it, if that node is still
    // drawn, else to the chart itself: focus never falls to the page. A close the code makes (restore, detach) moves no focus.
    function close(gesture) {
      const opener = current && current.node;
      current = null;
      delete view.spend;
      if (options.panel) { options.panel.replaceChildren(); options.panel.hidden = true; }
      if (gesture && gesture.focus && opener) giveFocusBack(opener);
    }
    function giveFocusBack(node) {
      if (options.restoreFocus && options.restoreFocus(node)) return;
      if (typeof host.focus !== 'function') return;
      if (host.hasAttribute && !host.hasAttribute('tabindex') && host.setAttribute) host.setAttribute('tabindex', '-1');
      host.focus();
    }
    const readout = (text) => { const el = options.panel && options.panel.querySelector('.flow-spend-read'); if (el) el.textContent = text; };
    const pulled = seg => { const [dx, dy] = (seg.dataset.pull || '0 0').split(' '); return `translate(${dx} ${dy})`; };
    // The panel's hands. A chart is re-mounted on every redraw and builds its own controller, so a controller takes its
    // listeners off again when it is detached: the panel never answers to a chart that is gone.
    const listeners = [];
    function on(target, type, handler, capture) { target.addEventListener(type, handler, capture); listeners.push([target, type, handler, capture]); }
    function wire() {
      const panel = options.panel;
      if (!panel) return;
      on(panel, 'click', event => {
        const target = event.target.closest ? event.target : null;
        if (!target || !current) return;
        if (target.closest('[data-spend-close]')) { close({focus: true}); return; }
        if (target.closest('[data-spend-rows]')) { options.onRows && options.onRows(current.node); return; }
        if (target.closest('[data-spend-payments]')) { view.payments = !view.payments; drawPanel(current.state); return; }
        const pick = target.closest('[data-cat]');
        if (pick) { view.category = pick.dataset.cat; drawPanel(current.state); }
      });
      on(panel, 'keydown', event => {
        const seg = event.target.closest && event.target.closest('.flow-spend-seg[data-cat]');
        if (current && seg && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); view.category = seg.dataset.cat; drawPanel(current.state); }
      });
      const over = event => {
        const seg = event.target.closest && event.target.closest('.flow-spend-seg[data-cat]');
        if (!seg) return;
        seg.setAttribute('transform', pulled(seg));
        readout(seg.getAttribute('aria-label').replace(/, /g, ' · '));
      };
      const out = event => {
        const seg = event.target.closest && event.target.closest('.flow-spend-seg[data-cat]');
        if (!seg) return;
        seg.removeAttribute('transform');
        const el = panel.querySelector('.flow-spend-read');
        if (el) el.textContent = el.dataset.default || '';
      };
      on(panel, 'pointerover', over);
      on(panel, 'pointerout', out);
      on(panel, 'focusin', over);
      on(panel, 'focusout', out);
      // Escape closes the panel before the view's own Escape (go back a level) can read it. It does nothing while the panel is
      // not showing, and nothing for an Escape typed in a field (that one belongs to the field).
      on(doc, 'keydown', event => {
        if (event.key !== 'Escape' || !current || panel.hidden) return;
        const from = event.target;
        if (from && (/^(?:INPUT|TEXTAREA|SELECT)$/.test(String(from.tagName || '').toUpperCase()) || from.isContentEditable)) return;
        event.preventDefault(); close({focus: true});
      }, true);
    }
    // The chart is being redrawn or left: hands off and the panel emptied, but what the reader had open (view.spend) is kept.
    function detach() {
      cancel();
      for (const [target, type, handler, capture] of listeners.splice(0)) target.removeEventListener(type, handler, capture);
      current = null;
      if (options.panel) { options.panel.replaceChildren(); options.panel.hidden = true; }
    }
    wire();
    return {section, cancel, open, close, detach, isOpen: () => Boolean(current), current: () => current && current.node,
      load: api.load, peek: api.peek,
      // a redraw (resize, colour) re-mounts the chart: the panel the reader had open comes back
      restore(find) {
        const saved = view.spend;
        const node = saved && find(saved.candidate);
        if (node) open(node, saved.cycle); else close();
      }};
  }

  const api = {ring, arcPath, donutSvg, cardHtml, panelHtml, bulkHtml, compare, compareHtml, payeesHtml, paymentsHtml, client, controller,
    eligible, colourOf, coverageText, money, percent, cycleText, summary, CATEGORIES, LABELS, NOTE, TOP, PEERS_MIN, DEBOUNCE_MS};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MentatCandidateSpending = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
