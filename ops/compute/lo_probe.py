#!/usr/bin/env python3
"""Synthetic probes of LibreOffice-as-calculator against Excel's behaviour (D-174 questions, 2026-10-12). Synthetic data only.

    lo_probe.py make DIR                 # write the probe workbooks to DIR/in
    lo_probe.py run DIR IMAGE            # make, recalculate each twice in the office-calc container (network none), check
    lo_probe.py check DIR                # judge DIR/out/*.view.xlsx against the models; DIR/report.json
    lo_probe.py excel DIR                # the Excel oracle: recalculate DIR/in/*.xlsx in a SEPARATE Excel instance -> DIR/excel_out
    lo_probe.py oracle DIR               # Excel against the models and against LibreOffice; DIR/report_excel.json

What it settles, and against what "Excel" means here (no Excel is started; see MODEL):
  * PERCENTRANK: does the engine truncate to the significance digits (Excel; fec/engine/compute_pillars.py:59) or round?
    Generated cases where the two differ, default significance, interpolated x, ties, the 5-digit member shape of the real
    workbook (x is always a member of its own column), and the `_xlfn.PERCENTRANK.INC` spelling.
  * AVERAGE over numbers, blanks, text, logicals, numeric text and errors, and over a 533-cell range shaped like
    'Blended Model'!S3:S535 (all numbers), against Excel's documented rules.
  * determinism: the same input run twice gives byte-identical view copies, or not.
MODEL: Excel's value is the documented rule evaluated in exact rational arithmetic (PERCENTRANK: below/(n-1) for a member,
linear interpolation otherwise, digits truncated), pinned where a real Excel run is recorded (the PERCENTRANK vector in
xl_equivalence.PROBE). A real Excel run on the same workbooks is the stronger oracle: `xl_equivalence.py excel-reference`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
import zipfile
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import office_recalc as OR  # noqa: E402
import xl_equivalence as X  # noqa: E402

SEED = 20261012
COMPOSE = HERE / "office-calc.compose.yaml"
ALLOW = ["AVERAGE", "_XLFN.PERCENTRANK.INC", "TRUNC", "COUNT"]   # on top of the melange policy; AVERAGE and TRUNC joined it on 2026-10-12
BOOKS = ("percentrank", "pr_shape", "average", "avg_shape", "iferror", "avg_forms")
DEFAULT_SIG = 3


def col(i):
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# ------------------------------------------------------------------------------------------ workbook writer

def cell_xml(addr, spec):
    kind, v = spec
    if kind == "f":
        return f'<c r="{addr}"><f>{escape(v)}</f></c>'
    if kind == "n":
        return f'<c r="{addr}"><v>{v!r}</v></c>'
    if kind == "s":
        return f'<c r="{addr}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
    if kind == "b":
        return f'<c r="{addr}" t="b"><v>{int(v)}</v></c>'
    return f'<c r="{addr}" t="e"><v>{v}</v></c>'                      # kind "e": an error constant such as #N/A


def sheet_xml(cells):
    rows = {}
    for addr, spec in cells.items():
        r, c = X.natural(addr)
        rows.setdefault(r, []).append((c, addr, spec))
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="%s"><sheetData>' % OR.MAIN_NS]
    for r in sorted(rows):
        out.append(f'<row r="{r}">' + "".join(cell_xml(a, s) for _c, a, s in sorted(rows[r])) + "</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_book(path, data, probe, names=("Data", "Probe")):
    """A two-sheet package: the skeleton of xl_equivalence.write_probe with its sheets and sheet names replaced."""
    path = Path(path)
    skeleton = path.with_name(path.name + ".skeleton")
    X.write_probe(skeleton)
    with zipfile.ZipFile(skeleton) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            body = zin.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = sheet_xml(data).encode("utf-8")
            elif info.filename == "xl/worksheets/sheet2.xml":
                body = sheet_xml(probe).encode("utf-8")
            elif info.filename == "xl/workbook.xml":
                body = body.decode().replace('name="Data"', f'name="{names[0]}"').replace('name="Probe"', f'name="{names[1]}"').encode()
            zi = zipfile.ZipInfo(info.filename, info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, body)
    skeleton.unlink()
    return path


# ------------------------------------------------------------------------------------------ PERCENTRANK model and cases

def pr_exact(vals, x, sig):
    """Excel's PERCENTRANK(.INC) in exact arithmetic, or None for #N/A. Interpolation assumes distinct neighbours."""
    s = sorted(vals)
    if x < s[0] or x > s[-1]:
        return None
    n = len(vals)
    if x in s:
        raw = Fraction(sum(1 for v in vals if v < x), n - 1)
    else:
        i = max(j for j, v in enumerate(s) if v < x)
        assert s[i] != s[i + 1] and s.count(s[i]) == 1, "interpolation next to a tie is outside the model"
        raw = (i + (Fraction(x) - Fraction(s[i])) / (Fraction(s[i + 1]) - Fraction(s[i]))) / (n - 1)
    scale = 10 ** sig
    t, r = math.floor(raw * scale), math.floor(raw * scale + Fraction(1, 2))
    return {"raw": raw, "trunc": float(Fraction(t, scale)), "round": float(Fraction(r, scale)), "differ": t != r,
            "round_sig": round_significant(raw, sig), "margin": abs(raw * scale - round(raw * scale))}


