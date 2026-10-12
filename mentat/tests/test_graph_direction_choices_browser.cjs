'use strict';
/* r4 code review F5: the page's Direction select (#graph-direction) offered
   all six sides in every candidate view, so a reader could land on a side that
   cannot hold anything - Contributions or Other activity among the saved
   estimates (which record no transaction type), or the unsplit 'Contribution /
   Other' on a report whose every row carries a code - and read the empty panel
   as a zero. The select now offers what the view can hold, and a permalink's
   side that the answer does not offer is asked again as the side it became.
   Offline: the page is the golden index.html and /api/graph is a fixture that
   answers from the request's own mode and direction. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');

async function open(browser,scope){
  const page=await browser.newPage({viewport:{width:1500,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.setContent(fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
  for(const sheet of ['style.css','graph.css','workspace.css'])await page.addStyleTag({path:path.join(app,sheet)});
  await page.addScriptTag({path:path.join(app,'theme.js')});
  await page.evaluate(scope=>{
    window.currentView='graph';window.splitView=false;window.selectedRun='fixture';
    document.querySelector('#view-loading').hidden=true;document.querySelector('#view-graph').hidden=false;
    // A permalink, as scope.js would read it; written scopes are recorded.
    window.written=[];
    if(scope)window.MentatScope={read:()=>Object.assign({invalid:[]},scope),write:partial=>window.written.push(partial)};
    const person=(id,label,amount)=>({id,label,amount,party:'D'});
    // Activity on a report whose every row carries a transaction type: the
    // unsplit side is empty (0 candidates, $0), as on 8812's Banking.
    const activity={Contribution:[person('H1','Contribution Candidate',120)],Other:[person('H2','Other Candidate',30)],
      Support:[person('H3','Support Candidate',50)],Oppose:[],'Contribution / Other':[],Unknown:[]};
    // Saved estimates record the bucket whole (no code).
    const estimates={Support:[person('H3','Support Candidate',11)],Oppose:[person('H4','Oppose Candidate',7)],
      'Contribution / Other':[person('H1','Contribution Candidate',19)],Unknown:[]};
    window.graphRequests=[];
    window.api=async url=>url.startsWith('/api/report-sources')?{runs:[{id:'fixture',name:'Offline fixture',state:'completed'}]}:{industries:[{name:'Synthetic'}]};
    window.fetch=async url=>{
      const address=new URL(String(url),'http://fixture.invalid'),query=address.searchParams;
      const mode=query.get('mode')||'direct',direction=query.get('direction')||'Support';
      // Only Graph's own reads are counted (the sparklines post elsewhere).
      if(address.pathname==='/api/graph')window.graphRequests.push({mode,direction});
      const sides=mode==='activity'?activity:mode==='estimates'?estimates:null;
      const candidates=sides?(sides[direction]||[]):[person('H9','Direct Candidate',90)];
      const body={options:{companies:['Fixture Company'],cycles:['2026'],categories:['Super PAC'],recipients:[{id:'C00000001',label:'Fixture PAC'}]},
        filters:{company:'',cycle:'2026',tier:'',funding:'all',mode,direction,category:'',recipient:''},
        totals:{giving:200,direct:90,organizations:110,unresolved_candidate_links:0,recipients:1,receipt_groups:2},
        organizations:[{id:'C00000001',label:'Fixture PAC',amount:110}],categories:[{id:'Super PAC',label:'Super PAC',amount:110}],
        companies:[{id:'Fixture Company',label:'Fixture Company',amount:200}],levels:[{id:'Employees',label:'Employees',amount:200}],
        levels_by_company:[],candidates,candidate_available:true,candidate_note:'Fixture.',candidate_details:[],
        note:'Synthetic test data only.',reports:[{name:'Fixture workbook.xlsx',sha256:'a'.repeat(64)}]};
      if(mode==='activity'){
        body.direction_totals=['Contribution','Other','Support','Oppose','Contribution / Other','Unknown'].map(name=>({direction:name,
          candidates:activity[name].length,amount:activity[name].reduce((sum,row)=>sum+row.amount,0),unidentified_amount:0,transaction_types:[]}));
        body.candidates_by_direction=activity;body.contribution_other_union=null;
      }
      return {ok:true,json:async()=>body};
    };
  },scope||null);
  await page.addScriptTag({path:path.join(app,'graph-explorer.js')});
  await page.addScriptTag({path:path.join(app,'graph.js')});
  await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden,undefined,{timeout:20000});
  return {page,errors};
}
const offered=page=>page.locator('#graph-direction option').evaluateAll(nodes=>nodes.map(node=>[node.value,node.textContent]));
const settled=(page,count)=>page.waitForFunction(count=>window.graphRequests.length>=count&&!document.querySelector('#graph-results').hidden,count,{timeout:20000});

test('the Direction select offers the sides each candidate view can hold (F5)',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());

  // 1. The page opens on direct contributions; the select is hidden there.
  const {page,errors}=await open(browser,null);
  assert.equal(await page.locator('label[for=graph-direction]').isHidden(),true);
  // 2. Downstream activity, on a report whose rows all carry a code: the
  // empty unsplit side and the empty Unknown are not offered; Support,
  // Opposition, Contributions and Other activity are (an empty Oppose is a
  // measured zero).
  await page.locator('#graph-tab-candidates').click();
  await page.waitForTimeout(300);
  await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden,undefined,{timeout:20000});
  let seen=await page.evaluate(()=>window.graphRequests.length);
  await page.locator('#graph-mode').selectOption('activity');
  await settled(page,seen+1);
  await page.waitForFunction(()=>document.querySelectorAll('#graph-direction option').length===4,undefined,{timeout:20000});
  assert.deepEqual(await offered(page),[['Support','Support'],['Oppose','Opposition'],['Contribution','Contributions'],['Other','Other activity']]);
  assert.equal(await page.locator('#graph-direction').inputValue(),'Support');
  // 3. Other activity ranks its own candidates.
  seen=await page.evaluate(()=>window.graphRequests.length);
  await page.locator('#graph-direction').selectOption('Other');
  await settled(page,seen+1);
  await page.waitForFunction(()=>document.querySelector('#graph-candidates').textContent.includes('Other Candidate'),undefined,{timeout:20000});
  assert.deepEqual((await page.evaluate(()=>window.graphRequests)).at(-1),{mode:'activity',direction:'Other'});
  // 4. Saved estimates carry no transaction type: the halves are not offered
  // there, and the chosen half is asked for as the recorded bucket it was
  // split from - never an empty estimates panel.
  seen=await page.evaluate(()=>window.graphRequests.length);
  await page.locator('#graph-mode').selectOption('estimates');
  await settled(page,seen+1);
  assert.deepEqual((await page.evaluate(()=>window.graphRequests)).at(-1),{mode:'estimates',direction:'Contribution / Other'});
  await page.waitForFunction(()=>document.querySelector('#graph-candidates').textContent.includes('Contribution Candidate'),undefined,{timeout:20000});
  assert.deepEqual(await offered(page),[['Support','Support'],['Oppose','Opposition'],['Contribution / Other','Contributions / other'],['Unknown','Unknown direction']]);
  assert.equal(await page.locator('#graph-direction').inputValue(),'Contribution / Other');
  // 5. Back to activity: the bucket is split again, and the answer's sides
  // decide - the empty unsplit side becomes its larger half, asked for once
  // more before anything is drawn.
  seen=await page.evaluate(()=>window.graphRequests.length);
  await page.locator('#graph-mode').selectOption('activity');
  await settled(page,seen+2);
  const tail=(await page.evaluate(()=>window.graphRequests)).slice(seen);
  assert.deepEqual(tail,[{mode:'activity',direction:'Contribution / Other'},{mode:'activity',direction:'Contribution'}]);
  await page.waitForFunction(()=>document.querySelector('#graph-candidates').textContent.includes('Contribution Candidate'),undefined,{timeout:20000});
  assert.equal(await page.locator('#graph-direction').inputValue(),'Contribution');
  assert.deepEqual(await offered(page),[['Support','Support'],['Oppose','Opposition'],['Contribution','Contributions'],['Other','Other activity']]);
  assert.deepEqual(errors,[]);

  // 6. An old permalink: mode=activity&direction=Contribution / Other on a
  // report whose rows all carry a code. It is asked for as written, then as
  // Contributions (the larger half), and the page shows that side's rows -
  // not an empty panel that reads as zero - and writes the side it shows.
  const old=await open(browser,{industry:'Synthetic',mode:'activity',direction:'Contribution / Other'});
  await old.page.waitForFunction(()=>window.graphRequests.length>=2&&document.querySelector('#graph-candidates').textContent.includes('Contribution Candidate'),undefined,{timeout:20000});
  assert.deepEqual(await old.page.evaluate(()=>window.graphRequests),
    [{mode:'activity',direction:'Contribution / Other'},{mode:'activity',direction:'Contribution'}]);
  assert.equal(await old.page.locator('#graph-direction').inputValue(),'Contribution');
  // The page's scope writes (the drawer writes its own keys separately).
  const scopes=(await old.page.evaluate(()=>window.written)).filter(partial=>'direction' in partial);
  assert.deepEqual(scopes.map(partial=>partial.direction),['Contribution / Other','Contribution']);
  assert.deepEqual(old.errors,[]);
});
