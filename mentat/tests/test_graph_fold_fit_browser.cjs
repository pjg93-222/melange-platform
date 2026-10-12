'use strict';
/* r5 merge, audit AUDIT-MEN-A-1 (men-graph open item 3): with the row under
   the cards gone, the default Overview still scrolled 14px at 1440x900 and
   10px at 1600x900 on the live console, because the shared shell keeps 40px
   of workspace padding under the cards. Board M4 fits the fold. On Graph, as
   on Chart Studio (chart-studio.css, board S1), that padding is the room left
   under the cards at 1100px and wider: 23px. The split view (Graph beside PAC
   evidence) and every other view keep the shared 40px, and the unscrolled
   page does not move. Offline: the golden index.html with every stylesheet it
   links and a fixture /api/graph of Top-10 cards. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

function page_html(){
  return fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
}
function sheets(){
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8');
  return [...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="\/([^"?#]+)"/gi)].map(match=>match[1]);
}

async function drawGraph(browser,width,height){
  const page=await browser.newPage({viewport:{width,height}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.setContent(page_html());
  const linked=sheets();
  assert.ok(linked.includes('graph.css')&&linked.includes('workspace-polish.css'),'index.html links graph.css and workspace-polish.css');
  for(const sheet of linked)await page.addStyleTag({path:path.join(app,sheet)});
  await page.addScriptTag({path:path.join(app,'theme.js')});
  await page.evaluate(()=>{
    window.currentView='graph';window.splitView=false;window.selectedRun='fixture';
    // What app.js showView('graph') sets.
    document.body.dataset.view='graph';
    for(const view of document.querySelectorAll('main .workspace > [id^="view-"]'))view.hidden=view.id!=='view-graph';
    document.getElementById('graph-actions').hidden=false;
    const rows=(prefix,count)=>Array.from({length:count},(_,i)=>({id:prefix+i,label:prefix+' '+i,amount:(count-i)*10}));
    const body={options:{companies:['Fixture Company'],cycles:['2026'],categories:[],recipients:[]},
      filters:{company:'',cycle:'2026',tier:'',funding:'all',mode:'direct',direction:'Support',category:'',recipient:''},
      totals:{giving:1000,direct:200,organizations:800,unresolved_candidate_links:0,recipients:10,receipt_groups:40},
      categories:rows('Category',10),organizations:rows('C0000000',10),companies:rows('Company',10),
      candidates:rows('H000000',10).map(row=>Object.assign(row,{party:'D'})),
      levels:[{id:'Employees',label:'Employees',amount:600},{id:'Middle-Management',label:'Middle-Management',amount:300},{id:'Chief',label:'Chief',amount:100}],
      levels_by_company:[],candidate_available:true,candidate_note:'Fixture.',candidate_details:[],
      note:'Synthetic methodology note for the fixture.',reports:[{name:'Fixture workbook.xlsx',sha256:'a'.repeat(64)}]};
    window.api=async url=>url.startsWith('/api/report-sources')?{runs:[{id:'fixture',name:'Offline fixture',state:'completed'}]}:{industries:[{name:'Synthetic'}]};
    window.fetch=async()=>({ok:true,json:async()=>body});
  });
  await page.addScriptTag({path:path.join(app,'graph-explorer.js')});
  await page.addScriptTag({path:path.join(app,'graph.js')});
  await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden&&document.querySelectorAll('#graph-companies .graph-bar-button').length>0,undefined,{timeout:30000});
  return {page,errors};
}

const measure=page=>page.evaluate(()=>{
  const workspace=document.querySelector('main .workspace');
  const panels=[...document.querySelectorAll('#view-graph .graph-panel')].filter(panel=>!panel.hidden&&panel.offsetParent);
  return {pad:getComputedStyle(workspace).paddingBottom,cards:panels.length,
    cardsBottom:Math.round(Math.max(...panels.map(panel=>panel.getBoundingClientRect().bottom))),
    scrollHeight:document.scrollingElement.scrollHeight,innerHeight};
});

test('Graph keeps 23px under its cards at 1100px and wider, so the Overview ends at the fold as board M4 does',{timeout:180000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  for(const [width,height] of [[1440,900],[1600,900],[1920,1080]]){
    const {page,errors}=await drawGraph(browser,width,height);
    const fit=await measure(page);
    assert.equal(fit.cards,4,`${width}: the four Overview cards`);
    assert.equal(fit.pad,'23px',`${width}: Graph's workspace keeps 23px under the cards`);
    // The page ends where the cards end plus that padding, inside the fold.
    assert.ok(fit.cardsBottom+23<=fit.innerHeight,`${width}x${height}: the cards and their padding fit the fold (${fit.cardsBottom}+23 > ${fit.innerHeight})`);
    assert.equal(fit.scrollHeight,fit.innerHeight,`${width}x${height}: the Overview does not scroll (${fit.scrollHeight} > ${fit.innerHeight})`);
    // The unscrolled page is the same page: only the room below the fold changed.
    // (Layout, not pixels: the cards redraw on resize with sub-pixel antialiasing.)
    const layout=()=>page.evaluate(()=>[...document.querySelectorAll('main .workspace *, .app-navigation, .app-navigation *')]
      .filter(element=>element.getClientRects().length).map(element=>{const r=element.getBoundingClientRect();
        return [element.tagName,element.id,Math.round(r.x*2)/2,Math.round(r.y*2)/2,Math.round(r.width*2)/2,Math.round(r.height*2)/2].join(' ');}));
    // The bars grow in on first draw; take the layout once it has settled.
    let before=await layout();
    for(let tries=0;tries<40;tries++){await page.waitForTimeout(250);const now=await layout();if(JSON.stringify(now)===JSON.stringify(before))break;before=now;}
    await page.addStyleTag({content:'body[data-view="graph"] main .workspace:not(.split-workspace){padding-bottom:40px!important}'});
    const shared=await measure(page);
    assert.equal(shared.pad,'40px');
    assert.equal(shared.cardsBottom,fit.cardsBottom,`${width}: the padding moves no card`);
    assert.deepEqual(await layout(),before,`${width}: nothing on the page moves with the shared padding`);
    // Negative control: with the shared 40px the same cards scroll past a 900px fold.
    if(height===900)assert.ok(shared.scrollHeight>shared.innerHeight,`${width}x${height}: the shared 40px would scroll (${shared.scrollHeight})`);
    assert.deepEqual(errors,[]);
    await page.close();
  }
});

test('the split view and every other view keep the shared 40px, and narrow Graph keeps its own',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const {page,errors}=await drawGraph(browser,1440,900);
  await page.evaluate(()=>document.querySelector('main .workspace').classList.add('split-workspace'));
  assert.equal((await measure(page)).pad,'40px','Graph beside PAC evidence keeps the shared padding');
  await page.evaluate(()=>document.querySelector('main .workspace').classList.remove('split-workspace'));
  for(const view of ['loading','collection','cross-reference','evidence','insights']){
    await page.evaluate(view=>{document.body.dataset.view=view;},view);
    assert.equal((await measure(page)).pad,'40px',`${view} keeps the shared padding`);
  }
  await page.evaluate(()=>{document.body.dataset.view='graph';});
  assert.equal((await measure(page)).pad,'23px');
  await page.setViewportSize({width:1000,height:900});
  const narrow=await page.evaluate(()=>getComputedStyle(document.querySelector('main .workspace')).paddingBottom);
  await page.evaluate(()=>{document.body.dataset.view='loading';});
  const shared=await page.evaluate(()=>getComputedStyle(document.querySelector('main .workspace')).paddingBottom);
  assert.equal(narrow,shared,'below 1100px Graph keeps the shared padding');
  assert.deepEqual(errors,[]);
  await page.close();
});
