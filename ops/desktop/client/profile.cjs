'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {appDefinition}=require('./apps.cjs');
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
module.exports={selectProfile,meaningfulProfile};
