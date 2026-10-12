// An office search under Staffer says which of the office's current staff it left out (Paul's ruling D-086, 2026-10-03).
// The server drops the staff who lack the coverage the model requires (no payroll record and not an operational staffer: a person with
// dated directory work at the office can be one, so the line does not say they have no work records), searches to the rest, and counts them in
// uncovered_staff_count on every office answer; test_staffer_office_covered_staff.py asserts that against the real producer
// through the router, the service and the HTTP handler. The page shows one plain line with the result when that count is above
// zero: its singular and plural forms, and none when the count is zero or missing, or when no search ran because the origin is the person without
// coverage (the answer names the origin among unavailable_person_ids; test_staffer_office_covered_staff.py pins that on the producer).
// The line names the coverage the staff lack as the answer's uncovered_basis says (v4.1): payroll_or_roster, or roster where the comparison reads
// directory episodes only, which has no payroll evidence to cover anyone; any other basis, or none, gets the payroll-or-roster wording. The real
// staffer-route.js runs in a vm over a small fake DOM and a scripted server. The page as it was before the change and as v4 ran it (read from git)
// and seventeen deliberate bugs of the new page are each caught by the same scenarios: the negative controls.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const REL='fec/surfaces/staffer_live/staffer-route.js';
const ROOT=path.resolve(__dirname,'../../..');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-route.js'),'utf8');

// The page as it was before the change: the tip this work was built on (the unreachable-hint change).
const BEFORE='a9db5fd5fc2c164bdbd591488867008649532d83';
function fromGit(rev,rel){
  try{return execFileSync('git',['-C',ROOT,'show',rev+':'+rel],{encoding:'utf8',stdio:['ignore','pipe','ignore'],maxBuffer:1<<25});}catch(error){return null;}
}
const before=fromGit(BEFORE,REL);
const NO_GIT=before===null?'the pre-change page (commit '+BEFORE.slice(0,9)+') is not in this checkout':false;
// The page as v4 ran it: the line is there, and it names payroll whatever the basis.
const V4='79ef9f1b7440bcee21c7f7c677a93263d9fcc04a';
const v4=fromGit(V4,REL);
const NO_V4=v4===null?'the v4 page (commit '+V4.slice(0,9)+') is not in this checkout':false;

const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
// How many ticks a scenario waits for something to happen. The controls wait briefly: a page with a bug is expected not to get there.
let patience=400;
async function until(predicate,what){for(let i=0;i<patience;i++){if(predicate())return;await tick();}assert.fail('timed out waiting for '+what);}
const person=(id,label)=>({id,label,kind:'person'});
const OFFICE={id:'office:example',kind:'office',label:'Example Office',staff_count:5,records_through:'2026-09-01'};
const WHERE='?route_origin=origin1&office=Example%20Office';
const searching=id=>({ready:true,status:'searching',search_id:id,progress:{expanded:5,neighbor_checks:9,elapsed_ms:30},routes_requested:3});
// The sentences, as the ruling words them.
const PLURAL=n=>`${n} of this office's current staff lack the payroll or staffer-roster coverage this model requires and were left out.`;
const SINGULAR="1 of this office's current staff lacks the payroll or staffer-roster coverage this model requires and was left out.";
// ... and for a comparison of directory episodes only (uncovered_basis 'roster'): no payroll evidence is read there, so the roster alone is named.
const ROSTER_PLURAL=n=>`${n} of this office's current staff lack the staffer-roster coverage this model requires and were left out.`;
const ROSTER_SINGULAR="1 of this office's current staff lacks the staffer-roster coverage this model requires and was left out.";
const COMPARED='Example Office · compared 5 staff';
const RECORDS='Staff listed in records through 2026-09-01';

