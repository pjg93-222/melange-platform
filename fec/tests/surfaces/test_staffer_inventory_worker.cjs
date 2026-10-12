const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-inventory-worker.js'),'utf8');
const row=(id,label,summary='',source='Quorum',kind='person')=>[id,label,summary,'','','',source,false,kind];
function harness(){
  const messages=[],pending=[];
  const context=vm.createContext({onmessage:null,postMessage:v=>messages.push(v),fetch:()=>new Promise(resolve=>pending.push(resolve))});
  vm.runInContext(source,context);
  return {messages,pending,send:data=>context.onmessage({data}),resolve:(i,people,extra={})=>pending[i]({ok:true,json:async()=>({ready:true,people,total:people.length,people_total:people.length,...extra})})};
}
(async()=>{
  const h=harness();
  const loading=h.send({type:'load',epoch:0,text:'',kind:'person',group:'member'});
  await h.send({type:'query',epoch:1,text:'Elissa Tew',kind:'person',source:'Quorum',group:'member'});
  h.resolve(0,[row('1','Elissa Tew'),row('2','Joshua Stewart','Communications Director · Elissa Slotkin'),row('3','Élissa R Tew')]);
  await loading;
  let page=h.messages.filter(m=>m.type==='page').at(-1);
  assert.equal(page.epoch,1);assert.deepEqual(Array.from(page.rows,r=>r[0]),['1','3']);
  await h.send({type:'view',epoch:1,key:'fixture',zoom:1,pan:{x:0,y:0},width:5000,height:5000});
  // Search narrows the list, never the map population.
  assert.equal(h.messages.at(-1).tiles.reduce((sum,t)=>sum+t.count,0),3);
  const old=h.send({type:'load',epoch:2,text:'',kind:'person'});
  const newest=h.send({type:'load',epoch:3,text:'',kind:'person'});
  h.resolve(2,[row('new','New record')]);await newest;
  h.resolve(1,[row('old','Obsolete record')]);await old;
  page=h.messages.filter(m=>m.type==='page').at(-1);
  assert.equal(page.epoch,3);assert.equal(page.rows[0][0],'new');
  const paged=h.send({type:'load',epoch:4,text:'',kind:'person'});
  h.resolve(3,Array.from({length:123},(_,i)=>row(String(i),'Person '+i)));await paged;
  const ids=[];
  for(let n=0;n<3;n++){await h.send({type:'page',page:n});ids.push(...h.messages.at(-1).rows.map(r=>r[0]));}
  assert.equal(new Set(ids).size,123);assert.equal(ids.length,123);
  await h.send({type:'query',epoch:5,text:'No matching source person',kind:'person'});
  assert.equal(h.messages.at(-1).total,0);
  const current=h.send({type:'load',epoch:6,text:'',kind:'person',employment:'current'});
  h.resolve(4,[[...row('current','Current staff'),true,'currently_listed'],[...row('payroll','Payroll staff'),false,'latest_payroll'],row('unknown','Historical source')]);await current;
  // Obsolete UI employment filters must not silently remove source records.
  assert.deepEqual(Array.from(h.messages.at(-1).rows,r=>r[0]),['current','payroll','unknown']);
  await h.send({type:'query',epoch:7,kind:'person',employment:'payroll'});
  assert.deepEqual(Array.from(h.messages.at(-1).rows,r=>r[0]),['current','payroll','unknown']);
  await h.send({type:'query',epoch:8,kind:'person',employment:''});
  assert.equal(h.messages.at(-1).total,3);
  const aliasLoad=h.send({type:'load',epoch:9,person:'old-id',kind:'person'});
  h.resolve(5,[[...row('canonical','Merged person'),true,'currently_listed','',[{kind:'email',value:'source@example.org'}]]],{revision:'new-generation',identity_aliases:{'old-id':'canonical'}});await aliasLoad;
  assert.equal(h.messages.findLast(m=>m.type==='ready').revision,'new-generation');
  assert.equal(h.messages.at(-1).id,'canonical');assert.equal(h.messages.at(-1).requestedPerson,'old-id');
  assert.equal(h.messages.at(-1).row[12][0].value,'source@example.org');
  const scoped=h.send({type:'load',epoch:10,scope:'network',kind:'person',group:'member'});
  h.resolve(6,[['network:1','Network person','','office','','','Payroll',false,'person',true,'currently_listed','',[],true],
    ['network:2','Second person','','office','','','Politico',false,'person',true,'currently_listed','',[],true]],
    {scope:'network',reconciliation:{network_people:2},identity_aliases:{old:'network:1'}});await scoped;
  assert.equal(h.messages.findLast(m=>m.type==='ready').reconciliation.network_people,2);
  assert.deepEqual({...h.messages.findLast(m=>m.type==='ready').network_source_counts},{Payroll:1,Politico:1});
  assert.equal(h.messages.findLast(m=>m.type==='page').total,2);
  await h.send({type:'locate',epoch:10,request:1,ids:['old','network:2','absent']});
  const located=h.messages.at(-1);assert.equal(located.type,'located');assert.equal(located.points.length,2);
  assert.equal(located.points[0].id,'network:1');assert.deepEqual(Array.from(located.missing),['absent']);
  await h.send({type:'view',epoch:10,key:'before-route',zoom:.1,pan:{x:0,y:0},width:5000,height:5000});
  assert.equal(h.messages.at(-1).tiles.reduce((sum,t)=>sum+t.count,0),2);
  await h.send({type:'query',epoch:11,scope:'network',text:'Second',kind:'person',group:'member'});
  assert.equal(h.messages.at(-1).total,1);
  await h.send({type:'locate',epoch:11,request:2,ids:['network:1','network:2']});
  // Route endpoints retain the same coordinates even outside the browse filter.
  assert.deepEqual(Array.from(h.messages.at(-1).points,p=>[p.id,p.x,p.y]),Array.from(located.points,p=>[p.id,p.x,p.y]));
  await h.send({type:'query',epoch:12,scope:'network',kind:'person',group:'member'});
  await h.send({type:'view',epoch:12,key:'after-route',zoom:.1,pan:{x:0,y:0},width:5000,height:5000});
  assert.equal(h.messages.at(-1).tiles.reduce((sum,t)=>sum+t.count,0),2);
  console.log('PASS: latest-query race, stale loads, name prefixes, accent folding, cluster conservation, complete pagination, empty results');
})().catch(error=>{console.error(error);process.exitCode=1;});
