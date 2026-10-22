"""xl_equivalence (D-174 gate, D-177 tolerance): ULP arithmetic, the exact/ULP tiers, the comparator's failure modes, the
negative control, the probe's known values. The Excel reference path is exercised on a tiny synthetic workbook only, and
only where Excel exists and XL_TEST_EXCEL=1 (it starts Excel; ~15-30 s per recalculation). Run it with `-p no:faulthandler`:
Excel's Quit makes COM raise a handled RPC_E_DISCONNECTED (0x80010108) that pytest's faulthandler prints as 'fatal'."""
import json
import math
import os
import re
import sys
import zipfile
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS / "compute"))
sys.path.insert(0, str(OPS.parent / "fec"))
import office_recalc as OR  # noqa: E402
import roster  # noqa: E402
import xl_equivalence as X  # noqa: E402

ALLOW = ["_XLFN.XLOOKUP"]
HAVE_EXCEL = False
if sys.platform == "win32" and os.environ.get("XL_TEST_EXCEL") == "1":
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Excel.Application"))
        HAVE_EXCEL = True
    except OSError:
        pass
needs_excel = pytest.mark.skipif(not HAVE_EXCEL, reason="set XL_TEST_EXCEL=1 on a Windows box with Excel")


def cells(**sheets):
    """{sheet: {addr: (kind, value, formula)}} from {sheet: {addr: (kind, value[, formula])}}."""
    return {s: {a: (v + ("",))[:3] if len(v) == 2 else v for a, v in rows.items()} for s, rows in sheets.items()}


def inject_probe(src, out, results=None):
    """A view copy of the probe holding the known values (what a correct calculator would write)."""
    results = results or {"Data": {}, "Probe": {c: (k, v) for c, _f, k, v, _lo in X.PROBE}}

    def calc(copy, cells_, **kw):
        return results, {"product": "x", "version": "1"}
    import hashlib
    OR.run(src, hashlib.sha256(Path(src).read_bytes()).hexdigest(), out, None, allow=ALLOW, calculator=calc)
    return out


@pytest.fixture
def probe(tmp_path):
    p = tmp_path / "probe.xlsx"
    X.write_probe(p)
    return p


# ---------------------------------------------------------------------------------------------------- ULP

def test_ulp_distance():
    assert X.ulp_distance(1.0, 1.0) == 0 and X.ulp_distance(0.0, -0.0) == 0
    assert X.ulp_distance(1.0, math.nextafter(1.0, 2.0)) == 1
    assert X.ulp_distance(0.1 + 0.2, 0.3) == 1                                  # the classic: one ULP
    assert X.ulp_distance(-1.0, math.nextafter(-1.0, -2.0)) == 1
    x = 1.0
    for _ in range(4):
        x = math.nextafter(x, 2.0)
    assert X.ulp_distance(1.0, x) == 4
    assert X.ulp_distance(-5e-324, 5e-324) == 2                                 # across zero, through both zeros' neighbours
    assert X.ulp_distance(5.551115123125783e-17, 0.0) > 4                       # a near-zero difference is NOT within 4 ULP


# ---------------------------------------------------------------------------------------------------- tiers

def test_discrete_formulas_and_precedent_columns():
    assert not X.is_discrete("SUM(A1:A3)*2") and not X.is_discrete('"a>b"&A1')
    assert all(X.is_discrete(f) for f in ("IF(A1>0,1,0)", "RANK(A1,A1:A3)", "A1<B1", "_xlfn.XLOOKUP(1,A:A,B:B)", "COUNTIFS(A:A,\">1\")",
                                          "PERCENTRANK($D$3:$D$535,D3,5)", "_xlfn.LET(_xlpm.x,A1,IF(_xlpm.x=0,0,1))"))
    tables = {"Table10": ("Rank", 2, ["Name", "Total Giving", "Rank"])}
    got = X.precedent_columns("S", "SUM(Data!$A$1:$B$9)+'My Sheet'!C3+D4+LOG10(E5)+Table10[[#This Row],[Total Giving]]+F:G", tables)
    assert got == {("Data", 1), ("Data", 2), ("My Sheet", 3), ("S", 4), ("S", 5), ("Rank", 3), ("S", 6), ("S", 7)}
    assert X.precedent_columns("S", "LOG10(2)+ATAN2(1,2)", tables) == set()               # a function name is not a cell


def test_only_continuous_cells_that_touch_no_decision_are_tier_one():
    v = cells(S={"B1": ("n", 1.0, "SUM(A1:A3)"),                 # feeds the IF below -> exact
                 "C1": ("n", 1.0, "IF(B1>0,1,0)"),               # decides -> exact
                 "D1": ("n", 2.0, "SUM(A1:A3)*2"),               # continuous, feeds nothing discrete -> tier 1
                 "E1": ("n", 3.0, "RANK(A1,A1:A3)"),             # decides -> exact
                 "F1": ("n", 4.0, "E1*0.5")})                    # downstream of a decision -> exact
    t0 = X.make_tier0(v, {})
    assert [a for a in v["S"] if t0("S", a)] == ["B1", "C1", "E1", "F1"] and not t0("S", "D1")
    t0 = X.make_tier0(v, {}, ["S!D1:D1"])
    assert t0("S", "D1")                                         # --exact-range forces tier 0
    with pytest.raises(SystemExit):
        X.parse_ranges(["no-sheet"])


# ---------------------------------------------------------------------------------------------------- compare

def ulps(x, n):
    for _ in range(n):
        x = math.nextafter(x, math.inf)
    return x


