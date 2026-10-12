const {test}=require('node:test');
const assert=require('node:assert/strict');
const math=require('../app/chart-math.js');
const data={columns:[{name:'Candidate'},{name:'Source'},{name:'Amount'}],rows:[['A','Company',100],['A','Company',-10],['A','PAC support',777],['B','PAC support',250],['B','Company',null]],row_numbers:[6,7,8,9,10],independent_series:1};
const config={type:'grouped',x:0,series:1,y:2,aggregation:'sum'};
test('source/candidate comparisons preserve distinct series and missing pairs',()=>{
  for(const type of ['grouped','groupedhorizontal','multiline']){
    const r=math.build(data,{...config,type});
    assert.deepEqual(r.groups.map(g=>g.values.map(v=>v.value)),[[90,777],[null,250]]);
    assert.equal(r.excluded,1);
  }
  assert.throws(()=>math.build(data,{...config,type:'bar'}),/keep funding sources/);
  assert.throws(()=>math.build(data,{...config,series:0}),/Keep Source/);
  assert.throws(()=>math.build(data,{...config,transform:'annual'}),/None/);
});
test('editable row count combines worksheet bounds and filters without mutating data',()=>{
  assert.deepEqual(math.filterRows(data,{start:7,limit:2}),data.rows.slice(1,3));
  assert.deepEqual(math.filterRows(data,{filters:[{column:1,op:'equals',value:'Company'}],limit:2}),data.rows.slice(0,2));
  assert.equal(math.filterRows(data,{limit:0}).length,5);
  assert.throws(()=>math.filterRows(data,{limit:-1}),/positive/);
  assert.throws(()=>math.filterRows(data,{limit:1.5}),/whole/);
  assert.equal(data.rows.length,5);
});
test('row-count statistic remains selected when clicking a chart',()=>{
  const d={columns:[{index:0,name:'Candidate',type:'category',distinct:2},{index:1,name:'Amount',type:'number'}]};
  const next=math.quickConfig(d,{y:-1,aggregation:'count'},'bar');
  assert.equal(next.y,-1);assert.equal(next.aggregation,'count');
});
test('series ordering and colors remain stable between recipient pages',()=>{
  const d={...data,series_order:['Company','PAC support']};
  const page=math.build(d,{...config,start:9});
  assert.deepEqual(page.series,['Company','PAC support']);
  assert.deepEqual(page.groups[0].values.map(v=>v.value),[null,250]);
});
test('swapping comparison axes only includes the newly selected series',()=>{
  const d={...data,series_order:['Company','PAC support']};
  const result=math.build(d,{...config,x:1,series:0});
  assert.deepEqual(result.series,['A','B']);
  assert.deepEqual(result.groups.map(g=>[g.label,g.values.map(v=>v.value)]),[['Company',[90,null]],['PAC support',[777,250]]]);
});
