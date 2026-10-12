// D-083 in the record browser, second step: the inventory worker's "People found" search never matches pieces of the member, committee or policy id
// (row columns 3, 4 and 5) either; the first step took the node id (column 0) out. A query that is the whole id of one of them (trimmed, in any case)
// lists the rows that hold it, after the row a whole node id names and ahead of the rows that match by words. Columns 1, 2, 6 and 11 (name, summary,
// source, aliases) are still searched by word prefix, accents folded, all words required.
// The worker as it was before this step is read from git as the negative control, and nine deliberate bugs of the new filter are each caught.
// The committee ids below are not of the shape container:office:<chamber>:<name>. The name inside that shape is searched by way of column 4, and the prefix before it
// is not: see test_staffer_inventory_worker_committee_names.cjs.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const REL='fec/surfaces/staffer_live/staffer-inventory-worker.js';
const ROOT=path.resolve(__dirname,'../../..');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-inventory-worker.js'),'utf8');

// The worker as it was before this step (the tip of the route latency branch, 86631c454): column 0 whole-matched, columns 3 to 5 searched by word prefix.
const BEFORE='86631c454815533798eb1ad81136ddb1a76a23b9';
function fromGit(rev,rel){
  try{return execFileSync('git',['-C',ROOT,'show',rev+':'+rel],{encoding:'utf8',stdio:['ignore','pipe','ignore'],maxBuffer:1<<25});}catch(error){return null;}
}
const before=fromGit(BEFORE,REL);
const NO_GIT=before===null?'the pre-change worker (commit '+BEFORE.slice(0,9)+') is not in this checkout':false;

// Columns as the worker reads them: 0 id, 1 label, 2 summary, 3 member, 4 committee, 5 policy, 6 source, 7 pending, 8 kind, 11 aliases.
const row=(id,label,o={})=>[id,label,o.summary||'',o.member||'',o.committee||'',o.policy||'',o.source||'Quorum',false,'person',false,'unknown',o.aliases||''];
const UUID_A='3f2a1b4c-5d6e-4f80-9a0b-c1d2e3f4a5b6',UUID_B='9c8d7e6f-0a1b-4c2d-8e3f-4a5b6c7d8e9f',UUID_C='e5f60718-293a-4b4c-9d5e-6f708192a3b4';
const FIXTURE=[
  row('politico:'+UUID_A,'Casey Rivera',{summary:'Chief of Staff',member:'A000370',committee:'HSBA',policy:'BANKING'}),      // 0
  row('politico:'+UUID_B,'Jordan Lee',{summary:'Legislative Director',aliases:'Jordie Lee',member:'A000370'}),            // 1
  row('politico:'+UUID_C,'Morgan Quill',{member:'S001223',committee:'container:hsag'}),                                  // 2
  row('hill:person:alex-adams','Alex Adams',{source:'Payroll',committee:'HSBA'}),                                        // 3
  row('Quorum:Staff-0042','Priya Nair',{policy:'BANKING'}),                                                              // 4
  row('lda:9917','Pat Orwell',{source:'Lobbying',policy:'TAXATION'}),                                                       // 5
  row('quorum:77','Élodie Gagné',{member:'a000370x'}),                                                                   // 6: a code that merely begins with a member id
  row('staff:ada-5','Ada Five',{member:'X000001'}),                                                                      // 7
  row('person:3f2a1b4c5d6e','Zed Zeta'),                                                                                 // 8
];
const ALL=FIXTURE.map((_,i)=>i);
// A decoy whose label is a whole member id (it matches by words and comes first in row order), and a row whose own id is that member id.
const RANK_FIXTURE=[
  row('decoy:1','A000370 Hale'),                                               // 0
  row('politico:'+UUID_A,'Casey Rivera',{member:'A000370'}),                  // 1
  row('politico:'+UUID_B,'Jordan Lee',{member:'A000370'}),                    // 2
  row('A000370','Pat Collision'),                                              // 3: named by its own id, which is also the member id of rows 1 and 2
  row('hill:person:zed','Zed Zeta',{member:'S001223'}),                        // 4
];
// A null among the codes and a number as a code.
const ODD_FIXTURE=[Object.assign(row('odd:1','Blank Holder'),{3:null,4:null,5:null}),Object.assign(row('odd:2','Seven Holder'),{5:7}),row('odd:3','Plain Person')];

