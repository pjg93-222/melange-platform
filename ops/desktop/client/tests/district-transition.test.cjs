'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {DistrictTransition,districtFingerprint}=require('../district-transition.cjs');
const {bindingCache}=require('../access-bootstrap.cjs');
const {attachLaunchHeader}=require('../runtime.cjs');
const {authorizeAvailable}=require('../access-gate.cjs');
function fixture(){
 let configuration={district:'co-05',dataDirectory:'data'},url='http://127.0.0.1:1001/walk?old=1',id=1000;const created=[],events=[];
 const grant={serverId:'server',deviceId:'device',principalId:'user',accountIdentity:'admin',role:'owner',districtIds:['co-05','or-05','ct-01']};
 const make=()=>{const runtime={origin:'http://127.0.0.1:'+(++id),child:{},launchPath:'/',requestsAllowed:true,starts:0,stops:0,activity:async()=>false,authorization:{accepted:{grant},owner:true},async start(selected){this.starts++;this.child={};this.configuration=structuredClone(selected);this.authorization={accepted:{grant},owner:true,principal:{districtId:selected.district}};return this.origin;},async stop(){this.stops++;this.child=null;this.configuration=null;}};created.push(runtime);return runtime;};
 let current=make();const initial=current;
 const controller=new DistrictTransition({read:()=>configuration,write:value=>{configuration=value;},scope:()=>grant.districtIds,verify:async()=>true,createRuntime:make,current:()=>current,activate:value=>{current=value;},navigate:async value=>{url=value;},location:()=>url,canReveal:async()=>true,freeMemory:()=>8*1024**3,log:value=>events.push(value)});
 controller.remember(configuration,current);
 return {controller,initial,created,events,get current(){return current;},get configuration(){return configuration;},get url(){return url;},setUrl:value=>{url=value;}};
}
test('switch preserves window navigation and reuses a freshly authorized visited backend',async()=>{
 const f=fixture();await f.controller.switch('or-05');assert.equal(f.configuration.district,'or-05');assert.equal(f.url,'http://127.0.0.1:1002/walk');assert.equal(f.initial.stops,0);
 await f.controller.switch('co-05');assert.equal(f.current,f.initial);assert.equal(f.initial.starts,1);assert.equal(f.created.length,2);assert.equal(f.events.findLast(e=>e.phase==='backend-ready').cacheHit,true);
});
test('busy activity refuses without changing settings or launching a backend',async()=>{
 const f=fixture();f.initial.activity=async()=>true;await assert.rejects(f.controller.switch('or-05'),/calculation/);assert.equal(f.configuration.district,'co-05');assert.equal(f.created.length,1);
});
test('unknown Terrier activity keeps renderer unsaved-work veto available',async()=>{const f=fixture();f.initial.activity=async()=>null;await f.controller.switch('or-05');assert.equal(f.configuration.district,'or-05');});
test('beforeunload veto restores prior runtime, URL and settings without stopping it',async()=>{
 const f=fixture();f.controller.navigate=async()=>{f.controller.cancel();throw new Error('ERR_ABORTED');};await assert.rejects(f.controller.switch('or-05'),/ERR_ABORTED/);
 assert.equal(f.current,f.initial);assert.equal(f.configuration.district,'co-05');assert.equal(f.url,'http://127.0.0.1:1001/walk?old=1');assert.equal(f.initial.stops,0);assert.equal(f.created[1].stops,1);
});
test('navigation failure after document replacement reloads previous URL',async()=>{
 const f=fixture();let calls=0;f.controller.navigate=async url=>{f.setUrl(url);if(++calls===1)throw new Error('ERR_FAILED');};await assert.rejects(f.controller.switch('or-05'),/ERR_FAILED/);
 assert.equal(calls,2);assert.equal(f.url,'http://127.0.0.1:1001/walk?old=1');assert.equal(f.current,f.initial);
});
test('early renderer-ready is retained until final authorization succeeds',async()=>{
 const f=fixture();f.controller.navigate=async url=>{f.setUrl(url);f.controller.rendererReady();};await f.controller.switch('or-05');assert.equal(f.events.filter(e=>e.phase==='renderer-ready').length,1);
});
test('suspend stops warm and starting backends and cancels pending activation',async()=>{
 const f=fixture();await f.controller.switch('or-05');let started,release;const begun=new Promise(r=>{started=r;}),blocked=new Promise(r=>{release=r;});
 f.controller.createRuntime=()=>{const runtime={...f.initial,stops:0,async start(){started();await blocked;},async stop(){this.stops++;}};f.created.push(runtime);return runtime;};
 const switching=f.controller.switch('ct-01');await begun;await f.controller.suspend();release();await assert.rejects(switching,/cancelled/);assert.ok(f.initial.stops>0);assert.ok(f.created[2].stops>0);assert.equal(f.configuration.district,'or-05');assert.equal(f.current.requestsAllowed,false);
});
test('changed dataset fingerprint stops stale retained runtime before fresh launch',async()=>{
 const f=fixture();let version=1;f.controller.fingerprint=configuration=>configuration.district+version;f.controller.remember(f.configuration,f.current);await f.controller.switch('or-05');version++;await f.controller.switch('co-05');assert.notEqual(f.current,f.initial);assert.equal(f.initial.stops,1);assert.equal(f.events.findLast(e=>e.phase==='backend-ready').cacheHit,false);
});
test('a changed density file invalidates a retained district runtime using the real file fingerprint',async t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-density-fingerprint-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const data=path.join(root,'data'),profile=path.join(root,'profile'),density=path.join(data,'districts','co-05','density.json');
 fs.mkdirSync(path.dirname(density),{recursive:true});fs.writeFileSync(density,'synthetic density before');
 const f=fixture();f.controller.fingerprint=configuration=>districtFingerprint(profile,{...configuration,dataDirectory:data});f.controller.remember(f.configuration,f.current);
 await f.controller.switch('or-05');fs.writeFileSync(density,'a changed synthetic density dataset');await f.controller.switch('co-05');
 assert.notEqual(f.current,f.initial);assert.equal(f.initial.stops,1);assert.equal(f.events.findLast(e=>e.phase==='backend-ready').cacheHit,false);
});
test('writes made while a district is active do not defeat warm reuse after switching away and back',async()=>{
 const f=fixture(),writes={};f.controller.fingerprint=configuration=>configuration.district+(writes[configuration.district]||0);f.controller.remember(f.configuration,f.current);
 writes['co-05']=1;await f.controller.switch('or-05');await f.controller.switch('co-05');
 assert.equal(f.current,f.initial);assert.equal(f.initial.stops,0);assert.equal(f.events.findLast(e=>e.phase==='backend-ready').cacheHit,true);
});
test('dataset replacement during warm authorization is rejected and cannot refresh stale fingerprint',async()=>{
 const f=fixture();let version=1;f.controller.fingerprint=configuration=>configuration.district+version;f.controller.remember(f.configuration,f.current);await f.controller.switch('or-05');
 f.initial.start=async()=>{version++;};await assert.rejects(f.controller.switch('co-05'),/data changed/);assert.equal(f.configuration.district,'or-05');assert.equal(f.initial.stops,1);assert.equal(f.controller.pool.has('co-05'),false);
});

