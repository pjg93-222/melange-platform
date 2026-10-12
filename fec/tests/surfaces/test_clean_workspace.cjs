const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../surfaces/approved/clean/app.js'),'utf8');
function harness(search=''){
 const saved=new Map(),events={},menu={},location={search},requests=[];
 const c={window:{addEventListener(){}},document:{querySelector:s=>s==='#menu'?menu:null,addEventListener:(name,fn)=>events[name]=fn},location,history:{replaceState(a,b,u){location.search=u.slice(u.indexOf('?'));},pushState(a,b,u){location.search=u.slice(u.indexOf('?'));}},URL,URLSearchParams,performance,console,setTimeout,clearTimeout,localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},fetch:async u=>{requests.push(u);return {ok:true,json:async()=>({})};}};
 vm.createContext(c);vm.runInContext(code,c);return {c,u:c.window.ML,saved,requests,events};
}
test('restored engines never receive attachment handlers for discarded layouts',async()=>{
 const {c,u,events}=harness('?view=coalition');
 const root={innerHTML:'',className:'',dataset:{},setAttribute(){},removeAttribute(){},querySelector(){return null;}};
 root.classList={contains:name=>root.className.split(' ').includes(name)};
 const nodes={'#content':root,'#navlinks':{},'#announcer':{}};
 c.document.querySelector=s=>nodes[s]||null;c.document.body={};
 c.fetch=async()=>({ok:true,json:async()=>({members:[],icons:{Members:''},cats:[]})});
 u.pages.coalition=()=>{throw Error('Discarded page renderer invoked');};
 u.pages.coalition.attach=()=>{throw Error('Discarded attach invoked');};
 await events.DOMContentLoaded();
 assert.match(root.innerHTML,/original-desk\.html/);assert.match(root.innerHTML,/deskview=coalition/);
 assert.doesNotMatch(root.innerHTML,/could not load/);
});
test('restored cross-page links preserve original bill and organization selections',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../../surfaces/approved/clean/restored-links.js'),'utf8');
 let click;const c={URL,URLSearchParams,window:{},parent:{location:{}},location:{href:'http://localhost/staffer-live/approved/original-industry.html',origin:'http://localhost'},document:{addEventListener:(type,fn)=>click=fn}};
 vm.createContext(c);vm.runInContext(source,c);
 for(const [href,view,key,value]of [['http://localhost/lobby-network.html#org=org%3A1','industry','org','org:1'],['http://localhost/lobby-forecast.html#bill=119%3Ahr%3A1','forecasts','bill','119:hr:1']]){
  click({target:{closest:()=>({href})},button:0,preventDefault(){}});
  const result=new URL(c.parent.location.href,'http://localhost');assert.equal(result.searchParams.get('view'),view);assert.equal(result.searchParams.get(key),value);assert.equal(new URLSearchParams(result.hash.slice(1)).get(key),value);
 }
});
test('selection follows visible page and clears unrelated filtered evidence',()=>{
 const {u}=harness('?selected=Member8');const rows=Array.from({length:12},(_,i)=>({name:'Member'+i}));
 assert.equal(u.pageSelection(rows,7).name,'Member8');assert.equal(u.pagination,1);
 assert.equal(u.pageSelection([],7),undefined);assert.equal(u.pagination,0);
 u.patchURL({selected:''});u.pagination=1;assert.equal(u.pageSelection(rows,7).name,'Member7');
});
test('shrinking result sets clamp pagination without phantom records',()=>{
 const {u}=harness();u.pagination=20;assert.equal(u.pageSelection([{name:'Only'}],6).name,'Only');assert.equal(u.pagination,0);
});
test('records load lazily, share concurrent fetches and can retry failed loads',async()=>{
 const {u,c,requests}=harness();assert.equal(requests.length,0);await Promise.all([u.data('member-1'),u.data('member-1')]);assert.equal(requests.length,1);
 let attempts=0;c.fetch=async()=>{attempts++;return {ok:attempts>1,status:503,json:async()=>({ok:true})};};
 await assert.rejects(u.data('bills'),/503/);assert.equal((await u.data('bills')).ok,true);assert.equal(attempts,2);
});
test('manual saves preserve other members and notes and expose failed writes',()=>{
 const {u,c,saved}=harness();saved.set('mdesk_manual_v1',JSON.stringify({One:{note:'Keep note'},Two:{note:'Untouched'}}));
 u.saveManual('One',{tracker:{owner:'Owner'}});assert.equal(u.manual('One').note,'Keep note');assert.equal(u.manual('Two').note,'Untouched');
 const before=saved.get('mdesk_manual_v1');c.localStorage.setItem=()=>{throw Error('Quota');};assert.throws(()=>u.saveManual('One',{note:'Draft'}),/Quota/);assert.equal(saved.get('mdesk_manual_v1'),before);
});
test('cosponsorship uses member joining date, never latest bill activity',async()=>{
 const {u,c}=harness('?period=2026-01-01');c.fetch=async()=>({ok:true,json:async()=>({bills:{'119:hr:1':{introduced_date:'2025-01-03',latest_action:{date:'2026-09-01'},cosponsors:[{bioguide:'B1',date:'2025-03-01'}]}}})});
 const member={bio:{bioguide:'B1'},bills:[{bill:'H.R.1',congress:119,role:'cosponsor',title:'Example',url:'https://www.congress.gov/bill/119th-congress/house-bill/1'}]};
 assert.equal((await u.records(member,false))[0].date,'2025-03-01');assert.equal((await u.records(member)).length,0);
});
test('navigation carries research scope and selected bill',()=>{
 const {u}=harness('?issue=Payments&congress=119&period=2026-01-01&bill=119%3Ahr%3A1');const q=new URL(u.url('profile',{m:'Example'}),'http://localhost').searchParams;
 assert.equal(q.get('issue'),'Payments');assert.equal(q.get('bill'),'119:hr:1');assert.equal(q.get('m'),'Example');
});
