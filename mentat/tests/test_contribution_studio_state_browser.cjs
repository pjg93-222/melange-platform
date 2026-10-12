'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('Studio keeps saved page scope, exact filters and source-checked complete CSV exports',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());const page=await browser.newPage({viewport:{width:1400,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const html=fs.readFileSync(path.join(app,'contribution-studio.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route('http://studio.fixture/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://studio.fixture/');await page.addStyleTag({path:path.join(app,'contribution-studio.css')});
  await page.evaluate(()=>{
    window.queries=[];window.downloads=[];window.images=[];window.changedIndex=false;window.pending=[];window.holdCycle=false;
    const fields=[...['company','cycle','recipient','zip5','state'].map(id=>({id,label:id,role:'dimension'})),...['amount','records','contributors'].map(id=>({id,label:id==='amount'?'Net amount':id,role:'measure',unit:id==='amount'?'cents':'count'}))];
    const provenance={workbook:'Fixture.xlsx',workbook_sha256:'a'.repeat(64),index_token:'fixture-index',query_engine_sha256:'fixture-engine',scope:'Fixture only'};
    const year=new Date().getFullYear(),cycleEnd=year+year%2,currentCycle=(cycleEnd-1)+'-'+cycleEnd;
    const rows=Array.from({length:60},(_,i)=>({company:i===0?'=unsafe()':'Company '+String(i+1).padStart(3,'0'),amount:i===0?-1250:100,records:1,contributors:1}));
    const respond=value=>({ok:true,json:async()=>value});
    window.fetch=async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;
      if(url.endsWith('/session'))return respond({token:'fixture-token',read_only:true});
      if(url.endsWith('/catalog'))return respond({datasets:[{id:'Banking',label:'Banking',available:true,fields,bases:['original_observed','original_supported','receipt','raw','pac_activity'].map(id=>({id,label:id})),provenance}],fields});
      if(url.endsWith('/query')){
        queries.push(body);
        if(body.measures[0]==='records')return respond({rows:body.dimensions[0]==='cycle'?[{cycle:currentCycle,records:60}]:rows,has_more:false});
        const selected=body.dimensions[0]==='cycle'?[{cycle:currentCycle,amount:4650,records:60,contributors:60}]:body.basis==='original_supported'?rows.filter(row=>row.amount>0):rows;
        const limit=body.limit===100000?30:body.limit;
        const result={rows:selected.slice(body.offset,body.offset+limit),columns:[...body.dimensions,...body.measures].map(id=>fields.find(f=>f.id===id)),totals:{amount:selected.reduce((sum,row)=>sum+row.amount,0),records:selected.reduce((sum,row)=>sum+row.records,0),contributors:selected.reduce((sum,row)=>sum+row.contributors,0)},basis_label:'Included receipts',group_count:selected.length,offset:body.offset,has_more:body.offset+limit<selected.length,provenance:{...provenance,index_token:changedIndex&&body.offset===30?'changed-index':provenance.index_token},reconciliation:{conserved:true,receipt_matches_workbook:true},notices:[]};
        if(holdCycle&&body.dimensions[0]==='cycle')return new Promise(resolve=>pending.push(()=>resolve(respond(result))));
        return respond(result);
      }
      throw new Error('Unexpected route '+url);
    };
    window.MentatExport={download:(content,type,name)=>downloads.push({content,type,name}),image:async(source,format,title,context)=>images.push({format,title,context})};
  });
  for(const script of ['contribution-plot.js','chart-studio-contributions.js','contribution-studio.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.waitForFunction(()=>document.querySelector('#visualization svg'));
  await page.locator('#next-page').click();await page.waitForFunction(()=>document.querySelector('#result-count').textContent.startsWith('51–60'));
  const count=await page.evaluate(()=>queries.length);
  await page.locator('#labels').uncheck();
  assert.equal(await page.locator('#previous-page').isEnabled(),true);assert.equal(await page.evaluate(()=>queries.length),count,'changing labels must not fetch or reset the page');
  await page.locator('#export-svg').click();assert.match(await page.evaluate(()=>images.at(-1).context),/Groups 51–60 of 60/);
  await page.locator('#view-name').fill('Quarterly receipts');await page.locator('#save-view').click();
  const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('mentat-contribution-views-v1'))['Quarterly receipts']);
  assert.equal(saved.view.offset,50);assert.equal(saved.view.labels,false);assert.equal(saved.view.basis,'receipt');
  await page.locator('[data-preset="company"]').click();await page.waitForFunction(()=>document.querySelector('#result-count').textContent.startsWith('1–50'));
  await page.locator('#saved-view').selectOption('Quarterly receipts');await page.waitForFunction(()=>document.querySelector('#result-count').textContent.startsWith('51–60'));
  assert.equal(await page.locator('#previous-page').isEnabled(),true);
  await page.locator('#export-csv').click();await page.waitForFunction(()=>downloads.length===1);
  const downloaded=await page.evaluate(()=>downloads[0].content);
  assert.equal(downloaded.split('\r\n').length,61,'CSV must include every page, not only the visible ten rows');
  assert.match(downloaded,/"'=unsafe\(\)"/);assert.match(downloaded,/"-12\.5"/);assert.doesNotMatch(downloaded,/"'-12\.5"/);
  assert.match(downloaded,/accounting_basis/);assert.match(downloaded,/"receipt"/);assert.match(downloaded,/a{64}/);
  await page.evaluate(()=>window.changedIndex=true);await page.locator('#export-csv').click();await page.waitForFunction(()=>document.querySelector('#error').textContent.includes('Source changed during export'));
  assert.equal(await page.evaluate(()=>downloads.length),1,'changed source must prevent download');
  await page.evaluate(()=>window.changedIndex=false);
  await page.locator('#add-filter').click();await page.locator('#filter-field').selectOption('amount');await page.locator('#filter-op').selectOption('gte');await page.locator('#filter-value').fill('-12.50');await page.locator('#filter-editor button[type="submit"]').click();
  await page.waitForFunction(()=>queries.some(q=>q.filters?.some(f=>f.field==='amount'&&f.value===-1250)));
  assert.equal(await page.locator('#previous-page').isDisabled(),true,'a real query change resets pagination');
  await page.locator('[data-preset="cycle"]').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('ongoing calendar cycle'));
  assert.match(await page.locator('#status').textContent(),/Collection coverage is not established/);
  await page.locator('#export-svg').click();assert.match(await page.evaluate(()=>images.at(-1).context),/ongoing calendar cycle\. Collection coverage is not established/);
  await page.locator('[data-preset="company"]').click();await page.waitForFunction(()=>document.querySelector('#visualization svg'));
  // A delayed query from an earlier chart may not overwrite the current chart.
  await page.evaluate(()=>window.holdCycle=true);await page.locator('[data-preset="cycle"]').click();await page.waitForFunction(()=>pending.length===1);
  await page.locator('[data-preset="company"]').click();await page.waitForFunction(()=>document.querySelector('#visualization svg'));
  await page.evaluate(()=>pending.shift()());await page.waitForTimeout(100);
  assert.match(await page.locator('#view-title').textContent(),/company/);assert.equal(await page.locator('#chart-types [data-chart="bar"]').getAttribute('aria-pressed'),'true');
  await page.locator('#basis').selectOption('original_supported');await page.waitForFunction(()=>document.querySelector('#receipt-values dt')?.textContent==='Selected gross amount');
  assert.match(await page.locator('#basis option:checked').textContent(),/Supported original giving \(gross subset\)/);assert.match(await page.locator('#basis-help').textContent(),/Refunds are excluded/);
  assert.match(await page.locator('#measure-shelf').textContent(),/Supported gross amount/);assert.match(await page.locator('#visualization svg').textContent(),/Supported gross amount/);
  assert.match(await page.locator('#view-title').textContent(),/gross subset/);await page.locator('#export-svg').click();assert.match(await page.evaluate(()=>images.at(-1).context),/Supported original giving \(gross subset\)/);assert.match(await page.evaluate(()=>images.at(-1).context),/Query engine SHA256 fixture-engine/);
  await page.locator('#basis').selectOption('receipt');await page.waitForFunction(()=>document.querySelector('#receipt-values dt')?.textContent==='Selected net amount');assert.match(await page.locator('#measure-shelf').textContent(),/Net amount/);
  assert.deepEqual(errors,[]);
});

