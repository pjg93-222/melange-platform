"""The presync audit's formula-cache check (fec/legislation/run_legislation_presync_audit.py) judges a receipt by the engine that
wrote it (D-174): Excel receipts by the rules they always had, LibreOffice receipts by the lane's own.

Every fixture here is a real audit tree built by audit_formula_cache_freshness.build_audit over a tiny synthetic workbook (N
formulas; the expected count is patched to N), so the validator reads exactly what it reads in production. The Excel half pins
today's behaviour (accept, and the refusals the validator already made); the LibreOffice half is the new branch."""
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
ROOT = OPS.parent
FEC = ROOT / "fec"
sys.path.insert(0, str(OPS / "compute"))
sys.path.insert(0, str(FEC))
import office_recalc as OR  # noqa: E402
import xl_equivalence as X  # noqa: E402

SPEC = importlib.util.spec_from_file_location("presync_engine_kinds", FEC / "legislation" / "run_legislation_presync_audit.py")
PRESYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PRESYNC
SPEC.loader.exec_module(PRESYNC)
AUD = PRESYNC.FORMULA_CACHE_FRESHNESS

FORMULAS = {"B1": "SUM(Data!A1:A4)", "B2": "MAX(Data!A1:A4)", "B3": "MIN(Data!A1:A4)", "B4": "IF(Data!A1>5,1,0)",
            "B5": "ABS(-3)", "B6": "SUM(Data!A1:A2)"}
RESULTS = {"B1": 100.0, "B2": 40.0, "B3": 10.0, "B4": 1.0, "B5": 3.0, "B6": 30.0}
N = len(FORMULAS)


@pytest.fixture(autouse=True)
def small_count(monkeypatch):
    monkeypatch.setattr(PRESYNC, "FORMULA_CACHE_EXPECTED_FORMULA_COUNT", N)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_book(path):
    """The live workbook's shape in miniature: formulas whose stored caches are blank (<v />), as an openpyxl save leaves them."""
    skeleton = Path(path).with_name("skeleton.xlsx")
    X.write_probe(skeleton)
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="%s"><sheetData>' % OR.MAIN_NS
    data = head + '<row r="1"><c r="A1"><v>10</v></c></row><row r="2"><c r="A2"><v>20</v></c></row>' \
                  '<row r="3"><c r="A3"><v>30</v></c></row><row r="4"><c r="A4"><v>40</v></c></row></sheetData></worksheet>'
    probe = head + "".join(f'<row r="{i}"><c r="{a}"><f>{f}</f><v /></c></row>' for i, (a, f) in enumerate(FORMULAS.items(), 1)) \
        + "</sheetData></worksheet>"
    with zipfile.ZipFile(skeleton) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            body = zin.read(info)
            body = data.encode() if info.filename == "xl/worksheets/sheet1.xml" else probe.encode() if info.filename == "xl/worksheets/sheet2.xml" else body
            zi = zipfile.ZipInfo(info.filename, info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, body)
    skeleton.unlink()
    return Path(path)


def fake_calc(copy, cells, **kw):
    return {"Data": {}, "Probe": {a: ("n", v) for a, v in RESULTS.items()}}, {
        "product": "LibreOffice", "version": "25.2.3.2", "version_string": "LibreOffice 25.2.3.2 fake",
        "automation_security": "MacroExecutionMode.NEVER_EXECUTE", "update_links": 0, "enable_events": False, "display_alerts": False,
        "ask_to_update_links": False, "visible": False, "read_only": True, "load_recalc_mode": "never",
        "calculation_request": "calculateAll", "calculation_completed": True, "calculate_before_save": False, "document_stored": False,
        "threaded_formula_groups": False, "opencl": False, "document_settings": dict(OR.DOC_SETTINGS)}      # all of libreoffice_calculate's facts


def repackage(path, stamp, restyle=False):
    """The same package with other zip dates: identical XML, different file bytes. restyle=True also rewrites the style catalog,
    as Excel does on save: the copy is then no longer 'exact semantics preserved' against the source (Excel's two saves agree
    with each other, not with the source)."""
    tmp = Path(path).with_name(Path(path).name + ".re")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            body = zin.read(info)
            if restyle and info.filename == "xl/styles.xml":
                body = body.replace(b'<sz val="11"/>', b'<sz val="10"/>')
            zout.writestr(zi, body)
    tmp.replace(path)


