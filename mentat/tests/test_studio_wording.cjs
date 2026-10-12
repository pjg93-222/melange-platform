/* F5 and the wording half of F4: the console names its own controls. There is
   no "contribution explorer" in this console - the control is Data source - and
   the default empty state names the mode the console actually starts in. Read
   from the shipped files and the pure modules, so a regression anywhere in the
   client is caught without a browser. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const app=path.join(__dirname,'../app');
const core=require(path.join(app,'chart-studio-core.js'));
const contributions=require(path.join(app,'chart-studio-contributions.js'));
const read=name=>fs.readFileSync(path.join(app,name),'utf8');

const COLUMNS=[{index:0,name:'Recipient Category',type:'category'},
  {index:1,name:'Amount',type:'number'},{index:2,name:'Contribution Count',type:'number'}];
const sheet=patch=>Object.assign(core.createSheet('Sheet 1','s1'),patch||{});
const issues=patch=>core.compileReport(sheet(patch),COLUMNS).issues.join(' ');
const notes=patch=>core.compileReport(sheet(patch),COLUMNS).notes.join(' ');

const CATALOG=[...['company','cycle','recipient'].map(id=>({id,label:id,role:'dimension'})),
  ...['amount','records'].map(id=>({id,label:id,role:'measure',unit:id==='records'?'count':'cents'}))];
const VERIFIED=contributions.fields(CATALOG);
const at=id=>VERIFIED.find(f=>f.id===id).index;
const compile=state=>contributions.compile(state,VERIFIED,{dataset:'Banking',basis:'receipt'});

test('no refusal points at a "contribution explorer" this console does not have',()=>{
  for(const name of ['chart-studio-core.js','chart-studio.js','chart-studio-contributions.js','index.html','chart-studio.css'])
    assert.doesNotMatch(read(name),/contribution explorer/i,name+' still names a control that does not exist');
});

test('an empty sheet asks for a grouping and a dollar measure, in those words',()=>{
  assert.match(issues({}),/Add a grouping \(company, recipient, cycle\) and a dollar measure\./);
  assert.match(issues({marks:Object.assign(core.createSheet('x').marks,{type:'bar'})}),
    /Add a grouping \(company, recipient, cycle\) and a dollar measure\./);
  assert.doesNotMatch(issues({}),/Drag a dimension/);
});

// Paul, 2026-09-23: "Additionally, within the Chart Studio make sure everything is freely flexible, that all
// graph types properly register and nothing is logically constrained, it needs to be utterly maluable." The
// three worksheet refusals this test pinned are gone: a Size field, a second measure and a grouped scatter
// all draw now (contract W2, W7, W9). The one note that still sends a reader to Verified contributions keeps
// its words, and the refusals may not come back.
test('no worksheet composition is refused, and the note that names Verified contributions keeps its words',()=>{
  const marks=patch=>Object.assign(core.createSheet('x').marks,patch);
  assert.strictEqual(issues({columns:[{column:0,aggregation:'sum'}],rows:[{column:1,aggregation:'sum'}],marks:marks({size:{column:2,aggregation:'sum'}})}),'');
  assert.strictEqual(issues({columns:[{column:0,aggregation:'sum'}],
    rows:[{column:1,aggregation:'sum'},{column:2,aggregation:'sum'}]}),'');
  assert.strictEqual(issues({marks:marks({type:'circle'}),columns:[{column:1,aggregation:'sum'}],
    rows:[{column:2,aggregation:'sum'},{column:0,aggregation:'sum'}]}),'');
  for(const gone of [/is not available for worksheet data/,/Worksheet charts show one measure/,/Grouping needs Verified contributions/])
    assert.doesNotMatch(read('chart-studio-core.js'),gone);
  assert.match(notes({columns:[{column:0,aggregation:'sum'}],rows:[{column:1,aggregation:'sum'}],
    filters:[{column:1,op:'gte',value:'5'}]}),
    /Number filters apply to rows, not totals\. Filter totals in Verified contributions\./);
});

test('the verified engine asks for a field and a mark in the reader’s vocabulary',()=>{
  // Paul: "nothing is logically constrained". A sheet of dimensions alone counts source observations
  // (contract G-V: records is the default measure); only a sheet with nothing on it asks for a field.
  const bare=Object.assign(core.createSheet('a'),{columns:[{column:at('company'),aggregation:'sum'}]});
  const counted=compile(bare);
  assert.deepStrictEqual(counted.query.measures,['records']);
  assert.match(counted.notes.join(' '),/No measure is placed, so each mark counts source observations\./);
  assert.throws(()=>compile(core.createSheet('empty')),/^Error: Add a dollar or count measure to Rows or Columns\.$/);
  const odd=Object.assign(core.createSheet('b'),{columns:[{column:at('company'),aggregation:'sum'}],
    rows:[{column:at('amount'),aggregation:'sum'}]});
  odd.marks.type='sunburst';
  assert.throws(()=>compile(odd),/^Error: Not available for verified data\. Use Bar, Line, Circle, Text or Map, or switch to Report worksheet\.$/);
});

test('the default empty state names the mode the console starts in',()=>{
  const html=read('index.html');
  assert.match(html,/<p id="studio-note" class="studio-note">Choose a verified report in the Data panel to begin\.<\/p>/);
  assert.doesNotMatch(html,/Choose a report worksheet in the Data panel to begin\.<\/p>/);
  // The two runtime empty states agree with it, each naming its own mode. Both
  // write the note of the surface they draw into - a board tile's, or the
  // console's own when the canvas is on its own.
  const js=read('chart-studio.js');
  assert.match(js,/node\('note'\)\.textContent='Choose a verified report in the Data panel to begin\.';/);
  assert.match(js,/node\('note'\)\.textContent = 'Choose a report worksheet in the Data panel to begin\.';/);
  assert.doesNotMatch(js,/in the Data pane\b/);
});

test('the header keeps a permanent, plainly named way back to the report picker',()=>{
  const html=read('index.html');
  // Build 03 (S1): the way back is the source chip itself - industry, cycle
  // scope and basis - and it opens the Data source popover; its title names
  // the act the old "Change data" label named.
  assert.match(html,/<button id="studio-scope-change" class="studio-scope-change" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="studio-source-popover" title="[^"]*change the data source"><span id="studio-scope-chip" class="studio-scope-chip"><\/span><span id="studio-scope-basis" class="studio-scope-basis"><\/span><\/button>/);
  assert.match(html,/<div id="studio-source-popover" class="studio-source-popover" role="dialog" aria-label="Data source" hidden>\s*<details class="studio-source-disclosure" id="studio-source-details" open>/);
  assert.doesNotMatch(html,/id="studio-scope-change"[^>]*\shidden/);
  assert.match(html,/<details class="studio-source-disclosure" id="studio-source-details" open>/);
});

test('Graph names the figure it shows, so it can be matched to Chart Studio',()=>{
  assert.match(read('index.html'),/<strong id="graph-total">—<\/strong><small id="graph-basis-note"><\/small>/);
  assert.match(read('graph.js'),
    /Net workbook receipts\. Same basis as Chart Studio’s Published included receipts\./);
});

test('the group limit says what it means, and is sixty everywhere',()=>{
  assert.strictEqual(core.STUDIO_GROUP_LIMIT,60);
  const markup='Groups shown per page';
  const label=markup+'. Totals and CSV exports include every group.';
  assert.match(read('index.html'),/title="Groups shown per page"/);
  assert.match(read('index.html'),/placeholder="60" title="Groups shown per page"/);
  assert.ok(read('chart-studio.js').includes(label),'the verified label opens with the same sentence as the markup');
  assert.doesNotMatch(read('index.html'),/default 40/);
  assert.doesNotMatch(read('index.html'),/40 per page/);
  // The page counter says the totals are not the page: since build 03 (S1) the
  // line reads "Groups 1–10 of 10" and its title carries "Totals cover all".
  assert.match(read('chart-studio.js'),/`Groups \$\{fmt\(result\.offset\+1\)\}–\$\{fmt\(result\.offset\+result\.rows\.length\)\} of \$\{fmt\(result\.group_count\)\}`/);
  assert.match(read('chart-studio.js'),/`Totals cover all \$\{fmt\(result\.group_count\)\} groups`/);
});

// The cross-reference tab was dwarfed on the owner's ruling: the published
// report workbook is the accounting of record, so the tab carries no totals and
// the review-reason vocabulary it used to print went with them.
test('the cross-reference says what it is, in the approved plain words',()=>{
  const xref=read('cross-reference.js');
  assert.ok(xref.includes('The published report workbook is the accounting of record. This view lists evidence-backed contradictions, clear refunds and unusually large counted receipts for inspection. Size alone never excludes a receipt; this view publishes no totals.'));
  for(const [key,label] of [
    ['other_receipt','Other receipt'],
    ['asset_liquidation','Asset or liability liquidation'],
    ['IND','Individual'],
    ['ORG','Organization']
  ]) assert.ok(xref.includes(key+":'"+label+"'"),'missing plain-word name for '+key);
  for(const heading of ['Clear refunds','Contradictions and large receipts','Negative adjustments without a matched original',
    'Evidence-backed contradictions to giving','Unusually large receipts retained for inspection'])
    assert.ok(xref.includes('>'+heading+'<'),'missing heading '+heading);
  assert.ok(!xref.includes('Observed original giving'),'the tab publishes no total');
});

test('the reconciliation strip has a reason for every way two totals can part',()=>{
  assert.deepStrictEqual(Object.keys(contributions.RECONCILE_REASONS),
    ['basis','graph_filter','workbook','aggregate','funding_classification','cycle','unavailable','unexplained']);
  for(const [id,reason] of Object.entries(contributions.RECONCILE_REASONS)){
    assert.ok(reason.trim().length>0,id+' has wording');
    assert.match(reason,/^[a-z]/,'a reason reads as the tail of a sentence');
  }
  // funding_classification is emitted, not just listed: published giving categories vs verified funding classifications.
  const part=contributions.reconcile({totals:{amount:0}},'receipt',{totals:{giving:51000000}},
    {studioCycle:'2023-2024',graphCycle:'2024',funding:'all',fundingClassification:true});
  assert.strictEqual(part.reasonId,'funding_classification');
  assert.strictEqual(part.reason,contributions.RECONCILE_REASONS.funding_classification);
});
