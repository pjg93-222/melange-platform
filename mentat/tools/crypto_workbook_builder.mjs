import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { readReportPayload } from "./read_report_payload.mjs";

const require = createRequire(import.meta.url);
const artifactToolPackage = "@oai/artifact-tool";

function resolveArtifactToolEntry() {
  const configured = process.env.MENTAT_NODE_MODULES?.trim();
  if (configured) {
    try {
      return require.resolve(artifactToolPackage, { paths: [configured] });
    } catch {
      throw new Error(
        `MENTAT_NODE_MODULES cannot resolve ${artifactToolPackage}: ${configured}`,
      );
    }
  }

  try {
    return require.resolve(artifactToolPackage);
  } catch {
    // Continue through NODE_PATH and the optional Codex runtime fallback.
  }

  const nodePathCandidates = (process.env.NODE_PATH ?? "")
    .split(path.delimiter)
    .map((value) => value.trim())
    .filter(Boolean);
  for (const candidate of nodePathCandidates) {
    try {
      return require.resolve(artifactToolPackage, { paths: [candidate] });
    } catch {
      // Try the next configured module directory.
    }
  }

  const cachedNodeModules = path.join(
    os.homedir(),
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "node",
    "node_modules",
  );
  try {
    return require.resolve(artifactToolPackage, {
      paths: [cachedNodeModules],
    });
  } catch {
    throw new Error(
      `${artifactToolPackage} was not found. Set MENTAT_NODE_MODULES ` +
        "to its node_modules directory or install it in the project.",
    );
  }
}

const artifactToolEntry = resolveArtifactToolEntry();
const { SpreadsheetFile, Workbook } = await import(
  pathToFileURL(artifactToolEntry).href
);

const [dataPath, outputDir, sourceSha256 = "not-recorded"] = process.argv.slice(2);
if (!dataPath || !outputDir) {
  throw new Error(
    "usage: crypto_workbook_builder.mjs <crypto-data.json> <output-dir> [source-sha256]",
  );
}

const payload = await readReportPayload(dataPath);
const cycleFilter = String(process.env.MENTAT_CYCLE_FILTER ?? "").trim();
if (cycleFilter) {
  const selectedCycle = payload.cycles.find((cycle) =>
    cycle.label === cycleFilter || String(cycle.cycle) === cycleFilter
  );
  if (!selectedCycle) {
    throw new Error(`Unknown cycle filter: ${cycleFilter}`);
  }
  const cycleNumber = selectedCycle.cycle;
  payload.cycles = [selectedCycle];
  for (const key of [
    "raw_rows",
    "employees",
    "committee_map",
    "downstream_activity",
    "downstream_reach",
    "downstream_measurement_ledger",
    "downstream_sources",
    "transfer_paths",
    "beneficiary_bridge",
    "beneficiary_summary",
    "recipient_detail",
    "contributor_detail",
    "state_destinations",
    "recipient_locations",
  ]) {
    payload[key] = (payload[key] ?? []).filter(
      (row) => row.cycle === cycleNumber,
    );
  }
  payload.cycle_exceptions = (payload.cycle_exceptions ?? []).filter(
    (row) => row.source_cycle === cycleNumber,
  );
  if (payload.metadata?.expected_cycle_totals != null) {
    payload.metadata.expected_cycle_totals = {
      [selectedCycle.label]:
        payload.metadata.expected_cycle_totals[selectedCycle.label] ?? 0,
    };
  }
  if (payload.metadata?.omitted_non_accepted_by_cycle != null) {
    payload.metadata.omitted_non_accepted_by_cycle = {
      [String(cycleNumber)]:
        payload.metadata.omitted_non_accepted_by_cycle[String(cycleNumber)]
        ?? 0,
    };
  }
}
const deferFormulas = process.env.MENTAT_DEFER_FORMULAS === "1";
const compactRaw = process.env.MENTAT_COMPACT_RAW === "1";
const aggregateFacts = process.env.MENTAT_FORMULA_FACTS === "1";
if (payload.metadata?.workbook_projection && (!aggregateFacts || !compactRaw)) {
  throw new Error("A projected workbook payload requires compact formula-fact mode; use the full payload for raw transaction views.");
}
const industry = String(payload.metadata?.industry ?? "Crypto").trim() || "Crypto";
const reportBaseName = cycleFilter
  ? `${industry} Industry Spending Overview ${payload.cycles[0].label}`
  : `${industry} Industry Spending Overview`;
const overviewSheetName = industry === "Crypto"
  ? "Crypto Spending Overview"
  : "Spending Overview";
const associatedFundingHeader = `Associated ${industry} Funding`;
const fundingDescription = `${industry} contributions`;
await fs.mkdir(outputDir, { recursive: true });
const diagnosticsDir = path.join(path.dirname(dataPath), "final_diagnostics_v2");
await fs.mkdir(diagnosticsDir, { recursive: true });

const expectedCycleTotals = Object.fromEntries(
  payload.cycles.map((cycle) => [
    cycle.label,
    Number(payload.metadata?.expected_cycle_totals?.[cycle.label] ?? 0),
  ]),
);
if (payload.metadata?.expected_cycle_totals == null) {
  for (const row of payload.raw_rows) {
    if (
      row.include_in_analysis === "Yes"
      && Object.hasOwn(expectedCycleTotals, row.cycle_label)
    ) {
      expectedCycleTotals[row.cycle_label] += Number(row.amount || 0);
    }
  }
}
const finalizerDataPath = path.join(outputDir, "finalizer_data.json");
const finalizerCycleLabels = new Map(
  payload.cycles.map((cycle) => [cycle.cycle, cycle.label]),
);

function downstreamActivityValues(row) {
  return [
    finalizerCycleLabels.get(row.cycle) ?? String(row.cycle),
    row.source_committee,
    row.source_committee_id,
    row.source_recipient_category,
    row.candidate_name,
    row.candidate_id,
    row.candidate_party,
    [row.office_label, [row.office_state, row.district].filter(Boolean).join("-")]
      .filter(Boolean)
      .join(" "),
    row.office_state,
    row.district,
    row.activity_type,
    row.support_oppose,
    row.transaction_type,
    row.first_activity_date,
    row.last_activity_date,
    Number(row.source_rows || 0),
    Number(row.included_rows || 0),
    Number(row.excluded_rows || 0),
    Number(row.superseded_rows || 0),
    Number(row.memo_rows || 0),
    Number(row.exact_duplicate_rows || 0),
    Number(row.reported_amount || 0),
    Number(row.excluded_amount || 0),
    deferFormulas ? Number(row.preliminary_included_amount || 0) : null,
    null,
    null,
    row.source_url,
    row.filing_records,
    deferFormulas ? "PENDING" : null,
    Number(row.unverified_version_rows || 0),
    Number(row.unresolved_version_rows || 0),
    Number(row.unresolved_version_amount || 0),
    Number(row.repeated_itemization_rows || 0),
  ];
}

function beneficiaryBridgeValues(row) {
  return [
    finalizerCycleLabels.get(row.cycle) ?? String(row.cycle),
    row.view,
    row.company,
    row.source_committee,
    row.source_committee_id,
    row.candidate_name,
    row.candidate_id,
    row.candidate_party,
    row.office_label,
    row.office_state,
    row.district,
    row.activity_relationships,
    row.directions,
    Number(row.transaction_count || 0),
    row.first_activity_date,
    row.last_activity_date,
    deferFormulas ? Number(row.preliminary_downstream_amount || 0) : null,
    deferFormulas ? Number(row.preliminary_crypto_funding || 0) : null,
    null,
    null,
    row.bridge_key,
    deferFormulas ? "PENDING" : null,
  ];
}

async function writeUnitSeparatedRows(filePath, sourceRows, mapper) {
  const separator = "\u001f";
  const handle = await fs.open(filePath, "w");
  try {
    let lines = [];
    for (const sourceRow of sourceRows) {
      const line = mapper(sourceRow)
        .map((value) => {
          if (value == null) return "";
          return String(value).replaceAll(/[\u001f\r\n]/g, " ");
        })
        .join(separator);
      lines.push(line);
      if (lines.length >= 500) {
        await handle.write(`${lines.join("\n")}\n`);
        lines = [];
      }
    }
    if (lines.length > 0) {
      await handle.write(`${lines.join("\n")}\n`);
    }
  } finally {
    await handle.close();
  }
}

// artifact-tool serializes the complete workbook through an in-memory OpenXML
// stream. A six-figure audit table can exceed that stream's fixed address-space
// ceiling even when the machine still has ample RAM. For unusually large
// reports, build one-row table shells here and stream every source row into the
// shells through Excel COM immediately after export. The final workbook remains
// complete and formula-driven; only the construction path changes.
const deferLargeTables =
  deferFormulas
  && aggregateFacts
  && (payload.downstream_activity?.length ?? 0) > 10000;
let largeTableSidecars = null;
if (deferLargeTables) {
  const downstreamPath = path.join(outputDir, "downstream_activity.usv");
  const bridgePath = path.join(outputDir, "beneficiary_bridge.usv");
  await writeUnitSeparatedRows(
    downstreamPath,
    payload.downstream_activity ?? [],
    downstreamActivityValues,
  );
  await writeUnitSeparatedRows(
    bridgePath,
    payload.beneficiary_bridge ?? [],
    beneficiaryBridgeValues,
  );
  largeTableSidecars = {
    downstream_activity: {
      path: downstreamPath,
      sheet: "Downstream Activity",
      table: "DownstreamActivityTable",
      row_count: payload.downstream_activity.length,
      column_count: 33,
      numeric_columns: [15, 16, 17, 18, 19, 20, 21, 22, 23, 29, 30, 31, 32],
    },
    beneficiary_bridge: {
      path: bridgePath,
      sheet: "Beneficiary Bridge",
      table: "BeneficiaryBridgeTable",
      row_count: (payload.beneficiary_bridge ?? []).length,
      column_count: 22,
      numeric_columns: [13, 16, 17],
    },
  };
  // Empty candidate selections have no body to hydrate (for example, a
  // network containing only unknown/non-congressional candidates).
  for (const [key, spec] of Object.entries(largeTableSidecars)) {
    if (!spec.row_count) delete largeTableSidecars[key];
  }
}
await fs.writeFile(
  finalizerDataPath,
  JSON.stringify({
    metadata: {
      industry,
      formula_facts: aggregateFacts,
      report_base_name: reportBaseName,
      large_table_sidecars: largeTableSidecars,
    },
    cycles: payload.cycles,
    companies: payload.companies,
    expected_cycle_totals: expectedCycleTotals,
    employee_rank_facts: aggregateFacts
      ? payload.employees.map((employee) => [
        finalizerCycleLabels.get(employee.cycle),
        employee.company,
        employee.person_key,
        Number(employee.preliminary_total || 0),
      ])
      : [],
  }),
  "utf8",
);

// New evidence-policy payloads retain all raw decisions, including uncertain
// attribution and explicit exclusions. Legacy payloads retain their historical
// accepted-only scope and disclose any omitted records; they are not silently
// reinterpreted as products of the new rules.
const omittedNonAcceptedByCycle = new Map(
  payload.cycles.map((cycle) => [
    cycle.cycle,
    Number(
      payload.metadata?.omitted_non_accepted_by_cycle?.[String(cycle.cycle)]
      ?? 0
    ),
  ]),
);
let workbookRawRows = payload.raw_rows.filter((row) => {
  // Evidence-policy payloads preserve every raw decision, including uncertainty
  // and exclusions. Legacy payloads retain their explicit historical scope.
  if (row.company_attribution_status != null) return true;
  if (row.company_match_status === "accepted") return true;
  if (
    payload.metadata?.omitted_non_accepted_by_cycle == null
    && omittedNonAcceptedByCycle.has(row.cycle)
  ) {
    omittedNonAcceptedByCycle.set(
      row.cycle,
      omittedNonAcceptedByCycle.get(row.cycle) + 1,
    );
  }
  return false;
});

