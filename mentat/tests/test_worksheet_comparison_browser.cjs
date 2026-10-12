/* Synthetic browser regression: independent sources, aggregation and stale exports. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {chromium}=require(require.resolve('playwright',{paths:[process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app=path.resolve(__dirname,'../app');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://comparison.test/**',async route=>{
   const url=new URL(route.request().url()),file=path.basename(url.pathname);
   if(url.pathname.startsWith('/api/')){
    const body=file==='report-sources'?{runs:[{id:'saved',name:'Saved reports'}]}:file==='reports'?{reports:[{index:0,name:'A.xlsx'},{index:1,name:'B.xlsx'}]}:!url.searchParams.has('sheet')?{sheets:['Facts']}:{sheet:'Facts',row_count:3,sha256:'fixture',columns:[{name:'Group',type:'category'},{name:'Amount',type:'number'}],rows:url.searchParams.get('index')==='1'?[['One',100],['One',200],['Three',0]]:[['One',10],['One',30],['Two',-5]],row_numbers:[6,7,8]};
    return route.fulfill({json:body});
   }
   if(file)return route.fulfill({contentType:file.endsWith('.js')?'text/javascript; charset=utf-8':'text/css; charset=utf-8',body:fs.readFileSync(path.join(app,file),'utf8')});
   const template=fs.readFileSync(path.join(app,'templates/view_chart_studio.html'),'utf8').replace('aria-labelledby="tab-chart-studio" hidden','aria-labelledby="tab-chart-studio"');
   return route.fulfill({contentType:'text/html',body:'<style>[hidden]{display:none!important}*{box-sizing:border-box}</style><link rel="stylesheet" href="/chart-studio.css">'+template+['chart-math.js','chart-studio-core.js','contribution-plot.js','worksheet-comparison.js'].map(s=>'<script src="/'+s+'"></script>').join('')});
  });
  await page.goto('http://comparison.test/');await page.click('#studio-compare-sheets');
  const sources=page.locator('.worksheet-comparison-sources fieldset');
  await sources.nth(1).getByLabel('Report',{exact:true}).selectOption('1');
  await page.waitForFunction(()=>[...document.querySelectorAll('.worksheet-comparison-sources .studio-note')].every(p=>p.textContent.includes('3 rows')));
  await sources.nth(0).getByLabel('Aggregation',{exact:true}).selectOption('mean');
  await page.selectOption('[data-compare-type]','table');await page.click('[data-compare-run]');
  assert.match(await page.locator('.worksheet-comparison-canvas').innerText(),/20/);
  assert.match(await page.locator('.worksheet-comparison-canvas').innerText(),/300/);
  assert.match(await page.locator('.worksheet-comparison-canvas').innerText(),/—/);
  assert.equal(await page.locator('[data-compare-export]').isEnabled(),true);
  await sources.nth(0).getByLabel('First worksheet row',{exact:true}).fill('7');await sources.nth(0).getByLabel('First worksheet row',{exact:true}).press('Tab');
  assert.equal(await page.locator('[data-compare-export]').isEnabled(),false);
  assert.equal(await page.locator('.worksheet-comparison-canvas').innerText(),'');
  await page.click('[data-compare-run]');assert.match(await page.locator('.worksheet-comparison-canvas').innerText(),/30/);
  await page.selectOption('[data-compare-type]','bar');assert.equal(await page.locator('.worksheet-comparison-canvas svg').count(),1);
  for(const width of [1280,620]){await page.setViewportSize({width,height:900});assert.ok(await page.locator('#studio-comparison').evaluate(el=>el.scrollWidth<=el.clientWidth+1));}
  await page.click('[data-compare-close]');assert.equal(await page.locator('#studio-comparison').isVisible(),false);
  assert.deepEqual(errors,[]);console.log('PASS worksheet comparison: independent mean/sum, missing versus zero, row selection, stale export invalidation, bars/table, desktop/mobile layout.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
