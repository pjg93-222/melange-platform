// The committee name in the record browser: filter() in staffer-inventory-worker.js. D-083 took the member, committee and policy ids (row columns 3 to 5) out of the
// searched words, and with them the committee's own name, because a committee id carries it: container:office:<chamber>:<committee name>, for example
// container:office:senate:AGRICULTURE, NUTRITION & FORESTRY. Paul wants the name searchable again; the machine prefix is not. So the words of a query are looked for, by
// word prefix as names are, in the name, summary, source and aliases (columns 1, 2, 6 and 11) and in the committee name of column 4, the person's first committee id,
// never in container:office:<chamber>: itself, and a whole identifier still matches whole, in any case, and lists its rows ahead of the rows that match by words.
// Columns 3 and 5 (member and policy ids) offer no name, and neither does a committee id of another shape (politico-affiliation:<hex>, container:hsag).
// The worker as it was before this correction (f23a40aee, what LIVE ran) is read from git as the negative control, and deliberate bugs of the new filter are each caught.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const REL='fec/surfaces/staffer_live/staffer-inventory-worker.js';
const ROOT=path.resolve(__dirname,'../../..');
const source=fs.readFileSync(path.resolve(__dirname,'../../surfaces/staffer_live/staffer-inventory-worker.js'),'utf8');

// The worker as it was before this correction: the committee name in column 4 was not searched.
const BEFORE='f23a40aee4c749bae9db132599a853a52c81d9bf';
function fromGit(rev,rel){
  try{return execFileSync('git',['-C',ROOT,'show',rev+':'+rel],{encoding:'utf8',stdio:['ignore','pipe','ignore'],maxBuffer:1<<25});}catch(error){return null;}
}
const before=fromGit(BEFORE,REL);
const NO_GIT=before===null?'the pre-correction worker (commit '+BEFORE.slice(0,9)+') is not in this checkout':false;

