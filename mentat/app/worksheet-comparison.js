/* Compare independently filtered worksheets at an explicitly chosen grouping.
   Each source is aggregated once. Missing groups stay missing; sources are
   never added together or joined at transaction grain. */
(function(root){
  'use strict';
  const math=typeof module!=='undefined'&&module.exports?require('./chart-math.js'):root.MentatCharts;
  function compare(sources){
    const keys=new Set(),series=sources.map((source,index)=>{
      const config={...source.config,type:'bar',transform:'none',series:-1,maxGroups:100000};
      const field=source.data.columns[config.y];
      if(config.aggregation==='sum'&&field?.accounting?.safe_to_sum===false)throw new Error(field.accounting.reason||'Choose an additive measure or another aggregation.');
      const result=math.build(source.data,config),values=new Map(result.groups.map(g=>[g.label,g.value]));
      for(const key of values.keys())keys.add(key);
      return {id:'source_'+index,label:source.label,values,selected:result.selected,excluded:result.excluded};
    });
    return {series,rows:[...keys].map(group=>Object.fromEntries([['group',group],...series.map(s=>[s.id,s.values.has(group)?s.values.get(group):null])]))};
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={compare};return;}
  const host=document.getElementById('studio-comparison'),toggle=document.getElementById('studio-compare-sheets');
  if(!host||!toggle)return;
  const make=(tag,text,className)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
  const cards=host.querySelector('.worksheet-comparison-sources'),canvas=host.querySelector('.worksheet-comparison-canvas'),note=host.querySelector('[role="status"]');
  let runs=[],sources=[],revision=0;
  const api=async path=>{const response=await fetch(path);const body=await response.json();if(!response.ok||body.error)throw new Error(body.error||'Source unavailable.');return body;};
  const options=(select,items)=>select.replaceChildren(...items.map(([id,label])=>new Option(label,id)));
  function control(parent,name,type='select'){
    const label=make('label',name),input=make(type);input.setAttribute('aria-label',name);label.append(input);parent.append(label);return input;
  }
  function changed(){revision++;host._comparison=null;host.querySelector('[data-compare-export]').disabled=true;canvas.replaceChildren();host.querySelector('.worksheet-comparison-evidence').replaceChildren();note.textContent='Selection changed. Compare to refresh the results.';}
  function addSource(){
    if(sources.length>=4)return;
    const card=make('fieldset'),legend=make('legend','Source '+(sources.length+1));card.append(legend);
    const source={card,revision:0,data:null};sources.push(source);cards.append(card);
    source.run=control(card,'Report folder');options(source.run,runs.map(r=>[r.id,r.name]));
    source.report=control(card,'Report');source.sheet=control(card,'Worksheet');
    source.header=control(card,'Header row (blank = detect)','input');source.header.type='number';source.header.min=1;source.header.max=100;
    const pair=make('div',null,'worksheet-comparison-fields');card.append(pair);
    source.group=control(pair,'Group by');source.measure=control(pair,'Measure');source.aggregate=control(pair,'Aggregation');
    options(source.aggregate,root.MentatStudioCore.AGGREGATIONS);
    source.start=control(pair,'First worksheet row','input');source.start.type='number';source.start.min=1;source.start.placeholder='All rows';
    source.end=control(pair,'Last worksheet row','input');source.end.type='number';source.end.min=1;source.end.placeholder='All rows';
    source.filters=make('div',null,'worksheet-comparison-filters');card.append(source.filters);
    const add=make('button','Add filter');add.type='button';add.onclick=()=>addFilter(source);card.append(add);
    source.status=make('p','Choose a worksheet.','studio-note');card.append(source.status);
    const remove=make('button','Remove source');remove.type='button';remove.onclick=()=>{sources=sources.filter(s=>s!==source);source.revision++;card.remove();changed();updateAdd();};card.append(remove);
    source.run.onchange=()=>loadReports(source);source.report.onchange=()=>loadSheets(source);source.sheet.onchange=()=>loadData(source);
    source.header.onchange=()=>loadData(source);
    for(const control of [source.group,source.measure,source.aggregate,source.start,source.end])control.onchange=changed;
    source.measure.onchange=()=>{source.aggregate.disabled=Number(source.measure.value)<0;if(source.aggregate.disabled)source.aggregate.value='count';changed();};
    updateAdd();loadReports(source);
  }
  function updateAdd(){host.querySelector('[data-compare-add]').disabled=sources.length>=4;}
  function path(source,withSheet=false){return '/api/report-data?'+new URLSearchParams({id:source.run.value,index:source.report.value,...(withSheet?{sheet:source.sheet.value,...(source.header.value?{header:source.header.value}:{})}:{})});}
  async function loadReports(source){
    const ticket=++source.revision;source.data=null;changed();source.status.textContent='Reading reports…';
    try{const body=await api('/api/reports?id='+encodeURIComponent(source.run.value));if(ticket!==source.revision)return;options(source.report,(body.reports||[]).map(r=>[r.index,r.name]));await loadSheets(source);}catch(e){if(ticket===source.revision)source.status.textContent=e.message;}
  }
  async function loadSheets(source){
    const ticket=++source.revision;source.data=null;changed();source.status.textContent='Reading worksheets…';
    try{const body=await api(path(source));if(ticket!==source.revision)return;options(source.sheet,(body.sheets||[]).map(s=>[s,s]));const overview=body.sheets?.find(s=>/^(Crypto )?Spending Overview$/.test(s));if(overview)source.sheet.value=overview;await loadData(source);}catch(e){if(ticket===source.revision)source.status.textContent=e.message;}
  }
  async function loadData(source){
    const ticket=++source.revision;source.data=null;changed();source.status.textContent='Scanning worksheet…';source.filters.replaceChildren();
    try{const data=await api(path(source,true));if(ticket!==source.revision)return;source.data=data;
      options(source.group,[[-1,'All selected rows'],...data.columns.map((f,i)=>[i,f.name])]);
      const numeric=data.columns.map((f,i)=>[f,i]).filter(([f])=>f.type==='number'||f.numeric_values>0);
      options(source.measure,[[-1,'Row count'],...numeric.map(([f,i])=>[i,f.name])]);
      const meta=root.MentatStudioCore.classifyFields(data.columns);source.group.value=String(meta.dimensions.find(f=>!f.hidden)?.index??-1);source.measure.value=String(meta.measures[0]?.index??numeric.find(([f])=>/amount|giving|total/i.test(f.name))?.[1]??-1);
      source.aggregate.disabled=Number(source.measure.value)<0;if(source.aggregate.disabled)source.aggregate.value='count';
      source.status.textContent=data.row_count.toLocaleString()+' rows · '+data.sheet;
    }catch(e){if(ticket===source.revision)source.status.textContent=e.message;}
  }
  function addFilter(source){
    if(!source.data)return;
    const row=make('div',null,'worksheet-comparison-filter'),field=control(row,'Filter field'),op=control(row,'Filter operation'),value=control(row,'Filter value','input');
    options(field,source.data.columns.map((f,i)=>[i,f.name]));options(op,[['equals','Equals'],['contains','Contains'],['gte','At least'],['lte','At most']]);
    const remove=make('button','Remove filter');remove.type='button';remove.onclick=()=>{row.remove();changed();};row.append(remove);
    row.read=()=>({column:Number(field.value),op:op.value,value:value.value});for(const n of [field,op,value])n.onchange=changed;source.filters.append(row);changed();
  }
  function draw(){
    const ticket=++revision;
    try{
      if(sources.length<2)throw new Error('Add at least two sources to compare.');
      if(sources.some(s=>!s.data))throw new Error('Wait for each selected worksheet to finish loading.');
      const chosen=sources.map((s,i)=>({data:s.data,label:`${String.fromCharCode(65+i)} · ${s.report.selectedOptions[0].textContent} / ${s.sheet.value} · ${s.aggregate.selectedOptions[0].textContent} ${s.measure.selectedOptions[0].textContent}`,
        config:{x:Number(s.group.value),y:Number(s.measure.value),aggregation:Number(s.measure.value)<0?'count':s.aggregate.value,start:Number(s.start.value)||0,end:Number(s.end.value)||0,limit:0,filters:[...s.filters.children].map(row=>row.read()).filter(f=>f.value!=='')}}));
      const result=compare(chosen);if(ticket!==revision)return;
      const columns=[{id:'group',name:'Group',role:'dimension'},...result.series.map(s=>({id:s.id,name:s.label,role:'measure',unit:'number'}))];
      const type=host.querySelector('[data-compare-type]').value;
      root.MentatContributionPlot.render(canvas,result.rows,{type,dimensions:['group'],measures:result.series.map(s=>s.id),columns,title:'Worksheet comparison',labels:true});
      note.textContent=`${result.rows.length} matched group labels · `+result.series.map(s=>`${s.label}: ${s.selected} selected rows, ${s.excluded} excluded`).join(' · ')+'. Sources retain their own units. A blank means that source has no matching group.';
      const evidence=host.querySelector('.worksheet-comparison-evidence');evidence.replaceChildren(...chosen.map(s=>make('p',s.label+' · SHA-256 '+s.data.sha256+' · '+(s.data.note||''),'studio-note')));
      host._comparison={result,columns};
      host.querySelector('[data-compare-export]').disabled=false;
    }catch(e){changed();note.textContent=e.message;}
  }
  host.querySelector('[data-compare-run]').onclick=draw;
  host.querySelector('[data-compare-type]').onchange=()=>{if(host._comparison)draw();};
  host.querySelector('[data-compare-add]').onclick=addSource;
  host.querySelector('[data-compare-export]').onclick=()=>{
    const value=host._comparison;if(!value)return;const ids=['group',...value.result.series.map(s=>s.id)];
    const quote=v=>'"'+String(typeof v==='string'&&/^\s*[=+@-]/.test(v)?"'"+v:v??'').replaceAll('"','""')+'"';
    const csv=[value.columns.map(c=>quote(c.name)).join(','),...value.result.rows.map(r=>ids.map(id=>quote(r[id])).join(','))].join('\r\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),a=make('a');a.href=url;a.download='worksheet-comparison.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const close=()=>{host.hidden=true;document.querySelector('#view-chart-studio .studio').classList.remove('studio-comparing');toggle.setAttribute('aria-pressed','false');toggle.focus();};
  host.querySelector('[data-compare-close]').onclick=close;
  toggle.onclick=async()=>{
    if(!host.hidden){close();return;}host.hidden=false;document.querySelector('#view-chart-studio .studio').classList.add('studio-comparing');toggle.setAttribute('aria-pressed','true');
    if(sources.length)return;note.textContent='Reading report folders…';
    try{const body=await api('/api/report-sources');runs=body.runs||[];addSource();addSource();}catch(e){note.textContent=e.message;}
  };
})(globalThis);
