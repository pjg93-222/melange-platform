const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'..');
function extract(source,name) {
  const start=source.indexOf(`function ${name}(`);
  assert.ok(start>=0,name);
  const line=source.slice(source.lastIndexOf('\n',start)+1,start);
  const indent=line.match(/^\s*/)[0];
  const end=source.indexOf('\n'+indent+'}',start);
  return source.slice(start,end+indent.length+2);
}
test('Overview exports uncertainty columns with matching headers, including deferred and empty tables',()=>{
  const source=fs.readFileSync(path.join(root,'tools/crypto_workbook_builder.mjs'),'utf8');
  const ranges=new Map();
  const sheet={getRange(address){
    const [a,b]=address.split(':');
    if(b)assert.ok(Number(a.match(/\d+/)[0])<=Number(b.match(/\d+/)[0]),address);
    const result={format:{}};ranges.set(address,result);return result;
  },tables:{add(){return {}}},freezePanes:{freezeRows(){}}};
  const context=vm.createContext({workbook:{worksheets:{getItem(){return sheet}}},
    payload:{cycles:[],metadata:{downstream_rows:1,downstream_superseded_rows:0,downstream_memo_rows:0},
      downstream_activity:[{cycle:2024,unverified_version_rows:7,unresolved_version_rows:2,
        unresolved_version_amount:12.34,repeated_itemization_rows:3,
        filing_records:'[{"amount":100,"source_url":"https://docquery.fec.gov/cgi-bin/fecimg/?1"}]'}],beneficiary_bridge:[]},
    finalizerCycleLabels:new Map([[2024,'2023-2024']]),deferLargeTables:true,deferFormulas:true,
    industry:'Fixture',associatedFundingHeader:'Associated Funding',countFormat:'0',currencyFormat:'$0.00',
    titleBlock(){},styleTable(){},applyIntegrityFormatting(){},excelColumn:n=>String.fromCharCode(64+n)});
  for(const name of ['downstreamActivityValues','beneficiaryBridgeValues','buildDownstreamActivity','buildBeneficiaryBridge'])
    vm.runInContext(extract(source,name),context);
  vm.runInContext('buildDownstreamActivity();buildBeneficiaryBridge()',context);
  const data=ranges.get('A5:AG6').values;
  assert.equal(data[0].length,33);assert.equal(data[1].length,33);
  const record=Object.fromEntries(data[0].map((label,i)=>[label,data[1][i]]));
  assert.match(record['Filing Records'],/docquery\.fec\.gov/);
  assert.equal(record['Unverified Version Rows'],7);
  assert.equal(record['Unresolved Version Amount'],12.34);
  assert.equal(record['Repeated Itemization Rows'],3);
  assert.equal(ranges.get('A5:V5').values.length,1,'empty bridge remains header-only');
});
test('Snapshot pane keeps direction, unknown totals, and incomplete coverage separate',async()=>{
  const source=fs.readFileSync(path.join(root,'app/evidence.js'),'utf8');
  const body={company:'Fixture',committee_finances:[{committee_id:'PAC',cycle:2024,
    regular_ie_total:100,regular_ie_reconciles:null,receipts_as_reported:null}],
    transfers:[{source:'PAC',cycle:2024,candidate_id:'H1',amount:25}],
    candidate_activity:[{committee_id:'PAC',cycle:2024,direction:'Support',committee_spending:100},
      {committee_id:'PAC',cycle:2024,direction:'Unresolved',committee_spending:-5},
      {committee_id:'PAC',cycle:2024,direction:'Contribution / Other',committee_spending:25}],
    downstream_coverage:[{committee_id:'CHILD',cycle:2024,status:'collection_limit'}],
    temporal_routes:[{cycle:2024,status:'depth_limit'}]};
  const pane={id:'fixture',committee:'PAC',cycle:'2024'};
  const context=vm.createContext({snapshotBodies:new Map([['fixture',body]]),pane,NOT_REPORTED:'Not reported'});
  vm.runInContext('async '+extract(source,'loadSnapshotPane'),context);
  await vm.runInContext('loadSnapshotPane(pane)',context);
  assert.equal(pane.totals[0].amount,null);
  assert.equal(pane.legCount,3,'candidate payment counted once');
  assert.equal(pane.counterparties.find(r=>r.name.endsWith('unknown direction')).amount,-5);
  assert.equal(pane.counterparties.some(r=>r.name.endsWith('oppose')),false);
  assert.match(pane.warnings.join(' '),/unavailable.*collection_limit.*depth/);
  pane.cycle='2022';
  await assert.rejects(vm.runInContext('loadSnapshotPane(pane)',context),/No saved financial coverage/);
});
