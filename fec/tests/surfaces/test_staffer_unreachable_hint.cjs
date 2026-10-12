// A Staffer search that finds no route: what the page shows, and what its one button does (Paul's ruling, 2026-10-03).
// The real staffer-route.js runs in a vm over a small fake DOM and a scripted server. Every payload comes from
// staffer_unreachable_hint_contract.cjs, which test_staffer_unreachable_hint.py asserts against the real producer, so the
// words this page shows are the words the server sends. The page as it was before the change (read from git) and eight
// deliberate bugs of the new page are each caught by the same scenarios: the negative controls.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const contract=require('./staffer_unreachable_hint_contract.cjs');
const REL='fec/surfaces/staffer_live/staffer-route.js';
const ROOT=path.resolve(__dirname,'../../..');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-route.js'),'utf8');

// The page as it was before the change: the tip this work was built on (the search-ids follow-up, f23a40aee).
const BEFORE='f23a40aee4c749bae9db132599a853a52c81d9bf';
function fromGit(rev,rel){
  try{return execFileSync('git',['-C',ROOT,'show',rev+':'+rel],{encoding:'utf8',stdio:['ignore','pipe','ignore'],maxBuffer:1<<25});}catch(error){return null;}
}
const before=fromGit(BEFORE,REL);
const NO_GIT=before===null?'the pre-change page (commit '+BEFORE.slice(0,9)+') is not in this checkout':false;

const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
// How many ticks a scenario waits for something to happen. The controls wait briefly: a page with a bug is expected not to get there.
let patience=400;
async function until(predicate,what){for(let i=0;i<patience;i++){if(predicate())return;await tick();}assert.fail('timed out waiting for '+what);}
const person=(id,label)=>({id,label,kind:'person'});
const OFFICE={id:'office:example',kind:'office',label:'Example Office',staff_count:3,records_through:'2026-09-01'};
const LEGACY='No route between these endpoints in Work & background. This does not mean either person has no connections.';
const searching=id=>({ready:true,status:'searching',search_id:id,progress:{expanded:5,neighbor_checks:9,elapsed_ms:30},routes_requested:3});
// What the service sends for a search that found nothing: the contract's fields on top of the fields every such answer has.
const answer=(payload,id='S1')=>({ready:true,found:false,optimal:true,nodes:[],path:[],hops:[],edges:[],routes:[],graph:{nodes:[],edges:[]},
  warnings:[],explanation:'x',elapsed_ms:3,search_id:id,...payload});
const foundAnswer=(id='S1')=>{
  const ids=['origin1','target1'],hop={source:'origin1',target:'target1',relationship:'work_time_overlap',relationship_label:'Dated workplace overlap',cost:1,overlap_duration_days:90,provenance:{}};
  return {ready:true,found:true,status:'found',optimal:true,objective:'work_time',steps:1,nodes:ids.map(i=>person(i,'Name '+i)),path:ids,hops:[hop],edges:[hop],
    routes:[],explanation:'x',warnings:[],elapsed_ms:3,search_id:id};
};