def excel_receipt(source, workbook):
    item = OR.file_item(workbook)
    safety, _ = OR.scan_package(source, set(OR.POLICIES["melange"]))
    return {"schema": AUD.ENGINE_SCHEMA, "status": "PASS_TEMP_COPY_RECALCULATED", "source_expected_sha256": sha(source),
            "temporary_copy_before": {"path": str(workbook), "bytes": Path(source).stat().st_size, "sha256": sha(source)},
            "temporary_copy_after": item,
            "safety_preflight": {**{k: v for k, v in safety.items() if k != "safe_for_isolated_calculation"},
                                 "safe_for_isolated_excel_calculation": True},
            "engine": {"version": "16.0", "automation_security": "msoAutomationSecurityForceDisable", "update_links": 0,
                       "enable_events": False, "display_alerts": False, "ask_to_update_links": False, "visible": False,
                       "calculation_request": "CalculateFull", "calculation_state_after": 0, "ready_after": True,
                       "calculate_before_save": False},
            "live_or_production_workbook_opened": False, "live_or_production_workbook_written": False,
            "macro_execution_allowed": False, "external_link_updates_allowed": False, "external_connections_allowed": False,
            "sync_or_promotion_performed": False}


def build(root, kind_a="libreoffice", kind_b=None, *, differ_b=False, one_receipt=False, restyle_all=False, excel_restyle=True):
    """An audit tree under root; returns {'pointer', 'source', 'copy_a', 'copy_b', 'receipt_a', 'receipt_b'}."""
    kind_b = kind_b or kind_a
    live, tmp = Path(root) / "live", Path(root) / "tmp"
    live.mkdir(parents=True)
    tmp.mkdir()
    source = source_book(live / "book.xlsx")
    paths = {"source": source}
    for x, kind in (("a", kind_a), ("b", kind_b)):
        copy, receipt = tmp / f"calc_{x}.xlsx", tmp / f"receipt_{x}.json"
        OR.run(source, sha(source), copy, receipt, calculator=fake_calc)
        if kind == "excel" or restyle_all or differ_b and x == "b":
            repackage(copy, (2027, 2, 3 if x == "a" else 4, 4, 5, 6), restyle=kind == "excel" and excel_restyle or restyle_all)   # Excel's saves never share bytes
        body = json.loads(receipt.read_text())
        if kind == "excel":
            body = excel_receipt(source, copy)
        else:
            body["temporary_copy_after"] = OR.file_item(copy)
        receipt.write_text(json.dumps(body, indent=2, sort_keys=True))
        paths.update({f"copy_{x}": copy, f"receipt_{x}": receipt})
    if one_receipt:
        paths["receipt_b"] = paths["receipt_a"]
    out = Path(root) / "formula_cache_freshness"
    AUD.build_audit(source_path=source, calculated_a_path=paths["copy_a"], calculated_b_path=paths["copy_b"],
                    receipt_a_path=paths["receipt_a"], receipt_b_path=paths["receipt_b"], out_root=out,
                    expected_source_sha256=sha(source), temp_root=tmp)
    paths["pointer"] = out / "current.json"
    return paths


def judge(paths):
    return PRESYNC.validate_formula_cache_freshness_observation(paths["pointer"])


def mutate(path, fn):
    body = json.loads(Path(path).read_text())
    fn(body)
    Path(path).write_text(json.dumps(body, indent=2, sort_keys=True))


def mutate_manifest(paths, edit):
    """Edit the audit manifest and re-seal the pointer's receipt of it, as the fec fixtures do."""
    pointer = json.loads(Path(paths["pointer"]).read_text())
    mutate(pointer["manifest"]["path"], edit)
    pointer["manifest"] = OR.file_item(Path(pointer["manifest"]["path"]))
    Path(paths["pointer"]).write_text(json.dumps(pointer, indent=2, sort_keys=True))


