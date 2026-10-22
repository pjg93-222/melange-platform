"""Congress.gov bill discovery is a soft step: a failure never hard-aborts the weekly refresh.

weekly_refresh.py cannot be imported (module scope takes locks and runs the refresh), so the
real step(), _soft_fail() and the module-level discovery and abort statements are executed from
the source text over a stubbed child process. Discovery yields pending-only candidates that
cannot score, so downstream keeps using the last-good discovery state; the failure is recorded
in the status marker's soft_failures and the completion summary, never in the abort list.

The same sandbox pins DE-043: the bill repository fetch and the membership sidecar rebind are one
unit, so the weekly takes a before-image of the repository ahead of the fetch and every hard
abort rolls back to it while the sidecar still names it (2026-10-18 weekly: aborted at the press
engine feed after the fetch, the sidecar stayed bound to the older repository).
"""
from __future__ import annotations

import ast
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))
SOURCE = (FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)
DISCOVERY = "Congress.gov bill discovery"


class HardAbort(Exception):
    pass


def function_source(name):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(SOURCE, node)


def statement(predicate):
    return next(node for node in TREE.body if predicate(node))


def is_step_guard(node, step_name):
    test = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Call) and test.operand.args
            and isinstance(test.operand.args[0], ast.Constant) and test.operand.args[0].value == step_name)


def is_abort_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
            and node.test.id == "_deferred_collection_failures")


def run_statement(node, namespace):
    exec(compile(ast.Module(body=[node], type_ignores=[]), "weekly_refresh.py", "exec"), namespace)


def weekly_world(*, returncode=1, timed_out=False):
    logs, statuses, events, aborts = [], [], [], []

    def run_bounded(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="boom", returncode=returncode, timed_out=timed_out,
                               timeout_note="timed out")

    def hard_abort(name, failures, why):
        aborts.append((name, list(failures), why))
        raise HardAbort(why)

    spec = SimpleNamespace(argv=[sys.executable], env=None, phase="collect", container=None)
    namespace = {
        "bounded_subprocess": SimpleNamespace(run_bounded=run_bounded),
        "compute_engine": SimpleNamespace(launch=lambda *args, **kwargs: spec, reap=lambda s: None),
        "subprocess": subprocess, "sys": sys, "os": os, "time": time, "datetime": datetime,
        "FE": str(FEC), "_LIVE_CHILD": [None], "_tolerated": [], "_soft_failures": [],
        "_deferred_collection_failures": [],
        "_producer_path": lambda name: os.path.join(str(FEC), *name.split("/")),
        "_step_log": lambda name: None, "_chain_tail_is_audit_full": lambda: True, "log": logs.append,
        "_write_status": lambda **kw: statuses.append(kw),
        "_emit": lambda *args, **kwargs: events.append((args, kwargs)), "_hard_abort": hard_abort,
        "_lease_handoff_out": lambda: None, "_lease_handoff_in": lambda: None,
    }
    for name in ("_adopt_live_child", "_soft_fail", "step"):
        exec(function_source(name), namespace)
    return SimpleNamespace(ns=namespace, logs=logs, statuses=statuses, events=events, aborts=aborts)


def run_discovery(world):
    run_statement(statement(lambda node: is_step_guard(node, DISCOVERY)), world.ns)


@pytest.mark.parametrize("failure", [{"returncode": 1}, {"returncode": 124, "timed_out": True}])
def test_a_failed_discovery_is_a_soft_failure_not_a_deferred_collection_prerequisite(failure):
    world = weekly_world(**failure)
    run_discovery(world)
    assert world.ns["_deferred_collection_failures"] == []
    assert world.ns["_soft_failures"] == [DISCOVERY]
    assert world.aborts == []
    assert {"soft_failures": [DISCOVERY]} in world.statuses
    failed = [(args, kwargs) for args, kwargs in world.events if args[:2] == ("refresh.step", "failed")]
    assert len(failed) == 1 and failed[0][1]["metadata"]["hard"] is False
    assert any("last-good candidate set retained" in line for line in world.logs)


def test_discovery_alone_does_not_trip_the_deferred_prerequisites_abort():
    world = weekly_world()
    run_discovery(world)
    run_statement(statement(is_abort_guard), world.ns)  # the hard abort that follows press acceptance
    assert world.aborts == []