// A worker in its own vm context, loaded with `people`. find(text) returns the row positions the "People found" list holds for that text, across every
// page, in order. `code` is the worker's source (the current file, a deliberate bug or the one read from git).
async function worker(people,code=source){
  const messages=[],pending=[],at=new Map(people.map((r,i)=>[r,i]));
  const context=vm.createContext({onmessage:null,postMessage:v=>messages.push(v),fetch:()=>new Promise(resolve=>pending.push(resolve))});
  vm.runInContext(code,context);
  const send=data=>context.onmessage({data});
  const loading=send({type:'load',epoch:0,text:'',kind:'person'});
  pending[0]({ok:true,json:async()=>({ready:true,people,total:people.length,people_total:people.length})});
  await loading;
  let epoch=0;
  const find=async text=>{
    await send({type:'query',epoch:++epoch,text,kind:'person'});
    const first=messages.at(-1);assert.equal(first.type,'page');
    const found=Array.from(first.rows,r=>at.get(r));  // Array.from: rows come from the vm realm, whose Array is not this one
    for(let n=1;n<first.pages;n++){await send({type:'page',page:n});found.push(...Array.from(messages.at(-1).rows,r=>at.get(r)));}
    assert.equal(first.total,found.length);
    return found;
  };
  return {find};
}

const alt=s=>[...s].map((c,i)=>i%2?c.toUpperCase():c.toLowerCase()).join('');
const variants=id=>[id,id.toUpperCase(),id.toLowerCase(),alt(id),'  '+id+'\t\n',' '+id.toUpperCase()+' '];
const ownerRows=(people,id)=>people.flatMap((r,i)=>r[0].toLowerCase()===id.toLowerCase()?[i]:[]);

// Pieces of ids that no name, summary, source or alias holds.
const ID_FRAGMENTS=['po','pol','poli','politico','politico:','politico:3f2a','3f2a','3f2a1b4c','5d6e','4f80','9a0b','c1d2e3f4','9c8d7e6f','e5f60718','hill','hill:person','person','0042','lda','lda:99','9917','3f2a1b4c5d6e'];
// Pieces of member, committee and policy ids. Before this step each found the rows holding the id.
const CODE_FRAGMENTS=['a0','a00','a000','a0003','a00037','s001','s0012','x000','x00000','hsb','hs','container','container:hs','hsag','bank','banki','tax','taxa','taxat','axation'];
// Whole member, committee and policy ids and the rows that hold them (a000370x is another member's id that merely begins with a000370).
const CODES=[['A000370',[0,1]],['HSBA',[0,3]],['container:hsag',[2]],['BANKING',[0,4]],['TAXATION',[5]],['S001223',[2]],['X000001',[7]],['a000370x',[6]]];

