/* Chart Studio's Flow mark (lane F, board #46): a Sankey drawn from a report worksheet's rows.
 *
 * build() is pure (node:test drives it). The rows the sheet's filters select become paths
 * through its stages; each path's total is a sum of whole rows, so every node balances by
 * construction. A path whose rows net to zero or below has no width to draw and is
 * disclosed, never hidden. Each stage keeps its largest members under the top N rule the
 * PAC evidence Flow uses (flow-layout.js otherMembers, flow_data.group's rule) and the rest
 * join one "Other" node. The layout and the drawing are the PAC evidence Flow's own
 * (flow-layout.js, flow-chart.js); only the words differ: worksheet rows, not filings.
 *
 * render() draws a Studio surface at board #46's size (lane F2a): 13px labels (flow.css sets
 * the Flow drawing's text to 13px over the canvas's 18px, and every label is measured at 13px),
 * about 10px nodes, the first stage's labels right-aligned left of its bars, and the stages
 * spread evenly over the whole width; decorate() turns Columns into the Stages shelf;
 * options() is the Marks card's Flow options block; tableRows() is the CSV.
 *
 * Colours come only from MentatTheme.categorical. CSP: no style attributes and no <style>;
 * the tooltip and the options are placed through classes and the CSSOM.
 */
(function (root) {
  'use strict';

  const inNode = typeof module !== 'undefined' && module.exports;
  const layoutApi = () => root.MentatFlowLayout || (inNode ? require('./flow-layout.js') : null);
  const chartApi = () => root.MentatFlowChart || (inNode ? require('./flow-chart.js') : null);
  const mathApi = () => root.MentatCharts || (inNode ? require('./chart-math.js') : null);

  const MISSING = '(Missing)';
  const OTHER = '\u0000other';        // a grouped member inside build(); never a worksheet value
  const TEXT = 13;                    // flow.css: #view-chart-studio .studio-canvas svg.flow-chart .flow-label
  const LINE = 16;
  const SUB_TEXT = 11.5, SUB_LINE = 14;
  const PADDING = 8;
  const NODE_WIDTH = 10;
  // flow-layout.js deals stage-one members these slots of the twelve; a Colour field that is
  // not a stage takes the same order, so its members never borrow an outcome's slot.
  const KEY_SLOTS = [2, 3, 4, 6, 9, 10, 11, 5];
  const NEUTRAL = '#a39b92';
  const AGG_WORD = {sum: 'Sum', count: 'Count', mean: 'Average', median: 'Median', min: 'Minimum', max: 'Maximum',
    countd: 'Count distinct'};
  const AGG_SHORT = {sum: 'SUM', count: 'COUNT', mean: 'AVG', median: 'MEDIAN', min: 'MIN', max: 'MAX', countd: 'COUNTD'};

  const present = value => value != null && !(typeof value === 'string' && !value.trim());
  const memberOf = (row, column) => {
    const value = row[column];
    const text = value == null ? '' : String(value).trim();
    return text || MISSING;
  };
  const dollars = cents => cents / 100;

  /* ---------------------------------------------------------------- formats */
  function shortNumber(value) {
    const size = Math.abs(value), sign = value < 0 ? '-' : '';
    if (size >= 999950) return `${sign}${(Math.round(size / 1e5) / 10).toFixed(1)}M`;
    if (size >= 9999.5) return `${sign}${Math.round(size / 1000).toLocaleString('en-US')}K`;
    return `${sign}${size.toLocaleString('en-US', {maximumFractionDigits: 2})}`;
  }
  const fullNumber = value => Number(value).toLocaleString('en-US', {maximumFractionDigits: 2});
  function formats(unit) {
    const chart = chartApi();
    return unit === 'money' ? {short: chart.short, full: chart.full} : {short: shortNumber, full: fullNumber};
  }
  const measureWords = measure => !measure ? 'Row count' : `${AGG_SHORT[measure.aggregation] || 'SUM'}(${measure.name})`;
  const pillWords = pill => `${AGG_SHORT[pill.aggregation] || 'SUM'}(${pill.name})`;

  /* -------------------------------------------------------------- the title */
  // Lanes G1/G1b (board #46): the automatic title in the board's words - whose money, by stage
  // ("Crypto money by stage"). The subject is the industry the source names, or the industry a
  // report's file name leads with ("Crypto Industry Spending Overview.xlsx" -> Crypto); any other file
  // name is no subject (the subtitle names the file already), and the title says "Money by stage". A
  // flow sized by anything but money names its measure ("Crypto: Gift count by stage"). The subtitle
  // keeps the detail (the source, the measure, the stages and the grouping), and the status line keeps
  // the SUM(...) the drawing reconciles to, unchanged.
  function subjectOf(source) {
    const given = source || {};
    const industry = String(given.industry || '').trim();
    if (industry) return industry;
    const name = String(given.reportName || '').trim().replace(/\.(xlsx|xlsm|xls|csv)$/i, '').trim();
    const lead = /^(.+?)\s+industry\b/i.exec(name);
    return lead ? lead[1].trim() : '';
  }
  // The measure in the fewest words: a sum is its field ("Analysis Amount"); a count keeps its function
  // ("COUNT(Recipient)"); a flow with no measure counts rows.
  const sizeName = measure => !measure ? 'Row count'
    : !measure.aggregation || measure.aggregation === 'sum' ? measure.name : measureWords(measure);
  function titleOf(source, measure, unit) {
    const subject = subjectOf(source);
    if (unit === 'money') return subject ? `${subject} money by stage` : 'Money by stage';
    return (subject ? `${subject}: ` : '') + `${sizeName(measure)} by stage`;
  }

  /* ------------------------------------------------------------- statistics */
  // Label and Tooltip measures are read per node or ribbon over its rows, never added across
  // a stage, so every aggregation the pill can hold is honest here.
  function statOf(rows, column, aggregation, math) {
    if (aggregation === 'count') return rows.reduce((count, row) => count + (present(row[column]) ? 1 : 0), 0);
    if (aggregation === 'countd') return new Set(rows.filter(row => present(row[column])).map(row => String(row[column]).trim())).size;
    const numbers = [];
    for (const row of rows) { const number = math.numeric(row[column]); if (number !== null) numbers.push(number); }
    if (!numbers.length) return null;
    if (aggregation === 'mean') return numbers.reduce((sum, number) => sum + number, 0) / numbers.length;
    if (aggregation === 'min') return numbers.reduce((low, number) => Math.min(low, number), Infinity);
    if (aggregation === 'max') return numbers.reduce((high, number) => Math.max(high, number), -Infinity);
    if (aggregation === 'median') {
      const sorted = numbers.sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }
    return numbers.reduce((sum, number) => sum + Math.round(number * 100), 0) / 100;
  }
  const statFormat = pill => pill.unit === 'currency' && !['count', 'countd'].includes(pill.aggregation) ? formats('money') : formats('number');

  /* ---------------------------------------------------------- the top N rule */
  // The members of one stage (or of a Colour field) that join "Other": flow-layout.js's rule,
  // the one flow_data.group applies to the PAC evidence Flow, on the members' drawn totals.
  function groupedMembers(paths, pick, top) {
    const totals = new Map();
    for (const path of paths) totals.set(pick(path), (totals.get(pick(path)) || 0) + path.cents);
    const column = [...totals].map(([member, cents]) => ({id: member, label: member, kind: 'committee', vin: cents, vout: cents}));
    return {totals, rest: new Set(layoutApi().otherMembers(column, top).map(node => node.id))};
  }

  /* ------------------------------------------------------------------ build */
  function build(data, config, settings) {
    const math = (settings && settings.math) || mathApi();
    const ctx = {lenient: true, warnings: [], totalRefusal: ''};
    const rows = math.filterRows(data, {filters: config.filters || [], start: config.start || 0, end: config.end || 0,
      limit: config.limit || 0, lenient: true}, ctx);
    const notes = ctx.warnings.slice();
    const stages = config.stages;
    const last = stages.length - 1;
    const measure = config.measure;
    const counting = !measure || measure.aggregation === 'count';
    const unit = !counting && measure.unit === 'currency' ? 'money' : 'number';
    const options = config.options || {};
    const top = Number(options.groupAfter) || 0;
    const colourStage = config.colour ? stages.findIndex(stage => stage.column === config.colour.column) : -1;
    // A ribbon splits by its Colour member, or (Ribbons: Outcome) by the last stage it reaches.
    const split = config.colour ? {stage: colourStage, column: config.colour.column, name: config.colour.name}
      : options.ribbons === 'outcome' ? {stage: last, column: stages[last].column, name: stages[last].name} : null;

    // 1. Every selected row's value (hundredths, so sums are exact) and path.
    let excluded = 0, rowsCents = 0;
    const items = [];
    for (const row of rows) {
      let cents;
      if (!measure) cents = 100;
      else if (measure.aggregation === 'count') { if (!present(row[measure.column])) continue; cents = 100; }
      else {
        const number = math.numeric(row[measure.column]);
        if (number === null) { excluded += 1; continue; }
        cents = Math.round(number * 100);
      }
      rowsCents += cents;
      items.push({row, cents, members: stages.map(stage => memberOf(row, stage.column)),
        colour: split && split.stage < 0 ? memberOf(row, split.column) : ''});
    }

    // 2. Paths, and the ones a width cannot show.
    const paths = new Map();
    for (const item of items) {
      const key = JSON.stringify([...item.members, item.colour]);
      let path = paths.get(key);
      if (!path) paths.set(key, path = {members: item.members, colour: item.colour, cents: 0, items: []});
      path.cents += item.cents;
      path.items.push(item);
    }
    const drawn = [];
    const dropped = {paths: 0, rows: 0, cents: 0}, zero = {paths: 0, rows: 0};
    for (const path of paths.values()) {
      if (path.cents > 0) { drawn.push(path); continue; }
      if (path.cents === 0) { zero.paths += 1; zero.rows += path.items.length; continue; }
      dropped.paths += 1; dropped.rows += path.items.length; dropped.cents += path.cents;
    }

    // 3. The top N rule on each stage's members, and on a Colour field that is not a stage.
    const groups = stages.map((stage, index) => groupedMembers(drawn, path => path.members[index], top));
    const colourGroup = split && split.stage < 0 ? groupedMembers(drawn, path => path.colour, top) : null;
    const grouped = (group, member) => group.rest.has(member) ? OTHER : member;

    // 4. The grouped paths, then nodes and ribbons.
    const final = new Map();
    for (const path of drawn) {
      const members = path.members.map((member, index) => grouped(groups[index], member));
      const colour = colourGroup ? grouped(colourGroup, path.colour) : '';
      const key = JSON.stringify([...members, colour]);
      let entry = final.get(key);
      if (!entry) final.set(key, entry = {members, colour, cents: 0, items: []});
      entry.cents += path.cents;
      for (const item of path.items) entry.items.push(item);
    }
    const nodeId = (index, member) => member === OTHER ? `s${index}:other` : `s${index}:m:${member}`;
    const keyOf = member => member === OTHER ? 'other' : `m:${member}`;
    const nodes = new Map(), links = new Map();
    for (const entry of final.values()) {
      entry.members.forEach((member, index) => {
        const id = nodeId(index, member);
        let node = nodes.get(id);
        if (!node) nodes.set(id, node = {id, stage: index, member, cents: 0, items: []});
        node.cents += entry.cents;
        for (const item of entry.items) node.items.push(item);
      });
      const splitMember = !split ? null : split.stage >= 0 ? entry.members[split.stage] : entry.colour;
      for (let index = 0; index < last; index += 1) {
        const source = nodeId(index, entry.members[index]), target = nodeId(index + 1, entry.members[index + 1]);
        const at = `${source}\u0001${target}\u0001${splitMember == null ? '' : splitMember}`;
        let link = links.get(at);
        if (!link) links.set(at, link = {source, target, member: splitMember, cents: 0, items: []});
        link.cents += entry.cents;
        for (const item of entry.items) link.items.push(item);
      }
    }

    // Ribbons between the same two nodes stack in their split member's rank, on both ends.
    const splitGroup = !split ? null : split.stage >= 0 ? groups[split.stage] : colourGroup;
    const rankOf = new Map();
    if (splitGroup) {
      const ranked = new Map();
      for (const [member, cents] of splitGroup.totals) {
        const key = splitGroup.rest.has(member) ? OTHER : member;
        ranked.set(key, (ranked.get(key) || 0) + cents);
      }
      [...ranked].sort((a, b) => (a[0] === OTHER) - (b[0] === OTHER) || b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
        .forEach(([member], at) => rankOf.set(member, at));
    }

    const details = (list, pills) => pills.map(pill => {
      const rowsOf = list.map(item => item.row);
      if (pill.role === 'measure') {
        const value = statOf(rowsOf, pill.column, pill.aggregation, math);
        return `${pillWords(pill)}: ${value === null ? '—' : statFormat(pill).full(value)}`;
      }
      const by = new Map();
      for (const item of list) { const member = memberOf(item.row, pill.column); by.set(member, (by.get(member) || 0) + item.cents); }
      const ordered = [...by].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
      const format = formats(unit);
      const shown = ordered.slice(0, 3).map(([member, cents]) => `${member} ${format.short(dollars(cents))}`);
      return `${pill.name}: ${shown.join(', ')}${ordered.length > 3 ? `, and ${ordered.length - 3} more` : ''}`;
    });
    const tooltip = config.tooltip || [];
    const label = config.label || null;
    const otherLabel = index => [...nodes.values()].some(node => node.stage === index && node.member === 'Other') ? 'Other (grouped)' : 'Other';
    const payloadNodes = [...nodes.values()].map(node => {
      const base = {id: node.id, stage: node.stage, value_in: node.stage > 0 ? dollars(node.cents) : 0,
        value_out: node.stage < last ? dollars(node.cents) : 0, rows: node.items.length};
      if (label) {
        const value = statOf(node.items.map(item => item.row), label.column, label.aggregation, math);
        base.amount_text = value === null ? '—' : statFormat(label).short(value);
      }
      base.details = [...(label ? details(node.items, [label]) : []), ...details(node.items, tooltip)];
      if (node.member === OTHER) {
        const members = [...groups[node.stage].totals].filter(([member]) => groups[node.stage].rest.has(member))
          .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
        return Object.assign(base, {kind: 'bucket', bucket: 'other', label: otherLabel(node.stage), member_count: members.length,
          members: members.slice(0, 200).map(([member, cents]) => ({label: member, value: dollars(cents)}))});
      }
      return Object.assign(base, {kind: node.stage === 0 ? 'donor' : 'committee', label: node.member},
        node.member === MISSING ? {missing: true} : {});
    });
    const payloadLinks = [...links.values()]
      .sort((a, b) => a.source < b.source ? -1 : a.source > b.source ? 1 : a.target < b.target ? -1 : a.target > b.target ? 1
        : (rankOf.get(a.member) ?? 0) - (rankOf.get(b.member) ?? 0))
      .map(link => {
        const out = {source: link.source, target: link.target, value: dollars(link.cents), kind: 'transfer', rows: link.items.length,
          details: [...(split ? [`${split.name}: ${link.member === OTHER ? 'Other' : link.member}`] : []), ...details(link.items, tooltip)]};
        if (split) {
          out.key = keyOf(link.member);
          out.split_member = link.member === OTHER ? 'Other' : link.member;
          if (split.stage >= 0) out.key_node = nodeId(split.stage, link.member);
          else out.key_rank = link.member === OTHER ? -1 : rankOf.get(link.member);
        }
        return out;
      });

    // What the flow does not show, and the checks every Studio total passes (chart-math.js
    // validateGrain's sentences: a flow is drawn, and its total is withheld with the reason).
    const format = formats(unit);
    if (excluded) notes.push(`${excluded.toLocaleString('en-US')} row${excluded === 1 ? ' has' : 's have'} no number in ${measure.name}, so ${excluded === 1 ? 'it is' : 'they are'} not in the flow.`);
    if (zero.paths) notes.push(`${zero.paths.toLocaleString('en-US')} path${zero.paths === 1 ? '' : 's'} (${zero.rows.toLocaleString('en-US')} row${zero.rows === 1 ? '' : 's'}) total ${format.full(0)} and draw${zero.paths === 1 ? 's' : ''} no ribbon.`);
    if (dropped.paths) notes.push(`${dropped.paths.toLocaleString('en-US')} path${dropped.paths === 1 ? '' : 's'} (${dropped.rows.toLocaleString('en-US')} row${dropped.rows === 1 ? '' : 's'}) net to ${format.full(dollars(dropped.cents))}, below zero, and cannot be drawn as ribbons.`);
    const accounting = data.accounting || {};
    const summing = !!measure && measure.aggregation === 'sum';
    const columnAccounting = measure ? ((data.columns || [])[measure.column] || {}).accounting || {} : {};
    let refusal = '';
    if (summing && columnAccounting.safe_to_sum === false) refusal = columnAccounting.reason || 'This raw measure is not safe to sum. Choose the analysis amount.';
    else if (summing && accounting.safe_to_sum === false) refusal = accounting.reason || 'This worksheet is not an additive fact table. Choose a reconciled contribution table or a safe subsection.';
    else if (summing && Number.isInteger(accounting.row_role_column)
      && rows.some(row => !(accounting.included_row_roles || ['fact']).includes(row[accounting.row_role_column])))
      refusal = 'Summary and detail rows cannot be summed together. Filter to one declared fact-row role.';
    else if (summing) {
      const kept = new Set([...stages.map(stage => stage.column), ...(config.colour ? [config.colour.column] : [])]);
      for (const column of accounting.independent_dimensions || []) {
        if (!kept.has(column) && new Set(rows.map(row => row[column])).size > 1) {
          refusal = 'Keep ' + (((data.columns || [])[column] || {}).name || 'the accounting basis')
            + ' in the stages or filter to one value; these amounts can overlap.';
          break;
        }
      }
    }
    if (refusal) notes.push(refusal);
    const drawnCents = drawn.reduce((sum, path) => sum + path.cents, 0);
    return {
      payload: {version: 1, source: 'worksheet', unit,
        stages: stages.map((stage, index) => ({index, key: `stage${index}`, label: stage.name})),
        nodes: payloadNodes, links: payloadLinks,
        totals: {drawn: dollars(drawnCents), rows_total: dollars(rowsCents), rows: items.length, selected: rows.length,
          excluded, dropped_paths: dropped.paths, dropped_rows: dropped.rows, dropped: dollars(dropped.cents),
          zero_paths: zero.paths, zero_rows: zero.rows,
          reconciles: !refusal && drawnCents === rowsCents, refusal}},
      split: split ? {stage: split.stage, name: split.name} : null,
      notes
    };
  }

  /* ----------------------------------------------------------------- colour */
  // Missing members are grey; a split ribbon takes its member's colour: the stage node's when
  // the member is a stage's, else the member's rank in flow-layout.js's slot order.
  function recolourer(built) {
    return (result, colours, palette, neutral) => {
      for (const node of result.nodes) if (node.missing) colours.set(node.id, neutral);
      if (!built.split) return;
      for (const link of result.links) {
        if (link.key_node) link.colour = colours.get(link.key_node) || neutral;
        else if (link.key != null) link.colour = link.key_rank >= 0 ? palette[KEY_SLOTS[link.key_rank % KEY_SLOTS.length]] : neutral;
      }
    };
  }

  function legendItems(built, palette, width) {
    if (!built.split || built.split.stage >= 0) return {items: [], height: 0};
    const seen = new Map();
    for (const link of built.payload.links) if (!seen.has(link.key)) seen.set(link.key, link);
    const entries = [...seen.values()].sort((a, b) => (a.key_rank < 0) - (b.key_rank < 0) || a.key_rank - b.key_rank);
    const title = `Colour: ${built.split.name}`;
    const items = [{x: 8, y: TEXT, label: title, colour: 'none'}];
    let x = 8 + 17 + title.length * TEXT * 0.56 + 14, y = TEXT;
    for (let at = 0; at < entries.length; at += 1) {
      const link = entries[at];
      const room = 17 + link.split_member.length * TEXT * 0.56 + 18;
      if (x + room > width - 8) {
        x = 8; y += LINE;
        // Three lines at most; what does not fit is counted, and every ribbon's tooltip names its member.
        if (y > TEXT + LINE * 2) { items.push({x, y: y - LINE, label: `+${entries.length - at} more`, colour: 'none'}); y -= LINE; break; }
      }
      items.push({x, y, label: link.split_member,
        colour: link.key_rank >= 0 ? palette[KEY_SLOTS[link.key_rank % KEY_SLOTS.length]] : NEUTRAL});
      x += room;
    }
    return {items, title, height: y + 10};
  }

  /* ----------------------------------------------------------------- render */
  const mounted = typeof WeakMap === 'function' ? new WeakMap() : null;

  function palette() {
    const theme = root.MentatTheme;
    return theme && Array.isArray(theme.categorical) && theme.categorical.length >= 12 ? theme.categorical : null;
  }

  // The height a flow needs so no two labels meet: each node reserves its name and amount.
  function neededHeight(payload, top) {
    const counts = new Map();
    for (const node of payload.nodes) counts.set(node.stage, (counts.get(node.stage) || 0) + 1);
    const most = Math.max(0, ...counts.values());
    return top + 12 + most * LINE * 2 + Math.max(0, most - 1) * PADDING;
  }

  // A label's width at the drawing's size (MentatTheme.measureText in the page).
  function textWidth(text) {
    const theme = root.MentatTheme;
    return theme && typeof theme.measureText === 'function' ? theme.measureText(String(text), TEXT) : String(text).length * TEXT * 0.56;
  }

  // The room right of the last stage's bars and (board #46) left of the first stage's: each as
  // wide as its longest name needs, within a share of the drawing, so the stages between them
  // spread evenly over the rest of the width.
  function lastColumnRoom(payload, width) {
    const last = payload.stages.length - 1;
    const longest = payload.nodes.filter(node => node.stage === last).reduce((most, node) => Math.max(most, textWidth(node.label)), 0);
    return Math.round(Math.min(width * 0.26, Math.max(120, longest + 30)));
  }

  function firstColumnRoom(payload, width) {
    const longest = payload.nodes.filter(node => node.stage === 0).reduce((most, node) => Math.max(most, textWidth(node.label)), 0);
    return Math.round(Math.min(width * 0.24, Math.max(90, longest + 18)));
  }

  function exportCopy(svg) {
    const copy = svg.cloneNode(true);
    if (typeof getComputedStyle !== 'function') return copy;
    const from = svg.querySelectorAll('text'), to = copy.querySelectorAll('text');
    from.forEach((text, at) => {
      const style = getComputedStyle(text);
      for (const [name, value] of [['font-family', style.fontFamily], ['font-size', style.fontSize], ['font-weight', style.fontWeight],
        ['fill', style.fill], ['stroke', style.stroke], ['stroke-width', style.strokeWidth], ['paint-order', style.paintOrder]])
        if (value) to[at].setAttribute(name, value);
    });
    return copy;
  }

  function render(view, report, data, context) {
    const node = view.el;
    const canvas = node('canvas');
    const previous = mounted && mounted.get(canvas);
    if (previous) { previous.destroy(); mounted.delete(canvas); }
    canvas.hidden = false;
    node('table').hidden = true;
    // A worksheet flow is drawn here and now: a verified query still pending from before the source
    // changed must not leave the canvas marked busy (chart-studio.css fades a busy canvas).
    if (canvas.getAttribute('aria-busy') === 'true') canvas.setAttribute('aria-busy', 'false');
    const notes = [...(context.notes || []), ...(report.notes || [])];
    // Recipient-committee activity is not the industry's money: the Studio's own caption.
    if (context.committee && !notes.includes(context.committee)) notes.push(context.committee);
    const config = report.config;
    if (!config) {
      node('title').textContent = '';
      node('subtitle').textContent = '';
      node('note').textContent = [...notes, ...(report.issues || [])].join(' ');
      canvas.replaceChildren();
      return {result: null};
    }
    const built = build(data, config);
    notes.push(...built.notes);
    const payload = built.payload, totals = payload.totals;
    const format = formats(payload.unit);
    const options = config.options || {};
    const sizeWords = measureWords(config.measure);
    const title = titleOf(context.source, config.measure, payload.unit);
    const chain = config.stages.map(stage => stage.name).join(' → ');
    node('title').textContent = title;
    node('subtitle').textContent = [context.subtitle, sizeName(config.measure), chain,
      options.groupAfter ? `top ${options.groupAfter} per stage` : 'every member'].filter(Boolean).join(' · ');
    const source = context.source || {};
    const filterText = (config.filters || []).map(f => `${((data.columns || [])[f.column] || {}).name || 'field'} ${f.op} ${f.value}`).join('; ');
    view.title = title;
    view.scope = [`${source.reportName || ''} / ${data.sheet}`,
      `${totals.selected.toLocaleString('en-US')} of ${Number(data.row_count || 0).toLocaleString('en-US')} rows selected; ${totals.excluded} rows excluded for missing or non-numeric values.`,
      `Stages: ${chain}.`, `Size: ${sizeWords}.`, `Filters: ${filterText || 'none'}.`,
      options.groupAfter ? `Top ${options.groupAfter} per stage, the rest as Other.` : 'Every member drawn.',
      `SHA-256: ${data.sha256 || ''}.`].join(' ');
    if (!payload.links.length) {
      canvas.replaceChildren();
      node('note').textContent = [...notes, 'No worksheet rows reach two stages with a value above zero, so there is no flow to draw.'].join(' ');
      status(node('status'), config, built, format, context);
      return {result: {type: 'flow', payload, links: []}};
    }
    const colours = palette() || [];
    const fit = context.fit || null;
    const width = Math.max(560, Math.floor(fit ? fit.width : canvas.clientWidth || 1000));
    const legend = legendItems(built, colours.length ? colours : Array(12).fill(NEUTRAL), width);
    const top = legend.height ? legend.height + 10 : 8;
    const needed = neededHeight(payload, top);
    const boxHeight = fit && fit.height ? Math.floor(fit.height) : 0;
    const height = Math.max(boxHeight || Math.round(width * 0.56), needed, 360);
    const handle = chartApi().mount(canvas, payload, {
      width, height, ribbons: options.ribbons === 'outcome' ? 'source' : options.ribbons || 'source',
      opacity: (Number.isFinite(Number(options.opacity)) ? Number(options.opacity) : 40) / 100,
      order: options.order === 'alphabetical' ? 'alpha' : 'value', headers: false,
      labelSize: TEXT, subSize: SUB_TEXT, labelLine: LINE, subLine: SUB_LINE, labelLines: 2, minPadding: PADDING,
      nodeWidth: NODE_WIDTH, firstLabels: 'left', firstLabelWidth: firstColumnRoom(payload, width), grow: true,
      labelWidth: lastColumnRoom(payload, width), margin: {top, right: 4, bottom: 10, left: 4},
      amounts: context.showLabels !== false, format, recolour: recolourer(built),
      legend: legend.items, legendTitle: legend.title, title: `${title}: ${chain}`});
    if (mounted) mounted.set(canvas, handle);
    const svg = canvas.querySelector('svg.flow-chart');
    if (svg) {
      svg.__mentatExport = () => exportCopy(svg);
      // The Studio redraws a drawing fitted to its box when the box changes; one taller than
      // its box (a plot that grew so no label is dropped, too) scrolls, drawn again when the
      // canvas is resized.
      const drawn = handle.result().height;
      if (fit && drawn <= boxHeight) { svg.setAttribute('data-fit', ''); svg.__fit = {box: fit}; }
      else if (view.primary) canvas.dataset.pieFit = 'wanted';
    }
    node('note').textContent = notes.join(' ') || `${source.reportName || ''} / ${data.sheet} · ${totals.selected.toLocaleString('en-US')} rows selected`;
    status(node('status'), config, built, format, context);
    return {result: {type: 'flow', payload, layout: handle.result(), totals}};
  }

  function status(host, config, built, format, context) {
    if (!host) return;
    const payload = built.payload, totals = payload.totals;
    const nodes = payload.nodes.length, ribbons = payload.links.length;
    const parts = [`${payload.stages.length} stages · ${nodes} node${nodes === 1 ? '' : 's'} · ${ribbons} ribbon${ribbons === 1 ? '' : 's'}`];
    const withheld = totals.refusal || context.committee;
    if (withheld) parts.push('Total withheld: see the note');
    else if (totals.reconciles)
      parts.push(`${format.full(totals.drawn)} · reconciles to the worksheet’s ${measureWords(config.measure)} over ${totals.rows.toLocaleString('en-US')} rows`);
    else parts.push(`${format.full(totals.drawn)} drawn of the worksheet’s ${format.full(totals.rows_total)}; the note says what is not drawn`);
    const nodesOut = parts.map(part => { const span = host.ownerDocument.createElement('span'); span.textContent = part; return span; });
    const badge = host.ownerDocument.createElement('span');
    badge.className = 'studio-local';
    badge.textContent = 'LOCAL ONLY';
    badge.title = 'Every value is read from the local report file and drawn in this browser.';
    nodesOut.push(badge);
    host.replaceChildren(...nodesOut);
  }

  // The CSV a download writes: one line per ribbon.
  function tableRows(result) {
    if (!result || !result.payload) return [];
    const byId = new Map(result.payload.nodes.map(node => [node.id, node]));
    const stageName = index => (result.payload.stages[index] || {}).label || '';
    return [['From stage', 'From', 'To stage', 'To', 'Colour', 'Value', 'Rows'],
      ...result.payload.links.map(link => {
        const from = byId.get(link.source), to = byId.get(link.target);
        return [stageName(from.stage), from.label, stageName(to.stage), to.label, link.split_member || '', link.value, link.rows];
      })];
  }

  /* ------------------------------------------------------ the Stages shelf */
  function decorate(ctx) {
    const on = !!ctx.flow;
    // Rows is put away while a flow is drawn, unless it holds a field: then it stays in view with
    // each pill marked, so nothing sent there is silently inert.
    const rowsShelf = ctx.rows && ctx.rows.closest('.studio-shelf');
    const rowPills = ctx.rows ? [...ctx.rows.querySelectorAll(':scope > .studio-pill')] : [];
    if (rowsShelf) rowsShelf.classList.toggle('studio-shelf--flow-hidden', on && !rowPills.length);
    if (on) for (const pill of rowPills) {
      pill.classList.add('is-inert');
      pill.title = 'A flow draws its Stages and Size; Rows is not drawn. Move the field to Stages or Size. \u00b7 ' + pill.title;
    }
    if (ctx.columnsName) ctx.columnsName.textContent = on ? 'Stages' : 'Columns';
    const shelf = ctx.columns;
    if (shelf) {
      shelf.classList.toggle('studio-drop--stages', on);
      for (const arrow of shelf.querySelectorAll(':scope > .studio-stage-arrow')) arrow.remove();
      if (on) {
        shelf.dataset.placeholder = 'Drop two to five dimensions, in order';
        const fields = ctx.fields || [];
        [...shelf.querySelectorAll(':scope > .studio-pill')].forEach((pill, at) => {
          const field = fields.find(item => String(item.index) === String(pill.dataset.column));
          const why = field && field.role !== 'dimension' ? 'Stages take dimensions; put a measure on Size.'
            : at >= 5 ? 'A flow draws up to five stages.' : '';
          if (why) { pill.classList.add('is-inert'); pill.title = why + ' · ' + pill.title; }
          if (at) {
            const arrow = shelf.ownerDocument.createElement('span');
            arrow.className = 'studio-stage-arrow';
            arrow.setAttribute('aria-hidden', 'true');
            arrow.textContent = '→';
            pill.before(arrow);
          }
        });
      }
    }
    // Board #46's "Chart: Sankey". The menu lists the marks both sources draw, so the flow's
    // entry is there only while a flow is drawn.
    if (ctx.mark) {
      const option = [...ctx.mark.options].find(item => item.value === 'flow');
      if (on && !option) ctx.mark.append(new Option('Sankey', 'flow'));
      if (!on && option) option.remove();
      if (on) ctx.mark.value = 'flow';
    }
    if (on && ctx.aggregation) ctx.aggregation.title = 'A flow’s measure is on Size; its pill menu sets the aggregation.';
  }

  /* ---------------------------------------------------------- Flow options */
  const GROUPS = [0, 3, 5, 8, 10, 12, 15, 20];
  const RIBBONS = [['source', 'Source'], ['target', 'Target'], ['outcome', 'Outcome'], ['gradient', 'Gradient']];
  let current = null;

  function optionsBlock(doc) {
    const block = doc.createElement('section');
    block.className = 'studio-flow-options';
    block.setAttribute('aria-label', 'Flow options');
    const heading = doc.createElement('h4');
    heading.textContent = 'Flow options';
    const row = (text, control, labelled) => {
      const wrap = doc.createElement(labelled ? 'div' : 'label');
      wrap.className = 'studio-flow-row';
      const name = doc.createElement('span');
      name.className = 'studio-flow-name';
      name.textContent = text;
      wrap.append(name, control);
      return wrap;
    };
    const ribbons = doc.createElement('div');
    ribbons.className = 'studio-segmented studio-flow-ribbons';
    ribbons.setAttribute('role', 'group');
    ribbons.setAttribute('aria-label', 'Ribbon colour');
    for (const [value, text] of RIBBONS) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.dataset.ribbons = value;
      button.textContent = text;
      button.onclick = () => { if (current && button.getAttribute('aria-disabled') !== 'true') current.update({ribbons: value}); };
      ribbons.append(button);
    }
    const order = doc.createElement('select');
    order.className = 'studio-flow-order';
    order.append(new Option('By value', 'value'), new Option('Alphabetical', 'alphabetical'));
    order.onchange = () => current && current.update({order: order.value});
    const range = doc.createElement('span');
    range.className = 'studio-flow-range';
    const opacity = doc.createElement('input');
    opacity.type = 'range'; opacity.min = '0'; opacity.max = '100'; opacity.step = '5';
    opacity.className = 'studio-flow-opacity';
    opacity.setAttribute('aria-label', 'Ribbon opacity');
    const shown = doc.createElement('output');
    shown.className = 'studio-flow-opacity-value';
    // The drawing follows the slider as it moves; the sheet takes the value when it settles.
    opacity.oninput = () => {
      shown.textContent = opacity.value + '%';
      for (const path of doc.querySelectorAll('#view-chart-studio .studio-canvas svg.flow-chart .flow-link'))
        path.setAttribute('fill-opacity', String(Number(opacity.value) / 100));
    };
    opacity.onchange = () => current && current.update({opacity: Number(opacity.value)});
    range.append(opacity, shown);
    const group = doc.createElement('select');
    group.className = 'studio-flow-group';
    for (const value of GROUPS) group.append(new Option(value ? `${value} per stage` : 'Show every member', String(value)));
    group.onchange = () => current && current.update({groupAfter: Number(group.value)});
    const note = doc.createElement('p');
    note.className = 'studio-flow-note';
    block.append(heading, row('Ribbon colour', ribbons, true), row('Node order', order), row('Ribbon opacity', range),
      row('Group the rest after', group), note);
    return block;
  }

  function options(ctx) {
    const host = ctx.host;
    if (!host) return;
    let block = host.querySelector(':scope > .studio-flow-options');
    if (!ctx.flow) { if (block) block.hidden = true; current = null; return; }
    if (!block) {
      block = optionsBlock(host.ownerDocument);
      const after = host.querySelector(':scope > .studio-mark-rows-note');
      if (after) after.after(block); else host.append(block);
    }
    current = ctx;
    block.hidden = false;
    const flow = ctx.options || {};
    const locked = ctx.colourName ? `Colour holds ${ctx.colourName}: each ribbon takes its ${ctx.colourName} colour. Take it off Colour to colour by Source, Target, Outcome or Gradient.` : '';
    for (const button of block.querySelectorAll('.studio-flow-ribbons > button')) {
      const on = !locked && button.dataset.ribbons === flow.ribbons;
      button.setAttribute('aria-pressed', String(on));
      if (locked) { button.setAttribute('aria-disabled', 'true'); button.title = locked; }
      else { button.removeAttribute('aria-disabled'); button.title = ''; }
    }
    block.querySelector('.studio-flow-order').value = flow.order || 'value';
    const opacity = block.querySelector('.studio-flow-opacity');
    opacity.value = String(Number.isFinite(Number(flow.opacity)) ? flow.opacity : 40);
    block.querySelector('.studio-flow-opacity-value').textContent = opacity.value + '%';
    const group = block.querySelector('.studio-flow-group');
    const value = String(Number(flow.groupAfter) || 0);
    if (![...group.options].some(option => option.value === value)) group.append(new Option(`${value} per stage`, value));
    group.value = value;
    block.querySelector('.studio-flow-note').textContent = locked;
  }

  const api = {build, render, decorate, options, tableRows, formats, shortNumber, statOf, MISSING, TEXT,
    // Lane G1 (board #46): the automatic title.
    titleOf, subjectOf,
    __test: {recolourer, legendItems, neededHeight}};
  if (inNode) module.exports = api;
  root.MentatStudioFlow = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
