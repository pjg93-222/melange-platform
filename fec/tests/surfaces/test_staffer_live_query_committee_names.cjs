// The committee name in the map's query filter: matchesQuery() in staffer-live.js. D-083 took the pieces of identifiers out of the searched text, and with them the
// committee's own name, because a committee id carries it: container:office:<chamber>:<committee name>, for example
// container:office:senate:AGRICULTURE, NUTRITION & FORESTRY. Paul wants the name searchable again; the machine prefix is not. So the words of a query are looked for in
// the name, the source summary and the committee name of every committee id of the node (by substring, as names are), never in container:office:<chamber>: itself, and a
// whole identifier still matches whole, in any case. The member and policy ids carry no readable part (a bioguide id is A000370, a policy id is policy:politico:<hex>),
// and a committee id of any other shape (politico-affiliation:<hex>, container:hsag) carries none either, so those stay whole-only.
// The real page code runs in a vm over a small fake DOM, so both callers (rebuild() and results()) are exercised as well as the predicate. The code as it was before this
// correction (f23a40aee, what LIVE ran) is read from git as the negative control, and deliberate bugs of the new code are each caught.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const REL='fec/surfaces/staffer_live/staffer-live.js';
const ROOT=path.resolve(__dirname,'../../..');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-live.js'),'utf8');

// The file as it was before this correction: the committee name inside a committee id was not searched.
const BEFORE='f23a40aee4c749bae9db132599a853a52c81d9bf';
function fromGit(rev,rel){
  try{return execFileSync('git',['-C',ROOT,'show',rev+':'+rel],{encoding:'utf8',stdio:['ignore','pipe','ignore'],maxBuffer:1<<25});}catch(error){return null;}
}
const before=fromGit(BEFORE,REL);
const NO_GIT=before===null?'the pre-correction file (commit '+BEFORE.slice(0,9)+') is not in this checkout':false;

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

// ---- a hand-written world: every expectation below is written out by hand. The committee ids are the real ones, as the producers emit them (container:office:<chamber>:<payroll
// office key>, upper case, with commas, ampersands, hyphens, parentheses and apostrophes), plus one written in mixed case.
const AGRI='container:office:senate:AGRICULTURE, NUTRITION & FORESTRY',BANK='container:office:senate:BANKING, HOUSING & URBAN AFFAIRS',FINSVC='container:office:house:COMMITTEE ON FINANCIAL SERVICES';
const SCI='container:office:house:COMM ON SCIENCE, SPACE AND TECH',HELP='container:office:senate:HEALTH, EDU,LABOR&PENS',HOUSEADMIN='container:office:house:HOUSE ADMINISTRATION';
const RULES='container:office:house:COMMITTEE ON RULES',VETS="container:office:senate:VETERANS' AFFAIRS -",MIXED='Container:Office:Senate:Judiciary (Majority) Subcommittee on Antitrust';
const DIGEST='politico-affiliation:'+'9f1c0a7b3d5e8a2c4b6d1e07';
const person=(id,label,o={})=>({id,kind:'person',label,source_summary:o.summary,member_ids:o.member||[],committee_ids:o.committee||[],policy_ids:o.policy||[]});
const FIXTURE=[
  person('person:hill:person:01','Dana Ruiz',{member:['A000370'],committee:[AGRI]}),                      // 0
  person('person:hill:person:02','Lee Park',{committee:[AGRI,BANK]}),                                      // 1: two committees, the second is found too
  person('person:hill:person:03','Sam Ortiz',{committee:[BANK]}),                                          // 2
  person('person:hill:person:04','Kim Vo',{committee:[FINSVC]}),                                          // 3
  person('person:hill:person:05','Ana Cole',{committee:[SCI]}),                                           // 4
  person('person:hill:person:06','Jo Hart',{committee:[HELP]}),                                           // 5
  person('person:hill:person:07','Max Reed',{committee:[HOUSEADMIN]}),                                    // 6: house is part of the committee's NAME here
  person('person:hill:person:08','Eli Shaw',{committee:[RULES]}),                                         // 7: house sits only in the machine prefix
  person('person:hill:person:09','Ira Lund',{committee:[VETS]}),                                          // 8
  person('person:hill:person:10','Una Judd',{committee:[MIXED]}),                                         // 9: a prefix and a name in mixed case
  person('politico:3f2a1b4c-5d6e-4f80-9a0b-c1d2e3f4a5b6','Pat Moss',{committee:[DIGEST]}),               // 10: a digest, no readable part
  person('quorum:11','Ned Orr',{summary:'Counsel · Senate Office of Rep. Hale',committee:['container:hsag']}), // 11: says senate and office in its summary, a code of another shape
  person('quorum:12','Bea Cho',{committee:[null,7]}),                                                     // 12: a null and a number among the ids
  person('person:hill:person:13','Cy Dunn'),                                                              // 13: no committee
];
const ids=list=>list.map(i=>FIXTURE[i].id);
// Two people joined by one enabled edge and named by nothing a query can reach: in the map's sample they are always visible, every other person only if the query matches.
const EDGE_NODES=['edge:a','edge:b'].map(id=>({id,kind:'person',label:'',member_ids:[],committee_ids:[],policy_ids:[]}));
const alt=s=>[...s].map((c,i)=>i%2?c.toUpperCase():c.toLowerCase()).join('');
const variants=id=>[id,id.toUpperCase(),id.toLowerCase(),alt(id),'  '+id+'\t\n',' '+id.toUpperCase()+' '];

