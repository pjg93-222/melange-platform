const {test} = require('node:test');
const assert = require('node:assert/strict');
const math = require('../app/chart-math.js');
const data = {
  columns:[{index:0,name:'Company',type:'category',distinct:2},{index:1,name:'Amount',type:'number',distinct:4},{index:2,name:'Date',type:'date',distinct:3}],
  rows:[['A',10,'2026-01-02'],['A',30,'2026-01-01'],['B',60,'2026-01-03'],['B',null,'2026-01-01']], row_numbers:[6,7,8,9]
};
const config = {type:'bar',x:0,y:1,aggregation:'sum',filters:[]};
test('missing, signed, currency, percent and finite numeric values',()=>{
  assert.equal(math.numeric(null),null); assert.equal(math.numeric(''),null); assert.equal(math.numeric(true),null);
  assert.equal(math.numeric('NaN'),null); assert.equal(math.numeric(Infinity),null); assert.equal(math.numeric('1,2'),null);
  assert.equal(math.numeric('$1,250.25'),1250.25); assert.equal(math.numeric('($20.50)'),-20.5); assert.equal(math.numeric('25%'),.25);
  assert.equal(math.numeric(1e-9),1e-9); assert.equal(math.numeric(0),0);
});
test('accounting percentages retain scale and malformed signs are excluded',()=>{
  assert.equal(math.numeric('(25%)'),-.25);
  for(const value of ['(20','20)','($20','$20)','(-20)','(-$20)']) assert.equal(math.numeric(value),null,value);
  const result=math.build({...data,rows:[['A','(25%)'],['A','75%'],['A','20)']],row_numbers:[6,7,8]},config);
  assert.equal(result.groups[0].value,.5); assert.equal(result.excluded,1);
});
test('known quartiles, Tukey whiskers and outlier',()=>{
  assert.deepEqual(math.box([1,2,3,4,100]),{n:5,q1:2,median:3,q3:4,low:1,high:4,outliers:[100]});
  assert.deepEqual(math.box([8]),{n:1,q1:8,median:8,q3:8,low:8,high:8,outliers:[]});
  assert.equal(math.quantile([1,2,3,4],.25),1.75);
});
test('grouped sum, mean and count retain missing distinction',()=>{
  const sum=math.build(data,config);
  assert.deepEqual(sum.groups,[{label:'A',value:40,n:2},{label:'B',value:60,n:1}]);
  assert.equal(sum.excluded,1);
  assert.equal(math.build(data,{...config,aggregation:'mean'}).groups[0].value,20);
  const count=math.build(data,{...config,aggregation:'count'});
  assert.equal(count.groups[1].value,1); assert.equal(count.excluded,1);
  const rows=math.build(data,{...config,y:-1,aggregation:'count'});
  assert.equal(rows.groups[1].value,2); assert.equal(rows.excluded,0);
});
test('AND filters and worksheet row ranges drive subsection totals',()=>{
  const filtered=math.build(data,{...config,start:7,end:9,filters:[{column:0,op:'equals',value:'a'},{column:1,op:'gte',value:20}]});
  assert.equal(filtered.selected,1); assert.equal(filtered.groups[0].value,30);
  assert.throws(()=>math.build(data,{...config,filters:[{column:1,op:'gte',value:'oops'}]}),/number/);
  assert.throws(()=>math.build(data,{...config,start:9,end:6}),/precede/);
  assert.throws(()=>math.build(data,{...config,filters:[{column:0,op:'equals',value:'absent'}]}),/No rows/);
});
test('pie rejects negative values and all-zero totals',()=>{
  assert.throws(()=>math.build({...data,rows:[['A',-1],['A',10]],row_numbers:[6,7]},{...config,type:'pie'}),/nonnegative/);
  assert.throws(()=>math.build({...data,rows:[['A',0]],row_numbers:[6]},{...config,type:'pie'}),/positive total/);
});
test('line orders dates and histogram counts each observation once',()=>{
  assert.deepEqual(math.build(data,{...config,type:'line',x:2}).groups.map(g=>g.label),['2026-01-01','2026-01-02','2026-01-03']);
  const histogram=math.build(data,{...config,type:'histogram'});
  assert.equal(histogram.groups.reduce((s,g)=>s+g.value,0),3);
  const constant=math.build({...data,rows:[['A',2],['B',2]],row_numbers:[6,7]},{...config,type:'histogram'});
  assert.equal(constant.groups.length,1); assert.equal(constant.groups[0].value,2);
});
test('scatter excludes incomplete pairs; boxes use individual observations',()=>{
  const scatter=math.build(data,{...config,type:'scatter',x:1});
  assert.deepEqual(scatter.points,[{x:10,y:10},{x:30,y:30},{x:60,y:60}]); assert.equal(scatter.excluded,1);
  assert.equal(math.build(data,{...config,type:'box'}).groups[0].median,20);
  assert.throws(()=>math.build(data,{...config,type:'scatter',x:0}),/No numeric/);
});
test('recommendations use detected types and grouping cardinality',()=>{
  const suggested=math.recommend(data.columns);
  assert.equal(suggested[0].type,'bar'); assert(suggested.some(s=>s.type==='box')); assert(suggested.some(s=>s.type==='line'));
  assert.equal(suggested.find(s=>s.type==='pie').aggregation,'count');
  assert(!suggested.some(s=>s.type==='scatter'));
});
test('all missing sums refuse while oversized groups conserve totals in Other',()=>{
  assert.throws(()=>math.build({...data,rows:[['A',null]],row_numbers:[6]},config),/No numeric/);
  const rows=Array.from({length:61},(_,i)=>['Group '+i,i]);
  const result=math.build({...data,rows,row_numbers:rows.map((_,i)=>i+6)},config);
  assert.equal(result.groups.length,61);
  assert.equal(result.groups.at(-1).other,true);
  assert.equal(result.groups.at(-1).folded,1);
  assert.equal(result.groups.reduce((total,g)=>total+g.value,0),1830);
});
test('category labels remain literal and input rows stay unchanged',()=>{
  const evil={...data,rows:[['<script>alert(1)</script>',3],['__proto__',4]],row_numbers:[6,7]};
  const before=JSON.stringify(evil);
  assert.equal(math.build(evil,config).groups[0].label,'<script>alert(1)</script>');
  assert.equal(JSON.stringify(evil),before);
});
test('dense scatter retains every exact valid pair beyond the former point cap',()=>{
  const rows=Array.from({length:125001},(_,i)=>[i/10,-i/3]);rows.push([null,999]);
  const d={columns:[{index:0,name:'Amount',type:'number'},{index:1,name:'Cost',type:'number'}],rows,row_numbers:rows.map((_,i)=>i+1)};
  const result=math.build(d,{type:'scatter',x:0,y:1,aggregation:'sum'});
  assert.equal(result.points.length,125001);assert.equal(result.excluded,1);
  assert.deepEqual(result.points[120000],{x:12000,y:-40000});
  assert.deepEqual(result.points.at(-1),{x:12500,y:-125000/3});
});
test('every chart chooses compatible axes and meaningful numeric measures',()=>{
  const d={columns:[{index:0,name:'Transaction ID',type:'number',distinct:6},{index:1,name:'Election year',type:'number',distinct:6},
    {index:2,name:'Region',type:'category',distinct:3},{index:3,name:'Party',type:'category',distinct:2},
    {index:4,name:'Receipt count',type:'number',distinct:6},{index:5,name:'Amount',type:'number',distinct:6},{index:6,name:'Cost',type:'number',distinct:6}],
    rows:Array.from({length:6},(_,i)=>[1000+i,2018+i,['A','B','C'][i%3],i%2?'D':'R',i+1,(i+1)*100,(i+1)*10]),row_numbers:[1,2,3,4,5,6]};
  for(const type of ['bar','horizontal','pie','doughnut','line','area','scatter','histogram','box','waterfall','pareto','radar','grouped','groupedhorizontal','multiline']){
    const next=math.quickConfig(d,{},type);
    assert.equal(next.y,5,type+' uses money, not IDs/year/count');
    assert.doesNotThrow(()=>math.build(d,next),type);
    if(['line','area','multiline'].includes(type))assert.equal(next.x,1,type+' uses the time axis');
    if(['grouped','groupedhorizontal','multiline'].includes(type))assert.notEqual(next.x,next.series);
  }
  assert.equal(math.quickConfig(d,{y:6,aggregation:'median'},'bar').y,6);
  assert.equal(math.quickConfig(d,{y:6,aggregation:'median'},'bar').aggregation,'median');
  assert.equal(math.quickConfig(d,{y:0},'bar').y,5);
  assert.equal(math.recommend(d.columns)[0].y,5);
  assert.equal(math.quickConfig(d,{y:-1,aggregation:'count'},'bar').y,-1);
  assert.equal(math.quickConfig(d,{y:-1,aggregation:'count'},'histogram').y,5);
});
test('automatic compatibility preserves independent funding and refuses signed share charts',()=>{
  const d={columns:[{index:0,name:'Recipient',type:'category',distinct:3},{index:1,name:'Source / activity',type:'category',distinct:2},{index:2,name:'Amount',type:'number'}],
    rows:[['A','Company',100],['B','PAC',20],['C','PAC',-10]],row_numbers:[1,2,3],independent_series:1};
  for(const [type,expected] of [['bar','grouped'],['line','multiline'],['horizontal','groupedhorizontal']]){
    const next=math.quickConfig(d,{},type);assert.equal(next.type,expected);assert.equal(next.series,1);assert.doesNotThrow(()=>math.build(d,next));
  }
  assert.throws(()=>math.quickConfig(d,{},'pie'),/independent funding/);
  const merged={...d,independent_series:null};
  for(const type of ['pie','doughnut','pareto','radar'])assert.throws(()=>math.quickConfig(merged,{},type),/nonnegative/);
  assert.throws(()=>math.build(merged,{type:'radar',x:0,y:2,aggregation:'sum'}),/nonnegative/);
  assert.throws(()=>math.quickConfig({columns:d.columns.slice(2)}, {},'grouped'),/two distinct grouping/);
});
test('date-only numeric worksheets show one useful distribution instead of a box per date',()=>{
  const d={columns:[{index:0,name:'Date',type:'date',distinct:90},{index:1,name:'Amount',type:'number',distinct:90}],
    rows:Array.from({length:90},(_,i)=>[new Date(Date.UTC(2023,0,i+1)).toISOString().slice(0,10),i]),row_numbers:Array.from({length:90},(_,i)=>i+1)};
  const next=math.quickConfig(d,{x:0,y:1},'box');
  assert.equal(next.x,-1);
  const result=math.build(d,next);assert.equal(result.groups.length,1);assert.equal(result.groups[0].n,90);assert.equal(result.groups[0].median,44.5);
});

