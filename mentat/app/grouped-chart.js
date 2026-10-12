/* A shared SVG view: nulls remain gaps and refunds retain their sign. */
// r3 (ENGINE CONTRACT W3, W4, W6-W8): result.stack stacks the series of a group
// ('stacked' from zero, 'percent' as shares of the group's positive total);
// result.dual draws result.dual.right against a second value axis as
// result.dual.mark; multiarea and multidot join multiline; values[].size,
// .extra and .labelValue reach the marks; options.labels writes a value on
// every mark. A result without these fields draws exactly as before.
globalThis.MentatGroupedSVG = (result,title,note='',options) => {
  const opts=options||{};
  const stack=['stacked','percent'].includes(result.stack)?result.stack:null, percent=stack==='percent';
  const dual=result.dual&&Array.isArray(result.dual.right)&&result.dual.right.length?result.dual:null;
  const ns='http://www.w3.org/2000/svg', horizontal=result.type==='groupedhorizontal'&&!dual, colors=MentatTheme.palette;
  // Paul, 2026-09-24: series take the theme's twelve categorical colours, tinted
  // past twelve (theme.js categoryColour), as a pie's groups do, so series i is
  // the same colour in both. Lines past the plain slots (the tints) are dashed,
  // as lines past a repeated colour were. A theme without the categorical slots
  // cycles the palette exactly as before.
  const categorical=typeof MentatTheme.categoryColour==='function'&&Array.isArray(MentatTheme.categorical);
  const colorOf=j=>categorical?MentatTheme.categoryColour(j):colors[j%colors.length];
  const dashedFrom=categorical?MentatTheme.categorical.length:colors.length;
  const lineLike=['multiline','multiarea','multidot'].includes(result.type);
  const count=result.groups.length, series=result.series.length;
  const plotHeight=horizontal?Math.max(280,count*(stack?52:24*series+28)):340;
  const plotWidth=horizontal?800:Math.max(800,count*Math.max(70,stack?0:series*16));
  const width=plotWidth+220+(dual?70:0), height=plotHeight+180+series*24;
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',title);
  const add=(tag,attrs={},text)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;svg.append(n);return n;};
  const label=(x,y,text,attrs={})=>add('text',{x,y,fill:MentatTheme.ink,'font-family':MentatTheme.sans,'font-size':12,...attrs},text);
  const tip=(node,text)=>{const n=document.createElementNS(ns,'title');n.textContent=text;node.append(n);};
  const wrapped=(x,y,text,characters,attrs={})=>{
    const words=String(text).split(/\s+/),lines=[];let line='';
    for(const word of words){if(line&&(line+' '+word).length>characters){lines.push(line);line='';}line+=(line?' ':'')+word;}
    if(line)lines.push(line);
    const shown=lines.slice(0,2).map(value=>value.length>characters?value.slice(0,characters-1)+'…':value);
    if(lines.length>2)shown[1]=shown[1].slice(0,characters-1)+'…';
    const node=label(x,y,'',attrs);
    shown.forEach((value,index)=>{const span=document.createElementNS(ns,'tspan');span.setAttribute('x',x);span.setAttribute('dy',index?'1.25em':0);span.textContent=value;node.append(span);});
    tip(node,text);return node;
  };
  add('title',{},title);add('desc',{},note);add('rect',{width,height,fill:'#fff'});wrapped(20,28,title,Math.floor((width-40)/10),{'font-size':18});
  // Which series sit on the left axis (all of them without a dual axis).
  const right=new Set(dual?dual.right:[]),onLeft=j=>!right.has(j);
  const shown=v=>v.value===null?null:percent?(v.share==null?null:v.share*100):v.value;
  // Stacked extents: each group's positive and negative running totals.
  const stacks=stack?result.groups.map(g=>{let pos=0,neg=0;return g.values.map((v,j)=>{const s=onLeft(j)?shown(v):null;if(s===null)return null;const start=s<0?neg:pos,end=start+s;if(s<0)neg=end;else pos=end;return [start,end];});}):null;
  const values=stacks?stacks.flatMap(list=>list.filter(Boolean).flat()):result.groups.flatMap(g=>g.values.filter((v,j)=>onLeft(j)).map(v=>v.value).filter(v=>v!==null));
  const low=Math.min(0,...values),high=Math.max(0,...values),range=high-low||1;
  const scale=v=>horizontal?200+(v-low)/range*plotWidth:70+plotHeight-(v-low)/range*plotHeight;
  // Value-axis geometry for callers that overlay reference lines on this view.
  svg.__plot={orient:horizontal?'horizontal':'vertical',min:low,max:high,p0:scale(low),p1:scale(high),
    from:horizontal?60:150,to:horizontal?70+plotHeight:150+plotWidth};
  for(let i=0;i<=5;i++){
    const value=low+range*i/5,p=scale(value),text=value.toLocaleString('en-US',{maximumFractionDigits:2})+(percent?'%':'');
    add('line',horizontal?{x1:p,x2:p,y1:60,y2:70+plotHeight,stroke:MentatTheme.grid}:{x1:150,x2:150+plotWidth,y1:p,y2:p,stroke:MentatTheme.grid});
    if(horizontal)label(p,90+plotHeight,text,{'text-anchor':'middle'});else label(140,p+4,text,{'text-anchor':'end'});
  }
  // The second axis (W4): its own scale, ticks start-anchored right of the plot.
  let rightScale=scale;
  if(dual){
    const rv=result.groups.flatMap(g=>g.values.filter((v,j)=>right.has(j)).map(v=>v.value).filter(v=>v!==null));
    const rlow=Math.min(0,...rv),rhigh=Math.max(0,...rv),rrange=rhigh-rlow||1;
    rightScale=v=>70+plotHeight-(v-rlow)/rrange*plotHeight;
    add('line',{x1:150+plotWidth,x2:150+plotWidth,y1:70,y2:70+plotHeight,stroke:MentatTheme.ink,'stroke-width':1,'data-axis':'right'});
    for(let i=0;i<=5;i++){const value=rlow+rrange*i/5;label(150+plotWidth+8,rightScale(value)+4,value.toLocaleString('en-US',{maximumFractionDigits:2}),{'text-anchor':'start','data-axis-tick':'right'});}
  }
  const step=horizontal?plotHeight/count:plotWidth/count;
  // Size (W7): bar thickness, line width and dot radius follow |size| / max.
  const sizes=result.groups.flatMap(g=>g.values.map(v=>Math.abs(Number(v.size))).filter(Number.isFinite));
  const maxSize=sizes.length?Math.max(...sizes)||1:0;
  const sizeShare=v=>maxSize&&Number.isFinite(Number(v?.size))?Math.abs(Number(v.size))/maxSize:null;
  const fmt=n=>Number(n).toLocaleString('en-US',{maximumFractionDigits:3});
  const detail=v=>(percent&&v.share!=null?` (${(v.share*100).toFixed(1)}%)`:'')+(v.forecast?' (same-phase baseline)':v.partial?' (partial period)':'')+
    (result.sizeLabel&&v.size!=null?`\n${result.sizeLabel}: ${fmt(v.size)}`:'')+
    (v.extra?Object.entries(v.extra).map(([k,x])=>`\n${k}: ${x==null?'—':typeof x==='number'?fmt(x):x}`).join(''):'');
  const markLabel=(x,y,v,attrs={})=>{if(!opts.labels)return;const text=v.labelValue!=null?String(typeof v.labelValue==='number'?fmt(v.labelValue):v.labelValue):percent&&v.share!=null?(v.share*100).toFixed(1)+'%':fmt(v.value);
    label(x,y,text,{'font-size':10,'text-anchor':'middle','data-mark-label':'true',...attrs});};
  // Bars share a group's slot: one per left series (one when stacked), plus
  // one per right series drawn as bars.
  const leftSeries=result.series.map((_,j)=>j).filter(onLeft),rightBars=dual&&dual.mark==='bar'?dual.right:[];
  const slots=(stack?1:leftSeries.length)+rightBars.length||1;
  const slotOf=j=>right.has(j)?(stack?1:leftSeries.length)+rightBars.indexOf(j):stack?0:leftSeries.indexOf(j);
  const areaPoints=result.type==='multiarea'?result.series.map(()=>[]):null;
  result.groups.forEach((g,i)=>{
    if(horizontal)wrapped(190,80+i*step,g.label,26,{'text-anchor':'end','data-group-label':'true'});
    else wrapped(150+step*(i+.5),90+plotHeight,g.label,24,{'text-anchor':'end','data-group-label':'true',transform:`rotate(-30 ${150+step*(i+.5)} ${90+plotHeight})`});
    g.values.forEach((v,j)=>{
      if(v.value===null)return;
      const color=colorOf(j),text=`${g.label} · ${v.series}: ${v.value.toLocaleString('en-US',{maximumFractionDigits:3})}`+detail(v);
      const onRight=right.has(j),yScale=onRight?rightScale:scale,segment=stacks&&!onRight?stacks[i][j]:null;
      const at=segment?segment[1]:onRight?v.value:shown(v);
      const asLine=onRight?dual.mark==='line':lineLike;
      if(result.type==='multiline'&&!dual&&!stack){
        const x=150+step*(i+.5),y=scale(v.value),previous=result.groups[i-1]?.values[j],share=sizeShare(v),prevShare=sizeShare(previous);
        const stroke=share===null?{}:{'stroke-width':1.5+4.5*((share+(prevShare??share))/2)};
        if(previous?.value!=null)add('line',{x1:x-step,x2:x,y1:scale(previous.value),y2:y,stroke:color,'stroke-width':2,'stroke-dasharray':j>=dashedFrom?'5 3':'none',...stroke});
        tip(add('circle',{cx:x,cy:y,r:4,fill:v.forecast?'#fff':color,...(v.forecast?{stroke:color}:{})}),text);markLabel(x,y-8,v);
      }else if(asLine){
        // Lines, areas and dots against their own axis; stacked ends when stacked.
        const x=150+step*(i+.5),y=yScale(at),prev=result.groups[i-1],pv=prev?.values[j],pSeg=stacks&&!onRight&&prev?stacks[i-1][j]:null;
        const pAt=pv==null||pv.value===null?null:pSeg?pSeg[1]:onRight?pv.value:shown(pv);
        const share=sizeShare(v),prevShare=sizeShare(pv);
        if(areaPoints&&!onRight)areaPoints[j].push({i,x,top:y,base:yScale(segment?segment[0]:0)});
        if(result.type!=='multidot'||onRight){
          if(pAt!=null)add('line',{x1:x-step,x2:x,y1:yScale(pAt),y2:y,stroke:color,'stroke-width':share===null?2:1.5+4.5*((share+(prevShare??share))/2),'stroke-dasharray':j>=dashedFrom?'5 3':'none'});
        }
        const r=result.type==='multidot'&&share!==null?3+12*Math.sqrt(share):result.type==='multidot'?6:4;
        tip(add('circle',{cx:x,cy:y,r,fill:v.forecast?'#fff':color,...(v.forecast?{stroke:color}:{}),...(result.type==='multidot'?{'fill-opacity':.85}:{})}),text);markLabel(x,y-r-4,v);
      }else if(horizontal){
        const share=sizeShare(v),f=share===null?1:.35+.65*Math.sqrt(share);
        if(segment){const h=Math.min(28,step*.55)*f,y=85+i*step+(Math.min(28,step*.55)-h)/2,x0=scale(segment[0]),x1=scale(segment[1]);
          tip(add('rect',{x:Math.min(x0,x1),y,width:Math.max(.5,Math.abs(x1-x0)),height:h,fill:color,stroke:'#fff','stroke-width':1}),text);if(shown(v))markLabel((x0+x1)/2,y+h/2+3,v,{fill:'#fff'});}
        else{const h=18*f,y=85+i*step+j*24+(18-h)/2;
          tip(add('rect',{x:Math.min(scale(0),scale(v.value)),y,width:Math.max(.5,Math.abs(scale(v.value)-scale(0))),height:h,fill:color}),text);markLabel(Math.max(scale(0),scale(v.value))+4,y+h/2+3,v,{'text-anchor':'start'});}
      }else{
        const w=step*.8/(stack||dual?slots:series),share=sizeShare(v),f=share===null?1:.35+.65*Math.sqrt(share);
        const slot=stack||dual?slotOf(j):j,x=150+i*step+step*.1+slot*w;
        if(segment){const y0=yScale(segment[0]),y1=yScale(segment[1]),bw=Math.max(1,w-2)*f;
          tip(add('rect',{x:x+(Math.max(1,w-2)-bw)/2,y:Math.min(y0,y1),width:bw,height:Math.max(.5,Math.abs(y1-y0)),fill:color,stroke:'#fff','stroke-width':1}),text);if(shown(v))markLabel(x+w/2,(y0+y1)/2+3,v,{fill:'#fff'});}
        else if(share!==null||onRight||v.forecast){const bw=Math.max(1,w-2)*f,y0=yScale(0),y1=yScale(v.value);
          tip(add('rect',{x:x+(Math.max(1,w-2)-bw)/2,y:Math.min(y0,y1),width:bw,height:Math.max(.5,Math.abs(y1-y0)),fill:color,...(v.forecast?{'fill-opacity':.45}:{})}),text);markLabel(x+w/2,Math.min(y0,y1)-4,v);}
        else{tip(add('rect',{x,y:Math.min(scale(0),scale(v.value)),width:Math.max(1,w-2),height:Math.max(.5,Math.abs(scale(v.value)-scale(0))),fill:color}),text);markLabel(x+w/2,Math.min(scale(0),scale(v.value))-4,v);}
      }
    });
  });
  // Areas (W6): one filled band per unbroken run of a series.
  if(areaPoints)areaPoints.forEach((points,j)=>{
    let run=[];const flush=()=>{if(run.length){const band=document.createElementNS(ns,'polygon');
      for(const [k,v] of Object.entries({points:[...run.map(p=>`${p.x},${p.top}`),...run.slice().reverse().map(p=>`${p.x},${p.base}`)].join(' '),fill:colorOf(j),'fill-opacity':stack?.55:.18,stroke:'none','data-series':result.series[j]}))band.setAttribute(k,v);
      // Under the lines and dots, which were drawn first.
      svg.insertBefore(band,svg.querySelector('circle'));}run=[];};
    points.forEach((p,k)=>{if(k&&p.i!==points[k-1].i+1)flush();run.push(p);});flush();
  });
  result.series.forEach((name,i)=>{add('rect',{x:20,y:plotHeight+160+i*24,width:12,height:12,fill:colorOf(i)});label(42,plotHeight+171+i*24,name+(right.has(i)?' (right axis)':''));});
  return svg;
};