// What the service sends for an office route that was found: every field the page reads, and the fields of the rule on top.
const hop={source:'origin1',target:'target1',relationship:'work_time_overlap',relationship_label:'Dated workplace overlap',cost:1,overlap_duration_days:90,provenance:{}};
const found=(extra={},id='S1')=>({ready:true,found:true,status:'found',optimal:true,objective:'work_time',steps:1,
  nodes:['origin1','target1'].map(i=>person(i,'Name '+i)),path:['origin1','target1'],hops:[hop],edges:[hop],
  office:{id:OFFICE.id,label:OFFICE.label,records_through:OFFICE.records_through},candidate_count:5,recommended_contact:{title:'Legislative Director'},
  routes:[],explanation:'x',warnings:[],elapsed_ms:3,search_id:id,...extra});
// ... and for one that was not: the contract's fields (staffer_unreachable_hint_contract.cjs) and the fields of the rule on top.
const none=(extra={},id='S1')=>({ready:true,found:false,optimal:true,objective:'work_time',nodes:[],path:[],hops:[],edges:[],routes:[],graph:{nodes:[],edges:[]},
  warnings:[],explanation:'x',elapsed_ms:3,search_id:id,suggested_objective:'evidence_cost',...extra});
const UNREACHABLE=none({status:'unreachable',unreachable_reason:'no_path_in_work_records',
  message:"No Staffer route between this person and this office's current staff in the work records this model uses."});
const COVERAGE=none({status:'insufficient_work_coverage',
  message:"These people are known, but their records do not meet this model's authenticated staffer coverage requirements. Try Ecosystem for documented associations."});
// What the server sends when the origin is the person without coverage: no search ran, and the answer names the origin among unavailable_person_ids.
// The staff it counts were not left out of any search. (test_staffer_office_covered_staff.py pins both fields on the real producer.)
const ORIGIN_UNCOVERED=(extra={})=>({...COVERAGE,origin:'origin1',unavailable_person_ids:['origin1','u1','u2'],uncovered_staff_count:2,uncovered_staff_ids:['u1','u2'],...extra});
// ... and when the origin is covered and none of the office's staff is: the staff are unavailable, the origin is not, and all of them were left out.
const NO_STAFF_COVERED=(extra={})=>({...COVERAGE,origin:'origin1',unavailable_person_ids:['u1','u2','u3'],uncovered_staff_count:3,uncovered_staff_ids:['u1','u2','u3'],...extra});

