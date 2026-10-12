// Boots the real staffer-route.js in a VM against a scripted server and drives the submit and cancel handlers.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-route.js'),'utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
async function until(predicate,what){for(let i=0;i<400;i++){if(predicate())return;await tick();}assert.fail('timed out waiting for '+what);}

const person=(id,label)=>({id,label,kind:'person'});
function route(rank,ids,extra={}){
  const hops=ids.slice(1).map((target,i)=>({source:ids[i],target,relationship:'work_time_overlap',relationship_label:'Dated workplace overlap',
    cost:1,overlap_duration_days:90,provenance:{}}));
  return {found:true,status:'found',optimal:true,objective:'work_time',route_rank:rank,steps:hops.length,
    nodes:ids.map(id=>person(id,'Name '+id)),path:ids,hops,edges:hops,explanation:'x',warnings:[],elapsed_ms:1500,
    alternative_strategy:'distinct',...extra};
}
const ROUTE1=route(1,['origin1','mid','target1']),ROUTE2=route(2,['origin1','other','target1']);
const finished=(id,extra={})=>({ready:true,...ROUTE1,routes:[ROUTE1,ROUTE2],routes_requested:3,routes_returned:2,
  alternative_strategy:'distinct',search_id:id,...extra});
const searching=(id,extra={})=>({ready:true,status:'searching',search_id:id,progress:{expanded:25,neighbor_checks:100,elapsed_ms:300},routes_requested:3,...extra});

// A page with just enough DOM; server(call) answers every request.
function boot(server,search='?route_origin=origin1&route_target=target1',globals={}){
  const elements={},created=[],calls=[],statusLog=[],shown=[],cleared=[];
  const defaults={'route-model':'work_time','route-count':'3','work-sources':'payroll_and_directory','alternative-strategy':'distinct'};
  const make=id=>{
    const e={id,children:[],dataset:{},attributes:{},listeners:{},value:defaults[id]??'',hidden:false,open:false,scrollTop:0,title:'',disabled:false,
      classList:{toggle(){},add(){},remove(){}},style:{},
      setAttribute(k,v){e.attributes[k]=String(v);},getAttribute(k){return e.attributes[k]??null;},
      addEventListener(type,fn){(e.listeners[type]??=[]).push(fn);},
      append(...nodes){e.children.push(...nodes);},replaceChildren(...nodes){e.children=nodes;},
      focus(){},scrollIntoView(){},getBoundingClientRect(){return {top:0};},
      requestSubmit(){e.requested=(e.requested||0)+1;for(const fn of e.listeners.submit||[])e.lastSubmit=fn({preventDefault(){}});}};
    let text='';
    if(id==='route-status')Object.defineProperty(e,'textContent',{get:()=>text,set:v=>{text=String(v);statusLog.push(text);}});
    else e.textContent='';
    return e;
  };
  const $=id=>elements[id]??=make(id);
  const live={canonicalId:id=>id,isPerson:n=>n.kind==='person',node:()=>null,sanitizeRoute:d=>d,showRoute:d=>shown.push(d),
    clearRoute:()=>cleared.push(1),fitView(){},select(){},showPerson(){},showNeighborhood(){},addNodes(){}};
  const windowListeners={};
  const window={StafferLive:live,addEventListener(type,fn){(windowListeners[type]??=[]).push(fn);},dispatchEvent(){return true;}};
  const document={getElementById:$,createElement:tag=>{const e=make(tag);e.tag=tag;created.push(e);return e;},
    querySelectorAll:()=>[],querySelector:()=>make('q'),body:{classList:{toggle(){},contains:()=>false}}};
  const aborted=signal=>{if(signal?.aborted){const error=new Error('aborted');error.name='AbortError';throw error;}};
  const fetch=async(input,init={})=>{
    const url=input instanceof URL?input:new URL(String(input),'http://localhost');
    const call={path:url.pathname,params:Object.fromEntries(url.searchParams),method:init.method||'GET',headers:init.headers,keepalive:init.keepalive,signal:init.signal,
      body:init.body?JSON.parse(init.body):undefined,order:calls.length};
    calls.push(call);aborted(init.signal);
    const data=await server(call,{shown,statusLog,calls});
    await tick();aborted(init.signal);
    return {ok:true,status:200,json:async()=>data};
  };
  const context=vm.createContext({window,document,location:{href:'http://localhost/staffer-live/'+search,search,origin:'http://localhost',pathname:'/staffer-live/'},
    history:{replaceState(){}},fetch,setTimeout:fn=>setTimeout(fn,0),clearTimeout,URL,URLSearchParams,AbortController,performance,
    matchMedia:()=>({matches:false}),requestAnimationFrame:fn=>fn(),CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}},
    Event:class{constructor(type){this.type=type;}},console,...globals});
  vm.runInContext(source,context);
  const submit=()=>elements['route-form'].listeners.submit[0]({preventDefault(){}});
  const buttons=label=>created.filter(e=>e.tag==='button'&&e.textContent===label);
  const stop=()=>{try{elements['route-clear'].onclick();}catch(error){}};   // ends any search still polling
  const fire=type=>{for(const fn of windowListeners[type]||[])fn({type});};   // window events such as pagehide
  return {$,elements,calls,statusLog,shown,cleared,submit,buttons,stop,windowListeners,fire,
    posts:()=>calls.filter(c=>c.method==='POST'),
    polls:id=>calls.filter(c=>c.path==='/api/staffer-route-result'&&(!id||c.params.id===id)),
    starts:()=>calls.filter(c=>c.path==='/api/staffer-route'),
    ready:async()=>{await until(()=>$('route-origin').value==='Name origin1'&&$('route-target').value==='Name target1','restored endpoints');}};
}
// The page token: the first start request carries it, and every later start, supersede and cancel must repeat it.
const token=page=>{const first=page.starts()[0];assert.ok(first,'the page has started a search');return first.params.holder;};
const personAnswer=call=>{
  if(call.path==='/api/staffer-person')return {ready:true,node:person(call.params.id,'Name '+call.params.id)};
  return undefined;
};

