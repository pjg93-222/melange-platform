'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const {validateState,displayName,inviteCode,activationKey,password,mount}=require('../access-ui.js');
const base={appId:'mentat',appName:'Mentat',phase:'needs_request'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(initial=base){
 const html=fs.readFileSync(path.join(__dirname,'../access.html'),'utf8'),ids=[...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
 const elements=Object.fromEntries(ids.map(id=>[id,{value:'',textContent:'',hidden:false,disabled:false,events:{},addEventListener(name,cb){this.events[name]=cb;},fire(name){this.events[name]?.({preventDefault(){}});}}]));
 for(const element of Object.values(elements))Object.defineProperty(element,'innerHTML',{set(){throw new Error('Unsafe HTML insertion');}});
 const document={title:'',body:{dataset:{}},getElementById:id=>elements[id],querySelectorAll:()=>Object.values(elements)};
 const listeners={},window={setTimeout:()=>1,clearTimeout(){},addEventListener:(name,fn)=>{listeners[name]=fn;}};
 const calls=[];let state={...initial},listener;
 const api=Object.fromEntries(['info','request','poll','activate','setPassword','unlock','open','lock'].map(method=>[method,async args=>{calls.push({method,args});return state;}]));
 api.onStatus=cb=>{listener=cb;return()=>{listener=null;};};
 return {elements,document,window,calls,api,setState(value){state=value;},push(value){listener?.(value);},pagehide(){listeners.pagehide?.();}};
}
test('renderer state accepts only matching fixed app identity and safe typed fields',()=>{
 assert.equal(validateState(base).appName,'Mentat');
 for(const value of [{...base,appName:'<script>secret</script>'},{...base,url:'https://evil.test'},{...base,token:'secret'},{...base,phase:'ready',owner:true},{...base,accountIdentity:'<img src=x>'},{...base,phase:'pending',requestStatus:'approved',canRequestAgain:true},{...base,appId:'terrier',appName:'Terrier',phase:'needs_password'}])assert.throws(()=>validateState(value));
 assert.throws(()=>validateState({...base,appId:'melange',appName:'Melange'},'mentat'));
 assert.equal(displayName('Fixture device'),'Fixture device');for(const value of ['', ' x', 'x\n', 'x'.repeat(81)])assert.throws(()=>displayName(value));
 assert.throws(()=>activationKey('short'));assert.equal(activationKey('a'.repeat(43)),'a'.repeat(43));
 assert.throws(()=>password('x'.repeat(11)));assert.throws(()=>password('😀'.repeat(257)));assert.throws(()=>password('123456789012\0'));assert.equal(password('valid fixture password'),'valid fixture password');
});
test('activation does not sign in or open; password creation clears secrets and requires separate unlock',async()=>{
 const f=fixture({...base,phase:'pending',requestStatus:'approved'}),ui=mount(f.document,f.api,f.window);await ui.ready;
 f.elements['activation-key'].value='a'.repeat(43);f.setState({...base,phase:'needs_password',accountIdentity:'recipient_'+ 'a'.repeat(32)});
 f.elements['activation-form'].fire('submit');assert.equal(f.elements['activation-key'].value,'');await tick();
 assert.deepEqual(f.calls.map(c=>c.method),['info','activate']);assert.equal(f.elements['ready-panel'].hidden,true);
 f.elements.password.value='valid fixture password';f.elements['password-confirm'].value='valid fixture password';f.setState({...base,phase:'locked'});f.elements['password-form'].fire('submit');
 assert.equal(f.elements.password.value,'');assert.equal(f.elements['password-confirm'].value,'');await tick();
 assert.deepEqual(f.calls.at(-1),{method:'setPassword',args:{password:'valid fixture password'}});assert.equal(f.elements['ready-panel'].hidden,true);
 f.elements.password.value='valid fixture password';f.setState({...base,phase:'ready'});f.elements['password-form'].fire('submit');await tick();
 assert.equal(f.calls.at(-1).method,'unlock');assert.equal(f.elements['ready-panel'].hidden,false);assert.equal(f.calls.some(c=>c.method==='open'),false);
 f.elements.open.fire('click');await tick();assert.equal(f.calls.at(-1).method,'open');ui.dispose();
});
test('mismatched passwords never cross IPC, failures never reveal raw errors and invalid status fails closed',async()=>{
 const f=fixture({...base,phase:'needs_password'}),ui=mount(f.document,f.api,f.window);await ui.ready;
 f.elements.password.value='valid fixture password';f.elements['password-confirm'].value='different password';f.elements['password-form'].fire('submit');
 assert.deepEqual(f.calls.map(c=>c.method),['info']);assert.equal(f.elements.password.value,'');
 f.push({...base,phase:'locked'});f.api.unlock=async()=>{throw new Error('private path / secret-token');};f.elements.password.value='valid fixture password';f.elements['password-form'].fire('submit');await tick();
 assert.doesNotMatch(f.elements.status.textContent,/private|secret-token/);assert.match(f.elements.status.textContent,/could not be completed/);
 f.push({...base,phase:'ready',grant:{}});assert.equal(f.elements['ready-panel'].hidden,true);f.elements.open.fire('click');await tick();assert.equal(f.calls.some(c=>c.method==='open'),false);
 ui.dispose();
});
test('each failure reason maps to a fixed message; unknown, malformed or raw errors fall back to the generic one',async()=>{
 const f=fixture({...base,phase:'locked'}),ui=mount(f.document,f.api,f.window);await ui.ready;
 const attempt=async message=>{f.api.unlock=async()=>{throw new Error(message);};f.elements.password.value='valid fixture password';f.elements['password-form'].fire('submit');await tick();return f.elements.status.textContent;};
 const wrapped=reason=>"Error invoking remote method 'desktop-access:unlock': Error: access:"+reason;
 assert.match(await attempt(wrapped('unreachable')),/Can't reach the administrator's access server/);
 assert.match(await attempt(wrapped('timeout')),/took too long/);
 assert.match(await attempt(wrapped('refused')),/did not accept/);
 assert.match(await attempt(wrapped('identity')),/identity didn't match/);
 assert.match(await attempt(wrapped('trust')),/administrator trust has changed/);
 assert.match(await attempt(wrapped('invite')),/invite code wasn't accepted.*Ask the administrator for a new code/);
 for(const message of [wrapped('failed'),wrapped('unreachablex'),'Access action could not be completed','secret-token /private/path'])assert.match(await attempt(message),/could not be completed/);
 const raw=await attempt(wrapped('identity')+' secret-token /private/path');assert.doesNotMatch(raw,/secret-token|private|Error invoking|desktop-access/);
 ui.dispose();
});
test('a failed request re-reads state so a stored unacknowledged request shows its pending panel and the reason',async()=>{
 const f=fixture(),ui=mount(f.document,f.api,f.window);await ui.ready;
 assert.equal(f.elements['request-form'].hidden,false);
 f.api.request=async()=>{f.setState({...base,phase:'pending'});throw new Error("Error invoking remote method 'desktop-access:request': Error: access:unreachable");};
 f.elements['display-name'].value='Fixture';f.elements['invite-code'].value='ABCD-2345-WXYZ-6789';f.elements['request-form'].fire('submit');await tick();await tick();
 assert.equal(f.elements['pending-panel'].hidden,false);assert.equal(f.elements['request-form'].hidden,true);
 assert.match(f.elements.status.textContent,/Can't reach the administrator's access server/);ui.dispose();
});
test('Terrier uses existing sign-in, terminal request UI is explicit, secrets cleared on page exit',async()=>{
 const f=fixture({appId:'terrier',appName:'Terrier',phase:'terrier_signin'}),ui=mount(f.document,f.api,f.window);await ui.ready;
 assert.equal(f.elements['password-form'].hidden,true);assert.equal(f.elements.open.textContent,'Continue to Terrier sign-in');assert.equal(f.elements.lock.hidden,true);
 f.elements['activation-key'].value='a'.repeat(43);f.pagehide();assert.equal(f.elements['activation-key'].value,'');
 const g=fixture({...base,phase:'pending',requestStatus:'expired',canRequestAgain:true}),other=mount(g.document,g.api,g.window);await other.ready;
 assert.equal(g.elements['request-form'].hidden,false);assert.equal(g.elements['activation-form'].hidden,true);assert.equal(g.calls.length,1);
 g.elements['display-name'].value='Fixture';g.elements['invite-code'].value='  ABCD-2345-WXYZ-6789  ';g.elements['request-form'].fire('submit');await tick();assert.deepEqual(g.calls.at(-1),{method:'request',args:{displayName:'Fixture',inviteCode:'ABCD-2345-WXYZ-6789'}});other.dispose();
});
test('the Terrier owner sees no password or sign-in step and no Lock; Melange/Mentat keep Lock as the way to ask for the password again',async()=>{
 const t=fixture({appId:'terrier',appName:'Terrier',phase:'ready',accountIdentity:'admin'}),ui=mount(t.document,t.api,t.window);await ui.ready;
 assert.equal(t.elements['password-form'].hidden,true);assert.equal(t.elements['ready-panel'].hidden,false);assert.equal(t.elements.lock.hidden,true);
 assert.equal(t.elements.open.textContent,'Open Terrier');assert.match(t.elements.description.textContent,/does not ask for a password/);
 t.elements.open.fire('click');await tick();assert.equal(t.calls.at(-1).method,'open');ui.dispose();
 const m=fixture({...base,phase:'ready'}),other=mount(m.document,m.api,m.window);await other.ready;
 assert.equal(m.elements.lock.hidden,false);assert.match(m.elements.description.textContent,/will not be asked again/);
 m.elements.lock.fire('click');await tick();assert.equal(m.calls.at(-1).method,'lock');
 m.push({...base,phase:'locked'});assert.equal(m.elements['password-form'].hidden,false);assert.match(m.elements.description.textContent,/not be asked again on this computer/);other.dispose();
});
test('markup has restrictive external-resource CSP and no inline handlers or HTML interpolation',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../access.html'),'utf8'),js=fs.readFileSync(path.join(__dirname,'../access-ui.js'),'utf8');
 for(const directive of ["default-src 'none'","connect-src 'none'","object-src 'none'","frame-src 'none'","form-action 'none'","base-uri 'none'"])assert.ok(html.includes(directive));
 assert.doesNotMatch(html,/\son[a-z]+\s*=|<script(?! src=)[^>]*>|https?:\/\//i);assert.doesNotMatch(js,/innerHTML|outerHTML|insertAdjacentHTML|\beval\(|\bfetch\(/);
});

test('invite code field is optional: trimmed, at most 64 characters, and left out of the request when empty',async()=>{
 assert.equal(inviteCode('  ABCD-2345-WXYZ-6789 '),'ABCD-2345-WXYZ-6789');assert.equal(inviteCode('x'.repeat(64)),'x'.repeat(64));
 for(const value of ['','   ',undefined,null,5])assert.equal(inviteCode(value),'');assert.throws(()=>inviteCode('x'.repeat(65)),/invite code wasn't accepted/);
 const f=fixture(),ui=mount(f.document,f.api,f.window);await ui.ready;
 f.elements['display-name'].value='Fixture';f.elements['invite-code'].value='   ';f.elements['request-form'].fire('submit');await tick();
 assert.deepEqual(f.calls.at(-1),{method:'request',args:{displayName:'Fixture'}});
 f.elements['display-name'].value='Fixture';f.elements['invite-code'].value='ABCD-2345-WXYZ-6789';f.elements['request-form'].fire('submit');await tick();
 assert.deepEqual(f.calls.at(-1),{method:'request',args:{displayName:'Fixture',inviteCode:'ABCD-2345-WXYZ-6789'}});ui.dispose();
});
test('a refused invite code keeps the request form with a cleared code field and the invite message visible',async()=>{
 const f=fixture(),ui=mount(f.document,f.api,f.window);await ui.ready;
 f.api.request=async args=>{f.calls.push({method:'request',args});throw new Error("Error invoking remote method 'desktop-access:request': Error: access:invite");};
 f.elements['display-name'].value='Fixture';f.elements['invite-code'].value='ABCD-2345-WXYZ-6789';f.elements['request-form'].fire('submit');
 assert.equal(f.elements['invite-code'].value,'');await tick();await tick();
 assert.equal(f.elements['request-form'].hidden,false);assert.equal(f.elements['pending-panel'].hidden,true);assert.equal(f.elements['invite-code'].value,'');
 assert.match(f.elements.status.textContent,/invite code wasn't accepted.*mistyped, expired, or already used/);assert.equal(f.elements['request-submit'].disabled,false);
 assert.deepEqual(f.calls.map(c=>c.method),['info','request','info']);
 f.elements['invite-code'].value='ABCD-2345-WXYZ-6789';f.pagehide();assert.equal(f.elements['invite-code'].value,'');
});
test('an invite refusal on Check approval re-reads state so the cleared request returns to the form with the message',async()=>{
 const f=fixture({...base,phase:'pending',requestStatus:'pending',requestExpiresAt:2000000000}),ui=mount(f.document,f.api,f.window);await ui.ready;
 assert.equal(f.elements['pending-panel'].hidden,false);assert.equal(f.elements['request-form'].hidden,true);
 f.api.poll=async()=>{f.setState(base);throw new Error("Error invoking remote method 'desktop-access:poll': Error: access:invite");};
 f.elements.poll.fire('click');await tick();await tick();
 assert.equal(f.elements['request-form'].hidden,false);assert.equal(f.elements['pending-panel'].hidden,true);assert.match(f.elements.status.textContent,/invite code wasn't accepted/);ui.dispose();
});
test('access.html has a labeled invite code input with the intended attributes and a hint in the request form',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../access.html'),'utf8').replace(/\r\n/g,'\n');
 const input=/<input\b[^>]*\bid="invite-code"[^>]*>/.exec(html)?.[0];assert.ok(input,'invite-code input missing');assert.doesNotMatch(input,/\brequired\b/);
 const attributes=Object.fromEntries([...input.matchAll(/\s([a-z-]+)="([^"]*)"/g)].map(m=>[m[1],m[2]]));
 assert.deepEqual({type:attributes.type,autocomplete:attributes.autocomplete,spellcheck:attributes.spellcheck,autocapitalize:attributes.autocapitalize,maxlength:attributes.maxlength,placeholder:attributes.placeholder},{type:'text',autocomplete:'off',spellcheck:'false',autocapitalize:'characters',maxlength:'64',placeholder:'XXXX-XXXX-XXXX-XXXX'});
 const form=/<form id="request-form"[\s\S]*?<\/form>/.exec(html)[0];assert.ok(form.includes('id="display-name"')&&form.includes('id="invite-code"'));
 assert.match(form,/<label for="invite-code">Invite code \(if you were given one\)<\/label>/);assert.match(form,/<p class="hint">[^<]*administrator[^<]*code[^<]*<\/p>/i);
});
test('a stored pending request with no reply yet is checked once on load; nothing else is',async()=>{
 const methods=async initial=>{const f=fixture(initial),ui=mount(f.document,f.api,f.window);await ui.ready;await tick();ui.dispose();return f.calls.map(c=>c.method);};
 assert.deepEqual(await methods({...base,phase:'pending'}),['info','poll']);
 assert.deepEqual(await methods({...base,phase:'pending',requestStatus:'pending',requestExpiresAt:2000000000}),['info']);
 assert.deepEqual(await methods(base),['info']);
});
test('the revoked message says district data was removed only when the app reports the store removed',async()=>{
 for(const [extra,pattern] of [[{},/Your local data remains on this computer/],[{districtDataRemoved:true},/^Access was revoked by the administrator\. District data has been removed from this computer\.$/]]){
  const f=fixture({...base,appId:'terrier',appName:'Terrier',phase:'revoked',canRequestAgain:true,...extra}),ui=mount(f.document,f.api,f.window);await ui.ready;
  assert.match(f.elements.description.textContent,pattern);ui.dispose();
 }
 assert.throws(()=>validateState({...base,phase:'revoked',canRequestAgain:true,districtDataRemoved:'yes'}));
});
