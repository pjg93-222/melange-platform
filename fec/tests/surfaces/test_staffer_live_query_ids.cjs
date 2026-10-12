// D-083 in the map's query filter: matchesQuery() in staffer-live.js (the page's query filter and the legacy result list) never matches pieces of an
// identifier. The words of a query are looked for only in what a person reads (the name and the source summary). The node id, the bare uuid of a
// politico:<uuid> id and the member, committee and policy ids match only when the whole query is that identifier (trimmed, in any case).
// The real page code runs in a vm over a small fake DOM, so both callers (rebuild() and results()) are exercised as well as the predicate. The
// code as it was before the change is read from git as the negative control, and eleven deliberate bugs of the new code are each caught.
// The committee ids below are not of the shape container:office:<chamber>:<name>. The name inside that shape is searched by way of the id, and the prefix before it
// is not: see test_staffer_live_query_committee_names.cjs.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const REL='fec/surfaces/staffer_live/staffer-live.js';
const ROOT=path.resolve(__dirname,'../../..');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-live.js'),'utf8');

// The file as it was before the follow-up (the tip of the route latency branch, 86631c454): D-083 had not reached this function.
const BEFORE='86631c454815533798eb1ad81136ddb1a76a23b9';
function fromGit(rev,rel){
  try{return execFileSync('git',['-C',ROOT,'show',rev+':'+rel],{encoding:'utf8',stdio:['ignore','pipe','ignore'],maxBuffer:1<<25});}catch(error){return null;}
}
const before=fromGit(BEFORE,REL);
const NO_GIT=before===null?'the pre-change file (commit '+BEFORE.slice(0,9)+') is not in this checkout':false;

// ---- the page, with a fake DOM. Nothing is drawn (requestAnimationFrame never fires) and nothing is fetched (the first request never answers).
function page(code){
  const elements=new Map();
  const ctx=new Proxy({},{get:(target,key)=>key in target?target[key]:()=>({width:0}),set:(target,key,value)=>{target[key]=value;return true;}});
  const element=id=>({id,children:[],textContent:'',className:'',hidden:false,disabled:false,title:'',value:'',style:{},dataset:{},
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;this.textContent='';},
    addEventListener(){},setPointerCapture(){},setAttribute(){},scrollIntoView(){},getContext:()=>ctx,
    getBoundingClientRect:()=>({left:0,top:0,right:1000,bottom:700,width:1000,height:700})});
  const document={getElementById:id=>{if(!elements.has(id))elements.set(id,element(id));return elements.get(id);},createElement:tag=>element(tag)};
  const window={dispatchEvent(){},devicePixelRatio:1};
  const context=vm.createContext({document,window,history:{replaceState(){}},location:{href:'http://localhost/staffer-live/',origin:'http://localhost'},
    fetch:()=>new Promise(()=>{}),setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,requestAnimationFrame:()=>0,performance:{now:()=>0},
    ResizeObserver:class{observe(){}},CustomEvent:class{constructor(type,init){this.type=type;this.detail=init&&init.detail;}},URL,AbortController});
  // The closure's own functions and state, exported by one line added before the page starts itself.
  const exported=code.replace(/^(\s*setLayers\(defaults\);refresh\(\);setInterval\(poll,30000\);)/m,'  window.__test={matchesQuery,results,rebuild,state};\n$1');
  assert.notEqual(exported,code,'the page must end by starting itself, so the export line has a place to go');
  vm.runInContext(exported,context);
  const t=window.__test;
  const load=(nodes,{edges=[]}={})=>{t.state.nodes.clear();for(const n of nodes)t.state.nodes.set(n.id,n);t.state.edges=edges;t.state.searchIndex.clear();};
  // The page's own predicate over every node, in node order, for the query exactly as given (untrimmed: matchesQuery trims it itself).
  const find=q=>{t.state.query=q;return Array.from(t.state.nodes.values()).filter(n=>t.matchesQuery(n)).map(n=>n.id);};
  // The legacy result list (results()): the label of every listed button, first page, and the count in its title.
  const listed=q=>{
    t.state.query=q.trim();t.state.resultPage=0;t.results();
    const out=document.getElementById('results').children.filter(b=>b.className!=='empty').map(b=>b.textContent);
    return {labels:out,count:Number(/\((\d+)\)/.exec(document.getElementById('results-title').textContent)[1])};
  };
  // The map's bounded sample (rebuild()): with one enabled edge between two people no query reaches, the others are visible only if the query matches them.
  const visible=q=>{t.state.query=q.trim();t.rebuild();return Array.from(t.state.visible,n=>n.id).filter(id=>!id.startsWith('edge:')).sort();};
  return {t,find,listed,visible,load,document,window};
}

