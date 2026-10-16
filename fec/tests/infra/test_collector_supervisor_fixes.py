"""Targeted tests for the collector_supervisor fixes (campaign supervisor-campaign, T1).

Covers: exact-argument leg matching (a foreign same-leg process is neither relaunched nor killed), the
spend_guard dry-run fail-CLOSED, the bounded Popen + taskkill /T /F path for a CAPPED gate step, and the
atomic O_EXCL lock acquire.  Synthetic only: no real process, no network, nothing outside tmp_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from collect import collector_supervisor as supervisor

QUOTED = ('"C:\\Python312\\python.exe" "C:\\Users\\pgoff\\OneDrive\\Desktop\\Melange Systems\\fec\\members'
          '\\fetch_interviews.py" ')


def _cmd(*leg):
    return QUOTED + " ".join(leg)


def test_leg_args_reads_the_args_after_a_quoted_spaced_script_path():
    assert supervisor._leg_args(_cmd("cnn", "--since", "2019-01-01", "--until", "2024-02-29")) == [
        "cnn", "--since", "2019-01-01", "--until", "2024-02-29"]
    assert supervisor._leg_args("python fetch_interviews.py fox") == ["fox"]      # unquoted form
    assert supervisor._leg_args(QUOTED.rstrip()) == []                            # no leg args at all
    assert supervisor._leg_args("python other_script.py fox") is None
    assert supervisor._leg_args("python fetch_interviews.pyc fox") is None
    assert supervisor._leg_args(None) is None


def _tick_with_procs(tmp_path, monkeypatch, procs, targets=None, progress=None):
    """Drive _tick (no sentinel => every target pending) against scripted processes.
    Returns (launched_tags, killed_pid_lists, log_messages)."""
    targets = targets if targets is not None else {"fox": ["fox"]}
    targets_file = tmp_path / "collector_targets.json"
    targets_file.write_text(json.dumps(targets), encoding="utf-8")
    prog_file = tmp_path / "collector_progress.json"
    if progress is not None:
        prog_file.write_text(json.dumps(progress), encoding="utf-8")
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "SENT", str(tmp_path / "collector_sentinels"))
    monkeypatch.setattr(supervisor, "TARGETS_F", str(targets_file))
    monkeypatch.setattr(supervisor, "PROG_F", str(prog_file))
    monkeypatch.setattr(supervisor, "leg_db_metric", lambda args: 0)
    monkeypatch.setattr(supervisor, "running_procs", lambda: procs)
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: False)
    monkeypatch.setattr(supervisor, "run_gates", lambda: None)
    monkeypatch.setattr(supervisor.time, "sleep", lambda seconds: None)
    launched, killed, messages = [], [], []
    monkeypatch.setattr(supervisor, "launch_detached", lambda args, tag: launched.append(tag))
    monkeypatch.setattr(supervisor, "kill_leg", lambda match, tag: killed.append([p["pid"] for p in match]))
    monkeypatch.setattr(supervisor, "log", messages.append)
    supervisor._tick()
    return launched, killed, messages


def test_foreign_same_leg_process_is_neither_relaunched_nor_killed(tmp_path, monkeypatch):
    """daily_media_refresh runs `fox --strict`; the supervisor's `fox` target must not adopt it."""
    procs = [{"pid": 900, "cpu": 3.0, "cmd": _cmd("fox", "--strict")}]
    launched, killed, messages = _tick_with_procs(tmp_path, monkeypatch, procs)
    assert launched == [] and killed == []
    assert any("fox" in m and "running outside the supervisor" in m and "900" in m for m in messages), messages
    assert not (tmp_path / "data" / "engine" / "collector_refire_state.json").exists(), "no refire recorded"


def test_exact_args_match_baselines_and_does_not_relaunch(tmp_path, monkeypatch):
    procs = [{"pid": 100, "cpu": 2.0, "cmd": _cmd("fox")}]
    launched, killed, messages = _tick_with_procs(tmp_path, monkeypatch, procs)
    assert launched == [] and killed == []
    assert any("fox alive (pid 100) — baseline set" in m for m in messages), messages