test('a search that finishes normally sends no supersedes and no cancel',async t=>{
  let polls=0;
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):(++polls<3?searching('S1'):finished('S1'))));
  t.after(page.stop);await page.ready();
  await page.submit();
  assert.equal(page.starts().length,1);
  assert.equal('supersedes' in page.starts()[0].params,false);
  assert.equal(page.starts()[0].params.async,'1');
  page.elements['route-clear'].onclick();   // nothing is running, so Clear must not cancel a finished search
  assert.deepEqual(page.posts(),[]);
  assert.match(page.$('route-status').textContent,/cleared/);
});

test('route 1 is drawn as soon as it arrives, with a plain status, and the finished result replaces it',async t=>{
  let polls=0,drawnBeforeFinal=null;
  const page=boot((call,context)=>{
    const answer=personAnswer(call);if(answer)return answer;
    if(call.path==='/api/staffer-route')return searching('S1');
    polls++;
    if(polls<=2)return searching('S1');
    if(polls===3)return searching('S1',{partial_routes:[ROUTE1],progress:{expanded:900,routes_found:1,routes_requested:3}});
    if(polls===4){return searching('S1',{partial_routes:[ROUTE1],progress:{expanded:1200,routes_found:1,routes_requested:3}});}
    drawnBeforeFinal=context.shown.length;
    return finished('S1');
  });
  t.after(page.stop);await page.ready();
  await page.submit();
  assert.equal(drawnBeforeFinal,1,'route 1 was drawn once, before the finished result existed');
  assert.equal(page.shown[0].route_rank,1);assert.deepEqual(page.shown[0].path,['origin1','mid','target1']);
  const waiting=page.statusLog.filter(text=>/still finding alternatives/.test(text));
  assert.ok(waiting.length>=2,'status says alternatives are still being found: '+JSON.stringify(page.statusLog.slice(-8)));
  assert.match(waiting[0],/^Route 1 found · still finding alternatives · \d+s/);
  assert.doesNotMatch(waiting.join('|'),/Staffer route/);   // renderRoute's own status line is replaced, not left behind
  assert.equal(page.shown.length,2);                          // finished result drawn after the partial one
  assert.match(page.$('route-status').textContent,/^Route 1 of 2 · Staffer route: 2 steps\./);
  assert.equal(page.$('route-options').hidden,false);
  assert.equal(page.$('route-submit').disabled,false);assert.equal(page.$('route-clear').textContent,'Clear');
});

