"""Refresh official bills, Excel, measurements and the live dashboard as one job.

Collectors run before the existing network-free pipeline.  The shared writer
lease excludes the weekly refresh and manual pipelines for the entire job.
Run without arguments for a complete refresh, or --status for a read-only check.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT
from lane_file_io import replace_atomic
from pipeline_lock import pipeline_run_lock, child_lock_environment
from process_liveness import process_alive
from audit_failure_gate import tolerate_known_gated
try:
    from surfaces.glassbox_emit import emit as _emit, new_run_id
except ImportError:
    def _emit(*args, **kwargs):
        return None
    def new_run_id(prefix):
        return prefix

ROOT = Path(ROOT)
CODE = Path(__file__).resolve().parents[1]
STATUS = Path("data/engine/congress_pipeline_status.json")
RECEIPT = Path("data/engine/congress_pipeline_receipt.json")
LIVE_LOG = Path("data/engine/congress_pipeline_live.log")
LIVE_LOG_KEEP = 400
HEARTBEAT_SECONDS = 60
# Discovery drives a real browser. A flaky Playwright launch (observed
# 2026-09-13: "launchPersistentContext: Timeout 180000ms exceeded") must not
# cost the whole day and restart a four-hour chain every hour, so the step is
# retried once and then deferred rather than treated as a hard failure. Only
# discovery is retried; every other step keeps stop-on-first-failure.
DISCOVERY_RETRY_DELAY_SECONDS = 300
RETRYABLE_STEPS = ("discovery",)
# The two combined dashboards are ~98 MB each. Copying them into the before-image
# cost 287 MB and eleven silent minutes per run, and nothing ever reads the copy:
# rank_changes reads before/ranks_python.json and nothing else opens before/*.html.
# Their identity is recorded instead.
HASH_ONLY_SNAPSHOT = (".html",)
SOURCES = (
    "bill_repository_live.json", "data/engine/congress_bill_refresh_receipt.json",
    "data/legislation/sentinel_admissions.json",
    "data/members/collector_roster_updates.ndjson", "data/members/collector_roster_pending.ndjson",
    "data/members/sentinel_verified_membership_history.ndjson",
    "data/engine/sentinel_verified_membership_history_receipt.json",
    "data/engine/sentinel_bill_workbook_receipt.json",
    "Sentinel Bill Ledger.xlsx", "Moneyball Main Version - Blended.xlsx",
    "archive_leg.json", "ranks_python.json", "dashboard_data.json",
    "data/engine/primacy_factors.json", "data/lobbying/industry_tiers.json",
    "member_desk_bundle.json", "Moneyball.html", "Moneyball_Redesign.html",
    "data/engine/source_to_dashboard_receipt.json",
    "data/engine/live_dashboard_alias_receipt.json",
    "data/engine/daily_media_refresh_receipt.json",
    "data/engine/daily_media_refresh_success.json", "Social Master.xlsx",
    "data/members/social_member_summary.json",
)
# W1 (DE-043): the membership sidecar is hash-bound to the repository and only the chain's
# verify_collector_roster_updates.py rebinds it, so these two are rolled back on a failed run.
ROLLBACK_SOURCES = ("bill_repository_live.json", "data/engine/congress_bill_refresh_receipt.json")
MEMBERSHIP_SIDECAR = "data/legislation/official_membership_movements.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # The status document is polled by the watcher every five seconds; a reader
    # holding it must not be able to kill the run on the rename.
    replace_atomic(temp, path)


def health(root=ROOT):
    path = Path(root) / STATUS
    if not path.is_file():
        return {"status": "NOT_RUN"}
    result = read(path)
    if result.get("status") == "RUNNING" and not process_alive(result.get("owner_pid")):
        result = {**result, "status": "INTERRUPTED", "reason": "The recorded owner process is no longer running."}
    return result


def steps(admission_limit, include_media=False, smoke=False):
    """The ordered stages. A step whose script is None is recorded, never run.

    Smoke mode exercises the whole shape of the job cheaply: discovery is
    skipped, admissions takes one new candidate and makes no model call, media
    collects a three-source subset and skips press, and the canonical pipeline
    still runs in full because there is no shorter honest version of it.
    """
    # 900s, not 120s: calibration is a local comparison that takes 0.14s on an
    # idle disk, but was measured at 71.65s under load and timed out at 120s on
    # 2026-09-13 19:42Z with its own log already reading "OK (3 checked)". The
    # budget bounds a hang; it should not cost a whole daily attempt to I/O.
    result = [("calibration", "calibration_guard.py", [], 900)]
    result.append(("discovery", None if smoke else "legislation/refresh_legislation_discovery.py", [], 21600))
    admissions = (["--limit", "1", "--no-llm", "--no-install"] if smoke
                  else ["--limit", str(admission_limit)])
    result.extend([
        ("admissions", "legislation/refresh_sentinel_admissions.py", admissions, 14400),
        ("votes", "legislation/refresh_vote_sources.py", [], 1800),
        ("repository", "legislation/update_bill_repository.py", [], 14400),
        ("billstatus", "legislation/refresh_billstatus_bulk.py", [], 3600),
    ])
    if include_media or smoke:
        local_date = datetime.now().astimezone().date().isoformat()
        result.append(("media", "collect/daily_media_refresh.py",
                       ["--local-date", local_date] + (["--smoke"] if smoke else []), 172800))
    result.append(("pipeline", "run_pipeline.py",
                   [] if (include_media or smoke) else ["--from", "verify_collector_roster_updates.py"], 14400))
    return result


def live_log(root, step, status, elapsed=None, note=""):
    """Append one human-readable progress line. Never held open, never raises."""
    try:
        path = Path(root) / LIVE_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        duration = "" if elapsed is None else "%.1fs" % elapsed
        with path.open("a", encoding="utf-8") as handle:
            handle.write("%s  %-12s %-14s %9s  %s\n" % (now(), step, status, duration, note))
    except Exception:
        # A missing progress line must never be able to fail a production run.
        pass


def trim_live_log(root, keep=LIVE_LOG_KEEP):
    """Bound the progress log at run start so it can never grow without limit."""
    try:
        path = Path(root) / LIVE_LOG
        if not path.is_file():
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(True)
        if len(lines) > keep:
            path.write_text("".join(lines[-keep:]), encoding="utf-8")
    except Exception:
        pass


def acknowledged_audit_only(output):
    # Use the same ratified exceptions as the weekly refresh; do not bypass a
    # failed producer or accept an incomplete/truncated audit verdict.
    module = ast.parse((CODE / "run_pipeline.py").read_text(encoding="utf-8"))
    chain = next(ast.literal_eval(node.value) for node in module.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "CHAIN" for t in node.targets))
    if chain[-1][0] != "audit_full.py" or not re.search(r"^\s*audit_full\.py\s+!!", output, re.M):
        return False, []
    return tolerate_known_gated(output, "", log=lambda line: None)


def validated_media_snapshot(root, started):
    """Validate and hash one receipt read, never two potentially different files."""
    media_path = Path(root) / "data/engine/daily_media_refresh_receipt.json"
    with media_path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        contents = handle.read()
        after = os.fstat(handle.fileno())
    if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
            or len(contents) != after.st_size):
        raise RuntimeError("Media receipt changed while being validated")
    media = json.loads(contents)
    try:
        completed = datetime.fromisoformat(str(media["completed_at"]).replace("Z", "+00:00"))
        if completed.tzinfo is None:
            raise ValueError("media completion must include its timezone")
        completed_at = completed.timestamp()
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Media collection lacks a verified completion timestamp") from error
    # Windows file-write times can trail the precise process clock. The exact
    # producer completion timestamp proves freshness; metadata remains a coarse
    # independent check, never the sole evidence that an old PASS is current.
    if (media.get("schema") != "daily-media-refresh-receipt-v1" or media.get("status") != "PASS"
            or not started <= completed_at <= time.time()
            or after.st_mtime <= started - 1):
        raise RuntimeError("Media collection did not produce a fresh verified PASS receipt")
    return media, {"sha256": hashlib.sha256(contents).hexdigest(), "size": len(contents)}


def validate_media_receipt(root, started):
    return validated_media_snapshot(root, started)[0]


def validate_publication(root, started, include_media=False):
    root = Path(root)
    repository = read(root / "data/engine/congress_bill_refresh_receipt.json")
    workbook = read(root / "data/engine/sentinel_bill_workbook_receipt.json")
    source = read(root / "data/engine/source_to_dashboard_receipt.json")
    alias = read(root / "data/engine/live_dashboard_alias_receipt.json")
    for label, receipt, schema in (("repository", repository, "congress-bill-refresh-receipt-v1"),
                                   ("workbook", workbook, "sentinel-bill-workbook-receipt-v1"),
                                   ("source-to-dashboard", source, "source-to-dashboard-receipt-v1")):
        if receipt.get("status") != "PASS" or receipt.get("schema") != schema:
            raise RuntimeError(f"{label} did not produce a PASS receipt")
    bindings = source.get("inputs_sha256") or {}
    required = {"member_desk_bundle.json", "dashboard_data.json", "ranks_python.json", "archive_leg.json",
                "Moneyball.html", "Moneyball_Redesign.html"}
    if not required.issubset(bindings):
        raise RuntimeError("Source-to-dashboard receipt is missing required input hash bindings")
    for relative, expected in bindings.items():
        path = (root / relative).resolve()
        path.relative_to(root.resolve())
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError(f"Source-to-dashboard input hash has changed: {relative}")
    repo_sha = digest(root / "bill_repository_live.json")
    if repository["repository"]["sha256"] != repo_sha or workbook["repository_sha256"] != repo_sha:
        raise RuntimeError("Repository and Excel receipts do not refer to the same bill generation")
    if not workbook.get("installed") or workbook["output"]["sha256"] != digest(root / "Sentinel Bill Ledger.xlsx"):
        raise RuntimeError("The verified Sentinel workbook was not installed")
    if (alias.get("schema") != "live-dashboard-alias-v1" or alias.get("status") != "PASS"
            or alias.get("sha256") != digest(root / "Moneyball_Redesign.html")):
        raise RuntimeError("The live dashboard alias is not verified by its publication receipt")
    for relative in ("Sentinel Bill Ledger.xlsx", "Moneyball Main Version - Blended.xlsx",
                     "dashboard_data.json", "member_desk_bundle.json",
                     "ranks_python.json", "archive_leg.json", "Moneyball.html",
                     "data/engine/live_dashboard_alias_receipt.json",
                     "data/engine/congress_bill_refresh_receipt.json", "data/engine/sentinel_bill_workbook_receipt.json",
                     "data/engine/source_to_dashboard_receipt.json"):
        if (root / relative).stat().st_mtime < started:
            raise RuntimeError(f"Required output was not regenerated this run: {relative}")
    if digest(root / "Moneyball.html") != digest(root / "Moneyball_Redesign.html"):
        # The clean workspace shell stands in for the copy only while its receipt binds it to this generation.
        from surfaces.sync_live_dashboard_alias import clean_workspace_current
        if not clean_workspace_current(root):
            raise RuntimeError("The served dashboard alias does not match the rebuilt dashboard")
    media_relative = "data/engine/daily_media_refresh_receipt.json"
    media_binding = validated_media_snapshot(root, started)[1] if include_media else None
    artifacts = {relative: {"sha256": digest(root / relative), "size": (root / relative).stat().st_size}
                 for relative in SOURCES if (root / relative).is_file()
                 and not (include_media and relative == media_relative)}
    if include_media:
        artifacts[media_relative] = media_binding
    return artifacts


def rollback_unbound_repository(root, run_dir):
    """W1: undo a repository fetch the chain never rebound.

    A run that fails after the repository step but before the chain's
    verify_collector_roster_updates.py leaves the membership sidecar bound to
    the OLD repository sha, so official_membership_movements.load raises and
    the desk and archive builders die (DE-043). While the sidecar still names
    the before-image repository, restore the repository and its fetch receipt
    from this run's before-image, then prove the load. Never raises: the run
    has already failed and this only records what it did.
    """
    root, before = Path(root), Path(run_dir) / "before"
    action = "verified"
    try:
        snapshot = before / ROLLBACK_SOURCES[0]
        sidecar = root / MEMBERSHIP_SIDECAR
        bound = read(sidecar).get("repository_sha256") if sidecar.is_file() else None
        if (snapshot.is_file() and bound == digest(snapshot)
                and digest(root / ROLLBACK_SOURCES[0]) != bound):
            for relative in ROLLBACK_SOURCES:
                if (before / relative).is_file():
                    temp = root / (relative + f".{os.getpid()}.rollback.tmp")
                    shutil.copy2(before / relative, temp)
                    replace_atomic(temp, root / relative)
            action = "restored"
        from legislation import official_membership_movements as membership
        membership.load(root)
        return {"rollback": action, "rollback_membership_load": "PASS"}
    except Exception as exc:
        return {"rollback": action, "rollback_membership_load": f"{type(exc).__name__}: {exc}"}


def terminate_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        process.kill()
    process.wait()


def run(root=ROOT, admission_limit=32, include_media=False, smoke=False):
    root = Path(root)
    # Smoke runs the media and canonical stages, so it takes the include_media
    # argument shape; "mode" is what keeps it out of the day's completion.
    include_media = include_media or smoke
    # A busy owner leaves the active run's status and receipt untouched.
    with pipeline_run_lock(root / ".pipeline_run.lock", owner_kind="congress_pipeline") as lease:
        started = time.time()
        run_dir = root / "audits/congress_pipeline" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir.mkdir(parents=True, exist_ok=False)
        state = {"schema": "congress-pipeline-status-v1", "status": "RUNNING",
                 "owner_pid": os.getpid(), "started_at": now(), "run_dir": str(run_dir), "steps": [],
                 "include_media": include_media, "mode": "smoke" if smoke else "full",
                 "deferred_steps": [],
                 "daily_attempt_id": os.environ.get("MELANGE_DAILY_ATTEMPT_ID")}
        stream_id = new_run_id("congress-pipeline")
        trim_live_log(root)
        live_log(root, "run", "STARTED", None, "mode=%s pid=%s" % (state["mode"], os.getpid()))
        environment = {**os.environ, **child_lock_environment(lease), "MONEYBALL_ROOT": str(root),
                       "BILLSTATUS_BULK_REFRESH": "1", "COSPONSOR_FORECAST_REFRESH": "1",
                       "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
        for override in ("DESK_OUT", "DASH_OUT"):
            environment.pop(override, None)
        acknowledged = []
        media_binding = None
        wake_held = False

        def launch(name, script, arguments, timeout, log_path, index, total):
            """Run one child to completion. Returns (step_start, returncode, seconds)."""
            step_start = time.time()
            last_beat = 0.0
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, str(CODE / script), *arguments],
                                           cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT)
                try:
                    while process.poll() is None:
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            state.update(heartbeat_at=now(), child_pid=process.pid)
                            write(root / STATUS, state)
                            elapsed = time.time() - step_start
                            if elapsed - last_beat >= HEARTBEAT_SECONDS:
                                last_beat = elapsed
                                _emit("refresh.heartbeat", "running",
                                      f"Congress pipeline: {name} still running",
                                      stage=name, detail=f"{int(elapsed)}s elapsed",
                                      run_id=stream_id, source="congress-pipeline",
                                      current=index, total=total,
                                      duration_ms=int(elapsed * 1000))
                                live_log(root, name, "RUNNING", elapsed, "child pid %s" % process.pid)
                            if elapsed > timeout:
                                raise TimeoutError(f"{name} exceeded {timeout} seconds")
                except BaseException:
                    if process.poll() is None:
                        terminate_tree(process)
                    raise
            return step_start, process.returncode, round(time.time() - step_start, 2)

        try:
            if os.name == "nt":
                import ctypes
                wake_held = bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000001))
                state["system_wake_lock"] = wake_held
            from run_pipeline import preflight_dependencies
            from legislation.sync_sentinel_bill_ledger import runtime
            preflight_dependencies()
            if runtime() is None:
                raise RuntimeError("Excel artifact-tool runtime is unavailable")
            selected = steps(admission_limit, include_media, smoke)
            total = len(selected)
            _emit("refresh.snapshot", "started", "Congress pipeline: before-image",
                  stage="snapshot", run_id=stream_id, source="congress-pipeline",
                  current=0, total=len(SOURCES))
            live_log(root, "snapshot", "STARTED", None, "%d source artifacts" % len(SOURCES))
            snapshot_start = time.time()
            before = {}
            for position, relative in enumerate(SOURCES, start=1):
                path = root / relative
                if not path.is_file():
                    continue
                copied = not relative.endswith(HASH_ONLY_SNAPSHOT)
                if copied:
                    target = run_dir / "before" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    path = target
                before[relative] = {"sha256": digest(path), "size": path.stat().st_size,
                                    "copied": copied}
                _emit("refresh.snapshot", "running", f"Congress pipeline: before-image {relative}",
                      stage="snapshot", run_id=stream_id, source="congress-pipeline",
                      current=position, total=len(SOURCES))
            write(run_dir / "before.json", before)
            snapshot_seconds = time.time() - snapshot_start
            _emit("refresh.snapshot", "completed", "Congress pipeline: before-image",
                  stage="snapshot", run_id=stream_id, source="congress-pipeline",
                  current=len(SOURCES), total=len(SOURCES),
                  duration_ms=int(snapshot_seconds * 1000))
            live_log(root, "snapshot", "COMPLETED", snapshot_seconds, "%d recorded" % len(before))
            for index, (name, script, arguments, timeout) in enumerate(selected, start=1):
                if script is None:
                    state["steps"].append({"step": name, "status": "SKIPPED_SMOKE",
                                           "returncode": None, "duration_seconds": 0})
                    write(root / STATUS, state)
                    print(f"{now()} {name}: skipped (smoke)", flush=True)
                    _emit("refresh.step", "skipped", f"Congress pipeline: {name} skipped in smoke mode",
                          stage=name, run_id=stream_id, source="congress-pipeline",
                          current=index, total=total)
                    live_log(root, name, "SKIPPED_SMOKE", 0, "smoke mode")
                    continue
                state.update(active_step=name, heartbeat_at=now())
                write(root / STATUS, state)
                print(f"{now()} {name}: starting", flush=True)
                _emit("refresh.step", "running", f"Congress pipeline: {name}",
                      stage=name, run_id=stream_id, source="congress-pipeline",
                      current=index, total=total)
                live_log(root, name, "STARTED", None, "step %d of %d" % (index, total))
                log_path = run_dir / f"{name}.log"
                step_start, code, seconds = launch(name, script, arguments, timeout,
                                                   log_path, index, total)
                row = {"step": name, "returncode": code,
                       "duration_seconds": seconds, "log": str(log_path)}
                if code and name in RETRYABLE_STEPS and not smoke:
                    first_code = code
                    print(f"{now()} {name}: exit {first_code}; one retry in "
                          f"{DISCOVERY_RETRY_DELAY_SECONDS}s", flush=True)
                    _emit("refresh.step", "retrying",
                          f"Congress pipeline: {name} exit {first_code}, retrying once",
                          stage=name, run_id=stream_id, source="congress-pipeline",
                          current=index, total=total)
                    live_log(root, name, "RETRYING", seconds,
                             "exit %s; one retry in %ss" % (first_code, DISCOVERY_RETRY_DELAY_SECONDS))
                    if DISCOVERY_RETRY_DELAY_SECONDS:
                        time.sleep(DISCOVERY_RETRY_DELAY_SECONDS)
                    log_path = run_dir / f"{name}.retry.log"
                    step_start, code, seconds = launch(name, script, arguments, timeout,
                                                       log_path, index, total)
                    row.update(returncode=code, duration_seconds=seconds, log=str(log_path),
                               first_returncode=first_code, retried=True)
                    if code:
                        # Twice is not flakiness. Continue without new-bill
                        # discovery: repository and BILLSTATUS still refresh
                        # every already-known bill, and the next run retries.
                        row["status"] = "DEFERRED"
                        row["note"] = ("%s collector failed twice (rc=%s, rc=%s); new-bill "
                                       "discovery deferred to the next run; see %s"
                                       % (name, first_code, code, log_path))
                state["steps"].append(row)
                output = log_path.read_text(encoding="utf-8", errors="replace")
                if code and row.get("status") != "DEFERRED":
                    accepted, names = acknowledged_audit_only(output) if name == "pipeline" else (False, [])
                    if not accepted:
                        live_log(root, name, "FAILED", row["duration_seconds"],
                                 "exit %s; see %s" % (code, log_path))
                        raise RuntimeError(f"{name} failed with exit {code}; see {log_path}")
                    acknowledged = names
                if row.get("status") == "DEFERRED":
                    state["deferred_steps"] = state["deferred_steps"] + [name]
                    write(root / STATUS, state)
                    print(f"{now()} {name}: DEFERRED ({row['duration_seconds']}s)", flush=True)
                    _emit("refresh.step", "deferred", f"Congress pipeline: {row['note']}",
                          stage=name, run_id=stream_id, source="congress-pipeline",
                          current=index, total=total,
                          duration_ms=int(row["duration_seconds"] * 1000))
                    live_log(root, name, "DEFERRED", row["duration_seconds"], row["note"])
                    continue
                note = ""
                if row.get("retried"):
                    note = "recovered on retry (first exit %s)" % row["first_returncode"]
                if name == "media":
                    media_document, media_binding = validated_media_snapshot(root, step_start)
                    # A PASS media receipt can still carry incomplete press
                    # coverage. Surface that here rather than let PASS read as
                    # complete collection.
                    partial_sources = media_document.get("partial_sources") or []
                    parked_steps = media_document.get("parked_steps") or []
                    if partial_sources or parked_steps:
                        row["partial_sources"] = partial_sources
                        row["press_partial"] = media_document.get("press_partial")
                        row["parked_steps"] = parked_steps
                        parts = []
                        if partial_sources:
                            parts.append("PARTIAL: " + ", ".join(partial_sources))
                        if parked_steps:
                            parts.append("parked: " + ", ".join(parked_steps))
                        note = "; ".join(parts)
                print(f"{now()} {name}: completed ({row['duration_seconds']}s)", flush=True)
                _emit("refresh.step", "completed",
                      f"Congress pipeline: {name}" + (f" ({note})" if note else ""),
                      stage=name, run_id=stream_id, source="congress-pipeline",
                      current=index, total=total,
                      duration_ms=int(row["duration_seconds"] * 1000))
                live_log(root, name, "COMPLETED", row["duration_seconds"], note)
            artifacts = validate_publication(root, started, include_media)
            if include_media:
                if media_binding != artifacts["data/engine/daily_media_refresh_receipt.json"]:
                    raise RuntimeError("Media receipt changed after the media stage completed")
                shutil.copy2(root / "data/engine/daily_media_refresh_receipt.json", run_dir / "daily_media_refresh_receipt.json")
                if (digest(run_dir / "daily_media_refresh_receipt.json") != media_binding["sha256"]
                        or digest(root / "data/engine/daily_media_refresh_receipt.json") != media_binding["sha256"]):
                    raise RuntimeError("Media receipt changed during terminal publication")
            old = read(run_dir / "before/ranks_python.json").get("rank", {})
            current = read(root / "ranks_python.json").get("rank", {})
            movers = [{"member": name, "before": old.get(name), "after": rank}
                      for name, rank in current.items() if old.get(name) != rank]
            write(run_dir / "rank_changes.json", movers)
            state.update(status="PASS", completed_at=now(), active_step=None, artifacts=artifacts,
                         acknowledged_audit_findings=acknowledged, rank_changes=len(movers))
            write(run_dir / "receipt.json", state)
            # A smoke run proves the shape of the job, not the day's generation.
            # It never becomes the last verified successful production receipt.
            if not smoke:
                write(root / RECEIPT, state)
            write(root / STATUS, state)
            summary = "Congress collection, Excel and dashboard verified"
            if state["deferred_steps"]:
                summary += " - PASS with deferred: " + ", ".join(state["deferred_steps"])
            _emit("refresh.run", "completed", summary,
                  run_id=stream_id, source="congress-pipeline", current=total, total=total)
            live_log(root, "run", "PASS", time.time() - started,
                     "mode=%s rank_changes=%d deferred=%s"
                     % (state["mode"], len(movers), ",".join(state["deferred_steps"]) or "none"))
            print(json.dumps({"status": "PASS", "mode": state["mode"],
                              "deferred_steps": state["deferred_steps"],
                              "receipt": str(run_dir / "receipt.json") if smoke else str(root / RECEIPT),
                              "rank_changes": len(movers)}), flush=True)
            return 0
        except BaseException as exc:
            state.update(status="FAILED", completed_at=now(), error=f"{type(exc).__name__}: {exc}")
            state.update(rollback_unbound_repository(root, run_dir))
            write(run_dir / "receipt.json", state)
            write(root / STATUS, state)
            _emit("refresh.run", "failed", state["error"], run_id=stream_id, source="congress-pipeline")
            live_log(root, "run", "FAILED", None, state["error"][:200])
            print(state["error"], file=sys.stderr, flush=True)
            return 1
        finally:
            if wake_held:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--admission-limit", type=int, default=32)
    parser.add_argument("--include-media", action="store_true", help="Collect media and run the complete canonical pipeline after bill collection")
    parser.add_argument("--smoke", action="store_true",
                        help="Cheap end-to-end shakeout: skip discovery, one no-model admission candidate, "
                             "a three-source media subset with press skipped, and the full canonical "
                             "pipeline. Writes no production receipt and cannot complete the day.")
    args = parser.parse_args(argv)
    if args.status:
        result = health()
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") in {"PASS", "RUNNING"} else 1
    if args.admission_limit < 1:
        parser.error("--admission-limit must be positive")
    if args.smoke and args.include_media:
        parser.error("--smoke already runs the media and canonical stages; do not combine it with --include-media")
    try:
        return run(admission_limit=args.admission_limit, include_media=args.include_media, smoke=args.smoke)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