def round_significant(raw, sig):
    """raw rounded half-up to `sig` SIGNIFICANT digits (leading zeros after the point do not count): the other reading of
    'significance'. Equal to the decimal-places rounding for raw >= 0.1."""
    if raw == 0:
        return 0.0
    d = math.floor(math.log10(raw))
    while Fraction(10) ** d > raw:
        d -= 1
    while Fraction(10) ** (d + 1) <= raw:
        d += 1
    scale = Fraction(10) ** (sig - 1 - d)
    return float(Fraction(math.floor(raw * scale + Fraction(1, 2))) / scale)


def pillars_percentrank(vals, x):
    """fec/engine/compute_pillars.py:59, the Python authority for the 5-digit member shape."""
    return math.floor(sum(1 for v in vals if v < x) / (len(vals) - 1) * 1e5) / 1e5


def pr_cases(seed=SEED):
    """Generated cases. Every case that the engine could get wrong by rounding has trunc != round; controls have trunc == round."""
    rng = random.Random(seed)
    cases = [dict(group="vector", vals=[10.0, 20.0, 30.0, 40.0], x=30.0, xkind="literal", sig=5)]

    def distinct(lo, hi, n):
        return [round(v, 2) for v in rng.sample([lo + i * 0.01 for i in range(int((hi - lo) * 100))], n)]

    def member_distinct():
        v = distinct(-50, 500, rng.randint(4, 40))
        return v, rng.choice(v), "member"

    def member_ties():
        v = [rng.randint(0, 6) * 5.0 for _ in range(rng.randint(6, 30))]
        return v, rng.choice(v), "member"

    def between():
        v = sorted(distinct(-50, 500, rng.randint(4, 40)))
        i = rng.randrange(len(v) - 1)
        return v, round(rng.uniform(v[i], v[i + 1]), 4), "between"

    def fill(group, maker, want, differ, sigs=(None, 1, 2, 3, 3, 4, 5)):
        got = 0
        while got < want:
            vals, x, xkind = maker()
            sig = rng.choice(sigs)
            if xkind == "between" and not (min(vals) < x < max(vals) and x not in vals):
                continue
            e = pr_exact(vals, x, sig or DEFAULT_SIG)
            if e and e["differ"] == differ and e["margin"] > 1e-6:          # not near a truncation boundary: float noise cannot flip it
                cases.append(dict(group=group, vals=vals, x=x, xkind=xkind, sig=sig))
                got += 1

    fill("member_distinct", member_distinct, 10, True)
    fill("member_ties", member_ties, 8, True)
    fill("between", between, 10, True)
    fill("control_equal", member_distinct, 3, False)
    fill("control_equal", between, 3, False)
    for x, name in ((10.0, "edge_min"), (40.0, "edge_max"), (9.0, "edge_below"), (41.0, "edge_above")):
        cases.append(dict(group=name, vals=[10.0, 20.0, 30.0, 40.0], x=x, xkind="between" if name.endswith(("below", "above")) else "member", sig=5))
    for c in [c for c in cases if c["group"] in ("member_distinct", "between")][:3] + [c for c in cases if c["group"] == "member_ties"][:3]:
        cases.append({**c, "group": "spelling_INC", "inc": True})
    for c in cases:
        if c["xkind"] == "member":
            c["row"] = c["vals"].index(c["x"]) + 1
    return cases


def build_percentrank(path, cases=None):
    cases = cases or pr_cases()
    data, probe = {}, {}
    for k, c in enumerate(cases):
        L, n = col(k + 1), len(c["vals"])
        for i, v in enumerate(c["vals"]):
            data[f"{L}{i + 1}"] = ("n", v)
        if c["xkind"] == "member":
            xref = f"Data!{L}{c['row']}"
        elif c["xkind"] == "between":
            data[f"{L}{n + 2}"] = ("n", c["x"])
            xref = f"Data!{L}{n + 2}"
        else:
            xref = repr(c["x"])
        sig = "" if c["sig"] is None else f",{c['sig']}"
        fn = "_xlfn.PERCENTRANK.INC" if c.get("inc") else "PERCENTRANK"
        probe[f"A{k + 1}"] = ("f", f"{fn}(Data!${L}$1:${L}${n},{xref}{sig})")
    return write_book(path, data, probe)


def shape_columns(seed=SEED, n=533):
    """Six 533-row columns shaped like the workbook's PERCENTRANK columns: x is a member of its own column, with ties."""
    rng = random.Random(seed + 1)
    cols = {
        "distinct": [round(rng.uniform(0, 1), 6) for _ in range(n)],
        "zeros_30pct": [0.0 if rng.random() < 0.3 else round(rng.uniform(0, 100), 4) for _ in range(n)],
        "tie_clusters": [float(rng.randint(0, 19)) for _ in range(n)],
        "all_equal": [0.0] * n,
        "signed": [round(rng.uniform(-5, 5), 3) for _ in range(n)],
        "with_blanks": [None if rng.random() < 0.08 else round(rng.uniform(1, 50), 3) for _ in range(n)],
    }
    return cols