// A page with just enough DOM. `server(call)` answers every request; `models` are the options of the network selector.
function boot(code,server,{search='?route_origin=origin1&route_target=target1',models=['work_time','evidence_cost']}={}){
  const elements={},created=[],calls=[],statusLog=[];
  const defaults={'route-model':'work_time','route-count':'3','work-sources':'payroll_and_directory','alternative-strategy':'distinct'};
  const make=id=>{
    const e={id,children:[],dataset:{},attributes:{},listeners:{},value:defaults[id]??'',hidden:false,open:false,scrollTop:0,title:'',disabled:false,
      classList:{toggle(){},add(){},remove(){}},style:{},
      setAttribute(k,v){e.attributes[k]=String(v);},getAttribute(k){return e.attributes[k]??null;},
      addEventListener(type,fn){(e.listeners[type]??=[]).push(fn);},
      append(...nodes){e.children.push(...nodes);},replaceChildren(...nodes){e.children=nodes;},
      focus(){},scrollIntoView(){},getBoundingClientRect(){return {top:0};},
      requestSubmit(){e.requested=(e.requested||0)+1;for(const fn of e.listeners.submit||[])e.lastSubmit=fn({preventDefault(){}});}};
    if(id==='route-model')e.options=models.map(value=>({value}));
    let text='';
    if(id==='route-status')Object.defineProperty(e,'textContent',{get:()=>text,set:v=>{text=String(v);statusLog.push(text);}});
    else e.textContent='';
    return e;
  };
  const $=id=>elements[id]??=make(id);
  const live={canonicalId:id=>id,isPerson:n=>n.kind==='person',node:()=>null,sanitizeRoute:d=>d,showRoute(){},clearRoute(){},fitView(){},select(){},showPerson(){},showNeighborhood(){},addNodes(){}};
  const window={StafferLive:live,addEventListener(){},dispatchEvent(){return true;}};
  const document={getElementById:$,createElement:tag=>{const e=make(tag);e.tag=tag;created.push(e);return e;},
    querySelectorAll:()=>[],querySelector:()=>make('q'),body:{classList:{toggle(){},contains:()=>false}}};
  const aborted=signal=>{if(signal?.aborted){const error=new Error('aborted');error.name='AbortError';throw error;}};
  const fetch=async(input,init={})=>{
    const url=input instanceof URL?input:new URL(String(input),'http://localhost');
    const call={path:url.pathname,params:Object.fromEntries(url.searchParams),method:init.method||'GET',signal:init.signal,order:calls.length};
    calls.push(call);aborted(init.signal);
    const data=await server(call);
    await tick();aborted(init.signal);
    return {ok:true,status:200,json:async()=>data};
  };
  const context=vm.createContext({window,document,location:{href:'http://localhost/staffer-live/'+search,search,origin:'http://localhost',pathname:'/staffer-live/'},
    history:{replaceState(){}},fetch,setTimeout:fn=>setTimeout(fn,0),clearTimeout,URL,URLSearchParams,AbortController,performance,
    matchMedia:()=>({matches:false}),requestAnimationFrame:fn=>fn(),CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}},
    Event:class{constructor(type){this.type=type;}},console});
  vm.runInContext(code,context);
  const office=search.includes('office=');
  return {$,calls,statusLog,
    submit:()=>$('route-form').listeners.submit[0]({preventDefault(){}}),
    stop:()=>{try{$('route-clear').onclick();}catch(error){}},   // ends any search still polling
    starts:()=>calls.filter(c=>c.path==='/api/staffer-route'||c.path==='/api/staffer-office-route'),
    polls:id=>calls.filter(c=>c.path==='/api/staffer-route-result'&&(!id||c.params.id===id)),
    // the buttons on the page now: those in the route panel's result area, by what they say
    offered:()=>$('route-hops').children.filter(e=>e.tag==='button').map(e=>e.textContent),
    button:label=>$('route-hops').children.find(e=>e.tag==='button'&&e.textContent===label),
    ready:async()=>{
      await until(()=>$('route-origin').value==='Name origin1','the restored origin');
      if(office)await until(()=>$('target-options').children.length>0,'the office suggestions');
      else await until(()=>$('route-target').value==='Name target1','the restored target');
    }};
}

// Answers a page's requests: the person and office lookups, then one search per start. The first search finishes with
// `payload` on its first poll; a later search for the Ecosystem objective (the one the button starts) is still running
// when the scenario ends, and a later one for any other objective finishes the same way.
function server(payload){
  const starts=[];
  const fn=call=>{
    if(call.path==='/api/staffer-person')return {ready:true,node:person(call.params.id,'Name '+call.params.id)};
    if(call.path==='/api/staffer-offices')return {ready:true,nodes:[OFFICE]};
    if(call.path==='/api/staffer-search')return {ready:true,nodes:[]};
    if(call.path==='/api/staffer-route'||call.path==='/api/staffer-office-route'){starts.push(call);return searching('S'+starts.length);}
    const started=starts[Number(call.params.id.slice(1))-1];
    return started!==starts[0]&&started.params.objective==='evidence_cost'?searching(call.params.id):(typeof payload==='function'?payload():answer(payload,call.params.id));
  };
  fn.starts=starts;
  return fn;
}
const where=mode=>mode==='office'?'?route_origin=origin1&office=Example%20Office':'?route_origin=origin1&route_target=target1';

