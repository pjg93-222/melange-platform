/* Compose the selected graph/roster/evidence layout before the route engines start. */
(() => {
const $=id=>document.getElementById(id),form=$('route-form'),panel=$('route-panel'),main=$('explore-panel'),aside=$('person-panel');
document.querySelector('header h1').textContent='People · routes & staff';
const routebar=document.createElement('section');routebar.className='routebar';routebar.id='find-route';routebar.setAttribute('aria-label','Find a connection');routebar.append(form);main.before(routebar);
const core=new Set(['route-origin','origin-options','route-target','target-options','route-submit','route-clear','target-label']);
const options=document.createElement('details');options.className='route-advanced';options.innerHTML='<summary>Route options & evidence model</summary>';
for(const child of [...form.children]){const forId=child.getAttribute('for');if(core.has(child.id)||forId==='route-origin'||forId==='route-target')continue;options.append(child);}
const origin=document.createElement('div');origin.className='route-input';for(const n of [...form.children])if(n.getAttribute('for')==='route-origin'||['route-origin','origin-options'].includes(n.id))origin.append(n);
const target=document.createElement('div');target.className='route-input';for(const n of [...form.children])if(n.getAttribute('for')==='route-target'||['route-target','target-options','target-label'].includes(n.id))target.append(n);
form.prepend(origin,target);form.append(options);$('route-submit').textContent='Find route';document.querySelector('.mobile-jumps a').href='#find-route';
panel.open=true;panel.querySelector('summary').textContent='Route evidence';panel.className='route-evidence';
const left=document.createElement('div');left.className='people-main';main.prepend(left);left.append($('network'));
const roster=document.createElement('section');roster.className='people-roster';roster.append($('results-title'),$('results'),document.querySelector('.pagination'));left.append(roster);$('results-title').textContent='Staff & people roster';
left.append($('groups-panel'));document.querySelector('header .brand')?.remove();document.querySelector('header .divider')?.remove();
window.MelangeRoster={render(out,rows,select){
const add=(parent,tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text||'';e.className=cls||'';parent.append(e);return e;};
const head=add(out,'div','','roster-head');for(const title of ['Name','Role / office','Source date','Contact'])add(head,'span',title);
for(const row of rows){const b=add(out,'button','','roster-row'),parts=row[2].split(' · '),date=parts.find(p=>/^\d{4}-\d{2}-\d{2}/.test(p));add(b,'span',row[1],'roster-name');add(b,'span',parts.filter(p=>p!==date&&!/Current as reported/.test(p)).join(' · ')||'Role unavailable','roster-role');add(b,'span',date||'Unknown','roster-date');add(b,'span',(row[12]||[]).map(c=>c.value).join(' · ')||'Not listed','roster-contact');b.onclick=()=>{for(const x of out.querySelectorAll('.roster-row'))x.classList.toggle('selected',x===b);select(row);};}
if(!rows.length)add(out,'p','No people match this search.');
}};
window.addEventListener('staffer-person-ready',()=>{const facts=document.querySelector('#detail .person-facts');if(!facts)return;const empty=[...facts.children].filter(n=>n.tagName==='DETAILS'&&/\(0\)$/.test(n.querySelector('summary')?.textContent||''));if(!empty.length)return;const group=document.createElement('details');group.innerHTML='<summary>Coverage details · '+empty.length+' categories not recorded</summary>';for(const n of empty)group.append(n);facts.append(group);});
})();
