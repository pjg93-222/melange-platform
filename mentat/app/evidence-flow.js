/* PAC evidence: the Flow view (lane F, board #44).
 *
 * A pane's third view, beside Summary, Map and Rows: where the pane's committee's money
 * came from and where it went, from GET /api/flow (contracts/flow.json). evidence.js owns
 * the panes and hands this module one pane; this module owns the view - its controls, the
 * Sankey (flow-chart.js on flow-layout.js), the Largest routes rail and the footer - and
 * hands the reader back for Summary, Map or Rows.
 *
 * Nothing here computes money. Every figure is the route's; the page formats it.
 * Reported activity is the default; "Attributed estimate" asks the route, which refuses
 * with 409 until an estimate bundle exists, and the view then offers Reported.
 */
(function (root) {
  'use strict';
  if (!root.document) return;
  const doc = root.document;
  const RAIL_LENGTH = 6;
  let state = null;
  let resizeTimer = 0;

  const TEMPLATE = `
<div class="flow-page-head">
  <div class="flow-page-titles">
    <p class="flow-crumb"><span id="evidence-flow-industry"></span><span class="flow-crumb-sep" aria-hidden="true">&rsaquo;</span><span>PAC evidence</span></p>
    <div class="flow-title-row"><h2 id="evidence-flow-title" class="flow-title"></h2><span id="evidence-flow-badge" class="flow-badge"></span></div>
  </div>
  <button type="button" class="secondary flow-back" id="evidence-flow-back"><span aria-hidden="true">&larr;</span> Back to panes</button>
</div>
<div class="card flow-controls" role="group" aria-label="Flow controls">
  <label class="flow-control flow-control--cycle"><span>Cycle</span><select id="evidence-flow-cycle"></select></label>
  <div class="segmented flow-views" role="group" aria-label="Pane view">
    <button type="button" data-view="summary" aria-pressed="false">Summary</button>
    <button type="button" data-view="map" aria-pressed="false">Map</button>
    <button type="button" data-view="flow" aria-pressed="true">Flow</button>
    <button type="button" data-view="rows" aria-pressed="false">Rows</button>
  </div>
  <div class="segmented flow-basis" role="group" aria-label="Basis">
    <button type="button" data-basis="reported" aria-pressed="true">Reported activity</button>
    <button type="button" data-basis="attributed" aria-pressed="false">Attributed estimate</button>
  </div>
  <label class="flow-control"><span>Ribbons</span><select id="evidence-flow-ribbons">
    <option value="gradient">Gradient</option><option value="source">Source</option>
    <option value="target">Target</option><option value="outcome">Outcome</option></select></label>
  <label class="flow-control"><span>Top</span><select id="evidence-flow-top">
    <option value="5">Top 5 per column</option><option value="8" selected>Top 8 per column</option>
    <option value="12">Top 12 per column</option><option value="0">Every node</option></select></label>
</div>
<div class="flow-body">
  <section class="card flow-card" aria-labelledby="evidence-flow-heading">
    <h3 id="evidence-flow-heading" class="flow-heading"></h3>
    <p id="evidence-flow-sub" class="flow-sub"></p>
    <p id="evidence-flow-reroot" class="flow-reroot" hidden></p>
    <div id="evidence-flow-status" class="flow-status" role="status" hidden></div>
    <div id="evidence-flow-canvas" class="flow-canvas"></div>
    <div id="evidence-flow-foot" class="flow-foot"></div>
  </section>
  <aside class="card flow-rail" aria-labelledby="evidence-flow-rail-title">
    <h3 id="evidence-flow-rail-title" class="flow-rail-title">Largest routes</h3>
    <ol id="evidence-flow-routes" class="flow-routes"></ol>
  </aside>
</div>`;

  const $ = id => doc.getElementById(id);
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const day = text => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(text || ''));
    return match ? `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}` : '';
  };
  const cycleLabel = cycle => (cycle ? `${Number(cycle) - 1}–${cycle}` : '');
  const full = value => '$' + Number(value || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const possessive = name => (/s$/i.test(name) ? `${name}’` : `${name}’s`);
  const surname = label => String(label || '').trim().split(/\s+/).pop();

  function ensureHost() {
    let host = $('evidence-flow');
    if (host) return host;
    host = doc.createElement('section');
    host.id = 'evidence-flow';
    host.className = 'evidence-flow';
    host.hidden = true;
    host.setAttribute('aria-label', 'Flow of money');
    host.innerHTML = TEMPLATE;
    const view = $('view-evidence');
    const map = $('evidence-map');
    if (map && map.parentNode === view) view.insertBefore(host, map.nextSibling); else view.appendChild(host);
    wire(host);
    return host;
  }

  function setPressed(group, attribute, value) {
    for (const button of group.querySelectorAll(`[${attribute}]`))
      button.setAttribute('aria-pressed', String(button.getAttribute(attribute) === value));
  }

  function wire(host) {
    $('evidence-flow-back').addEventListener('click', () => state && state.onBack && state.onBack());
    host.querySelector('.flow-views').addEventListener('click', event => {
      const button = event.target.closest('[data-view]');
      if (!button || !state || button.dataset.view === 'flow') return;
      state.onView && state.onView(button.dataset.view);
    });
    host.querySelector('.flow-basis').addEventListener('click', event => {
      const button = event.target.closest('[data-basis]');
      if (!button || !state || button.dataset.basis === state.basis) return;
      state.basis = button.dataset.basis;
      setPressed(host.querySelector('.flow-basis'), 'data-basis', state.basis);
      load();
      if (state.onChange) state.onChange();
    });
    $('evidence-flow-cycle').addEventListener('change', event => {
      if (!state) return;
      state.cycle = event.target.value;
      state.stack = [];
      state.committee = state.pane.committee;
      state.snapshot = state.pane.source === 'snapshot' ? state.pane.id : '';
      load();
    });
    $('evidence-flow-top').addEventListener('change', event => { if (state) { state.top = Number(event.target.value); load(); } });
    $('evidence-flow-ribbons').addEventListener('change', event => {
      if (!state) return;
      state.ribbons = event.target.value;
      if (state.chart) state.chart.update(null, {ribbons: state.ribbons});
    });
    $('evidence-flow-routes').addEventListener('click', event => {
      const button = event.target.closest('[data-route]');
      if (button && state && state.chart) state.chart.trace(button.dataset.route);
    });
    $('evidence-flow-routes').addEventListener('mouseleave', () => state && state.chart && state.chart.trace(null));
    $('evidence-flow-reroot').addEventListener('click', event => {
      const button = event.target.closest('[data-back]');
      if (!button || !state) return;
      const previous = state.stack.pop();
      state.committee = previous.committee;
      state.snapshot = previous.snapshot;
      load();
    });
    root.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (state && state.payload && !$('evidence-flow').hidden) draw(); }, 150);
    });
  }

  function status(text, offer) {
    const node = $('evidence-flow-status');
    node.replaceChildren();
    node.hidden = !text;
    if (!text) return;
    const words = doc.createElement('span');
    words.textContent = text;
    node.appendChild(words);
    if (offer) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.className = 'secondary flow-offer';
      button.textContent = offer;
      button.addEventListener('click', () => {
        state.basis = 'reported';
        setPressed($('evidence-flow').querySelector('.flow-basis'), 'data-basis', 'reported');
        load();
        if (state.onChange) state.onChange();
      });
      node.appendChild(button);
    }
  }

  async function load() {
    const ticket = state.ticket = (state.ticket || 0) + 1;
    if (!/^C\d{8}$/.test(state.committee)) {
      clearChart();
      status('Choose a PAC with a FEC committee ID to examine its flow.');
      return;
    }
    const query = new URLSearchParams({root: 'committee:' + state.committee, cycle: state.cycle, basis: state.basis,
      top: String(state.top), depth: '3'});
    if (state.snapshot) query.set('id', state.snapshot);
    if (state.pane.source === 'direct' || state.pane.source === 'csv') query.set('direct', '1');
    if (state.pane.industryLabel) query.set('industry', state.pane.industryLabel);
    status(state.pane.source === 'direct' || state.pane.source === 'csv'
      ? 'Collecting FEC filings and drawing the flow…'
      : 'Reading the flow…');
    $('evidence-flow').setAttribute('aria-busy', 'true');
    let response, body;
    try {
      response = await fetch('/api/flow?' + query, {headers: {Accept: 'application/json'}});
      body = await response.json();
    } catch (error) {
      body = {error: 'The flow could not be read: ' + error.message};
      response = {ok: false, status: 0};
    }
    if (!state || ticket !== state.ticket) return;
    $('evidence-flow').removeAttribute('aria-busy');
    if (response.status === 409 && body.reason === 'no_estimate_bundle') {
      clearChart();
      // The line under the heading names what was asked for, not the flow drawn before it.
      $('evidence-flow-sub').textContent = [cycleLabel(state.cycle), 'attributed estimate'].filter(Boolean).join(' · ');
      status(body.error, 'Show reported activity');
      return;
    }
    if (!response.ok) {
      clearChart();
      status(body.error || 'The flow could not be read.');
      return;
    }
    status('');
    state.payload = body;
    draw();
  }

  function clearChart() {
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    if (state) state.payload = null;
    $('evidence-flow-canvas').replaceChildren();
    $('evidence-flow-routes').replaceChildren();
    $('evidence-flow-foot').textContent = '';
  }

  function heading(payload) {
    const name = payload.root.label;
    $('evidence-flow-title').textContent = name;
    const kind = String(payload.root.committee_type || '').split(' (')[0];
    $('evidence-flow-badge').textContent = [kind, payload.root.id].filter(Boolean).join(' · ');
    $('evidence-flow-heading').textContent = `Where ${possessive(name)} money came from, and where it went`;
    const checks = (payload.coverage.per_committee || []).filter(row => row.drawn);
    const differs = checks.filter(row => Object.values(row.summary || {}).some(check => check.status === 'differs'));
    const window = `${day(payload.window.start)} – ${day(payload.window.end)}`;
    const basis = payload.basis === 'attributed' ? 'attributed estimate' : 'reported activity';
    const check = differs.length
      ? `${differs.length} committee${differs.length === 1 ? ' differs' : 's differ'} from ${differs.length === 1 ? 'its' : 'their'} FEC summary`
      : 'matches FEC summaries to the cent';
    $('evidence-flow-sub').textContent = [window, basis, check].join(' · ');
    const reroot = $('evidence-flow-reroot');
    reroot.replaceChildren();
    reroot.hidden = !state.stack.length;
    if (state.stack.length) {
      const back = doc.createElement('button');
      back.type = 'button';
      back.className = 'link-button';
      back.dataset.back = '1';
      back.textContent = `← Back to ${state.stack[state.stack.length - 1].label}`;
      reroot.append(`Re-rooted on ${name} · `, back);
    }
  }

  function rail(payload) {
    const list = $('evidence-flow-routes');
    list.replaceChildren();
    const routes = (root.MentatFlowLayout ? root.MentatFlowLayout.largestRoutes(payload, RAIL_LENGTH) : payload.routes || [])
      .slice(0, RAIL_LENGTH);
    const peak = Math.max(1, ...routes.map(route => route.value));
    for (const route of routes) {
      const item = doc.createElement('li');
      const button = doc.createElement('button');
      button.type = 'button';
      button.className = 'flow-route';
      button.dataset.route = route.target;
      const label = doc.createElement('span');
      label.className = 'flow-route-label';
      label.textContent = `${route.committee_label} → ${surname(route.candidate_label)}`
        + (route.direction && route.direction !== 'support' ? ` (${route.direction})` : '');
      const bar = doc.createElement('span');
      bar.className = 'flow-route-bar';
      const fill = doc.createElement('span');
      fill.className = `flow-route-fill flow-route-fill--${route.direction || 'support'}`;
      fill.style.width = `${Math.max(1, Math.round(route.value / peak * 100))}%`;
      bar.appendChild(fill);
      const amount = doc.createElement('span');
      amount.className = 'flow-route-amount';
      amount.textContent = full(route.value);
      button.append(label, bar, amount);
      if (route.date_unverified) button.title = 'Undated in the filing: counted, date not verified';
      item.appendChild(button);
      list.appendChild(item);
    }
  }

  function draw() {
    const payload = state.payload;
    heading(payload);
    rail(payload);
    const chartApi = root.MentatFlowChart;
    // FL7: one footnote line, every note in full under Details.
    chartApi.footnote($('evidence-flow-foot'), payload.notes || []);
    // FL10: the heading on one line.
    chartApi.fitHeading($('evidence-flow-heading'));
    const canvas = $('evidence-flow-canvas');
    const top = canvas.getBoundingClientRect().top;
    const height = Math.max(420, Math.min(820, Math.round(root.innerHeight - Math.max(top, 0) - 70)));
    const width = Math.max(640, Math.floor(canvas.clientWidth || 1000));
    // FL1/FL2: bars drawn to scale in any height (the plot grows before a label is dropped),
    // ribbons at the boards' resting opacity.
    const options = {width, height, grow: true, opacity: chartApi.VIEW_OPACITY, ribbons: state.ribbons, top: 0,
      title: $('evidence-flow-heading').textContent,
      onReroot: committee => {
        state.stack.push({committee: state.committee, snapshot: state.snapshot, label: payload.root.label});
        state.committee = committee;
        state.snapshot = '';
        load();
      },
      onCandidate: node => state.onView && state.onView('rows', {query: node.candidate_id || node.label})};
    if (state.chart) state.chart.destroy();
    state.chart = chartApi.mount(canvas, payload, options);
  }

  function open(detail) {
    const host = ensureHost();
    const pane = detail.pane;
    const basis = ['reported', 'attributed'].includes(detail.basis) ? detail.basis : 'reported';
    state = {pane, onBack: detail.onBack, onView: detail.onView, onChange: detail.onChange, basis, top: 8,
      ribbons: 'gradient',
      committee: pane.committee, snapshot: pane.source === 'snapshot' ? pane.id : '', stack: [], payload: null,
      chart: null, ticket: 0,
      cycle: String(pane.cycle || (pane.cycles || []).map(String).sort().reverse()[0] || '')};
    $('evidence-flow-industry').textContent = pane.industry || 'PAC evidence';
    $('evidence-flow-title').textContent = pane.name || pane.committee;
    $('evidence-flow-badge').textContent = [pane.category, pane.committee].filter(Boolean).join(' · ');
    const cycles = (pane.cycles || []).map(String).filter(Boolean);
    if (state.cycle && !cycles.includes(state.cycle)) cycles.push(state.cycle);
    const select = $('evidence-flow-cycle');
    select.replaceChildren(...cycles.sort().reverse().map(cycle => new Option(cycleLabel(cycle), cycle)));
    select.value = state.cycle;
    $('evidence-flow-top').value = '8';
    $('evidence-flow-ribbons').value = 'gradient';
    setPressed(host.querySelector('.flow-basis'), 'data-basis', state.basis);
    setPressed(host.querySelector('.flow-views'), 'data-view', 'flow');
    host.hidden = false;
    clearChart();
    $('evidence-flow-heading').textContent = '';
    $('evidence-flow-sub').textContent = '';
    if (!state.cycle) { status('This pane records no election cycle; choose one on the left to draw its flow.'); return; }
    load();
  }

  function close() {
    const host = $('evidence-flow');
    if (state && state.chart) state.chart.destroy();
    state = null;
    if (host) host.hidden = true;
  }

  root.MentatEvidenceFlow = {open, close, isOpen: () => Boolean(state), state: () => state && {
    committee: state.committee, cycle: state.cycle, basis: state.basis, top: state.top, ribbons: state.ribbons}};
})(typeof globalThis === 'undefined' ? this : globalThis);