test('only the first partial route is drawn; later partial answers leave the page alone',async t=>{
  let polls=0;
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):
    ++polls<6?searching('S1',{partial_routes:[ROUTE1,...(polls>2?[ROUTE2]:[])],progress:{routes_found:polls>2?2:1}}):finished('S1')));
  t.after(page.stop);await page.ready();
  await page.submit();
  assert.equal(page.shown.length,2);   // one partial, one finished
});

test('Cancel search stops polling and asks the server to cancel that search',async t=>{
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):searching('S1')));
  t.after(page.stop);await page.ready();
  const running=page.submit();
  await until(()=>page.polls('S1').length>=2,'two polls');
  assert.equal(page.$('route-clear').textContent,'Cancel search');
  page.elements['route-clear'].onclick();
  const post=page.posts();
  assert.equal(post.length,1);
  assert.equal(post[0].path,'/api/staffer-route-cancel');
  assert.deepEqual(post[0].body,{id:'S1',holder:token(page)});
  assert.equal(post[0].headers['Content-Type'],'application/json');assert.equal(post[0].keepalive,true);
  await running;                                  // the aborted loop ends without an error
  const polled=page.polls().length;
  for(let i=0;i<20;i++)await tick();
  assert.equal(page.polls().length,polled,'no polling after cancel');
  assert.equal(page.$('route-submit').disabled,false);
  page.elements['route-clear'].onclick();         // a second click must not cancel again
  assert.equal(page.posts().length,1);
});

test('cancelling before the first answer sends nothing, because there is no search id yet',async t=>{
  let release;const held=new Promise(resolve=>release=resolve);
  const page=boot(async call=>{const answer=personAnswer(call);if(answer)return answer;await held;return searching('S1');});
  t.after(page.stop);await page.ready();
  const running=page.submit();
  await until(()=>page.starts().length===1,'first request');
  page.elements['route-clear'].onclick();
  assert.deepEqual(page.posts(),[]);
  release();await running;
});

test('a new search supersedes the previous one on its first request only',async t=>{
  let second=false;
  const page=boot(call=>{
    const answer=personAnswer(call);if(answer)return answer;
    if(call.path==='/api/staffer-route')return searching(second?'S2':'S1');
    return call.params.id==='S2'?finished('S2'):searching('S1');
  });
  t.after(page.stop);await page.ready();
  const first=page.submit();
  await until(()=>page.polls('S1').length>=1,'first search polling');
  second=true;
  const next=page.submit();
  await Promise.all([first,next]);
  const starts=page.starts();
  assert.equal(starts.length,2);
  assert.equal('supersedes' in starts[0].params,false);
  assert.equal(starts[1].params.supersedes,'S1');
  assert.equal(page.posts().length,0,'superseding replaces an explicit cancel');
  assert.ok(page.polls('S2').length>=1);
  assert.ok(page.calls.every(c=>c.path==='/api/staffer-route'||!('supersedes' in c.params)),'polls never carry supersedes');
  assert.match(page.$('route-status').textContent,/^Route 1 of 2/);
});

test('Avoid this person resubmits with supersedes while route 1 is still showing',async t=>{
  let resubmitted=false;
  const page=boot(call=>{
    const answer=personAnswer(call);if(answer)return answer;
    if(call.path==='/api/staffer-route')return searching(resubmitted?'S2':'S1');
    if(call.params.id==='S2')return finished('S2');
    return searching('S1',{partial_routes:[ROUTE1],progress:{routes_found:1,routes_requested:3}});
  });
  t.after(page.stop);await page.ready();
  const first=page.submit();
  await until(()=>page.buttons('Avoid this person').length>0,'partial route drawn with its avoid button');
  resubmitted=true;
  page.buttons('Avoid this person')[0].onclick();
  await first;
  await until(()=>page.starts().length===2,'resubmit');
  const [one,two]=page.starts();
  assert.equal('supersedes' in one.params,false);
  assert.equal(two.params.supersedes,'S1');
  assert.equal(two.params.avoid_people,'mid');
  assert.ok(one.params.holder&&two.params.holder===one.params.holder,'the resubmit names the same page');
  await page.elements['route-form'].lastSubmit;
  assert.match(page.$('route-status').textContent,/^Route 1 of 2/);
});