// Columns as the worker reads them: 0 id, 1 label, 2 summary, 3 member, 4 committee, 5 policy, 6 source, 7 pending, 8 kind, 11 aliases.
const row=(id,label,o={})=>[id,label,o.summary||'',o.member||'',o.committee||'',o.policy||'',o.source||'Quorum',false,'person',false,'unknown',o.aliases||''];
// The committee ids are the real ones, as the producers emit them (container:office:<chamber>:<payroll office key>, upper case, with commas, ampersands, hyphens, parentheses and
// apostrophes), plus one written in mixed case.
const AGRI='container:office:senate:AGRICULTURE, NUTRITION & FORESTRY',BANK='container:office:senate:BANKING, HOUSING & URBAN AFFAIRS',FINSVC='container:office:house:COMMITTEE ON FINANCIAL SERVICES';
const SCI='container:office:house:COMM ON SCIENCE, SPACE AND TECH',HELP='container:office:senate:HEALTH, EDU,LABOR&PENS',HOUSEADMIN='container:office:house:HOUSE ADMINISTRATION';
const RULES='container:office:house:COMMITTEE ON RULES',VETS="container:office:senate:VETERANS' AFFAIRS -",MIXED='Container:Office:Senate:Judiciary (Majority) Subcommittee on Antitrust';
const DIGEST='politico-affiliation:9f1c0a7b3d5e8a2c4b6d1e07',UUID='3f2a1b4c-5d6e-4f80-9a0b-c1d2e3f4a5b6';
const FIXTURE=[
  row('person:hill:person:01','Dana Ruiz',{member:'A000370',committee:AGRI,source:'Payroll'}),                // 0
  row('person:hill:person:02','Lee Park',{committee:AGRI,source:'Payroll'}),                                  // 1: the browser keeps only a person's FIRST committee id
  row('person:hill:person:03','Sam Ortiz',{committee:BANK,source:'Payroll'}),                                 // 2
  row('person:hill:person:04','Kim Vo',{committee:FINSVC,source:'Payroll'}),                                  // 3
  row('person:hill:person:05','Ana Cole',{committee:SCI,source:'Payroll'}),                                   // 4
  row('person:hill:person:06','Jo Hart',{committee:HELP,source:'Payroll'}),                                   // 5
  row('person:hill:person:07','Max Reed',{committee:HOUSEADMIN,source:'Payroll'}),                            // 6: house is part of the committee's NAME here
  row('person:hill:person:08','Eli Shaw',{committee:RULES,source:'Payroll'}),                                 // 7: house sits only in the machine prefix
  row('person:hill:person:09','Ira Lund',{committee:VETS,source:'Payroll'}),                                  // 8
  row('person:hill:person:10','Una Judd',{committee:MIXED,source:'Payroll'}),                                 // 9: a prefix and a name in mixed case
  row('politico:'+UUID,'Pat Moss',{committee:DIGEST,source:'Politico'}),                                      // 10: a digest, no readable part
  row('quorum:11','Ned Orr',{committee:'container:hsag'}),                                                    // 11: a code of another shape
  Object.assign(row('quorum:12','Bea Cho'),{3:null,4:null,5:null}),                                           // 12: null codes
  row('person:hill:person:13','Cy Dunn'),                                                                      // 13: no committee
  // Container-shaped ids in the member and policy columns are not committee ids: only column 4 offers a name.
  row('person:hill:person:14','Wes Pryor',{member:'container:office:senate:ENERGY & NATURAL RESOURCES',policy:'container:office:house:COMMITTEE ON TAXATION'}), // 14
  Object.assign(row('quorum:15','Seven Holder'),{4:7}),                                                        // 15: a number as a code
];
const ALL=FIXTURE.map((_,i)=>i);
// A decoy whose label holds the word agriculture (it matches by words and comes first in row order), a row whose own id is the whole committee id, and a row whose
// label is the committee id spelled out (it matches the whole id by words, as a person's name would).
const RANK_FIXTURE=[
  row('decoy:1','Agriculture Hale'),                                                              // 0
  row('person:hill:person:01','Dana Ruiz',{committee:AGRI}),                                      // 1
  row('person:hill:person:02','Lee Park',{committee:AGRI}),                                       // 2
  row(AGRI,'Collision Person'),                                                                   // 3: named by its own id, which is also the committee id of rows 1 and 2
  row('person:hill:person:09','Ira Lund',{committee:VETS}),                                       // 4
  row('decoy:2',AGRI.toLowerCase()),                                                              // 5: its label is the committee id: it matches by words, after the rows the id names or attaches
];
// Chamber words that people do read: a summary and a name that say senate, house and office, beside committees that have those words only in the machine prefix.
const CHAMBER_FIXTURE=[
  row('quorum:21','Ned Orr',{summary:'Counsel · Senate Office of Rep. Hale',committee:BANK}),      // 0
  row('quorum:22','Una Judd',{summary:'Staff Director, House Rules',committee:RULES}),             // 1
  row('quorum:23','Max Reed',{committee:HOUSEADMIN}),                                              // 2: a committee that has house in its name
  row('quorum:24','Kim Vo',{committee:FINSVC}),                                                    // 3: house only in the prefix
  row('quorum:25','Jo Hart',{committee:HELP}),                                                     // 4: senate only in the prefix
];

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

