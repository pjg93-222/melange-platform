'use strict';
(() => {
  let bearer, csrf;
  const byId = id => document.getElementById(id);
  const status = text => { byId('status').textContent = text; };
  const element = (tag, text, parent, className) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text;if(className)e.className=className;if(parent)parent.append(e);return e; };
  async function call(route, value) {
    const response=await fetch(route,{method:'POST',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json',...(bearer?{'Authorization':'Bearer '+bearer,'X-Admin-CSRF':csrf}:{})},body:JSON.stringify(value)});
    if(!response.ok)throw new Error('The request was refused. Refresh or reopen the dashboard from this laptop.');
    return response.json();
  }
  async function action(callback){try{await callback();await refresh();}catch(error){status(error.message);}}
  function button(parent,label,run){const b=element('button',label,parent);b.type='button';b.addEventListener('click',()=>action(async()=>{b.disabled=true;try{await run();}finally{b.disabled=false;}}));return b;}
  async function refresh(){
    const data=await call('/dashboard',{});
    for(const id of ['requests','grants','activity'])byId(id).replaceChildren();
    for(const request of data.requests){
      const card=element('section',undefined,byId('requests'),'card');
      element('strong',request.display_name+' · '+request.app,card);
      element('div','Device '+request.device_id+' · '+request.platform+'/'+request.arch+' · '+request.state,card,'details');
      if(request.can_replace_key)button(card,'Replace unredeemed key',async()=>{if(!confirm('Invalidate the previous key and issue a new ten-minute key for this same approved request?'))return;const result=await call('/replace-key',{requestId:request.request_id});byId('key-value').textContent=result.activationKey;byId('key').showModal();});
      if(request.state!=='pending')continue;
      const role=element('select',undefined,card);role.setAttribute('aria-label','Approved role');
      const prompt=element('option','Choose approved role',role);prompt.value='';
      const choice=element('option',request.app==='terrier'?'District administrator (no superuser)':'Local application user',role);choice.value=request.app==='terrier'?'local_administrator':'local_user';
      const districts=[];
      if(request.app==='terrier')for(const id of ['co-05','ct-01','or-05']){const label=element('label',undefined,card);const checkbox=element('input',undefined,label);checkbox.type='checkbox';checkbox.value=id;label.append(document.createTextNode(id.toUpperCase()));districts.push(checkbox);}
      button(card,'Approve request',async()=>{const ids=districts.filter(c=>c.checked).map(c=>c.value);if(!role.value||(request.app==='terrier'&&!ids.length))throw new Error('Choose the role and each approved district first.');const result=await call('/approve',{requestId:request.request_id,role:role.value,districtIds:ids});byId('key-value').textContent=result.activationKey;byId('key').showModal();});
      button(card,'Deny request',()=>call('/deny',{requestId:request.request_id}));
    }
    for(const grant of data.grants){const card=element('section',undefined,byId('grants'),'card');element('strong',grant.display_name+' · '+grant.app,card);element('div','Device '+grant.device_id+' · '+(grant.revoked?'Revoked':grant.online?'Signed in / recently online':'Offline or locked')+' · '+(grant.version||'No version reported'),card,'details');element('div','Approved districts: '+JSON.parse(grant.district_ids).join(', '),card);if(!grant.revoked)button(card,'Revoke access',async()=>{if(confirm('Stop future access for this application and device? Existing local data will not be erased.'))await call('/revoke',{grantId:grant.grant_id});});}
    for(const event of data.activity){const card=element('section',undefined,byId('activity'),'card');element('strong',event.display_name+' · '+event.app+' · '+event.kind+' · succeeded',card);element('div',new Date(event.occurred*1000).toLocaleString()+' · device '+event.device_id+(event.district_id?' · '+event.district_id:''),card,'details');element('pre',JSON.stringify({counts:JSON.parse(event.counts),...JSON.parse(event.references_json)},null,2),card);}
    byId('notifications').textContent=data.notifications.pendingRequests+' access request(s) need review. Desktop notifications are optional Windows alerts; a handoff does not mean they were seen. Use this dashboard if alerts are suppressed.';
    status('Updated '+new Date(data.serverTime*1000).toLocaleTimeString()+'.');
  }
  byId('key-close').addEventListener('click',()=>{byId('key-value').textContent='';byId('key').close();});
  byId('key').addEventListener('close',()=>{byId('key-value').textContent='';});
  byId('refresh').addEventListener('click',()=>action(async()=>{}));
  const fragment=new URLSearchParams(location.hash.slice(1));const bootstrap=fragment.get('bootstrap');history.replaceState(null,'','/');
  (async()=>{try{if(!bootstrap)throw new Error('Open the dashboard from its local launcher to start a fresh protected session.');const session=await call('/session',{bootstrap});bearer=session.bearer;csrf=session.csrf;byId('refresh').disabled=false;await refresh();}catch(error){status(error.message);}})();
})();