def test_compare_numbers_by_tier():
    ref = cells(S={"D1": ("n", 1.0, "SUM(A1:A3)*2"), "C1": ("n", 1.0, "IF(D1>0,1,0)"), "G1": ("n", 7.0, "SUM(B1:B3)")})
    t0 = X.make_tier0(ref, {})
    assert t0("S", "C1") and t0("S", "D1") and not t0("S", "G1")
    same = X.compare(ref, ref, t0)
    assert same["equivalent"] and same["totals"]["exact"] == 3
    near = cells(S={"D1": ("n", 1.0, "x"), "C1": ("n", 1.0, "x"), "G1": ("n", ulps(7.0, 4), "x")})
    r = X.compare(ref, near, t0)
    assert r["equivalent"] and r["totals"]["within_ulp"] == 1                       # 4 ULP in tier 1 is fine
    far = cells(S={"D1": ("n", 1.0, "x"), "C1": ("n", 1.0, "x"), "G1": ("n", ulps(7.0, 5), "x")})
    r = X.compare(ref, far, t0)
    assert not r["equivalent"] and r["first_differences"][0]["why"] == "tier1_number" and r["first_differences"][0]["ulp"] == 5
    one = cells(S={"D1": ("n", ulps(1.0, 1), "x"), "C1": ("n", 1.0, "x"), "G1": ("n", 7.0, "x")})
    r = X.compare(ref, one, t0)
    assert not r["equivalent"] and r["first_differences"][0] == {
        "sheet": "S", "cell": "D1", "why": "tier0_number", "reference": ("n", 1.0), "candidate": ("n", ulps(1.0, 1)),
        "formula": "SUM(A1:A3)*2", "ulp": 1}                                       # one ULP in a decision's input is a failure
    assert X.compare(ref, near, t0, ulp=3)["equivalent"] is False                  # the limit is configurable


def test_shared_formula_children_take_their_text_from_the_other_side():
    # Excel saves C2:C3 as a shared formula: only C1 holds the text. The view copy keeps the explicit text of every cell.
    excel = cells(S={"C1": ("n", 1.0, "RANK(A1,A1:A3)"), "C2": ("n", 2.0, ""), "C3": ("n", 3.0, ""), "D2": ("n", 5.0, "")})
    view = cells(S={"C1": ("n", 1.0, "RANK(A1,A1:A3)"), "C2": ("n", 2.0, "RANK(A2,A1:A3)"), "C3": ("n", 3.0, "RANK(A3,A1:A3)"),
                    "D2": ("n", 5.0, "SUM(A1:A3)*2")})
    merged = X.merge_formulas(excel, view)
    assert [merged["S"][a][2] for a in ("C1", "C2", "C3", "D2")] == ["RANK(A1,A1:A3)", "RANK(A2,A1:A3)", "RANK(A3,A1:A3)", "SUM(A1:A3)*2"]
    t0 = X.make_tier0(merged, {})
    assert t0("S", "C2") and t0("S", "C3") and not t0("S", "D2")                  # the children decide too; D2 is continuous
    off = cells(S={"C1": ("n", 1.0, ""), "C2": ("n", ulps(2.0, 1), ""), "C3": ("n", 3.0, ""), "D2": ("n", ulps(5.0, 1), "")})
    r = X.compare(excel, off, t0)
    assert [(d["cell"], d["why"]) for d in r["first_differences"]] == [("C2", "tier0_number")]   # one ULP in a rank column fails
    assert r["totals"]["within_ulp"] == 1
    off_view = cells(S={**view["S"], "C2": ("n", ulps(2.0, 1), "RANK(A2,A1:A3)")})
    assert X.compare(excel, off_view, t0)["first_differences"][0]["formula"] == "RANK(A2,A1:A3)"   # the report shows the text
    # a cell present on one side only still gets its tier from the text on that side
    assert "E1" in X.merge_formulas(excel, cells(S={"E1": ("n", 1.0, "IF(A1>0,1,0)")}))["S"]


def test_compare_text_booleans_errors_types_and_census():
    nothing = lambda s, a: False                                                    # noqa: E731
    ref = cells(S={"A1": ("str", "ok", "f"), "A2": ("b", True, "f"), "A3": ("e", "#N/A", "f"), "A4": ("n", 1.0, "f")})
    assert X.compare(ref, ref, nothing)["equivalent"]
    for addr, bad, why in (("A1", ("str", "OK"), "str_value"), ("A2", ("b", False), "b_value"), ("A3", ("e", "#VALUE!"), "e_value"),
                           ("A4", ("b", True), "type"), ("A4", ("missing", None), "no_cached_value")):
        cand = {"S": {**ref["S"], addr: (*bad, "f")}}
        r = X.compare(ref, cand, nothing)
        assert not r["equivalent"] and r["first_differences"][0]["why"] == why, (addr, why)
    r = X.compare(ref, {"S": {a: v for a, v in ref["S"].items() if a != "A4"}}, nothing)
    assert not r["equivalent"] and r["first_differences"][0]["why"] == "only_in_reference"
    r = X.compare(ref, {"S": {**ref["S"], "A5": ("n", 1.0, "f")}}, nothing)
    assert not r["equivalent"] and "census" in " ".join(r["problems"])
    assert not X.compare({}, {}, nothing)["equivalent"] and "vacuous" in X.compare({}, {}, nothing)["problems"][0]
    assert not X.compare(ref, ref, nothing, expect_formulas=5)["equivalent"] and X.compare(ref, ref, nothing, expect_formulas=4)["equivalent"]


# ---------------------------------------------------------------------------------------------------- the C29 allowance (D-177)

CP = "Coalition Profiles"
MEAN = "AVERAGE('Blended Model'!$S$3:$S$535)"
C29_EXCEL = 0.4826714766094412                                                  # lo_probe_record_20261012.json avg_shape


def coalition(c29=C29_EXCEL, **extra):
    """'Coalition Profiles' in miniature: the mean in C29, a decision in the same column (so the column is tier 0), a continuous
    cell beside it, and the same shape on another sheet."""
    return cells(**{CP: {"C28": ("n", 0.5, "IFERROR(AVERAGEIF(A1:A9,B1,S1:S9),0)"), "C29": ("n", c29, MEAN), "D29": ("n", 7.0, "SUM(D18:D28)"),
                        **extra},
                    "Other": {"C28": ("n", 0.5, "IF(A1>0,1,0)"), "C29": ("n", C29_EXCEL, MEAN)}})


