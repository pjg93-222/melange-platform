/* Graph: Follow the money (lane F, board #45).
 *
 * An industry flow - companies, first recipients, the committees they pass money to, and the
 * outcome - from GET /api/flow?root=industry (contracts/flow.json). It opens from Graph's
 * overview (a "Follow the money" button beside Copy link) and from the downstream drawer (the
 * same button beside "Open in PAC evidence", which also lights that committee's routes).
 *
 * graph.js is not edited: this module reads the Graph's own selects (#graph-industry,
 * #graph-cycle, #graph-run) and the drawer's "<label> · <id>" line, adds its two buttons, and
 * takes the view over while it is open (#view-graph.flow-open; flow.css hides the rest).
 * It opens on Reported until an estimate bundle exists: the route refuses Attributed with
 * 409 and the view offers Reported. Clicking a committee re-roots the flow on it.
 */
(function (root) {
  'use strict';
  if (!root.document) return;
  const doc = root.document;
  const WORDS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight'];
  let state = null;
  let resizeTimer = 0;

  const TEMPLATE = `
<div class="flow-page-head">
  <div class="flow-page-titles">
    <p class="flow-crumb"><span id="graph-flow-crumb"></span><span class="flow-crumb-sep" aria-hidden="true">&rsaquo;</span><span>Follow the money</span></p>
    <div class="flow-title-row"><h2 class="flow-title">Graph</h2></div>
  </div>
  <button type="button" class="secondary flow-back" id="graph-flow-back"><span aria-hidden="true">&larr;</span> Back to Overview</button>
</div>
<div class="card flow-controls" role="group" aria-label="Follow the money controls">
  <label class="flow-control"><span>Industry</span><select id="graph-flow-industry"></select></label>
  <label class="flow-control flow-control--cycle"><span>Cycle</span><select id="graph-flow-cycle"></select></label>
  <div class="segmented flow-basis" role="group" aria-label="Basis">
    <button type="button" data-basis="reported" aria-pressed="true">Reported activity</button>
    <button type="button" data-basis="attributed" aria-pressed="false">Attributed estimate</button>
  </div>
  <label class="flow-control"><span>Top</span><select id="graph-flow-top">
    <option value="5">Top 5 per column</option><option value="8" selected>Top 8 per column</option>
    <option value="12">Top 12 per column</option><option value="0">Every node</option></select></label>
  <label class="flow-control"><span>Colour by</span><select id="graph-flow-colour">
    <option value="outcome">Outcome</option><option value="gradient">Gradient</option>
    <option value="source">Source</option><option value="target">Target</option></select></label>
</div>
<section class="card flow-card" aria-labelledby="graph-flow-heading">
  <h3 id="graph-flow-heading" class="flow-heading"></h3>
  <p id="graph-flow-sub" class="flow-sub"></p>
  <p id="graph-flow-reroot" class="flow-reroot" hidden></p>
  <div id="graph-flow-status" class="flow-status" role="status" hidden></div>
  <div id="graph-flow-canvas" class="flow-canvas"></div>
  <div id="graph-flow-foot" class="flow-foot"></div>
</section>`;

  const $ = id => doc.getElementById(id);
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const day = text => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(text || ''));
    return match ? `${MONTHS[Number(match[2]) - 1]} ${Number(match[3])}, ${match[1]}` : '';
  };
  const cycleLabel = cycle => (cycle ? `${Number(cycle) - 1}–${cycle}` : '');
  const possessive = name => (/s$/i.test(name) ? `${name}’` : `${name}’s`);
  const basisWords = basis => (basis === 'attributed'
    ? "Attributed estimate: each committee's spending is split by its donors' shares"
    : "Reported activity: each committee's own spending, not traced to a company");

  function graphSelect(name) { return $(`graph-${name}`); }

  function options(select) {
    return select ? [...select.options].map(option => ({value: option.value, label: option.textContent.trim()})) : [];
  }

  function cyclesOf() {
    return options(graphSelect('cycle')).map(item => item.value).filter(value => /^\d{4}$/.test(value)).sort().reverse();
  }

  function ensureHost() {
    let host = $('graph-flow');
    if (host) return host;
    host = doc.createElement('section');
    host.id = 'graph-flow';
    host.className = 'graph-flow';
    host.hidden = true;
    host.setAttribute('aria-label', 'Follow the money');
    host.innerHTML = TEMPLATE;
    $('view-graph').appendChild(host);
    $('graph-flow-back').addEventListener('click', close);
    host.querySelector('.flow-basis').addEventListener('click', event => {
      const button = event.target.closest('[data-basis]');
      if (!button || !state || button.dataset.basis === state.basis) return;
      state.basis = button.dataset.basis;
      setBasis(state.basis);
      load();
    });
    $('graph-flow-industry').addEventListener('change', event => {
      state.industry = event.target.value; state.stack = []; state.committee = ''; load();
    });
    $('graph-flow-cycle').addEventListener('change', event => {
      state.cycle = event.target.value; state.stack = []; state.committee = ''; load();
    });
    $('graph-flow-top').addEventListener('change', event => { state.top = Number(event.target.value); load(); });
    $('graph-flow-colour').addEventListener('change', event => {
      state.ribbons = event.target.value;
      if (state.chart) state.chart.update(null, {ribbons: state.ribbons});
    });
    $('graph-flow-reroot').addEventListener('click', event => {
      if (!event.target.closest('[data-back]') || !state) return;
      const previous = state.stack.pop();
      state.committee = previous.committee;
      load();
    });
    root.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (state && state.payload && !host.hidden) draw(); }, 150);
    });
    return host;
  }

  function setBasis(basis) {
    for (const button of $('graph-flow').querySelectorAll('.flow-basis [data-basis]'))
      button.setAttribute('aria-pressed', String(button.dataset.basis === basis));
  }

  function status(text, offer) {
    const node = $('graph-flow-status');
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
      button.addEventListener('click', () => { state.basis = 'reported'; setBasis('reported'); load(); });
      node.appendChild(button);
    }
  }

  function clear() {
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    $('graph-flow-canvas').replaceChildren();
    $('graph-flow-foot').textContent = '';
  }

  async function load() {
    const ticket = state.ticket = (state.ticket || 0) + 1;
    const query = new URLSearchParams({cycle: state.cycle, basis: state.basis, top: String(state.top), depth: '3',
      industry: state.industry, run: state.run});
    query.set('root', state.committee ? 'committee:' + state.committee : 'industry');
    status('Reading the flow…');
    let response, body;
    try {
      response = await fetch('/api/flow?' + query, {headers: {Accept: 'application/json'}});
      body = await response.json();
    } catch (error) {
      response = {ok: false, status: 0};
      body = {error: 'The flow could not be read: ' + error.message};
    }
    if (!state || ticket !== state.ticket) return;
    if (response.status === 409 && ['no_estimate_bundle', 'estimate_not_drawn'].includes(body.reason)) {
      clear();
      // The line under the heading names the basis asked for, not the flow drawn before it.
      $('graph-flow-sub').textContent = [basisWords(state.basis), cycleLabel(state.cycle)].filter(Boolean).join(' · ');
      status(body.error, 'Show reported activity');
      return;
    }
    if (!response.ok) { clear(); status(body.error || 'The flow could not be read.'); return; }
    // A committee asked for from the drawer is drawn: past Top N it asks for every node.
    if (state.focus && !state.committee && state.top && !(body.nodes || []).some(node => node.id === 'committee:' + state.focus)) {
      state.top = 0;
      $('graph-flow-top').value = '0';
      load();
      return;
    }
    status('');
    state.payload = body;
    draw();
  }

  function heading(payload) {
    const industry = payload.industry || state.industry;
    const steps = WORDS[payload.stages.length] || String(payload.stages.length);
    $('graph-flow-heading').textContent = payload.root.kind === 'industry'
      ? `Where ${String(industry).toLowerCase()} money went, ${steps} steps`
      : `Where ${possessive(payload.root.label)} money came from, and where it went`;
    const through = payload.coverage && payload.coverage.through ? `filings through ${day(payload.coverage.through)}` : '';
    $('graph-flow-sub').textContent = [basisWords(payload.basis), cycleLabel(payload.cycle), through].filter(Boolean).join(' · ');
    const reroot = $('graph-flow-reroot');
    reroot.replaceChildren();
    reroot.hidden = !state.stack.length;
    if (state.stack.length) {
      const back = doc.createElement('button');
      back.type = 'button';
      back.className = 'link-button';
      back.dataset.back = '1';
      back.textContent = `← Back to ${state.stack[state.stack.length - 1].label}`;
      reroot.append(`Re-rooted on ${payload.root.label} · `, back);
    }
  }

  function draw() {
    const payload = state.payload;
    heading(payload);
    const chartApi = root.MentatFlowChart;
    // FL7: one footnote line, every note in full under Details; FL10: the heading on one line.
    chartApi.footnote($('graph-flow-foot'), payload.notes || []);
    chartApi.fitHeading($('graph-flow-heading'));
    const canvas = $('graph-flow-canvas');
    const top = canvas.getBoundingClientRect().top;
    const height = Math.max(420, Math.min(820, Math.round(root.innerHeight - Math.max(top, 0) - 70)));
    const width = Math.max(640, Math.floor(canvas.clientWidth || 1100));
    if (state.chart) state.chart.destroy();
    // FL1/FL2: bars to scale in any height (the plot grows before a label is dropped), ribbons at 20% (lane G1).
    state.chart = chartApi.mount(canvas, payload, {width, height, grow: true, opacity: chartApi.VIEW_OPACITY,
      ribbons: state.ribbons,
      title: $('graph-flow-heading').textContent,
      onReroot: committee => {
        state.stack.push({committee: state.committee, label: payload.root.kind === 'industry'
          ? `${payload.industry || state.industry} money` : payload.root.label});
        state.committee = committee;
        load();
      }});
    if (state.focus && !state.committee) state.chart.trace('committee:' + state.focus);
  }

  function open(detail) {
    const host = ensureHost();
    const industries = options(graphSelect('industry')).filter(item => item.value);
    const cycles = cyclesOf();
    state = {industry: detail.industry, cycle: detail.cycle || cycles[0] || '', run: detail.run || 'published-reports',
      basis: 'reported', top: 8, ribbons: 'outcome', committee: '', focus: detail.focus || '', stack: [], ticket: 0,
      chart: null, payload: null};
    $('graph-flow-crumb').textContent = state.industry;
    $('graph-flow-industry').replaceChildren(...industries.map(item => new Option(item.label, item.value)));
    $('graph-flow-industry').value = state.industry;
    $('graph-flow-cycle').replaceChildren(...cycles.map(cycle => new Option(cycleLabel(cycle), cycle)));
    $('graph-flow-cycle').value = state.cycle;
    $('graph-flow-top').value = '8';
    $('graph-flow-colour').value = 'outcome';
    setBasis('reported');
    $('view-graph').classList.add('flow-open');
    host.hidden = false;
    clear();
    if (!state.industry) { status('Choose an industry on the Overview first.'); return; }
    if (!state.cycle) { status('This industry records no election cycle to follow.'); return; }
    load();
  }

  function close() {
    if (state && state.chart) state.chart.destroy();
    state = null;
    const host = $('graph-flow');
    if (host) host.hidden = true;
    $('view-graph').classList.remove('flow-open');
  }

  function current() {
    const industry = graphSelect('industry') ? graphSelect('industry').value : '';
    const cycle = graphSelect('cycle') && /^\d{4}$/.test(graphSelect('cycle').value) ? graphSelect('cycle').value : '';
    const run = graphSelect('run') ? graphSelect('run').value : 'published-reports';
    return {industry, cycle, run: run || 'published-reports'};
  }

  function addButtons() {
    const actions = doc.querySelector('#view-graph .graph-scope-actions');
    if (actions && !$('graph-follow-money')) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.id = 'graph-follow-money';
      button.className = 'secondary graph-follow-money';
      button.textContent = 'Follow the money';
      button.addEventListener('click', () => open(current()));
      actions.insertBefore(button, actions.firstChild);
    }
    const foot = doc.querySelector('#graph-drawer-panel .graph-panel-foot');
    if (foot && !$('graph-drawer-follow')) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.id = 'graph-drawer-follow';
      button.className = 'graph-text-button graph-drawer-follow';
      button.textContent = 'Follow the money';
      button.addEventListener('click', () => {
        const match = /(C\d{8})\s*$/.exec(($('graph-drawer-committee') || {}).textContent || '');
        open({...current(), focus: match ? match[1] : ''});
      });
      foot.insertBefore(button, $('graph-drawer-evidence') || null);
    }
  }

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', addButtons); else addButtons();
  root.addEventListener('mentat:view', event => { if ((event.detail || {}).view !== 'graph' && state) close(); });

  root.MentatGraphFlow = {open, close, isOpen: () => Boolean(state), state: () => state && {
    industry: state.industry, cycle: state.cycle, basis: state.basis, top: state.top, ribbons: state.ribbons,
    committee: state.committee, focus: state.focus}};
})(typeof globalThis === 'undefined' ? this : globalThis);
