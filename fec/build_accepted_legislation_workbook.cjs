"use strict";

/*
 * The Python producer prepares validated JSON only.  This CommonJS helper is
 * the exclusive XLSX author: import, targeted writes, inspection, rendering,
 * and export all go through @oai/artifact-tool.
 */
const fs = require("node:fs/promises");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");

const REPORT_SCHEMA = "accepted-legislation-artifact-tool-report-v1";
const PAYLOAD_SCHEMA = "accepted-legislation-workbook-payload-v1";
const ERROR_RE = /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/;

function fail(message) {
  throw new Error(message);
}

async function loadArtifactTool() {
  const modules = process.env.ARTIFACT_TOOL_NODE_MODULES;
  if (!modules) {
    fail("ARTIFACT_TOOL_NODE_MODULES is required");
  }
  const loader = createRequire(path.join(path.resolve(modules), "__loader.cjs"));
  const entry = loader.resolve("@oai/artifact-tool");
  return import(pathToFileURL(entry).href);
}

function validateMatrix(rows, label) {
  if (!Array.isArray(rows) || rows.length < 1 || !Array.isArray(rows[0])) {
    fail(`${label} must be a nonempty matrix`);
  }
  const width = rows[0].length;
  if (width < 1 || rows.some((row) => !Array.isArray(row) || row.length !== width)) {
    fail(`${label} is not rectangular`);
  }
  for (const row of rows) {
    for (const value of row) {
      if (typeof value === "string" && ERROR_RE.test(value)) {
        fail(`${label} contains an Excel error token`);
      }
      if (
        value !== null &&
        !["string", "number", "boolean"].includes(typeof value)
      ) {
        fail(`${label} contains a non-scalar cell value`);
      }
    }
  }
  return { rows: rows.length, cols: width };
}

function countErrorTokens(matrix) {
  let count = 0;
  for (const row of matrix || []) {
    for (const value of row || []) {
      if (typeof value === "string" && ERROR_RE.test(value)) {
        count += 1;
      }
    }
  }
  return count;
}

function columnName(number) {
  let value = number;
  let text = "";
  while (value > 0) {
    value -= 1;
    text = String.fromCharCode(65 + (value % 26)) + text;
    value = Math.floor(value / 26);
  }
  return text;
}

async function replaceSheetValues(workbook, sheetName, rows) {
  const dimensions = validateMatrix(rows, sheetName);
  const sheet = workbook.worksheets.getItem(sheetName);
  if (!sheet) {
    fail(`required worksheet is missing: ${sheetName}`);
  }
  const used = sheet.getUsedRange();
  if (used) {
    used.clear({ applyTo: "contents" });
  }
  const address = `A1:${columnName(dimensions.cols)}${dimensions.rows}`;
  sheet.getRange(address).values = rows;
  sheet.freezePanes.freezeRows(1);
  const tableInspection = await workbook.inspect({
    kind: "table",
    sheetId: sheetName,
    range: address,
    include: "values,formulas",
    tableMaxRows: Math.min(dimensions.rows, 12),
    tableMaxCols: Math.min(dimensions.cols, 24),
    maxChars: 12000,
  });
  const errorInspection = await workbook.inspect({
    kind: "match",
    sheetId: sheetName,
    range: address,
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: `formula error scan for ${sheetName}`,
    maxChars: 12000,
  });
  const actualValues = sheet.getRange(address).values;
  return {
    sheet: sheetName,
    address,
    ...dimensions,
    tableInspection: tableInspection.ndjson,
    errorInspection: errorInspection.ndjson,
    actualErrorTokenCount: countErrorTokens(actualValues),
  };
}

