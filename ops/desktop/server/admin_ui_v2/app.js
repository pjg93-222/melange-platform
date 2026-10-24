'use strict';
(() => {
  let bearer, csrf, snapshot, busy=false, view='apps', appsData, detail, tab='overview', current, appsTimer, followTimer, followGen=0, detailGen=0, logsBusy=false, logLines=[], logTop, appsWorking=false, slow=false, polling=false, dockerStarted=0, unansweredTimer;
  const legacyViews=['requests','grants','activity','invites'], expanded=new Set(['terrier']), logOptions={count:500,follow:true,filter:''};
  const drafts=new Map(), apps={melange:'Melange',terrier:'Terrier',mentat:'Mentat'}, appOf={'melange-desk':'melange','mentat-console':'mentat',terrier:'terrier'};
  const byId=id=>document.getElementById(id);
  let desktopLoading=false, desktopTimer;
  const noAnswer='Melange Admin is not answering. Trying again\u2026 If this keeps up, open Melange Admin again from its icon.';
  async function loadDesktopApps(){
    if(!bearer||desktopLoading)return;
    clearTimeout(desktopTimer);desktopLoading=true;const token=bearer,notice=byId('desktop-status');
    try{
      const data=await call('/desktop-apps',{});if(token!==bearer)return;
      const body=byId('desktop-apps-body');body.replaceChildren();
      for(const row of data.applications){
        const tr=element('tr',undefined,body),name=element('td',row.name,tr);
        name.title=row.path||'';element('td',row.installed?'Installed'+(row.version?' · '+row.version:''):'Not installed',tr);
        const button=element('button','Open '+row.name,element('td',undefined,tr),'btn primary');button.type='button';button.disabled=!row.installed;button.dataset.key='desktop-'+row.appId;
        button.addEventListener('click',async()=>{
          if(!bearer)return;button.disabled=true;notice.textContent='Opening '+row.name+'…';
          try{await call('/desktop-apps/open',{appId:row.appId});notice.textContent=row.name+' launch requested. Check its desktop window.';}
          catch(error){if(!lost(error))notice.textContent=error.body?.message||'Could not open '+row.name+'. Check its installation and Windows notification.';}
          finally{button.disabled=!bearer||!row.installed;}
        });
      }
      notice.textContent='Installed desktop applications on this computer.';
    }catch(error){
      if(token!==bearer)return;
      if(error.unanswered){notice.textContent=noAnswer;desktopTimer=setTimeout(loadDesktopApps,10000);}   // its own retry: any later answer clears the shared one
      else if(error.name==='AbortError'){notice.textContent='Could not read installed apps. It will try again by itself; choose Refresh apps to try sooner.';desktopTimer=setTimeout(loadDesktopApps,10000);}   // our own timeout: slow, not silent, so the same retry
      else if(!lost(error))notice.textContent='Could not read installed apps. Choose Refresh apps to try again.';
    }
    finally{desktopLoading=false;}
  }
  const status=(text,attention=false)=>{byId('status').textContent=text;byId('status').dataset.tone=attention?'attention':'neutral';};
  const element=(tag,text,parent,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;if(parent)parent.append(e);return e;};
  const titles={requests:'Access requests',grants:'Device sessions',activity:'Successful changes',invites:'Invites'};
  const descriptions={requests:'Verify the requester before choosing their role and issuing a single-use key.',grants:'Signed in / recently online means an authenticated session checked in within three minutes. It does not indicate a person is typing. Update contact is separate.',activity:'Reported successful changes contain operation metadata and counts. Record contents and passwords are not included.',invites:'When the listener enforces invites (inviteRequired is true in listener-status.json), a tester needs an invite code to request access. Each code works for a limited number of requests until it expires, and is shown only once when issued. Testers type it into the access request form.'};
  const reveal={key:{title:'Single-use access key',note:'Give this key only to the requester you verified. It expires in ten minutes and works only for that request and device.',field:'activationKey'},
    invite:{title:'Invite code',note:'Give this code only to the tester you invited. It is shown once and cannot be shown again; revoke it here if it is lost or shared. The tester types it into the access request form.',field:'code'}};
  // error.unanswered: fetch rejected with no HTTP status and not by our own timeout. A busy dashboard (all request slots taken) gives no answer, and so
  // does one that ended; either way the session is kept, so closing the window still ends it on the server. Only a 401 ends it here.
  async function call(route,value,wait=12000){
    const token=bearer,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),wait);
    try{const response=await fetch(route,{method:'POST',credentials:'omit',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',...(bearer?{'Authorization':'Bearer '+bearer,'X-Admin-CSRF':csrf}:{})},body:JSON.stringify(value)}).catch(error=>{if(error.name!=='AbortError'){error.unanswered=true;if(token===bearer)notAnswering();}throw error;});if(token===bearer)answering();if(!response.ok){const error=new Error('refused');error.status=response.status;error.body=await response.json().catch(()=>({}));throw error;}return await response.json();}finally{clearTimeout(timer);}
  }
  // Say so and look again every 10 s, in the view that is open, until the dashboard answers (any answer clears the line).
  function notAnswering(){const line=byId('unanswered');line.textContent=noAnswer;line.hidden=false;clearTimeout(unansweredTimer);unansweredTimer=setTimeout(()=>{if(bearer)poll();},10000);}
  function answering(){byId('unanswered').hidden=true;clearTimeout(unansweredTimer);}
  // True when the failure is dealt with: a 401 ends the session; no answer is already on the page (the line above), so nothing else may blame Docker for it.
  const lost=error=>{if(error.status===401)showExpired(true);return error.status===401||Boolean(error.unanswered);};
  function lock(value){busy=value;byId('refresh').disabled=value||!bearer;byId('end-session').disabled=value||!bearer;for(const button of document.querySelectorAll('[data-action]'))button.disabled=value||!bearer;}
  function remember(){for(const card of byId('requests').querySelectorAll('[data-request]')){const role=card.querySelector('select');if(role)drafts.set(card.dataset.request,{role:role.value,districts:[...card.querySelectorAll('input:checked')].map(c=>c.value)});}}
  async function refresh(){const token=bearer,data=await call('/dashboard',{});if(token!==bearer)return;remember();snapshot=data;render();status('Updated '+new Date(data.serverTime*1000).toLocaleTimeString()+'.');}
  async function action(callback){if(busy)return;lock(true);try{if(await callback()!==false)await refresh();}catch{status('The action response was not confirmed. Refresh to check its state before trying again. Reopen Melange Admin if the session expired.',true);}finally{lock(false);}}
  function button(parent,label,run,secondary=false){const b=element('button',label,parent,secondary?'secondary':'');b.type='button';b.dataset.action='';b.disabled=busy;b.addEventListener('click',()=>action(run));return b;}
  function heading(parent,app,name,detail,state,kind=''){const h=element('div',undefined,parent,'row-head');if(apps[app]){const logo=element('img',undefined,h);logo.src='/icons/'+app+'.ico';logo.alt=apps[app];}const text=element('div',undefined,h,'row-heading');element('strong',name+(app?' · '+(apps[app]||app):''),text);element('div',detail,text,'details');element('span',state,h,'badge '+kind);}
  function showKey(result,kind='key'){const r=reveal[kind];byId('key-title').textContent=r.title;byId('key-note').textContent=r.note;byId('key-value').textContent=result[r.field];byId('key').showModal();}
  function empty(parent,title,text){const e=element('div',undefined,parent,'empty');element('h2',title,e);element('p',text,e);}
  function selected(rows){const app=byId('app-scope').value;return rows.filter(row=>app==='all'||row.app===app);}

  // ---- formatting -------------------------------------------------------------------------------------------------
  const pad=n=>String(n).padStart(2,'0'), clock=d=>pad(d.getHours())+':'+pad(d.getMinutes());
  function dayName(epoch){const d=new Date(epoch*1000),now=new Date(),days=Math.round((new Date(now.getFullYear(),now.getMonth(),now.getDate())-new Date(d.getFullYear(),d.getMonth(),d.getDate()))/864e5);return days===0?'Today':days===1?'Yesterday':d.toLocaleDateString(undefined,{month:'short',day:'numeric'});}
  const when=epoch=>dayName(epoch)+' '+clock(new Date(epoch*1000));
  const short=epoch=>dayName(epoch)==='Today'?clock(new Date(epoch*1000)):when(epoch);
  const whenLower=epoch=>{const text=when(epoch);return /^(Today|Yesterday)/.test(text)?text[0].toLowerCase()+text.slice(1):text;};
  function ago(epoch){const s=Math.max(0,Math.round(Date.now()/1000-epoch));if(s<45)return'just now';const m=Math.round(s/60);if(m<60)return m+(m===1?' minute':' minutes')+' ago';const h=Math.round(m/60);if(h<48)return h+(h===1?' hour':' hours')+' ago';return Math.round(h/24)+' days ago';}
  const size=n=>n==null?'—':n>=1e8?(n/1e9).toFixed(1)+' GB':Math.max(1,Math.round(n/1e6))+' MB';
  function span(seconds){const d=Math.floor(seconds/86400),h=Math.floor(seconds%86400/3600),m=Math.floor(seconds%3600/60);return d?d+' d '+h+' h':h?h+' h '+m+' min':m+' min';}
  const numbers=['No','One','Two','Three','Four','Five','Six','Seven','Eight','Nine'];
  const verbs={start:'started',restart:'restarted',update:'updated',backup:'backed up',invite:'invited someone to'};
  const actor=name=>name==='admin'?'Administrator':name;
  const stateWord={running:'Running',update_ready:'Update ready',stopped:'Stopped',unhealthy:'Not healthy'};
  const stateTone={running:'ok',update_ready:'warn',stopped:'bad',unhealthy:'bad'};
  const doing={start:'Starting',restart:'Restarting',update:'Updating',backup:'Backing up'}, names={start:'Start',restart:'Restart',update:'Update',backup:'Backup'};
  const readyText=row=>row.readyVersion?row.readyVersion+' is ready':'A newer release is ready';
  function activityText(a){const name=a.name||a.service;return a.outcome==='succeeded'?actor(a.actor)+' '+(verbs[a.action]||a.action)+' '+name:name+' '+a.action+' did not finish';}
  function secondLine(row){
    if(row.busy)return 'In progress since '+clock(new Date(row.busy.since*1000));
    if(row.last&&row.last.outcome==='failed')return (names[row.last.action]||'The action')+' did not finish'+(row.last.message?': '+row.last.message:'');
    if(row.state==='update_ready')return readyText(row);
    if(row.state==='stopped')return 'since '+clock(new Date(row.since*1000))+(row.exitCode?', exited with code '+row.exitCode:'')+(row.update?' · '+readyText(row):'');
    return row.note||'';
  }
  function statusCell(parent,row){
    if(row.state==='missing'){element('span','Not set up in Docker',parent,'muted');return;}
    const tone=row.busy?'warn':stateTone[row.state],s=element('span',undefined,parent,'status '+tone);element('span',undefined,s,'dot');element('span',row.busy?doing[row.busy.action]||'Working':stateWord[row.state],s,'word');
    const line=secondLine(row);if(line)element('span',line,parent,'sub-line'+(row.state==='update_ready'&&!row.busy&&!(row.last&&row.last.outcome==='failed')?' dup':''));
  }
  function backupText(row){return row.backup==='not_set_up'?'Not set up':row.lastBackup?when(row.lastBackup):'No backup yet';}

  // ---- Apps list --------------------------------------------------------------------------------------------------
  const keepFocus=draw=>{const key=document.activeElement?.dataset?.key;draw();if(key)document.querySelector('[data-key="'+CSS.escape(key)+'"]')?.focus({preventScroll:true});};
  function say(text,attention=false){const e=byId('apps-message');e.hidden=!text;e.textContent=text||'';e.dataset.tone=attention?'attention':'neutral';}
  // True when the list on screen is not the latest: the server answered with its last good list (stale), or a request failed while one was showing.
  const lateApps=()=>Boolean(appsData&&appsData.docker.ok&&(appsData.stale||slow));
  function drawStale(){
    let line=byId('apps-stale');
    if(!line){line=element('p',undefined,undefined,'stale-line');line.id='apps-stale';line.setAttribute('role','status');byId('apps-table').before(line);}
    const late=lateApps();line.hidden=!late;
    line.textContent=late?'Showing the list from '+clock(new Date(appsData.checkedAt*1000))+'. '+(dockerWaiting()?'Starting Docker Desktop. This can take a minute or two.':appsData.docker.message||'Docker is slow to answer; trying again.'):'';
    line.title=late&&appsData.staleReason||'';
  }
  function renderSub(){if(!appsData)return;const n=appsData.services.length;byId('apps-sub').textContent=(appsData.docker.ok?(numbers[n]||n)+(n===1?' service':' services')+' on this computer. ':'')+'Last checked '+ago(appsData.checkedAt)+'.';}
  function rowActions(parent,row){
    const cell=element('div',undefined,parent,'row-actions');
    for(const name of row.actions){
      if(name==='open'){const a=element('a','Open web service',cell,'link');a.href=row.openUrl;a.target='_blank';a.rel='noopener noreferrer';a.dataset.key='open-'+row.service;continue;}
      const primary=name==='update'||(name==='start'&&!row.actions.includes('update')),b=element('button',name==='logs'?'View logs':name[0].toUpperCase()+name.slice(1),cell,primary?'btn primary':'link');b.type='button';b.disabled=appsWorking;b.dataset.key=name+'-'+row.service;
      b.addEventListener('click',()=>name==='logs'?openDetail(row.service,'logs'):name==='update'?confirmUpdate(row):runAction(row.service,name));
    }
  }
  function renderApps(){
    if(!appsData)return;
    keepFocus(()=>{
      renderSub();drawStale();byId('recent').hidden=byId('timeline').hidden=false;
      const ok=appsData.docker.ok,waiting=dockerWaiting(),starter=byId('start-docker');byId('apps-table').hidden=!ok;
      say(ok?appsData.notice:waiting?'Starting Docker Desktop. This can take a minute or two.':appsData.docker.message,!ok&&!waiting);
      starter.hidden=!appsData.docker.canStart;starter.disabled=appsWorking||waiting;
      const body=byId('apps-body');body.replaceChildren();
      for(const row of ok?appsData.services:[]){
        const tr=element('tr',undefined,body,'app-row'),name=element('td',undefined,tr,'c-name');if(row.terrier&&expanded.has(row.service)&&row.state!=='missing')tr.classList.add('open');
        if(row.terrier){const chevron=element('button',undefined,name,'chevron');chevron.type='button';chevron.dataset.key='chevron-'+row.service;chevron.setAttribute('aria-expanded',String(expanded.has(row.service)));chevron.setAttribute('aria-label','Show details for '+row.name);chevron.append(document.createElementNS('http://www.w3.org/2000/svg','svg'));chevron.firstChild.setAttribute('viewBox','0 0 16 16');const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',expanded.has(row.service)?'M3 6l5 5 5-5':'M6 3l5 5-5 5');chevron.firstChild.append(path);chevron.addEventListener('click',()=>{expanded.has(row.service)?expanded.delete(row.service):expanded.add(row.service);renderApps();});}
        const link=element('button',row.name,name,'svc-name');link.type='button';link.dataset.key='name-'+row.service;link.addEventListener('click',()=>openDetail(row.service,'overview'));
        const st=element('td',undefined,tr,'c-status');statusCell(st,row);
        const meta=[row.version,row.update?readyText(row):null,row.backup==='supported'&&row.lastBackup?'backed up '+whenLower(row.lastBackup):null].filter(Boolean);if(meta.length)element('span',meta.join(' · '),st,'stack-meta');
        if(row.state==='missing'){element('td',undefined,tr,'c-version');element('td',undefined,tr,'c-backup');element('td',undefined,tr,'c-disk');element('td',undefined,tr);continue;}
        element('td',row.version||'—',tr,'c-version');element('td',backupText(row),tr,'c-backup'+(row.backup==='supported'&&row.lastBackup?'':' muted'));element('td',size(row.disk),tr,'c-disk');
        rowActions(element('td',undefined,tr,'c-actions'),row);
        if(row.terrier&&expanded.has(row.service)){const more=element('tr',undefined,body,'app-more'),cell=element('td',undefined,more);cell.colSpan=6;element('p','Everyone with a Terrier account can work in every district.',cell,'muted');const manage=element('button','Manage accounts',cell,'link');manage.type='button';manage.dataset.key='accounts-'+row.service;manage.addEventListener('click',()=>openDetail(row.service,'accounts'));}
      }
      const line=byId('timeline');line.replaceChildren();
      if(!appsData.activity.length)element('li','Nothing has been changed from this page yet.',line,'muted');
      for(const a of appsData.activity){const li=element('li',undefined,line);element('span',short(a.at),li,'time');element('span',activityText(a),li);}
    });
    if(view==='app'&&current){renderDetail();}
  }
  const anyBusy=()=>Boolean(appsData&&appsData.services.some(s=>s.busy)||view==='app'&&detail&&detail.service&&detail.service.busy);
  const isLate=()=>Boolean(slow||(view==='app'?detail&&detail.stale:appsData&&appsData.stale));
  // True for three minutes after Start Docker Desktop was chosen, until Docker answers and the services have settled (those that were running come back by themselves).
  const dockerWaiting=()=>Boolean(dockerStarted&&Date.now()-dockerStarted<18e4&&!(appsData&&appsData.docker.ok&&!appsData.stale&&appsData.services.every(s=>s.state==='running'||s.state==='update_ready')));
  function keepPolling(delay=2500){clearTimeout(appsTimer);if(!bearer)return;if(isLate())appsTimer=setTimeout(poll,10000);else if(anyBusy())appsTimer=setTimeout(poll,delay);else if(dockerWaiting())appsTimer=setTimeout(poll,4000);}
  async function loadApps(){
    const token=bearer;
    try{const data=await call('/apps',{});if(token!==bearer)return false;appsData=data;slow=false;}
    catch(error){
      if(token!==bearer)return false;
      if(lost(error)){if(error.unanswered){clearTimeout(appsTimer);appsTimer=setTimeout(poll,10000);}return false;}   // its own retry: any later answer clears the shared one
      if(appsData&&appsData.docker.ok&&!(error.body&&error.body.message)){slow=true;drawStale();keepPolling();return false;}   // keep the list that is showing
      if(!appsData)byId('apps-sub').textContent='Local services have not been checked yet.';
      const timedOut=error.name==='AbortError';   // our own timeout: slow, not silent, so it looks again by itself
      say(error.body&&error.body.message?error.body.message:'Could not read the services. '+(timedOut?'It will try again by itself; choose Check now to try sooner.':'Choose Check now to try again.'),true);keepPolling();
      if(timedOut){clearTimeout(appsTimer);appsTimer=setTimeout(poll,10000);}
      return false;
    }
    renderApps();keepPolling();
    return true;
  }
  async function poll(){if(polling)return;polling=true;try{if(view==='app'&&current)await loadDetail();else await loadApps();}finally{polling=false;}}
  async function runAction(service,name){
    if(appsWorking)return;appsWorking=true;let problem;
    try{await call('/apps/action',{service,action:name});say('');await (view==='app'?loadDetail():loadApps());clearTimeout(appsTimer);appsTimer=setTimeout(()=>poll(),1500);}
    catch(error){if(lost(error))return;const body=error.body||{};problem=body.error==='busy'?'Another action is already running for this service.':body.message||'The action could not start. Check Docker, then try again.';}
    finally{appsWorking=false;if(appsData)renderApps();if(problem)say(problem,true);}   // after renderApps, which rewrites the message line
  }
  function ask({title,text,rows,ok}){
    return new Promise(resolve=>{
      const dialog=byId('confirm');byId('confirm-title').textContent=title;byId('confirm-text').textContent=text;byId('confirm-ok').textContent=ok;
      const list=byId('confirm-rows');list.replaceChildren();for(const [label,value] of rows){const r=element('div',undefined,list);element('dt',label,r);element('dd',value,r);}
      const finish=answer=>{byId('confirm-ok').onclick=byId('confirm-cancel').onclick=null;dialog.onclose=null;if(dialog.open)dialog.close();resolve(answer);};
      byId('confirm-ok').onclick=()=>finish(true);byId('confirm-cancel').onclick=()=>finish(false);dialog.onclose=()=>finish(false);dialog.showModal();
    });
  }
  async function confirmUpdate(row){
    const ok=await ask({title:'Update '+row.name+'?',text:'From '+(row.version||'the running release')+' to '+(row.readyVersion||'the newer release')+'. '+(row.state==='stopped'?row.name+' is stopped and starts on the new release.':row.name+' restarts and is unavailable for about a minute.')+' Your data stays where it is.',
      rows:[['Release',row.readyVersion||'Newer than the running release'],['Image','The image the compose file pins'],['Recorded in','Successful changes']],ok:'Update now'});
    if(ok)runAction(row.service,'update');
  }

  // ---- Service detail ---------------------------------------------------------------------------------------------
  function stopFollow(){clearTimeout(followTimer);followTimer=undefined;followGen++;}
  async function openDetail(service,name){current=service;tab=name;view='app';detail=undefined;logLines=[];logTop=undefined;stopFollow();say('');byId('apps-list').hidden=true;byId('app-detail').hidden=false;byId('app-detail').replaceChildren();await loadDetail();
    if(detail&&view==='app'&&current===service&&tab==='logs')enterLogs();}
  function backToList(){stopFollow();current=undefined;detail=undefined;view='apps';byId('app-detail').hidden=true;byId('apps-list').hidden=false;loadApps();}
  async function loadDetail(){
    // Only the newest request's answer is used: one for a service no longer open, or from an earlier visit or an overlapping poll, is dropped
    const service=current,gen=++detailGen;
    try{const made=await call('/apps/detail',{service});if(gen!==detailGen||service!==current||view!=='app')return;detail=made;slow=false;}
    catch(error){if(lost(error))return;if(gen!==detailGen||service!==current||view!=='app')return;if(detail&&!(error.body&&error.body.message)){slow=true;renderDetail();keepPolling();return;}say('Could not read this service. Choose Apps and try again.',true);keepPolling();return;}
    appsData=appsData||{services:[],activity:[],docker:{ok:true},checkedAt:Date.now()/1000};
    const index=appsData.services.findIndex(s=>s.service===current);if(index>=0)appsData.services[index]=detail.service;
    renderDetail();keepPolling();
  }
  function kv(parent,rows){const dl=element('dl',undefined,parent,'kv');for(const [label,value,muted] of rows){const r=element('div',undefined,dl);element('dt',label,r);element('dd',value,r,muted?'muted':'');}return dl;}
  function mountLabel(m){const known={'/state':'Accounts and saved work','/var/lib/mentat':'Mentat state','/var/cache/mentat':'Mentat cache'};return known[m.destination]||(m.readOnly?'Data, read only':m.destination);}
  function healthText(h){return h.status==='passing'?'Passing'+(h.checkedAt?', checked '+ago(h.checkedAt):''):h.status==='failing'?'Failing'+(h.checkedAt?', checked '+ago(h.checkedAt):''):h.status==='starting'?'Starting':'No health check';}
  function renderDetail(){
    if(!detail||view!=='app')return;
    const row=detail.service,box=byId('app-detail');
    keepFocus(()=>{
      box.replaceChildren();
      const crumbs=element('div',undefined,box,'crumbs'),back=element('button','Apps',crumbs,'link');back.type='button';back.dataset.key='back';back.addEventListener('click',backToList);element('span','/',crumbs);element('span',row.name,crumbs);
      const head=element('header',undefined,box,'page-head'),title=element('div',undefined,head),h1=element('h1',row.name,title);
      if(tab==='overview'&&row.state!=='missing'){const s=element('span',undefined,h1,'status '+(row.busy?'warn':stateTone[row.state]));element('span',undefined,s,'dot');element('span',row.busy?doing[row.busy.action]||'Working':stateWord[row.state],s,'word');
        const bits=[row.state==='stopped'?'Stopped since '+whenLower(row.since):row.since?'Running since '+whenLower(row.since):null,row.version?'version '+row.version:null,row.update?readyText(row):'up to date'].filter(Boolean);element('p',bits.join(' · '),title,'sub');}
      const acts=element('div',undefined,head,'head-actions');
      const put=(label,run,primary,key)=>{const b=element('button',label,acts,primary?'btn primary':'btn secondary');b.type='button';b.disabled=appsWorking||Boolean(row.busy);b.dataset.key=key;b.addEventListener('click',run);return b;};
      if(tab==='overview'){
        if(row.actions.includes('restart'))put('Restart',()=>runAction(row.service,'restart'),false,'d-restart');
        if(row.backup==='supported'&&row.state!=='missing'&&row.state!=='stopped')put('Back up now',()=>runAction(row.service,'backup'),false,'d-backup');
        if(row.actions.includes('start'))put('Start',()=>runAction(row.service,'start'),!row.actions.includes('update'),'d-start');
        if(row.actions.includes('update'))put('Update',()=>confirmUpdate(row),true,'d-update');
        if(row.openUrl&&row.actions.includes('open')){const a=element('a','Open '+row.name,acts,row.actions.includes('update')?'btn secondary':'btn primary');a.href=row.openUrl;a.target='_blank';a.rel='noopener noreferrer';a.dataset.key='d-open';}
      }else if(tab==='accounts')put('Invite someone',()=>{inviteOpen=true;byId('invite-form')?.removeAttribute('hidden');byId('invite-email')?.focus();},true,'d-invite');
      const tabs=element('div',undefined,box,'tabs');tabs.setAttribute('role','tablist');
      for(const [id,label] of [['overview','Overview'],['logs','Logs'],...(detail.accounts.supported?[['accounts','Accounts']]:[])]){const t=element('button',label,tabs,'tab');t.type='button';t.setAttribute('role','tab');t.setAttribute('aria-selected',String(tab===id));t.dataset.key='tab-'+id;t.addEventListener('click',()=>{tab=id;stopFollow();logTop=undefined;renderDetail();if(id==='logs')enterLogs();});}
      if(detail.stale||slow)element('p','Showing this service as of '+clock(new Date(detail.checkedAt*1000))+'. Docker is slow to answer; trying again.',box,'stale-line').title=detail.staleReason||'';
      if(row.state==='missing'){element('p','Not set up in Docker',box,'muted');return;}
      if(detail.notice)element('p',detail.notice,box,'notice-line');
      if(row.last&&row.last.outcome==='failed')element('p',secondLine(row),box,'notice-line');
      if(tab==='overview')renderOverview(box,row);else if(tab==='logs')renderLogs(box);else renderAccounts(box);
    });
  }
  function renderOverview(box,row){
    const cols=element('div',undefined,box,'cols'),left=element('div',undefined,cols),right=element('div',undefined,cols);
    element('h2','Health',left,'section');
    kv(left,[['Health check',healthText(detail.health||{status:'none'}),!detail.health],['Uptime',detail.uptime!=null?span(detail.uptime):'—'],
      ['Image',detail.image?(detail.image.update?readyText(row):row.version?'Matches release '+row.version:detail.image.pinnedByDigest?'Pinned by digest':'Pinned by the compose file'):'—']]);
    element('h2','Storage',right,'section');
    kv(right,(detail.storage||[]).map(m=>[mountLabel(m),m.bytes!=null?size(m.bytes):m.type==='bind'?'On this computer':'—',m.bytes==null]));
    element('h2','Backups',right,'section');
    kv(right,row.backup==='supported'?[['Last backup',row.lastBackup?when(row.lastBackup)+', finished':'No backup yet',!row.lastBackup],['Stored in','The data volume, backups folder']]:[['Backups','Not set up',true]]);
  }

  // ---- Logs -------------------------------------------------------------------------------------------------------
  function renderLogs(box){
    const bar=element('div',undefined,box,'log-bar'),count=element('select',undefined,bar);count.setAttribute('aria-label','Number of lines');count.dataset.key='log-count';for(const n of [100,200,500]){const o=element('option','Last '+n+' lines',count);o.value=String(n);}count.value=String(logOptions.count);
    count.addEventListener('change',()=>{logOptions.count=Number(count.value);drawLogs();});
    const sw=element('label',undefined,bar,'switch'),box2=element('input',undefined,sw);box2.type='checkbox';box2.setAttribute('role','switch');box2.checked=logOptions.follow;element('span',undefined,sw,'track');element('span','Follow',sw);
    box2.addEventListener('change',()=>{logOptions.follow=box2.checked;if(logOptions.follow){startFollow();drawLogs();}else stopFollow();});
    const filter=element('input',undefined,bar,'filter');filter.type='search';filter.placeholder='Filter lines';filter.setAttribute('aria-label','Filter lines');filter.dataset.key='log-filter';filter.value=logOptions.filter;filter.addEventListener('input',()=>{logOptions.filter=filter.value;drawLogs();});
    const dl=element('button','Download',bar,'btn secondary');dl.type='button';dl.addEventListener('click',download);
    const panel=element('div',undefined,box,'log-panel');panel.id='log-panel';panel.tabIndex=0;panel.setAttribute('role','log');panel.setAttribute('aria-label','Logs');
    panel.addEventListener('scroll',()=>{logTop=panel.scrollHeight-panel.scrollTop-panel.clientHeight<40?undefined:panel.scrollTop;});   // survives the rebuild a poll does
    const note=element('p',undefined,box,'hint');note.append('Passwords, keys and tokens are replaced with ');element('code','[redacted]',note);note.append(' before they reach this page.');
    drawLogs();   // a poll redraws this tab: loading and following start once, when the tab is entered (enterLogs)
  }
  const stamp=epoch=>{if(!epoch)return'';const d=new Date(epoch*1000);return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+' '+pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());};
  function visibleLines(){const needle=logOptions.filter.trim().toLowerCase();return logLines.slice(-logOptions.count).filter(l=>!needle||(l.level+' '+l.text).toLowerCase().includes(needle));}
  function drawLogs(){
    const panel=byId('log-panel');if(!panel)return;
    const lines=visibleLines();panel.replaceChildren();
    if(!lines.length)element('p',logLines.length?'No lines match the filter.':'No log lines yet.',panel,'muted');
    for(const l of lines){
      const line=element('div',undefined,panel,'log-line'+(l.level==='WARN'||l.level==='ERROR'?' '+l.level.toLowerCase():''));
      element('span',stamp(l.ts),line,'ts');element('span',l.level,line,'lvl');
      const msg=element('span',undefined,line,'msg');for(const part of l.text.split(/(\[redacted\])/))if(part==='[redacted]')element('span',part,msg,'redacted');else if(part)msg.append(part);
    }
    panel.scrollTop=logOptions.follow||logTop===undefined?panel.scrollHeight:logTop;
  }
  async function loadLogs(first=false){
    if(logsBusy||view!=='app'||tab!=='logs')return;
    logsBusy=true;const service=current;
    try{const made=await call('/apps/logs',{service});if(service!==current||view!=='app'||tab!=='logs')return;logLines=made.lines;}
    catch(error){if(lost(error))return;if(service!==current||view!=='app'||tab!=='logs')return;if(first&&error.status!==409)say('Could not read the logs for this service.',true);return;}
    finally{logsBusy=false;}
    drawLogs();
  }
  function startFollow(){stopFollow();const gen=followGen;(async function tick(){await loadLogs(true);if(gen===followGen)followTimer=setTimeout(tick,5000);})();}
  function enterLogs(){if(logOptions.follow)startFollow();else loadLogs(true);}
  function download(){
    const text=visibleLines().map(l=>stamp(l.ts)+' '+l.level+' '+l.text).join('\n')+'\n',d=new Date(),a=element('a');
    a.href=URL.createObjectURL(new Blob([text],{type:'text/plain'}));a.download=current+'-logs-'+d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate())+'-'+pad(d.getHours())+pad(d.getMinutes())+'.txt';
    document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  }

  // ---- Accounts (Terrier) -----------------------------------------------------------------------------------------
  function renderAccounts(box){
    element('p','Everyone with a Terrier account can work in every district.',box,'lead muted');
    const form=element('form',undefined,box,'inline-form');form.id='invite-form';form.hidden=!inviteOpen;
    const email=element('input',undefined,form);email.id='invite-email';email.type='email';email.required=true;email.maxLength=150;email.placeholder='organizer@example.org';email.setAttribute('aria-label','Email address');email.autocomplete='off';
    const role=element('select',undefined,form);role.setAttribute('aria-label','Role');for(const [value,label] of [['administrator','Administrator'],['organizer','Organizer'],['canvasser','Canvasser']]){const o=element('option',label,role);o.value=value;}role.value='organizer';
    const send=element('button','Send invite',form,'btn primary');send.type='submit';
    const cancel=element('button','Cancel',form,'link');cancel.type='button';cancel.addEventListener('click',()=>{inviteOpen=false;form.hidden=true;});
    const result=element('p',undefined,box,'notice-line');result.hidden=true;
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(appsWorking)return;appsWorking=true;send.disabled=true;result.hidden=true;
      try{const made=await call('/apps/invite',{email:email.value.trim(),role:role.value},50000);byId('link-value').textContent=made.link;byId('link').showModal();email.value='';inviteOpen=false;form.hidden=true;appsWorking=false;await loadDetail();}
      catch(error){if(lost(error))return;result.hidden=false;result.dataset.tone='attention';result.textContent=error.body&&error.body.message?error.body.message:error.status===400?'Enter a valid email address and choose a role.':'The invitation could not be created. Check that Terrier is running, then try again.';}
      finally{appsWorking=false;send.disabled=false;}
    });
    element('p','Listing accounts is not available yet. Invitations are created here and delivered by you.',box,'muted');
  }
  let inviteOpen=false;
  byId('link-close').addEventListener('click',()=>{byId('link-value').textContent='';byId('link').close();});byId('link').addEventListener('close',()=>{byId('link-value').textContent='';});

  // ---- Shell ------------------------------------------------------------------------------------------------------
  function showExpired(ended=false){
    bearer=csrf=undefined;snapshot=undefined;appsData=undefined;slow=false;clearTimeout(appsTimer);answering();stopFollow();byId('key-value').textContent='';byId('link-value').textContent='';
    for(const d of document.querySelectorAll('dialog'))if(d.open)d.close();
    if(ended){byId('expired-title').textContent='This session has ended';byId('expired-text').textContent='Open Melange Admin again from your desktop or Start menu to continue.';}
    byId('shell').hidden=true;byId('expired').hidden=false;document.title='Melange Admin';
  }
  function go(name){
    remember();stopFollow();clearTimeout(appsTimer);view=name;current=undefined;detail=undefined;slow=false;
    for(const nav of document.querySelectorAll('[data-view]')){if(nav.dataset.view===name)nav.setAttribute('aria-current','page');else nav.removeAttribute('aria-current');}
    const isApps=name==='apps';byId('apps-view').hidden=!isApps;byId('legacy-view').hidden=isApps;byId('app-detail').hidden=true;byId('apps-list').hidden=!isApps;
    byId('nav').classList.remove('open');byId('menu').setAttribute('aria-expanded','false');
    if(isApps){if(appsData)renderApps();if(bearer){loadDesktopApps();loadApps();}}else render();
  }
  function render(){
    if(!snapshot)return;
    byId('pending-count').textContent=String(snapshot.notifications.pendingRequests);
    byId('notifications').textContent=snapshot.notifications.pendingRequests+' access request(s) need review. Pending requests stay here until you review them.';
    if(!legacyViews.includes(view))return;
    const focus=document.activeElement?.id, scroll={x:scrollX,y:scrollY};
    for(const id of legacyViews){byId(id).replaceChildren();byId(id).hidden=id!==view;}
    byId('access-invite-form').hidden=view!=='invites';
    byId('view-title').textContent=titles[view];byId('view-description').textContent=descriptions[view];
    const requests=selected(snapshot.requests),grants=selected(snapshot.grants),events=selected(snapshot.activity);
    const changes=(snapshot.appActions||[]).filter(a=>a.outcome==='succeeded'&&(byId('app-scope').value==='all'||appOf[a.service]===byId('app-scope').value));
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
    const items=[...events.map(e=>({time:e.occurred,draw:()=>{const card=element('section',undefined,byId('activity'),'card');heading(card,e.app,e.display_name,new Date(e.occurred*1000).toLocaleString()+' · device '+e.device_id+(e.district_id?' · '+e.district_id:''),e.kind+' · succeeded');const summary=element('dl',undefined,card,'event-summary');for(const [key,value] of Object.entries({...JSON.parse(e.counts),...JSON.parse(e.references_json)})){element('dt',key,summary);element('dd',String(value),summary);}}})),
      ...changes.map(a=>({time:a.at,draw:()=>{const card=element('section',undefined,byId('activity'),'card');heading(card,appOf[a.service],actor(a.actor),new Date(a.at*1000).toLocaleString()+' · Apps page',a.action+' · succeeded');}}))].sort((a,b)=>b.time-a.time);
    if(!items.length)empty(byId('activity'),'No reported changes','Successful operations reported by authorized installations will appear here. This list does not imply every application action is currently reported.');
    for(const item of items)item.draw();
    if(!snapshot.invites.length)empty(byId('invites'),'No invite codes','Issue a code above and give it to a tester. Enforcement depends on the listener setting (check inviteRequired in listener-status.json); while it is on, requests without a valid code are refused before they use any capacity.');
    for(const invite of snapshot.invites){const card=element('section',undefined,byId('invites'),'card');heading(card,'',invite.label,'Used '+invite.uses+' of '+invite.maxUses+' · expires '+new Date(invite.expires*1000).toLocaleString(),invite.state,invite.state==='active'?'online':'');if(invite.state==='active'){const actions=element('div',undefined,card,'actions');button(actions,'Revoke invite',async()=>{if(confirm('Revoke this invite code? New requests using it will be refused.'))await call('/invite-revoke',{inviteId:invite.inviteId});},true);}}
    if(focus)byId(focus)?.focus({preventScroll:true});scrollTo(scroll.x,scroll.y);
  }
  byId('key-close').addEventListener('click',()=>{byId('key-value').textContent='';byId('key').close();});byId('key').addEventListener('close',()=>{byId('key-value').textContent='';});
  byId('end-session').addEventListener('click',async()=>{if(busy||!bearer)return;lock(true);try{await call('/session-close',{});bearer=csrf=undefined;byId('key-value').textContent='';byId('key').close();snapshot=undefined;appsData=undefined;drafts.clear();clearTimeout(appsTimer);stopFollow();byId('access-invite-form').hidden=true;for(const id of legacyViews)byId(id).replaceChildren();byId('apps-body').replaceChildren();byId('timeline').replaceChildren();byId('app-detail').replaceChildren();say('');byId('apps-sub').textContent='Session ended. You may close this tab. Use Melange Admin to open a fresh session.';status('Session ended. You may close this tab. Use Melange Admin to open a fresh session.');}catch{status('Could not confirm the session ended. Try End session again.');}finally{lock(false);}});
  addEventListener('pagehide',()=>{if(bearer)fetch('/session-close',{method:'POST',credentials:'omit',cache:'no-store',keepalive:true,headers:{'Content-Type':'application/json','Authorization':'Bearer '+bearer,'X-Admin-CSRF':csrf},body:'{}'}).catch(()=>{});});
  byId('refresh').addEventListener('click',async()=>{if(busy)return;lock(true);status('Refreshing…');try{await refresh();}catch{status('Could not refresh. Existing results remain visible. Reopen Melange Admin if the session expired.',true);}finally{lock(false);}});
  byId('access-invite-form').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const label=byId('access-invite-label').value.trim(),uses=Number(byId('access-invite-uses').value),days=Number(byId('access-invite-days').value);if(!label||!Number.isInteger(uses)||uses<1||uses>100||!Number.isInteger(days)||days<1||days>30){status('Enter who the invite is for, 1 to 100 uses, and 1 to 30 days.',true);return false;}showKey(await call('/invite-issue',{label,uses,days}),'invite');byId('access-invite-label').value='';});});
  byId('check-now').addEventListener('click',async()=>{if(byId('check-now').disabled||!bearer)return;byId('check-now').disabled=true;try{say('');if(view==='app'&&current)await loadDetail();else await loadApps();}finally{byId('check-now').disabled=!bearer;}});
  byId('start-docker').addEventListener('click',async()=>{
    if(appsWorking||!bearer)return;appsWorking=true;let problem;
    try{await call('/apps/docker-start',{});dockerStarted=Date.now();clearTimeout(appsTimer);appsTimer=setTimeout(poll,4000);}
    catch(error){if(lost(error))return;problem=error.body&&error.body.message?error.body.message:'Docker Desktop could not be started. Open it from the Start menu, then choose Check now.';}
    finally{appsWorking=false;if(appsData)renderApps();if(problem)say(problem,true);}   // after renderApps, which rewrites the message line
  });
  byId('app-scope').addEventListener('change',()=>{remember();render();});for(const nav of document.querySelectorAll('[data-view]'))nav.addEventListener('click',()=>go(nav.dataset.view));
  byId('desktop-refresh').addEventListener('click',loadDesktopApps);
  byId('menu').addEventListener('click',()=>{const open=byId('nav').classList.toggle('open');byId('menu').setAttribute('aria-expanded',String(open));});
  byId('expired-close').addEventListener('click',()=>{close();setTimeout(()=>{byId('expired-close').textContent='You can close this tab';byId('expired-close').disabled=true;},300);});
  setInterval(()=>{if(view==='apps'&&bearer)renderSub();},30000);
  // The requests, grants and activity views refresh themselves too; not mid-action, behind the key dialog, or while a form field is being used (the render rebuilds the cards).
  setInterval(()=>{if(['requests','grants','activity'].includes(view)&&bearer&&!busy&&!byId('key').open&&!document.activeElement?.closest('#legacy-view select, #legacy-view input'))refresh().catch(()=>{});},30000);
  const fragment=new URLSearchParams(location.hash.slice(1)),bootstrap=fragment.get('bootstrap');history.replaceState(null,'','/');
  (async()=>{
    lock(true);
    try{if(!bootstrap){showExpired();return;}const session=await call('/session',{bootstrap});bearer=session.bearer;csrf=session.csrf;byId('check-now').disabled=false;byId('apps-sub').textContent='Checking local services…';status('Loading access requests…');}
    catch{showExpired();return;}
    finally{lock(false);}
    // Authentication is complete. Each view can load without keeping session controls locked behind Docker or the access store.
    refresh().catch(()=>{if(bearer)status('Could not load access requests. Use Refresh to try again.',true);});
    loadDesktopApps();
    loadApps();
  })();
})();