const RULING={
  // The committee name is text a person reads: a word, in any case, a part of a word (the map filter matches by substring, as it does names), several words together.
  async names(p){
    const cases=[['agriculture',[0,1]],['AGRICULTURE',[0,1]],['Agriculture',[0,1]],['nutrition',[0,1]],['forestry',[0,1]],['agri',[0,1]],['nutri',[0,1]],['fores',[0,1]],['culture',[0,1]],
      ['agriculture nutrition',[0,1]],['forestry agriculture',[0,1]],['agriculture, nutrition & forestry',[0,1]],['nutrition & forestry',[0,1]],
      ['banking',[1,2]],['housing',[1,2]],['urban affairs',[1,2]],['banking, housing & urban affairs',[1,2]],
      ['financial services',[3]],['financial',[3]],['comm on science',[4]],['science, space',[4]],['tech',[4]],
      ['edu,labor&pens',[5]],['labor',[5]],['pens',[5]],['health',[5]],
      ['house administration',[6]],['house',[6]],['administration',[6]],['rules',[7]],['committee on rules',[7]],['on rules',[7]],
      ["veterans'",[8]],['veterans',[8]],["affairs -",[8]],
      ['judiciary',[9]],['JUDICIARY',[9]],['antitrust',[9]],['(majority)',[9]],['majority) sub',[9]],['subcommittee on antitrust',[9]],
      ['dana agriculture',[0]],['agriculture dana',[0]],['lee agriculture',[1]],['lee banking',[1]],['sam banking',[2]],['dana banking',[]],['sam agriculture',[]],
      ['agriculture banking',[1]],['agriculture rules',[]],['zzz',[]]];
    for(const [q,want] of cases)assert.deepEqual(p.find(q),ids(want),'the committee name must be searched like a name: '+JSON.stringify(q));
    // Padded or in another case, the same.
    for(const q of ['  agriculture  ','\tAGRICULTURE\n',' AgRiCuLtUrE'])assert.deepEqual(p.find(q),ids([0,1]),JSON.stringify(q));
  },
  // The machine prefix is never searched, alone, in pieces, or joined to the name; a chamber word that sits only in the prefix finds nothing, the same word in a name or a
  // summary still does.
  async prefix(p){
    const none=['container','contain','ontainer','tainer','container:office','container:office:','container:office:senate','container:office:senate:','container:office:house','container:office:house:',
      'office:senate','office:house:','ice:sen','e:senate:','nate:','senate:','house:','e:house:','senate:agri','senate: agriculture','container agriculture','office banking','container:office:senate:agri','container:office:senate:agriculture',
      'container:office:senate:agriculture, nutrition & forestry x','hon:','container:o'];
    for(const q of none)assert.deepEqual(p.find(q),[],'a piece of the machine prefix must find nothing by way of the id: '+JSON.stringify(q));
    // 'senate' and 'office' are in the summary of one person and in the prefix of many: only the summary counts.
    assert.deepEqual(p.find('senate'),ids([11]),'senate is searched where a person reads it, not in container:office:senate:');
    assert.deepEqual(p.find('office'),ids([11]),'office is searched where a person reads it, not in container:office:');
    assert.deepEqual(p.find('SENATE'),ids([11]));
    assert.deepEqual(p.find('offic'),ids([11]));
    // 'house' is a word of one committee's name (what the names check finds) and only the prefix of the other house committees: it must not reach them.
    const house=p.find('house');
    assert.ok(!house.includes(FIXTURE[7].id)&&!house.includes(FIXTURE[3].id)&&!house.includes(FIXTURE[4].id),'Eli Shaw, Kim Vo and Ana Cole sit on house committees, but house is only in their prefix');
    assert.ok(house.every(id=>id===FIXTURE[6].id),'house finds no one but the committee that has it in its name');
  },
  // A whole identifier of any kind still matches whole: a committee id in any case, padded, finds every node that holds it, among all of its committee ids.
  async wholeIds(p){
    for(const [id,nodes] of [[AGRI,[0,1]],[BANK,[1,2]],[FINSVC,[3]],[SCI,[4]],[HELP,[5]],[HOUSEADMIN,[6]],[RULES,[7]],[VETS,[8]],[MIXED,[9]],[DIGEST,[10]],['container:hsag',[11]]])
      for(const q of variants(id))assert.deepEqual(p.find(q),ids(nodes),'the whole committee id '+JSON.stringify(q)+' finds the nodes that hold it');
    for(const n of FIXTURE)for(const q of variants(n.id))assert.deepEqual(p.find(q),[n.id],'the whole node id '+JSON.stringify(q)+' finds its node');
    for(const q of variants('A000370'))assert.deepEqual(p.find(q),ids([0]),'the whole member id '+JSON.stringify(q));
  },
  // An id with no readable part contributes none: a committee id of another shape (a digest, a short code), a null or a number among the ids.
  async otherShapes(p){
    const none=['politico-affiliation','affiliation','9f1c0a7b','9f1c','0a7b3d5e','b6d1e07','hsag','container:hs','null','undefined','a000','a000370x','politico'];
    for(const q of none)assert.deepEqual(p.find(q),[],'an id with no readable part is searched whole only: '+JSON.stringify(q));
    // The whole of such an id, and a number among the ids, are ids like any other.
    assert.deepEqual(p.find('container:hsag'),ids([11]));assert.deepEqual(p.find('9F1C0A7B3D5E8A2C4B6D1E07'),[],'a bare digest is not an id: only politico-affiliation:<digest> is');
    assert.deepEqual(p.find('POLITICO-AFFILIATION:9f1c0a7b3d5e8a2c4b6d1e07'),ids([10]));assert.deepEqual(p.find('7'),ids([12]),'a number among the ids is an id like any other');
  },
  // Names and summaries are searched as before, by pieces, in any case, all words required.
  async text(p){
    const cases=[['dana',[0]],['RUIZ dana',[0]],['uiz',[0]],['ortiz',[2]],['counsel',[11]],['rep. hale',[11]],['senate office',[11]],['counsel hale',[11]],['dana nobody',[]],['   ',[]],['',[]],
      ['cy dunn',[13]],['bea',[12]],['pat moss',[10]]];
    for(const [q,want] of cases)assert.deepEqual(p.find(q),ids(want),'unchanged: '+JSON.stringify(q));
  },
  // Both callers: the legacy list shows exactly the matching people in node order, the map's sample holds exactly the matching people.
  async callers(p){
    const fixed=[['agriculture',[0,1]],['AGRI',[0,1]],['banking',[1,2]],['house',[6]],['senate',[11]],['senate:',[]],['container',[]],['container:office:senate:',[]],[AGRI.toLowerCase(),[0,1]],[' '+RULES+' ',[7]],
      ['dana',[0]],['dana banking',[]],['zzz',[]]];
    p.load(FIXTURE.concat(EDGE_NODES),{edges:[{source:'edge:a',target:'edge:b',layer:'co_tenure'}]});
    p.t.state.snapshot=null;
    for(const [q,want] of fixed){
      const names=want.map(i=>FIXTURE[i].label||FIXTURE[i].id);
      const shown=p.listed(q);
      assert.deepEqual(shown.labels,names,'the legacy list for '+JSON.stringify(q));assert.equal(shown.count,want.length);
      assert.deepEqual(p.visible(q),ids(want).slice().sort(),'the map sample for '+JSON.stringify(q));
    }
    p.load(FIXTURE);
  },
  // The cache holds the committee names with the rest of the text, one entry per node.
  async cache(p){
    p.find('agriculture');p.find('banking');
    assert.equal(p.t.state.searchIndex.size,FIXTURE.length,'one cached entry per node');
    assert.deepEqual(p.find('agriculture'),ids([0,1]));assert.deepEqual(p.find('AGRICULTURE'),ids([0,1]));
    p.t.state.searchIndex.delete(FIXTURE[0].id);
    assert.deepEqual(p.find('agriculture'),ids([0,1]),'a deleted entry is rebuilt with its committee names (the page deletes one when it learns more about a person)');
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
  names:'the committee name in a committee id is searched like a name: a word, part of a word, several words, in any case, any of the node\'s committees',
  prefix:'container:office:<chamber>: is never searched, alone, in pieces or joined to the name',
  wholeIds:'a whole committee id, node id or member id finds its nodes, trimmed and in any case',
  otherShapes:'a committee id of another shape, a null and a number contribute no words',
  text:'names and summaries are searched as before',
  callers:'the legacy list and the map sample follow the same rule',
  cache:'the cached text holds the committee names and is rebuilt when an entry is deleted',
}[name],async()=>RULING[name](newPage(source)));

