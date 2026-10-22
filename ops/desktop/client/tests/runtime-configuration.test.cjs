'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {EventEmitter}=require('node:events'),{createRequire}=require('node:module');

// Exercise the real launcher and root validation with temporary synthetic files. Process creation and health transport
// are isolated doubles: these tests never spawn a backend, taskkill, or touch an installed application/profile.
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-effective-config-')),user=path.join(root,'profile'),resources=path.join(root,'resources'),data=path.join(root,'owner-state'),cacheRoot=path.join(user,'packs'),sha='a'.repeat(64);
 const cache=path.join(cacheRoot,'github-districts','datasets',sha,'co-05');fs.mkdirSync(cache,{recursive:true});fs.mkdirSync(data);fs.mkdirSync(resources);
 fs.writeFileSync(path.join(data,'accounts.sqlite3'),'synthetic account bytes');fs.writeFileSync(path.join(data,'.secret'),'synthetic secret bytes');
 const configuration={dataDirectory:data,district:'co-05',terrierLegacy:{schemaVersion:1,district:'co-05',firstPasswordOwner:'admin',dataDirectory:data}};
 const effective={...configuration,ownerGitHubData:{schemaVersion:1,district:'co-05',cacheRoot,catalogueSha256:sha}},spawns=[],requests=[],runtimes=[];
 let child,authorized=true;
 const runtimeFile=path.join(__dirname,'..','runtime.cjs'),nativeRequire=createRequire(runtimeFile),module={exports:{}};
 const dependencies={
  'node:net':{createServer(){const server=new EventEmitter();server.listen=(_port,_host,ready)=>queueMicrotask(ready);server.address=()=>({port:43210});server.close=ready=>ready();return server;}},
  'node:http':{get(_url,_options,ready){const request=new EventEmitter();request.destroy=()=>request.emit('error',new Error('synthetic transport closed'));
    requests.push(()=>{const response=new EventEmitter();response.statusCode=200;response.destroy=()=>{};ready(response);response.emit('data',JSON.stringify({appId:'terrier',launchPath:'/'}));response.emit('end');});return request;}},
  'node:child_process':{spawn(binary,args,options){const process=new EventEmitter();process.exitCode=null;process.pid=987654;process.kill=()=>{};
    if(binary==='taskkill.exe'){queueMicrotask(()=>{child.exitCode=0;child.emit('exit',0);process.emit('exit',0);});return process;}
    child=process;spawns.push({binary,args:Array.from(args),options});return process;}},
  './backend-command.cjs':{backendCommand(){return {binary:path.join(resources,'python','python.exe'),runtime:resources,args:['synthetic-bootstrap']};},backendEnvironment:()=>({})},
 };
 vm.runInNewContext(fs.readFileSync(runtimeFile,'utf8'),{module,require:name=>Object.hasOwn(dependencies,name)?dependencies[name]:nativeRequire(name),structuredClone,process:{platform:'win32'},URL,setTimeout,clearTimeout},{filename:runtimeFile});
 const make=()=>{const runtime=new module.exports.Runtime('terrier',user,resources,{authorize:async()=>({accepted:{grant:{},revoked:!authorized},owner:true,principal:{districtId:'co-05'}})});runtimes.push(runtime);return runtime;};
 t.after(async()=>{for(const runtime of runtimes)await runtime.stop();fs.rmSync(root,{recursive:true,force:true});});
 return {configuration,effective,cache,data,spawns,requests,make,set authorized(value){authorized=value;},async pending(){for(let n=0;n<20&&!requests.length;n++)await new Promise(setImmediate);assert.equal(requests.length,1);},ready(){requests.shift()();},exit(){child.exitCode=1;child.emit('exit',1);}};
}

test('validated GitHub cache launches separately from unchanged owner state and is remembered only after backend health',async t=>{
 const f=fixture(t),runtime=f.make(),effective=structuredClone(f.effective),opening=runtime.start(effective);await f.pending();
 assert.equal(runtime.configuration,null);assert.equal(f.spawns.length,1);
 const args=f.spawns[0].args,value=name=>args[args.indexOf(name)+1];
 assert.equal(value('--data-root'),f.cache);assert.equal(value('--state-root'),f.data);assert.equal(value('--legacy-owner'),'admin');assert.equal(args.filter(arg=>arg==='--github-district-data').length,1);
 f.ready();await opening;assert.deepEqual(runtime.configuration,f.effective);
 effective.ownerGitHubData.catalogueSha256='b'.repeat(64);effective.dataDirectory='changed by caller';assert.deepEqual(runtime.configuration,f.effective,'later caller mutation cannot change the running cache fingerprint');
 await runtime.stop();assert.equal(runtime.configuration,null);assert.equal(runtime.roots,null);
 assert.equal(fs.readFileSync(path.join(f.data,'accounts.sqlite3'),'utf8'),'synthetic account bytes');assert.equal(fs.readFileSync(path.join(f.data,'.secret'),'utf8'),'synthetic secret bytes');
});
test('an arbitrary input flag does not enable GitHub mode for ordinary validated owner roots',async t=>{
 const f=fixture(t),runtime=f.make(),opening=runtime.start({...f.configuration,githubDistrictData:true});await f.pending();f.ready();await opening;
 assert.equal(f.spawns[0].args.includes('--github-district-data'),false);assert.equal(runtime.roots.data,f.data);assert.equal(runtime.roots.state,f.data);
 f.exit();assert.equal(runtime.configuration,null,'an exited backend cannot remain a warm effective configuration');
});
test('denied authorization and cancellation never retain an effective configuration',async t=>{
 const f=fixture(t),runtime=f.make();f.authorized=false;await assert.rejects(runtime.start(f.effective),/approval/);assert.equal(runtime.configuration,null);assert.equal(f.spawns.length,0);
 f.authorized=true;const opening=runtime.start(f.effective);await f.pending();await runtime.stop();f.ready();await assert.rejects(opening,/cancelled/);assert.equal(runtime.configuration,null);assert.equal(runtime.child,null);
});
