const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../surfaces/staffer_live/melange-dashboard.js'), 'utf8');
test('member evidence bridge reads the selected profile without changing records',()=>{
  const adapter=fs.readFileSync(path.join(__dirname,'../../surfaces/melange_dashboard.py'),'utf8');
  const bridge=adapter.match(/window\.__melangeMemberEvidence=function\(name\)\{[\s\S]*?\n    \};/)[0];
  const records=[{name:'One',bills:[{bill:'H.R.1',role:'original'}],articles:[{title:'Record',exclusion:'out of scope'}],refs:[]}];
  const before=JSON.stringify(records),context={M:records,window:{__profilesReady:false}};vm.createContext(context);vm.runInContext(bridge,context);
  assert.equal(context.window.__melangeMemberEvidence('One'),null);
  context.window.__profilesReady=true;
  assert.equal(context.window.__melangeMemberEvidence('Missing'),null);
  assert.equal(context.window.__melangeMemberEvidence('One').articles[0].exclusion,'out of scope');
  assert.equal(context.window.__melangeMemberEvidence('One').bills[0].role,'original');
  assert.equal(JSON.stringify(records),before);
});
test('local graph fit contains every finite point and leaves the topology unchanged',()=>{
  const adapter=fs.readFileSync(path.join(__dirname,'../../surfaces/melange_dashboard.py'),'utf8');
  const fit=adapter.match(/fitted = b'''(function fit\(\)\{[\s\S]*?)\n    window\.__melangeFitInfluence/)[1];
  const points={LX:[-200,300,1000,NaN],LY:[-100,500,900,NaN]},before=JSON.stringify(points);
  const context={...points,W:640,H:270,cam:{}};vm.createContext(context);vm.runInContext(fit+';fit();',context);
  for(let i=0;i<3;i++){const x=points.LX[i]*context.cam.s+context.cam.tx,y=points.LY[i]*context.cam.s+context.cam.ty;assert.ok(x>=20&&x<=620);assert.ok(y>=15&&y<=240);}
  assert.equal(JSON.stringify(points),before);
  context.W=0;vm.runInContext('fit()',context);assert.ok(Number.isFinite(context.cam.s));
});
function harness(url = 'http://localhost/?v=staffer') {
  const location = new URL(url), navigations = [], frames = [];
  const panel = {hidden:false, children:[]};
  const history = {replaceState(a,b,p){Object.assign(location, new URL(p,location)); location.href=new URL(p,location).href;},pushState(a,b,p){this.replaceState(a,b,p);}};
  const context = {URL,URLSearchParams,location,history,Boolean,String,setTimeout,
    document:{getElementById:id=>id==='app-staffer'?panel:null},window:{gotoStaffer:(...args)=>navigations.push(args)},
    fetch:async()=>({ok:true,json:async()=>({person_id:'person:42',route_available:true})}),
    add(parent,tag){
      const frame={contentWindow:{location:new URL('about:blank')},getAttribute(name){return name==='src'?this._src:null;},
        set src(value){this._src=value;this.contentWindow.location=new URL(value,location);frames.push(value);}};
      parent.children.push(frame);return frame;
    }};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  let frame='),source.indexOf('  const memberPanel=')),context);
  return {context,frames,navigations,panel};
}
test('staffer deep links retain destination and source-only state on first open',()=>{
  const {context,frames}=harness('http://localhost/?v=staffer&person=person:1&route_target=person:2&source_only=1');
  context.window.__stafferOpen('Example');
  const query=new URL(frames[0],'http://localhost').searchParams;
  assert.equal(query.get('person'),'person:1');assert.equal(query.get('route_target'),'person:2');
  assert.equal(query.get('source_only'),'1');assert.equal(query.get('office'),'Example');
});
test('same view reuses iframe; Back to empty view clears stale person and target',()=>{
  const {context,frames}=harness();
  context.openPeople(null,'person:1','person:2',false);
  context.openPeople(null,'person:1','person:2',false);
  assert.equal(frames.length,1);
  context.openPeople(null,null,null,false);
  assert.equal(frames.length,2);assert.equal(frames[1],'/staffer-live/?embedded=1');
});
test('changing source-only status reloads the requested evidence view',()=>{
  const {context,frames}=harness();
  context.openPeople(null,'person:1',null,true);
  context.openPeople(null,'person:1',null,false);
  assert.equal(frames.length,2);assert.ok(!frames[1].includes('source_only'));
});
test('sidebar return resumes the existing person and office without replacing the frame',()=>{
  const {context,frames,navigations}=harness();
  context.openPeople('Example','person:1','person:2',false);
  context.location.href='http://localhost/?v=industry';
  context.resumePeople();
  context.window.__stafferOpen('Example');
  assert.equal(frames.length,1);
  assert.equal(context.location.searchParams.get('person'),'person:1');
  assert.equal(context.location.searchParams.get('route_target'),'person:2');
  assert.equal(context.location.searchParams.get('s'),'Example');
  assert.equal(navigations.length,1);
});
test('member staffer link clears the conflicting member address',async()=>{
  const {context,navigations}=harness('http://localhost/?m=Example');
  await context.window.__melangeOpenStaffer({office:'Example',sourceId:'quorum:7'});
  assert.equal(context.location.searchParams.get('m'),null);
  assert.equal(context.location.searchParams.get('v'),'staffer');
  assert.equal(context.location.searchParams.get('person'),'person:42');
  assert.equal(context.location.searchParams.get('route_target'),'person:42');
  assert.equal(navigations.length,1);
});

test('a resolver HTTP failure remains inspectable and is distinguished from missing identity',async()=>{
  const {context}=harness('http://localhost/?m=Example');
  context.fetch=async()=>({ok:false,status:500});
  await context.window.__melangeOpenStaffer({office:'Example',sourceId:'quorum:7'});
  assert.equal(context.window.__melangeStafferResolution.failed,true);
  assert.equal(context.window.__melangeStafferResolution.sourceId,'quorum:7');
  assert.equal(context.location.searchParams.get('person'),'quorum:7');
  assert.equal(context.location.searchParams.get('source_only'),'1');
  assert.equal(context.location.searchParams.get('route_target'),null);
});

test('a malformed resolver body retains the source identity and recovery state',async()=>{
  const {context}=harness();context.fetch=async()=>({ok:true,json:async()=>null});
  await context.window.__melangeOpenStaffer({sourceId:'quorum:7'});
  assert.equal(context.window.__melangeStafferResolution.failed,true);
  assert.equal(context.location.searchParams.get('person'),'quorum:7');
});

test('retry clears the resolver failure and restores a supported route',async()=>{
  const {context}=harness();
  context.fetch=async()=>{throw new Error('offline');};
  await context.window.__melangeOpenStaffer({sourceId:'quorum:7'});
  assert.equal(context.window.__melangeStafferResolution.failed,true);
  context.fetch=async()=>({ok:true,json:async()=>({person_id:'person:42',route_available:true})});
  await context.window.__melangeOpenStaffer({sourceId:'quorum:7'});
  assert.equal(context.window.__melangeStafferResolution.failed,false);
  assert.equal(context.location.searchParams.get('route_target'),'person:42');
  assert.equal(context.location.searchParams.get('source_only'),null);
});

test('a successful source-only lookup is not described as a service failure',async()=>{
  const {context}=harness();
  context.fetch=async()=>({ok:true,json:async()=>({person_id:null,route_available:false,status:'source_only'})});
  await context.window.__melangeOpenStaffer({sourceId:'quorum:7'});
  assert.equal(context.window.__melangeStafferResolution.failed,false);
  assert.equal(context.location.searchParams.get('source_only'),'1');
});