def test_c29_has_an_explicit_per_cell_allowance_and_it_is_the_only_one():
    assert X.CELL_ULP == {(CP, "C29"): 16}
    ref = coalition()
    t0 = X.make_tier0(ref, {})
    assert t0(CP, "C29") and not t0(CP, "D29")                                  # why it needs one: its column decides things
    for n, ok in ((0, True), (1, True), (8, True), (16, True), (17, False), (64, False)):
        r = X.compare(ref, coalition(ulps(C29_EXCEL, n)), t0)
        assert r["equivalent"] is ok, n
        if not ok:
            assert r["first_differences"] == [{"sheet": CP, "cell": "C29", "why": "cell_allowance_number", "reference": ("n", C29_EXCEL),
                                               "candidate": ("n", ulps(C29_EXCEL, n)), "formula": MEAN, "ulp": n}]
        else:
            assert r["totals"].get("within_cell_ulp", 0) == (1 if n else 0) and r["cell_ulp"] == {f"{CP}!C29": 16}
    assert X.compare(ref, coalition(ulps(C29_EXCEL, 8)), t0, cell_ulp={})["first_differences"][0]["why"] == "tier0_number"   # no allowance, no pass
    assert X.compare(ref, coalition(ulps(C29_EXCEL, 20)), t0, cell_ulp={(CP, "C29"): 32})["equivalent"]                  # the table is the knob


def test_the_allowance_changes_the_rule_for_that_one_cell_and_nothing_around_it():
    ref = coalition()
    t0 = X.make_tier0(ref, {})
    one = ulps(0.5, 1)
    for sheet, addr, value, why in ((CP, "C28", one, "tier0_number"),                       # the neighbour in the same column: exact
                                    ("Other", "C29", ulps(C29_EXCEL, 1), "tier0_number"),    # the same address on another sheet: exact
                                    ("Other", "C28", one, "tier0_number"),
                                    (CP, "D29", ulps(7.0, 5), "tier1_number")):              # tier 1 keeps its 4 ULP
        cand = coalition()
        cand[sheet][addr] = (*("n", value), ref[sheet][addr][2])
        r = X.compare(ref, cand, t0)
        assert not r["equivalent"] and r["first_differences"][0]["cell"] == addr and r["first_differences"][0]["why"] == why, (sheet, addr)
    near = coalition()
    near[CP]["D29"] = ("n", ulps(7.0, 4), "x")
    assert X.compare(ref, near, t0)["equivalent"] and X.compare(ref, near, t0)["totals"]["within_ulp"] == 1
    # the allowance is about the NUMBER: any other divergence of C29 still fails
    for bad, why in ((("e", "#DIV/0!"), "type"), (("str", "0.48"), "type"), (("missing", None), "no_cached_value")):
        cand = coalition()
        cand[CP]["C29"] = (*bad, MEAN)
        r = X.compare(ref, cand, t0)
        assert not r["equivalent"] and r["first_differences"][0]["why"] == why, bad
    gone = coalition()
    del gone[CP]["C29"]
    assert not X.compare(ref, gone, t0)["equivalent"]


@pytest.mark.parametrize("reader,home", [
    ("C29*2", CP), ("SUM(C20:C40)", CP), ("SUM(C:C)", CP), ("SUM(29:29)", CP), ("$C$29+1", CP), ("INDEX(C1:C100,3)", CP),
    ("'Coalition Profiles'!C29", "Other"), ("SUM('Coalition Profiles'!$C$1:$C$99)", "Other"), ("SUM('Coalition Profiles'!C:D)", "Other")])
def test_the_allowance_is_void_once_a_formula_reads_the_cell(reader, home):
    ref = coalition()
    ref[home]["Z1"] = ("n", 1.0, reader)
    t0 = X.make_tier0(ref, {})
    cand = coalition(ulps(C29_EXCEL, 8))
    cand[home]["Z1"] = ("n", 1.0, reader)
    r = X.compare(ref, cand, t0)
    assert not r["equivalent"] and f"the allowance for {CP}!C29 is void: it is read by {home}!Z1" in r["problems"]
    assert r["first_differences"][0]["why"] == "tier0_number"                           # back under the column's exact rule
    same = X.compare(ref, {**ref}, t0)                                                  # an equal C29 never needed the allowance
    assert same["equivalent"] and same["problems"] == []


@pytest.mark.parametrize("not_a_reader,home", [
    ("C28*2", CP), ("SUM(C1:C28)", CP), ("SUM(C30:C40)", CP), ("D29+1", CP), ("SUM(D:D)", CP), ("SUM(30:30)", CP), ("Other!C29*2", CP),
    ("C29*2", "Other"), ('"C29"&"x"', CP), ("LOG10(2)", CP), ("'Blended Model'!C29", CP)])
def test_formulas_that_read_something_else_leave_the_allowance_alone(not_a_reader, home):
    ref = coalition()
    ref[home]["Z1"] = ("n", 1.0, not_a_reader)
    cand = coalition(ulps(C29_EXCEL, 8))
    cand[home]["Z1"] = ("n", 1.0, not_a_reader)
    r = X.compare(ref, cand, X.make_tier0(ref, {}))
    assert r["equivalent"] and r["problems"] == [] and r["totals"]["within_cell_ulp"] == 1, r["problems"]


def test_compare_cli_applies_the_allowance_and_the_flag_turns_it_off(monkeypatch, capsys):
    """Excel against LibreOffice gets the C29 allowance; Excel against Excel (the before/after of a formula rewrite) must be identical."""
    books = {"ref.xlsx": coalition(), "near.xlsx": coalition(ulps(C29_EXCEL, 8)), "far.xlsx": coalition(ulps(C29_EXCEL, 17))}
    monkeypatch.setattr(OR, "read_cached_formula_values", lambda path: books[Path(path).name])
    monkeypatch.setattr(X, "read_tables", lambda path: {})
    assert X.main(["compare", "ref.xlsx", "ref.xlsx", "--no-cell-allowance"]) == 0
    assert X.main(["compare", "ref.xlsx", "near.xlsx"]) == 0
    assert X.main(["compare", "ref.xlsx", "near.xlsx", "--no-cell-allowance"]) == 1
    assert X.main(["compare", "ref.xlsx", "far.xlsx"]) == 1
    out = capsys.readouterr().out
    assert "within_cell_ulp=1" in out and "tier0_number" in out and "cell_allowance_number" in out