// ---- a hand-written world: every expectation below is written out by hand
const UUID_A='3f2a1b4c-5d6e-4f80-9a0b-c1d2e3f4a5b6',UUID_B='9c8d7e6f-0a1b-4c2d-8e3f-4a5b6c7d8e9f',UUID_C='e5f60718-293a-4b4c-9d5e-6f708192a3b4';
const person=(id,label,o={})=>({id,kind:'person',label,source_summary:o.summary,member_ids:o.member||[],committee_ids:o.committee||[],policy_ids:o.policy||[]});
const FIXTURE=[
  person('decoy:1','Staff Ada 5'),                                                                                                                       // 0
  person('politico:'+UUID_A,'Casey Rivera',{summary:'Chief of Staff · House Ways Means · last seen 2024-05-01',member:['A000370'],committee:['HSBA'],policy:['BANKING']}), // 1
  person('politico:'+UUID_B,'Jordan Lee',{summary:'Legislative Director · Office of Rep. Hale',member:['A000370']}),                                    // 2
  person('politico:'+UUID_C,'Morgan Quill',{member:['S001223'],committee:['container:hsag']}),                                                          // 3
  person('hill:person:alex-adams','Alex Adams',{committee:['HSBA']}),                                                                                   // 4
  person('Quorum:Staff-0042','Priya Nair',{policy:['BANKING','policy:tax']}),                                                                           // 5
  person('lda:9917','Pat Orwell'),                                                                                                                      // 6
  {id:'person:3f2a1b4c5d6e',kind:'person',name:'Zed Zeta',member_ids:[],committee_ids:[],policy_ids:[]},                                                // 7: a name and no label
  {id:'quorum:77',kind:'person',member_ids:[],committee_ids:[],policy_ids:[]},                                                                          // 8: neither: the page's label() falls back to the id
  person('staff:ada-5','Ada Five',{member:[null,'X000001'],policy:[7]}),                                                                                // 9: a null and a number among the ids
];
const ids=list=>list.map(i=>FIXTURE[i].id);
// Two people joined by one enabled edge and named by nothing a query can reach: in the map's sample they are always visible, every other person only if the query matches.
const EDGE_NODES=['edge:a','edge:b'].map(id=>({id,kind:'person',label:'',member_ids:[],committee_ids:[],policy_ids:[]}));
const alt=s=>[...s].map((c,i)=>i%2?c.toUpperCase():c.toLowerCase()).join('');
const variants=id=>[id,id.toUpperCase(),id.toLowerCase(),alt(id),'  '+id+'\t\n',' '+id.toUpperCase()+' '];

// Pieces of identifiers that no name or summary holds: before the ruling each found nodes through the id, now none does.
// Among them are the part after a prefix other than politico: (3f2a1b4c5d6e, staff-0042): only a politico:<uuid> id has a bare form.
const ID_FRAGMENTS=['po','pol','poli','politico','politico:','politico:3f2a','3f2a','3f2a1b4c','5d6e','4f80','9a0b','c1d2e3f4','9c8d7e6f','e5f60718','hill','hill:person','person',
  'person:lda','0042','lda','lda:99','9917','quorum:7','77','3f2a1b4c5d6e','3F2A1B4C5D6E','person:3f2a1b4c','staff-0042'];
// Pieces of member, committee and policy ids, and strings that merely contain a whole one.
const CODE_FRAGMENTS=['a0','a00','a000','a0003','a000370x','s001','s0012','x000','x00000','hsb','hs','container','container:hs','hsag','bank','banki','polic','policy','policy:','tax','null','undefined'];
// Whole identifiers of the three kinds and the nodes that hold them.
const CODES=[['A000370',[1,2]],['HSBA',[1,4]],['container:hsag',[3]],['BANKING',[1,5]],['policy:tax',[5]],['S001223',[3]],['X000001',[9]]];