function cached(configuration,catalogueSha256='a'.repeat(64)){return {...configuration,ownerGitHubData:{schemaVersion:1,district:configuration.district,cacheRoot:'private-fixture-cache',catalogueSha256}};}
const cachedFingerprint=configuration=>JSON.stringify([configuration.dataDirectory,configuration.district,configuration.ownerGitHubData?.catalogueSha256]);
test('verified cache selection drives the runtime and pool while persisted owner settings keep their original state root',async()=>{
 const f=fixture(),verified=[];f.controller.fingerprint=cachedFingerprint;f.controller.verify=async selected=>{verified.push(structuredClone(selected));return cached(selected);};
 await f.controller.switch('or-05');
 assert.deepEqual(verified,[{district:'or-05',dataDirectory:'data'}]);
 assert.deepEqual(f.configuration,{district:'or-05',dataDirectory:'data'});
 assert.deepEqual(f.current.configuration,cached(f.configuration));
 assert.equal(f.controller.pool.get('or-05').fingerprint,cachedFingerprint(f.current.configuration));
 assert.equal(Object.hasOwn(f.configuration,'ownerGitHubData'),false,'the ephemeral cache mapping is never saved in owner settings');
});
test('the outgoing cached runtime is fingerprinted using its effective roots and reused after a return',async()=>{
 const f=fixture();f.controller.fingerprint=cachedFingerprint;f.initial.configuration=cached(f.configuration);f.controller.remember(f.initial.configuration,f.initial);
 f.controller.verify=async selected=>cached(selected);
 await f.controller.switch('or-05');await f.controller.switch('co-05');
 assert.equal(f.current,f.initial);assert.equal(f.initial.stops,0);assert.equal(f.events.findLast(e=>e.phase==='backend-ready').cacheHit,true);
 assert.deepEqual(f.configuration,{district:'co-05',dataDirectory:'data'});
});
test('a newer immutable GitHub dataset invalidates only the stale warm runtime and preserves approved state settings',async()=>{
 const f=fixture(),revisions={'co-05':'a'.repeat(64),'or-05':'a'.repeat(64)};f.controller.fingerprint=cachedFingerprint;
 f.initial.configuration=cached(f.configuration,revisions['co-05']);f.controller.remember(f.initial.configuration,f.initial);
 f.controller.verify=async selected=>cached(selected,revisions[selected.district]);
 await f.controller.switch('or-05');const oregon=f.current;revisions['co-05']='b'.repeat(64);await f.controller.switch('co-05');
 assert.notEqual(f.current,f.initial);assert.equal(f.initial.stops,1);assert.equal(oregon.stops,0);
 assert.equal(f.current.configuration.ownerGitHubData.catalogueSha256,revisions['co-05']);
 assert.equal(f.events.findLast(e=>e.phase==='backend-ready').cacheHit,false);assert.deepEqual(f.configuration,{district:'co-05',dataDirectory:'data'});
});
test('failed cached-data navigation restores original settings, URL and old effective runtime without touching its account roots',async()=>{
 const f=fixture(),previous=structuredClone(f.configuration),oldEffective=cached(previous);f.initial.configuration=structuredClone(oldEffective);
 f.controller.fingerprint=cachedFingerprint;f.controller.remember(oldEffective,f.initial);f.controller.verify=async selected=>cached(selected,'b'.repeat(64));
 let calls=0;f.controller.navigate=async url=>{f.setUrl(url);if(++calls===1)throw new Error('ERR_FAILED');};
 await assert.rejects(f.controller.switch('or-05'),/ERR_FAILED/);
 assert.deepEqual(f.configuration,previous);assert.equal(f.current,f.initial);assert.deepEqual(f.initial.configuration,oldEffective);
 assert.equal(f.initial.stops,0);assert.equal(f.url,'http://127.0.0.1:1001/walk?old=1');
 assert.equal(f.created[1].stops,1);assert.equal(f.created[1].configuration,null);assert.equal(f.controller.pool.has('or-05'),false);
 assert.equal(f.controller.pool.get('co-05').fingerprint,cachedFingerprint(oldEffective));
});
test('effective verification for a different district is refused before starting or changing settings',async()=>{
 const f=fixture();f.controller.verify=async selected=>cached({...selected,district:'ct-01'});
 await assert.rejects(f.controller.switch('or-05'),/different district/);assert.equal(f.created.length,1);assert.equal(f.current,f.initial);assert.deepEqual(f.configuration,{district:'co-05',dataDirectory:'data'});
});
test('low memory evicts only inactive runtimes and quit stops all',async()=>{const f=fixture();f.controller.freeMemory=()=>1024;await f.controller.switch('or-05');assert.equal(f.initial.stops,1);assert.equal(f.current.stops,0);await f.controller.stopAll();assert.equal(f.current.stops,1);});
test('prospective authorization failure leaves current district usable',async()=>{const f=fixture();f.controller.createRuntime=()=>({...f.initial,async start(){throw new Error('revoked');},async stop(){}});await assert.rejects(f.controller.switch('or-05'),/revoked/);assert.equal(f.current,f.initial);assert.equal(f.initial.stops,0);assert.equal(f.configuration.district,'co-05');});
test('launch headers follow active runtime and never send a retained token to old origin',()=>{
 let handlers={},active={contentsId:7,origin:'http://127.0.0.1:1',token:'first'};attachLaunchHeader({webRequest:{onBeforeRequest:f=>handlers.before=f,onBeforeSendHeaders:f=>handlers.headers=f}},()=>active);
 const request=(url)=>{let reply;handlers.headers({url,webContentsId:7,requestHeaders:{'X-Desktop-Token':'forged'}},value=>reply=value);return reply.requestHeaders;};
 assert.equal(request(active.origin)['X-Desktop-Token'],'first');active={contentsId:7,origin:'http://127.0.0.1:2',token:'second'};assert.equal(request('http://127.0.0.1:1')['X-Desktop-Token'],undefined);assert.equal(request(active.origin)['X-Desktop-Token'],'second');
});
test('validated binding cache invalidates on fingerprint change, expiry and failed probes',async()=>{
 let key='one',time=0,calls=0,fail=false;const read=bindingCache(async()=>{calls++;if(fail)throw new Error('invalid');return calls;},()=>key,{now:()=>time,ttl:2000});
 assert.equal(await read(),1);assert.equal(await read(),1);key='two';assert.equal(await read(),2);time=2001;assert.equal(await read(),3);key='three';fail=true;await assert.rejects(read(),/invalid/);fail=false;assert.equal(await read(),5);
});
test('valid offline approval opens immediately while renewal is pending',async()=>{
 let refreshed=false;const approved={ok:true};const gate={authorize:async()=>approved,refresh:()=>new Promise(()=>{refreshed=true;})};assert.equal(await authorizeAvailable(gate),approved);assert.equal(refreshed,true);
});
test('expired approval renews online before retrying, with no fallback on denial',async()=>{
 let attempts=0,refreshes=0;const gate={authorize:async()=>{if(++attempts===1)throw new Error('expired');return 'valid';},refresh:async()=>{refreshes++;}};assert.equal(await authorizeAvailable(gate),'valid');assert.equal(refreshes,1);
 await assert.rejects(authorizeAvailable({authorize:async()=>{throw new Error('revoked');},refresh:async()=>{}}),/revoked/);
});
