/* Chart Studio, round 3 (2026-09-23), in Paul's words:
     "You should be able to expand each section individually like with a census panel"
     "Additionally, within the Chart Studio make sure everything is freely flexible, that all graph types
      properly register and nothing is logically constrained, it needs to be utterly maluable"
   Offline fixture: the golden index.html with its scripts stripped, the app's own scripts added in the
   console's order, and a verified server (and a report worksheet) that answer any question. */
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

// A verified server that answers any grouping of these members with deterministic, positive figures, and a
// report worksheet with an identifier column. `worksheet` starts the console in Report worksheet mode.
async function boot(context,{worksheet=false}={}){
  const page=await context.newPage();
  page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://studio-freedom.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://studio-freedom.fixture/'+(worksheet?'?worksheet':'')+'#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css','workspace-polish.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[],geography:[],build:[]};
    const members={company:['Alpha','Beta','Gamma','Delta'],cycle:['2021-2022','2023-2024','2025-2026'],year:['2022','2023','2024'],
      zip5:['02134','99501'],state:['MA','AK'],recipient:['R1','R2','R3'],party:['DEM','REP'],amount_bin:[0,1000000,2000000]};
    const names={company:'Company',cycle:'Election cycle',year:'Contribution year',zip5:'Contributor ZIP',state:'Contributor state',
      recipient:'Recipient committee',party:'Recipient party',amount:'Selected amount',records:'Observation count',contributors:'Distinct reported donor identities'};
    const isMeasure=id=>['amount','records','contributors'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:isMeasure(id)?'measure':'dimension',
      type:isMeasure(id)?'number':'category',unit:id==='amount'?'cents':isMeasure(id)?'count':undefined}));
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),index_token:'index-1',
      query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    const statistic=(id,seed)=>({amount:100000*(seed+1),records:seed+2,contributors:seed+1,average_amount:5000*(seed+1),
      min_amount:100,q1_amount:1000*(seed+1),median_amount:2000*(seed+1),q3_amount:4000*(seed+1),max_amount:9000*(seed+1),
      first_date:'2023-01-0'+(1+seed%8),last_date:'2024-06-1'+(seed%9),span_days:500+seed,gross_amount:110000*(seed+1)})[id];
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,
        bases:[{id:'receipt',label:'receipt'}],provenance}],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        let tuples=[{}];
        for(const id of body.dimensions){const list=members[id]||['A','B'];tuples=tuples.flatMap(t=>list.map(v=>({...t,[id]:v})));}
        tuples=tuples.slice(0,60);
        const rows=tuples.map((t,seed)=>Object.assign({},t,...body.measures.map(id=>({[id]:statistic(id,seed)}))));
        const order=(body.sort||[])[0];
        if(order)rows.sort((a,b)=>{const x=a[order.field],y=b[order.field];const c=typeof x==='number'?x-y:String(x).localeCompare(String(y));return order.direction==='asc'?c:-c;});
        return respond({status:'ready',rows:rows.slice(body.offset||0,(body.offset||0)+body.limit),
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)||{id,name:id,role:'measure',type:'number'}),
          totals:{amount:1000000,records:30,contributors:12},comparison:{receipt:1000000},group_count:rows.length,
          offset:body.offset||0,limit:body.limit,has_more:false,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:false,conserved:true},provenance,notices:[],verified_unique:false});
      }
      if(url.endsWith('/geography')){calls.geography.push(body);return respond({points:[{zip5:'02134',longitude:-71.13,latitude:42.36},{zip5:'99501',longitude:-149.86,latitude:61.22}],
        layers:{states:{type:'FeatureCollection',features:[]},districts:{type:'FeatureCollection',features:[]},zip_areas:{type:'FeatureCollection',features:[]}},
        coverage:{unmatched_zips:[]},warnings:[],sources:[{label:'Fixture Census',sha256:'g'.repeat(64)}],congress:body.congress});}
      if(url.endsWith('/records'))return respond({status:'ready',rows:[],total_records:0,has_more:false,provenance});
      throw Error('Unexpected endpoint '+url);
    };
    const worksheet={columns:[{index:0,name:'Recipient Category',type:'category'},{index:1,name:'Committee ID',type:'category'},
      {index:2,name:'Election Cycle',type:'category'},{index:3,name:'Amount',type:'number'},{index:4,name:'Contribution Count',type:'number'},
      {index:5,name:'Date',type:'date'}],
      rows:[['Company PAC','C001','2021-2022',1000,4,'2021-03-01'],['Trade Association','C002','2021-2022',500,2,'2021-06-01'],
        ['Company PAC','C001','2023-2024',2000,6,'2023-03-01'],['Leadership PAC','C003','2023-2024',250,1,'2023-09-01'],
        ['Company PAC','C001','2025-2026',3000,9,'2025-02-01'],['Trade Association','C002','2025-2026',750,3,'2025-07-01']],
      row_numbers:[6,7,8,9,10,11],row_count:6,header_row:5,sheet:'Facts',sha256:'worksheet-fixture',note:'Worksheet fixture'};
    window.api=async url=>url==='/api/report-sources'?{runs:[{id:'published-reports',name:'Fixture reports'}]}
      :url.startsWith('/api/reports?')?{reports:[{index:0,name:'Fixture.xlsx'}]}:url.includes('sheet=')?worksheet:{sheets:['Facts']};
    window.MentatExport={download:()=>{},image:async()=>{}};
  });
  for(const file of SCRIPTS)await page.addScriptTag({path:path.join(app,file)});
  // Every configuration the worksheet engine is asked to build, so the test reads what the studio sent.
  await page.evaluate(()=>{const build=MentatCharts.build;MentatCharts.build=(data,config)=>{calls.build.push(JSON.parse(JSON.stringify(config)));return build(data,config);};});
  await page.waitForFunction(worksheet?()=>document.querySelector('#studio-loading').textContent.includes('6 rows'):()=>document.querySelector('#studio-canvas svg'));
  return {page,errors};
}
const settled=page=>page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
const drawn=page=>page.evaluate(()=>({svg:!!document.querySelector('#studio-canvas svg'),table:!!document.querySelector('#studio-canvas table, #studio-table:not([hidden]) table'),
  map:!!document.querySelector('#studio-canvas .contribution-map'),error:document.getElementById('studio-error').hidden?'':document.getElementById('studio-error').textContent,
  note:document.getElementById('studio-note').textContent}));

