/* The transform that used to read "Same-phase forecast" now reads "Same-phase baseline" everywhere a user
   sees it (owner ruling, 2026-09-30): the Chart Studio Analytics menu, the classic chart dialog and the
   Studio note. Labels only: the transform id stays `forecast`, and what is saved is that id, never the
   label. Real index.html and chart-studio.js, synthetic contribution rows. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.resolve(__dirname,'../app');
const STORE='mentat-chart-studio-v1';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://labels.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));await page.goto('http://labels.fixture/#chart-studio');
  for(const file of ['style.css','chart-studio.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
   window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(p=>p.hidden=p.id!=='view-chart-studio');window.currentView='chart-studio';window.selectedRun='published-reports';window.downloads=[];
   const fields=[{id:'company',label:'Company',role:'dimension'},{id:'year',label:'Year',role:'dimension'},...['amount','records','contributors'].map(id=>({id,label:id,role:'measure',unit:id==='amount'?'cents':'count'}))];
   const provenance={workbook:'Synthetic.xlsx',workbook_sha256:'a'.repeat(64),database_sha256:'b'.repeat(64),index_token:'index',query_engine_sha256:'engine'};
   const response=value=>({ok:true,json:async()=>value});
   window.fetch=async(url,options)=>{
    const q=options?.body?JSON.parse(options.body):{};
    if(url.endsWith('/session'))return response({token:'fixture',read_only:true});
    if(url.endsWith('/catalog'))return response({datasets:[{id:'Banking',label:'Synthetic',available:true,fields,bases:[{id:'receipt',label:'Included receipts'}],provenance}],fields});
    if(url.endsWith('/query'))return response({rows:Array.from({length:8},(_,i)=>({year:String(2018+i),amount:10000+1000*i,records:1,contributors:1})),columns:[...q.dimensions,...q.measures].map(id=>fields.find(f=>f.id===id)),totals:{amount:108000,records:8,contributors:8},basis_label:'Included receipts',group_count:8,offset:0,has_more:false,provenance,reconciliation:{conserved:true,receipt_matches_workbook:true},notices:[]});
    throw new Error(url);
   };
   window.MentatExport={download:content=>downloads.push(content),image:async()=>{}};
  });
  for(const file of ['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js'])await page.addScriptTag({path:path.join(app,file)});
  await page.evaluate(store=>{const state=MentatStudioCore.createSheet('Sheet','s1');state.columns=[{column:1,aggregation:'sum'}];state.rows=[{column:2,aggregation:'sum'}];state.marks.type='line';state.analytics.transform='none';state.analytics.period='year';localStorage.setItem(store,JSON.stringify({['contributions|Banking|'+'a'.repeat(64)]:{sheets:[state],active:0}}));},STORE);
  await page.addScriptTag({path:path.join(app,'chart-studio.js')});await page.waitForFunction(()=>document.querySelector('#studio-canvas svg')||document.querySelector('#studio-error').textContent);
  assert.equal(await page.locator('#studio-error').textContent(),'');

  // Chart Studio Analytics menu: the row is labelled as a baseline; its hint is unchanged.
  const rows=page.locator('#studio-analytics-period button.studio-analytics-item');
  assert.deepEqual(await rows.locator('.studio-analytics-text > span').allTextContents(),['Period totals','Year-over-year %','Period change','Same-phase baseline'],'The period-analysis menu');
  assert.equal(await rows.nth(3).locator('small').textContent(),'Repeat the last completed value from the matching election phase','The hint is not part of this change');
  assert.doesNotMatch(await page.locator('#studio-analytics-list').textContent(),/forecast/i,'No analytics row calls the baseline a forecast');

  // Choosing it saves the id, never the label: a saved view is the sheets, so this is what a view holds and what reopens.
  await page.locator('#studio-tab-analytics').click();assert.equal(await rows.nth(3).isVisible(),true,'The Analytics tab shows the menu');
  await rows.nth(3).click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-analytics-period button')[3].getAttribute('aria-checked')==='true');
  const raw=await page.evaluate(store=>localStorage.getItem(store),STORE),saved=Object.values(JSON.parse(raw));
  assert.equal(saved[0].sheets[0].analytics.transform,'forecast','The saved transform id is unchanged');
  assert.doesNotMatch(raw,/Same-phase/i,'No label reaches the saved sheets');
  assert.doesNotMatch(await page.evaluate(()=>location.href),/forecast|baseline/i,'The URL carries neither the id nor the label');
  const labels=await page.locator('#studio-canvas svg title').allTextContents();
  assert.ok(labels.some(x=>x.includes('Same-phase baseline')),'The chart marks already said so');

  // The classic chart dialog and the Studio note, from the real page markup.
  assert.equal(await page.locator('#chart-time-quick button[data-transform="forecast"]').count(),1,'The transform id on the quick button is unchanged');
  assert.equal((await page.locator('#chart-time-quick button[data-transform="forecast"]').textContent()).trim(),'Same-phase baseline','Quick button');
  assert.equal(await page.locator('#chart-transform option[value="forecast"]').textContent(),'Same-phase baseline','Time analysis choice');
  const graph=await page.locator('#view-graph').textContent();
  assert.match(graph,/Same-phase baseline repeats the latest completed value from the matching election phase;/,'Dialog note');
  assert.doesNotMatch(graph,/Forecast repeats/,'The dialog note no longer calls it a forecast');
  assert.match(await page.locator('#view-chart-studio').textContent(),/period change and the same-phase baseline need a date, year or election-cycle field on a shelf\./,'Studio note');
  assert.doesNotMatch(await page.locator('body').textContent(),/same-phase forecast/i,'No page text names the transform a same-phase forecast');
  assert.deepEqual(errors,[]);
  console.log('PASS Same-phase baseline: Analytics menu, classic dialog and Studio note relabelled; transform id and saved sheets unchanged');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
