/* The graph explorer makes same-origin reads only; SVG is generated locally. */
(() => {
  'use strict';
  const el = id => document.getElementById('chart-' + id);
  const esc = escapeHtml, math = globalThis.MentatCharts;
  let data = null, suggestions = [], revision = 0, chart = null, chartConfig = null, redrawTimer, graphContext = null;
  const palette = MentatTheme.palette;
  // Paul, 2026-09-24: groups take the theme's twelve categorical colours, tinted
  // past twelve (theme.js categoryColour). A theme without them (the parity
  // corpus's stand-in) cycles the palette exactly as before.
  const categoryColour = typeof MentatTheme.categoryColour === 'function' ? MentatTheme.categoryColour : i => palette[i % palette.length];
  const fmt = n => Number(n).toLocaleString('en-US', {maximumFractionDigits:3});
  function numericAxis(low,high,integer=false) {
    if(low===high){const padding=Math.abs(low)*.1||1;low-=padding;high+=padding;}
    const rough=(high-low)/5,magnitude=10**Math.floor(Math.log10(rough)),fraction=rough/magnitude;
    let step=(fraction<=1+1e-12?1:fraction<=2+1e-12?2:fraction<=5+1e-12?5:10)*magnitude;
    if(integer)step=Math.max(1,step);
    if(!Number.isFinite(step)||step<=0)throw new Error('The numeric range is too large or too small to display.');
    const units=value=>{const scaled=value/step,nearest=Math.round(scaled);return Math.abs(scaled-nearest)<=Number.EPSILON*Math.max(1,Math.abs(scaled))*4?nearest:scaled;};
    const start=Math.floor(units(low))*step,end=Math.ceil(units(high))*step;
    const decimals=Math.max(0,Math.min(100,-Math.floor(Math.log10(step))));
    const ticks=Array.from({length:Math.round((end-start)/step)+1},(_,i)=>Number((start+i*step).toFixed(decimals)));
    return {min:Math.min(low,ticks[0]),max:Math.max(high,ticks.at(-1)),ticks};
  }
  const aggregateNames={sum:'Sum',mean:'Mean',median:'Median',min:'Minimum',max:'Maximum',count:'Row count',countd:'Distinct count'};
  function columnLabel(index, source=data) {
    const column=source?.columns[index];
    if(!column)return 'All rows';
    return column.unit && !column.name.toLowerCase().includes(String(column.unit).toLowerCase()) ? `${column.name} (${column.unit})` : column.name;
  }
  function measureLabel(config=chartConfig,result=chart,source=data) {
    // r3 (W2): several measures read as one title; a scatter's value axis is the second.
    const listed=Array.isArray(config.measures)?config.measures.filter(m=>m&&Number.isInteger(Number(m.column))):[];
    if(listed.length>1&&result?.type==='scatter')return measureLabel({...config,measures:null,y:Number(listed[1].column),aggregation:listed[1].aggregation},result,source);
    if(listed.length>1)return listed.map(m=>measureLabel({...config,measures:null,y:Number(m.column),aggregation:m.aggregation},result,source)).join(' / ');
    const individual=['box','scatter','histogram'].includes(result?.type);
    const base=!individual&&config.aggregation==='count'?(config.y<0?'Row count':'Count of '+columnLabel(config.y,source)):!individual&&config.aggregation==='countd'?'Distinct count of '+columnLabel(config.y,source):columnLabel(config.y,source);
    const measured=individual||config.aggregation==='count'||config.aggregation==='countd'?base:`${base} · ${aggregateNames[config.aggregation]}`;
    return config.transform==='yoy'?`${base} · year-over-year change (%)`:config.transform==='change'?`${measured} · period change`:result?.split?`${measured} · coverage`:measured;
  }
  function error(message='') { el('error').textContent = message; el('error').hidden = !message; }
  function clearChart() { chart = null; el('image').replaceChildren(); el('values').replaceChildren(); el('export').hidden = true; el('scope').textContent = ''; }
  function reset() { clearTimeout(redrawTimer); data = null; clearChart(); error(); el('settings').disabled = true; el('columns').replaceChildren(); el('context').textContent = 'Loading your chart…'; }
  function options(node, items) { node.replaceChildren(...items.map(([value,label]) => new Option(label, value))); }
  function reportControls(enabled) {
    for(const id of ['run','report','sheet','header','rescan']) el(id).disabled=!enabled;
  }
  function measureOptions() {
    const measures=data.columns.filter(c=>c.type==='number'&&!/\b(year|cycle)\b/i.test(c.name));
    options(el('y'),[...measures.map(c=>[c.index,c.name]),[-1,'Row count']]);
    options(el('series'),[[-1,'None'],...data.columns.map(c=>[c.index,c.name])]);
  }
  function requestPath(sheet='', header=0) {
    const params = new URLSearchParams({id:el('run').value, index:el('report').value});
    if (sheet) params.set('sheet', sheet);
    if (header) params.set('header', header);
    return '/api/report-data?' + params;
  }
  async function loadRuns() {
    const ticket=++revision;reset();reportControls(true);el('loading').textContent='Choosing a completed report…';
    try {
      const body = await api('/api/report-sources');
      if(ticket!==revision)return;
      options(el('run'), body.runs.map(r => [r.id, r.name]));
      const preferred = body.runs.find(r => r.id === (document.getElementById('graph-run').value || selectedRun) && !['ready','needs review'].includes(r.state))
        || body.runs.find(r => r.state === 'completed');
      const candidates=[preferred,...body.runs.filter(r=>r.state==='completed')].filter((r,i,a)=>r&&a.findIndex(v=>v?.id===r.id)===i);
      let catalog=null;
      for(const candidate of candidates){
        const available=await api('/api/reports?id='+encodeURIComponent(candidate.id));
        if(ticket!==revision)return;
        if(available.reports.length){el('run').value=candidate.id;catalog=available;break;}
      }
      await loadReports(catalog);
    } catch (e) { if(ticket===revision)error(e.message); }
  }
  async function loadReports(preloaded=null) {
    const ticket = ++revision; reset(); options(el('report'), []); options(el('sheet'), []);
    el('loading').textContent = 'Reading reports…';
    try {
      if (!el('run').value) { el('loading').textContent = 'No collection runs available.'; return; }
      const run = preloaded?.reports ? preloaded : await api('/api/reports?id=' + encodeURIComponent(el('run').value));
      if (ticket !== revision) return;
      options(el('report'), run.reports.map(r => [r.index, r.name]));
      const scopedReport = graphContext?.reports?.map(source => run.reports.find(report => report.name === source.name)).find(Boolean);
      if (scopedReport) el('report').value = scopedReport.index;
      if (!run.reports.length) { el('loading').textContent = 'This run has no completed reports.'; return; }
      await loadSheets();
    } catch (e) { if (ticket === revision) { error(e.message); el('loading').textContent = 'Report list unavailable.'; } }
  }
  async function loadSheets() {
    const ticket = ++revision; reset(); options(el('sheet'), []); el('loading').textContent = 'Reading worksheets…';
    try {
      const body = await api(requestPath());
      if (ticket !== revision) return;
      options(el('sheet'), body.sheets.map(s => [s,s]));
      const preferred = ['Candidate Funding','Funding Cohorts','Allocation Estimates','Beneficiary Summary'].find(s => body.sheets.includes(s));
      if (preferred) el('sheet').value = preferred;
      await scan();
    } catch (e) { if (ticket === revision) { error(e.message); el('loading').textContent = 'Worksheets unavailable.'; } }
  }
  async function scan(manual=false) {
    const ticket = ++revision; reset(); el('loading').textContent = 'Scanning columns…';
    try {
      if (manual && !el('header').reportValidity()) throw new Error('Enter a header row from 1 to 100.');
      const body = await api(requestPath(el('sheet').value, manual ? Number(el('header').value) : 0));
      if (ticket !== revision) return;
      data = body; el('header').value = data.header_row; el('settings').disabled = false;
      el('filters').replaceChildren(); el('start').value = ''; el('end').value = ''; el('limit').value = '';
      el('loading').textContent = `${fmt(data.row_count)} rows · ${data.columns.length} columns scanned`;
      el('context').textContent = data.note;
      options(el('x'), [[-1,'All rows'], ...data.columns.map(c => [c.index, `${c.name} · ${c.type}`])]);
      measureOptions();
      suggestions = math.recommend(data.columns);
      options(el('suggestion'), suggestions.length ? suggestions.map((s,i) => [i,s.label]) : [['','Choose columns below']]);
      el('columns').innerHTML = data.columns.map(c => `<tr><td>${esc(c.name)}</td><td>${esc(c.type)}</td><td>${fmt(c.distinct)}</td><td>${fmt(c.missing)}</td></tr>`).join('');
      prepareSubjects();
      if (suggestions.length) applySuggestion();
      quick(suggestions[0]?.type || 'bar');
    } catch (e) { if (ticket === revision) { error(e.message); el('loading').textContent = 'Column scan unavailable.'; } }
  }
  function applySuggestion() {
    const s = suggestions[Number(el('suggestion').value)]; if (!s) return;
    el('type').value = s.type; el('x').value = s.x; el('y').value = s.y;
    el('aggregation').value = s.aggregation; el('transform').value='none'; syncControls();
  }
  function prepareSubjects() {
    const subject = data.columns.find(c => c.name === 'Subject');
    options(el('subject'), subject ? [...new Set(data.rows.map(r => r[subject.index]))].map(s=>[s,s]) : []);
  }
  function quick(choice) {
    if (!data) return;
    try {
      const next = math.quickConfig(data, configuration(), choice);
      for (const key of ['type','x','y','series','aggregation','transform','period','horizon']) if(next[key]!=null) el(key).value=next[key];
      syncControls(); create();
    } catch(e) { clearChart();syncControls();error(e.message); }
  }
  function syncControls() {
    const type = el('type').value;
    el('aggregation').disabled = ['box','scatter','histogram'].includes(type);
    el('x').disabled = type === 'histogram';
    el('y').disabled = false;
    el('series').disabled = !['grouped','groupedhorizontal','multiline'].includes(type);
    el('period').disabled=el('transform').value==='none';
    el('horizon').disabled=el('transform').value!=='forecast';
    el('x-label').textContent = type === 'scatter' ? 'Horizontal axis (X)' : el('transform').value !== 'none' ? 'Date or election cycle' : 'Group by';
    el('subject-label').hidden = el('transform').value === 'none' || !el('subject').options.length;
    for (const button of document.querySelectorAll('#chart-quick button, #chart-time-quick button')) {
      const choice = button.dataset.chart || button.dataset.transform;
      button.setAttribute('aria-pressed', String(button.dataset.transform ? el('transform').value===choice : el('transform').value==='none' && type===choice));
      try { if(data) math.quickConfig(data,{x:Number(el('x').value),y:Number(el('y').value),series:Number(el('series').value),aggregation:el('aggregation').value},choice); button.disabled=!data; button.title=''; }
      catch(e) {button.disabled=true;button.title=e.message;}
    }
    const timeNotes={annual:'Over time groups observations into calendar years or two-year election cycles using the selected aggregation. Partial periods remain partial.',yoy:'Year over year shows percentage change from the previous calendar year. Missing years and nonpositive prior totals cannot supply a percentage.',change:'Period change subtracts the immediately preceding year or election cycle; missing periods remain unavailable.',forecast:'Same-phase baseline compares corresponding election phases in completed periods. It needs sufficient comparable history; projected values are not observed giving.'};
    const chartNotes={scatter:'Scatter compares two numeric fields. Every valid selected row is plotted; dense plots combine SVG marks for speed without sampling.',histogram:'Histogram counts rows in numeric ranges of the selected measure.',box:'Box plot shows the median, middle half, whiskers and outliers of individual values in each group.',pie:'Pie shows each nonnegative group’s share of the total.',doughnut:'Doughnut shows each nonnegative group’s share of the total.',pareto:'Pareto orders nonnegative groups by value and adds cumulative percentage.',waterfall:'Waterfall shows each group’s change and the running total.',radar:'Radar compares at least three nonnegative groups on a shared scale.'};
    el('quick-note').textContent=timeNotes[el('transform').value]||chartNotes[type]||'Choose a chart type to select compatible fields automatically. Change the measure, grouping or aggregation to refine it.';
  }
  function addFilter() {
    if (!data) return;
    const row = document.createElement('div'); row.className = 'chart-filter';
    const column = document.createElement('select'); column.setAttribute('aria-label','Filter column');
    options(column, data.columns.map(c => [c.index,c.name]));
    const op = document.createElement('select'); op.setAttribute('aria-label','Filter operation');
    options(op, [['equals','Equals'],['contains','Contains'],['gte','At least'],['lte','At most']]);
    const value = document.createElement('input'); value.setAttribute('aria-label','Filter value'); value.placeholder = 'Value';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = 'Remove filter';
    remove.onclick = () => { row.remove(); create(); };
    row.append(column,op,value,remove); el('filters').append(row);
  }
  function configuration() {
    if (!el('start').reportValidity() || !el('end').reportValidity() || !el('limit').reportValidity()) throw new Error('Enter valid worksheet row numbers.');
    const subject=data?.columns.find(c=>c.name==='Subject');
    const filters=[...el('filters').children].filter(row=>row.children[2].value!=='').map(row=>({column:Number(row.children[0].value),op:row.children[1].value,value:row.children[2].value}));
    if(subject && el('transform').value!=='none')filters.push({column:subject.index,op:'equals',value:el('subject').value});
    return {type:el('type').value, x:Number(el('x').value), y:Number(el('y').value), aggregation:el('aggregation').value,
      transform:el('transform').value,period:el('period').value,horizon:Number(el('horizon').value),start:Number(el('start').value),end:Number(el('end').value),limit:Number(el('limit').value),series:Number(el('series').value),filters};
  }
  function create() {
    clearTimeout(redrawTimer); error(); clearChart(); if (!data) return;
    try {
      chartConfig = configuration(); chart = math.build(data, chartConfig);
      const measure = measureLabel();
      el('title').textContent = `${measure} · ${chartConfig.transform==='none' ? el('type').selectedOptions[0].textContent : el('transform').selectedOptions[0].textContent}`;
      el('context').textContent = `${el('report').selectedOptions[0].textContent} / ${data.sheet}`;
      const filterText = chartConfig.filters.map(f => `${data.columns[f.column].name} ${f.op} ${f.value}`).join('; ');
      const group = chart.type === 'histogram' ? 'Numeric bins' : data.columns[chartConfig.x]?.name || 'All rows';
      el('scope').textContent = `${el('report').selectedOptions[0].textContent} / ${data.sheet} · ${chart.selected} of ${data.row_count} rows selected; ${chart.excluded} rows excluded for missing/non-numeric values. Group/X: ${group}. ${['box','scatter','histogram'].includes(chart.type) ? 'Individual worksheet values.' : 'Aggregation: ' + chartConfig.aggregation + '.'} Worksheet rows: ${chartConfig.start || 'first data row'}–${chartConfig.end || 'last data row'}. Row count limit: ${chartConfig.limit || 'all matching rows'} (after filters). Filters: ${filterText || 'none'}. SHA-256: ${data.sha256}.` +
        (chart.type === 'box' ? ' Quartiles use linear interpolation; whiskers end at observations within 1.5×IQR; dots show outliers.' : '');
      if(chart.note)el('scope').textContent+=' '+chart.note; if(['Comparison','Funding comparison'].includes(data.sheet))el('scope').textContent+=' '+data.note;
      draw(chart, measure); renderValues(); el('export').hidden = false;
    } catch (e) { clearChart(); error(e.message); }
  }
  function svgNode(tag, attrs={}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg',tag);
    for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,String(v));
    if (text != null) node.textContent = text; return node;
  }
  // r3 (W6): these marks are drawn by contribution-charts.js from the worksheet
  // groups, so the worksheet and verified views share one drawing of each.
  const DELEGATED=['heatmap','highlight','treemap','bubbles','bullet','gantt'];
  // A JSON tuple label as the reader sees it ("A / NY"); plain labels unchanged.
  const readable=label=>{if(typeof label==='string'&&label.startsWith('[')){try{const parts=JSON.parse(label);if(Array.isArray(parts))return parts.map(p=>p??'(Missing)').join(' / ');}catch(e){}}return label;};
  const tupleOf=(g,n)=>{if(n<=1)return [g.label];if(g.other)return [g.label,...Array(n-1).fill('Other')];try{const parts=JSON.parse(g.label);if(Array.isArray(parts))return parts.map(p=>p??'(Missing)');}catch(e){}return [g.label,...Array(n-1).fill('')];};
  function delegatedSVG(result, measure, config, source, titleText, scopeText, options) {
    const charts=globalThis.MentatContributionCharts;
    if(!charts||typeof charts.render!=='function')throw new Error(result.type+' needs contribution-charts.js');
    const columns=Array.isArray(result.groupColumns)?result.groupColumns:(Array.isArray(config.dimensions)&&config.dimensions.length?config.dimensions:config.x>=0?[config.x]:[]);
    const used=new Set(),idOf=name=>{let id=String(name),k=2;while(used.has(id))id=name+' ('+(k++)+')';used.add(id);return id;};
    let dimIds=columns.map(c=>idOf(columnLabel(c,source)));if(!dimIds.length)dimIds=[idOf('Group')];
    const valueId=idOf(measure||'Value'),unitOf=c=>source?.columns[c]?.unit||'number';
    let rows=result.groups.map(g=>{const parts=tupleOf(g,dimIds.length),row={};dimIds.forEach((id,k)=>{row[id]=parts[k]??'(Missing)';});row[valueId]=g.value;return Object.assign(row,g.extra||{});});
    let dims=dimIds,measures=[valueId],cols=[...dimIds.map(id=>({id,name:id})),{id:valueId,name:measure||'Value',unit:'number',aggregation:config.aggregation}];
    const fallback=(why)=>{const bars={...result,type:'horizontal',series:undefined,groups:result.groups.map(g=>Array.isArray(g.values)?{...g,value:g.values[0]?.value??0}:g)};
      const drawn=chartSVG(bars,measure,config,source,titleText,scopeText,options);drawn.svg.append(svgNode('text',{x:20,y:Number(drawn.svg.getAttribute('viewBox').split(' ')[3])-8,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':12,'data-note':'fallback'},why));return drawn;};
    if(['heatmap','highlight'].includes(result.type)){
      // Exactly two axes for the grid: one field gives one column; three or
      // more key the rows by the tuple of the rest (V07).
      if(dims.length===1){const col=idOf(measure||'Value ');rows=rows.map(r=>({...r,[col]:measure}));dims=[dims[0],col];cols.push({id:col,name:' '});}
      else if(dims.length>2){const key=idOf(dimIds.slice(1).join(' / '));rows=rows.map(r=>({...r,[key]:dimIds.slice(1).map(id=>r[id]).join(' / ')}));dims=[key,dimIds[0]];cols.push({id:key,name:key});}
    }else if(result.type==='bullet'&&Array.isArray(result.series)&&result.series.length>=2&&result.groups.every(g=>Array.isArray(g.values))){
      // B-E33/V07 (Paul: "nothing is logically constrained"): contribution-charts draws every
      // measure. A second measure in the first one's unit is the marker; another unit is named in
      // its note; a one-measure bullet (the default rows above) draws its bars as a bullet.
      const ids=result.series.map(s=>idOf(s)),cfg=result.seriesConfig||[];
      rows=result.groups.map(g=>{const parts=tupleOf(g,dimIds.length),row={};dimIds.forEach((id,k)=>{row[id]=parts[k]??'(Missing)';});ids.forEach((id,k)=>{row[id]=g.values[k]?.value??null;});return row;});
      measures=ids;cols=[...dimIds.map(id=>({id,name:id})),...ids.map((id,k)=>({id,name:result.series[k],unit:unitOf(cfg[k]?.column),aggregation:cfg[k]?.aggregation}))];
    }else if(result.type==='gantt'){
      rows=result.groups.map(g=>{const parts=tupleOf(g,dimIds.length),row={};dimIds.forEach((id,k)=>{row[id]=parts[k]??'(Missing)';});return Object.assign(row,{first_date:g.first??null,last_date:g.last??null,span_days:g.span??null});});
      measures=['span_days'];cols=[...dimIds.map(id=>({id,name:id})),{id:'span_days',name:'Span (days)',unit:'days'}];
    }
    // Marks v3 (lane M): the worksheet's Size, Label, Colour measure and Tooltip
    // fields reach the delegated drawing; each group carries them (W7).
    const groupsOf=result.groups||[],carried=(g,key)=>g&&(g[key]!=null||Array.isArray(g.values)&&g.values.some(v=>v&&v[key]!=null));
    const enc={size:result.sizeLabel&&groupsOf.some(g=>carried(g,'size'))?idOf(result.sizeLabel):'',label:result.labelLabel&&groupsOf.some(g=>carried(g,'labelValue'))?idOf(result.labelLabel):'',
      colour:result.colorLabel&&groupsOf.some(g=>carried(g,'colorValue'))?idOf(result.colorLabel):''};
    const tips=(result.extraLabels||[]).filter(label=>groupsOf.some(g=>g&&(g.extra&&g.extra[label]!==undefined||Array.isArray(g.values)&&g.values.some(v=>v&&v.extra&&v.extra[label]!==undefined))));
    if(enc.size||enc.label||enc.colour||tips.length){
      const pick=(g,key)=>g[key]!=null?g[key]:Array.isArray(g.values)?(g.values.find(v=>v&&v[key]!=null)||{})[key]:undefined;
      rows=rows.map((row,i)=>{const g=groupsOf[i]||{},out=Object.assign({},row,g.extra||(Array.isArray(g.values)?(g.values.find(v=>v&&v.extra)||{}).extra:null)||{});
        if(enc.size&&pick(g,'size')!=null)out[enc.size]=pick(g,'size');if(enc.label&&pick(g,'labelValue')!=null)out[enc.label]=pick(g,'labelValue');if(enc.colour&&pick(g,'colorValue')!=null)out[enc.colour]=pick(g,'colorValue');return out;});
      for(const id of [enc.size,enc.label,enc.colour,...tips])if(id&&!cols.some(c=>c.id===id))cols.push({id,name:id,unit:'number'});
    }
    const container=document.createElement('div');
    const svg=charts.render(container,rows,{type:result.type,dimensions:dims,measures,columns:cols,labels:!!(options&&options.labels),title:titleText,
      ...(enc.size?{size:enc.size}:{}),...(enc.label?{labelFields:[enc.label]}:{}),...(enc.colour?{colorMeasure:enc.colour}:{}),...(tips.length?{tooltip:tips}:{})},()=>{})||null;
    if(svg){svg.setAttribute('aria-label',titleText);const desc=svgNode('desc',{},scopeText);svg.insertBefore(desc,svg.firstChild);return {svg,plot:null};}
    const empty=svgNode('svg',{xmlns:'http://www.w3.org/2000/svg',viewBox:'0 0 960 200',role:'img','aria-label':titleText});
    empty.append(svgNode('title',{},titleText),svgNode('desc',{},scopeText),svgNode('rect',{width:960,height:200,fill:'#fff'}),svgNode('text',{x:20,y:100,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':14},container.textContent||'Nothing to draw for this selection.'));
    return {svg:empty,plot:null};
  }
  /* Pie and doughnut (Paul, 2026-09-24).
     - On screen (options.fit = {width, height} of the chart box in CSS px;
       height null where the box grows with its chart) a pie is drawn at the
       box's shape: the whole pie, no title, subtitle or caption of its own (the
       Studio's header and figures above say them), and a legend centred beside
       it that tightens its rows, takes a second column and then ends "+N more".
       Every text is sized in CSS px, so none is under 11px on screen.
     - Anything that leaves the page draws today's picture instead:
       svg.__mentatExport re-draws it without options.fit (chart-export.js).
     - Names live in the legend only. With Labels on, a slice of 4% or more
       carries its percentage inside it, in white or plum, whichever reads
       better on that slice; nothing is drawn outside the pie. */
  const PIE = {labelShare: .04, legendPx: 13, notePx: 12, labelPx: 12, labelMinPx: 11, marginPx: 12, gapPx: 22,
    safetyPx: 4, legendMinPx: 170, columnMinPx: 120, minBoxPx: 240, hMin: 360, hMax: 670, hFree: 540};
  const PLUM = '#342649';
  // The drawing, in viewBox units, that fills a chart box of `box` CSS px: 960
  // wide, as tall as the box's shape (within 360-670), and never taller than
  // the box less a few px, so the canvas never needs a scrollbar for it.
  function pieFrame(box) {
    const width = box ? Number(box.width) : NaN, height = box && box.height != null ? Number(box.height) : null;
    if (!(width >= PIE.minBoxPx)) return null;
    const bounded = height != null && height > 0;
    let hPx = bounded ? Math.max(1, Math.min(height - PIE.safetyPx, width * PIE.hMax / 960)) : width * PIE.hFree / 960;
    const H = Math.round(100 * (bounded ? Math.max(PIE.hMin, Math.min(PIE.hMax, 960 * hPx / width)) : PIE.hFree)) / 100;
    const wPx = hPx * 960 / H;
    return {H, hPx, wPx, s: wPx / 960, bounded, box: {width, height: bounded ? height : null}};
  }
  // Text widths from the theme's measured canvas when it has one; otherwise the
  // same per-character estimate theme.js falls back to, so node tests agree.
  function textWidth(text, size) {
    if (typeof MentatTheme.measureText === 'function') return MentatTheme.measureText(text, size, MentatTheme.sans);
    let em = 0;
    for (const c of String(text)) em += c === ' ' ? .278 : c >= '0' && c <= '9' ? .556 : c >= 'A' && c <= 'Z' ? .68 : c >= 'a' && c <= 'z' ? .5 : .3;
    return em * size;
  }
  function fitLabel(text, max, size, suffix) {
    if (typeof MentatTheme.fitText === 'function') return MentatTheme.fitText(text, max, size, MentatTheme.sans, suffix);
    const value = String(text), tail = suffix || '';
    if (textWidth(value + tail, size) <= max) return value + tail;
    const chars = Array.from(value);
    let n = chars.length;
    while (n > 0 && textWidth(chars.slice(0, n).join('') + '…' + tail, size) > max) n--;
    return (n ? chars.slice(0, n).join('').replace(/[\s,·]+$/, '') : '') + '…' + tail;
  }
  // White or plum, whichever contrasts more with the slice as it is drawn
  // (a colour-measure fill is laid over white at its opacity).
  function rgbOf(colour) {
    const text = String(colour || '').trim();
    let m = /^#([0-9a-f]{3})$/i.exec(text);
    if (m) return [...m[1]].map(c => parseInt(c + c, 16));
    m = /^#([0-9a-f]{6})$/i.exec(text);
    if (m) return [0, 2, 4].map(at => parseInt(m[1].slice(at, at + 2), 16));
    m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(text);
    return m ? [m[1], m[2], m[3]].map(Number) : null;
  }
  const luminance = rgb => { const [r, g, b] = rgb.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
  const contrastOf = (a, b) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + .05) / (lo + .05); };
  function inkOn(fill) {
    const rgb = rgbOf(fill && fill.fill), opacity = fill && fill['fill-opacity'] != null ? Number(fill['fill-opacity']) : 1;
    if (!rgb) return '#ffffff';
    const seen = rgb.map(v => v * opacity + 255 * (1 - opacity)), plum = rgbOf(PLUM);
    return contrastOf(seen, [255, 255, 255]) >= contrastOf(seen, plum) ? '#ffffff' : PLUM;
  }
  // Where each slice's percentage goes. Largest slices first, each at its
  // slice's centroid (a doughnut: mid-ring), stepping along its radius if it
  // must. A label stays inside the pie (outside a doughnut's hole) and clear of
  // every other label. The first plan that places every label wins: inside its
  // own slice with one decimal, then as a whole percent, then at the 11px
  // floor, then anywhere inside the pie; a label that fits nowhere is left to
  // the legend, which lists every share.
  function pieLabelPlan(slices, geo, font, minFont) {
    const eligible = slices.filter(s => s.share >= PIE.labelShare).sort((a, b) => b.share - a.share || a.index - b.index);
    for (const [size, digits, strict] of [[font, 1, true], [font, 0, true], [minFont, 0, true], [minFont, 0, false]]) {
      const placed = placePieLabels(eligible, geo, size, digits, strict, false);
      if (placed.length === eligible.length) return placed;
    }
    return placePieLabels(eligible, geo, minFont, 0, false, true);
  }
  function placePieLabels(eligible, geo, size, digits, strict, greedy) {
    const placed = [], pad = size * .15, {cx, cy, r, inner} = geo;
    const inRing = box => [[box.x0, box.y0], [box.x1, box.y0], [box.x0, box.y1], [box.x1, box.y1]].every(([x, y]) => Math.hypot(x - cx, y - cy) <= r - pad)
      && (!inner || Math.hypot(Math.min(Math.max(cx, box.x0), box.x1) - cx, Math.min(Math.max(cy, box.y0), box.y1) - cy) >= inner + pad);
    const inWedge = (box, s) => [[box.x0, box.y0], [box.x1, box.y0], [box.x0, box.y1], [box.x1, box.y1]].every(([x, y]) => {
      let a = Math.atan2(y - cy, x - cx);
      while (a < s.a0) a += 2 * Math.PI;
      while (a >= s.a0 + 2 * Math.PI) a -= 2 * Math.PI;
      return a <= s.a1;
    });
    for (const s of eligible) {
      const text = (100 * s.share).toFixed(digits) + '%', w = textWidth(text, size) * 1.08 + 2 * pad, h = size * 1.2 + 2 * pad;
      const span = s.a1 - s.a0, whole = span >= 2 * Math.PI - 1e-9, angle = whole && inner ? -Math.PI / 2 : s.a0 + span / 2;
      // A slice over half the pie has its centroid near the hub, where every
      // other slice's edge meets; its label goes out along the same middle ray
      // to 40% of the radius, still well inside it.
      const centroid = 2 * r * Math.sin(span / 2) / (3 * span / 2);
      const base = inner ? (r + inner) / 2 : whole ? 0 : span > Math.PI ? Math.max(centroid, .4 * r) : centroid, step = r * .06;
      let spot = null;
      for (const rho of [base, base + step, base - step, base + 2 * step, base - 2 * step, base + 3 * step, base - 3 * step, base + 4 * step]) {
        if (rho < 0) continue;
        const x = cx + rho * Math.cos(angle), y = cy + rho * Math.sin(angle), box = {x0: x - w / 2, y0: y - h / 2, x1: x + w / 2, y1: y + h / 2};
        if (!inRing(box) || (strict && !whole && !inWedge(box, s))) continue;
        if (placed.some(p => box.x0 < p.box.x1 && p.box.x0 < box.x1 && box.y0 < p.box.y1 && p.box.y0 < box.y1)) continue;
        spot = {x, y, box};
        break;
      }
      if (!spot) { if (greedy) continue; return placed; }
      placed.push({slice: s, text, size, x: spot.x, y: spot.y, box: spot.box});
    }
    return placed;
  }
  // Shared renderer. The classic dialog and Chart Studio both draw through this
  // one function; `plot` reports the value-axis geometry (pixel positions of the
  // axis minimum and maximum) so a caller can overlay reference lines exactly.
  // r3 (W8): an optional seventh argument, options = {labels, axisLabels, legend}.
  function chartSVG(result, measure, config, source, titleText, scopeText, options) {
    let plot = null;
    const opts=options||{};
    const grouping=result.groupingLabel||columnLabel(config.x,source),xTitle=result.type==='histogram'?columnLabel(config.y,source):grouping;
    if(DELEGATED.includes(result.type))return delegatedSVG(result,measure,config,source,titleText,scopeText,options);
    // Marks v3 (lane M): only a configuration that carries them takes this path.
    if(config&&(V3_CHART.includes(config.mark)||(config.v3&&config.v3.path)))return v3SVG(result,measure,config,source,titleText,scopeText,options);
    result=liftEncodings(result);
    // With the r3 options argument a JSON tuple label reads as "A / NY"; the
    // six-argument call keeps today's labels.
    if(options&&Array.isArray(result.groups)&&result.groups.some(g=>typeof g.label==='string'&&g.label.startsWith('[')))result={...result,groups:result.groups.map(g=>({...g,label:readable(g.label)}))};
    if(result.series&&Array.isArray(result.groups)&&result.groups.every(g=>Array.isArray(g.values))){
      const svg=MentatGroupedSVG(result,titleText,scopeText,options),bounds=svg.getAttribute('viewBox').split(' ').map(Number);
      // Wide comparisons scroll inside the preview instead of shrinking labels.
      svg.style.minWidth=`${bounds[2]}px`;
      svg.setAttribute('viewBox',`0 0 ${bounds[2]} ${bounds[3]+55}`);
      svg.querySelector('rect').setAttribute('height',bounds[3]+55);
      const seriesTitle=result.measureSeries?`${grouping} · Measures`:result.type==='histogram'?`${xTitle} · Colour: ${columnLabel(config.series,source)}`:result.temporal?`${grouping} · Series: ${result.series.length}`:`${grouping} · Separate series: ${columnLabel(config.series,source)}`;
      for(const [axis,text,y] of [['y',measure,65],['x',seriesTitle,bounds[3]+24]])svg.append(svgNode('text',{x:150,y,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':13,'data-axis-title':axis},text));
      if(result.dual)svg.append(svgNode('text',{x:bounds[2]-20,y:65,'text-anchor':'end',fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':13,'data-axis-title':'right'},result.dual.right.map(i=>result.series[i]).join(', ')+' (right axis)'));
      return {svg,plot:svg.__plot||null};
    }
    // Pie legends list 16 members unless options.legend asks for more (W8).
    const legendCap=opts.legend==='all'?Infinity:Number.isInteger(opts.legend)&&opts.legend>0?opts.legend:16;
    const pieLegend=['pie','doughnut'].includes(result.type)?Math.min(result.groups.length,legendCap):0;
    // On screen a pie takes the shape of its chart box (options.fit, above).
    const fit=['pie','doughnut'].includes(result.type)?pieFrame(opts.fit):null;
    const canvasHeight=fit?fit.H:result.type==='horizontal'?Math.max(620,result.groups.length*36+190):['pie','doughnut'].includes(result.type)?(pieLegend>16?90+pieLegend*32+80:670):620;
    const svg = svgNode('svg',{xmlns:'http://www.w3.org/2000/svg',viewBox:`0 0 960 ${canvasHeight}`,role:'img','aria-label':titleText,...(fit?{'data-fit':'pie'}:{})});
    const add = (tag,attrs,text) => { const n = svgNode(tag,attrs,text); svg.append(n); return n; };
    add('title',{},titleText); add('desc',{},scopeText);
    add('rect',{width:960,height:canvasHeight,fill:'#fff'});
    const label = (x,y,text,attrs={}) => add('text',{x,y,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':12,...attrs},text);
    const tooltip = (node,text) => node.append(svgNode('title',{},text));
    const wrapped=(x,y,text,maxChars=32,maxLines=2,attrs={})=>{
      const source=String(text),words=source.split(/\s+/),lines=[];let line='';
      for(const word of words){if((line+' '+word).trim().length>maxChars&&line){lines.push(line);line='';}line+=(line?' ':'')+word;}
      if(line)lines.push(line);
      const shown=lines.slice(0,maxLines).map(part=>part.length>maxChars?part.slice(0,maxChars-1)+'…':part);
      if(lines.length>maxLines)shown[maxLines-1]=shown[maxLines-1].slice(0,maxChars-1)+'…';
      const node=label(x,y,'',attrs);shown.forEach((part,i)=>node.append(svgNode('tspan',{x,dy:i?'1.25em':0},part)));tooltip(node,source);return node;
    };
    const tick=n=>n!==0&&Math.abs(n)<.001?Number(n.toPrecision(3)).toExponential():Math.abs(n)>=1e6?Number(n).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2}):Number(n).toLocaleString('en-US',{maximumSignificantDigits:8});
    // A pie fitted to the screen draws no title or subtitle: the page's header
    // and figures above the chart already say them.
    if(!fit){
    wrapped(20,28,titleText,83,2,{'font-size':19,'font-weight':400,'font-family':MentatTheme.serif});
    label(20,70,`${source.sheet} · ${fmt(result.selected-result.excluded)} ${result.points?'plotted':'source'} rows${result.points?.length>2000?' · all points, no sampling':''}`);
    }
    // r3 encodings (W7, W11): tooltip lines, a sequential fill for a Colour
    // measure, a per-member colour for Colour joined to the grouping, and value
    // labels. A result without those fields draws exactly as before.
    const more=g=>(result.sizeLabel&&g.size!=null?`\n${result.sizeLabel}: ${fmt(g.size)}`:'')+
      (g.extra?Object.entries(g.extra).map(([k,v])=>`\n${k}: ${v==null?'—':typeof v==='number'?fmt(v):v}`).join(''):'')+
      (result.colorLabel&&g.colorValue!=null?`\n${result.colorLabel}: ${fmt(g.colorValue)}`:'')+
      (result.labelLabel&&g.labelValue!=null?`\n${result.labelLabel}: ${typeof g.labelValue==='number'?fmt(g.labelValue):g.labelValue}`:'');
    const range=Array.isArray(result.colorRange)?result.colorRange:null;
    const paint=(g,color)=>range&&typeof g.colorValue==='number'?{fill:palette[0],'fill-opacity':.25+.75*(range[1]>range[0]?(g.colorValue-range[0])/(range[1]-range[0]):1)}:{fill:color};
    const colourOf=(g,i)=>categoryColour(Number.isInteger(g.seriesIndex)?g.seriesIndex:i);
    const labelText=(g,value)=>g.labelValue!=null?(typeof g.labelValue==='number'?fmt(g.labelValue):readable(String(g.labelValue))):fmt(value);
    const markText=(x,y,text,attrs={})=>{if(opts.labels)label(x,y,text,{'font-size':11,'text-anchor':'middle','data-mark-label':'true',...attrs});};
    const clip=(text,n=28)=>String(text).length>n?String(text).slice(0,n-1)+'…':String(text);
    // Size (W7): |size| / max sets a bar's thickness, a line's width, a dot's radius.
    const sized=(result.groups||result.points||[]).map(g=>Math.abs(Number(g.size))).filter(Number.isFinite),maxSize=sized.length?Math.max(...sized)||1:0;
    const sizeShare=g=>maxSize&&g&&g.size!=null&&Number.isFinite(Number(g.size))?Math.abs(Number(g.size))/maxSize:null;
    const sizeFactor=g=>{const s=sizeShare(g);return s===null?1:.35+.65*Math.sqrt(s);};
    if(range&&!fit){const x0=760;add('rect',{x:x0,y:52,width:12,height:12,fill:palette[0],'fill-opacity':.25,'data-legend':'colour'});label(x0+16,62,fmt(range[0]));
      add('rect',{x:x0+90,y:52,width:12,height:12,fill:palette[0],'fill-opacity':1,'data-legend':'colour'});label(x0+106,62,fmt(range[1]));label(x0-6,62,result.colorLabel||'Colour',{'text-anchor':'end'});}
    // A Colour legend for marks that carry a series member (box, scatter).
    const seriesLegend=names=>{names.slice(0,12).forEach((name,k)=>{const x=100+(k%6)*140,y=538+Math.floor(k/6)*16;add('rect',{x,y:y-9,width:10,height:10,fill:categoryColour(k),'data-legend':'series'});label(x+14,y,clip(name,18),{'font-size':11});});if(names.length>12)label(100,570,`+${names.length-12} more colours; see the table.`,{'font-size':11});};
    if (['pie','doughnut'].includes(result.type)) {
      const total = result.groups.reduce((n,g) => n+g.value,0); let angle = -Math.PI/2;
      const share = g => (100*g.value/total).toFixed(1), slices = [];
      // Fitted: every length is set in CSS px and converted to drawing units.
      const u = fit ? px => px / fit.s : px => px;
      let cx = 285, cy = 290, r = 175, legend = null;
      if (fit) {
        // The pie is as tall as the box allows; the legend, centred beside it,
        // tightens its rows to a floor, then takes a second column, then ends
        // with "+N more". Pie and legend are centred across the box together.
        const fL = u(PIE.legendPx), fN = u(PIE.notePx), mX = u(14), mY = u(PIE.marginPx), gap = u(PIE.gapPx), colGap = u(16);
        const sw = .8*fL, textAt = sw + .55*fL, pref = 1.85*fL, tight = 1.4*fL, n = result.groups.length, listed = Math.min(n, legendCap);
        const header = range ? 3 : 0, availH = fit.H - 2*mY;
        // The legend's natural width: its widest row, and the Colour scale's lines.
        const need = Math.max(textAt + Math.max(0, ...result.groups.slice(0, listed).map(g => textWidth(`${g.label} · ${share(g)}%`, fL))),
          range ? Math.max(textWidth(result.colorLabel||'Colour', fN), textAt + Math.max(...range.map(v => textWidth(fmt(v), fN)))) : 0);
        r = Math.max(u(36), Math.min(availH/2, (960 - 2*mX - gap - Math.min(need, u(PIE.legendMinPx)))/2));
        const room = Math.max(0, 960 - 2*mX - gap - 2*r), canTwo = (room - colGap)/2 >= u(PIE.columnMinPx);
        let plan = null;
        for (const cols of canTwo ? [1, 2] : [1]) {
          const per = Math.ceil((listed + (n > listed ? 1 : 0))/cols), pitch = Math.min(pref, availH/(header + per));
          if (pitch >= tight - 1e-9) { plan = {cols, pitch, shown: listed}; break; }
        }
        if (!plan) {
          const cols = canTwo ? 2 : 1, per = Math.max(1, Math.floor(availH/tight + 1e-9) - header);
          plan = {cols, pitch: tight, shown: Math.max(0, Math.min(listed, cols*per - 1))};
        }
        const more = n - plan.shown, per = Math.max(1, Math.ceil((plan.shown + (more > 0 ? 1 : 0))/plan.cols));
        const colW = Math.min(need, plan.cols === 1 ? room : (room - colGap)/2), width = plan.cols*colW + (plan.cols - 1)*colGap;
        const x0 = Math.max(mX, (960 - (2*r + gap + width))/2);
        cx = x0 + r; cy = fit.H/2;
        legend = {...plan, per, more, colW, width, header, fL, fN, sw, textAt, colGap, x: x0 + 2*r + gap, top: (fit.H - (header + per)*plan.pitch)/2};
      }
      result.groups.forEach((g,i) => {
        const next = angle + 2*Math.PI*g.value/total, color = colourOf(g,i);
        if (g.value > 0) {
          const node = g.value === total ? add('circle',{cx,cy,r,...paint(g,color)}) : add('path',{
            d:`M ${cx} ${cy} L ${cx+r*Math.cos(angle)} ${cy+r*Math.sin(angle)} A ${r} ${r} 0 ${next-angle > Math.PI ? 1:0} 1 ${cx+r*Math.cos(next)} ${cy+r*Math.sin(next)} Z`,...paint(g,color),stroke:'#fff','stroke-width':fit?u(1.5):2});
          tooltip(node,`${g.label}: ${fmt(g.value)} (${share(g)}%)`+more(g));
          slices.push({index:i,a0:angle,a1:next,share:g.value/total,fill:paint(g,color)});
        }
        if (!fit && i < legendCap) { add('rect',{x:510,y:90+i*32,width:12,height:12,...paint(g,color)}); wrapped(533,100+i*32,`${g.label} · ${share(g)}%`,51,2); }
        angle = next;
      });
      const inner = result.type==='doughnut' ? (fit ? r*95/175 : 95) : 0;
      if(result.type==='doughnut')add('circle',{cx,cy,r:inner,fill:'#fff'});
      // Labels on: each slice of 4% or more shows its share inside it, and
      // nothing is drawn outside the pie. The group is there, even empty, so
      // a caller can tell the pie answered the Labels switch itself.
      // A fitted text's size is set through the element's style object: the
      // Studio's stylesheet sets every chart text to 18px, and the console's
      // Content-Security-Policy (style-src 'self') refuses a style attribute.
      const fontSized = (node, size) => { if (fit) node.style.fontSize = size + 'px'; return node; };
      if (opts.labels) {
        const layer = add('g',{'data-mark-label':'slices'});
        for (const p of pieLabelPlan(slices, {cx, cy, r, inner}, fit ? u(PIE.labelPx) : 12, fit ? u(PIE.labelMinPx) : 11))
          layer.append(fontSized(svgNode('text',{x:p.x,y:p.y+.35*p.size,'text-anchor':'middle',fill:inkOn(p.slice.fill),'font-family':MentatTheme.sans,'font-size':p.size,'font-weight':600,
            'data-mark-label':'true','pointer-events':'none'},p.text), p.size));
      }
      if (legend) {
        const L = legend, rowTop = k => L.top + k*L.pitch, baseline = (k, size) => rowTop(k) + L.pitch/2 + .35*size;
        const text = (x, y, value, size, attrs = {}) => fontSized(add('text',{x,y,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':size,...attrs},value), size);
        let k = 0;
        if (range) {
          // The Colour measure's scale: its name, then its lightest and its
          // darkest shade, one line each so a long figure never runs out.
          text(L.x, baseline(k, L.fN), fitLabel(result.colorLabel||'Colour', L.width, L.fN), L.fN, {'data-legend':'colour-title'}); k++;
          for (const [opacity, value] of [[.25, range[0]], [1, range[1]]]) {
            add('rect',{x:L.x,y:rowTop(k) + (L.pitch - L.sw)/2,width:L.sw,height:L.sw,fill:palette[0],'fill-opacity':opacity,'data-legend':'colour'});
            text(L.x + L.textAt, baseline(k, L.fN), fitLabel(fmt(value), L.width - L.textAt, L.fN), L.fN); k++;
          }
        }
        const slot = i => ({x: L.x + Math.floor(i/L.per)*(L.colW + L.colGap), row: L.header + i%L.per});
        result.groups.slice(0, L.shown).forEach((g,i) => {
          const {x, row} = slot(i);
          add('rect',{x,y:rowTop(row) + (L.pitch - L.sw)/2,width:L.sw,height:L.sw,...paint(g,colourOf(g,i)),'data-legend':'pie'});
          tooltip(text(x + L.textAt, baseline(row, L.fL), fitLabel(g.label, L.colW - L.textAt, L.fL, ` · ${share(g)}%`), L.fL, {'data-legend-row':i}), `${g.label} · ${share(g)}%`);
        });
        if (L.more > 0) {
          const {x, row} = slot(L.shown);
          tooltip(text(x, baseline(row, L.fN), `+${fmt(L.more)} more`, L.fN, {fill:MentatTheme.muted||'#62596e','data-legend-more':L.more}), 'Every slice is drawn. The table and the CSV list every group.');
        }
        svg.__fit = {box: fit.box, width: fit.wPx, height: fit.bounded ? fit.hPx : null, viewHeight: fit.H, scale: fit.s};
        // Exports draw today's picture: title, subtitle, caption and geometry.
        svg.__mentatExport = () => chartSVG(result, measure, config, source, titleText, scopeText, {...opts, fit: null}).svg;
      } else {
        label(20,canvasHeight-50,`${grouping} · share of ${measure}`);
        if (result.groups.length > legendCap) label(510,canvasHeight-30,'All slices shown. Full labels are in the table and CSV.');
      }
    } else if (result.type==='horizontal') {
      const groups=result.groups,axis=numericAxis(Math.min(0,...groups.map(g=>g.value)),Math.max(0,...groups.map(g=>g.value)),config.aggregation==='count');
      const scale=v=>280+(v-axis.min)/(axis.max-axis.min)*610;
      const plotHeight=canvasHeight-190,step=plotHeight/groups.length;
      plot={orient:'horizontal',min:axis.min,max:axis.max,p0:scale(axis.min),p1:scale(axis.max),from:118,to:125+plotHeight};
      wrapped(20,100,grouping,34,1,{'data-axis-title':'y'});
      groups.forEach((g,i)=>{const yy=125+i*step,h=Math.min(24,step*.65);wrapped(270,yy+h/2+3,g.label,33,2,{'text-anchor':'end'});
        const f=sizeFactor(g),hh=f===1?h:h*f,top=f===1?yy:yy+(h-hh)/2;
        tooltip(add('rect',{x:Math.min(scale(0),scale(g.value)),y:top,width:Math.max(1,Math.abs(scale(g.value)-scale(0))),height:hh,...paint(g,colourOf(g,i))}),`${g.label}: ${fmt(g.value)}`+more(g));
        markText(g.value<0?Math.min(scale(0),scale(g.value))-5:Math.max(scale(0),scale(g.value))+5,top+hh/2+4,labelText(g,g.value),{'text-anchor':g.value<0?'end':'start'});});
      for(const value of axis.ticks)label(scale(value),canvasHeight-42,tick(value),{'text-anchor':'middle','data-axis-tick':'x'});
      wrapped(585,canvasHeight-18,measure,70,1,{'text-anchor':'middle','data-axis-title':'x'});
    } else if(result.type==='radar') {
      // A lenient result (result.warnings) already says why a radar is thin or
      // left out negatives, so it draws instead of refusing (W1).
      const groups=result.groups;if(groups.length<3&&!Array.isArray(result.warnings))throw new Error('Radar charts need at least three groups.');
      if(groups.some(g=>g.value<0)&&!Array.isArray(result.warnings))throw new Error('Radar charts need nonnegative values.');
      const max=(groups.length?Math.max(...groups.map(g=>g.value)):1)||1,point=(i,r)=>[470+Math.cos(i*2*Math.PI/groups.length-Math.PI/2)*r,295+Math.sin(i*2*Math.PI/groups.length-Math.PI/2)*r];
      for(let ring=1;ring<=4;ring++)add('polygon',{points:groups.map((g,i)=>point(i,ring*45).join(',')).join(' '),fill:'none',stroke:MentatTheme.grid});
      groups.forEach((g,i)=>{if(opts.axisLabels!=='all'&&i%Math.max(1,Math.ceil(groups.length/12)))return;const p=point(i,210);wrapped(p[0],p[1],g.label,24,2,{'text-anchor':'middle'});});
      tooltip(add('polygon',{points:groups.map((g,i)=>point(i,Math.max(0,g.value)/max*180).join(',')).join(' '),fill:palette[0],'fill-opacity':.2,stroke:palette[0]}),groups.map(g=>`${g.label}: ${fmt(g.value)}`).join('; '));
      // Vertices carry each group's own tooltip, Colour and label (W7, W8, W11).
      if(opts.labels||groups.some(g=>Number.isInteger(g.seriesIndex)||g.extra||g.colorValue!=null))groups.forEach((g,i)=>{const p=point(i,Math.max(0,g.value)/max*180);
        tooltip(add('circle',{cx:p[0],cy:p[1],r:4,...paint(g,colourOf(g,0))}),`${g.label}: ${fmt(g.value)}`+more(g));markText(p[0],p[1]-8,labelText(g,g.value));});
      wrapped(20,575,`${grouping} · ${measure} · radial scale 0–${tick(max)}`,100,2,{'data-axis-title':'y'});
    } else {
      const groups = result.groups || [], isBox = result.type === 'box', scatter = result.type === 'scatter', dot = result.type === 'dot';
      let cumulative=0;const waterfall=result.type==='waterfall'?groups.map(g=>{const start=cumulative;cumulative+=g.value;return {start,end:cumulative};}):null;
      const values = waterfall?waterfall.flatMap(g=>[g.start,g.end]):scatter ? result.points.map(p=>p.y) : isBox ? groups.flatMap(g=>[g.low,g.high,...g.outliers]) : groups.map(g=>g.value);
      if (!values.length) values.push(0,1); // an empty lenient result draws empty axes
      let min = Math.min(...values), max = Math.max(...values);
      if (!isBox && !scatter && !dot) { min = Math.min(0,min); max = Math.max(0,max); }
      const countAxis=result.type==='histogram'||(!isBox&&!scatter&&config.aggregation==='count'&&config.transform!=='yoy');
      const axis=numericAxis(min,max,countAxis);min=axis.min;max=axis.max;
      const y = v => 430 - (v-min)/(max-min)*335;
      plot={orient:'vertical',min,max,p0:y(min),p1:y(max),from:100,to:920};
      for (const value of axis.ticks) { add('line',{x1:100,x2:920,y1:y(value),y2:y(value),stroke:MentatTheme.grid}); label(90,y(value)+4,tick(value),{'text-anchor':'end','data-axis-tick':'y'}); }
      wrapped(100,88,result.type === 'histogram' ? 'Row count' : result.type==='waterfall'?`${measure} · cumulative total`:measure,100,1,{'data-axis-title':'y'});
      if (scatter) {
        // Several measures (W2): measures[0] is the horizontal axis.
        const listed=Array.isArray(config.measures)&&config.measures.length>=2,xCol=listed?Number(config.measures[0].column):config.x;
        const seriesNames=Array.isArray(result.series)?result.series:null;
        const pointPaint=p=>range&&typeof p.colorValue==='number'?paint(p,palette[0]):{fill:seriesNames&&p.series!=null?categoryColour(Math.max(0,seriesNames.indexOf(p.series))):palette[0],'fill-opacity':.65};
        const xs=result.points.length?result.points.map(p=>p.x):[0,1],xAxis=numericAxis(Math.min(...xs),Math.max(...xs));
        const x=v=>100+(v-xAxis.min)/(xAxis.max-xAxis.min)*820;
        svg.setAttribute('data-total-points',result.points.length);
        if(result.points.length<=2000)result.points.forEach(p=>{const s=sizeShare(p),cx=x(p.x),cy=y(p.y);
          tooltip(add('circle',{cx,cy,r:s===null?4:3+12*Math.sqrt(s),...pointPaint(p)}),`${p.label!=null?readable(p.label)+'\n':''}${columnLabel(xCol,source)}: ${fmt(p.x)}; ${measure}: ${fmt(p.y)}${p.series!=null?`\nColour: ${p.series}`:''}`+more(p));
          markText(cx+7,cy-7,p.labelValue!=null?labelText(p,p.y):p.label!=null?clip(readable(p.label),24):fmt(p.y),{'text-anchor':'start'});});
        else (seriesNames?seriesNames.map(name=>result.points.filter(p=>p.series===name)):[result.points]).forEach((list,k)=>{for(let start=0;start<list.length;start+=1000){
          const points=list.slice(start,start+1000);
          const path=points.map(p=>`M${x(p.x).toFixed(3)},${y(p.y).toFixed(3)}h.01`).join(' ');
          add('path',{d:path,fill:'none',stroke:seriesNames?categoryColour(k):palette[0],'stroke-width':4,'stroke-linecap':'round','stroke-opacity':.6,'data-point-count':points.length});
        }});
        for(const value of xAxis.ticks)label(x(value),453,tick(value),{'text-anchor':'middle','data-axis-tick':'x'});
        wrapped(510,510,listed||result.grain==='group'?columnLabel(xCol,source):xTitle,85,2,{'text-anchor':'middle','data-axis-title':'x'});
        if(seriesNames)seriesLegend(seriesNames);
      } else {
        const step=820/groups.length, x=i=>100+step*(i+.5), width=Math.min(60,step*.7);
        if(result.type==='area')add('polygon',{points:`${x(0)},${y(0)} `+groups.map((g,i)=>`${x(i)},${y(g.value)}`).join(' ')+` ${x(groups.length-1)},${y(0)}`,fill:palette[0],'fill-opacity':.16});
        // With Size (W7) a line is drawn segment by segment, each as wide as the
        // mean size of its two ends; without it, one polyline as before.
        if (['line','area'].includes(result.type)&&maxSize) groups.forEach((g,i)=>{const p=groups[i-1];if(!i||g.forecast||p.forecast)return;
          const a=sizeShare(p),b=sizeShare(g);add('line',{x1:x(i-1),y1:y(p.value),x2:x(i),y2:y(g.value),stroke:palette[0],'stroke-width':1.5+4.5*(((a??b??0)+(b??a??0))/2),'stroke-linecap':'round'});});
        else if (['line','area'].includes(result.type)) add('polyline',{points:groups.filter(g=>!g.forecast).map((g,i)=>`${x(i)},${y(g.value)}`).join(' '),fill:'none',stroke:palette[0],'stroke-width':2});
        const firstForecast=groups.findIndex(g=>g.forecast);if(firstForecast>0)add('polyline',{points:groups.slice(firstForecast-1).map((g,i)=>`${x(firstForecast-1+i)},${y(g.value)}`).join(' '),fill:'none',stroke:palette[0],'stroke-width':2,'stroke-dasharray':'7 5'});
        if(result.type==='pareto'){let running=0;const total=groups.reduce((n,g)=>n+g.value,0);add('polyline',{points:groups.map((g,i)=>{running+=g.value;return `${x(i)},${430-running/total*335}`;}).join(' '),fill:'none',stroke:palette[1],'stroke-width':3});label(920,78,'Cumulative 0–100%',{'text-anchor':'end'});}
        // Every axis label on request (W8), turned when there are many.
        const allLabels=opts.axisLabels==='all',turned=allLabels&&groups.length>12;
        groups.forEach((g,i)=>{
          const color=colourOf(g,i);
          if(isBox){
            add('line',{x1:x(i),x2:x(i),y1:y(g.low),y2:y(g.high),stroke:color,'stroke-width':2});
            for(const v of [g.low,g.high])add('line',{x1:x(i)-width/3,x2:x(i)+width/3,y1:y(v),y2:y(v),stroke:color,'stroke-width':2});
            tooltip(add('rect',{x:x(i)-width/2,y:y(g.q3),width,height:Math.max(1,y(g.q1)-y(g.q3)),fill:color,'fill-opacity':.25,stroke:color}),`${g.label}: median ${fmt(g.median)}, Q1 ${fmt(g.q1)}, Q3 ${fmt(g.q3)}, n=${g.n}`+more(g));
            add('line',{x1:x(i)-width/2,x2:x(i)+width/2,y1:y(g.median),y2:y(g.median),stroke:color,'stroke-width':3});
            g.outliers.forEach(v=>tooltip(add('circle',{cx:x(i),cy:y(v),r:3,fill:color}),`${g.label}: ${fmt(v)} (outlier)`));
            markText(x(i)+width/2+4,y(g.median)+4,labelText(g,g.median),{'text-anchor':'start'});
          }else if(waterfall){const w=waterfall[i];tooltip(add('rect',{x:x(i)-width/2,y:Math.min(y(w.start),y(w.end)),width,height:Math.max(1,Math.abs(y(w.start)-y(w.end))),...paint(g,Number.isInteger(g.seriesIndex)?color:g.value<0?palette[1]:palette[0])}),`${g.label}: ${fmt(g.value)}; running total ${fmt(w.end)}`+more(g));
            markText(x(i),Math.min(y(w.start),y(w.end))-5,g.labelValue!=null?labelText(g,g.value):(g.value>0?'+':'')+fmt(g.value));
          }else if(['line','area'].includes(result.type)){ tooltip(add('circle',{cx:x(i),cy:y(g.value),r:4,fill:g.forecast?'white':palette[0],stroke:palette[0]}),`${g.forecast?'Same-phase baseline ':g.partial?'Partial period ':''}${g.label}: ${fmt(g.value)}${g.forecast_basis_year?`; based on ${g.forecast_basis_year}`:''}`+more(g));
            markText(x(i),y(g.value)-9,labelText(g,g.value));
          }else if(dot){const s=sizeShare(g);tooltip(add('circle',{cx:x(i),cy:y(g.value),r:s===null?6:3+12*Math.sqrt(s),...paint(g,palette[0])}),`${g.label}: ${fmt(g.value)}`+more(g));
            markText(x(i),y(g.value)-(s===null?10:7+12*Math.sqrt(s)),labelText(g,g.value));
          }else{const f=sizeFactor(g),bw=f===1?width:width*f;
            tooltip(add('rect',{x:x(i)-bw/2,y:Math.min(y(0),y(g.value)),width:bw,height:Math.max(1,Math.abs(y(0)-y(g.value))),...paint(g,color)}),`${g.label}: ${fmt(g.value)}`+more(g));
            markText(x(i),g.value<0?y(g.value)+14:y(g.value)-5,labelText(g,g.value));}
          if(allLabels||i % Math.max(1,Math.ceil(groups.length/8)) === 0){
            const displayLabel=result.type==='histogram'?g.label.split(' – ').map(value=>Number(value).toLocaleString('en-US',{maximumSignificantDigits:6})).join(' – '):g.label;
            const node=wrapped(x(i),453,displayLabel,16,2,turned?{'text-anchor':'end','data-category-label':'true',transform:`rotate(-30 ${x(i)} 453)`}:{'text-anchor':'middle','data-category-label':'true'});
            if(result.type==='histogram')node.querySelector('title').textContent=g.label;
          }
        });
        wrapped(510,515,xTitle,85,2,{'text-anchor':'middle','data-axis-title':'x'});
        if(groups.length>8)label(100,574,allLabels?`${groups.length} groups plotted; every axis label shown. Full labels and values are in the table and CSV.`:`${groups.length} groups plotted; selected axis labels shown. Full labels and values are in the table and CSV.`);
        if(isBox&&Array.isArray(result.series))seriesLegend(result.series);
      }
    }
    return {svg,plot};
  }
  // Marks v3 (lane M, boards #50-#52). Square, Shape, Polygon, Density, a Path
  // on a line or area and a dual axis of marks cards are drawn by
  // contribution-plot.js (a one-measure Square or Shape by contribution-charts.js,
  // as Circle is), from the worksheet's groups turned into rows. Only a
  // configuration that carries config.mark or config.v3.path gets here.
  const V3_CHART=['square','shape','polygon','density','cards','circle'];
  const AGG_NAME={sum:'SUM',mean:'AVG',count:'COUNT',countd:'COUNTD',median:'MEDIAN',min:'MIN',max:'MAX',first:'FIRST'};
  const specName=(spec,source)=>spec.column<0?'Row count':(AGG_NAME[spec.aggregation]||'SUM')+'('+(source?.columns[spec.column]?.name||'Field')+')';
  // A group that carries its own encoding labels (Chart Studio's verified pie,
  // doughnut, waterfall, Pareto and radar views) lends them to the result, so
  // its Colour measure shades and its Size and Label reach the hover text.
  function liftEncodings(result){
    const groups=Array.isArray(result&&result.groups)?result.groups:null;
    if(!groups||!groups.some(g=>g&&(g.colorLabel||g.sizeLabel||g.labelLabel)))return result;
    const out=Object.assign({},result),first=key=>(groups.find(g=>g&&g[key])||{})[key];
    const shades=groups.map(g=>g&&g.colorValue).filter(v=>typeof v==='number'&&Number.isFinite(v));
    if(!out.colorLabel&&first('colorLabel')){out.colorLabel=first('colorLabel');if(shades.length)out.colorRange=[Math.min(...shades),Math.max(...shades)];}
    if(!out.sizeLabel&&first('sizeLabel'))out.sizeLabel=first('sizeLabel');
    if(!out.labelLabel&&first('labelLabel'))out.labelLabel=first('labelLabel');
    return out;
  }
  function v3SVG(result, measure, config, source, titleText, scopeText, options) {
    const plot=globalThis.MentatContributionPlot,charts=globalThis.MentatContributionCharts;
    if(!plot||typeof plot.render!=='function')throw new Error((config.mark||'This mark')+' needs contribution-plot.js');
    const v3=config.v3||{},opts=options||{},scatter=result.type==='scatter';
    const used=new Set(),idOf=name=>{let id=String(name),k=2;while(used.has(id))id=name+' ('+(k++)+')';used.add(id);return id;};
    const listed=Array.isArray(config.dimensions)?config.dimensions:[];
    const dimColumns=scatter?listed.filter(c=>c!==config.x&&c!==config.y):(listed.length?listed:config.x>=0?[config.x]:[]);
    const dimIds=dimColumns.map(c=>idOf(columnLabel(c,source)));
    const dimOf=column=>{const at=dimColumns.indexOf(column);return at<0?'':dimIds[at];};
    const valueSpec=Array.isArray(config.measures)&&config.measures.length?config.measures[scatter&&config.measures.length>1?1:0]:{column:config.y,aggregation:config.aggregation};
    const yId=idOf(specName(scatter?{column:config.y,aggregation:config.aggregation}:valueSpec,source));
    const xId=scatter?idOf(specName({column:config.x,aggregation:config.xAggregation||'sum'},source)):'';
    const extraIds=(result.extraLabels||[]).slice(),sizeId=result.sizeLabel||'',labelId=result.labelLabel||'',colourId=result.colorLabel||'';
    const carry=(row,item)=>{if(item.extra)for(const [k,v] of Object.entries(item.extra))row[k]=v;if(sizeId&&item.size!=null)row[sizeId]=item.size;
      if(labelId&&item.labelValue!=null)row[labelId]=item.labelValue;if(colourId&&item.colorValue!=null)row[colourId]=item.colorValue;return row;};
    const partsOf=(label,other)=>{const parts=tupleOf({label,other},dimIds.length),row={};dimIds.forEach((id,k)=>{row[id]=parts[k]??'(Missing)';});return row;};
    const pointTypes=['polygon','density'].includes(config.mark);
    let rows,seriesId='';
    if(scatter)rows=(result.points||[]).map(p=>carry(Object.assign(dimIds.length&&p.label!=null?partsOf(p.label,false):{},{[xId]:p.x,[yId]:p.y}),p));
    else if(result.series&&(result.groups||[]).every(g=>Array.isArray(g.values))){
      seriesId=idOf(result.colorField||(config.series>=0?columnLabel(config.series,source):'Series'));
      rows=result.groups.flatMap(g=>g.values.filter(v=>v.value!=null).map(v=>carry(Object.assign(partsOf(g.label,g.other),{[seriesId]:v.series,[yId]:v.value}),v)));
    }else rows=(result.groups||[]).filter(g=>!(g.other&&pointTypes)).map(g=>carry(Object.assign(partsOf(g.label,g.other),{[yId]:g.value}),g));
    const dims=seriesId?[...dimIds,seriesId]:dimIds,measures=scatter?[xId,yId]:[yId];
    const valueIds=[...new Set([...measures,...extraIds,sizeId,labelId,colourId].filter(Boolean))];
    const cols=[...dims.map(id=>({id,name:id,role:'dimension'})),...valueIds.filter(id=>!dims.includes(id)).map(id=>({id,name:id,unit:'number'}))];
    const pillId=p=>!p?'':p.role==='dimension'?dimOf(p.column):(p.column===valueSpec.column&&p.aggregation===valueSpec.aggregation?yId:specName(p,source));
    const spec={dimensions:dims,measures,columns:cols,labels:!!opts.labels,title:titleText,tooltip:extraIds.filter(id=>!measures.includes(id))};
    if(opts.axisLabels)spec.axisLabels=opts.axisLabels;
    const colourDim=v3.color>=0?dimOf(v3.color):seriesId;
    if(colourDim)spec.color=colourDim;
    if(config.colorMeasure&&colourId)spec.colorMeasure=colourId;
    if(config.size&&sizeId)spec.size=sizeId;
    if(labelId)spec.labelFields=[labelId];
    if(config.mark==='cards'){
      const cards=Array.isArray(config.cards)?config.cards:[];
      Object.assign(spec,{type:'cards',synchronize:config.synchronize===true,measures:[...new Set(cards.map(c=>pillId(Object.assign({},c.measure,{role:'measure'}))))]});
      spec.tooltip=extraIds.filter(id=>!spec.measures.includes(id));
      spec.cards=cards.map(c=>({id:c.id,measure:pillId(Object.assign({},c.measure,{role:'measure'})),type:c.type,glyph:c.glyph||'',shape:c.shape>=0?dimOf(c.shape):'',shapeMap:c.glyphs||null,
        color:c.color&&c.color.role==='dimension'?dimOf(c.color.column):'',colorMeasure:c.color&&c.color.role==='measure'?specName(c.color,source):'',size:c.size?specName(c.size,source):'',
        labelFields:c.label?[pillId(c.label)].filter(Boolean):[],tooltip:(c.tooltip||[]).map(p=>specName(p,source)),path:c.path?pillId(c.path):'',
        opacity:c.opacity,border:c.border,halo:c.halo,visible:c.visible!==false}));
      for(const key of ['color','colorMeasure','size','labelFields'])delete spec[key];
    }else if(['square','shape','circle'].includes(config.mark)){
      spec.type=scatter?'scatter':'dot';
      spec.glyph=config.mark==='circle'?'':v3.glyph||(v3.shape>=0?'':'circle');
      if(v3.shape>=0){spec.shape=dimOf(v3.shape);if(v3.glyphs)spec.shapeMap=v3.glyphs;}
    }else if(pointTypes){spec.type=config.mark;spec.layout=scatter?'scatter':'axis';}
    else spec.type=['area','multiarea'].includes(result.type)?'area':'line';
    if(v3.path)spec.path=pillId(v3.path);
    const container=document.createElement('div');
    const draw=spec.type==='dot'&&charts&&typeof charts.render==='function'?charts.render:plot.render;
    const svg=draw(container,rows,spec,()=>{})||null;
    if(svg){svg.setAttribute('aria-label',titleText);svg.insertBefore(svgNode('desc',{},scopeText),svg.firstChild);
      // The drawing labels its own marks (data-label), so the Studio lays none over them.
      if(opts.labels)svg.append(svgNode('g',{'data-mark-label':'drawn'}));
      return {svg,plot:null};}
    const empty=svgNode('svg',{xmlns:'http://www.w3.org/2000/svg',viewBox:'0 0 960 200',role:'img','aria-label':titleText});
    empty.append(svgNode('title',{},titleText),svgNode('desc',{},scopeText),svgNode('rect',{width:960,height:200,fill:'#fff'}),
      svgNode('text',{x:20,y:100,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':14},container.textContent||'Nothing to draw for this selection.'));
    return {svg:empty,plot:null};
  }
  function draw(result, measure) {
    el('image').replaceChildren(chartSVG(result,measure,chartConfig,data,el('title').textContent,el('scope').textContent).svg);
  }
  function tableRows(result=chart, config=chartConfig, source=data) {
    const grouping=result.groupingLabel||columnLabel(config.x,source);
    // r3 columns (W7, W11, W6 gantt), present only when the result carries them.
    const r3=list=>{const cols=[];const has=key=>list.some(g=>g[key]!=null);
      if(has('first'))cols.push(['First date',g=>g.first??''],['Last date',g=>g.last??''],['Span (days)',g=>g.span??'']);
      if(result.sizeLabel&&has('size'))cols.push([`Size · ${result.sizeLabel}`,g=>g.size??'']);
      for(const key of result.extraLabels||[])cols.push([key,g=>g.extra?.[key]??'']);
      if(has('labelValue'))cols.push([`Label · ${result.labelLabel||'value'}`,g=>g.labelValue??'']);
      if(result.colorLabel&&has('colorValue'))cols.push([`Colour · ${result.colorLabel}`,g=>g.colorValue??'']);
      return cols;};
    const withR3=(table,list)=>{const cols=r3(list);if(!cols.length)return table;const [head,...rows]=table;return [[...head,...cols.map(c=>c[0])],...rows.map((row,i)=>[...row,...cols.map(c=>c[1](list[i]))])];};
    if(result.series&&Array.isArray(result.groups)&&result.groups.every(g=>Array.isArray(g.values)))return [[grouping,...result.series.map(name=>result.seriesConfig?name:`${name} · ${measureLabel(config,result,source)}`)],...result.groups.map(g=>[g.label,...g.values.map(v=>v.value??'Unavailable')])];
    if (result.type === 'scatter') {
      const listed=Array.isArray(config.measures)&&config.measures.length>=2,xCol=listed?Number(config.measures[0].column):config.x,yCol=listed?Number(config.measures[1].column):config.y;
      const named=result.points.some(p=>p.label!=null),coloured=result.points.some(p=>p.series!=null);
      if(!named&&!coloured&&!r3(result.points).length)return [[columnLabel(config.x,source),columnLabel(config.y,source)], ...result.points.map(p=>[p.x,p.y])];
      return withR3([[...(named?[grouping]:[]),columnLabel(xCol,source),columnLabel(yCol,source),...(coloured?['Colour']:[])],...result.points.map(p=>[...(named?[readable(p.label)]:[]),p.x,p.y,...(coloured?[p.series??'']:[])])],result.points);
    }
    if (result.type === 'box') return withR3([[grouping,'Count',`Q1 · ${measureLabel(config,result,source)}`,`Median · ${measureLabel(config,result,source)}`,`Q3 · ${measureLabel(config,result,source)}`,'Lower whisker','Upper whisker','Outliers'],...result.groups.map(g=>[g.label,g.n,g.q1,g.median,g.q3,g.low,g.high,g.outliers.join('; ')])],result.groups);
    return withR3([[result.type==='histogram'?`${columnLabel(config.y,source)} · range`:grouping,result.type==='histogram'?'Row count':result.type==='gantt'?'Span (days)':measureLabel(config,result,source),...(result.temporal?['Status','Baseline period']:[])],...result.groups.map(g=>[g.label,g.value,...(result.temporal?[g.forecast?'Same-phase baseline':g.partial?'Partial observation':'Observed / derived',g.forecast_basis_year??'']:[])])],result.groups);
  }
  function renderValues() {
    const [head,...rows]=tableRows();
    const shown=rows.slice(0,200);
    el('values').innerHTML=`<p class="chart-note">${rows.length>shown.length?`Showing the first ${fmt(shown.length)} of ${fmt(rows.length)} rows. Export data CSV includes every plotted value.`:`All ${fmt(rows.length)} rows shown.`}</p>`+'<table><thead><tr>'+head.map(h=>`<th>${esc(h)}</th>`).join('')+'</tr></thead><tbody>'+shown.map(r=>'<tr>'+r.map(v=>`<td>${esc(typeof v==='number'?fmt(v):v)}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
  }
  function download(content,type,name) {
    const url=URL.createObjectURL(new Blob([content],{type})), link=document.createElement('a');
    link.href=url; link.download=name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  for(const format of ['svg','png']) el('save-'+format).onclick=async()=>{
    if(!chart)return;
    try {await MentatExport.image(el('image').firstChild,format,el('title').textContent,el('scope').textContent);}
    catch(e){error(e.message);}
  };
  el('copy-image').onclick=async()=>{
    if(!chart)return;
    error('');
    try {await MentatExport.copy(el('image').firstChild,el('title').textContent,el('scope').textContent);}
    catch(e){error(e.message);}
  };
  el('briefing').onclick=()=>{
    if(!chart)return;
    if(!window.MentatBriefing){error('Briefing needs a console restart.');return;}
    error('');
    const report=(el('report').selectedOptions[0]||{}).textContent||'';
    window.MentatBriefing.capture({svg:el('image').firstChild,title:el('title').textContent,
      scope:el('scope').textContent,chip:report,note:'',basis:'Worksheet values',
      sheetId:'classic',sheetName:el('title').textContent,source:report,
      provenance:(data&&data.sha256)||'',origin:'classic'});
  };
  el('save-csv').onclick=()=>{
    if(!chart)return;
    const quote=v=>'"'+String(typeof v==='string' && /^[=+\-@\t\r]/.test(v)?"'"+v:v).replaceAll('"','""')+'"';
    const rows=[['Chart',el('title').textContent],['Scope',el('scope').textContent],...tableRows()];
    download('\uFEFF'+rows.map(r=>r.map(quote).join(',')).join('\r\n'),'text/csv;charset=utf-8','mentat-chart.csv');
  };
  el('run').onchange=loadReports; el('report').onchange=loadSheets; el('sheet').onchange=()=>scan();
  el('source-report').onclick=()=>loadRuns();
  el('rescan').onclick=()=>scan(true); el('suggestion').onchange=()=>{applySuggestion();create();}; el('add-filter').onclick=addFilter; el('create').onclick=create;
  document.querySelectorAll('#chart-quick button, #chart-time-quick button').forEach(button=>button.onclick=()=>quick(button.dataset.chart||button.dataset.transform));
  el('settings').addEventListener('input',event=>{
    if(event.target===el('type')){quick(el('type').value);return;}
    if(event.target===el('transform')&&el('transform').value!=='none'){quick(el('transform').value);return;}
    if(event.target===el('y'))el('aggregation').value=el('y').value==='-1'?'count':'sum';
    syncControls();clearTimeout(redrawTimer);if(event.target.tagName==='SELECT')create();else{clearChart();redrawTimer=setTimeout(create,120);}
  });
  el('all-rows').onclick=()=>{el('start').value='';el('end').value='';el('limit').value='';create();};
  el('header').addEventListener('input',()=>{clearChart();el('settings').disabled=true;el('loading').textContent='Rescan columns to apply the new header row.';});
  window.addEventListener('mentat:chart-data',event=>{
    ++revision;reset();reportControls(false);loaded=true;data=event.detail;
    options(el('report'),[['comparison','Selected comparison subjects']]);options(el('sheet'),[['Comparison','Comparison']]);
    options(el('run'),[['comparison','Selected subjects']]);
    el('header').value=1;el('settings').disabled=false;el('filters').replaceChildren();el('start').value='';el('end').value='';el('limit').value='';
    el('context').textContent=data.note;el('loading').textContent=`${data.row_count} subject-period observations`;
    options(el('x'),[[-1,'All rows'],...data.columns.map(c=>[c.index,c.name])]);measureOptions();
    el('x').value='0';el('y').value='2';el('type').value='bar';el('aggregation').value='sum';el('transform').value='none';
    if(data.default_config)for(const [key,value] of Object.entries(data.default_config))el(key).value=value;
    suggestions=[];options(el('suggestion'),[['','Comparison data']]);prepareSubjects();
    if(data.default_config){syncControls();create();}else quick('bar');
  });
  let loaded=false;
  window.addEventListener('mentat:graph',event=>{
    const next=event.detail;
    if(next?.run!==graphContext?.run || next?.industry!==graphContext?.industry)loaded=false;
    graphContext=next;
  });
  // Chart Studio renders through the same engine; every argument is supplied by
  // the caller, so nothing here reads or writes the dialog's own state.
  globalThis.MentatChartRender=Object.freeze({chartSVG,tableRows,measureLabel,columnLabel,numericAxis});
  function activate() {if(!document.getElementById('view-graph').hidden && document.getElementById('graph-builder').open && !loaded){loaded=true;loadRuns();}}
  document.getElementById('graph-builder').addEventListener('mentat:open',activate);
  document.getElementById('graph-advanced').addEventListener('toggle',activate);
  document.querySelector('.workspace-tabs').addEventListener('click',activate);
  document.querySelector('.workspace-tabs').addEventListener('keydown',activate);
  window.addEventListener('hashchange',activate);window.addEventListener('mentat:view',activate);activate();
})();
