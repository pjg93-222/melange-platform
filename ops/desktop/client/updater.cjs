'use strict';
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const crypto=require('node:crypto');
const semver=require('semver');
const {verifyWindowsPackage}=require('./native-signature.cjs');
const {jsonRequest,verifyEnvelope,applyKeyTransitions,downloadVerified,verifyPackageFile}=require('./trust.cjs');
const VERIFY_RETRY_MS=6*60*60*1000;
// Launch-time installs wait for the signature tool (up to 20 s). After this many 'no verdict' starts for the same
// staged release, launch install is skipped and the update installs when the app closes instead.
const MAX_LAUNCH_UNAVAILABLE=3;
const envelopeHash=envelope=>crypto.createHash('sha256').update(Buffer.from(envelope.payload,'base64')).digest('hex');
function verifiedFeed(manifest,filename){
  const nonce=crypto.randomBytes(32).toString('hex'),pkg=manifest.package;
  const yaml='version: '+JSON.stringify(manifest.version)+'\nfiles:\n  - url: '+JSON.stringify(pkg.name)+'\n    sha512: '+JSON.stringify(pkg.sha512)+'\n    size: '+pkg.size+'\npath: '+JSON.stringify(pkg.name)+'\nsha512: '+JSON.stringify(pkg.sha512)+'\nreleaseDate: '+JSON.stringify(manifest.issuedAt)+'\n';
  const server=http.createServer((request,response)=>{
    const address=server.address();
    if(request.headers.host!=='127.0.0.1:'+address.port||!['GET','HEAD'].includes(request.method)){response.writeHead(403).end();return;}
    let pathname;try{pathname=new URL(request.url,'http://localhost').pathname;}catch{response.writeHead(400).end();return;}
    const prefix='/'+nonce+'/';
    if(pathname===prefix+'latest.yml'||pathname===prefix+'latest-mac.yml') {response.writeHead(200,{'Content-Type':'text/yaml','Cache-Control':'no-store'});response.end(request.method==='HEAD'?'':yaml);return;}
    if(pathname!==prefix+pkg.name){response.writeHead(404).end();return;}
    response.writeHead(200,{'Content-Type':'application/octet-stream','Content-Length':pkg.size,'Cache-Control':'no-store'});
    if(request.method==='HEAD')response.end();else {const stream=fs.createReadStream(filename);stream.on('error',()=>response.destroy());stream.pipe(response);}
  });
  return new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',()=>resolve({url:'http://127.0.0.1:'+server.address().port+'/'+nonce+'/',close:()=>server.close()}));});
}
class DesktopUpdater {
  constructor(options) {
    Object.assign(this,options);
    this.platform=options.platform||process.platform;this.arch=options.arch||process.arch;
    this.request=options.request||jsonRequest;this.download=options.download||downloadVerified;
    this.verifyNativePackage=options.verifyNativePackage||verifyWindowsPackage;
    this.makeFeed=options.makeFeed||verifiedFeed;this.busy=false;this.feed=null;this.ready=null;
    this.autoUpdater.autoDownload=false;this.autoUpdater.autoInstallOnAppQuit=false;
    this.autoUpdater.allowDowngrade=false;this.autoUpdater.disableDifferentialDownload=true;
    this.autoUpdater.disableWebInstaller=true;
  }
  identity(){return {appId:this.appId,version:this.version,platform:this.platform,arch:this.arch};}
  packagePath(manifest){return path.join(this.userData,'updates',String(manifest.sequence),manifest.package.name);}
  async report(enrollment,status,errorCode){
    const body={appId:this.appId,version:this.version,status,...(errorCode?{errorCode}:{})};
    this.writeState({...this.readState(),outbox:body});
    try{await this.request(enrollment,'/v1/status',{method:'POST',token:enrollment.deviceToken,body});
      const latest=this.readState();if(JSON.stringify(latest.outbox)===JSON.stringify(body))this.writeState({...latest,outbox:null});
    }catch{} // Keep the last bounded status until the administrator becomes reachable.
  }
  async nativeTrust(){
    // Isolated acceptance profiles never download native installers, stage them in the
    // shared native updater cache, or activate them into the installed program directory.
    if(this.nativeInstallation===false)throw new Error('Native installation is disabled in the isolated test profile.');
    if(!this.packaged)throw new Error('Native installation is required before updates can be installed.');
    if(this.platform==='win32'){
      // electron-updater 6.8.9 silently skips Authenticode verification without publisherName.
      const config=await this.autoUpdater.configOnDisk.value;
      const publishers=Array.isArray(config.publisherName)?config.publisherName:[config.publisherName];
      if(!this.publisherName||publishers.length!==1||publishers[0]!==this.publisherName)throw new Error('Trusted Windows publisher is not configured; automatic installation is disabled.');
    }
  }
  recoveryCandidate(state=this.readState()){
    const candidate=state.installed&&state.installed.version!==this.version?state.installed:state.previous;
    if(!candidate?.envelope||candidate.version===this.version)throw new Error('No previous authorized installer is available for this installation.');
    return candidate;
  }
  async verifyNativeFile(filename,manifest){
    await verifyPackageFile(filename,manifest);await this.nativeTrust();
    if(this.platform!=='win32')throw new Error('This release supports verified Windows activation only.');
    await this.verifyNativePackage(filename,this.publisherName);
    await verifyPackageFile(filename,manifest);
  }
  async ensureRecoveryBaseline(enrollment,fetch=true){
    let state=this.readState(),candidate=state.installed;
    if(candidate?.version!==this.version){
      if(!fetch)throw new Error('A verified installer for the current version is required before the first update.');
      const query=new URLSearchParams({platform:this.platform,arch:this.arch,version:this.version});
      const envelope=await this.request(enrollment,'/v1/apps/'+this.appId+'/recovery?'+query,{token:enrollment.deviceToken});
      if(!envelope)throw new Error('Administrator approval of this current version’s recovery installer is required before updating.');
      const verified=verifyEnvelope(envelope,enrollment,this.identity(),{}),m=verified.manifest;
      if(m.version!==this.version)throw new Error('Recovery seed must exactly match the running version.');
      if(m.sequence===state.sequence&&state.manifestHash!==verified.manifestHash)throw new Error('Recovery seed conflicts with accepted release identity.');
      await this.nativeTrust();
      // Only the signed manifest is recorded: the running version's own installer is not downloaded again.
      // beginRecovery() fetches and fully verifies an installer when a rollback to it is actually requested.
      candidate={envelope,version:m.version,phase:'installed'};state=this.readState();
      const highWater=m.sequence>(state.sequence||0)?{sequence:m.sequence,manifestHash:verified.manifestHash}:{};
      this.writeState({...state,...highWater,acceptedVersion:state.acceptedVersion&&semver.gt(state.acceptedVersion,m.version)?state.acceptedVersion:m.version,installed:candidate});
    }
    const verified=verifyEnvelope(candidate.envelope,enrollment,this.identity(),{});
    if(verified.manifest.version!==this.version)throw new Error('Recovery baseline version mismatch.');
    return candidate;
  }
  verifyRecovery(enrollment,recovery){
    // The only downgrade exception is this exact, locally selected, previously installed envelope.
    const expected=crypto.createHash('sha256').update(Buffer.from(recovery.target.envelope.payload,'base64')).digest('hex');
    if(expected!==recovery.manifestHash)throw new Error('Recovery authorization changed');
    const verified=verifyEnvelope(recovery.target.envelope,enrollment,{...this.identity(),version:recovery.target.version},{});
    if(verified.manifest.version!==recovery.target.version)throw new Error('Recovery version differs from authorization');
    return verified;
  }
  packageOf(item){const m=JSON.parse(Buffer.from(item.envelope.payload,'base64'));return {sequence:String(m.sequence),path:this.packagePath(m),manifest:m};}
  // Rollback baselines: only the installed and the immediately previous version keep an installer (plus any
  // in-flight pending or rollback target). Called once a new version is confirmed running.
  pruneBaselines(state=this.readState()){
    const live=state.recovery&&!['completed','failed'].includes(state.recovery.phase)?[state.recovery.target]:[];
    const items=[state.installed,state.previous,state.pending,...live].filter(item=>item?.envelope);
    const sequences=new Set(),versions=new Set([state.installed,state.previous].filter(Boolean).map(item=>item.version));
    for(const item of items)try{sequences.add(this.packageOf(item).sequence);}catch{return;}
    const sweep=(root,keep)=>{try{for(const name of fs.readdirSync(root))if(!keep.has(name))fs.rmSync(path.join(root,name),{recursive:true,force:true});}catch{}};
    sweep(path.join(this.userData,'updates'),sequences);if(this.baselineRoot)sweep(this.baselineRoot,versions);
  }
  // The first installer's own copy (placed by installer.nsh) is COPIED to the rollback installer location, without any
  // download. The baseline itself stays until the rollback has completed and the target is confirmed running.
  // Returns the baseline source path, or null.
  adoptBaseline(manifest){
    if(!this.baselineRoot)return null;
    const directory=path.join(this.baselineRoot,manifest.version),target=this.packagePath(manifest),part=target+'.part';
    try{
      const names=fs.readdirSync(directory).filter(name=>/\.exe$/i.test(name)).sort((a,b)=>(b===manifest.package.name)-(a===manifest.package.name));
      if(!names.length)return null;
      const source=path.join(directory,names[0]);fs.mkdirSync(path.dirname(target),{recursive:true,mode:0o700});
      fs.copyFileSync(source,part);fs.renameSync(part,target);return source;
    }catch{try{fs.rmSync(part,{force:true});}catch{}return null;}
  }
  async prepare(enrollment,pending,recovery=null){
    const verified=recovery?this.verifyRecovery(enrollment,recovery):verifyEnvelope(pending.envelope,enrollment,this.identity(),this.readState()),m=verified.manifest;
    await this.verifyNativeFile(this.packagePath(m),m);
    if(this.ready?.manifestHash===verified.manifestHash)return;
    this.feed?.close();this.feed=await this.makeFeed(m,this.packagePath(m));
    this.autoUpdater.allowDowngrade=Boolean(recovery);
    this.autoUpdater.setFeedURL({provider:'generic',url:this.feed.url,useMultipleRangeRequest:false});
    const result=await this.autoUpdater.checkForUpdates();
    if(!result||result.updateInfo.version!==m.version||result.updateInfo.files.length!==1||!result.updateInfo.files.some(f=>f.sha512===m.package.sha512&&f.size===m.package.size))throw new Error('Native updater metadata differs from signed manifest');
    const nativeFiles=await this.autoUpdater.downloadUpdate();
    // Windows returns its actual cached installer. Verify those bytes again before activation.
    if(this.platform==='win32'){
      if(!Array.isArray(nativeFiles)||nativeFiles.length!==1)throw new Error('Native updater did not stage one installer');
      await this.verifyNativeFile(nativeFiles[0],m);
    }
    const latest=this.readState();
    this.writeState(recovery?{...latest,recovery:{...recovery,phase:'prepared',nativeFiles:nativeFiles||[]}}:{...latest,pending:{...pending,phase:'prepared',nativeFiles:nativeFiles||[]}});
    this.ready={...verified,nativeFiles:nativeFiles||[],recovery:Boolean(recovery)};
  }
  async preparePending(enrollment,pending){
    // A staged installer that is no longer cached (for example the updater file root moved) is simply fetched
    // again by the next check; it is not a failure of the signed release and starts no backoff.
    if(!fs.existsSync(this.packageOf(pending).path)){this.writeState({...this.readState(),pending:null});throw new Error('The staged installer is no longer cached and will be fetched again.');}
    // Environment and current-version baseline refusals are not failures of this signed release.
    await this.nativeTrust();await this.ensureRecoveryBaseline(enrollment,false);
    try{await this.prepare(enrollment,pending);}
    catch(error){
      this.ready=null;
      // The signature tool reached no verdict: keep the staged file and retry it locally, without backoff.
      // Report the condition once per staged manifest, as a status and code only.
      if(error.code==='SIGNATURE_CHECK_UNAVAILABLE'){
        const manifestHash=envelopeHash(pending.envelope),latest=this.readState();
        if(latest.signatureUnavailableReported!==manifestHash){this.writeState({...latest,signatureUnavailableReported:manifestHash});await this.report(enrollment,'deferred','SIGNATURE_CHECK_UNAVAILABLE');}
        throw error;
      }
      // Drop the candidate and back off from this exact signed manifest. The sequence and version
      // high-water is untouched; only a fresh download of a manifest passing every check can stage.
      this.writeState({...this.readState(),pending:null,verifyFailure:{manifestHash:envelopeHash(pending.envelope),failedAt:Date.now()}});
      await this.report(enrollment,'failed','VERIFY_FAILED');throw error;
    }
  }
  async prepareRecovery(enrollment,recovery){
    // A rollback whose retained installer no longer verifies is marked failed, so it can never
    // activate and no longer blocks normal update checks. The user may request it again.
    try{await this.prepare(enrollment,recovery.target,recovery);}
    catch(error){this.ready=null;
      // The cached installer is deleted ONLY when its own bytes fail the size/hash check (re-checked here), so the next
      // attempt fetches a fresh copy. An untrusted or refused signature, a native-updater or feed error, a trust
      // configuration error, or "no verdict" (signature tool unavailable) keeps it: its bytes are not at fault.
      if(error.code==='PACKAGE_INTEGRITY')try{const {path:file,manifest}=this.packageOf(recovery.target);await verifyPackageFile(file,manifest).catch(failure=>{if(failure.code==='PACKAGE_INTEGRITY')fs.rmSync(file,{recursive:true,force:true});});}catch{}
      const latest=this.readState();this.writeState({...latest,recovery:{...latest.recovery,phase:'failed'}});await this.report(enrollment,'failed','RECOVERY_FAILED');throw error;}
  }
  async beginRecovery(){
    if(this.busy||this.osShutdown)throw new Error('Wait for the current update check to finish.');
    this.busy=true;
    try{
      const enrollment=this.loadEnrollment();if(!enrollment)throw new Error('Administrator enrollment is required to verify a recovery package.');
      const state=this.readState(),target=this.recoveryCandidate(state);
      const manifestHash=crypto.createHash('sha256').update(Buffer.from(target.envelope.payload,'base64')).digest('hex');
      const recovery={target,manifestHash,fromVersion:this.version,phase:'staged',requestedAt:new Date().toISOString()};
      const m=this.verifyRecovery(enrollment,recovery).manifest;this.ready=null;
      // Local installer first, so a rollback needs no network: one retained from an earlier update, or a copy of the
      // installer the first install kept. A copy failing the hash check is deleted (with its baseline source). Only a
      // missing or deleted copy is fetched now (hash-checked on download); prepare() then runs the full hash and
      // native-publisher checks.
      const cached=this.packagePath(m);
      // Only a size/hash mismatch (PACKAGE_INTEGRITY) condemns a copy; a transient read error (EBUSY/EPERM, antivirus) keeps it.
      // Deleting retries a locked file (EBUSY/EPERM, e.g. an antivirus scan) for ~2.5 s without blocking, then gives up quietly.
      let corrupt=false;
      const drop=file=>fs.promises.rm(file,{recursive:true,force:true,maxRetries:4,retryDelay:250}).catch(()=>{});
      const intact=async()=>{try{await verifyPackageFile(cached,m);return true;}catch(error){corrupt=error.code==='PACKAGE_INTEGRITY';if(corrupt)await drop(cached);return false;}};
      let local=fs.existsSync(cached)&&await intact();
      if(!local){const source=this.adoptBaseline(m);if(source){local=await intact();if(!local&&corrupt)await drop(source);}}
      // A corrupt copy that is still locked cannot be replaced at this path, so a download could not land: stop before fetching.
      if(!local&&corrupt&&fs.existsSync(cached))throw new Error('The saved installer for version '+target.version+' is in use by another program. Close it and try again.');
      if(!local){await this.nativeTrust();await this.download(enrollment,enrollment.deviceToken,m,path.dirname(cached));}
      // A requested rollback beats any other pending update, which must not resurface after it. The running
      // version's own awaiting-startup record is kept.
      const latest=this.readState();
      this.writeState({...latest,recovery,...(latest.pending&&latest.pending.version!==this.version?{pending:null}:{})});await this.prepareRecovery(enrollment,recovery);
      this.onStatus('Previous authorized version '+target.version+' is verified. Close this app to restore its program files; local data stays in place.');
      return target.version;
    }finally{this.busy=false;}
  }
  async recover(enrollment){
    let state=this.readState();const recovery=state.recovery;
    if(recovery&&!['completed','failed'].includes(recovery.phase)){
      this.verifyRecovery(enrollment,recovery);
      if(recovery.target.version===this.version){
        if(this.applicationReady){
          this.writeState({...state,installed:recovery.target,pending:null,previous:null,recovery:{...recovery,phase:'completed'},suspendedVersions:[...new Set([...(state.suspendedVersions||[]),recovery.fromVersion])]});
          this.pruneBaselines();
          // The rolled-back-to version is confirmed running and its installer now lives with the retained ones.
          if(this.baselineRoot)try{fs.rmSync(path.join(this.baselineRoot,recovery.target.version),{recursive:true,force:true});}catch{}
          await this.report(enrollment,'rolled-back');
        }
        return;
      }
      if(recovery.phase==='activating'){
        this.writeState({...state,recovery:{...recovery,phase:'failed'}});
        await this.report(enrollment,'failed','RECOVERY_FAILED');return;
      }
      try{await this.prepareRecovery(enrollment,recovery);}catch(error){const notice='Previous version '+recovery.target.version+' failed verification and was not restored: '+error.message;this.onStatus(notice);return notice;}
      return;
    }
    const pending=state.pending;
    if(!pending)return;
    if(pending.version===this.version){
      if(pending.phase==='failed'){this.onStatus('This release failed local startup. Its data is retained; restore a previous authorized version or await a corrected release.');return;}
      // An installer found only at the pre-move cache path (legacyUserData) proves that this release was installed from the old
      // updater file root, so the running version confirms it with no readiness wait (a background-only run never starts a
      // backend). Otherwise a version string alone does not prove that its backend can start; a copy that is gone everywhere
      // is not verified, but still waits for a successful local startup.
      const {path:cached,manifest}=this.packageOf(pending),legacy=this.legacyUserData&&path.join(this.legacyUserData,'updates',String(manifest.sequence),manifest.package.name);
      const installer=[cached,legacy].find(file=>file&&fs.existsSync(file));
      if(!this.applicationReady&&installer!==legacy){this.onStatus('Updated app awaits a successful local startup.');return;}
      const verified=verifyEnvelope(pending.envelope,enrollment,this.identity(),state);
      if(installer)await this.verifyNativeFile(installer,verified.manifest);
      this.writeState({...state,previous:state.installed||state.previous||null,installed:{...pending,phase:'installed'},pending:null,installedVersion:this.version});
      // Only now, with pending cleared, keep the verified legacy copy at the current path too, so a later offline rollback to
      // this release finds it; a pending release is therefore never at both paths. Every later use re-verifies the copy, and a
      // failed copy only means that rollback downloads the installer.
      if(installer===legacy){const part=cached+'.part';try{fs.mkdirSync(path.dirname(cached),{recursive:true,mode:0o700});fs.copyFileSync(legacy,part);fs.renameSync(part,cached);}catch{try{fs.rmSync(part,{force:true});}catch{}}}
      this.pruneBaselines();
      await this.report(enrollment,'installed');return;
    }
    if(pending.phase==='activating'){
      this.writeState({...state,pending:{...pending,phase:'failed'}});
      await this.report(enrollment,'failed','INSTALL_FAILED');
      this.onStatus('Previous installation did not complete. Local data retained; a corrected release is required.');return;
    }
    if(pending.phase==='failed')return;
    // A staged candidate that fails reverification must not block discovery of a newer
    // signed release. The sequence/version high-water persisted before download is
    // untouched, so a later offer must still pass every rollback and downgrade check.
    try{await this.preparePending(enrollment,pending);}
    catch(error){this.ready=null;const notice='Staged update '+pending.version+' was not prepared: '+error.message;this.onStatus(notice);return notice;}
    this.onStatus('Verified update '+pending.version+' waits for this app to close.');
  }
  async check(){
    if(this.busy||this.osShutdown)return;this.busy=true;
    let enrollment;
    try {
      enrollment=this.loadEnrollment();if(!enrollment){this.onStatus('Update receiver awaits administrator approval and activation.');return;}
      const notice=await this.recover(enrollment);
      if(this.readState().pending?.version===this.version)return;
      if(this.ready?.recovery||['staged','prepared','activating'].includes(this.readState().recovery?.phase))return;
      const trust=await this.request(enrollment,'/v1/apps/'+this.appId+'/trust',{token:enrollment.deviceToken});
      enrollment=applyKeyTransitions(enrollment,trust.transitions);this.saveEnrollment(enrollment);
      const outbox=this.readState().outbox;if(outbox)await this.report(enrollment,outbox.status,outbox.errorCode);
      await this.ensureRecoveryBaseline(enrollment);
      const query=new URLSearchParams({platform:this.platform,arch:this.arch,version:this.version});
      const envelope=await this.request(enrollment,'/v1/apps/'+this.appId+'/updates?'+query,{token:enrollment.deviceToken});
      if(!envelope){if(!this.ready&&!notice)this.onStatus('No approved update.');return;}
      const previous=this.readState(),verified=verifyEnvelope(envelope,enrollment,this.identity(),previous),m=verified.manifest;
      if(m.version===this.version){this.onStatus('Already current.');return;}
      if(previous.suspendedVersions?.includes(m.version)){this.onStatus('Recovered release remains suspended. Awaiting a corrected release.');return;}
      if(previous.pending?.phase==='failed'&&previous.pending.version===m.version){this.onStatus('Failed release remains suspended. Awaiting a corrected release.');return;}
      if(this.ready?.manifestHash===verified.manifestHash)return;
      // Still staged after recover(): its local retry is pending, so do not download it again.
      if(['staged','prepared'].includes(previous.pending?.phase)&&envelopeHash(previous.pending.envelope)===verified.manifestHash)return;
      const failure=previous.verifyFailure,failedAt=failure?.failedAt,age=Date.now()-failedAt;
      if(failure?.manifestHash===verified.manifestHash&&typeof failedAt==='number'&&Number.isFinite(failedAt)&&age>=0&&age<VERIFY_RETRY_MS){this.onStatus('Update '+m.version+' failed verification; retry after '+new Date(failure.failedAt+VERIFY_RETRY_MS).toISOString()+'.');return;}
      await this.nativeTrust();
      // Remember accepted sequence before downloading. A failed transfer cannot allow rollback.
      this.ready=null;this.feed?.close();this.feed=null;
      this.writeState({...previous,sequence:m.sequence,manifestHash:verified.manifestHash,acceptedVersion:m.version,pending:null});
      this.onStatus('Downloading verified update '+m.version+'…');
      await this.download(enrollment,enrollment.deviceToken,m,path.dirname(this.packagePath(m)));
      const pending={envelope,version:m.version,fromVersion:this.version,phase:'staged',createdAt:new Date().toISOString()};
      this.writeState({...this.readState(),pending});await this.preparePending(enrollment,pending);
      this.onStatus('Update '+m.version+' verified. Installs after you close this app.');
      await this.report(enrollment,'downloaded');
    }catch(error){this.onStatus(this.ready?'Update check deferred. Verified update remains staged; local app remains available.':'Update deferred: '+error.message);
    }finally{this.busy=false;}
  }
  markApplicationReady(){this.applicationReady=true;}
  async markApplicationFailed(){
    this.applicationReady=false;this.ready=null;
    const state=this.readState();
    if(state.recovery?.target.version===this.version&&!['completed','failed'].includes(state.recovery.phase))this.writeState({...state,recovery:{...state.recovery,phase:'failed'}});
    else if(state.pending?.version===this.version)this.writeState({...state,pending:{...state.pending,phase:'failed'}});
    const enrollment=this.loadEnrollment();if(enrollment)await this.report(enrollment,'failed','APP_STARTUP_FAILED');
  }
  async nativeFailure(){
    const state=this.readState();
    if(state.recovery?.phase==='activating')this.writeState({...state,recovery:{...state.recovery,phase:'failed'}});
    else if(state.pending?.phase==='activating')this.writeState({...state,pending:{...state.pending,phase:'failed'}});
    else return;
    const enrollment=this.loadEnrollment();if(enrollment)await this.report(enrollment,'failed','INSTALL_FAILED');
  }
  // A verified update left pending by an earlier session (OS shutdown, a close during a check) installs
  // silently at the next start, before any window or backend exists, with every check of a normal close.
  launchCandidate(){
    try{
      const state=this.readState(),pending=state.pending;
      if(this.osShutdown||this.busy||!pending||!['staged','prepared'].includes(pending.phase)||pending.version===this.version)return null;
      // A requested rollback always beats a pending update.
      if(['staged','prepared','activating'].includes(state.recovery?.phase))return null;
      const strikes=state.launchUnavailable;
      if(strikes?.manifestHash===envelopeHash(pending.envelope)&&strikes.count>=MAX_LAUNCH_UNAVAILABLE)return null;
      const enrollment=this.loadEnrollment();return enrollment?{pending,enrollment}:null;
    }catch{return null;}
  }
  // Counts a launch-time failure against this staged manifest; at MAX_LAUNCH_UNAVAILABLE launchCandidate() stops offering it.
  strikeLaunch(pending){
    const latest=this.readState(),manifestHash=envelopeHash(pending.envelope),count=latest.launchUnavailable?.manifestHash===manifestHash?latest.launchUnavailable.count:0;
    this.writeState({...latest,launchUnavailable:{manifestHash,count:count+1}});
  }
  // relaunch may be a function, read only after preparation (a launch requested while preparing counts).
  // Every failure to reach the installer (preparation, nativeTrust, baseline, activation checks, signature tool without
  // a verdict) is a strike; strikes reset only once quitAndInstall was actually dispatched.
  async activateAtLaunch(relaunch){
    const candidate=this.launchCandidate();if(!candidate)return false;
    this.busy=true;
    try{await this.preparePending(candidate.enrollment,candidate.pending);}
    catch{this.ready=null;this.strikeLaunch(candidate.pending);return false;}
    finally{this.busy=false;}
    const dispatched=await this.activateAfterClose(relaunch);
    if(dispatched){const latest=this.readState();if(latest.launchUnavailable)this.writeState({...latest,launchUnavailable:null});}
    else if(this.activationFailure)this.strikeLaunch(candidate.pending);
    return dispatched;
  }
  async activateAfterClose(relaunch=false){
    this.activationFailure=null;
    if(this.osShutdown||this.busy||!this.ready)return false;
    try{
      const enrollment=this.loadEnrollment(),state=this.readState();
      const recovery=this.ready.recovery?state.recovery:null;
      if(recovery?recovery.phase!=='prepared':!state.pending||state.pending.phase!=='prepared')return false;
      const verified=recovery?this.verifyRecovery(enrollment,recovery):verifyEnvelope(state.pending.envelope,enrollment,this.identity(),state);
      if(verified.manifestHash!==this.ready.manifestHash)throw new Error('Staged update identity changed');
      if(!recovery)await this.ensureRecoveryBaseline(enrollment,false);
      await this.verifyNativeFile(this.packagePath(verified.manifest),verified.manifest);
      for(const filename of this.ready.nativeFiles)await this.verifyNativeFile(filename,verified.manifest);
      if(this.osShutdown)return false;
      const transaction={...(recovery||state.pending),phase:'activating',activatedAt:new Date().toISOString()};
      this.writeState(recovery?{...state,recovery:transaction}:{...state,pending:transaction});
      // Silent NSIS (/S): no update-caused prompt. electron-updater 6.8.9 resets quitAndInstallCalled only when
      // install() fails synchronously (no installer path, doInstall threw). NsisUpdater.doInstall otherwise returns
      // true right after an asynchronous spawn, and quitAndInstall then schedules app.quit() regardless. A spawn
      // failure then surfaces later as the autoUpdater 'error' event, but only for errors other than EACCES/UNKNOWN
      // (retried through elevate.exe, which can show a UAC prompt; rare for this per-user install) and ENOENT (opened
      // with shell.openPath, i.e. the installer runs interactively without /S); neither emits 'error'. In the
      // 'error' case the app exits once without installing, and nativeFailure() (or recover() at the next start)
      // marks the transaction failed with INSTALL_FAILED.
      const run=Boolean(typeof relaunch==='function'?relaunch():relaunch);
      this.autoUpdater.autoRunAppAfterInstall=run;
      this.autoUpdater.quitAndInstall(true,run);
      if(this.autoUpdater.quitAndInstallCalled===false)throw new Error('The installer could not be started.');
      return true;
    }catch(error){this.activationFailure=error;this.onStatus('Update activation refused: '+error.message);return false;}
  }
  suspendInstallation(){this.osShutdown=true;this.autoUpdater.autoInstallOnAppQuit=false;}
  close(){this.feed?.close();this.feed=null;}
}
module.exports={DesktopUpdater,verifiedFeed};