function selectDisplayedEmployees(employees) {
  const groups = new Map();
  for (const employee of employees) {
    const key = `${employee.cycle}\r${employee.company}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(employee);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .flatMap(([, group]) =>
      group
        .sort(
          (left, right) =>
            Number(right.preliminary_total || 0)
              - Number(left.preliminary_total || 0)
            || left.person.localeCompare(right.person),
        )
        .slice(0, 150),
    );
}

const displayedEmployeeRows = aggregateFacts
  ? selectDisplayedEmployees(payload.employees)
  : payload.employees;
const displayedEmployeeKeys = new Set(
  displayedEmployeeRows.map(
    (employee) =>
      `${employee.cycle}\r${employee.company}\r${employee.person_key}`,
  ),
);

function buildFormulaFacts(rows, employeeKeys) {
  const master = new Map();
  const employee = new Map();
  const recipient = new Map();
  const accumulate = (map, key, seed, row) => {
    let entry = map.get(key);
    if (!entry) {
      entry = {
        ...seed,
        amount: 0,
        gift_count: 0,
        include_in_analysis: "Yes",
        cycle_alignment: "OK",
      };
      map.set(key, entry);
    }
    entry.amount += Number(row.amount || 0);
    entry.gift_count += 1;
  };

  for (const row of rows) {
    if (row.include_in_analysis !== "Yes") continue;
    const common = {
      cycle: row.cycle,
      cycle_label: row.cycle_label,
      source_cycle: row.source_cycle,
      company: row.company,
    };
    accumulate(
      master,
      [
        row.cycle_label,
        row.company,
        row.employee_tier,
        row.party,
        row.recipient_category,
      ].join("\r"),
      {
        ...common,
        fact_type: "Master",
        employee_tier: row.employee_tier,
        party: row.party,
        recipient_category: row.recipient_category,
      },
      row,
    );
    if (
      employeeKeys.has(
        `${row.cycle}\r${row.company}\r${row.person_key}`,
      )
    ) {
      accumulate(
        employee,
        [
          row.cycle_label,
          row.company,
          row.person_key,
          row.contributor,
          row.employee_tier,
          row.party,
        ].join("\r"),
        {
          ...common,
          fact_type: "Employee",
          contributor: row.contributor,
          employee_tier: row.employee_tier,
          party: row.party,
          person_key: row.person_key,
        },
        row,
      );
    }
    accumulate(
      recipient,
      [
        row.cycle_label,
        row.company,
        row.recipient_key,
        row.recipient,
        row.committee_id,
        row.recipient_category,
        row.employee_tier,
        row.party,
      ].join("\r"),
      {
        ...common,
        fact_type: "Recipient",
        party: row.party,
        recipient_category: row.recipient_category,
        category_source: row.category_source,
        party_source: row.party_source,
        employee_tier: row.employee_tier,
        recipient: row.recipient,
        committee_id: row.committee_id,
        recipient_key: row.recipient_key,
      },
      row,
    );
  }

  const facts = [...master.values(), ...employee.values(), ...recipient.values()];
  facts.forEach((row, index) => {
    row.source_id = `FACT:${row.fact_type}:${index + 1}`;
  });
  return facts;
}

const acceptedTransactionCount = workbookRawRows.filter(
  (row) => row.include_in_analysis === "Yes",
).length;
const acceptedTransactionsByCycle = new Map(
  payload.cycles.map((cycle) => [
    cycle.cycle,
    workbookRawRows.filter(
      (row) =>
        row.cycle === cycle.cycle
        && row.include_in_analysis === "Yes",
    ).length,
  ]),
);
let detailRawRows;
if (aggregateFacts) {
  payload.raw_rows = buildFormulaFacts(
    workbookRawRows,
    displayedEmployeeKeys,
  );
  detailRawRows = payload.raw_rows;
  workbookRawRows = null;
} else {
  detailRawRows = workbookRawRows;
}
const masterDetailRows = aggregateFacts
  ? detailRawRows.filter((row) => row.fact_type === "Master")
  : detailRawRows;
const recipientDetailRows = aggregateFacts
  ? detailRawRows.filter((row) => row.fact_type === "Recipient")
  : detailRawRows;
console.log(
  `BUILD MODE compact_raw=${compactRaw ? "yes" : "no"} `
  + `formula_facts=${aggregateFacts ? "yes" : "no"} `
  + `accepted_rows=${acceptedTransactionCount.toLocaleString("en-US")} `
  + `workbook_rows=${payload.raw_rows.length.toLocaleString("en-US")} `
  + `raw_columns=${aggregateFacts ? 12 : compactRaw ? 20 : 35}`,
);

const workbook = Workbook.create();
const colors = {
  navy: "#17365D",
  blue: "#2F75B5",
  teal: "#1F6D72",
  lightBlue: "#D9EAF7",
  lightGray: "#F2F2F2",
  border: "#D9E2F3",
  white: "#FFFFFF",
  red: "#C00000",
  yellow: "#FFF2CC",
  green: "#E2F0D9",
  text: "#1F1F1F",
};
const currencyFormat = '$#,##0.00;[Red]($#,##0.00);-';
const percentFormat = '0.0%;[Red](0.0%);-';
const countFormat = '#,##0;[Red](#,##0);-';
const scopes = [
  "Total Giving",
  "Corporate",
  "Chief",
  "Middle Management",
  "Employee",
  "Super PAC",
  "Carey",
  "Conduit",
  "Other PAC",
  "Company PAC",
  "Leadership PAC",
  "Trade Association",
  "Candidate",
  "Party Committee",
  "JFC",
  "Unclassified",
];
// ActBlue and WinRed (R/report.R spending_recipient_classification) have their
// own Conduit row and Master Overview channel, directly after Carey. It stays
// declared even for an industry with none: a real zero shows as "-".
const masterCategories = [
  "Super PAC",
  "Carey",
  "Conduit",
  "Other PAC",
  "Company PAC",
  "Leadership PAC",
  "Trade Association",
  "Candidate",
  "Party Committee",
  "JFC",
  "Unclassified",
];
const masterTiers = ["Corporate", "Chief", "Middle Management", "Employee"];
const scopeLabel = (scope) => scope === "Company PAC" ? "Company PAC receipts" : scope;
const companyViewName = (company) =>
  `${String(company).trim()} View`.slice(0, 31).trimEnd();
const companyViewSheets = payload.companies.map((company) => companyViewName(company));
const sheetOrder = [
  "Master Overview",
  overviewSheetName,
  ...payload.cycles.map((cycle) => `${cycle.label} Summary`),
  "Company List",
  ...companyViewSheets,
  ...(aggregateFacts ? ["Employee Rank Facts"] : []),
  "Employee List",
  "Recipient List",
  "Beneficiary Summary",
  "PAC & Candidate Map",
  "Recipient Detail",
  "State Destinations",
  "Recipient Locations",
  "Contributor Detail",
  "Downstream Activity",
  "Beneficiary Bridge",
  "Accounting Basis",
  "Downstream Reach", "Downstream Measurement", "Downstream Sources",
  "Transfer Paths", "Funding Cohorts", "Allocation Estimates", "Allocation Coverage", "Source Freshness", "Classification Review", "Data Quality",
  ...payload.cycles.map((cycle) => cycle.label),
  "Cycle Exceptions",
];
for (const sheetName of sheetOrder) workbook.worksheets.add(sheetName);
const cycleByLabel = new Map(payload.cycles.map((cycle) => [cycle.label, cycle]));
const cycleLabelByNumber = new Map(payload.cycles.map((cycle) => [cycle.cycle, cycle.label]));

function safeTableToken(value) {
  const token = String(value).replaceAll(/[^A-Za-z0-9_]+/g, "_").replace(/^([0-9])/, "T_$1");
  return token || "Table";
}

function excelColumn(columnNumber) {
  let result = "";
  let value = columnNumber;
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function quoteSheet(name) {
  return `'${String(name).replaceAll("'", "''")}'`;
}

function excelString(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function titleBlock(sheet, title, subtitle, sourceNote, lastColumn) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastColumn}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 18 },
    verticalAlignment: "center",
    horizontalAlignment: "left",
  };
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 30;

  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: colors.lightBlue,
    font: { italic: true, color: colors.text, size: 10 },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange(`A2:${lastColumn}2`).format.rowHeight = 28;

  sheet.getRange(`A3:${lastColumn}3`).merge();
  sheet.getRange("A3").values = [[sourceNote]];
  sheet.getRange(`A3:${lastColumn}3`).format = {
    fill: colors.lightGray,
    font: { color: "#666666", size: 9 },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange(`A3:${lastColumn}3`).format.rowHeight = 24;
}

function styleTable(table) {
  table.name = table.name;
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showFilterButton = true;
}

function applyIntegrityFormatting(range, firstRow, columnLetter) {
  range.conditionalFormats.addCustom(`=$${columnLetter}${firstRow}<>"OK"`, {
    fill: colors.yellow,
    font: { bold: true, color: colors.red },
  });
}

function cycleRanges(cycleLabel) {
  const cycle = cycleByLabel.get(cycleLabel);
  if (!cycle) throw new Error(`Unknown cycle label: ${cycleLabel}`);
  const rows = payload.raw_rows.filter((row) => row.cycle === cycle.cycle);
  const first = 5;
  const last = first + rows.length - 1;
  const sheet = quoteSheet(cycle.label);
  const ref = (column) => `${sheet}!$${column}$${first}:$${column}$${last}`;
  return {
    cycle,
    rows,
    first,
    last,
    sourceIdGuard: ref("A"),
    company: ref(aggregateFacts ? "B" : "E"),
    tier: ref(aggregateFacts ? "C" : "G"),
    analysisAmount: ref(aggregateFacts ? "E" : "I"),
    party: ref(aggregateFacts ? "F" : "J"),
    category: ref(aggregateFacts ? "G" : "K"),
    included: ref(aggregateFacts ? "J" : compactRaw ? "R" : "AB"),
    personKey: ref(aggregateFacts ? "K" : compactRaw ? "S" : "AD"),
    recipientKey: ref(aggregateFacts ? "H" : compactRaw ? "N" : "P"),
    factType: aggregateFacts ? ref("A") : null,
    giftCount: aggregateFacts ? ref("I") : null,
  };
}

const cycleRangeMap = new Map(
  payload.cycles.map((cycle) => [cycle.label, cycleRanges(cycle.label)]),
);

function sumifs(rangeInfo, criteria = [], sumRange = "analysisAmount") {
  const args = [rangeInfo[sumRange]];
  for (const [rangeName, criterion] of criteria) {
    args.push(rangeInfo[rangeName], criterion);
  }
  return `SUMIFS(${args.join(",")})`;
}

function scopeFormula(rangeInfo, scope, extraCriteria = []) {
  if (aggregateFacts) {
    extraCriteria = [["factType", excelString("Master")], ...extraCriteria];
  }
  if (scope === "Total Giving" && extraCriteria.length === 0) {
    return `SUM(${rangeInfo.analysisAmount})`;
  }
  if (scope === "Total Giving") return sumifs(rangeInfo, extraCriteria);
  if (["Corporate", "Chief", "Middle Management", "Employee"].includes(scope)) {
    return sumifs(rangeInfo, [["tier", excelString(scope)], ...extraCriteria]);
  }
  const categoryMap = {
    "Super PAC": ["Super PAC"],
    Carey: ["Carey"],
    Conduit: ["Conduit"],
    "Other PAC": ["Other PAC"],
    "Company PAC": ["Company PAC"],
    "Leadership PAC": ["Leadership PAC"],
    "Trade Association": ["Trade Association"],
    Candidate: ["Candidate"],
    "Party Committee": ["Party Committee"],
    JFC: ["JFC"],
    Unclassified: ["Unclassified"],
  };
  return categoryMap[scope]
    .map((category) =>
      sumifs(rangeInfo, [["category", excelString(category)], ...extraCriteria]),
    )
    .join("+");
}

function companyFormula(rangeInfo, companyCell, extraCriteria = []) {
  if (aggregateFacts) {
    extraCriteria = [["factType", excelString("Master")], ...extraCriteria];
  }
  return sumifs(rangeInfo, [["company", companyCell], ...extraCriteria]);
}

function masterCompanyFormula(rangeInfo, companyCell, extraCriteria = []) {
  if (aggregateFacts) {
    extraCriteria = [["factType", excelString("Master")], ...extraCriteria];
  }
  const industry = extraCriteria.length === 0
    ? `SUM(${rangeInfo.analysisAmount})`
    : sumifs(rangeInfo, extraCriteria);
  const company = sumifs(
    rangeInfo,
    [["company", companyCell], ...extraCriteria],
  );
  return `IF(${companyCell}="Industry Total",${industry},${company})`;
}

function employeeFormula(rangeInfo, personKeyCell, extraCriteria = []) {
  if (aggregateFacts) {
    extraCriteria = [["factType", excelString("Employee")], ...extraCriteria];
  }
  return sumifs(rangeInfo, [["personKey", personKeyCell], ...extraCriteria]);
}

function employeeCountFormula(rangeInfo, personKeyCell) {
  if (!aggregateFacts) {
    return `COUNTIFS(${rangeInfo.personKey},${personKeyCell},${rangeInfo.included},"Yes")`;
  }
  return sumifs(
    rangeInfo,
    [
      ["personKey", personKeyCell],
      ["included", excelString("Yes")],
      ["factType", excelString("Employee")],
    ],
    "giftCount",
  );
}

function recipientFormula(rangeInfo, recipientKeyCell, extraCriteria = []) {
  if (aggregateFacts) {
    extraCriteria = [["factType", excelString("Recipient")], ...extraCriteria];
  }
  return sumifs(rangeInfo, [["recipientKey", recipientKeyCell], ...extraCriteria]);
}

function recipientCountFormula(rangeInfo, recipientKeyCell, companyCell = null) {
  if (!aggregateFacts) {
    return `COUNTIFS(${rangeInfo.recipientKey},${recipientKeyCell},${rangeInfo.included},"Yes"${
      companyCell ? `,${rangeInfo.company},${companyCell}` : ""
    })`;
  }
  const criteria = [
    ["recipientKey", recipientKeyCell],
    ["included", excelString("Yes")],
    ["factType", excelString("Recipient")],
  ];
  if (companyCell) criteria.push(["company", companyCell]);
  return sumifs(rangeInfo, criteria, "giftCount");
}

function rawRowsForRecords(records) {
  if (aggregateFacts) {
    return records.map((row) => [
      row.fact_type ?? "Exception",
      row.company,
      row.employee_tier,
      row.amount,
      deferFormulas ? (row.include_in_analysis === "Yes" ? Number(row.amount || 0) : 0) : null,
      row.party,
      row.recipient_category,
      row.recipient_key,
      Number(row.gift_count || 1),
      row.include_in_analysis,
      row.person_key,
      deferFormulas ? "PENDING" : null,
    ]);
  }
  if (compactRaw) {
    return records.map((row) => [
      row.source_id,
      row.cycle_label,
      row.source_cycle,
      row.cycle_alignment,
      row.company,
      row.contributor,
      row.employee_tier,
      row.amount,
      deferFormulas ? (row.include_in_analysis === "Yes" ? Number(row.amount || 0) : 0) : null,
      row.party,
      row.recipient_category,
      row.recipient,
      row.committee_id,
      row.recipient_key,
      row.date ? new Date(`${row.date}T00:00:00`) : null,
      row.employer,
      row.occupation,
      row.include_in_analysis,
      row.person_key,
      deferFormulas ? "PENDING" : null,
    ]);
  }
  return records.map((row) => [
      row.source_id,
      row.cycle_label,
      row.source_cycle,
      row.cycle_alignment,
      row.company,
      row.contributor,
      row.employee_tier,
      row.amount,
      deferFormulas ? (row.include_in_analysis === "Yes" ? Number(row.amount || 0) : 0) : null,
      row.party,
      row.recipient_category,
      row.category_source,
      row.party_source,
      row.recipient,
      row.committee_id,
      row.recipient_key,
      row.date ? new Date(`${row.date}T00:00:00`) : null,
      row.employer,
      row.occupation,
      row.street_1,
      row.street_2,
      row.city,
      row.state,
      row.zipcode,
      row.record_type,
      row.source_table,
      row.company_match_status,
      row.include_in_analysis,
      row.inclusion_reason,
      row.person_key,
      row.identity_basis,
      row.is_memo_leg,
      row.is_conduit_leg,
      row.superseded_by_amendment,
      row.duplicate_filing_version,
      row.duplicate_memo,
      deferFormulas ? "PENDING" : null,
    ]);
}

function rawRowsForCycle(cycle) {
  return rawRowsForRecords(
    payload.raw_rows.filter((row) => row.cycle === cycle.cycle),
  );
}

function emptyRawTablePlaceholder(headers, sheetName, exceptionSheet) {
  const values = new Map([
    ["Source ID", "NO_SOURCE_ROWS"],
    ["Fact ID", "NO_SOURCE_ROWS"],
    ["Cycle", sheetName],
    ["Fact Type", "Placeholder"],
    [
      "Cycle Alignment",
      exceptionSheet ? "Outside requested cycles" : "OK",
    ],
    ["Contributor", "No source rows"],
    ["Amount", 0],
    ["Analysis Amount", null],
    ["Gift Count", 0],
    ["Include in Analysis", "No"],
    ["Logic Check", deferFormulas ? "PENDING" : null],
  ]);
  return headers.map((header) => values.get(header) ?? null);
}

