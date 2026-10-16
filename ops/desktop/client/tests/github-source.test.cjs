'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {Readable}=require('node:stream');const {EventEmitter}=require('node:events');
const {GitHubSource,configuration,checkedURL,githubPolicy,enrollGitHub,response,json}=require('../github-source.cjs');
const {verifyEnvelope}=require('../trust.cjs');const {DesktopUpdater}=require('../updater.cjs');
const repo='owner/desktop-updates';
function fixture(){
 const keys=crypto.generateKeyPairSync('ed25519'),publicKeys={root:keys.publicKey.export({format:'der',type:'spki'}).subarray(-32).toString('base64')};
 const enrollment={source:'github',repository:repo,appId:'melange',publicKeys};
 const sourceRefs=Object.fromEntries(['melange','mentat','terrier'].map(app=>[app,{repository:'Owner/'+app,ref:'main',commit:'a'.repeat(40)}]));
 const packages=new Map(),manifests=new Map(),releases=new Map();
 function add(version,sequence){
  const name='melange-'+version+'-win32-x64.exe',bytes=Buffer.from('Inert native update fixture '+version);packages.set(name,bytes);
  const manifest={schemaVersion:1,purpose:'melange-desktop-github-release',distribution:'public-installations',repository:repo,sourceRefs,appId:'melange',version,sequence,platform:'win32',arch:'x64',minimumUpdaterVersion:'1.0.0',dataSchemaVersion:1,issuedAt:new Date().toISOString(),package:{name,path:'/v1/packages/melange/'+sequence+'/'+name,size:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),sha512:crypto.createHash('sha512').update(bytes).digest('base64')}};
  const payload=Buffer.from(JSON.stringify(manifest));const envelope={schemaVersion:1,keyId:'root',payload:payload.toString('base64'),signature:crypto.sign(null,payload,keys.privateKey).toString('base64')};manifests.set(version,envelope);
  const asset=(name,size)=>({name,size,browser_download_url:'https://github.com/'+repo+'/releases/download/desktop-v'+version+'/'+name});
  releases.set(version,{draft:false,prerelease:false,tag_name:'desktop-v'+version,assets:[asset(name,bytes.length),asset('melange-win32-x64.manifest.json',JSON.stringify(envelope).length),asset('melange-trust.json',18)]});return manifest;
 }
 const seed=add('1.0.0',1),next=add('1.0.1',2),calls=[];let corrupt=false;
 const source=new GitHubSource({repository:repo,appId:'melange',readJSON:async url=>{calls.push(url);if(url.includes('?per_page=100&page='))return [...releases.values()].reverse();if(url.includes('/tags/'))return releases.get(url.split('desktop-v')[1]);if(url.endsWith('/melange-trust.json'))return {transitions:[]};return manifests.get(url.split('/desktop-v')[1].split('/')[0]);},open:async url=>Readable.from(corrupt?Buffer.from('bad fixture'):packages.get(url.split('/').at(-1)))});
 return {source,enrollment,seed,next,manifests,releases,packages,calls,set corrupt(value){corrupt=value;}};
}
test('GitHub source selects stable app assets, exact current recovery and rejects repository/audience/signature/replay changes',async()=>{
 const f=fixture(),identity={appId:'melange',platform:'win32',arch:'x64',version:'1.0.0'};
 assert.deepEqual(await f.source.request(f.enrollment,'/v1/apps/melange/trust'),{transitions:[]});
 const next=await f.source.request(f.enrollment,'/v1/apps/melange/updates?platform=win32&arch=x64&version=1.0.0');
 assert.equal(verifyEnvelope(next,f.enrollment,identity).manifest.version,'1.0.1');
 assert.equal(verifyEnvelope(await f.source.request(f.enrollment,'/v1/apps/melange/recovery?platform=win32&arch=x64&version=1.0.0'),f.enrollment,identity).manifest.version,'1.0.0');
 assert.throws(()=>verifyEnvelope({...next,signature:Buffer.alloc(64).toString('base64')},f.enrollment,identity),/signature/);
 assert.throws(()=>verifyEnvelope(next,f.enrollment,identity,{sequence:3}),/Rollback/);
 for(const change of [{repository:'attacker/repo'},{distribution:'everyone-without-approval'},{purpose:'other'},{platform:'darwin'},{arch:'arm64'}])assert.throws(()=>githubPolicy({...f.next,...change},repo));
 assert.throws(()=>verifyEnvelope(next,{...f.enrollment,repository:'owner/other'},identity),/policy/);
 await assert.rejects(f.source.request({...f.enrollment,deviceToken:'never-send'},'/v1/apps/melange/trust'),/identity/);
 f.releases.get('1.0.1').assets[0].browser_download_url='https://evil.example/package.exe';await assert.rejects(f.source.manifest({version:'1.0.1',assets:f.releases.get('1.0.1').assets}),/asset/);
});

