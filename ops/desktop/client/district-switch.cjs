'use strict';
const fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {backendCommand,backendEnvironment}=require('./backend-command.cjs');
const {runtimeRoots,refuseInstalledOverlap,physicalPath}=require('./runtime-roots.cjs');
const IDS=Object.freeze(['co-05','or-05','ct-01']);
const LABELS=Object.freeze({'co-05':'Colorado · District 05','or-05':'Oregon · District 05','ct-01':'Connecticut · District 01'});
function ownerDistrictScope(configuration){
  const map=configuration.terrierDistricts;
  if(!map)return [configuration.district||'co-05'];
  if(!configuration.terrierLegacy||map.schemaVersion!==1||typeof map.legacyRoot!=='string'||!Array.isArray(map.districts)||map.districts.length>3||new Set(map.districts).size!==map.districts.length||map.districts.some(id=>!IDS.includes(id))||Object.keys(map).some(k=>!['schemaVersion','legacyRoot','districts'].includes(k)))throw new Error('Invalid approved district connections.');
  const current=configuration.district||'co-05',expected=current==='co-05'?map.legacyRoot:path.join(map.legacyRoot,current);
  if(physicalPath(expected)!==physicalPath(configuration.dataDirectory))throw new Error('Selected district does not match its approved data directory.');
  const {ownerGitHubData,...ownerConfiguration}=configuration;
  runtimeRoots('terrier','unused',ownerConfiguration);return [...map.districts];
}
function districtConfiguration(configuration,district,allowedDistricts){
  // A newly selected district must be freshly prepared by the owner provider;
  // a prior district's immutable cache mapping is never copied or persisted.
  const {ownerGitHubData,...base}=configuration;configuration=base;
  if(!IDS.includes(district)||!Array.isArray(allowedDistricts)||!allowedDistricts.includes(district))throw new Error('This district has not been approved for this installation.');
  if(configuration.terrierLegacy){
    if(!ownerDistrictScope(configuration).includes(district))throw new Error('This district has no approved existing data connection.');
    if(!configuration.terrierDistricts){if(district!==configuration.district)throw new Error('Connect the existing district data first.');return {...configuration};}
    const dataDirectory=district==='co-05'?configuration.terrierDistricts.legacyRoot:path.join(configuration.terrierDistricts.legacyRoot,district);
    return {...configuration,dataDirectory,district,terrierLegacy:{schemaVersion:1,district,firstPasswordOwner:'admin',dataDirectory}};
  }
  return {...configuration,district};
}
function verifyDistrict(resources,userData,configuration,protectedPaths=[]){
  const command=backendCommand(resources,'terrier'),roots=runtimeRoots('terrier',userData,configuration);
  if(!fs.statSync(configuration.dataDirectory).isDirectory())throw new Error('The selected data directory is unavailable.');
  for(const target of [roots.data,roots.state])refuseInstalledOverlap(target,[resources,...protectedPaths]);
  const result=spawnSync(command.binary,[...command.args,'--desktop-district-check'],{input:JSON.stringify({sourceRoot:path.join(command.runtime,'sources'),dataRoot:roots.data,district:roots.district,legacy:Boolean(roots.legacyOwner),...(roots.githubDistrictData?{stateRoot:roots.state,githubDistrictData:true}:{})}),encoding:'utf8',windowsHide:true,timeout:30000,maxBuffer:32768,env:backendEnvironment()});
  if(result.error||result.status!==0)throw new Error('The selected district data could not be verified.');
  let reply;try{reply=JSON.parse(result.stdout);}catch{throw new Error('District validation returned an invalid response.');}
  if(reply.district!==roots.district||reply.validated!==true)throw new Error('District validation did not confirm the selected data.');
  return true;
}
async function verifyDistrictAsync(resources,userData,configuration,protectedPaths=[]){
 const command=backendCommand(resources,'terrier'),roots=runtimeRoots('terrier',userData,configuration);
 if(!fs.statSync(configuration.dataDirectory).isDirectory())throw new Error('The selected data directory is unavailable.');
 for(const target of [roots.data,roots.state])refuseInstalledOverlap(target,[resources,...protectedPaths]);
 const reply=await require('./desktop-probe.cjs').desktopProbe(command,'--desktop-district-check',{sourceRoot:path.join(command.runtime,'sources'),dataRoot:roots.data,district:roots.district,legacy:Boolean(roots.legacyOwner),...(roots.githubDistrictData?{stateRoot:roots.state,githubDistrictData:true}:{})});
 if(reply.district!==roots.district||reply.validated!==true)throw new Error('District validation did not confirm the selected data.');return true;
}
class PendingDistrictSwitch{
  constructor({read,write,scope,verify}){this.read=read;this.write=write;this.scope=scope;this.verify=verify;this.pending=null;}
  prepare(district){const current=this.read(),next=districtConfiguration(current,district,this.scope(current));this.verify(next);this.pending=next;return next;}
  cancel(){this.pending=null;}
  commitAfterClosed(){if(!this.pending)return false;const current=this.read();const next=districtConfiguration(current,this.pending.district,this.scope(current));this.verify(next);this.write(next);this.pending=null;return true;}
}
function preserveUnloadVeto(contents,onVeto){contents.on('will-prevent-unload',()=>onVeto());}
module.exports={IDS,LABELS,ownerDistrictScope,districtConfiguration,verifyDistrict,verifyDistrictAsync,PendingDistrictSwitch,preserveUnloadVeto};