def test_any_other_deferred_collection_failure_still_aborts_after_press_acceptance():
    world = weekly_world()
    run_discovery(world)
    world.ns["_deferred_collection_failures"].append("interviews: CNN (14d)")
    with pytest.raises(HardAbort):
        run_statement(statement(is_abort_guard), world.ns)
    assert world.aborts[0][0] == "deferred collection prerequisites"
    assert world.aborts[0][1] == ["interviews: CNN (14d)"]


def test_the_completion_status_and_summary_report_soft_failures():
    final = next(node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "_write_status"
                 and any(k.arg == "last_outcome" and isinstance(k.value, ast.IfExp) for k in node.keywords))
    assert "soft_failures" in {k.arg for k in final.keywords}
    assert "non-hard step(s) failed" in SOURCE


# --- DE-043: the repository fetch and the membership rebind are one unit -----------------------

BEFORE = b'{"bills": {}, "generation": "before"}'
FETCHED = b'{"bills": {}, "generation": "fetched"}'
RECEIPT = "data/engine/congress_bill_refresh_receipt.json"
SIDECAR = "data/legislation/official_membership_movements.json"


def abort_world(root, *, armed=False, lease_held=True):
    """The real _snapshot_repository_before_image() and _hard_abort() over a temp root; everything else records."""
    events = []
    namespace = {
        "os": os, "sys": sys, "datetime": datetime, "MB_ROOT": str(root), "_started_at": datetime.datetime.now(),
        "_RUN_LOG_DIR": str(root / "audits" / "weekly_refresh" / "20261018T000000Z"),
        "_REPO_BEFORE_IMAGE": [armed],
        "_LEASE_HELD": [lease_held],
        "log": lambda line: events.append(("log", line)),
        "_write_status": lambda **kw: events.append(("status", kw)),
        "_emit": lambda *args, **kwargs: events.append(("emit", args)),
    }
    for name in ("_snapshot_repository_before_image", "_discard_repository_before_image", "_hard_abort"):
        exec(function_source(name), namespace)
    return SimpleNamespace(ns=namespace, events=events)


def write_repository(root, repository=BEFORE):
    """A repository, its fetch receipt, and a membership sidecar bound to that repository."""
    from datetime import timezone
    from legislation import refresh_congress_pipeline as pipeline
    (root / "bill_repository_live.json").write_bytes(repository)
    (root / RECEIPT).parent.mkdir(parents=True, exist_ok=True)
    (root / RECEIPT).write_text('{"generation": "before"}', encoding="utf-8")
    (root / SIDECAR).parent.mkdir(parents=True, exist_ok=True)
    (root / SIDECAR).write_text(json.dumps({
        "schema": "official-membership-movements-v1", "status": "PASS",
        "checked_at": datetime.datetime.now(timezone.utc).isoformat(), "facts": [], "additions": [],
        "repository_sha256": pipeline.digest(root / "bill_repository_live.json")}), encoding="utf-8")


def spy_on_rollback(monkeypatch, *, raises=None):
    from legislation import refresh_congress_pipeline as pipeline
    calls, real = [], pipeline.rollback_unbound_repository

    def spy(root, run_dir):
        calls.append((str(root), str(run_dir)))
        if raises is not None:
            raise raises
        return real(root, run_dir)

    monkeypatch.setattr(pipeline, "rollback_unbound_repository", spy)
    return calls


def abort(world, step="press engine feed"):
    with pytest.raises(SystemExit) as stop:
        world.ns["_hard_abort"](step, [], "step exited 1")
    return stop.value.code


def test_the_repository_before_image_is_taken_before_the_repository_step():
    snapshots = [i for i, node in enumerate(TREE.body) if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "_snapshot_repository_before_image"]
    fetch = next(i for i, node in enumerate(TREE.body) if isinstance(node, ast.Assign)
                 and any(getattr(t, "id", None) == "_bill_repository_ok" for t in node.targets))
    assert len(snapshots) == 1 and snapshots[0] < fetch


def test_the_before_image_copies_the_repository_and_its_receipt_into_the_run_folder(tmp_path):
    from legislation import refresh_congress_pipeline as pipeline
    write_repository(tmp_path)
    world = abort_world(tmp_path)
    world.ns["_snapshot_repository_before_image"]()
    for relative in pipeline.ROLLBACK_SOURCES:
        assert (Path(world.ns["_RUN_LOG_DIR"]) / "before" / relative).read_bytes() == (tmp_path / relative).read_bytes()
    assert world.ns["_REPO_BEFORE_IMAGE"] == [True]


