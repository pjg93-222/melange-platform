const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
test('routes, selections, neighborhoods and route focus stay with the full network renderer',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-live.js'),'utf8');
  const selection=source.slice(source.indexOf('  function select('),source.indexOf('  function matchesQuery('));
  const exports=source.slice(source.indexOf('  window.StafferLive='),source.indexOf('  setLayers(defaults);refresh();'));
  const calls=[];
  const state={nodes:new Map(),visible:[],routeNodes:new Map(),routeEdges:[],routeIds:new Set(),routeEdgeIds:new Set(),edges:[],personNodes:new Map(),personEdges:new Map(),searchIndex:new Map()};
  const a={id:'a',label:'A',kind:'person'},b={id:'b',label:'B',kind:'person'};
  state.nodes.set('a',a);state.nodes.set('unrelated-snapshot-person',{id:'unrelated-snapshot-person',kind:'person'});
  let selectedAddress;
  const context={state,Map,Set,Boolean,URL,location:{href:'http://localhost/staffer-live/?embedded=1&office=Example&route_target=a'},encodeURIComponent,history:{replaceState(a,b,url){selectedAddress=url;}},
    isPerson:n=>n?.kind==='person',canonicalId:v=>typeof v==='object'?v.id:v,enrichNode:n=>n,peopleMap:ns=>new Map(ns.map(n=>[n.id,n])),peopleEdges:es=>es,sanitizeRoute:d=>d,
    changeZoom(){},rebuild:()=>calls.push('rebuild'),draw(){},detail(){},results(){},refresh(){},screen(){},contentRect(){},
    fit:()=>{throw Error('Must not fit the old snapshot when the network renderer is available');},focusNeighborhood:()=>{throw Error('Must not switch to the snapshot neighborhood');},
    neighborhood:()=>[],fitZoom:()=>1,position:n=>n,layerOf:e=>e.layer,
    CustomEvent:class{},window:{dispatchEvent(){},matchMedia:()=>({matches:false}),StafferExplorer:{
      showRoute:d=>calls.push(['route',d.path]),selectPerson:id=>calls.push(['person',id]),showNeighborhood:()=>calls.push('neighborhood'),clearRoute:()=>calls.push('clear'),fitView:()=>calls.push('focus'),
      leave:()=>{throw Error('Must not leave the full network');}}}};
  vm.createContext(context);vm.runInContext(selection+exports,context);
  const live=context.window.StafferLive;
  live.showRoute({nodes:[a,b],path:['a','b'],edges:[{source:'a',target:'b'}]});
  assert.deepEqual(calls.at(-1),['route',['a','b']]);
  live.select('b',true);assert.deepEqual(calls.at(-1),['person','b']);
  const selectedQuery=new URL(selectedAddress,'http://localhost').searchParams;
  assert.equal(selectedQuery.get('embedded'),'1');assert.equal(selectedQuery.get('office'),'Example');assert.equal(selectedQuery.get('route_target'),'a');assert.equal(selectedQuery.get('person'),'b');
  live.showNeighborhood({coworkers:[a],edges:[{source:'a',target:'b'}]});assert.equal(calls.at(-1),'neighborhood');
  live.fitView();assert.equal(calls.at(-1),'focus');
  live.clearRoute();assert.equal(calls.at(-1),'clear');
  assert.equal(state.routeNodes.size,0);
});