test('every section folds on its own header, opens as S1 draws it, and is remembered per section',{timeout:180000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const {page,errors}=await boot(context);
  const folds=await page.evaluate(()=>[...document.querySelectorAll('#view-chart-studio button.studio-fold[data-fold]')].map(b=>[b.dataset.fold,b.getAttribute('aria-expanded')]));
  assert.deepEqual(folds.map(f=>f[0]),['dimensions','measures','shelf-columns','shelf-rows','shelf-filters','marks','enc-color','enc-size','enc-label',
    'enc-detail','enc-tooltip','enc-shape','enc-path','enc-angle','analytics-period','analytics-summarize','transform-params','filters-active'],'every section has its own fold');
  assert.ok(folds.every(f=>f[1]==='true'),'every section opens unfolded, as S1 draws it');
  // The Inspector's own disclosures keep S1's folded start and are remembered too.
  assert.deepEqual(await page.evaluate(()=>['showme-details','pages-details','params-details'].map(id=>document.getElementById('studio-'+id).open)),[false,false,false]);
  const box=selector=>page.evaluate(s=>{const r=document.querySelector(s).getBoundingClientRect();return {y:Math.round(r.y),h:Math.round(r.height)};},selector);
  const canvasBefore=await box('#studio-canvas'),measuresBefore=await box('#studio-measures');

  // Dimensions folds to its header, which names what it holds; Measures moves up into the room.
  await page.locator('button.studio-fold[data-fold="dimensions"]').click();
  assert.equal(await page.locator('button.studio-fold[data-fold="dimensions"]').getAttribute('aria-expanded'),'false');
  assert.equal(await page.locator('#studio-dimensions').isVisible(),false);
  assert.match(await page.locator('button.studio-fold[data-fold="dimensions"]').innerText(),/Dimensions\s*7 fields/i);
  assert.ok((await box('#studio-measures')).y<measuresBefore.y,'the next section takes the folded one’s place');
  assert.equal(await page.evaluate(()=>localStorage.getItem('mentat-chart-studio-fold:dimensions')),'0');

  // A folded shelf is one line that still names its fields, and the chart grows into the room it gives.
  for(const id of ['shelf-columns','shelf-rows','shelf-filters'])await page.locator('button.studio-fold[data-fold="'+id+'"]').click();
  assert.match(await page.evaluate(()=>getComputedStyle(document.getElementById('studio-columns'),'::after').content),/Company/);
  assert.equal(await page.locator('#studio-columns .studio-pill').first().isVisible(),false);
  assert.ok(await page.evaluate(()=>document.querySelector('#view-chart-studio .studio').classList.contains('studio-shelf-folded')));
  await page.waitForFunction(h=>document.getElementById('studio-canvas').getBoundingClientRect().height>h,canvasBefore.h);

  // The Marks card and each of its properties fold on their own; a folded property keeps taking drops.
  await page.locator('button.studio-fold[data-fold="enc-detail"]').click();
  assert.equal(await page.locator('#studio-detail').isVisible(),true,'a folded property is one line that still takes a drop');
  assert.ok(await page.evaluate(()=>document.getElementById('studio-detail').classList.contains('is-folded-drop')));
  await page.locator('button.studio-fold[data-fold="marks"]').click();
  assert.equal(await page.locator('.studio-mark-props').isVisible(),false);
  await page.locator('button.studio-fold[data-fold="marks"]').click();

  // Analytics and Filters sections too.
  await page.locator('#studio-tab-analytics').click();
  await page.locator('button.studio-fold[data-fold="analytics-summarize"]').click();
  assert.equal(await page.locator('#studio-analytics-summarize').isVisible(),false);
  await page.locator('button.studio-fold[data-fold="transform-params"]').click();
  assert.equal(await page.locator('#studio-param-period').isVisible(),false);
  await page.locator('#studio-tab-filters').click();
  await page.locator('button.studio-fold[data-fold="filters-active"]').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('button.studio-fold[data-fold="filters-active"]').getAttribute('aria-expanded'),'false','the keyboard folds a section');
  await page.locator('#studio-tab-marks').click();
  // Show Me's own disclosure remembers a click on its summary.
  await page.locator('#studio-showme-details > summary').click();
  await page.waitForFunction(()=>localStorage.getItem('mentat-chart-studio-fold:showme')==='1');

  // A fresh page in the same browser opens every section as the reader left it.
  const second=await boot(context);
  const remembered=Object.fromEntries(await second.page.evaluate(()=>[...document.querySelectorAll('#view-chart-studio button.studio-fold[data-fold]')].map(b=>[b.dataset.fold,b.getAttribute('aria-expanded')])));
  for(const id of ['dimensions','shelf-columns','shelf-rows','shelf-filters','enc-detail','analytics-summarize','transform-params','filters-active'])
    assert.equal(remembered[id],'false',id+' is remembered folded');
  for(const id of ['measures','marks','enc-color','enc-size','enc-label','enc-tooltip','analytics-period'])assert.equal(remembered[id],'true',id+' is remembered open');
  assert.equal(await second.page.evaluate(()=>document.getElementById('studio-showme-details').open),true);
  // The whole-pane hides and Expand chart are untouched by the folds.
  for(const id of ['data-collapse','shelves-collapse','expand'])assert.equal(await second.page.locator('#studio-'+id).count(),1);
  await second.page.locator('#studio-expand').click();
  assert.ok(await second.page.evaluate(()=>document.querySelector('#view-chart-studio .studio').classList.contains('studio-chart-expanded')));
  assert.deepEqual([...errors,...second.errors],[]);
});

