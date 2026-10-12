from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec" / "legislation" / "sync_sentinel_bill_ledger.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sentinel_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_archive_helper(name):
    """Return one pure helper from build_archive_legislative.py without running it.

    That producer is a script -- importing it reads the archive workbook and writes
    artifacts -- so the function is compiled out of the source instead.
    """
    fec = str(ROOT / "fec")
    if fec not in sys.path:
        sys.path.insert(0, fec)
    import bill_rules

    source = (ROOT / "fec" / "legislation" / "build_archive_legislative.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            module = ast.Module(body=[node], type_ignores=[])
            namespace = {"BR": bill_rules, "pd": pd}
            exec(compile(module, "<build_archive_legislative>", "exec"), namespace)
            return namespace[name]
    raise AssertionError("build_archive_legislative.py defines no %s()" % name)


def test_plan_tracks_canonical_ids_and_only_verified_rows_score(tmp_path, monkeypatch):
    module = load_module()
    repository_path = tmp_path / "repository.json"
    repository_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "REPOSITORY", repository_path)
    repository = {"bills": {"119:hr:7": {
        "key": "119:hr:7", "display": "H.R.7", "congress": 119,
        "type": "hr", "number": 7, "title": "Test Act",
        "sponsor": {"name": "Rep. Sponsor"}, "cosponsor_count": 3,
        "introduced_date": "2025-01-03", "update_date": "2026-09-01",
        "latest_action": {"date": "2026-08-31", "text": "Introduced"},
        "policy_area": "Finance", "source": "GovInfo",
        "url": "https://www.congress.gov/bill/119th-congress/house-bill/7",
        "summary": "Summary", "updated_at": "2026-09-02T00:00:00Z",
    }}}
    updates = [{
        "repository_key": "119:hr:7", "member": "Member One",
        "bioguide": "M000001", "role": "original", "join_date": "2025-01-03",
        "areas": ["Payments"], "source": "GovInfo", "verified_at": "2026-09-02T00:00:00Z",
    }]
    pending = [{
        "repository_key": "119:hr:7", "member": "Member Two",
        "role": "cosponsor", "reason": "needs review", "verified_at": "2026-09-02T00:00:00Z",
    }]
    plan = module.build_plan(repository, updates, pending,
                             discovery=[{"canonical_key":"119:s:9","display_number":"S.9","congress":119,"type":"s","number":9,"candidate_status":"included_unreviewed_candidate"}],
                             adjudications=[],
                             input_path=tmp_path / "in.xlsx", output_path=tmp_path / "out.xlsx")
    assert plan["counts"] == {
        "repository_bills": 1, "verified_memberships": 1,
        "verified_interaction_rows": 1, "pending_memberships": 1,
        "ledger_rows_excluding_header": 4,
        "discovery_candidates": 1, "discovery_only_ids": 1,
        "discovery_pending_review": 1,
    }
    header = plan["ledger_rows"][0]
    rows = [dict(zip(header, row)) for row in plan["ledger_rows"][1:]]
    assert {row["Canonical Bill ID"] for row in rows} == {"119:hr:7", "119:s:9"}
    assert sum(bool(row["Scoring Eligible"]) for row in rows) == 1
    pending_rows = [row for row in rows if row["Review Status"] == "pending review"]
    assert len(pending_rows) == 1
    assert pending_rows[0]["Scoring Eligible"] is False
    assert plan["accepted_interactions"][0]["sheet"] == "Original Co-Sponsor Interaction"
    assert plan["accepted_interactions"][0]["values"][1] == "original-cosponsor"


def test_pipeline_orders_workbook_sync_before_legislative_consumers():
    source = (ROOT / "fec" / "run_pipeline.py").read_text(encoding="utf-8")
    verify = source.index("('verify_collector_roster_updates.py',[])")
    sync = source.index("('sync_sentinel_bill_ledger.py',[])")
    archive = source.index("('build_archive_legislative.py',[])")
    stance = source.index("('per_category_stance.py',[])")
    pillars = source.index("('compute_pillars.py',['--write'])")
    assert verify < sync < archive < stance < pillars

    weekly = (ROOT / "fec" / "weekly_refresh.py").read_text(encoding="utf-8")
    discovery = weekly.index("'legislation/refresh_legislation_discovery.py'")
    repository = weekly.index("'legislation/update_bill_repository.py'", discovery)
    full_pipeline = weekly.index("'run_pipeline.py'", repository)
    assert discovery < repository < full_pipeline


