/* Chart Studio r4 open items, in Paul's words (2026-09-23):
     "Additionally, within the Chart Studio make sure everything is freely flexible, that all graph types
      properly register and nothing is logically constrained, it needs to be utterly maluable"
   1. Map and Filled map on a REPORT WORKSHEET draw real maps whenever the sheet has a ZIP, state, district or
      city/state field (census C-16), with a coverage line in the sheet's own measure; a sheet with none keeps
      the bars-with-a-note fallback.
   2. Labels on a Text table is a real toggle: off hides the printed values (hover and CSV keep them).
   3. Verified None is enabled where it used to send Descending's query: it lists the groups in their own order.
   Offline fixture: the golden index.html with its scripts stripped, the app's own scripts added in the console's
   order, a verified server that answers any grouping, two report worksheets and fixture Census geometry. */
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

async function boot(context,{worksheet=false}={}){
  const page=await context.newPage();
  page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://studio-worksheet-map.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://studio-worksheet-map.fixture/'+(worksheet?'?worksheet':'')+'#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css','workspace-polish.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[],geography:[],session:0};
    const members={company:['Gamma','alpha','Beta10','Beta2'],cycle:['2021-2022','2023-2024']};
    const names={company:'Company',cycle:'Election cycle',amount:'Selected amount',records:'Observation count'};
    const isMeasure=id=>['amount','records'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:isMeasure(id)?'measure':'dimension',
      type:isMeasure(id)?'number':'category',unit:id==='amount'?'cents':isMeasure(id)?'count':undefined}));
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),index_token:'index-1',
      query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    const box=(w,s,e,n)=>({type:'Polygon',coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session')){calls.session++;return respond({token:'fixture-token',read_only:true});}
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,
        bases:[{id:'receipt',label:'receipt'}],provenance}],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        let tuples=[{}];
        for(const id of body.dimensions){const list=members[id]||['A','B'];tuples=tuples.flatMap(t=>list.map(v=>({...t,[id]:v})));}
        // Deterministic sizes: Gamma largest, then alpha, Beta10, Beta2.
        const rows=tuples.map((t,seed)=>Object.assign({},t,...body.measures.map(id=>({[id]:id==='amount'?100000*(4-seed):seed+2}))));
        const order=(body.sort||[])[0];
        if(order)rows.sort((a,b)=>{const x=a[order.field],y=b[order.field];const c=typeof x==='number'?x-y:String(x).localeCompare(String(y));return order.direction==='asc'?c:-c;});
        return respond({status:'ready',rows:rows.slice(body.offset||0,(body.offset||0)+body.limit),
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)),
          totals:{amount:1000000,records:14,contributors:6},comparison:{receipt:1000000},group_count:rows.length,
          offset:body.offset||0,limit:body.limit,has_more:false,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:false,conserved:true},provenance,notices:[],verified_unique:false});
      }
      if(url.endsWith('/geography')){
        calls.geography.push(body);
        return respond({congress:body.congress,warnings:[],coverage:{unmatched_zips:[]},sources:[{vintage:'Fixture Census',sha256:'g'.repeat(64)}],
          points:[{zip5:'55401',longitude:-93.27,latitude:44.98},{zip5:'99501',longitude:-149.86,latitude:61.22}].filter(p=>body.zips.includes(p.zip5)),
          layers:{
            states:{type:'FeatureCollection',features:[
              {properties:{STUSAB:'MN',NAME:'Minnesota',GEOID:'27',INTPTLON:-94.3,INTPTLAT:46.3},geometry:box(-97,43.5,-89.5,49)},
              {properties:{STUSAB:'AK',NAME:'Alaska',GEOID:'02',INTPTLON:-150,INTPTLAT:63},geometry:box(-168,54,-141,71)},
              {properties:{STUSAB:'NY',NAME:'New York',GEOID:'36',INTPTLON:-75.5,INTPTLAT:42.9},geometry:box(-79.7,40.5,-71.9,45)}]},
            districts:{type:'FeatureCollection',features:body.layers.includes('districts')?[
              {properties:{GEOID:'2705',NAME:'District 5',STATE:'27',CDSESSN:'119',INTPTLON:-93.3,INTPTLAT:45.0},geometry:box(-93.5,44.8,-93.0,45.2)},
              {properties:{GEOID:'0200',NAME:'At large',STATE:'02',CDSESSN:'119',INTPTLON:-150,INTPTLAT:63},geometry:box(-168,54,-141,71)}]:[]}}});
      }
      if(url.endsWith('/records'))return respond({status:'ready',rows:[],total_records:0,has_more:false,provenance});
      throw Error('Unexpected endpoint '+url);
    };
    // Shaped like the published Beneficiary Summary; the second sheet has no place field.
    const places={columns:[{index:0,name:'Cycle',type:'category'},{index:1,name:'Candidate / Officeholder',type:'category'},
      {index:2,name:'Office',type:'category'},{index:3,name:'State',type:'category'},{index:4,name:'District',type:'category'},
      {index:5,name:'Downstream Activity',type:'number',unit:'currency'},{index:6,name:'ZIPs',type:'category'}],
      rows:[['2025-2026','A','House','MN','5',100,'55401; 554011234'],['2025-2026','B','House','MN','5',300,'55401'],
        ['2025-2026','C','Senate','PA','0',50,'10019; 02134'],['2025-2026','D','House','AK','0',20,'99501'],
        ['2025-2026','E','House','New York','12',30,'W25JR']],
      row_numbers:[2,3,4,5,6],row_count:5,header_row:1,sheet:'Beneficiary Summary',sha256:'places-fixture',note:'Worksheet fixture'};
    const plain={columns:[{index:0,name:'Company',type:'category'},{index:1,name:'Total Giving',type:'number'}],
      rows:[['Alpha',10],['Beta',20]],row_numbers:[2,3],row_count:2,header_row:1,sheet:'Company List',sha256:'plain-fixture',note:'Worksheet fixture'};
    window.api=async url=>url==='/api/report-sources'?{runs:[{id:'published-reports',name:'Fixture reports'}]}
      :url.startsWith('/api/reports?')?{reports:[{index:0,name:'Fixture.xlsx'}]}
      :url.includes('sheet=Company')?plain:url.includes('sheet=')?places:{sheets:['Beneficiary Summary','Company List']};
    window.MentatExport={download:()=>{},image:async()=>{}};
  });
  for(const file of SCRIPTS)await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(worksheet?()=>document.querySelector('#studio-loading').textContent.includes('5 rows'):()=>document.querySelector('#studio-canvas svg'));
  return {page,errors};
}
const launch=async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  return browser.newContext({viewport:{width:1440,height:900}});
};
// ZIPs and District are identifier-like names, so the Data pane lists them under More fields; open it first.
const pill=(page,name)=>({click:async()=>{
  await page.evaluate(()=>document.querySelectorAll('#studio-dimensions details.studio-more-fields, #studio-measures details.studio-more-fields').forEach(d=>{d.open=true;}));
  await page.locator('#studio-dimensions .studio-pill, #studio-measures .studio-pill').filter({hasText:name}).first().click();}});
