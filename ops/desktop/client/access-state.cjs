'use strict';
// Main process only. A renderer never supplies profile, kind or resource paths.
const {spawn}=require('node:child_process');
const {backendCommand,backendEnvironment}=require('./backend-command.cjs');
const KINDS=new Set(['identity','trust','state','owner','activity','dataset-journal']);
const LIMIT=12*1024*1024;
function protectedAccess({resources,appId,profile,action,kind,value}){
 if(!['read','write','describe'].includes(action)||action!=='describe'&&!KINDS.has(kind)||typeof profile!=='string')return Promise.reject(new Error('Invalid protected access operation'));
 const command=backendCommand(resources,appId),payload=JSON.stringify({action,profile,...(action!=='describe'?{kind}:{}),...(action==='write'?{value}:{})});
 if(Buffer.byteLength(payload)>LIMIT)return Promise.reject(new Error('Protected access record too large'));
 return new Promise((resolve,reject)=>{
  const child=spawn(command.binary,[...command.args,'--desktop-access-state'],{env:backendEnvironment(),windowsHide:true,stdio:['pipe','pipe','pipe']});let settled=false,size=0;const chunks=[];
  const finish=(error,result)=>{if(settled)return;settled=true;clearTimeout(timer);if(error)child.kill();error?reject(error):resolve(result);};
  const timer=setTimeout(()=>finish(new Error('Protected access storage timed out')),15000);
  child.on('error',()=>finish(new Error('Protected access storage unavailable')));child.stdin.on('error',()=>finish(new Error('Protected access storage unavailable')));child.stderr.resume();
  child.stdout.on('data',part=>{size+=part.length;if(size>LIMIT)finish(new Error('Protected access response too large'));else chunks.push(part);});
  child.on('close',code=>{if(code!==0)return finish(new Error('Protected access storage unavailable'));try{const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(action==='read'&&(!result||Object.keys(result).join('|')!=='value')||action==='write'&&(!result||Object.keys(result).join('|')!=='ok'||result.ok!==true)||action==='describe'&&(!result||Object.keys(result).sort().join('|')!=='profileHash|windowsSid'||typeof result.profileHash!=='string'||!/^[0-9a-f]{64}$/.test(result.profileHash)||typeof result.windowsSid!=='string'||result.windowsSid.length>184||!/^S-1-[0-9]+(?:-[0-9]+){1,15}$/.test(result.windowsSid)))throw new Error();finish(null,result);}catch{finish(new Error('Invalid protected access response'));}});
  child.stdin.end(payload);
 });
}
function accessStateStore({resources,appId,profile,kind}){if(!KINDS.has(kind))throw new Error('Unsupported protected access record');return {read:async()=>(await protectedAccess({resources,appId,profile,kind,action:'read'})).value,write:async value=>protectedAccess({resources,appId,profile,kind,action:'write',value})};}
module.exports={protectedAccess,accessStateStore};
