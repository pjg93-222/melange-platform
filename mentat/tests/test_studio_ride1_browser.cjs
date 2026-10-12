/* Ride 1, steps 17 and 27: the two things a reader hits that the warm-path
   suites never did.

   Step 17. "Open in Chart Studio" can be pressed before this page's Chart
   Studio has its verified data - a reader who lands on Graph first, or reloads
   there. The hand-off is called twice on that path, once eagerly by the click
   and once when the data arrives, and the carried company and cycle have to
   survive the first call.

   Step 27. The briefing drawer opens itself on every add. It is fixed over the
   right edge of the window, so the page has to give it room: without that the
   studio toolbar's right-hand controls sit underneath it and every click on
   them lands on the drawer.

   Run: node --test mentat/tests/test_studio_ride1_browser.cjs */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const CHANNEL=process.env.MENTAT_BROWSER_CHANNEL||'chrome';
const STUDIO_SCRIPTS=['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js',
  'contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'];

// The shipped markup, with its own scripts and links stripped so the fixture
// decides what runs. A label that moves in index.html moves here too.
const shell=()=>fs.readFileSync(path.join(app,'index.html'),'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');

// One offline console. `gateCatalog` holds the verified catalog back until the
// test releases it, which is what puts Chart Studio in the state a reader is in
// when they press the hand-off from Graph before Studio has ever drawn.
async function fixture(page,{gateCatalog=false}={}){
  await page.route('http://ride1.fixture/**',route=>route.fulfill({contentType:'text/html',body:shell()}));
  await page.goto('http://ride1.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css','chart-briefing.css'])
    await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(gate=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(p=>{p.hidden=p.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[],graph:[],briefing:[]};
    window.releaseCatalog=null;
    const choose=(id,pairs,value)=>{const node=document.getElementById(id);
      node.replaceChildren(...pairs.map(([v,label])=>new Option(label,v)));node.value=value;};
    choose('graph-run',[['published-reports','Authoritative fixture reports']],'published-reports');
    choose('graph-industry',[['Banking','Banking']],'Banking');
    choose('graph-company',[['','All companies'],['Alpha Bank','Alpha Bank']],'Alpha Bank');
    choose('graph-cycle',[['','All cycles'],['2024','2023–2024']],'2024');
    choose('graph-tier',[['','All levels']],'');
    choose('graph-funding',[['all','All giving']],'all');
    choose('graph-mode',[['direct','Direct contributions']],'direct');
    choose('graph-direction',[['Support','Support']],'Support');
    const names={company:'Company',cycle:'Election cycle',recipient_category:'Recipient category',
      amount:'Selected amount',records:'Observation count'};
    const measure=id=>['amount','records'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,
      role:measure(id)?'measure':'dimension',type:measure(id)?'number':'category',
      is_time:id==='cycle',unit:id==='amount'?'cents':measure(id)?'count':undefined}));
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
    const catalog={datasets:[{id:'Banking',label:'Banking',available:true,fields,
      bases:[{id:'receipt',label:'receipt'},{id:'original_observed',label:'original_observed'}],provenance}],fields};
    const held=gate?new Promise(resolve=>{window.releaseCatalog=resolve;}):Promise.resolve();
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:false});
      if(url.endsWith('/catalog')){await held;return respond(catalog);}
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
          totals:{amount:total,records:kept.reduce((n,r)=>n+r.records,0),contributors:kept.length},
          comparison:{receipt:total,original_observed:total,original_supported:total,
            unresolved_gross_exposure:0,unresolved_records:0},
          group_count:grouped.length,offset:body.offset||0,limit:body.limit,
          has_more:(body.offset||0)+body.limit<grouped.length,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:false,conserved:true},
          provenance,notices:[],verified_unique:false});
      }
      if(url.startsWith('/api/studio/briefing'))return respond({items:[]});
      throw Error('Unexpected endpoint '+url);
    };
    window.api=async(url,payload)=>{
      if(url.startsWith('/api/graph?')){
        const params=new URLSearchParams(url.slice(url.indexOf('?')+1));
        calls.graph.push(Object.fromEntries(params));
        return {filters:Object.fromEntries(params),reports:[{name:'Banking.xlsx',sha256:WORKBOOK}],
          totals:{giving:12090412,direct:0,organizations:0,unresolved_candidate_links:0,recipients:1,receipt_groups:2}};
      }
      if(url==='/api/studio/briefing/add'){
        calls.briefing.push(payload);
        return {id:'f'.repeat(32),added_at:new Date().toISOString()};
      }
      if(url.startsWith('/api/studio/briefing'))return {items:[]};
      throw Error('Unexpected api call '+url);
    };
    window.MentatExport={download:()=>{},image:async()=>{},
      compose:(svg,title,scope)=>({svg,width:960,height:540}),
      rasterize:async()=>new Blob(),copy:async()=>{}};
  },gateCatalog);
  for(const file of STUDIO_SCRIPTS)await page.addScriptTag({path:path.join(app,file)});
}