const note=page=>page.locator('#studio-note').textContent();

test('a report worksheet with a place field draws real maps, in its own measure',{timeout:180000},async t=>{
  const {page,errors}=await boot(await launch(t),{worksheet:true});
  await page.locator('#studio-clear').click();
  await pill(page,'State').click();
  await pill(page,'Downstream Activity').click();
  await page.locator('#studio-mark').selectOption('map');
  await page.waitForFunction(()=>document.querySelector('#studio-canvas .contribution-map'));
  // One Census request for the state layer; the token came from the session, as on the verified path.
  const geography=await page.evaluate(()=>calls.geography.at(-1));
  assert.ok(geography.layers.includes('states'),JSON.stringify(geography));
  assert.deepEqual(geography.zips,[]);
  // MN 400, AK 20 and New York 30 are located; PA 50 has no fixture shape, so it is unmapped.
  const marks=await page.evaluate(()=>[...document.querySelectorAll('#studio-canvas circle[role="button"]')].map(c=>c.getAttribute('aria-label')));
  assert.equal(marks.length,3,marks.join(' | '));
  assert.ok(marks.some(m=>/^MN: .*\$400\.00$/.test(m)),marks.join(' | '));
  const coverage=await page.locator('#studio-canvas .contribution-map-coverage').textContent();
  assert.match(coverage,/\$500\.00 · Located marks \$450\.00 · Unmapped \$50\.00\. 3 locations; no top-N cutoff\.$/,coverage);
  assert.doesNotMatch(coverage,/Selected amount/,'a worksheet has no selected amount');
  assert.doesNotMatch(await note(page),/drawn as bars/);

  // Filled map on a district: Census district shapes are filled; district 0 of a one-district state has one.
  await page.locator('#studio-clear').click();
  await pill(page,'District').click();
  await pill(page,'Downstream Activity').click();
  await page.locator('#studio-mark').selectOption('filled-map');
  await page.waitForFunction(()=>document.querySelector('#studio-canvas .contribution-map')&&document.querySelectorAll('#studio-canvas path[role="button"]').length);
  assert.ok((await page.evaluate(()=>calls.geography.at(-1).layers)).includes('districts'));
  const filled=await page.evaluate(()=>[...document.querySelectorAll('#studio-canvas path[role="button"]')].map(p=>p.getAttribute('aria-label')));
  assert.deepEqual(filled.sort(),['AK-00: Downstream Activity (currency) · Sum $20.00','MN-05: Downstream Activity (currency) · Sum $400.00'].sort());
  assert.match(await page.locator('#studio-canvas .contribution-map').textContent(),/District 0 is a statewide or at-large seat/);

  // A ZIP symbol map: ZIP+4 is read as its ZIP; a cell naming two ZIPs is counted, not guessed.
  await page.locator('#studio-clear').click();
  await pill(page,'ZIPs').click();
  await pill(page,'Downstream Activity').click();
  await page.locator('#studio-mark').selectOption('map');
  await page.waitForFunction(()=>calls.geography.at(-1).zips.length);
  await page.waitForFunction(()=>document.querySelectorAll('#studio-canvas circle[role="button"]').length===2);
  assert.deepEqual(await page.evaluate(()=>calls.geography.at(-1).zips),['55401','99501']);
  assert.match(await page.locator('#studio-canvas .contribution-map').textContent(),/1 group names several different places in one cell \(\$50\.00\)/);

  // A worksheet with no place field keeps the honest bars and says why.
  // The sheet picker lives in the folded source popover; choose the sheet the way its change handler reads it.
  await page.evaluate(()=>{const select=document.getElementById('studio-sheet');select.value='Company List';select.dispatchEvent(new Event('change'));});
  await page.waitForFunction(()=>document.querySelector('#studio-loading').textContent.includes('2 rows'));
  await page.locator('#studio-clear').click();
  await pill(page,'Company').click();
  await pill(page,'Total Giving').click();
  await page.locator('#studio-mark').selectOption('filled-map');
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg')&&!document.querySelector('#studio-canvas .contribution-map'));
  assert.match(await note(page),/Maps need a ZIP, state, congressional district or city field and this worksheet has none, so it is drawn as bars by Company\./);
  assert.deepEqual(errors,[]);
});

