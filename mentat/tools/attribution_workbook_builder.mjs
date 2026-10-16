import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require=createRequire(import.meta.url);
const modules=process.env.MENTAT_NODE_MODULES || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
// Desktop builds use the portable writer; existing artifact-tool builds keep their default.
const xlsxWriter=(process.env.MENTAT_XLSX_WRITER??'').trim().toLowerCase()||'artifact';
if(!['artifact','openpyxl'].includes(xlsxWriter)){
  throw new Error(`MENTAT_XLSX_WRITER must be artifact or openpyxl, got ${xlsxWriter}`);
}
const {Workbook,SpreadsheetFile}=xlsxWriter==='openpyxl'
  ? await import('./openpyxl_workbook_shim.mjs')
  : await import(pathToFileURL(require.resolve('@oai/artifact-tool',{paths:[modules]})).href);
const [input,output]=process.argv.slice(2);
const data=JSON.parse(await fs.readFile(input,'utf8'));
const workbook=Workbook.create();
const money='$#,##0.00;[Red]($#,##0.00);$0.00';
function sheet(name,headers,rows,description){
  const value=workbook.worksheets.add(name),last=String.fromCharCode(64+headers.length);
  value.getRange(`A1:${last}1`).merge();value.getRange('A1').values=[[name]];
  value.getRange(`A1:${last}1`).format={fill:'#123145',font:{bold:true,color:'#FFFFFF',size:19},rowHeight:36};
  value.getRange(`A2:${last}3`).merge();value.getRange('A2').values=[[description]];
  value.getRange(`A2:${last}3`).format.wrapText=true;
  const body=rows.length?rows:[headers.map(()=> '')];
  value.getRange(`A5:${last}${5+body.length}`).values=[headers,...body];
  const table=value.tables.add(`A5:${last}${5+body.length}`,true,name.replaceAll(' ','')+'Table');
  table.style='TableStyleMedium2';
  value.getRange(`A1:${last}1`).format.columnWidth=25;
  value.getRange(`A5:${last}5`).format.wrapText=true;
  value.getRange(`A5:${last}5`).format.rowHeight=42;
  value.freezePanes.freezeRows(5);
  return value;
}
// A cycle whose transfer index was not current arrives with null conditional values
// (enrich_downstream.py). They stay blank cells: blank is not traced, 0 would claim it was.
const untraced=data.rows.some(r=>r.estimated_non_opposition==null);
const estimates=sheet('Candidate Funding',
  ['Industry','Cycle','Candidate ID','Candidate','Party','Direct Observed','Conditional Non-opposition','Conditional Opposition','Direct + Non-opposition Estimate'],
  data.rows.map(r=>[r.industry,r.cycle,r.candidate_id,r.candidate_name,r.party,r.direct,r.estimated_non_opposition??'',r.estimated_opposition??'',r.direct_plus_estimated_non_opposition??'']),
  'Conditional estimates are not traced dollars or lower bounds. Opposition remains against its named target and is excluded from the combined non-opposition estimate. Sorted within each industry and cycle.'
  +(untraced?' Blank conditional values: that industry and cycle had no current transfer index, so nothing was traced there (see Data Quality). Blank is not zero.':''));
estimates.getRange(`F6:I${5+Math.max(1,data.rows.length)}`).format.numberFormat=money;
// Only traced rows carry the combined formula: F+G over a blank G would print Direct as if
// the conditional estimate were 0. An all-traced book gets the one call it always got.
for(let start=0;start<data.rows.length;){
  const traced=data.rows[start].estimated_non_opposition!=null;let end=start;
  while(end<data.rows.length&&(data.rows[end].estimated_non_opposition!=null)===traced)end++;
  if(traced)estimates.getRange(`I${start+6}:I${end+5}`).formulas=data.rows.slice(start,end).map((_,i)=>[`=F${start+i+6}+G${start+i+6}`]);
  start=end;
}
estimates.getRange('D1').format.columnWidth=37;
estimates.getRange('B1').format.columnWidth=10;
estimates.getRange('C1').format.columnWidth=16;
estimates.getRange('E1').format.columnWidth=9;
estimates.getRange('I1').format.columnWidth=30;
const coverage=sheet('Root Coverage',
  ['Industry','Cycle','Funded Committee','Model Funding','Estimated Candidate Activity','Unresolved','Unresolved Reasons'],
  data.coverage.map(r=>[r.industry,Number(r.cycle),r.root,r.model_funding,r.estimated_candidate_activity,r.unresolved,r.unresolved_reasons]),
  'Each root conserves nonnegative net funding: estimated candidate activity plus unresolved funding. This is an arithmetic check, not proof of complete collection.');
