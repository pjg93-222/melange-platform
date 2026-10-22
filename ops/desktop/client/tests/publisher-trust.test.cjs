'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {THUMBPRINT,CERTIFICATE,pinnedCertificate,publisherTrusted,trustPublisher,offerTrust}=require('../publisher-trust.cjs');
const root=path.resolve(__dirname,'..'),der=fs.readFileSync(path.join(root,CERTIFICATE));

test('bundled release certificate is the pinned, public, code-signing-only, non-CA certificate',()=>{
  assert.equal(crypto.createHash('sha1').update(der).digest('hex').toUpperCase(),THUMBPRINT);
  const certificate=new crypto.X509Certificate(der);
  assert.equal(certificate.subject,'CN=Melange Systems Desktop Release (self-signed)');assert.equal(certificate.issuer,certificate.subject);
  // Trusting it in Root cannot let it vouch for TLS sites or issue certificates.
  assert.equal(certificate.ca,false);assert.deepEqual(certificate.keyUsage,['1.3.6.1.5.5.7.3.3']);
  assert.ok(!der.toString('latin1').includes('PRIVATE KEY'));assert.ok(der.length<4096);
});

test('installer no longer imports the certificate; it only keeps an offline rollback copy of itself. Builder and standalone launcher use the pinned certificate in memory',()=>{
  const nsis=fs.readFileSync(path.join(root,'installer.nsh'),'utf8');
  assert.ok(!/certutil|addstore|Root/i.test(nsis.replace(/;[^\n]*/g,'')),'no certificate import in the installer, no elevation');
  const install=nsis.slice(nsis.indexOf('!macro customInstall'),nsis.indexOf('!macroend'));
  assert.match(install,/\$\{IfNot\} \$\{isUpdated\}\s+\$\{AndIfNot\} \$\{Silent\}\s+\$\{AndIf\} \$installMode == "CurrentUser"/,'per-user installs only');
  assert.ok(install.includes('CopyFiles /SILENT "$EXEPATH" "$LOCALAPPDATA\\${APP_ID}\\baseline\\${VERSION}\\$EXEFILE"'));
  assert.ok(nsis.slice(nsis.indexOf('!macro customUnInstall')).includes('RMDir /r "$LOCALAPPDATA\\${APP_ID}\\baseline"'));
  assert.ok(fs.readFileSync(path.join(root,'builder.cjs'),'utf8').includes("{from:path.join(__dirname,'"+CERTIFICATE+"'),to:'"+CERTIFICATE+"'}"));
  const launcher=fs.readFileSync(path.join(root,'turn-on-updates.cmd'),'ascii');
  assert.equal(launcher.split('\n').length,launcher.split('\r\n').length,'CRLF only');
  const embedded=Buffer.from(launcher.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0].replace(/\s/g,''),'base64');
  assert.ok(embedded.equals(der));assert.ok(launcher.includes("$pin='"+THUMBPRINT+"'"));assert.match(launcher,/\$cert\.Thumbprint -ne \$pin\)\{[^}]*exit 2\}/);
  // The checked in-memory object is what is added: no temp file, no certutil, no re-read.
  assert.ok(launcher.includes("X509Store]::new('Root','CurrentUser')")&&launcher.includes('$store.Add($cert)')&&!/certutil|WriteAllBytes|\$env:TEMP/i.test(launcher));
  assert.ok(!/runas|-Verb|LocalMachine/i.test(launcher),'current-user store only, no elevation');
});

// exec stub: records the call and the stdin text, answers from a script.
function stub(answer){
  const calls=[];
  const exec=(file,args,options,done)=>{
    const call={file,args,options,script:Buffer.from(args[args.indexOf('-EncodedCommand')+1],'base64').toString('utf16le'),input:null};calls.push(call);
    const result=answer(call);
    queueMicrotask(()=>done(result.error||null,result.stdout||'',''));
    return {stdin:{on(){},end(data){call.input=data;}}};
  };
  return {exec,calls};
}

