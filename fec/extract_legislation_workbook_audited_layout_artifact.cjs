"use strict";

/*
 * Read-only @oai/artifact-tool helper for the frozen legislative workbook.
 *
 * It imports exactly one XLSX, writes JSON and PNG evidence only, and never
 * exports or mutates a workbook.  The Python wrapper independently binds that
 * XLSX to the immutable baseline manifest and verifies its hash before/after.
 */

const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { FileBlob, SpreadsheetFile } = require("@oai/artifact-tool");

const SCHEMA = "legislation-workbook-layout-artifact-extraction-v1";
const ERROR_PATTERN = "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!";
const REQUIRED_SHEETS = [
  "Legislative Score - Raw",
  "Legislative Score - Normalized",
  "Sub Layer - Raw",
  "Congestion",
];
const CATEGORIES = [
  "Examination & Supervision",
  "CU Capital Requirements",
  "NCUA Budget & Insurance",
  "Interchange",
  "Payments",
  "Technology & Emerging",
  "Fraud Prevention",
  "Data Privacy & Cyber",
  "Charter Modernization",
  "Defense CU Issues",
  "Field of Membership",
  "CDFI Fund",
  "CFPB",
  "Credit Card Mandates",
  "TCPA",
  "Member Business Lending",
  "Mortgage & Housing",
  "CU Tax Status",
];
const BLOCK_RANGES = Object.fromEntries(
  CATEGORIES.map((category, index) => [
    category,
    [
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
    ][index],
  ]),
);
const SUBLAYER_COLUMNS = {
  "Examination & Supervision": "C",
  "CU Capital Requirements": "D",
  "NCUA Budget & Insurance": "E",
  Interchange: "F",
  Payments: "G",
  "Technology & Emerging": "H",
  "Fraud Prevention": "I",
  "Data Privacy & Cyber": "J",
  "Charter Modernization": "K",
  "Defense CU Issues": "L",
  "Field of Membership": "M",
  "CU Tax Status": "N",
  "CDFI Fund": "O",
  CFPB: "Q",
  TCPA: "R",
  "Credit Card Mandates": "S",
  "Member Business Lending": "T",
  "Mortgage & Housing": "U",
};
const EFFICIENCY_COLUMNS = [
  "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO",
  "AP", "AQ", "AR", "AS", "AT", "AU", "AV", "AW",
];
const CATEGORY_ALIASES = {
  "Examination & Supervision": ["examination", "supervision"],
  "CU Capital Requirements": [
    "cu capital",
    "credit union capital",
    "capital requirements",
  ],
  "NCUA Budget & Insurance": ["ncua budget", "share insurance"],
  Interchange: ["interchange"],
  Payments: ["payments"],
  "Technology & Emerging": ["technology", "emerging"],
  "Fraud Prevention": ["fraud prevention"],
  "Data Privacy & Cyber": ["data privacy", "cybersecurity"],
  "Charter Modernization": ["charter"],
  "Defense CU Issues": ["defense"],
  "Field of Membership": ["field of membership"],
  "CDFI Fund": ["cdfi"],
  CFPB: ["cfpb", "consumer financial protection bureau"],
  "Credit Card Mandates": ["credit card"],
  TCPA: ["tcpa", "telephone consumer protection"],
  "Member Business Lending": ["member business"],
  "Mortgage & Housing": ["mortgage", "housing"],
  "CU Tax Status": ["cu tax", "credit union tax"],
};

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

