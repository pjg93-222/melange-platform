'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const plot=require('../app/contribution-plot.js');

// Minimal DOM fixture exercises the generated coordinates, labels and selection
// handlers; browser integration tests independently exercise actual SVG rendering.
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.events={};this._text='';}
 setAttribute(key,value){this.attributes[key]=String(value);}
 getAttribute(key){return this.attributes[key]??null;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;this._text='';}
 addEventListener(type,handler){this.events[type]=handler;}
 set textContent(value){this._text=String(value??'');this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent??String(c)).join('');}
}
const document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text})};
function draw(rows,spec,onSelect){const previous=global.document;global.document=document;try{const container=new Element('div');plot.render(container,rows,spec,onSelect);return container;}finally{global.document=previous;}}
function all(node,predicate){return [node,...node.children.flatMap(child=>all(child,predicate))].filter(predicate);}
const marks=tree=>all(tree,node=>node.attributes.class==='plot-mark');
const spec={type:'bar',dimensions:['company'],measures:['amount'],columns:[{id:'amount',name:'Net giving',unit:'cents'},{id:'records',name:'Observations',unit:'count'},{id:'company',name:'Company'}]};

test('currency uses API cents while counts and missing values retain their meaning',()=>{
 assert.equal(plot.format(125,'amount'),'$1.25');
 assert.equal(plot.format(-1250,'refunds'),'-$12.50');
 assert.equal(plot.format(0,'amount'),'$0.00');
 assert.equal(plot.format(12345,'records'),'12,345');
 assert.equal(plot.format(null,'amount'),'—');
 assert.equal(plot.format(NaN,'amount'),'—');
 assert.equal(plot.format(120,'custom',[{id:'custom',unit:'usd_cents'}]),'$1.20');
 assert.match(plot.format(100000000,'amount',[],true),/^\$1(?:\.0)?M$/);
});

test('date and cycle parsing rejects invalid calendars and preserves actual elapsed time',()=>{
 assert.equal(plot.timeValue('2024-02-30','date'),null);
 assert.equal(plot.timeValue('2023-02-29','date'),null);
 assert.notEqual(plot.timeValue('2024-02-29','date'),null);
 assert.equal(plot.timeValue('2023-2024','cycle'),1012);
 assert.equal(plot.timeValue('2024-2026','cycle'),null);
 assert.equal(plot.timeValue('2025','cycle'),null);
 assert.equal(plot.timeValue('2024-13','month'),null);
 assert.equal(plot.timeValue('2024-03-04','date')-plot.timeValue('2024-03-01','date'),3);
});

test('timelines leave globally absent periods and explicit missing measures as gaps',()=>{
 const config={...spec,type:'line',dimensions:['year']};
 const rows=[{year:'2022',amount:100},{year:'2024',amount:300},{year:'2025',amount:null},{year:'2026',amount:500}];
 const result=plot.timeline(rows,config,'amount');
 assert.deepEqual(result.series[0].segments.map(s=>s.map(item=>item.at)),[[2022],[2024],[2026]]);
 const cycles=plot.timeline([{cycle:'2019-2020',amount:1},{cycle:'2021-2022',amount:2},{cycle:'2025-2026',amount:3}],{...config,dimensions:['cycle']},'amount');
 assert.deepEqual(cycles.series[0].segments.map(s=>s.length),[2,1]);
 assert.throws(()=>plot.timeline([{year:'2022',amount:100},{year:'2022',amount:200}],config,'amount'),/duplicate aggregate groups/);
});

test('line coordinates use real dates and paths never bridge missing days',()=>{
 const rows=[{date:'2024-01-01',amount:100},{date:'2024-01-02',amount:200},{date:'2024-01-05',amount:300}];
 const tree=draw(rows,{...spec,type:'line',dimensions:['date']});
 assert.deepEqual(marks(tree).map(mark=>Number(mark.attributes.cx)),[100,310,940]);
 const paths=all(tree,n=>n.tagName==='path'&&n.attributes['stroke-width']==='2.5');
 assert.equal(paths.length,2);
 assert.equal((paths[0].attributes.d.match(/L/g)||[]).length,1);
 assert.equal((paths[1].attributes.d.match(/L/g)||[]).length,0);
});

test('separate measures retain individual axes and negative bars extend from zero',()=>{
 const rows=[{company:'A',amount:-200,records:3},{company:'B',amount:100,records:7}];
 const tree=draw(rows,{...spec,measures:['amount','records'],labels:true});
 const panels=all(tree,n=>n.tagName==='g'&&n.attributes.transform);
 assert.equal(panels.length,2);
 assert.deepEqual(panels.map(panel=>marks(panel).length),[2,2]);
 const [negative,positive]=marks(panels[0]);
 assert.equal(Number(negative.attributes.x),255);
 assert.ok(Math.abs(Number(negative.attributes.width)-2*Number(positive.attributes.width))<1e-9);
 assert.ok(Math.abs(Number(negative.attributes.x)+Number(negative.attributes.width)-Number(positive.attributes.x))<1e-9);
 assert.match(panels[0].textContent,/-\$2/);
 assert.match(panels[1].textContent,/Observations/);
});

