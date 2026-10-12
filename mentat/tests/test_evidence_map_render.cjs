/* Real boundary performance and rendering checks, without collection or live UI state. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(require.resolve('playwright', {paths:[process.env.MENTAT_NODE_MODULES ||
  path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app=path.resolve(__dirname,'../app');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    await page.setContent('<div id="evidence-map"><div class="map-canvas-host" id="host"></div></div>');
    await page.addStyleTag({path:path.join(app,'acu-theme.css')});
    await page.addStyleTag({path:path.join(app,'workspace.css')});
    await page.addScriptTag({path:path.join(app,'evidence-map.js')});
    const shapes=JSON.parse(fs.readFileSync(path.join(app,'geo/cd119.json')));
    const points=JSON.parse(fs.readFileSync(path.join(app,'geo/zcta_points.json')));
    const bundle=JSON.parse(fs.readFileSync(path.join(app,'geo/sld/lower/TX.json')));
    const crosswalk=JSON.parse(fs.readFileSync(path.join(app,'geo/zcta_sld.json')));
    const result=await page.evaluate(async ({shapes,points,bundle,crosswalk})=>{
      const host=document.getElementById('host'), api=window.MentatEvidenceMap;
      const body={boundary_file:'cd119.json',by_zip:Object.keys(points.points).filter((_,i)=>i%11===0).slice(0,3000)
        .map((zip,i)=>({zip,amount:i+1,contributors:2})),kinds:[],by_state:[],by_region:[]};
      const view={body,shapes,points,layer:'zip',direction:'raised',off:new Set()};
      api.render(host,view);
      const path=host.querySelector('.map-district');
      const start=performance.now();
      for(let i=0;i<20;i++)api.render(host,{...view,focus:{kind:'district',key:i%2?'KY-06':'IL-07'}});
      const selectionMs=(performance.now()-start)/20;
      const retained=path===host.querySelector('.map-district');
      api.zoom(host,10);
      await new Promise(resolve=>requestAnimationFrame(resolve));
      // Flush the settled view through the public restore operation so the
      // sample includes viewport culling and the zoomed clustering level.
      api.restore(host,api.view(host));
      const before=api.frames(host).length;
      for(let i=0;i<30;i++){
        api.zoom(host,i%2?1/1.1:1.1);
        await new Promise(resolve=>requestAnimationFrame(resolve));
      }
      const frames=api.frames(host).slice(before).sort((a,b)=>a-b);
      const timing={selectionMs,retained,medianMs:frames[Math.floor(frames.length/2)],maxMs:Math.max(...frames),
        frameCount:frames.length,sourceZipCount:body.by_zip.length,circles:api.circleCount(host)};
      const spent={boundary_file:'cd119.json',kinds:[{kind:'candidates',districts:[{key:'KY-06',amount:30},{key:'IL-SEN',amount:100}]},
        {kind:'operating',states:[{key:'IL',amount:1000}]}],
        by_state:[{key:'KY',amount:30,kinds:{candidates:30}},{key:'IL',amount:1100,kinds:{candidates:100,operating:1000}}]};
      const spendView={body:spent,shapes,layer:'district',direction:'spent',off:new Set()};
      api.render(host,spendView);
      api.restore(host,{scale:1,x:0,y:0});
      api.render(host,spendView);
      timing.districtLegend=host.querySelector('.map-legend-range').textContent;
      timing.senateBase=host.querySelector('.map-state-base[data-key="IL"]').style.fill;
      api.render(host,{...spendView,layer:'state'});
      timing.stateLegend=host.querySelector('.map-legend-range').textContent;
      const fullBody={...body,by_zip:Object.keys(points.points).map((zip,i)=>({zip,amount:i+1,contributors:2,rows:2}))};
      api.render(host,{...view,body:fullBody});
      api.restore(host,{scale:1,x:0,y:0});
      timing.fullSource=fullBody.by_zip.length;
      timing.fullCircles=api.circleCount(host);
      timing.circleBudget=api.MAX_CIRCLES;
      const sldRows=api.legislativeRows(fullBody,bundle,crosswalk,'lower');
      const sldView={...view,body:fullBody,layer:'sld',sldBundle:bundle,sldRows,chamber:'lower'};
      api.render(host,sldView);
      timing.nationalSld=host.querySelectorAll('.map-sld').length;
      const box=bundle.districts.find(d=>d.id==='48001').bbox;
      api.restore(host,api.flyToTransform(box,975,610,40));
      timing.zoomedSld=host.querySelectorAll('.map-sld').length;
      const selected=host.querySelector('.map-sld'), selectedKey=selected.dataset.key;
      api.render(host,{...sldView,focus:{kind:'sld',key:selectedKey}});
      timing.sldRetained=selected===host.querySelector(`.map-sld[data-key="${selectedKey}"]`);
      timing.sldChosen=selected.classList.contains('chosen');
      api.render(host,{...sldView,direction:'spent',body:spent,sldRows:[]});
      timing.spentSldLegend=host.querySelector('.map-legend-range').textContent;
      // A national outlier must stop flattening Texas once Texas is selected.
      const focusBody={boundary_file:'cd119.json',kinds:[{kind:'candidates',districts:[
        {key:'TX-01',amount:100},{key:'TX-02',amount:500},{key:'CA-01',amount:1000000}]}]};
      const focusView={body:focusBody,shapes,layer:'district',direction:'spent',off:new Set()};
      api.render(host,focusView); api.restore(host,{scale:1,x:0,y:0});
      const texas=host.querySelector('.map-district[data-key="TX-02"]');
      const nationalFill=texas.style.fill;
      timing.nationalHeat=host.querySelector('.map-legend-range').textContent;
      api.render(host,{...focusView,focus:{kind:'state',key:'TX',label:'Texas'}});
      timing.texasHeat=host.querySelector('.map-legend-range').textContent;
      timing.texasHeatRetained=texas===host.querySelector('.map-district[data-key="TX-02"]');
      timing.texasRecoloured=nationalFill!==texas.style.fill && texas.style.fill==='rgb(50, 122, 122)';
      // No selection: zoom/pan alone must also refresh colors and legend.
      api.render(host,focusView);
      api.restore(host,api.flyToTransform(shapes.states.TX.bbox,975,610,40));
      timing.viewportHeat=host.querySelector('.map-legend-range').textContent;
      const beforeWheel=timing.viewportHeat;
      api.zoom(host,40);
      await new Promise(resolve=>setTimeout(resolve,220));
      timing.wheelHeat=host.querySelector('.map-legend-range').textContent;
      timing.wheelRescaled=timing.wheelHeat!==beforeWheel;
      api.restore(host,{scale:1,x:0,y:0});
      timing.restoredHeat=host.querySelector('.map-legend-range').textContent;
      return timing;
    },{shapes,points,bundle,crosswalk});
    console.log(JSON.stringify(result));
    assert.equal(result.retained,true,'selection must preserve polygon nodes');
    assert.ok(result.sourceZipCount>2500,'performance measurement requires thousands of source ZIPs');
    assert.ok(result.circles>0 && result.circles<result.sourceZipCount,'zoomed rendering must retain visible points and cull offscreen points');
    assert.equal(result.frameCount,30,'measure completed animation frames, not queued zoom calls');
    assert.ok(Number.isFinite(result.medianMs) && Number.isFinite(result.maxMs),'timings must include real transform updates');
    assert.match(result.districtLegend,/\$30 – \$30/);
    assert.match(result.stateLegend,/\$30 – \$1,100/);
    assert.equal(result.senateBase,'rgb(232, 243, 241)','Senate dollars must not color the House layer');
    assert.ok(result.selectionMs<50,'selection should reuse geometry');
    assert.ok(result.medianMs<25,'zoom median exceeds the 25ms regression budget');
    assert.ok(result.fullSource>33000 && result.fullCircles<=result.circleBudget,'full national ZIP set must respect the marker ceiling');
    assert.equal(result.nationalSld,150);
    assert.ok(result.zoomedSld>0 && result.zoomedSld<result.nationalSld,'offscreen legislative paths must be culled');
    assert.ok(result.sldRetained && result.sldChosen,'selecting a legislative district must retain its node');
    assert.match(result.spentSldLegend,/No positive amount/,'spending must not be guessed from receipt geography');
    assert.match(result.nationalHeat,/\$100 – \$1,000,000/);
    assert.match(result.texasHeat,/\$100 – \$500 · Texas/);
    assert.ok(result.texasHeatRetained && result.texasRecoloured,'focus must rescale fills without replacing polygons');
    assert.match(result.viewportHeat,/\$100 – \$500/);
    assert.ok(result.wheelRescaled,'settled wheel zoom must recompute the visible heat domain');
    assert.match(result.restoredHeat,/\$100 – \$1,000,000/);
    console.log('PASS real-boundary rendering, rescaling, House/state scope and performance');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