test('repository capitalization is normalized only for GitHub owner/repo and signed source provenance is required',async()=>{
 const f=fixture(),item={version:'1.0.1',assets:f.releases.get('1.0.1').assets};
 for(const asset of item.assets)asset.browser_download_url=asset.browser_download_url.replace('/owner/desktop-updates/','/Owner/Desktop-Updates/');
 assert.equal((await f.source.manifest(item)).schemaVersion,1);
 item.assets[0].browser_download_url=item.assets[0].browser_download_url.replace('/desktop-v1.0.1/','/Desktop-v1.0.1/');
 await assert.rejects(f.source.manifest(item),/asset/);
 for(const sourceRefs of [undefined,{}, {...f.next.sourceRefs,unexpected:{}}, {...f.next.sourceRefs,terrier:{...f.next.sourceRefs.terrier,commit:'0'.repeat(40)}}, {...f.next.sourceRefs,terrier:{...f.next.sourceRefs.terrier,ref:'main\nsecret'}}])assert.throws(()=>githubPolicy({...f.next,sourceRefs},repo),/provenance/);
 assert.equal(configuration({provider:'github',repository:'Owner/Desktop-Updates'}).repository,repo);
});

test('desktop discovery scans bounded pages independently of newer container tags and excludes drafts/prereleases',async()=>{
 const f=fixture(),calls=[],container={draft:false,prerelease:false,tag_name:'v2026.10.13.3',assets:[]};
 const source=new GitHubSource({repository:repo,appId:'melange',readJSON:async url=>{const page=Number(new URL(url).searchParams.get('page'));calls.push(page);return page===1?Array(100).fill(container):[f.releases.get('1.0.0'),{...f.releases.get('1.0.1'),draft:true},{...f.releases.get('1.0.1'),prerelease:true},f.releases.get('1.0.1')];}});
 assert.equal((await source.latest()).version,'1.0.1');assert.deepEqual(calls,[1,2]);
 calls.length=0;source.readJSON=async url=>{calls.push(url);return Array(100).fill(container);};assert.equal(await source.latest(),null);assert.equal(calls.length,5);
});
test('public receiver does not require app access or send device tokens and preserves verified key transitions',async()=>{
 const publicKeys={root:crypto.randomBytes(32).toString('base64')};let saved=null;
 const options={appId:'melange',publicKeys,config:{provider:'github',repository:repo},load:()=>saved,save:value=>{saved=value;}};
 const first=await enrollGitHub(options);assert.equal(first.deviceToken,undefined);assert.equal(first.deviceId,undefined);assert.equal(first.repository,repo);
 saved={...saved,keySequence:4,publicKeys:{...saved.publicKeys,verified:crypto.randomBytes(32).toString('base64')}};
 assert.equal((await enrollGitHub(options)).keySequence,4);
 await assert.rejects(enrollGitHub({...options,publicKeys:{root:crypto.randomBytes(32).toString('base64')}}),/verification identity/);
 await assert.rejects(enrollGitHub({...options,publicKeys:null}),/verification keys/);
 assert.throws(()=>configuration({provider:'github',repository:'https://evil.example/repo',token:'secret'}));
});
test('GitHub source reuses signed baseline, hash checks and native activation without unlocking application access',async t=>{
 const f=fixture(),directory=fs.mkdtempSync(path.join(os.tmpdir(),'github-updater-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 let state={},installs=0,currentManifest,currentFile;const native={configOnDisk:{value:Promise.resolve({publisherName:'Fixture Publisher'})},setFeedURL(){},async checkForUpdates(){return {updateInfo:{version:currentManifest.version,files:[currentManifest.package]}};},async downloadUpdate(){return [currentFile];},quitAndInstall(){installs++;}};
 const updater=new DesktopUpdater({appId:'melange',version:'1.0.0',platform:'win32',arch:'x64',userData:directory,packaged:true,publisherName:'Fixture Publisher',loadEnrollment:()=>f.enrollment,saveEnrollment:value=>Object.assign(f.enrollment,value),readState:()=>state,writeState:value=>{state=value;},request:f.source.request.bind(f.source),download:f.source.download.bind(f.source),autoUpdater:native,onStatus(){},verifyNativePackage:async()=>{},makeFeed:async(manifest,file)=>{currentManifest=manifest;currentFile=file;return {url:'http://127.0.0.1/fixture',close(){}};}});t.after(()=>updater.close());
 await updater.check();assert.equal(state.installed.version,'1.0.0');assert.equal(state.pending.version,'1.0.1');assert.equal(state.pending.phase,'prepared');assert.equal(installs,0);
 assert.equal(await updater.activateAfterClose(),true);assert.equal(installs,1);
 f.corrupt=true;await assert.rejects(f.source.download(f.enrollment,null,f.next,path.join(directory,'bad')),/integrity|size/);assert.deepEqual(fs.readdirSync(path.join(directory,'bad')),[]);
});
test('GitHub network redirects stay on HTTPS asset hosts, never forward credentials, and metadata is bounded',async t=>{
 const https=require('node:https'),original=https.request,requests=[];let mode='cdn',hops=0;
 https.request=(url,options,callback)=>{requests.push({url:url.href,options});const request=new EventEmitter();request.setTimeout=()=>{};request.end=()=>setImmediate(()=>{const stream=Readable.from(mode==='large'?Buffer.alloc(100):Buffer.from(mode==='secondary'?'You have exceeded a secondary rate limit':'{}'));stream.statusCode=mode==='quota'?429:['secondary','forbidden'].includes(mode)?403:(mode==='cdn'&&hops++===0)||mode==='bad'||mode==='loop'||mode==='api'?302:200;stream.headers=mode==='quota'?{'retry-after':'3600'}:stream.statusCode===302?{location:mode==='bad'?'https://evil.example/steal':mode==='api'?'https://api.github.com/other':'https://release-assets.githubusercontent.com/fixture?signature=opaque'}:{};callback(stream);});request.destroy=error=>request.emit('error',error);return request;};t.after(()=>{https.request=original;});
 assert.deepEqual(await json('https://github.com/'+repo+'/releases/download/desktop-v1.0.0/fixture.json',{asset:true}),{});
 assert.equal(requests.length,2);for(const {options} of requests){assert.equal(options.rejectUnauthorized,true);assert.equal(options.headers.Authorization,undefined);assert.equal(options.headers.Cookie,undefined);}
 mode='bad';await assert.rejects(response('https://github.com/'+repo+'/asset',{asset:true}),/refused/);
 mode='api';await assert.rejects(response('https://api.github.com/repos/'+repo+'/releases'),/redirect/);
 mode='loop';await assert.rejects(response('https://github.com/'+repo+'/asset',{asset:true}),/redirect/);
 mode='large';await assert.rejects(json('https://api.github.com/repos/'+repo+'/releases',{maxBytes:10}),/limit/);
 mode='quota';await assert.rejects(response('https://api.github.com/repos/'+repo+'/releases'),error=>error.githubRateLimit===true&&error.retryAfter==='3600');
 mode='secondary';await assert.rejects(response('https://api.github.com/repos/'+repo+'/releases'),error=>error.githubRateLimit===true);
 mode='forbidden';await assert.rejects(response('https://api.github.com/repos/'+repo+'/releases'),error=>!error.githubRateLimit&&/403/.test(error.message));
 for(const url of ['http://github.com/asset','https://github.com.evil.test/asset','https://user:pass@github.com/asset','https://github.com:444/asset'])assert.throws(()=>checkedURL(url,true));
});
