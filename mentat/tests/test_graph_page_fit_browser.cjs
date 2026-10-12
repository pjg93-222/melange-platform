'use strict';
/* r4 audit AUDIT-MEN-A-1: at 1440x900 the default Overview's
   document.scrollingElement.scrollHeight was 118px more than the four cards
   account for. The difference was a row of its own under the cards -
   "Details & methodology", Copy image, Add to briefing and the view's Export -
   that board M4 never drew. That row now lives in the More menu, so nothing
   of Graph's is laid out below the cards, and the tools are still one click
   away. Offline: the golden index.html with a fixture /api/graph. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('nothing of Graph is laid out under its cards; its details and exports are in More',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  for(const [width,height] of [[1440,900],[1920,1080]]){
    const page=await browser.newPage({viewport:{width,height}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>route.abort());
    await page.setContent(fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
    for(const sheet of ['style.css','graph.css','workspace.css'])await page.addStyleTag({path:path.join(app,sheet)});
    await page.addScriptTag({path:path.join(app,'theme.js')});
    await page.evaluate(()=>{
      window.currentView='graph';window.splitView=false;window.selectedRun='fixture';
      document.querySelector('#view-loading').hidden=true;document.querySelector('#view-graph').hidden=false;
      // app.js shows Graph's action row (and its More menu) on the Graph view.
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
    const fit=await page.evaluate(()=>{
      const panels=[...document.querySelectorAll('#view-graph .graph-panel')].filter(panel=>!panel.hidden&&panel.offsetParent);
      const bottom=Math.max(...panels.map(panel=>panel.getBoundingClientRect().bottom));
      // The workspace ends where the cards end, plus its own bottom padding.
      // (The audit measured document.scrollingElement.scrollHeight on the live
      // console, where the shell's footer is fixed in the sidebar; this page
      // loads Graph's stylesheets only, so the shell footer follows the
      // workspace here and is not Graph's to measure.)
      const workspace=document.querySelector('.workspace');
      const pad=parseFloat(getComputedStyle(workspace).paddingBottom)||0;
      const end=workspace.getBoundingClientRect().bottom;
      const tools=document.querySelector('#view-graph .graph-details-tools');
      return {cards:panels.length,below:Math.round(end-bottom-pad),inMore:Boolean(tools&&tools.closest('#graph-more'))};
    });
    assert.equal(fit.cards,4,`${width}: the four Overview cards`);
    assert.ok(fit.below<=1,`${width}: nothing of Graph is laid out under the cards (${fit.below}px)`);
    assert.equal(fit.inMore,true,'the details and exports sit in the More menu');
    for(const id of ['#graph-method-details > summary','#graph-copy-image','#graph-briefing','#graph-export-menu > summary'])
      assert.equal(await page.locator(id).isVisible(),false,'closed More shows none of them: '+id);
    // One click away: More opens on them, and Details & methodology still
    // carries the view's own note.
    await page.locator('#graph-more > summary').click();
    await page.locator('#graph-method-details > summary').click();
    assert.equal(await page.locator('#graph-method').textContent(),'Synthetic methodology note for the fixture.');
    assert.equal(await page.locator('#graph-method').isVisible(),true);
    for(const id of ['#graph-copy-image','#graph-briefing','#graph-export-menu > summary'])
      assert.equal(await page.locator(id).isVisible(),true,id+' is reachable in More');
    // The menu sits over the page, not in it: opening it moves no card.
    const moved=await page.evaluate(()=>Math.round(document.querySelector('.workspace').getBoundingClientRect().bottom
      -(parseFloat(getComputedStyle(document.querySelector('.workspace')).paddingBottom)||0)
      -Math.max(...[...document.querySelectorAll('#view-graph .graph-panel')].filter(panel=>!panel.hidden&&panel.offsetParent).map(panel=>panel.getBoundingClientRect().bottom))));
    assert.ok(moved<=1,`${width}: an open More does not add a row under the cards (${moved}px)`);
    // A card's Full table closes More rather than leave it over the table.
    await page.locator('#graph-export-menu > summary').click();
    await page.locator('#graph-export-target').selectOption('companies');
    await page.locator('#graph-full-table').click();
    assert.equal(await page.locator('#graph-more').evaluate(element=>element.open),false);
    await page.waitForFunction(()=>document.querySelectorAll('#graph-companies tbody tr').length===10);
    assert.deepEqual(errors,[]);
    await page.close();
  }
});