test('probe: only a definite "not found" is untrusted; every probe failure is unknown',async()=>{
  const answers=[{stdout:'True\r\n'},{stdout:'False\r\n'},{error:new Error('spawn failed')},{error:Object.assign(new Error('timed out'),{killed:true,signal:'SIGTERM'})},{stdout:'garbage'},{stdout:''}];
  const results=[];
  for(const answer of answers)results.push(await publisherTrusted(stub(()=>answer).exec));
  assert.deepEqual(results,[true,false,null,null,null,null]);
  assert.equal(await publisherTrusted(()=>{throw new Error('ENOENT');}),null,'a synchronous spawn failure is also unknown');
  const probe=stub(()=>({stdout:'False'}));await publisherTrusted(probe.exec);
  assert.equal(path.basename(probe.calls[0].file).toLowerCase(),'powershell.exe');
  assert.ok(probe.calls[0].script.includes("Test-Path -LiteralPath 'Cert:\\CurrentUser\\Root\\"+THUMBPRINT+"'")&&!/certutil/i.test(probe.calls[0].script));
});

test('trust import refuses a certificate other than the pinned one and imports exactly the checked bytes',async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'publisher-trust-'));
  try{
    fs.writeFileSync(path.join(directory,CERTIFICATE),Buffer.concat([der,Buffer.from([0])]));
    const refused=stub(()=>({stdout:'True'}));
    assert.throws(()=>pinnedCertificate(directory),/does not match/);await assert.rejects(()=>trustPublisher(directory,refused.exec),/does not match/);assert.equal(refused.calls.length,0);
    fs.writeFileSync(path.join(directory,CERTIFICATE),der);
    let present=false;
    const accepting=stub(call=>{return {stdout:present?'True':'False'};});
    const exec=(file,args,options,done)=>{const child=accepting.exec(file,args,options,done);const call=accepting.calls.at(-1);if(/\.Add\(/.test(call.script))present=true;return child;};
    assert.equal(await publisherTrusted(exec),false);assert.equal(await trustPublisher(directory,exec),true);
    const imported=accepting.calls.find(call=>/\.Add\(/.test(call.script));
    // The bytes cross on stdin as base64 of the checked bytes; the command line holds only fixed script text.
    assert.equal(imported.input,der.toString('base64'));assert.ok(!imported.script.includes(der.toString('base64').slice(0,40)));
    assert.ok(imported.script.includes("X509Store]::new('Root','CurrentUser')")&&imported.script.includes("Open('ReadWrite')")&&imported.script.includes("$cert.Thumbprint -ne '"+THUMBPRINT+"'"));
    assert.ok(!/certutil|-addstore|\.cer|ReadAllBytes|Get-Content/i.test(imported.script),'no second read of any file');
    assert.equal(imported.options.windowsHide,false,'Windows must be free to show its own Yes/No dialog');
    // Declined Windows prompt: the store still lacks it, so updates stay off.
    present=false;const declined=stub(()=>({error:new Error('cancelled'),stdout:'False'}));assert.equal(await trustPublisher(directory,declined.exec),false);
    // Import succeeded but the follow-up probe could not run: not reported as trusted.
    const unknown=stub(call=>/\.Add\(/.test(call.script)?{}:{error:new Error('timeout')});assert.equal(await trustPublisher(directory,unknown.exec),false);
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
});

test('first launch asks once: only a definite miss prompts, a refusal is reported for the caller to remember',async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'publisher-trust-'));
  try{
    fs.writeFileSync(path.join(directory,CERTIFICATE),der);
    const isAdd=call=>/\.Add\(/.test(call.script);
    // Trusted already, or the probe failed: no prompt at all.
    for(const answer of [{stdout:'True'},{error:new Error('timeout')}]){const s=stub(()=>answer);assert.equal(await offerTrust(directory,{exec:s.exec}),'unneeded');assert.ok(!s.calls.some(isAdd));}
    // Declined earlier: not even a probe.
    const quiet=stub(()=>({stdout:'False'}));assert.equal(await offerTrust(directory,{alreadyDeclined:true,exec:quiet.exec}),'skipped');assert.equal(quiet.calls.length,0);
    // Missing, accepted at the Windows dialog.
    let present=false;const yes=stub(call=>{if(isAdd(call))present=true;return {stdout:present?'True':'False'};});
    assert.equal(await offerTrust(directory,{exec:yes.exec}),'trusted');assert.equal(yes.calls.filter(isAdd).length,1);
    // Missing, declined: reported so the app can remember and not ask again.
    const no=stub(call=>isAdd(call)?{error:new Error('cancelled')}:{stdout:'False'});assert.equal(await offerTrust(directory,{exec:no.exec}),'declined');
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
});
