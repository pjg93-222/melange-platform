/* Contribution views consume server aggregates. Currency stays in integer cents. */
(function(root){
 'use strict';
 const NS='http://www.w3.org/2000/svg',palette=root.MentatTheme?.palette||['#276749','#477f9b','#aa7942','#805f87','#69783e','#ac6263','#527c77','#6f7499'];
 // Paul, 2026-09-24: Colour members, series and measures take the theme's twelve categorical colours,
 // tinted past twelve (theme.js categoryColour), as a pie's groups and a grouped chart's series do, so
 // group i is the same colour in all three. A colorMap still wins. Without theme.js the palette cycles.
 const categoryColour=typeof root.MentatTheme?.categoryColour==='function'?root.MentatTheme.categoryColour:i=>palette[i%palette.length];
 const moneyKeys=new Set(['amount','gross_amount','refunds','average_amount','median_amount','min_amount','max_amount','q1_amount','q3_amount','stddev_amount']);
 const isMoney=(id,columns=[])=>moneyKeys.has(id)||columns.some(c=>(c.id===id||c.name===id)&&['cents','currency','usd_cents'].includes(c.unit));
 function format(value,id,columns=[],compact=false){if(columns.some(c=>c.id===id&&c.unit==="date"))return value||"—";if(value==null||!Number.isFinite(Number(value)))return '—';return new Intl.NumberFormat('en-US',isMoney(id,columns)?{style:'currency',currency:'USD',maximumFractionDigits:compact?1:2,...(compact?{notation:'compact'}:{})}:{maximumFractionDigits:2,...(compact?{notation:'compact'}:{})}).format(isMoney(id,columns)?Number(value)/100:Number(value));}
 const label=(row,dimensions)=>dimensions.map(d=>row[d]==null||row[d]===''?'Unknown':String(row[d])).join(' / ')||'Selected population';
 const name=(id,columns)=>columns.find(c=>c.id===id)?.name||id.replaceAll('_',' ');
 /* Code review F2 (2026-09-24). A repeated aggregate group - the same value on
    every drawn dimension, which happens when a queried field is not drawn - is
    combined statistic by statistic, never by adding every number. Amounts and
    observation counts add; a mean is weighted by each group's observation
    count (the net amount over the observations when both are present); a
    population standard deviation is pooled from each group's count, mean and
    deviation; the minimum and maximum take the extremes; first and last dates
    widen the span and the span is recomputed from them. A median, a quartile
    or a distinct count cannot be rebuilt from group summaries, so a combined
    mark leaves it blank and the note says so. A field the chart does not draw
    keeps every value it had (a list), so a combined mark's drill-through asks
    for all of its groups' records. A worksheet column names its aggregation. */
 const COMBINE_RULE={amount:'sum',gross_amount:'sum',refunds:'sum',records:'sum',average_amount:'mean',stddev_amount:'stddev',min_amount:'min',max_amount:'max',first_date:'first',last_date:'last',span_days:'span',median_amount:'none',q1_amount:'none',q3_amount:'none',contributors:'distinct',committees:'distinct',companies:'distinct',active_days:'distinct'};
 function combineRule(id,columns=[]){
  if(COMBINE_RULE[id])return COMBINE_RULE[id];
  const column=columns.find(c=>c.id===id),agg=column&&column.aggregation;
  return agg==='sum'||agg==='count'?'sum':agg==='min'||agg==='max'?agg:agg==='countd'?'distinct':'none';
 }
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const isDimensionField=(id,values,columns)=>{const column=columns.find(c=>c.id===id);if(column)return column.role==='dimension'||column.type==='category';return !COMBINE_RULE[id]&&!values.every(v=>v==null||finite(v));};
 const RANK={blank:3,weighted:2,pooled:2};
 function combineGroup(list,dims,columns=[]){
  const row={...list[0]},outcome={};
  const ids=[...new Set(list.flatMap(r=>Object.keys(r)))].filter(id=>!dims.includes(id));
  const meanOf=r=>finite(r.amount)&&finite(r.records)&&r.records>0?r.amount/r.records:finite(r.average_amount)?r.average_amount:null;
  const whole=values=>values.every(v=>Number.isInteger(v));
  for(const id of ids){
   const values=list.map(r=>r[id]),present=values.filter(v=>v!=null);
   if(isDimensionField(id,values,columns)){const distinct=[...new Map(present.map(v=>[JSON.stringify(v),v])).values()];row[id]=distinct.length>1?distinct:distinct.length?distinct[0]:values[0];continue;}
   const rule=combineRule(id,columns),numbers=present.filter(finite);
   if(rule==='first'||rule==='last'){const dates=present.map(String).sort();row[id]=dates.length?(rule==='first'?dates[0]:dates.at(-1)):null;outcome[id]='widened';continue;}
   if(rule==='sum'){row[id]=numbers.length?numbers.reduce((a,b)=>a+b,0):null;outcome[id]='added';continue;}
   if(rule==='min'||rule==='max'){row[id]=numbers.length?Math[rule](...numbers):null;outcome[id]=rule==='min'?'lowest':'highest';continue;}
   if(rule==='mean'||rule==='stddev'){
    const used=list.filter(r=>finite(r.records)&&r.records>0),complete=used.length&&list.every(r=>r[id]==null||finite(r.records))&&used.every(r=>finite(meanOf(r))&&(rule==='mean'||finite(r[id])));
    if(!complete){row[id]=null;outcome[id]='blank';continue;}
    const n=used.reduce((a,r)=>a+r.records,0),mean=used.reduce((a,r)=>a+r.records*meanOf(r),0)/n;
    const value=rule==='mean'?mean:Math.sqrt(Math.max(0,used.reduce((a,r)=>a+r.records*(r[id]*r[id]+(meanOf(r)-mean)**2),0)/n));
    row[id]=whole(numbers)?Math.round(value):value;outcome[id]=rule==='mean'?'weighted':'pooled';continue;
   }
   if(rule==='span')continue;
   row[id]=null;outcome[id]='blank';
  }
  if(ids.includes('span_days')){
   const day=s=>Date.parse(String(s)+'T00:00:00Z');
   if(row.first_date&&row.last_date&&Number.isFinite(day(row.first_date))&&Number.isFinite(day(row.last_date))){row.span_days=Math.round((day(row.last_date)-day(row.first_date))/86400000);outcome.span_days='widened';}
   else{row.span_days=null;outcome.span_days='blank';}
  }
  return {row,outcome};
 }
 function combine(rows,dims,columns=[]){
  const groups=new Map();
  for(const row of rows){const key=JSON.stringify(dims.map(d=>row[d]??null));if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
  if(groups.size===rows.length)return {rows,combined:0,outcomes:{}};
  let combined=0;const outcomes={};
  const out=[...groups.values()].map(list=>{if(list.length===1)return list[0];combined+=list.length-1;const merged=combineGroup(list,dims,columns);mergeOutcomes(outcomes,merged.outcome);return merged.row;});
  return {rows:out,combined,outcomes};
 }
 function mergeOutcomes(into,from){for(const[id,kind]of Object.entries(from))if(!into[id]||(RANK[kind]||1)>(RANK[into[id]]||1))into[id]=kind;return into;}
 function combineNote(combined,outcomes,columns=[]){
  if(!combined)return '';
  const of=kind=>Object.keys(outcomes||{}).filter(id=>outcomes[id]===kind).map(id=>name(id,columns));
  const list=a=>a.length<3?a.join(' and '):a.slice(0,-1).join(', ')+' and '+a.at(-1);
  const verb=(a,one,many)=>list(a)+(a.length===1?one:many);
  const parts=[];
  const added=of('added'),weighted=of('weighted'),pooled=of('pooled'),lowest=of('lowest'),highest=of('highest'),widened=of('widened'),blank=of('blank');
  if(added.length)parts.push(verb(added,' is added',' are added'));
  if(weighted.length)parts.push(verb(weighted,' is weighted by observation count',' are weighted by observation count'));
  if(pooled.length)parts.push(verb(pooled,' is pooled from each group’s count, mean and deviation',' are pooled from each group’s count, mean and deviation'));
  if(lowest.length)parts.push(verb(lowest,' takes the lowest value',' take the lowest value'));
  if(highest.length)parts.push(verb(highest,' takes the highest value',' take the highest value'));
  if(widened.length)parts.push(verb(widened,' covers the combined date span',' cover the combined date span'));
  if(blank.length)parts.push(verb(blank,' cannot be rebuilt from group summaries, so a combined mark leaves it blank',' cannot be rebuilt from group summaries, so a combined mark leaves them blank'));
  return combined+' duplicate group'+(combined===1?' was':'s were')+' combined because a queried field is not drawn'+(parts.length?': '+parts.join('; '):'')+'.';
 }
 function validate(rows,spec){
  if(!Array.isArray(rows))throw new Error('Chart data must be a list of aggregate rows.');
  if(!['bar','line','area','scatter','table',...V3_TYPES].includes(spec.type))throw new Error('Choose a supported chart type.');
  // Marks v3: every card draws one of the plotted measures, and two measures place Polygon and Density on two axes.
  if(spec.type==='cards'&&(!Array.isArray(spec.cards)||!spec.cards.length||spec.cards.some(c=>!c||!spec.measures.includes(c.measure)||(c.color&&!(spec.dimensions||[]).includes(c.color)))))throw new Error('Each marks card draws one of the plotted measures.');
  if(['polygon','density'].includes(spec.type)&&spec.layout==='scatter'&&spec.measures.length<2)throw new Error('Two measures place these marks on two axes.');
  if(!spec.measures?.length)throw new Error('Add a measure to build a chart.');
  if(!Array.isArray(spec.measures)||spec.measures.some(id=>typeof id!=='string')||new Set(spec.measures).size!==spec.measures.length)throw new Error('Choose distinct named measures.');
  if(spec.dimensions!=null&&(!Array.isArray(spec.dimensions)||spec.dimensions.some(id=>typeof id!=='string')||new Set(spec.dimensions).size!==spec.dimensions.length))throw new Error('Choose distinct named dimensions.');
  if(spec.color&&!(spec.dimensions||[]).includes(spec.color))throw new Error('The Colour field must be included in the queried dimensions.');
  // r3 (Paul: "nothing is logically constrained"): a scatter takes two or more
  // measures ([0] x, [1] y, [2] size when spec.size is absent, the rest in the
  // tooltip); Size is drawn on bars, lines and areas too; a line or area whose
  // first dimension is not a time field is drawn on category positions
  // (spec.categorical); Colour may repeat the time axis.
  if(spec.type==='scatter'&&spec.measures.length<2)throw new Error('Scatter needs exactly two measures; each point represents the selected dimensions.');
  if(spec.size&&rows.some(row=>row[spec.size]!=null&&(typeof row[spec.size]!=='number'||!Number.isFinite(row[spec.size]))))throw new Error('Size must use a numeric source measure.');
  if(spec.colorMeasure&&rows.some(row=>row[spec.colorMeasure]!=null&&(typeof row[spec.colorMeasure]!=='number'||!Number.isFinite(row[spec.colorMeasure]))))throw new Error('A Colour measure must be a numeric source measure.');
  if(rows.some(row=>!row||typeof row!=='object'||Array.isArray(row)))throw new Error('Each chart row must be an aggregate record.');
  for(const row of rows)for(const id of spec.measures){if(row[id]!=null&&!(spec.type==='table'&&spec.columns?.some(c=>c.id===id&&c.unit==='date'))&&(typeof row[id]!=='number'||!Number.isFinite(row[id])))throw new Error('The query returned a nonnumeric measure.');}
 }
 function extent(values,zero=true){let lo=Infinity,hi=-Infinity;for(const value of values)if(typeof value==='number'&&Number.isFinite(value)){lo=Math.min(lo,value);hi=Math.max(hi,value);}if(lo===Infinity)return[0,1];if(zero){lo=Math.min(0,lo);hi=Math.max(0,hi);}if(lo===hi){const pad=Math.abs(lo)*.1||1;lo-=zero&&lo===0?0:pad;hi+=pad;}if(!Number.isFinite(hi-lo)||hi<=lo)throw new Error('The numeric range cannot be displayed safely.');return[lo,hi];}
 // Ordinals preserve real elapsed time and distinguish a missing period from zero.
 function timeValue(value,field){
  const text=String(value??'').trim();let match;
  if(field==='year'){if(!/^\d{4}$/.test(text))return null;const year=Number(text);return year>=1800&&year<=2200?year:null;}
  if(field==='cycle'){match=text.match(/^(?:(\d{4})[-–])?(\d{4})$/);if(!match)return null;const end=Number(match[2]);return end>=1800&&end<=2200&&end%2===0&&(!match[1]||Number(match[1])===end-1)?end/2:null;}
  if(field==='month'){match=text.match(/^(\d{4})-(\d{2})$/);if(!match||Number(match[1])<1800||Number(match[1])>2200||Number(match[2])<1||Number(match[2])>12)return null;return Number(match[1])*12+Number(match[2])-1;}
  if(field==='quarter'){match=text.match(/^(\d{4})-Q([1-4])$/);return match&&Number(match[1])>=1800&&Number(match[1])<=2200?Number(match[1])*4+Number(match[2])-1:null;}
  if(field==='date'){if(!/^\d{4}-\d{2}-\d{2}$/.test(text))return null;const date=new Date(text+'T00:00:00Z');return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===text?date.getTime()/86400000:null;}
  return null;
 }
 function timeline(rows,spec,measure){
  const time=spec.dimensions[0],other=spec.dimensions.slice(1),groups=new Map(),ticks=new Map();let excluded=0;
  for(const row of rows){const at=timeValue(row[time],time);if(at==null){excluded++;continue;}ticks.set(at,String(row[time]));const key=JSON.stringify(other.map(d=>row[d]??null));if(!groups.has(key))groups.set(key,{key,label:label(row,other),rows:[]});groups.get(key).rows.push({row,at});}
  const series=[...groups.values()].sort((a,b)=>a.key.localeCompare(b.key));
  for(const s of series){s.rows.sort((a,b)=>a.at-b.at);s.segments=[];let segment=[],previous=null;const flush=()=>{if(segment.length)s.segments.push(segment);segment=[];};for(const item of s.rows){if(previous===item.at)throw new Error('The timeline has duplicate aggregate groups. Include every queried dimension before drawing it.');if(previous!=null&&item.at!==previous+1)flush();if(item.row[measure]==null)flush();else segment.push(item);previous=item.at;}flush();}
  return {series,ticks:[...ticks].sort((a,b)=>a[0]-b[0]),excluded};
 }
 const TIME_FIELDS=['cycle','date','year','quarter','month'];
 // The timeline render() draws (V01, V07). timeline() above still refuses a
 // repeated group when called directly; the drawing sums the repeats and says
 // so. A categorical axis places the first dimension's members in row order.
 function drawnTimeline(rows,spec,measure,categorical){
  const time=spec.dimensions?.[0],other=(spec.dimensions||[]).slice(1),groups=new Map(),ticks=new Map(),order=new Map(),repeats=[];let excluded=0,merged=0;
  for(const row of rows){
   let at;
   if(categorical){const text=time==null?'Selected population':row[time]==null||row[time]===''?'Unknown':String(row[time]);if(!order.has(text))order.set(text,order.size);at=order.get(text);ticks.set(at,text);}
   else{at=timeValue(row[time],time);if(at==null){excluded++;continue;}ticks.set(at,String(row[time]));}
   const key=JSON.stringify(other.map(d=>row[d]??null));if(!groups.has(key))groups.set(key,{key,label:label(row,other),rows:[],byAt:new Map()});
   const s=groups.get(key),seen=s.byAt.get(at);
   if(seen){merged++;if(!seen.list){seen.list=[seen.row];repeats.push(seen);}seen.list.push(row);continue;}
   const item={row,at};s.byAt.set(at,item);s.rows.push(item);
  }
  // F2: each repeated group is combined statistic by statistic (combineGroup).
  const outcomes={};for(const item of repeats){const combined=combineGroup(item.list,spec.dimensions||[],spec.columns||[]);item.row=combined.row;mergeOutcomes(outcomes,combined.outcome);delete item.list;}
  const series=[...groups.values()].sort((a,b)=>a.key.localeCompare(b.key));
  for(const s of series){delete s.byAt;s.rows.sort((a,b)=>a.at-b.at);s.segments=[];let segment=[],previous=null;const flush=()=>{if(segment.length)s.segments.push(segment);segment=[];};
   for(const item of s.rows){if(!categorical&&previous!=null&&item.at!==previous+1)flush();if(item.row[measure]==null){if(!categorical)flush();}else segment.push(item);previous=item.at;}flush();}
  return {series,ticks:[...ticks].sort((a,b)=>a[0]-b[0]),excluded,merged,outcomes};
 }
 const scatterPairs=(rows,measures)=>rows.filter(row=>measures.every(id=>typeof row[id]==='number'&&Number.isFinite(row[id])));
 /* Marks v3 (lane M, boards #50-#52, 2026-09-25). Paul's Tableau shape palette:
    eleven shapes, each filled and outline ('-open'), drawn as one path each so
    every renderer places them the same way. r is the circle radius the shape
    replaces; the others are scaled to about the same ink. */
 const GLYPH_BASES=['circle','square','plus','times','asterisk','diamond','triangle-up','triangle-down','triangle-left','triangle-right','star'];
 const GLYPH_IDS=[...GLYPH_BASES,...GLYPH_BASES.map(g=>g+'-open')];
 const V3_TYPES=['polygon','density','cards'];
 const f2=v=>Number(v.toFixed(2));
 function glyphPoints(base,r){
  const turn=(pts,deg)=>{const a=deg*Math.PI/180,c=Math.cos(a),s=Math.sin(a);return pts.map(([x,y])=>[x*c-y*s,x*s+y*c]);};
  if(base==='square'){const s=r*.886;return [[-s,-s],[s,-s],[s,s],[-s,s]];}
  if(base==='diamond'){const s=r*1.2;return [[0,-s],[s*.8,0],[0,s],[-s*.8,0]];}
  if(base==='plus'||base==='times'){const w=r*.34,q=r*1.05,pts=[[-w,-q],[w,-q],[w,-w],[q,-w],[q,w],[w,w],[w,q],[-w,q],[-w,w],[-q,w],[-q,-w],[-w,-w]];return base==='times'?turn(pts,45):pts;}
  if(base==='asterisk'){
   // Six arms of half-width w; between two arms the edges meet on the
   // bisector, 2w from the centre (w / sin 30 degrees).
   const w=r*.2,q=r*1.1,pts=[];
   for(let k=0;k<6;k++){const a=(k*60-90)*Math.PI/180,u=[Math.cos(a),Math.sin(a)],n=[-u[1],u[0]];
    pts.push([q*u[0]-w*n[0],q*u[1]-w*n[1]],[q*u[0]+w*n[0],q*u[1]+w*n[1]],[2*w*Math.cos(a+Math.PI/6),2*w*Math.sin(a+Math.PI/6)]);}
   return pts;
  }
  if(base==='star'){const pts=[];for(let k=0;k<10;k++){const a=(k*36-90)*Math.PI/180,rad=k%2?r*.5:r*1.2;pts.push([rad*Math.cos(a),rad*Math.sin(a)]);}return pts;}
  if(base.startsWith('triangle-')){const s=r*1.25,up=[[0,-s],[s*.95,s*.62],[-s*.95,s*.62]];return turn(up,{up:0,right:90,down:180,left:270}[base.slice(9)]||0);}
  return null;
 }
 // The path of one shape centred on (cx, cy). An unknown id draws a circle.
 function glyphD(id,cx,cy,r){
  const base=String(id||'circle').replace(/-open$/,''),pts=glyphPoints(base,r);
  if(!pts)return `M${f2(cx-r)},${f2(cy)} a${f2(r)},${f2(r)} 0 1,0 ${f2(2*r)},0 a${f2(r)},${f2(r)} 0 1,0 ${f2(-2*r)},0 Z`;
  return pts.map(([x,y],i)=>(i?'L':'M')+f2(cx+x)+','+f2(cy+y)).join(' ')+' Z';
 }
 const glyphOpen=id=>/-open$/.test(String(id||''));
 // The shape each member of a Shape field takes: its chosen one, else the
 // palette in the members' own order.
 function glyphFor(spec,member,members){
  const chosen=spec.shapeMap&&spec.shapeMap[member];
  if(chosen&&GLYPH_IDS.includes(chosen))return chosen;
  return GLYPH_IDS[Math.max(0,members.indexOf(member))%GLYPH_IDS.length];
 }
 /* A card's Opacity, Border and Halo (the Colour popover, as Tableau's), laid
    over any renderer's drawing after it is drawn, so every mark type takes
    them the same way. Automatic is the renderer's own drawing. A mark is an
    element classed plot-mark, or a shape carrying its own tooltip <title>; a
    line is a stroked, unfilled path at least 2 wide. Legends, reference lines
    and invisible hover targets are left alone. */
 const STYLE_INK='#202522';
 // A group's or a style slot's colour: the theme's twelve categorical colours
 // (theme.js categoryColour) when the theme has them, else the theme palette.
 const slotColour=i=>(root.MentatTheme?.categoryColour||(k=>palette[k%palette.length]))(i);
 function styleColour(value){
  if(value==='ink')return STYLE_INK;
  if(value==='white')return '#ffffff';
  const slot=/^c(\d{1,2})$/.exec(String(value||''));
  return slot?slotColour(Number(slot[1])-1):null;
 }
 const GEOMETRY=['d','x','y','width','height','rx','ry','cx','cy','r','points','x1','y1','x2','y2','transform'];
 function styleMarks(root,style){
  if(!root||!style)return root;
  const opacity=Number.isFinite(Number(style.opacity))&&Number(style.opacity)<100?Math.max(0,Number(style.opacity))/100:null;
  const border=style.border&&style.border!=='automatic'?style.border:null;
  const halo=style.halo&&!['automatic','none'].includes(style.halo)?styleColour(style.halo):null;
  if(opacity==null&&!border&&!halo)return root;
  const attr=(node,key)=>node.getAttribute?node.getAttribute(key):null;
  const found=[];
  const walk=(parent,skip)=>{for(const child of [...(parent.children||[])]){
   const tag=String(child.tagName||'').toLowerCase();
   const off=skip||attr(child,'data-legend')!=null||attr(child,'data-reference')!=null||attr(child,'data-halo')!=null;
   if(!off&&attr(child,'data-density-layer')!=null){found.push({parent,child,kind:'layer'});continue;}
   if(!off&&attr(child,'data-hover-target')==null){
    const titled=(child.children||[]).some?.(c=>String(c.tagName||'').toLowerCase()==='title');
    const markLike=String(attr(child,'class')||'').split(/\s+/).includes('plot-mark')||(['rect','circle','path','polygon','ellipse'].includes(tag)&&titled);
    const lineLike=!markLike&&['path','polyline','line'].includes(tag)&&(tag==='line'||attr(child,'fill')==='none')&&attr(child,'stroke')&&attr(child,'stroke')!=='none'&&Number(attr(child,'stroke-width')||1)>=2;
    if(markLike||lineLike)found.push({parent,child,kind:markLike?'mark':'line'});
   }
   if(tag!=='title')walk(child,off);
  }};
  walk(root,false);
  for(const {parent,child,kind} of found){
   if(opacity!=null)child.setAttribute('opacity',String(Number(((Number(attr(child,'opacity'))||1)*opacity).toFixed(3))));
   if(border&&kind==='mark'&&attr(child,'fill')!=='none'){
    if(border==='none')child.setAttribute('stroke','none');
    else{child.setAttribute('stroke',styleColour(border));child.setAttribute('stroke-width','1.5');}
   }
   if(halo&&kind!=='layer'&&typeof parent.insertBefore==='function'){
    const doc=root.ownerDocument||(typeof document!=='undefined'?document:null);if(!doc)continue;
    const ring=doc.createElementNS('http://www.w3.org/2000/svg',String(child.tagName).toLowerCase());
    for(const key of GEOMETRY){const value=attr(child,key);if(value!=null)ring.setAttribute(key,value);}
    ring.setAttribute('fill','none');ring.setAttribute('stroke',halo);
    ring.setAttribute('stroke-width',String((kind==='line'?Number(attr(child,'stroke-width'))||2:1)+4));
    ring.setAttribute('stroke-linejoin','round');ring.setAttribute('stroke-linecap','round');
    ring.setAttribute('data-halo','true');ring.setAttribute('pointer-events','none');
    if(opacity!=null)ring.setAttribute('opacity',String(opacity));
    parent.insertBefore(ring,child);
   }
  }
  return root;
 }
 const setStyle=(node,key,value)=>{if(!node.style)node.style={};node.style[key]=value;};
 /* A text table (Text mark) that draws its encodings: Colour as a swatch per
    member or a tint by measure, Size as the value's type size, Label as its own
    column, Tooltip on hover. Used only when one is placed; without any the
    table is today's. */
 function tableV3(container,rows,spec,onSelect){
  const columns=spec.columns||[],dims=spec.dimensions||[],measures=spec.measures;
  const extra=[...new Set([...(spec.labelFields||[]),...(spec.size?[spec.size]:[]),...(spec.colorMeasure?[spec.colorMeasure]:[])])].filter(id=>!dims.includes(id)&&!measures.includes(id));
  const shown=[...dims,...measures,...extra],valued=[...measures,...extra];
  const isDim=id=>dims.includes(id)||columns.some(c=>c.id===id&&(c.role==='dimension'||c.type==='category'));
  const valueText=(row,id)=>isDim(id)?String(row[id]??'Unknown'):format(row[id],id,columns);
  const members=spec.color?[...new Set(rows.map(r=>String(r[spec.color]??'Unknown')))].sort((a,b)=>a.localeCompare(b)):[];
  const heatValues=spec.colorMeasure?rows.map(r=>r[spec.colorMeasure]).filter(finite):[],heat=heatValues.length?[Math.min(...heatValues),Math.max(...heatValues)]:null;
  const maxSize=spec.size?Math.max(1e-9,...rows.map(r=>Math.abs(Number(r[spec.size])||0))):0;
  const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
  table.setAttribute('data-encoded','true');
  for(const id of shown){const th=document.createElement('th');th.textContent=name(id,columns)+(extra.includes(id)&&(spec.labelFields||[]).includes(id)?' (label)':'');tr.append(th);}head.append(tr);table.append(head);
  const body=document.createElement('tbody');
  for(const row of rows){const line=document.createElement('tr');line.className='selectable';line.tabIndex=0;line.setAttribute('aria-label','Inspect '+label(row,dims));
   const tip=[label(row,dims),...valued.map(id=>name(id,columns)+': '+valueText(row,id)),...(spec.tooltip||[]).filter(id=>!valued.includes(id)).map(id=>name(id,columns)+': '+valueText(row,id))];
   line.title=tip.join('\n');line.setAttribute('data-tooltip',tip.join(' | '));
   const tint=heat&&finite(row[spec.colorMeasure])?(heat[1]>heat[0]?(row[spec.colorMeasure]-heat[0])/(heat[1]-heat[0]):1):null;
   const scale=spec.size&&finite(row[spec.size])?Math.sqrt(Math.abs(row[spec.size])/maxSize):null;
   shown.forEach((id,at)=>{const td=document.createElement('td'),value=valued.includes(id)?format(row[id],id,columns):row[id]??'Unknown';
    if(valued.includes(id)&&spec.labels===false){td.title=String(value);td.dataset.valueHidden='true';}else td.textContent=value;
    if(valued.includes(id))td.className='numeric';
    if(at===0&&spec.color){const swatch=document.createElement('span');swatch.className='plot-swatch';swatch.setAttribute('aria-hidden','true');setStyle(swatch,'backgroundColor',slotColour(members.indexOf(String(row[spec.color]??'Unknown'))));td.prepend?td.prepend(swatch):td.append(swatch);td.setAttribute('data-colour',String(row[spec.color]??'Unknown'));}
    if(tint!=null&&measures.includes(id)){setStyle(td,'backgroundColor','rgba(87, 68, 123, '+(.1+.55*tint).toFixed(3)+')');td.setAttribute('data-shade',tint.toFixed(3));}
    if(scale!=null&&measures.includes(id)){setStyle(td,'fontSize',(12+8*scale).toFixed(1)+'px');td.setAttribute('data-size',scale.toFixed(3));}
    line.append(td);});
   line.onclick=()=>onSelect?.(row);line.onkeydown=e=>{if(e.key==='Enter')onSelect?.(row);};body.append(line);}
  table.append(body);container.append(table);return null;
 }
 // Path (Line, Area): the series of a timeline regrouped without the Path field
 // and joined in its order, as Tableau's Path shelf orders a line's marks.
 function pathSeries(list,dims,path,m){
  const keyDims=dims.slice(1).filter(d=>d!==path),groups=new Map();
  for(const s of list)for(const item of s.rows){const key=JSON.stringify(keyDims.map(d=>item.row[d]??null));
   if(!groups.has(key))groups.set(key,{key,label:label(item.row,keyDims),rows:[]});groups.get(key).rows.push(item);}
  const cmp=(a,b)=>{const x=a.row[path],y=b.row[path];if(finite(x)&&finite(y))return x-y||a.at-b.at;
   return String(x??'').localeCompare(String(y??''),undefined,{numeric:true})||a.at-b.at;};
  return [...groups.values()].sort((a,b)=>a.key.localeCompare(b.key)).map(g=>{g.rows.sort(cmp);g.segments=[g.rows.filter(item=>item.row[m]!=null)].filter(s=>s.length);return g;});
 }
 function render(container,rows,spec,onSelect){
  validate(rows,spec);container.replaceChildren();
  const columns=spec.columns||[],dims=spec.dimensions||[],measures=spec.measures;
  if(!rows.length){const p=document.createElement('p');p.className='empty-view';p.textContent='No records match these filters. Try another cycle or accounting basis.';container.append(p);return null;}
  if(spec.type==='table'&&(spec.size||spec.colorMeasure||spec.color||(spec.labelFields||[]).length||(spec.tooltip||[]).length))return tableV3(container,rows,spec,onSelect);
  if(spec.type==='table'){const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');for(const id of [...dims,...measures]){const th=document.createElement('th');th.textContent=name(id,columns);tr.append(th);}head.append(tr);table.append(head);const body=document.createElement('tbody');for(const row of rows){const tr=document.createElement('tr');tr.className='selectable';tr.tabIndex=0;tr.setAttribute('aria-label','Inspect '+label(row,dims));for(const id of [...dims,...measures]){const td=document.createElement('td'),shownValue=measures.includes(id)?format(row[id],id,columns):row[id]??'Unknown';if(measures.includes(id)&&spec.labels===false){td.title=String(shownValue);td.dataset.valueHidden='true';}else td.textContent=shownValue;if(measures.includes(id))td.className='numeric';tr.append(td);}tr.onclick=()=>onSelect?.(row);tr.onkeydown=e=>{if(e.key==='Enter')onSelect?.(row);};body.append(tr);}table.append(body);container.append(table);return null;}
  // V01: a line or area whose first dimension is not a time field, or any line
  // with spec.categorical, is drawn on category positions in row order.
  const timed=['line','area'].includes(spec.type),categorical=timed&&(spec.categorical===true||!TIME_FIELDS.includes(dims[0]));
  const timelines=timed?measures.map(m=>drawnTimeline(rows,spec,m,categorical)):[],merged=timelines.reduce((n,t)=>Math.max(n,t.merged),0);
  // A scatter plots [0] against [1]; [2] sizes the points when spec.size is absent (V07).
  const sizeId=spec.size||(spec.type==='scatter'&&measures.length>2?measures[2]:null);
  const plotted=spec.type==='scatter'?scatterPairs(rows,measures.slice(0,2)):timed&&!categorical?rows.filter(row=>timeValue(row[dims[0]],dims[0])!=null):rows;
  if(!plotted.length){const note=document.createElement('p');note.className='empty-view';note.textContent=timed?'No valid dated groups remain. Use Table view to inspect missing or invalid dates.':'No complete measure pairs remain. Use Table view to inspect missing values.';container.append(note);return null;}
  if(plotted.length<rows.length){const note=document.createElement('p');note.className='plot-note';note.textContent=(rows.length-plotted.length)+(timed?' groups have missing or invalid dates.':' groups are missing one or both measures.')+' They remain available in Table view.';container.append(note);}
  if(merged){const note=document.createElement('p');note.className='plot-note';note.textContent=combineNote(merged,timelines.reduce((all,t)=>mergeOutcomes(all,t.outcomes||{}),{}),columns);container.append(note);}
  // V02: bars stacked by Colour, one row per group (labelDimensions, or every
  // dimension except Colour). V03: measures[1..] on a right-hand axis.
  const stacking=spec.type==='bar'&&spec.color&&['stacked','percent'].includes(spec.stack)?spec.stack:null;
  const stackDims=spec.labelDimensions||dims.filter(d=>d!==spec.color),stackKeys=stacking?[...new Set(rows.map(r=>label(r,stackDims)))]:null;
  const dual=spec.dual===true&&['bar','line'].includes(spec.type)&&measures.length>=2;
  const barCount=stackKeys?stackKeys.length:rows.length;
  const svg=document.createElementNS(NS,'svg');svg.setAttribute('class','plot-svg');svg.setAttribute('role','group');svg.setAttribute('aria-label',spec.type+' chart. Select a mark to inspect its source records. The Table view provides all displayed values.');
  // spec.barPitch (Chart Studio, S1): a taller row per bar; 29 otherwise.
  let pitch=spec.type==='bar'&&Number(spec.barPitch)>0?Number(spec.barPitch):29;
  // spec.width (bars only): draw at the container's own width, one unit per
  // CSS pixel; spec.barLeft is then the label column. Otherwise 1000 units.
  const native=spec.type==='bar'&&!dual&&Number(spec.width)>0;let barLeft=native&&Number(spec.barLeft)>0?Number(spec.barLeft):255;
  if(native)svg.setAttribute('data-native','');
  const width=native?Math.max(560,Math.round(Number(spec.width))):1000,panels=spec.type==='scatter'||dual||V3_TYPES.includes(spec.type)?1:measures.length;let barRight=width-(native?110:125),panelH=spec.type==='bar'&&!dual?Math.max(spec.panelTitles===false?300:350,barCount*pitch+(spec.panelTitles===false?70:105)):dual||spec.type==='cards'?500:420;
  // Lane F2b (boards #50 and #51, 2026-09-26): four fields, each drawn only when a spec asks for it, so a
  // spec without them draws byte for byte as before (RULE 0): plotHeight, legendPlacement 'right',
  // labelCollide and endLabels. They apply to a scatter and to a one-panel line or area. plotHeight: the
  // panel takes that many units of height (420 to 900), so the plot fills the chart box it is drawn in.
  const f2bPlot=(spec.type==='scatter'||timed)&&!dual&&panels===1;
  if(f2bPlot&&Number(spec.plotHeight)>0){panelH=Math.max(420,Math.min(900,Math.round(Number(spec.plotHeight))));svg.setAttribute('data-plot-fit',String(panelH));}
  const plotBottom=f2bPlot?panelH-75:345;
  svg.setAttribute('viewBox',`0 0 ${width} ${panelH*panels}`);svg.setAttribute('width',width);svg.setAttribute('height',panelH*panels);svg.setAttribute('font-family','Arial, sans-serif');svg.setAttribute('font-size','15');
  const title=document.createElementNS(NS,'title');title.textContent=spec.title||'Contribution analysis';svg.append(title);
  const node=(tag,attrs={},text,parent=svg)=>{const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!=null)n.textContent=text;parent.append(n);return n;};
  const context=document.createElement('canvas').getContext?.('2d');
  const measure=(value,size=15)=>{if(!context)return String(value).length*size;context.font=`${size}px Arial, sans-serif`;return context.measureText(String(value)).width;};
  const fittingLength=(chars,available,size)=>{let lo=0,hi=chars.length;while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(measure(chars.slice(0,mid).join(''),size)<=available)lo=mid;else hi=mid-1;}return lo;};
  const fit=(value,available,size=15)=>{const text=String(value);if(measure(text,size)<=available)return text;const chars=Array.from(text);return chars.slice(0,fittingLength(chars,Math.max(0,available-measure('…',size)),size)).join('')+'…';};
  const wrap=(value,available,size=15)=>{let chars=Array.from(String(value).replace(/\s+/g,' ').trim());const lines=[];while(chars.length){let count=Math.max(1,fittingLength(chars,available,size));if(count<chars.length){const space=chars.slice(0,count).lastIndexOf(' ');if(space>0)count=space;}lines.push(chars.slice(0,count).join(''));chars=Array.from(chars.slice(count).join('').trimStart());}return lines.length?lines:[''];};
  const fittedText=(attrs,value,available,parent)=>{const t=node('text',attrs,fit(value,available,attrs['font-size']||15),parent),hint=conduitNote(String(value));node('title',{},hint?value+'\n'+hint:value,t);return t;};
  const background=node('rect',{width,height:panelH*panels,fill:'#ffffff'});
  // spec.colorMap (value -> colour) pins a series to a colour and its legend
  // order, as Chart Studio's compared scopes do with --scope-1/2/3; without it
  // series take the palette in alphabetical order, as before.
  const mapped=spec.color&&spec.colorMap?Object.keys(spec.colorMap):null;
  const seen=[...new Set(plotted.map(r=>spec.color?String(r[spec.color]??'Unknown'):'Giving'))];
  const colorValues=mapped?[...mapped.filter(v=>seen.includes(v)),...seen.filter(v=>!mapped.includes(v)).sort((a,b)=>a.localeCompare(b))]:seen.sort((a,b)=>a.localeCompare(b));
  // Conduit (theme.js categoryColours) has a colour of its own and takes no slot, so the other members keep the colours they have without it.
  const slotColours=typeof root.MentatTheme?.categoryColours==='function'?root.MentatTheme.categoryColours(colorValues):colorValues.map((_,i)=>categoryColour(i));
  const colorOf=value=>(mapped&&spec.colorMap[value])||slotColours[Math.max(0,colorValues.indexOf(value))]||categoryColour(0);
  const color=row=>colorOf(spec.color?String(row[spec.color]??'Unknown'):'Giving');
  // The hover note a Conduit value carries wherever the recipient category is drawn (theme.js categoryNote).
  const conduitNote=value=>spec.color==='recipient_category'||dims.includes('recipient_category')?(root.MentatTheme?.categoryNote?.(value)||''):'';
  const markNote=row=>[...new Set([spec.color,...dims].filter(id=>id==='recipient_category'))].map(id=>conduitNote(row[id])).find(Boolean)||'';
  const encodedFields=[...new Set([...measures,...(spec.tooltip||[]),...(spec.labelFields||[]),...(sizeId?[sizeId]:[]),...(spec.colorMeasure?[spec.colorMeasure]:[])])];
  const valueText=(row,id)=>dims.includes(id)||columns.some(c=>c.id===id&&(c.role==='dimension'||c.type==='category'||c.type==='date'))?String(row[id]??'Unknown'):format(row[id],id,columns);
  const labelText=(row,fallback)=>spec.labelFields?.length?spec.labelFields.map(id=>valueText(row,id)).join(' / '):fallback;
  // Money at a bar's end reads in whole dollars (S1: "$10,892,411").
  const barEndValue=(v,id)=>isMoney(id,columns)&&Number.isFinite(Number(v))?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(Number(v)/100):format(v,id,columns,true);
  // The legend's layout, below the panels: one plan draws it and sizes the fit.
  const legendPlan=values=>{let x=24,y=28,rowHeight=28;const items=[];for(const [i,value]of values.entries()){const lines=wrap(value,width-78),itemWidth=30+Math.max(...lines.map(line=>measure(line)));if(x>24&&x+itemWidth>width-24){x=24;y+=rowHeight;rowHeight=28;}items.push({i,value,lines,x,y});rowHeight=Math.max(rowHeight,lines.length*20+8);x+=itemWidth+14;}return {items,height:y+rowHeight};};
  // Chart Studio sizing, native bars only. spec.barLeftMax: the label column
  // fits the longest group label (up to that many px) and the right margin
  // the longest value label plus its 8px gap and 8px pad, so the plot takes
  // the width it is given and keeps its round ticks. spec.fitHeight with
  // spec.barPitchMin: the pitch shrinks toward that minimum until the panels,
  // their axes and the legend fit that height (compared scopes multiply bars).
  // spec.barPitchMax (Chart Studio with a pane put away, ruling c): the pitch
  // may also grow toward that maximum, so the bars fill the height given.
  if(native&&Number(spec.barLeftMax)>0){
   const labelWidth=Math.max(0,...rows.map(r=>measure(label(r,spec.labelDimensions||dims),13.5)));
   barLeft=Math.max(60,Math.min(Number(spec.barLeftMax),Math.ceil(labelWidth)+22));
   const valueWidth=spec.labels?Math.max(0,...measures.flatMap(m=>rows.filter(r=>r[m]!=null&&r[m]>=0).map(r=>measure(labelText(r,barEndValue(r[m],m)),13.5)))):0;
   barRight=width-Math.max(36,Math.ceil(valueWidth)+16);
  }
  if(native&&Number(spec.fitHeight)>0&&(Number(spec.barPitchMin)>0||Number(spec.barPitchMax)>0)&&barCount){
   const chrome=spec.panelTitles===false?70:105,room=(Number(spec.fitHeight)-(spec.color?legendPlan(colorValues).height:0))/panels;
   const least=Number(spec.barPitchMin)>0?Math.min(Number(spec.barPitchMin),pitch):pitch,most=Math.max(pitch,Number(spec.barPitchMax)||0);
   pitch=Math.max(least,Math.min(most,Math.floor((room-chrome)/barCount)));
   panelH=Math.max(Math.min(spec.panelTitles===false?300:350,Math.floor(room)),barCount*pitch+chrome);
   svg.setAttribute('viewBox',`0 0 ${width} ${panelH*panels}`);svg.setAttribute('height',panelH*panels);background.setAttribute('height',panelH*panels);
  }
  const maxSize=sizeId?plotted.reduce((max,row)=>Math.max(max,Math.abs(row[sizeId]||0)),1):1;
  // Size (V04): a bar's thickness and a line's width follow |size| / max too.
  const sizeFactor=row=>sizeId&&typeof row[sizeId]==='number'?.35+.65*Math.sqrt(Math.abs(row[sizeId])/maxSize):1;
  // A Colour measure (V09): one hue, its opacity rising with the value.
  const heatValues=spec.colorMeasure?plotted.map(r=>r[spec.colorMeasure]).filter(v=>typeof v==='number'&&Number.isFinite(v)):[];
  const heat=heatValues.length?[Math.min(...heatValues),Math.max(...heatValues)]:null;
  const shade=row=>heat&&typeof row[spec.colorMeasure]==='number'?{fill:palette[0],'fill-opacity':.25+.75*(heat[1]>heat[0]?(row[spec.colorMeasure]-heat[0])/(heat[1]-heat[0]):1)}:null;
  // Per-panel value axes of the r3 layouts, so reference lines use the drawn scale.
  const axes=[];
  const mark=(tag,attrs,row,parent,keepRadius)=>{if(sizeId&&tag==='circle'&&!keepRadius)attrs={...attrs,r:3+12*Math.sqrt(Math.abs(row[sizeId]||0)/maxSize)};const note=markNote(row),detail=encodedFields.map(m=>`${name(m,columns)}: ${valueText(row,m)}`).join('\n')+(note?'\n'+note:'');const n=node(tag,{...attrs,class:'plot-mark',tabindex:0,role:'button','aria-label':label(row,dims)+'; '+detail},null,parent);node('title',{},label(row,dims)+'\n'+detail,n);n.addEventListener('click',()=>onSelect?.(row));n.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect?.(row);}});return n;};
  // Marks v3: a point drawn as a shape from the palette (Square, Shape), one per
  // member of a Shape field; without either it is today's circle.
  const shapeMembers=spec.shape?[...new Set(plotted.map(r=>String(r[spec.shape]??'Unknown')))].sort((a,b)=>a.localeCompare(b)):[];
  const glyphOf=row=>spec.shape?glyphFor(spec,String(row[spec.shape]??'Unknown'),shapeMembers):(spec.glyph||'');
  const pointMark=(row,cx,cy,base,paint,parent)=>{const id=glyphOf(row);if(!id)return mark('circle',{cx,cy,r:base,...paint,stroke:'white','stroke-width':1.4},row,parent);
   const rad=sizeId&&typeof row[sizeId]==='number'?3+12*Math.sqrt(Math.abs(row[sizeId])/maxSize):base;
   return mark('path',{d:glyphD(id,cx,cy,rad),...(glyphOpen(id)?{fill:'none',stroke:paint.fill,'stroke-width':1.8}:{...paint,stroke:'white','stroke-width':1.2}),'data-glyph':id},row,parent,true);};
  // spec.legendPlacement 'right' (#50, #51): the Colour, Size, Colour-measure and Shape legends are cards in a
  // column at the plot's right, inside the drawing so an export keeps them; the plot gives up that width.
  // Measured at 18: on screen the Studio sets chart text to 18px (workspace-polish.css).
  const T=18,legendRight=f2bPlot&&spec.legendPlacement==='right',legendCards=[];let rightCol=0,ends=null;
  if(legendRight){
   const series=spec.color?colorValues:timed&&dims.length>1?timelines[0].series.map(s=>s.label):[];
   if(series.length)legendCards.push({kind:'series',title:spec.color?name(spec.color,columns):dims.slice(1).map(d=>name(d,columns)).join(' / '),items:series.map((value,i)=>({text:value,fill:spec.color?colorOf(value):categoryColour(i),note:spec.color==='recipient_category'?conduitNote(value):''}))});
   if(sizeId&&spec.type==='scatter'){
    const nice=v=>{if(!(v>0))return 0;const mag=Math.pow(10,Math.floor(Math.log10(v))),n=v/mag;return (n>=5?5:n>=2?2:1)*mag;};
    const values=[...new Set([maxSize/20,maxSize/4,maxSize].map(nice))].filter(v=>v>0).sort((a,b)=>a-b);
    if(values.length)legendCards.push({kind:'size',title:name(sizeId,columns),items:values.map(v=>({text:format(v,sizeId,columns,true),r:3+12*Math.sqrt(v/maxSize)}))});
   }
   if(heat)legendCards.push({kind:'colour',title:name(spec.colorMeasure,columns),items:[{text:format(heat[0],spec.colorMeasure,columns,true),fill:palette[0],opacity:.25},{text:format(heat[1],spec.colorMeasure,columns,true),fill:palette[0],opacity:1}]});
   if(spec.shape&&shapeMembers.length&&spec.type==='scatter')legendCards.push({kind:'shape',title:name(spec.shape,columns),items:shapeMembers.map(member=>({text:member,glyph:glyphFor(spec,member,shapeMembers)}))});
   const lead=item=>item.r?2*Math.max(item.r,6)+10:24;
   const cardW=legendCards.length?Math.max(110,Math.min(230,Math.ceil(Math.max(...legendCards.map(c=>Math.max(measure(c.title,T),...c.items.map(i=>lead(i)+measure(i.text,T))))))+26)):0;
   const endW=spec.endLabels&&timed&&spec.color?Math.min(230,Math.ceil(Math.max(0,...colorValues.map(v=>measure(v,T))))+24):0;
   if(cardW||endW)rightCol=Math.max(cardW,endW)+36;
   let top=14;
   // A card taller than the room left keeps the rows that fit and says how many it left out.
   for(const c of legendCards){c.rows=c.items.map(i=>i.r?Math.max(24,2*i.r+8):24);
    const height=()=>40+c.rows.reduce((a,b)=>a+b,0)+(c.more?24:0);
    while(c.items.length>1&&top+height()>panelH-12){c.items.pop();c.rows.pop();c.more=(c.more||0)+1;}
    c.x=width-cardW-12;c.y=top;c.w=cardW;c.h=height();top+=c.h+10;}
  }
  // spec.labelCollide (#50, #51; Tableau's default): labels wait here and are placed after every mark, the
  // highest rank first - the extremes, then the largest marks, then the farthest from the middle. A label
  // that would overlap one already placed, a legend card or a line's name is left off; its value stays
  // in the mark's tooltip.
  const collide=(f2bPlot||spec.type==='cards')&&!!spec.labelCollide,waiting=[],taken=[],markBoxes=[];
  // own: a point inside the label's own mark (never in its way); alts: the other places the label may take.
  const wantLabel=(attrs,text,room,parent,rank,own,alts)=>{waiting.push({attrs,text,room,parent,rank,own,alts,order:waiting.length});};
  // A mark's box: a rect, a circle, or a glyph path (a circle glyph is drawn with arcs from its left edge).
  const pathBox=d=>{const p=(String(d).match(/-?\d+(?:\.\d+)?/g)||[]).map(Number);if(/a/i.test(d)&&p.length>=3)return {x:p[0],y:p[1]-p[2],w:2*p[2],h:2*p[2]};
   const xs=p.filter((_,i)=>i%2===0),ys=p.filter((_,i)=>i%2===1);return xs.length?{x:Math.min(...xs),y:Math.min(...ys),w:Math.max(...xs)-Math.min(...xs),h:Math.max(...ys)-Math.min(...ys)}:null;};
  const markBox=(tag,a)=>tag==='rect'?{x:Number(a.x),y:Number(a.y),w:Number(a.width),h:Number(a.height)}:tag==='circle'?{x:Number(a.cx)-Number(a.r),y:Number(a.cy)-Number(a.r),w:2*Number(a.r),h:2*Number(a.r)}:tag==='path'&&a.d?pathBox(a.d):null;
  const scatterRanks=(list,px,py,mx,my,spanX,spanY)=>{const rank=new Map(),n=Math.max(1,list.length),cx=list.reduce((a,r)=>a+px(r),0)/n,cy=list.reduce((a,r)=>a+py(r),0)/n;
   [[my,1],[mx,1],[my,-1],[mx,-1]].forEach(([id,sign],k)=>{let best=null;for(const r of list)if(best==null||sign*(r[id]-best[id])>0)best=r;if(best&&!rank.has(best))rank.set(best,40-k);});
   for(const r of list)if(!rank.has(r))rank.set(r,1+(sizeId&&finite(r[sizeId])?Math.sqrt(Math.abs(r[sizeId])/maxSize):0)+Math.hypot((px(r)-cx)/(spanX||1),(py(r)-cy)/(spanY||1)));
   return r=>rank.get(r)||1;};
  // Lane G1 (#51): an extreme's label may also sit beside its point - above or below, right or left.
  const sideSpots=(px,py,attrs)=>[[9,-8,'start'],[-9,-8,'end'],[9,19,'start'],[-9,19,'end']].map(([dx,dy,anchor])=>({...attrs,x:f2(px+dx),y:f2(py+dy),'text-anchor':anchor}));
  const lineRanks=(s,m)=>{const vals=s.rows.map(item=>item.row[m]).filter(finite),hi=vals.length?Math.max(...vals):0,lo=vals.length?Math.min(...vals):0,mean=vals.reduce((a,b)=>a+b,0)/Math.max(1,vals.length),span=hi-lo||1,last=((s.segments.at(-1)||[]).at(-1)||{}).row;
   return r=>r[m]===hi?30:r[m]===lo?28:r===last?26:1+Math.abs(r[m]-mean)/span;};
  for(let p=0;p<panels;p++){
   const g=node('g',{transform:`translate(0 ${p*panelH})`}),m=measures[p];
   // spec.panelTitles===false (Chart Studio, one measure): the canvas title
   // above the drawing already names the measure, so the panel skips its own.
   if(spec.panelTitles!==false&&spec.type!=='cards')node('text',{x:20,y:23,fill:'#202522','font-size':18,'font-weight':600},spec.type==='scatter'?`${name(measures[1],columns)} by ${name(measures[0],columns)}`:dual?`${name(measures[0],columns)} (left axis) · ${measures.slice(1).map(id=>name(id,columns)).join(', ')} (right axis)`:name(m,columns),g);
   if(spec.type==='bar'&&!dual){
    // Stacked (V02): each group's segments in Colour order, positives rightward
    // from zero and negatives leftward; 'percent' draws shares of the positive total.
    const stackOf=stacking?stackKeys.map(key=>{const members=rows.filter(r=>label(r,stackDims)===key).sort((a,b)=>colorValues.indexOf(String(a[spec.color]??'Unknown'))-colorValues.indexOf(String(b[spec.color]??'Unknown')));
     const total=members.reduce((sum,r)=>sum+(r[m]>0?r[m]:0),0);let pos=0,neg=0;
     const parts=members.filter(r=>r[m]!=null).map(r=>{const v=stacking==='percent'?(total?r[m]/total*100:0):r[m],start=v<0?neg:pos,end=start+v;if(v<0)neg=end;else pos=end;return {r,v,start,end};});
     return {key,parts,pos,neg,sum:members.reduce((sum,r)=>sum+(r[m]||0),0)};}):null;
    const left=barLeft,right=barRight,top=spec.panelTitles===false?20:55,bottom=panelH-43,[lo,hi]=stackOf?extent(stackOf.flatMap(s=>[s.pos,s.neg])):extent(rows.map(r=>r[m])),x=v=>left+(v-lo)/(hi-lo)*(right-left),zero=x(0);
    if(stackOf)axes[p]={values:stackOf.map(s=>stacking==='percent'?100:s.sum),lo,hi,bar:true};
    // spec.niceTicks (S1): round steps ("$2.0M") and the axis end, instead of
    // five equal fifths of the range. The last round step gives way to the
    // axis end only where their two labels would sit under 8px apart.
    const niceStep=span=>{const raw=span/6,mag=Math.pow(10,Math.floor(Math.log10(raw||1))),n=raw/mag;return (n<=1?1:n<=2?2:n<=2.5?2.5:n<=5?5:10)*mag;};
    let ticks=[0,1,2,3,4,5].map(t=>lo+(hi-lo)*t/5);
    const tickText=v=>stacking==='percent'?Number(v.toFixed(1))+'%':spec.niceTicks&&isMoney(m,columns)?(v===0?'$0':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',minimumFractionDigits:1,maximumFractionDigits:1}).format(v/100)):format(v,m,columns,true);
    if(spec.niceTicks&&hi>lo){const step=niceStep(hi-lo);ticks=[];for(let v=Math.ceil(lo/step)*step;v<=hi+step*1e-9;v+=step)ticks.push(v);if(lo<ticks[0])ticks.unshift(lo);const endGap=x(hi)-x(ticks.at(-1));if(endGap>=1){if(ticks.length>1&&endGap<(measure(tickText(ticks.at(-1)),native?13.5:15)+measure(tickText(hi),native?13.5:15))/2+8)ticks.pop();ticks.push(hi);}}
    for(const v of ticks){node('line',{x1:x(v),x2:x(v),y1:top-8,y2:bottom,stroke:spec.niceTicks&&v===0?'#8a8690':'#e8ebe6'},null,g);fittedText({x:x(v),y:panelH-17,'text-anchor':'middle',fill:'#68706b','font-size':native?13.5:15},tickText(v),110,g);}
    // spec.labelDimensions names a group once for a cluster of bars (one bar
    // per compared scope); the value label sits at the end of its own bar.
    const groupLabel=r=>label(r,spec.labelDimensions||dims);
    const barValue=v=>barEndValue(v,m);
    const barH=pitch===29?20:Math.round(pitch*.62),textY=pitch===29?14:Math.round(barH/2+5);
    // spec.seriesColor (Chart Studio, S1's teal): the fill of a single series.
    // Compared scopes (spec.color) keep their colorMap; S1 also draws $0 as a darker axis.
    const barFill=r=>spec.color||!spec.seriesColor?color(r):spec.seriesColor;
    if(stackOf)stackOf.forEach((s,i)=>{const y=top+i*pitch;fittedText({x:left-14,y:y+textY,'text-anchor':'end',fill:'#444c46','font-size':native?13.5:16,'data-label':'category'},s.key,left-(native?22:30),g);
     for(const part of s.parts)mark('rect',{x:Math.min(x(part.start),x(part.end)),y,width:Math.max(1,Math.abs(x(part.end)-x(part.start))),height:barH,fill:color(part.r),stroke:'#ffffff','stroke-width':1},part.r,g);
     if(spec.labels){const end=Math.max(x(s.pos),zero);fittedText({x:end+8,y:y+textY,'text-anchor':'start',fill:'#35483b','font-size':native?13.5:15,'data-label':'value'},stacking==='percent'?'100%':barValue(s.sum),Math.max(60,width-end-16),g);}});
    else rows.forEach((r,i)=>{const y=top+i*pitch,text=groupLabel(r);if(!spec.labelDimensions||!i||text!==groupLabel(rows[i-1]))fittedText({x:left-14,y:y+textY,'text-anchor':'end',fill:'#444c46','font-size':native?13.5:16,'data-label':'category'},text,left-(native?22:30),g);if(r[m]==null)return;const f=sizeFactor(r),h=f===1?barH:barH*f;mark('rect',{x:Math.min(zero,x(r[m])),y:f===1?y:y+(barH-h)/2,width:Math.max(1,Math.abs(x(r[m])-zero)),height:h,rx:2,...(shade(r)||{fill:barFill(r)})},r,g);if(spec.labels&&!native)fittedText({x:right+12,y:y+textY,'text-anchor':'start',fill:'#35483b','font-size':15,'data-label':'value'},labelText(r,format(r[m],m,columns,true)),width-right-28,g);else if(spec.labels){const end=x(r[m]),negative=r[m]<0;fittedText({x:negative?Math.max(left+4,end-8):end+8,y:y+textY,'text-anchor':negative?'end':'start',fill:'#35483b','font-size':native?13.5:15,'data-label':'value'},labelText(r,barValue(r[m])),Math.max(60,negative?end-left:width-end-16),g);}});
   }else if(dual){
    // Dual axis (V03): categories in row order across the panel; measures[0] on
    // the left axis as the chosen mark, measures[1..] on a right axis as
    // spec.dualMark ('line' by default, or 'bar' sharing the category slot).
    const left=110,right=890,top=60,bottom=420,catDims=dims.filter(d=>d!==spec.color),cats=[],catOf=new Map();
    for(const r of rows){const k=label(r,catDims);if(!catOf.has(k)){catOf.set(k,cats.length);cats.push(k);}}
    const n=cats.length,step=(right-left)/Math.max(1,n),cx=i=>left+step*(i+.5);
    const members=spec.color?colorValues:['Giving'],memberOf=r=>spec.color?String(r[spec.color]??'Unknown'):'Giving';
    const rightIds=measures.slice(1),dualMark=spec.dualMark==='bar'?'bar':'line';
    const [la,lb]=extent(rows.map(r=>r[measures[0]])),[ra,rb]=extent(rows.flatMap(r=>rightIds.map(id=>r[id])));
    const ly=v=>bottom-(v-la)/(lb-la)*(bottom-top),ry=v=>bottom-(v-ra)/(rb-ra)*(bottom-top);
    for(let i=0;i<=5;i++){const v=la+(lb-la)*i/5,w=ra+(rb-ra)*i/5;node('line',{x1:left,x2:right,y1:ly(v),y2:ly(v),stroke:'#e8ebe6'},null,g);
     fittedText({x:left-10,y:ly(v)+4,'text-anchor':'end',fill:categoryColour(0),'font-size':14,'data-axis':'left'},format(v,measures[0],columns,true),96,g);
     fittedText({x:right+10,y:ry(w)+4,'text-anchor':'start',fill:categoryColour(1),'font-size':14,'data-axis':'right'},format(w,rightIds[0],columns,true),96,g);}
    node('line',{x1:right,x2:right,y1:top,y2:bottom,stroke:'#8a8690'},null,g);
    const every=Math.max(1,Math.ceil(n/Math.max(1,Math.min(12,Math.floor((right-left)/90)))));
    const turnCats=spec.axisLabels==='all'&&every>1;
    cats.forEach((text,i)=>{if(turnCats)fittedText({x:cx(i),y:bottom+16,'text-anchor':'end',fill:'#68706b','font-size':12,transform:`rotate(-35 ${cx(i)} ${bottom+16})`,'data-axis-label':'all'},text,110,g);else if(i%every===0)fittedText({x:cx(i),y:bottom+24,'text-anchor':'middle',fill:'#68706b','font-size':14},text,Math.max(40,step*every-6),g);});
    const barIds=[...(spec.type==='bar'?[measures[0]]:[]),...(dualMark==='bar'?rightIds:[])],slots=Math.max(1,barIds.length*members.length),bw=Math.min(40,step*.8/slots);
    const drawBars=(id,scaleY,fillOf)=>{const k=barIds.indexOf(id);rows.forEach(r=>{if(r[id]==null)return;const i=catOf.get(label(r,catDims)),slot=k*members.length+Math.max(0,members.indexOf(memberOf(r))),x0=cx(i)-bw*slots/2+slot*bw;
      mark('rect',{x:x0,y:Math.min(scaleY(0),scaleY(r[id])),width:Math.max(1,bw-1),height:Math.max(1,Math.abs(scaleY(r[id])-scaleY(0))),fill:fillOf(r)},r,g);
      if(spec.labels)fittedText({x:x0+bw/2,y:Math.min(scaleY(0),scaleY(r[id]))-5,'text-anchor':'middle',fill:'#35483b','font-size':12,'data-label':'value'},labelText(r,format(r[id],id,columns,true)),90,g);});};
    const drawLine=(id,scaleY,strokeOf,dashed)=>{for(const member of members){const points=rows.filter(r=>memberOf(r)===member&&r[id]!=null).map(r=>({r,i:catOf.get(label(r,catDims))})).sort((a,b)=>a.i-b.i);if(!points.length)continue;const stroke=strokeOf(points[0].r);
      node('path',{d:points.map((q,k)=>(k?'L':'M')+cx(q.i)+','+scaleY(q.r[id])).join(' '),fill:'none',stroke,'stroke-width':2.5,...(dashed?{'stroke-dasharray':'6 4'}:{})},null,g);
      points.forEach(q=>{mark('circle',{cx:cx(q.i),cy:scaleY(q.r[id]),r:4.5,fill:stroke,stroke:'white','stroke-width':1.5},q.r,g,true);if(spec.labels)fittedText({x:cx(q.i),y:scaleY(q.r[id])-10,'text-anchor':'middle',fill:stroke,'font-size':12,'data-label':'value'},labelText(q.r,format(q.r[id],id,columns,true)),90,g);});}};
    const leftColour=r=>spec.color?color(r):categoryColour(0);
    if(spec.type==='bar')drawBars(measures[0],ly,leftColour);else drawLine(measures[0],ly,leftColour,false);
    rightIds.forEach((id,k)=>{const colour=r=>spec.color?color(r):categoryColour(k+1);if(dualMark==='bar')drawBars(id,ry,colour);else drawLine(id,ry,colour,!!spec.color);});
    node('text',{x:left,y:bottom+50,fill:'#59645c','font-size':13},`${name(measures[0],columns)}: left axis. ${rightIds.map(id=>name(id,columns)).join(', ')}: right axis${spec.color?' (dashed lines)':''}. Independent, labelled scales.`,g);
    axes[p]={values:rows.map(r=>r[measures[0]]),lo:la,hi:lb,at:ly,x1:left,x2:right,bar:false};
   }else if(spec.type==='polygon'||spec.type==='density'){
    // Marks v3 (#50-#52). Two measures place the marks on two axes (layout
    // 'scatter'); one measure places them along the first dimension ('axis').
    const scatterLayout=spec.layout==='scatter'&&measures.length>=2,left=100,right=935,top=65,bottom=345;
    let pts,xOf,yOf;
    if(scatterLayout){
     const [mx,my]=measures;pts=scatterPairs(rows,[mx,my]);
     const [xl,xh]=extent(pts.map(r=>r[mx]),false),[yl,yh]=extent(pts.map(r=>r[my]),false);
     xOf=r=>left+(r[mx]-xl)/(xh-xl)*(right-left);yOf=r=>bottom-(r[my]-yl)/(yh-yl)*(bottom-top);
     for(let i=0;i<=5;i++){const vx=xl+(xh-xl)*i/5,vy=yl+(yh-yl)*i/5,px=left+(vx-xl)/(xh-xl)*(right-left),py=bottom-(vy-yl)/(yh-yl)*(bottom-top);
      node('line',{x1:px,x2:px,y1:top,y2:bottom,stroke:'#e8ebe6'},null,g);node('line',{x1:left,x2:right,y1:py,y2:py,stroke:'#e8ebe6'},null,g);
      fittedText({x:px,y:bottom+23,'text-anchor':'middle',fill:'#68706b','font-size':15},format(vx,mx,columns,true),110,g);
      fittedText({x:left-10,y:py+3,'text-anchor':'end',fill:'#68706b','font-size':15},format(vy,my,columns,true),78,g);}
     node('text',{x:(left+right)/2,y:400,'text-anchor':'middle',fill:'#59645c'},name(mx,columns),g);node('text',{x:18,y:45,fill:'#59645c'},name(my,columns),g);
    }else{
     const m0=measures[0],axisDim=dims[0],key=r=>axisDim==null?'All':r[axisDim]==null||r[axisDim]===''?'Unknown':String(r[axisDim]);
     const timeAxis=!!axisDim&&TIME_FIELDS.includes(axisDim)&&!spec.categorical&&rows.every(r=>r[axisDim]==null||timeValue(r[axisDim],axisDim)!=null);
     const order=new Map();for(const r of rows)if(!order.has(key(r)))order.set(key(r),order.size);
     const atOf=r=>timeAxis?timeValue(r[axisDim],axisDim):order.get(key(r));
     pts=rows.filter(r=>finite(r[m0])&&atOf(r)!=null);
     const ats=pts.map(atOf),lo=ats.length?Math.min(...ats):0,hi=ats.length?Math.max(...ats):1,[yl,yh]=extent(pts.map(r=>r[m0]));
     const xAt=at=>left+(hi===lo?.5:(at-lo)/(hi-lo))*(right-left),yAt=v=>bottom-(v-yl)/(yh-yl)*(bottom-top);
     xOf=r=>xAt(atOf(r));yOf=r=>yAt(r[m0]);
     for(let i=0;i<=5;i++){const v=yl+(yh-yl)*i/5;node('line',{x1:left,x2:right,y1:yAt(v),y2:yAt(v),stroke:'#e8ebe6'},null,g);
      fittedText({x:left-12,y:yAt(v)+4,'text-anchor':'end',fill:'#68706b','font-size':15},format(v,m0,columns,true),76,g);}
     const ticks=[...new Map(pts.map(r=>[atOf(r),key(r)])).entries()].sort((a,b)=>a[0]-b[0]),every=Math.max(1,Math.ceil(ticks.length/Math.max(1,Math.floor((right-left)/90))));
     ticks.forEach(([at,text],i)=>{if(spec.axisLabels==='all'||i%every===0)fittedText({x:xAt(at),y:bottom+25,'text-anchor':'middle',fill:'#68706b','font-size':14},text,Math.max(40,(right-left)/Math.max(1,ticks.length)*every-6),g);});
     axes[p]={values:pts.map(r=>r[m0]),lo:yl,hi:yh,at:yAt,x1:left,x2:right,bar:false};
    }
    const partDims=(scatterLayout?dims:dims.slice(1)).filter(d=>d!==spec.path);
    const partOf=r=>partDims.length?partDims.map(d=>r[d]==null||r[d]===''?'Unknown':String(r[d])).join(' / '):'All marks';
    const parts=new Map();for(const r of pts){const k=partOf(r);if(!parts.has(k))parts.set(k,[]);parts.get(k).push(r);}
    const baseFill=spec.seriesColor||slotColour(0),fillOf=r=>spec.color?color(r):shade(r)?palette[0]:baseFill;
    if(spec.type==='polygon'){
     // Each partition (Colour and Detail members) is one closed shape; Path
     // orders its vertices, else the axis does (the rows' order for two measures).
     const cmp=(a,b)=>{if(spec.path){const va=a[spec.path],vb=b[spec.path];if(finite(va)&&finite(vb))return va-vb;return String(va??'').localeCompare(String(vb??''),undefined,{numeric:true});}return scatterLayout?0:xOf(a)-xOf(b);};
     for(const [k,list] of parts){const verts=list.slice().sort(cmp),fill=fillOf(verts[0]),shading=shade(verts[0]);
      const poly=node('path',{d:verts.map((r,j)=>(j?'L':'M')+f2(xOf(r))+','+f2(yOf(r))).join(' ')+' Z',fill,'fill-opacity':shading?shading['fill-opacity']:.35,stroke:fill,'stroke-width':1.6,'stroke-linejoin':'round',class:'plot-mark','data-partition':k,tabindex:0,role:'button','aria-label':k+'; '+verts.length+' vertices'},null,g);
      node('title',{},k+'\n'+verts.length+' vertices'+(spec.path?' in '+name(spec.path,columns)+' order':''),poly);
      poly.addEventListener('click',()=>onSelect?.(verts[0]));
      for(const r of verts)mark('circle',{cx:f2(xOf(r)),cy:f2(yOf(r)),r:6,fill,'fill-opacity':0,'data-hover-target':'vertex'},r,g,true);
      if(spec.labels){const px=verts.reduce((a,r)=>a+xOf(r),0)/verts.length,py=verts.reduce((a,r)=>a+yOf(r),0)/verts.length;
       fittedText({x:f2(px),y:f2(py+5),'text-anchor':'middle',fill:'#35483b','font-size':14,'data-label':'value'},labelText(verts[0],k),220,g);}
     }
    }else{
     // Density (the non-map case of #47/#48): a kernel density of the marks.
     // Size is each mark's radius, a Colour measure its weight, a Colour
     // dimension one layer per member. Low density fades to the plain chart.
     const cell=6,cols=Math.ceil((right-left)/cell),lines=Math.ceil((bottom-top)/cell);
     const radius=r=>sizeId&&finite(r[sizeId])?8+32*Math.sqrt(Math.abs(r[sizeId])/maxSize):18;
     const weight=r=>spec.colorMeasure&&finite(r[spec.colorMeasure])?Math.abs(r[spec.colorMeasure]):1;
     const layers=new Map();for(const r of pts){const k=spec.color?String(r[spec.color]??'Unknown'):'All marks';if(!layers.has(k))layers.set(k,[]);layers.get(k).push(r);}
     let peak=0;
     const grids=[...layers].map(([k,list])=>{const grid=new Float64Array(cols*lines);
      for(const r of list){const px=xOf(r),py=yOf(r),h=radius(r),w=weight(r),reach=2.5*h;
       const c0=Math.max(0,Math.floor((px-reach-left)/cell)),c1=Math.min(cols-1,Math.ceil((px+reach-left)/cell)),l0=Math.max(0,Math.floor((py-reach-top)/cell)),l1=Math.min(lines-1,Math.ceil((py+reach-top)/cell));
       for(let l=l0;l<=l1;l++)for(let c=c0;c<=c1;c++){const dx=left+(c+.5)*cell-px,dy=top+(l+.5)*cell-py;grid[l*cols+c]+=w*Math.exp(-(dx*dx+dy*dy)/(h*h/2));}}
      for(const v of grid)if(v>peak)peak=v;return {k,grid};});
     for(const layer of grids){const fill=spec.color?colorOf(layer.k):spec.colorMeasure?palette[0]:baseFill,layerG=node('g',{'data-density-layer':layer.k},null,g);
      for(let l=0;l<lines;l++)for(let c=0;c<cols;c++){const v=layer.grid[l*cols+c]/(peak||1);if(v<.04)continue;
       node('rect',{x:f2(left+c*cell),y:f2(top+l*cell),width:cell,height:cell,fill,'fill-opacity':f2(Math.min(.9,.05+.85*v))},null,layerG);}}
     for(const r of pts)mark('circle',{cx:f2(xOf(r)),cy:f2(yOf(r)),r:5,fill:fillOf(r),'fill-opacity':0,'data-hover-target':'point'},r,g,true);
     node('text',{x:left,y:bottom+48,fill:'#59645c','font-size':13,'data-note':'density'},'Darker cells hold more marks; each mark spreads '+(sizeId?'over a radius set by '+name(sizeId,columns):'about 18 px')+(spec.colorMeasure?', weighted by '+name(spec.colorMeasure,columns):'')+'.',g);
    }
   }else if(spec.type==='cards'){
    // Several marks cards (#52): one pane and one category axis, each card's
    // measure drawn with its own mark. The first card reads on the left axis,
    // every other card on the right; Synchronize axes gives them one scale.
    const cards=spec.cards.filter(c=>c.visible!==false),left=120,right=880,top=92,bottom=430,axisDim=dims[0];
    // Round steps (1, 2, 2.5 or 5 times a power of ten), as the Studio's bar axes use.
    const nice=(lo,hi)=>{const span=hi-lo||Math.abs(hi)||1,raw=span/5,mag=Math.pow(10,Math.floor(Math.log10(raw))),n=raw/mag,stepSize=(n<=1?1:n<=2?2:n<=2.5?2.5:n<=5?5:10)*mag;return [Math.floor(lo/stepSize+1e-9)*stepSize,Math.ceil(hi/stepSize-1e-9)*stepSize,stepSize];};
    // A card with a Label field labels its marks; with none, Labels labels every card.
    const labelled=cards.some(c=>c.labelFields&&c.labelFields.length);
    const catKey=r=>axisDim==null?'All':r[axisDim]==null||r[axisDim]===''?'Unknown':String(r[axisDim]);
    let cats=[...new Set(rows.map(catKey))];
    if(axisDim&&TIME_FIELDS.includes(axisDim)&&cats.every(c=>c==='Unknown'||timeValue(c,axisDim)!=null))cats=cats.sort((a,b)=>(timeValue(a,axisDim)??Infinity)-(timeValue(b,axisDim)??Infinity));
    const catAt=new Map(cats.map((c,i)=>[c,i])),n=cats.length,step=(right-left)/Math.max(1,n),cx=r=>left+step*(catAt.get(catKey(r))+.5);
    const sync=spec.synchronize===true,leftIds=[cards[0].measure],rightIds=cards.slice(1).map(c=>c.measure);
    const valuesOf=ids=>rows.flatMap(r=>ids.map(id=>r[id]));
    const [la,lb,ls]=nice(...extent(sync?valuesOf(cards.map(c=>c.measure)):valuesOf(leftIds))),[ra,rb,rs]=sync||!rightIds.length?[la,lb,ls]:nice(...extent(valuesOf(rightIds)));
    const ly=v=>bottom-(v-la)/(lb-la)*(bottom-top),ry=v=>bottom-(v-ra)/(rb-ra)*(bottom-top),scaleOf=i=>i===0||sync?ly:ry;
    const inkOf=i=>i===0?(spec.seriesColor||slotColour(0)):slotColour(i);
    for(let v=la;v<=lb+ls*1e-6;v+=ls){node('line',{x1:left,x2:right,y1:f2(ly(v)),y2:f2(ly(v)),stroke:'#e8ebe6'},null,g);
     fittedText({x:left-10,y:f2(ly(v)+4),'text-anchor':'end',fill:'#68706b','font-size':14,'data-axis':'left'},format(v,cards[0].measure,columns,true),110,g);}
    if(rightIds.length)for(let w=ra;w<=rb+rs*1e-6;w+=rs)fittedText({x:right+10,y:f2(ry(w)+4),'text-anchor':'start',fill:'#68706b','font-size':14,'data-axis':'right'},format(w,rightIds[0],columns,true),110,g);
    node('line',{x1:left,x2:left,y1:top,y2:bottom,stroke:'#8a8690'},null,g);
    if(rightIds.length)node('line',{x1:right,x2:right,y1:top,y2:bottom,stroke:'#8a8690'},null,g);
    fittedText({x:left-10,y:top-16,'text-anchor':'start',fill:inkOf(0),'font-size':15,'data-axis-title':'left'},name(cards[0].measure,columns)+(sync&&rightIds.length?' and '+rightIds.map(id=>name(id,columns)).join(', ')+' (one scale)':''),(right-left)/2,g);
    if(rightIds.length&&!sync)fittedText({x:right+10,y:top-16,'text-anchor':'end',fill:inkOf(1),'font-size':15,'data-axis-title':'right'},rightIds.map(id=>name(id,columns)).join(', '),(right-left)/2,g);
    const every=Math.max(1,Math.ceil(n/Math.max(1,Math.min(14,Math.floor((right-left)/70)))));
    cats.forEach((text,i)=>{if(spec.axisLabels==='all'||i%every===0)fittedText({x:f2(left+step*(i+.5)),y:bottom+24,'text-anchor':'middle',fill:'#68706b','font-size':14},text,Math.max(40,step*every-6),g);});
    const membersOf=c=>c.color?[...new Set(rows.map(r=>String(r[c.color]??'Unknown')))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})):[''];
    // Bars share each category's slot: one per bar card and Colour member.
    const slots=Math.max(1,cards.filter(c=>c.type==='bar').reduce((total,c)=>total+membersOf(c).length,0)),bw=Math.min(48,step*.72/slots);
    let slotBase=0;
    // Labels never sit on one another (Tableau's default): a label that would overlap
    // one already drawn is left off, and its value stays in the mark's hover text.
    const placed=[],free=(x,y,w,h)=>{if(placed.some(b=>x<b.x+b.w&&b.x<x+w&&y<b.y+b.h&&b.y<y+h))return false;placed.push({x,y,w,h});return true;};
    cards.forEach((c,index)=>{
     const y=scaleOf(index),m=c.measure,ink=inkOf(index),cg=node('g',{'data-card':c.id||String(index),'data-card-type':c.type},null,g);
     const members=membersOf(c),memberOf=r=>c.color?String(r[c.color]??'Unknown'):'';
     const heatVals=c.colorMeasure?rows.map(r=>r[c.colorMeasure]).filter(finite):[],hl=heatVals.length?Math.min(...heatVals):0,hh=heatVals.length?Math.max(...heatVals):0;
     const fillOf=r=>c.color?slotColour(members.indexOf(memberOf(r))):ink;
     const opacityOf=r=>c.colorMeasure&&finite(r[c.colorMeasure])?.25+.75*(hh>hl?(r[c.colorMeasure]-hl)/(hh-hl):1):null;
     const sizeMax=c.size?Math.max(1e-9,...rows.map(r=>Math.abs(Number(r[c.size])||0))):0,share=r=>c.size&&finite(r[c.size])?Math.sqrt(Math.abs(r[c.size])/sizeMax):null;
     const fields=[...new Set([m,...(c.tooltip||[]),...(c.labelFields||[]),...(c.size?[c.size]:[]),...(c.colorMeasure?[c.colorMeasure]:[])])];
     const tip=r=>label(r,dims)+'\n'+fields.map(id=>name(id,columns)+': '+valueText(r,id)).join('\n');
     const cardMark=(tag,attrs,r)=>{if(collide){const box=markBox(tag,attrs);if(box)markBoxes.push(box);}const made=node(tag,{...attrs,class:'plot-mark',tabindex:0,role:'button','aria-label':tip(r).replace(/\n/g,'; ')},null,cg);node('title',{},tip(r),made);
      made.addEventListener('click',()=>onSelect?.(r));made.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect?.(r);}});return made;};
     const cardLabel=(r,xx,yy,fill)=>{if(!spec.labels||(labelled&&!(c.labelFields&&c.labelFields.length)))return;const text=c.labelFields&&c.labelFields.length?c.labelFields.map(id=>valueText(r,id)).join(' / '):format(r[m],m,columns,true);
      if(collide){const vals=plotted.map(q=>q[m]).filter(finite),hi=Math.max(...vals),lo=Math.min(...vals);wantLabel({x:f2(xx),y:f2(yy),'text-anchor':'middle',fill:fill||'#35483b','font-size':12},text,110,cg,r[m]===hi?30:r[m]===lo?28:1+Math.abs(r[m])/(Math.max(Math.abs(hi),Math.abs(lo))||1),{x:xx,y:yy+8},fill===undefined?[-24,-48].map(dy=>({x:f2(xx),y:f2(yy+dy),'text-anchor':'middle',fill:'#35483b','font-size':12})):[]);return;}
      // Measured at 18px, the size the Studio draws chart text at.
      const w=measure(fit(text,110,12),18)+6;if(!free(xx-w/2,yy-17,w,21))return;
      fittedText({x:f2(xx),y:f2(yy),'text-anchor':'middle',fill:fill||'#35483b','font-size':12,'data-label':'value'},text,110,cg);};
     const plotted=rows.filter(r=>finite(r[m])&&catAt.has(catKey(r)));
     const byMember=new Map(members.map(k=>[k,[]]));for(const r of plotted){const k=memberOf(r);if(!byMember.has(k))byMember.set(k,[]);byMember.get(k).push(r);}
     const ordered=list=>list.slice().sort((a,b)=>{if(c.path){const va=a[c.path],vb=b[c.path];if(finite(va)&&finite(vb))return va-vb;const t=String(va??'').localeCompare(String(vb??''),undefined,{numeric:true});if(t)return t;}return cx(a)-cx(b);});
     if(c.type==='bar'){
      for(const r of plotted){const slot=slotBase+Math.max(0,members.indexOf(memberOf(r))),x0=cx(r)-bw*slots/2+slot*bw,f=share(r),w=f==null?Math.max(1,bw-2):Math.max(1,(bw-2)*(.35+.65*f));
       cardMark('rect',{x:f2(x0+(bw-w)/2),y:f2(Math.min(y(0),y(r[m]))),width:f2(w),height:f2(Math.max(1,Math.abs(y(r[m])-y(0)))),fill:fillOf(r),...(opacityOf(r)!=null?{'fill-opacity':f2(opacityOf(r))}:{})},r);
       cardLabel(r,x0+bw/2,Math.min(y(0),y(r[m]))-6);}
      slotBase+=members.length;
     }else if(['line','area','polygon'].includes(c.type)){
      for(const [k,list] of byMember){if(!list.length)continue;const pts=ordered(list),stroke=fillOf(pts[0]),d=pts.map((r,j)=>(j?'L':'M')+f2(cx(r))+','+f2(y(r[m]))).join(' ');
       if(c.type==='area')node('path',{d:d+' L'+f2(cx(pts.at(-1)))+','+f2(y(0))+' L'+f2(cx(pts[0]))+','+f2(y(0))+' Z',fill:stroke,'fill-opacity':.18,stroke:'none'},null,cg);
       if(c.type==='polygon'){cardMark('path',{d:d+' Z',fill:stroke,'fill-opacity':.3,stroke,'stroke-width':1.6,'stroke-linejoin':'round','data-partition':k||'All'},pts[0]);}
       else{const widths=pts.map(share);
        if(widths.some(v=>v!=null))pts.slice(1).forEach((r,j)=>{node('path',{d:'M'+f2(cx(pts[j]))+','+f2(y(pts[j][m]))+' L'+f2(cx(r))+','+f2(y(r[m])),fill:'none',stroke,'stroke-width':f2(1.5+4.5*(((widths[j]??0)+(widths[j+1]??0))/2)),'stroke-linecap':'round'},null,cg);});
        else node('path',{d,fill:'none',stroke,'stroke-width':2.5,'stroke-linejoin':'round'},null,cg);}
       for(const r of pts){const f=share(r);cardMark('circle',{cx:f2(cx(r)),cy:f2(y(r[m])),r:c.type==='polygon'?5:f==null?4.5:f2(3+8*f),fill:stroke,...(c.type==='polygon'?{'fill-opacity':0,'data-hover-target':'vertex'}:{stroke:'white','stroke-width':1.5})},r);cardLabel(r,cx(r),y(r[m])-10,stroke);}}
     }else if(c.type==='text'){
      for(const r of plotted){const text=c.labelFields&&c.labelFields.length?c.labelFields.map(id=>valueText(r,id)).join(' / '):format(r[m],m,columns,true),f=share(r);
       const t=node('text',{x:f2(cx(r)),y:f2(y(r[m])+4),'text-anchor':'middle',fill:fillOf(r),'font-size':f==null?13:f2(11+9*f),'font-weight':600,class:'plot-mark','data-label':'value'},text,cg);
       node('title',{},tip(r),t);t.addEventListener('click',()=>onSelect?.(r));}
     }else if(c.type==='density'){
      const cell=6,cols=Math.ceil((right-left)/cell),lines=Math.ceil((bottom-top)/cell),grid=new Float64Array(cols*lines);let peak=0;
      for(const r of plotted){const px=cx(r),py=y(r[m]),f=share(r),h=f==null?18:8+32*f,reach=2.5*h;
       const c0=Math.max(0,Math.floor((px-reach-left)/cell)),c1=Math.min(cols-1,Math.ceil((px+reach-left)/cell)),l0=Math.max(0,Math.floor((py-reach-top)/cell)),l1=Math.min(lines-1,Math.ceil((py+reach-top)/cell));
       for(let l=l0;l<=l1;l++)for(let q=c0;q<=c1;q++){const dx=left+(q+.5)*cell-px,dy=top+(l+.5)*cell-py;grid[l*cols+q]+=Math.exp(-(dx*dx+dy*dy)/(h*h/2));}}
      for(const v of grid)if(v>peak)peak=v;
      const layer=node('g',{'data-density-layer':c.id||String(index)},null,cg);
      for(let l=0;l<lines;l++)for(let q=0;q<cols;q++){const v=grid[l*cols+q]/(peak||1);if(v<.04)continue;node('rect',{x:f2(left+q*cell),y:f2(top+l*cell),width:cell,height:cell,fill:ink,'fill-opacity':f2(Math.min(.85,.05+.8*v))},null,layer);}
      for(const r of plotted)cardMark('circle',{cx:f2(cx(r)),cy:f2(y(r[m])),r:5,fill:ink,'fill-opacity':0,'data-hover-target':'point'},r);
     }else{
      // Circle, Square and Shape: a point per group.
      const shapeMembers=c.shape?[...new Set(rows.map(r=>String(r[c.shape]??'Unknown')))].sort((a,b)=>a.localeCompare(b)):[];
      const glyph=r=>c.shape?glyphFor({shapeMap:c.shapeMap},String(r[c.shape]??'Unknown'),shapeMembers):c.glyph||(c.type==='square'?'square':'circle');
      for(const r of plotted){const id=glyph(r),f=share(r),rad=f==null?6:3+12*f,fill=fillOf(r);
       cardMark('path',{d:glyphD(id,cx(r),y(r[m]),rad),...(glyphOpen(id)?{fill:'none',stroke:fill,'stroke-width':1.8}:{fill,stroke:'white','stroke-width':1.2,...(opacityOf(r)!=null?{'fill-opacity':f2(opacityOf(r))}:{})}),'data-glyph':id},r);
       cardLabel(r,cx(r),y(r[m])-rad-5,fill);}
     }
     if(Number.isFinite(c.opacity)&&c.opacity<100)cg.setAttribute('opacity',String(c.opacity/100));
     styleMarks(cg,{border:c.border,halo:c.halo});
    });
    // The legend reads each card's own mark (#52): a bar swatch, a line with its dot.
    let lx=right;const baseline=22;
    // Measured at 18: the Studio sets chart text to 18px (workspace-polish.css), whatever the attribute says.
    for(const [index,c] of [...cards.entries()].reverse()){const ink=inkOf(index),text=name(c.measure,columns),w=measure(text,18)+26;lx-=w+20;
     const item=node('g',{'data-legend':'cards','data-card':c.id||String(index)},null,g);
     if(c.type==='bar')node('rect',{x:f2(lx),y:baseline-12,width:14,height:14,rx:2,fill:ink},null,item);
     else if(['line','area','polygon'].includes(c.type)){node('line',{x1:f2(lx-3),x2:f2(lx+17),y1:baseline-5,y2:baseline-5,stroke:ink,'stroke-width':2.5},null,item);node('circle',{cx:f2(lx+7),cy:baseline-5,r:4,fill:ink},null,item);}
     else if(c.type==='text')node('text',{x:f2(lx+7),y:baseline,'text-anchor':'middle',fill:ink,'font-size':15,'font-weight':700},'T',item);
     else if(c.type==='density')node('rect',{x:f2(lx),y:baseline-12,width:14,height:14,rx:7,fill:ink,'fill-opacity':.45},null,item);
     else node('path',{d:glyphD(c.glyph||(c.type==='square'?'square':'circle'),lx+7,baseline-5,6),fill:ink},null,item);
     node('text',{x:f2(lx+24),y:baseline,fill:'#59645c','font-size':14},text,item);}
    axes[p]={values:rows.map(r=>r[cards[0].measure]),lo:la,hi:lb,at:ly,x1:left,x2:right,bar:false};
   }else if(spec.type==='scatter'){
    const [mx,my]=measures,left=100,right=rightCol?width-rightCol:935,top=65,bottom=plotBottom,[xl,xh]=extent(plotted.map(r=>r[mx]),false),[yl,yh]=extent(plotted.map(r=>r[my]),false),x=v=>left+(v-xl)/(xh-xl)*(right-left),y=v=>bottom-(v-yl)/(yh-yl)*(bottom-top);
    for(let i=0;i<=5;i++){const vx=xl+(xh-xl)*i/5,vy=yl+(yh-yl)*i/5;node('line',{x1:x(vx),x2:x(vx),y1:top,y2:bottom,stroke:'#e8ebe6'},null,g);node('line',{x1:left,x2:right,y1:y(vy),y2:y(vy),stroke:'#e8ebe6'},null,g);fittedText({x:x(vx),y:bottom+23,'text-anchor':'middle',fill:'#68706b','font-size':15},format(vx,mx,columns,true),110,g);fittedText({x:left-10,y:y(vy)+3,'text-anchor':'end',fill:'#68706b','font-size':15},format(vy,my,columns,true),78,g);}
    node('text',{x:(left+right)/2,y:plotBottom+55,'text-anchor':'middle',fill:'#59645c'},name(mx,columns),g);node('text',{x:18,y:45,fill:'#59645c'},name(my,columns),g);const rankOf=collide?scatterRanks(plotted,r=>x(r[mx]),r=>y(r[my]),mx,my,right-left,bottom-top):null;plotted.forEach(r=>{pointMark(r,x(r[mx]),y(r[my]),6,shade(r)||{fill:color(r),'fill-opacity':.72},g);if(rankOf){const rr=sizeId&&typeof r[sizeId]==='number'?3+12*Math.sqrt(Math.abs(r[sizeId])/maxSize):6;markBoxes.push({x:x(r[mx])-rr,y:y(r[my])-rr,w:2*rr,h:2*rr});}if(spec.labels){const at=x(r[mx]),end=at>(left+right)/2,attrs={x:at+(end?-8:8),y:y(r[my])-8,'text-anchor':end?'end':'start',fill:'#35483b','font-size':15},text=labelText(r,label(r,rankOf&&spec.color&&dims.length>1?dims.filter(d=>d!==spec.color):dims)),room=Math.min(300,end?at-24:width-at-24);if(rankOf)wantLabel(attrs,text,rightCol&&!end?Math.min(room,right-at-16):room,g,rankOf(r),{x:at,y:y(r[my])},[{...attrs,x:at+(end?8:-8),'text-anchor':end?'start':'end'}]);else fittedText(attrs,text,room,g);}});
   }else{
    const prepared=timelines[p],xs=prepared.ticks,left=100,right=rightCol?width-rightCol:940,top=60,bottom=plotBottom,[lo,hi]=extent(plotted.map(r=>r[m])),x=at=>left+(xs.length===1?.5:(at-xs[0][0])/(xs.at(-1)[0]-xs[0][0]))*(right-left),y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top);
    for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5;node('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#e8ebe6'},null,g);fittedText({x:left-12,y:y(v)+4,'text-anchor':'end',fill:'#68706b','font-size':15},format(v,m,columns,true),76,g);}
    const tickWidth=xs.reduce((max,[,text])=>Math.max(max,measure(text)),0),tickCount=Math.max(1,Math.min(12,Math.floor((right-left)/(tickWidth+16))));
    // spec.axisLabels 'all' (census B-E21): every tick is labelled, turned when they would collide.
    const allTicks=spec.axisLabels==='all',turnTicks=allTicks&&xs.length>tickCount;
    xs.forEach(([at,text],i)=>{if(turnTicks)fittedText({x:x(at),y:bottom+16,'text-anchor':'end',fill:'#68706b','font-size':12,transform:`rotate(-35 ${x(at)} ${bottom+16})`,'data-axis-label':'all'},text,110,g);else if(allTicks||i%Math.max(1,Math.ceil(xs.length/tickCount))===0)node('text',{x:x(at),y:bottom+25,'text-anchor':'middle',fill:'#68706b','font-size':15},text,g);});
    // Colour on the time axis itself colours each point by its period.
    const perPoint=spec.color&&spec.color===dims[0];
    // Lane F2b: a moved plot tells the reference lines its own scale; each coloured line is named at its end.
    if(rightCol||plotBottom!==345)axes[p]={values:plotted.map(r=>r[m]),lo,hi,at:y,x1:left,x2:right,bar:false};
    if(legendRight&&spec.endLabels&&spec.color&&!perPoint){const lines=spec.path?pathSeries(prepared.series,dims,spec.path,m):prepared.series,members=new Set(lines.map(s=>String((s.rows[0]||{row:{}}).row[spec.color]??'Unknown')));if(lines.length>=2&&lines.length<=12&&members.size===lines.length)ends=[];}
    for(const [seriesIndex,s] of (spec.path?pathSeries(prepared.series,dims,spec.path,m):prepared.series).entries()){const stroke=perPoint?'#8a8690':spec.color?color(s.rows[0].row):categoryColour(seriesIndex);const rank=collide?lineRanks(s,m):null;if(rank)for(const seg of s.segments)for(const q of seg)markBoxes.push({x:x(q.at)-4.5,y:y(q.row[m])-4.5,w:9,h:9});if(ends&&s.segments.length){const q=s.segments.at(-1).at(-1);ends.push({x:x(q.at),y:y(q.row[m]),stroke,name:String(s.rows[0].row[spec.color]??'Unknown')});}
     for(const segment of s.segments){const d=segment.map((item,i)=>(i?'L':'M')+x(item.at)+','+y(item.row[m])).join(' ');
      if(spec.type==='area'){const path=`M${x(segment[0].at)},${y(0)} `+segment.map(item=>`L${x(item.at)},${y(item.row[m])}`).join(' ')+` L${x(segment.at(-1).at)},${y(0)} Z`;node('path',{d:path,fill:perPoint?palette[0]:stroke,'fill-opacity':.1,stroke:'none'},null,g);}
      if(sizeId)segment.slice(1).forEach((item,k)=>{const prev=segment[k],share=v=>typeof v==='number'?Math.abs(v)/maxSize:0;node('path',{d:`M${x(prev.at)},${y(prev.row[m])} L${x(item.at)},${y(item.row[m])}`,fill:'none',stroke,'stroke-width':1.5+4.5*(share(prev.row[sizeId])+share(item.row[sizeId]))/2,'stroke-linecap':'round'},null,g);});
      else node('path',{d,fill:'none',stroke,'stroke-width':2.5},null,g);segment.forEach(({row:r,at})=>{mark('circle',{cx:x(at),cy:y(r[m]),r:4.5,fill:perPoint?color(r):stroke,stroke:'white','stroke-width':1.5},r,g,true);if(spec.labels){const attrs={x:x(at),y:y(r[m])-11,'text-anchor':'middle',fill:perPoint?color(r):stroke,'font-size':15},text=labelText(r,format(r[m],m,columns,true));if(rank)wantLabel(attrs,text,110,g,rank(r),{x:x(at),y:y(r[m])},[{...attrs,y:y(r[m])+23},...(rank(r)>=26?sideSpots(x(at),y(r[m]),attrs):[])]);else fittedText(attrs,text,110,g);}});
     }
    }
   }
  }
  // spec.endLabels (#51): with Colour on a dimension each line is named at its end, in its own colour, clear
  // of the legend cards and of the other names. The waiting labels then take the room that is left.
  for(const c of legendCards)taken.push({x:c.x-4,y:c.y-4,w:c.w+8,h:c.h+8});
  // Lane G1b (#51): each name's short leader is a line too; the value labels keep clear of it.
  const leaders=[];
  if(ends&&ends.length){const group=node('g',{'data-legend':'line-ends','aria-label':'Line names'});let below=-Infinity;
   for(const e of ends.sort((a,b)=>a.y-b.y)){const w=measure(e.name,T),tx=e.x+18;let ty=Math.max(e.y+6,below+T+3);
    for(const c of legendCards)if(tx<c.x+c.w&&c.x<tx+w&&ty-T<c.y+c.h+4&&c.y<ty+6)ty=c.y+c.h+T+4;
    if(ty>panelH-6)continue;below=ty;
    node('line',{x1:f2(e.x+6),y1:f2(e.y),x2:f2(e.x+14),y2:f2(ty-6),stroke:e.stroke,'stroke-width':1.2},null,group);leaders.push([e.x+6,e.y,e.x+14,ty-6,.6]);
    const t=node('text',{x:f2(tx),y:f2(ty),fill:e.stroke,'font-size':15,'data-end-label':e.name},fit(e.name,width-tx-6,T),group);node('title',{},e.name,t);
    taken.push({x:tx-3,y:ty-T*.8-2,w:w+6,h:T+4});}}
  if(waiting.length){
   // Lanes G1/G1b (#51, 2026-09-27): a label keeps a clear gap of GAP units from every other label (value
   // labels, axis ticks and titles, line names) and from every mark, and GAP plus half the stroke from every
   // line (the plot's lines and each name's leader). GAP is 4 units - 2.4px on the boards' 1344px screen -
   // or spec.labelGap, which Chart Studio sets so the gap is at least 2px at the size the chart is drawn, up to
   // 6 units: a label above its point sits 7.4 units over it, so a wider gap would part every label from the
   // line it belongs to.
   // A label's box is its glyphs' (ascent .78 and descent .2 of the size the Studio draws, T). On a scatter or
   // the dual-axis cards an extreme that finds every side on a mark may sit beside another mark, never on a
   // line; on a line or area chart the points are the lines' own ink, so no label comes nearer them than GAP.
   // A label with no such side is left to its tooltip (the round-1 capture struck $7,920.00 through with a
   // line, and set $4,372.00 against another line's point).
   const GAP=Math.min(6,Math.max(4,Number(spec.labelGap)||0)),ink=(attrs,w)=>{const x0=Number(attrs.x),y0=Number(attrs.y),anchor=attrs['text-anchor']||'start';return {x:anchor==='end'?x0-w:anchor==='middle'?x0-w/2:x0,y:y0-T*.78,w,h:T*.98};};
   const grown=(r,by)=>({x:r.x-by,y:r.y-by,w:r.w+2*by,h:r.h+2*by});
   // Lines are in the way too: every unfilled stroked path's segments, read off the drawing.
   const segs=[...leaders],walk=n=>{for(const k of [...(n.children||[])]){const get=key=>k.getAttribute?k.getAttribute(key):null;if(get('data-legend')!=null||get('data-reference')!=null)continue;
     if(String(k.tagName||'').toLowerCase()==='path'&&get('fill')==='none'&&get('stroke')&&get('stroke')!=='none')for(const part of String(get('d')||'').split('M').slice(1)){const p=(part.match(/-?\d+(?:\.\d+)?/g)||[]).map(Number),half=(Number(get('stroke-width'))||1)/2;for(let i=2;i+1<p.length;i+=2)segs.push([p[i-2],p[i-1],p[i],p[i+1],half]);}
     // Text already drawn (axis ticks and titles) is never covered, even by an extreme; a turned tick keeps its own rule.
     if(String(k.tagName||'').toLowerCase()==='text'&&!get('transform')){const words=k.childNodes?[...k.childNodes].filter(c=>c.nodeType===3).map(c=>c.nodeValue).join(''):String(k._text||'');
      if(words.trim()){const w=measure(words,T),x0=Number(get('x'))||0,anchor=get('text-anchor')||'start';taken.push(grown({x:anchor==='end'?x0-w:anchor==='middle'?x0-w/2:x0,y:(Number(get('y'))||0)-T*.8,w,h:T},GAP/2));}}
     walk(k);}};
   walk(svg);
   const meets=(p,q)=>p.x<q.x+q.w&&q.x<p.x+p.w&&p.y<q.y+q.h&&q.y<p.y+p.h;
   const cross=(a,b,c,d,e,f,g2,h)=>{const den=(c-a)*(h-f)-(d-b)*(g2-e);if(!den)return false;const t=((e-a)*(h-f)-(f-b)*(g2-e))/den,u=((e-a)*(d-b)-(f-b)*(c-a))/den;return t>=0&&t<=1&&u>=0&&u<=1;};
   const onSeg=(r,[a,b,c,d])=>{const inside=(x,y)=>x>=r.x&&x<=r.x+r.w&&y>=r.y&&y<=r.y+r.h;return inside(a,b)||inside(c,d)||cross(a,b,c,d,r.x,r.y,r.x+r.w,r.y)||cross(a,b,c,d,r.x,r.y+r.h,r.x+r.w,r.y+r.h)||cross(a,b,c,d,r.x,r.y,r.x,r.y+r.h)||cross(a,b,c,d,r.x+r.w,r.y,r.x+r.w,r.y+r.h);};
   const mine=(own,m)=>own&&own.x>=m.x-1&&own.x<=m.x+m.w+1&&own.y>=m.y-1&&own.y<=m.y+m.h+1;
   waiting.sort((a,b)=>b.rank-a.rank||a.order-b.order);
   for(const q of waiting){let placed=false;
    for(const clear of q.rank>=26&&!timed?[true,false]:[true]){for(const attrs of [q.attrs,...(q.alts||[])]){
     const shown=fit(q.text,q.room,attrs['font-size']||15),glyphs=ink(attrs,measure(shown,T)),b=grown(glyphs,GAP/2);
     if(b.x<0||b.x+b.w>width||b.y<0||b.y+b.h>panelH*panels||taken.some(t=>meets(b,t)))continue;
     if(segs.some(s=>onSeg(grown(glyphs,GAP+(s[4]||0)),s)))continue;
     if(clear&&markBoxes.some(m=>!mine(q.own,m)&&meets(grown(glyphs,GAP),m)))continue;
     taken.push(b);fittedText({...attrs,'data-label':'value'},q.text,q.room,q.parent);placed=true;break;}
     if(placed||!clear)break;}}}
  if(spec.references&&(spec.references.average||spec.references.median)&&spec.type!=='scatter'&&!(V3_TYPES.includes(spec.type)&&!axes[0])){
   for(let p=0;p<(dual||V3_TYPES.includes(spec.type)?1:measures.length);p++){
    const ax=axes[p];
    const id=measures[p],values=(ax?ax.values:plotted.map(row=>row[id])).filter(v=>typeof v==='number'&&Number.isFinite(v)).sort((a,b)=>a-b);if(!values.length)continue;
    const average=values.reduce((a,b)=>a+b,0)/values.length,middle=Math.floor(values.length/2),median=values.length%2?values[middle]:(values[middle-1]+values[middle])/2,[lo,hi]=ax?[ax.lo,ax.hi]:extent(values),bar=ax?ax.bar:spec.type==='bar';
    for(const [kind,value]of [['average',average],['median',median]])if(spec.references[kind]){
     const g=node('g',{transform:`translate(0 ${p*panelH})`,'data-reference':kind});const at=ax&&ax.at?ax.at(value):bar?barLeft+(value-lo)/(hi-lo)*(barRight-barLeft):345-(value-lo)/(hi-lo)*285;
     node('line',bar?{x1:at,x2:at,y1:48,y2:panelH-43,stroke:'#745079','stroke-dasharray':'6 4'}:{x1:ax?.x1??100,x2:ax?.x2??940,y1:at,y2:at,stroke:'#745079','stroke-dasharray':'6 4'},null,g);
     node('text',{x:bar?at+3:(ax?.x2??940)-2,y:bar?42:at-5,'text-anchor':bar?'start':'end',fill:'#745079','font-size':12},`${kind==='average'?'Mean':'Median'} of shown marks: ${format(value,id,columns,true)}`,g);
    }
   }
  }
  // Keep legends inside the exported SVG. A sibling HTML legend is absent from
  // PNG/SVG downloads, leaving a coloured series impossible to identify.
  const legends=legendRight?[]:spec.type==='cards'?[]:spec.color?colorValues:timed&&dims.length>1?timelines[0].series.map(s=>s.label):dual?measures.map((id,i)=>name(id,columns)+(i?' (right axis)':' (left axis)')):[];
  if(legends.length){
   const legend=node('g',{'data-legend':'series','aria-label':'Series legend'}),plan=legendPlan(legends),base=panelH*panels;
   for(const {i,value,lines,x,y}of plan.items){
    const swatch=node('rect',{x,y:base+y-11,width:11,height:11,rx:2,fill:spec.color?colorOf(value):categoryColour(i)},null,legend),hint=conduitNote(value);
    if(hint)node('title',{},hint,swatch);
    lines.forEach((line,j)=>{const t=node('text',{x:x+18,y:base+y+j*20,fill:'#59645c','font-size':15},line,legend);if(hint)node('title',{},hint,t);});
   }
   const height=base+plan.height;svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('height',height);
  }
  // A Colour measure's two-stop legend (V09), inside the exported SVG.
  if(heat&&!legendRight){
   const box=svg.getAttribute('viewBox').split(' ').map(Number),base=box[3],legend=node('g',{'data-legend':'colour','aria-label':'Colour legend'});
   node('text',{x:24,y:base+24,fill:'#59645c','font-size':14},name(spec.colorMeasure,columns),legend);
   node('rect',{x:24,y:base+34,width:14,height:14,rx:2,fill:palette[0],'fill-opacity':.25},null,legend);node('text',{x:44,y:base+46,fill:'#59645c','font-size':14},format(heat[0],spec.colorMeasure,columns,true),legend);
   node('rect',{x:180,y:base+34,width:14,height:14,rx:2,fill:palette[0],'fill-opacity':1},null,legend);node('text',{x:200,y:base+46,fill:'#59645c','font-size':14},format(heat[1],spec.colorMeasure,columns,true),legend);
   const height=base+60;svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('height',height);background.setAttribute('height',height);
  }
  // Marks v3: which shape each member of a Shape field takes.
  if(spec.shape&&shapeMembers.length&&spec.type==='scatter'&&!legendRight){
   const box=svg.getAttribute('viewBox').split(' ').map(Number),base=box[3],legend=node('g',{'data-legend':'shape','aria-label':'Shape legend'});
   node('text',{x:24,y:base+22,fill:'#59645c','font-size':14},name(spec.shape,columns),legend);
   let x=24,y=base+46;
   for(const member of shapeMembers){const id=glyphFor(spec,member,shapeMembers),w=measure(member,14)+36;if(x>24&&x+w>width-24){x=24;y+=24;}
    node('path',{d:glyphD(id,x+7,y-5,6),...(glyphOpen(id)?{fill:'none',stroke:'#59645c','stroke-width':1.5}:{fill:'#59645c'})},null,legend);node('text',{x:x+18,y,fill:'#59645c','font-size':14},member,legend);x+=w;}
   const height=y+14;svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('height',height);background.setAttribute('height',height);
  }
  // Lane F2b: the right-hand legend cards (spec.legendPlacement 'right'), drawn over the plot's column.
  if(legendCards.length){const legend=node('g',{'data-legend':'cards-right','aria-label':'Legend'});
   for(const c of legendCards){const card=node('g',{'data-legend-card':c.kind},null,legend);
    node('rect',{x:f2(c.x),y:f2(c.y),width:f2(c.w),height:f2(c.h),rx:8,fill:'#ffffff',stroke:'#e6e1e8'},null,card);
    const head=node('text',{x:f2(c.x+12),y:f2(c.y+26),fill:'#2f273f','font-size':15,'font-weight':600},fit(c.title,c.w-24,T),card);node('title',{},c.title,head);
    let at=c.y+36;
    c.items.forEach((item,i)=>{const mid=at+c.rows[i]/2,tx=c.x+12+(item.r?2*Math.max(item.r,6)+10:24);
     if(item.r)node('circle',{cx:f2(c.x+12+Math.max(item.r,6)),cy:f2(mid),r:f2(item.r),fill:'#b9b3c2','fill-opacity':.55,stroke:'#8a8690','stroke-width':1},null,card);
     else if(item.glyph)node('path',{d:glyphD(item.glyph,c.x+18,mid,6),...(glyphOpen(item.glyph)?{fill:'none',stroke:'#59645c','stroke-width':1.5}:{fill:'#59645c'})},null,card);
     else{const dot=node('circle',{cx:f2(c.x+18),cy:f2(mid),r:6,fill:item.fill,...(item.opacity!=null?{'fill-opacity':item.opacity}:{})},null,card);if(item.note)node('title',{},item.note,dot);}
     const t=node('text',{x:f2(tx),y:f2(mid+6),fill:'#4a4552','font-size':15},fit(item.text,c.x+c.w-12-tx,T),card);node('title',{},item.note?item.text+'\n'+item.note:item.text,t);at+=c.rows[i];});
    if(c.more)node('text',{x:f2(c.x+12),y:f2(at+17),fill:'#77727d','font-size':15},'+ '+c.more+' more',card);}}
  container.append(svg);
  return svg;
 }
 const api={render,format,isMoney,validate,extent,label,timeValue,timeline,drawnTimeline,scatterPairs,combine,combineGroup,combineRule,combineNote,COMBINE_RULE,
  glyphD,glyphFor,glyphOpen,GLYPH_IDS,V3_TYPES,styleMarks,styleColour,tableV3,pathSeries};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MentatContributionPlot=api;
})(globalThis);
