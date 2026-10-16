/* Flow (Sankey) layout: pure geometry for the Flow views (lane F, boards #44-#46).
 *
 * No DOM, no network: a /api/flow answer (contracts/flow.json) or a flow built from
 * worksheet rows goes in, positions come out, and node:test drives every rule.
 *
 *  - Width = quantity: one scale (px per dollar) for every node and ribbon. Arithmetic is
 *    in whole cents, so conservation holds exactly: a node's incoming ribbons stack to its
 *    inflow, its outgoing ribbons to its outflow.
 *  - Columns by stage. Nodes ordered by value (option: alphabetical) inside their group:
 *    named nodes, then buckets (own spending), then the Other node, then balances (not yet
 *    spent, kept, not yet traced) at the foot.
 *  - Top N per column plus "Other": the rule flow_data.group applies on the server,
 *    applied here too for client-built flows (Chart Studio) and idempotent on the
 *    server's already-grouped answer.
 *  - Link stacking that minimises crossings: each node's outgoing ribbons are sorted by
 *    their target's position and its incoming ribbons by their source's position.
 *  - Stubs (other donors, cash from before, loops, onward payments) are drawn beside the
 *    node they attach to - never as column nodes (board #44's Don't-copy).
 *  - Ribbons are cubic curves; the Tableau sigmoid (T from -6 to 6, 49 points,
 *    y = y0 + (y1 - y0) / (1 + e^-T)) is an option. Each ribbon carries the geometry of its
 *    source-to-target gradient (userSpaceOnUse from the source's right edge to the
 *    target's left edge).
 *  - The scale never gives way to the labels (lane F2a, FL1). Bars are drawn to one scale
 *    that is at least barShare of the scale at which the fullest column fills the plot;
 *    labels fit around them, shedding detail smallest node first: the one line under the
 *    name (FL6), then the name's second line, then the amount's own line (it moves beside
 *    the name), and only then the label itself, whose words stay in the tooltip. Before
 *    2026-09-26 the labels' room was a hard floor, and at a 420 px plot a column whose
 *    labels alone overfilled it drove the one scale to zero: 2 px bars, no ribbons.
 */