// ---- the scenarios. Each takes the page's code and `ok(condition, what)`: asserting for the real page, collecting for the controls.
async function shows(code,ok,c){
  const page=boot(code,server(c.payload),{search:where(c.mode)});
  try{
    await page.ready();await page.submit();
    ok(page.$('route-status').textContent===c.payload.message,c.name+': the status says '+JSON.stringify(page.$('route-status').textContent));
    ok(JSON.stringify(page.offered())===JSON.stringify(['Search Ecosystem']),c.name+': one button, Search Ecosystem; found '+JSON.stringify(page.offered()));
    ok(page.$('route-submit').disabled===false&&page.$('route-clear').textContent==='Clear',c.name+': the form is ready again');
  }finally{page.stop();}
}
async function searchesEcosystem(code,ok,c){
  const page=boot(code,server(c.payload),{search:where(c.mode)});
  try{
    await page.ready();await page.submit();
    const button=page.button('Search Ecosystem');
    ok(Boolean(button),c.name+': there is a Search Ecosystem button to press');
    if(!button)return;
    button.onclick();
    ok(page.$('route-model').value==='evidence_cost',c.name+': the network selector now says Ecosystem');
    ok(page.$('ecosystem-routes').attributes['aria-pressed']==='true'&&page.$('work-routes').attributes['aria-pressed']==='false',c.name+': the toolbar switch agrees');
    await until(()=>page.starts().length>=2,'the second search');
    const [first,second]=page.starts();
    ok(second.path===first.path,c.name+': same endpoint, '+first.path+' then '+second.path);
    ok(second.params.origin===first.params.origin,c.name+': same origin');
    const key=c.mode==='office'?'office':'target';
    ok(second.params[key]===first.params[key]&&Boolean(first.params[key]),c.name+': same '+key+' ('+first.params[key]+' then '+second.params[key]+')');
    ok(first.params.objective==='work_time'&&second.params.objective==='evidence_cost',c.name+': objectives '+first.params.objective+' then '+second.params.objective);
    ok(second.params.async==='1'&&second.params.route_count===first.params.route_count&&second.params.alternative_strategy===first.params.alternative_strategy,c.name+': the same search options');
    ok(typeof first.params.holder==='string'&&second.params.holder===first.params.holder,c.name+': the same page holder');
    ok(!('supersedes' in second.params),c.name+': nothing was running, so it supersedes nothing');
    ok(page.$('route-hops').children.every(e=>e.tag!=='button'),c.name+': the offer is gone once it is taken');
  }finally{page.stop();}
}
async function supersedesLikeAnyNewSearch(code,ok){
  const c=contract.cases.find(x=>x.mode==='person'&&x.payload.unreachable_reason);
  const run=server(c.payload),page=boot(code,run,{search:where('person')});
  try{
    await page.ready();await page.submit();
    page.button('Search Ecosystem')?.onclick();
    await until(()=>page.polls('S2').length>=1,'the Ecosystem search polling');
    page.$('route-model').value='work_time';   // the reader goes back to Staffer and searches again while it is still open
    const again=page.submit();
    await until(()=>page.starts().length>=3,'the third search');
    const third=page.starts()[2];
    ok(third.params.supersedes==='S2',c.name+': a new search supersedes the Ecosystem search still running, as any new search does ('+third.params.supersedes+')');
    ok(third.params.holder===page.starts()[0].params.holder,c.name+': under the same holder');
    page.stop();await again;
  }finally{page.stop();}
}
async function keepsTheEcosystemAnswerPlain(code,ok){
  const page=boot(code,server({status:'unreachable',objective:'evidence_cost'}),{search:where('person')+'&route_model=evidence_cost'});
  try{
    await page.ready();await page.submit();
    ok(page.$('route-status').textContent==='No route between these endpoints in Ecosystem. This does not mean either person has no connections.','Ecosystem: its own sentence, found '+JSON.stringify(page.$('route-status').textContent));
    ok(page.offered().length===0,'Ecosystem: no button, found '+JSON.stringify(page.offered()));
  }finally{page.stop();}
}
async function offersNothingForAFoundRoute(code,ok){
  const page=boot(code,server(()=>foundAnswer()),{search:where('person')});
  try{
    await page.ready();await page.submit();
    ok(/Staffer route: 1 step\./.test(page.$('route-status').textContent),'found: its own status, found '+JSON.stringify(page.$('route-status').textContent));
    ok(!page.offered().includes('Search Ecosystem'),'found: no Search Ecosystem button, found '+JSON.stringify(page.offered()));
  }finally{page.stop();}
}
async function fallsBackForAnAnswerWithoutAReason(code,ok){
  const page=boot(code,server({status:'unreachable',objective:'work_time',message:'No available routes'}),{search:where('person')});
  try{
    await page.ready();await page.submit();
    ok(page.$('route-status').textContent===LEGACY,'no reason: the page keeps its own sentence, found '+JSON.stringify(page.$('route-status').textContent));
    ok(JSON.stringify(page.offered())===JSON.stringify(['Search Ecosystem']),'no reason: the button is still offered, found '+JSON.stringify(page.offered()));
  }finally{page.stop();}
}
async function usesTheObjectiveTheServerNames(code,ok){
  const wanted=[['overlap_balanced','overlap_balanced'],['not_an_option','evidence_cost'],[undefined,'evidence_cost']];
  for(const [suggested,expected] of wanted){
    const payload={status:'unreachable',objective:'work_time',unreachable_reason:'no_path_in_work_records',message:'No Staffer route between these people in the work records this model uses.',...(suggested?{suggested_objective:suggested}:{})};
    const page=boot(code,server(payload),{search:where('person'),models:['work_time','evidence_cost','overlap_balanced']});
    try{
      await page.ready();await page.submit();
      page.button('Search Ecosystem')?.onclick();
      await until(()=>page.starts().length>=2,'the second search');
      ok(page.$('route-model').value===expected&&page.starts()[1].params.objective===expected,'suggested '+suggested+': searched '+page.starts()[1]?.params.objective+', wanted '+expected);
    }finally{page.stop();}
  }
}
async function clearsTheOfferWhenTheSearchChanges(code,ok){
  const c=contract.cases.find(x=>x.mode==='person'&&x.payload.unreachable_reason==='origin_without_work_connections');
  const page=boot(code,server(c.payload),{search:where('person')});
  try{
    await page.ready();await page.submit();
    ok(page.offered().length===1,'stale: the offer is up');
    page.$('route-origin').value='ab';page.$('route-origin').listeners.input[0]();   // the reader edits the starting name
    ok(page.offered().length===0,'stale: editing a name takes the offer away, found '+JSON.stringify(page.offered()));
    ok(page.$('route-status').textContent==='Names changed. Search again to update your route.','stale: and the status stops describing the old search, found '+JSON.stringify(page.$('route-status').textContent));
  }finally{page.stop();}
  const second=boot(code,server(c.payload),{search:where('person')});
  try{
    await second.ready();await second.submit();
    second.$('route-clear').onclick();
    ok(second.offered().length===0,'stale: Clear takes the offer away');
    second.$('route-origin').value='ab';second.$('route-origin').listeners.input[0]();
    ok(second.$('route-status').textContent.startsWith('Route and person exclusions cleared'),'stale: after Clear nothing is left to invalidate, found '+JSON.stringify(second.$('route-status').textContent));
  }finally{second.stop();}
}
const SCENARIOS=[
  ...contract.cases.filter(c=>c.mode&&(c.payload.status==='unreachable'||c.payload.status==='insufficient_work_coverage')).map(c=>[c.name+' is shown with its message and one button',(code,ok)=>shows(code,ok,c)]),
  ...contract.cases.map(c=>[c.name+': the button searches Ecosystem for the same endpoints, under the same holder',(code,ok)=>searchesEcosystem(code,ok,c)]),
  ['the Ecosystem search is superseded like any new search',supersedesLikeAnyNewSearch],
  ['an Ecosystem answer keeps its own sentence and has no button',keepsTheEcosystemAnswerPlain],
  ['a found route has no Search Ecosystem button',offersNothingForAFoundRoute],
  ['an answer without a reason keeps the page\'s own sentence and the button',fallsBackForAnAnswerWithoutAReason],
  ['the button switches to the objective the server names, when the page offers it',usesTheObjectiveTheServerNames],
  ['the offer goes when the search changes, as a drawn route does',clearsTheOfferWhenTheSearchChanges],
];
const assertOk=(condition,what)=>assert.ok(condition,what);
// Runs every scenario against `code`, collecting what fails instead of asserting.
async function failuresOf(code){
  const failures=[];
  patience=15;
  try{
    for(const [name,scenario] of SCENARIOS){
      const collect=(condition,what)=>{if(!condition)failures.push(what);};
      try{await scenario(code,collect);}catch(error){failures.push(name+': '+(error&&error.message||error));}
    }
  }finally{patience=400;}
  return failures;
}