test('a search the server reports as cancelled stops polling and says so',async t=>{
  let polls=0;
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):
    ++polls<3?searching('S1',polls===2?{partial_routes:[ROUTE1],progress:{routes_found:1}}:{}):
    {ready:true,status:'cancelled',search_id:'S1',cancel_reason:'abandoned'}));
  t.after(page.stop);await page.ready();
  await page.submit();
  assert.equal(page.polls().length,3);
  assert.equal(page.$('route-status').textContent,'Search cancelled. Search again to find a route.');
  assert.equal(page.$('route-options').hidden,true);
  assert.ok(page.cleared.length>=1,'the half-drawn route is removed');
  assert.equal(page.$('route-submit').disabled,false);assert.equal(page.$('route-clear').textContent,'Clear');
  assert.deepEqual(page.posts(),[]);
  page.elements['route-clear'].onclick();
  assert.deepEqual(page.posts(),[],'a search the server already ended is not cancelled again');
});

test('closing the page cancels the running search at once with a keepalive request, once',async t=>{
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):searching('S1')));
  t.after(page.stop);await page.ready();
  assert.equal((page.windowListeners.pagehide||[]).length,1,'the page listens for pagehide');
  page.fire('pagehide');
  assert.deepEqual(page.posts(),[],'nothing is running, so there is nothing to cancel');
  const running=page.submit();
  await until(()=>page.polls('S1').length>=2,'two polls');
  page.fire('pagehide');
  const post=page.posts();
  assert.equal(post.length,1);
  assert.equal(post[0].path,'/api/staffer-route-cancel');
  assert.deepEqual(post[0].body,{id:'S1',holder:token(page)});
  assert.equal(post[0].headers['Content-Type'],'application/json');
  assert.equal(post[0].keepalive,true,'keepalive lets the request outlive the closing page');
  page.fire('pagehide');
  assert.equal(page.posts().length,1,'a second pagehide does not cancel again');
  page.elements['route-clear'].onclick();   // ends the polling loop of this test page
  await running;
});

test('a search that finished before the page closed is not cancelled',async t=>{
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):finished('S1')));
  t.after(page.stop);await page.ready();
  await page.submit();
  page.fire('pagehide');
  assert.deepEqual(page.posts(),[]);
});

// The page token: one random token per page load, sent with every start, supersede and cancel, so the server can
// tell this tab from another tab that asked for the identical search.
test('every start and supersede carries one token for the page, a token the server accepts',async t=>{
  let second=false;
  const page=boot(call=>{
    const answer=personAnswer(call);if(answer)return answer;
    if(call.path==='/api/staffer-route')return searching(second?'S2':'S1');
    return call.params.id==='S2'?finished('S2'):searching('S1');
  });
  t.after(page.stop);await page.ready();
  const first=page.submit();
  await until(()=>page.polls('S1').length>=1,'first search polling');
  second=true;
  await Promise.all([first,page.submit()]);
  const starts=page.starts();
  assert.equal(starts.length,2);
  const holder=starts[0].params.holder;
  assert.equal(typeof holder,'string');
  assert.ok(holder.length>=1&&holder.length<=64,'1 to 64 characters, as the server requires: '+holder);
  assert.equal(starts[1].params.holder,holder);
  assert.equal(starts[1].params.supersedes,'S1');
  assert.equal(page.posts().length,0,'a supersede is not an explicit cancel');
});

test('a search the server no longer knows is started again with the same token and without supersedes',async t=>{
  let polls=0;
  const page=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):
    ++polls===1?{ready:true,status:'search_not_found',search_id:'S1'}:finished('S1')));
  t.after(page.stop);await page.ready();
  await page.submit();
  const starts=page.starts();
  assert.equal(starts.length,2,'the lost search is started again');
  assert.equal('supersedes' in starts[1].params,false);
  assert.ok(starts[0].params.holder&&starts[1].params.holder===starts[0].params.holder);
});

test('two page loads get two different tokens',async t=>{
  const server=call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):finished('S1'));
  const one=boot(server),two=boot(server);
  t.after(one.stop);t.after(two.stop);
  await one.ready();await two.ready();
  await one.submit();await two.submit();
  assert.notEqual(token(one),token(two));
});