def test_archive_builder_appends_scoring_eligible_rows_to_legacy_archive():
    source = (ROOT / "fec" / "legislation" / "build_archive_legislative.py").read_text(encoding="utf-8")
    assert "sheet_name='Sentinel Bill Ledger'" in source
    assert "'Scoring Eligible'" in source
    assert "sentinel_scoring_mask(_sentinel)" in source
    assert "text_column('Review Status').eq('verified')" in source
    assert "text_column('Record Type').eq('membership')" in source
    assert "_sentinel[_eligible]" in source
    # The eligible rows must actually reach the scored frame, not just be selected.
    assert "A=pd.concat([A,_verified[A.columns]],ignore_index=True)" in source


def test_workbook_writer_is_artifact_tool_only():
    source = (ROOT / "fec" / "sync_sentinel_bill_ledger_artifact.cjs").read_text(encoding="utf-8")
    assert '@oai/artifact-tool' in source
    assert "Workbook.create" in source
    assert "SpreadsheetFile.exportXlsx" in source
    assert "input_plan_sha256" in source
    assert "output_sha256" in source
    assert "openpyxl" not in source


def test_runtime_resolution_returns_none_when_no_runtime_exists():
    """A runtime-absent box must produce a skip signal, never an exception."""
    module = load_module()
    with tempfile.TemporaryDirectory() as home:
        missing = Path(home) / "definitely-absent"
        saved_env = {key: os.environ.get(key) for key in
                     ("ARTIFACT_TOOL_NODE", "ARTIFACT_TOOL_NODE_MODULES", "HOME", "USERPROFILE")}
        saved_root = module.CODEX_RUNTIME_ROOT
        try:
            os.environ["HOME"] = home
            os.environ["USERPROFILE"] = home
            os.environ["ARTIFACT_TOOL_NODE"] = str(missing / "node.exe")
            os.environ["ARTIFACT_TOOL_NODE_MODULES"] = str(missing / "node_modules")
            module.CODEX_RUNTIME_ROOT = missing / "cache"
            config = missing / "artifact_tool_runtime.json"
            assert not config.exists()
            candidates = module.runtime_candidates(config)
            assert [source for source, _node, _modules in candidates] == ["env", "codex_cache"]
            assert module.runtime(config) is None
        finally:
            module.CODEX_RUNTIME_ROOT = saved_root
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_install_target_is_locked_to_the_canonical_ledger():
    """--workbook may only ever name the ledger this script is allowed to replace."""
    module = load_module()
    assert module.LEDGER_BASENAME == "Sentinel Bill Ledger.xlsx"
    assert module.WORKBOOK.name == module.LEDGER_BASENAME
    saved_argv = sys.argv
    try:
        sys.argv = ["sync_sentinel_bill_ledger.py", "--workbook", "C:\\tmp\\Some Other Book.xlsx"]
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                module.main()
        except SystemExit as exit_code:
            assert exit_code.code == 2, exit_code.code
        else:
            raise AssertionError("a non-sentinel --workbook was accepted")
        assert "Sentinel Bill Ledger.xlsx" in stderr.getvalue()
    finally:
        sys.argv = saved_argv


