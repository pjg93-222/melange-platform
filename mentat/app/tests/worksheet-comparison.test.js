const {test}=require('node:test');
const assert=require('node:assert/strict');
const {compare}=require('../worksheet-comparison.js');
const data=rows=>({columns:[{name:'Group',type:'category'},{name:'Amount',type:'number'}],rows,row_numbers:rows.map((_,i)=>i+6)});
const source=(rows,aggregation='sum',extra={})=>({label:aggregation,data:data(rows),config:{x:0,y:1,aggregation,...extra}});
test('independent worksheet aggregation preserves missing groups and signed values',()=>{
 const result=compare([source([['A',10],['A',30],['B',-5]],'mean'),source([['A',100],['C',0]])]);
 assert.deepEqual(result.rows.find(r=>r.group==='A'),{group:'A',source_0:20,source_1:100});
 assert.deepEqual(result.rows.find(r=>r.group==='B'),{group:'B',source_0:-5,source_1:null});
 assert.deepEqual(result.rows.find(r=>r.group==='C'),{group:'C',source_0:null,source_1:0});
});
test('row ranges and filters remain independent, all six aggregates work',()=>{
 for(const [aggregation,expected]of [['sum',60],['mean',20],['count',3],['median',20],['min',10],['max',30]]){
  const r=compare([source([['A',10],['A',20],['A',30]],aggregation),source([['A',999],['A',5],['B',50]],'sum',{start:7,filters:[{column:0,op:'equals',value:'A'}]})]);
  assert.equal(r.rows[0].source_0,expected);assert.equal(r.rows[0].source_1,5);
 }
});
test('unsafe worksheet totals remain refused, not silently coerced',()=>{
 const s=source([['A',5]]);s.data.accounting={safe_to_sum:false,reason:'Overlapping summary rows'};
 assert.throws(()=>compare([s]),/Overlapping/);
});
