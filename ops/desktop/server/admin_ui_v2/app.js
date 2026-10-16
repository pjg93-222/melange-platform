'use strict';
(() => {
  let bearer, csrf, snapshot, busy=false, view='requests';
  const drafts=new Map(), apps={melange:'Melange',terrier:'Terrier',mentat:'Mentat'};
  const byId=id=>document.getElementById(id);
  const status=(text,attention=false)=>{byId('status').textContent=text;byId('status').dataset.tone=attention?'attention':'neutral';};
  const element=(tag,text,parent,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;if(parent)parent.append(e);return e;};
  const titles={requests:'Access requests',grants:'Device sessions',activity:'Successful changes'};
  const descriptions={requests:'Verify the requester before choosing their role and issuing a single-use key.',grants:'Signed in / recently online means an authenticated session checked in within three minutes. It does not indicate a person is typing. Update contact is separate.',activity:'Reported successful changes contain operation metadata and counts. Record contents and passwords are not included.'};
  async function call(route,value){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
    try{const response=await fetch(route,{method:'POST',credentials:'omit',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',...(bearer?{'Authorization':'Bearer '+bearer,'X-Admin-CSRF':csrf}:{})},body:JSON.stringify(value)});if(!response.ok)throw new Error('refused');return await response.json();}finally{clearTimeout(timer);}
  }
  function lock(value){busy=value;byId('refresh').disabled=value||!bearer;byId('end-session').disabled=value||!bearer;for(const button of document.querySelectorAll('[data-action]'))button.disabled=value||!bearer;}
  function remember(){for(const card of byId('requests').querySelectorAll('[data-request]')){const role=card.querySelector('select');if(role)drafts.set(card.dataset.request,{role:role.value,districts:[...card.querySelectorAll('input:checked')].map(c=>c.value)});}}
  async function refresh(){const data=await call('/dashboard',{});remember();snapshot=data;render();status('Updated '+new Date(data.serverTime*1000).toLocaleTimeString()+'.');}
  async function action(callback){if(busy)return;lock(true);try{if(await callback()!==false)await refresh();}catch{status('The action response was not confirmed. Refresh to check its state before trying again. Reopen Application Access Admin if the session expired.',true);}finally{lock(false);}}
  function button(parent,label,run,secondary=false){const b=element('button',label,parent,secondary?'secondary':'');b.type='button';b.dataset.action='';b.disabled=busy;b.addEventListener('click',()=>action(run));return b;}
  function heading(parent,app,name,detail,state,kind=''){const h=element('div',undefined,parent,'row-head');if(apps[app]){const logo=element('img',undefined,h);logo.src='/icons/'+app+'.ico';logo.alt=apps[app];}const text=element('div',undefined,h,'row-heading');element('strong',name+' · '+(apps[app]||app),text);element('div',detail,text,'details');element('span',state,h,'badge '+kind);}
  function showKey(result){byId('key-value').textContent=result.activationKey;byId('key').showModal();}
  function empty(parent,title,text){const e=element('div',undefined,parent,'empty');element('h2',title,e);element('p',text,e);}
  function selected(rows){const app=byId('app-scope').value;return rows.filter(row=>app==='all'||row.app===app);}
  function render(){
    if(!snapshot)return;
    const focus=document.activeElement?.id, scroll={x:scrollX,y:scrollY};
    for(const id of ['requests','grants','activity']){byId(id).replaceChildren();byId(id).hidden=id!==view;}
    byId('view-title').textContent=titles[view];byId('view-description').textContent=descriptions[view];
    for(const nav of document.querySelectorAll('[data-view]')){if(nav.dataset.view===view)nav.setAttribute('aria-current','page');else nav.removeAttribute('aria-current');}
    byId('pending-count').textContent=String(snapshot.notifications.pendingRequests);
    const requests=selected(snapshot.requests),grants=selected(snapshot.grants),events=selected(snapshot.activity);
    if(!requests.length)empty(byId('requests'),'No access requests','New requests for the selected application appear here. Use Refresh to check for changes.');
    for(const request of requests){
      const card=element('section',undefined,byId('requests'),'card');card.dataset.request=request.request_id;
      heading(card,request.app,request.display_name,'Device '+request.device_id+' · '+request.platform+'/'+request.arch,request.state,request.state==='pending'?'pending':'');
      if(request.can_replace_key){const actions=element('div',undefined,card,'actions');button(actions,'Replace unredeemed key',async()=>{if(confirm('Invalidate the previous key and issue a new ten-minute key for this same approved request?'))showKey(await call('/replace-key',{requestId:request.request_id}));},true);}
      if(request.state!=='pending')continue;
      const approval=element('div',undefined,card,'approval'),roleId='role-'+request.request_id,label=element('label','Approved role',approval);label.htmlFor=roleId;
      const role=element('select',undefined,approval);role.id=roleId;
      element('option','Choose approved role',role).value='';element('option',request.app==='terrier'?'District administrator':'Local application user',role).value=request.app==='terrier'?'local_administrator':'local_user';
      const prior=drafts.get(request.request_id);if(prior)role.value=prior.role;
      const districts=[];if(request.app==='terrier'){const group=element('div',undefined,approval,'districts');for(const id of ['co-05','ct-01','or-05']){const l=element('label',undefined,group),c=element('input',undefined,l);c.type='checkbox';c.value=id;c.id='district-'+request.request_id+'-'+id;c.checked=Boolean(prior?.districts.includes(id));l.append(document.createTextNode(id.toUpperCase()));districts.push(c);}}
      const actions=element('div',undefined,approval,'actions');
      const approve=button(actions,'Approve request',async()=>{const ids=districts.filter(c=>c.checked).map(c=>c.value);if(!role.value||(request.app==='terrier'&&!ids.length)){status('Choose the role and each approved district first.',true);return false;}showKey(await call('/approve',{requestId:request.request_id,role:role.value,districtIds:ids}));});approve.id='approve-'+request.request_id;
      button(actions,'Deny request',async()=>{if(confirm('Deny this application access request?'))await call('/deny',{requestId:request.request_id});},true);
    }
    if(!grants.length)empty(byId('grants'),'No approved devices','Approved application installations appear here after activation. No online status is inferred from an update check.');
    for(const grant of grants){const card=element('section',undefined,byId('grants'),'card');heading(card,grant.app,grant.display_name,'Device '+grant.device_id+' · '+(grant.version||'No version reported'),grant.revoked?'Revoked':grant.online?'Signed in / recently online':'Offline or locked',!grant.revoked&&grant.online?'online':'');const districts=JSON.parse(grant.district_ids);if(districts.length)element('div','Approved districts: '+districts.join(', '),card,'details');if(!grant.revoked){const actions=element('div',undefined,card,'actions');button(actions,'Revoke access',async()=>{if(confirm('Stop future access for this application and device? Existing local data will not be erased.'))await call('/revoke',{grantId:grant.grant_id});},true);}}
    if(!events.length)empty(byId('activity'),'No reported changes','Successful operations reported by authorized installations will appear here. This list does not imply every application action is currently reported.');
    for(const event of events){const card=element('section',undefined,byId('activity'),'card');heading(card,event.app,event.display_name,new Date(event.occurred*1000).toLocaleString()+' · device '+event.device_id+(event.district_id?' · '+event.district_id:''),event.kind+' · succeeded');const summary=element('dl',undefined,card,'event-summary');for(const [key,value] of Object.entries({...JSON.parse(event.counts),...JSON.parse(event.references_json)})){element('dt',key,summary);element('dd',String(value),summary);}}
    byId('notifications').textContent=snapshot.notifications.pendingRequests+' access request(s) need review. Pending requests stay here until you review them.';
    if(focus)byId(focus)?.focus({preventScroll:true});scrollTo(scroll.x,scroll.y);
  }
  byId('key-close').addEventListener('click',()=>{byId('key-value').textContent='';byId('key').close();});byId('key').addEventListener('close',()=>{byId('key-value').textContent='';});
  byId('end-session').addEventListener('click',async()=>{if(busy||!bearer)return;lock(true);try{await call('/session-close',{});bearer=csrf=undefined;byId('key-value').textContent='';byId('key').close();snapshot=undefined;drafts.clear();for(const id of ['requests','grants','activity'])byId(id).replaceChildren();status('Session ended. You may close this tab. Use Application Access Admin to open a fresh session.');}catch{status('Could not confirm the session ended. Try End session again.');}finally{lock(false);}});
  addEventListener('pagehide',()=>{if(bearer)fetch('/session-close',{method:'POST',credentials:'omit',cache:'no-store',keepalive:true,headers:{'Content-Type':'application/json','Authorization':'Bearer '+bearer,'X-Admin-CSRF':csrf},body:'{}'}).catch(()=>{});});
  byId('refresh').addEventListener('click',async()=>{if(busy)return;lock(true);status('Refreshing…');try{await refresh();}catch{status('Could not refresh. Existing results remain visible. Reopen Application Access Admin if the session expired.',true);}finally{lock(false);}});
  byId('app-scope').addEventListener('change',()=>{remember();render();});for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>{remember();view=button.dataset.view;render();});
  const fragment=new URLSearchParams(location.hash.slice(1)),bootstrap=fragment.get('bootstrap');history.replaceState(null,'','/');
  (async()=>{lock(true);try{if(!bootstrap){status('Open Application Access Admin from its local shortcut to start a fresh protected session.');return;}const session=await call('/session',{bootstrap});bearer=session.bearer;csrf=session.csrf;await refresh();}catch{status('The protected session could not open. Use the Application Access Admin shortcut on this laptop to try again.');}finally{lock(false);}})();
})();