const RULING={
  async idFragments(p){for(const q of ID_FRAGMENTS)assert.deepEqual(p.find(q),[],'a piece of an id must find nothing by way of the id: '+q);},
  async codeFragments(p){for(const q of CODE_FRAGMENTS)assert.deepEqual(p.find(q),[],'a piece of a member, committee or policy id must find nothing by way of it: '+q);},
  async wholeIds(p){for(const n of FIXTURE)for(const q of variants(n.id))assert.deepEqual(p.find(q),[n.id],'the whole id '+JSON.stringify(q)+' must find its node');},
  async bareUuids(p){
    for(const [n,uuid] of [[1,UUID_A],[2,UUID_B],[3,UUID_C]])for(const q of variants(uuid))assert.deepEqual(p.find(q),[FIXTURE[n].id],'the bare uuid '+JSON.stringify(q)+' must find its node');
  },
  async wholeCodes(p){for(const [code,nodes] of CODES)for(const q of variants(code))assert.deepEqual(p.find(q),ids(nodes),'the whole id '+JSON.stringify(q)+' finds the nodes attached to it');},
  // The words of a query still match names and summaries, anywhere in them, in any case, all words required.
  async text(p){
    const cases=[['casey',[1]],['CASEY riv',[1]],['rivera casey',[1]],['sey',[1]],['alex adams',[4]],['chief of staff',[1]],['ways means',[1]],['seen 2024',[1]],['legislative',[2]],['office of rep',[2]],
      ['zed',[7]],['ZETA',[7]],['ada',[0,4,9]],['ada five',[9]],['casey chief',[1]],['casey nobody',[]],['zzz',[]],['   ',[]],['',[]]];
    for(const [q,want] of cases)assert.deepEqual(p.find(q),ids(want),'unchanged: '+JSON.stringify(q));
  },
  // A node with neither a label nor a name is named by its id for display only: the id is not text to search by pieces, but the whole id still finds it.
  async labelFallback(p){
    for(const q of ['quorum','77','quorum:7','uorum:77'])assert.deepEqual(p.find(q),[],'a node without a label is not searched by pieces of its id: '+q);
    assert.deepEqual(p.find('QUORUM:77'),[ 'quorum:77']);
  },
  // The server's own flag is honoured when the answer was for this very query.
  async flag(p){
    p.t.state.snapshot={search:{query:'Elsewhere'}};FIXTURE[6].is_search_match=true;FIXTURE[7].is_search_match=false;
    try{
      assert.deepEqual(p.find('elsewhere'),['lda:9917'],'a flagged node matches the query the server answered');
      assert.deepEqual(p.find('  ELSEWHERE '),['lda:9917'],'... in any case and trimmed');
      assert.deepEqual(p.find('elsewhere again'),[],'... but not another query');
      p.t.state.snapshot=null;
      assert.deepEqual(p.find('elsewhere'),[],'... and not when no answer is held');
    }finally{p.t.state.snapshot=null;delete FIXTURE[6].is_search_match;delete FIXTURE[7].is_search_match;}
  },
  // A null among the ids is skipped, not turned into the word "null"; a number is an id like any other.
  async oddIds(p){
    for(const q of ['null','undefined','nan'])assert.deepEqual(p.find(q),[],'a null among the ids is not a word: '+q);
    assert.deepEqual(p.find('7'),ids([9]),'a number among the ids is an id like any other');
  },
  // Both callers: the legacy list shows exactly the matching people in node order, the map's sample holds exactly the matching people.
  async callers(p){
    const fixed=[['politico',[]],['3f2a',[]],[UUID_A,[1]],[UUID_B.toUpperCase(),[2]],['casey',[1]],['A000370',[1,2]],['a00',[]],['hsb',[]],['  hsba ',[1,4]],['banking',[1,5]],['zed zeta',[7]],['quorum:77',[8]],['quorum',[]],['ada',[0,4,9]]];
    p.load(FIXTURE.concat(EDGE_NODES),{edges:[{source:'edge:a',target:'edge:b',layer:'co_tenure'}]});
    p.t.state.snapshot=null;
    for(const [q,want] of fixed){
      const names=want.map(i=>FIXTURE[i].label||FIXTURE[i].name||FIXTURE[i].id);
      const shown=p.listed(q);
      assert.deepEqual(shown.labels,names,'the legacy list for '+JSON.stringify(q));assert.equal(shown.count,want.length);
      assert.deepEqual(p.visible(q),ids(want).slice().sort(),'the map sample for '+JSON.stringify(q));
    }
    p.load(FIXTURE);
  },
};
const newPage=code=>{const p=page(code);p.load(FIXTURE);return p;};
const outcomes=async p=>{
  const out={};
  for(const [name,check] of Object.entries(RULING)){try{await check(p);out[name]='pass';}catch(e){if(!(e instanceof assert.AssertionError))throw e;out[name]='fail';}}
  return out;
};
const ALL_PASS=Object.fromEntries(Object.keys(RULING).map(k=>[k,'pass']));