const rawHeaders = [
    "Source ID",
    "Cycle",
    "FEC Filing Cycle",
    "Cycle Alignment",
    "Company",
    "Contributor",
    "Employee Tier",
    "Amount",
    "Analysis Amount",
    "Party",
    "Recipient Category",
    "Category Source",
    "Party Source",
    "Recipient",
    "Committee ID",
    "Recipient Key",
    "Date",
    "Employer",
    "Occupation",
    "Street 1",
    "Street 2",
    "City",
    "State",
    "ZIP",
    "Record Type",
    "Source Table",
    "Company Match Status",
    "Include in Analysis",
    "Inclusion Reason",
    "Person Key",
    "Identity Basis",
    "Memo Leg",
    "Conduit Leg",
    "Superseded Amendment",
    "Duplicate Filing Version",
    "Duplicate Memo",
    "Logic Check",
];
const compactRawHeaders = [
  "Source ID",
  "Cycle",
  "FEC Filing Cycle",
  "Cycle Alignment",
  "Company",
  "Contributor",
  "Employee Tier",
  "Amount",
  "Analysis Amount",
  "Party",
  "Recipient Category",
  "Recipient",
  "Committee ID",
  "Recipient Key",
  "Date",
  "Employer",
  "Occupation",
  "Include in Analysis",
  "Person Key",
  "Logic Check",
];
const formulaFactHeaders = [
  "Fact Type",
  "Company",
  "Employee Tier",
  "Amount",
  "Analysis Amount",
  "Party",
  "Recipient Category",
  "Recipient Key",
  "Gift Count",
  "Include in Analysis",
  "Person Key",
  "Logic Check",
];

function buildRawSheet({
  sheetName,
  data,
  tableName,
  title,
  subtitle,
  sourceNote,
  exceptionSheet = false,
}) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const headers = aggregateFacts
    ? formulaFactHeaders
    : compactRaw
      ? compactRawHeaders
      : rawHeaders;
  const tableData = data.length > 0
    ? data
    : [emptyRawTablePlaceholder(headers, sheetName, exceptionSheet)];
  const lastColumn = excelColumn(headers.length);
  titleBlock(
    sheet,
    title,
    subtitle,
    sourceNote,
    lastColumn,
  );

  sheet.getRange(`A4:${lastColumn}${4 + tableData.length}`).values = [
    headers,
    ...tableData,
  ];
  const firstDataRow = 5;
  const lastDataRow = 4 + tableData.length;
  if (!deferFormulas) {
    const amountColumn = aggregateFacts ? "D" : "H";
    const analysisColumn = aggregateFacts ? "E" : "I";
    const includeColumn = aggregateFacts ? "J" : compactRaw ? "R" : "AB";
    const logicColumn = aggregateFacts ? "L" : compactRaw ? "T" : "AK";
    sheet.getRange(`${analysisColumn}${firstDataRow}`).formulas = [[
      `=IF(${includeColumn}${firstDataRow}="Yes",${amountColumn}${firstDataRow},0)`,
    ]];
    sheet.getRange(
      `${analysisColumn}${firstDataRow}:${analysisColumn}${lastDataRow}`,
    ).fillDown();
    const logicFormula = exceptionSheet
      ? `=IF(AND(${includeColumn}${firstDataRow}="No",${analysisColumn}${firstDataRow}=0),"OK","CHECK")`
      : aggregateFacts
        ? `=IF(AND(${includeColumn}${firstDataRow}="Yes",${analysisColumn}${firstDataRow}=${amountColumn}${firstDataRow}),"OK","CHECK")`
        : `=IF(AND(${analysisColumn}${firstDataRow}=IF(${includeColumn}${firstDataRow}="Yes",${amountColumn}${firstDataRow},0),OR(D${firstDataRow}="OK",D${firstDataRow}="Reassigned by contribution date")),"OK","CHECK")`;
    sheet.getRange(`${logicColumn}${firstDataRow}`).formulas = [[logicFormula]];
    sheet.getRange(`${logicColumn}${firstDataRow}:${logicColumn}${lastDataRow}`).fillDown();
  }

  const table = sheet.tables.add(`A4:${lastColumn}${lastDataRow}`, true, tableName);
  styleTable(table);
  sheet.freezePanes.freezeRows(4);

  const amountColumn = aggregateFacts ? "D" : "H";
  const analysisColumn = aggregateFacts ? "E" : "I";
  sheet.getRange(
    `${amountColumn}${firstDataRow}:${analysisColumn}${lastDataRow}`,
  ).format.numberFormat = currencyFormat;
  const dateColumn = compactRaw ? "O" : "Q";
  const logicColumn = aggregateFacts ? "L" : compactRaw ? "T" : "AK";
  if (!aggregateFacts) {
    sheet.getRange(`${dateColumn}${firstDataRow}:${dateColumn}${lastDataRow}`).format.numberFormat = "yyyy-mm-dd";
  }
  if (!compactRaw && !aggregateFacts) {
    sheet.getRange(`AF${firstDataRow}:AJ${lastDataRow}`).format.numberFormat = "0";
  }
  if (aggregateFacts) {
    sheet.getRange(`I${firstDataRow}:I${lastDataRow}`).format.numberFormat = countFormat;
  }
  applyIntegrityFormatting(
    sheet.getRange(`${logicColumn}${firstDataRow}:${logicColumn}${lastDataRow}`),
    firstDataRow,
    logicColumn,
  );
  if (!compactRaw && !aggregateFacts) {
    sheet.getRange(`AE${firstDataRow}:AE${lastDataRow}`).conditionalFormats.add(
      "beginsWith",
      {
        text: "Conservative split",
        format: { fill: colors.yellow, font: { color: "#7F6000" } },
      },
    );
  }

  const widths = {
    A: 20,
    B: 12,
    C: 13,
    D: 29,
    E: 16,
    F: 24,
    G: 18,
    H: 14,
    I: 15,
    J: 18,
    K: 20,
    L: 28,
    M: 25,
    N: 34,
    O: 14,
    P: 28,
    Q: 12,
    R: 26,
    S: 26,
    T: 20,
    U: 20,
    V: 18,
    W: 9,
    X: 12,
    Y: 14,
    Z: 18,
    AA: 20,
    AB: 17,
    AC: 38,
    AD: 36,
    AE: 48,
    AF: 10,
    AG: 11,
    AH: 18,
    AI: 20,
    AJ: 13,
    AK: 13,
  };
  const compactWidths = {
    A: 20,
    B: 12,
    C: 13,
    D: 29,
    E: 18,
    F: 26,
    G: 18,
    H: 14,
    I: 15,
    J: 14,
    K: 20,
    L: 34,
    M: 14,
    N: 28,
    O: 12,
    P: 26,
    Q: 26,
    R: 17,
    S: 36,
    T: 13,
  };
  const factWidths = {
    A: 14,
    B: 20,
    C: 18,
    D: 14,
    E: 15,
    F: 14,
    G: 20,
    H: 34,
    I: 12,
    J: 17,
    K: 36,
    L: 13,
  };
  const selectedWidths = aggregateFacts
    ? factWidths
    : compactRaw
      ? compactWidths
      : widths;
  for (const [column, width] of Object.entries(selectedWidths)) {
    sheet.getRange(`${column}1:${column}5`).format.columnWidth = width;
  }
  if (!aggregateFacts) {
    sheet.getRange(`A4:${lastColumn}${lastDataRow}`).format.verticalAlignment = "top";
  }
  return sheet;
}

for (const cycle of payload.cycles) {
  const data = rawRowsForCycle(cycle);
  const acceptedTransactions = acceptedTransactionsByCycle.get(cycle.cycle) ?? 0;
  buildRawSheet({
    sheetName: cycle.label,
    data,
    tableName: cycle.table_name,
    title: `${cycle.label} ${industry} ${
      aggregateFacts ? "Formula Facts" : "Contributions"
    }`,
    subtitle: aggregateFacts
      ? "Included observations aggregated into separate Master, Employee, and Recipient facts. Totals may contain unverified attribution or receipt roles; the Data workbook carries their flags. Every summary uses one matching fact type."
      : "Contribution-date cycle. Analysis Amount is formula-driven; FEC Filing Cycle and Cycle Alignment preserve the original reporting-period provenance.",
    sourceNote: aggregateFacts
      ? `Source DB SHA-256: ${sourceSha256} | Included observations: ${acceptedTransactions.toLocaleString("en-US")} | Formula facts: ${data.length.toLocaleString("en-US")} | Complete transaction-level audit remains in the Data workbook, cached JSON and SQLite source.`
      : `Source DB SHA-256: ${sourceSha256} | Source rows retained: ${data.length.toLocaleString("en-US")} | Legacy query matches omitted: ${(omittedNonAcceptedByCycle.get(cycle.cycle) ?? 0).toLocaleString("en-US")} (retained in source DB) | Included does not mean fully verified. | Official committee metadata: ${payload.committee_master_sources.join(" | ")}`,
  });
}

const exceptionData = rawRowsForRecords(payload.cycle_exceptions ?? []);
buildRawSheet({
  sheetName: "Cycle Exceptions",
  data: exceptionData,
  tableName: "CycleExceptionsTable",
  title: "Cycle Exceptions",
  subtitle:
    "Rows whose contribution dates fall outside 2021-2026 are retained here and excluded from every spending summary.",
  sourceNote: `Source DB SHA-256: ${sourceSha256} | Exception rows: ${exceptionData.length.toLocaleString("en-US")}`,
  exceptionSheet: true,
});

