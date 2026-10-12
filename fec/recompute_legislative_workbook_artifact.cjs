"use strict";

/*
 * The only workbook-writing component of recompute_legislative_workbook.py.
 * It deliberately accepts precomputed numeric matrices: policy selection and
 * computation belong in the audited Python/JSON preparation layer, while all
 * XLSX mutation, evaluation, inspection, rendering, and export stay inside
 * @oai/artifact-tool.
 */

const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { FileBlob, SpreadsheetFile } = require("@oai/artifact-tool");

const ERROR_PATTERN = "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A";
const REQUIRED_SHEETS = [
  "Legislative Score - Raw",
  "Legislative Score - Normalized",
  "Sub Layer - Raw",
  "Sub Layer - Normalized",
  "Blended Model",
];
const EXPECTED_PILLAR_MEMBER_COUNT = 533;

const sha256Bytes = (bytes) =>
  crypto.createHash("sha256").update(bytes).digest("hex");

function parseNdjson(value) {
  return String(value || "")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return { kind: "invalid-ndjson", raw: line };
      }
    });
}

function realErrorRecords(records) {
  return records.filter(
    (record) =>
      record.kind !== "notice" &&
      record.kind !== "summary" &&
      !(
        typeof record.message === "string" &&
        /matched\s+0\s+entries/i.test(record.message)
      ),
  );
}

function finiteMatrix(matrix) {
  return (
    Array.isArray(matrix) &&
    matrix.length > 0 &&
    matrix.every(
      (row) =>
        Array.isArray(row) &&
        row.length > 0 &&
        row.every(
          (value) =>
            typeof value === "number" && Number.isFinite(value),
        ),
    )
  );
}

function columnNumber(label) {
  return [...String(label).toUpperCase()].reduce(
    (value, character) =>
      value * 26 + character.charCodeAt(0) - 64,
    0,
  );
}

function rangeShape(range) {
  const match = String(range).match(
    /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/i,
  );
  if (!match) throw new Error(`invalid inspection range: ${range}`);
  const rows = Number(match[4]) - Number(match[2]) + 1;
  const cols = columnNumber(match[3]) - columnNumber(match[1]) + 1;
  return { rows, cols, cells: rows * cols };
}

function tableCounts(records, range) {
  const expected = rangeShape(range);
  const tables = records.filter((record) => Array.isArray(record.values));
  const values = tables.length === 1 ? tables[0].values : [];
  const rowWidths = values.map((row) =>
    Array.isArray(row) ? row.length : 0,
  );
  const actual = {
    rows: values.length,
    cols: rowWidths.length ? Math.max(...rowWidths) : 0,
    cells: rowWidths.reduce((total, width) => total + width, 0),
  };
  const countMatch =
    tables.length === 1 &&
    actual.rows === expected.rows &&
    actual.cols === expected.cols &&
    actual.cells === expected.cells &&
    rowWidths.every((width) => width === expected.cols);
  return {
    values,
    table_record_count: tables.length,
    expected_rows: expected.rows,
    expected_cols: expected.cols,
    expected_cells: expected.cells,
    actual_rows: actual.rows,
    actual_cols: actual.cols,
    actual_cells: actual.cells,
    count_match: countMatch,
  };
}

