'use strict';
// Parameters: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt
// Async/limits: https://nodejs.org/api/crypto.html#cryptoscryptpassword-salt-keylen-options-callback
const crypto=require('node:crypto');
const {promisify}=require('node:util');
const scrypt=promisify(crypto.scrypt);
const OPTIONS=Object.freeze({N:2**17,r:8,p:1,maxmem:192*1024*1024});
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DISTRICTS=new Set(['co-05','or-05','ct-01']);
let deriving=false;

function binding(value){
  if(!value||!['melange','mentat','terrier'].includes(value.appId))throw new Error('Invalid local account binding');
  const result={};
  for(const name of ['serverId','deviceId','principalId']){
    if(typeof value[name]!=='string'||!UUID.test(value[name])||value[name]==='00000000-0000-0000-0000-000000000000')throw new Error('Invalid local account binding');
    result[name]=value[name];
  }
  if(typeof value.accountIdentity!=='string'||!/^[A-Za-z0-9_@.+-]{1,150}$/.test(value.accountIdentity))throw new Error('Invalid local account identity');
  if(!Array.isArray(value.districtIds)||value.districtIds.length>3||new Set(value.districtIds).size!==value.districtIds.length||value.districtIds.some(d=>!DISTRICTS.has(d)))throw new Error('Invalid district scope');
  if(value.appId==='terrier'?(value.role!=='local_administrator'||!value.districtIds.length):(value.role!=='local_user'||value.districtIds.length))throw new Error('Invalid account role');
  return {...result,appId:value.appId,accountIdentity:value.accountIdentity,role:value.role,districtIds:[...value.districtIds].sort()};
}
function stableIdentity(value){const {role,districtIds,...stable}=binding(value);return stable;}
// One-time device verification. After the first successful sign-in a record bound to this approval (not to the
// password) is kept in protected storage; later launches open without a prompt until the computer is forgotten,
// the administrator replaces the approval (new grantId), or the record is missing, damaged or unreadable.
function trustBinding(value){
  const {role,districtIds,...stable}=binding(value);
  if(typeof value.grantId!=='string'||!UUID.test(value.grantId))throw new Error('Invalid local account binding');
  return {...stable,grantId:value.grantId};
}
function checkedTrust(record,expected){
  if(!record||typeof record!=='object'||Object.keys(record).sort().join('|')!=='binding|schemaVersion|verifiedAt'||record.schemaVersion!==1||!Number.isSafeInteger(record.verifiedAt)||record.verifiedAt<0)throw new Error('Invalid protected device record');
  const want=trustBinding(expected),have=record.binding;
  if(!have||typeof have!=='object'||Object.keys(have).length!==Object.keys(want).length||Object.keys(want).some(key=>have[key]!==want[key]))throw new Error('This computer was verified for a different approval');
  return record;
}
function passwordBytes(value){
  if(typeof value!=='string'||Array.from(value).length<12||Buffer.byteLength(value,'utf8')>1024||value.includes('\0'))throw new Error('Use a password of at least 12 characters and at most 1024 UTF-8 bytes.');
  return Buffer.from(value,'utf8');
}
function decode(value,length){
  if(typeof value!=='string'||value.length!==4*Math.ceil(length/3))throw new Error('Invalid protected password record');
  const bytes=Buffer.from(value,'base64');
  if(bytes.length!==length||bytes.toString('base64')!==value)throw new Error('Invalid protected password record');
  return bytes;
}
function checkedRecord(record,expected){
  const keys=['schemaVersion','binding','algorithm','N','r','p','salt','derivedKey','failures','blockedUntil','lastAttempt'];
  if(!record||typeof record!=='object'||Object.keys(record).sort().join('|')!==keys.sort().join('|')||record.schemaVersion!==1||record.algorithm!=='scrypt'||record.N!==OPTIONS.N||record.r!==8||record.p!==1)throw new Error('Invalid protected password record');
  if(JSON.stringify(stableIdentity(record.binding))!==JSON.stringify(stableIdentity(expected)))throw new Error('Password belongs to a different approved account');
  decode(record.salt,32);decode(record.derivedKey,64);
  if(!Number.isSafeInteger(record.failures)||record.failures<0||record.failures>1000000||!Number.isSafeInteger(record.blockedUntil)||record.blockedUntil<0||!Number.isSafeInteger(record.lastAttempt)||record.lastAttempt<0)throw new Error('Invalid protected password record');
  return record;
}
async function derive(password,salt){
  if(deriving)throw new Error('Another password check is in progress');
  const bytes=passwordBytes(password);deriving=true;
  try{return await scrypt(bytes,salt,64,OPTIONS);}
  finally{bytes.fill(0);deriving=false;}
}
class LocalAccess{
  // store is a dedicated Credential Manager namespace, never an update-token store.
  // devices is optional: without it every launch asks for the password, as before.
  constructor({store,devices=null,now=Date.now}){if(!store?.read||!store?.write)throw new Error('Protected account store required');if(devices&&!(devices.read&&devices.write&&devices.delete))throw new Error('Protected device store required');this.store=store;this.devices=devices;this.now=now;this.session=null;this.busy=false;this.verifiedKey=null;this.epoch=0;this.forgetting=0;}
  async create(approved,password){
    const identity=binding(approved);
    if(identity.appId==='terrier')throw new Error('Terrier passwords are managed by its existing sign-in system');
    if(this.busy)throw new Error('Another local account operation is in progress');this.busy=true;
    try{
      if(await this.store.read(identity))throw new Error('A local password already exists; sign in to that account');
      const salt=crypto.randomBytes(32);let key;
      try{
        key=await derive(password,salt);
        const record={schemaVersion:1,binding:identity,algorithm:'scrypt',N:OPTIONS.N,r:8,p:1,
          salt:salt.toString('base64'),derivedKey:key.toString('base64'),failures:0,blockedUntil:0,lastAttempt:0};
        await this.store.write(identity,record);
      }finally{key?.fill(0);salt.fill(0);}
      // Account creation never creates an authenticated session implicitly.
      return {created:true,accountIdentity:identity.accountIdentity};
    }finally{this.busy=false;}
  }
  async unlock(approved,password){
    const identity=binding(approved);
    if(identity.appId==='terrier')throw new Error('Use Terrier sign-in');
    if(this.busy)throw new Error('Another local account operation is in progress');this.busy=true;
    try{
      this.session=null;
      const record=checkedRecord(await this.store.read(identity),identity),now=this.now();
      if(!Number.isSafeInteger(now)||now<record.lastAttempt-60000)throw new Error('Local clock changed; password check refused');
      if(now<record.blockedUntil)throw new Error('Too many attempts. Try again later.');
      passwordBytes(password).fill(0);
      const failures=Math.min(record.failures+1,1000000);
      const delay=failures<5?0:Math.min(900000,5000*2**Math.min(failures-5,8));
      // Record the attempt before derivation, so restart/crash cannot reset throttling.
      await this.store.write(identity,{...record,failures,blockedUntil:now+delay,lastAttempt:now});
      let key;const expected=decode(record.derivedKey,64),salt=decode(record.salt,32);
      try{
        key=await derive(password,salt);
        if(!crypto.timingSafeEqual(key,expected))throw new Error('Incorrect password');
      }finally{key?.fill(0);expected.fill(0);salt.fill(0);}
      await this.store.write(identity,{...record,failures:0,blockedUntil:0,lastAttempt:now});
      this.session=Object.freeze({...identity,sessionId:crypto.randomUUID(),authenticatedAt:now,method:'local-password'});
      await this.remember(approved);
      return this.session;
    }finally{this.busy=false;}
  }
  // Best effort: if the record cannot be kept (for example an older helper without this namespace), the sign-in still
  // succeeds and the next launch simply asks for the password again.
  async remember(approved){
    if(!this.devices)return false;
    try{const identity=trustBinding(approved);if(identity.appId==='terrier')return false;await this.devices.write(identity,{schemaVersion:1,binding:identity,verifiedAt:this.now()});this.verifiedKey=JSON.stringify(identity);return true;}catch{return false;}
  }
  // The current session, or one re-established from this computer's device record. Null means the password is needed.
  async resume(approved){
    const current=this.current(approved);if(current)return current;
    if(!this.devices||this.forgetting)return null;   // a forget in progress: the record may still exist until its delete settles
    const epoch=this.epoch;
    try{
      const identity=trustBinding(approved);if(identity.appId==='terrier')return null;
      const key=JSON.stringify(identity);
      if(this.verifiedKey!==key){checkedTrust(await this.devices.read(identity),approved);if(epoch!==this.epoch)return null;this.verifiedKey=key;}
    }catch{return null;}
    if(epoch!==this.epoch)return null;
    this.session=Object.freeze({...binding(approved),sessionId:crypto.randomUUID(),authenticatedAt:this.now(),method:'device-trust'});
    return this.session;
  }
  // Ends the session and deletes this computer's device record; a delete failure is reported, never hidden.
  async forget(approved){
    this.epoch++;this.session=null;this.verifiedKey=null;
    if(!this.devices||!approved)return;
    this.forgetting++;   // epoch covers reads already in flight; this covers any resume that starts before the delete settles
    try{const identity=binding(approved);if(identity.appId!=='terrier')await this.devices.delete(identity);}
    finally{this.forgetting--;}
  }
  current(approved){if(!this.session||JSON.stringify(binding(this.session))!==JSON.stringify(binding(approved)))return null;return this.session;}
  // Ends the session and invalidates any resume still reading the device record, so a lock learned mid-read (revocation,
  // activation, suspend) cannot be undone by that read. The record itself is kept: only forget() deletes it.
  lock(){this.epoch++;this.session=null;}
}
module.exports={LocalAccess,binding,checkedRecord,OPTIONS};
