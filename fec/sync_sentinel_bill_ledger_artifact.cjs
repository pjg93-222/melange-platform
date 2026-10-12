"use strict";

/* Exclusive XLSX author for sync_sentinel_bill_ledger.py. */
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");
const crypto = require("node:crypto");

const ERROR_PATTERN = "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A";
const INTERACTION_HEADERS = ["Section","Role","Member","Name","Title","Sponsor","CosponsorCount","Committee","LastAction","Congress","DecayWeight","DetailLink","bill_summary"];
const interactionKey = record => [record._bioguide,record.values[0],record.values[1],record.values[2],record.values[3],String(record.values[9] ?? "")].join("\u001f").toLowerCase();

async function loadTool() {
  const modules = process.env.ARTIFACT_TOOL_NODE_MODULES;
  if (!modules) throw new Error("ARTIFACT_TOOL_NODE_MODULES is required");
  const loader = createRequire(path.join(path.resolve(modules), "__loader.cjs"));
  return import(pathToFileURL(loader.resolve("@oai/artifact-tool")).href);
}
function colName(number) {
  let value = number, result = "";
  while (value > 0) { value -= 1; result = String.fromCharCode(65 + value % 26) + result; value = Math.floor(value / 26); }
  return result;
}
function parseNdjson(text) {
  return String(text || "").split(/\r?\n/).filter(line => line.trim()).map(line => { try { return JSON.parse(line); } catch { return {kind:"invalid"}; } });
}
function actualMatches(records) {
  return records.filter(row => row.kind !== "notice" && row.kind !== "summary" && !(typeof row.message === "string" && /matched\s+0\s+entries/i.test(row.message)));
}

