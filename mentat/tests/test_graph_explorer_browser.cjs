'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

test('10,000 PAC relationships stay searchable, pageable, inspectable and exportable',{timeout:60000},async t=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1080}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setContent('<main id="view-graph" style="padding:30px;max-width:1100px;margin:auto"><h1>MENTAT · synthetic explorer test</h1><div id="host"></div></main>');
  for(const sheet of ['style.css','graph.css'])await page.addStyleTag({path:path.join(app,sheet)});
  await page.addScriptTag({path:path.join(app,'graph-explorer.js')});
  const mountMs=await page.evaluate(()=>{
    const rows=Array.from({length:10000},(_,index)=>({candidate:`Candidate ${String(index).padStart(5,'0')}`,
      candidate_id:`H${String(index).padStart(8,'0')}`,committee:index%2?'C00000002':'C00000003',committee_name:index%2?'Branch PAC':'Leaf PAC',
      cycle:'2024',direction:'Support',amount:index===9999?-99.25:index+1,share_of_pac_direction:null,
      route:index%2?['C00000001','C00000002']:['C00000001','C00000002','C00000003']}));
    const edges=[{source:'C00000001',destination:'C00000002',cycle:'2024',amount:100},
      {source:'C00000002',destination:'C00000003',cycle:'2024',amount:50},
      {source:'C00000003',destination:'C00000001',cycle:'2024',amount:5}];
    const start=performance.now();
    window.MentatGraphExplorer.mount(document.querySelector('#host'),{root:'C00000001',direction:'Support',rows,edges,
      sources:[{committee:'C00000002',cycle:'2024',coverage_start:'2023-01-01',coverage_end:'2024-12-31',sha256:'a'.repeat(64)}],
      onEvidence:(id,cycle)=>{window.evidenceOpened=[id,cycle];}});
    return performance.now()-start;
  });
  assert.equal(await page.locator('tbody tr').count(),40);
  assert.match(await page.locator('[role=status]').textContent(),/10,000 relationships/);
  await page.getByRole('button',{name:'Next',exact:true}).click();
  assert.match(await page.locator('[role=status]').textContent(),/41–80/);
  await page.getByRole('searchbox').fill('Candidate 09999');
  assert.equal(await page.locator('tbody tr').count(),1);
  await page.getByRole('button',{name:'Candidate 09999',exact:true}).click();
  const inspector=page.getByRole('region',{name:'Selected candidate evidence'});
  assert.match(await inspector.textContent(),/-\$99\.25/);
  assert.match(await inspector.textContent(),/2023-01-01 to 2024-12-31/);
  await inspector.getByRole('button',{name:'Open this PAC in evidence'}).click();
  assert.deepEqual(await page.evaluate(()=>window.evidenceOpened),['C00000002','2024']);
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export matching relationships'}).click();
  const file=await download;
  const csv=fs.readFileSync(await file.path(),'utf8');
  assert.match(csv,/Candidate 09999/);assert.match(csv,/-99\.25/);assert.match(csv,/not traced donor dollars/);
  await page.getByRole('searchbox').fill('');
  await page.locator('.graph-route-links button').filter({hasText:'→ C00000002'}).click();
  assert.equal(await page.getByRole('checkbox').isChecked(),true);
  assert.match(await page.locator('[role=status]').textContent(),/10,000 relationships/);
  await page.getByRole('checkbox').uncheck();
  assert.match(await page.locator('[role=status]').textContent(),/5,000 relationships/);
  await page.getByRole('checkbox').check();
  assert.match(await page.locator('[role=status]').textContent(),/10,000 relationships/);
  await page.getByRole('combobox',{name:'Sort',exact:true}).selectOption('ascending');
  assert.match(await page.locator('tbody tr').first().textContent(),/Candidate 09999/);
  assert.deepEqual(errors,[]);
  // MENTAT_UI_OUTPUT redirects the screenshot out of the tree, as the other
  // browser suites allow (final fix round, MENTAT-DBG-09); unset, it lands where it always has.
  const folder=process.env.MENTAT_UI_OUTPUT?path.resolve(process.env.MENTAT_UI_OUTPUT):path.resolve(__dirname,'../.cache/ui-performance-20260920/screenshots');
  fs.mkdirSync(folder,{recursive:true});
  await page.screenshot({path:path.join(folder,'graph-explorer.png'),fullPage:true});
  console.log(`MEASURED explorer DOM build: ${mountMs.toFixed(2)}ms for 10,000 relationships, 40 rendered rows`);
});
