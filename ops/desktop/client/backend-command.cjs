'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {appDefinition}=require('./apps.cjs');

function backendCommand(resources,appId,platform=process.platform,runtimeRoot=path.join(resources,'runtime')){
  const definition=appDefinition(appId),runtime=path.join(runtimeRoot,definition.id);
  const interpreter=path.join(runtime,'python',platform==='win32'?'python.exe':'bin/python3');
  const bootstrap=path.join(runtime,'bootstrap','desktop_backend.py');
  if(fs.existsSync(interpreter)&&fs.existsSync(bootstrap)){
    // Ignore environment-based Python injection and user site packages while retaining
    // bootstrap sibling imports. Python subprocesses keep real interpreter semantics.
    return {binary:interpreter,args:['-E','-s','-u',bootstrap],runtime};
  }
  const frozen=path.join(runtime,platform==='win32'?'desktop-backend.exe':'desktop-backend');
  if(fs.existsSync(frozen))return {binary:frozen,args:[],runtime};
  throw new Error('Native '+definition.name+' runtime is not installed. Build its runtime before packaging.');
}

function backendEnvironment(){
  const environment={...process.env};
  for(const key of Object.keys(environment)){
    if(/^(ACCESS_|CU_|MENTAT_|MONEYBALL_|MELANGE_)/.test(key)||/^PYTHON(?:HOME|PATH|USERBASE|STARTUP|INSPECT)$/.test(key))delete environment[key];
  }
  environment.PYTHONNOUSERSITE='1';environment.PYTHONUNBUFFERED='1';
  return environment;
}
module.exports={backendCommand,backendEnvironment};
