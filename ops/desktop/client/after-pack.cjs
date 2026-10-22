'use strict';
// electron-builder afterPack: strip Mentat user state and runtime databases out of the packed Mentat/app sources.
// Only resources/runtime/<app>/sources/** is touched; third-party files elsewhere (proj.db, R's KEYWORDS.db and
// license.db, RSQLite's datasets.sqlite, ...) are legitimate and survive. Each removal is logged; this never fails
// the build. The extraResources glob filter in builder.cjs is case-sensitive and shallow, so this is the real guard.
const fs=require('node:fs'),path=require('node:path');
const DATABASE_SUFFIXES=['.sqlite','.sqlite3','.db','-wal','-shm','-journal'];
function stripState(runtimeRoot,log=()=>{}){
  const removed=[];
  const remove=full=>{try{fs.rmSync(full,{recursive:true,force:true});removed.push(full);log('[after-pack] removed '+full);}catch(error){log('[after-pack] could not remove '+full+': '+error.message);}};
  const walk=directory=>{
    let entries;
    try{entries=fs.readdirSync(directory,{withFileTypes:true});}catch(error){log('[after-pack] could not read '+directory+': '+error.message);return;}
    for(const entry of entries){
      const lower=entry.name.toLowerCase(),full=path.join(directory,entry.name);
      if(lower==='state'||DATABASE_SUFFIXES.some(suffix=>lower.endsWith(suffix)))remove(full);
      else if(entry.isDirectory())walk(full);
    }
  };
  if(!fs.existsSync(runtimeRoot))return removed;
  for(const application of fs.readdirSync(runtimeRoot,{withFileTypes:true}))if(application.isDirectory())
    for(const child of fs.readdirSync(path.join(runtimeRoot,application.name),{withFileTypes:true}))
      if(child.isDirectory()&&child.name.toLowerCase()==='sources')walk(path.join(runtimeRoot,application.name,child.name));
  return removed;
}
async function afterPack(context){
  const removed=stripState(path.join(context.appOutDir,'resources','runtime'),message=>console.log(message));
  console.log('[after-pack] runtime packing report: removed '+removed.length+' state/database path(s) under runtime/*/sources');
}
module.exports=afterPack;module.exports.stripState=stripState;
