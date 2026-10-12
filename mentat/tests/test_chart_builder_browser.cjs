/* Offline browser regression checks: no server, collection or workbook writes. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('chart and comparison controls preserve source state, missing values and current requests',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent(fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
  await page.evaluate(()=>{
    window.currentView='graph';window.selectedRun='fixture';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.fixtureData={columns:[{index:0,name:'Region',type:'category',distinct:2,missing:0},{index:1,name:'Amount',type:'number',distinct:3,missing:0}],rows:[['A',10],['A',20],['B',80]],row_numbers:[2,3,4],row_count:3,header_row:1,sheet:'Totals',sha256:'fixture',note:'Offline fixture'};
    window.api=async url=>url==='/api/report-sources'?{runs:[{id:'fixture',name:'Fixture',state:'completed'}]}:url.startsWith('/api/reports?')?{reports:[{index:0,name:'Fixture.xlsx'}]}:url.includes('sheet=')?window.fixtureData:{sheets:['Totals']};
    window.sendComparison=()=>window.dispatchEvent(new CustomEvent('mentat:chart-data',{detail:{...window.fixtureData,sheet:'Comparison',columns:[...window.fixtureData.columns,{index:2,name:'Second amount',type:'number',distinct:3,missing:0}],rows:[['A',10,5],['A',20,10],['B',80,40]]}}));
  });
  for(const script of ['theme.js','chart-math.js','grouped-chart.js','charts.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.evaluate(()=>window.sendComparison());
  assert.equal(await page.locator('#chart-image svg').count(),1);
  for(const id of ['run','report','sheet','header','rescan'])assert.equal(await page.locator('#chart-'+id).isDisabled(),true,'Synthetic data must disable '+id);
  assert.equal(await page.locator('#chart-source-report').isDisabled(),false);

  await page.locator('#chart-source-report').evaluate(button=>button.click());
  await page.waitForFunction(()=>document.getElementById('chart-loading').textContent.includes('3 rows · 2 columns'));
  for(const id of ['run','report','sheet','header','rescan'])assert.equal(await page.locator('#chart-'+id).isDisabled(),false,'Workbook mode must enable '+id);
  await page.locator('#chart-suggestion').evaluate(select=>{select.value=[...select.options].find(option=>option.textContent==='Share of rows · pie chart').value;select.dispatchEvent(new Event('change',{bubbles:true}));});
  assert.equal(await page.locator('#chart-y').inputValue(),'-1');
  await page.locator('[data-chart="bar"]').evaluate(button=>button.click());
  assert.equal(await page.locator('#chart-y').inputValue(),'-1');
  assert.equal(await page.locator('#chart-aggregation').inputValue(),'count');
  assert.match(await page.locator('#chart-title').textContent(),/Row count/);

  await page.evaluate(()=>{
    window.api=()=>new Promise((resolve,reject)=>{window.rejectOldReportRequest=reject;});
    document.getElementById('chart-source-report').click();
    window.sendComparison();
    window.rejectOldReportRequest(new Error('Obsolete report request failed'));
  });
  await page.waitForFunction(()=>document.querySelector('#chart-image svg'));
  assert.equal(await page.locator('#chart-error').textContent(),'');

  await page.evaluate(()=>{
    document.getElementById('view-graph').hidden=false;
    window.runRequests=0;
    window.api=async url=>{
      if(url==='/api/report-sources'){window.runRequests++;return new Promise(resolve=>{window.releaseRuns=resolve;});}
      if(url.startsWith('/api/graph-catalog'))return url.includes('second')?new Promise(resolve=>{window.releaseIndustries=resolve;}):{industries:[{name:'Banking'}]};
      if(url.startsWith('/api/comparison?'))return {industry:url.includes('Technology')?'Technology':'Banking',reports:[],subjects:[{id:'company:test',label:'Test company',kind:'Company',company:'Test company',periods:{2024:{giving:100},2026:{giving:null}}}]};
      throw new Error('Unexpected fixture request: '+url);
    };
  });
  for(const script of ['ui-state.js','comparison.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.locator('#compare-open').evaluate(button=>{button.click();button.click();button.click();});
  assert.equal(await page.locator('#compare-open').textContent(),'Back to graph');
  assert.equal(await page.evaluate(()=>window.runRequests),1,'Reopening must share the pending catalog request');
  assert.equal(await page.locator('#compare-panel h2').evaluate(heading=>heading===document.activeElement),true);
  await page.evaluate(()=>window.releaseRuns({runs:[{id:'first',name:'First source'},{id:'second',name:'Second source'}]}));
  await page.waitForFunction(()=>document.querySelector('#compare-results button'));
  await page.locator('#compare-results button').evaluate(button=>button.click());
  assert.equal(await page.locator('#compare-cards tbody tr').last().locator('td').last().textContent(),'Unavailable','A missing current period must not appear as a negative change');
  await page.locator('#compare-run').evaluate(select=>{select.value='second';select.dispatchEvent(new Event('change'));});
  assert.equal(await page.locator('#compare-industry').isDisabled(),true);
  assert.equal(await page.locator('#compare-industry option').count(),0,'The prior industry must be cleared while loading the new source');
  await page.evaluate(()=>window.releaseIndustries({industries:[{name:'Technology'}]}));
  await page.waitForFunction(()=>document.querySelector('#compare-industry').value==='Technology'&&document.querySelector('#compare-results button'));
  await page.locator('#compare-open').evaluate(button=>button.click());
  assert.equal(await page.locator('#compare-open').textContent(),'Advanced comparison');
  assert.equal(await page.locator('#compare-panel').isHidden(),true);
  assert.deepEqual(errors,[]);
});

test('chart type defaults, axis labels, time explanations and dense scatter preserve complete data',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setContent(fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
  for(const file of ['style.css','graph.css','comparison.css','acu-theme.css','workspace.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    window.currentView='graph';window.selectedRun='fixture';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.api=async()=>{throw new Error('The chart workshop fixture must not load reports.');};
    window.workshop={columns:[{index:0,name:'Record ID',type:'number',distinct:24},{index:1,name:'Recipient organization',type:'category',distinct:3},
      {index:2,name:'Source',type:'category',distinct:2},{index:3,name:'Receipt amount (USD)',type:'number',distinct:24},
      {index:4,name:'People (count)',type:'number',distinct:24},{index:5,name:'Calendar year',type:'number',distinct:8}],
      rows:Array.from({length:24},(_,i)=>[1000+i,['A very long recipient organization name with several words','Second recipient','Third recipient'][i%3],i%2?'Employee':'Corporate',100+i*10,i+1,2015+Math.floor(i/3)]),
      row_numbers:Array.from({length:24},(_,i)=>i+2),row_count:24,header_row:1,sheet:'Workshop',sha256:'fictional-workshop',note:'Fictional browser fixture.',
      default_config:{type:'bar',x:1,y:3,series:-1,aggregation:'sum',transform:'none',period:'year'}};
    window.sendWorkshop=data=>window.dispatchEvent(new CustomEvent('mentat:chart-data',{detail:structuredClone(data)}));
  });
  for(const script of ['theme.js','chart-math.js','grouped-chart.js','charts.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.evaluate(()=>{window.sendWorkshop(window.workshop);document.getElementById('view-graph').hidden=false;document.getElementById('graph-builder').showModal();});
  const types=await page.locator('#chart-type option').evaluateAll(options=>options.map(option=>option.value));
  assert.equal(types.length,15);
  for(const type of types){
    await page.locator('#chart-x').evaluate(select=>{select.value='4';});
    await page.locator('#chart-series').evaluate(select=>{select.value='-1';});
    await page.locator('#chart-type').selectOption(type);
    assert.equal(await page.locator('#chart-error').isHidden(),true,`${type}: ${await page.locator('#chart-error').textContent()}`);
    assert.equal(await page.locator('#chart-type').inputValue(),type);
    assert.equal(await page.locator('#chart-y').inputValue(),'3','A valid selected measure stays selected.');
    assert.equal(await page.locator('#chart-image svg').count(),1);
    assert.match(await page.locator('#chart-image svg').textContent(),/Receipt amount \(USD\)/,`${type} identifies the measure and source unit.`);
    if(type==='scatter'){
      assert.equal(await page.locator('#chart-x').inputValue(),'4');
      assert.equal(await page.locator('#chart-image circle').count(),24);
      assert.equal((await page.locator('[data-axis-title=x] tspan').allTextContents()).join(' '),'People (count)');
    }else if(type==='histogram')assert.equal(await page.locator('#chart-x').inputValue(),'-1');
    else assert.notEqual(await page.locator('#chart-x').inputValue(),'4',`${type} replaces an incompatible numeric grouping.`);
    if(['grouped','groupedhorizontal','multiline'].includes(type))assert.notEqual(await page.locator('#chart-series').inputValue(),'-1');
    if(['grouped','groupedhorizontal'].includes(type)){
      assert.ok((await page.locator('[data-group-label] title').allTextContents()).includes('A very long recipient organization name with several words'));
      assert.equal(await page.locator('[data-group-label]').first().locator('tspan').count(),2,'Long comparison group labels wrap and retain their full SVG title.');
    }
  }
  await page.locator('#chart-type').selectOption('bar');
  assert.match(await page.locator('#chart-title').textContent(),/Receipt amount \(USD\) · Sum/);
  await page.locator('[data-transform=annual]').click();
  assert.equal(await page.locator('#chart-x').inputValue(),'5');
  assert.match(await page.locator('#chart-quick-note').textContent(),/groups observations/);
  await page.locator('[data-transform=yoy]').click();
  assert.match(await page.locator('[data-axis-title=y]').textContent(),/year-over-year change \(%\)/);
  assert.match(await page.locator('#chart-quick-note').textContent(),/previous calendar year/);
  await page.locator('[data-transform=forecast]').click();
  assert.equal(await page.locator('#chart-error').isHidden(),true);
  assert.match(await page.locator('#chart-scope').textContent(),/same-election-phase/);
  assert.match(await page.locator('#chart-values').textContent(),/Same-phase baseline/);
  assert.equal((await page.locator('[data-transform=forecast]').textContent()).trim(),'Same-phase baseline','The quick button carries the baseline label; its transform id is unchanged');
  assert.equal(await page.locator('#chart-transform').inputValue(),'forecast');assert.equal(await page.locator('#chart-transform option:checked').textContent(),'Same-phase baseline');
  assert.match(await page.locator('#chart-quick-note').textContent(),/^Same-phase baseline compares corresponding election phases/,'The quick note names the baseline');

  await page.evaluate(()=>{
    const simple={...window.workshop,columns:[{index:0,name:'Category',type:'category',distinct:2},{index:1,name:'Amount',type:'number',distinct:2}],rows:[['A',10],['B',20]],row_numbers:[2,3],row_count:2,
      default_config:{type:'bar',x:0,y:1,aggregation:'sum',transform:'none'}};
    window.sendWorkshop(simple);
  });
  await page.locator('#chart-type').selectOption('scatter');
  assert.match(await page.locator('#chart-error').textContent(),/two numeric/);
  assert.equal(await page.locator('#chart-image svg').count(),0,'An incompatible new chart must not leave the previous chart visible.');
  assert.equal(await page.locator('#chart-export').isHidden(),true);
  await page.locator('#chart-type').selectOption('grouped');
  assert.match(await page.locator('#chart-error').textContent(),/two distinct grouping/);
  await page.locator('#chart-type').selectOption('radar');
  assert.match(await page.locator('#chart-error').textContent(),/at least three/);
  await page.locator('#chart-type').selectOption('histogram');
  const countTicks=await page.locator('[data-axis-tick=y]').allTextContents();
  assert.deepEqual(countTicks,['0','1'],'Small row-count axes use only integer ticks.');
  const binLabels=await page.locator('[data-category-label] tspan').allTextContents();
  assert.deepEqual(binLabels,['10 – 15','15 – 20'],'Histogram ranges omit artificial trailing decimals.');
  assert.match(await page.locator('#chart-values').textContent(),/10\.000 – 15\.000/,'The original computed bin labels remain in data exports.');
  await page.evaluate(()=>{const signed=structuredClone(window.workshop);signed.rows[0][3]=-100;window.sendWorkshop(signed);});
  for(const type of ['pie','doughnut','pareto','radar']){
    await page.locator('#chart-type').selectOption(type);
    assert.match(await page.locator('#chart-error').textContent(),/nonnegative/);
    assert.equal(await page.locator('#chart-image svg').count(),0);
  }

  await page.evaluate(()=>window.sendWorkshop({columns:[{index:0,name:'X',type:'number',distinct:2},{index:1,name:'Y',type:'number',distinct:2}],
    rows:[[0,0],[.0000005,.000005]],row_numbers:[2,3],row_count:2,header_row:1,sheet:'Tiny scatter',sha256:'tiny-fixture',note:'Fictional tiny numeric span.',
    default_config:{type:'scatter',x:0,y:1,aggregation:'sum',transform:'none'}}));
  assert.equal(await page.locator('#chart-error').isHidden(),true);
  assert.deepEqual(await page.locator('[data-axis-tick=x]').allTextContents(),['0','1e-7','2e-7','3e-7','4e-7','5e-7']);
  assert.deepEqual(await page.locator('[data-axis-tick=y]').allTextContents(),['0','1e-6','2e-6','3e-6','4e-6','5e-6']);
  assert.equal(await page.locator('#chart-image circle').count(),2,'Tiny scalar values retain both exact observations.');

  await page.evaluate(()=>{
    const count=50000;
    window.sendWorkshop({columns:[{index:0,name:'Record ID',type:'number',distinct:count},{index:1,name:'Donation (USD)',type:'number',distinct:count},{index:2,name:'Tenure (years)',type:'number',distinct:count}],
      rows:Array.from({length:count},(_,i)=>[900000+i,i/10,i/100]),row_numbers:Array.from({length:count},(_,i)=>i+2),row_count:count,header_row:1,sheet:'Dense scatter',sha256:'dense-fixture',note:'Fictional 50,000-point test.',
      default_config:{type:'scatter',x:2,y:1,aggregation:'sum',transform:'none'}});
  });
  assert.equal(await page.locator('#chart-error').isHidden(),true);
  assert.equal(await page.locator('#chart-image svg').getAttribute('data-total-points'),'50000');
  assert.deepEqual(await page.locator('[data-axis-tick=x]').allTextContents(),['0','100','200','300','400','500']);
  assert.deepEqual(await page.locator('[data-axis-tick=y]').allTextContents(),['0','1,000','2,000','3,000','4,000','5,000']);
  assert.equal(await page.locator('#chart-image circle').count(),0,'Dense plots do not allocate a circle and title for each row.');
  assert.equal(await page.locator('#chart-image path[data-point-count]').evaluateAll(paths=>paths.reduce((sum,path)=>sum+Number(path.dataset.pointCount),0)),50000);
  assert.equal(await page.locator('#chart-image path[data-point-count]').evaluateAll(paths=>paths.reduce((sum,path)=>sum+(path.getAttribute('d').match(/M/g)||[]).length,0)),50000,'Each selected point has its own SVG mark.');
  assert.ok(await page.locator('#chart-image svg *').count()<150,'Dense rendering keeps the SVG DOM bounded.');
  assert.equal(await page.locator('#chart-values tbody tr').count(),200);
  assert.match(await page.locator('#chart-values').textContent(),/first 200 of 50,000/);
  assert.match(await page.locator('#chart-image svg').textContent(),/50,000 plotted rows · all points, no sampling/);
  const downloadPromise=page.waitForEvent('download');
  await page.locator('#chart-save-csv').click();
  const csv=fs.readFileSync(await (await downloadPromise).path(),'utf8');
  assert.equal(csv.split('\r\n').length,50003,'CSV keeps all 50,000 rows plus column and provenance headers.');
  assert.match(csv,/'?Tenure \(years\)/);
  assert.match(csv,/'?Donation \(USD\)/);
  assert.ok(csv.endsWith('"499.99","4999.9"'),'The last original point remains exact in CSV.');
  if(process.env.MENTAT_DENSE_CHART_SCREENSHOT)await page.screenshot({path:process.env.MENTAT_DENSE_CHART_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
});
