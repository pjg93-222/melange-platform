/* Ordered, sourced person facts; no markup from source text is executed. */
window.StafferContacts={render(parent,node,{links=true}={}){
  if(node?.kind&&node.kind!=='person')return;
  const add=(out,tag,value)=>{const e=document.createElement(tag);e.textContent=value;out.append(e);return e;};
  const out=add(parent,links?'section':'small','');out.className='person-contacts';
  const contacts=node?.contacts||[];
  for(const kind of ['phone','email']){
    const values=contacts.filter(c=>c.kind===kind),label=kind==='phone'?'Phone':'Email';
    if(!values.length){add(out,'span',label+': Not available');continue;}
    for(const c of values){const row=add(out,'span','');add(row,'span',(c.label||label)+': ');
      const value=add(row,links?'a':'span',c.value);
      if(links)value.href=kind==='email'?'mailto:'+encodeURIComponent(c.value):'tel:'+c.value.replace(/[^\d+]/g,'');
    }
  }
  if(contacts.length){const dates=[...new Set(contacts.map(c=>c.source_date).filter(Boolean))];add(out,'span','Source export: '+(dates.join(', ')||'date unavailable'));
    if(links){const proof=add(out,'details','');add(proof,'summary','Contact sources');add(proof,'p','Listed contact details; current validity is unconfirmed. Office phone is labeled separately.');add(proof,'pre',JSON.stringify(contacts,null,2));}
  }
}};
window.StafferPersonFacts={render(parent,node){
  const add=(out,tag,value)=>{const e=document.createElement(tag);e.textContent=value??'';out.append(e);return e;};
  const evidence=node.reconciled_career_evidence||[];
  if(evidence.length){
    const section=add(parent,'details','');add(section,'summary',`Reconciled career and affiliation evidence (${evidence.length})`);
    add(section,'p','These dated observations and partial histories preserve unknown service dates. Board roles are affiliations.');
    for(const claim of evidence){
      add(section,'p',[claim.title,claim.organization].filter(Boolean).join(' · '));
      add(section,'p',claim.date_description);
      if(claim.source?.url&&/^https:\/\//.test(claim.source.url)){
        const link=add(section,'a',(claim.source.label||'Official source')+(claim.source.page?` · page ${claim.source.page}`:''));
        link.href=claim.source.url;link.target='_blank';link.rel='noopener noreferrer';
      }
      if(claim.raw_text){const proof=add(section,'details','');add(proof,'summary','Source claim and evidence');add(proof,'p',claim.raw_text);add(proof,'pre',JSON.stringify({...claim.source,...(claim.additional_source_evidence?.length?{additional_source_evidence:claim.additional_source_evidence}:{})},null,2));}
    }
  }
  const facts=node.person_facts;if(!facts)return;
  const through=value=>value?new Date(Date.parse(value+'T00:00:00Z')-86400000).toISOString().slice(0,10):'';
  const section=add(parent,'section','');section.className='person-facts';
  add(section,'h3',evidence.length?'Directory and filing work record':'Current or latest role');
  const latest=facts.latest_roles||facts.current_roles;
  if(!latest.length)add(section,'p','A dated role has not been established in these records.');
  for(const role of latest){add(section,'strong',[role.title,role.employer].filter(Boolean).join(' · '));add(section,'p',`${role.status_label||'Current as reported'} · ${role.evidence_date||role.observed_at||'Date unavailable'}`);}
  for(const group of facts.sections.slice(1)){
    const historyLabel=evidence.length?'Dated directory employment history':'Employment history';
    const detail=add(section,'details','');add(detail,'summary',`${group.key==='past_employment'?historyLabel:group.label} (${group.items.length})`);
    if(!group.items.length)add(detail,'p',group.key==='past_employment'&&evidence.length?'No dated directory employment is recorded; partial career observations are shown separately above.':'Not recorded.');
    if(group.key==='past_employment')for(const role of facts.past_roles)add(detail,'p',[role.title,role.employer,role.date_text||[role.core_start,through(role.core_end_exclusive)].filter(Boolean).join(' to ')].filter(Boolean).join(' · '));
    else for(const item of group.items)add(detail,'p',item.label);
    const proof=add(detail,'details','');add(proof,'summary','Sources');add(proof,'pre',JSON.stringify(group.items,null,2));
  }
  add(section,'p',facts.identity_source_count>1?`${facts.identity_source_count} matched source identities combined.`:facts.identity_note);
}};
