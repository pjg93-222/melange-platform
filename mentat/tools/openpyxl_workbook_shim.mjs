// The part of @oai/artifact-tool's spreadsheet API that crypto_workbook_builder.mjs uses, answered without it
// (MENTAT_XLSX_WRITER=openpyxl, D-178). The builder decides what every cell holds; this module only records what it
// asks for, and xlsx_spec_writer.py replays the record onto an openpyxl workbook at save time.
//
// Anything the builder asks for that is not listed here throws, so a new call in the builder cannot be silently
// dropped from the workbook.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const WRITER = path.join(path.dirname(fileURLToPath(import.meta.url)), "xlsx_spec_writer.py");
const ROWS_PER_LINE = 1000;

function column(letters) {
  let n = 0;
  for (const ch of letters) n = n * 26 + ch.charCodeAt(0) - 64;
  return n;
}

function bounds(ref) {
  const match = /^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$/.exec(String(ref));
  if (!match) throw new Error(`Unsupported range reference: ${ref}`);
  const [, c1, r1, c2 = c1, r2 = r1] = match;
  return { c1: column(c1), r1: Number(r1), c2: column(c2), r2: Number(r2) };
}

const two = (n) => String(n).padStart(2, "0");

// artifact-tool stores a Date at local midnight as that calendar day (a date, not an instant) and any other Date as
// its UTC fields; this spells both the way its exporter does so the cell holds the same date.
function dateCell(date) {
  if (Number.isNaN(date.getTime())) return null;
  if (date.getHours() === 0 && date.getMinutes() === 0 && date.getSeconds() === 0 && date.getMilliseconds() === 0) {
    return `${String(date.getFullYear()).padStart(4, "0")}-${two(date.getMonth() + 1)}-${two(date.getDate())}T00:00:00.000Z`;
  }
  return date.toISOString();
}

function cell(value) {
  if (value === undefined || value === null) return null;
  if (value instanceof Date) return dateCell(value);
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  return String(value);
}

function grid(rows) {
  if (!Array.isArray(rows) || rows.some((row) => !Array.isArray(row))) {
    throw new Error("A range assignment needs an array of row arrays");
  }
  return rows.map((row) => row.map(cell));
}

class Format {
  constructor(range) {
    this.range = range;
  }