def _decay_plan(module, tmp_root):
    repository_path = Path(tmp_root) / "repository.json"
    repository_path.write_text("{}", encoding="utf-8")
    saved = module.REPOSITORY
    bills = {}
    for congress, number in ((118, 11), (117, 22)):
        bills["%d:hr:%d" % (congress, number)] = {
            "key": "%d:hr:%d" % (congress, number), "display": "H.R.%d" % number,
            "congress": congress, "type": "hr", "number": number, "title": "Test Act",
            "sponsor": {"name": "Rep. Sponsor"}, "cosponsor_count": 1,
            "introduced_date": "2023-01-03", "update_date": "2026-09-01",
            "latest_action": {"date": "2026-08-31", "text": "Introduced"},
            "policy_area": "Finance", "source": "GovInfo", "url": "https://example.invalid",
            "summary": "Summary", "updated_at": "2026-09-02T00:00:00Z",
        }
    updates = [{"repository_key": key, "member": "Member One", "bioguide": "M000001",
                "role": "original", "join_date": "2023-01-03", "areas": ["Payments"],
                "source": "GovInfo", "verified_at": "2026-09-02T00:00:00Z"}
               for key in sorted(bills)]
    try:
        module.REPOSITORY = repository_path
        return module.build_plan({"bills": bills}, updates, [],
                                 discovery=[], adjudications=[],
                                 input_path=Path(tmp_root) / "in.xlsx",
                                 output_path=Path(tmp_root) / "out.xlsx")
    finally:
        module.REPOSITORY = saved


def test_ledger_and_interaction_rows_carry_per_congress_decay():
    """Sentinel rows must use the archive's rule, not a literal 1.0."""
    module = load_module()
    import bill_rules

    with tempfile.TemporaryDirectory() as tmp_root:
        plan = _decay_plan(module, tmp_root)
    header = plan["ledger_rows"][0]
    assert "DecayWeight" in header
    rows = [dict(zip(header, row)) for row in plan["ledger_rows"][1:]]
    by_congress = {}
    for row in rows:
        by_congress.setdefault(int(row["Congress"]), set()).add(row["DecayWeight"])
    assert by_congress[118] == {bill_rules.congress_decay(118)}, by_congress
    assert by_congress[117] == {bill_rules.congress_decay(117)}, by_congress
    # The literal the old code shipped, and the reason this test exists.
    assert bill_rules.congress_decay(118) != 1.0
    assert bill_rules.congress_decay(119) == 1.0

    weights = {values[9]: values[10] for values in
               (record["values"] for record in plan["accepted_interactions"])}
    assert weights["118"] == bill_rules.congress_decay(118), weights
    assert weights["117"] == bill_rules.congress_decay(117), weights


def test_archive_decay_fill_computes_from_congress_when_blank():
    """A ledger written before the DecayWeight column must not score at full weight."""
    decay_weight_for = load_archive_helper("decay_weight_for")
    import bill_rules

    assert decay_weight_for(118, None) == bill_rules.congress_decay(118)
    assert decay_weight_for(118, "") == bill_rules.congress_decay(118)
    assert decay_weight_for(117, "nan") == bill_rules.congress_decay(117)
    assert decay_weight_for(119, None) == 1.0
    # A stored value always wins over the computed one.
    assert decay_weight_for(118, 0.5) == 0.5
    assert decay_weight_for(118, "0.8") == 0.8
    # An unparseable Congress with no stored weight stays neutral rather than crashing.
    assert decay_weight_for("", None) == 1.0


def _update(module, *, key="119:hr:7", bioguide="M000001", member="Member One",
            role="cosponsor", areas=None):
    return module.normalize_history_record({
        "repository_key": key,
        "bill": "H.R.7",
        "congress": "119",
        "member": member,
        "bioguide": bioguide,
        "role": role,
        "join_date": "2026-08-31",
        "areas": ["Payments"] if areas is None else areas,
        "title": "Test Act",
        "summary": "Summary",
        "source": "congress.gov BILLSTATUS via bill_repository_live",
        "verified_at": "2026-09-02T03:47:30Z",
        "repository_sha256": "a" * 64,
    }, kind="test", source_id="test", source_path="test.ndjson",
       source_sha256="b" * 64)


