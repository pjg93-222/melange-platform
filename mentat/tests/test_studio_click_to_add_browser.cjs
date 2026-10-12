/* F2: a single click on a Data-pane field places it, through the same path a
   drop uses. One undo step, one query, and the pill says so before the click. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('a click places a field on the shelf a drop would have chosen, and Undo takes it back',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://click-to-add.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://click-to-add.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[]};
    const names={company:'Company',cycle:'Election cycle',recipient_category:'Recipient category',amount:'Selected amount',records:'Observation count'};
    const measure=id=>['amount','records'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:measure(id)?'measure':'dimension',
      type:measure(id)?'number':'category',is_time:id==='cycle',unit:id==='amount'?'cents':measure(id)?'count':undefined}));
    const observations=[
      {company:'Alpha Bank',cycle:'2023-2024',recipient_category:'Company PAC',amount:800000,records:2},
      {company:'Beta Bank',cycle:'2023-2024',recipient_category:'Candidate',amount:200000,records:1}
    ];
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),
      index_token:'index-1',query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,
        bases:[{id:'receipt',label:'receipt'}],provenance}],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        const rows=new Map();
        for(const row of observations){
          const key=body.dimensions.map(id=>row[id]).join('|');
          const seen=rows.get(key)||Object.assign({},...body.dimensions.map(id=>({[id]:row[id]})),{amount:0,records:0});
          seen.amount+=row.amount;seen.records+=row.records;rows.set(key,seen);
        }
        const grouped=[...rows.values()].sort((a,b)=>b.amount-a.amount);
        return respond({status:'ready',rows:grouped.slice(body.offset||0,(body.offset||0)+body.limit),
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)).filter(Boolean),
          totals:{amount:1000000,records:3,contributors:3},comparison:{receipt:1000000},group_count:grouped.length,
          offset:body.offset||0,limit:body.limit,has_more:false,
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

  // Every status line the studio writes, so an assertion never has to race one.
  await page.evaluate(()=>{
    const node=document.getElementById('studio-loading');
    window.statusLog=[node.textContent];
    new MutationObserver(()=>statusLog.push(node.textContent)).observe(node,{childList:true,characterData:true,subtree:true});
  });

  // The pill states the three gestures it answers to, before any of them.
  const hint='Click to add · drag to place · Enter for actions';
  const pill=name=>page.locator('#studio-dimensions .studio-pill, #studio-measures .studio-pill').filter({hasText:name});
  assert.ok((await pill('Recipient category').first().getAttribute('title')).startsWith(hint),'the tooltip leads with the gesture');
  assert.match(await pill('Recipient category').first().getAttribute('aria-label'),new RegExp(hint.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));

  await page.locator('#studio-clear').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-columns .studio-pill').length===0&&
    document.querySelectorAll('#studio-rows .studio-pill').length===0);
  const before=await page.evaluate(()=>calls.query.length);

  // An empty sheet: a grouping goes across the top, a measure down the side.
  await pill('Recipient category').first().click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-columns .studio-pill').length===1);
  assert.match(await page.locator('#studio-columns .studio-pill').innerText(),/Recipient category/);
  assert.equal(await page.locator('#studio-rows .studio-pill').count(),0);
  await pill('Selected amount').first().click();
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg')&&document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
  assert.equal(await page.locator('#studio-rows .studio-pill').count(),1);
  assert.match(await page.locator('#studio-rows .studio-pill').innerText(),/Selected amount/);
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).dimensions),['recipient_category']);
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).measures),['amount']);
  assert.ok(await page.evaluate(count=>calls.query.length>count,before),'placing a field asks the server for the new view');
  assert.ok(await page.evaluate(()=>statusLog.includes('Added Recipient category to Columns.')),'the studio says where the field went');
  assert.ok(await page.evaluate(()=>statusLog.includes('Added Selected amount to Rows.')));

  // Paul, 2026-09-23: "...nothing is logically constrained, it needs to be
  // utterly maluable." A click on a field already on a shelf adds it once more
  // (census B-S26) - the grouping lands on Rows beside the measure, the measure
  // on Columns - and the status line says so. Undo takes each copy away.
  await pill('Recipient category').first().click();
  await page.waitForFunction(()=>statusLog.includes('Added Recipient category to Rows again.'));
  assert.equal(await page.locator('#studio-columns .studio-pill').count(),1);
  assert.equal(await page.locator('#studio-rows .studio-pill').count(),2);
  await pill('Selected amount').first().click();
  await page.waitForFunction(()=>statusLog.includes('Added Selected amount to Columns again.'));
  assert.equal(await page.locator('#studio-columns .studio-pill').count(),2);
  await page.locator('#studio-undo').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-columns .studio-pill').length===1);
  await page.locator('#studio-undo').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-rows .studio-pill').length===1);

  // A shelf that is already taken sends the next field of the same kind to the other one.
  await pill('Company').first().click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-rows .studio-pill').length===2);
  assert.match(await page.locator('#studio-rows').innerText(),/Company/);
  assert.equal(await page.locator('#studio-columns .studio-pill').count(),1,'a new field goes to one shelf');

  // One click is one undo step.
  await page.locator('#studio-undo').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-rows .studio-pill').length===1);
  await page.locator('#studio-undo').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-rows .studio-pill').length===0);
  await page.locator('#studio-undo').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-columns .studio-pill').length===0);

  // The keyboard reaches the same shelf, and says which one before it is pressed.
  await pill('Recipient category').first().press('Enter');
  await page.getByRole('button',{name:'Add to Columns',exact:true}).waitFor();
  await page.getByRole('button',{name:'Add to Columns',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-columns .studio-pill').length===1);

  // Show Me reads the shelves: a placed grouping and measure enable the tiles a
  // bare field selection used to.
  await pill('Selected amount').first().click();
  await page.waitForFunction(()=>!document.querySelector('#studio-showme-grid button[data-tile="horizontal-bars"]').disabled);
  assert.deepEqual(errors,[]);
});