for(const name of Object.keys(RULING))test('ruling: '+{
  idFragments:'a piece of a node id, a politico id or a uuid finds nothing by way of the id',
  codeFragments:'a piece of a member, committee or policy id finds nothing by way of it',
  wholeIds:'a whole node id finds its node, trimmed and in any case',
  bareUuids:'the bare uuid of a politico id is a whole id, and no other prefix has a bare form',
  wholeCodes:'a whole member, committee or policy id finds the nodes attached to it, trimmed and in any case',
  text:'names and summaries are still searched by pieces, in any case, all words required',
  labelFallback:'a node without a label is not searched by pieces of the id its label falls back to',
  flag:'the server flag is honoured for the query it answered and for no other',
  oddIds:'a null among the ids is skipped and a number is an id like any other',
  callers:'the legacy list and the map sample follow the same rule',
}[name],async()=>RULING[name](newPage(source)));

// ---- a seeded world: every answer equals the ruling written out by brute force, for every 1- and 2-character query and sampled names and pieces of ids
const mulberry32=a=>()=>{a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};
const hex=(rand,n)=>Array.from({length:n},()=>'0123456789abcdef'[Math.floor(rand()*16)]).join('');
const uuid=rand=>[8,4,4,4,12].map(n=>hex(rand,n)).join('-');
function world(rand,count){
  const pick=list=>list[Math.floor(rand()*list.length)];
  const given=['Alex','Casey','Jordan','Morgan','Priya','Pat','Zed','Beatrice','Cole','Élodie','Sam','Dana','Ruth'],family=['Adams','Rivera','Lee','Quill','Nair','Orwell','Zeta','Hale','Porter','Gagné','Stone','Marsh','Ode'];
  const orgs=['House Ways Means','Senate Banking','Office of Rep. Hale','Quorum Strategies LLC','Acme Lobbying Group','Committee on Finance'];
  const shapes=[()=>'politico:'+uuid(rand),()=>'politico:'+uuid(rand),()=>'hill:person:'+pick(['alex-adams','zed-zeta','pat-orwell'])+'-'+Math.floor(rand()*40),
    ()=>'person:lda:lobbyist:'+Math.floor(rand()*9000),()=>'quorum:'+Math.floor(rand()*900),()=>'person:'+hex(rand,12),()=>'Mixed:Case-'+Math.floor(rand()*40)];
  const seen=new Set(),nodes=[];
  while(nodes.length<count){
    const id=pick(shapes)();if(seen.has(id))continue;seen.add(id);
    const name=pick(given)+' '+pick(family),n={id,kind:'person',member_ids:[],committee_ids:[],policy_ids:[]};
    const style=rand();
    if(style<.08)n.name=name;else if(style>=.13)n.label=name;                         // 5%: neither a label nor a name
    if(rand()<.5)n.source_summary=pick(['Director','Counsel','Chief of Staff'])+' · '+pick(orgs)+' · last reported 20'+(10+Math.floor(rand()*15))+'-0'+(1+Math.floor(rand()*9))+'-15';
    if(rand()<.4)n.member_ids.push(pick(['A000370','S001223','V000127','M001204']));
    if(rand()<.1)n.member_ids.push(pick(['A000370','S001223','V000127','M001204']));
    if(rand()<.2)n.committee_ids.push(pick(['HSBA','SSFI','container:hsag','container:ssfi']));
    if(rand()<.2)n.policy_ids.push(pick(['BANKING','policy:tax','policy:health',3]));
    if(rand()<.03)n.member_ids.unshift(null);
    nodes.push(n);
  }
  // A decoy whose label is another person's id: it is found by its text, the person by the whole id.
  nodes[7].label=nodes[3].id;
  return nodes;
}
function queriesFor(nodes){
  const out=new Set(),alphabet='abcdefghijklmnopqrstuvwxyz0123456789:-. ';
  for(const a of alphabet){out.add(a);for(const b of alphabet)out.add(a+b);}
  for(const q of ['A0','Po','HS','S0','Ba','TA','  a  ',' po','po '])out.add(q);
  for(let i=0;i<nodes.length;i+=Math.max(1,Math.floor(nodes.length/70))){
    const n=nodes[i],id=n.id,text=n.label||n.name||'';
    for(const v of variants(id))out.add(v);
    if(id.startsWith('politico:'))for(const v of variants(id.slice(9)))out.add(v);
    for(let k=1;k<=Math.min(id.length,14);k+=2)out.add(id.slice(0,k));
    for(let k=2;k<=8;k+=3){out.add(id.slice(-k));out.add(id.slice(5,5+k));}
    for(const group of id.replace('politico:','').split(/[-:]/))for(let k=2;k<=Math.min(group.length,9);k+=2)out.add(group.slice(0,k));
    for(const code of [...n.member_ids,...n.committee_ids,...n.policy_ids].filter(c=>c!==null)){
      for(const v of variants(String(code)))out.add(v);
      for(let k=1;k<String(code).length;k++){out.add(String(code).slice(0,k));out.add(String(code).slice(k));}
    }
    out.add(text);out.add(text.toUpperCase());out.add(text.split(' ')[0].slice(0,3));out.add(text.split(' ').reverse().join(' '));
    if(n.source_summary){out.add(n.source_summary.split(' · ')[1]);out.add(n.source_summary.split(' · ')[0].toLowerCase());}
  }
  return [...out];
}
// Brute force, independent of the page: a node matches when the whole trimmed query is one of its identifiers, or every word of the query occurs in its text.
function ruled(nodes){
  return nodes.map(n=>{
    const identifiers=new Set([String(n.id)]);
    const bare=/^politico:(.+)$/i.exec(String(n.id));if(bare)identifiers.add(bare[1]);
    for(const key of ['member_ids','committee_ids','policy_ids'])for(const v of n[key])if(v!==null&&v!==undefined)identifiers.add(String(v));
    return {id:n.id,identifiers:[...identifiers].map(s=>s.toLowerCase()),text:((n.label||n.name||'')+' '+(n.source_summary||'')).toLowerCase()};
  });
}
const matches=(entry,query)=>{
  const q=query.trim().toLowerCase();
  if(!q)return false;
  if(entry.identifiers.some(i=>i===q))return true;
  return q.split(/\s+/).every(word=>entry.text.indexOf(word)>=0);
};
test('over a seeded world every answer equals the ruling, for every 1- and 2-character query, sampled names and pieces of ids',async t=>{
  const nodes=world(mulberry32(20261003),120),queries=queriesFor(nodes),entries=ruled(nodes);
  const p=page(source);p.load(nodes);
  let hits=0,wholeIdHits=0,fragmentsThatWouldHaveHit=0;
  for(const q of queries){
    const got=p.find(q),want=entries.filter(e=>matches(e,q)).map(e=>e.id);
    assert.deepEqual(got,want,'ruling: '+JSON.stringify(q));
    hits+=want.length;
    if(entries.some(e=>e.identifiers.includes(q.trim().toLowerCase())))wholeIdHits++;
    // What the previous text would have matched through an id or a code (a substring of one) and the ruling does not.
    if(q.trim()&&!want.length&&entries.some(e=>e.identifiers.some(i=>i.includes(q.trim().toLowerCase()))))fragmentsThatWouldHaveHit++;
  }
  // The world must be able to tell the ruling from the old search: fragments of identifiers that now find nothing, whole identifiers that still find nodes.
  const seen={queries:queries.length,hits,wholeIdHits,fragmentsThatWouldHaveHit};
  assert.ok(seen.queries>2500&&seen.wholeIdHits>400&&seen.fragmentsThatWouldHaveHit>300,'the world must exercise the ruling: '+JSON.stringify(seen));
  // The two callers agree with the predicate on a sample (the page itself, over this world with an edge no query reaches).
  p.load(nodes.concat(EDGE_NODES),{edges:[{source:'edge:a',target:'edge:b',layer:'co_tenure'}]});
  let sampled=0;
  for(const q of queries.filter((_,i)=>i%9===0)){
    const want=entries.filter(e=>matches(e,q)).map(e=>e.id),shown=p.listed(q);
    if(!q.trim())continue;
    assert.equal(shown.count,want.length,'legacy list count: '+JSON.stringify(q));
    assert.deepEqual(p.visible(q),want.slice().sort(),'map sample: '+JSON.stringify(q));
    sampled++;
  }
  assert.ok(sampled>150,'a sample of '+sampled+' queries went through both callers');
  t.diagnostic('seeded world: '+nodes.length+' nodes, '+JSON.stringify({...seen,predicateCalls:queries.length*nodes.length,callerQueries:sampled}));
});
test('the cache holds one entry per node and survives a second pass',async()=>{
  const p=newPage(source);
  p.find('casey');p.find('hsba');
  assert.equal(p.t.state.searchIndex.size,FIXTURE.length,'one cached entry per node');
  assert.deepEqual(p.find('casey'),ids([1]));assert.deepEqual(p.find('HSBA'),ids([1,4]));
  p.t.state.searchIndex.delete('politico:'+UUID_A);
  assert.deepEqual(p.find('casey'),ids([1]),'a deleted entry is rebuilt (the page deletes one when it learns more about a person)');
});