# ---------------------------------------------------------------------------------------------------- rank-gated tolerance (D-177 amended 2026-10-13)
# Paul: "Tiny rounding differences are allowed in number cells only when every rank, percentile and threshold matches exactly in the
# same run; checked every time." Continuous cells: at most 1024 ULP. Discrete cells and the member ranks: exact, in the same call.

BM = "Blended Model"
RANKS = {"ranges": [f"{BM}!T3:T7", f"{BM}!Y3:Y7"], "members": 5}
SCORES = (0.9, 0.7, 0.5, 0.3, 0.1)


def blended(blank=()):
    """'Blended Model' in miniature, rows 3-7: S continuous scores (SUMPRODUCT), T and Y their ranks, I a continuous cell fed by S, P a
    percentile, F a 0/1 flag, L a label, M a measure with a fallback. Columns in `blank` carry no formula text (an Excel shared-formula child)."""
    kinds = {"S": ("n", "SUMPRODUCT(A{n}:B{n},C{n}:D{n})"), "T": ("n", "RANK(S{n},$S$3:$S$7)"), "Y": ("n", "RANK(S{n},$S$3:$S$7,0)"),
             "I": ("n", "S{n}/MAX($S$3:$S$7)"), "P": ("n", "TRUNC(PERCENTRANK($S$3:$S$7,S{n},15),5)"),
             "F": ("n", "IF(S{n}>0.4,1,0)"), "L": ("str", 'IF(S{n}>0.4,"keep","drop")'), "M": ("n", "IFERROR(S{n}/B{n},0)")}
    rows = {}
    for i, score in enumerate(SCORES):
        n = i + 3
        values = {"S": score, "T": float(i + 1), "Y": float(i + 1), "I": score / 0.9, "P": (4 - i) / 4, "F": 1.0 if score > 0.4 else 0.0,
                  "L": "keep" if score > 0.4 else "drop", "M": score * 3}
        for col, (kind, formula) in kinds.items():
            rows[f"{col}{n}"] = (kind, values[col], "" if col in blank else formula.format(n=n))
    return {BM: rows}


def shifted(book, **ulp_by_cell):
    """A copy of `book` with the named 'Blended Model' numbers moved up by that many ULP."""
    out = {s: dict(rows) for s, rows in book.items()}
    for cell, n in ulp_by_cell.items():
        kind, value, formula = out[BM][cell]
        out[BM][cell] = (kind, ulps(value, n), formula)
    return out


def gated(ref, cand, **kw):
    return X.compare(ref, cand, X.make_tier0(X.merge_formulas(ref, cand), {}), ranks=RANKS, **kw)


def test_sumproduct_noise_passes_up_to_1024_ulp_when_the_ranks_and_discrete_cells_are_exact():
    ref = blended()
    cand = shifted(ref, S3=256, S4=1024, I3=1024, M4=700)                       # 256 is what SUMPRODUCT summation order gave on the real workbook
    r = gated(ref, cand)
    assert r["equivalent"] and r["problems"] == [], r["problems"]
    tol, rc = r["tolerance"], r["rank_check"]
    assert tol["applied"] and tol["continuous_ulp"] == 1024 and tol["max_continuous_ulp_observed"] == 1024
    assert tol["continuous_cells_within_tolerance"] == 4 == r["totals"]["within_continuous_ulp"] and tol["refused_because"] == []
    assert tol["discrete_differing"] == 0 and tol["discrete_cells"] == 25 and tol["discrete_differences"] == []   # T, Y, P, F, L: five rows each
    assert (rc["result"], rc["members"], rc["rank_exact"], rc["second_rank_column"]["exact"]) == ("PASS", 5, 5, 5)   # the report states both
    # the tolerance is the amendment's: without a rank check in the run, the same candidate fails the old tiers
    old = X.compare(ref, cand, X.make_tier0(ref, {}))
    assert not old["equivalent"] and old["tolerance"]["applied"] is False and old["rank_check"] is None
    assert "no rank check" in old["tolerance"]["refused_because"][0]


def test_1025_ulp_fails():
    ref = blended()
    r = gated(ref, shifted(ref, S3=1025))
    assert not r["equivalent"] and r["tolerance"]["applied"] is True                 # the gate held; the cell is simply too far
    assert r["first_differences"] == [{"sheet": BM, "cell": "S3", "why": "continuous_number", "reference": ("n", 0.9),
                                       "candidate": ("n", ulps(0.9, 1025)), "formula": "SUMPRODUCT(A3:B3,C3:D3)", "ulp": 1025}]
    assert gated(ref, shifted(ref, S3=1024))["equivalent"]
    assert gated(ref, shifted(ref, S3=0))["totals"].get("within_continuous_ulp", 0) == 0


@pytest.mark.parametrize("cell,rank_result", [("T3", "FAIL"), ("Y4", "FAIL"),            # the member ranks, both columns
                                              ("P5", "PASS"), ("T9", "PASS")])          # a percentile; a rank-column cell past the checked range
