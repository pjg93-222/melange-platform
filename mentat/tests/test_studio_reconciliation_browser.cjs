/* F0 and F4: a scope carried from Graph arrives as the cycle spelling the
   verified index stores, the reconciliation strip states both totals and either
   agrees or names the reason, and the chip says what was left behind. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('a carried Graph scope reconciles to the cent, and names the reason when it cannot',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://reconcile.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://reconcile.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[],graph:[]};
    // The Graph controls a reader would have set before pressing the hand-off.
    const choose=(id,pairs,value)=>{const node=document.getElementById(id);
      node.replaceChildren(...pairs.map(([v,label])=>new Option(label,v)));node.value=value;};
    choose('graph-run',[['published-reports','Authoritative fixture reports']],'published-reports');
    choose('graph-industry',[['Banking','Banking']],'Banking');
    choose('graph-company',[['','All companies'],['Alpha Bank','Alpha Bank']],'Alpha Bank');
    // Graph names a cycle by the year it ends in; the index stores the pair.
    choose('graph-cycle',[['','All cycles'],['2024','2023–2024']],'2024');
    choose('graph-tier',[['','All levels'],['Chief','Chief']],'');
    choose('graph-funding',[['all','All giving'],['personal','Personal giving']],'all');
    choose('graph-mode',[['direct','Direct contributions']],'direct');
    choose('graph-direction',[['Support','Support']],'Support');

    const names={company:'Company',cycle:'Election cycle',recipient_category:'Recipient category',
      amount:'Selected amount',records:'Observation count'};
    const measure=id=>['amount','records'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:measure(id)?'measure':'dimension',
      type:measure(id)?'number':'category',is_time:id==='cycle',unit:id==='amount'?'cents':measure(id)?'count':undefined}));
    // Alpha Bank in 2023-2024 is $12,090,412.00 of included receipts, exactly.
    const observations=[
      {company:'Alpha Bank',cycle:'2023-2024',recipient_category:'Company PAC',amount:809041200,records:3},
      {company:'Alpha Bank',cycle:'2023-2024',recipient_category:'Candidate',amount:400000000,records:2},
      {company:'Beta Bank',cycle:'2023-2024',recipient_category:'Company PAC',amount:250000000,records:1},
      {company:'Alpha Bank',cycle:'2021-2022',recipient_category:'Candidate',amount:100000000,records:1}
    ];
    const WORKBOOK='w'.repeat(64);
    const provenance={workbook:'Banking.xlsx',workbook_sha256:WORKBOOK,database_sha256:'d'.repeat(64),
      index_token:'index-1',query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,
        bases:[{id:'receipt',label:'receipt'},{id:'original_observed',label:'original_observed'}],provenance}],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        const kept=observations.filter(row=>(body.filters||[]).every(f=>
          f.op==='in'?f.value.includes(row[f.field]):f.op==='ne'?row[f.field]!==f.value:row[f.field]===f.value));
        const groups=new Map();
        for(const row of kept){
          const key=body.dimensions.map(id=>row[id]).join('|');
          const seen=groups.get(key)||Object.assign({},...body.dimensions.map(id=>({[id]:row[id]})),{amount:0,records:0});
          seen.amount+=row.amount;seen.records+=row.records;groups.set(key,seen);
        }
        const grouped=[...groups.values()].sort((a,b)=>b.amount-a.amount);
        const total=kept.reduce((sum,row)=>sum+row.amount,0);
        return respond({status:'ready',rows:grouped.slice(body.offset||0,(body.offset||0)+body.limit),
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)).filter(Boolean),
          totals:{amount:total,records:kept.reduce((n,row)=>n+row.records,0),contributors:kept.length},
          comparison:{receipt:total,original_observed:total,original_supported:total,
            unresolved_gross_exposure:0,unresolved_records:0},
          group_count:grouped.length,offset:body.offset||0,limit:body.limit,
          has_more:(body.offset||0)+body.limit<grouped.length,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:!!(body.aggregate_filters||[]).length,conserved:true},
          provenance,notices:[],verified_unique:false});
      }
      throw Error('Unexpected endpoint '+url);
    };
    // Graph answers in dollars, as graph_data.money() does.
    window.api=async url=>{
      if(url.startsWith('/api/graph?')){
        const params=new URLSearchParams(url.slice(url.indexOf('?')+1));
        calls.graph.push(Object.fromEntries(params));
        const giving=params.get('tier')?9000000:12090412;
        return {filters:Object.fromEntries(params),reports:[{name:'Banking.xlsx',sha256:WORKBOOK}],
          totals:{giving,direct:0,organizations:0,unresolved_candidate_links:0,recipients:1,receipt_groups:2}};
      }
      throw Error('Unexpected api call '+url);
    };
    window.MentatExport={download:()=>{},image:async()=>{}};
  });
  for(const file of ['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'])await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg'));

  // The hand-off: Graph's end-year cycle becomes the pair the index stores.
  await page.evaluate(()=>document.getElementById('graph-open-builder').click());
  await page.waitForFunction(()=>document.querySelectorAll('#studio-filters .studio-pill').length===2);
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).filters),
    [{field:'company',op:'eq',value:'Alpha Bank'},{field:'cycle',op:'eq',value:'2023-2024'}]);
  // Build 03 (S1): the strip is a note line; its full explanation sits behind
  // "Why" (#studio-reconcile-detail), and the line hides when the scopes and
  // the totals agree.
  await page.waitForFunction(()=>/Agree\./.test(document.getElementById('studio-reconcile-detail').textContent));
  const agreed=await page.locator('#studio-reconcile-detail').textContent();
  assert.match(agreed,/^Graph shows \$12,090,412\.00 for Alpha Bank · 2023-2024\. Studio shows \$12,090,412\.00\. Agree\./);
  assert.equal(await page.evaluate(()=>document.getElementById('studio-reconcile').hidden),true,'the note line hides when scope and totals agree');
  assert.equal(await page.locator('#studio-reconcile .studio-reconcile-agree').textContent(),'Agree.');
  assert.equal(await page.locator('#studio-reconcile .studio-reconcile-link').textContent(),'Why Cross-reference differs');
  // The strip asked Graph the question the studio is showing, not Graph's own.
  assert.deepEqual(await page.evaluate(()=>{const last=calls.graph.at(-1);
    return {industry:last.industry,company:last.company,cycle:last.cycle,tier:last.tier,funding:last.funding};}),
    {industry:'Banking',company:'Alpha Bank',cycle:'2024',tier:'',funding:'all'});
  // Nothing was left behind, so nothing is claimed to have been.
  assert.equal(await page.evaluate(()=>document.getElementById('studio-scope-note').hidden),true);
  // The header scope is a chip line since the 2026-09-17 UI clean-up: the same
  // parts, one <span> each, so they are read as a list rather than as one
  // sentence whose separators never reach textContent.
  assert.deepEqual(await page.locator('#studio-scope-chip span').allTextContents(),
    ['Carried from Graph','Banking','2023-2024','Alpha Bank']);
  // Totals cover every group, not only the page that is drawn.
  assert.match(await page.locator('#studio-group-count').textContent(),/^Groups 1–1 of 1$/);
  assert.equal(await page.locator('#studio-group-count').getAttribute('title'),'Totals cover all 1 groups');

  // An employee-level filter has no verified field, so the two totals part and
  // the strip says which filter did it.
  await page.evaluate(()=>{document.getElementById('graph-tier').value='Chief';
    document.getElementById('graph-open-builder').click();});
  await page.waitForFunction(()=>/They differ by/.test(document.getElementById('studio-reconcile').textContent));
  const differed=await page.locator('#studio-reconcile').textContent();
  assert.match(differed,/Graph shows \$9,000,000\.00 for Alpha Bank · 2023-2024\. Studio shows \$12,090,412\.00\./);
  assert.match(differed,/They differ by \$3,090,412\.00 because a Graph filter \(employee level or giving type\) is not carried\./);
  // The note line names the level Chart Studio cannot match, offers no Match
  // (nothing it could change), and prints no red.
  assert.equal(await page.evaluate(()=>document.getElementById('studio-reconcile').hidden),false);
  assert.equal(await page.locator('#studio-reconcile-text').textContent(),'Graph also narrows to the Chief employee level, which verified data cannot match.');
  assert.equal(await page.evaluate(()=>document.getElementById('studio-match-graph').hidden),true);
  assert.equal(await page.locator('#studio-reconcile .studio-reconcile-differ').count(),0,'no red verdict in the builder');
  await page.waitForFunction(()=>!document.getElementById('studio-scope-note').hidden);
  assert.equal(await page.locator('#studio-scope-note').textContent(),
    'Employee level: not carried (not in verified data)');

  // Match Graph scope (S1): Graph now shows every company while the sheet is
  // narrowed to Alpha Bank; the note says so, and Match takes the sheet to
  // Graph's scope (the cycle stays, the company filter goes).
  await page.evaluate(()=>{document.getElementById('graph-tier').value='';document.getElementById('graph-company').value='';
    document.getElementById('studio-fit').dispatchEvent(new Event('change'));});
  await page.waitForFunction(()=>!document.getElementById('studio-match-graph').hidden);
  assert.equal(await page.locator('#studio-reconcile-text').textContent(),'This sheet covers Alpha Bank. Graph shows all companies.');
  // Build 03 final fix round (R03-6): Escape closes Why while focus is still
  // on the Why button, where a click leaves it.
  await page.locator('#studio-reconcile-why').click();
  assert.equal(await page.evaluate(()=>document.getElementById('studio-reconcile-detail').hidden),false);
  await page.keyboard.press('Escape');
  assert.deepEqual(await page.evaluate(()=>[document.getElementById('studio-reconcile-detail').hidden,document.activeElement.id]),[true,'studio-reconcile-why']);
  await page.locator('#studio-match-graph').click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-filters .studio-pill').length===1);
  await page.waitForFunction(()=>/Same scope as Graph/.test(document.getElementById('studio-reconcile-text').textContent));
  assert.deepEqual(await page.evaluate(()=>calls.query.filter(q=>q.measures.includes('amount')).at(-1).filters),[{field:'cycle',op:'eq',value:'2023-2024'}]);
  assert.equal(await page.evaluate(()=>document.getElementById('studio-match-graph').hidden),true,'nothing left to match');
  await page.evaluate(()=>{document.getElementById('graph-company').value='Alpha Bank';});

  // A basis that is not the workbook's included receipts is a different
  // question, and the strip refuses to call two numbers the same answer.
  await page.evaluate(()=>{document.getElementById('graph-tier').value='';
    document.getElementById('graph-open-builder').click();});
  await page.waitForFunction(()=>/Agree\./.test(document.getElementById('studio-reconcile').textContent));
  await page.locator('#studio-scope-change').click();
  await page.locator('#studio-basis').selectOption('original_observed');
  await page.waitForFunction(()=>/uses a different basis than Graph/.test(document.getElementById('studio-reconcile').textContent));
  assert.match(await page.locator('#studio-reconcile-detail').textContent(),/^Graph shows .+ Studio shows .+ They differ by .+ because this sheet uses a different basis than Graph\./);
  assert.match(await page.locator('#studio-reconcile-text').textContent(),/^Same scope as Graph; the totals differ by \$/);

  // A worksheet view has no verified total to reconcile, so the strip goes away.
  await page.locator('#studio-source-mode').selectOption('worksheet');
  await page.waitForFunction(()=>document.getElementById('studio-reconcile').hidden);
  assert.deepEqual(errors,[]);
});
