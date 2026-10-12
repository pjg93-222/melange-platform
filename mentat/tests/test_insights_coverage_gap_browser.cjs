/* FIX 4 on the screen a reader actually sees. The Coverage gap chip and its caption must be on the
   always-visible Insights page (the period summary), not only in the Sources panel, which is hidden
   until the Sources button is pressed. Real Insights markup, styles and scripts; every response is
   synthetic and no console, report or service is touched. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(require.resolve('playwright',{paths:[process.env.MENTAT_NODE_MODULES || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app = path.resolve(__dirname,'../app');
const cell = (company,cycle,destination,held,reported,percent) => ({company,cycle,destination,held,reported,percent});
const GAPS = {failed:true,source:'pinned payload export_guards',unitemized:21,cells:[
  cell('Comcast','2023-2024','contributions',17869,29991,60),cell('Comcast','2021-2022','contributions',26441,34014,78),
  cell('Verizon','2023-2024','contributions',14783,19170,77),cell('Comcast','2025-2026','contributions',11753,15269,77),
  cell('Verizon','2025-2026','contributions',31654,33964,93)]};
const FULLY_ITEMIZED = {failed:true,source:'Data Quality sheet',unitemized:0,cells:[cell('Verizon','2025-2026','contributions',31654,33964,93)]};
const CLEAN = {failed:false,source:'Data Quality sheet',unitemized:0,cells:[]};
const catalog = {datasets:[
  {id:'Telecom',label:'Telecom',available:true,collection_gaps:GAPS},
  {id:'Itemized',label:'Itemized telecom',available:true,collection_gaps:FULLY_ITEMIZED},
  {id:'Clean',label:'Clean industry',available:true,collection_gaps:CLEAN},
  {id:'Older',label:'Older console',available:true}],panels:[]};
const html='<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/acu-theme.css"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/insights.css"><style>*{box-sizing:border-box}body{padding:20px;margin:0}.panel{border:1px solid #ddd}[hidden]{display:none!important}</style>'+
  fs.readFileSync(path.join(app,'templates/view_insights.html'),'utf8')+'<script src="/coverage-view.js"></script><script src="/insights.js"></script>';

async function prepare(page) {
  await page.route('http://insights-gap.test/**',route=>{
    const file=path.basename(new URL(route.request().url()).pathname);
    return route.fulfill({contentType:file ? file.endsWith('.js')?'application/javascript;charset=utf-8':'text/css;charset=utf-8':'text/html;charset=utf-8',body:file?fs.readFileSync(path.join(app,file),'utf8'):html});
  });
  await page.addInitScript(catalog=>{
    window.queries=[];
    window.fetch=async (url,options={})=>{
      const ok=data=>({ok:true,json:async()=>data});
      if(url==='/api/contributions/session')return ok({token:'gap-fixture'});
      if(url==='/api/insights/catalog')return ok(catalog);
      const query=JSON.parse(options.body);queries.push(query);
      const scope={...query,as_of:'2026-09-01',baseline_cycle:query.baseline_cycle||'2023-2024',current_cycle:query.current_cycle||'2025-2026',basis_label:'Reported receipts'};
      const columns=[{key:'recipient',label:'Recipient',type:'text'},{key:'amount_cents',label:'Amount',type:'money'},{key:'funding_source',label:'Funding source',type:'text'}];
      const rows=[{recipient:'Alpha recipient',amount_cents:125000,funding_source:'employee_original'}];
      const panels={};for(const key of query.panels)panels[key]={title:key,status:'ready',summary:'Synthetic observations.',columns,rows,notices:[]};
      if(panels.changes){panels.changes.cards=[{funding_source:'employee_original',baseline_cents:100000,current_cents:110000,reconciled:true,continuing_recipients:3,newly_observed_recipients:2,no_longer_observed_recipients:1}];panels.changes.rows=[];panels.changes.columns=[];}
      return ok({status:'ready',scope,panels,options:{companies:['Comcast','Verizon','Frontier Communications'],funding_sources:['employee_original'],cycles:['2021-2022','2023-2024','2025-2026']},provenance:{workbook:'Synthetic scope.xlsx',index_token:'synthetic-source'},notices:[]});
    };
  },catalog);
  await page.goto('http://insights-gap.test/');
  await page.evaluate(()=>{document.getElementById('view-insights').hidden=false;MentatInsights.setActive(true);});
  try { await page.waitForFunction(()=>document.getElementById('insights-results').getAttribute('aria-busy')==='false'&&document.querySelectorAll('#insights-results article').length===3,null,{timeout:8000}); }
  catch(error){ throw new Error('The cards never became ready: '+await page.locator('#insights-status').textContent()+' | queries '+await page.evaluate(()=>queries.length)); }
}
// What a reader sees: visible chips and the whole page's text, never the hidden Sources panel.
const visibleChips = page => page.locator('.coverage-gap-chip:visible');
const outsideSources = page => page.evaluate(()=>[...document.querySelectorAll('.coverage-gap-chip')].filter(chip=>chip.offsetParent!==null&&!chip.closest('#insights-sources')).map(chip=>chip.closest('#insights-summary')?'summary':'elsewhere'));
const settle = async (page,count) => { await page.waitForFunction(n=>queries.length===n,count); await page.waitForFunction(()=>document.getElementById('insights-results').getAttribute('aria-busy')==='false'&&document.querySelectorAll('#insights-results article').length===3); };

(async()=>{
  const browser=await chromium.launch({channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));await prepare(page);
    await page.selectOption('#insights-dataset','Telecom');await settle(page,2);
    // The hidden copy exists, but is not what is checked: the main screen has to say it.
    assert.equal(await page.locator('#insights-sources').isHidden(),true,'The Sources panel is closed');
    assert.ok((await outsideSources(page)).includes('summary'),'a visible Coverage gap chip sits in the period summary, outside the closed Sources panel');
    assert.match(await page.locator('body').innerText(),/FEC-reported/,'the page text a reader sees names what FEC reported');

    // Comcast, 2023-2024 -> 2025-2026: the company and the two compared cycles.
    await page.selectOption('#insights-company','Comcast');await settle(page,3);
    await page.selectOption('#insights-baseline-cycle','2023-2024');await settle(page,4);
    await page.selectOption('#insights-current-cycle','2025-2026');await settle(page,5);
    const summary=page.locator('#insights-summary .coverage-gap-chip');
    assert.equal(await summary.isVisible(),true);assert.equal(await summary.innerText(),'Coverage gap');
    const line=await page.locator('#insights-summary').innerText();
    assert.match(line,/Comcast 2025-26: 77% of FEC-reported records \(11,753 of 15,269 contributions\)/);
    assert.match(line,/Comcast 2023-24: 60% of FEC-reported records \(17,869 of 29,991 contributions\)/);
    assert.match(line,/21 more short cells are not itemized in the workbook's Data Quality check/);
    assert.doesNotMatch(line,/Verizon|Comcast 2021-22/,'only the selected company, and only the two compared cycles');
    assert.match(await summary.getAttribute('title'),/Source: pinned payload export_guards/);
    assert.equal((await visibleChips(page).count()),1,'one chip on the main screen');

    if(process.env.MENTAT_SCREENSHOT)await page.screenshot({path:process.env.MENTAT_SCREENSHOT,fullPage:false});
    // The strip still reads as before and the chip does not displace it.
    assert.match(await page.locator('#insights-summary-left').innerText(),/\$1,000/);

    // Another company in the same periods: its cells, not Comcast's.
    await page.selectOption('#insights-company','Verizon');await settle(page,6);
    const verizon=await page.locator('#insights-summary').innerText();
    assert.match(verizon,/Verizon 2025-26: 93% of FEC-reported records/);assert.match(verizon,/Verizon 2023-24: 77%/);assert.doesNotMatch(verizon,/Comcast/);

    // Refresh asks again and shows the same, once.
    await page.locator('#insights-refresh').click();await settle(page,7);
    assert.equal(await visibleChips(page).count(),1,'Refresh leaves one chip, not two');
    assert.match(await page.locator('#insights-summary').innerText(),/Verizon 2025-26: 93%/);

    // A company the check names no cell for, with every short cell itemized: nothing to say, nothing shown.
    await page.selectOption('#insights-dataset','Itemized');await settle(page,8);
    await page.selectOption('#insights-company','Frontier Communications');await settle(page,9);
    assert.equal(await visibleChips(page).count(),0,'a scope with no gap clears the chip');
    assert.doesNotMatch(await page.locator('body').innerText(),/FEC-reported/);
    assert.equal(await page.locator('#insights-summary .coverage-gap-chip').count(),0,'and removes it, rather than hiding a stale one');
    // The same company where the check left cells unlisted: never shown as complete.
    await page.selectOption('#insights-dataset','Telecom');await settle(page,10);
    await page.selectOption('#insights-company','Frontier Communications');await settle(page,11);
    assert.match(await page.locator('#insights-summary').innerText(),/^[\s\S]*Coverage gap[\s\S]*21 more short cells are not itemized/);
    assert.doesNotMatch(await page.locator('#insights-summary').innerText(),/% of FEC-reported records/,'but no cell is named that this company does not have');

    // A report whose check passed, and a console that predates the field.
    for(const [dataset,count] of [['Clean',12],['Older',13]]){
      await page.selectOption('#insights-dataset',dataset);await settle(page,count);
      assert.equal(await visibleChips(page).count(),0,dataset+' shows no chip');assert.equal(await page.locator('#insights-summary .coverage-gap-chip').count(),0);
    }

    // Opening Sources still shows the copy that was always there.
    await page.selectOption('#insights-dataset','Telecom');await settle(page,14);
    await page.selectOption('#insights-company','Comcast');await settle(page,15);
    await page.locator('#insights-page-sources').click();
    assert.equal(await page.locator('#insights-sources').isVisible(),true);
    assert.match(await page.locator('#insights-scope').innerText(),/77% of FEC-reported records/,'the Sources panel keeps its own copy');
    assert.deepEqual(errors,[]);
    console.log('PASS Insights coverage gap: chip and caption on the main screen for Comcast 2023-2024 to 2025-2026, per company, cleared when the scope has no gap, one chip after Refresh, Sources copy kept');
  } catch(error){console.error(error);throw error;} finally {await browser.close();}
})().catch(()=>{process.exitCode=1;});
