'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {appDefinition}=require('./apps.cjs');
const DISTRICTS=Object.freeze(['co-05','or-05','ct-01']);

function physicalPath(value){
  let current=path.resolve(value),suffix=[];
  while(!fs.existsSync(current)){const parent=path.dirname(current);if(parent===current)throw new Error('Unable to resolve storage path.');suffix.unshift(path.basename(current));current=parent;}
  const resolved=path.join(fs.realpathSync.native(current),...suffix);
  return process.platform==='win32'?resolved.toLowerCase():resolved;
}
function contains(parent,child){const relative=path.relative(parent,child);return relative===''||(!relative.startsWith('..'+path.sep)&&relative!=='..'&&!path.isAbsolute(relative));}
function refuseInstalledOverlap(dataDirectory,protectedPaths){
  const data=physicalPath(dataDirectory);
  for(const protectedPath of protectedPaths){const installed=physicalPath(protectedPath);if(contains(data,installed)||contains(installed,data))throw new Error('Choose a data directory separate from installed application files.');}
}

// <store>\stores is the app-managed private district store (district-packs.cjs; <store> is ...\MelangeData\terrier, or <profile>\packs when isolated).
// Its district state lives beside it in <store>\state, so accounts, imported snapshots and secrets never reach the synced Documents profile.
// Any other data directory, the legacy owner's and every other app keep the profile's state folder.
function managedStore(data){
  const name=value=>process.platform==='win32'?path.basename(value).toLowerCase():path.basename(value),parent=path.dirname(data);
  return name(data)==='stores'&&['terrier','packs'].includes(name(parent))?parent:null;
}

function runtimeRoots(appId,userData,configuration){
  appDefinition(appId);
  if(typeof configuration.dataDirectory!=='string'||!configuration.dataDirectory)throw new Error('Choose this installation\'s data directory.');
  const data=path.resolve(configuration.dataDirectory),state=path.join(userData,'state');
  if(appId!=='terrier')return {data,state};
  const district=configuration.district||'co-05';
  if(!DISTRICTS.includes(district))throw new Error('Unsupported Terrier district.');
  if(configuration.terrierLegacy!==undefined){
    const legacy=configuration.terrierLegacy;
    if(!legacy||legacy.schemaVersion!==1||legacy.district!==district||legacy.firstPasswordOwner!=='admin'||typeof legacy.dataDirectory!=='string'||physicalPath(legacy.dataDirectory)!==physicalPath(data)||Object.keys(legacy).some(key=>!['schemaVersion','district','firstPasswordOwner','dataDirectory'].includes(key)))throw new Error('Invalid approved Terrier legacy connection.');
    if(configuration.ownerGitHubData!==undefined){
      const selected=configuration.ownerGitHubData;
      if(!selected||Object.keys(selected).sort().join('|')!=='cacheRoot|catalogueSha256|district|schemaVersion'||selected.schemaVersion!==1||selected.district!==district||typeof selected.cacheRoot!=='string'||!path.isAbsolute(selected.cacheRoot)||typeof selected.catalogueSha256!=='string'||!/^[a-f0-9]{64}$/.test(selected.catalogueSha256))throw new Error('Invalid owner GitHub district cache.');
      const allowed=[path.join(userData,'packs'),...(process.env.LOCALAPPDATA?[path.join(process.env.LOCALAPPDATA,'MelangeData','terrier')]:[])];
      if(!allowed.some(root=>physicalPath(root)===physicalPath(selected.cacheRoot)))throw new Error('Unapproved owner GitHub cache root.');
      let cached=path.resolve(selected.cacheRoot);
      for(const part of ['','github-districts','datasets',selected.catalogueSha256,district]){
        if(part)cached=path.join(cached,part);
        if(!fs.existsSync(cached)||fs.lstatSync(cached).isSymbolicLink()||!fs.lstatSync(cached).isDirectory())throw new Error('Owner GitHub district cache is unavailable or unsafe.');
      }
      if(contains(physicalPath(data),physicalPath(cached))||contains(physicalPath(cached),physicalPath(data)))throw new Error('GitHub district data must be separate from existing owner state.');
      return {data:cached,state:data,district,legacyOwner:'admin',githubDistrictData:true};
    }
    return {data,state:data,district,legacyOwner:'admin'};
  }
  if(configuration.ownerGitHubData!==undefined)throw new Error('Owner GitHub district data requires an existing owner connection.');
  const managed=managedStore(data);
  return {data:path.join(data,'districts',district),state:path.join(managed?path.join(managed,'state'):state,'districts',district),district};
}

function prepareRuntimeRoots(appId,userData,configuration,protectedPaths=[]){
  const roots=runtimeRoots(appId,userData,configuration);
  if(!fs.statSync(configuration.dataDirectory).isDirectory())throw new Error('Choose this installation\'s data directory.');
  refuseInstalledOverlap(configuration.dataDirectory,protectedPaths);
  refuseInstalledOverlap(roots.data,protectedPaths);
  refuseInstalledOverlap(roots.state,protectedPaths);
  if(roots.legacyOwner){
    for(const name of ['accounts.sqlite3','.secret']){
      const existing=path.join(roots.state,name);
      if(!fs.existsSync(existing)||!fs.lstatSync(existing).isFile()||fs.lstatSync(existing).isSymbolicLink()||physicalPath(path.dirname(fs.realpathSync.native(existing)))!==physicalPath(roots.state))throw new Error('Approved legacy Terrier accounts and secret must exist together in the selected district directory.');
    }
  }else if(appId==='terrier'){
    for(const base of [path.dirname(path.dirname(roots.state)),path.resolve(configuration.dataDirectory)]){
      if(fs.existsSync(base)&&fs.readdirSync(base).some(name=>name!=='districts')){
        throw new Error('Existing Terrier data uses an unscoped layout. Its files were preserved. Migrate it explicitly before selecting district storage, or choose a fresh installation and empty data directory.');
      }
    }
  }
  fs.mkdirSync(roots.data,{recursive:true});fs.mkdirSync(roots.state,{recursive:true});
  return roots;
}

function validatedLaunchPath(appId,value){
  if(value===undefined||value==='/')return '/';
  if(appId==='terrier'&&typeof value==='string'&&/^\/[?#]invite=[A-Za-z0-9_-]{32,256}$/.test(value))return value;
  throw new Error('Invalid application startup path.');
}
module.exports={runtimeRoots,prepareRuntimeRoots,validatedLaunchPath,refuseInstalledOverlap,physicalPath};