test('a Graph scope carried before Chart Studio has its data is still carried',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:CHANNEL});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await fixture(page,{gateCatalog:true});

  // The state a reader is in when they come from Graph: the studio has asked
  // for its catalog and is still waiting, so it has no data to filter yet.
  await page.waitForFunction(()=>typeof window.releaseCatalog==='function');
  assert.equal(await page.evaluate(()=>!!document.querySelector('#studio-canvas svg')),false,
    'the fixture must press the hand-off before Chart Studio has drawn, or it tests the warm path');

  await page.evaluate(()=>document.getElementById('graph-open-builder').click());
  await page.evaluate(()=>window.releaseCatalog());

  // Both pills, not one and not none: the company and the cycle both survived
  // the eager hand-off that runs before the data lands.
  await page.waitForFunction(()=>document.querySelectorAll('#studio-filters .studio-pill').length===2);
  const pills=await page.$$eval('#studio-filters .studio-pill',nodes=>nodes.map(n=>n.textContent.replace(/\s+/g,' ').trim()));
  assert.ok(pills.some(text=>/Company = Alpha Bank/.test(text)),'the carried company is a filter pill: '+JSON.stringify(pills));
  assert.ok(pills.some(text=>/Election cycle = 2023-2024/.test(text)),'the carried cycle is a filter pill: '+JSON.stringify(pills));

  // And the chip says where the scope came from, naming both.
  // The header scope is a chip line since the 2026-09-17 UI clean-up (item 13):
  // the same parts, one <span> each, so they are read as a list rather than as
  // one sentence with separators in its text.
  const chip=await page.locator('#studio-scope-chip span').allTextContents();
  assert.deepEqual(chip,['Carried from Graph','Banking','2023-2024','Alpha Bank']);

  // The query the console was actually asked is the carried one, and the strip
  // agrees with Graph to the cent.
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).filters),
    [{field:'company',op:'eq',value:'Alpha Bank'},{field:'cycle',op:'eq',value:'2023-2024'}]);
  // Build 03 (S1): the explanation sits behind "Why" (#studio-reconcile-detail)
  // and the note line hides when the scopes and totals agree.
  await page.waitForFunction(()=>/Agree\./.test(document.getElementById('studio-reconcile-detail').textContent));
  assert.match(await page.locator('#studio-reconcile-detail').textContent(),
    /^Graph shows \$12,090,412\.00 for Alpha Bank · 2023-2024\. Studio shows \$12,090,412\.00\. Agree\./);
  assert.equal(await page.evaluate(()=>document.getElementById('studio-reconcile').hidden),true);
  assert.deepEqual(errors,[]);
});

test('the open briefing drawer never covers the studio toolbar controls',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:CHANNEL});
  t.after(()=>browser.close());
  const errors=[];
  // Every width a reader works at, including the one where the Inspector is a
  // drawer of its own and the toolbar is at its most crowded.
  for(const width of [1920,1440,1280,1100,960]){
    const page=await browser.newPage({viewport:{width,height:1000}});
    page.setDefaultTimeout(20000);
    page.on('pageerror',e=>errors.push(width+': '+e.message));
    await fixture(page);
    for(const file of ['chart-export.js','chart-briefing.js'])await page.addScriptTag({path:path.join(app,file)});
    await page.waitForFunction(()=>!!document.querySelector('#studio-canvas svg'));

    // A reader adds one chart. The drawer opens itself; that is the point of it.
    await page.locator('#studio-briefing-add').click();
    await page.waitForFunction(()=>!document.getElementById('studio-briefing').hidden);
    assert.equal(await page.locator('#studio-briefing-badge').textContent(),'1',width+'px: the badge counts the chart');

    const covered=await page.evaluate(()=>{
      const drawer=document.getElementById('studio-briefing');
      return ['studio-classic','studio-overview-toggle','studio-inspector-toggle','studio-briefing-add','studio-download']
        .map(id=>{
          const el=document.getElementById(id);
          if(!el||!el.offsetParent)return null;
          const box=el.getBoundingClientRect();
          const top=document.elementFromPoint(Math.round(box.x+box.width/2),Math.round(box.y+box.height/2));
          return top&&drawer.contains(top)?id:null;
        }).filter(Boolean);
    });
    assert.deepEqual(covered,[],width+'px: the drawer is sitting on top of '+covered.join(', '));

    // The reader can actually press the one it used to swallow. It opens the
    // classic dialog as a modal, which makes the rest of the page inert, so
    // that dialog is closed again before the drawer is.
    // Classic builder moved under Export (UI clean-up 2026-09-17, item 14).
    if(!(await page.locator('#studio-download').evaluate(el=>el.open)))await page.locator('#studio-download > summary').click();
    await page.locator('#studio-classic').click({timeout:5000});
    await page.waitForFunction(()=>document.getElementById('graph-builder').open);
    await page.evaluate(()=>document.getElementById('graph-builder').close());
    await page.locator('#studio-briefing-close').click();
    assert.equal(await page.evaluate(()=>document.body.classList.contains('studio-briefing-open')),false,
      width+'px: closing the drawer gives the gutter back');
    await page.close();
  }
  assert.deepEqual(errors,[]);
});
