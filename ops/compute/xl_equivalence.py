#!/usr/bin/env python3
"""Excel-vs-LibreOffice equivalence of computed workbook values (D-174 gate, D-177 tolerance).

    xl_equivalence.py compare ref.xlsx cand.xlsx [--exact-range 'Sheet!S3:T535'] [--ulp 4] [--expect-formulas N] [--no-cell-allowance]
    xl_equivalence.py excel-reference --in book.xlsx --out ref.xlsx [--policy melange] [--allow-function NAME]
    xl_equivalence.py negative-control --in book.xlsx --cell 'Sheet!A1' --cand-engine excel|libreoffice
    xl_equivalence.py probe-make probe.xlsx [--extended]   # SUM, LET, XLOOKUP, PERCENTRANK, ... with known values
    xl_equivalence.py probe-check view.xlsx      # known values against a recalculated probe (the CI smoke test)

`compare` reads the cached value of every formula cell on both sides (a recalculated Excel copy, and the view copy
ops/compute/office_recalc.py wrote) and tests them cell by cell:
  * kinds must match; text, booleans and errors are exact;
  * numbers are exact in tier 0, at most --ulp (default 4) ULP apart in tier 1; CELL_ULP names the one cell with its own
    allowance (D-177), which holds only while no formula reads that cell;
  * tier 0 = any cell that decides something (RANK/PERCENTRANK/COUNT*/MATCH/lookups/IF/comparisons) or feeds one or is
    fed by one, judged per COLUMN (sheet + column, conservative: a miss is only ever a stricter test), plus every
    --exact-range. Tier 1 is what is left: continuous cells that touch no discrete cell;
  * anti-vacuity: both sides must hold the same, non-zero set of formula cells, every one with a cached value.
Exit 0 = equivalent, 1 = not. The Excel side runs on Windows through COM on a COPY in a temp dir and never opens the
workbook it was given; nothing here runs a real workbook unless you point it at one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import office_recalc as OR  # noqa: E402

ULP_DEFAULT = 4
SHOW = 25
# D-177 allows a few ULP on a continuous cell that feeds nothing discrete. The one such cell the 2026-10-12 probes found:
# 'Coalition Profiles'!C29 = AVERAGE('Blended Model'!$S$3:$S$535), the mean of the 533 score cells. Excel adds them plainly,
# LibreOffice with compensation, so the means differ in the last bits (observed max 8 ULP, lo_probe_record_20261012.json
# avg_shape; the limit is twice that). No formula reads C29 (checked 2026-10-12 on the 98,269-formula workbook: no A1, range,
# whole-column or structured reference reaches it); compare() re-checks that every time the allowance is needed and drops it
# if a formula has started to read the cell. Its column also holds IF/IFERROR cells, which makes the column tier 0, hence the
# explicit entry. Every other cell keeps the tier rules.
CELL_ULP = {("Coalition Profiles", "C29"): 16}

# Functions whose result is a decision or a count, plus criteria-driven aggregates (the criteria compare values).
DISCRETE_FUNCS = {
    "RANK", "RANK.EQ", "RANK.AVG", "PERCENTRANK", "PERCENTRANK.INC", "PERCENTRANK.EXC", "PERCENTILE", "QUARTILE",
    "LARGE", "SMALL", "MEDIAN", "COUNT", "COUNTA", "COUNTBLANK", "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIF",
    "AVERAGEIFS", "_XLFN.MINIFS", "_XLFN.MAXIFS", "MATCH", "_XLFN.XMATCH", "_XLFN.XLOOKUP", "LOOKUP", "VLOOKUP", "HLOOKUP",
    "IF", "_XLFN.IFS", "IFERROR", "_XLFN.IFNA", "AND", "OR", "NOT", "ISNUMBER", "ISERROR", "ISBLANK", "ISTEXT", "ISNA"}
_STR_RE = re.compile(r'"(?:[^"]|"")*"')
_CMP_RE = re.compile(r"<=|>=|<>|<|>|=")
_REF_RE = re.compile(
    r"(?<![\w.$'])(?:(?:'((?:[^']|'')+)'|([A-Za-z_][\w.]*))!)?"
    r"(?:\$?([A-Z]{1,3})\$?[0-9]+(?::\$?([A-Z]{1,3})\$?[0-9]+)?|\$?([A-Z]{1,3}):\$?([A-Z]{1,3}))(?![\w(\[])")
_TABLE_RE = re.compile(r"([A-Za-z_][\w.]*)\[((?:[^\[\]]|\[[^\]]*\])*)\]")
_ROW_REF_RE = re.compile(
    r"(?<![\w.$'])(?:(?:'((?:[^']|'')+)'|([A-Za-z_][\w.]*))!)?"
    r"(?:\$?([A-Z]{1,3})\$?([0-9]+)(?::\$?([A-Z]{1,3})\$?([0-9]+))?|\$?([A-Z]{1,3}):\$?([A-Z]{1,3})|\$?([0-9]+):\$?([0-9]+))"
    r"(?![\w(\[])")


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def ulp_distance(a: float, b: float) -> int:
    """Number of representable doubles between a and b (-0.0 == 0.0)."""
    def key(x):
        i = struct.unpack("<q", struct.pack("<d", x))[0]
        return i if i >= 0 else -(1 << 63) - i
    return abs(key(a) - key(b))


def natural(addr):
    m = re.fullmatch(r"([A-Z]+)([0-9]+)", addr or "")
    return (int(m.group(2)), col_index(m.group(1))) if m else (0, 0)


def reads_cell(formula: str, home: str, sheet: str, addr: str) -> bool:
    """Does `formula`, written on sheet `home`, read cell sheet!addr? A1 cells and ranges, whole columns, whole rows."""
    m = re.fullmatch(r"([A-Z]+)([0-9]+)", addr)
    col, row = col_index(m.group(1)), int(m.group(2))
    for r in _ROW_REF_RE.finditer(_STR_RE.sub('""', formula)):
        if ((r.group(1) or "").replace("''", "'") or r.group(2) or home) != sheet:
            continue
        if r.group(3):
            c1, r1 = r.group(5) or r.group(3), r.group(6) or r.group(4)
            c_lo, c_hi, r_lo, r_hi = sorted((col_index(r.group(3)), col_index(c1))) + sorted((int(r.group(4)), int(r1)))
        elif r.group(7):
            c_lo, c_hi, r_lo, r_hi = *sorted((col_index(r.group(7)), col_index(r.group(8)))), 1, 1 << 30
        else:
            c_lo, c_hi, r_lo, r_hi = 1, 1 << 30, *sorted((int(r.group(9)), int(r.group(10))))
        if c_lo <= col <= c_hi and r_lo <= row <= r_hi:
            return True
    return False


def merge_formulas(ref: dict, cand: dict) -> dict:
    """The formula cells of both sides, each with its formula text taken from whichever side has it. Excel saves
    filled-down formulas as shared formulas whose children carry no text; the view copy keeps the original package's
    explicit text. The tiers are judged on the text, so they must see it."""
    out = {}
    for sheet in set(ref) | set(cand):
        r, c = ref.get(sheet, {}), cand.get(sheet, {})
        out[sheet] = {addr: ((r.get(addr) or c[addr])[0], (r.get(addr) or c[addr])[1],
                             (r.get(addr) or ("", "", ""))[2] or (c.get(addr) or ("", "", ""))[2])
                      for addr in set(r) | set(c)}
    return out


# ------------------------------------------------------------------------------------------ tiers

def read_tables(path):
    """{table name: (sheet, first column index, [column names])} from xl/tables/*.xml."""
    import xml.etree.ElementTree as ET
    out = {}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for sheet, part in OR.sheet_parts(zf):
            rels = f"xl/worksheets/_rels/{part.rsplit('/', 1)[1]}.rels"
            if rels not in names:
                continue
            for rel in ET.fromstring(zf.read(rels)).iter(f"{{{OR.REL_NS}}}Relationship"):
                if not (rel.get("Type") or "").endswith("/table"):
                    continue
                target = rel.get("Target") or ""
                tpart = target.lstrip("/") if target.startswith("/") else str(Path("xl/worksheets", target).as_posix())
                tpart = os.path.normpath(tpart).replace("\\", "/")
                if tpart not in names:
                    continue
                t = ET.fromstring(zf.read(tpart))
                first = re.match(r"([A-Z]+)", t.get("ref") or "")
                cols = [c.get("name") for c in t.iter(f"{{{OR.MAIN_NS}}}tableColumn")]
                if first:
                    out[t.get("displayName") or t.get("name")] = (sheet, col_index(first.group(1)), cols)
    return out


def is_discrete(formula: str) -> bool:
    s = _STR_RE.sub('""', formula)
    return bool({f.upper() for f in OR.FUNC_RE.findall(s)} & DISCRETE_FUNCS) or bool(_CMP_RE.search(s))


def precedent_columns(sheet: str, formula: str, tables: dict):
    """Columns (sheet, index) a formula reads: A1 ranges, whole columns and table columns. Column granularity on purpose."""
    s = _STR_RE.sub('""', formula)
    out = set()
    for m in _REF_RE.finditer(s):
        ref_sheet = (m.group(1) or "").replace("''", "'") or m.group(2) or sheet
        lo = m.group(3) or m.group(5)
        hi = m.group(4) or m.group(6) or lo
        for c in range(col_index(lo), col_index(hi) + 1):
            out.add((ref_sheet, c))
    for m in _TABLE_RE.finditer(s):
        if m.group(1) in tables:
            t_sheet, first, cols = tables[m.group(1)]
            names = re.findall(r"\[([^\[\]#@]+)\]", m.group(2)) or [m.group(2)]
            for n in names:
                if n in cols:
                    out.add((t_sheet, first + cols.index(n)))
    return out


def tier0_columns(values: dict, tables: dict):
    """Columns that must match exactly: discrete columns, everything that feeds them, everything they feed."""
    reads, seeds = defaultdict(set), set()
    for sheet, rows in values.items():
        for addr, (_k, _v, formula) in rows.items():
            node = (sheet, col_index(re.match(r"[A-Z]+", addr).group()))
            reads[node] |= precedent_columns(sheet, formula, tables)
            if is_discrete(formula):
                seeds.add(node)
    fed_by = defaultdict(set)
    for node, deps in reads.items():
        for d in deps:
            fed_by[d].add(node)
    exact = set(seeds)
    for graph in (reads, fed_by):                       # upstream closure, then downstream closure
        todo = list(seeds)
        seen = set(seeds)
        while todo:
            for nxt in graph.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        exact |= seen
    return exact


def parse_ranges(specs):
    """'Sheet!S3:T535' -> (sheet, col0, col1, row0, row1)."""
    out = []
    for spec in specs:
        sheet, _, rng = spec.rpartition("!")
        m = re.fullmatch(r"\$?([A-Z]+)\$?([0-9]+)(?::\$?([A-Z]+)\$?([0-9]+))?", rng)
        if not sheet or not m:
            raise SystemExit(f"--exact-range must look like 'Sheet!A1:B9', got {spec!r}")
        c1, r1 = m.group(3) or m.group(1), m.group(4) or m.group(2)
        out.append((sheet.strip("'"), col_index(m.group(1)), col_index(c1), int(m.group(2)), int(r1)))
    return out


def make_tier0(values, tables, exact_ranges=()):
    cols = tier0_columns(values, tables)
    ranges = parse_ranges(exact_ranges)

    def tier0(sheet, addr):
        m = re.fullmatch(r"([A-Z]+)([0-9]+)", addr)
        c, r = col_index(m.group(1)), int(m.group(2))
        return (sheet, c) in cols or any(s == sheet and c0 <= c <= c1 and r0 <= r <= r1 for s, c0, c1, r0, r1 in ranges)
    return tier0


# ------------------------------------------------------------------------------------------ comparison

def compare(ref: dict, cand: dict, tier0, ulp=ULP_DEFAULT, expect_formulas=None, cell_ulp=None):
    """ref/cand: office_recalc.read_cached_formula_values output. Returns the report dict (report['equivalent']).
    cell_ulp: {(sheet, addr): max ULP} per-cell allowances (default CELL_ULP; {} for none). An allowance replaces the tier
    rule for that cell's numbers only, and only while no formula on either side reads the cell."""
    cell_ulp = CELL_ULP if cell_ulp is None else cell_ulp
    sheets, totals, bad, voided, void_notes = {}, Counter(), [], {}, []

    def allowance(sheet, addr):
        cap = cell_ulp.get((sheet, addr))
        if cap is None:
            return None
        if (sheet, addr) not in voided:
            readers = sorted({f"{s}!{a}" for side in (ref, cand) for s, rows in side.items() for a, (_k, _v, f) in rows.items()
                              if f and reads_cell(f, s, sheet, addr)})
            voided[(sheet, addr)] = readers
            if readers:
                void_notes.append(f"the allowance for {sheet}!{addr} is void: it is read by {', '.join(readers[:3])}"
                                  + (f" and {len(readers) - 3} more" if len(readers) > 3 else ""))
        return None if voided[(sheet, addr)] else cap

    for sheet in sorted(set(ref) | set(cand)):
        r, c, s = ref.get(sheet, {}), cand.get(sheet, {}), Counter()
        for addr in sorted(set(r) | set(c), key=natural):
            a, b = r.get(addr), c.get(addr)
            why = None
            if a is None or b is None:
                why = "only_in_candidate" if a is None else "only_in_reference"
            elif a[0] == "missing" or b[0] == "missing":
                why = "no_cached_value"
            elif a[0] != b[0]:
                why = "type"
            elif a[0] == "n":
                if a[1] == b[1]:
                    s["exact"] += 1
                else:
                    d = ulp_distance(a[1], b[1])
                    cap = allowance(sheet, addr)
                    if cap is not None:
                        if d <= cap:
                            s["within_cell_ulp"] += 1
                        else:
                            why = "cell_allowance_number"
                    elif tier0(sheet, addr):
                        why = "tier0_number"
                    elif d <= ulp:
                        s["within_ulp"] += 1
                    else:
                        why = "tier1_number"
            elif a[1] == b[1]:
                s["exact"] += 1
            else:
                why = a[0] + "_value"
            if why:
                s[why] += 1
                if len(bad) < SHOW:
                    f = (a[2] if a and a[2] else b[2] if b else "")
                    bad.append({"sheet": sheet, "cell": addr, "why": why, "reference": a[:2] if a else None,
                                "candidate": b[:2] if b else None, "formula": f[:200],
                                **({"ulp": ulp_distance(a[1], b[1])} if why.endswith("number") else {})})
        s["compared"] = len(set(r) & set(c))
        s["formulas_reference"], s["formulas_candidate"] = len(r), len(c)
        sheets[sheet] = dict(s)
        totals.update(s)
    mismatches = sum(v for k, v in totals.items() if k not in
                     ("exact", "within_ulp", "within_cell_ulp", "compared", "formulas_reference", "formulas_candidate"))
    census = totals["formulas_reference"]
    problems = list(void_notes)
    if mismatches:
        problems.append(f"{mismatches} cell(s) differ")
    if census == 0:
        problems.append("vacuous: no formula cells")
    if totals["formulas_reference"] != totals["formulas_candidate"] or totals["compared"] != census:
        problems.append("formula census differs between the two workbooks")
    if expect_formulas is not None and census != expect_formulas:
        problems.append(f"expected {expect_formulas} formulas, found {census}")
    return {"equivalent": not problems, "problems": problems, "ulp_limit": ulp, "totals": dict(totals), "per_sheet": sheets,
            "cell_ulp": {f"{s}!{a}": cap for (s, a), cap in cell_ulp.items()}, "first_differences": bad}


# ------------------------------------------------------------------------------------------ Excel reference (COM)

def excel_recalculate(src, workdir=None, *, method="CalculateFullRebuild", timeout=1800, policy="melange", allow=()):
    """Recalculate a COPY of src in Excel and return the copy's path. Windows + Excel only. The same static preflight
    as the container runs first, so no macro or external link is ever opened."""
    if sys.platform != "win32":
        raise SystemExit("the Excel reference needs Windows with Excel")
    src = Path(src)
    safety, _ = OR.scan_package(src, set(OR.POLICIES[policy]) | {a.upper() for a in allow})
    if not safety["safe_for_isolated_calculation"]:
        raise SystemExit("safety preflight failed closed; Excel was not started")
    import gc

    import win32com.client
    workdir = Path(workdir or tempfile.mkdtemp(prefix="xl-ref-"))
    workdir.mkdir(parents=True, exist_ok=True)
    copy = workdir / f"{src.stem}.excel-ref.xlsx"
    if copy.resolve() == src.resolve():
        raise SystemExit("refusing to calculate the given workbook in place")
    shutil.copyfile(src, copy)
    xl = wb = boot = None
    try:
        xl = win32com.client.DispatchEx("Excel.Application")
        xl.Visible, xl.DisplayAlerts, xl.EnableEvents = False, False, False
        xl.AskToUpdateLinks, xl.ScreenUpdating, xl.AutomationSecurity = False, False, 3   # msoAutomationSecurityForceDisable
        boot = xl.Workbooks.Add()
        xl.Calculation = -4135                                                          # manual: no calculation on open
        boot.Close(False)
        wb = xl.Workbooks.Open(str(copy), 0, False)
        getattr(xl, method)()
        deadline = time.monotonic() + timeout
        while int(xl.CalculationState) != 0 and time.monotonic() < deadline:
            time.sleep(0.1)
        if int(xl.CalculationState) != 0:
            raise SystemExit(f"Excel calculation did not finish within {timeout} s")
        xl.CalculateBeforeSave = False
        wb.Save()
        wb.Close(False)
        wb = None
    finally:
        try:
            if wb is not None:
                wb.Close(False)
        finally:
            if xl is not None:
                xl.Quit()
            boot = wb = xl = None                           # drop the COM proxies before the process can tear COM down
            gc.collect()
    return copy


# ------------------------------------------------------------------------------------------ negative control

def perturb(src, dst, cell, delta=1.0):
    """Copy src -> dst with one numeric constant cell ('Sheet!A1') changed by delta. Byte edit; nothing else moves."""
    sheet, _, addr = cell.rpartition("!")
    sheet = sheet.strip("'")
    done = []

    def edit(data):
        def repl(m):
            if OR.parse_attrs(m.group(1)).get("r") != addr or not m.group(2) or b"<f" in m.group(2):
                return m.group(0)
            attrs = OR.parse_attrs(m.group(1))
            vm = re.search(rb"<v>(.*?)</v>", m.group(2), re.S)
            if attrs.get("t", "n") != "n" or not vm:
                raise SystemExit(f"{cell} is not a numeric constant")
            done.append(addr)
            new = OR.fmt_number(float(vm.group(1)) + delta).encode()
            return b"<c" + m.group(1) + b">" + m.group(2).replace(vm.group(0), b"<v>" + new + b"</v>") + b"</c>"
        return OR.CELL_RE.sub(repl, data)
    with zipfile.ZipFile(src) as zin:
        part = dict(OR.sheet_parts(zin)).get(sheet)
        if not part:
            raise SystemExit(f"no sheet {sheet!r}")
        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info)
                if info.filename == part:
                    data = edit(data)
                zi = zipfile.ZipInfo(info.filename, info.date_time)
                zi.compress_type, zi.external_attr = info.compress_type, info.external_attr
                zout.writestr(zi, data, compress_type=info.compress_type)
    if not done:
        raise SystemExit(f"{cell} not found as a numeric constant")


def negative_control(src, cell, ref_fn, cand_fn, workdir, delta=1.0, ulp=ULP_DEFAULT, tables=None, exact_ranges=()):
    """Reference = recalculation of src; candidate = recalculation of src with `cell` changed. compare() must FAIL."""
    ref = OR.read_cached_formula_values(ref_fn(src))
    pert = Path(workdir) / "perturbed.xlsx"
    perturb(src, pert, cell, delta)
    cand = OR.read_cached_formula_values(cand_fn(pert))
    report = compare(ref, cand, make_tier0(merge_formulas(ref, cand), tables or {}, exact_ranges), ulp)
    ok = (not report["equivalent"]) and report["totals"].get("compared", 0) > 0 and bool(report["first_differences"])
    return ok, report


# ------------------------------------------------------------------------------------------ probe workbook

# (cell on 'Probe', formula as stored in the file, kind, Excel's value, value LibreOffice is documented to give or None)
PROBE = [
    ("B1", "SUM(Data!A1:A4)", "n", 100.0, None),
    ("B2", "_xlfn.LET(_xlpm.x,Data!A1,_xlpm.y,Data!A2,_xlpm.x+_xlpm.y*2)", "n", 50.0, None),
    ("B3", '_xlfn.XLOOKUP("c",Data!B1:B4,Data!A1:A4)', "n", 30.0, None),
    # Excel truncates to the requested significance (compute_pillars.py pins 5 places); LibreOffice rounded until
    # tdf#104981 -> 0.66667. A run that gives the rounded value is reported as KNOWN_DIVERGENCE, anything else FAILS.
    ("B4", "PERCENTRANK(Data!A1:A4,30,5)", "n", 0.66666, 0.66667),
    ("B5", "Data!A1<Data!A2", "b", True, None),
    ("B6", '"x"&"y"', "str", "xy", None),
    ("B7", "1/0", "e", "#DIV/0!", None),
    ("B8", 'IF(Data!A1>100,"big","small")', "str", "small", None),
    ("B9", "RANK(Data!A3,Data!A1:A4)", "n", 2.0, None),
    ("B10", 'SUMIFS(Data!A1:A4,Data!B1:B4,"<>b")', "n", 80.0, None),
    ("B11", 'COUNTIFS(Data!A1:A4,">15")', "n", 3.0, None),
    ("B12", '_xlfn.MINIFS(Data!A1:A4,Data!A1:A4,">15")', "n", 20.0, None),
    ("B13", 'INDEX(Data!A1:A4,MATCH("d",Data!B1:B4,0))', "n", 40.0, None),
    ("B14", '""', "str", "", None),
    ("B15", "Data!C2+Data!C3", "n", 0.1 + 0.2, None),
    ("B16", 'AVERAGEIF(Data!A1:A4,">15")', "n", 30.0, None),
    ("B17", "SUMPRODUCT(Data!A1:A4,Data!A1:A4)", "n", 3000.0, None),
    ("B18", "N(Data!A1)", "n", 10.0, None),
    ("B19", "IFERROR(1/0,-1)", "n", -1.0, None),
]
PROBE_FUNCTIONS = ["_XLFN.XLOOKUP"]          # on top of the melange policy: pass as --allow-function

# The ways LibreOffice is expected to be able to differ (design 3.4): compensated summation, ties 1 ULP apart, approximate
# equality, wildcard and number-to-text criteria, error codes, interpolated PERCENTRANK. Same shape as PROBE, with the value
# Excel itself gives (recorded from a real Excel run, see test_excel_reference_reproduces_the_extended_probe). Any other value
# is reported as DIVERGENCE: it never fails the probe (that is the job of `compare` against an Excel reference).
EXTENDED_DATA = {"D1": 0.1, "D2": 0.2, "D3": -0.3, "E1": 0.3, "E2": 0.30000000000000004, "E3": 0.1, "F1": 0, "F2": 1e-17,
                 "G1": "apple", "G2": "Apple", "G3": "banana", "H1": 0.30000000000000004, "H2": 0.3, "H3": 0.1}
PROBE_EXTENDED = [
    ("B21", "SUM(Data!D1:D3)", "n", 0.0, None),
    ("B22", "RANK(Data!E1,Data!E1:E3)", "n", 2.0, None),
    ("B23", "RANK(Data!E2,Data!E1:E3)", "n", 1.0, None),
    ("B24", '_xlfn.LET(_xlpm.x,Data!F1,IF(_xlpm.x=0,"zero","nonzero"))', "str", "zero", None),
    ("B25", '_xlfn.LET(_xlpm.x,Data!F2,IF(_xlpm.x=0,"zero","nonzero"))', "str", "nonzero", None),
    ("B26", 'COUNTIF(Data!G1:G3,"a*")', "n", 2.0, None),
    ("B27", 'COUNTIF(Data!G1:G3,"?pple")', "n", 2.0, None),
    ("B28", 'MATCH("b*",Data!G1:G3,0)', "n", 3.0, None),
    ("B29", 'COUNTIFS(Data!H1:H3,">"&Data!H2)', "n", 0.0, None),
    ("B30", "INDEX(Data!A1:A4,9)", "e", "#REF!", None),
    ("B31", 'MATCH("zz",Data!B1:B4,0)', "e", "#N/A", None),
    ("B32", 'Data!A1+"x"', "e", "#VALUE!", None),
    ("B33", "PERCENTRANK(Data!A1:A4,25,5)", "n", 0.5, None),
    ("B34", 'IF(Data!E1=Data!E2,"eq","ne")', "str", "eq", None),
]


def write_probe(path, probe=None, extended=False):
    """The probe workbook. extended=True adds the PROBE_EXTENDED cells and their data (the cases LibreOffice may differ on)."""
    probe = PROBE if probe is None else probe
    data = {"A1": 10, "A2": 20, "A3": 30, "A4": 40, "B1": "a", "B2": "b", "B3": "c", "B4": "d", "C1": 1.5, "C2": 0.1, "C3": 0.2}
    if extended:
        probe, data = [*probe, *PROBE_EXTENDED], {**data, **EXTENDED_DATA}

    def sheet_xml(cells):
        rows = defaultdict(list)
        for addr, spec in cells.items():
            rows[natural(addr)[0]].append((natural(addr)[1], addr, spec))
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="%s"><sheetData>' % OR.MAIN_NS]
        for r in sorted(rows):
            out.append(f'<row r="{r}">')
            for _, addr, (kind, v) in sorted(rows[r]):
                if kind == "f":
                    out.append(f'<c r="{addr}"><f>{escape(v)}</f></c>')
                elif isinstance(v, str):
                    out.append(f'<c r="{addr}" t="inlineStr"><is><t>{escape(v)}</t></is></c>')
                else:
                    out.append(f'<c r="{addr}"><v>{v!r}</v></c>')
            out.append("</row>")
        out.append("</sheetData></worksheet>")
        return "".join(out)
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="%s"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>' % OR.REL_NS,
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/><sheet name="Probe" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>' % (OR.MAIN_NS, OR.DOCREL_NS),
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="%s"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' % OR.REL_NS,
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="%s"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>' % OR.MAIN_NS,
        "xl/worksheets/sheet1.xml": sheet_xml({a: ("v", v) for a, v in data.items()}),
        "xl/worksheets/sheet2.xml": sheet_xml({a: ("f", f) for a, f, *_ in probe}),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            zi = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, body.encode("utf-8"))



def check_probe(path):
    """Known values against a recalculated probe. Returns (rows, failed). A PROBE cell that is wrong is FAIL (or
    KNOWN_DIVERGENCE when it holds the documented LibreOffice value); a PROBE_EXTENDED cell, present only in an extended
    probe, is PASS or DIVERGENCE and never fails."""
    got = OR.read_cached_formula_values(path)["Probe"]
    rows, failed = [], False
    for tier, table in (("known", PROBE), ("extended", [r for r in PROBE_EXTENDED if r[0] in got])):
        for cell, formula, kind, want, lo_known in table:
            have = got.get(cell, ("missing", None))[:2]
            if have == (kind, want):
                status = "PASS"
            elif kind == "n" and have[0] == "n" and ulp_distance(have[1], want) <= ULP_DEFAULT:
                status = "PASS"
            elif lo_known is not None and have == (kind, lo_known):
                status = "KNOWN_DIVERGENCE"
            elif tier == "extended":
                status = "DIVERGENCE"
            else:
                status = "FAIL"
                failed = True
            rows.append({"cell": cell, "formula": formula, "expected": [kind, want], "got": list(have), "status": status})
    return rows, failed


# ------------------------------------------------------------------------------------------ CLI

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("reference", type=Path)
    c.add_argument("candidate", type=Path)
    c.add_argument("--exact-range", action="append", default=[], metavar="'Sheet!A1:B9'")
    c.add_argument("--ulp", type=int, default=ULP_DEFAULT)
    c.add_argument("--expect-formulas", type=int)
    c.add_argument("--no-cell-allowance", action="store_true", help="ignore CELL_ULP: Excel against Excel must be identical")
    c.add_argument("--report", type=Path)
    e = sub.add_parser("excel-reference")
    e.add_argument("--in", dest="src", required=True, type=Path)
    e.add_argument("--out", required=True, type=Path, help="where the Excel-calculated COPY goes")
    e.add_argument("--method", choices=["CalculateFull", "CalculateFullRebuild"], default="CalculateFullRebuild")
    e.add_argument("--policy", choices=sorted(OR.POLICIES), default="melange")
    e.add_argument("--allow-function", action="append", default=[])
    e.add_argument("--timeout", type=int, default=1800)
    n = sub.add_parser("negative-control")
    n.add_argument("--in", dest="src", required=True, type=Path)
    n.add_argument("--cell", required=True, help="numeric constant to change, 'Sheet!A1'")
    n.add_argument("--delta", type=float, default=1.0)
    n.add_argument("--cand-engine", choices=["excel", "libreoffice"], default="excel")
    n.add_argument("--policy", choices=sorted(OR.POLICIES), default="melange")
    n.add_argument("--allow-function", action="append", default=[])
    pm = sub.add_parser("probe-make")
    pm.add_argument("out", type=Path)
    pm.add_argument("--extended", action="store_true", help="add the cases LibreOffice may differ on (reported, never failing)")
    pc = sub.add_parser("probe-check")
    pc.add_argument("workbook", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "probe-make":
        write_probe(a.out, extended=a.extended)
        print(json.dumps({"probe": str(a.out), "sha256": sha256(a.out), "allow_function": PROBE_FUNCTIONS, "extended": a.extended}))
        return 0
    if a.cmd == "probe-check":
        rows, failed = check_probe(a.workbook)
        for r in rows:
            print(f"{r['status']:<17}{r['cell']:<5}{r['formula']}  expected={r['expected']} got={r['got']}")
        return 1 if failed else 0
    if a.cmd == "excel-reference":
        out = excel_recalculate(a.src, a.out.parent / (a.out.stem + ".work"), method=a.method, timeout=a.timeout,
                                policy=a.policy, allow=a.allow_function)
        shutil.move(str(out), str(a.out))
        print(json.dumps({"reference": str(a.out), "sha256": sha256(a.out)}))
        return 0
    if a.cmd == "negative-control":
        work = Path(tempfile.mkdtemp(prefix="xl-negctl-"))
        import xl_engine

        def excel_fn(p):
            return excel_recalculate(p, work / "excel", policy=a.policy, allow=a.allow_function)

        def lo_fn(p):
            return xl_engine.recalc_copy(p, expected_sha256=sha256(p), out=work / "lo" / (Path(p).stem + ".view.xlsx"),
                                         engine_name="libreoffice", policy=a.policy, allow_functions=a.allow_function)
        cand = excel_fn if a.cand_engine == "excel" else lo_fn
        ok, report = negative_control(a.src, a.cell, excel_fn, cand, work, a.delta, tables=read_tables(a.src))
        print(json.dumps({"negative_control": "PASS" if ok else "FAIL", "differences": report["totals"],
                          "first": report["first_differences"][:5]}, indent=2))
        return 0 if ok else 1
    ref, cand = OR.read_cached_formula_values(a.reference), OR.read_cached_formula_values(a.candidate)
    report = compare(ref, cand, make_tier0(merge_formulas(ref, cand), read_tables(a.reference), a.exact_range), a.ulp,
                     a.expect_formulas, {} if a.no_cell_allowance else None)
    text = json.dumps(report, indent=2, sort_keys=True)
    if a.report:
        a.report.write_text(text + "\n", encoding="utf-8")
    for sheet, s in report["per_sheet"].items():
        print(f"{sheet}: " + ", ".join(f"{k}={v}" for k, v in sorted(s.items())))
    print("EQUIVALENT" if report["equivalent"] else "NOT EQUIVALENT: " + "; ".join(report["problems"]))
    for d in report["first_differences"][:SHOW]:
        print(" ", json.dumps(d))
    return 0 if report["equivalent"] else 1


if __name__ == "__main__":
    sys.exit(main())
