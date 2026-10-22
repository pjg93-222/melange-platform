"""Isolated regression coverage for the recurring collector-to-publication job.

No collector, scheduler, live workbook, or publication is mutated by these tests.
The publication fixtures use the real receipt schemas and hash relationships.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import admission_budget
import chain_transaction
from legislation import refresh_congress_pipeline as pipeline
from audit_failure_gate import KNOWN_GATED_FAILURES
from pipeline_lock import LOCK_OWNER_PID_ENV, LOCK_TOKEN_ENV, pipeline_run_lock


REPOSITORY_RECEIPT = Path("data/engine/congress_bill_refresh_receipt.json")
WORKBOOK_RECEIPT = Path("data/engine/sentinel_bill_workbook_receipt.json")
SOURCE_RECEIPT = Path("data/engine/source_to_dashboard_receipt.json")
ALIAS_RECEIPT = Path("data/engine/live_dashboard_alias_receipt.json")
MEDIA_RECEIPT = Path("data/engine/daily_media_refresh_receipt.json")
HISTORY = Path("data/members/sentinel_verified_membership_history.ndjson")
PUBLICATIONS = (
    "bill_repository_live.json", "Sentinel Bill Ledger.xlsx",
    "Moneyball Main Version - Blended.xlsx", "archive_leg.json",
    "ranks_python.json", "dashboard_data.json", "member_desk_bundle.json",
    "Moneyball.html", "Moneyball_Redesign.html", "legislation_routing_audit.json",
    "data/money/fec_refresh_current.json", "data/engine/congestion.json",
)


@pytest.fixture(autouse=True)
def clean_inherited_lease(monkeypatch):
    monkeypatch.delenv(LOCK_TOKEN_ENV, raising=False)
    monkeypatch.delenv(LOCK_OWNER_PID_ENV, raising=False)
    monkeypatch.delenv(BUDGET_ENV, raising=False)    # the shell's own budget must never change a pinned argv
    monkeypatch.delenv("CONGRESS_REDERIVE", raising=False)
    monkeypatch.setattr(pipeline, "_emit", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "run_pipeline", SimpleNamespace(preflight_dependencies=lambda: None))
    monkeypatch.setitem(sys.modules, "legislation.sync_sentinel_bill_ledger",
                        SimpleNamespace(runtime=lambda: ("fixture", "node", "modules")))
    monkeypatch.setattr(chain_transaction, "preflight", lambda root: None)    # the 30 GB floor is the host disk, not the fixture


def write_json(root, relative, value):
    pipeline.write(root / relative, value)


def install_generation(root):
    for name in PUBLICATIONS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture for " + name, encoding="utf-8")
    write_json(root, "ranks_python.json", {"rank": {"Member A": 2, "Member B": 1}})
    (root / "Moneyball_Redesign.html").write_bytes((root / "Moneyball.html").read_bytes())
    write_json(root, REPOSITORY_RECEIPT, {
        "schema": "congress-bill-refresh-receipt-v1", "status": "PASS",
        "seeded": 1, "refreshed": 1, "failed": 0,
        "repository": {"sha256": pipeline.digest(root / "bill_repository_live.json")},
    })
    write_json(root, WORKBOOK_RECEIPT, {
        "schema": "sentinel-bill-workbook-receipt-v1", "status": "PASS",
        "installed": True,
        "repository_sha256": pipeline.digest(root / "bill_repository_live.json"),
        "output": {"sha256": pipeline.digest(root / "Sentinel Bill Ledger.xlsx")},
    })
    write_json(root, SOURCE_RECEIPT, {
        "schema": "source-to-dashboard-receipt-v1", "status": "PASS", "errors": [],
        "inputs_sha256": {name: pipeline.digest(root / name)
                          for name in PUBLICATIONS if name != "Sentinel Bill Ledger.xlsx"},
    })
    write_json(root, ALIAS_RECEIPT, {
        "schema": "live-dashboard-alias-v1", "status": "PASS",
        "sha256": pipeline.digest(root / "Moneyball.html"),
        "source": str(root / "Moneyball.html"),
        "alias": str(root / "Moneyball_Redesign.html"),
        "prepublication_source_guard": "PASS",
    })


@pytest.fixture
def generation(tmp_path):
    install_generation(tmp_path)
    return tmp_path


def test_complete_generation_is_accepted_and_records_artifact_hashes(generation):
    result = pipeline.validate_publication(generation, 0)
    assert result["Sentinel Bill Ledger.xlsx"]["sha256"] == pipeline.digest(generation / "Sentinel Bill Ledger.xlsx")
    assert result["Moneyball_Redesign.html"]["sha256"] == pipeline.digest(generation / "Moneyball.html")


@pytest.mark.parametrize("relative", [REPOSITORY_RECEIPT, WORKBOOK_RECEIPT, SOURCE_RECEIPT])
def test_partial_receipts_cannot_authorize_publication(generation, relative):
    receipt = pipeline.read(generation / relative)
    receipt["status"] = "PARTIAL"
    write_json(generation, relative, receipt)
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


@pytest.mark.parametrize("relative", [REPOSITORY_RECEIPT, WORKBOOK_RECEIPT, SOURCE_RECEIPT])
def test_wrong_receipt_schema_cannot_authorize_publication(generation, relative):
    receipt = pipeline.read(generation / relative)
    receipt["schema"] = "unrelated-pass-receipt"
    write_json(generation, relative, receipt)
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


@pytest.mark.parametrize("relative", ["bill_repository_live.json", "Sentinel Bill Ledger.xlsx"])
def test_repository_and_workbook_hash_drift_is_rejected(generation, relative):
    (generation / relative).write_text("changed after certification", encoding="utf-8")
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


@pytest.mark.parametrize("relative", ["ranks_python.json", "dashboard_data.json", "Moneyball Main Version - Blended.xlsx"])
def test_source_receipt_cannot_certify_changed_publication(generation, relative):
    (generation / relative).write_text("changed after certification", encoding="utf-8")
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


@pytest.mark.parametrize("bindings", [None, {}])
def test_source_receipt_requires_input_hash_bindings(generation, bindings):
    receipt = pipeline.read(generation / SOURCE_RECEIPT)
    if bindings is None:
        receipt.pop("inputs_sha256")
    else:
        receipt["inputs_sha256"] = bindings
    write_json(generation, SOURCE_RECEIPT, receipt)
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


def test_workbook_must_be_installed(generation):
    receipt = pipeline.read(generation / WORKBOOK_RECEIPT)
    receipt["installed"] = False
    write_json(generation, WORKBOOK_RECEIPT, receipt)
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


def test_stale_generation_and_stale_alias_are_rejected(generation):
    os.utime(generation / "dashboard_data.json", (1, 1))
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 2)
    (generation / "Moneyball_Redesign.html").write_text("old served dashboard", encoding="utf-8")
    with pytest.raises(RuntimeError):
        pipeline.validate_publication(generation, 0)


def test_unchanged_alias_is_allowed_with_fresh_hash_bound_alias_receipt(generation):
    os.utime(generation / "Moneyball_Redesign.html", (1, 1))
    assert pipeline.validate_publication(generation, 2)


@pytest.mark.parametrize("failure", ["stale", "wrong_hash", "missing"])
def test_unchanged_alias_still_requires_current_certification(generation, failure):
    os.utime(generation / "Moneyball_Redesign.html", (1, 1))
    if failure == "stale":
        os.utime(generation / ALIAS_RECEIPT, (1, 1))
    elif failure == "missing":
        (generation / ALIAS_RECEIPT).unlink()
    else:
        receipt = pipeline.read(generation / ALIAS_RECEIPT)
        receipt["sha256"] = "0" * 64
        write_json(generation, ALIAS_RECEIPT, receipt)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        pipeline.validate_publication(generation, 2)


def test_clean_workspace_shell_is_accepted_only_while_bound_to_this_generation(generation):
    from surfaces import sync_live_dashboard_alias as alias
    (generation / "Moneyball_Redesign.html").write_bytes(b"<!doctype html>\n<!-- melange-clean-workspace-v2 -->\n")
    shell = pipeline.digest(generation / "Moneyball_Redesign.html")
    receipt = pipeline.read(generation / ALIAS_RECEIPT)
    receipt.update(mode=alias.CLEAN_MODE, sha256=shell, source_sha256=pipeline.digest(generation / "Moneyball.html"),
                   workspace_inputs_sha256=alias.clean_inputs(generation))
    write_json(generation, ALIAS_RECEIPT, receipt)
    source = pipeline.read(generation / SOURCE_RECEIPT)
    source["inputs_sha256"]["Moneyball_Redesign.html"] = shell
    write_json(generation, SOURCE_RECEIPT, source)
    assert pipeline.validate_publication(generation, 0)["Moneyball_Redesign.html"]["sha256"] == shell
    receipt["workspace_inputs_sha256"] = {}
    write_json(generation, ALIAS_RECEIPT, receipt)
    with pytest.raises(RuntimeError, match="does not match the rebuilt dashboard"):
        pipeline.validate_publication(generation, 0)


def audit_output(names=None, *, marker="audit_full.py", tally=None):
    names = [KNOWN_GATED_FAILURES[0]] if names is None else names
    count = len(names) if tally is None else tally
    return (f"  {marker:<29} !!  4.2s\n"
            f"FULL-BODY AUDIT - 202 passed, {count} FAILED\n"
            f"FAILURES: {names!r}\n")


def test_acknowledged_audit_is_accepted_only_with_complete_final_verdict():
    accepted, names = pipeline.acknowledged_audit_only(audit_output())
    assert accepted and names == [KNOWN_GATED_FAILURES[0]]


@pytest.mark.parametrize("output", [
    audit_output(["new unexpected stale source"]),
    audit_output(tally=2),
    audit_output(marker="build_dashboard_bundle.py"),
    "  audit_full.py !!\nFAILURES: ['truncated",
])
def test_novel_truncated_and_producer_failures_are_rejected(output):
    assert pipeline.acknowledged_audit_only(output)[0] is False


def test_audit_exception_requires_audit_to_remain_last(tmp_path, monkeypatch):
    (tmp_path / "run_pipeline.py").write_text(
        "CHAIN = [('audit_full.py', []), ('another_producer.py', [])]", encoding="utf-8")
    monkeypatch.setattr(pipeline, "CODE", tmp_path)
    assert pipeline.acknowledged_audit_only(audit_output())[0] is False


def test_collector_chain_reaches_workbook_before_dashboard():
    selected = pipeline.steps(13)
    names = [step[0] for step in selected]
    assert names.index("calibration") < names.index("discovery") < names.index("admissions")
    assert names.index("admissions") < names.index("repository") < names.index("pipeline")
    assert dict((name, args) for name, _, args, _ in selected)["admissions"] == [
        "--limit", "13", "--max-classifier-calls", "48"]
    pipeline_args = next(args for name, _, args, _ in selected if name == "pipeline")
    entry = pipeline_args[pipeline_args.index("--from") + 1]
    tree = ast.parse((pipeline.CODE / "run_pipeline.py").read_text(encoding="utf-8"))
    chain = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "CHAIN" for t in n.targets))
    producers = [name for name, _ in chain]
    assert entry == "verify_collector_roster_updates.py"
    assert producers.index(entry) < producers.index("sync_sentinel_bill_ledger.py")
    assert producers.index("sync_sentinel_bill_ledger.py") < producers.index("build_dashboard_bundle.py")
    assert producers.index("build_dashboard_bundle.py") < producers.index("audit_source_to_dashboard.py")
    assert producers[-1] == "audit_full.py"


class CompletedProcess:
    pid = 8765

    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode


@pytest.mark.parametrize("known_audit_failure", [False, True])
def test_run_holds_shared_lease_backs_up_history_and_publishes_receipt(tmp_path, monkeypatch, known_audit_failure):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1, "Member B": 2}})
    (tmp_path / HISTORY).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / HISTORY).write_text('{"member": "prior verified member"}\n', encoding="utf-8")
    seen = []

    def launch(command, *, cwd, env, stdout, stderr):
        lease = pipeline.read(tmp_path / ".pipeline_run.lock")
        assert cwd == tmp_path
        assert env[LOCK_TOKEN_ENV] == lease["token"]
        assert env[LOCK_OWNER_PID_ENV] == str(lease["pid"])
        assert env["MONEYBALL_ROOT"] == str(tmp_path)
        seen.append(Path(command[1]).name)
        if seen[-1] == "run_pipeline.py":
            install_generation(tmp_path)
            if known_audit_failure:
                stdout.write(audit_output())
                return CompletedProcess(1)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path) == 0
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    assert receipt["status"] == "PASS"
    assert receipt["rank_changes"] == 2
    assert receipt["acknowledged_audit_findings"] == ([KNOWN_GATED_FAILURES[0]] if known_audit_failure else [])
    assert [r["step"] for r in receipt["steps"]] == [s[0] for s in pipeline.steps(32)]
    assert (Path(receipt["run_dir"]) / "before" / HISTORY).read_bytes() == (tmp_path / HISTORY).read_bytes()
    assert not (tmp_path / ".pipeline_run.lock").exists()
    assert pipeline.read(tmp_path / pipeline.STATUS) == receipt


def test_busy_shared_writer_does_not_overwrite_active_status(tmp_path, monkeypatch):
    original = {"status": "RUNNING", "owner_pid": os.getpid(), "active_step": "weekly"}
    write_json(tmp_path, pipeline.STATUS, original)
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start collectors"))
    with pipeline_run_lock(tmp_path / ".pipeline_run.lock", owner_kind="weekly_refresh"):
        with pytest.raises(RuntimeError, match="already running"):
            pipeline.run(tmp_path)
        assert pipeline.read(tmp_path / pipeline.STATUS) == original
        assert not (tmp_path / "audits/congress_pipeline").exists()


def test_failed_collector_halts_before_publication(tmp_path, monkeypatch):
    seen = []

    def launch(command, **kwargs):
        seen.append(Path(command[1]).name)
        return CompletedProcess(4)

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["status"] == "FAILED"
    assert "exit 4" in status["error"]
    assert seen == ["calibration_guard.py"]
    assert not (tmp_path / pipeline.RECEIPT).exists()
    assert not (tmp_path / ".pipeline_run.lock").exists()


@pytest.mark.parametrize("prerequisite", ["python", "excel"])
def test_missing_runtime_stops_before_collecting_any_sources(tmp_path, monkeypatch, prerequisite):
    def unavailable():
        raise RuntimeError("missing Python dependency")

    if prerequisite == "python":
        monkeypatch.setitem(sys.modules, "run_pipeline", SimpleNamespace(preflight_dependencies=unavailable))
    else:
        monkeypatch.setitem(sys.modules, "legislation.sync_sentinel_bill_ledger", SimpleNamespace(runtime=lambda: None))
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run a collector"))
    assert pipeline.run(tmp_path) == 1
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED" and state["steps"] == []
    assert not (tmp_path / ".pipeline_run.lock").exists()


def test_chain_disk_floor_refusal_stops_the_run_before_any_step(tmp_path, monkeypatch):
    def refuse(root):
        raise RuntimeError("chain transaction refused to start: floor")

    monkeypatch.setattr(chain_transaction, "preflight", refuse)
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run a collector"))
    assert pipeline.run(tmp_path) == 1
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED" and state["steps"] == []
    assert "refused to start" in state["error"]
    assert not (tmp_path / ".pipeline_run.lock").exists()


@pytest.mark.parametrize("interruption", [False, True])
def test_timeout_and_interruption_terminate_child_and_release_lease(tmp_path, monkeypatch, interruption):
    class PendingProcess(CompletedProcess):
        def wait(self, timeout=None):
            if interruption:
                raise KeyboardInterrupt("interrupted")
            raise subprocess.TimeoutExpired("fixture", timeout)

    process = PendingProcess(None)
    terminated = []
    # run start, before-image start, before-image end, step start, then the one
    # in-loop read that must exceed the 10s step budget.
    ticks = iter([0, 0, 0, 0, 20])
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=lambda: next(ticks)))
    monkeypatch.setattr(pipeline, "steps", lambda limit, include_media=False, smoke=False: [("calibration", "calibration_guard.py", [], 10)])
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(pipeline, "terminate_tree", lambda child: terminated.append(child.pid))
    assert pipeline.run(tmp_path) == 1
    assert terminated == [process.pid]
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED"
    assert ("KeyboardInterrupt" if interruption else "TimeoutError") in state["error"]
    assert not (tmp_path / ".pipeline_run.lock").exists()


def test_dead_recorded_owner_reports_interrupted_without_rewriting_status(tmp_path, monkeypatch):
    original = {"status": "RUNNING", "owner_pid": 8765}
    write_json(tmp_path, pipeline.STATUS, original)
    monkeypatch.setattr(pipeline, "process_alive", lambda pid: False)
    assert pipeline.health(tmp_path)["status"] == "INTERRUPTED"
    assert pipeline.read(tmp_path / pipeline.STATUS) == original


def test_missing_status_reports_not_run(tmp_path):
    assert pipeline.health(tmp_path) == {"status": "NOT_RUN"}


def freeze_clocks(monkeypatch, moment=None):
    """Pin both clocks a fixture run reads, so a zero-length step stays fresh.

    time.time() and datetime.now().timestamp() disagree below the microsecond,
    which only a fixture whose step takes no measurable time can ever notice.
    """
    fixed = moment or pipeline.datetime(2026, 9, 13, tzinfo=pipeline.timezone.utc)
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=fixed.timestamp))
    monkeypatch.setattr(pipeline, "now", fixed.isoformat)
    return fixed


def media_receipt(root, status="PASS", schema="daily-media-refresh-receipt-v1", completed_at=None):
    write_json(root, MEDIA_RECEIPT, {"schema": schema, "status": status,
                                   "completed_at": completed_at or pipeline.now(), "fixture": "fresh media evidence"})


def test_daily_admission_defaults_follow_the_2026_10_15_owner_ruling(monkeypatch):
    assert (admission_budget.KEY_LIMIT, admission_budget.CALL_BUDGET_DEFAULT) == (80, 48)
    # No limit is fixed at import: None means "resolve when the run starts", so a bad budget fails the run, not the import.
    assert inspect.signature(pipeline.run).parameters["admission_limit"].default is None
    calls = []
    monkeypatch.setattr(pipeline, "run", lambda **kwargs: calls.append(kwargs) or 0)
    assert pipeline.main([]) == 0
    assert calls[0]["admission_limit"] is None
    admissions = dict((name, args) for name, _, args, _ in pipeline.steps(calls[0]["admission_limit"]))["admissions"]
    assert admissions == ["--limit", "80", "--max-classifier-calls", "48"]
    assert pipeline.main(["--admission-limit", "19"]) == 0 and calls[1]["admission_limit"] == 19   # an explicit limit stands


# MELANGE_SENTINEL_CALL_BUDGET raises the paid-call budget for a flood run; unset keeps the D-211 default of 48.
# A bad value must stop the job, never quietly fall back to 48 and under-run a flood the operator asked for.
BUDGET_ENV = "MELANGE_SENTINEL_CALL_BUDGET"

# Media mode re-runs these after billstatus: daily_media_refresh.REPOSITORY_BOUND_DERIVED, named rederive_<derived step>.
REDERIVED = ["rederive_behavior_panel", "rederive_behavioral_dispositions", "rederive_big5_lexcal", "rederive_big5_hybrid"]


def test_call_budget_env_unset_keeps_the_d211_default_and_a_positive_integer_overrides_it():
    assert admission_budget.call_budget({}) == 48
    assert admission_budget.call_budget({BUDGET_ENV: "120"}) == 120
    assert admission_budget.call_budget({BUDGET_ENV: " 7 "}) == 7


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "", "4.5", "+5", "1_000", "\u0663", "\uff13", " ", "9" * 5000])
def test_call_budget_env_invalid_or_non_positive_fails_loudly(bad):
    # int() would accept "1_000", "+5" and non-ASCII digits (Arabic-Indic, fullwidth); only plain ASCII digits count.
    with pytest.raises(ValueError, match=BUDGET_ENV):
        admission_budget.call_budget({BUDGET_ENV: bad})


def test_a_digit_string_past_the_interpreter_limit_chains_its_cause():
    with pytest.raises(ValueError, match=BUDGET_ENV) as caught:
        admission_budget.call_budget({BUDGET_ENV: "9" * 5000})
    assert isinstance(caught.value.__cause__, ValueError)         # int()'s own refusal, kept as the cause


def test_the_key_limit_follows_the_budget_so_one_setting_floods():
    # selected keys are capped at the limit, so a budget above 80 did nothing: the limit rises with it, never below 80.
    assert admission_budget.key_limit({}) == 80
    assert admission_budget.key_limit({BUDGET_ENV: "48"}) == 80
    assert admission_budget.key_limit({BUDGET_ENV: "80"}) == 80
    assert admission_budget.key_limit({BUDGET_ENV: "120"}) == 120
    with pytest.raises(ValueError, match=BUDGET_ENV):
        admission_budget.key_limit({BUDGET_ENV: "nope"})


def test_a_budget_of_120_gives_the_daily_runner_limit_120_and_budget_120(monkeypatch):
    monkeypatch.setenv(BUDGET_ENV, "120")
    admissions = dict((name, args) for name, _, args, _ in pipeline.steps())["admissions"]
    assert admissions == ["--limit", "120", "--max-classifier-calls", "120"]
    # an explicit --admission-limit is the operator's word and stands, whatever the budget
    explicit = dict((name, args) for name, _, args, _ in pipeline.steps(30))["admissions"]
    assert explicit == ["--limit", "30", "--max-classifier-calls", "120"]


def test_the_env_budget_feeds_the_admissions_step_flag_when_the_steps_are_built(monkeypatch):
    monkeypatch.setenv(BUDGET_ENV, "120")
    admissions = dict((name, args) for name, _, args, _ in pipeline.steps(80))["admissions"]
    assert admissions == ["--limit", "80", "--max-classifier-calls", "120"]


@pytest.mark.parametrize("bad", ["0", "junk"])
def test_importing_the_module_with_a_bad_budget_succeeds_and_the_weekly_rollback_symbols_import(bad):
    # weekly_refresh.py imports ROLLBACK_SOURCES / rollback_unbound_repository from this module inside a broad except; an
    # import that raised on a typo would silently disarm the weekly repository rollback (DE-043). Importing is side-effect free.
    fec = Path(__file__).resolve().parents[2]
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
             "from legislation.refresh_congress_pipeline import ROLLBACK_SOURCES, rollback_unbound_repository; "
             "print(len(ROLLBACK_SOURCES), callable(rollback_unbound_repository))")
    run = subprocess.run([sys.executable, "-c", probe, str(fec)], capture_output=True, text=True, timeout=120,
                         env={**os.environ, BUDGET_ENV: bad})
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "2 True"


@pytest.mark.parametrize("bad", ["0", "abc"])
def test_a_bad_budget_fails_the_run_with_a_failed_status_and_receipt_not_the_import(tmp_path, monkeypatch, bad):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    monkeypatch.setenv(BUDGET_ENV, bad)
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *a, **k: pytest.fail("no step may start on a bad budget"))
    assert pipeline.run(tmp_path) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["status"] == "FAILED" and BUDGET_ENV in status["error"] and "ValueError" in status["error"]
    receipt = pipeline.read(Path(status["run_dir"]) / "receipt.json")
    assert receipt["status"] == "FAILED" and BUDGET_ENV in receipt["error"]


def test_expanded_chain_collects_all_bills_then_media_then_entire_canonical_chain():
    selected = pipeline.steps(19, include_media=True)
    names = [name for name, _, _, _ in selected]
    # DE-043/W1: media (hours, killable) precedes the repository fetch, so nothing long sits before the chain's rebind.
    # 2026-10-20: the repository-bound behavioural derivations re-run after billstatus (the media step's own copy read the old repository).
    assert names == ["calibration", "roster", "discovery", "admissions", "votes", "media", "repository", "billstatus", *REDERIVED, "pipeline"]
    assert selected[names.index("media")][1] == "collect/daily_media_refresh.py"
    assert selected[-1][1:3] == ("run_pipeline.py", [])
    assert selected[names.index("admissions")][2] == ["--limit", "19", "--max-classifier-calls", "48"]
    # Starting from the first canonical step includes collection intake and all
    # media consumers, instead of skipping to the bill-only roster verifier.
    tree = ast.parse((pipeline.CODE / "run_pipeline.py").read_text(encoding="utf-8"))
    chain = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "CHAIN" for target in node.targets))
    producers = [name for name, _ in chain]
    assert producers[0] == "calibration_guard.py"
    assert producers.index("audit_collection.py") < producers.index("sync_sentinel_bill_ledger.py")
    assert producers[-1] == "audit_full.py"


def test_publication_manifest_tracks_actual_social_rollup():
    assert "data/members/social_member_summary.json" in pipeline.SOURCES
    assert "social_member_summary.json" not in pipeline.SOURCES


def test_fresh_pass_media_receipt_is_required_and_hash_bound(generation):
    media_receipt(generation)
    artifacts = pipeline.validate_publication(generation, 0, include_media=True)
    assert artifacts[str(MEDIA_RECEIPT).replace("\\", "/")] == {
        "sha256": pipeline.digest(generation / MEDIA_RECEIPT),
        "size": (generation / MEDIA_RECEIPT).stat().st_size,
    }


def test_fresh_partial_media_receipt_is_accepted_and_hash_bound(generation):
    # PARTIAL (budgeted press backlog the next run resumes) is a non-fatal success (DE-020).
    media_receipt(generation, status="PARTIAL")
    artifacts = pipeline.validate_publication(generation, 0, include_media=True)
    assert artifacts[str(MEDIA_RECEIPT).replace("\\", "/")] == {
        "sha256": pipeline.digest(generation / MEDIA_RECEIPT),
        "size": (generation / MEDIA_RECEIPT).stat().st_size,
    }


@pytest.mark.parametrize("failure", ["missing", "fail", "running", "wrong_schema", "stale"])
def test_media_generation_cannot_be_certified_without_a_fresh_pass_or_partial(generation, failure):
    if failure != "missing":
        media_receipt(generation, status={"fail": "FAIL", "running": "RUNNING"}.get(failure, "PASS"),
                      schema="unrelated-pass-receipt" if failure == "wrong_schema" else "daily-media-refresh-receipt-v1")
    if failure == "stale":
        os.utime(generation / MEDIA_RECEIPT, (1, 1))
    with pytest.raises((RuntimeError, FileNotFoundError)):
        pipeline.validate_publication(generation, 2, include_media=True)


@pytest.mark.parametrize("completion,accepted", [
    ("2026-09-13T05:00:00.250000+00:00", True),
    ("2026-09-13T05:00:00.100000+00:00", False),
    ("2026-09-13T05:00:02+00:00", False),
    ("2026-09-13T05:00:00.250000", False),
])
def test_media_freshness_uses_precise_completion_despite_coarse_file_time(generation, monkeypatch, completion, accepted):
    start = pipeline.datetime.fromisoformat("2026-09-13T05:00:00.200000+00:00").timestamp()
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=lambda: start + 0.5))
    media_receipt(generation, completed_at=completion)
    # Reproduce the Windows metadata lag deterministically, including a stale
    # receipt from the same clock second that metadata alone cannot distinguish.
    os.utime(generation / MEDIA_RECEIPT, (start - 0.02, start - 0.02))
    if accepted:
        assert pipeline.validate_media_receipt(generation, start)["status"] == "PASS"
    else:
        with pytest.raises(RuntimeError):
            pipeline.validate_media_receipt(generation, start)


def test_expanded_run_echoes_owned_attempt_and_seals_immutable_media_snapshot(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1, "Member B": 2}})
    monkeypatch.setenv("MELANGE_DAILY_ATTEMPT_ID", "owned-daily-attempt-123")
    # Freeze both clocks, as the two race tests below already do. A fixture step
    # finishes in well under a microsecond, and datetime.now().timestamp()
    # truncates to microseconds, so it reads EARLIER than the time.time() taken
    # just before it about 88% of the time on this box. That made the real-clock
    # form of this test fail its own freshness precondition roughly one run in
    # three. The precise-completion boundary itself is covered deterministically
    # by test_media_freshness_uses_precise_completion_despite_coarse_file_time.
    freeze_clocks(monkeypatch)
    seen = []

    def launch(command, *, cwd, env, stdout, stderr):
        script = Path(command[1]).name
        seen.append(script)
        assert env["MELANGE_DAILY_ATTEMPT_ID"] == "owned-daily-attempt-123"
        state = pipeline.read(tmp_path / pipeline.STATUS)
        assert state["daily_attempt_id"] == "owned-daily-attempt-123"
        assert state["include_media"] is True
        if script == "daily_media_refresh.py":
            media_receipt(tmp_path)
        elif script == "run_pipeline.py":
            assert command[2:] == []
            assert pipeline.read(tmp_path / MEDIA_RECEIPT)["status"] == "PASS"
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 0
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    assert receipt["daily_attempt_id"] == "owned-daily-attempt-123"
    assert receipt["include_media"] is True
    assert receipt["status"] == "PASS"
    # 2026-10-20: the repository-bound behavioural derivations re-run between billstatus and the chain.
    assert seen[-8:] == ["daily_media_refresh.py", "update_bill_repository.py", "refresh_billstatus_bulk.py",
                         "behavior_panel.py", "behavioral_dispositions.py", "big5_lexcal_v2.py", "big5_hybrid.py", "run_pipeline.py"]
    immutable = Path(receipt["run_dir"]) / "daily_media_refresh_receipt.json"
    bound_hash = receipt["artifacts"][MEDIA_RECEIPT.as_posix()]["sha256"]
    assert immutable.read_bytes() == (tmp_path / MEDIA_RECEIPT).read_bytes()
    assert pipeline.digest(immutable) == bound_hash
    media_receipt(tmp_path, status="SUBSEQUENT_RUN")
    assert pipeline.digest(immutable) == bound_hash
    assert pipeline.digest(tmp_path / MEDIA_RECEIPT) != bound_hash


@pytest.mark.parametrize("failure", ["exit_failure", "missing", "fail", "running", "wrong_schema", "stale"])
def test_media_failure_blocks_canonical_dashboard_chain(tmp_path, monkeypatch, failure):
    seen = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append(script)
        assert script != "run_pipeline.py", "Unverified media must halt before any dashboard writer"
        if script == "daily_media_refresh.py":
            if failure not in ("missing", "exit_failure"):
                media_receipt(tmp_path, status={"fail": "FAIL", "running": "RUNNING"}.get(failure, "PASS"),
                              schema="wrong" if failure == "wrong_schema" else "daily-media-refresh-receipt-v1")
            if failure == "stale":
                os.utime(tmp_path / MEDIA_RECEIPT, (1, 1))
            return CompletedProcess(4 if failure == "exit_failure" else 0)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 1
    assert seen[-1] == "daily_media_refresh.py"
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED"
    assert state["include_media"] is True
    assert not (tmp_path / pipeline.RECEIPT).exists()
    assert not (tmp_path / "Moneyball.html").exists()
    assert not (tmp_path / ".pipeline_run.lock").exists()


@pytest.mark.parametrize("changed_path", ["snapshot", "live"])
def test_media_snapshot_race_cannot_produce_terminal_success(tmp_path, monkeypatch, changed_path):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    # This test targets the copy race, not the resolution of Windows file times.
    # Freeze both clocks so every precondition is deterministic; separate tests
    # above exercise precise completion and coarse filesystem time boundaries.
    fixed = pipeline.datetime(2026, 9, 13, tzinfo=pipeline.timezone.utc)
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=fixed.timestamp))
    monkeypatch.setattr(pipeline, "now", fixed.isoformat)
    injected = []

    def launch(command, **kwargs):
        if Path(command[1]).name == "daily_media_refresh.py":
            media_receipt(tmp_path)
        elif Path(command[1]).name == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    original_copy = pipeline.shutil.copy2

    def race(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        destination = Path(destination)
        if destination.name == MEDIA_RECEIPT.name and destination.parent.parent.name == "congress_pipeline":
            target = destination if changed_path == "snapshot" else Path(source)
            target.write_text('{"schema":"changed after validation"}', encoding="utf-8")
            injected.append(changed_path)
        return result

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    monkeypatch.setattr(pipeline.shutil, "copy2", race)
    assert pipeline.run(tmp_path, include_media=True) == 1
    assert injected == [changed_path], "The test must reach its intended race, not fail a precondition"
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED" and "Media receipt changed" in state["error"]
    assert not (tmp_path / pipeline.RECEIPT).exists()


@pytest.mark.parametrize("mutation", ["after_media_stage", "after_terminal_validation"])
def test_terminal_publication_binds_the_exact_validated_media_bytes(tmp_path, monkeypatch, mutation):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    fixed = pipeline.datetime(2026, 9, 13, tzinfo=pipeline.timezone.utc)
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=fixed.timestamp))
    monkeypatch.setattr(pipeline, "now", fixed.isoformat)
    injected = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        if script == "daily_media_refresh.py":
            media_receipt(tmp_path)
        elif script == "run_pipeline.py":
            install_generation(tmp_path)
            if mutation == "after_media_stage":
                # Another syntactically valid fresh PASS must not replace the
                # exact media generation that authorized downstream writers.
                document = pipeline.read(tmp_path / MEDIA_RECEIPT)
                document["fixture"] = "different media generation"
                write_json(tmp_path, MEDIA_RECEIPT, document)
                injected.append(mutation)
        return CompletedProcess()

    original_validate = pipeline.validated_media_snapshot
    calls = []
    def validate(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        calls.append(result[1])
        if mutation == "after_terminal_validation" and len(calls) == 2:
            media_receipt(tmp_path, status="FAIL")
            injected.append(mutation)
        return result

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    monkeypatch.setattr(pipeline, "validated_media_snapshot", validate)
    assert pipeline.run(tmp_path, include_media=True) == 1
    assert injected == [mutation]
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED" and "Media receipt changed" in state["error"]
    assert not (tmp_path / pipeline.RECEIPT).exists()


def test_cli_include_media_passes_expanded_mode_to_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "run", lambda **kwargs: calls.append(kwargs) or 0)
    assert pipeline.main(["--include-media", "--admission-limit", "12"]) == 0
    assert calls == [{"admission_limit": 12, "include_media": True, "smoke": False}]


# --- Smoke mode ---------------------------------------------------------


def test_smoke_skips_discovery_spends_no_model_call_and_keeps_the_full_chain():
    selected = pipeline.steps(32, smoke=True)
    names = [name for name, _, _, _ in selected]
    assert names == ["calibration", "roster", "discovery", "admissions", "votes", "media",
                     "repository", "billstatus", *REDERIVED, "pipeline"]
    by_name = {name: (script, args) for name, script, args, _ in selected}
    # Discovery is recorded in order but has no script, so it is never launched.
    assert by_name["discovery"][0] is None
    assert by_name["admissions"] == ("legislation/refresh_sentinel_admissions.py",
                                     ["--limit", "1", "--no-llm", "--no-install"])
    # The steps that were measured as cheap keep their full production scope.
    for name in ("votes", "repository", "billstatus"):
        assert by_name[name][1] == []
    assert by_name["media"][0] == "collect/daily_media_refresh.py"
    assert "--smoke" in by_name["media"][1]
    # There is no shortened canonical chain; smoke runs the same one.
    assert by_name["pipeline"] == ("run_pipeline.py", [])
    assert by_name["pipeline"] == {n: (s, a) for n, s, a, _ in pipeline.steps(32, include_media=True)}["pipeline"]


def test_smoke_and_include_media_cannot_be_combined(monkeypatch):
    monkeypatch.setattr(pipeline, "run", lambda **kwargs: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as exit_info:
        pipeline.main(["--smoke", "--include-media"])
    assert exit_info.value.code == 2


def test_cli_smoke_passes_smoke_mode_to_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "run", lambda **kwargs: calls.append(kwargs) or 0)
    assert pipeline.main(["--smoke"]) == 0
    assert calls == [{"admission_limit": None, "include_media": False, "smoke": True}]   # None: resolved when the steps are built


def test_smoke_run_records_its_mode_and_never_writes_the_production_receipt(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    freeze_clocks(monkeypatch)
    prior = {"schema": "congress-pipeline-status-v1", "status": "PASS", "mode": "full",
             "marker": "the last verified production generation"}
    write_json(tmp_path, pipeline.RECEIPT, prior)
    seen = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append(script)
        if script == "daily_media_refresh.py":
            assert "--smoke" in command[2:]
            media_receipt(tmp_path)
        elif script == "run_pipeline.py":
            assert command[2:] == []
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, smoke=True) == 0
    assert "refresh_legislation_discovery.py" not in seen
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["status"] == "PASS" and status["mode"] == "smoke"
    skipped = [row for row in status["steps"] if row["step"] == "discovery"]
    assert skipped == [{"step": "discovery", "status": "SKIPPED_SMOKE",
                        "returncode": None, "duration_seconds": 0}]
    receipt = pipeline.read(Path(status["run_dir"]) / "receipt.json")
    assert receipt["mode"] == "smoke" and receipt["status"] == "PASS"
    # The documented last verified production success is untouched.
    assert pipeline.read(tmp_path / pipeline.RECEIPT) == prior


def test_smoke_receipt_cannot_complete_the_day(tmp_path):
    import daily_refresh as daily
    directory = tmp_path / "audits/congress_pipeline/smoke-attempt"
    directory.mkdir(parents=True)
    media = {"schema": "daily-media-refresh-receipt-v1", "status": "PASS"}
    daily.write(tmp_path / daily.MEDIA_RECEIPT, media)
    daily.write(directory / "daily_media_refresh_receipt.json", media)
    receipt = {"schema": "congress-pipeline-status-v1", "status": "PASS", "include_media": True,
               "mode": "smoke", "daily_attempt_id": "smoke-attempt", "owner_pid": 4242,
               "run_dir": str(directory), "started_at": daily.utc_now(),
               "completed_at": daily.utc_now(),
               "artifacts": {daily.MEDIA_RECEIPT: {"sha256": daily.digest(tmp_path / daily.MEDIA_RECEIPT)}}}
    daily.write(directory / "receipt.json", receipt)
    daily.write(tmp_path / daily.WRAPPER_RECEIPT, receipt)
    attempt = {"id": "smoke-attempt", "child_pid": 4242, "started_epoch": 0}
    with pytest.raises(ValueError, match="smoke pipeline run cannot complete the day"):
        daily.verified_completion(tmp_path, attempt, current_media=True)
    # The identical receipt in full mode is exactly what does complete the day.
    daily.write(directory / "receipt.json", {**receipt, "mode": "full"})
    daily.write(tmp_path / daily.WRAPPER_RECEIPT, {**receipt, "mode": "full"})
    assert daily.verified_completion(tmp_path, attempt, current_media=True)["attempt_id"] == "smoke-attempt"


# --- Flaky discovery: retry once, then defer -----------------------------


def discovery_world(tmp_path, monkeypatch, discovery_codes):
    """Run the wrapper with discovery returning the given codes in order."""
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    monkeypatch.setattr(pipeline, "DISCOVERY_RETRY_DELAY_SECONDS", 0)
    slept = []
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: slept.append(seconds))
    codes = list(discovery_codes)
    seen = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append((script, kwargs["stdout"].name))
        if script == "refresh_legislation_discovery.py":
            code = codes.pop(0)
            if code:
                kwargs["stdout"].write("launchPersistentContext: Timeout 180000ms exceeded\n")
            return CompletedProcess(code)
        if script == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    return seen, slept


def test_flaky_discovery_recovers_on_one_retry_and_the_run_is_an_ordinary_pass(tmp_path, monkeypatch):
    seen, slept = discovery_world(tmp_path, monkeypatch, [1, 0])
    assert pipeline.run(tmp_path) == 0
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    assert receipt["status"] == "PASS"
    assert receipt["deferred_steps"] == []
    row = next(r for r in receipt["steps"] if r["step"] == "discovery")
    assert row["retried"] is True and row["first_returncode"] == 1
    assert row["returncode"] == 0 and "status" not in row
    assert row["log"].endswith("discovery.retry.log")
    # The retry is a second child writing its own log, not a reused one.
    logs = [name for script, name in seen if script == "refresh_legislation_discovery.py"]
    assert len(logs) == 2 and logs[0].endswith("discovery.log") and logs[1].endswith("discovery.retry.log")
    assert (Path(receipt["run_dir"]) / "discovery.log").is_file()
    assert (Path(receipt["run_dir"]) / "discovery.retry.log").is_file()


def test_discovery_failing_twice_defers_without_failing_the_day(tmp_path, monkeypatch):
    seen, slept = discovery_world(tmp_path, monkeypatch, [1, 1])
    events = []
    monkeypatch.setattr(pipeline, "_emit", lambda *args, **kwargs: events.append((args, kwargs)))
    assert pipeline.run(tmp_path) == 0
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    assert receipt["status"] == "PASS"
    assert receipt["deferred_steps"] == ["discovery"]
    row = next(r for r in receipt["steps"] if r["step"] == "discovery")
    assert row["status"] == "DEFERRED" and row["returncode"] == 1
    assert row["first_returncode"] == 1 and row["retried"] is True
    assert row["note"].startswith("discovery collector failed twice (rc=1, rc=1); "
                                  "new-bill discovery deferred to the next run; see ")
    assert row["note"].endswith("discovery.retry.log")
    # Every later step still ran: already-known bills are still refreshed.
    scripts = [script for script, _ in seen]
    for later in ("refresh_vote_sources.py", "update_bill_repository.py",
                  "refresh_billstatus_bulk.py", "run_pipeline.py"):
        assert later in scripts
    assert [r["step"] for r in receipt["steps"]] == [s[0] for s in pipeline.steps(32)]
    # The status document and the immutable receipt agree on the deferral.
    assert pipeline.read(tmp_path / pipeline.STATUS)["deferred_steps"] == ["discovery"]
    assert pipeline.read(Path(receipt["run_dir"]) / "receipt.json")["deferred_steps"] == ["discovery"]
    deferred = [args for args, kwargs in events
                if args[:2] == ("refresh.step", "deferred")]
    assert len(deferred) == 1 and "failed twice" in deferred[0][2]
    run_done = next(args for args, kwargs in events if args[:2] == ("refresh.run", "completed"))
    assert "PASS with deferred: discovery" in run_done[2]


def test_the_retry_waits_the_configured_delay(tmp_path, monkeypatch):
    discovery_world(tmp_path, monkeypatch, [1, 0])
    monkeypatch.setattr(pipeline, "DISCOVERY_RETRY_DELAY_SECONDS", 300)
    slept = []
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: slept.append(seconds))
    assert pipeline.run(tmp_path) == 0
    assert slept == [300]


@pytest.mark.parametrize("failing", ["calibration_guard.py", "refresh_vote_sources.py",
                                     "update_bill_repository.py"])
def test_a_non_discovery_step_is_never_retried_and_still_stops_the_run(tmp_path, monkeypatch, failing):
    monkeypatch.setattr(pipeline, "DISCOVERY_RETRY_DELAY_SECONDS", 0)
    seen = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append(script)
        return CompletedProcess(4 if script == failing else 0)

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path) == 1
    assert seen.count(failing) == 1
    assert seen[-1] == failing
    state = pipeline.read(tmp_path / pipeline.STATUS)
    assert state["status"] == "FAILED" and "exit 4" in state["error"]
    assert state["deferred_steps"] == []
    assert not (tmp_path / pipeline.RECEIPT).exists()


def test_smoke_still_skips_discovery_and_never_retries_it(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    freeze_clocks(monkeypatch)
    monkeypatch.setattr(pipeline, "DISCOVERY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(pipeline.time, "sleep",
                        lambda seconds: pytest.fail("smoke must not retry"), raising=False)
    seen = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append(script)
        if script == "daily_media_refresh.py":
            media_receipt(tmp_path)
        elif script == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, smoke=True) == 0
    assert "refresh_legislation_discovery.py" not in seen
    status = pipeline.read(tmp_path / pipeline.STATUS)
    row = next(r for r in status["steps"] if r["step"] == "discovery")
    assert row == {"step": "discovery", "status": "SKIPPED_SMOKE",
                   "returncode": None, "duration_seconds": 0}
    assert status["deferred_steps"] == []


# --- Observability ------------------------------------------------------


def test_before_image_hashes_the_huge_dashboards_instead_of_copying_them(tmp_path, monkeypatch):
    # The prior generation must already be on disk, or there is no before-image.
    install_generation(tmp_path)
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})

    def launch(command, **kwargs):
        if Path(command[1]).name == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path) == 0
    run_dir = Path(pipeline.read(tmp_path / pipeline.RECEIPT)["run_dir"])
    before = pipeline.read(run_dir / "before.json")
    for name in ("Moneyball.html", "Moneyball_Redesign.html"):
        assert before[name]["copied"] is False
        assert before[name]["sha256"] == pipeline.digest(tmp_path / name)
        assert before[name]["size"] == (tmp_path / name).stat().st_size
        assert not (run_dir / "before" / name).exists()
    # Everything rank_changes actually reads is still copied byte-for-byte.
    assert before["ranks_python.json"]["copied"] is True
    assert (run_dir / "before/ranks_python.json").is_file()


def test_progress_log_is_appended_per_event_and_bounded_at_run_start(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    path = tmp_path / pipeline.LIVE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join("old line %d\n" % i for i in range(900)), encoding="utf-8")

    def launch(command, **kwargs):
        if Path(command[1]).name == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path) == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    retained = [line for line in lines if line.startswith("old line")]
    assert len(retained) == pipeline.LIVE_LOG_KEEP
    assert retained[0] == "old line 500" and retained[-1] == "old line 899"
    written = [line for line in lines if not line.startswith("old line")]
    assert any("snapshot" in line and "COMPLETED" in line for line in written)
    assert any("calibration" in line and "COMPLETED" in line for line in written)
    assert any(line.split()[1:3] == ["run", "PASS"] for line in written)


def test_step_events_carry_position_and_media_partials_reach_the_status_row(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    freeze_clocks(monkeypatch)
    events = []
    monkeypatch.setattr(pipeline, "_emit", lambda *args, **kwargs: events.append((args, kwargs)))

    def launch(command, **kwargs):
        script = Path(command[1]).name
        if script == "daily_media_refresh.py":
            write_json(tmp_path, MEDIA_RECEIPT, {
                "schema": "daily-media-refresh-receipt-v1", "status": "PASS",
                "completed_at": pipeline.now(), "partial": True, "partial_sources": ["press"],
                "press_partial": {"pending_body_urls": 2200, "failed_bodies": 2578,
                                  "status": "PARTIAL"}})
        elif script == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 0
    total = len(pipeline.steps(32, include_media=True))
    running = [kwargs for args, kwargs in events if args[:2] == ("refresh.step", "running")]
    assert [kwargs["current"] for kwargs in running] == list(range(1, total + 1))
    assert {kwargs["total"] for kwargs in running} == {total}
    snapshots = [args for args, kwargs in events if args[0] == "refresh.snapshot"]
    assert [args[1] for args in snapshots][:1] == ["started"]
    assert "completed" in [args[1] for args in snapshots]
    media_row = next(row for row in pipeline.read(tmp_path / pipeline.RECEIPT)["steps"]
                     if row["step"] == "media")
    assert media_row["partial_sources"] == ["press"]
    assert media_row["press_partial"]["pending_body_urls"] == 2200
    completed_media = next(args for args, kwargs in events
                           if args[:2] == ("refresh.step", "completed") and kwargs.get("stage") == "media")
    assert "PARTIAL: press" in completed_media[2]


def test_partial_media_receipt_is_a_non_fatal_success_recorded_on_the_run(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    freeze_clocks(monkeypatch)
    events = []
    seen = []
    monkeypatch.setattr(pipeline, "_emit", lambda *args, **kwargs: events.append((args, kwargs)))

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append(script)
        if script == "daily_media_refresh.py":
            write_json(tmp_path, MEDIA_RECEIPT, {
                "schema": "daily-media-refresh-receipt-v1", "status": "PARTIAL",
                "completed_at": pipeline.now(), "partial": True, "partial_sources": ["press"],
                "press_partial": {"pending_body_urls": 2200, "failed_bodies": 2578,
                                  "status": "PARTIAL"}})
        elif script == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 0
    # The canonical chain ran behind the PARTIAL media step: it is not fatal.
    # 2026-10-20: the repository-bound behavioural derivations re-run between billstatus and the chain.
    assert seen[-8:] == ["daily_media_refresh.py", "update_bill_repository.py", "refresh_billstatus_bulk.py",
                         "behavior_panel.py", "behavioral_dispositions.py", "big5_lexcal_v2.py", "big5_hybrid.py", "run_pipeline.py"]
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    # The congress run itself stays PASS; the media gap is named beside it.
    assert receipt["status"] == "PASS"
    assert receipt["media_status"] == "PARTIAL" and receipt["partial_sources"] == ["press"]
    assert pipeline.read(Path(receipt["run_dir"]) / "receipt.json")["media_status"] == "PARTIAL"
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["media_status"] == "PARTIAL" and status["partial_sources"] == ["press"]
    # The PARTIAL receipt is the exact generation bound into the immutable run directory.
    immutable = Path(receipt["run_dir"]) / "daily_media_refresh_receipt.json"
    assert pipeline.digest(immutable) == receipt["artifacts"][MEDIA_RECEIPT.as_posix()]["sha256"]
    run_done = next(args for args, kwargs in events if args[:2] == ("refresh.run", "completed"))
    assert run_done[2] == "Congress collection, Excel and dashboard verified - media PARTIAL: press"


def test_a_clean_media_receipt_adds_no_partial_noise(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    freeze_clocks(monkeypatch)
    events = []
    monkeypatch.setattr(pipeline, "_emit", lambda *args, **kwargs: events.append((args, kwargs)))

    def launch(command, **kwargs):
        script = Path(command[1]).name
        if script == "daily_media_refresh.py":
            media_receipt(tmp_path)
        elif script == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 0
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    media_row = next(row for row in receipt["steps"] if row["step"] == "media")
    assert "partial_sources" not in media_row and "press_partial" not in media_row
    assert receipt["media_status"] == "PASS" and receipt["partial_sources"] == []
    run_done = next(args for args, kwargs in events if args[:2] == ("refresh.run", "completed"))
    assert run_done[2] == "Congress collection, Excel and dashboard verified"


def test_w1_failure_before_rebind_restores_repository_and_receipt_byte_exact(tmp_path, monkeypatch):
    # The sidecar is bound to the before-image repository; the repository step (step 5)
    # rewrites both files and then fails, so the chain never rebinds the sidecar (DE-043).
    from datetime import datetime, timezone
    (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "before"}\n')
    write_json(tmp_path, REPOSITORY_RECEIPT, {"schema": "congress-bill-refresh-receipt-v1", "gen": "before"})
    write_json(tmp_path, pipeline.MEMBERSHIP_SIDECAR, {
        "schema": "official-membership-movements-v1", "status": "PASS",
        "checked_at": datetime.now(timezone.utc).isoformat(), "facts": [], "additions": [],
        "repository_sha256": pipeline.digest(tmp_path / "bill_repository_live.json")})
    original = {rel: (tmp_path / rel).read_bytes() for rel in pipeline.ROLLBACK_SOURCES}

    def launch(command, **kwargs):
        if Path(command[1]).name == "update_bill_repository.py":
            (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "fetched"}\n')
            write_json(tmp_path, REPOSITORY_RECEIPT, {"gen": "fetched"})
            return CompletedProcess(5)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path) == 1
    for rel, data in original.items():
        assert (tmp_path / rel).read_bytes() == data, rel
    receipt = pipeline.read(tmp_path / pipeline.STATUS)
    assert receipt["rollback"] == "restored"
    assert receipt["rollback_membership_load"] == "PASS"


def test_w1_media_precedes_repository_fetch():
    # DE-043: the repository fetch and the chain's verify_collector_roster_updates.py (which rebinds the
    # membership sidecar to the new repository sha) are one unit, so media must not sit between them.
    for mode in ({"include_media": True}, {"smoke": True}):
        names = [name for name, _, _, _ in pipeline.steps(19, **mode)]
        assert names.index("media") < names.index("repository") < names.index("billstatus") < names.index("pipeline")
        assert names[-1] == "pipeline"
        assert names[names.index("billstatus") + 1:-1] == REDERIVED   # nothing else between billstatus and the chain
    # Without media the order is unchanged.
    assert [name for name, _, _, _ in pipeline.steps(19)] == [
        "calibration", "roster", "discovery", "admissions", "votes", "repository", "billstatus", "pipeline"]


REDERIVE_ENV = "CONGRESS_REDERIVE"


@pytest.mark.parametrize("value", [None, "", "1"])
def test_congress_rederive_is_on_by_default(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(REDERIVE_ENV, value)
    for mode in ({"include_media": True}, {"smoke": True}):
        names = [name for name, _, _, _ in pipeline.steps(19, **mode)]
        assert names[names.index("billstatus") + 1:-1] == REDERIVED


def test_congress_rederive_zero_is_the_revert_switch_and_skips_exactly_the_four_steps(monkeypatch):
    monkeypatch.setenv(REDERIVE_ENV, "0")
    for mode in ({"include_media": True}, {"smoke": True}):
        names = [name for name, _, _, _ in pipeline.steps(19, **mode)]
        assert names == ["calibration", "roster", "discovery", "admissions", "votes", "media", "repository", "billstatus", "pipeline"]
    # Without media there was never a re-derivation, so the dial changes nothing (and a bad value there is not its business).
    assert [n for n, _, _, _ in pipeline.steps(19)] == [
        "calibration", "roster", "discovery", "admissions", "votes", "repository", "billstatus", "pipeline"]
    monkeypatch.setenv(REDERIVE_ENV, "off")
    assert [n for n, _, _, _ in pipeline.steps(19)][-1] == "pipeline"


@pytest.mark.parametrize("bad", ["off", "2", "true", "no"])
def test_congress_rederive_rejects_any_other_value_and_the_run_fails_with_a_receipt(tmp_path, monkeypatch, bad):
    monkeypatch.setenv(REDERIVE_ENV, bad)
    with pytest.raises(ValueError, match=REDERIVE_ENV):
        pipeline.steps(19, include_media=True)
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *a, **k: pytest.fail("no step may start on a bad dial"))
    assert pipeline.run(tmp_path, include_media=True) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["status"] == "FAILED" and REDERIVE_ENV in status["error"]


def _derived_by_name():
    from datetime import date
    from collect import daily_media_refresh as media
    return media, {step.name: step for step in media.derived_steps(pipeline.ROOT, date(2026, 10, 20))}


def test_rederived_steps_are_the_media_steps_own_definitions_not_a_second_list():
    media, derived = _derived_by_name()
    for mode in ({"include_media": True}, {"smoke": True}):
        selected = {name: (script, args, timeout) for name, script, args, timeout in pipeline.steps(19, **mode)}
        assert [name.removeprefix("rederive_") for name in REDERIVED] == [
            step.name for step in derived.values() if step.name in media.REPOSITORY_BOUND_DERIVED]   # same order as derived_steps()
        for name in REDERIVED:
            step = derived[name.removeprefix("rederive_")]
            assert selected[name] == (step.script, list(step.args), step.timeout)


def test_repository_bound_derived_set_covers_every_reader_of_the_repository_and_their_consumers():
    # behavior_panel and behavioral_dispositions read bill_repository_live.json (member_identity.load_billstatus); big5_lexcal and
    # big5_hybrid read behavioral_dispositions.json. A derived producer added later that does either must join the set, or the
    # one-run rank lag returns for it.
    media, derived = _derived_by_name()
    bound = set(media.REPOSITORY_BOUND_DERIVED)
    assert bound <= set(derived)
    for name, step in derived.items():
        text = (media.CODE / step.script).read_text(encoding="utf-8", errors="replace")
        reads_repository = "load_billstatus(" in text or "bill_repository_live" in text
        reads_bound_output = any(Path(output).name in text for output in pipeline.REDERIVE_OUTPUTS)   # incl. big5_v2 / big5_v3_hybrid
        if reads_repository or reads_bound_output:
            assert name in bound, name


def test_media_mode_runs_the_rederived_steps_between_billstatus_and_the_chain(tmp_path, monkeypatch):
    write_json(tmp_path, "ranks_python.json", {"rank": {"Member A": 1}})
    freeze_clocks(monkeypatch)
    launched = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        launched.append(script)
        if script == "daily_media_refresh.py":
            media_receipt(tmp_path)
        elif script == "run_pipeline.py":
            install_generation(tmp_path)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 0
    assert launched[launched.index("update_bill_repository.py"):] == [
        "update_bill_repository.py", "refresh_billstatus_bulk.py", "behavior_panel.py", "behavioral_dispositions.py",
        "big5_lexcal_v2.py", "big5_hybrid.py", "run_pipeline.py"]
    rows = [row["step"] for row in pipeline.read(tmp_path / pipeline.RECEIPT)["steps"]]
    assert rows[rows.index("billstatus") + 1:] == [*REDERIVED, "pipeline"]


REDERIVE_SCRIPT_OUTPUT = {"behavior_panel.py": "behavior_panel.json",
                          "behavioral_dispositions.py": "behavioral_dispositions.json",
                          "big5_lexcal_v2.py": "data/members/big5_v2.json",
                          "big5_hybrid.py": "data/members/big5_v3_hybrid.json"}


def test_the_rederive_outputs_are_what_the_four_scripts_write():
    # Renaming an output in a script without this constant would leave a failed run restoring the wrong files.
    media, derived = _derived_by_name()
    assert set(pipeline.REDERIVE_OUTPUTS) == set(REDERIVE_SCRIPT_OUTPUT.values())
    by_script = {Path(step.script).name: step.script for step in derived.values()}
    for script, output in REDERIVE_SCRIPT_OUTPUT.items():
        assert Path(output).name in (media.CODE / by_script[script]).read_text(encoding="utf-8"), (script, output)


def _rederive_world(tmp_path, monkeypatch, fail_script=None, rebind_in=None):
    """A bound before-image repository, derived outputs the media step 'made', and a fake Popen that rewrites every output
    a rederive script owns. Returns (before_repo_receipt_bytes, media_derived_bytes, launched)."""
    from datetime import datetime, timezone
    (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "before"}\n')
    write_json(tmp_path, REPOSITORY_RECEIPT, {"schema": "congress-bill-refresh-receipt-v1", "gen": "before"})
    write_json(tmp_path, pipeline.MEMBERSHIP_SIDECAR, {
        "schema": "official-membership-movements-v1", "status": "PASS",
        "checked_at": datetime.now(timezone.utc).isoformat(), "facts": [], "additions": [],
        "repository_sha256": pipeline.digest(tmp_path / "bill_repository_live.json")})
    original = {rel: (tmp_path / rel).read_bytes() for rel in pipeline.ROLLBACK_SOURCES}
    freeze_clocks(monkeypatch)
    media_made = {}
    launched = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        launched.append(script)
        if script == "daily_media_refresh.py":
            media_receipt(tmp_path)
            for output in pipeline.REDERIVE_OUTPUTS:        # what the media step's own derived run left behind
                target = tmp_path / output
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"media-derived " + output.encode())
                media_made[output] = target.read_bytes()
        elif script == "update_bill_repository.py":
            (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "fetched"}\n')
            write_json(tmp_path, REPOSITORY_RECEIPT, {"gen": "fetched"})
        elif script in REDERIVE_SCRIPT_OUTPUT:
            (tmp_path / REDERIVE_SCRIPT_OUTPUT[script]).write_bytes(b"rederived by " + script.encode())
            if script == fail_script:
                return CompletedProcess(1)
        elif script == "run_pipeline.py" and rebind_in:
            # the chain's verify_collector_roster_updates rebinds the sidecar to the fetched repository, then a later step fails
            bind_membership(tmp_path, pipeline.digest(tmp_path / "bill_repository_live.json"))
            return CompletedProcess(1)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    return original, media_made, launched


def test_a_failed_rederive_step_fails_the_run_and_restores_the_repository_and_the_derived_outputs(tmp_path, monkeypatch):
    original, media_made, launched = _rederive_world(tmp_path, monkeypatch, fail_script="big5_hybrid.py")
    assert pipeline.run(tmp_path, include_media=True) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["status"] == "FAILED" and "rederive_big5_hybrid failed with exit 1" in status["error"]
    assert "run_pipeline.py" not in launched                       # stop on first failure
    assert status["rollback"] == "restored"
    for rel, data in original.items():
        assert (tmp_path / rel).read_bytes() == data, rel          # the repository and its receipt, byte for byte
    for rel, data in media_made.items():                           # three scripts had already rewritten theirs
        assert (tmp_path / rel).read_bytes() == data, rel
    assert sorted(status["rollback_rederived"]) == sorted(pipeline.REDERIVE_OUTPUTS)


def test_the_derived_before_image_is_taken_only_when_the_rederive_steps_start(tmp_path, monkeypatch):
    original, media_made, launched = _rederive_world(tmp_path, monkeypatch)
    # fail the repository step itself: the rederive steps never start, so nothing of theirs is imaged or restored
    real = pipeline.subprocess.Popen

    def launch(command, **kwargs):
        if Path(command[1]).name == "update_bill_repository.py":
            return CompletedProcess(1)
        return real(command, **kwargs)

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert media_made and "repository failed with exit 1" in status["error"]      # it failed before any rederive step started
    assert not (Path(status["run_dir"]) / "before" / "behavior_panel.json").exists()
    assert "rollback_rederived" not in status
    for rel, data in media_made.items():
        assert (tmp_path / rel).read_bytes() == data, rel


def test_a_failure_after_the_rederive_steps_also_restores_the_derived_outputs_with_the_repository(tmp_path, monkeypatch):
    original, media_made, launched = _rederive_world(tmp_path, monkeypatch)
    real = pipeline.subprocess.Popen

    def launch(command, **kwargs):
        if Path(command[1]).name == "run_pipeline.py":
            return CompletedProcess(1)                              # the chain dies before it rebinds the sidecar
        return real(command, **kwargs)

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["rollback"] == "restored"
    for rel, data in original.items():
        assert (tmp_path / rel).read_bytes() == data, rel
    for rel, data in media_made.items():                           # all four had been rederived from the fetched repository
        assert (tmp_path / rel).read_bytes() == data, rel


def test_when_the_repository_is_not_rolled_back_the_derived_outputs_are_left_alone(tmp_path, monkeypatch):
    # The chain rebound the sidecar to the fetched repository and then failed: the repository stays, so do the derivations
    # built from it. Restoring them alone would pair old derivations with the new repository.
    original, media_made, launched = _rederive_world(tmp_path, monkeypatch, rebind_in=True)
    assert pipeline.run(tmp_path, include_media=True) == 1
    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["rollback"] == "verified" and "rollback_rederived" not in status
    for script, output in REDERIVE_SCRIPT_OUTPUT.items():
        assert (tmp_path / output).read_bytes() == b"rederived by " + script.encode()


def bind_membership(root, bound_sha):
    from datetime import datetime, timezone
    write_json(root, pipeline.MEMBERSHIP_SIDECAR, {
        "schema": "official-membership-movements-v1", "status": "PASS",
        "checked_at": datetime.now(timezone.utc).isoformat(), "facts": [], "additions": [],
        "repository_sha256": bound_sha})


def test_w1_a_restore_that_raises_part_way_is_restore_failed_not_verified(tmp_path, monkeypatch):
    # Opus pre-merge review: a OneDrive or antivirus lock on replace left the label "verified" over a half-restored pair.
    before = b'{"bills": {}, "generation": "before"}\n'
    (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "fetched"}\n')
    bind_membership(tmp_path, __import__("hashlib").sha256(before).hexdigest())
    run_dir = tmp_path / "run"
    (run_dir / "before").mkdir(parents=True)
    (run_dir / "before" / "bill_repository_live.json").write_bytes(before)

    def locked(*args, **kwargs):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(pipeline, "replace_atomic", locked)
    result = pipeline.rollback_unbound_repository(tmp_path, run_dir)
    assert result["rollback"] == "restore_failed"
    assert result["rollback_membership_load"].startswith("PermissionError")


def test_w1_media_nonzero_exit_leaves_the_repository_untouched(tmp_path, monkeypatch):
    # 2026-10-16 hard death and 2026-10-19 sign-out kill (exit 0x40010004) both landed in media after the
    # repository had been fetched. A media child that exits nonzero must now stop the run before the
    # repository step launches. Death of the wrapper process itself is out of scope here (no Python runs).
    (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "before"}\n')
    write_json(tmp_path, REPOSITORY_RECEIPT, {"schema": "congress-bill-refresh-receipt-v1", "gen": "before"})
    bind_membership(tmp_path, pipeline.digest(tmp_path / "bill_repository_live.json"))
    original = {rel: (tmp_path / rel).read_bytes() for rel in pipeline.ROLLBACK_SOURCES}
    seen = []

    def launch(command, **kwargs):
        script = Path(command[1]).name
        seen.append(script)
        if script == "daily_media_refresh.py":
            return CompletedProcess(1073807364)
        if script == "update_bill_repository.py":
            (tmp_path / "bill_repository_live.json").write_bytes(b'{"bills": {}, "generation": "fetched"}\n')
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(tmp_path, include_media=True) == 1
    assert seen[-1] == "daily_media_refresh.py"
    assert not {"update_bill_repository.py", "refresh_billstatus_bulk.py", "run_pipeline.py"} & set(seen)
    for rel, data in original.items():
        assert (tmp_path / rel).read_bytes() == data, rel
    receipt = pipeline.read(tmp_path / pipeline.STATUS)
    assert receipt["status"] == "FAILED" and "exit 1073807364" in receipt["error"]
    # pipeline-health labels: the sidecar is bound to the untouched live repository, so nothing is restored.
    assert receipt["rollback"] == "verified" and receipt["rollback_membership_load"] == "PASS"


# --- Status writes are telemetry: a denied rename must not cost a run ---------
# 2026-10: PermissionError WinError 5 renaming congress_pipeline_status.json.<pid>.tmp
# from the child-wait heartbeat killed 2 of the last 5 daily runs. Only the status
# marker is best-effort; receipts and artifacts keep raising.


class HeartbeatProcess(CompletedProcess):
    """Pending until its first 15 s wait times out (one heartbeat write), then exits."""

    def __init__(self, returncode=0):
        super().__init__(returncode)
        self.beats = 0

    def poll(self):
        return self.returncode if self.beats else None

    def wait(self, timeout=None):
        self.beats += 1
        raise subprocess.TimeoutExpired("fixture", timeout)


def deny_rename(monkeypatch, target_name, statuses):
    """Refuse the rename onto target_name while the document's status is in statuses."""
    real = pipeline.replace_atomic
    refused = []

    def replace(scratch, path, **kwargs):
        document = json.loads(Path(scratch).read_text(encoding="utf-8"))
        if Path(path).name == target_name and document.get("status") in statuses:
            refused.append(document)
            os.unlink(scratch)  # what the real replace_atomic does on its final failure
            raise PermissionError(13, "Access is denied", str(path))
        return real(scratch, path, **kwargs)

    monkeypatch.setattr(pipeline, "replace_atomic", replace)
    return refused


