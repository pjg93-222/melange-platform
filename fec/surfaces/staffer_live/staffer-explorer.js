/* Complete source discovery with paginated evidence and responsive map aggregation. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),live=window.StafferLive;
  const worker=new Worker('staffer-inventory-worker.js');
  new ResizeObserver(()=>document.documentElement.style.setProperty('--explorer-toolbar-height',(document.querySelector('.toolbar').offsetHeight+12)+'px')).observe(document.querySelector('.toolbar'));
  let inventoryRevision=null,inventoryPeople=0,routingPeople=0,reloadTimer=null;
  let loaded=false,active=true,sourceSelected=null,epoch=0,page=0,total=0,tiles=[],lastView='',pendingView='',catalogRequest=0;
  let bounds={minX:-1100,maxX:1100,minY:-1100,maxY:1100},pendingFit=true,lastSize='',scrollResults=false;
  let sourceOffset=0,returnPerson=null;const sourceHistory=[];
  let loadedScope='network',locationRequest=0,highlightPoints=[],neighborEdges=[],neighborIds=[],focusedPerson=null,requestedFit=null;
  const add=(parent,tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value??'';if(cls)e.className=cls;parent.append(e);return e;};
  const fmt=n=>Number(n).toLocaleString();
  function sourceDate(value,fallback){
    if(!value)return fallback;
    if(/^\d{4}$/.test(value))return value+' (year only)';
    if(/^\d{4}-\d{2}$/.test(value))return value+' (month only)';
    if(/^\d{4}-00-00$/.test(value))return value.slice(0,4)+' (year only)';
    if(/^\d{4}-\d{2}-00$/.test(value))return value.slice(0,7)+' (month only)';
    if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value;
    return value+' (source date needs review)';
  }
  const activityCache=new Map();
  function renderNativeSources(node){
    const ids=new Set([node.lda_source_person_id,...(node.source_person_ids||[]),node.id?.replace(/^person:/,'')].filter(id=>id?.startsWith('lda:lobbyist:')));
    for(const id of ids){const button=add($('detail'),'button','Complete LDA source evidence · '+id);button.onclick=()=>showSource(id,0);}
  }
  async function renderActivities(id,offset=0,section=null){
    const paging=Boolean(section);
    section=section||add($('detail'),'section','','activity-evidence');
    section.replaceChildren();add(section,'h3','Lobbying reports');
    const status=add(section,'p','Loading all reported activity records…','note');
    try{
      const key=id+'|'+offset;
      let data=activityCache.get(key);
      if(!data){const response=await fetch('/api/staffer-person-activities?'+new URLSearchParams({id,offset}));if(!response.ok)throw Error('Activity HTTP '+response.status);data=await response.json();if(activityCache.size>100)activityCache.clear();activityCache.set(key,data);}
      if(!section.isConnected||live.viewport().selected!==id)return;
      status.textContent=data.records?.length?'Reports naming this person.':'No lobbying reports found for this person.';
      if(!data.records?.length)return;
      add(section,'p',`${offset+1}–${offset+data.records.length} of ${fmt(data.total)} named activity records`,'note');
      for(const row of data.records){const item=add(section,'details','');add(item,'summary',[row.issue||'Issue unspecified',row.report_start||'Report date unavailable',row.registrant_source_id].filter(Boolean).join(' · '));add(item,'pre',JSON.stringify(row,null,2));const open=add(item,'button','Open full filing');open.onclick=()=>showSource(row.source_activity_id,0);}
      const nav=add(section,'div','','pagination'),prev=add(nav,'button','Previous activity'),next=add(nav,'button','Next activity');prev.disabled=offset===0;next.disabled=!data.has_more;prev.onclick=()=>renderActivities(id,Math.max(0,offset-data.limit),section);next.onclick=()=>renderActivities(id,offset+data.limit,section);
      const proof=add(section,'details','');add(proof,'summary','Activity index verification');add(proof,'pre',JSON.stringify(data.provenance,null,2));
      if(paging){section.tabIndex=-1;section.setAttribute('aria-label','Lobbying activity evidence');section.focus({preventScroll:true});section.scrollIntoView({block:'start'});}
    }catch(error){if(section.isConnected){status.textContent=error.message;const retry=add(section,'button','Retry activity evidence');retry.onclick=()=>renderActivities(id,offset,section);}}
  }
  window.addEventListener('staffer-revision',event=>{activityCache.clear();if(loaded&&(event.detail?.source_revision||event.detail?.revision)!==inventoryRevision){clearTimeout(reloadTimer);reloadTimer=setTimeout(()=>request('load'),150);}});
  let SCOPE=new URLSearchParams(location.search).get('route_model')==='evidence_cost'?'sources':'network';
  const RECORD_KIND='person';
  function request(type='query'){
    if(type==='page'||type==='query')scrollResults=true;
    if(type==='query'||type==='load'){epoch++;page=0;lastView='';pendingView='';if(!loaded){tiles=[];pendingFit=true;}}
    worker.postMessage({type,epoch,page,scope:SCOPE,text:$('search').value,kind:RECORD_KIND,
      person:type==='load'?new URLSearchParams(location.search).get('person'):null});
    $('search-status').textContent=type==='load'?(loaded?'Updating records…':'Opening the network…'):'Searching indexed records…';
  }
  window.addEventListener('staffer-network-mode',event=>{
    const next=event.detail?.scope==='sources'?'sources':'network';
    if(next===SCOPE)return;
    SCOPE=next;loaded=false;tiles=[];pendingFit=true;request('load');
  });
  function info(){
    const route=live.viewport().routeNodes.size;
    if(loaded){$('inventory-total').textContent=`${fmt(inventoryPeople)} ${loadedScope==='network'?'staffer records':'ecosystem people'}`;
      $('inventory-total').title=loadedScope==='network'?'Verified Politico/Quorum operational population; interns and unresolved candidate matches are excluded.':'People in the retained evidence ecosystem; unresolved identities remain separate.';}
    const positioned=highlightPoints.filter(p=>live.viewport().routeNodes.has(p.id)).length;
    const extra=highlightPoints.filter(p=>p.overlay&&live.viewport().routeNodes.has(p.id)).length;
    $('counts').textContent=loaded?`${loadedScope==='network'?'Staffer network':'Ecosystem'} · Current and historical records${route?' · '+positioned+'/'+route+' route people on map'+(extra?' · '+extra+' additional historical records':''):''}`:'Opening the connection network…';
    $('sampling').textContent='Everyone stays on the map as groups. Routes and selected connections are drawn over it; no fallback to a small sample.';
    $('map-help').textContent=route?(positioned===route?'Route highlighted on the same network. Focus route zooms in.':'Locating route participants…'):'Zoom into groups; click a person to show documented connections.';
    $('focus-route').hidden=!route;
    $('focus-person').hidden=!neighborIds.length;
    $('canvas').dataset.zoom=String(live.viewport().zoom);
    $('canvas').dataset.visiblePeople=String(tiles.filter(t=>t.count===1).length);
    $('canvas').dataset.connectionEdges=String(neighborEdges.length);
    Object.assign($('canvas').dataset,{population:String(loadedScope==='network'?routingPeople:inventoryPeople),searchResults:String(total),routePeople:String(route),mapMode:loadedScope});
  }
  function reconcile(data,networkCounts={}){
    if(!data)return;
    const out=$('reconciliation-table');out.replaceChildren();const table=add(out,'table',''),head=add(table,'tr','');add(head,'th','Source');add(head,'th','Source identities');add(head,'th','Profiles in network');add(head,'th','Coverage');
    for(const source of data.sources){const row=add(table,'tr','');add(row,'td',source.source);add(row,'td',source.source_people==null?'Unavailable':fmt(source.source_people));add(row,'td',fmt(networkCounts[({'LDA':'Lobbying','Politico Pro':'Politico'})[source.source]||source.source]||0));add(row,'td',source.scope);}
    for(const [source,count] of Object.entries(data.additional_source_person_records||{})){const row=add(table,'tr','');add(row,'td',source);add(row,'td',fmt(count));add(row,'td',source==='Quorum'?fmt(data.quorum_contact_matches)+' contact matches':'�');add(row,'td','Additional source browsing; not added to the connection network');}
    $('reconciliation-equation').textContent=data.balance_holds?`${fmt(data.source_people_before_matching)} source identities − ${fmt(data.verified_lda_payroll_joins)} verified LDA/payroll matches − ${fmt(data.accepted_alias_reductions)} accepted duplicate records = ${fmt(data.network_people)} network profiles.`:'The count reconciliation is incomplete for this source generation.';
    $('reconciliation-note').textContent=`${data.network_scope ? fmt(data.network_scope.excluded_intern_records)+' intern records are excluded from the map and connection paths; the source totals below retain them. ' : ''}${data.note} ${fmt(data.current_congressional_staff)} currently listed congressional staff. Quorum has ${fmt(data.quorum_directory_ids)} directory IDs and ${fmt(data.quorum_contact_ids)} IDs across the contact exports; contacts attach to ${fmt(data.quorum_contact_matches)} network profiles. Profiles within each source are counted after accepted identity matches. A profile can belong to several sources, so these columns cannot be summed as unique people. Ambiguous source mentions and institutions are not counted as source identities.`;
  }
  function locateHighlights(fit=null){
    const request=++locationRequest;
    if(fit)requestedFit=fit;
    const view=live.viewport(),ids=new Set([...view.routeNodes.keys(),...neighborIds]);if(view.selected)ids.add(view.selected);
    if(!ids.size){highlightPoints=[];requestedFit=null;live.redraw();return;}
    highlightPoints=highlightPoints.filter(p=>ids.has(p.id));
    if(loaded)worker.postMessage({type:'locate',epoch,request,ids:[...ids],nodes:[...ids].map(id=>view.routeNodes.get(id)||view.nodes.get(id)).filter(Boolean)});
  }
  function fitHighlights(mode){
    const view=live.viewport(),points=highlightPoints.filter(p=>mode==='route'?view.routeNodes.has(p.id):p.id===view.selected||neighborIds.includes(p.id));
    if(!points.length)return;
    const r=live.mapRect(),xs=points.map(p=>p.x),ys=points.map(p=>p.y),loX=Math.min(...xs),hiX=Math.max(...xs),loY=Math.min(...ys),hiY=Math.max(...ys);
    view.zoom=Math.max(.02,Math.min(3,(r.right-r.left-140)/Math.max(100,hiX-loX),(r.bottom-r.top-80)/Math.max(100,hiY-loY)));
    view.pan={x:(r.left+r.right-50)/2-view.width/2-(loX+hiX)/2*view.zoom,y:(r.top+r.bottom)/2-view.height/2-(loY+hiY)/2*view.zoom};lastView='';live.redraw();
  }
  function fitMap(){const view=live.viewport(),r=live.mapRect();view.zoom=Math.max(.02,Math.min(3,(r.right-r.left-60)/Math.max(100,bounds.maxX-bounds.minX),(r.bottom-r.top-60)/Math.max(100,bounds.maxY-bounds.minY)));view.pan={x:(r.left+r.right)/2-view.width/2-(bounds.minX+bounds.maxX)/2*view.zoom,y:(r.top+r.bottom)/2-view.height/2-(bounds.minY+bounds.maxY)/2*view.zoom};lastView='';live.redraw();}
  function overview(resetSearch=false){active=true;sourceSelected=null;sourceHistory.length=0;returnPerson=null;catalogRequest++;live.select(null);if(resetSearch){$('route-clear').click();$('route-panel').open=true;$('search').value='';window.StafferGroups?.search();history.replaceState(null,'',location.pathname);request('query');}fitMap();info();live.redraw();}
  function select(row){
    if(!row)return;
    // A background inventory refresh must not reopen the selected profile and
    // replace its full facts with the compact search row.
    if(live.viewport().selected===row[0]&&live.viewport().personNodes?.has(row[0]))return;
    active=true;info();catalogRequest++;
    const n={id:row[0],label:row[1],source_summary:row[2],source_system:row[6],kind:row[8]||'person',member_ids:row[3]?[row[3]]:[],committee_ids:row[4]?[row[4]]:[],policy_ids:row[5]?[row[5]]:[],profile_pending:row[7],contacts:row[12]||[],canonical_eligible:false};
    if(row[0].startsWith('littlesis:')||row[0].startsWith('quorum:')){live.select(null);showSource(row[0],0);return;}
    if(n.kind!=='person'){live.select(null);showSource(n.id,0);return;}
    live.addNodes([n]);live.select(n.id,true);

  }
  async function showSource(id,offset,goingBack=false){
    if(!sourceSelected)returnPerson=live.viewport().selected;
    if(!goingBack&&sourceSelected&&sourceSelected!==id){sourceHistory.push({id:sourceSelected,offset:sourceOffset});if(sourceHistory.length>100)sourceHistory.shift();}
    sourceOffset=offset;
    sourceSelected=id;active=true;live.select(null);
    const requestId=++catalogRequest,out=$('detail');
    $('coworker-panel').hidden=true;$('detail-hint').hidden=true;out.replaceChildren();add(out,'p','Loading source evidence…','note');
    try{
      const response=await fetch('/api/staffer-source-record?'+new URLSearchParams({id,offset}));
      if(!response.ok)throw Error('Source evidence HTTP '+response.status);
      const data=await response.json();if(requestId!==catalogRequest)return;
      if(!data.record)throw Error('Source record is unavailable: '+data.status);
      out.replaceChildren();$('detail-title').textContent=data.node.label;window.StafferContacts?.render(out,data.node);
      if(sourceHistory.length){const back=add(out,'button','Back to previous source record');back.onclick=()=>{const previous=sourceHistory.pop();showSource(previous.id,previous.offset,true);};}
      else if(returnPerson){const back=add(out,'button','Back to person');back.onclick=()=>{const person=returnPerson;sourceSelected=null;catalogRequest++;live.select(person,true);};}
      $('person-panel').scrollTop=0;$('detail-title').setAttribute('tabindex','-1');$('detail-title').focus({preventScroll:true});
      if(window.matchMedia('(max-width:720px)').matches)$('person-panel').scrollIntoView({block:'start',behavior:'smooth'});
      add(out,'p',data.node.source_system+' · '+({container:'office',policy_area:'policy area',payroll_record:'unresolved payroll record'}[data.node.kind]||data.node.kind),'detail-value');add(out,'p',data.node.source_summary,'note');add(out,'p',data.note,'note');
      const record=add(out,'details','');add(record,'summary','Complete source record');add(record,'pre',JSON.stringify(data.record,null,2));
      if(data.native_source_id){const native=add(out,'button','Complete LDA source evidence');native.onclick=()=>showSource(data.native_source_id,0);}
      add(out,'h3',fmt(data.total)+' '+(data.relationship_label||'source relationship assertions'));
      for(const entry of data.relationships){
        const section=add(out,'section','','source-evidence');add(section,'strong',entry.category_name);
        const other=add(section,'button',entry.other_label);other.disabled=Boolean(entry.endpoint_missing&&entry.other_label.startsWith('Endpoint absent'));other.onclick=()=>{if(data.status==='verified_graph_context'&&entry.other_kind==='person'){active=true;sourceSelected=null;catalogRequest++;live.addNodes([{id:entry.other_id,label:entry.other_label,kind:'person'}]);live.select(entry.other_id,true);info();}else showSource(entry.other_id,0);};
        add(section,'p',entry.description||[entry.description1,entry.description2].filter(Boolean).join(' · '),'note');
        add(section,'p',[sourceDate(entry.raw_start_date,'Start unknown'),sourceDate(entry.raw_end_date,'End unknown'),entry.source_updated_at?'Source updated '+entry.source_updated_at.slice(0,10):''].filter(Boolean).join(' · '),'note');
        const detail=add(section,'details','');add(detail,'summary','Dates, source and assertion fields');add(detail,'pre',JSON.stringify(entry,null,2));
      }
      const nav=add(out,'div','','pagination'),prev=add(nav,'button','Previous evidence'),next=add(nav,'button','Next evidence');
      prev.disabled=offset===0;next.disabled=!data.has_more;prev.onclick=()=>showSource(id,Math.max(0,offset-data.limit));next.onclick=()=>showSource(id,offset+data.limit);
      add(out,'p',data.total?`${offset+1}–${Math.min(offset+data.relationships.length,data.total)} of ${fmt(data.total)} records`:'No relationship assertion names this record in the held snapshot.','note');
      const proof=add(out,'details','');add(proof,'summary','Catalog verification');add(proof,'pre',JSON.stringify(data.provenance,null,2));
      const address=new URL(location.href);address.searchParams.set('person',id);history.replaceState(null,'',address.pathname+address.search);
      window.dispatchEvent(new CustomEvent('staffer-source-selected',{detail:{id}}));
    }catch(error){if(requestId===catalogRequest){out.replaceChildren();add(out,'p',error.message,'note');const retry=add(out,'button','Retry');retry.onclick=()=>showSource(id,offset);}}
  }
  worker.onmessage=event=>{
    const data=event.data;
    if(data.epoch!==undefined&&data.epoch!==epoch)return;
    if(data.type==='waiting'){setTimeout(()=>request('load'),3000);$('search-status').textContent='Inventory loading: '+data.stage;return;}
    if(data.type==='error'){$('search-status').textContent=data.message+' — use Refresh to retry.';return;}
    if(data.type==='ready'){
      const firstLoad=!loaded;
      loaded=true;loadedScope=data.scope||SCOPE;inventoryRevision=data.revision;inventoryPeople=data.people_total;routingPeople=data.routing_people_total; $('inventory-total').dataset.revision=inventoryRevision||'';$('footer-note').textContent='One connection network · Source counts in Sources';
      reconcile(data.reconciliation,data.network_source_counts);
      $('inventory-coverage').textContent=`${fmt(data.people_total)} people. ${data.note}${data.complete?'':' Additional source catalog unavailable.'} Affiliations are available beside the map.`;
      if(data.catalog){const c=data.catalog.counts;$('inventory-coverage').textContent+=` LittleSis includes ${fmt(c.relationships)} source assertions; ${fmt(c.relationships_with_missing_endpoint)} refer to missing records and ${fmt(c.relationships_with_invalid_source_dates)} contains an invalid source date. These exceptions remain visible in the evidence.`;}
      if(data.ldaCatalog){const c=data.ldaCatalog.counts;$('inventory-coverage').textContent+=` LDA includes ${fmt(c.organization)} organizations, ${fmt(c.activity)} complete activity records and ${fmt(c.relationships)} source assertions; ${fmt(c.missing_endpoint_assertions)} assertions reference an endpoint without a separate held record.`;}
      if(firstLoad&&active&&!sourceSelected&&!live.viewport().selected&&!live.viewport().routeNodes.size)overview();
    }
    if(data.type==='page'){
      page=data.page;total=data.total;bounds=data.bounds;
      if(pendingFit&&active){pendingFit=false;fitMap();}
      $('search-status').textContent=fmt(total)+(total===1?' match':' matches')+' · map keeps everyone visible';
      $('results-title').textContent=$('search').value?'People found':'Browse people';
      $('results-page').textContent=`${page+1} / ${fmt(data.pages)}`;$('results-prev').disabled=page===0;$('results-next').disabled=page+1>=data.pages;
      const out=$('results');out.replaceChildren();
      if(window.MelangeRoster)window.MelangeRoster.render(out,data.rows,select);else for(const row of data.rows){const b=add(out,'button',row[1],'result');if(row[15])add(b,'small',row[15],'lda-status');add(b,'small',[row[14]?'Current':'',row[2],row[7]?'Profile content unavailable or pending':''].filter(Boolean).join(' · '));if(row[8]==='person')window.StafferContacts?.render(b,{kind:'person',contacts:row[12]||[]},{links:false});b.onclick=()=>select(row);}
      if(!data.rows.length)add(out,'p','No matching people. Try a shorter name, or open Offices & organisations above.','empty');
      if(scrollResults){scrollResults=false;const panel=$('person-panel');panel.scrollTop+=$('results-title').getBoundingClientRect().top-panel.getBoundingClientRect().top-12;if(window.matchMedia('(max-width:720px)').matches)panel.scrollIntoView({block:'start',behavior:'smooth'});}
      lastView='';locateHighlights();info();live.redraw();
    }
    if(data.type==='tiles'){tiles=data.tiles;lastView=data.viewKey;pendingView='';live.redraw();}
    if(data.type==='located'&&data.request===locationRequest){highlightPoints=data.points;if(requestedFit){fitHighlights(requestedFit);requestedFit=null;}live.redraw();}
    // An id with no row is an office, organisation, policy area or an unlinked
    // source record: it left the people list but its evidence page is unchanged,
    // so a saved link still opens it.
    if(data.type==='selection'){if(data.requestedPerson!==new URLSearchParams(location.search).get('person'))return;if(data.row)select(data.row);else if(data.id)showSource(data.id,0);}
  };
  worker.onerror=()=>{$('search-status').textContent='The record browser could not load. Refresh to retry.';};
  function capture(id,event,fn){$(id).addEventListener(event,e=>{e.stopImmediatePropagation();fn(e);},true);}
  let timer;
  capture('search','input',()=>{active=true;clearTimeout(timer);timer=setTimeout(()=>{request();window.StafferGroups?.search();},180);});
  capture('results-prev','click',()=>{page=Math.max(0,page-1);request('page');});
  capture('results-next','click',()=>{page++;request('page');});
  capture('full-network','click',()=>{overview(true);scrollResults=false;if(window.matchMedia('(max-width:720px)').matches)$('network').scrollIntoView({block:'start',behavior:'smooth'});});
  capture('refresh','click',()=>{request('load');live.refresh();window.StafferGroups?.refresh();});
  function showMapOnPhone(){if(window.matchMedia('(max-width:720px)').matches)$('network').scrollIntoView({block:'start'});}
  capture('fit','click',()=>{if(active)fitMap();else live.fitView();showMapOnPhone();});
  capture('focus-route','click',()=>{locateHighlights('route');showMapOnPhone();});
  capture('focus-person','click',()=>{locateHighlights('person');showMapOnPhone();});
  for(const id of ['zoom-in','zoom-out'])$(id).addEventListener('click',showMapOnPhone);
  $('canvas').addEventListener('keydown',e=>{
    const view=live.viewport();if(e.key==='Home'){fitMap();e.preventDefault();return;}
    if(e.key==='+'||e.key==='=')live.zoomBy(1.5);
    else if(e.key==='-')live.zoomBy(1/1.5);
    else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){view.pan.x+=e.key==='ArrowLeft'?50:e.key==='ArrowRight'?-50:0;view.pan.y+=e.key==='ArrowUp'?50:e.key==='ArrowDown'?-50:0;}
    else return;e.preventDefault();live.redraw();
  });
  window.StafferExplorer={get active(){return active;},get ready(){return loaded;},get sourceSelected(){return sourceSelected;},info,renderActivities,renderNativeSources,
    leave(){active=true;sourceSelected=null;sourceHistory.length=0;returnPerson=null;catalogRequest++;info();},results(){return loaded;},
    selectPerson(id){active=true;if(id){sourceSelected=null;sourceHistory.length=0;catalogRequest++;}if(id!==focusedPerson){focusedPerson=id;neighborEdges=[];neighborIds=[];}locateHighlights(id?'person':null);info();},
    showRoute(){active=true;sourceSelected=null;catalogRequest++;locateHighlights('route');info();},
    clearRoute(){locateHighlights();if(!live.viewport().selected)fitMap();info();},
    fitView(){if(live.viewport().routeNodes.size)locateHighlights('route');else fitMap();},
    showNeighborhood(data){neighborEdges=data.edges||[];neighborIds=[...new Set(neighborEdges.flatMap(e=>[e.source,e.target]))];locateHighlights('person');},
    pick(x,y){if(!active)return false;const view=live.viewport();for(const point of highlightPoints){const p=live.screenPoint(point);if(Math.hypot(p.x-x,p.y-y)<12){select(point.row);return true;}}let best=null,distance=30;for(const t of tiles){const p=live.screenPoint(t),d=Math.hypot(p.x-x,p.y-y);if(d<distance){distance=d;best=t;}}
      if(best?.row)select(best.row);else if(best){live.zoomBy(2.5);view.pan={x:-best.x*view.zoom,y:-best.y*view.zoom};live.redraw();}return true;},
    draw(ctx,view){
      const size=view.width+'|'+view.height;if(size!==lastSize){const firstSize=!lastSize;lastSize=size;if(firstSize&&loaded&&active)fitMap();}
      const key=[epoch,view.width,view.height,view.zoom,view.pan.x,view.pan.y].join('|');
      if(loaded&&key!==lastView&&key!==pendingView){pendingView=key;worker.postMessage({type:'view',epoch,key,zoom:view.zoom,pan:view.pan,width:view.width,height:view.height});}
      const dpr=window.devicePixelRatio||1;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,view.width,view.height);ctx.save();const clip=live.mapRect();ctx.beginPath();ctx.rect(clip.left,clip.top,clip.right-clip.left,clip.bottom-clip.top);ctx.clip();
      ctx.globalAlpha=(view.routeNodes.size||view.selected) ? .36 : 1;
      for(const t of tiles){const p=live.screenPoint(t),r=t.count===1?4:Math.min(21,9+Math.log10(t.count)*2.5);ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fillStyle=t.count===1?'#8cc3c4':'#eca91c';ctx.fill();if(t.count>1){ctx.fillStyle='#342649';ctx.font='600 12px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(t.count>=1000?(t.count/1000).toFixed(1)+'k':String(t.count),p.x,p.y);}else if(view.zoom>1){ctx.font='12px system-ui';ctx.textAlign='left';ctx.fillStyle='#f7ede4';ctx.fillText(t.row[1],p.x+8,p.y,150);}}
      const points=new Map(highlightPoints.map(p=>[p.id,{...p,...live.screenPoint(p)}]));
      let drawnRouteEdges=0;
      for(const [edges,route] of [[neighborEdges,false],[view.routeEdges,true]]){ctx.strokeStyle=route?'#eca91c':'#8cc3c4';ctx.lineWidth=route?3:1.5;ctx.globalAlpha=route?1:.8;ctx.beginPath();for(const edge of edges){const a=points.get(edge.source),b=points.get(edge.target);if(a&&b){ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);if(route)drawnRouteEdges++;}}ctx.stroke();}
      $('canvas').dataset.routeEdgesDrawn=String(drawnRouteEdges);
      ctx.globalAlpha=1;ctx.textAlign='left';ctx.textBaseline='alphabetic';ctx.font='600 12px system-ui';const labels=[];
      for(const edge of view.routeEdges){if(!edge.via_organization)continue;const a=points.get(edge.source),b=points.get(edge.target);if(!a||!b)continue;const x=(a.x+b.x)/2,y=(a.y+b.y)/2,label='Via client: '+edge.via_organization.label,w=Math.min(220,ctx.measureText(label).width);ctx.fillStyle='#342649';ctx.fillRect(x-4,y-15,w+8,21);ctx.strokeStyle='#eca91c';ctx.strokeRect(x-4,y-15,w+8,21);ctx.fillStyle='#f7ede4';ctx.fillText(label,x,y,220);labels.push({x,y,w});}
      const ordered=[...points.values()].sort((a,b)=>Number(b.id===view.selected)-Number(a.id===view.selected)||Number(view.routeNodes.has(b.id))-Number(view.routeNodes.has(a.id)));
      for(const p of ordered){if(p.x<clip.left||p.x>clip.right||p.y<clip.top||p.y>clip.bottom)continue;const important=view.routeNodes.has(p.id)||p.id===view.selected;ctx.fillStyle=important?'#eca91c':'#8cc3c4';ctx.beginPath();ctx.arc(p.x,p.y,important?7:5,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#342649';ctx.lineWidth=2;ctx.stroke();
        const name=p.row[1],width=Math.min(170,ctx.measureText(name).width);let spot=null;
        for(const dy of [-12,22,-30,40,-48,58]){const x=Math.min(clip.right-width-3,Math.max(clip.left+3,p.x+10)),y=p.y+dy;if(y-14<clip.top||y>clip.bottom)continue;if(!labels.some(l=>x<l.x+l.w&&x+width>l.x&&y>l.y-16&&y-16<l.y)){spot={x,y,w:width};break;}}
        if(spot){labels.push(spot);ctx.strokeStyle='#342649';ctx.lineWidth=4;ctx.strokeText(name,spot.x,spot.y,170);ctx.fillStyle='#f7ede4';ctx.fillText(name,spot.x,spot.y,170);}}
      ctx.restore();info();
    }};
  request('load');
})();
