/* Original Chart Studio shelves -> verified server query. No worksheet sums. */
(function(root){
  'use strict';
  const core=typeof module!=='undefined'&&module.exports?require('./chart-studio-core.js'):root.MentatStudioCore;
  const MONEY=new Set(['amount','gross_amount','refunds','average_amount','median_amount','min_amount','max_amount','q1_amount','q3_amount','stddev_amount']);
  const BASIS={receipt:['Published included receipts','Matches the workbook’s included receipts. Economic uniqueness remains unresolved; intermediary stages may overlap.'],original_observed:['Observed original giving (provisional)','Evidence-aware original giving. Ambiguous observations remain provisional.'],original_supported:['Supported original giving (gross subset)','Positive amounts supported by the current evidence rules. Refunds are excluded; this is a partial gross subset.'],raw:['Raw source observations','Includes excluded and repeated source representations. This is not a giving total.'],pac_activity:['PAC activity on recipient receipts','Recipient-reported activity is separate from original giving and is not a complete spending ledger.']};
  const AGG={sum:'amount',mean:'average_amount',count:'records',median:'median_amount',min:'min_amount',max:'max_amount'};
  const EXTRA_MARKS=[['heatmap','Heat map'],['highlight','Highlight table'],['stacked','Stacked bars'],['dot','Circle view'],['series-dot','Side-by-side circles'],['treemap','Treemap'],['bubbles','Packed bubbles'],['gantt','Observed date span'],['bullet','Bullet comparison'],['combination','Dual combination'],['dual-line','Dual lines'],['filled-map','Filled state map']];
  const types=new Set(['automatic','bar','line','area','circle','text','map','pie','doughnut','waterfall','pareto','radar','histogram','box',...EXTRA_MARKS.map(m=>m[0]),
    // Marks v3 (lane M, boards #50-#52).
    'square','shape','polygon','density']);
  const TIME=['date','year','cycle','quarter','month'];
  // Board S1's Data pane, by catalog id and in the board's order: Industry, Election cycle,
  // Contribution year, Company, Contribution date, Recipient, Recipient category, Party, State, ZIP;
  // Selected amount, Gift count (an observation is not always one gift, so it keeps its name
  // Observation count) and Contributors (distinct reported donor identities). S1's Candidate and
  // Net receipts have no field of their own: the catalog carries no candidate name, and the net
  // amount of the chosen basis is the field shown as Selected amount.
  const PANE_FIELDS={dimensions:['industry','cycle','year','company','date','recipient','recipient_category','party','state','zip5'],measures:['amount','records','contributors']};
  function fields(catalog,basis='receipt'){
    return catalog.map((f,index)=>({...f,index,name:f.id==='amount'?(basis==='original_supported'?'Supported gross amount':'Selected amount'):f.label||f.name||f.id,type:f.type||(f.role==='measure'?'number':f.id==='date'?'date':'category'),is_time:TIME.includes(f.id),visible:true,unit:f.unit}));
  }
  const RULES={'heat-map':['heatmap',2,1],'highlight-table':['highlight',2,1],'stacked-bars':['stacked',2,1],'circle-views':['dot',1,1],'side-by-side-circles':['series-dot',2,1],'treemap':['treemap',1,1],'packed-bubbles':['bubbles',1,1],'histogram':['histogram',0,1],'box-and-whisker':['box',0,1],'gantt':['gantt',1,0],'bullet-graph':['bullet',0,2],'dual-combination':['combination',1,2,true],'dual-lines':['dual-line',1,2,true]};
  // Paul, 2026-09-23: "...all graph types properly register and nothing is logically constrained".
  // Every tile is enabled on verified data as on a worksheet: a rule is what a tile prefers, `fits`
  // says whether the shelves hold it, and a click draws with what is there.
  const fill=missing=>missing.length?'Draws with the fields on the shelves and picks '+missing.join(' and ')+' from the source':'';
  function showMeEligibility(selection){
    return core.showMeEligibility(selection).map(tile=>{
      if(RULES[tile.id]){const[type,d,m,time]=RULES[tile.id],missing=[];if(selection.dimensions<d)missing.push((d-selection.dimensions)+' dimension'+(d-selection.dimensions===1?'':'s'));if(selection.measures<m)missing.push((m-selection.measures)+' measure'+(m-selection.measures===1?'':'s'));if(time&&!selection.times)missing.push('a date dimension');return {...tile,mark:type,enabled:true,reason:'',fits:!missing.length,missing,fill:fill(missing),requirement:`${d} or more Dimensions, ${m} or more Measures`,note:['histogram','box'].includes(type)?'Computed from individual selected observation amounts on the server.':type==='gantt'?'First-to-last observed receipt dates, not continuous giving.':type==='bullet'?'Two measures with the same unit; the second is a comparison, not an inferred target.':type==='dot'?'One circle per group at its value.':type==='series-dot'?'One circle series per Colour member.':'Uses the selected source groups; no repeated aggregation of subtotals.'};}
      if(tile.id==='scatter-plot'){const missing=selection.measures<2?[(2-selection.measures)+' measure'+(2-selection.measures===1?'':'s')]:[];return {...tile,enabled:true,reason:'',fits:!missing.length,missing,fill:fill(missing),requirement:'0 or more Dimensions, 2 plotted Measures',note:'Compares the first two selected measures as circles; one measure draws one circle per group.'};}
      if(['symbol-map','filled-map'].includes(tile.id))return {...tile,enabled:true,reason:'',fits:true,missing:[],fill:'',requirement:'Contributor ZIP or State, summed Selected amount',note:'Creates verified ZIP/state marks with optional state and congressional boundaries.'};
      return tile;
    });
  }
  // A tile changes the mark and keeps every pill, filter, aggregate, encoding and analysis (census
  // B-S28). Empty shelves are seeded from the fields in use, then from the source.
  // Show Me draws a view of its own: one card keeps its v3 fields and a dual axis
  // collapses to the mark the tile chose (core.adoptMarks).
  function showMeApply(tileId,state,columns,selected){return core.adoptMarks(showMeApplyV2(tileId,state,columns,selected));}
  function showMeApplyV2(tileId,state,columns,selected){
    if(['symbol-map','filled-map'].includes(tileId)){
      const next=structuredClone(state);next.marks={...next.marks,type:tileId==='filled-map'?'filled-map':'map'};return next;
    }
    const rule=RULES[tileId];
    if(!rule)return core.showMeApply(tileId,state,columns,selected);
    const next=structuredClone(state);next.marks={...next.marks,type:rule[0]};if(next.marks.orientation)delete next.marks.orientation;
    if(core.shelf(state.columns).length||core.shelf(state.rows).length)return next;
    const selection=core.showMeSelection(state,columns,selected),meta=core.classifyFields(columns,state.types),p=f=>({column:f.index,aggregation:'sum'});
    const inUse=selection.fields,dims=[...inUse.filter(f=>f.role==='dimension'),...meta.dimensions.filter(f=>!inUse.includes(f))],measures=[...inUse.filter(f=>f.role==='measure'),...meta.measures.filter(f=>!inUse.includes(f))];
    const times=dims.filter(f=>f.isTime),plain=dims.filter(f=>!f.isTime);
    next.columns=(rule[3]?(times.length?times:plain).slice(0,1):rule[0]==='histogram'?[]:[...plain,...times].slice(0,rule[1])).map(p);
    next.rows=(['histogram','box','gantt'].includes(rule[0])?[columns.find(f=>f.id==='amount')]:measures.slice(0,Math.max(1,rule[2]))).filter(Boolean).map(p);
    if(rule[0]==='histogram'&&columns.some(f=>f.id==='amount_bin')){next.columns=[p(columns.find(f=>f.id==='amount_bin'))];next.rows=[p(columns.find(f=>f.id==='records'))];}
    return next;
  }
  function filterValue(value,field){
    if(field.unit==='date'){if(!/^\d{4}-\d{2}-\d{2}$/.test(String(value)))throw new Error('Enter an ISO date, YYYY-MM-DD.');return String(value);}
    if(field.role!=='measure')return String(value??'');
    if(!/^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(value).trim()))throw new Error('Enter a number without currency symbols or commas.');
    const number=Number(value),converted=MONEY.has(field.id)?number*100:number,result=Math.round(converted);
    if(!Number.isSafeInteger(result)||Math.abs(converted-result)>0.00001)throw new Error(MONEY.has(field.id)?'Use at most two decimal places for money.':'Use a whole observation count.');
    return result;
  }
  // `notes`, when given, turns the two composition refusals into notes (census B-S35, B-S36): a list
  // over 1,000 members sends its first 1,000, and text matching on a number is dropped.
  function clauses(filter,field,notes){
    const op=filter.op,values=Array.isArray(filter.value)?filter.value:[filter.value];
    if(op==='in'||op==='notin'){
      let chosen=values.filter(v=>v!==''&&v!=null).map(v=>filterValue(v,field));
      if(!chosen.length)return [];
      if(chosen.length>1000){if(!notes)throw new Error('Choose at most 1,000 members, or use Is not one of.');notes.push(field.name+' filter sends its first 1,000 of '+chosen.length+' members; use Is not one of to keep every member.');chosen=chosen.slice(0,1000);}
      return op==='in'?[{field:field.id,op:'in',value:chosen}]:chosen.map(value=>({field:field.id,op:'ne',value}));
    }
    if(op==='between'){
      const out=[];
      if(values[0]!==''&&values[0]!=null)out.push({field:field.id,op:'gte',value:filterValue(values[0],field)});
      if(values[1]!==''&&values[1]!=null)out.push({field:field.id,op:'lte',value:filterValue(values[1],field)});
      return out;
    }
    const simple={equals:'eq',contains:'contains',gte:'gte',lte:'lte'}[op];
    if(!simple)throw new Error('Choose a supported filter operation.');
    if(field.role==='measure'&&simple==='contains'){if(!notes)throw new Error('Numeric filters use Equals, At least, At most or Between.');notes.push(field.name+' cannot be matched as text, so its Contains filter is left out; use Equals, At least, At most or Between.');return [];}
    if(filter.value===''||filter.value==null)return [];
    return [{field:field.id,op:simple,value:filterValue(filter.value,field)}];
  }
  /* ------------------------------------------- what the sort controls do
     One account, read by compile() below and by the toolbar, so a button can
     never claim something the query does not do.

     sheet().sort is none | asc | desc | az | za and opens at 'none'
     (chart-studio-core.js createSheet). Paul, 2026-09-23: "nothing is logically
     constrained" - so every mark sorts (census B-S16). 'none' is the view's own
     order: chronological when the first dimension is a time axis (or the
     histogram's bins), otherwise largest first, which is why this path has
     always read 'none' as descending there. asc and desc order the groups by
     the first measure on every mark; az and za by the first dimension's label. */
  const effectiveSort=state=>(state&&state.sort)==='asc'?'asc':'desc';
  const SORT_LOCK_TIME='Time views keep chronological order';
  const SORT_LOCK_BINS='A histogram keeps its bins in numeric order';
  const CHRONOLOGICAL=['line','area','combination','dual-line'];
  const ORDERED=['date','year','cycle','quarter','month','amount_bin'];
  const orderedAxis=dimensions=>ORDERED.includes((dimensions||[])[0]);
  function querySort(state,dimensions,measures){
    const stored=(state&&state.sort)||'none',first=(dimensions||[])[0];
    if((stored==='az'||stored==='za')&&first)return [{field:first,direction:stored==='az'?'asc':'desc'}];
    if(stored==='asc'||stored==='desc')return [{field:measures[0],direction:stored}];
    return orderedAxis(dimensions)?[{field:first,direction:'asc'}]:[{field:measures[0],direction:'desc'}];
  }
  // Retired (census B-S16): no mark refuses to be sorted. Kept for older callers, answering "no lock".
  function sortLock(){return '';}
  /* Paul, 2026-09-23: "nothing is logically constrained". The None button used to be disabled
     wherever 'none' asked the server the same question as Descending (a category axis, largest
     first). A stored 'none' keeps that meaning - it is every new sheet's and every saved view's
     order - and None now stores 'source': the same query (the page holds the largest groups),
     listed client-side in the groups' own order, A to Z with numbers ascending. On a time axis or
     a histogram's bins 'source' is the chronological/bin order 'none' already is. */
  const naturalOrder=(state,dimensions)=>(state&&state.sort)==='source'&&(dimensions||[]).length>0&&!orderedAxis(dimensions);
  function naturalRows(rows,dimensions){
    const dims=dimensions||[];
    return (rows||[]).map((row,at)=>({row,at})).sort((a,b)=>{for(const d of dims){const c=String(a.row[d]??'').localeCompare(String(b.row[d]??''),undefined,{numeric:true});if(c)return c;}return a.at-b.at;}).map(item=>item.row);
  }

  /* -------------------------------------------- which marks can be labelled
     Every mark type this path accepts or produces. Paul's words above: every
     renderer draws value labels (engine contract V05, W8), so every entry is
     `true`. A table's marks are its value cells, so its Labels button shows or
     hides the printed values (hidden values stay on hover and in the CSV), the
     way a text table reads with mark labels off; it is never disabled.
     app/tests/chart-studio-contributions-sort.test.js fails if a producible
     type is missing from the table or a stale one is left in it. */
  const LABEL_SUPPORT={
    automatic:true,bar:true,line:true,area:true,circle:true,scatter:true,
    heatmap:true,dot:true,'series-dot':true,stacked:true,histogram:true,bubbles:true,
    highlight:true,treemap:true,box:true,gantt:true,bullet:true,combination:true,'dual-line':true,
    pie:true,doughnut:true,waterfall:true,pareto:true,radar:true,map:true,'filled-map':true,
    text:true,table:true,
    // Marks v3: Density draws concentration, not single marks.
    square:true,shape:true,polygon:true,density:'Density draws how concentrated the marks are, so it has no mark labels; Circle, Square and Shape label each mark.'
  };
  // Period analysis keeps its labels too (contract W8: charts.js draws them).
  const LABELLED_TRANSFORMS=['none','annual','yoy','change','forecast'];
  const LABEL_LOCK_TRANSFORM='Period analysis is drawn on the report canvas, which has no mark labels';
  function labelLock(type){
    const support=LABEL_SUPPORT[type];
    if(support===true)return '';
    return support||'This mark type does not draw labels';
  }

  // Paul, 2026-09-23: "Additionally, within the Chart Studio make sure everything is freely
  // flexible, that all graph types properly register and nothing is logically constrained, it
  // needs to be utterly maluable." compile() throws only when no field at all is placed (and for a
  // mark id no menu offers); every other refusal became a note, and the query asks for what the
  // server can answer (engine contract, section G-V).
  const TIMELINE=['line','area','combination','dual-line'];
  // Marks v3 (lane M): the Colour measure, Size, Label and Tooltip of a verified
  // pie, doughnut, waterfall, Pareto or radar reach its drawing. Each group
  // carries its own labels (charts.js liftEncodings lends them to the result).
  // `format` is contribution-plot.js format; money stays in cents until here.
  function canvasEncodings(group,row,spec,nameOf,format){
    const value=id=>typeof row[id]!=='number'?row[id]??null:row[id]/(MONEY.has(id)?100:1);
    if(spec.colorMeasure&&typeof row[spec.colorMeasure]==='number'){group.colorValue=value(spec.colorMeasure);group.colorLabel=nameOf(spec.colorMeasure);}
    if(spec.size&&typeof row[spec.size]==='number'){group.size=value(spec.size);group.sizeLabel=nameOf(spec.size);}
    if(spec.labelFields&&spec.labelFields.length){group.labelValue=spec.labelFields.map(id=>typeof row[id]==='number'&&format?format(row[id],id):String(row[id]??'')).join(' / ');group.labelLabel=spec.labelFields.map(nameOf).join(' / ');}
    for(const id of spec.tooltip||[])if(row[id]!==undefined){group.extra=group.extra||{};group.extra[nameOf(id)]=value(id);}
    return group;
  }
  // Marks v3, a dual axis (#52). The shelves compile once through the one-card
  // path - bars grouped by the axis and every card's dimensions, with every other
  // card's measure and every measure a card encodes asked for too - and the spec
  // then names each card's measure and mark (spec.type 'cards').
  function compileCards(state,columns,options,cards){
    const byIndex=new Map(columns.map(f=>[f.index,f])),role=p=>(p&&byIndex.get(p.column)||{}).role||'';
    const proxy=JSON.parse(JSON.stringify(state));delete proxy.marksCards;delete proxy.syncAxes;
    const measures=cards.map(c=>c.measure).filter(Boolean),dims=[],carried=[];
    const keep=(list,p)=>{if(p&&!list.some(q=>q.column===p.column&&q.aggregation===p.aggregation))list.push({column:p.column,aggregation:p.aggregation});};
    for(const c of cards){const e=c.encodings,shape=e.shape&&e.shape.column!=null?{column:e.shape.column,aggregation:e.shape.aggregation}:null;
      for(const p of [e.color,e.size,e.label,e.path,shape,...e.detail,...e.tooltip])if(p&&role(p))keep(role(p)==='dimension'?dims:carried,p);}
    proxy.rows=[...core.shelf(state.rows).filter(p=>role(p)!=='measure'),...measures.slice(0,1)];
    proxy.marks={type:'bar',color:null,size:null,label:null,detail:dims,tooltip:[...measures.slice(1),...carried]};
    proxy.analytics={...(proxy.analytics||{}),transform:'none'};
    const compiled=compile(proxy,columns,options);
    const idOf=p=>{const f=p&&byIndex.get(p.column);if(!f)return '';return f.role==='measure'?(f.id==='amount'?(AGG[p.aggregation]||'amount'):f.id):f.id;};
    const timeAxis=TIME.includes(compiled.spec.dimensions[0]);
    const notes=compiled.notes.filter(note=>!/^Detail, Label and Tooltip dimensions/.test(note));
    if(state.analytics&&state.analytics.transform&&state.analytics.transform!=='none')notes.push('A dual axis draws each measure with its own mark, so period analysis is left off.');
    if(dims.length)notes.push('The cards share one level of detail: '+[...new Set(dims.map(p=>(byIndex.get(p.column)||{}).name))].join(' / ')+' splits every card’s marks.');
    const spec=compiled.spec;
    for(const key of ['dual','dualMark','stack','categorical','naturalOrder'])delete spec[key];
    spec.type='cards';spec.measures=[...new Set(measures.map(idOf))];spec.synchronize=state.syncAxes===true;
    spec.cards=cards.map((c,i)=>{const e=c.encodings,dimId=p=>p&&role(p)==='dimension'?idOf(p):'',measId=p=>p&&role(p)==='measure'?idOf(p):'';
      if(!core.DUAL_MARKS.includes(c.type))notes.push(core.markLabel(c.type)+' cannot share a dual axis, so '+((byIndex.get(c.measure.column)||{}).name||'its measure')+' is drawn as '+(i===0?'bars':'a line')+'.');
      const shapeField=e.shape&&e.shape.column!=null?byIndex.get(e.shape.column):null;
      return {id:c.id,measure:idOf(c.measure),type:core.dualCardMark(c.type,i,timeAxis),glyph:c.type==='square'?'square':(e.shape&&e.shape.glyph)||'',
        shape:shapeField&&shapeField.role==='dimension'?shapeField.id:'',shapeMap:e.shape&&e.shape.glyphs?{...e.shape.glyphs}:null,
        color:dimId(e.color),colorMeasure:measId(e.color),size:measId(e.size),labelFields:e.label?[idOf(e.label)].filter(Boolean):[],
        tooltip:e.tooltip.map(idOf).filter(Boolean),path:e.path?idOf(e.path):'',opacity:c.opacity,border:c.border,halo:c.halo,visible:c.visible!==false};});
    return {...compiled,spec,notes};
  }
  function compile(state,columns,{dataset,industry,basis='receipt',offset=0}={}){
    // Marks v3 (#52): a dual axis compiles through compileCards; one card reads
    // its v3 encodings (Shape, Path, Angle) from core.readCards.
    const cardSet=core.readCards(state);
    // Lane P: a map sheet's cards are layers, never a dual axis (map-studio.js draws them).
    if(cardSet.length>=2&&!(core.mapLayersOn&&core.mapLayersOn(state)))return compileCards(state,columns,{dataset,industry,basis,offset},cardSet);
    const card=cardSet[0];
    const byIndex=new Map(columns.map(f=>[f.index,f])),attach=list=>core.shelf(list).map(p=>({...p,field:byIndex.get(p.column)})).filter(p=>p.field);
    const axes=attach([...(state.columns||[]),...(state.rows||[])]),dimensions=[],measures=[],allMeasures=[],notes=[],computed=new Set();
    const addDim=f=>{if(!dimensions.includes(f.id))dimensions.push(f.id);};
    const addTo=(list,id)=>{if(id&&!list.includes(id))list.push(id);};
    const nameOf=id=>(columns.find(f=>f.id===id)||{}).name||id;
    const measure=p=>{const id=p.field.id;if(id==='amount')return AGG[p.aggregation]||'amount';if(p.aggregation!=='sum'&&!computed.has(id)){computed.add(id);notes.push(p.field.name+' is computed directly from source observations, so it keeps its source statistic.');}return id;};
    const mark=state.marks||{},color=attach(mark.color==null?[]:[mark.color])[0],size=attach(mark.size==null?[]:[mark.size])[0];
    const labels=attach(mark.label==null?[]:[mark.label]),tooltip=attach(mark.tooltip),detail=attach(mark.detail);
    if(!axes.length&&!color&&!size&&!labels.length&&!tooltip.length&&!detail.length)throw new Error('Add a dollar or count measure to Rows or Columns.');
    axes.filter(p=>p.field.role==='dimension').forEach(p=>addDim(p.field));
    axes.filter(p=>p.field.role==='measure').forEach(p=>addTo(measures,measure(p)));
    for(const p of [...(color?[color]:[]),...detail,...labels,...tooltip,...(size?[size]:[])])if(p.field.role==='dimension')addDim(p.field);
    // Marks v3 (#50-#52): Shape on Shape marks, Path on Line, Area and Polygon,
    // Angle on Pie; a dimension on any of them joins each mark's grouping.
    const v3Type=mark.type||'automatic',enc=card.encodings,v3Pill=value=>value&&value.column!=null?attach([{column:value.column,aggregation:value.aggregation}])[0]||null:null;
    const shapePill=v3Type==='shape'?v3Pill(enc.shape):null,pathPill=core.PATH_TYPES.includes(v3Type)?v3Pill(enc.path):null,anglePill=core.ANGLE_TYPES.includes(v3Type)?v3Pill(enc.angle):null;
    for(const p of [shapePill,pathPill,anglePill])if(p&&p.field.role==='dimension')addDim(p.field);
    if(shapePill&&shapePill.field.role==='measure')notes.push('Shape takes a dimension; '+shapePill.field.name+' is in each mark’s tooltip.');
    if(anglePill&&anglePill.field.role==='dimension')notes.push('Angle takes a measure; '+anglePill.field.name+' joins the grouping instead.');
    const count=columns.some(f=>f.id==='records')?'records':'amount';
    if(anglePill&&anglePill.field.role==='measure'){const id=measure(anglePill),at=measures.indexOf(id);if(at>=0)measures.splice(at,1);measures.unshift(id);}
    if(!measures.length){measures.push(count);notes.push('No measure is placed, so each mark counts '+(count==='records'?'source observations':'the selected amount')+'.');}
    allMeasures.push(...measures);
    const encoded=p=>p.field.role==='measure'?measure(p):p.field.id;
    const colorMeasure=color&&color.field.role==='measure'?encoded(color):'';
    const sizeMeasure=size&&size.field.role==='measure'?encoded(size):'';
    if(size&&!sizeMeasure)notes.push('Size follows a measure; '+size.field.name+' joins the grouping instead.');
    const tipMeasures=[...tooltip,...detail,...[shapePill,pathPill].filter(Boolean)].filter(p=>p.field.role==='measure').map(encoded);
    const encodingIds=[...labels.filter(p=>p.field.role==='measure').map(encoded),...tipMeasures,sizeMeasure,colorMeasure].filter(Boolean);
    for(const id of encodingIds)addTo(allMeasures,id);
    // A histogram, box or date span asks the server for its own statistics; the
    // Label, Tooltip, Detail, Size and Colour measures ride along (census C-07).
    const keepEncodings=()=>{for(const id of encodingIds)addTo(allMeasures,id);};
    let type=mark.type||'automatic';
    if(!types.has(type))throw new Error('Not available for verified data. Use Bar, Line, Circle, Text or Map, or switch to Report worksheet.');
    if(type==='automatic')type=dimensions.some(d=>TIME.includes(d))?'line':'bar';
    // Circle with two or more measures is a scatter ([0] x, [1] y, the rest in the tooltip); with one
    // it is one circle per group at its value.
    if(type==='circle')type=measures.length>=2?'scatter':'dot';
    // Square and Shape are Circle's two layouts drawn with a shape.
    const glyphType=type==='square'||type==='shape';
    if(glyphType)type=measures.length>=2?'scatter':'dot';
    if(type==='text')type='table';
    const filled=type==='filled-map';if(filled)type='map';
    let transform=state.analytics?.transform||'none';
    // Distributions, spans, maps and scatters have no period axis: the analysis is left off and said so.
    if(transform!=='none'&&(['histogram','box','gantt','map','scatter','polygon','density'].includes(type)||glyphType)){notes.push('Period analysis draws marks along a time axis, so this '+(glyphType?v3Type:type==='scatter'?'circle':type==='map'?'map':type)+' view is drawn without it.');transform='none';}
    if(transform!=='none'){
      const time=dimensions.findIndex(d=>['date','year','cycle'].includes(d));
      if(time<0){notes.push('Period analysis needs Contribution date, Year or Election cycle on a shelf, so the chart is drawn without it.');transform='none';}
      else{
        dimensions[time]=state.analytics.period==='year'?'year':state.analytics.period==='cycle'?'cycle':dimensions[time]==='date'?'year':dimensions[time];
        if(time)dimensions.unshift(dimensions.splice(time,1)[0]);
        // Contract V11/W5: the chosen mark is kept, and every measure and every other dimension is
        // its own series over the periods.
      }
    }
    let categorical=false;
    if(TIMELINE.includes(type)&&transform==='none'){
      const time=dimensions.findIndex(d=>TIME.includes(d)&&!(pathPill&&d===pathPill.field.id));
      if(time>0){dimensions.unshift(dimensions.splice(time,1)[0]);notes.push(nameOf(dimensions[0])+' leads the axis, so every other group is its own line.');}
      else if(time<0)categorical=true;
    }
    const needsSecond=['bullet','combination','dual-line'].includes(type)&&measures.length<2&&!(color&&color.field.role==='dimension');
    if(needsSecond){const second=['amount','records','contributors'].find(id=>id!==measures[0]&&columns.some(f=>f.id===id));if(second){measures.push(second);addTo(allMeasures,second);notes.push(nameOf(second)+' is the second measure; put another measure on the shelves to choose it.');}}
    if(sizeMeasure)notes.push('Mark size follows the absolute magnitude of '+size.field.name+'; signed values remain in the tooltip.');
    if(state.params?.splitMulti)notes.push('Split multi-value cells applies to report worksheets; verified source records are never multi-valued, so it is not used here.');
    const filters=[],aggregate_filters=[];
    for(const f of state.filters||[]){const field=byIndex.get(Number(f.column));if(!field)continue;const target=f.stage==='aggregate'||field.role==='measure'&&field.id!=='amount'?aggregate_filters:filters;try{for(const clause of clauses(f,field,notes))target.push(clause);}catch(error){notes.push(field.name+' filter is left out: '+error.message);}}
    const page=attach(state.pages==null?[]:[state.pages])[0];
    if(page){if(page.field.role!=='dimension')notes.push('Pages steps through a dimension; '+page.field.name+' is a measure, so Pages is not applied.');else if(state.pageMember!=='')filters.push({field:page.field.id,op:'eq',value:state.pageMember});}
    let binWidth=Math.round(Number(state.params?.binWidth||100)*100);
    if(!Number.isSafeInteger(binWidth)||binWidth<1){notes.push('The histogram bin width must be a positive amount in dollars and cents, so $100 bins are used.');binWidth=10000;}
    if(type==='histogram'){
      const keep=color&&color.field.role==='dimension'?[color.field.id]:[],dropped=dimensions.filter(d=>!keep.includes(d)&&d!=='amount_bin');
      dimensions.splice(0,dimensions.length,'amount_bin',...keep);measures.splice(0,measures.length,'records');allMeasures.splice(0,allMeasures.length,'records','amount');keepEncodings();
      notes.push('Bins count individual observations; no group means or subtotals are used.'+(dropped.length?' '+dropped.map(nameOf).join(', ')+(dropped.length===1?' is':' are')+' not binned; put a field on Colour to stack the bins by it.':''));
    }
    if(type==='box'){measures.splice(0,measures.length,'median_amount');allMeasures.splice(0,allMeasures.length,'min_amount','q1_amount','median_amount','q3_amount','max_amount');keepEncodings();notes.push('Quartiles use selected individual amounts. Whiskers show minimum and maximum, including outliers.');}
    if(type==='gantt'){measures.splice(0,measures.length,'span_days');allMeasures.splice(0,allMeasures.length,'first_date','last_date','span_days');keepEncodings();notes.push('Shows the observed receipt date span, not a project duration or continuous giving.');}
    if(type==='map'){
      if(!dimensions.includes('zip5')&&!dimensions.includes('state')){type='bar';notes.push('A map groups by Contributor ZIP or State; neither is on a shelf, so this view is drawn as bars.');}
      else{
      if(dimensions.includes('zip5')&&!dimensions.includes('state'))dimensions.push('state');
        const others=dimensions.filter(d=>!['zip5','state'].includes(d));
        if(others.length)notes.push('Each map mark combines every '+others.map(nameOf).join(' / ')+' group at its place; put '+(others.length===1?'it':'them')+' on Filters to map one.');
        if(measures[0]!=='amount')notes.push('The map shades by '+nameOf(measures[0])+'.');
        addTo(allMeasures,'amount');
        if(color||size||labels.length||tooltip.length)notes.push('Map marks show ZIP, recorded states and selected amount.');
      }
    }
    const wanted=state.params?.maxGroups;
    const limit=type==='map'?100000:type==='histogram'?10000:wanted==='all'?100000:Math.min(100000,Math.max(1,Number(wanted)||60));
    if(wanted==='all'&&type!=='map'&&type!=='histogram')notes.push('All groups: the server returns up to 100,000 groups per page.');
    const queryDimensions=[...new Set(dimensions)];
    const sort=querySort(state,queryDimensions,measures);
    // A time axis ordered by a measure is read in that order, not as elapsed time (contract V01).
    if(TIMELINE.includes(type)&&transform==='none'&&sort[0].field!==queryDimensions[0])categorical=true;
    let dual=false;
    // Contract V03: dual axes over a category axis are one panel of bars or a line with the second
    // measure on a right axis; over a time axis contribution-charts draws them.
    if(['combination','dual-line'].includes(type)&&categorical){dual=true;type=type==='combination'?'bar':'line';}
    const query={dataset,basis,dimensions:queryDimensions,measures:allMeasures,filters,aggregate_filters,sort,limit,offset:type==='map'||transform!=='none'?0:offset};
    if(industry)query.industry=industry;
    if(transform!=='none')query.limit=100000;
    if(type==='histogram'||query.dimensions.includes('amount_bin')||filters.some(f=>f.field==='amount_bin'))query.bin_width_cents=binWidth;
    const spec={type,filled,binWidth,dimensions:query.dimensions,measures,color:color&&color.field.role==='dimension'?color.field.id:'',size:sizeMeasure,labelFields:labels.map(encoded),tooltip:[...tooltip.map(encoded),...detail.filter(p=>p.field.role==='measure').map(encoded)],labels:!!state.showLabels,references:{average:!!state.analytics?.average,median:!!state.analytics?.median}};
    if(colorMeasure)spec.colorMeasure=colorMeasure;
    if(categorical)spec.categorical=true;
    if(dual){spec.dual=true;spec.dualMark='line';}
    if(naturalOrder(state,queryDimensions)&&transform==='none'&&type!=='map')spec.naturalOrder=true;
    if(type==='stacked'&&spec.color)spec.stack='stacked';
    // Marks v3: the shape each point takes (Square, Shape), the layout of Polygon
    // and Density, and the Path order of a line, area or polygon.
    if(glyphType){spec.glyph=v3Type==='square'?'square':(enc.shape&&enc.shape.glyph)||(shapePill&&shapePill.field.role==='dimension'?'':'circle');
      if(shapePill&&shapePill.field.role==='dimension'){spec.shape=shapePill.field.id;if(enc.shape.glyphs)spec.shapeMap={...enc.shape.glyphs};}}
    if(shapePill&&shapePill.field.role==='measure')spec.tooltip.push(encoded(shapePill));
    if(type==='polygon'||type==='density')spec.layout=measures.length>=2?'scatter':'axis';
    if(pathPill&&['line','area','polygon'].includes(type))spec.path=encoded(pathPill);
    // Census B-E21 / B-E22: Parameters ask for every axis label and every legend member.
    if(state.params?.axisLabels==='all')spec.axisLabels='all';
    if(state.params?.legend==='all')spec.legend='all';
    if(detail.length||labels.some(p=>p.field.role==='dimension')||tooltip.some(p=>p.field.role==='dimension'))notes.push('Detail, Label and Tooltip dimensions define the grouping behind each mark.');
    return {query,spec,notes,transform};
  }
  /* Graph and Chart Studio read the same workbook, so a difference between
     them is a fact about the question each one was asked, not about the
     money. reconcile() names that difference instead of leaving a reader to
     decide which of two numbers on two screens is the wrong one. Pure: it is
     handed the studio result, the basis it was drawn on, the Graph view for
     the same company and cycle, and the Graph controls that were set. */
  const RECONCILE_REASONS={
    basis:'this sheet uses a different basis than Graph',
    graph_filter:'a Graph filter (employee level or giving type) is not carried',
    workbook:'the workbook version differs',
    aggregate:'grouped filters apply after aggregation',
    funding_classification:'the Graph view uses published giving categories while this sheet uses verified funding classifications, which can leave organization receipts unverified',
    cycle:'the Graph view shows one election cycle and this sheet shows all of them',
    unavailable:'the Graph view has no total for this scope',
    unexplained:'the cause is unknown; report it'
  };
  function reconcile(result,basis,graphView,filters){
    const dollarsToCents=value=>Number.isFinite(Number(value))?Math.round(Number(value)*100):null;
    const wholeCents=value=>Number.isFinite(Number(value))?Math.round(Number(value)):null;
    const graphCents=graphView&&graphView.totals?dollarsToCents(graphView.totals.giving):null;
    const studioCents=result&&result.totals?wholeCents(result.totals.amount):null;
    const receiptCents=result&&result.comparison?wholeCents(result.comparison.receipt):null;
    const out={graphCents,studioCents,receiptCents,differenceCents:null,agree:false,reasonId:'',reason:''};
    if(graphCents===null||studioCents===null){out.reasonId='unavailable';out.reason=RECONCILE_REASONS.unavailable;return out;}
    out.differenceCents=studioCents-graphCents;
    if(basis==='receipt'&&out.differenceCents===0){out.agree=true;return out;}
    const scope=filters||{};
    const workbook=String((result.provenance||{}).workbook_sha256||'');
    const books=((graphView||{}).reports||[]).map(report=>String(report.sha256||'')).filter(Boolean);
    out.reasonId=basis!=='receipt'?'basis'
      :!scope.studioCycle&&scope.graphCycle?'cycle'
      :scope.tier||(scope.funding&&scope.funding!=='all')?'graph_filter'
      :workbook&&books.length&&!books.includes(workbook)?'workbook'
      :(result.reconciliation||{}).aggregate_filters_applied?'aggregate'
      :scope.fundingClassification?'funding_classification'
      :'unexplained';
    out.reason=RECONCILE_REASONS[out.reasonId];
    return out;
  }
  function markFilters(request,row){const filters=[...request.filters];if(row)for(const field of request.dimensions){const value=row[field];if(value!==undefined)filters.push({field,op:Array.isArray(value)?'in':'eq',value});}return filters;}
  function mapSelection(row){if(row.zip5&&Array.isArray(row.states)&&row.states.length)return {zip5:row.zip5,state:row.states};if(!row.zip5&&row.state)return {state:row.states||[row.state]};throw new Error('This map mark has no geographical source identity.');}
  const sameSource=(a,b)=>['workbook_sha256','database_sha256','index_token','query_engine_sha256','field_semantics_sha256'].every(k=>a?.[k]===b?.[k]);
  const quote=value=>'"'+String(typeof value==='string'&&/^\s*[=+@-]/.test(value)?"'"+value:value??'').replaceAll('"','""')+'"';
  // `gap`: the Coverage gap caption (coverage-view.js), when the scope touches a collection shortfall; it rides on every row like the provenance columns.
  function csv(result,request,gap=''){const cols=result.columns,tail=gap?[gap]:[];return '\uFEFF'+[cols.map(c=>c.id+(c.unit==='cents'?'_usd':'')).concat(['accounting_basis','source_workbook_sha256','database_sha256','analytical_index','query_engine_sha256','field_semantics_sha256','query_filters',...(gap?['coverage_gap']:[])]).map(quote).join(','),...result.rows.map(row=>cols.map(c=>c.unit==='cents'&&row[c.id]!=null?row[c.id]/100:row[c.id]).concat([request.basis,result.provenance.workbook_sha256,result.provenance.database_sha256,result.provenance.index_token,result.provenance.query_engine_sha256,result.provenance.field_semantics_sha256,JSON.stringify({filters:request.filters,aggregate_filters:request.aggregate_filters,bin_width_cents:request.bin_width_cents}),...tail]).map(quote).join(','))].join('\r\n');}
  /* Pending answers (r7, 2026-09-24). While the console builds a query view or a records
     sidecar in the background, POST /api/contributions/query and /records answer at once,
     HTTP 200, with {status:'building', phase:'query_view'|'records_sidecar', message,
     processed_records, total_records (an int, or null when unknown), elapsed_ms,
     retry_after_ms, dataset, provenance} instead of holding the request. The index build's
     older {status:'preparing', message, processed_records, retry_after_ms:1500} keeps its
     shape and means the same. A ready answer is today's body (status 'ready', or none). A
     build that fails comes back on the next poll as an ordinary error ({error}, non-2xx),
     and the same body sent once with retry:true starts it again. 'queued' belongs to
     snapshot jobs and is not a pending answer here. The server never gives up; the client
     stops after about 15 minutes of polling (the Insights ceiling) and says so. Every
     Chart Studio call to the two endpoints goes through pollPending(); graph.js
     sparkReady() polls the same answers. The standalone /contribution-studio page loads
     this file and polls through pollPending() too (code review finding 7, 2026-09-25). */
  const PENDING=new Set(['building','preparing']),PENDING_CEILING_MS=15*60*1000,PENDING_MIN_MS=250,PENDING_MAX_MS=5000;
  const pendingStatus=answer=>answer&&typeof answer==='object'&&PENDING.has(answer.status)?answer.status:'';
  // The server's retry_after_ms held between 250 ms and 5 s (it sends 300 ms, then 750 ms,
  // then 1500 ms); an answer without a numeric one backs off 0.5, 1, 2, then 4 s.
  function pendingDelay(answer,attempt=0){
    const asked=answer&&typeof answer==='object'?answer.retry_after_ms:undefined;
    if(typeof asked==='number'&&Number.isFinite(asked))return Math.min(PENDING_MAX_MS,Math.max(PENDING_MIN_MS,Math.round(asked)));
    return Math.min(4000,500*2**Math.min(3,Math.max(0,Math.floor(Number(attempt)||0))));
  }
  const countFormat=new Intl.NumberFormat('en-US');
  const clockText=ms=>{const s=Math.max(0,Math.floor((Number(ms)||0)/1000));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');};
  // One pending answer, read strictly: a share only when the server knows its total, else
  // the records so far; the server's elapsed_ms when it sends one, else this page's wait.
  function pendingProgress(answer,{path='query',attempt=0,waitedMs=0,delayMs=0}={}){
    answer=answer&&typeof answer==='object'?answer:{};
    const phase=typeof answer.phase==='string'?answer.phase:'',message=typeof answer.message==='string'?answer.message.trim():'';
    const processed=Number.isFinite(answer.processed_records)&&answer.processed_records>0?Math.floor(answer.processed_records):0;
    const total=Number.isInteger(answer.total_records)&&answer.total_records>0?answer.total_records:null;
    const waited=Math.max(0,Number(waitedMs)||0);
    return {status:pendingStatus(answer),phase,path,
      message:message||(path==='records'||phase==='records_sidecar'?'Preparing the source records\u2026':'Preparing the query view\u2026'),
      processed,total,percent:total===null?null:Math.min(100,Math.floor(processed*100/total)),
      elapsedMs:Number.isFinite(answer.elapsed_ms)&&answer.elapsed_ms>=0?answer.elapsed_ms:waited,waitedMs:waited,attempt,retryAfterMs:delayMs};
  }
  function pendingDetail(progress,{elapsed=true}={}){
    const parts=progress.percent!==null&&progress.percent!==undefined
      ?[progress.percent+'%',countFormat.format(progress.processed)+' of '+countFormat.format(progress.total)+' records']
      :progress.processed>0?[countFormat.format(progress.processed)+' records so far']:[];
    if(elapsed)parts.push(clockText(progress.elapsedMs)+' elapsed');
    return parts.join(' \u00b7 ');
  }
  const pendingText=(progress,options)=>{const detail=pendingDetail(progress,options);return detail?progress.message+' '+detail:progress.message;};
  const abortError=()=>typeof DOMException==='function'?new DOMException('View changed','AbortError'):Object.assign(new Error('View changed'),{name:'AbortError'});
  function pendingSleep(ms,signal){
    return new Promise((resolve,reject)=>{
      if(signal&&signal.aborted){reject(abortError());return;}
      let timer=0;const stop=()=>{clearTimeout(timer);reject(abortError());};
      timer=setTimeout(()=>{if(signal)signal.removeEventListener('abort',stop);resolve();},ms);
      if(signal)signal.addEventListener('abort',stop,{once:true});
    });
  }
  // `surface` names who stopped checking; the standalone page passes 'this page' (finding 7, 2026-09-25).
  function pendingCeiling(path,ceilingMs,progress,surface='Chart Studio'){
    const minutes=Math.round(ceilingMs/60000),span=minutes>=1?minutes+' minute'+(minutes===1?'':'s'):Math.max(1,Math.round(ceilingMs/1000))+' seconds';
    const error=new Error('The server is still preparing '+(path==='records'?'these source records':'this query view')+' after about '+span+', so '+surface+' stopped checking. It may still finish there; Retry checks again.');
    error.name='PendingCeiling';error.retryable=true;error.progress=progress;return error;
  }
  // ask(body) sends one request and resolves to the server's answer. A pending answer is
  // asked again after pendingDelay(), and onProgress hears each one. The poll stops at once
  // when `signal` aborts or `current()` turns false (a newer request won), waits on `gate()`
  // between polls without that wait counting toward the ceiling, and sends retry:true on
  // its first request only. An error from ask() is marked retryable; an AbortError is not.
  async function pollPending(ask,{body,path='query',signal=null,current=null,retry=false,onProgress=null,gate=null,
      sleep=pendingSleep,now=Date.now,ceilingMs=PENDING_CEILING_MS,surface='Chart Studio'}={}){
    const live=()=>{if((signal&&signal.aborted)||(current&&!current()))throw abortError();};
    const started=now();let paused=0,attempt=0,again=retry===true;
    for(;;){
      live();
      let answer;
      try{answer=await ask(again?{...body,retry:true}:body);}
      catch(error){if(error&&typeof error==='object'&&error.name!=='AbortError')error.retryable=true;throw error;}
      again=false;
      live();
      if(answer&&answer.status==='failed')throw Object.assign(new Error(answer.message||'Source verification failed.'),{retryable:true});
      if(!pendingStatus(answer))return answer;
      const waited=now()-started-paused,delay=pendingDelay(answer,attempt);
      const progress=pendingProgress(answer,{path,attempt,waitedMs:waited,delayMs:delay});
      if(waited>=ceilingMs)throw pendingCeiling(path,ceilingMs,progress,surface);
      if(onProgress)try{onProgress(progress);}catch(error){if(typeof console!=='undefined')console.error(error);}
      await sleep(delay,signal);
      if(gate){const held=now();try{await gate(signal);}finally{paused+=now()-held;}}
      attempt+=1;
    }
  }
  const api={fields,PANE_FIELDS,compile,compileCards,canvasEncodings,clauses,filterValue,showMeEligibility,showMeApply,markFilters,mapSelection,sameSource,csv,reconcile,effectiveSort,querySort,orderedAxis,naturalOrder,naturalRows,sortLock,labelLock,BASIS,MONEY,EXTRA_MARKS,RECONCILE_REASONS,LABEL_SUPPORT,MARK_TYPES:types,SORT_LOCK_TIME,SORT_LOCK_BINS,LABEL_LOCK_TRANSFORM,LABELLED_TRANSFORMS,CHRONOLOGICAL,TIMELINE,
    pollPending,pendingStatus,pendingDelay,pendingProgress,pendingDetail,pendingText,pendingCeiling,PENDING,PENDING_CEILING_MS,PENDING_MIN_MS,PENDING_MAX_MS};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MentatStudioContributions=api;
})(globalThis);
