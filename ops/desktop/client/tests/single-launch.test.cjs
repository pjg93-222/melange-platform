'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {singleLaunch}=require('../single-launch.cjs');
test('concurrent open intents share exactly one pending launch',async()=>{
 let starts=0,complete;const launch=singleLaunch(()=>{starts++;return new Promise(resolve=>{complete=resolve;});});
 const a=launch(),b=launch(),c=launch();assert.equal(a,b);assert.equal(b,c);await new Promise(resolve=>setImmediate(resolve));assert.equal(starts,1);complete('origin');assert.deepEqual(await Promise.all([a,b,c]),['origin','origin','origin']);
});
test('failed launch clears pending state and permits one deliberate retry',async()=>{
 let starts=0;const launch=singleLaunch(async()=>{if(++starts===1)throw new Error('fixture failure');return 'recovered';});
 const first=launch();await assert.rejects(first,/fixture failure/);assert.equal(await launch(),'recovered');assert.equal(starts,2);
});