def build_pr_shape(path):
    data, probe = {}, {}
    for j, (name, vals) in enumerate(shape_columns().items()):
        L, n = col(j + 1), len(vals)
        rng = f"Data!${L}$1:${L}${n}"
        for i, v in enumerate(vals):
            if v is not None:
                x = f"Data!{L}{i + 1}"
                data[x[5:]] = ("n", v)
                probe[f"{L}{i + 1}"] = ("f", f"PERCENTRANK({rng},{x},5)")                                    # the workbook's formula
                probe[f"{col(j + 11)}{i + 1}"] = ("f", f"TRUNC(PERCENTRANK({rng},{x},15),5)")                # candidate rewrite A
                probe[f"{col(j + 17)}{i + 1}"] = ("f", f"TRUNC((RANK({x},{rng},1)-1)/(COUNT({rng})-1),5)")   # candidate rewrite B
    # the IFERROR(...,fallback) shape of the workbook's H column: a blank, a text and an empty-string x
    probe["Y1"] = ("f", "IFERROR(PERCENTRANK(Data!$E$1:$E$533,Data!J1,5),-1)")          # Data!J1 is blank
    data["J2"] = ("s", "abc")
    probe["Y2"] = ("f", "IFERROR(PERCENTRANK(Data!$E$1:$E$533,Data!J2,5),-1)")
    probe["X3"] = ("f", '""')
    probe["Y3"] = ("f", "IFERROR(PERCENTRANK(Data!$E$1:$E$533,Probe!X3,5),-1)")
    return write_book(path, data, probe)


IFERR_SPECIAL = ("blank", "text", "empty_string_formula", "member", "interpolated", "below_min", "above_max", "error_constant",
                 "logical_true", "numeric_text")


def iferror_xs(seed=SEED):
    """x variants for the workbook's IFERROR(PERCENTRANK(range,x,5),fallback) shape: ten special kinds, then 40 interpolated values."""
    rng = random.Random(seed + 3)
    col_e = shape_columns()["signed"]
    special = [None, ("s", "abc"), ("f", '""'), ("n", col_e[4]), ("n", 0.5), ("n", -100.0), ("n", 100.0), ("e", "#N/A"), ("b", True), ("s", "5")]
    return special + [("n", round(rng.uniform(-4.9, 4.9), 4)) for _ in range(40)]


def build_iferror(path):
    """The original formula (5 digits) and the rewrites A (TRUNC(PERCENTRANK(..,15),5)) and B (TRUNC((RANK-1)/(COUNT-1),5)), each
    wrapped in IFERROR(..,-1) (columns A-C) and bare (D-F), for every x variant. Range = a 533-cell column with negatives and ties."""
    vals = shape_columns()["signed"]
    data, probe = {f"A{i + 1}": ("n", v) for i, v in enumerate(vals)}, {}
    rng = "Data!$A$1:$A$533"
    for r, x in enumerate(iferror_xs(), start=1):
        if x is not None:
            data[f"C{r}"] = x
        ref = f"Data!C{r}"
        forms = (f"PERCENTRANK({rng},{ref},5)", f"TRUNC(PERCENTRANK({rng},{ref},15),5)", f"TRUNC((RANK({ref},{rng},1)-1)/(COUNT({rng})-1),5)")
        for j, f in enumerate(forms):
            probe[f"{col(j + 1)}{r}"] = ("f", f"IFERROR({f},-1)")
            probe[f"{col(j + 4)}{r}"] = ("f", f)
    return write_book(path, data, probe)


# ------------------------------------------------------------------------------------------ AVERAGE model and cases

NA, DIV0 = ("e", "#N/A"), ("e", "#DIV/0!")


