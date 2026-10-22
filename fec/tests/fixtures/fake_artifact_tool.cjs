"use strict";

/*
 * Test double for @oai/artifact-tool.  It serves a synthetic, internally
 * consistent legislative workbook (names, Bioguide ids, all-numeric blocks,
 * Sub Layer formulas that reference every category block) for whatever range a
 * helper asks about, so the artifact helpers run end to end without the real
 * engine.  Every inspect/render/write call is appended to
 * $FAKE_ARTIFACT_TOOL_LOG, one JSON object per line, so a test can assert which
 * ranges a helper requested.  Tests install this file as
 * node_modules/@oai/artifact-tool/index.js next to a copy of the helper.
 */

const fs = require("node:fs");

const SHEETS = [
  "Legislative Score - Raw",
  "Legislative Score - Normalized",
  "Sub Layer - Raw",
  "Sub Layer - Normalized",
  "Blended Model",
  "Congestion",
];
// Name column and first member row of each sheet that lists members.
const NAMES = {
  "Legislative Score - Raw": { col: 1, first: 3 },
  "Sub Layer - Raw": { col: 2, first: 4 },
  Congestion: { col: 1, first: 3 },
  "Blended Model": { col: 1, first: 3 },
};
const NORMALIZED_STARTS = [
  "C", "K", "S", "AA", "AI", "AQ", "AY", "BG", "BO",
  "BW", "CE", "CM", "CU", "DC", "DK", "DS", "EA", "EW",
];

const colNumber = (label) =>
  [...label].reduce((v, c) => v * 26 + c.charCodeAt(0) - 64, 0);
const colName = (n) => {
  let s = "";
  for (; n > 0; n = Math.floor((n - 1) / 26)) {
    s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  }
  return s;
};
const log = (entry) => {
  if (process.env.FAKE_ARTIFACT_TOOL_LOG) {
    fs.appendFileSync(
      process.env.FAKE_ARTIFACT_TOOL_LOG,
      JSON.stringify(entry) + "\n",
    );
  }
};

// Every category's data and weight ranges, so any Sub Layer formula audit finds the ones it wants.
const sublayerFormula = (row) =>
  "=" +
  NORMALIZED_STARTS.map((start) => {
    const end = colName(colNumber(start) + 4);
    return (
      `SUMPRODUCT('Legislative Score - Normalized'!$${start}$${row - 1}:$${end}$${row - 1},` +
      `'Legislative Score - Normalized'!$${start}$2:$${end}$2)`
    );
  }).join("+");

function table(sheetId, range) {
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(range);
  if (!m) throw new Error(`fake artifact-tool: invalid range ${range}`);
  const [c1, r1, c2, r2] = [colNumber(m[1]), Number(m[2]), colNumber(m[3]), Number(m[4])];
  const names = NAMES[sheetId];
  const values = [];
  const formulas = [];
  for (let r = r1; r <= r2; r += 1) {
    const rowValues = [];
    const rowFormulas = [];
    for (let c = c1; c <= c2; c += 1) {
      const member = names && r >= names.first ? r - names.first + 1 : 0;
      let value = 1;
      if (member && c === names.col) value = `Member ${member}`;
      if (member && sheetId === "Congestion" && c === 2) {
        value = `A${String(member).padStart(6, "0")}`;
      }
      rowValues.push(value);
      rowFormulas.push(
        sheetId === "Sub Layer - Raw" && c1 === 2 && r >= 4 ? sublayerFormula(r) : "",
      );
    }
    values.push(rowValues);
    formulas.push(rowFormulas);
  }
  return { kind: "table", sheetId, range, values, formulas };
}

const workbook = {
  worksheets: {
    getItem: (name) => ({
      getRange: (range) => {
        const target = {};
        return new Proxy(target, {
          set(object, key, value) {
            log({ op: "write", sheet: name, range, key });
            object[key] = value;
            return true;
          },
        });
      },
    }),
  },
  async inspect(options) {
    log({
      op: "inspect",
      kind: options.kind,
      sheetId: options.sheetId,
      range: options.range,
      tableMaxRows: options.tableMaxRows,
      tableMaxCols: options.tableMaxCols,
    });
    if (options.kind === "sheet") {
      return {
        ndjson: SHEETS.map((name) => JSON.stringify({ kind: "sheet", name })).join("\n"),
      };
    }
    if (options.kind === "table") {
      return { ndjson: JSON.stringify(table(options.sheetId, options.range)) };
    }
    return { ndjson: "" };
  },
  async render(options) {
    log({ op: "render", sheet: options.sheetName, range: options.range });
    return { arrayBuffer: async () => new Uint8Array([137, 80, 78, 71]).buffer };
  },
};

module.exports = {
  FileBlob: { load: async (file) => ({ file }) },
  SpreadsheetFile: {
    importXlsx: async () => workbook,
    exportXlsx: async () => ({
      save: async (file) => fs.writeFileSync(file, "fake-xlsx"),
    }),
  },
};
