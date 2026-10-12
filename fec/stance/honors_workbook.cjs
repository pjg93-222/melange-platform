"use strict";
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const column = n => { let s=''; for(;n;n=Math.floor((n-1)/26)) s=String.fromCharCode(65+(n-1)%26)+s; return s; };

async function main() {
  const planPath = process.argv[2];
  const plan = JSON.parse(await fs.readFile(planPath, 'utf8'));
  const loader = createRequire(path.join(process.env.ARTIFACT_TOOL_NODE_MODULES, '__loader.cjs'));
  const {Workbook, SpreadsheetFile} = await import(pathToFileURL(loader.resolve('@oai/artifact-tool')).href);
  const wb = Workbook.create();
  for (const view of plan.views) {
    const sheet = wb.worksheets.add(view.name);
    const height=view.values.length, width=view.values[0].length, last=column(width);
    sheet.getRange('A1:'+last+height).values = view.values;
    sheet.getRange('A1:'+last+height).format.font = {name:'Calibri',size:11};
    sheet.getRange('A1:'+last+height).format.columnWidth = 23;
    sheet.getRange('A1:A'+height).format.columnWidth = 28;
    sheet.getRange('A1:F1').merge();
    sheet.getRange('A1:'+last+'1').format = {fill:'#163F4B',font:{name:'Calibri',size:17,bold:true,color:'#FFFFFF'},rowHeight:32};
    sheet.getRange('A2:F2').merge();
    sheet.getRange('A2:'+last+'2').format = {font:{name:'Calibri',size:11,color:'#425563'},rowHeight:42,wrapText:true};
    sheet.getRange('A3:'+last+'3').format = {font:{name:'Calibri',size:9,color:'#64748B'},rowHeight:22};
    sheet.getRange('A4:'+last+'4').format = {fill:'#DCECEF',font:{name:'Calibri',size:11,bold:true},rowHeight:44,wrapText:true};
    sheet.getRange('A5:'+last+height).format.rowHeight = 30;
    sheet.getRange('A5:'+last+height).format.wrapText = true;
    sheet.freezePanes.freezeRows(4);
    sheet.freezePanes.freezeColumns(view.name==='Recognition Detail'?2:4);
    if (view.name!=='Recognition Detail') {
      sheet.getRange('B1:B'+height).format.columnWidth=25;
      sheet.getRange('C1:C'+height).format.columnWidth=13;
      sheet.getRange('D1:D'+height).format.columnWidth=26;
      sheet.getRange('E1:E'+height).format.columnWidth=24;
    }
    if (view.name==='Recognition Detail') {
      sheet.getRange('E5:H'+height).setNumberFormat('0');
      sheet.getRange('I5:J'+height).setNumberFormat('0.00');
      sheet.getRange('N1:N'+height).format.columnWidth=38;
    }
    if (view.name==='Overall Standing') {
      sheet.getRange('G5:G'+height).setNumberFormat('0.00');
      sheet.getRange('H5:U'+height).setNumberFormat('0');
      sheet.getRange('V5:V'+height).setNumberFormat('0.00');
      sheet.getRange('W1:X'+height).format.columnWidth=38;
    }
  }
  wb.recalculate();
  for (const name of plan.views.map(view=>view.name)) {
    const preview=await wb.render({sheetName:name,range:'A1:F9',scale:1,format:'png'});
    await fs.writeFile(path.join(path.dirname(plan.output),name.replaceAll(' ','_')+'.png'),new Uint8Array(await preview.arrayBuffer()));
  }
  const output = await SpreadsheetFile.exportXlsx(wb);
  await output.save(plan.output);
  await fs.writeFile(plan.output+'.receipt.json',JSON.stringify({schema:'honors-workbook-author-v1',status:'PASS',
    generation_id:plan.generation_id,plan_sha256:sha(await fs.readFile(planPath)),
    helper_sha256:sha(await fs.readFile(__filename)),output_sha256:sha(await fs.readFile(plan.output))}));
  console.log('Authored recognition views: '+plan.generation_id);
}
main().catch(error=>{console.error(error);process.exitCode=1;});