def test_a_hard_abort_after_the_fetch_rolls_the_repository_back_to_the_before_image(tmp_path, monkeypatch):
    # the 2026-10-18 weekly: sidecar bound to the old repository, the fetch lands, then the press engine feed aborts
    write_repository(tmp_path)
    world = abort_world(tmp_path)
    world.ns["_snapshot_repository_before_image"]()
    world.events.clear()  # the abort's own sequence only
    (tmp_path / "bill_repository_live.json").write_bytes(FETCHED)
    (tmp_path / RECEIPT).write_text('{"generation": "fetched"}', encoding="utf-8")
    calls = spy_on_rollback(monkeypatch)
    assert abort(world) == 1
    assert calls == [(str(tmp_path), world.ns["_RUN_LOG_DIR"])]
    assert (tmp_path / "bill_repository_live.json").read_bytes() == BEFORE
    assert json.loads((tmp_path / RECEIPT).read_text(encoding="utf-8")) == {"generation": "before"}
    kinds = [kind for kind, _ in world.events]
    assert kinds[:3] == ["log", "emit", "status"] and set(kinds[3:]) == {"log"}, "the abort is recorded before the rollback runs"
    line = [text for kind, text in world.events if kind == "log"][-1]
    assert "repository rollback" in line and "restored" in line and "PASS" in line


def test_a_rollback_that_raises_does_not_change_the_abort(tmp_path, monkeypatch):
    write_repository(tmp_path)
    clean, broken = abort_world(tmp_path, armed=True), abort_world(tmp_path, armed=True)
    spy_on_rollback(monkeypatch)
    clean_code = abort(clean)
    spy_on_rollback(monkeypatch, raises=RuntimeError("disk gone"))
    assert abort(broken) == clean_code == 1
    statuses = [kw for kind, kw in broken.events if kind == "status"]
    assert statuses == [kw for kind, kw in clean.events if kind == "status"]
    assert statuses[0]["last_outcome"] == "HARD_FAILURE" and statuses[0]["failures"] == ["press engine feed"]
    assert broken.events[0] == clean.events[0]  # the HARD FAILURE line comes first, exactly as before
    assert any("RuntimeError: disk gone" in text for kind, text in broken.events if kind == "log")


def test_no_before_image_means_no_rollback(tmp_path, monkeypatch):
    write_repository(tmp_path)
    world = abort_world(tmp_path)
    calls = spy_on_rollback(monkeypatch)
    assert abort(world) == 1
    assert calls == []


def test_a_failed_before_image_is_logged_not_raised_and_arms_no_rollback(tmp_path, monkeypatch):
    write_repository(tmp_path)
    world = abort_world(tmp_path)
    monkeypatch.setattr(shutil, "copy2", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    world.ns["_snapshot_repository_before_image"]()
    assert world.ns["_REPO_BEFORE_IMAGE"] == [False]
    assert any("NOT taken" in text and "disk full" in text for kind, text in world.events if kind == "log")
    calls = spy_on_rollback(monkeypatch)
    abort(world)
    assert calls == []


def test_a_missing_receipt_is_logged_and_the_repository_alone_still_arms_the_rollback(tmp_path):
    write_repository(tmp_path)
    (tmp_path / RECEIPT).unlink()
    world = abort_world(tmp_path)
    world.ns["_snapshot_repository_before_image"]()
    before = Path(world.ns["_RUN_LOG_DIR"]) / "before"
    assert (before / "bill_repository_live.json").is_file() and not (before / RECEIPT).exists()
    assert world.ns["_REPO_BEFORE_IMAGE"] == [True]
    assert any(RECEIPT in text and "absent" in text and "leave the receipt" in text for kind, text in world.events if kind == "log")


def test_a_missing_repository_arms_no_rollback(tmp_path):
    write_repository(tmp_path)
    (tmp_path / "bill_repository_live.json").unlink()
    world = abort_world(tmp_path)
    world.ns["_snapshot_repository_before_image"]()
    assert world.ns["_REPO_BEFORE_IMAGE"] == [False]
    assert any("bill_repository_live.json" in text and "absent" in text for kind, text in world.events if kind == "log")
    assert any("NOT taken" in text for kind, text in world.events if kind == "log")


def test_the_before_image_is_kept_on_an_abort_and_freed_on_complete_without_touching_status(tmp_path):
    write_repository(tmp_path)
    world = abort_world(tmp_path)
    world.ns["_snapshot_repository_before_image"]()
    before = Path(world.ns["_RUN_LOG_DIR"]) / "before"
    abort(world)
    assert (before / "bill_repository_live.json").is_file(), "an abort keeps the before-image"
    world.events.clear()
    world.ns["_discard_repository_before_image"]()
    assert not before.exists()
    assert [kind for kind, _ in world.events] == [], "freeing the before-image writes no status, emit or log line"


def test_a_before_image_that_cannot_be_freed_is_logged_not_raised(tmp_path, monkeypatch):
    write_repository(tmp_path)
    world = abort_world(tmp_path)
    world.ns["_snapshot_repository_before_image"]()
    world.events.clear()
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("locked")))
    world.ns["_discard_repository_before_image"]()
    assert [kind for kind, _ in world.events] == ["log"] and "PermissionError: locked" in world.events[0][1]
    assert (Path(world.ns["_RUN_LOG_DIR"]) / "before" / "bill_repository_live.json").is_file()


