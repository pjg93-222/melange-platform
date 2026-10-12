const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-route.js'),'utf8');
test('both networks keep shared policy and unresolved identities out',()=>{
  const selector={value:'work_time'},context={$:()=>selector};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function evidenceOptions()'),source.indexOf("  $('route-model').onchange")),context);
  assert.equal(context.evidenceOptions().include_policy,0);
  selector.value='evidence_cost';
  assert.equal(context.evidenceOptions().include_policy,0);
  assert.equal(context.evidenceOptions().include_unresolved,0);
  assert.equal(context.evidenceOptions().include_historical,1);
  assert.equal(context.evidenceOptions().include_user_asserted,1);
});
test('saved route state replaces old destinations and retains selected profile and model',()=>{
  let saved;
  const context={avoidedPeople:new Map(),URL,location:{href:'http://localhost/staffer-live/?embedded=1&person=selected&route_target=old'},
    endpoints:{origin:{id:'start'},target:{id:'end',label:'End'}},mode:'person',name:n=>n.label,
    $:()=>({value:'evidence_cost'}),history:{replaceState(a,b,url){saved=new URL(url,'http://localhost');}},
    window:{dispatchEvent(){}},CustomEvent:class{}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function syncRouteAddress()'),source.indexOf('  function setPanel(')),context);
  context.syncRouteAddress();
  assert.equal(saved.searchParams.get('person'),'selected');
  assert.equal(saved.searchParams.get('route_target'),'end');
  assert.equal(saved.searchParams.get('route_origin'),'start');
  assert.equal(saved.searchParams.get('route_model'),'evidence_cost');
  context.avoidedPeople.set('unavailable','Unavailable person');
  context.syncRouteAddress();
  assert.equal(saved.searchParams.get('avoid_people'),'unavailable');
  context.avoidedPeople.clear();context.syncRouteAddress();
  assert.equal(saved.searchParams.has('avoid_people'),false);
  context.mode='office';context.endpoints.target={id:'office:1',label:'Example office'};
  context.syncRouteAddress();
  assert.equal(saved.searchParams.get('route_target'),null);
  assert.equal(saved.searchParams.get('office'),'Example office');
});

test('restored route status reflects both available endpoints',()=>{
  const context={endpoints:{origin:null,target:null}};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function routeReadyMessage()'),source.indexOf('  function setPanel(')),context);
  assert.match(context.routeReadyMessage(),/starting person and destination/i);
  context.endpoints.origin={id:'start'};
  assert.match(context.routeReadyMessage(),/Starting person ready/);
  context.endpoints.target={id:'end'};
  assert.match(context.routeReadyMessage(),/destination restored/);
});

test('toolbar ecosystem control opens a hidden pathfinder and keeps both selectors in sync',()=>{
  const elements={};
  const $=id=>elements[id]??=( {value:'work_time',attributes:{},setAttribute(k,v){this.attributes[k]=v;},focus(){this.focused=true;}} );
  let panelOpen=false,invalidations=0,addressUpdates=0;
  const context={$,hasRoute:false,routeController:null,invalidateRoute(){invalidations++;},syncRouteAddress(){addressUpdates++;},showTab(){},setPanel(open){panelOpen=open;},CustomEvent:class{},window:{dispatchEvent(){}}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function syncNetworkControls()'),source.indexOf('  function setMode(')),context);
  $('person-panel').scrollTop=800;
  $('ecosystem-routes').onclick();
  assert.equal($('route-model').value,'evidence_cost');
  assert.equal($('ecosystem-routes').attributes['aria-pressed'],'true');
  assert.equal($('work-routes').attributes['aria-pressed'],'false');
  assert.equal(panelOpen,true);assert.equal($('route-panel').open,true);
  assert.equal($('person-panel').scrollTop,0);
  assert.equal(invalidations,1);assert.equal(addressUpdates,1);
  $('route-model').value='work_time';$('route-model').onchange();
  assert.equal($('work-routes').attributes['aria-pressed'],'true');
  assert.equal($('ecosystem-routes').attributes['aria-pressed'],'false');
  assert.match($('route-model-note').textContent,/uncertainty/);
});

test('an unreachable work route offers an explicit ecosystem retry without claiming isolation',()=>{
  const elements={},$=id=>elements[id]??=({textContent:'',value:'overlap_balanced',replaceChildren(){},requestSubmit(){this.submitted=true;}});
  let retry,selectedModel;
  const context={$,live:{sanitizeRoute:d=>d,clearRoute(){}},el(parent,tag,text){retry={text};return retry;},openRouteNetwork(model){selectedModel=model;}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function renderRoute('),source.indexOf('  const normalizedName=')),context);
  vm.runInContext("renderRoute({found:false,status:'unreachable',message:'No available routes'})",context);
  assert.match($('route-status').textContent,/does not mean either person has no connections/);
  assert.equal(retry.text,'Search Ecosystem');retry.onclick();
  assert.equal(selectedModel,'evidence_cost');assert.equal($('route-form').submitted,true);
  vm.runInContext("renderRoute({found:false,status:'insufficient_work_coverage',message:'Known person; insufficient authenticated coverage'})",context);
  assert.match($('route-status').textContent,/Known person/);
  assert.equal(retry.text,'Search Ecosystem');
});

test('ecosystem evidence preserves its relationship class and counts observed periods as periods',()=>{
  const context={Date};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function evidenceSummary('),source.indexOf('  function renderRoute(')),context);
  assert.equal(vm.runInContext("relationship({relationship_label:'Same organization and quarterly filing'})",context),'Same organization and quarterly filing');
  const summary=vm.runInContext("evidenceSummary({observed_period_count:21,first_observed_period:'2021-01-01',last_observed_period_end_exclusive:'2026-04-01'})",context);
  assert.match(summary,/21 distinct shared reporting periods/);
  assert.match(summary,/continuous work unverified/);
  assert.match(summary,/2026-03-31/);
  const work=context.evidenceSummary({workplaces:[{last_overlap_day:736861}]});
  assert.match(work,/Last recorded overlap: 2018-06-16/);
  assert.doesNotMatch(work,/Dates not recorded/);
  assert.match(context.evidenceSummary({modeled_exposure:true,workplaces:[{last_overlap_day:736861}]}),/Last modeled overlap/);
});

test('source-summary role warnings distinguish shared employment and administrative overlap',()=>{
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function evidenceLimitNotes('),source.indexOf('  function renderRoute(')),context);
  const notes=context.evidenceLimitNotes({provenance:[{stint:{title_last:'SHARED EMPLOYEE',container_kind:'member_office'}}]});
  assert.equal(notes.length,1);assert.match(notes[0],/source stint summary/);
  assert.match(context.evidenceLimitNotes({provenance:[{stint:{title_last:'DIRECTOR',container_kind:'admin'}}]})[0],/Administrative-office/);
  assert.equal(context.evidenceLimitNotes({provenance:{index:'filing'}}).length,0);
  assert.equal(context.evidenceLimitNotes({provenance:[{stint:{title_last:'LEGISLATIVE DIRECTOR',container_kind:'member_office'}}]}).length,0);
});


test('route choices update map evidence and pressed state, and clear together',()=>{
  const elements={};
  const $=id=>elements[id]??=({children:[],textContent:'',replaceChildren(){this.children=[];}});
  const el=(parent,tag,text)=>{const element={tag,text,children:[],attrs:{},setAttribute(k,v){this.attrs[k]=v;}};parent.children.push(element);return element;};
  let shown;
  const context={$,el,avoidedPeople:new Map(),name:n=>n.label,renderRoute(route){shown=route;$('route-status').textContent='shown';}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function clearRouteOptions('),source.indexOf("  $('route-count').onchange")),context);
  const routes=[{found:true,steps:2,nodes:[{id:'a',label:'A'},{id:'b',label:'B'},{id:'d',label:'D'}]},
    {found:true,steps:2,nodes:[{id:'a',label:'A'},{id:'c',label:'C'},{id:'d',label:'D'}]}];
  context.renderRouteOptions({routes,routes_requested:3,alternative_strategy:'distinct'});
  assert.equal($('route-options').children[0].text,'No people excluded from this search.');
  assert.match($('route-options').children[1].text,/no further route under the exclusions/);
  assert.doesNotMatch($('route-options').children[1].text,/fewer than requested exist/);
  const buttons=$('route-options').children.filter(e=>e.tag==='button');
  assert.equal(buttons.length,2);assert.equal(buttons[0].attrs['aria-pressed'],'true');
  assert.equal(shown.nodes[1].id,'b');buttons[1].onclick();
  assert.equal(shown.nodes[1].id,'c');assert.equal(buttons[1].attrs['aria-pressed'],'true');
  assert.equal(buttons[0].attrs['aria-pressed'],'false');
  assert.match($('route-status').textContent,/Route 2 of 2/);
  assert.match($('route-hops').children.at(-1).text,/skipped contacts used in earlier choices: B/);
  context.clearRouteOptions();assert.equal($('route-options').hidden,true);
  assert.equal($('route-options').children.length,0);
});

test('person exclusions stay visible, removable and are cleared from saved state',()=>{
  const elements={},$=id=>elements[id]??=({children:[],replaceChildren(){this.children=[];}});
  const el=(parent,tag,text)=>{const child={tag,text};parent.children.push(child);return child;};
  const avoidedPeople=new Map([['darner','Michael P Darner']]);let saved;
  const context={$,el,avoidedPeople,invalidateRoute(){},syncRouteAddress(){saved=[...avoidedPeople.keys()];}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function renderAvoidedPeople()'),source.indexOf("  $('exclusion-status').onclick")),context);
  context.renderAvoidedPeople();
  assert.equal($('exclusion-status').textContent,'1 person excluded · review');
  assert.match($('exclusion-status').title,/Michael P Darner/);
  const remove=$('avoided-people').children.find(e=>e.text==='Avoiding Michael P Darner · remove');
  remove.onclick();assert.equal(avoidedPeople.size,0);assert.deepEqual(saved,[]);
  assert.equal($('exclusion-status').textContent,'No people excluded');
  avoidedPeople.set('a','A');avoidedPeople.set('b','B');context.renderAvoidedPeople();
  $('avoided-people').children.find(e=>e.text==='Clear all person exclusions').onclick();
  assert.equal(avoidedPeople.size,0);assert.deepEqual(saved,[]);
});
