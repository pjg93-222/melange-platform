/* Bill and coalition selection reuse the established repository and storage. */
(() => {
  'use strict';
  const UI=window.MelangeUI;if(!UI?.desk)return;
  const {desk,el,esc,table,sourceLink,date,missing}=UI;
  const $=(s,p=document)=>p.querySelector(s),$$=(s,p=document)=>[...p.querySelectorAll(s)];
  function bills(){
    const list=$('#billrepo');if(!list)return;
    const split=el('div','ml-bill-workspace'),detail=el('aside','ml-side ml-member-bill-detail');
    list.before(split);split.append(list,detail);
    let selected=new URL(location.href).searchParams.get('bill');
    function show(key,push=false){const b=desk.bills().find(b=>b.key===key);if(!b){detail.innerHTML=missing('Select a bill to inspect its source record.');return;}selected=key;
      const live=b.live||{},people=[...(live.sponsor?.name?[{...live.sponsor,role:'Sponsor',date:live.introduced_date}]:[]),...(live.cosponsors||[]).map(p=>({...p,role:'Cosponsor'}))];
      detail.innerHTML=`<span class="ml-kind">Public record · ${b.congress}th Congress</span><h2>${esc(b.display)}</h2><h3>${esc(b.title)}</h3><p>${sourceLink(b.url||live.url,'Official bill source')}</p><button type="button" class="ml-pin-bill">${desk.billState(key).pinned?'Unpin bill':'Pin bill'}</button><p role="status" class="ml-bill-save"></p><h3>Legislative milestones</h3>${table(['Milestone','Date'],[['Introduced',date(live.introduced_date)],['Latest action',date(live.latest_action?.date)]])}<p>${esc(live.latest_action?.text||b.action||'Latest action unavailable')}</p><details class="ml-record" open><summary>Source summary</summary><p>${esc(b.summary||live.summary||'Summary unavailable')}</p></details><h3>Sponsor & cosponsors</h3>${table(['Member','Role','Recorded date'],people.map(p=>[esc(p.name||'Name unavailable'),p.role,date(p.date)]))}<p class="ml-muted">Unretained participation dates are unavailable; the bill’s introduction date is not a cosponsor’s joining date.</p><a class="ml-button" href="/?v=desk&workspace=coalition&bill=${encodeURIComponent(key)}">Plan coalition</a>`;
      $('.ml-pin-bill',detail).onclick=()=>{try{desk.setBillState(key,'pinned',!desk.billState(key).pinned);show(key);$('.ml-bill-save',detail).textContent='Saved on this device';}catch(e){$('.ml-bill-save',detail).textContent='Save failed. Retry: '+e.message;}};
      for(const row of $$('.billcard',list))row.classList.toggle('ml-selected',row.dataset.key===key);
      if(push){const u=new URL(location.href);u.searchParams.set('bill',key);history.pushState(null,'',u);}
    }
    list.addEventListener('click',e=>{const summary=e.target.closest('summary');if(summary&&!e.target.closest('button,a')){e.preventDefault();show(summary.parentElement.dataset.key,true);}});
    const enhance=()=>{for(const row of $$('.billcard',list)){row.open=false;const sum=$('summary',row);sum.setAttribute('aria-label','Inspect '+$('.billno',row)?.textContent);}if(!selected)selected=$('.billcard',list)?.dataset.key;show(selected);};
    new MutationObserver(enhance).observe(list,{childList:true});enhance();window.addEventListener('popstate',()=>show(new URL(location.href).searchParams.get('bill')));
  }
  function coalition(){
    const view=$('#view-coalition'),search=$('#cbsearch'),suggestions=$('#cbsugg');if(!view||!search)return;
    const pending=new Set();search.onchange=null;search.placeholder='Find a proposed member';search.setAttribute('aria-label','Find proposed member');
    const add=el('button','ml-primary','Add selected');add.type='button';search.closest('.search').after(add);
    const input=el('label','ml-coalition-bill','Bill scope <input aria-label="Coalition bill scope" placeholder="Congress:bill:number">');input.querySelector('input').value=new URL(location.href).searchParams.get('bill')||'';$('.areasel',view).append(input);
    const summary=el('aside','ml-side ml-coalition-summary','<h3>Scenario snapshot</h3><p>Members in this worklist are proposed participants. Actual sponsorship remains in the bill source.</p><label>Snapshot name<input id="ml-snapshot-name" placeholder="Give this worklist a name"></label><p id="ml-coalition-status" role="status">No unsaved additions</p>');const grid=$('.cbgrid',view),snapshots=$('#cbsnapshots').parentElement;grid.append(summary);summary.append($('#cbsave'),snapshots);
    const heading=$('.eyebrow',grid);heading.firstChild.textContent='Current worklist ';$('.eyebrow',suggestions.parentElement).textContent='Proposed candidates · model estimates';
    const explanation=view.children[1];if(explanation?.tagName==='DIV'){const notes=el('details','ml-record','<summary>How candidate suggestions work</summary>');explanation.before(notes);notes.append(explanation);}
    const status=()=>$('#ml-coalition-status');
    add.onclick=()=>{const typed=search.value.trim();if(typed&&!desk.members.some(m=>m.name===typed)){status().textContent='Choose a retained member from the name list.';return;}if(typed)pending.add(typed);if(!pending.size){status().textContent='Select candidates or enter a member first.';return;}desk.addCoalition([...pending]);pending.clear();search.value='';status().textContent='Unsaved coalition changes';};
    function enhance(){for(const row of $$('.sugg',suggestions)){if($('input',row))continue;row.onclick=null;const name=desk.members[+row.dataset.i]?.name;if(!name)continue;const check=el('label','ml-check',`<input type="checkbox" aria-label="Select ${esc(name)}">Select`);row.prepend(check);$('input',check).onchange=e=>{e.target.checked?pending.add(name):pending.delete(name);status().textContent=pending.size+' candidates selected';};} }
    new MutationObserver(enhance).observe(suggestions,{childList:true});enhance();
    $('#cbsave').onclick=()=>{const name=$('#ml-snapshot-name').value.trim();if(!name||!desk.coalition().members.length){status().textContent='Enter a snapshot name and add at least one member.';return;}status().textContent='Saving…';try{desk.saveCoalition(name,$('input',input).value.trim());status().textContent='Saved on this device';}catch(e){status().textContent='Save failed. Your worklist is retained here. '+e.message;}};
    view.addEventListener('click',e=>{const load=e.target.closest('[data-snapload]');if(!load)return;e.stopImmediatePropagation();const s=desk.loadCoalition(load.dataset.snapload);if(s){$('input',input).value=s.bill||'';$('#ml-snapshot-name').value=s.name;status().textContent='Loaded saved snapshot · '+date(s.saved_at);}},true);
  }
  function persistentFilters(){
    const controls={q:'members-search',area:'members-area',stancefilter:'members-stance',priorityfilter:'members-priority',sort:'members-sort',fundedOnly:'members-funded',potentialOnly:'members-potential',billq:'bills-search',billarea:'bills-area',billcongress:'bills-congress',billtracked:'bills-pinned'};
    const query=new URLSearchParams(location.search);let restoring=true;
    for(const [id,key]of Object.entries(controls)){const n=$('#'+id);if(!n)continue;if(query.has(key)){if(n.type==='checkbox')n.checked=query.get(key)==='1';else n.value=query.get(key);n.dispatchEvent(new Event(n.type==='search'||id==='q'||id==='billq'?'input':'change',{bubbles:true}));}n.addEventListener(n.type==='search'||id==='q'||id==='billq'?'input':'change',()=>{if(restoring)return;const u=new URL(location.href),value=n.type==='checkbox'?(n.checked?'1':''):n.value;if(value)u.searchParams.set(key,value);else u.searchParams.delete(key);history.replaceState(null,'',u);});}
    const chamber=query.get('members-chamber');if(chamber)$$('#chamber button').find(n=>n.dataset.v===chamber)?.click();$('#chamber')?.addEventListener('click',e=>{if(restoring)return;const b=e.target.closest('[data-v]');if(b){const u=new URL(location.href);u.searchParams.set('members-chamber',b.dataset.v);history.replaceState(null,'',u);}});restoring=false;
  }
  function contactLedger(){
    const host=$('#contactbody');if(!host)return;
    function enhance(){const board=$('.ml-contact-workboard',host),columns=$('.ml-contact-columns',host);if(!board||!columns||$('.ml-contact-ledger',board))return;
      const split=el('div','ml-split'),ledger=el('aside','ml-side ml-contact-ledger');columns.before(split);split.append(columns,ledger);
      const rows=Object.entries(desk.manual()).flatMap(([office,m])=>(m.contacts||[]).map(c=>({...c,office}))).sort((a,b)=>(b.date||'').localeCompare(a.date||''));
      const show=c=>{ledger.innerHTML=`<h3>Chronological contact ledger</h3>${rows.length?rows.map(r=>`<p><button class="ml-text-button" data-contact-entry="${esc(r.id)}">${esc(r.date)} · ${esc(r.office)}</button></p>`).join(''):missing('No team contacts recorded.')}<hr>${c?`<h3>${esc(c.office)}</h3><p class="ml-kind">${esc(c.sourceType||'Team note')} · saved on this device</p>${table(['Field','Record'],[['Owner',esc(c.owner||'Unassigned')],['Contact date',date(c.date)],['Issue / bill',esc([c.issue,c.bill].filter(Boolean).join(' · '))],['Follow-up due',date(c.due)],['Updated',esc(c.updatedAt||'Timestamp unavailable')]])}<p>${esc(c.outcome)}</p><p>Next step: ${esc(c.next||'Not recorded')}</p>${c.source?sourceLink(c.source):missing('No source reference attached.')}`:''}`;};
      ledger.onclick=e=>{const b=e.target.closest('[data-contact-entry]');if(b)show(rows.find(c=>c.id===b.dataset.contactEntry));};show(rows[0]);
    }
    new MutationObserver(enhance).observe(host,{childList:true});enhance();
  }
  bills();coalition();persistentFilters();contactLedger();
})();