// ---- a seeded world: every answer equals the corrected rule written out by brute force, for every 1- and 2-character query, the committee names, pieces of them, and pieces of ids
const mulberry32=a=>()=>{a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};
const hex=(rand,n)=>Array.from({length:n},()=>'0123456789abcdef'[Math.floor(rand()*16)]).join('');
const uuid=rand=>[8,4,4,4,12].map(n=>hex(rand,n)).join('-');
const title=s=>s.replace(/(^|[^a-z])([a-z])/gi,(m,a,b)=>a+b.toUpperCase()).replace(/([A-Z])([A-Z]+)/g,(m,a,b)=>a+b.toLowerCase());
// Real committee ids (the v8 generation's groups), as the producers emit them; some are written again in mixed case, as Container:Office:Senate:Agriculture, Nutrition & Forestry.
const REAL=[AGRI,BANK,FINSVC,SCI,HELP,HOUSEADMIN,RULES,VETS,'container:office:senate:COMMERCE, SCI &TRANS','container:office:senate:MINORITY CONFERENCE COMMITTEE (D)','container:office:senate:JUDICIARY',
  'container:office:house:COMMITTEE ON WAYS AND MEANS','container:office:house:TRANSPORTATION-INFRASTRUCTURE','container:office:house:SELECT COMM - CLIMATE CRISIS','container:office:house:COMM ON VOTING IRREG-AUG2 2007',
  'container:office:senate:HOMELAND SEC.& GOV AFF','container:office:senate:FINANCE -','container:office:house:COMMITTEE ON AGRICULTURE'];
