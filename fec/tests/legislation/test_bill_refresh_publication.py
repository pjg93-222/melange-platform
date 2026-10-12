"""Operational regressions for the collector -> workbook -> dashboard command."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from legislation import update_bill_repository as updater
import pipeline_lock


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", str(tmp_path))
    receipt_path = tmp_path / "data/engine/congress_bill_refresh_receipt.json"
    monkeypatch.setattr(updater, "DEFAULT_RECEIPT", receipt_path)
    bundle = tmp_path / "member_desk_bundle.json"
    bundle.write_text(json.dumps({"members": [{"bills": [
        {"bill": "H.R.7", "congress": 119, "title": "Test Act", "areas": ["Payments"]}
    ]}]}), encoding="utf-8")
    args = updater.parser().parse_args(["--rebuild-dashboard"])
    state = {"locked": False, "events": []}
    lease = {"pid": 123, "token": "test-token", "adopted": False}

    @contextmanager
    def lock(**kwargs):
        assert not state["locked"]
        state["locked"] = True
        state["events"].append("lock")
        try:
            yield lease
        finally:
            state["locked"] = False
            state["events"].append("unlock")

    def preflight(value):
        assert state["locked"] and value is lease
        state["events"].append("preflight")

    def refresh(**kwargs):
        assert state["locked"]
        state["events"].append("refresh")
        kwargs["output_path"].write_text('{"fresh": true}', encoding="utf-8")
        return {"status": state.get("status", "PASS"), "seeded": 1,
                "refreshed": 1, "failed": 0}

    monkeypatch.setattr(pipeline_lock, "pipeline_run_lock", lock)
    monkeypatch.setattr(updater, "publication_preflight", preflight)
    monkeypatch.setattr(updater, "refresh_once", refresh)
    return args, state, receipt_path, lease


def test_pass_receipt_authorizes_current_bytes_before_downstream(cycle, monkeypatch):
    args, state, path, lease = cycle
    bound_before = {}

    def rebuild(value):
        assert state["locked"] and value is lease
        state["events"].append("downstream")
        receipt = updater.read_json(path)
        assert receipt["status"] == "PASS"
        assert receipt["repository"]["sha256"] == updater.sha256_file(args.output)
        assert receipt["publication"]["status"] == "PENDING"
        bound_before.update(receipt)
        # Publication can rewrite the bundle. Its new universe must never be
        # retroactively declared as the universe collected by this scrape.
        args.bundle.write_text('{"members": []}', encoding="utf-8")
        return {"combined_dashboard": "Moneyball.html"}

    monkeypatch.setattr(updater, "rebuild_dashboard", rebuild)
    result, code = updater.refresh_cycle(args)
    assert code == 0 and result["publication"]["status"] == "PASS"
    assert state["events"] == ["lock", "preflight", "refresh", "downstream", "unlock"]
    final = updater.read_json(path)
    assert final["seeds"] == bound_before["seeds"]
    assert final["completed_at"] == bound_before["completed_at"]


@pytest.mark.parametrize("status", ["PARTIAL", "STALE", "FAIL"])
def test_incomplete_collection_never_runs_downstream(cycle, monkeypatch, status):
    args, state, path, _lease = cycle
    state["status"] = status
    monkeypatch.setattr(updater, "rebuild_dashboard", lambda _: pytest.fail("published incomplete scrape"))
    result, code = updater.refresh_cycle(args)
    assert code == 1 and result["status"] == status
    assert updater.read_json(path)["status"] == status
    assert not state["locked"]


def test_downstream_failure_retains_authentic_scrape_and_reports_failure(cycle, monkeypatch):
    args, state, path, _lease = cycle

    def rebuild(_):
        assert state["locked"]
        raise RuntimeError("Excel file is locked")

    monkeypatch.setattr(updater, "rebuild_dashboard", rebuild)
    result, code = updater.refresh_cycle(args)
    assert code == 1 and result["status"] == "PASS"
    receipt = updater.read_json(path)
    assert receipt["status"] == "PASS"
    assert receipt["publication"] == {"status": "FAIL", "error": "RuntimeError: Excel file is locked"}
    assert not state["locked"]


def test_lock_refusal_does_not_replace_another_runs_receipt(cycle, monkeypatch):
    _args, state, path, _lease = cycle
    path.parent.mkdir(parents=True)
    path.write_text('{"status": "PASS", "owner": "other"}', encoding="utf-8")
    original = path.read_bytes()

    @contextmanager
    def blocked(**kwargs):
        raise RuntimeError("another collector owns the lease")
        yield

    monkeypatch.setattr(pipeline_lock, "pipeline_run_lock", blocked)
    monkeypatch.setattr(sys, "argv", ["update_bill_repository.py"])
    assert updater.main() == 1
    assert path.read_bytes() == original
    assert state["events"] == []


def test_watch_releases_writer_lease_before_sleep(cycle, monkeypatch):
    _args, state, _path, _lease = cycle
    monkeypatch.setattr(sys, "argv", ["update_bill_repository.py", "--watch"])

    def stop_after_first_cycle(seconds):
        assert not state["locked"]
        raise KeyboardInterrupt

    monkeypatch.setattr(updater.time, "sleep", stop_after_first_cycle)
    with pytest.raises(KeyboardInterrupt):
        updater.main()


def test_rebuild_rejects_noncanonical_receipt_before_collection(cycle, monkeypatch):
    _args, state, _path, _lease = cycle
    monkeypatch.setattr(sys, "argv", ["update_bill_repository.py", "--rebuild-dashboard",
                                      "--receipt", "different-receipt.json"])
    with pytest.raises(SystemExit) as stopped:
        updater.main()
    assert stopped.value.code == 2
    assert state["events"] == []


def test_inherited_rebuild_is_refused_before_preflight_or_collection():
    with pytest.raises(RuntimeError, match="lease owner must invoke run_pipeline"):
        updater.publication_preflight({"adopted": True})


@pytest.mark.parametrize("workbook_status", ["PASS", "SKIPPED_RUNTIME_ABSENT"])
def test_rebuild_runs_complete_chain_and_requires_current_excel(tmp_path, monkeypatch, workbook_status):
    monkeypatch.setattr(updater, "ROOT", str(tmp_path))
    monkeypatch.setenv("DESK_OUT", "unexpected-output")
    monkeypatch.setenv("DASH_OUT", "unexpected-output")
    repository = tmp_path / "bill_repository_live.json"
    repository.write_text('{"bills": {}}', encoding="utf-8")
    lease = {"pid": 123, "token": "test-token", "adopted": False}
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert command == [sys.executable, str(tmp_path / "fec/run_pipeline.py"),
                           "--from", "verify_collector_roster_updates.py"]
        assert kwargs["check"] is True
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"][pipeline_lock.LOCK_TOKEN_ENV] == "test-token"
        assert kwargs["env"][pipeline_lock.LOCK_OWNER_PID_ENV] == "123"
        assert "DESK_OUT" not in kwargs["env"] and "DASH_OUT" not in kwargs["env"]
        workbook = tmp_path / "Sentinel Bill Ledger.xlsx"
        workbook.write_bytes(b"new Excel bytes")
        receipt = tmp_path / "data/engine/sentinel_bill_workbook_receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({
            "status": workbook_status, "installed": workbook_status == "PASS",
            "repository_sha256": updater.sha256_file(repository),
            "live_ledger_workbook": {"sha256": updater.sha256_file(workbook)},
        }), encoding="utf-8")

    monkeypatch.setattr(updater.subprocess, "run", run)
    if workbook_status == "PASS":
        result = updater.rebuild_dashboard(lease)
        assert result["workbook"] == str(tmp_path / "Sentinel Bill Ledger.xlsx")
    else:
        with pytest.raises(RuntimeError, match="did not publish the refreshed Excel"):
            updater.rebuild_dashboard(lease)
    assert len(commands) == 1
