const {test}=require('node:test');
const assert=require('node:assert/strict');
const {descendants,selectRows,PAGE}=require('../graph-explorer.js');
const edges=[{source:'A',destination:'B',cycle:'2024'},{source:'B',destination:'A',cycle:'2024'},
  {source:'B',destination:'C',cycle:'2026'},{source:'B',destination:'D',cycle:'2024'}];
test('routes are cycle-safe and do not join different elections',()=>{
  assert.deepEqual([...descendants('A',edges,'2024')],['A','B','D']);
  const rows=['A','B','C','D'].map(committee=>({committee,cycle:'2026',direction:'Support',candidate:committee,amount:1}));
  assert.deepEqual(selectRows(rows,{direction:'Support',focus:'A',branches:true,edges}).map(r=>r.committee),['A']);
});
test('searches all relationships without dropping signed or small observations',()=>{
  const rows=Array.from({length:1000},(_,i)=>({candidate:`Name ${i}`,candidate_id:`H${i}`,committee:'A',cycle:'2024',direction:'Support',amount:i===999?-100000:i,route:['Root','A']}));
  assert.equal(selectRows(rows,{direction:'Support'}).length,1000);
  assert.equal(selectRows(rows,{direction:'Support',query:'name 999'})[0].amount,-100000);
  assert.equal(selectRows(rows,{direction:'Support',sort:'absolute'})[0].candidate,'Name 999');
  assert.equal(selectRows(rows,{direction:'Support',sort:'ascending'})[0].candidate,'Name 999');
  assert.equal(selectRows(rows,{direction:'Oppose'}).length,0);
  assert.equal(selectRows(rows,{direction:'Support',query:'Root'}).length,1000);
  assert.equal(PAGE,40);
});

test('natural candidate name order matches the name as filed',()=>{
  const rows=[{candidate:'BARR, GARLAND ANDY',candidate_id:'H0KY06104',committee:'C00836221',
    committee_name:'DEFEND AMERICAN JOBS',cycle:'2026',direction:'Support',amount:7170138.4,
    route:['C00835959','C00836221']}];
  for (const query of ['Andy Barr','Barr Andy','H0KY06104','Andy C00835959'])
    assert.equal(selectRows(rows,{direction:'Support',query}).length,1);
  assert.equal(selectRows(rows,{direction:'Support',query:'Barry Moore'}).length,0);
});
