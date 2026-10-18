'use strict';
const fs=require('node:fs'),path=require('node:path');
// Portable packaging changes program lifecycle only. Access, profile and data
// boundaries remain identical to the installed application.
function portableMode(resources,appId){
 const filename=path.join(resources,'portable-mode.json');let stat;
 try{stat=fs.lstatSync(filename);}catch(error){if(error.code==='ENOENT')return false;throw error;}
 if(!stat.isFile()||stat.isSymbolicLink()||stat.size>1024)throw new Error('Invalid portable application marker.');
 const value=JSON.parse(fs.readFileSync(filename,'utf8'));
 if(appId!=='terrier'||!value||Object.keys(value).sort().join('|')!=='appId|schemaVersion'||value.appId!==appId||value.schemaVersion!==1)throw new Error('Invalid portable application marker.');
 return true;
}
module.exports={portableMode};
