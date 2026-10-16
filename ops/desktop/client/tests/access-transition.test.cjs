'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {AccessTransition}=require('../access-transition.cjs');
test('chooser result cannot commit after Open or bootstrap started, even if it finished',()=>{
 const gate=new AccessTransition(()=>false),ticket=gate.choose();const finish=gate.begin();
 assert.throws(()=>gate.commit(ticket),/Access changed/);finish();assert.throws(()=>gate.commit(ticket),/Access changed/);
});
test('held bootstrap refuses trust selection and trust commit refuses new bootstrap/Open',()=>{
 const gate=new AccessTransition(()=>false),finish=gate.begin();assert.throws(()=>gate.choose(),/Close/);finish();
 const unlock=gate.commit(gate.choose());assert.throws(()=>gate.begin(),/verified/);assert.throws(()=>gate.choose(),/Close/);unlock();assert.equal(gate.active,0);
 const release=gate.begin();release();release();assert.equal(gate.active,0);
});
test('commit rechecks live backend and a canceled operation does not strand the boundary',()=>{
 let backend=false;const gate=new AccessTransition(()=>backend),ticket=gate.choose();backend=true;assert.throws(()=>gate.commit(ticket),/Access changed/);
 backend=false;const finish=gate.commit(gate.choose());finish();finish();assert.equal(gate.mutating,false);assert.doesNotThrow(()=>gate.choose());
});
