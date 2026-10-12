'use strict';
/* Paul, 2026-09-23 (Graph): "You should be able to expand each section
   individually like with a census panel". Every Graph card folds on its own
   heading triangle, open by default, remembered per section across a reload,
   and the card that shared a folded card's row takes the whole row - its chart
   is relaid at the new width. Offline: the page is served from a fixture
   origin so the fold memory (localStorage) is real, and /api/graph is a stub. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const ORIGIN='http://graph-folds.fixture';

async function boot(page) {
  await page.addStyleTag({path:path.join(app,'style.css')});
  await page.addStyleTag({path:path.join(app,'graph.css')});
  await page.addStyleTag({path:path.join(app,'workspace.css')});
  await page.addScriptTag({path:path.join(app,'theme.js')});
  await page.addScriptTag({path:path.join(app,'ui-state.js')});
  await page.evaluate(()=>{
    window.currentView='graph';window.splitView=false;window.selectedRun='fixture';
    document.querySelector('#view-loading').hidden=true;document.querySelector('#view-graph').hidden=false;
    const rows=(prefix,count,step)=>Array.from({length:count},(_,i)=>({id:prefix+i,label:prefix+' '+(i+1),amount:(count-i)*step}));
    const body={options:{companies:['Fixture Company'],cycles:['2024'],categories:['Super PAC'],recipients:[{id:'C00000001',label:'Fixture PAC'}]},
      filters:{company:'',cycle:'2024',tier:'',funding:'all',mode:'direct',direction:'Support'},
      totals:{giving:100,direct:40,organizations:60,unresolved_candidate_links:0,recipients:1,receipt_groups:1},
      organizations:[{id:'C00000001',label:'Fixture PAC',amount:60}],
      categories:rows('Category',6,10),companies:rows('Company',4,10),
      levels:[{id:'Employees',label:'Employees',amount:60},{id:'Chief',label:'Chief',amount:40}],
      levels_by_company:[],
      candidates:rows('Candidate',8,5),candidate_available:true,candidate_note:'',
      note:'Synthetic test data only.',reports:[{name:'Fixture workbook.xlsx',sha256:'a'.repeat(64)}],
      direction_totals:[{direction:'Contribution',candidates:2,amount:30,transaction_types:['24K']},
        {direction:'Other',candidates:1,amount:5,transaction_types:['24C']},
        {direction:'Support',candidates:1,amount:20},{direction:'Oppose',candidates:0,amount:0},
        {direction:'Contribution / Other',candidates:0,amount:0},{direction:'Unknown',candidates:0,amount:0}],
      pac_candidate_relationships:[],downstream_edges:[],downstream_sources:[],measurement_comparisons:[]};
    body.candidates_by_direction={Contribution:[{id:'H00000001',label:'Fixture Candidate',amount:20},{id:'H00000002',label:'Second Candidate',amount:10}],
      Other:[{id:'H00000001',label:'Fixture Candidate',amount:5}],Support:[{id:'H00000003',label:'Third Candidate',amount:20}],
      Oppose:[],'Contribution / Other':[],Unknown:[]};
    window.api=async url=>url.startsWith('/api/report-sources')?{runs:[{id:'fixture',name:'Offline fixture',state:'completed'}]}:{industries:[{name:'Synthetic'}]};
    window.fetch=async url=>({ok:true,json:async()=>String(url).includes('mode=activity')
      ?Object.assign({},body,{candidates:body.candidates_by_direction.Contribution}):body});
  });
  await page.addScriptTag({path:path.join(app,'graph-explorer.js')});
  await page.addScriptTag({path:path.join(app,'graph.js')});
  await page.waitForSelector('#graph-categories .graph-bar-button',{state:'attached'});
}
const box=(page,selector)=>page.locator(selector).evaluate(node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};});
const viewWidth=(page,selector)=>page.locator(selector).evaluate(node=>Number((node.getAttribute('viewBox')||'0 0 0 0').split(/\s+/)[2]));
const stored=page=>page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(k=>k.startsWith('mentat-graph-folded:')).map(k=>[k,localStorage.getItem(k)])));

test('every Graph card folds on its own heading, is remembered, and gives its row to the card left open',{timeout:90000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route(ORIGIN+'/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto(ORIGIN+'/');
  await page.evaluate(()=>localStorage.clear());
  await boot(page);

  // Open by default, the board's look: a triangle per card, in the card's
  // left padding, so the title has not moved.
  const cards=['category','candidate','company','level'];
  for(const name of cards){
    const fold=page.locator(`#graph-${name}-panel > .panel-heading > .graph-fold`);
    assert.equal(await fold.getAttribute('aria-expanded'),'true',name);
    assert.equal(await fold.getAttribute('aria-controls'),`graph-${name}-panel`);
    const card=await box(page,`#graph-${name}-panel`),button=await box(page,`#graph-${name}-panel > .panel-heading > .graph-fold`);
    const title=await box(page,`#graph-${name}-panel > .panel-heading > h2`);
    assert.ok(button.x>=card.x&&button.x+button.w<=title.x+0.5,`${name}: the triangle sits in the padding (${button.x}..${button.x+button.w}, title at ${title.x})`);
    const inset=await page.locator(`#graph-${name}-panel`).evaluate(node=>{const s=getComputedStyle(node);return parseFloat(s.paddingLeft)+parseFloat(s.borderLeftWidth);});
    assert.ok(Math.abs(title.x-card.x-inset)<1,`${name}: the title keeps its place (${title.x-card.x}px in, padding ${inset})`);
  }
  // Its accessible name is the card's own title.
  await page.getByRole('button',{name:'Giving by recipient type',exact:true}).waitFor();
  assert.deepEqual(await stored(page),{},'nothing is stored until a triangle is pressed');

  // Fold "Giving by recipient type": its heading line stays, the chart and the
  // tools go, and Top candidates takes the whole row and is redrawn wider.
  const half=await box(page,'#graph-candidate-panel');
  const halfView=await viewWidth(page,'#graph-candidates svg.graph-bars');
  await page.getByRole('button',{name:'Giving by recipient type',exact:true}).click();
  assert.equal(await page.locator('#graph-category-panel > .panel-heading > .graph-fold').getAttribute('aria-expanded'),'false');
  assert.equal(await page.locator('#graph-categories').isVisible(),false);
  assert.equal(await page.locator('#graph-category-panel .graph-panel-tools').isVisible(),false);
  assert.equal(await page.locator('#graph-category-panel > .panel-heading > h2').isVisible(),true);
  const strip=await box(page,'#graph-category-panel');
  assert.ok(strip.h<50,`the folded card is one line, ${strip.h}px`);
  const whole=await box(page,'#graph-candidate-panel');
  assert.ok(whole.w>half.w*1.8,`Top candidates takes the row: ${half.w} -> ${whole.w}`);
  // Relaid at the new width (the drawing is clamped at 920 and scales past it).
  await page.waitForFunction(width=>Number((document.querySelector('#graph-candidates svg.graph-bars').getAttribute('viewBox')||'').split(/\s+/)[2])>width,halfView);
  const chart=await box(page,'#graph-candidates svg.graph-bars');
  assert.ok(chart.w>half.w*1.6,`the chart itself is wider: ${chart.w}px in a ${whole.w}px card (was a ${half.w}px card)`);
  // Drawn at the row's width, past the 920 a card in its own column stops
  // at, and not scaled: the drawing's own width is the width it is shown at.
  assert.ok(await viewWidth(page,'#graph-candidates svg.graph-bars')>920);
  assert.ok(Math.abs(await viewWidth(page,'#graph-candidates svg.graph-bars')-chart.w)<2,'drawn at the width it is shown at');
  assert.equal(await page.locator('#graph-candidate-panel').evaluate(node=>node.classList.contains('is-widened')),true);
  assert.deepEqual(await stored(page),{'mentat-graph-folded:graph-category-panel':'1'});

  // Fold "Giving by employee level" from the keyboard: Company comparison takes
  // its row.
  const companyHalf=await box(page,'#graph-company-panel');
  await page.locator('#graph-level-panel > .panel-heading > .graph-fold').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#graph-level-panel > .panel-heading > .graph-fold').getAttribute('aria-expanded'),'false');
  assert.ok((await box(page,'#graph-company-panel')).w>companyHalf.w*1.8);
  assert.equal(await page.locator('#graph-candidate-panel > .panel-heading > .graph-fold').getAttribute('aria-expanded'),'true',
    'each section is its own: the others stay open');

  // Remembered across a reload, per section.
  await page.reload();
  await boot(page);
  for(const [name,open] of [['category',false],['candidate',true],['company',true],['level',false]]){
    assert.equal(await page.locator(`#graph-${name}-panel > .panel-heading > .graph-fold`).getAttribute('aria-expanded'),String(open),name);
    assert.equal(await page.locator(`#graph-${name}-panel`).evaluate(node=>node.classList.contains('is-folded')),!open,name);
  }
  assert.ok((await box(page,'#graph-candidate-panel')).w>half.w*1.8,'the open card still has the row after the reload');

  // The whole header line is the control, as the census panel's is: the
  // title unfolds its card, and the partner goes back to its half.
  await page.locator('#graph-category-panel > .panel-heading > h2').click();
  assert.equal(await page.locator('#graph-category-panel > .panel-heading > .graph-fold').getAttribute('aria-expanded'),'true');
  assert.equal(await page.locator('#graph-categories').isVisible(),true);
  assert.ok(Math.abs((await box(page,'#graph-candidate-panel')).w-half.w)<2,'Top candidates is back in its half');
  await page.waitForFunction(()=>!document.getElementById('graph-candidate-panel').classList.contains('is-widened'));
  assert.ok(await viewWidth(page,'#graph-candidates svg.graph-bars')<=920,'and drawn at its half again');
  assert.equal((await stored(page))['mentat-graph-folded:graph-category-panel'],'0');

  // Recipients with the drawer open: folding Top organizations gives the
  // drawer the row and its bars are relaid wider; folding the drawer keeps its
  // heading line, with the close mark, at the top of the row.
  await page.locator('#graph-tab-recipients').click();
  await page.locator('#graph-organizations .graph-bar-button').first().click();
  await page.waitForSelector('#graph-drawer-panel .graph-drawer-side');
  const drawerHalf=await box(page,'#graph-drawer-panel');
  const drawerView=await viewWidth(page,'#graph-drawer-rows svg.graph-bars');
  await page.locator('#graph-organization-panel > .panel-heading > .graph-fold').click();
  assert.ok((await box(page,'#graph-drawer-panel')).w>drawerHalf.w*1.6,'the drawer takes the row');
  await page.waitForFunction(width=>Number((document.querySelector('#graph-drawer-rows svg.graph-bars').getAttribute('viewBox')||'').split(/\s+/)[2])>width,drawerView);
  await page.locator('#graph-organization-panel > .panel-heading > .graph-fold').click();
  await page.getByRole('button',{name:/Where this PAC/}).click();
  assert.equal(await page.locator('#graph-drawer-directions').isVisible(),false);
  assert.equal(await page.locator('#graph-drawer-close').isVisible(),true,'the close mark stays on the folded line');
  const organizations=await box(page,'#graph-organization-panel'),folded=await box(page,'#graph-drawer-panel');
  assert.ok(folded.h<50&&folded.y<organizations.y,'the folded drawer is one line above the list');
  assert.ok(organizations.w>drawerHalf.w*1.6,'Top organizations takes the row');
  assert.equal((await stored(page))['mentat-graph-folded:graph-drawer-panel'],'1');
  assert.deepEqual(errors,[]);
  if(process.env.MENTAT_FOLDS_SCREENSHOT){
    fs.mkdirSync(path.dirname(process.env.MENTAT_FOLDS_SCREENSHOT),{recursive:true});
    await page.screenshot({path:process.env.MENTAT_FOLDS_SCREENSHOT,fullPage:true});
  }
});
