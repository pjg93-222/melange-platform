'use strict';
const {app,BrowserWindow,Menu,dialog,ipcMain,safeStorage,shell,session,powerMonitor,net}=require('electron');
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');const {pathToFileURL}=require('node:url');
const {appDefinition}=require('./apps.cjs');const {pairing,jsonRequest}=require('./trust.cjs');const {Runtime,attachLaunchHeader}=require('./runtime.cjs');const {DesktopUpdater}=require('./updater.cjs');
const {credentialCommand}=require('./credentials.cjs');
const {enrollFresh,enrollApproved}=require('./enrollment.cjs');
const {watchNetwork}=require('./lifecycle.cjs');
const {selectStartupProfile,confineIsolatedPath,confineIsolatedSettings,ISOLATED_TITLE_SUFFIX}=require('./profile.cjs');
const applicationConfig=app.isPackaged?require('./package.json').desktopAppConfig:{};
if(!applicationConfig||(app.isPackaged&&!applicationConfig.appId))throw new Error('Packaged application identity is missing.');
const definition=appDefinition(applicationConfig.appId||(process.env.DESKTOP_APP_ID||'melange'));
const githubConfig=applicationConfig.desktopUpdateSource?.provider==='github'?require('./github-source.cjs').configuration(applicationConfig.desktopUpdateSource):null;
app.setName(definition.name);
// Without the explicit isolated switch this resolves exactly the production profile, AppUserModelId, discovery cache and login registration.
let profile,isolationRefused=null;
try{profile=selectStartupProfile({appId:definition.id,argv:process.argv.slice(1),home:app.getPath('home'),appData:app.getPath('appData'),documents:()=>app.getPath('documents'),protectedPaths:()=>[app.getAppPath(),path.dirname(process.execPath)],packaged:app.isPackaged,testDirectory:process.env.MELANGE_DESKTOP_TEST_PROFILE});}
catch(error){if(error.code!=='ISOLATED_PROFILE_REFUSED')throw error;isolationRefused=error;}
if(isolationRefused){const message=isolationRefused.message+' Nothing was started; no production profile, login item or update installation was used.';process.stderr.write('[melange-desktop] Isolated test profile refused: '+message+'\n');dialog.showErrorBox(definition.name+' isolated test profile refused',message);app.exit(2);}else{
app.setAppUserModelId(profile.applicationUserModelId);
app.setPath('userData',profile.directory);
if(!app.requestSingleInstanceLock()){app.quit();}else{
if(profile.isolated){
 const notice='[melange-desktop] Isolated test profile active: '+profile.directory+' (AppUserModelId '+profile.applicationUserModelId+'). Login startup, OS registration and native update installation are disabled; access, trust, enrollment and signature checks are unchanged.';
 process.stdout.write(notice+'\n');try{fs.mkdirSync(path.join(profile.directory,'logs'),{recursive:true,mode:0o700});fs.appendFileSync(path.join(profile.directory,'logs','isolated-profile.log'),new Date().toISOString()+' '+notice+'\n',{mode:0o600});}catch{}
 const marked=title=>(title||definition.name).endsWith(ISOLATED_TITLE_SUFFIX)?(title||definition.name):(title||definition.name)+ISOLATED_TITLE_SUFFIX;
 // Electron applies constructor options after this event fires, so mark again on the next turn.
 app.on('browser-window-created',(_event,created)=>{const mark=()=>{if(!created.isDestroyed())created.setTitle(marked(created.getTitle()));};mark();setImmediate(mark);created.on('page-title-updated',(event,title)=>{event.preventDefault();created.setTitle(marked(title));});});
}
let window,settingsWindow,accessWindow,accessGate,accessInitializing,accessLifecycle,authenticationWindow,authenticationTicket,runtimePartition,presenceInterval,runtime,updater,districtSwitch,uiStateStore,activityOutbox,activityDraining=false,status='Choose your local data directory to start.',interval,networkWatcher,closing=false,closeApproved=false,askingClose=false,openingAccess=false,checkingUpdates=false;
let background=!profile.isolated&&app.isPackaged&&(process.argv.includes('--desktop-updater')||(process.platform==='darwin'&&app.getLoginItemSettings().wasOpenedAtLogin));
const userData=app.getPath('userData'),setupURL=pathToFileURL(path.join(__dirname,'setup.html')).href;
const accessURL=pathToFileURL(path.join(__dirname,'access.html')).href;
const loadingURL=pathToFileURL(path.join(__dirname,'loading.html')).href;
const resources=app.isPackaged?process.resourcesPath:__dirname;
const portable=app.isPackaged&&require('./portable-mode.cjs').portableMode(resources,definition.id);
const accessTransition=new(require('./access-transition.cjs').AccessTransition)(()=>Boolean(runtime?.child));
async function accessOperation(action){const finish=accessTransition.begin();try{return await action();}finally{finish();}}
const file=name=>path.join(userData,name);
const enrollmentFile=githubConfig?'github-enrollment.enc':'enrollment.enc';
function read(name,fallback={}){try{return JSON.parse(fs.readFileSync(file(name),'utf8'));}catch(error){if(error.code==='ENOENT')return fallback;throw error;}}
function write(name,value){fs.mkdirSync(userData,{recursive:true,mode:0o700});const temporary=file(name+'.tmp');const fd=fs.openSync(temporary,'w',0o600);try{fs.writeFileSync(fd,JSON.stringify(value,null,2));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}fs.renameSync(temporary,file(name));}
function loadEnrollment(){if(!fs.existsSync(file(enrollmentFile)))return null;if(!safeStorage.isEncryptionAvailable())throw new Error('Operating-system credential protection unavailable');const enrollment=JSON.parse(safeStorage.decryptString(fs.readFileSync(file(enrollmentFile))));if(githubConfig){if(enrollment.source!=='github'||enrollment.repository!==githubConfig.repository||enrollment.appId!==definition.id||enrollment.deviceToken)throw new Error('Protected GitHub update source mismatch');return enrollment;}if(enrollment.deviceToken){saveEnrollment(enrollment);return enrollment;}const result=credentialCommand(resources,definition.id,enrollment.deviceId,'read');if(typeof result.token!=='string'||result.token.length<20)throw new Error('Enrollment credential missing; pair this app again');return {...enrollment,deviceToken:result.token};}
function saveEnrollment(value){if(!safeStorage.isEncryptionAvailable())throw new Error('Operating-system credential protection unavailable');const {deviceToken,...metadata}=value;if(deviceToken)credentialCommand(resources,definition.id,value.deviceId,'write',deviceToken);const temporary=file(enrollmentFile+'.tmp');fs.writeFileSync(temporary,safeStorage.encryptString(JSON.stringify(metadata)),{mode:0o600});fs.renameSync(temporary,file(enrollmentFile));}
function updateStatus(message){status=message;if(settingsWindow&&!settingsWindow.isDestroyed())settingsWindow.webContents.send('desktop:status',message);}
function setupOnly(event){if(!settingsWindow||settingsWindow.isDestroyed()||event.sender!==settingsWindow.webContents||event.senderFrame!==settingsWindow.webContents.mainFrame||event.senderFrame.url!==setupURL)throw new Error('Untrusted desktop request');}
function runtimeOnly(event){if(runtime.requestsAllowed===false||accessLifecycle?.suspended||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame||!runtime.origin||new URL(event.senderFrame.url).origin!==runtime.origin)throw new Error('Untrusted application request');}
async function showSetup(){
 if(settingsWindow&&!settingsWindow.isDestroyed()){settingsWindow.show();settingsWindow.focus();return;}
 const owned=new BrowserWindow({width:820,height:900,title:definition.name+' settings',icon:path.join(__dirname,'icons',definition.id+'.ico'),show:false,webPreferences:{preload:path.join(__dirname,'preload.cjs'),partition:'settings-'+definition.id,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});settingsWindow=owned;
 owned.webContents.setWindowOpenHandler(()=>({action:'deny'}));owned.webContents.on('will-navigate',(event,url)=>{if(url!==setupURL)event.preventDefault();});owned.webContents.on('will-redirect',event=>event.preventDefault());
 owned.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));owned.webContents.session.setPermissionCheckHandler(()=>false);
 owned.on('closed',()=>{if(settingsWindow===owned)settingsWindow=null;});try{await owned.loadURL(setupURL);}catch(error){if(!owned.isDestroyed())throw error;}if(!owned.isDestroyed())owned.show();
}
async function ensureAccess(){
 if(accessTransition.mutating)throw new Error('Administrator trust is being verified');
 if(accessGate)return accessGate;if(accessInitializing)return accessInitializing;
 const finish=accessTransition.begin();accessInitializing=(async()=>{const gate=await require('./access-bootstrap.cjs').bootstrapAccess({resources,appId:definition.id,profile:userData,configuration:()=>read('settings.json'),packaged:app.isPackaged,bundledTrust:applicationConfig.desktopAccessTrust});accessGate=gate;
  activityOutbox=new(require('./activity-outbox.cjs').ActivityOutbox)({store:require('./access-state.cjs').accessStateStore({resources,appId:definition.id,profile:userData,kind:'activity'})});
  const {AccessLifecycle,terrierSession}=require('./access-lifecycle.cjs');
  accessLifecycle=new AccessLifecycle({runtime,gate:accessGate,hide:()=>window.hide(),showBoundary:showAccess,
   terrierAuthenticated:()=>terrierSession(runtime,runtimePartition),terrierLogout:()=>terrierSession(runtime,runtimePartition,{logout:true}),
   terrierPasswordLockDisabled:()=>terrierSession(runtime,runtimePartition,{passwordLockDisabled:true}),
   onSuspend:()=>districtSwitch?.suspend(),
   presence:(state,sessionId)=>accessGate.client.presence({sessionId,state,version:app.getVersion(),districtId:definition.id==='terrier'&&state==='active'?read('settings.json').district:null})});
 return gate;})();
 try{return await accessInitializing;}finally{accessInitializing=null;finish();}
}
async function drainActivity(){
 if(activityDraining||!accessGate||!activityOutbox)return;activityDraining=true;
 try{const accepted=await accessGate.evidence();if(!accepted||accepted.revoked)return;
  if(uiStateStore)await uiStateStore.drainActivity(accepted,(approval,event)=>activityOutbox.appendCommitted(approval,event));
  await activityOutbox.flush(accepted,events=>accessGate.client.reportActivity(events));
 }catch{/* Offline or unavailable durable sink: committed intents/outbox stay intact. */}
 finally{activityDraining=false;}
}
function accessOnly(event){if(!accessWindow||accessWindow.isDestroyed()||event.sender!==accessWindow.webContents||event.senderFrame!==accessWindow.webContents.mainFrame||event.senderFrame.url!==accessURL)throw new Error('Untrusted access request');}
async function showAccess(){
 if(accessWindow&&!accessWindow.isDestroyed()){const owned=accessWindow;try{const state=await accessGate?.info();if(state&&!owned.isDestroyed())owned.webContents.send('desktop-access:status',state);}catch{}if(!owned.isDestroyed()){owned.show();owned.focus();}return;}
 accessWindow=new BrowserWindow({width:640,height:740,title:definition.name+' access',icon:path.join(__dirname,'icons',definition.id+'.ico'),show:false,webPreferences:{preload:path.join(__dirname,'access-preload.cjs'),partition:'access-'+definition.id,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});
 const owned=accessWindow;owned.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 owned.webContents.on('will-navigate',(event,url)=>{if(url!==accessURL)event.preventDefault();});owned.webContents.on('will-redirect',event=>event.preventDefault());
 owned.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));owned.webContents.session.setPermissionCheckHandler(()=>false);
 owned.on('closed',()=>{if(accessWindow===owned)accessWindow=null;if(!openingAccess&&!runtime?.child&&!closing&&window&&!window.isDestroyed())window.close();});
 try{await owned.loadURL(accessURL);}catch(error){if(!owned.isDestroyed())throw error;}if(!owned.isDestroyed())owned.show();
}
async function importAccessTrust(){
 let ticket;try{ticket=accessTransition.choose();}catch{updateStatus('Close this app and finish access actions before changing its trusted server endpoint.');return;}
 const selected=await dialog.showOpenDialog(window,{title:'Import administrator server trust',filters:[{name:'Administrator trust JSON',extensions:['json']}],properties:['openFile']});
 if(selected.canceled)return;
 let finish;
 try{const selectedFile=selected.filePaths[0];if(fs.statSync(selectedFile).size>32768)throw new Error();const value=JSON.parse(fs.readFileSync(selectedFile,'utf8'));
  finish=accessTransition.commit(ticket);await require('./access-bootstrap.cjs').importTrust({resources,appId:definition.id,profile:userData,packaged:app.isPackaged,bundledTrust:applicationConfig.desktopAccessTrust,value});accessGate=null;finish();finish=null;await ensureAccess();updateStatus('Administrator trust verified. Open the app to request or use approved access.');
 }catch{updateStatus('Administrator trust could not be changed while access was active or the selected trust was invalid. Existing identity remains unchanged.');}finally{finish?.();}
}
const launch=require('./single-launch.cjs').singleLaunch(async function launchOnce(fromAccess=false){
 let finish;try{finish=accessTransition.begin();}catch{updateStatus('Wait for administrator trust verification before opening the app.');return;}
 try{return await launchAuthorized(fromAccess);}finally{finish();}
});
async function launchAuthorized(fromAccess){
 background=false;
 let generation=accessLifecycle?.generation;
 try{const gate=await ensureAccess();if(generation===undefined)generation=accessLifecycle.generation;await require('./access-gate.cjs').authorizeAvailable(gate,async state=>{if(state.phase==='revoked'&&runtime.child&&!accessLifecycle.suspended)await accessLifecycle.suspend('locked');});}catch{if(runtime.child&&!accessLifecycle?.suspended)await accessLifecycle?.suspend('locked');if(fromAccess)throw new Error('Access is not ready');window.hide();await showAccess();return;}
 if(closing||generation!==accessLifecycle.generation)return;
 if(runtime.child&&accessLifecycle.suspended&&definition.id==='terrier'&&(accessLifecycle.requiresFreshPassword||!await require('./access-lifecycle.cjs').terrierSession(runtime,runtimePartition))){await showTerrierSignIn();return;}
 openingAccess=true;
 try{const approved=await accessGate.authorize();await accessLifecycle.resume(approved,{expectedGeneration:generation});const origin=await runtime.start(read('settings.json'));districtSwitch?.remember(read('settings.json'),runtime);if(closing||!await accessLifecycle.canReveal(generation,approved))return;let currentOrigin;try{currentOrigin=new URL(window.webContents.getURL()).origin;}catch{}if(currentOrigin!==origin)await window.loadURL(origin+runtime.launchPath);runtime.launchPath='/';if(closing||!await accessLifecycle.canReveal(generation,approved))return;window.show();accessWindow?.close();if(!portable)updater.markApplicationReady();await accessLifecycle.heartbeat();}
 catch(error){if(accessLifecycle?.suspended)return;try{if(!portable)await updater.markApplicationFailed();}catch{}updateStatus('Application startup failed. Check local logs.');await showSetup();throw error;}
 finally{openingAccess=false;}
}
async function showTerrierSignIn(){
 if(authenticationWindow&&!authenticationWindow.isDestroyed()){if(accessLifecycle.authenticationCurrent(authenticationTicket)){authenticationWindow.show();return;}const stale=authenticationWindow;authenticationWindow=null;authenticationTicket=null;runtime.authenticationContentsId=null;stale.close();}
 const ticket=await accessLifecycle.prepareAuthentication();
 if(closing||!accessLifecycle.authenticationCurrent(ticket))return;
 const owned=new BrowserWindow({width:680,height:740,title:'Terrier sign in',icon:path.join(__dirname,'icons','terrier.ico'),show:false,webPreferences:{session:runtimePartition,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});authenticationWindow=owned;runtime.authenticationContentsId=owned.webContents.id;
 authenticationTicket=ticket;
 owned.webContents.setWindowOpenHandler(()=>({action:'deny'}));owned.webContents.on('will-navigate',(event,url)=>{try{const target=new URL(url);if(target.origin!==runtime.origin||target.pathname!=='/desktop-signin')event.preventDefault();}catch{event.preventDefault();}});owned.webContents.on('will-redirect',event=>event.preventDefault());
 owned.on('closed',()=>{const current=authenticationWindow===owned;if(current){authenticationWindow=null;authenticationTicket=null;runtime.authenticationContentsId=null;}if(current&&accessLifecycle.suspended&&!closing&&window&&!window.isDestroyed())showAccess().catch(()=>{});});
 await owned.loadURL(runtime.origin+'/desktop-signin');if(closing||owned.isDestroyed()||!accessLifecycle.authenticationCurrent(ticket)){if(!owned.isDestroyed())owned.close();return;}accessWindow?.hide();owned.show();
}
async function resumeTerrierSession(){
 const owned=authenticationWindow,ticket=authenticationTicket;if(!owned||owned.isDestroyed()||!accessLifecycle.authenticationCurrent(ticket)||!await require('./access-lifecycle.cjs').terrierSession(runtime,runtimePartition))return;
 try{const approved=await accessGate.authorize();if(authenticationWindow!==owned||!accessLifecycle.authenticationCurrent(ticket))return;await accessLifecycle.resume(approved,{freshPassword:true,expectedGeneration:ticket.generation});if(closing||!await accessLifecycle.canReveal(ticket.generation,approved))return;openingAccess=true;owned.close();accessWindow?.close();window.show();await accessLifecycle.heartbeat();}catch{}finally{openingAccess=false;}
}
async function checkUpdates(){
 if(portable){updateStatus('Portable copy: replace this extracted application folder to update. Your local data remains separate.');if(accessGate)try{const state=await accessGate.refresh();if(state.phase==='revoked'&&runtime.child&&!accessLifecycle.suspended)await accessLifecycle.suspend('locked');}catch{}await drainActivity();return;}

 if(checkingUpdates||closing||updater.osShutdown)return;checkingUpdates=true;
 try{
  let enrollmentDeferred=false;
  try{if(githubConfig)await require('./github-source.cjs').enrollGitHub({appId:definition.id,publicKeys:applicationConfig.desktopAccessTrust?.updatePublicKeysByApp[definition.id],config:githubConfig,load:loadEnrollment,save:saveEnrollment});else await accessOperation(async()=>{const gate=await ensureAccess();await enrollApproved({client:gate.client,version:app.getVersion(),load:loadEnrollment,save:saveEnrollment});});}
  catch{enrollmentDeferred=true;}
  await updater.check();
  if(enrollmentDeferred&&!fs.existsSync(file(enrollmentFile)))updateStatus(githubConfig?'GitHub update initialization is pending. The receiver retries when packaged verification keys and protected local storage are available.':'Automatic update enrollment is pending. The receiver retries when the approved server and protected credentials are available.');
  if(accessGate)try{const state=await accessGate.refresh();if(state.phase==='revoked'&&runtime.child&&!accessLifecycle.suspended)await accessLifecycle.suspend('locked');if(accessWindow&&!accessWindow.isDestroyed())accessWindow.webContents.send('desktop-access:status',state);}catch{}
  await drainActivity();
  if(background&&!runtime.child&&!window.isVisible()&&!updater.osShutdown){const activating=await updater.activateAfterClose();if(activating)closing=true;}
 }catch{updateStatus('Update check deferred. The receiver will retry; local work remains available.');}
 finally{checkingUpdates=false;}
}
async function recoverPrevious(){if(portable){updateStatus('Portable copy: replace the extracted application folder to change versions.');return;}try{const target=updater.recoveryCandidate();const result=await dialog.showMessageBox(window,{type:'warning',buttons:['Cancel','Prepare recovery'],defaultId:0,cancelId:0,title:'Restore previous '+definition.name+' version?',message:'Restore previously authorized version '+target.version+'?',detail:'Program files will be restored only after you close the app. Local data is retained. This recovery cannot reverse data changes made by a newer version.'});if(result.response===1)await updater.beginRecovery();}catch(error){updateStatus('Recovery unavailable: '+error.message);}}
async function confirmClose(){
  try{
    if(districtSwitch?.busy){updateStatus('Finish opening the district before closing.');return;}
    if(await runtime.activity()===true){await dialog.showMessageBox(window,{type:'info',message:'A calculation is still running. Close this app after it finishes.',buttons:['Keep working']});return;}
    const {response}=await dialog.showMessageBox(window,{type:'question',buttons:['Keep working','Close app'],defaultId:0,cancelId:0,title:'Close '+definition.name+'?',message:'Save changes and finish active calculations before closing.',detail:'Closing stops this app’s local processes. Verified updates install only after the app closes.'});
    if(response===1){if(await runtime.activity()===true){updateStatus('Close deferred: a calculation is running.');return;}closeApproved=true;window.close();}
  }finally{askingClose=false;}
}
async function finishClose(){if(closing)return;closing=true;clearInterval(interval);clearInterval(presenceInterval);networkWatcher?.close();if(districtSwitch)await districtSwitch.stopAll();else await runtime?.stop();const activating=portable?false:await updater?.activateAfterClose();if(!activating){updater?.close();app.quit();}}
app.on('second-instance',()=>{background=false;if(!runtime||closing)return;if(accessLifecycle?.suspended)showAccess().catch(()=>{});else launch().catch(()=>{});});
app.whenReady().then(async()=>{
  if(profile.migrationRequired){dialog.showErrorBox(definition.name+' profile migration required','Existing application settings, credentials or data were found in the earlier profile location. They have been preserved. Migrate that profile explicitly before using this installation.');app.quit();return;}
  fs.mkdirSync(userData,{recursive:true,mode:0o700});
  if(!fs.existsSync(file('settings.json'))){const dataDirectory=path.join(userData,'data');fs.mkdirSync(dataDirectory,{recursive:true,mode:0o700});write('settings.json',{dataDirectory,...(definition.id==='terrier'?{district:'co-05'}:{})});}
  if(profile.isolated)try{confineIsolatedSettings(profile.boundary,read('settings.json'));}catch(error){dialog.showErrorBox(definition.name+' isolated test profile refused',error.message+' The backend was not started. Correct or remove settings.json in '+userData+'.');app.quit();return;}
  const createRuntime=()=>new Runtime(definition.id,userData,resources,{protectedPaths:[app.getAppPath(),path.dirname(process.execPath)],rendererFactory:definition.id==='mentat'?()=>require('./render-service.cjs').startRenderService({BrowserWindow,session}):null,authorize:async configuration=>(await ensureAccess()).authorize(configuration),confine:profile.isolated?paths=>{for(const item of paths)confineIsolatedPath(profile.boundary,item,'The application data location');}:null});
  runtime=createRuntime();
  if(definition.id==='terrier'){
    const {verifyDistrictAsync}=require('./district-switch.cjs');
    districtSwitch=new(require('./district-transition.cjs').DistrictTransition)({
     read:()=>read('settings.json'),write:value=>write('settings.json',profile.isolated?confineIsolatedSettings(profile.boundary,value):value),scope:()=>accessGate?.districts()||[],
     fingerprint:configuration=>require('./district-transition.cjs').districtFingerprint(userData,configuration),
     verify:configuration=>verifyDistrictAsync(resources,userData,profile.isolated?confineIsolatedSettings(profile.boundary,configuration):configuration,[app.getAppPath(),path.dirname(process.execPath)]),createRuntime,current:()=>runtime,
     activate:next=>{runtime=next;runtime.contentsId=window.webContents.id;if(accessLifecycle)accessLifecycle.runtime=next;},
     location:()=>window.webContents.getURL(),navigate:url=>window.loadURL(url),
     canReveal:approved=>!closing&&accessLifecycle.canReveal(accessLifecycle.generation,approved),onUnavailable:showAccess,
     log:value=>{try{const logs=path.join(userData,'logs');fs.mkdirSync(logs,{recursive:true});fs.appendFileSync(path.join(logs,'district-switch.jsonl'),JSON.stringify({...value,time:new Date().toISOString()})+'\n');}catch{}}
    });
  }
  const partition=session.fromPartition('persist:'+definition.applicationId);runtimePartition=partition;attachLaunchHeader(partition,()=>runtime,()=>districtSwitch?Array.from(districtSwitch.pool.values(),item=>item.runtime.origin).filter(Boolean):[]);
  partition.setPermissionRequestHandler((contents,permission,callback)=>callback(false));
  partition.setPermissionCheckHandler(()=>false);
  window=new BrowserWindow({width:1400,height:960,title:definition.name,icon:path.join(__dirname,'icons',definition.id+'.ico'),show:false,webPreferences:{preload:path.join(__dirname,'preload.cjs'),session:partition,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});
  runtime.contentsId=window.webContents.id;
  partition.webRequest.onCompleted(details=>{
   if(definition.id!=='terrier'||!runtime.origin||!accessLifecycle||new URL(details.url).origin!==runtime.origin||details.statusCode!==200)return;
   const route=new URL(details.url).pathname;
   if(details.webContentsId===runtime.contentsId&&route==='/api/auth/logout'&&details.method==='POST')accessLifecycle.suspend('signed_out').catch(()=>{});
   if(['/api/auth/login','/api/auth/accept'].includes(route)&&details.method==='POST'){
    if(details.webContentsId===runtime.authenticationContentsId)resumeTerrierSession();
    else if(details.webContentsId===runtime.contentsId)accessLifecycle.heartbeat().catch(()=>{});
   }
  });
  ipcMain.handle('desktop-access:info',async event=>{accessOnly(event);try{return await accessOperation(async()=>await(await ensureAccess()).info());}catch{return {appId:definition.id,appName:definition.name,phase:'error'};}});
  for(const operation of ['request','poll','activate','setPassword','unlock','lock'])ipcMain.handle('desktop-access:'+operation,async(event,payload)=>{accessOnly(event);if(runtime.child&&!accessLifecycle?.suspended)throw new Error('Lock the workspace before changing access');try{const result=await accessOperation(async()=>await(await ensureAccess()).action(operation,payload));if(operation==='activate')checkUpdates();return result;}catch{throw new Error('Access action could not be completed');}});
  ipcMain.handle('desktop-access:open',async event=>{accessOnly(event);await launch(true);return (await ensureAccess()).info();});
  if(definition.id==='melange'){
    const {UIStateStore,registerStateIPC}=require('./ui-state.cjs');
    uiStateStore=new UIStateStore(userData,{activityContext:()=>runtime.authorization?.accepted||null,onMutation:()=>{drainActivity();}});
    registerStateIPC(ipcMain,()=>({window,runtime,appId:definition.id}),uiStateStore);
  }else{
    ipcMain.on('desktop:state-load',event=>{event.returnValue={enabled:false};});
  }
  // Visibility is granted only by the post-await authorization checks in launch.
  // A late ready-to-show event must never reveal preserved locked content.
  window.on('closed',()=>{authenticationWindow?.close();accessWindow?.close();settingsWindow?.close();});
  const permitted=url=>url===loadingURL||Boolean(runtime.origin&&new URL(url).origin===runtime.origin);
  window.webContents.on('will-navigate',(event,url)=>{if(!permitted(url))event.preventDefault();});
  window.webContents.on('will-redirect',(event,url)=>{if(!permitted(url))event.preventDefault();});
  window.webContents.setWindowOpenHandler(({url})=>{try{if(['https:','http:'].includes(new URL(url).protocol)&&!url.startsWith('http://127.'))shell.openExternal(url);}catch{}return {action:'deny'};});
  const githubSource=githubConfig?new(require('./github-source.cjs').GitHubSource)({repository:githubConfig.repository,appId:definition.id,cacheDirectory:profile.discoveryCache}):null;
  const githubOptions=githubSource?{request:githubSource.request.bind(githubSource),download:githubSource.download.bind(githubSource)}:{};
  updater=new DesktopUpdater({...githubOptions,appId:definition.id,version:app.getVersion(),userData:githubConfig?path.join(userData,'github-updates'):userData,loadEnrollment,saveEnrollment,readState:()=>read(githubConfig?'github-update-state.json':'update-state.json'),writeState:value=>write(githubConfig?'github-update-state.json':'update-state.json',value),onStatus:message=>updateStatus(githubSource?message+' '+githubSource.discoverySummary():message),autoUpdater:require('electron-updater').autoUpdater,packaged:app.isPackaged,nativeInstallation:profile.nativeInstallation,publisherName:applicationConfig.windowsPublisherName});
  require('electron-updater').autoUpdater.on('error',error=>{updateStatus('Update not installed: '+error.message);updater.nativeFailure().catch(()=>updateStatus('Update failure recorded locally; administrator status unavailable.')).finally(()=>{if(closing){updater.close();app.quit();}});});
  ipcMain.handle('desktop:info',event=>{setupOnly(event);return {name:definition.name,appId:definition.id,version:app.getVersion(),updateProvider:githubConfig?'github':'publisher',status,settings:read('settings.json'),paired:fs.existsSync(file(enrollmentFile))};});
  ipcMain.handle('desktop:directory',async event=>{setupOnly(event);const result=await dialog.showOpenDialog(settingsWindow,{properties:['openDirectory','createDirectory'],title:'Choose '+definition.name+' data directory'});if(!result.canceled){const settings={...read('settings.json'),dataDirectory:result.filePaths[0]};if(profile.isolated)try{confineIsolatedSettings(profile.boundary,settings);}catch(error){updateStatus(error.message+' Settings were not changed.');return read('settings.json');}write('settings.json',settings);if(runtime.child)updateStatus('Data folder saved for the next application start. Your current workspace remains open.');return settings;}return read('settings.json');});
  async function selectDistrict(event,district,fromSetup=false){
    if(fromSetup)setupOnly(event);else runtimeOnly(event);
    if(definition.id!=='terrier'||askingClose||closing||accessLifecycle?.suspended)throw new Error('District switching is unavailable.');
    if(district===read('settings.json').district)return false;
    return accessOperation(()=>districtSwitch.switch(district));
  }
  ipcMain.handle('desktop:district',(event,district)=>selectDistrict(event,district,true));
  ipcMain.handle('desktop:switch-district',(event,district)=>selectDistrict(event,district));
  ipcMain.handle('desktop:renderer-ready',event=>{runtimeOnly(event);districtSwitch?.rendererReady();});
  ipcMain.handle('desktop:districts',event=>{runtimeOnly(event);if(definition.id!=='terrier')throw new Error('Districts are only available in Terrier.');const {LABELS}=require('./district-switch.cjs'),configuration=read('settings.json');return {schema:'desktop-districts-1',current:configuration.district,districts:districtSwitch.scope(configuration).map(id=>({id,label:LABELS[id],ready:true,port:0}))};});
  ipcMain.handle('desktop:pair',async(event,bundle)=>{setupOnly(event);if(githubConfig)throw new Error('GitHub updates use the verification keys bundled with this installer. Manual publisher pairing is not required.');if(!safeStorage.isEncryptionAvailable())throw new Error('Operating-system credential protection unavailable');const result=await enrollFresh({bundle:pairing(bundle,definition.id),appId:definition.id,version:app.getVersion(),platform:process.platform,arch:process.arch,request:jsonRequest,readPrevious:()=>fs.existsSync(file(enrollmentFile))?JSON.parse(safeStorage.decryptString(fs.readFileSync(file(enrollmentFile)))):null,save:saveEnrollment,deleteCredential:(appId,deviceId)=>credentialCommand(resources,appId,deviceId,'delete')});updateStatus('Paired with a new device identity. Administrator recipient approval is required.'+(result.priorCredentialRemoved?'':' Previous credential cleanup remains pending.'));return true;});
  ipcMain.handle('desktop:launch',async event=>{setupOnly(event);await launch();});
  Menu.setApplicationMenu(Menu.buildFromTemplate([{label:definition.name,submenu:[{label:'Open app',click:()=>launch().catch(()=>{})},{label:'Lock workspace',click:()=>{if(runtime.child)accessLifecycle?.suspend('locked').catch(()=>{});}},{label:'Settings',click:showSetup},{label:'Import or refresh administrator server trust',click:importAccessTrust},{label:'Check for approved updates',click:checkUpdates},{label:'Restore previous authorized version',click:recoverPrevious},{label:'Open logs',click:()=>shell.openPath(path.join(userData,'logs'))},{type:'separator'},{role:'quit'}]},{role:'editMenu'},{role:'viewMenu'}]));
  window.on('close',event=>{
    if(updater.osShutdown||closeApproved||!runtime.child)return;
    event.preventDefault();if(askingClose)return;askingClose=true;
    confirmClose();
  });
  // Never override a renderer beforeunload veto; unsaved-work protections remain active.
  require('./district-switch.cjs').preserveUnloadVeto(window.webContents,()=>{closeApproved=false;districtSwitch?.cancel();});
  if(!background&&definition.id==='terrier'){await window.loadURL(loadingURL);window.show();}else await window.loadURL('about:blank');
  if(!background){if(read('settings.json').dataDirectory)await launch().catch(()=>{});else await showSetup();}
  if(!portable)if(profile.registerLoginItem)app.setLoginItemSettings({openAtLogin:true,...(process.platform==='win32'?{name:definition.applicationId,path:process.execPath,args:['--desktop-updater']}:{})});
  checkUpdates();interval=setInterval(checkUpdates,15*60*1000);interval.unref();
  networkWatcher=watchNetwork({isOnline:()=>net.isOnline(),onReconnect:checkUpdates});
  presenceInterval=setInterval(()=>{accessLifecycle?.heartbeat().catch(()=>{});drainActivity();},60000);presenceInterval.unref();
  powerMonitor.on('lock-screen',()=>{if(accessLifecycle){accessLifecycle.osLocked=true;accessLifecycle.suspend('locked').catch(()=>{});}});
  powerMonitor.on('unlock-screen',()=>{if(accessLifecycle){accessLifecycle.osLocked=false;accessLifecycle.heartbeat().catch(()=>{});}});
  powerMonitor.on('suspend',()=>{if(accessLifecycle)accessLifecycle.suspend('locked').catch(()=>{});});
  powerMonitor.on('resume',checkUpdates);
  powerMonitor.on('unlock-screen',checkUpdates);
  powerMonitor.on('shutdown',()=>updater.suspendInstallation());
  window.on('query-session-end',()=>updater.suspendInstallation());
  window.on('session-end',()=>updater.suspendInstallation());
});
app.on('window-all-closed',finishClose);
app.on('before-quit',event=>{if(!closing&&window&&!window.isDestroyed()){event.preventDefault();window.close();}else{clearInterval(interval);networkWatcher?.close();}});
}
}
