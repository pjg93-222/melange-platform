'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const {validateState,displayName,activationKey,password,mount}=require('../access-ui.js');
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
test('Terrier uses existing sign-in, terminal request UI is explicit, secrets cleared on page exit',async()=>{
 const f=fixture({appId:'terrier',appName:'Terrier',phase:'terrier_signin'}),ui=mount(f.document,f.api,f.window);await ui.ready;
 assert.equal(f.elements['password-form'].hidden,true);assert.equal(f.elements.open.textContent,'Continue to Terrier sign-in');assert.equal(f.elements.lock.hidden,true);
 f.elements['activation-key'].value='a'.repeat(43);f.pagehide();assert.equal(f.elements['activation-key'].value,'');
 const g=fixture({...base,phase:'pending',requestStatus:'expired',canRequestAgain:true}),other=mount(g.document,g.api,g.window);await other.ready;
 assert.equal(g.elements['request-form'].hidden,false);assert.equal(g.elements['activation-form'].hidden,true);assert.equal(g.calls.length,1);
 g.elements['display-name'].value='Fixture';g.elements['request-form'].fire('submit');await tick();assert.deepEqual(g.calls.at(-1),{method:'request',args:{displayName:'Fixture'}});other.dispose();
});
test('markup has restrictive external-resource CSP and no inline handlers or HTML interpolation',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../access.html'),'utf8'),js=fs.readFileSync(path.join(__dirname,'../access-ui.js'),'utf8');
 for(const directive of ["default-src 'none'","connect-src 'none'","object-src 'none'","frame-src 'none'","form-action 'none'","base-uri 'none'"])assert.ok(html.includes(directive));
 assert.doesNotMatch(html,/\son[a-z]+\s*=|<script(?! src=)[^>]*>|https?:\/\//i);assert.doesNotMatch(js,/innerHTML|outerHTML|insertAdjacentHTML|\beval\(|\bfetch\(/);
});