function buildMasterOverview() {
  const sheet = workbook.worksheets.getItem("Master Overview");
  const headers = [
    "Company",
    "Rank",
    "Total Giving",
    "% of Industry",
    "Democratic",
    "Republican",
    "Other / Unclassified",
    "Net Democratic %",
    "Net Republican %",
    "Party Coverage",
    "Prior Cycle",
    "$ Change",
    "% Change",
    "D $ Change",
    "R $ Change",
    "D Share Change",
    "R Share Change",
  ];
  for (const category of masterCategories) {
    headers.push(
      scopeLabel(category),
      `${scopeLabel(category)} %`,
      `${scopeLabel(category)} D`,
      `${scopeLabel(category)} R`,
      `${scopeLabel(category)} R %`,
    );
  }
  for (const tier of masterTiers) {
    headers.push(
      tier,
      `${tier} %`,
      `${tier} D`,
      `${tier} R`,
      `${tier} R %`,
    );
  }
  headers.push("Integrity Check");
  const lastColumn = excelColumn(headers.length);
  titleBlock(
    sheet,
    `${industry} Master Overview`,
    "Mentat master-workbook layout: each two-year cycle is a separate Excel table with company totals, industry share, partisan dollars and percentages, recipient channels, contributor roles, and changes versus the prior cycle.",
    `Source DB SHA-256: ${sourceSha256} | All summary values are Excel formulas linked to the cycle tables; zero values display as "-".`,
    lastColumn,
  );

  const reversedCycles = [...payload.cycles].reverse();
  let groupRow = 5;
  for (const cycle of reversedCycles) {
    const rangeInfo = cycleRangeMap.get(cycle.label);
    const priorIndex = payload.cycles.findIndex((entry) => entry.label === cycle.label) - 1;
    const priorCycle = priorIndex >= 0 ? payload.cycles[priorIndex] : null;
    const currentTotals = new Map(
      payload.companies.map((company) => [
        company,
        masterDetailRows
          .filter(
            (row) =>
              row.cycle === cycle.cycle &&
              row.company === company &&
              row.include_in_analysis === "Yes",
          )
          .reduce((sum, row) => sum + Number(row.amount || 0), 0),
      ]),
    );
    const companies = [...payload.companies].sort(
      (left, right) =>
        currentTotals.get(right) - currentTotals.get(left) ||
        left.localeCompare(right),
    );
    const rows = [...companies, "Industry Total"].map((company) => [
      company,
      ...Array(headers.length - 1).fill(null),
    ]);
    const headerRow = groupRow + 1;
    const first = headerRow + 1;
    const last = headerRow + rows.length;

    sheet.getRange(`A${groupRow}:Q${groupRow}`).merge();
    sheet.getRange(`A${groupRow}`).values = [[`${cycle.label} Company Overview`]];
    sheet.getRange(`A${groupRow}:Q${groupRow}`).format = {
      fill: colors.navy,
      font: { bold: true, color: colors.white, size: 12 },
      horizontalAlignment: "left",
      verticalAlignment: "center",
    };
    let groupStart = 18;
    for (const category of masterCategories) {
      const start = excelColumn(groupStart);
      const end = excelColumn(groupStart + 4);
      sheet.getRange(`${start}${groupRow}:${end}${groupRow}`).merge();
      sheet.getRange(`${start}${groupRow}`).values = [[scopeLabel(category)]];
      sheet.getRange(`${start}${groupRow}:${end}${groupRow}`).format = {
        fill: colors.blue,
        font: { bold: true, color: colors.white, size: 10 },
        horizontalAlignment: "center",
        verticalAlignment: "center",
      };
      groupStart += 5;
    }
    for (const tier of masterTiers) {
      const start = excelColumn(groupStart);
      const end = excelColumn(groupStart + 4);
      sheet.getRange(`${start}${groupRow}:${end}${groupRow}`).merge();
      sheet.getRange(`${start}${groupRow}`).values = [[`${tier} Contributors`]];
      sheet.getRange(`${start}${groupRow}:${end}${groupRow}`).format = {
        fill: colors.teal,
        font: { bold: true, color: colors.white, size: 10 },
        horizontalAlignment: "center",
        verticalAlignment: "center",
      };
      groupStart += 5;
    }
    sheet.getRange(`${lastColumn}${groupRow}`).values = [["Check"]];
    sheet.getRange(`${lastColumn}${groupRow}`).format = {
      fill: colors.navy,
      font: { bold: true, color: colors.white, size: 10 },
      horizontalAlignment: "center",
      verticalAlignment: "center",
    };
    sheet.getRange(`A${groupRow}:${lastColumn}${groupRow}`).format.rowHeight = 24;
    sheet.getRange(`A${headerRow}:${lastColumn}${last}`).values = [headers, ...rows];
    sheet.getRange(`A${headerRow}:${lastColumn}${headerRow}`).format = {
      wrapText: true,
      verticalAlignment: "center",
    };
    sheet.getRange(`A${headerRow}:${lastColumn}${headerRow}`).format.rowHeight = 32;

    if (!deferFormulas) {
      const formulas = rows.map((row, index) => {
        const excelRow = first + index;
        const companyCell = `$A${excelRow}`;
        const total = masterCompanyFormula(rangeInfo, companyCell);
        const democratic = masterCompanyFormula(rangeInfo, companyCell, [
          ["party", excelString("D")],
        ]);
        const republican = masterCompanyFormula(rangeInfo, companyCell, [
          ["party", excelString("R")],
        ]);
        const prior = (metric) => {
          if (!priorCycle) return "0";
          const industry = `SUMIFS(CompanyListTable[${metric}],CompanyListTable[Cycle],${excelString(priorCycle.label)})`;
          const company = `SUMIFS(CompanyListTable[${metric}],CompanyListTable[Cycle],${excelString(priorCycle.label)},CompanyListTable[Company],${companyCell})`;
          return `IF(${companyCell}="Industry Total",${industry},${company})`;
        };
        const priorTotal = prior("Total Giving");
        const priorDemocratic = prior("Democratic");
        const priorRepublican = prior("Republican");
        const result = [
          `=IF(${companyCell}="Industry Total",0,1+COUNTIFS($A$${first}:$A$${last},"<>Industry Total",$C$${first}:$C$${last},">"&C${excelRow}))`,
          `=${total}`,
          `=IF(SUM($C$${first}:$C$${last - 1})=0,0,C${excelRow}/SUM($C$${first}:$C$${last - 1}))`,
          `=${democratic}`,
          `=${republican}`,
          `=C${excelRow}-E${excelRow}-F${excelRow}`,
          `=IF(E${excelRow}+F${excelRow}=0,0,E${excelRow}/(E${excelRow}+F${excelRow}))`,
          `=IF(E${excelRow}+F${excelRow}=0,0,F${excelRow}/(E${excelRow}+F${excelRow}))`,
          `=IF(C${excelRow}=0,0,(E${excelRow}+F${excelRow})/C${excelRow})`,
          `=${priorTotal}`,
          priorCycle ? `=C${excelRow}-K${excelRow}` : "=0",
          priorCycle ? `=IF(K${excelRow}=0,0,L${excelRow}/ABS(K${excelRow}))` : "=0",
          priorCycle ? `=E${excelRow}-${priorDemocratic}` : "=0",
          priorCycle ? `=F${excelRow}-${priorRepublican}` : "=0",
          priorCycle
            ? `=H${excelRow}-IF((${priorDemocratic})+(${priorRepublican})=0,0,(${priorDemocratic})/((${priorDemocratic})+(${priorRepublican})))`
            : "=0",
          priorCycle
            ? `=I${excelRow}-IF((${priorDemocratic})+(${priorRepublican})=0,0,(${priorRepublican})/((${priorDemocratic})+(${priorRepublican})))`
            : "=0",
        ];
        let metricColumn = 18;
        for (const category of masterCategories) {
          const totalColumn = excelColumn(metricColumn);
          const democraticColumn = excelColumn(metricColumn + 2);
          const republicanColumn = excelColumn(metricColumn + 3);
          result.push(
            `=${masterCompanyFormula(rangeInfo, companyCell, [["category", excelString(category)]])}`,
            `=IF($C${excelRow}=0,0,${totalColumn}${excelRow}/$C${excelRow})`,
            `=${masterCompanyFormula(rangeInfo, companyCell, [["category", excelString(category)], ["party", excelString("D")]])}`,
            `=${masterCompanyFormula(rangeInfo, companyCell, [["category", excelString(category)], ["party", excelString("R")]])}`,
            `=IF(${democraticColumn}${excelRow}+${republicanColumn}${excelRow}=0,0,${republicanColumn}${excelRow}/(${democraticColumn}${excelRow}+${republicanColumn}${excelRow}))`,
          );
          metricColumn += 5;
        }
        for (const tier of masterTiers) {
          const totalColumn = excelColumn(metricColumn);
          const democraticColumn = excelColumn(metricColumn + 2);
          const republicanColumn = excelColumn(metricColumn + 3);
          result.push(
            `=${masterCompanyFormula(rangeInfo, companyCell, [["tier", excelString(tier)]])}`,
            `=IF($C${excelRow}=0,0,${totalColumn}${excelRow}/$C${excelRow})`,
            `=${masterCompanyFormula(rangeInfo, companyCell, [["tier", excelString(tier)], ["party", excelString("D")]])}`,
            `=${masterCompanyFormula(rangeInfo, companyCell, [["tier", excelString(tier)], ["party", excelString("R")]])}`,
            `=IF(${democraticColumn}${excelRow}+${republicanColumn}${excelRow}=0,0,${republicanColumn}${excelRow}/(${democraticColumn}${excelRow}+${republicanColumn}${excelRow}))`,
          );
          metricColumn += 5;
        }
        const categoryTotalColumns = masterCategories.map((_, offset) =>
          `${excelColumn(18 + offset * 5)}${excelRow}`,
        );
        const tierTotalColumns = masterTiers.map((_, offset) =>
          `${excelColumn(18 + masterCategories.length * 5 + offset * 5)}${excelRow}`,
        );
        result.push(
          `=IF(AND(ABS(C${excelRow}-E${excelRow}-F${excelRow}-G${excelRow})<0.01,ABS(C${excelRow}-(${categoryTotalColumns.join("+")}))<0.01,ABS(C${excelRow}-(${tierTotalColumns.join("+")}))<0.01),"OK","CHECK")`,
        );
        return result;
      });
      sheet.getRange(`B${first}:${lastColumn}${last}`).formulas = formulas;
    }

    const tableName = `MasterOverview_${cycle.cycle}`;
    const table = sheet.tables.add(
      `A${headerRow}:${lastColumn}${last}`,
      true,
      tableName,
    );
    styleTable(table);

    // Per-column average, one row per cycle block.
    //
    // It sits immediately BELOW the table and deliberately OUTSIDE the
    // ListObject. Everything about the sheet's machinery assumes the table
    // contains only companies plus Industry Total: the finalizer assigns one
    // formula to a whole column at once (Set-TableColumnFormula), so an in-table
    // row cannot hold a different formula; and its reconciliation sums every
    // non-"Industry Total" row and throws "Master Overview company sum mismatch"
    // if the result misses the industry total, which a mean row would break.
    // Staying outside the table means the Rank COUNTIFS population, the
    // "% of Industry" denominator and that reconciliation all keep excluding
    // this row structurally, with no formula edits anywhere.
    //
    // These writes must ALSO stay outside the `if (!deferFormulas)` block above:
    // production always builds with MENTAT_DEFER_FORMULAS=1, so that block never
    // runs and anything placed inside it ships blank.
    const companyFirst = first;
    const companyLast = last - 1; // last row is Industry Total
    const averageRow = last + 1;
    // Mean of the ten company rows, ratios included - the convention already used
    // for every Stats metric column (report.R) and stated in MENTAT_SEQUENCING_v2
    // as "Average = arithmetic mean of ten company rows". Averaging the ratios
    // themselves is also the only version that adds anything: the dollar-weighted
    // ratio-of-means is precisely what the Industry Total row above already shows.
    const averageFormulas = [];
    for (let column = 2; column <= headers.length; column += 1) {
      const letter = excelColumn(column);
      if (column === 2) {
        // Rank: the mean of ranks 1..N is always (N+1)/2, which would render as a
        // real rank while carrying no information. Left blank on purpose.
        averageFormulas.push("");
      } else if (column === headers.length) {
        averageFormulas.push(
          `=IF(AND(ABS(C${averageRow}-E${averageRow}-F${averageRow}-G${averageRow})<0.01,` +
            `ABS(C${averageRow}-(${masterCategories
              .map((_, offset) => `${excelColumn(18 + offset * 5)}${averageRow}`)
              .join("+")}))<0.01,` +
            `ABS(C${averageRow}-(${masterTiers
              .map((_, offset) =>
                `${excelColumn(18 + masterCategories.length * 5 + offset * 5)}${averageRow}`,
              )
              .join("+")}))<0.01),"OK","CHECK")`,
        );
      } else {
        averageFormulas.push(
          `=IF(COUNT(${letter}${companyFirst}:${letter}${companyLast})=0,"",` +
            `AVERAGE(${letter}${companyFirst}:${letter}${companyLast}))`,
        );
      }
    }
    sheet.getRange(`A${averageRow}`).values = [["Average"]];
    sheet.getRange(`B${averageRow}:${lastColumn}${averageRow}`).formulas = [
      averageFormulas,
    ];

    // Formats run to averageRow, not last: a mean left unformatted renders as a
    // raw float (0.35 instead of 35.0%).
    sheet.getRange(`B${first}:B${averageRow}`).format.numberFormat = countFormat;
    sheet.getRange(`C${first}:C${averageRow}`).format.numberFormat = currencyFormat;
    sheet.getRange(`D${first}:D${averageRow}`).format.numberFormat = percentFormat;
    sheet.getRange(`E${first}:G${averageRow}`).format.numberFormat = currencyFormat;
    sheet.getRange(`H${first}:J${averageRow}`).format.numberFormat = percentFormat;
    sheet.getRange(`K${first}:L${averageRow}`).format.numberFormat = currencyFormat;
    sheet.getRange(`M${first}:M${averageRow}`).format.numberFormat = percentFormat;
    sheet.getRange(`N${first}:O${averageRow}`).format.numberFormat = currencyFormat;
    sheet.getRange(`P${first}:Q${averageRow}`).format.numberFormat = percentFormat;
    let metricColumn = 18;
    for (const _group of [...masterCategories, ...masterTiers]) {
      sheet.getRange(
        `${excelColumn(metricColumn)}${first}:${excelColumn(metricColumn)}${averageRow}`,
      ).format.numberFormat = currencyFormat;
      sheet.getRange(
        `${excelColumn(metricColumn + 1)}${first}:${excelColumn(metricColumn + 1)}${averageRow}`,
      ).format.numberFormat = percentFormat;
      sheet.getRange(
        `${excelColumn(metricColumn + 2)}${first}:${excelColumn(metricColumn + 3)}${averageRow}`,
      ).format.numberFormat = currencyFormat;
      sheet.getRange(
        `${excelColumn(metricColumn + 4)}${first}:${excelColumn(metricColumn + 4)}${averageRow}`,
      ).format.numberFormat = percentFormat;
      metricColumn += 5;
    }
    applyIntegrityFormatting(
      sheet.getRange(`${lastColumn}${first}:${lastColumn}${averageRow}`),
      first,
      lastColumn,
    );
    sheet.getRange(`A${last}:Q${last}`).format = {
      fill: colors.lightBlue,
      font: { bold: true, color: colors.navy },
    };
    // Distinct from Industry Total's fill so the two summary rows read as
    // different things at a glance.
    sheet.getRange(`A${averageRow}:${lastColumn}${averageRow}`).format = {
      fill: colors.lightGray,
      font: { bold: true, color: colors.text },
    };
    groupRow = averageRow + 2;
  }

  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(1);
  sheet.getRange(`A1:A${groupRow}`).format.columnWidth = 21;
  sheet.getRange(`B1:B${groupRow}`).format.columnWidth = 8;
  for (const column of ["C", "E", "F", "G", "K", "L", "N", "O"]) {
    sheet.getRange(`${column}1:${column}${groupRow}`).format.columnWidth = 15;
  }
  for (const column of ["D", "H", "I", "J", "M", "P", "Q"]) {
    sheet.getRange(`${column}1:${column}${groupRow}`).format.columnWidth = 14;
  }
  let metricColumn = 18;
  for (const _group of [...masterCategories, ...masterTiers]) {
    sheet.getRange(
      `${excelColumn(metricColumn)}1:${excelColumn(metricColumn)}${groupRow}`,
    ).format.columnWidth = 14;
    for (let offset = 1; offset <= 4; offset++) {
      const column = excelColumn(metricColumn + offset);
      sheet.getRange(`${column}1:${column}${groupRow}`).format.columnWidth = 12;
    }
    metricColumn += 5;
  }
  sheet.getRange(`${lastColumn}1:${lastColumn}${groupRow}`).format.columnWidth = 13;
  return sheet;
}

