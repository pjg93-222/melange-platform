"""lo_probe (D-174 probes of 2026-10-12): the Excel models, what the generator guarantees, the judging logic against fake
calculators. Nothing here needs LibreOffice. The real run is the last test, gated on LO_PROBE_IMAGE (an office-calc image
tag or digest; needs Docker): it pins what office-calc 25.2.3 does TODAY, so a LibreOffice upgrade shows up as a failure
there rather than as a surprise in a workbook comparison."""
import hashlib
import os
import sys
from fractions import Fraction
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS / "compute"))
import lo_probe as P  # noqa: E402
import office_recalc as OR  # noqa: E402

POLICY = set(OR.POLICIES["melange"])


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(src, out, results):
    def calc(copy, cells, **kw):
        return results, {"product": "x", "version": "1"}
    OR.run(src, sha(src), out, None, allow=P.ALLOW, calculator=calc)
    return out


def test_excel_model_reproduces_the_recorded_vector_and_the_documented_rules():
    e = P.pr_exact([10.0, 20.0, 30.0, 40.0], 30.0, 5)
    assert (e["trunc"], e["round"], e["differ"]) == (0.66666, 0.66667, True)          # 0.66666 is what a real Excel run gave
    assert P.pr_exact([10.0, 20.0, 30.0, 40.0], 25.0, 5)["trunc"] == 0.5               # interpolated, exact
    assert P.pr_exact([10.0, 20.0, 30.0, 40.0], 9.0, 5) is None and P.pr_exact([10.0, 20.0, 30.0, 40.0], 41.0, 5) is None   # #N/A
    assert P.pr_exact([1.0, 1.0, 2.0, 3.0], 1.0, 3)["trunc"] == 0.0                    # a tie: strictly-below count / (n-1)
    assert P.pr_exact([1.0, 1.0, 2.0, 3.0], 2.0, 3)["trunc"] == 0.666                  # 2/3 truncated to the default 3 places


def test_the_python_authority_is_exact_truncation_at_the_workbooks_n():
    vals = [float(i) for i in range(533)]                                                # every possible below-count at n=533
    assert all(P.pillars_percentrank(vals, v) == P.pr_exact(vals, v, 5)["trunc"] for v in vals)


def test_round_significant_is_the_decimal_rounding_from_a_tenth_up_and_not_below():
    assert P.round_significant(Fraction(2, 3), 5) == 0.66667 and P.round_significant(Fraction(1, 2), 1) == 0.5
    assert P.round_significant(Fraction(1, 16), 1) == 0.06 and P.round_significant(Fraction(1, 16), 2) == 0.063    # leading zeros do not count
    assert P.round_significant(Fraction(0), 3) == 0.0 and P.round_significant(Fraction(1), 3) == 1.0


def test_generated_cases_cover_what_the_question_asks_and_are_deterministic():
    cases = P.pr_cases()
    assert cases == P.pr_cases()
    exact = [(c, P.pr_exact(c["vals"], c["x"], c["sig"] or P.DEFAULT_SIG)) for c in cases]
    differ = [c for c, e in exact if e and e["differ"]]
    assert len(differ) >= 20
    assert any(c["sig"] is None for c in differ) and any(c["xkind"] == "between" for c in differ)     # default significance, interpolation
    assert any(len(set(c["vals"])) < len(c["vals"]) for c in differ)                                 # ties
    assert {c["group"] for c in cases} >= {"vector", "member_distinct", "member_ties", "between", "spelling_INC", "edge_below"}
    assert all(e["margin"] > 1e-6 for c, e in exact if e and c["group"] in ("member_distinct", "member_ties", "between"))


def test_probe_workbooks_pass_the_preflight_only_with_the_extra_functions(tmp_path):
    books = P.make(tmp_path)
    for name, path in books.items():
        safety, _ = OR.scan_package(path, POLICY | {a.upper() for a in P.ALLOW})
        assert safety["safe_for_isolated_calculation"], (name, safety["unsafe_formula_examples"][:2])
    # AVERAGE and TRUNC joined the melange list on 2026-10-12: the average books pass on the policy alone, the rest still need COUNT
    # (or the _xlfn.PERCENTRANK.INC spelling), which the workbook itself does not use
    needs = {}
    for name, path in books.items():
        safety, _ = OR.scan_package(path, POLICY)
        needs[name] = sorted({f["function"] for f in safety["formula_functions"]} - POLICY)
        assert safety["safe_for_isolated_calculation"] == (not needs[name]), name
    assert needs == {"percentrank": ["_XLFN.PERCENTRANK.INC"], "pr_shape": ["COUNT"], "average": [], "avg_shape": [],
                     "iferror": ["COUNT"], "avg_forms": ["COUNT"]}
    assert OR.scan_package(books["avg_shape"], POLICY)[0]["formula_count"] == 534                   # 533 S cells + C29


def test_the_judge_tells_truncation_from_rounding(tmp_path):
    book = P.build_percentrank(tmp_path / "pr.xlsx")
    rows = {"trunc": {}, "round": {}}
    for k, c in enumerate(P.pr_cases()):
        e = P.pr_exact(c["vals"], c["x"], c["sig"] or P.DEFAULT_SIG)
        for kind, key in (("trunc", "trunc"), ("round", "round_sig")):
            rows[kind][f"A{k + 1}"] = ("e", "#N/A") if e is None else ("n", e[key])
    for kind, verdict in (("trunc", "TRUNCATES"), ("round", "ROUNDS (decimal places")):
        view = run(book, tmp_path / f"{kind}.view.xlsx", {"Data": {}, "Probe": rows[kind]})
        report = P.judge_percentrank(view)
        assert report["verdict"].startswith(verdict) and report["differing_cases"] >= 20, (kind, report["tally"])
    assert report["tally"]["TRUNCATES"] == 0 and report["tally"]["OTHER"] == 0                      # last view = the rounding engine


