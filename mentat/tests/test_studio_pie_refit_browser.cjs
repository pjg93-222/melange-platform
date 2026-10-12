/* Chart Studio pie refit (finding 6, 2026-09-25). A pie whose chart box grows with it (a window under 1100px:
   the box has no height of its own) already scales with that box's width through CSS, so it is drawn again only
   for a change wider than a classic 15-17px scrollbar. Before, a page scrollbar that came and went with the pie's
   own height redrew it every 150ms: width W, scrollbar, W-17, redraw, scrollbar gone, W, redraw again.
   A bounded pie (a wider window, the box's own height) keeps the 4px step.
   Offline fixture: the golden index.html with its scripts stripped, the app's own scripts in the console's order,
   and a verified server that answers any grouping (test_studio_r5_malleable_browser.cjs's). The scrollbar is
   stood in for by a 17px narrower window: Playwright's headless Chrome hides scrollbars. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const SCRIPTS=['theme.js','ui-state.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js',
  'contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'];
const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');

async function boot(context){
  const page=await context.newPage();
  page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://studio-refit.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://studio-refit.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css','workspace-polish.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';document.body.dataset.view='chart-studio';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const members={company:['Alpha','Beta','Gamma'],cycle:['2021-2022','2023-2024','2025-2026'],party:['DEM','REP','IND','LIB','GRE']};
    const names={company:'Company',cycle:'Election cycle',party:'Recipient party',amount:'Selected amount',records:'Observation count',contributors:'Distinct reported donor identities'};
    const isMeasure=id=>['amount','records','contributors'].includes(id);
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:isMeasure(id)?'measure':'dimension',
      type:isMeasure(id)?'number':'category',unit:id==='amount'?'cents':isMeasure(id)?'count':undefined}));
    const provenance={workbook:'Banking.xlsx',workbook_sha256:'w'.repeat(64),database_sha256:'d'.repeat(64),index_token:'index-1',
      query_engine_sha256:'q'.repeat(64),field_semantics_sha256:'s'.repeat(64),scope:'Offline fixture'};
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,bases:[{id:'receipt',label:'receipt'}],provenance}],fields});
      if(url.endsWith('/query')){
        let tuples=[{}];
        for(const id of body.dimensions){const list=members[id]||['A','B'];tuples=tuples.flatMap(t=>list.map(v=>({...t,[id]:v})));}
        const rows=tuples.map((t,seed)=>Object.assign({},t,...body.measures.map(id=>({[id]:{amount:100000*(seed+1),records:seed+2,contributors:seed+1}[id]}))));
        return respond({status:'ready',rows:rows.slice(body.offset||0,(body.offset||0)+body.limit),
          columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)||{id,name:id,role:'measure',type:'number'}),
          totals:{amount:1000000,records:30,contributors:12},comparison:{receipt:1000000},group_count:rows.length,
          offset:body.offset||0,limit:body.limit,has_more:false,
          reconciliation:{receipt_matches_workbook:true,aggregate_filters_applied:false,conserved:true},provenance,notices:[],verified_unique:false});
      }
      if(url.endsWith('/records'))return respond({status:'ready',rows:[],total_records:0,has_more:false,provenance});
      throw Error('Unexpected endpoint '+url);
    };
    window.api=async url=>url==='/api/report-sources'?{runs:[{id:'published-reports',name:'Fixture reports'}]}:url.startsWith('/api/reports?')?{reports:[]}:{sheets:[]};
    window.MentatExport={download:()=>{},image:async()=>{}};
  });
  for(const file of SCRIPTS)await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg'));
  return {page,errors};
}
const settled=async page=>{await page.waitForTimeout(120);await page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');await page.waitForTimeout(150);};
// The Studio's own restore path puts a saved arrangement in place (the same one a reload uses).
async function arrange(page,sheets){
  await page.evaluate(sheets=>{
    const STORE='mentat-chart-studio-v1',store=JSON.parse(localStorage.getItem(STORE)||'{}');
    const key=Object.keys(store).find(k=>k.startsWith('contributions|Banking|'));
    store[key]={sheets,active:0,board:null};
    const persist=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k===STORE)return;return persist.call(this,k,v);};
    persist.call(localStorage,STORE,JSON.stringify(store));
    document.getElementById('studio-dataset').dispatchEvent(new Event('change'));
    Storage.prototype.setItem=persist;
  },sheets);
  await settled(page);
}
// Catalog order in the fixture: contributions.fields() numbers each field by its position.
const FIELD_INDEX={company:0,cycle:1,party:2,amount:3,records:4,contributors:5};
const pieSheet={id:'s1',name:'Sheet s1',columns:[{column:FIELD_INDEX.party,aggregation:'sum'}],rows:[{column:FIELD_INDEX.amount,aggregation:'sum'}],
  marks:{type:'pie',color:null,size:null,label:null,detail:[],tooltip:[]},filters:[],pages:null,pageMember:'',
  analytics:{transform:'none',period:'',horizon:2,totals:false,average:false,median:false},types:{},params:{start:0,end:0,limit:0,subject:'',maxGroups:0,splitMulti:false},
  sort:'none',showLabels:true,fit:'standard',source:{}};
// The drawn pie's box (svg.__fit, charts.js) and the canvas's content width now, measured as pieFit measures it.
const state=page=>page.evaluate(()=>{
  const canvas=document.getElementById('studio-canvas'),svg=canvas.querySelector(':scope > svg[data-fit]'),style=getComputedStyle(canvas);
  const px=name=>parseFloat(style[name])||0;
  const width=Math.floor((style.overflowY==='scroll'?canvas.clientWidth:canvas.offsetWidth-px('borderLeftWidth')-px('borderRightWidth'))-px('paddingLeft')-px('paddingRight'));
  return {draws:window.pieDraws,box:svg&&svg.__fit?svg.__fit.box:null,width};
});
const WAIT=450;   // past the refit's 150ms settle, with room for a redraw to land

test('An unbounded pie is not redrawn by a scrollbar coming and going; a real resize, and a bounded pie\'s 4px step, still redraw it',{timeout:180000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width:1000,height:800}});
  const {page,errors}=await boot(context);
  await arrange(page,[pieSheet]);
  await page.waitForFunction(()=>{const svg=document.querySelector('#studio-canvas > svg[data-fit]');return svg&&svg.__fit;});
  // Every fitted pie put on the canvas from here on is one draw.
  await page.evaluate(()=>{window.pieDraws=0;new MutationObserver(list=>{for(const m of list)for(const n of m.addedNodes)if(n.nodeType===1&&n.matches('svg[data-fit]'))window.pieDraws++;})
    .observe(document.getElementById('studio-canvas'),{childList:true});});
  const start=await state(page);
  assert.equal(start.box.height,null,'under 1100px the pie\'s box grows with it');
  assert.equal(start.box.width,start.width,'drawn at the canvas\'s width');

  // A classic 17px scrollbar appears and goes three times, the resize settling each time.
  const widths=[];
  for(let i=0;i<3;i++){
    await page.setViewportSize({width:983,height:800});await page.waitForTimeout(WAIT);widths.push((await state(page)).width);
    await page.setViewportSize({width:1000,height:800});await page.waitForTimeout(WAIT);widths.push((await state(page)).width);
  }
  for(const w of widths){const moved=Math.abs(w-start.width);assert.ok(moved===0||(moved>=12&&moved<24),'the box moved by a scrollbar\'s width or not at all: '+moved);}
  assert.ok(widths.some(w=>start.width-w>=12),'the box did narrow by a scrollbar\'s width');
  assert.equal((await state(page)).draws,0,'a scrollbar coming and going does not redraw an unbounded pie');

  // A real change of the box (60px) draws it again at its new width.
  await page.setViewportSize({width:940,height:800});
  await page.waitForFunction(()=>window.pieDraws>=1);
  await page.waitForTimeout(WAIT);
  const narrower=await state(page);
  assert.equal(narrower.box.width,narrower.width,'redrawn at the new width');
  assert.equal(narrower.box.height,null);

  // A wider window bounds the pie by its box's height, and there a 6px change still redraws it.
  await page.setViewportSize({width:1440,height:900});
  await page.waitForFunction(()=>{const svg=document.querySelector('#studio-canvas > svg[data-fit]');return svg&&svg.__fit&&svg.__fit.box.height!=null;});
  await page.waitForTimeout(WAIT);
  const bounded=await state(page);
  await page.setViewportSize({width:1434,height:900});
  await page.waitForFunction(n=>window.pieDraws>n,bounded.draws);
  await page.waitForTimeout(WAIT);
  const after=await state(page);
  assert.ok(Math.abs(after.width-bounded.width)>=4&&Math.abs(after.width-bounded.width)<24,'a small change of a bounded box: '+(after.width-bounded.width));
  assert.equal(after.box.width,after.width,'a bounded pie keeps the 4px step');
  assert.deepEqual(errors,[]);
});
