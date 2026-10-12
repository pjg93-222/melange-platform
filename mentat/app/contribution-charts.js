/* Additional chart families over server aggregates, including source quartiles.
   Every interactive mark retains its exact group for source-record inspection. */
(function(root){
 'use strict';
 const base=typeof module!=='undefined'&&module.exports?require('./contribution-plot.js'):root.MentatContributionPlot;
 const TYPES=['heatmap','highlight','stacked','dot','series-dot','treemap','bubbles','histogram','box','gantt','bullet','combination','dual-line'];
 const palette=root.MentatTheme?.palette||['#57447b','#327a7a','#c68a18','#96628d','#466d9b','#80512d'];
 // Paul, 2026-09-24: members, series and stacks take the theme's twelve categorical colours, tinted
 // past twelve (theme.js categoryColour), so a legend repeats no colour in its first 36 members.
 // Without theme.js the palette cycles as before.
 const categoryColour=typeof root.MentatTheme?.categoryColour==='function'?root.MentatTheme.categoryColour:i=>palette[i%palette.length];
 const BOX=['min_amount','q1_amount','median_amount','q3_amount','max_amount'];
 // Paul, 2026-09-23: "within the Chart Studio make sure everything is freely flexible, that all graph
 // types properly register and nothing is logically constrained, it needs to be utterly maluable".
 // Any number of dimensions and measures draws (contract V07). Only an unknown chart, an empty field
 // list or an invalid source statistic is refused.
 function validate(rows,spec){
  if(!TYPES.includes(spec.type))throw new Error('Unknown analytical chart.');
  if(!Array.isArray(rows)||!Array.isArray(spec.dimensions)||!spec.measures?.length)throw new Error('Choose fields for this chart.');
  const required=spec.type==='box'?BOX:spec.type==='gantt'?['first_date','last_date']:spec.measures;
  for(const row of rows)for(const id of required){const v=row[id];if(v!=null&&(id.endsWith('_date')?!/^\d{4}-\d{2}-\d{2}$/.test(v):typeof v!=='number'||!Number.isFinite(v)))throw new Error('A required source statistic is invalid.');}
 }
 // V07: repeated groups are combined, never refused. Code review F2: each statistic by its own rule
 // (contribution-plot.js combineGroup): amounts and counts add, a mean is weighted by observation
 // count, a deviation is pooled, minimum and maximum take the extremes, dates widen the span; a
 // median, a quartile or a distinct count cannot be rebuilt, so a combined mark leaves it blank.
 function combine(rows,dims,columns=[]){return base.combine(rows,dims,columns);}
 const cut=(s,n=34)=>String(s).length>n?String(s).slice(0,n-1)+'…':String(s);
 function partition(items,x,y,w,h,weight){
  if(!items.length)return[];if(items.length===1)return[{row:items[0],x,y,w,h}];
  const total=items.reduce((s,r)=>s+weight(r),0);let at=1,sum=weight(items[0]);
  while(at<items.length-1&&sum<total/2)sum+=weight(items[at++]);
  const ratio=total?sum/total:at/items.length;
  return w>=h?[...partition(items.slice(0,at),x,y,w*ratio,h,weight),...partition(items.slice(at),x+w*ratio,y,w*(1-ratio),h,weight)]:[...partition(items.slice(0,at),x,y,w,h*ratio,weight),...partition(items.slice(at),x,y+h*ratio,w,h*(1-ratio),weight)];
 }
 function render(container,input,spec,onSelect){
  validate(input,spec);container.replaceChildren();if(!input.length){container.textContent='No observations match this selection.';return;}
  const dims=spec.dimensions,cols=spec.columns||[],{rows,combined,outcomes}=combine(input,dims,cols),ms=spec.measures,m=ms[0],fmt=(v,id=m)=>base.format(v,id,cols),label=r=>base.label(r,dims),name=id=>cols.find(c=>c.id===id)?.name||id;
  const notes=[];if(combined)notes.push(base.combineNote(combined,outcomes,cols));
  const unit=id=>cols.find(c=>c.id===id)?.unit||(base.isMoney(id,cols)?'cents':'count');
  // V08: Colour (spec.color) separates every type by member; the legend is drawn inside the SVG.
  const colorId=spec.color&&rows.some(r=>r[spec.color]!==undefined)?spec.color:'';
  const member=r=>String(r[colorId]??'Unknown'),members=colorId?[...new Set(rows.map(member))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})):[];
  // Conduit (theme.js categoryColours) has a colour of its own and takes no slot, so the other members keep the colours they have without it.
  const memberColours=names=>typeof root.MentatTheme?.categoryColours==='function'?root.MentatTheme.categoryColours(names):names.map((_,i)=>categoryColour(i));
  const memberSlots=memberColours(members);
  const fillOf=name=>spec.colorMap&&spec.colorMap[name]||memberSlots[Math.max(0,members.indexOf(name))]||categoryColour(0);
  // The hover note a Conduit value carries wherever the recipient category is drawn (theme.js categoryNote).
  const conduitNote=value=>spec.color==='recipient_category'||dims.includes('recipient_category')?(root.MentatTheme?.categoryNote?.(String(value))||''):'';
  const rowNote=r=>['recipient_category'].filter(id=>spec.color===id||dims.includes(id)).map(id=>conduitNote(r[id])).find(Boolean)||'';
  const memberFill=r=>fillOf(member(r));
  // V05: Label and Tooltip fields reach every mark's hover text, and Label replaces the drawn value.
  const isDim=id=>dims.includes(id)||cols.some(c=>c.id===id&&(c.role==='dimension'||c.type==='category'));
  const valueText=(r,id)=>isDim(id)?String(r[id]??'Unknown'):fmt(r[id],id);
  const extras=[...new Set([...(spec.tooltip||[]),...(spec.labelFields||[]),...(spec.size?[spec.size]:[]),...(spec.colorMeasure?[spec.colorMeasure]:[])])].filter(id=>!ms.includes(id));
  const extraText=r=>extras.filter(id=>r[id]!==undefined).map(id=>name(id)+': '+valueText(r,id)).join('; ');
  const labelText=(r,fallback)=>spec.labelFields?.length?spec.labelFields.map(id=>valueText(r,id)).join(' / '):fallback;
  let H=Math.max(430,rows.length*34+110);if(['treemap','bubbles','combination','dual-line','histogram'].includes(spec.type))H=560;
  const W=1000,svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','plot-svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('width',W);svg.setAttribute('height',H);svg.setAttribute('role','group');svg.setAttribute('aria-label',spec.type+' chart. Every mark can open its source records.');
  const node=(tag,attrs={},text,parent=svg)=>{const n=document.createElementNS(svg.namespaceURI,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!=null)n.textContent=text;parent.append(n);return n;};
  const text=(x,y,s,attrs={},parent=svg)=>node('text',{x,y,'font-size':14,fill:'#342649','font-family':'Arial, sans-serif',...attrs},s,parent);
  node('rect',{width:W,height:H,fill:'#fff'});node('title',{},spec.title||spec.type);text(22,25,spec.title||name(m),{'font-size':17});
  const note=s=>text(22,48,s,{'font-size':12,fill:'#645d6d'});
  const color=(v,max)=>v<0?`rgba(181,91,40,${.18+.8*Math.min(1,Math.abs(v)/(max||1))})`:`rgba(87,68,123,${.18+.8*Math.min(1,Math.abs(v)/(max||1))})`;
  // Marks v3 (Square, Shape): a circle view's circles are drawn as shapes from
  // the palette, one per member of a Shape field.
  const shapeMembers=spec.shape?[...new Set(rows.map(r=>String(r[spec.shape]??'Unknown')))].sort((a,b)=>a.localeCompare(b)):[];
  const glyphing=['dot','series-dot'].includes(spec.type)&&(!!spec.glyph||!!spec.shape)&&typeof base.glyphD==='function';
  const asGlyph=(tag,attrs,row)=>{if(!glyphing||tag!=='circle')return [tag,attrs];const id=spec.shape?base.glyphFor(spec,String(row[spec.shape]??'Unknown'),shapeMembers):spec.glyph;
   const {cx,cy,r,fill,...rest}=attrs;return ['path',{d:base.glyphD(id,Number(cx),Number(cy),Number(r)),...rest,...(base.glyphOpen(id)?{fill:'none',stroke:fill,'stroke-width':1.8}:{fill}),'data-glyph':id}];};
  // Marks v3: a Colour measure shades every analytical mark (one hue, its opacity
  // rising with the value, as contribution-plot shades bars and circles); the
  // heat map and highlight table already shade their cells by it.
  const shadeValues=spec.colorMeasure&&!['heatmap','highlight'].includes(spec.type)?rows.map(r=>r[spec.colorMeasure]).filter(v=>typeof v==='number'&&Number.isFinite(v)):[];
  const shadeRange=shadeValues.length?[Math.min(...shadeValues),Math.max(...shadeValues)]:null;
  const shadeMark=(attrs,row)=>{if(!shadeRange||!row||typeof row[spec.colorMeasure]!=='number'||attrs.fill==='none'||Number(attrs['fill-opacity'])<.05)return attrs;
   const t=shadeRange[1]>shadeRange[0]?(row[spec.colorMeasure]-shadeRange[0])/(shadeRange[1]-shadeRange[0]):1;return {...attrs,fill:palette[0],'fill-opacity':Number((.25+.75*t).toFixed(3))};};
  const mark=(tag0,attrs0,row,detail)=>{const [tag,attrs]=asGlyph(tag0,shadeMark(attrs0,row),row);const extra=[extraText(row),rowNote(row)].filter(Boolean).join('; '),n=node(tag,{...attrs,tabindex:0,role:'button',class:'plot-mark','aria-label':label(row)+'; '+(detail||ms.map(id=>name(id)+': '+fmt(row[id],id)).join('; '))+(extra?'; '+extra:'')});node('title',{},n.getAttribute('aria-label'),n);n.onclick=()=>onSelect?.(row);n.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect?.(row);}};return n;};
  const axis=(values,left=265,right=940,y=H-34)=>{const[lo,hi]=base.extent(values),scale=v=>left+(v-lo)/(hi-lo)*(right-left);for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5;node('line',{x1:scale(v),x2:scale(v),y1:70,y2:y-18,stroke:'#ece8ef'});text(scale(v),y,base.format(v,m,cols,true),{'text-anchor':'middle'});}return scale;};
  // The SVG grows downward for a legend or a note, so nothing is clipped from an export.
  const grow=h=>{const box=svg.getAttribute('viewBox').split(' ').map(Number),top=box[3];box[3]+=h;svg.setAttribute('viewBox',box.join(' '));svg.setAttribute('height',box[3]);svg.firstChild.setAttribute('height',box[3]);return top;};
  // V06: the mean and median of the shown marks on the value axis, as contribution-plot draws them.
  const refs=spec.references&&(spec.references.average||spec.references.median)?spec.references:null;
  const reference=(values,at,horizontal=false,id=m,span=[62,H-52])=>{
   if(!refs)return;const v=values.filter(x=>typeof x==='number'&&Number.isFinite(x)).sort((a,b)=>a-b);if(!v.length)return;
   const mid=Math.floor(v.length/2),stats={average:v.reduce((a,b)=>a+b,0)/v.length,median:v.length%2?v[mid]:(v[mid-1]+v[mid])/2};
   ['average','median'].filter(k=>refs[k]).forEach((kind,i)=>{const g=node('g',{'data-reference':kind}),p=at(stats[kind]),words=`${kind==='average'?'Mean':'Median'} of shown marks: ${base.format(stats[kind],id,cols,true)}`;
    if(horizontal){node('line',{x1:span[0],x2:span[1],y1:p,y2:p,stroke:'#745079','stroke-dasharray':'6 4'},null,g);text(span[1]-2,p-5-i*14,words,{'text-anchor':'end',fill:'#745079','font-size':12},g);}
    else{node('line',{x1:p,x2:p,y1:span[0],y2:span[1],stroke:'#745079','stroke-dasharray':'6 4'},null,g);text(p+3,span[0]+2+i*13,words,{fill:'#745079','font-size':12},g);}});
  };
  let legendItems=null;
  const legend=(labels,fills,dashed=[])=>{
   if(!labels.length)return;const width=Number(svg.getAttribute('viewBox').split(' ')[2]),items=[];let x=22,line=0;
   labels.forEach((l,i)=>{const s=cut(l,40),w=26+s.length*7.2;if(x+w>width-10&&x>22){x=22;line++;}items.push({i,s,x,line});x+=w+14;});
   const top=grow(30+line*20),g=node('g',{'data-legend':'series','aria-label':'Series legend'});
   for(const it of items){const y=top+18+it.line*20,swatch=node('rect',{x:it.x,y:y-10,width:11,height:11,rx:2,fill:fills[it.i]},null,g),hint=conduitNote(labels[it.i]);if(hint)node('title',{},hint,swatch);if(dashed[it.i])node('line',{x1:it.x,x2:it.x+11,y1:y+3,y2:y+3,stroke:fills[it.i],'stroke-dasharray':'3 2'},null,g);const caption=text(it.x+17,y,it.s,{'font-size':12,fill:'#59645c'},g);if(hint)node('title',{},hint,caption);}
  };
  const seriesFills=list=>{const slots=memberColours(list);return list.map((name,i)=>spec.colorMap&&spec.colorMap[name]||slots[i]);};
  if(['heatmap','highlight'].includes(spec.type)){
   // V07: one dimension gives one column (or one column per measure); three or more key the rows by
   // the tuple of dims[1..] with dims[0] across; no dimension is one row. V09: Colour measure shades.
   const byMeasure=dims.length<=1&&ms.length>1,shadeId=!byMeasure&&spec.colorMeasure&&rows.some(r=>typeof r[spec.colorMeasure]==='number')?spec.colorMeasure:null;
   const rowOf=dims.length>=3?r=>dims.slice(1).map(d=>String(r[d]??'Unknown')).join(' / '):dims.length?r=>String(r[dims[0]]??'Unknown'):()=>'All';
   const colOf=dims.length>=3?r=>String(r[dims[0]]??'Unknown'):dims.length===2?r=>String(r[dims[1]]??'Unknown'):()=>name(m);
   const cells=byMeasure?rows.flatMap(r=>ms.map(id=>({r,y:rowOf(r),x:name(id),id,v:r[id],shade:r[id],scale:id}))):rows.map(r=>({r,y:rowOf(r),x:colOf(r),id:m,v:r[m],shade:shadeId?r[shadeId]:r[m],scale:'all'}));
   const ys=[...new Set(cells.map(c=>c.y))],xs=[...new Set(cells.map(c=>c.x))];
   const cw=Math.max(95,680/xs.length),ch=38,width=Math.max(W,270+cw*xs.length),height=Math.max(360,150+ch*ys.length);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('width',width);svg.setAttribute('height',height);svg.firstChild.setAttribute('width',width);svg.firstChild.setAttribute('height',height);
   const max=new Map();for(const c of cells)max.set(c.scale,Math.max(max.get(c.scale)||1,Math.abs(c.shade||0)));
   note('Blank cells mean no returned group. Colour preserves signed values; inspect any cell.');xs.forEach((v,i)=>text(255+i*cw+cw/2,85,cut(v,12),{'text-anchor':'middle'}));ys.forEach((v,i)=>text(240,115+i*ch,cut(v),{'text-anchor':'end'}));
   if(shadeId)notes.push('Cells are shaded by '+name(shadeId)+'; the printed value is '+name(m)+'.');
   if(byMeasure)notes.push('Each measure is its own column, shaded on its own scale.');
   cells.forEach(c=>{const x=255+xs.indexOf(c.x)*cw,y=95+ys.indexOf(c.y)*ch,top=max.get(c.scale);mark('rect',{x,y,width:cw-2,height:ch-2,fill:color(c.shade,top)},c.r);if(spec.type==='highlight'||spec.labels)text(x+cw/2,y+24,cut(labelText(c.r,base.format(c.v,c.id,cols,true)),Math.max(8,Math.floor(cw/8))),{'text-anchor':'middle',fill:Math.abs(c.shade||0)>top*.55?'white':'#342649','pointer-events':'none'});});
  }else if(spec.type==='stacked'){
   // V02/V07: Colour is the series when placed; otherwise dims[1..] are; with one dimension and no
   // Colour, each measure in the first measure's unit is its own segment.
   const others=dims.filter(d=>d!==colorId);
   const groupOf=colorId?(others.length?r=>others.map(d=>String(r[d]??'Unknown')).join(' / '):()=>'All'):dims.length?r=>String(r[dims[0]]??'Unknown'):()=>'All';
   const seriesOf=colorId?member:dims.length>=2?r=>dims.slice(1).map(d=>String(r[d]??'Unknown')).join(' / '):null;
   const stackIds=seriesOf?[m]:ms.filter(id=>unit(id)===unit(m));
   const segs=seriesOf?rows.map(r=>({r,g:groupOf(r),s:seriesOf(r),v:r[m]||0})):rows.flatMap(r=>stackIds.map(id=>({r,g:groupOf(r),s:name(id),v:r[id]||0})));
   const groups=[...new Set(segs.map(s=>s.g))],series=colorId?members:[...new Set(segs.map(s=>s.s))];
   if(ms.length>stackIds.length)notes.push(ms.filter(id=>!stackIds.includes(id)).map(name).join(', ')+' '+(ms.length-stackIds.length===1?'is':'are')+' in each segment’s hover detail.');
   const spans=groups.map(g=>{const ss=segs.filter(s=>s.g===g);return [ss.reduce((a,s)=>a+Math.min(0,s.v),0),ss.reduce((a,s)=>a+Math.max(0,s.v),0)];});const x=axis(spans.flat());note('Positive and negative amounts stack separately from zero.');const totals=[];
   groups.forEach((g,i)=>{let pos=0,neg=0;const y=80+i*34;text(247,y+18,cut(g),{'text-anchor':'end'});for(const s of segs.filter(s=>s.g===g)){const v=s.v,start=v<0?neg:pos,end=start+v;if(v<0)neg=end;else pos=end;mark('rect',{x:Math.min(x(start),x(end)),y,width:Math.max(1,Math.abs(x(end)-x(start))),height:24,fill:colorId?fillOf(s.s):categoryColour(Math.max(0,series.indexOf(s.s)))},s.r);}totals.push(pos+neg);if(spec.labels)text(Math.max(x(pos),x(neg))+7,y+18,base.format(pos+neg,m,cols,true),{'data-label':'value','pointer-events':'none'});});
   reference(totals,x);legendItems=[series,seriesFills(series)];
  }else if(spec.type==='dot'||spec.type==='series-dot'){
   // V04/V08: Size scales the circle and Colour fills it by member; several measures in one unit are
   // circles on the same row, one colour per measure.
   const ids=colorId?[m]:ms.filter(id=>unit(id)===unit(m)),sizeId=spec.size&&rows.some(r=>typeof r[spec.size]==='number')?spec.size:'',maxSize=Math.max(...rows.map(r=>Math.abs(Number(r[sizeId])||0)),1e-9);
   const radius=r=>sizeId&&typeof r[sizeId]==='number'?4+10*Math.sqrt(Math.abs(r[sizeId])/maxSize):7;
   const x=axis(rows.flatMap(r=>ids.map(id=>r[id])));note('One circle per selected group on a numeric axis; circles are not joined by lines.');
   rows.forEach((r,i)=>{const y=85+i*34;text(248,y+5,cut(label(r)),{'text-anchor':'end'});ids.forEach((id,j)=>{if(r[id]==null)return;const fill=colorId?memberFill(r):ids.length>1?categoryColour(j):spec.type==='series-dot'?categoryColour(i):palette[0];mark('circle',{cx:x(r[id]),cy:y,r:radius(r),fill},r);if(spec.labels)text(Math.min(930,x(r[id])+12),y-10,labelText(r,base.format(r[id],id,cols,true)));});});
   if(ms.length>ids.length)notes.push(ms.filter(id=>!ids.includes(id)).map(name).join(', ')+' '+(ms.length-ids.length===1?'is':'are')+' in each circle’s hover detail.');
   if(sizeId)notes.push('Circle size follows the absolute magnitude of '+name(sizeId)+'.');
   reference(rows.map(r=>r[m]),x);
   if(colorId)legendItems=[members,seriesFills(members)];else if(ids.length>1)legendItems=[ids.map(name),seriesFills(ids)];
  }else if(spec.type==='treemap'){
   // V04: Size, when placed, is the area measure; the printed value stays the first measure.
   const areaId=spec.size&&rows.some(r=>typeof r[spec.size]==='number')?spec.size:m;let weight=r=>Math.abs(Number(r[areaId])||0);
   note(colorId?'Area represents absolute magnitude; colour is '+name(colorId)+'. Values retain their signs.':'Area represents absolute magnitude; orange denotes negative amounts. Values retain their signs.');
   if(!rows.reduce((s,r)=>s+weight(r),0)){weight=()=>1;notes.push('Every value is zero, so each group has an equal area; inspect any box for its value.');}
   if(areaId!==m)notes.push('Area follows '+name(areaId)+'; the printed value is '+name(m)+'.');
   for(const box of partition(rows,20,75,960,450,weight)){if(box.w<.01||box.h<.01)continue;mark('rect',{x:box.x,y:box.y,width:box.w,height:box.h,fill:colorId?memberFill(box.row):box.row[areaId]<0?'#b56b37':'#57447b',stroke:'white','stroke-width':2},box.row);if(box.w>70&&box.h>40){text(box.x+7,box.y+20,cut(label(box.row),Math.floor(box.w/8)-1),{fill:'white','pointer-events':'none'});text(box.x+7,box.y+39,cut(labelText(box.row,base.format(box.row[m],m,cols,true)),Math.floor(box.w/8)-1),{fill:'white','pointer-events':'none'});}}
   if(colorId)legendItems=[members,seriesFills(members)];
  }else if(spec.type==='bubbles'){
   const areaId=spec.size&&rows.some(r=>typeof r[spec.size]==='number')?spec.size:m;
   note(colorId?'Circle area represents absolute magnitude; colour is '+name(colorId)+'.':'Circle area represents absolute magnitude. Orange denotes negative values.');if(areaId!==m)notes.push('Circle area follows '+name(areaId)+'; the printed value is '+name(m)+'.');
   const max=Math.max(...rows.map(r=>Math.abs(r[areaId]||0)),1);let x=25,y=85,rowH=0;for(const r of rows){const radius=Math.max(3,90*Math.sqrt(Math.abs(r[areaId]||0)/max));if(x+2*radius>975){x=25;y+=rowH+12;rowH=0;}mark('circle',{cx:x+radius,cy:y+radius,r:radius,fill:colorId?memberFill(r):r[areaId]<0?'#b56b37':'#57447b','fill-opacity':.85},r);if(radius>28){text(x+radius,y+radius,cut(label(r),Math.floor(radius/4)),{'text-anchor':'middle',fill:'white','pointer-events':'none'});if(spec.labels)text(x+radius,y+radius+17,cut(labelText(r,base.format(r[m],m,cols,true)),Math.floor(radius/4)),{'text-anchor':'middle',fill:'white','font-size':12,'data-label':'value','pointer-events':'none'});}x+=2*radius+9;rowH=Math.max(rowH,2*radius);}if(y+rowH+20>H){H=y+rowH+20;svg.setAttribute('height',H);svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.querySelector('rect').setAttribute('height',H);}
   if(colorId)legendItems=[members,seriesFills(members)];
  }else if(spec.type==='histogram'){
   // V08: bins stack by Colour (or by any other dimension the query carried beside amount_bin).
   note('Counts of individual selected observations. Bins include the lower bound and exclude the upper bound.');
   const other=dims.filter(d=>d!=='amount_bin'),stackOf=colorId?member:other.length?r=>other.map(d=>String(r[d]??'Unknown')).join(' / '):()=>'';
   const width=spec.binWidth||10000,ordered=rows.filter(r=>r.amount_bin!=null&&Number.isFinite(Number(r.amount_bin))).sort((a,b)=>Number(a.amount_bin)-Number(b.amount_bin));if(!ordered.length)throw new Error('No amount bins in the selected groups.');
   const keys=[...new Set(ordered.map(stackOf))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})),stacked=keys.length>1,binTotal=new Map();for(const r of ordered){const b=Number(r.amount_bin);binTotal.set(b,(binTotal.get(b)||0)+(r.records||0));}
   const lo=Number(ordered[0].amount_bin),hi=Number(ordered.at(-1).amount_bin)+width,max=Math.max(...binTotal.values(),1),x=v=>90+(v-lo)/(hi-lo)*850,y=v=>490-v/max*390;
   for(let i=0;i<=5;i++){text(78,y(max*i/5)+4,String(Math.round(max*i/5)),{'text-anchor':'end'});node('line',{x1:90,x2:945,y1:y(max*i/5),y2:y(max*i/5),stroke:'#ece8ef'});text(90+i*170,520,base.format(lo+(hi-lo)*i/5,'amount',cols,true),{'text-anchor':'middle'});}
   const below=new Map();
   for(const r of (stacked?ordered.slice().sort((a,b)=>Number(a.amount_bin)-Number(b.amount_bin)||keys.indexOf(stackOf(a))-keys.indexOf(stackOf(b))):ordered)){const start=Number(r.amount_bin),under=below.get(start)||0,top=under+(r.records||0);below.set(start,top);mark('rect',{x:x(start),y:y(top),width:Math.max(.5,x(start+width)-x(start)-1),height:y(under)-y(top),fill:stacked?(colorId?fillOf(stackOf(r)):categoryColour(keys.indexOf(stackOf(r)))):'#57447b'},r,`${fmt(start,'amount')} to less than ${fmt(start+width,'amount')}; ${r.records} observations; net ${fmt(r.amount,'amount')}`+(stacked?'; '+stackOf(r):''));}
   if(spec.labels)for(const[start,total]of binTotal)text((x(start)+x(start+width))/2,y(total)-5,String(total),{'text-anchor':'middle','font-size':11,'data-label':'value','pointer-events':'none'});
   text(480,548,'Individual observation amount (USD)',{'text-anchor':'middle'});
   reference([...binTotal.values()],y,true,'records',[90,945]);
   if(stacked)legendItems=[keys,seriesFills(keys)];
  }else if(spec.type==='box'){
   note('Quartiles of individual observations; whiskers show the full minimum–maximum range.');const x=axis(rows.flatMap(r=>[r.min_amount,r.max_amount]));
   rows.forEach((r,i)=>{const y=85+i*34,ink=colorId?memberFill(r):'#57447b';text(248,y+7,cut(label(r)),{'text-anchor':'end'});if(r.min_amount==null)return;node('line',{x1:x(r.min_amount),x2:x(r.max_amount),y1:y,y2:y,stroke:ink,'stroke-width':2});for(const id of ['min_amount','max_amount'])node('line',{x1:x(r[id]),x2:x(r[id]),y1:y-8,y2:y+8,stroke:ink});
    // F2: a combined group keeps its minimum and maximum; its quartiles cannot be rebuilt, so the
    // range is drawn dashed and the mark says so instead of drawing a box it does not have.
    if(r.q1_amount==null||r.q3_amount==null||r.median_amount==null){mark('rect',{x:x(r.min_amount),y:y-11,width:Math.max(2,x(r.max_amount)-x(r.min_amount)),height:22,fill:'#ffffff','fill-opacity':0.01,stroke:ink,'stroke-dasharray':'5 3','data-quartiles':'unavailable'},r,BOX.map(id=>name(id)+': '+fmt(r[id],id)).join('; ')+'; quartiles cannot be rebuilt for a combined group');return;}
    mark('rect',{x:x(r.q1_amount),y:y-11,width:Math.max(2,x(r.q3_amount)-x(r.q1_amount)),height:22,...(colorId?{fill:ink,'fill-opacity':.35}:{fill:'#d5c7e7'}),stroke:ink},r,BOX.map(id=>name(id)+': '+fmt(r[id],id)).join('; '));node('line',{x1:x(r.median_amount),x2:x(r.median_amount),y1:y-11,y2:y+11,stroke:'#342649','stroke-width':3,'pointer-events':'none'});if(spec.labels)text(Math.min(945,x(r.max_amount)+8),y+5,labelText(r,base.format(r.median_amount,'median_amount',cols,true)),{'font-size':12,'data-label':'value','pointer-events':'none'});});
   reference(rows.map(r=>r.median_amount),x,false,'median_amount');
   if(colorId)legendItems=[members,seriesFills(members)];
  }else if(spec.type==='gantt'){
   note('Observed first-to-last receipt dates; the bar is a date span, not continuous giving or a project duration.');const ordinal=s=>Date.parse(s+'T00:00:00Z')/86400000,values=rows.flatMap(r=>[ordinal(r.first_date),ordinal(r.last_date)]).filter(Number.isFinite),lo=values.length?Math.min(...values):0,hi=Math.max(...values,lo+1),x=v=>265+(v-lo)/(hi-lo)*675;
   for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;text(x(v),H-25,new Date(v*86400000).toISOString().slice(0,10),{'text-anchor':'middle'});}
   // Marks v3 (#51): Size is a Gantt bar's length, scaled so the largest Size equals the longest observed span.
   const ganttSize=spec.size&&rows.some(r=>typeof r[spec.size]==='number')?spec.size:'',longest=Math.max(3,...rows.filter(r=>r.first_date&&r.last_date).map(r=>x(ordinal(r.last_date))-x(ordinal(r.first_date)))),ganttMax=Math.max(1e-9,...rows.map(r=>Math.abs(Number(r[ganttSize])||0)));
   if(ganttSize)notes.push('Bar length follows '+name(ganttSize)+', scaled so the largest equals the longest observed span; each bar starts at its first observed date and its span stays in its hover detail.');
   rows.forEach((r,i)=>{const y=80+i*34;text(248,y+18,cut(label(r)),{'text-anchor':'end'});if(!r.first_date||!r.last_date)return;const x0=x(ordinal(r.first_date)),x1=ganttSize&&typeof r[ganttSize]==='number'?x0+Math.max(3,longest*Math.abs(r[ganttSize])/ganttMax):x(ordinal(r.last_date));mark('rect',{x:x0,y,width:Math.max(3,x1-x0),height:23,fill:colorId?memberFill(r):'#327a7a'},r,`${r.first_date} to ${r.last_date}; observed span ${r.span_days} days`);if(spec.labels)text(Math.min(945,Math.max(x0+3,x1)+6),y+17,labelText(r,(r.span_days??'—')+' days'),{'font-size':12,'data-label':'value','pointer-events':'none'});});
   if(colorId)legendItems=[members,seriesFills(members)];
  }else if(spec.type==='bullet'){
   // B-E33/V07: a second measure in the same unit is the marker; one measure draws the bars alone;
   // a measure in another unit, or any further measure, is named here and kept in the hover detail.
   const target=ms.slice(1).find(id=>unit(id)===unit(m)),rest=ms.slice(1).filter(id=>id!==target);
   note(target?`${name(m)} (bar) compared with ${name(target)} (marker). The second measure is not an inferred target.`:`${name(m)} (bar). Add a second measure in the same unit to draw a comparison marker.`);
   if(rest.length)notes.push(rest.map(name).join(', ')+(rest.length===1?' is':' are')+' in each bar’s hover detail'+(rest.some(id=>unit(id)!==unit(m))?'; a measure in another unit cannot share the axis':'')+'.');
   const x=axis(rows.flatMap(r=>target?[r[m],r[target]]:[r[m]]));
   rows.forEach((r,i)=>{const y=85+i*34;text(248,y+13,cut(label(r)),{'text-anchor':'end'});if(r[m]!=null)mark('rect',{x:Math.min(x(0),x(r[m])),y,width:Math.max(1,Math.abs(x(r[m])-x(0))),height:20,fill:colorId?memberFill(r):'#57447b'},r);if(target&&r[target]!=null)node('line',{x1:x(r[target]),x2:x(r[target]),y1:y-4,y2:y+24,stroke:'#b87516','stroke-width':3});if(spec.labels&&r[m]!=null)text(Math.min(945,Math.max(x(0),x(r[m]),target&&r[target]!=null?x(r[target]):0)+6),y+15,labelText(r,base.format(r[m],m,cols,true)),{'font-size':12,'data-label':'value','pointer-events':'none'});});
   reference(rows.map(r=>r[m]),x);
   if(colorId)legendItems=[members,seriesFills(members)];
  }else{
   // V01/V07/V08: one or two plotted measures (left and right axis); further measures are hover
   // detail. A first dimension that is not a date, or spec.categorical, places the groups in row
   // order; every further dimension is its own series (solid left axis, dashed right axis).
   const ids=ms.slice(0,2),d=dims[0],serDims=dims.slice(1);
   note(ids.length===2?`${name(ids[0])}: left axis (purple). ${name(ids[1])}: right axis (teal). Independent, labelled scales.`:`${name(m)} on one axis. Add a second measure to draw a right axis.`);
   if(ms.length>2)notes.push(ms.slice(2).map(name).join(', ')+(ms.length===3?' is':' are')+' in each mark’s hover detail.');
   const placeOf=r=>d?String(r[d]??'Unknown'):'All',categorical=!!spec.categorical||!d||rows.every(r=>base.timeValue(r[d],d)==null),places=categorical?[...new Set(rows.map(placeOf))]:null;
   const ordered=rows.map(r=>({r,t:categorical?places.indexOf(placeOf(r)):base.timeValue(r[d],d),s:serDims.map(x=>String(r[x]??'Unknown')).join(' / ')})).filter(o=>o.t!=null).sort((a,b)=>a.t-b.t);
   if(ordered.length<rows.length)notes.push(rows.length-ordered.length+' group'+(rows.length-ordered.length===1?' has':'s have')+' no readable date and '+(rows.length-ordered.length===1?'is':'are')+' left out.');
   const lo=ordered[0].t,hi=ordered.at(-1).t,x=t=>100+(hi===lo?.5:(t-lo)/(hi-lo))*800,series=[...new Set(ordered.map(o=>o.s))],multi=series.length>1,ticks=[...new Map(ordered.map(o=>[o.t,categorical?places[o.t]:o.r[d]])).entries()];
   const slot=Math.min(32,660/ticks.length),sub=slot/series.length;
   ids.forEach((id,j)=>{const[a,b]=base.extent(rows.map(r=>r[id])),y=v=>470-(v-a)/(b-a)*370;for(let i=0;i<=5;i++)text(j?925:85,y(a+(b-a)*i/5)+4,base.format(a+(b-a)*i/5,id,cols,true),{'text-anchor':j?'start':'end',fill:multi?'#342649':palette[j]});
    series.forEach((s,k)=>{const ink=multi?categoryColour(k):palette[j];let previous=null;for(const o of ordered){if(o.s!==s)continue;const v=o.r[id];if(v==null){previous=null;continue;}
     if(spec.type==='combination'&&!j)mark('rect',{x:x(o.t)-slot/2+k*sub,y:Math.min(y(0),y(v)),width:sub,height:Math.max(1,Math.abs(y(v)-y(0))),fill:ink},o.r);
     else {if(previous&&o.t===previous.t+1)node('line',{x1:x(previous.t),x2:x(o.t),y1:y(previous.r[id]),y2:y(v),stroke:ink,'stroke-width':2,...(multi&&j?{'stroke-dasharray':'6 4'}:{})});mark('circle',{cx:x(o.t),cy:y(v),r:4.5,fill:ink},o.r);}
     if(spec.labels)text(x(o.t),y(v)-8,labelText(o.r,base.format(v,id,cols,true)),{'text-anchor':'middle','font-size':11,'data-label':'value','pointer-events':'none'});previous=o;}});});
   // spec.axisLabels 'all' (census B-E21): every tick is labelled, turned when they would collide.
   const turnTicks=spec.axisLabels==='all'&&ticks.length>8;
   ticks.forEach(([t,s],i)=>{if(turnTicks)text(x(t),492,cut(s,12),{'text-anchor':'end','font-size':11,transform:`rotate(-35 ${x(t)} 492)`,'data-axis-label':'all'});else if(i%Math.max(1,Math.ceil(ticks.length/8))===0)text(x(t),505,cut(s,18),{'text-anchor':'middle'});});
   // V06: the mean and median of the first measure's shown marks, on its (left) axis.
   if(refs){const[a0,b0]=base.extent(rows.map(r=>r[ids[0]]));reference(ordered.map(o=>o.r[ids[0]]),v=>470-(v-a0)/(b0-a0)*370,true,ids[0],[100,900]);}
   if(multi){legendItems=[series,seriesFills(series)];if(ids.length===2)notes.push('Solid marks read on the left axis; dashed lines on the right axis.');}
  }
  if(legendItems)legend(...legendItems);
  // Marks v3: which shape each member of a Shape field takes.
  if(shadeRange){const top=grow(34),g=node('g',{'data-legend':'colour','aria-label':'Colour legend'});text(22,top+20,name(spec.colorMeasure),{'font-size':12,fill:'#59645c'},g);
   node('rect',{x:200,y:top+9,width:12,height:12,fill:palette[0],'fill-opacity':.25},null,g);text(218,top+20,base.format(shadeRange[0],spec.colorMeasure,cols,true),{'font-size':12,fill:'#59645c'},g);
   node('rect',{x:330,y:top+9,width:12,height:12,fill:palette[0],'fill-opacity':1},null,g);text(348,top+20,base.format(shadeRange[1],spec.colorMeasure,cols,true),{'font-size':12,fill:'#59645c'},g);}
  if(glyphing&&spec.shape&&shapeMembers.length){const top=grow(30+Math.ceil(shapeMembers.length/5)*22),g=node('g',{'data-legend':'shape','aria-label':'Shape legend'});text(22,top+14,name(spec.shape),{'font-size':12,fill:'#59645c'},g);
   shapeMembers.forEach((m,i)=>{const x=22+(i%5)*180,y=top+34+Math.floor(i/5)*22,id=base.glyphFor(spec,m,shapeMembers);node('path',{d:base.glyphD(id,x+6,y-4,6),...(base.glyphOpen(id)?{fill:'none',stroke:'#59645c','stroke-width':1.5}:{fill:'#59645c'})},null,g);text(x+17,y,cut(m,20),{'font-size':12,fill:'#59645c'},g);});}
  for(const s of notes){const top=grow(20);text(22,top+14,s,{'font-size':12,fill:'#645d6d','data-note':'chart'});}
  container.append(svg);return svg;
 }
 const api={TYPES,render,validate,partition,combine};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MentatContributionCharts=api;
})(globalThis);
