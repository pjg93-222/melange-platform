'use strict';
const path=require('node:path');
const {execFile}=require('node:child_process');

// Fixed source; the file path is JSON data on stdin, never executable shell text.
const SCRIPT="$ProgressPreference='SilentlyContinue'; $ErrorActionPreference='Stop'; $inputData=[Console]::In.ReadToEnd() | ConvertFrom-Json; $signature=Get-AuthenticodeSignature -LiteralPath $inputData.path; [ordered]@{status=[int]$signature.Status; path=$signature.Path; subject=$signature.SignerCertificate.Subject} | ConvertTo-Json -Compress";
function verifySignatureResult(result,filename,publisherName){
  if(!publisherName||result?.status!==0||result.subject!==publisherName||typeof result.path!=='string'||
      path.win32.resolve(result.path).toLowerCase()!==path.win32.resolve(filename).toLowerCase())throw new Error('Windows package signature or exact publisher identity could not be verified.');
}
async function verifyWindowsPackage(filename,publisherName){
  if(process.platform!=='win32')throw new Error('Windows package signature verification requires Windows.');
  if(typeof publisherName!=='string'||!publisherName.includes('CN='))throw new Error('A complete Windows publisher certificate subject is required.');
  const executable=path.join(process.env.SystemRoot||'C:\\Windows','System32','WindowsPowerShell','v1.0','powershell.exe');
  const encoded=Buffer.from(SCRIPT,'utf16le').toString('base64');
  const result=await new Promise((resolve,reject)=>{
    const environment={...process.env,PSModulePath:path.join(process.env.SystemRoot||'C:\\Windows','System32','WindowsPowerShell','v1.0','Modules')};
    const child=execFile(executable,['-NoProfile','-NonInteractive','-EncodedCommand',encoded],{env:environment,windowsHide:true,timeout:20000,maxBuffer:32768},(error,stdout,stderr)=>{
      if(error||stderr){reject(new Error('Windows package signature verification failed.'));return;}
      try{resolve(JSON.parse(stdout));}catch{reject(new Error('Invalid Windows signature verification result.'));}
    });
    child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify({path:path.resolve(filename)}));
  });
  verifySignatureResult(result,filename,publisherName);
}
module.exports={verifyWindowsPackage,verifySignatureResult};
