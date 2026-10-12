/* Exact shortest person paths; office destinations compare a dated staff roster. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), live=window.StafferLive;
  const avoidedPeople=new Map();
  // One random token per page load names this page to the server. Identical searches are shared between pages, so a cancel or a supersede sent with it drops only this page's claim on a search another tab may still be waiting for.
  const holder=(()=>{try{const bytes=new Uint8Array(12);crypto.getRandomValues(bytes);return 'p'+Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}catch(error){return 'p'+Date.now().toString(36)+Math.random().toString(36).slice(2,12);}})();
  const endpoints={origin:null,target:null}, cache=new Map(), timers={}, generations={origin:0,target:0}, typeahead={origin:null,target:null};
  // offerShown: the Ecosystem offer after a Staffer search found no route is on the page, so the next change to the search clears it as it clears a route.
  let personRequest=0,routeRequest=0,routeController=null,activeSearch=null,selectedPersonId=null,hasRoute=false,offerShown=false,mode='person';
  function el(parent,tag,value,cls){const e=document.createElement(tag);e.textContent=String(value??'');if(cls)e.className=cls;parent.append(e);return e;}
  const name=n=>n?.label||n?.name||n?.id||'Name unavailable';
  // Each keystroke supersedes the typeahead request still in flight for that box; the server stops caring when the socket closes.
  function abortTypeahead(which){typeahead[which]?.abort();typeahead[which]=null;}
  function syncRouteAddress(){
    const address=new URL(location.href);
    for(const key of ['route_origin','route_target','office'])address.searchParams.delete(key);
    if(endpoints.origin)address.searchParams.set('route_origin',endpoints.origin.id);
    if(endpoints.target)address.searchParams.set(mode==='office'?'office':'route_target',mode==='office'?name(endpoints.target):endpoints.target.id);
    address.searchParams.set('route_model',$('route-model').value);
    address.searchParams.set('route_count',$('route-count').value);
    address.searchParams.set('work_sources',$('work-sources').value);
    address.searchParams.set('alternative_strategy',$('alternative-strategy').value);
    if(avoidedPeople.size)address.searchParams.set('avoid_people',[...avoidedPeople.keys()].join(','));
    else address.searchParams.delete('avoid_people');
    history.replaceState(null,'',address.pathname+address.search);
    window.dispatchEvent(new CustomEvent('staffer-route-state'));
  }
  function routeReadyMessage(){
    if(endpoints.origin&&endpoints.target)return 'Starting person and destination restored. Find a connection when ready.';
    if(endpoints.target)return 'Destination ready. Choose where you are starting from.';
    if(endpoints.origin)return 'Starting person ready. Choose a destination.';
    return 'Choose a starting person and destination.';
  }
  function setPanel(open){
    document.body.classList.toggle('details-collapsed',!open);
    $('panel-toggle').textContent=open?'Hide panel':'Show people & paths';
    $('panel-toggle').setAttribute('aria-expanded',String(open));
  }
  $('panel-toggle').onclick=()=>setPanel(document.body.classList.contains('details-collapsed'));
  function revealDetail(){
    setPanel(true);
    const panel=$('person-panel'),heading=$('detail-title');
    if(panel&&heading)panel.scrollTop+=heading.getBoundingClientRect().top-panel.getBoundingClientRect().top-16;
  }
  const canonicalNode=n=>({...n,id:live.canonicalId(n.id)});
  const uniquePeople=nodes=>[...new Map(nodes.filter(live.isPerson).map(n=>{const p=canonicalNode(n);return[p.id,p];})).values()];
  const TABS=[['explore','explore-tab','explore-panel'],['methods','methods-tab','methods-panel']].filter(([,id])=>$(id));
  function showTab(view){
    view=view===true?'methods':view===false?'explore':String(view||'explore');
    if(!TABS.some(([name])=>name===view))view='explore';
    for(const [name,id,panel] of TABS){const on=name===view;$(panel).hidden=!on;$(id).setAttribute('aria-selected',String(on));$(id).tabIndex=on?0:-1;}
    $('explore-tools').hidden=view!=='explore';
    document.body.classList.toggle('view-methods',view==='methods');
    window.dispatchEvent(new Event('resize'));
    if(view==='explore'&&hasRoute)requestAnimationFrame(()=>live.fitView());
  }
  for(const [name,id] of TABS)$(id).onclick=()=>showTab(name);
  for(const [,id] of TABS)$(id).onkeydown=event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const at=TABS.findIndex(([,tab])=>tab===id);
    const next=event.key==='Home'?0:event.key==='End'?TABS.length-1:(at+(event.key==='ArrowRight'?1:TABS.length-1))%TABS.length;
    showTab(TABS[next][0]);$(TABS[next][1]).focus();
  };
  window.StafferTabs={show:showTab};
  function invalidateRoute(message){clearRouteOptions();const active=hasRoute||routeController||offerShown;routeRequest++;routeController?.abort();routeController=null;$('route-submit').disabled=false;$('route-clear').textContent='Clear';if(hasRoute){live.clearRoute();$('route-hops').replaceChildren();hasRoute=false;}if(offerShown){$('route-hops').replaceChildren();offerShown=false;}if(active)$('route-status').textContent=message;}
  async function get(path,params,signal){const url=new URL(path,location.origin);for(const [k,v] of Object.entries(params))url.searchParams.set(k,String(v));const response=await fetch(url,{signal});const data=await response.json();if(!response.ok)throw Error(data.error||`Request failed (${response.status})`);return data;}
  function cancelSearch(){const id=activeSearch;activeSearch=null;if(id)fetch('/api/staffer-route-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,holder}),keepalive:true}).catch(()=>{});}
  function evidenceOptions(){return {include_historical:1,include_review:1,include_policy:0,include_unresolved:0,include_user_asserted:1,prefer_current:1};}
  function syncNetworkControls(){
    const ecosystem=$('route-model').value==='evidence_cost';
    $('work-routes').setAttribute('aria-pressed',String(!ecosystem));
    $('ecosystem-routes').setAttribute('aria-pressed',String(ecosystem));
    $('route-model-note').textContent=ecosystem?'Documented associations, including payroll and lobbying reports. Ranked by evidence costs; staffer exposure and uncertainty scores do not apply.':(($('work-sources').value==='directory'?'Directory employment only':'Payroll and directory employment')+', adjusted for staff size, shared roles and uncertainty about present access.');
    window.dispatchEvent(new CustomEvent('staffer-network-mode',{detail:{scope:ecosystem?'sources':'network'}}));
  }
  $('route-model').onchange=()=>{
    invalidateRoute('Connection network changed. Search again to update your route.');
    syncNetworkControls();
    syncRouteAddress();
  };
  function openRouteNetwork(model){
    if($('route-model').value!==model){$('route-model').value=model;$('route-model').onchange();}
    showTab('explore');setPanel(true);$('route-panel').open=true;$('person-panel').scrollTop=0;
    if(!hasRoute&&!routeController)$('route-status').textContent=model==='evidence_cost'?'Ecosystem routes include shared lobbying reports and official professional rosters. Choose endpoints, then find a connection.':'Staffer network selected. Choose endpoints with authenticated dated work overlap.';
    $('route-model').focus({preventScroll:true});
  }
  $('work-routes').onclick=()=>openRouteNetwork('work_time');
  $('ecosystem-routes').onclick=()=>openRouteNetwork('evidence_cost');
  function setMode(value){if(mode===value)return;mode=value;document.querySelectorAll('[name=destination]').forEach(r=>r.checked=r.value===mode);invalidateRoute(mode==='office'?'Choose an office to reach.':'Choose a person to reach.');endpoints.target=null;generations.target++;clearTimeout(timers.target);abortTypeahead('target');$('route-target').value='';delete $('route-target').dataset.nodeId;$('target-options').replaceChildren();$('route-target').setAttribute('aria-expanded','false');$('target-label').textContent=mode==='office'?'Office name':'Person’s name';$('route-target').placeholder='';$('office-help').hidden=mode!=='office';syncRouteAddress();}
  document.querySelectorAll('[name=destination]').forEach(r=>r.addEventListener('change',()=>{setMode(r.value);$('route-target').focus();}));
  function choose(which,node){const office=which==='target'&&mode==='office';if(office?node.kind!=='office':!live.isPerson(node))return;node=office?node:canonicalNode(node);if(endpoints[which]?.id!==node.id)invalidateRoute('Destination changed. Search again to update your route.');generations[which]++;clearTimeout(timers[which]);abortTypeahead(which);endpoints[which]=node;$('route-'+which).value=name(node);$(which+'-options').replaceChildren();$('route-'+which).setAttribute('aria-expanded','false');$('route-'+which).dataset.nodeId=node.id;syncRouteAddress();}
  async function matches(which,q,signal){const office=which==='target'&&mode==='office',key=(office?'office:':'person:')+q.toLowerCase();let data=cache.get(key);if(!data){data=await get(office?'/api/staffer-offices':'/api/staffer-search',{q,limit:20},signal);if(data.ready!==false)cache.set(key,data);if(cache.size>60)cache.delete(cache.keys().next().value);}return {...data,nodes:office?(data.nodes||[]):uniquePeople(data.nodes||[])};}
  async function suggest(which){const input=$('route-'+which),q=input.value.trim(),generation=++generations[which];abortTypeahead(which);endpoints[which]=null;delete input.dataset.nodeId;const out=$(which+'-options');out.replaceChildren();if(q.length<2)return;
    const controller=typeahead[which]=new AbortController();
    try{const data=await matches(which,q,controller.signal);if(generation!==generations[which])return;if(data.ready===false){el(out,'p','Loading records…','note');setTimeout(()=>{if(generation===generations[which])suggest(which);},2000);return;}input.setAttribute('aria-expanded',String(Boolean(data.nodes.length)));for(const node of data.nodes){const button=el(out,'button',name(node));button.type='button';el(button,'small',node.kind==='office'?`${node.staff_count} staff listed · records through ${node.records_through}`:node.source_summary||node.source||'Work records');if(node.kind!=='office')window.StafferContacts?.render(button,node,{links:false});button.onclick=()=>choose(which,node);}if(!data.nodes.length)el(out,'p','No matches. Try a full name.','note');}
    catch(error){if(error.name==='AbortError')return;if(generation===generations[which])el(out,'p',error.message,'note');}
    finally{if(typeahead[which]===controller)typeahead[which]=null;}
  }
  for(const which of ['origin','target']){$('route-'+which).addEventListener('input',()=>{invalidateRoute('Names changed. Search again to update your route.');endpoints[which]=null;delete $('route-'+which).dataset.nodeId;generations[which]++;clearTimeout(timers[which]);abortTypeahead(which);timers[which]=setTimeout(()=>suggest(which),180);syncRouteAddress();});}
  window.addEventListener('staffer-assertions-changed',()=>invalidateRoute('Your confirmed relationships changed. Search again to update your route.'));
  window.addEventListener('staffer-revision',()=>{invalidateRoute('Records changed. Search again to update your route.');cache.clear();if(selectedPersonId){selectedPersonId=live.canonicalId(selectedPersonId);window.dispatchEvent(new CustomEvent('staffer-select',{detail:{id:selectedPersonId}}));}for(const which of ['origin','target']){if(endpoints[which]&&endpoints[which].kind!=='office'){const id=live.canonicalId(endpoints[which].id);if(id!==endpoints[which].id)choose(which,live.node(id)||{...endpoints[which],id});}generations[which]++;clearTimeout(timers[which]);abortTypeahead(which);$(which+'-options').replaceChildren();$('route-'+which).setAttribute('aria-expanded','false');if(!endpoints[which]&&$('route-'+which).value.trim().length>=2)timers[which]=setTimeout(()=>suggest(which),180);}});
  function provenance(parent,record){const details=el(parent,'details','');el(details,'summary','View source details');let loaded=false;details.addEventListener('toggle',()=>{if(details.open&&!loaded){loaded=true;el(details,'pre',JSON.stringify(record,null,2));}});}
  function evidenceSummary(edge={}){
    const lastDays=(edge.workplaces||[]).map(w=>w.last_overlap_day).filter(Number.isFinite);
    if(lastDays.length){const last=new Date((Math.max(...lastDays)-719163)*86400000).toISOString().slice(0,10);return `Last ${edge.modeled_exposure?'modeled':'recorded'} overlap: ${last}. Employment intervals are retained in source details.`;}
    if(edge.evidence_summary)return edge.evidence_summary;
    if(edge.observed_period_count)return `${edge.observed_period_count} distinct shared reporting ${edge.observed_period_count===1?'period':'periods'} · ${edge.first_observed_period} to ${new Date(Date.parse(edge.last_observed_period_end_exclusive+'T00:00:00Z')-86400000).toISOString().slice(0,10)} · continuous work unverified`;
    const proofs=Array.isArray(edge.provenance)?edge.provenance:[edge.provenance||{}];
    if(edge.user_asserted&&proofs.some(p=>p.status==='active'&&!p.effective_until))return 'Coworkers you confirmed · since '+(edge.start||'the date you supplied');
    const end=edge.end_exclusive&&/^\d{4}-\d{2}-\d{2}$/.test(edge.end_exclusive)?new Date(Date.parse(edge.end_exclusive+'T00:00:00Z')-86400000).toISOString().slice(0,10):null;
    return edge.start?`${edge.start}${end?' to '+end:''}`:'Dates not recorded';
  }
  const relationship=edge=>edge.relationship_label?edge.relationship_label:edge.evidence_class==='shared_background_only'?'Shared background · work overlap unconfirmed':edge.evidence_class==='observed_report_core_overlap'?'Overlapping observed report periods · daily service unverified':edge.evidence_class==='work_presence_within_report'?'Work presence within a report window · duration unknown':edge.relationship==='shared_overlap'&&edge.work_evidence?relationship(edge.work_evidence):({current_coworker:'Same workplace in current evidence',overlapped_tenure:'Overlapping workplace records',co_reported_lobbying_activity:'Named in the same issue section',shared_policy_weak:'Share a policy interest',co_reported_firm_association:'Reported with the same firm'})[edge.relationship]||'Shared background';
  function evidenceLimitNotes(hop){
    const evidence=hop.work_evidence||hop,proofs=Array.isArray(evidence.provenance)?evidence.provenance:Object.values(evidence.provenance?.source_evidence||{}).flat().map(e=>e.provenance||{});
    const notes=[];
    if(hop.modeled_exposure)notes.push('Includes modeled payroll report intervals; daily coworking is unverified.');
    if(hop.work_sources?.length)notes.push('Employment sources: '+hop.work_sources.map(s=>({directory:'directory history',payroll_row:'payroll entries',payroll_trajectory:'payroll history'})[s]||s).join(', '));
    if(proofs.some(p=>/\bSHARED EMPLOYEE\b/i.test(p.stint?.title_last||'')))notes.push('Shared-employee role appears in a source stint summary; direct teamwork and the role on every overlap day are unverified.');
    if(proofs.some(p=>p.stint?.container_kind==='admin'))notes.push('Administrative-office overlap; direct teamwork is unverified.');
    return notes;
  }
  function renderRoute(data){data=live.sanitizeRoute(data);hasRoute=data.found!==false&&data.nodes?.length>0;const out=$('route-hops');out.replaceChildren();offerShown=false;if(data.found===false||!data.nodes?.length){live.clearRoute();$('route-status').textContent=data.status==='unknown_person'?'Choose a person from the suggestions.':data.status==='no_office_staff'?'No staff are available in the latest office records.':data.status==='unreachable'?(data.unreachable_reason&&data.message?data.message:'No route between these endpoints in '+($('route-model').value==='evidence_cost'?'Ecosystem':'Work & background')+'. This does not mean either person has no connections.'):data.message||'No connecting route in the available records.';const left=leftOutLine(data);if(left)el(out,'p',left,'note');if(['unreachable','insufficient_work_coverage'].includes(data.status)&&(data.objective||$('route-model').value)!=='evidence_cost')offerEcosystem(out,data);return;}
    if(!data.optimal){$('route-status').textContent='Still checking for the best route…';return;}
    live.showRoute(data);const nodes=new Map(data.nodes.map(n=>[String(n.id),n]));const path=data.path;const hops=data.hops?.length?data.hops:data.edges||[];
    const steps=Math.max(0,path.length-1);$('route-status').textContent=steps===0?'You are already at the destination.':`${({evidence_cost:'Ecosystem route',work_time:'Staffer route',overlap_fewest_steps:'Fewest steps',overlap_balanced:'Balanced route',overlap_strength:'Highest shared-background score'}[data.objective]||'Suggested route')}: ${steps} ${steps===1?'step':'steps'}.`;
    if(data.office){const card=el(out,'div','','office-result');el(card,'strong','Connection reaches: '+name(nodes.get(path.at(-1))));window.StafferContacts?.render(card,nodes.get(path.at(-1)));if(data.recommended_contact?.title)el(card,'p',data.recommended_contact.title,'note');el(card,'p',`${data.office.label} · compared ${data.candidate_count} staff`,'note');const left=leftOutLine(data);if(left)el(card,'p',left,'note');el(card,'p',`Staff listed in records through ${data.office.records_through}`,'note');provenance(card,{office:data.office,contact:data.recommended_contact});}
    const start=el(out,'div','','hop');el(start,'strong','Start: '+name(nodes.get(path[0])||endpoints.origin));window.StafferContacts?.render(start,nodes.get(path[0])||endpoints.origin);
    hops.forEach((hop,index)=>{const edge=hop.edge||hop,target=nodes.get(String(hop.target||path[index+1]));const card=el(out,'div','','hop');if(edge.via_organization)el(card,'p','Via client organization: '+edge.via_organization.label,'route-via');el(card,'strong',`${index+1}. ${name(target)}`);window.StafferContacts?.render(card,target);el(card,'p',relationship(edge)+(edge.employer_label&&!edge.user_asserted?' · '+edge.employer_label:''),'note');if(edge.overlap_duration_days||edge.model_interval_overlap_days)el(card,'p',`${Number(edge.overlap_duration_days||edge.model_interval_overlap_days).toLocaleString()} ${edge.modeled_exposure?'modeled exposure':'documented overlap'} days${data.objective==='work_time'?' · workplace-size adjusted':''}`,'note');if(edge.overlaps){for(const overlap of edge.overlaps)el(card,'p',overlap.label+': '+overlap.matches.map(m=>m.label).join('; '),'note');el(card,'p','Shared-background score: '+Number(edge.strength).toFixed(1)+' · personal familiarity unknown','note');if(edge.work_evidence)el(card,'p',evidenceSummary(edge.work_evidence),'note');}else el(card,'p',evidenceSummary(edge),'note');for(const note of evidenceLimitNotes(edge))el(card,'p',note,'note');const button=el(card,'button','View person');button.type='button';button.onclick=()=>live.select(target?.id||path[index+1],true);if(index<hops.length-1||mode==='office'){const avoid=el(card,'button','Avoid this person');avoid.type='button';avoid.onclick=()=>{avoidedPeople.set(target.id,name(target));renderAvoidedPeople();invalidateRoute('Person excluded. Search again for another route.');syncRouteAddress();$('route-form').requestSubmit();};}provenance(card,hop);});
    if(data.objective==='work_time'&&Number.isFinite(data.uncertainty_factor)){
      el(out,'p',`Uncertainty adjustment: retains ${(100*data.uncertainty_factor).toFixed(1)}% of this path's structural score. This is a model discount, not a success probability.`,'note');
      el(out,'p','Present willingness is unverified. Older overlap and reliance on one workplace add uncertainty.','note');
      const dependence=data.dependence_on_best_route;
      if(data.route_rank>1&&dependence)el(out,'p',`Compared with the first route: ${dependence.shared_staff_edges} shared staff connections; ${dependence.shared_workplace_contexts} shared workplaces. Alternatives may rely on the same evidence.`,'note');
    }
    const dependency=data.dependence_on_previous_routes;
    if(data.route_rank>1&&dependency)el(out,'p',`Different contacts from earlier choices; ${dependency.shared_workplace_contexts} shared workplace or organization contexts. Different people do not establish independent access.`,'note');
    const map=el(out,'button','Show route on map');map.type='button';map.onclick=()=>{live.fitView();$('network').scrollIntoView({block:'start',behavior:'smooth'});};
    const details=el(out,'details','','route-methods');el(details,'summary','How this route was chosen');el(details,'p',data.explanation,'note');el(details,'p',`Route computed from the indexed records. Ready in ${((data.response_elapsed_ms??data.elapsed_ms??data.search_elapsed_ms)/1000).toFixed(2)} seconds.${data.cache_hit?' Reused a cached result.':''}`,'note');for(const warning of data.warnings||[])el(details,'p',warning,'note');
    if(matchMedia('(max-width:720px)').matches)$('route-status').scrollIntoView({block:'start',behavior:'smooth'});
  }
  // An office search under Staffer leaves out the current staff who lack the coverage the model requires (no payroll record and not an operational staffer; a person with dated directory work can be one) and searches to the rest (D-086); the answer counts them in uncovered_staff_count and says in uncovered_basis which coverage they lack: payroll_or_roster, or roster where the comparison reads directory episodes only (work_sources=directory has no payroll evidence to cover anyone), and the line names that coverage. Any other basis, or none (an older server), gets the payroll-or-roster wording. One plain line with the result, only when there are some.
  function leftOutLine(data){
    // No search ran when the origin is the person without coverage (the answer names it among unavailable_person_ids), so nobody was left out of one: the server still counts the staff, and the page does not say they were left out.
    if(data.status==='insufficient_work_coverage'&&Array.isArray(data.unavailable_person_ids)&&data.unavailable_person_ids.includes(data.origin))return '';
    const left=Number(data.uncovered_staff_count);
    const coverage=data.uncovered_basis==='roster'?'staffer-roster':'payroll or staffer-roster';
    return left>0?`${left} of this office's current staff ${left===1?'lacks':'lack'} the ${coverage} coverage this model requires and ${left===1?'was':'were'} left out.`:'';
  }
  // After a Staffer search found no route: one button that runs the same search under Ecosystem. The server names that objective (suggested_objective); the selector must offer it, else it is the Ecosystem value this page has always used. The click is a normal new search (same endpoints, same holder, supersedes whatever is still active), so it goes through the form.
  function offerEcosystem(out,data){
    const objective=[...($('route-model').options||[])].some(o=>o.value===data.suggested_objective)?data.suggested_objective:'evidence_cost';
    const retry=el(out,'button','Search Ecosystem');retry.type='button';
    retry.onclick=()=>{openRouteNetwork(objective);$('route-form').requestSubmit();};
    offerShown=true;
  }
  function clearRouteOptions(){
    const out=$('route-options');out.replaceChildren();out.hidden=true;
  }
  function renderRouteOptions(data){
    clearRouteOptions();
    const routes=(Array.isArray(data.routes)?data.routes:[data]).filter(route=>route.found!==false&&route.nodes?.length);
    if(!routes.length){renderRoute(data);return;}
    const out=$('route-options');out.hidden=false;
    el(out,'p',avoidedPeople.size?'People excluded from this search: '+[...avoidedPeople.values()].join(', ')+'.':'No people excluded from this search.','note');
    const requested=Number(data.routes_requested||1);
    const distinct=['distinct','independent'].includes(data.alternative_strategy);
    el(out,'p',`${routes.length} route${routes.length===1?'':'s'} found${distinct?' with different contacts':''}${routes.length<requested?(distinct?' · no further route under the exclusions from these choices':' · fewer than requested in this search'):''}. Choose one to view its map and evidence.`,'note');
    if(data.alternative_strategy==='independent')el(out,'p','Also excludes repeated recorded workplace or organization evidence. Shared endpoints and missing evidence still limit independence.','note');
    const buttons=[];
    function selectRoute(index){
      buttons.forEach((button,i)=>button.setAttribute('aria-pressed',String(i===index)));
      renderRoute({...routes[index],response_elapsed_ms:data.response_elapsed_ms});
      if(distinct&&index>0){
        const prior=[...new Set(routes.slice(0,index).flatMap(r=>r.nodes.slice(1,r.office?undefined:-1).map(name)))];
        el($('route-hops'),'p','To find this alternative, the search skipped contacts used in earlier choices: '+prior.join(', ')+'. This applies only to this alternative, including records with the same displayed name.','note');
        if(data.alternative_strategy==='independent')el($('route-hops'),'p','It also skipped the recorded workplaces and organizations supporting earlier choices. See their source details for the evidence.','note');
      }
      $('route-status').textContent=`Route ${index+1} of ${routes.length} · `+$('route-status').textContent;
    }
    routes.forEach((route,index)=>{
      const button=el(out,'button','','route-option');button.type='button';
      button.setAttribute('aria-controls','route-hops');
      el(button,'strong',`Route ${index+1}${index===0?' · Best ranked':distinct?' · Different contacts':''} · ${route.steps} ${route.steps===1?'step':'steps'}`);
      el(button,'small',route.nodes.map(name).join(' → '));
      button.onclick=()=>selectRoute(index);buttons.push(button);
    });
    selectRoute(0);
  }
  $('route-count').onchange=()=>{invalidateRoute('Route count changed. Search again to compare options.');syncRouteAddress();};
  $('work-sources').onchange=()=>{invalidateRoute('Employment evidence changed. Search again to compare.');syncNetworkControls();syncRouteAddress();};
  $('alternative-strategy').onchange=()=>{invalidateRoute('Alternative strategy changed. Search again to compare options.');syncRouteAddress();};
  function renderAvoidedPeople(){
    const out=$('avoided-people');out.replaceChildren();
    const count=avoidedPeople.size;
    $('exclusion-status').textContent=count?`${count} ${count===1?'person':'people'} excluded · review`:'No people excluded';
    $('exclusion-status').title=count?'Excluded from this search: '+[...avoidedPeople.values()].join(', '):'No person exclusions are active.';
    el(out,'p',count?'Excluded from this search, including records with the same displayed name:':'No people excluded from this search.','note');
    for(const [id,label] of avoidedPeople){const button=el(out,'button','Avoiding '+label+' · remove');button.type='button';button.title=id;button.onclick=()=>{avoidedPeople.delete(id);renderAvoidedPeople();invalidateRoute('Exclusion removed. Search again to update routes.');syncRouteAddress();};}
    if(count){const clear=el(out,'button','Clear all person exclusions');clear.type='button';clear.onclick=()=>{clearPersonExclusions();invalidateRoute('All person exclusions cleared. Search again to update routes.');};}
  }
  function clearPersonExclusions(){avoidedPeople.clear();renderAvoidedPeople();syncRouteAddress();}
  $('exclusion-status').onclick=()=>{showTab('explore');setPanel(true);$('route-panel').open=true;$('avoided-people').scrollIntoView({block:'center',behavior:'smooth'});};
  const normalizedName=value=>String(value||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().match(/[a-z0-9]+/g)||[];
  async function resolveEndpoint(which){const input=$('route-'+which),q=input.value.trim();if(endpoints[which]&&name(endpoints[which])===q)return true;const generation=generations[which];try{const data=await matches(which,q);if(q!==input.value.trim()||generation!==generations[which])return false;if(data.ready===false){$('route-status').textContent='Records are loading. Try again shortly.';return false;}const terms=normalizedName(q),exact=data.nodes.filter(n=>[name(n),...(n.aliases||[])].some(value=>normalizedName(value).join(' ')===terms.join(' ')));let candidates=exact.length?exact:data.nodes.filter(n=>{return [name(n),...(n.aliases||[])].some(value=>{const words=normalizedName(value);return terms.length>=2&&terms.every(t=>words.some(w=>w.startsWith(t)));});});if(candidates.length===1){choose(which,candidates[0]);return true;}await suggest(which);$('route-status').textContent=data.nodes.length?'Choose a match from the suggestions.':'No matches. Check the name.';return false;}catch(error){$('route-status').textContent=error.message;return false;}}
  $('route-form').addEventListener('submit',async event=>{event.preventDefault();for(const which of ['origin','target']){clearTimeout(timers[which]);generations[which]++;abortTypeahead(which);}if(!endpoints.origin||!endpoints.target){$('route-status').textContent='Finding names…';const resolved=await Promise.all(['origin','target'].map(resolveEndpoint));if(!resolved.every(Boolean)||!endpoints.origin||!endpoints.target)return;}invalidateRoute('Finding route options…');const request=++routeRequest;routeController?.abort();routeController=new AbortController();const signal=routeController.signal;const supersedes=activeSearch;activeSearch=null;let shown=false;$('route-submit').disabled=true;$('route-clear').textContent='Cancel search';$('route-status').textContent='Finding the best route…';const started=performance.now();try{
      const office=mode==='office',endpoint=office?'/api/staffer-office-route':'/api/staffer-route',params={origin:endpoints.origin.id,[office?'office':'target']:endpoints.target.id,...evidenceOptions(),objective:$('route-model').value,route_count:$('route-count').value,work_sources:$('work-sources').value,alternative_strategy:$('alternative-strategy').value,avoid_people:[...avoidedPeople.keys()].join(','),async:1,holder};let data=await get(endpoint,supersedes?{...params,supersedes}:params,signal);
      while(data.ready===false||['searching','queued','search_not_found'].includes(data.status)){
        if(request!==routeRequest)return;
        if(data.search_id)activeSearch=data.search_id;
        if(!shown&&data.partial_routes?.length){shown=true;renderRoute({...data.partial_routes[0],response_elapsed_ms:performance.now()-started});}
        const progress=data.progress||{},seconds=Math.floor((performance.now()-started)/1000);$('route-status').textContent=data.status==='queued'?'Your search is queued and will continue automatically.':`${shown?'Route 1 found · still finding alternatives':progress.routes_found?'Comparing route options · '+progress.routes_found+' found':'Finding the best route…'} · ${seconds}s${progress.expanded?' · '+Number(progress.expanded).toLocaleString()+' people checked':''}${progress.best_steps_so_far!==null&&progress.best_steps_so_far!==undefined?' · checking a '+progress.best_steps_so_far+' step route':''}`;
        await new Promise(resolve=>setTimeout(resolve,performance.now()-started<1500?200:500));if(signal.aborted)return;
        try{data=data.ready===false||data.status==='search_not_found'?await get(endpoint,params,signal):await get('/api/staffer-route-result',{id:data.search_id},signal);}catch(error){if(signal.aborted)return;$('route-status').textContent='Reconnecting to your search…';await new Promise(resolve=>setTimeout(resolve,1500));}
      }
      if(request!==routeRequest)return;activeSearch=null;
      if(data.status==='cancelled'){clearRouteOptions();live.clearRoute();$('route-hops').replaceChildren();hasRoute=false;$('route-status').textContent='Search cancelled. Search again to find a route.';return;}
      if(data.status==='search_error'||data.status==='search_not_found')throw Error(data.error||'The search service restarted. Run the search again.');if(data.ready===false){$('route-status').textContent='Records are loading. Try again shortly.';return;}data.response_elapsed_ms=performance.now()-started;renderRouteOptions(data);$('route-status').dataset.elapsedMs=String(Math.round(performance.now()-started));
    }catch(error){if(error.name!=='AbortError'&&request===routeRequest)$('route-status').textContent=error.message;}finally{if(request===routeRequest){$('route-submit').disabled=false;$('route-clear').textContent='Clear';routeController=null;}}});
  $('route-clear').onclick=()=>{cancelSearch();clearRouteOptions();clearPersonExclusions();hasRoute=false;offerShown=false;routeRequest++;routeController?.abort();routeController=null;$('route-submit').disabled=false;$('route-clear').textContent='Clear';live.clearRoute();$('route-hops').replaceChildren();$('route-status').textContent='Route and person exclusions cleared. Choose a destination to find another route.';};
  // Closing the tab lets go of the running search at once (keepalive lets the request outlive the page), so it stops when no other tab still holds it; the server's sweeper is only the fallback.
  window.addEventListener('pagehide',()=>cancelSearch());
  async function openTarget(id){
    id=live.canonicalId(String(id||'').trim());if(!id)return false;
    setMode('person');showTab('explore');$('route-panel').open=true;$('route-status').textContent='Preparing this person as the destination…';
    try{
      const data=await get('/api/staffer-person',{id,...evidenceOptions()});
      if(data.ready===false){$('route-status').textContent='People records are still loading. Try again shortly.';return false;}
      if(!data.node){$('route-status').textContent='This directory record is available as source evidence, but path finding awaits a linked People profile.';return false;}
      choose('target',data.node);$('route-status').textContent=routeReadyMessage();$(endpoints.origin?'route-submit':'route-origin').focus();return true;
    }catch(error){$('route-status').textContent=error.message;return false;}
  }
  window.StafferRoute={openTarget};
  async function loadPerson(id,coworkerOffset=0,collaboratorOffset=0,paging=false){
    const request=++personRequest;
    const changedPerson=selectedPersonId!==(id||null);
    selectedPersonId=id||null;
    if(changedPerson){$('coworkers').replaceChildren();$('reported-collaborators').replaceChildren();}
    $('coworker-panel').hidden=!id;if(!id)return;
    if(changedPerson)revealDetail();
    $('coworker-status').textContent=changedPerson?'Loading work connections…':'Refreshing work connections…';
    try{
      const data=await get('/api/staffer-person',{id,...evidenceOptions(),coworker_offset:coworkerOffset,collaborator_offset:collaboratorOffset});
      if(request!==personRequest)return;
      if(data.ready===false){$('coworker-status').textContent='Loading: '+(data.stage||'evidence');setTimeout(()=>{if(request===personRequest)loadPerson(id,coworkerOffset,collaboratorOffset,paging);},2000);return;}
      if(data.node&&!paging){live.showPerson(data.node,data.edges);window.dispatchEvent(new CustomEvent('staffer-person-ready',{detail:{id}}));}
      if(data.node)live.showNeighborhood(data);
      const limited=data.search_limited||data.reported_collaborators_search_limited;
      const coworkerRows=(data.coworkers||[]).filter(r=>live.isPerson(r.node||r.person||r));
      const reportedRows=(data.reported_collaborators||[]).filter(r=>live.isPerson(r.node||r.person||r));
      $('coworker-status').textContent=`Connections found in indexed records: ${data.total_coworkers??data.coworker_total??coworkerRows.length} people in the same workplace at the same time + ${data.reported_collaborator_total??reportedRows.length} people connected through documented ecosystem evidence. This is not a complete count of their professional connections.${limited?' Search reached its limit; additional connections may exist.':''} `;
      function renderRows(records,parentId,pagination,isReported){
        const out=$(parentId);
        out.replaceChildren();
        for(const record of records){
          const node=record.node||record.person||record,edge=record.edge||record,relationship=record.relationship||edge.relationship||'Observed connection';
          const row=el(out,'div','','coworker-row'),button=el(row,'button',name(node),'result');
          el(button,'small',[edge.relationship_label,edge.via_organization?'Via '+edge.via_organization.label:null,evidenceSummary({...edge,...record})].filter(Boolean).join(' · '));
          window.StafferContacts?.render(row,node);button.onclick=()=>{live.addNodes([node]);live.select(node.id,true);};provenance(row,{...edge,...record});
          const route=el(row,'button','Find a route','coworker-route');route.type='button';
          route.onclick=()=>{setMode('person');if(isReported)openRouteNetwork('evidence_cost');else openRouteNetwork($('route-model').value);if(data.node)choose('origin',data.node);choose('target',node);$('route-form').requestSubmit();};
        }
        if(!pagination||pagination.search_limited)return;
        const offset=pagination.offset||0,limit=pagination.limit||100,total=pagination.total||0;
        if(total)el(out,'p',`${offset+1}–${Math.min(offset+records.length,total)} of ${Number(total).toLocaleString()}`,'note');
        if(offset||pagination.has_more){
          const nav=el(out,'div','','pagination'),prev=el(nav,'button','Previous '+(isReported?'collaborators':'people')),next=el(nav,'button','Next '+(isReported?'collaborators':'people'));
          prev.type=next.type='button';prev.disabled=offset===0;next.disabled=!pagination.has_more;
          const change=nextOffset=>loadPerson(id,isReported?coworkerOffset:nextOffset,isReported?nextOffset:collaboratorOffset,parentId);
          prev.onclick=()=>change(Math.max(0,offset-limit));next.onclick=()=>change(offset+limit);
        }
      }
      renderRows(coworkerRows,'coworkers',data.pagination?.coworkers,false);
      renderRows(reportedRows,'reported-collaborators',data.pagination?.reported_collaborators,true);
      if(paging){const list=$(paging);list.scrollTop=0;list.tabIndex=-1;list.setAttribute('role','region');list.setAttribute('aria-label',paging==='coworkers'?'Dated people in the same workplace at the same time':'Reported collaborators');list.focus({preventScroll:true});list.scrollIntoView({block:'start'});}
      if(!coworkerRows.length&&!reportedRows.length)$('coworker-status').textContent=limited?'Workplace-overlap search reached its effort limit; absence is not established.':'No workplace overlap found in the available records.';
    }catch(error){if(request===personRequest){$('coworker-status').textContent=error.message;const retry=el($('coworkers'),'button','Retry connections');retry.onclick=()=>loadPerson(id,coworkerOffset,collaboratorOffset,paging);}}
  }
  window.addEventListener('staffer-select',event=>loadPerson(event.detail?.id));
  window.addEventListener('staffer-source-selected',revealDetail);
  const routeQuery=new URLSearchParams(location.search),officeContext=routeQuery.get('office'),routeTarget=routeQuery.get('route_target');
  if(routeQuery.get('route_model')==='evidence_cost'){
    $('route-model').value='evidence_cost';
  }
  if(['1','3','5'].includes(routeQuery.get('route_count')))$('route-count').value=routeQuery.get('route_count');
  if(['distinct','independent','ranked'].includes(routeQuery.get('alternative_strategy')))$('alternative-strategy').value=routeQuery.get('alternative_strategy');
  for(const id of (routeQuery.get('avoid_people')||'').split(',').filter(Boolean)){
    avoidedPeople.set(id,id+' (from saved link; loading name)');
    get('/api/staffer-person',{id,...evidenceOptions()}).then(data=>{if(data.node&&avoidedPeople.has(id)){avoidedPeople.set(id,name(data.node));renderAvoidedPeople();}}).catch(()=>{});
  }
  renderAvoidedPeople();
  if(['directory','payroll_and_directory'].includes(routeQuery.get('work_sources')))$('work-sources').value=routeQuery.get('work_sources');
  syncNetworkControls();
  const originContext=routeQuery.get('route_origin');
  if(originContext)get('/api/staffer-person',{id:originContext,...evidenceOptions()}).then(data=>{if(data.node){choose('origin',data.node);$('route-status').textContent=routeReadyMessage();}}).catch(()=>{$('route-status').textContent='Could not restore the starting person. Choose a name to retry.';});
  if(officeContext){setMode('office');$('route-target').value=officeContext;timers.target=setTimeout(()=>suggest('target'),180);}
  if(routeTarget)setTimeout(()=>openTarget(routeTarget),0);
  else if(routeQuery.get('source_only')==='1')$('route-status').textContent='This directory record is open as source evidence. Path finding becomes available when it has one guarded People match.';
})();