const RULING={
  async idFragments(find){for(const q of ID_FRAGMENTS)assert.deepEqual(await find(q),[],'a piece of an id must find nothing by way of the id: '+q);},
  async codeFragments(find){
    for(const q of CODE_FRAGMENTS)assert.deepEqual(await find(q),[],'a piece of a member, committee or policy id must find nothing by way of it: '+q);
    // A code is not a word of a longer query either.
    for(const q of ['A000370 casey','casey A000370','hsba alex','banking priya'])assert.deepEqual(await find(q),[],'a code inside a longer query is not an identifier: '+q);
  },
  async wholeIds(find){
    for(const [i,r] of FIXTURE.entries())for(const q of variants(r[0]))assert.deepEqual((await find(q)).slice(0,1),[i],'the whole id '+JSON.stringify(q)+' must list its row first');
  },
  async bareUuids(find){
    for(const [i,uuid] of [[0,UUID_A],[1,UUID_B],[2,UUID_C]])for(const q of variants(uuid))assert.deepEqual((await find(q)).slice(0,1),[i],'the bare uuid '+JSON.stringify(q)+' must list its row first');
  },
  async wholeCodes(find){
    for(const [code,rows] of CODES)for(const q of variants(code))assert.deepEqual(await find(q),rows,'the whole id '+JSON.stringify(q)+' lists the rows that hold it, in row order');
  },
  // Named by id first, then the rows holding the id as a member, committee or policy id, then the rows that match only by words; each in row order.
  async ranking(find,people=RANK_FIXTURE){
    assert.deepEqual(ownerRows(people,'A000370'),[3]);
    for(const q of variants('A000370'))assert.deepEqual(await find(q),[3,1,2,0],'the person named by the id, then the people attached to it, then a row that only says it: '+JSON.stringify(q));
    assert.deepEqual(await find('S001223'),[4]);
    assert.deepEqual(await find('hale'),[0],'a word of a name is still a word');
  },
  // A null among the codes is skipped, not turned into the word "null"; a number is a code like any other.
  async oddCodes(find){
    assert.deepEqual(await find('null'),[],'a null code is not a word');
    assert.deepEqual(await find('7'),[1],'a number among the codes is an id like any other');
    assert.deepEqual(await find('Seven Holder'),[1]);
  },
  // Nothing else changed: the other columns still search by word prefix, accents folded, all words required, and the empty query lists everyone.
  async unchanged(find){
    const cases=[['cas',[0]],['casey riv',[0]],['alex adams',[3]],['ELODIE gagne',[6]],['élodie',[6]],['legislative dir',[1]],['jordie',[1]],
      ['lobbying',[5]],['payroll',[3]],['quorum',[0,1,2,4,6,7,8]],['casey chief',[0]],['zzz nobody',[]],['',ALL],['   ',ALL],['::',ALL]];
    for(const [q,want] of cases)assert.deepEqual(await find(q),want,'unchanged: '+JSON.stringify(q));
  },
};
// Each check runs against its own fixture; a worker is loaded per fixture.
const FIXTURES={ranking:RANK_FIXTURE,oddCodes:ODD_FIXTURE};
const run=async(name,code=source)=>RULING[name]((await worker(FIXTURES[name]||FIXTURE,code)).find);
const outcomes=async code=>{
  const out={};
  for(const name of Object.keys(RULING)){try{await run(name,code);out[name]='pass';}catch(e){if(!(e instanceof assert.AssertionError))throw e;out[name]='fail';}}
  return out;
};
const ALL_PASS=Object.fromEntries(Object.keys(RULING).map(k=>[k,'pass']));

for(const [name,title] of Object.entries({
  idFragments:'a piece of a node id finds nothing by way of the id',
  codeFragments:'a piece of a member, committee or policy id finds nothing by way of it, alone or inside a longer query',
  wholeIds:'a whole node id finds its row and lists it first, trimmed and in any case',
  bareUuids:'the bare uuid of a politico id is a whole id too',
  wholeCodes:'a whole member, committee or policy id lists the rows that hold it, trimmed and in any case',
  ranking:'the person a whole id names is listed first, then the people attached to it, then the rows that match only by words',
  oddCodes:'a null code is skipped and a number is a code like any other',
  unchanged:'every other column searches as before',
}))test('ruling: '+title,async()=>run(name));

