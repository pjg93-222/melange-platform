import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {createCongressCookieReplayRequestContext: create} = await import(new URL('../../lib/congress_context_http_fast_path.mjs', import.meta.url));
const good='https://www.congress.gov/bill/119th-congress/house-bill/1/text';
const response=(url=good,status=200,body='synthetic',headers={})=>({url,status,headers:new Headers(headers),text:async()=>body});
test('foreign and malformed initial URLs never receive the synthetic cookie',async()=>{
  for(const url of ['https://example.invalid/','http://www.congress.gov/x','https://user@www.congress.gov/x','https://www.congress.gov:444/x','bad']){
    let calls=0;const context=create({cookieHeader:'fixture=only',fetchImpl:async()=>{calls++;return response(url);}});
    await assert.rejects(context.get(url));assert.equal(calls,0);
  }
});
test('cross-origin redirect is refused before a second request',async()=>{
  const calls=[];const context=create({cookieHeader:'fixture=only',fetchImpl:async(url,opts)=>{
    calls.push({url,redirect:opts.redirect});return response(url,302,'',{'location':'https://example.invalid/private'});
  }});
  await assert.rejects(context.get(good));assert.equal(calls.length,1);assert.equal(calls[0].redirect,'manual');
});
test('same-origin redirect reaches one cached body read',async()=>{
  const calls=[];let reads=0;const final=good+'?format=txt';
  const context=create({cookieHeader:'fixture=only',fetchImpl:async(url,opts)=>{
    calls.push(url);assert.equal(opts.headers.Cookie,'fixture=only');
    return url===good ? response(url,302,'',{'location':final}) : {...response(final),text:async()=>{reads++;return 'body';}};
  }});
  const result=await context.get(good);assert.equal(result.url(),final);assert.equal(await result.text(),'body');
  assert.equal(await result.text(),'body');assert.equal(reads,1);assert.equal(calls.length,2);
});
test('deadline includes stalled body even when stub ignores abort',async()=>{
  let signal;const context=create({cookieHeader:'fixture=only',fetchImpl:async(url,opts)=>{
    signal=opts.signal;return {...response(),text:()=>new Promise(()=>{})};
  }});
  const operation=context.get(good,{timeout:15}).then(r=>r.text());
  const outcome=await Promise.race([operation.then(()=> 'resolved',()=> 'refused'),new Promise(r=>setTimeout(()=>r('outer-timeout'),100))]);
  assert.equal(outcome,'refused');assert.equal(signal.aborted,true);
});
test('deadline includes stalled headers even when stub ignores abort',async()=>{
  let signal;const context=create({cookieHeader:'fixture=only',fetchImpl:async(url,opts)=>{signal=opts.signal;return new Promise(()=>{});}});
  const outcome=await Promise.race([context.get(good,{timeout:15}).then(()=> 'resolved',()=> 'refused'),new Promise(r=>setTimeout(()=>r('outer-timeout'),100))]);
  assert.equal(outcome,'refused');assert.equal(signal.aborted,true);
});
test('invalid timeout refuses before fetch',async()=>{
  for(const timeout of [NaN,Infinity,0,-1,2**31]){
    let calls=0;const context=create({cookieHeader:'fixture=only',fetchImpl:async()=>{calls++;return response();}});
    await assert.rejects(context.get(good,{timeout}));assert.equal(calls,0);
  }
});
test('unbounded redirect loop is refused',async()=>{
  let calls=0;const context=create({cookieHeader:'fixture=only',fetchImpl:async(url)=>{calls++;return response(url,302,'',{'location':good});}});
  await assert.rejects(context.get(good));assert.ok(calls<=6);
});
test('unexpected loaded origin is refused and transport error is sanitized',async()=>{
  await assert.rejects(create({cookieHeader:'fixture=only',fetchImpl:async()=>response('https://example.invalid/')}).get(good));
  await assert.rejects(create({cookieHeader:'fixture=only',fetchImpl:async()=>{throw Error('fixture=only private data');}}).get(good),e=>!e.message.includes('fixture=only'));
});
test('late headers or redirect-body cancellation cannot initiate another request after deadline',async()=>{
  for(const stall of ['headers','redirect-cancel']){
    let release;let calls=0;let bodyReads=0;
    const held=new Promise(resolve=>{release=resolve;});
    const redirected={...response(good,302,'',{'location':good+'?next=1'}),
      body:{cancel:async()=>{if(stall==='redirect-cancel')await held;}},
      text:async()=>{bodyReads++;return 'must not read';}};
    const context=create({cookieHeader:'fixture=only',fetchImpl:async()=>{
      calls++;if(stall==='headers')await held;return redirected;
    }});
    await assert.rejects(context.get(good,{timeout:15}),/timed out/);
    release();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls,1,stall);assert.equal(bodyReads,0,stall);
  }
});
test('non-success response retains adapter status and body semantics',async()=>{
  const r=await create({cookieHeader:'fixture=only',fetchImpl:async()=>response(good,503,'retry')}).get(good);
  assert.equal(r.status(),503);assert.equal(await r.text(),'retry');
});
