const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
test('a failed initial load discards its cache token and retries until a real snapshot is applied',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-live.js'),'utf8');
  const code=source.slice(source.indexOf('async function refresh()'),source.indexOf('  let searchTimer;'));
  const elements=new Map(),timers=[],requests=[];
  const state={request:0,query:'',etag:'old',etagQuery:'',snapshot:null,nodes:new Map(),routeNodes:new Map(),personEdges:new Map(),searchIndex:new Map(),identityAliases:new Map()};
  let requestNumber=0;
  const context={state,URL,AbortController,Map,Array,String,Error,MAX_NODES:1800,FULL_MAX_NODES:20000,location:{origin:'http://localhost'},defaults:[],
    $:id=>{if(!elements.has(id))elements.set(id,{textContent:'',replaceChildren(){this.textContent='';}});return elements.get(id);},
    text:(element,tag,value)=>element.textContent=value,
    setTimeout:(callback,delay)=>{timers.push({callback,delay});return timers.length;},clearTimeout:()=>{},
    metadata:()=>{},applyAliases:()=>{},peopleMap:()=>new Map(),peopleEdges:()=>[],setLayers:()=>{},rebuild:()=>{},
    window:{dispatchEvent:()=>{}},CustomEvent:class{},
    fetch:async(url,options)=>{
      requests.push(options.headers);requestNumber++;
      if(requestNumber===1)throw new Error('Connection interrupted');
      if(requestNumber===2)return {status:304};
      return {status:200,ok:true,headers:{get:()=> 'fresh'},json:async()=>({ready:true,revision:'verified',source_revision:'current',nodes:[],edges:[]})};
    }};
  vm.createContext(context);vm.runInContext(code,context);
  await context.refresh();
  assert.equal(state.etag,null);assert.equal(timers.at(-1).delay,3000);
  assert.equal(elements.get('connection').textContent,'Reconnecting…');
  await timers.at(-1).callback();
  assert.equal(state.snapshot,null);assert.equal(timers.at(-1).delay,2000);
  assert.equal(requests[1]['If-None-Match'],undefined);
  await timers.at(-1).callback();
  assert.equal(state.snapshot.revision,'verified');assert.equal(state.etag,'fresh');
  assert.equal(elements.get('errors').textContent,'');
  assert.match(elements.get('connection').textContent,/Live/);
});
test('the 30 s poll never cancels a snapshot request in flight; a request out for two minutes is replaced (r5, audit F-4)',async()=>{
  // Before: setInterval(refresh,30000) and refresh() aborts the previous request, so an answer slower than 30 s was
  // cancelled (net::ERR_ABORTED) and re-requested every 30 s and never landed.
  const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-live.js'),'utf8');
  assert.match(source,/setInterval\(poll,30000\);/,'the interval runs the poll, not refresh itself');
  const code=source.slice(source.indexOf('async function refresh()'),source.indexOf('  let searchTimer;'))+source.slice(source.indexOf('  function poll('),source.indexOf('  setLayers(defaults);refresh();'));
  let now=1000;const elements=new Map(),signals=[],pending=[];
  const state={request:0,query:'',etag:null,etagQuery:'',snapshot:null,nodes:new Map(),routeNodes:new Map(),personEdges:new Map(),searchIndex:new Map(),identityAliases:new Map()};
  const context={state,URL,AbortController,Map,Array,String,Error,Object,Promise,MAX_NODES:1800,FULL_MAX_NODES:20000,location:{origin:'http://localhost'},defaults:[],
    Date:{now:()=>now},
    $:id=>{if(!elements.has(id))elements.set(id,{textContent:'',replaceChildren(){this.textContent='';}});return elements.get(id);},
    text:(element,tag,value)=>element.textContent=value,setTimeout:()=>0,clearTimeout:()=>{},
    metadata:()=>{},applyAliases:()=>{},peopleMap:()=>new Map(),peopleEdges:()=>[],setLayers:()=>{},rebuild:()=>{},
    window:{dispatchEvent:()=>{}},CustomEvent:class{},
    fetch:(url,options)=>{signals.push(options.signal);return new Promise((resolve,reject)=>{pending.push({resolve});options.signal.addEventListener('abort',()=>{const e=new Error('aborted');e.name='AbortError';reject(e);});});}};
  vm.createContext(context);vm.runInContext(code,context);
  const first=context.refresh();                      // the page's first load; the server has not answered yet
  assert.equal(signals.length,1);
  now+=30000;context.poll();                          // the first poll tick
  assert.equal(signals.length,1,'no second request while the first is in flight');
  assert.equal(signals[0].aborted,false,'the request in flight is not cancelled');
  pending[0].resolve({status:200,ok:true,headers:{get:()=>'e1'},json:async()=>({ready:true,revision:'r1',source_revision:'s1',nodes:[],edges:[]})});
  await first;
  assert.equal(state.snapshot.revision,'r1','the slow answer lands');
  assert.equal(state.busySince,0);
  now+=30000;context.poll();                          // the next tick polls as before
  assert.equal(signals.length,2);
  now+=119000;context.poll();                         // still inside two minutes: left alone
  assert.equal(signals.length,2);assert.equal(signals[1].aborted,false);
  now+=2000;context.poll();                           // out for over two minutes: treated as lost and replaced
  assert.equal(signals.length,3);assert.equal(signals[1].aborted,true);
  assert.ok(state.busySince>0,'the replacement is the request in flight now');
});
