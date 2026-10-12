/* One v8-reconciled population. Search changes the result list, never the map. */
'use strict';
let rows=[],matches=[],positions,query={},ready=false,epoch=0,latestQuery=null,loadGeneration=0,bounds={},byId=new Map(),aliases={};
const hash=s=>{let h=2166136261;for(const c of String(s)){h=Math.imul(h^c.charCodeAt(0),16777619);}return h>>>0;};
function rowPosition(r){
  const h=hash(r[3]||r[6]),a=(h%10000)/10000*Math.PI*2;
  const radius=r[3]?120+Math.sqrt((h>>>8)%10000)*7:0;
  const jitter=hash(r[0]),theta=(jitter%10000)/10000*Math.PI*2;
  const spread=r[3]?20+Math.sqrt((jitter>>>8)%10000)*1.7:Math.sqrt((jitter>>>8)%10000)*9;
  return {x:Math.fround(Math.cos(a)*radius+Math.cos(theta)*spread),y:Math.fround(Math.sin(a)*radius+Math.sin(theta)*spread)};
}
function arrange(){
  positions=new Float32Array(rows.length*2);
  bounds={minX:Infinity,maxX:-Infinity,minY:Infinity,maxY:-Infinity};
  for(let i=0;i<rows.length;i++){
    const p=rowPosition(rows[i]);positions[i*2]=p.x;positions[i*2+1]=p.y;
    bounds.minX=Math.min(bounds.minX,positions[i*2]);bounds.maxX=Math.max(bounds.maxX,positions[i*2]);
    bounds.minY=Math.min(bounds.minY,positions[i*2+1]);bounds.maxY=Math.max(bounds.maxY,positions[i*2+1]);
  }
  if(!rows.length)bounds={minX:-500,maxX:500,minY:-500,maxY:500};
}
// An identifier is searched whole, never by its pieces (D-083): column 0, the node id, and columns 3 to 5, the member, committee and policy ids, are not
// part of the searched text. A query that is a row's whole id (trimmed, in any case), or the bare uuid of a politico:<uuid> id, finds that row and lists it
// first; a query that is the whole member, committee or policy id of a row lists that row next, ahead of the rows that match by words.
// One part of an id is text a person reads. A committee id is container:office:<chamber>:<committee name>; the name after the prefix is searched like any
// other name (column 4 only, so a person's first committee), and the prefix before it never is. An id of any other shape adds nothing to the words.
// The filter runs over every row on every keystroke, so the words of a committee name are worked out once per distinct committee id (about a hundred), not per row.
const POLITICO='politico:';
const COMMITTEE=/^container:office:(?:house|senate):(.+)$/i,NO_WORDS=[],committeeWords=new Map();
function committeeTokens(code){
  if(!code)return NO_WORDS;
  let words=committeeWords.get(code);
  if(words===undefined){
    const m=COMMITTEE.exec(code);
    if(!m)return NO_WORDS;
    words=m[1].normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||NO_WORDS;
    committeeWords.set(code,words);
  }
  return words;
}
const wholeId=(id,want)=>{id=String(id).toLowerCase();return id===want||(id.startsWith(POLITICO)&&id.length>POLITICO.length&&id.slice(POLITICO.length)===want);};
const wholeCode=(code,want)=>code!==null&&code!==undefined&&String(code).toLowerCase()===want;
function filter(){
  const text=query.text||'',words=text.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
  const want=text.trim().toLowerCase(),named=[],attached=[],others=[];
  for(let i=0;i<rows.length;i++){
    const r=rows[i];
    if(want&&wholeId(r[0],want)){named.push(i);continue;}
    if(want&&(wholeCode(r[3],want)||wholeCode(r[4],want)||wholeCode(r[5],want))){attached.push(i);continue;}
    if(words.length){const tokens=[r[1],r[2],r[6],r[11]||''].join(' ').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[],named=committeeTokens(r[4]);if(!words.every(w=>tokens.some(token=>token.startsWith(w))||named.some(token=>token.startsWith(w))))continue;}
    others.push(i);
  }
  matches=named.concat(attached,others);
}
function page(request){
  const pages=Math.max(1,Math.ceil(matches.length/50)),number=Math.min(Math.max(0,request.page||0),pages-1);
  postMessage({type:'page',epoch,total:matches.length,page:number,pages,bounds,rows:matches.slice(number*50,number*50+50).map(i=>rows[i])});
}
function tiles(view){
  const size=64/Math.max(.01,view.zoom),bins=new Map();
  for(let i=0;i<rows.length;i++){
    const x=positions[i*2],y=positions[i*2+1],sx=view.width/2+view.pan.x+x*view.zoom,sy=view.height/2+view.pan.y+y*view.zoom;
    if(sx < -50||sy < -50||sx > view.width+50||sy > view.height+50)continue;
    const key=Math.floor(x/size)+':'+Math.floor(y/size);
    let cell=bins.get(key);
    if(!cell){cell={x:0,y:0,count:0,first:i};bins.set(key,cell);}
    cell.x+=x;cell.y+=y;cell.count++;
  }
  postMessage({type:'tiles',epoch,viewKey:view.key,tiles:[...bins.values()].map(c=>({x:c.x/c.count,y:c.y/c.count,count:c.count,row:c.count===1?rows[c.first]:null}))});
}
function locate(request){
  const points=[],missing=[];
  const supplied=new Map((request.nodes||[]).filter(n=>n.kind==='person').map(n=>[n.id,n]));
  for(let id of request.ids||[]){
    const seen=new Set();while(aliases[id]&&!seen.has(id)){seen.add(id);id=aliases[id];}
    const i=byId.get(id);
    if(i===undefined){
      const n=supplied.get(id);
      if(!n){missing.push(id);continue;}
      // Route evidence can include historical people outside the browse population.
      // Position those supplied records without changing the inventory or inventing edges.
      const row=[id,n.label||n.name||id,n.source_summary||'',n.member_ids?.[0]||'',n.committee_ids?.[0]||'',n.policy_ids?.[0]||'',n.source_system||n.source||'Payroll',false,'person'];
      points.push({id,...rowPosition(row),row,overlay:true});continue;
    }
    points.push({id,x:positions[i*2],y:positions[i*2+1],row:rows[i]});
  }
  postMessage({type:'located',epoch,request:request.request,points,missing});
}
onmessage=async event=>{
  let m=event.data;
  if(m.type==='load'||m.type==='query')latestQuery=m;
  try{
    if(m.type==='load'){
      const generation=++loadGeneration;
      const scope=m.scope==='network'?'network':'sources';
      const response=await fetch('/api/staffer-network-index?scope='+scope,{cache:'no-cache'});
      if(!response.ok)throw Error('Inventory HTTP '+response.status);
      const data=await response.json();
      if(generation!==loadGeneration)return;
      if(!data.ready){postMessage({type:'waiting',stage:data.stage});return;}
      m=latestQuery||m;
      rows=data.people;aliases=data.identity_aliases||{};byId=new Map(rows.map((r,i)=>[r[0],i]));ready=true;query=m;epoch=m.epoch;arrange();filter();
      const networkCounts={};for(const row of rows)if(row[13]&&row[8]==='person')for(const source of row[6].split('|'))networkCounts[source]=(networkCounts[source]||0)+1;
      postMessage({type:'ready',epoch,scope:data.scope,revision:data.revision,total:data.total,people_total:data.people_total,routing_people_total:data.routing_people_total,source_counts:data.source_counts,kind_counts:data.kind_counts,complete:data.complete_inventory,note:data.headcount_note,reconciliation:data.reconciliation,network_source_counts:networkCounts,catalog:data.source_catalog?{counts:data.source_catalog.counts,categories:data.source_catalog.categories}:null,ldaCatalog:data.lda_source_catalog?{counts:data.lda_source_catalog.counts}:null});
      page(m);if(m.person){let id=m.person;const seen=new Set();while(data.identity_aliases?.[id]&&!seen.has(id)){seen.add(id);id=data.identity_aliases[id];}postMessage({type:'selection',epoch,id,requestedPerson:m.person,row:rows.find(r=>r[0]===id)||null});}
    }else if(ready&&m.type==='query'){
      const regroup=query.group!==m.group;query=m;epoch=m.epoch;if(regroup)arrange();filter();page(m);
    }else if(ready&&m.type==='page')page(m);
    else if(ready&&m.type==='view'&&m.epoch===epoch)tiles(m);
    else if(ready&&m.type==='locate'&&m.epoch===epoch)locate(m);
  }catch(error){postMessage({type:'error',message:error.message});}
};
