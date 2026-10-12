/* people-ui.js: overlay for people.html (People & connections, Directory tab, board #43; the shell passes ?tab=network|directory|sources); owner: prompt 16. Runs after the staffer app, restored-links.js and ui-kit.js. */
/* The Directory tab re-renders today's Staffer groups data (GET /api/staffer-groups-index, the payload staffer-groups.js reads) as
   offices/organisations on the left and the selected group's listed people on the right. A person row calls the same legacy actions
   staffer-groups.js uses (StafferTabs.show('explore'), StafferLive.addNodes, StafferLive.select). Names and titles are shown as stored. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const app = $('app');
  const header = app && app.querySelector(':scope>header');
  const nav = header && header.querySelector('nav.app-tabs');
  const exploreTab = $('explore-tab'), methodsTab = $('methods-tab'), methodsPanel = $('methods-panel');
  if (!app || !header || !nav || !exploreTab || !methodsTab || !methodsPanel || window.PeopleDirectory) return;

  const K = window.MLK || {};
  const esc = K.esc || (v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])));
  const fmt = n => Number.isFinite(Number(n)) ? Number(n).toLocaleString('en-US') : '—';
  const plural = (n, one, many) => fmt(n) + ' ' + (Number(n) === 1 ? one : many);
  const wordsOf = text => String(text || '').toLowerCase().split(/\s+/).filter(Boolean);
  const FIRST = 50, CHUNK = 400;
  const NOUN = {offices: ['office', 'offices', 'Offices'], organizations: ['organisation', 'organisations', 'Organisations']};
  /* The chip mirrors the staffer app's own #status (staffer-live.js sets it from the collection state); the board's
     'Exploratory · review required' is the EXPLORATORY state, and live data wins over the drawing. */
  const STATUS_CHIP = {'EXPLORATORY': 'Exploratory · review required', 'VERIFIED COMPLETE': 'Verified complete', 'COLLECTED · SOURCE EXCEPTIONS': 'Collected · source exceptions'};
  /* Days on the record (board #43). /api/staffer-groups-index carries no day counts, so build_clean_workspace.py writes
     data/people-days/ from the dated records the staffer service already serves: payroll stints for an office, lobbying
     reports for an organisation. index.json names the shard count; a group's people sit in one shard (FNV-1a of the group
     id), fetched when that group is shown. A value is `covered` (days inside the union of the records, gaps not counted)
     or [covered, span]; the page shows `covered`, the measure board #43 draws (the old offices section's tenure_days).
     A person with no dated record for the group shows a dash. A payload that carries `tenure_days` itself still wins. */
  const DAYS_KEY = 'tenure_days';
  const DAYS_URL = '/staffer-live/approved/data/people-days/';
  const DAYS_MEASURE = 0;  // index into [covered, span]: 0 = covered (board #43); 1 = first record start to last record end
  const HOW_TO_READ = 'Days on the record: the days covered by this person’s dated records here (payroll stints or lobbying reports), first to last, gaps not counted. It is a record span, not employment tenure; a dash means no dated record.';
  const OTHER = {current: 'earlier', earlier: 'current'};
  /* office-names:start. Office names (r5, audit D-2). An office row is named by its payroll source's first raw
     spelling. Every House spelling carries the House Statement of Disbursements' document-year prefix ('2009 COMMITTEE
     ON APPROPRIATIONS') and two carry its continuation mark ('—Con.'), while the office itself is the year-free chamber
     and office key in its id (container:office:<chamber>:<key>): one row per office across all years, so no two rows
     are the same office. A row shows the spelling without those page marks and names its chamber, because the chambers
     share committee names (Senate JUDICIARY beside House COMMITTEE ON JUDICIARY; INTELLIGENCE in both). A spelling
     that would then read like another office of its chamber stays as stored (two pairs of distinct member offices).
     Nothing is merged, re-cased or re-ordered; the stored spelling stays searchable and in the row's tooltip. */
  const CHAMBERS = {house: 'House', senate: 'Senate'};
  const chamberOf = g => { const m = /^container:office:(house|senate):/.exec(String((g && g.id) || '')); return m ? CHAMBERS[m[1]] : ''; };
  const housePageMarksOff = name => name.replace(/^\d{4} (?=\S)/, '').replace(/\s*\u2014\s*Con\.$/, '');
  function officeNames(offices) {
    const shown = new Map(), uses = new Map();
    for (const g of offices || []) {
      const stored = String((g && g.name) || ''), chamber = chamberOf(g);
      const name = (chamber === 'House' && housePageMarksOff(stored)) || stored;
      shown.set(g, name);
      uses.set(chamber + '\u0001' + name, (uses.get(chamber + '\u0001' + name) || 0) + 1);
    }
    const names = new Map();
    for (const [g, name] of shown) {
      const stored = String((g && g.name) || ''), chamber = chamberOf(g);
      names.set(String(g && g.id), {name: uses.get(chamber + '\u0001' + name) > 1 ? stored : name, stored, chamber});
    }
    return names;
  }
  /* office-names:end */

  const S = {
    view: 'network', data: null, loading: false, error: '', stage: '', retryTimer: 0,
    kind: 'offices', query: '', words: [], sel: {offices: null, organizations: null},
    showAll: {offices: false, organizations: false}, shown: {offices: FIRST, organizations: FIRST},
    split: 'current', pref: 'current', shownGroup: '', sort: 'name', hay: {}, cache: new Map(),
    days: {offices: false, organizations: false}, splitOff: {current: false, earlier: false},
    dm: {status: 'idle'}, shards: new Map(), names: new Map()
  };

  /* ---------------------------------------------------------------- header row: tabs, chip, search */
  const dirTab = document.createElement('button');
  dirTab.type = 'button';
  dirTab.id = 'directory-tab';
  dirTab.setAttribute('role', 'tab');
  dirTab.setAttribute('aria-selected', 'false');
  dirTab.setAttribute('aria-controls', 'ppl-directory');
  dirTab.tabIndex = -1;
  dirTab.textContent = 'Directory';
  methodsTab.before(dirTab);
  nav.classList.add('mlk-tabs', 'ppl-tabs');

  const vrule = document.createElement('span');
  vrule.className = 'ppl-vrule';
  vrule.setAttribute('aria-hidden', 'true');
  const chip = document.createElement('span');
  chip.id = 'ppl-status-chip';
  chip.className = 'ppl-chip';
  chip.hidden = true;
  nav.after(vrule, chip);

  const searchBox = document.createElement('div');
  searchBox.className = 'mlk-search ppl-search';
  searchBox.hidden = true;
  searchBox.innerHTML = (K.icon ? K.icon('search').replace('<svg', '<svg class="mlk-search__icon" aria-hidden="true"') : '') +
    '<input id="ppl-search" class="mlk-search__input" type="search" autocomplete="off" spellcheck="false" placeholder="Find a person, office or organisation" aria-label="Find a person, office or organisation" aria-controls="ppl-list">';
  header.append(searchBox);
  const input = searchBox.querySelector('input');

  function syncChip() {
    const status = $('status');
    const label = STATUS_CHIP[(status && status.textContent || '').trim()];
    chip.hidden = !label;
    if (label) chip.textContent = label;
    const fresh = $('freshness');
    if (fresh && fresh.textContent) chip.title = fresh.textContent;
  }
  if ($('status')) new MutationObserver(syncChip).observe($('status'), {childList: true, characterData: true, subtree: true});
  syncChip();

  /* ---------------------------------------------------------------- the Directory panel */
  const panel = document.createElement('section');
  panel.id = 'ppl-directory';
  panel.className = 'ppl-dir';
  panel.setAttribute('role', 'tabpanel');
  panel.setAttribute('aria-labelledby', 'directory-tab');
  panel.hidden = true;
  panel.innerHTML =
    '<div class="ppl-left">' +
      '<div class="mlk-seg ppl-kind" role="group" aria-label="Directory lists">' +
        '<button type="button" data-kind="offices" aria-pressed="true">Offices</button>' +
        '<button type="button" data-kind="organizations" aria-pressed="false">Organisations</button>' +
      '</div>' +
      '<div class="ppl-listwrap"><div id="ppl-list" class="ppl-list" role="listbox" aria-label="Offices"></div></div>' +
      '<div class="ppl-leftfoot" hidden><button type="button" class="mlk-btn ppl-showall"></button></div>' +
    '</div>' +
    '<div class="ppl-right">' +
      '<div class="ppl-rhead">' +
        '<div class="ppl-rtitle"><h2 id="ppl-name" class="ppl-name" tabindex="-1"></h2><p id="ppl-caption" class="ppl-caption"></p></div>' +
        '<div class="ppl-rtools">' +
          '<div class="mlk-seg ppl-split" role="group" aria-label="People shown">' +
            '<button type="button" data-split="current" aria-pressed="true">Current</button>' +
            '<button type="button" data-split="earlier" aria-pressed="false">Earlier</button>' +
          '</div>' +
          '<label class="mlk-select ppl-sort"><span class="mlk-select__label">Sort:</span><select id="ppl-sort">' +
            '<option value="name">Name</option>' +
            '<option value="days" hidden disabled>Days on the record</option>' +
            '<option value="title">Title</option>' +
          '</select></label>' +
        '</div>' +
      '</div>' +
      '<div id="ppl-body" class="ppl-body"></div>' +
    '</div>';
  app.insertBefore(panel, methodsPanel);

  const list = $('ppl-list'), listWrap = panel.querySelector('.ppl-listwrap');
  const leftFoot = panel.querySelector('.ppl-leftfoot'), showAllBtn = panel.querySelector('.ppl-showall');
  const nameEl = $('ppl-name'), captionEl = $('ppl-caption'), bodyEl = $('ppl-body');
  const kindBtns = [...panel.querySelectorAll('.ppl-kind button')];
  const splitBtns = [...panel.querySelectorAll('.ppl-split button')];
  const sortSel = $('ppl-sort');
  const daysOpt = sortSel.querySelector('option[value="days"]');
  const rtools = panel.querySelector('.ppl-rtools');

  /* ---------------------------------------------------------------- data */
  async function load(force) {
    if (S.loading) return;
    loadDaysIndex();
    S.loading = true;
    S.error = '';
    clearTimeout(S.retryTimer);
    S.retryTimer = 0;
    renderAll();
    try {
      const response = await fetch('/api/staffer-groups-index', {cache: force ? 'reload' : 'no-cache'});
      if (!response.ok) throw Error('Groups HTTP ' + response.status);
      const payload = await response.json();
      if (!payload || payload.ready === false) {
        S.stage = (payload && payload.stage) || 'records';
        S.retryTimer = setTimeout(() => load(), 3000);
      } else {
        S.data = payload;
        S.names = officeNames(payload.offices);
        S.stage = '';
        S.hay = {};
        S.cache.clear();
        for (const kind of ['offices', 'organizations'])
          S.days[kind] = groupsOf(kind).some(g => (g.people || []).some(p => p && Object.prototype.hasOwnProperty.call(p, DAYS_KEY)));
      }
    } catch (error) {
      S.error = (error && error.message) || String(error);
    } finally {
      S.loading = false;
    }
    renderAll();
  }

  /* ---------------------------------------------------------------- days on the record: index, then one shard per group */
  const failure = error => (error && error.message) || String(error);
  function loadDaysIndex(force) {
    if (S.dm.status === 'loading' || (S.dm.status === 'ok' && !force)) return;
    S.dm = {status: 'loading'};
    fetch(DAYS_URL + 'index.json', {cache: force ? 'reload' : 'no-cache'})
      .then(r => { if (!r.ok) throw Error('Days HTTP ' + r.status); return r.json(); })
      .then(meta => {
        if (!meta || meta.available !== true || !(Number(meta.shards) > 0)) throw Error((meta && meta.reason) || 'This build has no day counts');
        S.dm = {status: 'ok', meta};
      })
      .catch(error => { S.dm = {status: 'error', error: failure(error)}; })
      .then(refreshDays);
  }
  function shardOf(id) {
    let h = 0x811c9dc5;
    for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return (h >>> 0) % Number(S.dm.meta.shards);
  }
  function loadShard(n) {
    const entry = {status: 'loading'};
    S.shards.set(n, entry);
    fetch(DAYS_URL + 'shard-' + String(n).padStart(2, '0') + '.json', {cache: 'no-cache'})
      .then(r => { if (!r.ok) throw Error('Days HTTP ' + r.status); return r.json(); })
      .then(d => {
        if (!d || d.shard !== n || !d.days || typeof d.days !== 'object') throw Error('Day counts file ' + n + ' is malformed');
        S.shards.set(n, {status: 'ok', days: d.days});
      })
      .catch(error => S.shards.set(n, {status: 'error', error: failure(error)}))
      .then(refreshDays);
  }
  // {status: 'payload' | 'loading' | 'error' | 'ok', table, error} for group g; starts the fetches it needs.
  function daysState(g) {
    if (S.days[S.kind]) return {status: 'payload'};
    if (S.dm.status === 'idle') loadDaysIndex();
    if (S.dm.status !== 'ok') return S.dm.status === 'error' ? {status: 'error', error: S.dm.error} : {status: 'loading'};
    const n = shardOf(String(g.id));
    if (!S.shards.has(n)) loadShard(n);
    const shard = S.shards.get(n);
    if (shard.status !== 'ok') return shard.status === 'error' ? {status: 'error', error: shard.error} : {status: 'loading'};
    return {status: 'ok', table: shard.days[g.id] || {}};
  }
  // A day count arrived (or failed): redraw the right pane in place, keeping its scroll position and a focused person.
  function refreshDays() {
    if ($('ppl-sources')) renderSources();
    if (!S.data || panel.hidden) return;
    const active = document.activeElement, card = bodyEl.querySelector('.ppl-tablecard');
    const pid = active && bodyEl.contains(active) ? active.getAttribute('data-pid') : null;
    const top = card ? card.scrollTop : 0;
    renderRight();
    const next = bodyEl.querySelector('.ppl-tablecard');
    if (next) next.scrollTop = top;
    if (pid) [...bodyEl.querySelectorAll('.ppl-person')].find(b => b.getAttribute('data-pid') === pid)?.focus({preventScroll: true});
  }

  function groupsOf(kind) { return (S.data && Array.isArray(S.data[kind])) ? S.data[kind] : []; }
  // A group's name as the page shows it, the spelling it is stored with, and (offices) its chamber (office-names above).
  function labelOf(g) { return S.names.get(String(g && g.id)) || {name: String((g && g.name) || ''), stored: String((g && g.name) || ''), chamber: ''}; }
  const nameTitle = g => { const l = labelOf(g); return l.name !== l.stored ? ' title="' + esc('Source spelling: ' + l.stored) + '"' : ''; };
  // What a group's own name matches in search: the stored spelling and the chamber, so 'house judiciary' finds it.
  const groupText = g => (String((g && g.name) || '') + ' ' + chamberOf(g)).toLowerCase();
  function haystack(kind) {
    if (!S.hay[kind]) S.hay[kind] = groupsOf(kind).map(g => [groupText(g)].concat((g.people || []).map(p => p.name || '')).join('\n').toLowerCase());
    return S.hay[kind];
  }
  // A search hit is a group whose name or any listed person's name holds every word. Groups named by the words list
  // first and groups reached only through a person follow, each tier in the data's own order (nothing is re-ranked
  // or relabelled), so the first hit, and the one selected, is an office or organisation the words name.
  const nameHit = g => S.words.every(w => groupText(g).includes(w));
  function matching(kind) {
    const key = kind + '\u0001' + S.words.join(' ');
    if (S.cache.has(key)) return S.cache.get(key);
    const groups = groupsOf(kind);
    let out;
    if (!S.words.length) out = groups.map((_, i) => i);
    else {
      const hay = haystack(kind), viaPerson = [];
      out = [];
      for (let i = 0; i < groups.length; i++) if (S.words.every(w => hay[i].includes(w))) (nameHit(groups[i]) ? out : viaPerson).push(i);
      out = out.concat(viaPerson);
    }
    S.cache.set(key, out);
    return out;
  }
  function selectedIndex() {
    const idx = matching(S.kind);
    if (S.sel[S.kind] == null || !idx.includes(S.sel[S.kind])) S.sel[S.kind] = idx.length ? idx[0] : null;
    return S.sel[S.kind];
  }
  // A new query keeps the selection only while the words still name it; otherwise the first hit is selected.
  function reselect() {
    if (!S.words.length) return;
    for (const kind of ['offices', 'organizations']) {
      const i = S.sel[kind], idx = matching(kind);
      if (i != null && idx.length && nameHit(groupsOf(kind)[idx[0]]) && !nameHit(groupsOf(kind)[i])) S.sel[kind] = null;
    }
  }

  /* ---------------------------------------------------------------- rendering */
  function metaLine(g, kind) {
    const bits = [plural(g.people_count, 'person', 'people')];
    if (kind === 'offices' && chamberOf(g)) bits.unshift(chamberOf(g));
    if (kind === 'organizations' && (g.employee_count || g.represented_by_count)) {
      if (g.employee_count) bits.push(plural(g.employee_count, 'employee', 'employees'));
      if (g.represented_by_count) bits.push(fmt(g.represented_by_count) + ' representing');
    } else bits.push(fmt(g.current_count) + ' current');
    return bits.join(' · ');
  }
  function rowHtml(i, selected) {
    const g = groupsOf(S.kind)[i];
    return '<div class="ppl-row" role="option" id="ppl-opt-' + i + '" data-i="' + i + '" aria-selected="' + selected + '" tabindex="' + (selected ? 0 : -1) + '">' +
      '<span class="ppl-row__name"' + nameTitle(g) + '>' + esc(labelOf(g).name) + '</span><span class="ppl-row__meta">' + esc(metaLine(g, S.kind)) + '</span></div>';
  }
  let observer = null;
  function renderList(appendFrom) {
    const idx = matching(S.kind);
    const sel = selectedIndex();
    const limit = S.showAll[S.kind] ? Math.min(S.shown[S.kind], idx.length) : Math.min(FIRST, idx.length);
    list.setAttribute('aria-label', NOUN[S.kind][2]);
    if (observer) { observer.disconnect(); observer = null; }
    const old = list.querySelector('.ppl-sentinel');
    if (old) old.remove();
    if (!idx.length) {
      list.innerHTML = '<p class="mlk-state ppl-state">' + esc(groupsOf(S.kind).length ? 'No ' + NOUN[S.kind][1] + ' match that search.' : 'No ' + NOUN[S.kind][1] + ' in this data.') + '</p>';
    } else {
      const from = appendFrom == null ? 0 : appendFrom;
      const html = idx.slice(from, limit).map(i => rowHtml(i, i === sel)).join('');
      if (appendFrom == null) list.innerHTML = html; else list.insertAdjacentHTML('beforeend', html);
      if (limit < idx.length && S.showAll[S.kind]) {
        const sentinel = document.createElement('div');
        sentinel.className = 'ppl-sentinel';
        sentinel.setAttribute('aria-hidden', 'true');
        list.append(sentinel);
        observer = new IntersectionObserver(entries => {
          if (!entries.some(e => e.isIntersecting)) return;
          const before = Math.min(S.shown[S.kind], idx.length);
          S.shown[S.kind] += CHUNK;
          renderList(before);
        }, {root: listWrap, rootMargin: '300px 0px'});
        observer.observe(sentinel);
      }
    }
    const more = !S.showAll[S.kind] && idx.length > FIRST;
    leftFoot.hidden = !more;
    showAllBtn.textContent = more ? 'Show all ' + fmt(idx.length) + ' ' + NOUN[S.kind][1] : '';
  }
  function renderKinds() {
    kindBtns.forEach(b => {
      const kind = b.dataset.kind;
      b.setAttribute('aria-pressed', String(kind === S.kind));
      b.textContent = NOUN[kind][2] + (S.data ? ' · ' + fmt(matching(kind).length) : '');
    });
  }
  function peopleFor(g, st) {
    let people = (g.people || []).filter(p => (S.split === 'current') === Boolean(p.current));
    const groupHit = nameHit(g);
    if (S.words.length && !groupHit) {
      const hits = people.filter(p => S.words.every(w => String(p.name || '').toLowerCase().includes(w)));
      if (hits.length) people = hits;
    }
    const byName = (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'en', {sensitivity: 'base'});
    if (S.sort === 'days' && (st.status === 'ok' || st.status === 'payload')) {
      // Most days first; a person without a dated record here sorts last.
      people = people.slice().sort((a, b) => {
        const da = daysOf(a, st), db = daysOf(b, st);
        if ((da == null) !== (db == null)) return da == null ? 1 : -1;
        return (db || 0) - (da || 0) || byName(a, b);
      });
    } else if (S.sort === 'title') {
      people = people.slice().sort((a, b) => {
        const ta = String(a.title || ''), tb = String(b.title || '');
        if (!ta !== !tb) return ta ? -1 : 1;
        return ta.localeCompare(tb, 'en', {sensitivity: 'base'}) || byName(a, b);
      });
    } else people = people.slice().sort(byName);
    return people;
  }
  // A number, null (no dated record for this group), or undefined (not known yet / not loaded).
  function daysOf(p, st) {
    if (st.status === 'payload') {
      const v = p ? p[DAYS_KEY] : null;
      return v == null || v === '' || !Number.isFinite(Number(v)) ? null : Number(v);
    }
    if (st.status !== 'ok' || !p) return undefined;
    const v = st.table[p.id];
    const n = Array.isArray(v) ? v[DAYS_MEASURE] : v;
    return Number.isFinite(n) ? n : null;
  }
  function daysCell(p, st) {
    if (st.status === 'loading') return '<span class="mlk-skel ppl-days-skel" aria-hidden="true"></span>';
    if (st.status === 'error') return '';
    const days = daysOf(p, st);
    return days == null ? '<span class="ppl-days-none" title="No dated record" aria-label="No dated record">—</span>' : esc(fmt(days));
  }
  function titleCell(p) {
    const kinds = (S.data && S.data.membership_kinds) || {};
    const bits = [];
    if (p.title) bits.push(esc(p.title));
    if (p.membership && p.membership !== 'listed' && kinds[p.membership]) bits.push('<span class="ppl-muted">' + esc(kinds[p.membership]) + '</span>');
    return bits.join(' · ');
  }
  function renderRight() {
    const i = selectedIndex();
    const g = i == null ? null : groupsOf(S.kind)[i];
    const key = g ? S.kind + ':' + i : '';
    // The payload lists at most people_cap people per group, current ones first, so a half can hold people
    // (the counts) yet list nobody. Count what is LISTED in each half.
    const listed = {current: 0, earlier: 0};
    if (g) for (const p of (g.people || [])) listed[p && p.current ? 'current' : 'earlier']++;
    // Open on the preferred half when it lists people, else on the half that does (most organisations list no
    // current people; offices whose current people fill the cap list no earlier ones).
    S.split = listed[S.pref] || !listed[OTHER[S.pref]] ? S.pref : OTHER[S.pref];
    S.shownGroup = key;
    rtools.hidden = !g;
    const st = g ? daysState(g) : {status: 'loading'};
    // The sort needs the counts; while they load the list stays in name order and re-sorts when they arrive.
    const canSortDays = st.status !== 'error';
    daysOpt.hidden = daysOpt.disabled = !canSortDays;
    if (S.sort === 'days' && !canSortDays) S.sort = 'name';
    sortSel.value = S.sort;
    if (!g) {
      S.splitOff = {current: false, earlier: false};
      splitBtns.forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.split === S.split)); b.removeAttribute('aria-disabled'); b.removeAttribute('title'); b.removeAttribute('aria-describedby'); });
      nameEl.textContent = '';
      nameEl.removeAttribute('title');
      captionEl.textContent = '';
      bodyEl.innerHTML = '';
      return;
    }
    const total = Number(g.people_count) || 0, current = Number(g.current_count) || 0, earlier = Math.max(0, total - current);
    const count = {current, earlier};
    // A half that lists nobody while the other half lists people is disabled, and says why (ME-05): its people exist
    // in the counts but are past the listing cap, or there are none.
    const offNote = {};
    for (const half of ['current', 'earlier']) {
      S.splitOff[half] = !listed[half] && listed[OTHER[half]] > 0;
      if (S.splitOff[half]) offNote[half] = count[half] ? fmt(count[half]) + ' ' + half + ' ' + (count[half] === 1 ? 'person' : 'people') + ', none listed here' : 'No ' + half + ' people in this data';
    }
    splitBtns.forEach(b => {
      const half = b.dataset.split;
      b.setAttribute('aria-pressed', String(half === S.split));
      if (S.splitOff[half]) {
        b.setAttribute('aria-disabled', 'true');
        b.title = offNote[half];
        if (count[half]) b.setAttribute('aria-describedby', 'ppl-split-note'); else b.removeAttribute('aria-describedby');
      } else {
        b.removeAttribute('aria-disabled');
        b.removeAttribute('title');
        b.removeAttribute('aria-describedby');
      }
    });
    const label = labelOf(g);
    nameEl.textContent = label.name;
    if (label.name !== label.stored) nameEl.title = 'Source spelling: ' + label.stored; else nameEl.removeAttribute('title');
    captionEl.textContent = [label.chamber, plural(total, 'person', 'people'), fmt(current) + ' current', fmt(earlier) + ' earlier'].filter(Boolean).join(' · ');
    const people = peopleFor(g, st);
    const remainder = Math.max(0, count[S.split] - listed[S.split]);
    let html = '';
    if (people.length) {
      html += '<div class="mlk-tablecard ppl-tablecard"><table class="mlk-table ppl-table ppl-table--days" aria-label="' + esc((S.split === 'current' ? 'Current' : 'Earlier') + ' people, ' + label.name) + '"' + (st.status === 'loading' ? ' aria-busy="true"' : '') + '>' +
        '<colgroup><col class="ppl-c-name"><col><col class="ppl-c-days"></colgroup>' +
        '<thead><tr><th scope="col">Name</th><th scope="col">Title</th><th scope="col" class="mlk-num">Days on the record</th></tr></thead><tbody>' +
        people.map(p => '<tr data-pid="' + esc(p.id) + '"><td><button type="button" class="ppl-person" data-pid="' + esc(p.id) + '">' + esc(p.name) + '</button></td>' +
          '<td class="ppl-title">' + titleCell(p) + '</td>' +
          '<td class="mlk-num ppl-days">' + daysCell(p, st) + '</td></tr>').join('') +
        '</tbody></table></div>';
      if (st.status === 'error')
        html += '<div class="mlk-state mlk-state--error ppl-days-error" role="alert"><p>Days on the record could not be loaded.</p><p class="ppl-muted">' + esc(st.error) + '</p>' +
          '<button type="button" class="mlk-btn mlk-btn--sm" data-ppl-daysretry>Retry</button></div>';
      else if (st.status === 'ok' && people.every(p => daysOf(p, st) == null))
        html += '<p class="ppl-more ppl-days-empty">No dated records for the people listed here.</p>';
    } else {
      html += '<p class="mlk-state ppl-state">' + esc('No ' + S.split + ' people listed for this ' + NOUN[S.kind][0] + '.') + '</p>';
    }
    if (remainder) html += '<p class="ppl-more">' + esc('and ' + fmt(remainder) + ' more people not listed here') + '</p>';
    for (const half of ['current', 'earlier'])
      if (S.splitOff[half] && count[half]) html += '<p class="ppl-more ppl-splitnote" id="ppl-split-note">' + esc(offNote[half]) + '</p>';
    bodyEl.innerHTML = html;
    const card = bodyEl.querySelector('.ppl-tablecard');
    if (card) card.scrollTop = 0;
  }
  function renderLoading() {
    kindBtns.forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.kind === S.kind)); b.textContent = NOUN[b.dataset.kind][2]; });
    leftFoot.hidden = true;
    rtools.hidden = true;
    nameEl.textContent = '';
    nameEl.removeAttribute('title');
    captionEl.textContent = '';
    if (S.error) {
      list.innerHTML = '<div class="mlk-state mlk-state--error ppl-state" role="alert"><p>Offices and organisations could not be loaded.</p><p class="ppl-muted">' + esc(S.error) + '</p>' +
        '<button type="button" class="mlk-btn" data-ppl-retry>Retry</button></div>';
      bodyEl.innerHTML = '';
      return;
    }
    const label = S.stage ? 'Loading: ' + S.stage : 'Loading offices and organisations…';
    list.innerHTML = '<p class="ppl-loading" role="status">' + esc(label) + '</p>' +
      '<div class="ppl-skelrow" aria-hidden="true"><span class="mlk-skel"></span><span class="mlk-skel"></span></div>'.repeat(7);
    bodyEl.innerHTML = '<div class="ppl-skelhead" aria-hidden="true"><span class="mlk-skel"></span><span class="mlk-skel"></span></div>' +
      '<div class="mlk-skel-row" aria-hidden="true"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div>'.repeat(10);
  }
  function renderSources() {
    let sec = $('ppl-sources');
    if (!sec) {
      sec = document.createElement('section');
      sec.id = 'ppl-sources';
      sec.className = 'ppl-sources';
      methodsPanel.insertBefore(sec, $('count-reconciliation'));
    }
    let html = '<h2>Offices &amp; organisations</h2>';
    if (S.data) {
      const c = S.data.counts || {};
      html += '<p id="ppl-receipt">' + esc(fmt(c.offices) + ' offices · ' + fmt(c.organizations) + ' organisations · ' + fmt(c.people_in_groups) + ' people listed') + '</p>' +
        '<p id="ppl-groups-note">' + esc([S.data.employment_note, S.data.note].filter(Boolean).join(' ')) + '</p>';
      if (S.dm.status === 'ok' && S.dm.meta.definition) html += '<p id="ppl-days-note">' + esc(S.dm.meta.definition) + '</p>';
    } else if (S.error) {
      html += '<p class="note" role="status">' + esc(S.error) + ' — open the Directory tab to retry.</p>';
    } else {
      html += '<p class="note" role="status">' + esc(S.stage ? 'Loading: ' + S.stage : 'Loading offices and organisations…') + '</p>';
    }
    sec.innerHTML = html;
  }
  function renderAll() {
    const busy = !S.data && !S.error;
    if (busy && S.view === 'directory') panel.setAttribute('aria-busy', 'true'); else panel.removeAttribute('aria-busy');
    if (S.view === 'sources' || $('ppl-sources')) renderSources();
    if (!S.data) { renderLoading(); return; }
    renderKinds();
    renderList();
    renderRight();
  }

  /* ---------------------------------------------------------------- selection and actions */
  function select(i, focus) {
    if (i == null || i === S.sel[S.kind]) { if (focus) focusRow(i); return; }
    S.sel[S.kind] = i;
    list.querySelectorAll('.ppl-row[aria-selected="true"]').forEach(r => { r.setAttribute('aria-selected', 'false'); r.tabIndex = -1; });
    const row = $('ppl-opt-' + i);
    if (row) { row.setAttribute('aria-selected', 'true'); row.tabIndex = 0; }
    renderRight();
    if (focus) focusRow(i);
  }
  function focusRow(i) {
    const row = i == null ? null : $('ppl-opt-' + i);
    if (row) { row.focus({preventScroll: true}); row.scrollIntoView({block: 'nearest'}); }
  }
  function openPerson(id) {
    const g = groupsOf(S.kind)[S.sel[S.kind]];
    const person = g && (g.people || []).find(p => p.id === id);
    if (!person) return;
    if (window.StafferTabs) window.StafferTabs.show('explore'); else exploreTab.click();
    // The person may not be in the bounded map yet; StafferLive.select refuses an id it does not already hold (staffer-groups.js).
    if (window.StafferLive) {
      window.StafferLive.addNodes([{id: person.id, label: person.name, kind: 'person'}]);
      window.StafferLive.select(person.id, true);
    }
  }

  list.addEventListener('click', e => {
    const row = e.target.closest('.ppl-row');
    if (row) select(Number(row.dataset.i), true);
    if (e.target.closest('[data-ppl-retry]')) load(true);
  });
  list.addEventListener('keydown', e => {
    const row = e.target.closest('.ppl-row');
    if (!row || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const rows = [...list.querySelectorAll('.ppl-row')];
    const at = rows.indexOf(row);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)));
    select(Number(rows[next].dataset.i), true);
  });
  showAllBtn.addEventListener('click', () => {
    const before = Math.min(FIRST, matching(S.kind).length);
    S.showAll[S.kind] = true;
    S.shown[S.kind] = FIRST + CHUNK;
    renderList(before);
    const next = list.querySelectorAll('.ppl-row')[before];
    if (next) next.focus({preventScroll: false}); else list.querySelector('.ppl-row[aria-selected="true"]')?.focus();
  });
  kindBtns.forEach(b => b.addEventListener('click', () => {
    if (b.dataset.kind === S.kind || !S.data) { S.kind = b.dataset.kind; if (!S.data) renderLoading(); return; }
    S.kind = b.dataset.kind;
    listWrap.scrollTop = 0;
    renderKinds();
    renderList();
    renderRight();
  }));
  splitBtns.forEach(b => b.addEventListener('click', () => {
    if (b.getAttribute('aria-disabled') === 'true') return;
    S.split = S.pref = b.dataset.split;
    renderRight();
  }));
  sortSel.addEventListener('change', () => {
    const v = sortSel.value;
    S.sort = v === 'title' ? 'title' : v === 'days' && !daysOpt.disabled ? 'days' : 'name';
    renderRight();
  });
  bodyEl.addEventListener('click', e => {
    if (e.target.closest('[data-ppl-daysretry]')) {
      if (S.dm.status === 'error') S.dm = {status: 'idle'};
      for (const [n, shard] of S.shards) if (shard.status === 'error') S.shards.delete(n);
      renderRight();
      const again = bodyEl.querySelector('[data-ppl-daysretry]');
      (again || nameEl).focus({preventScroll: true});
      return;
    }
    const hit = e.target.closest('[data-pid]');
    if (hit) openPerson(hit.dataset.pid);
  });

  let searchTimer = 0;
  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      S.query = input.value;
      S.words = wordsOf(input.value);
      S.showAll = {offices: false, organizations: false};
      S.shown = {offices: FIRST, organizations: FIRST};
      listWrap.scrollTop = 0;
      if (S.data) { reselect(); renderKinds(); renderList(); renderRight(); }
    }, 140);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape' && input.value) { e.preventDefault(); e.stopPropagation(); input.value = ''; input.dispatchEvent(new Event('input')); }
    else if (e.key === 'ArrowDown') { const row = list.querySelector('.ppl-row[aria-selected="true"]') || list.querySelector('.ppl-row'); if (row) { e.preventDefault(); row.focus(); } }
  });

  /* ---------------------------------------------------------------- tabs */
  function syncParam(view) {
    const value = view === 'network' ? null : view;
    const apply = u => { if (value) u.searchParams.set('tab', value); else u.searchParams.delete('tab'); return u.pathname + u.search + u.hash; };
    try { history.replaceState(history.state, '', apply(new URL(location.href))); } catch (e) { /* address bar is cosmetic */ }
    try {
      const shell = window.parent !== window && window.parent.MelangeShell;
      if (shell && shell.view() === 'people' && window.frameElement && window.frameElement.isConnected)
        window.parent.history.replaceState(window.parent.history.state, '', apply(new URL(window.parent.location.href)));
    } catch (e) { /* cross-document access refused: keep the shell address */ }
  }
  function setView(view, opts) {
    const o = opts || {};
    S.view = view;
    const dir = view === 'directory';
    panel.hidden = !dir;
    searchBox.hidden = !dir;
    dirTab.setAttribute('aria-selected', String(dir));
    dirTab.tabIndex = dir ? 0 : -1;
    document.body.classList.toggle('ppl-view-directory', dir);
    if (dir) {
      $('explore-panel').hidden = true;
      methodsPanel.hidden = true;
      if ($('explore-tools')) $('explore-tools').hidden = true;
      for (const t of [exploreTab, methodsTab]) { t.setAttribute('aria-selected', 'false'); t.tabIndex = -1; }
      document.body.classList.remove('view-methods');
    }
    if ((dir || view === 'sources') && !S.data && !S.loading && !S.retryTimer) load();
    else renderAll();
    if (o.sync !== false) syncParam(view);
    howToRead(dir);
  }
  // The Directory's method line sits behind the page head's "How to read this" while the Directory is shown.
  let howToReadShown = false;
  function howToRead(on) {
    if (on === howToReadShown) return;
    try {
      if (K.head && typeof K.head.setInfo === 'function' && (K.head.setInfo(on ? {text: HOW_TO_READ} : null) || !on)) howToReadShown = on;
    } catch (e) { /* not inside the shell */ }
  }
  const legacyShow = window.StafferTabs && window.StafferTabs.show;
  if (legacyShow) {
    window.StafferTabs.show = function (view) {
      if (view === 'directory') return setView('directory');
      const result = legacyShow.apply(this, arguments);
      setView(methodsPanel.hidden ? 'network' : 'sources');
      return result;
    };
  }
  exploreTab.addEventListener('click', () => setView('network'));
  methodsTab.addEventListener('click', () => setView('sources'));
  dirTab.addEventListener('click', () => setView('directory'));
  nav.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = [exploreTab, dirTab, methodsTab];
    const at = tabs.indexOf(e.target);
    if (at < 0) return;
    e.preventDefault();
    e.stopPropagation();
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (at + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    if (tabs[next] === dirTab) setView('directory'); else tabs[next].click();
    tabs[next].focus();
  }, true);

  window.addEventListener('staffer-revision', event => {
    const detail = event.detail || {};
    const next = detail.source_revision || detail.revision;
    if (S.data && next && next !== S.data.revision && S.view === 'directory') { S.data = null; load(true); }
  });

  /* ---------------------------------------------------------------- Network tab: inventory failure
     staffer-explorer.js reports a failed inventory load only in the line under its search box ('Inventory HTTP 502 — use
     Refresh to retry.') and leaves the map caption at 'Opening the connection network…'. Until the inventory has loaded
     once (the explorer then stamps #inventory-total with data-revision), that line swaps the caption for an error state
     whose Retry runs the toolbar's own Refresh. The explorer's files are untouched. */
  const invTotal = $('inventory-total'), searchStatus = $('search-status'), graphCaption = $('graph-caption'), refreshBtn = $('refresh');
  if (invTotal && searchStatus && graphCaption && refreshBtn) {
    const RETRY = /\s*(?:—\s*use\s+)?Refresh to retry\.\s*$/i;
    const netError = document.createElement('div');
    netError.id = 'ppl-net-error';
    netError.className = 'mlk-state mlk-state--error ppl-neterror';
    netError.setAttribute('role', 'alert');
    netError.hidden = true;
    graphCaption.after(netError);
    let retrying = false, shownDetail = null;
    const syncNetError = () => {
      const text = (searchStatus.textContent || '').trim();
      const failed = !retrying && RETRY.test(text) && !Object.prototype.hasOwnProperty.call(invTotal.dataset, 'revision');
      if (failed) {
        const detail = text.replace(RETRY, '').trim();
        if (detail !== shownDetail || netError.hidden) {
          shownDetail = detail;
          netError.innerHTML = '<p class="ppl-neterror__title">The connection network could not be loaded.</p>' +
            (detail ? '<p class="ppl-muted">' + esc(detail) + '</p>' : '') +
            '<button type="button" class="mlk-btn mlk-btn--sm" data-ppl-netretry>Retry</button>';
        }
      } else shownDetail = null;
      netError.hidden = !failed;
      document.body.classList.toggle('ppl-net-failed', failed);
    };
    // Any new word from the explorer ends a retry; a repeat failure writes the same line again and shows the error again.
    new MutationObserver(() => { retrying = false; syncNetError(); }).observe(searchStatus, {childList: true, characterData: true, subtree: true});
    new MutationObserver(syncNetError).observe(invTotal, {attributes: true, attributeFilter: ['data-revision']});
    // The toolbar's Refresh (also what Retry presses) starts a new load: back to the loading caption meanwhile.
    document.addEventListener('click', e => {
      if (!e.target || !e.target.closest || !e.target.closest('#refresh') || netError.hidden) return;
      const hadFocus = netError.contains(document.activeElement);
      retrying = true;
      syncNetError();
      if (hadFocus) $('canvas')?.focus({preventScroll: true});
    }, true);
    netError.addEventListener('click', e => { if (e.target.closest('[data-ppl-netretry]')) refreshBtn.click(); });
    syncNetError();
  }

  window.PeopleDirectory = {
    show() { setView('directory'); },
    reload() { S.data = null; S.error = ''; clearTimeout(S.retryTimer); S.retryTimer = 0; return load(true); },
    get view() { return S.view; },
    get ready() { return Boolean(S.data); },
    get state() {
      const g = S.data && S.sel[S.kind] != null ? groupsOf(S.kind)[S.sel[S.kind]] : null;
      const shard = g && S.dm.status === 'ok' ? S.shards.get(shardOf(String(g.id))) : null;
      return {view: S.view, kind: S.kind, split: S.split, sort: S.sort, query: S.query, selected: S.sel[S.kind], loading: S.loading, error: S.error,
        daysColumn: true, daysSource: S.days[S.kind] ? 'payload' : 'people-days', daysIndex: S.dm.status, daysShard: shard ? shard.status : null,
        howToRead: howToReadShown, splitDisabled: {...S.splitOff}, name: g ? labelOf(g).name : null, chamber: g ? chamberOf(g) : null};
    }
  };

  const initial = new URLSearchParams(location.search).get('tab');
  if (initial === 'directory') setView('directory', {sync: false});
  else if (initial === 'sources') { if (window.StafferTabs) window.StafferTabs.show('methods'); else methodsTab.click(); }
})();