def heartbeat_run(root, monkeypatch):
    write_json(root, "ranks_python.json", {"rank": {"Member A": 1}})
    monkeypatch.setattr(pipeline, "terminate_tree",
                        lambda child: pytest.fail("a status write must never cost the child its life"))

    def launch(command, **kwargs):
        script = Path(command[1]).name
        if script == "run_pipeline.py":
            install_generation(root)
        return HeartbeatProcess() if script == "calibration_guard.py" else CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    return pipeline.run(root)


def test_denied_heartbeat_write_does_not_kill_the_run_or_change_its_exit_code(tmp_path, monkeypatch):
    clean = heartbeat_run(tmp_path / "clean", monkeypatch)
    refused = deny_rename(monkeypatch, pipeline.STATUS.name, {"RUNNING"})
    faulty = heartbeat_run(tmp_path / "faulty", monkeypatch)
    assert clean == faulty == 0
    # The in-loop heartbeat itself (the only write that carries child_pid) was refused.
    assert any(document.get("child_pid") for document in refused)
    root = tmp_path / "faulty"
    receipt = pipeline.read(root / pipeline.RECEIPT)
    baseline = pipeline.read(tmp_path / "clean" / pipeline.RECEIPT)
    assert receipt["status"] == baseline["status"] == "PASS"
    assert receipt["rank_changes"] == baseline["rank_changes"]
    assert [r["returncode"] for r in receipt["steps"]] == [r["returncode"] for r in baseline["steps"]]
    assert pipeline.read(root / pipeline.STATUS)["status"] == "PASS"  # the next write retried naturally
    log = (root / pipeline.LIVE_LOG).read_text(encoding="utf-8")
    assert "WRITE_FAILED" in log and "Access is denied" in log
    assert not (root / ".pipeline_run.lock").exists()


