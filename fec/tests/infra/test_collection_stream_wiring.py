from __future__ import annotations

import ast
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import wave
import os
import subprocess

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from audit_failure_gate import KNOWN_GATED_FAILURES, audit_failures, tolerate_known_gated
import audit_collection
from audit_collection import archive_accepted_press_receipt
from collect import build_press_engine_feed as press_feed
from collect import collector_supervisor as supervisor
from collect.fold_promoted_press_runs import fold_promoted
from collect.press_receipt import ReceiptTimestampError, parse_receipt_timestamp
from members import b5_audio_manifest_adapter as audio_feed
from members import fetch_interviews
from members.hearing_display_contract import load_optional as load_hearing_display, write_fixture_feed
from pipeline_lock import child_lock_environment, pipeline_run_lock, valid_inherited_lease
import press_stream as press_stream_reader
from surfaces.publish_dashboard import publish as publish_dashboard
from surfaces import make_email_package


def _csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def test_abc_archive_discovery_accepts_decorated_story_queries():
    page = """
      <a href="/Politics/week-transcript-8-16-26/story?_apiEnv=preview&amp;cb=x&amp;id=135674660">Full</a>
      <a href="https://abc.com/Politics/week-transcript-8-23-26/story?id=135865528">Full</a>
      <a href="/Politics/week-transcript-archive/story?id=16614108">Archive</a>
    """
    assert fetch_interviews._abc_transcript_links(page) == {
        "https://abcnews.go.com/Politics/week-transcript-8-16-26/story?id=135674660",
        "https://abcnews.go.com/Politics/week-transcript-8-23-26/story?id=135865528",
    }


ABC_ARCHIVE = "https://abcnews.go.com/Politics/week-transcript-archive/story?id=16614108"
ABC_INDEX_PAGE = """
  <a href="/Politics/week-transcript-a/story?id=111">A</a>
  <a href="/Politics/week-transcript-b/story?id=222">B</a>
  <a href="/Politics/week-transcript-c/story?id=333">C</a>
"""
ABC_STORY = '<html>"datePublished":"2099-01-01" <p>SEN. NOBODY: words words words.</p></html>'


ABC_A = "https://abcnews.go.com/Politics/week-transcript-a/story?id=111"
ABC_B = "https://abcnews.go.com/Politics/week-transcript-b/story?id=222"
ABC_C = "https://abcnews.go.com/Politics/week-transcript-c/story?id=333"


def _abc_harness(monkeypatch, tmp_path, responses, index=ABC_INDEX_PAGE):
    """Point run_abc at a throwaway sqlite file and a scripted fetcher.

    `responses` maps story URL -> page text, or an exception instance to raise for that URL.
    Returns (sqlite path, list of URLs actually requested)."""
    database = tmp_path / "social_media.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fetch_interviews, "DB", str(database))
    monkeypatch.setattr(fetch_interviews, "PAUSE", 0)
    requested = []

    def fake_get(url, tries=3):
        requested.append(url)
        if url == ABC_ARCHIVE:
            return index
        outcome = responses[url]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(fetch_interviews, "get", fake_get)
    return database, requested


def _abc_receipts(database):
    import sqlite3

    connection = sqlite3.connect(str(database))
    rows = dict(connection.execute(
        "SELECT key, status FROM iv_fetch_log WHERE key LIKE 'abc:%'").fetchall())
    connection.close()
    return rows


def _http_error(code):
    import urllib.error

    return urllib.error.HTTPError("https://abcnews.go.com/c", code, "err", {}, None)


def test_abc_permanently_dead_archive_link_is_receipted_not_a_leg_failure(monkeypatch, tmp_path):
    """ABC's own archive lists story ids that 404 forever (verified id=986110097, 2026-08-28).
    They must be recorded and skipped, never block publication on every tick."""
    database, requested = _abc_harness(monkeypatch, tmp_path, {
        ABC_A: ABC_STORY, ABC_B: ABC_STORY, ABC_C: _http_error(404)})
    assert fetch_interviews.run_abc() is True
    assert _abc_receipts(database) == {"abc:111": "ok", "abc:222": "ok", "abc:333": "nopage"}
    # the second walk must not re-attempt the dead link (this is what ended the tick loop)
    requested.clear()
    assert fetch_interviews.run_abc() is True
    assert requested == [ABC_ARCHIVE]


