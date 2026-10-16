'use strict';
const {spawnSync}=require('node:child_process');
const {backendCommand,backendEnvironment}=require('./backend-command.cjs');
function credentialCommand(resources,appId,deviceId,action,token){
  if(!['melange','terrier','mentat'].includes(appId)||!['read','write','delete'].includes(action))throw new Error('Invalid credential scope');
  const command=backendCommand(resources,appId);
  const result=spawnSync(command.binary,[...command.args,'--desktop-credential'],{input:JSON.stringify({action,appId,deviceId,token}),env:backendEnvironment(),encoding:'utf8',windowsHide:true,timeout:15000,maxBuffer:16384});
  if(result.error||result.status!==0)throw new Error('Operating-system credential store unavailable');
  try{return JSON.parse(result.stdout);}catch{throw new Error('Invalid credential service response');}
}
module.exports={credentialCommand};