(function (root) {
  'use strict';

  const NODE_KINDS = new Set(['donor', 'committee', 'candidate', 'bucket', 'balance']);
  const LINK_KINDS = new Set(['receipt', 'transfer', 'ie_support', 'ie_oppose', 'ie_unknown', 'contribution',
    'operating', 'uncounted', 'unspent', 'prior_cash', 'external_in', 'untraced']);
  const DIRECTIONS = new Set(['support', 'oppose', 'contribution', 'unknown']);
  const REFUSALS = new Set(['no_estimate_bundle', 'estimate_not_drawn', 'reconciliation', 'conservation',
    'industry_unavailable']);
  const TOP_FIELDS = ['version', 'industry', 'build_stamp', 'root', 'cycle', 'window', 'basis', 'stages', 'nodes',
    'links', 'totals', 'coverage', 'reconciliation'];
  const GROUPABLE = new Set(['donor', 'committee', 'candidate']);
  const MIN_SHARE = 0.01;
  const BALANCE_ORDER = ['prior_cash', 'operating', 'refunded', 'unspent', 'kept', 'uncounted', 'untraced', 'unreported'];
  const CANDIDATE_KINDS = new Set(['ie_support', 'ie_oppose', 'ie_unknown', 'contribution']);

  /* ------------------------------------------------------------------ money */
  const toCents = value => Math.round(Number(value) * 100);
  const isMoney = value => {
    if (value === null || value === undefined || value === '' || typeof value === 'boolean') return false;
    const number = Number(value);
    if (!Number.isFinite(number)) return false;
    const scaled = number * 100;
    return Math.abs(scaled - Math.round(scaled)) < 1e-3;
  };
  const dollars = cents => cents / 100;


  /* ------------------------------------------------------------- the words */
  // Short money for labels ("$68.5M"), and the lines under a node's name: what the board
  // prints beneath it. Shared by the layout (to reserve room) and the renderer (to draw).
  function short(value) {
    const size = Math.abs(value), sign = value < 0 ? '-' : '';
    // Tenths of a million rounded half up on the exact number, as the board prints $54.6M
    // for $54,550,000 (toFixed on the binary 54.55 would say 54.5).
    if (size >= 999950) return `${sign}$${(Math.round(size / 1e5) / 10).toFixed(1)}M`;
    if (size >= 1000) return `${sign}$${Math.round(size / 1000).toLocaleString('en-US')}K`;
    return `${sign}$${Math.round(size).toLocaleString('en-US')}`;
  }
  const HELD = {bitcoin: 'bitcoin', 'USD coin': 'USD coin', ether: 'ether', tether: 'tether', solana: 'solana', stock: 'stock'};
  const heldWords = kinds => (kinds || []).map(kind => HELD[kind] || kind).join(' and ') || 'donated assets';
  const INFLOW_STUBS = new Set(['external', 'prior_cash', 'return_in']);

  function sublines(node, stubs, stubValue) {
    const lines = [];
    // A committee the answer has no name for says so, and its FEC ID is the small line under it (B4).
    if (node.unnamed && node.committee_id) lines.push(node.committee_id);
    if (node.balance === 'unspent' && (node.held || 0) > 0)
      lines.push(`cash ${short(node.cash || 0)} + ${heldWords(node.held_kinds)} ${short(node.held)}`);
    else if (node.kind === 'donor' && (node.held || 0) > 0)
      lines.push(`incl. ${short(node.held)} ${heldWords(node.held_kinds)} still held`);
    const mine = stubs.filter(stub => stub.attach === node.id);
    // Carryover (b): starting cash of a committee whose gate the previous cycle's transfers opened.
    for (const stub of mine.filter(item => INFLOW_STUBS.has(item.stub))) {
      lines.push(`${stub.label} ${short(stubValue(stub))}`);
      // A line of its own, short enough never to be cut: the cycle words are the measurement's.
      if (stub.carryover) {
        const span = /\d{4}-\d{4}/.exec(String(stub.carryover));
        lines.push(span ? `carried over from ${span[0]}` : 'carried over');
      }
    }
    // F12: what a committee paid before the network's money reached it, as a line under its
    // name - disclosed, never a ribbon (the renderer draws no path for it).
    for (const stub of mine.filter(item => item.stub === 'before_gate'))
      lines.push(`${stub.label} ${short(stubValue(stub))}`);
    for (const kind of ['return', 'onward']) {
      const group = mine.filter(item => item.stub === kind);
      if (group.length === 1) lines.push(`${group[0].label} ${short(stubValue(group[0]))}`);
      else if (group.length > 1)
        lines.push(`${kind === 'return' ? 'returned to' : 'passed on to'} ${group.length} committees `
          + short(group.reduce((sum, item) => sum + stubValue(item), 0)));
    }
    return lines;
  }

  // The one line a node shows under its name and amount (FL6): what it holds when it holds
  // donated assets (board #44's first line), else its largest addition, word for word as
  // sublines() writes it (other donors, cash from before this window, back from, not counted,
  // returned to, passed on to). Every line stays in the node's tooltip and its accessible name:
  // none is dropped, only moved off the drawing.
  function secondary(node, lines, stubs, stubValue) {
    if (!lines.length) return '';
    if (node.unnamed && node.committee_id && lines[0] === node.committee_id) return node.committee_id;
    if ((node.balance === 'unspent' || node.kind === 'donor') && (node.held || 0) > 0) return lines[0];
    let best = null;
    for (const stub of stubs) {
      if (stub.attach !== node.id) continue;
      const line = `${stub.label} ${short(stubValue(stub))}`;
      if (lines.includes(line) && (!best || stubValue(stub) > best.value)) best = {line, value: stubValue(stub)};
    }
    return best ? best.line : lines[0];
  }

  // Width of a text at a size in px: the page's measured fitter (MentatTheme.measureText,
  // passed by the renderer) or, in node:test, the renderer's own 0.56 em per character.
  const estimateWidth = (text, size) => String(text).length * size * 0.56;

  // A label's words in lines of the room it has, breaking between words only: a word longer
  // than the room keeps its own line and the renderer ends it with an ellipsis (FL3).
  function wordWrap(text, width, size, measure) {
    const lines = [];
    let line = '';
    for (const word of String(text).split(/\s+/).filter(Boolean)) {
      const next = line ? line + ' ' + word : word;
      if (line && measure(next, size) > width) { lines.push(line); line = word; } else line = next;
    }
    if (line) lines.push(line);
    return lines.length ? lines : [''];
  }

  // At most `count` lines: the words that do not fit join the last line, which the renderer
  // fits to the room with an ellipsis (the full name is the node's tooltip title).
  function clampLines(lines, count) {
    if (lines.length <= count) return lines.slice();
    return lines.slice(0, Math.max(count, 1) - 1).concat([lines.slice(Math.max(count, 1) - 1).join(' ')]);
  }

  // A node's label at each level of detail, richest first. Level 0: the name in up to
  // labelLines lines, the amount, the one secondary line; 1: without the secondary line;
  // 2: the name in one line; 3: the name with the amount beside it, one line; 4: no label
  // (the bar's tooltip still says it all). `block` is the height each takes.
  function labelLevels(node, room, opts, measure) {
    const words = wordWrap(labelText(node), room, opts.labelSize, measure);
    const named = clampLines(words, Math.max(1, opts.labelLines));
    const one = clampLines(words, 1);
    const amount = opts.amounts !== false ? opts.labelLine : 0;
    const sub = node.subline ? opts.subLine : 0;
    return [
      {name: named, amount: 'line', sub: !!node.subline, block: named.length * opts.labelLine + amount + sub},
      {name: named, amount: 'line', sub: false, block: named.length * opts.labelLine + amount},
      {name: one, amount: 'line', sub: false, block: opts.labelLine + amount},
      {name: one, amount: 'inline', sub: false, block: opts.labelLine},
      {name: [], amount: 'none', sub: false, block: 0}];
  }

  function labelText(node) {
    return node.kind === 'candidate' && !node.outcome && node.direction ? `${node.label} \u00b7 ${node.direction}` : node.label;
  }

  // The amount under a pooled committee's name is what reached it through the network (board
  // #44: "Defend American Jobs $54.6M", "Other donors $5.8M" beneath); every other node shows
  // its own value.
  function amountOf(node, inflowFromStubs) {
    if (node.kind === 'committee' && !node.root && node.pooled && inflowFromStubs > 0 && node.value_in > inflowFromStubs)
      return Math.round((node.value_in - inflowFromStubs) * 100) / 100;
    return node.value;
  }

  /* --------------------------------------------------------------- contract */
  // The same rules, and the same sentences, as flow_data.contract_problems.
  function validate(payload, status) {
    status = status || 200;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return ['the answer is not an object'];
    if (status === 409) {
      const problems = typeof payload.error === 'string' && payload.error ? [] : ['a refusal states no error'];
      if (!REFUSALS.has(payload.reason)) problems.push('a refusal names no known reason');
      if (payload.reason === 'reconciliation' && (payload.reconciliation || {}).equal !== false)
        problems.push('a reconciliation refusal must carry the unequal totals');
      return problems;
    }
    const problems = TOP_FIELDS.filter(field => !(field in payload)).map(field => `the answer has no ${field}`);
    if (problems.length) return problems;
    if (!['reported', 'attributed'].includes(payload.basis)) problems.push('basis must be reported or attributed');
    const recon = payload.reconciliation;
    if (!recon || recon.equal !== true) problems.push('a drawn flow must reconcile with the evidence network (equal: true)');
    else if (toCents(recon.evidence_network_total) !== toCents(recon.flow_total))
      problems.push('reconciliation says equal but its totals differ');
    const coverage = payload.coverage;
    if (!coverage || typeof coverage !== 'object' || !['through', 'per_committee', 'unreached'].every(key => key in coverage))
      problems.push('coverage must state through, per_committee and unreached');
    const window = payload.window;
    if (!window || typeof window !== 'object' || !('start' in window) || !('end' in window))
      problems.push('window must state start and end');
    const stages = payload.stages;
    if (!Array.isArray(stages) || !stages.length) return problems.concat(['stages must be a non-empty list']);
    const last = stages.length - 1;
    if (stages.some((stage, index) => !stage || stage.index !== index)) problems.push('stages must be indexed 0..n-1 in order');
    if (!Array.isArray(payload.nodes) || !Array.isArray(payload.links)) return problems.concat(['nodes and links must be lists']);
    const byId = new Map();
    for (const node of payload.nodes) {
      const id = node && typeof node === 'object' ? node.id : undefined;
      if (typeof id !== 'string' || !id || byId.has(id)) { problems.push(`node ${JSON.stringify(id)} has no unique id`); continue; }
      byId.set(id, node);
      if (!NODE_KINDS.has(node.kind)) problems.push(`node ${id} has an unknown kind ${JSON.stringify(node.kind)}`);
      if (!Number.isInteger(node.stage) || node.stage < 0 || node.stage > last) problems.push(`node ${id} sits on no stage`);
      if (typeof node.label !== 'string') problems.push(`node ${id} has no label`);
      for (const field of ['value_in', 'value_out'])
        if (!isMoney(node[field]) || Number(node[field]) < 0)
          problems.push(`node ${id} ${field} is not a whole-cent amount of zero or more`);
      if (node.kind === 'candidate') {
        if (node.stage !== last) problems.push(`candidate ${id} is not on the last stage`);
        if (!DIRECTIONS.has(node.direction)) problems.push(`candidate ${id} states no direction`);
      }
      if (node.kind === 'donor' && node.stage > 0 && !node.stub)
        problems.push(`donor ${id} is drawn as a column node past the first stage (external money is a stub)`);
    }
    const sumsIn = new Map(), sumsOut = new Map();
    const add = (map, key, value) => map.set(key, (map.get(key) || 0) + value);
    for (const link of payload.links) {
      if (!link || typeof link !== 'object') { problems.push('a link is not an object'); continue; }
      const source = byId.get(link.source), target = byId.get(link.target);
      const where = `${link.source} -> ${link.target}`;
      if (!source || !target) { problems.push(`link ${where} names a node that does not exist`); continue; }
      if (!isMoney(link.value) || Number(link.value) <= 0) { problems.push(`link ${where} is not a positive whole-cent amount`); continue; }
      if (!LINK_KINDS.has(link.kind)) problems.push(`link ${where} has an unknown kind ${JSON.stringify(link.kind)}`);
      if (!Number.isInteger(link.rows) || link.rows < 0) problems.push(`link ${where} states no row count`);
      if ('date_unverified' in link && typeof link.date_unverified !== 'boolean')
        problems.push(`link ${where} date_unverified is not true or false`);
      if ((link.kind === 'uncounted') !== (target.stub === 'before_gate'))
        problems.push(`link ${where}: money paid before the network's arrived is drawn as a flow `
          + '(it is a before_gate stub, and only that)');
      if (source.stub || target.stub) {
        const attached = source.stub ? source : target, other = source.stub ? target : source;
        if (attached.attach !== other.id) problems.push(`stub link ${where} does not connect the stub to the node it is attached to`);
      } else if (target.stage <= source.stage) {
        problems.push(`link ${where} runs backwards or within a column (a loop is a stub)`);
      }
      add(sumsOut, source.id, toCents(link.value));
      add(sumsIn, target.id, toCents(link.value));
    }
    for (const [id, node] of byId) {
      if (!isMoney(node.value_in) || !isMoney(node.value_out)) continue;
      const inflow = sumsIn.get(id) || 0, outflow = sumsOut.get(id) || 0;
      if (Math.abs(toCents(node.value_in) - inflow) > 0 || Math.abs(toCents(node.value_out) - outflow) > 0)
        problems.push(`node ${id} values are not the sums of its links`);
      if (inflow > 0 && outflow > 0 && inflow !== outflow)
        problems.push(`node ${id} does not balance: in ${dollars(inflow)} out ${dollars(outflow)}`);
      if (node.stub && !byId.has(node.attach)) problems.push(`stub ${id} is attached to no node`);
      // Carryover (b): a carried-over committee says so in the measurement's words, and only it.
      if (node.stub) {
        if (node.carryover && (((byId.get(node.attach) || {}).carryover) || {}).label !== node.carryover)
          problems.push(`stub ${id} says carried over but its committee is not`);
      } else if ((node.gate_source === 'carryover') !== !!(node.carryover && typeof node.carryover === 'object' && node.carryover.label))
        problems.push(`node ${id} is carried over without the measurement's label, or labelled without being`);
    }
    const rootInfo = payload.root;
    if (rootInfo && rootInfo.kind === 'committee') {
      const rootNode = byId.get('committee:' + rootInfo.id);
      const totalIn = (payload.totals || {}).in;
      if (!rootNode) problems.push('the root committee is not drawn');
      else {
        // A 'Not in the summary' stub is disclosed spending the summary cannot carry, not intake.
        const disclosed = payload.links.filter(link => link && link.target === rootNode.id
          && (byId.get(link.source) || {}).balance === 'unreconciled').reduce((sum, link) => sum + toCents(link.value), 0);
        if (!isMoney(totalIn) || (sumsIn.get(rootNode.id) || 0) - disclosed !== toCents(totalIn))
          problems.push('the root takes in more or less than totals.in (money counted twice or lost)');
      }
    }
    return problems;
  }

  /* ------------------------------------------------------------ top N rule */
  const valueOf = node => Math.max(node.vin || 0, node.vout || 0);

  function measure(nodes, links) {
    const byId = new Map(nodes.map(node => [node.id, node]));
    for (const node of nodes) { node.vin = 0; node.vout = 0; }
    for (const link of links) {
      byId.get(link.source).vout += link.cents;
      byId.get(link.target).vin += link.cents;
    }
    return byId;
  }

  function otherLabel(members) {
    const kinds = new Set(members.map(member => member.kind));
    if (kinds.size === 1 && kinds.has('donor') && members.every(member => member.industry))
      return `Other ${String(members[0].industry).toLowerCase()} companies`;
    if (kinds.size === 1 && kinds.has('donor'))
      return members.every(member => member.contribution !== false) ? 'All other contributors' : 'All other sources';
    if (kinds.size === 1 && kinds.has('candidate')) return 'Other candidates';
    if (kinds.size === 1 && kinds.has('committee')) return 'Other committees';
    return 'Other';
  }

  const byLabel = (a, b) => (a.label < b.label ? -1 : a.label > b.label ? 1 : 0) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

  // The top N rule for one column, flow_data.group's: the nodes that join "Other" (none when
  // nothing is grouped). group() applies it to an answer's columns; Chart Studio applies it
  // to a worksheet flow's stage members before it draws them (studio-flow.js).
  function otherMembers(column, top, minShare) {
    minShare = minShare === undefined ? MIN_SHARE : minShare;
    if (!top) return [];
    const total = column.reduce((sum, node) => sum + valueOf(node), 0);
    const floor = total * minShare;
    const groupable = column.filter(node => GROUPABLE.has(node.kind) && !node.root && !node.fixed);
    const fixed = column.length - groupable.length;
    const ordered = groupable.slice().sort((a, b) => valueOf(b) - valueOf(a) || byLabel(a, b));
    if (column.length <= top && ordered.every(node => valueOf(node) >= floor)) return [];
    const room = Math.max(top - fixed - 1, 0);
    const named = [];
    for (const node of ordered.slice(0, room)) {
      if (valueOf(node) < floor) break;
      named.push(node);
    }
    const rest = ordered.slice(named.length);
    return rest.length < 2 ? [] : rest;
  }

  // flow_data.group, line for line (the parity test runs both on contracts/flow.json).
  function group(nodes, links, top, minShare) {
    minShare = minShare === undefined ? MIN_SHARE : minShare;
    if (!top) return {nodes, links};
    const remap = new Map();
    const others = [];
    const stages = [...new Set(nodes.map(node => node.stage))].sort((a, b) => a - b);
    for (const stage of stages) {
      const column = nodes.filter(node => node.stage === stage && !node.stub);
      const rest = otherMembers(column, top, minShare);
      if (!rest.length) continue;
      const id = `other:${stage}`;
      others.push({id, stage, kind: 'bucket', bucket: 'other', label: otherLabel(rest), member_count: rest.length,
        members: rest.slice(0, 200).map(node => Object.assign({id: node.id, label: node.label, kind: node.kind,
          value: dollars(valueOf(node))}, node.direction ? {direction: node.direction} : {}, node.party ? {party: node.party} : {},
          node.carryover && typeof node.carryover === 'object' ? {carryover: node.carryover.label} : {}))});
      for (const node of rest) remap.set(node.id, id);
    }
    if (!remap.size) return {nodes, links};
    return mergeGroups(nodes, links, remap, others);
  }

  // The merge both rules share (flow_data.group's, and the small-transfers fold below): the nodes in
  // `remap` become the bucket they map to, a stub follows its committee into the bucket, and the links
  // that now join the same two nodes become one. Money is only ever added, never recomputed.
  function mergeGroups(nodes, links, remap, others) {
    const kept = nodes.filter(node => !remap.has(node.id));
    const stubIds = new Map();
    const result = [];
    const seen = new Set();
    for (const node of kept) {
      if (node.stub && remap.has(node.attach)) {
        const attach = remap.get(node.attach);
        const key = stubKey(node, attach);
        stubIds.set(node.id, key);
        if (!seen.has(key)) {
          seen.add(key);
          const other = others.find(item => item.id === attach);
          const merged = Object.assign({}, node, {id: key, attach, stage: other.stage});
          if (node.stub === 'external') { delete merged.committee_id; delete merged.fec_url; }
          delete merged.carryover; delete merged.gate;         // each belongs to one committee
          result.push(merged);
        }
        continue;
      }
      result.push(node);
    }
    result.push(...others);
    const target = id => remap.get(id) || stubIds.get(id) || id;
    const merged = new Map();
    for (const link of links) {
      const source = target(link.source), sink = target(link.target);
      const key = `${source}\u0000${sink}\u0000${link.kind}\u0000${link.key == null ? '' : link.key}`;
      if (!merged.has(key)) {
        merged.set(key, Object.assign({}, link, {source, target: sink, evidence: (link.evidence || []).slice()}));
        continue;
      }
      const entry = merged.get(key);
      entry.cents += link.cents;
      entry.rows = (entry.rows || 0) + (link.rows || 0);
      entry.evidence = entry.evidence.concat(link.evidence || []).slice(0, 12);
      if (entry.committee !== link.committee) delete entry.committee;
      if (link.date_unverified || entry.date_unverified) entry.date_unverified = true;
    }
    return {nodes: result, links: [...merged.values()]};
  }

  // The id a stub takes once its committee is inside a bucket (one stub per kind and counterparty).
  const stubKey = (node, attach) => (['return', 'onward', 'return_in'].includes(node.stub)
    ? `stub:${node.stub}:${attach}:${node.committee_id || node.label}` : `stub:${node.stub}:${attach}:${node.label}`);

  /* ------------------------------------------------------- arranging for display */
  // What a view does to an answer before it draws it, and never to its money (lane Z, 2026-10-15):
  //  - the small transfers of a "Passed to" column (a committee under MIN_SHARE of the column, or at
  //    nothing) fold into one "Small transfers (n)" node per column;
  //  - a bucket (Other, Small transfers, a first-recipient group) unfolds in place into its top members
  //    and a smaller residual bucket.
  // Both only add up or take apart what the answer already says: a folded bucket's links are the sums
  // of its members' (mergeGroups), an unfolded one's are the members' own. Unfolding a bucket the
  // answer built (Other) needs every member's links, which the answer does not carry; they come from
  // the same query at top=0 (`spec.full`) and are used only if they add up, to the cent, to the links
  // the bucket has (a bucket whose members do not reconcile is refused, not drawn).
  const SMALL_STAGE = 2;                                 // "Passed to" and beyond; the first recipients keep their own rule
  const isServerBucket = node => node.kind === 'bucket' && Array.isArray(node.members) && node.bucket !== 'small'
    && node.member_count > 1;

  function memberOf(node, cents) {
    return Object.assign({id: node.id, label: node.label, kind: node.kind, value: dollars(cents === undefined ? valueOf(node) : cents)},
      node.committee_id ? {committee_id: node.committee_id} : {}, node.unnamed ? {unnamed: true} : {});
  }

  // Zero and below-MIN_SHARE committees of a column into "Small transfers (n)". `keep` are nodes that must
  // stay as they are (the members of an unfolded bucket); `open` maps a small bucket's id to how many of
  // its members to show. Needs measured nodes (vin/vout).
  function foldSmall(nodes, links, spec) {
    spec = spec || {};
    const minShare = spec.minShare === undefined ? MIN_SHARE : spec.minShare;
    const keep = spec.keep || new Set(), open = spec.open || new Map();
    const remap = new Map(), others = [], groups = [];
    const stages = [...new Set(nodes.map(node => node.stage))].filter(stage => stage >= SMALL_STAGE).sort((a, b) => a - b);
    for (const stage of stages) {
      const column = nodes.filter(node => node.stage === stage && !node.stub);
      const floor = column.reduce((sum, node) => sum + valueOf(node), 0) * minShare;
      if (!(floor > 0)) continue;
      let small = column.filter(node => node.kind === 'committee' && !node.root && !keep.has(node.id) && valueOf(node) < floor)
        .sort((a, b) => valueOf(b) - valueOf(a) || byLabel(a, b));
      const id = `small:${stage}`;
      let shown = [];
      if (open.has(id)) { shown = small.slice(0, open.get(id)); small = small.slice(shown.length); }
      if (small.length < 2) { shown = shown.concat(small); small = []; }
      if (open.has(id)) groups.push({id, stage, shown: shown.map(node => node.id), residual: small.length > 0});
      for (const node of shown) node.group = id;
      if (!small.length) continue;
      others.push(Object.assign({id, stage, kind: 'bucket', bucket: 'small', label: `Small transfers (${small.length})`,
        member_count: small.length, members: small.slice(0, 200).map(node => memberOf(node))}, open.has(id) ? {group: id} : {}));
      for (const node of small) remap.set(node.id, id);
    }
    if (!remap.size) return {nodes, links, groups};
    return Object.assign(mergeGroups(nodes, links, remap, others), {groups});
  }

  const refusal = (message, detail) => Object.assign(new Error(message), {detail});

  // Unfold one bucket the answer built into its top `count` members and a residual bucket. `U` is the
  // same query at top=0. Returns {nodes, links, shown} or throws an Error whose message says why not.
  function expandBucket(P, U, id, count, allowDrop) {
    const pById = new Map(P.nodes.map(node => [node.id, node]));
    const bucket = pById.get(id);
    if (!bucket || !isServerBucket(bucket)) throw new Error('it has no members to unfold');
    const uNodes = U.nodes.map(node => Object.assign({}, node));
    const uLinks = U.links.map(link => Object.assign({}, link, {cents: toCents(link.value)})).filter(link => link.cents > 0);
    const uById = measure(uNodes, uLinks);
    const buckets = P.nodes.filter(isServerBucket);
    const category = new Map();                              // the learned rule: which bucket holds a category
    const listed = new Map();                                // the members each bucket names (at most 200 of them)
    for (const group of buckets)
      for (const member of group.members) {
        listed.set(member.id, group.id);
        listed.set('committee:' + member.id, group.id);
        const node = uById.get(member.id) || uById.get('committee:' + member.id);
        if (node && node.category) category.set(node.category, category.has(node.category) && category.get(node.category) !== group.id ? null : group.id);
      }
    const homes = new Map();
    const home = nodeId => {                                 // the node a full-answer node is drawn as in the grouped answer
      if (homes.has(nodeId)) return homes.get(nodeId);
      const node = uById.get(nodeId);
      let found = null;
      if (pById.has(nodeId) && !isServerBucket(pById.get(nodeId))) found = nodeId;
      else if (node && !node.stub && listed.has(nodeId)) found = listed.get(nodeId);
      else if (node && !node.stub) {
        const here = buckets.filter(group => group.stage === node.stage);
        if (here.length === 1) found = here[0].id;
        else if (here.length > 1 && node.category && category.get(node.category)) found = category.get(node.category);
      }
      homes.set(nodeId, found);
      return found;
    };
    const members = uNodes.filter(node => !node.stub && !pById.has(node.id) && home(node.id) === id)
      .sort((a, b) => valueOf(b) - valueOf(a) || byLabel(a, b));
    if (members.length < 2) throw new Error('the answer does not carry its members');
    const mine = nodeId => {                                 // is this full-answer node inside the unfolded bucket
      const node = uById.get(nodeId);
      return Boolean(node) && !pById.has(nodeId) && !node.stub && home(nodeId) === id;
    };
    const stubOf = nodeId => { const node = uById.get(nodeId); return node && node.stub ? node : null; };
    const insideStub = stub => mine(stub.attach);
    // The coarse key of a link: both ends as the grouped answer draws them (the bucket whole).
    const coarse = nodeId => {
      const stub = stubOf(nodeId);
      if (stub) {
        const at = home(stub.attach);
        return at === null ? null : (at === stub.attach ? stub.id : stubKey(stub, at));
      }
      return home(nodeId);
    };
    const keyOf = (source, sink, link) => `${source}\u0000${sink}\u0000${link.kind}\u0000${link.key == null ? '' : link.key}`;
    const removed = new Map();
    const inP = nodeId => nodeId === id || (pById.get(nodeId) || {}).attach === id;
    for (const link of P.links) if (inP(link.source) || inP(link.target)) {
      const key = keyOf(link.source, link.target, link);
      removed.set(key, (removed.get(key) || 0) + link.cents);
    }
    const added = new Map(), wanted = new Map();
    for (const link of uLinks) {
      const touches = mine(link.source) || mine(link.target)
        || (stubOf(link.source) && insideStub(stubOf(link.source))) || (stubOf(link.target) && insideStub(stubOf(link.target)));
      if (!touches) continue;
      const source = coarse(link.source), sink = coarse(link.target);
      if (source === null || sink === null) {
        if (allowDrop) continue;                             // an end the grouped answer does not draw (it follows fewer committees)
        throw refusal('a member’s links lead somewhere the answer does not draw', `${link.source} -> ${link.target}`);
      }
      const key = keyOf(source, sink, link);
      if (!removed.has(key)) {                               // a link the grouped answer does not draw: the industry flow follows
        if (allowDrop) continue;                             // fewer first recipients at Top N than at top=0 and stops at the group
        throw refusal('its members have links the group does not', `${source} -> ${sink} (${link.kind})`);
      }
      wanted.set(key, (wanted.get(key) || 0) + link.cents);
      added.set(key, (added.get(key) || []).concat(link));
    }
    for (const [key, cents] of removed)
      if (wanted.get(key) !== cents)
        throw refusal('its members’ own links are not all in the data', `${key.replace(/\0/g, ' -> ')}: ${wanted.get(key) || 0} of ${cents} cents`);
    // The top members are ranked by what the grouped answer draws for them (the links kept), not by what the
    // full answer holds: an answer that follows more committees gives a member money the group never had.
    const kept = new Map();
    for (const list of added.values())
      for (const link of list)
        for (const [end, side] of [[link.source, 'out'], [link.target, 'in']]) {
          if (!mine(end)) continue;
          const entry = kept.get(end) || {in: 0, out: 0};
          entry[side] += link.cents;
          kept.set(end, entry);
        }
    const worth = node => { const entry = kept.get(node.id); return entry ? Math.max(entry.in, entry.out) : 0; };
    members.sort((a, b) => worth(b) - worth(a) || byLabel(a, b));
    let take = Math.max(1, Math.min(count, members.length));
    if (members.length - take < 2) take = members.length;
    const shown = new Set(members.slice(0, take).map(node => node.id));
    const rest = members.slice(take);
    // Draw it: the top members as they are, the rest as one bucket, every link pointing at the right ones.
    const draw = nodeId => {
      if (shown.has(nodeId)) return nodeId;
      const stub = stubOf(nodeId);
      if (stub) {
        if (shown.has(stub.attach)) return nodeId;
        const at = home(stub.attach);
        return at === stub.attach ? nodeId : stubKey(stub, at);
      }
      return home(nodeId);
    };
    const nodes = P.nodes.filter(node => node.id !== id && node.attach !== id)
      .concat(members.slice(0, take).map(node => Object.assign({}, node, {group: id, stage: bucket.stage})));
    if (rest.length)
      nodes.push(Object.assign({}, bucket, {label: `+ ${rest.length} more`, member_count: rest.length, group: id,
        members: rest.slice(0, 200).map(node => memberOf(node, worth(node)))}));
    const seen = new Set(nodes.map(node => node.id));
    const links = P.links.filter(link => !inP(link.source) && !inP(link.target));
    const byKey = new Map();
    for (const list of added.values()) for (const link of list) {
      const source = draw(link.source), sink = draw(link.target);
      for (const end of [link.source, link.target]) {
        const stub = stubOf(end);
        if (!stub || !insideStub(stub)) continue;
        const stubId = draw(end);
        if (seen.has(stubId)) continue;
        seen.add(stubId);
        const attach = shown.has(stub.attach) ? stub.attach : id;
        const made = Object.assign({}, stub, {id: stubId, attach, stage: (uById.get(attach) || bucket).stage});
        if (attach === id) {
          delete made.carryover; delete made.gate;
          if (stub.stub === 'external') { delete made.committee_id; delete made.fec_url; }
        }
        nodes.push(made);
      }
      const key = keyOf(source, sink, link);
      if (!byKey.has(key)) { byKey.set(key, Object.assign({}, link, {source, target: sink, evidence: (link.evidence || []).slice()})); continue; }
      const entry = byKey.get(key);
      entry.cents += link.cents;
      entry.rows = (entry.rows || 0) + (link.rows || 0);
      entry.evidence = entry.evidence.concat(link.evidence || []).slice(0, 12);
      if (entry.committee !== link.committee) delete entry.committee;
      if (link.date_unverified) entry.date_unverified = true;
    }
    return {nodes, links: links.concat([...byKey.values()]), shown: [...shown], residual: rest.length > 0};
  }

  // The whole arrangement. spec: {small: true, minShare, open: {id: count}, keep: [ids never folded], full: the top=0 answer}. Returns
  // {payload, groups: [{id, stage, shown, residual}], refused: {id: reason}, needs: [ids waiting for spec.full]}.
  // The payload has the answer's shape (every node and link carries whole-cent values summing to its links),
  // so validate() runs on it as on the answer.
  function arrange(payload, spec) {
    spec = spec || {};
    const open = new Map(Object.entries(spec.open || {}).filter(([, count]) => count > 0));
    const known = new Set(payload.nodes.map(node => node.id));
    let state = {nodes: payload.nodes.map(node => Object.assign({}, node)),
      links: payload.links.map(link => Object.assign({}, link, {cents: toCents(link.value)}))
        .filter(link => known.has(link.source) && known.has(link.target) && link.cents > 0)};
    // spec.keep: nodes the reader is looking at (the focused committee, a pinned node) are never folded away.
    const groups = [], refused = {}, details = {}, needs = [], keep = new Set(spec.keep || []);
    for (const [id, count] of open) {
      if (id.startsWith('small:') || !state.nodes.some(node => node.id === id)) continue;
      if (!spec.full) { needs.push(id); continue; }
      try {
        const done = expandBucket(state, spec.full, id, count, (payload.root || {}).kind === 'industry');
        state = {nodes: done.nodes, links: done.links};
        for (const shown of done.shown) keep.add(shown);
        const first = state.nodes.find(item => item.group === id);
        groups.push({id, stage: first ? first.stage : 0, shown: done.shown, residual: done.residual});
      } catch (error) { refused[id] = error.message; if (error.detail) details[id] = error.detail; }
    }
    measure(state.nodes, state.links);
    if (spec.small) {
      const folded = foldSmall(state.nodes, state.links, {minShare: spec.minShare, keep,
        open: new Map([...open].filter(([id]) => id.startsWith('small:')))});
      state = {nodes: folded.nodes, links: folded.links};
      groups.push(...folded.groups);
      measure(state.nodes, state.links);
    }
    // Whole-cent values from the links, as the answer carries them.
    for (const node of state.nodes) { node.value_in = dollars(node.vin); node.value_out = dollars(node.vout); delete node.vin; delete node.vout; }
    for (const link of state.links) { link.value = dollars(link.cents); delete link.cents; }
    return {payload: Object.assign({}, payload, {nodes: state.nodes, links: state.links}), groups, refused, details, needs};
  }

  /* ----------------------------------------------------------------- order */
  function rank(node) {
    if (node.kind === 'balance') return 3;
    if (node.bucket === 'other') return 2;
    if (node.kind === 'bucket') return 1;
    return 0;
  }

  function sortColumn(column, order) {
    return column.slice().sort((a, b) => {
      const byRank = rank(a) - rank(b);
      if (byRank) return byRank;
      if (a.kind === 'balance' && b.kind === 'balance')
        return BALANCE_ORDER.indexOf(a.balance) - BALANCE_ORDER.indexOf(b.balance) || byLabel(a, b);
      if (a.sequence !== undefined && b.sequence !== undefined && a.sequence !== b.sequence)
        return a.sequence - b.sequence;         // the industry flow's outcomes keep the board's order
      if (order === 'alpha' && rank(a) === 0) return byLabel(a, b);
      return valueOf(b) - valueOf(a) || byLabel(a, b);
    });
  }

  /* ---------------------------------------------------------------- ribbons */
  function cubicPath(xs, ys0, ys1, xt, yt0, yt1) {
    const xm = (xs + xt) / 2;
    const f = number => Math.round(number * 100) / 100;
    return `M${f(xs)},${f(ys0)}C${f(xm)},${f(ys0)} ${f(xm)},${f(yt0)} ${f(xt)},${f(yt0)}`
      + `L${f(xt)},${f(yt1)}C${f(xm)},${f(yt1)} ${f(xm)},${f(ys1)} ${f(xs)},${f(ys1)}Z`;
  }

  // Tableau's sigmoid: T from -6 to 6 in 49 points, y = y0 + (y1 - y0) / (1 + e^-T).
  function sigmoidPoints(xs, y0, xt, y1) {
    const points = [];
    for (let step = 0; step < 49; step += 1) {
      const t = -6 + step * 0.25;
      points.push([xs + (xt - xs) * (t + 6) / 12, y0 + (y1 - y0) / (1 + Math.exp(-t))]);
    }
    return points;
  }

  function sigmoidPath(xs, ys0, ys1, xt, yt0, yt1) {
    const f = number => Math.round(number * 100) / 100;
    const top = sigmoidPoints(xs, ys0, xt, yt0);
    const bottom = sigmoidPoints(xs, ys1, xt, yt1).reverse();
    return 'M' + top.map(([x, y]) => `${f(x)},${f(y)}`).join('L') + 'L'
      + bottom.map(([x, y]) => `${f(x)},${f(y)}`).join('L') + 'Z';
  }

  /* ----------------------------------------------------------------- layout */
  // barShare: the bars' floor, as a share of the fill scale (FL1). grow: a view that can scroll
  // lets the plot grow (up to maxGrow times) before any label is left to its tooltip.
  // firstLabels 'left' puts the first column's labels left of its bars in firstLabelWidth (#46).
  const DEFAULTS = {width: 1100, height: 520, nodeWidth: 12, minPadding: 10, maxPadding: 56, stubLength: 56,
    labelWidth: 230, labelLine: 16, subLine: 14, labelSize: 13, labelLines: 2, top: 0, minShare: MIN_SHARE,
    order: 'value', curve: 'cubic', barShare: 0.5, grow: false, maxGrow: 4, firstLabels: 'right', firstLabelWidth: 0,
    amounts: true, margin: {top: 34, right: 8, bottom: 10, left: 8}};

  function layout(payload, options) {
    const opts = Object.assign({}, DEFAULTS, options || {});
    opts.margin = Object.assign({}, DEFAULTS.margin, (options || {}).margin || {});
    const stages = (payload.stages || []).map(stage => Object.assign({}, stage));
    let nodes = (payload.nodes || []).map(node => Object.assign({}, node));
    let links = (payload.links || []).map((link, index) => Object.assign({}, link, {cents: toCents(link.value), index}));
    const known = new Set(nodes.map(node => node.id));
    links = links.filter(link => known.has(link.source) && known.has(link.target) && link.cents > 0);
    measure(nodes, links);
    if (opts.top) ({nodes, links} = group(nodes, links, opts.top, opts.minShare));
    const byId = measure(nodes, links);
    const count = Math.max(stages.length, 1 + Math.max(0, ...nodes.map(node => node.stage)));
    const left = opts.firstLabels === 'left' ? Math.max(0, Number(opts.firstLabelWidth) || 0) : 0;
    const innerWidth = opts.width - opts.margin.left - opts.margin.right - opts.labelWidth - left;
    const step = count > 1 ? (innerWidth - opts.nodeWidth) / (count - 1) : 0;
    const columns = [];
    for (let index = 0; index < count; index += 1) {
      const stage = stages[index] || {index, key: `stage${index}`, label: ''};
      columns.push({index, key: stage.key, label: stage.label, x: opts.margin.left + left + index * step, nodes: []});
    }
    for (const node of nodes) if (!node.stub) columns[node.stage].nodes.push(node);
    const plot = Math.max(0, opts.height - opts.margin.top - opts.margin.bottom);
    const stubList = nodes.filter(node => node.stub);
    const stubCents = new Map();
    for (const link of links) {
      if (byId.get(link.source).stub) stubCents.set(link.source, (stubCents.get(link.source) || 0) + link.cents);
      if (byId.get(link.target).stub) stubCents.set(link.target, (stubCents.get(link.target) || 0) + link.cents);
    }
    const stubValue = stub => dollars(stubCents.get(stub.id) || 0);
    const measureText = typeof opts.measure === 'function' ? opts.measure : estimateWidth;
    columns.forEach((column, at) => {
      const next = columns[at + 1];
      column.labelSide = at === 0 && left ? 'left' : 'right';
      column.labelWidth = column.labelSide === 'left' ? Math.max(40, left - 8)
        : Math.max(40, (next ? next.x : opts.width - opts.margin.right) - column.x - opts.nodeWidth - 16);
    });
    for (const node of nodes) {
      node.value = dollars(valueOf(node));
      node.value_in = dollars(node.vin);
      node.sublines = node.stub ? [] : sublines(node, stubList, stubValue);
      node.subline = node.stub ? '' : secondary(node, node.sublines, stubList, stubValue);
      const room = node.stub ? 0 : columns[node.stage].labelWidth - (node.kind === 'candidate' && node.party ? 16 : 0);
      node.levels = node.stub ? null : labelLevels(node, room, opts, measureText);
    }
    // FL1. The gaps between nodes take at most 35% of the plot, so bars always keep the rest.
    const gapOf = (column, height) => {
      const gaps = Math.max(column.nodes.length - 1, 0);
      return gaps ? Math.min(opts.minPadding, height * 0.35 / gaps) : 0;
    };
    const need = (column, k, levels, gap) => column.nodes.reduce((sum, node, at) =>
      sum + Math.max(valueOf(node) * k, node.levels[levels[at]].block), 0) + gap * Math.max(column.nodes.length - 1, 0);
    // The fill scale: the fullest column's bars fill the plot asked for, less its gaps. A plot
    // that grows (grow) gives its labels more room; its bars keep this scale.
    let fill = Infinity;
    for (const column of columns) {
      const total = column.nodes.reduce((sum, node) => sum + valueOf(node), 0);
      if (total) fill = Math.min(fill, Math.max(0, plot - gapOf(column, plot) * Math.max(column.nodes.length - 1, 0)) / total);
    }
    function plan(height) {
      if (!Number.isFinite(fill)) return {scale: 0, levels: columns.map(column => column.nodes.map(() => 0)), hidden: 0};
      // The largest scale at which every label fits whole; the bars never go below barShare of fill.
      let whole = fill;
      for (const column of columns) {
        if (!column.nodes.length) continue;
        const rich = column.nodes.map(() => 0), gap = gapOf(column, height);
        if (need(column, fill, rich, gap) <= height) continue;
        if (need(column, 0, rich, gap) > height) { whole = 0; continue; }
        let low = 0, high = fill;
        for (let round = 0; round < 40; round += 1) {
          const middle = (low + high) / 2;
          if (need(column, middle, rich, gap) <= height) low = middle; else high = middle;
        }
        whole = Math.min(whole, low);
      }
      const scale = Math.max(whole, Math.min(1, Math.max(0, Number(opts.barShare) || 0)) * fill);
      // Where the labels still overfill a column, they shed detail level by level, smallest node
      // first, and only a label taller than its own bar (a bar that holds its label costs no room).
      let hidden = 0;
      const levels = columns.map(column => {
        const chosen = column.nodes.map(() => 0), gap = gapOf(column, height);
        const order = column.nodes.map((node, at) => at)
          .sort((a, b) => valueOf(column.nodes[a]) - valueOf(column.nodes[b]) || a - b);
        for (let level = 0; level < 4 && need(column, scale, chosen, gap) > height + 1e-9; level += 1) {
          for (const at of order) {
            const node = column.nodes[at];
            if (chosen[at] !== level || node.levels[level].block <= valueOf(node) * scale) continue;
            chosen[at] = level + 1;
            if (need(column, scale, chosen, gap) <= height + 1e-9) break;
          }
        }
        hidden += chosen.filter(level => level === 4).length;
        return chosen;
      });
      return {scale, levels, hidden};
    }
    let available = plot;
    let chosen = plan(available);
    if (opts.grow) {
      const most = plot * Math.max(1, Number(opts.maxGrow) || 1);
      while (chosen.hidden && available < most) {
        available = Math.min(most, available * 1.15);
        chosen = plan(available);
      }
    }
    const scale = chosen.scale;          // px per cent, one scale for every width
    columns.forEach((column, index) => {
      const levelOf = new Map(column.nodes.map((node, at) => [node.id, chosen.levels[index][at]]));
      column.nodes = sortColumn(column.nodes, opts.order);
      const heights = column.nodes.map(node => valueOf(node) * scale);
      const labels = column.nodes.map(node => node.levels[levelOf.get(node.id)]);
      const slots = column.nodes.map((node, at) => Math.max(heights[at], labels[at].block));
      const used = slots.reduce((sum, height) => sum + height, 0);
      const gaps = Math.max(column.nodes.length - 1, 0);
      const gap = gapOf(column, available);
      const padding = gaps ? Math.min(Math.max(opts.maxPadding, gap), Math.max(gap, (available - used) / gaps)) : 0;
      const block = used + padding * gaps;
      let y = opts.margin.top + Math.max(0, (available - block) / 2);
      column.nodes.forEach((node, at) => {
        const label = labels[at];
        node.x0 = column.x;
        node.x1 = column.x + opts.nodeWidth;
        node.y0 = y;
        node.y1 = y + heights[at];
        node.order = at;
        node.label_width = column.labelWidth;
        node.label_side = column.labelSide;
        node.label_level = levelOf.get(node.id);
        node.label_text = label.name;
        node.label_amount = opts.amounts === false && label.amount !== 'none' ? 'none' : label.amount;
        node.subline_shown = label.sub ? node.subline : '';
        node.block = label.block;
        // The label sits at the middle of a bar taller than it, else at the top of its slot.
        node.label_y = y + Math.max(0, (heights[at] - label.block) / 2);
        node.label_lines = label.name.length + (node.label_amount === 'line' ? 1 : 0) + (label.sub ? 1 : 0);
        delete node.levels;
        y += slots[at] + padding;
      });
    });
    // Stubs sit beside the node they attach to; their end is placed with the ribbon below.
    const stubs = nodes.filter(node => node.stub);
    for (const stub of stubs) {
      const host = byId.get(stub.attach);
      stub.stage = host ? host.stage : stub.stage;
    }
    // Ribbon stacking: outgoing by target position, incoming by source position; stubs last.
    const position = node => (node.stub ? Infinity : node.y0);
    const outgoing = new Map(), incoming = new Map();
    for (const link of links) {
      if (!outgoing.has(link.source)) outgoing.set(link.source, []);
      if (!incoming.has(link.target)) incoming.set(link.target, []);
      outgoing.get(link.source).push(link);
      incoming.get(link.target).push(link);
    }
    const tie = (a, b) => a.index - b.index;
    for (const [id, list] of outgoing) {
      const node = byId.get(id);
      list.sort((a, b) => position(byId.get(a.target)) - position(byId.get(b.target)) || tie(a, b));
      let y = node.stub ? 0 : node.y0;
      for (const link of list) { link.width = link.cents * scale; link.sy0 = y; link.sy1 = y + link.width; y = link.sy1; }
    }
    for (const [id, list] of incoming) {
      const node = byId.get(id);
      list.sort((a, b) => position(byId.get(a.source)) - position(byId.get(b.source)) || tie(a, b));
      let y = node.stub ? 0 : node.y0;
      for (const link of list) { link.ty0 = y; link.ty1 = y + link.cents * scale; y = link.ty1; }
    }
    for (const link of links) {
      const source = byId.get(link.source), target = byId.get(link.target);
      if (source.stub) {               // feeding in from the side: a short straight ribbon into the node
        link.sy0 = link.ty0; link.sy1 = link.ty1;
        link.x0 = target.x0 - opts.stubLength; link.x1 = target.x0;
        Object.assign(source, {x0: link.x0, x1: link.x0, y0: link.ty0, y1: link.ty1});
      } else if (target.stub) {        // leaving to the side: loops, onward payments
        link.ty0 = link.sy0; link.ty1 = link.sy1;
        link.x0 = source.x1; link.x1 = source.x1 + opts.stubLength;
        Object.assign(target, {x0: link.x1, x1: link.x1, y0: link.sy0, y1: link.sy1});
      } else {
        link.x0 = source.x1; link.x1 = target.x0;
      }
      link.value = dollars(link.cents);
      link.id = `${link.source}→${link.target}→${link.kind}` + (link.key == null ? '' : `→${link.key}`);
      link.gradient = {x1: link.x0, x2: link.x1, from: link.source, to: link.target};
      link.path = (opts.curve === 'sigmoid' ? sigmoidPath : cubicPath)(link.x0, link.sy0, link.sy1, link.x1, link.ty0, link.ty1);
    }
    for (const node of nodes) {
      node.value_in = dollars(node.vin);
      node.value_out = dollars(node.vout);
      node.value = dollars(valueOf(node));
      const fromStubs = links.filter(link => link.target === node.id && byId.get(link.source).stub)
        .reduce((sum, link) => sum + link.cents, 0);
      node.amount = amountOf(node, dollars(fromStubs));
    }
    const totals = columns.map(column => dollars(column.nodes.reduce((sum, node) => sum + valueOf(node), 0)));
    columns.forEach((column, at) => { column.total = totals[at]; column.ids = column.nodes.map(node => node.id); delete column.nodes; });
    // A plot that grew (grow) is taller than asked; the drawing is as tall as its plot.
    return {width: opts.width, height: opts.height + (available - plot), scale: scale * 100, columns, nodes, links, stubs,
      plot: available, hidden_labels: chosen.hidden,
      options: {curve: opts.curve, top: opts.top, order: opts.order, nodeWidth: opts.nodeWidth, stubLength: opts.stubLength}};
  }

  /* ------------------------------------------------------------ route trace */
  // Everything upstream and downstream of a node or a ribbon: the route a hover lights.
  function route(result, start) {
    const nodes = new Set(), links = new Set();
    const into = new Map(), out = new Map();
    for (const link of result.links) {
      if (!into.has(link.target)) into.set(link.target, []);
      if (!out.has(link.source)) out.set(link.source, []);
      into.get(link.target).push(link);
      out.get(link.source).push(link);
    }
    const walk = (id, map, next) => {
      const queue = [id];
      while (queue.length) {
        const current = queue.shift();
        for (const link of map.get(current) || []) {
          if (links.has(link.id)) continue;
          links.add(link.id);
          const other = link[next];
          if (!nodes.has(other)) { nodes.add(other); queue.push(other); }
        }
      }
    };
    const link = result.links.find(item => item.id === start);
    const seeds = link ? [link.source, link.target] : [start];
    if (link) links.add(link.id);
    for (const seed of seeds) nodes.add(seed);
    if (link) { walk(link.source, into, 'source'); walk(link.target, out, 'target'); }
    else { walk(start, into, 'source'); walk(start, out, 'target'); }
    return {nodes, links};
  }

  /* --------------------------------------------------------------- colours */
  // Colours come only from MentatTheme.categorical (12 slots); never an invented palette.
  // Outcomes own slots 0 (support), 7 (oppose), 1 (contribution; the root) and 8
  // (unknown). No donor or committee takes one of them, so a ribbon coloured by its
  // outcome can never be read as a donor's money (pooled money is not traced money).
  const DONOR_SLOTS = [2, 3, 4, 6, 9, 10, 11, 5];
  const COMMITTEE_SLOTS = [5, 11, 10, 9, 6, 4, 3, 2];
  const DIRECTION_SLOT = {support: 0, oppose: 7, contribution: 1, unknown: 8};

  function colours(result, palette, neutral) {
    const colour = new Map();
    const donors = result.nodes.filter(node => node.kind === 'donor' && !node.stub).sort((a, b) => b.value - a.value || byLabel(a, b));
    donors.forEach((node, at) => colour.set(node.id, palette[DONOR_SLOTS[at % DONOR_SLOTS.length]]));
    const committees = result.nodes.filter(node => node.kind === 'committee' && !node.stub && !node.root)
      .sort((a, b) => a.stage - b.stage || b.value - a.value || byLabel(a, b));
    committees.forEach((node, at) => colour.set(node.id, palette[COMMITTEE_SLOTS[at % COMMITTEE_SLOTS.length]]));
    for (const node of result.nodes) {
      if (colour.has(node.id)) continue;
      if (node.root) colour.set(node.id, palette[1]);
      else if (node.kind === 'candidate') colour.set(node.id, palette[DIRECTION_SLOT[node.direction] ?? 8]);
      else if (node.bucket === 'own_ads') colour.set(node.id, palette[2]);
      else if (node.bucket === 'own_contributions') colour.set(node.id, palette[1]);
      else if (node.outcome && DIRECTION_SLOT[node.outcome] !== undefined) colour.set(node.id, palette[DIRECTION_SLOT[node.outcome]]);
      else colour.set(node.id, neutral);
    }
    return colour;
  }

  // The outcome a node's money reaches: its own colour when it is terminal, else the
  // outcome of its largest outgoing ribbon (Ribbons: Outcome).
  function outcomes(result, colour) {
    const out = new Map();
    for (const link of result.links) {
      if (!out.has(link.source)) out.set(link.source, []);
      out.get(link.source).push(link);
    }
    const memo = new Map();
    const reach = (id, depth) => {
      if (memo.has(id)) return memo.get(id);
      const list = (out.get(id) || []).slice().sort((a, b) => b.cents - a.cents);
      const value = !list.length || depth > 12 ? colour.get(id) : reach(list[0].target, depth + 1);
      memo.set(id, value);
      return value;
    };
    for (const node of result.nodes) reach(node.id, 0);
    return memo;
  }

  /* ------------------------------------------------------------ largest routes */
  function largestRoutes(payload, limit) {
    if (Array.isArray(payload.routes) && payload.routes.length) return payload.routes.slice(0, limit || 6);
    const byId = new Map((payload.nodes || []).map(node => [node.id, node]));
    return (payload.links || []).filter(link => CANDIDATE_KINDS.has(link.kind) && (byId.get(link.target) || {}).kind === 'candidate')
      .map(link => ({committee: link.committee || link.source, committee_label: (byId.get(link.source) || {}).label || '',
        candidate_label: byId.get(link.target).label, direction: byId.get(link.target).direction, value: link.value,
        target: link.target, path: []}))
      .sort((a, b) => b.value - a.value).slice(0, limit || 6);
  }

  const api = {validate, group, otherMembers, arrange, foldSmall, layout, route, colours, outcomes, largestRoutes, cubicPath, sigmoidPath,
    sigmoidPoints,
    toCents, short, sublines, amountOf, MIN_SHARE, LINK_KINDS, NODE_KINDS};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MentatFlowLayout = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