def test_no_matching_process_still_relaunches(tmp_path, monkeypatch):
    procs = [{"pid": 7, "cmd": _cmd("nprfeed"), "cpu": 1.0}, {"pid": 8, "cmd": "python other.py", "cpu": 1.0}]
    launched, killed, _messages = _tick_with_procs(tmp_path, monkeypatch, procs)
    assert launched == ["fox"] and killed == []


def test_wedge_kill_hits_only_the_exact_args_process(tmp_path, monkeypatch):
    procs = [{"pid": 111, "cpu": 5.0, "cmd": _cmd("fox")},
             {"pid": 222, "cpu": 1.0, "cmd": _cmd("fox", "--strict")}]
    flat_for_ages = {"fox": {"pid": 111, "db": 0, "cpu": 5.0, "since": time.time() - 10 ** 5}}
    launched, killed, messages = _tick_with_procs(tmp_path, monkeypatch, procs, progress=flat_for_ages)
    assert killed == [[111]], messages          # the foreign pid 222 is never in the kill list
    assert launched == ["fox"]


def _scripted_run(monkeypatch, tmp_path, returncode, stdout, stderr=""):
    class Result:
        pass

    Result.returncode, Result.stdout, Result.stderr = returncode, stdout, stderr
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "GATE_TIMEOUT", None)
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *args, **kwargs: Result())
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)
    return messages


def test_spend_guard_fails_closed_when_the_dry_run_failed_even_with_an_est_line(monkeypatch, tmp_path):
    monkeypatch.delenv("REF_GATE_CAP", raising=False)
    messages = _scripted_run(monkeypatch, tmp_path, 1, "est ~$0.01\n", "Traceback: boom\n")
    assert supervisor.spend_guard("reference --score", "producer.py", ["--score"],
                                  "REF_GATE_CAP", "inf") == (False, 0.0)
    assert any("dry run failed" in m and "failing CLOSED" in m for m in messages), messages


def test_capped_gate_step_kills_the_tree_and_drains_the_pipes_with_a_bound(monkeypatch, tmp_path):
    """timeout is not None => Popen + taskkill /T /F + a bounded second communicate()."""
    communicate_timeouts, taskkills = [], []

    class FakeProc:
        pid = 4242
        returncode = None

        def __init__(self, argv, **kwargs):
            assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] == subprocess.PIPE

        def communicate(self, timeout=None):
            communicate_timeouts.append(timeout)
            if len(communicate_timeouts) == 1:
                raise subprocess.TimeoutExpired("producer.py", timeout)
            return "partial stdout\n", "partial stderr\n"

    def fake_run(argv, **kwargs):
        taskkills.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)
    ok, out = supervisor.gate_step("slow step", "producer.py", [], timeout=5)
    assert ok is False
    assert taskkills == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert communicate_timeouts == [5, 30]
    assert "partial stdout" in out and "partial stderr" in out
    assert any("TIMEOUT after 5s" in m for m in messages), messages


def test_acquire_lock_is_exclusive_and_takes_over_only_a_stale_lock(monkeypatch, tmp_path):
    lock = tmp_path / "collector_supervisor.lock"
    monkeypatch.setattr(supervisor, "LOCK", str(lock))
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)
    assert supervisor._acquire_lock() is True and lock.is_file()
    assert supervisor._acquire_lock() is False
    assert any("another supervisor tick active" in m for m in messages), messages
    old = time.time() - 6000                     # past the 5400 s stale window
    os.utime(lock, (old, old))
    assert supervisor._acquire_lock() is True
    assert any("stale lock" in m and "taking over" in m for m in messages), messages
    assert time.time() - os.path.getmtime(lock) < 60, "takeover wrote a fresh lock"