test('the contract covers every reason, both kinds of destination and the coverage answer',()=>{
  const reasons=new Set(contract.cases.map(c=>c.payload.unreachable_reason).filter(Boolean));
  assert.deepEqual([...reasons].sort(),['no_path_in_work_records','office_without_work_records','origin_without_work_connections','target_without_work_connections']);
  assert.deepEqual([...new Set(contract.cases.map(c=>c.mode))].sort(),['office','person']);
  assert.ok(contract.cases.some(c=>c.payload.status==='insufficient_work_coverage'));
  assert.ok(contract.cases.every(c=>c.payload.suggested_objective==='evidence_cost'));
});

test('the page the redesign People view shares has every element the offer touches',()=>{
  const html=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/index.html'),'utf8');
  for(const id of ['route-status','route-hops','route-form','route-model','route-options','route-submit','route-clear','ecosystem-routes','work-routes'])assert.match(html,new RegExp('id="'+id+'"'),id);
  assert.match(html,/<option value="evidence_cost">Ecosystem/);
});

for(const [name,scenario] of SCENARIOS)test(name,()=>scenario(source,assertOk));

test('positive control: the same scenarios, collected the way the controls collect them, find nothing wrong with the real page',async()=>{
  assert.deepEqual(await failuresOf(source),[]);
});