const RULING={
  // The committee name is text a person reads: a word, in any case, the start of a word (the browser matches words by prefix, as it does names), several words together.
  async names(find){
    const cases=[['agriculture',[0,1]],['AGRICULTURE',[0,1]],['Agriculture',[0,1]],['nutrition',[0,1]],['forestry',[0,1]],['agri',[0,1]],['nutri',[0,1]],['fores',[0,1]],
      ['agriculture nutrition',[0,1]],['forestry agriculture',[0,1]],['agriculture, nutrition & forestry',[0,1]],['nutrition & forestry',[0,1]],
      ['banking',[2]],['housing',[2]],['urban aff',[2]],['banking, housing & urban affairs',[2]],['affairs',[2,8]],
      ['financial services',[3]],['fin',[3]],['comm on science',[4]],['science, space',[4]],['sci',[4]],['tech',[4]],
      ['edu,labor&pens',[5]],['edu',[5]],['labor',[5]],['pens',[5]],['health',[5]],
      ['house administration',[6]],['house',[6]],['admin',[6]],['rules',[7]],['committee on rules',[7]],['on rules',[7]],
      ["veterans'",[8]],['veterans',[8]],['judiciary',[9]],['JUDICIARY',[9]],['antitrust',[9]],['(majority)',[9]],['majority',[9]],['subcommittee on antitrust',[9]],
      ['dana agriculture',[0]],['agriculture dana',[0]],['lee agriculture',[1]],['dana banking',[]],['sam agriculture',[]],['agriculture banking',[]],['agriculture rules',[]],['zzz',[]]];
    for(const [q,want] of cases)assert.deepEqual(await find(q),want,'the committee name must be searched like a name: '+JSON.stringify(q));
    for(const q of ['  agriculture  ','\tAGRICULTURE\n',' AgRiCuLtUrE'])assert.deepEqual(await find(q),[0,1],JSON.stringify(q));
    // By word prefix, not by substring: the middle or end of a word finds nothing, as it does for a name.
    for(const q of ['culture','ricultu','anking','ervices','ministration','ence','tion','ruiz ultu'])assert.deepEqual(await find(q),[],'a part inside a word is not a word prefix: '+JSON.stringify(q));
  },
  // The machine prefix is never searched, alone, in pieces, or joined to the name; a chamber word that sits only in the prefix finds nothing.
  async prefix(find){
    const none=['container','contain','ontainer','tainer','container:office','container:office:','container:office:senate','container:office:senate:','container:office:house','container:office:house:',
      'office','offic','ffice','office:senate','office:house:','senate','senate:','nate:','ice:sen','e:senate:','senate:agri','senate: agriculture','container agriculture','office banking','container:office:senate:agri',
      'container:office:senate:agriculture','container:office:senate:agriculture, nutrition & forestry x','hon:','container:o'];
    for(const q of none)assert.deepEqual(await find(q),[],'a piece of the machine prefix must find nothing by way of the id: '+JSON.stringify(q));
    // 'house' is a word of one committee's name (row 6, which the names check finds) and only the prefix of the other house committees (rows 3, 4 and 7): it must not reach them.
    for(const q of ['house','HOUSE:',' house '])for(const i of await find(q))assert.equal(i,6,'house finds no one but the committee that has it in its name: '+JSON.stringify(q)+' reached row '+i);
  },
  // The same words, read where a person reads them: a summary or a name that says senate, house or office is still found by it, and the prefix adds nobody.
  async chamberWords(find){
    assert.deepEqual(await find('senate'),[0],'senate is searched where a person reads it (a summary), not in container:office:senate:');
    assert.deepEqual(await find('office'),[0],'office is searched where a person reads it, not in container:office:');
    assert.deepEqual(await find('SENATE:'),[0]);
    assert.deepEqual(await find('house'),[1,2],'house is in one summary and one committee name, and in the prefix of the others');
    assert.deepEqual(await find('house rules'),[1]);assert.deepEqual(await find('senate banking'),[0]);assert.deepEqual(await find('house senate'),[]);
    assert.deepEqual(await find('container'),[]);assert.deepEqual(await find('container:office:house:'),[]);
  },
  // A whole identifier of any kind still matches whole, in any case, trimmed, and lists its rows (committee id rows after the row a whole node id names).
  async wholeCodes(find){
    for(const [id,rows] of [[AGRI,[0,1]],[BANK,[2]],[FINSVC,[3]],[SCI,[4]],[HELP,[5]],[HOUSEADMIN,[6]],[RULES,[7]],[VETS,[8]],[MIXED,[9]],[DIGEST,[10]],['container:hsag',[11]],['7',[15]],['A000370',[0]]])
      for(const q of variants(id))assert.deepEqual(await find(q),rows,'the whole id '+JSON.stringify(q)+' lists the rows that hold it, in row order');
    for(const [i,r] of FIXTURE.entries())for(const q of variants(r[0]))assert.deepEqual((await find(q)).slice(0,1),[i],'the whole node id '+JSON.stringify(q)+' must list its row first');
    assert.deepEqual((await find(UUID.toUpperCase())).slice(0,1),[10],'the bare uuid of a politico id is a whole id too');
  },
  // Named by id first, then the rows that hold the id as a committee id, then the rows that match only by words; each in row order.
  async ranking(find,people=RANK_FIXTURE){
    assert.deepEqual(ownerRows(people,AGRI),[3]);
    for(const q of variants(AGRI))assert.deepEqual(await find(q),[3,1,2,5],'the row the id names, then the rows attached to it, then the row that only says it: '+JSON.stringify(q));
    assert.deepEqual(await find('agriculture'),[0,1,2,5],'by words, the decoy that says agriculture, the committee rows and the label that spells the id, in row order');
    assert.deepEqual(await find('ira vet'),[4]);
  },
  // An id with no readable part contributes none (a committee id of another shape, a digest, a null, a number), and so does a container-shaped id in column 3 or 5.
  async otherShapes(find){
    const none=['politico-affiliation','affiliation','9f1c0a7b','9f1c','0a7b3d5e','b6d1e07','hsag','container:hs','null','undefined','a000','a000370x','politico-aff','seven nobody',
      'energy','natural','resources','energy & natural resources','taxation','committee on taxation','container:office:senate:energy'];
    for(const q of none)assert.deepEqual(await find(q),[],'an id with no readable part, or one outside column 4, is searched whole only: '+JSON.stringify(q));
    assert.deepEqual(await find('container:hsag'),[11]);assert.deepEqual(await find('9F1C0A7B3D5E8A2C4B6D1E07'),[],'a bare digest is not an id');
    assert.deepEqual(await find('seven'),[15],'the label is still a word');
  },
  // Nothing else changed: the other columns still search by word prefix, accents folded, all words required, and the empty query lists everyone.
  async unchanged(find){
    const cases=[['dana',[0]],['ruiz dana',[0]],['uiz',[]],['du',[13]],['payroll',[0,1,2,3,4,5,6,7,8,9]],['politico',[10]],['quorum',[11,12,13,14,15]],['pat moss',[10]],['seven holder',[15]],
      ['wes pryor',[14]],['dana nobody',[]],['zzz nobody',[]],['',ALL],['   ',ALL],['::',ALL]];
    for(const [q,want] of cases)assert.deepEqual(await find(q),want,'unchanged: '+JSON.stringify(q));
  },
};
// Each check runs against its own fixture; a worker is loaded per fixture.
const FIXTURES={ranking:RANK_FIXTURE,chamberWords:CHAMBER_FIXTURE};
const run=async(name,code=source)=>RULING[name]((await worker(FIXTURES[name]||FIXTURE,code)).find);
const outcomes=async code=>{
  const out={};
  for(const name of Object.keys(RULING)){try{await run(name,code);out[name]='pass';}catch(e){if(!(e instanceof assert.AssertionError))throw e;out[name]='fail';}}
  return out;
};
const ALL_PASS=Object.fromEntries(Object.keys(RULING).map(k=>[k,'pass']));

