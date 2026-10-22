'use strict';
// One-click trust of the pinned self-signed release certificate (public part only, shipped beside the app).
// Verified updates install only where Windows reports that signer as Valid; trust changes nothing else here.
// The certificate is end-entity (CA:false) with only the Code Signing EKU, so it cannot vouch for websites.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFile}=require('node:child_process');
const THUMBPRINT='0EBC9AD04E929CDD731EDB5D278A4143BC3D3D30',CERTIFICATE='melange-systems-desktop-release.cer';
const powershell=()=>path.join(process.env.SystemRoot||'C:\\Windows','System32','WindowsPowerShell','v1.0','powershell.exe');
const encode=script=>Buffer.from(script,'utf16le').toString('base64');
// Fixed script text; the certificate bytes travel as base64 on stdin, never inside command-line text.
const PROBE="$ErrorActionPreference='Stop';Test-Path -LiteralPath 'Cert:\\CurrentUser\\Root\\"+THUMBPRINT+"'";
const IMPORT="$ErrorActionPreference='Stop';$bytes=[Convert]::FromBase64String([Console]::In.ReadToEnd().Trim());$cert=[System.Security.Cryptography.X509Certificates.X509Certificate2]::new($bytes);if($cert.Thumbprint -ne '"+THUMBPRINT+"'){exit 2};$store=[System.Security.Cryptography.X509Certificates.X509Store]::new('Root','CurrentUser');$store.Open('ReadWrite');try{$store.Add($cert)}finally{$store.Close()}";
function run(exec,script,{input,windowsHide,timeout}){
  return new Promise(resolve=>{
    let child;
    try{child=exec(powershell(),['-NoProfile','-NonInteractive','-EncodedCommand',encode(script)],{windowsHide,timeout,maxBuffer:32768},(error,stdout)=>resolve({error,stdout:String(stdout||'')}));}
    catch(error){resolve({error,stdout:''});return;}
    try{child?.stdin?.on?.('error',()=>{});child?.stdin?.end(input||'');}catch{}
  });
}
// The certificate bytes are read exactly once, checked against the pin, and those same bytes are what gets imported.
function pinnedCertificate(resources){
  const bytes=fs.readFileSync(path.join(resources,CERTIFICATE));
  if(crypto.createHash('sha1').update(bytes).digest('hex').toUpperCase()!==THUMBPRINT)throw new Error('The bundled publisher certificate does not match the pinned release certificate.');
  return bytes;
}
// true = trusted, false = definitely not in the store, null = the probe itself failed (spawn error, timeout,
// unexpected output). Only false may keep updates off; null means "unknown", and Authenticode still gates installs.
async function publisherTrusted(exec=execFile){
  const {error,stdout}=await run(exec,PROBE,{windowsHide:true,timeout:20000});
  if(error)return null;
  const answer=stdout.trim();
  return answer==='True'?true:answer==='False'?false:null;
}
// Windows shows its own Yes/No security dialog for the add; declining leaves the store unchanged.
async function trustPublisher(resources,exec=execFile){
  const bytes=pinnedCertificate(resources);
  await run(exec,IMPORT,{input:bytes.toString('base64'),windowsHide:false,timeout:10*60*1000});
  return await publisherTrusted(exec)===true;
}
// First interactive launch: ask once. 'unneeded' = already trusted or unknown, 'skipped' = declined earlier,
// 'trusted' = just trusted, 'declined' = the store still definitely lacks it afterwards (caller remembers that).
async function offerTrust(resources,{alreadyDeclined=false,exec=execFile}={}){
  if(alreadyDeclined)return 'skipped';
  if(await publisherTrusted(exec)!==false)return 'unneeded';
  if(await trustPublisher(resources,exec))return 'trusted';
  return await publisherTrusted(exec)===false?'declined':'unneeded';
}
module.exports={THUMBPRINT,CERTIFICATE,pinnedCertificate,publisherTrusted,trustPublisher,offerTrust};
