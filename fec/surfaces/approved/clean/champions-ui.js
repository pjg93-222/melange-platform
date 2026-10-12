/* champions-ui.js: overlay for original-champions.html (Champions, board #39); owner: prompt 12. Runs after the page engine, restored-links.js and ui-kit.js. */
/* Overlay only: the engine's inline scripts and #page-data are untouched. The engine keeps its state
   (currentSector, championChamber, championSearch, openChampionDetails) and its listeners; this file
   moves the engine's own controls into the board's control row, moves the method notes into the
   "How these boards are built" drawer, and draws the two boards from the same rows v2BoardRows reads. */
(function () {
  'use strict';
  var K = window.MLK;
  var root = document.getElementById('champions');
  if (!K || !root || typeof DATA === 'undefined' || DATA.page !== 'champions') return;

  var esc = K.esc;
  var forced = null;            // proof hook: 'loading' | 'error' | null
  var pendingFocus = null;      // data-v2-detail key to refocus after a re-render
  var provOpen = new Set();     // data-v2-detail keys whose inset "Provenance" disclosure is open (kept across re-renders)
  var els = {};

  var CHEVRON = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg>';
  var SKEL = '<div class="mlk-skel-row"><span class="mlk-skel"></span><span class="mlk-skel"></span><span class="mlk-skel"></span></div>';

  function legacyReady() {
    return typeof v2Boards === 'function' && typeof v2TierValue === 'function' && typeof memberLink === 'function' &&
      typeof v2Score === 'function' && typeof number === 'function' && typeof renderV2Boards === 'function' &&
      typeof openChampionDetails !== 'undefined';
  }

  /* ---------- build the frame (moves live engine nodes; nothing is deleted) ---------- */
  function build() {
    var wrap = document.createElement('div');
    wrap.className = 'chm';
    wrap.id = 'chm';
    wrap.innerHTML =
      '<div class="chm-controls" role="group" aria-label="Board controls">' +
        '<div class="chm-controls__slot" data-slot="sector"></div>' +
        '<div class="chm-controls__slot" data-slot="chamber"></div>' +
        '<div class="mlk-search chm-search">' + K.icon('search').replace('<svg', '<svg class="mlk-search__icon"') + '</div>' +
        '<button type="button" class="mlk-info chm-howto" data-mlk-toggle="chm-method" aria-controls="chm-method" aria-expanded="false">' +
          K.icon('info') + '<span>How these boards are built</span></button>' +
      '</div>' +
      '<div class="chm-cards">' +
        card('sup', 'Most supportive') +
        card('opp', 'Most opposed') +
      '</div>' +
      '<div class="chm-folds" data-slot="folds"></div>' +
      '<aside class="mlk-drawer mlk-drawer--overlay chm-drawer" id="chm-method" role="dialog" aria-labelledby="chm-method-title" hidden>' +
        '<div class="mlk-drawer__head"><h2 class="chm-drawer__title" id="chm-method-title">How these boards are built</h2>' +
          '<button type="button" class="mlk-close" data-mlk-close="chm-method" aria-label="Close">' + K.icon('close') + '</button></div>' +
        '<div class="mlk-drawer__body chm-drawer__body"></div>' +
      '</aside>';
    root.insertBefore(wrap, root.firstChild);

    els.wrap = wrap;
    els.drawerBody = wrap.querySelector('.chm-drawer__body');
    els.sup = {cap: wrap.querySelector('#chm-sup-cap'), body: wrap.querySelector('#chm-sup-body')};
    els.opp = {cap: wrap.querySelector('#chm-opp-cap'), body: wrap.querySelector('#chm-opp-body')};

    // Controls: the engine's own sector tabs, chamber toggle and search box (their listeners stay attached).
    var sector = document.getElementById('champion-sector-tabs');
    var chamber = document.getElementById('champion-chamber-toggle');
    var search = document.getElementById('champion-member-search');
    [sector, chamber].forEach(function (seg, i) {
      if (!seg) return;
      seg.classList.remove('toggle');
      seg.classList.add('mlk-seg', i ? 'chm-seg--chamber' : 'chm-seg--sector');
      wrap.querySelector('[data-slot="' + (i ? 'chamber' : 'sector') + '"]').appendChild(seg);
    });
    if (sector) {
      // fix r1 MD-3: the engine's role=tablist gets tab keys (as the Industry and organization tablists have):
      // ArrowLeft/ArrowRight (wrapping), Home and End move focus to that tab and click() it, so the engine's own
      // click listener keeps currentSector and re-renders. Roving tabindex: only the selected tab is in the Tab order.
      els.sectorTabs = sector;
      sector.addEventListener('keydown', function (e) {
        var tab = e.target.closest && e.target.closest('[data-v2-sector]');
        if (!tab || !sector.contains(tab) || e.altKey || e.ctrlKey || e.metaKey) return;
        var tabs = sectorTabs(), i = tabs.indexOf(tab), n = tabs.length;
        var next = {ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1}[e.key];
        if (next === undefined || i < 0) return;
        e.preventDefault();
        var target = tabs[next];
        target.focus();
        if (target.getAttribute('aria-selected') !== 'true') target.click();
        syncSectorTabs();
      });
    }
    if (search) {
      search.classList.add('mlk-search__input');
      search.setAttribute('placeholder', 'Find a member');
      search.setAttribute('aria-label', 'Find a member');
      wrap.querySelector('.chm-search').appendChild(search);
    }

    // Drawer: today's method notes, verbatim and in today's order.
    var body = els.drawerBody;
    var sub = root.querySelector(':scope > .panelhead .subtle');
    if (sub) body.appendChild(sub);
    var tierPanel = document.getElementById('industry-tier-board-panel');
    var tierFold = tierPanel && tierPanel.closest('details');
    if (tierFold) { tierFold.open = true; tierFold.classList.add('chm-methodfold'); body.appendChild(tierFold); }
    var layer = root.querySelector(':scope > .v2-layer-title');
    if (layer) body.appendChild(layer);
    var honesty = root.querySelector(':scope > .honesty');
    if (honesty) body.appendChild(honesty);
    [['champions', 'Most supportive'], ['threats', 'Most opposed']].forEach(function (pair) {
      var note = root.querySelector('[data-v2-board="' + pair[0] + '"] > .panelhead .subtle');
      if (!note) return;
      var h = document.createElement('h3');
      h.className = 'chm-drawer__sub';
      h.textContent = pair[1];
      body.appendChild(h);
      body.appendChild(note);
    });

    // Mixed records and Most lobbied stay as today's collapsed sections, below the cards.
    var folds = wrap.querySelector('[data-slot="folds"]');
    Array.prototype.forEach.call(root.querySelectorAll(':scope > details.tierfold'), function (d) {
      d.classList.add('chm-fold');
      folds.appendChild(d);
    });
    var mode = root.querySelector('.toggle[aria-label="Champion score mode"]');
    if (mode) { mode.classList.remove('toggle'); mode.classList.add('mlk-seg', 'mlk-seg--sm'); }

    // Remember which disclosure was pressed so focus returns to it after the engine re-renders.
    wrap.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-v2-detail]');
      pendingFocus = b ? b.getAttribute('data-v2-detail') : pendingFocus;
    }, true);
    // An inset's "Provenance" disclosure keeps its open state when the engine re-renders the boards
    // (toggle does not bubble, so listen in the capture phase).
    wrap.addEventListener('toggle', function (e) {
      var d = e.target;
      if (!d || !d.matches || !d.matches('details[data-chm-prov]')) return;
      if (d.open) provOpen.add(d.getAttribute('data-chm-prov')); else provOpen.delete(d.getAttribute('data-chm-prov'));
    }, true);
    wrap.addEventListener('click', function (e) {
      var r = e.target.closest && e.target.closest('[data-chm-retry]');
      if (r) { forced = null; location.reload(); }
    });
  }

  function sectorTabs() {
    return els.sectorTabs ? Array.prototype.slice.call(els.sectorTabs.querySelectorAll('[data-v2-sector]')) : [];
  }
  function syncSectorTabs() {
    // The engine sets aria-selected on every render; the selected tab (or the first, if none is) takes tabindex 0.
    var tabs = sectorTabs();
    if (!tabs.length) return;
    var sel = tabs.filter(function (t) { return t.getAttribute('aria-selected') === 'true'; })[0] || tabs[0];
    tabs.forEach(function (t) { t.tabIndex = t === sel ? 0 : -1; });
  }

  function card(id, title) {
    return '<section class="chm-card" aria-labelledby="chm-' + id + '-title">' +
      '<header class="chm-card__head"><h2 class="chm-card__title" id="chm-' + id + '-title">' + esc(title) + '</h2>' +
      '<p class="chm-card__cap" id="chm-' + id + '-cap"></p></header>' +
      '<div class="chm-card__body" id="chm-' + id + '-body" aria-live="polite">' + loadingMarkup() + '</div></section>';
  }

  function loadingMarkup() { return '<p class="mlk-state chm-state" role="status">Loading boards…</p>' + new Array(9).join(SKEL); }
  function errorMarkup() {
    return '<p class="mlk-state mlk-state--error chm-state" role="alert">The champion boards could not be read.</p>' +
      '<p class="chm-state__action"><button type="button" class="mlk-btn" data-chm-retry>Retry</button></p>';
  }

  /* ---------- data (the same rows and filter v2BoardRows uses) ---------- */
  function filtered(board) {
    var q = String(championSearch || '').trim().toLocaleLowerCase();
    return (v2Boards()[board] || []).filter(function (row) {
      return (championChamber === 'Both' || row.chamber === championChamber) &&
        (!q || String(row.name || '').toLocaleLowerCase().includes(q) || String(row.bioguide || '').toLocaleLowerCase().includes(q));
    });
  }
  function limits() { return ((DATA.championsV2 || {}).receipt || {}).limits || {}; }
  function rowsLabel(n) { return number(n) + (n === 1 ? ' row' : ' rows'); }
  function billsLabel(n) { return number(n) + (n === 1 ? ' bill' : ' bills'); }

  function billLabel(key) {
    var parts = String(key || '').split(':'), congress = parts[0], type = parts[1] || '', num = parts[2] || '';
    var map = typeof SCREEN_BILL_LABELS !== 'undefined' ? SCREEN_BILL_LABELS : {};
    return {label: (map[type] || type.toUpperCase()) + num, congress: congress ? congress + 'th' : ''};
  }

  function party(row) {
    var m = K.memberByIdSync(row.bioguide);
    return m && m.party ? String(m.party) : '';
  }

  function memberCell(row) {
    var p = party(row), colour = p ? K.partyColor(p) : '';
    var dot = '<span class="mlk-dot chm-dot"' + (colour ? ' style="--mlk-dot:' + esc(colour) + '"' : '') +
      (p ? ' role="img" aria-label="' + esc(p) + '" title="' + esc(p) + '"' : ' aria-hidden="true"') + '></span>';
    return '<td class="chm-member">' + dot + '<span class="chm-who">' + memberLink(row.name, row.bioguide) +
      '<span class="chm-chamber">' + esc(row.chamber) + '</span></span></td>';
  }

  function tierCell(row) {
    var t = v2TierValue(row);
    return '<td class="chm-c">' + ([1, 2, 3].indexOf(t) >= 0
      ? '<span class="mlk-tier">T' + esc(t) + '</span>'
      : '<span class="mlk-tier chm-tier--none" aria-label="No tier">' + esc(EM_DASH) + '</span>') + '</td>';
  }

  function detailKey(board, row) { return currentSector + '|' + board + '|' + row.bioguide; }

  function inset(row, key) {
    // Bill labels stay plain text: no view opens one given bill for every key (the desk's Bills table holds
    // 23 of the 37 driving-bill keys, and Research > Bills does not read a bill parameter).
    // fix r1 MD-7 (board #39): the inset shows only the board's `BILL · congress · title` lines. Today's per-bill
    // category and sign source, the opposition provenance counts and the issues move, verbatim, behind a muted
    // "Provenance" disclosure (README rule 1: method text moves behind a link, never deleted).
    var bills = row.driving_bills || [];
    var lines = bills.length ? bills.map(function (bill) {
      var b = billLabel(bill.bill_key);
      return '<li class="chm-bill"><span class="chm-bill__main"><span class="chm-bill__key">' + esc(b.label) + '</span>' +
        (b.congress ? ' · ' + esc(b.congress) : '') + (bill.title ? ' · ' + esc(bill.title) : '') + '</span></li>';
    }).join('') : '<li class="chm-bill chm-bill--none">No driving bills were retained.</li>';
    var meta = bills.map(function (bill) {
      return '<li class="chm-inset__note"><span class="chm-bill__key">' + esc(billLabel(bill.bill_key).label) + '</span> · ' +
        (bill.category ? esc(bill.category) : 'Unmarked category') + ' · Sign source: ' + esc(bill.sign_source || 'unavailable') + '</li>';
    }).join('');
    var issues = (row.issues || []).filter(Boolean);
    return '<div class="chm-inset"><ul class="chm-bills">' + lines + '</ul>' +
      '<details class="chm-prov" data-chm-prov="' + esc(key) + '"' + (provOpen.has(key) ? ' open' : '') + '>' +
        '<summary class="chm-inset__note" style="cursor:pointer">Provenance</summary>' +
        (meta ? '<ul class="chm-bills">' + meta + '</ul>' : '') +
        '<p class="chm-inset__note">Opposition provenance · ' + esc(v2Provenance(row)) + '</p>' +
        (issues.length ? '<p class="chm-inset__note">Issues: ' + esc(issues.join('; ')) + '</p>' : '') +
      '</details></div>';
  }

  function disclosure(board, row, cls) {
    var key = detailKey(board, row), open = openChampionDetails.has(key), n = (row.driving_bills || []).length;
    return '<button type="button" class="' + cls + '" data-v2-detail="' + esc(key) + '" aria-expanded="' + open + '"' +
      ' aria-label="' + (open ? 'Hide ' : 'Show ') + esc(billsLabel(n)) + ' for ' + esc(row.name) + '">' +
      '<span class="chm-disc__text">' + esc(billsLabel(n)) + '</span><span class="chm-disc__chev">' + CHEVRON + '</span></button>';
  }

  function emptyState(board) {
    if (!(v2Boards()[board] || []).length) return '<p class="mlk-state chm-state">' + esc(EMPTY_V2_BOARD_NOTE) + '</p>';
    return '<p class="mlk-state chm-state">No members match this board, chamber, and search.</p>';
  }

  function supportive() {
    var rows = filtered('champions'), withBills = rows.some(function (r) { return (r.driving_bills || []).length; });
    var lim = limits().champions_per_chamber;
    els.sup.cap.textContent = rowsLabel(rows.length) + (lim ? ' · top ' + number(lim) + ' per chamber' : '');
    if (!rows.length) { els.sup.body.innerHTML = emptyState('champions'); return; }
    var cols = withBills ? 7 : 6, out = [];
    rows.forEach(function (row, i) {
      var key = detailKey('champions', row), open = openChampionDetails.has(key), has = (row.driving_bills || []).length > 0;
      out.push('<tr><td class="chm-rank">' + (i + 1) + '</td>' + memberCell(row) + tierCell(row) +
        '<td class="chm-c">' + v2Score(row.composite) + '</td>' +
        '<td class="chm-c">' + number(row.components.supportive_att) + '</td>' +
        '<td class="chm-c">' + number(row.components.opposition_att) + '</td>' +
        (withBills ? '<td class="chm-x">' + (has ? disclosure('champions', row, 'chm-disc chm-disc--small') : '') + '</td>' : '') + '</tr>');
      if (open && has) out.push('<tr class="chm-detailrow"><td></td><td colspan="' + (cols - 1) + '">' + inset(row, key) + '</td></tr>');
    });
    els.sup.body.innerHTML = '<div class="mlk-tablecard chm-tablecard"><table class="mlk-table chm-table chm-table--sup" aria-labelledby="chm-sup-title">' +
      '<colgroup><col class="chm-w-rank"><col><col class="chm-w-tier"><col class="chm-w-score"><col class="chm-w-sup"><col class="chm-w-opp">' + (withBills ? '<col class="chm-w-x">' : '') + '</colgroup>' +
      '<thead><tr><th scope="col" class="chm-rank">#</th><th scope="col">Member</th><th scope="col" class="chm-c">Tier</th><th scope="col" class="chm-c">Score</th>' +
      '<th scope="col" class="chm-c">Supportive</th><th scope="col" class="chm-c">Opposing</th>' +
      (withBills ? '<th scope="col" class="chm-x"><span class="chm-sr">Driving bills</span></th>' : '') + '</tr></thead><tbody>' + out.join('') + '</tbody></table></div>';
  }

  function opposed() {
    var rows = filtered('threats');
    els.opp.cap.textContent = rowsLabel(rows.length);
    if (!rows.length) { els.opp.body.innerHTML = emptyState('threats'); return; }
    var out = [];
    rows.forEach(function (row, i) {
      var key = detailKey('threats', row), open = openChampionDetails.has(key), c = row.components || {};
      out.push('<tr' + (open ? ' class="is-open"' : '') + '><td class="chm-rank">' + (i + 1) + '</td>' + memberCell(row) +
        '<td>' + v2Score(row.threat) + '</td><td>' + nullableCount(c.opp_sponsored) + '</td><td>' + nullableCount(c.opp_cosponsored) + '</td>' +
        '<td class="chm-bills-cell">' + disclosure('threats', row, 'chm-disc') + '</td></tr>');
      if (open) out.push('<tr class="chm-detailrow"><td></td><td colspan="5">' + inset(row, key) + '</td></tr>');
    });
    els.opp.body.innerHTML = '<div class="mlk-tablecard chm-tablecard"><table class="mlk-table chm-table chm-table--opp" aria-labelledby="chm-opp-title">' +
      '<colgroup><col class="chm-w-rank"><col><col class="chm-w-threat"><col class="chm-w-os"><col class="chm-w-oc"><col class="chm-w-bills"></colgroup>' +
      '<thead><tr><th scope="col" class="chm-rank">#</th><th scope="col">Member</th><th scope="col">Threat</th>' +
      '<th scope="col" class="chm-wrap">Opposed sponsored</th><th scope="col" class="chm-wrap">Opposed cosponsored</th><th scope="col">Driving bills</th></tr></thead>' +
      '<tbody>' + out.join('') + '</tbody></table></div>';
  }

  function paintState(kind) {
    var markup = kind === 'loading' ? loadingMarkup() : errorMarkup();
    [els.sup, els.opp].forEach(function (b) { b.cap.textContent = kind === 'loading' ? 'Loading…' : ''; b.body.innerHTML = markup; });
  }

  function render() {
    if (!els.wrap) return;
    if (forced) { paintState(forced); return; }
    if (!DATA.championsV2 || !DATA.championsV2.sectors) { paintState('error'); return; }
    provOpen.forEach(function (k) { if (!openChampionDetails.has(k)) provOpen.delete(k); });   // a closed row reopens with Provenance collapsed
    try { supportive(); opposed(); }
    catch (err) { paintState('error'); if (window.console) console.warn('champions-ui: board render failed', err); return; }
    if (pendingFocus) {
      var key = pendingFocus; pendingFocus = null;
      var btn = Array.prototype.find.call(els.wrap.querySelectorAll('[data-v2-detail]'), function (b) { return b.getAttribute('data-v2-detail') === key; });
      if (btn) btn.focus({preventScroll: true});
    }
  }

  /* ---------- start ---------- */
  if (!legacyReady()) return;   // the engine did not come up: leave today's page as it is
  build();
  document.documentElement.classList.add('chm-ready');

  // The engine re-renders its boards on every sector, chamber, search and disclosure change; follow it.
  var engineRender = window.renderV2Boards;
  window.renderV2Boards = function () { var r = engineRender.apply(this, arguments); syncSectorTabs(); render(); return r; };
  syncSectorTabs();
  render();

  // Party dots need the member catalog and the colour maps; draw again once both are in.
  Promise.all([K.colors(), K.catalog()]).then(render, function () { /* dots stay neutral */ });

  // "How these boards are built" lives in the control row, so the page head carries no info link.
  try { K.head.setInfo(null); } catch (e) { /* not framed */ }

  window.MelangeChampions = {
    render: render,
    state: function (kind) { forced = kind === 'loading' || kind === 'error' ? kind : null; render(); }
  };
})();