for(const [name,title] of Object.entries({
  names:'the committee name in column 4 is searched like a name: a word, the start of a word, several words, in any case',
  prefix:'container:office:<chamber>: is never searched, alone, in pieces or joined to the name',
  chamberWords:'senate, house and office are found where a person reads them and not in the prefix',
  wholeCodes:'a whole committee id, node id, member id or uuid still lists its rows, trimmed and in any case',
  ranking:'the row a whole id names comes first, then the rows attached to it, then the rows that match only by words',
  otherShapes:'a committee id of another shape, a null, a number and the other id columns contribute no words',
  unchanged:'every other column searches as before',
}))test('ruling: '+title,async()=>run(name));

// ---- a seeded world: the new answers equal the corrected rule written out by brute force, for every 1- and 2-character query, committee names, pieces of them and pieces of ids
const tokens=s=>s.normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
// The committee name of a column-4 value, by string operations (independent of the worker's regular expression): the part after container:office:house: or container:office:senate:.
const PREFIXES=['container:office:house:','container:office:senate:'];
const nameOf=code=>{const text=String(code),low=text.toLowerCase();for(const p of PREFIXES)if(low.startsWith(p)&&low.length>p.length)return text.slice(p.length);return '';};
// Brute force, independent of the worker: each row is classified on its own. A row is named by its id (or bare uuid), else attached by a code, else matched by words
// over the name, summary, source and aliases and the committee name of column 4 (names=false leaves that one out: the code before the correction).
function expected(people,text,{names=true}={}){
  const words=tokens(text),want=text.trim().toLowerCase(),named=[],attached=[],rest=[];
  people.forEach((r,i)=>{
    const id=String(r[0]).toLowerCase();
    if(want&&(id===want||(id.startsWith('politico:')&&id.slice(9)===want))){named.push(i);return;}
    if(want&&[3,4,5].some(c=>r[c]!==null&&r[c]!==undefined&&String(r[c]).toLowerCase()===want)){attached.push(i);return;}
    const have=tokens([1,2,6,11].map(c=>r[c]||'').concat(names?nameOf(r[4]):'').join(' '));
    if(words.every(w=>have.some(t=>t.startsWith(w))))rest.push(i);
  });
  return [...named,...attached,...rest];
}
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
  const words=['Alex','Adams','Élodie','Gagné','Casey','Rivera','Jordan','Lee','Morgan','Quill','Priya','Nair','Pat','Orwell','Zed','Zeta','Beatrice','Hale','Staff','Cole','Porter','Hill','Person'];
  const members=['A000370','S001223','V000127','M001204','a000370x'],policies=['policy:politico:'+'1b253a2520329806ed6f5be6','policy:politico:'+'41eb6c45a6fc36fac8e74f56','BANKING'];
  const shapes=[()=>'politico:'+uuid(rand),()=>'hill:person:'+pick(['alex-adams','zed-zeta','pat-orwell','cole-porter'])+'-'+Math.floor(rand()*30),
    ()=>'quorum:'+Math.floor(rand()*900),()=>'lda:'+Math.floor(rand()*9000),()=>'person:'+hex(rand,12),()=>'Mixed:Case-'+Math.floor(rand()*40),()=>'politico:'+uuid(rand)];
  const committee=()=>{if(rand()<.04)return pick(['container:hsag','HSBA',DIGEST,'politico-affiliation:'+hex(rand,24),null,7]);const id=pick(REAL);return rand()<.3?title(id):id;};
  const people=[];
  for(let i=0;i<count;i++){
    people.push(row(pick(shapes)(),pick(words)+' '+pick(words),{
      summary:rand()<.5?pick(words)+' '+pick(['Director','Counsel','Staff','Senate Banking','Office of Rep. Hale','House Ways Means','Container Services']):'',member:rand()<.4?pick(members):'',
      committee:rand()<.6?committee():'',policy:rand()<.25?pick(policies):'',
      source:pick(['Payroll','Politico','Quorum','Lobbying','Payroll|Politico']),aliases:rand()<.3?pick(words)+' '+pick(words):''}));
  }
  people.push(people[3].slice());                                                         // two rows with one id
  people.push(Object.assign(row('own:'+hex(rand,6),'Own Id Holder'),{3:null,4:null,5:null}));
  people.push(row(AGRI,'Collision Person'),row(AGRI.toLowerCase(),'Collision Lower'));   // rows named by a whole committee id that other rows hold in column 4
  return people;
}
function queriesFor(people){
  const out=new Set(),alphabet='abcdefghijklmnopqrstuvwxyz0123456789:-.,&\'() ';
  for(const a of alphabet){out.add(a);for(const b of alphabet)out.add(a+b);}
  for(const q of ['container','contain','office','offic','senate','senate:','house','house:','container:office','container:office:','container:office:senate:','container:office:house:','office:senate','ice:sen','e:senate:','  senate  ','Senate:','HOUSE'])out.add(q);
  for(const id of REAL.concat(REAL.map(title))){
    const name=id.replace(/^container:office:(house|senate):/i,''),words=name.toLowerCase().match(/[a-z0-9]+/g)||[];
    for(const v of variants(id))out.add(v);
    out.add(name);out.add(name.toLowerCase());out.add(name.toUpperCase());out.add(words.join(' '));out.add(words.slice().reverse().join(' '));out.add(words.slice(0,2).join(' '));
    for(const w of words){out.add(w);out.add(w.toUpperCase());for(let k=2;k<w.length;k+=2){out.add(w.slice(0,k));out.add(w.slice(k));}}
    out.add('senate:'+words[0]);out.add('senate: '+words[0]);out.add('house:'+words[0]);out.add('container '+words[0]);out.add('office '+words[0]);out.add('container:office:senate:'+words[0]);
    for(let k=1;k<id.length;k+=4){out.add(id.slice(0,k));out.add(id.slice(k));out.add(id.slice(k,k+6));}
  }
  for(let i=0;i<people.length;i+=Math.max(1,Math.floor(people.length/70))){
    const id=String(people[i][0]),label=people[i][1],aliases=people[i][11];
    for(const v of variants(id))out.add(v);
    if(id.startsWith('politico:'))for(const v of variants(id.slice(9)))out.add(v);
    for(let n=1;n<=Math.min(id.length,14);n+=2)out.add(id.slice(0,n));
    for(const code of [people[i][3],people[i][4],people[i][5]].filter(c=>c!==null&&c!==undefined&&c!==''))for(const v of variants(String(code)))out.add(v);
    out.add(label);out.add(label.toUpperCase());out.add(label.split(' ')[0].slice(0,3));if(aliases)out.add(aliases);
    const named=nameOf(people[i][4]);
    if(named)out.add(label.split(' ')[0].toLowerCase()+' '+named.split(/\s+/)[0].toLowerCase());   // a word of the name and a word of the committee name, both required
  }
  return [...out];
}
test('over a seeded world the new answers equal the corrected rule, for every 1- and 2-character query, committee names, pieces of them and pieces of ids',async t=>{
  const people=world(mulberry32(20261003),120),queries=queriesFor(people);
  const now=await worker(people);
  let nonEmpty=0,viaName=0,wholeCommitteeQueries=0,prefixOnly=0;
  for(const q of queries){
    const got=await now.find(q),want=expected(people,q);
    assert.deepEqual(got,want,'rule: '+JSON.stringify(q));
    if(got.length&&got.length<people.length)nonEmpty++;
    // A query whose answer changes when the committee names are left out: found by way of a committee name.
    if(expected(people,q,{names:false}).join()!==want.join())viaName++;
    if(people.some(r=>r[4]!==null&&r[4]!==undefined&&String(r[4]).toLowerCase()===q.trim().toLowerCase()&&nameOf(r[4])))wholeCommitteeQueries++;
    if(q.trim()&&/^(container|contain|container:office:?|container:office:(senate|house):?|office:senate|ice:sen|e:senate:|senate:)$/.test(q.trim().toLowerCase())&&!want.length)prefixOnly++;
  }
  // The world must be able to tell the correction from the old search and from a search of the whole id: names that now find rows, whole committee ids, pieces of the prefix that find none.
  const seen={queries:queries.length,nonEmpty,viaName,wholeCommitteeQueries,prefixOnly};
  assert.ok(seen.queries>4000&&seen.viaName>600&&seen.wholeCommitteeQueries>100&&seen.prefixOnly>=5,'the world must exercise the correction: '+JSON.stringify(seen));
  t.diagnostic('seeded world: '+people.length+' rows, '+JSON.stringify(seen));
});

