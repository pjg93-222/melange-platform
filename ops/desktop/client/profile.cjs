'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {appDefinition}=require('./apps.cjs');
const {physicalPath}=require('./runtime-roots.cjs');
function meaningfulProfile(directory){
  if(!fs.existsSync(directory))return false;
  for(const name of ['settings.json','enrollment.enc','update-state.json'])if(fs.existsSync(path.join(directory,name)))return true;
  for(const name of ['state','data']){const child=path.join(directory,name);if(fs.existsSync(child)&&(!fs.statSync(child).isDirectory()||fs.readdirSync(child).length))return true;}
  return false;
}
function selectProfile({appId,home,appData,packaged,testDirectory}){
  appDefinition(appId);
  if(!packaged&&testDirectory)return {directory:path.resolve(testDirectory),migrationRequired:false};
  const directory=path.join(home,'Documents','MelangeApplicationState',appId);
  const legacy=[path.join(appData,'MelangeApps',appId),path.join(home,'AppData','Roaming','MelangeApps',appId)];
  return {directory,migrationRequired:!meaningfulProfile(directory)&&legacy.some(meaningfulProfile)};
}
// Explicit packaged acceptance mode. Only a command-line switch selects it; it
// relocates writable roots and disables OS registration and native installation.
// It never changes trust, approval, enrollment, signature or updater verification.
const ISOLATED_SWITCH='--melange-isolated-profile';
const ISOLATED_TITLE_SUFFIX=' [isolated test profile]';
const ISOLATED_PATTERN=/^(?:--|-|\/)melange-isolated-profile(?:=|$)/i;
function refused(message){const error=new Error(message);error.code='ISOLATED_PROFILE_REFUSED';return error;}
function overlaps(a,b){for(const [parent,child] of [[a,b],[b,a]]){const relative=path.relative(parent,child);if(relative===''||(relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative)))return true;}return false;}
// A mistyped or repeated switch is refused rather than silently opening production state.
function isolatedProfileArgument(argv=[]){
  const matches=argv.filter(value=>typeof value==='string'&&ISOLATED_PATTERN.test(value));
  if(!matches.length)return null;
  if(matches.length!==1||!matches[0].startsWith(ISOLATED_SWITCH+'='))throw refused('Use exactly one '+ISOLATED_SWITCH+'=<absolute directory> argument.');
  return matches[0].slice(ISOLATED_SWITCH.length+1);
}
function isolatedRoot(value,{home,appData,documents,protectedPaths=[],platform=process.platform}){
  if(typeof value!=='string'||!value||value.length>1024||value.includes('\0'))throw refused('The isolated test profile directory is missing or invalid.');
  if(platform==='win32'?!/^[A-Za-z]:[\\/]/.test(value):!path.isAbsolute(value))throw refused('The isolated test profile must be an absolute local directory'+(platform==='win32'?' starting with a drive letter':'')+'.');
  const root=path.resolve(value);
  if(path.dirname(root)===root)throw refused('The isolated test profile cannot be a drive or filesystem root.');
  const forbidden=Object.freeze([path.join(home,'Documents','MelangeApplicationState'),path.join(home,'Documents','MelangeApplications'),path.join(appData,'MelangeApps'),path.join(home,'AppData','Roaming','MelangeApps'),
    ...(documents?[path.join(documents,'MelangeApplicationState'),path.join(documents,'MelangeApplications')]:[]),...protectedPaths]);
  const separate=()=>{const actual=physicalPath(root);for(const item of forbidden)if(overlaps(actual,physicalPath(item)))throw refused('The isolated test profile must be separate from production application state, legacy profiles and installed application files ('+item+').');};
  separate();
  if(fs.existsSync(root)&&!fs.statSync(root).isDirectory())throw refused('The isolated test profile path exists and is not a directory.');
  fs.mkdirSync(root,{recursive:true,mode:0o700});
  separate(); // Re-resolve after creation so a junction or link cannot redirect into production state.
  return Object.freeze({root,forbidden});
}
// Every path isolated mode reads or writes must resolve physically (through any
// junction or link, including children) inside the isolated root and outside
// production state, legacy profiles and installed program files.
function confineIsolatedPath(boundary,candidate,label='Path'){
  if(!boundary?.root||!Array.isArray(boundary.forbidden))throw refused('Isolated test profile boundary is unavailable.');
  if(typeof candidate!=='string'||!candidate||candidate.includes('\0')||!path.isAbsolute(candidate))throw refused(label+' must be an absolute directory inside the isolated test profile.');
  const actual=physicalPath(candidate),root=physicalPath(boundary.root),relative=path.relative(root,actual);
  if(relative.startsWith('..'+path.sep)||relative==='..'||path.isAbsolute(relative))throw refused(label+' resolves outside the isolated test profile ('+boundary.root+'): '+candidate);
  for(const item of boundary.forbidden)if(overlaps(actual,physicalPath(item)))throw refused(label+' resolves into production application state, legacy profiles or installed application files: '+candidate);
  return candidate;
}
// Settings are confined when loaded, when changed, and again before backend launch.
function confineIsolatedSettings(boundary,settings){
  if(!settings||typeof settings!=='object')throw refused('Isolated test profile settings are invalid.');
  if(settings.dataDirectory!==undefined)confineIsolatedPath(boundary,settings.dataDirectory,'The data directory');
  if(settings.terrierLegacy!==undefined)confineIsolatedPath(boundary,settings.terrierLegacy?.dataDirectory,'The legacy Terrier data directory');
  if(settings.terrierDistricts!==undefined)confineIsolatedPath(boundary,settings.terrierDistricts?.legacyRoot,'The Terrier district data root');
  return settings;
}
function selectStartupProfile({appId,argv=[],home,appData,documents,protectedPaths,packaged,testDirectory,platform}){
  const definition=appDefinition(appId),requested=isolatedProfileArgument(argv);
  if(requested===null)return {...selectProfile({appId,home,appData,packaged,testDirectory}),isolated:false,applicationUserModelId:definition.applicationId,
    discoveryCache:path.join(appData,'MelangeDesktop','github-discovery'),registerLoginItem:Boolean(packaged),nativeInstallation:true};
  try{
    const boundary=isolatedRoot(requested,{home,appData,documents:typeof documents==='function'?documents():documents,protectedPaths:(typeof protectedPaths==='function'?protectedPaths():protectedPaths)||[],platform});
    const {root}=boundary,directory=path.join(root,definition.id),discoveryCache=path.join(root,'github-discovery');
    const created=[[directory,'The isolated application profile'],[discoveryCache,'The isolated discovery cache']];
    // Writable children of the profile that the main process or backend uses directly.
    const children=['logs','state','data','updates','github-updates'].map(name=>[path.join(directory,name),'The isolated '+name+' directory']);
    for(const [item,label] of [...created,...children])confineIsolatedPath(boundary,item,label);
    for(const [item] of created){if(fs.existsSync(item)&&!fs.statSync(item).isDirectory())throw refused(item+' exists and is not a directory.');fs.mkdirSync(item,{recursive:true,mode:0o700});}
    for(const [item,label] of [...created,...children])confineIsolatedPath(boundary,item,label);
    return {directory,migrationRequired:false,isolated:true,root,boundary,applicationUserModelId:definition.applicationId+'.isolated',
      discoveryCache,registerLoginItem:false,nativeInstallation:false};
  }catch(error){throw error.code==='ISOLATED_PROFILE_REFUSED'?error:refused('The isolated test profile could not be prepared: '+error.message);}
}
module.exports={selectProfile,meaningfulProfile,selectStartupProfile,isolatedProfileArgument,confineIsolatedPath,confineIsolatedSettings,ISOLATED_SWITCH,ISOLATED_TITLE_SUFFIX};
