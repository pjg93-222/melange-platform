/* Original dossier markup, models and handlers; desk-profile.js (build 04) lays the dossier out as a tabbed page. */
(() => {
const query=new URLSearchParams(location.search), initial=query.get('deskview')||'ranked';
const routes={ranked:'members',bills:'member-bills',layers:'spending',efficiency:'investment',coalition:'coalition',contact:'contact'};
const originalOpen=openMember,originalView=setView;
const RETURN_KEY='mlk-members-return';
/* Build 04: leaving the desk goes through the shell (SPA, research scope kept) when the kit is present. */
function navigate(view,extra={}){if(window.MLK&&MLK.navigate&&MLK.url)MLK.navigate(MLK.url(view,extra));else window.restoredNavigate(view,extra);}
/* Build 04: re-opening the member already on screen (legacy note Save / Cancel) keeps the current tab. */
openMember=i=>{let tab='overview';try{const cur=new URLSearchParams(parent.location.search);if(initial==='member'&&cur.get('view')==='profile'&&cur.get('m')===M[i].name&&cur.get('tab'))tab=cur.get('tab');}catch(e){}navigate('profile',{m:M[i].name,tab});};
setView=v=>{if(v==='member')return originalView(v);navigate(routes[v]||'members');};
document.querySelectorAll('#viewseg button').forEach(b=>b.onclick=()=>navigate(routes[b.dataset.v]));
document.getElementById('exportbtn').textContent='Notes & exports';
document.getElementById('exportbtn').onclick=()=>navigate('exports');
/* Build 04: while the Members list is open, remember the shell address (with whatever filters it carries) at every
   click or Enter, so the profile's "← Members" link returns to the list as it was. */
if(initial==='ranked'){const remember=()=>{try{const p=new URLSearchParams(parent.location.search);if(p.get('view')==='members')sessionStorage.setItem(RETURN_KEY,parent.location.pathname+parent.location.search);}catch(e){}};remember();document.addEventListener('click',remember,true);document.addEventListener('keydown',e=>{if(e.key==='Enter')remember();},true);}
/* Build 01 hook: window.MelangeDeskViews[name] = {render(section, ctx)} renders deskviews the legacy desk does not know
   (compare) or replaces one it does (efficiency: ctx.mode is 'investment' or 'spending'). Overlay scripts load before this file. */
const REGISTRY=window.MelangeDeskViews=window.MelangeDeskViews||{};
const LEGACY_VIEWS=['ranked','bills','layers','efficiency','coalition','contact','member'];
const registered=name=>Object.prototype.hasOwnProperty.call(REGISTRY,name)?REGISTRY[name]:null;
let supportLoad=null;
function loadSupport(){if(!supportLoad)supportLoad=fetch('/staffer-live/approved/data/desk-support.json').then(r=>{if(!r.ok)throw Error('Records could not load.');return r.json();}).then(d=>{window.__melangeProfileMerge(d);return d;}).catch(e=>{supportLoad=null;throw e;});return supportLoad;}
async function loadRegistered(name,view){
 document.querySelectorAll('.view').forEach(v=>{v.hidden=true;});
 let section=document.getElementById('view-'+name);
 if(!section){section=document.createElement('section');section.className='view';section.id='view-'+name;section.dataset.deskView=name;const views=document.querySelectorAll('.view'),last=views[views.length-1];if(last)last.after(section);else document.body.append(section);}
 section.hidden=false;
 const ctx={name,query,mode:query.get('mode')==='spending'?'spending':'investment',section,navigate,originalView,originalOpen,loadSupport,M,D};
 try{if(view&&typeof view.render==='function')await view.render(section,ctx);else section.innerHTML='<p class="mlk-state" role="status">This view is not available in this build.</p>';}
 catch(e){section.innerHTML='';const status=document.createElement('p');status.className='mlk-state mlk-state--error';status.setAttribute('role','alert');status.textContent=(e&&e.message||'This view could not load.')+' ';const retry=document.createElement('button');retry.className='mlk-btn';retry.textContent='Retry';retry.onclick=()=>loadRegistered(name,view);status.append(retry);section.append(status);}
}
/* Build 04: the profile is a page. The legacy slide-over state (body.mel-over-open, the scrim, Escape -> ranked list)
   is switched off, the member is laid out by desk-profile.js, and an unknown member gets the board's empty state. */
function mountProfile(i){
 originalOpen(i);
 document.body.classList.remove('mel-over-open');document.getElementById('view-ranked').hidden=true;
 const scrim=document.getElementById('melscrim');if(scrim)scrim.removeAttribute('data-open');
 if(!window.MelangeProfile||typeof MelangeProfile.mount!=='function')throw Error('The profile layout could not load.');
 MelangeProfile.mount(i,{query,navigate});
}
function showUnknown(){
 const view=document.getElementById('view-member');view.hidden=false;
 if(window.MelangeProfile&&typeof MelangeProfile.unknown==='function')MelangeProfile.unknown(document.getElementById('memberbody'));
 else document.getElementById('memberbody').textContent='No member matches this link';
}
async function load(){
 const isProfile=initial==='member';
 if(registered(initial)||!LEGACY_VIEWS.includes(initial)){await loadRegistered(initial,registered(initial));return;}
 if(initial==='efficiency'){originalView(query.get('mode')==='spending'?'layers':'efficiency');return;}
 if(!isProfile&&['ranked','layers'].includes(initial)){originalView(initial);return;}
 const i=isProfile?resolveMemberAddress(query.get('member')):null;
 if(isProfile&&i==null){showUnknown();return;}
 const status=document.createElement('p');status.className='restore-loading';status.setAttribute('role','status');status.textContent=isProfile?'Loading member dossier…':'Loading bill and staff records…';document.body.append(status);
 try{
  const response=await fetch('/staffer-live/approved/data/desk-support.json');if(!response.ok)throw Error('Records could not load.');
  window.__melangeProfileMerge(await response.json());
  if(isProfile){
   const r=await fetch('/staffer-live/approved/data/member-'+i+'.json');if(!r.ok)throw Error('Member record could not load.');
   Object.assign(M[i],await r.json());mountProfile(i);
  }else originalView(initial);
  status.remove();
 }catch(e){status.textContent=e.message+' ';const retry=document.createElement('button');retry.className='mlk-btn';retry.textContent='Retry';retry.onclick=()=>{status.remove();load();};status.append(retry);}
}
if(initial==='member')document.body.classList.add('dossier-page','mlp-page');
if(['member','bills','coalition'].includes(initial)||registered(initial)||!LEGACY_VIEWS.includes(initial))document.getElementById('view-ranked').hidden=true;
load();
})();
