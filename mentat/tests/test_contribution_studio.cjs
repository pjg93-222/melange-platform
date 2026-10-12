'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../app/contribution-studio.js');

test('all shelf dimensions plus colour contribute to the query grain exactly once',()=>{
  const v=ui.defaults();v.dimensions=['company','cycle'];v.color='cycle';
  assert.deepEqual(ui.buildQuery(v).dimensions,['company','cycle']);
  v.color='party';assert.deepEqual(ui.buildQuery(v).dimensions,['company','cycle','party']);
});
test('row, cycle and company filters are distinct from post-aggregation filters',()=>{
  const v=ui.defaults();v.cycle='2023-2024';v.company='A';v.filters=[{field:'state',op:'eq',value:'VA'}];v.aggregate_filters=[{field:'amount',op:'gte',value:100000}];
  const q=ui.buildQuery(v);assert.equal(q.filters.length,3);assert.equal(q.aggregate_filters.length,1);assert.equal(q.filters[1].value,'2023-2024');assert.equal(q.aggregate_filters[0].value,100000);
});
test('ZIP map queries every matching ZIP instead of the chart page limit',()=>{
  const v=ui.defaults();v.type='map';v.dimensions=['zip5'];v.offset=50;v.limit=25;
  assert.equal(ui.buildQuery(v).offset,0);assert.equal(ui.buildQuery(v).limit,100000);
  assert.deepEqual(ui.buildQuery(v).dimensions,['zip5','state']);assert.deepEqual(v.dimensions,['zip5']);
});

test('supported basis is explicitly gross without changing the source field catalog',()=>{
  const fields=[{id:'amount',label:'Net amount',name:'Net amount',unit:'cents'},{id:'records',label:'Observations'}],before=JSON.stringify(fields);
  assert.equal(ui.basisLabel('original_supported','Certified net'),'Supported original giving (gross subset)');
  assert.equal(ui.basisFields(fields,'original_supported')[0].name,'Supported gross amount');assert.equal(ui.basisFields(fields,'original_supported')[0].label,'Supported gross amount');
  assert.equal(ui.basisFields(fields,'receipt')[0].label,'Net amount');assert.equal(JSON.stringify(fields),before);
});

test('exports retain and compare the query-engine version as well as data fingerprints',()=>{
  const provenance={workbook_sha256:'workbook',index_token:'index',query_engine_sha256:'engine-sha'};
  assert.equal(ui.sameExportSource(provenance,{...provenance}),true);
  for(const key of Object.keys(provenance))assert.equal(ui.sameExportSource(provenance,{...provenance,[key]:'changed'}),false,key);
  const output=ui.csv({columns:[{id:'amount',unit:'cents'}],rows:[{amount:125}],provenance},{dataset:'Banking',basis:'receipt',filters:[],aggregate_filters:[]});
  assert.match(output,/query_engine_sha256/);assert.match(output,/engine-sha/);
});
test('financial filters convert dollars to integer cents and preserve leading-zero ZIPs',()=>{
  const fields=[{id:'amount',role:'measure'},{id:'records',role:'measure'},{id:'zip5',role:'dimension'}];
  assert.equal(ui.filterValue('100.05','amount',fields),10005);assert.equal(ui.filterValue('-1.02','amount',fields),-102);
  assert.equal(ui.filterValue('02134','zip5',fields),'02134');assert.equal(ui.filterValue('2','records',fields),2);
  for(const value of ['1.001','NaN','Infinity','$100','1,000'])assert.throws(()=>ui.filterValue(value,'amount',fields));
  assert.throws(()=>ui.filterValue('1.2','records',fields));
});
test('CSV retains cents precision, named accounting basis and source hash',()=>{
  const result={columns:[{id:'zip5'},{id:'amount',unit:'cents'}],rows:[{zip5:'02134',amount:10005},{zip5:'=cmd',amount:-205}],provenance:{workbook_sha256:'source-sha'}};
  const output=ui.csv(result,{dataset:'Banking',basis:'receipt',filters:[],aggregate_filters:[]});
  assert.match(output,/amount_usd/);assert.match(output,/"100\.05"/);assert.match(output,/"-2\.05"/);assert.match(output,/"02134"/);assert.match(output,/"'=cmd"/);assert.match(output,/source-sha/);assert.match(output,/"receipt"/);
});

test('current calendar cycles and years have a coverage notice without changing data',()=>{
  const now=new Date(2026,8,12),request={dimensions:['cycle','year'],filters:[]},rows=[{cycle:'2023-2024',year:2024,amount:100},{cycle:'2025-2026',year:2026,amount:200}];
  const before=JSON.stringify({request,rows}),notice=ui.calendarNotice(request,rows,now);
  assert.match(notice,/2025–2026 is an ongoing calendar cycle/);assert.match(notice,/2026 is an ongoing calendar year/);assert.match(notice,/Collection coverage is not established/);
  assert.equal(JSON.stringify({request,rows}),before);assert.doesNotMatch(notice,/through today|collected through|complete/i);
  for(const value of ['2025–2026','2025-2026','2026',2026])assert.match(ui.calendarNotice({filters:[{field:'cycle',op:'eq',value}]},[],now),/ongoing calendar cycle/);
  assert.match(ui.calendarNotice({filters:[{field:'year',op:'in',value:['2024','2026']}]},[],now),/ongoing calendar year/);
});

test('calendar notices follow year rollover and do not imply coverage for past or future periods',()=>{
  const request={dimensions:['cycle','year']},now=new Date(2026,11,31,23,59);
  assert.equal(ui.calendarNotice(request,[{cycle:'2023-2024',year:2024},{cycle:'2027-2028',year:2028},{cycle:'2024-2026',year:'Unknown'}],now),'');
  assert.match(ui.calendarNotice(request,[{cycle:'2025-2026'}],now),/2025–2026/);
  const nextYear=new Date(2027,0,1);
  assert.equal(ui.calendarNotice(request,[{cycle:'2025-2026',year:2026}],nextYear),'');
  assert.match(ui.calendarNotice(request,[{cycle:'2027-2028',year:2027}],nextYear),/2027–2028.*2027 is an ongoing calendar year/);
  assert.equal(ui.calendarNotice({},[{cycle:'2025-2026',year:2026}],now),'','unqueried period fields must not invent displayed coverage');
  assert.equal(ui.calendarNotice({filters:[{field:'year',op:'contains',value:'2026'}]},[],now),'');
});