def test_history_preserves_prior_rows_appends_current_delta_and_is_idempotent():
    module = load_module()
    prior = _update(module)
    current = _update(
        module, key="119:s:9", bioguide="M000002", member="Member Two",
        role="original", areas=["Payments", "Fraud Prevention"],
    )
    first, first_stats = module.merge_verified_history(
        [prior], ("collector_verifier_delta", [current])
    )
    assert len(first) == 2
    assert module._history_interaction_count(first) == 3
    assert first_stats["batches"]["collector_verifier_delta"]["added_memberships"] == 1
    assert {row["bioguide"] for row in first} == {"M000001", "M000002"}

    second, second_stats = module.merge_verified_history(
        first, ("collector_verifier_delta", [current])
    )
    assert second == first
    assert second_stats["batches"]["collector_verifier_delta"] == {
        "candidate_memberships": 1,
        "candidate_interaction_rows": 2,
        "added_memberships": 0,
        "added_interaction_rows": 0,
        "duplicate_memberships": 1,
    }


def test_member_role_key_and_category_expansion_do_not_double_count():
    module = load_module()
    cosponsor = _update(module, areas=["Payments", "Payments"])
    same_key_new_category = _update(module, areas=["Fraud Prevention"])
    original = _update(module, role="original", areas=["Payments"])
    rows, stats = module.merge_verified_history(
        [], ("delta", [cosponsor, cosponsor, same_key_new_category, original, original])
    )
    assert len(rows) == 2  # one row per bill/person/role, not one row per run/category
    assert len({row["membership_key"] for row in rows}) == 2
    assert module._history_interaction_count(rows) == 3
    by_role = {row["role"]: row["areas"] for row in rows}
    assert by_role == {
        "cosponsor": ["Fraud Prevention", "Payments"],
        "original": ["Payments"],
    }
    assert stats["batches"]["delta"]["duplicate_memberships"] == 2


def test_hash_bound_bootstrap_recovers_196_memberships_and_201_interactions():
    """Prove the on-disk pre-regression PASS output remains recoverable."""
    module = load_module()
    if not module.HISTORY_BOOTSTRAP.is_file():
        pytest.skip("repository bootstrap receipt is unavailable")
    candidates, sources = module.recover_hash_bound_bootstrap(
        module.ROOT, module.HISTORY_BOOTSTRAP
    )
    historical = next(
        source for source in sources
        if source["source_id"] == "sentinel_bill_ledger_20260904T011145Z"
    )
    assert historical["verified_memberships"] == 196
    assert historical["verified_interaction_rows"] == 201
    rows, _stats = module.merge_verified_history([], ("bootstrap", candidates))
    assert len(rows) == 199
    assert module._history_interaction_count(rows) == 204