test('the token is built from the browser random source when there is one, and from a fallback when there is not',async t=>{
  const server=call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):finished('S1'));
  const bytes=boot(server,undefined,{crypto:{getRandomValues(array){for(let i=0;i<array.length;i++)array[i]=i*17%256;return array;}}});
  const broken=boot(server,undefined,{crypto:{getRandomValues(){throw new Error('no entropy source');}}});
  const absent=boot(server);   // the VM has no crypto at all
  for(const page of [bytes,broken,absent]){t.after(page.stop);await page.ready();await page.submit();}
  assert.equal(token(bytes),'p00112233445566778899aabb');
  for(const page of [broken,absent]){assert.match(token(page),/^p[0-9a-z]{2,40}$/);assert.ok(token(page).length<=64);}
});

// Typeahead: the server holds each /api/staffer-search answer until the test releases it.
function holdingTypeahead(){
  const held=[];
  const server=call=>{
    const answer=personAnswer(call);if(answer)return answer;
    if(call.path==='/api/staffer-search')return new Promise(resolve=>held.push(()=>resolve({ready:true,nodes:[person('p:'+call.params.q,'Name '+call.params.q)]})));
    return undefined;
  };
  return {held,server,searches:page=>page.calls.filter(c=>c.path==='/api/staffer-search')};
}
const type=(page,which,value)=>{page.$('route-'+which).value=value;page.$('route-'+which).listeners.input[0]();};

test('each keystroke aborts the typeahead request still in flight for that box',async t=>{
  const {held,server,searches}=holdingTypeahead();
  const page=boot(server,'');
  t.after(page.stop);
  type(page,'origin','ab');
  await until(()=>searches(page).length===1,'first typeahead request');
  const first=searches(page)[0];
  assert.equal(first.params.q,'ab');assert.equal(first.signal.aborted,false);
  type(page,'origin','abc');                       // the next keystroke, before the debounce has fired
  assert.equal(first.signal.aborted,true,'the previous request is aborted by the keystroke itself');
  await until(()=>searches(page).length===2,'second typeahead request');
  const second=searches(page)[1];
  assert.equal(second.params.q,'abc');assert.equal(second.signal.aborted,false);
  type(page,'origin','abcd');
  assert.equal(second.signal.aborted,true);
  await until(()=>searches(page).length===3,'third typeahead request');
  const third=searches(page)[2];
  assert.equal(third.signal.aborted,false);
  for(const release of held)release();             // late answers: only the last request may show its results
  for(let i=0;i<30;i++)await tick();
  const shownNames=page.$('origin-options').children.map(button=>button.children?.[0]?.textContent??button.textContent);
  assert.ok(page.$('origin-options').children.length<=1,'only the newest answer is rendered: '+JSON.stringify(shownNames));
  assert.equal(page.statusLog.filter(text=>/aborted/i.test(text)).length,0,'an aborted request shows no error');
  assert.equal(third.signal.aborted,false,'the answered request was never aborted');
});

test('the two boxes keep separate typeahead requests, and choosing a result aborts the pending one',async t=>{
  const {held,server,searches}=holdingTypeahead();
  const page=boot(server,'');
  t.after(page.stop);
  type(page,'origin','al');type(page,'target','bo');
  await until(()=>searches(page).length===2,'one request per box');
  const [origin,target]=['al','bo'].map(q=>searches(page).find(c=>c.params.q===q));
  assert.equal(origin.signal.aborted,false);assert.equal(target.signal.aborted,false,'typing in one box leaves the other alone');
  type(page,'origin','ali');
  assert.equal(origin.signal.aborted,true);assert.equal(target.signal.aborted,false);
  await until(()=>searches(page).length===3,'origin retried');
  const retry=searches(page)[2];
  held.splice(0).forEach(release=>release());
  for(let i=0;i<30;i++)await tick();
  assert.equal(retry.signal.aborted,false);
  assert.equal(target.signal.aborted,false);
});

test('an unreachable finished search and a search error behave as before',async t=>{
  const unreachable=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):
    {ready:true,found:false,status:'unreachable',search_id:'S1',optimal:true,nodes:[]}));
  t.after(unreachable.stop);await unreachable.ready();await unreachable.submit();
  assert.match(unreachable.$('route-status').textContent,/No route between these endpoints/);
  const failed=boot(call=>personAnswer(call)||(call.path==='/api/staffer-route'?searching('S1'):
    {ready:true,status:'search_error',search_id:'S1',error:'index unavailable'}));
  t.after(failed.stop);await failed.ready();await failed.submit();
  assert.equal(failed.$('route-status').textContent,'index unavailable');
});
