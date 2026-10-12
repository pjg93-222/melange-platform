// D-083 in the record browser: the inventory worker's "People found" search never matches pieces of a node id (row column 0).
// A query that is a row's whole id (trimmed, in any case; for a politico:<uuid> id also its bare uuid) still finds that row and lists it first.
// The previous search is embedded verbatim as a negative control, and seven deliberate bugs of the new one are each caught.
// Columns 3 to 5 (member, committee and policy id) were taken out of the searched text in the next step: see test_staffer_inventory_worker_codes.cjs.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-inventory-worker.js'),'utf8');

// The search before the ruling, verbatim: it read column 0, the node id, together with columns 1-6 and 11.
const OLD_FILTER=String.raw`function filter(){
  const words=(query.text||'').normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
  matches=[];
  for(let i=0;i<rows.length;i++){
    const r=rows[i];
    if(words.length){const tokens=[...r.slice(0,7),r[11]||''].join(' ').normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];if(!words.every(w=>tokens.some(token=>token.startsWith(w))))continue;}
    matches.push(i);
  }
}`;

// Columns as the worker reads them: 0 id, 1 label, 2 summary, 3 member, 4 committee, 5 policy, 6 source, 7 pending, 8 kind, 11 aliases.
const row=(id,label,o={})=>[id,label,o.summary||'',o.member||'',o.committee||'',o.policy||'',o.source||'Quorum',false,'person',false,'unknown',o.aliases||''];
const UUID_A='3f2a1b4c-5d6e-4f80-9a0b-c1d2e3f4a5b6',UUID_B='9c8d7e6f-0a1b-4c2d-8e3f-4a5b6c7d8e9f',UUID_C='e5f60718-293a-4b4c-9d5e-6f708192a3b4';
const FIXTURE=[
  row('decoy:1','Staff Ada 5'),                                      // 0: its label holds the words of row 10's id and it comes first in row order
  row('politico:'+UUID_A,'Casey Rivera',{summary:'Chief of Staff'}), // 1
  row('politico:'+UUID_B,'Jordan Lee',{summary:'Legislative Director',aliases:'Jordie Lee'}), // 2
  row('politico:'+UUID_C,'Morgan Quill'),                            // 3
  row('hill:person:alex-adams','Alex Adams',{source:'Payroll'}),     // 4
  row('Quorum:Staff-0042','Priya Nair'),                             // 5
  row('lda:9917','Pat Orwell',{source:'Lobbying'}),                  // 6
  row('hill:person:zed-zeta','Zed Zeta',{source:'Payroll'}),         // 7
  row('quorum:77','Élodie Gagné'),                                   // 8
  row('lda:9917','Patricia Orwell',{source:'Lobbying'}),             // 9: the same id as row 6
  row('staff:ada-5','Ada Five'),                                     // 10
];
const ALL=FIXTURE.map((_,i)=>i);