def test_any_rank_or_percentile_difference_fails_even_by_one_ulp(cell, rank_result):
    ref = blended()
    ref[BM]["T9"] = ("n", 9.0, "RANK(S9,$S$3:$S$7)")
    cand = shifted(ref, **{cell: 1})
    r = gated(ref, cand)
    assert not r["equivalent"] and r["rank_check"]["result"] == rank_result
    assert r["tolerance"]["applied"] is False and r["tolerance"]["refused_because"]
    assert [(d["cell"], d["why"], d["ulp"]) for d in r["first_differences"]] == [(cell, "discrete_number", 1)]
    assert [(d["cell"], d["reference"], d["candidate"]) for d in r["tolerance"]["discrete_differences"]] == [
        (cell, ref[BM][cell][:2], cand[BM][cell][:2])]                                 # a refused run names what refused it
    # SUMPRODUCT noise in the same run does not rescue it: with the gate shut the noisy cell is judged by the old (exact) tier
    noisy = gated(ref, shifted(cand, S3=256))
    assert {d["cell"]: d["why"] for d in noisy["first_differences"]} == {"S3": "tier0_number", cell: "discrete_number"}
    assert [d["cell"] for d in noisy["tolerance"]["discrete_differences"]] == [cell]
    # and the noise alone, with every rank, percentile and flag exact, is what the tolerance is for
    assert gated(ref, shifted(ref, S3=256))["equivalent"]


def test_a_whole_rank_column_is_exact_not_only_its_rank_formulas():
    ref = blended()
    ref[BM]["T8"] = ("n", 15.0, "SUM(T3:T7)")                                       # a total under the ranks: continuous by formula, but in a rank column
    ref[BM]["U8"] = ("n", 15.0, "SUM(U3:U7)")                                       # the same shape in a column with no rank in it
    r = gated(ref, shifted(ref, T8=1))
    assert [(d["cell"], d["why"]) for d in r["first_differences"]] == [("T8", "discrete_number")] and not r["equivalent"]
    assert gated(ref, shifted(ref, U8=1))["equivalent"]


def test_percentile_and_rank_columns_are_exact_even_when_the_excel_side_has_no_formula_text():
    ref = blended(blank=("T", "P", "F"))                                            # Excel's shared-formula children: the text is on the view side
    cand = shifted(blended(), T4=1)
    r = gated(ref, cand)
    assert not r["equivalent"] and r["first_differences"][0]["cell"] == "T4" and r["first_differences"][0]["why"] == "discrete_number"
    cand = shifted(blended(), P6=1)
    assert gated(ref, cand)["first_differences"][0] == {
        "sheet": BM, "cell": "P6", "why": "discrete_number", "reference": ("n", 0.25), "candidate": ("n", ulps(0.25, 1)),
        "formula": "TRUNC(PERCENTRANK($S$3:$S$7,S6,15),5)", "ulp": 1}


def test_a_categorical_if_output_difference_fails_and_a_continuous_one_does_not():
    ref = blended()
    label = {s: dict(rows) for s, rows in ref.items()}
    label[BM]["L5"] = ("str", "drop", 'IF(S5>0.4,"keep","drop")')                  # the label flipped
    r = gated(ref, label)
    assert not r["equivalent"] and r["first_differences"][0]["why"] == "str_value" and r["tolerance"]["applied"] is False
    r = gated(ref, shifted(ref, F4=1))                                              # IF(..,1,0): a 0/1 flag is categorical -> exact
    assert not r["equivalent"] and r["first_differences"][0]["why"] == "discrete_number"
    flag = {s: dict(rows) for s, rows in ref.items()}
    flag[BM]["F4"] = ("n", 0.0, "IF(S4>0.4,1,0)")                                   # the flag itself flipped
    assert not gated(ref, flag)["equivalent"]
    r = gated(ref, shifted(ref, M3=1, M4=1024))                                     # IFERROR(S/B,0): the result is computed -> continuous
    assert r["equivalent"] and r["totals"]["within_continuous_ulp"] == 2
    boolean = {BM: {"Z1": ("b", True, "S3>0.4")}}
    assert not X.compare(boolean, {BM: {"Z1": ("b", False, "S3>0.4")}}, lambda s, a: False, ranks=None)["equivalent"]


def test_the_tolerance_is_refused_when_the_rank_check_fails():
    ref = blended()
    cand = shifted(ref, S4=256)
    cand[BM]["T3"] = ("n", 2.0, "RANK(S3,$S$3:$S$7)")                               # one member's rank moved
    r = gated(ref, cand)
    assert not r["equivalent"] and r["rank_check"]["result"] == "FAIL" and r["rank_check"]["rank_exact"] == 4
    assert r["rank_check"]["rank_differing"] == ["T3"]
    assert any("the continuous-cell tolerance is refused: the rank check is FAIL" in p for p in r["problems"])
    assert r["tolerance"]["applied"] is False and r["totals"].get("within_continuous_ulp", 0) == 0
    # the noise falls back to the old tier rule (S feeds the ranks: tier 0, exact), so it fails too
    assert {d["cell"]: d["why"] for d in r["first_differences"]} == {"T3": "discrete_number", "S4": "tier0_number"}
    # the same noise on its own passes: the rank check, not the noise, is what failed above
    assert gated(ref, shifted(ref, S4=256))["equivalent"]
    # a rank check that cannot be made is a failed one: too few cells, a range that is not there, a short second column
    for ranks in ({"ranges": RANKS["ranges"], "members": 6}, {"ranges": [f"{BM}!T3:T7", "Nope!Y3:Y7"], "members": 5},
                  {"ranges": [f"{BM}!T3:T9"], "members": 7}):
        r = X.compare(ref, shifted(ref, S4=256), X.make_tier0(ref, {}), ranks=ranks)
        assert not r["equivalent"] and r["rank_check"]["result"] == "FAIL" and r["tolerance"]["applied"] is False, ranks
    second = shifted(ref, Y5=1)
    r = gated(ref, second)
    assert r["rank_check"]["result"] == "FAIL" and r["rank_check"]["rank_exact"] == 5 and r["rank_check"]["second_rank_column"]["differing"] == ["Y5"]


