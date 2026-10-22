'use strict';
// Pinned Nsis7z 19 cannot decode 7-Zip 24's automatic ARM64 filter and can
// silently omit those files. Enforce its supported BCJ codec for every build,
// including ordinary npm commands and inherited conflicting filter settings.
process.env.ELECTRON_BUILDER_7Z_FILTER='BCJ';
const fs=require('node:fs');const path=require('node:path');const {appDefinition}=require('./apps.cjs');
const selected=appDefinition(process.env.DESKTOP_APP_ID||'melange');
// Each app updates from its own public repository (Paul, 2026-10-15); DESKTOP_GITHUB_REPOSITORY still overrides.
const DEFAULT_GITHUB_REPOSITORY=Object.freeze({melange:'pjg93-222/Melange',mentat:'pjg93-222/Mentat',terrier:'pjg93-222/Geo'});
const runtimeRoot=path.resolve(process.env.DESKTOP_RUNTIME_ROOT||path.join(__dirname,'runtime'));
const runtime=path.join(runtimeRoot,selected.id);
const icon=path.join(__dirname,'icons',selected.id+'.ico');
const accessTrust=process.env.DESKTOP_ACCESS_TRUST?require('./access-client.cjs').descriptor(JSON.parse(fs.readFileSync(path.resolve(process.env.DESKTOP_ACCESS_TRUST),'utf8'))):null;
const updateProvider=process.env.DESKTOP_UPDATE_PROVIDER||'github';
if(!['github','publisher'].includes(updateProvider))throw new Error('Unsupported desktop update provider');
const desktopUpdateSource=updateProvider==='github'?require('./github-source.cjs').configuration({provider:'github',repository:process.env.DESKTOP_GITHUB_REPOSITORY||DEFAULT_GITHUB_REPOSITORY[selected.id]}):{provider:'publisher'};
if(!fs.existsSync(icon))throw new Error('Original application icon is missing.');
require('./backend-command.cjs').backendCommand(__dirname,selected.id,process.platform,runtimeRoot);
module.exports={
  appId:selected.applicationId,productName:selected.name,artifactName:selected.name+'-${version}-${os}-${arch}.${ext}',
  directories:{output:process.env.DESKTOP_DIST_ROOT?path.join(path.resolve(process.env.DESKTOP_DIST_ROOT),selected.id):'dist/'+selected.id},asar:true,
  files:[...require('./release-files.cjs')],
  // The filter below is case-sensitive; after-pack.cjs walks the packed runtime/*/sources case-insensitively and removes (and logs) any state directory or database file.
  afterPack:path.join(__dirname,'after-pack.cjs'),
  // Runtime databases created by any local run after staging are user state; build_runtime.py also refuses them.
  extraResources:[{from:runtime,to:'runtime/'+selected.id,filter:['**/*','!sources/**/state/**','!sources/**/*.{sqlite,sqlite3,db,sqlite-wal,sqlite-shm,sqlite-journal,sqlite3-wal,sqlite3-shm,sqlite3-journal,db-wal,db-shm,db-journal}']},
   // Public release certificate only (no longer used by the app; kept for publisher-trust.cjs and turn-on-updates.cmd).
   {from:path.join(__dirname,'melange-systems-desktop-release.cer'),to:'melange-systems-desktop-release.cer'}],
  extraMetadata:{name:selected.id+'-desktop',description:selected.name+' independent local desktop application',desktopAppConfig:{appId:selected.id,windowsPublisherName:process.env.WINDOWS_PUBLISHER_NAME||null,desktopAccessTrust:accessTrust,desktopUpdateSource}},
  win:{target:['nsis'],icon,signtoolOptions:process.env.WINDOWS_PUBLISHER_NAME?{publisherName:process.env.WINDOWS_PUBLISHER_NAME}:undefined,verifyUpdateCodeSignature:true},
  nsis:{oneClick:false,perMachine:false,allowToChangeInstallationDirectory:true,deleteAppDataOnUninstall:false,runAfterFinish:false,include:path.join(__dirname,'installer.nsh'),installerIcon:icon,uninstallerIcon:icon},
  mac:{target:['dmg','zip'],category:'public.app-category.productivity',hardenedRuntime:true,notarize:process.env.APPLE_ID?true:false},
  publish:{provider:'generic',url:'https://127.0.0.1/desktop-feed-disabled-until-paired/'},
  electronFuses:{runAsNode:false,enableCookieEncryption:true,enableNodeOptionsEnvironmentVariable:false,enableNodeCliInspectArguments:false,enableEmbeddedAsarIntegrityValidation:true,onlyLoadAppFromAsar:true}
};
