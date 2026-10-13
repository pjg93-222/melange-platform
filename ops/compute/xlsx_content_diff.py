"""Canonical content comparison of two .xlsx files (D-177: workbook bytes are never reproducible, content is).

Package metadata, timestamps, shared-string order, style-table order, the cell XML flavour (shared vs inline
strings, shared vs expanded formulas) and cached application properties are all ignored. What is compared:

  content  (gates the exit code)  sheet names, order and visibility; per cell coordinate -> kind and value or
                                  formula text (normalised); defined names; table definitions; merged ranges;
                                  conditional-format rules; data validations; hyperlinks; freeze panes.
  style    (reported; --gate-styles)  per-cell number format / font / fill / border / alignment, column widths,
                                  row heights, grid lines, tab colour, active tab.
  cached   (reported; --gate-cached)  the cached result stored beside each formula.

A blank cell and an empty-string cell are the same content. 1 and 1.0 are the same number. The report holds
counts and coordinates only; values appear nowhere unless --show-values is passed.

    python -m ops.compute.xlsx_content_diff A.xlsx B.xlsx [--examples 5] [--json out.json] [--gate-styles]

Exit status 0 identical on the gated groups, 1 different, 2 unreadable input.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from openpyxl.formula.translate import Translator
from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.utils import column_index_from_string, get_column_letter

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
Q, QR = "{%s}" % NS, "{%s}" % REL
COORD = re.compile(r"([A-Z]+)(\d+)")

CONTENT_KINDS = ("sheet_missing", "sheet_order", "sheet_state", "cell_only_in_a", "cell_only_in_b", "cell_kind",
                 "cell_value", "cell_formula", "defined_name", "table", "merge", "conditional_format",
                 "data_validation", "hyperlink", "freeze_pane")
STYLE_KINDS = ("cell_style", "column_width", "row_height", "grid_lines", "tab_color", "active_tab")
CACHED_KINDS = ("cached_value",)
_DEFAULT_STYLE = ("", (), (), (), ())


# ------------------------------------------------------------------ canonical forms

def num(text: str) -> str:
    """One spelling per number: integral values print as integers, the rest as the shortest text that round-trips,
    so two numbers are the same only when they are the same double (a writer that prints 16 digits is not)."""
    value = float(text)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def norm_formula(text: str) -> str:
    """Formula text without the leading '=', with insignificant whitespace dropped and case folded.

    Text inside double quotes, single-quoted sheet names and [structured reference] brackets is kept verbatim:
    a column called 'Total Giving' is not the same column as 'TotalGiving'.
    """
    out, quote, depth = [], "", 0
    for ch in text.lstrip("=").strip():
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "[":
            depth += 1
            out.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            out.append(ch)
        elif depth:
            out.append(ch)
        elif not ch.isspace():
            out.append(ch.upper())
    return "".join(out)


def _text(node) -> str:
    """Plain text of a shared string or inline string, phonetic runs excluded."""
    if node is None:
        return ""
    phonetic = {id(t) for ph in node.iter(Q + "rPh") for t in ph.iter(Q + "t")}
    return "".join(t.text or "" for t in node.iter(Q + "t") if id(t) not in phonetic)


def _color(node) -> str:
    if node is None:
        return ""
    if node.get("rgb"):
        return node.get("rgb")[-6:].upper()
    for attr in ("theme", "indexed", "auto"):
        if node.get(attr) is not None:
            return "%s:%s" % (attr, node.get(attr))
    return ""


def _text_color(node) -> str:
    """Font colour; theme 1 is the default text colour, which is also what no colour at all means."""
    color = _color(node)
    return "" if color == "theme:1" else color


FONT_ALIASES = {"Carlito": "Calibri"}      # artifact-tool's default font is the metric-compatible stand-in for Calibri
DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z?$")


def norm_date(text: str) -> str:
    """An ISO-8601 cell date with the zone letter and a zero fraction dropped (every writer here emits UTC)."""
    match = DATE.match(text)
    if not match:
        return text
    fraction = (match.group(2) or "").rstrip("0")
    return match.group(1) + ("" if fraction == "." else fraction)


def norm_sqref(text: str) -> str:
    """A cell range reference list with one spelling: 'O6:O6' is 'O6'."""
    parts = []
    for part in (text or "").split():
        first, _, last = part.partition(":")
        parts.append(first if first == last or not last else part)
    return " ".join(parts)


def _flag(node) -> bool:
    return node is not None and (node.get("val") or "1").lower() not in ("0", "false")


# ------------------------------------------------------------------ package readers

def _rels(zf: ZipFile, part: str) -> dict:
    folder, name = posixpath.split(part)
    path = posixpath.join(folder, "_rels", name + ".rels")
    if path not in zf.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(zf.read(path)):
        target = rel.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id")] = (rel.get("Type", ""), target)
    return out


def _styles(zf: ZipFile) -> dict:
    """cellXfs index -> hashable style tuple; also the dxf list conditional formats point into."""
    if "xl/styles.xml" not in zf.namelist():
        return {"xf": [], "dxf": [], "default": _DEFAULT_STYLE}
    root = ET.fromstring(zf.read("xl/styles.xml"))
    formats = dict(BUILTIN_FORMATS)
    for fmt in root.iterfind(".//" + Q + "numFmts/" + Q + "numFmt"):
        formats[int(fmt.get("numFmtId"))] = fmt.get("formatCode")

    def font(node):
        if node is None:
            return ()
        name, size = node.find(Q + "name"), node.find(Q + "sz")
        family = name.get("val") if name is not None else ""
        return (FONT_ALIASES.get(family, family), num(size.get("val")) if size is not None else "",
                _flag(node.find(Q + "b")), _flag(node.find(Q + "i")), node.find(Q + "u") is not None,
                _text_color(node.find(Q + "color")))

    def fill(node):
        pattern = node.find(Q + "patternFill") if node is not None else None
        if pattern is None or pattern.get("patternType") in (None, "none"):
            return ()
        return (pattern.get("patternType"), _color(pattern.find(Q + "fgColor")))

    def border(node):
        if node is None:
            return ()
        out = []
        for side in ("left", "right", "top", "bottom"):
            edge = node.find(Q + side)
            out.append((edge.get("style"), _color(edge.find(Q + "color"))) if edge is not None and edge.get("style") else ())
        return tuple(out) if any(out) else ()

    fonts = [font(n) for n in root.iterfind(Q + "fonts/" + Q + "font")]
    fills = [fill(n) for n in root.iterfind(Q + "fills/" + Q + "fill")]
    borders = [border(n) for n in root.iterfind(Q + "borders/" + Q + "border")]
    xfs = []
    for xf in root.iterfind(Q + "cellXfs/" + Q + "xf"):
        align = xf.find(Q + "alignment")
        a = (align.get("horizontal") or "", align.get("vertical") or "", align.get("wrapText") in ("1", "true"),
             align.get("indent") or "0") if align is not None else ()
        a = () if a == ("", "", False, "0") else a
        pick = lambda seq, key: seq[int(xf.get(key, 0))] if int(xf.get(key, 0)) < len(seq) else ()
        code = formats.get(int(xf.get("numFmtId", 0)), "General")
        xfs.append((code if code != "General" else "", pick(fonts, "fontId"), pick(fills, "fillId"),
                    pick(borders, "borderId"), a))
    dxfs = []
    for dxf in root.iterfind(Q + "dxfs/" + Q + "dxf"):
        pattern = dxf.find(Q + "fill/" + Q + "patternFill")
        shade = (_color(pattern.find(Q + "bgColor")) or _color(pattern.find(Q + "fgColor"))) if pattern is not None else ""
        number = dxf.find(Q + "numFmt")
        dxfs.append((font(dxf.find(Q + "font")), shade, number.get("formatCode") if number is not None else ""))
    return {"xf": xfs, "dxf": dxfs, "default": xfs[0] if xfs else _DEFAULT_STYLE}


def _events(zf: ZipFile, part: str):
    """iterparse over one package part, with the member handle closed when the stream is exhausted."""
    with zf.open(part) as handle:
        yield from ET.iterparse(handle)


def _shared_strings(zf: ZipFile) -> list:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    out = []
    for _, node in _events(zf, "xl/sharedStrings.xml"):
        if node.tag == Q + "si":
            out.append(_text(node))
            node.clear()
    return out


def _cells(zf: ZipFile, part: str, strings: list, want_styles: bool):
    """Stream sheetData: content cells, cached results, per-cell style ids, custom row heights."""
    cells, cached, style_ids, heights, shared = {}, {}, {}, {}, {}
    for _, el in _events(zf, part):
        tag = el.tag
        if tag == Q + "row":
            if el.get("ht") and el.get("customHeight") in ("1", "true"):
                heights[int(el.get("r"))] = num(el.get("ht"))
            el.clear()
            continue
        if tag != Q + "c":
            continue
        coord, kind = el.get("r"), el.get("t")
        formula, v, inline = el.find(Q + "f"), el.find(Q + "v"), el.find(Q + "is")
        raw = v.text if v is not None and v.text is not None else None
        if kind == "inlineStr":
            value = ("s", _text(inline))
        elif kind == "s":
            value = ("s", strings[int(raw)]) if raw is not None else None
        elif kind == "str":
            value = ("s", raw or "")
        elif kind == "b":
            value = ("b", "1" if raw in ("1", "true") else "0")
        elif kind == "e":
            value = ("e", raw or "")
        elif kind == "d":
            value = ("d", norm_date(raw or ""))
        else:
            value = ("n", num(raw)) if raw not in (None, "") else None
        if value is not None and value[0] == "s" and value[1] == "":
            value = None
        if formula is not None:
            text = formula.text or ""
            if formula.get("t") == "shared":
                si = formula.get("si")
                if text:
                    shared[si] = (coord, text)
                elif si in shared:
                    origin, master = shared[si]
                    text = Translator("=" + master, origin=origin).translate_formula(coord)[1:]
            cells[coord] = ("f", norm_formula(text))
            if value is not None:
                cached[coord] = value
        elif value is not None:
            cells[coord] = value
        if want_styles and el.get("s") not in (None, "0"):
            style_ids[coord] = int(el.get("s"))
        el.clear()
    return cells, cached, style_ids, heights


def _table(zf: ZipFile, part: str) -> tuple:
    root = ET.fromstring(zf.read(part))
    columns = tuple((c.get("name"), norm_formula(c.findtext(Q + "calculatedColumnFormula") or ""))
                    for c in root.iterfind(Q + "tableColumns/" + Q + "tableColumn"))
    style = root.find(Q + "tableStyleInfo")
    return (root.get("name"), root.get("displayName"), root.get("ref"), root.get("headerRowCount") or "1",
            root.get("totalsRowCount") or "0", columns, style.get("name") if style is not None else "")


def _structure(zf: ZipFile, part: str, styles: dict) -> dict:
    """Everything outside sheetData: freeze, merges, widths, conditional formats, validations, hyperlinks, tables."""
    out = {"merges": set(), "cols": {}, "cf": [], "dv": [], "links": set(), "freeze": None, "grid": True,
           "tab": "", "tables": []}
    rels = _rels(zf, part)
    for _, el in _events(zf, part):
        tag = el.tag
        if tag == Q + "sheetData":
            el.clear()
        elif tag == Q + "pane":
            out["freeze"] = (el.get("xSplit") or "0", el.get("ySplit") or "0", el.get("topLeftCell") or "",
                             el.get("state") or "")
        elif tag == Q + "sheetView":
            out["grid"] = el.get("showGridLines") not in ("0", "false")
        elif tag == Q + "tabColor":
            out["tab"] = _color(el)
        elif tag == Q + "mergeCell":
            out["merges"].add(norm_sqref(el.get("ref")))
        elif tag == Q + "col":
            width = num(el.get("width")) if el.get("width") and el.get("customWidth") in ("1", "true") else ""
            for index in range(int(el.get("min")), min(int(el.get("max")), int(el.get("min")) + 16384) + 1):
                out["cols"][index] = (width, el.get("hidden") in ("1", "true"))
        elif tag == Q + "conditionalFormatting":
            for rule in el.iterfind(Q + "cfRule"):
                dxf = int(rule.get("dxfId")) if rule.get("dxfId") is not None else None
                out["cf"].append((norm_sqref(el.get("sqref")), rule.get("type") or "", rule.get("operator") or "", rule.get("text") or "",
                                  tuple(norm_formula(f.text or "") for f in rule.iterfind(Q + "formula")),
                                  styles["dxf"][dxf] if dxf is not None and dxf < len(styles["dxf"]) else ()))
        elif tag == Q + "dataValidation":
            out["dv"].append((norm_sqref(el.get("sqref")), el.get("type") or "", el.get("operator") or "", el.get("allowBlank") or "",
                              tuple(norm_formula(f.text or "") for f in (el.find(Q + "formula1"), el.find(Q + "formula2"))
                                    if f is not None)))
        elif tag == Q + "hyperlink":
            out["links"].add((el.get("ref"), el.get("location") or "", rels.get(el.get(QR + "id"), ("", ""))[1]))
        elif tag == Q + "tablePart":
            target = rels.get(el.get(QR + "id"), ("", ""))[1]
            if target:
                out["tables"].append(_table(zf, target))
    out["cf"].sort(key=repr)
    out["dv"].sort(key=repr)
    return out


def read_book(path: str, styles: bool = True) -> dict:
    """The canonical model of one workbook: sheets in order, defined names, active tab."""
    with ZipFile(path) as zf:
        book = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = _rels(zf, "xl/workbook.xml")
        strings, table = _shared_strings(zf), _styles(zf)
        sheets = []
        for entry in book.find(Q + "sheets"):
            part = rels[entry.get(QR + "id")][1]
            cells, cached, style_ids, heights = _cells(zf, part, strings, styles)
            structure = _structure(zf, part, table)
            sheets.append({"name": entry.get("name"), "state": entry.get("state") or "visible", "cells": cells,
                           "cached": cached, "heights": heights, "default_style": table["default"], **structure,
                           "styles": {c: table["xf"][i] for c, i in style_ids.items() if i < len(table["xf"])}})
        names = set()
        for entry in book.iterfind(Q + "definedNames/" + Q + "definedName"):
            local = entry.get("localSheetId")
            scope = sheets[int(local)]["name"] if local is not None and int(local) < len(sheets) else ""
            names.add((entry.get("name"), scope, entry.get("hidden") in ("1", "true"), norm_formula(entry.text or "")))
        views = book.find(Q + "bookViews")
        active = (views[0].get("activeTab") or "0") if views is not None and len(views) else "0"
    return {"sheets": sheets, "names": names, "active_tab": active}


# ------------------------------------------------------------------ comparison

class Report:
    """Counts per kind (overall and per sheet) plus a bounded list of examples; values only when asked."""

    def __init__(self, examples: int, show_values: bool):
        self.counts = Counter()
        self.per_sheet = defaultdict(Counter)
        self.examples = defaultdict(list)
        self.limit, self.show_values = examples, show_values

    def add(self, kind: str, sheet: str = "", where: str = "", a=None, b=None):
        self.counts[kind] += 1
        if sheet:
            self.per_sheet[sheet][kind] += 1
        if len(self.examples[kind]) < self.limit:
            item = "%s!%s" % (sheet, where) if sheet and where else (sheet or where)
            if self.show_values:
                item += "  A=%r B=%r" % (a, b)
            self.examples[kind].append(item)


def _diff_set(report: Report, kind: str, sheet: str, a: set, b: set):
    for item in sorted(a ^ b, key=repr):
        report.add(kind, sheet, "only in A" if item in a else "only in B", item if item in a else None, item if item in b else None)


def _coord_key(coord: str):
    column, row = COORD.fullmatch(coord).groups()
    return int(row), column_index_from_string(column)


def compare_books(a: dict, b: dict, examples: int = 5, show_values: bool = False, styles: bool = True) -> dict:
    report = Report(examples, show_values)
    names_a, names_b = [s["name"] for s in a["sheets"]], [s["name"] for s in b["sheets"]]
    for name in sorted(set(names_a) ^ set(names_b)):
        report.add("sheet_missing", name, "only in A" if name in names_a else "only in B")
    common = [n for n in names_a if n in names_b]
    if common != [n for n in names_b if n in names_a]:
        report.add("sheet_order", where="common sheets are in a different order")
    _diff_set(report, "defined_name", "", a["names"], b["names"])
    if styles and a["active_tab"] != b["active_tab"]:
        report.add("active_tab", where="%s/%s" % (a["active_tab"], b["active_tab"]))
    by_a, by_b = {s["name"]: s for s in a["sheets"]}, {s["name"]: s for s in b["sheets"]}
    cells_compared = 0
    for name in common:
        sa, sb = by_a[name], by_b[name]
        if sa["state"] != sb["state"]:
            report.add("sheet_state", name, "%s/%s" % (sa["state"], sb["state"]))
        ca, cb = sa["cells"], sb["cells"]
        cells_compared += len(ca.keys() | cb.keys())
        for coord in sorted(ca.keys() - cb.keys(), key=_coord_key):
            report.add("cell_only_in_a", name, coord, ca[coord])
        for coord in sorted(cb.keys() - ca.keys(), key=_coord_key):
            report.add("cell_only_in_b", name, coord, None, cb[coord])
        for coord in sorted(ca.keys() & cb.keys(), key=_coord_key):
            x, y = ca[coord], cb[coord]
            if x != y:
                report.add("cell_formula" if x[0] == y[0] == "f" else "cell_value" if x[0] == y[0] else "cell_kind",
                           name, coord, x, y)
        for coord in sorted(sa["cached"].keys() | sb["cached"].keys(), key=_coord_key):
            if sa["cached"].get(coord) != sb["cached"].get(coord):
                report.add("cached_value", name, coord, sa["cached"].get(coord), sb["cached"].get(coord))
        _diff_set(report, "table", name, set(sa["tables"]), set(sb["tables"]))
        _diff_set(report, "merge", name, sa["merges"], sb["merges"])
        _diff_set(report, "conditional_format", name, set(map(repr, sa["cf"])), set(map(repr, sb["cf"])))
        _diff_set(report, "data_validation", name, set(map(repr, sa["dv"])), set(map(repr, sb["dv"])))
        _diff_set(report, "hyperlink", name, sa["links"], sb["links"])
        if sa["freeze"] != sb["freeze"]:
            report.add("freeze_pane", name, "%s/%s" % (sa["freeze"], sb["freeze"]))
        if not styles:
            continue
        if sa["grid"] != sb["grid"]:
            report.add("grid_lines", name)
        if sa["tab"] != sb["tab"]:
            report.add("tab_color", name)
        blank = ("", False)
        for index in sorted(sa["cols"].keys() | sb["cols"].keys()):
            if sa["cols"].get(index, blank) != sb["cols"].get(index, blank):
                report.add("column_width", name, get_column_letter(index), sa["cols"].get(index), sb["cols"].get(index))
        for row in sorted(sa["heights"].keys() | sb["heights"].keys()):
            if sa["heights"].get(row) != sb["heights"].get(row):
                report.add("row_height", name, str(row), sa["heights"].get(row), sb["heights"].get(row))
        # A style on a cell with no content is invisible, so only cells that hold something are compared; a cell
        # with no style id has its book's first cell format.
        for coord in sorted((sa["styles"].keys() | sb["styles"].keys()) & (ca.keys() | cb.keys()), key=_coord_key):
            x, y = sa["styles"].get(coord, sa["default_style"]), sb["styles"].get(coord, sb["default_style"])
            if x != y:
                report.add("cell_style", name, coord, x, y)
    return {"counts": dict(report.counts), "per_sheet": {k: dict(v) for k, v in report.per_sheet.items()},
            "examples": dict(report.examples), "sheets_a": len(names_a), "sheets_b": len(names_b),
            "cells_compared": cells_compared}


def verdict(result: dict, gate_styles: bool = False, gate_cached: bool = False) -> str:
    gated = set(CONTENT_KINDS) | (set(STYLE_KINDS) if gate_styles else set()) | (set(CACHED_KINDS) if gate_cached else set())
    return "DIFF" if any(result["counts"].get(k) for k in gated) else "IDENTICAL"


def compare_files(path_a: str, path_b: str, examples: int = 5, show_values: bool = False, styles: bool = True) -> dict:
    return compare_books(read_book(str(path_a), styles), read_book(str(path_b), styles), examples, show_values, styles)


def render(result: dict, gate_styles: bool = False, gate_cached: bool = False) -> str:
    counts = result["counts"]
    lines = ["VERDICT %s" % verdict(result, gate_styles, gate_cached),
             "sheets A=%d B=%d  cells compared=%d" % (result["sheets_a"], result["sheets_b"], result["cells_compared"])]
    for group, kinds in (("content", CONTENT_KINDS), ("style", STYLE_KINDS), ("cached", CACHED_KINDS)):
        shown = ["%s=%d" % (k, counts[k]) for k in kinds if counts.get(k)]
        lines.append("%-7s %s" % (group, ", ".join(shown) if shown else "none"))
    for kind, items in result["examples"].items():
        lines.append("  e.g. %s: %s" % (kind, "; ".join(items)))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--examples", type=int, default=0, help="locations of the first N differences per kind")
    parser.add_argument("--show-values", action="store_true", help="include cell values in the examples (off by default)")
    parser.add_argument("--no-styles", action="store_true", help="skip the style group entirely")
    parser.add_argument("--gate-styles", action="store_true", help="style differences also fail the comparison")
    parser.add_argument("--gate-cached", action="store_true", help="cached-value differences also fail the comparison")
    parser.add_argument("--json", help="write the full result here")
    args = parser.parse_args(argv)
    try:
        result = compare_files(args.a, args.b, args.examples, args.show_values, not args.no_styles)
    except (OSError, BadZipFile, KeyError, ET.ParseError) as exc:
        print("UNREADABLE %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    print(render(result, args.gate_styles, args.gate_cached))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({**result, "verdict": verdict(result, args.gate_styles, args.gate_cached)}, handle, indent=2)
    return 0 if verdict(result, args.gate_styles, args.gate_cached) == "IDENTICAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
