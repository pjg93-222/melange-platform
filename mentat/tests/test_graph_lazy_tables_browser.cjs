'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const names=['categories','organizations','candidates','companies','levels'];
const counts=[8,5000,3000,2000,4];
const breakdown=(name,count)=>Array.from({length:count},(_,i)=>({id:`${name}-${i}`,label:`${name} label ${i}`,
  amount:i===count-1?-123.45:(count-i)+.25,...(name==='candidates'?{party:'Fixture'}:{})}));
const fixture={options:{companies:['Fixture Company'],cycles:['2024'],unknown_tiers:false,recipients:[]},
  filters:{company:'',cycle:'2024',tier:'',mode:'direct',direction:'Support'},
  totals:{receipt_groups:10012,giving:123456.78,direct:23456.78,organizations:100000,unresolved_candidate_links:0},
  candidate_available:true,candidate_note:'Synthetic candidates, no real donor records.',note:'Offline fixture only.',
  reports:[{name:'Fixture workbook.xlsx',sha256:'a'.repeat(64)}],candidate_details:[
    {candidate_id:'candidates-2999',company:'Fixture Company',tier:'Employees',recipient:'Committee fixture',cycle:'2024',amount:-123.45}],
  ...Object.fromEntries(names.map((name,i)=>[name,breakdown(name,counts[i])]))};

