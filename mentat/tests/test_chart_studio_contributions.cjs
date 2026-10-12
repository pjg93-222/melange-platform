'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');
const studio=require('../app/chart-studio-contributions.js'),core=require('../app/chart-studio-core.js');
const catalog=[...['company','cycle','date','year','zip5','state','recipient'].map(id=>({id,label:id,role:'dimension'})),...['amount','records','contributors','average_amount','gross_amount'].map(id=>({id,label:id,role:'measure',unit:id==='records'||id==='contributors'?'count':'cents'}))];
const columns=studio.fields(catalog),index=id=>columns.find(f=>f.id===id).index,pill=(id,aggregation='sum')=>({column:index(id),aggregation});
function sheet(){const s=core.createSheet('A');s.columns=[pill('company')];s.rows=[pill('amount')];s.marks.type='bar';return s;}
const compile=s=>studio.compile(s,columns,{dataset:'Banking',basis:'receipt'});
// Paul, 2026-09-23, quoted by the tests below that changed with his words: "Additionally, within the Chart
// Studio make sure everything is freely flexible, that all graph types properly register and nothing is
// logically constrained, it needs to be utterly maluable."
const PAUL='nothing is logically constrained, it needs to be utterly maluable';
test('original shelves query source observations once and preserve independent dimensions',()=>{const s=sheet();s.rows.push(pill('state'));s.marks.detail=[pill('recipient')];s.marks.color=pill('cycle');const r=compile(s);assert.deepEqual(r.query.dimensions,['company','state','cycle','recipient']);assert.deepEqual(r.query.measures,['amount']);assert.equal(r.query.basis,'receipt');assert.equal(r.query.limit,60);});
test('measure aggregation requests actual source statistics, without summing group means or distincts',()=>{const s=sheet();s.rows=[pill('amount','mean'),pill('contributors')];assert.deepEqual(compile(s).query.measures,['average_amount','contributors']);s.rows=[pill('contributors','mean')];/* Paul: "nothing is logically constrained" - a source statistic keeps its own definition and says so (census B-S24) instead of refusing the sheet. */const r=compile(s);assert.deepEqual(r.query.measures,['contributors']);assert.match(r.notes.join(' '),/computed directly from source observations, so it keeps its source statistic/);});
// KEEP-RULED (option A): a ZIP map still queries state beside the ZIP. Paul: "nothing is logically constrained" -
// every pill stays movable afterwards: another dimension joins the query and each mark combines its groups at its
// place (census B-E36), where it used to refuse the sheet.
test('ZIP map queries state eligibility and keeps every other pill movable',()=>{const s=sheet();s.marks.type='map';s.columns=[pill('zip5')];const r=compile(s);assert.deepEqual(r.query.dimensions,['zip5','state']);assert.equal(r.query.limit,100000);s.columns.push(pill('company'));const moved=compile(s);assert.equal(moved.spec.type,'map');assert.deepEqual(moved.query.dimensions,['zip5','company','state']);assert.match(moved.notes.join(' '),/Each map mark combines every company group at its place/);});
test('money filters are cents; measure filters can run after grouping',()=>{const s=sheet();s.filters=[{column:index('amount'),op:'gte',value:'12.50'},{column:index('contributors'),op:'gte',value:'2',stage:'aggregate'},{column:index('zip5'),op:'equals',value:'01234'}];const r=compile(s);assert.deepEqual(r.query.filters,[{field:'amount',op:'gte',value:1250},{field:'zip5',op:'eq',value:'01234'}]);assert.deepEqual(r.query.aggregate_filters,[{field:'contributors',op:'gte',value:2}]);assert.throws(()=>studio.filterValue('1.001',columns[index('amount')]),/two decimal/);});
test('Pages limits source observations and Size/Label/Tooltip request truthful encodings',()=>{const s=sheet();s.pages=pill('cycle');s.pageMember='2023-2024';s.rows.push(pill('contributors'));s.marks.type='circle';s.marks.size=pill('records');s.marks.label=pill('state');s.marks.tooltip=[pill('gross_amount')];const r=compile(s);assert.equal(r.spec.type,'scatter');assert.equal(r.spec.size,'records');assert.deepEqual(r.spec.labelFields,['state']);assert.deepEqual(r.query.dimensions,['company','state']);assert.deepEqual(r.query.measures,['amount','contributors','gross_amount','records']);assert.deepEqual(r.query.filters,[{field:'cycle',op:'eq',value:'2023-2024'}]);});
test('style-only changes do not change query identity and temporal queries preserve complete periods',()=>{const s=sheet(),before=compile(s).query;s.fit='width';s.showLabels=true;s.analytics.average=true;assert.deepEqual(compile(s).query,before);s.columns=[pill('date')];s.analytics.transform='annual';s.analytics.period='cycle';assert.deepEqual(compile(s).query.dimensions,['cycle']);assert.equal(compile(s).query.limit,100000);});
test('distributions query individual source statistics and monetary splitting stays prohibited',()=>{const s=sheet();s.marks.type='histogram';assert.deepEqual(compile(s).query.dimensions,['amount_bin']);assert.deepEqual(compile(s).query.measures,['records','amount']);s.marks.type='box';assert.deepEqual(compile(s).query.measures,['min_amount','q1_amount','median_amount','q3_amount','max_amount']);s.marks.type='bar';s.params.splitMulti=true;/* Paul: "nothing is logically constrained" - verified records are never multi-valued, so Split is ignored with a note (census B-S34) rather than refusing the sheet; money is still never split. */const split=compile(s);assert.deepEqual(split.query.dimensions,['company']);assert.match(split.notes.join(' '),/verified source records are never multi-valued, so it is not used here/);});
test('verified Show Me separates categorical circles from two-measure scatter and enables source distributions',()=>{
 const one=core.showMeSelection(sheet(),columns,[]),verified=studio.showMeEligibility(one),worksheet=core.showMeEligibility(one);
 // Paul: "...all graph types properly register and nothing is logically constrained": every tile is enabled; what
 // a tile prefers and the shelves lack is `fits:false` with the fields a click will pick.
 assert.equal(verified.find(t=>t.id==='circle-views').enabled,true);assert.equal(verified.find(t=>t.id==='side-by-side-circles').enabled,true);assert.equal(verified.find(t=>t.id==='side-by-side-circles').fits,false);assert.equal(verified.find(t=>t.id==='scatter-plot').enabled,true);assert.equal(verified.find(t=>t.id==='scatter-plot').fits,false);assert.match(verified.find(t=>t.id==='scatter-plot').fill,/picks 1 measure/);
 assert.ok(verified.every(t=>t.enabled&&t.reason===''),PAUL);
 for(const id of ['histogram','box-and-whisker']){assert.equal(verified.find(t=>t.id===id).enabled,true);assert.match(verified.find(t=>t.id===id).note,/individual/);assert.equal(worksheet.find(t=>t.id===id).enabled,true);}
 assert.equal(worksheet.find(t=>t.id==='circle-views').enabled,true);
});
test('circle tiles create categorical circles while scatter keeps both measures and exact dimensions',()=>{
 const s=sheet();s.rows.push(pill('contributors'));s.columns.push(pill('state'));
 for(const [id,type] of [['circle-views','dot'],['side-by-side-circles','series-dot'],['scatter-plot','scatter']]){const next=studio.showMeApply(id,s,columns,[]),result=compile(next);assert.equal(result.spec.type,type);assert.deepEqual(result.query.measures,['amount','contributors']);assert.deepEqual(result.query.dimensions,['company','state']);assert.deepEqual(next.columns,s.columns);assert.deepEqual(next.rows,s.rows);}
});
test('Show Me preserves existing shelves, mean and filters for treemaps and horizontal bars',()=>{
 const s=sheet();s.rows=[pill('amount','mean')];s.columns.push(pill('state'));s.filters=[{column:index('cycle'),op:'equals',value:'2023-2024'}];
 for(const id of ['treemap','horizontal-bars']){const next=studio.showMeApply(id,s,columns,[]);assert.deepEqual(next.columns,s.columns);assert.deepEqual(next.rows,s.rows);assert.deepEqual(next.filters,s.filters);assert.deepEqual(compile(next).query.measures,['average_amount']);}
});
test('reconcile names why Graph and Studio differ, and says so plainly when they do not',()=>{
 const base={totals:{amount:1209041200},comparison:{receipt:1209041200},provenance:{workbook_sha256:'w'.repeat(64)},reconciliation:{aggregate_filters_applied:false}};
 const graph={totals:{giving:12090412},reports:[{name:'Banking.xlsx',sha256:'w'.repeat(64)}]};
 const clean={tier:'',funding:'all'},wider=amount=>({...base,totals:{amount}});
 const agree=studio.reconcile(base,'receipt',graph,clean);
 assert.equal(agree.agree,true);assert.equal(agree.graphCents,1209041200);assert.equal(agree.studioCents,1209041200);
 assert.equal(agree.differenceCents,0);assert.equal(agree.reason,'');
 // A different basis is a different question, so the strip must not call it agreement.
 const basis=studio.reconcile(wider(990000000),'original_observed',graph,clean);
 assert.equal(basis.agree,false);assert.equal(basis.reasonId,'basis');assert.match(basis.reason,/uses a different basis than Graph/);
 // A Graph employee-level or giving-type filter has no verified field, so the Studio total is the wider one.
 assert.equal(studio.reconcile(wider(1300000000),'receipt',graph,{tier:'Chief',funding:'all'}).reasonId,'graph_filter');
 assert.equal(studio.reconcile(wider(1300000000),'receipt',graph,{tier:'',funding:'personal'}).reasonId,'graph_filter');
 // Two different workbooks are not reconcilable by arithmetic.
 assert.equal(studio.reconcile(wider(1300000000),'receipt',{...graph,reports:[{sha256:'z'.repeat(64)}]},clean).reasonId,'workbook');
 assert.equal(studio.reconcile({...wider(1300000000),reconciliation:{aggregate_filters_applied:true}},'receipt',graph,clean).reasonId,'aggregate');
 const unexplained=studio.reconcile(wider(1300000000),'receipt',graph,clean);
 assert.equal(unexplained.reasonId,'unexplained');assert.equal(unexplained.differenceCents,90958800);
 // No Graph total is an absence, never a difference to explain.
 assert.equal(studio.reconcile(base,'receipt',null,clean).reasonId,'unavailable');
 assert.equal(studio.reconcile(base,'receipt',{totals:{}},clean).reasonId,'unavailable');
 assert.equal(studio.reconcile({totals:{}},'receipt',graph,clean).reasonId,'unavailable');
});
test('drill-through keeps exact ZIP and all selected raw state variants',()=>{const query={dimensions:['zip5','state'],filters:[{field:'company',op:'eq',value:'A'}]};assert.deepEqual(studio.markFilters(query,{zip5:'01234',state:['MA',' ma ']}),[...query.filters,{field:'zip5',op:'eq',value:'01234'},{field:'state',op:'in',value:['MA',' ma ']}]);});
test('state-only map marks retain exact state filters and unidentified marks never select every record',()=>{const selection=studio.mapSelection({kind:'state',state:'MA',states:['MA',' ma '],amount_cents:125});assert.deepEqual(studio.markFilters({dimensions:['state'],filters:[]},selection),[{field:'state',op:'in',value:['MA',' ma ']}]);assert.deepEqual(studio.mapSelection({state:'MA'}),{state:['MA']});assert.throws(()=>studio.mapSelection({amount_cents:100}),/no geographical/);});
test('CSV exports cents as USD and preserves source, query and filtering provenance',()=>{const result={columns:[{id:'zip5'},{id:'amount',unit:'cents'}],rows:[{zip5:'01234',amount:-1250}],provenance:{workbook_sha256:'workbook',database_sha256:'source',index_token:'index',query_engine_sha256:'engine'}};const value=studio.csv(result,{basis:'receipt',filters:[],aggregate_filters:[]});assert.match(value,/"01234","-12.5"/);assert.match(value,/database_sha256/);assert.match(value,/query_engine_sha256/);assert.match(value,/"receipt","workbook","source","index","engine"/);assert.equal(studio.sameSource(result.provenance,{...result.provenance,index_token:'changed'}),false);});
// Paul: "...all graph types properly register and nothing is logically constrained". Every mark the menu offers
// compiles from every arrangement a reader can make; only an empty sheet asks for a field.
test('every verified mark compiles from every arrangement, and only an empty sheet asks for a field',()=>{
 const arrangements={one:()=>{},dimOnly:s=>{s.rows=[];},measureOnly:s=>{s.columns=[];},twoDims:s=>{s.columns.push(pill('cycle'));},twoMeasures:s=>{s.rows.push(pill('records'));},threeMeasures:s=>{s.rows.push(pill('records'),pill('contributors'));},colorMeasure:s=>{s.marks.color=pill('records');},colorDim:s=>{s.marks.color=pill('state');},size:s=>{s.marks.size=pill('records');},sizeDim:s=>{s.marks.size=pill('state');},detailMeasure:s=>{s.marks.detail=[pill('contributors')];},geography:s=>{s.columns=[pill('zip5')];},filterContains:s=>{s.filters=[{column:index('amount'),op:'contains',value:'1'}];},every:s=>{s.params.maxGroups='all';},split:s=>{s.params.splitMulti=true;},pageMeasure:s=>{s.pages=pill('records');},period:s=>{s.columns=[pill('cycle'),pill('company')];s.analytics.transform='forecast';}};
 for(const type of [...studio.MARK_TYPES])for(const [name,arrange] of Object.entries(arrangements)){const s=sheet();s.marks.type=type;arrange(s);assert.doesNotThrow(()=>compile(s),type+' / '+name+': '+PAUL);}
 assert.throws(()=>compile(core.createSheet('E')),/^Error: Add a dollar or count measure to Rows or Columns\.$/);
});
test('a map without a place on the shelves is drawn as bars and says so, and it shades by any measure',()=>{
 const s=sheet();s.marks.type='map';const r=compile(s);assert.equal(r.spec.type,'bar');assert.match(r.notes.join(' '),/neither is on a shelf, so this view is drawn as bars/);
 s.columns=[pill('state')];s.rows=[pill('records')];const counted=compile(s);assert.equal(counted.spec.type,'map');assert.deepEqual(counted.spec.measures,['records']);
 assert.ok(counted.query.measures.includes('amount'),'the selected amount rides along for the coverage line');assert.match(counted.notes.join(' '),/The map shades by records/);
});
test('dimensions alone count observations; Colour, Size and Detail take measures; All asks for every group',()=>{
 const s=sheet();s.rows=[];const counted=compile(s);assert.deepEqual(counted.query.measures,['records']);assert.match(counted.notes.join(' '),/counts source observations/);
 const shaded=sheet();shaded.marks.color=pill('records');const r=compile(shaded);assert.equal(r.spec.colorMeasure,'records');assert.equal(r.spec.color,'');assert.ok(r.query.measures.includes('records'));
 const sized=sheet();sized.marks.size=pill('records');assert.equal(compile(sized).spec.size,'records');assert.equal(compile(sized).spec.type,'bar');
 const every=sheet();every.params.maxGroups='all';assert.equal(compile(every).query.limit,100000);assert.match(compile(every).notes.join(' '),/up to 100,000 groups per page/);
 const wide=sheet();wide.params.maxGroups=5000;assert.equal(compile(wide).query.limit,5000,'the old client cap of 1,000 is gone');
 const detail=sheet();detail.marks.detail=[pill('contributors')];assert.deepEqual(compile(detail).spec.tooltip,['contributors']);
});
test('timelines, circles, heat maps and dual axes draw from whatever is placed',()=>{
 const line=sheet();line.marks.type='line';const cat=compile(line);assert.equal(cat.spec.categorical,true,'a line over companies is drawn in order, not as time');assert.deepEqual(cat.query.dimensions,['company']);
 const moved=sheet();moved.marks.type='line';moved.columns=[pill('company'),pill('cycle')];const m=compile(moved);assert.deepEqual(m.query.dimensions,['cycle','company'],'the time field leads the axis');assert.ok(!m.spec.categorical);
 const circle=sheet();circle.marks.type='circle';assert.equal(compile(circle).spec.type,'dot');
 const heat=sheet();heat.marks.type='heatmap';assert.equal(compile(heat).spec.type,'heatmap');assert.deepEqual(compile(heat).query.dimensions,['company']);
 const bullet=sheet();bullet.marks.type='bullet';assert.deepEqual(compile(bullet).spec.measures,['amount','records']);
 const combo=sheet();combo.marks.type='combination';const c=compile(combo);assert.equal(c.spec.type,'bar');assert.equal(c.spec.dual,true);assert.deepEqual(c.spec.measures,['amount','records']);
 const timed=sheet();timed.marks.type='dual-line';timed.columns=[pill('cycle')];timed.rows=[pill('amount'),pill('contributors')];const t=compile(timed);assert.equal(t.spec.type,'dual-line');assert.ok(!t.spec.dual);
 const period=sheet();period.columns=[pill('cycle'),pill('company')];period.analytics.transform='yoy';const p=compile(period);assert.equal(p.transform,'yoy');assert.equal(p.spec.type,'bar','the chosen mark is kept');assert.deepEqual(p.query.dimensions,['cycle','company'],'the other dimension is a series');
});
test('filters never refuse the sheet: a number cannot be text-matched and a long member list is trimmed, each with a note',()=>{
 const s=sheet();s.filters=[{column:index('amount'),op:'contains',value:'5'},{column:index('company'),op:'in',value:Array.from({length:1200},(_,i)=>'C'+i)},{column:index('company'),op:'notin',value:['C1']}];
 const r=compile(s);assert.match(r.notes.join(' '),/cannot be matched as text, so its Contains filter is left out/);assert.match(r.notes.join(' '),/sends its first 1,000 of 1200 members/);
 assert.equal(r.query.filters.find(f=>f.op==='in').value.length,1000);assert.ok(r.query.filters.some(f=>f.op==='ne'&&f.value==='C1'),'two filters on one field both apply');
 assert.throws(()=>studio.clauses({op:'contains',value:'5'},columns[index('amount')]),/Numeric filters/,'without a notes list the clause builder still refuses, for the member-list callers');
});