test('every Show Me tile and every mark draws, pills go anywhere, and nothing is refused',{timeout:300000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const {page,errors}=await boot(context);
  const reset=async()=>{
    await page.locator('#studio-clear').click();
    await page.locator('#studio-dimensions .studio-pill').filter({hasText:'Company'}).first().click();
    await page.locator('#studio-measures .studio-pill').filter({hasText:'Selected amount'}).first().click();
    await settled(page);
  };
  // Every tile is enabled, and a click switches the chart to the tile's mark and draws it.
  await page.locator('#studio-showme-toggle').click();
  const tiles=await page.evaluate(()=>[...document.querySelectorAll('#studio-showme-grid .studio-tile')].map(b=>({id:b.dataset.tile,mark:b.dataset.mark,disabled:b.disabled})));
  assert.equal(tiles.length,24);
  assert.deepEqual(tiles.filter(t=>t.disabled).map(t=>t.id),[],'no tile is greyed');
  for(const tile of tiles){
    await reset();
    await page.locator('#studio-showme-grid .studio-tile[data-tile="'+tile.id+'"]').click();
    await settled(page);
    await page.waitForFunction(()=>document.querySelector('#studio-canvas svg, #studio-canvas table, #studio-canvas .contribution-map'));
    assert.equal(await page.locator('#studio-mark').inputValue(),tile.mark,tile.id+' switches the mark');
    const view=await drawn(page);
    assert.equal(view.error,'',tile.id+' draws without an error');
    assert.ok(view.svg||view.table||view.map,tile.id+' draws something');
  }
  // Every mark in the menu draws from the same shelves.
  const marks=await page.evaluate(()=>[...document.querySelectorAll('#studio-mark option')].map(o=>({value:o.value,disabled:o.disabled})));
  assert.equal(marks.length,30);
  assert.deepEqual(marks.filter(m=>m.disabled),[]);
  for(const {value} of marks){
    await reset();
    await page.locator('#studio-mark').selectOption(value);
    await settled(page);
    await page.waitForFunction(()=>document.querySelector('#studio-canvas svg, #studio-canvas table, #studio-canvas .contribution-map'));
    const view=await drawn(page);
    assert.equal(view.error,'',value+' draws without an error');
  }

  // KEEP-RULED option A: choosing Map re-shelves ZIP (and state) and the amount; afterwards every pill moves.
  await reset();
  await page.locator('#studio-mark').selectOption('map');
  await page.waitForFunction(()=>document.querySelector('#studio-canvas .contribution-map'));
  // The studio reuses an answer it already holds for thirty seconds, so the map query is read from the
  // log by its ZIP dimension rather than as the last request.
  assert.match(await page.locator('#studio-columns').innerText(),/Contributor ZIP/);
  assert.deepEqual(await page.evaluate(()=>calls.query.filter(q=>q.dimensions.includes('zip5')).at(-1).dimensions),['zip5','state']);
  await page.locator('#studio-dimensions .studio-pill').filter({hasText:'Company'}).first().press('Enter');
  await page.getByRole('button',{name:'Send to Columns',exact:true}).click();
  await settled(page);
  await page.waitForFunction(()=>document.querySelector('#studio-canvas .contribution-map'));
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).dimensions),['zip5','company','state'],'a second dimension joins the map query');
  assert.equal((await drawn(page)).error,'');
  assert.match((await drawn(page)).note,/Each map mark combines every Company group at its place/);
  await page.locator('#studio-columns .studio-pill[data-column]').first().press('Delete');
  await settled(page);
  assert.equal((await drawn(page)).error,'','removing ZIP draws the same groups as bars with a note, never a refusal');

  // Any pill onto any shelf: a second copy of a measure, a measure on Colour, two filters on one field.
  await reset();
  await page.locator('#studio-measures .studio-pill').filter({hasText:'Selected amount'}).first().click();
  assert.equal(await page.locator('#studio-columns .studio-pill, #studio-rows .studio-pill').count(),3,'a field can sit on the shelves twice');
  await page.locator('#studio-measures .studio-pill').filter({hasText:'Observation count'}).first().press('Enter');
  await page.getByRole('button',{name:'Send to Colour',exact:true}).click();
  await settled(page);
  assert.equal(await page.locator('#studio-color .studio-pill').count(),1,'a measure sits on Colour');
  assert.equal((await drawn(page)).error,'');
  // The same question may already be answered from the thirty-second cache (the marks above asked it).
  assert.ok(await page.evaluate(()=>calls.query.some(q=>q.dimensions.join()==='company'&&q.measures.join()==='amount,records')),'a Colour measure is fetched');
  for(let n=0;n<2;n++){
    await page.locator('#studio-tab-filters').click();
    await page.locator('#studio-filter-add').selectOption({label:'Company'});
  }
  assert.equal(await page.locator('#studio-filters .studio-pill').count(),2,'a field can carry two filters');
  await page.locator('#studio-tab-marks').click();

  // Sort on a time axis: it opens chronological, and Ascending orders by the measure.
  await reset();
  await page.locator('#studio-columns .studio-pill').first().press('Delete');
  await page.locator('#studio-dimensions .studio-pill').filter({hasText:'Election cycle'}).first().click();
  await page.locator('#studio-mark').selectOption('line');
  await settled(page);
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).sort),[{field:'cycle',direction:'asc'}]);
  await page.locator('#studio-sort > summary').click();
  assert.equal(await page.locator('#studio-sort-asc').isDisabled(),false,'a line sorts');
  await page.locator('#studio-sort-asc').click();
  await settled(page);
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).sort),[{field:'amount',direction:'asc'}]);
  await page.locator('#studio-sort > summary').click();
  await page.locator('#studio-sort-za').click();
  await settled(page);
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).sort),[{field:'cycle',direction:'desc'}],'Z to A orders by the label');

  // The group limit reaches All.
  await page.evaluate(()=>{document.getElementById('studio-params-details').open=true;});
  await page.locator('#studio-param-groups-all').check();
  await settled(page);
  assert.equal(await page.evaluate(()=>calls.query.at(-1).limit),100000);
  assert.equal(await page.locator('#studio-param-groups').getAttribute('placeholder'),'All');
  assert.equal(await page.locator('#studio-param-groups').getAttribute('max'),null,'no client cap on the group limit');
  assert.deepEqual(errors,[]);
});

