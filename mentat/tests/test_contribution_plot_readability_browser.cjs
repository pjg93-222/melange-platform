'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('plot text remains readable and bounded while narrow charts scroll internally',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1280,height:720}});
  await page.setContent('<main id="fixture" class="visualization" style="width:min(720px,calc(100vw - 32px));margin:16px"></main>');
  await page.addStyleTag({path:path.join(app,'contribution-studio.css')});await page.addScriptTag({path:path.join(app,'contribution-plot.js')});
  const report=await page.evaluate(()=>{
    const company='W'.repeat(90)+' — COMPANY',rows=[{company,amount:-200,records:3},{company:'Small firm',amount:100,records:7}];
    window.originalRows=JSON.stringify(rows);window.fixtureRows=rows;
    const svg=MentatContributionPlot.render(fixture,rows,{type:'bar',dimensions:['company'],measures:['amount','records'],color:'company',labels:true,columns:[{id:'amount',name:'Net amount',unit:'cents'},{id:'records',name:'Observations',unit:'count'}]});
    const bounds=selector=>[...svg.querySelectorAll(selector)].map(n=>{const b=n.getBBox();return {x:b.x,right:b.x+b.width,y:b.y,bottom:b.y+b.height,title:n.querySelector('title')?.textContent,shown:[...n.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('')};});
    return {categories:bounds('[data-label="category"]'),values:bounds('[data-label="value"]'),legend:bounds('[data-legend] text'),legendText:svg.querySelector('[data-legend]').textContent,height:svg.viewBox.baseVal.height,minFont:Math.min(...[...svg.querySelectorAll('text')].map(n=>parseFloat(getComputedStyle(n).fontSize)*svg.getBoundingClientRect().width/1000)),marks:[...svg.querySelectorAll('.plot-mark')].map(n=>({x:Number(n.getAttribute('x')),width:Number(n.getAttribute('width'))})),unchanged:JSON.stringify(rows)===originalRows,company};
  });
  assert.equal(report.unchanged,true);assert.equal(report.marks.length,4);assert.equal(report.marks[0].x,255);assert.ok(Math.abs(report.marks[0].width-2*report.marks[1].width)<1e-8);
  assert.ok(report.minFont>=10.7,JSON.stringify(report));
  for(const label of report.categories){assert.ok(label.x>=15&&label.right<=242,JSON.stringify(label));if(label.title===report.company)assert.ok(label.shown.endsWith('…'));}
  for(const label of report.values)assert.ok(label.x>=886&&label.right<=985,JSON.stringify(label));
  assert.equal(report.categories.filter(n=>n.title===report.company).length,2,'every panel retains the full company label');
  assert.equal((report.legendText.match(/W/g)||[]).length,90,'wrapped legend must retain the complete category');
  for(const label of report.legend)assert.ok(label.x>=24&&label.right<=977&&label.bottom<=report.height,JSON.stringify(label));
  await page.setViewportSize({width:390,height:844});
  const mobile=await page.evaluate(()=>{const svg=fixture.querySelector('svg');return {page:document.documentElement.scrollWidth,viewport:innerWidth,chart:svg.getBoundingClientRect().width,scroll:fixture.scrollWidth,client:fixture.clientWidth,minFont:Math.min(...[...svg.querySelectorAll('text')].map(n=>parseFloat(getComputedStyle(n).fontSize)*svg.getBoundingClientRect().width/1000))};});
  assert.ok(mobile.page<=mobile.viewport+1,JSON.stringify(mobile));assert.ok(mobile.scroll>mobile.client);assert.ok(mobile.chart>=720);assert.ok(mobile.minFont>=10.7);
});
