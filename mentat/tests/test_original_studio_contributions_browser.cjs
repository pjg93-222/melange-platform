/* Independent original-shell integration against small, exact offline facts. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('original shelves use verified facts, display edits reuse queries, and ZIP drills conserve money',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://original-studio.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://original-studio.fixture/#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(()=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>panel.hidden=panel.id!=='view-chart-studio');
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.calls={query:[],records:[],geography:[]};window.version=1;window.recordVersion=1;window.downloads=[];
    const names={company:'Company',cycle:'Election cycle',zip5:'Contributor ZIP',state:'Contributor state',recipient:'Recipient committee',amount:'Net amount',records:'Observation count',contributors:'Distinct reported donor identities'};
    const fields=Object.entries(names).map(([id,label])=>({id,label,name:label,role:['amount','records','contributors'].includes(id)?'measure':'dimension',type:['amount','records','contributors'].includes(id)?'number':'category',unit:id==='amount'?'cents':['records','contributors'].includes(id)?'count':undefined}));
    const provenance=version=>({workbook:'Banking Industry Spending Overview.xlsx',workbook_sha256:'a'.repeat(64),database_sha256:String(version).repeat(64),index_token:'index-'+version,query_engine_sha256:'q'.repeat(64),scope:'Offline four-observation fixture'});
    const zipRows=[{zip5:'02134',state:'MA',amount:10000,records:1,contributors:1},{zip5:'02134',state:'NY',amount:2000,records:1,contributors:1},{zip5:'02134',state:'ZZ',amount:500,records:1,contributors:1},{zip5:'99501',state:'AK',amount:20000,records:1,contributors:1}];
    const stateFeatures=[['MA',-71.5,42.2],['NY',-75,43],['AK',-149.86,61.22]].map(([state,lon,lat])=>({type:'Feature',properties:{STUSAB:state,GEOID:state,INTPTLON:lon,INTPTLAT:lat},geometry:{type:'Polygon',coordinates:[]}}));
    const geometry={points:[{zip5:'02134',longitude:-71.13,latitude:42.36},{zip5:'99501',longitude:-149.86,latitude:61.22}],layers:{states:{type:'FeatureCollection',features:stateFeatures},districts:{type:'FeatureCollection',features:[]},zip_areas:{type:'FeatureCollection',features:[]}},coverage:{unmatched_zips:[]},warnings:[],sources:[{label:'Fixture Census',sha256:'g'.repeat(64)}]};
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,bases:['receipt','original_observed','original_supported','raw','pac_activity'].map(id=>({id,label:id})),provenance:provenance(version)}],fields});
      if(url.endsWith('/query')){
        calls.query.push(body);
        const base=body.dimensions.includes('zip5')?zipRows:body.dimensions.includes('state')?zipRows.map(({state,amount,records,contributors})=>({state,amount,records,contributors})):body.dimensions.includes('amount_bin')?[{amount_bin:0,records:1,amount:500},{amount_bin:1000000,records:3,amount:32000}]:body.dimensions.includes('cycle')?[{cycle:'2023-2024',amount:32500,records:4,contributors:4}]:[{company:'Alpha',amount:12500,records:3,contributors:3},{company:'Beta',amount:20000,records:1,contributors:1}];
        // The server returns the groups in the order the query asked for, so the
        // fixture does too: without it "Sort ascending" could not be seen to do
        // anything here, which is the whole subject of the P2 assertions below.
        const order=(body.sort||[])[0];
        const rows=order&&base.length&&typeof base[0][order.field]==='number'
          ?[...base].sort((a,b)=>order.direction==='asc'?a[order.field]-b[order.field]:b[order.field]-a[order.field])
          :base;
        return respond({status:'ready',rows:rows.slice(body.offset,body.offset+body.limit),columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)||{id,label:id,name:'Amount bin',role:'dimension',type:'number',unit:'cents'}),totals:{amount:32500,records:4,contributors:4},group_count:rows.length,offset:body.offset||0,has_more:false,provenance:provenance(version),basis_label:'Published included receipts',reconciliation:{conserved:true,receipt_matches_workbook:true},notices:[],verified_unique:false});
      }
      if(url.endsWith('/geography')){calls.geography.push(body);return respond({...geometry,congress:body.congress});}
      if(url.endsWith('/records')){
        calls.records.push(body);
        const rows=zipRows.filter(row=>body.filters.every(f=>f.op==='in'?f.value.includes(row[f.field]):row[f.field]===f.value)).map((row,i)=>({...row,amount_cents:row.amount,raw_cents:row.amount,source_id:'fixture-'+i,contributor:'Fixture donor',date:'2024-01-01',company:'Alpha',reasons:['fixture'],source_url:'https://www.fec.gov/data/receipts/individual-contributions/'}));
        const proof=provenance(recordVersion);delete proof.query_engine_sha256;
        return respond({status:'ready',rows,total_records:rows.length,has_more:false,provenance:proof});
      }
      throw Error('Unexpected endpoint '+url);
    };
    const worksheet={columns:[{index:0,name:'Region',type:'category'},{index:1,name:'Amount',type:'number'}],rows:[['A',10],['B',20]],row_numbers:[6,7],row_count:2,header_row:5,sheet:'Totals',sha256:'worksheet-fixture',note:'Worksheet fixture'};
    window.api=async url=>url==='/api/report-sources'?{runs:[{id:'published-reports',name:'Authoritative fixture reports'}]}:url.startsWith('/api/reports?')?{reports:[{index:0,name:'Fixture.xlsx'}]}:url.includes('sheet=')?worksheet:{sheets:['Totals']};
    window.MentatExport={download:(content,type,name)=>downloads.push({content,type,name}),image:async()=>{}};
  });
  for(const file of ['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'])await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(()=>document.querySelector('#studio-canvas svg')&&document.querySelector('#studio-verified-summary').textContent.includes('$325.00'));
  // Since the 2026-09-17 UI clean-up (item 16) the Data source panel stays open
  // rather than folding itself away after the first load, and the permanent
  // "Change data" button is still the reader's way to the pickers inside it.
  await page.waitForFunction(()=>document.getElementById('studio-source-details').open);
  await page.locator('#studio-scope-change').click();
  await page.waitForFunction(()=>document.getElementById('studio-source-details').open);
  assert.equal(await page.locator('#studio-dataset').isVisible(),true);
  assert.equal(await page.locator('#studio-source-mode').inputValue(),'contributions');
  assert.equal(await page.locator('#studio-dataset').inputValue(),'Banking');
  assert.equal(await page.locator('#studio-basis').inputValue(),'receipt');
  assert.equal(await page.locator('#studio-columns .studio-pill').count(),1);
  assert.equal(await page.locator('#studio-rows .studio-pill').count(),1);

  /* P2. A toolbar control is never silently inert: at every moment each of the
     three buttons either changes the chart when pressed, or is disabled and its
     title says why. fittedText() hangs the full value in a <title> child, so a
     bar's drawn category is its text node, not the element's textContent. */
  const bars=()=>page.evaluate(()=>[...document.querySelectorAll('#studio-canvas svg text[data-label="category"]')]
    .map(node=>node.firstChild.nodeValue));
  const settled=()=>page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
  // sheet().sort opens at 'none' and this path reads anything but 'asc' as
  // descending, so the descending button is pressed before anyone touches it.
  assert.equal(await page.locator('#studio-sort-desc').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#studio-sort-asc').getAttribute('aria-pressed'),'false');
  assert.match(await page.locator('#studio-sort-desc').getAttribute('title'),/^Sort by .+, largest first$/);
  assert.match(await page.locator('#studio-sort-asc').getAttribute('title'),/^Sort by .+, smallest first$/);
  assert.equal(await page.locator('#studio-undo').isDisabled(),true);
  const descending=await bars();
  assert.equal(descending.length,2);
  const beforeSort=await page.evaluate(()=>calls.query.length);
  // Build 03 (S1): Ascending and Descending live in the one Sort menu; the
  // source popover opened above is put away first, as a reader would.
  if(await page.locator('#studio-source-popover').isVisible())await page.locator('#studio-scope-change').click();
  await page.locator('#studio-sort > summary').click();
  await page.locator('#studio-sort-desc').click();
  assert.equal(await page.locator('#studio-undo').isDisabled(),true,'pressing the direction the view is already in is not an edit');
  assert.equal(await page.evaluate(()=>calls.query.length),beforeSort,'and costs no query');
  assert.deepEqual(await bars(),descending,'and leaves the bars where they were');
  if(!(await page.locator('#studio-sort-asc').isVisible()))await page.locator('#studio-sort > summary').click();
  await page.locator('#studio-sort-asc').click();
  await settled();
  assert.equal(await page.locator('#studio-sort-asc').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#studio-sort-desc').getAttribute('aria-pressed'),'false','the two are a radio pair');
  assert.deepEqual(await bars(),[...descending].reverse(),'ascending reverses the order');
  assert.equal(await page.locator('#studio-undo').isDisabled(),false,'a real change is undoable');
  await page.locator('#studio-undo').click();
  await settled();
  assert.deepEqual(await bars(),descending,'undo restores the order');
  assert.equal(await page.locator('#studio-sort-desc').getAttribute('aria-pressed'),'true');

  const firstQueries=await page.evaluate(()=>calls.query.length);
  // Build 03 (S1): a new sheet starts with labels on, so the toggle goes off
  // and back on - two render-only edits - and labels stay on for what follows.
  await page.locator('#studio-labels').click();
  await page.locator('#studio-labels').click();
  await page.locator('#studio-fit').selectOption('entire');
  await page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
  assert.equal(await page.evaluate(()=>calls.query.length),firstQueries,'labels and fit must reuse exact aggregates');
  // Paul, 2026-09-23: "...nothing is logically constrained, it needs to be
  // utterly maluable." Sort works on every mark now (census B-S15, B-S16), a
  // histogram included: both buttons are enabled and name the measure they
  // order by, and None - the bins' own numeric order - is what it opens on.
  const defaultMark=await page.locator('#studio-mark').inputValue();
  await page.locator('#studio-mark').selectOption('histogram');
  await settled();
  for(const id of ['#studio-sort-asc','#studio-sort-desc']){
    assert.equal(await page.locator(id).isDisabled(),false,id+' sorts a histogram too');
    assert.match(await page.locator(id).getAttribute('title'),/^Sort by .+, (smallest|largest) first$/);
    assert.equal(await page.locator(id).getAttribute('aria-pressed'),'false');
  }
  assert.equal(await page.locator('#studio-sort-none').getAttribute('aria-pressed'),'true','a histogram opens in bin order');
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).sort),[{field:'amount_bin',direction:'asc'}]);
  // Labels were turned on above, and a histogram draws them as the count over
  // each bin - a position that cannot collide with anything by construction.
  assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('#studio-canvas svg text[data-label="value"]')]
    .map(n=>n.textContent)),['1','3'],'each bin carries its count');
  // Paul, 2026-09-23: "...nothing is logically constrained, it needs to be
  // utterly maluable." (r4) A table's marks are its value cells, so Labels is
  // a real toggle there too: it stays enabled and pressed, and off would hide
  // the printed values (tests/test_studio_worksheet_map_browser.cjs).
  await page.locator('#studio-mark').selectOption('text');
  await settled();
  assert.equal(await page.locator('#studio-labels').isDisabled(),false);
  assert.match(await page.locator('#studio-labels').getAttribute('title'),/^Print each value in its cell/);
  assert.equal(await page.locator('#studio-labels').getAttribute('aria-pressed'),'true','the stored value shows on a table too');
  await page.locator('#studio-mark').selectOption(defaultMark);
  await settled();
  assert.equal(await page.locator('#studio-labels').isDisabled(),false,'and it returns on a mark that draws them');
  assert.equal(await page.locator('#studio-labels').getAttribute('aria-pressed'),'true','with the stored value untouched');
  await page.locator('#studio-mark').selectOption('map');
  await page.waitForFunction(()=>document.querySelector('#studio-canvas .contribution-map'));
  assert.deepEqual(await page.evaluate(()=>calls.query.at(-1).dimensions),['zip5','state']);
  assert.deepEqual(await page.evaluate(()=>calls.geography.at(-1).zips),['02134','99501']);
  assert.match(await page.locator('#studio-canvas').textContent(),/\$325\.00/);
  assert.match(await page.locator('#studio-canvas').textContent(),/\$320\.00/);
  assert.match(await page.locator('#studio-canvas').textContent(),/\$5\.00/);
  const mapQueries=await page.evaluate(()=>calls.query.length);
  await page.locator('#studio-congress').selectOption('120');
  await page.waitForFunction(()=>calls.geography.at(-1).congress==='120');
  assert.equal(await page.evaluate(()=>calls.query.length),mapQueries,'boundary edition must not rerun the money query');
  await page.getByRole('button',{name:/^ZIP 02134:/}).last().click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-records tbody tr').length===2);
  assert.deepEqual(await page.evaluate(()=>calls.records.at(-1).filters),[{field:'zip5',op:'eq',value:'02134'},{field:'state',op:'in',value:['MA','NY']}]);
  await page.evaluate(()=>window.recordVersion=2);
  await page.getByRole('button',{name:/^ZIP 02134:/}).last().click();
  await page.waitForFunction(()=>/changed|refresh/i.test(document.querySelector('#studio-record-count').textContent));
  assert.equal(await page.locator('#studio-records tbody tr').count(),0,'new-source records must not attach to old marks');
  const beforeRefresh=await page.evaluate(()=>calls.query.length);
  await page.evaluate(()=>window.version=2);
  if(!(await page.locator('#studio-refresh-data').isVisible()))await page.locator('#studio-scope-change').click();
  await page.locator('#studio-refresh-data').click();
  await page.waitForFunction(count=>calls.query.length>count&&document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true',beforeRefresh);
  await page.locator('#studio-download summary').click();
  await page.locator('#studio-download-csv').click();
  await page.waitForFunction(()=>downloads.length===1);
  const csv=await page.evaluate(()=>downloads[0].content);
  assert.match(csv,/index-2/,'explicit refresh must invalidate the old result');
  assert.match(csv,/q{64}/,'export includes the query engine identity');
  assert.match(csv,/2{64}/,'export includes the refreshed source database identity');
  assert.match(csv,/"02134"/,'CSV preserves leading-zero ZIP text');
  await page.locator('#studio-download summary').click();
  await page.locator('#studio-dimensions .studio-pill[data-column="3"]').press('Enter');
  await page.getByRole('button',{name:'Send to Columns',exact:true}).click();
  await page.locator('#studio-columns .studio-pill[data-column="2"]').press('Delete');
  await page.getByRole('button',{name:/^MA:/}).last().waitFor();
  await page.getByRole('button',{name:/^MA:/}).last().click();
  await page.waitForFunction(()=>document.querySelectorAll('#studio-records tbody tr').length===1);
  assert.deepEqual(await page.evaluate(()=>calls.records.at(-1).filters),[{field:'state',op:'in',value:['MA']}],'state marks must not drill the entire dataset');
  if(!(await page.locator('#studio-source-mode').isVisible()))await page.locator('#studio-scope-change').click();
  await page.locator('#studio-source-mode').selectOption('worksheet');
  await page.waitForFunction(()=>document.querySelector('#studio-loading').textContent.includes('2 rows'));
  assert.equal(await page.locator('#studio-worksheet-source').isVisible(),true);
  await page.locator('#studio-source-mode').selectOption('contributions');
  await page.waitForFunction(()=>document.querySelector('#studio-verified-summary').textContent.includes('$325.00'));
  assert.equal(await page.locator('#studio-contribution-source').isVisible(),true);
  assert.deepEqual(errors,[]);
});
