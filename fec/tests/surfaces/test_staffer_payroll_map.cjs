const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-inventory-worker.js'),'utf8');
test('payroll route participants outside the browse population get stable positions without changing that population',async()=>{
  const messages=[],row=['casca','Mike Casca','','office','','','Politico',false,'person'];
  const context=vm.createContext({onmessage:null,postMessage:v=>messages.push(v),fetch:async()=>({ok:true,json:async()=>({ready:true,people:[row],people_total:1})})});
  vm.runInContext(source,context);const send=data=>context.onmessage({data});
  await send({type:'load',epoch:1,scope:'network'});
  const nodes=[['gordon','Gordon'],['hailey','Hailey'],['rene','Rene'],['casca','Mike Casca']].map(([id,label])=>({id,label,kind:'person',member_ids:['office']}));
  await send({type:'locate',epoch:1,request:1,ids:[...nodes.map(n=>n.id),'unknown','office'],nodes:[...nodes,{id:'office',kind:'office'}]});
  const located=messages.at(-1);
  assert.equal(located.points.length,4);assert.equal(located.points.filter(p=>p.overlay).length,3);
  assert.deepEqual(Array.from(located.missing),['unknown','office']);
  assert.ok(located.points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
  const points=new Map(located.points.map(p=>[p.id,p]));
  assert.equal(nodes.slice(1).filter((n,i)=>points.has(nodes[i].id)&&points.has(n.id)).length,3);
  await send({type:'query',epoch:2,text:'unmatched'});
  assert.equal(messages.at(-1).total,0);
  await send({type:'locate',epoch:2,request:2,ids:nodes.map(n=>n.id),nodes});
  assert.deepEqual(Array.from(messages.at(-1).points,p=>[p.id,p.x,p.y]),Array.from(located.points,p=>[p.id,p.x,p.y]));
  await send({type:'query',epoch:3,text:''});assert.equal(messages.at(-1).total,1);
  await send({type:'locate',epoch:3,request:3,ids:['gordon','casca']});
  assert.deepEqual(Array.from(messages.at(-1).points,p=>p.id),['casca']);
  assert.deepEqual(Array.from(messages.at(-1).missing),['gordon']);
});

test('clearing a route invalidates an outstanding position response',()=>{
  const explorer=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-explorer.js'),'utf8');
  const view={routeNodes:new Map([['a',{id:'a',kind:'person'}]]),nodes:new Map(),selected:null},messages=[];
  const context=vm.createContext({locationRequest:0,requestedFit:null,highlightPoints:[],neighborIds:[],loaded:true,epoch:1,
    live:{viewport:()=>view,redraw(){}},worker:{postMessage:m=>messages.push(m)}});
  vm.runInContext(explorer.slice(explorer.indexOf('  function locateHighlights('),explorer.indexOf('  function fitHighlights(')),context);
  context.locateHighlights('route');const stale=messages.at(-1).request;
  view.routeNodes.clear();context.locateHighlights();
  assert.ok(context.locationRequest>stale);assert.equal(context.requestedFit,null);
  assert.equal(context.highlightPoints.length,0);
  context.data={type:'located',request:stale,points:[{id:'a',x:1,y:2}]};
  const handler=explorer.slice(explorer.indexOf("    if(data.type==='located'"),explorer.indexOf('    // An id with no row'));
  vm.runInContext(handler,context);assert.equal(context.highlightPoints.length,0);
});
