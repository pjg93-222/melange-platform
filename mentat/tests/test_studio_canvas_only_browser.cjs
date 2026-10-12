/* F6: Canvas only puts the builder away but keeps the way to take the chart
   with you, and the Inspector toggle says what it is showing and how many
   filters are shaping the view behind it. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('Canvas only keeps Export, and the Inspector toggle counts the filters behind it',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://canvas-only.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://canvas-only.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css','workspace-polish.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const names={company:'Company',cycle:'Election cycle',recipient_category:'Recipient category',
      amount:'Selected amount',records:'Observation count'};
    const measure=id=>['amount','records'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:measure(id)?'measure':'dimension',
      type:measure(id)?'number':'category',is_time:id==='cycle',unit:id==='amount'?'cents':measure(id)?'count':undefined}));
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),
      index_token:'index-1',query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,
        bases:[{id:'receipt',label:'receipt'}],provenance}],fields});
      if(url.endsWith('/query')){
        const rows=[{company:'Alpha Bank',amount:800000,records:2},{company:'Beta Bank',amount:200000,records:1}];
        const grouped=body.dimensions.includes('company')?rows:[{amount:1000000,records:3}];
        return respond({status:'ready',rows:grouped,
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)).filter(Boolean),
          totals:{amount:1000000,records:3,contributors:3},comparison:{receipt:1000000},group_count:grouped.length,
          offset:0,limit:body.limit,has_more:false,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:false,conserved:true},
          provenance,notices:[],verified_unique:false});
      }
      throw Error('Unexpected endpoint '+url);
    };
    window.api=async url=>{throw Error('Unexpected api call '+url);};
    window.MentatExport={download:()=>{},image:async()=>{}};
  });
  for(const file of ['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'])await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg'));

  // A wide window arrives with the Inspector open, and the toggle says so.
  assert.equal(await page.locator('#studio-inspector-toggle').getAttribute('aria-expanded'),'true');
  assert.equal(await page.evaluate(()=>document.getElementById('studio-inspector-count').hidden),true,'no filters, no badge');

  // Two filters on the sheet, two on the badge.
  await page.locator('#studio-tab-filters').click();
  for(const id of ['recipient_category','cycle']){
    const column=await page.evaluate(field=>[...document.getElementById('studio-filter-add').options]
      .findIndex(o=>o.textContent===field),id==='cycle'?'Election cycle':'Recipient category');
    assert.ok(column>0,'the field is offered as a filter');
    await page.locator('#studio-filter-add').selectOption({index:column});
  }
  await page.waitForFunction(()=>document.getElementById('studio-inspector-count').textContent==='2');
  assert.equal(await page.evaluate(()=>document.getElementById('studio-inspector-count').hidden),false);
  assert.equal(await page.locator('#studio-inspector-toggle').getAttribute('aria-label'),'Inspector · 2 active filters');
  assert.equal(await page.locator('#studio-tab-filters').textContent(),'Filters (2)');

  // Canvas only puts the builder away and keeps the export menu.
  await page.locator('#studio-view-canvas').click();
  await page.waitForFunction(()=>document.querySelector('#view-chart-studio .studio').classList.contains('studio-options-hidden'));
  assert.equal(await page.locator('#studio-download').isVisible(),true,'Canvas only keeps the way to take the chart away');
  for(const id of ['studio-undo','studio-redo','studio-swap','studio-clear','studio-duplicate','studio-showme-toggle','studio-classic','studio-sort-asc','studio-labels','studio-fit'])
    assert.equal(await page.locator('#'+id).isVisible(),false,id+' belongs to the builder');
  assert.equal(await page.locator('#studio-shelves').isVisible(),false);
  assert.equal(await page.locator('#studio-pane-data').isVisible(),false);
  assert.equal(await page.locator('#studio-inspector').isVisible(),false);
  assert.equal(await page.locator('#studio-view-builder').isVisible(),true,'the way back never hides');
  assert.equal(await page.locator('#studio-canvas svg').count(),1,'the chart is still drawn');
  await page.locator('#studio-download summary').click();
  assert.equal(await page.locator('#studio-download-png').isVisible(),true);
  await page.locator('#studio-download summary').click();

  // Back to the builder, and the toolbar returns with it.
  await page.locator('#studio-view-builder').click();
  await page.waitForFunction(()=>!document.querySelector('#view-chart-studio .studio').classList.contains('studio-options-hidden'));
  assert.equal(await page.locator('#studio-undo').isVisible(),true);

  // Below 1100px the Inspector becomes a drawer, so it closes rather than sit on
  // top of the toggle that controls it, and the toggle states what it is doing.
  await page.setViewportSize({width:1099,height:900});
  await page.waitForFunction(()=>document.getElementById('studio-inspector').hidden);
  assert.equal(await page.locator('#studio-inspector-toggle').isVisible(),true);
  assert.equal(await page.locator('#studio-inspector-count').isVisible(),true,'the count is readable without opening it');
  assert.equal(await page.locator('#studio-inspector-toggle').getAttribute('aria-expanded'),'false');
  assert.equal(await page.evaluate(()=>getComputedStyle(document.getElementById('studio-inspector-toggle')).borderTopColor),
    'rgb(236, 169, 28)','a closed Inspector with filters behind it is marked in gold');
  await page.locator('#studio-inspector-toggle').click();
  await page.waitForFunction(()=>!document.getElementById('studio-inspector').hidden);
  assert.equal(await page.locator('#studio-inspector-toggle').getAttribute('aria-expanded'),'true');
  // The drawer covers the toggle, so Close is the way back out of it.
  await page.locator('#studio-inspector-close').click();
  await page.waitForFunction(()=>document.getElementById('studio-inspector').hidden);
  assert.equal(await page.locator('#studio-inspector-toggle').getAttribute('aria-expanded'),'false');
  // Back to a wide window and the Inspector is a column again.
  await page.setViewportSize({width:1440,height:1000});
  await page.waitForFunction(()=>!document.getElementById('studio-inspector').hidden);
  assert.equal(await page.locator('#studio-inspector-toggle').getAttribute('aria-expanded'),'true');
  assert.deepEqual(errors,[]);
});