def has(state, text):
    return any(text in issue for issue in state["issues"])


def test_the_expected_formula_count_is_the_current_workbooks():
    assert PRESYNC.__dict__["FORMULA_CACHE_EXPECTED_FORMULA_COUNT"] == N                               # patched by the fixture
    src = (FEC / "legislation" / "run_legislation_presync_audit.py").read_text(encoding="utf-8")
    assert "FORMULA_CACHE_EXPECTED_FORMULA_COUNT = 98_269\n" in src.replace("\r\n", "\n")            # 2026-10-12 census of the workbook


# ---------------------------------------------------------------------------------------------------- Excel: unchanged

def test_excel_receipts_are_accepted_as_before(tmp_path):
    paths = build(tmp_path, "excel")
    ok, state = judge(paths)
    assert ok and state["issues"] == [] and state["status"] == "CURRENT_INFORMATIONAL"
    assert state["formula_closure"]["engine_kind"] == "excel" and state["formula_closure"]["formula_count"] == N
    assert sha(paths["copy_a"]) != sha(paths["copy_b"]) and state["formula_closure"]["deterministic_cache_digests_match"]
    assert state["outputs"]["calculated_copy_a"]["sha256"] != state["outputs"]["calculated_copy_b"]["sha256"]


def test_excel_copies_must_still_be_distinct_files(tmp_path):
    paths = build(tmp_path, "excel")
    paths["copy_b"].write_bytes(paths["copy_a"].read_bytes())                  # two saves that are byte-identical
    ok, state = judge(paths)
    assert not ok and has(state, "calculated copies must be distinct files")
    assert not has(state, "byte-identical")                                    # the LibreOffice rule does not leak into Excel's


@pytest.mark.parametrize("label,edit,message", [
    ("schema", lambda r: r.update(schema="isolated-something-else-v1"), "schema/status drifted"),
    ("status", lambda r: r.update(status="FAILED"), "schema/status drifted"),
    ("no excel safety flag", lambda r: r["safety_preflight"].pop("safe_for_isolated_excel_calculation"), "safety preflight drifted"),
    ("libreoffice safety flag only", lambda r: r["safety_preflight"].update(
        {"safe_for_isolated_calculation": True, "safe_for_isolated_excel_calculation": False}), "safety preflight drifted"),
    ("excel isolation", lambda r: r["engine"].update(ready_after=False), "engine isolation/completion drifted"),
    ("libreoffice isolation block", lambda r: r.update(engine={"product": "LibreOffice", "version": "25.2.3.2",
                                                               "automation_security": "MacroExecutionMode.NEVER_EXECUTE",
                                                               "update_links": 0, "enable_events": False, "display_alerts": False,
                                                               "ask_to_update_links": False, "visible": False, "read_only": True,
                                                               "calculation_request": "calculateAll", "calculation_completed": True,
                                                               "calculate_before_save": False, "document_stored": False}),
     "engine isolation/completion drifted"),
    ("live write", lambda r: r.update(live_or_production_workbook_written=True), "must be False"),
])
def test_excel_receipts_keep_their_refusals(tmp_path, label, edit, message):
    paths = build(tmp_path, "excel")
    mutate(paths["receipt_a"], edit)
    ok, state = judge(paths)
    assert not ok and has(state, message), (label, state["issues"])


def test_an_excel_receipt_is_still_bound_to_the_calculated_copys_path(tmp_path):
    paths = build(tmp_path, "excel")
    mutate(paths["receipt_a"], lambda r: r["temporary_copy_after"].update(path=str(tmp_path / "elsewhere.xlsx")))
    ok, state = judge(paths)
    assert not ok and has(state, "output receipt binding drifted")


# ---------------------------------------------------------------------------------------------------- LibreOffice: the new branch

