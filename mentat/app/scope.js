/* The console's URL scope: the hash names the tab, the query string carries the
   scope. Pure except for `location` and `history`, so node:test can drive it
   with a fake pair. Contract: audits/mentat_failings_20260915/contracts/url_scope.json.

   Tab switches stay on history.replaceState (Paul's ruling C, 2026-09-17).
   pushState is never used here: Back does not step through tabs today and S5
   does not change that. */
(function (root) {
  'use strict';

  // Every key this module owns, in the order it serializes them. A scope built
  // from nothing writes its keys in this order; a scope merged into a live
  // query leaves the keys already there where they are.
  // S6's pane model (D-039). Up to four PAC evidence panes, each addressed by
  // a PREFIXED key so `cycle` can go on belonging to Graph and "whose cycle is
  // this?" stays answerable: `a.source` is the kind of source (an import or a
  // saved snapshot), `a.id` the import id or the snapshot directory name,
  // `a.committee` the FEC committee, `a.cycle` its cycle. A fifth prefix is not
  // a key this module knows, so `e.id` is preserved in the query and never read
  // as scope - the same treatment the retired studioPane gets (v4, 2026-09-19:
  // the N charts strip that wrote it is gone, and nothing reads it now).
  const PANES = ['a', 'b', 'c', 'd'];
  // S8 adds `map`: the direction of the district map open on that pane
  // ('raised' or 'spent'). Absent means the pane grid is showing, so an
  // ordinary permalink is unchanged and a map permalink reopens the map.
  // Console redesign build 06, fix round 1 (R06-09): `compare` is the second
  // cycle of an open two-cycle map compare (board M13), a four-digit cycle
  // like `cycle`. Absent means one map.
  // Lane F (board #44, 2026-09-25) adds `flow`: the basis of the Flow (Sankey) view open
  // on that pane, 'reported' or 'attributed'. Absent means the pane grid is showing.
  const PANE_FIELDS = ['source', 'id', 'committee', 'cycle', 'map', 'compare', 'flow'];
  const PANE_KEYS = [];
  for (let pane = 0; pane < PANES.length; pane += 1) {
    for (let field = 0; field < PANE_FIELDS.length; field += 1) {
      PANE_KEYS.push(PANES[pane] + '.' + PANE_FIELDS[field]);
    }
  }
  const PANE_SOURCES = ['csv', 'snapshot'];
  const PANE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
  const PANE_COMMITTEE = /^(unassigned|[A-Za-z0-9][A-Za-z0-9-]{0,31})$/;
  const PANE_MAP = ['raised', 'spent'];
  const PANE_FLOW = ['reported', 'attributed'];
  // The downstream drawer (v5, 2026-09-19). `drawer` is the committee whose
  // onward funding is open beside Top organizations, `drawerDirection` the
  // side of that committee's activity being shown. They are separate from
  // `recipient` on purpose: the click that opens the drawer also narrows the
  // page to that PAC, and a reader can close the drawer while staying
  // narrowed - so a permalink that named only `recipient` could not say
  // whether the drawer was open, and one that reused `direction` would move
  // the page's own Candidate view select.
  const DRAWER_ID = /^[A-Za-z0-9][A-Za-z0-9-]{0,31}$/;
  // The #graph-direction option values, verbatim (view_graph.html), in the
  // drawer's order. Paul, 2026-09-23, on the drawer's "Contributions / Other"
  // segment: "It should be two separate segments" - Contribution and Other are
  // that recorded bucket split by FEC transaction code. 'Contribution / Other'
  // stays a valid side: amounts whose source records no code keep it, and an
  // older permalink that names it still reads (the drawer then opens on the
  // largest side it offers).
  const DRAWER_DIRECTIONS = ['Contribution', 'Other', 'Support', 'Oppose', 'Contribution / Other', 'Unknown'];
  // Graph's comparison (v6, console redesign 02b, 2026-09-22). `compare`
  // names the one dimension being compared, and that dimension's own key -
  // industry, cycle or company - REPEATS in pill order:
  //   ?industry=Banking&industry=Credit%20Union&compare=industry#graph
  // scope.industry (and company, cycle) keeps answering the FIRST value, so
  // every reader that predates compare goes on working; scope.compareValues
  // carries the ordered values. `layout=stacked` is Graph's non-default card
  // layout for two scopes. Flat keys only, never JSON (D-039).
  const COMPARE_DIMS = ['industry', 'cycle', 'company'];
  const COMPARE_MAX = 3;
  const LAYOUTS = ['stacked'];
  const KEYS = ['industry', 'company', 'cycle', 'mode', 'direction', 'tier',
    'funding', 'category', 'recipient', 'saved', 'drawer',
    'drawerDirection', 'compare', 'layout'].concat(PANE_KEYS);
  // The #graph-mode option values, verbatim (index.html:105).
  const MODES = ['direct', 'activity', 'estimates'];
  // S6's pane model owns these. S5 never writes one and never removes one.
  const RESERVED = ['evidence', 'id', 'committee', 'offset', 'limit'];

  const CYCLE_REASON = 'cycle must be a four-digit election-cycle end year';
  const PANE_SOURCE_REASON = 'a pane source must be one of ' + PANE_SOURCES.join(', ');
  const PANE_ID_REASON = 'a pane id must be an import id or a saved-snapshot directory name';
  const PANE_COMMITTEE_REASON = 'a pane committee must be a committee id or "unassigned"';
  const PANE_MAP_REASON = 'a pane map must be one of ' + PANE_MAP.join(', ');
  const PANE_FLOW_REASON = 'a pane flow must be one of ' + PANE_FLOW.join(', ');
  const MODE_REASON = 'mode must be one of ' + MODES.join(', ');
  const SAVED_REASON = 'saved must be a 32-character hexadecimal saved-view id';
  const DRAWER_REASON = 'drawer must be a committee id';
  const DRAWER_DIRECTION_REASON = 'drawerDirection must be one of ' + DRAWER_DIRECTIONS.join(', ');
  const BLANK_REASON = 'a blank value names no scope, so the key is dropped';
  const COMPARE_REASON = 'compare must be one of ' + COMPARE_DIMS.join(', ');
  const COMPARE_COUNT_REASON = 'compare needs at least two different values of the key it names';
  const COMPARE_MAX_REASON = 'a comparison holds at most ' + COMPARE_MAX + ' values';
  const LAYOUT_REASON = 'layout must be ' + LAYOUTS.join(' or ');

  // A bad permalink still has to open its tab, so nothing in here throws: an
  // unusable value is dropped from the scope and reported through invalid[].
  function check(key, raw) {
    const text = raw == null ? '' : String(raw);
    if (!text) return {ok: false, value: text, reason: BLANK_REASON};
    const dot = key.indexOf('.');
    if (dot > 0) {
      const field = key.slice(dot + 1);
      if (field === 'source' && PANE_SOURCES.indexOf(text) < 0) return {ok: false, value: text, reason: PANE_SOURCE_REASON};
      if (field === 'id' && !PANE_ID.test(text)) return {ok: false, value: text, reason: PANE_ID_REASON};
      if (field === 'committee' && !PANE_COMMITTEE.test(text)) return {ok: false, value: text, reason: PANE_COMMITTEE_REASON};
      if (field === 'cycle' && !/^\d{4}$/.test(text)) return {ok: false, value: text, reason: CYCLE_REASON};
      if (field === 'map' && PANE_MAP.indexOf(text) < 0) return {ok: false, value: text, reason: PANE_MAP_REASON};
      if (field === 'compare' && !/^\d{4}$/.test(text)) return {ok: false, value: text, reason: CYCLE_REASON};
      if (field === 'flow' && PANE_FLOW.indexOf(text) < 0) return {ok: false, value: text, reason: PANE_FLOW_REASON};
      return {ok: true, value: text};
    }
    if (key === 'cycle' && !/^\d{4}$/.test(text)) return {ok: false, value: text, reason: CYCLE_REASON};
    if (key === 'mode' && MODES.indexOf(text) < 0) return {ok: false, value: text, reason: MODE_REASON};
    // The same rule server.py get_view() enforces, so the client never fires a
    // saved-view request the console will answer 400 for.
    if (key === 'saved' && !/^[a-f0-9]{32}$/.test(text)) return {ok: false, value: text, reason: SAVED_REASON};
    if (key === 'drawer' && !DRAWER_ID.test(text)) return {ok: false, value: text, reason: DRAWER_REASON};
    if (key === 'drawerDirection' && DRAWER_DIRECTIONS.indexOf(text) < 0)
      return {ok: false, value: text, reason: DRAWER_DIRECTION_REASON};
    if (key === 'compare' && COMPARE_DIMS.indexOf(text) < 0) return {ok: false, value: text, reason: COMPARE_REASON};
    if (key === 'layout' && LAYOUTS.indexOf(text) < 0) return {ok: false, value: text, reason: LAYOUT_REASON};
    return {ok: true, value: text};
  }

  // The compared key's values, in order: valid ones, each once. What is
  // refused is reported, except the first occurrence, which parse() has
  // already judged on its own.
  function compareValues(pairs, key, invalid) {
    const values = [];
    let first = true;
    for (const [name, value] of pairs) {
      if (name !== key) continue;
      const checked = check(key, value);
      if (!checked.ok) { if (!first) invalid.push({key: key, value: checked.value, reason: checked.reason}); }
      else if (values.indexOf(checked.value) < 0) {
        if (values.length < COMPARE_MAX) values.push(checked.value);
        else invalid.push({key: key, value: checked.value, reason: COMPARE_MAX_REASON});
      }
      first = false;
    }
    return values;
  }

  function entries(search) {
    const text = String(search == null ? '' : search).replace(/^[?]/, '');
    // URLSearchParams decodes both %20 and '+', keeps duplicates, keeps order,
    // and never throws on a malformed escape.
    return text ? [...new URLSearchParams(text)] : [];
  }

  /* --------------------------------------------------------------- reading */

  function parse(search) {
    const scope = {invalid: []};
    const seen = Object.create(null);
    const pairs = entries(search);
    for (const [key, value] of pairs) {
      if (KEYS.indexOf(key) < 0 || seen[key]) continue;
      seen[key] = true;
      const checked = check(key, value);
      if (checked.ok) scope[key] = checked.value;
      else scope.invalid.push({key: key, value: checked.value, reason: checked.reason});
    }
    // v6: the compared key's repeats are the comparison's values. Fewer than
    // two usable values is no comparison, and `compare` joins invalid[].
    // Without `compare`, a repeated key still reads its first value only.
    if (scope.compare) {
      const values = compareValues(pairs, scope.compare, scope.invalid);
      if (values.length < 2) {
        scope.invalid.push({key: 'compare', value: scope.compare, reason: COMPARE_COUNT_REASON});
        delete scope.compare;
      } else {
        scope[scope.compare] = values[0];
        scope.compareValues = values;
      }
    }
    return scope;
  }

  function read(search) {
    if (search !== undefined) return parse(search);
    const here = typeof location === 'undefined' ? null : location;
    return parse(here ? here.search : '');
  }

  /* --------------------------------------------------------------- writing */

  // Values are encodeURIComponent'd, keys are written plain. URLSearchParams
  // .toString() would spell a space as '+', and a published label such as
  // "Property & Casualty Insurance" has to survive a copied permalink.
  const encode = value => encodeURIComponent(String(value == null ? '' : value));

  // The values a scope object gives the compared key, in order, valid and
  // each once: an array on the key itself, or read()'s compareValues.
  function comparedList(source, key) {
    if (source.compare !== key) return null;
    const raw = Array.isArray(source[key]) ? source[key]
      : Array.isArray(source.compareValues) ? source.compareValues : null;
    if (!raw) return null;
    const values = [];
    for (const value of raw) {
      const checked = check(key, value);
      if (checked.ok && values.indexOf(checked.value) < 0 && values.length < COMPARE_MAX) values.push(checked.value);
    }
    return values;
  }
  // write() and serialize() take a comparison either way: {industry: [...],
  // compare: 'industry'} or read()'s own {industry, compare, compareValues}.
  function expand(partial) {
    const source = partial || {};
    if (COMPARE_DIMS.indexOf(source.compare) < 0 || Array.isArray(source[source.compare])
      || !Array.isArray(source.compareValues)) return source;
    return Object.assign({}, source, {[source.compare]: source.compareValues.slice()});
  }

  function serialize(scope) {
    const source = scope || {};
    const parts = [];
    for (const key of KEYS) {
      const list = COMPARE_DIMS.indexOf(key) >= 0 ? comparedList(source, key) : null;
      if (list) {
        for (const value of list) parts.push(key + '=' + encode(value));
        continue;
      }
      if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
      // A comparison with fewer than two values is not written at all.
      if (key === 'compare') {
        const named = comparedList(source, source.compare);
        if (!named || named.length < 2) continue;
      }
      const value = Array.isArray(source[key]) ? source[key][0] : source[key];
      const checked = check(key, value);
      if (checked.ok) parts.push(key + '=' + encode(checked.value));
    }
    return parts.join('&');
  }

  // Merge a partial scope into a live query string. An empty, null or
  // undefined value removes its key; a value this module would refuse to read
  // back removes it too, so the URL can never carry something read() rejects.
  // Every other key in the query - the retired studioPane, S6's reserved keys,
  // anything a future session adds - is preserved untouched and keeps its place.
  function merge(search, partial) {
    const pairs = entries(search);
    const changes = expand(partial);
    for (const key of Object.keys(changes)) {
      if (KEYS.indexOf(key) < 0) continue;
      // v6: an array writes the key once per value, in order, where the key
      // stood (or at the end); an empty or wholly refused array removes it.
      if (Array.isArray(changes[key])) {
        const values = [];
        for (const value of changes[key]) {
          const checked = check(key, value);
          if (checked.ok && values.indexOf(checked.value) < 0 && values.length < COMPARE_MAX) values.push(checked.value);
        }
        const at = pairs.findIndex(pair => pair[0] === key);
        for (let i = pairs.length - 1; i >= 0; i -= 1) if (pairs[i][0] === key) pairs.splice(i, 1);
        if (!values.length) continue;
        const inserted = values.map(value => [key, value]);
        if (at < 0) pairs.push(...inserted);
        else pairs.splice(at, 0, ...inserted);
        continue;
      }
      const checked = check(key, changes[key]);
      const at = pairs.findIndex(pair => pair[0] === key);
      if (!checked.ok) {
        for (let i = pairs.length - 1; i >= 0; i -= 1) if (pairs[i][0] === key) pairs.splice(i, 1);
        continue;
      }
      if (at < 0) pairs.push([key, checked.value]);
      else {
        pairs[at] = [key, checked.value];
        for (let i = pairs.length - 1; i > at; i -= 1) if (pairs[i][0] === key) pairs.splice(i, 1);
      }
    }
    // v6: the URL never carries a comparison read() would refuse - a
    // `compare` whose key holds fewer than two usable values is dropped.
    const named = pairs.find(pair => pair[0] === 'compare');
    if (named) {
      const usable = new Set(pairs.filter(pair => pair[0] === named[1] && check(named[1], pair[1]).ok).map(pair => pair[1]));
      if (COMPARE_DIMS.indexOf(named[1]) < 0 || usable.size < 2)
        for (let i = pairs.length - 1; i >= 0; i -= 1) if (pairs[i][0] === 'compare') pairs.splice(i, 1);
    }
    return pairs.map(pair => pair[0] + '=' + encode(pair[1])).join('&');
  }

  function notify(scope, options) {
    const opts = options || {};
    if (typeof opts.dispatch === 'function') { opts.dispatch(scope); return; }
    const target = opts.target || root;
    if (!target || typeof target.dispatchEvent !== 'function' || typeof CustomEvent !== 'function') return;
    target.dispatchEvent(new CustomEvent('mentat:scope', {detail: scope}));
  }

  function write(partial, options) {
    const opts = options || {};
    const here = opts.location || (typeof location === 'undefined' ? null : location);
    const past = opts.history || (typeof history === 'undefined' ? null : history);
    if (!here) return read();
    const before = String(here.search || '').replace(/^[?]/, '');
    const after = merge(before, partial);
    if (after === before || !past || typeof past.replaceState !== 'function') return parse(after);
    const url = (here.pathname || '/') + (after ? '?' + after : '') + (here.hash || '');
    // A document with an opaque origin (a page built by setContent, a sandboxed
    // frame) refuses replaceState. Nothing changed, so nothing is announced.
    try { past.replaceState(null, '', url); } catch (error) { return parse(before); }
    const scope = parse(after);
    notify(scope, opts);
    return scope;
  }

  // The permalink for a view and a scope. Used by "Open in Chart Studio" and
  // available to S6 for pane links.
  function href(view, scope, base) {
    const here = base === undefined ? (typeof location === 'undefined' ? null : location) : base;
    const path = (here && here.pathname) || '/';
    const query = serialize(scope);
    return path + (query ? '?' + query : '') + (view ? '#' + String(view) : '');
  }

  /* The one FEC link rule. Four copies of this test grew apart - evidence.js
     and cross-reference.js accept only the two named hosts, chart-studio.js
     and contribution-studio.js accept any *.fec.gov subdomain - so the same
     source_url rendered as a link on one screen and as bare text on another.
     This is the strict reading of the four: https only, host exactly
     www.fec.gov or docquery.fec.gov, no userinfo, the default port, and the
     fragment stripped. It answers the safe href, or '' for everything else,
     so a caller cannot half-apply it.
     It also keeps cross-reference's fourth rule, and raises it to every
     caller: a query string must be an FEC image number, 5 to 30 digits.
     That rule is not decoration. The negative control it was written
     against is https://www.fec.gov/file?api_key=secret
     (tests/test_cross_reference_view.cjs). A source_url is FEC-derived
     data, and a credential that ever rode in one would reach the reader's
     history and address bar the moment the row became an anchor - a
     no-referrer policy cannot help when the secret IS the URL. The two real
     FEC shapes lose nothing by it: a committee or candidate page carries no
     query at all, and a docquery image link carries the image number.
     Lives here rather than in a view because scope.js is the one module
     every view already loads and node:test can drive without a document. */
  const FEC_HOSTS = ['www.fec.gov', 'docquery.fec.gov'];
  function fecLink(value) {
    let url;
    try { url = new URL(String(value == null ? '' : value)); } catch (error) { return ''; }
    if (url.protocol !== 'https:') return '';
    if (FEC_HOSTS.indexOf(url.hostname) < 0) return '';
    if (url.username || url.password) return '';
    if (url.port && url.port !== '443') return '';
    if (url.search && !/^[?]\d{5,30}$/.test(url.search)) return '';
    url.hash = '';
    return url.href;
  }

  function subscribe(fn, target) {
    const host = target || root;
    if (typeof fn !== 'function' || !host || typeof host.addEventListener !== 'function') {
      return function () {};
    }
    const onScope = event => fn(event && event.detail ? event.detail : read());
    const onPop = () => fn(read());
    host.addEventListener('mentat:scope', onScope);
    host.addEventListener('popstate', onPop);
    return function () {
      host.removeEventListener('mentat:scope', onScope);
      host.removeEventListener('popstate', onPop);
    };
  }

  const api = {KEYS, MODES, RESERVED, PANES, PANE_FIELDS, PANE_KEYS, PANE_SOURCES,
    DRAWER_DIRECTIONS, FEC_HOSTS, COMPARE_DIMS, COMPARE_MAX, LAYOUTS,
    read, write, parse, serialize, href, fecLink, subscribe};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MentatScope = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