// ---- a seeded world: the new answers equal the ruling written out by brute force, for every 1- and 2-character query, sampled names and pieces of ids and codes
const tokens=s=>s.normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
// Brute force, independent of the worker: each row is classified on its own. A row is named by its id (or bare uuid), else attached by a code, else matched by words.
function expected(people,text){
  const words=tokens(text),want=text.trim().toLowerCase(),named=[],attached=[],rest=[];
  people.forEach((r,i)=>{
    const id=String(r[0]).toLowerCase();
    if(want&&(id===want||(id.startsWith('politico:')&&id.slice(9)===want))){named.push(i);return;}
    if(want&&[3,4,5].some(c=>r[c]!==null&&r[c]!==undefined&&String(r[c]).toLowerCase()===want)){attached.push(i);return;}
    const have=tokens([1,2,6,11].map(c=>r[c]||'').join(' '));
    if(words.every(w=>have.some(t=>t.startsWith(w))))rest.push(i);
  });
  return [...named,...attached,...rest];
}
const mulberry32=a=>()=>{a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};
const hex=(rand,n)=>Array.from({length:n},()=>'0123456789abcdef'[Math.floor(rand()*16)]).join('');
const uuid=rand=>[8,4,4,4,12].map(n=>hex(rand,n)).join('-');
function world(rand,count){
  const pick=list=>list[Math.floor(rand()*list.length)];
  const words=['Alex','Adams','Élodie','Gagné','Casey','Rivera','Jordan','Lee','Morgan','Quill','Priya','Nair','Pat','Orwell','Zed','Zeta','Beatrice','Hale','Staff','Cole','Porter','Hill','Person'];
  const members=['A000370','S001223','V000127','M001204','office:2021-hon-hale','a000370x'],committees=['HSBA','SSFI','container:hsag','container:ssfi'],policies=['BANKING','policy:tax','policy:health'];
  const shapes=[()=>'politico:'+uuid(rand),()=>'hill:person:'+pick(['alex-adams','zed-zeta','pat-orwell','cole-porter'])+'-'+Math.floor(rand()*30),
    ()=>'quorum:'+Math.floor(rand()*900),()=>'lda:'+Math.floor(rand()*9000),()=>'person:'+hex(rand,12),()=>'Mixed:Case-'+Math.floor(rand()*40),()=>'politico:'+uuid(rand),
    ()=>pick(members)+'-'+Math.floor(rand()*5)];
  const people=[];
  for(let i=0;i<count;i++){
    // Now and then a name that is a code (and a word): the row matches that code by words and may sit before or after the rows that hold it.
    people.push(row(pick(shapes)(),rand()<.04?pick([...members,...committees,...policies])+' '+pick(words):pick(words)+' '+pick(words),{
      summary:rand()<.5?pick(words)+' '+pick(['Director','Counsel','Staff']):'',member:rand()<.4?pick(members):'',committee:rand()<.25?pick(committees):'',policy:rand()<.25?pick(policies):'',
      source:pick(['Payroll','Politico','Quorum','Lobbying','Payroll|Politico']),aliases:rand()<.3?pick(words)+' '+pick(words):''}));
  }
  people.push(people[3].slice());                                                         // two rows with one id
  people.push(Object.assign(row('own:'+hex(rand,6),'Own Id Holder'),{3:null,4:null,5:null}));
  people.push(row('A000370','Collision Person'),row('hsba','Collision Committee'),row('banking','Collision Policy'));   // a row named by an id that other rows hold as a code
  return people;
}
function queriesFor(people){
  const out=new Set(),alphabet='abcdefghijklmnopqrstuvwxyz0123456789:-. ';
  for(const a of alphabet){out.add(a);for(const b of alphabet)out.add(a+b);}
  for(const q of ['A0','Po','HS','S0','Ba','TA','  a  ',' po','po ','A000370 casey','casey a000370','HSBA alex','x y z'])out.add(q);
  for(let i=0;i<people.length;i+=Math.max(1,Math.floor(people.length/70))){
    const id=people[i][0],label=people[i][1],aliases=people[i][11];
    for(const v of variants(id))out.add(v);
    if(id.startsWith('politico:'))for(const v of variants(id.slice(9)))out.add(v);
    for(let n=1;n<=Math.min(id.length,14);n+=2)out.add(id.slice(0,n));
    for(let n=2;n<=8;n+=3){out.add(id.slice(-n));out.add(id.slice(5,5+n));}
    for(const group of id.replace('politico:','').split(/[-:]/))for(let n=2;n<=Math.min(group.length,9);n+=2)out.add(group.slice(0,n));
    for(const code of [people[i][3],people[i][4],people[i][5]].filter(Boolean)){
      for(const v of variants(code))out.add(v);
      for(let n=1;n<code.length;n++){out.add(code.slice(0,n));out.add(code.slice(n));}
    }
    out.add(label);out.add(label.toUpperCase());out.add(label.split(' ')[0].slice(0,3));if(aliases)out.add(aliases);
  }
  return [...out];
}
test('over a seeded world the new answers equal the ruling, for every 1- and 2-character query, sampled names and pieces of ids and codes',async t=>{
  const people=world(mulberry32(20261003),120),queries=queriesFor(people);
  const now=await worker(people);
  let nonEmpty=0,wholeCodeQueries=0,codeFragmentQueries=0;
  const codes=new Set(people.flatMap(r=>[r[3],r[4],r[5]]).filter(c=>c!==null&&c!==undefined&&c!=='').map(c=>String(c).toLowerCase()));
  for(const q of queries){
    const got=await now.find(q),want=expected(people,q);
    assert.deepEqual(got,want,'ruling: '+JSON.stringify(q));
    if(got.length&&got.length<people.length)nonEmpty++;
    const w=q.trim().toLowerCase();
    if(codes.has(w))wholeCodeQueries++;
    // A strict piece of a code that no row's words reach: the previous worker found rows through the code, this one finds none.
    else if(w&&!got.length&&[...codes].some(c=>c.includes(w)))codeFragmentQueries++;
  }
  const seen={queries:queries.length,nonEmpty,wholeCodeQueries,codeFragmentQueries};
  assert.ok(seen.queries>2500&&seen.wholeCodeQueries>50&&seen.codeFragmentQueries>100,'the world must exercise the ruling: '+JSON.stringify(seen));
  t.diagnostic('seeded world: '+people.length+' rows, '+JSON.stringify(seen));
});

