'use strict';
// Public discovery metadata only. Signature and native installer verification
// remain in the updater. SQLite provides cross-process locks released on crash.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {DatabaseSync}=require('node:sqlite');
const TTL=6*60*60*1000,JITTER=30*60*1000,MAX_DELAY=7*24*60*60*1000,MAX_BYTES=4*1024*1024;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function rateLimit(status,headers={},body=''){
 const limited=status===429||(status===403&&(String(headers['x-ratelimit-remaining'])==='0'||headers['retry-after']!==undefined||/rate limit|abuse detection/i.test(body)));
 if(!limited)return null;
 const error=new Error('GitHub API rate limit reached');error.githubRateLimit=true;
 error.retryAfter=headers['retry-after'];error.resetAt=headers['x-ratelimit-reset'];return error;
}
function deadline(error,now,random){
 const retry=String(error.retryAfter??'');let delay;
 if(/^\d+$/.test(retry))delay=Number(retry)*1000;
 else if(retry&&Number.isFinite(Date.parse(retry)))delay=Date.parse(retry)-now;
 const reset=String(error.resetAt??'');const resetDelay=/^\d+$/.test(reset)?Number(reset)*1000-now:NaN;
 const requested=Math.max(Number.isFinite(delay)?delay:0,Number.isFinite(resetDelay)?resetDelay:0);
 return now+Math.min(MAX_DELAY,Math.max(60*1000,requested||60*60*1000))+Math.floor(random()*60000);
}
class DiscoveryCache{
 constructor({directory,repository,validate,now=Date.now,random=Math.random,onStatus=()=>{},waitMs=2000}){
  this.repository=repository;this.validate=validate;this.now=now;this.random=random;this.onStatus=onStatus;this.waitMs=waitMs;
  this.directory=path.resolve(directory);fs.mkdirSync(this.directory,{recursive:true,mode:0o700});
  const id=crypto.createHash('sha256').update(repository).digest('hex');
  this.filename=path.join(this.directory,id+'.sqlite');this.cooldownFile=path.join(this.directory,id+'-cooldown.sqlite');
 }
 open(filename,sql){
  if(fs.existsSync(filename)){const stat=fs.lstatSync(filename);if(!stat.isFile()||stat.isSymbolicLink()||stat.size>MAX_BYTES*3)throw new Error('GitHub discovery cache is invalid');}
  const db=new DatabaseSync(filename);try{db.exec('PRAGMA busy_timeout=0; PRAGMA trusted_schema=OFF;');db.exec(sql);return db;}catch(error){db.close();throw error;}
 }
 cacheDB(){return this.open(this.filename,'CREATE TABLE IF NOT EXISTS discovery (id INTEGER PRIMARY KEY CHECK(id=1), repository TEXT NOT NULL, checked INTEGER NOT NULL, next INTEGER NOT NULL, payload TEXT NOT NULL)');}
 cooldownDB(){return this.open(this.cooldownFile,'CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY CHECK(id=1), repository TEXT NOT NULL, recorded INTEGER NOT NULL, until INTEGER NOT NULL)');}
 read(db){
  const row=db.prepare('SELECT repository,checked,next,payload FROM discovery WHERE id=1').get();if(!row)return null;
  const now=this.now();
  if(row.repository!==this.repository||!Number.isSafeInteger(row.checked)||row.checked<0||row.checked>now+300000||!Number.isSafeInteger(row.next)||row.next<row.checked+TTL||row.next>row.checked+TTL+JITTER||typeof row.payload!=='string'||Buffer.byteLength(row.payload)>MAX_BYTES)return null;
  try{return {checkedAt:row.checked,nextCheckAt:row.next,releases:this.validate(JSON.parse(row.payload))};}catch{return null;}
 }
 cooldown(){
  let db;try{db=this.cooldownDB();const row=db.prepare('SELECT repository,recorded,until FROM cooldown WHERE id=1').get();if(!row)return 0;
   if(row.repository!==this.repository||!Number.isSafeInteger(row.recorded)||!Number.isSafeInteger(row.until)||row.until<row.recorded||row.until>row.recorded+MAX_DELAY+60000)throw new Error('GitHub cooldown state is invalid');
   // Normalize a wall-clock rollback once, rather than extending a sliding
   // future deadline on every read until the old system date returns.
   const now=this.now();if(row.recorded>now+300000){const until=now+60*60*1000;db.prepare('UPDATE cooldown SET recorded=?,until=? WHERE id=1 AND recorded=? AND until=?').run(now,until,row.recorded,row.until);return until;}
   return row.until>now?row.until:0;
  }finally{db?.close();}
 }
 async permitted(){const until=this.cooldown();if(until){this.onStatus({backoffUntil:until});throw new Error('GitHub API paused until '+new Date(until).toISOString());}}
 async note(error){
  if(!error?.githubRateLimit)return;
  const now=this.now(),until=deadline(error,now,this.random);
  for(let attempt=0;attempt<20;attempt++){
   let db;try{db=this.cooldownDB();db.prepare('INSERT INTO cooldown(id,repository,recorded,until) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET repository=excluded.repository, recorded=CASE WHEN excluded.until>cooldown.until THEN excluded.recorded ELSE cooldown.recorded END, until=MAX(cooldown.until,excluded.until)').run(this.repository,now,until);this.onStatus({backoffUntil:until});return;}
   catch(failure){if(!/locked|busy/i.test(failure.message)||attempt===19)throw failure;}
   finally{db?.close();}
   await sleep(25);
  }
 }
 async request(operation){await this.permitted();try{return await operation();}catch(error){await this.note(error);throw error;}}
 result(cached,extra={}){this.onStatus({checkedAt:cached.checkedAt,nextCheckAt:cached.nextCheckAt,cached:true,...extra});return cached.releases;}
 async get(refresh){
  const started=Date.now();let db,locked=false,cached;
  try{
   db=this.cacheDB();cached=this.read(db);const until=this.cooldown();
   if(until){if(cached)return this.result(cached,{backoffUntil:until});this.onStatus({backoffUntil:until});throw new Error('GitHub API paused until '+new Date(until).toISOString());}
   if(cached&&this.now()<cached.nextCheckAt)return this.result(cached);
   while(!locked){
    try{db.exec('BEGIN IMMEDIATE');locked=true;}
    catch(error){if(!/locked|busy/i.test(error.message))throw error;
     if(Date.now()-started>=this.waitMs){if(cached)return this.result(cached,{refreshDeferred:true});throw new Error('GitHub release discovery is already refreshing; retry later');}
     await sleep(50);cached=this.read(db);if(cached&&this.now()<cached.nextCheckAt)return this.result(cached);
    }
   }
   // Another app may have finished just before this writer acquired the lock.
   cached=this.read(db);if(cached&&this.now()<cached.nextCheckAt)return this.result(cached);
   await this.permitted();
   const releases=this.validate(await refresh()),payload=JSON.stringify(releases);
   if(Buffer.byteLength(payload)>MAX_BYTES)throw new Error('GitHub discovery metadata exceeds cache limit');
   const checkedAt=this.now(),nextCheckAt=checkedAt+TTL+Math.floor(this.random()*JITTER);
   db.prepare('INSERT INTO discovery(id,repository,checked,next,payload) VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET repository=excluded.repository,checked=excluded.checked,next=excluded.next,payload=excluded.payload').run(this.repository,checkedAt,nextCheckAt,payload);
   db.exec('COMMIT');locked=false;this.onStatus({checkedAt,nextCheckAt,cached:false});return releases;
  }catch(error){await this.note(error);if(cached)return this.result(cached,{refreshDeferred:true,backoffUntil:this.cooldown()||undefined});throw error;}
  finally{if(locked)try{db.exec('ROLLBACK');}catch{}db?.close();}
 }
}
module.exports={DiscoveryCache,rateLimit,deadline,TTL,JITTER,MAX_DELAY,MAX_BYTES};
