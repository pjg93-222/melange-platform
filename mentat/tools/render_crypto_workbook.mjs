import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

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
    os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime",
    "dependencies", "node", "node_modules",
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
const { FileBlob, SpreadsheetFile } = await import(pathToFileURL(artifactToolEntry).href);

const [workbookPath, jsonPath, outputDirectory] = process.argv.slice(2);
if (!workbookPath || !jsonPath || !outputDirectory) {
  throw new Error("usage: render_crypto_workbook.mjs <workbook.xlsx> <industry-data.json> <output-dir>");
}

const payload = JSON.parse(await fs.readFile(jsonPath, "utf8"));
const industry = String(payload.metadata?.industry ?? "Crypto").trim() || "Crypto";
const overviewSheetName = industry === "Crypto" ? "Crypto Spending Overview" : "Spending Overview";
const companyViewName = (company) => `${company} View`.slice(0, 31);
const previews = process.env.MENTAT_RENDER_MASTER_ONLY === "1" ? [
  ["Master Overview", "A1:Q48", "Master_Overview_Summary"],
  // Eleven recipient channels (R:BT, five columns each; Conduit follows Carey),
  // then the four contributor roles and the Integrity Check (BU:CO).
  ["Master Overview", "R1:BT18", "Master_Overview_Recipient_Channels"],
  ["Master Overview", "BU1:CO18", "Master_Overview_Contributor_Roles"],
] : [
  ["Master Overview", "A1:Q48"],
  [overviewSheetName, "A1:J52"],
  ...payload.cycles.map((cycle) => [`${cycle.label} Summary`, "A1:S35"]),
  ["Company List", "A1:O36"],
  ...payload.companies.map((company) => [companyViewName(company), "A1:S35"]),
  ["Employee List", "A1:S30"],
  ["Recipient List", "A1:AD30"],
  ["Beneficiary Summary", "A1:V30"],
  ["PAC & Candidate Map", "A1:S30"],
  ["Downstream Activity", "A1:AC25"],
  ["Beneficiary Bridge", "A1:V25"],
  ...payload.cycles.map((cycle) => [cycle.label, "A1:AI20"]),
  ["Cycle Exceptions", "A1:AI20"],
];

await fs.mkdir(outputDirectory, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const rendered = [];
for (const [sheetName, range, fileToken] of previews) {
  const preview = await workbook.render({ sheetName, range, scale: 0.65, format: "png" });
  const fileName = `${(fileToken ?? sheetName).replaceAll(/[^A-Za-z0-9]+/g, "_")}.png`;
  const target = path.join(outputDirectory, fileName);
  await fs.writeFile(target, new Uint8Array(await preview.arrayBuffer()));
  rendered.push({ sheet: sheetName, range, file: target });
}
await fs.writeFile(
  path.join(outputDirectory, "render_manifest.json"),
  JSON.stringify({ workbook: workbookPath, sheet_count: rendered.length, rendered }, null, 2),
  "utf8",
);
console.log(`RENDERED_SHEETS=${rendered.length}`);
