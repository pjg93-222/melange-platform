/* Search, drag and keyboard-add independent subjects from saved reports. */
(() => {
  const get=name=>document.getElementById('compare-'+name), esc=escapeHtml;
  const measures={giving:'Net giving',direct:'Direct candidate contributions',organizations:'Giving to organizations',personal:'Personal giving',corporate:'Corporate giving'};
  let catalog=null, revision=0, selected=[], context=null, dragging='', opening=null;
  const money=n=>n==null?'Unavailable':Number(n).toLocaleString('en-US',{style:'currency',currency:'USD'});
  const identity=s=>JSON.stringify([catalog.run,catalog.industry,s.id]);
  function status(text){get('status').textContent=text;}
  async function loadRuns() {
    const ticket=++revision;
    const result=await api('/api/report-sources');
    if(ticket!==revision)return;
    get('run').replaceChildren(...result.runs.map(r=>new Option(r.name,r.id)));
    if(context&&result.runs.some(r=>r.id===context.run))get('run').value=context.run;
    await loadIndustries();
  }
  async function loadIndustries() {
    const ticket=++revision;catalog=null;get('results').replaceChildren();status('Reading industries…');
    get('industry').replaceChildren();get('industry').disabled=true;
    const result=await api('/api/graph-catalog?id='+encodeURIComponent(get('run').value));
    if(ticket!==revision)return;
    get('industry').replaceChildren(...result.industries.map(r=>new Option(r.name,r.name)));
    get('industry').disabled=!result.industries.length;
    if(context?.run===get('run').value&&result.industries.some(r=>r.name===context.industry))get('industry').value=context.industry;
    await loadSubjects();
  }
  async function loadSubjects() {
    const ticket=++revision;catalog=null;get('results').replaceChildren();status('Reading subjects…');
    window.dispatchEvent(new CustomEvent('mentat:comparison-context',{detail:{id:get('run').value,industry:get('industry').value}}));
    if(!get('industry').value){status('No completed reports in this run.');return;}
    const result=await api('/api/comparison?'+new URLSearchParams({id:get('run').value,industry:get('industry').value}));
    if(ticket!==revision)return;
    catalog={...result,run:get('run').value};search();
  }
  function search() {
    get('results').replaceChildren();if(!catalog)return;
    const term=MentatState.normalizeName(get('search').value),kind=get('kind').value;
    const matches=catalog.subjects.filter(s=>(!kind||s.kind===kind)&&MentatState.normalizeName(`${s.label} ${s.company} ${s.kind}`).includes(term));
    status(`${matches.length.toLocaleString()} matching subjects · ${selected.length} selected. Search to narrow the list.`);
    // Search is over the full catalog; a bounded result window keeps input responsive.
    for(const subject of matches.slice(0,100)) {
      const row=document.createElement('button');row.type='button';row.className='compare-result';row.draggable=true;
      row.textContent=`${subject.label} · ${subject.kind}${subject.company?' · '+subject.company:''}`;
      row.setAttribute('aria-label','Add '+row.textContent);
      row.ondragstart=e=>{dragging=subject.id;e.dataTransfer.setData('text/plain',subject.id);};
      row.onclick=()=>add(subject.id);get('results').append(row);
    }
    if(matches.length>100)status(`${matches.length.toLocaleString()} matches; showing 100. Refine your search to reach any subject. ${selected.length} selected.`);
  }
  function add(id) {
    const subject=catalog?.subjects.find(s=>s.id===id);if(!subject)return;
    const key=identity(subject);if(selected.some(s=>s.key===key))return;
    selected.push({...subject,key,industry:catalog.industry,run:catalog.run,reports:catalog.reports});render();search();
  }
  function total(s,metric,cycle=get('cycle').value) {
    const rows=Object.entries(s.periods).filter(([year])=>!cycle||year===cycle).map(([,v])=>v[metric]);
    return !rows.length||rows.some(v=>v==null)?null:rows.reduce((a,b)=>a+b,0);
  }
  function render() {
    const prior=get('cycle').value, years=[...new Set(selected.flatMap(s=>Object.keys(s.periods)))].sort();
    get('cycle').replaceChildren(new Option('All available cycles',''),...years.map(y=>new Option(`${Number(y)-1}–${y}`,y)));
    if(years.includes(prior))get('cycle').value=prior;
    get('cards').replaceChildren();
    get('empty').hidden=!!selected.length;get('chart').disabled=get('export').disabled=!selected.length;
    selected.forEach((s,index)=>{
      const card=document.createElement('article');card.className='compare-card';card.draggable=true;
      card.ondragstart=e=>{dragging=s.key;e.dataTransfer.setData('text/plain',s.key);};
      card.ondragover=e=>e.preventDefault();
      card.ondrop=e=>{e.preventDefault();e.stopPropagation();const from=selected.findIndex(s=>s.key===(e.dataTransfer.getData('text/plain')||dragging));if(from<0){add(e.dataTransfer.getData('text/plain')||dragging);return;}selected.splice(index,0,selected.splice(from,1)[0]);render();};
      card.innerHTML=`<div class="compare-card-heading"><div><small>${esc(s.kind)} · ${esc(s.industry)}</small><h3>${esc(s.label)}</h3>${s.company?'<p>'+esc(s.company)+'</p>':''}</div><button type="button" class="secondary" aria-label="Remove ${esc(s.label)}">×</button></div><dl>${Object.entries(measures).map(([key,label])=>`<dt>${esc(label)}</dt><dd>${money(total(s,key))}</dd>`).join('')}</dl><details><summary>Cycle history & sources</summary><table><thead><tr><th>Cycle</th><th>Net giving</th><th>Change</th></tr></thead><tbody>${Object.entries(s.periods).sort().map(([y,v])=>{const previous=s.periods[String(Number(y)-2)]?.giving;return `<tr><td>${Number(y)-1}–${y}</td><td>${money(v.giving)}</td><td>${previous==null||v.giving==null?'Unavailable':money(v.giving-previous)}</td></tr>`;}).join('')}</tbody></table><p>Amounts follow saved report classifications. People are identified by the report’s person key and company.</p>${s.reports.map(r=>`<p class="chart-note">${esc(r.name)} · SHA-256 ${esc(r.sha256)}</p>`).join('')}</details>`;
      card.querySelector('button').onclick=()=>{selected=selected.filter(x=>x.key!==s.key);render();search();};
      get('cards').append(card);
    });
    get('count').textContent=`${selected.length} subjects`;
  }
  get('cards').ondragover=e=>e.preventDefault();get('cards').ondrop=e=>{e.preventDefault();add(e.dataTransfer.getData('text/plain')||dragging);};
  get('search').oninput=search;get('kind').onchange=search;get('cycle').onchange=render;
  get('run').onchange=()=>loadIndustries().catch(e=>status(e.message));get('industry').onchange=()=>loadSubjects().catch(e=>status(e.message));
  get('clear').onclick=()=>{selected=[];render();search();};
  get('chart').onclick=()=>{
    const rows=selected.flatMap(s=>Object.entries(s.periods).filter(([year])=>!get('cycle').value||year===get('cycle').value).map(([year,v])=>[`${s.label} · ${s.kind} · ${s.industry}`,Number(year),...Object.keys(measures).map(m=>v[m]??null)]));
    const columns=['Subject','Election cycle',...Object.values(measures)].map((name,index)=>({name,index,type:index?'number':'category',distinct:new Set(rows.map(r=>r[index])).size,missing:rows.filter(r=>r[index]==null).length}));
    const data={columns,rows,row_numbers:rows.map((_,i)=>i+1),row_count:rows.length,header_row:1,sheet:'Comparison',sha256:'See subject source fingerprints',note:'Independent subjects can overlap. Filter Subject before time analysis. Election-cycle totals are not annual totals. '+selected.flatMap(s=>s.reports.map(r=>`${s.label}: ${r.name} SHA-256 ${r.sha256}`)).join('; ')};
    document.getElementById('graph-builder').showModal();window.dispatchEvent(new CustomEvent('mentat:chart-data',{detail:data}));
  };
  get('export').onclick=()=>{
    const quote=v=>'"'+String(typeof v==='string'&&/^[=+\-@\t\r]/.test(v)?"'"+v:v??'').replaceAll('"','""')+'"';
    const rows=[['Subject','Kind','Industry','Run','Cycle',...Object.values(measures),'Report SHA-256'],...selected.flatMap(s=>Object.entries(s.periods).filter(([y])=>!get('cycle').value||y===get('cycle').value).map(([y,v])=>[s.label,s.kind,s.industry,s.run,y,...Object.keys(measures).map(k=>v[k]),s.reports.map(r=>r.sha256).join('; ')]))];
    const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.map(r=>r.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='mentat-comparison.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  window.addEventListener('mentat:graph',e=>{context=e.detail;});
  get('open').onclick=async()=>{
    const visible=get('panel').hidden;get('panel').hidden=!visible;
    get('open').setAttribute('aria-expanded',String(visible));get('open').textContent=visible?'Back to graph':'Advanced comparison';
    if(!visible){get('open').focus();return;}
    const heading=get('panel').querySelector('h2');if(heading){heading.tabIndex=-1;heading.focus();}
    if(!catalog&&!opening){opening=loadRuns().catch(e=>status(e.message)).finally(()=>{opening=null;});}
    if(opening)await opening;
  };
  render();
})();
