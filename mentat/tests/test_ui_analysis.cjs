const {test}=require('node:test');
const assert=require('node:assert/strict');
const math=require('../app/chart-math.js'),state=require('../app/ui-state.js');
const data=(rows,name='Date')=>({columns:[{index:0,name},{index:1,name:'Amount'}],rows,row_numbers:rows.map((_,i)=>i+1)});
const config={type:'line',x:0,y:1,aggregation:'sum',transform:'annual',period:'year'};
test('one-click charts choose valid columns and preserve the selected statistic',()=>{
  const d={columns:[{index:0,name:'Region',type:'category',distinct:3},{index:1,name:'Volume',type:'number'},
    {index:2,name:'Cost',type:'number'},{index:3,name:'Date',type:'date'}]};
  const scatter=math.quickConfig(d,{y:2},'scatter');
  assert.equal(scatter.x,1);assert.equal(scatter.y,2);
  assert.equal(math.quickConfig(d,{y:2},'bar').x,0);
  const forecast=math.quickConfig(d,{y:2},'forecast');
  assert.equal(forecast.x,3);assert.equal(forecast.period,'year');assert.equal(forecast.transform,'forecast');
  d.columns[3]={index:3,name:'Election cycle',type:'number'};
  assert.equal(math.quickConfig(d,{y:3},'annual').y,2);
  assert.equal(math.quickConfig(d,{y:2},'annual').period,'cycle');
  assert.throws(()=>math.quickConfig(d,{y:2},'yoy'),/two-year/);
  assert.throws(()=>math.quickConfig({columns:d.columns.slice(0,2)},{y:1},'scatter'),/two numeric/);
  assert.throws(()=>math.quickConfig({columns:d.columns.slice(0,2)},{y:1},'forecast'),/no date/);
});
test('elapsed clocks use elapsed seconds; terminal runs never advance',()=>{
  assert.deepEqual(state.timer({state:'running',elapsed_seconds:100,age_seconds:5,status:{estimated_remaining_seconds:80}},10),{elapsed:'0:01:50',remaining:'About 0:01:05'});
  assert.deepEqual(state.timer({state:'stopped',elapsed_seconds:100,status:{}},1000),{elapsed:'0:01:40',remaining:'Stopped'});
  assert.equal(state.timer({state:'running',age_seconds:80,status:{estimated_remaining_seconds:10}}).remaining,'Waiting for checkpoint');
  assert.equal(state.timer({state:'running',age_seconds:10,status:{estimated_remaining_seconds:5}}).remaining,'Estimate exceeded');
  assert.equal(state.timer({state:'running',age_seconds:0,status:{estimated_remaining_seconds:null}}).remaining,'Estimating…');
});
test('terminal checkpoints retain stopped and launch failure states',()=>{
  assert.deepEqual(state.timer({state:'running',elapsed_seconds:100,status:{stage:'stopped'}},1000),{elapsed:'0:01:40',remaining:'Stopped'});
  assert.deepEqual(state.timer({state:'running',elapsed_seconds:100,status:{stage:'launch failed'}},1000),{elapsed:'0:01:40',remaining:'Failed'});
  assert.equal(state.timer({state:'completed',elapsed_seconds:100,status:{stage:'failed'}}).remaining,'Failed');
});
test('company matching normalizes whitespace and case but never drops identity-bearing punctuation',()=>{
  assert.equal(state.normalizeName('  HARBORLIGHT  Credit Union '),state.normalizeName('Harborlight Credit Union'));
  assert.notEqual(state.normalizeName('ABC Inc'),state.normalizeName('ABC LLC'));
});
test('raw title preview keeps vice presidents out of the chief tier',()=>{
  assert.equal(state.titlePosition('CEO'),'Chief');
  assert.equal(state.titlePosition('President'),'Chief');
  assert.equal(state.titlePosition('EVP'),'Middle-Management');
  assert.equal(state.titlePosition('Software engineer'),'Employees');
  assert.equal(state.titlePosition(''),'Unreported');
});
test('annual amounts aggregate same-year receipts including refunds',()=>{
  const result=math.build(data([['2023-01-01',100],['2023-02-01',-10],['2024-01-01',120],['bad',99]]),config);
  assert.deepEqual(result.groups.map(g=>g.value),[90,120]);assert.equal(result.excluded,1);
});
test('year-over-year excludes missing periods and nonpositive prior bases',()=>{
  const result=math.build(data([[2020,100],[2021,120],[2023,200],[2024,0],[2025,50]]),{...config,transform:'yoy'});
  assert.deepEqual(result.groups.map(g=>[g.year,g.value]),[[2021,20],[2024,-100]]);
  assert.throws(()=>math.build(data([[2022,100],[2024,200]],'Election cycle'),{...config,transform:'yoy'}),/two-year/);
});
test('election-phase baseline learns observed cycle differences without an assumed uplift',()=>{
  const d=data([[2018,40],[2020,90],[2022,50],[2024,120]],'Election cycle');
  const before=JSON.stringify(d);
  const r=math.build(d,{...config,period:'cycle',transform:'forecast',horizon:2,as_of:'2025-01-01'});
  assert.deepEqual(r.groups.slice(-2).map(g=>[g.year,g.value,g.forecast_basis_year]),[[2026,50,2022],[2028,120,2024]]);
  assert.match(r.note,/historical baseline/);assert.match(r.note,/No calibrated uncertainty interval/);
  assert.equal(JSON.stringify(d),before);
  const reversed=data([[2018,90],[2020,40],[2022,120],[2024,50]],'Election cycle');
  assert.deepEqual(math.build(reversed,{...config,period:'cycle',transform:'forecast',as_of:'2025-01-01'}).groups.slice(-2).map(g=>g.value),[120,50]);
});
test('annual forecasts match all four calendar phases and refuse sparse history',()=>{
  const d=data(Array.from({length:8},(_,i)=>[2016+i,[80,20,40,30,100,25,60,35][i]]),'Year');
  const cfg={...config,transform:'forecast',horizon:4,as_of:'2024-01-01'};
  const r=math.build(d,cfg);
  assert.deepEqual(r.groups.slice(-4).map(g=>[g.year,g.value]),[[2024,100],[2025,25],[2026,60],[2027,35]]);
  assert.throws(()=>math.build(data([[2020,10],[2021,20],[2022,30]]),cfg),/eight completed/);
  assert.throws(()=>math.build(data([[2012,5],...d.rows.slice(1)]),cfg),/consecutive/);
});
test('current, partial and incomplete periods stay visible but cannot train the baseline',()=>{
  const d={...data([[2016,80],[2018,30],[2020,100],[2022,40],[2024,120],[2026,9000]],'Election cycle'),partial_periods:[2024]};
  const cfg={...config,period:'cycle',transform:'forecast',as_of:'2026-09-11'};
  const r=math.build(d,cfg);
  assert.deepEqual(r.groups.filter(g=>g.partial).map(g=>g.year),[2024,2026]);
  assert.deepEqual(r.groups.slice(-2).map(g=>[g.year,g.value,g.forecast_basis_year]),[[2028,100,2020],[2030,40,2022]]);
  assert.match(r.note,/partial observations/);
  const insufficient={...d,rows:d.rows.slice(1),row_numbers:d.row_numbers.slice(1)};
  assert.throws(()=>math.build(insufficient,cfg),/four completed/);
  assert.throws(()=>math.build({...d,verified_data_through:'2019-06-30'},cfg),/four completed/);
  assert.throws(()=>math.build(d,{...cfg,limit:6}),/row-count limits/);
  const incomplete={...d,rows:[...d.rows,[2020,null]],row_numbers:[...d.row_numbers,7]};
  assert.throws(()=>math.build(incomplete,cfg),/four completed/);
});
test('cycle differences use two-year steps and preserve signed values',()=>{
  const r=math.build(data([['2021-2022',100],['2023-2024',80],['2025-2026',90]],'Cycle'),{...config,transform:'change',period:'cycle'});
  assert.deepEqual(r.groups.map(g=>g.value),[-20,10]);
});
test('calendar dates and annual years aggregate into their ending-even-year cycles',()=>{
  const cfg={...config,period:'cycle'};
  for(const [name,rows] of [
    ['Date',[['2023-01-02',10],['2024-02-29',20],['2025-12-31T23:30:00-05:00',30],['2026-01-01',40],['2026-03-01',null],['2023-02-29',900],['2024-13-01',900],['2023-2024',900]]],
    ['Year',[[2023,10],[2024,20],[2025,30],[2026,40],[2026,null],['2023-2024',900]]],
  ]){
    const result=math.build(data(rows,name),cfg);
    assert.deepEqual(result.groups.map(g=>[g.year,g.value,g.n]),[[2024,30,2],[2026,70,2]],name);
    assert.equal(result.excluded,rows.length-4,name);
  }
});
test('explicit cycles retain even end years and only valid consecutive two-year ranges',()=>{
  const rows=[[2024,10],['2023–2024',20],['2025-2026',30],['2024-2026',900],['2023-2025',900],[2023,900],['2024-02-29',900],['2023-2024junk',900]];
  const result=math.build(data(rows,'Election cycle'),{...config,period:'cycle'});
  assert.deepEqual(result.groups.map(g=>[g.year,g.value]),[[2024,30],[2026,30]]);
  assert.equal(result.excluded,5);
});
test('cycle forecasts carry annual partial and missing-value metadata into the containing cycle',()=>{
  const rows=Array.from({length:12},(_,i)=>[2015+i,15+i]);
  const cfg={...config,period:'cycle',transform:'forecast',as_of:'2026-09-11'};
  for(const metadata of [{partial_periods:[2023]},{period_coverage:{2023:{complete:false}}},{complete_periods:rows.map(r=>r[0]).filter(year=>year!==2023)}]){
    const result=math.build({...data(rows,'Year'),...metadata},cfg);
    assert.deepEqual(result.groups.filter(g=>g.partial).map(g=>g.year),[2024,2026]);
    assert.deepEqual(result.groups.slice(-2).map(g=>[g.year,g.value,g.forecast_basis_year]),[[2028,39,2020],[2030,43,2022]]);
  }
  const missing=data(rows.map(([year,value])=>[year,year===2023?null:value]),'Year');
  assert.deepEqual(math.build(missing,cfg).groups.filter(g=>g.partial).map(g=>g.year),[2024,2026]);
  const oddCurrent=math.build(data(rows.slice(0,-1),'Year'),{...cfg,as_of:'2025-09-11'});
  assert.deepEqual(oddCurrent.groups.filter(g=>g.partial).map(g=>g.year),[2026]);
});
test('one observed member year or one explicitly complete member cannot make a completed cycle',()=>{
  const cfg={...config,period:'cycle',transform:'forecast',as_of:'2027-01-01'};
  const evenOnly=data([[2016,10],[2018,20],[2020,30],[2022,40]],'Year');
  assert.throws(()=>math.build(evenOnly,cfg),/four completed/);
  const allYears=data(Array.from({length:8},(_,i)=>[2015+i,i+1]),'Year');
  assert.throws(()=>math.build({...allYears,complete_periods:[2016,2018,2020,2022]},cfg),/four completed/);
  assert.doesNotThrow(()=>math.build({...allYears,complete_periods:allYears.rows.map(r=>r[0])},cfg));
  const explicit={...data(evenOnly.rows,'Election cycle'),complete_periods:[2016,2018,2020,2022]};
  assert.doesNotThrow(()=>math.build(explicit,cfg));
});
test('expanded chart aggregations calculate median and sort Pareto',()=>{
  const d=data([['A',10],['A',30],['A',20],['B',90]]);
  const result=math.build(d,{type:'pareto',x:0,y:1,aggregation:'median'});
  assert.deepEqual(result.groups.map(g=>[g.label,g.value]),[['B',90],['A',20]]);
});
