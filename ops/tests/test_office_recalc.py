"""office-calc (D-174): value injection, safety preflight, exit codes, the engine switch, the Excel-site map, the audit
gate's LibreOffice branch, and the pins of the image, compose file and workflow.

Nothing here needs LibreOffice or Excel: the calculator is replaced by known values (the probe's, which a real Excel run
confirmed on 2026-10-12). The LibreOffice step itself is exercised by the smoke test in compute-office.yml."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
import yaml

OPS = Path(__file__).resolve().parents[1]
ROOT = OPS.parent
sys.path.insert(0, str(OPS / "compute"))
sys.path.insert(0, str(OPS))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fec"))
import office_recalc as OR  # noqa: E402
import xl_engine as E  # noqa: E402
import xl_equivalence as X  # noqa: E402
import security_gate  # noqa: E402
from ops.tests.layout import src  # noqa: E402

ALLOW = ["_XLFN.XLOOKUP"]
WF = src("ops/github/workflows/compute-office.yml")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe_results(rows=None):
    """What a correct calculator returns for the probe: {sheet: {addr: (kind, value)}} (Data has no formulas)."""
    return {"Data": {}, "Probe": {c: (k, v) for c, _f, k, v, _lo in (rows or X.PROBE)}}


def fake_calc(results):
    def calc(copy, cells, **kw):
        assert Path(copy).is_file() and set(cells) == set(results)
        return results, {"product": "LibreOffice", "version": "25.2.3.2", "version_string": "LibreOffice 25.2.3.2 fake",
                         "automation_security": "MacroExecutionMode.NEVER_EXECUTE", "update_links": 0, "enable_events": False,
                         "display_alerts": False, "ask_to_update_links": False, "visible": False, "read_only": True,
                         "load_recalc_mode": "never", "calculation_request": "calculateAll", "calculation_completed": True,
                         "calculate_before_save": False, "document_stored": False}
    return calc


@pytest.fixture
def probe(tmp_path):
    p = tmp_path / "in" / "probe.xlsx"
    p.parent.mkdir()
    X.write_probe(p)
    return p


def run_probe(probe, out, **kw):
    return OR.run(probe, sha(probe), out, kw.pop("receipt", None), allow=ALLOW, calculator=kw.pop("calculator", fake_calc(probe_results())), **kw)


# ---------------------------------------------------------------------------------------------------- injection

def test_view_copy_holds_the_results_and_changes_nothing_else(probe, tmp_path):
    before = sha(probe)
    out = tmp_path / "out.xlsx"
    receipt = run_probe(probe, out, receipt=tmp_path / "r.json")
    assert sha(probe) == before                                           # the input is never written
    back = OR.read_cached_formula_values(out)["Probe"]
    for cell, formula, kind, value, _lo in X.PROBE:
        assert back[cell][:2] == (kind, value) and back[cell][2] == formula
    with zipfile.ZipFile(probe) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist()
        for n in a.namelist():
            if n != "xl/worksheets/sheet2.xml":                           # the only part with formulas
                assert a.read(n) == b.read(n), n
        # formulas are byte-identical: strip the cached values and the t= attributes and the sheets are equal
        strip = lambda d: re.sub(rb"<v>.*?</v>| t=\"[a-z]+\"", b"", d)    # noqa: E731
        assert strip(a.read("xl/worksheets/sheet2.xml")) == strip(b.read("xl/worksheets/sheet2.xml"))
    assert receipt["cached_by_type"] == {"b": 1, "e": 1, "n": 14, "str": 3} and receipt["formula_cached_error_count"] == 1
    assert receipt["unchanged_part_count"] == 5 and receipt["canonical_workbook_written"] is False


def test_two_runs_give_byte_identical_view_copies(probe, tmp_path):
    run_probe(probe, tmp_path / "a.xlsx")
    run_probe(probe, tmp_path / "b.xlsx")
    assert (tmp_path / "a.xlsx").read_bytes() == (tmp_path / "b.xlsx").read_bytes()


def test_source_date_epoch_makes_the_receipt_reproducible(probe, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1760000000")
    a = run_probe(probe, tmp_path / "a.xlsx", receipt=tmp_path / "a.json")
    assert a["generated_at"] == "2025-10-09T08:53:20+00:00" and a["started_at"] == a["generated_at"]


def test_inject_sheet_edge_cases():
    xml = (b'<sheetData><row r="1">'
           b'<c r="A1" s="2" t="str"><f>"a"</f><v>old</v></c>'                      # string -> number: t goes
           b'<c r="B1"><f t="shared" ref="B1:B2" si="0">A1+1</f><v/></c>'          # shared master, empty v
           b'<c r="B2"><f t="shared" si="0"/></c>'                                  # shared child, no v at all
           b'<c r="C1" s="3"/>'                                                     # untouched
           b'<c r="D1"><v>5</v></c>'                                                # constant, untouched
           b'<c r="E1" t="inlineStr"><is><t>x</t></is></c>'
           b'<c r="F1"><f>T(1)</f><v>0</v></c></row></sheetData>')
    res = {"A1": ("n", 2.5), "B1": ("str", "a&b <c> _x0041_ \x01"), "B2": ("b", True), "F1": ("e", "#N/A")}
    seen = set()
    out = OR.inject_sheet(xml, res, seen)
    assert seen == set(res)
    assert b'<c r="A1" s="2"><f>"a"</f><v>2.5</v></c>' in out
    assert b'<c r="B1" t="str"><f t="shared" ref="B1:B2" si="0">A1+1</f><v>a&amp;b &lt;c&gt; _x005F_x0041_ _x0001_</v></c>' in out
    assert b'<c r="B2" t="b"><f t="shared" si="0"/><v>1</v></c>' in out
    assert b'<c r="C1" s="3"/>' in out and b'<c r="D1"><v>5</v></c>' in out and b"<is><t>x</t></is>" in out
    assert b'<c r="F1" t="e"><f>T(1)</f><v>#N/A</v></c>' in out
    with pytest.raises(OR.VerifyError):
        OR.inject_sheet(xml, {"A1": ("n", 1.0)}, set())                          # a formula cell without a result


def test_values_round_trip_exactly():
    for x in (0.0, -0.0, 1.0, 100.0, 0.1 + 0.2, 1e-300, 5.551115123125783e-17, 1e22, 123456789012345680.0, -2.5, 2 / 3):
        assert float(OR.fmt_number(x)) == x
    assert OR.fmt_number(-0.0) == "0" and OR.fmt_number(3.0) == "3"
    assert OR.unescape_x(OR.escape_x("a_x0041_b\x01\x1f")) == "a_x0041_b\x01\x1f"
    with pytest.raises(OR.Unmappable):
        OR.encode_value("e", "Err:502")                                         # LibreOffice-only: never written


# ---------------------------------------------------------------------------------------------------- preflight

def test_preflight_passes_the_probe_only_with_its_function_allowed(probe):
    safety, cells = OR.scan_package(probe, set(OR.POLICIES["melange"]) | set(ALLOW))
    assert safety["safe_for_isolated_calculation"] and safety["formula_count"] == len(X.PROBE) == len(cells["Probe"]["addrs"])
    bad, _ = OR.scan_package(probe, OR.POLICIES["melange"])
    assert not bad["safe_for_isolated_calculation"] and bad["unsafe_formula_examples"][0]["function"] == "_XLFN.XLOOKUP"


def rewrite(src, dst, edits):
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst, "w") as b:
        for i in a.infolist():
            b.writestr(i, edits.pop(i.filename, a.read(i)))
        for name, data in edits.items():
            b.writestr(name, data)


@pytest.mark.parametrize("name,data,hint", [
    ("xl/vbaProject.bin", b"x", "forbidden_package_parts"),
    ("xl/externalLinks/externalLink1.xml", b"<x/>", "forbidden_package_parts"),
    ("xl/worksheets/_rels/sheet2.xml.rels", b'<Relationships xmlns="%s"><Relationship Id="r1" Type="x/oleObject" Target="file:///c:/x" TargetMode="External"/></Relationships>' % OR.REL_NS.encode(), "unsafe_external_relationships"),
])
def test_preflight_refuses_active_content(probe, tmp_path, name, data, hint):
    bad = tmp_path / "bad.xlsx"
    rewrite(probe, bad, {name: data})
    safety, _ = OR.scan_package(bad, set(OR.POLICIES["melange"]) | set(ALLOW))
    assert not safety["safe_for_isolated_calculation"] and safety[hint]


def test_preflight_refuses_external_references_array_formulas_and_risky_names(probe, tmp_path):
    with zipfile.ZipFile(probe) as z:
        sheet = z.read("xl/worksheets/sheet2.xml")
        book = z.read("xl/workbook.xml")
    cases = {
        "extref": {"xl/worksheets/sheet2.xml": sheet.replace(b"SUM(Data!A1:A4)", b"SUM('[other.xlsx]S'!A1)")},
        "array": {"xl/worksheets/sheet2.xml": sheet.replace(b"<f>SUM(Data!A1:A4)</f>", b'<f t="array" ref="B1:B2">SUM(Data!A1:A4)</f>')},
        "name": {"xl/workbook.xml": book.replace(b"</sheets>", b'</sheets><definedNames><definedName name="Auto_Open">Sheet1!A1</definedName></definedNames>')},
    }
    for label, edits in cases.items():
        bad = tmp_path / f"{label}.xlsx"
        rewrite(probe, bad, edits)
        safety, _ = OR.scan_package(bad, set(OR.POLICIES["melange"]) | set(ALLOW))
        assert not safety["safe_for_isolated_calculation"], label
    with pytest.raises(OR.Refused, match="safety preflight"):
        OR.run(tmp_path / "array.xlsx", sha(tmp_path / "array.xlsx"), tmp_path / "o.xlsx", allow=ALLOW, calculator=fake_calc(probe_results()))
    assert not (tmp_path / "o.xlsx").exists()


def test_mentat_policy_is_the_finalizers_function_set():
    assert "_XLFN.XLOOKUP" in OR.POLICIES["mentat"] and {"SUMIFS", "COUNTIFS", "IFERROR", "SEARCH", "ISNUMBER"} <= set(OR.POLICIES["mentat"])
    assert set(OR.POLICIES["melange"]) == {"_XLFN.LET", "_XLFN.MINIFS", "ABS", "AVERAGE", "AVERAGEIF", "COUNTIF", "COUNTIFS", "IF", "IFERROR",
                                           "INDEX", "MATCH", "MAX", "MIN", "N", "PERCENTRANK", "RANK", "SUM", "SUMIFS", "SUMPRODUCT",
                                           "TRUNC"}


def test_melange_policy_and_the_excel_era_preflight_list_move_in_lockstep():
    """fec/recalculate_formula_cache_copy.ps1 and POLICIES['melange'] are one list in two languages (AVERAGE and TRUNC were
    added to both on 2026-10-12): the container must not refuse a workbook the Excel path accepts, nor accept one it refuses."""
    ps1 = (ROOT / "fec" / "recalculate_formula_cache_copy.ps1").read_text(encoding="utf-8")
    block = re.search(r"\$script:AllowedFormulaFunctions\s*=\s*@\((.*?)\)", ps1, re.S).group(1)
    listed = re.findall(r"'([^']+)'", block)
    assert len(listed) == len(set(listed))
    assert set(listed) == set(OR.POLICIES["melange"]) and {"AVERAGE", "TRUNC"} <= set(listed)


# ---------------------------------------------------------------------------------------------------- the Excel fallback's forceFullCalc strip

PS1 = ROOT / "fec" / "recalculate_formula_cache_copy.ps1"
needs_powershell = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"), reason="Windows PowerShell only")


def test_ps1_strips_forcefullcalc_from_the_temporary_copy_before_excel_opens_it():
    """forceFullCalc="1" leaves Excel's CalculationState at 2, so the wait loop times out (900 s; proved 2026-10-13). The strip runs on the
    temporary copy, after the hash gate and the safety preflight have seen it as given and before Excel is even started."""
    ps1 = PS1.read_text(encoding="utf-8")
    call = ps1.index("Remove-ForceFullCalc -Path $workbookFullPath")
    assert ps1.index("Temporary copy hash does not match") < ps1.index("Get-WorkbookSafety -Path $workbookFullPath") < call
    assert call < ps1.index("[Activator]::CreateInstance") < ps1.index("$excel.Workbooks.Open(")
    assert "$engine.force_full_calc_removed" in ps1                                      # the receipt says whether it was there
    assert ps1.count("Workbooks.Open(") == 1 and ps1.count("$workbook.Save()") == 1      # the rest of the flow is unchanged


def run_strip(tmp_path, workbook):
    ps1 = PS1.read_text(encoding="utf-8")
    function = re.search(r"^function Remove-ForceFullCalc \{.*?^\}\n", ps1, re.S | re.M).group(0)
    script = tmp_path / "strip.ps1"
    script.write_text(function + f"\n[Console]::Out.Write((Remove-ForceFullCalc -Path '{workbook}'))\n", encoding="utf-8")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def package(path, calc_pr):
    parts = {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": ('<workbook xmlns="x"><sheets/>%s</workbook>' % calc_pr).encode("utf-8"),
             "xl/worksheets/sheet1.xml": b"<worksheet>" + bytes(range(256)) * 40 + b"</worksheet>", "xl/styles.xml": b"<styleSheet/>"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return parts


@needs_powershell
@pytest.mark.parametrize("calc_pr,expected", [
    ('<calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1" />', '<calcPr calcId="191029" fullCalcOnLoad="1" />'),       # the live workbook's
    ('<calcPr forceFullCalc="1" calcId="191029"/>', '<calcPr calcId="191029"/>'),
    ("<calcPr calcId='191029' forceFullCalc='true'/>", "<calcPr calcId='191029'/>")])
def test_the_strip_removes_that_attribute_and_nothing_else(tmp_path, calc_pr, expected):
    book = tmp_path / "copy.xlsx"
    before = package(book, calc_pr)
    assert run_strip(tmp_path, book) == "True"
    with zipfile.ZipFile(book) as z:
        assert z.testzip() is None and z.namelist() == list(before)                       # same parts, same order, a valid package
        assert z.read("xl/workbook.xml") == before["xl/workbook.xml"].replace(calc_pr.encode(), expected.encode())
        assert all(z.read(n) == body for n, body in before.items() if n != "xl/workbook.xml")
    assert run_strip(tmp_path, book) == "False"                                           # nothing left to strip


@needs_powershell
@pytest.mark.parametrize("calc_pr", ['<calcPr calcId="191029" fullCalcOnLoad="1"/>', '<calcPr calcId="191029" forceFullCalc="0"/>', "",
                                     '<other forceFullCalc="1"/>'])
def test_a_workbook_without_the_attribute_is_not_rewritten(tmp_path, calc_pr):
    book = tmp_path / "copy.xlsx"
    package(book, calc_pr)
    raw = book.read_bytes()
    assert run_strip(tmp_path, book) == "False" and book.read_bytes() == raw              # byte for byte, including the zip container


# ---------------------------------------------------------------------------------------------------- exit codes

def test_cli_exit_codes_and_the_input_is_never_touched(probe, tmp_path):
    out = tmp_path / "out.xlsx"
    base = ["--in", str(probe), "--out", str(out), "--allow-function", "_XLFN.XLOOKUP"]
    good = sha(probe)
    assert OR.main([*base, "--expect-sha", "0" * 64]) == OR.REFUSED                                     # wrong hash
    assert OR.main([*base, "--expect-sha", good, "--timeout", "5"]) == OR.REFUSED                       # timeout out of range
    assert OR.main(["--in", str(probe), "--out", str(probe), "--expect-sha", good]) == OR.REFUSED       # out == in
    # a clean input reaches the engine; there is no soffice here -> 4, and nothing is written
    assert OR.main([*base, "--expect-sha", good, "--soffice", str(tmp_path / "no-soffice")]) == OR.ENGINE
    assert not out.exists() and sha(probe) == good


def test_calculator_failures_map_to_their_exit_codes(probe, tmp_path):
    def failing(exc):
        def calc(*a, **k):
            raise exc
        return calc
    for exc, code in ((OR.Unmappable("Err:502"), OR.UNMAPPABLE), (OR.RecalcTimeout("slow"), OR.TIMEOUT), (OR.EngineError("x"), OR.ENGINE)):
        with pytest.raises(OR.RecalcError) as info:
            run_probe(probe, tmp_path / "o.xlsx", calculator=failing(exc))
        assert info.value.code == code and not (tmp_path / "o.xlsx").exists()
    assert not list(tmp_path.glob(".*.tmp"))                                                            # no temp file left behind


def test_a_wrong_result_set_fails_verification_not_silently(probe, tmp_path):
    partial = probe_results()
    del partial["Probe"]["B3"]
    with pytest.raises(OR.VerifyError):
        run_probe(probe, tmp_path / "o.xlsx", calculator=fake_calc(partial))
    assert not (tmp_path / "o.xlsx").exists()


def test_profile_registry_is_well_formed_and_turns_recalc_and_macros_off():
    root = ET.fromstring(OR.profile_xcu())
    props = {(i.get("{http://openoffice.org/2001/registry}path"), p.get("{http://openoffice.org/2001/registry}name")): p.find("value").text
             for i in root for p in i}
    assert props[("/org.openoffice.Office.Calc/Formula/Load", "OOXMLRecalcMode")] == "1"        # recalculation is calculateAll's job
    assert props[("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel")] == "3"
    assert props[("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution")] == "true"
    assert props[("/org.openoffice.Office.Common/Misc", "UseOpenCL")] == "false"
    assert OR.parse_version("LibreOffice 25.2.3.2 520(Build:2)") == (25, 2, 3)
    assert OR.parse_version("LibreOffice 24.8.0")[:2] >= OR.MIN_VERSION > OR.parse_version("LibreOffice 7.6.7.2")[:2]


# ---------------------------------------------------------------------------------------------------- the switch

def test_engine_defaults_to_excel_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv(E.ENGINE_ENV, raising=False)
    assert E.engine() == "excel"
    monkeypatch.setenv(E.ENGINE_ENV, "LibreOffice")
    assert E.engine() == "libreoffice" and E.engine("excel") == "excel"
    monkeypatch.setenv(E.ENGINE_ENV, "calc")
    with pytest.raises(ValueError):
        E.engine()


def test_excel_engine_runs_the_unchanged_ps1_in_place(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    wb = tmp_path / "c.xlsx"
    got = E.recalc_copy(wb, expected_sha256="a" * 64, receipt=tmp_path / "r.json", method="CalculateFullRebuild", timeout=900,
                        run=lambda argv, **kw: calls.append((argv, kw)))
    assert got == wb and calls[0][1] == {"check": True}
    argv = calls[0][0]
    assert argv[:6] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "fec" / "recalculate_formula_cache_copy.ps1")]
    assert argv[6:] == ["-WorkbookPath", str(wb), "-ExpectedSourceSha256", "a" * 64, "-ReceiptPath", str(tmp_path / "r.json"),
                        "-CalculationMethod", "CalculateFullRebuild", "-CalculationTimeoutSeconds", "900"]
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="needs Windows"):
        E.recalc_copy(wb, expected_sha256="a" * 64, receipt=tmp_path / "r.json", run=lambda *a, **k: None)


def test_libreoffice_engine_goes_through_the_container_by_digest_only(monkeypatch, tmp_path):
    calls = []
    run = lambda argv, **kw: calls.append((argv, kw))                                  # noqa: E731
    wb, out = tmp_path / "in" / "c.xlsx", tmp_path / "out" / "c.view.xlsx"
    kw = dict(expected_sha256="b" * 64, out=out, engine_name="libreoffice", timeout=300, allow_functions=["_XLFN.XLOOKUP"], run=run)
    monkeypatch.delenv(E.LOCAL_ENV, raising=False)
    monkeypatch.delenv(E.IMAGE_ENV, raising=False)
    with pytest.raises(RuntimeError, match="BY DIGEST"):
        E.recalc_copy(wb, **kw)
    monkeypatch.setenv(E.IMAGE_ENV, "ghcr.io/pjg93-222/office-calc:v2026.10.12.1")
    with pytest.raises(RuntimeError, match="BY DIGEST"):
        E.recalc_copy(wb, **kw)                                                         # a tag is refused
    monkeypatch.setenv(E.IMAGE_ENV, "ghcr.io/pjg93-222/office-calc@sha256:" + "c" * 64)
    assert E.recalc_copy(wb, **kw) == out
    argv, opts = calls[-1]
    assert argv[:7] == ["docker", "compose", "-f", str(E.COMPOSE), "--profile", "office", "run"] and "--rm" in argv
    assert argv[argv.index("--in") + 1] == "/in/c.xlsx" and argv[argv.index("--out") + 1] == "/out/c.view.xlsx"
    assert argv[argv.index("--receipt") + 1] == "/out/c.view.xlsx.receipt.json"
    assert argv[argv.index("--allow-function") + 1] == "_XLFN.XLOOKUP" and argv[argv.index("--timeout") + 1] == "300"
    assert opts["env"]["OFFICE_IN_DIR"] == str((tmp_path / "in").resolve()) and opts["env"]["OFFICE_OUT_DIR"] == str((tmp_path / "out").resolve())
    with pytest.raises(ValueError, match="view copy"):
        E.recalc_copy(wb, expected_sha256="b" * 64, engine_name="libreoffice", run=run)


def test_libreoffice_engine_can_run_the_script_directly_where_soffice_is_installed(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv(E.LOCAL_ENV, "1")
    E.recalc_copy(tmp_path / "c.xlsx", expected_sha256="d" * 64, out=tmp_path / "v.xlsx", engine_name="libreoffice",
                  run=lambda argv, **kw: calls.append(argv))
    assert calls[0][:2] == [sys.executable, str(OPS / "compute" / "office_recalc.py")] and "--receipt" in calls[0]


def test_engine_cli_prints_the_site_map_and_refuses_with_exit_2(monkeypatch, capsys, tmp_path):
    assert E.main(["sites"]) == 0
    out = capsys.readouterr().out
    assert all(site in out for site in E.SITES) and len(out.splitlines()) == 2 * len(E.SITES)
    monkeypatch.delenv(E.ENGINE_ENV, raising=False)
    assert E.main(["recalc", "--in", str(tmp_path / "c.xlsx"), "--expect-sha", "a" * 64, "--engine", "libreoffice"]) == 2   # no --out
    assert "view copy" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------- the Excel-site map

def test_every_excel_site_in_the_tree_has_a_mapped_replacement():
    pat = re.compile(E.CENSUS_PATTERN)
    found = set()
    for root in (ROOT / "fec", ROOT / "mentat"):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            rel = p.relative_to(root).parts
            if p.suffix in {".py", ".ps1"} and "tests" not in rel and "__pycache__" not in rel and p.is_file():
                if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                    found.add(p.relative_to(ROOT).as_posix())
    assert not found - set(E.SITES), f"Excel site(s) with no entry in ops/compute/xl_engine.py SITES: {sorted(found - set(E.SITES))}"
    assert {v["state"] for v in E.SITES.values()} <= {"switch", "gate", "gate-open", "no-port", "dead", "docstring", "mentat"}
    assert all(v["replacement"] for v in E.SITES.values())
    assert E.SITES["fec/recalculate_formula_cache_copy.ps1"]["state"] == "switch" and E.PS1.is_file()
    assert sum(1 for k in E.SITES if k.startswith("fec/") and "attic" not in k) >= 14                    # the 14 Melange files


# ---------------------------------------------------------------------------------------------------- the legfix5 gate branch

def _gate():
    import audit.audit_formula_cache_freshness as F
    return F


def _calc_pair(probe, tmp_path, rows):
    """Two independent LibreOffice-style runs into a temp root, as the audit expects them."""
    root = tmp_path / "C_tmp"
    root.mkdir()
    receipts = []
    for tag in "ab":
        out = root / f"calc-{tag}.xlsx"
        OR.run(probe, sha(probe), out, root / f"receipt-{tag}.json", allow=ALLOW, calculator=fake_calc(probe_results(rows)))
        receipts.append((out, root / f"receipt-{tag}.json"))
    return root, receipts


def test_audit_gate_accepts_a_libreoffice_receipt_and_rejects_tampering(tmp_path):
    F = _gate()
    rows = [r for r in X.PROBE if r[2] != "e"]                                           # the audit allows no cached errors
    src = tmp_path / "src" / "probe.xlsx"
    src.parent.mkdir()
    X.write_probe(src, rows)
    root, ((a, ra), (b, rb)) = _calc_pair(src, tmp_path, rows)
    ok = F.validate_engine_receipt(receipt_path=ra, workbook_path=a, expected_source_sha256=sha(src), temp_root=root)
    assert ok["engine"]["product"] == "LibreOffice" and ok["workbook"]["sha256"] == sha(a)
    snapshot, manifest, created = F.build_audit(source_path=src, calculated_a_path=a, calculated_b_path=b, receipt_a_path=ra,
                                                receipt_b_path=rb, out_root=tmp_path / "audit", expected_source_sha256=sha(src), temp_root=root)
    assert created and manifest["gates"]["temporary_recalculation_passed"] and manifest["gates"]["independent_formula_caches_identical"]
    for key, value in (("view_copy", False), ("canonical_workbook_written", True), ("input_unchanged", False)):
        r = json.loads(ra.read_text(encoding="utf-8"))
        r[key] = value
        bad = root / "bad.json"
        bad.write_text(json.dumps(r), encoding="utf-8")
        with pytest.raises(F.FormulaCacheAuditError):
            F.validate_engine_receipt(receipt_path=bad, workbook_path=a, expected_source_sha256=sha(src), temp_root=root)
    r = json.loads(ra.read_text(encoding="utf-8"))
    r["engine"]["version"] = "7.6.7.2"                                                    # too old for LET
    bad.write_text(json.dumps(r), encoding="utf-8")
    with pytest.raises(F.FormulaCacheAuditError, match="isolation"):
        F.validate_engine_receipt(receipt_path=bad, workbook_path=a, expected_source_sha256=sha(src), temp_root=root)
    with pytest.raises(F.FormulaCacheAuditError, match="source workbook hash"):
        F.validate_engine_receipt(receipt_path=ra, workbook_path=a, expected_source_sha256="0" * 64, temp_root=root)
    a.write_bytes(a.read_bytes() + b"x")
    with pytest.raises(F.FormulaCacheAuditError, match="drifted"):
        F.validate_engine_receipt(receipt_path=ra, workbook_path=a, expected_source_sha256=sha(src), temp_root=root)


def test_audit_gate_still_rejects_an_unknown_schema(tmp_path):
    F = _gate()
    (tmp_path / "r.json").write_text(json.dumps({"schema": "something-else", "status": "PASS_TEMP_COPY_RECALCULATED"}), encoding="utf-8")
    (tmp_path / "w.xlsx").write_bytes(b"x")
    with pytest.raises(F.FormulaCacheAuditError, match="wrong schema"):
        F.validate_engine_receipt(receipt_path=tmp_path / "r.json", workbook_path=tmp_path / "w.xlsx",
                                  expected_source_sha256="0" * 64, temp_root=tmp_path)


# ---------------------------------------------------------------------------------------------------- image, compose, workflow pins

def test_dockerfile_is_pinned_to_the_lock_and_the_snapshot_and_runs_non_root():
    lock = json.loads((OPS / "images.lock").read_text(encoding="utf-8"))["debian"]
    text = (OPS / "docker" / "office-calc.Dockerfile").read_text(encoding="utf-8")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", lock["digest"]) and re.fullmatch(r"debian:trixie-\d{8}-slim", lock["ref"])
    assert f"ARG DEBIAN_BASE={lock['ref']}@{lock['digest']}\n" in text                                  # never floats
    assert "bookworm" not in text.lower() and "USER 10001:10001" in text and "HEALTHCHECK NONE" in text
    assert "ARG GIT_SHA=unknown" in text and "ARG BUILD_TIME=unknown" in text and "PYTHONDONTWRITEBYTECODE=1" in text
    version = re.search(r"^ARG LIBREOFFICE_VERSION=4:(\d+)\.(\d+)\.", text, re.M)
    assert version and (int(version.group(1)), int(version.group(2))) >= OR.MIN_VERSION                 # LET and XLOOKUP
    assert re.search(r"^ARG SNAPSHOT=\d{8}T\d{6}Z$", text, re.M) and "snapshot.debian.org/archive/debian/${SNAPSHOT}/" in text
    assert "libreoffice-calc-nogui=${LIBREOFFICE_VERSION}" in text and "python3-uno=${LIBREOFFICE_VERSION}" in text
    assert 'test "$(dpkg-query -W -f=\'${Version}\' libreoffice-calc-nogui)" = "${LIBREOFFICE_VERSION}"' in text   # the pin is asserted
    assert "--no-install-recommends" in text and "pip install" not in text
    assert "ENTRYPOINT [\"/usr/bin/python3\", \"-I\", \"-B\", \"/opt/office-calc/office_recalc.py\"]" in text


def test_compose_file_meets_the_serve_compose_policy():
    cfg = yaml.safe_load((OPS / "compute" / "office-calc.compose.yaml").read_text(encoding="utf-8"))
    svc = cfg["services"]["office-calc"]
    assert security_gate.compose_findings(cfg) == []
    assert svc["network_mode"] == "none" and svc["profiles"] == ["office"] and svc["user"] == "10001:10001"
    assert [v["target"] for v in svc["volumes"] if v.get("read_only")] == ["/in"] and "ports" not in svc
    assert svc["image"].startswith("${IMAGE_OFFICE_CALC:?")                                              # no default tag to float on


def test_workflow_builds_by_digest_smokes_and_scans():
    raw = WF.read_text(encoding="utf-8")
    wf = yaml.safe_load(raw)
    assert wf[True]["push"]["tags"] == ["v*"] and "workflow_dispatch" in wf[True] and wf["permissions"] == {}
    assert set(wf["jobs"]) == {"test", "build", "sbom", "scan", "digests"} and wf["jobs"]["build"]["needs"] == "test"
    assert wf["jobs"]["sbom"]["needs"] == wf["jobs"]["scan"]["needs"] == "build"
    push = next(s for s in wf["jobs"]["build"]["steps"] if s.get("id") == "push")["with"]
    assert push["no-cache"] is True and push["provenance"] is False and "push=true" in push["outputs"]
    assert "DEBIAN_BASE=${{ steps.base.outputs.debian }}" in push["build-args"]
    for fn in ("SUM", "LET", "XLOOKUP", "PERCENTRANK"):
        assert any(fn == row[1].replace("_xlfn.", "").split("(")[0] for row in X.PROBE), fn            # the probe covers what the task names
    assert "probe-make" in raw and "probe-check" in raw and "--allow-function _XLFN.XLOOKUP" in raw
    assert "ghcr.io/pjg93-222/office-calc@${{ steps.image.outputs.digest }}" in raw                  # reused or just pushed
    assert "python ops/security_gate.py images --ci --compose-json compose.config.json" in raw and "anchore/sbom-action" in raw
    assert "sha256sum -c -" in raw and "ops/compute/office-calc.compose.yaml" in raw
    release = (WF.parent / "release.yml").read_text(encoding="utf-8")
    for action in set(re.findall(r"uses:\s*([\w./-]+@[0-9a-f]{40})", raw)):                              # same SHAs as release.yml
        assert action in release, action


def test_everything_the_new_workflow_and_image_read_survives_the_source_only_export():
    from ops.release_lib import export
    cfg = json.loads((OPS / "release_config.json").read_text(encoding="utf-8"))["export"]
    text = "\n".join(p.read_text(encoding="utf-8") for p in (WF, OPS / "docker" / "office-calc.Dockerfile", OPS / "compute" / "office-calc.compose.yaml"))
    needed = {p for p in re.findall(r"\bops/[A-Za-z0-9_./-]*[A-Za-z0-9_]", text) if (ROOT / p).is_file()}
    needed |= {"ops/github/workflows/compute-office.yml", "ops/docker/office-calc.Dockerfile"}
    assert {"ops/compute/office_recalc.py", "ops/compute/xl_equivalence.py", "ops/compute/office-calc.compose.yaml", "ops/images.lock",
            "ops/security_gate.py"} <= needed
    dropped = sorted(p for p in needed if not export.allowed_name(export.remap(p), cfg))
    assert not dropped, f"the export would drop {dropped}"
