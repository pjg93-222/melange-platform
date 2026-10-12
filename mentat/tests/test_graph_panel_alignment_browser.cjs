/* Regression: downstream coverage notes must not overlap shared panel footers. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {chromium}=require(require.resolve('playwright',{paths:[process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage(),app=path.resolve(__dirname,'../app');
  const css=['style.css','graph.css','acu-theme.css','workspace.css','workspace-polish.css'].map(file=>fs.readFileSync(path.join(app,file),'utf8')).join('\n');
  const card=(candidate)=>'<section class="panel graph-panel"><div class="panel-heading"><h2>'+ (candidate?'Top candidates':'Giving by recipient type')+'</h2><div style="height:'+(candidate?80:32)+'px">Controls</div></div><div style="height:374px">Chart</div>'+(candidate?'<p class="graph-panel-sub graph-panel-note">'+('Saved observed activity is limited to saved report coverage. '.repeat(5))+'</p>':'')+'<div class="graph-panel-foot">Showing 10 rows · Export</div></section>';
  await page.route('**/*',route=>route.abort());
  await page.setContent('<style>'+css+'</style><div class="graph-panels" data-analysis="overview">'+card(false)+card(true)+card(false)+card(false)+'</div>',{waitUntil:'domcontentloaded',timeout:15000});
  for(const width of [1280,900,620]){
   await page.setViewportSize({width,height:1000});
   const boxes=await page.locator('.graph-panel').evaluateAll(els=>els.map(el=>{const rect=s=>{const r=el.querySelector(s)?.getBoundingClientRect();return r?{top:r.top,bottom:r.bottom}:null;};return {left:el.getBoundingClientRect().left,chart:rect(':scope > div:nth-child(2)'),note:rect('.graph-panel-note'),foot:rect('.graph-panel-foot')};}));
   for(const box of boxes){assert.ok(box.foot.top>=box.chart.bottom-1);if(box.note){assert.ok(box.note.top>=box.chart.bottom-1);assert.ok(box.foot.top>=box.note.bottom-1);}}
   if(boxes[0].left!==boxes[1].left){assert.equal(boxes[0].chart.top,boxes[1].chart.top);assert.equal(boxes[0].foot.top,boxes[1].foot.top);}
  }
  console.log('PASS Graph panel alignment: shared chart baselines, separate coverage notes and footers, desktop and mobile.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
