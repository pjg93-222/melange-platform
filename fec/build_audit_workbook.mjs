import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require=createRequire(import.meta.url);
const {SpreadsheetFile,Workbook}=require("@oai/artifact-tool");

const root=path.resolve(process.argv[2]);
const handoffPath=path.resolve(process.argv[3]);
const outputDir=path.resolve(process.argv[4]);
const readJson=async p=>JSON.parse(await fs.readFile(p,"utf8"));
const [handoff,category,ranks]=await Promise.all([
  readJson(handoffPath),readJson(path.join(root,"category_stance.json")),readJson(path.join(root,"ranks_python.json"))
]);
if(handoff.schema!=="moneyball-workbook-audit-inputs-v1") throw new Error("workbook handoff schema mismatch");
await fs.mkdir(outputDir,{recursive:true});

const workbook=Workbook.create();
const navy="#17365D",blue="#2F75B5",pale="#D9EAF7",grid="#E7E6E6";
const header=range=>{range.format={fill:blue,font:{bold:true,color:"#FFFFFF"},wrapText:true,borders:{preset:"all",style:"thin",color:grid}};};
const title=(sheet,text,range)=>{
  sheet.mergeCells(range); sheet.getRange(range.split(":")[0]).values=[[text]];
  sheet.getRange(range).format={fill:navy,font:{bold:true,color:"#FFFFFF",size:16},horizontalAlignment:"center",verticalAlignment:"center"};
};

const summary=workbook.worksheets.add("Overview");
summary.showGridLines=false;
title(summary,"Moneyball Audit Workbook","A1:H1");
summary.getRange("A3:B10").values=[
  ["Scope","Generated review view; legacy source workbook is not a rank or stance authority"],
  ["Members",Object.keys(ranks.rank).length],
  ["Rank authority","ranks_python.json — workbook-coherent score and rank; formal office is display-only"],
  ["Category authority","category_stance.json — CU advocacy frame"],
  ["Source workbook",handoff.source_workbook],
  ["Source SHA-256",handoff.source_workbook_sha256],
  ["Legacy displaced normalized cells found",handoff.legacy_normalized_cell_mismatches],
  ["Repair","Normalization rebuilt from each member's same physical source row"],
];
header(summary.getRange("A3:A10"));
summary.getRange("B3:B10").format={fill:"#F8FBFE",wrapText:true,borders:{preset:"all",style:"thin",color:grid}};
summary.getRange("A12:E16").values=[
  ["Pillar","Nominal weight","Pillar std. dev.","Weighted spread","Realized spread share"],
  ...Object.keys(ranks.effective_weight_diagnostics.nominal_weights).map(k=>[
    k,ranks.effective_weight_diagnostics.nominal_weights[k],ranks.effective_weight_diagnostics.pillar_stddev[k],
    ranks.effective_weight_diagnostics.weighted_spread[k],ranks.effective_weight_diagnostics.realized_spread_share[k]
  ])
];
header(summary.getRange("A12:E12"));
summary.getRange("B13:E16").setNumberFormat("0.0000");
summary.getRange("A1:H16").format.autofitColumns(); summary.getRange("B1:B16").format.columnWidth=72;

const authority=workbook.worksheets.add("Rank Authority");
authority.showGridLines=false; title(authority,"Rank Authority","A1:O1");
authority.getRange("A2:O2").merge(); authority.getRange("A2").values=[["Shipped Python authority. Formal office is titled display context only and never modifies Influence or rank."]];
authority.getRange("A2:O2").format={fill:pale,font:{italic:true,color:navy},wrapText:true};
authority.getRange("A4:O4").values=[["Member","Score","Rank","Parity Score","Parity Rank","Legislative %","Press %","Influence %","Demographic %","Influence Raw","Office Rank Bonus","Influence Raw Parity","Display Role Weight","Role Office","Display Eligibility"]];
header(authority.getRange("A4:O4"));
const ordered=Object.keys(ranks.rank).sort((a,b)=>ranks.rank[a]-ranks.rank[b]||a.localeCompare(b));
authority.getRange("A5:O537").values=ordered.map(name=>{
  const p=ranks.pcts[name]||{}; return [name,ranks.score[name],ranks.rank[name],ranks.base_score[name],ranks.base_rank[name],p.leg,p.press,p.inf,p.dem,ranks.influence_raw_base[name],ranks.formal_role_influence_bonus[name],ranks.influence_raw_adjusted[name],ranks.formal_role_display_weight[name],ranks.formal_role_influence_office[name],ranks.formal_role_influence_eligibility[name]];
});
authority.getRange("B5:B537").setNumberFormat("0.000000000"); authority.getRange("C5:E537").setNumberFormat("0"); authority.getRange("F5:M537").setNumberFormat("0.000000");
authority.getRange("A1:O537").format.autofitColumns(); authority.getRange("A1:A537").format.columnWidth=24; authority.getRange("N1:O537").format.columnWidth=36; authority.freezePanes.freezeRows(4);