def test_terminal_pass_marker_is_retried_once_and_a_denial_keeps_the_exit_code(tmp_path, monkeypatch):
    refused = deny_rename(monkeypatch, pipeline.STATUS.name, {"PASS"})
    assert heartbeat_run(tmp_path, monkeypatch) == 0
    assert len(refused) == 2  # one attempt plus the single retry, then the marker is abandoned
    receipt = pipeline.read(tmp_path / pipeline.RECEIPT)
    assert receipt["status"] == "PASS"
    # The daily wrapper reads this immutable receipt, never the status snapshot.
    assert pipeline.read(Path(receipt["run_dir"]) / "receipt.json") == receipt
    log = (tmp_path / pipeline.LIVE_LOG).read_text(encoding="utf-8")
    assert "attempt 1 of 2" in log and "attempt 2 of 2" in log
    assert not (tmp_path / ".pipeline_run.lock").exists()


def test_terminal_failed_marker_denial_keeps_exit_one_and_the_failed_receipt(tmp_path, monkeypatch):
    refused = deny_rename(monkeypatch, pipeline.STATUS.name, {"FAILED"})
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda command, **kwargs: CompletedProcess(4))
    assert pipeline.run(tmp_path) == 1  # before the fix this raised PermissionError out of run()
    assert len(refused) == 2
    run_dir, = (tmp_path / "audits/congress_pipeline").iterdir()
    receipt = pipeline.read(run_dir / "receipt.json")
    assert receipt["status"] == "FAILED" and "exit 4" in receipt["error"]
    assert not (tmp_path / pipeline.RECEIPT).exists()
    assert not (tmp_path / ".pipeline_run.lock").exists()


def test_a_denied_receipt_write_is_still_fatal(tmp_path, monkeypatch):
    refused = deny_rename(monkeypatch, "receipt.json", {"PASS"})
    assert heartbeat_run(tmp_path, monkeypatch) == 1
    assert refused  # the run-dir receipt is evidence, not telemetry
    run_dir, = (tmp_path / "audits/congress_pipeline").iterdir()
    receipt = pipeline.read(run_dir / "receipt.json")
    assert receipt["status"] == "FAILED" and "PermissionError" in receipt["error"]
    assert not (tmp_path / pipeline.RECEIPT).exists()
