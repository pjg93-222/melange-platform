'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const {GitHubDistrictController,registerDistrictDataIPC,ownerGitHubSource}=require('../github-district-controller.cjs');
const configuration={district:'co-05',terrierLegacy:{dataDirectory:'fixture'}};
test('the real normalized GitHub update source enables only Terrier in its fixed repository',()=>{
 const source=require('../github-source.cjs').configuration({provider:'github',repository:'pjg93-222/Geo'});
 assert.equal(ownerGitHubSource('terrier',source),true);assert.equal(ownerGitHubSource('terrier',{provider:'github',repository:'pjg93-222/Geo'}),true);
 for(const [id,value]of [['mentat',source],['melange',source],['terrier',null],['terrier',{...source,repository:'other/Geo'}],['terrier',{...source,provider:'publisher'}]])assert.equal(ownerGitHubSource(id,value),false);
});
function approval(){
 const grant={appId:'terrier',districtIds:['co-05'],grantId:'fixture-grant',serverId:'fixture-server',deviceId:'fixture-device',principalId:'fixture-owner',accountIdentity:'admin'};
 return {accepted:{grant,revoked:false},owner:{mode:'existing-owner',appId:'terrier',grantId:grant.grantId,accountIdentity:'admin',dataBindings:[{districtId:'co-05'}]},principal:{...grant,mode:'existing-owner',districtId:'co-05'}};
}
function fixture(overrides={}){
 const calls=[],states=[];let state=null,authorization=approval();
 const provider={info:()=>state,ensure:async config=>{calls.push('ensure');state={district:config.district,phase:'ready',cached:true};return {...config,ownerGitHubData:{fixture:true}};},discover:async(config,_approval,options)=>{calls.push(['discover',options]);state={district:config.district,phase:'update-ready',cached:true,updateAvailable:true};return state;}};
 const controller=new GitHubDistrictController({createProvider:onStatus=>{calls.push('create');provider.emit=onStatus;return provider;},authorize:async()=>{calls.push('authorize');return authorization;},keepAwake:()=>{calls.push('awake');return()=>calls.push('release');},onStatus:state=>states.push(state),...overrides});
 return {calls,states,provider,controller,revoke(){authorization={...authorization,accepted:{...authorization.accepted,revoked:true}};},recipient(){authorization={...authorization,owner:null};}};
}
test('startup returns only an ephemeral configuration and discovery only stages data',async()=>{
 const f=fixture(),original=structuredClone(configuration);
 assert.equal(f.controller.info(configuration,approval()),null);
 const effective=await f.controller.ensure(configuration);
 assert.deepEqual(configuration,original);assert.deepEqual(effective.ownerGitHubData,{fixture:true});
 await f.controller.discover(effective);
 assert.deepEqual(f.calls,['authorize','create','awake','ensure','release','authorize','awake',['discover',{stageOnly:true}],'release']);
 assert.equal(f.controller.info(configuration,approval()).phase,'update-ready');
 assert.equal(f.states.at(-1).canRefresh,true);
});
test('queued work obtains current approval after the earlier download and refuses learned revocation',async()=>{
 const f=fixture();let finish;f.provider.ensure=async config=>{await new Promise(resolve=>{finish=resolve;});return config;};
 const opening=f.controller.ensure(configuration);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.controller.busy,true);assert.equal(await f.controller.discover(configuration,{skipIfBusy:true}),null);
 const queued=f.controller.discover(configuration);f.revoke();finish();await opening;
 assert.equal(await queued,null);assert.equal(f.calls.filter(call=>Array.isArray(call)).length,0);
 assert.equal(f.calls.filter(call=>call==='authorize').length,2);assert.equal(f.controller.busy,false);
});
test('recipients and stale district discoveries never construct the public provider',async()=>{
 const f=fixture();f.recipient();assert.equal(await f.controller.ensure(configuration),configuration);assert.equal(await f.controller.discover(configuration),null);assert.deepEqual(f.calls,['authorize','authorize']);
 const stale=fixture({canDiscover:()=>false});assert.equal(await stale.controller.discover(configuration),null);assert.deepEqual(stale.calls,[]);
});
test('provider failure releases sleep protection and the queue permits a later verified retry',async()=>{
 const f=fixture();f.provider.ensure=async()=>{throw new Error('fixture verification failure');};
 await assert.rejects(f.controller.ensure(configuration),/verification/);assert.equal(f.controller.busy,false);assert.equal(f.calls.at(-1),'release');
 f.provider.ensure=async config=>config;assert.equal(await f.controller.ensure(configuration),configuration);
});
test('runtime IPC denies untrusted frames before reads or refresh and ignores renderer arguments',async()=>{
 const handlers=new Map(),trusted={},calls=[];
 registerDistrictDataIPC({handle:(name,callback)=>handlers.set(name,callback)},{runtimeOnly:event=>{if(event!==trusted)throw new Error('Untrusted application request');},info:()=>{calls.push('info');return {phase:'ready'};},refresh:()=>{calls.push('refresh');return {phase:'update-ready'};},canRefresh:()=>true});
 assert.throws(()=>handlers.get('desktop:district-data')({}),/Untrusted/);
 await assert.rejects(handlers.get('desktop:district-data-refresh')({}),/Untrusted/);assert.deepEqual(calls,[]);
 assert.deepEqual(handlers.get('desktop:district-data')(trusted,{url:'https://untrusted.invalid'}),{phase:'ready'});
 assert.deepEqual(await handlers.get('desktop:district-data-refresh')(trusted,{directory:'outside'}),{phase:'update-ready'});assert.deepEqual(calls,['info','refresh']);
 const blocked=new Map();registerDistrictDataIPC({handle:(name,cb)=>blocked.set(name,cb)},{runtimeOnly:()=>{},info:()=>null,refresh:()=>assert.fail('must not refresh during switch/close'),canRefresh:()=>false});
 await assert.rejects(blocked.get('desktop:district-data-refresh')(trusted),/unavailable/);
});
test('preload exposes a read, argument-free refresh and a removable data status subscription',async()=>{
 const calls=[],listeners=new Map();let exposed;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','preload.cjs'),'utf8'),{require:name=>{assert.equal(name,'electron');return {contextBridge:{exposeInMainWorld:(name,value)=>{if(name==='desktop')exposed=value;}},ipcRenderer:{sendSync:()=>({enabled:false}),invoke:(...args)=>{calls.push(args);return Promise.resolve(null);},on:(name,listener)=>listeners.set(name,listener),removeListener:(name,listener)=>{assert.equal(listeners.get(name),listener);listeners.delete(name);}}};}});
 await exposed.districtData({path:'ignored'});await exposed.refreshDistrictData({url:'ignored'});
 assert.deepEqual(calls,[['desktop:district-data'],['desktop:district-data-refresh']]);let received;
 const remove=exposed.onDistrictData(value=>{received=value;});const state={phase:'ready'};listeners.get('desktop:district-data-status')({},state);assert.equal(received,state);remove();assert.equal(listeners.size,0);
});
test('owner data failures stay on the loading boundary and cannot mark a binary update as failed',async()=>{
 const source=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8');
 const start=source.indexOf('async function prepareDistrictData(){'),end=source.indexOf('// Learned revocation:',start);assert.ok(start>0&&end>start);
 const notices=[],settings={...configuration},context={read:()=>settings,prepareOwnerDistrict:async()=>{throw new Error('private/path and private response');},closing:false,downloads:{signal:{aborted:false}},districtLog:text=>notices.push(text),loadingStatus:text=>notices.push(text)};
 vm.createContext(context);vm.runInContext(source.slice(start,end)+';globalThis.prepare=prepareDistrictData;',context);
 assert.equal(await context.prepare(),false);assert.equal(notices.length,2);assert.match(notices[1],/Existing files were preserved/);assert.doesNotMatch(notices.join(' '),/private\/path|private response/);
});
