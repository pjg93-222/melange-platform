const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const base=path.join(__dirname,'../../surfaces/staffer_live');
test('official career evidence renders even without a complete directory profile',()=>{
  const make=tag=>({tag,children:[],append(child){this.children.push(child);}});
  const context={window:{},document:{createElement:make}};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(base,'staffer-person-facts.js'),'utf8'),context);
  const root=make('div');context.window.StafferPersonFacts.render(root,{reconciled_career_evidence:[{
    title:'Board member',organization:'Association',date_description:'Announced in 2024; actual tenure unverified',
    source:{url:'https://example.org/roster',page:1}}]});
  const all=[];const visit=e=>{all.push(e);e.children.forEach(visit);};visit(root);
  assert.ok(all.some(e=>e.textContent==='Board member · Association'));
  assert.ok(all.some(e=>e.textContent==='Announced in 2024; actual tenure unverified'));
  assert.ok(all.some(e=>e.tag==='a'&&e.href==='https://example.org/roster'&&e.rel==='noopener noreferrer'));
  assert.equal(all.filter(e=>e.textContent==='Current or latest role').length,0);
});
test('a roster observation does not display as quarterly coworker duration',()=>{
  const source=fs.readFileSync(path.join(base,'staffer-route.js'),'utf8'),context={Date};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function evidenceSummary('),source.indexOf('  function renderRoute(')),context);
  const result=context.evidenceSummary({evidence_summary:'September 2025 roster; duration unknown',start:'',end_exclusive:''});
  assert.equal(result,'September 2025 roster; duration unknown');
  assert.doesNotMatch(result,/NaN|quarter|current coworker/);
});
test('partial career evidence stays separate from dated directory employment',()=>{
  const make=tag=>({tag,children:[],append(child){this.children.push(child);}});
  const context={window:{},document:{createElement:make}};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(base,'staffer-person-facts.js'),'utf8'),context);
  const root=make('div');context.window.StafferPersonFacts.render(root,{
    reconciled_career_evidence:[{title:'Chief advocacy officer',organization:'Association',date_description:'Started April 20, 2026; end unverified'}],
    person_facts:{latest_roles:[],current_roles:[],sections:[{}, {key:'past_employment',label:'Past employment',items:[]}],past_roles:[],identity_source_count:1,identity_note:'Source identity'}
  });
  const all=[];const visit=e=>{all.push(e);e.children.forEach(visit);};visit(root);
  assert.ok(all.some(e=>e.textContent==='Directory and filing work record'));
  assert.ok(all.some(e=>e.textContent==='Dated directory employment history (0)'));
  assert.ok(all.some(e=>/partial career observations are shown separately/.test(e.textContent)));
});
