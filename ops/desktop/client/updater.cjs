'use strict';
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const crypto=require('node:crypto');
const semver=require('semver');
const {verifyWindowsPackage}=require('./native-signature.cjs');
const {jsonRequest,verifyEnvelope,applyKeyTransitions,downloadVerified,verifyPackageFile}=require('./trust.cjs');
const VERIFY_RETRY_MS=6*60*60*1000;
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
      await this.download(enrollment,enrollment.deviceToken,m,path.dirname(this.packagePath(m)));
      await this.verifyNativeFile(this.packagePath(m),m);
      candidate={envelope,version:m.version,phase:'installed'};state=this.readState();
      const highWater=m.sequence>(state.sequence||0)?{sequence:m.sequence,manifestHash:verified.manifestHash}:{};
      this.writeState({...state,...highWater,acceptedVersion:state.acceptedVersion&&semver.gt(state.acceptedVersion,m.version)?state.acceptedVersion:m.version,installed:candidate});
    }
    const verified=verifyEnvelope(candidate.envelope,enrollment,this.identity(),{});
    if(verified.manifest.version!==this.version)throw new Error('Recovery baseline version mismatch.');
    await this.verifyNativeFile(this.packagePath(verified.manifest),verified.manifest);
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
    catch(error){this.ready=null;const latest=this.readState();this.writeState({...latest,recovery:{...latest.recovery,phase:'failed'}});await this.report(enrollment,'failed','RECOVERY_FAILED');throw error;}
  }
  async beginRecovery(){
    if(this.busy||this.osShutdown)throw new Error('Wait for the current update check to finish.');
    this.busy=true;
    try{
      const enrollment=this.loadEnrollment();if(!enrollment)throw new Error('Administrator enrollment is required to verify a recovery package.');
      const state=this.readState(),target=this.recoveryCandidate(state);
      const manifestHash=crypto.createHash('sha256').update(Buffer.from(target.envelope.payload,'base64')).digest('hex');
      const recovery={target,manifestHash,fromVersion:this.version,phase:'staged',requestedAt:new Date().toISOString()};
      this.verifyRecovery(enrollment,recovery);this.ready=null;
      this.writeState({...state,recovery});await this.prepareRecovery(enrollment,recovery);
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
      // A version string alone does not prove that its backend can start.
      if(!this.applicationReady){this.onStatus('Updated app awaits a successful local startup.');return;}
      const verified=verifyEnvelope(pending.envelope,enrollment,this.identity(),state);
      await this.verifyNativeFile(this.packagePath(verified.manifest),verified.manifest);
      this.writeState({...state,previous:state.installed||state.previous||null,installed:{...pending,phase:'installed'},pending:null,installedVersion:this.version});
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
  async activateAfterClose(){
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
      this.autoUpdater.autoRunAppAfterInstall=false;
      this.autoUpdater.quitAndInstall(true,false);return true;
    }catch(error){this.onStatus('Update activation refused: '+error.message);return false;}
  }
  suspendInstallation(){this.osShutdown=true;this.autoUpdater.autoInstallOnAppQuit=false;}
  close(){this.feed?.close();this.feed=null;}
}
module.exports={DesktopUpdater,verifiedFeed};