// A page with just enough DOM. `server(call)` answers every request.
function boot(code,server,{search=WHERE}={}){
  const elements={},calls=[],statusLog=[];
  const defaults={'route-model':'work_time','route-count':'3','work-sources':'payroll_and_directory','alternative-strategy':'distinct'};
  const make=id=>{
    const e={id,children:[],dataset:{},attributes:{},listeners:{},value:defaults[id]??'',hidden:false,open:false,scrollTop:0,title:'',disabled:false,
      classList:{toggle(){},add(){},remove(){}},style:{},
      setAttribute(k,v){e.attributes[k]=String(v);},getAttribute(k){return e.attributes[k]??null;},
      addEventListener(type,fn){(e.listeners[type]??=[]).push(fn);},
      append(...nodes){e.children.push(...nodes);},replaceChildren(...nodes){e.children=nodes;},
      focus(){},scrollIntoView(){},getBoundingClientRect(){return {top:0};},
      requestSubmit(){e.requested=(e.requested||0)+1;for(const fn of e.listeners.submit||[])e.lastSubmit=fn({preventDefault(){}});}};
    if(id==='route-model')e.options=['work_time','evidence_cost'].map(value=>({value}));
    let text='';
    if(id==='route-status')Object.defineProperty(e,'textContent',{get:()=>text,set:v=>{text=String(v);statusLog.push(text);}});
    else e.textContent='';
    return e;
  };
  const $=id=>elements[id]??=make(id);
  const live={canonicalId:id=>id,isPerson:n=>n.kind==='person',node:()=>null,sanitizeRoute:d=>d,showRoute(){},clearRoute(){},fitView(){},select(){},showPerson(){},showNeighborhood(){},addNodes(){}};
  const window={StafferLive:live,addEventListener(){},dispatchEvent(){return true;}};
  const document={getElementById:$,createElement:tag=>{const e=make(tag);e.tag=tag;return e;},
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
  const walk=(node,visit)=>{visit(node);for(const child of node.children||[])walk(child,visit);};
  return {$,calls,statusLog,
    submit:()=>$('route-form').listeners.submit[0]({preventDefault(){}}),
    stop:()=>{try{$('route-clear').onclick();}catch(error){}},   // ends any search still polling
    // every note on the page now, in order, wherever it is in the result area
    notes:()=>{const all=[];walk($('route-hops'),n=>{if(n.tag==='p'&&n.className==='note')all.push(n.textContent);});return all;},
    leftOut:()=>{const all=[];walk($('route-hops'),n=>{if(n.tag==='p'&&/left out/.test(n.textContent))all.push(n.textContent);});return all;},
    card:()=>$('route-hops').children.find(c=>c.className==='office-result'),
    buttons:()=>$('route-hops').children.filter(e=>e.tag==='button').map(e=>e.textContent),
    ready:async()=>{
      await until(()=>$('route-origin').value==='Name origin1','the restored origin');
      await until(()=>$('target-options').children.length>0,'the office suggestions');
    }};
}

// Answers a page's requests: the person and office lookups, then one search that finishes with `payload` on its first poll.
function server(payload){
  return call=>{
    if(call.path==='/api/staffer-person')return {ready:true,node:person(call.params.id,'Name '+call.params.id)};
    if(call.path==='/api/staffer-offices')return {ready:true,nodes:[OFFICE]};
    if(call.path==='/api/staffer-search')return {ready:true,nodes:[]};
    if(call.path==='/api/staffer-office-route')return searching('S1');
    return typeof payload==='function'?payload():payload;
  };
}
async function search(code,payload,after){
  const page=boot(code,server(payload));
  try{await page.ready();await page.submit();return await after(page);}finally{page.stop();}
}

// ---- the scenarios. Each takes the page's code and `ok(condition, what)`: asserting for the real page, collecting for the controls.
async function showsThePluralLineInTheOfficeCard(code,ok){
  await search(code,found({uncovered_staff_count:3,uncovered_staff_ids:['u1','u2','u3']}),page=>{
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(3)]),'plural: exactly one line, "'+PLURAL(3)+'"; found '+JSON.stringify(page.leftOut()));
    const lines=page.card()?.children.filter(c=>c.tag==='p').map(c=>c.textContent)||[];
    ok(JSON.stringify(lines.filter(l=>[COMPARED,PLURAL(3),RECORDS].includes(l)))===JSON.stringify([COMPARED,PLURAL(3),RECORDS]),'plural: the line sits in the office card between the compared and the records lines; found '+JSON.stringify(lines));
    ok(page.$('route-status').textContent.includes('Staffer route: 1 step'),'plural: the route itself is shown as before, found '+JSON.stringify(page.$('route-status').textContent));
  });
}
async function showsTheSingularLine(code,ok){
  await search(code,found({uncovered_staff_count:1,uncovered_staff_ids:['u1']}),page=>{
    ok(JSON.stringify(page.leftOut())===JSON.stringify([SINGULAR]),'singular: "'+SINGULAR+'"; found '+JSON.stringify(page.leftOut()));
  });
}
async function showsNoLineWhenNobodyWasLeftOut(code,ok){
  const quiet=[['0',{uncovered_staff_count:0,uncovered_staff_ids:[]}],['missing',{}],['null',{uncovered_staff_count:null}],['not a number',{uncovered_staff_count:'many'}],['negative',{uncovered_staff_count:-2}]];
  for(const [what,fields] of quiet){
    await search(code,found(fields),page=>{
      ok(page.leftOut().length===0,what+': no line, found '+JSON.stringify(page.leftOut()));
      ok(page.notes().includes(COMPARED)&&page.notes().includes(RECORDS),what+': the office card is as before, found '+JSON.stringify(page.notes()));
    });
  }
  await search(code,UNREACHABLE,page=>{ok(page.leftOut().length===0,'unreachable without the field: no line, found '+JSON.stringify(page.leftOut()));});
}
async function showsTheLineWithAnUnreachableAnswer(code,ok){
  const payload={...UNREACHABLE,uncovered_staff_count:2,uncovered_staff_ids:['u1','u2']};
  await search(code,payload,page=>{
    ok(page.$('route-status').textContent===payload.message,'unreachable: the status says the message, found '+JSON.stringify(page.$('route-status').textContent));
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(2)]),'unreachable: one line, "'+PLURAL(2)+'"; found '+JSON.stringify(page.leftOut()));
    ok(JSON.stringify(page.buttons())===JSON.stringify(['Search Ecosystem']),'unreachable: the one button is still offered, found '+JSON.stringify(page.buttons()));
    const kinds=page.$('route-hops').children.map(c=>c.tag);
    ok(JSON.stringify(kinds)===JSON.stringify(['p','button']),'unreachable: the line comes before the button, found '+JSON.stringify(kinds));
  });
}
async function showsTheLineWithACoverageAnswer(code,ok){
  const payload={...COVERAGE,uncovered_staff_count:1,uncovered_staff_ids:['u1']};
  await search(code,payload,page=>{
    ok(page.$('route-status').textContent===payload.message,'coverage: the status says the message, found '+JSON.stringify(page.$('route-status').textContent));
    ok(JSON.stringify(page.leftOut())===JSON.stringify([SINGULAR]),'coverage: one line, singular; found '+JSON.stringify(page.leftOut()));
    ok(JSON.stringify(page.buttons())===JSON.stringify(['Search Ecosystem']),'coverage: the one button is still offered, found '+JSON.stringify(page.buttons()));
  });
}
async function showsEachRoutesLineOnce(code,ok){
  const second={...found({uncovered_staff_count:3,uncovered_staff_ids:['u1','u2','u3']}),route_rank:2};
  const routes=[found({uncovered_staff_count:3,uncovered_staff_ids:['u1','u2','u3']}),second];
  const payload={...found({uncovered_staff_count:3,uncovered_staff_ids:['u1','u2','u3']}),routes,routes_requested:3,alternative_strategy:'distinct'};
  await search(code,payload,page=>{
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(3)]),'routes: route 1 shows the line once, found '+JSON.stringify(page.leftOut()));
    const choices=page.$('route-options').children.filter(e=>e.tag==='button');
    ok(choices.length===2,'routes: two routes to choose from, found '+choices.length);
    choices[1]?.onclick();
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(3)]),'routes: route 2 shows the line once, found '+JSON.stringify(page.leftOut()));
    choices[0]?.onclick();
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(3)]),'routes: back to route 1, still once, found '+JSON.stringify(page.leftOut()));
  });
}
async function goesWhenTheSearchChanges(code,ok){
  const fields={uncovered_staff_count:2,uncovered_staff_ids:['u1','u2']};
  for(const [what,payload] of [['found',found(fields)],['unreachable',{...UNREACHABLE,...fields}]]){
    await search(code,payload,page=>{
      ok(page.leftOut().length===1,what+': the line is up');
      page.$('route-origin').value='ab';page.$('route-origin').listeners.input[0]();   // the reader edits the starting name
      ok(page.leftOut().length===0&&page.$('route-hops').children.length===0,what+': editing a name takes the line away with the result, found '+JSON.stringify(page.notes()));
    });
    await search(code,payload,page=>{
      page.$('route-clear').onclick();
      ok(page.leftOut().length===0,what+': Clear takes the line away');
    });
  }
}
async function leavesAPersonRouteAlone(code,ok){
  const page=boot(code,call=>{
    if(call.path==='/api/staffer-person')return {ready:true,node:person(call.params.id,'Name '+call.params.id)};
    if(call.path==='/api/staffer-route')return searching('S1');
    return {ready:true,found:true,status:'found',optimal:true,objective:'work_time',steps:1,nodes:['origin1','target1'].map(i=>person(i,'Name '+i)),path:['origin1','target1'],
      hops:[hop],edges:[hop],routes:[],explanation:'x',warnings:[],elapsed_ms:3,search_id:'S1'};
  },{search:'?route_origin=origin1&route_target=target1'});
  try{
    await until(()=>page.$('route-target').value==='Name target1','the restored target');await page.submit();
    ok(page.leftOut().length===0&&page.card()===undefined,'person route: no line and no office card, found '+JSON.stringify(page.notes()));
    ok(/Staffer route: 1 step/.test(page.$('route-status').textContent),'person route: shown as before, found '+JSON.stringify(page.$('route-status').textContent));
  }finally{page.stop();}
}
async function showsNoLineWhenTheOriginIsTheOneWithoutCoverage(code,ok){
  const extra={unavailable_person_ids:['origin1','u1','u2','u3'],uncovered_staff_count:3,uncovered_staff_ids:['u1','u2','u3']};
  for(const [what,payload] of [['some staff also uncovered',ORIGIN_UNCOVERED()],['every staff member also uncovered',ORIGIN_UNCOVERED(extra)]]){
    await search(code,payload,page=>{
      ok(page.$('route-status').textContent===COVERAGE.message,'origin uncovered, '+what+': the status says the message, found '+JSON.stringify(page.$('route-status').textContent));
      ok(page.leftOut().length===0&&page.notes().length===0,'origin uncovered, '+what+': no line, found '+JSON.stringify(page.notes()));
      ok(JSON.stringify(page.buttons())===JSON.stringify(['Search Ecosystem']),'origin uncovered, '+what+': the one button is still offered, found '+JSON.stringify(page.buttons()));
    });
  }
}
async function showsTheLineWhenTheOriginIsCoveredAndNoStaffMemberIs(code,ok){
  await search(code,NO_STAFF_COVERED(),page=>{
    ok(page.$('route-status').textContent===COVERAGE.message,'no staff covered: the status says the message, found '+JSON.stringify(page.$('route-status').textContent));
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(3)]),'no staff covered: one line, "'+PLURAL(3)+'"; found '+JSON.stringify(page.leftOut()));
    ok(JSON.stringify(page.buttons())===JSON.stringify(['Search Ecosystem']),'no staff covered: the one button is still offered, found '+JSON.stringify(page.buttons()));
  });
}
// The page knows the origin is the one without coverage only because the answer says so. When it does not, the line stays, as it was.
async function readsTheOriginOnlyFromTheAnswer(code,ok){
  const cases=[['no origin in the answer',{origin:undefined}],['no list of unavailable people',{unavailable_person_ids:undefined}],['an empty list',{unavailable_person_ids:[]}],
    ['a list that is not a list',{unavailable_person_ids:'origin1'}],['another person as the origin',{origin:'someone-else'}]];
  for(const [what,fields] of cases){
    await search(code,ORIGIN_UNCOVERED(fields),page=>{
      ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(2)]),'cannot tell ('+what+'): the line stays, found '+JSON.stringify(page.leftOut()));
    });
  }
}
// The rule is about a search that did not run for want of coverage. An answer of any other kind keeps its line, whatever its list of unavailable people holds.
async function onlyACoverageAnswerLosesItsLine(code,ok){
  const fields={origin:'origin1',unavailable_person_ids:['origin1'],uncovered_staff_count:2,uncovered_staff_ids:['u1','u2']};
  await search(code,{...UNREACHABLE,...fields},page=>{
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(2)]),'not a coverage answer (unreachable): the line stays, found '+JSON.stringify(page.leftOut()));
  });
  await search(code,found(fields),page=>{
    ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(2)]),'not a coverage answer (found): the line stays, found '+JSON.stringify(page.leftOut()));
  });
}
// The basis of the count picks the wording: 'roster' names the roster alone, in the singular and the plural, on every kind of answer that shows the line.
const ROSTER=(n,extra={})=>({uncovered_staff_count:n,uncovered_staff_ids:Array.from({length:n},(_,i)=>'u'+(i+1)),uncovered_basis:'roster',...extra});
async function namesTheRosterAloneWhereNoPayrollIsRead(code,ok){
  const cases=[['found, plural',found(ROSTER(3)),ROSTER_PLURAL(3)],['found, singular',found(ROSTER(1)),ROSTER_SINGULAR],
    ['unreachable',{...UNREACHABLE,...ROSTER(2)},ROSTER_PLURAL(2)],['coverage, no staff covered',NO_STAFF_COVERED({uncovered_basis:'roster'}),ROSTER_PLURAL(3)]];
  for(const [what,payload,expected] of cases){
    await search(code,payload,page=>{
      ok(JSON.stringify(page.leftOut())===JSON.stringify([expected]),'roster, '+what+': one line, "'+expected+'"; found '+JSON.stringify(page.leftOut()));
      ok(!/payroll/.test(page.leftOut().join(' ')),'roster, '+what+': the line does not mention payroll, found '+JSON.stringify(page.leftOut()));
    });
  }
  await search(code,found(ROSTER(3)),page=>{
    const lines=page.card()?.children.filter(c=>c.tag==='p').map(c=>c.textContent)||[];
    ok(JSON.stringify(lines.filter(l=>[COMPARED,ROSTER_PLURAL(3),RECORDS].includes(l)))===JSON.stringify([COMPARED,ROSTER_PLURAL(3),RECORDS]),'roster, card: the line sits between the compared and the records lines; found '+JSON.stringify(lines));
  });
  const second={...found(ROSTER(3)),route_rank:2};
  await search(code,{...found(ROSTER(3)),routes:[found(ROSTER(3)),second],routes_requested:3,alternative_strategy:'distinct'},page=>{
    const choices=page.$('route-options').children.filter(e=>e.tag==='button');
    choices[1]?.onclick();
    ok(JSON.stringify(page.leftOut())===JSON.stringify([ROSTER_PLURAL(3)]),'roster, routes: route 2 shows the line once, found '+JSON.stringify(page.leftOut()));
  });
  // where no search ran because the origin is the person without coverage there is still no line, whatever the basis
  await search(code,ORIGIN_UNCOVERED({uncovered_basis:'roster'}),page=>{
    ok(page.leftOut().length===0,'roster, origin uncovered: no line, found '+JSON.stringify(page.leftOut()));
  });
}
// Anything but the word 'roster' keeps the wording the page always had, so a page in front of an older server (no basis at all) says what it said.
async function keepsThePayrollOrRosterWordingForAnyOtherBasis(code,ok){
  const cases=[['payroll_or_roster',{uncovered_basis:'payroll_or_roster'}],['no basis, as an older server answers',{}],['null',{uncovered_basis:null}],
    ['an empty string',{uncovered_basis:''}],['another case',{uncovered_basis:'Roster'}],['a longer name',{uncovered_basis:'roster_only'}],['a list',{uncovered_basis:['roster']}]];
  for(const [what,extra] of cases){
    await search(code,found({uncovered_staff_count:2,uncovered_staff_ids:['u1','u2'],...extra}),page=>{
      ok(JSON.stringify(page.leftOut())===JSON.stringify([PLURAL(2)]),'basis ('+what+'): the payroll-or-roster line, found '+JSON.stringify(page.leftOut()));
    });
  }
  await search(code,found({uncovered_staff_count:1,uncovered_staff_ids:['u1'],uncovered_basis:'payroll_or_roster'}),page=>{
    ok(JSON.stringify(page.leftOut())===JSON.stringify([SINGULAR]),'basis (payroll_or_roster, singular): the singular payroll-or-roster line, found '+JSON.stringify(page.leftOut()));
  });
}
const SCENARIOS=[
  ['a found office route shows the plural line in the office card',showsThePluralLineInTheOfficeCard],
  ['one left-out member reads in the singular',showsTheSingularLine],
  ['no line when the count is 0, missing, null, not a number or negative',showsNoLineWhenNobodyWasLeftOut],
  ['an unreachable office answer shows the line before its one button',showsTheLineWithAnUnreachableAnswer],
  ['an insufficient-coverage answer shows the line before its one button',showsTheLineWithACoverageAnswer],
  ['each of several routes shows the line once',showsEachRoutesLineOnce],
  ['the line goes with the result when the search changes',goesWhenTheSearchChanges],
  ['a person route has no line',leavesAPersonRouteAlone],
  ['no line when the origin is the person without coverage, because no search ran',showsNoLineWhenTheOriginIsTheOneWithoutCoverage],
  ['the line stays when the origin is covered and no staff member is',showsTheLineWhenTheOriginIsCoveredAndNoStaffMemberIs],
  ['the line stays when the answer does not say the origin is the one without coverage',readsTheOriginOnlyFromTheAnswer],
  ['an answer that is not a coverage answer keeps its line',onlyACoverageAnswerLosesItsLine],
  ['a directory-only comparison names the staffer roster alone, in every kind of answer',namesTheRosterAloneWhereNoPayrollIsRead],
  ['any other basis, or none, keeps the payroll-or-roster wording',keepsThePayrollOrRosterWordingForAnyOtherBasis],
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

test('the sentences are the ruling\'s words, singular and plural',()=>{
  assert.equal(PLURAL(3),"3 of this office's current staff lack the payroll or staffer-roster coverage this model requires and were left out.");
  assert.equal(SINGULAR,"1 of this office's current staff lacks the payroll or staffer-roster coverage this model requires and was left out.");
  // What the line must not claim: that the people left out have no work records. A person with a dated directory episode can lack the coverage.
  assert.ok(!/work records/.test(PLURAL(3))&&!/work records/.test(SINGULAR));
  // The directory-only wording (uncovered_basis 'roster'): the roster alone, and no claim about payroll.
  assert.equal(ROSTER_PLURAL(3),"3 of this office's current staff lack the staffer-roster coverage this model requires and were left out.");
  assert.equal(ROSTER_SINGULAR,"1 of this office's current staff lacks the staffer-roster coverage this model requires and was left out.");
  assert.ok(!/payroll|work records/.test(ROSTER_PLURAL(3))&&!/payroll|work records/.test(ROSTER_SINGULAR));
});

for(const [name,scenario] of SCENARIOS)test(name,()=>scenario(source,assertOk));

test('positive control: the same scenarios, collected the way the controls collect them, find nothing wrong with the real page',async()=>{
  assert.deepEqual(await failuresOf(source),[]);
});

test('negative control: the page as it was before the change fails the scenarios that expect the line',{skip:NO_GIT},async()=>{
  const failures=await failuresOf(before);
  assert.ok(failures.some(f=>/^plural: exactly one line/.test(f)),'it shows no plural line');
  assert.ok(failures.some(f=>/^singular:/.test(f)),'it shows no singular line');
  assert.ok(failures.some(f=>/^unreachable: one line/.test(f)),'it shows no line with an unreachable answer');
  assert.ok(failures.some(f=>/^coverage: one line/.test(f)),'it shows no line with a coverage answer');
  assert.ok(failures.some(f=>/^routes: route 1 shows the line once/.test(f)),'it shows no line with several routes');
  assert.ok(failures.length>=6,'failed '+failures.length+' of the checks');
});

test('negative control: the page as v4 ran it names payroll for a directory-only comparison, and only there is it wrong',{skip:NO_V4},async()=>{
  const failures=await failuresOf(v4);
  assert.ok(failures.length>0,'it fails the roster scenario');
  assert.ok(failures.every(f=>/^roster, /.test(f)),'and nothing else: '+JSON.stringify(failures.filter(f=>!/^roster, /.test(f)).slice(0,4)));
  assert.ok(failures.some(f=>/^roster, found, plural/.test(f))&&failures.some(f=>/^roster, found, singular/.test(f)),'it names payroll in both forms');
});

// Deliberate bugs of the new page. Each is one edit to the real file, and the scenarios must notice every one.
// The guard of leftOutLine, as the deliberate bugs below see it.
const GUARD="if(data.status==='insufficient_work_coverage'&&Array.isArray(data.unavailable_person_ids)&&data.unavailable_person_ids.includes(data.origin))return '';";
const BUGS=[
  ['the singular still says "lack"',"${left===1?'lacks':'lack'}","${'lack'}",/^singular:/],
  ['the line says the staff have no work records, as it did before the coverage wording was corrected',"${left===1?'lacks':'lack'} the ${coverage} coverage this model requires","${left===1?'has':'have'} no work records this model uses",/^plural: exactly one line/],
  ['the singular still says "were"',"${left===1?'was':'were'}","${'were'}",/^singular:/],
  ['a count of zero gets a line',"return left>0?","return left>=0?",/^0: no line/],
  ['the line counts the compared staff, not the left-out',"const left=Number(data.uncovered_staff_count);","const left=Number(data.candidate_count);",/^0: no line|^missing: no line/],
  ['the office card has no line',"const left=leftOutLine(data);if(left)el(card,'p',left,'note');","",/^plural: exactly one line/],
  ['a result with no route has no line',"const left=leftOutLine(data);if(left)el(out,'p',left,'note');","",/^unreachable: one line|^coverage: one line/],
  ['the line is put after the button',"const left=leftOutLine(data);if(left)el(out,'p',left,'note');if(['unreachable','insufficient_work_coverage'].includes(data.status)&&(data.objective||$('route-model').value)!=='evidence_cost')offerEcosystem(out,data);return;}",
    "if(['unreachable','insufficient_work_coverage'].includes(data.status)&&(data.objective||$('route-model').value)!=='evidence_cost')offerEcosystem(out,data);const left=leftOutLine(data);if(left)el(out,'p',left,'note');return;}",/^unreachable: the line comes before the button/],
  ['a coverage answer loses its line whoever is uncovered',GUARD,"if(data.status==='insufficient_work_coverage')return '';",/^no staff covered: one line/],
  ['any answer loses its line when the origin is unavailable, a coverage answer or not',GUARD,"if(Array.isArray(data.unavailable_person_ids)&&data.unavailable_person_ids.includes(data.origin))return '';",/^not a coverage answer/],
  ['a coverage answer loses its line when anyone is unavailable, not only the origin',GUARD,"if(data.status==='insufficient_work_coverage'&&Array.isArray(data.unavailable_person_ids)&&data.unavailable_person_ids.length>0)return '';",/^no staff covered: one line/],
  ['the list of unavailable people is trusted to be a list',"&&Array.isArray(data.unavailable_person_ids)&&","&&",/^cannot tell \(a list that is not a list\)/],
  ['the line is never held back',GUARD,"",/^origin uncovered/],
  // The basis of the count (v4.1).
  ['the basis is ignored: every answer names payroll',"coverage=data.uncovered_basis==='roster'?'staffer-roster':'payroll or staffer-roster'","coverage='payroll or staffer-roster'",/^roster, /],
  ['the roster alone is named whatever the basis',"coverage=data.uncovered_basis==='roster'?'staffer-roster':'payroll or staffer-roster'","coverage='staffer-roster'",/^basis \(payroll_or_roster\)|^plural: exactly one line/],
  ['any basis that is set names the roster alone',"data.uncovered_basis==='roster'","data.uncovered_basis",/^basis \(payroll_or_roster\)/],
  ['the basis is read from the wrong field',"data.uncovered_basis==='roster'","data.basis==='roster'",/^roster, /],
];
for(const [name,find,replacement,expected] of BUGS){
  test('negative control: '+name+' is caught',async()=>{
    assert.equal(source.split(find).length-1,1,'the edit applies exactly once: '+find);
    const failures=await failuresOf(source.replace(find,()=>replacement));
    assert.ok(failures.some(f=>expected.test(f)),'caught by '+expected+', failures: '+JSON.stringify(failures.slice(0,6)));
  });
}