def test_libreoffice_receipts_with_byte_identical_copies_are_accepted(tmp_path):
    paths = build(tmp_path, "libreoffice")
    assert sha(paths["copy_a"]) == sha(paths["copy_b"]) and paths["copy_a"] != paths["copy_b"]
    assert paths["receipt_a"] != paths["receipt_b"]
    ok, state = judge(paths)
    assert ok and state["issues"] == [] and state["status"] == "CURRENT_INFORMATIONAL" and state["evidence_current"]
    closure = state["formula_closure"]
    assert closure["engine_kind"] == "libreoffice" and closure["formula_count"] == N and closure["formula_error_count"] == 0
    assert closure["deterministic_cache_digests_match"] and closure["verified_output_receipt_count"] == 4
    assert closure["live_source_bytes_unchanged"] and all(state["retained_blockers"].values())
    assert state["outputs"]["calculated_copy_a"]["sha256"] == state["outputs"]["calculated_copy_b"]["sha256"]
    assert state["informational_only"] and not state["affects_gate_decisions"] and not state["calculated_copy_promotion_eligible"]


def test_libreoffice_copies_that_differ_are_refused(tmp_path):
    ok, state = judge(build(tmp_path, "libreoffice", differ_b=True))
    assert not ok and has(state, "LibreOffice calculated copies must be byte-identical")
    assert not has(state, "must be distinct files")


def test_libreoffice_needs_two_distinct_receipt_files(tmp_path):
    with pytest.raises(AUD.FormulaCacheAuditError, match="distinct workbook and receipt files"):   # the builder refuses one receipt
        build(tmp_path / "builder", "libreoffice", one_receipt=True)
    paths = build(tmp_path / "validator", "libreoffice")                                             # and the validator still does
    mutate_manifest(paths, lambda m: m["independent_calculations"]["b"].update(receipt=m["independent_calculations"]["a"]["receipt"]))
    ok, state = judge(paths)
    assert not ok and has(state, "LibreOffice calculation receipts must be two distinct files")


def test_a_libreoffice_copy_equal_to_the_source_is_refused(tmp_path):
    paths = build(tmp_path, "libreoffice")
    for x in "ab":
        paths[f"copy_{x}"].write_bytes(paths["source"].read_bytes())          # "calculated" copies that are the unchanged source
    ok, state = judge(paths)
    assert not ok and has(state, "formula-cache calculation was not materialized")


@pytest.mark.parametrize("label,edit,message", [
    ("excel isolation block", lambda r: r.update(engine={"automation_security": "msoAutomationSecurityForceDisable", "update_links": 0,
                                                        "enable_events": False, "display_alerts": False, "ask_to_update_links": False,
                                                        "visible": False, "calculation_request": "CalculateFull",
                                                        "calculation_state_after": 0, "ready_after": True,
                                                        "calculate_before_save": False}), "engine isolation/completion drifted"),
    ("old libreoffice", lambda r: r["engine"].update(version="24.2.1"), "engine isolation/completion drifted"),
    ("not read only", lambda r: r["engine"].update(read_only=False), "engine isolation/completion drifted"),
    ("document stored", lambda r: r["engine"].update(document_stored=True), "engine isolation/completion drifted"),
    ("macros allowed", lambda r: r["engine"].update(automation_security="MacroExecutionMode.ALWAYS_EXECUTE"),
     "engine isolation/completion drifted"),
    ("excel safety flag only", lambda r: r["safety_preflight"].update(
        {"safe_for_isolated_excel_calculation": True, "safe_for_isolated_calculation": False}), "safety preflight drifted"),
    ("array formulas", lambda r: r["safety_preflight"].update(array_formula_count=1), "safety preflight drifted"),
    ("unsafe formula", lambda r: r["safety_preflight"].update(unsafe_formula_examples=[{"function": "CALL"}]), "safety preflight drifted"),
    ("wrong formula count", lambda r: r["safety_preflight"].update(formula_count=N + 1), "safety preflight drifted"),
    ("canonical workbook written", lambda r: r.update(canonical_workbook_written=True), "must be False"),
    ("not a view copy", lambda r: r.update(view_copy=False), "untouched input plus a view copy"),
    ("input changed", lambda r: r.update(input_unchanged=False), "untouched input plus a view copy"),
    ("live write", lambda r: r.update(live_or_production_workbook_written=True), "must be False"),
    ("promotion", lambda r: r.update(sync_or_promotion_performed=True), "must be False"),
    ("other source", lambda r: r.update(source_expected_sha256="0" * 64), "does not bind the unchanged live source bytes"),
    ("output hash", lambda r: r["temporary_copy_after"].update(sha256="0" * 64), "output receipt binding drifted"),
    ("output size", lambda r: r["temporary_copy_after"].update(bytes=1), "output receipt binding drifted"),
    ("schema", lambda r: r.update(schema="isolated-something-else-v1"), "schema/status drifted"),
])
def test_libreoffice_receipts_are_refused_when_their_own_rules_fail(tmp_path, label, edit, message):
    paths = build(tmp_path, "libreoffice")
    mutate(paths["receipt_a"], edit)
    ok, state = judge(paths)
    assert not ok and has(state, message), (label, state["issues"])