// ---- negative controls
test('negative control: the worker before this correction fails the committee-name checks and passes the others',{skip:NO_GIT},async()=>{
  const old=await outcomes(before);
  assert.deepEqual(old,{names:'fail',prefix:'pass',chamberWords:'fail',wholeCodes:'pass',ranking:'fail',otherShapes:'pass',unchanged:'pass'},
    'f23a40aee searched the name, summary, source and aliases and whole ids only: it never searched the prefix, and never the committee name: '+JSON.stringify(old));
  assert.deepEqual(await outcomes(source),ALL_PASS);
});
test('negative control: the worker before this correction misses names the corrected rule finds, and finds nothing extra, over the seeded world',{skip:NO_GIT},async t=>{
  const people=world(mulberry32(20261003),120),queries=queriesFor(people).filter((_,i)=>i%2===0),old=await worker(people,before);
  let differ=0,spurious=0,missing=0,reordered=0;
  for(const q of queries){
    const got=await old.find(q),want=expected(people,q),have=new Set(got),wanted=new Set(want);
    for(const i of got)if(!wanted.has(i))spurious++;
    for(const i of want)if(!have.has(i))missing++;
    if(got.join()!==want.join())differ++;
    if(got.length===want.length&&got.join()!==want.join()&&[...got].sort().join()===[...want].sort().join())reordered++;
  }
  assert.ok(differ>300&&missing>1500,'the old worker must answer differently: '+JSON.stringify({differ,spurious,missing,reordered}));
  assert.equal(spurious,0,'the old worker found nothing the corrected rule does not find: it only missed the committee names');
  assert.equal(reordered,0,'the same rows in another order never happens: the correction adds rows to the word matches and nothing else');
  t.diagnostic('worker before the correction over the same world: '+JSON.stringify({queries:queries.length,differ,spurious,missing,reordered}));
});
const NAMED='named=committeeTokens(r[4])',WHOLE="words=m[1].normalize('NFKD')";
const BUGS=[
  ['the whole committee id is searched as words, prefix included',WHOLE,"words=m[0].normalize('NFKD')",['prefix','chamberWords']],
  ['only senate committees are read','(?:house|senate)','(?:senate)',['names']],
  ['the prefix is matched in lower case only','(.+)$/i,NO_WORDS','(.+)$/,NO_WORDS',['names']],
  ['the member and policy columns are read as committee ids too',NAMED,'named=[...committeeTokens(r[3]),...committeeTokens(r[4]),...committeeTokens(r[5])]',['otherShapes']],
  ['the committee names are dropped',NAMED,'named=NO_WORDS',['names','ranking']],
  ['a committee name is searched by substring, not by word prefix','named.some(token=>token.startsWith(w))','named.some(token=>token.includes(w))',['names']],
  ['the words of a committee name are kept for the first committee id seen','committeeWords.get(code)','committeeWords.get(committeeWords.keys().next().value)',['names']],
  ['an affiliation digest is read as a committee name','container:office:(?:house|senate):(.+)','(?:container:office:(?:house|senate):|politico-affiliation:)(.+)',['otherShapes']],
  ['any text after the first colon is read as a name','^container:office:(?:house|senate):(.+)$','^[^:]*:(.+)$',['prefix','otherShapes']],
  ['a whole committee id no longer lists its rows first','matches=named.concat(attached,others)','matches=named.concat(others,attached)',['ranking']],
];
for(const [name,find,replace,caught] of BUGS)test('negative control: '+name+' is caught by '+caught.join(' and '),async()=>{
  assert.equal(source.split(find).length-1,1,'the worker source must hold the text to break exactly once: '+find);
  const got=await outcomes(source.replace(find,()=>replace));
  for(const check of caught)assert.equal(got[check],'fail',check+' must catch this bug: '+JSON.stringify(got));
});
test('negative control: a search of the whole committee id as words differs from the corrected rule over the seeded world',async t=>{
  const find="words=m[1].normalize('NFKD')",people=world(mulberry32(20261003),120),queries=queriesFor(people).filter((_,i)=>i%2===0);
  assert.equal(source.split(find).length-1,1);
  const bug=await worker(people,source.replace(find,()=>"words=m[0].normalize('NFKD')"));
  let spurious=0;
  for(const q of queries){const got=await bug.find(q),want=new Set(expected(people,q));for(const i of got)if(!want.has(i))spurious++;}
  assert.ok(spurious>1500,'the world must be able to tell a search of the prefix from the rule: '+spurious);
  t.diagnostic('whole-id-as-words bug over the same world: '+spurious+' rows the rule does not list');
});