coverage.getRange(`D6:F${5+Math.max(1,data.coverage.length)}`).format.numberFormat=money;
sheet('Data Quality',['Industry','Check','Severity','Passed','Detail'],
  data.warnings.map(r=>[r.industry,r.id,r.severity,r.passed==='True',r.detail]),
  'False results require review. Older collection checkpoints can remain unverified even when the corrected workbook builds successfully.');
sheet('Source Receipts',['Industry','Audit File','SHA-256'],data.sources.map(r=>[r.industry,r.file,r.sha256]),
  'Full transfer paths, allocations, classification review and source timestamps are in the companion CSV files. Hashes identify the exact inputs used here.');
sheet('Methodology',['Topic','Method'],[
 ['Observed funding','Included source contributions to uniquely linked principal or authorized candidate committees. This retains the report receipt ontology and is not an economic deduplication of conduit legs.'],
 ['Conditional allocation','Allocate nonnegative net root funding proportionally across positive observed candidate activity and outgoing 24G/24K committee cash transfers. Stop after three transfers; expose loops and unknown outflows as unresolved.'],
 ['Opposition','Retain the named target and report separately. Never assign opposition dollars to a rival candidate.'],
 ['Limitations','Negative activity does not enter allocation weights. Unobserved operating expenses and retained cash are not modelled. Estimates are neither causal tracing nor lower bounds.'],
 ['Period','Each two-year filing cycle is separate. Refreshed FEC bulk files do not imply that historical company/employer searches are current or exhaustive.'],
 ['Transfers','Only outgoing cash transfer codes 24G and 24K enter the graph. Incoming mirrors and independent expenditures are not counted again as transfers. Candidate committee transfers already represented as candidate activity are not traversed again.'],
 ['Classification','Hybrid Carey committees are separate from Super PACs. The earmark conduits ActBlue and WinRed are their own Conduit category. Registered ordinary PACs remain separate as Other PACs. Unresolved classifications stay visible.'],
 ['Entity matching','Results depend on supplied canonical names, aliases and source fields. Missing or erroneous employer data remains a coverage limit.']
], 'Read this before interpreting any estimate. All values are USD.');
workbook.worksheets.getItem('Methodology').getRange('B1').format.columnWidth=120;
workbook.worksheets.getItem('Methodology').getRange('B6:B13').format.wrapText=true;
workbook.worksheets.getItem('Methodology').getRange('A6:B13').format.rowHeight=85;
workbook.worksheets.getItem('Data Quality').getRange('E1').format.columnWidth=100;
workbook.worksheets.getItem('Data Quality').getRange(`E6:E${5+Math.max(1,data.warnings.length)}`).format.wrapText=true;
workbook.worksheets.getItem('Data Quality').getRange(`A6:E${5+Math.max(1,data.warnings.length)}`).format.rowHeight=70;
workbook.worksheets.getItem('Source Receipts').getRange('B1').format.columnWidth=100;
workbook.worksheets.getItem('Source Receipts').getRange('C1').format.columnWidth=65;
workbook.inspect=async()=>({ndjson:'',truncated:false,metadata:{notices:[]}});
await (await SpreadsheetFile.exportXlsx(workbook)).save(output);
console.log(`OUTPUT=${output}`);