test('a report worksheet lists its identifier fields and draws every mark and encoding',{timeout:240000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const {page,errors}=await boot(context,{worksheet:true});
  // Identifier columns are listed, under the Dimensions bucket's More fields, not behind a checkbox.
  assert.equal(await page.locator('#studio-show-hidden').count(),0,'the "Show identifier fields" checkbox is retired');
  const more=page.locator('#studio-dimensions details.studio-more-fields');
  assert.equal(await more.count(),1);
  await more.locator('summary').click();
  assert.equal(await more.locator('.studio-pill').filter({hasText:'Committee ID'}).count(),1);
  const pill=name=>page.locator('#studio-dimensions .studio-pill, #studio-measures .studio-pill').filter({hasText:name}).first();
  await page.locator('#studio-clear').click();
  await pill('Recipient Category').click();
  await pill('Amount').click();
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg'));
  // Every mark the shared menu offers draws on a worksheet, each with no error line.
  const marks=await page.evaluate(()=>[...document.querySelectorAll('#studio-mark option')].map(o=>o.value));
  assert.equal(marks.length,30,'the worksheet offers the same marks as Verified contributions');
  for(const value of marks){
    await page.locator('#studio-mark').selectOption(value);
    await page.waitForFunction(()=>document.querySelector('#studio-canvas svg, #studio-table:not([hidden]) table'));
    assert.equal((await drawn(page)).error,'',value+' draws on a worksheet');
  }
  await page.locator('#studio-mark').selectOption('bar');
  // Size, Label and Tooltip reach the engine; a Detail measure rides in the tooltip.
  for(const [name,zone] of [['Contribution Count','Size'],['Election Cycle','Label'],['Contribution Count','Tooltip']]){
    await pill(name).press('Enter');
    await page.getByRole('button',{name:'Send to '+zone,exact:true}).click();
  }
  const config=await page.evaluate(()=>calls.build.at(-1));
  assert.deepEqual(config.size,{column:4,aggregation:'sum'});
  assert.deepEqual(config.label,{column:2,aggregation:'first'});
  assert.deepEqual(config.tooltip,[{column:4,aggregation:'sum'}]);
  assert.equal(config.lenient,true);
  assert.equal((await drawn(page)).error,'');
  assert.deepEqual(errors,[]);
});