function realRecords(records) {
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

function tableRecord(inspection, label) {
  const records = parseNdjson(inspection.ndjson);
  const record = records.find((item) => Array.isArray(item.values));
  if (!record) throw new Error(`inspection lacks a value table: ${label}`);
  return {
    values: record.values,
    formulas: Array.isArray(record.formulas) ? record.formulas : [],
    ndjsonSha256: sha256Bytes(
      Buffer.from(String(inspection.ndjson || ""), "utf8"),
    ),
  };
}

async function inspectTable(workbook, sheetId, range, rows, cols) {
  const inspected = await workbook.inspect({
    kind: "table",
    sheetId,
    range,
    include: "values,formulas",
    maxChars: 1600000,
    tableMaxRows: rows,
    tableMaxCols: cols,
    tableMaxCellChars: 500,
  });
  return tableRecord(inspected, `${sheetId}!${range}`);
}

function columnNumber(column) {
  let value = 0;
  for (const character of column) {
    value = value * 26 + character.charCodeAt(0) - 64;
  }
  return value;
}

function columnName(number) {
  let result = "";
  for (let value = number; value > 0; value = Math.floor((value - 1) / 26)) {
    result = String.fromCharCode(((value - 1) % 26) + 65) + result;
  }
  return result;
}

function startColumn(cellRange) {
  const match = /^([A-Z]+)\d+:/.exec(cellRange);
  if (!match) throw new Error(`invalid range: ${cellRange}`);
  return match[1];
}

function finite(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} is not a finite number`);
  }
  return value;
}

function cleanName(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function categoryCandidates(texts) {
  const joined = texts
    .filter((value) => value !== null && value !== undefined)
    .map((value) => String(value).toLowerCase())
    .join(" | ");
  return CATEGORIES.filter((category) =>
    CATEGORY_ALIASES[category].some((alias) => joined.includes(alias)),
  );
}

function counts(values) {
  const result = {};
  for (const value of values) {
    if (!value) continue;
    result[value] = (result[value] || 0) + 1;
  }
  return result;
}

function formulaAt(table, row, column) {
  const value =
    Array.isArray(table.formulas[row]) ? table.formulas[row][column] : null;
  return typeof value === "string" ? value : "";
}

async function main() {
  const [inputArg, reportArg, renderDirArg] = process.argv.slice(2);
  if (!inputArg || !reportArg || !renderDirArg) {
    throw new Error(
      "usage: node helper.cjs frozen-input.xlsx report.json render-dir",
    );
  }
  const inputPath = path.resolve(inputArg);
  const reportPath = path.resolve(reportArg);
  const renderDir = path.resolve(renderDirArg);
  const inputBytes = await fs.readFile(inputPath);
  const inputSha256 = sha256Bytes(inputBytes);
  const workbook = await SpreadsheetFile.importXlsx(
    await FileBlob.load(inputPath),
  );

  const sheetInspection = await workbook.inspect({
    kind: "sheet",
    include: "id,name",
    maxChars: 16000,
  });
  const sheetText = String(sheetInspection.ndjson || "");
  const missingSheets = REQUIRED_SHEETS.filter(
    (name) => !sheetText.includes(`"name":"${name}"`),
  );
  if (missingSheets.length) {
    throw new Error(`required sheets missing: ${missingSheets.join(", ")}`);
  }

  const rawNames = await inspectTable(
    workbook,
    "Legislative Score - Raw",
    "A3:A535",
    533,
    1,
  );
  const sublayer = await inspectTable(
    workbook,
    "Sub Layer - Raw",
    "B4:U536",
    533,
    20,
  );
  const congestion = await inspectTable(
    workbook,
    "Congestion",
    "A3:B535",
    533,
    2,
  );
  const normalizedHeaders = await inspectTable(
    workbook,
    "Legislative Score - Normalized",
    "A1:FD2",
    2,
    160,
  );
  const efficiency = await inspectTable(
    workbook,
    "Sub Layer - Raw",
    "AG1:AW536",
    536,
    17,
  );

  const congestionByName = new Map();
  congestion.values.forEach((row, offset) => {
    const name = cleanName(row[0]);
    const bioguide = String(row[1] || "").trim();
    if (!name || congestionByName.has(name)) {
      throw new Error(`blank/duplicate Congestion member at row ${offset + 3}`);
    }
    congestionByName.set(name, { bioguide, row: offset + 3 });
  });

  const members = [];
  for (let index = 0; index < 533; index += 1) {
    const rawName = cleanName((rawNames.values[index] || [])[0]);
    const sublayerName = cleanName((sublayer.values[index] || [])[0]);
    if (!rawName || rawName !== sublayerName) {
      throw new Error(
        `raw/sublayer member mismatch at offset ${index}: ` +
          `${rawName} vs ${sublayerName}`,
      );
    }
    const mapped = congestionByName.get(rawName);
    if (!mapped) throw new Error(`member absent from Congestion: ${rawName}`);
    members.push({
      name: rawName,
      bioguide: mapped.bioguide,
      bioguide_id: mapped.bioguide,
      raw_row: index + 3,
      sublayer_row: index + 4,
      congestion_row: mapped.row,
    });
  }
  if (
    members.length !== 533 ||
    new Set(members.map((row) => row.name)).size !== 533 ||
    new Set(members.map((row) => row.bioguide_id)).size !== 533 ||
    congestionByName.size !== 533
  ) {
    throw new Error("member identity coverage is not exactly 533");
  }

  const nonlegislative = {};
  const legislativeWeights = {};
  const formulaMismatches = [];
  const inspectionReceipts = [
    {
      sheet: "Legislative Score - Raw",
      range: "A3:A535",
      ndjson_sha256: rawNames.ndjsonSha256,
    },
    {
      sheet: "Sub Layer - Raw",
      range: "B4:U536",
      ndjson_sha256: sublayer.ndjsonSha256,
    },
    {
      sheet: "Congestion",
      range: "A3:B535",
      ndjson_sha256: congestion.ndjsonSha256,
    },
  ];
  for (const category of CATEGORIES) {
    const first = startColumn(BLOCK_RANGES[category]);
    const firstNumber = columnNumber(first);
    const last = columnName(firstNumber + 4);
    const normalized = await inspectTable(
      workbook,
      "Legislative Score - Normalized",
      `${first}2:${last}535`,
      534,
      5,
    );
    inspectionReceipts.push({
      sheet: "Legislative Score - Normalized",
      range: `${first}2:${last}535`,
      ndjson_sha256: normalized.ndjsonSha256,
    });
    const weights = normalized.values[0].map((value, index) =>
      finite(value, `${category} weight ${index}`),
    );
    if (weights.length !== 5) {
      throw new Error(`${category} lacks exactly five legislative weights`);
    }
    legislativeWeights[category] = weights;
    const sublayerIndex = columnNumber(SUBLAYER_COLUMNS[category]) - 2;
    const residuals = [];
    for (let index = 0; index < 533; index += 1) {
      const normalizedRow = normalized.values[index + 1];
      if (!Array.isArray(normalizedRow) || normalizedRow.length !== 5) {
        throw new Error(`${category} normalized row ${index + 3} is incomplete`);
      }
      const legislative = normalizedRow.reduce(
        (sum, value, column) =>
          sum +
          finite(value, `${category} normalized ${index}:${column}`) *
            weights[column],
        0,
      );
      const total = finite(
        sublayer.values[index][sublayerIndex],
        `${category} sublayer total ${index}`,
      );
      const residual = total - legislative;
      residuals.push(Math.abs(residual) < 1e-12 ? 0 : residual);

      const formula = formulaAt(sublayer, index, sublayerIndex);
      const compact = formula.replace(/\$/g, "").replace(/\s+/g, "");
      const expectedData = `${first}${index + 3}:${last}${index + 3}`;
      const expectedWeights = `${first}2:${last}2`;
      if (
        !formula.startsWith("=") ||
        !compact.includes(expectedData) ||
        !compact.includes(expectedWeights)
      ) {
        formulaMismatches.push({
          category,
          cell: `${SUBLAYER_COLUMNS[category]}${index + 4}`,
          expected_data_range: expectedData,
          expected_weight_range: expectedWeights,
          formula,
        });
      }
    }
    nonlegislative[category] = residuals;
  }

  const normalizedHeaderByColumn = {};
  const headerValues = normalizedHeaders.values[0] || [];
  for (let index = 0; index < headerValues.length; index += 1) {
    normalizedHeaderByColumn[columnName(index + 1)] = headerValues[index];
  }
  const efficiencySlots = [];
  const legRef = /'Legislative Score - Normalized'!\$?([A-Z]{1,3})\$?\d+/g;
  const tableBasis = /Table1\[([^\]]+)\]/g;
  for (let slotIndex = 0; slotIndex < 17; slotIndex += 1) {
    const formulas = [];
    for (let row = 3; row < 536; row += 1) {
      formulas.push(formulaAt(efficiency, row, slotIndex));
    }
    const nonempty = formulas.filter((formula) => formula);
    const referencedColumns = [];
    const bases = [];
    for (const formula of nonempty) {
      for (const match of formula.matchAll(legRef)) {
        referencedColumns.push(match[1]);
      }
      for (const match of formula.matchAll(tableBasis)) {
        bases.push(match[1]);
      }
    }
    const uniqueReferences = Object.keys(counts(referencedColumns));
    const uniqueBases = Object.keys(counts(bases));
    const workbookTexts = [
      ...(efficiency.values.slice(0, 3).map((row) => row[slotIndex])),
      ...uniqueBases,
      ...uniqueReferences.map((column) => normalizedHeaderByColumn[column]),
    ];
    const candidates = categoryCandidates(workbookTexts);
    const basisCandidates = categoryCandidates(uniqueBases);
    const referenceCandidates = categoryCandidates(
      uniqueReferences.map((column) => normalizedHeaderByColumn[column]),
    );
    const unambiguous =
      nonempty.length === 533 &&
      uniqueReferences.length === 1 &&
      uniqueBases.length === 1 &&
      candidates.length === 1 &&
      basisCandidates.length === 1 &&
      referenceCandidates.length === 1 &&
      candidates[0] === basisCandidates[0] &&
      candidates[0] === referenceCandidates[0];
    efficiencySlots.push({
      column: EFFICIENCY_COLUMNS[slotIndex],
      status: unambiguous ? "IDENTIFIED_FROM_WORKBOOK" : "UNRESOLVED",
      category_candidates: candidates,
      evidence: {
        header_values: efficiency.values
          .slice(0, 3)
          .map((row) => row[slotIndex]),
        formula_count: nonempty.length,
        referenced_normalized_column_counts: counts(referencedColumns),
        referenced_normalized_headers: Object.fromEntries(
          uniqueReferences.map((column) => [
            column,
            normalizedHeaderByColumn[column] ?? null,
          ]),
        ),
        table_basis_counts: counts(bases),
        basis_category_candidates: basisCandidates,
        reference_category_candidates: referenceCandidates,
        inference_rule:
          "one category only when 533 formulas, one normalized column, " +
          "one Table1 basis, and both independently identify the same category",
      },
    });
  }

  const errorScanSpecs = [
    ["Legislative Score - Raw", "A1:FD535"],
    ["Legislative Score - Normalized", "A1:FD535"],
    ["Sub Layer - Raw", "A1:AW536"],
    ["Congestion", "A1:Q535"],
  ];
  const errorMatches = [];
  const errorScanReceipts = [];
  for (const [sheetId, range] of errorScanSpecs) {
    const errors = await workbook.inspect({
      kind: "match",
      sheetId,
      range,
      searchTerm: ERROR_PATTERN,
      options: { useRegex: true, maxResults: 2000 },
      summary: `audited layout formula error scan: ${sheetId}!${range}`,
      maxChars: 160000,
    });
    const matches = realRecords(parseNdjson(errors.ndjson));
    errorMatches.push(
      ...matches.map((match) => ({ sheet: sheetId, range, ...match })),
    );
    errorScanReceipts.push({
      sheet: sheetId,
      range,
      match_count: matches.length,
      ndjson_sha256: sha256Bytes(
        Buffer.from(String(errors.ndjson || ""), "utf8"),
      ),
    });
  }

  await fs.mkdir(renderDir, { recursive: true });
  const renderSpecs = [
    ["Legislative Score - Raw", "A1:FD12", "legislative_raw_sample.png"],
    ["Sub Layer - Raw", "A1:AW16", "sublayer_raw_sample.png"],
    ["Congestion", "A1:Q16", "congestion_member_map_sample.png"],
  ];
  const renders = [];
  for (const [sheetName, range, fileName] of renderSpecs) {
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

  const sourceAfter = sha256Bytes(await fs.readFile(inputPath));
  const status =
    inputSha256 === sourceAfter &&
    formulaMismatches.length === 0 &&
    errorMatches.length === 0 &&
    renders.every((item) => item.size > 0)
      ? "PASS"
      : "BLOCKED";
  const report = {
    schema: SCHEMA,
    status,
    engine: "@oai/artifact-tool",
    production_written: false,
    source_workbook_sha256: inputSha256,
    source_workbook_sha256_after: sourceAfter,
    raw_block_ranges: BLOCK_RANGES,
    sublayer_category_columns: SUBLAYER_COLUMNS,
    members,
    sublayer_nonlegislative_values: nonlegislative,
    sublayer_legislative_weights: legislativeWeights,
    efficiency_slots: efficiencySlots,
    efficiency_mapping_18_to_17_approved: false,
    sublayer_formula_audit: {
      checked_cells: 533 * 18,
      mismatch_count: formulaMismatches.length,
      mismatches: formulaMismatches.slice(0, 100),
    },
    formula_error_scan: {
      status: errorMatches.length === 0 ? "PASS" : "BLOCKED",
      match_count: errorMatches.length,
      matches: errorMatches,
      audited_ranges: errorScanReceipts,
    },
    workbook_inspections: inspectionReceipts,
    renders,
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
      members: members.length,
      formula_mismatches: formulaMismatches.length,
      formula_errors: errorMatches.length,
      identified_efficiency_slots: efficiencySlots.filter(
        (slot) => slot.status === "IDENTIFIED_FROM_WORKBOOK",
      ).length,
    }),
  );
  if (status !== "PASS") process.exitCode = 2;
}

main().catch(async (error) => {
  const message = error && error.stack ? error.stack : String(error);
  console.error(message);
  process.exitCode = 2;
});