def test_bootstrap_refuses_a_workbook_without_a_complete_hash_receipt(tmp_path):
    module = load_module()
    plan = tmp_path / "plan.json"
    report = tmp_path / "report.json"
    workbook = tmp_path / module.LEDGER_BASENAME
    plan.write_text("{}", encoding="utf-8")
    report.write_text("{}", encoding="utf-8")
    workbook.write_bytes(b"not-a-workbook")
    manifest = tmp_path / "bootstrap.ndjson"
    source = {
        "schema": module.HISTORY_BOOTSTRAP_SCHEMA,
        "status": "PASS",
        "source_id": "untrusted",
        "expected_counts": {"verified_memberships": 0, "verified_interaction_rows": 0},
        "plan": {"path": plan.name, "sha256": module.sha256(plan)},
        "artifact_report": {"path": report.name, "sha256": module.sha256(report)},
        # Missing hash is dispositive even though a file happens to exist.
        "workbook": {"path": workbook.name, "sha256": ""},
    }
    manifest.write_text(json.dumps(source) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no hash-bound workbook"):
        module.recover_hash_bound_bootstrap(tmp_path, manifest)


def test_materialized_history_bytes_stay_stable_across_delta_reruns(tmp_path):
    module = load_module()
    updates = tmp_path / "updates.ndjson"
    history = tmp_path / "history.ndjson"
    receipt = tmp_path / "receipt.json"
    raw = {
        "repository_key": "119:hr:7", "bill": "H.R.7", "congress": "119",
        "member": "Member One", "bioguide": "M000001", "role": "cosponsor",
        "join_date": "2026-08-31", "areas": ["Payments"],
        "verified_at": "2026-09-02T03:47:30Z",
    }
    updates.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    first, _ = module.materialize_verified_history(
        root=tmp_path, history_path=history, updates_path=updates,
        bootstrap_path=tmp_path / "absent.ndjson", receipt_path=receipt,
    )
    first_bytes = history.read_bytes()
    second, _ = module.materialize_verified_history(
        root=tmp_path, history_path=history, updates_path=updates,
        bootstrap_path=tmp_path / "absent.ndjson", receipt_path=receipt,
    )
    assert second == first
    assert history.read_bytes() == first_bytes


def _materialize_one_history(module, tmp_path):
    updates = tmp_path / "updates.ndjson"
    history = tmp_path / "history.ndjson"
    receipt = tmp_path / "history_receipt.json"
    raw = {
        "repository_key": "119:hr:7", "bill": "H.R.7", "congress": "119",
        "member": "Member One", "bioguide": "M000001", "role": "cosponsor",
        "join_date": "2026-08-31", "areas": ["Payments"],
        "verified_at": "2026-09-02T03:47:30Z",
    }
    updates.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    rows, document = module.materialize_verified_history(
        root=tmp_path, history_path=history, updates_path=updates,
        bootstrap_path=tmp_path / "absent.ndjson", receipt_path=receipt,
    )
    return updates, history, receipt, rows, document


def test_nonempty_existing_history_requires_its_prior_pass_receipt(tmp_path):
    module = load_module()
    history = tmp_path / "history.ndjson"
    history.write_text(
        json.dumps(_update(module), sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="no prior PASS receipt"):
        module.materialize_verified_history(
            root=tmp_path, history_path=history,
            updates_path=tmp_path / "updates.ndjson",
            bootstrap_path=tmp_path / "absent.ndjson",
            receipt_path=tmp_path / "missing_receipt.json",
        )


def test_tampered_existing_history_fails_receipt_hash_validation(tmp_path):
    module = load_module()
    updates, history, receipt, _rows, _document = _materialize_one_history(
        module, tmp_path
    )
    with history.open("a", encoding="utf-8") as stream:
        stream.write(" \n")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        module.materialize_verified_history(
            root=tmp_path, history_path=history, updates_path=updates,
            bootstrap_path=tmp_path / "absent.ndjson", receipt_path=receipt,
        )


def test_forged_row_fails_even_with_a_rehashed_receipt(tmp_path):
    module = load_module()
    updates, history, receipt, rows, document = _materialize_one_history(
        module, tmp_path
    )
    rows[0]["admission"]["source_sha256"] = "forged"
    history.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    document["history"]["sha256"] = module.sha256(history)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="valid source_sha256"):
        module.materialize_verified_history(
            root=tmp_path, history_path=history, updates_path=updates,
            bootstrap_path=tmp_path / "absent.ndjson", receipt_path=receipt,
        )


def test_well_formed_but_unanchored_provenance_is_rejected(tmp_path):
    module = load_module()
    updates, history, receipt, rows, document = _materialize_one_history(
        module, tmp_path
    )
    rows[0]["admission"]["source_sha256"] = "d" * 64
    history.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    document["history"]["sha256"] = module.sha256(history)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not anchored"):
        module.validate_existing_history(history, receipt)


def test_rerun_preserves_cumulative_bootstrap_and_source_receipts(tmp_path):
    module = load_module()
    updates, history, receipt, _rows, document = _materialize_one_history(
        module, tmp_path
    )
    historic = {
        "source_id": "historic-pass", "verified_memberships": 1,
        "verified_interaction_rows": 1,
        "plan": {"path": "historic-plan.json", "sha256": "b" * 64},
        "workbook": {"path": "historic.xlsx", "sha256": "c" * 64},
    }
    historic_source = {"kind": "hash_bound_bootstrap", **historic}
    document["bootstrap_sources"] = [historic]
    document["source_receipts"] = [historic_source]
    receipt.write_text(json.dumps(document), encoding="utf-8")
    _rows, rerun = module.materialize_verified_history(
        root=tmp_path, history_path=history, updates_path=updates,
        bootstrap_path=tmp_path / "absent.ndjson", receipt_path=receipt,
    )
    assert historic in rerun["bootstrap_sources"]
    assert historic_source in rerun["source_receipts"]
    assert rerun["prior_receipt_sha256"]


def test_history_publication_audit_reconciles_receipts_and_interaction_tabs(tmp_path):
    module = load_module()
    _updates, history, history_receipt, _rows, history_document = _materialize_one_history(
        module, tmp_path
    )
    artifact_report = tmp_path / "artifact_report.json"
    artifact_report.write_text(json.dumps({
        "schema": "sentinel-bill-workbook-artifact-report-v1",
        "status": "PASS",
        "input_plan_sha256": "f" * 64,
        "output_sha256": "e" * 64,
        "appended": {
            "Original Co-Sponsor Interaction": 0,
            "Co-Sponsor Interactions": 1,
        },
    }), encoding="utf-8")
    workbook_receipt = tmp_path / "workbook_receipt.json"
    workbook_receipt.write_text(json.dumps({
        "schema": "sentinel-bill-workbook-receipt-v1", "status": "PASS",
        "counts": {"verified_memberships": 1, "verified_interaction_rows": 1},
        "verified_history": history_document["history"],
        "artifact_report": str(artifact_report),
        "artifact_report_sha256": module.sha256(artifact_report),
        "plan": {"sha256": "f" * 64},
        "output": {"sha256": "e" * 64},
    }), encoding="utf-8")
    result = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )
    assert result["checks"] == {
        "history_contract": (True, None),
        "workbook_reconciliation": (True, None),
        "interaction_tabs_reconciliation": (True, None),
    }
    assert result["errors"] == []

    receipt_document = json.loads(workbook_receipt.read_text(encoding="utf-8"))
    receipt_document["counts"]["verified_memberships"] = 2
    workbook_receipt.write_text(json.dumps(receipt_document), encoding="utf-8")
    mismatch = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )
    assert mismatch["checks"]["history_contract"] == (True, None)
    assert mismatch["checks"]["workbook_reconciliation"][0] is False

    receipt_document["counts"]["verified_memberships"] = 1
    report_document = json.loads(artifact_report.read_text(encoding="utf-8"))
    report_document["appended"]["Co-Sponsor Interactions"] = 2
    artifact_report.write_text(json.dumps(report_document), encoding="utf-8")
    receipt_document["artifact_report_sha256"] = module.sha256(artifact_report)
    workbook_receipt.write_text(json.dumps(receipt_document), encoding="utf-8")
    tab_mismatch = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )
    assert tab_mismatch["checks"]["history_contract"] == (True, None)
    assert tab_mismatch["checks"]["workbook_reconciliation"] == (True, None)
    assert tab_mismatch["checks"]["interaction_tabs_reconciliation"][0] is False


