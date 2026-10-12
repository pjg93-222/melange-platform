const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.resolve(__dirname,'../app');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
 for (const transform of ['yoy','change','forecast']) {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://forecast.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));await page.goto('http://forecast.fixture/#chart-studio');
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
  await page.evaluate(transform=>{const state=MentatStudioCore.createSheet('Forecast','s1');state.columns=[{column:1,aggregation:'sum'}];state.rows=[{column:2,aggregation:'sum'}];state.marks.type='line';state.analytics.transform=transform;state.analytics.period='year';localStorage.setItem('mentat-chart-studio-v1',JSON.stringify({['contributions|Banking|'+'a'.repeat(64)]:{sheets:[state],active:0}}));},transform);
  await page.addScriptTag({path:path.join(app,'chart-studio.js')});await page.waitForFunction(()=>document.querySelector('#studio-canvas svg')||document.querySelector('#studio-error').textContent);
  assert.equal(await page.locator('#studio-error').textContent(),'');
  assert.deepEqual(errors,[]);
  const labels=await page.locator('#studio-canvas svg title').allTextContents();
  assert.ok(labels.length>=7,transform+' needs period marks');
  if(transform==='forecast'){
    assert.ok(await page.locator('#studio-canvas [stroke-dasharray="7 5"]').count(),'Projection must be distinguished from observed money');
    assert.ok(labels.some(x=>x.includes('Same-phase baseline')));
    assert.ok(labels.some(x=>x.includes('100')),'Cents are converted to dollars');
  }else if(transform==='change')assert.ok(labels.some(x=>x.includes('10')),'Annual change is $10');
  console.log(transform+': rendered successfully');await page.close();
 }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