async function main() {
  const [planArg, reportArg] = process.argv.slice(2);
  if (!planArg || !reportArg) throw new Error("usage: helper plan.json report.json");
  const planText = await fs.readFile(path.resolve(planArg), "utf8");
  const inputPlanSha256 = crypto.createHash("sha256").update(planText).digest("hex");
  const plan = JSON.parse(planText);
  if (plan.schema !== "sentinel-bill-workbook-plan-v1" || !Array.isArray(plan.ledger_rows)) throw new Error("invalid plan");
  const { SpreadsheetFile, Workbook } = await loadTool();
  const workbook = Workbook.create();

  const ledger = workbook.worksheets.add(plan.ledger_sheet);
  const ledgerRange = `A1:${colName(plan.ledger_rows[0].length)}${plan.ledger_rows.length}`;
  ledger.getRange(ledgerRange).values = plan.ledger_rows;
  ledger.showGridLines = false;
  ledger.freezePanes.freezeRows(1);
  ledger.getRange(`A1:${colName(plan.ledger_rows[0].length)}1`).format = {fill:"#17365D",font:{bold:true,color:"#FFFFFF"},wrapText:true,borders:{preset:"all",style:"thin",color:"#D9E2F3"}};
  ledger.getRange(ledgerRange).format.autofitColumns();
  for (const column of ["A","B","D","I","J","N","O","T","U","V","W","X","Z","AA"]) ledger.getRange(`${column}1:${column}${plan.ledger_rows.length}`).format.columnWidth = column === "X" ? 72 : 24;
  ledger.getRange(`X2:X${plan.ledger_rows.length}`).format.wrapText = true;

  const grouped = new Map();
  for (const record of plan.accepted_interactions) {
    if (!grouped.has(record.sheet)) grouped.set(record.sheet, []);
    grouped.get(record.sheet).push(record);
  }
  const appended = {};
  const interactionSheets = ["Original Co-Sponsor Interaction", "Co-Sponsor Interactions"];
  if (grouped.has("Sponsor Interactions")) interactionSheets.push("Sponsor Interactions");
  for (const sheetName of interactionSheets) {
    const sheet = workbook.worksheets.add(sheetName);
    const existing = [INTERACTION_HEADERS];
    sheet.getRange("A1:M1").values = existing;
    sheet.getRange("A1:M1").format = {fill:"#2F75B5",font:{bold:true,color:"#FFFFFF"},wrapText:true,borders:{preset:"all",style:"thin",color:"#D9E2F3"}};
    sheet.freezePanes.freezeRows(1);
    const seen = new Set();
    const additions = [];
    for (const record of grouped.get(sheetName) || []) if (!seen.has(interactionKey(record))) { seen.add(interactionKey(record)); additions.push(record.values); }
    if (additions.length) {
      const start = existing.length + 1;
      sheet.getRange(`A${start}:M${start + additions.length - 1}`).values = additions;
    }
    sheet.getRange(`A1:M${1 + additions.length}`).format.autofitColumns();
    sheet.getRange(`A1:A${1 + additions.length}`).format.columnWidth = 28;
    sheet.getRange(`C1:C${1 + additions.length}`).format.columnWidth = 24;
    sheet.getRange(`E1:F${1 + additions.length}`).format.columnWidth = 34;
    sheet.getRange(`M1:M${1 + additions.length}`).format.columnWidth = 72;
    sheet.getRange(`A2:M${1 + additions.length}`).format.rowHeight = 18;
    sheet.getRange(`A2:M${1 + additions.length}`).format.verticalAlignment = "top";
    sheet.getRange(`M2:M${1 + additions.length}`).format.wrapText = false;
    appended[sheetName] = additions.length;
  }

  const inspections = [];
  for (const [sheetName, range] of [[plan.ledger_sheet, ledgerRange], ...interactionSheets.map(name => [name, `A1:M${1 + appended[name]}`])]) {
    const table = await workbook.inspect({kind:"table",sheetId:sheetName,range,include:"values,formulas",tableMaxRows:16,tableMaxCols:27,maxChars:30000});
    inspections.push({sheet:sheetName,range,sha256:crypto.createHash("sha256").update(String(table.ndjson || "")).digest("hex")});
  }
  const targetedErrorRows = [];
  for (const item of inspections) {
    const errors = await workbook.inspect({kind:"match",sheetId:item.sheet,range:item.range,searchTerm:ERROR_PATTERN,options:{useRegex:true,maxResults:1000},summary:`post sentinel formula error scan: ${item.sheet}`,maxChars:30000});
    targetedErrorRows.push(...actualMatches(parseNdjson(errors.ndjson)).map(row => ({sheet:item.sheet,...row})));
  }
  if (targetedErrorRows.length) throw new Error(`mutated bill ranges contain ${targetedErrorRows.length} formula error matches`);

  const previewDir = path.join(path.dirname(path.resolve(plan.output_workbook)), "previews");
  await fs.mkdir(previewDir, {recursive:true});
  const previews = [];
  for (const [sheetName, range, fileName] of [[plan.ledger_sheet,`A1:N${Math.min(plan.ledger_rows.length, 24)}`,"sentinel_bill_ledger.png"], ...interactionSheets.map((name,i) => [name,`A1:M${Math.min(1 + appended[name],24)}`,`interactions_${i}.png`])]) {
    const render = await workbook.render({sheetName,range,scale:0.7,format:"png"});
    const preview = path.join(previewDir, fileName);
    await fs.writeFile(preview, new Uint8Array(await render.arrayBuffer()));
    previews.push(preview);
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.resolve(plan.output_workbook));
  const outputSha256 = crypto.createHash("sha256").update(await fs.readFile(path.resolve(plan.output_workbook))).digest("hex");
  const report = {schema:"sentinel-bill-workbook-artifact-report-v1",status:"PASS",engine:"@oai/artifact-tool",input_plan_sha256:inputPlanSha256,output_sha256:outputSha256,ledger_range:ledgerRange,appended,targeted_formula_error_count:0,formula_error_scan_scope:"all generated bill ranges",inspections,previews,output:path.resolve(plan.output_workbook)};
  await fs.writeFile(path.resolve(reportArg), JSON.stringify(report,null,2)+"\n", "utf8");
  console.log(JSON.stringify({status:"PASS",ledger_rows:plan.ledger_rows.length-1,appended}));
  // The Windows V8 teardown path can crash while freeing this workbook even
  // after every awaited file write has completed.  Exit explicitly only after
  // the verified report and XLSX are durable.
  process.exit(0);
}
main().catch(error => { console.error(error && error.stack ? error.stack : String(error)); process.exitCode = 2; });
