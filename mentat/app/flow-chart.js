/* Flow (Sankey) renderer: SVG for the Flow views (lane F, boards #44-#46).
 *
 * One rendering path. svgString() turns a flow-layout.js result into SVG markup (pure,
 * so node:test reads exactly what the page draws); mount() puts it in a host and wires
 * the reader's hands with delegated listeners:
 *  - hover (or keyboard focus) lights its route upstream and downstream and fades the
 *    rest; in a committee's flow the route stops at the root's pool (traced: pooled money
 *    is not traced money), so a candidate's route never lights every donor;
 *  - click re-roots on a committee (options.onReroot) and opens a candidate
 *    (options.onCandidate: the downstream drawer);
 *  - a tooltip gives the amount, its share of the stage, the rows behind it and the FEC
 *    link (MentatScope.fecLink, the one place such URLs are vetted);
 *  - arrow keys move focus through the nodes (up/down in a column, left/right across).
 *
 * Ribbon colour: Source | Target | Outcome | Gradient. Gradient is an SVG linearGradient
 * per ribbon, userSpaceOnUse from the source's right edge to the target's left edge: the
 * source colour blends into the target colour. Default opacity 40%; the PAC evidence and
 * Graph views rest at VIEW_OPACITY, 20% (boards #44/#45, lanes G1/G1b: lane F2a's 28% made
 * ribbons that overlap read as solid colour), and a hover lights its route at 60% (flow.css).
 * Colours come only from MentatTheme.categorical. Pooled money is never coloured by an
 * original donor: a ribbon takes the colour of the committee it leaves (or its outcome),
 * never a donor's.
 *
 * Labels (lane F2a): beside the bars, 12-13 px, no halo; each node's label is drawn at the
 * level of detail the layout chose (flow-layout.js), cut at a word with an ellipsis and an
 * amount kept whole; every word the drawing leaves out is in the tooltip and the node's
 * accessible name. The tooltip is compact and sits by the hovered point in the free space
 * around it (lanes G1/G1b, placeTip): the nearest spot at least 4 px clear of every label,
 * column header and legend, never over the point, and never above the drawing (the title
 * and subtitle sit there), and off the lit route's ribbons where it can; where a dense
 * drawing leaves no such spot near the point, it stays by the point and spares the headers
 * and the hovered route's own words and ribbons.
 *
 * CSP (style-src 'self'): no style attributes and no <style>; presentation attributes and
 * classes only, and the tooltip is placed through the CSSOM.
 */