def test_the_rank_check_reads_the_fields_cutover_needs_and_all_roster_members(tmp_path, monkeypatch):
    n = 7                                                                  # a synthetic roster: the member count is the roster's, never a literal
    (tmp_path / "data" / "engine").mkdir(parents=True)
    (tmp_path / "data" / "engine" / "roster.json").write_text(json.dumps(
        {"schema": roster.SCHEMA, "members": [{"member": f"M{i}", "bioguide": f"B{i}"} for i in range(n)]}), encoding="utf-8")
    assert X.rank_members(tmp_path) == roster.count(root=tmp_path) == n
    last = 2 + n
    book = {BM: {f"T{r}": ("n", float(r - 2), f"RANK(S{r},$S$3:$S${last})") for r in range(3, last + 1)}}
    rng = f"{BM}!T3:T{last}"
    rc = X.rank_check(book, book, [rng], n)
    assert (rc["result"], rc["members"], rc["rank_exact"], rc["members_expected"]) == ("PASS", n, n, n)
    monkeypatch.setattr(X, "REPO", tmp_path)                                 # no members argument: the default is the roster of the tree
    rc = X.rank_check(book, book, [rng])
    assert (rc["result"], rc["members_expected"]) == ("PASS", n)
    short = {BM: {a: v for a, v in book[BM].items() if a != "T9"}}
    rc = X.rank_check(book, short, [rng])
    assert rc["result"] == "FAIL" and rc["rank_exact"] == n - 1 and rc["rank_missing"] == ["T9"]
    text = {BM: {**book[BM], "T9": ("str", "2", f"RANK(S9,$S$3:$S${last})")}}
    assert X.rank_check(text, text, [rng])["result"] == "FAIL"           # equal but not a number is not a rank
    assert X.rank_check(book, book, [])["result"] == "FAIL"


@pytest.mark.parametrize("formula,discrete", [
    ("RANK(A1,A$1:A$9)", True), ("_xlfn.RANK.EQ(A1,A:A)", True), ("TRUNC(PERCENTRANK($D$3:$D$535,D3,15),5)", True), ("PERCENTRANK(A1:A9,B1)", True),
    ("TRUNC(A1,2)", True), ("ROUND(A1,2)", True), ("ROUNDUP(A1,0)", True), ("A1<B1", True), ("A1<>B1", True), ("AND(A1>0,B1>0)", True),
    ('IF(A1>0,"hi","lo")', True), ("IF(A1>0,1,0)", True), ('IF(A1>0,IF(B1>0,"a","b"),"c")', True), ('_xlfn.IFS(A1>0,"a",A1<0,"b")', True),
    ('_xlfn.LET(_xlpm.x,A1,IF(_xlpm.x=0,"zero","nonzero"))', True), ('IFERROR(MATCH("x",A1:A9,0),"none")', False),
    ("IFERROR(AVERAGEIF(A1:A9,B1,S1:S9),0)", False), ("IF(A1>0,A1*2,0)", False), ("IFERROR(1/0,-1)", False), ("SUM(A1:A3)*2", False),
    ("C3/MAX($C$3:$C$535)", False), ("'Rank & Allocation'!F3", False), ("$AB$18*I7+$AB$19*L7", False), ("SUMPRODUCT(A1:A9,B1:B9)", False),
    ('"a>b"&A1', False), ("AVERAGE('Blended Model'!$S$3:$S$535)", False), ("INDEX(A1:A9,MATCH(1,B1:B9,0))", False)])
def test_what_counts_as_a_discrete_formula(formula, discrete):
    assert X.discrete_formula(formula) is discrete


def test_exact_range_pins_a_continuous_cell_and_c29_allowance_is_redundant_but_kept():
    ref = blended()
    r = gated(ref, shifted(ref, I3=1), exact_ranges=[f"{BM}!I3:I4"])
    assert not r["equivalent"] and r["first_differences"][0]["why"] == "discrete_number"
    assert gated(ref, shifted(ref, I3=1))["equivalent"]                               # unpinned, the same cell is tolerated
    book = {**blended(), **coalition()}
    for n, legacy_ok in ((8, True), (100, False)):                                  # C29 at 100 ULP: past its 16, inside 1024
        cand = {**blended(), **coalition(ulps(C29_EXCEL, n))}
        amended = X.compare(book, cand, X.make_tier0(X.merge_formulas(book, cand), {}), ranks=RANKS)
        assert amended["equivalent"] and amended["totals"]["within_continuous_ulp"] == 1 and amended["cell_ulp"] == {f"{CP}!C29": 16}
        old = X.compare(book, cand, X.make_tier0(X.merge_formulas(book, cand), {}))
        assert old["equivalent"] is legacy_ok
    assert X.CELL_ULP == {(CP, "C29"): 16}                                          # kept


def test_compare_cli_rank_gate(monkeypatch, capsys, tmp_path):
    """--rank-range turns the tolerance on, writes both reports, and the rank-check file has the fields cutover.py reads."""
    ref = blended()
    far_rank = shifted(ref, S4=256)
    far_rank[BM]["T3"] = ("n", 2.0, "RANK(S3,$S$3:$S$7)")
    books = {"ref.xlsx": ref, "near.xlsx": shifted(ref, S3=256, I4=900), "far.xlsx": shifted(ref, S3=2000), "rank.xlsx": far_rank}
    monkeypatch.setattr(OR, "read_cached_formula_values", lambda path: books[Path(path).name])
    monkeypatch.setattr(X, "read_tables", lambda path: {})
    gate = ["--rank-range", f"{BM}!T3:T7", "--rank-range", f"{BM}!Y3:Y7", "--rank-members", "5"]
    rep, rank = tmp_path / "eq.json", tmp_path / "rank.json"
    assert X.main(["compare", "ref.xlsx", "near.xlsx", *gate, "--report", str(rep), "--rank-check-report", str(rank)]) == 0
    out = capsys.readouterr().out
    assert "rank check: PASS 5/5 exact" in out and "tolerance: APPLIED, continuous cells within 1024 ULP (max seen 900, 2 cells)" in out
    doc, rdoc = json.loads(rep.read_text()), json.loads(rank.read_text())
    assert doc["equivalent"] is True and doc["tolerance"]["applied"] is True and doc["rank_check"] == {
        k: v for k, v in rdoc.items() if k in doc["rank_check"]}
    assert (rdoc["result"], rdoc["members"], rdoc["rank_exact"], rdoc["rank_differing"], rdoc["rank_missing"]) == ("PASS", 5, 5, [], [])
    assert rdoc["schema"] == "office-rank-check-v1" and rdoc["same_run_as_equivalence"] is True
    assert X.main(["compare", "ref.xlsx", "near.xlsx"]) == 1                          # no --rank-range, no tolerance
    assert "tolerance: NOT APPLIED: no rank check in this run" in capsys.readouterr().out
    assert X.main(["compare", "ref.xlsx", "far.xlsx", *gate, "--continuous-ulp", "1999"]) == 1                 # the limit is a flag
    assert X.main(["compare", "ref.xlsx", "far.xlsx", *gate, "--continuous-ulp", "2000"]) == 0
    capsys.readouterr()
    assert X.main(["compare", "ref.xlsx", "rank.xlsx", *gate, "--rank-check-report", str(rank)]) == 1
    assert json.loads(rank.read_text())["result"] == "FAIL" and "tolerance: NOT APPLIED" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        X.main(["compare", "ref.xlsx", "near.xlsx", "--rank-check-report", str(rank)])      # a rank-check report needs ranges