test('money cannot be repeated across split ZIPs and coverage normalizes duplicate tokens',()=>{
  const d={columns:[{name:'ZIPs',type:'category'},{name:'Amount',type:'number'}],rows:[['10001; 10001; 10002',100],['10001',50]],row_numbers:[2,3]};
  for(const aggregation of ['sum','mean','median','min','max'])assert.throws(()=>math.build(d,{...config,aggregation,splitMulti:true}),/coverage-only/);
  const coverage=math.build(d,{...config,y:-1,aggregation:'count',splitMulti:true});
  assert.deepEqual(coverage.groups.map(g=>[g.label,g.value]),[['10001',2],['10002',1]]);
  assert.equal(coverage.selected,3);
  assert.match(coverage.note,/do not allocate money/);
  assert.deepEqual(d.rows,[['10001; 10001; 10002',100],['10001',50]],'source facts remain unchanged');
});

test('COUNT(field) excludes null and empty values but retains zeros and null-only groups',()=>{
  const d={...data,rows:[['A',null],['A',''],['B',0],['B','text']],row_numbers:[2,3,4,5]};
  const result=math.build(d,{...config,aggregation:'count'});
  assert.deepEqual(result.groups.map(g=>[g.label,g.value]),[['A',0],['B',2]]);
  assert.equal(result.excluded,2);
  const grouped=math.build({...d,columns:[...d.columns,{name:'Series'}],rows:d.rows.map(r=>[...r,null,'S'])},{...config,type:'grouped',series:3,aggregation:'count'});
  assert.deepEqual(grouped.groups.map(g=>[g.label,g.values[0].value]),[['A',0],['B',2]]);
});