(function (root) {
  'use strict';

  const layoutApi = () => root.MentatFlowLayout || (typeof require === 'function' ? require('./flow-layout.js') : null);

  // The campaign-spending card and panel (candidate-spending.js) load the first time a candidate is hovered, as
  // evidence-map.js does: a console that never draws a Flow does not carry them. A node they can speak for is an
  // individual candidate (not the industry flow's aggregate outcome) in an even cycle.
  const spendable = (node, cycle) => Boolean(node && node.kind === 'candidate' && !node.outcome && !node.stub
    && /^[HSP][0-9A-Z]{8}$/.test(String(node.candidate_id || '')) && Number(cycle) % 2 === 0 && Number(cycle) > 1900);
  let spendLoading = null;
  function spendModule() {
    if (root.MentatCandidateSpending) return root.MentatCandidateSpending;
    if (typeof module !== 'undefined' && typeof require === 'function') {
      try { return require('./candidate-spending.js'); } catch (error) { return null; }
    }
    return null;
  }
  function spendReady(doc) {
    const have = spendModule();
    if (have) return Promise.resolve(have);
    if (!spendLoading) spendLoading = new Promise(resolve => {
      const script = doc.createElement('script');
      script.src = '/candidate-spending.js';
      script.onload = () => resolve(spendModule());
      script.onerror = () => { spendLoading = null; resolve(null); };
      doc.head.appendChild(script);
    });
    return spendLoading;
  }
  // The hosted console (Cloudflare Access) refuses /api/candidate-spending (it is not on the hosted allowlist), so there
  // the card and panel are off entirely: no hover card, no click panel, and a click keeps its Rows behaviour.
  // app.js sets MentatConsole.hosted from /api/session.
  const hostedConsole = () => Boolean(root.MentatConsole && root.MentatConsole.hosted);
  const spendingOn = (options, hosted) => Boolean(options) && options.spending !== false && !(hosted === undefined ? hostedConsole() : hosted);
  // Once the module is there: `use(api)` only if the chart is still alive. A chart destroyed while the module was loading
  // (a redraw, the view left) must not get a controller or a listener from the late arrival.
  function spendLater(doc, alive, use) {
    return spendReady(doc).then(api => { if (api && alive()) use(api); });
  }
  const NEUTRAL = '#a39b92';
  const FALLBACK = ['#327a7a', '#57447b', '#8b6984', '#eca91c', '#de8aab', '#7d4b00', '#3cb0a3', '#83333e', '#99973f',
    '#a09eea', '#d88766', '#509ecd'];
  const PARTY_RING = {R: '#83333e', D: '#509ecd'};
  const MODES = ['gradient', 'source', 'target', 'outcome'];
  const VIEW_OPACITY = 0.2;
  const HEADER_SIZE = 11, HEADER_SPACING = 0.14;     // flow.css .flow-header: 11px, letter-spacing .14em
  const ELLIPSIS = '…';
  const MONEY_TAIL = /\s(\$[\d.,]+[MK]?)$/;

  const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, character =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
  const f = number => String(Math.round(number * 100) / 100);

  function short(value) {
    const size = Math.abs(value), sign = value < 0 ? '-' : '';
    // Tenths of a million rounded half up on the exact number, as the board prints $54.6M
    // for $54,550,000 (toFixed on the binary 54.55 would say 54.5).
    if (size >= 999950) return `${sign}$${(Math.round(size / 1e5) / 10).toFixed(1)}M`;
    if (size >= 1000) return `${sign}$${Math.round(size / 1000).toLocaleString('en-US')}K`;
    return `${sign}$${Math.round(size).toLocaleString('en-US')}`;
  }
  const full = value => (value < 0 ? '-' : '') + '$' + Math.abs(value).toLocaleString('en-US',
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const percent = share => `${(share * 100).toFixed(1)}%`;

  function palette() {
    const theme = root.MentatTheme;
    return theme && Array.isArray(theme.categorical) && theme.categorical.length >= 12 ? theme.categorical : FALLBACK;
  }

  const HELD = {bitcoin: 'bitcoin', 'USD coin': 'USD coin', ether: 'ether', tether: 'tether', solana: 'solana', stock: 'stock'};
  const heldWords = kinds => (kinds || []).map(kind => HELD[kind] || kind).join(' and ') || 'donated assets';

  // Text fitted to the room before the next column (MentatTheme.fitText in the page); the
  // suffix is kept whole (an amount).
  function fitter(options) {
    const theme = root.MentatTheme;
    if (options.fit) return options.fit;
    if (theme && typeof theme.fitText === 'function')
      return (text, width, size, suffix) => theme.fitText(text, width, size, undefined, suffix);
    return (text, width, size, suffix) => {
      const tail = suffix == null ? '' : String(suffix);
      const room = Math.floor(width / (size * 0.56)) - tail.length;
      return String(text).length <= room ? String(text) + tail
        : String(text).slice(0, Math.max(1, room - 1)).trimEnd() + ELLIPSIS + tail;
    };
  }

  // A text's width in px (MentatTheme.measureText in the page, else the fitter's estimate).
  function measurer(options) {
    const theme = root.MentatTheme;
    if (options.measure) return options.measure;
    if (theme && typeof theme.measureText === 'function') return (text, size) => theme.measureText(text, size);
    return (text, size) => String(text).length * size * 0.56;
  }

  // One line fitted to its room, cut between words with an ellipsis (FL3); a trailing amount
  // ("Other donors $85.2M") is kept whole, and only a single word too long for the room is cut
  // inside it, by the fitter, still with an ellipsis.
  function fitLine(text, room, size, fit, measure) {
    const value = String(text == null ? '' : text);
    if (measure(value, size) <= room) return value;
    const money = MONEY_TAIL.exec(value);
    const head = money ? value.slice(0, money.index) : value;
    const tail = money ? ' ' + money[1] : '';
    const words = head.split(' ');
    for (let count = words.length - 1; count >= 1; count -= 1) {
      const shown = words.slice(0, count).join(' ').replace(/[\s,\u00b7:;(]+$/, '') + ELLIPSIS + tail;
      if (measure(shown, size) <= room) return shown;
    }
    return fit(head, room, size, tail);
  }

  // A column header in the room before the next column, cut between words (its letter-spacing
  // counted); the full words go in a <title>.
  function fitHeader(text, room, fit, measure) {
    const width = value => measure(value, HEADER_SIZE) + HEADER_SPACING * HEADER_SIZE * value.length;
    if (width(text) <= room) return text;
    const words = text.split(' ');
    for (let count = words.length - 1; count >= 1; count -= 1) {
      const shown = words.slice(0, count).join(' ') + ELLIPSIS;
      if (width(shown) <= room) return shown;
    }
    return fit(text, room / (1 + HEADER_SPACING / 0.56), HEADER_SIZE);
  }

  function nodeTitle(node) {
    if (node.kind !== 'candidate' || node.outcome) return node.label;
    const where = [node.party, node.state].filter(Boolean).join('-');
    return where ? `${node.label} (${where})` : node.label;
  }

  function prepare(payload, options) {
    const api = layoutApi();
    // The layout wraps names between words with the page's measured widths (FL3).
    if (!options.measure) options = Object.assign({}, options, {measure: measurer(options)});
    // A view's own arrangement (small transfers folded, a bucket unfolded) is applied to the answer first. The
    // arrangement drops a dangling or empty link and re-adds every node's totals, so it can hide what is wrong with
    // the answer: the answer is checked as it arrived, and again as arranged. If either does not conserve, the
    // answer is drawn as received (nothing folded or unfolded) and the view says so (problems).
    let arranged = null, problems = [];
    if (options.arrange) {
      problems = api.validate(payload);
      if (!problems.length) {
        arranged = api.arrange(payload, options.arrange);
        problems = api.validate(arranged.payload);
        if (problems.length) arranged = null;
      }
    }
    const result = api.layout(arranged ? arranged.payload : payload, options);
    const colours = api.colours(result, options.palette || palette(), options.neutral || NEUTRAL);
    // Chart Studio (#46): a worksheet flow deals its own colours after the palette's
    // (a stage member on Colour, a ribbon split by its Colour or Outcome member).
    if (typeof options.recolour === 'function') options.recolour(result, colours, options.palette || palette(), options.neutral || NEUTRAL);
    const outcomes = api.outcomes(result, colours);
    return {result, colours, outcomes, format: options.format || {short, full},
      payload: arranged ? arranged.payload : payload, groups: arranged ? arranged.groups : [],
      refused: arranged ? arranged.refused : {}, needs: arranged ? arranged.needs : [], problems};
  }

  function ribbonFill(link, context, mode, index, prefix) {
    const {colours, outcomes, result} = context;
    const byId = id => result.nodes.find(node => node.id === id);
    const source = byId(link.source), target = byId(link.target);
    const sourceColour = source.stub ? colours.get(target.id) : colours.get(source.id);
    const targetColour = target.stub ? colours.get(source.id) : colours.get(target.id);
    if (link.colour) return link.colour;
    if (mode === 'source') return sourceColour;
    if (mode === 'target') return targetColour;
    if (mode === 'outcome') return outcomes.get(target.stub ? source.id : target.id) || targetColour;
    return `url(#${prefix}-g-${index})`;
  }

  /* ----------------------------------------------------------------- markup */
  function svgString(payload, options) {
    options = Object.assign({ribbons: 'gradient', opacity: 0.4, width: 1100, height: 520, headers: true,
      labelSize: 13, subSize: 11.5, labelLine: 16, subLine: 14, amounts: true}, options || {});
    const mode = MODES.includes(options.ribbons) ? options.ribbons : 'gradient';
    const prefix = String(options.idPrefix || 'flow').replace(/[^A-Za-z0-9_-]/g, '');
    // A focus zoom keeps the level it came from as a narrow strip on the left; the chart takes the rest.
    const stripWidth = options.context && options.stripWidth ? Number(options.stripWidth) : 0;
    const context = options.prepared || prepare(payload, stripWidth ? Object.assign({}, options, {width: options.width - stripWidth}) : options);
    const {result, colours, format} = context;
    const fit = fitter(options);
    const measure = measurer(options);
    const byId = new Map(result.nodes.map(node => [node.id, node]));
    const labels = nodeMarkup(result, options, {colours, format, fit, measure, prefix});
    const parts = [];
    const outer = result.width + stripWidth;
    parts.push(`<svg xmlns="http://www.w3.org/2000/svg" class="flow-chart" viewBox="0 0 ${f(outer)} ${f(result.height)}"`
      + ` width="${f(outer)}" height="${f(result.height)}" role="group" aria-label="${escape(options.title || 'Flow of money')}">`);
    parts.push(`<defs><pattern id="${prefix}-hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">`
      + `<rect width="6" height="6" fill="#f4efe9"/><line x1="0" y1="0" x2="0" y2="6" stroke="${NEUTRAL}" stroke-width="1.4"/></pattern>`);
    result.links.forEach((link, index) => {
      const source = byId.get(link.source), target = byId.get(link.target);
      if (target.stub === 'before_gate') return;
      if ((mode !== 'gradient' || link.colour) && !source.stub && !target.stub) return;
      // Stubs fade in (money from outside) or out (money leaving the view) in the node's colour.
      const from = source.stub ? colours.get(target.id) : colours.get(source.id);
      const to = target.stub ? colours.get(source.id) : colours.get(target.id);
      const fadeIn = source.stub ? 0 : 1, fadeOut = target.stub ? 0 : 1;
      parts.push(`<linearGradient id="${prefix}-g-${index}" gradientUnits="userSpaceOnUse" x1="${f(link.gradient.x1)}" y1="0"`
        + ` x2="${f(link.gradient.x2)}" y2="0"><stop offset="0" stop-color="${from}" stop-opacity="${fadeIn}"/>`
        + `<stop offset="1" stop-color="${to}" stop-opacity="${fadeOut}"/></linearGradient>`);
    });
    parts.push('</defs>');
    const made = stripWidth ? strip(options.context, options, result.height) : null;
    if (made) parts.push(made.markup);
    parts.push(`<g class="flow-main"${made ? ` transform="translate(${stripWidth} 0)"` : ''}>`);
    if (options.headers) {
      parts.push('<g class="flow-headers" aria-hidden="true">');
      result.columns.forEach((column, at) => {
        const words = String(column.label || '').toUpperCase();
        const next = result.columns[at + 1];
        const shown = fitHeader(words, (next ? next.x : result.width - 8) - column.x - 12, fit, measure);
        const text = `<text class="flow-header" x="${f(column.x)}" y="14">${escape(shown)}</text>`;
        parts.push(shown === words ? text : `<g class="flow-header-cut"><title>${escape(words)}</title>${text}</g>`);
      });
      parts.push('</g>');
    }
    if (Array.isArray(options.legend) && options.legend.length) {
      parts.push(`<g class="flow-legend" aria-label="${escape(options.legendTitle || 'Colour')}">`);
      for (const item of options.legend) {
        parts.push(`<rect class="flow-legend-swatch" x="${f(item.x)}" y="${f(item.y - options.labelSize * 0.72)}" width="12" height="12"`
          + ` rx="2" fill="${item.colour}"/><text class="flow-legend-label" x="${f(item.x + 17)}" y="${f(item.y)}">${escape(item.label)}</text>`);
      }
      parts.push('</g>');
    }
    // Labels are drawn over the translucent ribbons as they are, with no halo and no knocked-out
    // box behind them (boards #44 and #46; a knock-out read as a halo in the round-1 captures).
    parts.push('<g class="flow-links">');
    result.links.forEach((link, index) => {
      const source = byId.get(link.source), target = byId.get(link.target);
      const stubbed = source.stub || target.stub;
      const fill = stubbed ? `url(#${prefix}-g-${index})` : ribbonFill(link, context, mode, index, prefix);
      // F12: money paid before the network's arrived is a line under the node, not a ribbon.
      if (target.stub === 'before_gate') return;
      const late = link.date_unverified ? ' flow-link--unverified' : '';
      const onward = target.stub === 'onward' && target.committee_id ? ' flow-link--onward' : '';
      parts.push(`<path class="flow-link${stubbed ? ' flow-link--stub' : ''}${onward}${late}" data-link="${escape(link.id)}"`
        + ` d="${link.path}" fill="${fill}" fill-opacity="${options.opacity}"/>`);
    });
    parts.push('</g>');
    parts.push(...groupMarkup(context, escape));
    parts.push('<g class="flow-nodes">');
    parts.push(...labels.parts);
    parts.push('</g>');
    if (options.explore) parts.push(...chipMarkup(result, measure));
    parts.push('</g></svg>');
    return parts.join('');
  }

  // A thin bracket on each unfolded group, with a chevron to fold it back.
  function groupMarkup(context, esc) {
    const parts = [];
    for (const group of context.groups || []) {
      const nodes = context.result.nodes.filter(node => node.group === group.id && !node.stub);
      if (!nodes.length) continue;
      const x = Math.min(...nodes.map(node => node.x0)) - 6;
      const top = Math.min(...nodes.map(node => node.y0)) - 1, bottom = Math.max(...nodes.map(node => node.y1)) + 1;
      const cy = top - 9;
      parts.push(`<g class="flow-group" data-group="${esc(group.id)}"><path class="flow-bracket" d="M${f(x + 6)},${f(top)}H${f(x)}V${f(bottom)}H${f(x + 6)}"/>`
        + `<g class="flow-collapse" data-collapse="${esc(group.id)}" role="button" tabindex="-1" aria-label="Fold this group back">`
        + `<circle cx="${f(x)}" cy="${f(cy)}" r="8"/><path d="M${f(x - 3.5)},${f(cy + 1.8)}L${f(x)},${f(cy - 1.8)}L${f(x + 3.5)},${f(cy + 1.8)}"/></g></g>`);
    }
    return parts;
  }

  // "-> n onward" at the end of a node's stub ribbons, shown while the node is hovered or focused: the
  // money it passed on past the drawn steps, each a committee the reader can follow (B6).
  function onwardOf(result) {
    const byNode = new Map();
    for (const stub of result.nodes) {
      if (stub.stub !== 'onward' || !stub.committee_id) continue;
      if (!byNode.has(stub.attach)) byNode.set(stub.attach, []);
      byNode.get(stub.attach).push(stub);
    }
    return byNode;
  }

  function chipMarkup(result, measure) {
    const parts = [];
    for (const [id, stubs] of onwardOf(result)) {
      const text = `→ ${stubs.length} onward`;
      const width = Math.ceil(measure(text, 10.5)) + 14;
      const x = Math.max(...stubs.map(stub => stub.x0)) + 4;
      const y = stubs.reduce((sum, stub) => sum + (stub.y0 + stub.y1) / 2, 0) / stubs.length;
      parts.push(`<g class="flow-onward" data-onward="${escape(id)}" role="button" tabindex="-1" aria-label="${escape(`Passed on to ${stubs.length} committee${stubs.length === 1 ? '' : 's'} past the drawn steps; follow`)}">`
        + `<rect x="${f(x)}" y="${f(y - 8)}" width="${width}" height="16" rx="8"/><text x="${f(x + 7)}" y="${f(y + 3.6)}">${escape(text)}</text></g>`);
    }
    return parts;
  }

  // A group the reader can unfold: a bucket that lists its members.
  const expandable = node => node.kind === 'bucket' && Array.isArray(node.members) && node.member_count > 1;

  // Each node's bar and label, the label at the level of detail the layout chose.
  function nodeMarkup(result, options, tools) {
    const {colours, format, fit, measure, prefix} = tools;
    const amountSize = options.amountSize || 12;
    const parts = [];
    const first = result.columns.flatMap(column => column.ids)[0];
    for (const node of result.nodes) {
      if (node.stub) continue;
      const colour = colours.get(node.id);
      const hatched = node.kind === 'balance' && ['unspent', 'kept', 'uncounted', 'untraced', 'unreported'].includes(node.balance);
      const height = Math.max(node.y1 - node.y0, 2);
      const label = nodeTitle(node);
      // The accessible name says everything the label may leave to the tooltip (FL6).
      const said = (node.sublines || []).length ? `; ${node.sublines.join('; ')}` : '';
      const folds = options.explore && expandable(node);
      const aria = `${label}, ${format.full(node.value)}${said}${folds ? '; press Enter to unfold' : ''}`;
      parts.push(`<g class="flow-node flow-node--${node.kind}${node.root ? ' flow-node--root' : ''}${folds ? ' flow-node--folds' : ''}" data-node="${escape(node.id)}"`
        + ` tabindex="${node.id === first ? 0 : -1}" role="button" aria-label="${escape(aria)}">`);
      parts.push(`<rect class="flow-bar" x="${f(node.x0)}" y="${f(node.y0)}" width="${f(node.x1 - node.x0)}" height="${f(height)}"`
        + ` fill="${hatched ? `url(#${prefix}-hatch)` : colour}"${hatched ? ` stroke="${NEUTRAL}" stroke-width="1"` : ''}/>`);
      const lines = Array.isArray(node.label_text) ? node.label_text : [node.label];
      if (node.label_level !== 4 && lines.length) {
        // Beside the bar: to its right, or (the Studio's first column, #46) to its left, right-aligned.
        const left = node.label_side === 'left';
        const anchor = left ? ' text-anchor="end"' : '';
        let x = left ? node.x0 - 8 : node.x1 + 8;
        const y = node.label_y + Math.round(options.labelSize * 0.89);
        if (!left && node.kind === 'candidate' && PARTY_RING[node.party]) {
          parts.push(`<circle class="flow-party" cx="${f(x + 5)}" cy="${f(y - 4)}" r="4.5" fill="none" stroke="${PARTY_RING[node.party]}"`
            + ' stroke-width="2"/>');
          x += 16;
        }
        const room = (node.label_width || 180) - (left ? 0 : x - node.x1 - 8);
        // The Studio's Labels toggle takes the amounts off; a Label measure prints its own text.
        const amount = options.amounts === false ? ''
          : node.amount_text != null ? String(node.amount_text) : format.short(node.amount ?? node.value);
        const inline = node.label_amount === 'inline' && amount;
        lines.forEach((line, at) => {
          const last = at === lines.length - 1;
          if (inline && last) {
            // One line: the name, then its amount beside it.
            const beside = measure(amount, amountSize) + 6;
            const shown = fitLine(line, room - beside, options.labelSize, fit, measure);
            parts.push(`<text class="flow-label" x="${f(x)}" y="${f(y + at * options.labelLine)}"${anchor}>`
              + `${escape(shown)}<tspan class="flow-amount" dx="6">${escape(amount)}</tspan></text>`);
          } else {
            const shown = fitLine(line, room, options.labelSize, fit, measure);
            parts.push(`<text class="flow-label" x="${f(x)}" y="${f(y + at * options.labelLine)}"${anchor}>${escape(shown)}</text>`);
            if (folds && at === 0 && !left)
              parts.push(`<text class="flow-chevron" x="${f(x + measure(shown, options.labelSize) + 5)}" y="${f(y)}" aria-hidden="true">▾</text>`);
          }
        });
        let below = y + lines.length * options.labelLine;
        if (amount && node.label_amount === 'line') {
          parts.push(`<text class="flow-amount" x="${f(x)}" y="${f(below)}"${anchor}>${escape(amount)}</text>`);
          below += options.subLine;
        } else below += options.subLine - options.labelLine;
        // At most one short line under the label (FL6); the rest is in the tooltip.
        if (node.subline_shown) {
          const shown = fitLine(node.subline_shown, room, options.subSize, fit, measure);
          parts.push(`<text class="flow-subline" x="${f(x)}" y="${f(below)}"${anchor}>${escape(shown)}</text>`);
        }
      }
      parts.push('</g>');
    }
    return {parts};
  }

  /* ---------------------------------------------------------------- tooltip */
  // Where a pooling committee's receipts came from, largest first (two at most): the reader is
  // told whose money is in the pool, never that a donor's dollars went to a candidate.
  function shares(node, cycle) {
    const list = (node.share_from_upstream || []).filter(item => item.share != null)
      .slice().sort((a, b) => b.share - a.share).slice(0, 2);
    if (!list.length) return 'pooled, not traced dollars';
    const [first, second] = list;
    return `${percent(first.share)} of its ${cycle} receipts came from ${first.label}`
      + (second ? `, ${percent(second.share)} from ${second.label}` : '') + ' · pooled, not traced dollars';
  }

  function describe(target, context, payload, hints) {
    const {result} = context;
    const {short, full} = context.format || {short: module_short, full: module_full};
    const worksheet = payload.source === 'worksheet';
    const byId = new Map(result.nodes.map(node => [node.id, node]));
    const cycle = payload.cycle ? `${Number(payload.cycle) - 1}–${payload.cycle}` : '';
    const fec = url => (root.MentatScope && root.MentatScope.fecLink ? root.MentatScope.fecLink(url) : '');
    if (target.link) {
      const link = target.link;
      const source = byId.get(link.source), sink = byId.get(link.target);
      const route = (payload.routes || []).find(item => item.target === link.target && item.committee === link.committee
        && Math.round(item.value * 100) === link.cents);
      const path = route ? route.path.map(step => step.label) : [source.stub ? sink.label : source.label];
      const lines = [];
      const title = sink.kind === 'candidate'
        ? [...path, nodeTitle(sink)].join(' → ') + (sink.outcome ? '' : `, ${sink.direction}`)
        : `${source.label} → ${sink.label}`;
      const spender = link.committee ? (byId.get('committee:' + link.committee) || {}).label || source.label : source.label;
      lines.push(sink.kind === 'candidate' ? `${full(link.value)} spent by ${spender}` : full(link.value));
      const pooled = byId.get('committee:' + (link.committee || ''));
      if (pooled && pooled.pooled && (pooled.share_from_upstream || []).length) lines.push(shares(pooled, cycle));
      const column = result.columns[sink.stub ? source.stage : sink.stage];
      const rowWord = worksheet ? 'worksheet row' : 'filing';
      if (column && column.total) lines.push(`${percent(link.value / column.total)} of ${column.label || 'this stage'}`
        + (link.rows ? ` · ${link.rows.toLocaleString('en-US')} ${rowWord}${link.rows === 1 ? '' : 's'}` : ''));
      for (const line of link.details || []) lines.push(line);
      if (link.date_unverified) lines.push('Undated in the filing: counted, date not verified');
      if (source.stub && source.carryover) lines.push(`Carried over: ${source.carryover}`);
      const record = (link.evidence || []).find(item => fec(item.source_url));
      const url = record ? fec(record.source_url) : fec(link.evidence_url || '');
      const follows = sink.stub === 'onward' && sink.committee_id && hints && hints.zoom;
      return {title, lines, url, urlText: record ? 'FEC filing' : 'Committee on FEC', hint: follows ? 'Click to follow it onward' : ''};
    }
    const node = target.node;
    const lines = [full(node.value)];
    const column = result.columns[node.stage];
    if (column && column.total && !node.stub) lines.push(`${percent(node.value / column.total)} of ${column.label || 'this stage'}`);
    if (node.pooled && (node.share_from_upstream || []).length) lines.push(shares(node, cycle));
    if (node.balance === 'unspent') lines.push(`cash on hand ${full(node.cash || 0)} at the end of the window`
      + ((node.held || 0) > 0 ? ` + ${heldWords(node.held_kinds)} ${full(node.held)} still held` : ''));
    if (node.bucket === 'small') {
      // The committees folded into it, largest first: each a name and its amount (most are at nothing).
      lines.push(`${node.member_count} grouped:`);
      for (const member of (node.members || []).slice(0, 10)) lines.push(`${member.label} ${short(member.value)}${member.unnamed ? ' (no name on file)' : ''}`);
      if (node.member_count > 10) lines.push(`… and ${node.member_count - 10} more`);
    } else if (node.bucket === 'other' || (node.kind === 'bucket' && node.member_count)) lines.push(`${node.member_count} grouped` + ((node.members || []).length ? ': ' + node.members.slice(0, 4)
      .map(member => `${member.label} ${short(member.value)}`).join(', ') + (node.member_count > 4 ? ', …' : '') : ''));  // hosted unlisted bodies drop members
    // A name from the FEC committee master (a committee the filings only name by ID) says which year's file it is.
    if (node.name_as_filed && node.name_as_filed !== node.label)
      lines.push(`${node.name_master ? `Name from FEC committee master, ${node.name_master}` : 'As filed'}: ${node.name_as_filed}`);
    // The lines the drawing leaves off (FL6: one line under a label at most): here, every one.
    for (const line of node.sublines || []) if (line !== node.subline_shown) lines.push(line);
    for (const line of node.details || []) lines.push(line);
    if (node.unreached) lines.push(`Not collected (${String(node.unreached).replace(/_/g, ' ')}); not traced past it`);
    // Carryover (b), in the measurement's words: the gate the previous cycle's transfers opened.
    if (node.carryover) {
      lines.push(`Counted from the start of the cycle: ${node.carryover.label}`);
      const by = node.carryover.opened_by || [];
      if (by.length) lines.push('Opened by ' + by.slice(0, 2).map(item => `${item.label} ${short(item.amount)}`
        + (item.first_date ? ` from ${item.first_date}` : '')).join(', ') + (by.length > 2 ? `, and ${by.length - 2} more` : ''));
    }
    return {title: nodeTitle(node), lines, url: fec(node.fec_url || ''), urlText: node.kind === 'candidate' ? 'Candidate on FEC' : 'Committee on FEC',
      hint: hintFor(node, hints)};
  }

  // The gestures a node answers to, in the words of the board: shown last in its tooltip.
  function hintFor(node, hints) {
    if (!hints) return '';
    const pin = hints.pin === false ? '' : 'Shift-click to pin';
    const join = (...words) => words.filter(Boolean).join(' · ');
    if (expandable(node)) return join(node.group ? 'Click to show more' : 'Click to expand', pin);
    if (node.kind === 'committee' && !node.root && node.committee_id && hints.zoom) return join('Click to zoom in', pin);
    if (node.kind === 'candidate' && hints.spend && spendable(node, hints.cycle)) return join('Click for spending detail', pin);
    if (node.kind === 'candidate' && hints.rows) return join('Click to open in Rows', pin);
    return node.stub ? '' : pin;
  }

  /* -------------------------------------------------------------- the trace */
  // Lane G1 (board #44): what a hover lights. flow-layout.js route() is the whole route, upstream and
  // downstream, and in a committee's flow it crosses the root committee's pool. The side of the pool
  // the hovered ribbon or node is not on stays out of the light: Defend American Jobs -> Moreno lights
  // FAIRSHAKE -> Defend American Jobs -> Moreno, not every donor into FAIRSHAKE (pooled money is not
  // traced money, and a whole column of donor ribbons lit at 60% read as solid colour, not the board's
  // pastel). Hovering the root itself, or any flow without a root (the industry flow, a worksheet's),
  // lights the whole route as before.
  function traced(result, start) {
    const lit = layoutApi().route(result, start);
    const root = result.nodes.find(node => node.root && !node.stub);
    if (!root) return lit;
    const byId = new Map(result.nodes.map(node => [node.id, node]));
    const linkById = new Map(result.links.map(link => [link.id, link]));
    const stageOf = id => (byId.get(id) || {}).stage;
    // Money arriving at the root (or before it), and money leaving it (or after it).
    const upstream = link => link.target === root.id || stageOf(link.target) < root.stage;
    const downstream = link => link.source === root.id || stageOf(link.source) > root.stage;
    const link = linkById.get(start);
    const node = link ? null : byId.get(start);
    const side = link ? (upstream(link) ? -1 : downstream(link) ? 1 : 0)
      : !node || node.id === root.id ? 0 : Math.sign(node.stage - root.stage);
    if (!side) return lit;
    const links = new Set([...lit.links].filter(id => {
      const item = linkById.get(id);
      return !item || !(side > 0 ? upstream(item) : downstream(item));
    }));
    const nodes = new Set([...lit.nodes].filter(id => {
      const item = byId.get(id);
      return !item || item.id === root.id || (side > 0 ? item.stage >= root.stage : item.stage <= root.stage);
    }));
    return {nodes, links};
  }

  /* ----------------------------------------------------------- the tooltip spot */
  // Lanes G1/G1b (board #44): where the tooltip goes - in free space near the point (x, y). Spots are
  // swept about the point (above it, below it, beside it and up to `reach` px farther out) inside
  // [4, room - width - 4] x [top, bottom - height - 4]; none covers the point or comes within 6 px of it
  // (the pointer must reach the ribbons, not the tooltip). Every text obstacle - label, amount, column
  // header, legend - is grown by `margin` (4 px) so the tooltip never sits flush against words. A spot
  // clear of all of them wins: the nearest (its distance from the point plus a small cost for any bar it
  // covers - an obstacle with `soft` set, that many px of distance per covered px^2 - and 3 px when it is
  // not above the point, as the board sets the tooltip above the route). Where no spot near the point is
  // clear (a dense drawing leaves no room the tooltip's size), it stays by the point and covers what
  // matters least. A label it touches counts whole (a label cut in half is as unread as one hidden, so
  // the tooltip's edges fall between labels): a column header or a label of the hovered route (kind
  // 'header' or 'route') costs a px of distance per px^2, any other label a hundredth of that (a whole
  // label, about 6000 px^2 with its margin, is worth 60 px of distance).
  const TIP_FALLBACK = {header: 1, route: 1, text: 0.01};
  function placeTip(spec) {
    const x = Number(spec.x) || 0, y = Number(spec.y) || 0;
    const width = Math.max(1, Number(spec.width) || 1), height = Math.max(1, Number(spec.height) || 1);
    const gap = spec.gap == null ? 12 : Number(spec.gap), reach = spec.reach == null ? 160 : Number(spec.reach);
    const margin = spec.margin == null ? 4 : Math.max(0, Number(spec.margin) || 0);
    const minTop = spec.top == null ? 4 : Number(spec.top);
    const maxLeft = Math.max(4, (spec.room == null ? x + 14 + width + 4 : Number(spec.room)) - width - 4);
    const maxTop = Math.max(minTop, (spec.bottom == null ? y + gap + height + 4 : Number(spec.bottom)) - height - 4);
    const obstacles = (spec.obstacles || []).filter(item => item && item.w > 0 && item.h > 0)
      .map(item => item.soft ? item : {x: item.x - margin, y: item.y - margin, w: item.w + 2 * margin, h: item.h + 2 * margin,
        kind: TIP_FALLBACK[item.kind] ? item.kind : 'text'});
    const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
    const covers = (left, top) => {
      const out = {text: 0, soft: 0, weighted: 0};
      for (const item of obstacles) {
        const w = Math.min(left + width, item.x + item.w) - Math.max(left, item.x);
        if (w <= 0) continue;
        const h = Math.min(top + height, item.y + item.h) - Math.max(top, item.y);
        if (h <= 0) continue;
        if (item.soft) out.soft += w * h * item.soft;
        else { out.text += w * h; out.weighted += item.w * item.h * TIP_FALLBACK[item.kind]; }
      }
      return out;
    };
    let clear = null, fallback = null;
    const seen = new Set();
    const consider = (rawLeft, rawTop) => {
      const left = Math.round(clamp(rawLeft, 4, maxLeft)), top = Math.round(clamp(rawTop, minTop, maxTop));
      const key = left + ',' + top;
      if (seen.has(key)) return;
      seen.add(key);
      if (x > left - 6 && x < left + width + 6 && y > top - 6 && y < top + height + 6) return;
      const dx = x < left ? left - x : x > left + width ? x - left - width : 0;
      const dy = y < top ? top - y : y > top + height ? y - top - height : 0;
      const cover = covers(left, top);
      const cost = Math.hypot(dx, dy) + cover.soft + (top + height <= y ? 0 : 3);
      if (cover.text <= 0) { if (!clear || cost < clear.cost) clear = {left, top, cover: 0, cost}; return; }
      const price = cost + cover.weighted;
      if (!fallback || price < fallback.price) fallback = {left, top, cover: cover.text, price};
    };
    // The four spots the tooltip took before lane G1, then the sweep about the point.
    for (const [left, top] of [[x + 14, y - gap - height], [x - 14 - width, y - gap - height], [x + 14, y + 14], [x - 14 - width, y + 14]])
      consider(left, top);
    for (let top = y - gap - height - reach; top <= y + gap + reach; top += 6)
      for (let left = x - 14 - width - reach; left <= x + 14 + reach; left += 8) consider(left, top);
    // The sweep's 8 x 6 px grid, refined to the pixel about the spot it chose: the edges settle in the
    // gaps between labels, and a clear spot comes as near the point as it can.
    const chosen = clear || fallback;
    if (chosen)
      for (let top = chosen.top - 6; top <= chosen.top + 6; top += 1)
        for (let left = chosen.left - 8; left <= chosen.left + 8; left += 1) consider(left, top);
    const best = clear || fallback;
    return best ? {left: best.left, top: best.top, cover: best.cover}
      : {left: clamp(x + 14, 4, maxLeft), top: clamp(y + 14, minTop, maxTop), cover: null};
  }

  /* ----------------------------------------------- zoom, pins and the strip (lane Z, 2026-10-15) */
  // The path of a focus zoom, shared by PAC evidence's Flow and Graph's Follow the money (the re-root
  // stack the evidence Flow has always kept, now one thing): each step is a level the reader left, with
  // what is needed to return to it without asking the server again.
  function trail() {
    const steps = [];
    return {
      steps,
      push(step) { steps.push(step); },
      back() { return steps.pop(); },
      jump(index) { const step = steps[index]; steps.length = Math.max(0, index); return step; },
      clear() { steps.length = 0; },
      depth: () => steps.length,
      labels: current => steps.map(step => step.label).concat(current ? [current] : [])
    };
  }

  // "Banking > ActBlue > DCCC": every step before the last is a button back to it, with a Back button
  // to the previous level. handlers: {onJump(index), onBack()}. An empty trail hides the line.
  function crumbs(element, path, current, handlers) {
    const doc = element.ownerDocument;
    element.replaceChildren();
    element.hidden = !path.steps.length;
    if (!path.steps.length) return;
    const list = doc.createElement('ol');
    list.className = 'flow-trail';
    list.setAttribute('aria-label', 'Zoom path');
    path.steps.forEach((step, index) => {
      const item = doc.createElement('li');
      const button = doc.createElement('button');
      button.type = 'button';
      button.className = 'link-button';
      button.dataset.jump = String(index);
      button.textContent = step.label;
      button.addEventListener('click', () => handlers.onJump(index));
      item.appendChild(button);
      list.appendChild(item);
    });
    const here = doc.createElement('li');
    here.setAttribute('aria-current', 'location');
    here.textContent = current;
    list.appendChild(here);
    const back = doc.createElement('button');
    back.type = 'button';
    back.className = 'secondary flow-trail-back';
    back.dataset.back = '1';
    back.textContent = `← Back to ${path.steps[path.steps.length - 1].label}`;
    back.addEventListener('click', () => handlers.onBack());
    element.append(list, back);
  }

  // One request at a time per query, kept for a few minutes: a first read of an industry builds it from
  // the saved reports (30 s to a minute and more), and a control changed meanwhile used to throw that
  // answer away and ask again. Failures are not kept.
  const READ_KEEP = 8, READ_MS = 5 * 60 * 1000;
  const reads = new Map(), pending = new Map();
  function read(url, fetcher) {
    const kept = reads.get(url);
    if (kept && Date.now() - kept.at < READ_MS) return Promise.resolve(kept.value);
    if (pending.has(url)) return pending.get(url);
    const run = Promise.resolve().then(() => (fetcher || root.fetch.bind(root))(url, {headers: {Accept: 'application/json'}}))
      .then(async response => ({ok: response.ok, status: response.status, body: await response.json()}))
      .catch(error => ({ok: false, status: 0, body: {error: 'The flow could not be read: ' + error.message}}))
      .then(value => {
        pending.delete(url);
        if (value.ok) {
          reads.set(url, {at: Date.now(), value});
          while (reads.size > READ_KEEP) reads.delete(reads.keys().next().value);
        }
        return value;
      });
    pending.set(url, run);
    return run;
  }

  // "Reading the flow... 12 s", and after a while why it is slow. Returns stop().
  function waiting(set, words) {
    const begun = Date.now();
    set(`${words}…`);
    const timer = setInterval(() => {
      const seconds = Math.round((Date.now() - begun) / 1000);
      set(`${words}… ${seconds} s` + (seconds >= 8
        ? ' · the first read builds the flow from the saved reports and can take a minute or more; later reads are quick' : ''));
    }, 1000);
    return () => clearInterval(timer);
  }

  // One read at a time per view. begin() ends the one before it - its timer stops, its answer is dropped - and
  // returns {live(), end()}: live() is false once a later begin() or cancel() has replaced this read (or the
  // view was left, Escape, Back), so a slow read that finishes late neither writes the status line nor the chart.
  // The status setter is guarded by the same test, so a tick already queued cannot write over a newer answer.
  function loader() {
    let ticket = 0, stop = null;
    const cancel = () => { ticket += 1; if (stop) { stop(); stop = null; } };
    return {
      cancel,
      begin(set, words) {
        cancel();
        const mine = ticket;
        const own = stop = waiting(text => { if (mine === ticket) set(text); }, words);
        return {live: () => mine === ticket, end() { if (mine === ticket) { own(); stop = null; } }};
      }
    };
  }

  // The chain behind a pinned node, step by step: its largest way in and its largest way out, each hop
  // with the amount of that one step. Nothing is summed or carried along a chain (pooled money is not
  // traced money), and a chain never crosses the root committee's pool: a candidate's chain ends at the
  // pool that paid it, a donor's at the pool it paid.
  function chainOf(result, id) {
    const byId = new Map(result.nodes.map(node => [node.id, node]));
    const start = byId.get(id);
    if (!start) return [];
    const pool = result.nodes.find(node => node.root && !node.stub);
    const into = new Map(), out = new Map();
    for (const link of result.links) {
      const source = byId.get(link.source), target = byId.get(link.target);
      if (!source || !target || source.stub || target.stub) continue;
      if (!into.has(link.target)) into.set(link.target, []);
      if (!out.has(link.source)) out.set(link.source, []);
      into.get(link.target).push(link);
      out.get(link.source).push(link);
    }
    const largest = list => (list || []).slice().sort((a, b) => b.cents - a.cents || (a.id < b.id ? -1 : 1))[0];
    const upLimit = pool && start.stage > pool.stage ? pool.id : null;
    const downLimit = pool && start.stage < pool.stage ? pool.id : null;
    const before = [], after = [];
    let seen = new Set([id]);
    for (let at = start, link; at.id !== upLimit && (link = largest(into.get(at.id))) && !seen.has(link.source) && before.length < 8;) {
      seen.add(link.source);
      before.unshift({node: byId.get(link.source), via: link});
      at = byId.get(link.source);
    }
    seen = new Set([id]);
    for (let at = start, link; at.id !== downLimit && (link = largest(out.get(at.id))) && !seen.has(link.target) && after.length < 8;) {
      seen.add(link.target);
      after.push({node: byId.get(link.target), via: link});
      at = byId.get(link.target);
    }
    // each hop carries the link that leads INTO its node from the one before it
    const hops = [];
    before.forEach((hop, at) => hops.push({node: hop.node, via: at === 0 ? null : before[at - 1].via}));
    hops.push({node: start, via: before.length ? before[before.length - 1].via : null});
    after.forEach(hop => hops.push({node: hop.node, via: hop.via}));
    return hops;
  }

  // Pin colours: five slots of the categorical palette that no ribbon colours by default.
  const PIN_SLOTS = [3, 6, 7, 9, 4];
  const MAX_PINS = 5;
  const pinColour = (at, colours) => (colours || palette())[PIN_SLOTS[at % PIN_SLOTS.length]] || NEUTRAL;

  // A node's wording, for the pins and the notices.
  const nameOf = node => (node ? nodeTitle(node) : '');
  const possess = name => (/s$/i.test(name) ? `${name}'` : `${name}'s`);

  // The committees that pool: the root's own pool, and any committee that takes money in from more than one
  // source in the chart (a second company or committee, an "Other donors" or "cash from before" stub). What leaves
  // one is its spending out of the pool, not any one source's money.
  function pools(result) {
    const sources = new Map();
    for (const link of result.links) {
      if (!sources.has(link.target)) sources.set(link.target, new Set());
      sources.get(link.target).add(link.source);
    }
    return new Set(result.nodes.filter(node => node.kind === 'committee' && !node.stub
      && (node.root || (sources.get(node.id) || []).size > 1)).map(node => node.id));
  }

  // The route a pinned node lights (the one a hover lights), with each of its ribbons told apart: 'traced', the
  // pin's own money, from the pin out to and into the first pooling committee in either direction; and 'pooled',
  // that committee's own spending (or, upstream of a pinned outcome, what filled its pool), drawn but never counted
  // as the pin's. A node stays open (traced onward) only while every ribbon that reaches it is traced and it does not
  // pool, so no unbroken chain runs through a pool. Stubs are not steps and are left out.
  function pinRoute(result, id) {
    const kinds = new Map();
    const start = result.nodes.find(node => node.id === id);
    if (!start) return {kinds, nodes: new Set()};
    const lit = traced(result, id);
    const pool = pools(result);
    const byId = new Map(result.nodes.map(node => [node.id, node]));
    const own = result.links.filter(link => lit.links.has(link.id) && !byId.get(link.source).stub && !byId.get(link.target).stub);
    const ids = new Set([id]);
    for (const link of own) { ids.add(link.source); ids.add(link.target); }
    for (const way of [{near: 'source', far: 'target', sign: 1}, {near: 'target', far: 'source', sign: -1}]) {
      const edges = new Map();
      for (const link of own) {
        if (!edges.has(link[way.near])) edges.set(link[way.near], []);
        edges.get(link[way.near]).push(link);
      }
      const heard = new Map();                           // node -> how many traced / pooled ribbons reached it
      const open = node => node === id || (heard.has(node) && heard.get(node).pooled === 0 && heard.get(node).traced > 0 && !pool.has(node));
      const order = [...ids].sort((a, b) => way.sign * (byId.get(a).stage - byId.get(b).stage));
      for (const node of order) {
        if (node !== id && !heard.has(node)) continue;       // not reached from the pin in this direction
        for (const link of edges.get(node) || []) {
          if (kinds.has(link.id)) continue;
          const kind = open(node) ? 'traced' : 'pooled';
          kinds.set(link.id, kind);
          const far = heard.get(link[way.far]) || {traced: 0, pooled: 0};
          far[kind] += 1;
          heard.set(link[way.far], far);
        }
      }
    }
    return {kinds, nodes: lit.nodes};
  }

  // The panel's account of one pin: its chain (chainOf) cut into blocks, in the order money moves. 'traced' is the
  // pin's own money; 'pooled' is a pool's spending out of (or receipts into) its pool, headed as all donors', with
  // each step its own amount. A pooling committee shows its own total, never a donor's share of it. Every step and
  // the node it enters sit in the block of that step, so no downstream amount can land in a donor's block.
  function pinBlocks(result, id, format) {
    const money = (format && format.short) || short;
    const hops = chainOf(result, id);
    const { kinds } = pinRoute(result, id);
    const pool = pools(result);
    const blocks = [];
    const rowOf = hop => {
      const pooled = pool.has(hop.node.id);
      return {kind: 'node', node: hop.node, pin: hop.node.id === id, pool: pooled,
        amount: pooled ? hop.node.value : (hop.node.amount != null ? hop.node.amount : hop.node.value)};
    };
    hops.forEach((hop, at) => {
      if (!at) { if (hops.length === 1) blocks.push({kind: 'traced', rows: [rowOf(hop)], steps: []}); return; }
      const kind = kinds.get(hop.via.id) || 'pooled';
      let block = blocks[blocks.length - 1];
      if (!block || block.kind !== kind) {
        block = {kind, rows: [], steps: []};
        blocks.push(block);
        if (blocks.length === 1) block.rows.push(rowOf(hops[0]));
      }
      block.rows.push({kind: 'step', via: hop.via, amount: hop.via.value}, rowOf(hop));
      block.steps.push({from: hops[at - 1].node, to: hop.node, via: hop.via});
    });
    const middle = blocks.findIndex(block => block.kind === 'traced');
    blocks.forEach((block, at) => {
      const first = block.steps[0], last = block.steps[block.steps.length - 1];
      if (block.kind === 'pooled') {
        block.head = at < middle ? `What reached ${nameOf(last.to)} (pooled, all donors)`
          : `${possess(nameOf(first.from))} spending from its pool (all donors)`;
      } else {
        const heads = [];
        if (at > 0 && last) heads.push(`From ${nameOf(first.from)} into ${nameOf(first.to)}: ${money(first.via.value)}`);
        if (at < blocks.length - 1 && last && !(heads.length && block.steps.length === 1))
          heads.push(`Into ${nameOf(last.to)} from ${nameOf(last.from)}: ${money(last.via.value)}`);
        block.head = heads.join(' · ');
      }
    });
    return {hops, blocks};
  }

  // The strip of the level the reader came from: its columns up to the node they clicked, narrow and
  // faded, the clicked node marked. It is drawn from the answer that level showed (its arranged
  // payload), so it is what they saw, not a second reading of the data.
  function strip(context, options, height) {
    const api = layoutApi();
    const before = context.payload;
    const clicked = before.nodes.find(node => node.id === context.nodeId);
    if (!clicked) return null;
    const width = options.stripWidth;
    const keep = new Set(before.nodes.filter(node => !node.stub && node.stage <= clicked.stage).map(node => node.id));
    const slice = {stages: before.stages.slice(0, clicked.stage + 1), nodes: before.nodes.filter(node => keep.has(node.id)),
      links: before.links.filter(link => keep.has(link.source) && keep.has(link.target))};
    const drawn = api.layout(slice, {width: width - 14, height, nodeWidth: 7, labelWidth: 0, labelLine: 0, subLine: 0, labelLines: 1,
      amounts: false, minPadding: 3, maxPadding: 12, measure: () => 0, margin: {top: 34, right: 4, bottom: 10, left: 4}});
    const colours = api.colours(drawn, options.palette || palette(), options.neutral || NEUTRAL);
    const byId = new Map(drawn.nodes.map(node => [node.id, node]));
    const parts = [`<g class="flow-context" aria-hidden="true" data-context="${escape(context.nodeId)}">`];
    drawn.links.forEach(link => {
      const source = byId.get(link.source);
      parts.push(`<path class="flow-ctx-link" d="${link.path}" fill="${colours.get(source.id)}" fill-opacity="0.16"/>`);
    });
    for (const node of drawn.nodes) {
      if (node.stub) continue;
      const focus = node.id === context.nodeId;
      parts.push(`<rect class="flow-ctx-bar${focus ? ' flow-ctx-focus' : ''}" x="${f(node.x0)}" y="${f(node.y0)}" width="${f(node.x1 - node.x0)}"`
        + ` height="${f(Math.max(node.y1 - node.y0, 2))}" fill="${colours.get(node.id)}" fill-opacity="${focus ? 1 : 0.4}"/>`);
    }
    // No column headers on the strip: they sat beside the main chart's own and ran into them ("FIRS…RECEIVED
    // FROM"). The breadcrumb above names the level the strip is.
    parts.push('</g>');
    return {width, markup: parts.join('')};
  }

  // A small drawing of the original chart with the nodes the reader zoomed through marked: where this
  // focus sits in the whole. marks: {ids: [node ids, the last the current one]}.
  function minimap(payload, marks, options) {
    options = options || {};
    const api = layoutApi();
    const width = options.width || 214, height = options.height || 100;
    const arranged = api.arrange(payload, {small: true}).payload;
    const drawn = api.layout(arranged, {width, height, nodeWidth: 5, labelWidth: 0, labelLine: 0, subLine: 0, labelLines: 1, amounts: false,
      minPadding: 2, maxPadding: 7, measure: () => 0, margin: {top: 4, right: 4, bottom: 4, left: 4}});
    const marked = new Map();                  // a node inside a group is marked as the group that holds it
    for (const id of (marks && marks.ids) || []) {
      const node = drawn.nodes.find(item => item.id === id)
        || drawn.nodes.find(item => Array.isArray(item.members) && item.members.some(member => member.id === id));
      if (node) marked.set(node.id, id === (marks.ids || [])[(marks.ids || []).length - 1]);
    }
    const parts = [`<svg xmlns="http://www.w3.org/2000/svg" class="flow-minimap-svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"`
      + ' role="img" aria-label="Where this focus sits in the original chart">'];
    for (const link of drawn.links) parts.push(`<path class="flow-mini-link" d="${link.path}"/>`);
    for (const node of drawn.nodes) {
      if (node.stub) continue;
      const mark = marked.has(node.id);
      parts.push(`<rect class="flow-mini-bar${mark ? ' flow-mini-mark' : ''}${marked.get(node.id) ? ' flow-mini-current' : ''}" x="${f(node.x0)}"`
        + ` y="${f(node.y0 - (mark ? 1.5 : 0))}" width="${f(node.x1 - node.x0)}" height="${f(Math.max(node.y1 - node.y0, 2) + (mark ? 3 : 0))}"/>`);
    }
    parts.push('</svg>');
    return parts.join('');
  }

  /* ------------------------------------------------------------------ mount */
  // options.explore turns on what the two Flow views of lane Z add to the hover: unfolding a group in
  // place, pinning a route, the onward markers and the gesture hints. Chart Studio's flow leaves it off.
  // The reader's own state (which groups are unfolded, which nodes are pinned) is options.view, an object
  // the chart writes into and the view keeps, so a redraw (a resize, a colour) returns to where it was.
  let mounted = 0;
  function mount(host, payload, options) {
    mounted += 1;
    options = Object.assign({ribbons: 'gradient', opacity: 0.4, idPrefix: `flow${mounted}`, more: 8}, options || {});
    const state = {payload, options, context: null, focus: null, full: options.full || null, loading: false, menu: false, notice: 0,
      view: options.view || {}};
    state.view.open = state.view.open || {};
    state.view.pins = state.view.pins || [];
    state.view.only = Boolean(state.view.only);
    let prune = Boolean(options.prunePins);
    const doc = host.ownerDocument;
    const tip = doc.createElement('div');
    tip.className = 'flow-tooltip';
    tip.hidden = true;
    tip.setAttribute('role', 'status');
    const explore = () => Boolean(state.options.explore);

    /* ------------------------------------------------------- a candidate's own spending (card and panel) */
    // The campaign's spending is the campaign's, never this flow's dollars. The card is a section of the tooltip; a click
    // opens the panel docked beside the chart (state.options.spendingPanel); Shift-click still pins. Both need the cycle
    // the flow was drawn for, and the module, which loads on the first hover.
    let spend = null, lastPlace = null;
    const cycleOf = () => Number(state.payload && state.payload.cycle);
    // The other candidates of the same office the chart draws: the only campaigns a comparison may read.
    const peersOf = node => [...new Set(state.context.result.nodes.filter(item => spendable(item, cycleOf())
      && item.candidate_id !== node.candidate_id && String(item.candidate_id)[0] === String(node.candidate_id)[0])
      .map(item => item.candidate_id))];
    let destroyed = false;
    const spendingEnabled = () => explore() && spendingOn(state.options);      // off on the hosted console, and where a view turns it off
    function spending() {
      if (spend || destroyed || !spendingEnabled()) return spend;
      const api = spendModule();
      if (!api) return null;
      spend = api.controller(host, {fetcher: state.options.spendFetcher, panel: state.options.spendingPanel || null, view: state.view,
        link: url => (root.MentatScope && root.MentatScope.fecLink ? root.MentatScope.fecLink(url) : ''),
        onRows: state.options.onCandidate, peersOf,
        // closing the panel returns focus to the node that opened it, if it is still drawn (the controller falls back to the chart)
        restoreFocus: node => {
          const group = host.querySelector(`.flow-node[data-node="${CSS.escape(node.id)}"]`);
          if (group) focusNode(node.id);
          return Boolean(group);
        },
        after: () => { if (!tip.hidden && lastPlace) place(lastPlace.anchor, lastPlace.point); }});
      return spend;
    }
    function openSpend(node) {
      if (!spendingEnabled() || !state.options.spendingPanel || !spendable(node, cycleOf())) return false;
      const ready = spending();
      if (ready) { hide(); return ready.open(node, cycleOf()); }
      spendLater(doc, () => !destroyed, () => { const made = spending(); if (made) made.open(node, cycleOf()); });
      return true;
    }

    function draw() {
      const total = options.width || Math.max(640, Math.floor(host.clientWidth || 1100));
      const height = options.height || Math.max(360, Math.floor(host.clientHeight || 520));
      const stripWidth = state.options.context ? Math.min(190, Math.max(112, Math.round(total * 0.15))) : 0;
      const settings = Object.assign({}, state.options, {width: total, height, stripWidth,
        // the node the view is about (options.focus) and the pinned ones are never folded into a bucket
        arrange: explore() ? {small: true, open: state.view.open, full: state.full,
          keep: [state.options.focus].concat(state.view.pins).filter(Boolean)} : null});
      if (!stripWidth) delete settings.context;
      state.context = prepare(state.payload, Object.assign({}, settings, {width: total - stripWidth}));
      // A group the answer cannot unfold honestly stays folded and says why, beside its node.
      const refused = Object.entries(state.context.refused || {});
      if (refused.length) {
        for (const [id] of refused) delete state.view.open[id];
        draw();
        for (const [id, why] of refused) notice(id, `Can’t unfold: ${why}`);
        return;
      }
      host.innerHTML = svgString(state.payload, Object.assign({}, settings, {prepared: state.context}));
      const problems = state.context.problems;
      if (problems.length) {                // the answer does not conserve: drawn as received, and it says so
        const note = doc.createElement('p');
        note.className = 'flow-contract-notice';
        note.setAttribute('role', 'alert');
        note.textContent = `This answer has ${problems.length} problem${problems.length === 1 ? '' : 's'} with its numbers`
          + ` (${problems[0]}${problems.length > 1 ? ', …' : ''}); it is drawn as received, with nothing folded or unfolded.`;
        host.insertBefore(note, host.firstChild);
      }
      host.appendChild(tip);
      if (prune) {                          // a zoom keeps the pins whose nodes are still drawn
        prune = false;
        state.view.pins = state.view.pins.filter(id => find(id));
      }
      applyPins();
      // The panel the reader had open comes back after a redraw (a resize), for the candidate if it is still drawn.
      if (state.view.spend && spendingEnabled() && state.options.spendingPanel) {
        const ready = spending();
        if (ready) ready.restore(id => state.context.result.nodes.find(item => spendable(item, cycleOf()) && item.candidate_id === id));
      }
      if (state.context.needs.length) wantFull(state.context.needs);
    }

    // Unfolding a group the answer built needs every member's own links: the same query at top=0.
    function wantFull(ids) {
      if (state.loading) return;
      if (!state.options.loadFull) { for (const id of ids) fail(id, 'this answer does not carry its members'); return; }
      state.loading = true;
      host.classList.add('is-loading');
      Promise.resolve().then(() => state.options.loadFull()).then(full => {
        state.loading = false; host.classList.remove('is-loading');
        state.full = full;
        if (state.options.onFull) state.options.onFull(full);
        draw();
      }, error => {
        state.loading = false; host.classList.remove('is-loading');
        for (const id of ids) fail(id, (error && error.message) || 'The members could not be read.');
      });
    }

    function fail(id, why) {
      delete state.view.open[id];
      draw();
      notice(id, `Can’t unfold: ${why}`);
    }

    const find = id => (state.context ? state.context.result.nodes.find(node => node.id === id) : null);
    const findLink = id => state.context.result.links.find(link => link.id === id);

    /* ------------------------------------------------------------- the lit route */
    function light(start) {
      const svg = host.querySelector('svg.flow-chart');
      if (!svg) return;
      for (const chip of svg.querySelectorAll('.flow-onward.is-lit')) chip.classList.remove('is-lit');
      if (!start) { svg.classList.remove('is-tracing'); for (const item of svg.querySelectorAll('.is-lit')) item.classList.remove('is-lit'); return; }
      const lit = traced(state.context.result, start);
      svg.classList.add('is-tracing');
      for (const path of svg.querySelectorAll('.flow-link')) path.classList.toggle('is-lit', lit.links.has(path.dataset.link));
      for (const group of svg.querySelectorAll('.flow-node')) group.classList.toggle('is-lit', lit.nodes.has(group.dataset.node));
      // The onward marker of the node hovered (or of the node a hovered stub ribbon leaves).
      const link = findLink(start);
      const owner = link ? (find(link.target) && find(link.target).stub ? find(link.target).attach : link.source) : start;
      for (const chip of svg.querySelectorAll('.flow-onward')) chip.classList.toggle('is-lit', chip.dataset.onward === owner);
    }

    /* --------------------------------------------------------------------- pins */
    const pinned = id => state.view.pins.includes(id);
    function togglePin(id) {
      if (!explore()) return;
      const node = find(id);
      if (!node || node.stub) return;
      if (pinned(id)) state.view.pins = state.view.pins.filter(item => item !== id);
      else if (state.view.pins.length >= MAX_PINS) { notice(id, `Up to ${MAX_PINS} pins: unpin one first.`); return; }
      else state.view.pins.push(id);
      applyPins();
    }

    // Each pin's route stays lit in its own colour; with "Show only linked" the rest fades. A pinned
    // node's route is the one a hover lights, so pooled money is not traced past the root's pool. A pinned
    // route is in its colour only up to and into the first committee that pools money from more than one
    // source (pinRoute): what that committee spends out of its pool is drawn hatched with a dashed edge, not as
    // the pin's money.
    function applyPins() {
      const svg = host.querySelector('svg.flow-chart');
      if (!svg || !state.context) return;
      const result = state.context.result;
      const colours = palette();
      const hatch = `url(#${String(state.options.idPrefix || 'flow').replace(/[^A-Za-z0-9_-]/g, '')}-hatch)`;
      for (const old of svg.querySelectorAll('.flow-pin')) old.remove();
      for (const path of svg.querySelectorAll('.flow-link.is-pinned, .flow-link.is-pooled')) {
        path.classList.remove('is-pinned', 'is-pooled');
        if (path.dataset.fill) { path.setAttribute('fill', path.dataset.fill); delete path.dataset.fill; }
        path.removeAttribute('data-pin');
        path.removeAttribute('stroke');
      }
      for (const group of svg.querySelectorAll('.flow-node.is-pinned, .flow-node.is-routed')) group.classList.remove('is-pinned', 'is-routed');
      const live = state.view.pins.filter(id => find(id));
      const claimed = new Map(), pooled = new Map();
      live.forEach((id, at) => {
        const route = pinRoute(result, id);          // no stub is a step of a route; stubs stay as drawn
        for (const [link, kind] of route.kinds) {
          const into = kind === 'traced' ? claimed : pooled;
          if (!into.has(link)) into.set(link, at);
        }
        for (const group of svg.querySelectorAll('.flow-node')) if (route.nodes.has(group.dataset.node)) group.classList.add('is-routed');
      });
      for (const path of svg.querySelectorAll('.flow-link')) {
        const link = path.dataset.link;
        const mine = claimed.get(link), spending = pooled.get(link);
        if (mine === undefined && spending === undefined) continue;
        path.dataset.fill = path.getAttribute('fill');
        if (mine !== undefined) {
          path.classList.add('is-pinned');
          path.dataset.pin = String(mine);
          path.setAttribute('fill', pinColour(mine, colours));
        } else {
          path.classList.add('is-pooled');
          path.dataset.pin = String(spending);
          path.setAttribute('fill', hatch);
          path.setAttribute('stroke', pinColour(spending, colours));
        }
      }
      live.forEach((id, at) => {
        const group = svg.querySelector(`.flow-node[data-node="${CSS.escape(id)}"]`);
        const node = find(id);
        if (!group || !node) return;
        group.classList.add('is-pinned');
        const mark = doc.createElementNS('http://www.w3.org/2000/svg', 'g');
        mark.setAttribute('class', 'flow-pin');
        mark.setAttribute('aria-hidden', 'true');
        const x = node.x0 - 1, y = node.y0 - 3;
        const dot = doc.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('cx', f(x)); dot.setAttribute('cy', f(y - 5)); dot.setAttribute('r', '4.5');
        dot.setAttribute('fill', pinColour(at, colours));
        const stem = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
        stem.setAttribute('d', `M${f(x)},${f(y - 1)}L${f(x)},${f(y + 5)}`);
        stem.setAttribute('stroke', pinColour(at, colours));
        stem.setAttribute('stroke-width', '1.6');
        mark.append(dot, stem);
        group.appendChild(mark);
        const bar = group.querySelector('.flow-bar');
        if (bar) bar.setAttribute('stroke', pinColour(at, colours));
      });
      for (const group of svg.querySelectorAll('.flow-node:not(.is-pinned) .flow-bar[stroke]:not([stroke-dasharray])')) {
        if (!group.getAttribute('fill').startsWith('url(')) group.removeAttribute('stroke');
      }
      svg.classList.toggle('has-pins', live.length > 0);
      svg.classList.toggle('is-linked-only', live.length > 0 && state.view.only);
      renderPanel(live);
    }

    function renderPanel(live) {
      const panel = state.options.pinPanel;
      if (!panel) return;
      panel.replaceChildren();
      panel.hidden = !live.length;
      if (!live.length) return;
      const colours = palette();
      const result = state.context.result;
      const format = state.context.format;
      const head = doc.createElement('div');
      head.className = 'flow-pins-head';
      const title = doc.createElement('h3');
      title.className = 'flow-rail-title';
      title.textContent = 'Linked units';
      const toggle = doc.createElement('label');
      toggle.className = 'flow-only';
      const box = doc.createElement('input');
      box.type = 'checkbox';
      box.checked = state.view.only;
      box.addEventListener('change', () => {
        state.view.only = box.checked;
        const svg = host.querySelector('svg.flow-chart');
        if (svg) svg.classList.toggle('is-linked-only', state.view.only);
      });
      toggle.append(box, doc.createTextNode(' Show only linked'));
      head.append(title, toggle);
      panel.appendChild(head);
      live.forEach((id, at) => {
        const {hops, blocks} = pinBlocks(result, id, format);
        const node = find(id);
        const card = doc.createElement('section');
        card.className = 'flow-pin-card';
        card.dataset.pin = String(at);
        const header = doc.createElement('div');
        header.className = 'flow-pin-head';
        const swatch = doc.createElement('span');
        swatch.className = 'flow-pin-swatch';
        swatch.style.background = pinColour(at, colours);
        const name = doc.createElement('strong');
        name.textContent = `Pin ${at + 1} — ${nameOf(node)}`;
        const gone = doc.createElement('button');
        gone.type = 'button';
        gone.className = 'flow-pin-x';
        gone.dataset.unpin = id;
        gone.setAttribute('aria-label', `Unpin ${nameOf(node)}`);
        gone.textContent = '×';
        gone.addEventListener('click', () => togglePin(id));
        header.append(swatch, name, gone);
        card.appendChild(header);
        for (const block of blocks) {
          const part = doc.createElement('div');
          part.className = `flow-pin-block is-${block.kind}`;
          part.dataset.block = block.kind;
          if (block.head) {
            const head = doc.createElement('p');
            head.className = 'flow-pin-block-head';
            head.textContent = block.head;
            part.appendChild(head);
          }
          const list = doc.createElement('ol');
          list.className = 'flow-pin-path';
          for (const row of block.rows) {
            const item = doc.createElement('li');
            if (row.kind === 'step') {
              item.className = 'flow-pin-step';
              item.textContent = `↓ ${format.short(row.amount)} this step`;
            } else {
              item.className = 'flow-pin-node' + (row.pin ? ' is-pin' : '');
              const label = doc.createElement('span');
              label.textContent = nameOf(row.node);
              const amount = doc.createElement('span');
              amount.className = 'flow-pin-amount';
              // a pooling committee's own total, all donors', never a share of it
              amount.textContent = (row.pool ? 'pool ' : '') + format.short(row.amount);
              if (row.pool) amount.title = `${nameOf(row.node)}: the whole pool, all donors`;
              item.append(label, amount);
            }
            list.appendChild(item);
          }
          part.appendChild(list);
          card.appendChild(part);
        }
        const end = hops[hops.length - 1];
        if (end && end.node.kind === 'candidate' && state.options.onCandidate) {
          const rows = doc.createElement('button');
          rows.type = 'button';
          rows.className = 'secondary flow-pin-rows';
          rows.textContent = 'Open in Rows';
          rows.addEventListener('click', () => state.options.onCandidate(end.node));
          card.appendChild(rows);
        }
        panel.appendChild(card);
      });
      const note = doc.createElement('p');
      note.className = 'flow-pin-note';
      note.textContent = 'Amounts are per step; pooled money is not traced to a company.';
      panel.appendChild(note);
    }

    /* ----------------------------------------------------------------- notices */
    // A short word on or beside a node (a drill that found nothing, a group that cannot unfold): the chart
    // stays as it is and usable. Returns false when the node is not drawn, so the view can say it elsewhere.
    function notice(id, text) {
      clearNotice();
      const svg = host.querySelector('svg.flow-chart');
      const node = find(id);
      if (!svg || !node) return false;
      const made = svg.querySelector('.flow-main') || svg;
      const measure = measurer(state.options);
      const width = Math.ceil(measure(text, 12)) + 18;
      const x = Math.min(node.x1 + 6, Math.max(4, (state.context.result.width || width) - width - 4));
      const y = Math.max(20, node.y0 - 26);
      const group = doc.createElementNS('http://www.w3.org/2000/svg', 'g');
      group.setAttribute('class', 'flow-notice');
      group.setAttribute('role', 'status');
      group.innerHTML = `<rect x="${f(x)}" y="${f(y)}" width="${width}" height="22" rx="5"/>`
        + `<text x="${f(x + 9)}" y="${f(y + 15)}">${escape(text)}</text>`;
      made.appendChild(group);
      state.notice = setTimeout(clearNotice, 9000);
      return true;
    }
    function clearNotice() {
      clearTimeout(state.notice);
      for (const old of host.querySelectorAll('.flow-notice')) old.remove();
    }

    /* ------------------------------------------------------------------- tooltip */
    // FL5, lanes G1/G1b (board #44): the tooltip sits by the hovered point (by a focused node's bar)
    // in the free space around it: placeTip weighs spots about the point by what they would cover - a
    // label, a column header, a legend (each with 4 px to spare), a bar, the lit route's ribbons - and takes
    // the nearest spot that covers no words; where none is near, it stays by the point and spares the
    // headers and the words and ribbons of the route the hover lit (light() has marked it). It never
    // reaches above the drawing (the title and subtitle sit above it) and keeps to the part of the drawing
    // on screen.
    function place(anchor, point) {
      const box = host.getBoundingClientRect();
      const scrollX = host.scrollLeft || 0, scrollY = host.scrollTop || 0;
      const svg = host.querySelector('svg.flow-chart');
      const rectOf = (item, soft, kind) => {
        const at = item.getBoundingClientRect();
        return {x: at.left - box.left, y: at.top - box.top, w: at.width, h: at.height, soft, kind};
      };
      const words = item => rectOf(item, 0, item.classList.contains('flow-header') ? 'header'
        : item.closest('.flow-node.is-lit') ? 'route' : 'text');
      const obstacles = svg ? [
        ...[...svg.querySelectorAll('.flow-label, .flow-amount, .flow-subline, .flow-header, .flow-legend-label, .flow-legend-swatch, .flow-party')]
          .map(words),
        ...[...svg.querySelectorAll('.flow-bar')].map(item => rectOf(item, 0.1))] : [];
      // The lit route's ribbons, sampled along both long edges and the middle: a spot that would hide the
      // route the reader is tracing costs a px of distance per px^2 of the samples it covers. They are not
      // words, so a spot over them still counts as free space.
      const View = doc.defaultView;
      for (const path of svg && View && View.DOMPoint ? svg.querySelectorAll('.flow-link.is-lit') : []) {
        const length = typeof path.getTotalLength === 'function' ? path.getTotalLength() : 0;
        const matrix = length && typeof path.getScreenCTM === 'function' ? path.getScreenCTM() : null;
        if (!matrix) continue;
        for (let step = 0; step <= 12; step += 1) {
          const a = path.getPointAtLength(length * step / 24), b = path.getPointAtLength(length * (1 - step / 24));
          for (const p of [a, b, {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}]) {
            const at = new View.DOMPoint(p.x, p.y).matrixTransform(matrix);
            obstacles.push({x: at.x - box.left - 3, y: at.y - box.top - 3, w: 6, h: 6, soft: 1});
          }
        }
      }
      let x, y;
      if (point) { x = point.x - box.left; y = point.y - box.top; }
      else {
        const bar = rectOf((anchor.querySelector && anchor.querySelector('.flow-bar')) || anchor, 0, 'route');
        x = bar.x + bar.w; y = bar.y + Math.min(bar.h / 2, 14);
        obstacles.push(bar);
      }
      const width = tip.offsetWidth, height = tip.offsetHeight;
      const room = host.clientWidth || box.width;
      const tall = Math.max(host.clientHeight || box.height, height + 8);
      const view = doc.defaultView;
      const top = Math.max(4, 4 - box.top);
      const onScreen = view && view.innerHeight ? view.innerHeight - box.top : tall;
      const bottom = Math.min(tall, Math.max(onScreen, top + height + 8));
      const spot = placeTip({x, y, width, height, room, top, bottom, obstacles});
      tip.style.transform = 'none';
      tip.style.left = `${Math.round(spot.left + scrollX)}px`;
      tip.style.top = `${Math.round(spot.top + scrollY)}px`;
    }

    const hintsOf = () => (explore() ? {zoom: Boolean(state.options.onReroot), rows: Boolean(state.options.onCandidate),
      spend: Boolean(state.options.spendingPanel) && spendingEnabled(), cycle: cycleOf()} : null);

    function show(target, anchor, point) {
      const text = describe(target, state.context, state.payload, hintsOf());
      tip.replaceChildren();
      tip.classList.remove('has-spend');
      tip.dataset.for = target.node ? target.node.id : (target.link ? target.link.id : '');
      lastPlace = {anchor, point};
      const title = doc.createElement('strong');
      title.textContent = text.title;
      tip.appendChild(title);
      for (const line of text.lines) {
        const row = doc.createElement('span');
        row.textContent = line;
        tip.appendChild(row);
      }
      // A candidate: how their campaign spent its money, a section of the tooltip (read lazily, one read per candidate).
      const candidate = target.node && spendingEnabled() && spendable(target.node, cycleOf()) ? target.node : null;
      if (candidate) {
        const ready = spending();
        if (ready) ready.section(tip, candidate, cycleOf());
        else spendLater(doc, () => !destroyed && !tip.hidden && tip.dataset.for === candidate.id, () => show(target, anchor, point));
      }
      if (text.url) {
        const link = doc.createElement('a');
        link.href = text.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = `${text.urlText} ↗`;
        tip.appendChild(link);
      }
      if (text.hint) {
        const hint = doc.createElement('em');
        hint.className = 'flow-tip-hint';
        hint.textContent = text.hint;
        tip.appendChild(hint);
      }
      tip.hidden = false;
      place(anchor, point);
    }

    function hide() {
      tip.hidden = true; state.shown = null; state.menu = false;
      tip.classList.remove('has-spend');
      if (spend) spend.cancel();
    }

    function targetOf(element) {
      const group = element.closest('.flow-node');
      if (group) return {node: find(group.dataset.node), element: group};
      const path = element.closest('.flow-link');
      if (path) return {link: findLink(path.dataset.link), element: path};
      return null;
    }

    /* --------------------------------------------------------------- the gestures */
    const zoomable = node => node.kind === 'committee' && !node.root && node.committee_id && state.options.onReroot;

    function toggleGroup(id, open) {
      clearNotice();
      if (open) state.view.open[id] = (state.view.open[id] || 0) + (Number(state.options.more) || 8);
      else delete state.view.open[id];
      hide(); light(null);
      draw();
    }

    function follow(committee, node, anchor) {
      hide(); light(null);
      state.options.onReroot(committee, node, anchor);
    }

    // The money a node passed on past the drawn steps: one committee is followed; several are listed.
    function onward(id, chip) {
      const node = find(id);
      const stubs = onwardOf(state.context.result).get(id) || [];
      if (!node || !stubs.length) return;
      if (stubs.length === 1) { follow(stubs[0].committee_id, stubs[0], node); return; }
      tip.replaceChildren();
      const title = doc.createElement('strong');
      title.textContent = `${nodeTitle(node)} passed on to ${stubs.length} committees`;
      tip.appendChild(title);
      const format = state.context.format;
      const ranked = stubs.map(stub => ({stub, value: state.context.result.links.filter(link => link.target === stub.id)
        .reduce((sum, link) => sum + link.value, 0)})).sort((a, b) => b.value - a.value);
      for (const item of ranked.slice(0, 8)) {
        const button = doc.createElement('button');
        button.type = 'button';
        button.className = 'link-button flow-tip-go';
        button.textContent = `${String(item.stub.label).replace(/^passed to /, '')} ${format.short(item.value)}`;
        button.addEventListener('click', () => follow(item.stub.committee_id, item.stub, node));
        tip.appendChild(button);
      }
      if (ranked.length > 8) {
        const more = doc.createElement('span');
        more.textContent = `and ${ranked.length - 8} more`;
        tip.appendChild(more);
      }
      tip.hidden = false;
      state.menu = true;
      const at = chip.getBoundingClientRect();
      place(chip, {x: at.left + at.width / 2, y: at.top + at.height / 2});
    }

    function activate(node, event) {
      if (!node) return;
      if (event && event.shiftKey && explore()) { togglePin(node.id); return; }      // Chart Studio: a plain click
      if (explore() && expandable(node)) { toggleGroup(node.id, true); return; }
      if (zoomable(node)) follow(node.committee_id, node, node);
      else if (node.kind === 'candidate' && openSpend(node)) { /* the panel is open: Open in Rows is a button in it */ }
      else if (node.kind === 'candidate' && state.options.onCandidate) state.options.onCandidate(node);
      else if (node.bucket === 'other' && state.options.onOther) state.options.onOther(node);
    }

    function move(node, key) {
      const result = state.context.result;
      const column = result.columns[node.stage];
      const at = column.ids.indexOf(node.id);
      let next = null;
      if (key === 'ArrowDown') next = column.ids[at + 1];
      if (key === 'ArrowUp') next = column.ids[at - 1];
      if (key === 'ArrowRight' || key === 'ArrowLeft') {
        const step = key === 'ArrowRight' ? 1 : -1;
        for (let index = node.stage + step; index >= 0 && index < result.columns.length && !next; index += step) {
          const ids = result.columns[index].ids;
          if (!ids.length) continue;
          const middle = (node.y0 + node.y1) / 2;
          next = ids.slice().sort((a, b) => Math.abs((find(a).y0 + find(a).y1) / 2 - middle)
            - Math.abs((find(b).y0 + find(b).y1) / 2 - middle))[0];
        }
      }
      if (!next) return;
      focusNode(next);
    }

    function focusNode(id) {
      const group = host.querySelector(`.flow-node[data-node="${CSS.escape(id)}"]`);
      if (!group) return;
      for (const item of host.querySelectorAll('.flow-node[tabindex="0"]')) item.setAttribute('tabindex', '-1');
      group.setAttribute('tabindex', '0');
      group.focus();
    }

    const over = event => {
      if (state.menu) return;
      const target = targetOf(event.target);
      if (!target || (!target.node && !target.link)) return;
      const id = target.node ? target.node.id : target.link.id;
      // Moving between a node's bar and its words keeps the tooltip where it is.
      if (state.shown === id && !tip.hidden) return;
      light(id);
      // By the pointer, for a ribbon or a node alike (keyboard focus places it by the bar).
      show(target, target.element, Number.isFinite(event.clientX) ? {x: event.clientX, y: event.clientY} : null);
      state.shown = id;
    };
    // Moving onto the tooltip keeps it (its FEC link can be clicked); leaving it for the page hides it.
    const out = event => {
      if (state.menu) return;
      const next = event.relatedTarget;
      if (next && host.contains(next) && (targetOf(next) || tip.contains(next) || (next.closest && next.closest('.flow-onward')))) return;
      light(null); hide();
    };
    const click = event => {
      const reach = event.target.closest ? event.target : null;
      if (!reach) return;
      if (state.menu && !tip.contains(reach)) { hide(); light(null); }
      const fold = reach.closest('.flow-collapse');
      if (fold) { toggleGroup(fold.dataset.collapse, false); return; }
      const chip = reach.closest('.flow-onward');
      if (chip && explore()) { onward(chip.dataset.onward, chip); return; }
      if (reach.closest('.flow-notice')) { clearNotice(); return; }
      const target = targetOf(reach);
      if (!target) return;
      if (target.node) { activate(target.node, event); return; }
      const link = target.link;
      const sink = link && find(link.target);
      if (explore() && sink && sink.stub === 'onward' && sink.committee_id && state.options.onReroot && !event.shiftKey)
        follow(sink.committee_id, sink, find(sink.attach));
    };
    const key = event => {
      const target = targetOf(event.target);
      if (event.target.closest && event.target.closest('.flow-collapse') && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); toggleGroup(event.target.closest('.flow-collapse').dataset.collapse, false); return;
      }
      if (!target || !target.node) return;
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(target.node, event); return; }
      if (event.key === 'Escape') {
        if (!tip.hidden || host.querySelector('.flow-notice') || state.menu) { event.preventDefault(); clearNotice(); }
        light(null); hide(); return;
      }
      // P and C belong to the views that explore, and never to a browser or system shortcut (Ctrl-P prints).
      const plain = explore() && !event.ctrlKey && !event.metaKey && !event.altKey;
      if (plain && (event.key === 'p' || event.key === 'P')) { event.preventDefault(); togglePin(target.node.id); return; }
      if (plain && (event.key === 'c' || event.key === 'C') && target.node.group) {
        event.preventDefault();
        const id = target.node.group;
        toggleGroup(id, false);
        focusNode(id);
        return;
      }
      if (event.key.startsWith('Arrow')) { event.preventDefault(); move(target.node, event.key); }
    };
    const focusIn = event => {
      const target = targetOf(event.target);
      if (target && target.node) { light(target.node.id); show(target, target.element); }
    };
    const focusOut = event => { if (!host.contains(event.relatedTarget) && !state.menu) { light(null); hide(); } };
    host.addEventListener('pointerover', over);
    host.addEventListener('pointerout', out);
    host.addEventListener('click', click);
    host.addEventListener('keydown', key);
    host.addEventListener('focusin', focusIn);
    host.addEventListener('focusout', focusOut);
    draw();
    return {
      update(nextPayload, nextOptions) {
        if (nextPayload) { state.payload = nextPayload; state.full = null; }
        if (nextOptions) Object.assign(state.options, nextOptions);
        hide(); draw();
      },
      result: () => state.context.result,
      colours: () => state.context.colours,
      // What is drawn after the arrangement (small transfers folded, groups unfolded): the strip of the
      // next level is made from it.
      arranged: () => state.context.payload,
      groups: () => state.context.groups,
      view: () => state.view,
      pins: () => state.view.pins.slice(),
      pin: togglePin,
      notice,
      trace: light,
      describe: target => describe(target, state.context, state.payload, hintsOf()),
      destroy() {
        destroyed = true;           // a spending module still loading must not build a controller or a listener for this chart
        clearNotice();
        host.removeEventListener('pointerover', over);
        host.removeEventListener('pointerout', out);
        host.removeEventListener('click', click);
        host.removeEventListener('keydown', key);
        host.removeEventListener('focusin', focusIn);
        host.removeEventListener('focusout', focusOut);
        host.replaceChildren();
        if (state.options.pinPanel) { state.options.pinPanel.replaceChildren(); state.options.pinPanel.hidden = true; }
        // The spending panel is emptied with the chart; what the reader had open (view.spend) is kept for the next draw.
        if (spend) spend.detach();
        else if (state.options.spendingPanel) { state.options.spendingPanel.replaceChildren(); state.options.spendingPanel.hidden = true; }
      }
    };
  }

  /* ------------------------------------------------------------- the foot */
  // FL7: the notes under a Flow as the boards set them, one grey line, with a Details
  // disclosure that lists every note in full (F2, F4, F12, pooled money, crypto counted once:
  // none is dropped). The line ends with an ellipsis where it runs out of room.
  let footSerial = 0;
  function footnote(host, notes) {
    const doc = host.ownerDocument;
    const texts = (notes || []).map(note => (typeof note === 'string' ? note : (note && note.text) || '')).filter(Boolean);
    host.replaceChildren();
    if (!texts.length) return {line: null, toggle: null, list: null};
    footSerial += 1;
    const line = doc.createElement('p');
    line.className = 'flow-foot-line';
    line.textContent = texts.join(' · ');
    const list = doc.createElement('ul');
    list.className = 'flow-foot-list';
    list.id = `flow-foot-list-${footSerial}`;
    list.hidden = true;
    for (const text of texts) {
      const item = doc.createElement('li');
      item.textContent = text;
      list.appendChild(item);
    }
    const toggle = doc.createElement('button');
    toggle.type = 'button';
    toggle.className = 'flow-foot-toggle';
    toggle.textContent = 'Details';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', list.id);
    toggle.addEventListener('click', () => {
      const open = list.hidden;
      list.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      toggle.textContent = open ? 'Hide details' : 'Details';
    });
    host.append(line, toggle, list);
    // One short note that fits needs no disclosure; anything more is listed under Details.
    toggle.hidden = texts.length < 2 && !(line.scrollWidth > line.clientWidth + 1);
    return {line, toggle, list};
  }

  // FL10: a Flow heading on one line: its stylesheet size, stepped down to 20 px while it
  // overflows, then cut with an ellipsis and the full words in its title. The size is set
  // through the CSSOM (the console's CSP refuses a style attribute).
  // Whether a one-line heading's words are wider than its box, to the subpixel (scrollWidth is
  // rounded, and an overflow of half a pixel is enough for the ellipsis to show).
  function overflows(element) {
    const doc = element.ownerDocument;
    if (doc && typeof doc.createRange === 'function') {
      const range = doc.createRange();
      range.selectNodeContents(element);
      const style = getComputedStyle(element);
      const room = element.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
      return range.getBoundingClientRect().width > room - 0.5;
    }
    return element.scrollWidth > element.clientWidth;
  }

  function fitHeading(element, least) {
    if (!element || typeof getComputedStyle !== 'function') return;
    element.style.fontSize = '';
    element.removeAttribute('title');
    if (!element.clientWidth) return;
    let size = parseFloat(getComputedStyle(element).fontSize) || 26;
    const floor = least || 20;
    while (overflows(element) && size > floor) {
      size -= 1;
      element.style.fontSize = `${size}px`;
    }
    if (overflows(element)) element.title = element.textContent;
    // Measured before the serif face arrived, the fit is done again once it has (a heading fitted
    // in the fallback face overflowed in the real one: round-1 capture, Democrats Serve).
    const fonts = element.ownerDocument && element.ownerDocument.fonts;
    if (fonts && fonts.status !== 'loaded' && !element.__flowFitPending) {
      element.__flowFitPending = true;
      fonts.ready.then(() => { element.__flowFitPending = false; fitHeading(element, least); });
    }
  }

  const module_short = short, module_full = full;
  const api = {svgString, mount, prepare, describe, short, full, footnote, fitHeading, fitLine, MODES, VIEW_OPACITY,
    // Lane G1 (board #44): what a hover lights, and where its tooltip goes.
    traced, placeTip,
    // Lane Z (2026-10-15): the zoom path, the strip and minimap, the chain behind a pin, one read per query.
    trail, crumbs, minimap, chainOf, pools, pinRoute, pinBlocks, read, waiting, loader, MAX_PINS,
    // The campaign-spending card and panel: whether they are on (off on the hosted console), and the late-module guard.
    spendingOn, spendLater};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MentatFlowChart = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
