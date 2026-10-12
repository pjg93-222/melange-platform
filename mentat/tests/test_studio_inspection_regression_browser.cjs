const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.resolve(__dirname,'../app');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage();page.setDefaultTimeout(10000);
  await page.setContent(fs.readFileSync(path.join(app,'contribution-studio.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
  await page.evaluate(()=>{
   const fields=[...['company','cycle'].map(id=>({id,label:id,name:id,role:'dimension'})),...['amount','records','contributors'].map(id=>({id,label:id,name:id,role:'measure',unit:id==='amount'?'cents':'count'}))];
   const provenance={workbook:'Synthetic.xlsx',workbook_sha256:'a'.repeat(64),database_sha256:'a'.repeat(64),index_token:'old-index',query_engine_sha256:'same-engine'};
   window.stale=false;const response=value=>({ok:true,json:async()=>value});
   window.fetch=async(url,options)=>{
    const q=options?.body?JSON.parse(options.body):{};
    if(url.endsWith('/session'))return response({token:'fixture',read_only:true});
    if(url.endsWith('/catalog'))return response({datasets:[{id:'Banking',label:'Synthetic',available:true,fields,bases:[{id:'receipt',label:'Included receipts'}],provenance}],fields});
    if(url.endsWith('/query'))return response({rows:[{company:'Fixture Company',cycle:'2023-2024',amount:10000,records:1,contributors:1}],columns:[...q.dimensions,...q.measures].map(id=>fields.find(f=>f.id===id)),totals:{amount:10000,records:1,contributors:1},basis_label:'Included receipts',group_count:1,offset:0,has_more:false,provenance,reconciliation:{conserved:true,receipt_matches_workbook:true},notices:[]});
    if(url.endsWith('/records'))return response({rows:[{company:'Fixture Company',contributor:window.stale?'CHANGED SOURCE DONOR':'ORIGINAL DONOR',amount_cents:window.stale?90000:10000,raw_cents:window.stale?90000:10000,date:'2024-01-01',source_id:'new-record',reasons:[]}],total_records:1,has_more:false,provenance:window.stale?{...provenance,workbook_sha256:'b'.repeat(64),database_sha256:'b'.repeat(64),index_token:'new-index'}:provenance});
    throw new Error(url);
   };
  });
  for(const script of ['contribution-plot.js','chart-studio-contributions.js','contribution-studio.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.waitForFunction(()=>document.querySelector('#visualization svg'));
  await page.locator('#inspect-all').click();
  await page.waitForFunction(()=>document.querySelector('#record-table').textContent.includes('ORIGINAL DONOR'));
  assert.match(await page.locator('#record-table').textContent(),/\$100\.00/);
  await page.evaluate(()=>window.stale=true);
  await page.locator('#inspect-all').click();
  await page.waitForFunction(()=>document.querySelector('#record-table').textContent.includes('Source changed'));
  assert.doesNotMatch(await page.locator('#record-table').textContent(),/CHANGED SOURCE DONOR/);
  assert.match(await page.locator('#receipt-values').textContent(),/\$100\.00/);
  console.log('Matching source accepted; stale inspection rejected.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