function buildOverview() {
  const sheet = workbook.worksheets.getItem(overviewSheetName);
  const headers = [
    "Cycle",
    "Scope",
    "Total Giving",
    "Democratic",
    "Republican",
    "Other / Unclassified",
    "Net Democratic %",
    "Net Republican %",
    "Party Coverage",
    "Integrity Check",
  ];
  const rows = [];
  for (const cycle of payload.cycles) {
    for (const scope of scopes) rows.push([cycle.label, scope, ...Array(8).fill(null)]);
  }
  titleBlock(
    sheet,
    reportBaseName,
    "Company PAC receipts are money into the PAC. PAC outgoing spending is a separate view. Net D/R percentages use D+R; Party Coverage is (D+R)/total. Role and recipient rows are separate views of the same dollars.",
    `Source DB SHA-256: ${sourceSha256} | ${payload.metadata.raw_rows.toLocaleString("en-US")} retained rows | ${payload.metadata.cycle_exception_rows.toLocaleString("en-US")} cycle exceptions retained separately | Zero values display as "-".`,
    "J",
  );
  sheet.getRange(`A5:J${5 + rows.length}`).values = [headers, ...rows.map(row => [row[0], scopeLabel(row[1]), ...row.slice(2)])];
  const first = 6;
  const last = 5 + rows.length;
  const formulas = rows.map((row, index) => {
    const excelRow = first + index;
    const rangeInfo = cycleRangeMap.get(row[0]);
    const scope = row[1];
    return [
      `=${scopeFormula(rangeInfo, scope)}`,
      `=${scopeFormula(rangeInfo, scope, [["party", excelString("D")]])}`,
      `=${scopeFormula(rangeInfo, scope, [["party", excelString("R")]])}`,
      `=C${excelRow}-D${excelRow}-E${excelRow}`,
      `=IF(D${excelRow}+E${excelRow}=0,0,D${excelRow}/(D${excelRow}+E${excelRow}))`,
      `=IF(D${excelRow}+E${excelRow}=0,0,E${excelRow}/(D${excelRow}+E${excelRow}))`,
      `=IF(C${excelRow}=0,0,(D${excelRow}+E${excelRow})/C${excelRow})`,
      `=IF(AND(ABS(C${excelRow}-D${excelRow}-E${excelRow}-F${excelRow})<0.01,COUNTA(${rangeInfo.sourceIdGuard})=${rangeInfo.rows.length}),"OK","CHECK")`,
    ];
  });
  if (!deferFormulas) sheet.getRange(`C${first}:J${last}`).formulas = formulas;
  const table = sheet.tables.add(`A5:J${last}`, true, "OverviewTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`C${first}:F${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`G${first}:I${last}`).format.numberFormat = percentFormat;
  applyIntegrityFormatting(sheet.getRange(`J${first}:J${last}`), first, "J");
  for (const [column, width] of Object.entries({
    A: 14,
    B: 23,
    C: 16,
    D: 16,
    E: 16,
    F: 20,
    G: 15,
    H: 15,
    I: 15,
    J: 15,
  })) sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;

  const noteRow = last + 3;
  sheet.getRange(`A${noteRow}:J${noteRow}`).merge();
  sheet.getRange(`A${noteRow}`).values = [[
    "Formula integrity: native structured references expand with every Excel table. Each summary row proves Total = Democratic + Republican + Other / Unclassified, and every detail row carries a formula-driven logic check.",
  ]];
  sheet.getRange(`A${noteRow}:J${noteRow}`).format = {
    fill: colors.green,
    font: { color: "#375623", size: 10 },
    wrapText: true,
  };
  sheet.getRange(`A${noteRow}:J${noteRow}`).format.rowHeight = 34;
  return sheet;
}

function buildCompanyList() {
  const sheet = workbook.worksheets.getItem("Company List");
  const headers = [
    "Cycle",
    "Rank",
    "Company",
    "Total Giving",
    "Democratic",
    "Republican",
    "Other / Unclassified",
    "Net Democratic %",
    "Net Republican %",
    "Party Coverage",
    "Corporate",
    "Chief",
    "Middle Management",
    "Employee",
    "Integrity Check",
  ];
  const currentTotals = new Map();
  for (const cycle of payload.cycles) {
    for (const company of payload.companies) {
      const total = masterDetailRows
        .filter(
          (row) =>
            row.cycle === cycle.cycle &&
            row.company === company &&
            row.include_in_analysis === "Yes",
        )
        .reduce((sum, row) => sum + Number(row.amount || 0), 0);
      currentTotals.set(`${cycle.label}\r${company}`, total);
    }
  }
  const rows = [];
  for (const cycle of payload.cycles) {
    const companies = [...payload.companies].sort(
      (left, right) =>
        currentTotals.get(`${cycle.label}\r${right}`) -
          currentTotals.get(`${cycle.label}\r${left}`) || left.localeCompare(right),
    );
    for (const company of companies) rows.push([cycle.label, null, company, ...Array(12).fill(null)]);
  }
  titleBlock(
    sheet,
    "Company List",
    "Companies are ranked within each two-year cycle. Totals, role splits, party splits, rank, and integrity checks are Excel formulas over the cycle tables.",
    `${industry} roster: ${payload.companies.length} companies | Zero values display as “-”.`,
    "O",
  );
  sheet.getRange(`A5:O${5 + rows.length}`).values = [headers, ...rows];
  const first = 6;
  const last = 5 + rows.length;
  const formulas = rows.map((row, index) => {
    const excelRow = first + index;
    const rangeInfo = cycleRangeMap.get(row[0]);
    const companyCell = `$C${excelRow}`;
    return [
      `=1+COUNTIFS($A$${first}:$A$${last},A${excelRow},$D$${first}:$D$${last},">"&D${excelRow})`,
      `=${companyFormula(rangeInfo, companyCell)}`,
      `=${companyFormula(rangeInfo, companyCell, [["party", excelString("D")]])}`,
      `=${companyFormula(rangeInfo, companyCell, [["party", excelString("R")]])}`,
      `=D${excelRow}-E${excelRow}-F${excelRow}`,
      `=IF(E${excelRow}+F${excelRow}=0,0,E${excelRow}/(E${excelRow}+F${excelRow}))`,
      `=IF(E${excelRow}+F${excelRow}=0,0,F${excelRow}/(E${excelRow}+F${excelRow}))`,
      `=IF(D${excelRow}=0,0,(E${excelRow}+F${excelRow})/D${excelRow})`,
      `=${companyFormula(rangeInfo, companyCell, [["tier", excelString("Corporate")]])}`,
      `=${companyFormula(rangeInfo, companyCell, [["tier", excelString("Chief")]])}`,
      `=${companyFormula(rangeInfo, companyCell, [["tier", excelString("Middle Management")]])}`,
      `=${companyFormula(rangeInfo, companyCell, [["tier", excelString("Employee")]])}`,
      `=IF(AND(ABS(D${excelRow}-E${excelRow}-F${excelRow}-G${excelRow})<0.01,ABS(D${excelRow}-K${excelRow}-L${excelRow}-M${excelRow}-N${excelRow})<0.01),"OK","CHECK")`,
    ];
  });
  if (!deferFormulas) {
    sheet.getRange(`B${first}:B${last}`).formulas = formulas.map((row) => [row[0]]);
    sheet.getRange(`D${first}:O${last}`).formulas = formulas.map((row) => row.slice(1));
  }
  const table = sheet.tables.add(`A5:O${last}`, true, "CompanyListTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`B${first}:B${last}`).format.numberFormat = countFormat;
  sheet.getRange(`D${first}:G${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`H${first}:J${last}`).format.numberFormat = percentFormat;
  sheet.getRange(`K${first}:N${last}`).format.numberFormat = currencyFormat;
  applyIntegrityFormatting(sheet.getRange(`O${first}:O${last}`), first, "O");
  for (const [column, width] of Object.entries({
    A: 14,
    B: 8,
    C: 18,
    D: 16,
    E: 15,
    F: 15,
    G: 20,
    H: 15,
    I: 15,
    J: 15,
    K: 15,
    L: 15,
    M: 20,
    N: 15,
    O: 15,
  })) sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  return sheet;
}

function buildEmployeeList() {
  const sheet = workbook.worksheets.getItem("Employee List");
  const headers = [
    "Cycle",
    "Industry Rank",
    "Company Rank",
    "Person",
    "Company",
    "Total Giving",
    "Democratic",
    "Republican",
    "Other / Unclassified",
    "Net Democratic %",
    "Net Republican %",
    "Party Coverage",
    "Contribution Count",
    "Cities / States",
    "ZIPs",
    "Titles",
    "Identity Basis",
    "Person Key",
    "Integrity Check",
  ];
  const cycleLabelFor = new Map(payload.cycles.map((cycle) => [cycle.cycle, cycle.label]));
  const employeeRows = displayedEmployeeRows;
  const rows = employeeRows.map((employee) => [
    cycleLabelFor.get(employee.cycle),
    null,
    null,
    employee.person,
    employee.company,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    employee.locations,
    employee.zipcodes,
    employee.titles,
    employee.identity_basis,
    employee.person_key,
    null,
  ]);
  titleBlock(
    sheet,
    "Employee List",
    "People carry both an industry-wide cycle rank and a within-company cycle rank. Identities are consolidated only on high-confidence company/name/location/title evidence.",
    aggregateFacts
      ? `${rows.length.toLocaleString("en-US")} top-per-company cycle-person groups displayed and formula-driven | All ${payload.metadata.employee_groups.toLocaleString("en-US")} groups remain in the validated Employee Rank Facts basis.`
      : `${payload.metadata.employee_groups} cycle-person groups | ${payload.metadata.identity_review_groups} conservative same-name splits retained rather than guessed together.`,
    "S",
  );
  sheet.getRange(`A5:S${5 + rows.length}`).values = [headers, ...rows];
  const first = 6;
  const last = 5 + rows.length;
  const formulas = rows.map((row, index) => {
    const excelRow = first + index;
    const rangeInfo = cycleRangeMap.get(row[0]);
    const keyCell = `$R${excelRow}`;
    return [
      aggregateFacts
        ? `=1+COUNTIFS(EmployeeRankFactsTable[Cycle],A${excelRow},EmployeeRankFactsTable[Total Giving],">"&F${excelRow})`
        : `=1+COUNTIFS($A$${first}:$A$${last},A${excelRow},$F$${first}:$F$${last},">"&F${excelRow})`,
      aggregateFacts
        ? `=1+COUNTIFS(EmployeeRankFactsTable[Cycle],A${excelRow},EmployeeRankFactsTable[Company],E${excelRow},EmployeeRankFactsTable[Total Giving],">"&F${excelRow})`
        : `=1+COUNTIFS($A$${first}:$A$${last},A${excelRow},$E$${first}:$E$${last},E${excelRow},$F$${first}:$F$${last},">"&F${excelRow})`,
      `=${employeeFormula(rangeInfo, keyCell)}`,
      `=${employeeFormula(rangeInfo, keyCell, [["party", excelString("D")]])}`,
      `=${employeeFormula(rangeInfo, keyCell, [["party", excelString("R")]])}`,
      `=F${excelRow}-G${excelRow}-H${excelRow}`,
      `=IF(G${excelRow}+H${excelRow}=0,0,G${excelRow}/(G${excelRow}+H${excelRow}))`,
      `=IF(G${excelRow}+H${excelRow}=0,0,H${excelRow}/(G${excelRow}+H${excelRow}))`,
      `=IF(F${excelRow}=0,0,(G${excelRow}+H${excelRow})/F${excelRow})`,
      `=${employeeCountFormula(rangeInfo, keyCell)}`,
      `=IF(ABS(F${excelRow}-G${excelRow}-H${excelRow}-I${excelRow})<0.01,"OK","CHECK")`,
    ];
  });
  if (!deferFormulas) {
    sheet.getRange(`B${first}:C${last}`).formulas = formulas.map((row) => row.slice(0, 2));
    sheet.getRange(`F${first}:M${last}`).formulas = formulas.map((row) => row.slice(2, 10));
    sheet.getRange(`S${first}:S${last}`).formulas = formulas.map((row) => [row[10]]);
  }
  const table = sheet.tables.add(`A5:S${last}`, true, "EmployeeListTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`B${first}:C${last}`).format.numberFormat = countFormat;
  sheet.getRange(`F${first}:I${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`J${first}:L${last}`).format.numberFormat = percentFormat;
  sheet.getRange(`M${first}:M${last}`).format.numberFormat = countFormat;
  sheet.getRange(`Q${first}:Q${last}`).conditionalFormats.add("beginsWith", {
    text: "Conservative split",
    format: { fill: colors.yellow, font: { color: "#7F6000" } },
  });
  applyIntegrityFormatting(sheet.getRange(`S${first}:S${last}`), first, "S");
  for (const [column, width] of Object.entries({
    A: 14,
    B: 8,
    C: 12,
    D: 25,
    E: 18,
    F: 16,
    G: 15,
    H: 15,
    I: 20,
    J: 15,
    K: 15,
    L: 15,
    M: 16,
    N: 36,
    O: 24,
    P: 34,
    Q: 48,
    R: 42,
    S: 15,
  })) sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  sheet.getRange(`N${first}:R${last}`).format.wrapText = true;
  return sheet;
}

function buildEmployeeRankFacts() {
  if (!aggregateFacts) return null;
  const sheet = workbook.worksheets.getItem("Employee Rank Facts");
  titleBlock(
    sheet,
    "Employee Rank Facts",
    "Complete cycle-person rank basis for the curated Employee List.",
    "Excel finalization writes the complete validated cycle-person basis here. Displayed employee totals remain Excel formulas; the full rank basis uses exporter-verified preliminary totals to avoid duplicating tens of thousands of hidden employee facts.",
    "D",
  );
  sheet.getRange("A5:D5").values = [[
    "Cycle",
    "Company",
    "Person Key",
    "Total Giving",
  ]];
  sheet.getRange("A5:D5").format.fill = colors.blue;
  sheet.getRange("A5:D5").format.font = {
    bold: true,
    color: colors.white,
  };
  sheet.freezePanes.freezeRows(5);
  for (const [column, width] of Object.entries({
    A: 14,
    B: 22,
    C: 42,
    D: 18,
  })) {
    sheet.getRange(`${column}1:${column}5`).format.columnWidth = width;
  }
  return sheet;
}

function mostCommon(rows, field) {
  const counts = new Map();
  for (const row of rows) {
    const value = String(row[field] ?? "").trim();
    if (!value) continue;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts.entries()].sort(
    (left, right) => right[1] - left[1] || left[0].localeCompare(right[0]),
  )[0]?.[0] ?? "";
}

function sourceSet(rows, field) {
  return [...new Set(rows.map((row) => String(row[field] ?? "").trim()).filter(Boolean))]
    .sort()
    .join("; ");
}

function buildRecipientList() {
  const sheet = workbook.worksheets.getItem("Recipient List");
  const headers = [
    "Cycle",
    "View",
    "Company",
    "Rank",
    "Recipient",
    "Committee ID",
    "Recipient Category",
    "Direct Candidate / Sponsor",
    "Committee Relationship",
    "Ownership Status",
    "Top Downstream Congressional Beneficiary",
    "Top Downstream Candidate ID",
    "Top Downstream Activity",
    "Total Giving",
    "Democratic",
    "Republican",
    "Other / Unclassified",
    "Net Democratic %",
    "Net Republican %",
    "Party Coverage",
    "Corporate",
    "Chief",
    "Middle Management",
    "Employee",
    "Contribution Count",
    "Category Source",
    "Party Source",
    "Recipient Key",
    "FEC Committee URL",
    "Integrity Check",
  ];
  const industryGroups = new Map();
  const companyGroups = new Map();
  for (const row of recipientDetailRows) {
    if (row.include_in_analysis !== "Yes") continue;
    const industryKey = `${row.cycle_label}\r${row.recipient_key}`;
    const companyKey = `${row.cycle_label}\r${row.company}\r${row.recipient_key}`;
    if (!industryGroups.has(industryKey)) industryGroups.set(industryKey, []);
    if (!companyGroups.has(companyKey)) companyGroups.set(companyKey, []);
    industryGroups.get(industryKey).push(row);
    companyGroups.get(companyKey).push(row);
  }
  const makeEntry = (group, view, company) => ({
    cycleLabel: group[0].cycle_label,
    view,
    company,
    recipientKey: group[0].recipient_key,
    recipient: mostCommon(group, "recipient"),
    committeeId: mostCommon(group, "committee_id"),
    category: mostCommon(group, "recipient_category"),
    categorySource: sourceSet(group, "category_source"),
    partySource: sourceSet(group, "party_source"),
    preliminaryTotal: group.reduce((sum, row) => sum + Number(row.amount || 0), 0),
  });
  const entries = [
    ...[...industryGroups.values()].map((group) => makeEntry(group, "Industry", "Industry Total")),
    ...[...companyGroups.values()].map((group) => makeEntry(group, "Company", group[0].company)),
  ];
  const cycleOrder = new Map(payload.cycles.map((cycle, index) => [cycle.label, index]));
  entries.sort(
    (left, right) =>
      cycleOrder.get(left.cycleLabel) - cycleOrder.get(right.cycleLabel) ||
      (left.view === "Industry" ? 0 : 1) - (right.view === "Industry" ? 0 : 1) ||
      left.company.localeCompare(right.company) ||
      right.preliminaryTotal - left.preliminaryTotal ||
      left.recipient.localeCompare(right.recipient),
  );
  const rows = entries.map((entry) => [
    entry.cycleLabel,
    entry.view,
    entry.company,
    null,
    entry.recipient,
    entry.committeeId,
    entry.category,
    ...Array(18).fill(null),
    entry.categorySource,
    entry.partySource,
    entry.recipientKey,
    null,
    null,
  ]);
  titleBlock(
    sheet,
    "Recipient List",
    "Filter View = Industry for cycle-wide leaders or View = Company plus Company for within-company leaders. Every rank and total is formula-linked to native cycle tables.",
    `${entries.length.toLocaleString("en-US")} industry/company cycle-recipient groups | Official FEC committee masters: ${payload.committee_master_sources.join(" | ")}`,
    "AD",
  );
  sheet.getRange(`A5:AD${5 + rows.length}`).values = [headers, ...rows];
  const first = 6;
  const last = 5 + rows.length;
  const formulas = rows.map((row, index) => {
    const excelRow = first + index;
    const rangeInfo = cycleRangeMap.get(row[0]);
    const keyCell = `$AB${excelRow}`;
    const companyCriteria = row[1] === "Company" ? [["company", `$C${excelRow}`]] : [];
    return {
      rank: `=1+COUNTIFS($A$${first}:$A$${last},A${excelRow},$B$${first}:$B$${last},B${excelRow},$C$${first}:$C$${last},C${excelRow},$N$${first}:$N$${last},">"&N${excelRow})`,
      metrics: [
        `=${recipientFormula(rangeInfo, keyCell, companyCriteria)}`,
        `=${recipientFormula(rangeInfo, keyCell, [...companyCriteria, ["party", excelString("D")]])}`,
        `=${recipientFormula(rangeInfo, keyCell, [...companyCriteria, ["party", excelString("R")]])}`,
        `=N${excelRow}-O${excelRow}-P${excelRow}`,
        `=IF(O${excelRow}+P${excelRow}=0,0,O${excelRow}/(O${excelRow}+P${excelRow}))`,
        `=IF(O${excelRow}+P${excelRow}=0,0,P${excelRow}/(O${excelRow}+P${excelRow}))`,
        `=IF(N${excelRow}=0,0,(O${excelRow}+P${excelRow})/N${excelRow})`,
        `=${recipientFormula(rangeInfo, keyCell, [...companyCriteria, ["tier", excelString("Corporate")]])}`,
        `=${recipientFormula(rangeInfo, keyCell, [...companyCriteria, ["tier", excelString("Chief")]])}`,
        `=${recipientFormula(rangeInfo, keyCell, [...companyCriteria, ["tier", excelString("Middle Management")]])}`,
        `=${recipientFormula(rangeInfo, keyCell, [...companyCriteria, ["tier", excelString("Employee")]])}`,
        `=${recipientCountFormula(
          rangeInfo,
          keyCell,
          row[1] === "Company" ? `$C${excelRow}` : null,
        )}`,
      ],
      url: `=IF(F${excelRow}="","","https://www.fec.gov/data/committee/"&F${excelRow}&"/")`,
      integrity: `=IF(AND(ABS(N${excelRow}-O${excelRow}-P${excelRow}-Q${excelRow})<0.01,ABS(N${excelRow}-U${excelRow}-V${excelRow}-W${excelRow}-X${excelRow})<0.01),"OK","CHECK")`,
    };
  });
  if (!deferFormulas) {
    sheet.getRange(`D${first}:D${last}`).formulas = formulas.map((row) => [row.rank]);
    sheet.getRange(`N${first}:Y${last}`).formulas = formulas.map((row) => row.metrics);
    sheet.getRange(`AC${first}:AD${last}`).formulas = formulas.map((row) => [
      row.url,
      row.integrity,
    ]);
  }
  const table = sheet.tables.add(`A5:AD${last}`, true, "RecipientListTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`D${first}:D${last}`).format.numberFormat = countFormat;
  sheet.getRange(`M${first}:Q${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`R${first}:T${last}`).format.numberFormat = percentFormat;
  sheet.getRange(`U${first}:X${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`Y${first}:Y${last}`).format.numberFormat = countFormat;
  applyIntegrityFormatting(sheet.getRange(`AD${first}:AD${last}`), first, "AD");
  const widths = {
    A: 14, B: 12, C: 20, D: 8, E: 38, F: 15, G: 20, H: 34,
    I: 34, J: 38, K: 34, L: 15, M: 18, N: 16, O: 15, P: 15,
    Q: 20, R: 15, S: 15, T: 15, U: 15, V: 15, W: 20, X: 15,
    Y: 17, Z: 38, AA: 34, AB: 30, AC: 42, AD: 15,
  };
  for (const [column, width] of Object.entries(widths)) {
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  }
  sheet.getRange(`E${first}:K${last}`).format.wrapText = true;
  sheet.getRange(`Z${first}:AC${last}`).format.wrapText = true;
  return sheet;
}

function buildPacCandidateMap() {
  const sheet = workbook.worksheets.getItem("PAC & Candidate Map");
  const headers = [
    "Cycle", "Recipient Committee", "Committee ID", "Lookup Key",
    "Recipient Category", "Direct Candidate / Sponsor", "Candidate ID(s)",
    "Direct Party", "Office(s)", "Relationship", "Ownership Status",
    "Connected Organization", "Top Downstream Congressional Beneficiary",
    "Top Downstream Candidate ID", "Top Downstream Activity", "Mapping Source",
    "FEC Committee URL", "FEC Candidate URL", "Integrity Check",
  ];
  const rows = [...(payload.committee_map ?? [])]
    .sort((left, right) => left.cycle - right.cycle || left.committee_name.localeCompare(right.committee_name))
    .map((row) => [
      cycleLabelByNumber.get(row.cycle) ?? String(row.cycle),
      row.committee_name,
      row.committee_id,
      `${cycleLabelByNumber.get(row.cycle) ?? row.cycle}|${row.committee_id}`,
      row.recipient_category,
      row.direct_person,
      row.direct_candidate_ids,
      row.direct_party,
      row.direct_offices,
      row.relationship,
      row.ownership_status,
      row.connected_organization,
      row.top_downstream_person,
      row.top_downstream_candidate_id,
      deferFormulas ? Number(row.preliminary_top_downstream_amount || 0) : null,
      row.mapping_source,
      null,
      null,
      deferFormulas ? "PENDING" : null,
    ]);
  titleBlock(
    sheet,
    "PAC and Candidate / Officeholder Map",
    "Candidate and leadership-PAC relationships are direct FEC linkages. Other PACs and organizations correctly show no individual owner; their candidate activity is reported separately.",
    `${rows.length.toLocaleString("en-US")} recipient committee-cycle groups | Candidate master, candidate-committee linkage, leadership-PAC sponsor, and committee-to-candidate FEC sources.`,
    "S",
  );
  const last = 5 + rows.length;
  sheet.getRange(`A5:S${last}`).values = [headers, ...rows];
  const table = sheet.tables.add(`A5:S${last}`, true, "PACMapTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`O6:O${last}`).format.numberFormat = currencyFormat;
  applyIntegrityFormatting(sheet.getRange(`S6:S${last}`), 6, "S");
  const widths = [14,38,15,30,20,34,24,16,24,36,42,34,36,18,18,52,42,42,15];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`B6:N${last}`).format.wrapText = true;
  return sheet;
}

// Fine-grain chartable detail sheets.
//
// These two sheets are the only published place a chart can group by committee,
// committee type, designation, ZIP, city, state, employer or occupation. The
// raw transaction sheet cannot serve that purpose: prepare_workbook_payload.py
// projects raw_rows onto 18 fields that carry no geography, employer or
// occupation at all, and formula-fact mode replaces raw_rows outright with
// aggregates. Both sheets are therefore built from payload.recipient_detail and
// payload.contributor_detail - their own payload keys, which no compaction mode
// touches - and never from raw_rows. Dates are published as yyyy-mm-dd TEXT,
// matching every other date column in this workbook, so the console reads the
// same string the exporter wrote instead of a locale-dependent serial.
function detailPlaceholder(headers, numericHeaders) {
  // build_industry_report.ps1 refuses any Excel table whose ref covers only its
  // header row, so an empty frame still has to publish one visible row saying
  // that it is empty.
  return headers.map((header, index) =>
    index === 0 ? "No source rows" : numericHeaders.has(header) ? 0 : "");
}

function detailSourceNote(rows, folded) {
  return `Source DB SHA-256: ${sourceSha256} | Rows: ${rows.toLocaleString("en-US")} `
    + `| Folded rows: ${folded.toLocaleString("en-US")} `
    + `| Row cap: ${Number(payload.metadata?.detail_row_cap ?? 0).toLocaleString("en-US")}`;
}

function buildRecipientDetail() {
  const sheet = workbook.worksheets.getItem("Recipient Detail");
  const headers = [
    "Cycle", "Company", "Recipient", "Committee ID", "Committee Type",
    "Committee Type Label", "Designation", "Designation Label",
    "Recipient Category", "Party", "Direct Candidate / Sponsor", "Relationship",
    "Connected Organization", "Total Giving", "Gift Count", "Contributors",
    "First Date", "Last Date", "Rows Folded",
  ];
  const numericHeaders = new Set([
    "Total Giving", "Gift Count", "Contributors", "Rows Folded",
  ]);
  // Sponsor identity is joined in as a STATIC value. The console reads cell
  // values, so a lookup formula would reach it as an empty cache.
  const sponsors = new Map(
    (payload.committee_map ?? []).map((row) => [
      `${row.cycle}|${row.committee_id}`,
      row,
    ]),
  );
  const source = payload.recipient_detail ?? [];
  const rows = [...source]
    .sort((left, right) =>
      left.cycle - right.cycle
      || Number(right.total_giving || 0) - Number(left.total_giving || 0)
      || String(left.recipient ?? "").localeCompare(String(right.recipient ?? "")))
    .map((row) => {
      const sponsor = sponsors.get(`${row.cycle}|${row.committee_id}`) ?? {};
      return [
        cycleLabelByNumber.get(row.cycle) ?? row.cycle_label ?? String(row.cycle),
        row.company ?? "",
        row.recipient ?? "",
        row.committee_id ?? "",
        row.committee_type ?? "",
        row.committee_type_label ?? "",
        row.designation ?? "",
        row.designation_label ?? "",
        row.recipient_category ?? "",
        row.party ?? "",
        sponsor.direct_person ?? "",
        sponsor.relationship ?? "",
        sponsor.connected_organization ?? "",
        Number(row.total_giving || 0),
        Number(row.gift_count || 0),
        Number(row.contributors || 0),
        row.first_date ?? "",
        row.last_date ?? "",
        Number(row.folded_rows || 0),
      ];
    });
  const folded = rows.reduce((total, row) => total + Number(row[18] || 0), 0);
  titleBlock(
    sheet,
    "Recipient Detail",
    "One row per recipient committee per company per cycle. Static values: totals reconcile to the cycle totals; smaller rows beyond the row cap are folded into an Other row per company and cycle.",
    detailSourceNote(rows.length, folded),
    "S",
  );
  const body = rows.length ? rows : [detailPlaceholder(headers, numericHeaders)];
  const last = 5 + body.length;
  sheet.getRange(`A5:S${last}`).values = [headers, ...body];
  const table = sheet.tables.add(`A5:S${last}`, true, "RecipientDetailTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`N6:N${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`O6:P${last}`).format.numberFormat = countFormat;
  sheet.getRange(`S6:S${last}`).format.numberFormat = countFormat;
  const widths = [14, 28, 42, 15, 16, 32, 14, 30, 20, 10, 34, 30, 34, 18, 14, 14, 14, 14, 14];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`B6:M${last}`).format.wrapText = true;
  return sheet;
}

function buildContributorDetail() {
  const sheet = workbook.worksheets.getItem("Contributor Detail");
  const headers = [
    "Cycle", "Company", "Employer", "Occupation", "Street 1", "Street 2",
    "City", "State", "ZIP", "Total Giving", "Gift Count", "Contributors",
    "Rows Folded",
  ];
  const numericHeaders = new Set([
    "Total Giving", "Gift Count", "Contributors", "Rows Folded",
  ]);
  const source = payload.contributor_detail ?? [];
  const rows = [...source]
    .sort((left, right) =>
      left.cycle - right.cycle
      || Number(right.total_giving || 0) - Number(left.total_giving || 0)
      || String(left.state ?? "").localeCompare(String(right.state ?? ""))
      || String(left.city ?? "").localeCompare(String(right.city ?? ""))
      || String(left.zip5 ?? "").localeCompare(String(right.zip5 ?? "")))
    .map((row) => [
      cycleLabelByNumber.get(row.cycle) ?? row.cycle_label ?? String(row.cycle),
      row.company ?? "",
      row.employer ?? "",
      row.occupation ?? "",
      row.street_1 ?? "",
      row.street_2 ?? "",
      row.city ?? "",
      row.state ?? "",
      row.zip5 ?? "",
      Number(row.total_giving || 0),
      Number(row.gift_count || 0),
      Number(row.contributors || 0),
      Number(row.folded_rows || 0),
    ]);
  const folded = rows.reduce((total, row) => total + Number(row[12] || 0), 0);
  titleBlock(
    sheet,
    "Contributor Detail",
    "One row per contributor employer / occupation / street / city / state / ZIP per company per cycle. Static values: totals reconcile to the cycle totals; smaller rows beyond the row cap are folded into an Other row per company and cycle. A blank ZIP is a contribution whose filed ZIP is missing or not five digits, not a dropped row.",
    detailSourceNote(rows.length, folded),
    "M",
  );
  const body = rows.length ? rows : [detailPlaceholder(headers, numericHeaders)];
  const last = 5 + body.length;
  sheet.getRange(`A5:M${last}`).values = [headers, ...body];
  const table = sheet.tables.add(`A5:M${last}`, true, "ContributorDetailTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`J6:J${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`K6:M${last}`).format.numberFormat = countFormat;
  const widths = [14, 28, 42, 32, 20, 20, 24, 10, 12, 18, 14, 14, 14];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`B6:I${last}`).format.wrapText = true;
  return sheet;
}

function buildAuditTable(name, sourceRows, fields, explanation, complete = false) {
  const sheet = workbook.worksheets.getItem(name);
  if (complete && sourceRows.length > 1048570) throw new Error(name + " exceeds Excel's row capacity; cannot truncate destination data.");
  const shown = complete ? sourceRows : sourceRows.slice(0, 10000);
  const headers = fields.map(([key, label]) => label);
  const values = shown.map(row => fields.map(([key]) => {
    const value = row[key];
    return value == null ? "" : typeof value === "object" ? JSON.stringify(value) : value;
  }));
  const lastColumn = excelColumn(headers.length);
  titleBlock(sheet, name, explanation,
    `${sourceRows.length.toLocaleString("en-US")} records; ${shown.length.toLocaleString("en-US")} shown. ${complete ? 'Complete destination data.' : 'Full records are in the accompanying audit CSV files.'}`, lastColumn);
  const last = 5 + Math.max(1, values.length);
  sheet.getRange(`A5:${lastColumn}${last}`).values = [headers, ...(values.length ? values : [headers.map(() => "")])];
  const table = sheet.tables.add(`A5:${lastColumn}${last}`, true, safeTableToken(name) + "Table");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  fields.forEach(([key], index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = /amount|funding|unresolved|activity|direct/.test(key) ? 22 : 27;
    if (/amount|funding|^unresolved$|activity$|^direct$|estimated_non_opposition|estimated_opposition/.test(key)) {
      sheet.getRange(`${column}6:${column}${last}`).format.numberFormat = currencyFormat;
    }
  });
}

function buildDownstreamActivity() {
  const sheet = workbook.worksheets.getItem("Downstream Activity");
  const headers = [
    "Cycle", "Source Committee", "Source Committee ID", "Source Recipient Category",
    "Candidate / Officeholder", "Candidate ID", "Candidate Party", "Office",
    "State", "District", "Activity Type", "Support / Oppose", "Transaction Type",
    "First Activity Date", "Last Activity Date", "Source Rows", "Included Rows",
    "Excluded Rows", "Superseded Rows", "Memo Rows", "Duplicate Rows",
    "Reported Amount", "Excluded Amount", "Net Included Amount", "FEC Committee URL",
    "FEC Candidate URL", "Source URL", "Filing Records", "Logic Check",
    "Unverified Version Rows", "Unresolved Version Rows", "Unresolved Version Amount", "Repeated Itemization Rows",
  ];
  const sourceRows = payload.downstream_activity ?? [];
  const rows = deferLargeTables && sourceRows.length
    ? [downstreamActivityValues(sourceRows[0])]
    : sourceRows.map(downstreamActivityValues);
  titleBlock(
    sheet,
    "Downstream Committee-to-Candidate Activity",
    `This is same-cycle activity by committees funded in the ${industry} dataset; it is not dollar-level pass-through attribution. Reported minus excluded amendment/memo rows equals Net Included Amount.`,
    `${payload.metadata.downstream_rows.toLocaleString("en-US")} exact source rows condensed to ${sourceRows.length.toLocaleString("en-US")} committee-candidate-activity groups | ${payload.metadata.downstream_superseded_rows.toLocaleString("en-US")} superseded and ${payload.metadata.downstream_memo_rows.toLocaleString("en-US")} memo rows excluded.`,
    "AG",
  );
  const last = 5 + rows.length;
  sheet.getRange(`A5:AG${last}`).values = [headers, ...rows];
  const table = sheet.tables.add(`A5:AG${last}`, true, "DownstreamActivityTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  if (!rows.length) return sheet;
  sheet.getRange(`N6:O${last}`).format.numberFormat = "yyyy-mm-dd";
  sheet.getRange(`P6:U${last}`).format.numberFormat = countFormat;
  sheet.getRange(`V6:X${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`AD6:AE${last}`).format.numberFormat = countFormat;
  sheet.getRange(`AF6:AF${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`AG6:AG${last}`).format.numberFormat = countFormat;
  applyIntegrityFormatting(sheet.getRange(`AC6:AC${last}`), 6, "AC");
  const widths = [14,38,15,20,34,15,16,22,9,10,34,18,15,14,14,12,12,12,14,10,12,18,18,18,42,42,54,64,15,20,20,22,22];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`B6:M${last}`).format.wrapText = true;
  return sheet;
}

function buildBeneficiaryBridge() {
  const sheet = workbook.worksheets.getItem("Beneficiary Bridge");
  const headers = [
    "Cycle", "View", "Company", "Source Committee", "Source Committee ID",
    "Candidate / Officeholder", "Candidate ID", "Candidate Party", "Office", "State",
    "District", "Activity Relationships", "Directions", "Transaction Count",
    "First Activity Date", "Last Activity Date", "Downstream Activity",
    associatedFundingHeader, "FEC Committee URL", "FEC Candidate URL",
    "Reference Key", "Integrity Check",
  ];
  const sourceRows = payload.beneficiary_bridge ?? [];
  const rows = deferLargeTables && sourceRows.length
    ? [beneficiaryBridgeValues(sourceRows[0])]
    : sourceRows.map(beneficiaryBridgeValues);
  titleBlock(
    sheet,
    "Beneficiary Bridge",
    "Observed activity of reachable spending committees, counted once per cycle/view/company/candidate even across shared routes. See Downstream Reach. Associated funding stays at its actual recipient; an indirect committee can show zero associated receipts and nonzero activity. Company views overlap and must not be added together.",
    `${sourceRows.length.toLocaleString("en-US")} company/industry committee-candidate paths | Top 100 congressional beneficiaries retained per cycle and view.`,
    "V",
  );
  const last = 5 + rows.length;
  sheet.getRange(`A5:V${last}`).values = [headers, ...rows];
  const table = sheet.tables.add(`A5:V${last}`, true, "BeneficiaryBridgeTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  if (!rows.length) return sheet;
  sheet.getRange(`N6:N${last}`).format.numberFormat = countFormat;
  sheet.getRange(`O6:P${last}`).format.numberFormat = "yyyy-mm-dd";
  sheet.getRange(`Q6:R${last}`).format.numberFormat = currencyFormat;
  applyIntegrityFormatting(sheet.getRange(`V6:V${last}`), 6, "V");
  const widths = [14,12,18,38,15,34,15,16,18,9,10,48,24,14,14,14,18,20,42,42,42,15];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`D6:M${last}`).format.wrapText = true;
  return sheet;
}

function buildBeneficiarySummary() {
  const sheet = workbook.worksheets.getItem("Beneficiary Summary");
  const headers = [
    "Cycle", "View", "Company", "Rank", "Candidate / Officeholder", "Candidate ID",
    "Party", "Office", "State", "District", "Source Committees", "Committee Count",
    "Activity Relationships", "Directions", "Downstream Activity",
    "Support Activity", "Oppose Activity",
    associatedFundingHeader, "Democratic Activity", "Republican Activity",
    "Other / Unclassified Activity", "Party Coverage", "FEC Candidate URL", "Integrity Check",
  ];
  // Support/Oppose are written as values straight from the R aggregation, which
  // is the only place the per-row direction still exists. Downstream Activity
  // beside them is an Excel SUMIFS over the bridge, so the two are computed by
  // different paths and the Integrity Check compares them - a disagreement means
  // the split and the total were built from different row sets.
  const rows = (payload.beneficiary_summary ?? []).map((row) => [
    cycleLabelByNumber.get(row.cycle) ?? String(row.cycle), row.view, row.company, null,
    row.candidate_name, row.candidate_id, row.candidate_party, row.office_label,
    row.office_state, row.district, row.source_committees,
    Number(row.source_committee_count || 0), row.activity_relationships, row.directions,
    deferFormulas ? Number(row.preliminary_downstream_amount || 0) : null,
    Number(row.support_amount || 0),
    Number(row.oppose_amount || 0),
    deferFormulas ? Number(row.preliminary_crypto_funding || 0) : null,
    ...Array(4).fill(null), null, deferFormulas ? "PENDING" : null,
  ]);
  titleBlock(
    sheet,
    "Congressional Beneficiary Summary",
    "Industry and company views rank observed House/Senate candidate activity across the saved reachable network. Each spending committee counts once per view; transfers are not added to candidate spending. Coverage is limited to saved sources; this is not attributed company spending.",
    `${rows.length.toLocaleString("en-US")} top-100 candidate rows across cycles/views | Amounts formula-sum the Beneficiary Bridge; party columns count non-opposition activity, including contributions and corrections; opposition is reported separately. Support + Oppose can exceed Downstream Activity: negative correction and refund rows carry neither direction code, so they reduce the unsigned total without reducing either component.`,
    "X",
  );
  const last = 5 + rows.length;
  sheet.getRange(`A5:X${last}`).values = [headers, ...rows];
  const table = sheet.tables.add(`A5:X${last}`, true, "BeneficiarySummaryTable");
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`D6:D${last}`).format.numberFormat = countFormat;
  sheet.getRange(`L6:L${last}`).format.numberFormat = countFormat;
  sheet.getRange(`O6:U${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`V6:V${last}`).format.numberFormat = percentFormat;
  applyIntegrityFormatting(sheet.getRange(`X6:X${last}`), 6, "X");
  const widths = [14,12,18,8,34,15,16,18,9,10,60,14,48,24,18,18,18,20,18,18,24,15,42,15];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`E6:N${last}`).format.wrapText = true;
  return sheet;
}

function groupedRecipientEntries() {
  const groups = new Map();
  for (const row of recipientDetailRows) {
    if (row.include_in_analysis !== "Yes") continue;
    const key = `${row.cycle_label}\r${row.company}\r${row.recipient_key}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const companyEntries = [...groups.values()].map((group) => ({
    cycle: group[0].cycle_label,
    company: group[0].company,
    recipient: mostCommon(group, "recipient"),
    recipientKey: group[0].recipient_key,
    committeeId: mostCommon(group, "committee_id"),
    total: group.reduce((sum, row) => sum + Number(row.amount || 0), 0),
  }));
  const industryGroups = new Map();
  for (const entry of companyEntries) {
    const key = `${entry.cycle}\r${entry.recipientKey}`;
    if (!industryGroups.has(key)) industryGroups.set(key, []);
    industryGroups.get(key).push(entry);
  }
  const industryEntries = [...industryGroups.values()].map((group) => ({
    cycle: group[0].cycle,
    company: "Industry Total",
    recipient: group[0].recipient,
    recipientKey: group[0].recipientKey,
    committeeId: group[0].committeeId,
    total: group.reduce((sum, row) => sum + row.total, 0),
  }));
  return { companyEntries, industryEntries };
}

function buildFocusedView(sheetName, tableName, title, subtitle, rows) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const headers = [
    "Cycle", "Section", "Rank", "Company", "Name / Recipient",
    "Candidate / Officeholder", "Source Committee / Path", "Relationship", "Metric",
    "Total / Activity", "Democratic", "Republican", "Other / Unclassified",
    "Party Coverage", "Reference View", "Reference Company", "Reference Key", "FEC ID",
    "Integrity Check",
  ];
  titleBlock(
    sheet, title, subtitle,
    "All displayed amounts and ranks are Excel formulas over Company List, Employee List, Recipient List, or Beneficiary Summary. Zero values display as '-'.",
    "S",
  );
  const last = 5 + rows.length;
  sheet.getRange(`A5:S${last}`).values = [headers, ...rows];
  const table = sheet.tables.add(`A5:S${last}`, true, tableName);
  styleTable(table);
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`C6:C${last}`).format.numberFormat = countFormat;
  sheet.getRange(`J6:M${last}`).format.numberFormat = currencyFormat;
  sheet.getRange(`N6:N${last}`).format.numberFormat = percentFormat;
  applyIntegrityFormatting(sheet.getRange(`S6:S${last}`), 6, "S");
  const widths = [14,26,8,18,34,34,56,42,34,18,16,16,22,15,14,18,34,16,15];
  widths.forEach((width, index) => {
    const column = excelColumn(index + 1);
    sheet.getRange(`${column}1:${column}${last}`).format.columnWidth = width;
  });
  sheet.getRange(`E6:I${last}`).format.wrapText = true;
  return sheet;
}

function focusedRowsForCycle(cycle) {
  const label = cycle.label;
  const mapByCommittee = new Map((payload.committee_map ?? [])
    .filter((row) => row.cycle === cycle.cycle)
    .map((row) => [row.committee_id, row]));
  const { industryEntries } = groupedRecipientEntries();
  const rows = [];
  const companyTotals = payload.companies.map((company) => ({
    company,
    total: masterDetailRows.filter((row) => row.cycle === cycle.cycle && row.company === company && row.include_in_analysis === "Yes")
      .reduce((sum, row) => sum + Number(row.amount || 0), 0),
  })).sort((a, b) => b.total - a.total || a.company.localeCompare(b.company));
  for (const entry of companyTotals) rows.push([
    label, "Company Total", null, entry.company, entry.company, "", "", "",
    fundingDescription, null, null, null, null, null, "", entry.company, entry.company, "", null,
  ]);
  const employees = (payload.employees ?? []).filter((row) => row.cycle === cycle.cycle)
    .sort((a, b) => Number(b.preliminary_total || 0) - Number(a.preliminary_total || 0) || a.person.localeCompare(b.person)).slice(0, 15);
  for (const entry of employees) rows.push([
    label, "Top Contributor", null, entry.company, entry.person, "", "", entry.identity_basis,
    fundingDescription, null, null, null, null, null, "", entry.company, entry.person_key, "", null,
  ]);
  const recipients = industryEntries.filter((row) => row.cycle === label)
    .sort((a, b) => b.total - a.total || a.recipient.localeCompare(b.recipient)).slice(0, 15);
  for (const entry of recipients) {
    const map = mapByCommittee.get(entry.committeeId) ?? {};
    rows.push([
      label, "Top Direct Recipient", null, "Industry Total", entry.recipient,
      map.direct_person ?? "", "", map.relationship ?? "", fundingDescription,
      null, null, null, null, null, "Industry", "Industry Total", entry.recipientKey,
      entry.committeeId, null,
    ]);
  }
  const beneficiaries = (payload.beneficiary_summary ?? [])
    .filter((row) => row.cycle === cycle.cycle && row.view === "Industry")
    .sort((a, b) => Number(b.preliminary_downstream_amount || 0) - Number(a.preliminary_downstream_amount || 0) || a.candidate_name.localeCompare(b.candidate_name)).slice(0, 15);
  for (const entry of beneficiaries) rows.push([
    label, "Top Downstream Beneficiary", null, "Industry Total", entry.candidate_name,
    entry.candidate_name, entry.source_committees, `${entry.activity_relationships}; ${entry.directions}`,
    "Associated committee activity - not attributed", null, null, null, null, null,
    "Industry", "Industry Total", entry.candidate_id, entry.candidate_id, null,
  ]);
  return rows;
}

function focusedRowsForCompany(company) {
  const rows = [];
  const { companyEntries } = groupedRecipientEntries();
  for (const cycle of payload.cycles) {
    const label = cycle.label;
    const mapByCommittee = new Map((payload.committee_map ?? [])
      .filter((row) => row.cycle === cycle.cycle)
      .map((row) => [row.committee_id, row]));
    rows.push([
      label, "Company Total", null, company, company, "", "", "", fundingDescription,
      null, null, null, null, null, "", company, company, "", null,
    ]);
    const employees = (payload.employees ?? []).filter((row) => row.cycle === cycle.cycle && row.company === company)
      .sort((a, b) => Number(b.preliminary_total || 0) - Number(a.preliminary_total || 0) || a.person.localeCompare(b.person)).slice(0, 10);
    for (const entry of employees) rows.push([
      label, "Top Contributor", null, company, entry.person, "", "", entry.identity_basis,
      fundingDescription, null, null, null, null, null, "", company, entry.person_key, "", null,
    ]);
    const recipients = companyEntries.filter((row) => row.cycle === label && row.company === company)
      .sort((a, b) => b.total - a.total || a.recipient.localeCompare(b.recipient)).slice(0, 10);
    for (const entry of recipients) {
      const map = mapByCommittee.get(entry.committeeId) ?? {};
      rows.push([
        label, "Top Direct Recipient", null, company, entry.recipient, map.direct_person ?? "",
        "", map.relationship ?? "", fundingDescription, null, null, null, null, null,
        "Company", company, entry.recipientKey, entry.committeeId, null,
      ]);
    }
    const beneficiaries = (payload.beneficiary_summary ?? [])
      .filter((row) => row.cycle === cycle.cycle && row.view === "Company" && row.company === company)
      .sort((a, b) => Number(b.preliminary_downstream_amount || 0) - Number(a.preliminary_downstream_amount || 0) || a.candidate_name.localeCompare(b.candidate_name)).slice(0, 10);
    for (const entry of beneficiaries) rows.push([
      label, "Top Downstream Beneficiary", null, company, entry.candidate_name, entry.candidate_name,
      entry.source_committees, `${entry.activity_relationships}; ${entry.directions}`,
      "Associated committee activity - not attributed", null, null, null, null, null,
      "Company", company, entry.candidate_id, entry.candidate_id, null,
    ]);
  }
  return rows;
}

const masterSheet = buildMasterOverview();
const overviewSheet = buildOverview();
const companySheet = buildCompanyList();
const employeeRankFactsSheet = buildEmployeeRankFacts();
const employeeSheet = buildEmployeeList();
const recipientSheet = buildRecipientList();
const beneficiarySummarySheet = buildBeneficiarySummary();
const pacMapSheet = buildPacCandidateMap();
const recipientDetailSheet = buildRecipientDetail();
buildAuditTable("State Destinations", payload.state_destinations ?? [],
  [["cycle","Cycle"],["company","Company"],["party","Recipient party"],["location_view","Location view"],["destination","Destination"],["accounting_basis","Accounting basis"],["net_amount","Net amount"],["receipt_count","Receipt count"]],
  "Two alternative partitions of included receipt money. Do not add location views together. Unknown and ambiguous destinations remain visible; this is not downstream cash tracing.", true);
buildAuditTable("Recipient Locations", payload.recipient_locations ?? [],
  [["cycle","Cycle"],["committee_id","Recipient ID"],["recipient","Recipient"],["recipient_state","Committee state"],["represented_state","Candidate represented state"],["recipient_location_status","Committee location status"],["represented_location_status","Candidate location status"],["recipient_location_evidence","Committee location evidence"],["represented_location_evidence","Candidate link evidence"]],
  "Cycle-specific reported committee addresses and uniquely linked authorized candidates. Presidential candidates are national; unresolved links remain unknown or ambiguous.", true);
const contributorDetailSheet = buildContributorDetail();
const downstreamSheet = buildDownstreamActivity();
const bridgeSheet = buildBeneficiaryBridge();
for (const cycle of payload.cycles) {
  buildFocusedView(
    `${cycle.label} Summary`,
    `CycleSummary_${cycle.cycle}`,
    `${cycle.label} ${industry} Industry Summary`,
    "Industry-wide company totals, top contributors, direct recipient committees with candidate/sponsor resolution, and downstream congressional beneficiaries.",
    focusedRowsForCycle(cycle),
  );
}
for (const company of payload.companies) {
  buildFocusedView(
    companyViewName(company),
    `CompanyView_${safeTableToken(company)}`,
    `${company} Cycle-by-Cycle View`,
    "Each two-year cycle is kept together with the company's top contributors, direct recipient committees, and associated downstream congressional beneficiaries.",
    focusedRowsForCompany(company),
  );
}

buildAuditTable("Accounting Basis", [
  {area:"Headline metric", meaning:"Provisional included receipts at their named recipients. Amendment and intermediary relationships are not fully resolved."},
  {area:"Original giving", meaning:"The intended measure counts original employee and corporate giving once. This receipt view does not yet verify that measure."},
  {area:"Funding source", meaning:"Individuals, organizations, PACs/committees and unknown sources are separate. A corporate report grouping does not prove treasury ownership."},
  {area:"PAC activity", meaning:"Money received by a company PAC and that PAC's outgoing spending are separate stages and must not be added together as original giving."},
  {area:"Partisan percentages", meaning:"D/(D+R) uses classified net receipts. Party Coverage shows how much of the total that denominator covers."},
  {area:"Scope", meaning:"FEC records for the listed companies and cycles. No lobbying. Passing workbook checks does not establish collection completeness or unique dollars."},
], [["area","Area"],["meaning","Meaning"]],
"Read this basis before comparing headline totals. Differences from an external source need a matched scope and a record-level explanation.");
buildAuditTable("Funding Cohorts", payload.funding_cohorts ?? [],
  [["cycle","Cycle"],["company","Company"],["funding_type","Funding Type"],["funding_source_type","Source of Money"],["funding_direction","Direction"],["committee_id","Recipient Committee"],["receipt_role","Receipt Role"],["amount","Included Net Receipts"],["receipt_count","Receipt Count"],["first_date","First Receipt"],["last_date","Last Receipt"]],
  "FEC individual, organization, PAC/committee and unknown entity evidence is shown separately. Organization receipts do not by themselves establish corporate treasury ownership. Into company PAC means funding received by that PAC; outgoing spending remains separate.");
// enrich_downstream.py names each cycle's transfer index status. A cycle that is not
// current publishes no transfer-derived rows, so these three sheets carry its note
// ("Not traced: the transfer index for <cycle> is <status>; rebuild with ...") instead
// of looking like a traced network that found nothing.
const transferIndexNotes = payload.cycles
  .map((cycle) => (payload.metadata.transfer_index_notes ?? {})[String(cycle.cycle)])
  .filter(Boolean);
const withTransferIndexNotes = (text) => transferIndexNotes.length ? `${text} ${transferIndexNotes.join(" ")}` : text;
// F14 (Paul approved option (a), 2026-09-26): each transfer once per cycle. The
// root-by-transfer detail lives only in the tidy staging store (transfer_path_detail).
const transferPathFields = [["cycle","Cycle"],["source","Source Committee"],["destination","Destination Committee"],["amount","Reported Net Transfer"],["record_count","Record Count"],["first_date","First Transfer"],["last_date","Last Transfer"],["status","Traversal Status"],["roots_reaching","Funded Roots Reaching"],["min_depth","Shortest Depth"],["top_roots","Top Funded Roots"],["roots_via_carryover","Roots via Carryover"],["date_rule","Date Rule"],["source_gate","Source Gate"],["source_gate_source","Source Gate From"],["excluded_before_gate_records","Records Before Gate"],["excluded_before_gate_amount","Excluded Before Gate Amount"],["source_carryover_route","Carryover Route"],["carryover_status","Carryover Status"]];
buildAuditTable("Transfer Paths", payload.transfer_paths ?? [], transferPathFields,
  withTransferIndexNotes("Each observed 24G/24K cash transfer once per cycle, with how many funded roots' routes reach it and the largest of them. Amounts are committee activity, not traced company dollars; money that moves through several committees appears at every transfer, so the column does not sum to a total. Counted from the source's gate (the date rule); the full root-by-transfer detail is in the Data workbook's store."), true);
buildAuditTable("Downstream Reach", payload.downstream_reach ?? [],
  [["cycle","Cycle"],["view","View"],["company","Company"],["source_committee_id","Spending Committee"],["representative_root","Representative Funded Root"],["representative_path","Representative Path"]],
  "One spending committee per view, regardless of shared routes or cycles. A route establishes observed association, not a donor dollar allocation. Company and Industry views overlap.", true);
buildAuditTable("Downstream Measurement", payload.downstream_measurement_ledger ?? [],
  [["cycle","Cycle"],["view","View"],["company","Company"],["candidate_id","Candidate ID"],["before_amount","Direct-only Association"],["after_amount","Reachable Committee Activity"],["delta","Difference"],["basis","Comparison Basis"]],
  "Before/after algorithms on identical source rows, before top-100 filtering. These are not prior published workbook totals. Distinct unresolved filing versions remain provisional under count_all.", true);
buildAuditTable("Downstream Sources", payload.downstream_sources ?? [],
  [["cycle","Cycle"],["source","Source"],["path","Source Path"],["sha256","SHA-256"],["status","Coverage"],["policy","Policy"]],
  "Preserved source fingerprints for the downstream measurement ledger. Missing evidence is not zero spending; a saved file does not prove complete filing coverage.");
buildAuditTable("Allocation Estimates", payload.allocation_estimates ?? [],
  [["cycle","Cycle"],["root","Funded Committee"],["candidate_id","Candidate ID"],["candidate_name","Candidate"],["direction","Direction"],["estimated_amount","Conditional Estimate"]],
  withTransferIndexNotes(payload.metadata.allocation_method ?? "No allocation model supplied."));
buildAuditTable("Allocation Coverage", payload.allocation_coverage ?? [],
  [["cycle","Cycle"],["root","Funded Committee"],["input_net_funding","Net Input Funding"],["model_funding","Nonnegative Model Funding"],["estimated_candidate_activity","Estimated Candidate Activity"],["unresolved","Unresolved"],["negative_net_funding_excluded","Negative Net Funding"],["unresolved_reasons","Unresolved Reasons"]],
  withTransferIndexNotes("Each root's model funding equals estimated candidate activity plus unresolved funding. This checks arithmetic, not causal attribution or complete reporting."));
if (transferIndexNotes.length) {
  for (const [name, lastColumn] of [["Transfer Paths", excelColumn(transferPathFields.length)], ["Allocation Estimates", "F"], ["Allocation Coverage", "H"]]) {
    workbook.worksheets.getItem(name).getRange(`A2:${lastColumn}2`).format.rowHeight = 28 + 30 * transferIndexNotes.length;
  }
}
buildAuditTable("Source Freshness", payload.source_freshness ?? [],
  [["cycle","Cycle"],["source","FEC Dataset"],["checked_at","Checked At"],["last_modified","Server Last Modified"],["sha256","SHA-256"],["url","Source URL"]],
  "Snapshot retrieval and server dates are not transaction coverage end dates. See collection receipts separately for the roster search grid.");
buildAuditTable("Classification Review", payload.classification_review ?? [],
  [["cycle","Cycle"],["committee_id","Committee ID"],["committee_name","Committee"],["master_committee_type","Official Type"],["recipient_category","Reporting Category"],["preliminary_crypto_funding","Included Funding"]],
  "Included contributions with unresolved recipient categories. These dollars remain included in totals and are not silently assigned to Super PACs.");
buildAuditTable("Data Quality", payload.quality_checks ?? payload.metadata.export_guards ?? [],
  [["id","Check"],["severity","Severity"],["passed","Passed"],["detail","Detail"]],
  "Review every false result. Collection warnings remain unresolved until fresh searches and coverage checks pass. A completed workbook build does not establish collection completeness.");

void masterSheet;
void overviewSheet;
void companySheet;
void employeeRankFactsSheet;
void employeeSheet;
void recipientSheet;
void beneficiarySummarySheet;
void pacMapSheet;
void recipientDetailSheet;
void contributorDetailSheet;
void downstreamSheet;
void bridgeSheet;

const previewRanges = new Map([
  // Each cycle block gained an Average row, so the third block now ends at 48.
  // The last column is the Integrity Check: 17 summary columns, five per category
  // and per contributor role, then the check, so it follows masterCategories.
  ["Master Overview", `A1:${excelColumn(18 + (masterCategories.length + masterTiers.length) * 5)}48`],
  [overviewSheetName, "A1:J52"],
  ["Company List", "A1:O36"],
  ...(aggregateFacts ? [["Employee Rank Facts", "A1:D25"]] : []),
  ["Employee List", "A1:S30"],
  ["Recipient List", "A1:AD30"],
  ["Beneficiary Summary", "A1:X30"],
  ["PAC & Candidate Map", "A1:S30"],
  ["Recipient Detail", "A1:S30"],
  ["Contributor Detail", "A1:M30"],
  ["Downstream Activity", "A1:AC25"],
  ["Beneficiary Bridge", "A1:V25"],
  ...payload.cycles.map((cycle) => [`${cycle.label} Summary`, "A1:S35"]),
  ...payload.companies.map((company) => [companyViewName(company), "A1:S35"]),
  ["2021-2022", "A1:AI20"],
  ["2023-2024", "A1:AI20"],
  ["2025-2026", "A1:AI20"],
  ["Cycle Exceptions", "A1:AI20"],
]);
const outputPath = path.join(outputDir, `${reportBaseName}.xlsx`);
// artifact-tool's XLSX exporter currently performs an unbounded whole-workbook
// inspect before serialization. Large but valid report workbooks can exceed
// V8's maximum string length while that diagnostic NDJSON is joined, before
// the XLSX serializer even runs. The report pipeline performs its own bounded
// structure/formula/render audits after export, so suppress only this embedded
// diagnostic and leave the actual workbook serialization unchanged.
const originalInspect = workbook.inspect;
workbook.inspect = async () => ({
  ndjson: "",
  truncated: false,
  metadata: { notices: ["Embedded export inspection deferred to report QA."] },
});
try {
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
} finally {
  workbook.inspect = originalInspect;
}
if (process.env.MENTAT_RENDER_PREVIEWS === "1") {
  for (const [sheetName, range] of previewRanges) {
    const preview = await workbook.render({
      sheetName,
      range,
      scale: 0.75,
      format: "png",
    });
    const fileName = `${sheetName.replaceAll(/[^A-Za-z0-9]+/g, "_")}.png`;
    await fs.writeFile(
      path.join(diagnosticsDir, fileName),
      new Uint8Array(await preview.arrayBuffer()),
    );
  }
}
console.log(`OUTPUT=${outputPath}`);