def test_history_publication_runtime_skip_reconciles_without_regenerating_tabs(tmp_path):
    module = load_module()
    _updates, history, history_receipt, _rows, history_document = _materialize_one_history(
        module, tmp_path
    )
    workbook_receipt = tmp_path / "workbook_receipt.json"
    workbook_receipt.write_text(json.dumps({
        "schema": "sentinel-bill-workbook-receipt-v1",
        "status": "SKIPPED_RUNTIME_ABSENT",
        "verified_history": history_document["history"],
    }), encoding="utf-8")

    result = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )

    assert module.history_publication_state(
        history, history_receipt, workbook_receipt
    ) == "PRESENT"
    assert result["checks"] == {
        "history_contract": (True, None),
        "workbook_reconciliation": (True, None),
        "interaction_tabs_reconciliation": (
            True,
            "skipped: runtime absent, interaction tabs not regenerated this cycle",
        ),
    }


def test_history_publication_all_absent_is_not_run_yet(tmp_path):
    module = load_module()
    paths = (
        tmp_path / "history.ndjson",
        tmp_path / "history_receipt.json",
        tmp_path / "workbook_receipt.json",
    )

    result = module.audit_history_publication(*paths)

    assert module.history_publication_state(*paths) == "NOT_RUN"
    assert result["checks"] == {
        "history_contract": (True, "not run yet"),
        "workbook_reconciliation": (True, "not run yet"),
        "interaction_tabs_reconciliation": (True, "not run yet"),
    }


