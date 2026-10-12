/* Chart Studio, round 5 (2026-09-24), in Paul's words:
     "Additionally, within the Chart Studio make sure everything is freely flexible, that all graph types
      properly register and nothing is logically constrained, it needs to be utterly maluable"
   A full live retest of final/r3/cs/CENSUS.md found four arrangements the Studio still refused or reduced:
   "+ Compare with..." drew only bars, lines, areas, circles and tables, without Colour or period analysis
   (census B-S32); a board drew a map only in the selected tile (B-S29); there was no way to ask for every
   axis label or every legend member (B-E21, B-E22); and Expand chart grew only the bar charts. Each now draws.
   Offline fixture: the golden index.html with its scripts stripped, the app's own scripts in the console's
   order, a verified server that answers any grouping (honouring member filters), and a stand-in for the
   Graph compare menu that picks the scopes the test names. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const SCRIPTS=['theme.js','ui-state.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js',
  'contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'];
const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');

async function boot(context){
  const page=await context.newPage();
  page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://studio-r5.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://studio-r5.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css','workspace-polish.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';document.body.dataset.view='chart-studio';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[],plot:[],charts:[]};
    const members={company:['Alpha','Beta','Gamma'],cycle:['2021-2022','2023-2024','2025-2026'],party:['DEM','REP'],recipient:['R1','R2','R3'],
      state:['MA','AK'],zip5:['02134','99501'],amount_bin:[0,1000000,2000000]};
    const names={company:'Company',cycle:'Election cycle',party:'Recipient party',recipient:'Recipient committee',state:'Contributor state',zip5:'Contributor ZIP',
      amount:'Selected amount',records:'Observation count',contributors:'Distinct reported donor identities'};
    const isMeasure=id=>['amount','records','contributors'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:isMeasure(id)?'measure':'dimension',
      type:isMeasure(id)?'number':'category',unit:id==='amount'?'cents':isMeasure(id)?'count':undefined,geographic_role:{zip5:'postal_code',state:'state'}[id]}));
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),index_token:'index-1',
      query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    const statistic=(id,seed)=>({amount:100000*(seed+1),records:seed+2,contributors:seed+1,average_amount:5000*(seed+1),
      min_amount:100,q1_amount:1000*(seed+1),median_amount:2000*(seed+1),q3_amount:4000*(seed+1),max_amount:9000*(seed+1),
      first_date:'2023-01-0'+(1+seed%8),last_date:'2024-06-1'+(seed%9),span_days:500+seed,gross_amount:110000*(seed+1)})[id];
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,bases:[{id:'receipt',label:'receipt'}],provenance}],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        let tuples=[{}];
        for(const id of body.dimensions){const list=members[id]||['A','B'];tuples=tuples.flatMap(t=>list.map(v=>({...t,[id]:v})));}
        // Member filters are honoured, so a compared company or cycle returns only its own groups.
        for(const f of body.filters||[])if(body.dimensions.includes(f.field)){if(f.op==='in')tuples=tuples.filter(t=>f.value.map(String).includes(String(t[f.field])));if(f.op==='eq')tuples=tuples.filter(t=>String(t[f.field])===String(f.value));}
        const rows=tuples.slice(0,200).map((t,seed)=>Object.assign({},t,...body.measures.map(id=>({[id]:statistic(id,seed)}))));
        return respond({status:'ready',rows:rows.slice(body.offset||0,(body.offset||0)+body.limit),
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)||{id,name:id,role:'measure',type:'number'}),
          totals:{amount:1000000,records:30,contributors:12},comparison:{receipt:1000000},group_count:rows.length,
          offset:body.offset||0,limit:body.limit,has_more:false,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:false,conserved:true},provenance,notices:[],verified_unique:false});
      }
      if(url.endsWith('/geography'))return respond({points:[{zip5:'02134',longitude:-71.13,latitude:42.36},{zip5:'99501',longitude:-149.86,latitude:61.22}],
        layers:{states:{type:'FeatureCollection',features:[]},districts:{type:'FeatureCollection',features:[]},zip_areas:{type:'FeatureCollection',features:[]}},
        coverage:{unmatched_zips:[]},warnings:[],sources:[{label:'Fixture Census',sha256:'g'.repeat(64)}],congress:body.congress});
      if(url.endsWith('/records'))return respond({status:'ready',rows:[],total_records:0,has_more:false,provenance});
      throw Error('Unexpected endpoint '+url);
    };
    window.api=async url=>url==='/api/report-sources'?{runs:[{id:'published-reports',name:'Fixture reports'}]}:url.startsWith('/api/reports?')?{reports:[]}:{sheets:[]};
    window.MentatExport={download:()=>{},image:async()=>{}};
    // The Graph compare menu (graph.js) is not on this page: a stand-in picks the scopes the test queues,
    // and draws the pills with a "+" that asks for one more, as the real pills do.
    window.nextPicks=[];
    window.MentatCompareMenu={
      open:(anchor,opts)=>{const kind=(opts.kinds||[]).includes(window.pickKind)?window.pickKind:(opts.kinds||['company'])[0];const value=window.nextPicks.shift();if(value!=null)setTimeout(()=>opts.onPick(kind,value),0);return {close(){}};},
      pills:(host,opts)=>{const draw=values=>{const add=document.createElement('button');add.className='compare-pills-add';add.textContent='+';add.onclick=()=>opts.onAdd(add);
        host.replaceChildren(...values.map(v=>{const s=document.createElement('span');s.className='scope-pill-text';s.textContent=v.label;return s;}),add);};
        draw(opts.values);return {update:draw,destroy(){host.replaceChildren();}};}
    };
  });
  for(const file of SCRIPTS)await page.addScriptTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    const plot=MentatContributionPlot.render;MentatContributionPlot.render=(c,rows,spec,...rest)=>{calls.plot.push({type:spec.type,axisLabels:spec.axisLabels,color:spec.color});return plot(c,rows,spec,...rest);};
    const charts=MentatContributionCharts.render;MentatContributionCharts.render=(c,rows,spec,...rest)=>{calls.charts.push({type:spec.type,color:spec.color,dimensions:spec.dimensions});return charts(c,rows,spec,...rest);};
  });
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg'));
  return {page,errors};
}
const settled=async page=>{await page.waitForTimeout(120);await page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');await page.waitForTimeout(150);};
// The Studio's own restore path puts a saved arrangement in place (the same one a reload uses).
async function arrange(page,sheets,board=null){
  await page.evaluate(({sheets,board})=>{
    const STORE='mentat-chart-studio-v1',store=JSON.parse(localStorage.getItem(STORE)||'{}');
    const key=Object.keys(store).find(k=>k.startsWith('contributions|Banking|'));
    store[key]={sheets,active:0,board};
    const persist=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k===STORE)return;return persist.call(this,k,v);};
    persist.call(localStorage,STORE,JSON.stringify(store));
    document.getElementById('studio-dataset').dispatchEvent(new Event('change'));
    Storage.prototype.setItem=persist;
  },{sheets,board});
  await settled(page);
}
function sheet(id,{columns=['party'],rows=['amount'],type='bar',color=null,transform='none',params={}}={}){
  return {id,name:'Sheet '+id,columns:columns.map(f=>({column:FIELD_INDEX[f],aggregation:'sum'})),rows:rows.map(f=>({column:FIELD_INDEX[f],aggregation:'sum'})),
    marks:{type,color:color?{column:FIELD_INDEX[color],aggregation:'sum'}:null,size:null,label:null,detail:[],tooltip:[]},filters:[],pages:null,pageMember:'',
    analytics:{transform,period:'',horizon:2,totals:false,average:false,median:false},types:{},params:Object.assign({start:0,end:0,limit:0,subject:'',maxGroups:0,splitMulti:false},params),
    sort:'none',showLabels:true,fit:'standard',source:{}};
}
// Catalog order in the fixture: contributions.fields() numbers each field by its position.
const FIELD_INDEX={company:0,cycle:1,party:2,recipient:3,state:4,zip5:5,amount:6,records:7,contributors:8};

test('B-S32: "+ Compare with..." draws both scopes in every mark, with Colour and with a period analysis',{timeout:300000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const {page,errors}=await boot(context);
  const cases=[
    ['bars by party',{columns:['party']}],
    ['heat map',{columns:['party','recipient'],type:'heatmap'}],
    ['pie',{columns:['party'],type:'pie'}],
    ['treemap',{columns:['recipient'],type:'treemap'}],
    ['stacked bars',{columns:['party','recipient'],type:'stacked'}],
    ['circle view',{columns:['party'],type:'dot'}],
    ['box plot',{columns:['party'],type:'box'}],
    ['histogram',{columns:[],type:'histogram'}],
    ['dual combination',{columns:['cycle'],rows:['amount','records'],type:'combination'}],
    ['bars with Colour',{columns:['recipient'],color:'party'}],
    ['period change',{columns:['cycle'],type:'line',transform:'change'}],
    ['filled map',{columns:['state'],type:'filled-map'}]
  ];
  for(const [name,arrangement] of cases){
    await arrange(page,[sheet('s1',arrangement)]);
    await page.evaluate(()=>{window.pickKind='company';window.nextPicks=['Alpha','Beta'];});
    await page.locator('#studio-compare-add').click();
    await settled(page);
    if(await page.locator('#studio-compare-note').isVisible()){await page.locator('#studio-compare-pills .compare-pills-add').click();await settled(page);}
    await page.waitForFunction(()=>/compared: Alpha vs Beta/.test(document.getElementById('studio-subtitle').textContent));
    await settled(page);
    const view=await page.evaluate(()=>({text:document.getElementById('studio-canvas').textContent,maps:document.querySelectorAll('#studio-canvas .contribution-map').length,
      svg:!!document.querySelector('#studio-canvas svg'),note:document.getElementById('studio-note').textContent,
      error:document.getElementById('studio-error').hidden?'':document.getElementById('studio-error').textContent}));
    assert.equal(view.error,'',name+' draws without an error');
    assert.doesNotMatch(view.note,/own scope|could not draw/,name+' is not reduced to one scope or to bars');
    if(arrangement.type==='filled-map'){
      assert.equal(view.maps,2,'a map compares its scopes side by side');
      assert.match(view.note,/Each compared scope is its own map on one shading scale/);
    }else{
      assert.ok(view.svg,name+' draws');
      assert.match(view.text,/Alpha/,name+' names the first scope');
      assert.match(view.text,/Beta/,name+' names the second scope');
    }
    if(arrangement.color)assert.match(view.text,/Alpha · DEM|Alpha · REP/,'Colour pairs with the scope');
    await page.locator('#studio-compare-clear').click();
    await settled(page);
  }
  assert.deepEqual(errors,[]);
});

test('B-S29, B-E21/B-E22 and Expand chart: a map in every tile, every label when asked, Escape and fit',{timeout:240000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const {page,errors}=await boot(context);

  // B-S29: in Canvas mode, a board tile that is not selected draws its own map.
  await page.locator('#studio-view-canvas').click();
  await arrange(page,[sheet('s1',{columns:['company']}),sheet('s2',{columns:['state'],type:'filled-map'})],{tiles:2,slots:[0,1]});
  await page.waitForFunction(()=>document.querySelectorAll('#studio-board > .studio-board-tile').length===2&&document.querySelectorAll('#studio-board .contribution-map').length===1);
  const tiles=await page.evaluate(()=>[...document.querySelectorAll('#studio-board > .studio-board-tile')].map(t=>({map:!!t.querySelector('.contribution-map'),svg:!!t.querySelector('svg'),text:t.textContent})));
  assert.equal(tiles[0].map,false);
  assert.equal(tiles[1].map,true,'the unselected tile draws its map');
  assert.ok(tiles.every(t=>!/select this tile/i.test(t.text)),'no tile is told to be selected first');
  // Two maps side by side, each its own instance.
  await arrange(page,[sheet('s1',{columns:['zip5'],type:'map'}),sheet('s2',{columns:['state'],type:'filled-map'})],{tiles:2,slots:[0,1]});
  await page.waitForFunction(()=>document.querySelectorAll('#studio-board .contribution-map').length===2);
  await page.locator('#studio-view-builder').click();
  await settled(page);

  // B-E21/B-E22: Parameters carry "every axis label" and "every legend member" to the renderers.
  await arrange(page,[sheet('s1',{columns:['recipient'],type:'line'})]);
  await page.evaluate(()=>{document.getElementById('studio-params-details').open=true;});
  assert.equal(await page.locator('#studio-param-axis-labels').isChecked(),false);
  await page.locator('#studio-param-axis-labels').check();
  await settled(page);
  assert.equal(await page.evaluate(()=>calls.plot.at(-1).axisLabels),'all','every axis label reaches the renderer');
  await page.locator('#studio-param-legend').check();
  await settled(page);
  assert.equal(await page.evaluate(()=>{const s=JSON.parse(localStorage.getItem('mentat-chart-studio-v1'));return Object.values(s)[0].sheets[0].params.legend;}),'all');
  await page.locator('#studio-param-axis-labels').uncheck();
  await settled(page);
  assert.equal(await page.evaluate(()=>calls.plot.at(-1).axisLabels),undefined,'unchecked, the renderer thins as before');

  // Expand chart: a chart drawn at a fixed size scales into the room, and Escape brings the panes back.
  await arrange(page,[sheet('s1',{columns:['company','party'],type:'heatmap'})]);
  const before=await page.evaluate(()=>Math.round(document.querySelector('#studio-canvas svg').getBoundingClientRect().height));
  await page.locator('#studio-expand').click();
  await page.waitForFunction(()=>document.querySelector('#view-chart-studio .studio').classList.contains('studio-chart-expanded'));
  await page.waitForTimeout(400);
  const grown=await page.evaluate(()=>{const c=document.getElementById('studio-canvas'),s=c.querySelector('svg');return {h:Math.round(s.getBoundingClientRect().height),w:s.style.width,fits:s.getBoundingClientRect().height<=c.clientHeight+1&&s.getBoundingClientRect().width<=c.clientWidth+1};});
  assert.ok(grown.h>before,'the heat map grows into the room ('+before+' -> '+grown.h+')');
  assert.ok(grown.fits,'and fits the canvas without scrolling');
  await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.querySelector('#view-chart-studio .studio').classList.contains('studio-chart-expanded'));
  assert.equal(await page.evaluate(()=>document.activeElement&&document.activeElement.id),'studio-expand','focus returns to the Expand chart button');
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>document.querySelector('#studio-canvas svg').style.width),'','panes back: the chart returns to its own size');
  assert.deepEqual(errors,[]);
});

// r5 re-audit MEN-A-REAUDIT-1: Expand chart fitted a fixed-size drawing only on the next animation frame, and a
// document the browser is not painting (a background tab, a minimised window) runs none. On a background tab a
// treemap stayed 1000x560 and scrolled in the 1171x501 room, and a line stayed 1000x420. Here the page paints no
// frame at all while each mark is drawn, expanded (mark first, then Expand; and Expand first, then the mark) and
// put back.
test('Expand chart fits every fixed-size mark into the room without an animation frame (MEN-A-REAUDIT-1)',{timeout:240000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const {page,errors}=await boot(context);
  await arrange(page,[sheet('s1',{columns:['recipient']})]);
  // From here on the page runs no animation frames, as a background tab does.
  await page.evaluate(()=>{window.paintFrame=window.requestAnimationFrame;window.requestAnimationFrame=()=>0;});
  const poll={polling:100,timeout:20000};
  const drawn=[];
  for(const mark of ['treemap','line','heatmap','pie']){
    await page.evaluate(()=>document.querySelector('#studio-canvas > svg')?.setAttribute('data-drawn-before',''));
    // The mark menu is chosen the way its change event reaches the Studio (the menu sits in a folded card here).
    await page.evaluate(mark=>{const menu=document.getElementById('studio-mark');menu.value=mark;menu.dispatchEvent(new Event('change',{bubbles:true}));},mark);
    assert.equal(await page.evaluate(()=>document.getElementById('studio-mark').value),mark,mark+' is on the mark menu');
    await page.waitForFunction(()=>{const svg=document.querySelector('#studio-canvas > svg');
      return svg&&!svg.hasAttribute('data-drawn-before')&&document.getElementById('studio-canvas').getAttribute('aria-busy')!=='true';},undefined,poll);
    if(!drawn.length){
      await page.locator('#studio-expand').click();
      await page.waitForFunction(()=>document.querySelector('#view-chart-studio .studio').classList.contains('studio-chart-expanded'),undefined,poll);
    }
    const size=await page.evaluate(()=>{const canvas=document.getElementById('studio-canvas'),svg=canvas.querySelector(':scope > svg'),box=svg.getBoundingClientRect();
      return {w:box.width,h:box.height,room:canvas.clientWidth,tall:canvas.clientHeight,style:svg.style.width,native:svg.hasAttribute('data-native'),
        scrolls:canvas.scrollHeight>canvas.clientHeight+1||canvas.scrollWidth>canvas.clientWidth+1};});
    drawn.push({mark,...size});
  }
  for(const size of drawn){
    const label=size.mark+' '+JSON.stringify(size);
    assert.equal(size.native,false,label+': a fixed-size drawing');
    assert.notEqual(size.style,'',label+': sized to the room');
    assert.ok(size.w<=size.room+1&&size.h<=size.tall+1&&!size.scrolls,label+': fits the expanded canvas without scrolling');
    assert.ok(Math.max(size.w/size.room,size.h/size.tall)>=0.97,label+': fills the room in one direction');
  }
  await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.querySelector('#view-chart-studio .studio').classList.contains('studio-chart-expanded'),undefined,poll);
  assert.equal(await page.evaluate(()=>document.activeElement&&document.activeElement.id),'studio-expand','focus returns to the Expand chart button');
  assert.equal(await page.evaluate(()=>document.querySelector('#studio-canvas > svg').style.width),'','panes back, still without a frame: the chart returns to its own size');
  await page.evaluate(()=>{window.requestAnimationFrame=window.paintFrame;});
  assert.deepEqual(errors,[]);
});