async function main() {
  const [
    inputArg,
    planArg,
    outputArg,
    snapshotArg,
    reportArg,
    renderDirArg,
  ] = process.argv.slice(2);
  if (!inputArg || !planArg || !outputArg || !snapshotArg || !reportArg) {
    throw new Error(
      "usage: node helper.cjs input.xlsx plan.json output.xlsx " +
        "pillar_snapshot.json verification.json [render-dir]",
    );
  }
  const inputPath = path.resolve(inputArg);
  const outputPath = path.resolve(outputArg);
  const snapshotPath = path.resolve(snapshotArg);
  const reportPath = path.resolve(reportArg);
  const plan = JSON.parse(await fs.readFile(path.resolve(planArg), "utf8"));
  if (
    plan.schema !== "legislation-workbook-numeric-write-plan-v1" ||
    plan.status !== "PASS" ||
    !Array.isArray(plan.writes) ||
    plan.writes.length === 0
  ) {
    throw new Error("numeric workbook write plan is not PASS/complete");
  }

  const inputBytes = await fs.readFile(inputPath);
  const inputSha256 = sha256Bytes(inputBytes);
  const workbook = await SpreadsheetFile.importXlsx(
    await FileBlob.load(inputPath),
  );
  const sheetInspection = await workbook.inspect({
    kind: "sheet",
    include: "id,name",
    maxChars: 12000,
  });
  const sheetText = String(sheetInspection.ndjson || "");
  const missingSheets = REQUIRED_SHEETS.filter(
    (name) => !sheetText.includes(`"name":"${name}"`),
  );
  if (missingSheets.length) {
    throw new Error(`required sheets missing: ${missingSheets.join(", ")}`);
  }

  for (const write of plan.writes) {
    if (
      typeof write.sheet !== "string" ||
      typeof write.range !== "string" ||
      !finiteMatrix(write.values)
    ) {
      throw new Error(`invalid numeric write: ${JSON.stringify(write)}`);
    }
    const sheet = workbook.worksheets.getItem(write.sheet);
    const target = sheet.getRange(write.range);
    target.values = write.values;
  }

  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: ERROR_PATTERN,
    options: { useRegex: true, maxResults: 1000 },
    summary: "post-recompute formula error scan",
    maxChars: 90000,
  });
  const errorRecords = parseNdjson(errors.ndjson);
  const errorMatches = realErrorRecords(errorRecords);

  const categoryBlocks = [
    "C3:J535",
    "K3:R535",
    "S3:Z535",
    "AA3:AH535",
    "AI3:AP535",
    "AQ3:AX535",
    "AY3:BF535",
    "BG3:BN535",
    "BO3:BV535",
    "BW3:CD535",
    "CE3:CL535",
    "CM3:CT535",
    "CU3:DB535",
    "DC3:DJ535",
    "DK3:DR535",
    "DS3:DZ535",
    "EA3:EH535",
    "EW3:FD535",
  ];
  const subLayerCategoryColumns = [
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "M",
    "N",
    "O",
    "Q",
    "R",
    "S",
    "T",
    "U",
  ];
  const numericChecks = [
    ...categoryBlocks.map((range) => [
      "Legislative Score - Raw",
      range,
    ]),
    ...categoryBlocks.map((range) => [
      "Legislative Score - Normalized",
      range,
    ]),
    ...subLayerCategoryColumns.map((column) => [
      "Sub Layer - Raw",
      `${column}4:${column}536`,
    ]),
    ["Sub Layer - Raw", "AG4:AW536"],
    ...subLayerCategoryColumns.map((column) => [
      "Sub Layer - Normalized",
      `${column}4:${column}536`,
    ]),
    ["Sub Layer - Normalized", "AG4:AW536"],
  ];
  const inspections = [];
  const criticalInspectionMismatches = [];
  let nonNumeric = 0;
  for (const [sheetId, range] of numericChecks) {
    const inspected = await workbook.inspect({
      kind: "table",
      sheetId,
      range,
      include: "values,formulas",
      maxChars: 1200000,
      tableMaxRows: 600,
      tableMaxCols: 170,
      tableMaxCellChars: 80,
    });
    const records = parseNdjson(inspected.ndjson);
    const counts = tableCounts(records, range);
    const localNonNumeric = counts.values
      .flat()
      .filter(
        (value) =>
          typeof value !== "number" || !Number.isFinite(value),
      ).length;
    nonNumeric += localNonNumeric;
    const inspection = {
      sheet: sheetId,
      range,
      table_record_count: counts.table_record_count,
      expected_rows: counts.expected_rows,
      expected_cols: counts.expected_cols,
      expected_cells: counts.expected_cells,
      actual_rows: counts.actual_rows,
      actual_cols: counts.actual_cols,
      actual_cells: counts.actual_cells,
      count_match: counts.count_match,
      non_numeric_cells: localNonNumeric,
      ndjson_sha256: sha256Bytes(
        Buffer.from(String(inspected.ndjson || ""), "utf8"),
      ),
    };
    inspections.push(inspection);
    if (!counts.count_match) criticalInspectionMismatches.push(inspection);
  }

  const blended = await workbook.inspect({
    kind: "table",
    sheetId: "Blended Model",
    range: "A3:O535",
    include: "values,formulas",
    maxChars: 1400000,
    tableMaxRows: 540,
    tableMaxCols: 15,
    tableMaxCellChars: 100,
  });
  const blendedRecords = parseNdjson(blended.ndjson);
  const blendedCounts = tableCounts(blendedRecords, "A3:O535");
  const blendedValues = blendedCounts.values;
  const snapshot = { O: {}, E: {}, F: {} };
  let snapshotInvalid = 0;
  let unnamedSnapshotRows = 0;
  const duplicateSnapshotNames = [];
  const seenSnapshotNames = new Set();
  for (const row of blendedValues) {
    if (!Array.isArray(row) || !row[0]) {
      unnamedSnapshotRows += 1;
      continue;
    }
    const name = String(row[0]);
    if (seenSnapshotNames.has(name)) duplicateSnapshotNames.push(name);
    seenSnapshotNames.add(name);
    const values = { E: row[4], F: row[5], O: row[14] };
    for (const [key, value] of Object.entries(values)) {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        snapshotInvalid += 1;
      } else {
        snapshot[key][name] = value;
      }
    }
  }
  snapshot.schema = "pillar-inputs-snapshot-v1";
  snapshot.generation_id = plan.generation_id;
  snapshot.source = "artifact-tool evaluated staged workbook";
  snapshot.selected_role_weight_policy =
    plan.selected_role_weight_policy;
  const pillarSnapshotMembers = Object.keys(snapshot.O).length;
  const pillarSnapshotFieldCounts = {
    O: Object.keys(snapshot.O).length,
    E: Object.keys(snapshot.E).length,
    F: Object.keys(snapshot.F).length,
  };
  const pillarSnapshotMemberCountMatch =
    pillarSnapshotMembers === EXPECTED_PILLAR_MEMBER_COUNT &&
    Object.values(pillarSnapshotFieldCounts).every(
      (count) => count === EXPECTED_PILLAR_MEMBER_COUNT,
    ) &&
    unnamedSnapshotRows === 0 &&
    duplicateSnapshotNames.length === 0;

  const renders = [];
  if (renderDirArg) {
    const renderDir = path.resolve(renderDirArg);
    await fs.mkdir(renderDir, { recursive: true });
    const renderChecks = [
      ["Legislative Score - Raw", "A1:FD18", "legislative_raw.png"],
      [
        "Legislative Score - Normalized",
        "A1:FD18",
        "legislative_normalized.png",
      ],
      ["Sub Layer - Raw", "A1:BB18", "sub_layer_raw.png"],
      [
        "Sub Layer - Normalized",
        "A1:BB18",
        "sub_layer_normalized.png",
      ],
    ];
    for (const [sheetName, range, fileName] of renderChecks) {
      const rendered = await workbook.render({
        sheetName,
        range,
        scale: 1,
        format: "png",
      });
      const bytes = new Uint8Array(await rendered.arrayBuffer());
      await fs.writeFile(path.join(renderDir, fileName), bytes);
      renders.push({
        sheet: sheetName,
        range,
        file: fileName,
        size: bytes.byteLength,
        sha256: sha256Bytes(bytes),
      });
    }
  }

  const status =
    errorMatches.length === 0 &&
    nonNumeric === 0 &&
    criticalInspectionMismatches.length === 0 &&
    blendedCounts.count_match &&
    snapshotInvalid === 0 &&
    pillarSnapshotMemberCountMatch &&
    renders.every((item) => item.size > 0)
      ? "PASS"
      : "BLOCKED";

  if (status === "PASS") {
    await fs.mkdir(path.dirname(outputPath), { recursive: true });
    const output = await SpreadsheetFile.exportXlsx(workbook);
    await output.save(outputPath);
    await fs.writeFile(
      snapshotPath,
      `${JSON.stringify(snapshot, null, 2)}\n`,
      "utf8",
    );
  }
  const report = {
    schema: "legislation-workbook-artifact-tool-verification-v1",
    status,
    engine: "@oai/artifact-tool",
    input_sha256: inputSha256,
    generation_id: plan.generation_id,
    selected_role_weight_policy: plan.selected_role_weight_policy,
    write_count: plan.writes.length,
    formula_error_count: errorMatches.length,
    formula_error_records: errorMatches,
    numeric_checks: inspections,
    critical_inspection_expected_count: numericChecks.length,
    critical_inspection_actual_count: inspections.length,
    critical_inspection_exact_count_passes:
      inspections.length - criticalInspectionMismatches.length,
    critical_inspection_count_mismatches:
      criticalInspectionMismatches.length,
    critical_inspection_count_mismatch_records:
      criticalInspectionMismatches,
    critical_inspection_expected_cells: inspections.reduce(
      (total, item) => total + item.expected_cells,
      0,
    ),
    critical_inspection_actual_cells: inspections.reduce(
      (total, item) => total + item.actual_cells,
      0,
    ),
    non_numeric_cells: nonNumeric,
    blended_inspection: {
      range: "A3:O535",
      table_record_count: blendedCounts.table_record_count,
      expected_rows: blendedCounts.expected_rows,
      expected_cols: blendedCounts.expected_cols,
      expected_cells: blendedCounts.expected_cells,
      actual_rows: blendedCounts.actual_rows,
      actual_cols: blendedCounts.actual_cols,
      actual_cells: blendedCounts.actual_cells,
      count_match: blendedCounts.count_match,
    },
    pillar_snapshot_invalid_cells: snapshotInvalid,
    pillar_snapshot_expected_members: EXPECTED_PILLAR_MEMBER_COUNT,
    pillar_snapshot_members: pillarSnapshotMembers,
    pillar_snapshot_member_count_match: pillarSnapshotMemberCountMatch,
    pillar_snapshot_field_counts: pillarSnapshotFieldCounts,
    pillar_snapshot_unnamed_rows: unnamedSnapshotRows,
    pillar_snapshot_duplicate_names: duplicateSnapshotNames,
    renders,
    output_written: status === "PASS",
  };
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(
    reportPath,
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  console.log(
    JSON.stringify({
      status,
      formula_error_count: errorMatches.length,
      non_numeric_cells: nonNumeric,
      critical_inspection_count_mismatches:
        criticalInspectionMismatches.length,
      snapshot_invalid_cells: snapshotInvalid,
      pillar_snapshot_members: pillarSnapshotMembers,
    }),
  );
  if (status !== "PASS") process.exitCode = 2;
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 2;
});
