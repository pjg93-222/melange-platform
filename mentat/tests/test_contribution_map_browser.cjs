/* Offline native-SVG map interaction; no server, donor upload, or data writes. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
test('map overlays preserve cents, ZIP selection works, and viewport zoom persists',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1200,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<main><h1>Contribution origin map</h1><div id="map"></div></main>');
  await page.addScriptTag({path:path.join(__dirname,'../app/contribution-map.js')});
  let geo={congress:'119',points:[{zip5:'02134',longitude:-71.13,latitude:42.36},{zip5:'99501',longitude:-149.86,latitude:61.22},{zip5:'96813',longitude:-157.85,latitude:21.32}],layers:{
    states:{type:'FeatureCollection',features:[{type:'Feature',properties:{GEOID:'25',STUSAB:'MA',NAME:'Massachusetts',INTPTLAT:42.2,INTPTLON:-71.5},geometry:{type:'Polygon',coordinates:[[[-73,42],[-71,42],[-71,43],[-73,42]]]}}]},
    districts:{type:'FeatureCollection',features:[{type:'Feature',properties:{GEOID:'2507',NAME:'Congressional District 7'},geometry:{type:'Polygon',coordinates:[[[-71.2,42.3],[-71.1,42.3],[-71.1,42.4],[-71.2,42.3]]]}}]}},sources:[],warnings:[]};
  if(process.env.MENTAT_GEO_FIXTURE)geo=JSON.parse(fs.readFileSync(process.env.MENTAT_GEO_FIXTURE,'utf8'));
  await page.evaluate(geometry=>{
    window.geometry=geometry;window.groups=[{zip5:'02134',state:'MA',amount_cents:10000},{zip5:'99501',state:'AK',amount_cents:20000},{zip5:'96813',state:'HI',amount_cents:-1500},{zip5:'99999',state:'MA',amount_cents:200},{zip5:'',state:'MA',amount_cents:300}];
    window.selected=null;window.viewport=null;
    window.map=MentatContributionMap.render(document.getElementById('map'),{groups,geometry,layers:['states','districts','zip_areas'],onSelect:value=>window.selected=value,onViewport:value=>window.viewport=value});
  },geo);
  assert.equal(await page.locator('svg circle').count(),3);
  assert.match(await page.locator('.contribution-map-coverage').textContent(),/Selected amount \$290\.00/);
  assert.equal(await page.locator('svg').getAttribute('role'),'group');
  await page.getByRole('button',{name:/^ZIP 02134:/}).last().click();
  assert.equal(await page.evaluate(()=>selected.zip5),'02134');
  await page.getByRole('button',{name:/^ZIP 96813:/}).last().focus();await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>selected.amount_cents),-1500);
  const before=await page.evaluate(()=>JSON.stringify(map.data));
  await page.getByLabel('States',{exact:true}).uncheck();
  await page.getByLabel('Congress 119 districts',{exact:true}).uncheck();
  assert.equal(await page.evaluate(()=>JSON.stringify(map.data)),before);
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  const zoomed=await page.locator('svg').getAttribute('viewBox');
  assert.notEqual(zoomed,'0 0 1000 640');
  await page.getByRole('button',{name:'Reset map',exact:true}).click();
  assert.equal(await page.locator('svg').getAttribute('viewBox'),'0 0 1000 640');
  await page.getByLabel('Focus map region').selectOption('Contiguous states');
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await page.waitForFunction(()=>window.viewport?.bbox?.length===4);
  await page.evaluate(()=>{const view=map.getView();map.destroy();map=MentatContributionMap.render(document.getElementById('map'),{groups,geometry,layers:['states','districts','zip_areas']});map.setView(view);});
  assert.notEqual(await page.locator('svg').getAttribute('viewBox'),'0 0 1000 640');
  await page.getByRole('button',{name:'Reset map',exact:true}).click();
  if(process.env.MENTAT_GEO_SCREENSHOT)await page.screenshot({path:process.env.MENTAT_GEO_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
});