def test_the_before_image_is_freed_only_after_the_complete_status_is_written():
    calls = [i for i, node in enumerate(TREE.body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
             and getattr(node.value.func, "id", None) == "_discard_repository_before_image"]
    final = next(i for i, node in enumerate(TREE.body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and getattr(node.value.func, "id", None) == "_write_status"
                 and any(k.arg == "last_outcome" and isinstance(k.value, ast.IfExp) for k in node.value.keywords))
    assert len(calls) == 1 and calls[0] > final
    assert "_discard_repository_before_image" not in function_source("_hard_abort")


def test_collector_roster_verification_precedes_the_chain():
    # the rollback is a no-op once the sidecar is rebound, and verify_collector_roster_updates.py is what rebinds it
    # (official_membership_movements.build/write); the chain's own rebind comes later, so a hard abort after this step must not roll back
    lines = {}
    for node in ast.walk(TREE):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "step" and len(node.args) > 1
                and isinstance(node.args[1], ast.List) and node.args[1].elts and isinstance(node.args[1].elts[0], ast.Constant)):
            lines.setdefault(os.path.basename(node.args[1].elts[0].value), []).append(node.lineno)
    assert len(lines["verify_collector_roster_updates.py"]) == 1 and len(lines["run_pipeline.py"]) == 1
    assert lines["verify_collector_roster_updates.py"][0] < lines["run_pipeline.py"][0]


def drifted_world(root):
    """The 2026-10-16 shape: the sidecar names a third repository, then this run fetches and aborts."""
    import hashlib
    write_repository(root)
    sidecar = json.loads((root / SIDECAR).read_text(encoding="utf-8"))
    sidecar["repository_sha256"] = hashlib.sha256(b"a repository neither the before-image nor the live file").hexdigest()
    (root / SIDECAR).write_text(json.dumps(sidecar), encoding="utf-8")
    world = abort_world(root)
    world.ns["_snapshot_repository_before_image"]()
    world.events.clear()
    (root / "bill_repository_live.json").write_bytes(FETCHED)
    return world


def test_an_unhealable_drift_rollback_is_logged_and_emitted_and_leaves_the_abort_unchanged(tmp_path, monkeypatch):
    world = drifted_world(tmp_path)
    clean = abort_world(tmp_path, armed=False)
    calls = spy_on_rollback(monkeypatch)
    assert abort(world) == abort(clean) == 1
    assert len(calls) == 1
    assert (tmp_path / "bill_repository_live.json").read_bytes() == FETCHED, "unhealable drift restores nothing"
    statuses = [kw for kind, kw in world.events if kind == "status"]
    assert statuses == [kw for kind, kw in clean.events if kind == "status"], "the drift writes no status of its own"
    logs = [text for kind, text in world.events if kind == "log"]
    assert any("unhealable_drift" in text for text in logs)
    assert any("UNHEALABLE DRIFT" in text and "rebound" in text and "membership_sha256" in text for text in logs)
    emits = [args for kind, args in world.events if kind == "emit"]
    assert emits[0][:2] == ("refresh.run", "failed"), "the abort's own event still comes first"
    assert emits[1][:2] == ("refresh.step", "failed") and "unhealable drift" in emits[1][2]
    assert not any("membership load did NOT pass" in text for text in logs), "drift skips the load on purpose"


@pytest.mark.parametrize("result, expect", [
    ({"rollback": "verified", "rollback_membership_load": "PASS"}, "restored nothing"),
    ({"rollback": "verified", "rollback_membership_load": "FileNotFoundError: gone"}, "membership load did NOT pass"),
    ({"rollback": "restored", "rollback_membership_load": "ValueError: bad"}, "membership load did NOT pass"),
    ({"rollback": "restore_failed", "rollback_membership_load": "PermissionError: locked"}, "RESTORE FAILED part-way"),
    ({"rollback": "some_future_label", "rollback_membership_load": "PASS"}, "unrecognised label 'some_future_label'"),
    ("not a dict", "unrecognised label None"),
])
def test_every_rollback_label_is_named_in_the_log_without_changing_the_abort(tmp_path, monkeypatch, result, expect):
    from legislation import refresh_congress_pipeline as pipeline
    monkeypatch.setattr(pipeline, "rollback_unbound_repository", lambda root, run_dir: result)
    world = abort_world(tmp_path, armed=True)
    assert abort(world) == 1
    assert [kind for kind, _ in world.events][:3] == ["log", "emit", "status"]
    assert [kind for kind, _ in world.events][3:] == ["log"] * (len(world.events) - 3), "no extra status or emit"
    assert any(expect in text for kind, text in world.events if kind == "log")


def test_an_abort_without_the_writer_lease_keeps_the_before_image_and_does_not_roll_back(tmp_path, monkeypatch):
    # CodeRabbit, PR #16: a chain that timed out (the take-back is skipped) or a take-back that failed (another writer owns the
    # lease) reaches _hard_abort without the lease; the rollback would then replace the repository under someone else's lease.
    world = drifted_world(tmp_path)   # any armed world: the guard comes before the helper
    world.ns["_LEASE_HELD"][0] = False
    clean = abort_world(tmp_path, armed=False)
    calls = spy_on_rollback(monkeypatch)
    assert abort(world) == abort(clean) == 1
    assert calls == []
    assert (tmp_path / "bill_repository_live.json").read_bytes() == FETCHED
    assert (Path(world.ns["_RUN_LOG_DIR"]) / "before" / "bill_repository_live.json").read_bytes() == BEFORE
    assert [kw for kind, kw in world.events if kind == "status"] == [kw for kind, kw in clean.events if kind == "status"]
    assert any("SKIPPED" in text and "writer lease" in text for kind, text in world.events if kind == "log")


def test_the_lease_handoff_clears_and_restores_the_held_flag():
    class Lock:
        def __init__(self, fail=False):
            self.fail = fail
        def __enter__(self):
            if self.fail:
                raise RuntimeError("held by another writer")
            return "lease"
        def __exit__(self, *exc):
            return False
    aborts = []
    namespace = {"os": os, "LOCK_TOKEN_ENV": "X_TOKEN", "LOCK_OWNER_PID_ENV": "X_PID", "_PIPELINE_LOCK_CONTEXT": Lock(),
                 "_LEASE_HELD": [True], "log": lambda line: None, "child_lock_environment": lambda lease: {},
                 "_hard_abort": lambda *args: aborts.append(args)}
    for name in ("_lease_handoff_out", "_lease_handoff_in"):
        exec(function_source(name), namespace)
    namespace["pipeline_run_lock"] = lambda **kw: Lock()
    namespace["_lease_handoff_out"]()
    assert namespace["_LEASE_HELD"] == [False]
    namespace["_lease_handoff_in"]()
    assert namespace["_LEASE_HELD"] == [True] and aborts == []
    namespace["_lease_handoff_out"]()
    namespace["pipeline_run_lock"] = lambda **kw: Lock(fail=True)
    namespace["_lease_handoff_in"]()
    assert namespace["_LEASE_HELD"] == [False] and aborts and aborts[0][0] == "chain container lease"
    assert "_LEASE_HELD=[True]" in SOURCE