async function renderPreview(workbook, sheetName, rowCount, outputPath) {
  const lastRow = Math.max(1, Math.min(rowCount, 25));
  const blob = await workbook.render({
    sheetName,
    range: `A1:M${lastRow}`,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(outputPath, new Uint8Array(await blob.arrayBuffer()));
}

async function exportWorkbook(SpreadsheetFile, workbook, outputPath) {
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(outputPath);
}

async function main() {
  const [payloadArg, reportArg] = process.argv.slice(2);
  if (!payloadArg || !reportArg) {
    fail("usage: helper payload.json report.json");
  }
  const payloadPath = path.resolve(payloadArg);
  const reportPath = path.resolve(reportArg);
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  if (
    payload.schema !== PAYLOAD_SCHEMA ||
    payload.production_written !== false ||
    !/^[0-9a-f]{64}$/i.test(String(payload.generation_id || ""))
  ) {
    fail("invalid prepared payload contract");
  }
  const { FileBlob, SpreadsheetFile } = await loadArtifactTool();
  const previewDir = path.resolve(payload.outputs.preview_dir);
  await fs.mkdir(previewDir, { recursive: true });
  const mutations = [];
  const previews = [];

  const modelInput = await FileBlob.load(path.resolve(payload.baseline.model));
  const model = await SpreadsheetFile.importXlsx(modelInput);
  const requiredModelSheets = Object.keys(payload.model_sheets).sort();
  let requiredSheetsPresent = true;
  for (const sheetName of requiredModelSheets) {
    try {
      if (!model.worksheets.getItem(sheetName)) {
        requiredSheetsPresent = false;
      }
    } catch (_error) {
      requiredSheetsPresent = false;
    }
  }
  for (const sheetName of requiredModelSheets) {
    const mutation = await replaceSheetValues(
      model,
      sheetName,
      payload.model_sheets[sheetName],
    );
    mutations.push({ workbook: "model", ...mutation });
    const preview = path.join(
      previewDir,
      `model_${sheetName.replace(/[^A-Za-z0-9]+/g, "_")}.png`,
    );
    await renderPreview(model, sheetName, mutation.rows, preview);
    previews.push(preview);
  }
  await exportWorkbook(
    SpreadsheetFile,
    model,
    path.resolve(payload.outputs.model),
  );

  const archiveInput = await FileBlob.load(path.resolve(payload.baseline.archive));
  const archive = await SpreadsheetFile.importXlsx(archiveInput);
  try {
    if (!archive.worksheets.getItem(payload.archive.sheet)) {
      requiredSheetsPresent = false;
    }
  } catch (_error) {
    requiredSheetsPresent = false;
  }
  const archiveMutation = await replaceSheetValues(
    archive,
    payload.archive.sheet,
    payload.archive.rows,
  );
  mutations.push({ workbook: "archive", ...archiveMutation });
  const archivePreview = path.join(previewDir, "archive_All_Bills.png");
  await renderPreview(
    archive,
    payload.archive.sheet,
    archiveMutation.rows,
    archivePreview,
  );
  previews.push(archivePreview);
  await exportWorkbook(
    SpreadsheetFile,
    archive,
    path.resolve(payload.outputs.archive),
  );

  const targetedFormulaErrorCount = mutations.reduce(
    (total, mutation) => total + mutation.actualErrorTokenCount,
    0,
  );
  if (!requiredSheetsPresent) {
    fail("one or more required workbook sheets are absent");
  }
  if (targetedFormulaErrorCount !== 0) {
    fail(
      `targeted workbook ranges contain ${targetedFormulaErrorCount} error tokens`,
    );
  }
  const report = {
    schema: REPORT_SCHEMA,
    status: "PASS",
    engine: "@oai/artifact-tool",
    staged_only: true,
    production_written: false,
    generation_id: payload.generation_id,
    mutations: mutations.map((mutation) => ({
      workbook: mutation.workbook,
      sheet: mutation.sheet,
      address: mutation.address,
      rows: mutation.rows,
      cols: mutation.cols,
      actual_error_token_count: mutation.actualErrorTokenCount,
      table_inspection: mutation.tableInspection,
      error_match_inspection: mutation.errorInspection,
    })),
    required_sheets: [...requiredModelSheets, payload.archive.sheet],
    required_sheets_present: requiredSheetsPresent,
    targeted_formula_error_count: targetedFormulaErrorCount,
    verification: {
      targeted_cells_are_values_only: true,
      input_error_tokens_rejected: true,
      previews_rendered: previews.length,
      engine_table_inspection_executed: true,
      engine_error_match_scan_executed: true,
      required_sheet_presence_checks: mutations.map(
        (mutation) => `${mutation.workbook}:${mutation.sheet}`,
      ),
    },
    outputs: {
      model: path.resolve(payload.outputs.model),
      archive: path.resolve(payload.outputs.archive),
      previews,
    },
  };
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exitCode = 2;
});
