/* Guided Graph section: read-only, same-origin report queries and local SVG. */
(() => {
  'use strict';

  /* ----------------------------------------------------- pure row geometry
     One account of where a bar row's six columns are, shared by the drawing
     below and by app/tests/graph-layout.test.js. Keeping it pure is what lets
     the invariant be asserted without a browser: the dot, label, bar, value,
     share and sparkline boxes are pairwise disjoint and all inside [0, W].
     Painting a panel needs a document; this geometry does not, so node:test
     loads the file for the geometry and stops at the guard below. */
  // Company comparison ends its rows as boards M4 and 1 draw them (Paul,
  // 2026-09-23: "Keep the dollar column, match the aesthetic"): the sparkline
  // stands SPARK_INSET clear of the card's right edge with SPARK_GAP of air
  // before it, and the dollar column keeps the value/share pair every other
  // card uses. It was 10px from the share and 4px from the edge.
  const SPARK_W = 54, SPARK_GAP = 28, SPARK_INSET = 22, SHARE_W = 56, VALUE_GAP = 24;
  const BAR_MIN = 48, DOT_COL = 22, DOT_CX = 9, LABEL_SIZE = 13;
  /* The card's own rhythm, from board M4 (console redesign 02, 2026-09-22):
     23px between row baselines - at that pitch both rows of the Overview's
     four cards fit one 1080px screen - 24px of head room above the first,
     13px label and value, 12px share and a 12px bar. Top organizations is the
     one two-line panel - the name owns the first line at the card's full
     width, the bar, value and share the second - because a registered PAC
     name does not fit a third of a card and was arriving as an ellipsis. The
     downstream drawer keeps the approved board-5 rhythm (DRAWER_PITCH and the
     14px bar). These live beside the columns so the geometry suite can pin the
     pitch instead of the drawing re-deciding it. */
  const ROW_PITCH = 23, ROW_HEAD = 24, TWO_LINE_PITCH = 50, SHARE_SIZE = 12;
  const TWO_LINE_NAME_DROP = 20, DRAWER_PITCH = 34;
  const BAR_H = 12, BAR_RISE = 10.5, LOOSE_BAR_H = 14, LOOSE_BAR_RISE = 11;
  function layoutColumns(width, options) {
    const opts = options || {};
    if (opts.compact) return compactColumns(width, opts);
    // 920 is the widest a card drawn in its own column is ever drawn; a card
    // given a folded neighbour's row (maxWidth, see widenedCap) draws wider.
    const W = Math.max(420, Math.min(Math.max(920, Number(opts.maxWidth) || 0), Math.round(Number(width) || 520)));
    const sparkWidth = opts.spark ? SPARK_W : 0, sparkGap = opts.spark ? SPARK_GAP : 0;
    const sparkX = W - (opts.spark ? SPARK_INSET : 4) - sparkWidth;
    // The share is anchored to the column the layout reserves for it, never to
    // the panel edge: that unconditional edge is how the percentage ended up
    // underneath the sparkline it was supposed to sit beside.
    const shareRight = sparkX - sparkGap;
    const valueRight = shareRight - SHARE_W;
    // The same label column in every panel, so bars line up between stacked
    // cards instead of each panel starting its bars at its own fraction.
    const labelRight = Math.max(130, Math.min(260, Math.round(W * 0.28)));
    const barLeft = labelRight + 8;
    const dotColumn = opts.party ? DOT_COL : 0;
    // A value column cannot eat the bar: what is left once the minimum bar and
    // its gutter are taken is the widest value column this width can carry.
    const room = Math.max(0, valueRight - barLeft - BAR_MIN - VALUE_GAP);
    const valueWidth = Math.max(0, Math.min(Number(opts.valueWidth) || 0, room));
    const barMax = Math.max(BAR_MIN, valueRight - valueWidth - VALUE_GAP - barLeft);
    return {width: W, dotColumn: dotColumn, dotCX: DOT_CX,
      labelLeft: dotColumn, labelRight: labelRight, labelWidth: labelRight - dotColumn,
      barLeft: barLeft, barMax: barMax,
      valueRight: valueRight, valueWidth: valueWidth,
      shareRight: shareRight, shareWidth: SHARE_W,
      sparkX: sparkX, sparkWidth: sparkWidth, labelSize: LABEL_SIZE};
  }

  /* A compare half (02b): the same columns in a half or a third of a card.
     The floor drops to 200px and the gutters tighten. The amount is never cut
     - a clipped value column is how G2's company halves drew bars through
     their amounts. Fix round 1 (R02-M1): the bar keeps a real share of the
     half, a quarter of it, instead of the 24px floor that left every bar a
     colour swatch at 1440; the label yields to that, but never below what
     its own widest name needs or three tenths of the half, whichever is
     less. A narrow half may drop its share column (shareWidth 0, as G2's
     earlier cycle has none) and a company half its bar (bar:false: G1 and
     G2 end those rows in the sparkline instead). */
  const COMPACT_BAR_MIN = 24, COMPACT_GAP = 12, COMPACT_SHARE_W = 46, COMPACT_SPARK_W = 40, COMPACT_LABEL_GAP = 8;
  const COMPACT_BAR_SHARE = 0.25, COMPACT_LABEL_KEEP = 0.3, COMPACT_SHARE_MIN_W = 285;
  function compactColumns(width, options) {
    const opts = options || {};
    const W = Math.max(200, Math.min(920, Math.round(Number(width) || 320)));
    const sparkWidth = opts.spark ? COMPACT_SPARK_W : 0, sparkGap = opts.spark ? 8 : 0;
    const sparkX = W - 2 - sparkWidth;
    const shareWidth = opts.shareWidth === 0 ? 0 : (Number(opts.shareWidth) || COMPACT_SHARE_W);
    const shareRight = W - 2 - sparkWidth - sparkGap;
    const valueRight = shareRight - shareWidth;
    const dotColumn = opts.party ? DOT_COL : 0;
    const bar = opts.bar !== false;
    const barMin = bar ? COMPACT_BAR_MIN : 0, gutter = bar ? COMPACT_LABEL_GAP + COMPACT_GAP : COMPACT_GAP;
    // The label's floor yields too when a half is too narrow for every column
    // (never on screen: halves stack under 600px), so nothing ever overlaps.
    const floor = Math.max(dotColumn, Math.min(dotColumn + (opts.leftRead ? 56 : 48), valueRight - gutter - barMin));
    const cap = opts.leftRead ? Math.min(dotColumn + 240, Math.round(W * 0.5)) : Math.min(180, Math.round(W * 0.4));
    // The value keeps its measured width for as long as the label's floor allows.
    const valueWidth = Math.max(0, Math.min(Number(opts.valueWidth) || 0, valueRight - floor - gutter - barMin));
    // What the label and the bar share: the half's left edge to the value's gutter.
    const room = valueRight - valueWidth - gutter;
    const need = Number(opts.labelWidth) > 0 ? dotColumn + Math.ceil(Number(opts.labelWidth)) : Infinity;
    let labelRight;
    if (!bar) labelRight = Math.max(floor, room);
    else {
      const want = Math.min(cap, need);
      const keep = Math.min(want, dotColumn + Math.round((W - dotColumn) * COMPACT_LABEL_KEEP));
      const reserve = Math.max(COMPACT_BAR_MIN, Math.round(W * COMPACT_BAR_SHARE));
      labelRight = Math.max(floor, keep, Math.min(want, room - reserve));
      // nameFirst (final fix round, TA1-01/TA1-03): the widest name is the
      // label's floor and the bar shrinks first, down to its own floor.
      if (opts.nameFirst) labelRight = Math.max(labelRight, Math.min(need, room - COMPACT_BAR_MIN));
      labelRight = Math.max(floor, Math.min(labelRight, room - COMPACT_BAR_MIN));
    }
    const barLeft = labelRight + (bar ? COMPACT_LABEL_GAP : 0);
    const barMax = bar ? Math.max(COMPACT_BAR_MIN, valueRight - valueWidth - COMPACT_GAP - barLeft) : 0;
    return {width: W, dotColumn: dotColumn, dotCX: DOT_CX,
      labelLeft: dotColumn, labelRight: labelRight, labelWidth: labelRight - dotColumn,
      barLeft: barLeft, barMax: barMax,
      valueRight: valueRight, valueWidth: valueWidth,
      shareRight: shareRight, shareWidth: shareWidth,
      sparkX: sparkX, sparkWidth: sparkWidth, labelSize: LABEL_SIZE, compact: true};
  }
  /* What a compare half gives up so its widest name is not cut (final fix
     round, TA1-01..03). `order` lists the steps a card allows, first to
     last: 'bar' (G1 and G4 draw candidate and company halves without one),
     'share' (G3's candidate halves have none; a company half narrower than
     COMPACT_NAME_SHARE_W gives its share up before its name - the share stays
     in the row's title and the footer) and 'room' (nameFirst: the label takes
     what the name needs and the bar keeps the rest, never under
     COMPACT_BAR_MIN). A step is taken only while the name is still cut, so a
     wide half keeps every column. Returns the options to lay the half out
     with; the steps taken are `yielded`. */
  const COMPACT_NAME_SHARE_W = 340;
  function compactYield(width, options, order) {
    const opts = Object.assign({}, options || {});
    const need = Math.ceil(Number(opts.labelWidth) || 0);
    const yielded = [];
    for (const step of order || []) {
      if (compactColumns(width, opts).labelWidth >= need) break;
      if (step === 'bar' && opts.bar !== false) opts.bar = false;
      else if (step === 'share' && opts.shareWidth !== 0) opts.shareWidth = 0;
      else if (step === 'room' && !opts.nameFirst) opts.nameFirst = true;
      else continue;
      yielded.push(step);
    }
    opts.yielded = yielded;
    return opts;
  }
  // What fitText() is told to protect from the cut. A party CODE is worth
  // keeping whole - a truncated row still says which side of the aisle it is
  // on - but a party VALUE is not a code: 'Other / Unclassified' rode through
  // as the protected suffix and left the name a bare ellipsis, so anything
  // longer than a code is appended to the name and fitted with it.
  const PARTY_CODE_MAX = 3;
  function labelParts(name, party) {
    const text = name == null ? '' : String(name);
    const code = (party == null ? '' : String(party)).trim();
    if (!code) return {text: text, suffix: ''};
    if (Array.from(code).length <= PARTY_CODE_MAX) return {text: text, suffix: ' · ' + code};
    return {text: text + ' · ' + code, suffix: ''};
  }
  /* ------------------------------------------------- pure navigation model
     Which parts of the scope trail are shown, what pressing one clears, and how
     deep the Back stack goes. It lives beside the geometry and above the
     document guard for the same reason: app/tests/graph-navigation.test.js can
     assert it without a browser, and the drawing below has one account to
     follow rather than a second one of its own. */
  const TRAIL_ORDER = ['industry', 'company', 'category', 'recipient'];
  const PAST_MAX = 20;
  // Only the parts that are set. `index` is the part's place in TRAIL_ORDER, not
  // its place on screen: a trail with no company still clears the right things.
  function trailModel(scope) {
    const named = scope || {};
    return TRAIL_ORDER
      .map((key, index) => ({key: key, index: index,
        label: String(named[key] == null ? '' : named[key])}))
      .filter(part => part.label);
  }
  // Pressing a part clears it and everything to its right. The industry is the
  // one exception: a Graph with no industry has nothing to show, so pressing it
  // returns to the whole industry rather than leaving the page unloadable.
  function clearedBy(index) {
    return TRAIL_ORDER.slice(Math.max(1, Number(index) || 0));
  }
  // The oldest entry is dropped, never the newest: a reader twenty-one steps in
  // can still go back one.
  function pushPast(stack, entry, max) {
    const ceiling = max === undefined ? PAST_MAX : max;
    const kept = (stack || []).concat([entry]);
    return kept.slice(Math.max(0, kept.length - ceiling));
  }
  /* ------------------------------------------ pure compare-menu model (02b)
     "+ Compare with..." and the pill group are one component, published as
     window.MentatCompareMenu below the document guard; Chart Studio (build
     03) calls it with its own value lists. What the menu DECIDES - which
     kinds it offers, which values survive `exclude` and the search, where the
     arrow keys land, that onPick fires once, how many pills may be added -
     lives here, free of the document, so app/tests/compare-menu.test.js can
     assert it with node:test. The DOM half only draws this state. */
  const COMPARE_KINDS = ['industry', 'cycle', 'company'];
  const COMPARE_LABELS = {industry: 'Another industry', cycle: 'Another cycle', company: 'Another company'};
  const COMPARE_MAX = 3;
  // The kinds a host asked for, in its order, known ones only, each once.
  function menuKinds(kinds, labels) {
    const names = Object.assign({}, COMPARE_LABELS, labels || {});
    const asked = Array.isArray(kinds) && kinds.length ? kinds : COMPARE_KINDS;
    return asked.filter((kind, at) => COMPARE_KINDS.indexOf(kind) >= 0 && asked.indexOf(kind) === at)
      .map(kind => ({kind: kind, label: String(names[kind] || COMPARE_LABELS[kind])}));
  }
  // A kind's values without the excluded ones (compared as strings, so a
  // cycle given as 2024 or '2024' is the same cycle) and without repeats.
  function menuValues(list, excluded) {
    const drop = new Set((excluded || []).map(value => String(value)));
    const seen = new Set();
    const out = [];
    for (const item of list || []) {
      if (!item || item.value == null) continue;
      const value = String(item.value);
      if (value === '' || drop.has(value) || seen.has(value)) continue;
      seen.add(value);
      out.push({value: item.value, label: String(item.label == null ? value : item.label)});
    }
    return out;
  }
  function menuFilter(items, query) {
    const needle = String(query || '').trim().toLowerCase();
    if (!needle) return (items || []).slice();
    return (items || []).filter(item => item.label.toLowerCase().includes(needle)
      || String(item.value).toLowerCase().includes(needle));
  }
  // Arrow keys wrap, Home and End jump; with nothing to land on there is no
  // active row (-1).
  function menuMove(index, key, count) {
    const n = Number(count) || 0;
    if (n <= 0) return -1;
    const at = Number.isInteger(index) ? index : -1;
    if (key === 'ArrowDown') return at < 0 ? 0 : (at + 1) % n;
    if (key === 'ArrowUp') return at < 0 ? n - 1 : (at - 1 + n) % n;
    if (key === 'Home') return 0;
    if (key === 'End') return n - 1;
    return Math.min(Math.max(at, -1), n - 1);
  }
  // The menu's whole behaviour as a small state machine. The DOM layer calls
  // key()/type()/choose()/pick() and redraws from state(); every call keeps
  // its own state, so two menus (Graph's and Chart Studio's) never share one.
  function createMenuController(options) {
    const opts = options || {};
    const kinds = menuKinds(opts.kinds, opts.labels);
    const exclude = opts.exclude || {};
    let stage = kinds.length === 1 ? 'values' : 'kinds';
    let kind = kinds.length === 1 ? kinds[0].kind : '';
    let items = [], shown = [], query = '', active = kinds.length === 1 ? -1 : 0, loading = false, failure = '';
    let done = false, request = 0;
    function state() {
      return {stage, kind, kinds: kinds.slice(), items: shown.slice(), query, active, loading, error: failure, done};
    }
    function close(reason) {
      if (done) return state();
      done = true;
      if (typeof opts.onClose === 'function') opts.onClose(reason || 'close');
      return state();
    }
    function refilter() {
      shown = menuFilter(items, query);
      active = shown.length ? 0 : -1;
    }
    // Choosing a kind asks the host for its values; a slower answer to an
    // earlier choice is ignored.
    function choose(name) {
      if (done) return Promise.resolve(state());
      const entry = kinds.find(item => item.kind === name);
      if (!entry) return Promise.resolve(state());
      stage = 'values'; kind = name; items = []; shown = []; query = ''; active = -1; loading = true; failure = '';
      const ticket = ++request;
      let answer;
      try { answer = typeof opts.valuesFor === 'function' ? opts.valuesFor(name) : []; }
      catch (error) { answer = Promise.reject(error); }
      return Promise.resolve(answer).then(list => {
        if (ticket !== request || done) return state();
        items = menuValues(list, exclude[name]);
        loading = false; refilter();
        return state();
      }, error => {
        if (ticket !== request || done) return state();
        loading = false; failure = (error && error.message) || 'Values unavailable.';
        return state();
      });
    }
    function type(text) {
      if (done || stage !== 'values') return state();
      query = String(text == null ? '' : text); refilter();
      return state();
    }
    // onPick fires exactly once, then the menu is closed.
    function pick(value) {
      if (done || stage !== 'values') return state();
      const chosen = value === undefined ? shown[active] : shown.find(item => String(item.value) === String(value));
      if (!chosen) return state();
      done = true;
      if (typeof opts.onPick === 'function') opts.onPick(kind, chosen.value, chosen);
      if (typeof opts.onClose === 'function') opts.onClose('pick');
      return state();
    }
    // One key. Answers what the DOM should do next: 'move', 'choose' (a
    // pending promise in .then), 'pick', 'close' or 'none'.
    function key(name) {
      if (done) return {action: 'none', state: state()};
      const count = stage === 'kinds' ? kinds.length : shown.length;
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(name) >= 0) {
        active = menuMove(active, name, count);
        return {action: 'move', state: state()};
      }
      if (name === 'Escape') return {action: 'close', state: close('escape')};
      if (name === 'Enter') {
        if (stage === 'kinds') {
          const entry = kinds[active];
          if (!entry) return {action: 'none', state: state()};
          return {action: 'choose', then: choose(entry.kind), state: state()};
        }
        if (active < 0) return {action: 'none', state: state()};
        return {action: 'pick', state: pick()};
      }
      return {action: 'none', state: state()};
    }
    function start() {
      return stage === 'values' ? choose(kind) : Promise.resolve(state());
    }
    return {state, choose, type, pick, key, close, start};
  }
  // The pill group: one pill per value in scope order, each with its remove
  // name, and a "+" while there is room for another scope.
  function pillsModel(values, max) {
    const ceiling = Math.max(1, Math.min(COMPARE_MAX, Number(max) || COMPARE_MAX));
    const list = (values || []).slice(0, ceiling).map((item, at) => {
      const value = item && typeof item === 'object' ? item.value : item;
      const label = String(item && typeof item === 'object' && item.label != null ? item.label : value);
      return {value, label, scope: at + 1, removeLabel: 'Remove ' + label};
    });
    return {pills: list, canAdd: list.length < ceiling, max: ceiling};
  }
  /* Short forms of a compared value (fix round 1: R02-M3, R02-m9). A pill or
     a headline line that cannot hold the whole name shows a shorter form
     rather than an ellipsis, and keeps the whole name in its title: a company
     without its legal suffix ("Goldman Sachs Bank NA" -> "Goldman Sachs"),
     then its leading words ("Goldman", "JP Morgan", as G3's headline prints
     them); a cycle as "2025–26". Longest first; an industry has its name only. */
  const NAME_SUFFIX = /(?:,?\s+(?:N\.?\s?A\.?|National Association|Bank|Bancorp|Bancorporation|Inc\.?|Corp\.?|Corporation|Company|Co\.?|Group|Holdings|Financial|Credit Union|FCU))+$/i;
  const NAME_JOINER = /^(?:of|the|and|&|for|at|in)$/i;
  const NAME_GENERIC = /^(?:bank|first|national|united|american|community|federal|credit|state|citizens|capital|new|north|south|east|west)$/i;
  const PERSON_SUFFIX = /^(?:JR|SR|II|III|IV|V|MR|MRS|MS|DR)\.?$/i;
  function nameForms(kind, full) {
    const text = String(full == null ? '' : full).trim();
    if (kind === 'cycle') {
      const match = /^(\d{4})\u2013(\d{4})(.*)$/.exec(text);
      return match ? [text, match[1] + '\u2013' + match[2].slice(2) + match[3]] : [text];
    }
    // A candidate ("SURNAME, GIVEN MIDDLE", as FEC files it) keeps its surname
    // and first given name and gives up the rest to initials, then entirely:
    // "WARNER, MARK ROBERT" -> "WARNER, MARK R" -> "WARNER, MARK" (final fix
    // round, TA1-01; G1 and G5 print "WARNER, MARK R"). A generational or
    // courtesy suffix (JR., III, MR.) is left out of the short forms.
    if (kind === 'person') {
      const comma = text.indexOf(',');
      if (comma < 1) return [text];
      const surname = text.slice(0, comma).trim();
      const given = text.slice(comma + 1).trim().split(/\s+/).filter(word => word && !PERSON_SUFFIX.test(word));
      const forms = [text];
      const add = form => { if (forms.indexOf(form) < 0) forms.push(form); };
      if (!given.length) return forms;
      const initials = given.slice(1).map(word => word.replace(/[^A-Za-z]/g, '').charAt(0)).filter(Boolean);
      if (initials.length) add(surname + ', ' + given[0] + ' ' + initials.join(' '));
      add(surname + ', ' + given[0]);
      return forms;
    }
    if (kind !== 'company') return [text];
    const forms = [text];
    const stripped = text.replace(NAME_SUFFIX, '').trim();
    if (stripped && stripped !== text) forms.push(stripped);
    const words = (stripped || text).split(/\s+/);
    for (let n = words.length - 1; n >= 1; n--) {
      const last = words[n - 1];
      if (NAME_JOINER.test(last) || (n === 1 && NAME_GENERIC.test(last))) continue;
      const head = words.slice(0, n).join(' ');
      if (head.replace(/[^A-Za-z0-9]/g, '').length < 3 || forms.indexOf(head) >= 0) continue;
      forms.push(head);
    }
    return forms;
  }
  /* The downstream drawer's sides. Paul, 2026-09-23, on its one "Contributions
     / Other" segment: "It should be two separate segments". The server answers
     six sides for a committee (graph_data.ACTIVITY_DIRECTIONS): Contribution
     and Other - the recorded 'Contribution / Other' bucket split by its FEC
     transaction code - Support, Oppose, 'Contribution / Other' itself for
     amounts whose saved source records no code, and Unknown. The drawer
     offers Contributions and Other beside Support and Oppose, always; the
     unsplit side and Unknown only when they hold something; and for a source
     that records no code at all, the unsplit side in place of the two halves
     it cannot be split into, so no zero is shown that was never measured. */
  const DRAWER_SIDE_ORDER = ['Contribution', 'Other', 'Support', 'Oppose', 'Contribution / Other', 'Unknown'];
  function drawerSideEmpty(row) {
    return !(Number(row && row.candidates) > 0) && !Number(row && row.amount) && !Number(row && row.unidentified_amount);
  }
  function drawerSides(totals) {
    const rows = (Array.isArray(totals) ? totals : []).filter(row => row && DRAWER_SIDE_ORDER.indexOf(row.direction) >= 0);
    const side = name => rows.find(row => row.direction === name);
    const unsplit = side('Contribution / Other');
    const halvesEmpty = ['Contribution', 'Other'].every(name => !side(name) || drawerSideEmpty(side(name)));
    const onlyUnsplit = halvesEmpty && Boolean(unsplit) && !drawerSideEmpty(unsplit);
    return rows.filter(row => {
      if (row.direction === 'Unknown' || row.direction === 'Contribution / Other') return !drawerSideEmpty(row);
      if (row.direction === 'Contribution' || row.direction === 'Other') return !onlyUnsplit;
      return true;
    }).sort((a, b) => DRAWER_SIDE_ORDER.indexOf(a.direction) - DRAWER_SIDE_ORDER.indexOf(b.direction));
  }
  const DRAWER_SIDE_LABEL = {'Contribution': 'Contributions', 'Other': 'Other',
    'Contribution / Other': 'Contributions / Other', 'Support': 'Support', 'Oppose': 'Oppose', 'Unknown': 'Unknown'};
  /* The two segments' counts, reconciled (round-3 verify, D1). Paul,
     2026-09-23: "It should be two separate segments". Each segment counts
     its own candidates, and one candidate can have both a contribution and
     other activity from the same committee - 40 of ActBlue's 1,005 in
     Banking 2025-2026 have a 24K and a 24C - so Contributions and Other add
     up to more than the candidates behind them (1,000 + 45). The server
     counts the recorded bucket once (graph_data.recorded_bucket, the
     response's contribution_other_union). This is the line the drawer shows
     under the segments, and each segment's tooltip sentence; null when no
     candidate is on more than one of the segments shown. */
  function drawerOverlap(totals, union) {
    const shared = Number(union && union.on_more_than_one_side) || 0;
    if (!(shared > 0)) return null;
    const recorded = Array.isArray(union.sides) ? union.sides : ['Contribution', 'Other', 'Contribution / Other'];
    const sides = drawerSides(totals).filter(row => recorded.indexOf(row.direction) >= 0 && Number(row.candidates) > 0);
    if (sides.length < 2) return null;
    const names = sides.map(row => DRAWER_SIDE_LABEL[row.direction] || row.direction);
    const list = names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
    const n = value => Number(value).toLocaleString();
    const candidates = Number(union.candidates) || 0;
    const pair = sides.length === 2;
    const text = `${list}: ${n(candidates)} different candidates; ` + (pair
      ? `${n(shared)} ${shared === 1 ? 'has both, so it is' : 'have both, so they are'} in both counts.`
      : `${n(shared)} ${shared === 1 ? 'is' : 'are'} in more than one count.`);
    const basis = direction => pair
      ? `${n(shared)} of them ${shared === 1 ? 'is' : 'are'} also counted under ${names[sides[0].direction === direction ? 1 : 0]}`
      : `${n(shared)} candidate${shared === 1 ? ' is' : 's are'} counted under more than one of ${list}`;
    return {sides: sides.map(row => row.direction), candidates, shared,
      counted: sides.reduce((sum, row) => sum + Number(row.candidates), 0), text, basis};
  }
  /* F4 (Paul approved 2026-09-25): the drawer's line says when money is not
     traced. `trace` is the answer's transfer_trace (graph_data.transfer_trace).
     When none of this committee's transfers could be followed - no saved
     network, and no Transfer Paths sheet or a stale transfer index - the line
     stops claiming "the PACs it funds", and the server's sentence follows it.
     A rebuild command, when the server names one, is for the tooltip. */
  function drawerNote(trace, company) {
    const untraced = Boolean(trace && trace.root_traced === false);
    const said = trace && trace.note ? ' ' + String(trace.note) : '';
    return {text: `Reported activity of this committee${untraced ? '' : ' and the PACs it funds'}, not money attributed to ${company}.` + said,
      command: trace && trace.command ? String(trace.command) : ''};
  }
  /* The page's Direction select (#graph-direction), r4 code review F5. It
     offered all six sides in every candidate view, so a reader could land on
     a side that cannot hold anything - Contributions or Other activity among
     the saved estimates, which record no transaction type, or the unsplit
     'Contribution / Other' on a report whose every row carries a code - and
     read the empty panel as a zero. It now offers what the view can hold: in
     activity, the drawer's own rule (drawerSides) over the answer's
     direction_totals, so a zero it offers is a measured one; in estimates, the
     directions an estimate is recorded under (graph_data.estimate_rows); and
     before an answer names its sides, Support, Opposition and the two split
     sides plus whatever is chosen, so a permalink is not rewritten before it
     is read. A chosen side the view does not offer becomes the side it was
     split into (the larger half, as the drawer opens on the largest side) or
     from ('Contribution / Other'), else Support, the page's default. */
  const DIRECTION_OPTIONS = [['Support', 'Support'], ['Oppose', 'Opposition'], ['Contribution', 'Contributions'],
    ['Other', 'Other activity'], ['Contribution / Other', 'Contributions / other (type not recorded)'],
    ['Unknown', 'Unknown direction']];
  const ESTIMATE_DIRECTIONS = ['Support', 'Oppose', 'Contribution / Other', 'Unknown'];
  function directionChoices(mode, totals, current) {
    const order = DIRECTION_OPTIONS.map(option => option[0]);
    const measured = mode === 'activity' && Array.isArray(totals) && totals.length > 0;
    const offered = mode === 'estimates' ? ESTIMATE_DIRECTIONS
      : measured ? drawerSides(totals).map(row => row.direction)
      : ['Support', 'Oppose', 'Contribution', 'Other', current];
    const values = order.filter(value => offered.indexOf(value) >= 0);
    let value = current;
    if (values.indexOf(value) < 0) {
      if (value === 'Contribution / Other') {
        const halves = (measured ? drawerSides(totals) : [])
          .filter(row => (row.direction === 'Contribution' || row.direction === 'Other') && values.indexOf(row.direction) >= 0)
          .sort((a, b) => Math.abs(Number(b.amount) || 0) - Math.abs(Number(a.amount) || 0));
        value = halves.length ? halves[0].direction : 'Support';
      } else if ((value === 'Contribution' || value === 'Other') && values.indexOf('Contribution / Other') >= 0) {
        value = 'Contribution / Other';
      } else value = 'Support';
    }
    const labels = {};
    for (const [id, label] of DIRECTION_OPTIONS) labels[id] = label;
    // Estimates never split the bucket, so there is no "type" to miss.
    if (mode === 'estimates') labels['Contribution / Other'] = 'Contributions / other';
    return {values, value, options: values.map(id => [id, labels[id]])};
  }
  /* A cycle-compare change chip (board G2): the latest cycle's value against
     the earlier cycle's, as a share of the earlier. The r4 audit's
     AUDIT-MEN-A-2 asked for the arithmetic to be re-derived: every chip on
     Banking 2023-2024 | 2025-2026 was recomputed from the two scopes' own
     /api/graph answers and agrees (r5 men-graph report). These are the cases
     that view does not reach, fixed here. A name the earlier cycle does not
     have is "new". An earlier net of exactly zero has no percentage: a latest
     amount is "new", a latest zero is no change. An earlier net below zero
     (refunds larger than receipts) was there, so it is not "new", and a
     percentage of a negative base reads backwards: the chip gives the change
     in whole dollars instead. A percentage is rounded once - one decimal
     under 100%, whole percent from 100% (99.96% prints "+100%", never
     "+100.0%") - and a change that rounds to nothing prints "0.0%" with no
     sign and no arrow. */
  function changeText(latest, earlier) {
    const missing = earlier === null || earlier === undefined || earlier === '' || !Number.isFinite(Number(earlier));
    if (missing) return {text: 'new', direction: ''};
    const before = Number(earlier), after = Number(latest) || 0;
    if (before === 0) return after === 0 ? {text: '0.0%', direction: ''} : {text: 'new', direction: ''};
    if (before < 0) {
      const delta = Math.round(after - before);
      if (!delta) return {text: '$0', direction: '', dollars: true};
      return {text: (delta > 0 ? '+' : '−') + '$' + Math.abs(delta).toLocaleString('en-US'),
        direction: delta > 0 ? 'up' : 'down', dollars: true};
    }
    const change = (after - before) / before * 100;
    let rounded = Math.round(Math.abs(change) * 10) / 10;
    const whole = rounded >= 100;
    if (whole) rounded = Math.round(Math.abs(change));
    if (rounded === 0) return {text: '0.0%', direction: ''};
    return {text: (change > 0 ? '+' : '−') + rounded.toFixed(whole ? 0 : 1) + '%', direction: change > 0 ? 'up' : 'down'};
  }
  /* The cards that fold (Paul, 2026-09-23: "You should be able to expand each
     section individually like with a census panel"), by the id graph.js's
     get() takes, and the per-browser key each one's state is kept under. */
  const FOLD_SECTIONS = ['category-panel', 'candidate-panel', 'company-panel', 'level-panel',
    'organization-panel', 'compare-table', 'drawer-panel'];
  const FOLD_KEY = 'mentat-graph-folded:';
  /* Company sparklines' access to contribution data (perf H11, 2026-09-24).
     The contributions catalog does not depend on the industry, so it is read
     once per page load and shared by every industry; refresh() starts that
     read beside the page's /api/graph request instead of after it. A failed
     read is forgotten, so the next caller reads again. A copy read in an
     earlier turn (Graph refresh) that offers no available dataset for an
     industry is read once more before that industry is refused: each industry
     used to read its own copy, so a source that became available after the
     page loaded still draws. The session, and its token, stays per industry.
     `read` supplies session(industry) and catalog(), each a promise of the
     parsed answer; source(industry, turn) answers {token, dataset}. */
  function sparkAccessModel(read) {
    let shared = null, sharedTurn = null;
    const byIndustry = new Map();
    function catalog(turn) {
      if (!shared) {
        const pending = Promise.resolve().then(() => read.catalog());
        shared = pending; sharedTurn = turn;
        pending.catch(() => { if (shared === pending) { shared = null; sharedTurn = null; } });
      }
      return shared;
    }
    const match = (list, industry) => ((list && list.datasets) || []).find(item =>
      item.available !== false && (item.id === industry || item.label === industry)) || null;
    function source(industry, turn) {
      if (!byIndustry.has(industry)) {
        const held = catalog(turn), heldTurn = sharedTurn;
        byIndustry.set(industry, Promise.all([read.session(industry), held]).then(async ([session, list]) => {
          let dataset = match(list, industry);
          if (!dataset && heldTurn !== turn) {
            if (shared === held) { shared = null; sharedTurn = null; }
            dataset = match(await catalog(turn), industry);
          }
          if (!dataset) throw new Error('No verified quarterly contribution dataset matches this Graph industry.');
          return {token: session.token, dataset: dataset.id};
        }).catch(error => { byIndustry.delete(industry); throw error; }));
      }
      return byIndustry.get(industry);
    }
    return {catalog, source};
  }
  /* RACF on the client (Paul, 2026-09-24: "replicational aware caching ...
     integrate into the data retrieval generally", and "Yeah do that" to
     browser-side reuse). The nearest tier answers first: this page's memory,
     then the browser's HTTP cache revalidated by ETag (every /api/graph read
     goes out with cache:'no-cache', so the browser sends If-None-Match and an
     unchanged answer comes back as a bodiless 304), then the server.
     graphMemoryModel is the memory tier: PARSED /api/graph answers keyed by
     request URL, each held with the ETag the server sent or confirmed for that
     URL. An entry is only ever made from such an answer in this page's life;
     it lives in this page's memory only, never in a browser store (D-039),
     so a reload starts empty. take(url, etag) hands back the parsed object only when the
     ETag the server has just confirmed is the one it was parsed from;
     peek(url) is the last confirmed answer, which refresh() draws at once and
     then revalidates. Bounded by entry count and by JSON text length; the
     least recently used leaves first. A held answer is shared, never copied:
     graph.js copies before it sorts and never writes into an answer. */
  const MEMORY_ENTRIES = 30, MEMORY_CHARS = 30 * 1024 * 1024;
  function graphMemoryModel(limits) {
    const maxEntries = (limits && limits.entries) || MEMORY_ENTRIES;
    const maxChars = (limits && limits.chars) || MEMORY_CHARS;
    const entries = new Map();
    let chars = 0;
    function drop(url) {
      const held = entries.get(url);
      if (held) { chars -= held.size; entries.delete(url); }
    }
    function recent(url, held) { entries.delete(url); entries.set(url, held); return held; }
    function put(url, etag, body, size) {
      drop(url);
      if (!url || !etag || !Number.isFinite(size) || size < 0 || size > maxChars) return false;
      entries.set(url, {etag, body, size});
      chars += size;
      for (const oldest of entries.keys()) {
        if (entries.size <= maxEntries && chars <= maxChars) break;
        drop(oldest);
      }
      return true;
    }
    function take(url, etag) {
      const held = entries.get(url);
      return held && etag && held.etag === etag ? recent(url, held).body : null;
    }
    function peek(url) {
      const held = entries.get(url);
      return held ? recent(url, held) : null;
    }
    return {put, take, peek, drop, has: url => entries.has(url),
      size: () => entries.size, chars: () => chars};
  }
  /* The likely next Graph views (RACF: pre-fetch the likely next items): the
     other top companies of this industry at this cycle, in the answer's own
     ranking, then this scope at the adjacent cycle (the one before it, else
     the one after), which keeps the last of the `max` places. Each is the
     request the control would send: `scope` is currentScope() after render,
     a company or cycle change clears category and recipient exactly as those
     selects do, and toUrl is requestParams. A company the select does not
     offer is skipped (neither the select nor its bar could ask for it); an
     All-cycles view has no adjacent cycle. */
  const PREFETCH_MAX = 4;
  function prefetchPlan(view, scope, toUrl, max) {
    const limit = max === undefined ? PREFETCH_MAX : max, out = [];
    if (!view || !view.filters || !view.options || !scope || !(limit > 0)) return out;
    const cycle = String(view.filters.cycle == null ? (scope.cycle || '') : view.filters.cycle);
    const own = String(view.filters.company || scope.company || '');
    const base = Object.assign({}, scope, {cycle, category: '', recipient: ''});
    const current = toUrl(Object.assign({}, scope, {cycle}));
    const cycles = (view.options.cycles || []).map(String).sort();
    const at = cycle ? cycles.indexOf(cycle) : -1;
    const adjacent = at < 0 ? '' : (at > 0 ? cycles[at - 1] : (cycles[at + 1] || ''));
    const offered = new Set((view.options.companies || []).map(String));
    const ranked = (view.companies || []).map((row, index) => ({
      id: row && row.id != null ? String(row.id) : '', amount: Number(row && row.amount) || 0, index}))
      .filter(row => row.id && row.id !== own && offered.has(row.id))
      .sort((a, b) => b.amount - a.amount || a.index - b.index);
    const add = (url, room) => { if (out.length < room && url !== current && !out.includes(url)) out.push(url); };
    for (const row of ranked) add(toUrl(Object.assign({}, base, {company: row.id})), adjacent ? limit - 1 : limit);
    if (adjacent) add(toUrl(Object.assign({}, base, {cycle: adjacent})), limit);
    return out;
  }
  /* One likely-next read at a time, only while the page is idle, visible and
     reading nothing else (RACF item 3). start(urls) replaces any plan;
     cancel() drops the plan and aborts the read under way - a new refresh()
     and any user interaction or navigation call it. `env` supplies
     idle(callback) -> handle and cancelIdle(handle), visible(), busy() (an
     /api/graph read in flight), has(url) (already held in memory) and
     read(url, signal). Only Graph GETs are ever planned: Insights POSTs
     belong to the server's warmer. */
  function prefetchScheduler(env) {
    let queue = [], handle = null, reading = null, generation = 0;
    function cancel() {
      generation += 1; queue = [];
      if (handle !== null) { env.cancelIdle(handle); handle = null; }
      if (reading) { reading.abort(); reading = null; }
    }
    function next(turn) {
      if (turn !== generation || handle !== null || reading || !queue.length) return;
      handle = env.idle(() => {
        handle = null;
        if (turn !== generation) return;
        if (!env.visible()) { cancel(); return; }
        if (env.busy()) { next(turn); return; }
        const url = queue.shift();
        if (env.has(url)) { next(turn); return; }
        const mine = new AbortController();
        reading = mine;
        Promise.resolve().then(() => env.read(url, mine.signal)).catch(() => {}).then(() => {
          if (reading === mine) reading = null;
          next(turn);
        });
      });
    }
    function start(urls) { cancel(); queue = (urls || []).slice(0, PREFETCH_MAX); next(generation); }
    return {start, cancel, pending: () => queue.slice(), reading: () => Boolean(reading)};
  }
  /* The one /api/graph reader (RACF). Every read on the page goes through
     it: cache:'no-cache' makes the browser revalidate its copy by ETag, and
     an answer whose ETag matches the parsed copy `memory` (graphMemoryModel)
     holds for that URL is not parsed again. busy() is true while any read is
     in flight, so the prefetcher never runs beside one. A response with no
     ETag (or no text(), as a test's stand-in fetch) is parsed and never
     held, and a failure drops what was held for its URL.
     read(url, signal) is a reader's own read (a select, a click, the drawer,
     a compare scope); prefetch(url, signal) is the idle prefetch's guess at
     the likely next view. They differ only in the request header
     X-Mentat-Prefetch: 1, on which the server answers exactly the same but
     does not record the read as a user access (review H4, 2026-09-24): the
     value ranking the warmer primes by counts what people ask for, not what
     this page guessed. The URL is the same, so the browser's cache entry, the
     ETag and the memory tier are shared. A custom X- header, because browsers
     forbid scripts to set Sec-Purpose. fetcher(url, init) is fetch. */
  const PREFETCH_HEADER = 'X-Mentat-Prefetch';
  function graphReader(memory, fetcher) {
    let reads = 0;
    async function read(url, signal, prefetch) {
      reads += 1;
      try {
        const init = {signal, cache: 'no-cache'};
        if (prefetch) init.headers = {[PREFETCH_HEADER]: '1'};
        const response = await fetcher(url, init);
        const etag = response.ok && response.headers && typeof response.headers.get === 'function' ? response.headers.get('ETag') || '' : '';
        const held = etag ? memory.take(url, etag) : null;
        if (held) { if (response.body && typeof response.body.cancel === 'function') response.body.cancel().catch(() => {}); return {ok: true, etag, body: held, reused: true}; }
        if (typeof response.text !== 'function') { const body = await response.json(); memory.drop(url); return {ok: response.ok, etag: '', body, reused: false}; }
        const text = await response.text(), body = JSON.parse(text);
        if (!etag || !memory.put(url, etag, body, text.length)) memory.drop(url);
        return {ok: response.ok, etag, body, reused: false};
      } finally { reads -= 1; }
    }
    return {read: (url, signal) => read(url, signal, false), prefetch: (url, signal) => read(url, signal, true),
      busy: () => reads > 0, has: url => memory.has(url)};
  }
  /* The idle prefetcher as the page runs it: prefetchScheduler over a
     graphReader, reading through reader.prefetch (so every planned read
     carries X-Mentat-Prefetch: 1), held back while reader.busy() and skipping
     what the reader's memory holds. `env` supplies idle, cancelIdle and
     visible, as prefetchScheduler describes. */
  function graphPrefetcher(reader, env) {
    return prefetchScheduler(Object.assign({}, env, {busy: reader.busy, has: reader.has, read: reader.prefetch}));
  }
  /* Downstream PAC activity is not the industry's giving. The view ranks what
     every committee the industry's money reached did with it (downstream.py
     NOTE), so a chart of it that is called "Top candidates" reads as the
     industry's own giving: Telecom 2025-26 reached Bill Hagerty for $142,494
     and gave him $100. In that view the title says what it measures, and the
     amount a row reached is always printed beside the industry's own direct
     amount, which /api/graph attaches to the row as `direct`. A report name
     carries its cycle ("Telecom 2025-2026"); the industry is the name alone. */
  const industryName = value => String(value == null ? '' : value).replace(/\s+\d{4}[-–]\d{4}$/, '');
  function activityTitle(industries) {
    const industry = [...new Set([].concat(industries).map(industryName).filter(Boolean))].join(' / ');
    return `Activity of committees that received ${industry} money, not ${industry}'s own giving`;
  }
  const isActivity = data => Boolean(data && data.filters && data.filters.mode === 'activity');
  // "$142,494 reached · $100 direct"; a row with no direct amount (the
  // unidentified-candidate row, an older answer) keeps its one amount.
  const reachedText = (row, said, money) => row && row.direct != null ? `${said} reached · ${money(row.direct)} direct` : said;
  function reachedForms(row, money) {
    const reached = money(row.amount);
    if (row.direct == null) return [reached];
    const direct = money(row.direct);
    return [`${reached} reached · ${direct} direct`, `${reached} · ${direct} direct`, reached];
  }
  const COMPARE_MENU = {KINDS: COMPARE_KINDS, LABELS: COMPARE_LABELS, MAX: COMPARE_MAX,
    menuKinds, menuValues, menuFilter, menuMove, createMenuController, pillsModel};
  const layout = {layoutColumns: layoutColumns, labelParts: labelParts,
    PARTY_CODE_MAX: PARTY_CODE_MAX, SPARK_W: SPARK_W, SPARK_GAP: SPARK_GAP, SPARK_INSET: SPARK_INSET,
    SHARE_W: SHARE_W, VALUE_GAP: VALUE_GAP, BAR_MIN: BAR_MIN, DOT_COL: DOT_COL,
    DOT_CX: DOT_CX, LABEL_SIZE: LABEL_SIZE, ROW_PITCH: ROW_PITCH,
    ROW_HEAD: ROW_HEAD, TWO_LINE_PITCH: TWO_LINE_PITCH, SHARE_SIZE: SHARE_SIZE,
    TWO_LINE_NAME_DROP: TWO_LINE_NAME_DROP, DRAWER_PITCH: DRAWER_PITCH,
    BAR_H: BAR_H, BAR_RISE: BAR_RISE, LOOSE_BAR_H: LOOSE_BAR_H, LOOSE_BAR_RISE: LOOSE_BAR_RISE,
    trailModel: trailModel, clearedBy: clearedBy, pushPast: pushPast,
    TRAIL_ORDER: TRAIL_ORDER, PAST_MAX: PAST_MAX,
    compactColumns: compactColumns, COMPACT_BAR_MIN: COMPACT_BAR_MIN, compareMenu: COMPARE_MENU,
    COMPACT_BAR_SHARE: COMPACT_BAR_SHARE, COMPACT_LABEL_KEEP: COMPACT_LABEL_KEEP,
    COMPACT_SHARE_MIN_W: COMPACT_SHARE_MIN_W, nameForms: nameForms,
    compactYield: compactYield, COMPACT_NAME_SHARE_W: COMPACT_NAME_SHARE_W,
    drawerSides: drawerSides, DRAWER_SIDE_ORDER: DRAWER_SIDE_ORDER,
    drawerOverlap: drawerOverlap, DRAWER_SIDE_LABEL: DRAWER_SIDE_LABEL, drawerNote: drawerNote,
    directionChoices: directionChoices, DIRECTION_OPTIONS: DIRECTION_OPTIONS, changeText: changeText,
    FOLD_SECTIONS: FOLD_SECTIONS, FOLD_KEY: FOLD_KEY, sparkAccessModel: sparkAccessModel,
    graphMemoryModel: graphMemoryModel, MEMORY_ENTRIES: MEMORY_ENTRIES, MEMORY_CHARS: MEMORY_CHARS,
    prefetchPlan: prefetchPlan, prefetchScheduler: prefetchScheduler, PREFETCH_MAX: PREFETCH_MAX,
    graphReader: graphReader, graphPrefetcher: graphPrefetcher, PREFETCH_HEADER: PREFETCH_HEADER,
    industryName: industryName, activityTitle: activityTitle, isActivity: isActivity,
    reachedText: reachedText, reachedForms: reachedForms};
  if (typeof module !== 'undefined' && module.exports) module.exports = layout;
  globalThis.MentatGraphLayout = layout;
  if (typeof document === 'undefined') return;

  /* ------------------------------------------------ MentatCompareMenu (02b)
     The reusable half of compare: "+ Compare with..." opens this menu, and
     the compared chip holds this pill group. Graph uses it below; Chart
     Studio (build 03) calls the same two functions with its own lists.
       MentatCompareMenu.open(anchorEl, {kinds, labels?, valuesFor(kind) ->
         Promise<[{value,label}]>, exclude:{kind:[values]}, onPick(kind,value),
         onClose?, captions?:{kind:text}}) -> {close()}
       (captions, fix round 1: the heading over a kind's list when it must say
       more than the kind's label, e.g. which value the comparison starts from)
       MentatCompareMenu.pills(container, {kind, values:[{value,label}], max,
         onRemove(index), onAdd(anchorEl)}) -> {update(values), destroy()}
     Styles are the unscoped .compare-menu / .compare-pills / .compare-clear
     classes in graph.css. No state outside each call. */
  const compareModel = COMPARE_MENU;
  let compareMenuSerial = 0;
  function openCompareMenu(anchor, options) {
    const opts = options || {};
    const id = 'compare-menu-' + (++compareMenuSerial);
    const menu = document.createElement('div');
    menu.className = 'compare-menu';
    menu.id = id;
    const closeWith = reason => {
      document.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('resize', place);
      menu.remove();
      if (anchor && typeof anchor.setAttribute === 'function') anchor.setAttribute('aria-expanded', 'false');
      if (reason !== 'outside' && anchor && typeof anchor.focus === 'function' && anchor.isConnected) anchor.focus();
      if (typeof opts.onClose === 'function') opts.onClose(reason);
    };
    const controller = compareModel.createMenuController({kinds: opts.kinds, labels: opts.labels,
      valuesFor: opts.valuesFor, exclude: opts.exclude,
      onPick: (kind, value, item) => { if (typeof opts.onPick === 'function') opts.onPick(kind, value, item); },
      onClose: reason => closeWith(reason)});
    let search = null, list = null;
    function place() {
      if (!anchor || !anchor.getBoundingClientRect) return;
      const box = anchor.getBoundingClientRect();
      const width = Math.max(240, Math.min(320, box.width + 120));
      const left = Math.max(8, Math.min(box.left, document.documentElement.clientWidth - width - 8));
      menu.style.width = width + 'px';
      menu.style.left = (left + window.scrollX) + 'px';
      menu.style.top = (box.bottom + 6 + window.scrollY) + 'px';
    }
    function draw() {
      const now = controller.state();
      if (now.done) return;
      menu.replaceChildren();
      if (now.stage === 'kinds') {
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', 'Compare with');
        now.kinds.forEach((entry, at) => {
          const item = document.createElement('button');
          item.type = 'button';
          item.className = 'compare-menu-kind';
          item.setAttribute('role', 'menuitem');
          item.dataset.kind = entry.kind;
          item.tabIndex = at === now.active ? 0 : -1;
          item.textContent = entry.label;
          item.onclick = () => { controller.choose(entry.kind).then(draw); draw(); };
          menu.append(item);
        });
        const current = menu.children[now.active];
        if (current) current.focus();
        return;
      }
      menu.removeAttribute('role');
      menu.setAttribute('aria-label', (compareModel.LABELS[now.kind] || 'Compare with'));
      const label = document.createElement('label');
      label.className = 'compare-menu-search';
      const caption = document.createElement('span');
      caption.className = 'compare-menu-caption';
      caption.textContent = (opts.captions && opts.captions[now.kind]) || (opts.labels && opts.labels[now.kind]) || compareModel.LABELS[now.kind];
      search = document.createElement('input');
      search.type = 'search';
      search.autocomplete = 'off';
      search.spellcheck = false;
      search.placeholder = 'Search';
      search.value = now.query;
      search.setAttribute('role', 'combobox');
      search.setAttribute('aria-expanded', 'true');
      search.setAttribute('aria-controls', id + '-list');
      search.setAttribute('aria-autocomplete', 'list');
      label.append(caption, search);
      list = document.createElement('ul');
      list.id = id + '-list';
      list.className = 'compare-menu-list';
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', caption.textContent);
      if (now.loading || now.error || !now.items.length) {
        const note = document.createElement('li');
        note.className = 'compare-menu-note';
        note.setAttribute('role', 'presentation');
        note.textContent = now.loading ? 'Loading…' : now.error || (now.query ? 'No matches' : 'Nothing else to compare');
        list.append(note);
      }
      now.items.forEach((entry, at) => {
        const option = document.createElement('li');
        option.id = id + '-option-' + at;
        option.className = 'compare-menu-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(at === now.active));
        option.dataset.value = String(entry.value);
        option.textContent = entry.label;
        option.onpointerdown = event => event.preventDefault();
        option.onclick = () => controller.pick(entry.value);
        list.append(option);
      });
      if (now.active >= 0) search.setAttribute('aria-activedescendant', id + '-option-' + now.active);
      search.oninput = () => { controller.type(search.value); redrawList(); };
      menu.append(label, list);
      search.focus();
    }
    // Typing redraws the list only, so the input keeps its caret and focus.
    function redrawList() {
      const now = controller.state();
      if (now.done || !list) return;
      const keep = search;
      draw();
      if (keep && search) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
    }
    function sync() {
      const now = controller.state();
      if (now.done) return;
      if (now.stage === 'kinds') {
        [...menu.querySelectorAll('.compare-menu-kind')].forEach((node, at) => { node.tabIndex = at === now.active ? 0 : -1; if (at === now.active) node.focus(); });
        return;
      }
      [...menu.querySelectorAll('.compare-menu-option')].forEach((node, at) => {
        node.setAttribute('aria-selected', String(at === now.active));
        if (at === now.active) node.scrollIntoView({block: 'nearest'});
      });
      if (search) {
        if (now.active >= 0) search.setAttribute('aria-activedescendant', id + '-option-' + now.active);
        else search.removeAttribute('aria-activedescendant');
      }
    }
    menu.addEventListener('keydown', event => {
      if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', 'Escape'].indexOf(event.key) < 0) return;
      // Home and End belong to the text field while it holds text.
      if ((event.key === 'Home' || event.key === 'End') && event.target === search && search.value) return;
      event.preventDefault();
      event.stopPropagation();
      const answer = controller.key(event.key);
      if (answer.action === 'move') sync();
      else if (answer.action === 'choose') { draw(); answer.then.then(draw); }
    });
    function outside(event) {
      if (menu.contains(event.target) || (anchor && anchor.contains && anchor.contains(event.target))) return;
      controller.close('outside');
    }
    (document.body || document.documentElement).append(menu);
    place();
    window.addEventListener('resize', place);
    document.addEventListener('pointerdown', outside, true);
    if (anchor && typeof anchor.setAttribute === 'function') {
      anchor.setAttribute('aria-expanded', 'true');
      anchor.setAttribute('aria-controls', id);
    }
    draw();
    controller.start().then(draw);
    return {close: () => controller.close('api'), element: menu};
  }
  function comparePills(container, options) {
    const opts = options || {};
    const group = document.createElement('span');
    group.className = 'compare-pills';
    group.setAttribute('role', 'group');
    if (opts.label) group.setAttribute('aria-label', opts.label);
    let values = opts.values || [];
    function draw() {
      const model = compareModel.pillsModel(values, opts.max);
      group.replaceChildren();
      model.pills.forEach((pill, at) => {
        const node = document.createElement('span');
        node.className = 'scope-pill scope-pill--' + pill.scope;
        node.dataset.scope = String(pill.scope);
        node.dataset.value = String(pill.value);
        const text = document.createElement('span');
        text.className = 'scope-pill-text';
        text.textContent = pill.label;
        node.title = pill.label;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'scope-pill-remove';
        remove.setAttribute('aria-label', pill.removeLabel);
        remove.title = pill.removeLabel;
        remove.textContent = '×';
        remove.onclick = event => { event.preventDefault(); event.stopPropagation(); if (typeof opts.onRemove === 'function') opts.onRemove(at); };
        node.append(text, remove);
        group.append(node);
      });
      if (model.canAdd) {
        const add = document.createElement('button');
        add.type = 'button';
        add.className = 'compare-pills-add';
        const noun = opts.kind === 'cycle' ? 'cycle' : opts.kind === 'company' ? 'company' : 'industry';
        add.setAttribute('aria-label', 'Add another ' + noun);
        add.title = 'Add another ' + noun;
        add.textContent = '+';
        add.onclick = event => { event.preventDefault(); event.stopPropagation(); if (typeof opts.onAdd === 'function') opts.onAdd(add); };
        group.append(add);
      }
    }
    draw();
    container.append(group);
    return {
      update(next) { values = next || []; draw(); },
      destroy() { group.remove(); },
      element: group
    };
  }
  const MentatCompareMenu = Object.freeze({open: openCompareMenu, pills: comparePills, model: compareModel});
  globalThis.MentatCompareMenu = MentatCompareMenu;

  const get = name => document.getElementById('graph-' + name);
  const dollars = n => Number(n).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2});
  // The headline figures and the level legend read in whole dollars (board M4,
  // ruling R6); the exact amount is their tooltip. Card rows keep cents.
  const wholeDollars = n => Number(n).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:0,maximumFractionDigits:0});
  const titleFor = {categories:'Recipient types',organizations:'Top organizations',candidates:'Top candidates',companies:'Company comparison',levels:'Giving by employee level'};
  // The title a card, an export and a briefing slide carry. The activity view's
  // candidates are named for what they measure (activityTitle), whichever
  // surface prints them; every other card keeps its own.
  const compareIndustries = () => compare && compare.dim === 'industry' ? compare.values : get('industry').value;
  function titleOf(name, data = view, industry = get('industry').value) {
    return name === 'candidates' && isActivity(data) ? activityTitle(industry) : titleFor[name];
  }
  let analysis = 'overview';
  let loaded = false, revision = 0, view = null, category = '', recipient = '', controller = null, unavailable = [];
  // Whether this console has a report to show. The Candidate view select is
  // visible from that moment on (Paul's ruling A(ii), 2026-09-17), not only on
  // the Candidates analysis tab, so Downstream PAC activity is a control the
  // reader can see and `mode=activity` is a state a permalink can name.
  let reportChosen = false;
  // A permalink's company, cycle and tier cannot be put on the selects before
  // the first response: those options are built in render() from the view the
  // server echoes back. So they ride along with the first request instead, and
  // render()'s own options(..., view.filters.company) lands them on the selects.
  let urlScope = null;
  // Build 02b (boards G1-G5): the comparison. `compare` is null or
  // {dim, values:[v1, v2, v3?]}; scopes 2 and 3 live in compareViews[i],
  // never in `view` (see the compare section near the end of this file).
  let compare=null,compareViews=[],compareAll=null,compareLayout='',pendingCompare=null,viewKey='';
  // The sides the last activity answer measured (direction_totals), for the
  // Direction select (F5); null until an answer for this scope names them.
  // directionScope is the scope they measured: the request less its
  // direction, because the sides are the same whichever side is asked for.
  // A refresh of that same scope keeps them while its answer is read (r5
  // code review F2), so the select no longer drops to the shorter unmeasured
  // list for a 20-35 s Banking read and comes back when the answer lands. Any
  // other scope starts from the unmeasured list, as before: its sides are
  // not known until its own answer names them.
  let directionTotals=null,directionScope='';
  function sidesScope(params) {const scope=new URLSearchParams(params);scope.delete('direction');return scope.toString();}
  // RACF (graphMemoryModel and graphReader, above). Every /api/graph read on
  // this page goes through graphIo: graphRead() for a reader's own read, and
  // the prefetcher's reads through graphIo.prefetch, which adds
  // X-Mentat-Prefetch: 1 so the server does not count them as user accesses
  // (review H4). Both share the memory tier; the fetch is looked up at call
  // time, so a test's stand-in fetch is used.
  const graphMemory=graphMemoryModel();
  const graphIo=graphReader(graphMemory,(url,init)=>fetch(url,init));
  function graphRead(url,signal) {return graphIo.read(url,signal);}
  // Idle prefetch (RACF item 3): after a view is drawn from an answer that
  // carried an ETag - one the memory tier can hold - the likely next views
  // are read one at a time while the page is idle and visible, never beside
  // another /api/graph read.
  const prefetcher=graphPrefetcher(graphIo,{
    idle:typeof requestIdleCallback==='function'?callback=>requestIdleCallback(callback,{timeout:1500}):callback=>setTimeout(callback,250),
    cancelIdle:typeof cancelIdleCallback==='function'?handle=>cancelIdleCallback(handle):handle=>clearTimeout(handle),
    visible:()=>document.visibilityState==='visible'&&!document.getElementById('view-graph')?.hidden});
  function schedulePrefetch(etag) {
    if(!etag||!view||compare||pendingCompare)return;
    prefetcher.start(prefetchPlan(view,currentScope(),scope=>'/api/graph?'+requestParams(scope),PREFETCH_MAX));
  }
  for(const type of ['pointerdown','keydown','wheel','touchstart'])window.addEventListener(type,()=>prefetcher.cancel(),{capture:true,passive:true});
  for(const type of ['hashchange','popstate','pagehide'])window.addEventListener(type,()=>prefetcher.cancel());
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='visible')prefetcher.cancel();});
  function options(node, values, selected='') {
    node.replaceChildren(...values.map(([id,label]) => new Option(label,id)));
    if ([...node.options].some(o=>o.value===selected)) node.value=selected;
  }
  function error(text='') {get('error').textContent=text;get('error').hidden=!text;}
  function clear(keepSides=false) {view=null;if(!keepSides){directionTotals=null;directionScope='';}window.dispatchEvent(new CustomEvent('mentat:graph',{detail:null}));get('results').hidden=true;get('candidate-detail').hidden=true;get('freshness').replaceChildren();get('provenance').replaceChildren();error();}
  /* Four card skeletons in the shape the panels will take, so a 20-35 second
     Banking read is a page that is filling rather than a page that is empty.
     The status line above them keeps saying what is being read. */
  function skeleton(on) {const node=get('skeleton');if(node)node.hidden=!on;}
  /* #graph-status is a 12px line under the scope card's second row, on screen
     only while a read is under way or has failed (board M4, ruling R7). The
     loaded sentence - how many receipt groups, local data - moves into the Net
     giving tooltip, so the element keeps its id and its live-region role and
     hides once the figures it describes are on screen. Until then the figures
     from the previous scope stay visible but dimmed, so the roll to the new
     numbers still starts from what the reader last saw. */
  function status(text,loaded=false) {
    const node=get('status');if(!node)return;
    node.textContent=text;node.hidden=Boolean(loaded);
    document.querySelector('#view-graph .graph-metrics')?.classList.toggle('is-stale',!loaded);
  }
  function syncControls() {
    get('tier').disabled=get('funding').value==='corporate';
    if(get('tier').disabled)get('tier').value='';
    get('direction').parentElement.hidden=get('mode').value==='direct';
    if(get('mode').value!=='direct')syncDirection(get('mode').value==='activity'?directionTotals:null);
    // Industry compare shares no company across scopes (02b state model).
    const locked=Boolean(compare&&compare.dim==='industry');
    get('company').disabled=locked;if(locked)get('company').value='';
    const chip=get('company').closest('.chip-select');if(chip)chip.title=locked?'Clear the comparison to choose a company':'';
  }
  // r4 code review F5: the Direction select offers the sides this candidate
  // view can hold (directionChoices, above). Rebuilt only when the list
  // changes, so a keyboard user's place in it is not reset for nothing.
  function syncDirection(totals) {
    const select=get('direction');
    const choice=directionChoices(get('mode').value,totals,select.value);
    const now=[...select.options].map(option=>option.value+'\u0001'+option.textContent).join('\u0002');
    if(now!==choice.options.map(pair=>pair.join('\u0001')).join('\u0002'))options(select,choice.options,choice.value);
    select.value=choice.value;
    return choice;
  }
  // A direction from a permalink or the trail: any side the server answers
  // for, placed in the list for the current mode.
  function setDirection(value) {
    if(!DIRECTION_OPTIONS.some(option=>option[0]===value))return;
    const choice=directionChoices(get('mode').value,null,value);
    options(get('direction'),choice.options,choice.value);
  }
  /* One scope's /api/graph request, built from an explicit scope object. The
     primary scope below reads the page's controls; a compare scope (phase
     02b) passes its own values and gets the same keys in the same order.
     Nothing here adds latest=1: refresh() adds it for a first load that names
     no cycle, and a compare scope uses scope 1's resolved cycle instead. */
  function requestParams(scope) {
    const s=scope||{};
    return new URLSearchParams({id:s.run||'',industry:s.industry||'',company:s.company||'',
      tier:s.tier||'',cycle:s.cycle||'',funding:s.funding||'all',mode:s.mode||'direct',
      direction:s.direction||'Support',category:s.category||'',recipient:s.recipient||''});
  }
  // The primary scope, as the controls - and a permalink not yet consumed - state it.
  function currentScope() {
    return {run:get('run').value,industry:get('industry').value,
      company:urlScope?.company ?? get('company').value,
      tier:urlScope?.tier ?? get('tier').value,
      cycle:urlScope?.cycle ?? get('cycle').value,
      funding:get('funding').value,mode:get('mode').value,
      direction:get('direction').value,category,recipient};
  }
  function filters() { return requestParams(currentScope()); }
  // Graph's own defaults are absent from the URL rather than spelled out in it,
  // so a permalink names only what the reader actually chose.
  const SCOPE_DEFAULTS={mode:'direct',direction:'Support',funding:'all'};
  // The URL mirrors the request that was just made - same keys, same spellings
  // (audits/mentat_failings_20260915/contracts/url_scope.json) - so a permalink,
  // a reload and a tab switch all describe one query. A page that did not load
  // scope.js keeps working; it simply has no URL scope.
  function writeScope(query) {
    const value=key=>{const text=query.get(key)||'';return text===SCOPE_DEFAULTS[key]?'':text;};
    const partial={industry:query.get('industry')||'',company:query.get('company')||'',
      cycle:query.get('cycle')||'',mode:value('mode'),direction:value('direction'),
      tier:query.get('tier')||'',funding:value('funding'),
      category:query.get('category')||'',recipient:query.get('recipient')||''};
    // A comparison repeats its key in pill order (url_scope v6); `layout` is
    // written only when it is not the default: two scopes, Stacked chosen.
    const shown=compare||pendingCompare;
    if(shown){partial[shown.dim]=shown.values.slice();partial.compare=shown.dim;}else partial.compare='';
    partial.layout=shown&&shown.values.length===2&&compareLayout==='stacked'?'stacked':'';
    globalThis.MentatScope?.write(partial);
  }
  // The permalink, applied once the catalog's own options exist. A value with no
  // matching option is skipped, never invented.
  function applyUrlScope() {
    urlScope=null;
    const scope=globalThis.MentatScope?.read();
    if(!scope)return;
    for(const name of ['industry','mode','direction','funding'])
      if(name==='direction'){if(scope.direction)setDirection(scope.direction);}
      else if(scope[name]&&[...get(name).options].some(option=>option.value===scope[name]))get(name).value=scope[name];
    category=scope.category||'';recipient=scope.recipient||'';
    pendingDrawer=scope.drawer?{id:scope.drawer,direction:scope.drawerDirection||''}:null;
    // A permalink's comparison. Industries can be checked now; cycles and
    // companies wait for the first answer, which names the options.
    pendingCompare=null;compareLayout=scope.layout==='stacked'?'stacked':'';
    if(scope.compare&&Array.isArray(scope.compareValues)&&scope.compareValues.length>=2){
      if(scope.compare==='industry'){
        const known=[...get('industry').options].map(option=>option.value);
        const values=scope.compareValues.filter((value,at,all)=>known.includes(value)&&all.indexOf(value)===at).slice(0,COMPARE_MENU.MAX);
        if(values.length>=2){compare={dim:'industry',values};get('industry').value=values[0];setTopN(COMPARE_TOP);}
      } else pendingCompare={dim:scope.compare,values:scope.compareValues.slice(0,COMPARE_MENU.MAX)};
    }
    const industryCompare=Boolean(compare&&compare.dim==='industry');
    if(scope.company||scope.cycle||scope.tier)urlScope={company:industryCompare?'':scope.company,cycle:scope.cycle,tier:scope.tier};
  }
  // Which saved run this console reads. Chart Studio's reconciliation strip has
  // to answer this the same way Graph does - a strip that reconciles against a
  // run Graph would never display is worse than no strip - so the rule lives in
  // one function and both views call it. A run that is only ready, or awaiting
  // review, is never chosen; a run whose catalog has no industries is walked
  // past rather than displayed empty.
  function runCandidates(runs, preferred) {
    const usable = run => !['ready','needs review'].includes(run.state);
    const first = runs.find(r => r.id === preferred && usable(r))
      || runs.find(r => r.state === 'completed') || runs[0];
    return [first, ...runs.filter(usable)].filter((r,i,a) => r && a.findIndex(v => v?.id === r.id) === i);
  }
  async function chooseRun(preferred) {
    const body = await api('/api/report-sources');
    const runs = body.runs || [];
    const candidates = runCandidates(runs, preferred);
    for (const candidate of candidates) {
      const available = await api('/api/graph-catalog?id=' + encodeURIComponent(candidate.id));
      if (available.industries.length) return {runs, run: candidate, catalog: available};
    }
    return {runs, run: candidates[0] || null, catalog: null};
  }
  globalThis.MentatGraphRun = {
    choose: preferred => chooseRun(preferred === undefined ? selectedRun : preferred),
    candidates: runCandidates};

  async function loadRuns() {
    // The template's "Choose a report source." is for a console with no run;
    // while the runs are being read, say so (R02-m5).
    status('Reading saved report sources…');
    try {
      const {runs, run: chosen, catalog} = await chooseRun(selectedRun);
      options(get('run'),runs.map(r=>[r.id,`${r.name} · ${r.state}`]),chosen?.id);
      await loadCatalog(catalog);
    } catch(e){error(e.message);status('Report sources unavailable.');}
  }
  async function loadCatalog(preloaded=null) {
    const ticket=++revision;controller?.abort();clear();status('Reading saved report sources…');
    options(get('industry'),[]);options(get('company'),[['','All companies']]);options(get('cycle'),[['','All cycles']]);
    category='';recipient='';get('tier').value='';get('funding').value='all';get('mode').value='direct';reportChosen=false;urlScope=null;dropCompare();syncControls();
    if(!get('run').value){status('No saved runs available.');return;}
    try {
      const body=preloaded?.industries ? preloaded : await api('/api/graph-catalog?id='+encodeURIComponent(get('run').value));
      if(ticket!==revision)return;
      options(get('industry'),body.industries.map(i=>[i.name,i.name]));
      unavailable=body.unavailable_reports||[];
      if(unavailable.length)get('source-label').textContent='Unavailable saved reports: '+unavailable.join(', ');
      if(!body.industries.length){status(body.message||'This run has no finalized report. Choose another run or check Collection.');return;}
      reportChosen=true;applyUrlScope();syncControls();applyAnalysis();
      await refresh(true);
    } catch(e){if(ticket===revision){error(e.message);status('Report sources unavailable.');}}
  }
  async function refresh(initial=false,redirected=false) {
    const ticket=++revision;prefetcher.cancel();controller?.abort();controller=new AbortController();clear(Boolean(directionTotals)&&directionScope===sidesScope(filters()));syncControls();
    if(!get('industry').value){skeleton(false);return;}
    status('Reading saved giving data…');
    skeleton(true);
    // One query object: the request that goes out and the URL the reader can
    // copy are the same values, and the permalink is consumed exactly once.
    const query=filters();writeScope(query);urlScope=null;
    // 02b: the compared scopes read in parallel with scope 1 whenever its
    // cycle is already known; a first load that asks for the latest cycle
    // waits for the answer that resolves it.
    const early=Boolean(compare)&&!(initial&&!query.get('cycle'));
    if(early)loadCompareScopes(query);
    // perf H11: the sparklines' contributions catalog does not depend on this
    // answer, so its one read per page starts here, beside /api/graph (a no-op
    // once it has been read). A tier scope draws no sparklines and reads none.
    if(!query.get('tier'))sparkAccess.catalog(ticket);
    try {
      // latest=1 makes graph_view() replace the requested cycle with max(years)
      // (graph_data.py:317-318). The URL already carries the cycle the reader
      // chose, and writeScope() has just published it, so sending latest=1 as
      // well left the URL saying 2022 while the view showed 2024. It is still
      // sent on a first load that names no cycle, which is what it is for.
      const url='/api/graph?'+query+(initial&&!query.get('cycle')?'&latest=1':'');
      // RACF: a view this page has already read is drawn at once from memory,
      // with the ETag the server gave it for this URL; the read below still
      // goes out and the server decides. The same ETag (a 304) keeps what is
      // drawn, a new answer is drawn over it, and a failure takes it down.
      const held=graphMemory.peek(url);
      if(held&&!settle(held.body))return;
      const answer=await graphRead(url,controller.signal);
      const body=answer.body;if(!answer.ok)throw new Error(body.error||'Graph unavailable.');
      if(ticket!==revision)return;
      if((held&&answer.etag===held.etag)||settle(body))schedulePrefetch(answer.etag);
    } catch(e){if(ticket===revision&&e.name!=='AbortError'){if(view)clear();skeleton(false);error(e.message);status('Graph unavailable for this selection.');}}
    // One answer onto the page; false when it asked again instead.
    function settle(body) {
      // r4 code review F5: the answer names the sides it measured. A chosen
      // side it does not offer - an old permalink's 'Contribution / Other'
      // on a report whose rows all carry a code - is asked again as the side
      // directionChoices makes of it, before anything is drawn, so no empty
      // panel is read as a measured zero. direction_totals are the same
      // whichever side was asked for, so this happens at most once.
      if(!redirected&&body.filters&&body.filters.mode==='activity'&&Array.isArray(body.direction_totals)&&body.direction_totals.length){
        const choice=directionChoices('activity',body.direction_totals,body.filters.direction);
        if(choice.value!==body.filters.direction){
          options(get('direction'),choice.options,choice.value);
          // The side asked again is on the same scope, so its sides are these.
          if(sidesScope(filters())===sidesScope(query)){directionTotals=body.direction_totals;directionScope=sidesScope(query);}
          refresh(initial,true);return false;
        }
      }
      view=body;render();window.dispatchEvent(new CustomEvent('mentat:graph',{detail:{...body,run:get('run').value,industry:get('industry').value}}));
      if(compare&&!early)loadCompareScopes(null);
      return true;
    }
  }
  function chip(text,clearFilter) {
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=text+' ×';
    button.onclick=()=>{clearFilter();refresh();};get('chips').append(button);
  }
  /* R12: a chip with less room than its value shows the value cut by the
     measured fitter the bars use, and the whole value in its title. The select
     keeps its id, its options and its keyboard; it only stops painting its own
     text (graph.css), and an aria-hidden face paints the fitted one. */
  /* While comparing (fix round 1, R02-M3), no chip gives up its caption:
     each other chip keeps a floor (--chip-min, held by graph.css) of its whole
     caption or a short value, whichever is wider. The pill group gives way
     instead, in measured steps: tighter gaps and no chevron (is-tight), then
     each pill's shorter forms ("Goldman Sachs", then "Goldman"; "2025–26"),
     and an ellipsis only after that. Each pass starts again from the whole
     names, so a wider screen gets them back. */
  const CHIP_VALUE_KEEP=50,FIGURE_NAME_MAX=72;
  // The longest form that fits `room` at `size`px; else the last, cut.
  function fitForm(forms,room,size) {
    for(const form of forms)if(MentatTheme.measureText(form,size,MentatTheme.sans)<=room)return form;
    return MentatTheme.fitText(forms[forms.length-1],room,size,MentatTheme.sans);
  }
  function fitCompareRow(grid) {
    for(const chip of grid.querySelectorAll('.chip-select:not(.chip-compare)')){
      const caption=chip.querySelector(':scope>span:first-child'),select=chip.querySelector('select');
      if(!caption||!select)continue;
      const style=getComputedStyle(chip),family=getComputedStyle(caption).fontFamily;
      const left=parseFloat(style.paddingLeft)||12,right=parseFloat(style.paddingRight)||26;
      const value=select.selectedOptions[0]?.textContent||'';
      const keep=select.id==='graph-cycle'?Infinity:CHIP_VALUE_KEEP;
      const captionNeed=MentatTheme.measureText(caption.textContent,11,family)+left+12;
      const valueNeed=Math.min(MentatTheme.measureText(value,13,family),keep)+left+right+2;
      chip.style.setProperty('--chip-min',Math.ceil(Math.max(captionNeed,valueNeed))+'px');
    }
    grid.classList.remove('is-tight');
    const chip=grid.querySelector('.chip-compare');
    if(!chip||!compare)return;
    const texts=[...chip.querySelectorAll('.scope-pill-text')];
    const forms=compare.values.map(value=>nameForms(compare.dim,valueLabel(compare.dim,value)));
    const depth=Math.max(1,...forms.map(list=>list.length));
    const use=level=>texts.forEach((node,at)=>{const list=forms[at]||[node.textContent];node.textContent=list[Math.min(level,list.length-1)];});
    const cut=()=>texts.some(node=>node.scrollWidth>node.clientWidth+0.5)||grid.scrollWidth>grid.clientWidth+1;
    grid.classList.remove('is-wrapped');
    const cascade=()=>{
      use(0);if(!cut())return true;
      grid.classList.add('is-tight');if(!cut())return true;
      for(let level=1;level<depth;level++){use(level);if(!cut())return true;}
      return false;
    };
    // Last resort (three scopes at 1440, or any comparison on a narrower
    // screen): the pill chip takes a row of its own in the scope card rather
    // than cutting every pill to two letters.
    if(cascade())return;
    grid.classList.remove('is-tight');grid.classList.add('is-wrapped');
    cascade();
  }
  function fitChips() {
    const row=document.querySelector('#view-graph .graph-filter-grid');
    if(row)fitCompareRow(row);
    for(const select of document.querySelectorAll('#view-graph .graph-filter-grid .chip-select select')){
      const chip=select.closest('.chip-select');
      let face=chip.querySelector('.chip-face');
      if(!face){face=document.createElement('span');face.className='chip-face';face.setAttribute('aria-hidden','true');chip.append(face);}
      const full=select.selectedOptions[0]?.textContent||'';
      const style=getComputedStyle(select);
      const room=select.clientWidth-(parseFloat(style.paddingLeft)||0)-(parseFloat(style.paddingRight)||0);
      face.textContent=room>0&&globalThis.MentatTheme?.fitText?MentatTheme.fitText(full,room,parseFloat(style.fontSize)||13,style.fontFamily):full;
      select.title=full;
    }
  }
  const chipGrid=document.querySelector('#view-graph .graph-filter-grid');
  if(chipGrid){
    chipGrid.addEventListener('change',fitChips);chipGrid.addEventListener('input',fitChips);
    // Each chip as well as the row: a pill chip growing narrows its
    // neighbours without changing the row's own width.
    if(typeof ResizeObserver!=='undefined'){let frame=0;const watch=new ResizeObserver(()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(fitChips);});
      watch.observe(chipGrid);for(const chip of chipGrid.querySelectorAll('.chip-select'))watch.observe(chip);}
  }
  // FIX 4 (collection gaps): the report's own Data Quality check names the company x cycle cells whose stored
  // rows are materially fewer than FEC reported (view.collection_gaps, already limited to the cycles shown).
  // The scope this page is showing is every loaded scope's company and cycle: one view, or while comparing
  // each compared scope plus the all-company ranking Company comparison draws. coverage-view.js words it.
  function coverageNotice() {
    const api=globalThis.MentatCoverage;
    if(!api||!view)return null;
    const datas=compare?[...compareScopes().map(scope=>scope.data),compare.dim==='company'&&compareAll&&compareAll.data].filter(Boolean):[view];
    return api.gapNotice(datas.map(data=>({gaps:data.collection_gaps,
      scope:{companies:data.filters.company?[data.filters.company]:null,cycles:data.filters.cycle?[data.filters.cycle]:null}})));
  }
  // The scope line is what the CSV, the image caption, the SVG <desc>, the briefing chip and the per-figure
  // Image header all quote, so the gap is appended to it here and travels with every one of them. On screen
  // the chip and caption sit in the chips row.
  let scopeBase='';
  function paintScope(base) {
    if(base!==undefined)scopeBase=base;
    const notice=coverageNotice();
    get('scope').textContent=notice?globalThis.MentatCoverage.withGap(scopeBase,notice):scopeBase;
    paintCoverage(notice);
  }
  function paintCoverage(notice) {
    const host=get('chips');
    for(const old of host.querySelectorAll('.graph-coverage'))old.remove();
    if(!notice)return;
    const line=document.createElement('span'),caption=document.createElement('span');
    line.className='graph-coverage';caption.className='chart-note graph-coverage-caption';caption.textContent=notice.caption;
    line.append(globalThis.MentatCoverage.chipNode(notice),caption);host.append(line);
  }
  function render() {
    const opts=view.options;
    options(get('company'),[['','All companies'],...opts.companies.map(c=>[c,c])],view.filters.company);
    options(get('cycle'),[['','All cycles'],...opts.cycles.map(c=>[c,`${Number(c)-1}–${c}`])],view.filters.cycle);
    options(get('tier'),[['','All'],...['Chief','Middle-Management','Employees',...(opts.unknown_tiers?['Unknown']:[])].map(t=>[t,t])],view.filters.tier);
    directionTotals=view.filters&&view.filters.mode==='activity'&&Array.isArray(view.direction_totals)&&view.direction_totals.length?view.direction_totals:null;
    // The selects now hold this answer's own scope (a first load's latest cycle included).
    directionScope=directionTotals?sidesScope(filters()):'';
    syncControls();get('chips').replaceChildren();
    if(pendingCompare){resolvePendingCompare();syncControls();}
    renderCompareChip();
    if(category)chip(category,()=>{category='';recipient='';});
    if(recipient)chip(opts.recipients.find(r=>r.id===recipient)?.label||recipient,()=>{recipient='';});
    // The scope sentence is no longer printed on screen - it only repeated the
    // selects the reader is looking at (UI clean-up 2026-09-17, item 1) - but
    // it is still built here, because the CSV, the image caption, the SVG
    // <desc> and the briefing slide all quote it as their scope line.
    const scope=[get('industry').value,view.filters.company||'All companies',view.filters.tier||'All levels',(get('funding').value==='all'?'All giving':get('funding').selectedOptions[0].textContent),get('cycle').selectedOptions[0].textContent,category,recipient].filter(Boolean).join(' · ');
    paintScope(scope);
    get('source-label').textContent=get('run').selectedOptions[0].textContent+(unavailable.length?' · Unavailable saved reports: '+unavailable.join(', '):'');
    // R7: the loaded sentence leaves the screen and joins the Net giving
    // tooltip, beside the exact amount and the basis note.
    const loadedText=`${view.totals.receipt_groups.toLocaleString()} included receipt groups · local data`;
    status(loadedText,true);
    roll('total',view.totals.giving);roll('direct',view.totals.direct);
    // Name the figure, so a reader can match it to Chart Studio rather than
    // wonder which of two numbers on two screens is the real one. The note
    // stays in the DOM but is not shown (board M4); it rides in the tooltip.
    get('basis-note').textContent='Net workbook receipts. Same basis as Chart Studio’s Published included receipts.';
    roll('recipient-count',view.totals.organizations);
    // R6: whole dollars on screen, the exact amount in the tooltip.
    get('total').title=`${dollars(view.totals.giving)} · ${loadedText} · ${get('basis-note').textContent}`;
    get('direct').title=dollars(view.totals.direct);
    get('recipient-count').title=dollars(view.totals.organizations);
    // Each part figure carries its share. Board M4 prints the share alone
    // ("18.0%"); what it is a share of stays in the accessible text and the
    // tooltip. The share bar is kept for its older readers but not drawn (M4
    // has none), and is still sized only for a real fraction of the net: a
    // signed total has no width, and a bar at 0% would read as "none of it".
    for(const [name,value] of [['direct',view.totals.direct],['organization',view.totals.organizations]]) {
      const usable=view.totals.giving>0&&value>=0&&value<=view.totals.giving;
      const percent=usable?100*value/view.totals.giving:0;
      const share=get(name+'-share'),spoken=document.createElement('span');
      spoken.className='sr-only';spoken.textContent=usable?' of net giving':' Share unavailable for signed totals';
      share.replaceChildren(usable?percent.toFixed(1)+'%':'\u2014',spoken);
      share.title=usable?percent.toFixed(1)+'% of net giving':'Share unavailable for signed totals';
      const bar=get(name+'-bar');bar.style.width=percent.toFixed(1)+'%';
      bar.parentElement.hidden=!usable;
    }
    // F4: an unavailable view says why in the panel itself (paintPanel), so
    // the line under it does not repeat the sentence; a rebuild command, when
    // the server names one, is that line's tooltip.
    serverNote=view.candidate_available?(view.candidate_note||''):'';
    serverNoteTitle=view.candidate_note_title||'';
    // Item 4: the candidate-category receipts with no unique authorized
    // candidate link are an aside about the denominator, so they became the
    // tooltip on the candidates panel's information mark instead of a second
    // sentence of prose under the chart.
    unresolvedNote=view.filters.mode==='direct'&&view.totals.unresolved_candidate_links!==0
      ? `${dollars(view.totals.unresolved_candidate_links)} in candidate-category receipts has no unique authorized candidate link and is excluded from these rankings.`
      : '';
    get('method').textContent=view.note;
    get('provenance').replaceChildren(...view.reports.map(r=>{const p=document.createElement('p');p.className='muted';p.textContent=`${r.name} · SHA-256 ${r.sha256}`;return p;}));
    // Layout first, paint second. clear() hides #graph-results at the top of
    // refresh(), so painting here - as this did - measured clientWidth 0 on
    // every panel, fell back to 520, and left CSS to scale one column up and
    // the other down. applyAnalysis() settles which panels are shown, then the
    // results are revealed, and only then does each panel have a width to draw
    // at.
    applyAnalysis();
    skeleton(false);
    get('results').hidden=false;
    paintAll(true);
    renderFreshness();syncExport();renderTrail();restoreDrawer();fitChips();
    renderCompareFigures();renderCompareTable();markCompareState();
    viewKey=filters().toString();
  }
  function svgNode(tag,attributes={},text) {
    const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
    Object.entries(attributes).forEach(([k,v])=>node.setAttribute(k,String(v)));
    if(text!==undefined)node.textContent=text;
    return node;
  }
  const PANELS=['categories','organizations','candidates','companies','levels'];
  // What each panel's share is a share OF. These are the figures the KPI strip
  // above already prints, so a reader can add a panel's shares up and land on
  // the tile the panel belongs to (item 6).
  // Downstream PAC activity and conditional industry estimates are not part of
  // "direct to candidates" - they are other committees' whole reported
  // activity - so only the direct view divides by that KPI. The other two
  // divide by the candidate rows they are showing, and the label says so.
  function denomLabel(name,data=view) {
    if(name!=='candidates')return {categories:'net giving',organizations:'recipient organizations',
      companies:'net giving',levels:'the shown levels'}[name]||'net giving';
    return data&&data.filters.mode!=='direct'?'this candidate view':'direct to candidates';
  }
  const PANEL_CLICK={
    categories:id=>{remember();category=id;recipient='';closeDrawer(false);pendingDrawer=null;refresh();},
    // A PAC click still narrows the page to that PAC - that is what the chip,
    // the trail and the permalink all describe - and now also opens the drawer
    // beside the panel, which is the second hop the reader came for.
    organizations:id=>{remember();recipient=id;closeDrawer(false);pendingDrawer={id,direction:''};refresh();},
    candidates:id=>showCandidate(id),
    companies:id=>{remember();get('company').value=id;category='';recipient='';closeDrawer(false);pendingDrawer=null;refresh();},
    levels:id=>{remember();get('tier').value=id;get('funding').value='personal';refresh();}
  };
  // Top N is a client-side slice of the payload the one /api/graph request has
  // already returned. /api/graph has no row-count parameter - the old global
  // "Rows per chart" input sliced in this function too - so a Top N change
  // costs no request, no route change and nothing in the URL (item 2).
  function topN(name) {
    const select=document.getElementById('graph-top-'+name);
    return Math.max(1,Number(select&&select.value)||10);
  }
  function denominator(name,data=view) {
    if(!data)return 0;
    if(name==='candidates')return data.filters.mode==='direct'
      ? data.totals.direct
      : (data.candidates||[]).reduce((sum,row)=>sum+row.amount,0);
    if(name==='levels')return (data.levels||[]).reduce((sum,row)=>sum+row.amount,0);
    if(name==='organizations')return data.totals.organizations;
    return data.totals.giving;
  }
  // Party colour only where the payload actually carries a party
  // (graph_data.py:371 joins every party recorded for a candidate). A label
  // this console cannot read as one party gets the neutral dot, never a guess.
  function partyClass(party) {
    const text=String(party||'').toUpperCase();
    if(!text)return '';
    const dem=/(^|[^A-Z])(DEM|D|DFL)([^A-Z]|$)/.test(text),rep=/(^|[^A-Z])(REP|R|GOP)([^A-Z]|$)/.test(text);
    if(dem&&!rep)return 'is-dem';
    if(rep&&!dem)return 'is-rep';
    return 'is-other';
  }
  function emptyPanel(name,message,host,footNode,title) {
    const container=host||get(name);container.replaceChildren();
    const p=document.createElement('p');p.className='graph-empty';p.textContent=message;if(title)p.title=title;container.append(p);
    const foot=footNode===null?null:(footNode||document.getElementById('graph-foot-'+name));
    if(foot){foot.textContent='';foot.removeAttribute('title');}
    if(!host)labelValues(name,0,0);
  }
  /* MOTION. One switch: the reduced-motion media query rewrites --motion in
     graph.css, and everything here - the CSS animations and the JS number
     roll alike - reads that token rather than asking matchMedia a second
     time. A stagger of 42ms is capped at seven steps so a fifty-row panel
     still finishes growing inside 300ms. */
  const MOTION_STEPS=7;
  function motionMs() {
    const raw=getComputedStyle(document.documentElement).getPropertyValue('--motion');
    const value=parseFloat(raw);
    return Number.isFinite(value)?(raw.trim().endsWith('ms')?value:value*1000):0;
  }
  // An entrance is over once it has run; the class goes with it, so a chart
  // exported later cannot carry an animation the export would have to strip.
  function settle(svg) {
    setTimeout(()=>{
      svg.classList.remove('graph-enter');
      for(const node of svg.querySelectorAll('[style]'))node.style.removeProperty('--i');
    },Math.max(40,motionMs()+MOTION_STEPS*42+120));
  }
  const rolled=new Map();
  // The three figures in the strip travel from the number that was there to
  // the number that replaced it, so a scope change is legible as a change.
  // A figure nobody has seen yet is simply written: there is nothing to roll
  // from, and a roll from zero would read as growth that did not happen.
  function roll(name,value,format=wholeDollars) {
    const node=get(name),from=rolled.get(name),to=Number(value)||0;
    rolled.set(name,to);
    const span=motionMs()<=1||from===undefined||from===to?0:800;
    if(!span){node.textContent=format(to);return;}
    const started=performance.now();
    const step=now=>{
      const part=Math.min(1,(now-started)/span),eased=1-Math.pow(1-part,3);
      if(rolled.get(name)!==to)return;
      node.textContent=format(from+(to-from)*eased);
      if(part<1)requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  // The full-value table stays exactly where it was - inside the panel's own
  // container, built on first open - because "Full table", "View all" and the
  // lazy-table regression all reach for `#graph-<name> > details`.
  function appendValues(container,name,rows,onClick) {
    const details=document.createElement('details'),summary=document.createElement('summary');
    summary.textContent=`All ${rows.length} values`;
    let valuesBuilt=false;
    details.addEventListener('toggle',()=>{
      if(!details.open||valuesBuilt)return;
      const scroll=document.createElement('div');scroll.className='table-scroll';
      const table=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody');
      // Downstream activity rows carry the industry's own direct amount: it gets its column.
      const withDirect=rows.some(row=>row.direct!=null);
      head.innerHTML=withDirect?'<tr><th>Name</th><th>Net amount reached</th><th>Direct</th></tr>':'<tr><th>Name</th><th>Net amount</th></tr>';
      rows.forEach(row=>{const tr=document.createElement('tr'),label=document.createElement('td'),value=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='graph-text-button';button.textContent=row.label;button.onclick=()=>onClick(row.id);label.append(button);value.textContent=dollars(row.amount);tr.append(label,value);
        if(withDirect){const direct=document.createElement('td');direct.textContent=row.direct==null?'—':dollars(row.direct);tr.append(direct);}
        body.append(tr);});
      table.append(head,body);scroll.append(table);details.append(scroll);valuesBuilt=true;
    });
    details.append(summary);container.append(details);
  }
  function footText(name,shown,total,percentages,covered) {
    return `Showing ${shown} of ${total}`+(percentages
      ? ` · ${covered.toFixed(1)}% of ${denomLabel(name)}`
      : ' · shares withheld for signed net amounts');
  }
  const ROW_HEIGHT=ROW_PITCH,ROW_TOP=ROW_HEAD;
  /* Which way a panel's names read, from the approved board. Recipient types
     stay right-anchored against their bars; candidates and companies read
     left from one x, so a column of names is a column and not a ragged right
     edge. Top organizations is two-line (see TWO_LINE_PITCH). */
  const LEFT_LABELLED=['candidates','companies'];
  const TWO_LINE=['organizations'];
  // The chart is drawn at the panel's own pixel width, so its viewBox and its
  // layout width agree and nothing is scaled: a 720-wide drawing inside a
  // 540-wide bento tile renders 13px type at under 10px. A panel with no
  // width yet - hidden, or not laid out - falls back to 520 and is redrawn by
  // panelObserver as soon as it has one.
  const panelWidth=container=>Math.max(420,Math.min(widenedCap(container),Math.round(container.clientWidth||520)));
  /* Paul, 2026-09-23: "You should be able to expand each section individually
     like with a census panel" - the point being a bigger graph. A card that
     graph.css gives the whole row because the card beside it is folded is
     drawn at that row's width, its bars longer at the same 13px type, rather
     than stopping at the 920 a card in its own column is capped at; the card
     carries .is-widened meanwhile, which lifts the stylesheet's 920px
     max-width for its drawing. The drawer widened by its own explorer
     (.graph-drawer-wide) keeps the cap, as it always has. */
  function widenedCap(container) {
    const card=container&&container.closest?container.closest('.graph-panel'):null;
    const grid=card&&card.parentElement;
    let wide=false;
    if(card&&grid&&!card.classList.contains('is-folded')&&!card.classList.contains('graph-drawer-wide')
      &&grid.querySelector(':scope>.graph-panel.is-folded:not([hidden])')){
      const tracks=getComputedStyle(grid).gridTemplateColumns.split(/\s+/).filter(Boolean).length;
      wide=tracks>1&&getComputedStyle(card).gridColumnEnd==='-1';
    }
    if(card)card.classList.toggle('is-widened',wide);
    return wide?Math.max(920,Math.round(container.clientWidth||0)):920;
  }
  // A compare half (02b) is drawn at its own width too, down to 200px: the
  // 420px floor would hand a third of a card a drawing scaled to 10px type.
  const compactWidth=container=>Math.max(200,Math.min(920,Math.round(container.clientWidth||320)));
  const NEW_TAG_SIZE=10.5;
  // A two-line row's name starts clear of the selected row's 3px rule, as
  // board 5 insets it (final fix round, TA1-05: the rule painted over the
  // first letter, "ACTBLUE" read "CTBLUE"). Every two-line row takes the same
  // inset so the names stay in one column.
  const TWO_LINE_INSET=14;
  function draw(name,rows,onClick,options) {
    const opts=options||{};
    const data=opts.data||view;
    const container=opts.container||get(name);
    const footNode=opts.foot===null?null:(opts.foot||document.getElementById('graph-foot-'+name));
    if(!rows.length){emptyPanel(name,opts.empty||'No nonzero amounts recorded for this selection.',container,footNode);return;}
    container.replaceChildren();
    const shown=rows.slice(0,opts.topN||topN(name)),total=opts.total===undefined?denominator(name,data):opts.total;
    const spark=opts.spark===undefined?name==='companies':opts.spark;
    const party=opts.party===undefined?name==='candidates':opts.party;
    const twoLine=opts.twoLine===undefined?TWO_LINE.includes(name):opts.twoLine;
    const leftRead=twoLine||(opts.leftRead===undefined?LEFT_LABELLED.includes(name):opts.leftRead);
    const forms=opts.forms===undefined?(name==='companies'?'company':name==='candidates'?'person':''):opts.forms;
    const heading=opts.title||titleOf(name,data,opts.industry),denom=opts.denom||denomLabel(name,data);
    const pitch=twoLine?TWO_LINE_PITCH:(opts.pitch||ROW_HEIGHT);
    // Compare halves (02b) print whole dollars (R6c), and a category a scope
    // lacks arrives as amount null: a dash, no bar, nothing to click.
    const money=opts.money||dollars,has=row=>row.amount!=null;
    const marks=opts.marks instanceof Map?opts.marks:new Map(),tint=opts.tint instanceof Set?opts.tint:null;
    // A card row carries M4's 12px bar; the roomier two-line and drawer rows
    // keep the 14px bar their boards drew.
    const loose=twoLine||(pitch>ROW_HEIGHT&&!opts.compact),barH=loose?LOOSE_BAR_H:BAR_H,barRise=loose?LOOSE_BAR_RISE:BAR_RISE;
    const selected=opts.selected===undefined?(name==='organizations'?recipient:''):opts.selected;
    // The value column is as wide as the widest figure it has to hold, measured
    // at the size it is drawn at, rather than a fixed 96px guess.
    // An activity row prints what it reached beside what the industry gave, in
    // the longest of reachedForms that fits; a compare half is too narrow for
    // the pair and keeps its one amount, with the direct amount in its tooltip.
    const activity=name==='candidates'&&isActivity(data),pair=activity&&!opts.compact;
    const valueForms=row=>pair&&row.direct!=null?reachedForms(row,wholeDollars):[money(row.amount)];
    const valueWidth=shown.filter(has).reduce((wide,row)=>Math.max(wide,MentatTheme.measureText(valueForms(row)[0],LABEL_SIZE,MentatTheme.sans)),0);
    // A narrow half (1440: ~265px) has no room for the "new" tag after the
    // name, so there it takes the chip column; a wider half keeps G2's place.
    const tagInChips=Boolean(opts.compact)&&compactWidth(container)<340;
    const withChips=[...marks.values()].some(mark=>mark&&(mark.change||(mark.newTag&&tagInChips)));
    // A compare half measures its widest name (and a "new" tag after it) so
    // its label column is no wider than it needs and the bar has the rest.
    const labelWidth=opts.compact?shown.reduce((wide,row)=>{
      const parts=labelParts((row.rank?`#${row.rank} `:'')+row.label,party?row.party:'');
      const mark=marks.get(String(row.id));
      const tag=mark&&mark.newTag&&leftRead&&!twoLine&&!tagInChips?MentatTheme.measureText('new',NEW_TAG_SIZE,MentatTheme.sans)+20:0;
      return Math.max(wide,MentatTheme.measureText(parts.text+parts.suffix,LABEL_SIZE,MentatTheme.sans)+tag);
    },0):0;
    const shareWidth=!opts.compact?undefined:withChips?60:opts.share===false?0:undefined;
    // Final fix round (TA1-01..03): a compare half may give up its bar, its
    // share or the bar's room so that its widest name is not cut. The card
    // names the steps it allows (opts.yieldOrder, see compactYield) and reads
    // back what this half gave up from draw()'s answer, so every half of a
    // card can draw the same columns. A chip column is never given up.
    const asked={party:party&&!twoLine,spark,valueWidth,compact:Boolean(opts.compact),leftRead,shareWidth,labelWidth,bar:opts.bar,nameFirst:Boolean(opts.nameFirst)};
    const planned=opts.compact&&Array.isArray(opts.yieldOrder)
      ?compactYield(compactWidth(container),asked,withChips?opts.yieldOrder.filter(step=>step!=='share'):opts.yieldOrder):asked;
    const drawWidth=opts.compact?compactWidth(container):panelWidth(container);
    const box=layoutColumns(drawWidth,opts.compact?planned:Object.assign({},planned,{maxWidth:drawWidth}));
    const CHART_WIDTH=box.width,shareRight=box.shareRight,sparkX=box.sparkX,valueRight=box.valueRight;
    const LABEL_RIGHT=box.labelRight,BAR_LEFT=box.barLeft,barMax=box.barMax;
    const percentages=total>0&&shown.every(row=>!has(row)||row.amount>=0);
    const height=shown.length*pitch+ROW_TOP+(twoLine?18:-13); // a single-line row ends at top+pitch-19 (its hover box): 6px under the last row, as on M4 (fix round 2, R02c-M1; it was 29)
    const svg=svgNode('svg',{viewBox:`0 0 ${CHART_WIDTH} ${height}`,role:'img','aria-label':heading,class:'graph-bars'+(opts.animate?' graph-enter':'')});
    svg.append(svgNode('title',{},heading),svgNode('desc',{},`${activity?heading+'. ':''}${opts.caption??get('scope').textContent}. ${shown.length} of ${rows.length} groups. Values in USD.`));
    const amounts=shown.filter(has).map(r=>r.amount);
    const positive=Math.max(0,...amounts),negative=Math.min(0,...amounts);
    const scale=barMax/(positive-negative||1),zero=BAR_LEFT-negative*scale;
    // Group i takes the theme's categoryColour(i), as Chart Studio's groups do (2026-09-25); a theme without it cycles the palette.
    const colors=MentatTheme.palette,colourOf=typeof MentatTheme.categoryColour==='function'?MentatTheme.categoryColour:i=>colors[i%colors.length];
    // Recipient types: Conduit (ActBlue, WinRed) has a colour of its own and takes no slot, so every other type is the colour it is without it (theme.js categoryColours).
    const typed=name==='categories'&&typeof MentatTheme.categoryColours==='function'?MentatTheme.categoryColours(shown.map(row=>row.id)):null;
    let covered=0;
    shown.forEach((row,i)=>{
      const top=ROW_TOP+i*pitch;
      // Two-line rows put the name on the first line and the bar, value and
      // share on the second; single-line rows share one baseline.
      const y=top,barBase=twoLine?top+TWO_LINE_NAME_DROP:top;
      const label=row.label+(row.party?` · ${row.party}`:'');
      const percent=percentages&&has(row)?100*row.amount/total:null;
      if(percent!==null)covered+=percent;
      const dot=party?partyClass(row.party):'';
      const mark=marks.get(String(row.id))||null;
      const newChip=Boolean(mark&&mark.newTag)&&tagInChips;
      const newTag=Boolean(mark&&mark.newTag)&&leftRead&&!twoLine&&!newChip;
      const tagWidth=newTag?MentatTheme.measureText('new',NEW_TAG_SIZE,MentatTheme.sans)+14:0;
      // Measured, not counted: the budget is the pixels the name is given -
      // the whole card on a two-line row - and the party rides in the suffix
      // so that it survives the cut instead of being the first thing lost.
      const parts=labelParts((row.rank?`#${row.rank} `:'')+row.label,party?row.party:'');
      const budget=(twoLine?CHART_WIDTH-8-TWO_LINE_INSET:box.labelWidth)-(newTag?tagWidth+6:0);
      let text=MentatTheme.fitText(parts.text,budget,LABEL_SIZE,MentatTheme.sans,parts.suffix);
      // A name too long for its column takes nameForms()' longest short form
      // that fits before an ellipsis: a company without its legal suffix
      // ("Goldman Sachs", "Navy Federal"), as By company does (R02c-m2), and a
      // candidate with initials after the first given name ("WARNER, MARK R",
      // as G1 and G5 print it); the party code stays whole (final fix round,
      // TA1-01, TA1-02). The title and the accessible name keep the whole name.
      const lead=row.rank?`#${row.rank} `:'',base=row.label==null?'':String(row.label);
      if(forms&&text!==parts.text+parts.suffix&&parts.text===lead+base)
        text=lead+fitForm(nameForms(forms,base),budget-MentatTheme.measureText(lead+parts.suffix,LABEL_SIZE,MentatTheme.sans),LABEL_SIZE)+parts.suffix;
      // The accessible name is unchanged: name, colon, money. The share and the
      // per-cycle figures ride in <title>, which is the hover detail (item 12).
      // A drawer row is a reading, not a control: there is nothing behind it
      // to open, so it is not announced as a button and not in the tab order.
      const said=has(row)?reachedText(row,dollars(row.amount),dollars):'not recorded in this scope';
      const inert=opts.click===false||!has(row);
      const group=svgNode('g',inert
        ?{role:'listitem','aria-label':`${label}: ${said}`,class:'graph-bar-row'}
        :{role:'button',tabindex:0,'aria-label':`${label}: ${said}`,
          class:'graph-bar-button'+(selected&&row.id===selected?' is-selected':'')});
      group.append(svgNode('title',{},`${label} · ${said}`+(percent!==null?` · ${percent.toFixed(1)}% of ${denom}`:'')
        +(mark&&mark.change&&mark.change.against?` · ${mark.change.text} against ${mark.change.against}`:'')
        +(name==='categories'&&MentatTheme.categoryNote&&MentatTheme.categoryNote(row.id)?`\n${MentatTheme.categoryNote(row.id)}`:'')));
      const lit=opts.litIds instanceof Set&&opts.litIds.has(String(row.id));
      group.append(svgNode('rect',{x:0,y:top-15,width:CHART_WIDTH,height:pitch-4,rx:4,
        class:'graph-row'+((tint&&tint.has(String(row.id)))||lit?' is-shared':'')}));
      if(selected&&row.id===selected)
        group.append(svgNode('rect',{x:0,y:top-15,width:3,height:pitch-4,class:'graph-row-rule'}));
      if(dot)group.append(svgNode('circle',{cx:box.dotCX,cy:y-4,r:4.5,class:'graph-party '+dot}));
      const labelX=twoLine?TWO_LINE_INSET:(leftRead?box.labelLeft:LABEL_RIGHT);
      group.append(svgNode('text',{x:labelX,y,fill:MentatTheme.ink,'text-anchor':twoLine||leftRead?'start':'end','font-family':MentatTheme.sans,'font-size':LABEL_SIZE,class:row.rank?'graph-label graph-rank-label':'graph-label'},text));
      if(newTag){
        const at=labelX+MentatTheme.measureText(text,LABEL_SIZE,MentatTheme.sans)+6;
        group.append(svgNode('rect',{x:at,y:y-12,width:tagWidth,height:15,rx:7.5,class:'graph-new-box'}));
        group.append(svgNode('text',{x:at+tagWidth/2,y:y-1,'text-anchor':'middle','font-family':MentatTheme.sans,'font-size':NEW_TAG_SIZE,'font-weight':600,class:'graph-new-text'},'new'));
      }
      if(has(row)&&box.barMax>0){
        const fill=typeof opts.colorFor==='function'?opts.colorFor(row,i):row.amount<0?MentatTheme.negative:typed?typed[i]:colourOf(i);
        const bar=svgNode('rect',{x:Math.min(zero,zero+row.amount*scale),y:barBase-barRise,width:Math.max(1,Math.abs(row.amount*scale)),height:barH,rx:2,fill,class:'graph-mark'});
        if(opts.animate)bar.style.setProperty('--i',String(Math.min(i,MOTION_STEPS)));
        group.append(bar);
      }
      group.append(svgNode('text',{x:valueRight,y:barBase,fill:MentatTheme.ink,'text-anchor':'end','font-family':MentatTheme.sans,'font-size':LABEL_SIZE,class:'graph-value'},has(row)?(pair&&row.direct!=null?fitForm(valueForms(row),box.valueWidth,LABEL_SIZE):money(row.amount)):'—'));
      if(newChip){
        const chipWidth=MentatTheme.measureText('new',NEW_TAG_SIZE,MentatTheme.sans)+14;
        group.append(svgNode('rect',{x:shareRight-chipWidth,y:barBase-12,width:chipWidth,height:16,rx:8,class:'graph-new-box'}));
        group.append(svgNode('text',{x:shareRight-chipWidth/2,y:barBase,'text-anchor':'middle','font-family':MentatTheme.sans,'font-size':NEW_TAG_SIZE,'font-weight':600,class:'graph-new-text'},'new'));
      } else if(mark&&mark.change){
        // The change chip takes the share's place: plum, never a party colour,
        // and only ever in cycle mode (G2).
        const chipText=(mark.change.direction==='up'?'↑ ':mark.change.direction==='down'?'↓ ':'')+mark.change.text;
        const chipWidth=MentatTheme.measureText(chipText,NEW_TAG_SIZE,MentatTheme.sans)+10;
        group.append(svgNode('rect',{x:shareRight-chipWidth,y:barBase-12,width:chipWidth,height:16,rx:5,class:'graph-change-box'}));
        group.append(svgNode('text',{x:shareRight-5,y:barBase,'text-anchor':'end','font-family':MentatTheme.sans,'font-size':NEW_TAG_SIZE,'font-weight':600,class:'graph-change graph-change-text'},chipText));
      } else if(box.shareWidth>0)group.append(svgNode('text',{x:shareRight,y:barBase,fill:MentatTheme.muted,'text-anchor':'end','font-family':MentatTheme.sans,'font-size':SHARE_SIZE,class:'graph-share'},percent!==null?percent.toFixed(1)+'%':(has(row)?'—':'')));
      if(spark)group.append(svgNode('g',{class:'graph-spark','data-spark':row.id,'data-width':box.sparkWidth,transform:`translate(${sparkX},${barBase-11})`}));
      if(!inert){
        const select=()=>onClick(row.id);group.addEventListener('click',select);
        group.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select();}});
      }
      svg.append(group);
    });
    if(Number(opts.dividerAfter)>=0&&opts.dividerAfter<shown.length-1){
      const ruleY=ROW_TOP+(opts.dividerAfter+1)*pitch-17.5;
      svg.append(svgNode('line',{x1:0,x2:CHART_WIDTH,y1:ruleY,y2:ruleY,class:'graph-divider'}));
    }
    if(opts.click===false)svg.setAttribute('role','list');
    container.append(svg);
    if(opts.animate)settle(svg);
    if(opts.values!==false)appendValues(container,name,rows,onClick);
    // A redrawn footer starts without the sparkline's hover (drawSparklines
    // gives it back when the quarters arrive).
    if(footNode){footNode.textContent=footText(name,shown.length,rows.length,percentages,covered);footNode.removeAttribute('title');}
    if(opts.values!==false)labelValues(name,shown.length,rows.length);
    return {bar:planned.bar!==false,share:planned.shareWidth!==0,nameFirst:Boolean(planned.nameFirst)};
  }
  // Item 11: employee level is one stacked bar with a legend. Each segment is
  // still a .graph-bar-button carrying the same accessible name the bars use,
  // so the keyboard path, the export preview count and the click-through into
  // the level are all unchanged. Board M4 adds "By company" under the legend
  // (ruling R1b): one thin stacked bar per company, the companies and order of
  // Company comparison, as many as this card's own Top N - which had only
  // three levels to trim, so every level is drawn now. An answer without
  // levels_by_company (a console not yet restarted on the new server) shows
  // no By company list and keeps the old footer.
  const STACK_H=16;
  function byCompanyNames(name,data,opts) {
    if(opts.byCompany===false||!data||!Array.isArray(data.levels_by_company))return null;
    // A compare half (phase 02b) may pass its own list: cycle mode aligns
    // both halves on scope 1's companies.
    const names=Array.isArray(opts.byCompany)?opts.byCompany:(data.companies||[]).map(row=>row.id);
    const shown=names.slice(0,opts.topN||topN(name));
    return shown.length?shown:null;
  }
  // One thin stacked bar per company, its levels in the legend's order and
  // colours, and the shares at the right. A share is of that company's own
  // non-Corporate giving (levels_by_company[].amount), never of
  // companies[].amount, which counts Corporate giving. A company with no such
  // giving - or a signed split with no honest share - shows an empty track and
  // a dash per level. Company names never carry a party dot.
  function drawByCompany(host,data,names,levels,animate) {
    const split=new Map(data.levels_by_company.map(row=>[row.company,row]));
    const block=document.createElement('div');block.className='graph-by-company'+(animate?' graph-enter':'');
    const heading=document.createElement('p');heading.className='graph-by-company-title';heading.textContent='By company';
    const list=document.createElement('ol');list.className='graph-by-company-rows';list.setAttribute('aria-label','Employee level by company');
    names.forEach((company,i)=>{
      const entry=split.get(company);
      const amounts=new Map((entry?entry.levels:[]).map(level=>[level.id,Number(level.amount)||0]));
      const total=entry?Number(entry.amount)||0:0;
      const usable=total>0&&[...amounts.values()].every(value=>value>=0);
      const row=document.createElement('li');row.className='graph-by-company-row';
      const label=document.createElement('span');label.className='graph-by-company-name';label.textContent=company;
      const track=document.createElement('span');track.className='graph-by-company-track';track.setAttribute('aria-hidden','true');
      const shares=document.createElement('span');shares.className='graph-by-company-shares';shares.setAttribute('aria-hidden','true');
      const spoken=[],detail=[];
      levels.forEach(level=>{
        const amount=amounts.get(level.id)||0,percent=usable?100*amount/total:null;
        if(percent!==null&&amount>0){
          const part=document.createElement('i');part.style.width=percent+'%';part.style.background=level.fill;
          if(animate)part.style.setProperty('--i',String(Math.min(i,MOTION_STEPS)));
          track.append(part);
        }
        const cell=document.createElement('span');cell.textContent=percent===null?'—':Math.round(percent)+'%';shares.append(cell);
        spoken.push(`${level.label} ${percent===null?'no share':Math.round(percent)+'%'}`);
        detail.push(`${level.label} ${dollars(amount)}`+(percent===null?'':` (${percent.toFixed(1)}%)`));
      });
      row.setAttribute('aria-label',`${company}: ${spoken.join(', ')}`);
      row.title=`${company} · `+(entry?detail.join(' · '):'no personal giving in this scope');
      row.append(label,track,shares);list.append(row);
    });
    block.append(heading,list);host.append(block);/* Fix round 2 (R02c-m2): a name too long for its column (G1's 254px halves at 1440) takes nameForms()' shorter form ("Goldman Sachs"), not an ellipsis; the row's title and label keep the whole name. */for(const cell of list.querySelectorAll('.graph-by-company-name'))if(cell.clientWidth&&cell.scrollWidth>cell.clientWidth+0.5)cell.textContent=fitForm(nameForms('company',cell.textContent),cell.clientWidth,11);
  }
  function drawStack(name,rows,onClick,options) {
    const opts=options||{};
    const data=opts.data||view;
    if(!rows.length){emptyPanel(name,'No nonzero amounts recorded for this selection.',opts.container,opts.foot);return;}
    const container=opts.container||get(name);container.replaceChildren();
    const footNode=opts.foot===null?null:(opts.foot||document.getElementById('graph-foot-'+name));
    // A compare half (02b) passes its levels in scope 1's order so the halves
    // line up; a level this scope lacks is amount null - a dash, no segment.
    const has=row=>row.amount!=null;
    const shown=rows,total=denominator(name,data);
    const percentages=total>0&&shown.every(row=>!has(row)||row.amount>=0);
    const width=opts.compact?compactWidth(container):panelWidth(container),height=STACK_H+4;
    const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':titleFor[name],class:'graph-bars graph-stack'+(opts.animate?' graph-enter':'')});
    svg.append(svgNode('title',{},titleFor[name]),svgNode('desc',{},`${opts.caption??get('scope').textContent}. ${shown.length} of ${rows.length} levels. Values in USD.`));
    const span=shown.reduce((sum,row)=>sum+Math.max(0,row.amount||0),0)||1;
    // Level i takes categoryColour(i), as a group does in draw() (2026-09-25).
    const colors=MentatTheme.palette,colourOf=typeof MentatTheme.categoryColour==='function'?MentatTheme.categoryColour:i=>colors[i%colors.length],legend=document.createElement('div'),levels=[];
    legend.className='graph-legend';
    let x=0,covered=0;
    shown.forEach((row,i)=>{
      const percent=percentages&&has(row)?100*row.amount/total:null;
      if(percent!==null)covered+=percent;
      const fill=has(row)&&row.amount<0?MentatTheme.negative:colourOf(i);
      levels.push({id:row.id,label:row.label,fill});
      const select=()=>onClick(row.id);
      if(has(row)){
        const wide=percentages?Math.max(2,width*Math.max(0,row.amount)/span):Math.max(2,width/shown.length);
        const group=svgNode('g',{role:'button',tabindex:0,'aria-label':`${row.label}: ${dollars(row.amount)}`,class:'graph-bar-button'});
        group.append(svgNode('title',{},`${row.label} · ${dollars(row.amount)}`+(percent!==null?` · ${percent.toFixed(1)}% of ${denomLabel(name,data)}`:'')));
        const mark=svgNode('rect',{x,y:2,width:wide,height:STACK_H,rx:2,fill,class:'graph-mark'});
        if(opts.animate)mark.style.setProperty('--i',String(Math.min(i,MOTION_STEPS)));
        group.append(mark);
        group.addEventListener('click',select);group.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select();}});
        svg.append(group);
        x+=wide;
      }
      const item=document.createElement('button');item.type='button';item.className='graph-legend-item';
      item.title=`${row.label} · `+(has(row)?dollars(row.amount):'not recorded in this scope');
      const swatch=document.createElement('i');swatch.style.background=fill;
      const caption=document.createElement('span');
      // M4's legend names the amount; a half (G1) has room for the share only.
      caption.textContent=!has(row)?`${row.label} —`:opts.compact
        ?`${row.label} `+(percent!==null?`${percent.toFixed(1)}%`:wholeDollars(row.amount))
        :`${row.label} ${wholeDollars(row.amount)}`+(percent!==null?` · ${percent.toFixed(1)}%`:'');
      item.append(swatch,caption);
      if(has(row))item.onclick=select;else item.disabled=true;
      legend.append(item);
    });
    container.append(svg,legend);
    // A half's legend is one line under its bar, as G1 and G2 draw it; when
    // the three items do not fit it becomes a two-column grid rather than one
    // item wrapping alone, and wraps as before only if even that is too wide
    // (final fix round, TA1-04; graph.css holds the three states).
    if(opts.compact&&legend.clientWidth){
      if(legend.scrollWidth>legend.clientWidth+0.5)legend.classList.add('is-grid');
      if(legend.scrollWidth>legend.clientWidth+0.5)legend.classList.replace('is-grid','is-wrap');
    }
    if(opts.animate)settle(svg);
    const companies=byCompanyNames(name,data,opts);
    if(companies)drawByCompany(container,data,companies,levels,Boolean(opts.animate));
    if(opts.values!==false)appendValues(container,name,rows,onClick);
    if(footNode){
      // With the By company list the footer counts its companies, as board M4
      // does ("Showing 10 of 10 - 100.0% of net giving"): the share is the
      // listed companies' part of net giving, Company comparison's own figure.
      if(companies){
        const ranked=new Map((data.companies||[]).map(row=>[row.id,Number(row.amount)||0]));
        const amounts=companies.map(company=>ranked.get(company)||0);
        const usable=data.totals.giving>0&&amounts.every(value=>value>=0);
        footNode.textContent=footText('companies',companies.length,(data.companies||[]).length,usable,
          usable?100*amounts.reduce((sum,value)=>sum+value,0)/data.totals.giving:0);
      } else footNode.textContent=footText(name,shown.length,rows.length,percentages,covered);
    }
    if(opts.values!==false)labelValues(name,shown.length,rows.length);
  }
  /* One card from one /api/graph answer into one host. drawPanel() below
     paints the primary answer into the page's own panels; a compare half
     (phase 02b) passes its own scope's answer and its own host:
       paintPanel(name, answer, {container, foot},
                  {animate, topN, caption, onClick, values:false, byCompany})
     Nothing here reads another scope's state: denominators, labels and the
     By company list all come from `data`. Sparklines stay the primary's. */
  function paintPanel(name,data,host,options) {
    const place=host||{},opts=options||{};
    const onClick=opts.onClick||(()=>{});
    const common={data,container:place.container,foot:place.foot,animate:opts.animate,topN:opts.topN,
      caption:opts.caption,values:opts.values,byCompany:opts.byCompany,selected:opts.selected};
    if(name==='candidates'&&!data.candidate_available){
      // F4: the server's own reason ("No estimate bundle for this industry yet;
      // rebuild the workbook", "Not traced: ...") rather than an empty panel.
      emptyPanel('candidates',data.candidate_note||'This candidate view is unavailable for the selected scope.',place.container,place.foot,data.candidate_note_title||'');return;
    }
    if(name==='levels')drawStack(name,data.levels||[],onClick,common);
    else draw(name,data[name]||[],onClick,common);
  }
  function drawPanel(name,animate) {
    if(name==='drawer'){drawDrawerRows(animate);return;}
    if(!view)return;
    if(compare){paintCompareCard(name,animate);return;}
    const panel=get(name);
    if(panel){drawnWidth.set(name,Math.round(panel.clientWidth));panel.classList.remove('graph-halves');panel.style.removeProperty('--scopes');}
    paintPanel(name,view,{},{animate,onClick:PANEL_CLICK[name]});
    if(name==='companies')fillSparklines();
  }
  function paintAll(animate) { for(const name of PANELS)drawPanel(name,animate); drawDrawerRows(animate); }
  for(const name of PANELS) {
    const select=document.getElementById('graph-top-'+name);
    if(select)select.onchange=()=>{if(view)drawPanel(name,false);};
  }
  // A panel is redrawn when its own column changes width, and only then: the
  // drawing is sized in the viewBox, so a stale one is scaled by CSS rather
  // than relaid. A redraw at the same rounded width would repaint the same
  // pixels, and a redraw at no width at all would put the 520 fallback back,
  // so both are skipped - which is also what makes the observer unable to
  // feed itself.
  const drawnWidth=new Map(),resized=new Set();
  let resizeTimer=0;
  const RESIZE_DEBOUNCE=120;
  function redrawResized() {
    resizeTimer=0;
    const names=[...resized];resized.clear();
    if(!view)return;
    // A resize is not a scope change, so nothing grows again: the bars are
    // relaid at the new width and left where they are.
    for(const name of names)drawPanel(name,false);
  }
  if(typeof ResizeObserver!=='undefined') {
    const panelObserver=new ResizeObserver(entries=>{
      for(const entry of entries) {
        const raw=entry.target.id.replace('graph-','');
        const name=raw==='drawer-rows'?'drawer':raw,width=Math.round(entry.target.clientWidth);
        if(width>0&&drawnWidth.get(name)!==width)resized.add(name);
      }
      if(!resized.size)return;
      clearTimeout(resizeTimer);resizeTimer=setTimeout(redrawResized,RESIZE_DEBOUNCE);
    });
    for(const name of [...PANELS,'drawer-rows']){const node=get(name);if(node)panelObserver.observe(node);}
  }
  /* ------------------------------------------------------- section folds
     Paul, 2026-09-23: "You should be able to expand each section individually
     like with a census panel". The census panel is the PAC evidence map's
     Sources disclosure (evidence.js .map-source-details): one muted header
     line with a disclosure triangle, and everything under it folds away. Each
     card in FOLD_SECTIONS gets that triangle on its heading, built here so the
     golden index.html does not change. Every card starts open - nothing
     stored is the board's look - and a press is remembered per section in
     this browser (MentatState, as Chart Studio's panes are), never in the URL.
     graph.css draws a folded card as its heading line across the row and
     gives the card that shared its row the whole row; the observer above
     relays that card's bars at the new width, so the chart left open is the
     one that grows. */
  function setFolded(section,folded,remember) {
    section.classList.toggle('is-folded',folded);
    const button=section.querySelector(':scope>.panel-heading>.graph-fold');
    if(button){
      button.setAttribute('aria-expanded',String(!folded));
      button.title=folded?'Expand this section':'Collapse this section';
    }
    if(remember)globalThis.MentatState?.remember?.(FOLD_KEY+section.id,folded?'1':'0');
  }
  for(const name of FOLD_SECTIONS) {
    const section=get(name),heading=section&&section.querySelector(':scope>.panel-heading');
    const title=heading&&heading.querySelector(':scope>h2');
    if(!title)continue;
    if(!title.id)title.id=section.id+'-title';
    const button=document.createElement('button');
    button.type='button';button.className='graph-fold';
    button.setAttribute('aria-controls',section.id);
    button.setAttribute('aria-labelledby',title.id);
    heading.prepend(button);
    const toggle=()=>setFolded(section,!section.classList.contains('is-folded'),true);
    button.addEventListener('click',toggle);
    // The census panel's whole header line is its control; so is this title,
    // for a pointer (not after a text selection). The button stays the one
    // keyboard stop, so the title adds nothing to the tab order.
    title.addEventListener('click',()=>{if(!String(globalThis.getSelection?.()||''))toggle();});
    setFolded(section,globalThis.MentatState?.recall?.(FOLD_KEY+section.id)==='1',false);
  }
  /* --------------------------------------------------- company sparklines */
  // One verified contribution query supplies company-by-quarter observations.
  // This avoids the former fan-out of one Graph request per election cycle and
  // makes the time scale explicit. Missing quarters stay gaps: they are never
  // manufactured as zero-dollar observations.
  const sparkCache=new Map();
  // One contributions catalog per page load, shared by every industry, and
  // one session per industry (sparkAccessModel above, perf H11).
  const sparkAccess=sparkAccessModel({
    session:industry=>fetch('/api/contributions/session?industry='+encodeURIComponent(industry),{cache:'no-store'}).then(async response=>{const body=await response.json();if(!response.ok||body.error||!body.token)throw new Error(body.error||'Contribution session unavailable.');return body;}),
    catalog:()=>fetch('/api/contributions/catalog',{cache:'no-cache'}).then(async response=>{const body=await response.json();if(!response.ok||body.error)throw new Error(body.error||'Contribution catalog unavailable.');return body;})});
  const SPARK_PERIODS=8,SPARK_MINIMUM=3;
  const quarterIndex=value=>{const match=/^(\d{4})-Q([1-4])$/.exec(String(value));return match?Number(match[1])*4+Number(match[2])-1:null;};
  // `data` is the answer the sparklines belong to: the page's own view, or a
  // compared scope's (02b), whose company, cycle and funding they follow.
  function sparkRequest(dataset,data=view) {
    if(!data||data.filters.tier)return null;
    const scoped=[];
    if(data.filters.company)scoped.push({field:'company',op:'eq',value:data.filters.company});
    if(data.filters.cycle){const end=Number(data.filters.cycle);if(Number.isFinite(end))scoped.push({field:'cycle',op:'eq',value:`${end-1}-${end}`});}
    if(data.filters.funding==='personal')scoped.push({field:'funding_source',op:'eq',value:'employee_original'});
    if(data.filters.funding==='corporate')scoped.push({field:'funding_source',op:'eq',value:'corporate_original'});
    if(category)scoped.push({field:'recipient_category',op:'eq',value:category});
    if(recipient)scoped.push({field:'committee_id',op:'eq',value:recipient});
    return {dataset,basis:'receipt',dimensions:['company','quarter'],measures:['amount'],filters:scoped,
      aggregate_filters:[],sort:[{field:'quarter',direction:'asc'}],limit:100000,offset:0};
  }
  async function sparkReady(request,token) {
    for(let attempt=0;attempt<120;attempt++){
      const response=await fetch('/api/contributions/query',{method:'POST',headers:{'Content-Type':'application/json','X-Mentat-Token':token},body:JSON.stringify(request)});
      const body=await response.json();if(!response.ok||body.error||body.status==='failed')throw new Error(body.error||body.message||'Quarterly contribution data unavailable.');
      if(!['preparing','building','queued'].includes(body.status))return body;
      await new Promise(resolve=>setTimeout(resolve,Math.max(500,Math.min(4000,body.retry_after_ms||1000))));
    }
    throw new Error('Quarterly contribution data is still preparing.');
  }
  // With no target this fills Company comparison from the page's own view; a
  // compare half passes {svg, data, industry, foot} for its own drawing.
  async function fillSparklines(target) {
    const own=!target;
    const ticket=revision,data=own?view:target.data,industry=own?get('industry').value:target.industry;
    if(!data||!industry||data.filters.tier)return;
    try{
      const access=await sparkAccess.source(industry,ticket),request=sparkRequest(access.dataset,data);
      if(!request||ticket!==revision)return;
      const key=JSON.stringify(request);
      if(!sparkCache.has(key))sparkCache.set(key,sparkReady(request,access.token).catch(error=>{sparkCache.delete(key);throw error;}));
      const answer=await sparkCache.get(key);
      if(ticket!==revision||!view||!Array.isArray(answer.rows))return;
      if(!own&&!target.svg.isConnected)return;
      const periods=[...new Set(answer.rows.map(row=>String(row.quarter||'')).filter(period=>quarterIndex(period)!==null))]
        .sort((a,b)=>quarterIndex(a)-quarterIndex(b)).slice(-SPARK_PERIODS);
      if(periods.length<SPARK_MINIMUM)return;
      const wanted=new Set(periods),series=new Map();
      for(const row of answer.rows){const period=String(row.quarter||'');if(!wanted.has(period)||!row.company||!Number.isFinite(Number(row.amount)))continue;if(!series.has(row.company))series.set(row.company,[]);series.get(row.company).push([period,Number(row.amount)/100]);}
      drawSparklines(series,periods,own?null:target);
    }catch(_){/* The bars remain complete when optional quarterly detail is unavailable. */}
  }
  function drawSparklines(series,periods,target) {
    const svg=target?target.svg:get('companies').querySelector('svg');
    if(!svg)return;
    const first=quarterIndex(periods[0]),last=quarterIndex(periods[periods.length-1]),periodSpan=Math.max(1,last-first);
    for(const slot of svg.querySelectorAll('[data-spark]')) {
      const points=(series.get(slot.dataset.spark)||[]).slice().sort((a,b)=>quarterIndex(a[0])-quarterIndex(b[0]));
      if(points.length<SPARK_MINIMUM)continue;
      const values=points.map(point=>point[1]),low=Math.min(...values),high=Math.max(...values);
      // The polyline is drawn into the same slot the layout reserves for it,
      // with its end dot inside: a 54px line in a 46px slot is how the last
      // point used to land past the right edge of the viewBox.
      const span=high-low||1,wide=(Number(slot.dataset.width)||SPARK_W)-2,tall=14;
      const coords=points.map(point=>[(quarterIndex(point[0])-first)*wide/periodSpan,tall-2-((point[1]-low)/span)*(tall-4)]);
      slot.replaceChildren();
      // The line is drawn inside its own group: the slot carries a transform
      // ATTRIBUTE for its position, and a CSS transform on the same element
      // would replace it rather than compose with it.
      const ink=svgNode('g',{class:'graph-spark-draw'}),segments=[];let segment=[];
      points.forEach((point,index)=>{if(index&&quarterIndex(point[0])-quarterIndex(points[index-1][0])>1){if(segment.length>1)segments.push(segment);segment=[];}segment.push(coords[index]);});if(segment.length>1)segments.push(segment);
      for(const line of segments)ink.append(svgNode('polyline',{fill:'none','stroke-width':1.5,class:'graph-spark-line',points:line.map(([x,y])=>`${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}));
      ink.append(svgNode('circle',{cx:coords[coords.length-1][0].toFixed(1),cy:coords[coords.length-1][1].toFixed(1),r:1.8,class:'graph-spark-dot'}));
      slot.append(ink);
      const title=slot.parentElement&&slot.parentElement.querySelector('title');
      if(title&&!title.textContent.includes(points[0][0]))
        title.textContent+=' · '+points.map(([cycle,value])=>`${cycle} ${dollars(value)}`).join(' · ');
    }
    const foot=target?target.foot:document.getElementById('graph-foot-companies');
    // Board M4's footer names the sparkline's range on the footer's one line
    // ("... · sparkline 2022 · 2024 · 2026 (i)"). The missing-data rule is the
    // card's (i) note beside it ("missing quarters remain gaps, not zeroes")
    // and the footer's own hover; spelled out on the line as well, it wrapped
    // the footer to two lines at 1440-1920 and made the row 17px taller than
    // the board's (r5 men-graph).
    if(foot&&!foot.textContent.includes('sparkline')){
      foot.textContent+=` · sparkline ${periods[0]}–${periods[periods.length-1]}`;
      foot.title=`Sparkline: observed quarters ${periods[0]}–${periods[periods.length-1]}; a missing quarter is a gap, not zero.`;
    }
  }
  /* ------------------------------------------------ where the reader has been
     One in-memory stack of the scope each drill-down was made FROM. It is not a
     second store of navigational state: nothing reaches localStorage or
     sessionStorage, the URL stays the only persisted scope, and a new industry
     empties it because none of its entries could be restored into it. Paul's
     ruling C stands - replaceState, never pushState - so the browser's own Back
     leaves the console, and this is the only way back inside it. */
  let PAST=[];
  let pendingDrawer=null;
  function snapshot() {
    return {company:get('company').value,tier:get('tier').value,cycle:get('cycle').value,
      funding:get('funding').value,mode:get('mode').value,direction:get('direction').value,
      category,recipient,analysis,
      drawer:drawer?drawer.id:'',drawerDirection:drawer?drawer.direction:''};
  }
  function remember() { PAST=pushPast(PAST,snapshot()); }
  function back() {
    const past=PAST.pop();
    if(!past)return;
    for(const name of ['company','tier','cycle','funding','mode','direction'])
      if(name==='direction')setDirection(past.direction);
      else if([...get(name).options].some(option=>option.value===past[name]))get(name).value=past[name];
    // The sides measured for the scope being left are not this one's.
    directionTotals=null;
    category=past.category;recipient=past.recipient;analysis=past.analysis||analysis;
    closeDrawer(false);
    pendingDrawer=past.drawer?{id:past.drawer,direction:past.drawerDirection}:null;
    syncControls();applyAnalysis();refresh();
  }
  // The parts of the trail, left to right. Pressing one clears it and everything
  // to its right; the industry cannot be cleared - a Graph with no industry has
  // nothing to show - so pressing it returns to the whole industry.
  function recipientLabel(id) {
    return (view&&view.options.recipients.find(r=>r.id===id)||{}).label||id;
  }
  function trailParts() {
    return trailModel({industry:get('industry').value,
      company:(view&&view.filters.company)||get('company').value,
      category:category,recipient:recipient?recipientLabel(recipient):''});
  }
  function clearFrom(index) {
    remember();
    for(const key of clearedBy(index)) {
      if(key==='company')get('company').value='';
      if(key==='category')category='';
      if(key==='recipient')recipient='';
    }
    closeDrawer(false);pendingDrawer=null;
    refresh();
  }
  function renderTrail() {
    const host=get('trail-parts'),row=get('trail');
    if(!host||!row)return;
    const parts=trailParts();
    host.replaceChildren();
    // 02b: while comparing, the overline names the comparison (R-overline).
    if(compare){const part=document.createElement('span');part.className='graph-trail-part graph-trail-compare';
      part.setAttribute('aria-current','page');part.textContent=compareOverline();host.append(part);}
    else parts.forEach((part,at)=>{
      if(at){const mark=document.createElement('span');mark.className='graph-trail-mark';
        mark.setAttribute('aria-hidden','true');mark.textContent='\u203a';host.append(mark);}
      const button=document.createElement('button');
      button.type='button';button.className='graph-trail-part';
      button.textContent=part.label;
      button.title=at?`Back to ${parts[at-1].label}`:'Back to the whole industry';
      if(at===parts.length-1)button.setAttribute('aria-current','page');
      button.onclick=()=>clearFrom(part.index);
      host.append(button);
    });
    get('back').hidden=!drawer&&!PAST.length;
    get('back').textContent=drawer?'← Back to all recipients':'← Back';
    get('back').title=drawer?'Back to all recipients':'Back to the previous scope';
    row.hidden=(!compare&&parts.length<1)||get('industry').closest('#view-graph').hidden;
  }

  /* ------------------------------------------------------- downstream drawer
     The second hop has always been on the server: /api/graph with mode=activity
     and a recipient answers the candidates that committee went on to fund. It
     was unreachable because a PAC click only narrowed the page, and the panel
     the answer lands in is never on screen at the same time as the panel the
     PAC was clicked in. So the drawer asks for itself, beside the panel that
     was clicked, and leaves the page's own Candidate view alone. */
  // Board 5's word is "Contributions"; the rest of the recorded bucket is
  // "Other" (Paul, 2026-09-23: "It should be two separate segments"). Amounts
  // whose source records no transaction type keep the combined name. The one
  // table is the pure DRAWER_SIDE_LABEL above, which drawerOverlap also reads.
  const DIRECTION_LABEL=DRAWER_SIDE_LABEL;
  // What a split side holds, in its tooltip: the recorded FEC codes behind it
  // and, when a candidate is on more than one of the recorded sides, how many
  // are (drawerOverlap; the halves' amounts add up to the recorded bucket,
  // their counts need not). overlap is undefined only when the answer does
  // not count the bucket (a console started before contribution_other_union):
  // then the caveat is said whenever both halves hold candidates.
  function sideBasis(row,overlap,both) {
    const codes=Array.isArray(row.transaction_types)?row.transaction_types:[];
    const shared=overlap?(overlap.sides.includes(row.direction)?`. ${overlap.basis(row.direction)}`:''):
      overlap===undefined&&both&&(row.direction==='Contribution'||row.direction==='Other')
        ?'. A candidate with both kinds of activity is counted on both sides':'';
    if(row.direction==='Contribution'||row.direction==='Other')
      return (codes.length?`. Recorded as FEC transaction type${codes.length>1?'s':''} ${codes.join(', ')}`:'')+shared;
    if(row.direction==='Contribution / Other')
      return '. The saved source records no transaction type for these amounts, so they are not split into Contributions and Other'+shared;
    return '';
  }
  let drawer=null,drawerController=null;
  function writeDrawerScope() {
    globalThis.MentatScope?.write({drawer:drawer?drawer.id:'',
      drawerDirection:drawer&&drawer.direction?drawer.direction:''});
  }
  function closeDrawer(write) {
    drawerController?.abort();drawerController=null;
    if(!drawer)return;
    const panel=get('drawer-panel');
    drawer=null;
    // It leaves the way it came in. The card is kept for the length of the
    // slide and hidden after it, so the grid column does not collapse out
    // from under a card that is still on screen.
    if(panel&&!panel.hidden){
      panel.classList.add('is-closing');
      setTimeout(()=>{panel.classList.remove('is-closing');applyDrawer();},
        motionMs()<=1?0:420);
    } else applyDrawer();
    if(write!==false)writeDrawerScope();
  }
  function drawerCycle() {
    const owner=drawer&&drawer.scope&&compare?(compareScopes()[drawer.scope]||{}).data:view;
    const value=(owner&&owner.filters.cycle)||'';
    return value?`${Number(value)-1}\u2013${value}`:'all saved cycles';
  }
  async function openDrawer(id,preferred,scope) {
    drawerController?.abort();
    const mine=new AbortController();drawerController=mine;
    // Paul, 2026-09-23 (board 5): the explorer starts collapsed. A different
    // committee, or a drawer opened after one was closed, starts it collapsed
    // again; switching sides within one drawer keeps what the reader chose.
    if(!drawer||drawer.id!==id)get('drawer-panel')?.classList.remove('graph-drawer-wide');
    // 02b: a compared scope's row opens that scope's drawer (R21).
    const index=compare&&Number(scope)>0&&compare.values[Number(scope)]!==undefined?Number(scope):0;
    const owner=index?(compareScopes()[index]||{}).data:view;
    const label=((owner&&owner.options&&owner.options.recipients||[]).find(row=>row.id===id)||{}).label||recipientLabel(id);
    drawer={id,label,scope:index,direction:preferred||'',totals:[],rows:[],note:'',loading:true,error:''};
    applyDrawer();
    // The drawer's own request. It never touches #graph-mode: the page stays on
    // the candidate view the reader chose, and this asks activity for itself.
    const ask=async direction=>{
      const query=index?scopeParams(index,compareBase(null)):filters();
      query.set('recipient',id);query.set('mode','activity');query.set('direction',direction);
      const answer=await graphRead('/api/graph?'+query,mine.signal),body=answer.body;
      if(!answer.ok)throw new Error(body.error||'Downstream activity is unavailable for this committee.');
      return body;
    };
    try {
      let direction=preferred||get('direction').value||'Support';
      let body=await ask(direction);
      const totals=Array.isArray(body.direction_totals)?body.direction_totals:[];
      // direction_totals is the same numbers whichever side was asked for, so
      // opening on the largest side costs one extra request at most, and none
      // when the side asked for is already the largest. A side the drawer does
      // not offer - an old permalink's 'Contribution / Other' on a report that
      // records transaction types, split since Paul's "It should be two
      // separate segments" (2026-09-23) - opens as a fresh click does.
      const offered=drawerSides(totals).map(row=>row.direction);
      if((!preferred||!offered.includes(preferred))&&totals.length){
        const best=drawerSides(totals).sort((a,b)=>Math.abs(b.amount)-Math.abs(a.amount))[0];
        if(best&&(Math.abs(best.amount)>0||!offered.includes(direction))&&best.direction!==direction){
          direction=best.direction;
          if(body.candidates_by_direction?.[direction]) body={...body,candidates:body.candidates_by_direction[direction]};
          else body=await ask(direction);
        }
      }
      if(drawerController!==mine)return;
      drawer={id,label,scope:index,direction,totals,
        rows:body.candidate_available?(body.candidates||[]):[],
        relationships:body.pac_candidate_relationships||[],
        allDirections:body.candidates_by_direction||{},edges:body.downstream_edges||[],
        union:body.contribution_other_union,
        sources:body.downstream_sources||[],comparisons:body.measurement_comparisons||[],
        trace:body.transfer_trace||null,
        note:body.candidate_note||'',loading:false,
        error:body.candidate_available?'':(body.candidate_note||'This committee has no saved downstream activity.')};
      applyDrawer();writeDrawerScope();
    } catch(e) {
      if(e.name==='AbortError'||drawerController!==mine)return;
      drawer={id,label,scope:index,direction:preferred||'',totals:[],rows:[],note:'',loading:false,error:e.message};
      applyDrawer();writeDrawerScope();
    }
  }
  function drawDirections() {
    const host=get('drawer-directions');
    if(!host)return;
    // drawerSides (pure, above) decides which sides are offered: Contributions
    // and Other beside Support and Oppose; Unknown, and amounts no transaction
    // type splits, only when they hold something - an empty cell is a
    // question nobody asked.
    const sides=drawerSides(drawer.totals);
    // Round-3 verify (D1), under Paul's "It should be two separate segments":
    // each segment counts its own candidates, so when one candidate is on
    // both, the line under the segments says how many different candidates
    // they hold (drawerOverlap, from the server's count of the bucket).
    const overlap=drawer.union===undefined?undefined:drawerOverlap(drawer.totals,drawer.union);
    const both=['Contribution','Other'].every(name=>sides.some(row=>row.direction===name&&Number(row.candidates)>0));
    host.replaceChildren(...sides.map(row=>{
      const button=document.createElement('button');
      button.type='button';button.className='graph-drawer-side';
      button.setAttribute('aria-pressed',String(row.direction===drawer.direction));
      const name=document.createElement('span');name.className='graph-drawer-side-name';
      name.textContent=DIRECTION_LABEL[row.direction]||row.direction;
      const count=document.createElement('strong');
      count.textContent=Number(row.candidates||0).toLocaleString()+(row.unidentified_amount?' + ?':'');
      button.append(name,count);
      button.title=`${dollars(row.amount)} across ${Number(row.candidates||0).toLocaleString()} identified candidates`+
        (row.unidentified_amount?`; ${dollars(row.unidentified_amount)} has no candidate identity`:'')+sideBasis(row,overlap,both);
      button.onclick=()=>{
        if(row.direction===drawer.direction)return;
        if(drawer.allDirections?.[row.direction]) {
          drawer.direction=row.direction;drawer.rows=drawer.allDirections[row.direction];applyDrawer();writeDrawerScope();
        } else openDrawer(drawer.id,row.direction);
      };
      return button;
    }));
    host.hidden=!sides.length;
    const line=get('drawer-overlap');
    if(line){line.textContent=overlap&&sides.length?overlap.text:'';line.hidden=!line.textContent;}
  }
  function drawDrawerRows(animate) {
    const host=get('drawer-rows'),panel=get('drawer-panel');
    if(!host||!panel||panel.hidden||!drawer)return;
    drawnWidth.set('drawer',Math.round(host.clientWidth));
    if(drawer.loading){host.replaceChildren();return;}
    const rows=drawer.rows||[];
    draw('drawer',rows,()=>{},{container:host,foot:null,values:false,click:false,
      total:rows.reduce((sum,row)=>sum+row.amount,0),party:true,spark:false,leftRead:true,
      twoLine:false,selected:'',animate:Boolean(animate),topN:10,pitch:DRAWER_PITCH,
      title:'Candidates in saved PAC activity',denom:'this direction of saved network activity',
      empty:drawer.error||'No candidates are recorded on this side of this committee\u2019s activity.'});
    const ties=(drawer.relationships||[]).filter(row=>row.direction===drawer.direction);
    if(globalThis.MentatGraphExplorer) {
      globalThis.MentatGraphExplorer.mount(host,{root:drawer.id,rootLabel:drawer.label,direction:drawer.direction,
        rows:drawer.relationships||[],edges:drawer.edges||[],sources:drawer.sources||[],
        onEvidence:(id,cycle)=>{
          globalThis.MentatEvidence?.openCommittee?.(id,cycle);
          globalThis.MentatReturn?.arrive('graph','evidence');
          if(typeof showView==='function')showView('evidence');else location.hash='#evidence';
        }});
    }
    // Paul, 2026-09-23 (board 5): under the rows the drawer shows only
    // "Expand explorer" until it is pressed. The explorer and these
    // disclosures (.graph-drawer-extra) wait behind it: graph.css hides them
    // while the panel is not .graph-drawer-wide, the state the toggle sets.
    // The toggle is drawn by the explorer, so without the explorer script
    // nothing waits: a disclosure behind a button that was never drawn could
    // not be opened at all.
    const behind=globalThis.MentatGraphExplorer?'graph-drawer-extra':'';
    if(ties.length&&!globalThis.MentatGraphExplorer){
      const details=document.createElement('details');details.open=true;
      const summary=document.createElement('summary');summary.textContent=`PAC–candidate ties · all ${ties.length} relationships`;
      const scroll=document.createElement('div');scroll.className='table-scroll';
      const table=document.createElement('table');
      const head=table.createTHead().insertRow();
      ['Candidate','Net amount','Net share','Spending PAC / observed route','Cycle'].forEach(text=>{
        const th=document.createElement('th');th.textContent=text;head.append(th);
      });
      const body=table.createTBody();
      for(const tie of ties){
        const tr=body.insertRow();
        const values=[tie.candidate,dollars(tie.amount),
          tie.share_of_pac_direction==null?'Unavailable':`${(tie.share_of_pac_direction*100).toFixed(1)}%`,
          `${tie.committee_name} · ${tie.route.join(' → ')}`,tie.cycle];
        values.forEach(text=>{const td=tr.insertCell();td.textContent=text;});
      }
      const note=document.createElement('p');note.className='muted';
      note.textContent='Net share is of this PAC’s saved activity in the selected direction, not donor-attributed dollars or candidate control. Routes show committee connections, not a traced path for these dollars. Percentages are unavailable when net reversals make a share misleading.';
      scroll.append(table);details.append(summary,note,scroll);host.append(details);
    }
    if(drawer.note){
      const details=document.createElement('details');details.className=behind;
      const summary=document.createElement('summary');summary.textContent='Measurement details';
      const note=document.createElement('p');note.textContent=drawer.note;
      details.append(summary,note);host.append(details);
    }
    if((drawer.comparisons||[]).length){
      const details=document.createElement('details');details.open=true;details.className=behind;
      const summary=document.createElement('summary');summary.textContent='Different saved measurements · not reconciled';
      details.append(summary);
      for(const row of drawer.comparisons){
        const p=document.createElement('p');
        p.textContent=`${row.committee} · ${row.cycle} · ${row.schedule}: workbook ${row.workbook_amount == null ? 'not present in saved activity' : dollars(row.workbook_amount)}; selected snapshot ${dollars(row.snapshot_amount)}. ${row.basis}`;
        details.append(p);
      }
      host.append(details);
    }
    if((drawer.sources||[]).length){
      const details=document.createElement('details');details.className=behind;
      const summary=document.createElement('summary');summary.textContent='Saved snapshot coverage';details.append(summary);
      for(const row of drawer.sources){
        const p=document.createElement('p');p.textContent=`${row.committee} · cycle ${row.cycle} · ${row.coverage_start||'unknown start'} to ${row.coverage_end||'unknown end'} · SHA-256 ${row.sha256||'unavailable'}`;details.append(p);
      }
      host.append(details);
    }
  }
  function applyDrawer() {
    const panel=get('drawer-panel');
    if(!panel)return;
    const open=Boolean(drawer)&&analysis==='recipients';
    panel.hidden=!open;
    const grid=document.querySelector('.graph-panels');
    if(grid)grid.dataset.drawer=open?'open':'';
    // Board 5: with the drawer open, Recipients is two columns, Top
    // organizations and the drawer; the recipient-type card steps aside until
    // the drawer closes (fix round 1, R02-M5). Comparing, Recipients is the
    // table card and that card is hidden anyway.
    if(analysis==='recipients'&&!(compare&&tableKind()))get('category-panel').hidden=open;
    renderTrail();
    if(!open)return;
    // The drawer header shows its scope's pill while comparing (R21).
    const pill=get('drawer-scope'),owner=compare?compareScopes()[drawer.scope||0]:null;
    if(pill){pill.hidden=!owner;if(owner){pill.className='scope-pill scope-pill--'+(owner.index+1);pill.textContent=owner.label;}}
    get('drawer-title').textContent=`Where this PAC\u2019s money went next \u00b7 ${drawerCycle()}`;
    get('drawer-committee').textContent=`${drawer.label} \u00b7 ${drawer.id}`;
    get('drawer-evidence').title=`Open ${drawer.label} \u00b7 ${drawer.id} in PAC evidence`;
    drawDirections();
    const status=get('drawer-status');
    status.textContent=drawer.loading?'Reading this committee\u2019s onward funding\u2026':'';
    status.hidden=!drawer.loading;
    const note=get('drawer-note');
    // The note is a disclosure, not an aside: it says in words that these are
    // the committee's own dollars, not the selected company's. It is board 5's
    // sentence (Paul, 2026-09-23), kept truthful: the rows include spending by
    // the PACs this committee funds (downstream.NOTE) and saved coverage is
    // not "full", so neither is claimed. The server's whole note stays in
    // Measurement details behind the explorer, and in this line's tooltip.
    const scoped=drawer.scope&&compare?(compareScopes()[drawer.scope]||{}).data:view;
    const company=scoped&&scoped.filters&&scoped.filters.company?'the company':'the companies';
    // F4: drawerNote keeps "and the PACs it funds" only while this committee's
    // transfers were followed, and adds the server's not-traced sentence; a
    // rebuild command leads the tooltip.
    const said=drawerNote(drawer.trace,company);
    note.textContent=drawer.loading?'':drawer.error||said.text;
    note.title=drawer.loading?'':drawer.error?said.command:[said.command,drawer.note||''].filter(Boolean).join('\n');
    note.hidden=Boolean(drawer.loading)||(Boolean(drawer.error)&&!drawer.note);
    drawDrawerRows(true);
  }
  // A permalink, or a Back, names a drawer before there is a view to open it
  // against. It is opened here, once the answer that knows the recipients is in.
  function restoreDrawer() {
    const wanted=pendingDrawer;pendingDrawer=null;
    if(!wanted||!wanted.id){if(drawer)closeDrawer(true);else writeDrawerScope();return;}
    if(analysis!=='recipients')selectAnalysis('recipients');
    openDrawer(wanted.id,wanted.direction||'');
  }

  function showCandidate(id,scope) {
    const owner=compare&&Number(scope)>0?compareScopes()[Number(scope)]:null,data=owner?owner.data:view;
    if(!data)return;
    const row=data.candidates.find(c=>c.id===id);if(!row)return;
    get('candidate-detail-title').textContent=`${row.label} · ${id} · ${data.filters.mode==='direct'?'Included receipt groups':'Source committee activity'}`+(compare?` · ${(owner||compareScopes()[0]).label}`:'');
    const rows=data.candidate_details.filter(r=>r.candidate_id===id);
    get('candidate-rows').replaceChildren(...rows.map(row=>{const tr=document.createElement('tr');for(const value of [row.company||'—',row.tier||'—',row.recipient,row.cycle,dollars(row.amount)]){const td=document.createElement('td');td.textContent=value;tr.append(td);}return tr;}));
    get('candidate-detail').hidden=false;get('candidate-detail').open=true;
    get('candidate-detail').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function exportChart(name) {
    if(!view)return;
    const safe=value=>'"'+String(typeof value==='string'&&/^[=+\-@\t\r]/.test(value)?"'"+value:value).replaceAll('"','""')+'"';
    // While comparing, a card's CSV carries every scope: the comparison named
    // at the top, each scope's report fingerprints, and one amount column per
    // pill (fix round 1, R02-5). Company mode's Company comparison is one
    // ranking, the shared industry's, with the compared companies in it.
    if(compare&&!(name==='companies'&&compare.dim==='company')){
      const scopes=compareScopes(),joined=tableRows(name,scopes);
      // Every scope answers the same candidate view, so the activity view's
      // reached amounts come with each scope's direct amount beside them.
      const withDirect=name==='candidates'&&isActivity(view);
      const lines=[['Graph',titleOf(name,view,compareIndustries())],['Comparing',`${DIM_CAPTION[compare.dim]}: ${scopes.map(scope=>scope.label).join(' | ')}`],['Scope',get('scope').textContent],
        ...scopes.flatMap(scope=>((scope.data&&scope.data.reports)||[]).map(r=>['Report SHA-256',`${scope.label} · ${r.name} ${r.sha256}`])),
        ['Name',...scopes.map(scope=>withDirect?`${scope.label} reached (USD)`:`${scope.label} (USD)`),...(withDirect?scopes.map(scope=>`${scope.label} direct (USD)`):[]),'Identifier'],
        ...joined.map(row=>[row.label,...row.amounts.map(value=>value===null?'':value),...(withDirect?row.directs.map(value=>value===null?'':value):[]),row.id])];
      const url=URL.createObjectURL(new Blob(['\uFEFF'+lines.map(r=>r.map(safe).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
      const link=document.createElement('a');link.href=url;link.download='mentat-compare-'+name+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      return;
    }
    const source=compare&&compareAll&&compareAll.data?compareAll.data:view;
    const withDirect=name==='candidates'&&isActivity(source);
    const rows=[['Graph',titleOf(name,source)],['Scope',get('scope').textContent],['Candidate view',get('mode').selectedOptions[0].textContent],['Direction',source.filters.mode==='direct'?'Direct':source.filters.direction],
      ['Note',name==='candidates'?source.candidate_note:source.note],...source.reports.map(r=>['Report SHA-256',r.name+' '+r.sha256]),
      ['Name',...(withDirect?['Reached (USD)','Direct (USD)']:['USD']),'Identifier'],
      ...source[name].map(r=>[r.label,r.amount,...(withDirect?[r.direct==null?'':r.direct]:[]),r.id])];
    const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.map(r=>r.map(safe).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='mentat-'+name+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  get('run').onchange=loadCatalog;
  get('industry').onchange=()=>{if(compare&&compare.dim==='company')dropCompare();PAST=[];closeDrawer(false);pendingDrawer=null;category='';recipient='';options(get('company'),[['','All companies']]);options(get('cycle'),[['','All cycles']]);refresh(true);};
  for(const name of ['company','tier','cycle','funding'])get(name).onchange=()=>{category='';recipient='';if(name==='tier'&&get('tier').value)get('funding').value='personal';syncControls();refresh();};
  for(const name of ['mode','direction'])get(name).onchange=()=>{syncControls();refresh();};
  get('reset').onclick=()=>{dropCompare();remember();closeDrawer(false);pendingDrawer=null;category='';recipient='';get('company').value='';get('tier').value='';get('cycle').value=view?.options.cycles.at(-1)||'';get('funding').value='all';get('mode').value='direct';get('direction').value='Support';selectAnalysis('overview');refresh();};
  // The candidates panel's asides. Board M4 prints the separation sentence as
  // one muted line under the direct-contribution rows; the server's candidate
  // note and the unresolved-link aside ride on the panel's information mark.
  // Any other candidate view prints the server's note on that line instead,
  // because then it is a disclosure rather than an aside.
  let unresolvedNote='',serverNote='',serverNoteTitle='';
  const SEPARATION='Direct contributions only. Downstream PAC activity, and conditional estimates are separate candidate views.';
  function candidateAside() {
    // The card's heading is what its drawing, its image header (figure-pdf.js
    // prints the card's own <h2>) and its exports are called: the activity
    // view says it is not the industry's giving. It follows the answer on
    // screen, not the select, which has moved ahead of it while one is read.
    const title=get('candidate-panel')?.querySelector(':scope>.panel-heading>h2');
    if(title)title.textContent=titleOf('candidates',view,compareIndustries());
    const direct=get('mode').value==='direct',note=get('candidate-note');
    note.textContent=direct?SEPARATION:serverNote;
    note.title=direct?'':serverNoteTitle;
    note.hidden=!note.textContent;
    const text=[direct?serverNote:'',unresolvedNote].filter(Boolean).join(' ');
    const mark=get('unresolved');
    mark.hidden=!text;mark.title=text;mark.setAttribute('aria-label',text);
  }
  function applyAnalysis() {
    const names=['overview','recipients','candidates','companies'];
    names.forEach(name=>{const tab=get('tab-'+name);tab.setAttribute('aria-selected',String(name===analysis));tab.tabIndex=name===analysis?0:-1;});
    get('analysis-panel').setAttribute('aria-labelledby','graph-tab-'+analysis);
    document.querySelector('.graph-panels').dataset.analysis=analysis;
    // Item 8: the Overview is a two by two grid of the four analyses; each tab
    // still opens its own analysis in full, with the organization breakdown
    // only where a reader asked for recipients.
    get('category-panel').hidden=!['overview','recipients'].includes(analysis);
    get('organization-panel').hidden=analysis!=='recipients';
    get('candidate-panel').hidden=!['overview','candidates'].includes(analysis);
    // Ruling A(ii): the Candidate view select appears as soon as a report is
    // chosen, wherever the candidate panel is showing, rather than only on the
    // Candidates analysis tab. Downstream PAC activity was reachable before
    // this only by finding a tab that revealed the control.
    get('candidate-controls').hidden=get('candidate-panel').hidden||!reportChosen;
    candidateAside();
    get('company-panel').hidden=!['overview','companies'].includes(analysis);
    get('level-panel').hidden=!['overview','companies'].includes(analysis);
    // 02b: comparing, Recipients, Candidates and Companies are one table
    // card each; company mode keeps Companies as the lit ranking (R20).
    const tableOn=Boolean(compare)&&Boolean(tableKind());
    if(tableOn)for(const id of ['category-panel','organization-panel','candidate-panel','company-panel','level-panel'])get(id).hidden=true;
    if(compare)get('organization-panel').hidden=true;
    const panels=document.querySelector('.graph-panels');if(panels)panels.dataset.table=tableOn?'on':'';
    if(get('compare-table'))get('compare-table').hidden=!tableOn;
    syncLayout();
    applyDrawer();
    renderTrail();
    renderCompareTable();
  }
  function selectAnalysis(name,focus=false) {
    analysis=name;applyAnalysis();
    // A panel that was hidden when it was drawn had no width to draw at, so
    // the panels are redrawn as its tab opens. This costs no request: Top N is
    // a slice of the payload already in hand, and the sparkline cache is keyed
    // by request string.
    if(view)paintAll(false);
    get('export-target').value={overview:'categories',recipients:'organizations',candidates:'candidates',companies:'companies'}[name];syncExport();
    if(name==='overview'&&get('mode').value!=='direct'){get('mode').value='direct';refresh();}
    if(focus)get('tab-'+name).focus();
  }
  document.querySelector('.analysis-tabs').addEventListener('click',e=>{const tab=e.target.closest('[role=tab]');if(tab)selectAnalysis(tab.id.replace('graph-tab-',''));});
  document.querySelector('.analysis-tabs').addEventListener('keydown',e=>{
    const names=['overview','recipients','candidates','companies'],index=names.indexOf(analysis);
    const next={ArrowRight:(index+1)%4,ArrowLeft:(index+3)%4,Home:0,End:3}[e.key];
    if(next!==undefined){e.preventDefault();selectAnalysis(names[next],true);}
  });
  /* The full table opens where the reader is standing. It used to switch the
     analysis sub-tab first, so pressing \u201cView all\u201d in the Overview moved the
     reader to another tab with no way back - the label promised a list and
     delivered a navigation. The one case left is a chart named from the view's
     own Export menu whose panel is not on screen: expanding a table nobody can
     see would be a dead control, so that, and only that, reveals its panel. */
  const VALUES_ANALYSIS={categories:'recipients',organizations:'recipients',
    candidates:'candidates',companies:'companies',levels:'companies'};
  function showValues(name) {
    // While comparing, View all opens the drill-down table for that analysis.
    if(compare){selectAnalysis(VALUES_ANALYSIS[name]);return;}
    const panel=get(name)?.closest('.graph-panel');
    if(panel&&panel.hidden)selectAnalysis(VALUES_ANALYSIS[name]);
    // The same for a folded card: a table opened inside a folded card could
    // not be seen, so asking for it unfolds the card, as its triangle would.
    if(panel&&panel.classList.contains('is-folded'))setFolded(panel,false,true);
    get('export-target').value=name;syncExport();
    const details=get(name).querySelector('details');
    if(!details){labelValues(name);return;}
    details.open=!details.open;
    if(details.open){details.scrollIntoView({block:'nearest'});details.querySelector('summary').focus();}
    labelValues(name);
  }
  // Keep the board's short action label; the accessible hint carries the count.
  function labelValues(name,shown,total) {
    const button=document.querySelector('#view-graph [data-values=\"'+name+'\"]');
    if(!button)return;
    const host=get(name),details=host?host.querySelector('details'):null;
    const open=Boolean(details&&details.open);
    const all=total===undefined?(view&&Array.isArray(view[name])?view[name].length:null):total;
    const top=shown===undefined?topN(name):shown;
    button.setAttribute('aria-expanded',String(open));
    button.disabled=all===0;
    button.textContent=open?`Show top ${top.toLocaleString()}`
      :'View all \u2192';
    button.title=open?`Show top ${top.toLocaleString()}`:all===null?'View all rows':`View all ${all.toLocaleString()} rows`;
  }
  document.querySelectorAll('[data-analysis-tab]').forEach(b=>b.onclick=()=>selectAnalysis(b.dataset.analysisTab,true));
  document.querySelectorAll('[data-values]').forEach(b=>b.onclick=()=>showValues(b.dataset.values));
  get('view-candidates').onclick=()=>showValues('candidates');
  // The view's Full table lives in the More menu (r4 audit AUDIT-MEN-A-1):
  // the table opens in its card, so the menu closes rather than cover it, as
  // a card's own Export menu closes when it opens a table.
  get('full-table').onclick=()=>{const more=document.getElementById('graph-more');if(more)more.open=false;showValues(get('export-target').value);};
  function syncExport() {
    const name=get('export-target').value;
    get('export').disabled=!view||(name==='candidates'&&!view.candidate_available)||!view[name]?.length;
    get('full-table').disabled=get('export').disabled;
    get('copy-image').disabled=get('export').disabled;
    get('briefing').disabled=get('export').disabled;
  }
  get('back').onclick=()=>{
    if(drawer&&compare)closeDrawer(true);
    else if(drawer){remember();recipient='';closeDrawer(true);pendingDrawer=null;refresh();}
    else back();
  };
  get('drawer-close').onclick=()=>closeDrawer(true);
  // Evidence resolves this committee against its saved sources and preserves
  // the requested cycle, including on the first visit to that tab.
  get('drawer-evidence').onclick=()=>{
    const api=globalThis.MentatEvidence;
    if(api&&typeof api.openCommittee==='function'&&drawer)
      api.openCommittee(drawer.id,(view&&view.filters.cycle)||'');
    globalThis.MentatReturn?.arrive('graph','evidence');
    if(typeof showView==='function')showView('evidence');else location.hash='#evidence';
  };
  let copyTimer=0;
  get('copy-link').onclick=async()=>{
    const api=globalThis.MentatScope,button=get('copy-link');
    if(!api)return;
    const url=new URL(api.href('graph',api.read()),location.href).href;
    // The button is an icon (board M4): the outcome goes to its name and its
    // tooltip, never into its text, which would wipe the glyph.
    const setName=text=>{button.title=text;button.setAttribute('aria-label',text);button.dataset.state=text==='Copied'?'copied':'';};
    const say=text=>{setName(text);clearTimeout(copyTimer);
      copyTimer=setTimeout(()=>{setName('Copy link');},2000);};
    try {
      if(!navigator.clipboard||typeof navigator.clipboard.writeText!=='function')
        throw new Error('This browser has no clipboard here.');
      await navigator.clipboard.writeText(url);
      get('copy-fallback').hidden=true;say('Copied');
    } catch(e) {
      // A plain http:// host is not a secure context and has no clipboard, and a
      // write can be refused. The link is put on screen rather than lost.
      const field=get('copy-url');
      field.value=url;get('copy-fallback').hidden=false;field.focus();field.select();
      say('Copy it from the field');
    }
  };
  get('export-target').onchange=syncExport;
  // One set of panel actions, wherever the reader presses it: a panel's own
  // Export menu and the view's export line both go through the same
  // exportSubject() / exportChart() pair, so the two can never disagree about
  // which chart, which scope or which provenance they are writing out.
  document.querySelectorAll('#view-graph [data-export]').forEach(button=>{
    button.onclick=()=>{
      const name=button.dataset.export,format=button.dataset.format;
      const menu=button.closest('details');
      if(menu)menu.open=false;
      get('export-target').value=name;syncExport();
      if(format==='table'){showValues(name);return;}
      if(format==='copy'){get('copy-image').click();return;}
      if(format==='briefing'){get('briefing').click();return;}
      get('export-format').value=format;get('export').click();
    };
  });
  // A menu left open after the reader has moved on is a menu in the way. Only
  // an outside click closes them: Escape belongs to the dialogs these menus
  // open, and a menu that vanished on Escape would take the button the dialog
  // has to return focus to with it.
  document.addEventListener('click',event=>{
    for(const menu of document.querySelectorAll('#view-graph details.graph-menu[open]'))
      if(!menu.contains(event.target))menu.open=false;
  });
  // One account of the chart being exported, read by the download, the
  // clipboard copy and the briefing, so the three can never disagree.
  // Comparing, an image would carry one scope's half: none is offered, and the
  // CSV (which has every scope) is (R02-5).
  const COMPARE_IMAGE_NOTE='Images export one scope. Clear the comparison first; Data (CSV) carries every scope.';
  function exportSubject() {
    if(!view||compare)return null;
    const name=get('export-target').value;
    if(name==='candidates'&&!view.candidate_available)return null;
    const svg=get(name).querySelector('svg');
    if(!svg)return null;
    const note=name==='candidates'?view.candidate_note:view.note;
    const context=`${get('scope').textContent}. ${name==='candidates'?get('mode').selectedOptions[0].textContent+'. '+(view.filters.mode==='direct'?'':view.filters.direction+'. '):''}${note} Preview: ${get(name).querySelectorAll('svg .graph-bar-button').length} of ${view[name].length} groups. `+view.reports.map(r=>`${r.name} SHA-256 ${r.sha256}`).join('; ');
    const title=titleOf(name),scope=get('scope').textContent;
    return {name,svg,title,context,chip:title===titleFor[name]?scope:`${scope} · ${title}`,reports:view.reports};
  }
  get('export').onclick=async()=>{
    if(!view)return;
    const format=get('export-format').value;
    if(compare&&format==='csv'){exportChart(get('export-target').value);return;}
    const subject=exportSubject();
    if(!subject){if(compare)error(COMPARE_IMAGE_NOTE);return;}
    if(format==='csv'){exportChart(subject.name);return;}
    try {await MentatExport.image(subject.svg,format,subject.title,subject.context,'mentat-'+subject.name);}
    catch(e){error(e.message);}
  };
  get('copy-image').onclick=async()=>{
    const subject=exportSubject();
    if(!subject){error('No image to copy. Export the CSV instead.');return;}
    error('');
    try {await MentatExport.copy(subject.svg,subject.title,subject.context);}
    catch(e){error(e.message);}
  };
  get('briefing').onclick=()=>{
    const subject=exportSubject();
    if(!subject){error('No image to add. Export the CSV instead.');return;}
    if(!window.MentatBriefing){error('Briefing needs a console restart.');return;}
    error('');
    const report=(subject.reports||[])[0]||{};
    window.MentatBriefing.capture({svg:subject.svg,title:subject.title,scope:subject.context,
      chip:subject.chip,note:'',basis:'Graph totals',sheetId:subject.name,sheetName:subject.title,
      source:report.name||'',provenance:report.sha256||'',origin:'graph'});
  };
  function renderFreshness() {
    const host=get('freshness');host.replaceChildren();
    const field=(node,label,value)=>{
      const row=document.createElement('div');row.className='source-field';
      const key=document.createElement('span'),val=document.createElement('span');key.textContent=label;val.textContent=value||'Unknown';
      if(!value)val.className='freshness-unknown';row.append(key,val);node.append(row);
    };
    view.reports.forEach(report=>{
      const fresh=report.freshness||{},section=document.createElement('section');section.className='source-receipt';
      const h=document.createElement('h3');h.textContent=report.name;section.append(h);
      field(section,'Report file modified',fresh.report_modified_at);
      const status=document.createElement('p');status.className='chart-note';status.textContent='Overall report freshness unknown. Source dates do not establish complete search coverage.';section.append(status);
      for(const source of fresh.sources||[]) {
        const group=document.createElement('section');group.className='source-receipt';
        const name=document.createElement('h3');name.textContent=[source.source,source.cycle].filter(Boolean).join(' · ');group.append(name);
        field(group,'Retrieved',source.retrieved_at);field(group,'File modified',source.file_modified_at);field(group,'Verified data through',source.verified_data_through);
        const badge=document.createElement('p');badge.className=source.verified_data_through?'freshness-verified':'freshness-unknown';badge.textContent=source.verified_data_through?'Verified date in receipt':'Freshness unknown';group.append(badge);
        const details=document.createElement('details'),summary=document.createElement('summary'),receipt=document.createElement('p');summary.textContent='Source receipt';receipt.className='chart-note';receipt.textContent=[source.receipt,source.local_receipt,source.sha256,source.note,source.verification_evidence].filter(Boolean).join(' · ')||'Receipt unavailable';details.append(summary,receipt);
        field(details,'Last checked',source.checked_at);field(details,'Server last modified',source.server_last_modified);group.append(details);section.append(group);
      }
      if(!fresh.sources?.length)field(section,'Source freshness',null);
      for(const issue of fresh.issues||[]){const p=document.createElement('p');p.className='freshness-unknown';p.textContent=issue;section.append(p);}
      host.append(section);
    });
  }
  for(const name of ['sources','builder']) {
    get('open-'+name).onclick=()=>{get(name).showModal();if(name==='builder')get(name).dispatchEvent(new Event('mentat:open'));};
    get('close-'+name).onclick=()=>get(name).close();
  }
  let reportSignature='';
  window.addEventListener('mentat:run',event=>{
    const run=event.detail;if(!loaded||get('run').value!=='published-reports')return;
    const signature=JSON.stringify(run.reports.map(r=>[r.index,r.quality?.sha256]));
    if(reportSignature&&signature!==reportSignature)loadCatalog();
    reportSignature=signature;
  });
  /* ======================================================== compare (02b)
     Boards G1-G5. One dimension - industry, cycle or company - is compared at
     a time, two or three values, in pill order. Scope 1 is the page's own
     scope: it still loads through refresh(), so mentat:graph, the URL and the
     other views keep following it, and its select holds values[0]. Scopes 2
     and 3 are independent fetches with their own AbortControllers, stored in
     compareViews[i] and never in `view`, and never announced. Every card,
     the headline figures and the drill-down tables are drawn from those
     answers; nothing here is a second store of scope: the URL carries the
     comparison as repeated flat keys (scope.js v6, D-039). */
  const DIM_SELECT={industry:'industry',cycle:'cycle',company:'company'};
  const DIM_CAPTION={industry:'Industry',cycle:'Cycle',company:'Company'};
  const COMPARE_PITCH=27,HALF_SPARK_MIN=380,COMPARE_TOP=6,DEFAULT_TOP=10;
  /* What a half may give up for its names (final fix round; compactYield).
     Recipient types keep every column and shrink the bar first ("Party
     Committee" whole, TA1-03). Candidates comparing industries end without a
     bar, as G1 and G4 draw them; comparing companies they give up the share
     and then the bar's room, as G3 draws them with a bar and no share
     (TA1-01); comparing cycles they keep R02-m7's rule above. Company halves
     give up the bar they keep at 380px and wider and, under 340px, the share
     (TA1-02); a name still too long then takes its short form in draw(). */
  function halfYieldOrder(name,width) {
    if(name==='categories')return ['room'];
    if(name==='candidates')return compare.dim==='industry'?['bar']:compare.dim==='company'?['share','room']:[];
    if(name==='companies')return width<COMPACT_NAME_SHARE_W?['bar','share']:['bar'];
    return [];
  }
  const halvesShown=new Set(),compareTimings=[];
  let compareTicket=0,compareEntering=-1,compareMenu=null,pillGroup=null,compareStartedAt=0;
  // A cycle pill reads 2025–2026, and " · to date" while the cycle is not
  // over by the viewer's own calendar (contribution-studio.js calendarNotice).
  function cycleText(end) { const year=Number(end);return Number.isFinite(year)&&year>0?`${year-1}–${year}`:String(end||''); }
  function cycleLabel(end) { return cycleText(end)+(Number(end)>=new Date().getFullYear()?' · to date':''); }
  function valueLabel(dim,value) { return dim==='cycle'?cycleLabel(value):String(value); }
  function compareScopes() {
    if(!compare)return [];
    return compare.values.map((value,index)=>{
      const label=valueLabel(compare.dim,value);
      if(!index)return {index,value,label,status:view?'ready':'loading',data:view,error:''};
      const entry=compareViews[index]||{status:'loading',data:null,error:''};
      return {index,value,label,status:entry.status,data:entry.data,error:entry.error};
    });
  }
  function setTopN(value) {
    for(const select of document.querySelectorAll('#view-graph .graph-panels [data-top]'))
      if([...select.options].some(option=>option.value===String(value)))select.value=String(value);
  }
  function setSelect(dim,value) {
    const select=get(DIM_SELECT[dim]);
    if(!select)return;
    if(![...select.options].some(option=>option.value===String(value)))select.append(new Option(dim==='cycle'?cycleText(value):String(value),String(value)));
    select.value=String(value);
  }
  // Scope 1's request as the server resolved it, the base every other scope
  // starts from; a query that has not answered yet stands in for it.
  function compareBase(query) {
    const base=new URLSearchParams(query||filters());
    if(!query&&view&&view.filters)
      for(const key of ['company','tier','cycle','funding','mode','direction','category','recipient'])
        if(view.filters[key]!==undefined)base.set(key,view.filters[key]==null?'':String(view.filters[key]));
    base.delete('latest');
    return base;
  }
  function scopeParams(index,base) {
    const params=new URLSearchParams(base);
    const value=compare.values[index];
    if(compare.dim==='industry'){params.set('industry',value);params.set('company','');}
    else if(compare.dim==='cycle')params.set('cycle',value);
    else params.set('company',value);
    return params;
  }
  // A failed scope says so in words. The browser's own "Failed to fetch" (a
  // dropped connection) is not a message a reader can act on; an answer the
  // server wrote keeps its own text (R02-m1).
  function scopeError(error) {
    const text=error&&error.message?String(error.message):'';
    if(!text||error.name==='TypeError'||error.name==='SyntaxError'||/failed to fetch|networkerror|load failed/i.test(text))return 'This scope could not be read.';
    return text;
  }
  async function readGraph(params,signal) {
    const answer=await graphRead('/api/graph?'+params,signal),body=answer.body;
    if(!answer.ok)throw new Error(body.error||'Graph unavailable for this scope.');
    return body;
  }
  // Scopes 2 and 3, in parallel. An answer already in hand for the same
  // request is kept, so adding a third scope does not reload the second.
  function loadCompareScopes(query) {
    if(!compare){for(const entry of compareViews)entry&&entry.controller&&entry.controller.abort();compareViews=[];compareAll=null;return;}
    const ticket=++compareTicket,base=compareBase(query);
    const previous=new Map(compareViews.filter(Boolean).map(entry=>[entry.key,entry]));
    const next=[];
    compare.values.forEach((value,index)=>{
      if(!index)return;
      const params=scopeParams(index,base),key=params.toString();
      const kept=previous.get(key);
      if(kept&&kept.status!=='error'){previous.delete(key);next[index]=kept;return;}
      const entry={key,value,status:'loading',data:null,error:'',controller:new AbortController(),started:performance.now()};
      next[index]=entry;
      readGraph(params,entry.controller.signal).then(body=>{
        if(ticket!==compareTicket&&!compareViews.includes(entry))return;
        entry.status='ready';entry.data=body;compareArrived(index);
      },error=>{
        if(error.name==='AbortError'||(ticket!==compareTicket&&!compareViews.includes(entry)))return;
        entry.status='error';entry.error=scopeError(error);compareArrived(index);
      });
    });
    for(const entry of previous.values())entry.controller&&entry.controller.abort();
    compareViews=next;
    // Company mode keeps Company comparison whole: the ranking of every
    // company in the shared industry and cycle, with the compared ones lit.
    if(compare.dim==='company'){
      const params=new URLSearchParams(base);params.set('company','');
      const key=params.toString();
      if(!compareAll||compareAll.key!==key||compareAll.status==='error'){
        compareAll&&compareAll.controller&&compareAll.controller.abort();
        const entry={key,status:'loading',data:null,error:'',controller:new AbortController()};
        compareAll=entry;
        readGraph(params,entry.controller.signal).then(body=>{if(compareAll!==entry)return;entry.status='ready';entry.data=body;compareArrived(-1);},
          error=>{if(error.name==='AbortError'||compareAll!==entry)return;entry.status='error';entry.error=scopeError(error);compareArrived(-1);});
      }
    } else {compareAll&&compareAll.controller&&compareAll.controller.abort();compareAll=null;}
    markCompareState();
  }
  function markCompareState() {
    const host=document.getElementById('view-graph');
    if(!host)return;
    const scopes=compareScopes();
    paintScope();
    host.dataset.compare=compare?compare.dim:'';
    host.dataset.compareState=!compare?'':scopes.some(scope=>scope.status==='loading')||(compare.dim==='company'&&(!compareAll||compareAll.status==='loading'))?'loading':'ready';
    // How long a comparison took from the pick to every half painted.
    if(compare&&compareStartedAt&&host.dataset.compareState==='ready'){
      compareTimings.push({dim:compare.dim,scopes:compare.values.length,ms:Math.round(performance.now()-compareStartedAt)});
      compareStartedAt=0;
    }
  }
  function compareOverline() {
    const cycle=view&&view.filters.cycle?cycleText(view.filters.cycle):'All cycles';
    const count=compare.values.length;
    if(compare.dim==='industry')return (count>2?`Comparing ${count} industries`:'Comparing industries')+` · ${cycle}`;
    if(compare.dim==='cycle')return `${get('industry').value} · comparing cycles`;
    return `${get('industry').value} · comparing companies · ${cycle}`;
  }
  // Leave a comparison without redrawing: for a caller that is about to
  // refresh anyway (Reset, a new industry, a new source folder).
  function dropCompare() {
    for(const entry of compareViews)entry&&entry.controller&&entry.controller.abort();
    compareAll&&compareAll.controller&&compareAll.controller.abort();
    const had=Boolean(compare);
    compare=null;compareViews=[];compareAll=null;pendingCompare=null;compareLayout='';halvesShown.clear();
    if(had)setTopN(DEFAULT_TOP);
    if(tableMoved)restoreCandidateControls();
    for(const host of document.querySelectorAll('#view-graph .graph-halves'))host.classList.remove('graph-halves');
    renderCompareChip();syncLayout();syncControls();
  }
  function compareArrived(index) {
    if(!view||!compare){markCompareState();return;}
    compareEntering=index;
    paintScope();
    try{renderCompareFigures();paintAll(false);renderCompareTable();}
    finally{compareEntering=-1;}
    markCompareState();
  }
  /* ------------------------------------------ the compared chip and its menu */
  function renderCompareChip() {
    const grid=document.querySelector('#view-graph .graph-filter-grid');
    if(!grid)return;
    // The chips' compare widths (graph.css keys them on this attribute) must
    // hold before fitChips() measures them, so it is set here, not only in
    // markCompareState() after the paint.
    const host=document.getElementById('view-graph');if(host)host.dataset.compare=compare?compare.dim:'';
    for(const dim of Object.keys(DIM_SELECT)){
      const label=grid.querySelector(`.chip-select[for=graph-${DIM_SELECT[dim]}]`);
      if(label)label.hidden=Boolean(compare&&compare.dim===dim);
    }
    const add=get('compare-add'),clearLink=get('compare-clear');
    let chip=grid.querySelector('.chip-compare');
    if(!compare){
      if(chip)chip.remove();pillGroup=null;
      if(add)add.hidden=false;if(clearLink)clearLink.hidden=true;
      return;
    }
    const values=compare.values.map(value=>({value,label:valueLabel(compare.dim,value)}));
    if(!chip||chip.dataset.dim!==compare.dim){
      if(chip)chip.remove();
      chip=document.createElement('div');chip.className='chip-select chip-compare';chip.dataset.dim=compare.dim;
      const caption=document.createElement('span');caption.textContent=DIM_CAPTION[compare.dim];chip.append(caption);
      grid.querySelector(`.chip-select[for=graph-${DIM_SELECT[compare.dim]}]`).after(chip);
      pillGroup=MentatCompareMenu.pills(chip,{kind:compare.dim,values,max:COMPARE_MENU.MAX,label:'Compared '+DIM_CAPTION[compare.dim].toLowerCase()+' values',
        onRemove:index=>removeScope(index),onAdd:anchor=>openCompare(anchor,[compare.dim])});
    } else pillGroup.update(values);
    // M4's chevron stays on the chip while another value fits, and a click on
    // the chip itself (not a pill, not a button) opens the same menu the "+"
    // does, for this dimension (boards G1 and G5; G4, full, draws none).
    const room=compare.values.length<COMPARE_MENU.MAX;
    chip.classList.toggle('can-add',room);
    chip.onclick=event=>{
      if(!compare||event.target.closest('button')||compare.values.length>=COMPARE_MENU.MAX)return;
      openCompare(chip.querySelector('.compare-pills-add')||chip,[compare.dim]);
    };
    if(add)add.hidden=true;if(clearLink)clearLink.hidden=false;
  }
  function compareValuesFor(kind) {
    const select=get(DIM_SELECT[kind]);
    const values=[...(select?select.options:[])].filter(option=>option.value)
      .map(option=>({value:option.value,label:kind==='cycle'?cycleLabel(option.value):option.textContent}));
    return Promise.resolve(values);
  }
  function openCompare(anchor,kinds) {
    if(compareMenu){const open=compareMenu;compareMenu=null;open.close();return;}
    if(!view)return;
    const exclude={industry:[get('industry').value],cycle:[],company:[]};
    if(view.filters.cycle)exclude.cycle=[String(view.filters.cycle)];
    if(view.filters.company)exclude.company=[String(view.filters.company)];
    // "All companies" and "All cycles" are not values a pill can hold, so such
    // a comparison starts from the top company or the newest cycle: the list
    // leaves that value out and its heading names it (R02-m3).
    const captions={};
    for(const kind of ['company','cycle']){
      if((kind==='company'?view.filters.company:view.filters.cycle)||(compare&&compare.dim===kind))continue;
      const start=firstValue(kind,'');
      if(!start)continue;
      exclude[kind]=[start];
      captions[kind]=kind==='company'?`Compare ${start} (top company) with…`:`Compare ${cycleLabel(start)} (newest cycle) with…`;
    }
    if(compare)exclude[compare.dim]=compare.values.slice();
    compareMenu=MentatCompareMenu.open(anchor,{kinds:kinds||COMPARE_MENU.KINDS,valuesFor:compareValuesFor,exclude,captions,
      onPick:(kind,value)=>{compareMenu=null;pickCompare(kind,String(value));},
      onClose:()=>{compareMenu=null;}});
  }
  // v1 is the chip's own value when the comparison starts. "All cycles" and
  // "All companies" are not values a pill can hold, so those start from the
  // newest cycle, or from the largest company in the scope.
  function firstValue(kind,picked) {
    if(kind==='industry')return get('industry').value;
    if(kind==='cycle'){
      const own=String(view.filters.cycle||get('cycle').value||'');
      if(own)return own;
      return [...(view.options.cycles||[])].map(String).filter(cycle=>cycle!==picked).sort().at(-1)||'';
    }
    const own=String(view.filters.company||get('company').value||'');
    if(own)return own;
    return ((view.companies||[]).map(row=>String(row.id)).find(company=>company!==picked))||'';
  }
  function pickCompare(kind,value) {
    if(!view||!value)return;
    compareStartedAt=performance.now();
    if(compare&&compare.dim===kind){
      if(compare.values.length>=COMPARE_MENU.MAX||compare.values.includes(value))return;
      compare.values.push(value);applyCompare(compare.values.length-1);return;
    }
    const first=firstValue(kind,value);
    if(!first||first===value)return;
    if(compare&&compare.dim!==kind)setSelect(compare.dim,compare.values[0]);
    compare={dim:kind,values:[first,value]};compareLayout='';halvesShown.clear();
    for(const entry of compareViews)entry&&entry.controller&&entry.controller.abort();
    compareViews=[];
    setTopN(COMPARE_TOP);
    if(kind==='industry'&&get('company').value)get('company').value='';
    if(kind!=='industry')setSelect(kind,first);
    applyCompare(1);
    setTimeout(()=>{const target=document.querySelector('#view-graph .chip-compare .compare-pills-add')||get('compare-clear');if(target&&!target.hidden)target.focus();},0);
  }
  // After any change to the comparison: scope 1 reloads through refresh() if
  // its own request changed; otherwise only the other scopes are read.
  function applyCompare(entering) {
    syncControls();renderCompareChip();syncLayout();
    const query=filters();
    if(!view||query.toString()!==viewKey){refresh();return;}
    writeScope(query);
    loadCompareScopes(null);
    compareEntering=entering;
    try{applyAnalysis();renderCompareFigures();paintAll(false);renderCompareTable();}
    finally{compareEntering=-1;}
    renderTrail();markCompareState();fitChips();
  }
  function removeScope(index) {
    if(!compare)return;
    const leaving=[...document.querySelectorAll(`#view-graph .graph-half[data-scope="${index+1}"]`)];
    const run=()=>{
      if(!compare)return;
      const values=compare.values.slice();values.splice(index,1);
      if(values.length<2){endCompare(values[0]);return;}
      compare.values=values;
      if(!index)setSelect(compare.dim,values[0]);
      applyCompare(-1);
    };
    if(motionMs()<=1||!leaving.length){run();return;}
    for(const half of leaving)half.classList.add('is-leaving');
    setTimeout(run,180);
  }
  function endCompare(keep) {
    if(!compare)return;
    const dim=compare.dim;
    for(const entry of compareViews)entry&&entry.controller&&entry.controller.abort();
    compareAll&&compareAll.controller&&compareAll.controller.abort();
    compare=null;compareViews=[];compareAll=null;pendingCompare=null;compareLayout='';halvesShown.clear();
    if(keep!==undefined&&keep!=='')setSelect(dim,keep);
    setTopN(DEFAULT_TOP);
    if(tableMoved)restoreCandidateControls();
    for(const host of document.querySelectorAll('#view-graph .graph-halves'))host.classList.remove('graph-halves');
    applyCompare(-1);
    const add=get('compare-add');if(add&&!add.hidden)setTimeout(()=>add.focus(),0);
  }
  // A permalink's comparison, once the options it names exist.
  function resolvePendingCompare() {
    const wanted=pendingCompare;pendingCompare=null;
    if(!wanted||!view)return;
    const known=wanted.dim==='cycle'?(view.options.cycles||[]).map(String):wanted.dim==='company'?(view.options.companies||[]).map(String):[...get('industry').options].map(option=>option.value);
    const values=wanted.values.map(String).filter((value,at,all)=>known.includes(value)&&all.indexOf(value)===at).slice(0,COMPARE_MENU.MAX);
    if(values.length<2)return;
    compare={dim:wanted.dim,values};
    setTopN(COMPARE_TOP);
  }
  /* ------------------------------------------------------------- layout */
  function compareStacked() { return Boolean(compare)&&(compare.values.length>=3||compareLayout==='stacked'); }
  function syncLayout() {
    const box=get('layout'),grid=document.querySelector('#view-graph .graph-panels');
    const count=compare?compare.values.length:0,stacked=compareStacked();
    if(box){
      box.hidden=!compare||Boolean(tableKind());
      const gridButton=get('layout-grid'),stackButton=get('layout-stacked');
      gridButton.setAttribute('aria-pressed',String(!stacked));stackButton.setAttribute('aria-pressed',String(stacked));
      gridButton.disabled=count>=3;
      gridButton.title=count>=3?'Three scopes need the stacked layout':'Two cards per row';
      stackButton.title='One card per row';
    }
    if(grid){grid.dataset.compare=count?String(count):'';grid.dataset.layout=count?(stacked?'stacked':'grid'):'';}
    for(const button of document.querySelectorAll('#view-graph .graph-panel [data-export]:not([data-format=csv])')){
      button.disabled=Boolean(compare);
      if(compare)button.title=COMPARE_IMAGE_NOTE;else button.removeAttribute('title');
    }
  }
  /* --------------------------------------------------- headline figures */
  const FIGURES=[['giving','Net giving'],['direct','Direct to candidates'],['organizations','Recipient organizations']];
  // Cycle mode compares the latest cycle against the one before it, by the
  // cycle's own value rather than by pill order.
  function cycleOrder() {
    if(!compare||compare.dim!=='cycle')return null;
    const ranked=compare.values.map((value,index)=>({value:Number(value),index})).sort((a,b)=>a.value-b.value);
    return {latest:ranked.at(-1).index,earlier:ranked.length>1?ranked.at(-2).index:-1};
  }
  function changeChip(latest,earlier,sayWhat) {
    const change=changeText(latest,earlier),chip=document.createElement('span');
    chip.className='change-chip';chip.textContent=change.text;
    if(change.direction)chip.dataset.direction=change.direction;
    chip.title=sayWhat||'Change against the earlier cycle';
    return chip;
  }
  function renderCompareFigures() {
    const tiles=[...document.querySelectorAll('#view-graph .graph-metrics > div')];
    const scopes=compareScopes(),order=cycleOrder();
    // Company lines lead with the company, shortened by whole words to fit
    // 72px ("Goldman", "JP Morgan", as G3 prints them) rather than cut with an
    // ellipsis; the column is as wide as the longest of them (R02-m9).
    const headNames=compare&&compare.dim==='company'?scopes.map(scope=>fitForm(nameForms('company',scope.label),FIGURE_NAME_MAX,12)):[];
    const headWidth=headNames.length?Math.ceil(Math.max(...headNames.map(text=>MentatTheme.measureText(text,12,MentatTheme.sans))))+2:0;
    tiles.forEach((tile,at)=>{
      let lines=tile.querySelector('.graph-figure-lines');
      const figure=tile.querySelector('.graph-figure');
      if(!compare){if(lines)lines.remove();if(figure)figure.hidden=false;tile.classList.remove('is-compare');return;}
      if(figure)figure.hidden=true;
      tile.classList.add('is-compare');
      if(!lines){lines=document.createElement('ul');lines.className='graph-figure-lines';tile.append(lines);}
      lines.replaceChildren();
      const [key]=FIGURES[at]||[];
      scopes.forEach(scope=>{
        const line=document.createElement('li');line.className='graph-figure-line';line.dataset.scope=String(scope.index+1);
        const dot=document.createElement('i');dot.className='graph-figure-dot';dot.setAttribute('aria-hidden','true');line.append(dot);
        if(compare.dim==='company'){
          const name=document.createElement('span');name.className='graph-figure-name';name.title=scope.label;
          name.textContent=headNames[scope.index];name.style.width=headWidth+'px';line.append(name);
        }
        const strong=document.createElement('strong');
        const totals=scope.status==='ready'&&scope.data?scope.data.totals:null;
        if(!totals){strong.textContent=scope.status==='error'?'—':'…';strong.title=scope.status==='error'?scope.error:'Reading this scope';line.append(strong);}
        else{
          const value=Number(totals[key])||0;
          // The share rides in the value's title too: a tight tab row drops
          // the printed share first (fitStrip, R02-M2).
          const usable=key!=='giving'&&totals.giving>0&&value>=0&&value<=totals.giving;
          const percent=usable?(100*value/totals.giving).toFixed(1)+'%':'';
          strong.textContent=wholeDollars(value);strong.title=`${scope.label} · ${dollars(value)}`+(usable?` · ${percent} of net giving`:'');line.append(strong);
          if(key!=='giving'){
            const share=document.createElement('small');share.className='graph-figure-share';share.textContent=usable?` · ${percent}`:'';
            share.title=usable?'of net giving':'';line.append(share);
          }
          if(order&&scope.index===order.latest&&order.earlier>=0){
            const earlier=scopes[order.earlier];
            if(earlier&&earlier.status==='ready'&&earlier.data){
              const before=Number(earlier.data.totals[key])||0;
              line.append(changeChip(value,before,`${scope.label} ${dollars(value)} against ${earlier.label} ${dollars(before)}`));
            }
          }
        }
        const spoken=document.createElement('span');spoken.className='sr-only';spoken.textContent=` ${scope.label}`;line.prepend(spoken);
        lines.append(line);
      });
    });
    const metrics=document.querySelector('#view-graph .graph-metrics');
    if(metrics)metrics.classList.toggle('is-compare',Boolean(compare));
    fitStrip();
  }
  /* M4 keeps the figures on the tab row, and so does every comparison
     (design call 1: the headline never moves). Where one line per scope does
     not fit beside the tabs, the figures get shorter in measured steps (fix
     round 1, R02-M2): is-tight drops the printed share from each line (the
     value's title keeps it), is-tighter also folds the Layout caption to the
     screen reader and closes the tab gaps. Only a screen too narrow for even
     that (under ~1300px) gives the figures a row of their own. */
  function fitStrip() {
    const strip=document.querySelector('#view-graph .analysis-strip');
    if(!strip)return;
    strip.classList.remove('is-wrapped','is-tight','is-tighter');
    if(!compare||!strip.clientWidth)return;
    const tabs=strip.querySelectorAll('.analysis-tabs button'),metrics=strip.querySelector('.graph-metrics');
    const last=tabs[tabs.length-1];
    if(!last||!metrics)return;
    const tabsRow=strip.querySelector('.analysis-tabs'),layoutBox=get('layout');
    const fits=()=>{
      if(tabsRow.scrollWidth>tabsRow.clientWidth+1||strip.scrollWidth>strip.clientWidth+1)return false;
      const figures=metrics.getBoundingClientRect();
      // Under 1200px graph.css already gives the figures a row of their own;
      // there is room for the whole lines, so nothing is shortened.
      if(figures.top>=tabsRow.getBoundingClientRect().bottom-1)return true;
      if(last.getBoundingClientRect().right+16>figures.left)return false;
      return !(layoutBox&&!layoutBox.hidden&&figures.right>layoutBox.getBoundingClientRect().left+1);
    };
    for(const step of ['is-tight','is-tighter','is-wrapped']){if(fits())return;strip.classList.add(step);}
  }
  /* ------------------------------------------------------ the card halves */
  function skeletonRows(count) {
    const box=document.createElement('div');box.className='graph-half-skeleton';box.setAttribute('aria-hidden','true');
    for(let i=0;i<count;i++)box.append(document.createElement('b'));
    return box;
  }
  function halfError(message) {
    const p=document.createElement('p');p.className='graph-empty graph-half-error';p.setAttribute('role','alert');
    p.textContent=message||'This scope could not be read.';return p;
  }
  function rowId(name,row) { return name==='candidates'?String(row.id||''):String(row.id); }
  function joinable(name,row) { return !(name==='candidates'&&row.id==='unidentified-candidate'); }
  // The per-row marks a ranked half carries: the shared tint (a name in two
  // or more halves), and in cycle mode the change chip or the "new" tag.
  function rankedMarks(name,scopes,top) {
    const shownIds=scopes.map(scope=>new Set(scope.status==='ready'&&scope.data?(scope.data[name]||[]).slice(0,top).filter(row=>joinable(name,row)).map(row=>rowId(name,row)):[]));
    const counts=new Map();
    for(const ids of shownIds)for(const id of ids)counts.set(id,(counts.get(id)||0)+1);
    const shared=new Set([...counts].filter(([,count])=>count>=2).map(([id])=>id));
    return {shared,order:cycleOrder()};
  }
  function marksFor(name,scopes,index,top,info) {
    const marks=new Map();
    const order=info&&info.order;
    if(!order||index!==order.latest||order.earlier<0)return marks;
    const earlier=scopes[order.earlier];
    if(!earlier||earlier.status!=='ready'||!earlier.data)return marks;
    const before=new Map((earlier.data[name]||[]).filter(row=>joinable(name,row)).map(row=>[rowId(name,row),Number(row.amount)||0]));
    const rows=(scopes[index].data[name]||[]).slice(0,top);
    for(const row of rows){
      if(!joinable(name,row))continue;
      const id=rowId(name,row);
      // The hover names both amounts, so every chip can be re-derived.
      if(before.has(id))marks.set(id,{change:{...changeText(row.amount,before.get(id)),against:`${earlier.label} ${dollars(before.get(id))}`}});
      else if(name==='candidates'||name==='companies')marks.set(id,{newTag:true});
      else marks.set(id,{change:{text:'new',direction:''}});
    }
    return marks;
  }
  const HALF_CLICK={
    categories:()=>id=>{remember();category=id;recipient='';closeDrawer(false);pendingDrawer=null;refresh();},
    candidates:index=>id=>showCandidate(id,index),
    companies:()=>id=>{if(compare&&compare.dim==='cycle'){remember();get('company').value=id;category='';recipient='';refresh();}},
    levels:()=>id=>{remember();get('tier').value=id;get('funding').value='personal';syncControls();refresh();}
  };
  function paintCompareCard(name,animate) {
    const host=get(name);
    if(!host)return;
    drawnWidth.set(name,Math.round(host.clientWidth));
    const foot=document.getElementById('graph-foot-'+name);
    if(name==='organizations'){host.replaceChildren();host.classList.remove('graph-halves');return;}
    if(name==='companies'&&compare.dim==='company'){paintHighlighted(host,foot,animate);return;}
    const scopes=compareScopes(),top=topN(name);
    host.replaceChildren();host.classList.add('graph-halves');host.style.setProperty('--scopes',String(scopes.length));
    const first=scopes[0].data;
    let aligned=null;
    if(name==='categories'&&first)aligned=(first.categories||[]).slice(0,top).map(row=>({id:row.id,label:row.label}));
    if(name==='levels'&&first)aligned=(first.levels||[]).map(row=>({id:row.id,label:row.label}));
    const info=['candidates','companies'].includes(name)?rankedMarks(name,scopes,top):{shared:new Set(),order:cycleOrder()};
    // Cycle mode aligns "By company" on scope 1's first four companies.
    const alignedCompanies=compare.dim==='cycle'&&first?(first.companies||[]).slice(0,4).map(row=>row.id):null;
    const drawn=[];
    // Every half is in the card before any is drawn. A half measured while it
    // was still the card's only child had no neighbour's padding beside it,
    // drew 16px too wide and was then scaled down, so its rows drifted off the
    // other halves' (measured 8.5px at 1440 before this).
    const bodies=scopes.map((scope,index)=>{
      const half=document.createElement('div');half.className='graph-half';half.dataset.scope=String(index+1);
      // A half slides in the first time its scope appears in this card, never
      // again on a redraw (Top N, a resize, a later answer).
      const seen=name+'\u0001'+compare.dim+'\u0001'+scope.value;
      if(!halvesShown.has(seen)){halvesShown.add(seen);if(index>0&&motionMs()>1)half.classList.add('is-entering');}
      const pill=document.createElement('span');pill.className='scope-pill scope-pill--'+(index+1);pill.title=scope.label;
      const pillText=document.createElement('span');pillText.className='scope-pill-text';pillText.textContent=scope.label;pill.append(pillText);
      const body=document.createElement('div');body.className='graph-half-body';
      half.append(pill,body);host.append(half);
      return body;
    });
    scopes.forEach((scope,index)=>{
      const body=bodies[index];
      if(scope.status==='loading'){body.append(skeletonRows(Math.min(top,6)));return;}
      if(scope.status==='error'||!scope.data){body.append(halfError(scope.error));return;}
      const data=scope.data,grow=animate===true||index===compareEntering;
      const click=HALF_CLICK[name]?HALF_CLICK[name](index):()=>{};
      const caption=`${scope.label}. ${get('scope').textContent}`;
      if(name==='levels'){
        const rows=aligned?aligned.map(level=>(data.levels||[]).find(row=>row.id===level.id)||{id:level.id,label:level.label,amount:null}):(data.levels||[]);
        const byCompany=compare.dim==='company'?false:alignedCompanies||(data.companies||[]).slice(0,4).map(row=>row.id);
        drawStack('levels',rows,click,{data,container:body,foot:null,values:false,animate:grow,caption,compact:true,byCompany,topN:4,
          marks:marksFor('levels',scopes,index,rows.length,info)});
        return;
      }
      if(name==='candidates'&&!data.candidate_available){emptyPanel('candidates',data.candidate_note||'This candidate view is unavailable for this scope.',body,null,data.candidate_note_title||'');return;}
      const rows=aligned?aligned.map(entry=>(data[name]||[]).find(row=>row.id===entry.id)||{id:entry.id,label:entry.label,amount:null}):(data[name]||[]);
      // Company halves end in the sparkline, as G1 and G2 draw them; under
      // 380px a bar does not fit beside it, so the bar gives way (R02-m7).
      // Comparing cycles, candidate halves carry the change chip and the
      // "new" tag where the share was; under 380px the name keeps the room
      // and the bar goes, as G2 draws those halves (a bar left there was a
      // 38px swatch beside a four-letter name). A half under 285px drops its
      // share column, as G2's earlier cycle has none; each row's title still
      // carries the share (R02-M1).
      const width=compactWidth(body);
      const spark=name==='companies',narrow=width<HALF_SPARK_MIN;
      const bar=!(narrow&&(spark||(name==='candidates'&&compare.dim==='cycle'))),share=width>=COMPACT_SHARE_MIN_W;
      const options={data,container:body,foot:null,values:false,animate:grow,topN:aligned?aligned.length:top,caption,
        industry:compare.dim==='industry'?scope.value:undefined,
        compact:true,pitch:COMPARE_PITCH,money:wholeDollars,spark,bar,share,tint:info.shared,marks:marksFor(name,scopes,index,aligned?Infinity:top,info),
        click:name==='companies'&&compare.dim!=='cycle'?false:undefined,yieldOrder:halfYieldOrder(name,width)};
      drawn.push({scope,body,rows,click,data,spark,options,plan:draw(name,rows,click,options)});
    });
    // Every half of a card draws the same columns: when one half gave up its
    // bar or its share for a name, the others give it up too (TA1-01, TA1-02).
    // A half redrawn without its bar may still give up its share, so this
    // settles in at most two passes (bar, then share).
    const dropped=part=>drawn.some(half=>half.plan&&half.options[part]!==false&&!half.plan[part]);
    for(let pass=0;pass<2;pass++){
      const dropBar=dropped('bar'),dropShare=dropped('share');
      let redrawn=false;
      for(const half of drawn){
        if(!half.plan||!((dropBar&&half.plan.bar&&half.options.bar!==false)||(dropShare&&half.plan.share&&half.options.share!==false)))continue;
        half.options={...half.options,bar:dropBar?false:half.options.bar,share:dropShare?false:half.options.share};
        half.plan=draw(name,half.rows,half.click,half.options);redrawn=true;
      }
      if(!redrawn)break;
    }
    for(const half of drawn){
      if(half.spark){
        const industry=compare.dim==='industry'?half.scope.value:get('industry').value;
        const svg=half.body.querySelector('svg');
        if(svg)fillSparklines({svg,data:half.data,industry,foot:null});
      }
    }
    if(foot)foot.textContent=compareFoot(name,scopes,aligned,top);
  }
  // While comparing, a card's footer gives each scope's own coverage of its
  // own denominator, one share per pill, not scope 1's alone (R02-m13).
  // Employee level is 100% of the shown levels in every scope, so it keeps
  // the one-scope sentence.
  function compareFoot(name,scopes,aligned,top) {
    const first=scopes[0];
    if(name==='levels'){
      if(first.status!=='ready'||!first.data)return '';
      const rows=first.data.levels||[],total=denominator(name,first.data),amounts=rows.map(row=>Number(row.amount)||0);
      const usable=total>0&&amounts.every(value=>value>=0);
      return footText(name,rows.length,rows.length,usable,usable?100*amounts.reduce((a,b)=>a+b,0)/total:0);
    }
    let shown=0;
    const parts=scopes.map(scope=>{
      const label=compare.dim==='cycle'?cycleText(scope.value):compare.dim==='company'?fitForm(nameForms('company',scope.label),FIGURE_NAME_MAX,12):scope.label;
      if(scope.status!=='ready'||!scope.data)return `${label} —`;
      const rows=scope.data[name]||[];
      const picked=aligned?aligned.map(entry=>rows.find(row=>row.id===entry.id)).filter(Boolean):rows.slice(0,top);
      shown=Math.max(shown,aligned?aligned.length:picked.length);
      const total=denominator(name,scope.data),amounts=picked.map(row=>Number(row.amount)||0);
      const usable=total>0&&amounts.every(value=>value>=0);
      return `${label} ${usable?(100*amounts.reduce((a,b)=>a+b,0)/total).toFixed(1)+'%':'—'}`;
    });
    return `Showing ${shown} per scope · ${parts.join(' · ')} of ${denomLabel(name,first.data||view)}`;
  }
  // Company mode (G3): Company comparison is not split. The whole ranking of
  // the shared industry and cycle, taupe, with the compared companies in
  // their scope colours; one ranked below Top N is appended after a rule.
  function paintHighlighted(host,foot,animate) {
    host.classList.remove('graph-halves');host.style.removeProperty('--scopes');
    const all=compareAll;
    if(!all||all.status==='loading'){host.replaceChildren(skeletonRows(6));if(foot)foot.textContent='';return;}
    if(all.status==='error'||!all.data){host.replaceChildren(halfError(all.error));if(foot)foot.textContent='';return;}
    const ranked=all.data.companies||[],top=topN('companies');
    const lit=new Map(compare.values.map((value,index)=>[String(value),index]));
    const shown=ranked.slice(0,top),extra=ranked.map((row,at)=>({row,at})).filter(entry=>entry.at>=top&&lit.has(String(entry.row.id)));
    const rows=shown.concat(extra.map(entry=>Object.assign({},entry.row,{rank:entry.at+1})));
    const taupe=MentatTheme.palette[MentatTheme.palette.length-1];
    const colours=compare.values.map((value,index)=>getComputedStyle(document.documentElement).getPropertyValue('--scope-'+(index+1)).trim()||taupe);
    draw('companies',rows,()=>{},{data:all.data,container:host,foot:null,values:false,animate:animate===true,topN:rows.length,
      money:wholeDollars,spark:true,click:false,dividerAfter:extra.length?shown.length-1:-1,
      colorFor:row=>lit.has(String(row.id))?colours[lit.get(String(row.id))]:taupe,
      litIds:new Set(lit.keys()),caption:`Company comparison. ${get('scope').textContent}`});
    const svg=host.querySelector('svg');
    if(svg)fillSparklines({svg,data:all.data,industry:get('industry').value,foot:null});
    if(foot)foot.textContent='Compared companies highlighted';
  }
  /* ---------------------------------------- drill-down tables while comparing */
  const TABLE_FOR={recipients:'organizations',candidates:'candidates',companies:'companies'};
  const TABLE_TEXT={
    organizations:{title:'Top organizations',noun:'Recipient',find:'Find a recipient',sub:'Recipient organizations in each scope.'},
    candidates:{title:'Top candidates',noun:'Candidate',find:'Find a candidate',sub:'Direct contributions only.'},
    companies:{title:'Company comparison',noun:'Company',find:'Find a company',sub:'Net giving by company in each scope.'}};
  const TABLE_PAGE=10;
  let table={kind:'',filter:'all',query:'',sort:'s0',dir:'desc',page:0},tableMoved=false;
  function tableKind() {
    if(!compare)return '';
    const kind=TABLE_FOR[analysis]||'';
    return kind==='companies'&&compare.dim==='company'?'':kind;
  }
  function tableRows(kind,scopes) {
    const joined=new Map();
    scopes.forEach((scope,index)=>{
      const data=scope.status==='ready'?scope.data:null;
      const rows=!data?[]:(kind==='candidates'&&!data.candidate_available)?[]:(data[kind]||[]);
      for(const row of rows){
        if(!joinable(kind,row))continue;
        const id=rowId(kind,row);
        let entry=joined.get(id);
        if(!entry){entry={id,label:String(row.label==null?id:row.label),party:row.party||'',amounts:scopes.map(()=>null),directs:scopes.map(()=>null)};joined.set(id,entry);}
        entry.amounts[index]=Number(row.amount)||0;
        // The industry's own direct amount, on the activity view's rows only.
        if(row.direct!=null)entry.directs[index]=Number(row.direct)||0;
        if(!entry.party&&row.party)entry.party=row.party;
      }
    });
    return [...joined.values()];
  }
  function tableFilters(rows,scopes) {
    const every=rows.filter(row=>row.amounts.every(value=>value!==null));
    const list=[{id:'all',label:'All',count:rows.length},{id:'both',label:scopes.length>2?'In every scope':'Funded by both',count:every.length}];
    scopes.forEach((scope,index)=>list.push({id:'only'+index,label:'Only '+scope.label,count:rows.filter(row=>row.amounts[index]!==null&&row.amounts.every((value,at)=>at===index||value===null)).length}));
    return list;
  }
  function tableApply(rows,scopes) {
    const needle=table.query.trim().toLowerCase();
    let out=rows;
    if(table.filter==='both')out=out.filter(row=>row.amounts.every(value=>value!==null));
    else if(/^only\d$/.test(table.filter)){const at=Number(table.filter.slice(4));out=out.filter(row=>row.amounts[at]!==null&&row.amounts.every((value,i)=>i===at||value===null));}
    if(needle)out=out.filter(row=>row.label.toLowerCase().includes(needle));
    const diff=row=>(row.amounts[0]||0)-(row.amounts[1]||0);
    const value=row=>table.sort==='name'?row.label.toLowerCase():table.sort==='party'?String(row.party):table.sort==='diff'?diff(row):row.amounts[Number(table.sort.slice(1))];
    const sign=table.dir==='asc'?1:-1;
    return out.slice().sort((a,b)=>{
      const x=value(a),y=value(b);
      if(x===null&&y!==null)return 1;if(y===null&&x!==null)return -1;
      if(x<y)return -1*sign;if(x>y)return sign;
      return a.label.localeCompare(b.label);
    });
  }
  function signedDollars(value) { return (value>0?'+':value<0?'−':'')+wholeDollars(Math.abs(value)); }
  function partyLetter(party) { const kind=partyClass(party);return kind==='is-dem'?'D':kind==='is-rep'?'R':String(party||'—'); }
  function restoreCandidateControls() {
    const controls=get('candidate-controls'),home=document.querySelector('#graph-candidate-panel .graph-panel-tools');
    if(controls&&home&&controls.parentElement!==home)home.prepend(controls);
    tableMoved=false;
  }
  function renderCompareTable() {
    const card=get('compare-table');
    if(!card)return;
    const kind=tableKind();
    if(!kind||!view){card.hidden=true;if(tableMoved)restoreCandidateControls();return;}
    card.hidden=false;
    if(table.kind!==kind){table={kind,filter:'all',query:'',sort:'s0',dir:'desc',page:0};get('compare-search').value='';}
    const text=TABLE_TEXT[kind],scopes=compareScopes();
    get('compare-table-title').textContent=kind==='candidates'?titleOf('candidates',view,compareIndustries()):text.title;
    get('compare-table-sub').textContent=kind==='candidates'&&get('mode').value!=='direct'?get('mode').selectedOptions[0].textContent+'.':text.sub;
    get('compare-search').placeholder=text.find;get('compare-search').setAttribute('aria-label',text.find);
    // The candidates' own "Direct contributions" select rides into the table's
    // tool row, moved rather than cloned, and goes home when the table closes.
    const slot=get('compare-mode-slot'),controls=get('candidate-controls');
    if(kind==='candidates'&&controls&&slot){if(controls.parentElement!==slot)slot.append(controls);controls.hidden=false;tableMoved=true;}
    else if(tableMoved)restoreCandidateControls();
    const rows=tableRows(kind,scopes),filtersList=tableFilters(rows,scopes);
    const chips=get('compare-filters');chips.replaceChildren();
    for(const entry of filtersList){
      const button=document.createElement('button');button.type='button';button.className='graph-compare-filter';
      button.dataset.filter=entry.id;button.setAttribute('aria-pressed',String(table.filter===entry.id));
      const name=document.createElement('span');name.textContent=entry.label;
      const count=document.createElement('span');count.className='graph-compare-count';count.textContent=entry.count.toLocaleString();
      button.append(name,count);
      button.onclick=()=>{table.filter=entry.id;table.page=0;renderCompareTable();};
      chips.append(button);
    }
    const loading=scopes.some(scope=>scope.status==='loading');
    const result=tableApply(rows,scopes);
    const pages=Math.max(1,Math.ceil(result.length/TABLE_PAGE));
    table.page=Math.min(table.page,pages-1);
    const slice=result.slice(table.page*TABLE_PAGE,table.page*TABLE_PAGE+TABLE_PAGE);
    const peak=Math.max(1,...result.flatMap(row=>row.amounts.filter(value=>value!==null).map(value=>Math.abs(value))));
    const head=get('compare-grid').tHead,body=get('compare-grid').tBodies[0];
    const columns=[{key:'name',label:text.noun,type:'name'}];
    if(kind==='candidates')columns.push({key:'party',label:'Party',type:'party'});
    scopes.forEach((scope,index)=>columns.push({key:'s'+index,label:scope.label,type:'amount',index}));
    if(scopes.length===2)columns.push({key:'diff',label:'Difference',type:'diff'});
    const headRow=document.createElement('tr');
    for(const column of columns){
      const th=document.createElement('th');th.scope='col';th.className='graph-compare-col graph-compare-col--'+column.type;
      const sorted=table.sort===column.key;
      th.setAttribute('aria-sort',sorted?(table.dir==='asc'?'ascending':'descending'):'none');
      const button=document.createElement('button');button.type='button';button.className='graph-compare-sort';
      // G5's amount headers are plain text; the scope colour is on each row's
      // inline bar (final fix round, TA1-06: the 8px header dot is gone).
      const label=document.createElement('span');label.textContent=column.label;button.append(label);
      const arrow=document.createElement('span');arrow.className='graph-compare-arrow';arrow.setAttribute('aria-hidden','true');
      arrow.textContent=sorted?(table.dir==='asc'?'▲':'▼'):'';button.append(arrow);
      button.title='Sort by '+column.label;
      button.onclick=()=>{
        if(table.sort===column.key)table.dir=table.dir==='asc'?'desc':'asc';
        else{table.sort=column.key;table.dir=column.type==='name'||column.type==='party'?'asc':'desc';}
        table.page=0;renderCompareTable();
      };
      th.append(button);headRow.append(th);
    }
    head.replaceChildren(headRow);
    body.replaceChildren();
    if(!slice.length){
      const tr=body.insertRow(),td=tr.insertCell();td.colSpan=columns.length;td.className='graph-compare-empty';
      td.textContent=loading?'Reading the compared scopes…':'No rows match this filter.';
    }
    for(const row of slice){
      const tr=body.insertRow();tr.dataset.id=row.id;
      for(const column of columns){
        const td=tr.insertCell();td.className='graph-compare-col graph-compare-col--'+column.type;
        if(column.type==='name'){
          if(kind==='organizations'){
            const button=document.createElement('button');button.type='button';button.className='graph-compare-name';button.textContent=row.label;
            const firstScope=row.amounts.findIndex(value=>value!==null);
            button.title=`Open ${row.label}'s downstream activity for ${scopes[firstScope].label}`;
            button.onclick=()=>openDrawer(row.id,'',firstScope);td.append(button);
          } else td.textContent=row.label;
          td.title=row.label;
        } else if(column.type==='party'){
          const dot=document.createElement('i');dot.className='graph-compare-party '+partyClass(row.party);dot.setAttribute('aria-hidden','true');
          const letter=document.createElement('span');letter.textContent=partyLetter(row.party);td.append(dot,letter);td.title=row.party||'Party not recorded';
        } else if(column.type==='amount'){
          const value=row.amounts[column.index];
          const holder=kind==='organizations'&&value!==null?document.createElement('button'):document.createElement('span');
          holder.className='graph-compare-cell';
          if(holder.tagName==='BUTTON'){holder.type='button';holder.title=`Open ${row.label}'s downstream activity for ${scopes[column.index].label}`;holder.onclick=()=>openDrawer(row.id,'',column.index);}
          const money=document.createElement('span');money.className='graph-compare-money';money.textContent=value===null?'—':wholeDollars(value);
          holder.append(money);
          // The activity view's amount is what the industry's money reached; its tooltip says what the industry gave.
          if(value!==null&&row.directs[column.index]!==null)holder.title=`${row.label} · ${scopes[column.index].label} · ${reachedText({direct:row.directs[column.index]},dollars(value),dollars)}`;
          if(value!==null){
            const track=document.createElement('span');track.className='graph-compare-track';track.setAttribute('aria-hidden','true');
            const bar=document.createElement('i');bar.className='scope-bar--'+(column.index+1);bar.style.width=Math.max(1,100*Math.abs(value)/peak).toFixed(1)+'%';
            track.append(bar);holder.append(track);
          }
          td.append(holder);
        } else {
          const diff=(row.amounts[0]||0)-(row.amounts[1]||0);td.textContent=signedDollars(diff);
          td.title=`${scopes[0].label} minus ${scopes[1].label}`;
        }
      }
    }
    const start=result.length?table.page*TABLE_PAGE+1:0,end=table.page*TABLE_PAGE+slice.length;
    get('compare-range').textContent=`Rows ${start.toLocaleString()}-${end.toLocaleString()} of ${result.length.toLocaleString()}`;
    get('compare-page').textContent=String(table.page+1);
    get('compare-prev').disabled=table.page<=0;get('compare-next').disabled=table.page>=pages-1;
    get('compare-export').disabled=!result.length;
    card.dataset.kind=kind;
  }
  function exportCompareTable() {
    const kind=tableKind();if(!kind)return;
    const scopes=compareScopes(),rows=tableApply(tableRows(kind,scopes),scopes);
    const safe=value=>'"'+String(typeof value==='string'&&/^[=+\-@\t\r]/.test(value)?"'"+value:value).replaceAll('"','""')+'"';
    // The activity view's table says what it is and gives each scope's direct amount after the difference.
    const activity=kind==='candidates'&&isActivity(view);
    const header=[TABLE_TEXT[kind].noun,'Party',...scopes.map(scope=>activity?`${scope.label} reached (USD)`:scope.label),...(scopes.length===2?['Difference']:[]),
      ...(activity?scopes.map(scope=>`${scope.label} direct (USD)`):[])];
    const lines=[...(activity?[['Graph',titleOf('candidates',view,compareIndustries())]]:[]),header,...rows.map(row=>[row.label,row.party||'',...row.amounts.map(value=>value===null?'':value),
      ...(scopes.length===2?[(row.amounts[0]||0)-(row.amounts[1]||0)]:[]),...(activity?row.directs.map(value=>value===null?'':value):[])])];
    const url=URL.createObjectURL(new Blob(['﻿'+lines.map(line=>line.map(safe).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download=`mentat-compare-${kind}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  /* -------------------------------------------------------------- wiring */
  if(get('compare-add')){
    get('compare-add').disabled=false;get('compare-add').removeAttribute('title');
    get('compare-add').setAttribute('aria-haspopup','menu');get('compare-add').setAttribute('aria-expanded','false');
    get('compare-add').onclick=()=>openCompare(get('compare-add'));
  }
  if(get('compare-clear'))get('compare-clear').onclick=()=>endCompare(compare?compare.values[0]:undefined);
  if(get('layout-grid'))get('layout-grid').onclick=()=>{if(!compare||compare.values.length>=3)return;compareLayout='';syncLayout();writeScope(filters());paintAll(false);};
  if(get('layout-stacked'))get('layout-stacked').onclick=()=>{if(!compare)return;compareLayout=compare.values.length===2?'stacked':'';syncLayout();writeScope(filters());paintAll(false);};
  if(get('compare-search'))get('compare-search').addEventListener('input',()=>{table.query=get('compare-search').value;table.page=0;renderCompareTable();});
  if(get('compare-prev'))get('compare-prev').onclick=()=>{table.page=Math.max(0,table.page-1);renderCompareTable();};
  if(get('compare-next'))get('compare-next').onclick=()=>{table.page+=1;renderCompareTable();};
  if(get('compare-export'))get('compare-export').onclick=exportCompareTable;
  const stripNode=document.querySelector('#view-graph .analysis-strip');
  if(stripNode&&typeof ResizeObserver!=='undefined'){let frame=0;new ResizeObserver(()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(fitStrip);}).observe(stripNode);}
  // For the suites and the proof: the comparison as Graph holds it.
  globalThis.MentatGraphCompare={state:()=>({compare:compare?{dim:compare.dim,values:compare.values.slice()}:null,
    layout:compareStacked()?'stacked':compare?'grid':'',scopes:compareScopes().map(scope=>({label:scope.label,status:scope.status,error:scope.error})),
    table:Object.assign({},table),timings:compareTimings.slice()}),
    pick:(kind,value)=>pickCompare(kind,String(value)),remove:index=>removeScope(index),clear:()=>endCompare(compare?compare.values[0]:undefined)};
  applyAnalysis();
  /* R8: one #split-view. While Graph is the tab, the node sits in the scope
     card's actions, just before Open in Chart Studio; on every other view it
     goes back to its own place in the page header. The node is moved, never
     cloned, so app.js's listener and its label logic keep working. */
  const splitButton=document.getElementById('split-view');
  const splitHome=splitButton?{parent:splitButton.parentNode,next:splitButton.nextElementSibling}:null;
  function placeSplit() {
    if(!splitButton||!splitHome)return;
    const anchor=get('open-builder');
    if(typeof currentView!=='undefined'&&currentView==='graph'&&anchor){
      if(splitButton.nextElementSibling!==anchor)anchor.parentNode.insertBefore(splitButton,anchor);
    } else if(splitButton.parentNode!==splitHome.parent){
      splitHome.parent.insertBefore(splitButton,splitHome.next&&splitHome.next.parentNode===splitHome.parent?splitHome.next:null);
    }
  }
  function activate(){renderTrail();placeSplit();fitChips();if((currentView==='graph'||(currentView==='evidence'&&splitView))&&!loaded){loaded=true;loadRuns();}}
  window.addEventListener('mentat:view',activate);
  document.querySelector('.workspace-tabs').addEventListener('click',activate);
  document.querySelector('.workspace-tabs').addEventListener('keydown',activate);
  window.addEventListener('hashchange',activate);activate();
})();