test('responsive Studio keeps long filter values inside the page',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());const page=await browser.newPage({viewport:{width:390,height:844}});
  const html=fs.readFileSync(path.join(app,'contribution-studio.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.setContent(html);await page.addStyleTag({path:path.join(app,'contribution-studio.css')});
  await page.evaluate(()=>{
    const chip=document.createElement('span');chip.className='filter-chip';
    const text=document.createElement('span');text.textContent='Recipient contains '+ 'LONGRECIPIENT'.repeat(16);
    const remove=document.createElement('button');remove.textContent='×';chip.append(text,remove);document.querySelector('#filter-chips').append(chip);
  });
  const sizes=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,page:document.documentElement.scrollWidth,chip:document.querySelector('.filter-chip').getBoundingClientRect().width}));
  assert.ok(sizes.page<=sizes.viewport+1,JSON.stringify(sizes));
  await page.setViewportSize({width:1280,height:720});
  await page.evaluate(()=>{document.querySelector('#filter-chips').replaceChildren();document.querySelector('#visualization').innerHTML='<svg width="1000" height="1600" viewBox="0 0 1000 1600"><rect width="1000" height="1600" fill="white"/></svg>';window.scrollTo(0,0);});
  await page.locator('#save-view').scrollIntoViewIfNeeded();
  const desktop=await page.evaluate(()=>({viewport:innerHeight,page:document.documentElement.scrollHeight,scrollY,paneHeight:document.querySelector('.analysis-pane').clientHeight,paneScroll:document.querySelector('.analysis-pane').scrollHeight}));
  assert.ok(desktop.paneScroll>desktop.paneHeight,'large charts must scroll within the desktop analysis pane');
  assert.ok(desktop.page<=desktop.viewport+2,JSON.stringify(desktop));assert.equal(desktop.scrollY,0,'screen-reader labels must not pull the outer desktop page down');
});
