'use strict';
// GitHub hosts public artifacts; the independently pinned manifest signature and
// native publisher identity remain the authorities for installing them.
const https=require('node:https'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {pipeline}=require('node:stream/promises');const {Transform}=require('node:stream');const semver=require('semver');
const {verifyPackageFile}=require('./trust.cjs');
const PURPOSE='melange-desktop-github-release',DISTRIBUTION='public-installations';
const ASSET_HOSTS=new Set(['github.com','release-assets.githubusercontent.com','objects.githubusercontent.com']);
function repository(value){if(typeof value!=='string'||! /^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value)||value.split('/').some(part=>part.length>100))throw new Error('Invalid GitHub repository');return value.toLowerCase();}
function configuration(value){if(!value||Object.keys(value).sort().join('|')!=='provider|repository'||value.provider!=='github')throw new Error('Invalid GitHub update configuration');return {provider:'github',repository:repository(value.repository)};}
function tag(version){if(typeof version!=='string'||!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)||semver.valid(version)!==version)throw new Error('Stable desktop version required');return 'desktop-v'+version;}
function checkedURL(value,asset=false){const u=new URL(value);if(u.protocol!=='https:'||u.port||u.username||u.password||u.hash||!(asset?ASSET_HOSTS.has(u.hostname):u.hostname==='api.github.com'))throw new Error('GitHub update URL refused');return u;}
function response(url,{asset=false,redirects=0,deadlineMs=15000}={}){
 const target=checkedURL(url,asset);
 return new Promise((resolve,reject)=>{
  const request=https.request(target,{method:'GET',rejectUnauthorized:true,headers:{Accept:asset?'application/octet-stream':'application/vnd.github+json','User-Agent':'MelangeDesktopUpdater/1','X-GitHub-Api-Version':'2022-11-28'}},res=>{
   if(res.statusCode>=300&&res.statusCode<400){res.resume();clearTimeout(deadline);if(!asset||redirects>=3||typeof res.headers.location!=='string'){reject(new Error('GitHub redirect refused'));return;}
    let next;try{next=checkedURL(new URL(res.headers.location,target).href,true);}catch(error){reject(error);return;}
    response(next.href,{asset:true,redirects:redirects+1,deadlineMs}).then(resolve,reject);return;
   }
   if(res.statusCode!==200){res.resume();clearTimeout(deadline);reject(new Error('GitHub release unavailable ('+res.statusCode+')'));return;}
   res.once('end',()=>clearTimeout(deadline));res.once('close',()=>clearTimeout(deadline));res.once('error',()=>clearTimeout(deadline));resolve(res);
  });
  const deadline=setTimeout(()=>request.destroy(new Error('GitHub request deadline exceeded')),deadlineMs);
  request.setTimeout(15000,()=>request.destroy(new Error('GitHub request timed out')));
  request.once('error',error=>{clearTimeout(deadline);reject(error);});request.end();
 });
}
async function json(url,{asset=false,maxBytes=2*1024*1024}={}){const stream=await response(url,{asset});const chunks=[];let size=0;for await(const chunk of stream){size+=chunk.length;if(size>maxBytes){stream.destroy();throw new Error('GitHub metadata exceeds limit');}chunks.push(chunk);}return JSON.parse(Buffer.concat(chunks).toString('utf8'));}
function assetURL(repo,release,name){
 const expected='https://github.com/'+repository(repo)+'/releases/download/'+tag(release.version)+'/'+encodeURIComponent(name);
 const found=release.assets.filter(item=>item&&item.name===name);
 // GitHub may return canonical owner/repository capitalization. Only these two
 // path segments are case insensitive; release tag and asset name remain exact.
 const actual=found[0]?.browser_download_url;
 const normalized=typeof actual==='string'?actual.replace(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\//,(_prefix,owner,name)=>'https://github.com/'+owner.toLowerCase()+'/'+name.toLowerCase()+'/'):null;
 if(found.length!==1||normalized!==expected||!Number.isSafeInteger(found[0].size)||found[0].size<1)throw new Error('Required GitHub release asset is missing or ambiguous');
 return expected;
}
function release(value){if(!value||value.draft!==false||value.prerelease!==false||typeof value.tag_name!=='string'||!value.tag_name.startsWith('desktop-v')||!Array.isArray(value.assets)||value.assets.length>100)throw new Error('Unsupported GitHub release');const version=value.tag_name.slice(9);tag(version);return {version,assets:value.assets};}
function githubPolicy(manifest,repo){
 if(manifest.purpose!==PURPOSE||manifest.distribution!==DISTRIBUTION||manifest.repository!==repository(repo)||manifest.platform!=='win32'||manifest.arch!=='x64'||manifest.package?.name!==manifest.appId+'-'+manifest.version+'-win32-x64.exe')throw new Error('Signed GitHub release policy does not match this installation');
 const sources=manifest.sourceRefs;
 if(!sources||Array.isArray(sources)||Object.keys(sources).sort().join('|')!=='melange|mentat|terrier')throw new Error('Signed GitHub source provenance is missing');
 for(const source of Object.values(sources)){
  if(!source||Array.isArray(source)||Object.keys(source).sort().join('|')!=='commit|ref|repository'||typeof source.ref!=='string'||source.ref.length<1||source.ref.length>512||/[\x00-\x1f\x7f]/.test(source.ref)||typeof source.commit!=='string'||!/^[0-9a-f]{40}$/.test(source.commit)||/^0{40}$/.test(source.commit))throw new Error('Invalid signed GitHub source provenance');
  repository(source.repository);
 }
 tag(manifest.version);return manifest;
}
class GitHubSource{
 constructor({repository:repo,appId,platform='win32',arch='x64',readJSON=json,open=response}){if(!['melange','mentat','terrier'].includes(appId)||platform!=='win32'||arch!=='x64')throw new Error('Unsupported GitHub desktop target');this.repository=repository(repo);this.appId=appId;this.platform=platform;this.arch=arch;this.readJSON=readJSON;this.open=open;}
 identity(enrollment){if(enrollment?.source!=='github'||enrollment.repository!==this.repository||enrollment.appId!==this.appId||enrollment.deviceToken)throw new Error('GitHub update source identity mismatch');}
 api(suffix){return 'https://api.github.com/repos/'+this.repository+'/releases'+suffix;}
 async latest(){const candidates=[];for(let page=1;page<=5;page++){const values=await this.readJSON(this.api('?per_page=100&page='+page));if(!Array.isArray(values)||values.length>100)throw new Error('Invalid GitHub releases');for(const value of values){try{const item=release(value);assetURL(this.repository,item,this.appId+'-'+this.platform+'-'+this.arch+'.manifest.json');candidates.push(item);}catch{}}if(values.length<100)break;}return candidates.sort((a,b)=>semver.rcompare(a.version,b.version))[0]||null;}
 async manifest(item){const url=assetURL(this.repository,item,this.appId+'-'+this.platform+'-'+this.arch+'.manifest.json');const envelope=await this.readJSON(url,{asset:true,maxBytes:1024*1024});if(!envelope||typeof envelope.payload!=='string'||envelope.payload.length>2*1024*1024)throw new Error('Invalid GitHub manifest');const body=githubPolicy(JSON.parse(Buffer.from(envelope.payload,'base64').toString('utf8')),this.repository);if(body.version!==item.version||body.appId!==this.appId||body.platform!==this.platform||body.arch!==this.arch)throw new Error('GitHub release target differs from signed manifest');assetURL(this.repository,item,body.package.name);const metadata=item.assets.find(asset=>asset.name===body.package.name);if(metadata.size!==body.package.size)throw new Error('GitHub package size differs from signed manifest');return envelope;}
 async request(enrollment,relative){
  this.identity(enrollment);const base='/v1/apps/'+this.appId;
  if(relative==='/v1/status')return {ok:true,localOnly:true};
  if(relative===base+'/trust'){this.current=await this.latest();if(!this.current)return {transitions:[]};const result=await this.readJSON(assetURL(this.repository,this.current,this.appId+'-trust.json'),{asset:true,maxBytes:1024*1024});if(!result||Object.keys(result).join('|')!=='transitions'||!Array.isArray(result.transitions)||result.transitions.length>32)throw new Error('Invalid GitHub key transitions');return result;}
  const parsed=new URL(relative,'https://local.invalid');
  if(parsed.pathname!==base+'/updates'&&parsed.pathname!==base+'/recovery')throw new Error('Unsupported GitHub update operation');
  const query=parsed.searchParams;if(query.get('platform')!==this.platform||query.get('arch')!==this.arch)throw new Error('GitHub release target mismatch');
  if(parsed.pathname.endsWith('/recovery')){const expected=tag(query.get('version'));const item=release(await this.readJSON(this.api('/tags/'+expected)));if(tag(item.version)!==expected)throw new Error('Recovery release tag mismatch');return this.manifest(item);}
  const item=this.current||await this.latest();if(!item||semver.lte(item.version,query.get('version')))return null;return this.manifest(item);
 }
 async download(enrollment,_token,manifest,directory){
  this.identity(enrollment);githubPolicy(manifest,this.repository);
  if(manifest.appId!==this.appId)throw new Error('GitHub package belongs to another app');
  const item=release(await this.readJSON(this.api('/tags/'+tag(manifest.version))));if(item.version!==manifest.version)throw new Error('GitHub package release differs');
  const url=assetURL(this.repository,item,manifest.package.name);if(item.assets.find(asset=>asset.name===manifest.package.name).size!==manifest.package.size)throw new Error('GitHub package size differs');
  fs.mkdirSync(directory,{recursive:true,mode:0o700});const destination=path.join(directory,manifest.package.name),temporary=destination+'.'+crypto.randomUUID()+'.part';let count=0;
  try{const stream=await this.open(url,{asset:true,deadlineMs:30*60*1000});const bounded=new Transform({transform(chunk,_encoding,done){count+=chunk.length;done(count>manifest.package.size?new Error('GitHub package exceeds signed size'):null,chunk);}});await pipeline(stream,bounded,fs.createWriteStream(temporary,{flags:'wx',mode:0o600}));await verifyPackageFile(temporary,manifest);fs.renameSync(temporary,destination);return destination;}finally{try{fs.unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
 }
}
async function enrollGitHub({appId,publicKeys,config,load,save}){
 const selected=configuration(config);
 if(!['melange','mentat','terrier'].includes(appId)||!publicKeys||Array.isArray(publicKeys)||Object.keys(publicKeys).length<1||Object.keys(publicKeys).length>8)throw new Error('Packaged GitHub verification keys are required');
 for(const [id,key] of Object.entries(publicKeys))if(!/^[A-Za-z0-9._-]{1,100}$/.test(id)||typeof key!=='string'||Buffer.from(key,'base64').length!==32||Buffer.from(key,'base64').toString('base64')!==key)throw new Error('Invalid packaged GitHub verification key');
 const previous=await load();if(previous){if(previous.source!=='github'||previous.repository!==selected.repository||previous.appId!==appId||previous.deviceToken||Object.entries(publicKeys).some(([id,key])=>previous.publicKeys?.[id]!==key))throw new Error('Protected GitHub source differs from the packaged verification identity');return previous;}
 // Public binary updates are independent of app login/approval. No access token,
 // account state or device identifier is needed or sent to GitHub.
 const value={source:'github',repository:selected.repository,appId,publicKeys:structuredClone(publicKeys)};await save(value);return value;
}
module.exports={GitHubSource,configuration,repository,tag,checkedURL,githubPolicy,enrollGitHub,response,json,assetURL,release};
