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
    return {data,state:data,district,legacyOwner:'admin'};
  }
  return {data:path.join(data,'districts',district),state:path.join(state,'districts',district),district};
}

function prepareRuntimeRoots(appId,userData,configuration,protectedPaths=[]){
  const roots=runtimeRoots(appId,userData,configuration);
  if(!fs.statSync(configuration.dataDirectory).isDirectory())throw new Error('Choose this installation\'s data directory.');
  refuseInstalledOverlap(configuration.dataDirectory,protectedPaths);
  refuseInstalledOverlap(roots.state,protectedPaths);
  if(roots.legacyOwner){
    for(const name of ['accounts.sqlite3','.secret']){
      const existing=path.join(roots.data,name);
      if(!fs.existsSync(existing)||!fs.lstatSync(existing).isFile()||fs.lstatSync(existing).isSymbolicLink()||physicalPath(path.dirname(fs.realpathSync.native(existing)))!==physicalPath(roots.data))throw new Error('Approved legacy Terrier accounts and secret must exist together in the selected district directory.');
    }
  }else if(appId==='terrier'){
    for(const base of [path.join(userData,'state'),path.resolve(configuration.dataDirectory)]){
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