  #cells(props) {
    this.range.sheet.ops.push({ op: "fmt", ref: this.range.ref, p: props });
  }

  set fill(color) { this.#cells({ fill: color }); }
  set font(spec) { this.#cells({ font: spec }); }
  set numberFormat(code) { this.#cells({ numberFormat: code }); }
  set wrapText(on) { this.#cells({ wrap: on }); }
  set verticalAlignment(value) { this.#cells({ v: value }); }
  set horizontalAlignment(value) { this.#cells({ h: value }); }
  set rowHeight(height) {
    const { r1, r2 } = bounds(this.range.ref);
    this.range.sheet.ops.push({ op: "rowHeight", r1, r2, h: height });
  }
  set columnWidth(width) {
    const { c1, c2 } = bounds(this.range.ref);
    this.range.sheet.ops.push({ op: "colWidth", c1, c2, w: width });
  }
}

const FORMAT_KEYS = new Set(["fill", "font", "numberFormat", "wrapText", "verticalAlignment", "horizontalAlignment", "rowHeight", "columnWidth"]);

class ConditionalFormats {
  constructor(range) {
    this.range = range;
  }

  #look(format = {}) {
    return { fill: format.fill ?? null, font: format.font ?? null };
  }

  addCustom(formula, format) {
    this.range.sheet.ops.push({ op: "cf", ref: this.range.ref, type: "expression", formula, ...this.#look(format) });
  }

  add(type, options) {
    if (type !== "beginsWith") throw new Error(`Unsupported conditional format: ${type}`);
    this.range.sheet.ops.push({ op: "cf", ref: this.range.ref, type, text: options.text, ...this.#look(options.format) });
  }
}

class Range {
  constructor(sheet, ref) {
    this.sheet = sheet;
    this.ref = ref;
  }

  #write(op, rows) {
    const { c1, r1 } = bounds(this.ref);
    const data = grid(rows);
    for (let at = 0; at < data.length; at += ROWS_PER_LINE) {
      this.sheet.ops.push({ op, r: r1 + at, c: c1, rows: data.slice(at, at + ROWS_PER_LINE) });
    }
  }

  set values(rows) { this.#write("values", rows); }
  set formulas(rows) { this.#write("formulas", rows); }

  get format() { return new Format(this); }
  set format(spec) {
    const format = new Format(this);
    for (const [key, value] of Object.entries(spec)) {
      if (!FORMAT_KEYS.has(key)) throw new Error(`Unsupported format property: ${key}`);
      format[key] = value;
    }
  }

  get conditionalFormats() { return new ConditionalFormats(this); }
  merge() { this.sheet.ops.push({ op: "merge", ref: this.ref }); }
  fillDown() { this.sheet.ops.push({ op: "fillDown", ref: this.ref }); }
}

class Table {
  constructor(ref, headers, name) {
    this.ref = ref;
    this.headers = headers;
    this.name = name;
    this.style = "TableStyleMedium2";
    this.showHeaders = true;
    this.showFilterButton = true;
  }
}

class Worksheet {
  constructor(name) {
    this.name = name;
    this.ops = [];
    this.tableList = [];
    this.freeze = { rows: 0, cols: 0 };
    this.grid = true;
    this.freezePanes = {
      freezeRows: (rows) => { this.freeze.rows = rows; },
      freezeColumns: (cols) => { this.freeze.cols = cols; },
    };
    this.tables = {
      add: (ref, headers, tableName) => {
        const table = new Table(ref, headers, tableName);
        this.tableList.push(table);
        return table;
      },
    };
  }

  set showGridLines(on) { this.grid = Boolean(on); }
  get showGridLines() { return this.grid; }
  getRange(ref) { return new Range(this, ref); }
}

export class Workbook {
  static create() {
    return new Workbook();
  }

  constructor() {
    this.sheets = [];
    this.worksheets = {
      add: (name) => {
        if (this.sheets.some((sheet) => sheet.name === name)) throw new Error(`Duplicate worksheet name: ${name}`);
        const sheet = new Worksheet(name);
        this.sheets.push(sheet);
        return sheet;
      },
      getItem: (name) => {
        const sheet = this.sheets.find((item) => item.name === name);
        if (!sheet) throw new Error(`Unknown worksheet: ${name}`);
        return sheet;
      },
    };
  }

  // The builder replaces this to keep artifact-tool's export from inspecting the whole workbook; here it is a no-op.
  async inspect() {
    return { ndjson: "", truncated: false };
  }
}

function pythonCommand() {
  return process.env.MENTAT_PYTHON?.trim() || (process.platform === "win32" ? "python" : "python3");
}

function writeSpec(workbook, specPath) {
  const fd = fs.openSync(specPath, "w");
  try {
    const emit = (record) => fs.writeSync(fd, JSON.stringify(record) + "\n");
    emit({ op: "book", sheets: workbook.sheets.map((sheet) => sheet.name) });
    workbook.sheets.forEach((sheet, s) => {
      emit({ s, op: "grid", show: sheet.grid });
      for (const record of sheet.ops) emit({ s, ...record });
      for (const t of sheet.tableList) {
        emit({ s, op: "table", ref: t.ref, name: t.name, style: t.style, headers: t.showHeaders, filter: t.showFilterButton });
      }
      emit({ s, op: "freeze", rows: sheet.freeze.rows, cols: sheet.freeze.cols });
    });
  } finally {
    fs.closeSync(fd);
  }
}

export const SpreadsheetFile = {
  async exportXlsx(workbook) {
    return {
      async save(outputPath) {
        const specPath = `${outputPath}.spec.ndjson`;
        writeSpec(workbook, specPath);
        const run = spawnSync(pythonCommand(), [WRITER, specPath, outputPath], { stdio: ["ignore", "inherit", "inherit"] });
        if (run.error || run.status !== 0) {
          throw new Error(`openpyxl workbook writer failed (${run.error?.message ?? `exit ${run.status}`}); spec kept at ${specPath}`);
        }
        fs.rmSync(specPath, { force: true });
      },
    };
  },
};
