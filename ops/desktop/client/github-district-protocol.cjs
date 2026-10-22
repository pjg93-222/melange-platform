'use strict';
// A separate signature domain and sequence from application binary updates.
const crypto=require('node:crypto');
const {canonical}=require('./access-client.cjs');
const {exact,hash,integer,strictJson}=require('./dataset-protocol.cjs');
const REPOSITORY='pjg93-222/Geo',POINTER_PATH='docs/district-testsets/current.json';
const IDS=Object.freeze(['co-05','or-05','ct-01']);
const ROLES=Object.freeze(['contacts.sqlite3','density.json','roads.sqlite3','scores.sqlite3','street.sqlite3','walk.sqlite3']);
const COMMIT=/^[a-f0-9]{40}$/,TAG=/^district-testsets-\d{4}-\d{2}-\d{2}(?:-[a-z0-9][a-z0-9.-]{0,40})?$/;
const MAX_MANIFEST=2*1024*1024,MAX_FILE=3*1024**3,MAX_ARCHIVE=2*1024**3-1;
function requireValue(value,message='Invalid GitHub district metadata'){if(!value)throw new Error(message);}
function assetURL(releaseTag,name){
 requireValue(TAG.test(releaseTag)&&typeof name==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,150}$/.test(name));
 return 'https://github.com/'+REPOSITORY+'/releases/download/'+releaseTag+'/'+name;
}
function pointer(envelope,publicKeys,previous={}){
 exact(envelope,['schemaVersion','keyId','payload','signature']);
 requireValue(envelope.schemaVersion===1&&typeof envelope.keyId==='string'&&Object.hasOwn(publicKeys,envelope.keyId)&&typeof envelope.payload==='string'&&envelope.payload.length<16384&&typeof envelope.signature==='string','Untrusted GitHub district pointer');
 const raw=publicKeys[envelope.keyId],bytes=Buffer.from(envelope.payload,'base64'),signature=Buffer.from(envelope.signature,'base64');
 requireValue(typeof raw==='string'&&Buffer.from(raw,'base64').length===32&&Buffer.from(raw,'base64').toString('base64')===raw&&bytes.toString('base64')===envelope.payload&&signature.length===64&&signature.toString('base64')===envelope.signature,'Invalid GitHub district signature encoding');
 const key=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),Buffer.from(raw,'base64')]),format:'der',type:'spki'});
 requireValue(crypto.verify(null,bytes,key,signature),'Invalid GitHub district signature');
 const value=strictJson(bytes),digest=crypto.createHash('sha256').update(bytes).digest('hex');
 exact(value,['schemaVersion','purpose','appId','repository','catalogSequence','releaseTag','releaseCommit','manifest']);
 requireValue(canonical(value)===bytes.toString('utf8')&&value.schemaVersion===1&&value.purpose==='terrier-public-district-data'&&value.appId==='terrier'&&value.repository===REPOSITORY&&integer(value.catalogSequence)&&value.catalogSequence>0&&typeof value.releaseTag==='string'&&TAG.test(value.releaseTag)&&typeof value.releaseCommit==='string'&&COMMIT.test(value.releaseCommit)&&value.releaseCommit!=='0'.repeat(40),'Unsupported GitHub district pointer');
 exact(value.manifest,['asset','bytes','sha256']);
 requireValue(value.manifest.asset==='manifest.json'&&integer(value.manifest.bytes,MAX_MANIFEST)&&value.manifest.bytes>0&&hash(value.manifest.sha256));
 requireValue(value.catalogSequence>=(previous.catalogSequence||0)&&(value.catalogSequence!==previous.catalogSequence||digest===previous.pointerDigest),'GitHub district rollback or equivocation refused');
 return {value,digest,envelope:structuredClone(envelope)};
}
function relative(value){return typeof value==='string'&&value.length<400&&value.split('/').every(part=>part&&!['.','..'].includes(part)&&!/[\\:\x00-\x1f\x7f]/.test(part));}
function manifest(bytes,checked){
 requireValue(Buffer.isBuffer(bytes)&&bytes.length===checked.value.manifest.bytes&&crypto.createHash('sha256').update(bytes).digest('hex')===checked.value.manifest.sha256,'GitHub district catalogue integrity failed');
 const value=strictJson(bytes),releaseTag=checked.value.releaseTag;
 requireValue(value.schema==='terrier-district-testsets-v1'&&value.repository===REPOSITORY&&value.release_tag===releaseTag&&Array.isArray(value.districts)&&value.districts.length===IDS.length&&IDS.every(id=>value.districts.includes(id))&&new Set(value.districts).size===IDS.length,'Unsupported GitHub district catalogue');
 exact(value.distribution,['kind','make_latest','desktop_update_tag']);
 requireValue(value.distribution.kind==='data_prerelease'&&value.distribution.make_latest===false&&value.distribution.desktop_update_tag===false);
 requireValue(Array.isArray(value.files)&&value.files.length>0&&value.files.length<=10000&&value.unique_dataset_files===value.files.length&&integer(value.dataset_input_bytes)&&Array.isArray(value.dataset_assets)&&value.dataset_assets.length===3);
 const files=new Map(),assets=new Map();let raw=0;
 for(const item of value.files){
  exact(item,['path','district','bytes','roles','sha256','asset']);
  requireValue(relative(item.path)&&IDS.includes(item.district)&&item.path.startsWith(item.district+'/')&&integer(item.bytes,MAX_FILE)&&hash(item.sha256)&&Array.isArray(item.roles)&&item.roles.length>0&&new Set(item.roles).size===item.roles.length&&item.roles.every(role=>['runtime_dataset','snapshot_or_cache','supplemental','source_csv'].includes(role))&&!files.has(item.path.toLowerCase()));
  files.set(item.path.toLowerCase(),item);raw+=item.bytes;
 }
 requireValue(raw===value.dataset_input_bytes);
 for(const id of IDS){
  const matches=value.dataset_assets.filter(item=>item?.name==='terrier-testsets-'+id+'-'+releaseTag.slice('district-testsets-'.length)+'.zip');
  requireValue(matches.length===1);const asset=matches[0];
  exact(asset,['name','bytes','sha256','members','raw_bytes','member_sha256_verified','zip_crc_verified','download_url']);
  const members=value.files.filter(item=>item.district===id);
  requireValue(integer(asset.bytes,MAX_ARCHIVE)&&asset.bytes>0&&hash(asset.sha256)&&asset.members===members.length&&asset.raw_bytes===members.reduce((sum,item)=>sum+item.bytes,0)&&asset.member_sha256_verified===true&&asset.zip_crc_verified===true&&asset.download_url===assetURL(releaseTag,asset.name)&&members.every(item=>item.asset===asset.name));
  assets.set(id,asset);
 }
 exact(value.runtime_roles,IDS);
 const runtime={};
 for(const id of IDS){
  exact(value.runtime_roles[id],ROLES);runtime[id]=[];
  for(const role of ROLES){const item=value.runtime_roles[id][role];exact(item,['path','bytes','sha256','asset']);const row=files.get((id+'/data/'+role).toLowerCase());
   requireValue(item.path===id+'/data/'+role&&row?.path===item.path&&row.roles.includes('runtime_dataset')&&item.bytes>0&&row.bytes===item.bytes&&row.sha256===item.sha256&&row.asset===item.asset);
   runtime[id].push({role,path:item.path,bytes:item.bytes,sha256:item.sha256});
  }
  requireValue(runtime[id].reduce((sum,item)=>sum+item.bytes,0)<=12*1024**3);
 }
 requireValue(value.files.filter(item=>item.roles.includes('runtime_dataset')).length===18);
 return {value,runtime,assets,bytes:Buffer.from(bytes),sha256:checked.value.manifest.sha256};
}
function authorized(configuration,authorization){
 const {accepted,owner,principal}=authorization||{},grant=accepted?.grant,district=configuration?.district;
 return Boolean(configuration?.terrierLegacy&&IDS.includes(district)&&accepted?.revoked===false&&grant?.appId==='terrier'&&Array.isArray(grant.districtIds)&&grant.districtIds.includes(district)&&owner?.mode==='existing-owner'&&owner.appId==='terrier'&&owner.grantId===grant.grantId&&owner.accountIdentity===grant.accountIdentity&&owner.dataBindings?.some(item=>item.districtId===district)&&principal?.mode==='existing-owner'&&principal.districtId===district&&['serverId','deviceId','principalId','accountIdentity'].every(key=>typeof grant[key]==='string'&&principal[key]===grant[key]));
}
module.exports={REPOSITORY,POINTER_PATH,IDS,ROLES,COMMIT,TAG,MAX_MANIFEST,MAX_FILE,MAX_ARCHIVE,assetURL,pointer,manifest,authorized,requireValue};