// A worker in its own vm context, loaded with `people`. `edit` rewrites the worker source (the deliberate bugs); `oldFilter` swaps the
// previous search in. find(text) returns the row positions the "People found" list holds for that text, across every page, in order.
async function worker(people,{edit=s=>s,oldFilter=false}={}){
  const messages=[],pending=[],at=new Map(people.map((r,i)=>[r,i]));
  const context=vm.createContext({onmessage:null,postMessage:v=>messages.push(v),fetch:()=>new Promise(resolve=>pending.push(resolve))});
  vm.runInContext(edit(source),context);
  if(oldFilter)vm.runInContext(OLD_FILTER,context);
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
const owners=id=>FIXTURE.flatMap((r,i)=>r[0].toLowerCase()===id.toLowerCase()?[i]:[]);
// Pieces of ids that no other column holds: before the ruling each found a row through its id, now none does.
const FRAGMENTS=['po','pol','poli','politico','politico:','politico:3f2a','3f2a','3f2a1b4c','5d6e','4f80','9a0b','c1d2e3f4','9c8d7e6f','e5f60718',
  'hill','hill:person','person','0042','lda','lda:99','9917'];

const RULING={
  async fragments(find){
    for(const q of FRAGMENTS)assert.deepEqual(await find(q),[],'a piece of an id must find nothing by way of the id: '+q);
  },
  async wholeIds(find){
    for(const r of FIXTURE)for(const q of variants(r[0])){
      const want=owners(r[0]);
      assert.deepEqual((await find(q)).slice(0,want.length),want,'the whole id '+JSON.stringify(q)+' must list its row first');
    }
  },
  async bareUuids(find){
    for(const r of FIXTURE.filter(r=>r[0].startsWith('politico:')))for(const q of variants(r[0].slice(9))){
      const want=owners(r[0]);
      assert.deepEqual((await find(q)).slice(0,want.length),want,'the bare uuid '+JSON.stringify(q)+' must list its row first');
    }
  },
  async ranking(find){
    assert.deepEqual(await find('staff:ada-5'),[10,0],'the id row comes before a row that matches only by words, though it is later in row order');
  },
  // Nothing but column 0 changed in this step: the other columns still search by word prefix, accents folded, all words required.
  async unchanged(find){
    const cases=[['cas',[1]],['casey riv',[1]],['alex adams',[4]],['ELODIE gagne',[8]],['élodie',[8]],['legislative dir',[2]],['jordie',[2]],
      ['lobbying',[6,9]],['quorum',[0,1,2,3,5,8,10]],['casey chief',[1]],['zzz nobody',[]],['',ALL],['   ',ALL],['::',ALL]];
    for(const [q,want] of cases)assert.deepEqual(await find(q),want,'unchanged: '+JSON.stringify(q));
  },
};
const outcomes=async find=>{
  const out={};
  for(const [name,check] of Object.entries(RULING)){try{await check(find);out[name]='pass';}catch(e){if(!(e instanceof assert.AssertionError))throw e;out[name]='fail';}}
  return out;
};

test('a piece of an id finds nothing by way of the id',async()=>RULING.fragments((await worker(FIXTURE)).find));
test('a whole id finds its row and lists it first, trimmed and in any case',async()=>RULING.wholeIds((await worker(FIXTURE)).find));
test('the bare uuid of a politico id is a whole id too',async()=>RULING.bareUuids((await worker(FIXTURE)).find));
test('a whole id outranks a row that matches only by words',async()=>RULING.ranking((await worker(FIXTURE)).find));
test('every other column searches as before',async()=>RULING.unchanged((await worker(FIXTURE)).find));

// ---- a seeded world: the new answers equal the ruling written out, and the previous filter equals the previous rules
const tokens=s=>s.normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
// Brute force, independent of the worker. idColumn=true is the old rule (column 0 searched with the rest, and columns 3 to 5 by word prefix); false is the ruling:
// column 0 and the member, committee and policy ids of columns 3 to 5 match only whole (the rows a whole code names follow the row a whole id names).
function expected(people,text,{idColumn}){
  const words=tokens(text),want=text.trim().toLowerCase(),named=[],attached=[],rest=[];
  people.forEach((r,i)=>{
    const id=String(r[0]).toLowerCase();
    if(!idColumn&&want&&(id===want||(id.startsWith('politico:')&&id.slice(9)===want))){named.push(i);return;}
    if(!idColumn&&want&&[3,4,5].some(c=>String(r[c]||'').toLowerCase()===want)){attached.push(i);return;}
    const have=tokens((idColumn?[0,1,2,3,4,5,6,11]:[1,2,6,11]).map(c=>r[c]||'').join(' '));
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
  const shapes=[()=>'politico:'+uuid(rand),()=>'hill:person:'+pick(['alex-adams','zed-zeta','pat-orwell','cole-porter'])+'-'+Math.floor(rand()*30),
    ()=>'quorum:'+Math.floor(rand()*900),()=>'lda:'+Math.floor(rand()*9000),()=>'person:'+hex(rand,12),()=>'Mixed:Case-'+Math.floor(rand()*40),()=>'politico:'+uuid(rand)];
  const people=[];
  for(let i=0;i<count;i++){
    people.push(row(pick(shapes)(),pick(words)+' '+pick(words),{
      summary:rand()<.5?pick(words)+' '+pick(['Director','Counsel','Staff']):'',member:rand()<.4?pick(['A000370','S001223','V000127','M001204']):'',
      committee:rand()<.2?'container:'+pick(['hsag','ssfi']):'',policy:rand()<.2?'policy:'+pick(['tax','health']):'',
      source:pick(['Payroll','Politico','Quorum','Lobbying','Payroll|Politico']),aliases:rand()<.3?pick(words)+' '+pick(words):''}));
  }
  people.push(people[3].slice());  // two rows with one id
  return people;
}
function queriesFor(people){
  const out=new Set(),alphabet='abcdefhilopqtz0123456789:- ';
  for(const a of alphabet){out.add(a);for(const b of alphabet)out.add(a+b);}
  for(let i=0;i<people.length;i+=Math.floor(people.length/60)){
    const id=people[i][0],label=people[i][1],aliases=people[i][11];
    for(const v of variants(id))out.add(v);
    if(id.startsWith('politico:'))for(const v of variants(id.slice(9)))out.add(v);
    for(let n=1;n<=Math.min(id.length,14);n+=2)out.add(id.slice(0,n));
    for(let n=2;n<=8;n+=3){out.add(id.slice(-n));out.add(id.slice(5,5+n));}
    for(const group of id.replace('politico:','').split(/[-:]/))for(let n=2;n<=Math.min(group.length,9);n+=2)out.add(group.slice(0,n));
    out.add(label);out.add(label.toUpperCase());out.add(label.split(' ')[0].slice(0,3));if(aliases)out.add(aliases);
  }
  return [...out];
}
test('over a seeded world the new answers equal the ruling and the previous filter equals the previous rules',async()=>{
  const people=world(mulberry32(20261003),110),queries=queriesFor(people);
  const now=await worker(people),was=await worker(people,{oldFilter:true});
  let changed=0,emptied=0;
  for(const q of queries){
    const a=await now.find(q),b=await was.find(q);
    assert.deepEqual(a,expected(people,q,{idColumn:false}),'ruling: '+JSON.stringify(q));
    assert.deepEqual(b,expected(people,q,{idColumn:true}),'previous rules: '+JSON.stringify(q));
    if(a.join()!==b.join())changed++;
    if(!a.length&&b.length)emptied++;
  }
  // The world can tell the two apart: pieces of ids found rows before and find none now.
  const seen={queries:queries.length,changed,emptied};
  assert.ok(seen.queries>2000&&seen.changed>150&&seen.emptied>100,'the world must exercise the ruling: '+JSON.stringify(seen));
});

// ---- negative controls
test('negative control: the previous search fails the ruling checks that are the ruling and passes the others',async()=>{
  const old=await worker(FIXTURE,{oldFilter:true});
  assert.deepEqual(await outcomes(old.find),{fragments:'fail',wholeIds:'fail',bareUuids:'pass',ranking:'fail',unchanged:'pass'});
  assert.deepEqual(await outcomes((await worker(FIXTURE)).find),{fragments:'pass',wholeIds:'pass',bareUuids:'pass',ranking:'pass',unchanged:'pass'});
});
const BUGS=[
  ['the id is searched with the rest again',"[r[1],r[2],r[6],r[11]||'']","[r[0],r[1],r[2],r[6],r[11]||'']",['fragments']],
  ['no whole-id match','if(want&&wholeId(r[0],want)){named.push(i);continue;}','',['wholeIds','bareUuids']],
  ['a case-sensitive id','id=String(id).toLowerCase();','id=String(id);',['wholeIds']],
  ['an untrimmed query','want=text.trim().toLowerCase()','want=text.toLowerCase()',['wholeIds']],
  ['no bare uuid','||(id.startsWith(POLITICO)&&id.length>POLITICO.length&&id.slice(POLITICO.length)===want)','',['bareUuids']],
  ['the whole-id rows after the rest','matches=named.concat(attached,others)','matches=others.concat(attached,named)',['wholeIds','ranking']],
  ['the whole-id rows in row order','named.push(i);continue;','others.push(i);continue;',['wholeIds','ranking']],
];
for(const [name,find,replace,caught] of BUGS)test('negative control: '+name+' is caught by '+caught.join(' and '),async()=>{
  assert.equal(source.split(find).length-1,1,'the worker source must hold the line to break exactly once: '+find);
  const bug=await worker(FIXTURE,{edit:s=>s.replace(find,()=>replace)});
  const got=await outcomes(bug.find);
  for(const name of caught)assert.equal(got[name],'fail',name+' must catch this bug: '+JSON.stringify(got));
});