function world(rand,count){
  const pick=list=>list[Math.floor(rand()*list.length)];
  const given=['Alex','Casey','Jordan','Morgan','Priya','Pat','Zed','Beatrice','Cole','Élodie','Sam','Dana','Ruth'],family=['Adams','Rivera','Lee','Quill','Nair','Orwell','Zeta','Hale','Porter','Gagné','Stone','Marsh','Ode'];
  const orgs=['House Ways Means','Senate Banking','Office of Rep. Hale','Quorum Strategies LLC','Acme Lobbying Group','Committee on Finance','Container Services'];
  const shapes=[()=>'politico:'+uuid(rand),()=>'politico:'+uuid(rand),()=>'hill:person:'+pick(['alex-adams','zed-zeta','pat-orwell'])+'-'+Math.floor(rand()*40),
    ()=>'person:lda:lobbyist:'+Math.floor(rand()*9000),()=>'quorum:'+Math.floor(rand()*900),()=>'person:'+hex(rand,12),()=>'Mixed:Case-'+Math.floor(rand()*40)];
  const committee=()=>{const id=pick(REAL);return rand()<.3?title(id):id;};
  const seen=new Set(),nodes=[];
  while(nodes.length<count){
    const id=pick(shapes)();if(seen.has(id))continue;seen.add(id);
    const n={id,kind:'person',member_ids:[],committee_ids:[],policy_ids:[],label:pick(given)+' '+pick(family)};
    if(rand()<.4)n.source_summary=pick(['Director','Counsel','Chief of Staff'])+' · '+pick(orgs)+' · last reported 20'+(10+Math.floor(rand()*15))+'-0'+(1+Math.floor(rand()*9))+'-15';
    if(rand()<.4)n.member_ids.push(pick(['A000370','S001223','V000127','M001204']));
    if(rand()<.6)n.committee_ids.push(committee());
    if(rand()<.2)n.committee_ids.push(committee());
    if(rand()<.04)n.committee_ids.push(pick(['container:hsag','HSBA',DIGEST,'politico-affiliation:'+hex(rand,24),null,7]));
    if(rand()<.2)n.policy_ids.push(pick(['policy:politico:'+hex(rand,24),'policy:tax',3]));
    nodes.push(n);
  }
  return nodes;
}
function queriesFor(nodes){
  const out=new Set(),alphabet='abcdefghijklmnopqrstuvwxyz0123456789:-.,&\'() ';
  for(const a of alphabet){out.add(a);for(const b of alphabet)out.add(a+b);}
  for(const q of ['container','contain','office','offic','senate','senate:','house','house:','container:office','container:office:','container:office:senate:','container:office:house:','office:senate','ice:sen','e:senate:','  senate  ','Senate:','HOUSE'])out.add(q);
  for(const id of REAL.concat(REAL.map(title))){
    const name=id.replace(/^container:office:(house|senate):/i,''),words=name.toLowerCase().match(/[a-z0-9]+/g)||[];
    for(const v of variants(id))out.add(v);
    out.add(name);out.add(name.toLowerCase());out.add(name.toUpperCase());out.add(words.join(' '));out.add(words.slice().reverse().join(' '));out.add(words.slice(0,2).join(' '));
    for(const w of words){out.add(w);out.add(w.toUpperCase());for(let k=2;k<w.length;k+=2){out.add(w.slice(0,k));out.add(w.slice(k));}}
    out.add('senate:'+words[0]);out.add('senate: '+words[0]);out.add('house:'+words[0]);out.add('container '+words[0]);out.add('office '+words[0]);out.add('container:office:senate:'+words[0]);
    for(let k=1;k<id.length;k+=3){out.add(id.slice(0,k));out.add(id.slice(k));out.add(id.slice(k,k+6));}
  }
  for(let i=0;i<nodes.length;i+=Math.max(1,Math.floor(nodes.length/70))){
    const n=nodes[i],text=n.label;
    for(const v of variants(n.id))out.add(v);
    if(n.id.startsWith('politico:'))for(const v of variants(n.id.slice(9)))out.add(v);
    for(let k=1;k<=Math.min(n.id.length,14);k+=2)out.add(n.id.slice(0,k));
    for(const code of [...n.member_ids,...n.committee_ids,...n.policy_ids].filter(c=>c!==null))for(const v of variants(String(code)))out.add(v);
    out.add(text);out.add(text.toUpperCase());out.add(text.split(' ')[0].slice(0,3));
    const named=n.committee_ids.find(c=>nameOf(c));
    if(named)out.add(text.split(' ')[0].toLowerCase()+' '+nameOf(named).split(/\s+/)[0].toLowerCase());   // a word of the name and a word of the committee name, both required
    if(n.source_summary){out.add(n.source_summary.split(' · ')[1]);out.add(n.source_summary.split(' · ')[0].toLowerCase());}
  }
  return [...out];
}
// Brute force, independent of the page and of its regular expression: a node matches when the whole trimmed query is one of its identifiers, or every word of the query
// occurs in its text, which is the name, the source summary and the committee name of each of its committee ids (the part after container:office:house: or
// container:office:senate:, read by string operations).
const PREFIXES=['container:office:house:','container:office:senate:'];
const nameOf=code=>{const text=String(code),low=text.toLowerCase();for(const p of PREFIXES)if(low.startsWith(p)&&low.length>p.length)return text.slice(p.length);return '';};
function ruled(nodes,{names=true}={}){
  return nodes.map(n=>{
    const identifiers=new Set([String(n.id)]);
    const bare=/^politico:(.+)$/i.exec(String(n.id));if(bare)identifiers.add(bare[1]);
    for(const key of ['member_ids','committee_ids','policy_ids'])for(const v of n[key])if(v!==null&&v!==undefined)identifiers.add(String(v));
    const committeeNames=names?n.committee_ids.map(nameOf).filter(Boolean):[];
    return {id:n.id,identifiers:[...identifiers].map(s=>s.toLowerCase()),text:[n.label||n.name||'',n.source_summary||'',...committeeNames].join(' ').toLowerCase()};
  });
}
const matches=(entry,query)=>{
  const q=query.trim().toLowerCase();
  if(!q)return false;
  if(entry.identifiers.some(i=>i===q))return true;
  return q.split(/\s+/).every(word=>entry.text.indexOf(word)>=0);
};
test('over a seeded world every answer equals the corrected rule, for every 1- and 2-character query, committee names, pieces of them and pieces of ids',async t=>{
  const nodes=world(mulberry32(20261003),120),queries=queriesFor(nodes),entries=ruled(nodes),without=ruled(nodes,{names:false});
  const p=page(source);p.load(nodes);
  let hits=0,viaName=0,wholeIdHits=0,prefixOnly=0;
  for(const q of queries){
    const got=p.find(q),want=entries.filter(e=>matches(e,q)).map(e=>e.id);
    assert.deepEqual(got,want,'rule: '+JSON.stringify(q));
    hits+=want.length;
    const unnamed=without.filter(e=>matches(e,q)).map(e=>e.id);
    if(want.length>unnamed.length)viaName++;
    if(entries.some(e=>e.identifiers.includes(q.trim().toLowerCase())))wholeIdHits++;
    // A piece that sits only in container:office:<chamber>: (and in no name, summary or other id) must find no one by way of a committee id.
    if(q.trim()&&/^(container|contain|office|offic|senate:?|house:|container:office:?|container:office:(senate|house):?|office:senate|ice:sen)$/.test(q.trim().toLowerCase())&&!want.length)prefixOnly++;
  }
  // The world must be able to tell the correction from the old search and from a search of the whole id: names that now find staff, whole ids, pieces of the prefix that find no one.
  const seen={queries:queries.length,hits,viaName,wholeIdHits,prefixOnly};
  assert.ok(seen.queries>4000&&seen.viaName>800&&seen.wholeIdHits>800&&seen.prefixOnly>=8,'the world must exercise the correction: '+JSON.stringify(seen));
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

// ---- negative controls
test('negative control: the code before this correction fails the committee-name checks and passes the others',{skip:NO_GIT},async()=>{
  const old=await outcomes(newPage(before));
  assert.deepEqual(old,{names:'fail',prefix:'pass',wholeIds:'pass',otherShapes:'pass',text:'pass',callers:'fail',cache:'fail'},
    'f23a40aee searched the name and the summary and whole ids only: it never searched the prefix, and never the committee name: '+JSON.stringify(old));
  assert.deepEqual(await outcomes(newPage(source)),ALL_PASS);
});
test('negative control: the code before this correction misses names the corrected rule finds, and finds nothing extra, over the seeded world',{skip:NO_GIT},async t=>{
  const nodes=world(mulberry32(20261003),120),queries=queriesFor(nodes),entries=ruled(nodes);
  const p=page(before);p.load(nodes);
  let differ=0,spurious=0,missing=0;
  for(const q of queries){
    const got=new Set(p.find(q)),want=new Set(entries.filter(e=>matches(e,q)).map(e=>e.id));
    for(const id of got)if(!want.has(id))spurious++;
    for(const id of want)if(!got.has(id))missing++;
    if(got.size!==want.size||[...got].some(id=>!want.has(id)))differ++;
  }
  assert.ok(differ>800&&missing>5000,'the old code must answer differently: '+JSON.stringify({differ,spurious,missing}));
  assert.equal(spurious,0,'the old code found nothing the corrected rule does not find: it only missed the committee names');
  t.diagnostic('code before the correction over the same world: '+JSON.stringify({queries:queries.length,differ,spurious,missing}));
});
const BUGS=[
  ['the whole committee id is searched as text, prefix included','names.push(m[1])','names.push(m[0])',['prefix','callers']],
  ['only the first committee id of a node is read','[].concat(n.committee_ids||[])','[].concat((n.committee_ids||[]).slice(0,1))',['names']],
  ['only senate committees are read','(?:house|senate)','(?:senate)',['names','callers']],
  ['the prefix is matched in lower case only','(.+)$/i;','(.+)$/;',['names']],
  ['the committee names are dropped','.concat(committeeNames(n))','',['names','callers']],
  ['an affiliation digest is read as a committee name','container:office:(?:house|senate):(.+)','(?:container:office:(?:house|senate):|politico-affiliation:)(.+)',['otherShapes']],
  ['any text after the first colon is read as a name','^container:office:(?:house|senate):(.+)$','^[^:]*:(.+)$',['prefix','otherShapes']],
];
for(const [name,find,replace,caught] of BUGS)test('negative control: '+name+' is caught by '+caught.join(' and '),async()=>{
  assert.equal(source.split(find).length-1,1,'the page source must hold the text to break exactly once: '+find);
  const bug=source.replace(find,()=>replace);
  const got=await outcomes(newPage(bug));
  for(const check of caught)assert.equal(got[check],'fail',check+' must catch this bug: '+JSON.stringify(got));
});
test('negative control: a search of the whole committee id as text differs from the corrected rule over the seeded world',async t=>{
  const find='names.push(m[1])',nodes=world(mulberry32(20261003),120),queries=queriesFor(nodes),entries=ruled(nodes);
  assert.equal(source.split(find).length-1,1);
  const p=page(source.replace(find,()=>'names.push(m[0])'));p.load(nodes);
  let spurious=0;
  for(const q of queries){const got=p.find(q),want=new Set(entries.filter(e=>matches(e,q)).map(e=>e.id));for(const id of got)if(!want.has(id))spurious++;}
  assert.ok(spurious>5000,'the world must be able to tell a search of the prefix from the rule: '+spurious);
  t.diagnostic('whole-id-as-text bug over the same world: '+spurious+' answers the rule does not give');
});