def test_reads_cell():
    assert X.reads_cell("A1+B2", "S", "S", "B2") and not X.reads_cell("A1+B2", "S", "T", "B2")
    assert X.reads_cell("SUM(B1:C3)", "S", "S", "C2") and X.reads_cell("SUM(C3:B1)", "S", "S", "C2") and not X.reads_cell("SUM(B1:C3)", "S", "S", "D2")
    assert X.reads_cell("T!$B$2", "S", "T", "B2") and X.reads_cell("'My ''Sheet'!B2", "S", "My 'Sheet", "B2")
    assert X.reads_cell("SUM(B:D)", "S", "S", "C99") and X.reads_cell("SUM(2:4)", "S", "S", "Z3") and not X.reads_cell("SUM(2:4)", "S", "S", "Z5")
    assert not X.reads_cell('"B2"&LOG10(B3)', "S", "S", "B2") and not X.reads_cell("ATAN2(1,2)", "S", "S", "B2")


# ---------------------------------------------------------------------------------------------------- probe + negative control

def test_probe_known_values_are_checked_and_a_known_divergence_is_reported_not_failed(probe, tmp_path):
    view = inject_probe(probe, tmp_path / "ok.xlsx")
    rows, failed = X.check_probe(view)
    assert not failed and {r["status"] for r in rows} == {"PASS"} and len(rows) == 19
    rounded = {"Data": {}, "Probe": {c: (k, 0.66667 if c == "B4" else v) for c, _f, k, v, _lo in X.PROBE}}
    rows, failed = X.check_probe(inject_probe(probe, tmp_path / "pr.xlsx", rounded))
    assert not failed and [r["cell"] for r in rows if r["status"] == "KNOWN_DIVERGENCE"] == ["B4"]      # tdf#104981
    wrong = {"Data": {}, "Probe": {c: (k, 99.0 if c == "B1" else v) for c, _f, k, v, _lo in X.PROBE}}
    rows, failed = X.check_probe(inject_probe(probe, tmp_path / "bad.xlsx", wrong))
    assert failed and [r["cell"] for r in rows if r["status"] == "FAIL"] == ["B1"]
    boolean_as_number = {"Data": {}, "Probe": {c: ("n", 1.0) if c == "B5" else (k, v) for c, _f, k, v, _lo in X.PROBE}}
    assert X.check_probe(inject_probe(probe, tmp_path / "b.xlsx", boolean_as_number))[1]          # TRUE written as 1 is a failure


def test_probe_cli(probe, tmp_path, capsys):
    out = tmp_path / "p2.xlsx"
    assert X.main(["probe-make", str(out)]) == 0 and out.read_bytes() == probe.read_bytes()       # deterministic generator
    view = inject_probe(probe, tmp_path / "v.xlsx")
    assert X.main(["probe-check", str(view)]) == 0
    assert X.main(["compare", str(view), str(view)]) == 0
    other = inject_probe(probe, tmp_path / "w.xlsx", {"Data": {}, "Probe": {c: (k, v + 1.0 if c == "B17" else v) for c, _f, k, v, _lo in X.PROBE}})
    assert X.main(["compare", str(view), str(other), "--report", str(tmp_path / "rep.json")]) == 1
    text = capsys.readouterr().out
    assert "NOT EQUIVALENT" in text and "B17" in text and (tmp_path / "rep.json").is_file()


def extended_results(**override):
    rows = {c: (k, v) for c, _f, k, v, _lo in [*X.PROBE, *X.PROBE_EXTENDED]}
    rows.update(override)
    return {"Data": {}, "Probe": rows}


def test_extended_probe_reports_divergences_and_never_fails_on_them(tmp_path):
    ext = tmp_path / "ext.xlsx"
    X.write_probe(ext)
    base = ext.read_bytes()
    X.write_probe(ext, extended=True)
    assert ext.read_bytes() != base
    safety, cells = OR.scan_package(ext, set(OR.POLICIES["melange"]) | {"_XLFN.XLOOKUP"})
    assert safety["safe_for_isolated_calculation"] and len(cells["Probe"]["addrs"]) == len(X.PROBE) + len(X.PROBE_EXTENDED)
    # a correct calculator (Excel's own values): every row passes
    rows, failed = X.check_probe(inject_probe(ext, tmp_path / "ok.xlsx", extended_results()))
    assert not failed and {r["status"] for r in rows} == {"PASS"} and len(rows) == len(X.PROBE) + len(X.PROBE_EXTENDED)
    # the divergences design 3.4 expects from LibreOffice: Neumaier-style SUM leaves 2.8e-17, approximate-equal ranks tie,
    # the criteria number is text of a different precision: reported as DIVERGENCE, the run does not fail
    lo = extended_results(B21=("n", 2.7755575615628914e-17), B23=("n", 2.0), B29=("n", 1.0), B34=("str", "ne"))
    rows, failed = X.check_probe(inject_probe(ext, tmp_path / "lo.xlsx", lo))
    assert not failed and {r["cell"] for r in rows if r["status"] == "DIVERGENCE"} == {"B21", "B23", "B29", "B34"}
    # ... but a wrong value in the plain probe still fails, extended or not
    rows, failed = X.check_probe(inject_probe(ext, tmp_path / "bad.xlsx", extended_results(B1=("n", 99.0))))
    assert failed and [r["cell"] for r in rows if r["status"] == "FAIL"] == ["B1"]
    # a plain probe has no extended cells to check
    X.write_probe(tmp_path / "plain.xlsx")
    plain = inject_probe(tmp_path / "plain.xlsx", tmp_path / "plain.view.xlsx")
    assert len(X.check_probe(plain)[0]) == len(X.PROBE)