test('scatter uses only complete pairs for axes and keeps selection identity',()=>{
 const a={company:'A',amount:100,records:10},b={company:'B',amount:200,records:20};
 const rows=[a,b,{company:'Incomplete',amount:1e12,records:null}];
 assert.deepEqual(plot.scatterPairs(rows,['amount','records']),[a,b]);
 let selected=null;const tree=draw(rows,{...spec,type:'scatter',measures:['amount','records'],labels:true},row=>selected=row);
 const points=marks(tree);assert.equal(points.length,2);
 assert.deepEqual(points.map(point=>Number(point.attributes.cx)),[100,935]);
 points[1].events.click();assert.equal(selected,b);
 points[0].events.keydown({key:'Enter',preventDefault(){}});assert.equal(selected,a);
 assert.match(tree.textContent,/1 groups are missing one or both measures/);
 assert.match(tree.textContent,/A/);
});

test('unknown dates are disclosed and table preserves their source values',()=>{
 const rows=[{date:null,amount:100},{date:'2024-01-01',amount:200}];
 const tree=draw(rows,{...spec,type:'line',dimensions:['date']});
 assert.equal(marks(tree).length,1);
 assert.match(tree.textContent,/1 groups have missing or invalid dates/);
 const table=draw(rows,{...spec,type:'table',dimensions:['date']});
 assert.match(table.textContent,/Unknown/);assert.match(table.textContent,/\$1\.00/);
});

test('multiple timeline series get a consistent legend even without an explicit Colour',()=>{
 const rows=[{year:'2022',company:'B',amount:200},{year:'2022',company:'A',amount:100},{year:'2023',company:'A',amount:150}];
 const tree=draw(rows,{...spec,type:'line',dimensions:['year','company']});
 const legend=all(tree,node=>node.attributes['data-legend']==='series')[0];
 assert.ok(legend);assert.equal(legend.textContent,'AB');
 assert.ok(tree.children.find(node=>node.tagName==='svg').children.includes(legend),'legend belongs to the exported SVG');
 const result=plot.timeline(rows,{...spec,dimensions:['year','company']},'amount');
 assert.deepEqual(result.series.map(s=>s.label),['A','B']);
});

test('extents handle dense values, tiny spans, zero and signed ranges without overflow',()=>{
 assert.deepEqual(plot.extent(Array.from({length:200000},(_,i)=>i)),[0,199999]);
 assert.deepEqual(plot.extent([-100,20]),[-100,20]);
 assert.deepEqual(plot.extent([0,0],false),[-1,1]);
 const tiny=plot.extent([1e-9,2e-9],false);assert.deepEqual(tiny,[1e-9,2e-9]);
 assert.throws(()=>plot.extent([-Number.MAX_VALUE,Number.MAX_VALUE]),/numeric range/);
});

test('invalid encodings are rejected before a misleading chart can render',()=>{
 assert.throws(()=>plot.validate([],{...spec,type:'pie'}),/supported chart/);
 assert.throws(()=>plot.validate([],{...spec,color:'party'}),/queried dimensions/);
 assert.throws(()=>plot.validate([],{...spec,measures:['amount','amount']}),/distinct named measures/);
 assert.throws(()=>plot.validate([{amount:'100'}],spec),/nonnumeric/);
 // Paul, 2026-09-23: "within the Chart Studio make sure everything is freely flexible, that all graph
 // types properly register and nothing is logically constrained". Colour may repeat the time axis
 // (ENGINE CONTRACT V07): no refusal, and each period takes its own colour.
 assert.doesNotThrow(()=>plot.validate([],{...spec,type:'line',dimensions:['cycle'],color:'cycle'}));
 const byPeriod=marks(draw([{cycle:'2021-2022',amount:1},{cycle:'2023-2024',amount:2}],{...spec,type:'line',dimensions:['cycle'],color:'cycle'}));
 assert.equal(byPeriod.length,2);assert.notEqual(byPeriod[0].attributes.fill,byPeriod[1].attributes.fill);
});

test('original Marks shelves draw requested labels, tooltip measures and scatter size',()=>{
 const rows=[{company:'A',amount:100,records:2,gross_amount:150},{company:'B',amount:200,records:8,gross_amount:250}];
 const tree=draw(rows,{...spec,type:'scatter',measures:['amount','records'],size:'gross_amount',labelFields:['company'],tooltip:['gross_amount'],labels:true});
 const points=marks(tree);assert.ok(Number(points[1].attributes.r)>Number(points[0].attributes.r));assert.match(points[0].textContent,/gross amount: \$1.50/);assert.match(tree.textContent,/A/);
 // Paul, 2026-09-23: "nothing is logically constrained". Size is no longer refused off a scatter
 // (ENGINE CONTRACT V04): on bars it sets the bar's thickness, so the larger size draws the thicker bar.
 assert.doesNotThrow(()=>plot.validate(rows,{...spec,size:'records'}));
 const sizedBars=marks(draw(rows,{...spec,size:'records'}));
 assert.equal(sizedBars.length,2);assert.ok(Number(sizedBars[1].attributes.height)>Number(sizedBars[0].attributes.height));
});
test('average and median lines identify the displayed marks rather than source statistics',()=>{
 const tree=draw([{company:'A',amount:100},{company:'B',amount:300},{company:'C',amount:2000}],{...spec,references:{average:true,median:true}});
 assert.equal(all(tree,node=>node.attributes['data-reference']==='average').length,1);assert.match(tree.textContent,/Mean of shown marks/);assert.match(tree.textContent,/Median of shown marks: \$3/);
});
