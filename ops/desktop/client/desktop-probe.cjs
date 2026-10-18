'use strict';
const {spawn}=require('node:child_process');
const {backendEnvironment}=require('./backend-command.cjs');
// These probes run independently; never block the Electron event loop on Python.
function desktopProbe(command,mode,payload,{timeout=30000,limit=32768}={}){
 return new Promise((resolve,reject)=>{
  const child=spawn(command.binary,[...command.args,mode],{env:backendEnvironment(),windowsHide:true,stdio:['pipe','pipe','pipe']});
  let settled=false,size=0;const chunks=[];
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);if(error)child.kill();error?reject(error):resolve(value);};
  const timer=setTimeout(()=>finish(new Error('Local data verification timed out.')),timeout);
  child.on('error',()=>finish(new Error('Local data verification unavailable.')));child.stdin.on('error',()=>finish(new Error('Local data verification unavailable.')));child.stderr.resume();
  child.stdout.on('data',chunk=>{size+=chunk.length;if(size>limit)finish(new Error('Invalid local verification response.'));else chunks.push(chunk);});
  child.on('close',code=>{if(code!==0)return finish(new Error('Local data verification failed.'));try{finish(null,JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{finish(new Error('Invalid local verification response.'));}});
  child.stdin.end(JSON.stringify(payload));
 });
}
module.exports={desktopProbe};
