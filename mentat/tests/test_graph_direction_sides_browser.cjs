'use strict';
/* r5 code review F2: refresh() called clear(), which dropped the sides the
   last activity answer measured (directionTotals), and syncControls() then
   rebuilt #graph-direction to the unmeasured list for the whole request;
   render() rebuilt it again to the measured list when the answer landed. On
   a 20-35 s Banking read the reader saw a shorter list - no 'Contribution /
   Other', which this fixture's report measures - for the duration. The sides
   do not depend on the side asked for, so a refresh of the same scope now
   keeps them while its answer is read; a different scope still starts from
   the unmeasured list, because its sides are not known until its own answer
   names them. Offline: the golden index.html with graph.js, /api/graph a
   fixture whose activity report holds all six sides, and one answer held
   2 s while the list is sampled (the code review's own probe, as a test). */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const SIX=['Support','Oppose','Contribution','Other','Contribution / Other','Unknown'];

async function open(browser){
  const page=await browser.newPage({viewport:{width:1500,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.setContent(fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,''));
  for(const sheet of ['style.css','graph.css','workspace.css'])await page.addStyleTag({path:path.join(app,sheet)});
  await page.addScriptTag({path:path.join(app,'theme.js')});
  await page.evaluate(()=>{
    window.currentView='graph';window.splitView=false;window.selectedRun='fixture';
    document.querySelector('#view-loading').hidden=true;document.querySelector('#view-graph').hidden=false;
    // A permalink that opens on downstream activity, Support.
    window.MentatScope={read:()=>({invalid:[],mode:'activity',direction:'Support'}),write:()=>{}};
    const person=(id,label,amount)=>({id,label,amount,party:'D'});
    // Every side holds a candidate, so the measured list is all six.
    const activity={Contribution:[person('H1','Contribution Candidate',120)],Other:[person('H2','Other Candidate',30)],
      Support:[person('H3','Support Candidate',50)],Oppose:[person('H5','Oppose Candidate',9)],
      'Contribution / Other':[person('H6','Unsplit Candidate',77)],Unknown:[person('H7','Unknown Candidate',5)]};
    window.graphRequests=[];window.holdNext=0;
    window.api=async url=>url.startsWith('/api/report-sources')?{runs:[{id:'fixture',name:'Offline fixture',state:'completed'}]}:{industries:[{name:'Synthetic'}]};
    window.fetch=async url=>{
      const address=new URL(String(url),'http://fixture.invalid'),query=address.searchParams;
      const mode=query.get('mode')||'direct',direction=query.get('direction')||'Support',funding=query.get('funding')||'all';
      if(address.pathname==='/api/graph'){
        window.graphRequests.push({mode,direction,funding});
        // The next Graph answer is held, as a slow Banking read would be.
        if(window.holdNext){const ms=window.holdNext;window.holdNext=0;await new Promise(resolve=>setTimeout(resolve,ms));}
      }
      const candidates=mode==='activity'?(activity[direction]||[]):[person('H9','Direct Candidate',90)];
      const body={options:{companies:['Fixture Company'],cycles:['2026'],categories:['Super PAC'],recipients:[{id:'C00000001',label:'Fixture PAC'}]},
        filters:{company:'',cycle:'2026',tier:'',funding,mode,direction,category:'',recipient:''},
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
  });
  await page.addScriptTag({path:path.join(app,'graph-explorer.js')});
  await page.addScriptTag({path:path.join(app,'graph.js')});
  await page.waitForFunction(()=>!document.querySelector('#graph-results').hidden,undefined,{timeout:20000});
  return {page,errors};
}
const list=page=>page.evaluate(()=>[...document.querySelectorAll('#graph-direction option')].map(option=>option.value));
const value=page=>page.evaluate(()=>document.getElementById('graph-direction').value);
const landed=(page,count)=>page.waitForFunction(count=>window.graphRequests.length>=count&&!document.querySelector('#graph-results').hidden,count,{timeout:20000});
// The list, sampled about every 100 ms for as long as the held answer is
// being read (clear() hides the results until render() shows them again).
async function during(page){
  const samples=[];
  for(let i=0;i<80;i++){
    const sample=await page.evaluate(()=>({list:[...document.querySelectorAll('#graph-direction option')].map(option=>option.value),
      value:document.getElementById('graph-direction').value,reading:document.querySelector('#graph-results').hidden}));
    if(!sample.reading)break;
    samples.push(sample);
    await page.waitForTimeout(100);
  }
  assert.ok(samples.length>=3,'the list was sampled while the answer was read ('+samples.length+' samples)');
  return samples;
}

test('the Direction select keeps the measured sides while the same scope is read again (F2)',{timeout:120000},async t=>{
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const {page,errors}=await open(browser);
  await page.waitForFunction(six=>[...document.querySelectorAll('#graph-direction option')].length===six,SIX.length,{timeout:20000});
  assert.deepEqual(await list(page),SIX,'the answer measured all six sides');
  assert.equal(await value(page),'Support');

  // 1. Another side of the same scope: the answer is held 2 s, and for all
  // of it the select still offers the six sides the scope measured.
  let seen=await page.evaluate(()=>window.graphRequests.length);
  await page.evaluate(()=>{window.holdNext=2000;});
  await page.locator('#graph-direction').selectOption('Unknown');
  const same=await during(page);
  for(const sample of same){
    assert.deepEqual(sample.list,SIX,'no shorter list while the same scope is read again');
    assert.equal(sample.value,'Unknown','the chosen side stays chosen');
  }
  await landed(page,seen+1);
  assert.deepEqual(await list(page),SIX);
  assert.equal(await value(page),'Unknown');
  assert.deepEqual((await page.evaluate(()=>window.graphRequests)).at(-1),{mode:'activity',direction:'Unknown',funding:'all'});

  // 2. A different scope (personal giving only): its sides are not known
  // until its answer names them, so the select offers the unmeasured list
  // while it is read - never the previous scope's sides - and the measured
  // six once the answer lands.
  seen=await page.evaluate(()=>window.graphRequests.length);
  await page.evaluate(()=>{window.holdNext=2000;});
  await page.locator('#graph-funding').selectOption('personal');
  const other=await during(page);
  for(const sample of other){
    assert.deepEqual(sample.list,['Support','Oppose','Contribution','Other','Unknown'],'a new scope starts from the unmeasured list');
    assert.equal(sample.value,'Unknown');
  }
  await landed(page,seen+1);
  await page.waitForFunction(six=>[...document.querySelectorAll('#graph-direction option')].length===six,SIX.length,{timeout:20000});
  assert.deepEqual(await list(page),SIX);
  assert.deepEqual((await page.evaluate(()=>window.graphRequests)).at(-1),{mode:'activity',direction:'Unknown',funding:'personal'});

  // 3. And that scope, read again for another side, keeps its own six.
  seen=await page.evaluate(()=>window.graphRequests.length);
  await page.evaluate(()=>{window.holdNext=2000;});
  await page.locator('#graph-direction').selectOption('Contribution / Other');
  for(const sample of await during(page))assert.deepEqual(sample.list,SIX);
  await landed(page,seen+1);
  assert.equal(await value(page),'Contribution / Other');
  assert.deepEqual(errors,[]);
});
