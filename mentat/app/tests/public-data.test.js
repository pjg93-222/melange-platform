'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const publicData = require('../public_data.js');
const source = {sourceDigest:'a'.repeat(64),sourceFingerprint:'b'.repeat(64)};

function element(tag) {
  return {tag,children:[],textContent:'',value:'',hidden:false,disabled:false,listeners:{},
    append(...children) {this.children.push(...children);if(this.tag === 'select' && !this.value && children[0])this.value=children[0].value;},
    replaceChildren(...children) {this.children=[];this.value='';this.append(...children);},
    addEventListener(name,callback) {this.listeners[name]=callback;}};
}
function documentFixture() {
  const elements=new Map();
  for(const id of ['table','status','rows','previous','next','download','source','provenance','reload']) elements.set('public-data-'+id,element(id==='table'?'select':id));
  return {createElement:element,getElementById:id=>elements.get(id),elements};
}
const roles=[2022,2024,2026].flatMap(cycle=>['cm','cn','ccl','pas2'].map(table=>({id:`mentat.fec.${table}.${cycle}`,label:table,cycle,records:101})));
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('fixed endpoints admit only role/page selectors and deterministic official URLs',()=>{
  assert.equal(publicData.endpoint('rows','mentat.fec.cm.2022',1),'/api/public-data/rows?table=mentat.fec.cm.2022&page=1');
  assert.equal(publicData.officialUrl('mentat.fec.pas2.2026'),'https://www.fec.gov/files/bulk-downloads/2026/pas226.zip');
  for(const role of ['../private','https://invalid','mentat.fec.cm.2028'])assert.throws(()=>publicData.endpoint('download',role));
  for(const page of [-1,NaN,Infinity,'0',1e30])assert.throws(()=>publicData.endpoint('rows','mentat.fec.cm.2022',page));
  assert.throws(()=>publicData.endpoint('party','mentat.fec.cm.2022'));
});

test('source identity includes both manifest and canonical descriptor fingerprints',()=>{
  assert.equal(publicData.sameSource(source,{...source}),true);
  assert.equal(publicData.sameSource(source,{...source,sourceFingerprint:'c'.repeat(64)}),false);
  assert.equal(publicData.sameSource({},{}),false);
});

test('source strings become text nodes, including script-like values',()=>{
  const doc=documentFixture(),table=element('table');
  publicData.renderTable(doc,table,{columns:['<img onerror=fixture>'],rows:[['<script>fixture & café</script>']]});
  assert.equal(table.children[0].children[0].children[0].textContent,'<img onerror=fixture>');
  assert.equal(table.children[1].children[0].children[0].textContent,'<script>fixture & café</script>');
  assert.equal('innerHTML' in table,false);
});

test('actual mounted catalogue/rows/download/provenance and paging are passive',async()=>{
  const doc=documentFixture(),requests=[];
  const fetcher=async path=>{
    requests.push(path);const url=new URL(path,'http://localhost'),role=url.searchParams.get('table');
    const value=path.includes('/catalogue')?{...source,available:true,tables:roles}:
      path.includes('/provenance')?{...source,observedAt:'fixture-date',document:{role,memberSha256:'c'.repeat(64)}}:
      {...source,table:role,columns:['CMTE_ID'],rows:[['fixture']],page:Number(url.searchParams.get('page')),firstRow:1,totalRows:101,previousPage:null,nextPage:1};
    return {ok:true,json:async()=>value};
  };
  const view=publicData.mount(doc,fetcher);await view.setActive(true);
  assert.equal(requests.length,3);
  assert.equal(doc.getElementById('public-data-download').href,publicData.endpoint('download',roles[0].id));
  assert.equal(doc.getElementById('public-data-source').href,publicData.officialUrl(roles[0].id));
  doc.getElementById('public-data-next').listeners.click();await tick();
  assert.ok(requests.some(value=>value.endsWith('page=1')));
  assert.ok(requests.every(value=>!value.includes('name=')&&!value.includes('party=')&&!value.includes('forecast')));
});

test('different generation refuses rows and clears old table/download/provenance',async()=>{
  const doc=documentFixture();
  const fetcher=async path=>({ok:true,json:async()=>path.includes('/catalogue')?{...source,available:true,tables:roles}:
    {...source,sourceDigest:'f'.repeat(64),table:roles[0].id,columns:['field'],rows:[['fixture']],document:{role:roles[0].id}}});
  await publicData.mount(doc,fetcher).setActive(true);
  assert.match(doc.getElementById('public-data-status').textContent,/source changed/);
  assert.equal(doc.getElementById('public-data-rows').children.length,0);
  assert.equal(doc.getElementById('public-data-download').hidden,true);
});

test('unavailable context is explicit and does not invent fallback data',async()=>{
  const doc=documentFixture(),requests=[];
  const view=publicData.mount(doc,async path=>{requests.push(path);return{ok:false,json:async()=>({error:'No verified public data source is installed.'})};});
  await view.setActive(true);assert.equal(requests.length,1);
  assert.match(doc.getElementById('public-data-status').textContent,/No verified/);
  assert.equal(doc.getElementById('public-data-table').disabled,true);
});

test('late older page response cannot replace the current source selection',async()=>{
  const doc=documentFixture();let pending;
  const fetcher=async path=>{
    const url=new URL(path,'http://localhost'),role=url.searchParams.get('table');
    if(path.includes('/catalogue'))return{ok:true,json:async()=>({...source,available:true,tables:roles})};
    if(path.includes('/rows')&&role===roles[0].id)return new Promise(resolve=>{pending=()=>resolve({ok:true,json:async()=>({...source,table:role,columns:['field'],rows:[['older']],page:0,firstRow:1,totalRows:1,previousPage:null,nextPage:null})});});
    return{ok:true,json:async()=>path.includes('/provenance')?{...source,document:{role,memberSha256:'c'.repeat(64)},observedAt:'fixture'}:{...source,table:role,columns:['field'],rows:[['current']],page:0,firstRow:1,totalRows:1,previousPage:null,nextPage:null}};
  };
  const view=publicData.mount(doc,fetcher),loading=view.setActive(true);await tick();
  doc.getElementById('public-data-table').value=roles[1].id;
  doc.getElementById('public-data-table').listeners.change();await tick();pending();await loading;
  assert.equal(doc.getElementById('public-data-rows').children[1].children[0].children[0].textContent,'current');
  assert.equal(doc.getElementById('public-data-download').href,publicData.endpoint('download',roles[1].id));
});
