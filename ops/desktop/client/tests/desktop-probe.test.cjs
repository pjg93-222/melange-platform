'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {getEventListeners}=require('node:events');
const {desktopProbe}=require('../desktop-probe.cjs');
const childCode=`const fs=require('node:fs');let input='';process.stdin.on('data',chunk=>input+=chunk);process.stdin.on('end',()=>{const value=JSON.parse(input);fs.writeFileSync(value.marker,String(process.pid));if(value.wait)setInterval(()=>{},1000);else process.stdout.write(JSON.stringify({validated:true,district:value.district}));});`;
const command={binary:process.execPath,args:['-e',childCode]};
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-probe-fixture-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true,maxRetries:5}));return path.join(root,'child-started');}
async function until(check){const deadline=Date.now()+15000;while(!check()){if(Date.now()>deadline)throw new Error('Fixture child did not reach expected state');await new Promise(resolve=>setTimeout(resolve,20));}}

test('an already canceled probe rejects before spawning and does not expose the cancellation reason',async()=>{
 const childProcess=require('node:child_process'),original=childProcess.spawn,modulePath=require.resolve('../desktop-probe.cjs');let spawned=0;
 childProcess.spawn=()=>{spawned++;throw new Error('must not spawn');};delete require.cache[modulePath];
 try{const isolated=require('../desktop-probe.cjs').desktopProbe,controller=new AbortController();controller.abort(new Error('private cancellation reason'));
  await assert.rejects(()=>isolated(command,'fixture-probe',{}, {signal:controller.signal}),error=>error.message==='Local data verification canceled.');assert.equal(spawned,0);assert.equal(getEventListeners(controller.signal,'abort').length,0);
 }finally{childProcess.spawn=original;delete require.cache[modulePath];}
});

test('canceling an in-flight probe terminates its real child and releases the abort listener',async t=>{
 const marker=fixture(t),controller=new AbortController();let pid;
 t.after(()=>{if(pid)try{process.kill(pid);}catch{}});
 const pending=desktopProbe(command,'fixture-probe',{marker,wait:true},{signal:controller.signal,timeout:30000});
 const rejected=assert.rejects(pending,error=>error.message==='Local data verification canceled.');
 await until(()=>fs.existsSync(marker));pid=Number(fs.readFileSync(marker,'utf8'));assert.ok(Number.isInteger(pid)&&pid>0);
 assert.ok(getEventListeners(controller.signal,'abort').length>0);controller.abort(new Error('private cancellation reason'));await rejected;
 await until(()=>{try{process.kill(pid,0);return false;}catch(error){if(error.code==='ESRCH')return true;throw error;}});
 await until(()=>getEventListeners(controller.signal,'abort').length===0);pid=null;
});

test('a completed real probe returns its result and later cancellation has no listener or effect',async t=>{
 const marker=fixture(t),controller=new AbortController();
 const result=await desktopProbe(command,'fixture-probe',{marker,district:'co-05'},{signal:controller.signal});
 assert.deepEqual(result,{validated:true,district:'co-05'});assert.equal(getEventListeners(controller.signal,'abort').length,0);controller.abort(new Error('late cancellation'));assert.deepEqual(result,{validated:true,district:'co-05'});
});
