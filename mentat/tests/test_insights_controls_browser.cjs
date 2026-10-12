/* Exercise keyboard use and scope controls through the real Insights module.
   Every response is synthetic; no production files, servers or services. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(require.resolve('playwright',{paths:[process.env.MENTAT_NODE_MODULES || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app=path.resolve(__dirname,'../app');
const html='<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/acu-theme.css"><link rel="stylesheet" href="/insights.css"><style>*{box-sizing:border-box}body{padding:20px;margin:0}.panel{border:1px solid #ddd}[hidden]{display:none!important}</style>'+fs.readFileSync(path.join(app,'templates/view_insights.html'),'utf8')+'<script src="/insights.js"></script>';
async function prepare(page,storage='normal') {
  await page.route('http://insights-controls.test/**',route=>{
    const file=path.basename(new URL(route.request().url()).pathname);
    return route.fulfill({contentType:file ? file.endsWith('.js')?'application/javascript;charset=utf-8':'text/css;charset=utf-8':'text/html;charset=utf-8',body:file?fs.readFileSync(path.join(app,file),'utf8'):html});
  });
  await page.addInitScript(storage=>{
    if(storage==='corrupt'){localStorage.setItem('mentat.insights.panels.v1','invalid JSON');localStorage.setItem('mentat.insights.forecast-card.v1','invalid JSON');}
    if(storage==='blocked'){Storage.prototype.getItem=()=>{throw new DOMException('Blocked','SecurityError');};Storage.prototype.setItem=()=>{throw new DOMException('Blocked','SecurityError');};}
    window.queries=[];window.recordQueries=[];window.hideCompany=false;window.failNextPage=false;
    window.fetch=async (url,options={})=>{
      const ok=data=>({ok:true,json:async()=>data});
      if(url==='/api/contributions/session')return ok({token:'controls-fixture'});
      if(url==='/api/insights/catalog')return ok({datasets:[{id:'Alpha',label:'Alpha',available:true},{id:'Beta',label:'Beta',available:true}],panels:[]});
      const query=JSON.parse(options.body);
      if(url==='/api/insights/records'){
        recordQueries.push(query);
        if(query.offset && failNextPage){window.failNextPage=false;return{ok:false,json:async()=>({error:'Synthetic page failure'})};}
        return ok({rows:Array.from({length:query.offset?1:50},(_,i)=>({source_id:'record-'+(query.offset+i),amount_cents:100})),total_records:51,has_more:!query.offset});
      }
      queries.push(query);
      const cycles=['2021-2022','2023-2024','2025-2026'],current=query.current_cycle||cycles.at(-1),baseline=query.baseline_cycle||[...cycles].reverse().find(cycle=>cycle<current)||'';
      if(baseline&&baseline>=current)return{ok:false,json:async()=>({error:'The baseline cycle must be earlier than the current cycle.'})};
      const scope={...query,as_of:query.as_of||'2026-09-01',baseline_cycle:baseline,current_cycle:current,basis_label:query.basis==='pac_activity'?'PAC activity':'Reported receipts'};
      const columns=[{key:'recipient',label:'Recipient',type:'text'},{key:'amount_cents',label:'Amount',type:'money'},{key:'funding_source',label:'Funding source',type:'text'}];
      const rows=[{recipient:'Alpha recipient',amount_cents:125000,funding_source:'employee_original',filters:[{field:'committee_id',op:'eq',value:'C00000001'}]},{recipient:'Beta recipient',amount_cents:50000,funding_source:'pac_activity',filters:[{field:'committee_id',op:'eq',value:'C00000002'}]},{recipient:'Unavailable amount',amount_cents:null,funding_source:'funding_unverified'}];
      const panels={};for(const key of query.panels)panels[key]={title:key,status:'ready',summary:'Synthetic observations.',columns,rows,notices:[]};
      if(panels.changes){panels.changes.cards=[];panels.changes.rows=[];panels.changes.columns=[{key:'component',label:'Component',type:'text'},{key:'funding_source',label:'Funding source',type:'text'},{key:'change_cents',label:'Change',type:'money'},{key:'baseline_cycle',label:'Baseline cycle',type:'text'}];for(const channel of ['employee_original','pac_activity']){panels.changes.cards.push({funding_source:channel,baseline_cents:100000,current_cents:110000,reconciled:true,continuing_recipients:3,newly_observed_recipients:2,no_longer_observed_recipients:1});(panels.changes.recipient_components??=[]).push(...[['Continuing observed recipients',4000,3],['Newly observed recipients',9000,2],['No longer observed recipients',-3000,1]].map(([component,change_cents,recipients])=>({component,funding_source:channel,change_cents,recipients,filters:[{field:'funding_source',op:'eq',value:channel}],comparison_scopes:{baseline:[{field:'cycle',op:'eq',value:'2023-2024'}],current:[{field:'cycle',op:'eq',value:'2025-2026'}]}})));for(const [component,change]of [['Continuing observed companies',5000],['Newly observed companies',7000],['No longer observed companies',-2000]])panels.changes.rows.push({component,funding_source:channel,change_cents:change,baseline_cycle:'2023-2024',filters:[{field:'funding_source',op:'eq',value:channel}]});}}
      return ok({status:'ready',scope,panels,options:{companies:hideCompany?['Other company']:['Alpha Corp','Other company'],funding_sources:['employee_original','pac_activity'],cycles:['2021-2022','2023-2024','2025-2026']},provenance:{workbook:'Synthetic scope.xlsx',index_token:'synthetic-source'},notices:[]});
    };
  },storage);
  await page.goto('http://insights-controls.test/');
  await page.evaluate(()=>{document.getElementById('view-insights').hidden=false;MentatInsights.setActive(true);});
  await page.waitForFunction(()=>document.querySelectorAll('#insights-results article[data-state="ready"]').length===3);
}
(async()=>{
  const browser=await chromium.launch({channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome',headless:true});
  try {
    const page=await browser.newPage({acceptDownloads:true,viewport:{width:1440,height:1080}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));await prepare(page);
    await page.locator('#insights-panel-favorites .insights-card-tools button').filter({hasText:'Table'}).click();
    const sort=page.locator('#insights-table-favorites').getByRole('button',{name:'Sort by Amount',exact:true});await sort.focus();await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Sort by Amount','Sorting must preserve keyboard focus');
    assert.equal(await page.locator('#insights-table-favorites tbody tr:first-child td:first-child').innerText(),'Alpha recipient');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#insights-table-favorites tbody tr:first-child td:first-child').innerText(),'Beta recipient','A second Enter reverses numeric order');
    assert.equal(await page.locator('#insights-table-favorites tbody tr:last-child td:first-child').innerText(),'Unavailable amount','Missing numbers stay last in both directions');
    const evidence=page.locator('#insights-table-favorites .insights-source-button').first();await evidence.focus();await page.keyboard.press('Enter');
    await page.waitForFunction(()=>recordQueries.length===1);await page.locator('#insights-sources-close').click();
    assert.equal(await page.evaluate(()=>document.activeElement.textContent),'View records','Closing evidence returns keyboard focus to its trigger');
    await page.keyboard.press('Enter');await page.waitForFunction(()=>recordQueries.length===2);
    await page.evaluate(()=>window.failNextPage=true);await page.locator('#insights-sources-next').click();await page.waitForFunction(()=>recordQueries.length===3);
    assert.equal(await page.locator('#insights-sources-prev').isDisabled(),false,'A failed later page must not trap the user away from earlier records');
    await page.locator('#insights-sources-prev').click();await page.waitForFunction(()=>recordQueries.length===4);assert.equal(await page.evaluate(()=>recordQueries[3].offset),0);
    await page.locator('#insights-sources-close').click();
    await page.selectOption('#insights-company','Alpha Corp');await page.waitForFunction(()=>queries.at(-1).company==='Alpha Corp');
    await page.evaluate(()=>window.hideCompany=true);await page.selectOption('#insights-basis','pac_activity');await page.waitForFunction(()=>queries.at(-1).basis==='pac_activity');
    assert.equal(await page.locator('#insights-company').inputValue(),'Alpha Corp','Unavailable options cannot silently erase the filter that produced the result');
    assert.match(await page.locator('#insights-scope').textContent(),/Alpha Corp/);
    await page.locator('#insights-more-options').click();
    await page.selectOption('#insights-funding-source','employee_original');await page.waitForFunction(()=>queries.at(-1).funding_source==='employee_original');
    assert.equal(await page.locator('#insights-funding-source option:checked').innerText(),'Employee giving');
    assert.equal(await page.locator('#insights-comparison-dataset option[value="Alpha"]').evaluate(option=>option.disabled),true,'The selected industry cannot compare to itself');
    await page.selectOption('#insights-comparison-dataset','Beta');await page.waitForFunction(()=>queries.at(-1).comparison_dataset==='Beta');
    await page.selectOption('#insights-baseline-cycle','2021-2022');await page.waitForFunction(()=>queries.at(-1).baseline_cycle==='2021-2022');
    await page.selectOption('#insights-current-cycle','2023-2024');await page.waitForFunction(()=>queries.at(-1).current_cycle==='2023-2024');
    await page.locator('#insights-as-of').fill('2024-06-30');await page.waitForFunction(()=>queries.at(-1).as_of==='2024-06-30');
    const selected=await page.evaluate(()=>queries.at(-1));assert.equal(selected.company,'Alpha Corp');assert.equal(selected.funding_source,'employee_original');assert.equal(selected.comparison_dataset,'Beta');
    const queryCountBeforeChart=await page.evaluate(()=>queries.length),filter=page.locator('#insights-panel-changes .insights-chart-filter select');assert.equal(await filter.count(),1);assert.equal(await page.locator('#insights-panel-changes figure:visible').count(),1);
    await filter.selectOption('pac_activity');assert.equal(await page.locator('#insights-panel-changes figure:visible').count(),1);assert.equal(await page.locator('#insights-panel-changes figure:visible').getAttribute('data-channel'),'pac_activity');assert.match(await page.locator('#insights-panel-changes figure:visible svg').getAttribute('aria-label'),/PAC activity/);
    assert.equal(await page.evaluate(()=>queries.length),queryCountBeforeChart,'Changing a chart channel does not request or change the analysis scope');
    const downloadWait=page.waitForEvent('download');if(!await page.locator('#insights-export-csv').isVisible())await page.locator('.insights-export summary').click();await page.locator('#insights-export-csv').click();const csv=fs.readFileSync(await(await downloadWait).path(),'utf8');
    const headers=csv.split('\r\n')[0].split(',');assert.equal(new Set(headers).size,headers.length,'CSV scope and analysis fields need distinct headers');assert.match(csv,/employee_original/,'Exports retain source codes');
    await page.selectOption('#insights-dataset','Beta');await page.waitForFunction(()=>queries.at(-1).dataset==='Beta');const changed=await page.evaluate(()=>queries.at(-1));for(const key of ['company','funding_source','comparison_dataset'])assert.equal(changed[key],undefined,'Changing industry clears incompatible '+key);assert.equal(changed.as_of,'2024-06-30');assert.equal(changed.baseline_cycle,'2021-2022');assert.equal(changed.current_cycle,'2023-2024');
    assert.deepEqual(errors,[]);
    const corrupt=await browser.newPage();await prepare(corrupt,'corrupt');assert.equal(await corrupt.locator('#insights-toggles input:checked').count(),3);assert.equal(await corrupt.locator('#insights-toggles input').count(),10,'A malformed forecast setting reads as off');assert.equal(await corrupt.locator('#insights-toggle-forecast').count(),0);assert.equal(await corrupt.locator('#insights-preference-note').isHidden(),true,'A repaired malformed preference is not a storage failure');
    const blocked=await browser.newPage();await prepare(blocked,'blocked');assert.equal(await blocked.locator('#insights-preference-note').isVisible(),true);for(const id of await blocked.locator('#insights-toggles input:checked').evaluateAll(nodes=>nodes.map(node=>node.id)))await blocked.locator('#'+id).uncheck();assert.equal(await blocked.locator('#insights-results article').count(),0);
    await blocked.locator('#insights-more-options').click();await blocked.locator('#insights-show-forecast').check();assert.equal(await blocked.locator('#insights-toggle-forecast').count(),1,'The forecast setting applies to a visit whose storage is blocked');assert.equal(await blocked.locator('#insights-preference-note').isVisible(),true);
    // Swap exchanges the chips only: no request, and the strip, change and waterfall
    // stay in time order (earlier -> later), so a fall is never shown as growth.
    const swap=await browser.newPage(),swapErrors=[];swap.on('pageerror',error=>swapErrors.push(error.message));await prepare(swap);
    const strip=()=>swap.evaluate(()=>({left:document.getElementById('insights-summary-left-label').firstChild.textContent.trim(),right:document.getElementById('insights-summary-right-label').firstChild.textContent.trim(),leftValue:document.getElementById('insights-summary-left').textContent,rightValue:document.getElementById('insights-summary-right').textContent,change:document.getElementById('insights-summary-change').textContent,changeClass:document.getElementById('insights-summary-change').className,recipients:document.getElementById('insights-summary-recipients').textContent,waterfall:[...document.querySelectorAll('#insights-panel-changes figure:not([hidden]) .insights-xlabel')].map(label=>label.textContent)}));
    await swap.waitForSelector('#insights-panel-changes figure:not([hidden]) .insights-xlabel');
    const unswapped=await strip(),swapQueries=await swap.evaluate(()=>queries.length);
    assert.deepEqual(unswapped,{left:'Net giving 2023-2024',right:'Net giving 2025-2026',leftValue:'$1,000',rightValue:'$1,100',change:'+$100+10.0%',changeClass:'insights-up',recipients:'3 continuing · 2 new · 1 no longer observed',waterfall:['Baseline2023-2024','Continuing','Newlyobserved','No longerobserved','Current2025-2026']},'The strip counts recipients by period and the waterfall reads recipients earlier -> later');
    await swap.locator('#insights-swap-periods').click();
    assert.deepEqual(await swap.locator('.insights-period-text').allInnerTexts(),['2025-2026','2023-2024'],'Swap exchanges the chips');
    assert.equal(await swap.evaluate(()=>queries.length),swapQueries,'Swap redraws from the held result without a request');
    assert.deepEqual(await strip(),unswapped,'After Swap the strip and waterfall stay in time order; a later-first pair never reads as growth');
    await swap.locator('#insights-swap-periods').click();assert.deepEqual(await swap.locator('.insights-period-text').allInnerTexts(),['2023-2024','2025-2026']);assert.equal(await swap.evaluate(()=>queries.length),swapQueries);
    await swap.locator('#insights-toggle-changes').uncheck();await swap.waitForFunction(n=>queries.length===n+1,swapQueries);await swap.waitForFunction(()=>document.querySelectorAll('#insights-results article[data-state="ready"]').length===2);
    assert.equal(await swap.locator('#insights-summary-recipients').textContent(),'—','Without the What changed analysis the Recipients cell reports no counts');
    assert.match(await swap.locator('#insights-summary-recipients').getAttribute('title'),/Turn on Changes/);assert.deepEqual(swapErrors,[]);
    // Period chips: picking the cycle the other chip shows exchanges the pair, and
    // no request ever pairs a baseline with an equal or later current cycle.
    const periods=await browser.newPage(),periodErrors=[];periods.on('pageerror',error=>periodErrors.push(error.message));await prepare(periods);
    const chips=()=>periods.locator('.insights-period-text').allInnerTexts();let sentCount=await periods.evaluate(()=>queries.length);
    const sent=async()=>{sentCount++;await periods.waitForFunction(n=>queries.length===n,sentCount);await periods.waitForFunction(()=>document.querySelectorAll('#insights-results article[data-state="ready"]').length===3||document.querySelector('#insights-results .insights-card-error'));const last=await periods.evaluate(()=>queries.at(-1));assert.equal(await periods.locator('#insights-results .insights-card-error').count(),0,'The chips sent a pair the server refuses: '+JSON.stringify([last.baseline_cycle,last.current_cycle]));return last;};
    await periods.selectOption('#insights-baseline-cycle','2025-2026');let pair=await sent();
    assert.deepEqual(await chips(),['2025-2026','2023-2024'],'Picking the latest cycle on the left exchanges the pair');assert.deepEqual([pair.baseline_cycle,pair.current_cycle],['2023-2024','2025-2026'],'A later-first pair is requested in time order');
    await periods.selectOption('#insights-current-cycle','2025-2026');pair=await sent();
    assert.deepEqual(await chips(),['2023-2024','2025-2026'],'Picking the left chip\'s cycle on the right exchanges the pair back');assert.deepEqual([pair.baseline_cycle,pair.current_cycle],['2023-2024','2025-2026']);
    await periods.selectOption('#insights-current-cycle','2023-2024');pair=await sent();
    assert.deepEqual(await chips(),['2025-2026','2023-2024'],'Picking the baseline cycle as current exchanges the pair');
    await periods.selectOption('#insights-current-cycle','');pair=await sent();
    assert.deepEqual(await chips(),['2023-2024','2025-2026'],'An automatic chip that would repeat the other chip\'s cycle exchanges the pair');assert.equal(pair.current_cycle,undefined);
    assert.equal(await periods.locator('#insights-results .insights-card-error').count(),0,'Chip changes never produce a refused pair');
    for(const [left,right] of [['2025-2026','2025-2026'],['2025-2026','']]){
      await periods.evaluate(([left,right])=>{document.getElementById('insights-baseline-cycle').value=left;document.getElementById('insights-current-cycle').value=right;},[left,right]);
      await periods.locator('#insights-refresh').click();await periods.waitForFunction(()=>/Choose two different cycles/.test(document.getElementById('insights-status').textContent));
      assert.equal(await periods.evaluate(()=>queries.length),sentCount,'An equal pair ('+left+' / '+(right||'automatic')+') is refused before any request');
      assert.equal(await periods.locator('#insights-results .insights-retry-link').count(),0,'The refusal offers no Try again that would resend it');
    }
    assert.ok(await periods.evaluate(()=>queries.every(q=>!q.baseline_cycle||!q.current_cycle||q.baseline_cycle<q.current_cycle)),'No request pairs a baseline with an equal or later current cycle');assert.deepEqual(periodErrors,[]);
    console.log('PASS Insights control audit: keyboard sorting and evidence return, page failure recovery, retained filters, all scope controls, chart channel selector, distinct CSV headers, unavailable storage, recipient counts, Swap in time order without a request, and period chips that never send an equal pair');
  } catch(error){console.error(error);throw error;} finally {await browser.close();}
})().catch(()=>{process.exitCode=1;});
