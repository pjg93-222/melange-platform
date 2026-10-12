/* Saved committee routes and complete relationship tables; never donor tracing. */
(function () {
  'use strict';
  const PAGE = 40;
  function descendants(start, edges, cycle) {
    const seen = new Set([start]), queue = [start], next = new Map();
    for (const edge of edges) {
      if (cycle && String(edge.cycle) !== String(cycle)) continue;
      if (!next.has(edge.source)) next.set(edge.source, []);
      next.get(edge.source).push(edge.destination);
    }
    for (const id of queue) for (const child of next.get(id) || []) {
      if (!seen.has(child)) { seen.add(child); queue.push(child); }
    }
    return seen;
  }
  function selectRows(rows, options) {
    const terms = String(options.query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    // An all-cycle view must not invent a path by joining edges from different elections.
    const byCycle = new Map();
    const included = row => {
      if (!options.focus) return true;
      if (!options.branches) return row.committee === options.focus;
      const cycle = String(row.cycle);
      if (!byCycle.has(cycle)) byCycle.set(cycle, descendants(options.focus, options.edges || [], cycle));
      return byCycle.get(cycle).has(row.committee);
    };
    const selected = rows.filter(row => row.direction === options.direction
      && (!options.cycle || String(row.cycle) === String(options.cycle))
      && included(row)
      && terms.every(term => [row.candidate,row.candidate_id,row.committee,row.committee_name,...(row.route||[])].join(' ').toLowerCase().includes(term)));
    const order = options.sort || 'amount';
    selected.sort((a,b) => (order === 'name' ? String(a.candidate).localeCompare(String(b.candidate))
      : order === 'absolute' ? Math.abs(b.amount)-Math.abs(a.amount)
      : order === 'ascending' ? a.amount-b.amount : b.amount-a.amount)
      || String(a.committee).localeCompare(String(b.committee)) || String(a.candidate_id).localeCompare(String(b.candidate_id)));
    return selected;
  }
  const pure = {descendants, selectRows, PAGE};
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
  if (typeof document === 'undefined') return;
  const states = new WeakMap();
  const money = value => (value < 0 ? '-' : '') + '$' + Math.abs(Number(value)).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  const el = (tag, text, className) => { const node=document.createElement(tag); if(text!=null)node.textContent=text; if(className)node.className=className; return node; };
  function button(text, action) { const node=el('button',text,'graph-text-button');node.type='button';node.onclick=action;return node; }
  function download(state) {
    const csv = value => '"' + String(typeof value==='string' && /^[=+\-@\t\r]/.test(value) ? "'"+value : value).replaceAll('"','""') + '"';
    const rows = [['Basis','Observed PAC activity; not traced donor dollars'],
      ['Scope',`Root ${state.data.root}; cycle ${state.cycle||'all saved'}; direction ${state.direction}; selected PAC ${state.focus||'all'}; descendants ${state.branches}; query ${state.query}`],
      ['Candidate','Candidate ID','Spending PAC','Cycle','Direction','Net amount','Net share','Observed route'],
      ...selectRows(state.data.rows,state).map(r=>[r.candidate,r.candidate_id,r.committee,r.cycle,r.direction,r.amount,r.share_of_pac_direction??'',(r.route||[]).join(' > ')])];
    const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.map(r=>r.map(csv).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const link=el('a');link.href=url;link.download='mentat-saved-pac-relationships.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function inspect(state, row) {
    const host=state.inspector;host.replaceChildren();host.hidden=false;
    host.append(el('h3', row.candidate || row.candidate_id || 'Unidentified candidate'));
    host.append(el('p',`${money(row.amount)} · ${row.direction} · cycle ${row.cycle}`));
    const route=el('div',null,'graph-route-chain');
    for(const id of row.route||[row.committee]) route.append(button(id,()=>focus(state,id)));
    if ((row.route || []).length > 1)
      host.append(el('p',`${state.data.rootLabel || state.data.root}-backed spending via ${row.committee_name || row.committee}`));
    host.append(route,el('p','Backing is assumed from recorded PAC transfers.','muted'));
    const source=state.data.sources.find(s=>s.committee===row.committee && String(s.cycle)===String(row.cycle));
    host.append(el('p',source ? `${source.coverage_start||'Unknown start'} to ${source.coverage_end||'unknown end'} · SHA-256 ${source.sha256||'unavailable'}`
      : 'No saved snapshot for this committee/cycle. Activity follows the saved workbook.','muted'));
    if(state.data.onEvidence)host.append(button('Open this PAC in evidence',()=>state.data.onEvidence(row.committee,row.cycle)));
    host.append(button('Close detail',()=>{host.hidden=true;}));
  }
  function focus(state, id) { state.focus=id;state.page=0;state.edgePage=0;state.inspector.hidden=true;paint(state); }
  function paint(state) {
    state.edges=state.data.edges;state.direction=state.data.direction;
    const rows=selectRows(state.data.rows,state);
    state.page=Math.max(0,Math.min(state.page,Math.ceil(rows.length/PAGE)-1));
    state.route.replaceChildren();
    state.route.append(button('Whole saved network',()=>focus(state,'')));
    if(state.focus)state.route.append(el('strong',state.focus));
    const current=state.focus||state.data.root;
    const edges=state.data.edges.filter(e=>(!state.cycle||String(e.cycle)===String(state.cycle))&&(e.source===current||e.destination===current));
    state.edgePage=Math.max(0,Math.min(state.edgePage||0,Math.ceil(edges.length/PAGE)-1));
    const links=el('div',null,'graph-route-links');
    for(const edge of edges.slice(state.edgePage*PAGE,(state.edgePage+1)*PAGE)) {
      const outward=edge.source===current, target=outward?edge.destination:edge.source;
      links.append(button(`${outward?'→':'←'} ${target} · ${money(edge.amount)} · ${edge.cycle}`,()=>focus(state,target)));
    }
    if(!edges.length)links.append(el('p','No further saved committee route at this node. This is a coverage limit, not proof of no activity.','muted'));
    state.route.append(links);
    if(edges.length>PAGE) {
      const pager=el('div',null,'graph-explorer-pager');
      pager.append(el('span',`${edges.length} connections · page ${state.edgePage+1}`));
      const previous=button('Previous routes',()=>{state.edgePage--;paint(state);});previous.disabled=state.edgePage===0;
      const next=button('More routes',()=>{state.edgePage++;paint(state);});next.disabled=(state.edgePage+1)*PAGE>=edges.length;
      pager.append(previous,next);state.route.append(pager);
    }
    state.count.textContent=`${rows.length.toLocaleString()} relationships · showing ${rows.length?state.page*PAGE+1:0}–${Math.min(rows.length,(state.page+1)*PAGE)}`;
    state.body.replaceChildren();
    for(const row of rows.slice(state.page*PAGE,(state.page+1)*PAGE)) {
      const tr=el('tr'), candidate=el('td');candidate.append(button(row.candidate||'Unidentified candidate',()=>inspect(state,row)));tr.append(candidate);
      tr.append(el('td',money(row.amount),'num'),el('td',row.share_of_pac_direction==null?'Unavailable':(row.share_of_pac_direction*100).toFixed(1)+'%','num'));
      const pac=el('td');pac.append(button(row.committee_name||row.committee,()=>focus(state,row.committee)));tr.append(pac,el('td',row.cycle));state.body.append(tr);
    }
    state.previous.disabled=state.page===0;state.next.disabled=(state.page+1)*PAGE>=rows.length;
  }
  function mount(host,data) {
    let state=states.get(host);
    if(!state||state.data.root!==data.root) { state={data,query:'',sort:'amount',cycle:'',focus:'',branches:true,page:0};states.set(host,state); }
    state.data=data;
    const box=el('section',null,'graph-explorer');box.setAttribute('aria-label','Explore saved PAC network');
    // In the drawer the explorer starts collapsed (Paul, 2026-09-23, board 5):
    // this toggle, first in the box, is all that shows under the rows until it
    // is pressed, and pressing it opens the explorer at the drawer's full
    // width. graph.css keys the collapse on .graph-drawer-wide, the one state
    // the toggle sets.
    const panel=host.closest('.graph-drawer');
    if(panel) {
      const label=open=>open?'Collapse explorer':'Expand explorer';
      const expand=button(label(panel.classList.contains('graph-drawer-wide')),()=>{
        const wide=panel.classList.toggle('graph-drawer-wide');
        expand.textContent=label(wide);
        expand.setAttribute('aria-expanded',String(wide));
      });
      expand.classList.add('graph-explorer-toggle');
      expand.setAttribute('aria-expanded',String(panel.classList.contains('graph-drawer-wide')));
      box.append(expand);
    }
    box.append(el('h3','Explore PAC routes & candidates'));
    box.append(el('p','Search all candidates and PAC routes. Backing is assumed from recorded transfers.','muted'));
    const tools=el('div',null,'graph-explorer-tools');
    const searchLabel=el('label','Search candidates or PACs'),search=el('input');search.type='search';search.placeholder='Name, ID, or route';search.value=state.query;searchLabel.append(search);tools.append(searchLabel);
    search.oninput=()=>{state.query=search.value;state.page=0;paint(state);};
    const sortLabel=el('label','Sort'),sort=el('select');
    for(const [value,label] of [['amount','Largest net amount'],['absolute','Largest magnitude'],['ascending','Smallest / reversals'],['name','Candidate name']]) {const option=el('option',label);option.value=value;sort.append(option);}sort.value=state.sort;
    sort.onchange=()=>{state.sort=sort.value;state.page=0;paint(state);};sortLabel.append(sort);tools.append(sortLabel);
    const cycleLabel=el('label','Cycle'),cycle=el('select');
    for(const year of ['',...new Set(data.rows.map(r=>String(r.cycle)))].sort()) {const option=el('option',year||'All saved cycles');option.value=year;cycle.append(option);}cycle.value=state.cycle;
    cycle.onchange=()=>{state.cycle=cycle.value;state.page=0;paint(state);};cycleLabel.append(cycle);tools.append(cycleLabel);
    const branchLabel=el('label',null,'graph-explorer-check'),branches=el('input');branches.type='checkbox';branches.checked=state.branches;branchLabel.append(branches,document.createTextNode('Include descendants of selected PAC'));tools.append(branchLabel);
    branches.onchange=()=>{state.branches=branches.checked;state.page=0;paint(state);};
    tools.append(button('Export matching relationships',()=>download(state)));box.append(tools);
    const network=el('details');network.open=true;network.append(el('summary','Committee routes'));state.route=el('div',null,'graph-route-chain');network.append(state.route);box.append(network);
    state.count=el('p',null,'muted');state.count.setAttribute('role','status');box.append(state.count);
    const scroll=el('div',null,'table-scroll'),table=el('table'),head=table.createTHead().insertRow();
    for(const label of ['Candidate','Net amount','Net share','Spending PAC','Cycle'])head.append(el('th',label));state.body=table.createTBody();scroll.append(table);box.append(scroll);
    const pager=el('div',null,'graph-explorer-pager');state.previous=button('Previous',()=>{state.page--;paint(state);});state.next=button('Next',()=>{state.page++;paint(state);});pager.append(state.previous,state.next);box.append(pager);
    state.inspector=el('section',null,'graph-explorer-inspector');state.inspector.hidden=true;state.inspector.setAttribute('aria-label','Selected candidate evidence');box.append(state.inspector);
    host.append(box);paint(state);
  }
  window.MentatGraphExplorer={...pure,mount};
})();
