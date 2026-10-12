/* Studio/map integration with offline sources and controlled asynchronous fetch. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('Studio map sends geography only, preserves viewport, and rejects stale reloads',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());const page=await browser.newPage({viewport:{width:1400,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent(fs.readFileSync(path.join(app,'contribution-studio.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
  await page.evaluate(()=>{
    window.geoRequests=[];window.holdGeography=false;window.pendingGeography=[];window.imageCaptions=[];window.recordRequests=[];window.analyticalRequests=[];
    window.MentatExport={image:async(source,format,title,context)=>imageCaptions.push(context)};
    const fields=[...['company','cycle','zip5','state'].map(id=>({id,label:id==='zip5'?'Contributor ZIP':id,role:'dimension'})),...['amount','records','contributors'].map(id=>({id,label:id==='amount'?'Net amount':id,role:'measure',unit:id==='amount'?'cents':'count'}))];
    const provenance={workbook:'Fixture.xlsx',workbook_sha256:'a'.repeat(64),database_sha256:'b'.repeat(64),index_token:'fixture-index',scope:'Fixture only',query_engine_sha256:'fixture-query-engine'};
    const zipRows=[{zip5:'02134',state:'MA',amount:10000,records:1,contributors:1},{zip5:'02134',state:'NY',amount:2000,records:1,contributors:1},{zip5:'02134',state:'ZZ',amount:500,records:1,contributors:1},{zip5:'02134',state:'',amount:300,records:1,contributors:1},{zip5:'10001',state:'ZZ',amount:100,records:1,contributors:1},{zip5:'10002',state:'',amount:200,records:1,contributors:1},{zip5:'99501',state:'AK',amount:20000,records:1,contributors:1}];
    const geometry={congress:'119',points:[{zip5:'02134',longitude:-71.13,latitude:42.36},{zip5:'99501',longitude:-149.86,latitude:61.22}],layers:{states:{type:'FeatureCollection',features:[{type:'Feature',properties:{GEOID:'25',NAME:'Massachusetts',STUSAB:'MA',INTPTLON:-71.5,INTPTLAT:42.2},geometry:{type:'Polygon',coordinates:[[[-73,42],[-71,42],[-71,43],[-73,42]]]}}]},districts:{type:'FeatureCollection',features:[]},zip_areas:{type:'FeatureCollection',features:[]}},coverage:{unmatched_zips:[]},warnings:[],sources:[]};
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,bases:['original_observed','original_supported','receipt','raw','pac_activity'].map(id=>({id,label:id})),provenance}],fields});
      if(url.endsWith('/geography')){
        geoRequests.push(body);if(holdGeography)return new Promise(resolve=>pendingGeography.push(()=>resolve(respond({...geometry,congress:body.congress}))));
        return respond({...geometry,congress:body.congress});
      }
      if(url.endsWith('/query')){
        analyticalRequests.push(body);
        const dimension=body.dimensions[0];
        const rows=dimension==='zip5'?zipRows:dimension==='cycle'?[{cycle:'2023-2024',amount:33100,records:7,contributors:7}]:[{company:'Fixture company',amount:33100,records:7,contributors:7}];
        return respond({rows,columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)),totals:{amount:33100,records:7,contributors:7},basis_label:'Fixture basis',group_count:rows.length,offset:0,has_more:false,provenance,reconciliation:{conserved:true,receipt_matches_workbook:true},notices:[]});
      }
      if(url.endsWith('/records')){
        recordRequests.push(body);const rows=zipRows.filter(row=>body.filters.every(f=>f.op==='in'?f.value.includes(row[f.field]):row[f.field]===f.value)).map(row=>({...row,amount_cents:row.amount,contributor:'Fixture contributor'}));
        return respond({rows,total_records:rows.length,has_more:false,provenance});
      }
      throw new Error('Unexpected route '+url);
    };
  });
  for(const script of ['contribution-plot.js','contribution-map.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.evaluate(()=>{const original=MentatContributionMap.render;MentatContributionMap.render=(container,options)=>{window.lastMapOptions=options;window.lastMap=original(container,options);return window.lastMap;};});
  await page.addScriptTag({path:path.join(app,'chart-studio-contributions.js')});
  await page.addScriptTag({path:path.join(app,'contribution-studio.js')});
  await page.waitForFunction(()=>document.querySelector('#visualization svg'));
  await page.locator('[data-preset="zip"]').click();
  await page.waitForFunction(()=>document.querySelector('#visualization .contribution-map'));
  assert.equal(await page.evaluate(()=>lastMapOptions.showLayerControls),false);
  assert.equal(await page.locator('.contribution-map-toolbar input[type="checkbox"]').count(),0);
  const sent=await page.evaluate(()=>geoRequests[0]);
  assert.deepEqual(sent.zips,['02134','99501']);assert.deepEqual(Object.keys(sent).sort(),['congress','layers','zips']);
  assert.deepEqual(await page.evaluate(()=>analyticalRequests.find(q=>q.dimensions[0]==='zip5').dimensions),['zip5','state']);
  assert.equal(await page.evaluate(()=>lastMap.data.selected_cents),33100);assert.equal(await page.evaluate(()=>lastMap.data.mapped_cents),32000);assert.equal(await page.evaluate(()=>lastMap.data.unmapped_cents),1100);
  assert.match(await page.locator('#grain-label').textContent(),/One mark per ZIP, combining selected ZIP\/state groups/);
  assert.equal(await page.evaluate(()=>lastMap.data.marks.find(m=>m.zip5==='02134').amount_cents),12000);
  await page.locator('#export-svg').click();assert.match(await page.evaluate(()=>imageCaptions.at(-1)),/Negative amounts use orange; other amounts use teal/);
  assert.match(await page.evaluate(()=>imageCaptions.at(-1)),/fixture-query-engine/);assert.match(await page.evaluate(()=>imageCaptions.at(-1)),/foreign or unrecognized state codes/);assert.match(await page.evaluate(()=>imageCaptions.at(-1)),/location is uncertain/);
  await page.getByRole('button',{name:/^ZIP 02134:/}).last().click();await page.waitForFunction(()=>recordRequests.length===1);
  assert.deepEqual(await page.evaluate(()=>recordRequests[0].filters),[{field:'zip5',op:'eq',value:'02134'},{field:'state',op:'in',value:['MA','NY']}]);
  await page.waitForFunction(()=>document.querySelectorAll('#record-table tbody tr').length===2);
  await page.locator('#layer-zips').check();
  await page.waitForFunction(()=>geoRequests.length===2&&lastMapOptions.layers.includes('zip_areas'));
  await page.evaluate(()=>{lastMap.setView([200,100,400,256]);lastMapOptions.onViewport({bbox:[-90,31,-72,47]});});
  await page.waitForFunction(()=>geoRequests.length===3);
  await page.waitForFunction(()=>lastMap.getView().join(',')==='200,100,400,256');
  await page.waitForTimeout(900);
  assert.equal(await page.evaluate(()=>geoRequests.length),3,'restoring viewport must not re-request itself');
  assert.deepEqual(await page.evaluate(()=>geoRequests[2].bbox),[-90,31,-72,47]);
  // Queue a viewport callback, then leave map mode before its debounce expires.
  await page.evaluate(()=>lastMapOptions.onViewport({bbox:[-85,33,-76,43]}));
  await page.locator('[data-chart="bar"]').click();
  await page.waitForTimeout(900);
  assert.equal(await page.locator('#visualization .contribution-map').count(),0);
  assert.equal(await page.evaluate(()=>geoRequests.length),3,'leaving the map must cancel its queued reload');
  // A delayed response from the old map also must not replace a new bar view.
  await page.evaluate(()=>window.holdGeography=true);
  await page.locator('[data-preset="zip"]').click();
  await page.waitForFunction(()=>pendingGeography.length===1);
  await page.locator('[data-chart="bar"]').click();
  await page.waitForFunction(()=>document.querySelector('#visualization svg'));
  await page.evaluate(()=>pendingGeography.shift()());await page.waitForTimeout(300);
  assert.equal(await page.locator('#visualization .contribution-map').count(),0);
  assert.deepEqual(errors,[]);
});