test('negative control: the page as it was before the change fails the same scenarios',{skip:NO_GIT},async()=>{
  const failures=await failuresOf(before);
  assert.ok(failures.length>=SCENARIOS.length-6,'the old page fails most of them, failed '+failures.length+' of '+SCENARIOS.length+' checks');
  assert.ok(failures.some(f=>/the status says/.test(f)),'it shows a fixed sentence, not the message');
  assert.ok(failures.some(f=>/one button, Search Ecosystem/.test(f)),'its button is labelled differently');
  assert.ok(failures.some(f=>/the offer is up|editing a name takes the offer away/.test(f)),'its offer outlives a changed search');
});

// Deliberate bugs of the new page. Each is one edit to the real file, and the scenarios must notice every one.
const BUGS=[
  ['the status ignores the reason the server sends',"data.unreachable_reason&&data.message?data.message:",'false?data.message:',/the status says/],
  ['the button is labelled Search',"el(out,'button','Search Ecosystem')","el(out,'button','Search')",/one button, Search Ecosystem|no Search Ecosystem button|Search Ecosystem button to press/],
  ['two buttons are offered',"const retry=el(out,'button','Search Ecosystem');retry.type='button';","el(out,'button','Search Ecosystem');const retry=el(out,'button','Search Ecosystem');retry.type='button';",/one button, Search Ecosystem/],
  ['the button switches the network but does not search',"openRouteNetwork(objective);$('route-form').requestSubmit();","openRouteNetwork(objective);",/the second search/],
  ['the button searches without switching the network',"openRouteNetwork(objective);$('route-form').requestSubmit();","$('route-form').requestSubmit();",/the network selector now says Ecosystem|objectives work_time then work_time/],
  ['the objective the server names is ignored',"?data.suggested_objective:'evidence_cost'","?'evidence_cost':'evidence_cost'",/suggested overlap_balanced/],
  ['the offer is made after an Ecosystem search too',"(data.objective||$('route-model').value)!=='evidence_cost'",'true',/Ecosystem: no button/],
  ['a changed search leaves the offer on the page',"if(offerShown){$('route-hops').replaceChildren();offerShown=false;}",'',/stale: editing a name takes the offer away/],
];
for(const [name,find,replacement,expected] of BUGS){
  test('negative control: '+name+' is caught',async()=>{
    assert.equal(source.split(find).length-1,1,'the edit applies exactly once: '+find);
    const failures=await failuresOf(source.replace(find,()=>replacement));
    assert.ok(failures.some(f=>expected.test(f)),'caught by '+expected+', failures: '+JSON.stringify(failures.slice(0,6)));
  });
}