def test_abc_genuine_failure_still_hard_fails_the_gate(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: a real breakage (5xx / transport / timeout) must still fail the leg, so
    --strict exits nonzero and weekly_refresh's hard ABC step aborts GATE-1."""
    import urllib.error

    injected = [_http_error(503), urllib.error.URLError("connection reset"),
                TimeoutError("timed out")]
    for index, failure in enumerate(injected):
        database, _requested = _abc_harness(monkeypatch, tmp_path / ("case%d" % index), {
            ABC_A: ABC_STORY, ABC_B: ABC_STORY, ABC_C: failure})
        assert fetch_interviews.run_abc() is False, failure
        # a genuine failure is never converted into a terminal receipt
        assert "abc:333" not in _abc_receipts(database), failure


def test_abc_mass_404_is_treated_as_breakage_not_terminal_loss(monkeypatch, tmp_path):
    """If the archive still renders but its stories are all gone, that is site breakage: no
    terminal receipts, keys stay retryable, and the leg keeps failing until code is repaired."""
    database, _requested = _abc_harness(monkeypatch, tmp_path, {
        ABC_A: _http_error(404), ABC_B: _http_error(410), ABC_C: _http_error(404)})
    assert fetch_interviews.run_abc() is False
    assert _abc_receipts(database) == {}


def test_abc_empty_discovery_still_fails_the_leg(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: a rendered archive page that yields zero stories is a discovery
    breakage — it must stay a hard failure, never a quiet zero-work success."""
    _abc_harness(monkeypatch, tmp_path, {}, index="<html>no transcript links</html>")
    assert fetch_interviews.run_abc() is False


def test_abc_unreachable_archive_still_fails_the_leg(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: if the archive page itself cannot be fetched, the leg fails."""
    database = tmp_path / "social_media.db"
    monkeypatch.setattr(fetch_interviews, "DB", str(database))
    monkeypatch.setattr(fetch_interviews, "PAUSE", 0)

    def dead(url, tries=3):
        raise _http_error(500)

    monkeypatch.setattr(fetch_interviews, "get", dead)
    assert fetch_interviews.run_abc() is False


def test_strict_interview_legs_exit_nonzero_on_leg_failure():
    """The leg's Boolean contract only gates publication if the CLI converts it to an exit code
    and weekly_refresh runs that leg with hard=True (asserted separately below)."""
    source = (FEC / "members" / "fetch_interviews.py").read_text(encoding="utf-8-sig")
    for leg in ("mtp", "cbs", "abc", "pbs", "npr", "msnow"):
        assert "if _strict and not run_%s(" % leg in source, leg
    assert source.count("sys.exit(2)") >= 7


def _fetch_interviews_tree():
    return ast.parse((FEC / "members" / "fetch_interviews.py").read_text(encoding="utf-8-sig"))


def _cli_main_body(tree):
    """The statements inside fetch_interviews.py's `if __name__ == '__main__':` block."""
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            return node.body
    raise AssertionError("fetch_interviews.py has no __main__ dispatch block")


def _declared_leg_names(tree):
    """The leg tuple the script itself uses to derive the sentinel tag (`_leg = next(...)`)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_leg" for target in node.targets):
            continue
        for inner in ast.walk(node.value):
            if isinstance(inner, ast.Tuple) and inner.elts and all(
                    isinstance(element, ast.Constant) and isinstance(element.value, str)
                    for element in inner.elts):
                return tuple(element.value for element in inner.elts)
    raise AssertionError("fetch_interviews.py has no `_leg = next((l for l in (...)))` leg tuple")


def _dispatch_branches(body):
    """Map leg name -> the statements executed for it, walking the `'x' in sys.argv` elif chain."""
    branches = {}

    def walk(node):
        if not isinstance(node, ast.If):
            return
        test = node.test
        if (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.In)
                and isinstance(test.left, ast.Constant) and isinstance(test.left.value, str)
                and ast.unparse(test.comparators[0]) == "sys.argv"):
            branches[test.left.value] = node.body
        for alternative in node.orelse:
            walk(alternative)

    for statement in body:
        walk(statement)
    return branches


def _sentinel_writing_legs():
    tree = _fetch_interviews_tree()
    branches = _dispatch_branches(_cli_main_body(tree))
    return {
        leg: any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                 and call.func.id == "_sentinel"
                 for statement in body for call in ast.walk(statement))
        for leg, body in branches.items()
    }, _declared_leg_names(tree)


def test_every_dispatch_leg_commits_a_completion_sentinel():
    """REGRESSION GUARD (2026-08-28): the CLI dispatch briefly wrote `collector_sentinels/<tag>.done`
    on the cnn branch only. That file is collector_supervisor's ONLY completion signal
    (collector_supervisor.py:511 classifies each target by its sentinel; :567 returns before
    run_gates while any target is pending), so the legs that stopped writing it were relaunched on
    every 10-minute tick forever and no gate could ever fire. Every leg the script itself can
    dispatch must reach a `_sentinel()` call. Still binding after the 2026-08-29 timed-sentinel
    ruling — and stricter: the sentinel now EXPIRES, so a leg that cannot write one blocks the
    gates on every cycle, not just the first."""
    coverage, legs = _sentinel_writing_legs()
    assert not [leg for leg in legs if leg not in coverage], \
        "leg names with no dispatch branch: %s" % [leg for leg in legs if leg not in coverage]
    assert not [leg for leg in legs if not coverage[leg]], \
        "dispatch branches that never write a completion sentinel: %s" % [
            leg for leg in legs if not coverage[leg]]


def test_supervisor_targets_resolve_to_sentinel_writing_legs():
    """The supervisor supervises TAGS from collector_targets.json, whose first argument is the leg
    name it launches. Every configured target must resolve to a leg that can finish."""
    targets_path = FEC.parent / "data" / "stance" / "collector_targets.json"
    if not targets_path.is_file():
        pytest.skip("no collector_targets.json in this checkout")
    targets = json.loads(targets_path.read_text(encoding="utf-8-sig"))
    coverage, _legs = _sentinel_writing_legs()
    for tag, args in targets.items():
        assert args and coverage.get(args[0]) is True, \
            "supervisor target %r launches %r, which writes no completion sentinel" % (tag, args)
    # the cnn-deep TAG only matches its sentinel filename because of the backfill rename rule
    source = (FEC / "members" / "fetch_interviews.py").read_text(encoding="utf-8-sig")
    if "cnn-deep" in targets:
        assert "_leg = 'cnn-deep'" in source


def _tick_harness(tmp_path, monkeypatch, sentinels, targets=None):
    """Drive collector_supervisor._tick against a THROWAWAY sentinel directory and targets file.

    `sentinels` maps tag -> mtime offset in seconds (negative = older than now, positive = future),
    or None for "no sentinel file at all". Nothing here touches the live collector_sentinels/
    directory, the live targets file, social_media.db, the supervisor log, or any real process:
    running_procs, launch_detached, leg_db_metric and run_gates are all scripted.
    Returns (relaunched_tags, log_messages, gate_runs)."""
    sentinel_dir = tmp_path / "collector_sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    targets = targets if targets is not None else {"fox": ["fox"],
                                                   "cnn-deep": ["cnn", "--until", "2024-02-29"]}
    targets_file = tmp_path / "collector_targets.json"
    targets_file.write_text(json.dumps(targets), encoding="utf-8")
    now = time.time()
    for tag, offset in sentinels.items():
        if offset is None:
            continue
        stamp = sentinel_dir / (tag + ".done")
        stamp.write_text("2099-01-01T00:00:00", encoding="utf-8")
        os.utime(stamp, (now + offset, now + offset))
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "SENT", str(sentinel_dir))
    monkeypatch.setattr(supervisor, "TARGETS_F", str(targets_file))
    monkeypatch.setattr(supervisor, "PROG_F", str(tmp_path / "collector_progress.json"))
    monkeypatch.setattr(supervisor, "leg_db_metric", lambda args: 0)
    monkeypatch.setattr(supervisor, "running_procs", lambda: [])   # nothing alive -> relaunch path
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: False)
    relaunched, gate_runs, messages = [], [], []
    monkeypatch.setattr(supervisor, "launch_detached", lambda args, tag: relaunched.append(tag))
    monkeypatch.setattr(supervisor, "run_gates", lambda: gate_runs.append(True))
    monkeypatch.setattr(supervisor, "log", messages.append)
    supervisor._tick()
    return relaunched, messages, gate_runs


def test_sentinel_state_classifies_every_case(tmp_path, monkeypatch):
    """Paul ruling 2026-08-29: 'the whole point of the sentinels is that they fire on a timed
    basis.' The predicate is age-based, not existence-based."""
    sentinel_dir = tmp_path / "collector_sentinels"; sentinel_dir.mkdir()
    monkeypatch.setattr(supervisor, "SENT", str(sentinel_dir))
    assert supervisor.sentinel_state("fox") == ("missing", None)
    stamp = sentinel_dir / "fox.done"
    stamp.write_text("x", encoding="utf-8")
    state, age = supervisor.sentinel_state("fox")
    assert state == "fresh" and age < 5
    os.utime(stamp, (time.time() - 8 * 86400,) * 2)
    state, age = supervisor.sentinel_state("fox")
    assert state == "stale" and 7.9 * 86400 < age < 8.1 * 86400
    # an explicit window argument overrides the module default in both directions
    assert supervisor.sentinel_state("fox", window=9 * 86400)[0] == "fresh"
    assert supervisor.sentinel_state("fox", window=60)[0] == "stale"


def test_fresh_sentinel_counts_done_and_fires_nothing(tmp_path, monkeypatch):
    relaunched, messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": -10, "cnn-deep": -3600})
    assert relaunched == []
    assert gate_runs == [True], "all-fresh must still fall through to the gates"
    assert any("2 done (cnn-deep,fox), 0 pending" in message for message in messages), messages


def test_stale_sentinel_refires_its_target_with_a_distinct_log_line(tmp_path, monkeypatch):
    """THE REPAIR (2026-08-29). Before this, `done` was
    `{t for t in targets if os.path.exists(...)}` — one sentinel marked its leg done FOREVER, so
    the corpus froze at the first successful walk and no later interview was ever collected."""
    relaunched, messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": -60, "cnn-deep": -8 * 86400})
    assert relaunched == ["cnn-deep"]
    assert gate_runs == [], "a pending target must still short-circuit the gates"
    stale = [message for message in messages if "sentinel stale for cnn-deep" in message]
    assert len(stale) == 1, messages
    assert "8.0 days old" in stale[0] and "window 7.0 days" in stale[0] and "refiring" in stale[0]
    # the stale case must be distinguishable from the never-ran case in an unattended log
    assert not any("sentinel missing for cnn-deep" in message for message in messages), messages
    assert any("1 done (fox), 1 pending" in message for message in messages), messages


def test_missing_sentinel_still_fires_its_target(tmp_path, monkeypatch):
    """Pre-existing behaviour, preserved: no sentinel = never completed = fire."""
    relaunched, messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": None, "cnn-deep": -60})
    assert relaunched == ["fox"] and gate_runs == []
    assert any("sentinel missing for fox" in message for message in messages), messages
    assert not any("sentinel stale for fox" in message for message in messages), messages


def test_sentinel_freshness_window_dial_is_respected(tmp_path, monkeypatch):
    """A 60-second window lets the test AGE a file with os.utime instead of sleeping."""
    monkeypatch.setattr(supervisor, "SENTINEL_FRESH_S", 60.0)
    relaunched, messages, _gates = _tick_harness(
        tmp_path, monkeypatch, {"fox": -30, "cnn-deep": -61})
    assert relaunched == ["cnn-deep"], messages
    assert any("window 0.0 days" in message and "sentinel stale for cnn-deep" in message
               for message in messages), messages


def test_sentinel_freshness_window_is_an_environment_dial():
    """The window must be reachable without a code edit, like every other supervisor dial
    (GATE_STEP_TIMEOUT, GATE1_RETRY_COOLDOWN_S, ...). Checked in a child process so this test
    cannot leave a reloaded module behind for its neighbours."""
    program = ("import sys;sys.path.insert(0, sys.argv[1]);"
               "from collect import collector_supervisor as s;print(s.SENTINEL_FRESH_S)")
    environment = os.environ.copy()
    environment["SENTINEL_FRESH_S"] = "60"
    overridden = subprocess.run([sys.executable, "-c", program, str(FEC)],
                                capture_output=True, text=True, env=environment)
    assert overridden.returncode == 0, overridden.stderr
    assert overridden.stdout.strip() == "60.0", overridden.stdout
    environment.pop("SENTINEL_FRESH_S")
    default = subprocess.run([sys.executable, "-c", program, str(FEC)],
                             capture_output=True, text=True, env=environment)
    assert default.returncode == 0, default.stderr
    assert default.stdout.strip() == "604800.0", default.stdout   # 7 days, the weekly cadence


def test_future_dated_sentinel_reads_fresh_and_never_refires(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: clock skew / DST / a stamp copied off another machine must not produce a
    re-fire storm and must not raise — a future mtime clamps to age 0."""
    relaunched, messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": 10 ** 6, "cnn-deep": -60})
    assert relaunched == [] and gate_runs == [True]
    assert not any("stale" in message for message in messages), messages
    monkeypatch.setattr(supervisor, "SENT", str(tmp_path / "collector_sentinels"))
    assert supervisor.sentinel_state("fox") == ("fresh", 0.0)


def test_unreadable_sentinel_mtime_refires_instead_of_crashing(tmp_path, monkeypatch):
    """A racing deletion or a transient OneDrive sharing violation between exists() and getmtime()
    must not kill the tick, and must not be read as 'done': the legs resume from iv_fetch_log, so a
    spurious re-fire is a cheap incremental walk while a wrong 'done' starves collection."""
    real_getmtime = os.path.getmtime

    def unreadable(path):
        if str(path).endswith(".done"):
            raise OSError(13, "permission denied")
        return real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", unreadable)
    relaunched, messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": -60, "cnn-deep": -60})
    assert sorted(relaunched) == ["cnn-deep", "fox"]
    assert gate_runs == []
    assert len([message for message in messages if "sentinel unreadable" in message]) == 2, messages
    assert any("0 done (-), 2 pending" in message for message in messages), messages


def _refire_state(tmp_path):
    return tmp_path / "data" / "engine" / "collector_refire_state.json"


def _refire_entries(tmp_path):
    return json.loads(_refire_state(tmp_path).read_text(encoding="utf-8"))["targets"]


# `fox` expired a day past the 7-day window, `cnn-deep` is still fresh: the fox leg is the one that
# keeps being fired. Offsets are re-stamped on every _tick_harness call, so repeating the same dict
# reproduces "the sentinel STILL was not committed" tick after tick.
FOX_NEVER_COMMITS = {"fox": -8 * 86400, "cnn-deep": -60}


def test_refire_backoff_seconds_doubles_and_is_capped():
    assert supervisor._refire_backoff_seconds(0) == 0.0, "the first fire must be immediate"
    assert supervisor._refire_backoff_seconds(1) == supervisor.REFIRE_BACKOFF_S
    assert supervisor._refire_backoff_seconds(3) == supervisor.REFIRE_BACKOFF_S * 4
    assert supervisor._refire_backoff_seconds(500) == supervisor.REFIRE_BACKOFF_MAX_S


def test_refire_backoff_dials_are_environment_dials():
    """Reachable without a code edit, like every other supervisor dial. Checked in a child process
    so a reloaded module is never left behind for the neighbouring tests."""
    program = ("import sys;sys.path.insert(0, sys.argv[1]);"
               "from collect import collector_supervisor as s;"
               "print(s.REFIRE_BACKOFF_S, s.REFIRE_BACKOFF_MAX_S)")
    environment = os.environ.copy()
    environment["COLLECTOR_REFIRE_BACKOFF_S"] = "5"
    environment["COLLECTOR_REFIRE_BACKOFF_MAX_S"] = "40"
    overridden = subprocess.run([sys.executable, "-c", program, str(FEC)],
                                capture_output=True, text=True, env=environment)
    assert overridden.returncode == 0, overridden.stderr
    assert overridden.stdout.strip() == "5.0 40.0", overridden.stdout
    for key in ("COLLECTOR_REFIRE_BACKOFF_S", "COLLECTOR_REFIRE_BACKOFF_MAX_S"):
        environment.pop(key)
    default = subprocess.run([sys.executable, "-c", program, str(FEC)],
                             capture_output=True, text=True, env=environment)
    assert default.returncode == 0, default.stderr
    assert default.stdout.strip() == "1800.0 86400.0", default.stdout   # 30 min, 24 h


def test_refire_is_backed_off_after_a_fire_that_committed_no_sentinel(tmp_path, monkeypatch):
    """THE WEEKLY-EXPIRY HAZARD (2026-08-29). With a 7-day window every target expires together, so
    a leg that finishes WITHOUT committing its sentinel (the 2026-08-28 dispatch-bug class) is
    pending on every tick and used to be relaunched every ~10 minutes forever — invisibly, because
    the only log line was the ordinary 'RELAUNCHED'. The refire must now be paced and named."""
    relaunched, _messages, gate_runs = _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert relaunched == ["fox"] and gate_runs == []
    entries = _refire_entries(tmp_path)
    assert entries["fox"]["consecutive_fires"] == 1
    assert entries["fox"]["next_refire_after_s"] == supervisor.REFIRE_BACKOFF_S
    assert "cnn-deep" not in entries, "a target that never fired must hold no refire state"
    # the very next tick, sentinel still not committed -> SKIPPED, loudly
    relaunched, messages, gate_runs = _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert relaunched == [], "a leg that never commits a sentinel was refired on the next tick"
    assert gate_runs == [], "a pending target must still short-circuit the gates"
    backoff = [message for message in messages if "REFIRE BACKOFF for fox" in message]
    assert len(backoff) == 1, messages
    assert "1 consecutive fire(s) without a sentinel commit" in backoff[0]
    assert "backoff minutes remain" in backoff[0] and "30 backoff minutes" in backoff[0]
    assert _refire_entries(tmp_path)["fox"]["consecutive_fires"] == 1, "a skip is not a fire"
    # the census stays classification-only: a backed-off target is still PENDING on that line
    assert any("1 done (cnn-deep), 1 pending" in message for message in messages), messages


def test_refire_backoff_expires_then_doubles(tmp_path, monkeypatch):
    _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    marker = _refire_state(tmp_path)
    state = json.loads(marker.read_text(encoding="utf-8"))
    state["targets"]["fox"]["last_fire_at"] -= supervisor.REFIRE_BACKOFF_S + 1
    marker.write_text(json.dumps(state), encoding="utf-8")
    relaunched, messages, _gates = _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert relaunched == ["fox"], "an elapsed backoff must not block the refire"
    entries = _refire_entries(tmp_path)
    assert entries["fox"]["consecutive_fires"] == 2
    assert entries["fox"]["next_refire_after_s"] == supervisor.REFIRE_BACKOFF_S * 2
    distress = [message for message in messages if message.startswith("  DISTRESS: fox")]
    assert len(distress) == 1 and "fired 2 consecutive times" in distress[0], messages


def test_a_committed_sentinel_resets_the_refire_state(tmp_path, monkeypatch):
    """The counter tracks ONE pending episode. A fresh sentinel ends it, so the next weekly expiry
    starts from an immediate fire instead of inheriting last week's backoff."""
    _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert _refire_entries(tmp_path)["fox"]["consecutive_fires"] == 1
    relaunched, _messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": -30, "cnn-deep": -60})       # fox finally commits
    assert relaunched == [] and gate_runs == [True]
    assert _refire_entries(tmp_path) == {}
    relaunched, messages, _gates = _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert relaunched == ["fox"], messages
    assert not any("REFIRE BACKOFF" in message for message in messages), messages
    assert not any(message.startswith("  DISTRESS") for message in messages), messages


def test_refire_state_fails_open_on_a_corrupt_or_future_dated_marker(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: this state paces relaunches; it must never be what permanently stops
    collection. Firing is the safe direction — the legs resume from iv_fetch_log, so a spurious
    fire is one cheap incremental walk, while a wedged counter starves the corpus silently."""
    marker = _refire_state(tmp_path)
    marker.parent.mkdir(parents=True)
    hot = {"consecutive_fires": 9, "last_fire_at": time.time()}
    for corrupt in ("{not json",
                    "",
                    json.dumps({"schema": "someone-elses-file", "targets": {"fox": hot}}),
                    json.dumps({"schema": supervisor.REFIRE_SCHEMA, "targets": "not-a-dict"}),
                    json.dumps({"schema": supervisor.REFIRE_SCHEMA, "targets": {"fox": "not-a-dict"}}),
                    json.dumps({"schema": supervisor.REFIRE_SCHEMA,
                                "targets": {"fox": {"consecutive_fires": "many",
                                                    "last_fire_at": None}}}),
                    json.dumps({"schema": supervisor.REFIRE_SCHEMA,
                                "targets": {"fox": {"consecutive_fires": 9,
                                                    "last_fire_at": time.time() + 10 ** 6}}})):
        marker.write_text(corrupt, encoding="utf-8")
        relaunched, messages, _gates = _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
        assert relaunched == ["fox"], corrupt
        assert not any("REFIRE BACKOFF" in message for message in messages), corrupt


def test_refire_backoff_is_per_target_not_global(tmp_path, monkeypatch):
    """One broken leg must never delay a DIFFERENT leg's first fire — that would convert a single
    non-committing collector into a system-wide collection stall."""
    relaunched, _messages, _gates = _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert relaunched == ["fox"]
    # fox is inside its backoff; cnn-deep expires for the FIRST time on this tick
    relaunched, messages, gate_runs = _tick_harness(
        tmp_path, monkeypatch, {"fox": -8 * 86400, "cnn-deep": -9 * 86400})
    assert relaunched == ["cnn-deep"], messages
    assert any("REFIRE BACKOFF for fox" in message for message in messages), messages
    assert not any("REFIRE BACKOFF for cnn-deep" in message for message in messages), messages
    assert gate_runs == []
    entries = _refire_entries(tmp_path)
    assert entries["fox"]["consecutive_fires"] == 1
    assert entries["cnn-deep"]["consecutive_fires"] == 1


def test_targets_in_refire_backoff_still_block_the_gates(tmp_path, monkeypatch):
    """FAIL-CLOSED STANDS. Backoff rate-limits the RELAUNCH; it does not make a target done. A tick
    on which nothing is fired at all because every pending target is backing off must STILL return
    before run_gates() — the short-circuit is about collection freshness, not about this tick's
    launches."""
    both_stale = {"fox": -8 * 86400, "cnn-deep": -9 * 86400}
    relaunched, _messages, gate_runs = _tick_harness(tmp_path, monkeypatch, both_stale)
    assert sorted(relaunched) == ["cnn-deep", "fox"] and gate_runs == []
    relaunched, messages, gate_runs = _tick_harness(tmp_path, monkeypatch, both_stale)
    assert relaunched == []
    assert gate_runs == [], "backoff must never become a gates-proceed path"
    assert len([message for message in messages if "REFIRE BACKOFF" in message]) == 2, messages
    assert any("0 done (-), 2 pending" in message for message in messages), messages


def test_refire_state_is_dropped_for_a_deconfigured_target(tmp_path, monkeypatch):
    """A tag removed from collector_targets.json must not leave a fossil counter that would back
    off its FIRST fire if it is ever re-added."""
    _tick_harness(tmp_path, monkeypatch, FOX_NEVER_COMMITS)
    assert "fox" in _refire_entries(tmp_path)
    _tick_harness(tmp_path, monkeypatch, {"cnn-deep": -60}, targets={"cnn-deep": ["cnn"]})
    assert _refire_entries(tmp_path) == {}


_IMPORT_BLOCKER = """
import sys
class Missing:
    def find_spec(self, name, path=None, target=None):
        if name == 'audit_failure_gate':
            raise ModuleNotFoundError("No module named 'audit_failure_gate'", name=name)
        return None
sys.meta_path.insert(0, Missing())
"""


def _import_supervisor(tmp_path, blocker):
    program = blocker + ("import sys\n"
                         "sys.path.insert(0, sys.argv[1])\n"
                         "from collect import collector_supervisor\n"
                         "print('IMPORTED')\n")
    environment = os.environ.copy()
    environment["MONEYBALL_ROOT"] = str(tmp_path)
    result = subprocess.run([sys.executable, "-c", program, str(FEC)],
                            capture_output=True, text=True, env=environment)
    log_file = tmp_path / "data" / "stance" / "collector_supervisor.log"
    return result, log_file


def test_missing_gate_module_logs_a_fatal_line_and_still_dies(tmp_path):
    """collector_supervisor imports audit_failure_gate at MODULE scope, and fec/audit_failure_gate.py
    is currently untracked. If it is ever lost, every 10-minute tick dies at import — and Task
    Scheduler swallows the traceback, so the supervisor log (the only unattended diagnostic surface)
    would show nothing at all: an empty log and silently stalled collection. The import must stay
    FATAL — the ratified KNOWN_GATED_FAILURES list cannot be defaulted — but it has to say so
    first."""
    result, log_file = _import_supervisor(tmp_path, _IMPORT_BLOCKER)
    assert result.returncode != 0, result.stdout
    assert "IMPORTED" not in result.stdout, "the import must NOT be survivable"
    assert "audit_failure_gate" in result.stderr, result.stderr
    assert "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr, result.stderr
    assert log_file.is_file(), "no supervisor log line was written: %s" % result.stderr
    body = log_file.read_text(encoding="utf-8").strip()
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] FATAL: ", body), body
    assert "audit_failure_gate import failed" in body
    assert "supervisor cannot run" in body


def test_gate_module_import_guard_is_silent_when_the_module_is_present(tmp_path):
    """NEGATIVE CONTROL: the guard must fire only on a real ImportError. If it logged (or the module
    failed to import) unconditionally, the test above would pass for the wrong reason."""
    result, log_file = _import_supervisor(tmp_path, "")
    assert result.returncode == 0, result.stderr
    assert "IMPORTED" in result.stdout
    assert not log_file.exists(), log_file.read_text(encoding="utf-8")


def test_supervisor_has_no_fallback_for_the_missing_gate_module():
    """A missing gate module must stay fatal, not degrade into a locally-defined tolerance."""
    source = (FEC / "collect" / "collector_supervisor.py").read_text(encoding="utf-8-sig")
    guard = source.split("except ImportError")[1].split("\nTARGETS_F")[0]
    assert guard.rstrip().endswith("\n    raise"), guard   # re-raise is the handler's LAST statement
    assert "def tolerate_known_gated" not in source
    assert "tolerate_known_gated =" not in source


def _neutral_press_run(root, rows, completed_at="2099-01-01T00:00:00+00:00",
                       record_date="2099-01-01"):
    root.mkdir()
    host_fields = ["bioguide", "member", "chamber", "state", "party", "date_final",
                   "date", "title", "url", "body", "source"]
    scored_fields = ["member", "chamber", "state", "party", "bioguide", "date", "title",
                     "url", "score", "score_graded", "score_label", "polarity", "relevance",
                     "categories", "word_count", "recency_weight"]
    hosts = []
    scored = []
    for row in rows:
        hosts.append({
            "bioguide": row["bioguide"], "member": row["member"], "chamber": "Test",
            "state": "NA", "party": "Neutral", "date_final": record_date,
            "date": record_date, "title": row.get("title", "Neutral record"),
            "url": row["url"], "body": " ".join(["neutral"] * 30), "source": "",
        })
        scored.append({
            "member": row["member"], "chamber": "Test", "state": "NA", "party": "Neutral",
            "bioguide": row["bioguide"], "date": record_date,
            "title": row.get("title", "Neutral record"), "url": row["url"],
            "score": "0", "score_graded": "0", "score_label": "Neutral",
            "polarity": "0", "relevance": "neutral fixture",
            "categories": "Test", "word_count": "30", "recency_weight": "1",
        })
    _csv(root / "press_delta_host_articles.csv", host_fields, hosts)
    _csv(root / "press_delta_article_mentions.csv", host_fields, [])
    _csv(root / "press_delta_scored.csv", scored_fields, scored)
    _csv(root / "press_delta_fulltext.csv", host_fields, hosts)
    _csv(root / "press_delta_articles.csv", host_fields, hosts)
    _csv(root / "press_delta_failed_bodies.csv", host_fields, [])
    _csv(root / "press_delta_dated.csv", host_fields, hosts)
    _csv(root / "press_delta_undated.csv", host_fields, [])
    _csv(root / "press_delta_out_of_range.csv", host_fields, [])
    (root / "press_delta_fulltext.rds").write_bytes(b"neutral-rds-fixture")
    index_fields = ["title", "url", "date", "title_src", "bioguide", "member",
                    "chamber", "state", "party"]
    _csv(root / "press_url_delta.csv", index_fields, [{
        "title": row.get("title", "Neutral record"), "url": row["url"],
        "date": record_date, "title_src": "fixture", "bioguide": row["bioguide"],
        "member": row["member"], "chamber": "Test", "state": "NA", "party": "Neutral",
    } for row in rows])
    retry = {
        "mode": "live_network_retry", "completed_at": completed_at,
        "fulltext_csv_sha256": hashlib.sha256((root / "press_delta_fulltext.csv").read_bytes()).hexdigest(),
        "fulltext_rds_sha256": hashlib.sha256((root / "press_delta_fulltext.rds").read_bytes()).hexdigest(),
        "fulltext_receipts": len(rows), "usable_article_member_rows": len(rows),
        "unique_host_articles": len(rows), "mention_relationships": 0, "failed_bodies": 0,
        "exhausted_network_body_failures": 0, "rejected_short_mention_slices": 0,
    }
    _csv(root / "press_delta_pending.csv", ["bioguide", "url", "date", "collection_status"], [])
    retry.update(status="PASS", pending_body_urls=0, pending_body_urls_sha256=hashlib.sha256(
        (root / "press_delta_pending.csv").read_bytes()).hexdigest())
    (root / "press_network_retry_receipt.json").write_text(
        json.dumps(retry), encoding="utf-8")
    return root


def _accepted_press_receipt(root, path):
    source_names = ("press_delta_scored.csv", "press_delta_host_articles.csv",
                    "press_delta_article_mentions.csv")
    document = {
        "schema": "collection-intake-receipt-v1", "status": "ACCEPTED",
        "generated_at": "2099-01-01T00:00:01+00:00",
        "inputs": {
            "press_delta_root": str(root.resolve()),
            "press_retry_receipt_sha256": hashlib.sha256(
                (root / "press_network_retry_receipt.json").read_bytes()).hexdigest(),
            "press_source_sha256": {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in source_names
            },
        },
        "press_delta": {"live_network_retry_receipt_valid": True},
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def test_shared_receipt_timestamp_contract_accepts_iso_and_legacy_r_shape():
    assert parse_receipt_timestamp("2099-01-01T00:00:00+00:00").isoformat().endswith("+00:00")
    assert parse_receipt_timestamp("2099-01-01 00:00:00 UTC").isoformat().endswith("+00:00")
    with pytest.raises(ReceiptTimestampError) as rejected:
        parse_receipt_timestamp("not-a-date")
    assert rejected.value.reason == "TIMESTAMP_UNPARSABLE"


def test_valid_run_names_timestamp_failure_and_accepts_legacy_shape(tmp_path):
    root = _neutral_press_run(
        tmp_path / "press_refresh_20990101",
        [{"member": "Neutral A", "bioguide": "N000001", "url": "https://example.test/a"}],
        completed_at="2099-01-01 00:00:00 UTC")
    assert press_feed.valid_run(root) is not None
    receipt = json.loads((root / "press_network_retry_receipt.json").read_text(encoding="utf-8"))
    receipt["completed_at"] = "gibberish"
    (root / "press_network_retry_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    reasons = []
    assert press_feed.valid_run(root, on_reject=lambda exc: reasons.append(exc.reason)) is None
    assert reasons == ["COMPLETED_AT_UNPARSABLE"]


def test_accepted_receipt_archive_is_immutable_and_binding_checked(tmp_path):
    root = tmp_path / "press_refresh_20990101"; root.mkdir()
    receipt = {"schema": "collection-intake-receipt-v1", "status": "ACCEPTED",
               "inputs": {"press_delta_root": str(root),
                          "press_retry_receipt_sha256": "a" * 64,
                          "press_source_sha256": {"x": "b" * 64}}}
    archive = tmp_path / "archive"
    destination = archive_accepted_press_receipt(receipt, archive)
    assert destination.name == "press_refresh_20990101.json"
    archive_accepted_press_receipt(dict(receipt, generated_at="later"), archive)
    changed = json.loads(json.dumps(receipt))
    changed["inputs"]["press_retry_receipt_sha256"] = "c" * 64
    with pytest.raises(RuntimeError, match="binding changed"):
        archive_accepted_press_receipt(changed, archive)


def test_press_only_audit_does_not_touch_unrelated_collection_domains(tmp_path, monkeypatch):
    root = tmp_path / "press_refresh_20990101"; root.mkdir()
    for name in ("press_delta_scored.csv", "press_delta_host_articles.csv",
                 "press_delta_article_mentions.csv", "press_network_retry_receipt.json"):
        (root / name).write_text("neutral\n", encoding="utf-8")
    monkeypatch.setattr(
        audit_collection, "audit_press_delta",
        lambda _root, _floor, **_kwargs: {"live_network_retry_receipt_valid": True, "errors": []})
    for forbidden in ("audit_house", "audit_audio_supply", "audit_db", "audit_press"):
        monkeypatch.setattr(
            audit_collection, forbidden,
            lambda *_args, _name=forbidden, **_kwargs: pytest.fail(
                "%s must not run in press scope" % _name))
    output = tmp_path / "press-intake.json"
    code = audit_collection.main([
        "--scope", "press", "--press-delta-root", str(root),
        "--output", str(output),
        "--accepted-press-receipts-dir", str(tmp_path / "accepted"),
    ])
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0 and receipt["status"] == "ACCEPTED"
    assert receipt["scope"] == ["press"] and set(receipt) >= {"inputs", "press_delta"}
    assert not ({"house", "audio_supply", "interviews", "press"} & set(receipt))


def test_press_only_audit_rejects_partial_run_dir_with_named_reason(tmp_path):
    root = tmp_path / "press_refresh_20260829"; root.mkdir()
    (root / "press_network_retry_receipt.json").write_text(json.dumps({
        "mode": "live_network_retry", "completed_at": "2026-08-29T12:00:00+00:00",
        "fulltext_csv_sha256": "a" * 64, "fulltext_rds_sha256": "b" * 64,
        "fulltext_receipts": 0, "usable_article_member_rows": 0,
        "unique_host_articles": 0, "mention_relationships": 0, "failed_bodies": 0,
        "exhausted_network_body_failures": 0, "rejected_short_mention_slices": 0,
    }), encoding="utf-8")
    output = tmp_path / "press-intake.json"
    archive = tmp_path / "accepted"
    code = audit_collection.main([
        "--scope", "press", "--press-delta-root", str(root),
        "--output", str(output), "--accepted-press-receipts-dir", str(archive),
    ])
    assert code == 2
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "REJECTED"
    for missing in ("press_delta_scored.csv", "press_delta_host_articles.csv",
                    "press_delta_article_mentions.csv"):
        assert any(missing in reason for reason in receipt["errors"]), receipt["errors"]
    assert not (archive / "press_refresh_20260829.json").exists()

    # Positive control: a complete fixture run still yields an accepted receipt, so
    # the new existence check does not reject a legitimate run.
    complete = _neutral_press_run(
        tmp_path / "press_refresh_20260830",
        [{"member": "Neutral A", "bioguide": "N000001", "url": "https://example.test/complete"}],
        completed_at="2026-08-30T12:00:00+00:00", record_date="2026-08-30")
    good_output = tmp_path / "press-intake-good.json"
    good_code = audit_collection.main([
        "--scope", "press", "--press-delta-root", str(complete),
        "--press-date-floor", "2026-06-25", "--output", str(good_output),
        "--accepted-press-receipts-dir", str(archive),
    ])
    assert good_code == 0
    good_receipt = json.loads(good_output.read_text(encoding="utf-8"))
    assert good_receipt["status"] == "ACCEPTED"

    # Second case: the retry receipt itself is the missing file.
    no_retry = tmp_path / "press_refresh_20260827"; no_retry.mkdir()
    for name in ("press_delta_scored.csv", "press_delta_host_articles.csv",
                 "press_delta_article_mentions.csv"):
        (no_retry / name).write_text("neutral\n", encoding="utf-8")
    no_retry_output = tmp_path / "press-intake-no-retry.json"
    no_retry_code = audit_collection.main([
        "--scope", "press", "--press-delta-root", str(no_retry),
        "--output", str(no_retry_output), "--accepted-press-receipts-dir", str(archive),
    ])
    assert no_retry_code == 2
    no_retry_receipt = json.loads(no_retry_output.read_text(encoding="utf-8"))
    assert no_retry_receipt["status"] == "REJECTED"
    assert any("press_network_retry_receipt.json" in reason
               for reason in no_retry_receipt["errors"]), no_retry_receipt["errors"]
    assert not (archive / "press_refresh_20260827.json").exists()


def test_cumulative_press_promotion_preserves_earlier_rows_and_adds_new_rows(tmp_path):
    run1 = _neutral_press_run(tmp_path / "press_refresh_20990101", [
        {"member": "Neutral A", "bioguide": "N000001", "url": "https://example.test/a",
         "title": "First accepted title"}])
    run2 = _neutral_press_run(tmp_path / "press_refresh_20990102", [
        {"member": "Neutral A", "bioguide": "N000001", "url": "https://example.test/a",
         "title": "Repeated later title"},
        {"member": "Neutral B", "bioguide": "N000002", "url": "https://example.test/b"}],
        completed_at="2099-01-02T00:00:00+00:00")
    archive = tmp_path / "accepted"; archive.mkdir()
    _accepted_press_receipt(run1, archive / "press_refresh_20990101.json")
    current = tmp_path / "current.json"
    _accepted_press_receipt(run2, current)
    tone = tmp_path / "tone.csv"; fulltext = tmp_path / "fulltext.jsonl"
    mentions = tmp_path / "mentions.jsonl"; receipt = tmp_path / "feed.json"
    result = press_feed.build_cumulative(
        tone, fulltext, mentions, receipt, current, archive)
    rows = list(csv.DictReader(tone.open(encoding="utf-8-sig")))
    assert result["counts"] == {"accepted_runs": 2, "tone_rows": 2,
                                "fulltext_rows": 2, "official_mentions": 0}
    assert {row["member"] for row in rows} == {"Neutral A", "Neutral B"}
    assert next(row for row in rows if row["member"] == "Neutral A")["title"] == "First accepted title"
    assert result["promotion_mode"] == "CUMULATIVE_ACCEPTED_RUN_LEDGER"


def test_neutral_trickle_audit_promote_fold_and_prepare_local_dashboard(tmp_path):
    archive = tmp_path / "accepted"
    current = tmp_path / "press-intake.json"
    run1 = _neutral_press_run(
        tmp_path / "press_refresh_20260831",
        [{"member": "Neutral A", "bioguide": "N000001",
          "url": "https://example.test/neutral-a"}],
        completed_at="2026-08-31T12:00:00+00:00", record_date="2026-08-31")
    run2 = _neutral_press_run(
        tmp_path / "press_refresh_20260901",
        [{"member": "Neutral B", "bioguide": "N000002",
          "url": "https://example.test/neutral-b"}],
        completed_at="2026-09-01 12:00:00 UTC", record_date="2026-09-01")
    for root in (run1, run2):
        assert audit_collection.main([
            "--scope", "press", "--press-delta-root", str(root),
            "--press-date-floor", "2026-06-25", "--output", str(current),
            "--accepted-press-receipts-dir", str(archive),
        ]) == 0

    tone = tmp_path / "tone.csv"; fulltext = tmp_path / "fulltext.jsonl"
    mentions = tmp_path / "mentions.jsonl"; feed = tmp_path / "feed.json"
    promoted = press_feed.build_cumulative(
        tone, fulltext, mentions, feed, current, archive)
    assert promoted["counts"] == {
        "accepted_runs": 2, "tone_rows": 2, "fulltext_rows": 2,
        "official_mentions": 0,
    }

    baseline = tmp_path / "baseline.csv"
    baseline.write_text(
        "title,url,date,title_src,bioguide,member,chamber,state,party\n",
        encoding="utf-8")
    folded = fold_promoted(feed, baseline, tmp_path / "fold-receipt.json")
    assert {row["source_run"] for row in folded} == {run1.name, run2.name}

    source = tmp_path / "Moneyball.html"
    source.write_text("<html>neutral trickle</html>", encoding="utf-8")
    password = tmp_path / "password.txt"
    password.write_text("neutral-test-secret", encoding="utf-8")
    deployment = publish_dashboard(
        source, tmp_path / "deploy" / "index.html", password,
        tmp_path / "deployment-receipt.json", feed)
    assert deployment["status"] == "LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE"
    assert deployment["press_lineage"]["accepted_runs"] == 2


def test_fold_consumes_only_hash_bound_promoted_runs(tmp_path):
    promoted = _neutral_press_run(tmp_path / "press_refresh_20990101", [
        {"member": "Neutral A", "bioguide": "N000001", "url": "https://example.test/a"}])
    _neutral_press_run(tmp_path / "press_refresh_20990102", [
        {"member": "Neutral B", "bioguide": "N000002", "url": "https://example.test/b"}],
        completed_at="2099-01-02T00:00:00+00:00")
    archive = tmp_path / "accepted"; archive.mkdir()
    intake = archive / "press_refresh_20990101.json"
    _accepted_press_receipt(promoted, intake)
    tone = tmp_path / "tone.csv"; fulltext = tmp_path / "fulltext.jsonl"
    mentions = tmp_path / "mentions.jsonl"; feed = tmp_path / "feed.json"
    press_feed.build_cumulative(tone, fulltext, mentions, feed, intake, archive)
    baseline = tmp_path / "baseline.csv"
    baseline.write_text(
        "title,url,date,title_src,bioguide,member,chamber,state,party\n",
        encoding="utf-8")
    fold_receipt = tmp_path / "fold.json"
    results = fold_promoted(feed, baseline, fold_receipt)
    assert len(results) == 1 and results[0]["source_run"] == promoted.name
    text = baseline.read_text(encoding="utf-8")
    assert "example.test/a" in text and "example.test/b" not in text


def test_dashboard_publication_is_password_safe_and_lineage_bound(tmp_path):
    source = tmp_path / "Moneyball.html"; source.write_text("<html>neutral</html>", encoding="utf-8")
    password = tmp_path / "password.txt"; password.write_text("neutral-test-secret", encoding="utf-8")
    outputs = {}
    for name in ("tone", "fulltext", "mentions"):
        path = tmp_path / (name + ".dat"); path.write_text("neutral-" + name, encoding="utf-8")
        outputs[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    press_receipt = tmp_path / "press.json"
    press_receipt.write_text(json.dumps({
        "schema": "press-refresh-engine-feed-v1", "status": "ACTIVE_TYPED_SUPPLEMENT",
        "promotion_mode": "CUMULATIVE_ACCEPTED_RUN_LEDGER", "accepted_runs": [{"source_run": "neutral"}],
        "generated_at": "2099-01-01T00:00:00+00:00", "outputs": outputs,
    }), encoding="utf-8")
    deployed = tmp_path / "deploy" / "index.html"; receipt = tmp_path / "deploy-receipt.json"
    result = publish_dashboard(source, deployed, password, receipt, press_receipt)
    serialized = receipt.read_text(encoding="utf-8")
    assert result["status"] == "LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE"
    assert result["contract"]["remote_deploy_confirmed"] is False
    assert deployed.is_file() and "neutral-test-secret" not in serialized


def test_hearing_display_requires_current_hash_bound_corrected_attribution(tmp_path):
    artifact = tmp_path / "hearing.json"; receipt = tmp_path / "hearing-receipt.json"
    write_fixture_feed(
        artifact, receipt,
        {"Neutral A": {"antagonism_z": 0.0, "grandstand_z": 0.0}},
        [{"member": "Neutral A", "date": "2099-01-01", "package": "TEST-1",
          "title": "Neutral hearing", "turns": 1}],
        ceiling="2099-01-01", expires_at="2099-02-01T00:00:00+00:00",
        generated_at="2099-01-01T00:00:00+00:00")
    now = datetime(2099, 1, 2, tzinfo=timezone.utc)
    assert load_hearing_display(artifact, receipt, now=now)["appearances"][0]["package"] == "TEST-1"
    artifact.write_text("{}", encoding="utf-8")
    assert load_hearing_display(artifact, receipt, now=now) is None


def test_hearing_display_omits_expired_or_incomplete_coverage_artifact(tmp_path):
    artifact = tmp_path / "hearing.json"; receipt = tmp_path / "hearing-receipt.json"
    write_fixture_feed(
        artifact, receipt,
        {"Neutral A": {"antagonism_z": 0.0, "grandstand_z": 0.0}},
        [{"member": "Neutral A", "date": "2099-01-01", "package": "TEST-1",
          "title": "Neutral hearing", "turns": 1}],
        ceiling="2099-01-01", expires_at="2099-02-01T00:00:00+00:00",
        generated_at="2099-01-01T00:00:00+00:00")
    now = datetime(2099, 1, 2, tzinfo=timezone.utc)
    # positive control first: the unmodified fixture is accepted.
    assert load_hearing_display(artifact, receipt, now=now) is not None
    good = json.loads(receipt.read_text(encoding="utf-8"))

    # artifact_sha256 binds only the artifact bytes (hearing_display_contract.sha256_file
    # runs over artifact_path, never receipt_path), so it stays correct below even though
    # every case here edits only the receipt and never touches the artifact.
    expired = json.loads(json.dumps(good)); expired["expires_at"] = "2099-01-01T00:00:00+00:00"
    receipt.write_text(json.dumps(expired), encoding="utf-8")
    assert load_hearing_display(artifact, receipt, now=now) is None

    partial = json.loads(json.dumps(good)); partial["coverage"]["status"] = "PARTIAL"
    receipt.write_text(json.dumps(partial), encoding="utf-8")
    assert load_hearing_display(artifact, receipt, now=now) is None

    stale_contract = json.loads(json.dumps(good))
    stale_contract["attribution_contract"] = "chamber-scoped-speaker-attribution-v1"
    receipt.write_text(json.dumps(stale_contract), encoding="utf-8")
    assert load_hearing_display(artifact, receipt, now=now) is None


def test_press_feed_keeps_mentions_out_of_stance(tmp_path):
    root = tmp_path / "press_refresh_20990101"; root.mkdir()
    (root / "press_network_retry_receipt.json").write_text(
        json.dumps({"completed_at": "2099-01-01T00:00:00+00:00"}), encoding="utf-8")
    fields = ["bioguide", "member", "chamber", "state", "party", "date_final",
              "date", "title", "url", "body", "source"]
    host = {"bioguide": "A000001", "member": "Alpha Member", "chamber": "House",
            "state": "AA", "party": "Independent", "date_final": "2099-01-01",
            "date": "2099-01-01", "title": "Credit union bill", "url": "https://example.test/a",
            "body": " ".join(["substantive"] * 30), "source": ""}
    mention = dict(host, bioguide="B000001", member="Beta Member",
                   url="https://example.test/a#B000001", source="OFFICIAL_MENTION")
    _csv(root / "press_delta_host_articles.csv", fields, [host])
    _csv(root / "press_delta_article_mentions.csv", fields, [mention])
    scored_fields = ["member", "chamber", "state", "party", "bioguide", "date", "title", "url",
                     "score", "score_graded", "score_label", "polarity", "relevance", "categories",
                     "word_count", "recency_weight"]
    _csv(root / "press_delta_scored.csv", scored_fields, [{
        "member": "Alpha Member", "chamber": "House", "state": "AA", "party": "Independent",
        "bioguide": "A000001", "date": "2099-01-01", "title": "Credit union bill",
        "url": "https://example.test/a", "score": "1", "score_graded": "0.4",
        "score_label": "Weakly positive", "polarity": "1", "relevance": "credit-union mention",
        "categories": "Member Business Lending", "word_count": "30", "recency_weight": "1",
    }])
    tone = tmp_path / "tone.csv"; text = tmp_path / "text.jsonl"
    mentions = tmp_path / "mentions.jsonl"; receipt = tmp_path / "receipt.json"
    retry_sha = hashlib.sha256((root / "press_network_retry_receipt.json").read_bytes()).hexdigest()
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps({
        "schema": "collection-intake-receipt-v1", "status": "ACCEPTED",
        "generated_at": "2099-01-01T00:00:01+00:00",
        "inputs": {"press_delta_root": str(root.resolve()),
                   "press_retry_receipt_sha256": retry_sha,
                   "press_source_sha256": {
                       name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                       for name in ("press_delta_scored.csv", "press_delta_host_articles.csv",
                                    "press_delta_article_mentions.csv")
                   }},
        "press_delta": {"live_network_retry_receipt_valid": True},
    }), encoding="utf-8")
    result = press_feed.build(root, tone, text, mentions, receipt, intake)
    tone_rows = list(csv.DictReader(tone.open(encoding="utf-8-sig")))
    mention_rows = [json.loads(line) for line in mentions.open(encoding="utf-8")]
    assert len(tone_rows) == 1 and tone_rows[0]["member"] == "Alpha Member"
    assert tone_rows[0]["source"] == "PRESS_REFRESH_HOST_ARTICLE"
    assert all(row.get("source") != "OFFICIAL_MENTION" for row in tone_rows)
    assert len(mention_rows) == 1
    assert mention_rows[0]["disposition"] == "REFERENCE_DISCOVERY_ONLY_NOT_STANCE_OR_SPEECH"
    assert result["contract"]["official_mentions_enter_press_stance"] is False
    assert result["collection_intake"]["sha256"] == hashlib.sha256(intake.read_bytes()).hexdigest()


def test_press_feed_refuses_post_audit_source_mutation(tmp_path):
    root = tmp_path / "press_refresh_20990101"; root.mkdir()
    retry = root / "press_network_retry_receipt.json"
    retry.write_text(json.dumps({"completed_at": "2099-01-01T00:00:00+00:00"}), encoding="utf-8")
    fields = ["member", "bioguide", "url", "body"]
    for name in ("press_delta_scored.csv", "press_delta_host_articles.csv",
                 "press_delta_article_mentions.csv"):
        _csv(root / name, fields, [])
    source_hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("press_delta_scored.csv", "press_delta_host_articles.csv",
                     "press_delta_article_mentions.csv")
    }
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps({
        "schema": "collection-intake-receipt-v1", "status": "ACCEPTED",
        "inputs": {"press_delta_root": str(root.resolve()),
                   "press_retry_receipt_sha256": hashlib.sha256(retry.read_bytes()).hexdigest(),
                   "press_source_sha256": source_hashes},
        "press_delta": {"live_network_retry_receipt_valid": True},
    }), encoding="utf-8")
    with (root / "press_delta_scored.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")
    with pytest.raises(RuntimeError, match="not hash-bound"):
        press_feed.build(root, tmp_path / "tone.csv", tmp_path / "text.jsonl",
                         tmp_path / "mentions.jsonl", tmp_path / "receipt.json", intake)


def _wav(path, frames=480000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
        handle.writeframes(b"\x01\x00" * frames)


def test_audio_manifest_is_partitioned_into_candidate_queue_only(tmp_path):
    clips = tmp_path / "clips"; clips.mkdir()
    wav = clips / "wj.wav"; _wav(wav)
    audio_sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    base = {"member": "Alpha Member", "out_wav": str(wav), "sha256": audio_sha,
            "frames": 480000, "samplerate": 16000, "dur": 30.0, "words": 50,
            "seg": 1, "sid": "show-1", "turn_id": "turn-1", "artifact_id": "artifact-1",
            "package": "https://example.test/program/1", "transcript_path": "transcript.json",
            "text_sha256": "a" * 64, "source_audio_sha256": audio_sha,
            "date": "2099-01-01", "show": "Washington Journal", "outlet": "CSPAN",
            "alignment_method": "official-cspan-speaker+official-offset-boundaries"}
    rows = [base, dict(base, outlet="HouseLive", show="House Floor", turn_id="turn-2",
                       artifact_id="artifact-2", out_wav=str(clips / "house.wav"), seg=2),
            dict(base, outlet="NPR", show="NPR", turn_id="", artifact_id="",
                 out_wav=str(clips / "npr.wav"), seg=3)]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"schema": "collection-intake-receipt-v1",
                                 "status": "ACCEPTED",
                                 "inputs": {"clip_manifest_sha256": manifest_sha},
                                 "house": {"all_clip_rows": 3, "all_clip_errors": []}}), encoding="utf-8")
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps([{"member": "Alpha Member"}]), encoding="utf-8")
    out = tmp_path / "reviews.json.candidate"
    doc = audio_feed.build(manifest, audit, out, roster, tmp_path,
                           merge_existing=True, verify_hash=True)
    assert doc["partition"]["total"] == 3
    assert doc["partition"]["queued"] == 1
    assert doc["partition"]["excluded"] == 2
    assert doc["contract"]["active_artifacts_written"] is False
    queued = next(iter(doc["clips"].values()))
    assert queued["reviews"] == [] and queued["topic"] == {}
    assert queued["review_status"].startswith("PENDING_TWO_DISTINCT")


def test_audio_manifest_refuses_stale_audit(tmp_path):
    manifest = tmp_path / "manifest.jsonl"; manifest.write_text("{}\n", encoding="utf-8")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"schema": "collection-intake-receipt-v1",
                                 "status": "ACCEPTED",
                                 "inputs": {"clip_manifest_sha256": "0" * 64},
                                 "house": {"all_clip_rows": 1, "all_clip_errors": []}}), encoding="utf-8")
    roster = tmp_path / "roster.json"; roster.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not bound"):
        audio_feed.build(manifest, audit, tmp_path / "out.json.candidate", roster, tmp_path)


def test_publishers_reject_negative_collection_receipts(tmp_path):
    rejected = tmp_path / "rejected.json"
    rejected.write_text(json.dumps({"schema": "collection-intake-receipt-v1",
                                    "status": "REJECTED"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not an accepted gate receipt"):
        press_feed.build(tmp_path, tmp_path / "tone.csv", tmp_path / "fulltext.jsonl",
                         tmp_path / "mentions.jsonl", tmp_path / "feed.json", rejected)
    manifest = tmp_path / "clips.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    roster = tmp_path / "roster.json"
    roster.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not an accepted gate receipt"):
        audio_feed.build(manifest, rejected, tmp_path / "audio.json", roster, tmp_path)


def test_canonical_pipeline_orders_intake_extractors_and_scorers():
    import run_pipeline
    names = [name for name, _args in run_pipeline.CHAIN]
    assert names.index("audit_collection.py") < names.index("build_press_engine_feed.py")
    assert names.index("audit_collection.py") < names.index("b5_audio_manifest_adapter.py")
    assert names.index("extract_cu_interviews.py") < names.index("interview_stance_stack.py")
    assert names.index("build_press_engine_feed.py") < names.index("press_stance_stack.py")
    assert names.index("press_stance_stack.py") < names.index("build_media_blend.py")
    assert names.index("press_hcoi.py") < names.index("build_multilayer_network.py")
    assert names.index("build_multilayer_network.py") < names.index("make_member_desk.py")
    assert names.index("make_combined.py") < names.index("make_email_package.py")
    assert names.index("make_email_package.py") < names.index("audit_full.py")


def test_weekly_collection_publication_gates_fail_hard():
    tree = ast.parse((FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig"))
    required = {"press delta refresh", "press intake audit", "collection intake audit",
                "press engine feed", "fold promoted press runs", "accepted audio intake",
                "prepare dashboard deployment"}
    observed = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "step" or not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = node.args[0].value
        if name in required:
            observed[name] = any(
                keyword.arg == "hard" and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
    assert observed == {name: True for name in required}


def test_unrelated_collection_failures_are_deferred_until_press_acceptance_finishes():
    source = (FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig")
    bill = source.index("_bill_repository_ok=step('Congress.gov bill repository'")
    interviews = source.index("step('interviews: CNN (14d)'")
    press = source.index("step('press engine feed'")
    fold = source.index("step('fold promoted press runs'")
    abort = source.index("_hard_abort('deferred collection prerequisites'", bill)
    full_audit = source.index("step('collection intake audit'", abort)
    audio = source.index("step('accepted audio intake'")
    roster = source.index("step('collector roster verification'")
    assert bill < interviews < press < fold < abort < full_audit < audio < roster
    deferred = source[bill:press]
    assert "hard=False" in deferred
    for name in ("interviews: CNN (14d)", "interviews: MTP", "interviews: CBS",
                 "interviews: ABC", "interviews: PBS (21d)", "interviews: NPR (14d)",
                 "interviews: MS NOW"):
        call = deferred[deferred.index("step('%s'" % name):]
        assert "hard=False" in call.split("\n", 1)[0]
        assert "_deferred_collection_failures.append('%s')" % name in source
    assert "--scope','press'" in source[source.index("step('press intake audit'"):press]
    assert "press_collection_intake_receipt.json" in source[source.index("step('press intake audit'"):fold]


def test_weekly_hard_abort_names_the_failing_step_in_the_status_marker():
    """weekly_refresh.py cannot be imported (module scope takes locks and runs the refresh), so the
    guard is structural. Every hard step without a `gate=` reaches _hard_abort with failures=[];
    the status marker must still say WHICH step died, or an unattended supervisor only learns that
    'something' hard-failed (the whole of 2026-08-28)."""
    tree = ast.parse((FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig"))
    abort = next((node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_hard_abort"), None)
    assert abort is not None
    writes = [node for node in ast.walk(abort)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "_write_status"]
    assert len(writes) == 1
    failures = next((keyword for keyword in writes[0].keywords if keyword.arg == "failures"), None)
    assert failures is not None, "_hard_abort must write a failures list"
    reported = failures.value
    if isinstance(reported, ast.Name):   # resolve one local binding (`_reported = ...`)
        reported = next(node.value for node in ast.walk(abort)
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == reported.id
                                for target in node.targets))
    names = {node.id for node in ast.walk(reported) if isinstance(node, ast.Name)}
    assert "name" in names, \
        "_hard_abort's status failures list must carry the failing step name, got %s" % (
            ast.unparse(failures.value),)
    outcome = next(keyword for keyword in writes[0].keywords if keyword.arg == "last_outcome")
    assert ast.literal_eval(outcome.value) == "HARD_FAILURE"


def test_weekly_refresh_has_cross_task_single_owner_lock():
    source = (FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig")
    assert ".weekly_refresh.lock" in source
    assert "LK_NBLCK" in source and "LOCK_EX|fcntl.LOCK_NB" in source
    assert "pipeline_run_lock(owner_kind='weekly_refresh')" in source
    assert "child_lock_environment" in source


def test_pipeline_writer_lock_blocks_peer_and_allows_only_direct_child_adoption(tmp_path):
    lock_path = tmp_path / ".pipeline_run.lock"
    with pipeline_run_lock(lock_path, owner_kind="test") as lease:
        with pytest.raises(RuntimeError, match="already running"):
            with pipeline_run_lock(lock_path, owner_kind="competitor"):
                pass
        environment = os.environ.copy()
        environment.update(child_lock_environment(lease))
        child = subprocess.run(
            [sys.executable, "-c",
             "import sys;sys.path.insert(0,sys.argv[1]);"
             "from pipeline_lock import pipeline_run_lock;"
             "p=sys.argv[2];"
             "c=pipeline_run_lock(p);lease=c.__enter__();"
             "print(lease.get('adopted'));c.__exit__(None,None,None)",
             str(FEC), str(lock_path)],
            capture_output=True, text=True, env=environment,
        )
        assert child.returncode == 0 and child.stdout.strip() == "True"
        assert lock_path.exists()
    assert not lock_path.exists()


def test_pipeline_writer_lock_never_unlinks_a_fresh_partially_written_lease(tmp_path):
    lock_path = tmp_path / ".pipeline_run.lock"
    lock_path.write_bytes(b"")
    with pytest.raises(RuntimeError, match="already running"):
        with pipeline_run_lock(lock_path, owner_kind="competitor"):
            pass
    assert lock_path.exists()


def test_pipeline_descendant_can_recognize_but_not_adopt_parent_lease(tmp_path, monkeypatch):
    lock_path = tmp_path / ".pipeline_run.lock"
    with pipeline_run_lock(lock_path, owner_kind="test") as lease:
        for key, value in child_lock_environment(lease).items():
            monkeypatch.setenv(key, value)
        assert valid_inherited_lease(lock_path)
        assert not valid_inherited_lease(lock_path, direct_parent=True)
        with pytest.raises(RuntimeError, match="invalid inherited"):
            with pipeline_run_lock(lock_path):
                pass
    assert not lock_path.exists()


def _scripted_step(monkeypatch, tmp_path, returncode, stdout, stderr):
    class Result:
        pass

    Result.returncode = returncode
    Result.stdout = stdout
    Result.stderr = stderr
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *args, **kwargs: Result())
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)
    return messages


def test_supervisor_gate_step_preserves_stderr(monkeypatch, tmp_path):
    messages = _scripted_step(monkeypatch, tmp_path, 1, "estimate line\n",
                              "Traceback: submission rejected\n")
    ok, output = supervisor.gate_step("submission", "producer.py", [])
    assert ok is False
    assert "estimate line" in output
    assert "submission rejected" in output
    assert any("submission rejected" in message for message in messages)


def test_gate_step_success_returns_stdout_only(monkeypatch, tmp_path):
    """The returned text feeds a SPEND decision (spend_guard takes the last 'est ~$' match) and the
    'ALL MODULES CLEAN' probe. On a successful run, stderr chatter must not reach either."""
    _scripted_step(monkeypatch, tmp_path, 0, "est ~$0.01\nALL MODULES CLEAN\n",
                   "WARNING: noisy library\n")
    ok, output = supervisor.gate_step("clean step", "producer.py", [])
    assert ok is True
    assert "WARNING: noisy library" not in output
    assert output == "est ~$0.01\nALL MODULES CLEAN\n"
    assert not (tmp_path / "data" / "engine").exists(), "a successful step writes no .err artifact"


def test_spend_guard_estimate_ignores_stderr_on_a_successful_dry_run(monkeypatch, tmp_path):
    """REGRESSION GUARD: appending stderr on the success path let any '$' figure written to stderr
    become the LAST 'est ~$' match and thereby move a real spend decision."""
    monkeypatch.delenv("REF_GATE_CAP", raising=False)
    _scripted_step(monkeypatch, tmp_path, 0,
                   "scored set: 10 | cached 8 | to classify 2 | est ~$0.01\n",
                   "WARNING: prior invoice est ~$980.00\n")
    go, est = supervisor.spend_guard("reference --score", "producer.py", ["--score"],
                                     "REF_GATE_CAP", "1.00")
    assert (go, est) == (True, 0.01)


def test_gate_step_failure_persists_the_full_body_and_logs_five_lines(monkeypatch, tmp_path):
    """2026-08-28: every failed weekly_refresh logged only its atexit 'EMAIL REPORT SKIPPED' line —
    the real failure was nowhere in the supervisor log, and the body exceeded any tail."""
    body = "\n".join("line %04d %s" % (index, "x" * 60) for index in range(400))
    stdout = body + "\nEMAIL REPORT SKIPPED - unrelated epilogue\n"
    messages = _scripted_step(monkeypatch, tmp_path, 1, stdout, "Traceback: batch 400 invalid\n")
    ok, output = supervisor.gate_step("weekly_refresh", "weekly_refresh.py", [])
    assert ok is False
    saved = tmp_path / "data" / "engine" / "gate_step_weekly_refresh.err"
    assert saved.is_file()
    persisted = saved.read_text(encoding="utf-8")
    assert persisted.endswith(output), "the .err file must hold the FULL captured body"
    assert "line 0000" in persisted and "batch 400 invalid" in persisted
    assert len(output) > 1000, "this fixture must exceed the old 1000-char log tail"
    # the run-up to the epilogue is now visible, not just the last line
    prior = [message for message in messages if message.startswith("      prior |")]
    assert len(prior) == 4
    assert any("line 0399" in message for message in prior)
    assert any(str(saved) in message for message in messages)


def test_gate_step_error_path_is_filesystem_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    for name in ("reference --score", "big5 re-score (dry est)", "C retest (prereg)"):
        path = Path(supervisor.gate_step_error_path(name))
        assert path.parent == tmp_path / "data" / "engine"
        assert re.fullmatch(r"gate_step_[a-z0-9_]+\.err", path.name), path.name


def test_supervisor_gate1_never_marks_failed_refresh_done(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: gate == "GATE-2")
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)
    monkeypatch.setattr(supervisor, "gate_step", lambda *args, **kwargs: (False, "refresh failed"))
    supervisor.run_gates()
    assert not (tmp_path / "gate1.done").exists()
    assert any("GATE-1 INCOMPLETE" in message for message in messages)


def test_supervisor_gate1_marks_only_two_successes_done(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: gate == "GATE-2")
    monkeypatch.setattr(supervisor, "log", lambda _message: None)
    status = tmp_path / "data" / "engine" / "weekly_refresh_status.json"
    status.parent.mkdir(parents=True)
    def successful_refresh(*args, **kwargs):
        status.write_text(json.dumps({"last_outcome": "COMPLETE", "failures": []}), encoding="utf-8")
        return True, "refresh complete"
    monkeypatch.setattr(supervisor, "gate_step", successful_refresh)
    supervisor.run_gates()
    assert (tmp_path / "gate1.done").is_file()
    receipt = json.loads((tmp_path / "data" / "engine" / "collector_gate1_receipt.json").read_text())
    assert receipt["status"] == "COMPLETE"


def _gate1_harness(tmp_path, monkeypatch, refresh):
    """Drive run_gates' GATE-1 branch with a scripted weekly_refresh. Returns (calls, messages);
    `refresh` is a zero-arg callable returning gate_step's (ok, output)."""
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: gate == "GATE-2")
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)
    calls = []

    def gate_step(name, *args, **kwargs):
        calls.append(name)
        return refresh()

    monkeypatch.setattr(supervisor, "gate_step", gate_step)
    return calls, messages


def _cooldown_marker(tmp_path):
    return tmp_path / "data" / "engine" / "collector_gate1_cooldown.json"


def test_gate1_hard_failure_starts_a_retry_cooldown(tmp_path, monkeypatch):
    """2026-08-28: a failed weekly_refresh was relaunched on the very next 10-minute tick, ~15
    times in six hours, each attempt re-walking every collector to re-reach the same failure."""
    calls, messages = _gate1_harness(tmp_path, monkeypatch, lambda: (False, "refresh failed"))
    supervisor.run_gates()
    assert calls == ["weekly_refresh"]
    state = json.loads(_cooldown_marker(tmp_path).read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 1
    assert state["next_retry_after_s"] == supervisor.GATE1_COOLDOWN_S
    calls.clear(); messages.clear()
    supervisor.run_gates()          # the very next tick
    assert calls == [], "weekly_refresh was relaunched inside its own cooldown"
    assert any("GATE-1 COOLDOWN" in message for message in messages), messages


def test_gate1_cooldown_expires_then_backs_off_further(tmp_path, monkeypatch):
    calls, _messages = _gate1_harness(tmp_path, monkeypatch, lambda: (False, "refresh failed"))
    supervisor.run_gates()
    marker = _cooldown_marker(tmp_path)
    state = json.loads(marker.read_text(encoding="utf-8"))
    state["last_failure_at"] -= supervisor.GATE1_COOLDOWN_S + 1
    marker.write_text(json.dumps(state), encoding="utf-8")
    calls.clear()
    supervisor.run_gates()
    assert calls == ["weekly_refresh"], "an elapsed cooldown must not block the retry"
    state = json.loads(marker.read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 2
    assert state["next_retry_after_s"] == supervisor.GATE1_COOLDOWN_S * 2


def test_gate1_success_clears_the_cooldown(tmp_path, monkeypatch):
    status = tmp_path / "data" / "engine" / "weekly_refresh_status.json"
    status.parent.mkdir(parents=True)
    _cooldown_marker(tmp_path).write_text(json.dumps({
        "schema": supervisor.GATE1_COOLDOWN_SCHEMA, "consecutive_failures": 3,
        "last_failure_at": time.time() - 10 ** 6}), encoding="utf-8")

    def refresh():
        status.write_text(json.dumps({"last_outcome": "COMPLETE", "failures": []}), encoding="utf-8")
        return True, "refresh complete"

    calls, _messages = _gate1_harness(tmp_path, monkeypatch, refresh)
    supervisor.run_gates()
    assert calls == ["weekly_refresh"]
    assert not _cooldown_marker(tmp_path).exists()
    assert (tmp_path / "gate1.done").is_file()


def test_gate1_backoff_doubles_and_is_capped():
    assert supervisor._gate1_backoff_seconds(0) == 0.0
    assert supervisor._gate1_backoff_seconds(1) == supervisor.GATE1_COOLDOWN_S
    assert supervisor._gate1_backoff_seconds(3) == supervisor.GATE1_COOLDOWN_S * 4
    assert supervisor._gate1_backoff_seconds(500) == supervisor.GATE1_COOLDOWN_MAX_S


def test_gate1_cooldown_fails_open_on_an_unreadable_marker(tmp_path, monkeypatch):
    """The cooldown paces the machine; it must never be what permanently prevents a refresh."""
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    marker = _cooldown_marker(tmp_path)
    marker.parent.mkdir(parents=True)
    assert supervisor._gate1_cooldown_remaining() == (0.0, 0)   # absent marker
    for corrupt in ("{not json", json.dumps({"schema": "someone-elses-file"}),
                    json.dumps({"schema": supervisor.GATE1_COOLDOWN_SCHEMA,
                                "consecutive_failures": "many", "last_failure_at": None})):
        marker.write_text(corrupt, encoding="utf-8")
        assert supervisor._gate1_cooldown_remaining() == (0.0, 0), corrupt
    # a future-dated marker (clock/DST move) must also not wedge the gate
    marker.write_text(json.dumps({"schema": supervisor.GATE1_COOLDOWN_SCHEMA,
                                  "consecutive_failures": 2,
                                  "last_failure_at": time.time() + 10 ** 6}), encoding="utf-8")
    assert supervisor._gate1_cooldown_remaining()[0] == 0.0


def test_supervisor_gate2_never_receipts_failed_validation(tmp_path, monkeypatch):
    artifact = tmp_path / "data" / "members" / "big5_llm_opus.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: gate == "GATE-1")
    monkeypatch.setattr(supervisor, "spend_guard", lambda *args, **kwargs: (True, 0.0))
    monkeypatch.setattr(supervisor, "log", lambda _message: None)
    def gate(name, *args, **kwargs):
        return (False, "validation failed") if name == "validate_big5" else (True, "ok")
    monkeypatch.setattr(supervisor, "gate_step", gate)
    supervisor.run_gates()
    assert not (tmp_path / "data" / "engine" / "collector_gate2_receipt.json").exists()
    assert not (tmp_path / "gate2.done").exists()


def _audit_output(names, passed=200):
    """An audit_full tail shaped exactly like fec\\audit\\audit_full.py:2214-2216 prints it."""
    return "FULL-BODY AUDIT — %d passed, %d FAILED\n  FAILURES: %r\n" % (
        passed, len(names), list(names))


def _gate2_harness(tmp_path, monkeypatch, audit_result):
    artifact = tmp_path / "data" / "members" / "big5_llm_opus.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    (tmp_path / "c_prereg_test.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "B", str(tmp_path))
    monkeypatch.setattr(supervisor, "_gate_receipt_valid", lambda gate: gate == "GATE-1")
    monkeypatch.setattr(supervisor, "spend_guard", lambda *args, **kwargs: (True, 0.0))
    messages = []
    monkeypatch.setattr(supervisor, "log", messages.append)

    def gate(name, *args, **kwargs):
        return audit_result if name == "audit_full" else (True, "ok")

    monkeypatch.setattr(supervisor, "gate_step", gate)
    supervisor.run_gates()
    receipt = tmp_path / "data" / "engine" / "collector_gate2_receipt.json"
    return receipt, messages


def test_gate2_tolerates_exactly_the_ratified_standing_audit_items(tmp_path, monkeypatch):
    """audit_full always exits 1 while V-1 and the legfix5 culling item stand, so requiring
    returncode==0 made GATE-2 unpassable: it would loop every 10 minutes forever, re-entering the
    paid big5/reference steps each time. The tolerance must reuse weekly_refresh's ratified list."""
    receipt, messages = _gate2_harness(
        tmp_path, monkeypatch, (False, _audit_output(KNOWN_GATED_FAILURES)))
    assert receipt.is_file(), messages
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETE"
    assert payload["steps"]["audit_full"] is True
    assert payload["steps"]["audit_known_gated_failures"] == list(KNOWN_GATED_FAILURES)


def test_gate2_still_hard_fails_on_any_novel_audit_failure(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: one unratified name alongside the known two must still block the gate."""
    novel = "own-source channel inputs within 28 days of the corpus (channels are default-ON)"
    receipt, messages = _gate2_harness(
        tmp_path, monkeypatch,
        (False, _audit_output(list(KNOWN_GATED_FAILURES) + [novel])))
    assert not receipt.exists()
    assert not (tmp_path / "gate2.done").exists()
    assert any("NOVEL" in message and novel in message for message in messages), messages


def test_gate2_fails_closed_on_an_unreadable_audit_report(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: a report we cannot parse proves nothing — no tolerance, no receipt.
    Covers a missing FAILURES line, a missing tally, and a tally/name-count mismatch (truncation)."""
    truncated = ("FULL-BODY AUDIT — 200 passed, 5 FAILED\n  FAILURES: %r\n"
                 % list(KNOWN_GATED_FAILURES))
    for output in ("audit died before printing anything",
                   "  FAILURES: %r\n" % list(KNOWN_GATED_FAILURES),
                   truncated):
        receipt, messages = _gate2_harness(
            tmp_path / hashlib.sha256(output.encode()).hexdigest()[:8], monkeypatch,
            (False, output))
        assert not receipt.exists(), output
        assert any("not a readable audit failure report" in message for message in messages), output


def test_known_gated_failure_list_has_exactly_one_source_of_truth():
    """Two unattended gates consult this list. A second copy could let them disagree about which
    audit dissent is Paul-ratified, and a name added in one place would not apply in the other."""
    owner = (FEC / "audit_failure_gate.py").read_text(encoding="utf-8-sig")
    assert "KNOWN_GATED_FAILURES = (" in owner
    for consumer in ("weekly_refresh.py", "collect/collector_supervisor.py"):
        source = (FEC / consumer).read_text(encoding="utf-8-sig")
        assert "audit_failure_gate import" in source, consumer
        assert "KNOWN_GATED_FAILURES=(" not in source and "KNOWN_GATED_FAILURES = (" not in source, \
            "%s re-defines the ratified list instead of importing it" % consumer


def test_ratified_audit_item_names_still_exist_verbatim_in_audit_full():
    """The tolerance matches audit_full's ck() names as exact strings. If a check is reworded and
    this list is not, both gates silently stop tolerating it and deadlock again."""
    source = (FEC / "audit" / "audit_full.py").read_text(encoding="utf-8-sig")
    # The A1 store names are not literals: system_manifest_check.py, which audit_full loads, builds
    # them per manifest store from one template, so the template is what must not be reworded.
    manifest_check = (FEC / "audit" / "system_manifest_check.py").read_text(encoding="utf-8-sig")
    for name in KNOWN_GATED_FAILURES:
        if re.fullmatch(r"A1 \S+ still exists", name):
            assert "'A1 %s still exists'" in manifest_check and "system_manifest_check.py" in source, \
                "ratified A1 item no longer built by audit_full's manifest check: %s" % name
            continue
        assert name in source, "ratified audit item no longer present in audit_full.py: %s" % name


def test_audit_failure_parser_rejects_incomplete_reports():
    assert audit_failures(_audit_output(KNOWN_GATED_FAILURES), "")[0] == list(KNOWN_GATED_FAILURES)
    assert audit_failures("no report here", "")[0] is None
    assert audit_failures("FULL-BODY AUDIT — 1 passed, 2 FAILED\n  FAILURES: ['a']\n", "")[0] is None
    assert audit_failures("  FAILURES: ['a']\n", "")[0] is None
    assert tolerate_known_gated("nothing", "", log=lambda _message: None) == (False, [])


def test_press_reader_rejects_mixed_publication_set(tmp_path):
    tone = tmp_path / "press_refresh_tone.csv"
    fulltext = tmp_path / "press_refresh_fulltext.jsonl"
    mentions = tmp_path / "official_mentions.jsonl"
    receipt = tmp_path / "receipt.json"
    tone.write_text("member,url\nAlpha,https://example.test/a\n", encoding="utf-8")
    fulltext.write_text('{"url":"https://example.test/a","text":"body"}\n', encoding="utf-8")
    mentions.write_text("", encoding="utf-8")
    outputs = {
        "data/stance/press_refresh_tone.csv": hashlib.sha256(tone.read_bytes()).hexdigest(),
        "data/stance/press_refresh_fulltext.jsonl": hashlib.sha256(fulltext.read_bytes()).hexdigest(),
        "data/press/official_mentions.jsonl": hashlib.sha256(mentions.read_bytes()).hexdigest(),
    }
    receipt.write_text(json.dumps({"schema": "press-refresh-engine-feed-v1",
                                   "status": "ACTIVE_TYPED_SUPPLEMENT",
                                   "promotion_mode": "CUMULATIVE_ACCEPTED_RUN_LEDGER",
                                   "accepted_runs": [{"source_run": "neutral-fixture"}],
                                   "outputs_sha256": outputs}), encoding="utf-8")
    press_stream_reader.validate_refresh_receipt(tone, fulltext, mentions, receipt)
    fulltext.write_text('{"url":"https://example.test/a","text":"partial new vintage"}\n',
                        encoding="utf-8")
    with pytest.raises(RuntimeError, match="mixed or uncommitted"):
        press_stream_reader.validate_refresh_receipt(tone, fulltext, mentions, receipt)


def test_email_package_is_hash_bound_and_contains_current_surfaces(tmp_path, monkeypatch):
    # build() now also checks each page's recognition-generation marker against the active honors
    # generation (honors_publication.validate_pages, its own suites). These fixture pages carry no
    # generation, so the honors layer runs in its explicit legacy mode: no marker expected.
    monkeypatch.setenv("HONORS_MODE", "legacy")
    # MONOLITH_FILES became (source, arcname) pairs on 2026-09-05: the all-in-one zip is built
    # from the three-surface Moneyball_Email.html and stored as Moneyball.html.  Only the SOURCE
    # names are files that have to exist in the tree.
    sources = list(make_email_package.FAMILY_FILES) + [
        name for name, _arcname in make_email_package.entries(make_email_package.MONOLITH_FILES)]
    for name in sources:
        (tmp_path / name).write_text("<html>%s</html>" % name, encoding="utf-8")
    manifest = make_email_package.build(tmp_path, tmp_path / "outputs" / "email_package")
    assert manifest["schema"] == "email-package-v2"
    assert manifest["status"] == "CURRENT_HASH_BOUND"
    assert len(manifest["inputs"]) == 8
    for relative, package in manifest["packages"].items():
        path = tmp_path / Path(relative)
        assert path.is_file() and path.stat().st_size == package["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == package["sha256"]
    # Every hash-bound input must name a file that really is in the tree -- that is exactly what
    # fec/audit/audit_full.py re-checks -- and the monolith must also record the arcname it is
    # delivered under, so a reader of the manifest can tell source from delivered name.
    for item in manifest["inputs"] + manifest["monolith_inputs"]:
        source = tmp_path / item["name"]
        assert source.is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]
    assert [item["name"] for item in manifest["monolith_inputs"]] == ["Moneyball_Email.html"]
    assert [item["arcname"] for item in manifest["monolith_inputs"]] == ["Moneyball.html"]
