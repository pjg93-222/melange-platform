'use strict';
const crypto=require('node:crypto');const {canonical,descriptor}=require('./access-client.cjs');
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,HASH=/^[a-f0-9]{64}$/;
const KINDS=new Set(['melange.workspace-catalog.v1','melange.notes.v1','melange.identity-decisions.v1','melange.staffer-graph.v1','melange.evidence.v1','mentat.published-report.v1','mentat.collection.v1','mentat.forecast.v1','terrier.district-data.v1','terrier.record-changes.v1']);
const PROPOSAL=['schemaVersion','datasetId','revisionId','parentRevisionIds','districtId','kind','adapterVersion','objects','counts'];
const SIGNED=[...PROPOSAL,'purpose','serverId','appId','originPrincipalId','originDeviceId','committedAt','catalogSequence'];
const MAX_OBJECT=128*1024**3,CHUNK=4*1024**2;
function exact(value,fields){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==[...fields].sort().join('|'))throw new Error('Unsupported dataset metadata');}
function uuid(value){return typeof value==='string'&&UUID.test(value)&&value!=='00000000-0000-0000-0000-000000000000';}
function hash(value){return typeof value==='string'&&HASH.test(value);}
function integer(value,max=Number.MAX_SAFE_INTEGER){return Number.isSafeInteger(value)&&value>=0&&value<=max;}
function scope(grant,districtId){if(grant.appId==='terrier'?!grant.districtIds.includes(districtId):districtId!==null)throw new Error('Dataset district is not approved');}
function proposal(value,grant,{registry=Object.freeze({}),allowUnsupported=false,signed=false}={}){
 exact(value,signed?SIGNED:PROPOSAL);
 if(value.schemaVersion!==1||value.adapterVersion!==1||!uuid(value.datasetId)||!uuid(value.revisionId)||!KINDS.has(value.kind)||!value.kind.startsWith(grant.appId+'.'))throw new Error('Unsupported dataset identity or kind');
 scope(grant,value.districtId);
 if(!Array.isArray(value.parentRevisionIds)||value.parentRevisionIds.length>8||new Set(value.parentRevisionIds).size!==value.parentRevisionIds.length||value.parentRevisionIds.some(id=>!uuid(id)||id===value.revisionId))throw new Error('Invalid dataset parents');
 if(!Array.isArray(value.objects)||value.objects.length<1||value.objects.length>128)throw new Error('Invalid dataset objects');
 // District packs are gzip objects; rawSize (bytes after gunzip) lets the client bound decompression before it starts.
 const raw=value.kind==='terrier.district-data.v1';
 const roles=new Set();for(const item of value.objects){exact(item,raw?['role','sha256','size','rawSize']:['role','sha256','size']);if(typeof item.role!=='string'||!/^[a-z][a-z0-9_.-]{0,63}$/.test(item.role)||roles.has(item.role)||!hash(item.sha256)||!integer(item.size,MAX_OBJECT)||raw&&!integer(item.rawSize))throw new Error('Invalid dataset object');roles.add(item.role);}
 if(!value.counts||typeof value.counts!=='object'||Array.isArray(value.counts)||Object.keys(value.counts).some(key=>!['items','files','records'].includes(key)||!integer(value.counts[key],1000000000)))throw new Error('Invalid dataset counts');
 if(Buffer.byteLength(canonical(value))>65536)throw new Error('Dataset metadata too large');
 const adapter=Object.hasOwn(registry,value.kind)?registry[value.kind]:null;
 if(!adapter){if(!allowUnsupported)throw new Error('Dataset adapter is unavailable');return {supported:false,adapter:null};}
 if(adapter.appId!==grant.appId||adapter.version!==1||typeof adapter.validateMetadata!=='function')throw new Error('Invalid compiled dataset adapter');
 if(value.parentRevisionIds.length>1&&(typeof adapter.validateMerge!=='function'||adapter.validateMerge(value)!==true))throw new Error('Dataset merge requires a compiled domain validator');
 if(adapter.validateMetadata(value)!==true)throw new Error('Dataset adapter rejected metadata');
 return {supported:true,adapter};
}
function verifyRevision(envelope,trustValue,grant,options={}){
 const trust=descriptor(trustValue);exact(envelope,['schemaVersion','keyId','payload','signature']);
 if(envelope.schemaVersion!==1||typeof envelope.keyId!=='string'||!Object.hasOwn(trust.accessPublicKeys,envelope.keyId)||typeof envelope.payload!=='string'||envelope.payload.length>4*Math.ceil(65536/3)||typeof envelope.signature!=='string')throw new Error('Untrusted dataset envelope');
 const bytes=Buffer.from(envelope.payload,'base64'),signature=Buffer.from(envelope.signature,'base64');if(bytes.length>65536||bytes.toString('base64')!==envelope.payload||signature.length!==64||signature.toString('base64')!==envelope.signature)throw new Error('Invalid dataset encoding');
 const key=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),Buffer.from(trust.accessPublicKeys[envelope.keyId],'base64')]),format:'der',type:'spki'});
 if(!crypto.verify(null,bytes,key,signature))throw new Error('Invalid dataset signature');
 const body=JSON.parse(bytes.toString('utf8'));if(canonical(body)!==bytes.toString('utf8'))throw new Error('Noncanonical dataset metadata');
 const checked=proposal(body,grant,{...options,allowUnsupported:true,signed:true});
 if(body.purpose!=='melange-desktop-dataset'||body.serverId!==trust.serverId||body.serverId!==grant.serverId||body.appId!==grant.appId||!uuid(body.originPrincipalId)||!uuid(body.originDeviceId)||!integer(body.catalogSequence)||body.catalogSequence<1||!integer(body.committedAt)||body.committedAt<946684800||body.committedAt>Math.floor((options.now??Date.now())/1000)+300)throw new Error('Dataset signature has the wrong purpose or scope');
 return {revision:body,envelope:structuredClone(envelope),digest:crypto.createHash('sha256').update(bytes).digest('hex'),supported:checked.supported,adapter:checked.adapter};
}
function uploadStatus(value,{uploadId,size}={}){
 const failed=value?.status==='failed';exact(value,failed&&Object.hasOwn(value,'error')?['uploadId','offset','size','complete','status','error']:['uploadId','offset','size','complete','status']);
 if(!uuid(value.uploadId)||uploadId&&value.uploadId!==uploadId||!integer(value.size,MAX_OBJECT)||size!==undefined&&value.size!==size||!integer(value.offset,value.size)||typeof value.complete!=='boolean'||!['uploading','verifying','complete','failed'].includes(value.status)||value.complete!==(value.status==='complete')||value.complete&&value.offset!==value.size||value.status==='verifying'&&value.offset!==value.size||value.error!==undefined&&!['invalid_object','storage_unavailable'].includes(value.error))throw new Error('Invalid dataset upload status');return value;
}
// Untrusted JSON replies can contain whitespace, but never duplicate keys or
// prototype aliases. Signed payloads have their stricter canonical byte check.
function strictJson(bytes){
 const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);let position=0,nodes=0;
 const ws=()=>{while(/[\t\r\n ]/.test(text[position]||'!'))position++;};
 function string(){const start=position++;while(position<text.length){const c=text[position++];if(c==='"')return JSON.parse(text.slice(start,position));if(c==='\\')position++;}throw new Error('Invalid dataset JSON');}
 function parse(depth){if(depth>40||++nodes>200000)throw new Error('Dataset JSON nesting too large');ws();const c=text[position];
  if(c==='"')return string();
  if(c==='{'){position++;const object=Object.create(null),keys=new Set();ws();if(text[position]==='}'){position++;return object;}while(true){ws();if(text[position]!=='"')throw new Error('Invalid dataset JSON');const key=string();if(keys.has(key)||['__proto__','constructor','prototype'].includes(key))throw new Error('Duplicate or unsafe dataset JSON key');keys.add(key);ws();if(text[position++]!==':')throw new Error('Invalid dataset JSON');object[key]=parse(depth+1);ws();const end=text[position++];if(end==='}')return object;if(end!==',')throw new Error('Invalid dataset JSON');}}
  if(c==='['){position++;const value=[];ws();if(text[position]===']'){position++;return value;}while(true){value.push(parse(depth+1));ws();const end=text[position++];if(end===']')return value;if(end!==',')throw new Error('Invalid dataset JSON');}}
  const found=text.slice(position).match(/^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/);if(!found)throw new Error('Invalid dataset JSON');position+=found[0].length;const value=JSON.parse(found[0]);if(typeof value==='number'&&!Number.isFinite(value))throw new Error('Invalid dataset number');return value;
 }
 const result=parse(0);ws();if(position!==text.length)throw new Error('Trailing dataset JSON');return result;
}
module.exports={exact,uuid,hash,integer,scope,proposal,verifyRevision,uploadStatus,strictJson,KINDS,MAX_OBJECT,CHUNK};
