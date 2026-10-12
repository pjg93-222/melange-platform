/* Exercise downloaded bytes and rasterized pixels, not just serialized strings. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('SVG and PNG exports preserve zoomed geography, clip its viewport and retain legends',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());const page=await browser.newPage();
  await page.setContent('<main id="fixture"></main>');
  await page.evaluate(()=>{
    window.exportBlobs=[];window.exportNames=[];
    const create=URL.createObjectURL.bind(URL);URL.createObjectURL=blob=>{exportBlobs.push(blob);return create(blob);};
    HTMLAnchorElement.prototype.click=function(){exportNames.push(this.download);};
    window.raster=async blob=>{
      const url=create(blob),img=new Image();
      try{
        await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;img.src=url;});
        const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;
        const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);
        return {width:canvas.width,height:canvas.height,pixel:(x,y)=>Array.from(ctx.getImageData(x,y,1,1).data)};
      }finally{URL.revokeObjectURL(url);}
    };
  });
  for(const script of ['chart-export.js','contribution-plot.js'])await page.addScriptTag({path:path.join(app,script)});
  const zoom=await page.evaluate(async()=>{
    fixture.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="640" viewBox="200 100 400 256" style="background:#e7eef0"><rect x="210" y="110" width="20" height="20" fill="#ff0000"/><rect x="200" y="360" width="400" height="100" fill="#0000ff"/></svg>';
    await MentatExport.image(fixture.firstChild,'svg','Zoomed ZIP amounts','Receipt basis; source fixture; pinned Census 119th Congress','zoom');
    const blob=exportBlobs.at(-1),content=await blob.text(),doc=new DOMParser().parseFromString(content,'image/svg+xml'),viewport=doc.documentElement.querySelector('svg'),rendered=await raster(blob);
    return {viewBox:viewport.getAttribute('viewBox'),overflow:viewport.getAttribute('overflow'),width:rendered.width,height:rendered.height,mark:rendered.pixel(35,35),background:rendered.pixel(1,1),footer:rendered.pixel(850,660),text:doc.documentElement.textContent,original:fixture.firstChild.getAttribute('viewBox')};
  });
  assert.equal(zoom.viewBox,'200 100 400 256');assert.equal(zoom.overflow,'hidden');assert.equal(zoom.original,zoom.viewBox);
  assert.equal(zoom.width,1000);assert.equal(zoom.height,709);
  assert.deepEqual(zoom.mark,[255,0,0,255],'a zoomed mark must remain in the displayed location');
  assert.deepEqual(zoom.background,[231,238,240,255]);
  assert.deepEqual(zoom.footer,[255,255,255,255],'off-viewport geography must not cover the caption');
  assert.match(zoom.text,/Zoomed ZIP amounts/);assert.match(zoom.text,/pinned Census 119th Congress/);

  const png=await page.evaluate(async()=>{
    await MentatExport.image(fixture.firstChild,'png','Zoomed ZIP amounts','Receipt basis','zoom');
    const blob=exportBlobs.at(-1),rendered=await raster(blob);
    return {type:blob.type,size:blob.size,width:rendered.width,height:rendered.height,mark:rendered.pixel(70,70),footer:rendered.pixel(1700,1320),name:exportNames.at(-1)};
  });
  assert.equal(png.type,'image/png');assert.ok(png.size>1000);assert.equal(png.name,'zoom.png');
  assert.equal(png.width,2000);assert.equal(png.height,1418);assert.deepEqual(png.mark,[255,0,0,255]);assert.deepEqual(png.footer,[255,255,255,255]);

  const legacy=await page.evaluate(async()=>{
    fixture.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="400" viewBox="0 0 1000 400"><rect x="200" y="100" width="20" height="20" fill="#00ff00"/></svg>';
    await MentatExport.image(fixture.firstChild,'svg','Existing chart','Original worksheet scope','legacy');
    const blob=exportBlobs.at(-1),doc=new DOMParser().parseFromString(await blob.text(),'image/svg+xml'),rendered=await raster(blob);
    return {viewBox:doc.documentElement.querySelector('svg').getAttribute('viewBox'),width:rendered.width,height:rendered.height,mark:rendered.pixel(205,105),text:doc.documentElement.textContent,name:exportNames.at(-1)};
  });
  assert.equal(legacy.viewBox,'0 0 1000 400');assert.equal(legacy.width,1000);assert.equal(legacy.height,469);assert.deepEqual(legacy.mark,[0,255,0,255]);
  assert.match(legacy.text,/Existing chart/);assert.match(legacy.text,/Original worksheet scope/);assert.equal(legacy.name,'legacy.svg');

  const legend=await page.evaluate(async()=>{
    const long='Long company '+ 'name '.repeat(50);
    const rows=[{year:'2022',company:'Alpha',amount:100},{year:'2023',company:'Alpha',amount:200},{year:'2022',company:long,amount:300}];
    const svg=MentatContributionPlot.render(fixture,rows,{type:'line',dimensions:['year','company'],measures:['amount'],columns:[{id:'amount',name:'Net amount',unit:'cents'}]});
    const group=svg.querySelector('[data-legend="series"]');
    await MentatExport.image(svg,'svg','Company timeline','Source fixture','legend');
    const content=await exportBlobs.at(-1).text(),doc=new DOMParser().parseFromString(content,'image/svg+xml'),output=doc.querySelector('[data-legend="series"]');
    return {parent:group.parentNode===svg,text:output.textContent,lines:output.querySelectorAll('text').length,height:svg.viewBox.baseVal.height,maxY:Math.max(...[...output.querySelectorAll('text')].map(n=>Number(n.getAttribute('y')))),colors:[...output.querySelectorAll('rect')].map(n=>n.getAttribute('fill'))};
  });
  assert.equal(legend.parent,true);assert.match(legend.text,/Alpha/);assert.match(legend.text,/Long company/);assert.equal((legend.text.match(/name/g)||[]).length,50);
  assert.ok(legend.lines>=4);assert.ok(legend.height>legend.maxY);assert.equal(legend.colors.length,2);assert.notEqual(legend.colors[0],legend.colors[1]);
  const tall=await page.evaluate(async()=>{
    fixture.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="50000" viewBox="0 0 1000 50000"><rect width="1000" height="50000" fill="#00ff00"/></svg>';
    await MentatExport.image(fixture.firstChild,'png','Long chart','All displayed groups','tall');
    const blob=exportBlobs.at(-1),rendered=await raster(blob);return {width:rendered.width,height:rendered.height,size:blob.size,pixel:rendered.pixel(10,10)};
  });
  assert.ok(tall.height<=16384&&tall.height>16000);assert.ok(tall.width>300);assert.ok(tall.width*tall.height<=32000000);assert.ok(tall.size>1000);assert.deepEqual(tall.pixel,[0,255,0,255]);

  // E0 split compose()/rasterize(): the pieces must produce exactly what the
  // download produces, and neither may reach for an anchor of its own - the
  // clipboard, the print sheet and the slide all call them without one.
  const split=await page.evaluate(async()=>{
    fixture.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300"><rect x="10" y="10" width="40" height="40" fill="#123456"/></svg>';
    const before=exportNames.length;
    const composed=MentatExport.compose(fixture.firstChild,'Composed once','Receipt basis; source fixture');
    const text=new XMLSerializer().serializeToString(composed.svg);
    const blob=await MentatExport.rasterize(composed,{scale:1});
    const bare=MentatExport.compose(fixture.firstChild,'Composed once','Receipt basis; source fixture',{caption:false});
    const quiet=exportNames.length-before;
    await MentatExport.image(fixture.firstChild,'svg','Composed once','Receipt basis; source fixture','composed');
    return {quiet,type:blob.type,size:blob.size,width:composed.width,height:composed.height,
      bareHeight:bare.height,bareText:bare.svg.textContent,
      same:(await exportBlobs.at(-1).text())===text,name:exportNames.at(-1)};
  });
  assert.equal(split.quiet,0,'compose and rasterize must not download anything on their own');
  assert.equal(split.same,true,'the downloaded SVG must be byte-for-byte what compose built');
  assert.equal(split.type,'image/png');assert.ok(split.size>200);
  assert.equal(split.width,600);assert.ok(split.height>300,'the caption band belongs to the composed drawing');
  assert.equal(split.bareHeight,300,'caption:false draws the chart alone, for a slide that writes its own caption');
  assert.equal(split.bareText,'','caption:false must not bake any text into the drawing');
  assert.equal(split.name,'composed.svg');
  const invalid=await page.evaluate(async()=>{fixture.innerHTML='<svg viewBox="0 0 0 0"></svg>';try{await MentatExport.image(fixture.firstChild,'svg','Bad','');return '';}catch(e){return e.message;}});
  // chart-export.js:17, reworded by the 2026-09-15 wording pass (f64316daa)
  // from 'The chart has no exportable viewport.'; the refusal itself is unchanged.
  assert.match(invalid,/Nothing to export/);
});
