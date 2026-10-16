'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {DiscoveryCache,rateLimit,deadline,TTL,JITTER,MAX_DELAY}=require('../github-discovery.cjs');
const {GitHubSource,discoveryMetadata}=require('../github-source.cjs');
const repo='owner/releases';
function directory(t){const value=fs.mkdtempSync(path.join(os.tmpdir(),'github-discovery-'));t.after(()=>fs.rmSync(value,{recursive:true,force:true}));return value;}
function release(version='1.0.0'){
 const assets=['melange','mentat','terrier'].flatMap(app=>[app+'-win32-x64.manifest.json',app+'-trust.json',app+'-'+version+'-win32-x64.exe']).map(name=>({name,size:10,browser_download_url:'https://github.com/'+repo+'/releases/download/desktop-v'+version+'/'+name}));
 return {draft:false,prerelease:false,tag_name:'desktop-v'+version,assets};
}
function options(t,extra={}){return {directory:directory(t),repository:repo,validate:values=>discoveryMetadata(values,repo),random:()=>0,...extra};}
test('three apps and restarted receivers share one bounded discovery refresh until six-hour jittered TTL',async t=>{
 const root=directory(t);let now=Date.now(),calls=0;
 const create=appId=>new GitHubSource({appId,repository:repo,cacheDirectory:root,cacheOptions:{now:()=>now,random:()=>0.5},readJSON:async()=>{calls++;return [release()];}});
 const sources=['melange','mentat','terrier'].map(create);
 for(const source of sources)assert.equal((await source.latest()).version,'1.0.0');assert.equal(calls,1);
 assert.match(sources[2].discoverySummary(),/cached release list checked.*Next discovery/);
 now+=TTL;assert.equal((await create('melange').latest()).version,'1.0.0');assert.equal(calls,1);
 now+=JITTER/2+1;await create('mentat').latest();assert.equal(calls,2);
 const independent=new GitHubSource({appId:'melange',repository:'owner/other',cacheDirectory:root,readJSON:async()=>{calls++;return [];}});await independent.latest();assert.equal(calls,3);
});
test('empty discovery is cached across apps/restarts and trust plus update never doubles the scan',async t=>{
 const root=directory(t);let calls=0;
 const create=appId=>new GitHubSource({appId,repository:repo,cacheDirectory:root,readJSON:async()=>{calls++;return [];}});
 const a=create('melange'),enrollment={source:'github',repository:repo,appId:'melange'};
 assert.deepEqual(await a.request(enrollment,'/v1/apps/melange/trust'),{transitions:[]});
 assert.equal(await a.request(enrollment,'/v1/apps/melange/updates?platform=win32&arch=x64&version=1.0.0'),null);
 assert.equal(await create('terrier').latest(),null);assert.equal(await create('melange').latest(),null);assert.equal(calls,1);
});
test('quota cooldown persists across apps/restart and blocks list and exact-tag APIs until reset',async t=>{
 const root=directory(t);let now=Date.now(),calls=0,limited=false;
 const create=appId=>new GitHubSource({appId,repository:repo,cacheDirectory:root,cacheOptions:{now:()=>now,random:()=>0},readJSON:async()=>{calls++;if(limited)throw rateLimit(429,{'retry-after':'3600'});return [release()];}});
 await create('melange').latest();now+=TTL+1;limited=true;
 assert.equal((await create('mentat').latest()).version,'1.0.0');assert.equal(calls,2);
 const restarted=create('terrier');assert.equal((await restarted.latest()).version,'1.0.0');assert.equal(calls,2);assert.match(restarted.discoverySummary(),/API retry after/);
 await assert.rejects(restarted.request({source:'github',repository:repo,appId:'terrier'},'/v1/apps/terrier/recovery?platform=win32&arch=x64&version=1.0.0'),/paused until/);assert.equal(calls,2);
 now+=3600001;limited=false;await create('melange').latest();assert.equal(calls,3);
});
test('rate-limit classification distinguishes plain403 and parses bounded reset/date/fallback deadlines',()=>{
 const now=Date.now();assert.equal(rateLimit(403,{},'Repository access denied'),null);
 assert.equal(rateLimit(404),null);assert.equal(rateLimit(403,{},'You have exceeded a secondary rate limit').githubRateLimit,true);
 assert.equal(deadline(rateLimit(429,{'retry-after':'120'}),now,()=>0),now+120000);
 const date=new Date(Math.ceil(now/1000)*1000+120000).toUTCString();assert.equal(deadline(rateLimit(429,{'retry-after':date}),now,()=>0),Date.parse(date));
 const reset=Math.ceil(now/1000)+3600;assert.equal(deadline(rateLimit(403,{'x-ratelimit-remaining':'0','x-ratelimit-reset':String(reset)}),now,()=>0),reset*1000);
 assert.equal(deadline(rateLimit(429),now,()=>0),now+3600000);
 assert.equal(deadline(rateLimit(429,{'retry-after':'999999999999999999'}),now,()=>0),now+MAX_DELAY);
});
test('concurrent app refreshes serialize, wait is bounded, failed refresh releases the writer lock',async t=>{
 const config=options(t,{waitMs:100}),first=new DiscoveryCache(config),second=new DiscoveryCache(config);let unblock,entered,calls=0;
 const started=new Promise(resolve=>{entered=resolve;});const pending=first.get(async()=>{calls++;entered();await new Promise(resolve=>{unblock=resolve;});return [];});await started;
 await assert.rejects(second.get(async()=>{calls++;return [];}),/already refreshing/);assert.equal(calls,1);unblock();await pending;
 assert.deepEqual(await second.get(async()=>{calls++;return [];}),[]);assert.equal(calls,1);
 const failed=new DiscoveryCache(options(t));await assert.rejects(failed.get(async()=>{throw new Error('fixture fetch failed');}),/fixture/);assert.deepEqual(await failed.get(async()=>[]),[]);
});
test('SQLite releases a crashed process refresh lock without deleting another app state',async t=>{
 const config=options(t),cache=new DiscoveryCache(config);const modulePath=path.resolve(__dirname,'../github-discovery.cjs');
 const child=spawnSync(process.execPath,['-e',`const {DiscoveryCache}=require(${JSON.stringify(modulePath)});const c=new DiscoveryCache({directory:${JSON.stringify(config.directory)},repository:${JSON.stringify(repo)},validate:x=>x});const d=c.cacheDB();d.exec('BEGIN IMMEDIATE');process.exit(9);`],{timeout:30000,windowsHide:true,encoding:'utf8'});
 assert.equal(child.status,9,child.stderr);assert.deepEqual(await cache.get(async()=>[]),[]);
});
test('cached metadata and future clock bounds are revalidated before reuse',async t=>{
 let now=Date.now();const config=options(t,{now:()=>now}),cache=new DiscoveryCache(config);await cache.get(async()=>[]);
 let db=cache.cacheDB();db.prepare('UPDATE discovery SET checked=?,next=? WHERE id=1').run(now+86400000,now+86400000+TTL);db.close();let calls=0;await cache.get(async()=>{calls++;return [];});assert.equal(calls,1);
 const item={version:'1.0.0',assets:release().assets};item.assets[0].browser_download_url='https://evil.example/manifest';
 db=cache.cacheDB();db.prepare('UPDATE discovery SET payload=? WHERE id=1').run(JSON.stringify([item]));db.close();await cache.get(async()=>{calls++;return [];});assert.equal(calls,2);
 assert.throws(()=>discoveryMetadata([{version:'1.0.0',assets:[...release().assets,...release().assets]}],repo),/cached/);
 await cache.note(rateLimit(429));now-=86400000;assert.equal(cache.cooldown(),now+3600000);now+=3600001;assert.equal(cache.cooldown(),0);
});
