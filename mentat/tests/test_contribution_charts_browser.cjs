'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
test('all new chart families preserve source groups, typed values and keyboard inspection',{timeout:120000},async t=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});t.after(()=>browser.close());const page=await browser.newPage({viewport:{width:1200,height:800}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<div id="fixture" style="max-width:100%;overflow:auto"></div>');
 for(const file of ['theme.js','chart-math.js','chart-studio-core.js','contribution-plot.js','contribution-charts.js','chart-studio-contributions.js'])await page.addScriptTag({path:path.join(app,file)});
 const result=await page.evaluate(()=>{
  const columns=[...['company','party','month','quarter','date','cycle'].map(id=>({id,name:id,label:id,role:'dimension',type:'category',is_time:['month','quarter','date','cycle'].includes(id)})),...['amount','gross_amount','records','min_amount','q1_amount','median_amount','q3_amount','max_amount','span_days','first_date','last_date'].map(id=>({id,label:id,name:id,role:'measure',unit:id.endsWith('_date')?'date':['records','span_days'].includes(id)?'count':'cents'}))];
  const rows=[{company:'A',party:'D',month:'2024-01',quarter:'2024-Q1',amount:10000,gross_amount:12000,records:3,min_amount:-1000,q1_amount:1000,median_amount:3000,q3_amount:6000,max_amount:9000,first_date:'2024-01-01',last_date:'2024-01-15',span_days:14},{company:'B',party:'R',month:'2024-02',quarter:'2024-Q2',amount:-2000,gross_amount:0,records:1,min_amount:-2000,q1_amount:-2000,median_amount:-2000,q3_amount:-2000,max_amount:-2000,first_date:'2024-02-01',last_date:'2024-02-01',span_days:0}];
  const original=JSON.stringify(rows),reports=[];window.selections=[];
  for(const type of MentatContributionCharts.TYPES){const spec={type,dimensions:['company'],measures:['amount'],columns,labels:true,title:type};let input=rows;
   if(['heatmap','highlight','stacked'].includes(type))spec.dimensions=['company','party'];
   if(['combination','dual-line'].includes(type)){spec.dimensions=['month'];spec.measures=['amount','records'];}
   if(type==='bullet')spec.measures=['amount','gross_amount'];
   if(type==='gantt')spec.measures=['span_days'];
   if(type==='box')spec.measures=['median_amount'];
   if(type==='histogram'){spec.dimensions=['amount_bin'];spec.measures=['records'];input=[{amount_bin:'-10000',amount:-2000,records:1},{amount_bin:'10000',amount:10000,records:3}];spec.binWidth=10000;}
   const svg=MentatContributionCharts.render(fixture,input,spec,row=>selections.push(row));
   const marks=[...svg.querySelectorAll('.plot-mark')];for(const mark of marks)mark.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
   reports.push({type,marks:marks.length,sourceMatches:selections.slice(-marks.length).every(r=>input.includes(r)),bad:[...svg.querySelectorAll('*')].some(n=>[...n.attributes].some(a=>/NaN|Infinity/.test(a.value))),svg:new XMLSerializer().serializeToString(svg).includes('role="button"')});
  }
  const fields=MentatStudioContributions.fields(columns),state=MentatStudioCore.createSheet('All');state.columns=[{column:0},{column:1},{column:2}];state.rows=[{column:6},{column:7}];
  const tiles=MentatStudioContributions.showMeEligibility(MentatStudioCore.showMeSelection(state,fields,[]));
  return {reports,unchanged:JSON.stringify(rows)===original,tiles:tiles.map(t=>({id:t.id,enabled:t.enabled,reason:t.reason})),quarter:MentatContributionPlot.timeValue('2024-Q2','quarter')};
 });
 assert.equal(result.unchanged,true);assert.equal(result.reports.length,13);for(const r of result.reports){assert.ok(r.marks>=2,JSON.stringify(r));assert.equal(r.sourceMatches,true);assert.equal(r.bad,false);assert.equal(r.svg,true);}
 assert.equal(result.tiles.length,24);assert.ok(result.tiles.every(t=>t.enabled),JSON.stringify(result.tiles));assert.equal(result.quarter,8097);assert.deepEqual(errors,[]);
});