test('Labels on a Text table shows and hides the printed values, on both sources',{timeout:180000},async t=>{
  const context=await launch(t);
  const {page,errors}=await boot(context,{worksheet:true});
  await page.locator('#studio-clear').click();
  await pill(page,'State').click();
  await pill(page,'Downstream Activity').click();
  await page.locator('#studio-mark').selectOption('text');
  await page.waitForFunction(()=>document.querySelector('#studio-table:not([hidden]) table'));
  const button=page.locator('#studio-labels');
  assert.equal(await button.isDisabled(),false,'Labels is enabled on a table (Paul: "nothing is logically constrained")');
  assert.equal(await button.getAttribute('aria-pressed'),'true');
  assert.match(await button.getAttribute('title'),/^Print each value in its cell/);
  assert.equal(await page.locator('#studio-table td[data-value-hidden]').count(),0);
  await button.click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-table td[data-value-hidden]').length);
  assert.equal(await button.getAttribute('aria-pressed'),'false');
  const hidden=await page.evaluate(()=>[...document.querySelectorAll('#studio-table td[data-value-hidden]')].map(td=>[td.textContent,td.title]));
  assert.ok(hidden.every(([text,title])=>text===''&&title!==''),'a hidden value keeps its hover: '+JSON.stringify(hidden));
  assert.match(await note(page),/Labels are off, so the table lists its groups without their values/);
  await button.click();
  await page.waitForFunction(()=>!document.querySelectorAll('#studio-table td[data-value-hidden]').length);
  assert.deepEqual(errors,[]);

  const verified=await boot(await launch(t));
  await verified.page.locator('#studio-mark').selectOption('text');
  await verified.page.waitForFunction(()=>document.querySelector('#studio-canvas table'));
  assert.equal(await verified.page.locator('#studio-labels').isDisabled(),false);
  await verified.page.locator('#studio-labels').click();
  await verified.page.waitForFunction(()=>document.querySelectorAll('#studio-canvas td[data-value-hidden]').length);
  assert.match(await note(verified.page),/Labels are off/);
  assert.deepEqual(verified.errors,[]);
});

test('verified None is enabled and lists the groups in their own order on the same query',{timeout:180000},async t=>{
  const {page,errors}=await boot(await launch(t));
  const bars=()=>page.evaluate(()=>[...document.querySelectorAll('#studio-canvas svg text[data-label="category"]')].map(n=>n.firstChild.nodeValue));
  // A new sheet opens largest first, with Descending pressed, as before.
  assert.equal(await page.locator('#studio-sort-desc').getAttribute('aria-pressed'),'true');
  assert.deepEqual(await bars(),['Gamma','alpha','Beta10','Beta2']);
  const queries=await page.evaluate(()=>calls.query.length);
  await page.locator('#studio-sort > summary').click();
  assert.equal(await page.locator('#studio-sort-none').isDisabled(),false,'None is never disabled');
  assert.match(await page.locator('#studio-sort-none').getAttribute('title'),/^No sort: each group in its own order/);
  await page.locator('#studio-sort-none').click();
  await page.waitForFunction(()=>document.querySelector('#studio-sort-none').getAttribute('aria-pressed')==='true');
  assert.deepEqual(await bars(),['alpha','Beta2','Beta10','Gamma'],'A to Z with numbers ascending');
  assert.equal(await page.locator('#studio-sort-desc').getAttribute('aria-pressed'),'false');
  assert.equal(await page.evaluate(()=>calls.query.length),queries,'the same query: nothing is asked of the server again');
  assert.equal(await page.locator('#studio-undo').isDisabled(),false,'a real, undoable edit');
  if(!(await page.locator('#studio-sort-desc').isVisible()))await page.locator('#studio-sort > summary').click();
  await page.locator('#studio-sort-desc').click();
  await page.waitForFunction(()=>document.querySelector('#studio-sort-desc').getAttribute('aria-pressed')==='true');
  assert.deepEqual(await bars(),['Gamma','alpha','Beta10','Beta2']);
  assert.deepEqual(errors,[]);
});