const stance=workbook.worksheets.add("Category Stance");
stance.showGridLines=false; title(stance,"Current Category Stance — CU Advocacy Frame","A1:T1");
stance.getRange("A2:T2").values=[["Member","General Stance",...category.cats]]; header(stance.getRange("A2:T2"));
const general=new Map(handoff.rank_rows.map(r=>[r[0],r[2]]));
const stanceNames=Object.keys(category.matrix).sort((a,b)=>a.localeCompare(b));
stance.getRange("A3:T535").values=stanceNames.map(name=>[name,general.get(name)||"—",...category.cats.map(cat=>(category.matrix[name]||{})[cat]||"—")]);
stance.getRange("A537:T537").merge(); stance.getRange("A537").values=[["Positive means aligned with the credit-union advocacy position. Defensive issue signs are normalized upstream; this sheet is generated, never hand-pasted."]];
stance.getRange("A537:T537").format={fill:pale,font:{italic:true,color:navy},wrapText:true};
stance.getRange("A1:T537").format.autofitColumns(); stance.getRange("A1:B537").format.columnWidth=24; stance.freezePanes.freezeRows(2); stance.freezePanes.freezeColumns(2);

const agenda=workbook.worksheets.add("Legislative Agenda");
agenda.showGridLines=false; title(agenda,"Legislative Agenda — Fresh Source View","A1:C1");
agenda.getRange("A2:C2").values=[["Member","Legislative agenda raw","General Stance"]]; header(agenda.getRange("A2:C2"));
agenda.getRange("A3:C535").values=handoff.rank_rows.map(r=>[r[0],r[1],r[2]]);
agenda.getRange("A537:C537").merge(); agenda.getRange("A537").values=[[`Source workbook SHA-256: ${handoff.source_workbook_sha256}`]];
agenda.getRange("A537:C537").format={fill:pale,font:{italic:true,color:navy},wrapText:true};
agenda.getRange("B3:B535").setNumberFormat("0.000000"); agenda.getRange("A1:C537").format.autofitColumns(); agenda.getRange("A1:A535").format.columnWidth=24; agenda.freezePanes.freezeRows(2);

const raw=workbook.worksheets.add("Legislative Raw");
raw.getRange("A1:FD535").values=handoff.legislative_raw; header(raw.getRange("A1:FD1")); raw.freezePanes.freezeRows(2); raw.freezePanes.freezeColumns(2);
const norm=workbook.worksheets.add("Legislative Normalized");
norm.getRange("A1:FD535").values=handoff.legislative_normalized; header(norm.getRange("A1:FD1")); norm.getRange("C3:FD535").setNumberFormat("0.000000"); norm.freezePanes.freezeRows(2); norm.freezePanes.freezeColumns(2);

const output=path.join(outputDir,"Moneyball Audit Workbook.xlsx");
const xlsx=await SpreadsheetFile.exportXlsx(workbook); await xlsx.save(output);
for(const sheetName of ["Overview","Rank Authority","Category Stance","Legislative Agenda"]){
  const image=await workbook.render({sheetName,autoCrop:"all",scale:0.35,format:"png"});
  await fs.writeFile(path.join(outputDir,sheetName.toLowerCase().replaceAll(" ","_")+".png"),new Uint8Array(await image.arrayBuffer()));
}
console.log(JSON.stringify({output,members:ordered.length,sheets:workbook.worksheets.items.map(s=>s.name)}));
