/* F3: the data source is always findable. The panel arrives open and stays
   open, states on one chip line what it is pointed at, keeps a permanent way
   back, states why a report cannot be read, and shows a long prepare with its
   elapsed time and a way out. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

const BLOCKED='The Telecom report changed during source discovery. Refresh sources.';
const PREPARING='Indexing published receipt identities in bounded storage.';

test('the data source panel opens, stays open, names what it cannot read, and shows a prepare it can cancel',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://data-source.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://data-source.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css'])await page.addStyleTag({path:path.join(app,file)});

  // The picker is the first thing in the pane, before any script has run.
  assert.equal(await page.evaluate(()=>document.getElementById('studio-source-details').open),true,'the panel arrives open');
  // Build 03 (S1): the source chip is the way in; it names the popover it opens.
  assert.equal(await page.locator('#studio-scope-change').getAttribute('aria-controls'),'studio-source-popover');
  assert.equal(await page.locator('#studio-scope-change #studio-scope-chip').count(),1);

  await page.evaluate(([blocked,preparing])=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[]};window.prepareTimes=2;window.preparingForever=false;
    const names={company:'Company',cycle:'Election cycle',amount:'Selected amount',records:'Observation count'};
    const measure=id=>['amount','records'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:measure(id)?'measure':'dimension',
      type:measure(id)?'number':'category',is_time:id==='cycle',unit:id==='amount'?'cents':measure(id)?'count':undefined}));
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),
      index_token:'index-1',query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    // A stub that honours its abort signal, so Cancel exercises the real path.
    const guard=(signal,value)=>new Promise((resolve,reject)=>{
      if(signal?.aborted)return reject(new DOMException('Aborted','AbortError'));
      const timer=setTimeout(()=>resolve(respond(value)),5);
      signal?.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));});
    });
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[
        {id:'Banking',label:'Banking',available:true,fields,bases:[{id:'receipt',label:'receipt'}],provenance},
        {id:'Telecom',label:'Telecom',available:false,message:blocked,fields,bases:[{id:'receipt',label:'receipt'}],provenance}
      ],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        if(window.preparingForever||prepareTimes>0){
          if(!window.preparingForever)prepareTimes--;
          return guard(options?.signal,{status:'preparing',message:preparing,retry_after_ms:50});
        }
        const rows=[{company:'Alpha Bank',amount:800000,records:2},{company:'Beta Bank',amount:200000,records:1}];
        const grouped=body.dimensions.includes('company')?rows:[{amount:1000000,records:3}];
        return guard(options?.signal,{status:'ready',rows:grouped,
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
  },[BLOCKED,PREPARING]);
  for(const file of ['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'])await page.addScriptTag({path:path.join(app,file)});

  // A prepare states the server's own message and how long it has taken.
  await page.locator('#studio-progress').waitFor({state:'visible'});
  assert.match(await page.locator('#studio-progress-message').textContent(),/Preparing verified data · Indexing published receipt identities/);
  assert.match(await page.locator('#studio-progress-elapsed').textContent(),/^\d+:\d{2} elapsed$/);
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg'));
  await page.locator('#studio-progress').waitFor({state:'hidden'});
  assert.ok(await page.evaluate(()=>calls.query.length>=3),'the preparing replies were polled, then the view loaded');

  // Still open once the fields arrived (UI clean-up item 16), with the state it
  // used to write on the fold now read off the summary's own chip line.
  assert.equal(await page.evaluate(()=>document.getElementById('studio-source-details').open),true);
  assert.deepEqual(await page.locator('#studio-source-summary span').allTextContents(),
    ['Banking','Published included receipts','✓ Ready']);

  // A report that cannot be read says why, under the select and on its option.
  assert.equal(await page.evaluate(()=>document.getElementById('studio-dataset-message').hidden),false);
  assert.equal(await page.locator('#studio-dataset-message').textContent(),'Telecom: '+BLOCKED);
  const telecom=await page.evaluate(()=>{
    const option=[...document.getElementById('studio-dataset').options].find(o=>o.value==='Telecom');
    return {title:option.title,disabled:option.disabled,text:option.textContent};
  });
  assert.equal(telecom.title,BLOCKED);
  assert.equal(telecom.disabled,true);
  assert.match(telecom.text,/ — unavailable$/);

  // The way back is permanent and leads to the panel, where the reason is read.
  assert.equal(await page.locator('#studio-scope-change').isVisible(),true);
  await page.locator('#studio-scope-change').click();
  assert.equal(await page.evaluate(()=>document.getElementById('studio-source-details').open),true);
  await page.waitForFunction(()=>document.querySelectorAll('#studio-source-summary span').length>=2);
  assert.deepEqual(await page.locator('#studio-source-summary span').allTextContents(),
    ['Banking','Published included receipts','✓ Ready']);
  assert.equal(await page.locator('#studio-dataset').isVisible(),true);
  assert.equal(await page.locator('#studio-dataset-message').isVisible(),true);

  // A refresh is not the console's chance to fold the panel away behind the reader.
  await page.locator('#studio-refresh-data').click();
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg')&&document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
  assert.equal(await page.evaluate(()=>document.getElementById('studio-source-details').open),true,'a reopened panel stays open');

  // Cancelling a prepare keeps the view that was already on the canvas.
  const drawn=await page.locator('#studio-canvas svg').count();
  assert.equal(drawn,1);
  await page.evaluate(()=>{window.preparingForever=true;});
  // Build 03 (S1): the source popover is put away before the reader reaches
  // for a field (it can lie over the Data pane).
  if(await page.locator('#studio-source-popover').isVisible())await page.locator('#studio-scope-change').click();
  await page.locator('#studio-measures .studio-pill').filter({hasText:'Observation count'}).first().click();
  await page.locator('#studio-progress').waitFor({state:'visible'});
  await page.locator('#studio-progress-cancel').click();
  await page.locator('#studio-progress').waitFor({state:'hidden'});
  assert.equal(await page.locator('#studio-canvas svg').count(),1,'the last good view is still on the canvas');
  assert.match(await page.locator('#studio-loading').textContent(),/Cancelled\. Showing the last loaded view\./);
  assert.deepEqual(errors,[]);
});
