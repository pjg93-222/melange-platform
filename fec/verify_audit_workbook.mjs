import { createRequire } from "node:module";
const require=createRequire(import.meta.url);
const {FileBlob,SpreadsheetFile}=require("@oai/artifact-tool");

const input=process.argv[2];
if(!input) throw new Error("usage: node verify_audit_workbook.mjs <xlsx>");
const workbook=await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const required=["Overview","Rank Authority","Category Stance","Legislative Agenda","Legislative Raw","Legislative Normalized"];
const names=workbook.worksheets.items.map(s=>s.name);
const missing=required.filter(name=>!names.includes(name));
if(missing.length) throw new Error("missing sheets: "+missing.join(", "));
const formulaScan=await workbook.inspect({kind:"formula",maxChars:4000,options:{maxResults:100}});
const text=formulaScan.ndjson || "";
if(/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/.test(text)) throw new Error("formula error token found");
console.log(JSON.stringify({status:"PASS",sheets:names,formula_error_tokens:0}));