def test_probe_make_extended_cli(tmp_path, capsys):
    out = tmp_path / "e.xlsx"
    assert X.main(["probe-make", str(out), "--extended"]) == 0 and '"extended": true' in capsys.readouterr().out
    first = out.read_bytes()
    assert X.main(["probe-make", str(out), "--extended"]) == 0 and out.read_bytes() == first       # deterministic
    with zipfile.ZipFile(out) as z:
        assert b"COUNTIFS(Data!H1:H3" in z.read("xl/worksheets/sheet2.xml") and b"1e-17" in z.read("xl/worksheets/sheet1.xml")


def const(path, sheet, addr):
    with zipfile.ZipFile(path) as z:
        part = dict(OR.sheet_parts(z))[sheet]
        m = re.search(rb'<c r="%s"[^>]*><v>(.*?)</v>' % addr.encode(), z.read(part))
    return float(m.group(1))


def test_perturb_changes_one_constant_and_only_that(probe, tmp_path):
    out = tmp_path / "p.xlsx"
    X.perturb(probe, out, "Data!A1", 5.0)
    assert const(out, "Data", "A1") == 15.0 and const(probe, "Data", "A1") == 10.0
    with zipfile.ZipFile(probe) as a, zipfile.ZipFile(out) as b:
        assert [n for n in a.namelist() if a.read(n) != b.read(n)] == ["xl/worksheets/sheet1.xml"]
    for bad in ("Probe!B1", "Data!Z99", "Nope!A1"):                                # a formula, a missing cell, a missing sheet
        with pytest.raises(SystemExit):
            X.perturb(probe, tmp_path / "x.xlsx", bad)


def fake_engine(workdir):
    """A tiny calculator for the probe: SUM and SUMPRODUCT really depend on Data!A1:A4; the rest are the known values."""
    def engine(path):
        a = [const(path, "Data", f"A{i}") for i in range(1, 5)]
        res = {"Data": {}, "Probe": {c: (k, v) for c, _f, k, v, _lo in X.PROBE}}
        res["Probe"]["B1"] = ("n", sum(a))
        res["Probe"]["B17"] = ("n", sum(x * x for x in a))
        return inject_probe(path, Path(workdir) / (Path(path).stem + ".calc.xlsx"), res)
    return engine


def test_negative_control_fails_the_comparison_and_names_the_dependents(probe, tmp_path):
    ok, report = X.negative_control(probe, "Data!A1", fake_engine(tmp_path), fake_engine(tmp_path), tmp_path, delta=1.0)
    assert ok and not report["equivalent"]
    assert {d["cell"] for d in report["first_differences"]} == {"B1", "B17"}        # exactly the cells that read Data!A1
    # a candidate that ignores the perturbation (a blind comparison) is caught: the control reports FAIL
    blind = lambda p: fake_engine(tmp_path)(probe)                                  # noqa: E731
    ok, report = X.negative_control(probe, "Data!A1", fake_engine(tmp_path), blind, tmp_path, delta=1.0)
    assert not ok and report["equivalent"]


# ---------------------------------------------------------------------------------------------------- Excel (opt-in, tiny synthetic workbook)

@needs_excel
def test_excel_reference_reproduces_the_extended_probe(tmp_path):
    ext = tmp_path / "ext.xlsx"
    X.write_probe(ext, extended=True)
    rows, failed = X.check_probe(X.excel_recalculate(ext, tmp_path / "excel", allow=ALLOW))
    assert not failed and {r["status"] for r in rows} == {"PASS"}, [r for r in rows if r["status"] != "PASS"]   # the recorded values are Excel's


@needs_excel
def test_excel_reference_reproduces_the_probe_known_values_and_the_pipeline_round_trips(probe, tmp_path):
    ref = X.excel_recalculate(probe, tmp_path / "excel", allow=ALLOW)
    assert ref != probe and probe.read_bytes() == probe.read_bytes()
    rows, failed = X.check_probe(ref)
    assert not failed and {r["status"] for r in rows} == {"PASS"}                    # Excel itself gives every known value, PERCENTRANK 0.66666 included
    values = OR.read_cached_formula_values(ref)
    # the injection pipeline fed with Excel's own results reproduces Excel's workbook cell by cell
    view = inject_probe(probe, tmp_path / "view.xlsx", {s: {a: (k, v) for a, (k, v, _f) in rows.items()} for s, rows in values.items()})
    assert X.compare(values, OR.read_cached_formula_values(view), X.make_tier0(values, {}))["equivalent"]
    # negative control with Excel on both sides: changing Data!A1 must show up in the formulas that read it
    ok, report = X.negative_control(probe, "Data!A1", lambda p: X.excel_recalculate(p, tmp_path / "e1", allow=ALLOW),
                                    lambda p: X.excel_recalculate(p, tmp_path / "e2", allow=ALLOW), tmp_path, delta=5.0)
    assert ok and {"B1", "B17"} <= {d["cell"] for d in report["first_differences"]}