def test_history_publication_empty_history_with_zero_pass_counts_is_inconsistent(tmp_path):
    module = load_module()
    history = tmp_path / "history.ndjson"
    history_receipt = tmp_path / "history_receipt.json"
    workbook_receipt = tmp_path / "workbook_receipt.json"
    history.write_text("", encoding="utf-8")
    history_receipt.write_text("{}", encoding="utf-8")
    workbook_receipt.write_text(json.dumps({
        "schema": "sentinel-bill-workbook-receipt-v1", "status": "PASS",
        "counts": {"verified_memberships": 0, "verified_interaction_rows": 0},
    }), encoding="utf-8")

    result = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )

    note = "verified membership history is empty"
    assert module.history_publication_state(
        history, history_receipt, workbook_receipt
    ) == "INCONSISTENT"
    assert result["checks"] == {
        "history_contract": (False, note),
        "workbook_reconciliation": (False, note),
        "interaction_tabs_reconciliation": (False, note),
    }


def test_history_publication_missing_workbook_receipt_is_inconsistent(tmp_path):
    module = load_module()
    _updates, history, history_receipt, _rows, _document = _materialize_one_history(
        module, tmp_path
    )
    workbook_receipt = tmp_path / "missing_workbook_receipt.json"

    result = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )

    note = f"sentinel workbook receipt path {workbook_receipt}: missing"
    assert module.history_publication_state(
        history, history_receipt, workbook_receipt
    ) == "INCONSISTENT"
    assert result["checks"] == {
        "history_contract": (False, note),
        "workbook_reconciliation": (False, note),
        "interaction_tabs_reconciliation": (False, note),
    }


def test_history_publication_existing_directory_is_inconsistent(tmp_path):
    module = load_module()
    history = tmp_path / "history.ndjson"
    history.mkdir()
    history_receipt = tmp_path / "history_receipt.json"
    workbook_receipt = tmp_path / "workbook_receipt.json"

    result = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )
    note = result["checks"]["history_contract"][1]

    assert module.history_publication_state(
        history, history_receipt, workbook_receipt
    ) == "INCONSISTENT"
    assert f"verified membership history path {history}: directory" in note
    assert f"verified membership history receipt path {history_receipt}: missing" in note
    assert f"sentinel workbook receipt path {workbook_receipt}: missing" in note
    assert all(not check[0] for check in result["checks"].values())


def test_history_publication_rejects_live_like_stale_workbook_counts(tmp_path):
    module = load_module()
    history = tmp_path / "sentinel_verified_membership_history.ndjson"
    history_receipt = tmp_path / "sentinel_verified_membership_history_receipt.json"
    workbook_receipt = tmp_path / "sentinel_bill_workbook_receipt.json"
    source = tmp_path / "synthetic_source.ndjson"
    source.write_text("synthetic source\n", encoding="utf-8")
    source_id = "synthetic-history"
    source_sha256 = module.sha256(source)
    rows = [
        _update(module, bioguide="M000001", member="Member One"),
        _update(module, bioguide="M000002", member="Member Two"),
    ]
    for row in rows:
        for admission in [row["admission"], *row["area_admissions"]]:
            admission["source_id"] = source_id
            admission["source_path"] = str(source.resolve())
            admission["source_sha256"] = source_sha256
    history.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    history_block = {
        "path": str(history.resolve()),
        "sha256": module.sha256(history),
        "verified_memberships": 2,
        "verified_interaction_rows": 2,
    }
    history_receipt.write_text(json.dumps({
        "schema": module.HISTORY_RECEIPT_SCHEMA,
        "status": "PASS",
        "history": history_block,
        "source_receipts": [{
            "kind": "synthetic_test", "source_id": source_id,
            "path": str(source.resolve()), "sha256": source_sha256,
        }],
    }), encoding="utf-8")
    workbook_receipt.write_text(json.dumps({
        "schema": "sentinel-bill-workbook-receipt-v1", "status": "PASS",
        "counts": {"verified_memberships": 1, "verified_interaction_rows": 1},
        "verified_history": dict(history_block),
    }), encoding="utf-8")

    result = module.audit_history_publication(
        history, history_receipt, workbook_receipt
    )
    workbook_document = json.loads(workbook_receipt.read_text(encoding="utf-8"))

    assert workbook_document["verified_history"]["path"] == history_block["path"]
    assert workbook_document["verified_history"]["sha256"] == history_block["sha256"]
    assert workbook_document["counts"]["verified_memberships"] == 1
    assert workbook_document["counts"]["verified_interaction_rows"] == 1
    assert result["metrics"]["verified_memberships"] == 2
    assert result["metrics"]["verified_interaction_rows"] == 2
    assert result["checks"]["history_contract"] == (True, None)
    assert result["checks"]["workbook_reconciliation"] == (
        False, "sentinel workbook receipt counts do not reconcile to verified history",
    )


