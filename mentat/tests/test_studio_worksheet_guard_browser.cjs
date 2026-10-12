/* The published Overview workbook is a report layout, not a table: its cycle
   sheets stack Recipient, Employee and Master partitions of the same money, its
   Recipient List repeats every recipient under two views, and four of its
   sheets carry recipient committees' own activity. This drives the real client
   over an offline fixture shaped like those sheets and asserts what a reader
   sees: the guard pill, the note that explains it, the removal that stands, and
   the committee sheets whose totals are refused. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const core=require(path.join(app,'chart-studio-core.js'));

const GUARD_NOTE='Cycle sheets stack three fact partitions (Recipient, Employee, Master) of the same money. Only one partition matches the report total; Recipient is selected. Remove this filter to see the others.';
const VIEW_NOTE='This sheet repeats every recipient under an Industry view and a Company view. Industry is selected so amounts are not doubled.';
const COMMITTEE_NOTE=core.COMMITTEE_ACTIVITY_NOTE;

// Two books of one industry, exactly as published_reports() names them.
const BOOKS=[{index:11,name:'Banking Industry Spending Overview.xlsx',industry:'Banking',kind:'overview'},
  {index:22,name:'Banking Industry Spending Data.xlsx',industry:'Banking',kind:'data'}];

const SHEETS={
  '2025-2026':{columns:[{index:0,name:'Fact Type',type:'category'},{index:1,name:'Recipient',type:'category'},{index:2,name:'Amount',type:'number'}],
    rows:[['Recipient','Alpha PAC',70],['Recipient','Beta PAC',30],['Employee','Alpha PAC',55],['Employee','Beta PAC',25],['Master','Alpha PAC',70],['Master','Beta PAC',30]]},
  'Recipient List':{columns:[{index:0,name:'View',type:'category'},{index:1,name:'Recipient',type:'category'},{index:2,name:'Total Giving',type:'number'}],
    rows:[['Industry','Alpha PAC',70],['Industry','Beta PAC',30],['Company','Alpha PAC',70],['Company','Beta PAC',30]]},
  'Downstream Activity':{columns:[{index:0,name:'Committee',type:'category'},{index:1,name:'Net Included Amount',type:'number'}],
    rows:[['Alpha PAC',2000],['Beta PAC',800]]},
  'Recipient Detail':{columns:[{index:0,name:'Recipient',type:'category'},{index:1,name:'Amount',type:'number'}],
    rows:[['Alpha PAC',70],['Beta PAC',30]]}
};

async function open(t,origin,books){
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1600,height:1000}});
  page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(app,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.route(origin+'/**',route=>route.fulfill({contentType:'text/html',body:html}));
  // `?worksheet` is the console's own way into Report worksheet mode, so no
  // verified endpoint is ever reached and the fixture stays small.
  await page.goto(origin+'/?worksheet#chart-studio');
  for(const file of ['style.css','acu-theme.css','workspace.css','chart-studio.css'])await page.addStyleTag({path:path.join(app,file)});
  await page.evaluate(([sheets,catalog])=>{
    document.querySelectorAll('[role="tabpanel"][id^="view-"]').forEach(panel=>{panel.hidden=panel.id!=='view-chart-studio';});
    window.currentView='chart-studio';window.selectedRun='published-reports';
    window.escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.sheetCalls=[];
    const body=name=>{
      const sheet=sheets[name];
      return {...sheet,sheet:name,header_row:1,row_numbers:sheet.rows.map((_,i)=>i+2),
        row_count:sheet.rows.length,sha256:name.replace(/\W/g,'_').padEnd(64,'0').slice(0,64),note:'Offline fixture'};
    };
    window.api=async url=>{
      if(url==='/api/report-sources')return {runs:[{id:'published-reports',name:'Authoritative fixture reports'}]};
      if(url.startsWith('/api/reports?'))return {reports:catalog,report_directory:'fixture'};
      const name=new URLSearchParams(url.split('?')[1]).get('sheet');
      if(!name)return {sheets:Object.keys(sheets)};
      sheetCalls.push(name);
      return body(name);
    };
    window.fetch=async url=>{throw Error('Unexpected network call '+url);};
    window.MentatExport={download:()=>{},image:async()=>{}};
  },[SHEETS,books]);
  for(const file of ['theme.js','chart-math.js','grouped-chart.js','charts.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','contribution-map.js','chart-studio-contributions.js','chart-studio.js'])await page.addScriptTag({path:path.join(app,file)});
  await page.waitForFunction(()=>window.sheetCalls&&sheetCalls.length>0);
  // Since the 2026-09-17 UI clean-up (item 16) the Data source panel stays open
  // rather than folding itself away after the first load; the permanent "Change
  // data" button is still the reader's way to the pickers inside it.
  await page.waitForFunction(()=>document.getElementById('studio-source-details').open);
  await page.locator('#studio-scope-change').click();
  await page.waitForFunction(()=>document.getElementById('studio-source-details').open);
  await page.locator('#studio-sheet').waitFor({state:'visible'});
  return {page,errors};
}

const noteText=page=>page.locator('#studio-note').textContent();
// The pill's own text carries its type glyph and menu caret; the name span is
// the sentence a reader reads.
const pills=page=>page.locator('#studio-filters .studio-pill .studio-pill-name').allTextContents();

// Put a grouping and a measure on the shelves the way a reader does: the Data
// panel's field pills are click-to-add.
async function buildChart(page,dimension,measure){
  // Build 03 (S1): the source popover is put away before reaching for a field.
  if(await page.locator('#studio-source-popover').isVisible())await page.locator('#studio-scope-change').click();
  await page.locator('#studio-dimensions .studio-pill').filter({hasText:dimension}).first().click();
  await page.locator('#studio-measures .studio-pill').filter({hasText:measure}).first().click();
  await page.waitForFunction(()=>!!document.querySelector('#studio-canvas svg'));
}

test('a cycle sheet opens on the Recipient partition, says why, and lets the reader take it off',{timeout:120000},async t=>{
  const {page,errors}=await open(t,'http://worksheet-guard.fixture',BOOKS);

  // Companion first: the tall Data book is its own group and is what the
  // picker lands on, with the Overview book still one click away.
  assert.deepEqual(await page.locator('#studio-report optgroup').evaluateAll(g=>g.map(n=>n.label)),
    ['Data workbook (tall, chart-ready)','Overview workbook (report layout)']);
  assert.equal(await page.locator('#studio-report').inputValue(),'22');
  assert.equal(await page.locator('#studio-worksheet-hint').isVisible(),false,'a published Data book needs no hint');

  assert.equal(await page.locator('#studio-sheet').inputValue(),'2025-2026');
  assert.deepEqual(await pills(page),['Fact Type = Recipient']);
  assert.match(await noteText(page),/Cycle sheets stack three fact partitions/);

  // The guard is doing the arithmetic, not just the talking: 100, not 280.
  await buildChart(page,'Recipient','Amount');
  assert.match(await page.locator('#studio-status').textContent(),/SUM\(Amount\): 100(?![0-9,])/);
  assert.ok((await noteText(page)).startsWith(GUARD_NOTE),'the note leads with the reason the pill is there');

  // One click removes it, like any pill, and the other two partitions appear.
  await page.locator('#studio-filters .studio-pill').first().press('Delete');
  await page.waitForFunction(()=>document.querySelectorAll('#studio-filters .studio-pill').length===0);
  assert.match(await page.locator('#studio-status').textContent(),/SUM\(Amount\): 280(?![0-9,])/);
  assert.doesNotMatch(await noteText(page),/Cycle sheets stack three fact partitions/);

  // The removal stands across re-renders: a display edit must not put it back.
  await page.locator('#studio-labels').click();
  await page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
  assert.deepEqual(await pills(page),[],'a re-render never re-arms the guard');

  // A fresh load of the same worksheet does arm it again.
  // Build 03 (S1): the worksheet pickers live in the source chip's popover.
  if(!(await page.locator('#studio-sheet').isVisible()))await page.locator('#studio-scope-change').click();
  await page.locator('#studio-sheet').selectOption('Recipient Detail');
  await page.waitForFunction(()=>document.getElementById('studio-loading').textContent.includes('2 rows'));
  assert.deepEqual(await pills(page),[],'a sheet with no polymorphic column is not guarded');
  assert.doesNotMatch(await noteText(page),/Cycle sheets stack|recipient committees/);
  await page.locator('#studio-sheet').selectOption('2025-2026');
  await page.waitForFunction(()=>document.getElementById('studio-loading').textContent.includes('6 rows'));
  assert.deepEqual(await pills(page),['Fact Type = Recipient'],'a fresh load of the worksheet arms it again');

  // Recipient List repeats its money under two views; Industry is selected.
  await page.locator('#studio-sheet').selectOption('Recipient List');
  await page.waitForFunction(()=>document.getElementById('studio-loading').textContent.includes('4 rows'));
  assert.deepEqual(await pills(page),['View = Industry']);
  assert.ok((await noteText(page)).startsWith(VIEW_NOTE));
  await buildChart(page,'Recipient','Total Giving');
  assert.match(await page.locator('#studio-status').textContent(),/SUM\(Total Giving\): 100(?![0-9,])/);

  assert.deepEqual(errors,[]);
});

test('a committee-activity sheet says whose money it is and refuses a grand total',{timeout:120000},async t=>{
  const {page,errors}=await open(t,'http://committee-activity.fixture',BOOKS);
  await page.locator('#studio-sheet').selectOption('Downstream Activity');
  await page.waitForFunction(()=>document.getElementById('studio-loading').textContent.includes('2 rows'));
  assert.deepEqual(await pills(page),[],'this sheet has no partition column, so nothing is guarded');
  assert.ok((await noteText(page)).startsWith(COMMITTEE_NOTE),'the caption is there before a chart is built');

  await buildChart(page,'Committee','Net Included Amount');
  assert.ok((await noteText(page)).startsWith(COMMITTEE_NOTE),'and it stays once the chart is drawn');
  assert.doesNotMatch(await page.locator('#studio-status').textContent(),/SUM\(Net Included Amount\)/,
    'the status line prints no total for committee activity');

  // Asking for Totals gets the same sentence, once, and no number on the chart.
  await page.locator('#studio-tab-analytics').click();
  await page.getByRole('switch',{name:/^Totals/}).click();
  await page.waitForFunction(()=>document.querySelector('#studio-canvas').getAttribute('aria-busy')!=='true');
  assert.doesNotMatch(await page.locator('#studio-canvas').textContent(),/Grand total/,
    'no grand total is drawn over recipient-committee activity');
  const note=await noteText(page);
  assert.ok(note.startsWith(COMMITTEE_NOTE));
  assert.equal(note.split(COMMITTEE_NOTE).length-1,1,'the reason is given once, not twice');

  assert.deepEqual(errors,[]);
});

test('with no Data workbook published, only the heading and a one-line hint change',{timeout:120000},async t=>{
  const overviewOnly=BOOKS.filter(b=>b.kind==='overview');
  const {page,errors}=await open(t,'http://overview-only.fixture',overviewOnly);
  assert.deepEqual(await page.locator('#studio-report optgroup').evaluateAll(g=>g.map(n=>n.label)),
    ['Overview workbook (report layout)']);
  assert.equal(await page.locator('#studio-report').inputValue(),'11');
  assert.equal(await page.locator('#studio-worksheet-hint').textContent(),
    'The chart-ready Data workbook has not been published for this industry yet.');
  assert.equal(await page.locator('#studio-worksheet-hint').isVisible(),true);
  // Everything else is unchanged: the same sheets, the same first selection.
  assert.equal(await page.locator('#studio-sheet').inputValue(),'2025-2026');
  assert.deepEqual(await pills(page),['Fact Type = Recipient']);
  assert.deepEqual(errors,[]);
});
