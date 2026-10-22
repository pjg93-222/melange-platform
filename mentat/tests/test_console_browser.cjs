/* Run with MENTAT_NODE_MODULES pointing to a local Playwright installation. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const {once} = require('node:events');
const modules = process.env.MENTAT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = require(require.resolve('playwright', {paths:[modules]}));
(async () => {
  const trace=message=>{if(process.env.MENTAT_BROWSER_TRACE)console.log(`[browser] ${message}`);};
  const fixture = spawn(process.env.MENTAT_PYTHON || 'python', [path.join(__dirname, 'console_browser_fixture.py')], {stdio:['pipe','pipe','pipe'],windowsHide:true});
  trace(`Starting test ${process.pid}; temporary fixture ${fixture.pid}.`);
  let browser;
  const stderr=[]; fixture.stderr.on('data', chunk=>stderr.push(chunk.toString()));
  try {
    const origin = await new Promise((resolve,reject)=>{
      let output='';
      fixture.stdout.on('data',chunk=>{output+=chunk; if(output.includes('\n'))resolve(output.trim().split('\n')[0]);});
      fixture.on('exit',code=>reject(new Error(`Fixture exited ${code}: ${stderr.join('')}`)));
      fixture.on('error',reject);
    });
    browser = await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL || 'chrome'});
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    page.setDefaultTimeout(15000);
    // Graph answers these waits with fixture queries; when other browser suites share the machine they have
    // taken longer than the 15 s default (the same conditions, only more time to reach them).
    const GRAPH_QUERY_WAIT={timeout:60000};
    async function captureScreenshot(file,fullPage=true) {
      if(!file)return;
      await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
      await page.screenshot({path:file,fullPage});
    }
    const errors=[],remote=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',request=>{if(!request.url().startsWith(origin) && !request.url().startsWith('blob:'))remote.push(request.url());});
    const savedEvidence={version:1,company:'Harborlight Credit Union',aliases:['HARBORLIGHT CU'],retrieved_at:'2026-09-10T00:00:00Z',scope:'Fixture evidence',
      committee_finances:[{cycle:2024,committee_id:'C00000001',committee_name:'First PAC'},{cycle:2026,committee_id:'C00000002',committee_name:'Second PAC'}],
      receipts:[],transfers:[],candidate_activity:[],temporal_routes:[]};
    const crossReference={available:true,status:'completed',run_id:'fictional-browser-fixture',report:{
      generated_at:'2026-09-10T00:00:00Z',scope:{industries:['Fictional credit union sample'],companies:[savedEvidence.company],cycles:[2024],source_count:1},
      summary:{input_records:5,automatic_decisions:2,unresolved_records:1,unresolved_amount:40,observed_original_giving:100,
        employee_giving:100,pac_outgoing:60,baseline_amount:160,amount_change:-60,verified_unique:false},
      steps:[{id:'collect',status:'completed'},{id:'connect',status:'review',detail:'Missing counterpart'}],
      unresolved_reasons:[{reason:'Reference missing',records:1,amount:40}],
      review_queue:[{company:savedEvidence.company,reason:'Reference missing',cycle:2024,amount:40,record_id:'fixture-123'}],
      changes:[{company:savedEvidence.company,reason:'Linked outgoing stage',cycle:2024,before:160,after:100,delta:-60,record_id:'fixture-456'}],
      limitations:['Fictional fixture only. Collection coverage remains unverified.'],
    }};
    let extraSnapshot=false;
    await page.route('**/api/evidence?*',route=>route.fulfill({json:savedEvidence}));
    await page.route('**/api/evidence',route=>route.fulfill({json:{snapshots:[{id:'fixture-evidence',company:savedEvidence.company,cycles:[2024,2026],retrieved_at:savedEvidence.retrieved_at},...(extraSnapshot?[{id:'new-evidence',company:'Another Company',cycles:[2026]}]:[])]}}));
    await page.route('**/api/cross-reference?*',route=>route.fulfill({json:crossReference}));
    await page.route('**/api/cross-reference',route=>route.fulfill({json:crossReference}));
    await page.goto(origin+'/');
    assert.equal(await page.locator('#tab-loading').getAttribute('aria-selected'),'true');
    assert.deepEqual(await page.locator('.workspace-tabs [role=tab]').evaluateAll(tabs=>tabs.map(tab=>tab.id)),
      ['tab-loading','tab-collection','tab-graph','tab-chart-studio','tab-insights','tab-cross-reference','tab-evidence','tab-public-data']);
    assert.equal(await page.locator('.workspace-tabs').getAttribute('aria-orientation'),'vertical');
    assert.equal(await page.evaluate(()=>{
      const navigation=document.querySelector('.app-navigation').getBoundingClientRect();
      const main=document.querySelector('#workspace-main').getBoundingClientRect();
      const tabs=[...document.querySelectorAll('.workspace-tabs [role=tab]')].map(tab=>tab.getBoundingClientRect());
      return navigation.right<=main.left && tabs.every((tab,index)=>index===0 || tab.top>=tabs[index-1].bottom);
    }),true,'Desktop navigation occupies its own sidebar without overlapping the workspace.');
    await page.waitForFunction(()=>document.querySelector('#loading-coverage').textContent.includes('Stale coverage'));
    // Build 07: the grid chip shows the verified date as the board writes it ('Stale · to Jun 30, 2026').
    assert.match(await page.locator('#loading-coverage').innerText(),/Jun 30, 2026/);
    assert.match(await page.locator('#loading-coverage').innerText(),/Unverifiable coverage/);
    assert.match(await page.locator('#loading-coverage').innerText(),/Current within policy/);
    await page.locator('#sector').fill('A different sector');
    // Build 07: the grid keeps every saved sector (what is on this computer) and adds the
    // roster's own sector as a row; its selected cell says nothing is saved for it.
    assert.match(await page.locator('#loading-coverage').innerText(),/A different sector\s+This roster/);
    assert.match(await page.locator('#loading-coverage').innerText(),/Jun 30, 2026/);
    assert.match(await page.locator('#loading-coverage').innerText(),/No saved collection evidence/);
    await page.locator('#sector').fill('');
    const coverageHelp=page.locator('#view-loading .coverage-panel .inline-help');
    assert.equal(await coverageHelp.getAttribute('open'),null);
    assert.match(await page.locator('#loading-coverage').innerText(),/Stale coverage/);
    await coverageHelp.locator('summary').click();
    assert.match(await coverageHelp.innerText(),/more than 30 days/);
    assert.match(await page.locator('#loading-coverage').innerText(),/Unverifiable coverage/);
    await coverageHelp.locator('summary').click();
    await captureScreenshot(process.env.MENTAT_COVERAGE_SCREENSHOT);
    assert.match(await page.locator('#sector').getAttribute('placeholder'),/Credit unions/);
    assert.match(await page.locator('.company-name').getAttribute('placeholder'),/Credit Union/);
    const rosterTemplate=await page.evaluate(()=>fetch('/template.csv').then(response=>response.text()));
    assert.match(rosterTemplate,/Harborlight Credit Union/);
    assert.doesNotMatch(rosterTemplate,/Crypto|Coinbase|Example Company/);
    // Evidence is configurable before Graph has ever loaded. Since S6 the scope
    // bar lists every import and every saved snapshot under one Source select
    // whose values are '', 'csv:<id>' or 'snapshot:<id>' (evidence.js
    // fillSourceSelect). This fixture publishes no imports, so the only real
    // source is the saved snapshot the /api/evidence route serves.
    await page.locator('#tab-evidence').click();
    await page.waitForFunction(()=>[...document.querySelector('#evidence-source').options].some(option=>option.value.startsWith('snapshot:')));
    assert.deepEqual(await page.locator('#evidence-source option').evaluateAll(options=>options.map(option=>option.value)),
      ['','snapshot:fixture-evidence'],'The scope bar offers the saved snapshot by its real id before Graph has loaded.');
    assert.equal(await page.locator('#evidence-source').inputValue(),'','No source is chosen until the reader picks one.');
    // evidence.js initialize() says "Finding imported files and saved snapshots…", awaits the sources and
    // /api/pac-flow-catalog, and only then sayScope()s the counts; the source option appears before that
    // last step, so wait (bounded by the page timeout) for the final status instead of reading it at once.
    await page.waitForFunction(()=>/0 imported files and 1 saved snapshot/.test(document.querySelector('#evidence-status').textContent)).catch(()=>{});
    assert.match(await page.locator('#evidence-status').innerText(),/0 imported files and 1 saved snapshot/);
    // 500,000,001 bytes exceeds the 500 MB cap without a 500 MB write: a sparse
    // file (open + ftruncate) reports the right file.size for the client's
    // pre-flight while NTFS allocates nothing until written.
    await page.locator('#evidence-import-open').click();
    const oversizeDir=fs.mkdtempSync(path.join(os.tmpdir(),'mentat-console-oversize-'));
    const oversizePath=path.join(oversizeDir,'too-large.csv');
    {const fd=fs.openSync(oversizePath,'w');fs.ftruncateSync(fd,500_000_001);fs.closeSync(fd);}
    await page.locator('#evidence-csv-file').setInputFiles(oversizePath);
    assert.match(await page.locator('#evidence-status').innerText(),/too-large\.csv \(500\.0 MB\): Choose a CSV no larger than 500 MB\./);
    fs.rmSync(oversizeDir,{recursive:true,force:true});
    await page.locator('#evidence-import-open').click();
    // Choosing a source fills the cycle and PAC pickers from that source. Since
    // S6 the cycle is carried into the pane rather than narrowing the PAC list:
    // visibleCommittees() filters on #evidence-type and #evidence-find only
    // (evidence.js:158-166), so the old "cycle 2024 leaves only First PAC"
    // expectation no longer describes the product.
    await page.locator('#evidence-source').selectOption('snapshot:fixture-evidence');
    await page.waitForFunction(()=>!document.querySelector('#evidence-cycle').disabled);
    assert.equal(await page.locator('#evidence-cycle').isDisabled(),false);
    assert.deepEqual(await page.locator('#evidence-cycle option').evaluateAll(o=>o.map(x=>x.value)),['','2026','2024']);
    assert.deepEqual(await page.locator('#evidence-pac option').evaluateAll(o=>o.map(x=>x.value)),['C00000001','C00000002']);
    await page.locator('#evidence-cycle').selectOption('2024');
    // The snapshot drawer still seeds the collector from the chosen snapshot.
    await page.locator('#evidence-snapshot-open').click();
    await page.locator('#evidence-use-config').click();
    assert.equal(await page.locator('#evidence-company-input').inputValue(),savedEvidence.company);
    assert.equal(await page.locator('#evidence-cycles-input').inputValue(),'2024, 2026');
    extraSnapshot=true;await page.locator('#evidence-refresh').click();
    await page.waitForFunction(()=>[...document.querySelector('#evidence-source').options].filter(option=>option.value.startsWith('snapshot:')).length===2);
    let evidenceRequest;
    await page.route('**/api/evidence-collect',route=>{evidenceRequest=route.request().postDataJSON();return route.fulfill({json:{id:'new-evidence',state:'collecting'}});});
    await page.route('**/api/evidence-job?*',route=>route.fulfill({json:{state:'completed',message:'Snapshot ready.'}}));
    await page.locator('#evidence-company-input').fill('Another Company');
    await page.locator('#evidence-collect').click();
    await page.waitForFunction(()=>document.querySelector('#evidence-source').value==='snapshot:new-evidence');
    assert.equal(await page.locator('#evidence-source').inputValue(),'snapshot:new-evidence','Collecting selects the snapshot it just wrote.');
    assert.equal(evidenceRequest.company,'Another Company');
    assert.equal(evidenceRequest.committees,'C00000001, C00000002');
    assert.equal(await page.locator('#evidence-collect').isDisabled(),false);
    await captureScreenshot(process.env.MENTAT_EVIDENCE_SCREENSHOT);
    await page.locator('#evidence-source').selectOption('snapshot:fixture-evidence');
    await page.waitForFunction(()=>!document.querySelector('#evidence-pac').disabled);
    await captureScreenshot(process.env.MENTAT_CSV_SCREENSHOT);
    await page.locator('#split-view').click();
    await page.locator('#graph-results').waitFor();
    assert.equal(await page.locator('#view-evidence').isVisible(),true);
    assert.equal(await page.locator('#view-graph').isVisible(),true);
    assert.equal(await page.locator('#view-graph').getAttribute('role'),'region');
    assert.equal(await page.evaluate(()=>document.querySelector('#view-graph').getBoundingClientRect().right<=document.querySelector('#view-evidence').getBoundingClientRect().left),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await captureScreenshot(process.env.MENTAT_SPLIT_SCREENSHOT);
    await page.setViewportSize({width:820,height:1000});
    await page.waitForFunction(()=>document.querySelector('.workspace-tabs').getAttribute('aria-orientation')==='horizontal');
    assert.equal(await page.evaluate(()=>document.querySelector('#view-graph').getBoundingClientRect().right<=document.querySelector('#view-evidence').getBoundingClientRect().left),true,'The 820px in-app browser keeps both split panes side by side.');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await captureScreenshot(process.env.MENTAT_SPLIT_MEDIUM_SCREENSHOT);
    await page.setViewportSize({width:1440,height:1050});
    // First chart open occurs from an evidence-led split, before selecting Graph.
    await page.locator('#graph-open-builder').click();
    await page.locator('#view-chart-studio').waitFor({state:'visible'});
    // Classic builder moved under Export (UI clean-up 2026-09-17, item 14).
    if(!(await page.locator('#studio-download').evaluate(el=>el.open)))await page.locator('#studio-download > summary').click();
    await page.locator('#studio-classic').click();
    await page.locator('#chart-image svg').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('#tab-evidence').click();
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('.workspace-tabs').getAttribute('aria-orientation')==='horizontal');
    assert.equal(await page.evaluate(()=>document.querySelector('#view-evidence').getBoundingClientRect().top>=document.querySelector('#view-graph').getBoundingClientRect().bottom),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await captureScreenshot(process.env.MENTAT_SPLIT_MOBILE_SCREENSHOT);
    await page.setViewportSize({width:1440,height:1050});
    await page.locator('#split-view').click();
    assert.equal(await page.locator('#view-graph').isVisible(),false);
    assert.equal(await page.locator('#view-evidence').getAttribute('role'),'tabpanel');
    await page.locator('#evidence-source').selectOption('snapshot:fixture-evidence');
    await page.locator('#tab-collection').click();
    // renderRunLibrary() (app.js:217-227) sets aria-pressed only after
    // refreshRuns() -> /api/runs?include_archived=1 returns, so reading it
    // straight after the click is a race: green warm, red under load.
    await page.waitForFunction(()=>document.querySelector('[data-run-group=history]')?.getAttribute('aria-pressed')==='true');
    assert.equal(await page.locator('[data-run-group=history]').getAttribute('aria-pressed'),'true');
    await page.locator('[data-run-group=progress]').click();
    assert.equal(await page.locator('#runs').inputValue(),'');
    assert.equal(await page.locator('#monitor').isVisible(),false);
    assert.equal(await page.locator('#stop').isDisabled(),true);
    await page.locator('[data-run-group=history]').click();
    await page.locator('#monitor').waitFor();
    await page.locator('#archive-run').click();
    await page.waitForFunction(()=>document.querySelector('#run-count-archived').textContent==='1'&&document.querySelector('#runs').value==='');
    assert.equal(await page.locator('#monitor').isVisible(),false);
    await page.locator('[data-run-group=archived]').click();
    await page.locator('#monitor').waitFor();
    assert.equal(await page.locator('#archive-run').innerText(),'Restore run');
    await captureScreenshot(process.env.MENTAT_ARCHIVED_SCREENSHOT);
    assert.equal(await page.evaluate(()=>fetch('/api/reports?id=fixture').then(response=>response.json()).then(body=>body.reports.length)),1,'Archiving preserves completed report files.');
    await page.locator('#archive-run').click();
    await page.waitForFunction(()=>document.querySelector('#run-count-archived').textContent==='0');
    await page.locator('[data-run-group=history]').click();
    await page.locator('#monitor').waitFor();
    assert.equal(await page.locator('#runs').inputValue(),'fixture');
    await page.locator('#collection-coverage-details > summary').click();
    assert.match(await page.locator('#collection-coverage').innerText(),/Stale coverage/);
    assert.match(await page.locator('#collection-coverage').innerText(),/Unverifiable coverage/);
    assert.match(await page.locator('#collection-coverage').innerText(),/2026-06-30/);
    await page.locator('#collection-freshness-details > summary').click();
    await page.locator('#report-freshness summary').first().waitFor();
    assert.match(await page.locator('#report-freshness').innerText(),/1\/2 sources with verified/);
    await page.locator('#report-freshness summary').first().click();
    assert.match(await page.locator('#report-freshness').innerText(),/2026-07-31/);
    assert.match(await page.locator('#report-freshness').innerText(),/2026-08-01T00:00:00\+00:00/);
    const collectionRun=await page.evaluate(()=>fetch('/api/run?id=fixture').then(response=>response.json()));
    const readyRun={...structuredClone(collectionRun),state:'ready',status:null,control:{}};
    let simulatedRun=readyRun;
    await page.route('**/api/run?*',route=>route.fulfill({json:simulatedRun}));
    await page.evaluate(run=>render(run),readyRun);
    assert.equal(await page.locator('#monitor').isVisible(),false,'A ready draft does not display an empty collection monitor.');
    assert.equal(await page.locator('#resume-setup').isVisible(),true);
    assert.equal(await page.locator('#run-readiness-note').isVisible(),true);
    assert.match(await page.locator('#run-readiness-note').innerText(),/review|setup|start|collect/i);
    await captureScreenshot(process.env.MENTAT_READY_SCREENSHOT);
    const stoppedRun=structuredClone(collectionRun);
    stoppedRun.state='stopped';stoppedRun.control={stop:true};
    stoppedRun.status={...stoppedRun.status,stage:'collecting',current_industry:'Credit Unions',
      fanout:{total:10,completed:4,running:8,failed:0},
      industries:[{industry:'Credit Unions',stage:'collecting',workers:{active_workers:8,requested_workers:8,
        database_writers:1,request_pool:{queued_requests:4}}}]};
    simulatedRun=stoppedRun;
    await page.evaluate(run=>render(run),stoppedRun);
    // The 3 s setInterval(poll) at app.js:406 refetches the mocked /api/run?*;
    // a response that left the route while simulatedRun was still readyRun can
    // land after render(stoppedRun) and put 'ready' back. Wait for the state to
    // converge, then assert it -- the equality below is still the check.
    await page.waitForFunction(()=>document.querySelector('#stage').textContent==='Stopped');
    assert.equal(await page.locator('#stage').innerText(),'Stopped');
    assert.equal(await page.locator('#stage').getAttribute('data-run-state'),'stopped','B22: the header pill carries the run state.');
    assert.equal(await page.locator('#view-collection .primary--gold:visible').count(),1,'B22: exactly one gold button on the run card.');
    // B22: the worker metrics live in the collapsed Workers disclosure; open it as the operator would.
    await page.evaluate(()=>{document.querySelector('#collection-workers-details').open=true;});
    assert.match(await page.locator('#worker-metric-label').innerText(),/last checkpoint/i);
    assert.equal(await page.locator('#workers').innerText(),'8','Stopped runs preserve the worker count as a historical checkpoint.');
    assert.equal(await page.locator('#stop').isDisabled(),true);
    assert.equal(await page.locator('#apply-workers').isDisabled(),true);
    assert.equal(await page.locator('#worker-target').isDisabled(),true);
    assert.equal(await page.locator('#eta').innerText(),'Stopped');
    assert.equal(await page.locator('#stopping-banner').isVisible(),false);
    await captureScreenshot(process.env.MENTAT_STOPPED_SCREENSHOT);
    await page.unroute('**/api/run?*');
    await page.evaluate(run=>render(run),collectionRun);
    await captureScreenshot(process.env.MENTAT_COLLECTION_SCREENSHOT);
    await page.locator('#tab-cross-reference').click();
    await page.getByText('What this run covers',{exact:true}).waitFor();
    assert.equal(await page.locator('#view-cross-reference').getByText('Observed original giving',{exact:true}).count(),0);
    assert.match(await page.locator('#xref-results').innerText(),/this view publishes no totals/i);
    await captureScreenshot(process.env.MENTAT_CROSS_REFERENCE_SCREENSHOT);
    trace('Loading, Evidence, Collection and Cross-reference checks passed.');
    await page.locator('#tab-graph').click();
    assert.equal(await page.locator('#workspace-title').textContent(),'Graph');
    assert.equal(await page.locator('#graph-builder').getAttribute('open'),null);
    await page.locator('#graph-results').waitFor();
    const graphHash=new URL(page.url()).hash;
    await page.locator('.skip-link').focus();
    await page.keyboard.press('Enter');
    assert.equal(new URL(page.url()).hash,graphHash,'Skipping navigation preserves the selected view URL.');
    assert.equal(await page.locator('#tab-graph').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('#workspace-main').evaluate(element=>element===document.activeElement),true);
    assert.equal(await page.locator('#graph-more').getAttribute('open'),null);
    assert.equal(await page.locator('#graph-run').isVisible(),false);
    await page.locator('#graph-more > summary').click();
    assert.equal(await page.locator('#graph-run').isVisible(),true);
    assert.equal(await page.locator('#graph-run').inputValue(),'published-reports');
    assert.equal(await page.locator('#graph-run option').count(),1);
    await page.locator('#graph-run').focus();
    assert.equal(await page.locator('#graph-run').evaluate(element=>element===document.activeElement),true);
    await page.locator('#graph-more > summary').click();
    assert.equal(await page.locator('#graph-scope').isVisible(),false,'the scope sentence is no longer printed on screen');
    assert.match(await page.locator('#graph-scope').textContent(),/Harborlight|All companies/,'it is still built, for the CSV and the image caption');
    assert.equal(await page.locator('#graph-total').textContent(),'$1,585');
    assert.equal(await page.locator('#graph-direct').textContent(),'$90');
    assert.equal(await page.locator('#graph-tab-overview').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('#graph-organization-panel').isVisible(),false);
    await page.locator('#graph-more > summary').click();
    await page.locator('#graph-open-sources').click();
    assert.match(await page.locator('#graph-freshness').innerText(),/2026-07-31/);
    assert.match(await page.locator('#graph-freshness').innerText(),/Freshness unknown/);
    assert.match(await page.locator('#graph-freshness').innerText(),/Report file modified/);
    await captureScreenshot(process.env.MENTAT_SOURCES_SCREENSHOT,false);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#graph-open-sources').evaluate(e=>e===document.activeElement),true);
    await page.locator('#graph-more > summary').click();
    assert.deepEqual(await page.locator('#graph-tier option').allTextContents(),['All','Chief','Middle-Management','Employees']);
    await page.locator('#graph-company').selectOption('Harborlight Credit Union');
    await page.locator('#graph-tier').selectOption('Chief');
    await page.waitForFunction(()=>document.querySelector('#graph-total').textContent==='$300'&&!document.querySelector('#graph-results').hidden,undefined,GRAPH_QUERY_WAIT);
    assert.match(await page.locator('#graph-scope').textContent(),/Chief/);
    assert.equal(await page.locator('#graph-direct').textContent(),'$100');
    await page.locator('#graph-categories').getByRole('button',{name:'JFC: $200.00',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#graph-total').textContent==='$200'&&!document.querySelector('#graph-results').hidden,undefined,GRAPH_QUERY_WAIT);
    assert.equal(await page.locator('#graph-direct').textContent(),'$0');
    await page.locator('#graph-chips').getByRole('button',{name:'JFC ×'}).click();
    await page.locator('#graph-tier').selectOption('Middle-Management');
    await page.locator('#graph-tab-candidates').click();
    assert.equal(await page.locator('#graph-company').inputValue(),'Harborlight Credit Union');
    assert.equal(await page.locator('#graph-tier').inputValue(),'Middle-Management');
    await page.locator('#graph-mode').selectOption('activity');
    await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden&&document.querySelector('#graph-candidates').textContent.includes('$777.00'),undefined,GRAPH_QUERY_WAIT);
    assert.match(await page.locator('#graph-candidate-note').textContent(),/not money attributed/);
    await page.locator('#graph-direction').selectOption('Oppose');
    await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden&&document.querySelector('#graph-candidates').textContent.includes('$999.00'),undefined,GRAPH_QUERY_WAIT);
    await page.locator('#graph-mode').selectOption('estimates');
    // F4 (lane C, 2026-09-25): an unavailable candidate view gives its reason inside the panel (graph.js paintPanel ->
    // emptyPanel), so the estimates refusal is now the sentence in #graph-candidates and the note under the panel no
    // longer repeats it. The refusal still reaches the reader; only its place moved.
    await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden&&(document.querySelector('#graph-candidates .graph-empty')?.textContent||'').includes('Saved estimates cover the whole industry'),undefined,GRAPH_QUERY_WAIT);
    assert.doesNotMatch(await page.locator('#graph-candidate-note').textContent(),/whole industry/,'the note under the panel does not repeat the refusal');
    assert.equal(await page.locator('#graph-export').isDisabled(),true);
    await page.locator('#graph-tab-overview').click();
    await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden&&document.querySelector('#graph-mode').value==='direct',undefined,GRAPH_QUERY_WAIT);
    assert.equal(await page.locator('#graph-company').inputValue(),'Harborlight Credit Union');
    assert.equal(await page.locator('#graph-tier').inputValue(),'Middle-Management');
    await page.locator('#graph-reset').click();
    await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden&&document.querySelector('#graph-total').textContent==='$1,585',undefined,GRAPH_QUERY_WAIT);
    await page.locator('#graph-candidates').getByRole('button',{name:'Candidate One · D: $90.00',exact:true}).click();
    assert.equal(await page.locator('#graph-candidate-rows tr').count(),2);
    // The view's export tools are in the More menu now (r4 audit
    // AUDIT-MEN-A-1: board M4 has no row under the cards).
    await page.locator('#graph-more > summary').click();
    assert.equal(await page.locator('#graph-method-details > summary').isVisible(),true,'Details & methodology is in the More menu');
    await page.locator('#graph-export-menu > summary').click();
    const guidedDownload=page.waitForEvent('download');
    await page.locator('#graph-export-format').selectOption('csv');
    await page.locator('#graph-export').click();
    const guidedCsv=fs.readFileSync(await (await guidedDownload).path(),'utf8');
    assert.match(guidedCsv,/JFC/);assert.match(guidedCsv,/Report SHA-256/);
    await page.locator('#graph-export-format').selectOption('svg');
    const svgPromise=page.waitForEvent('download');
    await page.locator('#graph-export').click();
    assert.match(fs.readFileSync(await (await svgPromise).path(),'utf8'),/Report|SHA-256/);
    await page.locator('#graph-export-format').selectOption('png');
    const pngPromise=page.waitForEvent('download');
    await page.locator('#graph-export').click();
    const png=fs.readFileSync(await (await pngPromise).path());
    assert.equal(png.subarray(1,4).toString(),'PNG');
    // P3.2: the full table opens IN PLACE. It used to switch the analysis
    // sub-tab as a side effect the label did not name.
    const analysisBefore=await page.evaluate(()=>document.querySelector('.graph-panels').dataset.analysis);
    await page.locator('#graph-full-table').click();
    assert.equal(await page.locator('#graph-categories > details').getAttribute('open'),'');
    assert.equal(await page.evaluate(()=>document.querySelector('.graph-panels').dataset.analysis),
      analysisBefore,'the full table must not move the reader to another analysis');
    assert.equal(await page.locator('[data-values=categories]').getAttribute('aria-expanded'),'true');
    assert.match(await page.locator('[data-values=categories]').textContent(),/^Show top \d/);
    await page.locator('#graph-tab-recipients').click();
    assert.equal(await page.locator('#graph-tab-recipients').getAttribute('aria-selected'),'true');
    await page.locator('#graph-tab-companies').click();
    assert.equal(await page.locator('#graph-level-panel').isVisible(),true);
    assert.match(await page.locator('#graph-levels').textContent(),/Chief/);
    await page.locator('#graph-tab-overview').click();
    await page.locator('#graph-candidate-detail').evaluate(e=>e.hidden=true);
    await captureScreenshot(process.env.MENTAT_GRAPH_SCREENSHOT);
    trace('Guided Graph checks passed; entering mobile navigation checks.');
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('.workspace-tabs').getAttribute('aria-orientation')==='horizontal');
    assert.equal(await page.locator('.workspace-tabs').getAttribute('aria-orientation'),'horizontal');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.locator('#tab-graph').focus();
    await page.keyboard.press('End');
    assert.equal(await page.locator('#tab-public-data').getAttribute('aria-selected'),'true');
    const mobileTabBounds=await page.locator('#tab-public-data').evaluate(element=>{
      const tab=element.getBoundingClientRect(),nav=element.closest('.workspace-tabs').getBoundingClientRect();
      return {tabLeft:tab.left,tabRight:tab.right,navLeft:nav.left,navRight:nav.right};
    });
    // Native scroll positions round fractional button widths to CSS pixels.
    assert.equal(Math.round(mobileTabBounds.tabLeft)>=Math.round(mobileTabBounds.navLeft) && Math.round(mobileTabBounds.tabRight)<=Math.round(mobileTabBounds.navRight),
      true,`Keyboard navigation brings the active mobile tab into view: ${JSON.stringify(mobileTabBounds)}`);
    await page.keyboard.press('Home');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-graph').getAttribute('aria-selected'),'true');
    // Graph redraws its charts once after the tab is shown at this width (3 mutations, then steady), and a label
    // replaced mid-measure has no box; poll until a label on the page is measured, then hold it to the same size rule.
    let mobileChartLabel=null;
    for(const end=Date.now()+15000;!mobileChartLabel&&Date.now()<end;)mobileChartLabel=await page.locator('#graph-categories svg text').first().boundingBox()||await page.waitForTimeout(100).then(()=>null);
    assert.ok(mobileChartLabel && mobileChartLabel.height>=10,'Mobile chart labels retain a readable rendered size.');
    await page.evaluate(()=>window.scrollTo(0,0));
    await captureScreenshot(process.env.MENTAT_MOBILE_SCREENSHOT);
    // At 390 px only the active step keeps its label; that label must fit its own step and never widen the page.
    await page.locator('#tab-loading').click();
    for(const step of [1,2,3]){
      const fit=await page.evaluate(step=>{setLoadingStep(step);const active=document.querySelector('#view-loading .loading-step.is-active');
        return {step,page:Math.max(0,document.documentElement.scrollWidth-innerWidth),label:active.scrollWidth-active.clientWidth};},step);
      assert.deepEqual(fit,{step,page:0,label:0},`Loading stepper at 390 px, step ${step}`);
    }
    await page.evaluate(()=>setLoadingStep(1));
    await page.locator('#tab-graph').click();
    for(const width of [768,1099,1100]) {
      await page.setViewportSize({width,height:900});
      await page.waitForFunction(expected=>document.querySelector('.workspace-tabs').getAttribute('aria-orientation')===expected,width>=1100?'vertical':'horizontal');
      assert.equal(await page.locator('.workspace-tabs').getAttribute('aria-orientation'),width>=1100?'vertical':'horizontal');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`No page overflow at ${width}px.`);
    }
    trace('Responsive layout and mobile navigation checks passed; entering chart builder.');
    await page.setViewportSize({width:1440,height:1050});
    await page.locator('#graph-open-builder').click();
    await page.locator('#view-chart-studio').waitFor({state:'visible'});
    // Classic builder moved under Export (UI clean-up 2026-09-17, item 14).
    if(!(await page.locator('#studio-download').evaluate(el=>el.open)))await page.locator('#studio-download > summary').click();
    await page.locator('#studio-classic').click();
    await page.locator('#chart-image svg').waitFor();
    assert.equal(await page.locator('#graph-advanced').getAttribute('open'),null);
    assert.equal(await page.locator('#chart-dataset').getAttribute('open'),null);
    await page.locator('[data-chart="scatter"]').click();
    assert.equal(await page.locator('#chart-type').inputValue(),'scatter');
    assert.equal(await page.locator('#chart-image circle').count(),4);
    await page.locator('[data-chart="histogram"]').click();
    assert.equal(await page.locator('#chart-error').isVisible(),false);
    assert.equal(await page.locator('#chart-header').inputValue(),'5');
    assert.match(await page.locator('#chart-loading').innerText(),/5 rows · 5 columns/);
    await page.locator('#chart-dataset > summary').click();
    await page.locator('#chart-sheet').selectOption('Regional sample');
    await page.waitForFunction(()=>document.querySelector('#chart-loading').textContent.includes('3 rows · 3 columns'));
    assert.match(await page.locator('#chart-x').textContent(),/Region/);
    await page.locator('#chart-y').selectOption('2');
    await page.waitForFunction(()=>document.querySelector('#chart-title').textContent.startsWith('Cost'));
    await page.locator('[data-chart="bar"]').click();
    assert.match(await page.locator('#chart-values').textContent(),/60/);
    await page.locator('#chart-sheet').selectOption('Candidate Funding');
    await page.waitForFunction(()=>document.querySelector('#chart-loading').textContent.includes('5 rows · 5 columns'));
    await page.locator('#graph-advanced > summary').click();
    await page.locator('#chart-add-filter').click();
    await page.getByLabel('Filter column',{exact:true}).selectOption('0');
    await page.getByLabel('Filter value',{exact:true}).fill('Harborlight Credit Union');
    await page.locator('#chart-type').selectOption('box');
    await page.locator('#chart-create').click();
    assert.match(await page.locator('#chart-scope').innerText(),/2 of 5 rows selected/);
    assert.match(await page.locator('#chart-values').textContent(),/20/);
    assert.equal(await page.locator('#chart-error').isVisible(),false);
    await page.locator('#chart-type').selectOption('pie');
    await page.locator('#chart-aggregation').selectOption('sum');
    await page.locator('#chart-create').click();
    assert.equal(await page.locator('#chart-image circle').count(),1);
    await page.getByRole('button',{name:'Remove filter'}).click();
    await page.locator('#chart-create').click();
    assert.match(await page.locator('#chart-error').innerText(),/nonnegative/);
    assert.equal(await page.locator('#chart-export').isVisible(),false);
    await page.locator('#chart-type').selectOption('bar');
    await page.locator('#chart-create').click();
    assert.equal(await page.locator('#chart-values img').count(),0);
    const downloadPromise=page.waitForEvent('download');
    await page.locator('#chart-save-svg').click();
    const download=await downloadPromise;
    const contents=fs.readFileSync(await download.path(),'utf8');
    assert.match(contents,/<desc>.*SHA-256:/);
    assert.match(contents,/&lt;img/);
    const chartPngPromise=page.waitForEvent('download');
    await page.locator('#chart-save-png').click();
    assert.equal(fs.readFileSync(await (await chartPngPromise).path()).subarray(1,4).toString(),'PNG');
    await page.locator('#chart-type').selectOption('scatter');
    await page.locator('#chart-x').selectOption('1');
    await page.locator('#chart-y').selectOption('2');
    await page.locator('#chart-create').click();
    assert.equal(await page.locator('#chart-image circle').count(),4);
    await page.locator('#chart-type').selectOption('line');
    await page.locator('#chart-x').selectOption('4');
    await page.locator('#chart-create').click();
    assert.equal(await page.locator('#chart-image polyline').count(),1);
    await page.locator('#chart-type').selectOption('histogram');
    await page.locator('#chart-create').click();
    assert.equal(await page.locator('#chart-error').isVisible(),false);
    const dataDownload=page.waitForEvent('download');
    await page.locator('#chart-save-csv').click();
    const chartCsv=fs.readFileSync(await (await dataDownload).path(),'utf8');
    assert.match(chartCsv,/SHA-256:/);assert.match(chartCsv,/Candidate Funding/);
    assert.match(chartCsv,/Numeric bins/);
    await page.locator('#graph-builder').evaluate(e=>e.scrollTop=0);
    await captureScreenshot(process.env.MENTAT_SCREENSHOT);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#chart-create').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth),false);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#graph-builder').isVisible(),false);
    // P3.3c: Classic borrowed the Graph tab for its dialog, and dismissing the
    // dialog used to leave the reader standing there. It hands the tab back.
    await page.waitForFunction(()=>location.hash==='#chart-studio');
    assert.equal(await page.evaluate(()=>location.hash),'#chart-studio');
    await page.locator('#tab-graph').click();
    await page.locator('#graph-tab-overview').click();
    await page.locator('#graph-tab-overview').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#graph-tab-recipients').getAttribute('aria-selected'),'true');
    await page.locator('#tab-graph').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-chart-studio').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-insights').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-cross-reference').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-evidence').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-public-data').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-loading').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowUp');
    assert.equal(await page.locator('#tab-public-data').getAttribute('aria-selected'),'true');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.locator('#tab-loading').getAttribute('aria-selected'),'true');
    await page.keyboard.press('End');
    assert.equal(await page.locator('#tab-public-data').getAttribute('aria-selected'),'true');
    await page.keyboard.press('Home');
    assert.equal(await page.locator('#tab-loading').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('.workspace-tabs [aria-selected=true]').count(),1);
    assert.equal(await page.locator('#tab-loading').evaluate(element=>element===document.activeElement),true);
    assert.deepEqual(errors,[]); assert.deepEqual(remote,[]);
    console.log('Browser checks passed: Graph guided scopes, JFC, levels, direct/downstream separation, exports, Advanced charts, freshness disclosures, responsive sidebar, skip link, keyboard tabs and local requests only.');
  } catch(error) {
    trace(`Failure before cleanup: ${error.stack || error}`);
    throw error;
  } finally {
    trace('Closing browser.');
    if(browser) await browser.close();
    trace('Closing temporary fixture.');
    const exited=once(fixture,'exit'); fixture.stdin.end('\n'); await exited;
    trace('Cleanup complete.');
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