// ---- negative controls
test('negative control: the previous worker fails the checks that are this step and passes the others',{skip:NO_GIT},async()=>{
  const old=await outcomes(before);
  assert.deepEqual(old,{idFragments:'pass',codeFragments:'fail',wholeIds:'pass',bareUuids:'pass',wholeCodes:'fail',ranking:'fail',oddCodes:'pass',unchanged:'pass'},
    'the previous worker searched columns 3 to 5 by word prefix: '+JSON.stringify(old));
  assert.deepEqual(await outcomes(source),ALL_PASS);
});
test('negative control: the previous worker differs from the ruling over the seeded world',{skip:NO_GIT},async t=>{
  const people=world(mulberry32(20261003),120),queries=queriesFor(people),old=await worker(people,before);
  let differ=0,spurious=0,missing=0,reordered=0;
  for(const q of queries){
    const got=await old.find(q),want=expected(people,q),have=new Set(got),wanted=new Set(want);
    for(const i of got)if(!wanted.has(i))spurious++;
    for(const i of want)if(!have.has(i))missing++;
    if(got.join()!==want.join())differ++;
    if(got.length===want.length&&got.join()!==want.join()&&[...got].sort().join()===[...want].sort().join())reordered++;
  }
  assert.ok(differ>200&&spurious>500,'the previous worker must answer differently: '+JSON.stringify({differ,spurious,missing,reordered}));
  assert.equal(missing,0,'the previous worker found everything the ruling finds (a whole code is a word prefix of itself): it only found too much');
  t.diagnostic('previous worker over the same world: '+JSON.stringify({queries:queries.length,differ,spurious,missing,reordered}));
});
const TOKENS="[r[1],r[2],r[6],r[11]||'']";
const ATTACHED='if(want&&(wholeCode(r[3],want)||wholeCode(r[4],want)||wholeCode(r[5],want))){attached.push(i);continue;}';
const BUGS=[
  ['the codes are searched by their pieces again',TOKENS,"[r[1],r[2],r[3],r[4],r[5],r[6],r[11]||'']",['codeFragments']],
  ['no whole-code match',ATTACHED,'',['wholeCodes','ranking']],
  ['a case-sensitive code','String(code).toLowerCase()===want','String(code)===want',['wholeCodes']],
  ['only the member id is matched whole','||wholeCode(r[4],want)||wholeCode(r[5],want)','',['wholeCodes']],
  ['the attached rows after the rest','matches=named.concat(attached,others)','matches=named.concat(others,attached)',['ranking']],
  ['the attached rows before the row named by the id','matches=named.concat(attached,others)','matches=attached.concat(named,others)',['ranking']],
  ['an untrimmed query','want=text.trim().toLowerCase()','want=text.toLowerCase()',['wholeCodes','wholeIds']],
  ['a null code becomes the word null','code!==null&&code!==undefined&&','',['oddCodes']],
  ['the column-0 handling is dropped','if(want&&wholeId(r[0],want)){named.push(i);continue;}','',['wholeIds','bareUuids']],
];
for(const [name,find,replace,caught] of BUGS)test('negative control: '+name+' is caught by '+caught.join(' and '),async()=>{
  assert.equal(source.split(find).length-1,1,'the worker source must hold the text to break exactly once: '+find);
  const got=await outcomes(source.replace(find,()=>replace));
  for(const check of caught)assert.equal(got[check],'fail',check+' must catch this bug: '+JSON.stringify(got));
});
