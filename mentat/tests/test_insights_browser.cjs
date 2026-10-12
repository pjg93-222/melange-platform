/* Real Insights assets, synthetic source responses. No report or live FEC data.
   Delayed fetch deliberately ignores abort: revision checks must protect scope. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const {chromium} = require(require.resolve('playwright', {paths:[process.env.MENTAT_NODE_MODULES || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app = path.resolve(__dirname,'../app');
const {PANELS} = require(path.join(app,'insights.js'));
const catalog = {datasets:[{id:'Alpha',label:'Alpha industry',available:true},{id:'Beta',label:'Beta industry',available:true}],panels:PANELS.map(([id,title,description]) => ({id,title,description}))};
function fixture(dataset='Alpha', panels=['favorites','trends','changes']) {
  const out = {status:'ready',scope:{dataset,basis:'receipt',basis_label:'Reported receipts',as_of:'2026-09-01',baseline_cycle:'2023-2024',current_cycle:'2025-2026',company:null,funding_source:null},provenance:{workbook:dataset+' synthetic.xlsx',workbook_sha256:'a'.repeat(64),index_token:'fixture-version-1',scope:'Synthetic fixture, not production data.'},options:{companies:[dataset+' Corp'],funding_sources:['employee','corporate'],cycles:['2023-2024','2025-2026']},notices:['Fixture coverage is incomplete.'],panels:{}};
  for (const key of panels) out.panels[key] = {status:'ready',title:PANELS.find(p => p[0]===key)[1],summary:dataset+' synthetic '+key,columns:[{key:'recipient',label:'Recipient',type:'text'},{key:'amount',label:'Selected amount',type:'money'},{key:'breadth',label:'Company breadth',type:'number'},{key:'share',label:'Share',type:'percent'}],rows:[{recipient:dataset+' candidate',amount:125000,breadth:3,share:.25,filters:[{field:'committee_id',op:'eq',value:'C0001'}]},{recipient:'=HYPERLINK("https://invalid.test")',amount:50000,breadth:1,share:.1}],notices:[]};
  return out;
}
(async () => {
  const browser = await chromium.launch({channel:process.env.MENTAT_BROWSER_CHANNEL || 'chrome',headless:true});
  try {
    const page = await browser.newPage({acceptDownloads:true,viewport:{width:1440,height:1080}}), errors=[];
    page.on('pageerror',error => errors.push(error.message));
    await page.route('http://mentat.test/**',route => {
      const url = new URL(route.request().url());
      if (url.pathname !== '/') { const name=path.basename(url.pathname); return route.fulfill({contentType:name.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(app,name),'utf8')}); }
      return route.fulfill({contentType:'text/html;charset=utf-8',body:'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/acu-theme.css"><link rel="stylesheet" href="/insights.css"><style>*{box-sizing:border-box}body{margin:0;padding:24px}.panel{border:1px solid var(--line);border-radius:10px;background:white}button{font:inherit;padding:8px 12px;border-radius:5px;cursor:pointer}.muted{color:var(--muted)}.section-label{font-size:10px;text-transform:uppercase;letter-spacing:1px}[hidden]{display:none!important}</style></head><body>'+fs.readFileSync(path.join(app,'templates/view_insights.html'),'utf8')+'<script src="/insights.js"></script></body></html>'});
    });
    await page.addInitScript(data => {
      window.calls=[]; window.pending=[];
      window.fetch=(url,options={}) => {
        const body=options.body ? JSON.parse(options.body) : null;
        window.calls.push({url,body,headers:options.headers});
        if (url==='/api/insights/catalog') return Promise.resolve({ok:true,json:async()=>data});
        if (url==='/api/contributions/session') return Promise.resolve({ok:true,json:async()=>({token:'fixture-token'})});
        return new Promise((resolve,reject)=>window.pending.push({url,body,resolve,reject}));
      };
      window.respond=(index,data)=>window.pending[index].resolve({ok:true,json:async()=>data});
      window.reject=index=>window.pending[index].reject(new Error('Synthetic analysis failure'));
    },catalog);
    const pending = count => page.waitForFunction(n=>window.pending.length===n,count);
    const respond = (index,data) => page.evaluate(({index,data})=>window.respond(index,data),{index,data});
    const show = () => page.evaluate(()=>{document.getElementById('view-insights').hidden=false;MentatInsights.setActive(true);});
    await page.goto('http://mentat.test/');
    assert.equal(await page.evaluate(()=>calls.length),0,'Inactive workspace must not fetch');
    await show(); await pending(1);
    assert.deepEqual(await page.evaluate(()=>pending[0].body.panels),['favorites','trends','changes']);
    await page.locator('#insights-page-sources').click();
    assert.equal(await page.locator('#insights-sources').isVisible(),true,'Source records opens before a result exists');
    assert.match(await page.locator('#insights-sources-status').innerText(),/Run an analysis/);
    await page.locator('#insights-sources-close').click();
    await respond(0,fixture());
    // The rail replaced the Choose analyses tab: it sits beside the cards.
    assert.equal(await page.locator('#insights-analyses').isVisible(),true,'The analyses rail is always on the page');
    assert.equal(await page.locator('#insights-results').isVisible(),true,'The rail sits beside the cards instead of replacing them');
    assert.equal(await page.locator('#insights-toggles input').count(),10,'Ten switches: the forecast card is behind a setting that is off by default');
    assert.equal(await page.locator('#insights-toggle-forecast').count(),0,'The forecast switch is not rendered while its setting is off');assert.equal(await page.locator('#insights-panel-forecast').count(),0);
    assert.equal(await page.locator('#insights-toggles input:checked').count(),3);
    assert.equal(await page.locator('#insights-panel-favorites .insights-ranked tbody tr').count(),2,'Top recipients ranks the returned rows in Chart view');
    assert.equal(await page.locator('#insights-panel-trends .insights-card-tools button').first().isDisabled(),true,'No dated series: no running-total curve is drawn from undated totals');
    await page.locator('#insights-panel-favorites .insights-card-tools button').filter({hasText:'Table'}).click();
    assert.match(await page.locator('#insights-panel-favorites').innerText(),/\$1,250\.00/,'Money is integer cents');
    assert.match(await page.locator('#insights-panel-favorites').innerText(),/25%/);
    assert.match(await page.locator('#insights-scope').textContent(),/2026-09-01/,'Sources carries the resolved cutoff');
    assert.doesNotMatch(await page.locator('#insights-toggles').innerText(),/lobbying|inflation/i);
    await page.locator('#insights-toggle-concentration').check(); await pending(2);
    assert.equal(await page.locator('#insights-results article[data-state="ready"]').count(),0,'Prior result clears when analyses change');
    const enabled=['favorites','trends','changes','concentration'];
    await respond(1,fixture('Alpha',enabled));
    assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('mentat.insights.panels.v1'))),enabled);
    await page.selectOption('#insights-dataset','Beta'); await pending(3);
    await page.selectOption('#insights-dataset','Alpha'); await pending(4);
    await respond(3,fixture('Alpha',enabled)); await respond(2,fixture('Beta',enabled));
    assert.equal(await page.locator('#insights-results').isVisible(),true,'Overview restores the analysis panels');
    assert.equal(await page.locator('#insights-sources').isHidden(),true,'The cards show beside the rail with no records view open');
    await page.locator('#insights-panel-favorites .insights-card-tools button').filter({hasText:'Table'}).click();
    await page.locator('#insights-panel-favorites .insights-info').click();
    assert.match(await page.locator('#insights-results').innerText(),/Alpha synthetic/);
    assert.doesNotMatch(await page.locator('#insights-results').innerText(),/Beta synthetic/,'Late response cannot overwrite current scope');
    await page.locator('#insights-panel-favorites .insights-info').click();
    await page.locator('#insights-panel-favorites .insights-source-button').first().click(); await pending(5);
    assert.equal(await page.locator('#insights-sources').isVisible(),true,'A record link opens the Source records view');
    assert.equal(await page.locator('#insights-results').isHidden(),true,'Source records replaces the Overview display');
    const request=await page.evaluate(()=>pending[4].body);
    assert.equal(request.as_of,'2026-09-01'); assert.equal(request.baseline_cycle,'2023-2024'); assert.equal(request.dataset,'Alpha'); assert.equal(request.basis,'receipt'); assert.equal(request.index_token,'fixture-version-1'); assert.deepEqual(request.filters,[{field:'committee_id',op:'eq',value:'C0001'}]);
    assert.equal(await page.evaluate(()=>calls[calls.length-1].headers['X-Mentat-Token']),'fixture-token');
    await respond(4,{rows:[{record_id:'synthetic-receipt',amount:25000}],columns:[{key:'record_id',label:'Record',type:'text'},{key:'amount',label:'Amount',type:'money'}],total:1});
    assert.match(await page.locator('#insights-sources').innerText(),/synthetic-receipt/);
    assert.match(await page.locator('#insights-sources').innerText(),/\$250\.00/);
    assert.equal(await page.locator('#insights-sources-next').isDisabled(),true);
    assert.match(await page.locator('#insights-panel-favorites').innerText(),/No record filter/);
    const csvWait=page.waitForEvent('download'); if(!await page.locator('#insights-export-csv').isVisible())await page.locator('.insights-export summary').click();await page.locator('#insights-export-csv').click(); const csv=fs.readFileSync(await (await csvWait).path(),'utf8');
    assert.match(csv,/amount \(cents\)/); assert.match(csv,/'=HYPERLINK/,'CSV neutralizes formula-like recipient text'); assert.match(csv,/workbook_sha256/); assert.match(csv,/2026-09-01/);
    const jsonWait=page.waitForEvent('download'); if(!await page.locator('#insights-export-json').isVisible())await page.locator('.insights-export summary').click();await page.locator('#insights-export-json').click(); const json=JSON.parse(fs.readFileSync(await (await jsonWait).path(),'utf8'));
    assert.equal(json.scope.dataset,'Alpha'); assert.equal(json.provenance.workbook,'Alpha synthetic.xlsx'); assert.ok(json.exported_at); assert.equal(json._request,undefined);
    await page.selectOption('#insights-dataset','Beta'); await pending(6);
    assert.equal(await page.locator('#insights-sources').isHidden(),true);
    assert.equal(await page.locator('#insights-export-json').isDisabled(),true);
    const empty=fixture('Beta',enabled); Object.values(empty.panels).forEach(panel=>{panel.rows=[];panel.status='empty';});
    await respond(5,empty); assert.match(await page.locator('#insights-status').innerText(),/missing evidence does not establish zero/);
    await page.locator('#insights-refresh').click(); await pending(7); await page.evaluate(()=>reject(6));
    assert.match(await page.locator('#insights-status').innerText(),/Synthetic analysis failure/); assert.equal(await page.locator('#insights-results .insights-retry-link').first().isVisible(),true,'A failed query offers Try again inside its card');
    await page.locator('#insights-results .insights-retry-link').first().click(); await pending(8);
    assert.equal(await page.evaluate(()=>pending[7].body.retry),true,'Explicit retry resets a failed background job');
    await respond(7,{status:'building',message:'Preparing fixture snapshot',retry_after_ms:500}); assert.match(await page.locator('#insights-status').innerText(),/Preparing fixture snapshot/);
    await pending(9); assert.equal(await page.evaluate(()=>pending[8].body.retry),undefined,'Polling must not repeatedly reset builds'); await respond(8,fixture('Beta',enabled));
    await page.locator('#insights-panel-favorites .insights-source-button').first().click(); await pending(10);
    await page.selectOption('#insights-dataset','Alpha'); await pending(11);
    await respond(10,fixture('Alpha',enabled)); await respond(9,{rows:[{record_id:'stale-Beta-record'}],total:1});
    assert.equal(await page.locator('#insights-sources').isHidden(),true,'Late evidence cannot return to a new scope');
    await page.locator('#insights-refresh').click(); await pending(12);
    await page.evaluate(()=>MentatInsights.setActive(false)); await respond(11,fixture('Beta',enabled));
    assert.equal(await page.locator('#insights-results article').count(),0,'Inactive workspace rejects late analysis');
    await page.evaluate(()=>MentatInsights.setActive(true)); await pending(13); await respond(12,fixture('Alpha',enabled));
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Narrow layout must not overflow the page');
    const screenshotPath=process.env.MENTAT_INSIGHTS_SCREENSHOT; if(screenshotPath) await page.screenshot({path:screenshotPath,fullPage:true});
    // No Turn all off control: switch each analysis off in the rail.
    for(const id of await page.locator('#insights-toggles input:checked').evaluateAll(nodes=>nodes.map(node=>node.id)))await page.locator('#'+id).uncheck();
    assert.match(await page.locator('#insights-status').innerText(),/All analyses are off/); assert.equal(await page.evaluate(()=>pending.length),16,'The all-off state sends no query');
    await page.reload(); await show(); assert.equal(await page.locator('#insights-toggles input:checked').count(),0,'Off choices persist across page reload'); assert.equal(await page.evaluate(()=>calls.length),0);
    await page.locator('#insights-core-only').click(); await pending(1); await respond(0,fixture());
    // The forecast card sits behind a setting (More options, off by default). Switch it on so the engine pass below covers every card.
    await page.locator('#insights-more-options').click(); await page.locator('#insights-show-forecast').check();
    assert.equal(await page.locator('#insights-toggle-forecast').isChecked(),false,'Showing the forecast switch does not turn the card on');assert.equal(await page.evaluate(()=>pending.length),1,'Showing the switch requests nothing');
    assert.equal(await page.locator('#insights-toggles input:checked').count(),3);
    // A second pass uses actual pure-engine envelopes, not a copied UI contract.
    const engine=JSON.parse(execFileSync(process.env.MENTAT_PYTHON || 'python',['-c',[
      'import json, sys',
      'sys.path.insert(0, sys.argv[1])',
      'from analysis_metrics import analyze',
      'rows=[]',
      'for cycle in (2020,2022,2024,2026):',
      ' for channel in ("employee","corporate"):',
      '  for company, amount in (("Synthetic A",125000),("Synthetic B",50000)):',
      '   rows.append(dict(source_id=str(len(rows)), company=company, committee_id="C0001", recipient="Synthetic candidate", funding_source=channel, amount_cents=amount+(cycle-2020)*1000, date=str(cycle)+"-04-10", cycle=str(cycle), contributor_key=company))',
      'print(json.dumps(analyze(rows,dict(as_of="2026-09-01",basis="receipt"))))'
    ].join('\n'),app],{encoding:'utf8',windowsHide:true}));
    await page.locator('#insights-enable-all').click(); await pending(2); assert.deepEqual(await page.evaluate(()=>pending[1].body.panels),PANELS.map(p=>p[0]),'With the forecast setting on, All requests every analysis');
    const actual=fixture('Alpha',[]); actual.panels=engine.panels;
    actual.panels.spending={status:'partial',title:'Outgoing payments and campaign finances',summary:'Synthetic full committee outgoing activity.',columns:[{key:'entity',label:'Entity',type:'text'},{key:'amount',label:'Disbursements',type:'money'},{key:'source_url',label:'Source',type:'url'}],rows:[{entity:'Synthetic campaign',amount:12345,source_url:'https://www.fec.gov/data/committee/C0001/'}],sources:[{file:'synthetic-openfec-manifest.json',sha256:'synthetic-integration-sha',scope:'All campaign activity'}],notices:[],vendor_patterns:[{sender_id:'C0001',payee:'Synthetic payee',amount_cents:12345,positive_share:1}]};
    await respond(1,actual);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('#insights-panel-concentration .insights-bar-group-title').count(),2,'Concentration names each group (company, recipient) after its figure repaints');
    assert.equal(await page.locator('#insights-panel-trends .insights-chart-legend span').count(),2,'The earlier and later cycles draw as separate lines');
    assert.equal(await page.locator('#insights-panel-changes .insights-waterfall-total').count(),4,'Each funding source has its own baseline/current waterfall');
    assert.deepEqual(await page.locator('#insights-panel-changes figure:not([hidden]) .insights-xlabel').evaluateAll(labels=>labels.map(label=>label.textContent)),['Baseline2023-2024','Continuing','Newlyobserved','No longerobserved','Current2025-2026'],'The waterfall decomposes the change by recipient, earlier -> later');
    assert.equal(await page.locator('#insights-summary-recipients').textContent(),'1 continuing · 0 new · 0 no longer observed','Recipients are counted by period from the engine response');
    assert.deepEqual(await page.locator('#insights-panel-favorites .insights-ranked th').evaluateAll(cells=>cells.map(cell=>cell.textContent)),['#','Recipient','Amount','Change'],'Top recipients draws # | Recipient | Amount | Change');
    assert.deepEqual(await page.locator('#insights-panel-favorites .insights-ranked tbody tr:first-child td').evaluateAll(cells=>cells.map(cell=>[cell.textContent,cell.className])),[['1.','insights-rank'],['Synthetic candidate','insights-recipient'],['$1.9K','insights-numeric'],['+$40','insights-numeric insights-up']],'Amount is the later period\'s same-stage net; Change is teal when up');
    await page.locator('#insights-panel-changes .insights-card-tools button').filter({hasText:'Table'}).click();
    await page.locator('#insights-panel-spending .insights-info').click();
    assert.match(await page.locator('#insights-panel-spending .insights-card-source').innerText(),/separate from industry giving/);
    assert.doesNotMatch(await page.locator('#insights-panel-spending .insights-card-source').innerText(),/Reported receipts|Alpha synthetic.xlsx/,'Integration money cannot inherit the giving accounting footer');
    assert.match(await page.locator('#insights-panel-spending').textContent(),/synthetic-integration-sha/);
    assert.equal(await page.locator('#insights-panel-spending .insights-source-link').getAttribute('href'),'https://www.fec.gov/data/committee/C0001/');
    const integrationExportWait=page.waitForEvent('download');if(!await page.locator('#insights-export-csv').isVisible())await page.locator('.insights-export summary').click();await page.locator('#insights-export-csv').click();
    const integrationCSV=fs.readFileSync(await (await integrationExportWait).path(),'utf8');assert.match(integrationCSV,/synthetic-integration-sha/);assert.match(integrationCSV,/spending.vendor_patterns/);assert.match(integrationCSV,/separate from industry giving/);
    await page.locator('#insights-panel-changes .insights-source-button').filter({hasText:'Baseline records'}).first().click(); await pending(3);
    assert.ok((await page.evaluate(()=>pending[2].body.filters)).some(f=>f.field==='cycle' && f.value==='2023-2024'),'Baseline trace uses the baseline cycle');
    await respond(2,{rows:[{record_id:'historical-source',amount_cents:25000,first_seen_by_insights:'2026-09-20T20:00:00Z'}],total_records:51,has_more:true});
    assert.match(await page.locator('#insights-sources-table').innerText(),/First inspected here/);
    assert.match(await page.locator('#insights-sources-table').innerText(),/not filing or publication dates/);
    assert.match(await page.locator('#insights-sources-status').innerText(),/of 51/);assert.match(await page.locator('#insights-sources-table').innerText(),/\$250\.00/);
    await page.locator('#insights-sources-next').click();await pending(4);assert.equal(await page.evaluate(()=>pending[3].body.offset),50);
    await page.evaluate(()=>reject(3));assert.equal(await page.locator('#insights-sources-retry').isVisible(),true);
    await page.locator('#insights-sources-retry').click();await pending(5);await respond(4,{rows:[{source_id:'second-page'}],total_records:51,has_more:false});
    assert.match(await page.locator('#insights-sources-status').innerText(),/Records 51–51 of 51/);assert.equal(await page.locator('#insights-sources-prev').isDisabled(),false);assert.equal(await page.locator('#insights-sources-next').isDisabled(),true);
    await page.locator('#insights-sources-close').click();
    await page.locator('#insights-panel-forecast .insights-info').click();
    assert.match(await page.locator('#insights-panel-forecast').innerText(),/Forecast requirements and validation/);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Engine-driven narrow layout must not overflow the page');
    await page.locator('#insights-core-only').click(); await pending(6); const core=fixture('Alpha',[]); for(const key of ['favorites','trends','changes'])core.panels[key]=engine.panels[key]; await respond(5,core);
    await page.setViewportSize({width:1440,height:1080});
    for(const id of await page.locator('#insights-results article').evaluateAll(cards=>cards.filter(card=>{const chart=card.querySelector('.insights-card-tools button');return chart&&!chart.disabled&&chart.getAttribute('aria-pressed')==='false';}).map(card=>card.id)))await page.locator('#'+id+' .insights-card-tools button').filter({hasText:'Chart'}).click();
    if(screenshotPath) await page.screenshot({path:screenshotPath,fullPage:true});
    assert.deepEqual(errors,[]);
    // Full generated console: app.js precedes insights.js in defer order. The
    // direct link must activate even though its first setActive call was early.
    const consolePage=await browser.newPage({viewport:{width:1440,height:1080}}),consoleErrors=[];
    consolePage.on('pageerror',error=>consoleErrors.push(error.message));
    const serveConsole=route=>{
      const url=new URL(route.request().url()),file=url.pathname==='/'?path.join(app,'index.html'):path.join(app,path.basename(url.pathname));
      if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
      const extension=path.extname(file),type=extension==='.js'?'application/javascript;charset=utf-8':extension==='.css'?'text/css;charset=utf-8':extension==='.svg'?'image/svg+xml':'text/html;charset=utf-8';
      return route.fulfill({contentType:type,body:fs.readFileSync(file)});
    };
    await consolePage.route('http://mentat-console.test/**',serveConsole);
    const initializeConsole=({catalog,response})=>{
      window.analysisQueries=[];
      window.fetch=(url,options={})=>{
        let data;
        if(url==='/api/insights/catalog')data=catalog;
        else if(url==='/api/contributions/session')data={token:'fixture-token'};
        else if(url==='/api/insights/query'){const query=JSON.parse(options.body);window.analysisQueries.push(query);const labels={receipt:'Reported receipts',original_observed:'Observed original giving',original_supported:'Supported original giving',pac_activity:'PAC activity'};data=catalog.datasets.some(dataset=>dataset.id===query.dataset)?{...response,scope:{...response.scope,...query,basis_label:labels[query.basis]},provenance:{...response.provenance,workbook:query.dataset+' synthetic.xlsx'}}:{status:'failed',message:'No published report for '+query.dataset};}
        else return new Promise(()=>{}); // Keep unrelated app bootstrap inert.
        return Promise.resolve({ok:true,json:async()=>data});
      };
    };
    const consoleFixture={catalog,response:{...core,scope:{...core.scope,dataset:'Beta'}}};
    await consolePage.addInitScript(initializeConsole,consoleFixture);
    await consolePage.goto('http://mentat-console.test/?industry=Beta#insights');
    await consolePage.waitForFunction(()=>window.analysisQueries.length===1);
    assert.equal(await consolePage.locator('#view-insights').isVisible(),true);
    assert.equal(await consolePage.locator('#tab-insights').getAttribute('aria-selected'),'true');
    assert.equal(await consolePage.evaluate(()=>analysisQueries[0].dataset),'Beta','Direct link retains its industry');
    assert.equal(await consolePage.locator('#insights-results article').count(),3);
    assert.equal(await consolePage.locator('#insights-baseline-cycle').inputValue(),'','Automatic periods do not become explicit filters when results arrive');
    assert.equal(await consolePage.locator('#insights-current-cycle').inputValue(),'','Panel toggles should reuse the same automatic scope');
    const widths=await consolePage.locator('#insights-results').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length);
    assert.equal(widths,2,'Supporting analyses use two columns below the dominant trend chart');
    if(screenshotPath)await consolePage.screenshot({path:screenshotPath.replace(/\.png$/, '-console.png'),fullPage:true});
    assert.ok((await consolePage.locator('#insights-results').boundingBox()).y<760,'Core charts must enter the first desktop viewport');
    assert.equal(await consolePage.locator('.insights-card-tools button[aria-expanded="false"]').count(),3,'Charts are primary and tables are available on demand');
    const headingY=await consolePage.locator('#insights-results .insights-panel-heading').evaluateAll(nodes=>nodes.slice(1).map(node=>node.getBoundingClientRect().top));
    const footers=await consolePage.locator('#insights-results article').evaluateAll(cards=>cards.slice(1).map(card=>Boolean(card.querySelector('.insights-info[aria-controls]')&&card.querySelector('.insights-method .insights-card-source'))));
    assert.ok(Math.max(...headingY)-Math.min(...headingY)<2,'Supporting desktop headings align');assert.deepEqual(footers,[true,true],'Supporting cards keep their method and source footer behind the (i)');
    // Industry is shared by all workspaces; Insights' two-cycle controls and
    // accounting basis remain independent from Graph's single-cycle filter.
    await consolePage.selectOption('#insights-basis','original_supported');await consolePage.waitForFunction(()=>analysisQueries.length===2);
    assert.equal(await consolePage.locator('#insights-baseline-cycle').isVisible(),true,'The period chips replace the Compare button');
    await consolePage.selectOption('#insights-baseline-cycle','2023-2024');await consolePage.waitForFunction(()=>analysisQueries.length===3);
    await consolePage.selectOption('#insights-current-cycle','2025-2026');await consolePage.waitForFunction(()=>analysisQueries.length===4);
    await consolePage.evaluate(()=>MentatScope.write({cycle:'2022',company:'Graph company'}));
    await consolePage.selectOption('#insights-dataset','Alpha');await consolePage.waitForFunction(()=>analysisQueries.length===5);
    assert.equal(await consolePage.evaluate(()=>MentatScope.read().industry),'Alpha','Insights industry changes must reach the shared URL');
    assert.equal(await consolePage.evaluate(()=>MentatScope.read().cycle),'2022','Insights must not rewrite Graph cycle from its comparison periods');
    assert.equal(await consolePage.locator('#insights-baseline-cycle').inputValue(),'2023-2024');assert.equal(await consolePage.locator('#insights-current-cycle').inputValue(),'2025-2026');
    assert.equal(await consolePage.evaluate(()=>analysisQueries.at(-1).basis),'original_supported');
    await consolePage.evaluate(()=>MentatScope.write({industry:'Beta',cycle:'2024'}));await consolePage.waitForFunction(()=>analysisQueries.length===6);
    assert.equal(await consolePage.locator('#insights-dataset').inputValue(),'Beta','Another workspace can change the active Insights industry');
    assert.equal(await consolePage.evaluate(()=>analysisQueries.at(-1).baseline_cycle),'2023-2024');assert.equal(await consolePage.evaluate(()=>analysisQueries.at(-1).current_cycle),'2025-2026');
    await consolePage.evaluate(()=>MentatScope.write({cycle:'2026'}));assert.equal(await consolePage.evaluate(()=>analysisQueries.length),6,'Unrelated shared-scope changes must not duplicate Insights queries');
    await consolePage.locator('#tab-loading').click();assert.equal(await consolePage.locator('#view-insights').isHidden(),true);
    assert.equal(await consolePage.locator('#insights-results article').count(),0);
    await consolePage.evaluate(()=>MentatScope.write({industry:'Alpha'}));assert.equal(await consolePage.evaluate(()=>analysisQueries.length),6,'Hidden Insights must not fetch when another workspace changes industry');
    await consolePage.locator('#tab-insights').click();await consolePage.waitForFunction(()=>analysisQueries.length===7);
    assert.equal(await consolePage.evaluate(()=>analysisQueries.at(-1).dataset),'Alpha','Returning to Insights adopts the current shared industry once');
    await consolePage.evaluate(()=>{history.replaceState(null,'','?industry=Beta&cycle=2022#insights');dispatchEvent(new PopStateEvent('popstate'));});await consolePage.waitForFunction(()=>analysisQueries.length===8);
    assert.equal(await consolePage.locator('#insights-dataset').inputValue(),'Beta','History navigation also carries the shared industry');
    assert.equal(await consolePage.locator('#insights-basis').inputValue(),'original_supported');assert.equal(await consolePage.locator('#insights-current-cycle').inputValue(),'2025-2026');
    assert.equal(await consolePage.locator('#insights-options').isHidden(),true,'More options stays closed until it is asked for');
    if(screenshotPath)await consolePage.screenshot({path:screenshotPath.replace(/\.png$/, '-console.png'),fullPage:true});
    await consolePage.locator('#insights-more-options').click();
    await consolePage.setViewportSize({width:1024,height:900});assert.ok(await consolePage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Full console with all controls open fits a medium screen');
    if(screenshotPath)await consolePage.screenshot({path:screenshotPath.replace(/\.png$/, '-console-medium.png'),fullPage:true});
    await consolePage.setViewportSize({width:390,height:844});
    assert.ok(await consolePage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Full console with all controls open fits a narrow screen');
    if(screenshotPath)await consolePage.screenshot({path:screenshotPath.replace(/\.png$/, '-console-narrow.png'),fullPage:true});
    assert.deepEqual(consoleErrors,[]);
    const missingPage=await browser.newPage();await missingPage.route('http://mentat-console.test/**',serveConsole);await missingPage.addInitScript(initializeConsole,consoleFixture);
    await missingPage.goto('http://mentat-console.test/?industry=Missing%20industry#insights');await missingPage.waitForFunction(()=>analysisQueries.length===1);
    assert.equal(await missingPage.evaluate(()=>analysisQueries[0].dataset),'Missing industry','An unavailable shared industry must never silently become the first catalog entry');
    assert.equal(await missingPage.locator('#insights-dataset').inputValue(),'Missing industry');assert.match(await missingPage.locator('#insights-dataset option:checked').innerText(),/unavailable/);
    assert.match(await missingPage.locator('#insights-status').innerText(),/No published report for Missing industry/);assert.equal(await missingPage.locator('#insights-results article[data-state="ready"]').count(),0);assert.equal(await missingPage.locator('#insights-results .insights-card-error').count(),3,'The failure reads inside each card');
    await missingPage.selectOption('#insights-dataset','Beta');await missingPage.waitForFunction(()=>analysisQueries.length===2);assert.equal(await missingPage.evaluate(()=>MentatScope.read().industry),'Beta');assert.equal(await missingPage.locator('#insights-results article[data-state="ready"]').count(),3,'Selecting an available industry recovers without reloading');
    const transmitted=await page.evaluate(()=>calls.filter(call=>['/api/insights/query','/api/insights/records'].includes(call.url)).map(call=>({body:call.body,records:call.url.endsWith('/records')})));
    execFileSync(process.env.MENTAT_PYTHON || 'python',['-c','import json,sys; sys.path.insert(0,sys.argv[1]); from analysis_data import validate; [validate(item["body"], records=item["records"]) for item in json.loads(sys.argv[2])]',app,JSON.stringify(transmitted)],{encoding:'utf8',windowsHide:true});
    console.log('PASS Insights: lazy activation, ten toggles plus the forecast setting, persistent preferences, integer-cent charts, scope invalidation, resolved source context, source pagination, exports, failure/retry, preparation polling, late replies and narrow layout');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