test('closed Graph tables are deferred without losing rows, callbacks or exports',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.setContent(html);
  for(const sheet of ['style.css','graph.css','workspace.css'])await page.addStyleTag({path:path.join(app,sheet)});
  // theme.js carries the measured text fitter graph.js draws its labels with,
  // so it is loaded here rather than stubbed. The stub below goes on owning the
  // colours, which is what this fixture needs to be deterministic about.
  await page.addScriptTag({path:path.join(app,'theme.js')});
  await page.evaluate(body=>{
    window.currentView='graph';window.splitView=false;window.selectedRun='fixture';
    document.querySelector('#view-loading').hidden=true;document.querySelector('#view-graph').hidden=false;
    // app.js shows Graph's action row (and its More menu) on the Graph view.
    document.getElementById('graph-actions').hidden=false;
    window.MentatTheme=Object.assign({},window.MentatTheme,{palette:['#13717d','#806bb1'],ink:'#263c48',muted:'#57666e',negative:'#be592f',grid:'#ddd',sans:'Arial'});
    window.fixtureBody=body;window.graphCalls=[];window.renderMeasurements=[];window.createdTableRows=0;
    const create=document.createElement.bind(document);
    document.createElement=(name,...args)=>{if(name.toLowerCase()==='tr')window.createdTableRows++;return create(name,...args);};
    window.api=async url=>url.startsWith('/api/report-sources')?{runs:[{id:'fixture',name:'Offline fixture',state:'completed'}]}:{industries:[{name:'Synthetic'}]};
    window.fetch=async url=>{
      window.graphCalls.push(url);
      return {ok:true,json:async()=>{window.createdTableRows=0;window.renderStarted=performance.now();return body;}};
    };
    window.addEventListener('mentat:graph',event=>{if(event.detail){
      const panels=document.querySelector('.graph-panels');panels.getBoundingClientRect();
      window.renderMeasurements.push({milliseconds:performance.now()-window.renderStarted,
        tableRows:panels.querySelectorAll('details tbody tr').length,createdTableRows:window.createdTableRows,
        descendants:panels.querySelectorAll('*').length,marks:panels.querySelectorAll('.graph-bar-button').length});
    }});
    const createURL=URL.createObjectURL.bind(URL);
    URL.createObjectURL=blob=>{window.lastExportBlob=blob;return createURL(blob);};
    window.MentatExport={image:async(svg,format,title,caption)=>{window.lastImage={marks:svg.querySelectorAll('.graph-bar-button').length,format,title,caption};}};
  },fixture);
  await page.addScriptTag({path:process.env.MENTAT_GRAPH_SCRIPT||path.join(app,'graph.js')});
  await page.waitForFunction(()=>renderMeasurements.length===1);
  // Repeat the same in-memory selection to separate startup from rendering.
  await page.evaluate(()=>{for(let i=0;i<2;i++){
    createdTableRows=0;const start=performance.now();
    for(const name of ['categories','organizations','candidates','companies','levels'])
      document.getElementById('graph-top-'+name).onchange();
    const panels=document.querySelector('.graph-panels');panels.getBoundingClientRect();
    renderMeasurements.push({milliseconds:performance.now()-start,tableRows:panels.querySelectorAll('details tbody tr').length,
      createdTableRows,descendants:panels.querySelectorAll('*').length,marks:panels.querySelectorAll('.graph-bar-button').length});
  }});
  const initial=await page.evaluate(()=>renderMeasurements.slice());
  console.log(JSON.stringify({fixtureRows:counts.reduce((a,b)=>a+b),render:initial}));
  if(process.env.MENTAT_GRAPH_BENCHMARK_OUTPUT)fs.writeFileSync(process.env.MENTAT_GRAPH_BENCHMARK_OUTPUT,JSON.stringify({fixtureRows:counts.reduce((a,b)=>a+b),render:initial},null,2)+'\n');
  if(process.env.MENTAT_GRAPH_BENCH_ONLY)return;
  for(const sample of initial){assert.equal(sample.tableRows,0);assert.equal(sample.createdTableRows,0);assert.equal(sample.marks,42);}
  assert.equal(await page.locator(names.map(name=>`#graph-${name}>details`).join(',')).count(),5);

  // Existing Full table button opens and materializes all 5,000 exact values.
  // The view's export controls live in one Export menu now (UI clean-up item 7),
  // and a click anywhere outside it closes it, so each trip opens it if it is shut.
  // It sits in the More menu (r4 audit AUDIT-MEN-A-1), which Full table closes.
  const openExportMenu=async()=>{
    if(!(await page.locator('#graph-more').evaluate(el=>el.open)))await page.locator('#graph-more > summary').click();
    if(!(await page.locator('#graph-export-menu').evaluate(el=>el.open)))await page.locator('#graph-export-menu > summary').click();
  };
  await openExportMenu();
  await page.locator('#graph-export-target').selectOption('organizations');
  await page.locator('#graph-full-table').click();
  await page.waitForFunction(()=>document.querySelectorAll('#graph-organizations tbody tr').length===5000);
  const full=await page.locator('#graph-organizations tbody tr').evaluateAll(rows=>rows.map(row=>[...row.cells].map(cell=>cell.textContent)));
  assert.deepEqual(full,fixture.organizations.map(row=>[row.label,row.amount.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2})]));
  assert.equal(await page.locator('.graph-panels details:not([open]) tbody tr').count(),0);
  const rowsBefore=await page.evaluate(()=>{window.savedTable=document.querySelector('#graph-organizations table');return createdTableRows;});
  await page.locator('#graph-organizations summary').click();
  await page.locator('#graph-organizations summary').click();
  assert.equal(await page.evaluate(()=>savedTable===document.querySelector('#graph-organizations table')),true);
  assert.equal(await page.evaluate(()=>createdTableRows),rowsBefore,'reopening reuses the materialized table');
  for(const name of names.filter(name=>name!=='organizations')){
    await openExportMenu();await page.locator('#graph-export-target').selectOption(name);await page.locator('#graph-full-table').click();
    await page.waitForFunction(({name,count})=>document.querySelectorAll(`#graph-${name}>details tbody tr`).length===count,{name,count:fixture[name].length});
    const values=await page.locator(`#graph-${name}>details tbody tr`).evaluateAll(rows=>rows.map(row=>[...row.cells].map(cell=>cell.textContent)));
    assert.deepEqual(values,fixture[name].map(row=>[row.label,row.amount.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2})]));
  }
  await openExportMenu();await page.locator('#graph-export-target').selectOption('organizations');await page.locator('#graph-full-table').click();

  // CSV always comes from every source row, independently of table visibility.
  await openExportMenu();await page.locator('#graph-export-format').selectOption('csv');
  await page.locator('#graph-export').click();
  const csv=await page.evaluate(()=>lastExportBlob.text());
  assert.ok(csv.includes('"organizations label 4999","-123.45","organizations-4999"'));
  assert.equal((csv.match(/"organizations label /g)||[]).length,5000);
  assert.ok(csv.includes('a'.repeat(64)));
  await openExportMenu();await page.locator('#graph-export-format').selectOption('svg');await page.locator('#graph-export').click();
  const image=await page.evaluate(()=>lastImage);assert.equal(image.marks,10);assert.match(image.caption,/Preview: 10 of 5000 groups/);

  // The last row's original callback still filters the correct recipient.
  await page.locator('#graph-organizations tbody tr').last().locator('button').click();
  // Only /api/graph reads count: the company sparklines now fetch their own
  // contribution session and catalog through the same fetch. And since the
  // downstream drawer (2026-09-19) a PAC click narrows the page AND opens the
  // drawer, whose own /api/graph reads follow the page's in the same tick, so
  // the count passes 2 between two animation frames. The page's read is the
  // second one; every drawer read names the same recipient too.
  const graphReads=()=>page.evaluate(()=>graphCalls.filter(url=>String(url).startsWith('/api/graph?')));
  await page.waitForFunction(()=>graphCalls.filter(url=>String(url).startsWith('/api/graph?')).length>=2);
  assert.match((await graphReads())[1],/recipient=organizations-4999/);
  assert.match((await graphReads()).at(-1),/recipient=organizations-4999/);
  await page.waitForFunction(()=>document.querySelectorAll('.graph-panels details tbody tr').length===0);
  await page.locator('#graph-tab-candidates').click();await page.locator('#graph-view-candidates').click();
  await page.waitForFunction(()=>document.querySelectorAll('#graph-candidates tbody tr').length===3000);
  await page.locator('#graph-candidates tbody tr').last().locator('button').click();
  assert.equal(await page.locator('#graph-candidate-rows tr').count(),1);
  assert.match(await page.locator('#graph-candidate-rows').textContent(),/-\$123\.45/);
  // Explicit Show all rows continues to expand the actual SVG preview.
  await page.locator('#graph-top-candidates').selectOption('50');
  assert.equal(await page.locator('#graph-candidates .graph-bar-button').count(),50,'Top N caps the preview at 50 rows');
  assert.equal(await page.locator(names.map(name=>`#graph-${name}>details tbody tr`).join(',')).count(),0);
  assert.deepEqual(errors,[]);
});