test('multiple dimensions preserve distinct tuples and Other conserves their money',()=>{
  const d={columns:[{name:'Company',type:'category'},{name:'Amount',type:'number'},{name:'State',type:'category'}],rows:[['A',10,'NY'],['A',20,'CA'],['B',30,'NY'],['A',5,'NY']],row_numbers:[2,3,4,5]};
  const result=math.build(d,{...config,dimensions:[0,2]});
  assert.deepEqual(result.groups.map(g=>[JSON.parse(g.label),g.value]),[[['A','NY'],15],[['A','CA'],20],[['B','NY'],30]]);
  assert.equal(result.groupingLabel,'Company × State');
  const folded=math.build(d,{...config,dimensions:[0,2],maxGroups:1});
  assert.equal(folded.groups.reduce((total,g)=>total+g.value,0),65);
  assert.equal(folded.groups.at(-1).value,35);
});

test('source and measure accounting contracts refuse unsafe totals and allow declared facts',()=>{
  assert.throws(()=>math.build({...data,accounting:{safe_to_sum:false,reason:'Mixed summary grain.'}},config),/Mixed summary grain/);
  const columns=data.columns.map(c=>({...c}));columns[1].accounting={safe_to_sum:false,reason:'Choose Analysis Amount.'};
  assert.throws(()=>math.build({...data,columns},config),/Choose Analysis Amount/);
  const d={columns:[{name:'Role'},{name:'Amount',type:'number'}],rows:[['fact',10],['fact',20],['total',30]],row_numbers:[2,3,4],accounting:{safe_to_sum:true,row_role_column:0,included_row_roles:['fact']}};
  assert.throws(()=>math.build(d,config),/Summary and detail/);
  const selected=math.build(d,{...config,filters:[{column:0,op:'equals',value:'fact'}]});
  assert.equal(selected.groups[0].value,30);
});

test('independent accounting dimensions must stay separated and source notes survive',()=>{
  const d={columns:[{name:'Company'},{name:'Amount',type:'number'},{name:'Basis'}],rows:[['A',100,'receipt'],['A',100,'attribution']],row_numbers:[2,3],note:'Same money appears in two accounting layers.',accounting:{safe_to_sum:true,independent_dimensions:[2]}};
  assert.throws(()=>math.build(d,config),/can overlap/);
  const result=math.build(d,{...config,dimensions:[0,2]});
  assert.equal(result.groups.length,2);
  assert.match(result.totalRefusal,/can overlap/);
  assert.match(result.note,/Same money appears/);
  assert.throws(()=>math.build(d,{...config,dimensions:[0,2],maxGroups:1}),/Other cannot combine overlapping/);
  const selected=math.build(d,{...config,filters:[{column:2,op:'equals',value:'receipt'}]});
  assert.equal(selected.groups[0].value,100);
  assert.equal(selected.totalRefusal,undefined);
});