// ---- negative controls
test('negative control: the previous code fails the checks that are the ruling and passes the others',{skip:NO_GIT},async()=>{
  const old=await outcomes(newPage(before));
  assert.deepEqual(old,{idFragments:'fail',codeFragments:'fail',wholeIds:'pass',bareUuids:'pass',wholeCodes:'pass',text:'pass',labelFallback:'fail',flag:'pass',oddIds:'fail',callers:'fail'},
    'the previous matchesQuery searched the id, the label that falls back to it and the codes by pieces: '+JSON.stringify(old));
  assert.deepEqual(await outcomes(newPage(source)),ALL_PASS);
});
test('negative control: the previous code differs from the ruling over the seeded world',{skip:NO_GIT},async t=>{
  const nodes=world(mulberry32(20261003),120),queries=queriesFor(nodes),entries=ruled(nodes);
  const p=page(before);p.load(nodes);
  let differ=0,spurious=0,missing=0;
  for(const q of queries){
    const got=new Set(p.find(q)),want=new Set(entries.filter(e=>matches(e,q)).map(e=>e.id));
    for(const id of got)if(!want.has(id))spurious++;
    for(const id of want)if(!got.has(id))missing++;
    if(got.size!==want.size||[...got].some(id=>!want.has(id)))differ++;
  }
  assert.ok(differ>300&&spurious>1000,'the previous code must answer differently: '+JSON.stringify({differ,spurious,missing}));
  assert.equal(missing,0,'the previous code found everything the ruling finds (a whole id contains itself): it only found too much');
  t.diagnostic('previous code over the same world: '+JSON.stringify({queries:queries.length,differ,spurious,missing}));
});
const BUGS=[
  ['the id is searched by its pieces again',"words:[n.label||n.name||'',n.source_summary||'']","words:[n.label||n.name||'',n.id,n.source_summary||'']",['idFragments','callers']],
  ['the label falls back to the id again',"words:[n.label||n.name||'',","words:[n.label||n.name||n.id,",['labelFallback']],
  ['the member, committee and policy ids are searched by their pieces again',"words:[n.label||n.name||'',n.source_summary||'']","words:[n.label||n.name||'',n.source_summary||'',...(n.member_ids||[]),...(n.committee_ids||[]),...(n.policy_ids||[])]",['codeFragments','callers']],
  ['no whole-id match','||searchable.ids.includes(q)','',['wholeIds','bareUuids','wholeCodes','callers']],
  ['a case-sensitive id','const id=String(n.id).toLowerCase(),ids=[id];','const id=String(n.id),ids=[id];',['wholeIds']],
  ['ids are matched by substring','searchable.ids.includes(q)','searchable.ids.some(i=>i.includes(q))',['idFragments','codeFragments','callers']],
  ['an untrimmed query','const q=state.query.toLowerCase().trim();','const q=state.query.toLowerCase();',['wholeIds','wholeCodes']],
  ['no bare uuid',"if(id.startsWith('politico:')&&id.length>9)ids.push(id.slice(9));",'',['bareUuids']],
  ['the member, committee and policy ids are not whole-matched',"for(const key of ['member_ids','committee_ids','policy_ids'])",'for(const key of [])',['wholeCodes']],
  ['a null among the ids becomes the word null','if(value!==null&&value!==undefined)','',['oddIds']],
  ['the server flag is dropped',"(String(state.snapshot?.search?.query||'').toLowerCase()===q&&n.is_search_match===true)||",'',['flag']],
];
for(const [name,find,replace,caught] of BUGS)test('negative control: '+name+' is caught by '+caught.join(' and '),async()=>{
  assert.equal(source.split(find).length-1,1,'the page source must hold the text to break exactly once: '+find);
  const got=await outcomes(newPage(source.replace(find,()=>replace)));
  for(const check of caught)assert.equal(got[check],'fail',check+' must catch this bug: '+JSON.stringify(got));
});