def test_a_libreoffice_receipt_is_bound_by_hash_not_by_path(tmp_path):
    """The calculator ran in a container: its recorded paths (/in, /out) are not the host's, and must not matter."""
    paths = build(tmp_path, "libreoffice")
    mutate(paths["receipt_a"], lambda r: (r["temporary_copy_before"].update(path="/in/book.xlsx"),
                                          r["temporary_copy_after"].update(path="/out/calc_a.xlsx")))
    ok, state = judge(paths)
    assert not has(state, "output receipt binding drifted")                    # (the receipt file's own hash did move: that is reported)
    assert has(state, "receipt hash drifted") and not ok


def test_mixed_engine_kinds_are_refused(tmp_path):
    ok, state = judge(build(tmp_path, "libreoffice", "excel", restyle_all=True))      # (both copies restyled: the audit itself must build)
    assert not ok and has(state, "mix engine kinds") and state["formula_closure"]["engine_kind"] == "mixed"


# ---------------------------------------------------------------------------------------------------- the gates a copy can never loosen

def test_excel_copies_must_still_differ_from_the_source_in_exact_semantics(tmp_path):
    """Excel rewrites the package on save; an 'Excel' audit that says the copy preserved the source exactly is refused, as before."""
    ok, state = judge(build(tmp_path, "excel", excel_restyle=False))
    assert not ok and has(state, "gates.postsave_candidate_exact_semantics_preserved must be False")
    assert has(state, "calculated_copy_promotion_eligibility blocker was not retained")


def test_a_libreoffice_view_copy_may_record_exact_semantics_preserved(tmp_path):
    paths = build(tmp_path, "libreoffice")
    manifest = json.loads(Path(json.loads(paths["pointer"].read_text())["manifest"]["path"]).read_text())
    assert manifest["gates"]["postsave_candidate_exact_semantics_preserved"] is True       # the view copy is the source plus caches
    assert judge(paths)[0]


@pytest.mark.parametrize("label,edit,message", [
    ("promotion eligible", lambda m: m["gates"].update(postsave_candidate_promotion_eligible=True), "postsave_candidate_promotion_eligible must be False"),
    ("freshness proven", lambda m: m["gates"].update(live_stored_formula_cache_freshness_proven=True),
     "live_stored_formula_cache_freshness_proven must be False"),
    ("exact semantics not recorded", lambda m: m["gates"].update(postsave_candidate_exact_semantics_preserved=None),
     "postsave_candidate_exact_semantics_preserved must be recorded"),
    ("blocker dropped", lambda m: m.update(unresolved=[u for u in m["unresolved"] if u["surface"] != "postsave_candidate_exact_semantics"]),
     "unresolved surfaces were dropped"),
    ("authority claimed", lambda m: m.update(authoritative_for_sync_or_promotion=True), "grants authority"),
])
def test_a_libreoffice_audit_still_cannot_promote_or_satisfy_a_blocker(tmp_path, label, edit, message):
    paths = build(tmp_path, "libreoffice")
    mutate_manifest(paths, edit)
    ok, state = judge(paths)
    assert not ok and has(state, message), (label, state["issues"])
    assert state["status"] == "STALE_OR_INVALID" and not state["evidence_current"] and not state["can_satisfy_blocked_gate"]