def avg_cases():
    """(id, what, {column letter placeholder: cells}, formula with {C}/{D}, Excel's value). None = blank cell."""
    n, s, b, e, f = (lambda v: ("n", v)), (lambda v: ("s", v)), (lambda v: ("b", v)), (lambda v: ("e", v)), (lambda v: ("f", v))
    return [
        ("numbers", "plain numbers", {"C": [n(1.0), n(2.0), n(3.0), n(4.0)]}, "AVERAGE(Data!{C}1:{C}4)", ("n", 2.5)),
        ("zero_counts", "a zero is a number", {"C": [n(0.0), n(2.0), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", ("n", 2.0)),
        ("blanks", "blank cells are ignored", {"C": [n(2.0), None, n(4.0), None, n(6.0)]}, "AVERAGE(Data!{C}1:{C}5)", ("n", 4.0)),
        ("text", "text in a range is ignored", {"C": [n(2.0), s("abc"), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", ("n", 3.0)),
        ("logical_constants", "TRUE/FALSE in a range are ignored", {"C": [n(2.0), b(True), n(4.0), b(False)]}, "AVERAGE(Data!{C}1:{C}4)", ("n", 3.0)),
        ("numeric_text", "numeric-looking text in a range is ignored", {"C": [n(2.0), s("10"), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", ("n", 3.0)),
        ("formula_empty_string", "a formula returning \"\" is text", {"C": [n(2.0), f('""'), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", ("n", 3.0)),
        ("formula_logical", "a formula returning TRUE is a logical", {"C": [n(2.0), f("1>0"), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", ("n", 3.0)),
        ("error_na", "an error propagates", {"C": [n(2.0), e("#N/A"), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", NA),
        ("error_div0", "an error propagates", {"C": [n(2.0), e("#DIV/0!"), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", DIV0),
        ("two_errors", "the first error in row order wins", {"C": [n(2.0), e("#N/A"), e("#DIV/0!")]}, "AVERAGE(Data!{C}1:{C}3)", NA),
        ("formula_error", "a formula error propagates", {"C": [n(2.0), f("1/0"), n(4.0)]}, "AVERAGE(Data!{C}1:{C}3)", DIV0),
        ("all_blank", "no numbers", {"C": [None, None]}, "AVERAGE(Data!{C}1:{C}2)", DIV0),
        ("all_text", "no numbers", {"C": [s("a"), s("b")]}, "AVERAGE(Data!{C}1:{C}2)", DIV0),
        ("direct_arguments", "typed TRUE and \"3\" are counted: (1+1+3)/3", {}, 'AVERAGE(1,TRUE,"3")', ("n", 5 / 3)),
        ("direct_bad_text", "typed text that is not a number", {}, 'AVERAGE(1,"abc")', ("e", "#VALUE!")),
        ("array_constant", "logicals and text inside an array are ignored", {}, 'AVERAGE({1,2,TRUE,"x"})', ("n", 1.5)),
        ("two_areas", "two ranges, text ignored", {"C": [n(1.0), n(3.0)], "D": [n(5.0), s("t")]}, "AVERAGE(Data!{C}1:{C}2,Data!{D}1:{D}2)", ("n", 3.0)),
        ("single_ref_logical", "one cell reference holding TRUE", {"C": [b(True)]}, "AVERAGE(Data!{C}1)", DIV0),
        ("single_ref_numeric_text", "one cell reference holding \"5\"", {"C": [s("5")]}, "AVERAGE(Data!{C}1)", DIV0),
        ("sum_order", "[1e16,1,-1e16]: a plain running sum loses the 1 (MODEL, not an Excel run)", {"C": [n(1e16), n(1.0), n(-1e16)]},
         "AVERAGE(Data!{C}1:{C}3)", ("n", 0.0)),
        ("sum_tenths", "[0.1,0.2,0.3]: plain running sum (MODEL, not an Excel run)", {"C": [n(0.1), n(0.2), n(0.3)]},
         "AVERAGE(Data!{C}1:{C}3)", ("n", (0.1 + 0.2 + 0.3) / 3)),
    ]


def build_average(path):
    data, probe = {}, {}
    for k, (_id, _what, cols, formula, _want) in enumerate(avg_cases()):
        letters = {"C": col(2 * k + 1), "D": col(2 * k + 2)}
        for key, cells in cols.items():
            for i, spec in enumerate(cells):
                if spec is not None:
                    data[f"{letters[key]}{i + 1}"] = spec
        probe[f"A{k + 1}"] = ("f", formula.replace("{C}", letters["C"]).replace("{D}", letters["D"]))   # not .format: array constants hold braces
    return write_book(path, data, probe)


def avg_shape_inputs(seed=SEED, n=533):
    rng = random.Random(seed + 2)
    return [[rng.random() for _ in range(n)] for _ in range(4)], (0.3, 0.25, 0.25, 0.2)


def build_avg_shape(path):
    """'Blended Model'!S3:S535 = $AB$2*P+$AB$3*I+$AB$4*R+$AB$5*M (the real formula's shape), 'Coalition Profiles'!C29 = AVERAGE(S)."""
    (p, i, r, m), w = avg_shape_inputs()
    data, probe = {}, {}
    for k, wk in enumerate(w):
        data[f"AB{k + 2}"] = ("n", wk)
    for row in range(533):
        for letter, vals in (("P", p), ("I", i), ("R", r), ("M", m)):
            data[f"{letter}{row + 3}"] = ("n", vals[row])
        data[f"S{row + 3}"] = ("f", f"$AB$2*P{row + 3}+$AB$3*I{row + 3}+$AB$4*R{row + 3}+$AB$5*M{row + 3}")
    probe["C29"] = ("f", "AVERAGE('Blended Model'!$S$3:$S$535)")
    return write_book(path, data, probe, names=("Blended Model", "Coalition Profiles"))


# ------------------------------------------------------------------------------------------ container

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def container_recalc(image, src, out_dir, run=subprocess.run):
    """One office-calc job through the compose file (network none, read-only root, cap_drop ALL), like the CI smoke test."""
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    flags = [x for fn in ALLOW for x in ("--allow-function", fn)]
    env = {**os.environ, "IMAGE_OFFICE_CALC": image, "OFFICE_IN_DIR": str(src.parent.resolve()), "OFFICE_OUT_DIR": str(out_dir.resolve()),
           "MSYS_NO_PATHCONV": "1"}
    stem = src.stem
    proc = run(["docker", "compose", "-f", str(COMPOSE), "--profile", "office", "run", "-T", "--rm", "office-calc",
                "--in", f"/in/{src.name}", "--expect-sha", sha256(src), "--out", f"/out/{stem}.view.xlsx",
                "--receipt", f"/out/{stem}.receipt.json", *flags], env=env, capture_output=True, text=True)
    return proc.returncode, proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""


# ------------------------------------------------------------------------------------------ judging

def values(view, sheet):
    return OR.read_cached_formula_values(view)[sheet]


def judge_percentrank(view):
    got, cases, rows = values(view, "Probe"), pr_cases(), []
    for k, c in enumerate(cases):
        e = pr_exact(c["vals"], c["x"], c["sig"] or DEFAULT_SIG)
        lo = got[f"A{k + 1}"][:2]
        if e is None:
            verdict = "PASS" if lo == ("e", "#N/A") else "DIVERGENCE"
            want = ("e", "#N/A")
        else:
            want = ("n", e["trunc"])
            verdict = ("TRUNCATES" if lo == ("n", e["trunc"]) else "ROUNDS" if lo == ("n", e["round"]) else
                       "ROUNDS_SIG" if lo == ("n", e["round_sig"]) else "OTHER") if e["differ"] else (
                "PASS" if lo == ("n", e["trunc"]) else "ROUNDS_SIG" if lo == ("n", e["round_sig"]) else "OTHER")
        rows.append({"case": k + 1, "group": c["group"], "n": len(c["vals"]), "x": c["x"], "sig": c["sig"] or DEFAULT_SIG,
                     "sig_given": c["sig"] is not None, "excel_trunc": want[1], "excel_would_round_to": e["round"] if e else None,
                     "round_to_sig_digits": e["round_sig"] if e else None,
                     "differ": bool(e and e["differ"]), "lo": list(lo), "verdict": verdict})
    differ = [r for r in rows if r["differ"]]
    tally = {v: sum(1 for r in differ if r["verdict"] == v) for v in ("TRUNCATES", "ROUNDS", "ROUNDS_SIG", "OTHER")}
    return {"rows": rows, "differing_cases": len(differ), "tally": tally,
            "default_significance_cases": sum(1 for r in differ if not r["sig_given"]),
            "interpolated_cases": sum(1 for r in differ if r["group"] == "between"),
            "verdict": "TRUNCATES" if tally["TRUNCATES"] == len(differ) else
            "ROUNDS (decimal places for raw >= 0.1, significant digits below)" if tally["TRUNCATES"] == tally["OTHER"] == 0 else "MIXED/OTHER"}


def judge_pr_shape(view):
    got = values(view, "Probe")
    out = {}
    for j, (name, vals) in enumerate(shape_columns().items()):
        L = col(j + 1)
        nums = [v for v in vals if v is not None]
        c = {"cells": 0, "equal_authority": 0, "equal_exact_trunc": 0, "equal_round": 0, "equal_round_sig": 0, "other": 0,
             "differ_trunc_vs_round": 0, "rewrite_A_trunc_percentrank15": 0, "rewrite_B_trunc_rank_count": 0}
        for i, v in enumerate(vals):
            if v is None:
                continue
            lo = got[f"{L}{i + 1}"][:2]
            auth, rnd = pillars_percentrank(nums, v), pr_exact(nums, v, 5)
            c["cells"] += 1
            c["equal_authority"] += lo == ("n", auth)
            c["equal_exact_trunc"] += lo == ("n", rnd["trunc"])
            c["equal_round"] += lo == ("n", rnd["round"])
            c["differ_trunc_vs_round"] += rnd["differ"]
            c["equal_round_sig"] += lo == ("n", rnd["round_sig"])
            c["other"] += lo not in (("n", rnd["trunc"]), ("n", rnd["round"]), ("n", rnd["round_sig"]))
            c["rewrite_A_trunc_percentrank15"] += got[f"{col(j + 11)}{i + 1}"][:2] == ("n", auth)
            c["rewrite_B_trunc_rank_count"] += got[f"{col(j + 17)}{i + 1}"][:2] == ("n", auth)
        c["cells_that_differ_from_excel_model"] = c["cells"] - c["equal_authority"]
        out[name] = c
    out["iferror_shape"] = {"blank_x": got["Y1"][:2], "text_x": got["Y2"][:2], "empty_string_x": got["Y3"][:2],
                            "note": "fallback -1 means PERCENTRANK errored; Excel: blank x = 0, text and \"\" = #VALUE! (documented, not Excel-run)"}
    return out


AVG_FORMS = ("AVERAGE({r})", "SUM({r})/COUNT({r})", "SUMPRODUCT({r})/COUNT({r})", "SUMPRODUCT({r}*1)/COUNT({r})",
             'AVERAGEIF({r},">-1E+300")', 'SUMIFS({r},{r},">-1E+300")/COUNTIF({r},">-1E+300")')


def avg_draws(seed=SEED, draws=30, n=533):
    """`draws` independent 533-value columns, each the value shape of 'Blended Model'!S (weighted sum of four uniforms)."""
    out = []
    for d in range(draws):
        rng = random.Random(seed + 100 + d)
        w = (0.3, 0.25, 0.25, 0.2)
        out.append([w[0] * rng.random() + w[1] * rng.random() + w[2] * rng.random() + w[3] * rng.random() for _ in range(n)])
    return out


def build_avg_forms(path):
    """Row d of Probe = the same 533 values averaged six ways: AVERAGE and five rewrites (the last three use only allowlisted functions
    besides COUNT). Which rewrite, if any, gives Excel's AVERAGE bit for bit in LibreOffice?"""
    data, probe = {}, {}
    for d, vals in enumerate(avg_draws()):
        L = col(d + 1)
        for i, v in enumerate(vals):
            data[f"{L}{i + 1}"] = ("n", v)
        for j, form in enumerate(AVG_FORMS):
            probe[f"{col(j + 1)}{d + 1}"] = ("f", form.format(r=f"Data!${L}$1:${L}$533"))
    return write_book(path, data, probe)


def judge_average(view):
    got, rows = values(view, "Probe"), []
    for k, (cid, what, _cols, formula, want) in enumerate(avg_cases()):
        lo = got[f"A{k + 1}"][:2]
        status = "PASS" if lo == want else "DIVERGENCE"
        ulp = X.ulp_distance(lo[1], want[1]) if lo[0] == "n" and want[0] == "n" and status != "PASS" else None
        rows.append({"id": cid, "what": what, "formula": formula.replace("{C}", "?").replace("{D}", "?"), "excel": list(want), "lo": list(lo),
                     "status": status, **({"ulp": ulp} if ulp is not None else {})})
    return rows


def judge_avg_shape(view):
    s = values(view, "Blended Model")
    (p, i, r, m), w = avg_shape_inputs()
    py = [w[0] * p[k] + w[1] * i[k] + w[2] * r[k] + w[3] * m[k] for k in range(533)]
    lo_s = [s[f"S{k + 3}"][1] for k in range(533)]
    lo = values(view, "Coalition Profiles")["C29"]
    plain = 0.0
    for v in lo_s:
        plain += v
    ref = {"running_sum/n": plain / 533, "fsum/n": math.fsum(lo_s) / 533, "statistics.fmean": statistics.fmean(lo_s)}
    return {"S_cells": 533, "S_kinds": sorted({v[0] for v in s.values()}), "S_equal_python": sum(a == b for a, b in zip(lo_s, py)),
            "C29": list(lo[:2]), "ulp_vs": {k: X.ulp_distance(lo[1], v) for k, v in ref.items()}}


def judge_determinism(out_a, out_b):
    rows = {}
    for name in BOOKS:
        a, b = Path(out_a) / f"{name}.view.xlsx", Path(out_b) / f"{name}.view.xlsx"
        if a.is_file() and b.is_file():
            ra = {k: v for k, v in json.loads((Path(out_a) / f"{name}.receipt.json").read_text()).items() if k not in ("generated_at", "started_at", "temporary_copy_after")}
            rb = {k: v for k, v in json.loads((Path(out_b) / f"{name}.receipt.json").read_text()).items() if k not in ("generated_at", "started_at", "temporary_copy_after")}
            rows[name] = {"view_a": sha256(a), "view_b": sha256(b), "byte_identical": a.read_bytes() == b.read_bytes(),
                          "receipts_equal_apart_from_times_and_view_hash": ra == rb}
    return rows


def check(root):
    root = Path(root)
    out = root / "out"
    report = {"percentrank": judge_percentrank(out / "percentrank.view.xlsx"), "pr_shape": judge_pr_shape(out / "pr_shape.view.xlsx"),
              "average": judge_average(out / "average.view.xlsx"), "avg_shape": judge_avg_shape(out / "avg_shape.view.xlsx")}
    if (root / "out2").is_dir():
        report["determinism"] = judge_determinism(out, root / "out2")
    (root / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


# ------------------------------------------------------------------------------------------ the Excel oracle

def excel_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"EXCEL.EXE"')}


def run_excel(root, timeout=150):
    """xl_equivalence.excel_recalculate on each probe workbook, one process each (DispatchEx: its own Excel instance, alerts off,
    quit at the end). On a timeout only the Excel processes that were not running before THIS call are killed."""
    root, before, report = Path(root), excel_pids(), {}
    (root / "excel_out").mkdir(exist_ok=True)
    flags = [x for fn in ALLOW for x in ("--allow-function", fn)]
    for name in BOOKS:
        cmd = [sys.executable, "-B", str(HERE / "xl_equivalence.py"), "excel-reference", "--in", str((root / "in" / f"{name}.xlsx").resolve()),
               "--out", str((root / "excel_out" / f"{name}.xlsx").resolve()), *flags]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            report[name] = {"exit": proc.returncode, "error": proc.stderr.strip().splitlines()[-1:] if proc.returncode else None}
        except subprocess.TimeoutExpired:
            mine = excel_pids() - before
            for pid in mine:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            report[name] = {"exit": "timeout", "killed_own_excel_pids": sorted(mine)}
            break
    for _ in range(20):                                              # Excel needs a moment to exit after Quit()
        if not excel_pids() - before:
            break
        time.sleep(0.5)
    report["excel_processes_of_mine_left"] = sorted(excel_pids() - before)
    return report


def _v(rows, addr):
    return tuple(rows[addr][:2]) if addr in rows else ("missing", None)


def oracle(root):
    """Excel's cached values (excel_out) against the models and against the LibreOffice view copies (out)."""
    root = Path(root)
    X_ = {n: OR.read_cached_formula_values(root / "excel_out" / f"{n}.xlsx") for n in BOOKS}
    L_ = {n: OR.read_cached_formula_values(root / "out" / f"{n}.view.xlsx") for n in BOOKS}
    rep = {}
    # 1a. PERCENTRANK cases: Excel against the truncation model, and against LibreOffice
    rows, ex, lo = [], X_["percentrank"]["Probe"], L_["percentrank"]["Probe"]
    for k, c in enumerate(pr_cases()):
        e = pr_exact(c["vals"], c["x"], c["sig"] or DEFAULT_SIG)
        want = ("e", "#N/A") if e is None else ("n", e["trunc"])
        rows.append({"case": k + 1, "group": c["group"], "n": len(c["vals"]), "x": c["x"], "sig": c["sig"] or DEFAULT_SIG,
                     "sig_given": c["sig"] is not None, "model_trunc": want[1], "excel": list(_v(ex, f"A{k + 1}")),
                     "lo": list(_v(lo, f"A{k + 1}")), "differ": bool(e and e["differ"]),
                     "excel_equals_model": _v(ex, f"A{k + 1}") == want, "excel_equals_lo": _v(ex, f"A{k + 1}") == _v(lo, f"A{k + 1}")})
    rep["percentrank_cases"] = {"cases": len(rows), "excel_equals_model": sum(r["excel_equals_model"] for r in rows),
                                "differing_cases": sum(r["differ"] for r in rows),
                                "differing_excel_equals_model": sum(r["excel_equals_model"] for r in rows if r["differ"]),
                                "excel_equals_lo": sum(r["excel_equals_lo"] for r in rows),
                                "not_equal_model": [r for r in rows if not r["excel_equals_model"]]}
    # 1b. the 533-row shapes: Excel vs the Python authority and vs LO; rewrites A and B against the ORIGINAL formula, in Excel
    xs, ls, shape = X_["pr_shape"]["Probe"], L_["pr_shape"]["Probe"], {}
    for j, (name, vals) in enumerate(shape_columns().items()):
        nums = [v for v in vals if v is not None]
        c = dict.fromkeys(("cells", "excel_equals_authority", "excel_equals_lo", "A_excel_equals_orig_excel", "B_excel_equals_orig_excel",
                           "A_lo_equals_orig_excel", "B_lo_equals_orig_excel"), 0)
        for i, v in enumerate(vals):
            if v is None:
                continue
            o, a, b = (f"{col(j + d)}{i + 1}" for d in (1, 11, 17))
            c["cells"] += 1
            c["excel_equals_authority"] += _v(xs, o) == ("n", pillars_percentrank(nums, v))
            c["excel_equals_lo"] += _v(xs, o) == _v(ls, o)
            c["A_excel_equals_orig_excel"] += _v(xs, a) == _v(xs, o)
            c["B_excel_equals_orig_excel"] += _v(xs, b) == _v(xs, o)
            c["A_lo_equals_orig_excel"] += _v(ls, a) == _v(xs, o)
            c["B_lo_equals_orig_excel"] += _v(ls, b) == _v(xs, o)
        shape[name] = c
    shape["blank_text_empty_x (original, IFERROR -1)"] = {"excel": [list(_v(xs, a)) for a in ("Y1", "Y2", "Y3")], "lo": [list(_v(ls, a)) for a in ("Y1", "Y2", "Y3")]}
    rep["pr_shape"] = shape
    # 1c. IFERROR shape with every x variant: orig / A / B, wrapped and bare
    xi, li, ir = X_["iferror"]["Probe"], L_["iferror"]["Probe"], []
    for r, spec in enumerate(iferror_xs(), start=1):
        kind = IFERR_SPECIAL[r - 1] if r <= len(IFERR_SPECIAL) else "interpolated_random"
        ir.append({"row": r, "x_kind": kind, "x": spec[1] if spec else None,
                   "excel_wrapped": [list(_v(xi, f"{col(j)}{r}")) for j in (1, 2, 3)], "lo_wrapped": [list(_v(li, f"{col(j)}{r}")) for j in (1, 2, 3)],
                   "excel_bare": [list(_v(xi, f"{col(j)}{r}")) for j in (4, 5, 6)], "lo_bare": [list(_v(li, f"{col(j)}{r}")) for j in (4, 5, 6)]})
    same = lambda r, key, i: r[key][i] == r[key][0]                                                           # noqa: E731
    rep["iferror"] = {"rows": ir, "rows_total": len(ir),
                      "A_unmoved_wrapped_excel": sum(same(r, "excel_wrapped", 1) for r in ir), "B_unmoved_wrapped_excel": sum(same(r, "excel_wrapped", 2) for r in ir),
                      "A_unmoved_bare_excel": sum(same(r, "excel_bare", 1) for r in ir), "B_unmoved_bare_excel": sum(same(r, "excel_bare", 2) for r in ir),
                      "orig_excel_equals_lo_wrapped": sum(r["excel_wrapped"][0] == r["lo_wrapped"][0] for r in ir),
                      "A_lo_equals_orig_excel_wrapped": sum(r["lo_wrapped"][1] == r["excel_wrapped"][0] for r in ir),
                      "B_lo_equals_orig_excel_wrapped": sum(r["lo_wrapped"][2] == r["excel_wrapped"][0] for r in ir)}
    # 2a. AVERAGE cases
    ex, lo, rows = X_["average"]["Probe"], L_["average"]["Probe"], []
    for k, (cid, what, _c, _f, want) in enumerate(avg_cases()):
        a, b = _v(ex, f"A{k + 1}"), _v(lo, f"A{k + 1}")
        rows.append({"id": cid, "documented_or_model": list(want), "excel": list(a), "lo": list(b), "excel_equals_documented": a == want,
                     "excel_equals_lo": a == b, **({"ulp_excel_vs_lo": X.ulp_distance(a[1], b[1])} if a[0] == b[0] == "n" and a != b else {})})
    rep["average"] = rows
    # 2b. the S3:S535-shaped range
    xs_, ls_ = X_["avg_shape"]["Blended Model"], L_["avg_shape"]["Blended Model"]
    s_x = [xs_[f"S{k + 3}"][1] for k in range(533)]
    cx, cl = _v(X_["avg_shape"]["Coalition Profiles"], "C29"), _v(L_["avg_shape"]["Coalition Profiles"], "C29")
    running = 0.0
    for v in s_x:
        running += v
    rep["avg_shape"] = {"S_cells_excel_equal_lo": sum(xs_[f"S{k + 3}"][:2] == ls_[f"S{k + 3}"][:2] for k in range(533)),
                        "C29_excel": cx[1], "C29_lo": cl[1], "ulp_excel_vs_lo": X.ulp_distance(cx[1], cl[1]),
                        "excel_ulp_vs": {"running_sum/n": X.ulp_distance(cx[1], running / 533), "fsum/n": X.ulp_distance(cx[1], math.fsum(s_x) / 533)},
                        "C29_excel_hex": cx[1].hex(), "C29_lo_hex": cl[1].hex()}
    # 2c. AVERAGE and rewrites over 30 independent 533-value columns
    xf, lf, forms = X_["avg_forms"]["Probe"], L_["avg_forms"]["Probe"], {}
    for j, form in enumerate(AVG_FORMS):
        ulps = [X.ulp_distance(_v(lf, f"{col(j + 1)}{d + 1}")[1], _v(xf, f"A{d + 1}")[1]) for d in range(30)]
        forms[form] = {"excel_equals_excel_AVERAGE": sum(_v(xf, f"{col(j + 1)}{d + 1}") == _v(xf, f"A{d + 1}") for d in range(30)),
                       "lo_equals_excel_AVERAGE": sum(u == 0 for u in ulps), "lo_vs_excel_AVERAGE_ulp_max": max(ulps),
                       "lo_vs_excel_AVERAGE_ulp_median": sorted(ulps)[15], "draws": 30}
    rep["avg_forms"] = forms
    # 3. the lane's comparator (tiers), Excel as the reference and the LibreOffice view copy as the candidate
    cmp_ = {}
    for n in BOOKS:
        merged = X.merge_formulas(X_[n], L_[n])
        r = X.compare(X_[n], L_[n], X.make_tier0(merged, {}))
        cmp_[n] = {"equivalent": r["equivalent"], "formulas": r["totals"]["formulas_reference"], "problems": r["problems"],
                   "first_differences": r["first_differences"][:3]}
    rep["comparator_excel_vs_lo"] = cmp_
    (root / "report_excel.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return rep


def make(root):
    inp = Path(root) / "in"
    inp.mkdir(parents=True, exist_ok=True)
    return {n: build(inp / f"{n}.xlsx") for n, build in zip(BOOKS, (build_percentrank, build_pr_shape, build_average, build_avg_shape, build_iferror, build_avg_forms))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["make", "run", "check", "excel", "oracle"])
    ap.add_argument("dir", type=Path)
    ap.add_argument("image", nargs="?", help="office-calc image (tag or digest) for `run`")
    a = ap.parse_args(argv)
    if a.cmd in ("make", "run"):
        books = make(a.dir)
        print(json.dumps({n: sha256(p) for n, p in books.items()}))
    if a.cmd == "run":
        if not a.image:
            raise SystemExit("run needs the image")
        for out in ("out", "out2"):                                    # the second pass is the determinism check
            for name in BOOKS:
                rc, msg = container_recalc(a.image, a.dir / "in" / f"{name}.xlsx", a.dir / out)
                print(f"{out}/{name}: exit {rc} {msg if rc else ''}")
                if rc and out == "out":
                    return rc
    if a.cmd in ("run", "check"):
        print(json.dumps(check(a.dir), indent=1, sort_keys=True, default=str))
    if a.cmd == "excel":
        report = run_excel(a.dir)
        print(json.dumps(report, indent=1))
        return 0 if all(v.get("exit") == 0 for v in report.values() if isinstance(v, dict)) and not report["excel_processes_of_mine_left"] else 1
    if a.cmd == "oracle":
        print(json.dumps(oracle(a.dir), indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
