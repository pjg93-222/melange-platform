/* MELANGE selected direction, 2026-09-21. Source records remain in their existing generation. */
(() => {
  'use strict';
  if (document.body.classList.contains('ml-approved')) return;
  document.body.classList.add('ml-approved');
  const $ = (s, p = document) => p.querySelector(s);
  const $$ = (s, p = document) => [...p.querySelectorAll(s)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const el = (tag, cls, html = '') => { const n = document.createElement(tag); n.className = cls || ''; n.innerHTML = html; return n; };
  const safeURL = value => { if(!value||!String(value).trim())return '';try { const u = new URL(value, location.origin); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
  const sourceLink = (url, text = 'Original source') => safeURL(url) ? `<a href="${esc(safeURL(url))}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : '<span class="ml-missing">Source link unavailable</span>';
  const number = value => value == null || !Number.isFinite(Number(value)) ? 'Unknown' : Number(value).toLocaleString();
  const date = value => /^\d{4}-\d{2}-\d{2}/.test(String(value || '')) ? String(value).slice(0, 10) : 'Date unavailable';
  const table = (headers, rows, caption = '') => `<div class="ml-table-region" tabindex="0" role="region" aria-label="${esc(caption || 'Research records')}"><table>${caption ? `<caption>${esc(caption)}</caption>` : ''}<thead><tr>${headers.map(h => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${headers.length}" class="ml-missing">No records match this scope.</td></tr>`}</tbody></table></div>`;
  const section = (title, body) => `<section class="ml-section"><h3>${title}</h3>${body}</section>`;
  const button = (label, action, cls = '') => `<button type="button" class="${cls}" data-ml-action="${action}">${label}</button>`;
  const missing = text => `<p class="ml-missing">${esc(text)}</p>`;
  const desk = window.MelangeDesk;
  const network = window.MelangeNetwork;
  const params = new URLSearchParams(location.search);
  let stored = {}; try { stored = JSON.parse(sessionStorage.getItem('melange_context_v1')) || {}; } catch {}
  const context = {issue: params.get('issue') ?? stored.issue ?? 'Interchange', congress: params.get('congress') ?? stored.congress ?? '', period: params.get('period') ?? stored.period ?? ''};
  let activeMember = null, directoryURL = '/?v=desk', currentTab = 'overview';
  const noteDrafts=new Map();
  function retainContext(url, push = false) {
    const u = new URL(url, location.origin);
    for (const key of ['issue', 'congress', 'period']) { if (context[key]) u.searchParams.set(key, context[key]); else u.searchParams.delete(key); }
    history[push ? 'pushState' : 'replaceState'](null, '', u.pathname + u.search + u.hash);
    try { sessionStorage.setItem('melange_context_v1', JSON.stringify(context)); } catch {}
  }
  function scopeLabel() { return `${context.issue || 'All issues'} · ${context.congress ? context.congress + 'th Congress' : 'All retained Congresses'}${context.period ? ' · From ' + context.period : ''}`; }
  function contextControls() {
    const issues = desk?.data.cats || ['Interchange', 'Payments', 'Technology & Emerging'];
    return `<div class="ml-context" role="group" aria-label="Research scope"><label>Issue<select data-ml-context="issue"><option value="">All issues</option>${issues.map(i => `<option ${i === context.issue ? 'selected' : ''}>${esc(i)}</option>`).join('')}</select></label><label>Congress<select data-ml-context="congress"><option value="">All retained</option>${['119','118','117'].map(c => `<option value="${c}" ${c === context.congress ? 'selected' : ''}>${c}th</option>`).join('')}</select></label><label>From<input type="date" data-ml-context="period" value="${esc(context.period)}"></label><span class="ml-context-note">Public record · evidence through ${esc(desk?.data.rank_honors?.as_of || 'the source dates shown')}</span></div>`;
  }
  function manual(name) {
    if (desk) return desk.manual()[name] || {};
    try { return (JSON.parse(localStorage.getItem('mdesk_manual_v1')) || {})[name] || {}; } catch { return {}; }
  }
  function writeManual(name, patch) {
    if (!desk) throw new Error('Member storage is unavailable. Reopen Members and retry.');
    desk.writeManual(name, patch);
  }
  function records(m, scoped = true) {
    const results = [];
    for (const bill of m.bills || []) {
      const id = desk?.billIdentity(bill), live = id && desk.data.bill_live?.bills?.[id.key];
      const bioguide = m.honors?.member_id;
      const joined = (live?.cosponsors || []).find(c => c.bioguide === bioguide);
      const actionDate = bill.role === 'sponsor' ? live?.introduced_date : joined?.date;
      results.push({kind: /cosponsor/i.test(bill.role) ? 'Cosponsorship' : 'Sponsorship', title: `${bill.bill} · ${bill.title || 'Title unavailable'}`, date: date(actionDate), url: bill.url || live?.url, excerpt: bill.summary || live?.summary || bill.action || '', areas: bill.areas || [], congress: String(bill.congress || ''), reason: `Recorded ${bill.role || 'legislative role'} on this measure. Bill membership does not establish a meeting.`, bill});
    }
    for (const a of m.articles || []) results.push({kind: a.source === 'hearing' ? 'Committee actions' : 'Public statements', title: a.title || 'Untitled source record', date: date(a.date), url: a.url, excerpt: a.excerpt || a.text || a.snippet || '', areas: a.cats || [], congress: a.date ? String(Math.floor((Number(a.date.slice(0,4)) - 1789) / 2) + 1) : '', reason: a.exclusion ? `Excluded from scoring: ${a.exclusion}` : a.rel || 'Retained public-source record; interpretation requires the original context.', scored: a.scored, source: a.source, stance: a.stance});
    const filtered = scoped ? results.filter(r => (!context.issue || r.areas.includes(context.issue)) && (!context.congress || r.congress === context.congress) && (!context.period || (r.date !== 'Date unavailable' && r.date >= context.period))) : results;
    const seen = new Set();
    return filtered.filter(r => { const key = [r.kind, r.url || r.title, r.date].join('|'); if (seen.has(key)) return false; seen.add(key); return true; }).sort((a,b) => (b.date === 'Date unavailable' ? '' : b.date).localeCompare(a.date === 'Date unavailable' ? '' : a.date));
  }
  function timeline(rows, limit = 3, selectable = false) {
    if (!rows.length) return missing('No retained public actions match this scope. Missing evidence does not establish neutrality.');
    return `<ol class="ml-timeline">${rows.slice(0, limit).map((r,i) => `<li><time>${esc(r.date)}</time><div><span class="ml-kind">Public record · ${esc(r.kind)}</span><p>${selectable ? `<button class="ml-text-button" data-event="${i}">${esc(r.title)}</button>` : esc(r.title)}</p><small>${sourceLink(r.url)} · ${esc(r.reason)}</small></div></li>`).join('')}</ol>`;
  }
  function economic(m) {
    const ind = m.industry || {}, year = ind.latest_year;
    return table(['District context','Value','Period / source'], [
      ['Covered jobs', number(ind.employment?.total), year ? `${year} · LEHD LODES · district workplace employment` : 'Coverage unavailable'],
      ['Finance and insurance share', ind.makeup?.CNS10 == null ? 'Unknown' : `${(ind.makeup.CNS10*100).toFixed(1)}%`, year ? `${year} · share of covered jobs` : 'Coverage unavailable'],
      ['Manufacturing share', ind.makeup?.CNS05 == null ? 'Unknown' : `${(ind.makeup.CNS05*100).toFixed(1)}%`, year ? `${year} · share of covered jobs` : 'Coverage unavailable']
    ], 'Economic context · measured employment; source limitations in Demographics');
  }
  function overview(m) {
    const t = manual(m.name).tracker || {}, r = records(m), committees = m.committees || [];
    return `<div class="ml-split"><div>${section('Role & jurisdiction', `<p>${esc(m.chamber)} · ${esc(m.district)}</p>${committees.length ? table(['Committee','Recorded role'], committees.slice(0,4).map(c => [esc(c.name), esc(c.role || c.position || 'Member')])) : missing('Committee records unavailable.')}`)}${section('Documented public actions', timeline(r))}${section('Economic context', economic(m))}<div class="ml-engagement-strip"><span>Team note</span><strong>${esc(t.owner || 'Owner unassigned')}</strong><span>${esc(t.next || 'No next step recorded')}</span><span>Due ${esc(t.due || 'not set')}</span>${button('Edit engagement','notes')}</div></div><aside class="ml-side"><h3>Meeting brief</h3><span class="ml-kind">Team note · saved on this device</span><h4>Meeting objective</h4><p>${esc(t.objective || 'No meeting objective recorded.')}</p><h4>Open questions</h4><p class="ml-prewrap">${esc(t.questions || 'No open questions recorded.')}</p><h4>Owner & next step</h4><p>${esc(t.owner || 'Unassigned')}<br>${esc(t.next || 'No next step recorded')}<br>Due: ${esc(t.due || 'Not set')}</p>${button('Add contact / follow-up','contact')}${section('Sources in this brief', r.length ? `<ul class="ml-source-list">${r.slice(0,3).map(a=>`<li>${sourceLink(a.url,a.title)}<small>${esc(a.date)}</small></li>`).join('')}</ul>` : missing('No source records in the selected scope.'))}</aside></div>`;
  }
  const tabNames = [['overview','Overview'],['background','Background'],['demographics','Demographics'],['behavior','Behavior'],['positions','Positions & bills'],['access','People & access'],['coverage','Past coverage'],['notes','Notes'],['sources','Sources & method']];
  function changeTab(id, push = true) {
    if (!activeMember || !tabNames.some(t=>t[0] === id)) return;
    currentTab = id;
    for (const t of $$('.ml-dossier-tabs button')) { const on = t.dataset.tab === id; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; }
    for (const pane of $$('.ml-dossier-pane')) pane.hidden = pane.dataset.pane !== id;
    const u = new URL(location.href); u.searchParams.set('m',activeMember.name); u.searchParams.delete('v'); u.hash = 'profile-' + id; retainContext(u, push);
    if (id === 'notes') renderNotes(activeMember);
  }
  function attachProfile() {
    const body = $('#memberbody'), head = $('.mhead',body || document);
    if (!desk || !body || !head || head.dataset.approved) return;
    const name = $('h2',head)?.childNodes[0]?.textContent.trim();
    const m = desk.members.find(x=>x.name === name); if (!m) return;
    activeMember = m; head.dataset.approved = 'true';
    const requested = location.hash.replace('#profile-','');
    const legacy = [...body.children].filter(n=>n !== head);
    for (const n of legacy) if (n.matches('.melange-profile-tools,.melange-assessment-note')) n.remove();
    const panes = new Map();
    const root = el('div','ml-dossier');
    const top = el('div','ml-dossier-top', `${contextControls()}<div class="ml-profile-actions">${button('Prepare cited brief','export')}${button('Add contact','contact')}</div>`);
    head.before(top); head.after(root);
    $('.verdict', head)?.remove();
    const classification = $('details', head); if (classification) classification.remove();
    const tabs = el('nav','ml-dossier-tabs',tabNames.map(([id,label])=>`<button id="ml-tab-${id}" role="tab" aria-controls="ml-pane-${id}" aria-selected="false" data-tab="${id}" tabindex="-1">${label}</button>`).join(''));
    tabs.setAttribute('aria-label','Member dossier'); tabs.setAttribute('role','tablist'); root.append(tabs);
    for (const [id,label] of tabNames) { const p = el('section','ml-dossier-pane'); p.id = 'ml-pane-'+id; p.dataset.pane = id; p.hidden = true; p.setAttribute('role','tabpanel'); p.setAttribute('aria-labelledby','ml-tab-'+id); panes.set(id,p); root.append(p); }
    panes.get('overview').innerHTML = overview(m);
    const models = el('details','ml-models','<summary>Model details & interpretive research</summary><p>Model estimates and rank classifications are secondary research. A rank is not a persuasion probability. Conditional spending outputs are not observed causal returns.</p>');
    if (classification) models.append(classification);
    for(const badge of $$('.potential-tag',head))models.append(badge);
    for (const n of legacy) {
      if (!n.isConnected) continue;
      const key = n.dataset.key;
      const destination = {who:'background',district:'demographics',stand:'positions',reach:'access',money:'coverage',method:'sources'}[key];
      if (destination) { n.open = true; panes.get(destination).append(n); }
      else if (n.id === 'trackcard') { n.hidden = true; panes.get('notes').append(n); }
      else if (!n.matches('.melange-profile-tools,.melange-assessment-note')) models.append(n);
    }
    panes.get('overview').append(models);
    panes.get('background').prepend(el('div','',section('Recorded roles',table(['Committee / jurisdiction','Role','Date coverage'],(m.committees||[]).map(c=>[esc(c.name),esc(c.role||c.position||'Member'),esc(c.start || c.date || 'Appointment date unavailable')])))));
    const interpretation = el('details','ml-models','<summary>Interpretive research</summary><p>Provisional narratives, inferred traits and speech research are not established characteristics or forecasts of conduct. Review coverage and methodology.</p>');
    for (const sub of $$('details.sub',panes.get('background'))) if (/Working style|Observed speech|Personal details/.test($('summary',sub)?.textContent || '')) interpretation.append(sub);
    renderBehavior(m,panes.get('behavior')); panes.get('behavior').append(interpretation);
    panes.get('demographics').prepend(el('div','ml-economic-brief',section('District economic brief', `<p>${esc(m.district_brief?.p1 || 'District brief unavailable.')}</p><p>${esc(m.district_brief?.p2 || '')}</p>${economic(m)}${missing('Institution coordinates are not included in this generation. No synthetic geography is shown.')}`)));
    panes.get('positions').prepend(el('div','',section('Legislative actions',timeline(records(m).filter(r=>r.bill),3))));
    panes.get('access').prepend(el('div','ml-engagement-strip',`<span>Documented workplace overlap is not confirmation of current access.</span><a href="/?v=staffer&s=${encodeURIComponent(m.name)}">Find a route to this office</a>`));
    renderCoverage(m,panes.get('coverage'));
    panes.get('sources').prepend(el('div','',sourcesHTML(m)));
    tabs.onclick = e => { const b=e.target.closest('[data-tab]'); if (b) changeTab(b.dataset.tab); };
    tabs.onkeydown = e => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return; e.preventDefault(); const items=$$('button',tabs), i=items.indexOf(document.activeElement); const j=e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowRight'?1:-1)+items.length)%items.length; items[j].click(); items[j].focus(); };
    const back = $('#mdback',head) || $('#backbtn',head); if (back) { back.textContent='Back to members'; back.onclick=()=>{activeMember=null;window.gotoMemberDesk('ranked');retainContext(directoryURL);}; }
    const print = $('#printbtn',head); if (print) print.remove();
    changeTab(tabNames.some(t=>t[0]===requested)?requested:'overview',false);
  }
  function renderBehavior(m,pane) {
    const rows=records(m), kinds=['Sponsorship','Cosponsorship','Committee actions','Public statements'];
    const counts=kinds.map(k=>rows.filter(r=>r.kind===k).length), max=Math.max(1,...counts);
    pane.innerHTML=`<h2>Documented behavior</h2><p class="ml-muted">${esc(scopeLabel())} · ${esc(m.chamber)}. Counts describe retained records; the eligible universe is not supplied.</p><div class="ml-split"><div>${table(['Action type','Observed actions','Eligible records / denominator','Period','Source coverage'],kinds.map((k,i)=>[k,counts[i] ? `<span class="ml-countbar"><i style="width:${counts[i]/max*100}%"></i><b>${counts[i]} records</b></span>`:'<span class="ml-missing">Unknown · no retained records</span>','Unavailable · no rate calculated',esc(context.period ? 'Since '+context.period : 'All retained dates'),`${rows.filter(r=>r.kind===k&&r.url).length} linked sources`]))}${section('Dated action timeline',timeline(rows,12,true))}</div><aside class="ml-side" id="ml-event-detail"></aside></div>`;
    const show=i=>{const r=rows[i];$('#ml-event-detail',pane).innerHTML=r?`<h3>Selected evidence</h3><span class="ml-kind">Public record · ${esc(r.kind)}</span><p>${esc(r.date)}</p><h4>${esc(r.title)}</h4><p>${esc(r.reason)}</p>${sourceLink(r.url)}<h4>Original excerpt</h4>${r.excerpt?`<blockquote>${esc(r.excerpt)}</blockquote>`:missing('Original excerpt unavailable. Open the source to read the full record.')}<p class="ml-muted">Missing coverage: eligible-action denominator and comprehensive committee-action history are unavailable.</p>`:missing('No selected event. Broaden the issue or date scope.');};show(0);
    pane.addEventListener('click',e=>{const b=e.target.closest('[data-event]');if(b)show(+b.dataset.event);});
  }
  function renderCoverage(m,pane) {
    const rows=records(m).filter(r=>!r.bill), groups=new Map();
    for(const r of rows){const key=(r.date+'|'+r.title.toLowerCase().replace(/\W+/g,' '));if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
    const intro=el('div','ml-coverage-summary',`<h2>Public coverage</h2><p>${groups.size} exact date/title groups · ${rows.length} retained channel records. Different headlines remain separate until an event identity is verified.</p>${[...groups.values()].slice(0,8).map((g,i)=>`<details class="ml-record" ${i===0?'open':''}><summary>${esc(g[0].date)} · ${esc(g[0].title)}</summary><p>${esc(g[0].reason)}</p>${table(['Channel','Source date','Original record'],g.map(r=>[esc(r.source||r.kind),esc(r.date),sourceLink(r.url)]))}</details>`).join('')}`);pane.prepend(intro);
  }
  function sourcesHTML(m) {
    const d=desk.data, sourceDates=d.freshness?.sources||{}, rows=records(m,false);
    return `<h2>Sources & method</h2>${table(['Source','Retained records','Refreshed','Scope / limitation'],[
      ['Legislative records',number((m.bills||[]).length),esc(date(sourceDates['bill_repository_live.json'])),'Recorded sponsorship/cosponsorship; missing action dates stay unavailable.'],
      ['Public coverage',number((m.articles||[]).length),esc(d.rank_honors?.as_of||'Unknown'),'Retained items may repeat an event; exclusions remain in the source ledger.'],
      ['District employment',m.industry?.employment?.total==null?'Unknown':number(m.industry.employment.total)+' jobs',esc(date(sourceDates['data/lobbying/district_industry.json'])),'LEHD LODES · '+esc(m.industry?.latest_year||'period unavailable')],
      ['Staff directory',number((m.staffdir||[]).length),'See each source record','Employment overlap is not a confirmed relationship.'],
      ['Linked evidence',number(rows.filter(r=>r.url).length),'Per-record dates','No rate or coverage percentage is inferred from the retained sample.']
    ])}<details class="ml-record"><summary>Rank, probability and scenario</summary><p>A rank orders the defined cohort. It is not a probability. A forecast probability requires a named target, horizon and evaluated model. Scenarios describe conditional modeled outcomes and proposed allocations; historical PAC contributions remain fixed.</p></details>`;
  }
  function noteForm(m) {
    const t=noteDrafts.get(m.name)||manual(m.name).tracker||{};
    return `<form class="ml-note-form" id="ml-note-form"><h3>Engagement & meeting brief</h3><p class="ml-kind">Team note · device-local storage</p><label>Meeting objective<textarea name="objective" rows="2">${esc(t.objective||'')}</textarea></label><label>Open questions<textarea name="questions" rows="3">${esc(t.questions||'')}</textarea></label><div class="ml-form-grid"><label>Owner<input name="owner" value="${esc(t.owner||'')}"></label><label>Follow-up due<input name="due" type="date" value="${esc(t.due||'')}"></label></div><label>Next step<input name="next" value="${esc(t.next||'')}"></label><label>Source references<textarea name="sources" rows="2" placeholder="One source URL per line">${esc(t.sources||'')}</textarea></label><label>Status<select name="status">${['Not started','Researching','Outreach sent','Meeting set','Met','Committed','Declined'].map(s=>`<option ${s===t.status?'selected':''}>${s}</option>`).join('')}</select></label><div class="ml-save-row"><button type="submit" class="ml-primary">Save on this device</button><span role="status" id="ml-note-status">${t.updatedAt?'Saved on this device · '+esc(date(t.updatedAt)):'No saved meeting brief'}</span></div></form>`;
  }
  function renderNotes(m) {
    const pane=$('#ml-pane-notes');if(!pane)return;
    if($('#ml-note-form',pane)?.dataset.dirty==='true')return;
    let content=$('.ml-notes-content',pane);if(!content){content=el('div','ml-notes-content');pane.prepend(content);}
    const cs=manual(m.name).contacts||[];
    content.innerHTML=`<div class="ml-split"><div>${noteForm(m)}</div><aside class="ml-side"><h3>Contact & follow-up ledger</h3>${button('Add contact','contact')}${cs.length?cs.slice().sort((a,b)=>b.date.localeCompare(a.date)).map(c=>contactCard(c)).join(''):missing('No team contacts recorded for this office.')}</aside></div>`;
    const form=$('#ml-note-form'),status=$('#ml-note-status');
    if(noteDrafts.has(m.name)){status.textContent='Unsaved changes';form.dataset.dirty='true';}
    form.oninput=()=>{status.textContent='Unsaved changes';form.dataset.dirty='true';noteDrafts.set(m.name,Object.fromEntries(new FormData(form)));};
    form.onsubmit=e=>{e.preventDefault();status.textContent='Saving…';try{const patch=Object.fromEntries(new FormData(form));writeManual(m.name,{tracker:{...(manual(m.name).tracker||{}),...patch,issue:context.issue,congress:context.congress,updatedAt:new Date().toISOString()}});status.textContent='Saved on this device';form.dataset.dirty='false';noteDrafts.delete(m.name);}catch(error){status.textContent='Save failed. Your entries remain here. '+error.message;status.setAttribute('role','alert');}};
  }
  function contactCard(c) {return `<article class="ml-contact-card"><strong>${esc(c.office||c.person)}</strong><p>${esc(c.date)} · ${esc(c.owner||'Owner unassigned')}</p><p>${esc(c.outcome)}</p><small>Team note · ${esc(c.issue||'No issue')} · Due ${esc(c.due||'not set')}</small>${c.id?`<button class="ml-text-button" data-edit-contact="${esc(c.id)}" data-office="${esc(c.office)}">Edit contact</button>`:''}</article>`;}
  function openDialog(title,body) {
    const previous=document.activeElement,d=el('dialog','ml-dialog',`<header><h2>${title}</h2><button type="button" data-close aria-label="Close ${esc(title)}">Close</button></header>${body}`);document.body.append(d);d.showModal();
    const close=()=>d.close();$('[data-close]',d).onclick=close;
    d.addEventListener('close',()=>{d.remove();if(previous?.isConnected)previous.focus();});
    return d;
  }
  function openContact(m=activeMember,contactId=null) {
    if(!desk)return;const all=desk.members,old=m&&(manual(m.name).contacts||[]).find(c=>c.id===contactId)||{};
    const d=openDialog(contactId?'Edit contact':'Add contact',`<form class="ml-contact-form"><p class="ml-kind">Team note · saved on this device</p><div class="ml-form-grid"><label>Office<select name="office" required>${all.map(x=>`<option ${x.name===(old.office||m?.name)?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label><label>Person<input name="person" value="${esc(old.person||'')}" placeholder="Optional staff contact"></label><label>Contact date<input type="date" name="date" required value="${esc(old.date||new Date().toLocaleDateString('en-CA'))}"></label><label>Owner<input name="owner" required value="${esc(old.owner||manual(m?.name).tracker?.owner||'')}"></label><label>Issue<input name="issue" value="${esc(old.issue??context.issue)}"></label><label>Bill / Congress<input name="bill" value="${esc(old.bill||'')}" placeholder="e.g. H.R. 123 · 119th"></label><label>Follow-up due<input name="due" type="date" value="${esc(old.due||'')}"></label><label>Status<select name="status">${['Open','Scheduled','Complete'].map(s=>`<option ${s===old.status?'selected':''}>${s}</option>`).join('')}</select></label></div><label>Outcome / contact record<textarea name="outcome" required rows="3">${esc(old.outcome||'')}</textarea></label><label>Next step<input name="next" value="${esc(old.next||'')}"></label><label>Source or reference<input name="source" value="${esc(old.source||'')}" placeholder="Optional reference URL"></label><button class="ml-primary" type="submit">Save contact</button><p role="status" class="ml-contact-status"></p></form>`);
    const f=$('form',d),status=$('.ml-contact-status',d);if(contactId)$('[name=office]',f).disabled=true;
    f.oninput=()=>status.textContent='Unsaved changes';
    f.onsubmit=e=>{e.preventDefault();status.textContent='Saving…';const entry={...old,...Object.fromEntries(new FormData(f)),office:contactId?old.office:$('[name=office]',f).value,id:old.id||crypto.randomUUID(),sourceType:'Team note',createdAt:old.createdAt||new Date().toISOString(),updatedAt:new Date().toISOString()};try{const saved=manual(entry.office),contacts=(saved.contacts||[]).filter(c=>c.id!==entry.id).concat(entry);writeManual(entry.office,{contacts,tracker:{...(saved.tracker||{}),owner:entry.owner,next:entry.next,due:entry.due,last:entry.date,issue:entry.issue,updatedAt:entry.updatedAt}});status.textContent='Saved on this device';$('button[type=submit]',f).textContent='Saved · update contact';if(!$('#view-contact')?.hidden)renderContacts();if(activeMember)renderNotes(activeMember);}catch(error){status.textContent='Save failed. Your entries remain here. '+error.message;status.setAttribute('role','alert');}};
  }
  function renderContacts() {
    const p=$('#contactbody');if(!p||!desk)return;
    const old=p.querySelector('.ml-contact-workboard');if(old)old.remove();
    let all=Object.entries(desk.manual()).flatMap(([office,o])=>(o.contacts||[]).map(c=>({...c,office})));
    const root=el('section','ml-contact-workboard',`<div class="ml-section-heading"><div><h2>Contact log</h2><p>Team records and owned follow-ups · saved on this device</p></div>${button('Add contact','contact')}</div><div class="ml-form-grid"><label>Filter owner<input type="search" id="ml-contact-owner" placeholder="All owners"></label><label>Filter issue<input type="search" id="ml-contact-issue" placeholder="All issues"></label></div><div class="ml-contact-columns"></div><details class="ml-legacy-contact"><summary>Existing staff meeting flags</summary></details>`);
    const legacy=$('.ml-legacy-contact',root);for(const n of [...p.children])legacy.append(n);p.append(root);
    const draw=()=>{const owner=$('#ml-contact-owner').value.toLowerCase(),issue=$('#ml-contact-issue').value.toLowerCase();const rows=all.filter(c=>(c.owner||'').toLowerCase().includes(owner)&&(c.issue||'').toLowerCase().includes(issue));$('.ml-contact-columns',root).innerHTML=['Open','Scheduled','Complete'].map(s=>`<section><h3>${s} <small>${rows.filter(c=>(c.status||'Open')===s).length}</small></h3>${rows.filter(c=>(c.status||'Open')===s).sort((a,b)=>(a.due||'9999').localeCompare(b.due||'9999')).map(contactCard).join('')||missing('No contacts in this status.')}</section>`).join('');};
    $('#ml-contact-owner').oninput=draw;$('#ml-contact-issue').oninput=draw;draw();
  }
  function briefHTML(m,includeNotes,enabled=new Set(['role','actions','economy'])) {
    const r=records(m),t=manual(m.name).tracker||{};
    return `<h1>${esc(m.name)} · meeting brief</h1><p>${esc(scopeLabel())}<br>Prepared ${new Date().toLocaleDateString()} · evidence through ${esc(desk.data.rank_honors?.as_of||'per-source dates')}</p>${enabled.has('role')?section('Role & jurisdiction',`<p>${esc(m.chamber)} · ${esc(m.district)}</p><ul>${(m.committees||[]).map(c=>`<li>${esc(c.name)} · ${esc(c.role||'Member')}</li>`).join('')}</ul>`):''}${enabled.has('actions')?section('Documented actions',timeline(r,8)):''}${enabled.has('economy')?section('Economic context',economic(m)):''}${includeNotes?section('Private team notes',`<p>Saved on this device. Included by explicit selection.</p><p>${esc(t.objective||'No objective recorded')}</p><p>${esc(t.questions||'No questions recorded')}</p><p>Owner: ${esc(t.owner||'Unassigned')} · Next: ${esc(t.next||'Not set')} · Due: ${esc(t.due||'Not set')}</p>`):'<p>Private team notes excluded.</p>'}`;
  }
  function openExport(m=activeMember) {
    if(!m&&desk)m=desk.members.find(x=>x.name===$('#ml-directory-preview')?.dataset.member)||desk.members[0];if(!m)return;
    const d=openDialog('Notes & exports',`<div class="ml-composer"><aside><h3>Brief sections</h3>${[['role','Role & jurisdiction'],['actions','Documented actions & sources'],['economy','Economic context']].map(([id,label])=>`<label class="ml-check"><input type="checkbox" name="${id}" checked>${label}</label>`).join('')}<label class="ml-check"><input type="checkbox" name="private">Include private team notes</label><p role="status" id="ml-export-privacy">Local notes excluded</p><button type="button" class="ml-primary" id="ml-download-brief">Download cited brief</button><details><summary>Advanced JSON backup</summary><p>Includes all device-local notes. Keep this file private.</p><button type="button" id="ml-notes-backup">Download local notes</button></details></aside><article class="ml-brief-preview"></article></div>`);
    const render=()=>{const privateNotes=$('[name=private]',d).checked,enabled=new Set($$('input:checked',d).map(x=>x.name));$('.ml-brief-preview',d).innerHTML=briefHTML(m,privateNotes,enabled);$('#ml-export-privacy',d).textContent=privateNotes?'Private local notes included':'Local notes excluded';};d.onchange=render;render();
    function download(text,name,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    $('#ml-download-brief',d).onclick=()=>download(`<!doctype html><html lang="en"><meta charset="utf-8"><title>${esc(m.name)} meeting brief</title><style>body{font:16px/1.5 Georgia,serif;max-width:900px;margin:40px auto;padding:20px;color:#342649;background:#fcf7f4}table{border-collapse:collapse;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid #d8cbbc}small,time{display:block}a{color:#57447b}h1,h3{font-weight:400}</style>${$('.ml-brief-preview',d).innerHTML}</html>`,`${m.name.replace(/\W+/g,'-')}-meeting-brief.html`,'text/html');
    $('#ml-notes-backup',d).onclick=()=>download(JSON.stringify(desk.manual(),null,2),'melange-private-local-notes.json','application/json');
  }
  function navigation() {
    const nav=$('#melange-nav');if(!nav)return;
    const opener=el('button','ml-nav-open','<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>Menu</span>');opener.type='button';opener.setAttribute('aria-controls','melange-nav');opener.setAttribute('aria-expanded','false');document.body.prepend(opener);
    const scrim=el('button','ml-nav-scrim');scrim.type='button';scrim.setAttribute('aria-label','Close navigation');scrim.hidden=true;document.body.append(scrim);
    const close=()=>{document.body.classList.remove('ml-nav-visible');scrim.hidden=true;opener.setAttribute('aria-expanded','false');if(matchMedia('(max-width:760px)').matches)nav.inert=true;};
    opener.onclick=()=>{document.body.classList.add('ml-nav-visible');nav.inert=false;scrim.hidden=false;opener.setAttribute('aria-expanded','true');$('a',nav)?.focus();};scrim.onclick=()=>{close();opener.focus();};
    const media=matchMedia('(max-width:760px)');const resize=()=>{if(media.matches){if(!document.body.classList.contains('ml-nav-visible'))nav.inert=true;}else{nav.inert=false;close();}};media.addEventListener('change',resize);resize();
    nav.addEventListener('click',e=>{if(e.target.closest('a,button'))close();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('ml-nav-visible')){close();opener.focus();}if(e.key==='Tab'&&document.body.classList.contains('ml-nav-visible')){const focusable=$$('a,button,summary',nav).filter(n=>n.getClientRects().length);const first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
  }
  function directory() {
    const view=$('#view-ranked'),rows=$('#rows');if(!view||!rows||!desk||$('#ml-directory-preview'))return;
    const container=$('.tablewrap',view),split=el('div','ml-directory-split'),preview=el('aside','ml-side');preview.id='ml-directory-preview';container.before(split);split.append(container,preview);
    const show=name=>{const m=desk.members.find(x=>x.name===name);if(!m)return;preview.dataset.member=name;preview.innerHTML=`<h2>${esc(name)}</h2><p>${esc(m.chamber)} · ${esc(m.district)}</p><p class="ml-kind">Selected member · public record</p>${timeline(records(m),3)}<a class="ml-button" href="/?m=${encodeURIComponent(name)}#profile-overview">Open full dossier</a>`;for(const row of $$('tr',rows))row.classList.toggle('ml-selected',row.querySelector('a')?.textContent.trim()===name);};
    const controls=el('div','ml-directory-tools',`<span>Preview a member’s evidence before opening the dossier.</span>${button('Clear filters','clear-directory')}<details><summary>Optional columns</summary><label class="ml-check"><input type="checkbox" data-column="owner">Owner & next step</label><label class="ml-check"><input type="checkbox" data-column="coverage">Evidence date</label></details>`);split.before(controls);
    function enhanceRows(){for(const row of $$('tr',rows)){if(row.dataset.mlRow)return;row.dataset.mlRow='1';const a=$('a',row);if(!a)continue;const name=a.textContent.trim(),b=el('button','ml-preview-button','Preview');b.type='button';b.setAttribute('aria-label','Preview '+name);b.onclick=e=>{e.stopPropagation();show(name);};a.after(b);}if(!$$('tr a',rows).length)preview.innerHTML=missing('No matching members. Clear filters to recover the directory.');else if(!$$('tr a',rows).some(a=>a.textContent.trim()===preview.dataset.member))show($('tr a',rows).textContent.trim());for(const check of $$('input[data-column]:checked',controls))check.dispatchEvent(new Event('change',{bubbles:true}));}
    new MutationObserver(enhanceRows).observe(rows,{childList:true});enhanceRows();show($('a',rows)?.textContent.trim()||desk.members[0].name);
    controls.addEventListener('change',e=>{const field=e.target.dataset.column;if(!field)return;const tableNode=$('table',container);$$('[data-optional="'+field+'"]',tableNode).forEach(n=>n.remove());if(!e.target.checked)return;const th=el('th','',field==='owner'?'Owner / next step':'Evidence date');th.dataset.optional=field;$('thead tr',tableNode).append(th);for(const tr of $$('tbody tr',tableNode)){const m=desk.members.find(x=>x.name===$('a',tr)?.textContent.trim());const td=el('td','',m?(field==='owner'?esc((manual(m.name).tracker?.owner||'Unassigned')+' · '+(manual(m.name).tracker?.next||'No next step')):esc(records(m,false)[0]?.date||'Unknown')):'');td.dataset.optional=field;tr.append(td);}});
  }
  function influence() {
    if(!network)return;const graph=$('#app-network #netwrap'),right=$('#app-network #right');if(!graph||!right)return;
    const controls=el('div','ml-graph-controls',`${button('−','zoom-out')}${button('+','zoom-in')}${button('Fit network','fit-network')}`);graph.append(controls);
    $('#cv').setAttribute('aria-label','Congressional influence network. Use Find member or the target roster to inspect the same records.');$('#cv').setAttribute('tabindex','0');
    const dossier=el('section','ml-network-evidence');dossier.id='ml-network-evidence';$('#tpanel').after(dossier);
    const draw=()=>{const s=network.state(),name=s.member||s.targets[0],m=desk?.members.find(x=>x.name===name);if(!name)return;let scope=network.data.issues[s.issue];if(desk?.data.cats[s.issue])scope=desk.data.cats[s.issue];if(!$('#app-network').hidden)context.issue=scope;
      dossier.innerHTML=`<span class="ml-kind">Selected evidence</span><h3>${esc(name)}</h3><a href="/?m=${encodeURIComponent(name)}&issue=${encodeURIComponent(scope)}#profile-overview">Open dossier & sources</a>${m?timeline(records(m),3):missing('Member source records are unavailable.')}<details><summary>Network relationship basis</summary><p>${network.links(name).length} retained network connections in the selected network. Edges represent the active cosponsorship, committee or documented co-appearance layers; they do not establish access or agreement.</p><ul>${network.links(name).slice(0,5).map(l=>`<li>${esc(l.name)} · ${l.weight==null?'weight unavailable':'edge weight '+number(l.weight)}</li>`).join('')}</ul><p>Original pair-level source links are not retained in this graph payload; member records are separate context.</p></details>`;
      const targets=$$('#targets > .tgt');targets.forEach((n,i)=>n.classList.toggle('ml-extra-target',i>=7));
      const mode=$('#rtog');if(mode)for(const b of $$('button',mode))if(/persuadable/i.test(b.textContent))b.textContent='Model priority';
    };
    let scheduled=false;const schedule=()=>{if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;draw();});};
    for(const target of [$('#targets'),$('#mpanel')].filter(Boolean))new MutationObserver(schedule).observe(target,{childList:true});
    $('#issue')?.addEventListener('change',()=>{schedule();retainContext(location.href);});$('#netseg')?.addEventListener('click',schedule);$('#search')?.addEventListener('change',schedule);
    const showMore=el('button','ml-targets-more','Show all scenario targets');showMore.type='button';$('#targets')?.after(showMore);showMore.onclick=()=>{right.classList.toggle('ml-all-targets');showMore.textContent=right.classList.contains('ml-all-targets')?'Show seven targets':'Show all scenario targets';};
    const options=$('#viewpanel');if(options){options.setAttribute('aria-label','Advanced network controls');options.addEventListener('keydown',e=>{if(e.key==='Escape'){$('#viewbtn').click();$('#viewbtn').focus();}});}
    if(context.issue){const opt=$$('#issue option').find(o=>o.textContent===context.issue||o.value===String(desk?.data.cats.indexOf(context.issue)));if(opt){$('#issue').value=opt.value;$('#issue').dispatchEvent(new Event('change'));}}
    draw();
  }
  function methods() {
    const p=$('#melange-methods');if(!p)return;
    const existing=[...p.children],details=el('details','ml-record','<summary>Technical source notes & build checks</summary>');existing.slice(2).forEach(n=>details.append(n));
    p.append(el('div','',table(['Evidence class','What it establishes','Limits'],[
      ['Public record','A dated source action or reported filing','A filing mention is not a confirmed meeting, contact, endorsement or position.'],
      ['Model estimate','An output under a named model and population','Rank is not probability; no implied causal effect.'],
      ['Scenario','A conditional comparison using proposed inputs','Not actual contributions or realized ROI.'],
      ['Team note','A user-entered engagement record','Saved on this device; no shared synchronization.'],
      ['Missing evidence','A source or measurement is unavailable','Unknown is not zero.']
    ])));for(const [title,text]of [['Ranking','Compare only members within the stated population and chamber.'],['Probability','Use the named bill, eligible candidates, target horizon and calibration. Missing calibration remains unavailable.'],['Scenarios','Keep actual spending, proposed allocations and modeled results separate.']])p.append(el('details','ml-record',`<summary>${title}</summary><p>${text}</p>`));p.append(details);
  }
  function connectEvents() {
    document.addEventListener('click',e=>{
      const action=e.target.closest('[data-ml-action]')?.dataset.mlAction;
      if(action==='notes')changeTab('notes');if(action==='contact')openContact();if(action==='export')openExport();if(action==='zoom-in')network?.zoom(1.2);if(action==='zoom-out')network?.zoom(1/1.2);if(action==='fit-network')network?.reset();
      if(action==='clear-directory'){for(const id of ['q','area','stancefilter','priorityfilter']){const n=$('#'+id);if(n){n.value='';n.dispatchEvent(new Event(id==='q'?'input':'change',{bubbles:true}));}}for(const id of ['fundedOnly','potentialOnly']){const n=$('#'+id);if(n){n.checked=false;n.dispatchEvent(new Event('change',{bubbles:true}));}}const sort=$('#sort');if(sort){sort.value='rank';sort.dispatchEvent(new Event('change',{bubbles:true}));}$('#chamber button[data-v=all]')?.click();}
      const edit=e.target.closest('[data-edit-contact]');if(edit)openContact(desk.members.find(m=>m.name===edit.dataset.office),edit.dataset.editContact);
      const dest=e.target.closest('#viewseg [data-v]');if(dest){activeMember=null;const u=new URL(location.href);u.searchParams.delete('m');u.searchParams.set('v','desk');u.searchParams.set('workspace',dest.dataset.v);u.hash='';retainContext(u,true);if(dest.dataset.v==='contact')renderContacts();}
      if(e.target.closest('.melange-file-tools > summary')){e.preventDefault();$('.melange-file-tools').open=false;openExport();}
    });
    document.addEventListener('change',e=>{const key=e.target.dataset.mlContext;if(!key)return;context[key]=e.target.value;retainContext(location.href);if(activeMember){const m=activeMember;desk.open(m.name);attachProfile();} });
    for(const fn of ['gotoNetwork','gotoMember','gotoMemberDesk','gotoIndustry','gotoStaffer']){const original=window[fn];if(typeof original!=='function')continue;window[fn]=function(...args){if(fn==='gotoMember'&&!activeMember)directoryURL=location.href;const previous=new URL(location.href);const out=original.apply(this,args);const next=new URL(location.href);for(const [key,value]of previous.searchParams)if(key.startsWith('members-')||key.startsWith('bills-'))next.searchParams.set(key,value);retainContext(next);if(fn==='gotoMember')attachProfile();if(fn==='gotoMemberDesk')activeMember=null;return out;};}
    window.addEventListener('popstate',()=>{const q=new URLSearchParams(location.search);for(const key of ['issue','congress','period'])context[key]=q.get(key)||'';if(q.get('m')){attachProfile();changeTab(location.hash.replace('#profile-','')||'overview',false);}else if(q.get('workspace')){desk?.show(q.get('workspace'));if(q.get('workspace')==='contact')renderContacts();}});
    window.addEventListener('beforeunload',e=>{if(noteDrafts.size||$('[data-dirty=true]')){e.preventDefault();e.returnValue='';}});
    const body=$('#memberbody');if(body)new MutationObserver(attachProfile).observe(body,{childList:true});
    document.addEventListener('click',()=>requestAnimationFrame(attachProfile));
    if(desk){const w=params.get('workspace');if(w){window.gotoMemberDesk(w,true);if(w==='contact')renderContacts();}attachProfile();}
  }
  navigation();directory();influence();methods();connectEvents();
  // Research pages use their own complete, verified JSON payloads.
  window.MelangeUI={el,esc,table,section,missing,sourceLink,number,date,button,context,records,desk,network};
})();
