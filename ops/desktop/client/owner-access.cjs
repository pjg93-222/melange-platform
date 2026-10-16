'use strict';
const fs=require('node:fs');const crypto=require('node:crypto');
const {canonical,descriptor,verifyAccessEnvelope}=require('./access-client.cjs');
const FIELDS=['schemaVersion','purpose','serverId','appId','deviceId','grantId','principalId','accountIdentity','grantSequence','issuedAt','profileHash','windowsSid','dataBindings'];
function exact(value,fields){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==[...fields].sort().join('|'))throw new Error('Invalid signed owner migration');}
function canonicalWindowsPath(value){
 if(process.platform!=='win32'||typeof value!=='string'||!/^[A-Za-z]:[\\/]/.test(value))throw new Error('Existing local Windows directory required');
 let result=fs.realpathSync.native(value);if(result.startsWith('\\\\?\\'))result=result.slice(4);
 if(!/^[A-Za-z]:[\\/]/.test(result)||!fs.statSync(result).isDirectory())throw new Error('Existing local Windows directory required');
 result=result.replaceAll('\\','/').toLowerCase();return result.length>3?result.replace(/\/+$/,''):result;
}
function windowsPathHash(value){return crypto.createHash('sha256').update('win32:'+canonicalWindowsPath(value),'utf8').digest('hex');}
function localMapping(item,appId,districts){
 exact(item,['districtId','dataRootHash','localUserId','localUsername']);
 if(typeof item.dataRootHash!=='string'||!/^[a-f0-9]{64}$/.test(item.dataRootHash))throw new Error('Invalid owner data binding');
 if(appId==='terrier'){
  if(!districts.includes(item.districtId)||typeof item.localUserId!=='string'||!/^[1-9][0-9]{0,18}$/.test(item.localUserId)||BigInt(item.localUserId)>9223372036854775807n||typeof item.localUsername!=='string'||!/^[A-Za-z0-9_@.+-]{1,150}$/.test(item.localUsername))throw new Error('Invalid existing Terrier account mapping');
 }else if(item.districtId!==null||item.localUserId!==null||item.localUsername!==null)throw new Error('Unexpected local owner account mapping');
 return item;
}
function verifyOwnerRecord(record,{trust,accepted,profile,windowsSid,dataBindings,now=Date.now()}){
 exact(record,['schemaVersion','attestation']);if(record.schemaVersion!==1||!accepted?.grant||accepted.revoked!==false)throw new Error('Active signed owner approval required');
 const trusted=descriptor(trust),grant=accepted.grant,{body}=verifyAccessEnvelope(record.attestation,trusted);exact(body,FIELDS);
 if(body.schemaVersion!==1||body.purpose!=='melange-desktop-owner'||body.serverId!==trusted.serverId||body.grantSequence!==grant.sequence||!Number.isSafeInteger(body.issuedAt)||body.issuedAt<946684800||body.issuedAt>Math.floor(now/1000)+300)throw new Error('Invalid signed owner migration');
 for(const key of ['serverId','appId','deviceId','grantId','principalId','accountIdentity'])if(body[key]!==grant[key])throw new Error('Owner migration belongs to another approved installation');
 if(typeof windowsSid!=='string'||!/^S-1-[0-9]+(?:-[0-9]+){1,15}$/.test(windowsSid)||body.windowsSid!==windowsSid||body.profileHash!==windowsPathHash(profile))throw new Error('Owner migration belongs to another Windows user or profile');
 const required=grant.appId==='terrier'?grant.districtIds:[null];
 if(!Array.isArray(body.dataBindings)||body.dataBindings.length!==required.length||!Array.isArray(dataBindings)||dataBindings.length!==required.length)throw new Error('Owner workspace bindings are incomplete');
 const actual=body.dataBindings.map(item=>localMapping(item,grant.appId,grant.districtIds));
 if(canonical(actual.map(item=>item.districtId))!==canonical(required))throw new Error('Owner district bindings do not match approved scope');
 const expected=dataBindings.map(item=>{exact(item,['districtId','dataRoot','localUserId','localUsername']);return localMapping({districtId:item.districtId,dataRootHash:windowsPathHash(item.dataRoot),localUserId:item.localUserId,localUsername:item.localUsername},grant.appId,grant.districtIds);});
 if(canonical(actual)!==canonical(expected))throw new Error('Owner account or workspace mapping changed');
 return Object.freeze({mode:'existing-owner',appId:grant.appId,grantId:grant.grantId,accountIdentity:grant.accountIdentity,dataBindings:structuredClone(actual)});
}
module.exports={canonicalWindowsPath,windowsPathHash,verifyOwnerRecord};