def test_interaction_dedup_key_includes_bioguide_identity(tmp_path, monkeypatch):
    module = load_module()
    repository_path = tmp_path / "repository.json"
    repository_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "REPOSITORY", repository_path)
    repository = {"bills": {"119:hr:7": {
        "key": "119:hr:7", "display": "H.R.7", "congress": 119,
        "type": "hr", "number": 7, "title": "Test Act",
        "sponsor": {"name": "Rep. Sponsor"}, "cosponsor_count": 3,
        "introduced_date": "2025-01-03", "update_date": "2026-09-01",
        "latest_action": {"date": "2026-08-31", "text": "Introduced"},
        "policy_area": "Finance", "source": "GovInfo",
        "url": "https://www.congress.gov/bill/119th-congress/house-bill/7",
        "summary": "Summary", "updated_at": "2026-09-02T00:00:00Z",
    }}}
    updates = [
        _update(module, bioguide="M000001", member="Same Display Name"),
        _update(module, bioguide="M000002", member="Same Display Name"),
    ]
    plan = module.build_plan(
        repository, updates, [], discovery=[], adjudications=[],
        input_path=tmp_path / "in.xlsx", output_path=tmp_path / "out.xlsx",
    )

    def interaction_key(record):
        values = record["values"]
        return "\x1f".join(str(value) for value in (
            record["_bioguide"], values[0], values[1], values[2], values[3], values[9],
        )).lower()

    interactions = plan["accepted_interactions"]
    assert len(interactions) == 2
    assert interactions[0]["values"] == interactions[1]["values"]
    assert {record["_bioguide"] for record in interactions} == {"M000001", "M000002"}
    assert all(len(record["values"]) == 13 for record in interactions)
    assert len({interaction_key(record) for record in interactions}) == 2


def test_full_audit_invokes_all_cumulative_history_gates():
    source = (ROOT / "fec" / "audit" / "audit_full.py").read_text(encoding="utf-8")
    assert "from legislation.sync_sentinel_bill_ledger import audit_history_publication" not in "\n".join(source.splitlines()[:20])
    assert "    from legislation.sync_sentinel_bill_ledger import audit_history_publication" in source
    assert "_sbh_detail=str(_sbh_exc)[:300]" in source
    assert "if _sbl_block_raw is not None and not isinstance(_sbl_block_raw,dict):" in source
    assert "except Exception as _sbl_exc:" in source
    assert source.count("print('   note:") == 3
    assert "audit_history_publication(" in source
    assert "sentinel verified history receipt/hash/counts and unique keys" in source
    assert "sentinel workbook receipt reconciles to cumulative verified history" in source
    assert "sentinel generated interaction tabs reconcile to cumulative verified history" in source


def test_downstream_mask_requires_verified_scoring_eligible_membership():
    mask = load_archive_helper("sentinel_scoring_mask")
    frame = pd.DataFrame([
        {"Record Type": "membership", "Review Status": "verified", "Scoring Eligible": True},
        {"Record Type": "membership", "Review Status": "pending review", "Scoring Eligible": True},
        {"Record Type": "bill", "Review Status": "verified", "Scoring Eligible": True},
        {"Record Type": "membership", "Review Status": "verified", "Scoring Eligible": False},
    ])
    assert list(mask(frame)) == [True, False, False, False]
