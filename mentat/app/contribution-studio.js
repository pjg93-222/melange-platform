/* Contribution Studio: source-backed aggregation, explicit accounting and local views. */
(function (root) {
  'use strict';
  const money = new Set(['amount','gross_amount','refunds','average_amount','median_amount','min_amount','max_amount']);
  const defaults = () => ({dataset:'Banking',basis:'receipt',dimensions:['company'],measures:['amount'],filters:[],aggregate_filters:[],cycle:'',company:'',type:'bar',color:'',sort:'amount-desc',limit:50,offset:0,labels:true,congress:'119',layers:['states']});
  const clone = value => JSON.parse(JSON.stringify(value));
  const basisLabel=(basis,fallback=basis)=>basis==='original_supported'?'Supported original giving (gross subset)':fallback;
  const basisFields=(fields,basis)=>basis==='original_supported'?fields.map(field=>field.id==='amount'?{...field,label:'Supported gross amount',name:'Supported gross amount'}:field):fields;
  const sameExportSource=(a={},b={})=>['workbook_sha256','index_token','query_engine_sha256'].every(key=>a[key]===b[key]);
  function markFilters(request,selection){const filters=[...(request.filters||[])];if(selection)for(const dimension of request.dimensions){const value=selection[dimension]??'';filters.push({field:dimension,op:Array.isArray(value)?'in':'eq',value:Array.isArray(value)?[...value]:value});}return filters;}
  function buildQuery(view, overrides={}) {
    const dimensions = [...new Set([...view.dimensions,...(view.color?[view.color]:[])])];
    if(view.type==='map'&&dimensions.includes('zip5')&&!dimensions.includes('state'))dimensions.push('state');
    const filters = [...view.filters];
    if(view.cycle)filters.push({field:'cycle',op:'eq',value:view.cycle});
    if(view.company)filters.push({field:'company',op:'eq',value:view.company});
    const first=view.measures[0]||'amount';
    const sort=view.sort==='label'&&dimensions.length?[{field:dimensions[0],direction:'asc'}]:[{field:first,direction:view.sort==='amount-asc'?'asc':'desc'}];
    return {dataset:view.dataset,basis:view.basis,dimensions,measures:[...view.measures],filters,aggregate_filters:[...view.aggregate_filters],sort,limit:view.type==='map'?100000:Number(view.limit),offset:view.type==='map'?0:view.offset,...overrides};
  }
  function filterValue(value,field,fields) {
    const f=fields.find(f=>f.id===field);
    if(f?.role==='measure') {
      if(!/^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(value).trim()))throw new Error('Enter a number without a currency symbol or commas.');
      const number=Number(value), result=money.has(field)?Math.round(number*100):number;
      if(!Number.isFinite(result)||!Number.isSafeInteger(result))throw new Error(money.has(field)?'Use an amount with at most two decimal places.':'Use a whole count.');
      if(money.has(field)&&Math.abs(number*100-result)>0.00001)throw new Error('Use an amount with at most two decimal places.');
      return result;
    }
    return String(value).trim();
  }
  const csvCell = value => {
    let text=String(value??'');
    if(typeof value==='string'&&/^[\s]*[=+@-]/.test(text))text="'"+text;
    return '"'+text.replaceAll('"','""')+'"';
  };
  function csv(result,request) {
    const cols=result.columns, headers=cols.map(c=>c.id+(c.unit==='cents'?'_usd':''));
    const scope=JSON.stringify({filters:request.filters,aggregate_filters:request.aggregate_filters});
    return '\uFEFF'+[headers.concat(['dataset','accounting_basis','workbook_sha256','analytical_index','query_engine_sha256','query_filters']).map(csvCell).join(','),...result.rows.map(row=>cols.map(c=>c.unit==='cents'&&row[c.id]!=null?(row[c.id]/100):row[c.id]).concat([request.dataset,request.basis,result.provenance?.workbook_sha256||'',result.provenance?.index_token||'',result.provenance?.query_engine_sha256||'',scope]).map(csvCell).join(','))].join('\r\n');
  }
  function calendarNotice(request={},rows=[],now=new Date()) {
    const year=now.getFullYear();if(!Number.isFinite(year))return '';
    const cycleEnd=year+year%2,values={cycle:[],year:[]};
    for(const field of ['cycle','year']){
      if(request.dimensions?.includes(field))for(const row of rows)values[field].push(row[field]);
      for(const filter of request.filters||[])if(filter.field===field&&(filter.op==='eq'||filter.op==='in'))values[field].push(...(Array.isArray(filter.value)?filter.value:[filter.value]));
    }
    const ongoingCycle=values.cycle.some(value=>{const match=String(value??'').trim().match(/^(?:(\d{4})[-–])?(\d{4})$/);return match&&Number(match[2])===cycleEnd&&(!match[1]||Number(match[1])===cycleEnd-1);});
    const ongoingYear=values.year.some(value=>String(value??'').trim()===String(year));
    const periods=[];
    if(ongoingCycle)periods.push(`${cycleEnd-1}–${cycleEnd} is an ongoing calendar cycle`);
    if(ongoingYear)periods.push(`${year} is an ongoing calendar year`);
    return periods.length?periods.join('; ')+'. Collection coverage is not established.':'';
  }
  /* F3, 2026-09-24 (Paul: "FEC: Whatever is most efficient", one link per row).
     ActBlue and WinRed filings have no FEC page image - all 14 tried answered
     "Error#1896: Image is not processed yet" - so a record row filed by either
     links the whole electronic FEC report instead. PAC evidence gets these URLs
     from the server (app/fec_citation.py); this page builds its own record
     links, so the same two committees and the same report URL are mirrored here
     and nowhere else on the page. Every other row keeps its page-image link. */
  const FEC_CONDUITS=new Set(['C00401224','C00694323']);
  const REPORT_TITLE='FEC has no page image for this filing';
  const isConduitRecord=row=>FEC_CONDUITS.has(String(row?.committee_id||'').trim().toUpperCase());
  // The one link a conduit record gets: {href,text,title}; {pending:true} while
  // its report number is being read (fileNumber undefined); null when there is none.
  function filingLink(row,fileNumber){
    if(!isConduitRecord(row))return null;
    if(fileNumber===undefined)return {pending:true};
    const number=String(fileNumber??'').trim();
    if(!/^[1-9]\d{0,11}$/.test(number))return null;
    return {href:`https://docquery.fec.gov/cgi-bin/forms/${String(row.committee_id).trim().toUpperCase()}/${number}/`,
      text:'FEC report',title:REPORT_TITLE};
  }
  // The second, narrow records request for a page's conduit rows: the same
  // selection, keyed to their source IDs, reading the reported file number
  // through the pinned source database (no index rebuild). null when none.
  function reportNumberQuery(query,rows){
    const ids=[...new Set((rows||[]).filter(isConduitRecord).map(row=>String(row.source_id||'')).filter(Boolean))];
    return ids.length?{...query,dimensions:['reported_file_num'],aggregate_filters:[],
      filters:[...(query.filters||[]),{field:'source_id',op:'in',value:ids}],limit:ids.length,offset:0}:null;
  }
  /* P6, batch 2 (2026-09-25). While the console builds a query view or a records sidecar, the two
     routes answer at once, HTTP 200, {status:'building', phase, message, processed_records,
     total_records (an int, or null), elapsed_ms, retry_after_ms, ...}; the index build's older
     {status:'preparing', ...} means the same. Only those two are pending here: 'queued' belongs to
     snapshot jobs. Code review finding 7 (2026-09-25): the contract lives in one place, Chart
     Studio's pollPending (chart-studio-contributions.js, loaded before this file), so this page and
     Chart Studio stop at the same time for the same answers. These names adapt it for this page:
     retry_after_ms held between 250 ms and 5 s, else 0.5, 1, 2, then 4 s; after about 15 minutes of
     polling the page stops and offers Retry, which sends retry:true once. */
  const shared=(typeof module!=='undefined'&&module.exports?require('./chart-studio-contributions.js'):root.MentatStudioContributions)||null;
  const {PENDING,PENDING_CEILING_MS,pendingDelay}=shared||{};
  const isPending=answer=>Boolean(shared.pendingStatus(answer));
  const pendingText=(answer,path='query')=>shared.pendingText(shared.pendingProgress(answer,{path}));
  const pendingCeiling=(path,ceilingMs)=>shared.pendingCeiling(path,ceilingMs,undefined,'this page');
  // ask(body) sends one request; onProgress hears pendingText() and the progress of each pending
  // answer. retry:true goes on the first request only. current() false stops at once (AbortError);
  // an error, a 'failed' answer and the ceiling are marked retryable. Finding 7 repair (2026-09-25):
  // if chart-studio-contributions.js did not load, the page still boots and takes a ready answer
  // (status 'ready', or none); it does not poll, and any other answer stops with a reload message.
  async function pollReady(ask,{path='query',body={},current=null,retry=false,onProgress=null,sleep,now,ceilingMs}={}){
    if(!shared){const answer=await ask(retry===true?{...body,retry:true}:body);if(answer&&typeof answer==='object'&&answer.status!=null&&answer.status!=='ready')throw new Error('This page could not load chart-studio-contributions.js, so it cannot wait while the server prepares this view. Reload the page.');return answer;}
    return shared.pollPending(ask,{path,body,current,retry,sleep,now,ceilingMs,surface:'this page',
      onProgress:onProgress&&(progress=>onProgress(shared.pendingText(progress),progress))});
  }
  const api={defaults,buildQuery,filterValue,csv,csvCell,calendarNotice,basisLabel,basisFields,sameExportSource,markFilters,
    FEC_CONDUITS,filingLink,reportNumberQuery,isConduitRecord,
    PENDING,PENDING_CEILING_MS,isPending,pendingDelay,pendingText,pendingCeiling,pollReady};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  root.MentatContributionStudio=api;
  const $=id=>document.getElementById(id), plot=root.MentatContributionPlot;
  let view=defaults(),catalog=null,token='',result=null,renderedRequest=null,svg=null,revision=0,geoRevision=0,facetDataset='',history=[],mapState=null,mapGeometry=null,mapTimer=null,recordOffset=0,recordSelection=null,recordRevision=0,exporting=false;
  const storageKey='mentat-contribution-views-v1';
  const dataset=()=>catalog?.datasets.find(d=>d.id===view.dataset);
  const fields=()=>basisFields(dataset()?.fields||catalog?.fields||[],view.basis);
  const fieldName=id=>fields().find(f=>f.id===id)?.label||id;
  const text=(id,value)=>{$(id).textContent=value??'';};
  const node=(tag,content,className)=>{const n=document.createElement(tag);if(content!=null)n.textContent=content;if(className)n.className=className;return n;};
  function showError(message){text('error',message);$('error').hidden=!message;}
  function status(message,busy=false){const notice=!busy&&result&&renderedRequest?calendarNotice(renderedRequest,result.rows):'';text('status',[message,notice].filter(Boolean).join(' '));$('status').classList.toggle('busy',busy);$('visualization').setAttribute('aria-busy',String(busy));}
  function option(select,value,label){const o=node('option',label);o.value=value;select.append(o);return o;}
  function options(id,values,selected,empty){const select=$(id);select.replaceChildren();if(empty)option(select,'',empty);values.forEach(v=>option(select,typeof v==='object'?v.id:v,typeof v==='object'?(v.label||v.name):v));select.value=selected;}
  function offerRetry(container,action){const button=node('button','Retry','retry');button.type='button';button.onclick=()=>{button.disabled=true;action();};container.append(' ',button);}
  function failure(error,retry){if(error?.name==='AbortError')return;showError(error?.message||String(error));if(retry&&error?.retryable)offerRetry($('error'),retry);status('View unavailable. Adjust the selection or retry.');}
  async function request(path,data){
    const response=await fetch('/api/contributions/'+path,data?{method:'POST',headers:{'Content-Type':'application/json','X-Mentat-Token':token},body:JSON.stringify(data)}:{});
    const body=await response.json();if(!response.ok||body.error)throw new Error(body.error||'Contribution service unavailable.');return body;
  }
  function ready(path,query,current,onProgress,retry=false){
    return pollReady(body=>request(path,body),{path,body:query,current,retry,onProgress});
  }
  function remember(){history.push(clone(view));if(history.length>40)history.shift();$('undo').disabled=false;}
  function change(action,{query=true}={}){remember();action();if(query)view.offset=0;sync();if(query)run();else render();}
  function basisHelp(){
    const descriptions={original_observed:'Evidence-aware original giving. Ambiguous observations remain provisional; review the supported subset and unresolved exposure.',original_supported:'Supported original giving is a gross subset of positive amounts supported by the current evidence rules. Refunds are excluded. This is not the complete amount given.',receipt:'Matches the workbook’s included receipts. Receipts may repeat economic gifts or include intermediary stages.',raw:'Every retained source observation, including excluded and repeated representations. This is not a giving total.',pac_activity:'Recipient-reported PAC activity, separate from original giving. Coverage is not a complete Schedule B ledger.'};
    text('basis-help',descriptions[view.basis]);
  }
  function sync(){
    const d=dataset();if(!d)return;
    $('source').value=view.dataset;options('basis',d.bases.map(b=>({...b,label:basisLabel(b.id,b.label||b.name||b.id)})),view.basis);basisHelp();
    $('cycle').value=view.cycle;$('company').value=view.company;
    $('sort').value=view.sort;$('limit').value=String(view.limit);$('labels').checked=view.labels;$('labels').disabled=view.type==='map';$('congress').value=view.congress;
    options('color',fields().filter(f=>f.role==='dimension'&&(!['line','area'].includes(view.type)||f.id!==view.dimensions[0])),view.color,'Single colour');$('color').disabled=view.type==='map';
    $('map-settings').hidden=view.type!=='map';$('limit').disabled=view.type==='map';
    for(const layer of ['states','districts','zip_areas'])$(layer==='zip_areas'?'layer-zips':'layer-'+layer).checked=view.layers.includes(layer);
    document.querySelectorAll('[data-chart]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.chart===view.type)));
    for(const role of ['dimension','measure']){
      const shelf=$(role+'-shelf');shelf.replaceChildren();
      for(const id of view[role+'s']){
        const pill=node('span',null,'pill '+role);pill.draggable=true;pill.dataset.field=id;pill.ondragstart=e=>e.dataTransfer.setData('text/plain',id);
        pill.append(node('span',fieldName(id)));const remove=node('button','×');remove.type='button';remove.setAttribute('aria-label','Remove '+fieldName(id));
        remove.onclick=()=>change(()=>{view[role+'s']=view[role+'s'].filter(v=>v!==id);});pill.append(remove);shelf.append(pill);
      }
    }
    renderFields();renderFilters();renderSource(d.provenance);
    const dims=buildQuery(view).dimensions;text('grain-label',view.type==='map'?'One mark per ZIP, combining selected ZIP/state groups. Recorded state determines map eligibility.':dims.length?'One mark per '+dims.map(fieldName).join(' + '):'One mark for all selected records');
    const subject={original_observed:'Observed giving',original_supported:'Supported original giving (gross subset)',receipt:'Included receipts',raw:'Raw observations',pac_activity:'PAC receipts'}[view.basis]||'Contributions';
    const title=subject+(view.type==='map'?' by Contributor ZIP':view.dimensions.length?' by '+view.dimensions.map(fieldName).join(' / '):' in this selection');
    text('view-title',title);if(document.activeElement!==$('view-name'))$('view-name').value=title;
  }
  function renderFields(){
    const search=$('field-search').value.toLowerCase();$('fields').replaceChildren();
    for(const role of ['dimension','measure']){
      const section=node('section',null,'field-section');section.append(node('h3',role==='dimension'?'Dimensions':'Measures'));
      for(const f of fields().filter(f=>f.role===role&&f.label.toLowerCase().includes(search))){
        const row=node('div',null,'field-row');row.draggable=true;row.ondragstart=e=>e.dataTransfer.setData('text/plain',f.id);
        row.append(node('span',role==='dimension'?(f.id==='zip5'?'◎':'Abc'):'#','field-glyph'),node('span',f.label));
        const add=node('button','+');add.type='button';add.setAttribute('aria-label','Add '+f.label+' to '+role+'s');add.disabled=view[role+'s'].includes(f.id);add.onclick=()=>addField(f.id,role);row.append(add);section.append(row);
      }
      $('fields').append(section);
    }
  }
  function addField(id,role){const field=fields().find(f=>f.id===id);if(!field||field.role!==role)return;if(view[role+'s'].includes(id))return;change(()=>view[role+'s'].push(id));}
  function renderFilters(){
    $('filter-chips').replaceChildren();
    for(const [stage,list]of [['filters',view.filters],['aggregate_filters',view.aggregate_filters]])list.forEach((f,i)=>{
      const value=money.has(f.field)?plot.format(f.value,f.field):Array.isArray(f.value)?f.value.join(', '):f.value;
      const chip=node('span',null,'filter-chip');chip.append(node('span',(stage==='aggregate_filters'?'Grouped ':'')+fieldName(f.field)+' '+f.op+' '+value));
      const remove=node('button','×');remove.type='button';remove.setAttribute('aria-label','Remove filter '+fieldName(f.field));remove.onclick=()=>change(()=>list.splice(i,1));chip.append(remove);$('filter-chips').append(chip);
    });
  }
  function renderSource(provenance){
    $('source-info').replaceChildren();for(const[key,label]of [['workbook','Workbook'],['cycle_basis','Cycle basis'],['scope','Coverage'],['workbook_sha256','Workbook SHA-256'],['database_sha256','Analytical source SHA-256'],['original_database_sha256','Original report source SHA-256']])if(provenance?.[key]){
      const row=node('div',null,'provenance-row');row.append(node('strong',label),node('span',provenance[key]));$('source-info').append(row);
    }
    text('source-stamp',provenance?.workbook?(provenance.workbook+' · SHA '+provenance.workbook_sha256.slice(0,12)):'');
  }
  function receipt(){
    const r=result,p=r.provenance||{};text('receipt-basis',basisLabel(view.basis,r.basis_label||view.basis));$('receipt-basis').classList.toggle('provisional',view.basis!=='original_supported');
    const amount=r.totals?.amount, items=[[view.basis==='original_supported'?'Selected gross amount':'Selected net amount',plot.format(amount,'amount')],['Included observations',plot.format(r.totals?.records,'records')],['Reported donor identities',plot.format(r.totals?.contributors,'contributors')]];
    if(r.comparison){items.push(['Supported gross subset',plot.format(r.comparison.original_supported,'amount')],['Unresolved exposure¹',plot.format(r.comparison.unresolved_gross_exposure,'amount')]);}
    if(view.aggregate_filters.length)items.push(['After grouped filters',plot.format(r.reconciliation?.grouped_amount_cents,'amount')]);
    $('receipt-values').replaceChildren();items.forEach(([label,value])=>{const div=node('div');div.append(node('dt',label),node('dd',value));$('receipt-values').append(div);});
    text('receipt-scope',view.dataset+' · '+(view.cycle||'All available cycles')+' · '+(view.company||'All tracked companies')+'. Totals cover matching source records before grouped filters. ¹ Absolute raw exposure flagged unresolved; it overlaps the selected amount and must not be added to it.');
    $('reconciliation').replaceChildren();
    const match=r.reconciliation?.receipt_matches_workbook;
    const lines=[match===true?'Included receipt amounts reconcile exactly to the pinned workbook.':match===false?'Receipt reconciliation failed. This source needs review.':'Receipt reconciliation is unavailable.',r.reconciliation?.conserved===true?'Grouping preserves the selected amount to the cent.':view.aggregate_filters.length?'Grouped filters select a subset after aggregation.':null,...(r.notices||[]),p.scope];
    lines.filter(Boolean).forEach(s=>$('reconciliation').append(node('p',s)));renderSource(p);
  }
  function clearResult(){
    clearTimeout(mapTimer);mapState?.destroy?.();
    result=null;renderedRequest=null;svg=null;mapState=null;mapGeometry=null;++geoRevision;++recordRevision;$('inspection').hidden=true;
    $('visualization').replaceChildren();const empty=node('div',null,'empty-view');empty.append(node('h2','Preparing your view'),node('p','Checking the source and accounting basis…'));$('visualization').append(empty);
    text('result-count','');text('receipt-basis','Awaiting verified result');$('receipt-values').replaceChildren();text('receipt-scope','');$('reconciliation').replaceChildren();
    for(const id of ['export-csv','export-svg','export-png','inspect-all','previous-page','next-page'])$(id).disabled=true;
  }
  async function run(options={}){
    const current=++revision;clearResult();showError('');status('Reading verified contribution records…',true);
    try{
      if(!dataset()?.available)throw new Error(dataset()?.message||'Select an available source.');
      const query=buildQuery(view);
      if(!query.measures.length)throw new Error('Add at least one measure to create a view.');
      if(view.type==='map'&&(query.dimensions.length!==2||!query.dimensions.includes('zip5')||!query.dimensions.includes('state')||query.measures.length!==1||query.measures[0]!=='amount'))throw new Error('The ZIP map uses Contributor ZIP, recorded state and the selected amount. Select the map preset to restore those fields.');
      const data=await ready('query',query,()=>current===revision,message=>status(message,true),options.retry===true);
      if(current!==revision)return;
      result=data;renderedRequest=query;receipt();render();
      if(facetDataset!==view.dataset)loadFacets(view.dataset,current);
    }catch(e){if(current===revision)failure(e,()=>run({retry:true}));}
  }
  async function loadFacets(source,current){
    try{
      const base={dataset:source,basis:'raw',measures:['records'],limit:100000,offset:0};
      const values=await Promise.all(['cycle','company'].map(field=>ready('query',{...base,dimensions:[field],sort:[{field,direction:'asc'}]},()=>current===revision&&source===view.dataset)));
      if(current!==revision||source!==view.dataset)return;
      ['cycle','company'].forEach((field,i)=>options(field,values[i].rows.map(r=>r[field]).filter(Boolean),view[field],field==='cycle'?'All available cycles':'All tracked companies'));
      facetDataset=source;
    }catch(e){if(current===revision)status('Chart ready. Quick filters could not load; use Add filter.');}
  }
  function render(){
    if(!result)return;showError('');
    try{
      const query=renderedRequest;
      const first=result.offset+1,last=result.offset+result.rows.length;
      text('result-count',result.group_count?`${first.toLocaleString()}–${last.toLocaleString()} of ${result.group_count.toLocaleString()} groups · select a mark for records`:'No included observations in this selection.');
      $('previous-page').disabled=view.type==='map'||!view.offset;$('next-page').disabled=view.type==='map'||!result.has_more;
      $('export-csv').disabled=exporting;$('inspect-all').disabled=false;
      if(view.type==='map'){renderMap();return;}
      svg=plot.render($('visualization'),result.rows,{type:view.type,dimensions:query.dimensions,measures:query.measures,color:view.color,labels:view.labels,columns:basisFields(result.columns,view.basis),title:$('view-title').textContent},row=>inspect(row));
      $('export-svg').disabled=!svg;$('export-png').disabled=!svg;
      status(basisLabel(view.basis,result.basis_label||view.basis)+' · '+(result.has_more?'Totals include groups on every page.':'All matching groups shown.')+(view.aggregate_filters.length?' Grouped filters apply after aggregation.':'')+(view.basis==='receipt'?' Economic uniqueness is unresolved.':''));
    }catch(e){$('visualization').replaceChildren();svg=null;$('export-svg').disabled=true;$('export-png').disabled=true;failure(e);}
  }
  async function renderMap(bbox=null){
    if(view.type!=='map')return;
    const current=revision,geoCurrent=++geoRevision,data=result;if(!data)return;
    status('Loading Census geography for the selected ZIPs…',true);
    $('export-svg').disabled=true;$('export-png').disabled=true;
    const saved=mapState?.getView();
    try{
      if(data.has_more)throw new Error('The map query has more groups than its supported page. Narrow the filters to keep every ZIP in view.');
      const zips=root.MentatContributionMap.eligibleZips(data.rows);
      const geometry=await request('geography',{zips,layers:view.layers,congress:view.congress,...(bbox?{bbox}:{})});
      if(current!==revision||geoCurrent!==geoRevision||view.type!=='map')return;
      mapGeometry=geometry;
      mapState?.destroy?.();
      mapState=root.MentatContributionMap.render($('visualization'),{groups:data.rows.map(r=>({zip5:r.zip5,state:r.state,amount_cents:r.amount,records:r.records})),geometry,layers:view.layers,title:$('view-title').textContent,valueLabel:view.basis==='original_supported'?'Selected gross amount':'Selected net amount',showLayerControls:false,onSelect:row=>inspect({zip5:row.zip5,state:row.states}),onViewport:event=>{
        if(!view.layers.includes('zip_areas'))return;clearTimeout(mapTimer);mapTimer=setTimeout(()=>renderMap(event.bbox),500);
      }});
      if(saved)mapState.setView(saved);
      svg=$('visualization').querySelector('svg');$('export-svg').disabled=!svg;$('export-png').disabled=!svg;
      const coverage=geometry.coverage;const unmatched=Array.isArray(coverage.unmatched_zips)?coverage.unmatched_zips.length:coverage.unmatched_zips||0;
      text('map-coverage',`${geometry.points.length.toLocaleString()} located ZIPs · ${unmatched.toLocaleString()} ZIPs without a location. `+(geometry.warnings||[]).join(' '));
      text('result-count',`${data.group_count.toLocaleString()} selected ZIP/state groups; ${mapState.data.marks.length.toLocaleString()} located ZIP marks. All amounts retained, including unmapped amounts.`);
      status(basisLabel(view.basis,data.basis_label||view.basis)+' · '+view.congress+'th Congress boundaries · money is unchanged by overlays.'+(view.aggregate_filters.length?' Grouped filters select ZIP/state groups before eligible groups combine into ZIP marks.':''));
    }catch(e){if(current===revision&&geoCurrent===geoRevision)failure(e);}
  }
  // F3: a conduit record's "FEC evidence" cell. The page-image link a conduit
  // row used to get never opened, so the cell reads "FEC report…" until the
  // report number arrives, then links the report, or says Unavailable.
  function drawFiling(td,link){
    if(link?.href){const a=node('a',link.text);a.href=link.href;a.target='_blank';a.rel='noopener noreferrer';a.title=link.title;td.replaceChildren(a);td.title=link.title;return;}
    if(link?.pending){td.replaceChildren(node('span','FEC report…','muted'));td.title='Reading the FEC report number. '+REPORT_TITLE+'.';return;}
    td.replaceChildren();td.textContent='Unavailable';td.title=REPORT_TITLE+', and its report number could not be read here.';
  }
  async function fillReportLinks(query,data,cells,current){
    let numbers=null;
    try{
      const reply=await ready('records',reportNumberQuery(query,data.rows),current);
      if(!current())return;
      if(reply.status==='ready'&&['workbook_sha256','database_sha256','index_token'].every(key=>reply.provenance?.[key]&&reply.provenance[key]===data.provenance?.[key]))
        numbers=new Map((reply.rows||[]).map(row=>[String(row.source_id),row.reported_file_num]));
    }catch(error){if(!current())return;}
    for(const[td,row]of cells)drawFiling(td,filingLink(row,numbers?(numbers.get(String(row.source_id))??null):null));
  }
  async function inspect(selection=null,next=false,again=false){
    if(!result)return;
    if(!next){recordSelection=selection;recordOffset=0;}
    const seq=++recordRevision,current=revision;
    $('inspection').hidden=false;$('record-table').replaceChildren(node('p','Loading source records…'));$('more-records').disabled=true;
    const filters=markFilters(renderedRequest,recordSelection);
    const title=recordSelection?renderedRequest.dimensions.map(d=>fieldName(d)+': '+(recordSelection[d]||'Missing')).join(' / '):'All selected source records';
    text('inspection-title',title);text('inspection-scope',basisLabel(view.basis,result.basis_label||view.basis)+(recordSelection?' · Selected mark':' · Before grouped filters')+' · Amounts use the same accounting basis as the chart.');
    try{
      const query={...renderedRequest,filters,aggregate_filters:[],limit:100,offset:recordOffset};
      const data=await ready('records',query,()=>seq===recordRevision&&current===revision,message=>{if(seq===recordRevision)$('record-table').replaceChildren(node('p',message));},again);
      if(seq!==recordRevision||current!==revision)return;
      if(!['workbook_sha256','database_sha256','index_token'].every(key=>data.provenance?.[key]&&data.provenance[key]===result.provenance?.[key]))throw new Error('Source changed before record inspection. Refresh the chart before inspecting its records.');
      const columns=[['date','Date'],['contributor','Contributor'],['company','Company'],['recipient','Recipient'],['amount_cents','Selected amount'],['raw_cents','Raw observation'],['zip5','ZIP'],['state','State'],['decision_status','Accounting status'],['receipt_role','Receipt role'],['reasons','Reasons'],['source_id','Source ID'],['source_url','FEC evidence']];
      const table=node('table'),thead=node('thead'),head=node('tr');columns.forEach(([,label])=>head.append(node('th',label)));thead.append(head);table.append(thead);const body=node('tbody');
      const conduitCells=[];
      for(const row of data.rows){const tr=node('tr');for(const[id]of columns){const td=node('td');const val=row[id];if(id==='source_url'&&val){try{const url=new URL(val);if(url.protocol==='https:'&&(url.hostname==='fec.gov'||url.hostname.endsWith('.fec.gov'))){const a=node('a','Open filing');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';td.append(a);}else td.textContent='Unavailable';}catch{td.textContent='Unavailable';}}else td.textContent=id.endsWith('_cents')?plot.format(val,'amount'):Array.isArray(val)?val.join('; '):val??'';if(id.endsWith('_cents'))td.className='numeric';td.title=td.textContent;if(id==='source_url'&&isConduitRecord(row)){conduitCells.push([td,row]);drawFiling(td,filingLink(row,undefined));}tr.append(td);}body.append(tr);}table.append(body);$('record-table').replaceChildren(table);
      if(conduitCells.length)fillReportLinks(query,data,conduitCells,()=>seq===recordRevision&&current===revision);
      text('record-count',`${data.total_records?recordOffset+1:0}–${recordOffset+data.rows.length} of ${data.total_records.toLocaleString()} included observations`);$('more-records').disabled=!data.has_more;
      $('inspection').scrollIntoView({behavior:'smooth',block:'nearest'});
    }catch(e){if(e?.name==='AbortError')return;if(seq===recordRevision){const note=node('p',e.message);if(e?.retryable)offerRetry(note,()=>inspect(recordSelection,true,true));$('record-table').replaceChildren(note);}}
  }
  function saved(){try{const value=JSON.parse(localStorage.getItem(storageKey)||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}catch{return {};}}
  function refreshSaved(){options('saved-view',Object.keys(saved()).map(id=>({id,label:id})), '','Open a saved view…');}
  function restoreView(raw){
    const next={...defaults(),...raw};const d=catalog.datasets.find(d=>d.id===next.dataset&&d.available);if(!d)throw new Error('The saved view’s source is unavailable.');
    const names=new Set(d.fields.map(f=>f.id));if([...next.dimensions,...next.measures,...next.filters.map(f=>f.field),...next.aggregate_filters.map(f=>f.field)].some(id=>!names.has(id)))throw new Error('A saved field is no longer available. Create a new view.');
    if(!d.bases.some(b=>b.id===next.basis))throw new Error('The saved accounting basis is unavailable.');
    return next;
  }
  function filterFields(){const grouped=$('filter-stage').value==='aggregate';options('filter-field',fields().filter(f=>grouped?f.role==='measure':f.role==='dimension'||f.id==='amount'),grouped?'amount':'recipient');$('filter-value').placeholder=grouped?'Amount in USD, or a count':'Exact value or text';}
  async function start(){
    try{
      const firstLoad=!catalog;
      const session=await request('session');token=session.token;catalog=await request('catalog');
      options('source',catalog.datasets,view.dataset);for(const opt of $('source').options){const source=catalog.datasets.find(d=>d.id===opt.value);opt.disabled=!source.available;if(!source.available)opt.textContent+=' — unavailable';}
      if(!dataset()?.available)view.dataset=catalog.datasets.find(d=>d.available)?.id||'';
      const requested=new URLSearchParams(location.search).get('industry');if(firstLoad&&catalog.datasets.some(d=>d.id===requested&&d.available))view.dataset=requested;
      if(!view.dataset)throw new Error('No verified contribution sources are available in the configured report folder.');
      sync();refreshSaved();await run();
    }catch(e){failure(e);}
  }
  $('source').onchange=()=>change(()=>{view.dataset=$('source').value;view.cycle='';view.company='';facetDataset='';options('cycle',[],'','All available cycles');options('company',[],'','All tracked companies');view.filters=[];view.aggregate_filters=[];});
  $('basis').onchange=()=>change(()=>view.basis=$('basis').value);
  for(const id of ['cycle','company','sort','limit'])$(id).onchange=()=>change(()=>view[id]=id==='limit'?Number($(id).value):$(id).value);
  $('color').onchange=()=>change(()=>view.color=$('color').value);
  $('labels').onchange=()=>change(()=>view.labels=$('labels').checked,{query:false});
  $('field-search').oninput=renderFields;
  $('refresh').onclick=()=>{facetDataset='';start();};
  $('reset').onclick=()=>change(()=>{view={...defaults(),dataset:view.dataset};});
  $('undo').onclick=()=>{if(!history.length)return;view=history.pop();$('undo').disabled=!history.length;sync();run();};
  $('previous-page').onclick=()=>{view.offset=Math.max(0,view.offset-view.limit);run();};
  $('next-page').onclick=()=>{view.offset+=view.limit;run();};
  $('chart-types').onclick=e=>{const type=e.target.closest('[data-chart]')?.dataset.chart;if(!type)return;change(()=>{view.type=type;if(type==='map'){view.dimensions=['zip5'];view.measures=['amount'];view.color='';}else if(type==='line'||type==='area'){if(!['cycle','year','date','month'].includes(view.dimensions[0]))view.dimensions=['cycle',...view.dimensions.filter(d=>d!=='cycle')];if(view.color===view.dimensions[0])view.color='';view.sort='label';}else if(type==='scatter'&&view.measures.length!==2)view.measures=['amount','contributors'];});};
  document.querySelectorAll('[data-preset]').forEach(button=>button.onclick=()=>change(()=>{const preset=button.dataset.preset;view.dimensions=[preset==='zip'?'zip5':preset];view.measures=['amount'];view.type=preset==='zip'?'map':preset==='cycle'?'line':'bar';view.color='';view.sort=preset==='cycle'?'label':'amount-desc';}));
  for(const id of ['layer-states','layer-districts','layer-zips','congress'])$(id).onchange=()=>{remember();view.congress=$('congress').value;view.layers=['states','districts','zip_areas'].filter(l=>$(l==='zip_areas'?'layer-zips':'layer-'+l).checked);renderMap(mapState?.getBounds());};
  document.querySelectorAll('.dropzone').forEach(zone=>{zone.ondragover=e=>{e.preventDefault();zone.classList.add('dragover');};zone.ondragleave=()=>zone.classList.remove('dragover');zone.ondrop=e=>{e.preventDefault();zone.classList.remove('dragover');addField(e.dataTransfer.getData('text/plain'),zone.dataset.role);};});
  $('add-filter').onclick=()=>{$('filter-editor').hidden=false;filterFields();$('filter-value').focus();};$('cancel-filter').onclick=()=>{$('filter-editor').hidden=true;};$('filter-stage').onchange=filterFields;
  $('filter-editor').onsubmit=e=>{e.preventDefault();try{const field=$('filter-field').value,op=$('filter-op').value;if(fields().find(f=>f.id===field)?.role==='measure'&&op==='contains')throw new Error('Choose Equals, At least or At most for a measure.');const value=filterValue($('filter-value').value,field,fields());const grouped=$('filter-stage').value==='aggregate';change(()=>view[grouped?'aggregate_filters':'filters'].push({field,op,value}));$('filter-editor').hidden=true;$('filter-value').value='';}catch(error){failure(error);}};
  $('inspect-all').onclick=()=>inspect();$('close-inspection').onclick=()=>{++recordRevision;$('inspection').hidden=true;};$('more-records').onclick=()=>{recordOffset+=100;inspect(recordSelection,true);};
  $('save-view').onclick=()=>{try{const name=$('view-name').value.trim();if(!name)throw new Error('Name the view before saving.');const views=saved();views[name]={view:clone(view),workbook_sha256:dataset().provenance.workbook_sha256,saved_at:new Date().toISOString()};localStorage.setItem(storageKey,JSON.stringify(views));refreshSaved();text('save-status','Saved on this device.');}catch(e){failure(e);}};
  $('saved-view').onchange=()=>{const item=saved()[$('saved-view').value];if(!item)return;try{const next=restoreView(item.view);remember();view=next;facetDataset='';sync();text('save-status',item.workbook_sha256!==dataset().provenance.workbook_sha256?'Source changed since this view was saved; using its current verified version.':'Saved view restored.');run();}catch(e){failure(e);}};
  $('export-csv').onclick=async()=>{
    if(!result||exporting)return;exporting=true;$('export-csv').disabled=true;const current=revision,query=clone(renderedRequest),original=result;status('Preparing all matching groups for CSV…',true);
    try{
      let rows=[],offset=0;for(;;){const page=await ready('query',{...query,limit:100000,offset},()=>current===revision);if(!sameExportSource(page.provenance,original.provenance))throw new Error('Source changed during export. Refresh the view first.');rows=rows.concat(page.rows);if(!page.has_more)break;if(!page.rows.length)throw new Error('Export stopped because the next page was empty.');offset+=page.rows.length;}
      root.MentatExport.download(csv({...original,rows},query),'text/csv;charset=utf-8','mentat-contributions.csv');status('Exported '+rows.length.toLocaleString()+' groups with accounting basis, source fingerprint and filters.');
    }catch(e){failure(e);}finally{exporting=false;$('export-csv').disabled=!result;}
  };
  for(const format of ['svg','png'])$('export-'+format).onclick=()=>{
    if(!svg||!result)return;
    const geoContext=view.type==='map'?['Census geography; Congress '+view.congress+'; '+view.layers.join(', ')+'; overlays do not allocate money.',
      'Negative amounts use orange; other amounts use teal.',
      'Mapped '+plot.format(mapState?.data.mapped_cents,'amount')+'; unmapped '+plot.format(mapState?.data.unmapped_cents,'amount'),
      'ZIP marks combine eligible selected ZIP/state groups; grouped filters apply before that combination.',
      ...(mapState?.data.coverage_notes||[]),...(mapGeometry?.sources||[]).map(s=>(s.vintage||s.label||s.url)+' SHA256 '+s.sha256),...(mapGeometry?.warnings||[])]:[];
    const scope=[view.dataset,basisLabel(view.basis,result.basis_label||view.basis),view.cycle||'All available cycles',view.company||'All tracked companies',`Groups ${result.offset+1}–${result.offset+result.rows.length} of ${result.group_count}`,JSON.stringify({filters:renderedRequest.filters,grouped_filters:renderedRequest.aggregate_filters}),'Source '+result.provenance.workbook+' SHA256 '+result.provenance.workbook_sha256,'Analytical index '+result.provenance.index_token,'Query engine SHA256 '+result.provenance.query_engine_sha256,calendarNotice(renderedRequest,result.rows),...geoContext].filter(Boolean).join(' · ');
    root.MentatExport.image(svg,format,$('view-title').textContent,scope,'mentat-contributions').catch(failure);
  };
  start();
})(globalThis);
