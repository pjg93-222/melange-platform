"""Rollback drift (2026-10-19, pipeline-health Fix 2): a membership sidecar bound to NEITHER the before-image
repository NOR the live one cannot be healed by a rollback, and must be recorded as such rather than as "verified".

A 10-16 run died without its failure handler, so the sidecar stayed bound to a repository that no snapshot holds.
Every later failing run then restored nothing, called official_membership_movements.load (which refuses on a
repository-sha mismatch, deliberately) and recorded rollback="verified" beside a load error. The strict check in
official_membership_movements.py is unchanged; rollback_unbound_repository now names the drift instead.

Everything runs against tmp_path. No live tree, collector or heal/roster-verify step is touched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import official_membership_movements as membership
from legislation import refresh_congress_pipeline as pipeline
from pipeline_lock import LOCK_OWNER_PID_ENV, LOCK_TOKEN_ENV

REPOSITORY = "bill_repository_live.json"
RECEIPT = Path("data/engine/congress_bill_refresh_receipt.json")


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv(LOCK_TOKEN_ENV, raising=False)
    monkeypatch.delenv(LOCK_OWNER_PID_ENV, raising=False)
    monkeypatch.setattr(pipeline, "_emit", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "run_pipeline", SimpleNamespace(preflight_dependencies=lambda: None))
    monkeypatch.setitem(sys.modules, "legislation.sync_sentinel_bill_ledger",
                        SimpleNamespace(runtime=lambda: ("fixture", "node", "modules")))


def sidecar(root, bound):
    pipeline.write(root / pipeline.MEMBERSHIP_SIDECAR, {
        "schema": "official-membership-movements-v1", "status": "PASS",
        "checked_at": datetime.now(timezone.utc).isoformat(), "facts": [], "additions": [],
        "repository_sha256": bound})


def world(tmp_path, *, live=b'{"bills": {}, "generation": "fetched"}\n', before=b'{"bills": {}, "generation": "before"}\n'):
    """A root with a live repository, a run_dir/before image of it (None = no image), and no sidecar yet."""
    root, run_dir = tmp_path / "root", tmp_path / "run"
    (run_dir / "before").mkdir(parents=True)
    root.mkdir()
    (root / REPOSITORY).write_bytes(live)
    if before is not None:
        (run_dir / "before" / REPOSITORY).write_bytes(before)
    return root, run_dir


def test_drift_matching_neither_snapshot_nor_live_is_recorded_not_loaded(tmp_path, monkeypatch):
    root, run_dir = world(tmp_path)
    sidecar(root, "f" * 64)                                   # a repository no copy on disk holds
    load = Mock(side_effect=AssertionError("a drifted sidecar must never reach the raising load"))
    monkeypatch.setattr(membership, "load", load)
    live_before = (root / REPOSITORY).read_bytes()

    result = pipeline.rollback_unbound_repository(root, run_dir)

    load.assert_not_called()
    assert result["rollback"] == "unhealable_drift"
    assert result["rollback_drift"] == {
        "membership_sha256": "f" * 64,
        "live_repository_sha256": pipeline.digest(root / REPOSITORY),
        "before_snapshot_sha256": pipeline.digest(run_dir / "before" / REPOSITORY)}
    assert result["rollback_membership_load"].startswith("SKIPPED")   # the raising load was not attempted
    assert (root / REPOSITORY).read_bytes() == live_before    # nothing was "restored" over a repository we cannot bind


def test_drift_with_no_before_image_is_still_unhealable_drift(tmp_path, monkeypatch):
    root, run_dir = world(tmp_path, before=None)
    sidecar(root, "e" * 64)
    monkeypatch.setattr(membership, "load", Mock(side_effect=AssertionError("must not load")))

    result = pipeline.rollback_unbound_repository(root, run_dir)

    assert result["rollback"] == "unhealable_drift"
    assert result["rollback_drift"]["before_snapshot_sha256"] is None
    assert result["rollback_drift"]["membership_sha256"] == "e" * 64


def test_drift_is_logged_loudly(tmp_path, monkeypatch, capsys):
    root, run_dir = world(tmp_path)
    sidecar(root, "d" * 64)
    monkeypatch.setattr(membership, "load", Mock())

    pipeline.rollback_unbound_repository(root, run_dir)

    assert "unhealable_drift" in capsys.readouterr().err.lower()
    log = (root / pipeline.LIVE_LOG).read_text(encoding="utf-8")
    assert "UNHEALABLE_DRIFT" in log and "d" * 64 in log


def test_sidecar_bound_to_the_before_image_is_still_restored_byte_exact(tmp_path):
    # (b) the DE-043 case is unchanged: the sidecar names the before-image, the fetch replaced the repository.
    root, run_dir = world(tmp_path)
    before = (run_dir / "before" / REPOSITORY).read_bytes()
    (run_dir / "before" / RECEIPT).parent.mkdir(parents=True)
    (run_dir / "before" / RECEIPT).write_bytes(b'{"gen": "before"}\n')
    (root / RECEIPT).parent.mkdir(parents=True)
    (root / RECEIPT).write_bytes(b'{"gen": "fetched"}\n')
    sidecar(root, pipeline.digest(run_dir / "before" / REPOSITORY))

    result = pipeline.rollback_unbound_repository(root, run_dir)

    assert result == {"rollback": "restored", "rollback_membership_load": "PASS"}
    assert (root / REPOSITORY).read_bytes() == before
    assert (root / RECEIPT).read_bytes() == b'{"gen": "before"}\n'


def test_sidecar_bound_to_the_live_repository_is_verified(tmp_path):
    root, run_dir = world(tmp_path)
    sidecar(root, pipeline.digest(root / REPOSITORY))

    assert pipeline.rollback_unbound_repository(root, run_dir) == {
        "rollback": "verified", "rollback_membership_load": "PASS"}


def test_no_sidecar_is_not_drift(tmp_path):
    # official_membership_movements.load treats an absent sidecar as empty; that stays a plain "verified".
    root, run_dir = world(tmp_path)

    assert pipeline.rollback_unbound_repository(root, run_dir) == {
        "rollback": "verified", "rollback_membership_load": "PASS"}


class CompletedProcess:
    pid = 8765

    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_failed_run_with_a_drifted_sidecar_publishes_unhealable_drift_in_receipt_and_status(tmp_path, monkeypatch):
    # The whole failure handler, not just the helper: the run fails at the repository step, the sidecar names a
    # repository nobody holds, and the outcome must reach both the run receipt and the status marker.
    (tmp_path / REPOSITORY).write_bytes(b'{"bills": {}, "generation": "before"}\n')
    pipeline.write(tmp_path / RECEIPT, {"schema": "congress-bill-refresh-receipt-v1", "gen": "before"})
    sidecar(tmp_path, "c" * 64)

    def launch(command, **kwargs):
        if Path(command[1]).name == "update_bill_repository.py":
            (tmp_path / REPOSITORY).write_bytes(b'{"bills": {}, "generation": "fetched"}\n')
            return CompletedProcess(5)
        return CompletedProcess()

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)

    assert pipeline.run(tmp_path) == 1

    status = pipeline.read(tmp_path / pipeline.STATUS)
    assert status["status"] == "FAILED"
    assert status["rollback"] == "unhealable_drift"
    assert status["rollback_drift"]["membership_sha256"] == "c" * 64
    assert status["rollback_drift"]["live_repository_sha256"] == pipeline.digest(tmp_path / REPOSITORY)
    receipt = pipeline.read(Path(status["run_dir"]) / "receipt.json")
    assert receipt["rollback"] == "unhealable_drift" and receipt["rollback_drift"] == status["rollback_drift"]