def test_the_average_judge_passes_excel_values_and_flags_a_logical_counted_as_one(tmp_path):
    book = P.build_average(tmp_path / "avg.xlsx")
    results = {f"A{k + 1}": want for k, (_i, _w, _c, _f, want) in enumerate(P.avg_cases())}
    known = {'""': ("str", ""), "1>0": ("b", True), "1/0": ("e", "#DIV/0!")}                         # the formula cells inside the data ranges
    data = {f"{P.col(2 * k + 1)}{i + 1}": known[spec[1]] for k, c in enumerate(P.avg_cases()) for i, spec in enumerate(c[2].get("C", []))
            if spec and spec[0] == "f"}
    assert {r["status"] for r in P.judge_average(run(book, tmp_path / "ok.view.xlsx", {"Data": data, "Probe": results}))} == {"PASS"}
    results["A5"] = ("n", 1.75)                                                                      # [2,TRUE,4,FALSE] averaged with TRUE=1
    bad = [r for r in P.judge_average(run(book, tmp_path / "bad.view.xlsx", {"Data": data, "Probe": results})) if r["status"] != "PASS"]
    assert [r["id"] for r in bad] == ["logical_constants"]


@pytest.mark.skipif(not os.environ.get("LO_PROBE_IMAGE"), reason="set LO_PROBE_IMAGE to an office-calc image (needs Docker)")
def test_office_calc_25_2_3_today(tmp_path):
    assert P.main(["run", str(tmp_path), os.environ["LO_PROBE_IMAGE"]]) == 0
    report = P.check(tmp_path)
    pr = report["percentrank"]
    assert pr["verdict"].startswith("ROUNDS") and pr["tally"]["TRUNCATES"] == 0 and pr["tally"]["OTHER"] == 0 and pr["differing_cases"] >= 20
    assert all(c["other"] == 0 and c["equal_round_sig"] == c["cells"] for k, c in report["pr_shape"].items() if k != "iferror_shape")
    assert all(c["rewrite_B_trunc_rank_count"] == c["cells"] for k, c in report["pr_shape"].items() if k != "iferror_shape")
    assert {r["id"] for r in report["average"] if r["status"] != "PASS"} >= {"logical_constants", "single_ref_logical"}
    assert report["avg_shape"]["S_equal_python"] == 533 and report["avg_shape"]["ulp_vs"]["fsum/n"] == 0
    assert all(d["byte_identical"] for d in report["determinism"].values())


@pytest.mark.skipif(not (os.environ.get("LO_PROBE_IMAGE") and os.environ.get("XL_TEST_EXCEL") == "1" and sys.platform == "win32"),
                    reason="set LO_PROBE_IMAGE and XL_TEST_EXCEL=1 on a Windows box with Excel and Docker (starts a separate Excel; ~5 min)")
def test_excel_oracle_against_office_calc_25_2_3_today(tmp_path):
    """Real Excel on the synthetic probe workbooks, beside the container run of the same files. Run with -p no:faulthandler."""
    assert P.main(["run", str(tmp_path), os.environ["LO_PROBE_IMAGE"]]) == 0
    ran = P.run_excel(tmp_path)
    assert all(v["exit"] == 0 for v in ran.values() if isinstance(v, dict)) and ran["excel_processes_of_mine_left"] == [], ran
    o = P.oracle(tmp_path)
    pc = o["percentrank_cases"]
    assert pc["excel_equals_model"] == pc["cases"] == len(P.pr_cases()) and pc["differing_excel_equals_model"] == pc["differing_cases"] >= 20
    assert pc["excel_equals_lo"] < pc["cases"] // 2                                               # LibreOffice agrees with Excel on few of them
    shapes = {k: v for k, v in o["pr_shape"].items() if k in P.shape_columns()}
    assert all(c["excel_equals_authority"] == c["cells"] for c in shapes.values())               # Excel truncates, exactly as compute_pillars.py:59
    assert all(c["A_excel_equals_orig_excel"] == c["A_lo_equals_orig_excel"] == c["cells"] for c in shapes.values())   # rewrite A: unmoved, and LO == Excel
    ie = o["iferror"]
    assert ie["A_unmoved_wrapped_excel"] == ie["A_lo_equals_orig_excel_wrapped"] == ie["rows_total"]                   # blank, text, "", #N/A, TRUE, out of range, interpolated
    assert ie["B_unmoved_wrapped_excel"] < ie["rows_total"]                                      # rewrite B is only valid where x is a member
    assert all(r["excel_equals_documented"] for r in o["average"])                               # Excel follows its documented AVERAGE rules, 22 of 22
    assert o["avg_shape"]["S_cells_excel_equal_lo"] == 533 and o["avg_shape"]["ulp_excel_vs_lo"] == 8
    assert all(f["excel_equals_excel_AVERAGE"] == 30 and f["lo_equals_excel_AVERAGE"] < 30 for f in o["avg_forms"].values())   # no sum form matches

