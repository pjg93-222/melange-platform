'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const map=require('../app/contribution-map.js');
const studio=require('../app/contribution-studio.js');

test('foreign and unknown-state ZIP collisions remain unmapped while eligible groups combine once',()=>{
  const groups=[{zip5:'02134',state:'MA',amount_cents:10000},{zip5:'02134',state:'NY',amount_cents:2000},{zip5:'02134',state:'ZZ',amount_cents:500},{zip5:'02134',state:'',amount_cents:300},{zip5:'00601',state:'PR',amount_cents:-100}];
  const geometry={points:[{zip5:'02134',longitude:-71,latitude:42},{zip5:'00601',longitude:-66.7,latitude:18.2}]},before=JSON.stringify(groups);
  const data=map.prepare(groups,geometry);
  assert.equal(data.selected_cents,12700);assert.equal(data.mapped_cents,11900);assert.equal(data.unmapped_cents,800);assert.equal(data.mapped_cents+data.unmapped_cents,data.selected_cents);
  assert.equal(data.marks.length,2);assert.deepEqual(data.marks.find(m=>m.zip5==='02134').states,['MA','NY']);assert.equal(data.marks.find(m=>m.zip5==='02134').amount_cents,12000);
  assert.equal(data.unmapped_reasons.state_not_us_postal.amount_cents,500);assert.equal(data.unmapped_reasons.state_unknown.amount_cents,300);assert.match(data.coverage_notes.join(' '),/location is uncertain/);
  assert.deepEqual(map.eligibleZips([...groups,{zip5:'10001',state:'ZZ'},{zip5:'99501',state:''}]),['02134','00601']);assert.equal(JSON.stringify(groups),before);
  const filters=studio.markFilters({dimensions:['zip5','state'],filters:[]},{zip5:'02134',state:data.marks.find(m=>m.zip5==='02134').states});
  const selected=groups.filter(row=>filters.every(f=>f.op==='in'?f.value.includes(row[f.field]):row[f.field]===f.value));
  assert.equal(selected.reduce((sum,row)=>sum+row.amount_cents,0),12000);assert.equal(selected.length,2);assert.ok(selected.every(row=>row.state!=='ZZ'&&row.state!==''));
});

test('postal-associated states and military codes are eligible but never invent missing geometry',()=>{
  for(const state of ['DC','PR','VI','AS','GU','MP','AA','AE','AP','FM','MH','PW']){
    assert.equal(map.zipEligibility({zip5:'00001',state}).eligible,true,state);
    const data=map.prepare([{zip5:'00001',state,amount_cents:100}],{});assert.equal(data.mapped_cents,0);assert.equal(data.unmapped_reasons.location_unavailable.amount_cents,100);
  }
  for(const state of ['ZZ','ON','QC','GB'])assert.equal(map.zipEligibility({zip5:'02134',state}).eligible,false,state);
  for(const state of ['',null,undefined,'Unknown'])assert.equal(map.zipEligibility({zip5:'02134',state}).reason,'state_unknown');
  assert.equal(map.zipEligibility({zip5:2134,state:'MA'}).eligible,false);assert.equal(map.zipEligibility({zip5:'00001',state:'MA'}).zip5,'00001');
  const missing=map.prepare([{zip5:null,state:'MA',amount_cents:100}],{layers:{states:{features:[{properties:{STUSAB:'MA',INTPTLON:-71,INTPTLAT:42}}]}}});assert.equal(missing.mapped_cents,0,'missing ZIP must not silently become a state mark');
  for(const point of [[134.5,7.5],[158.2,6.9],[171.4,7.1]]){const [x,y]=map.project(...point);assert.ok(x>=800&&x<=1000&&y>=430&&y<=640);}
});
