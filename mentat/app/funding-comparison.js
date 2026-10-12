/* Point-and-click report-bound source → recipient comparisons. */
(() => {
  const el=id=>document.getElementById('flow-'+id), esc=escapeHtml;
  let catalog=null, ticket=0, chosenSources=new Set(), chosenTargets=new Set(), channels=new Set(), page=0, chartData=null;
  const normalize=s=>MentatState.normalizeName(s), options=(node,items)=>node.replaceChildren(...items.map(([v,n])=>new Option(n,v)));
  function targetMatches() {
    const q=normalize(el('target-search').value);
    return (catalog?.destinations||[]).filter(d=>d.kind===el('mode').value&&(!el('category').value||d.category===el('category').value)&&normalize(d.label+' '+d.id).includes(q));
  }
  function choices(node,items,selected,onchange,limit=100) {
    node.replaceChildren();
    for(const item of items.slice(0,limit)){
      const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=selected.has(item.id);
      input.onchange=()=>{if(input.checked)selected.add(item.id);else selected.delete(item.id);page=0;onchange();};
      label.append(input,document.createTextNode(`${item.label} · ${item.category} · ${item.id.replace(/^[^:]+:/,'')}`));node.append(label);
    }
    if(items.length>limit){const more=document.createElement('button');more.type='button';more.className='secondary';more.textContent=`Show more (${limit} of ${items.length} matches)`;more.onclick=()=>choices(node,items,selected,onchange,limit+100);node.append(more);}
  }
  function chips(node,items,selected){
    node.replaceChildren(...items.filter(s=>selected.has(s.id)).map(s=>{const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=s.label+' ×';button.setAttribute('aria-label','Remove '+s.label);button.onclick=()=>{selected.delete(s.id);page=0;renderChoices();draw();};return button;}));
  }
  function renderChoices(){
    if(!catalog)return;
    const q=normalize(el('source-search').value),type=el('source-type').value;
    const matches=catalog.sources.filter(s=>(!type||s.category===type)&&normalize(s.label+' '+s.id).includes(q));
    choices(el('sources'),matches,chosenSources,()=>{renderChoices();draw();});
    choices(el('targets'),targetMatches(),chosenTargets,()=>{renderChoices();draw();});
    chips(el('source-picks'),catalog.sources,chosenSources);chips(el('target-picks'),catalog.destinations,chosenTargets);
  }
  function categories(){
    const prior=el('category').value;
    options(el('category'),[['','All receiver types'],...[...new Set(catalog.destinations.filter(d=>d.kind===el('mode').value).map(d=>d.category))].sort().map(v=>[v,v])]);
    el('category').disabled=el('mode').value==='Candidate';
    if([...el('category').options].some(o=>o.value===prior))el('category').value=prior;
  }
  async function load(detail){
    const current=++ticket;catalog=null;chartData=null;chosenSources.clear();chosenTargets.clear();page=0;
    for(const id of ['sources','targets','source-picks','target-picks','channels','preview','table'])el(id).replaceChildren();
    el('chart').disabled=true;el('status').textContent='Reading saved funding relationships…';
    try{
      const result=await api('/api/comparison-flows?'+new URLSearchParams(detail));if(current!==ticket)return;
      catalog=result;options(el('cycle'),[['','All available cycles'],...result.cycles.map(c=>[c,`${Number(c)-1}–${c}`])]);
      options(el('source-type'),[['','All companies and committees'],...[...new Set(result.sources.map(s=>s.category))].sort().map(t=>[t,t])]);
      channels=new Set(result.rows.map(r=>r.channel));
      for(const channel of channels){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=true;input.onchange=()=>{if(input.checked)channels.add(channel);else channels.delete(channel);draw();};label.append(input,document.createTextNode(channel));el('channels').append(label);}
      categories();renderChoices();draw();el('note').textContent=result.note+' '+result.issues.join(' ');
    }catch(e){if(current===ticket)el('status').textContent=e.message;}
  }
  function draw(){
    chartData=null;el('chart').disabled=true;el('preview').replaceChildren();el('table').replaceChildren();if(!catalog)return;
    const count=Number(el('count').value);if(!Number.isInteger(count)||count<1||count>60){el('status').textContent='Enter 1–60 recipients per view. All selections are retained across pages.';return;}
    const targets=catalog.destinations.filter(d=>chosenTargets.has(d.id)&&d.kind===el('mode').value);
    page=Math.min(page,Math.max(0,Math.ceil(targets.length/count)-1));const visible=targets.slice(page*count,(page+1)*count),ids=new Set(visible.map(d=>d.id));
    el('prev').disabled=page===0;el('next').disabled=(page+1)*count>=targets.length;
    el('status').textContent=chosenSources.size&&targets.length?`${chosenSources.size} sources · recipients ${page*count+1}–${Math.min((page+1)*count,targets.length)} of ${targets.length} selected. Chart and export use this page; amounts include all matching observations.`:'Choose at least one source and one candidate or recipient to create your chart.';
    if(!chosenSources.size||!targets.length)return;
    const cycle=el('cycle').value, incompletePAC=(catalog.unavailable_activity_cycles||[]).some(c=>!cycle||c===cycle);
    const all=catalog.rows.filter(r=>chosenSources.has(r.source)&&channels.has(r.channel)&&(!cycle||r.cycle===cycle)&&!(incompletePAC&&r.source.startsWith('committee:')));
    if(incompletePAC)el('status').textContent+=' PAC totals are unavailable across the selected cycles because some activity is missing; choose a complete cycle to inspect its observations.';
    const sourceMap=new Map(catalog.sources.map(s=>[s.id,s])),targetMap=new Map(visible.map(d=>[d.id,d]));
    const rows=all.filter(r=>ids.has(r.destination)).map(r=>[`${targetMap.get(r.destination).label} [${r.destination.split(':').at(-1)}]`,`${sourceMap.get(r.source).label} [${r.source}] · ${r.channel}`,r.amount,Number(r.cycle)]);
    // Preserve missing pairs without inventing zero or summing distinct channels.
    const series=new Set(all.map(r=>`${sourceMap.get(r.source).label} [${r.source}] · ${r.channel}`));
    for(const id of chosenSources)if(!all.some(r=>r.source===id))series.add(`${sourceMap.get(id).label} [${id}] · no matching observations`);
    const present=new Set(rows.map(r=>JSON.stringify(r.slice(0,2))));
    for(const d of visible)for(const s of series){const label=`${d.label} [${d.id.split(':').at(-1)}]`;if(!present.has(JSON.stringify([label,s])))rows.push([label,s,null,cycle?Number(cycle):null]);}
    const columns=['Recipient','Source / activity','Amount','Election cycle'].map((name,index)=>({name,index,type:index<2?'category':'number',distinct:new Set(rows.map(r=>r[index])).size}));
    chartData={columns,rows,series_order:[...series],row_numbers:rows.map((_,i)=>i+1),row_count:rows.length,header_row:0,sheet:'Funding comparison',independent_series:1,sha256:catalog.reports.map(r=>r.sha256).join('; '),note:el('status').textContent+' '+catalog.note+' '+catalog.issues.join(' ')+' '+catalog.reports.map(r=>r.name+' SHA-256 '+r.sha256).join('; '),default_config:{type:el('type').value,x:0,series:1,y:2,aggregation:'sum',transform:'none'}};
    try{
      const chart=MentatCharts.build(chartData,chartData.default_config);
      el('preview').append(MentatGroupedSVG(chart,'Funding comparison',chartData.note));
      el('table').innerHTML='<table><thead><tr><th>Recipient</th>'+chart.series.map(s=>'<th>'+esc(s)+'</th>').join('')+'</tr></thead><tbody>'+chart.groups.map(g=>'<tr><th>'+esc(g.label)+'</th>'+g.values.map(v=>'<td>'+(v.value===null?'Unavailable':v.value.toLocaleString('en-US',{style:'currency',currency:'USD'}))+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
      el('chart').disabled=false;
    }catch(e){el('status').textContent=e.message;chartData=null;}
  }
  for(const id of ['source-search','target-search'])el(id).oninput=renderChoices;
  el('source-type').onchange=renderChoices;
  el('mode').onchange=()=>{page=0;categories();renderChoices();draw();};
  el('category').onchange=()=>{page=0;renderChoices();draw();};
  for(const id of ['cycle','type','count'])el(id).oninput=()=>{page=0;draw();};
  el('select-matches').onclick=()=>{for(const d of targetMatches())chosenTargets.add(d.id);renderChoices();draw();};
  el('prev').onclick=()=>{page--;draw();};el('next').onclick=()=>{page++;draw();};
  el('clear').onclick=()=>{chosenSources.clear();chosenTargets.clear();page=0;renderChoices();draw();};
  el('chart').onclick=()=>{if(!chartData)return;document.getElementById('graph-builder').showModal();window.dispatchEvent(new CustomEvent('mentat:chart-data',{detail:chartData}));};
  window.addEventListener('mentat:comparison-context',e=>load(e.detail));
})();
