"use strict";
const fs=require('node:fs/promises');
const path=require('node:path');
const {createRequire}=require('node:module');
const {pathToFileURL}=require('node:url');
const crypto=require('node:crypto');
async function main(){
  const plan=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
  const loader=createRequire(path.join(process.env.ARTIFACT_TOOL_NODE_MODULES,'__loader.cjs'));
  const {Workbook,SpreadsheetFile}=await import(pathToFileURL(loader.resolve('@oai/artifact-tool')).href);
  const wb=Workbook.create();
  const knownSheets=new Set(plan.views.map(v=>v.name));
  const supportedFormula=value=>{
    const refs=[...value.matchAll(/'([^']+)'!/g)].map(m=>m[1]);
    if(refs.some(name=>!knownSheets.has(name)))return false;
    if(/\bCentrality!|\bTable\d+\[/.test(value))return false;
    return true;
  };
  // Create every dependency before assigning formulas, so references resolve.
  for(const view of plan.views)wb.worksheets.add(view.name);
  const column=n=>{let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
  for(const view of plan.views){
    const sheet=wb.worksheets.getItem(view.name);
    const range=sheet.getRange('A1:'+column(view.values[0].length)+view.values.length);
    range.values=view.values.map(row=>row.map(v=>typeof v==='string'&&v.startsWith('=')?null:v));
    for(let c=0;c<view.values[0].length;c++){
      for(let r=0;r<view.values.length;){
        const v=view.values[r][c];
        if(!(typeof v==='string'&&v.startsWith('=')&&supportedFormula(v))){r++;continue;}
        const start=r;const formulas=[];
        while(r<view.values.length){const v=view.values[r][c];if(!(typeof v==='string'&&v.startsWith('=')&&supportedFormula(v)))break;formulas.push([v]);r++;}
        sheet.getRange(column(c+1)+(start+1)+':'+column(c+1)+r).formulas=formulas;
      }
    }
    for(let r=0;r<Math.min(8,view.values.length);r++)for(let c=0;c<6;c++)sheet.getRange(column(c+1)+(r+1)).format=plan.formats[view.styles[r][c]];
    for(const [col,width] of Object.entries(view.widths))sheet.getRange(col+'1:'+col+view.values.length).format.columnWidth=width;
    for(const [row,height] of Object.entries(view.heights))if(Number(row)<9)sheet.getRange('A'+row+':F'+row).format.rowHeight=height;
  }
  console.log('Loaded the four bounded workbook formula views.');
  const dir=path.dirname(plan.output);
  for(const [label,range] of [['before','A1:F8']]){
    const preview=await wb.render({sheetName:'Category Stance',range,scale:1,format:'png'});
    await fs.writeFile(path.join(dir,label+'.png'),new Uint8Array(await preview.arrayBuffer()));
  }
  const templates=wb.worksheets.add('Label Style Templates');const styleCells=new Map();
  for(const c of plan.changes){
    if(c.style_reference&&!styleCells.has(c.style_reference)){
      const address='A'+(styleCells.size+1);styleCells.set(c.style_reference,address);
      templates.getRange(address).copyFrom(wb.worksheets.getItem(c.sheet).getRange(c.style_reference),'all');
      templates.getRange(address).format=plan.formats[c.style_id];
    }
  }
  const grouped=new Map();
  for(const c of plan.changes){
    const [,col,row]=c.cell.match(/^([A-Z]+)(\d+)$/);const formula=typeof c.value==='string'&&c.value.startsWith('=');
    const key=c.sheet+'|'+col+'|'+formula;
    if(!grouped.has(key))grouped.set(key,{sheet:c.sheet,col,formula,cells:[]});
    grouped.get(key).cells.push({row:Number(row),value:c.value});
    if(c.style_reference&&Number(row)<=8&&col.length===1&&col<='F')wb.worksheets.getItem(c.sheet).getRange(c.cell).copyFrom(templates.getRange(styleCells.get(c.style_reference)),'all');
  }
  for(const g of grouped.values()){
    g.cells.sort((a,b)=>a.row-b.row);
    for(let i=0;i<g.cells.length;){
      const start=g.cells[i].row;const values=[[g.cells[i++].value]];let end=start;
      while(i<g.cells.length&&g.cells[i].row===end+1){end++;values.push([g.cells[i++].value]);}
      const range=wb.worksheets.getItem(g.sheet).getRange(g.col+start+':'+g.col+end);
      if(g.formula)range.formulas=values;else range.values=values;
    }
  }
  // Recalculation fixture inputs are evaluated from the original source cells by
  // the same pure input evaluator used by the live rank authority. They are never
  // copied into the production workbook; its original input formulas stay intact.
  const ra=wb.worksheets.getItem('Rank & Allocation');
  ra.getRange('C'+plan.first_row+':C'+plan.last_row).values=plan.context.map(c=>[c.press]);
  ra.getRange('E'+plan.first_row+':F'+plan.last_row).values=plan.context.map(c=>[c.influence,c.demographic]);
  wb.recalculate();
  const rows=wb.worksheets.getItem('Blended Model').getRange('A'+plan.first_row+':T'+plan.last_row).values;
  if(rows.length!==plan.roster_count)throw new Error('Incomplete workbook scoring verification: '+rows.length);
  const mismatches=rows.filter(r=>typeof r[18]!=='number'||Math.abs(r[18]-plan.scores[r[0]])>1e-8||r[19]!==plan.ranks[r[0]]);
  if(mismatches.length)throw new Error('Workbook score/rank mismatch: '+JSON.stringify(mismatches.slice(0,2).map(r=>({member:r[0],score:r[18],rank:r[19],want:plan.scores[r[0]]}))));
  const preview=await wb.render({sheetName:'Category Stance',range:'A1:F8',scale:1,format:'png'});
  await fs.writeFile(path.join(dir,'after.png'),new Uint8Array(await preview.arrayBuffer()));
  const output=await SpreadsheetFile.exportXlsx(wb);await output.save(plan.output);
  const auditDir=path.resolve(plan.root,'data/engine/workbook_measurement_preview');
  await fs.mkdir(auditDir,{recursive:true});
  for(const name of ['before','after'])await fs.copyFile(path.join(dir,name+'.png'),path.join(auditDir,'workbook_'+name+'.png'));
  const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
  await fs.writeFile(plan.output+'.report.json',JSON.stringify({schema:'workbook-cell-author-v1',status:'PASS',
    scores_and_ranks_verified:rows.length,output_sha256:sha(await fs.readFile(plan.output)),
    plan_sha256:sha(await fs.readFile(process.argv[2])),helper_sha256:sha(await fs.readFile(__filename))}));
  console.log(JSON.stringify({status:'PASS',authored:plan.changes.length}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
