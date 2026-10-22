'use strict';
const {spawn}=require('node:child_process');
const {backendCommand,backendEnvironment}=require('./backend-command.cjs');
const SCOPES=new Set(['access-token','access-password','access-request','access-device']);
function accessCredential({resources,appId,deviceId,scope,action,token}){
  if(!SCOPES.has(scope)||!['read','write','delete'].includes(action)||!['melange','terrier','mentat'].includes(appId))return Promise.reject(new Error('Invalid access credential scope'));
  const command=backendCommand(resources,appId);
  return new Promise((resolve,reject)=>{
    const child=spawn(command.binary,[...command.args,'--desktop-credential'],{env:backendEnvironment(),windowsHide:true,stdio:['pipe','pipe','pipe']});
    let size=0,settled=false;const chunks=[];
    const timer=setTimeout(()=>{child.kill();finish(new Error('Protected access storage timed out'));},15000);
    const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value);};
    child.on('error',()=>finish(new Error('Protected access storage unavailable')));
    child.stdin.on('error',()=>finish(new Error('Protected access storage unavailable')));
    child.stderr.resume();
    child.stdout.on('data',bytes=>{size+=bytes.length;if(size>8192){child.kill();finish(new Error('Invalid protected access response'));}else chunks.push(bytes);});
    child.on('close',code=>{if(code!==0)return finish(new Error('Protected access storage unavailable'));try{finish(null,JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{finish(new Error('Invalid protected access response'));}});
    child.stdin.end(JSON.stringify({action,appId,deviceId,scope,...(action==='write'?{token}:{})}));
  });
}
// One JSON record per app and device in its own Credential Manager namespace: the local password verifier, or the
// one-time device verification kept after the first successful sign-in.
function recordStore(resources,scope){return {
  async read(identity){const value=await accessCredential({resources,appId:identity.appId,deviceId:identity.deviceId,scope,action:'read'});if(value.token===null)return null;try{return JSON.parse(Buffer.from(value.token,'base64').toString('utf8'));}catch{throw new Error('Invalid protected local account');}},
  async write(identity,record){const token=Buffer.from(JSON.stringify(record)).toString('base64');if(token.length>2048)throw new Error('Protected local account is too large');await accessCredential({resources,appId:identity.appId,deviceId:identity.deviceId,scope,action:'write',token});},
  async delete(identity){await accessCredential({resources,appId:identity.appId,deviceId:identity.deviceId,scope,action:'delete'});}
};}
const passwordStore=resources=>recordStore(resources,'access-password');
const deviceStore=resources=>recordStore(resources,'access-device');
function accessSecrets(resources,identity){return {
  async read(scope){if(!['access-token','access-request'].includes(scope))throw new Error('Invalid access secret scope');const value=await accessCredential({resources,...identity,scope,action:'read'});if(value.token===null)return null;try{return JSON.parse(Buffer.from(value.token,'base64').toString('utf8'));}catch{throw new Error('Invalid protected access material');}},
  async write(scope,value){if(!['access-token','access-request'].includes(scope))throw new Error('Invalid access secret scope');const token=Buffer.from(JSON.stringify(value)).toString('base64');if(Buffer.byteLength(token,'ascii')>2048)throw new Error('Protected access material is too large');await accessCredential({resources,...identity,scope,action:'write',token});},
  async delete(scope){if(!['access-token','access-request'].includes(scope))throw new Error('Invalid access secret scope');await accessCredential({resources,...identity,scope,action:'delete'});}
};}
module.exports={accessCredential,passwordStore,deviceStore,accessSecrets};
